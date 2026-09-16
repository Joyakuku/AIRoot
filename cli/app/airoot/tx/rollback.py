"""Undoing a transaction's **own** activation, once, for every runner (draft §109).

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
3. the active row is ours -> deactivate **only our row**, then re-activate the key's row with the
   greatest generation strictly below ours (the binding we displaced). If there is none, the key
   correctly ends with no active binding, because there was nothing before us.

Known limit, recorded rather than papered over: rule 3 re-activates the *newest row* below ours,
not necessarily the row that was active when our transaction started. A predecessor that had been
deactivated on purpose (``retire``) before our commit is therefore re-activated. Recording the
exact displaced row needs a field ``transaction.schema.json`` does not have, and adding one is a
contract change, not a bug fix.
"""

from __future__ import annotations

from typing import Any

__all__ = ["revert_own_activation"]


def _binding_key_of(registry: Any, instance_id: str) -> str | None:
    """The binding key this instance is registered under.

    The transaction document does not carry the key (``plan.target.binding_key`` is not copied into
    it), so the key is read back from the bindings table — the same lookup both runners used.
    """

    for row in registry.bindings():
        if str(row["instance_id"]) == str(instance_id):
            return str(row["binding_key"])
    return None


def revert_own_activation(registry: Any, connection: Any, *, instance_id: str) -> int | None:
    """Undo this transaction's activation of its key; return the restored generation, if any.

    Runs inside the caller's SQLite write transaction (``journal.advance(apply=...)``), so the read
    of the active row and the write that replaces it are one atomic step. The caller still marks
    its instance ``broken`` afterwards: the payload is kept as evidence.
    """

    key = _binding_key_of(registry, instance_id)
    if key is None:
        return None

    active = connection.execute(
        "SELECT generation, instance_id FROM bindings WHERE binding_key = ? AND active = 1",
        (key,),
    ).fetchall()
    if not active:
        # Rule 1: the key has no active row. Something other than a commit owns that state.
        return None

    ours = [int(row["generation"]) for row in active if str(row["instance_id"]) == str(instance_id)]
    if not ours:
        # Rule 2: another instance is active. It committed after us; our rollback does not fight it.
        return None

    ours_generation = max(ours)
    connection.execute(
        "UPDATE bindings SET active = 0 WHERE binding_key = ? AND instance_id = ? AND active = 1",
        (key, str(instance_id)),
    )
    row = connection.execute(
        "SELECT MAX(generation) FROM bindings WHERE binding_key = ? AND generation < ?",
        (key, ours_generation),
    ).fetchone()
    predecessor = None if row is None else row[0]
    if predecessor is None:
        # Rule 3, nothing before us: no active binding is the honest end state.
        return None
    connection.execute(
        "UPDATE bindings SET active = 1 WHERE binding_key = ? AND generation = ?",
        (key, int(predecessor)),
    )
    return int(predecessor)
