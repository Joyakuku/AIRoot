"""In-process loopback harness that plays the broker — a **test-path-only** stand-in.

What this harness *is*: the one producer of a `broker-response` document in this build. Since P2
stage 1 the client half of the wire exists (`broker/protocol.py` builds a `broker-request` and reads
a `broker-response`), but nothing answers a request — there is no broker process, no named pipe and
no approval issuer outside the tests (ADR-0025's D1). This module answers it in process, by
dispatching each operation to the **existing in-process implementation** of that operation, so the
four operations finally have an end-to-end path that can be exercised on a temporary root.

What this harness **is not** — three negatives, because each is the nearest misreading:

* **not a broker.** It does not verify the caller's token: the `client` block is relayed into the
  answer as the caller's own claim and is never checked against the OS, so a request may name any
  SID, pid or integrity level at all. Nothing here calls an IPC primitive, impersonates a client or
  knows what a peer's token is.
* **not an ACL boundary.** It creates no ACL, reads no ACL it did not ask to observe, and holds no
  privilege. `probe_root` *observes* a directory's DACL with `caps/acl.py` — the same read-only call
  `doctor` makes — which is why that operation reports rather than protects.
* **not proof of the trust boundary.** A green run says the four operations can be driven from a
  request document and answered with a document the published schema accepts. It says nothing about
  whether a real caller was entitled to ask, because an in-process call has no caller to authorise:
  the tokens, nonces and hashes it checks are checked by the *in-process operations*, not by a
  boundary this harness stands behind.

**The answer is always User compatibility mode**, and this is the honest reading rather than a
placeholder (ADR-0002, docs/broker §2): `security_mode="policy_only"` with
`enforcement="same_user_can_bypass"`, always. An in-process binding has no ACL protecting the
protected zone and no separate process to enforce anything, and the same user who runs the harness
can bypass every check in it by editing the files it reads. Claiming `protected_machine` /
`acl_enforced` would assert a mechanical property nothing here has, so the harness **refuses** to
answer in protected mode (see :func:`refusal_for_mode`) instead of relabelling itself — the mode a
caller may request is the first thing validated, before any operation runs.

Two shape traps this module is careful about, both documented in `broker/protocol.py`:

* `evidence` in a broker response is an array of **objects** (`common.schema.json#/$defs.evidence`:
  `kind` + `detail` required, `path`/`digest` optional) — *not* the string array an error envelope
  carries. Every evidence item here is built by :func:`evidence_object`.
* a refusal is a **returned document**, never an exception: a tampered plan, a replayed nonce or a
  missing file comes back as `status="rejected"`/`"failed"` with a registered `reason_code` from
  `exits.py` and evidence explaining it. The only thing that can escape :func:`serve` is a defect in
  this harness itself (an output document the published schema refuses, or a failure with no
  registered code to carry) — see :class:`HarnessDefect`.

Self-validation follows the core's rule (AGENTS.md §7): the response is checked with
`schema_io.validate_self` before it is returned, because "the schema accepts what we produced" is the
only reason producing a `broker-response` is worth anything. That check is deliberately *not* the
check that guards the boundary: a received request is validated with `validate_document`, so a
malformed request is answered as a refusal (it is invalid *input*), while a malformed **answer** is
this build's bug and is reported as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from airoot import root as root_module
from airoot import schema_io
from airoot.caps import acl, lifecycle
from airoot.clock import SYSTEM_CLOCK
from airoot.exits import EXIT_SUCCESS, REASON_EXIT, AirootError
from airoot.paths import from_root_relative
from airoot.registry import Registry
from airoot.broker import protocol
from airoot.tx import journal as journal_module
from airoot.tx import simulate

__all__ = [
    "HARNESS_SECURITY_MODE",
    "HARNESS_ENFORCEMENT",
    "SUPPORTED_OPERATIONS",
    "REJECTION_CODES",
    "HarnessDefect",
    "refusal_for_mode",
    "compatibility_block",
    "evidence_object",
    "serve",
]

# --------------------------------------------------------------------------------------------- #
# the frozen User compatibility mode
# --------------------------------------------------------------------------------------------- #

#: The only security mode this harness can answer in (docs/broker §2, ADR-0002). It is a constant
#: rather than a per-call choice on purpose: nothing about an in-process call ever makes it true.
HARNESS_SECURITY_MODE = "policy_only"
HARNESS_ENFORCEMENT = "same_user_can_bypass"

#: Every operation the harness can honour — the request schema's own `enum`, relayed from the
#: protocol layer rather than copied, so the two cannot drift apart.
SUPPORTED_OPERATIONS: tuple[str, ...] = protocol.OPERATIONS

#: Reason codes that mean **the request was refused**, as opposed to a requested operation that ran
#: and did not succeed. The distinction is what `status="rejected"` vs `"failed"` reports, and it is a
#: decision this harness had to make (the published schema enumerates the four statuses but does not
#: say which cause maps to which): a refusal is a verdict about *whether this may be done at all*
#: (absent, expired, replayed, revoked, mismatched or unproven provenance), while `failed` is the
#: outcome of an operation that was allowed to run and broke (a digest that moved, an instance
#: conflict, a filesystem refusal). `recovery_required` is neither: the transaction is mid-flight and
#: the journal has to be reconciled first.
REJECTION_CODES: frozenset[str] = frozenset(
    {
        "NOT_FOUND",
        "INVALID_INPUT",
        "PATH_ESCAPES_ROOT",
        "UNC_NOT_ALLOWED",
        "REPARSE_POINT_REJECTED",
        "NOT_IMPLEMENTED",
        "PRIVILEGE_REQUIRED",
        "ACL_MISMATCH",
        "APPROVAL_REQUIRED",
        "APPROVAL_EXPIRED",
        "APPROVAL_REPLAYED",
        "APPROVAL_REVOKED",
        "INVALID_APPROVAL",
        "POLICY_REVISION_MISMATCH",
        "SCOPE_UPGRADE_REQUIRES_APPROVAL",
        "SCOPE_CONFIRMATION_REQUIRED",
        "PERSISTENCE_REQUIRES_APPROVAL",
        "INVALID_PLAN",
        "PROVENANCE_FAILED",
        "UNSUPPORTED_BACKEND",
        "OWNERSHIP_REQUIRED",
        "ROOT_NOT_RESOLVED",
        "ROOT_MARKER_MISSING",
        "ROOT_MARKER_INVALID",
        "SCHEMA_UNSUPPORTED",
    }
)

#: Deliberately **not** in the set above, though the first version of this table had them there:
#: `INSTANCE_CONFLICT` is emitted by `simulate._fail` *after* the operation started — the store path
#: was compared and holds a different payload — so by this module's own rule (a verdict is decided
#: before anything runs; a failure is what happens once it has) it is a `failed`, not a `rejected`.
#: `DIGEST_MISMATCH` and `PAYLOAD_MISSING` are the same shape and were never in the set. Recording
#: the exception here rather than silently narrowing the set: a rule and a table that disagree is how
#: this project's recurring defect starts.

#: `common.$defs.evidence.detail` is `minLength: 1, maxLength: 2048`; a filesystem message can be
#: longer than that on a deep path, and a document the schema refuses is not something this harness
#: may return — so long details are truncated with a marker rather than dropped.
MAX_DETAIL = 2048
_TRUNCATION_MARKER = " ... [truncated]"


class HarnessDefect(RuntimeError):
    """A bug in this harness rather than a verdict about the request.

    Raised only for the two things a returned document must never paper over: an answer the published
    `broker-response` schema refuses (the core's `SELF_VALIDATION_FAILED` rule), and a failure whose
    reason code is not registered, so there is no exit code it could honestly carry.
    """


# --------------------------------------------------------------------------------------------- #
# the mode guard
# --------------------------------------------------------------------------------------------- #


def compatibility_block() -> dict[str, str]:
    """The two constants every answer carries, as a fresh dict (safe for a caller to mutate)."""

    return {"security_mode": HARNESS_SECURITY_MODE, "enforcement": HARNESS_ENFORCEMENT}


def refusal_for_mode(security_mode: str) -> AirootError | None:
    """The refusal for a request to answer in anything but User compatibility mode, else ``None``.

    Draft §E1 asked for exactly this rule: a harness that cannot enforce a boundary must not be able
    to *relabel* itself into one. `protected_machine` is a claim about ACLs on the protected zone and
    about a separate elevated process — neither of which an in-process call has — so a caller asking
    for it is told `PRIVILEGE_REQUIRED` (exit 5: "this needs the protected state that is not here")
    rather than being handed `policy_only` under a protected label. `NOT_IMPLEMENTED` (exit 1) would
    be the other defensible spelling; exit 5 is chosen because the thing that is missing is precisely
    the elevated, ACL-enforcing execution the mode names (ADR-0025's D1 leaves it to P2), not the
    operation itself, which this harness does implement.
    """

    if security_mode == HARNESS_SECURITY_MODE:
        return None
    return AirootError(
        "PRIVILEGE_REQUIRED",
        f"this harness answers only as {HARNESS_SECURITY_MODE}; it refuses to answer as "
        f"{security_mode!r}",
        evidence=[
            "an in-process binding has no ACL on the protected zone and no separate process, so it "
            "cannot enforce anything (docs/broker §2, User compatibility mode)",
            f"the two constants it can honestly carry are {HARNESS_SECURITY_MODE} and "
            f"{HARNESS_ENFORCEMENT}",
            "the protected mode needs the elevated broker this build does not have (ADR-0025 D1)",
        ],
    )


def evidence_object(kind: str, detail: Any, *, path: str | Path | None = None) -> dict[str, Any]:
    """One `common.$defs.evidence` item: `{kind, detail}` plus an optional `path`.

    The single place the object shape is built, because the neighbouring shape — the error envelope's
    list of strings — is the trap `broker/protocol.py` documents and this module must not repeat.
    """

    text = str(detail)
    if len(text) > MAX_DETAIL:
        text = text[: MAX_DETAIL - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    item: dict[str, Any] = {"kind": kind, "detail": text}
    if path is not None:
        item["path"] = str(path)[:MAX_DETAIL]
    return item


# --------------------------------------------------------------------------------------------- #
# the operation context: a temporary root, opened the way the core opens one
# --------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _OperationContext:
    """What an operation is run against: the root, its registry, and when it is asked to stop.

    ``injector`` is the harness's one connection to the fault-injection seam the transaction engine
    already has (`tx/journal.py`'s checkpoint hook). It is not a broker feature: it exists so a test
    can stop a commit mid-flight and then ask `recover_transaction` to finish it, which is the only
    way to exercise recovery without hand-writing a journal envelope.
    """

    root: root_module.RootInfo
    registry: Registry
    clock: Any = SYSTEM_CLOCK
    injector: Any = None


# --------------------------------------------------------------------------------------------- #
# reading the two referenced documents
# --------------------------------------------------------------------------------------------- #


def _resolve_ref(context: _OperationContext, ref: str) -> Path:
    """The path a `plan_ref`/`approval_ref` names, refusing anything that escapes the root.

    The reference is a *root-relative* path by contract (`state/plans/...`, `state/approvals/...`),
    and `paths.from_root_relative` is the repo's one traversal rejection — so a wire path like
    `state/plans/../../../etc/hosts` is refused as `PATH_ESCAPES_ROOT` instead of being joined onto
    the root and read.
    """

    return from_root_relative(ref, context.root.path)


def _load_ref(context: _OperationContext, ref: str, schema: str, *, kind: str) -> dict[str, Any]:
    """Read and validate one referenced document, or refuse with a registered code.

    A missing file is `NOT_FOUND` (exit 1) with the resolved path as evidence — deliberately not
    `INVALID_INPUT`, which would blame the request's *shape* for a document that simply is not there,
    and not an approval code, which would claim a decision about a token nobody produced. The
    document that *is* there but does not conform is the schema's refusal, carried with the code its
    consumer uses (`INVALID_PLAN` / `INVALID_APPROVAL`).
    """

    path = _resolve_ref(context, ref)
    if not path.is_file():
        raise AirootError(
            "NOT_FOUND",
            f"no {kind} document at {path}",
            evidence=[str(path), f"the request names it as {ref}"],
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AirootError(
            "INVALID_PLAN" if schema == "plan" else "INVALID_APPROVAL",
            f"{kind} document is not readable JSON: {path}",
            evidence=[f"{error.__class__.__name__}: {error}"],
        ) from error
    if not isinstance(document, dict):
        raise AirootError(
            "INVALID_PLAN" if schema == "plan" else "INVALID_APPROVAL",
            f"{kind} document is not a JSON object: {path}",
        )
    schema_io.validate_document(
        schema, document, reason_code="INVALID_PLAN" if schema == "plan" else "INVALID_APPROVAL"
    )
    return document


def _plan_and_token(context: _OperationContext, request: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two referenced documents, or `INVALID_INPUT` when the request names neither.

    The check is not redundant with `protocol.build_request`, which refuses a bare `gc_apply` on the
    caller's behalf: a hand-written request can skip the codec, and the published schema says nothing
    about what `gc_apply` must carry — so a request that reaches here without the refs must be
    answered as bad *input*, not as the harness failing to find a key nobody supplied.
    """

    missing = [name for name in ("plan_ref", "approval_ref") if request.get(name) is None]
    if missing:
        raise AirootError(
            "INVALID_INPUT",
            f"operation {request.get('operation')!r} requires {', '.join(missing)}",
            evidence=[f"{name} is missing from the request" for name in missing],
        )
    plan = _load_ref(context, str(request["plan_ref"]), "plan", kind="plan")
    token = _load_ref(context, str(request["approval_ref"]), "approval-token", kind="approval")
    return plan, token


# --------------------------------------------------------------------------------------------- #
# the four operations — each deferred to the in-process implementation that already exists
# --------------------------------------------------------------------------------------------- #


def _commit_plan(context: _OperationContext, request: dict) -> dict[str, Any]:
    """`commit_plan` → `tx/simulate.py` `SimulationRunner.commit` (the fake-fixture backend).

    No second implementation of the transaction lives here: the plan and the token are read off
    disk exactly as the CLI reads them, and the runner drives the frozen state machine. The
    artifact runner (`tx/artifact.py`) is the real-artifact twin and is deliberately not reachable
    from this harness — a request naming a real artifact belongs to P4's backend, not to a loopback.
    """

    plan, token = _plan_and_token(context, request)
    runner = simulate.SimulationRunner(
        context.registry, clock=context.clock, injector=context.injector
    )
    return runner.commit(plan, token)


def _recover_transaction(context: _OperationContext, request: dict) -> dict[str, Any]:
    """`recover_transaction` → `tx/journal.py` `classify` + `tx/simulate.py` `repair`."""

    transaction_id = str(request["transaction_id"])
    tx, _plan, _token = journal_module.TransactionJournal(
        context.registry, clock=context.clock, injector=context.injector
    ).load_context(transaction_id)
    # `classify` is read first even though `repair` reads it again, so the answer can name the action
    # the journal asked for. The decision itself stays in one place: `repair` is what acts on it.
    action = journal_module.classify(tx)
    outcome = simulate.repair(context.registry, transaction_id, clock=context.clock)
    return {"transaction": tx, "action": action, "outcome": outcome}


def _probe_root(context: _OperationContext, _request: dict) -> dict[str, Any]:
    """`probe_root` → `root.py` `open_root` plus `caps/acl.py` `capture_acl`, both read-only.

    The answer is the observation, not an opinion: the root marker's identity, the volume guard's
    verdict (``open_root`` re-reads the volume serial unless it is asked not to), and the DACL
    snapshot's owner / ACE count / digest. **The observed values are machine fingerprints** (owner
    and trustee SIDs), so the SIDs themselves are not put in the evidence: presence and counts are
    what a caller needs, and a returned document is not the place for a machine identity
    (AGENTS.md §9).
    """

    snapshot = acl.capture_acl(context.root.path)
    entries = len(snapshot.entries)
    return {
        "operation": "probe_root",
        "root_instance_id": context.root.root_instance_id,
        "volume_verified": True,
        "acl_observed": snapshot.observed,
        "acl_reason": snapshot.reason,
        "acl_owner_present": snapshot.owner is not None,
        "acl_dacl_present": snapshot.dacl_present,
        "acl_entry_count": entries,
        # The digest is a 64-hex sha256 with no machine-identifying content, so it is safe to carry;
        # the SIDs behind it are named by count only.
        "acl_digest": acl.acl_digest(snapshot) if snapshot.observed else None,
        # No SIDs and no owner name: they are machine fingerprints (AGENTS.md §9), and a returned
        # document is not the place for an identity. The count is what a caller can act on. A first
        # version of this field printed the sorted SID list under the comment above saying the SIDs
        # were left out — the §112 measurement caught a document disagreeing with its own docstring.
        "acl_trustees": f"{entries} trustee(s) observed; SIDs withheld because they are machine "
        "fingerprints (AGENTS.md §9)",
        "identity_note": (
            "the harness did not read the caller's token; the `client` block is the caller's own "
            "claim, so this probe says nothing about who asked"
        ),
    }


def _gc_apply(context: _OperationContext, request: dict) -> dict[str, Any]:
    """`gc_apply` → `caps/lifecycle.py` `apply_gc_plan` (plan + token, both read off disk)."""

    plan, token = _plan_and_token(context, request)
    return lifecycle.apply_gc_plan(
        context.registry, plan, token, root=context.root.path, clock=context.clock
    )


#: The dispatch table, as data: `operation` → the function that runs the existing implementation.
#: Keeping it a module-level table means the wire vocabulary and the callable set are one object.
DISPATCH: dict[str, Callable[[_OperationContext, dict], dict[str, Any]]] = {
    "commit_plan": _commit_plan,
    "recover_transaction": _recover_transaction,
    "probe_root": _probe_root,
    "gc_apply": _gc_apply,
}


# --------------------------------------------------------------------------------------------- #
# turning an operation's outcome (or an in-process refusal) into a response document
# --------------------------------------------------------------------------------------------- #


def _exit_code_for(code: str) -> int:
    """The exit code `code` maps to, or a harness defect when that code does not exist.

    "Do not invent a code" is the rule this function enforces from the inside: an `AirootError` cannot
    even be constructed with an unregistered code, so any other source of a code would be a bug in
    this module and is reported as one rather than rendered into a document.
    """

    try:
        return REASON_EXIT[code]
    except KeyError as error:  # pragma: no cover - only reachable through a harness bug
        raise HarnessDefect(f"unregistered reason code reached the harness: {code!r}") from error


def _status_class(code: str) -> str:
    """Which of the published statuses a reason code expresses.

    Read off the exit code for the success case first, so it cannot be forgotten: `SUCCESS` (and the
    other informational exit-0 codes) mean `ok`, and a mapping that reported "success" as a failure
    would be the loudest possible lie. `RECOVERY_REQUIRED` is its own status — deliberately named
    rather than derived from exit 6, because that exit code *also* carries `JOURNAL_TRUNCATED` ("the
    journal cannot be read, so nothing may proceed"), which is a failure, not a recoverable state.
    The refusal set then decides between `rejected` and `failed`.
    """

    if _exit_code_for(code) == EXIT_SUCCESS:
        return "ok"
    if code == "RECOVERY_REQUIRED":
        return "recovery_required"
    if code in REJECTION_CODES:
        return "rejected"
    return "failed"


def _refusal_document(
    request: Any, error: AirootError, *, transaction_id: str | None = None, state: str | None = None
) -> dict[str, Any]:
    """A refusal as a returned document — the whole reason `serve` never raises for a domain failure.

    The status is not chosen here: `_answer` derives it from the reason code, so a refusal and a
    failed operation cannot be classified by two different rules.
    """

    code = error.reason_code
    evidence = [evidence_object("reason", error.message)]
    evidence += [evidence_object("evidence", line) for line in error.evidence]
    for key, value in sorted((error.details or {}).items()):
        if isinstance(value, (str, int, float, bool)) or value is None:
            evidence.append(evidence_object("detail", f"{key}={value}"))
    return _answer(
        request,
        reason_code=code,
        transaction_id=transaction_id,
        state=state,
        evidence=evidence,
        retryable=code in {"PENDING_TRANSACTION", "STALE_GENERATION", "RECOVERY_REQUIRED"},
    )


def _commit_outcome(result: dict[str, Any]) -> tuple[str, str | None, bool]:
    """`(reason_code, state, retryable)` read off a transaction the runner returned.

    A commit that broke after the binding moved comes back `ROLLED_BACK` with the failure under
    `failure.code`; a commit that broke before it comes back `ROLLED_BACK` too (from `_fail`), so the
    transaction's own code is the only honest source for "why". An interrupted one is not a verdict
    at all: the journal has to be reconciled, which is exit 6.
    """

    state = str(result.get("state"))
    if result.get("interrupted"):
        return "RECOVERY_REQUIRED", state, True
    code = result.get("outcome")
    if not isinstance(code, str):
        failure = result.get("failure") or {}
        code = failure.get("code")
    if not isinstance(code, str):
        code = "SUCCESS" if state == "FINALIZED" else "SELF_VALIDATION_FAILED"
    retryable = bool((result.get("failure") or {}).get("retryable", False))
    return code, state, retryable


def _recover_outcome(result: dict[str, Any]) -> dict[str, Any]:
    """The response fields a recovery answer carries, plus the evidence describing what it did."""

    tx = result["transaction"]
    action = result["action"]
    outcome = result["outcome"]
    state = str(outcome.get("state") or tx["state"])
    # A recovery that changed nothing is a success, not a placeholder: `classify` said `no_action`
    # because the transaction was already terminal, and inventing work would be worse than saying so.
    evidence = [
        evidence_object("journal", f"state {tx['state']} -> {state}"),
        evidence_object("recovery", f"classified action: {action.action}"),
    ]
    if outcome.get("result") is not None:
        evidence.append(
            evidence_object("recovery", f"resumed to {outcome['result'].get('state', state)}")
        )
    return {
        "reason_code": "SUCCESS",
        "transaction_id": str(tx["transaction_id"]),
        "state": state,
        "evidence": evidence,
        "retryable": False,
    }


def _probe_outcome(result: dict[str, Any]) -> dict[str, Any]:
    """`probe_root`'s answer: a report, with no transaction and no state to name.

    `broker-response` requires both keys, so they are present and **null** — the schema's own way of
    saying "this operation has neither". A fabricated transaction id would be a lie about history.
    """

    acl_summary = evidence_object(
        "acl",
        f"observed={result['acl_observed']} dacl_present={result['acl_dacl_present']} "
        f"entries={result['acl_entry_count']} owner_present={result['acl_owner_present']}",
    )
    if result.get("acl_path"):
        # The directory the observation was made against, so the answer is about a place and not a
        # floating number. Absent rather than empty when the caller assembled the fields itself.
        acl_summary["path"] = str(result["acl_path"])[:MAX_DETAIL]
    evidence = [
        evidence_object("root", f"root_instance_id={result['root_instance_id']}"),
        evidence_object("capability", "opened through root.open_root with the volume guard enabled"),
        acl_summary,
        evidence_object("acl", result["acl_trustees"]),
    ]
    if result.get("acl_reason"):
        # Failure is data (caps/acl.py): an unreadable DACL is reported as a reason, never as "empty".
        evidence.append(evidence_object("acl", f"the DACL could not be read: {result['acl_reason']}"))
    if result.get("acl_digest"):
        observed = evidence_object("acl", "the observed DACL, as a digest (no SIDs behind it)")
        observed["digest"] = result["acl_digest"]
        evidence.append(observed)
    evidence.append(evidence_object("note", result["identity_note"]))
    return {
        "reason_code": "SUCCESS",
        "transaction_id": None,
        "state": None,
        "evidence": evidence,
        "retryable": False,
    }


def _gc_outcome(result: dict[str, Any], request: dict) -> dict[str, Any]:
    """`gc_apply`'s answer. Like `probe_root`, it has no transaction and no transaction state.

    The delete is not journalled as a transaction by `apply_gc_plan`: it writes `GC_INTENT` and
    `GC_APPLIED` events around the `rmtree`, and the registry row is retained for binding history.
    So `transaction_id` and `state` are null for the same reason as `probe_root` — there is no
    transaction to name — and the operation's own result fields carry the outcome instead.
    """

    evidence = [
        evidence_object("plan", f"plan_hash={result['plan_hash']} plan_id={result['plan_id']}"),
        evidence_object(
            "store",
            f"instance_id={result['instance_id']} store_path={result['store_path']} "
            f"payload_removed={result['payload_removed']} "
            f"already_collected={result['already_collected']}",
        ),
        evidence_object(
            "steward",
            f"data_root_files_touched={result['data_root_files_touched']}; deletion grading is "
            "enforced by caps/lifecycle.py, not by this harness",
        ),
        evidence_object("approval", f"consumed approval {request.get('approval_ref')}"),
    ]
    return {
        "reason_code": "SUCCESS",
        "transaction_id": None,
        "state": None,
        "evidence": evidence,
        "retryable": False,
    }


def _answer(
    request: Any,
    *,
    reason_code: str,
    transaction_id: str | None,
    state: str | None,
    evidence: list[dict[str, Any]],
    retryable: bool,
) -> dict[str, Any]:
    """Assemble the `broker-response` envelope and validate it before anyone sees it.

    `status` is **derived** from `reason_code` rather than passed in, so a refusal, a failed
    operation and a success cannot be classified by three different rules — one table
    (:func:`_status_class`) decides, and both the refusal path and the success path come through
    here. Every field the published schema requires is set; the security-mode constants are applied
    last and unconditionally, so no branch of this module can produce an answer that carries a
    stronger mode than the harness can honestly claim.
    """

    # `reason_code` may be null in the schema (a success needs no code), but this harness always has a
    # registered one for every answer it produces, so it always names it rather than leaving the field
    # for a reader to interpret.
    document: dict[str, Any] = {
        "schema_version": 1,
        "request_id": str(request.get("request_id", "")) if isinstance(request, dict) else "",
        "status": _status_class(reason_code),
        "transaction_id": transaction_id,
        "state": state,
        "reason_code": reason_code,
        "evidence": evidence,
        "retryable": retryable,
    }
    document.update(compatibility_block())

    problems = schema_io.errors_for("broker-response", document)
    if problems:
        # The core's rule (AGENTS.md §7): the outward document is self-validated, and a failure is
        # this build's bug. Raised instead of returned because a document the published schema
        # refuses is not a refusal — returning it would be the harness lying about its own output.
        raise HarnessDefect(
            "broker-response rejected a document produced by the fake broker harness: "
            + "; ".join(problems[:8])
        )
    return document


def _outcome_fields(
    operation: str, outcome: dict[str, Any], request: dict
) -> dict[str, Any]:
    """The response fields one operation's finished result maps to, as `_answer` takes them."""

    if operation == "probe_root":
        return _probe_outcome(outcome)
    if operation == "gc_apply":
        return _gc_outcome(outcome, request)
    if operation == "recover_transaction":
        return _recover_outcome(outcome)
    code, state, retryable = _commit_outcome(outcome)
    return {
        "reason_code": code,
        "transaction_id": str(outcome.get("transaction_id") or "") or None,
        "state": state,
        "evidence": _commit_evidence(outcome, request),
        "retryable": retryable,
    }


# --------------------------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------------------------- #


def serve(
    request: dict,
    *,
    root: Any = None,
    security_mode: str = HARNESS_SECURITY_MODE,
    clock: Any = SYSTEM_CLOCK,
    injector: Any = None,
) -> dict:
    """Answer a broker-request document, in process. Returns a document that validates against
    `broker-response`. Never raises for a domain failure: a refusal is a returned document.

    ``root`` is the AIROOT root to run against — a temporary one, in tests. It may be a
    ``pathlib.Path`` or an already-opened `root.RootInfo`; when omitted the root is resolved the way
    the CLI resolves one (`--root` → `AIROOT_HOME` → fail closed). ``commit_plan``, `gc_apply` and
    `recover_transaction` need an initialised SQLite registry under that root (`Registry.initialize`),
    because the operations they dispatch to are the ones that write registry state; `probe_root` needs
    only the layout and the root marker.

    ``security_mode`` is what the caller is **asking** the harness to answer as, not a label the
    harness adopts. Only User compatibility mode can be answered; anything else is refused before an
    operation runs (see :func:`refusal_for_mode`).

    ``injector`` is the transaction engine's fault-injection seam, forwarded so a test can interrupt a
    commit and then exercise `recover_transaction` on the resulting journal. It is not a wire field
    and not a broker feature.
    """

    # The mode guard runs first, on a request that may not even be schema-valid: "do not relabel
    # yourself" must not be reachable only through a well-formed request.
    refused = refusal_for_mode(security_mode)
    if refused is not None:
        return _refusal_document(request, refused)

    # A *received* request is validated as input: a malformed one is the caller's mistake and comes
    # back as a refusal naming what was wrong, never as an exception out of the harness.
    try:
        schema_io.validate_document("broker-request", request, reason_code="INVALID_INPUT")
    except AirootError as error:
        return _refusal_document(request, error)

    operation = request.get("operation")
    handler = DISPATCH.get(operation)
    if handler is None:
        return _refusal_document(
            request,
            AirootError(
                "INVALID_INPUT",
                f"unknown broker operation: {operation!r}",
                evidence=[f"operation must be one of: {', '.join(SUPPORTED_OPERATIONS)}"],
            ),
        )

    try:
        context = _open_context(root, clock=clock, injector=injector)
    except AirootError as error:
        # Booting the root is part of answering: no root, no marker, no volume match, no registry —
        # all of them are verdicts about the request, so they come back as documents too.
        return _refusal_document(request, error)

    try:
        outcome = handler(context, request)
    except AirootError as error:
        # The refusal paths live here: a plan whose content moved after approval, a replayed nonce, a
        # transaction the journal cannot resume, and every other domain verdict the in-process
        # operations already produce (no second copy of any of them exists in this module).
        transaction_id, state = _identify(request, error)
        return _refusal_document(request, error, transaction_id=transaction_id, state=state)
    except HarnessDefect:
        raise
    except Exception as error:  # noqa: BLE001 - the contract is "never raise for a domain failure"
        # A failure the in-process implementations did not anticipate must still not escape as an
        # exception, or the one property that makes this harness usable end-to-end is gone. It is
        # reported honestly as a defect of the harness rather than as a verdict about the request.
        return _refusal_document(
            request,
            AirootError(
                "SELF_VALIDATION_FAILED",
                f"the harness hit an unhandled {error.__class__.__name__} while answering "
                f"{operation}",
                evidence=[
                    f"{error.__class__.__name__}: {error}",
                    "this is a defect in the test harness (cli/tests/fake_broker.py), not a verdict "
                    "about the request",
                ],
            ),
        )

    if operation == "probe_root":
        # The probe's evidence names the directory it observed; the operation itself returns only the
        # observation, so the path is attached where the answer is assembled.
        outcome["acl_path"] = str(context.root.path)
    return _answer(request, **_outcome_fields(operation, outcome, request))


def _commit_evidence(result: dict[str, Any], request: dict) -> list[dict[str, Any]]:
    """What a commit answer says about itself: the plan, the token, the move it made, the outcome."""

    evidence = [
        evidence_object("plan", f"plan_id={result.get('plan_id')} plan_hash={result.get('plan_hash')}"),
        evidence_object("transaction", f"transaction_id={result.get('transaction_id')}"),
        evidence_object(
            "transaction",
            f"generation {result.get('generation_before')} -> {result.get('generation_after')} "
            f"(ACTIVE_BOUND is the only point that may change the active binding)",
        ),
        evidence_object("approval", f"approval_id={result.get('approval_id')} request={request.get('request_id')}"),
    ]
    if result.get("interrupted"):
        evidence.append(
            evidence_object(
                "journal",
                "the commit was interrupted mid-flight; the journal is the recovery authority, so "
                "this is not a verdict (ask recover_transaction)",
            )
        )
    failure = result.get("failure") or {}
    if failure:
        evidence.append(
            evidence_object("failure", f"{failure.get('code')}: {failure.get('message')}")
        )
        evidence += [evidence_object("failure", item.get("detail", "")) for item in failure.get("evidence", [])]
    if result.get("idempotent"):
        evidence.append(
            evidence_object(
                "idempotent",
                "this plan and token were already applied; the answer is the existing transaction",
            )
        )
    return evidence


def _identify(request: dict, error: AirootError) -> tuple[str | None, str | None]:
    """The `transaction_id`/`state` a refusal can honestly carry, or ``(None, None)``.

    A refusal knows the transaction only when the request named one (`recover_transaction`) or when
    the error itself carries one in its details. It never infers a state from the code: "rejected"
    with a made-up state would be a claim about a record nobody read.
    """

    details = error.details or {}
    transaction_id = details.get("transaction_id")
    if transaction_id is None and isinstance(request.get("transaction_id"), str):
        transaction_id = request["transaction_id"]
    state = details.get("state")
    return (
        str(transaction_id) if transaction_id is not None else None,
        str(state) if state is not None else None,
    )


def _open_context(root: Any, *, clock: Any, injector: Any) -> _OperationContext:
    """Open the root (with its volume guard) and its registry, or refuse with a registered code."""

    if root is None:
        info = root_module.open_root(env=None)
    elif isinstance(root, root_module.RootInfo):
        info = root
    else:
        info = root_module.open_root(root)
    try:
        registry = Registry.open(info.path, clock=clock)
    except AirootError:
        raise
    return _OperationContext(root=info, registry=registry, clock=clock, injector=injector)
