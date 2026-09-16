"""L1: the broker protocol layer — the published `broker-*` schemas' one writer and reader.

`broker-request` and `broker-response` were published with no writer at all (ADR-0026), and this
build still has no broker (ADR-0025's D1): these tests therefore pin the *wire*, never a service.
Nothing here opens a handle, elevates, or needs an admin — the layer is pure, so the whole file is
about which document comes out and which reason code comes back.

What is asserted, and where the authority for each is:

* the request shape and every pattern/enum in it — the published schema, read through the repo's own
  ``schema_io`` helpers, never a regex re-implemented here;
* which operation must carry which field — the schema's own ``allOf`` branches, and one requirement
  this layer adds because the schema is silent about an operation an implementation *does* demand;
* the response's verdict — the response's own ``reason_code``, so the broker's exit code survives;
* the failure document — `error-response`, because the broker's structured evidence objects have to
  become the strings an error's evidence is made of.
"""

from __future__ import annotations

import ast
import copy
import inspect
import pathlib

import pytest

from airoot import schema_io
from airoot.broker import protocol
from airoot.caps import lifecycle
from airoot.exits import REASON_EXIT, AirootError

CLIENT = {
    "sid": "S-1-5-21-1000",
    "pid": 4242,
    "integrity": "medium",
    "application_id": "airoot-cli",
}
REQUEST_ID = "req/commit/0001"
PLAN_REF = "state/plans/plan-0001.json"
APPROVAL_REF = "state/approvals/approval-0001.json"
TRANSACTION_ID = "tx-0001"

#: The optional fields each operation needs, as *this suite* supplies them. Kept beside the
#: behaviour test below so a request that stops being constructible is visible here and not only in
#: `requirements`.
FIELDS = {
    "plan_ref": PLAN_REF,
    "approval_ref": APPROVAL_REF,
    "transaction_id": TRANSACTION_ID,
}


def request_fields(operation: str) -> dict:
    """The values `required_fields` says `operation` needs, ready to pass to `build_request`."""

    return {name: FIELDS[name] for name in protocol.required_fields(operation)}


def build(operation: str, **overrides) -> dict:
    """A request for `operation` with everything it needs, plus overrides."""

    arguments = {"operation": operation, "client": copy.deepcopy(CLIENT), "request_id": REQUEST_ID}
    arguments.update(request_fields(operation))
    arguments.update(overrides)
    return protocol.build_request(**arguments)


def valid_request(operation: str, **overrides) -> dict:
    document = build(operation, **overrides)
    assert schema_io.errors_for("broker-request", document) == []
    return document


def ok_response(**overrides) -> dict:
    document = {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "status": "ok",
        "security_mode": "policy_only",
        "enforcement": "same_user_can_bypass",
        "transaction_id": None,
        "state": None,
        "reason_code": "SUCCESS",
        "evidence": [],
    }
    document.update(overrides)
    return document


def verdict(status: str, code, **overrides) -> dict:
    document = ok_response(status=status, reason_code=code)
    document.update(overrides)
    return document


# --------------------------------------------------------------------------------------------- #
# the request: built from the published schema, refused when it cannot be true
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("operation", protocol.OPERATIONS)
def test_every_operation_produces_a_document_the_published_schema_accepts(operation: str) -> None:
    document = valid_request(operation)

    assert document["protocol_version"] == 1
    assert document["operation"] == operation
    assert document["request_id"] == REQUEST_ID
    assert document["client"] == CLIENT
    # Unset optional fields are absent rather than null: the schema requires four keys, and a null
    # would claim the caller considered a field they never mentioned.
    for name in ("plan_ref", "approval_ref", "transaction_id", "requested_at"):
        if name not in protocol.required_fields(operation):
            assert name not in document


def test_a_commit_plan_request_carries_both_refs() -> None:
    document = valid_request(
        "commit_plan", plan_ref=PLAN_REF, approval_ref=APPROVAL_REF, requested_at="2024-01-01T00:00:00Z"
    )

    assert document["plan_ref"] == PLAN_REF
    assert document["approval_ref"] == APPROVAL_REF
    assert document["requested_at"] == "2024-01-01T00:00:00Z"


def test_a_recover_transaction_request_carries_the_transaction_id() -> None:
    document = valid_request("recover_transaction", transaction_id=TRANSACTION_ID)

    assert document["transaction_id"] == TRANSACTION_ID


def test_a_probe_root_request_needs_nothing_beyond_the_envelope() -> None:
    """`probe_root` asks a question and commits to nothing — the layer adds no requirement to it."""

    document = valid_request("probe_root")

    assert protocol.required_fields("probe_root") == ()
    assert set(document) == {"protocol_version", "request_id", "operation", "client"}


def test_a_gc_apply_request_carries_a_plan_and_an_approval() -> None:
    document = valid_request("gc_apply")

    assert document["plan_ref"] == PLAN_REF
    assert document["approval_ref"] == APPROVAL_REF


def test_the_request_spells_the_refs_the_way_the_schema_does() -> None:
    """The broker design's own example envelope names them `plan`/`approval`; the schema does not.

    `broker-request` requires `plan_ref`/`approval_ref` and forbids unknown properties, so an
    implementation that followed the example could only ever have its request rejected (ADR-0003:
    the published schema is layer 1). This pins the spelling the code chose.
    """

    document = valid_request("commit_plan")

    assert "plan_ref" in document and "approval_ref" in document
    assert "plan" not in document and "approval" not in document

    example_spelling = dict(document, plan=document["plan_ref"], approval=document["approval_ref"])
    assert schema_io.errors_for("broker-request", example_spelling) != [], (
        "the design document's key names must be refused by the published schema"
    )


@pytest.mark.parametrize(
    "operation, missing",
    [
        ("commit_plan", {"plan_ref"}),
        ("commit_plan", {"approval_ref"}),
        ("commit_plan", {"plan_ref", "approval_ref"}),
        ("recover_transaction", {"transaction_id"}),
        ("gc_apply", {"plan_ref"}),
        ("gc_apply", {"approval_ref"}),
        ("gc_apply", {"plan_ref", "approval_ref"}),
    ],
)
def test_an_operation_without_what_it_needs_is_refused_naming_the_field(operation: str, missing: set) -> None:
    """The choice this layer made: the caller gets `INVALID_INPUT`, not a self-validation failure.

    The published schema would reject these documents too, but from behind `validate_self` — i.e. as
    `SELF_VALIDATION_FAILED`, this build's word for "the core emitted an invalid document". The
    request is wrong, not the build, so the caller is told `INVALID_INPUT` and *which field*.
    """

    arguments = {"operation": operation, "client": copy.deepcopy(CLIENT), "request_id": REQUEST_ID}
    arguments.update({name: value for name, value in request_fields(operation).items() if name not in missing})

    with pytest.raises(AirootError) as failure:
        protocol.build_request(**arguments)

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    for name in missing:
        assert any(name in line for line in failure.value.evidence), (name, failure.value.evidence)
        assert name in failure.value.message


def test_an_unknown_operation_is_refused_as_input() -> None:
    with pytest.raises(AirootError) as failure:
        protocol.build_request(operation="commit_plans", client=copy.deepcopy(CLIENT), request_id=REQUEST_ID)

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert "commit_plans" in failure.value.message
    assert any("commit_plan" in line for line in failure.value.evidence)


def test_the_operations_are_exactly_the_published_enum() -> None:
    """Two copies of one vocabulary: the frozen tuple, and the schema's `enum`. Both directions."""

    schema = schema_io.load_schema("broker-request")

    assert list(protocol.OPERATIONS) == schema["properties"]["operation"]["enum"]
    assert protocol.PROTOCOL_VERSION == schema["properties"]["protocol_version"]["const"]


def test_the_conditional_requirements_are_the_schemas_own() -> None:
    """The literal table is checked against the schema it transcribes, so it cannot drift."""

    schema = schema_io.load_schema("broker-request")
    from_schema = {
        branch["if"]["properties"]["operation"]["const"]: tuple(branch["then"]["required"])
        for branch in schema["allOf"]
    }

    assert from_schema == {
        "commit_plan": ("plan_ref", "approval_ref"),
        "recover_transaction": ("transaction_id",),
    }, "the published dispatcher changed; the literal table has to change with it"

    for operation, required in from_schema.items():
        assert protocol.REQUIRED_BY_OPERATION[operation] == required
    for operation in protocol.OPERATIONS:
        if operation not in from_schema:
            assert protocol.REQUIRED_BY_OPERATION[operation] == (), (
                f"{operation} is not dispatched on by the schema, so the literal table must not "
                "invent a requirement for it"
            )


def test_the_requirements_this_layer_adds_are_recorded_with_reasons() -> None:
    """The schema is silent about `gc_apply`; its silence is a gap, not a permission.

    A wire request that names neither a plan nor an approval could not be honoured by any honest
    broker — the in-process operation it stands for demands both — so the layer requires them and
    records *why*, here, so that deleting the check has to argue with the reason rather than with a
    bare assertion.
    """

    added = protocol.ADDITIONAL_REQUIREMENTS

    assert added == {"gc_apply": ("plan_ref", "approval_ref")}
    assert set(added) <= set(protocol.OPERATIONS)
    assert set(protocol.ADDITIONAL_REQUIREMENT_REASONS) == set(added), "every addition needs a reason"
    for reason in protocol.ADDITIONAL_REQUIREMENT_REASONS.values():
        assert reason.strip()

    # Beyond the schema, not a restatement of it: the schema states no requirement for `gc_apply`.
    schema = schema_io.load_schema("broker-request")
    dispatched = {
        branch["if"]["properties"]["operation"]["const"] for branch in schema["allOf"]
    }
    assert "gc_apply" not in dispatched


def test_the_gc_apply_addition_is_backed_by_the_operation_it_stands_for() -> None:
    """The reason above names a real signature: `apply_gc_plan` takes a plan and a token.

    Measuring it here keeps the addition from becoming a rule nobody can check — if the in-process
    operation ever stopped needing one of the two, this test would be the place that says so.
    """

    signature = inspect.signature(lifecycle.apply_gc_plan)
    assert list(signature.parameters)[:3] == ["registry", "plan", "token"]
    assert "verify_approval" in inspect.getsource(lifecycle.apply_gc_plan)


def test_every_operation_demands_exactly_what_required_fields_says() -> None:
    """Table and behaviour held equal, both directions: enough is enough, and each field is needed."""

    for operation in protocol.OPERATIONS:
        valid_request(operation)
        for name in protocol.required_fields(operation):
            arguments = {"operation": operation, "client": copy.deepcopy(CLIENT), "request_id": REQUEST_ID}
            arguments.update(
                {key: value for key, value in request_fields(operation).items() if key != name}
            )
            with pytest.raises(AirootError) as failure:
                protocol.build_request(**arguments)
            assert failure.value.reason_code == "INVALID_INPUT"
            assert any(name in line for line in failure.value.evidence)


@pytest.mark.parametrize(
    "request_id",
    [
        "Req/0001",  # uppercase: the id pattern is lower-case only
        "req 0001",  # a space is not in the pattern's character class
        "",  # the pattern needs at least one character
        "-leading-dash",
    ],
)
def test_a_request_id_the_schema_refuses_is_refused(request_id: str) -> None:
    """No hand-rolled pattern lives here — the schema's `id` definition is what says no."""

    with pytest.raises(AirootError) as failure:
        protocol.build_request(operation="probe_root", client=copy.deepcopy(CLIENT), request_id=request_id)

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert any("request_id" in line for line in failure.value.evidence), failure.value.evidence
    # The refusal is the schema's, so the same document is refused by the schema directly.
    assert schema_io.errors_for(
        "broker-request",
        {
            "protocol_version": 1,
            "request_id": request_id,
            "operation": "probe_root",
            "client": CLIENT,
        },
    )


@pytest.mark.parametrize(
    "client",
    [
        {**CLIENT, "sid": "not-a-sid"},
        {**CLIENT, "pid": 0},
        {**CLIENT, "pid": "4242"},
        {**CLIENT, "integrity": "root"},
        {**CLIENT, "application_id": "AIROOT-CLI"},
        {**CLIENT, "nickname": "extra"},
        {key: value for key, value in CLIENT.items() if key != "application_id"},
        "airoot-cli",
    ],
)
def test_a_client_block_the_schema_refuses_is_refused(client) -> None:
    """The client block is validated as a whole by the schema, including its unknown properties."""

    with pytest.raises(AirootError) as failure:
        protocol.build_request(operation="probe_root", client=client, request_id=REQUEST_ID)

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert any("client" in line for line in failure.value.evidence), failure.value.evidence


def test_build_request_self_validates_before_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frozen rule (AGENTS.md §7) — removing the call because `validate_document` looks enough
    would leave the outward document checked only as if it were somebody else's input."""

    seen: list[str] = []
    real = protocol.validate_self

    def recorder(name: str, document: object) -> None:
        seen.append(name)
        real(name, document)

    monkeypatch.setattr(protocol, "validate_self", recorder)
    protocol.build_request(operation="probe_root", client=copy.deepcopy(CLIENT), request_id=REQUEST_ID)

    assert seen == ["broker-request"]


def test_the_layer_carries_no_transport_and_no_service() -> None:
    """This module is the wire shape. An import of a pipe, a registry or a privilege API would be
    the moment "protocol layer" stopped being true — and the whole point of the deliverable."""

    source = pathlib.Path(protocol.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert imported == {"__future__", "typing", "exits", "schema_io"}, imported
    for forbidden in ("socket", "subprocess", "winreg", "ctypes", "multiprocessing", "os"):
        assert forbidden not in imported


# --------------------------------------------------------------------------------------------- #
# the response: the broker's verdict has to survive
# --------------------------------------------------------------------------------------------- #


def test_an_ok_response_round_trips_unchanged() -> None:
    document = ok_response(transaction_id=TRANSACTION_ID, state="FINALIZED")

    assert schema_io.errors_for("broker-response", document) == []
    assert protocol.parse_response(document) == document


def test_a_response_missing_a_required_field_is_invalid_input() -> None:
    document = ok_response()
    del document["evidence"]

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(document)

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert any("evidence" in line for line in failure.value.evidence), failure.value.evidence


@pytest.mark.parametrize(
    "status, code, exit_code",
    [
        ("rejected", "PRIVILEGE_REQUIRED", 5),
        ("failed", "DIGEST_MISMATCH", 7),
        ("recovery_required", "RECOVERY_REQUIRED", 6),
    ],
)
def test_a_non_ok_response_raises_with_the_brokers_own_code(status: str, code: str, exit_code: int) -> None:
    """The verdict must survive intact: the caller's next move (5/6/7) is the broker's to choose."""

    document = verdict(status, code, transaction_id=TRANSACTION_ID, retryable=False)
    assert schema_io.errors_for("broker-response", document) == []

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(document)

    assert failure.value.reason_code == code
    assert failure.value.exit_code == REASON_EXIT[code] == exit_code
    assert code in failure.value.message
    assert failure.value.details["status"] == status
    assert failure.value.details["transaction_id"] == TRANSACTION_ID


@pytest.mark.parametrize("reason_code", [None, "NO_SUCH_REASON_CODE"])
def test_a_verdict_with_no_usable_code_is_invalid_input(reason_code) -> None:
    """A code that is missing or unregistered has no exit code to carry, so it cannot be obeyed."""

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(verdict("failed", reason_code))

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert any("reason_code" in line for line in failure.value.evidence), failure.value.evidence


def test_a_rejection_that_claims_a_success_code_is_invalid_input() -> None:
    """`SUCCESS` is registered, but carrying it would exit 0 on a response that is not ok."""

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(verdict("rejected", "SUCCESS"))

    assert failure.value.reason_code == "INVALID_INPUT"
    assert failure.value.exit_code == 8
    assert any("SUCCESS" in line for line in failure.value.evidence), failure.value.evidence


def test_the_failure_document_still_passes_error_response_when_the_broker_sent_object_evidence() -> None:
    """The two evidence shapes meet here: the response carries objects, an error carries strings.

    `broker-response.evidence` items are `common.$defs.evidence` objects; `AirootError.evidence` is a
    list of strings (`error-response.schema.json`). Passing the objects through would produce a
    failure document the core's own error contract refuses — so `parse_response` renders them, and
    this test holds the rendered envelope against the published failure schema.
    """

    document = verdict(
        "rejected",
        "PRIVILEGE_REQUIRED",
        evidence=[
            {"kind": "acl", "detail": "the caller's token does not carry WRITE_DAC", "path": "store/tools"},
            {"kind": "policy", "detail": "policy revision mismatch"},
        ],
    )

    with pytest.raises(AirootError) as failure:
        protocol.parse_response(document)

    error = failure.value
    assert all(isinstance(line, str) for line in error.evidence)
    assert "acl: the caller's token does not carry WRITE_DAC (store/tools)" in error.evidence
    assert "policy: policy revision mismatch" in error.evidence

    schema_io.validate_self("error-response", error.to_envelope())


# --------------------------------------------------------------------------------------------- #
# no broker to ask
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("operation", protocol.OPERATIONS)
def test_broker_unavailable_is_not_implemented_and_names_the_adr(operation: str) -> None:
    """ADR-0025's D1: this build has no broker, and the refusal must not read like a typo (ADR-0027)."""

    error = protocol.broker_unavailable(operation)

    assert isinstance(error, AirootError)
    assert error.reason_code == "NOT_IMPLEMENTED"
    assert error.exit_code == 1
    assert operation in error.message
    assert "ADR-0025" in error.message
    assert any("ADR-0025" in line for line in error.evidence)
    assert error.details["operation"] == operation
    assert error.details["adr"] == "ADR-0025"

    schema_io.validate_self("error-response", error.to_envelope())
