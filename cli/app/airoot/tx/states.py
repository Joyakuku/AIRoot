"""The transaction state machine (v0.3 §14.1) as the single source of truth.

Unknown transitions are refused: the legal-move table below is transcribed from
the frozen document, and a cross-document test asserts that every state it lists
is representable in ``transaction.schema.json`` (which was missing ``ROLLED_BACK``
before ADR-0003).
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

TRANSITIONS: dict[str, tuple[str, ...]] = {
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

TERMINAL_STATES = frozenset({"FINALIZED", "EXPIRED"})

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
