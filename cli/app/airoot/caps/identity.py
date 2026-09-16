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
        field that could not be read is not this function's job: a caller with
        ``is_complete() == False`` has to decide what to do instead, because a ``None`` passed through
        here would produce a document the published schema rejects.
        """

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
    """

    sid: str | None = None
    integrity: str | None = None
    elevated: bool | None = None
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
            token_handle, status = _open_token(advapi32, process_handle)
            if token_handle is None:
                evidence.append(
                    f"OpenProcessToken(pid={pid}, TOKEN_QUERY) failed: {_status_message(status)}"
                )
                return ProcessIdentity(
                    pid=pid, sid=None, integrity=None, elevated=None, evidence=tuple(evidence)
                )
            try:
                sid, integrity, elevated, notes = _read_token_facts(advapi32, kernel32, token_handle)
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
        pid=pid, sid=sid, integrity=integrity, elevated=elevated, evidence=tuple(evidence)
    )
