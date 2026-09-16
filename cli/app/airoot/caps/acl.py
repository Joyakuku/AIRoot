"""ACL observation **and the unprotected write side** for data roots (ADR-0023).

Planning §8.2 requires that "``doctor`` 必须检查 ACL 是否偏离", and the steward draft records an
``acl_baseline`` on every data root as an **observed value**. The observation half
(:func:`capture_acl` / :func:`differences`) landed first and is read-only; imposing a baseline needs
``WRITE_DAC`` behind the P2 broker, and the butler model says AIROOT may notice a change in the
user's directory, not revert it.

This module now also carries the **write half as a library** — :func:`apply_baseline`,
:func:`verify_baseline` and :func:`restore_acl` — because the plan that carries a baseline has to be
executable somewhere, and a library that can be tested without a broker is what the broker will
eventually call. The boundary it will sit behind is **still absent**.

**This write path is not protected, and a reader must not read it as though it were.** There is no
broker, no elevation, no named pipe, and nothing checks the caller: any process running as the same
user can call :func:`apply_baseline` and change a directory's DACL. ``SetSecurityInfo`` succeeds
exactly when the caller's token already holds ``WRITE_DAC`` on that directory — so on a directory
the user owns, this module grants no capability the user did not already have, which is also why
nothing here defends against the same user. ``AGENTS.md`` §5-1 states the project-wide form of this
(``security_mode=policy_only`` / ``enforcement=same_user_can_bypass``); this module is an instance of
it, not an exception to it.

That sentence is about *gaining* access, and the write side is also able to **lose** it: a protected
DACL that grants the caller nothing takes the directory away from the process that wrote it, and the
process may not be able to put it back (see the measured facts below). "The caller already had
``WRITE_DAC``" is a statement about the write, not about the directory staying reachable.

Four properties matter more than the parsing:

* **Failure is data.** ``GetNamedSecurityInfoW`` and ``SetSecurityInfo`` report status codes rather
  than raising, and a directory's ACL can legitimately be unreadable or unwritable. Every observed
  failure — a refused write, a vanished directory, a handle that cannot be opened — comes back as a
  returned message naming the call and the Windows status, never as an exception. The one deliberate
  exception is a caller handing in *nonsense*: applying a baseline that Windows cannot represent is
  refused with ``ValueError`` (see :func:`apply_baseline`), because that is a programming error in
  the caller, not a fact about the world.
* **ACE order is preserved, on purpose.** Windows ACE order carries meaning: a deny before an allow
  is not the same as the allow before the deny. Sorting would launder a meaningful permission change
  into "unchanged", so the canonical form keeps the order and the comparison is order-sensitive. The
  accepted cost is that a pure reordering reports drift; ADR-0023 records that trade. The write side
  inherits it: entries are written in the order given, and a verification reads them back in the
  order Windows returns them.
* **A baseline is data, and it is not a promise about the user's directory.** ADR-0023 decision 1
  still holds for ``doctor``: a recorded baseline is an *observation*, and drift means "this changed",
  not "you violated a requirement". Imposing one is a deliberate act by a caller that owns the
  decision — never something ``doctor`` does as remediation.
* **A baseline is serialisable and comparable, because a human will eventually approve it.** It is
  carried as the same JSON document :class:`AclSnapshot` already uses for the registry's
  ``acl_baseline`` column, so there is exactly one wire form for "these principals get this access".

Three facts about the write side were **measured on this host and are not preferences**. They are
recorded here because each one changed the code, and a reader who assumed the textbook behaviour would
write the same wrong version again:

* **A baseline is written with ``PROTECTED_DACL_SECURITY_INFORMATION``, and that is the only way it can
  land.** Writing the same DACL *without* the protection flag left the parent's live inheritable ACEs
  in place: a one-entry baseline stored as **12** entries (the explicit one plus the 11 inherited from
  ``%TEMP%``), so ``apply_baseline`` returned a difference and ``verify_baseline`` could never return an
  empty list on such a directory. A "baseline" that no directory can ever match is not a baseline, so
  the protection flag goes with the write. What protection *means* for a caller is recorded on
  :func:`apply_baseline`, and it is not small: the directory stops following its parent.
* **An empty baseline is refused, and it is refused because it cannot be undone, not because it is
  meaningless.** A protected empty DACL does land (0 entries, verified), and the directory then denies
  every principal — including the caller — so ``CreateFileW(…, WRITE_DAC)`` returns
  ``ERROR_ACCESS_DENIED``(5) and ``restore_acl`` is refused as well. Measured on this host under an
  **elevated** token, so the reachable-again path is ownership plus privilege, i.e. exactly the
  elevation §5-1 says this build does not have. Writing it would make one unprivileged library call a
  door that closes behind the caller, and :func:`restore_acl` exists to promise that applying a
  baseline is not that door. A valid instruction the module declines to execute is reported as a
  finding, the same way an absent DACL is (see :func:`apply_baseline`).
* **What protection costs is the ``INHERITED_ACE`` bit, once.** Imposing a snapshot whose entries were
  all inherited stores the same ACEs, in the same order, with the same masks and SIDs, but as explicit
  entries: the first write therefore reports one difference per ACE ("flags 0x13 vs 0x03") and every
  write after it is clean and stable. This is why the round-trip tests compare the two ACLs with that
  one bit masked out and say so, rather than asserting a digest equality that this host makes
  impossible.

**What a reviewer must settle before this sits behind a broker.** None of these is a defect in the
code below; each is a property of ``SetSecurityInfo`` that the broker, not this library, has to own:

* **Who survives the write.** A protected baseline that names only other principals leaves the caller
  unable to open the directory again, so this module can revoke the very right it was called through.
  It does not refuse that case: deciding "this baseline is safe for the caller" needs a policy about
  which principals matter, which is the broker's job. Measured, on this host, with an allow-only
  baseline: the directory stays writable when the DACL grants the caller anything (an allow for
  ``BUILTIN\\Users`` or ``Everyone`` was enough, under a token holding ``BUILTIN\\Administrators``),
  and is refused with ``ERROR_ACCESS_DENIED``(5) when the DACL grants the caller nothing — no allow
  matching the token, or no allow at all.
* **Privileges.** Every call here needs ``WRITE_DAC`` (and ``READ_CONTROL``) on the directory and
  nothing more; the module runs unprivileged and enables no privilege — the ``SeRestorePrivilege``,
  ``SeTakeOwnershipPrivilege`` and ``SeSecurityPrivilege`` note on :func:`restore_acl` is a
  measurement, not an offer. Owner writes are attempted only when the owner actually moved, and an
  ordinary user's owner change is refused. What this cannot do is put back a DACL that no longer
  grants the caller anything: ownership plus an enabled privilege is the escape route, and the broker
  is the only component allowed to hold it.
* **Ordering and inheritance.** ACE order is written as given and read back as stored (see the order
  property above); inherited ACEs are dropped by protection and re-stored as explicit ones, one way,
  once. A directory whose parent has live inheritable ACEs therefore ends the first write with a DACL
  that no longer tracks that parent, and its existing children keep what they inherited earlier.
* **What ``SetSecurityInfo`` accepting a call does not prove.** ``ERROR_SUCCESS`` means "the kernel
  took the request", not "the stored descriptor is the one asked for" — which is why every function
  here re-reads and returns the difference, and why a caller that treats "no exception" as success
  has already lost the argument.

No third-party dependency: this uses ``ctypes`` against ``advapi32``/``kernel32``, exactly as
``paths.py`` reads volume identity. ``jsonschema`` stays the only runtime dependency.

**The observed values are machine fingerprints** (owner and trustee SIDs). They belong in a
registry, never in a committed fixture — see ``AGENTS.md`` §9 and draft §58.2.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canon import canonical_bytes, digest_bytes
from .identity import _status_message

__all__ = [
    "AclEntry",
    "AclSnapshot",
    "acl_baseline",
    "acl_digest",
    "apply_baseline",
    "capture_acl",
    "differences",
    "restore_acl",
    "verify_baseline",
]

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
#: ``PROTECTED_DACL_SECURITY_INFORMATION``: store the DACL as given and detach it from the parent's
#: inheritance. `SetSecurityInfo` takes this in the *information* argument, not as an ACL flag, and the
#: kernel is what sets `SE_DACL_PROTECTED` — an in-memory descriptor built by
#: :func:`_dacl_pointer` has no such flag to set. Named rather than inlined because it is the
#: difference between a baseline the directory can match and one it can never match; see the module
#: docstring.
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000

#: `CreateFileW` access rights and flags for a directory handle that may rewrite a DACL.
_WRITE_DAC = 0x00040000
_READ_CONTROL = 0x00020000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
#: `ACL_REVISION` and `SECURITY_DESCRIPTOR_REVISION`, the two ABI numbers this module writes. Both are
#: named rather than inlined because they appear on both sides of the write and a typo in either is an
#: `ERROR_INVALID_ACL`/`ERROR_INVALID_SECURITY_DESCR` that would otherwise look like a permission
#: problem.
_ACL_REVISION = 2
_SECURITY_DESCRIPTOR_REVISION = 1
#: A self-relative `SECURITY_DESCRIPTOR` is 20 bytes with the optional SACL pointer omitted; 64 bytes
#: is the conventional over-allocation and leaves room for the revision's own fields.
_SECURITY_DESCRIPTOR_BYTES = 64
#: Passing `NULL` for the DACL of a *set* call means "replace nothing" — the action is ignored — so the
#: write side never relies on that ambiguity: it either passes a real ACL or takes the dedicated
#: owner-only path in :func:`restore_acl`. `SetSecurityInfo` cannot be asked to store a NULL DACL
#: through this signature, and that is recorded as a known limit rather than worked around.
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

#: ACE types this module understands well enough to name a trustee. Anything else is recorded by
#: type and size only: the point is to notice that *something* changed, so an ACE we cannot fully
#: decode must still be able to make the digest move.
_ACE_ACCESS_ALLOWED = 0x00
_ACE_ACCESS_DENIED = 0x01
_TRUSTEE_BEARING = (_ACE_ACCESS_ALLOWED, _ACE_ACCESS_DENIED)

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _Acl(ctypes.Structure):
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


class _AccessAce(ctypes.Structure):
    """The prefix shared by allow/deny ACEs: header, mask, then the SID bytes."""

    _fields_ = [("Header", _AceHeader), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]


def _declare_signatures() -> None:
    """Pin the argument and return types of every entry point the **write** side uses.

    Not decoration, and not optional in this project: ``identity.py`` documents what an unannotated
    parameter costs — ctypes defaults it to C ``int``, so a 64-bit ``HANDLE`` arrives truncated and
    the call dies with an ``OverflowError`` raised from inside ctypes instead of with the access
    error it should have reported. `FILE_FLAG_BACKUP_SEMANTICS` also makes ``0x02000000`` a *signed*
    ``DWORD`` argument in that failure mode, and this module's whole point is reporting failures
    accurately. Called lazily by the two public entry points rather than at import, so a syntax-only
    import of this module never touches the DLL.
    """

    _kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.restype = wintypes.HANDLE
    _advapi32.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _advapi32.InitializeAcl.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.AddAccessAllowedAceEx.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    _advapi32.AddAccessDeniedAceEx.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _advapi32.AddAccessDeniedAceEx.restype = wintypes.BOOL
    # The signature is the 6-parameter form: handle, object type, security information, and the
    # owner/group/DACL/SACL pointers — there is no "optional parameters" argument.
    _advapi32.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _advapi32.SetSecurityInfo.restype = wintypes.DWORD
    _advapi32.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _advapi32.InitializeSecurityDescriptor.restype = wintypes.BOOL
    _advapi32.SetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        ctypes.c_void_p,
        wintypes.BOOL,
    ]
    _advapi32.SetSecurityDescriptorDacl.restype = wintypes.BOOL
    _advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    _advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL


def _build_acl(entries: tuple[AclEntry, ...]) -> tuple[ctypes.Array[Any] | None, int, str | None]:
    """Lay out a DACL in memory for ``SetSecurityInfo``.

    Returns ``(buffer, status, problem)``. ``problem`` is set for a caller error this function can
    name precisely — an entry whose SID is not a SID — while ``status`` carries a Windows failure. The
    two are kept apart so :func:`apply_baseline` can decide which one is a ``ValueError`` (the caller
    handed in nonsense) and which one is a data finding about the machine.

    The buffer is sized from the ACEs themselves rather than guessed: an ACL that is one byte short is
    ``ERROR_INVALID_ACL``, and the required size is exactly
    ``ACL_HEADER + sum(ACE_HEADER + mask + SID)``. Alignment is not a free choice —
    ``InitializeAcl`` expects a ``DWORD``-aligned address, so the buffer is allocated as a
    ``c_uint32`` array.

    **The ACE bytes are written by ``AddAccessAllowedAceEx``/``AddAccessDeniedAceEx``, exactly once
    each.** An earlier version also wrote the ACE header by hand through the same ``_AccessAce`` view
    the reader uses, on the theory that it kept the two halves from disagreeing. It did the opposite:
    the hand-written header sat at the *end* of the ACL while the API appends at the running offset, so
    from the third ACE on the two disagreed about where the ACE was — the hand-written header landed on
    top of an already-added ACE and the next ``Add`` failed with ``ERROR_INVALID_ACL`` (1336) on an ACL
    that was perfectly well formed. Nothing here writes ACE bytes; the API owns that, and the reader's
    view of the layout is only used for the *size* arithmetic.

    Every SID stays owned by this function for the whole call: they are freed in one place, after the
    buffer no longer references them.
    """

    if not entries:
        # Not reachable from `apply_baseline`, which refuses an empty baseline before it gets here (see
        # there, and the module docstring): an empty DACL cannot be undone by the caller that wrote it.
        # The branch is kept as a *report* rather than an assertion because this is a private helper and
        # the public entry point is where the decision belongs — but it must not silently build an ACL
        # that locks a directory, so it hands back a problem instead.
        return None, 0, _empty_baseline_problem()

    _declare_signatures()
    sid_pointers: list[ctypes.c_void_p] = []
    sized: list[tuple[AclEntry, int]] = []
    try:
        for index, entry in enumerate(entries):
            if entry.ace_type not in _TRUSTEE_BEARING:
                return None, 0, _unsupported_entry(index, entry)
            if not entry.sid:
                return (
                    None,
                    0,
                    f"entry {index} has no trustee SID, so there is no principal to apply it to",
                )
            pointer, status = _convert_sid(entry.sid)
            if pointer is None:
                return (
                    None,
                    0,
                    f"entry {index} names trustee {entry.sid!r}, which ConvertStringSidToSidW could "
                    f"not convert ({_status_message(status)})",
                )
            sid_pointers.append(pointer)
            sized.append((entry, int(_advapi32.GetLengthSid(pointer))))

        acl_header = ctypes.sizeof(_Acl)
        ace_prefix = ctypes.sizeof(_AccessAce)
        size = acl_header + sum(ace_prefix + sid_length for _entry, sid_length in sized)
        buffer = (ctypes.c_uint32 * ((size + 3) // 4))()
        base = ctypes.cast(buffer, ctypes.c_void_p)
        if not _advapi32.InitializeAcl(base, size, _ACL_REVISION):
            return None, ctypes.get_last_error() or 0, None

        for (entry, _sid_length), pointer in zip(sized, sid_pointers):
            add = (
                _advapi32.AddAccessAllowedAceEx
                if entry.ace_type == _ACE_ACCESS_ALLOWED
                else _advapi32.AddAccessDeniedAceEx
            )
            if not add(base, _ACL_REVISION, entry.flags, entry.mask, pointer):
                return None, ctypes.get_last_error() or 0, None
        return buffer, 0, None
    finally:
        for pointer in sid_pointers:
            _kernel32.LocalFree(pointer)


def _unsupported_entry(index: int, entry: AclEntry) -> str:
    return (
        f"entry {index} has ACE type 0x{entry.ace_type:02x}, which this module cannot write; only "
        f"access-allowed (0x00) and access-denied (0x01) ACEs are supported"
    )


def _empty_baseline_problem() -> str:
    """Why an empty baseline is refused rather than written. One spelling, two readers.

    The reasoning is in the module docstring and the measured numbers are in the tests; this is the
    sentence a caller actually receives, kept in one place so the refusal a test asserts and the
    refusal a caller reads cannot drift apart.
    """

    return (
        "the baseline has no entries, i.e. an explicit empty DACL. That is writable but not undoable: "
        "an empty DACL denies every principal including the caller, so after the write "
        "CreateFileW(..., WRITE_DAC) returns ERROR_ACCESS_DENIED(5) and restore_acl is refused too "
        "(measured on this host, under an elevated token). Regression to the directory would need "
        "ownership plus privilege, which this build does not have. Nothing was written; pass a "
        "baseline that leaves the caller able to undo it"
    )


def _dacl_pointer(dacl: Any) -> tuple[Any, ctypes.c_void_p | None, str | None]:
    """Wrap a built ACL in a self-relative descriptor and hand back its DACL pointer.

    Returns ``(descriptor, pointer, problem)``. **The caller must keep ``descriptor`` alive for as long
    as it uses ``pointer``**: a self-relative security descriptor stores its DACL as an offset into its
    own memory, so the pointer is only an address while the descriptor exists. Dropping it here — the
    obvious shape, and the one this function first had — leaves a dangling address that the kernel
    accepts without complaint while storing no DACL at all.

    Why route through a descriptor at all is recorded in :func:`_set_security_info`: a real ``c_void_p``
    for the DACL landed the ACL on every repetition, while handing over the ACL array itself was
    silently unreliable on this host.
    """

    descriptor = ctypes.create_string_buffer(_SECURITY_DESCRIPTOR_BYTES)
    if not _advapi32.InitializeSecurityDescriptor(descriptor, _SECURITY_DESCRIPTOR_REVISION):
        return descriptor, None, (
            f"InitializeSecurityDescriptor failed ({_status_message(ctypes.get_last_error() or 0)})"
        )
    if not _advapi32.SetSecurityDescriptorDacl(descriptor, True, dacl, False):
        return descriptor, None, (
            f"SetSecurityDescriptorDacl failed ({_status_message(ctypes.get_last_error() or 0)})"
        )
    present = wintypes.BOOL()
    pointer = ctypes.c_void_p()
    defaulted = wintypes.BOOL()
    if not _advapi32.GetSecurityDescriptorDacl(
        descriptor, ctypes.byref(present), ctypes.byref(pointer), ctypes.byref(defaulted)
    ):
        return descriptor, None, (
            f"GetSecurityDescriptorDacl failed ({_status_message(ctypes.get_last_error() or 0)})"
        )
    if not present.value or pointer.value is None:
        return descriptor, None, "the descriptor reported no DACL after one was set on it"
    return descriptor, pointer, None


def _open_for_write(path: Path) -> tuple[Any | None, int]:
    """A directory handle carrying ``WRITE_DAC``, or ``(None, status)``."""

    handle = _kernel32.CreateFileW(
        ctypes.c_wchar_p(str(path)),
        _WRITE_DAC | _READ_CONTROL,
        0x00000001 | 0x00000002 | 0x00000004,  # FILE_SHARE_READ | WRITE | DELETE
        None,
        0x00000003,  # OPEN_EXISTING
        _FILE_FLAG_BACKUP_SEMANTICS,  # required to get a handle on a *directory*
        None,
    )
    if handle is None or handle == _INVALID_HANDLE_VALUE:
        return None, ctypes.get_last_error() or 0
    return handle, 0


def _owner_sid(sid: str) -> tuple[ctypes.c_void_p, ctypes.c_void_p | None, int]:
    """The binary SID behind a ``S-1-...`` string, plus the pointer to free it with.

    ``SetSecurityInfo`` takes a **binary** SID: handing it a string is ``ERROR_INVALID_PARAMETER``
    (measured on this host, and the reason :func:`restore_acl` converts first). An empty address means
    the conversion failed, so the caller reports ``status`` instead of writing the wrong SID.

    ``None`` for the third element is the sentinel case — an empty SID string — and it is kept apart
    from a failed conversion because "nothing to convert" must not be reported as an error.
    """

    if not sid:
        return ctypes.c_void_p(), None, 0
    pointer, status = _convert_sid(sid)
    if pointer is None:
        return ctypes.c_void_p(), None, status
    return pointer, pointer, 0


def _convert_sid(sid: str) -> tuple[ctypes.c_void_p | None, int]:
    """``ConvertStringSidToSidW``: the SID a string names, or ``(None, status)``."""

    pointer = ctypes.c_void_p()
    if not _advapi32.ConvertStringSidToSidW(ctypes.c_wchar_p(sid), ctypes.byref(pointer)):
        return None, ctypes.get_last_error() or 0
    return pointer, 0


def _set_security_info(
    path: Path,
    *,
    dacl: Any = None,
    owner: str | None = None,
    protect_dacl: bool = False,
) -> tuple[int, str | None]:
    """Apply ``SetSecurityInfo`` and return ``(status, problem)``; ``status == 0`` means applied.

    A separate call per information class, because ``SetSecurityInfo``'s DACL parameter cannot express
    "store a NULL DACL": passing ``NULL`` means "do not change the DACL", so an owner write and a DACL
    write are two calls with nothing in common to conflate. Each opens its own handle, so a failure in
    the first cannot leave a half-configured handle behind for the second.

    **Where the value goes is decided by the information class, not by position.** ``SetSecurityInfo``
    takes owner, group, DACL and SACL pointers in that fixed order, so a DACL handed to the owner slot
    is not a near-miss: the call *succeeds* (``ERROR_SUCCESS``), the kernel reads the owner slot as a
    SID that is really an ACL, and the DACL slot stays ``NULL`` — meaning "leave the DACL alone". The
    measured result is a directory whose DACL came back **absent**, i.e. granting everyone full
    control, while the write reported success. Every call below therefore names its own slot.

    **The DACL is handed over as a pointer taken out of a security descriptor, and that is not
    cosmetic either.** Handing over the ACL buffer itself let ctypes marshal an array through a
    ``c_void_p`` parameter, and that form was unreliable here. Routing the buffer through
    ``InitializeSecurityDescriptor``/``SetSecurityDescriptorDacl`` and taking the pointer back with
    ``GetSecurityDescriptorDacl`` produces a real ``c_void_p``; the descriptor stays a local of this
    function so the address inside it stays valid for the call.

    ``owner`` is the ``S-1-...`` string; it is converted to the binary SID ``SetSecurityInfo`` actually
    takes (a string is ``ERROR_INVALID_PARAMETER``).

    ``protect_dacl`` adds ``PROTECTED_DACL_SECURITY_INFORMATION`` to the **information** argument of the
    DACL call, which is what makes the stored DACL the one that was passed instead of that one *plus*
    the parent's live inheritable ACEs. The caller decides (see :func:`apply_baseline`); the default is
    ``False`` so that an owner write cannot accidentally carry the flag, since the two are independent
    calls and only one of them is allowed to detach inheritance.
    """

    _declare_signatures()
    # (information, the owner/group/DACL/SACL pointers in that fixed order, what to free afterwards)
    settings: list[tuple[int, tuple[Any, Any, Any], ctypes.c_void_p | None]] = []
    if owner is not None:
        pointer, free_with, status = _owner_sid(owner)
        if pointer.value is None and owner:
            return status, f"ConvertStringSidToSidW({owner!r}) failed: {_status_message(status)}"
        settings.append((OWNER_SECURITY_INFORMATION, (pointer, None, None), free_with))
    if dacl is not None:
        descriptor, dacl_pointer, problem = _dacl_pointer(dacl)
        if problem is not None:
            return 0, problem
        # The DACL goes in the *third* slot. There is no version of this call where position is
        # inferred: `SetSecurityInfo` takes owner, group, DACL, SACL.
        information = DACL_SECURITY_INFORMATION | (
            PROTECTED_DACL_SECURITY_INFORMATION if protect_dacl else 0
        )
        settings.append((information, (None, None, dacl_pointer), None))
    else:
        descriptor = None

    try:
        for information, security_pointers, _free_with in settings:
            handle, status = _open_for_write(path)
            if handle is None:
                return status, None
            try:
                status = int(
                    _advapi32.SetSecurityInfo(
                        handle, SE_FILE_OBJECT, information, *security_pointers
                    )
                )
            finally:
                _kernel32.CloseHandle(handle)
            if status != 0:
                return status, None
        return 0, None
    finally:
        for _information, _pointers, free_with in settings:
            if free_with is not None:
                _kernel32.LocalFree(free_with)
        # Referenced so the descriptor and the SID live at least as long as the calls above.
        del descriptor



def _sid_to_string(sid_pointer: ctypes.c_void_p) -> str | None:
    out = ctypes.c_wchar_p()
    if not _advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(out)):
        return None
    try:
        return out.value
    finally:
        _kernel32.LocalFree(out)


@dataclass(frozen=True)
class AclEntry:
    """One ACE, in the order Windows returned it."""

    ace_type: int
    flags: int
    mask: int
    sid: str | None

    def to_document(self) -> list[Any]:
        return [self.ace_type, self.flags, self.mask, self.sid]

    @classmethod
    def from_document(cls, document: Any) -> "AclEntry":
        ace_type, flags, mask, sid = document
        return cls(int(ace_type), int(flags), int(mask), sid if sid is None else str(sid))


@dataclass(frozen=True)
class AclSnapshot:
    """What the directory's security descriptor looked like when we asked."""

    path: str
    owner: str | None = None
    entries: tuple[AclEntry, ...] = ()
    dacl_present: bool = False
    #: Set when the observation failed. A snapshot with a reason carries no entries, so it can never
    #: be mistaken for "the ACL is empty" — those are different facts.
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def observed(self) -> bool:
        return self.reason is None

    def content(self) -> dict[str, Any]:
        """The observed facts, without the digest — what the digest is computed *over*.

        Kept separate from :meth:`to_document` on purpose: folding the digest into the hashed
        payload is a one-line way to make the function recurse until the interpreter gives up.
        """

        return {
            "schema_version": 1,
            "owner": self.owner,
            "dacl_present": self.dacl_present,
            "entries": [entry.to_document() for entry in self.entries],
        }

    def to_document(self) -> dict[str, Any]:
        document = self.content()
        # `observed`/`reason` are carried alongside the facts so a stored failure can never be read
        # back as "the ACL is empty". Those are different facts and only one of them is safe to
        # compare against.
        document["observed"] = self.observed
        document["reason"] = self.reason
        document["digest"] = acl_digest(self) if self.observed else None
        return document

    @classmethod
    def from_document(cls, path: str, document: dict[str, Any]) -> "AclSnapshot":
        return cls(
            path=str(path),
            owner=document.get("owner"),
            entries=tuple(AclEntry.from_document(item) for item in document.get("entries", ())),
            dacl_present=bool(document.get("dacl_present", False)),
            reason=document.get("reason"),
        )


def capture_acl(path: Path | str) -> AclSnapshot:
    """Observe ``path``'s owner and DACL. Never raises: an unreadable ACL comes back as a reason."""

    target = Path(path)
    security_descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    status = _advapi32.GetNamedSecurityInfoW(
        ctypes.c_wchar_p(str(target)),
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | OWNER_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if status != 0:
        return AclSnapshot(
            path=str(target),
            reason=f"GetNamedSecurityInfoW returned {status} (winerror={ctypes.get_last_error()})",
        )
    try:
        entries: list[AclEntry] = []
        dacl_present = bool(dacl)
        if dacl:
            acl = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
            for index in range(acl.AceCount):
                ace_pointer = ctypes.c_void_p()
                if not _advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                    return AclSnapshot(
                        path=str(target),
                        reason=f"GetAce failed at index {index} (winerror={ctypes.get_last_error()})",
                    )
                header = ctypes.cast(ace_pointer, ctypes.POINTER(_AceHeader)).contents
                if header.AceType in _TRUSTEE_BEARING:
                    ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAce)).contents
                    sid = _sid_to_string(ctypes.c_void_p(ace_pointer.value + _AccessAce.SidStart.offset))
                    entries.append(
                        AclEntry(int(header.AceType), int(header.AceFlags), int(ace.Mask), sid)
                    )
                else:
                    # An ACE shape this module does not decode still has to move the digest: the
                    # question is "did this change", and an object ACE changing is a change.
                    entries.append(AclEntry(int(header.AceType), int(header.AceFlags), 0, None))
        return AclSnapshot(
            path=str(target),
            owner=_sid_to_string(owner) if owner else None,
            entries=tuple(entries),
            dacl_present=dacl_present,
            metadata={"aces": len(entries)},
        )
    finally:
        _kernel32.LocalFree(security_descriptor)


def acl_digest(snapshot: AclSnapshot) -> str:
    """A short, order-sensitive digest of the observed security descriptor."""

    return digest_bytes(canonical_bytes(snapshot.content()))


def _acls_comparable(recorded: AclSnapshot, current: AclSnapshot) -> bool:
    return recorded.observed and current.observed


def differences(recorded: AclSnapshot, current: AclSnapshot) -> list[str]:
    """Human-readable differences, for an answer that has to say *what* changed.

    Only meaningful for two successful observations; the caller decides what to do when either side
    could not be read, because "unreadable" and "changed" are different facts and the answer must
    not blur them.
    """

    if not _acls_comparable(recorded, current):
        return []
    findings: list[str] = []
    if recorded.owner != current.owner:
        findings.append(f"owner: recorded={recorded.owner} actual={current.owner}")
    if recorded.dacl_present != current.dacl_present:
        findings.append(
            f"dacl: recorded={'present' if recorded.dacl_present else 'absent'} "
            f"actual={'present' if current.dacl_present else 'absent'}"
        )
    if len(recorded.entries) != len(current.entries):
        findings.append(f"entries: recorded={len(recorded.entries)} actual={len(current.entries)}")
    for index, (before, after) in enumerate(zip(recorded.entries, current.entries)):
        if before != after:
            findings.append(
                f"entry {index}: recorded=type {before.ace_type}/flags 0x{before.flags:02x}/"
                f"mask 0x{before.mask:08x} actual=type {after.ace_type}/flags 0x{after.flags:02x}/"
                f"mask 0x{after.mask:08x}"
            )
    return findings


# --- The write side (UNCALLED from the CLI on purpose; see the module docstring) -------------------
#
# Nothing in `cli.py`, no verb, no plan schema and no golden fixture reaches these functions: without
# the broker there is no approved caller, so this ships as a library with a tested contract and no
# user-facing surface. A baseline's wire form is `AclSnapshot`'s own document — the same one the
# registry already stores in `acl_baseline_json` — so there is exactly one spelling of "these
# principals get this access", and it is the one `doctor` already compares against.


def acl_baseline(path: Path | str) -> AclSnapshot:
    """Read ``path`` and produce a baseline a caller could impose elsewhere.

    A separate name rather than a call to :func:`capture_acl` because the two answer different
    questions from the same observation: a capture is *evidence about this directory*, while a
    baseline is *an instruction for a directory*. The value is the same document, and deriving one
    from the other is this one line — which is the point, because a second serialisation of the same
    facts is how two halves of a module start disagreeing.

    The owner is carried too. It is **not** imposed by :func:`apply_baseline` (see there), but a
    restoration target needs it, so dropping it here would make "capture, apply, restore" impossible.
    """

    return capture_acl(path)


def verify_baseline(path: Path | str, baseline: AclSnapshot) -> list[str]:
    """Does ``path``'s DACL match ``baseline``? Empty means match; anything else is a difference.

    Delegates to :func:`differences` rather than restating the DACL comparison, so the answer uses the
    same order-sensitive rules the drift check uses and there is one place where "same ACL" is decided.

    Two facts the caller must not flatten:

    * **An empty list means "verified", so an unreadable directory must never return one.** A capture
      that failed comes back as its own line naming the reader and the status — a caller that cannot
      see the directory has not verified anything, and the honest answer is the failure, not silence.
    * **Ownership is deliberately not verified.** :func:`apply_baseline` does not write the owner (a
      butler does not take ownership of the user's directory), so a reported owner difference would be
      a difference the write path can never clear. The baseline's ``owner`` is still carried as a
      restoration target for :func:`restore_acl`, which does write it.
    """

    current = capture_acl(path)
    if not current.observed:
        return [f"capture_acl: {current.reason}"]
    if not baseline.observed:
        # The baseline is the *expectation*, so a failed read on that side is the caller's input error
        # rather than a fact about ``path`` — and returning an empty list here would report a match
        # that was never established.
        return ["baseline: this baseline was never observed, so there is nothing to verify against"]
    return _dacl_findings(baseline, current)


def _dacl_findings(baseline: AclSnapshot, current: AclSnapshot) -> list[str]:
    """``differences`` with the owner line dropped, and only that line.

    Filtering is confined to the one prefix the reader itself writes, so a second kind of finding can
    never be dropped by accident — the alternative (passing ``current.owner`` as the baseline's owner)
    would silence the owner comparison by making the input say something it does not.
    """

    return [finding for finding in differences(baseline, current) if not finding.startswith("owner:")]


def apply_baseline(path: Path | str, baseline: AclSnapshot) -> list[str]:
    """Impose ``baseline``'s DACL on ``path``. Empty means applied **and** re-read as matching.

    The flow this exists for is capture → apply → verify → restore → verify-again, so the answer is
    never "the call returned success": ``SetSecurityInfo`` accepting an ACL and Windows then returning
    a different one are two different facts (inheritance can change what lands), and only the second one
    decides whether the directory now has the posture the caller asked for. An empty list therefore
    means the post-write capture matched.

    **The DACL is written protected, so the directory stops following its parent.** With
    ``PROTECTED_DACL_SECURITY_INFORMATION`` the stored DACL is exactly the one passed and the parent's
    live inheritable ACEs no longer flow in. Without it, the same call left a one-entry baseline stored
    as 12 entries on this host and **no** directory under an inheriting parent could ever satisfy a
    baseline, which is the whole purpose of this function. Three consequences a caller must know
    before handing this to a broker:

    * Inherited ACEs the directory currently has are **stored as explicit ones** and stop tracking the
      parent. Effective access on the directory does not change; what changes is that a later change to
      the parent no longer propagates here.
    * That conversion is one-way and shows up once: imposing a snapshot whose entries were all inherited
      reports one difference per ACE (``flags 0x13`` → ``flags 0x03``) because ``INHERITED_ACE`` is gone,
      and the directory matches from then on. The round-trip tests compare with that bit masked out and
      say so.
    * Protection is per-object. Existing *descendants* keep whatever they inherited, and new ones take
      what this directory now offers.

    **What it does not do**: it does not write the owner. A baseline is a statement about which
    principals get which access; taking ownership of a directory the butler does not own is a
    different act with a different consequence, so it is :func:`restore_acl`'s job and not this one's.
    It also does not check that the caller survives its own write — a protected DACL that grants the
    caller nothing (``allow`` only for other principals) leaves the caller unable to write again, and
    this unprivileged layer cannot safely refuse every such baseline. That check belongs to the broker;
    it is recorded in the module docstring's limits.

    **An empty baseline is refused as a finding.** It is the one posture that is writable but not
    undoable — see :func:`_empty_baseline_problem` and the module docstring — so the refusal is data
    (a stable answer about the machine, like ``dacl_present=false`` below) rather than the ``ValueError``
    this function reserves for a caller error a retry could not fix.

    **Raises ``ValueError`` — deliberately, and only for nonsense the caller can fix.** This module's
    rule is that failure is data, and every *machine* failure (the directory vanished, the handle
    cannot be opened, ``SetSecurityInfo`` is refused, the ACL cannot be built) is returned as a line
    naming the call and the Windows status. An ACE this module cannot write, an entry with no trustee,
    or a baseline that was never successfully observed are not machine facts: no retry, no privilege
    and no different machine would change the answer, and a caller that gets data back for those would
    reasonably try again. Refusing loudly is the honest response to a caller passing nonsense, and it
    is pinned in the tests rather than left to the docstring.

    Note there is no new reason code behind this: ``ACL_MISMATCH``(5) already means "the ACL is not the
    required one" and belongs to whoever calls this from behind the broker.
    """

    if not baseline.observed:
        raise ValueError(
            "apply_baseline needs an observed baseline; this one is a failed observation "
            f"({baseline.reason})"
        )
    # `dacl_present` is the only thing the baseline says that the entry list cannot: "this directory
    # grants nothing" and "this directory's DACL is absent" are different postures, and the reader
    # records which one it saw. `SetSecurityInfo`'s DACL parameter cannot express a NULL DACL through
    # this signature (NULL means "do not change it"), so the absent case is a limit that is reported
    # instead of being silently approximated — a NULL DACL hands everyone full control, and that is the
    # one substitution a caller must never get without asking for it. The "grants nothing" posture is
    # refused just below for the mirror-image reason: it can be written but not undone. This check comes
    # first of the two because an absent DACL is normally recorded with no entries at all, and "there is
    # no DACL here" is the more specific fact about what the caller asked for.
    if not baseline.dacl_present:
        return [
            "apply_baseline: this baseline records an absent DACL (dacl_present=false), and "
            "SetSecurityInfo cannot store a NULL DACL through the parameter this module passes (NULL "
            "spells 'do not change this DACL'); nothing was written"
        ]

    # Before anything is built or opened: an empty baseline is the one instruction this module will not
    # carry out. `_build_acl` refuses it too, so no future caller of that helper can slip past this.
    if not baseline.entries:
        return [f"apply_baseline: {_empty_baseline_problem()}"]
    buffer, status, problem = _build_acl(baseline.entries)
    if problem is not None:
        raise ValueError(f"apply_baseline cannot impose this baseline: {problem}")
    if buffer is None:
        return [f"SetSecurityInfo: InitializeAcl/AddAccess* failed, status={_status_message(status)}"]

    status, problem = _set_security_info(path, dacl=buffer, protect_dacl=True)
    if problem is not None:
        return [problem]
    if status != 0:
        return [f"SetSecurityInfo: {_status_message(status)}"]

    current = capture_acl(path)
    if not current.observed:
        # The write may well have landed; what failed is the confirmation. Saying so is the only
        # honest answer, and it is why this cannot collapse into `return []`.
        return [f"capture_acl after the write: {current.reason}"]
    return _dacl_findings(baseline, current)


def restore_acl(path: Path | str, snapshot: AclSnapshot) -> list[str]:
    """Put a captured ``snapshot`` back: its DACL, and its owner when that differs from the current one.

    The other half of "applying a baseline is never a one-way door". A caller that captured before it
    wrote can hand the same snapshot back and get the directory's security descriptor returned — the
    flow is capture, apply, verify, restore, verify again, and the last verification is what makes the
    claim "it was put back" mean something. An empty list means the whole descriptor matches again.

    **"Put back" means the same access and the same owner, not the same ``INHERITED_ACE`` bits.**
    Protection is one-way (see :func:`apply_baseline`), so a snapshot captured from an inheriting
    directory is restored with its ACEs stored explicitly: same order, same types, same masks, same
    SIDs, and the inherited mark gone for good. Feeding the *restored* observation back in is what comes
    back clean — the same snapshot re-imposed a second time reports nothing. The distinction is not
    pedantry: on this host the digest of a directory whose 11 ACEs were inherited never equals the
    digest of the same 11 ACEs written back, so a caller that asserted digest equality across a
    round trip would conclude the restore failed when it did not.

    **The DACL is restored first, and the owner is written only when it is actually different.** Both
    halves of that sentence were measured, not chosen:

    * An owner write is refused with ``ERROR_ACCESS_DENIED``(5) on an ordinary user's directory on this
      host, and enabling ``SeRestorePrivilege``, ``SeTakeOwnershipPrivilege`` and ``SeSecurityPrivilege``
      on the token did **not** change that. Writing the owner *first* therefore turned the whole round
      trip — the one this API exists for — into a refusal that also skipped the DACL. It is also not
      needed: putting back the owner the directory already has changes nothing.
    * So the owner is written only when a fresh observation says it differs. That makes the ordinary
      capture/apply/restore cycle unprivileged (its owner never moved), and reserves the refusal for the
      case that really needs privilege, where it is reported like every other refusal here.

    The owner is skipped entirely when the snapshot has ``owner=None`` — a snapshot whose owner could
    not be read, where "restore it to nobody" is not a thing.

    ``dacl_present=false`` is the one baseline shape that cannot be restored, and it is reported rather
    than approximated: ``SetSecurityInfo``'s DACL parameter spells ``NULL`` as "leave this DACL alone",
    so there is no way through it to put back an absent DACL. An absent DACL grants everyone full
    control, and the empty-but-present ACL this module refuses to write is the opposite posture — so
    substituting one for the other would be a silent permission change. The owner half is still
    attempted and its outcome reported alongside, because the two calls are independent and the caller
    has to know which of them landed.

    An **empty** snapshot is refused by :func:`apply_baseline` on this same path, and for the same
    reason: an empty DACL denies the caller too, so a snapshot that records one is not a restoration
    target. Nothing is written, and the finding says so.

    Raises ``ValueError`` for a snapshot that was never observed, on the same rule as
    :func:`apply_baseline`: there is nothing to restore, and no retry would help.
    """

    if not snapshot.observed:
        raise ValueError(
            "restore_acl needs an observed snapshot; this one is a failed observation "
            f"({snapshot.reason})"
        )
    findings: list[str] = []
    if not snapshot.dacl_present:
        findings.append(
            "restore_acl: this snapshot records an absent DACL (dacl_present=false), and "
            "SetSecurityInfo cannot store a NULL DACL through the parameter this module passes (NULL "
            "spells 'do not change this DACL'); the DACL was left as it is"
        )
    else:
        findings.extend(f"DACL: {finding}" for finding in apply_baseline(path, snapshot))

    owner_note = _restore_owner(path, snapshot)
    if owner_note is not None:
        findings.append(owner_note)
    if findings:
        return findings
    return []


def _restore_owner(path: Path | str, snapshot: AclSnapshot) -> str | None:
    """Write the snapshot's owner back **only if it moved**, and return a finding when that fails."""

    if not snapshot.owner:
        return None
    current = capture_acl(path)
    if not current.observed:
        return f"capture_acl before the owner write: {current.reason}"
    if current.owner == snapshot.owner:
        # Nothing to do, and doing it anyway is the one thing measured to fail on an ordinary user's
        # directory (see `restore_acl`). Reporting "the owner already matched" as a success is honest:
        # the descriptor the caller asked for is the descriptor that is there.
        return None
    status, problem = _set_security_info(path, owner=snapshot.owner)
    if problem is not None:
        return problem
    if status != 0:
        return (
            f"SetSecurityInfo (owner {snapshot.owner}, was {current.owner}): {_status_message(status)}"
        )
    return None


