"""The dependency-routing decision layer (draft §12).

This module answers exactly one question — **"where should this capability go, and do I
have to ask the human?"** — and nothing else. It deliberately does not install anything:
the install backends are P4, and inventing one here would be the wrong kind of progress.

The routing rule is a single judge (draft §12.1):

* referenced by a project manifest → project-isolated, **never ask**
* a single-file generic CLI → the data root, **never ask**
* needs packaging / builds an environment / over the size threshold → **must confirm**
* source not verifiable → reference only
* no capability in the whitelist → cannot be decided at all

"Never ask" for the obvious cases is a design requirement, not an optimisation: if
confirmation becomes noise the human starts approving reflexively and the high-risk
confirmations stop working too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canon import canonical_bytes, digest_bytes, digest_file
from ..exits import AirootError
from .discovery import Whitelist, load_whitelist

DEFAULT_SIZE_THRESHOLD_BYTES = 300 * 1024 * 1024  # 300 MB, decided by the operator (§12.1)

TOOLING_MEMORY_RELATIVE = ".ai/tooling.json"

# Manifest files that make a capability "owned by the project". Lock files are included
# because a lock records the decision to depend on it, which is exactly the judgement.
PROJECT_MANIFESTS: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-lock.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "environment.yml",
    "environment.yaml",
    "conda.yml",
)

# A capability_id is not the only name a manifest can use for the same tool, so the rule consults a
# table instead of claiming one it does not have (draft §170: the docstring used to say "plus the
# common alias forms" while the code had no alias logic at all). Every alias is a name this
# repository's own policy already uses for that capability: `capabilities.json` names the entry
# (`cmake.exe` for `build`) and the whitelist's `executable_name.any_of` lists the rest
# (`7z`/`7za`/`7zr`, `python3`, `javac`). `npm` is the only addition — it is node's own package
# manager, and the name a manifest actually carries for the node toolchain.
CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "archive": ("7z", "7za", "7zr"),
    "build": ("cmake",),
    "java": ("javac",),
    "node": ("npm",),
    "python": ("python3",),
}

# Where a manifest declares what it depends on (draft §170). A **section name** is not one of these:
# `[build-system]` is the table that *carries* `requires`, it is not a dependency on `build`, and a
# `"scripts": {"build": ...}` entry is a task rather than a dependency. Only the contents of these
# containers are read. Keys are compared lowercased because JSON spells them `devDependencies` while
# `Pipfile` and `pyproject.toml` spell them `dev-packages` / `dev-dependencies`.
DEPENDENCY_CONTAINERS: frozenset[str] = frozenset(
    {
        "dependencies",
        "devdependencies",
        "dev-dependencies",
        "dev-packages",
        "optionaldependencies",
        "optional-dependencies",
        "peerdependencies",
        "bundleddependencies",
        "engines",
        "resolutions",
        "overrides",
        "packages",
        "requires",
    }
)

# Characters that cannot appear inside a dependency name, so splitting on them separates a name from
# its version and from the punctuation around it (`cmake>=3.31`, `"cmake@^3.0":`, `- cmake=3.31`).
# `.` `-` `_` `+` are deliberately **not** separators: they are part of a name, and that is what keeps
# `build-backend`, `setuptools.build_meta` and `python-dotenv` from reading as `build` or `python`.
_NAME_SEPARATORS = re.compile(r"[\s,;:\[\]\(\)\{\}\"'<>=!~^@*|/\\]+")

# The three options are fixed (draft §12.2). They may not be renamed or extended.
CONFIRMATION_OPTIONS: tuple[str, ...] = ("project-isolated", "data-root", "cancel")

SCOPE_PROJECT = "project"
SCOPE_DATA_ROOT = "data-root"
SCOPE_REFERENCE_ONLY = "reference-only"
SCOPE_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ScopeRequest:
    """What the caller wants to do, and what it already knows."""

    capability_id: str
    project_root: Path | None = None
    intent: str = "install"  # install | reference
    creates_environment: bool = False
    requires_cuda_or_native: bool = False
    # Never guessed: only what the caller actually knows. Unknown stays unknown.
    declared_size_bytes: int | None = None
    source_verifiable: bool = True


@dataclass(frozen=True)
class ScopeDecision:
    capability_id: str
    scope: str
    confirmation_required: bool
    origin: str
    reason_code: str
    options: tuple[str, ...] = ()
    size_estimate_bytes: int | None = None
    size_source: str = "unknown"
    threshold_bytes: int = DEFAULT_SIZE_THRESHOLD_BYTES
    manifest_hits: tuple[str, ...] = ()
    memory: dict[str, Any] | None = None
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "capability_id": self.capability_id,
            "scope": self.scope,
            "confirmation_required": self.confirmation_required,
            "origin": self.origin,
            "options": list(self.options),
            "size_estimate_bytes": self.size_estimate_bytes,
            "size_source": self.size_source,
            "threshold_bytes": self.threshold_bytes,
            "manifest_hits": list(self.manifest_hits),
            "memory": self.memory,
            "evidence": [dict(item) for item in self.evidence],
            "reason_code": self.reason_code,
        }


# --------------------------------------------------------------------------- #
# project manifest detection
# --------------------------------------------------------------------------- #


def manifest_paths(project_root: Path) -> list[Path]:
    root = Path(project_root)
    if not root.is_dir():
        return []
    return [root / name for name in PROJECT_MANIFESTS if (root / name).is_file()]


@dataclass(frozen=True)
class ManifestHit:
    """One capability a project manifest declares, and the declaration that says so.

    The reading has to be *answerable*: "referenced by a project manifest" on its own cannot be
    checked by the caller, so every hit carries the file, the line and the text it was read from
    (draft §170). A hit that comes from a parsed value has no line — the parse does not keep one —
    and says so with ``None`` instead of inventing a number.
    """

    capability_id: str
    manifest: str
    line: int | None
    declaration: str

    def describe(self) -> str:
        where = self.manifest if self.line is None else f"{self.manifest}:{self.line}"
        return f"{self.capability_id} <- {self.declaration.strip()!r} ({where})"


def capability_names(capability_id: str) -> frozenset[str]:
    """The names a manifest may use for this capability: its ``capability_id`` and its aliases."""

    return frozenset({capability_id.lower(), *CAPABILITY_ALIASES.get(capability_id, ())})


def _texts_in(value: Any) -> list[str]:
    """The declaration texts one dependency container carries.

    A mapping declares its dependencies by **key** (``{"dependencies": {"node": "^20"}}``), and its
    values are recursed into so a nested constraint (``node = {version = "^20"}``) keeps its name.
    A list declares them by element, and an element that is a mapping is a poetry.lock-style table
    whose ``name`` is the dependency.
    """

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        texts: list[str] = []
        for key, item in value.items():
            if isinstance(key, str):
                texts.append(key)
            texts.extend(_texts_in(item))
        return texts
    if isinstance(value, list):
        texts = []
        for item in value:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str):
                texts.append(name)
            texts.extend(_texts_in(item))
        return texts
    return []


def _container_texts(node: Any) -> list[str]:
    """Every declaration text in a parsed manifest — the contents of the containers, and nothing else.

    The recursion is what finds ``[project.optional-dependencies]`` and ``tool.poetry.dependencies``:
    the section that *contains* a container is traversed, but a section or key that is not one of
    :data:`DEPENDENCY_CONTAINERS` never contributes text of its own.
    """

    texts: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in DEPENDENCY_CONTAINERS:
                texts.extend(_texts_in(value))
            else:
                texts.extend(_container_texts(value))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_container_texts(item))
    return texts


def _declaration_lines(text: str) -> list[tuple[int, str]]:
    """The non-comment lines of a manifest this build does not parse."""

    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append((number, line))
    return lines


def _parser_for(path: Path) -> str | None:
    """Which parser reads this manifest, or ``None`` for the line-oriented ones.

    The manifest *formats* this build parses rather than reads line by line. Everything else
    (``requirements*.txt``, the lock files, ``environment.yml``) is line-oriented already, and a
    structured file that fails to parse falls back to lines rather than counting as "no dependency".
    """

    if path.suffix.lower() == ".json" or path.name == "Pipfile.lock":
        return "json"
    if path.suffix.lower() == ".toml" or path.name == "Pipfile":
        return "toml"
    return None


def _declarations(path: Path) -> list[tuple[int | None, str]]:
    """``(line, text)`` for every declaration in one manifest (draft §170).

    A structured manifest is parsed, because only a parse can tell a *declaration* from a section
    name. A parse failure is not a verdict — the text is then read line by line, where a name still
    has to stand on its own (``[build-system]`` tokenises to ``build-system``, so the section header
    cannot be read as a dependency on ``build`` either way). ``requirements*.txt``, the lock files
    and ``environment.yml`` have no structure to parse and are read that way from the start.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    parser = _parser_for(path)
    parsed: Any = None
    if parser == "json":
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    elif parser == "toml":
        try:
            import tomllib

            parsed = tomllib.loads(text)
        except (ValueError, TypeError):
            parsed = None
    if isinstance(parsed, (dict, list)):
        return [(None, item) for item in _container_texts(parsed)]
    return _declaration_lines(text)


def manifest_hits(project_root: Path, whitelist: Whitelist) -> tuple[ManifestHit, ...]:
    """Which whitelisted capabilities the project's manifests **declare**, and where.

    The rule is deliberately narrow (draft §170): a capability counts when one of its names appears
    as a whole dependency name in a declaration. A name that merely *contains* it does not
    (``python-dotenv`` is not ``python``), and neither does a section or key that happens to contain
    it (``[build-system]`` is not a dependency on ``build``).
    """

    by_name: dict[str, str] = {}
    for rule in whitelist.entries:
        for name in sorted(capability_names(rule.capability_id)):
            by_name.setdefault(name, rule.capability_id)

    hits: list[ManifestHit] = []
    seen: set[tuple[str, str]] = set()
    for path in manifest_paths(project_root):
        for line, text in _declarations(path):
            tokens = {token for token in _NAME_SEPARATORS.split(text.lower()) if token}
            for token in sorted(tokens):
                capability_id = by_name.get(token)
                if capability_id is None or (capability_id, path.name) in seen:
                    continue
                seen.add((capability_id, path.name))
                hits.append(ManifestHit(capability_id, path.name, line, text))
    return tuple(sorted(hits, key=lambda hit: (hit.capability_id, hit.manifest)))


def declared_capabilities(project_root: Path, whitelist: Whitelist) -> tuple[str, ...]:
    """Whitelisted capabilities the project's manifests actually reference.

    A capability is referenced by **name** — its ``capability_id`` or one of its declared alias forms
    (:data:`CAPABILITY_ALIASES`) — and only inside a dependency declaration, which
    :func:`manifest_hits` reads and explains. The alias table is data, and a test reads it back, so
    this sentence and the rule cannot drift apart (draft §170).
    """

    return tuple(sorted({hit.capability_id for hit in manifest_hits(project_root, whitelist)}))


def manifest_fingerprint(project_root: Path) -> str | None:
    """Digest of the manifests, so a recorded choice can be invalidated when they change."""

    paths = manifest_paths(project_root)
    if not paths:
        return None
    records = {
        path.name: digest_file(path)
        for path in paths
    }
    return digest_bytes(canonical_bytes(records))


# --------------------------------------------------------------------------- #
# the recorded choice (read-only in this stage)
# --------------------------------------------------------------------------- #


def tooling_memory_path(project_root: Path) -> Path:
    return Path(project_root) / TOOLING_MEMORY_RELATIVE


def read_tooling_memory(project_root: Path) -> dict[str, Any] | None:
    """Read ``.ai/tooling.json``; ``None`` when absent or unreadable.

    This file is a **read-only memory for the CLI**, not an authorisation: an unreadable
    or malformed file degrades to "no memory" and the routing question is asked again.
    Writing it belongs to the human approval channel (P2) — see draft §17.3-1.
    """

    path = tooling_memory_path(project_root)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def memory_choice(
    memory: dict[str, Any] | None,
    *,
    capability_id: str,
    manifest_fingerprint_value: str | None,
) -> dict[str, Any] | None:
    """The remembered choice for this capability, if the memory is still valid.

    Valid means: same project manifests (fingerprint match) and the same capability.
    Anything else is treated as "no memory" so the question is asked again — a stale
    answer is worse than no answer.
    """

    if not memory:
        return None
    if memory.get("manifest_fingerprint") != manifest_fingerprint_value:
        return None
    choice = (memory.get("choices") or {}).get(capability_id)
    return choice if isinstance(choice, dict) else None


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def decide_scope(
    request: ScopeRequest,
    *,
    whitelist: Whitelist | None = None,
    threshold_bytes: int = DEFAULT_SIZE_THRESHOLD_BYTES,
    memory: dict[str, Any] | None = None,
) -> ScopeDecision:
    """Classify one request. Deterministic: the first matching rule wins."""

    rules = whitelist or load_whitelist()
    capability_id = request.capability_id
    rule = next((item for item in rules.entries if item.capability_id == capability_id), None)
    # Existence is decided by the **frozen capability list** (draft §15.2-1); the discovery
    # whitelist only says how to recognise an object that is already on disk. A capability can
    # legitimately have no discovery predicate (a tool AIROOT installs itself), and routing must
    # still be able to answer for it.
    kind = rule.kind if rule is not None else _frozen_kind(capability_id)
    evidence: list[dict[str, Any]] = [
        {
            "kind": "query",
            "detail": (
                f"capability={capability_id} intent={request.intent} "
                f"creates_environment={request.creates_environment} "
                f"requires_cuda_or_native={request.requires_cuda_or_native}"
            ),
        }
    ]
    hits = manifest_hits(request.project_root, rules) if request.project_root else ()
    declared_ids = tuple(sorted({hit.capability_id for hit in hits}))
    if hits:
        # The grounds, not just the verdict: `manifest_hits` names the capabilities, and this names
        # the file, the line and the text each one was read from (draft §170).
        evidence.append(
            {"kind": "project_manifest", "detail": "; ".join(hit.describe() for hit in hits)}
        )
    fingerprint = manifest_fingerprint(request.project_root) if request.project_root else None

    def decision(
        scope: str,
        *,
        confirmation: bool,
        origin: str,
        reason_code: str,
        extra_evidence: list[dict[str, Any]] | None = None,
        remembered: dict[str, Any] | None = None,
    ) -> ScopeDecision:
        return ScopeDecision(
            capability_id=capability_id,
            scope=scope,
            confirmation_required=confirmation,
            origin=origin,
            reason_code=reason_code,
            options=CONFIRMATION_OPTIONS if confirmation else (),
            size_estimate_bytes=request.declared_size_bytes,
            size_source="declared" if request.declared_size_bytes is not None else "unknown",
            threshold_bytes=threshold_bytes,
            manifest_hits=declared_ids,
            memory=remembered,
            evidence=tuple(evidence + list(extra_evidence or [])),
        )

    # 0. A capability that is not declared anywhere cannot be routed at all.
    if rule is None and kind is None:
        evidence.append(
            {
                "kind": "whitelist",
                "detail": f"no whitelist entry for {capability_id}",
            }
        )
        return decision(
            SCOPE_UNSUPPORTED,
            confirmation=False,
            origin="no_capability",
            reason_code="CAPABILITY_NOT_DECLARED",
        )

    # 1. A recorded choice beats everything except a missing capability (draft §12.2).
    remembered = memory_choice(memory, capability_id=capability_id, manifest_fingerprint_value=fingerprint)
    if remembered is not None:
        evidence.append(
            {
                "kind": "memory",
                "detail": f"{TOOLING_MEMORY_RELATIVE} records scope={remembered.get('scope')}",
            }
        )
        return decision(
            str(remembered.get("scope", SCOPE_PROJECT)),
            confirmation=False,
            origin="memory",
            reason_code="SUCCESS",
            remembered=remembered,
        )

    # 2. Declared by the project's manifests → project-isolated, never ask. The evidence for *this*
    #    answer was appended above, together with the declaration that carries it.
    if capability_id in declared_ids:
        return decision(
            SCOPE_PROJECT, confirmation=False, origin="project_manifest", reason_code="SUCCESS"
        )

    # 3. Unverifiable source → reference only, and no install of any kind.
    if not request.source_verifiable:
        evidence.append(
            {"kind": "source", "detail": "来源或完整性不可验证，只允许 reference（§12.1 第 5 行）"}
        )
        return decision(
            SCOPE_REFERENCE_ONLY,
            confirmation=False,
            origin="unverifiable_source",
            reason_code="SUCCESS",
        )

    # 4. High risk: packaging, environment creation, CUDA/native, or over the threshold.
    #    This set is closed on purpose (draft §12.1): "严的边界只能划在'往运行时装包、创建环境、
    #    下大体积'这三类动作上". Installing a *runtime* is the "creates an environment" case —
    #    it is also the one case where project-isolated vs data-root is a genuine choice
    #    (a per-project nvm/conda vs one shared interpreter), so it must stay in this set.
    #    What must NOT enter this set is a capability with only one sensible answer
    #    (`rule.kind == "tool"`, rule 5): confirming those is the noise that makes
    #    high-risk confirmations meaningless.
    high_risk_reasons: list[str] = []
    if request.creates_environment:
        high_risk_reasons.append("creates an environment")
    if request.requires_cuda_or_native:
        high_risk_reasons.append("needs CUDA or non-Python binaries")
    if request.declared_size_bytes is not None and request.declared_size_bytes > threshold_bytes:
        high_risk_reasons.append(
            f"declared size {request.declared_size_bytes} exceeds the {threshold_bytes} byte threshold"
        )
    if rule is not None and rule.kind == "runtime" or kind == "runtime":
        high_risk_reasons.append(
            "installing a runtime creates an environment, and where it lives is a real choice"
        )
    if high_risk_reasons:
        evidence.append({"kind": "high_risk", "detail": "; ".join(high_risk_reasons)})
        return decision(
            SCOPE_DATA_ROOT,
            confirmation=True,
            origin="high_risk",
            reason_code="SCOPE_CONFIRMATION_REQUIRED",
        )

    # 5. Single-file generic tool → the data root, never ask.
    if kind == "tool":
        evidence.append(
            {
                "kind": "generic_tool",
                "detail": f"{capability_id} is a general-purpose tool; the answer is always the same",
            }
        )
        return decision(SCOPE_DATA_ROOT, confirmation=False, origin="generic_tool", reason_code="SUCCESS")

    # 6. Anything else: ask, because we could not prove it is one of the obvious cases.
    evidence.append({"kind": "fallback", "detail": "no rule proved this is an obvious case"})
    return decision(
        SCOPE_DATA_ROOT,
        confirmation=True,
        origin="unclassified",
        reason_code="SCOPE_CONFIRMATION_REQUIRED",
    )


def _frozen_kind(capability_id: str) -> str | None:
    """``kind`` from the frozen capability list, or ``None`` when it is not frozen.

    A missing or unreadable list is treated as "nothing is frozen": routing then refuses to
    decide, which is the safe direction (draft §15.4).
    """

    from .boundary import load_capabilities

    try:
        capability = load_capabilities().by_id(capability_id)
    except AirootError:
        return None
    return capability.kind if capability is not None else None


def require_decidable(decision: ScopeDecision) -> None:
    """Raise for decisions a caller must not act on silently."""

    if decision.reason_code == "CAPABILITY_NOT_DECLARED":
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"no capability is declared for {decision.capability_id}",
            evidence=[item["detail"] for item in decision.evidence],
        )


def import_scope_decision(
    capability_id: str,
    *,
    source_bytes: int,
    threshold_bytes: int = DEFAULT_SIZE_THRESHOLD_BYTES,
) -> ScopeDecision:
    """The routing decision for ``adopt --mode import`` (draft §70).

    `plan` and `adopt --mode import` both produce a plan for a managed instance, and §69 showed what
    happens when only one of two entry points runs a gate. This is the second gate: §12.1 makes
    confirmation mandatory for the high-risk classes, and the import path never consulted it.

    Only the facts this command can actually observe are passed: the **measured** size of the file it
    already hashed, and (inside ``decide_scope``) the frozen ``kind``. The two declarable flags of
    `plan` — ``--creates-environment`` and ``--requires-cuda-or-native`` — have no import equivalent
    on purpose: a caller must not be able to declare the risk away in the command that is about to
    copy the payload.

    A named seam rather than an inline call, so the over-threshold trigger is testable without
    writing a 300 MB file.
    """

    return decide_scope(
        ScopeRequest(
            capability_id=capability_id,
            intent="install",
            declared_size_bytes=source_bytes,
        ),
        threshold_bytes=threshold_bytes,
    )


__all__ = [
    "CAPABILITY_ALIASES",
    "CONFIRMATION_OPTIONS",
    "DEFAULT_SIZE_THRESHOLD_BYTES",
    "DEPENDENCY_CONTAINERS",
    "PROJECT_MANIFESTS",
    "SCOPE_DATA_ROOT",
    "SCOPE_PROJECT",
    "SCOPE_REFERENCE_ONLY",
    "SCOPE_UNSUPPORTED",
    "ManifestHit",
    "ScopeDecision",
    "ScopeRequest",
    "TOOLING_MEMORY_RELATIVE",
    "capability_names",
    "declared_capabilities",
    "decide_scope",
    "import_scope_decision",
    "manifest_fingerprint",
    "manifest_hits",
    "manifest_paths",
    "memory_choice",
    "read_tooling_memory",
    "require_decidable",
    "tooling_memory_path",
]
