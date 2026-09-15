"""The transaction driver for artifact backends (§21.5).

``SimulationRunner`` drives the fake fixture; this drives a real, verified artifact. The state
machine, journal, generation rule and approval handling are **identical** — only the four byte
moving steps differ, and they are delegated to the backend.

Two digest meanings are kept separate on purpose:

* ``plan.source.integrity.artifact_digest`` — the **source** digest (the file's SHA256). It is
  what ``fetch``/``verify`` check against the plan.
* ``instance.artifact_digest`` — the **owned payload** digest (tree digest of ``store/<id>``).
  It is what ``doctor`` re-checks later, and what makes "the payload changed after installation"
  detectable for a real artifact exactly as it already is for the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..canon import file_manifest, file_manifest_digest, plan_hash, tree_digest
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..paths import from_root_relative, relative_to_root
from ..schema_io import validate_document, validate_self
from ..registry.entities import Binding, Instance, binding_key, managed_tool_payload
from .approval import load_keyring, verify_approval
from .journal import TransactionJournal, classify
from .simulate import PROPAGATING_CODES, _evidence_objects
from .states import is_terminal


class ArtifactRunner:
    """Plan → approval → fetch → verify → stage → commit → bind, for a real artifact."""

    def __init__(
        self,
        registry: Any,
        backend: Any,
        *,
        clock: Clock = SYSTEM_CLOCK,
        injector: Any = None,
        keyring: dict[str, bytes] | None = None,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.clock = clock
        self.root = Path(registry.path).parent.parent
        self.journal = TransactionJournal(registry, clock=clock, injector=injector)
        self._keyring = keyring

    def keyring(self) -> dict[str, bytes]:
        return self._keyring if self._keyring is not None else load_keyring(self.root)

    def _fetch_dir(self, transaction_id: str) -> Path:
        return self.journal.stage_path(transaction_id).parent / "fetch"

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
        if str(result.get("state")) in {"FINALIZED", "ROLLED_BACK", "EXPIRED"}:
            self.registry.update_projection()
        return result

    def resume(self, transaction_id: str) -> dict[str, Any]:
        tx, plan, token = self.journal.load_context(transaction_id)
        if is_terminal(str(tx["state"])):
            return {"transaction": tx, "action": "no_action", "reason": "transaction already terminal"}
        if plan is None or token is None:
            raise AirootError("JOURNAL_TRUNCATED", "the journal envelope cannot resume")
        verify_approval(
            self.registry, plan, token, keyring=self.keyring(), clock=self.clock, check_expiry=False
        )
        return self.drive(tx, plan)

    # ------------------------------------------------------------------- drive #

    def drive(self, tx: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        instance_id = str(tx["instance_id"])
        expected_source = plan["source"]["integrity"]["artifact_digest"]
        locator = plan["source"]["locator"]
        key = plan["target"].get("binding_key") or binding_key(plan["target"]["capability_id"], "machine")
        store_dir = from_root_relative(f"store/{instance_id}", self.root)
        fetch_dir = self._fetch_dir(str(tx["transaction_id"]))
        artifact_path = fetch_dir / Path(locator).name

        try:
            if tx["state"] == "PROPOSED":
                self.journal.advance(tx, "APPROVED", "approval verified against plan, root, machine and policy")

            if tx["state"] == "APPROVED":
                if not artifact_path.is_file():
                    artifact = self.backend.fetch(locator=locator, destination=artifact_path)
                else:
                    from ..caps.backends.base import Artifact, sha256_file

                    artifact = Artifact(
                        path=artifact_path,
                        digest=sha256_file(artifact_path),
                        size=artifact_path.stat().st_size,
                        fetched_from=locator,
                    )
                self._artifact = artifact
                self.journal.advance(tx, "FETCHED", f"artifact read ({artifact.size} bytes)")

            if tx["state"] == "FETCHED":
                result = self.backend.verify(self._artifact, expected_digest=expected_source)
                result.require_ok(expected=expected_source)
                self.journal.advance(tx, "VERIFIED", f"artifact digest verified: {result.digest}")

            if tx["state"] == "VERIFIED":
                self._ensure_stage(tx)
                self.journal.advance(tx, "STAGED", "artifact staged for the immutable store")

            if tx["state"] == "STAGED":
                self._ensure_stage(tx)
                self._commit_stage(tx, store_dir)

            if tx["state"] == "COMMITTED":
                instance = self._instance_document(plan, store_dir)
                validate_self("managed-tool-instance", instance.payload)
                self.journal.advance(
                    tx,
                    "REGISTERED",
                    "instance registered inactive",
                    apply=lambda connection: self.registry.add_instance(connection, instance),
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
                    self.journal.advance(tx, "EXPOSED", "active binding observed through a fresh registry read")

            if tx["state"] == "EXPOSED":
                row = self.registry.instance(instance_id)
                if row is None or not store_dir.is_dir() or tree_digest(store_dir) != row["artifact_digest"]:
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
                raise
            if tx["state"] in {"PROPOSED", "APPROVED", "FETCHED", "VERIFIED"}:
                return self._fail(tx, error.reason_code, error.message, evidence=error.evidence)
            return self._rollback(tx, error.reason_code, error.message, evidence=error.evidence)

    def _ensure_stage(self, tx: dict[str, Any]) -> Path:
        """Idempotent staging: a resumed transaction never re-fetches or re-copies blindly."""

        stage_dir = self.journal.stage_path(str(tx["transaction_id"]))
        if not stage_dir.is_dir() or not any(item.is_file() for item in stage_dir.iterdir()):
            self.backend.stage(self._artifact, stage_dir=stage_dir)
        return stage_dir

    def _commit_stage(self, tx: dict[str, Any], store_dir: Path) -> None:
        self._ensure_stage(tx)
        self.backend.commit(store_dir=store_dir, stage_dir=self.journal.stage_path(str(tx["transaction_id"])))
        self.journal.advance(tx, "COMMITTED", "payload moved into the immutable store")

    # ------------------------------------------------------------------ pieces #

    def _instance_document(self, plan: dict[str, Any], store_dir: Path) -> Instance:
        instance_id = plan["target"]["instance_id"]
        manifest = file_manifest(store_dir)
        payload_digest = tree_digest(store_dir)
        entrypoints = self.backend.expose(store_dir)
        payload = managed_tool_payload(
            tool_id=plan["target"]["capability_id"],
            instance_id=instance_id,
            capability_id=plan["target"]["capability_id"],
            version=plan["target"]["version"],
            install_backend_id=plan["metadata"]["backend_id"],
            artifact_digest=payload_digest,
            store_path=f"store/{instance_id}",
            file_manifest_digest=file_manifest_digest(manifest),
            lifecycle_status="installed",
            health="healthy",
            entrypoints=entrypoints,
            source=plan["source"],
            file_manifest=manifest,
            created_at=self.clock.timestamp(),
        )
        return Instance(
            instance_id=instance_id,
            kind=plan["target"]["kind"],
            capability_id=plan["target"]["capability_id"],
            version=plan["target"]["version"],
            platform=plan["target"]["platform"],
            architecture=plan["target"]["architecture"],
            install_backend_id=plan["metadata"]["backend_id"],
            artifact_digest=payload_digest,
            store_path=f"store/{instance_id}",
            lifecycle_status="installed",
            health="healthy",
            tool_id=plan["target"]["capability_id"],
            file_manifest_digest=file_manifest_digest(manifest),
            entrypoints=tuple(entrypoints),
            payload=payload,
            created_at=self.clock.timestamp(),
        )

    def _already_applied(self, plan: dict[str, Any]) -> dict[str, Any] | None:
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

    def _fail(
        self, tx: dict[str, Any], code: str, message: str, *, evidence: list[str] | None = None
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
        self, tx: dict[str, Any], code: str, message: str, *, evidence: list[str] | None = None
    ) -> dict[str, Any]:
        instance_id = str(tx["instance_id"])
        previous_generation = tx.get("generation_before")

        def _revert(connection: Any) -> None:
            key = self._binding_key_of(instance_id)
            if key is not None:
                self.registry.clear_active_binding(connection, key)
                if previous_generation is not None:
                    connection.execute(
                        "UPDATE bindings SET active = 1 WHERE binding_key = ? AND generation = ?",
                        (key, int(previous_generation)),
                    )
            self.registry.set_instance_status(
                connection, instance_id, health="broken", lifecycle_status="broken"
            )

        self.journal.advance(
            tx,
            "ROLLBACK_PENDING",
            message,
            failure={
                "code": code,
                "message": message,
                "retryable": False,
                "evidence": _evidence_objects(evidence),
            },
            bump=True,
            apply=_revert,
        )
        self.journal.advance(tx, "ROLLED_BACK", "active binding switched back; new payload retained as evidence")
        tx["outcome"] = code
        return tx

    def _binding_key_of(self, instance_id: str) -> str | None:
        for row in self.registry.bindings():
            if row["instance_id"] == instance_id:
                return str(row["binding_key"])
        return None


def create_artifact_plan(
    registry: Any,
    backend: Any,
    *,
    capability_id: str,
    version: str,
    kind: str,
    locator: str,
    source_digest: str,
    platform: str = "windows",
    architecture: str = "x64",
    clock: Clock = SYSTEM_CLOCK,
    ttl_minutes: int = 5,
    requested_by: str = "airoot-cli",
    plan_id: str | None = None,
) -> dict[str, Any]:
    """A canonical plan for a real artifact, with the backend recorded in ``metadata``."""

    import uuid
    from datetime import timedelta

    from ..clock import isoformat, parse_timestamp

    declaration = backend.declaration
    if declaration.executes_scripts:
        raise AirootError(
            "UNSUPPORTED_BACKEND",
            f"backend {declaration.backend_id} executes scripts and may not enter the core transaction",
            evidence=["script-type installers need their own declared model (三大核心契约 决策3:49)"],
        )
    if not source_digest.startswith("sha256:"):
        raise AirootError(
            "INVALID_PLAN",
            "a real artifact plan requires a sha256 source digest",
            evidence=[f"source_digest={source_digest!r}", "v1 does no signature checks, so the digest is the proof"],
        )
    created_at = clock.timestamp()
    instance_id = f"{capability_id}/{Path(locator).stem}/{version}/win-x64"
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan_id or f"plan/{capability_id}/{version}/{uuid.uuid4().hex[:12]}",
        "plan_hash": "sha256:" + "0" * 64,
        "operation": "install_tool" if kind == "managed_tool" else "install_runtime",
        "root_instance_id": registry.root_instance_id,
        "machine_id": registry.machine_id,
        "policy_revision": registry.policy_revision,
        "created_at": created_at,
        "expires_at": isoformat(parse_timestamp(created_at) + timedelta(minutes=ttl_minutes)),
        "requested_by": requested_by,
        "target": {
            "capability_id": capability_id,
            "instance_id": instance_id,
            "kind": kind,
            "version": version,
            "platform": platform,
            "architecture": architecture,
            "binding_key": binding_key(capability_id, "machine"),
        },
        "operations": [
            {
                "step_id": "step/fetch",
                "kind": "fetch",
                "description": f"fetch {locator} with backend {declaration.backend_id}",
                "target_scope": "machine",
                "reversible": True,
                "source_mutation": "none",
                "artifact_digest": source_digest,
            },
            {
                "step_id": "step/verify",
                "kind": "verify",
                "description": "verify the artifact against the planned sha256 digest",
                "target_scope": "machine",
                "reversible": True,
                "artifact_digest": source_digest,
            },
            {
                "step_id": "step/stage",
                "kind": "stage",
                "description": "stage the verified artifact on the same volume",
                "target_scope": "machine",
                "reversible": True,
            },
            {
                "step_id": "step/commit",
                "kind": "commit",
                "description": "move the staged payload into the immutable store",
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
        "source": {
            "kind": "https" if declaration.network_access else "local_file",
            "locator": locator,
            "provenance": {"source_id": f"{declaration.backend_id}/{capability_id}", "publisher": requested_by},
            "integrity": {"artifact_digest": source_digest, "file_manifest_digest": source_digest},
            "signature": None,
        },
        "side_effects": ["writes_store", "writes_registry"],
        "canonicalization": "jcs-rfc8785-compatible",
        "metadata": {
            "backend_id": declaration.backend_id,
            "backend": declaration.to_document(),
            "source_digest": source_digest,
            "v1_baseline": "script-free artifact; digest verified before commit",
        },
    }
    plan["plan_hash"] = plan_hash(plan)
    validate_self("plan", plan)
    return plan


def store_relative(root: Path, path: Path) -> str:
    return relative_to_root(path, root)


__all__ = ["ArtifactRunner", "create_artifact_plan", "store_relative"]
