"""Read-only observation of *this process's* Windows identity.

Why this exists: the P2 broker is a separate, elevated process, and
``cli/schema/broker-request.schema.json`` makes the caller prove who it is before the broker will
act — the ``client`` block is required and carries exactly ``sid``, ``pid``, ``integrity`` and
``application_id``. This module is the half that reads the first three off the current process. It
needs no privilege: a process may always open *its own* token, so developing and testing this is
unprivileged work even though the feature it feeds is not.

Three rules, each copied from a sibling module that learned it the hard way:

* **read-only, ``ctypes`` only, no new dependency.** Same shape as ``caps/usn.py`` and ``caps/acl.py``:
  ``advapi32`` token APIs, no ``pywin32``, ``jsonschema`` stays the only third-party runtime
  dependency. Nothing here opens a process other than the current one and nothing here writes.
* **failure is data.** ``OpenProcessToken``/``GetTokenInformation`` can fail (a restricted token, a
  protected process, a future Windows build that adds a token class), and an identity probe that
  raises tells the caller nothing except "go ask again somewhere else". Every failure becomes ``None``
  plus an ``evidence`` line naming the call and the status, so the caller can say *why* it does not
  know — "unknown" is a legitimate answer, silence is not.
* **no field is guessed.** ``elevated`` comes from ``TokenElevation`` on the same token, not from
  ``shell32.IsUserAnAdmin``. ``usn.py`` uses that shortcut for a diagnostic note and it is the wrong
  tool here: it reads group membership through a convenience wrapper whose semantics have changed
  across Windows versions, while ``TokenElevation`` asks the token itself the question the broker
  cares about. Under UAC, a filtered administrator token must read ``elevated=False`` — the caller is
  *not* privileged, and a broker that accepts it as though it were would be trusting a guess.

**What this module deliberately does not do.** It does not decide whether the identity is *good
enough* for the broker: :meth:`ClientIdentity.is_complete` only says whether the schema's four fields
can be filled at all, and the policy question (which ``application_id`` values may ask for which
operations) belongs to the broker, not to the process introducing itself.

**The other half: reading a *different* process's token (draft §113).** Everything above answers "who
am I", which is the one question a process can answer about itself without any privilege — and,
precisely because it is self-reported, it proves nothing about a *caller*. The protected broker's job
(docs/broker §3:52) is to check the client process's token, user SID, integrity level and application
identity, and that needs the client's token: :func:`probe_process` opens the target with
``PROCESS_QUERY_LIMITED_INFORMATION`` — the least right that still reads a token — then opens *that*
process's token with ``TOKEN_QUERY`` and reads the same three facts through the same helpers
:func:`probe_identity` uses. One implementation of "what does this token say", so the two paths cannot
drift apart about the machine they share.

The two functions answer different questions, and that difference is the reason there are two. For this
process, "unreadable" is an oddity; for another process it is the *ordinary* answer — the pid names
nothing, the process exited mid-probe, it is protected, it belongs to another user, or it runs at a
higher integrity level. Each of those becomes ``None`` plus an evidence line, never the negative fact:
``elevated=False`` means the target's token answered the question, while ``elevated=None`` means nobody
managed to ask. A broker that blurred those two would refuse a legitimate client — or, worse, read
"could not look" as "looked, and it was fine".

**What the server needs that the caller must not self-report (draft §114).** docs/broker §3:52 requires
the broker to check a client's token, user SID, integrity level *and* application identity, and the
`client` block cannot grow to carry the last of those (its four fields are frozen). So the peer
observation reads four more facts off the *target's* token and record: the elevation **type** (which
says what `elevated` alone cannot — a filtered administrator and a standard user both read
``False``), the session id, whether the token is an AppContainer and, only if it is, that container's
application SID. Each is cross-checked or qualified rather than passed through, and each still fails as
data: :func:`_read_peer_facts` is where those rules live, in one place, so the broker's eventual policy
layer reads one implementation of "what is this caller" rather than several.

**Observation is not authorisation.** For either function: the fact that a caller is elevated, or
shares the user, or carries any particular SID says nothing about whether it is *entitled* to the
operation it asks for. Entitlement is policy — which caller may ask for which operation, under which
plan and which approval — and no such policy exists in this build (ADR-0025's D1 leaves the whole
protected broker to P2). Nothing here is a permission decision: :meth:`ProcessIdentity.is_complete`
reports that the observation succeeded, never that the observed process is welcome.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from ..exits import AirootError

__all__ = ["ClientIdentity", "ProcessIdentity", "probe_identity", "probe_process"]

#: `TOKEN_QUERY` — enough to read the token, not enough to do anything with it.
TOKEN_QUERY = 0x0008

#: `PROCESS_QUERY_LIMITED_INFORMATION` — the least process right that still reads a token, and the
#: only one `probe_process` asks for. `PROCESS_QUERY_INFORMATION` would also work and is wrong here:
#: the limited right is granted where the full one is not, and a read-only observation that asks for
#: more than it needs is how a probe turns into a privilege requirement.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: `TOKEN_INFORMATION_CLASS` members this module reads. The numbers are the ABI: they come from
#: `winnt.h` and are what the OS dispatches on, so they are pinned in a test rather than trusted.
TOKEN_USER = 1
TOKEN_ELEVATION = 20
TOKEN_INTEGRITY_LEVEL = 25
#: Added with the *peer* observation (draft §114). Each is a fact the server can read about a caller
#: and the caller cannot put into its own request: `broker-request`'s `client` block has four fields
#: and `additionalProperties: false`, so widening the observation is the only side of this pair that
#: can grow. `TokenElevationType` exists because `TokenElevation` alone cannot tell an administrator
#: with UAC off (`default`) from a filtered administrator (`limited`) — both read `elevated=False`,
#: which is the truth about the *token* and an incomplete truth about the caller.
TOKEN_SESSION_ID = 12
TOKEN_ELEVATION_TYPE = 18
TOKEN_IS_APP_CONTAINER = 29
TOKEN_APP_CONTAINER_SID = 31

#: `TOKEN_ELEVATION_TYPE` values, as `winnt.h` names them.
_ELEVATION_TYPES: dict[int, str] = {1: "default", 2: "full", 3: "limited"}

#: What each elevation type says about `TokenElevation`. **The weaker, true implication**, and the one
#: the cross-check in :func:`_read_peer_facts` is held to.
#:
#: The tempting one-liner — "`elevated` is true exactly when the type is `full`" — is **false**, and
#: this machine falsifies it: `TokenElevationTypeDefault` means "not an elevated-or-limited (split)
#: token", which covers a standard user (``TokenElevation`` FALSE) *and* an administrator with UAC
#: disabled (``TokenElevation`` TRUE, `high` integrity). So `default` constrains nothing; only the two
#: split types do — `full` is an elevated token, `limited` is a filtered one — and a reading that
#: contradicts either is unusable.
_ELEVATION_REQUIRES: dict[str, bool] = {"full": True, "limited": False}

#: `TokenIsAppContainer` is a BOOL; these are the two values it is allowed to take.
_APP_CONTAINER_TRUE = 1
_APP_CONTAINER_FALSE = 0

#: `ERROR_INSUFFICIENT_BUFFER` — the documented "ask again with more room" answer.
ERROR_INSUFFICIENT_BUFFER = 122

#: `ERROR_ACCESS_DENIED`. Exported because it is the status a restricted process gets, and the tests
#: need a real, nameable Windows status to simulate a refusal with — a made-up number would test the
#: formatting rather than the path a machine actually takes.
ERROR_ACCESS_DENIED = 5

#: `ERROR_INVALID_PARAMETER` — what `OpenProcess` answers for a pid that names no process at all.
#: Exported for the same reason as the denial above, and the pair must not be conflated: a pid that
#: exists but is protected answers `ERROR_ACCESS_DENIED`, so only this status is evidence that the pid
#: is free. Two different facts, two different numbers, and a test that must tell them apart.
ERROR_INVALID_PARAMETER = 87

#: `SECURITY_MANDATORY_*_RID_BASE` from `winnt.h`, and the four words the published schema allows.
#: Windows compares integrity levels with `>=` against these bases, so the mapping is a threshold
#: ladder rather than an equality table.
_INTEGRITY_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (0x4000, "system"),
    (0x3000, "high"),
    (0x2000, "medium"),
    (0x1000, "low"),
)

#: The absolute maximum a token information buffer can need, so a retry cannot loop forever.
_MAX_TOKEN_INFO_BYTES = 1 << 16

def _sid_pointer_offset(raw: bytes, base: int, size: int) -> int | None:
    """Where the SID bytes sit inside a ``SID_AND_ATTRIBUTES`` buffer, or ``None`` when unknowable.

    Both classes decoded here (`TOKEN_USER`, `TOKEN_MANDATORY_LABEL`) are a ``SID_AND_ATTRIBUTES``: the
    OS fills in a pointer, and **that pointer aims inside the very buffer we handed in** (measured on
    this machine: a 16-byte delta for `TokenUser`). A pointer is only meaningful next to the base it is
    relative to, which is why the caller passes the buffer's address in.

    Returns ``None`` rather than guessing when the pointer does not land inside the buffer: an offset
    read out of memory we do not own turns a real SID into `ERROR_INVALID_SID` with no visible cause,
    which is exactly the failure this module spent a while producing before the layout was understood.
    """

    if len(raw) < ctypes.sizeof(ctypes.c_void_p):
        return None
    pointer = ctypes.c_void_p.from_buffer_copy(raw[: ctypes.sizeof(ctypes.c_void_p)]).value or 0
    offset = pointer - base
    # `offset >= 8`: a SID cannot start inside the record's own pointer field, so anything below the
    # struct's first member is a pointer we failed to place, not a SID.
    if 8 <= offset <= size - 8:
        return offset
    return None


def _extract_sid(raw: bytes, base: int, size: int) -> bytes | None:
    """Copy the SID out of a ``SID_AND_ATTRIBUTES`` record buffer.

    The copy is not optional: the SID lives inside the buffer `GetTokenInformation` was given, so it is
    only guaranteed to exist while that buffer is alive. Keeping the bytes in a ``bytes`` object we own
    makes the answer independent of anyone else's allocation lifetime — the failure this replaced was
    real and reproducible (see the module docstring).

    The length comes from the SID's own ``SubAuthorityCount`` byte (offset 1): a SID is
    ``8 + 4 x count`` bytes.
    """

    offset = _sid_pointer_offset(raw, base, size)
    if offset is None or len(raw) < offset + 8:
        return None
    length = 8 + 4 * raw[offset + 1]
    if len(raw) < offset + length:
        return None
    return bytes(raw[offset : offset + length])


def _sid_to_string(advapi32: Any, kernel32: Any, sid_bytes: bytes) -> str | None:
    """`ConvertSidToStringSidW` over a SID we own, and free the string it allocated.

    ``sid_bytes`` is wrapped in a local buffer that stays alive for the call, so the address handed to
    the API is ours for the whole conversion.
    """

    if not sid_bytes:
        return None
    buffer = ctypes.create_string_buffer(sid_bytes, len(sid_bytes))
    out = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(ctypes.cast(buffer, ctypes.c_void_p), ctypes.byref(out)):
        return None
    try:
        return out.value
    finally:
        kernel32.LocalFree(out)


def _load_libraries() -> tuple[Any, Any]:
    """`advapi32`/`kernel32`, or two ``None``s off Windows.

    Kept as a function rather than a module-level load on purpose: v1 is a Windows provider
    (``AGENTS.md`` §7), and this keeps the module importable — and its failure path exercisable — on
    any host.
    """

    if os.name != "nt":  # pragma: no cover - v1 is a Windows provider
        return None, None
    return (
        ctypes.WinDLL("advapi32", use_last_error=True),
        ctypes.WinDLL("kernel32", use_last_error=True),
    )


def _declare_signatures(advapi32: Any, kernel32: Any) -> None:
    """Pin the argument and return types of every entry point used here.

    Not decoration, and not optional: ctypes defaults an unannotated parameter to C ``int``, so a
    64-bit ``HANDLE`` — and the current-process pseudo-handle is ``(HANDLE)-1`` — arrives truncated and
    the call dies with an ``OverflowError`` raised from inside ctypes rather than with the access
    error it should have reported. Declaring them once keeps that failure mode out of a module whose
    whole point is to report failures accurately.
    """

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _status_message(status: int) -> str:
    """`err=5 (Access is denied.)` — the number *and* the OS's own text for it.

    The text is localized and may be missing (`FormatError` only knows the messages the current
    thread's language tables carry), which is why the number is always there and the text is
    best-effort. A reader who sees only "err=5" can still look the code up; a reader who sees only
    "Access is denied." cannot.
    """

    try:
        text = ctypes.FormatError(status).strip()
    except Exception:  # pragma: no cover - defensive: FormatError is best-effort
        return f"err={status}"
    return f"err={status} ({text})" if text else f"err={status}"


def _open_token(advapi32: Any, process_handle: Any) -> tuple[Any | None, int]:
    """Open ``process_handle``'s token for query only. Returns ``(handle, status)``.

    Split out of :func:`_open_process_token` when the process-targeted probe arrived: the call is the
    same for the current-process pseudo-handle as for a real handle from ``OpenProcess``, and this
    module's rule is that a token is read through one implementation rather than two that must be kept
    in step. ``process_handle`` stays the caller's to close — this function never closes what it did
    not open, and it returns only the token it did open.
    """

    handle = wintypes.HANDLE()
    ok = advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(handle))
    if not ok:
        return None, ctypes.get_last_error() or 0
    return handle, 0


def _open_process_token(advapi32: Any, kernel32: Any) -> tuple[Any | None, int]:
    """Open the *current* process's token for query only. Returns ``(handle, status)``.

    A seam rather than an inline call: the failure path is the interesting one, and there is no way to
    provoke ``OpenProcessToken`` to fail on a healthy machine — so the tests replace this function
    (:func:`probe_identity` looks it up by name at call time).
    """

    return _open_token(advapi32, kernel32.GetCurrentProcess())


def _open_process(kernel32: Any, pid: int) -> tuple[Any | None, int]:
    """Open another process with the least right that still reads its token. ``(handle, status)``.

    A seam for the same reason :func:`_open_process_token` is one: on a healthy machine the real call
    succeeds, so the branch that reports "there is no such process" would otherwise never run until
    the day a broker is handed a stale pid. The handle belongs to the caller, which must close it.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` and not ``PROCESS_QUERY_INFORMATION``: see the constant. A
    pid that names no process comes back as ``ERROR_INVALID_PARAMETER`` (87), which is a different
    status from the ``ERROR_ACCESS_DENIED`` (5) a protected process answers with — the caller has to
    be able to say which of the two happened.
    """

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, ctypes.get_last_error() or 0
    return handle, 0


def _get_token_information(
    advapi32: Any, handle: Any, info_class: int
) -> tuple[bytes | None, int, int, int]:
    """Read one token information class.

    Returns ``(raw bytes, buffer address, buffer size, status)``. The buffer address is returned
    because the two ``SID_AND_ATTRIBUTES`` classes fill their struct with a pointer *into this very
    buffer*, and a pointer is only meaningful next to the base it is relative to (see
    :func:`_sid_pointer_offset`).

    The needed size is asked for first and the buffer is grown once on `ERROR_INSUFFICIENT_BUFFER`,
    because these classes carry variable-length data (a SID, a group list) and a fixed guess is
    exactly how this call fails in the field.
    """

    needed = wintypes.DWORD(0)
    advapi32.GetTokenInformation(handle, info_class, None, 0, ctypes.byref(needed))
    size = int(needed.value) or 256
    for _attempt in range(2):
        if size > _MAX_TOKEN_INFO_BYTES:  # pragma: no cover - defensive
            return None, 0, 0, ERROR_INSUFFICIENT_BUFFER
        buffer = ctypes.create_string_buffer(size)
        if advapi32.GetTokenInformation(handle, info_class, buffer, size, ctypes.byref(needed)):
            return buffer.raw, ctypes.addressof(buffer), size, 0
        status = ctypes.get_last_error() or 0
        if status != ERROR_INSUFFICIENT_BUFFER:
            return None, 0, 0, status
        size = int(needed.value) or size * 2
    return None, 0, 0, ERROR_INSUFFICIENT_BUFFER


def _sid_rid(sid_bytes: bytes) -> int | None:
    """The last sub-authority — for an integrity SID that *is* the level."""

    if len(sid_bytes) < 8 or not sid_bytes[1]:
        return None
    # The sub-authorities are little-endian DWORDs; indexing the bytes directly avoids handing the OS a
    # pointer into memory whose lifetime we do not control (see `_sid_pointer_offset`).
    offset = 8 + 4 * (sid_bytes[1] - 1)
    return int.from_bytes(sid_bytes[offset : offset + 4], "little")


def _integrity_word(advapi32: Any, kernel32: Any, sid_bytes: bytes | None) -> tuple[str | None, str]:
    """Map the token's integrity SID to one of low/medium/high/system.

    The mapping is Windows' own `>=` ladder over the `SECURITY_MANDATORY_*_RID_BASE` values, which is
    why `medium plus` (0x2100) reads as `medium` — inside Windows' own range. What does *not* map is a
    rid below `low` (0x0005, `SECURITY_MANDATORY_UNTRUSTED_RID`, which really occurs) or above the
    ladder's top; those are reported as unknown rather than rounded to the nearest of the four words.
    """

    if not sid_bytes:
        return None, "the token reported no readable integrity SID"
    sid = _sid_to_string(advapi32, kernel32, sid_bytes)
    rid = _sid_rid(sid_bytes)
    label = sid if sid is not None else "<unreadable>"
    if rid is None:
        return None, f"the integrity SID {label} had no readable sub-authority"
    for base, word in _INTEGRITY_THRESHOLDS:
        if rid >= base:
            return word, f"TokenIntegrityLevel -> {word} (SID {sid}, rid=0x{rid:04x})"
    return None, f"integrity SID {label} (rid=0x{rid:04x}) is not one of low/medium/high/system"


def _read_token_facts(
    advapi32: Any, kernel32: Any, handle: Any
) -> tuple[str | None, str | None, bool | None, list[str]]:
    """Read the three observable facts off an **open** token handle: SID, integrity word, elevation.

    Shared by both probes, because "what does this token say" is one question whether the token belongs
    to this process or to another one: the classes, the copy-out, the integrity ladder and the elevation
    read are the module's, restated nowhere. The evidence lines are therefore identical in both paths,
    which is also what lets the failure tests pin them once.

    The three reads are independent: a token that answers ``TokenElevation`` but refuses
    ``TokenIntegrityLevel`` yields ``False``/``None`` rather than nothing at all, because those are two
    different observations and only one of them failed. The handle is the caller's and is not closed
    here — the caller opened it and the caller knows when it is done.
    """

    sid: str | None = None
    integrity: str | None = None
    elevated: bool | None = None
    evidence: list[str] = []

    raw, base, size, status = _get_token_information(advapi32, handle, TOKEN_USER)
    if raw is None:
        evidence.append(f"GetTokenInformation(TokenUser) failed: {_status_message(status)}")
    else:
        sid_bytes = _extract_sid(raw, base, size)
        sid = None if sid_bytes is None else _sid_to_string(advapi32, kernel32, sid_bytes)
        if sid is None:
            evidence.append("the token's user SID could not be converted to S-1-... form")
        else:
            evidence.append(f"TokenUser -> {sid}")

    raw, base, size, status = _get_token_information(advapi32, handle, TOKEN_INTEGRITY_LEVEL)
    if raw is None:
        evidence.append(f"GetTokenInformation(TokenIntegrityLevel) failed: {_status_message(status)}")
    else:
        integrity, note = _integrity_word(advapi32, kernel32, _extract_sid(raw, base, size))
        evidence.append(note)

    raw, _base, _size, status = _get_token_information(advapi32, handle, TOKEN_ELEVATION)
    if raw is None:
        evidence.append(f"GetTokenInformation(TokenElevation) failed: {_status_message(status)}")
    else:
        # The token says yes or no; anything else would be a guess, and a guess is the one thing this
        # module may not return for this field. A read that *failed* leaves `None`, which is a
        # different answer from `False` and has to stay one.
        elevated = bool(int.from_bytes(raw[:4], "little"))
        evidence.append(f"TokenElevation -> {elevated}")

    return sid, integrity, elevated, evidence


def _process_session_id(kernel32: Any, pid: int) -> tuple[int | None, int]:
    """`ProcessIdToSessionId` — the kernel's own answer to "which session does this pid run in".

    A seam in the same sense :func:`_open_process` is one: on a healthy machine the call succeeds, so
    the branch that reports "this source did not answer" could not be reached in a test otherwise. It
    is deliberately *independent* of the token: this is the second opinion `TokenSessionId` is
    cross-checked against, and an opinion derived from the first one would prove nothing.
    """

    session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
        return None, ctypes.get_last_error() or 0
    return int(session.value), 0


def _process_creation_time(kernel32: Any, process_handle: Any) -> tuple[int | None, int]:
    """The target's creation time as the raw 64-bit `FILETIME` integer from `GetProcessTimes`.

    Raw on purpose. The only thing this number is for is comparison — "is this still the process that
    was observed, or a later one that reused the pid" — and a `FILETIME` compares as an integer
    without any calendar in between. It is never rendered into a committed document, so turning it
    into a datetime here would add an epoch-and-timezone decision to a fact that needs none.

    The four out-parameters are all required by the API; only the creation time is kept.
    """

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    ok = kernel32.GetProcessTimes(
        process_handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    )
    if not ok:
        return None, ctypes.get_last_error() or 0
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime, 0


def _read_elevation_type(advapi32: Any, handle: Any) -> tuple[str | None, list[str]]:
    """`TokenElevationType` as one of ``default``/``full``/``limited``, or ``None`` plus why.

    Unrecognised is **not** rounded to the nearest word. `winnt.h` names exactly three values, so a
    fourth would mean the token answered a question this module does not have the words for —
    reporting the closest one would invent a fact about a token that declined to give one.
    """

    evidence: list[str] = []
    raw, _base, _size, status = _get_token_information(advapi32, handle, TOKEN_ELEVATION_TYPE)
    if raw is None:
        evidence.append(f"GetTokenInformation(TokenElevationType) failed: {_status_message(status)}")
        return None, evidence
    value = int.from_bytes(raw[:4], "little")
    word = _ELEVATION_TYPES.get(value)
    if word is None:
        known = ", ".join(f"{number} ({name})" for number, name in sorted(_ELEVATION_TYPES.items()))
        evidence.append(
            f"TokenElevationType returned {value}, which is none of {known}: an elevation type this "
            "module has no word for"
        )
        return None, evidence
    evidence.append(f"TokenElevationType -> {word}")
    return word, evidence


def _read_session_id(
    advapi32: Any, kernel32: Any, handle: Any, pid: int
) -> tuple[int | None, list[str]]:
    """The target's session id, read twice and believed only where the two answers agree.

    `TokenSessionId` and `ProcessIdToSessionId` answer the same question from two places that do not
    derive from each other — the token the process runs under, and the kernel's record for the pid —
    which is what makes agreement mean something. A stale pid, or a token that outlived the session
    it was created in, shows up as the two disagreeing; and a disagreement is reported as *unknown*
    rather than as either number, because this module has no basis to prefer one and would be
    choosing a fact to print.
    """

    evidence: list[str] = []
    token_session: int | None = None
    raw, _base, _size, status = _get_token_information(advapi32, handle, TOKEN_SESSION_ID)
    if raw is None:
        evidence.append(f"GetTokenInformation(TokenSessionId) failed: {_status_message(status)}")
    else:
        token_session = int.from_bytes(raw[:4], "little")

    api_session, api_status = _process_session_id(kernel32, pid)
    if api_session is None:
        evidence.append(f"ProcessIdToSessionId(pid={pid}) failed: {_status_message(api_status)}")

    if token_session is not None and api_session is not None:
        if token_session != api_session:
            evidence.append(
                f"TokenSessionId -> {token_session} but ProcessIdToSessionId -> {api_session}: the "
                "two independent reads disagree, so the session is unknown"
            )
            return None, evidence
        evidence.append(
            f"TokenSessionId -> {token_session} and ProcessIdToSessionId -> {api_session}: the two "
            "independent reads agree"
        )
        return token_session, evidence
    if token_session is not None:
        evidence.append(
            f"TokenSessionId -> {token_session}: used, because ProcessIdToSessionId was unavailable"
        )
        return token_session, evidence
    if api_session is not None:
        evidence.append(
            f"ProcessIdToSessionId -> {api_session}: used, because the token's TokenSessionId was "
            "unavailable"
        )
        return api_session, evidence
    evidence.append("the target's session id could not be read from either source")
    return None, evidence


def _read_app_container(
    advapi32: Any, kernel32: Any, handle: Any
) -> tuple[bool | None, str | None, list[str]]:
    """`TokenIsAppContainer`, and the application identity only an AppContainer token carries.

    The three answers are the point of the function, and the evidence says which one happened in
    words, so nobody has to infer it from a bare ``None``:

    * ``True`` — the token carries an application identity, so `TokenAppContainerSid` is read and
      reported;
    * ``False`` — an ordinary Win32 token with **no** application identity: that is the *answer* to
      the question, not a failure to look, and the evidence says so explicitly;
    * ``None`` — the question could not be put to the token, or it answered with a value that is
      neither 0 nor 1.
    """

    evidence: list[str] = []
    raw, _base, _size, status = _get_token_information(advapi32, handle, TOKEN_IS_APP_CONTAINER)
    if raw is None:
        evidence.append(
            f"GetTokenInformation(TokenIsAppContainer) failed: {_status_message(status)}"
        )
        return None, None, evidence

    value = int.from_bytes(raw[:4], "little")
    if value == _APP_CONTAINER_FALSE:
        evidence.append(
            "TokenIsAppContainer -> False; not in an AppContainer: there is no application "
            "identity in the token to read"
        )
        return False, None, evidence
    if value != _APP_CONTAINER_TRUE:
        evidence.append(
            f"TokenIsAppContainer returned {value}, which is neither {_APP_CONTAINER_TRUE} (true) "
            f"nor {_APP_CONTAINER_FALSE} (false)"
        )
        return None, None, evidence

    raw, base, size, status = _get_token_information(advapi32, handle, TOKEN_APP_CONTAINER_SID)
    if raw is None:
        evidence.append(
            f"GetTokenInformation(TokenAppContainerSid) failed: {_status_message(status)}"
        )
        return True, None, evidence
    sid_bytes = _extract_sid(raw, base, size)
    if sid_bytes is None:
        evidence.append("the token's AppContainer SID could not be extracted from the buffer")
        return True, None, evidence
    container_sid = _sid_to_string(advapi32, kernel32, sid_bytes)
    if container_sid is None:
        evidence.append("the token's AppContainer SID could not be converted to S-1-... form")
        return True, None, evidence
    evidence.append(f"TokenIsAppContainer -> True; TokenAppContainerSid -> {container_sid}")
    return True, container_sid, evidence


def _read_peer_facts(
    advapi32: Any, kernel32: Any, handle: Any, pid: int, elevated: bool | None
) -> tuple[str | None, int | None, bool | None, str | None, list[str]]:
    """The facts only the *server* can read about a caller (draft §114).

    ``elevated`` is passed in rather than re-read here: the cross-check below is about the two
    readings, not about which function took them, and :func:`_read_token_facts` already owns
    `TokenElevation`.

    **The elevation cross-check.** Windows states the implication in one direction only: `full` is an
    elevated token and `limited` is a filtered one, so a `full` token reading ``elevated=False`` — or a
    `limited` one reading ``True`` — is a contradiction, and the *word* is unusable: it is withdrawn
    (``None`` plus evidence naming both readings). `elevated` is kept exactly as read; it is what the
    token said about itself, and this function has no standing to overrule a value whose twin is the
    thing that failed to agree with it.

    ``default`` carries **no** constraint, and asserting one is the defect this table exists to
    prevent: `TokenElevationTypeDefault` means "not a split token" — a standard user (``False``) *or*
    an administrator with UAC off (``True``, `high` integrity). Requiring ``default`` to pair with
    ``False`` would declare every UAC-disabled machine's elevation type unreadable, which is a
    legitimate caller being refused on a fact that was never in doubt.

    Everything else is independent, so one failed read does not remove the others.
    """

    evidence: list[str] = []

    elevation_type, notes = _read_elevation_type(advapi32, handle)
    evidence.extend(notes)

    required_elevated = None if elevation_type is None else _ELEVATION_REQUIRES.get(elevation_type)
    if (
        required_elevated is not None
        and elevated is not None
        and required_elevated != elevated
    ):
        evidence.append(
            f"TokenElevationType -> {elevation_type} contradicts TokenElevation -> {elevated}: a "
            f"'{elevation_type}' token is one whose TokenElevation is {required_elevated}, so the "
            "type is unknown (TokenElevation stands)"
        )
        elevation_type = None

    session_id, notes = _read_session_id(advapi32, kernel32, handle, pid)
    evidence.extend(notes)
    is_app_container, app_container_sid, notes = _read_app_container(advapi32, kernel32, handle)
    evidence.extend(notes)

    return elevation_type, session_id, is_app_container, app_container_sid, evidence


@dataclass(frozen=True)
class ClientIdentity:
    """What this process can prove about itself. Every field is None when it could not be established."""

    sid: str | None
    integrity: str | None
    elevated: bool | None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    #: Always available, and the one field that is: `os.getpid()` cannot fail. Declared with a default
    #: factory rather than as a required positional field so that both spellings the interface allows
    #: work — `ClientIdentity(sid, integrity, elevated)` and `ClientIdentity(sid, integrity, elevated,
    #: pid=n)`.
    pid: int = field(default_factory=os.getpid)

    def is_complete(self) -> bool:
        """True when every field the broker request schema requires can be filled.

        `pid` is always set and `application_id` comes from the caller, so completeness is exactly
        "the two facts only the OS can supply were both read".
        """

        return self.sid is not None and self.integrity is not None

    def to_document(self, application_id: str) -> dict[str, Any]:
        """The `client` block of a broker request: exactly {sid, pid, integrity, application_id}.

        The shape is the schema's (`additionalProperties: false`), and inventing a placeholder for a
        field that could not be read is not this function's job.

        **It refuses rather than emitting a null.** The schema requires all four fields and types
        each of them, so a ``None`` passed through here produces a document that fails its own
        validation — and that failure surfaces from `broker/protocol.py`'s `validate_document`, which
        reports *caller* input as `INVALID_INPUT`. The caller did nothing wrong: the *probe* could not
        read the fact. So the refusal happens here, as `SELF_VALIDATION_FAILED` (exit 8 — this build's
        word for "the core was about to emit an invalid document", AGENTS.md §7), naming the exact
        schema field that would have been null.

        :meth:`is_complete` is the cheap question to ask first; this is the one that cannot be
        skipped by forgetting to ask it.
        """

        missing = [
            field_name
            for field_name, value in (
                ("sid", self.sid),
                ("integrity", self.integrity),
                ("application_id", application_id),
            )
            if value is None
        ]
        if missing:
            names = ", ".join(f"client.{field_name}" for field_name in missing)
            raise AirootError(
                "SELF_VALIDATION_FAILED",
                f"the client block cannot be built: {names} would be null, and broker-request "
                "requires client.sid, client.pid, client.integrity and client.application_id",
                evidence=[
                    f"client.{field_name} would be null" for field_name in missing
                ]
                + list(self.evidence),
            )

        return {
            "sid": self.sid,
            "pid": self.pid,
            "integrity": self.integrity,
            "application_id": application_id,
        }


@dataclass(frozen=True)
class ProcessIdentity:
    """What a *different* process can be observed to be. Every field is None when it could not be read.

    ``pid`` is the one field that is always there, because it is what the caller asked about: a pid
    that names nothing still yields ``ProcessIdentity(pid=...)`` with everything else unknown, and
    "unknown" is a legitimate answer rather than a defect. ``elevated=False`` and ``elevated=None``
    are the two facts this type exists to keep apart — the target's token said no, versus nobody
    managed to ask it.

    Observation only. Being able to read this is not authorisation for anything; see
    :func:`probe_process`.
    """

    pid: int
    sid: str | None
    integrity: str | None
    elevated: bool | None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    #: `TokenElevationType` as one of ``default``/``full``/``limited`` (draft §114).
    elevation_type: str | None = None
    #: `TokenSessionId`, cross-checked against `ProcessIdToSessionId` before it is believed.
    session_id: int | None = None
    #: `TokenIsAppContainer`. The observable stand-in for the claim `client.application_id`: an
    #: AppContainer process really does carry an application identity, an ordinary Win32 process
    #: carries none at all (``False``), and "no identity" is not the same fact as "no answer".
    is_app_container: bool | None = None
    #: `TokenAppContainerSid` when `is_app_container` is ``True``. ``None`` for an ordinary process,
    #: which is the *answer* here rather than a failure to look.
    app_container_sid: str | None = None
    #: The target's creation time as a raw `FILETIME` integer, so a pid can be shown to still name the
    #: process that was observed rather than a later process that reused the number.
    creation_time: int | None = None

    def is_complete(self) -> bool:
        """True when the three observable facts are all present.

        The contrast with :meth:`ClientIdentity.is_complete` is deliberate: there, completeness is
        what the *request schema* requires of a client block (SID and integrity; elevation is not one
        of its required fields), while here nothing outside this observation consumes the answer yet,
        so completeness means the whole observation — SID, integrity level and elevation — succeeded.
        """

        return self.sid is not None and self.integrity is not None and self.elevated is not None


def probe_identity() -> ClientIdentity:
    """Read-only observation of this process's identity. NEVER raises for an unreadable token."""

    sid: str | None = None
    integrity: str | None = None
    elevated: bool | None = None
    evidence: list[str] = []
    try:
        advapi32, kernel32 = _load_libraries()
        if advapi32 is None or kernel32 is None:  # pragma: no cover - v1 is a Windows provider
            evidence.append("the Windows token APIs are unavailable on this platform")
            return ClientIdentity(sid=None, integrity=None, elevated=None, evidence=tuple(evidence))

        _declare_signatures(advapi32, kernel32)

        handle, status = _open_process_token(advapi32, kernel32)
        if handle is None:
            evidence.append(f"OpenProcessToken(TOKEN_QUERY) failed: {_status_message(status)}")
            return ClientIdentity(sid=None, integrity=None, elevated=None, evidence=tuple(evidence))
        try:
            sid, integrity, elevated, notes = _read_token_facts(advapi32, kernel32, handle)
            evidence.extend(notes)
        finally:
            kernel32.CloseHandle(handle)
    except Exception as error:  # never raise: a probe that dies teaches the caller nothing
        evidence.append(f"identity probe failed: {error.__class__.__name__}: {error}")
    return ClientIdentity(sid=sid, integrity=integrity, elevated=elevated, evidence=tuple(evidence))


def probe_process(pid: int) -> ProcessIdentity:
    """Read-only observation of another process's token. NEVER raises for an unreadable target.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` on the process, then ``TOKEN_QUERY`` on its token, then the
    same three ``GetTokenInformation`` classes :func:`probe_identity` reads — through the same helpers,
    so this is the inverse of that function and not a second implementation of it.

    **This is observation, not authorisation.** It reports what the target's token can be seen to be
    (user SID, integrity level, whether it is elevated). It says nothing about whether that process is
    *entitled* to the operation it may be asking for: entitlement is policy — which caller may ask for
    which operation, under which plan and which approval — and that policy does not exist in this build
    (ADR-0025's D1 leaves the protected broker to P2). A complete reading means the probe could look,
    never that the caller is welcome; the decision belongs to the broker's policy layer, which is the
    next thing to be built (docs/broker §3:52 lists the check this reading feeds).

    Failure is data, never an exception and never a guess. A pid that names no process, a process that
    exited mid-probe, a protected process whose token refuses to open, a token that will not answer one
    of the classes — each becomes ``None`` plus an ``evidence`` line naming the call and the Windows
    status. ``elevated=False`` means the token was read and answered no; ``elevated=None`` means nobody
    managed to ask, and a caller that conflates the two decides wrongly in one direction or the other.

    Beyond the three shared facts it reads the four a broker needs and a caller cannot supply about
    itself (draft §114 — see :func:`_read_peer_facts` for the elevation cross-check, the session
    cross-check, the AppContainer distinction and the refusal to round an unknown elevation type).
    Every one of them is still "failure is data": the widened observation adds fields, not exceptions.
    """

    sid: str | None = None
    integrity: str | None = None
    elevated: bool | None = None
    elevation_type: str | None = None
    session_id: int | None = None
    is_app_container: bool | None = None
    app_container_sid: str | None = None
    creation_time: int | None = None
    evidence: list[str] = []
    try:
        advapi32, kernel32 = _load_libraries()
        if advapi32 is None or kernel32 is None:  # pragma: no cover - v1 is a Windows provider
            evidence.append("the Windows token APIs are unavailable on this platform")
            return ProcessIdentity(
                pid=pid, sid=None, integrity=None, elevated=None, evidence=tuple(evidence)
            )

        _declare_signatures(advapi32, kernel32)

        process_handle, status = _open_process(kernel32, pid)
        if process_handle is None:
            evidence.append(
                f"OpenProcess(pid={pid}, PROCESS_QUERY_LIMITED_INFORMATION) failed: "
                f"{_status_message(status)}"
            )
            return ProcessIdentity(
                pid=pid, sid=None, integrity=None, elevated=None, evidence=tuple(evidence)
            )
        try:
            # Read off the *process* handle and before the token, because it is independent of the
            # token and answers a question that matters most exactly when the token does not open:
            # "is this still the process that was observed, or a later one reusing the pid".
            creation_time, creation_status = _process_creation_time(kernel32, process_handle)
            if creation_time is None:
                evidence.append(
                    f"GetProcessTimes(pid={pid}) failed: {_status_message(creation_status)}"
                )

            token_handle, status = _open_token(advapi32, process_handle)
            if token_handle is None:
                evidence.append(
                    f"OpenProcessToken(pid={pid}, TOKEN_QUERY) failed: {_status_message(status)}"
                )
                return ProcessIdentity(
                    pid=pid,
                    sid=None,
                    integrity=None,
                    elevated=None,
                    creation_time=creation_time,
                    evidence=tuple(evidence),
                )
            try:
                sid, integrity, elevated, notes = _read_token_facts(advapi32, kernel32, token_handle)
                evidence.extend(notes)
                (
                    elevation_type,
                    session_id,
                    is_app_container,
                    app_container_sid,
                    notes,
                ) = _read_peer_facts(advapi32, kernel32, token_handle, pid, elevated)
                evidence.extend(notes)
            finally:
                # Closed as soon as the reads are done, not at the end: the token handle is needed for
                # nothing else, and every path out of here has to leave it closed. The close sits
                # inside the outer `try` on purpose, so a close that somehow fails becomes evidence
                # rather than an exception escaping a probe that promises never to raise.
                kernel32.CloseHandle(token_handle)
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception as error:  # never raise: a probe that dies teaches the caller nothing
        evidence.append(f"process identity probe failed: {error.__class__.__name__}: {error}")
    return ProcessIdentity(
        pid=pid,
        sid=sid,
        integrity=integrity,
        elevated=elevated,
        elevation_type=elevation_type,
        session_id=session_id,
        is_app_container=is_app_container,
        app_container_sid=app_container_sid,
        creation_time=creation_time,
        evidence=tuple(evidence),
    )
