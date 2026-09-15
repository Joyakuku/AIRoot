"""The transaction state machine (v0.3 §14.1) as the single source of truth.

Unknown transitions are refused: the legal-move table below is transcribed from
the frozen document, and a cross-document test asserts that every state it lists
is representable in ``transaction.schema.json`` (which was missing ``ROLLED_BACK``
before ADR-0003).

**How the table relates to §4.1 (ADR-0021).** §4.1 gives the happy path and then says that a
transaction may enter ``FAILED`` / ``EXPIRED`` / ``ROLLBACK_PENDING`` / ``ROLLED_BACK`` /
``RECOVERY_REQUIRED`` **from any stage**. The first transcription only implemented a subset of that,
which left the document and the table saying different things (draft §45.5 recorded the ambiguity,
because §40–§48's guards all check consistency and none of them could decide *which* of the two was
authoritative). ADR-0021 resolved it in the **wider** direction: the prose wins, and the extra edges
are generated from the rule below rather than hand-listed, so the intent stays visible.

Two boundaries survive the widening, and both come from stronger frozen rules than "any stage":

* ``FINALIZED`` keeps no outgoing edge — it is the successful end of the happy path, and §4.2 reads
  a terminal state as a dead end (the "terminal ⟺ dead end" invariant);
* ``EXPIRED`` is not enterable from a state that has already changed the active binding, because
  §4.1 calls ``EXPIRED`` terminal and "does not change the active binding", while §14.2 requires that
  a changed binding always keeps a rollback route. Allowing it would strand a committed binding in a
  state with no way back, which is not a looser *permission*, it is a lost recovery path.

Exception-to-exception edges are **not** widened: §4.1's "any stage" is about stages of the main
path, and the rollback/recovery ladder below is already the full set of moves between exception
states.
"""

from __future__ import annotations

from ..exits import AirootError

# Happy path, in order.
HAPPY_PATH: tuple[str, ...] = (
    "PROPOSED",
    "APPROVED",
    "FETCHED",
    "VERIFIED",
    "STAGED",
    "COMMITTED",
    "REGISTERED",
    "ACTIVE_BOUND",
    "EXPOSED",
    "VERIFIED_AGAIN",
    "FINALIZED",
)

EXCEPTION_STATES: tuple[str, ...] = (
    "FAILED",
    "EXPIRED",
    "ROLLBACK_PENDING",
    "ROLLED_BACK",
    "RECOVERY_REQUIRED",
)

DOCUMENTED_STATES: tuple[str, ...] = HAPPY_PATH + EXCEPTION_STATES

#: The dead ends. Defined here rather than next to the table because the widening rule below has to
#: know them: `FINALIZED` is a happy-path state, so without this check the "any stage" rule would
#: hand it outgoing edges and destroy the "terminal ⟺ dead end" invariant.
TERMINAL_STATES = frozenset({"FINALIZED", "EXPIRED"})

#: The five states §4.1 says a transaction may enter "from any stage" (任何阶段都可能进入).
ANY_STAGE_EXCEPTIONS: tuple[str, ...] = EXCEPTION_STATES

#: The happy-path states at which the active binding may already have changed. §14.2 makes
#: `ACTIVE_BOUND` the only commit point, and requires a changed binding to keep a rollback route —
#: so `EXPIRED`, which §4.1 defines as terminal and as not changing the binding, is not reachable
#: from here (see the module docstring).
BINDING_CHANGE_STATES = frozenset({"COMMITTED", "REGISTERED", "ACTIVE_BOUND", "EXPOSED", "VERIFIED_AGAIN"})

#: Happy path plus the exception moves that are *not* part of "any stage": the rollback/recovery
#: ladder between exception states, and the exception moves that were already transcribed.
_CORE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "PROPOSED": ("APPROVED", "FAILED"),
    "APPROVED": ("FETCHED", "FAILED", "EXPIRED"),
    "FETCHED": ("VERIFIED", "FAILED"),
    "VERIFIED": ("STAGED", "FAILED"),
    "STAGED": ("COMMITTED", "ROLLBACK_PENDING", "FAILED"),
    "COMMITTED": ("REGISTERED", "ROLLBACK_PENDING"),
    "REGISTERED": ("ACTIVE_BOUND", "ROLLBACK_PENDING"),
    "ACTIVE_BOUND": ("EXPOSED", "ROLLBACK_PENDING"),
    "EXPOSED": ("VERIFIED_AGAIN", "ROLLBACK_PENDING"),
    "VERIFIED_AGAIN": ("FINALIZED", "ROLLBACK_PENDING"),
    "ROLLBACK_PENDING": ("ROLLED_BACK", "RECOVERY_REQUIRED"),
    "FAILED": ("ROLLED_BACK", "RECOVERY_REQUIRED"),
    "ROLLED_BACK": ("FINALIZED",),
    "RECOVERY_REQUIRED": ("ROLLED_BACK", "FINALIZED"),
    "EXPIRED": (),
    "FINALIZED": (),
}


#: §4.1's terminal "the approval or plan lifetime ended" state. It is the one exception state the
#: widening holds back (see :data:`BINDING_CHANGE_STATES`).
EXPIRY_STATE = "EXPIRED"


def _widen(core: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Add §4.1's "any stage" exception edges to every non-terminal stage of the happy path.

    Generated rather than hand-listed so that the rule (and its two boundaries) is the thing a
    reader checks, instead of 33 individually transcribed edges.
    """

    widened: dict[str, tuple[str, ...]] = {}
    for state, targets in core.items():
        if state not in HAPPY_PATH or state in TERMINAL_STATES:
            widened[state] = targets
            continue
        extra = [
            target
            for target in ANY_STAGE_EXCEPTIONS
            if not (target == EXPIRY_STATE and state in BINDING_CHANGE_STATES)
        ]
        widened[state] = tuple(dict.fromkeys([*targets, *extra]))
    return widened


TRANSITIONS: dict[str, tuple[str, ...]] = _widen(_CORE_TRANSITIONS)

# States at or after which a store payload may already exist.
PAYLOAD_STATES = frozenset(
    {"COMMITTED", "REGISTERED", "ACTIVE_BOUND", "EXPOSED", "VERIFIED_AGAIN", "FINALIZED", "ROLLBACK_PENDING"}
)

# The only commit point allowed to change the active binding (v0.3 §14.2).
ACTIVE_BINDING_COMMIT_STATE = "ACTIVE_BOUND"


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def is_documented(state: str) -> bool:
    return state in DOCUMENTED_STATES


def allowed_next(state: str) -> tuple[str, ...]:
    return TRANSITIONS.get(state, ())


def can_transition(current: str, target: str) -> bool:
    return target in allowed_next(current)


def require_transition(current: str, target: str) -> None:
    if current not in TRANSITIONS:
        raise AirootError("ILLEGAL_TRANSITION", f"unknown transaction state: {current}")
    if not can_transition(current, target):
        raise AirootError(
            "ILLEGAL_TRANSITION",
            f"{current} -> {target} is not a legal transition",
            evidence=[f"allowed={','.join(allowed_next(current)) or 'none'}"],
        )


def next_happy_state(current: str) -> str | None:
    try:
        index = HAPPY_PATH.index(current)
    except ValueError:
        return None
    return HAPPY_PATH[index + 1] if index + 1 < len(HAPPY_PATH) else None
