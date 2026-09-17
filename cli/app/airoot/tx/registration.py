"""Registering an instance, once, for every runner (draft §164).

Both runners reach ``REGISTERED`` the same way, and §164 gave that step a second job: when the row
is already there, the payload that was just written back *is* the same payload, so the "collected by
an approved gc" latch has to go. ``tool verify`` refuses to verify a payload it believes is gone and
``tool status`` reports ``PAYLOAD_COLLECTED``, so leaving the latch set made the registry assert
something false about bytes sitting in ``store/``.

The rule lives here rather than in both runners because it is **one** rule. §109/ADR-0035 made the
same move for rollback after the two runners' copies drifted apart; a rule copied into two places is
a rule that will be right in one of them, and that is measurable rather than theoretical: a mutation
that removed the artifact runner's copy was invisible to the guard written for this defect, because
that guard drives the simulate runner.
"""

from __future__ import annotations

from typing import Any

__all__ = ["register_instance"]


def register_instance(
    registry: Any, connection: Any, instance: Any, *, source_digest: str | None = None
) -> bool:
    """Insert ``instance``, and clear the collected latch when the row was already there.

    Returns whether **this transaction** created the row. The row is read before it is written,
    which is the only moment at which "did we create this, or did we find it?" is still answerable:
    a resume that starts at or past ``REGISTERED`` never re-runs this step, so the caller's flag
    stays ``None`` rather than guessing (``tx/artifact.py`` says what that means for a rollback).
    """

    created_here = registry.instance(instance.instance_id) is None
    inserted = registry.add_instance(connection, instance, source_digest=source_digest)
    if not inserted:
        # The payload we just wrote back is the same one, so "collected" no longer describes it.
        registry.clear_collected(connection, instance.instance_id)
    return bool(inserted and created_here)
