"""L1: path containment is decided in one spelling, not two (draft §126).

`canonicalize` resolves the candidate and compares it with the root it was handed. On Windows the same
directory has two spellings -- an 8.3 short name and the long name -- and `resolve()` expands one of
them. Resolving only the candidate therefore reports a legal path as an escape; this is the guard for
that, and it was red before the fix on any machine whose `TEMP` is a short path.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from airoot.paths import canonicalize, from_root_relative


def _short_name(path: Path) -> str | None:
    """The 8.3 spelling of ``path``, or ``None`` where the volume does not have one."""

    if sys.platform != "win32":  # pragma: no cover - v1 is Windows only
        return None
    buffer = ctypes.create_unicode_buffer(2048)
    written = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 2048)
    if not written or not buffer.value:
        return None
    return buffer.value


def test_a_root_spelled_two_ways_does_not_make_a_child_an_escape(tmp_path: Path) -> None:
    (tmp_path / "store").mkdir()
    short = _short_name(tmp_path)
    if short is None or Path(short) == tmp_path:
        pytest.skip("this volume gives no second spelling for the root; the case cannot be built here")

    relative = from_root_relative("store", Path(short))
    assert relative.is_dir(), "the short spelling of the root names the same directory"
    assert relative == canonicalize(tmp_path / "store", root=Path(short))
    assert relative == canonicalize(tmp_path / "store", root=tmp_path), (
        "both spellings must accept the same child, or 'inside' means two different things"
    )


def test_a_child_outside_the_root_is_still_refused(tmp_path: Path) -> None:
    """The fix must not turn the check off: an actual escape still raises."""

    from airoot.exits import AirootError

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    with pytest.raises(AirootError) as info:
        canonicalize(outside, root=root)
    assert info.value.reason_code == "PATH_ESCAPES_ROOT"
