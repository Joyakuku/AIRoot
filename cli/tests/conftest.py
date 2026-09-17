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
import time
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

#: Where every test-created root lives. Overridable through ``AIROOT_TEST_TMP`` since §108: the
#: session fixture below **deletes this whole directory** when the session ends, so two runs sharing
#: it do not merely get untidy — each one deletes the other's roots mid-run, and the failure lands in
#: the *other* run (which is how parallel agents working in one checkout would break each other).
#: Point a parallel run at a subdirectory (``cli/tests/.tmp/<name>``) and it stays inside the ignored
#: path, so nothing it leaves behind can be committed by accident.
TESTS_TMP = Path(os.environ.get("AIROOT_TEST_TMP") or (TESTS_DIR / ".tmp"))

#: How a test tree is removed. `shutil.rmtree(..., ignore_errors=True)` was the whole policy until
#: §139, and it hid a real defect: a SQLite connection still open at teardown keeps
#: `state/registry.db` undeletable on Windows, so the session silently left the remains of a root
#: behind — the walk deletes everything else, then stops at the locked file, which is why every
#: leftover contained exactly `state/registry.db` and nothing else. Cleanup that cannot clean must
#: say so; a locked file is a defect in the test (or in its harness), not an environment quirk.
_TREE_REMOVE_ATTEMPTS = 10
_TREE_REMOVE_PAUSE_SECONDS = 0.15


def remove_test_tree(
    path: Path,
    *,
    attempts: int = _TREE_REMOVE_ATTEMPTS,
    pause: float = _TREE_REMOVE_PAUSE_SECONDS,
) -> None:
    """Delete a test tree, retrying transient locks, then **report** what is still there.

    The retry is not forgiveness: it distinguishes "another process is holding this for a moment"
    from "something in this process never let go", and only the second one is a failure. The
    assertion names what survived, because the alternative — the silence this replaced — cost a
    whole stage to notice (draft §139).
    """

    last: OSError | None = None
    for _ in range(max(attempts, 1)):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as error:  # a locked file, a refused ACL, a path that is not a tree
            last = error
            time.sleep(pause)
    remaining = sorted(str(item) for item in path.rglob("*")) if path.exists() else []
    raise AssertionError(
        f"the test tree {path} could not be removed after {attempts} attempt(s): {last!r}; "
        f"still present: {remaining[:5]}. A locked file means something in this process still holds "
        "a handle, and a refused delete means a DACL is in the way — fix that instead of swallowing "
        "the failure (draft §139)."
    )


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
    remove_test_tree(TESTS_TMP)


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
    remove_test_tree(path)


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
    try:
        yield handle
    finally:
        # §139: the fixture closes what the fixture opened. Left to the garbage collector this
        # usually worked — refcounting gets there first — which is exactly why the failures
        # were rare, silent and confusing: whichever test still held a reference at teardown
        # kept `state/registry.db` locked, and the swallowing teardown left the remains of
        # that root behind (the rest of the tree deletes fine, which is why the leftovers
        # contained nothing but `state/`).
        handle.close()
    handle.close()
