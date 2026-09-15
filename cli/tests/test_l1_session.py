"""L1: session activation with a snapshot stack (规划 §16.4, draft §25).

§16.4 asks for four things beyond "print a script": the environment **diff**, the **source
generation**, a **pre-activation snapshot id**, and **deactivate information** — plus a nesting
stack and ``SESSION_STATE_STALE`` when the world moved underneath it. These tests hold all of
them, and the rule that matters most: deactivation **restores** rather than deletes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.session import (
    SESSIONS_DIR,
    pop_frames,
    push_frame,
    read_stack,
    restore_script,
    session_path,
    write_stack,
)
from airoot.cli import main
from airoot.exits import AirootError, exit_code_for
from airoot.registry import ExternalReference

JAVA_ENTRY = {"variables": {"JAVA_HOME": "<entrypoint_dir_parent>"}, "path_prepend": []}


class FakeStore:
    """A stand-in for HKCU: the session snapshot must never need the real one."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def read(self, name: str):
        from airoot.caps.environment import StoredValue

        if name.upper() in {key.upper() for key in self.values}:
            actual = next(key for key in self.values if key.upper() == name.upper())
            return StoredValue(name=name, value=self.values[actual], kind="REG_EXPAND_SZ")
        return None


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def java_reference(registry, tests_tmp: Path) -> str:
    """One reference with a whitelist environment block (java declares JAVA_HOME)."""

    data_root = tests_tmp / "session-env"
    object_root = data_root / "Java" / "jdk-25.0.2"
    entrypoint = object_root / "bin" / "java.exe"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_bytes(b"MZ-placeholder")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/java",
                capability_id="java",
                path=str(data_root / "Java"),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="25.0.2.0",
                active_version="25.0.2.0",
                entrypoints=("jdk-25.0.2/bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )
    return "external/dr-env/java"


# --------------------------------------------------------------------------- #
# the stack
# --------------------------------------------------------------------------- #


def test_a_frame_records_what_it_replaced(tmp_path: Path) -> None:
    store = FakeStore({"JAVA_HOME": "C:\\old-java"})
    frame, frames = push_frame(
        tmp_path,
        session_id="s-1",
        external_id="external/x",
        capability_id="java",
        variables={"JAVA_HOME": "D:\\new-java"},
        path_variable="Path",
        path_entries=["D:\\new-java\\bin"],
        read_current=store.read,
        generation=7,
    )

    assert frame.variables["JAVA_HOME"].old == "C:\\old-java"
    assert frame.variables["JAVA_HOME"].existed is True
    assert frame.snapshot_id.startswith("snap/")
    assert frame.generation == 7
    assert len(frames) == 1
    assert session_path(tmp_path, "s-1").is_file()


def test_reactivating_the_same_reference_does_not_stack_twice(tmp_path: Path) -> None:
    store = FakeStore()
    for _ in range(3):
        _frame, frames = push_frame(
            tmp_path,
            session_id="s-1",
            external_id="external/x",
            capability_id="java",
            variables={"JAVA_HOME": "D:\\new"},
            path_variable="Path",
            path_entries=[],
            read_current=store.read,
            generation=1,
        )

    assert len(frames) == 1, "a repeated activate is a no-op, not a second layer"


def test_nested_frames_pop_newest_first(tmp_path: Path) -> None:
    store = FakeStore()
    for name in ("external/a", "external/b"):
        push_frame(
            tmp_path,
            session_id="s-1",
            external_id=name,
            capability_id="java",
            variables={name: "1"},
            path_variable="Path",
            path_entries=[],
            read_current=store.read,
            generation=1,
        )

    popped, remaining = pop_frames(tmp_path, session_id="s-1")
    assert [frame.external_id for frame in popped] == ["external/b"]
    assert [frame.external_id for frame in remaining] == ["external/a"]

    popped_all, remaining_all = pop_frames(tmp_path, session_id="s-1", all_frames=True)
    assert [frame.external_id for frame in popped_all] == ["external/a"]
    assert remaining_all == []
    assert not session_path(tmp_path, "s-1").exists(), "an empty stack leaves no file"


def test_an_unreadable_stack_is_stale_not_silent(tmp_path: Path) -> None:
    path = session_path(tmp_path, "s-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        read_stack(tmp_path, "s-1")
    assert caught.value.reason_code == "SESSION_STATE_STALE"
    assert exit_code_for("SESSION_STATE_STALE") == 2


def test_a_stack_from_another_version_is_stale(tmp_path: Path) -> None:
    path = session_path(tmp_path, "s-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 99, "frames": []}), encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        read_stack(tmp_path, "s-1")
    assert caught.value.reason_code == "SESSION_STATE_STALE"


def test_restoring_a_variable_that_did_not_exist_removes_it(tmp_path: Path) -> None:
    store = FakeStore()
    frame, _frames = push_frame(
        tmp_path,
        session_id="s-1",
        external_id="external/x",
        capability_id="java",
        variables={"JAVA_HOME": "D:\\new"},
        path_variable="Path",
        path_entries=[],
        read_current=store.read,
        generation=1,
    )

    script = restore_script([frame], shell="powershell")
    assert "Remove-Item Env:JAVA_HOME" in script

    store2 = FakeStore({"JAVA_HOME": "C:\\old"})
    frame2, _ = push_frame(
        tmp_path,
        session_id="s-2",
        external_id="external/x",
        capability_id="java",
        variables={"JAVA_HOME": "D:\\new"},
        path_variable="Path",
        path_entries=[],
        read_current=store2.read,
        generation=1,
    )
    assert "$env:JAVA_HOME = 'C:\\old'" in restore_script([frame2], shell="powershell")


def test_write_stack_is_removed_when_the_stack_empties(tmp_path: Path) -> None:
    write_stack(tmp_path, "s-1", [])
    assert not (tmp_path / SESSIONS_DIR / "s-1.json").exists()


def test_a_session_id_with_unsafe_characters_is_sanitised(tmp_path: Path) -> None:
    path = session_path(tmp_path, "a/b\\c:d")

    assert path.parent.name == "sessions"
    assert "/" not in path.name and "\\" not in path.name and ":" not in path.name


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_activate_records_a_snapshot_when_a_session_is_given(
    capsys, cli_root: Path, java_reference: str
) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "env", "activate", java_reference, "--session", "s-1",
    )

    assert code == 0, document
    assert document["session_id"] == "s-1"
    assert document["snapshot_id"].startswith("snap/")
    assert document["source_generation"] == 0
    assert document["stack_depth"] == 1
    assert "diff" in document
    assert document["deactivate"]["snapshot_id"] == document["snapshot_id"]
    assert "env deactivate --session s-1" in document["deactivate"]["command"]
    assert (cli_root / SESSIONS_DIR / "s-1.json").is_file()


def test_cli_activate_without_a_session_stays_stateless(
    capsys, cli_root: Path, java_reference: str
) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "env", "activate", java_reference)

    assert code == 0
    assert document["snapshot_id"] is None
    assert document["stack_depth"] == 0
    assert not (cli_root / SESSIONS_DIR).exists(), "no --session means nothing is recorded"


def test_cli_deactivate_prints_the_restore_script(
    capsys, cli_root: Path, java_reference: str
) -> None:
    run(capsys, "--json", "--root", str(cli_root), "env", "activate", java_reference, "--session", "s-1")

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "s-1"
    )

    assert code == 0, document
    assert document["deactivated"] == [java_reference]
    assert document["stack_depth"] == 0
    assert "JAVA_HOME" in document["restored"]["variables"]
    assert document["script"]
    assert not session_path(cli_root, "s-1").exists()


def test_cli_deactivate_on_an_empty_stack_is_a_no_op(capsys, cli_root: Path) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "never-used"
    )

    assert code == 0
    assert document["deactivated"] == []
    assert "already empty" in document["note"]


def test_cli_deactivate_reports_a_stale_session(
    capsys, cli_root: Path, java_reference: str, registry
) -> None:
    run(capsys, "--json", "--root", str(cli_root), "env", "activate", java_reference, "--session", "s-1")
    # Move the world: the declared state advances after the activation was recorded.
    with registry.write(expected_generation=registry.generation, bump=True):
        pass

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "s-1"
    )

    assert code == 2, document
    assert document["reason_code"] == "SESSION_STATE_STALE"
    assert "generation" in " ".join(document["evidence"])
    assert session_path(cli_root, "s-1").is_file(), "a refused deactivate leaves the stack alone"


def test_cli_nested_activation_and_deactivation(
    capsys, cli_root: Path, java_reference: str, registry, tests_tmp: Path
) -> None:
    second = tests_tmp / "session-env-2" / "Java" / "jdk-26"
    (second / "bin").mkdir(parents=True, exist_ok=True)
    (second / "bin" / "java.exe").write_bytes(b"MZ-placeholder")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/java2",
                capability_id="java",
                path=str(second.parent),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="26.0.0.0",
                entrypoints=("jdk-26/bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )

    run(capsys, "--json", "--root", str(cli_root), "env", "activate", java_reference, "--session", "s-1")
    _code, second_doc = run(
        capsys, "--json", "--root", str(cli_root), "env", "activate", "external/dr-env/java2", "--session", "s-1"
    )
    assert second_doc["stack_depth"] == 2

    code, popped = run(capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "s-1")
    assert code == 0
    assert popped["deactivated"] == ["external/dr-env/java2"]
    assert popped["stack_depth"] == 1, "the first activation is still active"

    code, cleared = run(
        capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "s-1", "--all"
    )
    assert code == 0
    assert cleared["deactivated"] == [java_reference]
    assert cleared["stack_depth"] == 0


def test_the_session_store_never_touches_the_registry_or_host_environment(
    capsys, cli_root: Path, java_reference: str, registry
) -> None:
    generation_before = registry.generation
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference  # noqa: B018 - the writer exists; we assert it is unused
    run(capsys, "--json", "--root", str(cli_root), "env", "activate", java_reference, "--session", "s-1")
    run(capsys, "--json", "--root", str(cli_root), "env", "deactivate", "--session", "s-1")

    assert registry.generation == generation_before, "session activation never changes declared state"
    assert registry.environment_persist_records() == [], "and never persists anything"
