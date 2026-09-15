"""A read-only USN capability probe (draft §34, ADR-0020).

Why this exists: the search protocol's *fast* implementation needs NTFS metadata and the USN journal,
and this build does not have it. "Why not" should be answerable by the tool rather than by prose.

Three rules shape this module, and each one comes from something that already went wrong once:

* **read-only, and only on request.** The default path opens no volume handle: a diagnostic that
  "just checks" is a side effect nobody asked for. `query=True` is the explicit opt-in.
* **failure is data, never an exception.** A missing volume, a non-NTFS filesystem, an access denial
  or a rejected control code all become structured fields. A probe that raises teaches the caller
  nothing about the machine (the PE-probe lesson, draft §16.0).
* **no binary verdict about what was not tested.** The report keeps the raw observations
  (`elevated`, handle access, `unprivileged_read`) instead of collapsing them into "supported".

The control codes and structures below are the ones a one-off probe confirmed working on a real
machine (ADR-0020): `FSCTL_QUERY_USN_JOURNAL`, `FSCTL_READ_USN_JOURNAL` and `FSCTL_ENUM_USN_DATA`
all succeeded there with a `GENERIC_READ` volume handle, while `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL`
was recognised but rejected the documented V0/V1 input. That last one stays an observation, not a
conclusion, so it is reported verbatim.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FILE_DEVICE_FILE_SYSTEM = 0x00000009

GENERIC_READ = 0x80000000
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


def ctl_code(function: int, *, method: int = 3, access: int = 0) -> int:
    """`CTL_CODE` for FILE_DEVICE_FILE_SYSTEM; `method=3` is METHOD_NEITHER, as the USN codes use."""

    return (FILE_DEVICE_FILE_SYSTEM << 16) | (access << 14) | (function << 2) | method


FSCTL_QUERY_USN_JOURNAL = ctl_code(61, method=0)
FSCTL_ENUM_USN_DATA = ctl_code(44)
FSCTL_READ_USN_JOURNAL = ctl_code(46)
FSCTL_READ_UNPRIVILEGED_USN_JOURNAL = ctl_code(55)

#: `ERROR_INVALID_FUNCTION` — the control code is not implemented for this handle.
ERROR_INVALID_FUNCTION = 1
#: `ERROR_ACCESS_DENIED` — the handle could not be opened with the access we asked for.
ERROR_ACCESS_DENIED = 5
#: `ERROR_INVALID_PARAMETER` — the code exists, the input was rejected.
ERROR_INVALID_PARAMETER = 87

DRIVE_FIXED = 3


@dataclass(frozen=True)
class UsnProbe:
    """What one read-only look at a volume actually showed."""

    volume: str
    checked: bool = False
    filesystem: str | None = None
    drive_type: int | None = None
    elevated: bool | None = None
    volume_handle_access: str | None = None
    journal_present: bool | None = None
    journal_id: int | None = None
    first_usn: int | None = None
    next_usn: int | None = None
    enumeration_bytes: int | None = None
    unprivileged_read: str | None = None
    reason: str = "not probed"
    observations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def native_index_available(self) -> bool:
        """A native index needs a readable journal *and* the ability to enumerate the volume once."""

        return bool(self.journal_present and (self.enumeration_bytes or 0) > 0)

    def to_document(self) -> dict[str, Any]:
        return {
            "volume": self.volume,
            "checked": self.checked,
            "filesystem": self.filesystem,
            "drive_type": self.drive_type,
            "elevated": self.elevated,
            "volume_handle_access": self.volume_handle_access,
            "journal_present": self.journal_present,
            "journal_id": self.journal_id,
            "first_usn": self.first_usn,
            "next_usn": self.next_usn,
            "enumeration_bytes": self.enumeration_bytes,
            "unprivileged_read": self.unprivileged_read,
            "native_index_available": self.native_index_available,
            "reason": self.reason,
            "observations": list(self.observations),
        }


class _UsnJournalDataV0(ctypes.Structure):
    _fields_ = [
        ("UsnJournalID", ctypes.c_ulonglong),
        ("FirstUsn", ctypes.c_longlong),
        ("NextUsn", ctypes.c_longlong),
        ("LowestValidUsn", ctypes.c_longlong),
        ("MaxUsn", ctypes.c_longlong),
        ("MaximumSize", ctypes.c_ulonglong),
        ("AllocationDelta", ctypes.c_ulonglong),
    ]


class _MftEnumDataV0(ctypes.Structure):
    _fields_ = [
        ("StartFileReferenceNumber", ctypes.c_ulonglong),
        ("LowUsn", ctypes.c_longlong),
        ("HighUsn", ctypes.c_longlong),
    ]


class _ReadUsnJournalDataV1(ctypes.Structure):
    _fields_ = [
        ("StartUsn", ctypes.c_longlong),
        ("ReasonMask", ctypes.c_ulong),
        ("ReturnOnlyOnClose", ctypes.c_ulong),
        ("Timeout", ctypes.c_ulonglong),
        ("BytesToWaitFor", ctypes.c_ulonglong),
        ("UsnJournalID", ctypes.c_ulonglong),
        ("MinMajorVersion", ctypes.c_ushort),
        ("MaxMajorVersion", ctypes.c_ushort),
    ]


def system_volume() -> str:
    drive = os.environ.get("SystemDrive") or "C:"
    return str(drive).rstrip("\\") + "\\"


def default_volume() -> str:
    return system_volume()


def probe(
    volume: str | None = None,
    *,
    query: bool = False,
    prober: Callable[[str], UsnProbe] | None = None,
) -> UsnProbe:
    """Look at `volume` once, read-only.

    Without `query=True` this returns "not probed" and touches nothing — the caller has to ask for
    the volume I/O explicitly.
    """

    target = volume or default_volume()
    if not query:
        return UsnProbe(volume=target, checked=False, reason="not probed (pass query=True)")
    if prober is not None:
        return prober(target)
    if os.name != "nt":  # pragma: no cover - v1 is a Windows provider
        return UsnProbe(volume=target, checked=True, reason="USN journals are a Windows/NTFS feature")
    return _windows_probe(target)


def _windows_probe(volume: str) -> UsnProbe:
    """The real thing. Every failure below becomes a field rather than an exception."""

    observations: list[str] = []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p

    elevated: bool | None
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - defensive
        elevated = None

    filesystem: str | None = None
    drive_type: int | None = None
    try:
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong(0)
        maxlen = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)
        if kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(volume),
            name,
            261,
            ctypes.byref(serial),
            ctypes.byref(maxlen),
            ctypes.byref(flags),
            fs,
            261,
        ):
            filesystem = fs.value
        drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(volume)))
    except Exception as error:  # pragma: no cover - defensive
        observations.append(f"volume information unavailable: {error.__class__.__name__}")

    if filesystem and filesystem.upper() != "NTFS":
        return UsnProbe(
            volume=volume,
            checked=True,
            filesystem=filesystem,
            drive_type=drive_type,
            elevated=elevated,
            journal_present=False,
            reason=f"the change journal exists only on NTFS (this volume is {filesystem})",
            observations=tuple(observations),
        )

    device = "\\\\.\\" + volume.rstrip("\\")
    handle = kernel32.CreateFileW(
        ctypes.c_wchar_p(device),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    access = "GENERIC_READ"
    if not handle or handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        observations.append(f"CreateFileW({device}, GENERIC_READ) failed: err={error}")
        if error == ERROR_ACCESS_DENIED:
            # The documented reason a non-elevated process cannot enumerate a volume. This build
            # does not claim to have verified that from a non-elevated process (ADR-0020).
            observations.append("access denied: enumerating a volume is documented to need elevation")
        return UsnProbe(
            volume=volume,
            checked=True,
            filesystem=filesystem,
            drive_type=drive_type,
            elevated=elevated,
            volume_handle_access=None,
            journal_present=False,
            reason=f"cannot open the volume read-only (err={error})",
            observations=tuple(observations),
        )

    try:
        journal = _UsnJournalDataV0()
        out = ctypes.create_string_buffer(128)
        returned = ctypes.c_ulong(0)
        ok = kernel32.DeviceIoControl(
            ctypes.c_void_p(handle),
            ctypes.c_ulong(FSCTL_QUERY_USN_JOURNAL),
            None,
            0,
            out,
            128,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            error = ctypes.get_last_error()
            observations.append(f"FSCTL_QUERY_USN_JOURNAL failed: err={error}")
            return UsnProbe(
                volume=volume,
                checked=True,
                filesystem=filesystem,
                drive_type=drive_type,
                elevated=elevated,
                volume_handle_access=access,
                journal_present=False,
                reason=f"no readable change journal (err={error})",
                observations=tuple(observations),
            )
        ctypes.memmove(ctypes.byref(journal), out.raw, ctypes.sizeof(journal))
        observations.append(
            f"journal id={journal.UsnJournalID} first_usn={journal.FirstUsn} next_usn={journal.NextUsn}"
        )

        enumeration = _MftEnumDataV0(
            StartFileReferenceNumber=0, LowUsn=0, HighUsn=int(journal.NextUsn)
        )
        enum_out = ctypes.create_string_buffer(65536)
        enum_returned = ctypes.c_ulong(0)
        enum_ok = kernel32.DeviceIoControl(
            ctypes.c_void_p(handle),
            ctypes.c_ulong(FSCTL_ENUM_USN_DATA),
            ctypes.byref(enumeration),
            ctypes.sizeof(enumeration),
            enum_out,
            65536,
            ctypes.byref(enum_returned),
            None,
        )
        if not enum_ok:
            observations.append(f"FSCTL_ENUM_USN_DATA failed: err={ctypes.get_last_error()}")
        else:
            observations.append("volume enumeration succeeded (this is the privileged step)")

        unprivileged = "not attempted"
        data = _ReadUsnJournalDataV1(
            StartUsn=int(journal.FirstUsn),
            ReasonMask=0xFFFFFFFF,
            ReturnOnlyOnClose=0,
            Timeout=0,
            BytesToWaitFor=0,
            UsnJournalID=int(journal.UsnJournalID),
            MinMajorVersion=2,
            MaxMajorVersion=4,
        )
        unprivileged_out = ctypes.create_string_buffer(65536)
        unprivileged_returned = ctypes.c_ulong(0)
        unprivileged_ok = kernel32.DeviceIoControl(
            ctypes.c_void_p(handle),
            ctypes.c_ulong(FSCTL_READ_UNPRIVILEGED_USN_JOURNAL),
            ctypes.byref(data),
            ctypes.sizeof(data),
            unprivileged_out,
            65536,
            ctypes.byref(unprivileged_returned),
            None,
        )
        if unprivileged_ok:
            unprivileged = f"ok ({int(unprivileged_returned.value)} bytes)"
        else:
            unprivileged = f"rejected (err={ctypes.get_last_error()})"
            observations.append(
                "FSCTL_READ_UNPRIVILEGED_USN_JOURNAL was recognised but rejected the documented "
                "input; recorded as an observation, not as a capability verdict"
            )

        return UsnProbe(
            volume=volume,
            checked=True,
            filesystem=filesystem,
            drive_type=drive_type,
            elevated=elevated,
            volume_handle_access=access,
            journal_present=True,
            journal_id=int(journal.UsnJournalID),
            first_usn=int(journal.FirstUsn),
            next_usn=int(journal.NextUsn),
            enumeration_bytes=int(enum_returned.value) if enum_ok else None,
            unprivileged_read=unprivileged,
            reason=(
                "the journal is readable and the volume can be enumerated, so a native index could "
                "be built here — by a broker, not by this unprivileged build (ADR-0020)"
            ),
            observations=tuple(observations),
        )
    except Exception as error:  # pragma: no cover - defensive
        return UsnProbe(
            volume=volume,
            checked=True,
            filesystem=filesystem,
            drive_type=drive_type,
            elevated=elevated,
            volume_handle_access=access,
            reason=f"probe failed: {error.__class__.__name__}",
            observations=tuple(observations),
        )
    finally:
        try:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:  # pragma: no cover - defensive
            pass


def native_candidate(repo_policy_path: Path | None = None) -> dict[str, Any]:
    """The protocol's target implementation, as declared in policy rather than hard-coded."""

    from .search import load_search_policy

    policy = load_search_policy(repo_policy_path)
    candidate = dict(policy.index_setting("native_candidate", {}) or {})
    return {
        "implementation_id": candidate.get("implementation_id", "airoot-native-search-native-index"),
        "implementation_kind": candidate.get("implementation_kind", "native"),
        "requires": list(candidate.get("requires", ["ntfs_metadata", "usn_journal"])),
        "available": False,
        # `checked` distinguishes "we did not look" from "we looked and it is unusable": the report
        # must not collapse those two into one boolean (draft §34.3-4).
        "checked": False,
        "reason_code": "SEARCH_BACKEND_UNAVAILABLE",
        "reason": (
            "not built in this build: the initial volume enumeration needs a privileged volume "
            "handle, so it belongs to the P2 broker (ADR-0020). Probe with "
            "`airoot search status --probe-native-index`."
        ),
        "probe_hint": "airoot search status --probe-native-index --json",
    }


__all__ = [
    "ERROR_ACCESS_DENIED",
    "ERROR_INVALID_FUNCTION",
    "ERROR_INVALID_PARAMETER",
    "FSCTL_ENUM_USN_DATA",
    "FSCTL_QUERY_USN_JOURNAL",
    "FSCTL_READ_UNPRIVILEGED_USN_JOURNAL",
    "FSCTL_READ_USN_JOURNAL",
    "UsnProbe",
    "ctl_code",
    "default_volume",
    "native_candidate",
    "probe",
    "system_volume",
]
