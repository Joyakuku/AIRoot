"""L1: read-only inspection verbs and the PATH invariant (draft §26, 规划 §15.4:1601-1603).

Two things are under test:

* `tool list/status/verify` observe the owned domain and **must not write anything** — §15.4:1602
  says status "must not switch active", §15.4:1603 says verify "must not adopt or repair";
* `path verify` checks a frozen rule that had no checker at all: the machine PATH may hold exactly
  one AIROOT entry (``cli\\exposure\\bin``), and a version/store directory never goes on PATH.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import fake_issuer
from airoot.caps.pathexposure import sanctioned_entry, verify_path_exposure
from airoot.caps.toolstate import list_tools, tool_status, tool_verify
from airoot.cli import main
from airoot.exits import exit_code_for
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "fake-tool"


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


def install(registry, clock, root) -> str:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return str(plan["target"]["instance_id"])


# --------------------------------------------------------------------------- #
# tool list / status / verify
# --------------------------------------------------------------------------- #


def test_list_reports_definition_facts_lifecycle_binding_and_source(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)

    document = list_tools(registry)

    assert document["count"] == 1
    entry = document["instances"][0]
    assert entry["instance_id"] == instance_id
    assert entry["capability_id"] == CAPABILITY
    assert entry["version"] == "1.0.0"
    assert entry["architecture"] == "x64"
    assert entry["lifecycle_status"] == "active"
    assert entry["active_binding"]["binding_key"]
    assert entry["source"]["kind"] == "generated_fixture"


def test_status_is_healthy_for_a_fresh_instance(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)

    status = tool_status(registry, instance_id, root=root.path)

    assert status.payload_present is True
    assert status.entrypoints_missing == []
    assert status.findings == []
    assert status.reason_code == "SUCCESS"


def test_status_reports_a_missing_payload_as_an_error(registry, clock, root) -> None:
    import shutil

    instance_id = install(registry, clock, root)
    shutil.rmtree(Path(root.path) / "store" / instance_id)

    status = tool_status(registry, instance_id, root=root.path)

    assert status.payload_present is False
    assert status.reason_code == "PAYLOAD_MISSING"
    assert exit_code_for(status.reason_code) == 3


def test_status_treats_a_retired_instance_as_a_state_not_an_error(registry, clock, root) -> None:
    from airoot.caps.lifecycle import retire

    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)

    status = tool_status(registry, instance_id, root=root.path)

    assert status.instance["lifecycle_status"] == "retired"
    assert status.instance["active_binding"] is None
    assert status.reason_code == "SUCCESS", "retiring is legal, not a defect"


def test_verify_passes_on_an_intact_payload(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)

    document = tool_verify(registry, instance_id, root=root.path)

    assert document["verified"] is True
    assert document["actual_digest"] == document["expected_digest"]
    assert document["problems"] == []
    assert document["repaired"] is False and document["adopted"] is False


def test_verify_detects_a_tampered_payload_and_does_not_repair(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    payload = Path(root.path) / "store" / instance_id / "fake-tool.bin"
    payload.write_bytes(b"tampered payload\n")

    document = tool_verify(registry, instance_id, root=root.path)

    assert document["verified"] is False
    assert document["reason_code"] == "MANIFEST_DIGEST_MISMATCH"
    assert exit_code_for(document["reason_code"]) == 3
    assert document["repaired"] is False
    assert payload.read_bytes() == b"tampered payload\n", "verify never repairs"


def test_verify_refuses_an_unknown_instance(registry, root) -> None:
    from airoot.exits import AirootError

    with pytest.raises(AirootError) as caught:
        tool_verify(registry, "not-a-thing", root=root.path)
    assert caught.value.reason_code == "NOT_FOUND"


def test_the_three_verbs_do_not_change_the_generation(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    before = registry.generation

    list_tools(registry)
    tool_status(registry, instance_id, root=root.path)
    tool_verify(registry, instance_id, root=root.path)

    assert registry.generation == before, "observation never changes declared state"


# --------------------------------------------------------------------------- #
# the PATH invariant
# --------------------------------------------------------------------------- #


def test_a_clean_path_has_no_violation(tmp_path: Path) -> None:
    verification = verify_path_exposure(
        tmp_path, machine_entries=["C:\\Windows"], user_entries=["C:\\Users\\me\\bin"]
    )

    assert verification.violations == 0
    assert verification.reason_code() == "SUCCESS"
    assert verification.to_document()["path_written"] is False


def test_the_single_sanctioned_entry_is_accepted(tmp_path: Path) -> None:
    from airoot.caps.launcher import write_launcher

    expected = sanctioned_entry(tmp_path)
    written = write_launcher(tmp_path, "cargo")  # ADR-0050: presence means an *entry*, not a directory

    verification = verify_path_exposure(
        tmp_path, machine_entries=[str(expected)], user_entries=[], expected_launchers=["cargo"]
    )

    assert verification.launcher_present is True
    assert verification.launchers[0]["path"] == written.path
    assert verification.violations == 0
    assert verification.entries[0]["sanctioned"] is True
    assert all(finding.severity != "info" for finding in verification.findings)


def test_a_duplicate_airoot_entry_is_a_violation(tmp_path: Path) -> None:
    expected = sanctioned_entry(tmp_path)
    expected.mkdir(parents=True)

    verification = verify_path_exposure(
        tmp_path, machine_entries=[str(expected)], user_entries=[str(expected)]
    )

    assert verification.violations == 1
    assert verification.reason_code() == "PATH_EXPOSURE_VIOLATION"
    assert exit_code_for("PATH_EXPOSURE_VIOLATION") == 2
    assert any(finding.severity == "warning" for finding in verification.findings)


def test_a_store_directory_on_the_machine_path_is_an_error(tmp_path: Path) -> None:
    version_dir = tmp_path / "store" / "fake-tool" / "1.0.0" / "win-x64"
    version_dir.mkdir(parents=True)

    verification = verify_path_exposure(tmp_path, machine_entries=[str(version_dir)], user_entries=[])

    assert verification.violations == 1
    assert any(finding.severity == "error" for finding in verification.findings)
    assert any("store payload directory" in finding.detail for finding in verification.findings)


def test_an_unsanctioned_airoot_directory_is_a_warning(tmp_path: Path) -> None:
    other = tmp_path / "cli" / "tools"
    other.mkdir(parents=True)

    verification = verify_path_exposure(tmp_path, machine_entries=[str(other)], user_entries=[])

    assert verification.violations == 1
    assert any("not the sanctioned entry" in finding.detail for finding in verification.findings)


def test_an_empty_launcher_directory_is_not_a_stable_entry(tmp_path: Path) -> None:
    """ADR-0050: `launcher_present` means "an entry exists", not "the directory exists".

    The old reading reported `true` for a directory nobody had put anything in, so judgement 10 could be
    satisfied by `mkdir`. This is the guard for the new reading, and it is the only case where the two
    disagree: the tests next to it have both a directory and a file, or neither.
    """

    expected = sanctioned_entry(tmp_path)
    expected.mkdir(parents=True)

    verification = verify_path_exposure(
        tmp_path, machine_entries=[], user_entries=[], expected_launchers=["cargo"]
    )

    assert verification.launcher_present is False, "an empty directory is not a stable entry"
    assert verification.launchers == []
    assert verification.violations == 1, "the binding has no entry; that is the drift this reports"


def test_a_missing_launcher_directory_is_information_not_a_defect(tmp_path: Path) -> None:
    verification = verify_path_exposure(tmp_path, machine_entries=[], user_entries=[])

    assert verification.launcher_present is False
    assert verification.violations == 0, "P1 writes no launchers; its absence is not a violation"
    assert any(finding.code == "EXPOSURE_NOT_IMPLEMENTED" for finding in verification.findings)
    assert verification.reason_code() == "SUCCESS"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_tool_list_and_status_and_verify(capsys, cli_root: Path, installed) -> None:
    code, listing = run(capsys, "--json", "--root", str(cli_root), "tool", "list")
    assert code == 0
    assert listing["count"] == 1

    code, status = run(capsys, "--json", "--root", str(cli_root), "tool", "status", installed)
    assert code == 0
    assert status["payload_present"] is True

    code, verified = run(capsys, "--json", "--root", str(cli_root), "tool", "verify", installed)
    assert code == 0
    assert verified["verified"] is True


def test_cli_path_verify_never_writes(capsys, cli_root: Path, monkeypatch) -> None:
    """The check runs against an injected PATH so the host is never read or written."""

    from airoot.caps import pathexposure

    expected = pathexposure.sanctioned_entry(cli_root)
    monkeypatch.setattr("airoot.caps.effective.machine_path", lambda: [os.path.normcase(str(cli_root))])
    monkeypatch.setattr("airoot.caps.effective.user_path", lambda: [])

    code, document = run(capsys, "--json", "--root", str(cli_root), "path", "verify")

    assert code == 2, document
    assert document["reason_code"] == "PATH_EXPOSURE_VIOLATION"
    assert document["path_written"] is False
    assert document["entries"], "the AIROOT root itself on PATH is reported"
    assert str(expected) == document["sanctioned_entry"]


@pytest.fixture
def installed(registry, clock, root) -> str:
    return install(registry, clock, root)
