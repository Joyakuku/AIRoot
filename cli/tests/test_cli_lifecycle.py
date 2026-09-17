"""CLI contract for the deletion verbs (draft §14).

The CLI is where the ownership rule is actually enforced for a caller, so these tests go
through ``main``: `uninstall` on a reference must exit 7 and print the path; `uninstall` on an
owned payload must stop at the approval boundary without deleting anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fake_issuer
from airoot.cli import main
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


@pytest.fixture
def installed(registry, clock, root) -> str:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return str(plan["target"]["instance_id"])


def test_cli_retire_keeps_the_payload(capsys, cli_root: Path, installed: str, registry) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "tool", "retire", installed)

    assert code == 0, document
    assert document["payload_removed"] is False
    assert document["lifecycle_status"] == "retired"
    assert (cli_root / "store" / installed).is_dir(), "retire never deletes"


def test_cli_retire_removes_its_own_stable_entry(capsys, cli_root: Path, installed: str, registry) -> None:
    """Draft §165 through the CLI: the projection goes with the binding, so `path verify` stays green.

    The reading this replaces was "an entry with no binding is reported as drift": true while `retire`
    left the file behind, and the drift it reported was one this build had just manufactured. The
    entry directory is now empty after a retire, and the exposure surface agrees with the registry.
    """

    entry = cli_root / "cli" / "exposure" / "bin" / f"{CAPABILITY}.cmd"
    assert entry.is_file(), "the EXPOSED step of the install wrote the stable entry"

    code, document = run(capsys, "--json", "--root", str(cli_root), "tool", "retire", installed)

    assert code == 0, document
    assert document["launcher_removed"] is True, document
    assert document["launcher_path"] == str(entry)
    assert not entry.exists()
    assert [item.name for item in entry.parent.glob("*.cmd")] == []

    code, verified = run(capsys, "--json", "--root", str(cli_root), "path", "verify")
    assert code == 0, verified
    assert verified["expected_launchers"] == [] and verified["violations"] == 0, verified
    assert verified["launcher_present"] is False


def test_cli_gc_plan_then_apply_needs_a_token(capsys, cli_root: Path, installed: str, registry) -> None:
    run(capsys, "--json", "--root", str(cli_root), "tool", "retire", installed)

    code, document = run(capsys, "--json", "--root", str(cli_root), "tool", "gc", "--plan")
    assert code == 0, document
    assert document["collectable"] == 1
    plan_file = Path(document["plans"][0]["plan_file"])
    assert plan_file.is_file()

    code, document = run(capsys, "--json", "--root", str(cli_root), "tool", "gc", "--apply")
    assert code == 4
    assert document["reason_code"] == "APPROVAL_REQUIRED"
    assert (cli_root / "store" / installed).is_dir(), "still nothing deleted"


def test_cli_uninstall_stops_at_the_approval_boundary(
    capsys, cli_root: Path, installed: str
) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "uninstall", installed)

    assert code == 4
    assert document["reason_code"] == "APPROVAL_REQUIRED"
    assert document["payload_removed"] is False
    assert document["retire"]["payload_removed"] is False
    assert "token-file" in document["required_action"]
    assert (cli_root / "store" / installed).is_dir()


def test_cli_uninstall_dry_run_changes_nothing(
    capsys, cli_root: Path, installed: str, registry
) -> None:
    """§160: every other dry run in this CLI writes nothing, and this one used to retire.

    §159 measured the damage: `--dry-run` retired the instance, `where` stopped resolving it, and the
    way back was to install a *different* version — the same plan and the same version both answer
    `INSTANCE_CONFLICT`. The flag's own help said "retire and print the plan", which is what the code
    did; the flag name promised the other thing. Now the name is what happens.
    """

    before = run(capsys, "--json", "--root", str(cli_root), "tool", "status", installed)[1]

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "uninstall", installed, "--dry-run"
    )

    assert code == 0, document
    assert document["dry_run"] is True
    assert document["payload_removed"] is False
    assert document["plan"] is None, "a dry run must not build a gc plan it cannot honour"
    assert "tool retire" in document["required_action"], document["required_action"]

    after = run(capsys, "--json", "--root", str(cli_root), "tool", "status", installed)[1]
    assert before["instance"]["lifecycle_status"] == "active"
    assert after["instance"]["lifecycle_status"] == "active", "a dry run retired the instance"
    assert (cli_root / "store" / installed).is_dir(), "a dry run touched the payload"

    # ...and the state it would have changed is still changeable: nothing was half-done.
    code, gc_plan = run(capsys, "--json", "--root", str(cli_root), "tool", "gc", "--plan")
    assert code == 0 and gc_plan["collectable"] == 0, "a dry run left something collectable behind"


def test_cli_uninstall_on_a_reference_is_refused_with_the_path(
    capsys, cli_root: Path, tests_tmp: Path, registry
) -> None:
    target = tests_tmp / "cli-lifecycle-env" / "java"
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/dr-env/java', 'java', ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (str(target),),
        )

    code, document = run(capsys, "--json", "--root", str(cli_root), "uninstall", "external/dr-env/java")

    assert code == 7
    assert document["reason_code"] == "OWNERSHIP_REQUIRED"
    assert any("airoot forget external/dr-env/java" in item for item in document["evidence"])
    assert any(str(target) in item for item in document["evidence"])
