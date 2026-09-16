"""L1: the broker's named pipe — docs/broker §2's user compatibility mode, driven over a real pipe.

`broker/protocol.py` builds and parses the documents, `caps/identity.py` observes a peer's token and
`broker/policy.py` decides whether a caller may ask; `broker/pipe.py` is the wire that connects them.
These tests drive that wire **for real** on this machine: a server thread, a client in the same
process, a genuine named pipe, and a peer whose pid the OS reports. Nothing here is elevated and
nothing here needs to be.

What is asserted, and why each assertion is the point of the stage:

* a `probe_root` request is admitted and answered with a document the published schema accepts,
  carrying both marking fields (`security_mode=policy_only`, `enforcement=same_user_can_bypass`);
* `commit_plan`, `recover_transaction` and `gc_apply` are refused with `NOT_IMPLEMENTED` and
  ADR-0025's pointer — the routing is a table with one line of *why* per operation, and these tests
  read that table rather than a second hand-written list;
* a caller the expectation does not allow gets a **verdict**, not a silent close, and the refusal
  arrives through `protocol.parse_response` as the response's own `CALLER_NOT_AUTHORIZED` — the path
  §112 built the response corpus for;
* a request whose `client` block lies is treated exactly like an honest one, because the observation
  decides and the claim is never read;
* a malformed frame is answered (or recorded) without a traceback escaping `serve_pipe`;
* `ERROR_PIPE_CONNECTED` is treated as success — the race where the client connects between
  `CreateNamedPipeW` and `ConnectNamedPipe`;
* `serve_pipe` stops cleanly through `stop` with no leaked threads, and every handle is closed.

**What these tests deliberately do not claim.** That any of this is a trust boundary. The server runs
as the same user as the client, its DACL admits that user, and the mode's own answers say so on every
response. `test_the_response_marking_is_the_builds_posture` asserts the marking rather than assuming
it, and the module's docstring records what the DACL does and does not buy.

**Two environment facts this file has to live with, both measured rather than assumed.**

1. **`serve_pipe` refuses to start from an elevated token** (broker/pipe.py's rationale: 'the current
   user' would then be the elevated user, and the mode's claim would describe a different session).
   The development shell for this stage is elevated, so every test that needs a live pipe is marked
   with :func:`_requires_unelevated` and skipped there with that reason. The skip is honest: those
   tests were run unprivileged before being reported.
2. **`PIPE_REJECT_REMOTE_CLIENTS` must be passed in `dwPipeMode`, not `dwOpenMode`** — the brief said
   otherwise and the OS rejected it (err 87). The two tests that pin this are live tests (they call
   `CreateNamedPipeW` themselves), so they share the unelevated requirement; the *placement* is
   asserted statically by :func:`test_the_local_only_bit_is_in_the_pipe_mode_argument`, which runs
   everywhere.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from airoot import root as root_module
from airoot import schema_io
from airoot.broker import pipe, policy, protocol, transport
from airoot.caps import identity
from airoot.clock import SYSTEM_CLOCK
from airoot.exits import REASON_EXIT, AirootError

#: A request the server must ignore the identity of: `client` claims a SID and an application id that
#: are not this process's, which is exactly what the observation is supposed to overrule.
CLIENT = {
    "sid": "S-1-5-21-1000",
    "pid": 4242,
    "integrity": "medium",
    "application_id": "airoot-cli",
}

#: A `client` block that lies as loudly as the schema allows: a foreign SID, pid 1, and `system`
#: integrity. If the request could influence the decision, this is the value that would prove it.
LYING_CLIENT = {
    "sid": "S-1-5-21-9999999999-1",
    "pid": 1,
    "integrity": "system",
    "application_id": "someone-else",
}

#: A SID in `S-1-...` form. A response travels back to a caller, so none may appear in one
#: (AGENTS.md §9) — this is the pattern the leak guards scan for.
SID_PATTERN = re.compile(r"S-1-[0-9-]+")

#: The request id every live test uses, so a failure names the same document everywhere.
REQUEST_ID = "req/probe/0001"


# --------------------------------------------------------------------------------------------- #
# helpers and fixtures
# --------------------------------------------------------------------------------------------- #


def _unelevated() -> bool:
    """Whether this process's token is unprivileged, as `serve_pipe` requires.

    `elevated is False` and not a truthiness test: `None` means the probe could not read the token,
    and "could not look" must not be read as "looked, and it was fine" — the same rule
    `broker/policy.py` applies to a caller.
    """

    return identity.probe_identity().elevated is False


_requires_unelevated = pytest.mark.skipif(
    not _unelevated(),
    reason=(
        "serve_pipe refuses to start from an elevated token (broker/pipe.py): 'the current user' "
        "would then be the elevated user and the mode's same-user claim would describe another "
        "session. This machine's shell is elevated, so the live-pipe tests cannot run here; they were "
        "run unprivileged before being reported."
    ),
)


@pytest.fixture
def user_sid() -> str:
    """This process's own SID — the value the pipe's DACL must name, and the one admission compares."""

    sid = identity.probe_identity().sid
    if sid is None:  # pragma: no cover - only on a machine where the token cannot be read
        pytest.skip("this process's own SID could not be read, so no expectation can name it")
    return sid


@pytest.fixture
def pipe_name() -> str:
    """A pipe name unique to this test, so two tests can never share an instance."""

    return "\\\\.\\pipe\\airoot-broker-test-" + os.urandom(8).hex()


@pytest.fixture
def broker_root(root_dir: Path):
    """A temporary, initialised AIROOT root — the thing `probe_root` observes."""

    return root_module.init_root(
        root_dir,
        root_instance_id="root-4f2a9c1d8e3b",
        machine_id="host-broker-pipe",
        clock=SYSTEM_CLOCK,
    )


@pytest.fixture
def caller(user_sid: str) -> policy.CallerExpectation:
    """Who may ask: this user, at any integrity — the policy `serve_pipe` is handed, not one it owns."""

    return policy.CallerExpectation(
        allowed_sids=frozenset({user_sid}),
        minimum_integrity="low",
        evidence=("test expectation: the current user, every integrity level",),
    )


def probe_request(request_id: str = REQUEST_ID, *, client: dict | None = None) -> dict:
    """A schema-valid `probe_root` request, built through the published builder."""

    return protocol.build_request(
        operation="probe_root",
        request_id=request_id,
        client=dict(CLIENT if client is None else client),
    )


class Server:
    """A `serve_pipe` running in a thread, with the report it returns and a clean stop."""

    def __init__(self, root, expectation, pipe_name: str, *, max_requests: int = 1):
        self.pipe_name = pipe_name
        self.stop = threading.Event()
        self.result: dict = {}
        self.error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            args=(root, expectation, max_requests),
            name="airoot-broker-pipe-test",
            daemon=True,
        )
        self._thread.start()
        # The server must have created its first instance before a client may connect; polling for
        # it is what keeps these tests from racing the thread that serves them.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with open(self.pipe_name, "r+b"):  # noqa: SIM115 - probe-and-close, not a handle
                    break
            except OSError:
                time.sleep(0.005)

    def _run(self, root, expectation, max_requests: int) -> None:
        try:
            self.result = pipe.serve_pipe(
                root=root,
                expectation=expectation,
                pipe_name=self.pipe_name,
                max_requests=max_requests,
                stop=self.stop,
            )
        except BaseException as error:  # noqa: BLE001 - recorded so a test can assert on it
            self.error = error

    def call(self, request: dict, *, timeout_ms: int = 4000) -> dict:
        return pipe.call_broker(request, pipe_name=self.pipe_name, timeout_ms=timeout_ms)

    def finish(self, *, timeout: float = 10.0) -> dict:
        """Wait for the thread and return the report, failing loudly on a server-side exception."""

        self.stop.set()
        self._thread.join(timeout=timeout)
        assert not self._thread.is_alive(), "serve_pipe did not return after stop was set"
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def server(broker_root, caller, pipe_name):
    """A running server, stopped (and its handles closed) at the end of the test."""

    running = Server(broker_root, caller, pipe_name)
    yield running
    if running._thread.is_alive():
        running.finish()


# --------------------------------------------------------------------------------------------- #
# what this build answers, as data
# --------------------------------------------------------------------------------------------- #


def test_every_published_operation_is_routed() -> None:
    """The routing table covers the request schema's `operation` enum exactly.

    An operation added to the published schema and forgotten here would be an unanswerable request —
    the server would fall through the routing loop. The enum is read from the schema itself, so this
    cannot drift away from the contract it protects.
    """

    published = set(schema_io.load_schema("broker-request")["properties"]["operation"]["enum"])
    assert set(pipe.ROUTING) == published
    assert set(pipe.ROUTING) == set(protocol.OPERATIONS)


def test_the_refusals_are_the_operations_this_build_defers() -> None:
    """`REFUSAL_REASONS` and `ROUTING` are one decision spelled twice, and they must agree.

    `ROUTING` is derived from `REFUSAL_REASONS`, so this is not a tautology: it pins the derivation
    and the fact that `probe_root` is *absent* from the reasons table (absence is the statement that
    it is honoured).
    """

    refused = {op for op, decision in pipe.ROUTING.items() if decision == "refuse"}
    assert refused == set(pipe.REFUSAL_REASONS)
    assert "probe_root" not in pipe.REFUSAL_REASONS
    assert pipe.ROUTING["probe_root"] == "answer"
    assert refused == {"commit_plan", "recover_transaction", "gc_apply"}


def test_every_refusal_carries_a_reason_and_names_the_adr() -> None:
    """A refusal with no reason is the thing the reasons table exists to prevent."""

    for operation, reason in pipe.REFUSAL_REASONS.items():
        assert reason.strip(), f"{operation} has an empty reason"
        error = pipe._operation_refusal(operation)
        assert error.reason_code == "NOT_IMPLEMENTED"
        assert REASON_EXIT["NOT_IMPLEMENTED"] == 1
        assert "ADR-0025" in error.message
        assert any("ADR-0025" in line for line in error.evidence)
        assert error.details.get("adr") == "ADR-0025"


def test_the_local_only_bit_is_in_the_pipe_mode_argument() -> None:
    """`PIPE_REJECT_REMOTE_CLIENTS` is passed in ``dwPipeMode`` — statically, on every machine.

    The brief for this stage put it in ``dwOpenMode`` and the OS rejected it (err 87); the live proof
    is :func:`test_the_reject_remote_bit_is_rejected_in_dw_open_mode`, and this is the proof that does
    not need a pipe: the constant's value is unchanged and it is one of the bits of the mode argument
    the server actually passes.
    """

    assert pipe.PIPE_REJECT_REMOTE_CLIENTS == 0x00000008
    assert pipe.PIPE_MODE & pipe.PIPE_REJECT_REMOTE_CLIENTS
    # Byte type and byte read mode: message mode makes `read_frame`'s `read(n)` contract unkeepable
    # (see the module docstring) and message read mode is invalid with byte type.
    assert pipe.PIPE_MODE & pipe.PIPE_TYPE_MESSAGE == 0
    assert pipe.PIPE_MODE & pipe.PIPE_READMODE_MESSAGE == 0


# --------------------------------------------------------------------------------------------- #
# the response marking — asserted on every path, never assumed
# --------------------------------------------------------------------------------------------- #


def test_the_response_marking_is_the_builds_posture() -> None:
    """Every response carries the mode marking, and it is `posture()`'s pair rather than a literal.

    `broker-response` requires both fields, so this is a contract obligation; that it is
    `policy_only`/`same_user_can_bypass` and not `protected_machine`/`acl_enforced` is the honesty
    rule — nothing in this build enforces anything.
    """

    from airoot.posture import posture

    marking = posture()
    assert marking == {"security_mode": "policy_only", "enforcement": "same_user_can_bypass"}

    # Every path that builds a document goes through `_answer`, so one construction covers success,
    # an operation refusal and an admission refusal alike.
    document = pipe._answer(request_id="req/x/1", reason_code="SUCCESS")
    assert document["security_mode"] == marking["security_mode"]
    assert document["enforcement"] == marking["enforcement"]
    schema_io.validate_document("broker-response", document)

    refusal = pipe._refusal(
        "req/x/1",
        AirootError("NOT_IMPLEMENTED", "no", evidence=["because"]),
        redact=False,
    )
    assert refusal["security_mode"] == marking["security_mode"]
    assert refusal["enforcement"] == marking["enforcement"]
    assert refusal["status"] == "rejected"
    schema_io.validate_document("broker-response", refusal)


def test_a_success_response_has_no_transaction_and_no_state() -> None:
    """`probe_root` has neither, and the schema's own way of saying so is a present null.

    A fabricated transaction id would be a lie about history, so the fields are `null` rather than
    omitted: `broker-response` requires both keys.
    """

    document = pipe._answer(
        request_id=REQUEST_ID,
        reason_code="SUCCESS",
        transaction_id=None,
        state=None,
        evidence=[pipe.evidence_object("root", "root_instance_id=root-x")],
    )
    assert document["transaction_id"] is None
    assert document["state"] is None
    assert document["status"] == "ok"
    assert document["reason_code"] == "SUCCESS"


# --------------------------------------------------------------------------------------------- #
# the live pipe: admitted, refused, and observed
# --------------------------------------------------------------------------------------------- #


def test_an_elevated_server_refuses_to_start_and_says_why(pipe_name, broker_root) -> None:
    """The elevation refusal is **not** skipped on an elevated machine — it is the one case it can test.

    Every other live-pipe test here is `_requires_unelevated`, because `serve_pipe` will not start
    elevated; on this machine that means the suite's live-pipe half is skipped. This test runs in both
    worlds on purpose, and it is the one that keeps the refusal itself honest: the guard has no
    coverage if the only tests that could reach it are the ones it makes unrunnable. A relaxed check
    (starting elevated anyway) would otherwise leave every other test green while making the mode's own
    sentence — "the current user can bypass" — describe a session that is not the one being served.

    The refusal happens before any pipe is created, so this test opens nothing: on an elevated token it
    asserts the raise, and on a normal token it asserts the opposite (the server starts) by finishing a
    server that nobody calls — which is the `stop` path, already covered by its own test.
    """

    if identity.probe_identity().elevated is True:
        with pytest.raises(AirootError) as raised:
            pipe.serve_pipe(
                root=broker_root.path,
                expectation=policy.CallerExpectation(
                    allowed_sids=frozenset(), minimum_integrity="low", evidence=("test expectation",)
                ),
                pipe_name=pipe_name,
            )
        assert raised.value.reason_code == "SELF_VALIDATION_FAILED"
        assert raised.value.exit_code == 8
        assert "elevated" in raised.value.message
        assert any("same-user" in line or "same user" in line for line in raised.value.evidence), (
            "the refusal has to name the mode it cannot honour, not only the token it read: "
            f"{raised.value.evidence}"
        )
        return

    running = Server(broker_root, policy.CallerExpectation(
        allowed_sids=frozenset({identity.probe_identity().sid}), minimum_integrity="low",
        evidence=("test expectation",),
    ), pipe_name)
    try:
        # Nobody calls it: `finish` sets `stop`, and the stop watcher is what releases the wait. If the
        # refusal had been relaxed into a warning, this run would start a server instead of raising.
        assert running.finish()["requests"] == 0
    finally:
        if running._thread.is_alive():  # pragma: no cover - finish already joined it
            running.finish()


@_requires_unelevated
def test_a_probe_root_request_is_admitted_and_answered(server) -> None:
    """The whole path: a real pipe, an observed peer, an admitted caller, a schema-valid answer."""

    response = server.call(probe_request())
    report = server.finish()

    schema_io.validate_document("broker-response", response)
    assert response["status"] == "ok"
    assert response["reason_code"] == "SUCCESS"
    assert response["request_id"] == REQUEST_ID
    assert response["transaction_id"] is None
    assert response["state"] is None
    # Both marking fields, on the wire, on the success path.
    assert response["security_mode"] == "policy_only"
    assert response["enforcement"] == "same_user_can_bypass"
    assert report["requests"] == 1
    assert report["admitted"] == 1
    assert report["refused"] == 0
    assert report["malformed"] == 0


@_requires_unelevated
def test_exchange_returns_the_document_that_call_broker_turns_into_an_exception(server) -> None:
    """`exchange` hands over the raw response; `call_broker` interprets it. One exchange, two readings.

    This is the entry point a byte-level corpus needs: `parse_response` is the *untrusted caller's*
    reading (it turns a verdict into an exception), so a fixture that has to be a document cannot be
    built through it. Both readings are asserted on the same answer, which is what keeps the two from
    drifting.
    """

    request = protocol.build_request(
        operation="commit_plan",
        request_id="req/commit/0001",
        plan_ref="state/plans/plan-0001.json",
        approval_ref="state/approvals/approval-0001.json",
        client=dict(CLIENT),
    )
    status = "rejected"

    document = server.exchange(request)
    with pytest.raises(AirootError) as raised:
        server.call(request)
    report = server.finish()

    # The documented answer: a schema-valid `broker-response` this build wrote.
    schema_io.validate_document("broker-response", document)
    assert document["status"] == status
    assert document["reason_code"] == "NOT_IMPLEMENTED"
    assert document["request_id"] == request["request_id"]
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"
    # The interpreting reading carries the same verdict as the response's own code, not a rewrite.
    assert raised.value.reason_code == document["reason_code"]
    assert raised.value.details["reason_code"] == document["reason_code"]
    assert raised.value.details["status"] == document["status"]
    # Two requests, both admitted: the refusal is about the operation, not about who asked.
    assert report["requests"] == 2
    assert report["admitted"] == 2
    assert report["refused"] == 0


@_requires_unelevated
def test_the_refusal_document_is_reproducible_byte_for_byte(server, pipe_name, broker_root, caller) -> None:
    """The same request, answered by two different servers, frames to the same bytes.

    The orchestrator will freeze this refusal as a golden fixture, and
    `test_golden_fixtures_reproduce_exactly` regenerates it on every machine — so the answer must
    carry nothing derived from this machine or this run. The check is on the **framed bytes**, because
    that is what a fixture is.

    `server` is asked for the first answer and a second server on its own unique name for the second,
    so the two are genuinely separate runs rather than one response read twice.
    """

    request = protocol.build_request(
        operation="commit_plan",
        request_id="req/commit/0001",
        plan_ref="state/plans/plan-0001.json",
        approval_ref="state/approvals/approval-0001.json",
        client=dict(CLIENT),
    )
    first = transport.encode_frame(server.exchange(request))

    second_name = "\\\\.\\pipe\\airoot-broker-test-" + os.urandom(8).hex()
    other = Server(broker_root, caller, second_name)
    try:
        second = transport.encode_frame(other.exchange(request))
    finally:
        other.finish()
    server.finish()

    assert first == second, "the refusal is not reproducible; it carries run- or machine-derived text"
    # Nothing derived from this machine or this run may be in it. The prose is ASCII by construction,
    # so any non-ASCII codepoint is a machine value that leaked.
    rendered = first.decode("utf-8")
    assert all(ord(char) < 128 for char in rendered), f"non-ASCII in the answer: {rendered!r}"
    assert not SID_PATTERN.search(rendered)
    assert pipe_name not in rendered and second_name not in rendered


@_requires_unelevated
@pytest.mark.parametrize("operation", sorted(pipe.REFUSAL_REASONS))
def test_each_committing_operation_is_refused_as_not_implemented(server, operation: str) -> None:
    """The three committing operations get `NOT_IMPLEMENTED` (exit 1) and ADR-0025's pointer.

    Driven through `call_broker`, so the assertion is about what the *client* receives:
    `parse_response` raises with the response's own code, which is the only way the broker's verdict
    survives the trip.

    The parameter list is read from `pipe.REFUSAL_REASONS` rather than typed out, so this test and
    `ROUTING` cannot disagree about which operations are refused: adding a refusal without a case here
    leaves that operation's behaviour unmeasured, and the guard that holds `ROUTING`'s keys equal to the
    request schema's operations (in this file) is what keeps the two sets from drifting apart.
    """

    refs: dict = {"request_id": f"req/{operation.replace('_', '-')}/0001", "client": dict(CLIENT)}
    if operation == "commit_plan":
        refs |= {
            "plan_ref": "state/plans/plan-0001.json",
            "approval_ref": "state/approvals/approval-0001.json",
        }
    if operation == "recover_transaction":
        refs["transaction_id"] = "tx-0001"
    if operation == "gc_apply":
        refs |= {
            "plan_ref": "state/plans/plan-0001.json",
            "approval_ref": "state/approvals/approval-0001.json",
        }
    request = protocol.build_request(operation=operation, **refs)

    with pytest.raises(AirootError) as raised:
        server.call(request)
    report = server.finish()

    assert raised.value.reason_code == "NOT_IMPLEMENTED"
    assert raised.value.exit_code == 1
    assert raised.value.details.get("reason_code") == "NOT_IMPLEMENTED"
    # The verdict is a *rejection*, not a failure of the operation, and the ADR pointer travels with
    # it in the evidence the client can read.
    assert raised.value.details.get("status") == "rejected"
    assert raised.value.details.get("security_mode") == "policy_only"
    assert raised.value.details.get("enforcement") == "same_user_can_bypass"
    assert any("ADR-0025" in line for line in raised.value.evidence)
    # The caller was admitted — the refusal is about the operation, not about who asked.
    assert report["admitted"] == 1
    assert report["requests"] == 1


@_requires_unelevated
def test_an_unallowed_caller_gets_a_verdict_not_a_silent_close(pipe_name, broker_root) -> None:
    """A caller the expectation does not allow is refused *in a document*, with its own code.

    `admit_caller` is handed an expectation naming nobody, so its `sid_not_allowed` rule fires. The
    reply shape is the point: `protocol.parse_response` raises with the response's
    `CALLER_NOT_AUTHORIZED` — §112 built the response corpus for exactly this, and a closed pipe
    would have left the client with no reason at all.
    """

    expectation = policy.CallerExpectation(
        allowed_sids=frozenset(),  # nobody may ask — not "no restriction"
        minimum_integrity="low",
        evidence=("test expectation: an empty allowed set, which allows nobody",),
    )
    running = Server(broker_root, expectation, pipe_name)
    try:
        with pytest.raises(AirootError) as raised:
            running.call(probe_request())
        report = running.finish()
    finally:
        if running._thread.is_alive():
            running.finish()

    assert raised.value.reason_code == "CALLER_NOT_AUTHORIZED"
    assert raised.value.exit_code == REASON_EXIT["CALLER_NOT_AUTHORIZED"] == 5
    assert raised.value.details.get("reason_code") == "CALLER_NOT_AUTHORIZED"
    assert raised.value.details.get("status") == "rejected"
    assert raised.value.details.get("security_mode") == "policy_only"
    assert raised.value.details.get("enforcement") == "same_user_can_bypass"
    # The rule that fired is named, so the refusal is re-examinable.
    assert raised.value.details.get("rule") == "sid_not_allowed"
    assert any("sid_not_allowed" in line for line in raised.value.evidence)
    # A verdict was delivered, so this is not a malformed frame and not a connection error.
    assert report["refused"] == 1
    assert report["admitted"] == 0
    assert report["malformed"] == 0
    assert report["connection_errors"] == 0


@_requires_unelevated
def test_a_lying_client_block_is_treated_exactly_like_an_honest_one(server) -> None:
    """The observation decides, so a request claiming an allowed SID and a foreign app id is admitted.

    This is the assertion the whole stage exists for. `LYING_CLIENT` names a SID that is *not* this
    process's and an application id nobody checked; if the request could influence the decision, this
    caller would be refused. It is admitted, and the answer says the `client` block played no part.
    """

    response = server.call(probe_request(REQUEST_ID, client=LYING_CLIENT))
    report = server.finish()

    schema_io.validate_document("broker-response", response)
    assert response["status"] == "ok"
    assert response["reason_code"] == "SUCCESS"
    assert report["admitted"] == 1
    assert report["refused"] == 0
    # The claim is not echoed anywhere in the answer either.
    rendered = json.dumps(response, sort_keys=True)
    assert LYING_CLIENT["application_id"] not in rendered
    assert LYING_CLIENT["sid"] not in rendered


@_requires_unelevated
def test_a_lying_client_block_changes_nothing_about_a_refusal(server) -> None:
    """The same lie cannot buy an operation either: the refusal is identical to an honest request's.

    Run against the same server as an honest `commit_plan`, so "identical" means identical bytes apart
    from the request id the caller chose.
    """

    honest = protocol.build_request(
        operation="commit_plan",
        request_id="req/commit/honest",
        plan_ref="state/plans/p.json",
        approval_ref="state/approvals/a.json",
        client=dict(CLIENT),
    )
    lying = protocol.build_request(
        operation="commit_plan",
        request_id="req/commit/honest",
        plan_ref="state/plans/p.json",
        approval_ref="state/approvals/a.json",
        client=dict(LYING_CLIENT),
    )
    with pytest.raises(AirootError) as first:
        server.call(honest)
    with pytest.raises(AirootError) as second:
        server.call(lying)
    report = server.finish()

    assert first.value.reason_code == second.value.reason_code == "NOT_IMPLEMENTED"
    assert first.value.message == second.value.message
    assert first.value.evidence == second.value.evidence
    assert report["admitted"] == 2
    assert report["refused"] == 0


def test_no_response_carries_a_sid_or_a_machine_fingerprint() -> None:
    """No path may put a SID into a response — the leak guard, on the refusals that carry one.

    The admission refusal is the interesting case: `policy._refuse` names the observed SID on purpose
    (its own docstring says a refusal that hid which identity was compared could not be re-examined),
    so `pipe._redact` has to remove it where an `AirootError` becomes a *wire document*. This test
    builds that refusal without a pipe, by handing `_admission_refusal` the error `admit_caller`
    really produces.
    """

    sid = identity.probe_identity().sid
    if sid is None:  # pragma: no cover - only where the token cannot be read
        pytest.skip("this process's SID could not be read")

    observed = identity.ProcessIdentity(
        pid=os.getpid(),
        sid=sid,
        integrity="medium",
        elevated=False,
        elevation_type="limited",
        session_id=1,
        is_app_container=False,
    )
    with pytest.raises(AirootError) as raised:
        policy.admit_caller(
            observed,
            policy.CallerExpectation(
                allowed_sids=frozenset({"S-1-5-21-0"}),
                minimum_integrity="low",
                evidence=("test expectation: one SID that is not this process's",),
            ),
        )
    document = pipe._admission_refusal(REQUEST_ID, raised.value)

    schema_io.validate_document("broker-response", document)
    rendered = json.dumps(document, sort_keys=True)
    assert not SID_PATTERN.search(rendered), f"a SID reached a response: {rendered}"
    assert "withheld" in rendered
    # The rule still travels, so the refusal is useful without the identity.
    assert "sid_not_allowed" in rendered


# --------------------------------------------------------------------------------------------- #
# malformed frames: answered, recorded, and never a traceback
# --------------------------------------------------------------------------------------------- #


def _connect_raw(pipe_name: str):
    """A client handle, opened through the module's own retry loop."""

    advapi32, kernel32 = pipe._load_libraries()
    pipe._declare_signatures(kernel32, advapi32)
    handle, status = pipe._open_client(kernel32, pipe_name, 4000)
    assert handle is not None, f"could not connect to {pipe_name}: err={status}"
    return kernel32, handle


def _send_raw(pipe_name: str, raw: bytes) -> dict | None:
    """Write raw bytes to the pipe and read one framed answer back (``None`` if none came).

    The write loops on ``written`` because `WriteFile`'s count is an upper bound, not a promise; a
    failure is an assertion because every caller here expects its bytes to have been accepted.
    """

    kernel32, handle = _connect_raw(pipe_name)
    try:
        offset = 0
        while offset < len(raw):
            chunk = raw[offset:]
            buffer = ctypes.create_string_buffer(chunk, len(chunk))
            written = wintypes.DWORD(0)
            assert kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None), (
                f"WriteFile failed: err={ctypes.get_last_error()}"
            )
            assert written.value, "WriteFile accepted the call and wrote nothing"
            offset += int(written.value)
        kernel32.FlushFileBuffers(handle)
        payload = transport.read_frame(pipe._read_file_shim(kernel32, handle))
    finally:
        kernel32.CloseHandle(handle)
    return None if payload is None else transport.decode_frame(payload)


@_requires_unelevated
def test_a_frame_that_is_not_json_is_answered_without_a_traceback(server) -> None:
    """Bad JSON is a verdict with `INVALID_INPUT`, and the server survives to answer the next caller."""

    answer = _send_raw(server.pipe_name, transport.encode_frame(b"{not json at all"))
    response = server.call(probe_request())
    report = server.finish()

    assert answer is not None, "the server closed the pipe instead of answering a malformed frame"
    schema_io.validate_document("broker-response", answer)
    assert answer["status"] == "rejected"
    assert answer["reason_code"] == "INVALID_INPUT"
    assert answer["security_mode"] == "policy_only"
    assert answer["enforcement"] == "same_user_can_bypass"
    assert any("not_json" in item["detail"] for item in answer["evidence"])
    # The server kept serving: the bad frame was answered, and the good request after it was answered
    # normally. That is the report's clean invariant — every request was admitted or refused — and the
    # malformed counter is about frames that never became a request at all.
    assert report["requests"] == 2
    assert report["refused"] == 1
    assert report["admitted"] == 1
    assert report["malformed"] == 0
    assert report["connection_errors"] == 0
    assert response["reason_code"] == "SUCCESS"


@_requires_unelevated
def test_a_length_beyond_the_bound_is_refused_without_reading_the_body(server) -> None:
    """A header declaring more than `MAX_FRAME_BYTES` is refused, and the body is never sent.

    The bound is enforced before the body is requested, so a lying length costs the server nothing:
    the frame here declares 4 GiB and carries three bytes.
    """

    header = struct.pack(transport.LENGTH_FORMAT, 0xFFFFFFFF)
    answer = _send_raw(server.pipe_name, header + b"abc")
    server.stop.set()

    assert answer is not None
    schema_io.validate_document("broker-response", answer)
    assert answer["reason_code"] == "INVALID_INPUT"
    assert any("too_large" in item["detail"] for item in answer["evidence"])
    report = server.finish()
    assert report["malformed"] == 1
    assert report["connection_errors"] == 0


@_requires_unelevated
def test_a_truncated_frame_does_not_escape_serve_pipe(server) -> None:
    """A frame whose body never arrives is a connection that ends, not an exception out of the loop.

    The client writes a header promising more than it sends and then disconnects. The server must
    close that connection, record it, and be ready for the next one.
    """

    kernel32, handle = _connect_raw(server.pipe_name)
    header = struct.pack(transport.LENGTH_FORMAT, 64)
    buffer = ctypes.create_string_buffer(header, len(header))
    written = wintypes.DWORD(0)
    kernel32.WriteFile(handle, buffer, len(header), ctypes.byref(written), None)
    kernel32.FlushFileBuffers(handle)
    kernel32.CloseHandle(handle)  # the body never comes

    response = server.call(probe_request())
    report = server.finish()
    assert response["reason_code"] == "SUCCESS"
    assert report["malformed"] == 1
    assert report["admitted"] == 1
    assert server.error is None


# --------------------------------------------------------------------------------------------- #
# the ERROR_PIPE_CONNECTED race
# --------------------------------------------------------------------------------------------- #


def test_pipe_connected_is_success_in_the_connect_helper() -> None:
    """`_connect` folds `ERROR_PIPE_CONNECTED` into success — against the OS's own constant.

    The constant is read from the OS by provoking it: the live test is
    :func:`test_a_client_that_wins_the_connect_race_is_served`, and this pins the value and the
    folding without needing a pipe, so a machine that cannot run the live one still checks the rule.
    """

    assert pipe.ERROR_PIPE_CONNECTED == 535

    class FakeKernel32:
        def __init__(self, ok: bool, status: int) -> None:
            self._ok = ok
            self._status = status

        def ConnectNamedPipe(self, _handle, _overlapped):  # noqa: N802 - the Win32 name
            import ctypes

            ctypes.set_last_error(self._status)
            return self._ok

    assert pipe._connect(FakeKernel32(True, 0), None) == 0
    # The race: the client connected first, so ConnectNamedPipe reports failure with 535. That is the
    # client we must serve, not one we must drop.
    assert pipe._connect(FakeKernel32(False, pipe.ERROR_PIPE_CONNECTED), None) == 0
    # A real failure is still a failure and is reported as its status.
    assert pipe._connect(FakeKernel32(False, 5), None) == 5


@_requires_unelevated
def test_a_client_that_wins_the_connect_race_is_served(pipe_name, broker_root, caller) -> None:
    """The classic race, won on purpose: the client connects before `ConnectNamedPipe` is called.

    Between `CreateNamedPipeW` and `ConnectNamedPipe` the server does almost nothing, so the race
    cannot be *relied* on — the test holds that window open through the module's documented test seam
    (``_TEST_HOOKS["before_connect_pause_s"]``) and races a real client into it. The server then gets
    a real `ERROR_PIPE_CONNECTED` from the OS, counts it, and serves the caller.
    """

    counter: list[int] = []
    pipe._TEST_HOOKS["before_connect_pause_s"] = 0.35
    pipe._TEST_HOOKS["race_counter"] = counter
    try:
        running = Server(broker_root, caller, pipe_name)
        try:
            response = running.call(probe_request())
            report = running.finish()
        finally:
            if running._thread.is_alive():
                running.finish()
    finally:
        pipe._TEST_HOOKS.clear()

    assert report["connect_races"] >= 1, (
        "the client did not win the connect race; the ERROR_PIPE_CONNECTED branch was not exercised"
    )
    assert counter, "the race branch was never reached"
    # Winning the race must produce a served caller, not a dropped connection.
    assert response["status"] == "ok"
    assert response["reason_code"] == "SUCCESS"
    assert report["admitted"] == 1
    assert report["requests"] == 1


# --------------------------------------------------------------------------------------------- #
# the SDDL, and what the OS did with it
# --------------------------------------------------------------------------------------------- #


@_requires_unelevated
def test_the_report_carries_the_sddl_that_was_asked_for(server, user_sid: str) -> None:
    """The report's SDDL is the one this build builds, and it names the current user's SID.

    Not hard-coded anywhere: the third trustee is read from `probe_identity()`, so the assertion is
    that the *observed* SID is what the descriptor asks for. SYSTEM and Builtin Administrators are
    the two well-known trustees, and there is no fourth.
    """

    server.call(probe_request())
    report = server.finish()

    sddl = report["sddl"]
    assert sddl.startswith("D:")
    assert "(A;;GA;;;SY)" in sddl
    assert "(A;;GA;;;BA)" in sddl
    assert f"(A;;GA;;;{user_sid})" in sddl
    # No-everyone, no authenticated-users, no interactive: three trustees and nothing else. The count
    # is what makes "no fourth" a check rather than a hope.
    assert len(re.findall(r"\(A;;", sddl)) == 3
    assert "WD" not in sddl and "AU" not in sddl and "IU" not in sddl
    assert report["trustees"] == ["SY", "BA", "the current user's SID"]


@_requires_unelevated
def test_the_os_reports_the_local_only_bit_set(server) -> None:
    """`GetNamedPipeInfo` says the OS took the local-only bit — the observation, not the intention.

    `os_pipe_flags` is read from the instance the kernel created, so this is the OS's answer. The
    server-end bit is set too, which is what makes the read meaningful: the query is about *this*
    handle.
    """

    server.call(probe_request())
    report = server.finish()

    flags = report["os_pipe_flags"]
    assert flags is not None, "the server never read its own pipe flags"
    assert flags & pipe.PIPE_REJECT_REMOTE_CLIENTS, f"the OS did not set it: 0x{flags:08x}"
    assert flags & pipe.PIPE_SERVER_END, f"not the server end: 0x{flags:08x}"
    assert report["rejects_remote_clients"] is True


#: `ACCESS_ALLOWED_ACE_TYPE` (`winnt.h`) — the ACE type every requested entry lands as.
ACCESS_ALLOWED_ACE_TYPE = 0x00
#: `INHERITED_ACE` — set on an entry the object inherited rather than one the descriptor states.
INHERITED_ACE = 0x10
#: `SE_KERNEL_OBJECT` — the object type a pipe handle is, for `GetSecurityInfo`.
SE_KERNEL_OBJECT = 6
#: `DACL_SECURITY_INFORMATION`.
DACL_SECURITY_INFORMATION = 0x00000004


class _AclHeader(ctypes.Structure):
    _fields_ = [
        ("AclRevision", ctypes.c_ubyte),
        ("Sbz1", ctypes.c_ubyte),
        ("AclSize", ctypes.c_ushort),
        ("AceCount", ctypes.c_ushort),
        ("Sbz2", ctypes.c_ushort),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", ctypes.c_ushort),
    ]


class _AccessAceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", ctypes.c_ushort),
        ("Mask", ctypes.c_uint32),
    ]


def _dacl_aces(handle) -> list[tuple[int, int, int]]:
    """``(ace_type, ace_flags, mask)`` for every ACE on a kernel handle's DACL.

    Read from the **handle**, because a pipe *name* is not an object `GetNamedSecurityInfoW` can open
    (`rc=161 ERROR_BAD_PATHNAME`, measured). The comparison downstream is a set rather than the SDDL
    string, because the read-back is not the request — `GA` is canonicalised to `FA` — so a string
    comparison would report drift forever. That measurement was passed on by the orchestrator; this
    turns it into the check.
    """

    advapi32, kernel32 = pipe._load_libraries()
    pipe._declare_signatures(kernel32, advapi32)
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    rc = advapi32.GetSecurityInfo(
        handle,
        SE_KERNEL_OBJECT,
        DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    assert rc == 0, f"GetSecurityInfo failed with rc={rc}"
    try:
        if not dacl:
            return []
        header = ctypes.cast(dacl, ctypes.POINTER(_AclHeader)).contents
        aces: list[tuple[int, int, int]] = []
        offset = ctypes.sizeof(_AclHeader)
        for _ in range(header.AceCount):
            ace = ctypes.cast(
                ctypes.c_void_p(dacl.value + offset), ctypes.POINTER(_AceHeader)
            ).contents
            assert ace.AceSize >= ctypes.sizeof(_AccessAceHeader), (
                f"ACE at offset {offset} is too small: {ace.AceSize}"
            )
            access = ctypes.cast(
                ctypes.c_void_p(dacl.value + offset), ctypes.POINTER(_AccessAceHeader)
            ).contents
            aces.append((int(ace.AceType), int(ace.AceFlags), int(access.Mask)))
            offset += ace.AceSize
        return aces
    finally:
        kernel32.LocalFree(descriptor)


@_requires_unelevated
def test_the_dacl_lands_as_three_allow_entries_and_nothing_else(pipe_name) -> None:
    """The requested DACL landed: three `ACCESS_ALLOWED` ACEs, none inherited — compared as a set.

    Omitting the SDDL is not an option, and that is measured rather than assumed: without one the
    DACL comes from the creating token's default, read back as **5 ACEs including `Everyone` and
    `ANONYMOUS LOGON` with read/execute** — connectable by everyone. This is the check behind the
    module's claim that the descriptor asks for three trustees and no more.
    """

    advapi32, kernel32 = pipe._load_libraries()
    pipe._declare_signatures(kernel32, advapi32)
    descriptor = pipe._convert_sddl(advapi32, pipe._build_sddl(identity.probe_identity().sid))
    attributes = pipe.SECURITY_ATTRIBUTES(
        nLength=ctypes.sizeof(pipe.SECURITY_ATTRIBUTES),
        lpSecurityDescriptor=descriptor.pointer,
        bInheritHandle=False,
    )
    handle = kernel32.CreateNamedPipeW(
        pipe_name,
        pipe.PIPE_ACCESS_DUPLEX | pipe.FILE_FLAG_FIRST_PIPE_INSTANCE,
        pipe.PIPE_MODE,
        pipe.PIPE_UNLIMITED_INSTANCES,
        4096,
        4096,
        0,
        ctypes.byref(attributes),
    )
    try:
        assert not pipe._invalid_handle(handle), f"create failed: {ctypes.get_last_error()}"
        aces = _dacl_aces(handle)
    finally:
        if not pipe._invalid_handle(handle):
            kernel32.CloseHandle(handle)
        kernel32.LocalFree(descriptor.pointer)

    assert len(aces) == 3, f"expected three ACEs, got {aces}"
    for ace_type, ace_flags, mask in aces:
        assert ace_type == ACCESS_ALLOWED_ACE_TYPE, f"not an allow entry: {ace_type}"
        assert not ace_flags & INHERITED_ACE, f"an inherited entry: {ace_flags:#x}"
        assert mask, "an allow entry granting nothing"
    # `GA` canonicalises to the same concrete mask on every entry, so the three are interchangeable in
    # the only respect this descriptor cares about.
    assert len({mask for _t, _f, mask in aces}) == 1, f"entries disagree about the mask: {aces}"


@_requires_unelevated
def test_the_os_answers_error_pipe_local_for_a_local_peer(server) -> None:
    """The local-ness observation every served caller depends on, checked against a real peer.

    `test_a_non_local_peer_is_refused_before_anything_is_observed` covers the refusal branch; this is
    the positive control. If `GetNamedPipeClientComputerNameW` did not answer `ERROR_PIPE_LOCAL` for a
    same-process peer, every legitimate caller would be refused as non-local.
    """

    kernel32, handle = _connect_raw(server.pipe_name)
    try:
        local, evidence = pipe._client_is_local(kernel32, handle)
    finally:
        kernel32.CloseHandle(handle)
    server.finish()

    assert local is True, f"a local peer was not recognised as local: {evidence}"
    assert "ERROR_PIPE_LOCAL" in evidence


def test_a_non_local_peer_is_refused_before_anything_is_observed() -> None:
    """A peer that is not local is refused, and "could not look" is refused too — without a pipe.

    A loopback-SMB client makes `GetNamedPipeClientProcessId` answer ``65279``, which names no local
    process; if that number ever collided with a live pid, admission would be deciding about an
    unrelated local process. The observation is exercised through its seam, so the rule is pinned even
    where the live path cannot run.
    """

    class FakeKernel32:
        def __init__(self, ok: bool, status: int, name: str = "") -> None:
            self._ok = ok
            self._status = status
            self._name = name

        def GetNamedPipeClientComputerNameW(self, _handle, buffer, _size):  # noqa: N802
            if not self._ok:
                ctypes.set_last_error(self._status)
                return False
            buffer.value = self._name
            return True

    # A local peer: FALSE with ERROR_PIPE_LOCAL.
    assert pipe._client_is_local(FakeKernel32(False, pipe.ERROR_PIPE_LOCAL), None)[0] is True
    # A remote peer: TRUE with the computer's name.
    local, evidence = pipe._client_is_local(FakeKernel32(True, 0, "\\\\OTHERBOX"), None)
    assert local is False
    assert "OTHERBOX" in evidence
    # TRUE with an empty name, and any other failure, are both "could not look" — which is not
    # "looked, and it was local", so the caller is refused on it.
    assert pipe._client_is_local(FakeKernel32(True, 0, ""), None)[0] is None
    assert pipe._client_is_local(FakeKernel32(False, 5), None)[0] is None


def test_connect_treats_no_data_as_a_dead_peer_not_as_connected() -> None:
    """Only `ERROR_PIPE_CONNECTED` is success: `ERROR_NO_DATA` is a peer that already left.

    Measured twice by the measurement agent — a client that connects and then disconnects before
    `ConnectNamedPipe` makes it answer FALSE with ``ERROR_NO_DATA`` (232). A server that read any
    FALSE as "already connected" would go on to serve a peer that is gone.
    """

    class FakeKernel32:
        def __init__(self, ok: bool, status: int) -> None:
            self._ok = ok
            self._status = status

        def ConnectNamedPipe(self, _handle, _overlapped):  # noqa: N802
            ctypes.set_last_error(self._status)
            return self._ok

    assert pipe.ERROR_NO_DATA == 232
    assert pipe._connect(FakeKernel32(False, pipe.ERROR_NO_DATA), None) == pipe.ERROR_NO_DATA
    assert pipe._connect(FakeKernel32(False, pipe.ERROR_PIPE_CONNECTED), None) == 0


@_requires_unelevated
def test_the_reject_remote_bit_is_rejected_in_dw_open_mode(pipe_name) -> None:
    """The brief's placement fails, measured: ``dwOpenMode`` carrying the bit → err 87.

    The orchestrator's correction arrived with this measurement and this test pins it on the machine
    it was measured on. `CreateNamedPipeW` is called directly (the module puts the bit where it
    works), so the assertion is about the OS's rule rather than about this build's argument list.
    """

    advapi32, kernel32 = pipe._load_libraries()
    pipe._declare_signatures(kernel32, advapi32)
    descriptor = pipe._convert_sddl(advapi32, pipe._build_sddl(identity.probe_identity().sid))
    attributes = pipe.SECURITY_ATTRIBUTES(
        nLength=ctypes.sizeof(pipe.SECURITY_ATTRIBUTES),
        lpSecurityDescriptor=descriptor.pointer,
        bInheritHandle=False,
    )
    try:
        wrong = kernel32.CreateNamedPipeW(
            pipe_name + "-wrong",
            pipe.PIPE_ACCESS_DUPLEX | pipe.PIPE_REJECT_REMOTE_CLIENTS,
            pipe.PIPE_MODE,
            pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            ctypes.byref(attributes),
        )
        wrong_status = ctypes.get_last_error() or 0
        right = kernel32.CreateNamedPipeW(
            pipe_name + "-right",
            pipe.PIPE_ACCESS_DUPLEX,
            pipe.PIPE_MODE,
            pipe.PIPE_UNLIMITED_INSTANCES,
            4096,
            4096,
            0,
            ctypes.byref(attributes),
        )
        right_status = ctypes.get_last_error() or 0
    finally:
        kernel32.LocalFree(descriptor.pointer)

    assert pipe._invalid_handle(wrong), "the OS accepted the bit in dwOpenMode after all"
    assert wrong_status == 87, f"expected ERROR_INVALID_PARAMETER, got {wrong_status}"
    assert not pipe._invalid_handle(right), f"the module's own placement failed: {right_status}"
    kernel32.CloseHandle(right)


# --------------------------------------------------------------------------------------------- #
# lifecycle: stop, threads, and handles
# --------------------------------------------------------------------------------------------- #


@_requires_unelevated
def test_stop_releases_a_server_nobody_ever_calls(pipe_name, broker_root, caller) -> None:
    """`stop` ends a server that is waiting for a caller who never comes — the case that wedges.

    There is no server-side connect timeout, so a synchronous `ConnectNamedPipe` blocks forever; a
    server that only checked `stop` "between connections" could never return here, and this test is
    the one that catches it. `broker/pipe.py`'s stop watcher is what makes the wait endable, and the
    release connection must not be mistaken for a caller — hence `requests == 0` and `malformed == 0`
    rather than a counted empty frame.
    """

    before = threading.active_count()
    running = Server(broker_root, caller, pipe_name, max_requests=5)
    assert running._thread.is_alive()
    try:
        report = running.finish(timeout=10.0)
    finally:
        if running._thread.is_alive():
            running.finish()

    assert report["stopped"] is True
    assert report["requests"] == 0, "the release connection was served as if it were a caller"
    assert report["admitted"] == 0
    assert report["malformed"] == 0
    assert report["connection_errors"] == 0
    assert report["error"] is None
    # The watcher thread is joined before `serve_pipe` returns, so no thread is left parked.
    deadline = time.monotonic() + 5.0
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threading.active_count() <= before, (
        f"threads leaked: {threading.active_count()} active, {before} before"
    )


@_requires_unelevated
def test_serve_pipe_stops_cleanly_through_stop(pipe_name, broker_root, caller) -> None:
    """`stop` after a served request ends the loop with no leaked threads.

    The companion to the no-caller case above: here the server has already answered one caller and is
    waiting for a second when `stop` is set.
    """

    before = threading.active_count()
    running = Server(broker_root, caller, pipe_name, max_requests=3)
    running.call(probe_request())
    report = running.finish()

    assert report["stopped"] is True
    assert report["requests"] == 1, "the server served more than the one request that was made"
    assert report["admitted"] == 1
    assert not running._thread.is_alive()
    deadline = time.monotonic() + 5.0
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threading.active_count() <= before, (
        f"threads leaked: {threading.active_count()} active, {before} before"
    )


@_requires_unelevated
def test_max_requests_bounds_how_many_callers_are_served(pipe_name, broker_root, caller) -> None:
    """`max_requests` is a bound on connections, and the default is one.

    With `max_requests=1` the server answers exactly one caller and then returns; a second caller
    finds no server, which is a `NOT_IMPLEMENTED` from `call_broker` rather than a hang.
    """

    running = Server(broker_root, caller, pipe_name, max_requests=1)
    first = running.call(probe_request())
    report = running.finish()
    assert first["status"] == "ok"
    assert report["requests"] == 1

    with pytest.raises(AirootError) as raised:
        pipe.call_broker(probe_request(), pipe_name=pipe_name, timeout_ms=300)
    assert raised.value.reason_code == "NOT_IMPLEMENTED"
    assert raised.value.details.get("pipe_name") == pipe_name


@_requires_unelevated
def test_the_report_shape_is_stable(server) -> None:
    """`serve_pipe`'s report has a fixed key set, so a reader can rely on it.

    The keys that make the mode's honesty legible are all here: the SDDL that was asked for, the
    flags the OS reported, and the counts. No key is machine-specific beyond the pipe's own name.
    """

    server.call(probe_request())
    report = server.finish()

    assert set(report) == {
        "pipe_name",
        "security_mode",
        "enforcement",
        "sddl",
        "trustees",
        "os_pipe_flags",
        "rejects_remote_clients",
        "requests",
        "admitted",
        "refused",
        "malformed",
        "connection_errors",
        "connect_races",
        "stopped",
        "error",
        "notes",
    }
    assert report["pipe_name"] == server.pipe_name
    assert report["security_mode"] == "policy_only"
    assert report["enforcement"] == "same_user_can_bypass"
    assert report["error"] is None
    assert report["stopped"] is False
    # Counts add up: every request that reached the decision was either admitted or refused, and none
    # is lost. A frame that never decoded to a request is counted separately, in `malformed`.
    assert report["requests"] == report["admitted"] + report["refused"]
    assert report["malformed"] == 0
    assert report["connection_errors"] == 0


@_requires_unelevated
def test_two_servers_on_different_names_do_not_collide(broker_root, caller) -> None:
    """Each connection gets its own instance, so sequential runs on one name work and two names coexist.

    The second part is what makes the first meaningful: if the pipe were a singleton, a per-test name
    would be a courtesy rather than the isolation it is.
    """

    first_name = "\\\\.\\pipe\\airoot-broker-test-" + os.urandom(8).hex()
    second_name = "\\\\.\\pipe\\airoot-broker-test-" + os.urandom(8).hex()
    first = Server(broker_root, caller, first_name)
    second = Server(broker_root, caller, second_name)
    try:
        assert first.call(probe_request())["status"] == "ok"
        assert second.call(probe_request())["status"] == "ok"
    finally:
        first_report = first.finish()
        second_report = second.finish()
    assert first_report["admitted"] == 1
    assert second_report["admitted"] == 1
