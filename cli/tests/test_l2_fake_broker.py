"""L2: the in-process loopback harness — the first producer of a `broker-response`.

`broker-response` had no writer at all until P2 stage 1 added the client half of the wire
(`broker/protocol.py`), and even then nothing answered a request (ADR-0026, ADR-0025's D1). These
tests drive `cli/tests/fake_broker.py` — a **test-path** module — through the published request
builder and hold every answer against the published response schema, so all four operations have an
end-to-end path that exists.

What is asserted here, and what is deliberately **not**:

* the answer validates against `broker-response` — the whole point of producing one;
* the two compatibility-mode constants, and the refusal to answer in any other mode;
* refusals with a real cause (a plan whose content moved after approval, a replayed nonce, a file
  that is not there) come back as documents with registered reason codes, never as exceptions;
* evidence items are objects (`{kind, detail}`), not the error envelope's strings.

What these tests cannot show, and must not be read as showing: that the caller was entitled to ask.
The harness verifies no token, has no ACL and holds no privilege (see its module docstring) — an
in-process call has no boundary behind it. Nothing here touches a named pipe, a registry hive, PATH
or a real AIROOT root beyond the temporary one under `cli/tests/.tmp/`; the session-level
host-mutation guard in `conftest.py` is relied on for that rather than duplicated as a second check.
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

import fake_broker
import fake_issuer
from airoot import root as root_module
from airoot import schema_io
from airoot.broker import protocol
from airoot.canon import plan_hash
from airoot.caps import lifecycle
from airoot.exits import REASON_EXIT, AirootError
from airoot.registry import Registry
from airoot.tx import create_plan
from airoot.tx import journal as journal_module
from airoot.tx import simulate

CLIENT = {
    "sid": "S-1-5-21-1000",
    "pid": 4242,
    "integrity": "medium",
    "application_id": "airoot-cli",
}


class Stop:
    """The transaction engine's fault-injection seam, used to interrupt a commit mid-flight.

    Recovery cannot be exercised against a transaction that finished, so the one test that needs a
    real journal stops the runner at a named state and leaves the durable record behind.
    """

    def __init__(self, *, stop_after: str) -> None:
        self.stop_after = stop_after

    def checkpoint(self, state: str) -> None:
        if state == self.stop_after:
            raise InterruptedError(f"stopped after {state}")


# --------------------------------------------------------------------------------------------- #
# fixtures and helpers
# --------------------------------------------------------------------------------------------- #


@pytest.fixture
def broker_root(root_dir: Path, machine, clock):
    """A temporary AIROOT root with an initialised registry — the harness's whole world.

    The registry is a precondition of `commit_plan`/`gc_apply`/`recover_transaction` (they dispatch
    to the operations that write registry state), so it is created here rather than inside the
    harness: the harness must not create state to answer a question about state.
    """

    info = root_module.init_root(
        root_dir,
        root_instance_id=machine.root_instance_id,
        machine_id=machine.machine_id,
        clock=clock,
    )
    registry = Registry.initialize(
        info.path,
        machine_id=machine.machine_id,
        root_instance_id=machine.root_instance_id,
        clock=clock,
    )
    registry.close()
    return info


def write_plan(root, plan: dict, name: str = "plan-0001.json") -> str:
    """Write a plan where `state/plans/...` says it lives, and return its request-relative ref."""

    target = Path(root.path) / "state" / "plans" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"state/plans/{name}"


def write_token(root, token: dict, name: str = "approval-0001.json") -> str:
    target = Path(root.path) / "state" / "approvals" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(token, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"state/approvals/{name}"


def fresh_plan_and_token(registry: Registry, root, clock, *, version: str = "1.0.0"):
    """A committable plan and its approval, built the way the transaction tests build them.

    `cli/tests/fake_issuer.py` is the issuer the **test path** uses (a token can also come from the
    core's explicit local step, `airoot.tx.issuer`, since ADR-0046 — but a test that needs one should
    not depend on a key being provisioned in the operator's root), so the token comes from here.
    """

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock, ttl_minutes=600)
    token = fake_issuer.issue(plan, clock=clock, ttl_minutes=600)
    return plan, token


def request(operation: str, *, request_id: str = "req/harness/0001", **overrides) -> dict:
    """A request document built through `broker/protocol.py` — never hand-written here."""

    arguments = {"operation": operation, "client": copy.deepcopy(CLIENT), "request_id": request_id}
    arguments.update(overrides)
    return protocol.build_request(**arguments)


def answer(
    operation: str,
    root,
    clock,
    *,
    request_id: str = "req/harness/0001",
    injector=None,
    **overrides,
) -> dict:
    """Build a request, serve it, and hold the answer against the published response schema.

    ``injector`` is the one harness-only argument (see `fake_broker.serve`); everything else is a
    request field and goes through `broker/protocol.py`, so no request in this file is hand-written.
    """

    document = fake_broker.serve(
        request(operation, request_id=request_id, **overrides),
        root=root.path,
        clock=clock,
        injector=injector,
    )
    schema_io.validate_document("broker-response", document)
    return document


# --------------------------------------------------------------------------------------------- #
# the four operations, end to end
# --------------------------------------------------------------------------------------------- #


def test_commit_plan_is_answered_ok_and_finalizes_the_transaction(broker_root, clock) -> None:
    """`commit_plan` → `tx/simulate.py` `SimulationRunner.commit`: the happy path, over the wire."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock)
        plan_ref = write_plan(broker_root, plan)
        approval_ref = write_token(broker_root, token)

        document = answer("commit_plan", broker_root, clock, plan_ref=plan_ref, approval_ref=approval_ref)

        assert document["status"] == "ok"
        assert document["reason_code"] == "SUCCESS"
        assert REASON_EXIT[document["reason_code"]] == 0
        assert document["state"] == "FINALIZED"
        transaction_id = document["transaction_id"]
        assert transaction_id and transaction_id.startswith("tx/fake-tool/")
        # The registry really moved: the commit ran through the in-process implementation.
        assert registry.generation == 1
        assert registry.instance(plan["target"]["instance_id"]) is not None
        assert registry.transaction(transaction_id) is not None
    finally:
        registry.close()


def test_recover_transaction_finishes_an_interrupted_commit(broker_root, clock) -> None:
    """`recover_transaction` → `classify` + `repair`, against a journal a stopped commit left behind."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="2.0.0")
        plan_ref = write_plan(broker_root, plan)
        approval_ref = write_token(broker_root, token)

        stopped = answer(
            "commit_plan",
            broker_root,
            clock,
            plan_ref=plan_ref,
            approval_ref=approval_ref,
            injector=Stop(stop_after="ACTIVE_BOUND"),
        )
        # An interrupted commit is not a verdict: the journal is the recovery authority (exit 6).
        assert stopped["status"] == "recovery_required"
        assert stopped["reason_code"] == "RECOVERY_REQUIRED"
        assert stopped["state"] == "ACTIVE_BOUND"
        assert stopped["retryable"] is True
        transaction_id = stopped["transaction_id"]
        assert transaction_id

        healed = answer("recover_transaction", broker_root, clock, transaction_id=transaction_id)

        assert healed["status"] == "ok"
        assert healed["reason_code"] == "SUCCESS"
        assert healed["state"] == "FINALIZED"
        assert healed["transaction_id"] == transaction_id
        assert any("classified action" in item["detail"] for item in healed["evidence"])
    finally:
        registry.close()


def test_recover_transaction_on_a_terminal_transaction_is_a_no_op(broker_root, clock) -> None:
    """A finished transaction is not repaired again — `classify` says `no_action` and that is the answer."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="2.1.0")
        committed = answer(
            "commit_plan",
            broker_root,
            clock,
            plan_ref=write_plan(broker_root, plan, name="plan-2.1.0.json"),
            approval_ref=write_token(broker_root, token, name="approval-2.1.0.json"),
        )
        assert committed["status"] == "ok"

        healed = answer(
            "recover_transaction", broker_root, clock, transaction_id=committed["transaction_id"]
        )

        assert healed["status"] == "ok"
        assert healed["state"] == "FINALIZED"
        assert any("no_action" in item["detail"] for item in healed["evidence"])
    finally:
        registry.close()


def test_probe_root_reports_the_observation_without_a_transaction(broker_root, clock) -> None:
    """`probe_root` → `open_root` + `capture_acl`, both read-only; it has no transaction to name."""

    document = answer("probe_root", broker_root, clock)

    assert document["status"] == "ok"
    assert document["reason_code"] == "SUCCESS"
    # Honest nulls: the schema requires the keys and this operation has neither a transaction nor a
    # transaction state. A synthesized id would be a statement about a record nobody wrote.
    assert document["transaction_id"] is None
    assert document["state"] is None

    kinds = {item["kind"] for item in document["evidence"]}
    assert {"root", "capability", "acl", "note"} <= kinds
    assert any("observed=True" in item["detail"] or "observed=False" in item["detail"] for item in document["evidence"])
    # It observed the temporary root, not a real one.
    assert any(f"root_instance_id={broker_root.root_instance_id}" in item["detail"] for item in document["evidence"])


def test_gc_apply_deletes_only_the_named_payload(broker_root, clock) -> None:
    """`gc_apply` → `caps/lifecycle.py` `apply_gc_plan`, with the plan and the token off disk."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="3.0.0")
        simulate.SimulationRunner(registry, clock=clock).commit(plan, token)
        instance_id = str(plan["target"]["instance_id"])
        store_dir = Path(broker_root.path) / "store" / instance_id
        assert store_dir.is_dir()

        lifecycle.retire(registry, instance_id, clock=clock)
        gc_plan = lifecycle.build_gc_plan(
            registry, instance_id, clock=clock, plan_id="plan/gc/harness-0001"
        )
        scoped_token = fake_issuer.issue(gc_plan, clock=clock)

        document = answer(
            "gc_apply",
            broker_root,
            clock,
            plan_ref=write_plan(broker_root, gc_plan, name="plan-gc-0001.json"),
            approval_ref=write_token(broker_root, scoped_token, name="approval-gc-0001.json"),
        )

        assert document["status"] == "ok"
        assert document["reason_code"] == "SUCCESS"
        assert REASON_EXIT[document["reason_code"]] == 0
        # Same honest nulls as `probe_root`: `apply_gc_plan` writes GC_INTENT/GC_APPLIED events, not a
        # `transaction` row, so there is no transaction id and no transaction state to carry.
        assert document["transaction_id"] is None
        assert document["state"] is None
        assert "payload_removed=True" in " ".join(item["detail"] for item in document["evidence"])
        assert not store_dir.exists()
        # The registry row survives: collection is recorded, never by dropping the row.
        assert registry.instance(instance_id)["collected_at"]
    finally:
        registry.close()


# --------------------------------------------------------------------------------------------- #
# the dispatch table
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "operation, expects",
    [
        ("commit_plan", {"plan_ref", "approval_ref"}),
        ("recover_transaction", {"transaction_id"}),
        ("probe_root", set()),
        ("gc_apply", {"plan_ref", "approval_ref"}),
    ],
)
def test_the_dispatch_table_covers_the_wire_vocabulary(operation: str, expects: set) -> None:
    """The table is exactly the published `operation` enum, and each entry needs what the schema says."""

    assert set(fake_broker.DISPATCH) == set(protocol.OPERATIONS) == set(fake_broker.SUPPORTED_OPERATIONS)
    assert fake_broker.DISPATCH[operation] in {
        fake_broker._commit_plan,
        fake_broker._recover_transaction,
        fake_broker._probe_root,
        fake_broker._gc_apply,
    }
    assert set(protocol.required_fields(operation)) == expects
    assert callable(fake_broker.DISPATCH[operation])


def test_the_dispatch_table_points_at_the_existing_implementations() -> None:
    """No second implementation: each handler calls the module that already owns the operation."""

    assert fake_broker.DISPATCH == {
        "commit_plan": fake_broker._commit_plan,
        "recover_transaction": fake_broker._recover_transaction,
        "probe_root": fake_broker._probe_root,
        "gc_apply": fake_broker._gc_apply,
    }

    commit_source = inspect.getsource(fake_broker._commit_plan)
    recover_source = inspect.getsource(fake_broker._recover_transaction)
    probe_source = inspect.getsource(fake_broker._probe_root)
    gc_source = inspect.getsource(fake_broker._gc_apply)

    assert "simulate.SimulationRunner" in commit_source and ".commit(plan, token)" in commit_source
    assert "journal_module.classify" in recover_source and "simulate.repair" in recover_source
    assert "acl.capture_acl" in probe_source and "context.root" in probe_source
    assert "lifecycle.apply_gc_plan" in gc_source
    # ...and those are those modules' own public entry points, not private stand-ins.
    assert fake_broker.simulate is simulate
    assert fake_broker.journal_module is journal_module
    assert fake_broker.lifecycle is lifecycle
    assert callable(simulate.SimulationRunner.commit)
    assert callable(journal_module.classify)
    assert callable(lifecycle.apply_gc_plan)


# --------------------------------------------------------------------------------------------- #
# refusal paths, with a real cause
# --------------------------------------------------------------------------------------------- #


def test_a_plan_edited_after_approval_is_refused_as_a_rejection(broker_root, clock) -> None:
    """Refusal (a): the token is bound to a hash the plan no longer has.

    The plan file on disk is edited and **re-hashed**, so the file is internally consistent — the
    check it fails is the one that matters: the approval was signed for a different plan. That lands
    in `verify_approval` as `INVALID_APPROVAL` (exit 4), a refusal about entitlement rather than a
    failure of the operation.
    """

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="4.0.0")
        approval_ref = write_token(broker_root, token)

        tampered = copy.deepcopy(plan)
        tampered["target"]["version"] = "4.0.1"  # the content moved after approval
        tampered["plan_hash"] = plan_hash(tampered)  # ...and the file still agrees with itself
        assert tampered["plan_hash"] != plan["plan_hash"]
        plan_ref = write_plan(broker_root, tampered)

        document = answer("commit_plan", broker_root, clock, plan_ref=plan_ref, approval_ref=approval_ref)

        assert document["status"] == "rejected"
        assert document["reason_code"] == "INVALID_APPROVAL"
        assert REASON_EXIT[document["reason_code"]] == 4
        assert any("different plan hash" in item["detail"] for item in document["evidence"])
        # Nothing moved.
        assert registry.generation == 0
        assert registry.instance(plan["target"]["instance_id"]) is None
    finally:
        registry.close()


def test_a_plan_whose_own_hash_does_not_match_is_refused_as_an_invalid_plan(broker_root, clock) -> None:
    """The variant that never reaches the token: the file contradicts its own `plan_hash`.

    The same story with the edit *not* re-hashed — the runner's first check recomputes the hash and
    compares, so the refusal is `INVALID_PLAN` (exit 7). Kept beside the test above because which of
    the two fires is exactly the kind of thing a reader would otherwise have to guess.
    """

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="4.2.0")
        approval_ref = write_token(broker_root, token)

        tampered = copy.deepcopy(plan)
        tampered["requested_by"] = "somebody-else"
        plan_ref = write_plan(broker_root, tampered)

        document = answer("commit_plan", broker_root, clock, plan_ref=plan_ref, approval_ref=approval_ref)

        assert document["status"] == "rejected"
        assert document["reason_code"] == "INVALID_PLAN"
        assert REASON_EXIT[document["reason_code"]] == 7
        assert any("hash does not match" in item["detail"] for item in document["evidence"])
        assert registry.generation == 0
    finally:
        registry.close()


def test_a_replayed_approval_nonce_is_refused(broker_root, clock) -> None:
    """Refusal (b): the same token is presented twice; the nonce is consumed exactly once.

    The first commit consumes the nonce, and the second request names the same files under a fresh
    `request_id` — a replay, not a retry. `verify_approval` refuses it as `APPROVAL_REPLAYED`
    (exit 4), and the answer is a **returned document**: the harness returned rather than raised.
    """

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="5.0.0")
        plan_ref = write_plan(broker_root, plan)
        approval_ref = write_token(broker_root, token)

        first = answer("commit_plan", broker_root, clock, plan_ref=plan_ref, approval_ref=approval_ref)
        assert first["status"] == "ok"
        assert registry.generation == 1

        replayed = answer(
            "commit_plan",
            broker_root,
            clock,
            request_id="req/harness/0002",
            plan_ref=plan_ref,
            approval_ref=approval_ref,
        )

        assert replayed["status"] == "rejected"
        assert replayed["reason_code"] == "APPROVAL_REPLAYED"
        assert REASON_EXIT[replayed["reason_code"]] == 4
        assert any("already consumed" in item["detail"] for item in replayed["evidence"])
        # The replay changed nothing: one generation, and the transaction that already exists.
        assert registry.generation == 1
        assert registry.transaction(first["transaction_id"]) is not None
    finally:
        registry.close()


def test_gc_apply_refuses_a_payload_that_changed_after_approval(broker_root, clock) -> None:
    """The third refusal shape: an operation allowed to run that broke — `failed`, not `rejected`.

    The admission decision is recomputed at apply time (`caps/lifecycle.py`), so a payload whose tree
    digest moved after the plan was approved is `DIGEST_MISMATCH` (exit 7) and nothing is deleted.
    This is the case the two statuses exist to distinguish: the plan and the token were both fine.
    """

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="3.1.0")
        simulate.SimulationRunner(registry, clock=clock).commit(plan, token)
        instance_id = str(plan["target"]["instance_id"])
        store_dir = Path(broker_root.path) / "store" / instance_id

        lifecycle.retire(registry, instance_id, clock=clock)
        gc_plan = lifecycle.build_gc_plan(
            registry, instance_id, clock=clock, plan_id="plan/gc/harness-0002"
        )
        scoped_token = fake_issuer.issue(gc_plan, clock=clock)

        (store_dir / "fake-tool.bin").write_bytes(b"changed after the plan was approved\n")

        document = answer(
            "gc_apply",
            broker_root,
            clock,
            plan_ref=write_plan(broker_root, gc_plan, name="plan-gc-0002.json"),
            approval_ref=write_token(broker_root, scoped_token, name="approval-gc-0002.json"),
        )

        assert document["status"] == "failed"
        assert document["reason_code"] == "DIGEST_MISMATCH"
        assert REASON_EXIT[document["reason_code"]] == 7
        assert any("changed after the plan was approved" in item["detail"] for item in document["evidence"])
        assert store_dir.is_dir(), "a refused collection deletes nothing"
        assert registry.instance(instance_id)["collected_at"] is None
    finally:
        registry.close()


def test_a_gc_apply_request_without_a_plan_or_approval_cannot_even_be_constructed(
    broker_root, clock
) -> None:
    """The codec already refuses what the operation it stands for could not honour.

    `broker/protocol.py` adds the plan and the approval to `gc_apply` because the published schema is
    silent about that operation while `apply_gc_plan` demands both — so the refusal happens at
    `build_request`, before the harness is reached. Pinned here because the harness's own
    `_plan_and_token` would otherwise be the only place that rule lived.
    """

    with pytest.raises(AirootError) as failure:
        protocol.build_request(
            operation="gc_apply",
            client=copy.deepcopy(CLIENT),
            request_id="req/harness/gc-bare",
        )

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert "plan_ref" in failure.value.message and "approval_ref" in failure.value.message

    # ...and a hand-written request that skips the codec is still answered, not raised.
    document = fake_broker.serve(
        {
            "protocol_version": 1,
            "request_id": "req/harness/gc-hand-written",
            "operation": "gc_apply",
            "client": copy.deepcopy(CLIENT),
        },
        root=broker_root.path,
        clock=clock,
    )
    schema_io.validate_document("broker-response", document)
    assert document["status"] == "rejected"
    assert document["reason_code"] == "INVALID_INPUT"  # the request names neither ref
    assert any("plan_ref" in item["detail"] or "approval_ref" in item["detail"] for item in document["evidence"])


def test_a_missing_plan_document_is_refused_as_not_found(broker_root, clock) -> None:
    """A referenced file that is not there is `NOT_FOUND` (exit 1) — not a schema complaint."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        _plan, token = fresh_plan_and_token(registry, broker_root, clock, version="6.0.0")
        approval_ref = write_token(broker_root, token)

        document = answer(
            "commit_plan",
            broker_root,
            clock,
            plan_ref="state/plans/not-written.json",
            approval_ref=approval_ref,
        )

        assert document["status"] == "rejected"
        assert document["reason_code"] == "NOT_FOUND"
        assert REASON_EXIT[document["reason_code"]] == 1
        assert any(item["kind"] == "evidence" for item in document["evidence"])
    finally:
        registry.close()


def test_a_missing_approval_document_is_refused_as_not_found(broker_root, clock) -> None:
    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, _token = fresh_plan_and_token(registry, broker_root, clock, version="6.1.0")
        plan_ref = write_plan(broker_root, plan)

        document = answer(
            "gc_apply",
            broker_root,
            clock,
            plan_ref=plan_ref,
            approval_ref="state/approvals/not-written.json",
        )

        assert document["status"] == "rejected"
        assert document["reason_code"] == "NOT_FOUND"
        assert REASON_EXIT[document["reason_code"]] == 1
        assert any("no approval document" in item["detail"] for item in document["evidence"])
    finally:
        registry.close()


def test_a_path_that_escapes_the_root_is_refused(broker_root, clock) -> None:
    """The wire path is a root-relative reference, so a traversal is refused rather than joined."""

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        _plan, token = fresh_plan_and_token(registry, broker_root, clock, version="7.0.0")
        approval_ref = write_token(broker_root, token)
        # Hand-written because the request schema's own pattern permits it: it constrains the prefix,
        # not the traversal, so the harness is the layer that has to say no.
        document = fake_broker.serve(
            {
                "protocol_version": 1,
                "request_id": "req/harness/traversal",
                "operation": "commit_plan",
                "plan_ref": "state/plans/../../../escape.json",
                "approval_ref": approval_ref,
                "client": copy.deepcopy(CLIENT),
            },
            root=broker_root.path,
            clock=clock,
        )

        schema_io.validate_document("broker-response", document)
        assert document["status"] == "rejected"
        assert document["reason_code"] == "PATH_ESCAPES_ROOT"
        assert REASON_EXIT[document["reason_code"]] == 8
    finally:
        registry.close()


def test_a_transaction_with_no_journal_is_refused_not_guessed(broker_root, clock) -> None:
    """Recovery never invents the missing steps: a journal that is not there is `JOURNAL_TRUNCATED`."""

    document = answer(
        "recover_transaction", broker_root, clock, transaction_id="tx/fake-tool/nothing"
    )

    assert document["status"] == "failed"
    assert document["reason_code"] == "JOURNAL_TRUNCATED"
    assert REASON_EXIT[document["reason_code"]] == 6
    # The transaction the request named is carried back, so the refusal points at something.
    assert document["transaction_id"] == "tx/fake-tool/nothing"
    assert document["state"] is None


def test_a_malformed_request_is_refused_rather_than_raised(broker_root, clock) -> None:
    """The request is *input*: a document the published schema refuses comes back as a refusal."""

    document = fake_broker.serve(
        {"protocol_version": 1, "request_id": "req/harness/bad", "operation": "commit_plan"},
        root=broker_root.path,
        clock=clock,
    )

    schema_io.validate_document("broker-response", document)
    assert document["status"] == "rejected"
    assert document["reason_code"] == "INVALID_INPUT"
    assert REASON_EXIT[document["reason_code"]] == 8
    assert any(item["kind"] == "reason" for item in document["evidence"])


def test_an_operation_the_harness_does_not_carry_is_refused_by_name(broker_root, clock) -> None:
    """Defence in depth: the schema enumerates the operations, and the dispatch table says so too."""

    document = fake_broker.serve(
        {
            "protocol_version": 1,
            "request_id": "req/harness/unknown",
            "operation": "reconcile",
            "client": copy.deepcopy(CLIENT),
        },
        root=broker_root.path,
        clock=clock,
    )

    schema_io.validate_document("broker-response", document)
    assert document["status"] == "rejected"
    assert document["reason_code"] == "INVALID_INPUT"
    assert any("reconcile" in item["detail"] for item in document["evidence"])


def test_a_root_that_cannot_be_opened_is_refused_as_a_document(root_dir: Path, clock) -> None:
    """No marker, no answer — and still an answer, because booting the root is part of the verdict."""

    document = fake_broker.serve(
        request("probe_root"), root=Path(root_dir) / "not-a-root", clock=clock
    )

    schema_io.validate_document("broker-response", document)
    assert document["status"] == "rejected"
    assert document["reason_code"] in {"ROOT_NOT_RESOLVED", "ROOT_MARKER_MISSING"}
    assert REASON_EXIT[document["reason_code"]] in {1, 8}
    assert document["security_mode"] == "policy_only"


# --------------------------------------------------------------------------------------------- #
# User compatibility mode, and the guard in the other direction
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("operation", protocol.OPERATIONS)
def test_every_answer_is_user_compatibility_mode(broker_root, clock, operation) -> None:
    """`policy_only` + `same_user_can_bypass`, always — an in-process binding has no ACL to claim.

    Every operation is really served here (not merely constructed), because the property under test
    belongs to the answer. Whether each one *succeeds* is beside the point and asserted elsewhere.
    """

    document = fake_broker.serve(
        request(operation, **_references_for(broker_root, clock, operation)),
        root=broker_root.path,
        clock=clock,
    )

    schema_io.validate_document("broker-response", document)
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"
    assert document["security_mode"] == fake_broker.HARNESS_SECURITY_MODE
    assert document["enforcement"] == fake_broker.HARNESS_ENFORCEMENT


def test_the_harness_refuses_to_answer_as_protected_machine(broker_root, clock) -> None:
    """Draft §E1's rule: it may not **relabel** itself into a boundary it does not have.

    Answering `protected_machine` / `acl_enforced` would assert an ACL on the protected zone and a
    separate elevated process — neither exists here — so the request is refused, with the requested
    mode named, and nothing is executed: the guard runs before the operation does.
    """

    refusal = fake_broker.refusal_for_mode("protected_machine")
    assert isinstance(refusal, AirootError)
    assert refusal.reason_code == "PRIVILEGE_REQUIRED"
    assert refusal.exit_code == 5
    assert "protected_machine" in refusal.message
    assert fake_broker.refusal_for_mode("policy_only") is None

    document = fake_broker.serve(
        request("probe_root"),
        root=broker_root.path,
        clock=clock,
        security_mode="protected_machine",
    )

    schema_io.validate_document("broker-response", document)
    assert document["status"] == "rejected"
    assert document["reason_code"] == "PRIVILEGE_REQUIRED"
    assert REASON_EXIT[document["reason_code"]] == 5
    # Refused, never relabelled.
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"
    # And it is a returned document, not an exception.
    assert any("protected_machine" in item["detail"] for item in document["evidence"])


def test_the_compatibility_block_is_exactly_the_two_frozen_constants() -> None:
    assert fake_broker.compatibility_block() == {
        "security_mode": "policy_only",
        "enforcement": "same_user_can_bypass",
    }
    assert fake_broker.HARNESS_SECURITY_MODE == "policy_only"
    assert fake_broker.HARNESS_ENFORCEMENT == "same_user_can_bypass"
    # The published schema's own vocabularies are the authority for both values.
    common = schema_io.load_schema("common.schema.json")
    assert fake_broker.HARNESS_SECURITY_MODE in common["$defs"]["securityMode"]["enum"]
    assert fake_broker.HARNESS_ENFORCEMENT in common["$defs"]["enforcement"]["enum"]


# --------------------------------------------------------------------------------------------- #
# evidence shape, self-validation, and the harness's own honesty
# --------------------------------------------------------------------------------------------- #


def test_evidence_is_an_array_of_objects(broker_root, clock) -> None:
    """`{kind, detail}` objects — **not** the error envelope's list of strings.

    This is the trap `broker/protocol.py` documents, so it is asserted on a real answer rather than
    trusted: every item is an object with a non-empty `kind` and `detail`, the optional keys stay
    inside the schema's set, and a string-array evidence is refused by the response schema.
    """

    document = answer("probe_root", broker_root, clock)

    assert isinstance(document["evidence"], list) and document["evidence"]
    for item in document["evidence"]:
        assert isinstance(item, dict), f"evidence items are objects, not strings: {item!r}"
        assert set(item) <= {"kind", "detail", "path", "digest"}
        assert isinstance(item["kind"], str) and item["kind"]
        assert isinstance(item["detail"], str) and item["detail"]
    digests = [item["digest"] for item in document["evidence"] if "digest" in item]
    assert digests, "the probe observed a DACL, so it has a digest to carry"
    assert all(isinstance(value, str) and value.startswith("sha256:") for value in digests)
    # The neighbouring shape is refused, which is the whole reason this test exists.
    wrong_shape = dict(document, evidence=["a string, not an object"])
    assert schema_io.errors_for("broker-response", wrong_shape) != []


def test_every_answer_self_validates_against_the_published_response_schema(broker_root, clock) -> None:
    """The core's rule (AGENTS.md §7): the outward document is checked before it is returned."""

    seen: list[str] = []
    real = fake_broker.schema_io.errors_for

    def recorder(name: str, document: object) -> list[str]:
        seen.append(name)
        return real(name, document)

    fake_broker.schema_io.errors_for = recorder  # type: ignore[assignment]
    try:
        document = fake_broker.serve(request("probe_root"), root=broker_root.path, clock=clock)
    finally:
        fake_broker.schema_io.errors_for = real  # type: ignore[assignment]

    assert document["status"] == "ok"
    assert "broker-response" in seen


def test_a_response_this_build_cannot_emit_validly_is_a_harness_defect(broker_root, clock) -> None:
    """A document the schema refuses is a bug, not a verdict — and it must not be returned as one."""

    with pytest.raises(fake_broker.HarnessDefect) as failure:
        fake_broker._answer(
            request("probe_root"),
            reason_code="SUCCESS",
            transaction_id=None,
            state=None,
            evidence=["not an object"],  # the trap this harness must not fall into
            retryable=False,
        )

    assert "broker-response rejected" in str(failure.value)


def test_a_failure_with_no_registered_code_is_a_harness_defect() -> None:
    """'Do not invent a code': an unregistered one has no exit code it could honestly carry."""

    with pytest.raises(fake_broker.HarnessDefect):
        fake_broker._exit_code_for("NO_SUCH_REASON_CODE")

    assert fake_broker._exit_code_for("INVALID_APPROVAL") == 4
    assert fake_broker._exit_code_for("NOT_IMPLEMENTED") == 1


def test_a_long_message_is_truncated_rather_than_returned_over_length() -> None:
    """`common.$defs.evidence.detail` is capped at 2048; a path-heavy OSError can exceed it."""

    item = fake_broker.evidence_object("failure", "x" * 5000)
    assert len(item["detail"]) == fake_broker.MAX_DETAIL
    assert item["detail"].endswith("[truncated]")
    schema_io.validate_document(
        "broker-response",
        {
            "schema_version": 1,
            "request_id": "req/harness/long",
            "status": "failed",
            "transaction_id": None,
            "state": None,
            "reason_code": "INSTALL_IO_FAILED",
            "evidence": [item, fake_broker.evidence_object("path", "y" * 5000, path="z" * 5000)],
            "retryable": True,
            **fake_broker.compatibility_block(),
        },
    )


def test_every_refusal_code_the_harness_can_emit_is_registered() -> None:
    """Both directions of the code table: a code with no exit code, and a status with no meaning."""

    for code in fake_broker.REJECTION_CODES:
        assert code in REASON_EXIT, code
    assert fake_broker._status_class("APPROVAL_REPLAYED") == "rejected"
    assert fake_broker._status_class("RECOVERY_REQUIRED") == "recovery_required"
    assert fake_broker._status_class("DIGEST_MISMATCH") == "failed"
    # Every status the harness can produce is one the published schema enumerates.
    schema = schema_io.load_schema("broker-response")
    statuses = set(schema["properties"]["status"]["enum"])
    produced = {fake_broker._status_class(code) for code in fake_broker.REJECTION_CODES}
    produced |= {fake_broker._status_class("SUCCESS"), fake_broker._status_class("DIGEST_MISMATCH")}
    assert produced <= statuses


def test_protocol_parse_response_reads_the_harness_answers(broker_root, clock) -> None:
    """The producer and the consumer meet: an ok answer round-trips, and a refusal comes back as the
    harness's own verdict rather than as `INVALID_INPUT`."""

    ok = answer("probe_root", broker_root, clock)
    assert protocol.parse_response(ok) == ok

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        _plan, token = fresh_plan_and_token(registry, broker_root, clock, version="9.0.0")
        approval_ref = write_token(broker_root, token)
        refused = answer(
            "commit_plan",
            broker_root,
            clock,
            plan_ref="state/plans/absent.json",
            approval_ref=approval_ref,
        )
    finally:
        registry.close()

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(refused)

    assert failure.value.reason_code == refused["reason_code"] == "NOT_FOUND"
    assert failure.value.exit_code == 1


def _references_for(broker_root, clock, operation: str) -> dict:
    """Whatever `operation` needs, written to the root so a request is buildable *and* servable.

    The compatibility-mode test needs every operation to reach the harness rather than merely be
    constructed, so the referenced documents are produced here.
    """

    registry = Registry.open(broker_root.path, clock=clock)
    try:
        plan, token = fresh_plan_and_token(registry, broker_root, clock, version="10.0.0")
        plan_ref = write_plan(broker_root, plan, name=f"plan-{operation}.json")
        approval_ref = write_token(broker_root, token, name=f"approval-{operation}.json")
    finally:
        registry.close()

    if operation in {"commit_plan", "gc_apply"}:
        return {"plan_ref": plan_ref, "approval_ref": approval_ref}
    if operation == "recover_transaction":
        return {"transaction_id": "tx/fake-tool/absent"}
    return {}
def test_a_probe_answer_carries_no_machine_identity(broker_root, clock, monkeypatch) -> None:
    """§112: a returned document must not carry a SID (AGENTS.md §9).

    `probe_root` observes a DACL, and a DACL is full of SIDs. Measured: the answer carried the sorted
    trustee list as `detail` text while the handler's own docstring said the SIDs were left out — a
    document disagreeing with the sentence that describes it, and a machine fingerprint in a document
    whose remote is public. A substring rule is enough here because a SID is unmistakable (`S-1-`),
    and it is checked against the whole answer rather than one field, so a new field cannot smuggle one
    back in.

    The snapshot is injected because **this host reports no trustee SIDs at all** (measured: restoring
    the leaky field left the guard green), so a check that only read the real ACL would have had no red
    direction. The second assertion keeps the first from passing because the field vanished.
    """

    from airoot.caps import acl

    snapshot = acl.AclSnapshot(
        path=str(broker_root.path),
        owner="S-1-5-21-1000",
        entries=(acl.AclEntry(0, 0, 0x1F01FF, "S-1-5-21-1000"),),
        dacl_present=True,
    )
    monkeypatch.setattr(acl, "capture_acl", lambda path: snapshot)

    document = answer("probe_root", broker_root, clock)

    assert "S-1-" not in json.dumps(document), "a returned document carried a machine identity"
    assert "trustee" in json.dumps(document), "the observation must still report how many it saw"
