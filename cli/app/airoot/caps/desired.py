"""The ``desired`` layer: what the operator *wants*, as opposed to what exists (§17).

The five-layer model (desired / declared / physical / effective / historical) had exactly one
layer with no code at all. This module is it, and the boundary is what makes it useful:

* **desired is an input, not an authority.** It lives in ``state/desired.json`` following the
  shape 规划 §17 prescribes (``schema_version``, ``manifest_id``, ``manifest_revision``,
  ``platform``, ``architectures``, ``capabilities[]``). It never replaces the registry, and
  nothing here writes declared state;
* **``pin`` expresses intent and produces a plan** (§15.4:1604). It must not change the active
  binding directly — that still happens only at the transaction's ``ACTIVE_BOUND`` point, after
  an approval. So pinning is: record the wish, then *offer* a plan that would satisfy it;
* a manifest may not contain shell. §17 lists `curl | sh`, `pip install` and arbitrary
  PowerShell as things a manifest must never contain, so the loader refuses them by name.

Version constraints use the same grammar as `where --version` (``>=3.11,<3.13``), because a pin
that means something different from a query would be a trap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canon import digest_bytes, canonical_bytes
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from .boundary import load_capabilities
from .version import satisfies

DESIRED_RELATIVE = "state/desired.json"
MANIFEST_SCHEMA_VERSION = 1

# §17: a manifest expresses desired state, never a script.
FORBIDDEN_MANIFEST_FRAGMENTS = (
    "curl | sh",
    "curl|sh",
    "iwr | iex",
    "iwr|iex",
    "invoke-expression",
    "pip install",
    "npm install -g",
    "powershell -",
    "cmd /c",
)


@dataclass(frozen=True)
class DesiredCapability:
    capability_id: str
    version: str | None = None
    scope: str = "machine"
    artifact_digest: str | None = None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"id": self.capability_id, "scope": self.scope}
        if self.version:
            document["version"] = self.version
        if self.artifact_digest:
            document["artifact_digest"] = self.artifact_digest
        return document


@dataclass
class DesiredManifest:
    manifest_id: str = "airoot.local/machine"
    manifest_revision: int = 0
    platform: str = "windows"
    architectures: tuple[str, ...] = ("x64",)
    capabilities: list[DesiredCapability] = field(default_factory=list)
    source: dict[str, Any] | None = None
    policies: dict[str, Any] = field(default_factory=dict)

    def find(self, capability_id: str) -> DesiredCapability | None:
        return next((item for item in self.capabilities if item.capability_id == capability_id), None)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_id": self.manifest_id,
            "manifest_revision": self.manifest_revision,
            "platform": self.platform,
            "architectures": list(self.architectures),
            "source": self.source,
            "capabilities": [item.to_document() for item in self.capabilities],
            "policies": self.policies,
        }

    def fingerprint(self) -> str:
        return digest_bytes(canonical_bytes(self.to_document()))


def desired_path(root: Path) -> Path:
    return Path(root) / DESIRED_RELATIVE


def load_desired(root: Path) -> DesiredManifest:
    """Read the desired manifest. Absent is an empty manifest, not an error."""

    path = desired_path(root)
    if not path.is_file():
        return DesiredManifest()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(
            "INVALID_INPUT",
            f"the desired manifest is unreadable: {path}",
            evidence=[str(exc), "fix or delete it; a wrong manifest silently plans the wrong thing"],
        ) from exc
    if int(document.get("schema_version", 0)) != MANIFEST_SCHEMA_VERSION:
        raise AirootError(
            "INVALID_INPUT",
            f"unsupported desired-manifest schema_version: {document.get('schema_version')}",
            evidence=[f"this build reads {MANIFEST_SCHEMA_VERSION}"],
        )
    text = json.dumps(document, ensure_ascii=False).lower()
    for fragment in FORBIDDEN_MANIFEST_FRAGMENTS:
        if fragment in text:
            # §17 is explicit: a manifest expresses state, never a command to run.
            raise AirootError(
                "INVALID_INPUT",
                "the desired manifest contains a shell fragment",
                evidence=[f"found {fragment!r}", "a manifest declares state; it never carries a script"],
            )
    capabilities = [
        DesiredCapability(
            capability_id=str(item["id"]),
            version=item.get("version"),
            scope=str(item.get("scope", "machine")),
            artifact_digest=item.get("artifact_digest"),
        )
        for item in document.get("capabilities", [])
    ]
    return DesiredManifest(
        manifest_id=str(document.get("manifest_id", "airoot.local/machine")),
        manifest_revision=int(document.get("manifest_revision", 0)),
        platform=str(document.get("platform", "windows")),
        architectures=tuple(str(item) for item in document.get("architectures", ["x64"])),
        capabilities=capabilities,
        source=document.get("source"),
        policies=document.get("policies") or {},
    )


def save_desired(root: Path, manifest: DesiredManifest) -> Path:
    path = desired_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def pin(
    root: Path,
    *,
    capability_id: str,
    version: str | None,
    scope: str = "machine",
    clock: Clock = SYSTEM_CLOCK,
) -> DesiredManifest:
    """Record an intent. This writes *desired* state only — never the registry, never a binding."""

    if not capability_id:
        raise AirootError("INVALID_INPUT", "pin needs a capability id")
    if scope not in {"machine", "project", "session"}:
        raise AirootError(
            "INVALID_INPUT", f"unsupported pin scope: {scope}", evidence=["machine, project, session"]
        )
    # §91: the frozen list is the boundary of what AIROOT may manage at all, and five other entry
    # points that name a capability apply it (`plan`, `adopt --mode import`, `scope decide`,
    # `capability check`, `source resolve`). This one did not, so an intent for a name no plan can ever
    # satisfy was recorded with exit 0 — and the pin report then blamed the *second* blocker
    # ("no trusted source is declared for X") while the first one, that X is not a capability, stayed
    # unsaid. Refusing here keeps the same code every other entry point uses.
    frozen = load_capabilities()
    if frozen.by_id(capability_id) is None:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"no frozen capability is declared for {capability_id}",
            evidence=[
                f"revision {frozen.revision} declares: {', '.join(sorted(frozen.ids()))}",
                "an intent outside the frozen list is one no plan can satisfy; propose the name "
                "through the growth path first (planning §15.4: propose -> freeze -> whitelist)",
            ],
        )
    if version:
        # Validate the constraint now: a pin that cannot be parsed would fail later, at a worse time.
        satisfies("0.0.0", version)
    manifest = load_desired(root)
    existing = manifest.find(capability_id)
    if existing is not None:
        manifest.capabilities = [
            item for item in manifest.capabilities if item.capability_id != capability_id
        ]
    manifest.capabilities.append(
        DesiredCapability(capability_id=capability_id, version=version, scope=scope)
    )
    manifest.capabilities.sort(key=lambda item: item.capability_id)
    manifest.manifest_revision += 1
    save_desired(root, manifest)
    return manifest


def clear_pin(root: Path, *, capability_id: str) -> DesiredManifest:
    manifest = load_desired(root)
    remaining = [item for item in manifest.capabilities if item.capability_id != capability_id]
    if len(remaining) == len(manifest.capabilities):
        raise AirootError(
            "NOT_FOUND",
            f"{capability_id} is not pinned",
            evidence=[item.capability_id for item in manifest.capabilities],
        )
    manifest.capabilities = remaining
    manifest.manifest_revision += 1
    save_desired(root, manifest)
    return manifest


# --------------------------------------------------------------------------- #
# desired vs declared
# --------------------------------------------------------------------------- #


@dataclass
class SyncEntry:
    capability_id: str
    desired_version: str | None
    scope: str
    active_version: str | None
    active_instance_id: str | None
    in_sync: bool
    detail: str

    def to_document(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "desired_version": self.desired_version,
            "scope": self.scope,
            "active_version": self.active_version,
            "active_instance_id": self.active_instance_id,
            "in_sync": self.in_sync,
            "detail": self.detail,
        }


def evaluate(registry: Any, manifest: DesiredManifest) -> list[SyncEntry]:
    """Compare desired against **declared** (the active binding). Read-only."""

    entries: list[SyncEntry] = []
    for desired in manifest.capabilities:
        active = None
        for row in registry.active_bindings_for_capability(desired.capability_id):
            instance = registry.instance(row["instance_id"])
            if instance is None:
                continue
            active = instance
            break
        if active is None:
            entries.append(
                SyncEntry(
                    capability_id=desired.capability_id,
                    desired_version=desired.version,
                    scope=desired.scope,
                    active_version=None,
                    active_instance_id=None,
                    in_sync=False,
                    detail="nothing active satisfies this pin; a plan is needed",
                )
            )
            continue
        version = str(active["version"])
        ok = satisfies(version, desired.version)
        entries.append(
            SyncEntry(
                capability_id=desired.capability_id,
                desired_version=desired.version,
                scope=desired.scope,
                active_version=version,
                active_instance_id=str(active["instance_id"]),
                in_sync=ok,
                detail=(
                    "the active instance satisfies the pin"
                    if ok
                    else f"active {version} does not satisfy {desired.version}"
                ),
            )
        )
    return entries


__all__ = [
    "DESIRED_RELATIVE",
    "DesiredCapability",
    "DesiredManifest",
    "FORBIDDEN_MANIFEST_FRAGMENTS",
    "MANIFEST_SCHEMA_VERSION",
    "SyncEntry",
    "clear_pin",
    "desired_path",
    "evaluate",
    "load_desired",
    "pin",
    "save_desired",
]
