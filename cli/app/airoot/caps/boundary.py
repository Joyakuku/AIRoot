"""The capability boundary: what AIROOT is allowed to manage at all (draft §15).

ADR-0004's promise is "omnipotent about capabilities, not about software". That promise is only
credible if "is there a frozen capability for this?" is a **machine-checkable fact** rather than a
sentence in a document. ``policy/capabilities.json`` is that fact, and this module is its only
reader.

The boundary is deliberately boring, and every part of it is refusal:

1. no frozen capability -> ``unmanaged``: reported, never adopted (draft §15.2-1);
2. source/integrity unverifiable -> ``reference`` or ``quarantined`` only (§15.2-2);
3. needs an irreversible side effect (service, driver, task, persistent elevation) -> ``excluded``
   (§15.2-3, §15.3).

This module never installs, adopts or repairs anything. It answers one question and lists the
evidence for the answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import CLI_ROOT
from ..exits import AirootError
from .inventory import SCOPES as BINDING_SCOPES

CAPABILITIES_PATH = CLI_ROOT / "app" / "airoot" / "policy" / "capabilities.json"

# The published sideEffect enum (common.schema.json). A ceiling must use these spellings.
SIDE_EFFECTS: tuple[str, ...] = (
    "none",
    "derived_cache",
    "writes_store",
    "writes_registry",
    "writes_path",
    "executes_scripts",
    "mutates_system",
    "deletes_source",
    "moves_source",
)

# Effects AIROOT will never accept as a *requirement* of a managed object (draft §15.2-3).
IRREVERSIBLE_EFFECTS = frozenset({"mutates_system", "executes_scripts", "deletes_source", "moves_source"})

KINDS = ("tool", "runtime")

_ALLOWED_KEYS = frozenset({"schema_version", "revision", "capabilities", "notes"})
_ALLOWED_CAPABILITY_KEYS = frozenset({"capability_id", "kind", "entry", "side_effects", "scope", "notes"})


@dataclass(frozen=True)
class Capability:
    capability_id: str
    kind: str
    entry: str
    side_effects: tuple[str, ...] = ("none",)
    scope: tuple[str, ...] = ()
    notes: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind,
            "entry": self.entry,
            "side_effects": list(self.side_effects),
            "scope": list(self.scope),
        }


@dataclass(frozen=True)
class CapabilityList:
    revision: str
    capabilities: tuple[Capability, ...] = ()

    def by_id(self, capability_id: str) -> Capability | None:
        return next((item for item in self.capabilities if item.capability_id == capability_id), None)

    def ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.capabilities)


def load_capabilities(path: Path | None = None) -> CapabilityList:
    target = Path(path) if path is not None else CAPABILITIES_PATH
    if not target.is_file():
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"the frozen capability list is missing: {target}",
            evidence=["without it nothing may be adopted; see draft §15.4 for the growth path"],
        )
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED", f"the capability list is unreadable: {target}", evidence=[str(exc)]
        ) from exc
    if not isinstance(document, dict):
        raise AirootError("CAPABILITY_NOT_DECLARED", f"the capability list is not an object: {target}")
    unknown = sorted(set(document) - _ALLOWED_KEYS)
    if unknown:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"capability list has unknown keys: {', '.join(unknown)}",
            evidence=[f"allowed: {', '.join(sorted(_ALLOWED_KEYS))}"],
        )

    capabilities: list[Capability] = []
    seen: set[str] = set()
    for item in document.get("capabilities", []):
        if not isinstance(item, dict):
            raise AirootError("CAPABILITY_NOT_DECLARED", "every capability entry must be an object")
        extra = sorted(set(item) - _ALLOWED_CAPABILITY_KEYS)
        if extra:
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability entry has unknown keys: {', '.join(extra)}",
                evidence=[f"entry={item.get('capability_id')}"],
            )
        capability_id = str(item.get("capability_id", ""))
        if not capability_id:
            raise AirootError("CAPABILITY_NOT_DECLARED", "a capability entry has no capability_id")
        if capability_id in seen:
            raise AirootError(
                "CAPABILITY_NOT_DECLARED", f"capability {capability_id} is declared twice"
            )
        seen.add(capability_id)
        kind = str(item.get("kind", ""))
        if kind not in KINDS:
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability {capability_id} has an unknown kind: {kind!r}",
                evidence=[f"known kinds: {', '.join(KINDS)}"],
            )
        effects = tuple(str(value) for value in item.get("side_effects", ["none"]))
        bad = [value for value in effects if value not in SIDE_EFFECTS]
        if bad:
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability {capability_id} declares unknown side effects: {', '.join(bad)}",
                evidence=[f"published enum: {', '.join(SIDE_EFFECTS)}"],
            )
        forbidden = sorted(set(effects) & IRREVERSIBLE_EFFECTS)
        if forbidden:
            # A capability that *requires* an irreversible effect is outside the boundary by
            # construction (draft §15.2-3), so freezing one would contradict the contract.
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability {capability_id} requires effects outside the boundary: {', '.join(forbidden)}",
                evidence=["services, drivers, tasks and persistent elevation are excluded (draft §15.3)"],
            )
        # `scope` names the binding scopes this capability may be used at (draft §15.2). It is
        # declared-only today — nothing enforces it yet (draft §47.5) — but the vocabulary still has
        # to be real: `str(v) for v in "machine"` would read a bare string one character at a time,
        # and a typo would ride along unnoticed into `capability list`'s agent-facing answer.
        declared_scopes = item.get("scope", [])
        if not isinstance(declared_scopes, list) or not all(
            isinstance(value, str) for value in declared_scopes
        ):
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability {capability_id} declares scope as something other than a list of names",
                evidence=["a bare string would be read one character at a time"],
            )
        unknown_scopes = sorted({value for value in declared_scopes if value not in BINDING_SCOPES})
        if unknown_scopes:
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"capability {capability_id} declares unknown scopes: {', '.join(unknown_scopes)}",
                evidence=[f"known scopes: {', '.join(sorted(BINDING_SCOPES))}"],
            )
        capabilities.append(
            Capability(
                capability_id=capability_id,
                kind=kind,
                entry=str(item.get("entry", "")),
                side_effects=effects,
                scope=tuple(declared_scopes),
                notes=str(item.get("notes", "")),
            )
        )
    return CapabilityList(
        revision=str(document.get("revision", "cap-0")), capabilities=tuple(capabilities)
    )


# --------------------------------------------------------------------------- #
# the three admission conditions
# --------------------------------------------------------------------------- #


@dataclass
class Admission:
    """Whether one object may be managed, and which condition decided it."""

    target: str
    capability_id: str | None
    verdict: str  # adoptable | reference_only | excluded | unmanaged
    conditions: dict[str, bool] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    remediation: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "target": self.target,
            "capability_id": self.capability_id,
            "verdict": self.verdict,
            "conditions": dict(self.conditions),
            "evidence": list(self.evidence),
            "remediation": self.remediation,
            "reason_code": (
                "SUCCESS" if self.verdict in {"adoptable", "reference_only"} else "CAPABILITY_NOT_DECLARED"
            ),
        }


def check_admission(
    *,
    target: Path,
    capability_id: str | None,
    source_verifiable: bool = True,
    requires_irreversible_effect: bool = False,
    capabilities: CapabilityList | None = None,
) -> Admission:
    """Apply draft §15.2's three conditions, in order, with the evidence for each."""

    frozen = capabilities or load_capabilities()
    path = Path(target)
    admission = Admission(target=str(path), capability_id=capability_id, verdict="unmanaged")

    known = capability_id is not None and frozen.by_id(capability_id) is not None
    admission.conditions["has_frozen_capability"] = known
    if capability_id is None:
        admission.evidence.append("no capability matched this object")
    elif not known:
        admission.evidence.append(f"{capability_id} is not in the frozen list (revision {frozen.revision})")

    admission.conditions["source_verifiable"] = bool(source_verifiable)
    if not source_verifiable:
        admission.evidence.append("source or integrity cannot be verified")

    admission.conditions["no_irreversible_effect_required"] = not requires_irreversible_effect
    if requires_irreversible_effect:
        admission.evidence.append("managing it would need an irreversible side effect")

    if not known:
        admission.verdict = "unmanaged"
        admission.remediation = (
            "to manage it: propose a capability, freeze it in policy/capabilities.json, then add a "
            "whitelist evidence predicate (draft §15.4)"
        )
        return admission
    if requires_irreversible_effect:
        admission.verdict = "excluded"
        admission.remediation = "nothing to do: this class is excluded on purpose (draft §15.3)"
        return admission
    if not source_verifiable:
        admission.verdict = "reference_only"
        admission.remediation = "record it as a reference; never import or recreate it"
        return admission
    admission.verdict = "adoptable"
    admission.remediation = f"airoot adopt {path} --mode reference"
    return admission


def check_whitelist_capabilities(whitelist: Any, capabilities: CapabilityList | None = None) -> list[str]:
    """Every whitelist entry must name a frozen capability. Returns the problems (empty is good).

    This is the drift guard between the two policy files: the whitelist says *how to recognise*
    an object, the capability list says *it is allowed to exist*. A whitelist entry without a
    frozen capability would silently widen the boundary.
    """

    frozen = capabilities or load_capabilities()
    return [
        f"whitelist entry {entry.capability_id} is not in the frozen capability list"
        for entry in whitelist.entries
        if frozen.by_id(entry.capability_id) is None
    ]


__all__ = [
    "Admission",
    "CAPABILITIES_PATH",
    "Capability",
    "IRREVERSIBLE_EFFECTS",
    "KINDS",
    "CapabilityList",
    "SIDE_EFFECTS",
    "check_admission",
    "check_whitelist_capabilities",
    "load_capabilities",
]
