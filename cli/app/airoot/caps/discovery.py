"""``discover``: read-only classification of a data root's contents.

Implements draft §4 (data root + capability whitelist) and §5 (reference lifecycle).
Hard rules this module enforces:

* the scan is **read-only** — it never writes, moves or deletes anything;
* nothing found is ever executed (PE probing reads bytes only);
* matching is by *evidence*, never by directory name alone (v0.3 §9.3:716);
* unmatched objects become ``unmanaged`` (reported, never taken over);
* reparse points are not followed, and the scan is depth/count bounded
  (v0.3 §9.4:748-754 "不默认递归扫描所有磁盘").
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Iterable

from .. import CLI_ROOT
from ..exits import AirootError
from .environment import spec_from_entry
from .probe_pe import PeMetadata, is_probably_executable, probe_executable

WHITELIST_PATH = CLI_ROOT / "app" / "airoot" / "policy" / "discovery-whitelist.json"

DEFAULT_LIMITS = {
    "max_depth": 4,
    "max_relative_depth": 2,
    "max_files_per_object": 400,
    "max_objects": 200,
    "max_versions_per_object": 8,
    "max_active_markers": 16,
}

_SLUG_PATTERN = re.compile(r"[^a-z0-9._-]+")


# --------------------------------------------------------------------------- #
# whitelist
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WhitelistEntry:
    capability_id: str
    kind: str
    evidence_all: tuple[dict[str, Any], ...]
    evidence_any: tuple[dict[str, Any], ...] = ()
    entrypoints: tuple[str, ...] = ()
    version_source: str | None = None
    weak_evidence: bool = False
    weak_reason: str | None = None
    environment: dict[str, Any] | None = None


@dataclass(frozen=True)
class Exclusion:
    reason: str
    directory_names: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Whitelist:
    revision: str
    entries: tuple[WhitelistEntry, ...]
    exclusions: tuple[Exclusion, ...]
    limits: dict[str, int]

    def exclusion_for(self, directory_name: str) -> Exclusion | None:
        lowered = directory_name.lower()
        for exclusion in self.exclusions:
            if any(name.lower() == lowered for name in exclusion.directory_names):
                return exclusion
            if any(lowered.endswith(suffix.lower()) for suffix in exclusion.suffixes):
                return exclusion
        return None


#: The predicate types `_matches` actually evaluates. Declared as data so the loader can refuse one
#: it could never evaluate: `_matches` falls through to `False` for an unknown type, which is
#: fail-closed at match time but **silent** — a typo would switch a capability's detection off
#: without a word, and the machine would simply look like it does not have that capability.
#: Rejecting at load time turns that into an error at the earliest point, the way this loader
#: already refuses an entry with no static evidence or one naming an injection-type variable.
PREDICATE_TYPES = ("executable_name", "sibling_file", "pe_static")

#: `pe_static` reads these attributes. Anything else hands the matcher `None`, which it reads as
#: "no match" — the same silent failure by a second route.
PE_PREDICATE_FIELDS = tuple(sorted(item.name for item in dataclass_fields(PeMetadata)))

#: The comparison a `pe_static` predicate must carry. Exactly one: with neither the predicate always
#: fails, and with both one of them is silently dead.
PE_PREDICATE_OPERATORS = ("equals", "contains")

#: `version_source` selects which static field supplies the version. Only this prefix is
#: implemented; an unrecognised one falls back to the default field, i.e. a wrong answer rather
#: than an error.
VERSION_SOURCE_PREFIX = "pe_static"


def _validate_predicates(entry: WhitelistEntry) -> None:
    """Refuse a predicate the matcher cannot evaluate (see ``PREDICATE_TYPES``)."""

    for predicate in entry.evidence_all + entry.evidence_any:
        kind = predicate.get("type")
        if kind not in PREDICATE_TYPES:
            raise AirootError(
                "INVALID_INPUT",
                f"whitelist entry {entry.capability_id} declares an unknown evidence predicate: {kind!r}",
                evidence=[
                    f"known predicates: {', '.join(PREDICATE_TYPES)}",
                    "an unevaluated predicate never matches, so the entry could never be detected",
                ],
            )
        if kind in {"executable_name", "sibling_file"} and not predicate.get("any_of"):
            raise AirootError(
                "INVALID_INPUT",
                f"whitelist entry {entry.capability_id} declares {kind} without any_of",
                evidence=["a name predicate needs at least one candidate name"],
            )
        if kind == "pe_static":
            field_name = str(predicate.get("field", ""))
            if field_name not in PE_PREDICATE_FIELDS:
                raise AirootError(
                    "INVALID_INPUT",
                    f"whitelist entry {entry.capability_id} reads an unknown PE field: {field_name!r}",
                    evidence=[f"known fields: {', '.join(PE_PREDICATE_FIELDS)}"],
                )
            operators = [item for item in PE_PREDICATE_OPERATORS if item in predicate]
            if len(operators) != 1:
                raise AirootError(
                    "INVALID_INPUT",
                    f"whitelist entry {entry.capability_id} must carry exactly one of "
                    f"{'/'.join(PE_PREDICATE_OPERATORS)}; it carries {operators or 'neither'}",
                    evidence=[
                        "with neither the predicate always fails",
                        "with both, one of them is silently dead",
                    ],
                )


def _validate_version_source(entry: WhitelistEntry) -> None:
    """Refuse a ``version_source`` the version picker would silently ignore."""

    if entry.version_source is None:
        return
    prefix, separator, attribute = str(entry.version_source).partition(":")
    if separator != ":" or prefix != VERSION_SOURCE_PREFIX or attribute not in PE_PREDICATE_FIELDS:
        raise AirootError(
            "INVALID_INPUT",
            f"whitelist entry {entry.capability_id} declares an unusable version_source: "
            f"{entry.version_source!r}",
            evidence=[
                f"the implemented form is {VERSION_SOURCE_PREFIX}:<field>",
                f"known fields: {', '.join(PE_PREDICATE_FIELDS)}",
                "an unrecognised source silently falls back to the default field",
            ],
        )


def load_whitelist(path: Path | None = None) -> Whitelist:
    target = Path(path) if path is not None else WHITELIST_PATH
    if not target.is_file():
        raise AirootError(
            "INVALID_INPUT",
            f"discovery whitelist not found: {target}",
            evidence=["discover cannot classify anything without a whitelist"],
        )
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError("INVALID_INPUT", f"discovery whitelist is unreadable: {target}", evidence=[str(exc)]) from exc

    entries = tuple(
        WhitelistEntry(
            capability_id=str(item["capability_id"]),
            kind=str(item.get("kind", "tool")),
            evidence_all=tuple(item.get("evidence_all", [])),
            evidence_any=tuple(item.get("evidence_any", [])),
            entrypoints=tuple(item.get("entrypoints", [])),
            version_source=item.get("version_source"),
            weak_evidence=bool(item.get("weak_evidence", False)),
            weak_reason=item.get("weak_reason"),
            environment=item.get("environment"),
        )
        for item in document.get("entries", [])
    )
    for entry in entries:
        has_static = any(
            item.get("type") in {"pe_static", "sibling_file"} for item in entry.evidence_all + entry.evidence_any
        )
        if not has_static and not entry.weak_evidence:
            raise AirootError(
                "INVALID_INPUT",
                f"whitelist entry {entry.capability_id} has no static evidence predicate",
                evidence=[
                    "a bare executable name is never sufficient (v0.3 §9.3:716)",
                    "declare weak_evidence + weak_reason if name matching must be accepted",
                ],
            )
        # A whitelist may not request a forbidden (injection-type) variable, and its
        # declared paths must stay relative. Validated at load time, not at write time.
        spec_from_entry(entry.capability_id, entry.environment)
        # And it may not use evidence vocabulary the matcher cannot evaluate (see
        # `PREDICATE_TYPES`): an unknown predicate never matches, so the failure would be silent.
        _validate_predicates(entry)
        _validate_version_source(entry)
    exclusions = tuple(
        Exclusion(
            reason=str(item.get("reason", "")),
            directory_names=tuple(item.get("any_of", [])),
            suffixes=tuple(item.get("suffix_any_of", [])),
        )
        for item in document.get("exclusions", [])
    )
    limits = {**DEFAULT_LIMITS, **{k: int(v) for k, v in document.get("scan", {}).items()}}
    whitelist = Whitelist(
        revision=str(document.get("revision", "wl-0")),
        entries=entries,
        exclusions=exclusions,
        limits=limits,
    )
    # Recognition rules may not outrun the frozen boundary (draft §15.2-1): an entry naming a
    # capability that was never frozen must fail here rather than quietly widen what may be
    # adopted. Imported lazily so the two policy readers stay independent.
    from .boundary import check_whitelist_capabilities

    problems = check_whitelist_capabilities(whitelist)
    if problems:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            "the whitelist references capabilities that are not frozen",
            evidence=[*problems, "freeze the capability first (draft §15.4)"],
        )
    return whitelist


# --------------------------------------------------------------------------- #
# evidence predicates
# --------------------------------------------------------------------------- #


def _field_value(metadata: PeMetadata, field_name: str) -> Any:
    if field_name == "is_pe":
        return metadata.is_pe
    if field_name == "is_dll":
        return metadata.is_dll
    if field_name == "architecture":
        return metadata.architecture
    return getattr(metadata, field_name, None)


def _matches(
    predicate: dict[str, Any],
    *,
    executable: Path,
    executable_name: str,
    metadata: PeMetadata,
) -> bool:
    kind = predicate.get("type")
    if kind == "executable_name":
        candidates = [str(item).lower() for item in predicate.get("any_of", [])]
        return executable_name.lower() in candidates
    if kind == "sibling_file":
        # A file that must exist beside the executable. Static, read-only, and much
        # stronger than a name: it is how Node.js and FFmpeg builds are identified,
        # because neither ships a version resource.
        wanted = [str(item).lower() for item in predicate.get("any_of", [])]
        try:
            siblings = {entry.name.lower() for entry in executable.parent.iterdir() if entry.is_file()}
        except OSError:
            return False
        return any(item in siblings for item in wanted)
    if kind == "pe_static":
        value = _field_value(metadata, str(predicate.get("field", "")))
        if value is None:
            return False
        if "equals" in predicate:
            return value == predicate["equals"]
        if "contains" in predicate:
            return isinstance(value, str) and str(predicate["contains"]).lower() in value.lower()
        return False
    # Unknown predicate types never match: silently ignoring one would let a
    # whitelist entry claim evidence it does not actually have.
    return False


def match_entry(
    entry: WhitelistEntry,
    *,
    executable_name: str,
    metadata: PeMetadata,
    executable: Path | None = None,
) -> bool:
    target = Path(executable) if executable is not None else Path(metadata.path)
    for predicate in entry.evidence_all:
        if not _matches(predicate, executable=target, executable_name=executable_name, metadata=metadata):
            return False
    if entry.evidence_any and not any(
        _matches(predicate, executable=target, executable_name=executable_name, metadata=metadata)
        for predicate in entry.evidence_any
    ):
        return False
    return bool(entry.evidence_all or entry.evidence_any)


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    """One object found under a data root, classified but not yet registered."""

    object_root: Path
    directory_name: str
    management: str
    data_root_id: str | None = None
    #: Posix path below the data root, when the caller could name it. This is what makes the
    #: id an **address** instead of a directory name (draft §140 / ADR-0051).
    relative_path: str | None = None
    capability_id: str | None = None
    kind: str | None = None
    version: str | None = None
    versions: tuple[str, ...] = ()
    active_version: str | None = None
    architecture: str | None = None
    entrypoints: tuple[str, ...] = ()
    executable: Path | None = None
    probe_level: int | None = None
    source_kind: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def external_id(self) -> str:
        """Stable id namespaced by the data root **and by the object's path below it**.

        `external/<root>/<name>` assumed one level, which was enough while `adopt` only
        accepted direct children. §140 allows any depth, and then two objects called `bin`
        under one root would have collided; the path is the addressing that was missing. A
        depth-1 object produces the identical string (a one-component path), so nothing
        recorded before §140 moves.
        """

        prefix = slug(self.data_root_id) if self.data_root_id else "root"
        tail = self.relative_path or self.directory_name
        parts = [part for part in tail.replace("\\", "/").split("/") if part]
        return f"external/{prefix}/" + "/".join(slug(part) for part in parts)

    def to_document(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "directory_name": self.directory_name,
            "data_root_id": self.data_root_id,
            "path": str(self.object_root),
            "management": self.management,
            "capability_id": self.capability_id,
            "capability_kind": self.kind,
            "version": self.version,
            "versions": list(self.versions),
            "active_version": self.active_version,
            "architecture": self.architecture,
            "entrypoints": list(self.entrypoints),
            "executable": str(self.executable) if self.executable else None,
            "probe_level": self.probe_level,
            "source_kind": self.source_kind,
            "evidence": self.evidence,
            "notes": self.notes,
        }


@dataclass
class DiscoveryReport:
    data_root_id: str | None
    data_root_path: str
    whitelist_revision: str
    scanned_objects: int
    files_inspected: int
    candidates: list[Candidate]
    truncated: bool = False

    def counts(self) -> dict[str, int]:
        by_class: dict[str, int] = {}
        for candidate in self.candidates:
            by_class[candidate.management] = by_class.get(candidate.management, 0) + 1
        return by_class

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "data_root_id": self.data_root_id,
            "data_root_path": self.data_root_path,
            "whitelist_revision": self.whitelist_revision,
            "scanned_objects": self.scanned_objects,
            "files_inspected": self.files_inspected,
            "truncated": self.truncated,
            "counts": self.counts(),
            "candidates": [candidate.to_document() for candidate in self.candidates],
        }


def slug(value: str, *, max_length: int = 48) -> str:
    """Turn a directory name into a schema-valid id fragment (``^[a-z0-9][a-z0-9._/-]*``)."""

    slugged = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-._")
    slugged = re.sub(r"-{2,}", "-", slugged)[:max_length].strip("-._")
    return slugged or "object"


def _iter_executables(root: Path, limits: dict[str, int]) -> tuple[list[tuple[Path, int]], bool]:
    """Bounded, reparse-point-free walk for PE images, **shallowest first**.

    Returns ``(executable, directory_depth_below_root)`` pairs; ``truncated`` says the
    per-object file cap was hit. Depth is reported so the matcher can require an
    object's *own* entrypoint rather than a tool bundled deep inside it.

    The walk is **breadth-first**, and that is a correctness property rather than a detail:
    the cap is a *budget*, so a depth-first walk spends it inside the first big subtree it
    meets. Measured (§142): `D:\\env_apps\\Git` keeps its own `bin\\git.exe` at depth 1, but a
    depth-first walk of 400 files never got past `usr\\`, so the tree came out `unmanaged`
    — same tree, same predicate, different answer depending on walk order. Shallowest-first
    also matches how the matcher already chooses among several candidates.

    The walk descends through the `\\\\?\\` form so a data root below `MAX_PATH` works regardless of
    the machine's long-path policy, and returns **native** paths — the prefix is ours, not the
    caller's (draft §38, sharing `paths.extended_path` with `search`).
    """

    from ..paths import extended_path, native_path

    found: list[tuple[Path, int]] = []
    truncated = False
    walk_root = Path(extended_path(root))
    base_depth = len(walk_root.parts)
    queue: deque[Path] = deque([walk_root])
    while queue:
        current = queue.popleft()
        try:
            entries = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if len(found) >= limits["max_files_per_object"]:
                return found, True
            try:
                if entry.is_symlink() or (entry.is_dir() and _is_reparse(entry)):
                    continue
                if entry.is_dir():
                    if len(entry.parts) - base_depth < limits["max_depth"]:
                        queue.append(entry)
                    continue
            except OSError:
                continue
            if is_probably_executable(entry):
                found.append(
                    (Path(native_path(entry)), len(entry.parent.parts) - base_depth)
                )
    return found, truncated


def _is_reparse(path: Path) -> bool:
    from ..paths import is_reparse_point

    return is_reparse_point(path)


def _relative_inside(target: Path, data_root_path: Path | None) -> str | None:
    """`target`'s posix path below `data_root_path`, or None when no root was given.

    A root that does not contain the object is a caller mistake, not a verdict about the
    object: the id would quietly fall back to the directory name and could then collide with
    a same-named object at another depth — the collision §140 exists to remove. A data root
    is a scope, not an object, so it cannot be classified as its own child either.
    """

    if data_root_path is None:
        return None
    try:
        relative = target.relative_to(data_root_path).as_posix()
    except ValueError as exc:
        raise AirootError(
            "INVALID_INPUT",
            f"object is not inside the data root it was classified for: {target}",
            evidence=[f"data_root={data_root_path}"],
        ) from exc
    if relative == ".":
        raise AirootError(
            "INVALID_INPUT",
            f"a data root is not one of its own objects: {target}",
            evidence=[f"data_root={data_root_path}"],
        )
    return relative


def classify_object(
    entry: Path,
    *,
    data_root_id: str | None = None,
    data_root_path: Path | None = None,
    whitelist: Whitelist | None = None,
) -> Candidate:
    """Classify one object inside a data root, at any depth. Pure read."""

    rules = whitelist or load_whitelist()
    target = Path(entry)
    relative_path = _relative_inside(target, data_root_path)
    if not target.is_dir():
        raise AirootError("INVALID_INPUT", f"an adopted object must be a directory: {target}")
    if _is_reparse(target):
        return Candidate(
            object_root=target,
            directory_name=target.name,
            management="quarantined",
            data_root_id=data_root_id,
            relative_path=relative_path,
            notes=["reparse point: not followed, not classified"],
        )
    exclusion = rules.exclusion_for(target.name)
    if exclusion is not None:
        return Candidate(
            object_root=target,
            directory_name=target.name,
            management="excluded",
            data_root_id=data_root_id,
            relative_path=relative_path,
            notes=[exclusion.reason],
        )
    executables, _capped = _iter_executables(target, rules.limits)
    classified = _classify_object(target, executables, rules, probe_cache={})
    if classified is None:
        classified = _unmanaged(target, executables)
    classified.data_root_id = data_root_id
    classified.relative_path = relative_path
    return classified


def discover_data_root(
    *,
    path: Path,
    data_root_id: str | None = None,
    whitelist: Whitelist | None = None,
) -> DiscoveryReport:
    """Classify the top-level objects of one data root. Pure read."""

    root = Path(path)
    if not root.is_dir():
        raise AirootError("DATA_ROOT_MISSING", f"data root is not a directory: {root}")
    rules = whitelist or load_whitelist()

    candidates: list[Candidate] = []
    files_inspected = 0
    truncated = False
    try:
        objects = sorted(entry for entry in root.iterdir() if entry.is_dir() or entry.is_file())
    except (OSError, PermissionError) as exc:
        raise AirootError("DATA_ROOT_MISSING", f"data root is unreadable: {root}", evidence=[str(exc)]) from exc

    for index, entry in enumerate(objects):
        if index >= rules.limits["max_objects"]:
            truncated = True
            break
        if entry.is_file():
            continue  # a data root's objects are directories in v1
        if _is_reparse(entry):
            candidates.append(
                Candidate(
                    object_root=entry,
                    directory_name=entry.name,
                    management="quarantined",
                    data_root_id=data_root_id,
                    notes=["reparse point: not followed, not classified"],
                )
            )
            continue

        exclusion = rules.exclusion_for(entry.name)
        if exclusion is not None:
            candidates.append(
                Candidate(
                    object_root=entry,
                    directory_name=entry.name,
                    management="excluded",
                    data_root_id=data_root_id,
                    notes=[exclusion.reason],
                )
            )
            continue

        executables, capped = _iter_executables(entry, rules.limits)
        files_inspected += len(executables)
        truncated = truncated or capped
        classified = _classify_object(entry, executables, rules, probe_cache={})
        if classified is None:
            classified = _unmanaged(entry, executables)
        classified.data_root_id = data_root_id
        candidates.append(classified)
    for candidate in candidates:
        candidate.relative_path = _relative_inside(candidate.object_root, root)
    return DiscoveryReport(
        data_root_id=data_root_id,
        data_root_path=str(root),
        whitelist_revision=rules.revision,
        scanned_objects=min(len(objects), rules.limits["max_objects"]),
        files_inspected=files_inspected,
        candidates=candidates,
        truncated=truncated,
    )


def _unmanaged(entry: Path, executables: Iterable[tuple[Path, int]]) -> Candidate:
    return Candidate(
        object_root=entry,
        directory_name=entry.name,
        management="unmanaged",
        notes=[f"no whitelist entry matched ({len(list(executables))} executable(s) inspected)"],
    )


def _rule_for(
    executable: Path, metadata: PeMetadata, rules: Whitelist
) -> WhitelistEntry | None:
    for rule in rules.entries:
        if match_entry(rule, executable_name=executable.name, metadata=metadata, executable=executable):
            return rule
    return None


def _classify_object(
    entry: Path,
    executables: list[tuple[Path, int]],
    rules: Whitelist,
    *,
    probe_cache: dict[Path, PeMetadata],
) -> Candidate | None:
    """Match the object by its **own** entrypoints, not by a tool bundled inside it.

    Only executables within ``max_relative_depth`` of the object root are eligible (a
    Python install keeps ``python.exe`` at the root, a JDK at ``bin\\``, while Flutter's
    bundled ``bin\\mingit\\cmd\\git.exe`` is three levels down). The shallowest match
    decides the object's capability; **all** matching versions are collected, because one
    object may host several (nvm keeps v22.14.0/v22.15.0/v26.8.1 side by side).
    """

    eligible = [(path, depth) for path, depth in executables if depth <= rules.limits["max_relative_depth"]]
    ordered = sorted(eligible, key=lambda item: (item[1], str(item[0]).lower()))

    matches: list[tuple[WhitelistEntry, Path, int, PeMetadata]] = []
    seen_versions: set[tuple[str, str]] = set()
    for executable, depth in ordered:
        metadata = probe_cache.get(executable)
        if metadata is None:
            metadata = probe_executable(executable)
            probe_cache[executable] = metadata
        if not metadata.is_pe:
            continue
        rule = _rule_for(executable, metadata, rules)
        if rule is None:
            continue
        version = _version_for(rule, metadata)
        key = (rule.capability_id, version or str(executable))
        if key in seen_versions:
            continue
        seen_versions.add(key)
        matches.append((rule, executable, depth, metadata))
        if len(matches) >= rules.limits["max_versions_per_object"]:
            break

    if not matches:
        return None

    versions = sorted(
        {
            version
            for _rule, _exe, _depth, _meta in matches
            for version in [_version_for(_rule, _meta)]
            if version
        }
    )
    active_version, active_evidence = _observe_active_version(
        entry, matches[0][0], versions, rules.limits, probe_cache
    )

    # The primary match must be the same version we report: if a marker proved which
    # version is active, that version's entrypoint *is* the object's entrypoint.
    # Otherwise `version` and `executable` would describe two different things.
    rule, executable, depth, metadata = matches[0]
    if active_version:
        for candidate_rule, candidate_exe, candidate_depth, candidate_meta in matches:
            if _version_for(candidate_rule, candidate_meta) == active_version:
                rule, executable, depth, metadata = (
                    candidate_rule,
                    candidate_exe,
                    candidate_depth,
                    candidate_meta,
                )
                break

    evidence: list[dict[str, Any]] = [
        {
            "kind": "pe_static",
            "detail": "; ".join(f"{key}={value}" for key, value in metadata.facts().items())
            or "PE image with no version resource",
            "path": str(executable),
        },
        {
            "kind": "discovery",
            "detail": f"entrypoint depth {depth} below the object root",
            "path": str(executable),
        },
    ]
    if len(versions) > 1:
        evidence.append(
            {
                "kind": "version_set",
                "detail": f"observed versions: {', '.join(versions)}",
                "path": str(entry),
            }
        )
    if rule.weak_evidence:
        evidence.append(
            {
                "kind": "weak_evidence",
                "detail": rule.weak_reason or "name-only matching accepted for this capability",
                "path": str(executable),
            }
        )
    if active_evidence is not None:
        evidence.append(active_evidence)

    return Candidate(
        object_root=entry,
        directory_name=entry.name,
        management="external_reference",
        capability_id=rule.capability_id,
        kind=rule.kind,
        # `version` is "the version this object currently provides": the observed active
        # one when a marker proves it, otherwise the primary (shallowest) entrypoint's.
        # `active_version` stays null in the latter case so the uncertainty is visible.
        version=active_version or _version_for(rule, metadata),
        versions=tuple(versions),
        active_version=active_version,
        architecture=metadata.architecture,
        entrypoints=_observed_entrypoints(executable, matches, entry),
        executable=executable,
        probe_level=metadata.probe_level or 2,
        source_kind="pe_static",
        evidence=evidence,
    )


def _relative_entrypoint(executable: Path, object_root: Path) -> str:
    """The entrypoint **relative to the object root** — the directory is part of the fact.

    Recording only the basename loses where the entry actually is: ``java.exe`` lives at
    ``bin\\java.exe`` under ``D:\\env\\Java``, and a name without its directory made both
    ``where`` and session activation point at a directory that holds no entrypoint.
    """

    try:
        return executable.relative_to(object_root).as_posix()
    except ValueError:  # pragma: no cover - matches are always below the object root
        return executable.name


def _observed_entrypoints(
    primary: Path, matches: list[tuple[Any, Path, int, Any]], object_root: Path
) -> tuple[str, ...]:
    """Every entrypoint this object demonstrably has, the chosen one first."""

    ordered = [_relative_entrypoint(primary, object_root)]
    for _rule, executable, _depth, _metadata in matches:
        relative = _relative_entrypoint(executable, object_root)
        if relative not in ordered:
            ordered.append(relative)
    return tuple(ordered)


def _observe_active_version(
    object_root: Path,
    rule: WhitelistEntry,
    versions: list[str],
    limits: dict[str, int],
    probe_cache: dict[Path, PeMetadata],
) -> tuple[str | None, dict[str, Any] | None]:
    """Read the object's reparse-point markers to learn which version is active.

    Version managers expose the active version as a link (nvm's ``nodejs`` junction
    points at ``v26.8.1``). Reading the link target is a single ``readlink`` — the link
    itself is **not** followed or traversed, which keeps v0.3 §9.4's rule intact.

    Nothing is guessed: when no marker resolves to one of the observed versions, the
    result is ``None``.
    """

    if not versions:
        return None, None
    try:
        entries = sorted(object_root.iterdir())
    except (OSError, PermissionError):
        return None, None
    markers = [entry for entry in entries if _is_reparse(entry)][: limits["max_active_markers"]]

    for marker in markers:
        try:
            target_text = os.readlink(marker)
        except OSError:
            continue
        target = _normalize_link_target(target_text, object_root)
        if target is None or not target.is_dir():
            continue
        shallow = {**limits, "max_depth": limits["max_relative_depth"]}
        linked_executables, _capped = _iter_executables(target, shallow)
        for executable, _depth in linked_executables:
            metadata = probe_cache.get(executable)
            if metadata is None:
                metadata = probe_executable(executable)
                probe_cache[executable] = metadata
            if not metadata.is_pe:
                continue
            if not match_entry(rule, executable_name=executable.name, metadata=metadata, executable=executable):
                continue
            version = _version_for(rule, metadata)
            if version and version in versions:
                return version, {
                    "kind": "junction_target",
                    "detail": f"{marker.name} -> {target_text}",
                    "path": str(marker),
                }
    return None, None


def _normalize_link_target(target_text: str, object_root: Path) -> Path | None:
    """Turn a Windows reparse target into a path, without resolving it recursively."""

    text = target_text
    for prefix in ("\\\\?\\", "\\??\\"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    try:
        target = Path(text)
    except (OSError, ValueError):  # pragma: no cover - defensive
        return None
    return target if target.is_absolute() else (object_root / target)


def _version_for(rule: WhitelistEntry, metadata: PeMetadata) -> str | None:
    source = (rule.version_source or "").split(":", 1)
    if len(source) == 2 and source[0] == "pe_static":
        return getattr(metadata, source[1], None) or metadata.product_version
    return metadata.file_version or metadata.product_version


def discover_all(registry: Any, *, whitelist: Whitelist | None = None) -> list[DiscoveryReport]:
    """Every active data root, in registration order. Read-only."""

    from ..registry.entities import data_root_from_row

    reports: list[DiscoveryReport] = []
    for row in registry.data_roots():
        data_root = data_root_from_row(row)
        reports.append(
            discover_data_root(path=Path(data_root.path), data_root_id=data_root.data_root_id, whitelist=whitelist)
        )
    return reports


__all__ = [
    "Candidate",
    "DiscoveryReport",
    "Exclusion",
    "Whitelist",
    "WhitelistEntry",
    "WHITELIST_PATH",
    "classify_object",
    "discover_all",
    "discover_data_root",
    "load_whitelist",
    "match_entry",
    "slug",
]
