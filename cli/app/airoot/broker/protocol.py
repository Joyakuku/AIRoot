"""The protected broker's wire shapes — the protocol layer, and only that (ADR-0026).

`broker-request.schema.json` and `broker-response.schema.json` were published long before any code
existed and had no writer at all (ADR-0026). This module is the writer and the reader: it builds a
conforming request, and it reads a response into the repo's one error type. Three things it is
deliberately *not*, because each is the nearest misreading:

* **not the broker.** Nothing here elevates, opens a handle, writes a plan or touches a registry.
  There is no broker in this build at all — ADR-0025's D1 leaves the production approval issuer and
  the elevated service to P2 — which is why the only way this module can answer "ask the broker" is
  :func:`broker_unavailable`.
* **not the transport.** A request is a document, not a frame. The authenticated named pipe, the
  `client` block's authenticity (the broker re-reads the peer's token itself; a caller-supplied SID
  is a claim, never a fact) and every timeout belong to whoever carries the bytes.
* **not the trust boundary.** Passing the published schema says the *shape* is right and nothing
  more: not that the plan is approved, the path canonical, or the caller privileged. The broker
  revalidates all of that itself (docs/broker §4), and this layer must never be read as having done
  any of it.

The published schema is the authority for every rule it states. `build_request` therefore hand-rolls
no pattern check — `request_id` and the `client` block are the schema's business — and it checks the
conditional `if`/`then` requirements itself only so that the caller is told *which* field is missing
as `INVALID_INPUT` (the request is wrong) rather than as `SELF_VALIDATION_FAILED` (this build is
wrong), which is what the schema would otherwise report from behind `validate_self`.

One requirement is this layer's own rather than the schema's: `gc_apply` must carry a plan and an
approval (see :data:`ADDITIONAL_REQUIREMENTS`). The schema enumerates the operation and constrains
nothing about what it must carry, so without that check the wire would permit a request the
operation it stands for cannot honour. Requirements the layer adds are data here, with their
reasons, so that "the schema does not say this" can never be mistaken for "this is not required".

Where the design document's own example envelope disagrees with the schema, the schema wins
(ADR-0003, layer 1): docs/broker's example names the plan and the approval `plan`/`approval`, and
`broker-request` requires `plan_ref`/`approval_ref` under `additionalProperties: false`, so the
example could only ever be rejected. This module spells them the schema's way.

The one thing this layer cannot check for itself is the response's `requested_at` twin on the
request: `format: date-time` is stated by the schema but this repo's validator configures no format
checker (``schema_io``), so a caller-supplied `requested_at` is passed through unchecked. That is a
property of the whole tree, not something to paper over with a hand-rolled timestamp regex here.
"""

from __future__ import annotations

from typing import Any

from ..exits import EXIT_SUCCESS, REASON_EXIT, AirootError
from ..schema_io import validate_document, validate_self

PROTOCOL_VERSION = 1

#: Every operation the published request schema's `operation` enum carries, in the schema's order.
OPERATIONS = ("commit_plan", "recover_transaction", "probe_root", "gc_apply")

#: What each operation must carry, read off the request schema's `allOf` dispatch branches. A
#: literal, so that `build_request` can name a missing field without parsing JSON Schema at runtime;
#: `test_the_conditional_requirements_are_the_schemas_own` reads the same requirements out of the
#: schema and compares, so this table cannot drift away from the contract it explains.
REQUIRED_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "commit_plan": ("plan_ref", "approval_ref"),
    "recover_transaction": ("transaction_id",),
    "probe_root": (),
    "gc_apply": (),
}

#: Requirements this layer adds **on top of** what the published schema states, with the reason in
#: `ADDITIONAL_REQUIREMENT_REASONS`. The schema has no `allOf` branch for `gc_apply`, and its silence
#: is a gap rather than a permission: `gc_apply` stands for the in-process operation
#: `caps/lifecycle.py`'s `apply_gc_plan()`, which takes a plan hash and an approval token, so a
#: request naming neither is one no honest broker could honour. `probe_root` adds nothing — it asks a
#: question and commits to nothing.
ADDITIONAL_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "gc_apply": ("plan_ref", "approval_ref"),
}

#: One line per addition above, so the check is a decision a reader can re-examine instead of a rule
#: whose only defence is that deleting it turns a test red.
ADDITIONAL_REQUIREMENT_REASONS: dict[str, str] = {
    "gc_apply": "the operation it stands for, `caps/lifecycle.py`'s `apply_gc_plan()`, takes a plan "
    "hash and an approval token, so a request that names neither could not be honoured by any honest "
    "broker (ADR-0025's D1 has not built the issuer that could approve it either)",
}


def required_fields(operation: str) -> tuple[str, ...]:
    """Every field ``operation`` must carry: the schema's own conditional rule, plus this layer's.

    One definition, so a caller (and a future guard) can ask what `build_request` will demand
    instead of re-deriving it from two tables that must stay in step.
    """

    return REQUIRED_BY_OPERATION.get(operation, ()) + ADDITIONAL_REQUIREMENTS.get(operation, ())


def build_request(
    *,
    operation: str,
    client: dict,
    request_id: str,
    plan_ref: str | None = None,
    approval_ref: str | None = None,
    transaction_id: str | None = None,
    requested_at: str | None = None,
) -> dict:
    """Build the `broker-request` document for ``operation``.

    ``client`` is the caller's identity block as observed elsewhere (SID, pid, integrity level,
    application id); this layer relays it and does not authenticate it — see the module docstring.

    Raises `AirootError` with `INVALID_INPUT` (exit 8) when the request cannot describe anything
    real: an operation the schema does not enumerate, or one given without the fields `required_fields`
    demands for it (evidence names each missing field). Everything the published schema already
    constrains — `request_id`'s pattern above all — is left to the schema, and a value it refuses
    comes back as the schema's own message rather than as a hand-rolled check here.
    """

    if operation not in OPERATIONS:
        raise AirootError(
            "INVALID_INPUT",
            f"unknown broker operation: {operation!r}",
            evidence=[f"operation must be one of: {', '.join(OPERATIONS)}"],
        )

    supplied = {
        "plan_ref": plan_ref,
        "approval_ref": approval_ref,
        "transaction_id": transaction_id,
    }
    missing = [name for name in required_fields(operation) if supplied[name] is None]
    if missing:
        raise AirootError(
            "INVALID_INPUT",
            f"operation {operation!r} requires {', '.join(missing)}",
            evidence=[f"{operation} requires a value for {name}" for name in missing],
        )

    document: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": operation,
        "client": client,
    }
    # Absent, not `null`: the schema requires only those four keys, and a null would add a field the
    # caller never chose — a distinction the broker would then have to give a meaning to.
    document.update({name: value for name, value in supplied.items() if value is not None})
    if requested_at is not None:
        document["requested_at"] = requested_at

    # Two readings of one published schema, in this order and for a reason. The variable parts of
    # this document (`request_id`, `client`) come from the caller, so a value the schema refuses is
    # the caller's mistake: `validate_document` reports it as `INVALID_INPUT` with the schema's own
    # message. Alone, `validate_self` would say `SELF_VALIDATION_FAILED` — this build's word for
    # "the core emitted an invalid document" (AGENTS.md §7) — and blame the wrong side. The
    # `validate_self` call stays, because the frozen rule is that the core self-validates every
    # outward document, and what only this build controls (`protocol_version`, the field set) is
    # still its to get wrong.
    validate_document("broker-request", document)
    validate_self("broker-request", document)
    return document


def parse_response(document: dict) -> dict:
    """Validate ``document`` as a `broker-response`; return it when `status` is ``ok``, else raise.

    A response the published schema refuses is `INVALID_INPUT` (exit 8) with the schema's errors as
    evidence: the document *we received* is invalid input to us, exactly as a malformed request is
    invalid input to the broker.

    A well-formed response whose status is not ``ok`` is the broker's verdict, and the caller's next
    move depends on it (exit 4 asks for approval, 5 for privilege, 6 for recovery, 7 for a new
    plan), so carrying the response's **own** `reason_code` is the only way that verdict survives
    intact — rewriting it as `INVALID_INPUT` would say "your document was malformed" about a
    document that was not. Two codes cannot be carried and are refused as incoherent input instead:
    one that is missing or unregistered has no exit code to carry, and one registered as a success
    (`SUCCESS`, `POLICY_ONLY_MODE`) would return exit 0 from a response that is not ok — the "code
    that says the wrong thing" ADR-0027 removed.
    """

    validate_document("broker-response", document)

    status = document["status"]
    if status == "ok":
        return document

    code = document.get("reason_code")
    if not isinstance(code, str) or code not in REASON_EXIT:
        raise AirootError(
            "INVALID_INPUT",
            f"broker response status {status!r} carries no usable reason code",
            evidence=[
                f"reason_code is {code!r}; a response that is not ok must carry a code "
                "registered in exits.py",
                *_render_evidence(document),
            ],
        )
    if REASON_EXIT[code] == EXIT_SUCCESS:
        raise AirootError(
            "INVALID_INPUT",
            f"broker response status {status!r} carries {code}, which is a success code",
            evidence=[
                f"{code} maps to exit 0, so it cannot express a response that is not ok",
                *_render_evidence(document),
            ],
        )

    # Scalars only: `error-response.details` accepts strings/ints/bools/null and nothing nested.
    details: dict[str, Any] = {"status": status, "reason_code": code}
    for key in ("request_id", "transaction_id", "state", "retryable", "security_mode", "enforcement"):
        if document.get(key) is not None:
            details[key] = document[key]

    raise AirootError(
        code,
        f"broker response status {status!r}: {code}",
        evidence=_render_evidence(document),
        details=details,
    )


def broker_unavailable(operation: str) -> AirootError:
    """The refusal for "this build has no broker to ask" (ADR-0025's D1).

    Returns the error rather than raising it, so the caller decides where the refusal lands. The
    code is `NOT_IMPLEMENTED` (exit 1), the one draft §101/ADR-0027 added for a thing that is
    declared and deliberately absent from this build: `INVALID_INPUT` would blame the operation name
    as if it were a typo, and `PRIVILEGE_REQUIRED` would promise that elevating produces a broker —
    which is precisely what ADR-0025's D1 decided not to build yet.
    """

    return AirootError(
        "NOT_IMPLEMENTED",
        f"no protected broker exists in this build, so {operation} cannot be asked "
        "(decided: ADR-0025 leaves the protected broker and its approval issuer to P2)",
        evidence=[
            f"operation: {operation}",
            "ADR-0025 (D1) keeps the production issuer and the elevated broker waiting for P2, "
            "so no request can be delivered",
        ],
        details={"operation": operation, "adr": "ADR-0025"},
    )


def _render_evidence(document: dict) -> list[str]:
    """The response's structured `evidence` objects as the short lines `AirootError` carries.

    `common.$defs.evidence` items are objects (`kind`/`detail`/`path`/`digest`) and an error's
    evidence is a list of strings (`error-response.schema.json`), so the two shapes meet here rather
    than in every caller.
    """

    lines: list[str] = []
    for item in document.get("evidence", []):
        if not isinstance(item, dict):  # the schema refused everything else already
            lines.append(str(item))
            continue
        line = f"{item.get('kind', '?')}: {item.get('detail', '')}"
        if item.get("path"):
            line += f" ({item['path']})"
        lines.append(line)
    return lines
