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
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
    runtime_family_for,
    runtime_instance_payload,
    validate_instance,
)
from .approval import load_keyring, verify_approval
from .journal import TransactionJournal, classify
from .registration import register_instance
from .rollback import revert_own_activation
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
        #: ``None`` until this run's ``REGISTERED`` step answers it, then whether this run *inserted*
        #: the instance row (draft §164) — the step reads the registry *before* it registers, so the
        #: answer is about the row's origin and not about what exists afterwards. `None` is a real
        #: answer: a resume may start at or past ``ACTIVE_BOUND``, where the step never re-runs, so
        #: the rollback treats this as one signal among several rather than as a default. See
        #: ``_row_is_live_for_another_transaction``.
        self._registered_payload: bool | None = None

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

    def _artifact_from_fetch_dir(self, artifact_path: Path, locator: str) -> Any:
        """The fetched artifact, rebuilt from the file the fetch step left behind.

        `self._artifact` is in-process state: it is set by the fetch step of *this* process, so a resumed
        transaction starts without it, and reading it anyway is what made resuming from `FETCHED` raise
        `AttributeError` instead of continuing or rolling back (draft §128.5). The file on disk is the one
        the fetch step wrote, so the description can be rebuilt from it; when the file is gone, recovery
        says so with a reason code rather than an attribute error -- a crash is not a verdict.
        """

        from ..caps.backends.base import Artifact, sha256_file

        if not artifact_path.is_file():
            raise AirootError(
                "PAYLOAD_MISSING",
                f"the fetched artifact is missing: {artifact_path}",
                evidence=[
                    "a resumed transaction rebuilds its artifact description from the fetch directory",
                    "the file is not there, so this run cannot verify or stage it",
                ],
            )
        return Artifact(
            path=artifact_path,
            digest=sha256_file(artifact_path),
            size=artifact_path.stat().st_size,
            fetched_from=locator,
        )

    def drive(self, tx: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        instance_id = str(tx["instance_id"])
        expected_source = plan["source"]["integrity"]["artifact_digest"]
        locator = plan["source"]["locator"]
        key = plan["target"].get("binding_key") or binding_key(plan["target"]["capability_id"], "machine")
        store_dir = from_root_relative(f"store/{instance_id}", self.root)
        fetch_dir = self._fetch_dir(str(tx["transaction_id"]))
        artifact_path = fetch_dir / Path(locator).name

        # A resumed run has no in-memory state, so `_artifact` -- which the fetch step of *this* process
        # would have set -- is still unset, while the states from FETCHED onward dereference it. Rebuild it
        # from the file the fetch step left behind; `getattr` because a fresh runner has no attribute at all.
        if getattr(self, "_artifact", None) is None and tx["state"] not in {"PROPOSED", "APPROVED"}:
            self._artifact = self._artifact_from_fetch_dir(artifact_path, locator)

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
                # The schema follows the kind (draft §150). The helper owns both names literally,
                # which is what keeps the standing writer census able to see them.
                validate_instance(instance.payload, kind=str(instance.kind))
                # Read *before* the row is written: this is the only moment at which "did this
                # transaction create the instance, or did it find one?" is still answerable (draft
                # §164). `tx/registration.py` owns that read for both runners, along with the
                # collected-latch correction that belongs to the same step.
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
                    # The write happens *before* the journal advances, and it is idempotent, so
                    # replaying this step after a crash changes no bytes.
                    from ..caps.launcher import write_launcher

                    write_launcher(self.root, str(plan["target"]["capability_id"]))
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
            return self._classified_failure(tx, error.reason_code, error.message, evidence=error.evidence)
        except OSError as error:
            # A filesystem refusal is not an `AirootError`, and until draft §62 it was not caught at
            # all: measured with the real backend, a full disk during `stage` and a locked/occupied
            # `store` path during `commit` both escaped `commit()` as raw `OSError`s. The caller got a
            # traceback instead of a diagnosis, the journal kept a **non-terminal** transaction that
            # the next `doctor` would report as pending recovery, and the old generation was left
            # un-reverted — for a failure that happened *after* `ACTIVE_BOUND`, that is a binding that
            # never got switched back.
            converted = AirootError(
                "INSTALL_IO_FAILED",
                f"the filesystem refused the {tx['state']} step",
                evidence=[
                    f"{type(error).__name__}: {error}",
                    f"errno={getattr(error, 'errno', None)} winerror={getattr(error, 'winerror', None)}",
                    f"transaction state when it failed: {tx['state']}",
                    "the request is fine; free the space, release the lock or fix the permission and re-plan",
                ],
            )
            return self._classified_failure(tx, converted.reason_code, converted.message, evidence=converted.evidence)

    def _classified_failure(
        self, tx: dict[str, Any], code: str, message: str, *, evidence: list[str] | None = None
    ) -> dict[str, Any]:
        """FAILED before the binding could move, ROLLED_BACK after — then honour `failure_cleanup`.

        The split is the existing rule (nothing to revert before ``COMMITTED``, everything after it);
        what is new in draft §62 is that a *filesystem* failure goes through it too, and that the
        backend's declared ``failure_cleanup`` is finally acted on. It was declared by every backend
        and read by nothing: `TransactionJournal.discard_stage` existed with no caller, so
        ``stage_only`` — the value both shipped backends publish — was a promise the core never kept.

        The cleanup runs **before** the failure is recorded so the fact goes into that record's
        evidence, rather than needing a new event state after the transaction is already terminal
        (the state machine allows no transition out of ROLLED_BACK, and inventing one to carry a
        cleanup flag would be the tail wagging the dog).
        """

        declared = str(getattr(getattr(self.backend, "declaration", None), "failure_cleanup", "manual"))
        cleaned = False
        if declared == "stage_only":
            cleaned = self.journal.discard_stage(str(tx["transaction_id"]))
        facts = list(evidence or [])
        facts.append(f"failure_cleanup={declared} stage_cleaned={cleaned}")

        # `STAGED` belongs with the states below `COMMITTED`, not with the states above it: the
        # `STAGED` step's whole job is the payload move, and §164 made that move refuse **before** it
        # happens when the store path is occupied. Nothing moved and nothing was bound, so there is
        # nothing to revert — and routing it to a rollback is what made a duplicate install bump the
        # generation and rewrite the live instance row to `broken`.
        if tx["state"] in {"PROPOSED", "APPROVED", "FETCHED", "VERIFIED", "STAGED"}:
            return self._fail(tx, code, message, evidence=facts)
        return self._rollback(tx, code, message, evidence=facts)

    def _ensure_stage(self, tx: dict[str, Any]) -> Path:
        """Idempotent staging: a resumed transaction never re-fetches or re-copies blindly."""

        stage_dir = self.journal.stage_path(str(tx["transaction_id"]))
        if not stage_dir.is_dir() or not any(item.is_file() for item in stage_dir.iterdir()):
            self.backend.stage(self._artifact, stage_dir=stage_dir)
        return stage_dir

    def _commit_stage(self, tx: dict[str, Any], store_dir: Path) -> None:
        """Move the staged payload into the store — or say who is already there (draft §164).

        The occupied-store refusal exists in the backend and stays there (`portable_file` and
        `portable_archive` both raise `INSTANCE_CONFLICT`). What this adds is **where** it is
        decided. Before §164 the runner only called ``backend.commit`` at the end of the ``STAGED``
        step and let that error escape, so the caller classified it by *state*: the failure became a
        rollback, bumped the generation, and — for a duplicate install of a version that was already
        active — rewrote the live instance row to ``broken``. Measured (F12): a byte-identical
        re-install of the active version exited 7 with ``ROLLED_BACK``, ``generation 2 -> 3``, the
        previously working instance marked ``broken/broken`` in both ``lifecycle_status`` and
        ``health``, and the capability moved back to the version the successful install had
        displaced.

        Asking first turns that into a **state** the attempt can report rather than a move that
        failed half-way: nothing is moved, nothing is bound, no generation is bumped, the transaction
        row is the only thing that changes, and the caller's existing pre-``COMMITTED`` classification
        reports it without a rollback. Guessing here would be worse than refusing: the store is
        append-only by construction (a new payload gets a new id), and "these bytes might be ours
        from a crashed attempt" cannot be told apart from "someone else holds this path" without
        trusting a backend-specific layout.
        """

        self._ensure_stage(tx)
        if store_dir.exists():
            raise AirootError(
                "INSTANCE_CONFLICT",
                f"the store path is already occupied: {store_dir}",
                evidence=[
                    "an existing payload is never overwritten; a new instance gets a new id",
                    "refused before the payload was moved, so no binding and no generation changed",
                ],
            )
        self.backend.commit(store_dir=store_dir, stage_dir=self.journal.stage_path(str(tx["transaction_id"])))
        self.journal.advance(tx, "COMMITTED", "payload moved into the immutable store")

    # ------------------------------------------------------------------ pieces #

    def _instance_document(self, plan: dict[str, Any], store_dir: Path) -> Instance:
        instance_id = plan["target"]["instance_id"]
        manifest = file_manifest(store_dir)
        payload_digest = tree_digest(store_dir)
        entrypoints = self.backend.expose(store_dir)
        payload = self._instance_payload(plan, payload_digest, entrypoints, manifest)
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

    def _instance_payload(
        self,
        plan: dict[str, Any],
        payload_digest: str,
        entrypoints: list[str],
        manifest: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The payload document for this plan's kind, built by the builder that owns that shape.

        Before draft §150 both runners built a **managed-tool** document unconditionally, so a
        `kind=runtime` plan produced a document the published `runtime-instance` schema does not
        describe — and since nothing ever *planned* a runtime, that schema had no writer at all.
        The kind now comes from the frozen capability list, and this is where it decides the shape.
        """

        capability_id = str(plan["target"]["capability_id"])
        common: dict[str, Any] = {
            "instance_id": str(plan["target"]["instance_id"]),
            "capability_id": capability_id,
            "version": str(plan["target"]["version"]),
            "install_backend_id": str(plan["metadata"]["backend_id"]),
            "artifact_digest": payload_digest,
            "store_path": f"store/{plan['target']['instance_id']}",
            "lifecycle_status": "installed",
            "health": "healthy",
            "entrypoints": entrypoints,
            "source": plan["source"],
            "file_manifest_digest": file_manifest_digest(manifest),
        }
        if str(plan["target"]["kind"]) == "runtime":
            return runtime_instance_payload(
                runtime_id=capability_id,
                runtime_family=runtime_family_for(capability_id),
                **common,
            )
        return managed_tool_payload(
            tool_id=capability_id,
            file_manifest=manifest,
            created_at=self.clock.timestamp(),
            **common,
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
        """Switch the active binding back; keep the new payload as evidence.

        The revert is the shared `tx/rollback.py` rule, not a copy of it: a rollback undoes its own
        activation and leaves a binding that a concurrent transaction committed after us alone
        (draft §109). This runner used to carry its own copy of the same three buggy lines.

        Two facts about *what the revert did* go into the failure record rather than being inferred
        from the final state (draft §164): which predecessor came back, or why none could, and
        whether the instance row was left alone. ``set_instance_status(..., "broken")`` is scoped to
        the rows this rollback may speak for, and the rule is stated in
        :meth:`_row_is_live_for_another_transaction`.
        """

        instance_id = str(tx["instance_id"])
        #: Whether this run inserted the instance row, read by the ``REGISTERED`` step *before* it
        #: wrote it. `None` here means the machine never reached that step — a resume may start at or
        #: past ``ACTIVE_BOUND`` — so it is only one of the two signals below, never a default.
        inserted_here = self._registered_payload

        def _row_is_live_for_another_transaction(reverted: Any) -> bool:
            """Whether the row is a working payload that belongs to a different transaction.

            This is the §164 rule, stated in facts the rollback can actually see. "Did this run
            register the row" cannot answer it: a transaction interrupted at or after
            ``ACTIVE_BOUND`` (T-010's loser) is resumed by a fresh runner that starts at that state,
            so ``REGISTERED`` never re-runs and the resumed run would answer "not ours" about the
            row its own earlier process created.

            Two facts do answer it. ``generation_after`` is non-null exactly when this transaction
            passed ``ACTIVE_BOUND`` — the one commit point — and the payload under this instance id
            is then necessarily its own. And the revert reports whether the key's binding came back
            to **this** instance. So a row that is not this transaction's own work, that no restore
            pointed at, and that still reads ``active`` **and** ``healthy``, belongs to another
            transaction: rewriting it would take a working version out of service to report a
            failure that was never about it, which is exactly what F12 measured on the
            duplicate-install path (the row was `active`/`healthy` one command earlier and came
            back `broken`/`broken` in both columns).
            """

            if str(tx.get("generation_after")) not in {"None", ""}:
                return False  # this transaction passed ACTIVE_BOUND: the row is its own work
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
            # The failure record and the revert share one SQLite transaction, so the evidence is
            # built by the code that did the work and written with it.
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
        self.journal.advance(
            tx,
            "ROLLED_BACK",
            "active binding switched back; new payload retained as evidence",
        )
        tx["outcome"] = code
        return tx


def source_kind_for(locator: str) -> str:
    """The ``source.kind`` a locator actually is (draft §170).

    ``kind`` describes the **source**, so it is read from the locator and not from the backend's
    network declaration. Until §170 it was ``"https" if declaration.network_access else "local_file"``,
    which is a statement about the *backend*: ``portable_archive`` declares ``network_access=true``
    because it can fetch over https, so planning an **offline** resolution with it produced one
    document that said ``kind="https"``, ``locator="C:\\...\\cmake-...zip"`` and
    ``metadata.source_catalog.offline=true`` at the same time.

    A local artifact is a filesystem path; the only network source this build has is an https URL
    (``caps/sources.py`` refuses everything else at resolve time, and ``https_artifact`` is HTTPS-only),
    so any other URL scheme is refused here rather than mislabelled.
    """

    scheme = urlsplit(locator).scheme.lower()
    if scheme == "https":
        return "https"
    if scheme and "://" in locator:
        raise AirootError(
            "INVALID_PLAN",
            f"the source locator is not an https URL: {locator}",
            evidence=[
                f"scheme={scheme!r}",
                "v1's only network source is https (draft §21), and a local artifact is a filesystem path",
                "plan a local artifact by naming its path, not a URL of another scheme",
            ],
        )
    return "local_file"


#: The published id shape (``common.schema.json#/$defs/id``), and the two rules a *fragment* of a
#: derived id has to satisfy. Only the fragment that **starts** the whole id has to begin with
#: `[a-z0-9]`; a later one follows a `/`, so `.cmake-3.31.6` is a perfectly legal middle piece.
#: Blaming a fragment that is not the culprit would be its own small lie, so the two rules are
#: separate here rather than one pattern applied to all three inputs.
_ID_SHAPE = r"^[a-z0-9][a-z0-9._/-]{0,127}$"
_ID_PATTERN = re.compile(_ID_SHAPE)
_FRAGMENT_TEXT = re.compile(r"^[a-z0-9._/-]+$")
_FRAGMENT_START = re.compile(r"^[a-z0-9]")


def derive_artifact_ids(
    *, capability_id: str, locator: str, version: str, plan_id: str | None = None
) -> tuple[str, str]:
    """The ids a real artifact plan carries, **checked where they are derived** (draft §171).

    An artifact's file name is not an id. The release archives this build's catalog admits happen to
    be id-shaped (``cmake-3.31.6-windows-x86_64``), which is why the derivation looked harmless — but
    a file named ``cmake-3.30.5-MISSING.zip`` yields an id with upper-case letters, and the plan then
    failed the **published shape of its own field**. The core reported that as
    ``SELF_VALIDATION_FAILED``, whose meaning is "this build produced a document it cannot read" — an
    implementation defect. It was not one: the caller's resolution is a legal document, and the
    illegal string was invented *here*, out of inputs a caller is free to choose (the artifact's file
    name and the version). So the refusal lives here instead, is ``INVALID_INPUT``(8) — the code for
    "your input cannot be used" — and names the input that could not become an id.

    ``SELF_VALIDATION_FAILED`` keeps its single meaning: a document *this build* produced is invalid.
    Nothing a caller can construct may reach it through this path any more, which is what the
    parametrized guard asserts.
    """

    stem = Path(locator).stem
    instance_id = f"{capability_id}/{stem}/{version}/win-x64"
    derived_plan_id = plan_id or f"plan/{capability_id}/{version}/{uuid.uuid4().hex[:12]}"
    inputs = (
        ("the capability id", capability_id),
        ("the artifact file name", stem),
        ("the version", version),
    )
    names = [("target.instance_id", instance_id)]
    if plan_id is None:
        # An explicit `plan_id` is the caller's own string, not something derived from these inputs.
        names.append(("plan_id", derived_plan_id))

    problems: list[str] = []
    for name, value in names:
        if _ID_PATTERN.match(value):
            continue
        blamed = [
            f"{label} {text!r}"
            for index, (label, text) in enumerate(inputs)
            if not _FRAGMENT_TEXT.match(text) or (index == 0 and not _FRAGMENT_START.match(text))
        ]
        if blamed:
            problems.append(f"{name}: {' and '.join(blamed)} cannot appear in an id")
        else:
            problems.append(
                f"{name}: the derived id is {len(value)} characters long, and the shape allows 128"
            )
    if problems:
        raise AirootError(
            "INVALID_INPUT",
            "this artifact cannot be planned: it does not make a valid instance id",
            evidence=[
                *problems,
                f"derived instance id: {instance_id}",
                f"expected shape: {_ID_SHAPE} (common.schema.json#/$defs/id)",
                "the id is derived from the artifact's file name, the version and the capability id, "
                "so one of them has to change: resolve a source whose artifact name is id-shaped "
                "(lower-case, no spaces) or plan a version that is",
            ],
        )
    return instance_id, derived_plan_id


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
    # The ids are derived from the artifact, so they are refused *here* when an input cannot make one
    # (draft §171): a plan that fails the shape of its own `target.instance_id` is the caller's
    # document being blamed for a name this function invented.
    instance_id, plan_id_derived = derive_artifact_ids(
        capability_id=capability_id, locator=locator, version=version, plan_id=plan_id
    )
    plan: dict[str, Any] = {
        "schema_version": 1,
        "plan_id": plan_id_derived,
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
            "kind": source_kind_for(locator),
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


__all__ = ["ArtifactRunner", "create_artifact_plan", "derive_artifact_ids", "source_kind_for", "store_relative"]
