"""Undoing a transaction's **own** activation, once, for every runner (draft §109, §164).

``ACTIVE_BOUND`` is the only commit point that may change the active binding, so a rollback has
exactly one thing to undo: the activation *this* transaction performed. Two runners drive that
same state machine (``simulate.py`` for the fake fixture, ``artifact.py`` for a real artifact) and
both carried their own three-line copy of the revert — which is how the two defects below stayed
invisible: each copy was read on its own, and neither was read against the other.

* **The deactivation was key-wide.** ``clear_active_binding(key)`` deactivates *every* active row
  for the key, not ours. A concurrent transaction that legitimately committed after us (its
  ``bind_active`` deactivates ours, T-010: the DB lock *"guarantees one commit, the other retries
  or exits"* — verification plan:206) had its own binding taken away by our rollback, leaving the
  key with **zero** active bindings while its transaction was ``FINALIZED``.
* **The restore used the wrong generation.** ``tx["generation_before"]`` is the **registry-wide**
  generation captured by ``journal.create`` (``journal.py:134``), not this key's previous row. As
  soon as another key was bound in between, ``(key, generation_before)`` named no row at all, and
  the rollback silently restored nothing.

The rules below are the contract, not an implementation choice:

1. the key has no active row -> nothing here is ours to undo (``retire``/``uninstall`` own that
   state now);
2. the active row belongs to another instance -> that transaction committed after us; we report
   our own failure and leave the winner alone;
3. the active row is ours -> deactivate **only our row**, then re-activate the key's newest row
   below ours **whose payload is still a candidate**: not collected by an approved ``gc``, not
   retired, and its store directory still on disk. If there is none, the key correctly ends with
   no active binding, because there was nothing *valid* before us.

Rule 3's candidate filter is the §164 correction, and it is a data-integrity rule rather than a
preference. The previous form picked ``MAX(generation) WHERE generation < ours`` — a pure
generation query with no predicate on ``active``, ``lifecycle_status``, ``retired_at`` or
``collected_at`` and no check that the payload still existed. Measured (F12): after
``retire 9.9.10`` -> ``gc --apply`` -> re-install 9.9.10 -> a failing re-install, the rollback
restored the binding to the generation whose payload ``gc --apply`` had already deleted, and the
capability then answered ``where archive`` with exit 3 / ``BROKEN`` / ``MANAGED_NOT_HEALTHY``
instead of the honest ``NOT_FOUND``. A binding to a payload that is gone is worse than no binding:
it turns "nothing here" into "something here is broken".

This module still does not invent a predecessor. It restores the **newest valid** row below ours,
which is not necessarily the row that was active when our transaction started: a predecessor that
had been deactivated on purpose (``retire``) is skipped, and a predecessor that a concurrent
transaction displaced may be chosen. Recording the exact displaced row needs a field
``transaction.schema.json`` does not have (``additionalProperties: false``), and adding one is a
contract change, not a bug fix. What the rollback chose, skipped and why is therefore reported
through :class:`RevertOutcome` and lands in the failure evidence the caller writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import from_root_relative
from ..registry.entities import is_store_path

__all__ = ["RevertOutcome", "revert_own_activation"]


@dataclass
class RevertOutcome:
    """What the revert did, and what it refused to do.

    ``restored_generation`` is ``None`` whenever the key ends with no active binding — either
    because rule 1/2 applied (``restored is None`` and the key was never ours to touch) or because
    every candidate below ours was rejected (``rejected`` is then non-empty and ``explanation``
    says so). The two are different facts and the caller's evidence distinguishes them.
    """

    key: str | None
    owned_binding: bool = False
    restored_instance_id: str | None = None
    restored_generation: int | None = None
    rejected: list[str] = field(default_factory=list)
    explanation: str = ""

    def evidence(self) -> list[str]:
        """Plain sentences for the failure record, in the order a reader needs them."""

        lines: list[str] = []
        if self.key is None:
            return ["no binding row names this instance, so there was no activation of ours to undo"]
        lines.append(f"rollback of binding key {self.key}")
        if not self.owned_binding:
            lines.append(
                "this transaction's instance does not hold the active binding, so no active binding "
                "was changed"
            )
        if self.restored_generation is not None:
            lines.append(
                f"active binding restored to generation {self.restored_generation} "
                f"({self.restored_instance_id})"
            )
        lines.extend(self.rejected)
        if self.explanation:
            lines.append(self.explanation)
        return lines


def _binding_key_of(registry: Any, instance_id: str) -> str | None:
    """The binding key this instance is registered under.

    The transaction document does not carry the key (``plan.target.binding_key`` is not copied into
    it), so the key is read back from the bindings table — the same lookup both runners used.
    """

    for row in registry.bindings():
        if str(row["instance_id"]) == str(instance_id):
            return str(row["binding_key"])
    return None


def _payload_present(registry: Any, row: Any) -> bool:
    """Whether the instance's payload is still on disk, worked out the same way callers do.

    A store path that cannot be made root-relative (a corrupted or foreign value) counts as
    absent: restoring it would point the capability at a path this process will not use.
    """

    store_path = str(row["store_path"] or "")
    if not is_store_path(store_path):
        return False
    try:
        store_dir = from_root_relative(store_path, Path(registry.path).parent.parent)
    except Exception:  # noqa: BLE001 - any refusal to resolve means "not a usable payload"
        return False
    return store_dir.is_dir()


def _unusable_reason(registry: Any, row: Any) -> str | None:
    """Why this predecessor must not be re-activated, or ``None`` when it is a legal one."""

    instance_id = str(row["instance_id"])
    if row["collected_at"]:
        return (
            f"predecessor generation {int(row['generation'])} ({instance_id}) was skipped: "
            f"its payload was collected by an approved gc at {row['collected_at']}"
        )
    if row["retired_at"] is not None or str(row["lifecycle_status"]) == "retired":
        return (
            f"predecessor generation {int(row['generation'])} ({instance_id}) was skipped: "
            "it was retired on purpose, so it is not a state to go back to"
        )
    if not _payload_present(registry, row):
        return (
            f"predecessor generation {int(row['generation'])} ({instance_id}) was skipped: "
            f"its payload is not on disk ({row['store_path']})"
        )
    return None


def revert_own_activation(registry: Any, connection: Any, *, instance_id: str) -> RevertOutcome:
    """Undo this transaction's activation of its key; report what was restored and what was skipped.

    Runs inside the caller's SQLite write transaction (``journal.advance(apply=...)``), so the read
    of the active row and the write that replaces it are one atomic step. The caller still marks
    its instance ``broken`` afterwards: the payload is kept as evidence.
    """

    key = _binding_key_of(registry, instance_id)
    if key is None:
        return RevertOutcome(key=None)

    active = connection.execute(
        "SELECT generation, instance_id FROM bindings WHERE binding_key = ? AND active = 1",
        (key,),
    ).fetchall()
    if not active:
        # Rule 1: the key has no active row. Something other than a commit owns that state.
        return RevertOutcome(key=key)

    ours = [int(row["generation"]) for row in active if str(row["instance_id"]) == str(instance_id)]
    if not ours:
        # Rule 2: another instance is active. It committed after us; our rollback does not fight it.
        return RevertOutcome(key=key)

    ours_generation = max(ours)
    connection.execute(
        "UPDATE bindings SET active = 0 WHERE binding_key = ? AND instance_id = ? AND active = 1",
        (key, str(instance_id)),
    )
    outcome = RevertOutcome(key=key, owned_binding=True)

    # Newest first: the candidate the old rule would have chosen is still chosen whenever it is
    # still a candidate, so this narrows the rule instead of replacing it.
    candidates = connection.execute(
        """
        SELECT b.generation, b.instance_id, i.lifecycle_status, i.retired_at, i.collected_at,
               i.store_path
          FROM bindings b
          JOIN instances i ON i.instance_id = b.instance_id
         WHERE b.binding_key = ? AND b.generation < ?
         ORDER BY b.generation DESC
        """,
        (key, ours_generation),
    ).fetchall()

    for row in candidates:
        reason = _unusable_reason(registry, row)
        if reason is not None:
            outcome.rejected.append(reason)
            continue
        connection.execute(
            "UPDATE bindings SET active = 1 WHERE binding_key = ? AND generation = ?",
            (key, int(row["generation"])),
        )
        outcome.restored_instance_id = str(row["instance_id"])
        outcome.restored_generation = int(row["generation"])
        return outcome

    # Rule 3 with nothing valid before us: no active binding is the honest end state. It is not
    # invented from a row that no longer stands.
    if candidates:
        outcome.explanation = (
            f"no predecessor of binding key {key} below generation {ours_generation} is still a "
            "candidate, so the key was left with no active binding rather than restored to a "
            "payload that is collected, retired or gone"
        )
    else:
        outcome.explanation = (
            f"binding key {key} has no row below generation {ours_generation}, so there was "
            "nothing before us to restore"
        )
    return outcome
