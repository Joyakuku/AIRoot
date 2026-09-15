"""CLI contract for ``env`` / ``exec`` (draft §13).

The host-mutation guard in ``conftest.py`` is the real assertion here: none of these
commands may write ``HKCU\\...\\Environment`` in the successful test paths. The
persisted write is exercised through the module API with an injected store (see
``test_l1_exposure.py``), and through the CLI only up to the approval boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.environment import InMemoryEnvironmentStore
from airoot.caps.exposure import (
    ExposureTarget,
    apply_reference_plan,
    build_reference_plan,
    request_from_entry,
)
from airoot.cli import main
from airoot.paths import volume_serial
from airoot.registry import ExternalReference
from airoot.registry.entities import DataRoot

JAVA_ENTRY = {"variables": {"JAVA_HOME": "<object_root>"}, "path_prepend": ["bin"]}


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def data_root(tests_tmp: Path) -> Path:
    path = tests_tmp / "cli-env-data-root"
    (path / "java" / "bin").mkdir(parents=True, exist_ok=True)
    (path / "java" / "bin" / "java.exe").write_bytes(b"MZ-placeholder-never-executed\n")
    return path


@pytest.fixture
def registered_reference(registry, data_root: Path) -> Path:
    object_root = data_root / "java"
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-3",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/java",
                capability_id="java",
                path=str(object_root),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="25.0.2.0",
                architecture="x64",
                entrypoints=("bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )
    return object_root


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


def env(capsys, cli_root: Path, *argv: str) -> tuple[int, dict]:
    return run(capsys, "--json", "--root", str(cli_root), "env", *argv)


def persist_module(registry, object_root: Path, store: InMemoryEnvironmentStore):
    target = ExposureTarget(
        external_id="external/dr-env/java",
        capability_id="java",
        path=object_root,
        object_root=object_root,
        entrypoint_dir=object_root / "bin",
    )
    request = request_from_entry(target=target, entry=JAVA_ENTRY, data_roots=(object_root.parent,))
    plan = build_reference_plan(request, registry=registry)
    return apply_reference_plan(plan, registry=registry, store=store)


# --------------------------------------------------------------------------- #
# session activation
# --------------------------------------------------------------------------- #


def test_env_activate_prints_a_powershell_script_and_persists_nothing(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    code = main(
        ["--root", str(cli_root), "env", "activate", "external/dr-env/java", "--shell", "powershell"]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert f"$env:JAVA_HOME = '{registered_reference}'" in captured.out
    assert f"$env:Path = '{registered_reference / 'bin'};' + $env:Path" in captured.out
    assert registry.environment_persist_records() == []


def test_env_activate_cmd_shell(capsys, cli_root: Path, registered_reference: Path) -> None:
    code = main(["--root", str(cli_root), "env", "activate", "external/dr-env/java", "--shell", "cmd"])
    captured = capsys.readouterr()

    assert code == 0
    assert f'set "JAVA_HOME={registered_reference}"' in captured.out
    assert 'set "Path=' in captured.out


def test_env_activate_json_reports_the_session_scope(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    code, document = env(capsys, cli_root, "activate", "external/dr-env/java")

    assert code == 0
    assert document["scope"] == "session"
    assert document["persisted"] is False
    assert document["variables"] == {"JAVA_HOME": str(registered_reference)}


# --------------------------------------------------------------------------- #
# persisted environment: the approval boundary
# --------------------------------------------------------------------------- #


def test_env_persist_without_a_token_stops_at_the_approval_boundary(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    code, document = env(capsys, cli_root, "persist", "external/dr-env/java")

    assert code == 4
    assert document["reason_code"] == "PERSISTENCE_REQUIRES_APPROVAL"
    assert document["plan_hash"] == document["plan_hash"]
    assert Path(document["plan_file"]).is_file()
    assert "token-file" in document["required_action"]
    # Nothing was written to the environment or the registry.
    assert registry.environment_persist_records() == []


def test_env_persist_dry_run_builds_the_plan_and_writes_nothing(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    code, document = env(capsys, cli_root, "persist", "external/dr-env/java", "--dry-run")

    assert code == 0
    assert document["dry_run"] is True
    assert document["exposure"]["variables"] == {"JAVA_HOME": str(registered_reference)}
    assert document["exposure"]["path_prepend"] == [str(registered_reference / "bin")]
    assert registry.environment_persist_records() == []
    assert registry.events() == []


def test_env_persist_machine_scope_needs_the_broker(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    code, document = env(capsys, cli_root, "persist", "external/dr-env/java", "--scope", "machine")

    assert code == 5
    assert document["reason_code"] == "PRIVILEGE_REQUIRED"


def test_env_persist_outside_every_data_root_is_refused(
    capsys, cli_root: Path, tests_tmp: Path, data_root: Path, registry
) -> None:
    outside = tests_tmp / "cli-env-outside"
    outside.mkdir(exist_ok=True)
    with registry.write(expected_generation=registry.generation) as connection:
        # The data root exists, but it does not contain this object.
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-3",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/outside",
                capability_id="java",
                path=str(outside),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                entrypoints=("bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )

    code, document = env(capsys, cli_root, "persist", "external/dr-env/outside")

    assert code == 8
    assert document["reason_code"] == "PERSISTENCE_TARGET_FORBIDDEN"


def test_env_persist_unknown_reference_is_refused(capsys, cli_root: Path) -> None:
    code, document = env(capsys, cli_root, "persist", "external/dr-env/absent")

    assert code == 1  # EXIT_NOT_FOUND
    assert document["reason_code"] == "NOT_FOUND"


def test_env_list_reports_what_was_persisted(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    assert env(capsys, cli_root, "list")[1]["environment_persist"] == []

    store = InMemoryEnvironmentStore()
    persist_module(registry, registered_reference, store)

    code, document = env(capsys, cli_root, "list")
    assert code == 0
    assert [item["variable"] for item in document["environment_persist"]] == ["JAVA_HOME", "Path"]
    assert document["environment_persist"][0]["had_previous_value"] is False


def test_env_forget_dry_run_reports_without_changing_anything(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    store = InMemoryEnvironmentStore()
    store.write("JAVA_HOME", "C:\\old-java", "REG_EXPAND_SZ")
    persist_module(registry, registered_reference, store)

    code, document = env(capsys, cli_root, "forget", "external/dr-env/java", "--dry-run")

    assert code == 0
    assert document["dry_run"] is True
    assert document["restored"] == ["JAVA_HOME", "Path"]
    # Still recorded: a dry run must not consume the record.
    assert len(registry.environment_persist_records(active_only=True)) == 2


# --------------------------------------------------------------------------- #
# exec: session-only injection
# --------------------------------------------------------------------------- #


def test_exec_injects_the_environment_into_one_child_process(
    capsys, cli_root: Path, registered_reference: Path, registry
) -> None:
    code, document = run(
        capsys,
        "--json",
        "--root",
        str(cli_root),
        "exec",
        "external/dr-env/java",
        "cmd",
        "/c",
        "echo",
        "%JAVA_HOME%",
    )

    assert code == 0
    assert document["exit_status"] == 0
    assert document["persisted"] is False
    assert document["variables"] == ["JAVA_HOME"]
    # `--json` is machine mode: the child's own output is captured into the document
    # instead of being interleaved with it.
    assert str(registered_reference) in document["stdout"]
    assert registry.environment_persist_records() == []


def test_exec_reports_a_failing_child_without_leaking_its_exit_code(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root), "exec", "external/dr-env/java", "cmd", "/c", "exit", "3"
    )

    assert code == 2  # EXIT_DEGRADED, inside the frozen 0-9 space
    assert document["exit_status"] == 3
    assert document["reason_code"] == "CHILD_PROCESS_FAILED"


# --------------------------------------------------------------------------- #
# exec argument shape (planning §15.1 spells it `exec --env <name> -- …`)
# --------------------------------------------------------------------------- #


def test_exec_accepts_the_planning_flag_spelling(capsys, cli_root: Path, registered_reference: Path) -> None:
    """§15.1/§16.4 write `exec --env <name> -- <command>`; that spelling has to parse."""

    code, document = run(
        capsys,
        "--json",
        "--root",
        str(cli_root),
        "exec",
        "--env",
        "external/dr-env/java",
        "--",
        "cmd",
        "/c",
        "echo",
        "%JAVA_HOME%",
    )

    assert code == 0
    assert document["external_id"] == "external/dr-env/java"
    assert document["command"] == ["cmd", "/c", "echo", "%JAVA_HOME%"]
    assert document["reason_code"] == "SUCCESS"


def test_exec_spellings_are_the_same_command(capsys, cli_root: Path, registered_reference: Path) -> None:
    arguments = ("--json", "--root", str(cli_root), "exec")
    child = ("--", "cmd", "/c", "echo", "%JAVA_HOME%")
    positional = run(capsys, *arguments, "external/dr-env/java", *child)
    flagged = run(capsys, *arguments, "--env", "external/dr-env/java", *child)
    equals_form = run(capsys, *arguments, "--env=external/dr-env/java", *child)

    assert positional == flagged == equals_form


def test_exec_options_after_the_identifier_still_reach_airoot(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """`--json` used to be swallowed by the child's argument list *and* silently ignored."""

    code, document = run(
        capsys,
        "--root",
        str(cli_root),
        "exec",
        "external/dr-env/java",
        "--json",
        "--",
        "cmd",
        "/c",
        "echo",
        "%JAVA_HOME%",
    )

    # `run` only produces a document if AIROOT really was in machine mode.
    assert code == 0
    assert document["command"] == ["cmd", "/c", "echo", "%JAVA_HOME%"]
    assert "--json" not in document["command"]


def test_exec_hoists_a_global_option_given_after_the_identifier(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """`--root` after the identifier is AIROOT's option, not the child's argument."""

    code, document = run(
        capsys,
        "exec",
        "--root",
        str(cli_root),
        "external/dr-env/java",
        "--json",
        "--",
        "cmd",
        "/c",
        "echo",
        "%JAVA_HOME%",
    )

    assert code == 0
    assert document["command"] == ["cmd", "/c", "echo", "%JAVA_HOME%"]


def test_exec_without_a_child_command_is_invalid_input(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "exec", "external/dr-env/java")

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
