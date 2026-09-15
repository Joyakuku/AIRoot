"""Shared fixtures.

Fixture names mirror ``AIROOT-v0.3-验证与测试方案.md`` §3: ``TestRoot``,
``FakeExtension``, ``FakeInstallBackend``, ``FakeClock``, ``FaultInjector`` and
``MachineFixture``. Every fixture works inside ``cli/tests/.tmp`` and is deleted
on teardown; nothing here may touch machine PATH, ACLs or a real AIROOT root.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pytest

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"
CLI_DIR = TESTS_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from airoot import root as root_module  # noqa: E402
from airoot.clock import FakeClock  # noqa: E402

TESTS_TMP = TESTS_DIR / ".tmp"

_MACHINE_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


@dataclass(frozen=True)
class MachineFixture:
    """Stable, injected machine identity (the real algorithm is still open)."""

    machine_id: str = "host-0123456789abcdef0123456789abcdef"
    root_instance_id: str = "root-4f2a9c1d8e3b"
    user_sid: str = "S-1-5-21-1000"
    integrity: str = "medium"


@dataclass
class FakeInstallBackend:
    """Deterministic artifact with controllable failure points."""

    name: str = "fake_fixture"
    version: str = "1.0.0"
    fail_after: str | None = None
    payload: bytes = b"AIROOT-FAKE-TOOL-V1\nThis payload is data and is never executed.\n"
    calls: list[str] = field(default_factory=list)

    def stage(self, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "fake-tool.bin").write_bytes(self.payload)
        (destination / "artifact.json").write_text(
            '{"kind": "managed_tool", "name": "fake-tool", "test_only": true, "version": "%s"}\n' % self.version,
            encoding="utf-8",
        )
        self.calls.append("stage")
        return destination


@dataclass
class FaultInjector:
    """Abort after a named journal state, as required by the test plan §6.1."""

    stop_after: str | None = None

    def checkpoint(self, state: str) -> None:
        if self.stop_after is not None and state == self.stop_after:
            raise InterruptedError(f"fault injected after {state}")


def _read_registry_path(hive: int, subkey: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(hive, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
        return value
    except OSError:
        return None


def host_path_snapshot() -> dict[str, str | None]:
    """Machine and user PATH as stored in the registry (never written by tests)."""

    import winreg

    if sys.platform != "win32":
        return {"machine": None, "user": None}
    return {
        "machine": _read_registry_path(winreg.HKEY_LOCAL_MACHINE, _MACHINE_KEY),
        "user": _read_registry_path(winreg.HKEY_CURRENT_USER, "Environment"),
    }


@pytest.fixture(scope="session", autouse=True)
def _host_mutation_guard() -> Iterator[None]:
    """Fail the session if anything wrote the host PATH (verification plan §3)."""

    before = host_path_snapshot()
    yield
    after = host_path_snapshot()
    assert before == after, f"tests modified host PATH: {before} -> {after}"


@pytest.fixture(autouse=True)
def _environment_guard() -> Iterator[None]:
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(scope="session")
def tests_tmp() -> Iterator[Path]:
    TESTS_TMP.mkdir(parents=True, exist_ok=True)
    yield TESTS_TMP
    shutil.rmtree(TESTS_TMP, ignore_errors=True)


@pytest.fixture
def machine() -> MachineFixture:
    return MachineFixture()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def root_dir(tests_tmp: Path) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="airoot-test-", dir=tests_tmp))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def root(root_dir: Path, machine: MachineFixture, clock: FakeClock) -> Any:
    """A TestRoot with layout + root marker (registry is added by ``registry``)."""

    return root_module.init_root(root_dir, root_instance_id=machine.root_instance_id, machine_id=machine.machine_id, clock=clock)


@pytest.fixture
def registry(root: Any, machine: MachineFixture, clock: FakeClock) -> Iterator[Any]:
    """A TestRoot plus an initialised SQLite registry and its JSON projection."""

    from airoot.registry import Registry

    handle = Registry.initialize(
        root.path,
        machine_id=machine.machine_id,
        root_instance_id=machine.root_instance_id,
        clock=clock,
    )
    handle.update_projection()
    yield handle
    handle.close()
