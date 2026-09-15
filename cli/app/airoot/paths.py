"""Path canonicalization, root containment and volume identity.

Rules enforced here (docs, "路径序列化也必须固定" and §8.1):

* registry stores root-relative canonical paths; JSON output defaults to absolute;
* a path must never escape the root, and never be a Windows UNC path;
* reparse points are rejected where the core needs a trustworthy location;
* volume identity is read for the root guard, never written.
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
from ctypes import wintypes
from pathlib import Path

from .exits import AirootError

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _norm(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def is_within(child: Path, parent: Path) -> bool:
    """Case-insensitive containment test (Windows semantics)."""

    child_n = _norm(child)
    parent_n = _norm(parent)
    if child_n == parent_n:
        return True
    try:
        return os.path.commonpath([child_n, parent_n]) == parent_n
    except ValueError:  # different drives
        return False


def is_unc(path: str | Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


#: The Windows extended-length prefix. It is used **only** for syscalls on paths that may belong to
#: user data: a walk that depended on the machine's `LongPathsEnabled` policy would answer
#: differently on different machines (draft §36/§38). Reported paths never carry it.
EXTENDED_PREFIX = "\\\\?\\"


def extended_path(path: str | Path) -> str:
    """`\\\\?\\`-prefixed form for the syscalls.

    Two things this buys: a path deeper than `MAX_PATH` works even when the machine's long-path
    policy is off, and directory entries keep the prefix while a walk descends, so no per-segment
    length check is needed. `native_path` undoes it for reporting.
    """

    raw = str(path)
    if os.name != "nt" or raw.startswith(EXTENDED_PREFIX):
        return raw
    absolute = os.path.abspath(raw)
    if absolute.startswith("\\\\"):
        # A UNC path takes the documented `\\?\UNC\server\share` form.
        return EXTENDED_PREFIX + "UNC" + absolute[1:]
    return EXTENDED_PREFIX + absolute


def native_path(path: str | Path) -> str:
    """The inverse of `extended_path`: what a caller (and any JSON field) should see."""

    raw = str(path)
    if not raw.startswith(EXTENDED_PREFIX):
        return raw
    remainder = raw[len(EXTENDED_PREFIX) :]
    if remainder.upper().startswith("UNC\\"):
        return "\\" + remainder[3:]
    return remainder


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = os.lstat(path).st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def canonicalize(path: str | Path, *, root: Path | None = None, must_exist: bool = False) -> Path:
    """Absolute, symlink-resolved, root-contained path or :class:`AirootError`."""

    if is_unc(path):
        raise AirootError("UNC_NOT_ALLOWED", f"UNC paths are not accepted: {path}")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AirootError("INVALID_INPUT", f"path must be absolute: {path}")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise AirootError("INVALID_INPUT", f"cannot resolve path: {path}", evidence=[str(exc)]) from exc
    if root is not None and not is_within(resolved, Path(root)):
        raise AirootError(
            "PATH_ESCAPES_ROOT",
            f"path escapes the AIROOT root: {resolved}",
            evidence=[f"root={Path(root)}"],
        )
    return resolved


def reject_reparse_chain(path: Path, *, root: Path | None = None) -> None:
    """Reject a location whose own entry (or any component under ``root``) is a reparse point."""

    resolved = Path(path)
    if is_reparse_point(resolved):
        raise AirootError("REPARSE_POINT_REJECTED", f"reparse point is not accepted: {resolved}")
    if root is None:
        return
    root = Path(root)
    for candidate in [resolved, *resolved.parents]:
        if not is_within(candidate, root):
            break
        if is_reparse_point(candidate):
            raise AirootError("REPARSE_POINT_REJECTED", f"reparse point under root: {candidate}")


def relative_to_root(path: Path, root: Path) -> str:
    """Root-relative, forward-slashed, canonical form stored in the registry."""

    resolved = canonicalize(path, root=root, must_exist=False)
    relative = resolved.relative_to(Path(root).resolve())
    return relative.as_posix()


def from_root_relative(relative: str, root: Path) -> Path:
    """Inverse of :func:`relative_to_root`, with traversal rejection."""

    if not relative or relative.startswith(("/", "\\")) or is_unc(relative):
        raise AirootError("PATH_ESCAPES_ROOT", f"not a root-relative path: {relative}")
    parts = [part for part in relative.replace("\\", "/").split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise AirootError("PATH_ESCAPES_ROOT", f"path traversal rejected: {relative}")
    return canonicalize(Path(root).joinpath(*parts), root=root, must_exist=False)


def volume_serial(path: Path) -> str:
    """Filesystem volume serial for ``path``, used by the root identity guard."""

    if sys.platform != "win32":
        return "nonwindows-0000"
    resolved = Path(path).resolve()
    drive = os.path.splitdrive(str(resolved))[0]
    if not drive:
        raise AirootError("VOLUME_IDENTITY_MISMATCH", f"cannot determine volume for {resolved}")
    serial = wintypes.DWORD(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        ctypes.c_wchar_p(drive + "\\"),
        None,
        0,
        ctypes.byref(serial),
        None,
        None,
        None,
        0,
    )
    if not ok:
        raise AirootError(
            "VOLUME_IDENTITY_MISMATCH",
            f"cannot read volume identity for {drive}",
            evidence=[f"path={resolved}"],
        )
    return f"{serial.value:08x}"
