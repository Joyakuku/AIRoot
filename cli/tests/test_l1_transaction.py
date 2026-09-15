"""L1: the transaction state machine, journal-driven recovery and rollback.

Covers the P1-applicable cases from ``AIROOT-v0.3-验证与测试方案.md``: §6.1 (abort
at every state boundary), T-002/T-006/T-007/T-008/T-009/T-010/T-011/T-012/T-015,
P-004..P-008, P-011, P-014, S-009, S-018, S-022.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import fake_issuer
from airoot.clock import FakeClock
from airoot.exits import AirootError
from airoot.registry import Registry
from airoot.tx import create_plan, repair
from airoot.tx.journal import TransactionJournal, classify
from airoot.tx.simulate import (
    PAYLOAD_NAME,
    SIMULATION_BACKEND,
    SimulationRunner,
    happy_path_states,
)
from airoot.tx.states import (
    ACTIVE_BINDING_COMMIT_STATE,
    DOCUMENTED_STATES,
    HAPPY_PATH,
    PAYLOAD_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    can_transition,
    next_happy_state,
    require_transition,
)


class Tamper:
    """An injector that mutates the payload instead of interrupting."""

    def __init__(self, *, at_state: str, store_dir: Path) -> None:
        self.at_state = at_state
        self.store_dir = store_dir
        self.fired = False

    def checkpoint(self, state: str) -> None:
        if state == self.at_state and not self.fired:
            self.fired = True
            (self.store_dir / PAYLOAD_NAME).write_bytes(b"tampered after ACTIVE_BOUND\n")


def build(registry: Registry, clock: FakeClock, root, *, version: str = "1.0.0", ttl_minutes: int = 60):
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock, ttl_minutes=ttl_minutes)
    token = fake_issuer.issue(plan, clock=clock)
    return plan, token


def rehash(plan: dict) -> dict:
    """Recompute the plan hash after an intentional edit (negative tests only)."""

    from airoot.canon import plan_hash

    altered = dict(plan)
    altered["plan_hash"] = plan_hash(altered)
    return altered


# --------------------------------------------------------------------------- #
# the state machine itself
# --------------------------------------------------------------------------- #


def test_transition_table_matches_the_frozen_document() -> None:
    assert happy_path_states() == (
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
    assert can_transition("PROPOSED", "APPROVED")
    assert not can_transition("PROPOSED", "STAGED"), "steps may not be skipped"
    assert not can_transition("REGISTERED", "EXPOSED"), "REGISTERED must pass through ACTIVE_BOUND"
    assert can_transition("STAGED", "ROLLBACK_PENDING")
    assert not can_transition("FINALIZED", "FAILED")
    assert TRANSITIONS["EXPIRED"] == ()


def test_every_documented_state_is_representable_in_the_schema() -> None:
    """Cross-document contract check: the schema must be able to express the state machine."""

    from airoot.schema_io import load_schema

    schema_states = set(load_schema("transaction")["properties"]["state"]["enum"])
    missing = set(DOCUMENTED_STATES) - schema_states
    assert not missing, f"transaction.schema.json cannot express {sorted(missing)} (ADR-0003)"
    assert schema_states - set(DOCUMENTED_STATES) == set()


# --------------------------------------------------------------------------- #
# the shape of the legal-move table itself (draft §45)
#
# The table is hand-transcribed from the frozen contract, and until now only two things about it
# were checked: that `EXPIRED` is a dead end, and that the schema can *name* every state. Nothing
# checked that the table is internally coherent — a state you can reach but that is not documented,
# a documented state you can never reach, a terminal state with an exit, or a happy path that
# contains an illegal step would all have gone unnoticed.
# --------------------------------------------------------------------------- #


def test_the_transition_table_and_the_documented_states_are_the_same_set() -> None:
    """A reachable-but-undocumented state, or a documented dead end, is a contract defect."""

    assert set(TRANSITIONS) == set(DOCUMENTED_STATES), (
        f"table-only: {sorted(set(TRANSITIONS) - set(DOCUMENTED_STATES))}; "
        f"documented-only: {sorted(set(DOCUMENTED_STATES) - set(TRANSITIONS))}"
    )


def test_the_any_stage_exception_rule_is_implemented_and_bounded() -> None:
    """§4.1 says a transaction may enter the five exception states **from any stage** (ADR-0021).

    §45.5 recorded that the prose is wider than the table and that nothing could decide which was
    authoritative. ADR-0021 decided it in the wider direction — the prose wins — and this pins the
    rule rather than the 62 individual edges, so a future narrowing has to be a decision:

    * every non-terminal happy-path stage may enter FAILED / ROLLBACK_PENDING / ROLLED_BACK /
      RECOVERY_REQUIRED;
    * `EXPIRED` is held back from the stages that may already have changed the active binding
      (§4.1 calls it terminal and binding-preserving, §14.2 requires a changed binding to keep a
      rollback route — so allowing it would strand the binding instead of loosening anything);
    * the widening added **only** exception targets: the forward discipline is untouched, which is
      what makes this a loosening of the exception moves rather than of the happy path.
    """

    from airoot.tx.states import BINDING_CHANGE_STATES, EXCEPTION_STATES, EXPIRY_STATE

    always = ("FAILED", "ROLLBACK_PENDING", "ROLLED_BACK", "RECOVERY_REQUIRED")
    stages = [state for state in HAPPY_PATH if state not in TERMINAL_STATES]

    for state in stages:
        for target in always:
            assert can_transition(state, target), f"§4.1 says {target} is reachable from any stage: {state}"
        expected_expiry = state not in BINDING_CHANGE_STATES
        assert can_transition(state, EXPIRY_STATE) is expected_expiry, (
            f"{state} -> {EXPIRY_STATE}: the boundary is deliberate (ADR-0021), so changing it is a decision"
        )

    # The widening must not have relaxed the happy path: the only non-exception target of a stage is
    # the next stage (and nothing at all beyond the end).
    for state in HAPPY_PATH:
        forward = {target for target in TRANSITIONS[state] if target not in EXCEPTION_STATES}
        successor = next_happy_state(state)
        assert forward == ({successor} if successor else set()), (
            f"{state} may only move forward to {successor}, found {sorted(forward)}"
        )


def test_every_transition_target_is_a_documented_state() -> None:
    for state, targets in TRANSITIONS.items():
        unknown = sorted(target for target in targets if target not in DOCUMENTED_STATES)
        assert unknown == [], f"{state} points at states that are not documented: {unknown}"


def test_every_happy_path_step_is_a_legal_move() -> None:
    """`next_happy_state` hands out moves; the table is what decides they are legal."""

    for current, target in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        assert target in TRANSITIONS[current], f"happy path step {current} -> {target} is not legal"
        assert next_happy_state(current) == target
    assert next_happy_state(HAPPY_PATH[-1]) is None


def test_terminal_states_are_exactly_the_dead_ends() -> None:
    """Both directions: a terminal state with an exit, or a dead end that is not terminal."""

    for state, targets in TRANSITIONS.items():
        if state in TERMINAL_STATES:
            assert targets == (), f"terminal {state} still has exits: {targets}"
        else:
            assert targets != (), f"{state} is a dead end but is not declared terminal"


def test_every_documented_state_is_reachable_from_proposed() -> None:
    """An unreachable state is contract text nobody can ever observe."""

    seen, frontier = {"PROPOSED"}, ["PROPOSED"]
    while frontier:
        for target in TRANSITIONS.get(frontier.pop(), ()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert sorted(set(DOCUMENTED_STATES) - seen) == []


def test_payload_states_are_the_states_at_or_after_commit() -> None:
    """`PAYLOAD_STATES` answers "may a payload exist here"; it must track the happy path."""

    expected = set(HAPPY_PATH[HAPPY_PATH.index("COMMITTED"):]) | {"ROLLBACK_PENDING"}
    assert PAYLOAD_STATES == expected
    assert "COMMITTED" in PAYLOAD_STATES
    for state in HAPPY_PATH[: HAPPY_PATH.index("COMMITTED")]:
        assert state not in PAYLOAD_STATES, f"no payload can exist in {state}"


def test_the_active_binding_commit_point_is_on_the_happy_path() -> None:
    """The single commit point (core contracts §4.2) has to be a state the machine can reach."""

    assert ACTIVE_BINDING_COMMIT_STATE in HAPPY_PATH
    assert ACTIVE_BINDING_COMMIT_STATE not in TERMINAL_STATES


def test_the_legal_move_table_matches_its_acceptance_artifact() -> None:
    """The edge set is contract, so it is pinned in the Rust-port corpus, not only in code.

    The document names the happy path and the exception states but not the edges, so a port could
    reproduce every response and still disagree about which moves are legal (draft §45.1).
    """

    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parent / "fixtures" / "golden" / "transaction_transitions.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    assert document["happy_path"] == list(HAPPY_PATH)
    assert document["terminal_states"] == sorted(TERMINAL_STATES)
    assert document["payload_states"] == sorted(PAYLOAD_STATES)
    assert document["active_binding_commit_state"] == ACTIVE_BINDING_COMMIT_STATE
    assert document["transitions"] == {
        state: list(targets) for state, targets in sorted(TRANSITIONS.items())
    }


def test_illegal_transition_is_refused() -> None:
    with pytest.raises(AirootError) as err:
        require_transition("PROPOSED", "FINALIZED")
    assert err.value.reason_code == "ILLEGAL_TRANSITION"
    assert err.value.exit_code == 7


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_happy_path_commits_payload_binding_and_generation(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root)
    runner = SimulationRunner(registry, clock=clock)
    tx = runner.commit(plan, token)

    assert tx["state"] == "FINALIZED"
    assert tx["generation_before"] == 0 and tx["generation_after"] == 1
    assert registry.generation == 1

    instance = registry.instance(plan["target"]["instance_id"])
    assert instance is not None
    assert instance["lifecycle_status"] == "active" and instance["health"] == "healthy"
    assert instance["install_backend_id"] == SIMULATION_BACKEND

    active = registry.bindings(active_only=True)
    assert [row["instance_id"] for row in active] == [plan["target"]["instance_id"]]
    assert active[0]["generation"] == 1

    store_dir = Path(root.path) / "store" / plan["target"]["instance_id"]
    assert (store_dir / PAYLOAD_NAME).is_file(), "the payload lands in store, not in tools"
    assert registry.integrity_problems() == []


def test_transaction_records_every_state_in_order(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root)
    tx = SimulationRunner(registry, clock=clock).commit(plan, token)
    states = [row["state"] for row in registry.events(transaction_id=tx["transaction_id"])]
    assert states == list(happy_path_states())


def test_approval_nonce_is_consumed_exactly_once(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    stored = registry.approval_by_nonce(token["nonce"])
    assert stored is not None and stored["consumed_at"]

    with pytest.raises(AirootError) as err:
        with registry.write(expected_generation=registry.generation) as connection:
            registry.consume_approval(connection, token["approval_id"])
    assert err.value.reason_code == "APPROVAL_REPLAYED"

    # A finalized transaction needs no recovery work.
    tx_id = registry.transactions()[0]["transaction_id"]
    assert repair(registry, tx_id, clock=clock)["action"] == "no_action"


def test_replaying_the_same_approval_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root)
    runner = SimulationRunner(registry, clock=clock)
    runner.commit(plan, token)
    with pytest.raises(AirootError) as err:
        runner.commit(plan, token)
    assert err.value.reason_code == "APPROVAL_REPLAYED"
    assert err.value.exit_code == 4


def test_P016_the_same_token_consumed_twice_never_rewinds_the_transaction(
    registry: Registry, clock, root
) -> None:
    """P-016: the same token applied twice — one transaction, one binding change, one consumption.

    A thread race would be the obvious test and a bad one: whether the loser arrives before or after
    the winner finalizes decides *which* answer it gets, so a race-only test is a coin flip that can
    pass for the wrong reason. Draft §61 measured the two interleavings deterministically instead.
    Both are real and both must hold:

    * **mid-flight** — the loser arrives while the winner sits at ``ACTIVE_BOUND``. It picks up the
      same transaction (the id is derived from plan+approval) and finishes it. It must do so **without
      rewinding it**: the audit trail below used to read `… ACTIVE_BOUND, PROPOSED, … FINALIZED`,
      i.e. a second pass through the one commit point that changes the active binding, plus a
      throwaway generation bump, because `journal.create` overwrote the durable envelope with a fresh
      `PROPOSED`;
    * **after the fact** — once the winner has finalized, the same token is ``APPROVAL_REPLAYED``.
    """

    from conftest import FakeClock, FaultInjector

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="41.0.0", clock=clock, ttl_minutes=600)
    token = fake_issuer.issue(plan, clock=clock, ttl_minutes=600)

    # The winner stops at the one commit point that changes the active binding.
    winner = SimulationRunner(registry, clock=clock, injector=FaultInjector(stop_after="ACTIVE_BOUND"))
    stopped = winner.commit(plan, token)
    assert stopped["state"] == "ACTIVE_BOUND"
    assert len(registry.bindings(active_only=True)) == 1

    # Mid-flight loser, on its own connection, with the same plan and the same token.
    other = Registry.open(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z"))
    try:
        driven = SimulationRunner(other, clock=FakeClock(start="2024-01-01T00:00:00Z")).commit(plan, token)
    finally:
        other.close()
    assert driven["state"] == "FINALIZED"
    assert driven["journal_seq"] > stopped["journal_seq"], "the journal only ever moves forward"

    rows = registry.transactions()
    assert len(rows) == 1, "the same plan and token must never open a second transaction"
    assert rows[0]["state"] == "FINALIZED"
    assert rows[0]["journal_seq"] == driven["journal_seq"]

    # The two assertions this test exists for: one pass through the commit point, one beginning.
    short = str(stopped["transaction_id"]).split("/")[-1]
    states = [
        str(event["state"])
        for event in registry.events()
        if event["transaction_id"] and str(event["transaction_id"]).split("/")[-1] == short
    ]
    assert states.count("PROPOSED") == 1, f"the transaction was restarted, not resumed: {states}"
    assert states.count("ACTIVE_BOUND") == 1, f"the commit point was passed twice: {states}"

    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert active[0]["instance_id"] == plan["target"]["instance_id"]
    assert active[0]["generation"] <= registry.generation
    assert registry.integrity_problems() == []

    # The nonce was consumed exactly once — the scenario's headline claim.
    consumed = registry.approval(str(token["approval_id"]))
    assert consumed is not None and consumed["consumed_at"]

    # After the fact, the same token is a replay, on the original runner and a fresh one alike.
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "APPROVAL_REPLAYED"
    assert err.value.exit_code == 4


def test_the_audit_record_of_an_approval_says_which_kind_it_was(registry: Registry, clock, root) -> None:
    """三大核心契约 决策3: a policy approval must be recorded as ``approval_mode=policy``.

    This is the audit half of the scenario asking for a low-risk **policy** approval to be
    distinguishable from a human one. The other half — a component that *produces* a policy approval —
    still needs an issuer, so that half is still filed as missing. What is pinned here is the part the
    contract states as a requirement on the record, and the part that was genuinely absent: measured
    before draft §65, the `events` table had no such column, so an agent's approval and a person's
    were the same row shape and no reader could tell them apart.

    Three values matter, not two. An event with no approval records **no** mode, and that must stay
    distinguishable from a human approval — "not checked" and "checked, and it was a person" are
    different answers (the rule §50 applied to bounds, applied here to authorship).
    """

    from airoot.tx.approval import record_approval

    fake_issuer.install_keyring(root.path)

    policy_plan = create_plan(registry, version="60.0.0", clock=clock, ttl_minutes=600)
    policy_token = fake_issuer.issue(policy_plan, clock=clock, ttl_minutes=600, mode="policy")
    record_approval(registry, policy_token)

    human_plan = create_plan(registry, version="61.0.0", clock=clock, ttl_minutes=600)
    human_token = fake_issuer.issue(human_plan, clock=clock, ttl_minutes=600, mode="human")
    record_approval(registry, human_token)

    with registry.write(expected_generation=registry.generation) as connection:
        registry.append_event(connection, state="NOTE", detail="nothing was approved here")

    by_event = {(row["approval_id"], row["state"]): row for row in registry.events()}

    recorded_policy = by_event[(policy_token["approval_id"], "APPROVED")]
    assert recorded_policy["approval_mode"] == "policy"
    assert "mode=policy" in recorded_policy["detail"], recorded_policy["detail"]

    recorded_human = by_event[(human_token["approval_id"], "APPROVED")]
    assert recorded_human["approval_mode"] == "human"

    unapproved = by_event[(None, "NOTE")]
    assert unapproved["approval_mode"] is None, "no approval recorded must not read as a human one"


# --------------------------------------------------------------------------- #
# fault injection at every state boundary (§6.1)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("boundary", list(happy_path_states()))
def test_every_boundary_is_recoverable(registry: Registry, clock, root, machine, boundary: str) -> None:
    from conftest import FaultInjector

    version = f"2.{happy_path_states().index(boundary)}.0"
    plan, token = build(registry, clock, root, version=version)
    injector = FaultInjector(stop_after=boundary)
    runner = SimulationRunner(registry, clock=clock, injector=injector)

    tx = runner.commit(plan, token)
    assert tx["state"] == boundary, "the interruption happens only after the state is durable"

    # A crash means nothing in memory survives: reopen from disk only.
    reopened = Registry.open(root.path, clock=clock)
    try:
        assert reopened.transaction(tx["transaction_id"])["state"] == boundary
    finally:
        reopened.close()

    result = repair(registry, tx["transaction_id"], clock=clock, keyring={fake_issuer.KEY_ID: fake_issuer.TEST_SECRET})
    assert result["state"] == "FINALIZED"

    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert active[0]["instance_id"] == plan["target"]["instance_id"]
    assert registry.generation == active[0]["generation"]
    assert registry.integrity_problems() == []

    # repair is idempotent (T-011)
    again = repair(registry, tx["transaction_id"], clock=clock, keyring={fake_issuer.KEY_ID: fake_issuer.TEST_SECRET})
    assert again["action"] == "no_action"


def test_registered_instance_is_inactive_until_active_bound(registry: Registry, clock, root) -> None:
    from conftest import FaultInjector

    plan, token = build(registry, clock, root, version="3.0.0")
    runner = SimulationRunner(registry, clock=clock, injector=FaultInjector(stop_after="REGISTERED"))
    tx = runner.commit(plan, token)

    assert tx["state"] == "REGISTERED"
    assert registry.bindings(active_only=True) == [], "REGISTERED must never be selectable"
    assert registry.instance(plan["target"]["instance_id"])["lifecycle_status"] == "installed"


def test_recovery_actions_are_classified_from_the_journal(registry: Registry, clock, root) -> None:
    from conftest import FaultInjector

    plan, token = build(registry, clock, root, version="4.0.0")
    runner = SimulationRunner(registry, clock=clock, injector=FaultInjector(stop_after="ACTIVE_BOUND"))
    tx = runner.commit(plan, token)

    journal = TransactionJournal(registry, clock=clock)
    action = classify(journal.load_context(tx["transaction_id"])[0])
    assert action.state == "ACTIVE_BOUND"
    assert action.action == "reconcile_binding_then_resume_or_revert"
    assert action.requires_explicit_repair is False

    journal = TransactionJournal(registry, clock=clock)
    tx_doc, plan_doc, token_doc = journal.load_context(tx["transaction_id"])
    tx_doc["state"] = "RECOVERY_REQUIRED"
    needs_repair = classify(tx_doc)
    assert needs_repair.requires_explicit_repair is True
    assert plan_doc is not None and token_doc is not None


def test_truncated_journal_requires_recovery_not_guessing(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="5.0.0")
    tx = SimulationRunner(registry, clock=clock).commit(plan, token)
    journal = TransactionJournal(registry, clock=clock)
    journal.envelope_path(tx["transaction_id"]).unlink()

    with pytest.raises(AirootError) as err:
        repair(registry, tx["transaction_id"], clock=clock)
    assert err.value.reason_code == "JOURNAL_TRUNCATED"
    assert err.value.exit_code == 6


# --------------------------------------------------------------------------- #
# negative approval / plan paths
# --------------------------------------------------------------------------- #


def test_plan_hash_tampering_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root)
    tampered = dict(plan, plan_hash="sha256:" + "f" * 64)
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(tampered, token)
    assert err.value.reason_code == "INVALID_PLAN"
    assert err.value.exit_code == 7


def test_token_bound_to_another_plan_is_refused(registry: Registry, clock, root) -> None:
    plan, _token = build(registry, clock, root, version="6.0.0")
    other_plan = create_plan(registry, version="6.1.0", clock=clock)
    token = fake_issuer.issue(other_plan, clock=clock)  # signed for the other plan
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "INVALID_APPROVAL"


def test_tampered_signature_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="7.0.0")
    broken = dict(token, plan_hash=plan["plan_hash"], nonce="a" * 32)
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, broken)
    assert err.value.reason_code == "INVALID_APPROVAL"


def test_token_signed_with_an_unknown_key_is_refused(registry: Registry, clock, root) -> None:
    fake_issuer.install_keyring(root.path, secret=b"a-different-secret")
    plan = create_plan(registry, version="8.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock, secret=b"yet-another-secret")
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "INVALID_APPROVAL"


def test_root_or_machine_mismatch_is_refused(registry: Registry, clock, root) -> None:
    plan, _ = build(registry, clock, root, version="9.0.0")
    token = fake_issuer.issue(plan, clock=clock, machine_id="host-ffffffffffffffff")
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "INVALID_APPROVAL"


def test_expired_approval_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="10.0.0", ttl_minutes=1)
    clock.advance(__import__("datetime").timedelta(minutes=5))
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "APPROVAL_EXPIRED"
    assert err.value.exit_code == 4


def test_plan_expiry_is_enforced(registry: Registry, clock, root) -> None:
    """A plan whose own expiry has passed cannot be committed even with a live token."""

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="11.0.0", clock=clock, ttl_minutes=-1)
    token = fake_issuer.issue(plan, clock=clock, ttl_minutes=60)
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "APPROVAL_EXPIRED"
    assert registry.bindings(active_only=True) == []


def test_policy_revision_change_invalidates_approval(registry: Registry, clock, root) -> None:
    plan, _ = build(registry, clock, root, version="12.0.0")
    token = fake_issuer.issue(plan, clock=clock, policy_revision=99)
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "POLICY_REVISION_MISMATCH"
    assert err.value.exit_code == 4


def test_ed25519_tokens_are_not_accepted_in_p1(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="13.0.0")
    production = dict(token)
    production["signature"] = {"algorithm": "ed25519", "key_id": "prod-key", "value": "base64:AAAA"}
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, production)
    assert err.value.reason_code == "PROVENANCE_FAILED"


def test_missing_keyring_is_reported(registry: Registry, clock, root) -> None:
    plan = create_plan(registry, version="14.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(plan, token)
    assert err.value.reason_code == "PROVENANCE_FAILED"


# --------------------------------------------------------------------------- #
# source drift, unsupported backends, idempotency, concurrency
# --------------------------------------------------------------------------- #


def test_source_drift_after_planning_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="15.0.0")
    fixture = Path(root.path) / plan["source"]["locator"].replace("/", "\\")
    (fixture / "drift-marker.txt").write_text("changed after planning", encoding="utf-8")

    tx = SimulationRunner(registry, clock=clock).commit(plan, token)
    assert tx["state"] == "ROLLED_BACK"
    assert tx["outcome"] == "DIGEST_MISMATCH"
    assert registry.bindings(active_only=True) == []
    assert registry.generation == 0


def test_non_simulation_sources_are_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="16.0.0")
    network_plan = rehash(dict(plan, source=dict(plan["source"], kind="https", locator="https://example.invalid/tool.zip")))
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(network_plan, token)
    assert err.value.reason_code == "UNSUPPORTED_BACKEND"
    assert err.value.exit_code == 7


def test_source_outside_the_fixture_cache_is_refused(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="17.0.0")
    escaped = rehash(dict(plan, source=dict(plan["source"], locator="store/evil/1.0.0/win-x64")))
    with pytest.raises(AirootError) as err:
        SimulationRunner(registry, clock=clock).commit(escaped, token)
    assert err.value.reason_code == "UNSUPPORTED_BACKEND"


def test_reinstalling_the_same_plan_is_idempotent(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="18.0.0")
    SimulationRunner(registry, clock=clock).commit(plan, token)
    generation = registry.generation

    fresh_token = fake_issuer.issue(plan, clock=clock)
    result = SimulationRunner(registry, clock=clock).commit(plan, fresh_token)
    assert result.get("idempotent") is True
    assert result["state"] == "FINALIZED"
    assert registry.generation == generation
    assert len(registry.transactions()) == 1


def test_concurrent_commits_keep_a_single_active_binding(registry: Registry, clock, root) -> None:
    """T-010: two agents commit at once; the DB lock plus CAS must stay consistent."""

    fake_issuer.install_keyring(root.path)
    plans = [(create_plan(registry, version=f"20.{index}.0", clock=clock, ttl_minutes=600), index) for index in range(2)]
    tokens = [(plan, fake_issuer.issue(plan, clock=clock, ttl_minutes=600)) for plan, _ in plans]

    outcomes: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(plan: dict, token: dict) -> None:
        # Each thread opens its own connection (sqlite connections are thread-bound)
        # and uses the same instant the plan and token were created at.
        own = Registry.open(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z"))
        try:
            runner = SimulationRunner(own, clock=FakeClock(start="2024-01-01T00:00:00Z"))
            for _attempt in range(20):
                try:
                    runner.commit(plan, token)
                    with lock:
                        outcomes.append("committed")
                    return
                except AirootError as error:
                    if error.reason_code == "STALE_GENERATION":
                        time.sleep(0.01)
                        continue
                    raise
            with lock:
                errors.append("gave up after repeated stale generations")
        except Exception as error:  # surface any unexpected failure as a test failure
            detail = getattr(error, "evidence", None)
            with lock:
                errors.append(f"{type(error).__name__}: {error} {detail if detail else ''}".strip())
        finally:
            own.close()

    threads = [threading.Thread(target=worker, args=(plan, token)) for plan, token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sorted(outcomes) == ["committed", "committed"]
    active = registry.bindings(active_only=True)
    assert len(active) == 1, "exactly one active binding per key, no matter the interleaving"
    # The generation is a monotonic commit counter, not a transaction counter: a
    # retried attempt may have bumped it once before succeeding on the retry.
    finalized = [tx for tx in registry.transactions() if tx["state"] == "FINALIZED"]
    assert len(finalized) == 2
    assert registry.generation >= len(finalized)
    assert active[0]["generation"] <= registry.generation
    assert registry.instance(active[0]["instance_id"]) is not None
    assert registry.integrity_problems() == []


# --------------------------------------------------------------------------- #
# rollback keeps evidence and restores the previous generation
# --------------------------------------------------------------------------- #


def test_post_bind_verification_failure_restores_the_previous_instance(registry: Registry, clock, root) -> None:
    first_plan, first_token = build(registry, clock, root, version="30.0.0")
    SimulationRunner(registry, clock=clock).commit(first_plan, first_token)
    assert len(registry.bindings(active_only=True)) == 1
    first_active = registry.bindings(active_only=True)[0]["instance_id"]

    second_plan, second_token = build(registry, clock, root, version="31.0.0")
    store_dir = Path(root.path) / "store" / second_plan["target"]["instance_id"]
    injector = Tamper(at_state="ACTIVE_BOUND", store_dir=store_dir)
    tx = SimulationRunner(registry, clock=clock, injector=injector).commit(second_plan, second_token)

    assert tx["state"] == "ROLLED_BACK"
    assert tx["outcome"] == "VERIFY_FAILED"
    assert registry.bindings(active_only=True)[0]["instance_id"] == first_active, "old active stays usable"

    broken = registry.instance(second_plan["target"]["instance_id"])
    assert broken is not None and broken["health"] == "broken"
    assert (Path(root.path) / "store" / second_plan["target"]["instance_id"]).is_dir(), "payload kept as evidence"


def test_failure_before_commit_changes_nothing(registry: Registry, clock, root) -> None:
    plan, token = build(registry, clock, root, version="32.0.0")
    fixture = Path(root.path) / plan["source"]["locator"].replace("/", "\\")
    (fixture / "drift-marker.txt").write_text("drift", encoding="utf-8")
    tx = SimulationRunner(registry, clock=clock).commit(plan, token)

    assert tx["state"] == "ROLLED_BACK"
    assert registry.generation == 0
    assert registry.instances() == []
    assert registry.bindings() == []
    assert not (Path(root.path) / "store" / plan["target"]["instance_id"]).exists()
