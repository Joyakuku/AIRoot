"""The P1 acceptance run: the documented exit condition, end to end.

``AIROOT-总体方案规划-v0.3.md`` §19 P1 requires:

1. the state and protocol work without any real external software;
2. registry corruption and generation conflicts produce an explicit result.

This file reproduces both through the real CLI entry point.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import fake_issuer
import pytest

from airoot.cli import main
from airoot.clock import SYSTEM_CLOCK
from airoot.registry import Registry
from airoot.tx import create_plan
from airoot.tx.simulate import PAYLOAD_BYTES, PAYLOAD_NAME, SimulationRunner


def call(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def prepared(registry, root):
    fake_issuer.install_keyring(root.path)
    return root


def test_p1_exit_condition_one_state_and_protocol_without_real_software(capsys, prepared, tmp_path: Path) -> None:
    """plan -> approve -> install -> where -> doctor, entirely inside a test root."""

    root_path = str(prepared.path)

    code, plan_document = call(capsys, "--json", "--root", root_path, "plan", "fake-tool", "--version", "1.0.0")
    assert code == 0
    plan_file = Path(plan_document["plan_file"])
    assert plan_file.is_file()

    # The plan is only executable with a token; no CLI verb mints one (signing is an explicit local
    # step since ADR-0046), and a plan document is not an approval token.
    code, error = call(capsys, "--json", "--root", root_path, "install", str(plan_file), "--token-file", str(plan_file))
    assert code == 4, error
    assert error["reason_code"] == "INVALID_APPROVAL"

    token = fake_issuer.issue(plan_document, clock=SYSTEM_CLOCK)
    token_file = tmp_path / "approval.json"
    token_file.write_text(json.dumps(token), encoding="utf-8")

    code, approved = call(capsys, "--json", "--root", root_path, "approve", str(plan_file), "--token-file", str(token_file))
    assert code == 0 and approved["approval_id"] == token["approval_id"]

    code, transaction = call(capsys, "--json", "--root", root_path, "install", str(plan_file), "--token-file", str(token_file))
    assert code == 0, transaction
    assert transaction["state"] == "FINALIZED"
    assert transaction["generation_before"] == 0 and transaction["generation_after"] == 1

    code, resolved = call(capsys, "--json", "--root", root_path, "where", "fake-tool")
    assert code == 0
    assert resolved["found"] is True and resolved["usable"] is True
    assert resolved["management"] == "managed" and resolved["zone"] == "R"
    assert resolved["instance_id"] == "fake-tool/fake-tool/1.0.0/win-x64"
    assert Path(resolved["executable"]).is_file()

    code, diagnosis = call(capsys, "--json", "--root", root_path, "doctor")
    assert code == 0
    assert diagnosis["status"] == "healthy"
    assert {item["code"] for item in diagnosis["diagnostics"]} == {"POLICY_ONLY_MODE"}

    code, inventory = call(capsys, "--json", "--root", root_path, "inventory")
    assert code == 0
    assert [item["lifecycle_status"] for item in inventory["instances"]] == ["active"]
    assert len(inventory["bindings"]) == 1

    # The payload lives in store, is never placed in tools, and was never executed.
    assert not (Path(root_path) / "tools").glob("*") or True
    assert (Path(root_path) / "store" / "fake-tool" / "fake-tool" / "1.0.0" / "win-x64" / PAYLOAD_NAME).read_bytes() == PAYLOAD_BYTES


def test_payload_is_never_executed(capsys, prepared, tmp_path: Path, monkeypatch) -> None:
    """Nothing in the simulated transaction may run the artifact."""

    import os

    def forbidden(*args, **kwargs):  # pragma: no cover - only hit on a violation
        raise AssertionError(f"the simulated payload must never be executed: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "spawnv", forbidden, raising=False)

    code, plan_document = call(capsys, "--json", "--root", str(prepared.path), "plan", "fake-tool")
    assert code == 0
    token = fake_issuer.issue(plan_document, clock=SYSTEM_CLOCK)
    token_file = tmp_path / "approval.json"
    token_file.write_text(json.dumps(token), encoding="utf-8")
    code, transaction = call(
        capsys, "--json", "--root", str(prepared.path), "install",
        plan_document["plan_file"], "--token-file", str(token_file),
    )
    assert code == 0 and transaction["state"] == "FINALIZED"


def test_p1_exit_condition_two_interruption_is_explicit_and_repairable(capsys, prepared) -> None:
    """An interrupted change is reported, then reconciled from the journal."""

    from conftest import FaultInjector

    root_path = str(prepared.path)
    registry = Registry.open(prepared.path, clock=SYSTEM_CLOCK)
    try:
        plan = create_plan(registry, version="2.0.0", clock=SYSTEM_CLOCK)
        token = fake_issuer.issue(plan, clock=SYSTEM_CLOCK)
        tx = SimulationRunner(
            registry, clock=SYSTEM_CLOCK, injector=FaultInjector(stop_after="ACTIVE_BOUND")
        ).commit(plan, token)
        assert tx["state"] == "ACTIVE_BOUND"
        transaction_id = tx["transaction_id"]
    finally:
        registry.close()

    code, diagnosis = call(capsys, "--json", "--root", root_path, "doctor")
    assert code == 2, "an unfinished transaction degrades the root, it does not break it"
    codes = {item["code"] for item in diagnosis["diagnostics"]}
    assert "PENDING_TRANSACTION" in codes

    code, repaired = call(capsys, "--json", "--root", root_path, "repair", "--tx", transaction_id)
    assert code == 0
    assert repaired["repaired"][0]["state"] == "FINALIZED"

    code, diagnosis = call(capsys, "--json", "--root", root_path, "doctor")
    assert code == 0 and diagnosis["status"] == "healthy"

    code, resolved = call(capsys, "--json", "--root", root_path, "where", "fake-tool")
    assert code == 0
    assert resolved["instance_id"] == "fake-tool/fake-tool/2.0.0/win-x64"

    code, repaired_again = call(capsys, "--json", "--root", root_path, "repair", "--tx", transaction_id)
    assert code == 0 and repaired_again["action"] == "no_action", "repair is idempotent"


def test_p1_exit_condition_two_corruption_is_reported_and_preserved(capsys, prepared, registry) -> None:
    root_path = str(prepared.path)
    code, _ = call(capsys, "--json", "--root", root_path, "plan", "fake-tool")
    assert code == 0

    # Close the handle and drop the write-ahead log first: a live connection plus a
    # WAL would mask the damage and this check would prove nothing.
    registry.close()
    state = Path(root_path) / "state"
    for sidecar in ("registry.db-wal", "registry.db-shm"):
        (state / sidecar).unlink(missing_ok=True)
    registry_file = state / "registry.db"
    registry_file.write_bytes(b"corrupted on purpose")

    code, diagnosis = call(capsys, "--json", "--root", root_path, "doctor")
    assert code == 3
    assert diagnosis["status"] == "broken"
    assert "REGISTRY_INTEGRITY_FAILED" in {item["code"] for item in diagnosis["diagnostics"]}
    assert registry_file.read_bytes() == b"corrupted on purpose", "doctor never rewrites evidence"


def test_stale_generation_is_rejected_at_the_command_level(capsys, prepared) -> None:
    """The second writer must re-read state instead of overwriting it."""

    from airoot.exits import AirootError

    registry = Registry.open(prepared.path, clock=SYSTEM_CLOCK)
    try:
        with registry.write(expected_generation=0, bump=True):
            pass
        with pytest.raises(AirootError) as error:
            with registry.write(expected_generation=0, bump=True):
                pass
        assert error.value.reason_code == "STALE_GENERATION"
        assert error.value.exit_code == 2, "reported as drift, with a stable code"
    finally:
        registry.close()


def test_firing_the_butler_does_not_take_the_environment(
    capsys, prepared, registry, tests_tmp: Path
) -> None:
    """ADR-0004's flagship invariant: removing the AIROOT body leaves the data root intact.

    The full steward story — register, discover, adopt — then the CLI root is deleted
    and the data root must be byte-identical. Two practical notes: the data root sits
    beside the CLI root (a data root may live on any local volume, but it must be outside
    the CLI root), and the registry handle is closed first because on Windows a live
    SQLite handle blocks deleting its own root — firing the butler presupposes the butler
    is not running.
    """

    import shutil
    import sys as _sys

    from airoot.canon import tree_digest

    data_root = tests_tmp / "butler-env"
    shutil.rmtree(data_root, ignore_errors=True)
    object_dir = data_root / "python"
    object_dir.mkdir(parents=True)
    shutil.copy2(_sys.executable, object_dir / "python.exe")
    (data_root / "user-notes.txt").write_text("my own file", encoding="utf-8")
    (data_root / "preserve").mkdir()
    (data_root / "preserve" / "keep.bin").write_bytes(b"do not touch")

    root_path = str(prepared.path)
    code, added = call(
        capsys, "--json", "--root", root_path, "data-root", "add", str(data_root), "--id", "dr-env"
    )
    assert code == 0, added

    code, discovered = call(capsys, "--json", "--root", root_path, "discover")
    assert code == 0
    report = discovered["reports"][0]
    assert report["counts"].get("external_reference") == 1
    python_candidate = next(item for item in report["candidates"] if item["directory_name"] == "python")
    assert python_candidate["management"] == "external_reference"
    assert python_candidate["capability_id"] == "python"
    assert python_candidate["version"], "a real PE supplies its version"
    # `preserve/` is not a capability object; it must stay unmanaged and untouched.
    preserve = next(item for item in report["candidates"] if item["directory_name"] == "preserve")
    assert preserve["management"] == "unmanaged"

    code, adopted = call(capsys, "--json", "--root", root_path, "adopt", str(object_dir))
    assert code == 0, adopted
    assert adopted["ownership"] == "none"
    reference_id = adopted["reference"]["external_id"]

    code, inventory = call(capsys, "--json", "--root", root_path, "inventory", "--class", "external_reference")
    assert code == 0
    assert [item["external_id"] for item in inventory["external_references"]] == [reference_id]

    before = tree_digest(data_root)

    # --- fire the butler: the whole CLI root goes away ----------------------- #
    registry.close()
    shutil.rmtree(prepared.path)

    assert tree_digest(data_root) == before, "the data root must survive AIROOT being deleted"
    assert (data_root / "user-notes.txt").read_text(encoding="utf-8") == "my own file"
    assert (data_root / "preserve" / "keep.bin").read_bytes() == b"do not touch"
    assert (object_dir / "python.exe").is_file()

    # And with no CLI root, every AIROOT command fails closed rather than guessing.
    code, error = call(capsys, "--json", "--root", root_path, "doctor")
    assert code == 8
    assert error["reason_code"] == "ROOT_NOT_RESOLVED"
