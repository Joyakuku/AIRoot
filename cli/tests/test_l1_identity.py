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
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from airoot import schema_io
from airoot.caps import identity

#: A plausible-looking application id: the schema's `id` pattern needs a lowercase first character.
APPLICATION_ID = "airoot-cli"

#: A SID that satisfies the schema's pattern but belongs to no real token — for the "complete
#: identity" case, where the point is the *shape*, not the value.
SYNTHETIC_SID = "S-1-5-21-1000-1000-1000-1001"


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
