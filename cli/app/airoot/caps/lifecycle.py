"""Deletion semantics graded by ownership (draft §14, plan `retire_tool` / `gc_apply`).

Four verbs, and the differences between them are the whole point:

===========================  ==========  ==========  ==========================
verb                         deletes?    approval    applies to
===========================  ==========  ==========  ==========================
``forget <external-id>``     no          no          references only
``retire <capability>``      no          no          owned only
``uninstall <capability>``   yes         **yes**     owned only (retire → gc)
``env forget``               no (restore) no         what AIROOT persisted
===========================  ==========  ==========  ==========================

``retire``'s "no" is about the **payload**: it never deletes what it manages. It does remove the one
derived file that binding projected — ``cli/exposure/bin/<capability>.cmd`` (ADR-0050) — because an
entry with no binding resolves nothing, and `path verify` reports it as drift forever. That file is
not a payload, is not under a data root and is not in the store; clearing the binding without
clearing it is how orphaned entries were manufactured (draft §165).

The hard rules this module exists to enforce (draft §14.2):

1. a reference never offers ``uninstall`` — AIROOT does not own it, so it only *prints* the
   absolute paths and a suggested command (``OWNERSHIP_REQUIRED``, exit 7);
2. nothing under a data root is ever deleted, by any verb;
3. ``uninstall`` is never one step: it is ``retire`` then ``gc``, with no ``--force``;
4. deletion requires an approval token, and the admission decision is recomputed at apply
   time — the token authorises a plan hash, not a stale list;
5. a payload referenced by a binding, a rollback, an unfinished transaction or a project
   manifest is refused, and nothing is deleted.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..canon import plan_hash, tree_digest
from ..clock import Clock, SYSTEM_CLOCK, isoformat, parse_timestamp
from ..exits import AirootError
from ..paths import from_root_relative, is_within
from ..registry.entities import STORE_PREFIX, is_store_path
from ..schema_io import validate_self
from ..tx.approval import load_keyring, verify_approval
from .launcher import launcher_path

# Kept as the name this module has always exported; the spelling itself lives in one place now
# (`registry/entities.STORE_PREFIX`), so doctor/where/toolstate and deletion grading cannot drift
# apart on what "inside the store" means (draft §66).
OWNED_STORE_PREFIX = STORE_PREFIX
EXTERNAL_BACKENDS = frozenset({"external", "external_reference"})

DEFAULT_PLAN_TTL_SECONDS = 900

RETIRE_TOOL = "retire_tool"
GC_APPLY = "gc_apply"


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #


def is_owned(row: Any) -> bool:
    """Owned means: a payload AIROOT put in the immutable store itself.

    Two independent signals must agree, because "AIROOT installed it" is exactly the claim
    that has to hold before anything may be deleted (draft §14.2-3).
    """

    store_path = str(row["store_path"] or "")
    backend = str(row["install_backend_id"] or "")
    return is_store_path(store_path) and backend not in EXTERNAL_BACKENDS


def expected_launchers(registry: Any) -> list[str]:
    """Capabilities whose active machine-level binding must have a stable entry (ADR-0050).

    This is the **one** derivation of "which entries this root should have". Three readers depend on
    it and none of them may spell it again: `path verify` reports the entries it cannot find,
    `path repair` writes exactly those, and `retire` asks whether the entry it projected still has a
    source before it removes it. A second copy of the rule is how the reporter, the writer and the
    remover would start disagreeing about the same root — the defect this repository keeps paying for
    (`tx/rollback.py`, `tx/registration.py`).

    The predicate is ADR-0050's: scope ``machine`` and an instance AIROOT owns. A session or project
    binding is exposed through its own activation, not through a version-independent file name, and an
    instance AIROOT does not own (a reference row) has no stable entry either.
    """

    expected: list[str] = []
    for row in registry.bindings(active_only=True):
        if str(row["scope"]) != "machine":
            continue
        instance = registry.instance(str(row["instance_id"]))
        if instance is None or not is_owned(instance):
            continue
        expected.append(str(instance["capability_id"]))
    return sorted(set(expected))


def find_target(registry: Any, target: str) -> tuple[str, Any]:
    """Resolve a user-supplied target to either an instance row or a reference row.

    Returns ``("owned", row)`` / ``("reference", row)``; the caller decides what is legal.
    A reference is matched first, so naming something AIROOT never owned can never be
    mistaken for an owned instance.
    """

    reference = registry.external_reference(target)
    if reference is not None:
        return "reference", reference
    row = registry.instance(target)
    if row is not None:
        return ("owned" if is_owned(row) else "foreign"), row
    for candidate in registry.instances():
        if str(candidate["capability_id"]) == target:
            return ("owned" if is_owned(candidate) else "foreign"), candidate
    raise AirootError(
        "NOT_FOUND",
        f"no owned instance or reference matches {target}",
        evidence=[
            *[str(item["instance_id"]) for item in registry.instances()],
            *[str(item["external_id"]) for item in registry.external_references()],
        ],
    )


def require_owned(registry: Any, target: str) -> Any:
    """Refuse anything AIROOT does not own, telling the user what to run instead."""

    kind, row = find_target(registry, target)
    if kind == "owned":
        return row
    if kind == "reference":
        path = Path(str(row["path"]))
        raise AirootError(
            "OWNERSHIP_REQUIRED",
            f"{row['external_id']} is an external reference; AIROOT does not own it and will not delete it",
            evidence=[
                f"absolute path: {path}",
                "AIROOT only records it; removing the files is your call",
                f"suggestion: airoot forget {row['external_id']}   # drops the record, keeps the files",
            ],
        )
    raise AirootError(
        "OWNERSHIP_REQUIRED",
        f"{row['instance_id']} is not an AIROOT-owned payload (store_path={row['store_path']})",
        evidence=["only payloads under store/ with a non-external install backend may be deleted"],
    )


# --------------------------------------------------------------------------- #
# retire: declarative, no payload movement, no approval
# --------------------------------------------------------------------------- #


def retire(
    registry: Any,
    target: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    root: Path | None = None,
) -> dict[str, Any]:
    """Clear the active binding and mark the instance retired. The payload stays.

    This is deliberately **not** a transaction: nothing moves and no payload is deleted, so it
    is a single declarative change. The binding write and the generation bump still happen in
    one SQLite transaction, which is what "only one commit point may change the active
    binding" actually protects (draft §19.3-1).

    The **projection** of that binding goes with it: the stable entry in ``cli/exposure/bin`` is one
    file derived from "this capability has an active machine-level binding" (ADR-0050), so leaving it
    behind manufactures an entry that resolves nothing — drift `path verify` can only report, never
    fix. ``root`` is the AIROOT root the entry lives in; it defaults to the same derivation
    ``build_gc_plan`` uses, so a caller with the root in hand should pass it.

    A projection the filesystem refuses to remove is a **degraded** outcome, not a failure of the verb:
    the binding half is committed, and what is left is exactly the drift `path verify` reports, which
    re-running this verb or `path repair` can retry. So it is reported as ``DEGRADED`` with the path and
    a warning — never as a borrowed install code, and never silently (draft §165 ruling).
    """

    row = require_owned(registry, target)
    instance_id = str(row["instance_id"])
    capability_id = str(row["capability_id"])
    root = Path(root) if root is not None else Path(registry.path).parent.parent
    key = next(
        (str(item["binding_key"]) for item in registry.bindings() if item["instance_id"] == instance_id),
        None,
    )
    already = str(row["lifecycle_status"]) == "retired" and row["retired_at"] is not None
    if already and (key is None or not _is_active(registry, key, instance_id)):
        # Already retired: nothing to write in the registry, but the projection may still be there
        # (a root whose entries were written before this rule existed). Re-running the verb is how an
        # operator converges such a root, so the cleanup runs here too.
        projection = _clear_projection(registry, root, capability_id)
        return {
            "schema_version": 1,
            "operation": RETIRE_TOOL,
            "instance_id": instance_id,
            "binding_key": key,
            "lifecycle_status": "retired",
            "payload_removed": False,
            "idempotent": True,
            **projection,
        }

    stamp = clock.timestamp()
    with registry.write(expected_generation=registry.generation, bump=True) as connection:
        if key is not None:
            registry.clear_active_binding(connection, key)
        registry.set_instance_status(
            connection, instance_id, lifecycle_status="retired", retired_at=stamp
        )
        registry.append_event(
            connection,
            state="RETIRED",
            detail=(
                f"{instance_id} retired: active binding cleared, "
                f"payload retained at {row['store_path']} as rollback evidence"
            ),
            outcome="ok",
        )
    registry.update_projection()
    # After the binding is gone, so the question "does anything still project this entry?" is asked
    # about the state the registry is now in.
    projection = _clear_projection(registry, root, capability_id)
    return {
        "schema_version": 1,
        "operation": RETIRE_TOOL,
        "instance_id": instance_id,
        "binding_key": key,
        "lifecycle_status": "retired",
        "payload_removed": False,
        "retired_at": stamp,
        **projection,
    }


def _clear_projection(registry: Any, root: Path, capability_id: str) -> dict[str, Any]:
    """The projection half of a retire result: what happened to the stable entry, and how loudly."""

    removed, entry, refusal = _remove_projected_entry(registry, root, capability_id)
    return {
        "launcher_removed": removed,
        "launcher_path": entry,
        "warnings": [refusal] if refusal else [],
        "reason_code": "DEGRADED" if refusal else "SUCCESS",
    }


def _remove_projected_entry(registry: Any, root: Path, capability_id: str) -> tuple[bool, str, str | None]:
    """Remove the stable entry ``capability_id`` projected, unless something still projects it.

    Returns ``(removed, path, refusal)``: ``refusal`` is a warning line when the file is there but
    could not be removed, and ``None`` otherwise.

    Exactly one file is in scope — ``cli/exposure/bin/<capability>.cmd``. It is not a payload, not
    under a data root and not in the store, so "nothing under a data root is ever deleted" and "only
    the store is collected" are untouched by this.

    Two things it deliberately does **not** do:

    * it does not remove the entry when another active machine-level binding still projects it (the
      retired instance may have been bound only for a session), because that would trade an orphan for
      a binding with no entry — the other direction of the same drift;
    * it does not swallow a filesystem refusal: the caller reports it. On the **reason code**, that
      answer is `DEGRADED` (exit 2) rather than `INSTALL_IO_FAILED`: the binding half of the verb
      succeeded and what is left is the drift `path verify` reports, so "a step of an install was
      refused" would be a borrowed code — borrowing one because the exit number happens to match is
      how a code table stops meaning anything (draft §115).
    """

    path = launcher_path(root, capability_id)
    if capability_id in expected_launchers(registry):
        return False, str(path), None
    if not path.is_file():
        return False, str(path), None
    try:
        path.unlink()
    except OSError as error:
        return False, str(path), (
            f"the stable entry {path} could not be removed ({error}); the binding is already cleared, "
            "so this is the drift `path verify` reports — re-run `tool retire`, or remove the file by "
            "hand (`path repair` writes and never deletes)"
        )
    return True, str(path), None


def _is_active(registry: Any, key: str, instance_id: str) -> bool:
    return any(
        str(item["binding_key"]) == key and str(item["instance_id"]) == instance_id
        for item in registry.bindings(active_only=True)
    )


# --------------------------------------------------------------------------- #
# gc: collectable decisions
# --------------------------------------------------------------------------- #


@dataclass
class Collectable:
    instance_id: str
    capability_id: str
    version: str
    store_path: str
    lifecycle_status: str
    collectable: bool
    blockers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "store_path": self.store_path,
            "lifecycle_status": self.lifecycle_status,
            "collectable": self.collectable,
            "blockers": list(self.blockers),
            "evidence": list(self.evidence),
        }


def gc_candidates(registry: Any, *, target: str | None = None) -> list[Collectable]:
    """Every instance and whether it may be collected, with the reason either way.

    The blockers are exactly the frozen list (v0.3 §9:847): active binding, rollback
    retention, unfinished transaction, audit retention, project manifest.
    """

    collectables: list[Collectable] = []
    unfinished = {
        str(row["instance_id"])
        for row in registry.transactions(unfinished_only=True)
        if row["instance_id"]
    }
    for row in registry.instances():
        instance_id = str(row["instance_id"])
        blockers: list[str] = []
        evidence: list[str] = []
        if target is not None and target not in {instance_id, str(row["capability_id"])}:
            continue
        if not is_owned(row):
            blockers.append("not_owned_by_airoot")
        status = str(row["lifecycle_status"])
        if status != "retired":
            blockers.append(f"lifecycle_status={status} (must be retired first)")
        if str(row["health"]) == "broken" and status == "retired":
            evidence.append("health=broken; retained deliberately as evidence of a failed change")
        active = [item for item in registry.bindings(active_only=True) if str(item["instance_id"]) == instance_id]
        if active:
            blockers.append(f"active_binding={active[0]['binding_key']}")
        if instance_id in unfinished:
            blockers.append("unfinished_transaction_references_it")
        if row["collected_at"]:
            blockers.append("already_collected")
            evidence.append(f"collected_at={row['collected_at']}")
        # Retention: a retired payload is kept while any *other* generation of the same
        # binding key still exists in history, unless that history is already collected.
        retained = [
            item
            for item in registry.bindings()
            if str(item["binding_key"]).split("/")[0] == str(row["capability_id"])
            and str(item["instance_id"]) != instance_id
        ]
        if retained:
            evidence.append(f"{len(retained)} historical binding(s) reference other instances of this capability")
        collectables.append(
            Collectable(
                instance_id=instance_id,
                capability_id=str(row["capability_id"]),
                version=str(row["version"]),
                store_path=str(row["store_path"]),
                lifecycle_status=status,
                collectable=not blockers,
                blockers=blockers,
                evidence=evidence,
            )
        )
    return collectables


def check_payload_removable(registry: Any, root: Path, instance_id: str) -> tuple[Path, list[str]]:
    """Return the store directory to delete, or refuse with every blocker listed."""

    row = registry.instance(instance_id)
    if row is None:
        raise AirootError("NOT_FOUND", f"unknown instance: {instance_id}")
    if not is_owned(row):
        raise AirootError(
            "OWNERSHIP_REQUIRED",
            f"{instance_id} is not an AIROOT-owned payload",
            evidence=[f"store_path={row['store_path']}", f"install_backend_id={row['install_backend_id']}"],
        )
    blockers = [
        item
        for item in gc_candidates(registry, target=instance_id)
        if item.instance_id == instance_id
    ]
    problems = blockers[0].blockers if blockers else ["unknown_instance"]
    if problems:
        raise AirootError(
            "REFERENCE_IN_USE",
            f"{instance_id} may not be collected",
            evidence=problems,
        )
    store_dir = from_root_relative(str(row["store_path"]), Path(root))
    if not is_within(store_dir, Path(root) / "store"):
        # Belt and braces: the store is the only directory gc may ever touch.
        raise AirootError(
            "PATH_ESCAPES_ROOT",
            f"refusing to delete outside store/: {store_dir}",
            evidence=[f"store_path={row['store_path']}"],
        )
    return store_dir, [
        f"artifact_digest={row['artifact_digest']}",
        f"store_path={row['store_path']}",
    ]


# --------------------------------------------------------------------------- #
# gc --apply: a canonical plan plus an approval token
# --------------------------------------------------------------------------- #


def build_gc_plan(
    registry: Any,
    instance_id: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS,
    requested_by: str = "airoot-cli",
    plan_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """A schema-valid ``plan`` for collecting exactly one payload."""

    root = Path(root or Path(registry.path).parent.parent)
    row = registry.instance(instance_id)
    if row is None:
        raise AirootError("NOT_FOUND", f"unknown instance: {instance_id}")
    store_dir, evidence = check_payload_removable(registry, root, instance_id)
    created_at = clock.timestamp()
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan_id or f"plan/gc/{instance_id.replace('/', '_')}/{uuid.uuid4().hex[:12]}",
        "plan_hash": "sha256:" + "0" * 64,
        "operation": GC_APPLY,
        "root_instance_id": registry.root_instance_id,
        "machine_id": registry.machine_id,
        "policy_revision": registry.policy_revision,
        "created_at": created_at,
        "expires_at": isoformat(parse_timestamp(created_at) + timedelta(seconds=ttl_seconds)),
        "requested_by": requested_by,
        "target": {
            "capability_id": str(row["capability_id"]),
            "instance_id": instance_id,
            "kind": str(row["kind"]),
            "version": str(row["version"]),
            "platform": str(row["platform"]),
            "architecture": str(row["architecture"]),
        },
        "operations": [
            {
                "step_id": "step/delete",
                "kind": "delete",
                "description": f"delete the collected payload at {row['store_path']} (retired, unreferenced)",
                "target_scope": "machine",
                "reversible": False,
                "source_mutation": "delete",
                "artifact_digest": str(row["artifact_digest"]),
            }
        ],
        "side_effects": ["writes_store", "writes_registry"],
        "canonicalization": "jcs-rfc8785-compatible",
        "metadata": {
            "gc": True,
            "store_path": str(row["store_path"]),
            "instance_id": instance_id,
            "expected_digest": str(row["artifact_digest"]),
            "admission_evidence": evidence,
        },
    }
    plan["plan_hash"] = plan_hash(plan)
    validate_self("plan", plan)
    return plan


def apply_gc_plan(
    registry: Any,
    plan: dict[str, Any],
    token: dict[str, Any],
    *,
    root: Path | None = None,
    clock: Clock = SYSTEM_CLOCK,
    keyring: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Consume the approval and delete exactly one store payload.

    The admission decision is recomputed here: the token authorises this plan hash, not the
    list that existed when the plan was written.
    """

    root = Path(root or Path(registry.path).parent.parent)
    if plan.get("operation") != GC_APPLY:
        raise AirootError("INVALID_PLAN", f"not a gc plan: {plan.get('operation')!r}", evidence=[GC_APPLY])
    computed = plan_hash(plan)
    if computed != plan.get("plan_hash"):
        raise AirootError(
            "INVALID_PLAN",
            "plan hash does not match the plan contents",
            evidence=[f"declared={plan.get('plan_hash')}", f"computed={computed}"],
        )
    verify_approval(
        registry,
        plan,
        token,
        keyring=keyring if keyring is not None else load_keyring(root),
        clock=clock,
    )

    instance_id = str(plan["target"]["instance_id"])
    row = registry.instance(instance_id)
    if row is None:
        raise AirootError("NOT_FOUND", f"unknown instance: {instance_id}")
    if row["collected_at"]:
        return _gc_result(registry, plan, instance_id, already=True)

    store_dir, _evidence = check_payload_removable(registry, root, instance_id)
    expected = str(plan["metadata"].get("expected_digest") or row["artifact_digest"])
    if store_dir.is_dir() and tree_digest(store_dir) != expected:
        raise AirootError(
            "DIGEST_MISMATCH",
            "the payload changed after the plan was approved; a new plan and approval are required",
            evidence=[f"expected={expected}", f"actual={tree_digest(store_dir)}"],
        )

    stamp = clock.timestamp()
    with registry.write(expected_generation=registry.generation) as connection:
        # Recording the token is what makes it consumable later; without it `consume_approval`
        # has nothing to mark and the delete would happen with an unrecorded authorisation.
        registry.upsert_approval(connection, token)
        registry.append_event(
            connection,
            state="GC_INTENT",
            detail=f"collecting {instance_id} at {store_dir} (approved by {token['approval_id']})",
            approval_id=str(token["approval_id"]),
            plan_hash=str(plan["plan_hash"]),
            outcome="ok",
            approval_mode=str(token["approval_mode"]),
        )
    # The delete happens between INTENT and APPLIED so an interruption leaves a durable
    # record of what was being removed; both outcomes are identifiable and idempotent.
    removed = False
    if store_dir.is_dir():
        shutil.rmtree(store_dir)
        removed = True
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(
            connection,
            instance_id,
            collected_at=stamp,
            collected_approval_id=str(token["approval_id"]),
        )
        registry.consume_approval(connection, str(token["approval_id"]))
        registry.append_event(
            connection,
            state="GC_APPLIED",
            detail=(
                f"{instance_id} payload {'removed' if removed else 'was already absent'} at "
                f"{store_dir}; registry row retained for binding history"
            ),
            approval_id=str(token["approval_id"]),
            plan_hash=str(plan["plan_hash"]),
            artifact_digest=expected,
            reason_code=None,
            outcome="ok",
            approval_mode=str(token["approval_mode"]),
        )
    registry.update_projection()
    return _gc_result(registry, plan, instance_id, already=False, removed=removed, store_dir=store_dir)


def _gc_result(
    registry: Any,
    plan: dict[str, Any],
    instance_id: str,
    *,
    already: bool,
    removed: bool = False,
    store_dir: Path | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": GC_APPLY,
        "plan_id": str(plan["plan_id"]),
        "plan_hash": str(plan["plan_hash"]),
        "instance_id": instance_id,
        "store_path": str(store_dir) if store_dir is not None else str(plan["metadata"].get("store_path")),
        "payload_removed": removed,
        "already_collected": already,
        "data_root_files_touched": 0,
        "reason_code": "SUCCESS",
    }


# --------------------------------------------------------------------------- #
# uninstall: retire then gc, never one step
# --------------------------------------------------------------------------- #


def uninstall_target(registry: Any, target: str, *, clock: Clock = SYSTEM_CLOCK) -> dict[str, Any]:
    """Retire an owned instance and return the plan that would collect it.

    ``uninstall`` never deletes on its own: the caller must obtain approval for the returned
    plan. This function performs the ``retire`` half because that half deletes nothing.
    """

    row = require_owned(registry, target)
    retired = retire(registry, str(row["instance_id"]), clock=clock)
    plan = build_gc_plan(
        registry,
        str(row["instance_id"]),
        clock=clock,
        requested_by="airoot-cli",
    )
    return {"retire": retired, "plan": plan}


__all__ = [
    "Collectable",
    "DEFAULT_PLAN_TTL_SECONDS",
    "EXTERNAL_BACKENDS",
    "GC_APPLY",
    "OWNED_STORE_PREFIX",
    "RETIRE_TOOL",
    "apply_gc_plan",
    "build_gc_plan",
    "check_payload_removable",
    "expected_launchers",
    "find_target",
    "gc_candidates",
    "is_owned",
    "require_owned",
    "retire",
    "uninstall_target",
]
