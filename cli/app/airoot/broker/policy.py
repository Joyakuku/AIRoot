"""The protected broker's first decision: who is asking, and may they ask at all (draft §114).

docs/broker §3:52 gives the server one job at the door — it "校验客户端进程 token、用户 SID、完整性级别
和 application identity" — and this module is the part of that job which decides rather than observes. It
takes the **observed** identity of the process on the other end and answers "may this caller ask at
all"; it runs before the operation is looked at, and it answers either with the observation it admitted
or with a refusal naming the single rule that stopped it.

Five negatives, because each is the nearest misreading of what this module is:

* **The caller's claim cannot reach the decision.** `broker-request`'s `client` block is a *claim*:
  `caps/identity.py` says so in its own docstring, and `broker/protocol.py` relays it without
  authenticating anything. So the decision's only caller-side input is a `ProcessIdentity` — the
  read-only observation the server makes of the peer's token — and this module never sees a request
  document. `ProcessIdentity` has no `application_id` field and must not gain one here: the moment the
  decision could read a claimed application id it would be deciding on a value the caller chose for
  itself. `admit_caller` takes two parameters and neither of them can carry a request.
* **Not the observation.** Nothing here opens a handle, reads a token or calls `probe_process`; the
  observation is `caps/identity.py`'s job. That is what makes this a pure function of two values, and
  testable — refusals included — without a token, a pipe or an elevated session.
* **Not the policy source.** Where `allowed_sids` and `minimum_integrity` come from is deliberately
  *not* decided at this stage (see the known boundary below), so they are explicit inputs and this
  module imposes no threshold of its own: no `from_root()` helper, no default minimum, no
  "empty allowed set means everyone".
* **Not a trust boundary in itself.** A green `admit_caller` says the observation was complete, the
  caller is not an AppContainer, its SID is one the expectation allows, its integrity is at or above
  the expectation's minimum, and its pid still names the process that was observed. It does not say
  the request is authentic, the plan approved, the path canonical or the operation permitted: the
  server revalidates all of that per operation (docs/broker §4), and no admission here may be read as
  having done any of it.
* **Not the last word.** This is the *first* decision. An admitted caller is one that may *ask*, not
  one whose request will be honoured.

The rules, in the order they are applied. The order is part of the contract rather than an accident,
because it decides which *one* rule a refusal names when several of them would fire, and a reader has
to be able to say which fact decided:

1. ``observation_incomplete`` — one of the facts this decision reads is ``None``. ``None`` is the
   probe's word for "nobody could look", and reading it as "looked, and it was fine" is the misreading
   that turns an unreadable token into an admitted caller.
2. ``app_container_caller`` — ``is_app_container is True``. Authority: docs/broker §2's table puts a
   capability extension and an install backend at 用户或**受限**进程 and forbids both from writing R or
   `store/tools/env/exposure/state`; an AppContainer token is the machine's own name for a restricted
   caller.
3. ``sid_not_allowed`` — the observed SID is not in ``expectation.allowed_sids``. This is the identity
   the caller cannot choose, which is why it is the one the decision compares.
4. ``integrity_below_minimum`` — the observed integrity is below the minimum on Windows' own ``>=``
   ladder over the ``SECURITY_MANDATORY_*_RID_BASE`` values. A word that is not one of the four cannot
   be placed on that ladder at all — on either side of the comparison — and an unorderable pair is
   refused rather than guessed at.
5. ``pid_reused`` — ``expectation.creation_time`` is set and the observation's differs, so the pid no
   longer names the process those facts were read from and they belong to a process nobody asked
   about.

``creation_time`` is the one conditional fact: it is required exactly when the expectation supplies
one, because a comparison needs both sides. Requiring it unconditionally would refuse every caller
whose creation time nobody read for an expectation that never asked for it — an invented requirement,
not a stronger check.

**Why the required set is wider than the four facts the refusal rules compare.** `REQUIRED_FACTS` is
the observation's whole account of the caller, and rule 1 reads it as one tuple. Two reasons, and the
second is the sharp one: `admit_caller` returns the observation so the caller's audit event can carry
the actor and the facts behind it (docs/broker §8), and a record that cannot name the caller's session
or its elevation *type* is one nobody can re-examine. `elevation_type` in particular is the only fact
that separates an administrator with UAC off (``default``) from a filtered administrator (``limited``)
— both read ``elevated=False``, which is the truth about the token and an incomplete truth about the
caller (`caps/identity.py`'s note on `TOKEN_ELEVATION_TYPE`). A decision that admitted a caller
without ever observing that difference could not later be asked to tell the two apart.

**Known boundary: this module has no policy source.** Nothing in this build produces an expectation.
There is no root declaration (nothing under `state/` names who may ask), no `policy/*.json` entry, and
no named-pipe DACL to derive the allowed set from — the protected server that would own all three does
not exist (ADR-0025's D1). So a caller has to be handed a `CallerExpectation`, and this module refuses
to invent one: no `from_root()`, no default minimum, no reading of the caller's claimed application id
to fill the gap. That gap is recorded here rather than papered over because the alternative — a door
that guesses its own threshold — is the invented certainty this project's honesty rules forbid
(AGENTS.md §8).

**Restating the integrity ladder, once, with a proof.** The four words and their order are Windows'
own (``caps/identity.py`` maps a token's integrity SID through the same ``>=`` ladder and exports no
public ordering), so `INTEGRITY_LADDER` restates them here, lowest first, and
`test_l1_broker_policy.py` holds the two definitions equal in both directions. A second ordering
nobody checks is how two modules come to disagree about the same machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from ..exits import AirootError

if TYPE_CHECKING:  # pragma: no cover - typing only: the decision does not import the observation
    from ..caps.identity import ProcessIdentity

__all__ = [
    "CallerExpectation",
    "INTEGRITY_LADDER",
    "REFUSAL_REASONS",
    "REQUIRED_FACTS",
    "admit_caller",
    "required_facts",
]

#: Windows' integrity ladder, **lowest first**: the same four words `caps/identity.py` derives from the
#: `SECURITY_MANDATORY_*_RID_BASE` values, restated as an ordering. The bases are ``low`` 0x1000,
#: ``medium`` 0x2000, ``high`` 0x3000, ``system`` 0x4000, and Windows compares a token's level with
#: ``>=`` against them — which is why this is a ladder and not an equality table, and why ``medium
#: plus`` (0x2100) reads as ``medium``.
INTEGRITY_LADDER: tuple[str, ...] = ("low", "medium", "high", "system")

#: Every fact this decision reads, unconditionally. `creation_time` is required **only** when the
#: expectation supplies one; see `required_facts`. The tuple is the observation's whole account of the
#: caller, not just the four facts the later rules compare — see the module docstring for why that is
#: deliberate rather than a restatement of someone else's completeness check.
REQUIRED_FACTS: tuple[str, ...] = (
    "sid",
    "integrity",
    "elevated",
    "elevation_type",
    "is_app_container",
    "session_id",
)

#: The fact that is required only when there is something to compare it against.
CREATION_TIME_FACT = "creation_time"

#: One line per refusal rule, so each check is a decision a reader can re-examine instead of a rule
#: whose only defence is that deleting it turns a test red — the same pattern `broker/protocol.py` uses
#: for `ADDITIONAL_REQUIREMENT_REASONS`. The keys are exactly the ids `admit_caller` can emit, and
#: `test_l1_broker_policy.py` derives that set from this module's own syntax tree rather than trusting
#: a second hand-written list to stay in step.
REFUSAL_REASONS: dict[str, str] = {
    "observation_incomplete": (
        "one of the facts this decision reads is None, which is the probe's word for `nobody could "
        "look` — reading it as `looked, and it was fine` is the one mistake that turns an unreadable "
        "token into an admitted caller"
    ),
    "app_container_caller": (
        "an AppContainer token is the machine's own name for a restricted caller, and docs/broker §2 "
        "gives a capability extension or an install backend — 用户或受限进程 — no way to write R or "
        "store/tools/env/exposure/state"
    ),
    "sid_not_allowed": (
        "the observed SID comes from the caller's own token, so it is the identity the caller cannot "
        "choose; a caller the expectation does not name may not ask, whatever its integrity level or "
        "whatever its request's `client` block claims"
    ),
    "integrity_below_minimum": (
        "Windows compares integrity levels with `>=` over the SECURITY_MANDATORY_*_RID_BASE ladder, so "
        "a level below the expectation's minimum is a caller the operation was not opened to — and a "
        "word outside low/medium/high/system, on either side, is a comparison that cannot be made at "
        "all, which is a refusal and never an admission"
    ),
    "pid_reused": (
        "the pid no longer names the process the observation was made of, so every fact in that "
        "observation may belong to a later process that nobody asked about"
    ),
}


@dataclass(frozen=True)
class CallerExpectation:
    """Who may ask, as an explicit input — the decision does not know where it came from.

    ``allowed_sids`` and ``minimum_integrity`` are the whole policy: an empty set allows nobody (it
    does not mean "no restriction"), and the minimum is compared on `INTEGRITY_LADDER`. ``None`` for
    ``creation_time`` means "nobody asked for the pid to be held to a particular process", not "the
    creation time is zero". ``evidence`` is the expectation's own provenance, carried into every
    refusal so a reader sees the policy side of the comparison as well as the observation side; this
    module never fills it, because where an expectation comes from is precisely what is not decided
    here (see the module docstring's known boundary).
    """

    allowed_sids: frozenset[str]
    minimum_integrity: str
    creation_time: int | None = None
    evidence: tuple[str, ...] = ()


def required_facts(expectation: CallerExpectation) -> tuple[str, ...]:
    """Every fact ``admit_caller`` must find observed, for this expectation.

    One definition, so a caller — and the future server that has to decide whether to ask at all —
    can ask what the decision demands instead of re-deriving it from the rules.
    """

    if expectation.creation_time is None:
        return REQUIRED_FACTS
    return REQUIRED_FACTS + (CREATION_TIME_FACT,)


def admit_caller(identity: ProcessIdentity, expectation: CallerExpectation) -> ProcessIdentity:
    """Admit ``identity``, or refuse with `CALLER_NOT_AUTHORIZED` (exit 5) naming the rule that fired.

    Returns the observation itself, so the caller's audit event carries the actor's SID and the facts
    the decision was made on (docs/broker §8) — not a verdict detached from what it was a verdict
    about.

    ``identity`` is the **observed** `caps/identity.py`'s `ProcessIdentity`, and the only caller-side
    input there is: a `broker-request`'s `client` block is a claim and cannot be passed here, which is
    a property of the signature rather than a promise in prose. The refusal carries
    ``details={"rule": <id>}`` — one of `REFUSAL_REASONS`' keys — plus evidence lines: the rule, why
    it fired, the observation's own account of what the probe saw, and the expectation's provenance.

    Raises nothing else: an unreadable fact, an AppContainer caller, a foreign SID, an integrity word
    off the ladder and a reused pid are all the same refusal with a different rule, because they are
    all the same answer to "may this caller ask" — no.
    """

    # Rule 1. `getattr(..., None)` on purpose: the names are `ProcessIdentity`'s fields (a guard test
    # measures that), and if one is ever renamed this refuses with `observation_incomplete` naming the
    # fact rather than dying with an `AttributeError` inside a broker whose whole job is to answer.
    missing = [name for name in required_facts(expectation) if getattr(identity, name, None) is None]
    if missing:
        _refuse(
            "observation_incomplete",
            f"the caller's identity was not fully observed: {', '.join(missing)}",
            [
                f"{name}: None, which is `nobody could look` and not `looked, and it was fine`"
                for name in missing
            ],
            identity,
            expectation,
        )

    # Rule 2. `is True` and not a truthiness test: `None` (nobody asked) is refused one rule earlier,
    # and `False` is the token answering — the two must not collapse into each other here.
    if identity.is_app_container is True:
        _refuse(
            "app_container_caller",
            "the caller is an AppContainer process, which docs/broker §2 places among the restricted "
            "callers that may not write R or store/tools/env/exposure/state",
            [
                "is_app_container is True: the token carries an application identity, and the "
                "machine's own name for that restriction is the authority, not the request",
                "docs/broker §2: a capability extension and an install backend are 用户或受限进程, "
                "and neither may write R, store, tools, env, exposure or state",
            ],
            identity,
            expectation,
        )

    # Rule 3. The membership test is the whole rule: the allowed set is not echoed into the evidence,
    # because it is this machine's configured identity list and a refusal travels back to a caller —
    # the same reason `cli/tests/fake_broker.py`'s `probe_root` reports its observed trustees by count.
    if identity.sid not in expectation.allowed_sids:
        _refuse(
            "sid_not_allowed",
            f"the observed SID {identity.sid} is not one this broker serves",
            [
                f"the observed SID {identity.sid} is not among the "
                f"{len(expectation.allowed_sids)} SID(s) in allowed_sids",
                "the SID is read from the caller's own token, so it is the identity the caller cannot "
                "choose — unlike the `client` block, which is whatever the request says",
            ],
            identity,
            expectation,
        )

    # Rule 4, first half: unorderable words. Both sides are checked, because either one being off the
    # ladder leaves the comparison with no answer, and "no answer" is a refusal (AGENTS.md §8: do not
    # fabricate certainty). `caps/identity.py` never emits a fifth word, so this is the guard against
    # an expectation written by hand and against a future probe widening the vocabulary.
    unorderable = [
        (label, word)
        for label, word in (
            ("the observed integrity", identity.integrity),
            ("minimum_integrity", expectation.minimum_integrity),
        )
        if _integrity_rank(word) is None
    ]
    if unorderable:
        _refuse(
            "integrity_below_minimum",
            "an integrity word outside the ladder cannot be compared: "
            + ", ".join(f"{label} is {word!r}" for label, word in unorderable),
            [
                f"{label} {word!r} is not one of {', '.join(INTEGRITY_LADDER)}, so it cannot be "
                "placed on Windows' `>=` ladder and no ordering between the two words exists"
                for label, word in unorderable
            ]
            + [f"the ladder, lowest first: {', '.join(INTEGRITY_LADDER)}"],
            identity,
            expectation,
        )
    if _integrity_rank(identity.integrity) < _integrity_rank(expectation.minimum_integrity):
        _refuse(
            "integrity_below_minimum",
            f"the caller's integrity level {identity.integrity} is below the minimum "
            f"{expectation.minimum_integrity}",
            [
                f"{identity.integrity} < {expectation.minimum_integrity} on Windows' own `>=` ladder "
                "over SECURITY_MANDATORY_*_RID_BASE",
                f"the ladder, lowest first: {', '.join(INTEGRITY_LADDER)}",
            ],
            identity,
            expectation,
        )

    # Rule 5. Last, because it is about whether the *observation* is still about this process at all —
    # a fact that only matters once the observation is otherwise good enough to admit.
    if (
        expectation.creation_time is not None
        and identity.creation_time != expectation.creation_time
    ):
        _refuse(
            "pid_reused",
            f"pid {identity.pid} no longer names the process this observation was made of",
            [
                f"the observation's creation_time is {identity.creation_time}, the expectation's is "
                f"{expectation.creation_time}",
                "a pid is reused, so these facts may belong to a later process that nobody asked "
                "about",
            ],
            identity,
            expectation,
        )

    return identity


def _integrity_rank(word: str) -> int | None:
    """Where ``word`` sits on `INTEGRITY_LADDER`, or ``None`` when it is not one of the four.

    An index rather than a number of its own: the comparison is the ladder's order, and a second
    mapping from words to values is exactly how two modules start disagreeing about one machine.
    """

    try:
        return INTEGRITY_LADDER.index(word)
    except ValueError:
        return None


def _refuse(
    rule: str,
    message: str,
    evidence: list[str],
    identity: ProcessIdentity,
    expectation: CallerExpectation,
) -> NoReturn:
    """Raise the one refusal shape this module produces. The only place `admit_caller` refuses.

    One site, so the reason code, the `details["rule"]` id and the evidence layout cannot drift
    between rules — and so a guard can derive the emittable rule ids from the call sites themselves.
    Every refusal carries both sides of the comparison: the rule and why it fired, the observation's
    own lines (what the probe actually saw, or why it could not), and the expectation's provenance.

    The evidence therefore names real SIDs at runtime. That is the point — a refusal that hid which
    identity was compared could not be re-examined — but it is also why one of these refusals must
    never be turned into a committed golden fixture (AGENTS.md §9).
    """

    raise AirootError(
        "CALLER_NOT_AUTHORIZED",
        message,
        evidence=[
            f"refusal rule: {rule}",
            *evidence,
            *(f"observed: {line}" for line in identity.evidence),
            *(f"expectation: {line}" for line in expectation.evidence),
        ],
        details={"rule": rule},
    )
