"""Read-only ACL observation for data roots (ADR-0023).

Planning §8.2 requires that "``doctor`` 必须检查 ACL 是否偏离", and the steward draft records an
``acl_baseline`` on every data root as an **observed value**. This module is the observation half.
It is deliberately read-only: imposing a baseline needs ``WRITE_DAC`` behind the P2 broker, and the
butler model says AIROOT may notice a change in the user's directory, not revert it.

Two properties matter more than the parsing:

* **Failure is data.** ``GetNamedSecurityInfoW`` reports a status code rather than raising, and a
  directory's ACL can legitimately be unreadable. Every failure returns a ``reason`` and never
  raises — a caller that cannot observe has to be able to say so instead of falling silent.
* **ACE order is preserved, on purpose.** Windows ACE order carries meaning: a deny before an allow
  is not the same as the allow before the deny. Sorting would launder a meaningful permission change
  into "unchanged", so the canonical form keeps the order and the comparison is order-sensitive. The
  accepted cost is that a pure reordering reports drift; ADR-0023 records that trade.

No third-party dependency: this uses ``ctypes`` against ``advapi32``, exactly as ``paths.py`` reads
volume identity. ``jsonschema`` stays the only runtime dependency.

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

__all__ = [
    "AclEntry",
    "AclSnapshot",
    "acl_digest",
    "capture_acl",
    "differences",
]

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004

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
