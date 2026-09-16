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
"""

from __future__ import annotations

import os

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
