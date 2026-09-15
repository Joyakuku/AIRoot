"""``where``: deterministic capability selection (v0.3 §9.10, steward-first per draft §6).

The order is project → session → (steward reference ↔ owned machine payload) → diagnostics:

1. current project binding, constraint satisfied and healthy;
2. current session binding, constraint satisfied and healthy;
3. a healthy external reference — **a first-class candidate, not a fallback**;
4. a healthy machine-scope owned binding;
5. ``unmanaged`` / ``quarantined`` objects: diagnostics only, never the default answer;
6. ``broken`` / ``stale`` / ``drifted`` / ``quarantined``: never selected as healthy.

Which of 3 and 4 comes first is a **policy** decision (``policy/selection-policy.json``),
defaulting to ``steward``: AIROOT is a hired butler, so an object the user already had is not
a second-class citizen behind something AIROOT installed (ADR-0004, ADR-0006).

When the owned payload is unusable and a healthy reference takes over, that is a **normal
degradation** (``CURRENT_SOURCE_DEGRADED``, exit 2), not a conflict. ``CONFLICT_MANAGED_BROKEN``
was the old managed-first answer; it is no longer emitted on this path.

**Steps 3 and 4 are machine-level discovery, so Zone W never takes part in them** (ADR-0022). The
contract says it in four places — "不允许进入 machine PATH" (规划 §3.2), "不会把这些路径加入 machine
PATH、stable launcher 或默认 `where` 结果" (规划 §5), "只能通过显式 session/project activation，不参与
机器级发现" (三大核心契约 :466), "不能被 machine `where` 发现" (验证方案 `P-002`). Steps 1 and 2 stay
open to W on purpose: those slots already require an explicit project/session identity, which is
exactly the "显式 activation" the contract permits. See ``machine_discoverable`` below for why the
exclusion is reported rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exits import AirootError
from ..paths import from_root_relative
from ..registry.entities import is_store_path, load_json
from ..schema_io import validate_self
from .effective import effective_state, machine_path, process_path, user_path
from .selection import SelectionPolicy, load_selection_policy
from .version import satisfies

SCOPE_PREFERENCE: tuple[tuple[str, int], ...] = (("project", 0), ("session", 1), ("machine", 2))

# Reason codes still reachable, kept named so the intent is greppable.
DEGRADED_TO_REFERENCE = "CURRENT_SOURCE_DEGRADED"
CONFLICT_MANAGED_BROKEN = "CONFLICT_MANAGED_BROKEN"


@dataclass
class WhereQuery:
    capability_id: str
    version: str | None = None
    scope: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    allow_external_fallback: bool = False


@dataclass
class _Candidate:
    binding_key: str
    instance_id: str
    scope: str
    zone: str
    version: str
    health: str
    management: str
    source: str
    path: str | None
    usable: bool
    version_ok: bool
    identity_match: bool
    # Which slot this candidate competes in: project/session/steward/machine, or None for
    # diagnostics-only candidates (unmanaged, quarantined, unprobed).
    slot: str | None = None
    # Set when the object *contains* a satisfying version that is not the active one.
    version_available_but_inactive: str | None = None
    # Whether this candidate may answer **machine-level** discovery (steps 3 and 4). False for
    # Zone W: "W 可写但不参与机器级发现" (ADR-0022). Carried on the candidate, and reported in the
    # response, because a healthy-looking row that is passed over with no visible reason is the
    # kind of silence this project treats as a defect.
    machine_discoverable: bool = True
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _executable_path(root: Path, store_path: str, entrypoints: list[str]) -> str | None:
    if not entrypoints:
        return None
    try:
        return str(from_root_relative(f"{store_path}/{entrypoints[0]}", root))
    except AirootError:
        return None


def _session_id_of(binding_key: str, scope: str) -> str | None:
    if scope != "session":
        return None
    parts = binding_key.split("/")
    return parts[1] if len(parts) > 2 else None


def _version_ok(version: str, constraint: str | None) -> bool:
    """Unknown never counts as satisfying: an unproven version is not a match.

    ``satisfies`` raises on an empty version, and ``where --version >=3.11`` must not turn a
    reference whose version was never probed into a silent match.
    """

    if constraint is None or not constraint.strip():
        return True
    if not version:
        return False
    return satisfies(version, constraint)


def _managed_candidates(registry: Any, query: WhereQuery, root: Path) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for row in registry.active_bindings_for_capability(query.capability_id):
        instance = registry.instance(row["instance_id"])
        if instance is None:
            continue
        entrypoints = [str(item) for item in load_json(instance["entrypoints_json"], [])]
        scope = str(row["scope"])
        zone = str(row["zone"])
        identity_match = True
        if scope == "project":
            identity_match = query.project_id is not None and query.project_id == row["project_id"]
        elif scope == "session":
            identity_match = query.session_id is not None and query.session_id == _session_id_of(str(row["binding_key"]), scope)
        if query.scope and scope != query.scope:
            identity_match = False
        healthy = str(instance["health"]) == "healthy"
        executable = _executable_path(root, str(instance["store_path"]), entrypoints)
        # A payload outside `store/` is never selectable, however healthy its row claims to be
        # (draft §66): `store` is the only payload storage (冻结契约 §5.3), so a declaration pointing
        # elsewhere cannot be honoured. It is still *reported* as a candidate — skipping it in silence
        # is the failure mode ADR-0022 already legislated against for Zone W.
        in_store = is_store_path(instance["store_path"])
        candidates.append(
            _Candidate(
                binding_key=str(row["binding_key"]),
                instance_id=str(instance["instance_id"]),
                scope=scope,
                zone=zone,
                version=str(instance["version"]),
                health=str(instance["health"]),
                management="managed",
                source="registry",
                path=executable,
                usable=healthy and in_store,
                version_ok=_version_ok(str(instance["version"]), query.version),                identity_match=identity_match,
                slot=scope,
                machine_discoverable=zone != "W",
                evidence=[
                    {"kind": "binding", "detail": f"{row['binding_key']} active at generation {row['generation']}"},
                    {
                        "kind": "registry",
                        "detail": f"lifecycle={instance['lifecycle_status']} health={instance['health']}",
                        "path": executable,
                        "digest": instance["artifact_digest"],
                    },
                    *(
                        []
                        if in_store
                        else [
                            {
                                "kind": "layout",
                                "detail": (
                                    f"store_path is not under store/: {instance['store_path']} "
                                    "(store is the only payload storage)"
                                ),
                            }
                        ]
                    ),
                ],
            )
        )
    return candidates


def _reference_version(row: Any) -> str:
    """The version this object currently provides: the observed active one when proven."""

    return str(row["active_version"] or row["version"] or "")


def _reference_candidates(registry: Any, query: WhereQuery) -> list[_Candidate]:
    """Registered external references, version-checked against their **active** version."""

    candidates: list[_Candidate] = []
    for row in registry.external_references():
        if row["capability_id"] != query.capability_id:
            continue
        management = str(row["management"])
        if management not in {"external_reference", "unmanaged", "quarantined"}:
            continue
        healthy = str(row["health"]) == "healthy"
        version = _reference_version(row)
        reference_like = management == "external_reference"
        version_ok = _version_ok(version, query.version)

        # A multi-version object may *contain* a satisfying version while its active one does
        # not. We do not switch the user's active version; we say so instead (draft §18.3-2).
        inactive_match: str | None = None
        if reference_like and not version_ok and query.version:
            for item in load_json(row["versions_json"], []):
                if str(item) != version and satisfies(str(item), query.version):
                    inactive_match = str(item)
                    break

        evidence: list[dict[str, Any]] = [
            {
                "kind": "external_reference",
                "detail": f"observed at {row['observed_at']} ({management})"
                + (f" active_version={version}" if version else ""),
                "path": str(row["path"]),
                "digest": row["observed_digest"],
            }
        ]

        # `executable` must name the entry actually run, not the object directory: the field
        # feeds `effective_state`, which compares the executable's **parent** against PATH.
        # Pointing it at the object root would report "not on PATH" for `...\bin\java.exe`.
        entrypoints = [str(item) for item in load_json(row["entrypoints_json"], [])]
        resolved_path = str(row["path"])
        entrypoint_exists = True
        if entrypoints:
            candidate_path = Path(str(row["path"])) / entrypoints[0]
            resolved_path = str(candidate_path)
            entrypoint_exists = candidate_path.is_file()
            if not entrypoint_exists:
                evidence.append(
                    {
                        "kind": "external_reference",
                        "detail": f"the recorded entrypoint {entrypoints[0]} is missing; the object is stale",
                        "path": resolved_path,
                    }
                )

        if inactive_match is not None:
            evidence.append(
                {
                    "kind": "version_set",
                    "detail": (
                        f"this object contains {inactive_match}, which satisfies {query.version}, "
                        f"but its active version is {version or 'unknown'}; switching versions is the user's call"
                    ),
                    "path": str(row["path"]),
                }
            )
        if reference_like and not version_ok and not version and query.version:
            evidence.append(
                {
                    "kind": "weak_evidence",
                    "detail": (
                        f"no version evidence was recorded for this object, so it cannot be proven "
                        f"to satisfy {query.version}"
                    ),
                    "path": str(row["path"]),
                }
            )

        candidates.append(
            _Candidate(
                binding_key=f"external/{row['external_id']}",
                instance_id=str(row["external_id"]),
                scope="machine",
                zone="R",
                version=version,
                health=str(row["health"]),
                management=management,
                source="path",
                path=resolved_path,
                usable=healthy and reference_like and entrypoint_exists,
                version_ok=version_ok,
                identity_match=True,
                slot="steward" if reference_like else None,
                version_available_but_inactive=inactive_match,
                evidence=evidence,
            )
        )
    return candidates


def _candidate_document(candidate: _Candidate) -> dict[str, Any]:
    return {
        "path": candidate.path or "",
        "management": candidate.management,
        "health": candidate.health,
        "usable": bool(candidate.usable and candidate.version_ok),
        "source": candidate.source,
        "machine_discoverable": bool(candidate.machine_discoverable),
        "evidence": candidate.evidence,
    }


def _slot_order(policy: SelectionPolicy) -> tuple[str, ...]:
    """The two machine-level slots in policy order (draft §6)."""

    if policy.steward_first:
        return ("steward", "machine")
    return ("machine", "steward")


def _selection_reason_for(candidate: _Candidate, policy: SelectionPolicy) -> str:
    if candidate.slot == "project":
        return "PROJECT_MANAGED_HEALTHY"
    if candidate.slot == "session":
        return "SESSION_MANAGED_HEALTHY"
    if candidate.slot == "steward":
        return "STEWARD_REFERENCE_HEALTHY"
    return "MACHINE_MANAGED_HEALTHY" if policy.steward_first else "MACHINE_MANAGED_HEALTHY_BY_POLICY"


def where(
    registry: Any,
    query: WhereQuery,
    *,
    root: Path,
    process_entries: list[str] | None = None,
    machine_entries: list[str] | None = None,
    user_entries: list[str] | None = None,
    policy: SelectionPolicy | None = None,
) -> dict[str, Any]:
    if not query.capability_id:
        raise AirootError("INVALID_INPUT", "where needs a capability id")

    selected_policy = policy or load_selection_policy()
    managed = _managed_candidates(registry, query, Path(root))
    external = _reference_candidates(registry, query)

    selected: _Candidate | None = None
    version_failures: list[_Candidate] = []
    unhealthy: list[_Candidate] = []
    inactive_version_notes: list[_Candidate] = []

    # Slots 1 and 2 keep the frozen project/session precedence: a project binding is a
    # stronger statement of intent than any machine-wide answer.
    for scope_name in ("project", "session"):
        for candidate in managed:
            if candidate.slot != scope_name or not candidate.identity_match:
                continue
            if not candidate.version_ok:
                version_failures.append(candidate)
                continue
            if not candidate.usable:
                unhealthy.append(candidate)
                continue
            selected = candidate
            break
        if selected is not None:
            break

    # Slots 3 and 4: the steward/owned machine-level pair, ordered by policy. A candidate that is
    # not machine-discoverable (Zone W, ADR-0022) is skipped here and only here — the project and
    # session slots above stay open to it, because those require an explicit identity.
    if selected is None:
        by_slot = {"steward": external, "machine": managed}
        for slot_name in _slot_order(selected_policy):
            for candidate in by_slot[slot_name]:
                if candidate.slot != slot_name or not candidate.identity_match:
                    continue
                if not candidate.machine_discoverable:
                    continue
                if not candidate.version_ok:
                    version_failures.append(candidate)
                    if candidate.version_available_but_inactive is not None:
                        inactive_version_notes.append(candidate)
                    continue
                if not candidate.usable:
                    unhealthy.append(candidate)
                    continue
                selected = candidate
                break
            if selected is not None:
                break

    reason = "SUCCESS"
    selection_reason: str | None = None

    # Computed independently of the scan order: whether an owned machine-level payload was
    # *skipped* is what makes a reference selection a degradation rather than a plain win.
    owned_unusable = [
        item
        for item in managed
        if item.slot == "machine" and item.identity_match and not (item.usable and item.version_ok)
    ]

    if selected is not None:
        selection_reason = _selection_reason_for(selected, selected_policy)
        # Normal degradation: an owned payload is declared but unusable, and a healthy
        # reference answered instead. This is no longer a conflict (draft §6).
        if selected.slot == "steward" and owned_unusable:
            reason = DEGRADED_TO_REFERENCE
            selection_reason = DEGRADED_TO_REFERENCE
    elif managed and unhealthy:
        # Nothing else could answer, and an owned binding is declared but broken (S-014).
        reason = "BROKEN"
        selection_reason = "MANAGED_NOT_HEALTHY"
    elif version_failures:
        reason = "VERSION_UNSATISFIED"
        selection_reason = "VERSION_UNSATISFIED"
        if inactive_version_notes:
            selection_reason = "VERSION_AVAILABLE_BUT_INACTIVE"
    elif any(item.slot == "steward" for item in external):
        # A reference exists but cannot be used: drifted/stale/unhealthy, or unprobed
        # against a version constraint. Never silently treated as a match.
        reason = "NOT_FOUND"
        selection_reason = "REFERENCE_NOT_USABLE"
    elif external:
        reason = "NOT_FOUND"
        selection_reason = "UNMANAGED_ONLY"
    else:
        reason = "NOT_FOUND"
        selection_reason = "NOT_FOUND"

    executable = selected.path if selected is not None else None
    effective_now, effective_new_process = (False, False)
    if selected is not None and executable:
        effective_now, effective_new_process = effective_state(
            executable,
            process_entries=process_entries if process_entries is not None else process_path(),
            machine_entries=machine_entries if machine_entries is not None else machine_path(),
            user_entries=user_entries if user_entries is not None else user_path(),
        )
        if reason in {"SUCCESS", DEGRADED_TO_REFERENCE} and effective_now != effective_new_process:
            reason = "CURRENT_PROCESS_ENV_OLD"

    document: dict[str, Any] = {
        "schema_version": 1,
        "found": selected is not None,
        "capability_id": query.capability_id,
        "instance_id": selected.instance_id if selected is not None else None,
        "executable": executable,
        "version": selected.version if selected is not None and selected.version else None,
        "scope": selected.scope if selected is not None else None,
        "zone": selected.zone if selected is not None else None,
        "health": selected.health if selected is not None else None,
        "management": selected.management if selected is not None else None,
        "source": selected.source if selected is not None else None,
        "selection_reason": selection_reason,
        "usable": bool(selected.usable) if selected is not None else False,
        "effective_now": bool(effective_now),
        "effective_new_process": bool(effective_new_process),
        "candidates": [_candidate_document(item) for item in managed + external],
        "evidence": _overall_evidence(query, managed, external, reason, selected_policy),
        "reason_code": reason,
    }
    validate_self("where-response", document)
    return document


def _overall_evidence(
    query: WhereQuery,
    managed: list[_Candidate],
    external: list[_Candidate],
    reason: str,
    policy: SelectionPolicy,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = [
        {
            "kind": "query",
            "detail": f"capability={query.capability_id} version={query.version or '*'} "
            f"scope={query.scope or '*'}",
        },
        {
            "kind": "policy",
            "detail": f"selection precedence={policy.precedence} "
            f"(revision={policy.revision}, source={policy.source})",
        },
    ]
    if query.allow_external_fallback:
        evidence.append(
            {
                "kind": "policy",
                "detail": (
                    "deprecated_flag_ignored: --allow-external-fallback no longer changes the "
                    "answer; a healthy reference is a first-class candidate (draft §6)"
                ),
            }
        )
    if not managed and not external:
        evidence.append({"kind": "registry", "detail": "no satisfying active binding and no external reference"})
    if reason == DEGRADED_TO_REFERENCE:
        evidence.append(
            {
                "kind": "registry",
                "detail": (
                    "an owned binding is declared but not healthy; a healthy reference answered instead "
                    "(normal degradation, not a conflict)"
                ),
            }
        )
        for item in managed:
            if item.slot == "machine" and item.identity_match and not (item.usable and item.version_ok):
                evidence.append(
                    {
                        "kind": "registry",
                        "detail": f"degraded_from={item.binding_key} health={item.health} version={item.version or 'unknown'}",
                        "path": item.path,
                    }
                )
    if reason == "BROKEN":
        evidence.append(
            {"kind": "registry", "detail": "an owned binding is declared but not healthy and no reference can answer"}
        )
    if reason == "VERSION_UNSATISFIED":
        evidence.append({"kind": "registry", "detail": f"no candidate satisfies {query.version}"})
    if reason == "CURRENT_PROCESS_ENV_OLD":
        evidence.append(
            {
                "kind": "effective",
                "detail": "a new process sees the resolved path, the current process does not; restart the shell",
            }
        )
    return evidence
