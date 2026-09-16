"""The named pipe the protected broker would listen on — the user compatibility mode's wire (§115).

`broker/protocol.py` builds and parses the documents, `caps/identity.py` observes a peer's token and
`broker/policy.py` decides whether a caller may ask. Nothing connected them: this module is the wire,
and it is the **user compatibility mode** docs/broker §2 sanctions — "可以运行无 elevated Broker 的同用户
模拟, 但响应必须带 `security_mode: policy_only` 与 `enforcement: same_user_can_bypass`".

Five negatives, because each is the nearest misreading of what this is:

* **not the protected broker.** No elevation, no Rust, no UAC, no protected Zone R. docs/broker §3's
  pipe name is reused and the DACL is real, but a same-user process can do everything this server can
  — and *every* answer says so, on the wire, in the two fields the schema requires. The server
  therefore **refuses to start at all** from an elevated token (see :func:`serve_pipe`), because then
  the "current user" it would admit is the elevated token's user and the mode's own sentence would be
  describing a different session.
* **not a new implementation of anything.** The documents, the observation, the decision, the framing
  and the mode marking all belong to their own modules: this file calls `protocol`, `identity`,
  `policy`, `transport` and `posture`. It adds exactly three things those cannot have — a named pipe,
  the rule that a verdict must reach the caller, and the fact that the peer's pid comes from the OS.
* **the request's `client` block decides nothing.** The peer's pid comes from
  `GetNamedPipeClientProcessId` on the connected handle and every fact the admission decision reads
  comes from `probe_process` on that pid. `client` is never read, never compared, never echoed. That
  is the whole point of §114/ADR-0041, and :func:`_decision_facts` is where a reader can confirm the
  request is not in scope: it is not a parameter there, and `ProcessIdentity` has no
  `application_id` field for a claimed one to land in.
* **not an authority on who may ask.** `admit_caller` is called with the ``expectation`` the caller of
  `serve_pipe` supplies, because this build has no policy source (see `policy.py`'s known boundary).
  This module does not invent one.
* **not a durable audit.** docs/broker §8's audit events belong to the protected service. The report
  `serve_pipe` returns is a test- and operator-facing count, not a record; nothing is appended to
  `logs/audit`.

**What can be honoured, and what cannot.** `probe_root` is the only operation this build answers.
`commit_plan`, `recover_transaction` and `gc_apply` are refused with `NOT_IMPLEMENTED` (exit 1) and
ADR-0025's pointer, because they need the production approval issuer and the elevated broker that
ADR-0025's D1 leaves to P2. The routing is :data:`ROUTING`, with one line of *why* per refused
operation in :data:`REFUSAL_REASONS` — the pattern `protocol.ADDITIONAL_REQUIREMENT_REASONS` uses — so
the refusal is a decision a reader can re-examine rather than a rule whose only defence is that
deleting it turns a test red.

**`probe_root` answers with a verdict and evidence, because the response schema has nowhere else to
put an answer.** `broker-response` is `additionalProperties: false` with ten fields and no
operation-result field (the orchestrator measured this before this module was written), so this
operation cannot carry a payload and must not invent one. What it *can* say is the verdict
(`status=ok`, `reason_code=SUCCESS`, `transaction_id=null`, `state=null`) plus evidence lines naming
what was observed. The honest consequence, stated rather than buried: **a caller learns that the root
probe succeeded and what it looked at, not the probe's values.** The counts and the ACL digest that
`cli/tests/fake_broker.py`'s in-process `probe_root` carries are deliberately **not** here, because
they are runtime state and this pipe's answer has to be reproducible byte for byte — the golden
fixture going into `cli/tests/fixtures/golden/` is produced through :func:`call_broker` against a real
server, and `test_golden_fixtures_reproduce_exactly` regenerates it on every machine. A document that
carried an entry count or a DACL digest would be a fingerprint and would make that fixture
unreproducible on any other machine. Where the in-process harness can afford to observe more, it
does; the wire is the stricter surface, and this is the trade written down.

**Three measurements that contradict a plausible reading, each kept where it bites.** All three were
found by running the pipe rather than by reasoning about it, and each is also asserted in
`cli/tests/test_l1_broker_pipe.py`:

1. **`PIPE_REJECT_REMOTE_CLIENTS` belongs in ``dwPipeMode``, not ``dwOpenMode``.** The brief for this
   stage asked for ``dwOpenMode``. `CreateNamedPipeW` returns `INVALID_HANDLE_VALUE` with
   `ERROR_INVALID_PARAMETER` (87) for ``dwOpenMode = PIPE_ACCESS_DUPLEX | PIPE_REJECT_REMOTE_CLIENTS``
   and succeeds with the same bit in ``dwPipeMode``; `winbase.h` groups it under ``dwPipeMode`` while
   MSDN's page lists it under ``dwOpenMode``. The OS wins, so it goes in :data:`PIPE_MODE`, and
   :data:`PIPE_REJECT_REMOTE_CLIENTS` keeps the value ``0x00000008`` the brief named. The bit is not
   silently ignored where the brief put it — it is rejected — which
   ``test_the_reject_remote_bit_is_rejected_in_dw_open_mode`` measures, and
   ``test_the_os_reports_the_local_only_bit_set`` confirms the OS *took* it where this module puts it.
2. **The pipe is a byte stream, not a message stream.** It was first created with
   `PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE`, on the reasoning that a frame should arrive as one
   message. In message mode a `ReadFile` **fails** when its buffer is smaller than the message: the
   client writes header + payload as one message, the server asks for the 4 header bytes, and
   `ReadFile` answers FALSE with ``ERROR_MORE_DATA`` (234). `transport.read_frame` is declared against
   a byte stream — ``read(n)`` returns "at most ``n`` bytes" and is asked repeatedly — so
   :data:`PIPE_MODE` is byte type with byte read mode, and the length prefix does the framing.
   (`PIPE_TYPE_BYTE | PIPE_READMODE_MESSAGE` is itself rejected with err 87: a read mode only means
   something in message mode.)
3. **Every write is flushed before the handle is closed, and without that the answer is silently
   truncated.** A byte-mode pipe discards whatever is still buffered when its handle is closed, so the
   client received the 4 header bytes and then ``ERROR_PIPE_NOT_CONNECTED`` (233) on the body — a
   response that looks like a transport failure for a request that was answered. With
   `FlushFileBuffers` after the write, all payload bytes arrive. See :func:`_write_frame`.
4. **A server waiting in `ConnectNamedPipe` can never observe `stop`, so the wait has to be made
   endable.** There is no server-side connect timeout; the measurement agent's probe and this module's
   first one both hung on exactly this. :func:`_stop_watcher` releases the wait by connecting, and
   `test_stop_releases_a_server_nobody_ever_calls` is the case that wedges without it.
5. **`ConnectNamedPipe` FALSE is not one condition.** Only `ERROR_PIPE_CONNECTED` (535) means
   "already connected"; `ERROR_NO_DATA` (232) means the peer connected and *disconnected* before the
   call, so treating any FALSE as success serves a dead peer. :func:`_connect` branches on the status.
6. **A remote peer's pid must not be believed, and local-ness is measurable.** A loopback-SMB client
   makes `GetNamedPipeClientProcessId` answer ``65279``, which names no local process; if that number
   ever collided with a live local pid, admission would be deciding about an unrelated local process.
   `GetNamedPipeClientComputerNameW` answers FALSE with `ERROR_PIPE_LOCAL` (229) for a local peer, so
   :func:`_client_is_local` refuses a non-local peer **before anything is observed about it**.
7. **A pipe's name is not exclusive, and the default DACL is not safe.** `FILE_FLAG_FIRST_PIPE_INSTANCE`
   only makes *this* create fail (err 5) when the name is already held — a later process creating a
   plain instance of the same name succeeds, and names are case-insensitive in both the leaf and the
   `\pipe\` component — so it is squat detection and never exclusivity. And omitting the SDDL does not
   fall back to something safe: the DACL then comes from the creating token's default, measured at
   **5 ACEs including `Everyone` and `ANONYMOUS LOGON` with read/execute**. Hence the explicit SDDL and
   the fail-closed err 5 above: **the DACL cannot make the name exclusive against a same-user
   process**, which is exactly what docs/broker §2's user compatibility mode says about itself,
   measured rather than assumed.

**The pipe's DACL, and the hole in it that gives this mode its name.** The SDDL asks for exactly
three trustees: SYSTEM, Builtin Administrators, and **the current user**, whose SID is read from
`identity.probe_identity()` and never hard-coded. Allowing the current user is *precisely* the
same-user bypass the mode is named after — the user who runs the server can also connect to it, read
the root, and edit every file the server reads — and it is written down here rather than quietly
narrowed, because a DACL admitting only SYSTEM would not make this boundary real either; it would only
make the mode's own name a lie. What the DACL does buy is the thing §3 actually asks for: a
**local-only** pipe (`PIPE_REJECT_REMOTE_CLIENTS`) that no other user on the machine may open. The
requested descriptor lands as asked (three `ACCESS_ALLOWED` ACEs, no inherited entries), but the
**read-back SDDL string is not the request** — `GA` is canonicalised to `FA` — so the test compares
the ACE set rather than the string, and the report carries the string *as asked for* rather than a
round-tripped one that would report drift forever.

**Handles.** Every kernel handle is closed on every path, including the error paths, and the pipe
handle is closed after each connection so `max_requests` connections do not accumulate instances.
`serve_pipe` is safe to run in a thread — the tests drive it exactly that way, with the server in one
thread and `call_broker` in the same process, so the peer the server observes is a genuinely
different pid from the OS's point of view and not a stand-in.
"""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable

from ..caps import identity
from ..exits import EXIT_SUCCESS, REASON_EXIT, AirootError
from ..posture import SECURITY_MODE, enforcement_for, posture
from . import protocol, transport
from .policy import CallerExpectation, admit_caller

__all__ = [
    "PIPE_MODE",
    "PIPE_NAME",
    "PIPE_REJECT_REMOTE_CLIENTS",
    "REFUSAL_REASONS",
    "ROUTING",
    "call_broker",
    "exchange",
    "serve_pipe",
]

# --------------------------------------------------------------------------------------------- #
# the constants the OS dispatches on
# --------------------------------------------------------------------------------------------- #

#: docs/broker §3's own name for the pipe, spelled exactly as §3 does.
PIPE_NAME = r"\\.\pipe\airoot-broker-v1"

#: `PIPE_REJECT_REMOTE_CLIENTS` (`winbase.h`). Named here at the value the brief gave, and passed in
#: `dwPipeMode` — see measurement 1 in the module docstring for why that is not where the brief put it.
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008

#: `PIPE_ACCESS_DUPLEX` (`winbase.h`): both ends read and write. The one ``dwOpenMode`` bit this
#: module sets.
PIPE_ACCESS_DUPLEX = 0x00000003

#: `PIPE_TYPE_MESSAGE` / `PIPE_READMODE_MESSAGE` (`winbase.h`) — declared so the *rejected*
#: combination below is nameable rather than a bare number. This build passes neither.
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002

#: `PIPE_TYPE_BYTE` / `PIPE_READMODE_BYTE` / `PIPE_WAIT`: the type this build uses, the only read mode
#: byte type permits, and blocking reads (`CreateNamedPipeW`'s two timeouts are ignored under
#: `PIPE_WAIT`, so a caller's timeout is the client's business — see `call_broker`).
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000

#: ``dwPipeMode``: **byte** framing, blocking, and the local-only bit.
#:
#: Byte mode, and that was measured twice rather than assumed. The pipe was first created in message
#: mode (`PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE`), on the reasoning that a frame should arrive as
#: one message. It does not work with this framing: message mode makes a `ReadFile` **fail** when the
#: buffer is smaller than the message — the client writes the whole frame (header + payload) as one
#: message, the server asks for the 4 header bytes, and `ReadFile` returns FALSE with
#: ``ERROR_MORE_DATA`` (234). `transport.read_frame` is declared against a **byte stream** — its
#: ``read(n)`` returns "at most ``n`` bytes" and is asked repeatedly, which is the contract message
#: mode refuses to honour.
#:
#: The second measurement: byte type and `PIPE_READMODE_MESSAGE` together are **rejected** by
#: `CreateNamedPipeW` (err 87) — a read mode only has meaning in message mode. So this is neither bit
#: and the length prefix does all the framing, which is what the prefix is for.
#:
#: `PIPE_REJECT_REMOTE_CLIENTS` is independent of both, so the local-only guarantee is unaffected:
#: `GetNamedPipeInfo` reports the bit **set** in this mode (probed: ``0x00000009``, i.e. the low bit
#: for byte type plus `0x8`; message mode read back ``0x0000000d``, the same nibble with the type bit).
PIPE_MODE = PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS

#: `PIPE_UNLIMITED_INSTANCES` — the loop creates one instance per request and closes it again.
PIPE_UNLIMITED_INSTANCES = 255

#: `PIPE_SERVER_END` (`winbase.h`): the bit `GetNamedPipeInfo` sets for the server's own handle. The
#: local-only bit is the only *policy* flag asked for here, so the answer to "did the OS take it" is
#: `flags & PIPE_REJECT_REMOTE_CLIENTS`, and this constant exists to read the rest of the value.
PIPE_SERVER_END = 0x80000000

#: `ERROR_PIPE_CONNECTED` (`winerror.h`). **Success, not failure**: it is what `ConnectNamedPipe`
#: reports when a client connected between `CreateNamedPipeW` and the call. Reading it as an error
#: loses the client that is already there, and the pipe then appears to answer only every second
#: caller — or to hang on the first.
ERROR_PIPE_CONNECTED = 535

#: `ERROR_PIPE_BUSY` — the client's own "all instances are busy" answer, which is the one a client
#: retries rather than reports.
ERROR_PIPE_BUSY = 231

#: `ERROR_NO_DATA` — what `ConnectNamedPipe` answers when the peer connected and then *disconnected*
#: before the call. It is **not** success (see :func:`_connect`): that connection is already gone.
ERROR_NO_DATA = 232

#: `ERROR_PIPE_LOCAL` — `GetNamedPipeClientComputerNameW`'s answer for a local peer, and the signal
#: that the caller's pid may be believed (see :func:`_client_is_local`).
ERROR_PIPE_LOCAL = 229

#: `FILE_FLAG_FIRST_PIPE_INSTANCE` (`winbase.h`). A *squat detector*, not exclusivity: a later
#: process creating a **plain** instance of the same name succeeds, so this cannot make the name
#: ours. What it does buy is that *our* create fails closed when the name is already held, which is
#: the one case where continuing would mean talking to something that is not us.
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000

#: `FILE_FLAG_OVERLAPPED`. NOT used: see :func:`_connect_or_stop` for why `stop` is honoured with a
#: stop-connect helper thread instead — an overlapped server handle would demand an `OVERLAPPED` on
#: every `ReadFile`/`WriteFile` too, and the framing layer's `read(n)` contract is synchronous.
FILE_FLAG_OVERLAPPED = 0x40000000

#: `GENERIC_READ | GENERIC_WRITE` for the client's `CreateFileW`; `OPEN_EXISTING`.
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

#: `SECURITY_DESCRIPTOR_REVISION`, for `ConvertStringSecurityDescriptorToSecurityDescriptorW`.
SDDL_REVISION_1 = 1

#: Well-known SDDL trustee strings: SYSTEM and Builtin Administrators. The third trustee is the
#: current user's SID and is *not* a literal here — see `_build_sddl`.
_SDDL_SYSTEM = "SY"
_SDDL_ADMINISTRATORS = "BA"
#: `A` = `ACCESS_ALLOWED_ACE_TYPE` and `GA` = `GENERIC_ALL`, the two halves of one SDDL ACE:
#: ``(A;;GA;;;SY)``. They are separate constants because they are separate fields of the ACE — an ACE
#: string without its type is not a shorter ACE, it is a different (and rejected) grammar, which is
#: what this module produced until a probe caught it: `ConvertStringSecurityDescriptorToSecurityDescriptorW`
#: answers ``err=87`` for ``D:(GA;;;SY)`` and accepts ``D:(A;;GA;;;SY)``.
#:
#: A narrower mask than `GA` would spell out a permission set the server's own reads and writes have
#: to fit into exactly, and a mismatch there fails closed in a way that looks like a broken machine.
_SDDL_ACE_TYPE = "A"
_SDDL_GRANT = "GA"

#: A no-write-up mandatory label: a lower-integrity caller of the **same user** can still open the
#: pipe and read, and cannot write. The user's own token is normally `medium` and the DACL above is
#: what does the work; this only keeps a `low`-integrity (sandboxed) same-user process from driving
#: the server. It is a *label*, not a privilege.
_SDDL_MANDATORY_LABEL = "S:(ML;;NW;;;ME)"

#: `common.$defs.evidence.detail` is `maxLength: 2048`, and a Windows message can be longer.
MAX_DETAIL = 2048
_TRUNCATION_MARKER = " ... [truncated]"

#: `common.$defs.id`, which both `request_id` fields are constrained to. The server echoes the
#: request's id back, so a response can only be built for a request whose id already has this shape.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")

#: A SID in `S-1-...` form. A response travels back to a caller, so nothing that matches may reach
#: one (AGENTS.md §9); :func:`_redact` is the single place that rule is applied.
_SID_PATTERN = re.compile(r"S-1-[0-9-]+")


def _truncate(text: str) -> str:
    if len(text) > MAX_DETAIL:
        return text[: MAX_DETAIL - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return text


def evidence_object(kind: str, detail: Any, *, path: str | None = None) -> dict[str, Any]:
    """One `common.$defs.evidence` item — `{kind, detail}` plus an optional `path`.

    The object shape, not the error envelope's list of strings: `broker/protocol.py` documents that
    trap and `cli/tests/fake_broker.py` has the same helper for the same reason. Built here rather
    than imported from the test harness, because a module under `airoot/` may not import the tests.
    """

    item: dict[str, Any] = {"kind": kind, "detail": _truncate(str(detail))}
    if path is not None:
        item["path"] = _truncate(str(path))
    return item


def _redact(line: str) -> str:
    """Replace every SID in ``line`` with the fact that one was there, by count.

    The refusals this server relays are full of real SIDs: `policy._refuse` names the observed
    identity on purpose, because a refusal that hid which identity was compared could not be
    re-examined (its own docstring says so). A *response document* is a different place — AGENTS.md §9
    forbids machine fingerprints in anything that travels, and `fake_broker.probe_root` set the
    posture by reporting observed trustees as a count. So the redaction happens exactly once, here,
    where an `AirootError` becomes evidence.
    """

    if not _SID_PATTERN.search(line):
        return line
    count = len(_SID_PATTERN.findall(line))
    return _SID_PATTERN.sub(f"<S-1-... withheld; {count} SID(s) observed>", line)


# --------------------------------------------------------------------------------------------- #
# kernel32 / advapi32, fully declared
# --------------------------------------------------------------------------------------------- #


class SECURITY_ATTRIBUTES(ctypes.Structure):
    """``SECURITY_ATTRIBUTES`` (`winnt.h`) — only the three members `CreateNamedPipeW` reads."""

    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _load_libraries() -> tuple[Any, Any]:
    """`advapi32`/`kernel32`, or two ``None``s off Windows — the shape `caps/acl.py` uses."""

    if os.name != "nt":  # pragma: no cover - v1 is a Windows provider
        return None, None
    return (
        ctypes.WinDLL("advapi32", use_last_error=True),
        ctypes.WinDLL("kernel32", use_last_error=True),
    )


def _declare_signatures(kernel32: Any, advapi32: Any) -> None:
    """Pin every entry point's argument and return types.

    Not decoration: ctypes defaults an unannotated parameter to C ``int``, so a 64-bit ``HANDLE``
    arrives truncated and the call dies with an ``OverflowError`` raised inside ctypes instead of with
    the status it should have reported. `caps/identity.py` documents the same trap for the token APIs;
    this is its pipe-shaped twin. ``CreateNamedPipeW`` also *returns* a handle, so an undeclared
    ``restype`` would truncate the result on 64-bit — the failure the brief names.
    """

    kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
    ]
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
    kernel32.GetNamedPipeClientProcessId.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.ULONG),
    ]
    kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    # Local-ness is a *precondition* of believing the peer's pid, not a nicety: a loopback-SMB client
    # reports 65279, which names no local process (measured), and if that number collided with a live
    # local pid the admission decision would be made about an unrelated process.
    kernel32.GetNamedPipeClientComputerNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.ULONG,
    ]
    kernel32.GetNamedPipeClientComputerNameW.restype = wintypes.BOOL
    # `GetNamedPipeInfo` is how the local-only bit becomes *observable* rather than intended: the
    # third out-parameter is the flags word the OS actually gave the instance. Without it, "we passed
    # PIPE_REJECT_REMOTE_CLIENTS" would be a claim about this module's own argument list.
    kernel32.GetNamedPipeInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetNamedPipeInfo.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.restype = wintypes.HANDLE

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    # Reading the DACL back off the server's own handle. Two reasons it is this call and not
    # `GetNamedSecurityInfoW` on the name: a pipe *name* is not a filesystem object this API can open
    # (`GetNamedSecurityInfoW` answers `rc=161 ERROR_BAD_PATHNAME` on a pipe name — measured), and the
    # question being asked is about the instance this server holds, so the handle is the right object.
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD


def _invalid_handle(handle: Any) -> bool:
    """True for a null handle or `INVALID_HANDLE_VALUE` (``-1`` as a pointer).

    One predicate, because comparing a handle to ``-1`` directly is the bug this avoids: ctypes hands
    a failed `CreateNamedPipeW` back as the *unsigned* 64-bit all-ones value.
    """

    if not handle:
        return True
    return int(handle) == (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1


def _status_message(status: int) -> str:
    """``err=87 (The parameter is incorrect.)`` — the number *and* the OS's own text."""

    try:
        text = ctypes.FormatError(status).strip()
    except Exception:  # pragma: no cover - defensive: FormatError is best-effort
        return f"err={status}"
    return f"err={status} ({text})" if text else f"err={status}"


# --------------------------------------------------------------------------------------------- #
# the pipe's DACL, built from the observed identity rather than from a literal
# --------------------------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SecurityDescriptor:
    """A converted SDDL string, with the allocation `LocalFree` has to release."""

    sddl: str
    pointer: ctypes.c_void_p
    size: int
    trustees: tuple[str, ...]


def _build_sddl(sid: str) -> str:
    """The SDDL this server asks for: SYSTEM, Builtin Administrators and ``sid``, and no one else.

    ``sid`` comes from `probe_identity()` — the current process's own token — so nothing here is
    hard-coded. The third trustee is the same-user bypass the mode is named after; see the module
    docstring for why it is written down rather than narrowed.
    """

    return (
        f"D:({_SDDL_ACE_TYPE};;{_SDDL_GRANT};;;{_SDDL_SYSTEM})"
        f"({_SDDL_ACE_TYPE};;{_SDDL_GRANT};;;{_SDDL_ADMINISTRATORS})"
        f"({_SDDL_ACE_TYPE};;{_SDDL_GRANT};;;{sid})"
        f"{_SDDL_MANDATORY_LABEL}"
    )


def _convert_sddl(advapi32: Any, sddl: str) -> _SecurityDescriptor:
    """`ConvertStringSecurityDescriptorToSecurityDescriptorW` over ``sddl``.

    A failure here is `SELF_VALIDATION_FAILED` (exit 8), not a refusal: the SDDL is built by this
    build from this build's own facts, so a string the OS will not parse is a defect in *this module*
    — the same rule that makes a schema-rejected response a defect rather than a verdict.
    """

    pointer = ctypes.c_void_p()
    size = wintypes.ULONG()
    ok = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(pointer), ctypes.byref(size)
    )
    if not ok or not pointer:
        raise AirootError(
            "SELF_VALIDATION_FAILED",
            "the pipe's SDDL was refused by "
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            evidence=[
                f"the SDDL this build built: {sddl}",
                f"the call failed: {_status_message(ctypes.get_last_error() or 0)}",
                "this is a defect in broker/pipe.py's own security descriptor, not a verdict about "
                "a caller",
            ],
        )
    return _SecurityDescriptor(
        sddl=sddl,
        pointer=pointer,
        size=int(size.value),
        trustees=(_SDDL_SYSTEM, _SDDL_ADMINISTRATORS, "the current user's SID"),
    )


# --------------------------------------------------------------------------------------------- #
# what this build can and cannot honour, as data with reasons
# --------------------------------------------------------------------------------------------- #

#: One line of *why* per operation this build refuses. `probe_root` is deliberately **absent**, and
#: its absence is the statement that this build answers it.
REFUSAL_REASONS: dict[str, str] = {
    "commit_plan": (
        "a commit writes Zone R and moves the active binding at ACTIVE_BOUND, which needs both the "
        "elevated broker and the approval issuer ADR-0025's D1 leaves to P2 — the pipe is real but "
        "the process behind it is not, and a same-user server that changed a binding would be "
        "claiming an authority its own DACL does not give it"
    ),
    "recover_transaction": (
        "a recovery reconciles a journal by acting on it (docs/broker §8), so it writes the same "
        "protected state a commit does and is refused for the same reason: nothing in this build may "
        "act on a transaction, only a protected broker may"
    ),
    "gc_apply": (
        "a gc apply deletes a payload after consuming an approval token; the deletion grading is "
        "implemented (caps/lifecycle.py) but the approval issuer that could authorise it is not "
        "(ADR-0025's D1), and a deletion cannot be taken back by a later stage"
    ),
}

#: ``operation -> "answer" | "refuse"``. The single decision the routing loop reads. It is derived
#: from `REFUSAL_REASONS` so the two cannot disagree, and `test_l1_broker_pipe.py` holds the key set
#: equal to the operations the published request schema enumerates — an operation added to the schema
#: and forgotten here shows up as a test failure rather than as an unanswerable request.
ROUTING: dict[str, str] = {
    "probe_root": "answer",
    **{operation: "refuse" for operation in REFUSAL_REASONS},
}

#: The operations this build answers, for a caller that wants to ask without parsing `ROUTING`.
HONOURED_OPERATIONS: tuple[str, ...] = tuple(
    operation for operation, decision in ROUTING.items() if decision == "answer"
)


# --------------------------------------------------------------------------------------------- #
# the framing: `transport`'s, driven by a `read(n)` shim over `ReadFile`
# --------------------------------------------------------------------------------------------- #


def _framing() -> Any:
    """`broker/transport.py` itself.

    Looked up by name at call time so this module can be imported — and its constants and failure
    paths exercised — even when the framing sibling has not landed. The interface is frozen
    (``FRAME_HEADER_BYTES = 4``, ``encode_frame``/``read_frame``/``decode_frame``, all failures
    `AirootError("INVALID_INPUT", ...)`), and re-implementing it here is the one thing forbidden.
    """

    return transport


def _read_file_shim(kernel32: Any, handle: Any) -> Callable[[int], bytes]:
    """A ``read(n)`` over `ReadFile`, which is what `transport.read_frame` is declared against.

    Bytes come back exactly as the pipe delivered them; a short read is the caller's business
    (`read_frame` accumulates until it has a whole frame or runs out). A failed read returns ``b""``
    rather than raising — the peer went away — so the frame reader reports an incomplete frame instead
    of this module raising out of a server loop that promises a verdict.
    """

    def read(count: int) -> bytes:
        buffer = ctypes.create_string_buffer(count)
        got = wintypes.DWORD(0)
        if not kernel32.ReadFile(handle, buffer, count, ctypes.byref(got), None):
            return b""
        return buffer.raw[: int(got.value)]

    return read


def _write_frame(kernel32: Any, handle: Any, document: dict) -> None:
    """Frame ``document`` with `transport.encode_frame`, write every byte, and **flush**.

    The flush is not belt-and-braces, and it was found by measurement rather than reasoning. Without
    it the answer is silently truncated: the server writes the frame, `CloseHandle` discards whatever
    a *byte*-mode pipe still holds, and the client receives the 4 header bytes and then
    ``ERROR_PIPE_NOT_CONNECTED`` (233) on the body — a response that looks like a transport failure
    for a request that was answered. Instrumented from inside, the same call with `FlushFileBuffers`
    before the handle is closed delivers all 1019 payload bytes. `caps/search.py`'s stream writer makes
    the same call for the same reason.
    """

    payload = _framing().encode_frame(document)
    offset = 0
    while offset < len(payload):
        chunk = payload[offset:]
        buffer = ctypes.create_string_buffer(chunk, len(chunk))
        written = wintypes.DWORD(0)
        if not kernel32.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
            raise AirootError(
                "INVALID_INPUT",
                "the response could not be written to the pipe",
                evidence=[f"WriteFile failed: {_status_message(ctypes.get_last_error() or 0)}"],
            )
        if not written.value:
            raise AirootError(
                "INVALID_INPUT",
                "WriteFile reported success but wrote nothing",
                evidence=["the pipe is not accepting bytes"],
            )
        offset += int(written.value)
    # Blocks until the peer has read what was written, which is what makes the bytes survive the
    # `CloseHandle` that follows: a byte-mode pipe drops anything still buffered when it is closed.
    kernel32.FlushFileBuffers(handle)


# --------------------------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------------------------- #


def _status_for(reason_code: str) -> str:
    """Which published status a reason code expresses.

    Read off the exit code for the success case first so it cannot be forgotten, then everything else
    is a refusal. Deliberately narrower than `cli/tests/fake_broker.py`'s classifier: this server runs
    at most one operation, so the only statuses it can honestly emit are ``ok`` and ``rejected``, and
    the ``recovery_required`` status that harness needs for an interrupted commit has no meaning here
    — this server has no transactions to be mid-flight in.
    """

    if REASON_EXIT.get(reason_code) == EXIT_SUCCESS:
        return "ok"
    return "rejected"


def _answer(
    *,
    request_id: str,
    reason_code: str,
    transaction_id: str | None = None,
    state: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Assemble a `broker-response`, **mark it, and validate it before it goes on the wire.**

    The mode marking is applied here and nowhere else, unconditionally, through `posture()` — the
    build's one definition of the two fields, shared with `caps/doctor.py` and `ext/envelope.py`
    rather than copied from them. Success and refusal both come through this function, so "every
    response carries the marking" is a property of there being one constructor rather than of two
    call sites remembering.

    `validate_self` (not `validate_document`) because the document is this build's own output: a
    response the published schema refuses is a defect here, exactly as `broker/protocol.py` and
    `fake_broker.py` treat it.
    """

    from .. import schema_io

    document: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "status": _status_for(reason_code),
        "transaction_id": transaction_id,
        "state": state,
        "reason_code": reason_code,
        "evidence": list(evidence or []),
        "retryable": retryable,
    }
    document.update(posture())
    schema_io.validate_self("broker-response", document)
    return document


def _refusal(
    request_id: str,
    error: AirootError,
    *,
    redact: bool,
    transaction_id: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """An `AirootError` as a response document — the verdict the caller gets instead of a silence.

    ``redact`` is set for a refusal that quotes an *observation* (the admission decision's), because
    those evidence lines name real SIDs; see :func:`_redact`. A refusal this module composes itself —
    an unimplemented operation, a malformed request — has no SIDs to hide, and passing it through the
    redactor anyway would be a claim that it might have.
    """

    render = _redact if redact else (lambda line: line)
    evidence = [evidence_object("reason", render(error.message))]
    evidence += [evidence_object("evidence", render(line)) for line in error.evidence]
    for key, value in sorted((error.details or {}).items()):
        if isinstance(value, (str, int, float, bool)) or value is None:
            evidence.append(evidence_object("detail", f"{key}={value}"))
    return _answer(
        request_id=request_id,
        reason_code=error.reason_code,
        transaction_id=transaction_id,
        state=state,
        evidence=evidence,
    )


def _operation_refusal(operation: str) -> AirootError:
    """`NOT_IMPLEMENTED` (exit 1) plus ADR-0025's pointer, for an operation this build defers.

    The code and the pointer come from `protocol.broker_unavailable`, the rest of the build's one
    answer to "ask the broker", so a reader who has seen that function recognises this one. The
    operation-specific *why* is appended from `REFUSAL_REASONS`, and the message is **not** copied
    verbatim from `broker_unavailable`: its sentence says "no protected broker exists in this build",
    which is now only half true — a pipe and a same-user server do exist, and what is missing is the
    elevation and the issuer behind them. A refusal repeating the older sentence would be this build
    describing a thing it has as a thing it does not.
    """

    base = protocol.broker_unavailable(operation)
    return AirootError(
        base.reason_code,
        f"{operation} is refused by the user compatibility mode's server: "
        f"{REFUSAL_REASONS[operation]} "
        "(decided: ADR-0025 leaves the production approval issuer and the elevated broker to P2)",
        evidence=[
            f"operation: {operation}",
            f"security_mode: {SECURITY_MODE}, enforcement: {enforcement_for(SECURITY_MODE)} — this "
            "pipe is served by a same-user process, so nothing here could enforce what the operation "
            "needs",
            f"why this operation is not honoured: {REFUSAL_REASONS[operation]}",
            "ADR-0025 (D1) keeps the production issuer and the elevated broker waiting for P2",
        ],
        details={"operation": operation, "adr": "ADR-0025"},
    )


# --------------------------------------------------------------------------------------------- #
# the one operation this build honours
# --------------------------------------------------------------------------------------------- #


def _probe_root(root: Any) -> list[dict[str, Any]]:
    """`probe_root`'s answer: a success verdict plus evidence naming what was observed.

    Read-only twice over — `root.open_root` re-reads the root marker and its volume identity, and
    `caps/acl.capture_acl` observes the directory's owner and DACL — and neither writes nor decides.

    **Deliberately nothing that varies.** No SID, no owner name, no entry count, no DACL digest, no
    absolute path, no timestamp: this answer is the one the golden fixture is generated from, and
    `test_golden_fixtures_reproduce_exactly` regenerates it on every machine, so anything derived from
    runtime state would make that fixture unreproducible and anything derived from this machine would
    be a fingerprint travelling back to a caller (AGENTS.md §9). The cost is real and is stated in the
    module docstring: a caller learns *that* the probe succeeded and what kind of thing was probed,
    not the probe's values. The in-process harness (`cli/tests/fake_broker.py`) carries the counts
    because it never reaches a wire; this surface is stricter.

    The one exception is `root_instance_id`, which is a root's own declared identity — created by the
    fixture, stable across runs, and the fact `docs/broker §4` step 6 says a caller compares — not a
    machine fingerprint.

    A root that is not there, a marker that drifted and a volume identity that moved all raise
    `AirootError` from `open_root`, which the caller turns into a refusal document.
    """

    from .. import root as root_module
    from ..caps import acl

    if root is None:
        info = root_module.open_root(env=None)
    elif isinstance(root, root_module.RootInfo):
        # Re-read the marker and the volume rather than trusting the object: "the root is what it says
        # it is" is exactly the check this operation exists to make.
        info = root_module.open_root(root.path)
    else:
        info = root_module.open_root(root)

    snapshot = acl.capture_acl(info.path)
    evidence = [
        evidence_object("root", f"root_instance_id={info.root_instance_id}"),
        evidence_object("capability", "opened through root.open_root with the volume guard enabled"),
        evidence_object(
            "acl",
            "the root directory's owner and DACL were observed with caps/acl.capture_acl",
        ),
    ]
    if snapshot.reason:
        # Failure is data (`caps/acl.py`): an unreadable DACL is a reason, never "empty". The reason
        # is redacted because a Windows status message can quote a name.
        evidence.append(evidence_object("acl", _redact(f"the DACL could not be read: {snapshot.reason}")))
    else:
        evidence.append(
            evidence_object(
                "acl",
                "the observation is not carried in this document: the trustee SIDs and the entry "
                "count are machine facts, and a response is not the place for an identity "
                "(AGENTS.md §9)",
            )
        )
    return evidence


# --------------------------------------------------------------------------------------------- #
# the server loop
# --------------------------------------------------------------------------------------------- #

#: Test-only seam, empty in every real run. It exists because the `ERROR_PIPE_CONNECTED` race is
#: otherwise *unwinnable on purpose*: between `CreateNamedPipeW` and `ConnectNamedPipe` the server
#: does almost nothing, so a client cannot be relied on to connect first. Two keys, both read at the
#: point they widen rather than somewhere else:
#:
#: * ``before_connect_pause_s`` — sleep inserted **inside** the window, so the client wins the race
#:   against a real `ConnectNamedPipe` that really returns `ERROR_PIPE_CONNECTED`. The branch under
#:   test is untouched.
#: * ``race_counter`` — a one-element list the server appends to when the race status is seen, which
#:   is how the test observes a branch whose whole content is "this status is not a failure". Nothing
#:   in production sets either key, and `serve_pipe`'s report carries the count it produced.
_TEST_HOOKS: dict[str, Any] = {}


@dataclass
class _Report:
    """The counts `serve_pipe` returns, accumulated by the loop that produces them."""

    pipe_name: str
    sddl: str = ""
    trustees: tuple[str, ...] = ()
    requests: int = 0
    admitted: int = 0
    refused: int = 0
    #: Frames that were **not a request at all**: nothing arrived, or the bytes did not decode to a
    #: JSON object. Kept apart from `refused`, which counts *requests that were answered* with a
    #: refusal — the two are different facts, and together they give the report one clean invariant:
    #: ``requests == admitted + refused``.
    malformed: int = 0
    #: Connections that ended in a failure of the stream itself (`OSError` out of the framing layer)
    #: rather than in a verdict about a document. Kept apart from `malformed` because they are
    #: different facts: a malformed frame is the caller's bytes being wrong and is answered, while a
    #: broken stream is nobody's document and cannot be answered at all.
    connection_errors: int = 0
    connect_races: int = 0
    stopped: bool = False
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    #: The flags word `GetNamedPipeInfo` read back from the instance the OS created — the **OS's**
    #: answer about the local-only bit, not this build's intention. `None` when no instance was ever
    #: created (a stop before the first connection) or the query failed.
    os_pipe_flags: int | None = None

    def rejects_remote(self) -> bool | None:
        """Whether the OS took the local-only bit, or ``None`` when no instance was observed."""

        if self.os_pipe_flags is None:
            return None
        return bool(self.os_pipe_flags & PIPE_REJECT_REMOTE_CLIENTS)

    def to_document(self) -> dict[str, Any]:
        return {
            "pipe_name": self.pipe_name,
            "security_mode": SECURITY_MODE,
            "enforcement": posture()["enforcement"],
            "sddl": self.sddl,
            "trustees": list(self.trustees),
            "os_pipe_flags": self.os_pipe_flags,
            "rejects_remote_clients": self.rejects_remote(),
            "requests": self.requests,
            "admitted": self.admitted,
            "refused": self.refused,
            "malformed": self.malformed,
            "connection_errors": self.connection_errors,
            "connect_races": self.connect_races,
            "stopped": self.stopped,
            "error": self.error,
            "notes": list(self.notes),
        }


def _create_pipe(kernel32: Any, name: str, descriptor: _SecurityDescriptor) -> Any:
    """`CreateNamedPipeW` for one instance, returning the handle or raising a defect.

    ``dwOpenMode`` is `PIPE_ACCESS_DUPLEX` alone and ``dwPipeMode`` carries
    `PIPE_REJECT_REMOTE_CLIENTS`; see the module docstring for the measurement behind that, which
    contradicts the brief. A failure here is this build's own defect — the attributes are its own —
    and is reported as one, with the SDDL it asked for so a reader can see the request.
    """

    attributes = SECURITY_ATTRIBUTES(
        nLength=ctypes.sizeof(SECURITY_ATTRIBUTES),
        lpSecurityDescriptor=descriptor.pointer,
        bInheritHandle=False,
    )
    handle = kernel32.CreateNamedPipeW(
        name,
        PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_MODE,
        PIPE_UNLIMITED_INSTANCES,
        65536,
        65536,
        0,
        ctypes.byref(attributes),
    )
    if _invalid_handle(handle):
        status = ctypes.get_last_error() or 0
        # `ERROR_ACCESS_DENIED` (5) is what `FILE_FLAG_FIRST_PIPE_INSTANCE` answers when the name is
        # already held by another process. That is **fail closed**, and deliberately not a retry: a
        # named pipe's name is not exclusive (a later process creating a *plain* instance of the same
        # name succeeds, and names are case-insensitive in both the leaf and the `\pipe\` component),
        # so a name somebody else holds is a name this server cannot promise is its own — and the
        # honest thing is to refuse to start rather than to answer callers who may be talking to
        # somebody else. See the module docstring for what that means for the mode.
        #
        # The code is `PRIVILEGE_REQUIRED` and **not** `ACL_MISMATCH`, which was this line's first
        # word: err 5 here is about *this process not being allowed to take the name*, while
        # `ACL_MISMATCH` speaks about a directory whose ACL drifted from its baseline — a different
        # subject, and one this build still has no writer for (`references/reason-codes.md` §《这一版
        # 发不出来的码》). Borrowing a code because it shares an exit number is how a code table stops
        # meaning anything. Elevating is also not the remedy, so the evidence says what the remedy is
        # rather than repeating the code's usual next step.
        kind = "PRIVILEGE_REQUIRED" if status == 5 else "SELF_VALIDATION_FAILED"
        raise AirootError(
            kind,
            f"CreateNamedPipeW failed for {name}",
            evidence=[
                _status_message(status),
                f"dwOpenMode=0x{PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE:08x} "
                f"dwPipeMode=0x{PIPE_MODE:08x}",
                f"the SDDL this build asked for: {descriptor.sddl}",
                "PIPE_REJECT_REMOTE_CLIENTS (0x00000008) belongs in dwPipeMode: this machine rejects "
                "it with err=87 when it is passed in dwOpenMode",
            ]
            + (
                [
                    "FILE_FLAG_FIRST_PIPE_INSTANCE reports ERROR_ACCESS_DENIED (5) when the name is "
                    "already held; this server fails closed rather than share a name it cannot claim, "
                    "because a pipe name is not exclusive against a same-user process",
                    "the remedy is a name nobody else holds, not elevation: the token has the "
                    "privilege to create the pipe, and what it does not have is a name it can call "
                    "its own — which no privilege can grant",
                ]
                if status == 5
                else []
            ),
        )
    return handle


def _client_is_local(kernel32: Any, handle: Any) -> tuple[bool | None, str]:
    """Whether the connected peer is on this machine, with a line of evidence either way.

    **This runs before anything is observed about the caller, and a non-local peer is refused without
    being observed at all.** The reason is that a remote peer's pid is not a local pid and cannot be
    believed: a loopback-SMB client makes `GetNamedPipeClientProcessId` answer ``65279``, which names
    no process on this machine (`OpenProcess(65279)` → err 87). A server that fed that number to
    `probe_process` would get "unknown" — the safe answer — but if the number ever collided with a
    live local pid, the admission decision would be made about an **unrelated local process**, which
    is the dangerous direction. So the local-ness test is a precondition, not a second opinion:
    `GetNamedPipeClientComputerNameW` answers FALSE with `ERROR_PIPE_LOCAL` (229) for a local peer and
    TRUE with a non-empty computer name for a remote one (measured for a loopback-SMB peer).

    `PIPE_REJECT_REMOTE_CLIENTS` is the OS's own gate and is expected to have refused such a client
    already (err 5). This is deliberately a *second*, independent check: the pipe mode is a flag this
    build sets, while this is an observation of the peer it actually got. ``None`` means the question
    could not be put — which is refused, because "could not look" is not "looked, and it was local".
    """

    buffer = ctypes.create_unicode_buffer(256)
    ok = kernel32.GetNamedPipeClientComputerNameW(handle, buffer, len(buffer))
    status = ctypes.get_last_error() or 0
    if ok:
        name = buffer.value
        if name:
            return False, f"the peer identified itself as {name!r}, which is not this machine"
        # TRUE with an empty name is not a documented answer; treat it as "could not look".
        return None, "GetNamedPipeClientComputerNameW succeeded but named no computer"
    if status == ERROR_PIPE_LOCAL:
        return True, "GetNamedPipeClientComputerNameW answered ERROR_PIPE_LOCAL (229), so the peer is on this machine"
    return None, f"GetNamedPipeClientComputerNameW failed: {_status_message(status)}"


def _connect(kernel32: Any, handle: Any) -> int:
    """`ConnectNamedPipe`; returns ``0`` when a client is connected, else the Windows status.

    **`ERROR_PIPE_CONNECTED` (535) is success** — a client connected between `CreateNamedPipeW` and
    this call, so the race the brief names is won by the client, and reading it as a failure would
    drop that caller and make the server answer only every second request.

    **Nothing else is success**, and `ERROR_NO_DATA` (232) is the trap: it is what this call answers
    when the peer connected and then *disconnected* before we got here (measured twice by the
    measurement agent). A server that treated any FALSE as "already connected" would go on to serve a
    peer that is already gone. So the branch is on the status, not on the return value.
    """

    pause = float(_TEST_HOOKS.get("before_connect_pause_s") or 0.0)
    if pause:
        time.sleep(pause)
    if kernel32.ConnectNamedPipe(handle, None):
        return 0
    status = ctypes.get_last_error() or 0
    if status == ERROR_PIPE_CONNECTED:
        counter = _TEST_HOOKS.get("race_counter")
        if counter is not None:
            counter.append(1)
        return 0
    return status


def _pipe_flags(kernel32: Any, handle: Any) -> int | None:
    """The flags word `GetNamedPipeInfo` reports for ``handle``, or ``None`` if it will not answer.

    This is what turns the local-only bit from an intention into an observation. MSDN lists
    `PIPE_REJECT_REMOTE_CLIENTS` under ``dwOpenMode`` and `winbase.h` groups it under ``dwPipeMode``;
    the header and this build's own measurement agree that only the second position is accepted (see
    the module docstring), and `report["os_pipe_flags"]` carries this number so a reader can check the
    OS's answer rather than this module's argument list.
    """

    flags = wintypes.DWORD(0)
    out_buffer = wintypes.DWORD(0)
    in_buffer = wintypes.DWORD(0)
    max_instances = wintypes.DWORD(0)
    ok = kernel32.GetNamedPipeInfo(
        handle,
        ctypes.byref(flags),
        ctypes.byref(out_buffer),
        ctypes.byref(in_buffer),
        ctypes.byref(max_instances),
    )
    return int(flags.value) if ok else None


def _client_pid(kernel32: Any, handle: Any) -> tuple[int | None, int]:
    """The peer's pid, from the OS's own record of *this* handle's client.

    This is the one fact the request cannot influence. `GetNamedPipeClientProcessId` asks the kernel
    which process actually connected; the request's `client.pid` is a number the caller typed and is
    never read. A failure returns ``None`` — "nobody could look" — which the decision refuses as
    `observation_incomplete` rather than treating as an anonymous caller.
    """

    pid = wintypes.ULONG()
    if not kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        return None, ctypes.get_last_error() or 0
    return int(pid.value), 0


def _decision_facts(pid: int | None) -> identity.ProcessIdentity:
    """The observation `admit_caller` decides on: `probe_process` of the OS-reported pid.

    The request is not an input and cannot become one: this function has no parameter to put one in,
    and `ProcessIdentity` has no `application_id` field for a claimed one to land in. That is
    `policy.py`'s structural guarantee restated at the call site rather than trusted.

    A pid of ``None`` — `GetNamedPipeClientProcessId` failed — becomes an identity whose every fact is
    ``None``, which `admit_caller` refuses as `observation_incomplete`. That is the observation
    "nobody could look", not a caller, and pid ``0`` names no process on Windows.
    """

    if pid is None:
        return identity.ProcessIdentity(pid=0, sid=None, integrity=None, elevated=None)
    return identity.probe_process(pid)


def _admission_refusal(request_id: str, error: AirootError) -> dict[str, Any]:
    """The verdict a caller that cannot be admitted gets — a document, never a silent close.

    The reply *shape* is how the caller learns what happened: §112 built the response corpus for
    exactly this path, and `protocol.parse_response` raises with the response's **own** code
    (`CALLER_NOT_AUTHORIZED`). Closing the pipe instead would leave the caller with a broken pipe and
    no reason, which is the failure mode the brief names.

    Nothing identifying goes in. `_refusal(..., redact=True)` withholds the observation's SIDs, and
    the evidence counts what the refusal rested on without saying which identity it was.
    """

    decision = _refusal(request_id, error, redact=True)
    decision["evidence"] = [
        evidence_object(
            "caller",
            "the peer's pid came from GetNamedPipeClientProcessId on the connected handle, and its "
            "facts from caps/identity.probe_process(pid); the request's `client` block was not read",
        ),
        evidence_object(
            "caller",
            "the observed SID is withheld because it is a machine fingerprint that would travel back "
            "to a caller (AGENTS.md §9); the rule that refused is named in `details.rule` below",
        ),
    ] + list(decision["evidence"])
    # `_refusal` validated its own document and this one differs from it, so it is checked again
    # rather than assumed to still hold.
    from .. import schema_io

    schema_io.validate_self("broker-response", decision)
    return decision


def _answer_request(
    request: dict,
    *,
    peer_pid: int | None,
    root: Any,
    expectation: CallerExpectation,
) -> tuple[dict, bool]:
    """Answer one decoded request. Returns ``(response, admitted)``.

    The order is the contract: the caller is admitted **before** the operation is looked at, so an
    unauthorised caller cannot learn from a refusal which operations this build honours. The operation
    is then routed through `ROUTING`, and only the honoured ones run.

    One thing the caller influences is echoed and nothing else: `request_id`, which the response
    schema requires and which the caller must be able to match an answer to. The `client` block is not
    echoed, not compared and not stored.
    """

    request_id = request["request_id"]
    observed = _decision_facts(peer_pid)
    try:
        admit_caller(observed, expectation)
    except AirootError as error:
        return _admission_refusal(request_id, error), False

    operation = request["operation"]
    if ROUTING.get(operation) == "refuse":
        return _refusal(request_id, _operation_refusal(operation), redact=False), True

    try:
        evidence = _probe_root(root)
    except AirootError as error:
        # A root that is not there, a marker that drifted, a volume identity that moved: all of them
        # are verdicts about the request, so they travel back as documents like every other refusal.
        return _refusal(request_id, error, redact=False), True
    evidence += [
        evidence_object(
            "caller",
            "the caller was admitted on the token the server observed for the connected peer; the "
            "request's `client` block played no part in the decision",
        ),
        evidence_object(
            "security_mode",
            f"security_mode={SECURITY_MODE}, enforcement={posture()['enforcement']}: a same-user "
            "process can bypass every check this server makes, so nothing here is enforced",
        ),
    ]
    return (
        _answer(
            request_id=request_id,
            reason_code="SUCCESS",
            transaction_id=None,
            state=None,
            evidence=evidence,
        ),
        True,
    )


def _serve_connection(
    kernel32: Any,
    handle: Any,
    *,
    root: Any,
    expectation: CallerExpectation,
    report: _Report,
) -> None:
    """Observe the peer, read one frame, answer it, and never raise out of the loop.

    Every failure mode the brief names is handled here, and each differently, because they are
    different facts:

    * the peer's pid cannot be read — the decision refuses as `observation_incomplete`, and that
      refusal *is* the answer;
    * **the peer is not local** — refused before anything is observed about it, because a remote
      peer's pid names no process on this machine and a collision would attribute the call to an
      unrelated local process (:func:`_client_is_local`);
    * the frame is malformed (`transport.decode_frame`/`read_frame` speak `INVALID_INPUT`) — the
      caller is told, because a closed pipe with no reason is not a verdict;
    * the caller is not admitted — a `CALLER_NOT_AUTHORIZED` document goes back;
    * the response cannot be written — the caller is gone, and there is nobody to tell.
    """

    framing = _framing()

    # Local-ness first, and before any frame is read: a remote peer's pid is not a local pid and must
    # not be believed, so there is nothing to admit such a caller on. Refused with a real code and a
    # real reason rather than observed, and counted as a refusal.
    local, local_evidence = _client_is_local(kernel32, handle)
    if local is not True:
        report.requests += 1
        report.refused += 1
        _try_write(
            kernel32,
            handle,
            _refusal(
                _fresh_request_id(),
                AirootError(
                    "CALLER_NOT_AUTHORIZED",
                    "the peer is not a local client, so its pid is not a pid this machine can believe",
                    evidence=[
                        local_evidence,
                        "PRIVILEGE_REQUIRED is not the answer: no privilege of the caller's would "
                        "make a remote pid name a local process",
                        "docs/broker §3 admits only local clients, which PIPE_REJECT_REMOTE_CLIENTS "
                        "is asked to enforce; this is the independent observation of the same rule",
                    ],
                    details={"rule": "peer_not_local"},
                ),
                redact=True,
            ),
        )
        return

    pid, pid_status = _client_pid(kernel32, handle)
    if pid is None:
        report.error = f"GetNamedPipeClientProcessId failed: {_status_message(pid_status)}"

    try:
        payload = framing.read_frame(_read_file_shim(kernel32, handle))
        request = None if payload is None else framing.decode_frame(payload)
    except AirootError as error:
        # A malformed frame leaves no request to answer, so `request_id` cannot be echoed. The
        # response schema requires one, so a fresh conforming id is used and the evidence says why —
        # the alternative is an invalid document or a silent close, and both are worse.
        report.malformed += 1
        report.refused += 1
        _try_write(kernel32, handle, _refusal(_fresh_request_id(), error, redact=False))
        return
    except OSError as error:
        # The framing layer lets a failure of the *stream* propagate unchanged, on purpose: the
        # frozen reason table has no word for "the IPC stream failed" and `INVALID_INPUT` would blame
        # the caller's document for the transport's failure. Deciding what it means is this loop's
        # job, and its decision is "failure is data": the connection is over, nothing is answered,
        # the handles are closed by `serve_pipe`'s `finally`, and the server keeps serving. A
        # transport failure must never escape `serve_pipe` or kill it.
        report.connection_errors += 1
        report.error = f"{error.__class__.__name__} on the pipe stream: {error}"
        return

    if request is None:
        # An empty frame: the peer connected and wrote nothing. Not a request, so not counted as one;
        # recorded as malformed because that is the honest word for a frame that was not one.
        report.malformed += 1
        return

    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not _ID_PATTERN.match(request_id):
        # The response must carry a conforming `request_id` and this request has none, so there is no
        # document that could honestly carry its identity back. It *decoded* to an object, so it is a
        # request (counted) and it is answered with a refusal under a fresh id.
        report.requests += 1
        report.refused += 1
        _try_write(
            kernel32,
            handle,
            _refusal(
                _fresh_request_id(),
                AirootError(
                    "INVALID_INPUT",
                    "the request carries no usable request_id",
                    evidence=[
                        f"request_id is {request_id!r}; common.$defs.id requires "
                        "^[a-z0-9][a-z0-9._/-]{0,127}$",
                        "the answer is sent under a fresh id, because a response must carry one",
                    ],
                ),
                redact=False,
            ),
        )
        return

    report.requests += 1
    response, admitted = _answer_request(
        request, peer_pid=pid, root=root, expectation=expectation
    )
    if admitted:
        report.admitted += 1
    else:
        report.refused += 1
    _try_write(kernel32, handle, response)


def _try_write(kernel32: Any, handle: Any, document: dict) -> None:
    """Write a response, swallowing a failure the caller caused by leaving.

    The one place a write failure is not an answer: the peer disconnected mid-answer, so there is no
    caller to tell. The loop must survive it — one client hanging up is not a reason for the server to
    die — and the failure is not a verdict, so it is not recorded as one.
    """

    try:
        _write_frame(kernel32, handle, document)
    except AirootError:
        return


def _fresh_request_id() -> str:
    """A conforming `request_id` for a response to a request that could not supply one."""

    return f"req/server/{os.urandom(8).hex()}"


def _stop_watcher(kernel32: Any, name: str, stop: threading.Event) -> threading.Thread:
    """A thread that unblocks a server waiting in `ConnectNamedPipe` when ``stop`` is set.

    **Why this exists.** There is no server-side connect timeout: `ConnectNamedPipe` on a
    synchronous handle blocks until a client arrives, so a server that is waiting for its second
    caller can never notice `stop` — the measurement agent's own probe hung on exactly this, and so
    did this module's first one. A pipe server that cannot be shut down when nobody connects is not
    shippable, and "checked between connections" is not an excuse when the wait between connections
    is unbounded.

    The fix is to make the wait *endable* rather than to make it *polled*. The watcher waits on
    ``stop`` and then connects to the server's own pipe once, which is a real local connection and
    therefore a legitimate way to finish the current `ConnectNamedPipe`; the loop then sees ``stop``
    set before it creates another instance. Two properties matter and both are asserted in
    `cli/tests/test_l1_broker_pipe.py`: it produces no request (the connection carries no frame, so
    the loop's per-connection work sees an empty frame and counts nothing), and it is joined before
    `serve_pipe` returns.

    **Why not overlapped I/O**, which is the other standard answer and the one the orchestrator
    suggested: a handle opened `FILE_FLAG_OVERLAPPED` requires an `OVERLAPPED` on **every** operation,
    including `ReadFile` and `WriteFile`. `transport.read_frame`'s contract is a synchronous
    ``read(n)`` that returns partial bytes, and `_read_file_shim` implements exactly that; making the
    handle overlapped would force a completion-port or event dance into the shim for no gain here. The
    watcher keeps I/O synchronous and still guarantees the shutdown, which is what `stop` is for.

    Returns an already-started thread. It is a daemon so a watcher that is still parked in
    `_open_client` can never hold the process open; `serve_pipe` joins it on the way out.
    """

    def watch() -> None:
        if not stop.wait():
            return  # not stopped: nothing to release
        handle, _status = _open_client(kernel32, name, 500)
        if handle is not None:
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=watch, name="airoot-pipe-stop", daemon=True)
    thread.start()
    return thread


def serve_pipe(
    *,
    root: Any,
    expectation: CallerExpectation,
    pipe_name: str = PIPE_NAME,
    max_requests: int = 1,
    stop: threading.Event | None = None,
    clock: Any = None,
) -> dict:
    """Serve ``max_requests`` connections on ``pipe_name``; return a small report.

    Runnable in a thread — the tests drive it that way, with `call_broker` in the same process, which
    makes the observed peer a genuinely different pid as far as the OS is concerned. ``stop`` is a
    `threading.Event`. Setting it ends the loop, and — because `ConnectNamedPipe` on a synchronous
    handle can block with nothing to wait for — a **stop watcher** thread connects to this server once
    when the event is set, which releases that wait. Without it the loop could never observe ``stop``
    on a server that is waiting for a caller that never comes, and a broker that cannot be shut down
    is not shippable (see :func:`_stop_watcher`; `test_stop_releases_a_server_nobody_ever_calls` is
    the test for exactly that case). ``clock`` is accepted for interface symmetry with the rest of the
    build and is **not used**: this server reads no time and stamps nothing — the request's
    ``requested_at`` belongs to the caller, and a server timestamp would make every answer
    unreproducible and would be a fact nobody asked for.

    The report is a count for a test and an operator, not an audit record: docs/broker §8's events
    belong to the protected service. It always carries the **SDDL this build asked for**, so a reader
    sees what the DACL claims instead of trusting a sentence in a docstring.

    Raises `AirootError` only for a defect of this build: off Windows, an unreadable own SID, an
    elevated token (see the module docstring), or a security descriptor the OS refuses. A misbehaving
    *caller* never raises — that is the whole contract of the loop.
    """

    report = _Report(pipe_name=pipe_name)
    advapi32, kernel32 = _load_libraries()
    if kernel32 is None or advapi32 is None:  # pragma: no cover - v1 is a Windows provider
        raise AirootError(
            "EXTENSION_UNAVAILABLE",
            "the named-pipe server needs Windows",
            evidence=["os.name is not 'nt'"],
        )

    _declare_signatures(kernel32, advapi32)

    me = identity.probe_identity()
    if me.sid is None:
        raise AirootError(
            "SELF_VALIDATION_FAILED",
            "the pipe's DACL cannot be built: this process's own SID could not be read",
            evidence=[
                "caps/identity.py's probe_identity() returned no SID, so the third trustee of the "
                "SDDL has no value, and nothing may be hard-coded in its place",
                *me.evidence,
            ],
        )
    if me.elevated is True:
        # A server running from an elevated token cannot honour the mode it is about to claim: the
        # user it would admit is the elevated token's user, and the mode's own sentence — "same user
        # can bypass" — would be describing a different session. No file is created and no pipe is
        # opened; the refusal is the whole behaviour.
        raise AirootError(
            "SELF_VALIDATION_FAILED",
            "the user compatibility mode's server refuses to start from an elevated token",
            evidence=[
                "probe_identity() reports elevated=True, so 'the current user' is the elevated "
                "token's user, and an unprivileged process of the same account could not be told "
                "apart from an administrator",
                "docs/broker §2's compatibility mode is a same-user simulation; starting it elevated "
                "would produce answers whose security_mode claim is not true of the session serving "
                "them",
                "this is a condition of the shell, not a defect to work around: run the server "
                "unprivileged",
            ],
        )

    descriptor = _convert_sddl(advapi32, _build_sddl(me.sid))
    report.sddl = descriptor.sddl
    report.trustees = descriptor.trustees

    try:
        served = 0
        while served < max_requests:
            if stop is not None and stop.is_set():
                report.stopped = True
                break
            handle = _create_pipe(kernel32, pipe_name, descriptor)
            try:
                # Read the OS's own flags word before anyone connects: this is the instance as the
                # kernel built it, and the report carries it so "local only" is observed, not claimed.
                report.os_pipe_flags = _pipe_flags(kernel32, handle)
                # Started before the wait and joined after it, so the event is watched for exactly as
                # long as the wait can block. A watcher that has nothing to do returns immediately.
                watcher = (
                    _stop_watcher(kernel32, pipe_name, stop) if stop is not None else None
                )
                status = _connect(kernel32, handle)
                if watcher is not None:
                    watcher.join(timeout=2.0)
                if stop is not None and stop.is_set():
                    # The connection that ended the wait is the stop watcher's, not a caller's: it
                    # carries no frame, so serving it would count a malformed frame for a connection
                    # this server made to itself. Ended here rather than at the top of the loop, so
                    # the report says `stopped` truthfully.
                    report.stopped = True
                    break
                if status != 0:
                    report.error = f"ConnectNamedPipe failed: {_status_message(status)}"
                    break
                try:
                    _serve_connection(
                        kernel32, handle, root=root, expectation=expectation, report=report
                    )
                finally:
                    kernel32.DisconnectNamedPipe(handle)
            finally:
                # Every path, including the one where `ConnectNamedPipe` failed and the one where the
                # handler raised: an instance left open is a leaked kernel handle and a pipe name
                # that silently stops accepting callers.
                kernel32.CloseHandle(handle)
            served += 1
    finally:
        kernel32.LocalFree(descriptor.pointer)

    counter = _TEST_HOOKS.get("race_counter")
    if counter:
        report.connect_races = len(counter)
    return report.to_document()


# --------------------------------------------------------------------------------------------- #
# the client half
# --------------------------------------------------------------------------------------------- #


def _open_client(kernel32: Any, name: str, timeout_ms: int) -> tuple[Any, int]:
    """Connect to ``name`` with `WaitNamedPipeW` + `CreateFileW`, retrying inside the budget.

    A named pipe server opens its instance *before* anyone can connect and closes it after each
    request, so a client that arrives between two of those windows gets `ERROR_FILE_NOT_FOUND`. That
    is not an error the caller should see for a server that is merely between requests — but it must
    not become an infinite wait either, so the budget is the caller's ``timeout_ms`` and running out
    is reported as the timeout it is.
    """

    deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
    last_status = 0
    while True:
        kernel32.WaitNamedPipeW(name, 100)
        handle = kernel32.CreateFileW(
            name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if not _invalid_handle(handle):
            return handle, 0
        last_status = ctypes.get_last_error() or 0
        if time.monotonic() >= deadline:
            return None, last_status
        time.sleep(0.005)


def exchange(request: dict, *, pipe_name: str = PIPE_NAME, timeout_ms: int = 2000) -> dict:
    """Send one request, read one response, validate it, and return it — **interpreting nothing**.

    This is the corpus- and test-facing entry point, and :func:`call_broker` is the caller-facing one:
    one implementation of the exchange with two readings, which is what keeps the two from drifting.
    The difference is the whole point of having two. A reader of `protocol.parse_response` is the
    *untrusted caller*: it turns a verdict into an exception and refuses a response whose reason code
    is unregistered or is a success code. A test that wants to assert what came back on the wire — a
    byte-level corpus, above all — must see the document **before** that interpretation, and a
    fixture has to be a document, not an exception.

    The returned document is validated here with `schema_io.validate_self`: this build produced it,
    so a response the published `broker-response` schema refuses is this build's defect
    (`SELF_VALIDATION_FAILED`, exit 8) and never a verdict about the request — exactly as
    `broker/protocol.py` and `cli/tests/fake_broker.py` treat their own output. That check is
    deliberately *not* `parse_response`'s: `parse_response` validates a *received* document as
    `INVALID_INPUT`, which is right for a foreign answer and wrong for one this build's own server
    just wrote.

    Raises `AirootError` for a framing or transport failure, and for a response this build's own
    schema refuses. It **never** raises for the broker's verdict: a `rejected` document is returned
    like any other.
    """

    from .. import schema_io

    advapi32, kernel32 = _load_libraries()
    if kernel32 is None or advapi32 is None:  # pragma: no cover - v1 is a Windows provider
        raise AirootError(
            "EXTENSION_UNAVAILABLE",
            "the named-pipe client needs Windows",
            evidence=["os.name is not 'nt'"],
        )
    _declare_signatures(kernel32, advapi32)
    framing = _framing()

    handle, status = _open_client(kernel32, pipe_name, timeout_ms)
    if handle is None:
        # `ERROR_FILE_NOT_FOUND` (2) is what both "no server has ever run here" and "the server
        # stopped" answer, so the status is reported as what it is and *not* as "the broker crashed":
        # the two are indistinguishable from here (measured), and choosing one would be inventing a
        # fact about a process this client cannot see.
        raise AirootError(
            "NOT_IMPLEMENTED",
            f"no broker is listening on {pipe_name}",
            evidence=[
                f"CreateFileW/WaitNamedPipeW gave up after {timeout_ms} ms: {_status_message(status)}",
                "err=2 is answered both by a name no server has ever held and by a server that has "
                "stopped, so this says 'nothing is listening' and not 'the broker failed'",
                "in this build the only server is broker/pipe.py's user compatibility mode, started "
                "in-process by whoever runs it",
            ],
            details={"pipe_name": pipe_name, "timeout_ms": timeout_ms},
        )
    try:
        _write_frame(kernel32, handle, request)
        payload = framing.read_frame(_read_file_shim(kernel32, handle))
    finally:
        kernel32.CloseHandle(handle)
    if payload is None:
        raise AirootError(
            "INVALID_INPUT",
            "the broker closed the pipe without answering",
            evidence=["no complete frame arrived before the peer disconnected", f"pipe: {pipe_name}"],
        )
    document = framing.decode_frame(payload)
    schema_io.validate_self("broker-response", document)
    return document


def call_broker(request: dict, *, pipe_name: str = PIPE_NAME, timeout_ms: int = 2000) -> dict:
    """Send ``request`` to the broker on ``pipe_name`` and return the parsed response.

    This is the **caller's** API: :func:`exchange` reads the document, and `protocol.parse_response`
    then interprets it, so a verdict reaches the caller as the response's **own** reason code
    (`CALLER_NOT_AUTHORIZED`, `NOT_IMPLEMENTED`, ...) and only an ``ok`` comes back as a document —
    the path §112 built the response corpus for. A request the framing layer refuses raises
    `INVALID_INPUT` before any byte is written.

    Raises `AirootError` for: a request the framing layer refuses, a server that cannot be reached
    inside ``timeout_ms``, and every non-``ok`` verdict. Returns the response document otherwise.
    Use :func:`exchange` when the *document* is what is wanted rather than the verdict.
    """

    return protocol.parse_response(exchange(request, pipe_name=pipe_name, timeout_ms=timeout_ms))
