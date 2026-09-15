"""Transaction journal: write-ahead state records plus recovery classification.

Every state change is persisted *before* the external effect it describes, in two
places: the authoritative SQLite row (a projection conforming to
``transaction.schema.json``) and the resumable JSON envelope in ``tx/<id>.json``.
The envelope additionally carries the plan and approval documents so a later
``repair`` can continue the transaction without re-deriving anything.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import TX_DIR
from ..canon import digest_bytes, canonical_bytes
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..schema_io import validate_document, validate_self
from .states import is_terminal, require_transition

RECOVERY_ACTIONS: dict[str, str] = {
    "PROPOSED": "resume_or_expire",
    "APPROVED": "resume_or_expire",
    "FETCHED": "discard_cache",
    "VERIFIED": "discard_cache",
    "STAGED": "cleanup_stage",
    "COMMITTED": "verify_store_then_resume_or_revert",
    "REGISTERED": "confirm_inactive_then_resume",
    "ACTIVE_BOUND": "reconcile_binding_then_resume_or_revert",
    "EXPOSED": "reverify_then_resume_or_revert",
    "VERIFIED_AGAIN": "finalize",
    "ROLLBACK_PENDING": "revert",
    "FAILED": "revert",
    "ROLLED_BACK": "no_action",
    "RECOVERY_REQUIRED": "require_repair",
}

NEVER_AUTOMATIC = frozenset({"RECOVERY_REQUIRED"})


@dataclass(frozen=True)
class RecoveryAction:
    """What recovery may do for one interrupted transaction (v0.3 §14.4)."""

    state: str
    action: str
    requires_explicit_repair: bool
    evidence: list[str] = field(default_factory=list)


def classify(tx: dict[str, Any]) -> RecoveryAction:
    state = str(tx["state"])
    if is_terminal(state):
        return RecoveryAction(state, "no_action", False, ["transaction already terminal"])
    action = RECOVERY_ACTIONS.get(state)
    if action is None:
        return RecoveryAction(
            state,
            "require_repair",
            True,
            [f"no documented recovery action for {state}"],
        )
    return RecoveryAction(
        state,
        action,
        state in NEVER_AUTOMATIC,
        [f"journal state {state}", f"journal_seq={tx.get('journal_seq')}"],
    )


class TransactionJournal:
    """Durable transaction records under ``<root>/tx``."""

    def __init__(self, registry: Any, *, clock: Clock = SYSTEM_CLOCK, injector: Any = None) -> None:
        self.registry = registry
        self.clock = clock
        self.injector = injector

    # ------------------------------------------------------------------ paths #

    @property
    def directory(self) -> Path:
        return Path(self.registry.path).parent.parent / TX_DIR

    def envelope_path(self, transaction_id: str) -> Path:
        return self.directory / f"{transaction_id.replace('/', '_')}.json"

    def stage_path(self, transaction_id: str) -> Path:
        return self.directory / transaction_id.replace("/", "_") / "stage"

    # ------------------------------------------------------------------ write #

    def create(
        self,
        plan: dict[str, Any],
        approval: dict[str, Any],
        *,
        instance_id: str | None,
        apply: Any = None,
    ) -> dict[str, Any]:
        transaction_id = f"tx/{plan['target']['capability_id']}/{_short_id(plan, approval)}"

        # **Get-or-create, never rewind** (draft §61). The id is derived from plan+approval, so a
        # second `commit` for the same plan and token lands on the same transaction — and it used to
        # overwrite the durable envelope and row with a fresh ``PROPOSED``, after which the caller
        # re-drove the whole state machine. Measured with fault injection, that produced one
        # transaction whose audit trail read `… ACTIVE_BOUND, PROPOSED, … FINALIZED`, i.e. **a second
        # pass through `ACTIVE_BOUND`** and a throwaway generation bump, and it erased the journal's
        # record that the binding had already changed. The journal is the recovery authority, so
        # rewriting it to an earlier state is not a retry, it is a false statement about the past.
        #
        # The second caller is already handled correctly one level up: it may only get here while the
        # approval is unconsumed (an unconsumed nonce is checked in `verify_approval`), so the honest
        # reading of "someone ran commit again" is *resume the transaction that is already underway*
        # — which is exactly what `resume` does from the same durable record.
        underway = self.registry.transaction(transaction_id)
        if underway is not None:
            return json.loads(underway["payload_json"])

        timestamp = self.clock.timestamp()
        tx: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "state": "PROPOSED",
            "root_instance_id": self.registry.root_instance_id,
            "machine_id": self.registry.machine_id,
            "instance_id": instance_id or plan["target"]["instance_id"],
            "generation_before": self.registry.generation,
            "generation_after": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "journal_seq": 0,
            "approval_id": approval.get("approval_id"),
            "approval_nonce": approval.get("nonce"),
            "failure": None,
        }
        validate_self("transaction", tx)
        with self.registry.write(expected_generation=self.registry.generation) as connection:
            if apply is not None:
                apply(connection)
            self._write_envelope(tx, plan, approval)
            self.registry.upsert_transaction(connection, tx)
            self.registry.append_event(
                connection,
                state="PROPOSED",
                detail="canonical plan accepted",
                transaction_id=transaction_id,
                plan_hash=tx["plan_hash"],
                approval_id=tx["approval_id"],
                after_state="PROPOSED",
                outcome="ok",
            )
        return tx

    def advance(
        self,
        tx: dict[str, Any],
        target_state: str,
        detail: str,
        *,
        failure: dict[str, Any] | None = None,
        generation_after: int | None = None,
        bump: bool = False,
        apply: Any = None,
    ) -> dict[str, Any]:
        """Persist the next state (write-ahead), then let the injector interrupt.

        ``apply`` runs inside the same SQLite transaction, which is how a state
        change that also mutates declared state (``REGISTERED``, ``ACTIVE_BOUND``)
        stays atomic with its journal record.
        """

        require_transition(str(tx["state"]), target_state)
        previous = str(tx["state"])
        with self.registry.write(expected_generation=self.registry.generation, bump=bump) as connection:
            if apply is not None:
                apply(connection)
            tx["state"] = target_state
            tx["journal_seq"] = int(tx["journal_seq"]) + 1
            tx["updated_at"] = self.clock.timestamp()
            if failure is not None:
                tx["failure"] = failure
            if generation_after is not None:
                tx["generation_after"] = generation_after
            elif bump:
                tx["generation_after"] = self.registry.generation + 1
            validate_self("transaction", tx)
            self._write_envelope(tx)
            self.registry.upsert_transaction(connection, tx)
            self.registry.append_event(
                connection,
                state=target_state,
                detail=detail,
                transaction_id=tx["transaction_id"],
                plan_hash=tx["plan_hash"],
                approval_id=tx.get("approval_id"),
                generation=tx.get("generation_after"),
                before_state=previous,
                after_state=target_state,
                reason_code=(failure or {}).get("code"),
                outcome="failed" if failure else "ok",
            )
        self.checkpoint(tx)
        return tx

    def _write_envelope(
        self,
        tx: dict[str, Any],
        plan: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> None:
        path = self.envelope_path(str(tx["transaction_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        envelope = {
            "transaction": tx,
            "plan": plan if plan is not None else existing.get("plan"),
            "approval": approval if approval is not None else existing.get("approval"),
        }
        path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------- read #

    def load_context(self, transaction_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        path = self.envelope_path(transaction_id)
        if not path.is_file():
            raise AirootError(
                "JOURNAL_TRUNCATED",
                f"transaction journal envelope missing: {path}",
                evidence=["recovery must not guess the missing steps"],
            )
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AirootError("JOURNAL_TRUNCATED", f"transaction journal unreadable: {path}", evidence=[str(exc)]) from exc
        tx = envelope.get("transaction")
        if not isinstance(tx, dict):
            raise AirootError("JOURNAL_TRUNCATED", f"transaction journal has no transaction record: {path}")
        validate_document("transaction", tx, reason_code="JOURNAL_TRUNCATED")
        return tx, envelope.get("plan"), envelope.get("approval")

    def unfinished(self) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.registry.transactions(unfinished_only=True)]

    def checkpoints(self) -> list[str]:
        return [str(row["state"]) for row in self.registry.events()]

    # --------------------------------------------------------------- injector #

    def checkpoint(self, tx: dict[str, Any]) -> None:
        if self.injector is not None:
            self.injector.checkpoint(str(tx["state"]))

    def discard_stage(self, transaction_id: str) -> bool:
        stage = self.stage_path(transaction_id).parent
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
            return True
        return False


def _short_id(plan: dict[str, Any], approval: dict[str, Any]) -> str:
    return digest_bytes(canonical_bytes({"plan": plan["plan_id"], "nonce": approval.get("nonce")}))[7:19]
