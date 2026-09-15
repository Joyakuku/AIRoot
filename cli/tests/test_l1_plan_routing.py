"""L1: `plan` routes before it plans (draft §12.4, §20).

The gap these tests close: "`scope decide` says data-root" and "`plan` produced an install plan"
used to be two unrelated answers, so a scope decision could be approved by nobody. Now the
routing decision is part of the plan (`metadata.routing`), and widening a project-owned
capability to the data root is refused without a separate approval (S-010/C-020).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.cli import main
from airoot.registry.entities import DataRoot
from airoot.paths import volume_serial


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def data_root(tests_tmp: Path, registry) -> str:
    path = tests_tmp / "plan-target-env"
    path.mkdir(parents=True, exist_ok=True)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(path),
                role="runtime",
                volume_serial=volume_serial(path),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-4",
            ),
        )
    return "dr-env"


@pytest.fixture
def project(tests_tmp: Path) -> Path:
    path = tests_tmp / "plan-project"
    path.mkdir(parents=True, exist_ok=True)
    (path / "requirements.txt").write_text("build==3.31.6\n", encoding="utf-8")
    return path


def plan_files(cli_root: Path) -> list[Path]:
    directory = cli_root / "state" / "plans"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


# --------------------------------------------------------------------------- #
# dry run: route and report, write nothing
# --------------------------------------------------------------------------- #


def test_dry_run_reports_the_target_size_and_approval_without_writing(
    capsys, cli_root: Path, data_root: str
) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}", "--dry-run",
    )

    assert code == 0, document
    assert document["operation"] == "plan_dry_run"
    assert document["target"]["data_root_id"] == data_root
    assert document["target"]["path"].endswith("plan-target-env")
    assert document["required_approval"] == "none"
    assert document["size_estimate_bytes"] is None
    assert document["size_note"] == "SIZE_ESTIMATE_UNAVAILABLE", "an unknown size is reported as unknown"
    assert document["side_effects"]
    assert plan_files(cli_root) == [], "a dry run writes no plan"


def test_dry_run_reports_the_size_the_caller_actually_knows(capsys, cli_root: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--dry-run", "--size-bytes", "123456",
    )

    assert code == 0
    assert document["size_estimate_bytes"] == 123456
    assert document["size_source"] == "declared"


def test_a_project_manifest_reference_routes_to_the_project_without_asking(
    capsys, cli_root: Path, project: Path
) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--scope", "project", "--project", str(project), "--dry-run",
    )

    assert code == 0, document
    assert document["routing"]["decided_scope"] == "project"
    assert document["routing"]["origin"] == "project_manifest"
    assert document["confirmation_required"] is False


def test_widening_a_project_capability_is_refused(capsys, cli_root: Path, project: Path, data_root: str) -> None:
    """C-020/S-010: the project's own manifest may not be widened to the data root silently."""

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--project", str(project),
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_UPGRADE_REQUIRES_APPROVAL"
    assert plan_files(cli_root) == []


def test_widening_is_reported_by_the_dry_run_too(capsys, cli_root: Path, project: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--project", str(project), "--dry-run",
    )

    assert code == 4
    assert document["required_approval"] == "scope_upgrade"
    assert document["reason_code"] == "SCOPE_UPGRADE_REQUIRES_APPROVAL"


# --------------------------------------------------------------------------- #
# confirmation: no plan file until the human answered
# --------------------------------------------------------------------------- #


def test_confirmation_is_required_and_no_plan_is_written(capsys, cli_root: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--creates-environment",
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_CONFIRMATION_REQUIRED"
    assert "project-isolated" in " ".join(document["evidence"])
    assert plan_files(cli_root) == [], "an unconfirmed plan must not exist on disk"


def test_the_threshold_is_what_triggers_the_question(capsys, cli_root: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--size-bytes", str(400 * 1024 * 1024),
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_CONFIRMATION_REQUIRED"


def test_an_unknown_capability_never_gets_a_plan(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "plan", "not-a-capability")

    assert code == 9
    assert document["reason_code"] == "CAPABILITY_NOT_DECLARED"
    assert plan_files(cli_root) == []


# --------------------------------------------------------------------------- #
# the happy path carries the routing decision into the plan
# --------------------------------------------------------------------------- #


def test_the_plan_records_the_routing_decision(capsys, cli_root: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
    )

    assert code == 0, document
    routing = document["metadata"]["routing"]
    assert routing["requested_scope"] == "data-root"
    assert routing["decided_scope"] == "data-root"
    assert routing["target_id"] == data_root
    assert routing["origin"] == "generic_tool"
    # The hash covers the routing block, so an approval cannot be reused for a different target.
    assert document["plan_hash"].startswith("sha256:")
    assert Path(document["plan_file"]).is_file()


def test_the_routing_target_changes_the_plan_hash(capsys, cli_root: Path, data_root: str, tests_tmp: Path) -> None:
    """The same capability aimed at two targets must not share an approval."""

    first = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
    )[1]
    other = tests_tmp / "plan-target-other"
    other.mkdir(parents=True, exist_ok=True)
    from airoot.registry import Registry

    registry = Registry.open(cli_root)
    try:
        with registry.write(expected_generation=registry.generation) as connection:
            registry.add_data_root(
                connection,
                DataRoot(
                    data_root_id="dr-other",
                    path=str(other),
                    role="tool",
                    volume_serial=volume_serial(other),
                    added_at="2024-01-01T00:00:00Z",
                    whitelist_revision="wl-4",
                ),
            )
    finally:
        registry.close()
    second = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", "data-root:dr-other",
    )[1]

    assert first["plan_hash"] != second["plan_hash"]


def test_an_unknown_data_root_target_is_refused(capsys, cli_root: Path) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", "data-root:dr-nope",
    )

    assert code == 6
    assert document["reason_code"] == "DATA_ROOT_MISSING"


def test_a_malformed_target_is_refused(capsys, cli_root: Path) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", "D:/somewhere",
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_a_managed_capability_without_a_discovery_rule_can_still_be_planned(
    capsys, cli_root: Path
) -> None:
    """`fake-tool` has no whitelist predicate (AIROOT installs it itself) but is frozen.

    Existence is decided by the frozen capability list; the whitelist only says how to
    recognise an object already on disk (draft §15.2-1).
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "plan", "fake-tool", "--scope", "machine"
    )

    assert code == 0, document
    assert document["metadata"]["routing"]["decided_scope"] == "data-root"
    assert document["metadata"]["routing"]["decision_reason"] == "SUCCESS"
