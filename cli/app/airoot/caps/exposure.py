"""Exposure plans for steward-domain references (draft §13, schema ``reference-plan``).

Two kinds of exposure are kept apart on purpose:

``session``
    ``airoot exec --env`` / ``env activate`` print shell code or inject a block into one
    child process. Nothing is written anywhere; nothing is recorded, because nothing
    changed outside that process.

``persisted``
    A real ``HKCU\\Environment`` write. This module turns it into a **plan document**
    (``reference-plan.schema.json``) so that the write is bound to an approval token,
    recorded with its exact previous value, and exactly reversible.

The one rule that must never bend (draft §13.2): a persisted value points at the
**object inside a registered data root**. Never at ``exposure\\bin``, never at something
AIROOT owns — deleting AIROOT must leave no trace and break nothing.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..canon import plan_hash
from ..clock import Clock, SYSTEM_CLOCK, isoformat, parse_timestamp
from ..exits import AirootError
from ..registry.entities import EnvironmentPersist, environment_persist_from_row
from ..schema_io import validate_document
from .environment import (
    EnvironmentSpec,
    EnvironmentStore,
    PATH_VARIABLES,
    remove_path_value,
    resolve_path_entries,
    resolve_variables,
    spec_from_entry,
    validate_spec,
    validate_target_in_data_root,
    validate_value,
    validate_variable_name,
)

SCOPE_USER = "user"
SCOPE_MACHINE = "machine"
PERSIST_SCOPES = (SCOPE_USER, SCOPE_MACHINE)

DEFAULT_PLAN_TTL_SECONDS = 900

PLAN_OPERATION = "record_reference_exposure"


# --------------------------------------------------------------------------- #
# request
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExposureTarget:
    """One external reference, plus the directories its environment would point at."""

    external_id: str
    capability_id: str
    path: Path
    object_root: Path
    entrypoint_dir: Path
    active_version_dir: Path | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "capability_id": self.capability_id,
            "management": "external_reference",
            "path": str(self.path),
        }


@dataclass(frozen=True)
class ExposureRequest:
    target: ExposureTarget
    spec: EnvironmentSpec
    scope: str = SCOPE_USER
    data_roots: tuple[Path, ...] = ()
    requested_by: str = "cli"
    ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS


def request_from_entry(
    *,
    target: ExposureTarget,
    entry: dict[str, Any] | None,
    scope: str = SCOPE_USER,
    data_roots: tuple[Path, ...] = (),
    requested_by: str = "cli",
) -> ExposureRequest:
    return ExposureRequest(
        target=target,
        spec=spec_from_entry(target.capability_id, entry),
        scope=scope,
        data_roots=data_roots,
        requested_by=requested_by,
    )


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedExposure:
    """The concrete values a plan would write, before any write happens."""

    variables: dict[str, str]
    path_entries: tuple[Path, ...]
    value_kind: str

    def is_empty(self) -> bool:
        return not self.variables and not self.path_entries


def resolve_exposure(request: ExposureRequest) -> ResolvedExposure:
    """Expand the declared environment and prove every value may be persisted."""

    if request.scope not in PERSIST_SCOPES:
        raise AirootError(
            "INVALID_INPUT",
            f"unknown persistence scope: {request.scope}",
            evidence=list(PERSIST_SCOPES),
        )
    spec = request.spec
    validate_spec(spec)

    target = request.target
    variables = resolve_variables(
        spec,
        object_root=target.object_root,
        entrypoint_dir=target.entrypoint_dir,
        active_version_dir=target.active_version_dir,
    )
    path_entries = tuple(
        resolve_path_entries(
            spec, object_root=target.object_root, entrypoint_dir=target.entrypoint_dir
        )
    )

    for name, value in variables.items():
        validate_variable_name(name)
        validate_value(value, spec.value_kind)
        # Only real paths have to live inside a data root; a literal like "1" does not.
        # A template that *did* name a path always starts with the object root, so the
        # check keys off that: a value containing a path separator must be inside a root.
        if "\\" in value or "/" in value:
            _require_inside_data_root(Path(value), request.data_roots, variable=name)

    for entry in path_entries:
        _require_inside_data_root(entry, request.data_roots, variable="PATH")

    return ResolvedExposure(variables=variables, path_entries=path_entries, value_kind=spec.value_kind)


def _require_inside_data_root(path: Path, data_roots: tuple[Path, ...], *, variable: str) -> None:
    if not data_roots:
        raise AirootError(
            "DATA_ROOT_MISSING",
            f"cannot persist {variable}: no data root is registered",
            evidence=["a persisted value must point inside a declared data root (draft §13.2)"],
        )
    try:
        validate_target_in_data_root(path, list(data_roots))
    except AirootError as exc:
        raise AirootError(
            exc.reason_code,
            f"cannot persist {variable}: {exc.message}",
            evidence=list(exc.evidence),
        ) from exc


def build_reference_plan(
    request: ExposureRequest,
    *,
    registry: Any,
    clock: Clock = SYSTEM_CLOCK,
    plan_id: str | None = None,
    resolved: ResolvedExposure | None = None,
) -> dict[str, Any]:
    """Build the canonical plan document for one reference exposure.

    **A request is judged before the environment's limits are reported** (draft §61). Two of the
    three refusals below are about the request itself and one is about what this build may do, and
    the request-level ones have to come first: ``PRIVILEGE_REQUIRED`` (5) says *"elevate and retry"*,
    which no amount of elevation can make true for the injection-type variables §13.5 forbids at
    every scope.

    Measured, not assumed, when §61 audited C-025: on the **CLI** route this order was already
    right, because `request_from_entry` builds the spec through `spec_from_entry` and therefore
    refuses a forbidden variable before this function is ever called. What was wrong was that the
    two owners of the rule disagreed: this function is also a library entry point (`ExposureRequest`
    can be constructed directly, and `resolved=` can be passed in), and *there* the machine gate ran
    first and answered 5 for a request that is invalid at every scope. Both now answer the request.
    """

    exposure = resolved or resolve_exposure(request)
    if exposure.is_empty():
        raise AirootError(
            "INVALID_INPUT",
            f"capability {request.target.capability_id} declares no environment to persist",
            evidence=["the whitelist entry has neither variables nor path_prepend"],
        )

    if request.scope == SCOPE_MACHINE:
        raise AirootError(
            "PRIVILEGE_REQUIRED",
            "machine-scope environment variables need the protected broker",
            evidence=[
                "HKLM writes require elevation; the broker is P2 (draft §13.3)",
                "use --scope user, or persist nothing and use session activation",
            ],
        )

    target = request.target
    created_at = clock.timestamp()
    document: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan_id or f"plan/reference/{request.target.capability_id}/{uuid.uuid4().hex[:12]}",
        "operation": PLAN_OPERATION,
        "root_instance_id": str(registry.root_instance_id),
        "machine_id": str(registry.machine_id),
        "policy_revision": int(registry.policy_revision),
        "created_at": created_at,
        "expires_at": isoformat(parse_timestamp(created_at) + timedelta(seconds=request.ttl_seconds)),
        "requested_by": request.requested_by,
        "target": target.to_document(),
        "operations": _operations(request, exposure, created_at),
        "side_effects": _side_effects(exposure),
        "exposure": {
            "scope": request.scope,
            "value_kind": exposure.value_kind,
            "variables": dict(exposure.variables),
            "path_prepend": [str(entry) for entry in exposure.path_entries],
        },
        "canonicalization": "jcs-rfc8785-compatible",
        "metadata": {
            "value_kind": exposure.value_kind,
            "path_entries": [str(entry) for entry in exposure.path_entries],
        },
    }
    document["plan_hash"] = plan_hash(document)
    validate_document("reference-plan", document, reason_code="INVALID_INPUT")
    return document


def _side_effects(exposure: ResolvedExposure) -> list[str]:
    """Only values the frozen ``sideEffect`` enum can express (v0.3 §5.3)."""

    effects = ["mutates_system", "writes_registry"]
    if exposure.path_entries:
        effects.append("writes_path")
    return effects


def _operations(
    request: ExposureRequest, exposure: ResolvedExposure, created_at: str
) -> list[dict[str, Any]]:
    names = sorted(exposure.variables)
    if exposure.path_entries:
        names.append("PATH")
    return [
        {
            "step_id": f"step/expose/{index:02d}",
            "kind": "expose",
            "description": f"persist {name} for {request.target.capability_id} (scope={request.scope})",
            "target_scope": "machine" if request.scope == SCOPE_MACHINE else "user",
            "reversible": True,
        }
        for index, name in enumerate(names, start=1)
    ]


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #


@dataclass
class ExposureResult:
    plan_id: str
    plan_hash: str
    scope: str
    variable: str | None = None
    variables: list[str] = field(default_factory=list)
    path_entries: list[str] = field(default_factory=list)
    approval_id: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "apply_reference_exposure",
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "scope": self.scope,
            "variables": sorted(self.variables),
            "path_entries": list(self.path_entries),
            "approval_id": self.approval_id,
            "reason_code": "SUCCESS",
        }


def _store_for_scope(scope: str, store: EnvironmentStore | None) -> EnvironmentStore:
    if store is not None:
        return store
    from .environment import WindowsEnvironmentStore

    if scope != SCOPE_USER:
        raise AirootError("PRIVILEGE_REQUIRED", "only the user scope has an in-process store")
    return WindowsEnvironmentStore()


def apply_reference_plan(
    plan: dict[str, Any],
    *,
    registry: Any,
    store: EnvironmentStore | None = None,
    approval_id: str | None = None,
    approval_mode: str | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> ExposureResult:
    """Write the plan's values, recording every previous value first.

    Order matters: the old value is read and journaled *before* the write, so a crash
    between the two leaves a record of what to restore rather than a silent change.

    ``approval_mode`` is optional and travels beside ``approval_id`` rather than replacing it: the
    id says *which* approval, the mode says *what kind* (draft §65). Callers that only hold the id
    leave it ``None``, and the event then records no mode — which is honest, because that caller
    did not have one to record.
    """

    if plan.get("operation") != PLAN_OPERATION:
        raise AirootError(
            "INVALID_INPUT",
            f"not a reference exposure plan: {plan.get('operation')!r}",
            evidence=[PLAN_OPERATION],
        )
    if plan.get("plan_hash") != plan_hash(plan):
        raise AirootError(
            "INVALID_INPUT",
            "plan hash does not match the plan contents",
            evidence=[f"declared={plan.get('plan_hash')}", f"computed={plan_hash(plan)}"],
        )
    if str(plan["root_instance_id"]) != str(registry.root_instance_id) or str(plan["machine_id"]) != str(
        registry.machine_id
    ):
        raise AirootError("INVALID_INPUT", "the plan was built for a different root or machine")

    scope = str(plan["exposure"]["scope"])
    target = plan["target"]
    resolved = _store_for_scope(scope, store)

    written: list[str] = []
    path_entries = [Path(item) for item in plan["exposure"].get("path_prepend", [])]
    value_kind = str(plan["exposure"].get("value_kind", "REG_EXPAND_SZ"))

    records: list[EnvironmentPersist] = []
    for name, value in sorted(plan["exposure"]["variables"].items()):
        previous = resolved.read(name)
        records.append(
            EnvironmentPersist(
                capability_id=target["capability_id"],
                scope=scope,
                variable=name,
                value=value,
                value_kind=value_kind,
                written_at=clock.timestamp(),
                external_id=target["external_id"],
                old_value=previous.value if previous else None,
                old_kind=previous.kind if previous else None,
                plan_id=str(plan["plan_id"]),
                plan_hash=str(plan["plan_hash"]),
                approval_id=approval_id,
            )
        )
        resolved.write(name, value, value_kind)
        written.append(name)

    if path_entries:
        from .environment import merge_path_value

        name = _path_variable_name(resolved)
        previous = resolved.read(name)
        # Preserve the existing kind: rewriting REG_EXPAND_SZ as REG_SZ would freeze
        # %SystemRoot% into a literal path.
        kind = previous.kind if previous else value_kind
        merged = merge_path_value(previous.value if previous else None, path_entries)
        validate_value(merged, kind)
        records.append(
            EnvironmentPersist(
                capability_id=target["capability_id"],
                scope=scope,
                variable=name,
                value=merged,
                value_kind=kind,
                written_at=clock.timestamp(),
                external_id=target["external_id"],
                old_value=previous.value if previous else None,
                old_kind=previous.kind if previous else None,
                plan_id=str(plan["plan_id"]),
                plan_hash=str(plan["plan_hash"]),
                approval_id=approval_id,
            )
        )
        resolved.write(name, merged, kind)
        written.append(name)

    with registry.write(expected_generation=registry.generation) as connection:
        for record in records:
            registry.record_environment_persist(connection, record)
        registry.append_event(
            connection,
            state="EXPOSED",
            detail=(
                f"persisted {', '.join(sorted(written))} for {target['capability_id']} "
                f"(scope={scope}, plan={plan['plan_id']})"
            ),
            approval_id=approval_id,
            plan_hash=str(plan["plan_hash"]),
            reason_code=None,
            outcome="ok",
            approval_mode=approval_mode,
        )

    return ExposureResult(
        plan_id=str(plan["plan_id"]),
        plan_hash=str(plan["plan_hash"]),
        scope=scope,
        variables=written,
        path_entries=[str(entry) for entry in path_entries],
        approval_id=approval_id,
    )


def _path_variable_name(store: EnvironmentStore) -> str:
    """Windows uses ``Path``; a machine may still carry the legacy ``PATH`` spelling."""

    for name in PATH_VARIABLES:
        if store.read(name) is not None:
            return name
    return "Path"


# --------------------------------------------------------------------------- #
# forget
# --------------------------------------------------------------------------- #


@dataclass
class ForgetResult:
    scope: str
    capability_id: str
    variable: str | None = None
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "forget_reference_persist",
            "capability_id": self.capability_id,
            "scope": self.scope,
            "restored": sorted(self.restored),
            "removed": sorted(self.removed),
            "drifted": sorted(self.drifted),
            "dry_run": self.dry_run,
            "reason_code": "SUCCESS",
        }


@dataclass
class ForgetAllResult:
    """What `env forget --all` restored, across every capability at once (draft §63)."""

    scopes: list[str] = field(default_factory=list)
    capability_ids: list[str] = field(default_factory=list)
    records: int = 0
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    #: Variables more than one capability had written. Reported rather than hidden: for a plain
    #: variable it means the restore target came from the chain start, and for PATH it means the
    #: entries removed were the union of several capabilities' additions.
    shared_variables: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "forget_all_persist",
            "scopes": sorted(self.scopes),
            "capability_ids": sorted(set(self.capability_ids)),
            "records": self.records,
            "restored": sorted(self.restored),
            "removed": sorted(self.removed),
            "drifted": sorted(self.drifted),
            "shared_variables": sorted(self.shared_variables),
            "dry_run": self.dry_run,
            "reason_code": "SUCCESS",
        }


def added_path_entries(record: EnvironmentPersist) -> list[Path]:
    """The entries AIROOT itself introduced: present now, absent from the old value.

    Recording the merged value is what makes the write auditable, but it cannot be used
    to un-merge: it contains everybody's entries. The difference against the recorded
    previous value is exactly what this plan added, in any order.
    """

    def normalize(part: str) -> str:
        return os.path.normcase(os.path.normpath(part))

    previous = {normalize(part) for part in (record.old_value or "").split(";") if part}
    return [
        Path(part)
        for part in record.value.split(";")
        if part and normalize(part) not in previous
    ]


def forget_all_persist(
    *,
    registry: Any,
    store: EnvironmentStore | None = None,
    clock: Clock = SYSTEM_CLOCK,
    dry_run: bool = False,
) -> "ForgetAllResult":
    """Undo **everything** AIROOT ever persisted (draft §13.4's ``env forget --all``).

    This is the other half of the steward model's promise. The per-capability form answers "stop
    managing java"; this one answers "**the steward is leaving, put my environment back**" — and
    §13.4 says why it has to exist: without it, "deleting AIROOT leaves no trace" cannot be done,
    because what is left behind is a set of variables whose origin nobody can tell any more. The
    table was even created with this command in mind (`ddl.sql`: *"env forget --all exact: the
    complete old value is recorded before the write"*) — it just never got written (draft §63).

    Three things make doing it in one pass different from looping the per-capability form:

    * **PATH needs no order.** ``remove_path_value`` removes the entries *AIROOT added*, computed
      as the difference between what it wrote and what was there before, so undoing two capabilities
      in either order leaves the same survivor set. The union of every record's added entries is
      what has to go;
    * **a plain variable needs the *chain start*.** If two capabilities wrote the same variable, the
      earlier record's ``old_value`` is the later record's ``value`` — so restoring them in the wrong
      order puts the earlier write *back*. The value to restore is the ``old_value`` that is not any
      record's ``value``, which is order-free and needs no timestamps;
    * **drift is judged against every record**, not one: "the machine no longer holds what AIROOT
      wrote" is only true when the live value matches none of them.

    Nothing outside ``environment_persist`` is consulted, so a variable AIROOT never wrote cannot be
    touched even by accident — that is the clause the scenario actually cares about.
    """

    rows = registry.environment_persist_records(active_only=True)
    result = ForgetAllResult(dry_run=dry_run)
    if not rows:
        # A legitimate answer, not a lookup failure: "AIROOT has nothing recorded here" is exactly
        # what someone checking before deleting AIROOT wants to hear. The per-capability form still
        # refuses (its question names a capability that should have records), and the difference is
        # deliberate rather than an oversight.
        return result

    groups: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        record = environment_persist_from_row(row)
        groups.setdefault((str(record.scope), str(record.variable)), []).append(record)
        result.scopes.append(str(record.scope))
        result.capability_ids.append(str(record.capability_id))
    result.records = len(rows)

    # (scope, variable, action, value, kind): the complete decision, computed before any write.
    plan: list[tuple[str, str, str, str | None, str | None]] = []
    for (scope, variable), group in sorted(groups.items()):
        resolved = _store_for_scope(scope, store)
        current = resolved.read(variable)
        if len(group) > 1:
            result.shared_variables.append(variable)

        if variable.lower() in {name.lower() for name in PATH_VARIABLES}:
            added: dict[str, Any] = {}
            for record in group:
                for entry in added_path_entries(record):
                    added[os.path.normcase(os.path.normpath(str(entry)))] = entry
            if current is None:
                plan.append((scope, variable, "delete", None, None))
                continue
            cleaned = remove_path_value(current.value, list(added.values()))
            result.restored.append(variable)
            if cleaned:
                plan.append((scope, variable, "write", cleaned, current.kind))
            else:
                plan.append((scope, variable, "delete", None, None))
            continue

        values = {record.value for record in group}
        starts = [record for record in group if record.old_value not in values]
        # A single chain start is the answer. Several mean the variable was reset between two AIROOT
        # writes; the earliest wins, and the tiebreak is written down so the result cannot depend on
        # row order. (With the shipped whitelist only PATH can be written by two capabilities —
        # JAVA_HOME is declared by exactly one — so this is the honest handling of a case the policy
        # currently makes unreachable, not a guess about a common one.)
        target = min(
            starts or group,
            key=lambda record: (str(record.written_at), str(record.old_value), str(record.capability_id)),
        )
        if current is not None and current.value not in values:
            result.drifted.append(variable)
        if target.old_value is None:
            result.removed.append(variable)
            plan.append((scope, variable, "delete", None, None))
        else:
            result.restored.append(variable)
            plan.append(
                (scope, variable, "write", target.old_value, target.old_kind or target.value_kind)
            )

    if dry_run:
        return result

    for scope, variable, action, value, kind in plan:
        resolved = _store_for_scope(scope, store)
        if action == "delete":
            resolved.delete(variable)
        else:
            assert value is not None and kind is not None
            resolved.write(variable, value, kind)

    with registry.write(expected_generation=registry.generation) as connection:
        for row in rows:
            registry.mark_environment_persist_forgotten(
                connection, str(row["capability_id"]), str(row["scope"]), str(row["variable"])
            )
        registry.append_event(
            connection,
            state="FORGOTTEN",
            detail=(
                f"restored every persisted environment AIROOT had written "
                f"({len(rows)} record(s) across {len(set(result.capability_ids))} capability(ies))"
            ),
            reason_code=None,
            outcome="ok",
        )
    return result


def forget_reference_persist(
    *,
    registry: Any,
    capability_id: str,
    variable: str | None = None,
    store: EnvironmentStore | None = None,
    clock: Clock = SYSTEM_CLOCK,
    dry_run: bool = False,
) -> ForgetResult:
    """Undo what AIROOT persisted for one capability.

    Non-PATH variables get their recorded previous value back (or are removed when there
    was none). PATH entries are removed surgically instead, so a path another tool added
    after us survives. When the live value no longer matches what AIROOT wrote, the
    variable is reported as ``drifted`` — forgetting never silently overwrites somebody
    else's later edit.

    ``dry_run`` performs only the reads, so the whole outcome can be inspected (and
    scripted against) before anything on the machine changes.
    """

    rows = registry.environment_persist_records(active_only=True)
    selected = [
        environment_persist_from_row(row)
        for row in rows
        if row["capability_id"] == capability_id and (variable is None or row["variable"] == variable)
    ]
    if not selected:
        raise AirootError(
            "ENVIRONMENT_PERSIST_NOT_FOUND",
            f"no persisted environment is recorded for {capability_id}"
            + (f" variable {variable}" if variable else ""),
        )

    scope = selected[0].scope
    resolved = _store_for_scope(scope, store)
    result = ForgetResult(scope=scope, capability_id=capability_id, variable=variable, dry_run=dry_run)
    # (variable, action, value, kind): the complete decision, computed before any write.
    plan: list[tuple[str, str, str | None, str | None]] = []

    for record in selected:
        current = resolved.read(record.variable)
        if record.variable.lower() in {name.lower() for name in PATH_VARIABLES}:
            from .environment import remove_path_value

            result.restored.append(record.variable)
            if current is None:
                plan.append((record.variable, "delete", None, None))
                continue
            cleaned = remove_path_value(current.value, added_path_entries(record))
            if cleaned:
                plan.append((record.variable, "write", cleaned, current.kind))
            else:
                plan.append((record.variable, "delete", None, None))
        else:
            if current is not None and current.value != record.value:
                result.drifted.append(record.variable)
            if record.old_value is None:
                result.removed.append(record.variable)
                plan.append((record.variable, "delete", None, None))
            else:
                result.restored.append(record.variable)
                plan.append(
                    (record.variable, "write", record.old_value, record.old_kind or record.value_kind)
                )

    if dry_run:
        return result

    for name, action, value, kind in plan:
        if action == "delete":
            resolved.delete(name)
        else:
            assert value is not None and kind is not None
            resolved.write(name, value, kind)

    with registry.write(expected_generation=registry.generation) as connection:
        for record in selected:
            registry.mark_environment_persist_forgotten(
                connection, capability_id, scope, record.variable
            )
        registry.append_event(
            connection,
            state="FORGOTTEN",
            detail=f"restored the environment of {capability_id} (scope={scope})",
            reason_code=None,
            outcome="ok",
        )
    return result


__all__ = [
    "DEFAULT_PLAN_TTL_SECONDS",
    "ExposureRequest",
    "ExposureResult",
    "ExposureTarget",
    "ForgetAllResult",
    "ForgetResult",
    "PLAN_OPERATION",
    "PERSIST_SCOPES",
    "ResolvedExposure",
    "SCOPE_MACHINE",
    "SCOPE_USER",
    "apply_reference_plan",
    "build_reference_plan",
    "added_path_entries",
    "forget_all_persist",
    "forget_reference_persist",
    "request_from_entry",
    "resolve_exposure",
]
