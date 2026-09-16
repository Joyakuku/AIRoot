"""L1: the read-only process identity probe behind a broker request's `client` block (draft §108).

Phase P2's broker is a separate, elevated process, and ``broker-request.schema.json`` requires the
caller to introduce itself with exactly ``sid``, ``pid``, ``integrity`` and ``application_id``.
Nothing in this repository could produce that block before this module. The tests here pin the four
properties a caller has to be able to rely on:

* **it works on a real machine, unprivileged** — a process may always open *its own* token, so this is
  testable without admin, and the tests pass whether or not the session is elevated;
* **what it emits satisfies the published schema** — the core's rule: a document it produces must pass
  the schema that describes it (``test_l0_consistency`` guards the schema; this guards the document);
* **unknown is an answer, not an exception** — the failure path is exercised deliberately, because the
  interesting behaviour of a probe is what it does when the OS says no;
* **`elevated` is read, never assumed** — the field must be able to be ``False``, which is the one
  outcome a hard-coded ``True`` or an ``IsUserAnAdmin`` shortcut would quietly get wrong.

Draft §113 adds the other half: `probe_process` reads a *different* process's token, which is what the
broker has in front of it (docs/broker §3:52). The additions below pin what that half owes:

* **the two paths share one implementation** — the one deterministic, host-free cross-check available
  is this process read as a *target*: it must agree with `probe_identity()` field for field, which is
  only true if both read the token through the same helpers;
* **a second real process** — a spawned child is the case the broker actually meets, and it is read
  without any privilege because it is a child this process created;
* **the failure paths are injected, not waited for** — a healthy machine's `OpenProcess` and
  `OpenProcessToken` succeed, so "no such process" and "token refused" are reached by replacing the
  module's own seams (the same shape as the unreadable own-token test above);
* **`None` and `False` are different facts about a target** — "could not look" must never be reported
  as "looked, and it is not elevated", and each side is asserted against the other.

Draft §114 widens that half to the facts a *server* has to read for itself, because the `client` block
cannot carry them (docs/broker §3:52 asks for token, user SID, integrity level and application
identity; `broker-request`'s `client` block has four frozen fields). What the tests below pin:

* **every new field on a real process** — this one and a spawned child, with `session_id` checked
  against `ProcessIdToSessionId` called directly rather than through the probe;
* **two readings are held to each other** — `elevation_type` against `elevated`, the token's session
  against the kernel's; agreement is what lets either be believed, and disagreement withdraws the
  fact instead of picking a side (both numbers named);
* **`None` is not a synonym for "no"** — a non-AppContainer token has *no* application identity and
  says so in words, which is a different answer from "the flag could not be read";
* **an injected failure per read** — a machine's healthy calls cannot be made to fail on demand, so
  every refusal branch is driven through the module's own seams, and each refusal has to name the call
  and the Windows status;
* **a null in a `client` block is refused at the source** — the document it used to emit fails the
  published schema, and that failure would be blamed on the caller, so `to_document` raises
  `SELF_VALIDATION_FAILED` (exit 8) naming the schema field instead.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from airoot import schema_io
from airoot.caps import identity
from airoot.exits import AirootError

#: A plausible-looking application id: the schema's `id` pattern needs a lowercase first character.
APPLICATION_ID = "airoot-cli"

#: A SID that satisfies the schema's pattern but belongs to no real token — for the "complete
#: identity" case, where the point is the *shape*, not the value.
SYNTHETIC_SID = "S-1-5-21-1000-1000-1000-1001"

#: The exact evidence line an ordinary (non-AppContainer) target must produce (draft §114), asserted
#: as a literal rather than imported from the module. The words are the contract: a reader has to be
#: able to tell "this token carries no application identity" apart from "nobody managed to look".
NOT_AN_APP_CONTAINER = (
    "TokenIsAppContainer -> False; not in an AppContainer: there is no application identity in the "
    "token to read"
)


def complete(sid: str = SYNTHETIC_SID, integrity: str = "medium", pid: int = 4242) -> identity.ClientIdentity:
    """A synthetic identity with every field present; used where a real token would only add noise."""

    return identity.ClientIdentity(
        sid=sid,
        integrity=integrity,
        elevated=False,
        evidence=("synthetic",),
        pid=pid,
    )


def test_the_token_information_classes_are_the_abi_numbers() -> None:
    """These are dispatched on by the OS, so a typo is not a style problem: it reads another class.

    The sibling USN test pins its control codes for the same reason (`test_ctl_codes_match_the_
    documented_values`, ADR-0020) — a wrong number there was silent, and a wrong number here would
    silently report a different fact about the process.
    """

    assert identity.TOKEN_QUERY == 0x0008
    assert identity.TOKEN_USER == 1
    assert identity.TOKEN_ELEVATION == 20
    assert identity.TOKEN_INTEGRITY_LEVEL == 25
    # draft §114 added four classes to the same dispatch table, and they are pinned here for exactly
    # the same reason as the three above: a typo reads a *different* token fact and says so
    # confidently.
    assert identity.TOKEN_SESSION_ID == 12
    assert identity.TOKEN_ELEVATION_TYPE == 18
    assert identity.TOKEN_IS_APP_CONTAINER == 29
    assert identity.TOKEN_APP_CONTAINER_SID == 31
    assert identity._ELEVATION_TYPES == {1: "default", 2: "full", 3: "limited"}
    assert (identity._APP_CONTAINER_FALSE, identity._APP_CONTAINER_TRUE) == (0, 1)


def test_probe_identity_returns_an_object_and_the_real_pid() -> None:
    """The one field that cannot fail has to be right, and nothing here may raise."""

    observed = identity.probe_identity()

    assert isinstance(observed, identity.ClientIdentity)
    assert observed.pid == os.getpid()
    assert observed.evidence, "a probe with no observations at all would say nothing about the machine"


def test_a_complete_identity_builds_a_client_block_the_published_schema_accepts() -> None:
    """Validated through a whole broker request, so the client block is checked where it really lives.

    A `client` sub-schema validated on its own would miss the enclosing `additionalProperties: false`
    and the `required` list — the two things this document could most easily violate.
    """

    observed = complete()
    document = observed.to_document(APPLICATION_ID)

    assert set(document) == {"sid", "pid", "integrity", "application_id"}
    assert document == {
        "sid": SYNTHETIC_SID,
        "pid": 4242,
        "integrity": "medium",
        "application_id": APPLICATION_ID,
    }

    request = {
        "protocol_version": 1,
        "request_id": "req-identity-probe",
        "operation": "probe_root",
        "client": document,
    }
    assert schema_io.errors_for("broker-request", request) == []


@pytest.mark.parametrize("integrity", ["low", "medium", "high", "system"])
def test_every_integrity_word_the_schema_allows_is_accepted(integrity: str) -> None:
    """The mapping has exactly four outputs, and all four have to fit the schema's enum."""

    request = {
        "protocol_version": 1,
        "request_id": "req-identity-probe",
        "operation": "probe_root",
        "client": complete(integrity=integrity).to_document(APPLICATION_ID),
    }
    assert schema_io.errors_for("broker-request", request) == []


def test_a_real_probe_that_is_complete_validates_against_the_published_client_schema() -> None:
    """The same check against the machine's own answer, skipped only when the token was unreadable.

    Skipping on `not is_complete()` rather than asserting completeness is deliberate: an unreadable
    token is a legitimate outcome this module is built to report, and a test that failed for it would
    be asserting a privilege level rather than a behaviour.
    """

    observed = identity.probe_identity()
    if not observed.is_complete():
        pytest.skip(f"this machine's token was not fully readable: {'; '.join(observed.evidence)}")

    request = {
        "protocol_version": 1,
        "request_id": "req-identity-probe",
        "operation": "probe_root",
        "client": observed.to_document(APPLICATION_ID),
    }
    assert schema_io.errors_for("broker-request", request) == []


def test_the_real_sid_looks_like_a_sid_without_being_a_machine_fingerprint() -> None:
    """`S-1-...` shape only. The value itself is a fingerprint and must never be written down here.

    ``AGENTS.md`` §9: this repository is public, so a test asserting a real user SID would commit the
    machine's identity. The assertion is therefore about the shape and about the evidence naming its
    source, which is what a reviewer needs to see anyway.
    """

    observed = identity.probe_identity()
    if observed.sid is None:
        pytest.skip("the token's user SID was not readable on this machine")

    assert observed.sid.startswith("S-1-")
    assert any(line.startswith("TokenUser ->") for line in observed.evidence)


def test_is_complete_is_exactly_the_two_facts_only_the_os_can_supply() -> None:
    """`pid` is always there and `application_id` comes from the caller, so those two decide it."""

    assert complete().is_complete() is True

    missing_sid = identity.ClientIdentity(sid=None, integrity="medium", elevated=False, pid=1)
    missing_integrity = identity.ClientIdentity(sid=SYNTHETIC_SID, integrity=None, elevated=False, pid=1)

    assert missing_sid.is_complete() is False
    assert missing_integrity.is_complete() is False
    # `elevated` is *not* part of completeness: it is not one of the schema's required client fields.
    assert identity.ClientIdentity(
        sid=SYNTHETIC_SID, integrity="medium", elevated=None, pid=1
    ).is_complete() is True


def test_elevated_can_be_false_which_is_why_it_is_not_hard_coded() -> None:
    """A limited token must be able to say so; this is the value a shortcut would get wrong.

    Nothing here reads the real token: the point is that the field's type admits both answers and that
    the constructor does not coerce them. The real value is asserted in the test below.
    """

    assert complete().to_document(APPLICATION_ID)["pid"] == 4242
    assert identity.ClientIdentity(
        sid=SYNTHETIC_SID, integrity="medium", elevated=False, pid=1
    ).elevated is False
    assert identity.ClientIdentity(
        sid=SYNTHETIC_SID, integrity="medium", elevated=True, pid=1
    ).elevated is True


def test_the_real_elevation_reading_is_a_boolean_that_matches_the_session() -> None:
    """`TokenElevation` answering `False` on this machine must not be reported as `True`.

    The assertion ties the probe to an independent observation from the same process's token: a
    restricted token whose integrity level is `low`/`medium` cannot be an elevated one. That is a
    one-way check on purpose — an elevated token may legitimately report `high` **or** `system`, so
    the converse would be a guess, and this module does not guess.
    """

    observed = identity.probe_identity()
    if observed.elevated is None:
        pytest.skip("the token's elevation could not be read on this machine")

    assert isinstance(observed.elevated, bool)
    if observed.integrity in {"low", "medium"}:
        assert observed.elevated is False, (
            "a low/medium integrity token cannot be an elevated one, so this reading contradicts an "
            "independent fact about the same token"
        )


def test_a_token_that_cannot_be_opened_still_reports_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure path, taken deliberately: making ``OpenProcessToken`` fail is the only way to see it.

    This is the test that matters most. On a healthy machine the real call succeeds, so the branch that
    handles refusal would otherwise never run until the day it matters — and a probe that raises there
    would leave the caller with nothing to report. It runs by replacing the module's own seam, so no
    privilege, no host change and no fake Windows API is involved.
    """

    def refuses(_advapi32: object, _kernel32: object) -> tuple[None, int]:
        return None, identity.ERROR_ACCESS_DENIED

    monkeypatch.setattr(identity, "_open_process_token", refuses)

    observed = identity.probe_identity()

    assert isinstance(observed, identity.ClientIdentity)
    assert observed.sid is None
    assert observed.integrity is None
    assert observed.elevated is None
    assert observed.pid == os.getpid()
    assert observed.is_complete() is False
    assert observed.evidence, "a refusal with no explanation is not data, it is silence"
    assert any(line.startswith("OpenProcessToken(TOKEN_QUERY) failed: err=5") for line in observed.evidence), (
        "the evidence has to name the call and carry the OS status; the text after the number is "
        f"localized and cannot be asserted: {observed.evidence}"
    )


def test_a_token_that_can_be_opened_but_not_read_reports_which_read_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the failure path: the handle is fine and every *read* on it fails.

    Keeping the two seams separate is what makes this test possible — `OpenProcessToken` and
    `GetTokenInformation` fail for different reasons on a real machine (no access to the process
    versus a class the token will not answer), and the evidence has to say which one happened.
    """

    def opens(_advapi32: object, _kernel32: object) -> tuple[object, int]:
        return object(), 0

    def unreadable(_advapi32: object, _handle: object, _info_class: int) -> tuple[None, int, int, int]:
        return None, 0, 0, identity.ERROR_ACCESS_DENIED

    monkeypatch.setattr(identity, "_open_process_token", opens)
    monkeypatch.setattr(identity, "_get_token_information", unreadable)

    observed = identity.probe_identity()

    assert observed.sid is None
    assert observed.integrity is None
    assert observed.elevated is None
    assert observed.is_complete() is False
    assert any("GetTokenInformation(TokenUser)" in line for line in observed.evidence)
    assert any("GetTokenInformation(TokenIntegrityLevel)" in line for line in observed.evidence)
    assert any("GetTokenInformation(TokenElevation)" in line for line in observed.evidence)


def test_an_integrity_sid_outside_the_four_words_is_unknown_rather_than_rounded() -> None:
    """A rid below `low` is real (`SECURITY_MANDATORY_UNTRUSTED_RID`, 0x0005) and is not one of the four
    words the schema allows, so it must be reported as unknown rather than rounded up to `low`.

    This is the module's "never fabricate certainty" rule at its sharpest point: the tempting answer
    (`low`) is wrong, and the honest one (`None`) is what the schema can carry. The mapped direction is
    asserted alongside it, because "unknown for everything" would pass this test and be useless.
    """

    import ctypes

    def label(rid: int) -> bytes:
        """A SID with one sub-authority, as `TokenIntegrityLevel` carries it: `S-1-16-<rid>`."""

        return bytes([1, 1, 0, 0, 0, 0, 0, 16]) + rid.to_bytes(4, "little")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    identity._declare_signatures(advapi32, kernel32)

    unknown, note = identity._integrity_word(advapi32, kernel32, label(0x0005))
    assert unknown is None
    assert "0x0005" in note

    # The ladder itself: every word has to be reachable, or this test would pass on a stub.
    assert identity._integrity_word(advapi32, kernel32, label(0x1000))[0] == "low"
    assert identity._integrity_word(advapi32, kernel32, label(0x2000))[0] == "medium"
    assert identity._integrity_word(advapi32, kernel32, label(0x3000))[0] == "high"
    assert identity._integrity_word(advapi32, kernel32, label(0x4000))[0] == "system"


def test_a_pointer_that_cannot_be_placed_is_unknown_rather_than_a_read_out_of_bounds() -> None:
    """The extractor must refuse a pointer it cannot place, instead of trusting it.

    The pointer inside a `SID_AND_ATTRIBUTES` record decides where the SID sits; if a future Windows
    build put it outside the buffer we own, reading there would read memory that is not ours. `None` is
    the only safe answer, and it is also the honest one — the caller learns "unknown", not a SID built
    from whatever happened to be at that address.
    """

    # A record whose pointer is zero: nothing to point at, which is not the same as "offset 0".
    assert identity._extract_sid(bytes(44), 0, 44) is None
    # A pointer that lands inside the record's own pointer field cannot be a SID either.
    inside_the_record = bytearray(44)
    inside_the_record[0:8] = (4).to_bytes(8, "little")
    assert identity._extract_sid(bytes(inside_the_record), 0, 44) is None
    # And a truncated view of an otherwise fine record must not be read past its end.
    sid = bytes([1, 1, 0, 0, 0, 0, 0, 16]) + (0x2000).to_bytes(4, "little")
    raw = bytearray(16 + len(sid))
    raw[0:8] = (16).to_bytes(8, "little")
    raw[16:] = sid
    assert identity._extract_sid(bytes(raw), 0, 20) is None


# --- draft §113: reading a *different* process's token -------------------------------------------


def _free_pid() -> int:
    """A pid that names no process on this machine, measured rather than assumed.

    ``OpenProcess`` is asked about descending multiples of four (Windows hands pids out in steps of
    four) and the first one answering ``ERROR_INVALID_PARAMETER`` is returned. That status *is* the
    measurement: a pid that exists but is protected answers ``ERROR_ACCESS_DENIED`` instead, so
    treating a denial as "free" would aim the test at a live process and assert the wrong thing about
    it. Starting high is what makes this cheap — the pid space above the live range is empty — and the
    walk is bounded so a broken measurement fails loudly instead of looping.

    The one live pid this depends on is checked first: if this process cannot be opened, the
    measurement is unsound and saying so beats returning a number nothing verified.
    """

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    identity._declare_signatures(advapi32, kernel32)

    live, _status = identity._open_process(kernel32, os.getpid())
    if live is None:
        raise AssertionError("the free-pid measurement is unsound: this process could not be opened")
    kernel32.CloseHandle(live)

    candidate = 0x7FFFFFFC
    for _attempt in range(64):
        handle, status = identity._open_process(kernel32, candidate)
        if handle is not None:
            kernel32.CloseHandle(handle)
        elif status == identity.ERROR_INVALID_PARAMETER:
            return candidate
        candidate -= 4
    raise AssertionError("no pid free of any process was found")


def test_the_process_access_right_and_the_status_codes_are_the_abi_numbers() -> None:
    """Same reasoning as the token classes above: these are numbers the OS dispatches on.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` is a right, not a hint — the wrong bit either fails on a
    target that is readable or asks for more access than a read-only observation needs. The two status
    codes are pinned beside it because the whole free-pid test below rests on ``OpenProcess``
    answering a *different* number for "no such process" than for "protected".
    """

    assert identity.PROCESS_QUERY_LIMITED_INFORMATION == 0x1000
    assert identity.ERROR_ACCESS_DENIED == 5
    assert identity.ERROR_INVALID_PARAMETER == 87


def test_this_process_reads_back_as_a_target_and_agrees_with_probe_identity() -> None:
    """The one deterministic cross-check available without a second user or a second machine.

    Both functions read the same token through the same helpers, so they must agree field for field:
    this is the integration proof that `probe_process` reuses that machinery instead of restating it,
    and `os.getpid()` is the one target whose answer can be known in advance. Unlike the own-token
    test above, completeness is *asserted* rather than skipped: there the machine's token may be
    unreadable for reasons this module cannot control, while here the probe opened a handle to the
    process running it — so an incomplete reading is this probe's defect, not the host's property.
    """

    own = identity.probe_identity()
    targeted = identity.probe_process(os.getpid())

    assert isinstance(targeted, identity.ProcessIdentity)
    assert targeted.pid == os.getpid()
    assert targeted.is_complete(), (
        "a process must be able to read its own token, so an incomplete reading means the "
        f"process-targeted path is broken: {targeted.evidence}"
    )
    assert targeted.sid == own.sid
    assert targeted.integrity == own.integrity
    assert targeted.elevated == own.elevated
    assert any(line.startswith("TokenUser ->") for line in targeted.evidence)


def test_a_real_child_process_is_observed_and_carries_the_parents_token() -> None:
    """A second process, not this one: the case the broker actually has in front of it.

    The child is spawned with no token of its own, so it inherits the parent's — SID, integrity level
    and elevation all have to match, and the SID is the fact that ties the reading to "the same user".
    No privilege is involved: a process may always query a child it created. The cleanup is a
    `finally` that reaps the child, because a sleeper left behind by a failed assertion would outlive
    the run that created it and the next run would inherit the mess.
    """

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        observed = identity.probe_process(child.pid)
        parent = identity.probe_process(os.getpid())

        assert observed.pid == child.pid
        assert observed.is_complete(), f"the child's token was not fully readable: {observed.evidence}"
        assert parent.is_complete(), f"this process's own token was not readable: {parent.evidence}"
        assert observed.sid == parent.sid, "a spawned child runs as the same user"
        assert observed.integrity == parent.integrity
        assert observed.elevated == parent.elevated
        assert any(line.startswith("TokenUser ->") for line in observed.evidence)
    finally:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - the child only sleeps
            child.kill()
            child.wait(timeout=15)


def test_a_process_that_cannot_be_opened_stops_before_the_token_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first failure path: the pid names nothing, or names a process this caller may not open.

    Two claims in one test because they are one decision — every field is `None` with an evidence line
    naming `OpenProcess` and the Windows status, and the token seam is **never reached**. Reading a
    token off a handle that was never opened is not a failure mode this module may have, and a probe
    that tried would report the second failure instead of the first.
    """

    def refuses(_kernel32: object, _pid: int) -> tuple[None, int]:
        return None, identity.ERROR_ACCESS_DENIED

    reached: list[int] = []

    def must_not_be_reached(*_args: object) -> tuple[None, int]:
        reached.append(1)
        return None, identity.ERROR_ACCESS_DENIED

    monkeypatch.setattr(identity, "_open_process", refuses)
    monkeypatch.setattr(identity, "_open_token", must_not_be_reached)

    observed = identity.probe_process(4242)

    assert isinstance(observed, identity.ProcessIdentity)
    assert observed.pid == 4242
    assert observed.sid is None
    assert observed.integrity is None
    assert observed.elevated is None
    assert observed.is_complete() is False
    assert reached == [], "the token seam was reached without a process handle"
    assert any(
        line.startswith("OpenProcess(pid=4242, PROCESS_QUERY_LIMITED_INFORMATION) failed: err=5")
        for line in observed.evidence
    ), f"the evidence has to name the call and carry the status: {observed.evidence}"


def test_an_unreachable_target_token_is_unknown_rather_than_not_elevated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second failure path, and the sharpest distinction this module owns.

    A process handle was opened and its *token* refused — the real instance is a protected process,
    where `OpenProcess` succeeds and `OpenProcessToken` answers `ERROR_ACCESS_DENIED`. Nothing was
    learned about elevation, so the answer must be `None`: a broker reading this as "not elevated"
    would refuse a client it never managed to look at, and one reading it as "elevated" would do the
    opposite. `is not False` is asserted on the value itself, because a truthiness check would accept
    the wrong one.
    """

    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (0x4A4A, 0))
    monkeypatch.setattr(
        identity, "_open_token", lambda _advapi32, _handle: (None, identity.ERROR_ACCESS_DENIED)
    )

    observed = identity.probe_process(4242)

    assert observed.elevated is None
    assert observed.elevated is not False
    assert observed.sid is None
    assert observed.integrity is None
    assert observed.is_complete() is False
    assert any(
        line.startswith("OpenProcessToken(pid=4242, TOKEN_QUERY) failed: err=5")
        for line in observed.evidence
    ), f"which call refused has to survive into the evidence: {observed.evidence}"


def test_a_target_token_that_can_be_opened_but_not_read_reports_each_failed_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third path: the token opens and every read on it refuses.

    The three classes are read independently, so all three failures have to appear — a module that
    stopped at the first would leave two facts unexplained, and one that reported a single generic
    "token read failed" would not say *which* observation is missing. This is the same shape the
    existing own-token test pins, which is the point: one helper produces both.
    """

    def opens(_kernel32: object, _pid: int) -> tuple[int, int]:
        return 0x4A4A, 0

    def token(_advapi32: object, _handle: object) -> tuple[int, int]:
        return 0x5B5B, 0

    def unreadable(_advapi32: object, _handle: object, _info_class: int) -> tuple[None, int, int, int]:
        return None, 0, 0, identity.ERROR_ACCESS_DENIED

    monkeypatch.setattr(identity, "_open_process", opens)
    monkeypatch.setattr(identity, "_open_token", token)
    monkeypatch.setattr(identity, "_get_token_information", unreadable)

    observed = identity.probe_process(4242)

    assert observed.sid is None
    assert observed.integrity is None
    assert observed.elevated is None
    assert observed.is_complete() is False
    assert any("GetTokenInformation(TokenUser) failed: err=5" in line for line in observed.evidence)
    assert any(
        "GetTokenInformation(TokenIntegrityLevel) failed: err=5" in line for line in observed.evidence
    )
    assert any("GetTokenInformation(TokenElevation) failed: err=5" in line for line in observed.evidence)


def test_every_handle_the_probe_opens_is_closed_on_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle lifetime, which is otherwise unobservable: both handles are closed, in that order.

    Checked twice through the library seam, because "was this handle closed" cannot be asked of the OS
    afterwards and a leak per probe is the kind of defect that only shows up as a slowly dying broker.
    The order matters as a property, not as a preference: the token is closed before the process it was
    taken from, so no close can end up depending on the other handle by accident.
    """

    from types import SimpleNamespace

    closed: list[object] = []
    process_handle = object()
    token_handle = object()

    monkeypatch.setattr(
        identity,
        "_load_libraries",
        lambda: (SimpleNamespace(), SimpleNamespace(CloseHandle=closed.append)),
    )
    monkeypatch.setattr(identity, "_declare_signatures", lambda *_args: None)
    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (process_handle, 0))
    monkeypatch.setattr(identity, "_open_token", lambda _advapi32, _handle: (token_handle, 0))
    # The two §114 reads that do not go through `_read_token_facts` are stubbed to *succeed* and say
    # nothing: this test is about which handles get closed, and a refusal line would only add noise to
    # the evidence equality asserted below. The peer facts themselves are covered by their own tests.
    monkeypatch.setattr(identity, "_process_creation_time", lambda *_args: (1234567890, 0))
    monkeypatch.setattr(identity, "_read_peer_facts", lambda *_args: (None, None, None, None, []))

    # The read fails: the handles still have to be closed, and the failure still has to be reported.
    monkeypatch.setattr(
        identity,
        "_read_token_facts",
        lambda *_args: (None, None, None, ["injected: the token read failed"]),
    )
    failed = identity.probe_process(4242)

    assert closed == [token_handle, process_handle]
    assert failed.evidence == ("injected: the token read failed",)
    assert failed.is_complete() is False

    # The read succeeds: same two handles, closed the same way, with the facts kept.
    closed.clear()
    monkeypatch.setattr(
        identity,
        "_read_token_facts",
        lambda *_args: (SYNTHETIC_SID, "medium", False, ["injected: the facts"]),
    )
    succeeded = identity.probe_process(4242)

    assert closed == [token_handle, process_handle]
    assert succeeded.is_complete() is True
    assert succeeded.evidence == ("injected: the facts",)


def test_a_readable_target_token_that_answers_no_reads_false_which_is_a_different_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the pair above: `False` is an *answer*, `None` is the absence of one.

    Injected, because the session's own privilege is not something a test may assume — whether this
    host runs elevated decides what a real target says, and an assertion that depended on it would be
    measuring the machine rather than the probe. The companion test pins the `None` side; this one
    pins the value the probe passes through when the token really answered no.
    """

    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (0x4A4A, 0))
    monkeypatch.setattr(identity, "_open_token", lambda _advapi32, _handle: (0x5B5B, 0))
    monkeypatch.setattr(
        identity,
        "_read_token_facts",
        lambda *_args: (SYNTHETIC_SID, "medium", False, ["TokenElevation -> False"]),
    )

    observed = identity.probe_process(4242)

    assert observed.elevated is False
    assert observed.elevated is not None
    assert observed.sid == SYNTHETIC_SID
    assert observed.integrity == "medium"
    assert observed.is_complete() is True


def test_a_pid_that_names_no_process_is_unknown_rather_than_not_elevated() -> None:
    """The failure path a broker will meet in practice: a stale, recycled or invented pid.

    The pid is *measured* by `_free_pid` (see there for how), not guessed, and the status is asserted
    to be the one that says so. The assertion that carries the weight is `elevated is None`: "there is
    no such process" is not "that process is not elevated", and only the first of those is a fact.
    """

    pid = _free_pid()
    observed = identity.probe_process(pid)

    assert observed.pid == pid
    assert observed.sid is None
    assert observed.integrity is None
    assert observed.elevated is None
    assert observed.elevated is not False
    assert observed.is_complete() is False
    assert any(
        line.startswith(f"OpenProcess(pid={pid}, PROCESS_QUERY_LIMITED_INFORMATION) failed: err=87")
        for line in observed.evidence
    ), f"a free pid has to report the status that says so: {observed.evidence}"


def test_the_system_process_is_observed_or_reported_unknown_and_never_guessed() -> None:
    """Every Windows host has one target a non-privileged caller may not read: pid 4.

    Whether it *can* be read depends on the caller's privileges (measured on this host: `OpenProcess`
    succeeds and `OpenProcessToken` answers `ERROR_ACCESS_DENIED`), so the assertion is the contract,
    not the host's privilege level — either the whole observation succeeded, or every field is `None`
    and the evidence names what refused. A partial identity, or a `False` nobody read, is what a
    caller must never receive, on any host.
    """

    observed = identity.probe_process(4)

    assert observed.pid == 4
    assert observed.evidence, "an answer with no observations would say nothing about the machine"
    if observed.is_complete():
        assert any(line.startswith("TokenElevation ->") for line in observed.evidence)
    else:
        assert observed.sid is None
        assert observed.integrity is None
        assert observed.elevated is None
        assert any("failed: err=" in line for line in observed.evidence)


def test_process_is_complete_needs_all_three_observable_facts() -> None:
    """Three facts here, unlike `ClientIdentity` where elevation is not one of the schema's fields.

    The difference is deliberate rather than an inconsistency: a client block is complete when the two
    fields `broker-request` requires are present, while this type has no consumer yet, so its
    completeness is the whole observation. Stated as its own test so that "three" cannot quietly
    become "two" when a caller arrives.
    """

    assert identity.ProcessIdentity(
        pid=1, sid=SYNTHETIC_SID, integrity="medium", elevated=False
    ).is_complete() is True
    assert identity.ProcessIdentity(
        pid=1, sid=SYNTHETIC_SID, integrity="medium", elevated=True
    ).is_complete() is True

    for missing in (
        identity.ProcessIdentity(pid=1, sid=None, integrity="medium", elevated=False),
        identity.ProcessIdentity(pid=1, sid=SYNTHETIC_SID, integrity=None, elevated=False),
        identity.ProcessIdentity(pid=1, sid=SYNTHETIC_SID, integrity="medium", elevated=None),
    ):
        assert missing.is_complete() is False

    # `pid` is the caller's question, always present, and evidence is optional: a synthetic identity
    # is as constructible as a real one.
    assert identity.ProcessIdentity(
        pid=99, sid=None, integrity=None, elevated=None
    ).evidence == ()


# --- draft §114: the facts the broker needs about a caller ----------------------------------------


def _sid_bytes(*sub_authorities: int, authority: int = 5) -> bytes:
    """A real-shaped SID. `ConvertSidToStringSidW` converts these for real, so the injected paths
    exercise the module's own conversion instead of a stub of it.

    ``authority`` is the identifier authority (5 = NT, 15 = AppContainer, 16 = mandatory label), so
    the SIDs this builds are the ones the classes really carry rather than merely SID-shaped bytes.
    """

    raw = bytes([1, len(sub_authorities)]) + authority.to_bytes(6, "big")
    for value in sub_authorities:
        raw += value.to_bytes(4, "little")
    return raw


#: `S-1-5-21-1000-1000-1000-1001`, the synthetic user identity as its own bytes.
USER_SID = _sid_bytes(21, 1000, 1000, 1000, 1001)
#: `S-1-16-8192` — `SECURITY_MANDATORY_MEDIUM_RID_BASE`, i.e. the integrity word `medium`.
MEDIUM_INTEGRITY_SID = _sid_bytes(0x2000, authority=16)
#: `S-1-15-4660` — the application identity an injected AppContainer token carries.
CONTAINER_SID = "S-1-15-4660"
#: The AppContainer SID itself, in the same bytes the token class would carry it in.
CONTAINER_SID_BYTES = _sid_bytes(0x1234, authority=15)


def _record(sid: bytes) -> tuple[bytes, int, int]:
    """A `SID_AND_ATTRIBUTES` buffer the way the OS fills it: a pointer, then the SID it aims at."""

    raw = bytearray(16 + len(sid))
    raw[0:8] = (16).to_bytes(8, "little")
    raw[16:] = sid
    return bytes(raw), 0, len(raw)


def _dword(value: int) -> tuple[bytes, int, int]:
    """A class that answers with one DWORD: `TokenElevation`, `TokenSessionId`, the two BOOLs."""

    return value.to_bytes(4, "little"), 0, 4


def _answers(
    overrides: dict[int, tuple[bytes, int, int] | None] | None = None,
) -> dict[int, tuple[bytes, int, int] | None]:
    """Every class a healthy token answers, so a test overrides only the read it is about.

    A value of ``None`` means "this class refuses" — the reader built below turns it into the same
    ``(None, 0, 0, status)`` the real helper returns on failure.
    """

    base: dict[int, tuple[bytes, int, int] | None] = {
        identity.TOKEN_USER: _record(USER_SID),
        identity.TOKEN_INTEGRITY_LEVEL: _record(MEDIUM_INTEGRITY_SID),
        identity.TOKEN_ELEVATION: _dword(0),
        identity.TOKEN_ELEVATION_TYPE: _dword(3),
        identity.TOKEN_SESSION_ID: _dword(7),
        identity.TOKEN_IS_APP_CONTAINER: _dword(0),
    }
    base.update(overrides or {})
    return base


def _reader(
    answers: dict[int, tuple[bytes, int, int] | None],
    asked: list[int],
    status: int = identity.ERROR_ACCESS_DENIED,
):
    """A `_get_token_information` that answers per class, and records what it was asked for."""

    def read(
        _advapi32: object, _handle: object, info_class: int
    ) -> tuple[bytes | None, int, int, int]:
        asked.append(info_class)
        answer = answers.get(info_class)
        if answer is None:
            return None, 0, 0, status
        raw, base, size = answer
        return raw, base, size, 0

    return read


def _inject_target(
    monkeypatch: pytest.MonkeyPatch,
    answers: dict[int, tuple[bytes, int, int] | None],
    *,
    api_session: tuple[int | None, int] = (7, 0),
    creation: tuple[int | None, int] = (1234567890, 0),
    asked: list[int] | None = None,
) -> list[int]:
    """Point `probe_process` at a fake target while leaving the real libraries in place.

    The handles are made-up integers and the pid names no process, so nothing here touches the host;
    the libraries are still the real `advapi32`/`kernel32`, which keeps the conversions and the
    `ctypes` struct layouts under test. Closing a handle the OS never issued fails harmlessly.

    Every seam the probe has is replaced: the process, the token, the per-class read, the kernel's
    session id and the creation time. A test overrides exactly the read it is about and the rest of
    the observation stays successful, which is what makes "one failed read leaves the others alone"
    an assertion rather than a hope.
    """

    if asked is None:
        asked = []
    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (0x4A4A, 0))
    monkeypatch.setattr(identity, "_open_token", lambda _advapi32, _handle: (0x5B5B, 0))
    monkeypatch.setattr(identity, "_get_token_information", _reader(answers, asked))
    monkeypatch.setattr(identity, "_process_session_id", lambda _kernel32, _pid: api_session)
    monkeypatch.setattr(identity, "_process_creation_time", lambda _kernel32, _handle: creation)
    return asked


def _api_session_id(pid: int) -> int | None:
    """`ProcessIdToSessionId` called directly in the test, so the probe meets an outside answer.

    Calling the module's own wrapper would compare one implementation with itself.
    """

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.ProcessIdToSessionId.restype = ctypes.c_int
    session = ctypes.c_ulong()
    if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
        return None
    return session.value


def test_the_new_fields_are_defaulted_after_the_evidence_field() -> None:
    """The four original positional fields still construct, and the new facts are all unknown."""

    observed = identity.ProcessIdentity(4242, SYNTHETIC_SID, "medium", False)

    assert (
        observed.elevation_type,
        observed.session_id,
        observed.is_app_container,
        observed.app_container_sid,
        observed.creation_time,
    ) == (None, None, None, None, None)
    assert observed.is_complete() is True


def test_process_is_complete_is_still_only_the_three_shared_facts() -> None:
    """§114 widened the observation, not the meaning of completeness.

    A rich identity that is missing one of the three shared facts is still incomplete, and a complete
    one whose new fields are all unknown is still complete — so a caller that learned to ask
    `is_complete()` does not silently start getting a different answer (brief item 8).
    """

    assert identity.ProcessIdentity(
        pid=1,
        sid=None,
        integrity="medium",
        elevated=False,
        elevation_type="full",
        session_id=1,
        is_app_container=True,
        app_container_sid="S-1-15-2",
        creation_time=1,
    ).is_complete() is False
    assert identity.ProcessIdentity(
        pid=1, sid=SYNTHETIC_SID, integrity="medium", elevated=False
    ).is_complete() is True


def test_this_process_as_a_target_answers_the_extra_facts() -> None:
    """Every new field, on the one target whose answer can be known in advance."""

    observed = identity.probe_process(os.getpid())

    assert observed.is_complete(), f"this process must be readable: {observed.evidence}"
    assert observed.session_id is not None, f"the session was not read: {observed.evidence}"
    assert observed.session_id == _api_session_id(os.getpid()), (
        "the token's session has to be the session the kernel reports for this pid, or the "
        "cross-check accepted a disagreement"
    )
    assert observed.creation_time is not None, f"the creation time was not read: {observed.evidence}"
    assert observed.creation_time > 0
    assert observed.is_app_container is not None, (
        f"a boolean flag has to be readable for a process this one opened: {observed.evidence}"
    )
    if observed.is_app_container:
        assert observed.app_container_sid is not None
    else:
        assert observed.app_container_sid is None
        assert NOT_AN_APP_CONTAINER in observed.evidence


def test_the_real_elevation_type_is_readable_and_consistent_with_the_flag() -> None:
    """The weaker, true implication — on the host that falsified the stronger one.

    "`elevated` is true exactly when the type is `full`" is **false**: `TokenElevationTypeDefault`
    means "not a split token", which is a standard user *or* an administrator with UAC disabled. This
    machine is the second case (`default` with `elevated=True` and `high` integrity), so a rule that
    treated `default` as a contradiction would withdraw a legitimate type here. This test is what
    catches that, by requiring the type to be *readable* — a bare "unknown is allowed" assertion would
    have accepted the wrong rule.
    """

    observed = identity.probe_process(os.getpid())
    if observed.elevated is None:
        pytest.skip("the token's elevation could not be read on this machine")

    if observed.elevation_type is None:
        refused = [
            line
            for line in observed.evidence
            if line.startswith("GetTokenInformation(TokenElevationType) failed")
        ]
        assert refused, (
            "the elevation type may only be unknown when the class itself was refused: `default` "
            "pairs legitimately with either elevation flag, so withdrawing it would be applying a "
            f"rule Windows does not have: {observed.evidence}"
        )
        pytest.skip(f"the elevation type class was refused on this machine: {refused[0]}")

    assert observed.elevation_type in {"default", "full", "limited"}
    if observed.elevation_type == "full":
        assert observed.elevated is True, "a `full` token is an elevated one"
    elif observed.elevation_type == "limited":
        assert observed.elevated is False, "a `limited` token is a filtered one"
    else:
        assert not any("contradicts" in line for line in observed.evidence), (
            "`default` constrains `TokenElevation` in neither direction, so it can never be a "
            f"contradiction: {observed.evidence}"
        )


def test_a_real_child_is_observed_with_the_extra_facts_too() -> None:
    """The broker's real case is another process; the new fields have to survive that trip."""

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        observed = identity.probe_process(child.pid)
        own = identity.probe_identity()

        assert observed.is_complete(), f"the child's token was not readable: {observed.evidence}"
        assert observed.sid == own.sid, "a spawned child runs as the same user"
        assert observed.integrity == own.integrity
        assert observed.elevated == own.elevated
        assert observed.session_id is not None
        assert observed.session_id == _api_session_id(child.pid)
        assert observed.creation_time is not None and observed.creation_time > 0
        assert observed.is_app_container is not None
    finally:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - the child only sleeps
            child.kill()
            child.wait(timeout=15)


@pytest.mark.parametrize(
    "value,word,elevated", [(1, "default", 0), (1, "default", 1), (2, "full", 1), (3, "limited", 0)]
)
def test_every_elevation_type_word_is_reachable_and_matches_the_flag(
    monkeypatch: pytest.MonkeyPatch, value: int, word: str, elevated: int
) -> None:
    """Every compatible pair: all three words, and `default` with *both* elevation flags.

    "Unknown for everything" would pass the unrecognised-value test below, so the mapped direction is
    asserted here — each word in the ABI table has to be reachable through a real read. The two
    `default` rows are the ones the wrong cross-check would have failed: `default` is "not a split
    token", which a standard user and a UAC-disabled administrator both have.
    """

    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_ELEVATION_TYPE: _dword(value),
                identity.TOKEN_ELEVATION: _dword(elevated),
            }
        ),
    )

    observed = identity.probe_process(4242)

    assert observed.elevation_type == word
    assert observed.elevated is bool(elevated)
    assert f"TokenElevationType -> {word}" in observed.evidence
    assert not any("contradicts TokenElevation" in line for line in observed.evidence), (
        f"this pair is legitimate, not a contradiction: {observed.evidence}"
    )


def test_a_uac_disabled_administrator_pair_is_not_a_contradiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`default` + ``elevated=True``, stated on its own because it is the pair that broke a rule.

    The rule "`TokenElevation` is true exactly when the type is `full`" declares this pair a
    contradiction and withdraws the type — and this machine *is* this pair. It is a legitimate token:
    UAC disabled means the administrator's token is not split, so it is `default` and elevated at
    once. Kept as its own test so a future rewrite of the cross-check meets it by name.
    """

    _inject_target(
        monkeypatch,
        _answers({identity.TOKEN_ELEVATION_TYPE: _dword(1), identity.TOKEN_ELEVATION: _dword(1)}),
    )

    observed = identity.probe_process(4242)

    assert observed.elevation_type == "default", (
        "a UAC-disabled administrator's token is `default`, and that is readable — not a "
        f"contradiction to withdraw: {observed.evidence}"
    )
    assert observed.elevated is True
    assert not any("contradicts" in line for line in observed.evidence)


def test_a_default_type_with_an_unelevated_flag_is_accepted_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other legitimate `default`: a standard user. `default` constrains neither direction."""

    _inject_target(
        monkeypatch,
        _answers({identity.TOKEN_ELEVATION_TYPE: _dword(1), identity.TOKEN_ELEVATION: _dword(0)}),
    )

    observed = identity.probe_process(4242)

    assert observed.elevation_type == "default"
    assert observed.elevated is False
    assert not any("contradicts" in line for line in observed.evidence)


def test_an_elevation_type_with_no_word_is_unknown_rather_than_rounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A type outside the three `winnt.h` names is not rounded to its nearest neighbour.

    The tempting answer (`default` is 1, and 9 is "near" nothing in particular) would be a word this
    module has no basis for: the token answered a question whose vocabulary does not contain the
    answer. The read that *did* answer — `TokenElevation` — must not be discarded with it.
    """

    _inject_target(
        monkeypatch,
        _answers({identity.TOKEN_ELEVATION_TYPE: _dword(9), identity.TOKEN_ELEVATION: _dword(1)}),
    )

    observed = identity.probe_process(4242)

    assert observed.elevation_type is None
    assert any("TokenElevationType returned 9" in line for line in observed.evidence), (
        f"the unrecognised value itself has to survive into the evidence: {observed.evidence}"
    )
    assert observed.elevated is True


@pytest.mark.parametrize("type_value,word,elevated", [(2, "full", 0), (3, "limited", 1)])
def test_a_split_type_that_contradicts_the_flag_is_withdrawn(
    monkeypatch: pytest.MonkeyPatch, type_value: int, word: str, elevated: int
) -> None:
    """The two real contradictions: `full` is elevated, `limited` is filtered, and nothing else is.

    A `full` token reading ``elevated=False``, or a `limited` one reading ``True``, means the *word* is
    unusable, so it is withdrawn (`None`) while the flag the token actually answered is kept — and the
    evidence names both readings so a reader can see which two facts collided. Only these two shapes:
    see `_ELEVATION_REQUIRES` for why `default` must not be added to them.
    """

    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_ELEVATION_TYPE: _dword(type_value),
                identity.TOKEN_ELEVATION: _dword(elevated),
            }
        ),
    )

    observed = identity.probe_process(4242)

    assert observed.elevation_type is None
    assert observed.elevated is bool(elevated), "the flag is kept exactly as the token answered"
    line = next((line for line in observed.evidence if "contradicts TokenElevation" in line), None)
    assert line is not None, f"the disagreement has to be reported: {observed.evidence}"
    assert word in line
    assert f"TokenElevation -> {bool(elevated)}" in line


def test_a_session_the_two_reads_agree_on_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject_target(monkeypatch, _answers({identity.TOKEN_SESSION_ID: _dword(4)}), api_session=(4, 0))

    observed = identity.probe_process(4242)

    assert observed.session_id == 4
    assert any("the two independent reads agree" in line for line in observed.evidence)
    assert not any("disagree" in line for line in observed.evidence)


def test_a_session_disagreement_is_unknown_and_names_both_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two independent reads answering differently means *nobody* knows the session.

    Either number alone would be a coin flip presented as a fact, so both are named and the fact is
    withdrawn. The rest of the observation is untouched, which is asserted too: one disagreement is
    one unknown, not a failed probe.
    """

    _inject_target(
        monkeypatch, _answers({identity.TOKEN_SESSION_ID: _dword(3)}), api_session=(9, 0)
    )

    observed = identity.probe_process(4242)

    assert observed.session_id is None
    line = next((line for line in observed.evidence if "disagree" in line), None)
    assert line is not None, f"the disagreement has to be reported: {observed.evidence}"
    assert "3" in line and "9" in line, "both numbers have to be in the line, or nobody can check it"
    assert observed.is_complete() is True


def test_a_missing_process_id_to_session_id_keeps_the_tokens_answer_and_names_the_missing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject_target(
        monkeypatch,
        _answers({identity.TOKEN_SESSION_ID: _dword(5)}),
        api_session=(None, identity.ERROR_ACCESS_DENIED),
    )

    observed = identity.probe_process(4242)

    assert observed.session_id == 5
    assert any(
        line.startswith("ProcessIdToSessionId(pid=4242) failed: err=5")
        for line in observed.evidence
    ), f"the call and its status have to be named: {observed.evidence}"
    assert any("ProcessIdToSessionId was unavailable" in line for line in observed.evidence)


def test_a_missing_token_session_keeps_the_kernels_answer_and_names_the_missing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction: the same rule, so neither source is privileged over the other."""

    _inject_target(monkeypatch, _answers({identity.TOKEN_SESSION_ID: None}), api_session=(6, 0))

    observed = identity.probe_process(4242)

    assert observed.session_id == 6
    assert any(
        line.startswith("GetTokenInformation(TokenSessionId) failed: err=5")
        for line in observed.evidence
    )
    assert any("TokenSessionId was unavailable" in line for line in observed.evidence)


def test_a_session_no_source_can_answer_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject_target(
        monkeypatch,
        _answers({identity.TOKEN_SESSION_ID: None}),
        api_session=(None, identity.ERROR_ACCESS_DENIED),
    )

    observed = identity.probe_process(4242)

    assert observed.session_id is None
    assert any("could not be read from either source" in line for line in observed.evidence)


def test_the_non_app_container_answer_is_recorded_and_the_sid_class_is_never_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boolean `False` is an answer, and the evidence has to say so in words.

    It must also not turn into a failed read: an ordinary token has no application identity, so
    asking for `TokenAppContainerSid` would report a refusal for a fact that was never there. The
    reader records every class it was asked for, which is what makes that an assertion.
    """

    asked = _inject_target(monkeypatch, _answers())

    observed = identity.probe_process(4242)

    assert observed.is_app_container is False
    assert observed.app_container_sid is None
    assert NOT_AN_APP_CONTAINER in observed.evidence, (
        "`None` here is an answer, not a failure to look, and the reader has to be able to tell: "
        f"{observed.evidence}"
    )
    assert identity.TOKEN_APP_CONTAINER_SID not in asked, (
        "an ordinary token has no application identity to read"
    )


def test_an_ordinary_process_reports_no_application_identity_as_an_answer() -> None:
    """The same claim against a real token, since the injected one is the module's own story."""

    observed = identity.probe_process(os.getpid())
    if observed.is_app_container is None:
        pytest.skip(f"the AppContainer flag was not readable: {observed.evidence}")
    if observed.is_app_container:
        assert observed.app_container_sid is not None, (
            "a token that says it is an AppContainer carries an application identity, so a missing "
            "SID would be a failed read of a token that has one"
        )
        return

    assert observed.app_container_sid is None
    assert NOT_AN_APP_CONTAINER in observed.evidence


def test_an_app_container_token_yields_its_application_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the `False`: `True` means the identity is there and must be produced."""

    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_IS_APP_CONTAINER: _dword(1),
                identity.TOKEN_APP_CONTAINER_SID: _record(CONTAINER_SID_BYTES),
            }
        ),
    )

    observed = identity.probe_process(4242)

    assert observed.is_app_container is True
    assert observed.app_container_sid == CONTAINER_SID
    assert (
        f"TokenIsAppContainer -> True; TokenAppContainerSid -> {CONTAINER_SID}"
        in observed.evidence
    )


def test_an_app_container_whose_sid_will_not_convert_is_unknown_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`True` with no SID is a *failed* read, and must not read as "no application identity"."""

    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_IS_APP_CONTAINER: _dword(1),
                identity.TOKEN_APP_CONTAINER_SID: _record(CONTAINER_SID_BYTES),
            }
        ),
    )
    monkeypatch.setattr(identity, "_sid_to_string", lambda *_args: None)

    observed = identity.probe_process(4242)

    assert observed.is_app_container is True
    assert observed.app_container_sid is None
    assert any(
        "AppContainer SID could not be converted to S-1-... form" in line
        for line in observed.evidence
    )
    assert NOT_AN_APP_CONTAINER not in observed.evidence


def test_an_app_container_whose_sid_class_refuses_names_that_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_IS_APP_CONTAINER: _dword(1),
                identity.TOKEN_APP_CONTAINER_SID: None,
            }
        ),
    )

    observed = identity.probe_process(4242)

    assert observed.is_app_container is True
    assert observed.app_container_sid is None
    assert any(
        line.startswith("GetTokenInformation(TokenAppContainerSid) failed: err=5")
        for line in observed.evidence
    )


def test_an_app_container_sid_the_buffer_cannot_place_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record whose pointer cannot be placed inside the buffer is not read out of bounds."""

    _inject_target(
        monkeypatch,
        _answers(
            {
                identity.TOKEN_IS_APP_CONTAINER: _dword(1),
                identity.TOKEN_APP_CONTAINER_SID: (bytes(44), 0, 44),
            }
        ),
    )

    observed = identity.probe_process(4242)

    assert observed.is_app_container is True
    assert observed.app_container_sid is None
    assert any(
        "AppContainer SID could not be extracted from the buffer" in line
        for line in observed.evidence
    )


def test_an_app_container_flag_that_is_neither_zero_nor_one_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BOOL is 1 or 0; anything else leaves the question unanswerable rather than `False`."""

    _inject_target(monkeypatch, _answers({identity.TOKEN_IS_APP_CONTAINER: _dword(2)}))

    observed = identity.probe_process(4242)

    assert observed.is_app_container is None
    assert observed.is_app_container is not False
    assert observed.app_container_sid is None
    assert any(
        "TokenIsAppContainer returned 2" in line and "neither" in line
        for line in observed.evidence
    ), f"the value has to be named, with both legal ones: {observed.evidence}"


def test_a_creation_time_that_cannot_be_read_is_unknown_with_the_call_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject_target(monkeypatch, _answers(), creation=(None, identity.ERROR_ACCESS_DENIED))

    observed = identity.probe_process(4242)

    assert observed.creation_time is None
    assert any(
        line.startswith("GetProcessTimes(pid=4242) failed: err=5") for line in observed.evidence
    ), f"the call and its status have to be named: {observed.evidence}"
    assert observed.is_complete() is True, (
        "the process-times read is independent of the token reads: one failure must not erase the "
        "other observation"
    )


def test_the_creation_time_is_the_raw_64_bit_filetime_with_the_high_half_kept() -> None:
    """It is compared, never printed, so it stays the integer the API produced.

    The high half is asserted explicitly: a conversion to a datetime — or a 32-bit truncation — would
    pass a `> 0` check on the injected value above while losing the very bits that make two FILETIMEs
    distinguishable.
    """

    from types import SimpleNamespace

    creation = 0x01D0000000000001

    def fills(
        _handle: object, creation_out: object, _exit: object, _kernel: object, _user: object
    ) -> int:
        # The FILETIME out-parameter is two adjacent DWORDs, low half first.
        first = ctypes.cast(creation_out, ctypes.POINTER(ctypes.c_uint32))
        first[0] = creation & 0xFFFFFFFF
        first[1] = creation >> 32
        return 1

    value, status = identity._process_creation_time(SimpleNamespace(GetProcessTimes=fills), 0x4A4A)
    assert (value, status) == (creation, 0)
    assert value is not None and value > 0xFFFFFFFF, "the high half cannot be dropped"

    def refuses(*_args: object) -> int:
        return 0

    missing, _status = identity._process_creation_time(SimpleNamespace(GetProcessTimes=refuses), 1)
    assert missing is None


def test_a_target_whose_token_refuses_still_yields_a_creation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The creation-time read comes off the *process* handle, so a token refusal cannot erase it.

    That matters most in exactly the case where it is needed: a protected target whose token will not
    open still has a creation time, and that is what keeps a reused pid from being mistaken for the
    process that was observed.
    """

    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (0x4A4A, 0))
    monkeypatch.setattr(
        identity, "_open_token", lambda _advapi32, _handle: (None, identity.ERROR_ACCESS_DENIED)
    )
    monkeypatch.setattr(
        identity, "_process_creation_time", lambda *_args: (0x01D0000000000001, 0)
    )

    observed = identity.probe_process(4242)

    assert observed.creation_time == 0x01D0000000000001
    assert observed.elevated is None
    assert any(
        line.startswith("OpenProcessToken(pid=4242, TOKEN_QUERY) failed: err=5")
        for line in observed.evidence
    )


def test_a_peer_read_that_raises_becomes_evidence_rather_than_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The widened probe keeps the module's oldest promise: it never raises.

    The new reads are more code on the same promise, so one of them is made to explode and the probe
    still has to come back with an object and a line saying so.
    """

    def explodes(*_args: object) -> list[str]:
        raise RuntimeError("injected peer-read failure")

    monkeypatch.setattr(identity, "_open_process", lambda _kernel32, _pid: (0x4A4A, 0))
    monkeypatch.setattr(identity, "_open_token", lambda _advapi32, _handle: (0x5B5B, 0))
    monkeypatch.setattr(identity, "_process_creation_time", lambda *_args: (1, 0))
    monkeypatch.setattr(identity, "_read_peer_facts", explodes)

    observed = identity.probe_process(4242)

    assert isinstance(observed, identity.ProcessIdentity)
    assert observed.elevation_type is None
    assert observed.session_id is None
    assert observed.is_app_container is None
    assert observed.app_container_sid is None
    assert any(
        "process identity probe failed: RuntimeError" in line for line in observed.evidence
    ), f"an exception has to become data: {observed.evidence}"


def test_a_client_block_that_would_carry_a_null_is_one_the_published_schema_rejects() -> None:
    """Why `to_document` refuses, measured rather than recalled.

    `broker-request` requires and types all four `client` fields, so `{"sid": None, ...}` fails it —
    and it fails it from behind `broker/protocol.py`'s caller-facing validation, which reports
    *caller* input as `INVALID_INPUT`. The caller supplied nothing wrong: the probe could not read
    the fact.
    """

    broken = {"sid": None, "pid": 4242, "integrity": "medium", "application_id": APPLICATION_ID}
    request = {
        "protocol_version": 1,
        "request_id": "req-identity-probe",
        "operation": "probe_root",
        "client": broken,
    }
    assert schema_io.errors_for("broker-request", request) != []


@pytest.mark.parametrize(
    "field,observed,application_id",
    [
        (
            "sid",
            identity.ClientIdentity(
                sid=None,
                integrity="medium",
                elevated=False,
                pid=1,
                evidence=("GetTokenInformation(TokenUser) failed: err=5",),
            ),
            APPLICATION_ID,
        ),
        (
            "integrity",
            identity.ClientIdentity(
                sid=SYNTHETIC_SID,
                integrity=None,
                elevated=False,
                pid=1,
                evidence=("GetTokenInformation(TokenIntegrityLevel) failed: err=5",),
            ),
            APPLICATION_ID,
        ),
        (
            "application_id",
            identity.ClientIdentity(sid=SYNTHETIC_SID, integrity="medium", elevated=False, pid=1),
            None,
        ),
    ],
)
def test_to_document_refuses_a_null_instead_of_returning_a_document_the_schema_rejects(
    field: str, observed: identity.ClientIdentity, application_id: str | None
) -> None:
    """Each required fact, unreadable: the refusal names the schema field and does not return.

    `pytest.raises` is the "and NOT returning a document" half: a function that raised *and* returned
    is not a thing, while a function that returned the broken document is exactly the defect.
    """

    with pytest.raises(AirootError) as raised:
        observed.to_document(application_id)  # type: ignore[arg-type]

    error = raised.value
    assert error.reason_code == "SELF_VALIDATION_FAILED"
    assert error.exit_code == 8, "this is the implementation's own defect, not the caller's input"
    assert f"client.{field}" in error.message, "the refusal has to name the exact schema field"
    assert any(f"client.{field} would be null" in line for line in error.evidence)
    if observed.evidence:
        assert observed.evidence[0] in error.evidence, (
            "the probe's own reason for not knowing has to travel with the refusal"
        )


def test_a_complete_identity_still_builds_its_client_block() -> None:
    """The refusal must not swallow the case it exists for."""

    document = complete().to_document(APPLICATION_ID)

    assert document == {
        "sid": SYNTHETIC_SID,
        "pid": 4242,
        "integrity": "medium",
        "application_id": APPLICATION_ID,
    }
    assert schema_io.errors_for(
        "broker-request",
        {
            "protocol_version": 1,
            "request_id": "req-identity-probe",
            "operation": "probe_root",
            "client": document,
        },
    ) == []
