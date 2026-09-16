"""L1: the protected broker's first decision — who is asking, and may they ask at all (draft §114).

`broker/policy.py` is the server's side of the door and this file is its acceptance face. Five things
are pinned here, and each of them is a different way for the decision to be wrong:

* **the rules, one at a time** — every refusal id, the fact that decides it, and the order they are
  applied in, because a refusal names *one* rule and a reader has to know which fact decided;
* **"unknown" is not "fine"** — the property that matters most, parametrised over every fact the
  decision reads: an observation missing that one fact is refused, and refused *because of* that fact
  (the evidence names it and nothing else is reported missing);
* **the ladder is Windows' own** — `policy.INTEGRITY_LADDER` is restated rather than imported, so this
  file holds the two definitions equal in both directions against `caps/identity.py`'s table;
* **the claim cannot reach the decision** — measured structurally (no `application` field on the
  observation, no request-shaped parameter) and behaviourally (a request whose `client` block claims an
  allowed SID, a high integrity and an application id is still refused for the identity that was
  actually observed);
* **the table is the rules** — `REFUSAL_REASONS`' keys are derived from this module's own syntax tree,
  so a rule cannot be added to the code without a reason and a reason cannot outlive its rule.

Only one test observes anything real (`probe_process(os.getpid())`), and it is the one whose subject is
the machine's own answer. Everywhere else the identity is injected, because the decision is a pure
function of two values and a synthetic caller can be made exactly as complete — or as broken — as each
case needs, on a host that has no broker, no pipe and no privilege at all.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import pathlib

import pytest

from airoot.broker import policy, protocol
from airoot.caps import identity
from airoot.exits import AirootError

#: A SID that could be allowed, and one that cannot be: both satisfy the published schema's `sid`
#: pattern and neither is a real account on any machine (AGENTS.md §9 — no host fingerprints here).
SID_ALLOWED = "S-1-5-21-1000-1000-1000-1001"
SID_OTHER = "S-1-5-21-2000-2000-2000-2002"

#: A raw `FILETIME` integer. The value is arbitrary; what matters is that `pid_reused` compares two
#: of them rather than trusting the pid (a pid is reused, a creation time is not).
CREATION_TIME = 133_500_000_000_000_000

#: Parameter names that would mean a request document, or a piece of one, had reached the decision.
CLAIM_SHAPED = frozenset({"client", "claim", "request", "application_id"})

#: The required set, written out **by hand**, and the only place in this file that writes it.
#:
#: Deliberate, and measured (draft §114): the property test below used to be parametrised over
#: `policy.REQUIRED_FACTS` itself, so deleting a fact from the tuple also deleted its own test case —
#: `elevation_type` was removed from the module and the whole suite stayed green. A set that can shrink
#: without anything noticing is not a contract, so the contract is restated here where a deletion has
#: to argue with a reader instead of with a loop that follows it.
REQUIRED_FACTS_EXPECTED = (
    "sid",
    "integrity",
    "elevated",
    "elevation_type",
    "is_app_container",
    "session_id",
)


def observed(**overrides) -> identity.ProcessIdentity:
    """A complete observation, with any fact overridden — the shape every case starts from.

    ``app_container_sid=None`` is included on purpose: for an ordinary process that is the token's
    *answer*, not a fact nobody read (`caps/identity.py` says so), and the decision must not require it.
    """

    facts: dict = {
        "pid": 4242,
        "sid": SID_ALLOWED,
        "integrity": "medium",
        "elevated": False,
        "evidence": ("injected: a complete observation",),
        "elevation_type": "limited",
        "session_id": 2,
        "is_app_container": False,
        "app_container_sid": None,
        "creation_time": CREATION_TIME,
    }
    facts.update(overrides)
    return identity.ProcessIdentity(**facts)


def expectation(**overrides) -> policy.CallerExpectation:
    """Who may ask, as an explicit input. No `creation_time` unless a case asks for one."""

    terms: dict = {"allowed_sids": frozenset({SID_ALLOWED}), "minimum_integrity": "medium"}
    terms.update(overrides)
    return policy.CallerExpectation(**terms)


def refusal(subject: identity.ProcessIdentity, terms: policy.CallerExpectation) -> AirootError:
    with pytest.raises(AirootError) as failure:
        policy.admit_caller(subject, terms)
    return failure.value


def assert_refused(
    subject: identity.ProcessIdentity, terms: policy.CallerExpectation, rule: str
) -> AirootError:
    """The refusal contract every rule shares: the code, the exit, the rule id, and the rule's line."""

    error = refusal(subject, terms)
    assert error.reason_code == "CALLER_NOT_AUTHORIZED"
    assert error.exit_code == 5
    assert error.details == {"rule": rule}
    assert any(line.startswith(f"refusal rule: {rule}") for line in error.evidence), error.evidence
    return error


def missing_report(error: AirootError) -> list[str]:
    """The evidence lines that report a fact as unobserved, in the order the decision wrote them."""

    return [line for line in error.evidence if ": None, which is" in line]


# --------------------------------------------------------------------------------------------- #
# admission, and the one rule that is not a refusal
# --------------------------------------------------------------------------------------------- #


def test_a_complete_admitted_identity_is_returned_as_the_actor_it_was_admitted_as() -> None:
    """`admit_caller` answers with the observation, not a detached verdict: the audit event needs the
    actor SID and the facts the decision was made on (docs/broker §8)."""

    subject = observed()

    admitted = policy.admit_caller(subject, expectation())

    assert admitted is subject, "the admitted observation must be the one that was observed"
    assert admitted.sid == SID_ALLOWED
    assert admitted.integrity == "medium"
    assert admitted.is_app_container is False
    assert admitted.elevation_type == "limited"


def test_a_refusal_carries_both_sides_of_the_comparison() -> None:
    """The observation's own lines and the expectation's provenance, so a reader can re-examine both.

    The observation is where "why is this fact unknown" lives, and the expectation is where the policy
    came from; a refusal that carried neither would be a verdict nobody could check.
    """

    subject = observed(sid=SID_OTHER, evidence=("TokenUser -> <the injected SID>",))
    terms = expectation(evidence=("pipe DACL: the allowed set comes from whoever carries the bytes",))

    error = assert_refused(subject, terms, rule="sid_not_allowed")

    assert any(line == "observed: TokenUser -> <the injected SID>" for line in error.evidence)
    assert any(line.startswith("expectation: pipe DACL:") for line in error.evidence)


def test_every_refusal_is_the_same_code_and_exit() -> None:
    """Five rules, one answer to "may this caller ask": no. The rule id is the only difference.

    Exit 5 is the *tier* (the caller lacks an authority the operation needs), and the resemblance to
    `PRIVILEGE_REQUIRED` stops there — running elevated does not change a SID — which is why the rule
    id and the evidence have to say which authority was missing.
    """

    cases = [
        (observed(sid=None), expectation()),
        (observed(is_app_container=True), expectation()),
        (observed(sid=SID_OTHER), expectation()),
        (observed(integrity="low"), expectation()),
        (observed(), expectation(creation_time=CREATION_TIME - 1)),
    ]

    for subject, terms in cases:
        error = refusal(subject, terms)
        assert error.reason_code == "CALLER_NOT_AUTHORIZED"
        assert error.exit_code == 5
        assert set(error.details) == {"rule"}, error.details


# --------------------------------------------------------------------------------------------- #
# each rule, and the fact each one decides on
# --------------------------------------------------------------------------------------------- #


def test_an_app_container_caller_is_refused() -> None:
    """docs/broker §2 gives a restricted process no way to write R; the token names the restriction."""

    error = assert_refused(observed(is_app_container=True), expectation(), rule="app_container_caller")

    assert error.message.startswith("the caller is an AppContainer process")
    assert any("is_app_container is True" in line for line in error.evidence), error.evidence
    assert any("docs/broker" in line for line in error.evidence), error.evidence


@pytest.mark.parametrize(
    "elevated, elevation_type",
    [
        (False, "limited"),  # a filtered administrator: the ordinary UAC case
        (True, "full"),  # an elevated administrator
        (True, "default"),  # an administrator with UAC off — measured on this box (draft §114)
        (False, "default"),  # a standard user: `TokenElevationTypeDefault`, nothing to elevate
    ],
)
def test_elevated_and_elevation_type_are_independent_observed_facts(
    elevated: bool, elevation_type: str
) -> None:
    """Both are *read*; neither is re-checked against the other, and it must stay that way.

    `TokenElevation` alone cannot tell a standard user from an administrator with UAC disabled — both
    report ``elevated=False`` — while ``TokenElevationTypeDefault`` covers those two cases together, so
    the only implications Windows offers are ``full`` ⇒ elevated and ``limited`` ⇒ not elevated. A
    first cut of the elevation cross-check in `caps/identity.py` assumed ``default`` meant "not
    elevated", and on a host where the real pair is ``(True, "default")`` it dropped the fact; a
    decision that re-checked the pair here would repeat that mistake, and this parametrisation is what
    would redden if it did.
    """

    subject = observed(elevated=elevated, elevation_type=elevation_type)

    assert policy.admit_caller(subject, expectation()) is subject


def test_a_sid_outside_the_allowed_set_is_refused_and_the_set_is_not_echoed() -> None:
    """The observed SID is the deciding fact; the allowed set is the machine's own identity list.

    The refusal travels back to the caller, so the configured list stays out of it — the same reason
    `cli/tests/fake_broker.py`'s `probe_root` reports observed trustees by count. The observed SID is
    named, because a refusal that hid which identity was compared could not be re-examined.
    """

    subject = observed(sid=SID_OTHER)

    error = assert_refused(subject, expectation(), rule="sid_not_allowed")

    assert error.message.endswith(f"the observed SID {SID_OTHER} is not one this broker serves")
    assert any(SID_OTHER in line for line in error.evidence), error.evidence
    assert any("is not among the 1 SID(s) in allowed_sids" in line for line in error.evidence), (
        error.evidence
    )
    assert SID_ALLOWED not in json.dumps(error.to_envelope()), "the allowed set must not be echoed"


def test_an_integrity_below_the_minimum_is_refused_naming_both_words() -> None:
    subject = observed(integrity="low")

    error = assert_refused(subject, expectation(), rule="integrity_below_minimum")

    assert "low" in error.message and "medium" in error.message
    assert any("low < medium" in line for line in error.evidence), error.evidence
    assert any("SECURITY_MANDATORY_*_RID_BASE" in line for line in error.evidence), error.evidence


def test_a_pid_that_no_longer_names_the_observed_process_is_refused() -> None:
    """A pid is reused; a creation time is not, which is the whole reason the fact is carried."""

    subject = observed()
    terms = expectation(creation_time=CREATION_TIME - 1)

    error = assert_refused(subject, terms, rule="pid_reused")

    assert str(CREATION_TIME) in " ".join(error.evidence)
    assert str(CREATION_TIME - 1) in " ".join(error.evidence)
    assert any("reused" in line for line in error.evidence), error.evidence


@pytest.mark.parametrize(
    "subject, terms, rule",
    [
        # Everything is wrong at once: the first rule in the order is the one that is named.
        (
            observed(sid=None, is_app_container=True, integrity="low", creation_time=None),
            expectation(creation_time=CREATION_TIME),
            "observation_incomplete",
        ),
        (
            observed(is_app_container=True, sid=SID_OTHER, integrity="low"),
            expectation(creation_time=CREATION_TIME - 1),
            "app_container_caller",
        ),
        (
            observed(sid=SID_OTHER, integrity="low"),
            expectation(creation_time=CREATION_TIME - 1),
            "sid_not_allowed",
        ),
        (observed(integrity="low"), expectation(creation_time=CREATION_TIME - 1), "integrity_below_minimum"),
        (observed(), expectation(creation_time=CREATION_TIME - 1), "pid_reused"),
    ],
)
def test_the_rule_named_is_the_first_one_in_the_order(
    subject: identity.ProcessIdentity, terms: policy.CallerExpectation, rule: str
) -> None:
    """The order is contract, not implementation detail: it decides which single fact is reported."""

    assert_refused(subject, terms, rule=rule)


# --------------------------------------------------------------------------------------------- #
# "nobody could look" is not "looked, and it was fine"
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("fact", [*REQUIRED_FACTS_EXPECTED, policy.CREATION_TIME_FACT])
def test_a_fact_that_was_never_observed_refuses_the_caller(fact: str) -> None:
    """The property the whole module exists for, over every fact the decision reads.

    Parametrised over the **hand-written** tuple above, not over `policy.REQUIRED_FACTS`: a case
    derived from the module cannot notice the module dropping it. ``creation_time`` is parametrised
    with the rest because this expectation supplies one — which is exactly when it is read (see the
    next test for the other direction).
    """

    terms = expectation(creation_time=CREATION_TIME)
    subject = observed(**{fact: None})
    assert fact in policy.required_facts(terms), (
        f"{fact} is no longer a fact the decision requires: the required set shrank"
    )

    error = assert_refused(subject, terms, rule="observation_incomplete")

    reported = missing_report(error)
    assert [line.split(":", 1)[0] for line in reported] == [fact], (
        "the refusal has to name the fact that was missing, and only that one — every other fact was "
        f"observed: {error.evidence}"
    )
    assert any(f"{fact}: None" in line for line in error.evidence), error.evidence


def test_the_required_set_is_the_six_facts_the_decision_reads() -> None:
    """The set itself, not just the property over it: equality, in both directions.

    The other direction is the interesting one — a *wider* tuple would refuse a caller for a fact no
    rule reads (`app_container_sid` is the nearest candidate, and it is an answer rather than a
    reading for an ordinary process), and the fields test below would not notice either.
    """

    assert policy.REQUIRED_FACTS == REQUIRED_FACTS_EXPECTED
    assert policy.required_facts(expectation()) == REQUIRED_FACTS_EXPECTED
    assert policy.required_facts(expectation(creation_time=CREATION_TIME)) == (
        REQUIRED_FACTS_EXPECTED + (policy.CREATION_TIME_FACT,)
    )


def test_creation_time_is_required_only_when_the_expectation_supplies_one() -> None:
    """An expectation that names no creation time is not asking for one; requiring it anyway would
    refuse callers for a fact nobody asked about, which is an invented requirement."""

    no_comparison = policy.required_facts(expectation())
    assert policy.CREATION_TIME_FACT not in no_comparison

    admitted = policy.admit_caller(observed(creation_time=None), expectation())
    assert admitted.creation_time is None

    terms = expectation(creation_time=CREATION_TIME)
    assert policy.CREATION_TIME_FACT in policy.required_facts(terms)

    error = assert_refused(observed(creation_time=None), terms, rule="observation_incomplete")
    assert [line.split(":", 1)[0] for line in missing_report(error)] == [policy.CREATION_TIME_FACT]

    # And when it is observed, it is compared rather than merely present.
    assert policy.admit_caller(observed(), terms).creation_time == CREATION_TIME
    assert_refused(observed(creation_time=CREATION_TIME + 1), terms, rule="pid_reused")


# --------------------------------------------------------------------------------------------- #
# the integrity ladder
# --------------------------------------------------------------------------------------------- #


def test_the_ladder_restated_here_is_the_one_caps_identity_uses() -> None:
    """Two definitions of one machine's ordering, held equal in both directions.

    `caps/identity.py` keeps the four words in a table keyed by their `SECURITY_MANDATORY_*_RID_BASE`
    values, highest first, and exports no public ordering — so `policy.INTEGRITY_LADDER` restates it
    lowest first. This measures the direction as well as the membership: if identity.py ever reordered
    or re-worded its table, one of these assertions says so instead of the broker disagreeing quietly.
    """

    thresholds = identity._INTEGRITY_THRESHOLDS
    bases = [base for base, _word in thresholds]

    assert bases == sorted(bases, reverse=True), (
        "identity.py's table is highest-RID first; the reversal below is what makes it an ordering"
    )
    assert policy.INTEGRITY_LADDER == tuple(word for _base, word in reversed(thresholds))
    assert set(policy.INTEGRITY_LADDER) == {word for _base, word in thresholds}
    assert len(set(policy.INTEGRITY_LADDER)) == len(policy.INTEGRITY_LADDER)


@pytest.mark.parametrize(
    "minimum, level",
    [
        ("low", "low"),
        ("low", "medium"),
        ("low", "high"),
        ("low", "system"),
        ("medium", "medium"),
        ("medium", "high"),
        ("medium", "system"),
        ("high", "high"),
        ("high", "system"),
        ("system", "system"),
    ],
)
def test_an_integrity_at_or_above_the_minimum_is_admitted(minimum: str, level: str) -> None:
    """`>=`, not `>`: the equal case is the one a strict comparison would wrongly refuse."""

    subject = observed(integrity=level)

    assert policy.admit_caller(subject, expectation(minimum_integrity=minimum)) is subject


@pytest.mark.parametrize(
    "minimum, level",
    [
        ("medium", "low"),
        ("high", "low"),
        ("high", "medium"),
        ("system", "low"),
        ("system", "medium"),
        ("system", "high"),
    ],
)
def test_an_integrity_below_the_minimum_is_refused(minimum: str, level: str) -> None:
    assert_refused(
        observed(integrity=level),
        expectation(minimum_integrity=minimum),
        rule="integrity_below_minimum",
    )


@pytest.mark.parametrize("word", ["medium plus", "untrusted", "SYSTEM", "low ", ""])
def test_an_unrecognised_integrity_word_is_refused_on_either_side(word: str) -> None:
    """`medium plus` (0x2100) is a real Windows level that `caps/identity.py` maps to `medium`; as a
    word it is not one of the four, and an unorderable pair is refused rather than rounded."""

    error = assert_refused(
        observed(integrity=word), expectation(minimum_integrity="low"), rule="integrity_below_minimum"
    )
    assert any(repr(word) in line for line in error.evidence), error.evidence
    assert any("cannot be placed on Windows' `>=` ladder" in line for line in error.evidence)

    error = assert_refused(
        observed(integrity="system"), expectation(minimum_integrity=word), rule="integrity_below_minimum"
    )
    assert any(repr(word) in line for line in error.evidence), error.evidence


def test_both_unrecognised_words_are_reported_when_neither_can_be_ordered() -> None:
    error = assert_refused(
        observed(integrity="nonsense"), expectation(minimum_integrity="rubbish"),
        rule="integrity_below_minimum",
    )

    assert "the observed integrity" in error.message and "minimum_integrity" in error.message
    assert any("'nonsense'" in line for line in error.evidence), error.evidence
    assert any("'rubbish'" in line for line in error.evidence), error.evidence


# --------------------------------------------------------------------------------------------- #
# the table is the rules the code can emit
# --------------------------------------------------------------------------------------------- #


def policy_tree() -> ast.Module:
    return ast.parse(pathlib.Path(policy.__file__).read_text(encoding="utf-8"))


def emittable_rules(tree: ast.Module) -> set[str]:
    """Every rule id the module can refuse with, derived from its own call sites.

    `_refuse` is the module's only raise site, so the ids are the first argument of every call to it.
    Derived rather than listed by hand, because a second hand-written list is exactly the thing that
    drifts: this way a rule added to the code without a reason, and a reason whose rule was deleted,
    both turn this file red.
    """

    rules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_refuse":
            assert node.args, "_refuse was called with no rule id"
            rule = node.args[0]
            assert isinstance(rule, ast.Constant) and isinstance(rule.value, str), ast.dump(rule)
            rules.add(rule.value)
    return rules


def test_the_refusal_reasons_are_exactly_the_rules_the_code_can_emit() -> None:
    tree = policy_tree()

    emitted = emittable_rules(tree)

    assert emitted, "no `_refuse` call site was found; the derivation, not the module, is broken"
    assert emitted == set(policy.REFUSAL_REASONS), (
        "a rule without a reason, or a reason whose rule is gone: "
        f"emitted={sorted(emitted)} tabulated={sorted(policy.REFUSAL_REASONS)}"
    )
    for rule, reason in policy.REFUSAL_REASONS.items():
        assert reason.strip(), f"{rule} has an empty reason"
        assert "\n" not in reason, f"{rule}'s reason is not one line"
        assert rule == rule.lower() and " " not in rule, f"{rule} is not a rule id"


def test_the_refusal_code_has_exactly_one_raise_site_in_the_module() -> None:
    """One site, so no path can refuse with the code but without a `rule` in `details`."""

    literals = [
        node.value
        for node in ast.walk(policy_tree())
        if isinstance(node, ast.Constant) and node.value == "CALLER_NOT_AUTHORIZED"
    ]

    assert literals == ["CALLER_NOT_AUTHORIZED"]


def test_the_rule_derivation_reports_a_rule_the_table_does_not_carry() -> None:
    """The guard's red direction, on input that is not this module (the §54.4 pattern).

    A derivation that returned nothing would make the equality above fail loudly; one that returned
    the table's own keys would pass it while checking nothing. So it is measured against a rule the
    table does not carry, which only the call sites can supply.
    """

    synthetic = ast.parse('def f():\n    _refuse("ghost_rule", "message", [], i, e)\n')

    assert emittable_rules(synthetic) == {"ghost_rule"}
    assert emittable_rules(synthetic) != set(policy.REFUSAL_REASONS)


# --------------------------------------------------------------------------------------------- #
# the claim cannot reach the decision
# --------------------------------------------------------------------------------------------- #


def test_the_observed_identity_has_no_field_that_could_carry_a_claim() -> None:
    """`ProcessIdentity` has no `application_id`, and gaining one here would be the defect.

    The caller's application identity is the one field of `broker-request`'s `client` block that is
    pure self-description, and this type is what the server *observes*. `is_app_container` is the
    observable stand-in, which is why it is a different name for a different fact.
    """

    names = [field.name for field in dataclasses.fields(identity.ProcessIdentity)]
    assert [name for name in names if "application" in name] == []
    assert "is_app_container" in names, "the observable stand-in vanished; this guard is weaker now"

    expectation_fields = [field.name for field in dataclasses.fields(policy.CallerExpectation)]
    assert [name for name in expectation_fields if "application" in name] == []


def test_the_decision_takes_the_observation_and_the_expectation_and_nothing_else() -> None:
    signature = inspect.signature(policy.admit_caller)
    parameters = list(signature.parameters.values())

    assert len(parameters) == 2, [parameter.name for parameter in parameters]
    assert [parameter.name for parameter in parameters] == ["identity", "expectation"]
    assert not CLAIM_SHAPED.intersection(parameter.name for parameter in parameters)
    assert [parameter.kind for parameter in parameters] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ], "a keyword-only or variadic parameter is a second way to pass something that is not the pair"
    assert [parameter.annotation for parameter in parameters] == [
        "ProcessIdentity",
        "CallerExpectation",
    ]
    assert signature.return_annotation == "ProcessIdentity"


def test_the_module_cannot_name_a_claim_and_does_not_import_the_machine() -> None:
    """Structural, because "we do not read the client block" is only true if it cannot be spelled.

    Two claims in one test, both about the module as written rather than about a run: no identifier
    mentions an application id, and the module's runtime imports are `typing`/`exits` only — the
    observation itself is imported under `TYPE_CHECKING`, so the decision can be exercised without
    `ctypes`, a token or a machine.
    """

    tree = policy_tree()

    suspicious = sorted(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and "application" in node.id
    ) + sorted(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and "application" in node.attr
    )
    assert suspicious == [], suspicious

    guarded = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
        for inner in ast.walk(node)
    }
    runtime: set[str] = set()
    everything: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            everything |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            everything.add(node.module or "")
        else:
            continue
        if id(node) not in guarded:
            runtime |= {alias.name for alias in node.names} if isinstance(node, ast.Import) else {node.module or ""}

    assert runtime == {"__future__", "typing", "dataclasses", "exits"}, runtime
    assert everything == {"__future__", "typing", "dataclasses", "exits", "caps.identity"}, everything
    assert "ctypes" not in everything and "os" not in everything


def test_the_required_facts_are_real_fields_of_the_observation() -> None:
    """The tuples are looked up on the type, so a renamed field must fail here and not at runtime."""

    names = {field.name for field in dataclasses.fields(identity.ProcessIdentity)}
    required = set(REQUIRED_FACTS_EXPECTED)

    assert required <= names, sorted(required - names)
    assert policy.CREATION_TIME_FACT in names
    assert policy.required_facts(policy.CallerExpectation(frozenset(), "low")) == REQUIRED_FACTS_EXPECTED
    assert len(required) == len(REQUIRED_FACTS_EXPECTED), "a duplicated fact would be reported twice"


def test_a_request_that_claims_an_allowed_sid_is_still_refused_for_the_observed_one() -> None:
    """The claim changes nothing — proved against a real `broker-request`, not only in prose.

    The request is built by the wire layer, so its `client` block is a *valid* document that claims an
    allowed SID, a high integrity level and an application id. The observation is someone else's, so
    the answer is `sid_not_allowed` on the SID the token carried — and none of the claimed values can
    be found in the refusal, because the decision never had them.
    """

    request = protocol.build_request(
        operation="probe_root",
        request_id="req-policy-claim",
        client={
            "sid": SID_ALLOWED,
            "pid": 4242,
            "integrity": "high",
            "application_id": "airoot-cli",
        },
    )
    assert request["client"]["sid"] == SID_ALLOWED

    subject = observed(sid=SID_OTHER, integrity="medium")

    error = assert_refused(subject, expectation(), rule="sid_not_allowed")

    envelope = json.dumps(error.to_envelope())
    assert SID_OTHER in envelope, "the observed SID is the deciding fact and has to be named"
    for claimed in ("airoot-cli", SID_ALLOWED):
        assert claimed not in envelope, f"a claimed value reached the refusal: {claimed}"
    assert "high" not in envelope, "the claimed integrity level reached the refusal"


# --------------------------------------------------------------------------------------------- #
# the one test whose subject is the machine's own answer
# --------------------------------------------------------------------------------------------- #


def test_the_machines_own_answer_is_admitted_or_refused_naming_what_it_could_not_read() -> None:
    """This process, read as a target by `probe_process` — the observation the decision really gets.

    Every other case in this file injects its identity, because the decision is a pure function and a
    synthetic caller can be made exactly as complete or as broken as a case needs. Here the machine
    answers for itself, and the property asserted is the honest one rather than "the probe is
    complete": if every fact the decision reads was observed, the caller is admitted; otherwise it is
    refused as `observation_incomplete`, naming exactly the facts that were `None` and never the ones
    that were read. Written two-sided on purpose — this build's peer probe is still being widened
    (draft §114), and an assertion that it is complete would measure the host instead of the decision.

    That is not hypothetical: at the time of writing this box's answer is ``elevated=True`` with
    ``elevation_type="default"`` (an administrator with UAC off), and a cross-check in
    `caps/identity.py` that mistook ``default`` for "not elevated" dropped `elevation_type` to ``None``
    for a perfectly legitimate caller. This test stays green either way — through the refusal branch
    while that is outstanding, and through the admission branch once it lands — because what it
    measures is the decision's honesty about ``None``, not the probe's completeness.

    `probe_process` is the decision's sibling and not part of it: it lives in `caps/identity.py`, and
    `broker/policy.py` imports nothing from it at runtime.
    """

    reading = identity.probe_process(os.getpid())
    terms = policy.CallerExpectation(
        allowed_sids=frozenset({reading.sid}) if reading.sid else frozenset(),
        minimum_integrity="low",
    )
    unobserved = [
        name for name in policy.required_facts(terms) if getattr(reading, name, None) is None
    ]

    if not unobserved:
        admitted = policy.admit_caller(reading, terms)
        assert admitted is reading
        assert admitted.pid == os.getpid()
        return

    error = assert_refused(reading, terms, rule="observation_incomplete")

    assert [line.split(":", 1)[0] for line in missing_report(error)] == unobserved
    for name in policy.required_facts(terms):
        if name not in unobserved:
            assert not any(line.startswith(f"{name}: None") for line in error.evidence), name
