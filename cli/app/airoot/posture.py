"""The build's security posture, in one place (draft §115, ADR-0043).

`common.schema.json#/$defs/securityMode` allows two values and `.../$defs/enforcement` allows two, so the
published schema admits **four** pairs of which only two are coherent: a build that is `policy_only` cannot
be `acl_enforced` (that claims an enforcement nothing implements) and a build that is `protected_machine`
cannot be `same_user_can_bypass` (that claims a protection a same-user process can walk around). Nothing in
the schema forbids the other two — JSON Schema cannot narrow a shared `$defs` entry into a cross-field rule
without rejecting documents that are valid today, and a published schema may not gain a rejecting
constraint inside `schema_version: 1` (AGENTS.md §7). So the coherence lives *here*, in one mapping, and a
guard requires this module to be the only place that mapping exists.

**This build is `policy_only`.** There is no elevated broker, no pipe ACL and no ACL enforcement; the
named pipe of draft §115 is docs/broker §2's *user compatibility mode*, and it must say so on every
response — the marking is a required field of `broker-response`, not a courtesy. The mapping's other
branch is kept because the schema enumerates the value, so the rule is total over the enum rather than
half-written with a comment promising the rest.
"""

from __future__ import annotations

from .exits import AirootError

#: What this build is. The only value any writer in this tree may emit.
SECURITY_MODE = "policy_only"

#: The mode-to-enforcement rule, total over the schema's `securityMode` enum. One mapping, because the
#: same conditional expression was written into `caps/doctor.py` and `ext/envelope.py`, and a third
#: writer (the broker's pipe) was about to restate it — three copies of one rule is how two of them
#: start disagreeing.
ENFORCEMENT_BY_MODE: dict[str, str] = {
    "policy_only": "same_user_can_bypass",
    "protected_machine": "acl_enforced",
}


def enforcement_for(security_mode: str) -> str:
    """The enforcement a mode implies. An unknown mode is refused, never defaulted.

    A default here would be the worst possible shape: whatever it defaulted to would be a *claim* about
    enforcement, made on behalf of a mode nobody recognises. `INVALID_INPUT` (exit 8) is the schema tier —
    the value is malformed input to whoever asked, exactly as an unregistered reason code is.
    """

    try:
        return ENFORCEMENT_BY_MODE[security_mode]
    except KeyError:
        raise AirootError(
            "INVALID_INPUT",
            f"unknown security_mode: {security_mode!r}",
            evidence=[
                f"security_mode must be one of: {', '.join(sorted(ENFORCEMENT_BY_MODE))}",
                "the published schema enumerates these two values, so anything else is malformed input "
                "rather than a mode this build has not met yet",
            ],
        ) from None


def posture() -> dict[str, str]:
    """The pair this build emits, ready to merge into a document.

    Returned as a fresh dict so a caller cannot mutate a shared one into a different claim.
    """

    return {"security_mode": SECURITY_MODE, "enforcement": enforcement_for(SECURITY_MODE)}
