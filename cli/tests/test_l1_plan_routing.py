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
    """§153: the refusal is for **nobody answered** — so this invocation names no scope at all.

    It used to pass `--scope data-root --target ...` and still expect the refusal, which is what
    ADR-0055 measured: the gate listed project-isolated / data-root / cancel, and naming one of
    them changed nothing. The invitation to answer is still asserted here; the answer itself is
    the next test.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--creates-environment",
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_CONFIRMATION_REQUIRED"
    assert "project-isolated" in " ".join(document["evidence"])
    assert plan_files(cli_root) == [], "an unconfirmed plan must not exist on disk"


def test_the_threshold_is_what_triggers_the_question(capsys, cli_root: Path, data_root: str) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--size-bytes", str(400 * 1024 * 1024),
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_CONFIRMATION_REQUIRED"
    assert plan_files(cli_root) == []


def test_naming_the_scope_is_the_answer_that_produces_the_plan(
    capsys, cli_root: Path, data_root: str
) -> None:
    """ADR-0055 / §153: the gate asks *where*; naming it answers, and the answer is recorded.

    Both checks above name no scope and are still refused. This one names it, and the plan exists —
    with `confirmation_answered_by` in its routing, because "confirmation_required: true" on its own
    reads as "nobody confirmed", which is exactly what the metadata must not say about an answered
    question.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "data-root", "--target", f"data-root:{data_root}",
        "--creates-environment",
    )

    assert code == 0, document
    routing = document["metadata"]["routing"]
    assert routing["confirmation_required"] is True
    assert routing["confirmation_answered_by"] == "explicit_scope"
    assert plan_files(cli_root), "an answered plan must exist on disk"


def test_answering_with_a_different_scope_is_refused(capsys, cli_root: Path, data_root: str, project: Path) -> None:
    """§155: an answer that is not the routing's answer is not an answer.

    §153 made naming a scope produce a plan, and §154 measured what that opened: `plan node --scope
    project` recorded `requested_scope=project`, was overridden by the high-risk rule (which returns
    the data root unconditionally), and bound `machine/node/windows/x64`. The answer was taken, not
    honoured. The refusal names both scopes, because unlike "nobody answered" the caller said
    something — and it uses the code the opposite direction already uses (project → data-root).
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "node", "--scope", "project", "--project", str(project),
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_UPGRADE_REQUIRES_APPROVAL"
    evidence = " ".join(document["evidence"])
    assert "requested_scope=project" in evidence
    assert "decided_scope=data-root" in evidence
    assert plan_files(cli_root) == [], "a plan whose answer was overridden must not exist on disk"


def test_the_dry_run_reports_a_diverging_answer_just_as_the_real_call_does(
    capsys, cli_root: Path, data_root: str, project: Path
) -> None:
    """The dry run is only useful if its verdict is the verdict (S-010's other half)."""

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "node", "--scope", "project", "--project", str(project), "--dry-run",
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_UPGRADE_REQUIRES_APPROVAL"
    assert document["required_approval"] == "scope_upgrade"
    assert document["routing"]["requested_scope"] == "project"
    assert plan_files(cli_root) == []


def test_a_bare_directory_target_says_it_is_a_project_answer(
    capsys, cli_root: Path, data_root: str, project: Path
) -> None:
    """§155: `--target` alone still says what kind of place it is, and the answer is recorded as that.

    It used to default to `machine` — a scope nobody named — so the evidence above would have been a
    claim about the caller's intent rather than a reading of it.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "plan", "node", "--target", str(project)
    )

    assert code == 4
    assert "requested_scope=project" in " ".join(document["evidence"])


def test_a_data_root_target_alone_resolves_to_the_data_root(
    capsys, cli_root: Path, data_root: str
) -> None:
    """§155: `--target data-root:<id>` was recorded as the literal string in `target_path`.

    `--scope` is optional here because the spelling already carries the kind — the plan's destination
    is now the data root's path and its `target_id` is the registered id, instead of a path-shaped
    string that names no directory on this machine.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "plan", "node", "--target", f"data-root:{data_root}"
    )

    assert code == 0, document
    routing = document["metadata"]["routing"]
    assert routing["requested_scope"] == "data-root"
    assert routing["target_id"] == data_root
    assert Path(routing["target_path"]).is_dir(), "the destination must be a real directory"


def test_a_scope_that_contradicts_its_target_is_refused(capsys, cli_root: Path, data_root: str) -> None:
    """Two spellings, one question: `--scope machine` and a data-root target are different answers."""

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "archive", "--scope", "machine", "--target", f"data-root:{data_root}",
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert plan_files(cli_root) == []


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
