"""L1: read-only PE static probing (evidence level 2).

The probe must never execute the file it inspects, so these tests assert both the
parsed facts and the absence of any execution: ``subprocess``/``os.system`` are
poisoned for the duration.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from airoot.caps.probe_pe import MACHINE_ARCHITECTURE, is_probably_executable, probe_executable


@pytest.fixture(autouse=True)
def _forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover - only hit on a violation
        raise AssertionError(f"PE probing must never execute anything: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "spawnv", forbidden, raising=False)


def test_interpreter_is_a_pe_with_version_facts() -> None:
    """``sys.executable`` is a real, always-present PE image with version resources."""

    metadata = probe_executable(sys.executable)
    assert metadata.is_pe is True
    assert metadata.errors == [] or "no RT_VERSION resource" not in metadata.errors
    assert metadata.architecture in MACHINE_ARCHITECTURE.values()
    assert metadata.file_version, "CPython ships a version resource"
    assert metadata.size_bytes and metadata.size_bytes > 0
    facts = metadata.facts()
    assert facts.get("architecture") == metadata.architecture


def test_a_path_below_max_path_is_read_rather_than_reported_unreadable(tmp_path: Path) -> None:
    """Draft §38: I/O goes through the extended-length form; the *reported* path stays native."""

    import shutil

    deep = tmp_path / "deep"
    while len(str(deep)) < 300:
        deep = deep / ("d" * 40)
    target = deep / "python.exe"
    try:
        target.parent.mkdir(parents=True)
        shutil.copy2(sys.executable, target)
    except OSError:  # pragma: no cover - depends on the machine's long-path policy
        pytest.skip("this machine cannot create a path longer than MAX_PATH")

    metadata = probe_executable(target)

    assert metadata.is_pe is True, metadata.errors
    assert metadata.size_bytes and metadata.size_bytes > 0
    assert not any("unreadable" in error for error in metadata.errors)
    assert not str(metadata.path).startswith("\\\\?\\"), "the prefix must never reach the answer"


def test_probe_reports_architecture_and_not_dll_for_an_exe() -> None:
    metadata = probe_executable(sys.executable)
    assert metadata.is_dll is False
    assert metadata.machine in MACHINE_ARCHITECTURE
    assert metadata.probe_level == 2


def test_non_pe_file_is_reported_not_raised(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("this is not an executable, it is a text file of some length", encoding="utf-8")

    metadata = probe_executable(text)
    assert metadata.is_pe is False
    assert metadata.probe_level == 0
    assert metadata.architecture is None
    assert any("PE" in item for item in metadata.errors)


def test_tiny_file_is_reported_as_too_small(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.exe"
    tiny.write_text("MZ", encoding="utf-8")
    metadata = probe_executable(tiny)
    assert metadata.is_pe is False
    assert any("too small" in item for item in metadata.errors)


def test_truncated_pe_is_reported_not_raised(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.exe"
    truncated.write_bytes(b"MZ" + b"\x00" * 10)

    metadata = probe_executable(truncated)
    assert metadata.is_pe is False
    assert metadata.errors, "a damaged image must produce evidence, not an exception"


def test_mz_header_with_broken_pe_offset_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "broken.exe"
    payload = bytearray(0x80)
    payload[0:2] = b"MZ"
    payload[0x3C:0x40] = (0x7FFFFFFF).to_bytes(4, "little")  # e_lfanew far past EOF
    broken.write_bytes(bytes(payload))

    metadata = probe_executable(broken)
    assert metadata.is_pe is False
    assert metadata.errors


def test_missing_file_is_reported_not_raised(tmp_path: Path) -> None:
    metadata = probe_executable(tmp_path / "absent.exe")
    assert metadata.is_pe is False
    assert any("unreadable" in item for item in metadata.errors)


def test_empty_file_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.exe"
    empty.write_bytes(b"")
    metadata = probe_executable(empty)
    assert metadata.is_pe is False
    assert metadata.errors


def test_header_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    """Only a bounded header range is read, so huge binaries stay inspectable."""

    from airoot.caps import probe_pe

    monkeypatch.setattr(probe_pe, "MAX_HEADER_BYTES", 16)
    metadata = probe_pe.probe_executable(sys.executable)
    assert metadata.is_pe is False
    assert metadata.errors, "a truncated header read must be reported, not raised"


def test_resource_section_cap_is_reported(monkeypatch) -> None:
    from airoot.caps import probe_pe

    monkeypatch.setattr(probe_pe, "MAX_RESOURCE_SECTION_BYTES", 16)
    metadata = probe_pe.probe_executable(sys.executable)
    assert metadata.is_pe is True, "headers are still parsed"
    assert any("inspection cap" in item for item in metadata.errors)


def test_large_binary_is_probed_from_headers_only(tmp_path: Path, monkeypatch) -> None:
    """A file far larger than the header cap must still yield PE facts."""

    from airoot.caps import probe_pe

    big = tmp_path / "big.exe"
    big.write_bytes(Path(sys.executable).read_bytes())
    original_size = big.stat().st_size
    with big.open("ab") as handle:  # pad to simulate a large binary
        handle.write(b"\x00" * (3 * 1024 * 1024))

    metadata = probe_pe.probe_executable(big)
    assert metadata.size_bytes > original_size
    assert metadata.is_pe is True
    assert metadata.architecture == probe_pe.probe_executable(sys.executable).architecture


def test_cheap_prefilter(tmp_path: Path) -> None:
    real = tmp_path / "real.exe"
    real.write_bytes(b"MZ" + b"\x00" * 4)
    fake = tmp_path / "fake.exe"
    fake.write_text("plain text", encoding="utf-8")

    assert is_probably_executable(real) is True
    assert is_probably_executable(fake) is False
    assert is_probably_executable(tmp_path / "absent.exe") is False


def test_facts_omits_unknown_values() -> None:
    metadata = probe_executable(sys.executable)
    for value in metadata.facts().values():
        assert value not in (None, "")
