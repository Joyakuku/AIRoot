"""Simulation transaction: the P1 stand-in for a real controlled install.

P1 deliberately has exactly one install backend, ``fake_fixture``: a deterministic
payload generated under ``cache/fixtures`` that is *never executed*. A plan whose
source is anything else is refused with ``UNSUPPORTED_BACKEND``, so P1 cannot be
pointed at real software.

Everything else follows the frozen contract: plan → approval → write-ahead
journal → ``COMMITTED`` → ``REGISTERED`` (inactive) → ``ACTIVE_BOUND`` (the only
point that may change the active binding, atomically with the new generation)
→ ``EXPOSED`` → ``VERIFIED_AGAIN`` → ``FINALIZED``.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..canon import file_manifest, file_manifest_digest, plan_hash, tree_digest
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..paths import from_root_relative, relative_to_root
from ..schema_io import validate_document, validate_self
from ..registry.entities import (
    Binding,
    Instance,
    binding_key,
    managed_tool_payload,
    validate_instance,
)
from .approval import load_keyring, verify_approval
from .journal import TransactionJournal, classify
from .registration import register_instance
from .rollback import revert_own_activation
from .states import HAPPY_PATH, is_terminal

SIMULATION_BACKEND = "fake_fixture"
DEFAULT_CAPABILITY = "fake-tool"
PAYLOAD_NAME = "fake-tool.bin"
PAYLOAD_BYTES = b"AIROOT-FAKE-TOOL-V1\nThis payload is data and is never executed.\n"
FIXTURE_PREFIX = "cache/fixtures"

# Conditions that are the caller's problem to resolve (re-read state and retry, or
# fix the build) rather than something to record as a rollback of this plan.
PROPAGATING_CODES = frozenset({"STALE_GENERATION", "SELF_VALIDATION_FAILED", "ILLEGAL_TRANSITION"})


def _evidence_objects(items: list[str] | None) -> list[dict[str, Any]]:
    """``transaction.schema.json`` expects evidence objects, not bare strings."""

    return [{"kind": "failure", "detail": str(item)} for item in (items or [])]


@dataclass
class SimulationBackend:
    """Deterministic fake artifact, materialised inside the root's cache."""

    root: Path
    clock: Clock = SYSTEM_CLOCK
    capability_id: str = DEFAULT_CAPABILITY
    tool_id: str = DEFAULT_CAPABILITY

    def fixture_dir(self, version: str) -> Path:
        return Path(self.root) / FIXTURE_PREFIX / self.capability_id / version

    def materialise(self, version: str) -> Path:
        directory = self.fixture_dir(version)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / PAYLOAD_NAME).write_bytes(PAYLOAD_BYTES)
        (directory / "artifact.json").write_text(
            json.dumps(
                {
                    "kind": "managed_tool",
                    "name": self.tool_id,
                    "test_only": True,
                    "version": version,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return directory

    def source_document(self, version: str) -> dict[str, Any]:
        directory = self.materialise(version)
        manifest = file_manifest(directory)
        return {
            "kind": "generated_fixture",
            "locator": relative_to_root(directory, Path(self.root)),
            "provenance": {"source_id": f"fixture/{self.tool_id}-v1", "publisher": "airoot-test"},
            "integrity": {
                "artifact_digest": tree_digest(directory),
                "file_manifest_digest": file_manifest_digest(manifest),
            },
            "signature": None,
        }

    def instance_id(self, version: str) -> str:
        return f"{self.capability_id}/{self.tool_id}/{version}/win-x64"


def create_plan(
    registry: Any,
    *,
    version: str = "1.0.0",
    clock: Clock = SYSTEM_CLOCK,
    ttl_minutes: int = 5,
    requested_by: str = "airoot-cli",
    capability_id: str = DEFAULT_CAPABILITY,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Build and hash a canonical plan for the simulated capability.

    ``plan_id`` is injectable so golden fixtures can be reproducible; production
    callers leave it unset and get a random id.
    """

    backend = SimulationBackend(Path(registry.path).parent.parent, clock=clock, capability_id=capability_id)
    source = backend.source_document(version)
    instance_id = backend.instance_id(version)
    created = clock.now()
    key = binding_key(capability_id, "machine")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan_id or f"plan/{capability_id}/{version}/{uuid.uuid4().hex[:12]}",
        "plan_hash": "sha256:" + "0" * 64,
        "operation": "install_tool",
        "root_instance_id": registry.root_instance_id,
        "machine_id": registry.machine_id,
        "policy_revision": registry.policy_revision,
        "created_at": created.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at": (created + timedelta(minutes=ttl_minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "requested_by": requested_by,
        "target": {
            "capability_id": capability_id,
            "instance_id": instance_id,
            "kind": "managed_tool",
            "version": version,
            "platform": "windows",
            "architecture": "x64",
            "binding_key": key,
        },
        "operations": [
            {
                "step_id": "step/fetch",
                "kind": "fetch",
                "description": "read the deterministic simulated artifact from cache/fixtures",
                "target_scope": "machine",
                "reversible": True,
                "source_mutation": "none",
                "artifact_digest": source["integrity"]["artifact_digest"],
            },
            {
                "step_id": "step/verify",
                "kind": "verify",
                "description": "verify artifact digest and file manifest",
                "target_scope": "machine",
                "reversible": True,
                "artifact_digest": source["integrity"]["artifact_digest"],
            },
            {
                "step_id": "step/stage",
                "kind": "stage",
                "description": "stage the payload inside tx/<id>/stage on the same volume",
                "target_scope": "machine",
                "reversible": True,
            },
            {
                "step_id": "step/commit",
                "kind": "commit",
                "description": "move the verified payload into the immutable store",
                "target_scope": "machine",
                "reversible": True,
            },
            {
                "step_id": "step/expose",
                "kind": "expose",
                "description": "bind the active generation and observe it",
                "target_scope": "machine",
                "reversible": True,
            },
        ],
        "source": source,
        "side_effects": ["writes_store", "writes_registry", "derived_cache"],
        "canonicalization": "jcs-rfc8785-compatible",
        "metadata": {"simulation": True, "backend": SIMULATION_BACKEND},
    }
    plan["plan_hash"] = plan_hash(plan)
    validate_self("plan", plan)
    return plan


class SimulationRunner:
    """Drives the frozen transaction state machine against the simulated artifact."""

    def __init__(
        self,
        registry: Any,
        *,
        clock: Clock = SYSTEM_CLOCK,
        injector: Any = None,
        keyring: dict[str, bytes] | None = None,
    ) -> None:
        self.registry = registry
        self.clock = clock
        self.root = Path(registry.path).parent.parent
        self.journal = TransactionJournal(registry, clock=clock, injector=injector)
        self.backend = SimulationBackend(self.root, clock=clock)
        self._keyring = keyring
        #: ``None`` until this run's ``REGISTERED`` step answers it, then whether this run *inserted*
        #: the instance row — read *before* registering, so the answer is about the row's origin and
        #: not about what exists afterwards (draft §164). `None` is a real answer on a resume that
        #: starts at or past ``ACTIVE_BOUND``. Same rule as the artifact runner.
        self._registered_payload: bool | None = None

    # ------------------------------------------------------------------ guards #

    def keyring(self) -> dict[str, bytes]:
        return self._keyring if self._keyring is not None else load_keyring(self.root)

    @staticmethod
    def require_simulation_source(plan: dict[str, Any]) -> None:
        source = plan.get("source")
        if not isinstance(source, dict) or source.get("kind") != "generated_fixture":
            raise AirootError(
                "UNSUPPORTED_BACKEND",
                "P1 only accepts the simulated generated_fixture source",
                evidence=[f"kind={(source or {}).get('kind')}", "script and network backends belong to P4+"],
            )
        locator = str(source.get("locator", ""))
        if not locator.replace("\\", "/").startswith(FIXTURE_PREFIX + "/"):
            raise AirootError(
                "UNSUPPORTED_BACKEND",
                "the simulated source must live under cache/fixtures",
                evidence=[f"locator={locator}"],
            )

    # ----------------------------------------------------------------- commit #

    def commit(self, plan: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
        validate_document("plan", plan, reason_code="INVALID_PLAN")
        computed = plan_hash(plan)
        if computed != plan["plan_hash"]:
            raise AirootError(
                "INVALID_PLAN",
                "plan hash does not match the plan contents",
                evidence=[f"declared={plan['plan_hash']}", f"computed={computed}"],
            )
        self.require_simulation_source(plan)

        # Approval checks come first so a replayed or expired token is refused
        # (P-006/P-005) rather than being absorbed by the idempotency shortcut.
        verify_approval(self.registry, plan, token, keyring=self.keyring(), clock=self.clock)

        applied = self._already_applied(plan)
        if applied is not None:
            applied["idempotent"] = True
            return applied

        tx = self.journal.create(
            plan,
            token,
            instance_id=plan["target"]["instance_id"],
            apply=lambda connection: self.registry.upsert_approval(connection, token),
        )
        try:
            self.journal.checkpoint(tx)
            result = self.drive(tx, plan)
        except InterruptedError:
            tx["interrupted"] = True
            return tx
        self._refresh_projections(result)
        return result

    def resume(self, transaction_id: str) -> dict[str, Any]:
        tx, plan, token = self.journal.load_context(transaction_id)
        if is_terminal(str(tx["state"])):
            return {"transaction": tx, "action": "no_action", "reason": "transaction already terminal"}
        if plan is None or token is None:
            raise AirootError(
                "JOURNAL_TRUNCATED",
                "the journal envelope cannot resume: plan or approval missing",
                evidence=[f"transaction={transaction_id}"],
            )
        action = classify(tx)
        if action.requires_explicit_repair:
            raise AirootError(
                "RECOVERY_REQUIRED",
                f"transaction {transaction_id} requires an explicit repair decision",
                evidence=action.evidence,
            )
        verify_approval(
            self.registry,
            plan,
            token,
            keyring=self.keyring(),
            clock=self.clock,
            check_expiry=False,
        )
        # Every step below is written to be idempotent, so a resume never rewinds the
        # journal: a missing stage is simply rebuilt from the deterministic source.
        result = self.drive(tx, plan)
        self._refresh_projections(result)
        return result

    def _refresh_projections(self, tx: dict[str, Any]) -> None:
        """Refresh the derived JSON projections once a transaction has ended."""

        if str(tx.get("state")) in {"FINALIZED", "ROLLED_BACK", "EXPIRED"}:
            self.registry.update_projection()

    # --------------------------------------------------------------- idempotent #

    def _ensure_stage(self, tx: dict[str, Any], source_dir: Path, expected_digest: str) -> Path:
        stage = self.journal.stage_path(str(tx["transaction_id"]))
        if not stage.exists():
            stage.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, stage)
        if tree_digest(stage) != expected_digest:
            raise AirootError("DIGEST_MISMATCH", "staged payload does not match the plan digest")
        return stage

    # ------------------------------------------------------------------- drive #

    def drive(self, tx: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        instance_id = str(tx["instance_id"])
        expected_digest = plan["source"]["integrity"]["artifact_digest"]
        key = plan["target"].get("binding_key") or binding_key(plan["target"]["capability_id"], "machine")
        source_dir = from_root_relative(plan["source"]["locator"], self.root)
        store_dir = from_root_relative(f"store/{instance_id}", self.root)

        try:
            if tx["state"] == "PROPOSED":
                if tree_digest(source_dir) != expected_digest:
                    return self._fail(tx, "DIGEST_MISMATCH", "source changed after planning")
                self.journal.advance(tx, "APPROVED", "approval verified against plan, root, machine and policy")

            if tx["state"] == "APPROVED":
                self.journal.advance(tx, "FETCHED", "simulated artifact read from the declared fixture")

            if tx["state"] == "FETCHED":
                self._ensure_stage(tx, source_dir, expected_digest)
                self.journal.advance(tx, "VERIFIED", "stage tree digest verified")

            if tx["state"] == "VERIFIED":
                self._ensure_stage(tx, source_dir, expected_digest)
                self.journal.advance(tx, "STAGED", "stage manifest written for the immutable store")

            if tx["state"] == "STAGED":
                self._ensure_stage(tx, source_dir, expected_digest)
                if store_dir.exists():
                    if tree_digest(store_dir) != expected_digest:
                        return self._fail(tx, "INSTANCE_CONFLICT", "immutable store path holds a different payload")
                else:
                    store_dir.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(self.journal.stage_path(str(tx["transaction_id"])), store_dir)
                self.journal.advance(tx, "COMMITTED", "payload moved into the immutable store")

            if tx["state"] == "COMMITTED":
                instance = self._instance_document(plan, store_dir)
                # Same rule as the real runner (draft §150). This runner's own plan builder fixes
                # kind=managed_tool, so the payload below is still that shape.
                validate_instance(instance.payload, kind=str(instance.kind))

                # Same rule as the real runner (draft §164), and the same one implementation of it:
                # `tx/registration.py` reads the row before writing it and clears the collected
                # latch when the row was already there.
                def _register(connection: Any) -> None:
                    self._registered_payload = register_instance(self.registry, connection, instance)

                self.journal.advance(
                    tx,
                    "REGISTERED",
                    "instance registered inactive",
                    apply=_register,
                )

            if tx["state"] == "REGISTERED":
                new_generation = self.registry.generation + 1
                binding = Binding(key, instance_id, "machine", "R", "stable_launcher", new_generation, True)

                def _bind(connection: Any) -> None:
                    self.registry.bind_active(connection, binding)
                    self.registry.set_instance_status(
                        connection, instance_id, lifecycle_status="active", health="healthy"
                    )

                self.journal.advance(
                    tx,
                    "ACTIVE_BOUND",
                    "active binding and registry generation committed in one transaction",
                    bump=True,
                    apply=_bind,
                )

            if tx["state"] in {"ACTIVE_BOUND", "EXPOSED"}:
                if not self._observe_active_binding(key, instance_id):
                    return self._rollback(tx, "EXPOSURE_VERIFY_FAILED", "the new binding is not observable")
                if tx["state"] == "ACTIVE_BOUND":
                    # ADR-0050: `EXPOSED` is the exposure write side, not only an observation.
                    from ..caps.launcher import write_launcher

                    write_launcher(self.root, str(plan["target"]["capability_id"]))
                    self.journal.advance(tx, "EXPOSED", "active binding observed through a fresh registry read")

            if tx["state"] == "EXPOSED":
                if not self._verify_payload_and_binding(key, instance_id, expected_digest):
                    return self._rollback(tx, "VERIFY_FAILED", "post-bind verification failed")
                self.journal.advance(tx, "VERIFIED_AGAIN", "post-bind verification passed")

            if tx["state"] == "VERIFIED_AGAIN":
                self.journal.advance(
                    tx,
                    "FINALIZED",
                    "approval nonce consumed and transaction finalized",
                    apply=lambda connection: self.registry.consume_approval(connection, str(tx["approval_id"])),
                )
            return tx
        except InterruptedError:
            raise
        except AirootError as error:
            if error.reason_code in PROPAGATING_CODES:
                # A stale generation means "re-read state and retry the commit"; it is
                # not a domain failure of this plan. SELF_VALIDATION_FAILED is an
                # implementation bug and must never be dressed up as a rollback.
                raise
            if tx["state"] in {"PROPOSED", "APPROVED", "FETCHED", "VERIFIED"}:
                return self._fail(tx, error.reason_code, error.message, evidence=error.evidence)
            return self._rollback(tx, error.reason_code, error.message, evidence=error.evidence)
        except OSError as error:
            # The same hole the artifact runner had (draft §62): this path also copies and renames
            # bytes (`_ensure_stage`, `os.replace`), so a full disk or a locked file lands here as a
            # raw `OSError` — outside the `AirootError` branch above, and therefore outside both the
            # diagnosis and the rollback. Fixed in the sibling rather than only where the scenario
            # pointed: "the same bug one file over" is not a different bug.
            return self._fail_or_rollback_io(tx, error)

    def _fail_or_rollback_io(self, tx: dict[str, Any], error: OSError) -> dict[str, Any]:
        """Turn a filesystem refusal into a diagnosed failure, before or after the binding moved."""

        message = f"the filesystem refused the {tx['state']} step"
        evidence = [
            f"{type(error).__name__}: {error}",
            f"errno={getattr(error, 'errno', None)} winerror={getattr(error, 'winerror', None)}",
            "the request is fine; free the space, release the lock or fix the permission and re-plan",
        ]
        if tx["state"] in {"PROPOSED", "APPROVED", "FETCHED", "VERIFIED"}:
            return self._fail(tx, "INSTALL_IO_FAILED", message, evidence=evidence)
        return self._rollback(tx, "INSTALL_IO_FAILED", message, evidence=evidence)

    # ------------------------------------------------------------------ pieces #

    def _instance_document(self, plan: dict[str, Any], store_dir: Path) -> Instance:
        instance_id = plan["target"]["instance_id"]
        manifest = file_manifest(store_dir)
        payload = managed_tool_payload(
            tool_id=self.backend.tool_id,
            instance_id=instance_id,
            capability_id=plan["target"]["capability_id"],
            version=plan["target"]["version"],
            install_backend_id=SIMULATION_BACKEND,
            artifact_digest=plan["source"]["integrity"]["artifact_digest"],
            store_path=f"store/{instance_id}",
            file_manifest_digest=file_manifest_digest(manifest),
            lifecycle_status="installed",
            health="healthy",
            entrypoints=[PAYLOAD_NAME],
            source=plan["source"],
            file_manifest=manifest,
            created_at=self.clock.timestamp(),
        )
        return Instance(
            instance_id=instance_id,
            kind="managed_tool",
            capability_id=plan["target"]["capability_id"],
            version=plan["target"]["version"],
            platform="windows",
            architecture="x64",
            install_backend_id=SIMULATION_BACKEND,
            artifact_digest=plan["source"]["integrity"]["artifact_digest"],
            store_path=f"store/{instance_id}",
            lifecycle_status="installed",
            health="healthy",
            tool_id=self.backend.tool_id,
            file_manifest_digest=file_manifest_digest(manifest),
            entrypoints=(PAYLOAD_NAME,),
            payload=payload,
            created_at=self.clock.timestamp(),
        )

    def _already_applied(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        """Re-installing the same plan is idempotent (test T-009)."""

        for row in self.registry.transactions():
            if row["plan_id"] != plan["plan_id"]:
                continue
            payload = json.loads(row["payload_json"])
            if payload["state"] == "FINALIZED":
                return payload
        return None

    def _observe_active_binding(self, key: str, instance_id: str) -> bool:
        return any(
            row["binding_key"] == key and row["instance_id"] == instance_id
            for row in self.registry.bindings(active_only=True)
        )

    def _verify_payload_and_binding(self, key: str, instance_id: str, expected_digest: str) -> bool:
        row = self.registry.instance(instance_id)
        if row is None or row["artifact_digest"] != expected_digest:
            return False
        store_dir = from_root_relative(row["store_path"], self.root)
        if not store_dir.is_dir() or tree_digest(store_dir) != expected_digest:
            return False
        return self._observe_active_binding(key, instance_id)

    def _fail(
        self,
        tx: dict[str, Any],
        code: str,
        message: str,
        *,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        self.journal.advance(
            tx,
            "FAILED",
            message,
            failure={
                "code": code,
                "message": message,
                "retryable": code in PROPAGATING_CODES,
                "evidence": _evidence_objects(evidence),
            },
        )
        self.journal.advance(tx, "ROLLED_BACK", "no active binding was changed")
        tx["outcome"] = code
        return tx

    def _rollback(
        self,
        tx: dict[str, Any],
        code: str,
        message: str,
        *,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Switch the active binding back; keep the new payload as evidence.

        "Back" means **this transaction's own activation**, never the key as a whole: a concurrent
        transaction that committed after us owns the key now and must survive our rollback. The
        rules live in one place, `tx/rollback.py`, shared with `artifact.py` (draft §109) — including
        the candidate filter that stops a rollback restoring a retired or `gc`-collected predecessor
        (draft §164), and the evidence that records what the revert chose or refused.
        """

        instance_id = str(tx["instance_id"])
        outcome: dict[str, Any] = {}
        #: Whether this run inserted the instance row (read by ``REGISTERED`` before it wrote it).
        #: `None` on a resume that starts at or past ``ACTIVE_BOUND``, which is why it is a signal
        #: and not the answer.
        inserted_here = self._registered_payload

        def _row_is_live_for_another_transaction(reverted: Any) -> bool:
            """Same rule as `artifact.py` (draft §164): a live, healthy row that is not this
            transaction's own work — it never passed ``ACTIVE_BOUND`` and no restore pointed at it —
            is left alone instead of being rewritten to `broken`."""

            if str(tx.get("generation_after")) not in {"None", ""}:
                return False
            if reverted.restored_instance_id == instance_id:
                return False
            if inserted_here:
                return False
            row = self.registry.instance(instance_id)
            return bool(
                row is not None
                and str(row["lifecycle_status"]) == "active"
                and str(row["health"]) == "healthy"
            )

        def _revert(connection: Any) -> None:
            reverted = revert_own_activation(self.registry, connection, instance_id=instance_id)
            outcome["evidence"] = reverted.evidence()
            if not _row_is_live_for_another_transaction(reverted):
                self.registry.set_instance_status(
                    connection, instance_id, health="broken", lifecycle_status="broken"
                )
            else:
                outcome["evidence"] = [
                    *outcome["evidence"],
                    f"instance {instance_id} reads active/healthy and this transaction did not "
                    "register it, so its row was left as it was (draft §164)",
                ]

        failure: dict[str, Any] = {
            "code": code,
            "message": message,
            "retryable": False,
            "evidence": _evidence_objects(evidence),
        }

        def _apply(connection: Any) -> None:
            _revert(connection)
            failure["evidence"] = [
                *(failure["evidence"] or []),
                *_evidence_objects([str(item) for item in outcome.get("evidence", [])]),
            ]

        self.journal.advance(
            tx,
            "ROLLBACK_PENDING",
            message,
            failure=failure,
            bump=True,
            apply=_apply,
        )
        self.journal.advance(tx, "ROLLED_BACK", "active binding switched back; new payload retained as evidence")
        tx["outcome"] = code
        return tx

    # -------------------------------------------------------------- reporting #

    def unfinished(self) -> list[dict[str, Any]]:
        return self.journal.unfinished()

    def recovery_action(self, transaction_id: str) -> Any:
        tx, _plan, _token = self.journal.load_context(transaction_id)
        return classify(tx)


def happy_path_states() -> tuple[str, ...]:
    return HAPPY_PATH


def repair(
    registry: Any,
    transaction_id: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    keyring: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Journal-driven, idempotent repair (v0.3 §4.3 / §14.4)."""

    journal = TransactionJournal(registry, clock=clock)
    tx, plan, _token = journal.load_context(transaction_id)
    action = classify(tx)
    if action.action == "no_action":
        return {"action": "no_action", "state": tx["state"], "transaction_id": transaction_id}
    if action.requires_explicit_repair:
        raise AirootError(
            "RECOVERY_REQUIRED",
            f"transaction {transaction_id} needs an explicit decision",
            evidence=action.evidence,
        )
    from .runners import runner_for

    # Resume with the driver this transaction was created with. Hard-coding the simulation runner
    # meant a real artifact transaction was finished by the wrong one (draft §128).
    runner = runner_for(registry, plan, clock=clock, keyring=keyring)
    result = runner.resume(transaction_id)
    return {"action": action.action, "state": result.get("state", tx["state"]), "transaction_id": transaction_id, "result": result}


__all__ = [
    "SIMULATION_BACKEND",
    "DEFAULT_CAPABILITY",
    "PAYLOAD_NAME",
    "PAYLOAD_BYTES",
    "FIXTURE_PREFIX",
    "SimulationBackend",
    "SimulationRunner",
    "create_plan",
    "repair",
    "happy_path_states",
]
