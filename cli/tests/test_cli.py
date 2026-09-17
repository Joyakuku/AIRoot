"""CLI contract: documented commands, stable exit codes, machine-readable output.

The exit-code table is frozen (v0.3 §15.6), so this file pins it at the command
level. Calls are in-process so a sandbox can never mask a real failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import fake_issuer
import pytest

from airoot.cli import main


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = {}
    if captured.out.strip():
        document = json.loads(captured.out)
    return code, document


@pytest.fixture
def prepared(root, registry, clock):
    """A root with the simulation keyring installed and a plan on disk."""

    fake_issuer.install_keyring(root.path)
    return root


def write_token(token: dict, directory: Path, name: str = "token.json") -> Path:
    path = directory / name
    path.write_text(json.dumps(token), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# root resolution
# --------------------------------------------------------------------------- #


def test_missing_root_argument_fails_closed(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AIROOT_HOME", raising=False)
    code, document = run(capsys, "--json", "root", "status")
    assert code == 8
    assert document["reason_code"] == "ROOT_NOT_RESOLVED"


def test_unresolvable_root_fails_closed(capsys, tmp_path: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(tmp_path / "absent"), "root", "status")
    assert code == 8
    assert document["reason_code"] == "ROOT_NOT_RESOLVED"


def test_root_status_reports_identity_and_generation(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "root", "status")
    assert code == 0
    assert document["root_instance_id"] == prepared.root_instance_id
    assert document["registry_state"] == "available"
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"


def test_root_status_reports_a_missing_registry_without_claiming_health(capsys, root) -> None:
    code, document = run(capsys, "--json", "--root", str(root.path), "root", "status")
    assert code == 6
    assert document["registry_state"] == "unavailable"
    assert document["reason_code"] == "REGISTRY_MISSING"


def test_airoot_home_is_honoured(capsys, prepared, monkeypatch) -> None:
    monkeypatch.setenv("AIROOT_HOME", str(prepared.path))
    code, document = run(capsys, "--json", "root", "status")
    assert code == 0
    assert document["canonical_path"] == str(prepared.path)


# --------------------------------------------------------------------------- #
# creating a root (§157)
# --------------------------------------------------------------------------- #


def test_root_init_creates_a_root_from_nothing(capsys, tests_tmp: Path, monkeypatch) -> None:
    """The one command that runs before a root exists — and it needs no root to resolve.

    Measured before this stage: every command, including this one, answered
    `ROOT_MARKER_MISSING`(6) with "the root must be created by bootstrap (P2) or by a test fixture",
    so a first-time operator had no path from nothing to a working root.
    """

    monkeypatch.delenv("AIROOT_HOME", raising=False)
    target = tests_tmp / "init-from-nothing"

    code, document = run(
        capsys, "--json",
        "root", "init", str(target),
        "--root-instance-id", "root-created-by-test", "--machine-id", "host-created-by-test",
    )

    assert code == 0, document
    assert document["canonical_path"] == str(target)
    assert document["path_written"] is False, "creating a root never writes PATH"
    assert document["security_mode"] == "policy_only"

    # ...and the root it made is usable by the very next command, without an env var.
    code, status = run(capsys, "--json", "--root", str(target), "root", "status")
    assert code == 0, status
    assert status["registry_state"] == "available"
    assert status["machine_id"] == "host-created-by-test"
    assert status["root_instance_id"] == "root-created-by-test"


def test_root_init_reports_the_layout_it_actually_created(capsys, tests_tmp: Path) -> None:
    """What the answer claims to have made has to be on disk: the count and the paths are read."""

    from airoot.root import LAYOUT_DIRS

    target = tests_tmp / "init-layout"
    code, document = run(
        capsys, "--json",
        "root", "init", str(target), "--root-instance-id", "root-layout", "--machine-id", "host-layout",
    )

    assert code == 0, document
    assert document["directories_created"] == len(LAYOUT_DIRS)
    assert Path(document["marker"]).is_file()
    assert Path(document["registry"]).is_file()
    for relative in ("state/plans", "state/approvals", "cli/exposure/bin", "store"):
        assert target.joinpath(relative).is_dir(), relative


def test_root_init_never_invents_an_identity(capsys, tests_tmp: Path) -> None:
    """Both identities are explicit inputs: P1 does not derive them (ADR-0025 keeps that open).

    The refusal is the parser's `INVALID_INPUT` (exit 8), not an argparse traceback — the CLI has one
    way to say "your input is wrong", and it happens before anything touches the filesystem.
    """

    target = tests_tmp / "init-without-identity"
    code, document = run(capsys, "--json", "root", "init", str(target))

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert "--root-instance-id" in document["message"] and "--machine-id" in document["message"]
    assert not target.exists(), "a usage refusal created the directory"


def test_root_init_refuses_an_existing_root_without_rewriting_it(capsys, root) -> None:
    marker = Path(root.path) / "state" / "root.json"
    before = marker.read_text(encoding="utf-8")

    code, document = run(
        capsys, "--json",
        "root", "init", str(root.path), "--root-instance-id", "root-other", "--machine-id", "host-other",
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert any("marker=" in item for item in document["evidence"]), document["evidence"]
    assert marker.read_text(encoding="utf-8") == before, "a refused init must not rewrite the identity"


def test_root_init_refuses_a_directory_that_already_has_contents(capsys, tests_tmp: Path) -> None:
    """`init_root` only refuses an existing *marker*; without this the verb would take over any
    directory the caller mistyped, which is silent clutter in a place that is not AIROOT's."""

    target = tests_tmp / "init-not-empty"
    target.mkdir(parents=True, exist_ok=True)
    (target / "the-user-s-file.txt").write_text("keep me\n", encoding="utf-8")

    code, document = run(
        capsys, "--json",
        "root", "init", str(target), "--root-instance-id", "root-nonempty", "--machine-id", "host-nonempty",
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert any("entries=1" in item for item in document["evidence"]), document["evidence"]
    assert not target.joinpath("state").exists(), "a refused init created part of the layout"
    assert target.joinpath("the-user-s-file.txt").is_file()


# --------------------------------------------------------------------------- #
# where / doctor / inventory
# --------------------------------------------------------------------------- #


def test_where_not_found_exits_one(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "where", "fake-tool")
    assert code == 1
    assert document["found"] is False
    assert document["reason_code"] == "NOT_FOUND"


def test_doctor_healthy_exits_zero(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "doctor")
    assert code == 0
    assert document["status"] == "healthy"


def test_doctor_reports_recovery_required_with_exit_six(capsys, prepared) -> None:
    (Path(prepared.path) / "state" / "root.json").unlink()
    code, document = run(capsys, "--json", "--root", str(prepared.path), "doctor")
    assert code == 6
    assert document["status"] == "recovery_required"


def test_inventory_lists_declared_state(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "inventory")
    assert code == 0
    assert document["instances"] == []
    assert document["generation"] == 0


def test_inventory_rejects_unknown_class(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "inventory", "--class", "nonsense")
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# extensions
# --------------------------------------------------------------------------- #


def test_extension_list_shows_the_bundled_extension(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "extension", "list")
    assert code == 0
    # The bundled set is asserted exactly: a third extension must change this test on purpose.
    assert [item["extension_id"] for item in document["extensions"]] == [
        "airoot-fake-extension",
        "airoot-native-search-extension",
    ]
    assert all(item["registered"] is False for item in document["extensions"])


def test_extension_status_runs_the_self_test(capsys, prepared) -> None:
    code, document = run(
        capsys, "--json", "--root", str(prepared.path), "extension", "status", "airoot-fake-extension"
    )
    assert code == 0
    assert document["status"] == "ok"
    assert document["data"]["health"] == "healthy"
    assert document["security_mode"] == "policy_only"


def test_unknown_extension_exits_nine(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "extension", "status", "nope")
    assert code == 9
    assert document["reason_code"] == "EXTENSION_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# plan / approve / install
# --------------------------------------------------------------------------- #


def test_plan_writes_a_canonical_plan(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "plan", "fake-tool", "--version", "1.0.0")
    assert code == 0
    assert document["target"]["instance_id"] == "fake-tool/fake-tool/1.0.0/win-x64"
    assert document["canonicalization"] == "jcs-rfc8785-compatible"
    assert Path(document["plan_file"]).is_file()


def test_approve_consumes_a_token_and_refuses_a_replay(capsys, prepared, tmp_path: Path) -> None:
    """The CLI evaluates expiry against real time, so this fixture uses it too."""

    from airoot.clock import SYSTEM_CLOCK
    from airoot.registry import Registry
    from airoot.tx import create_plan

    registry = Registry.open(prepared.path, clock=SYSTEM_CLOCK)
    try:
        plan = create_plan(registry, version="1.0.0", clock=SYSTEM_CLOCK)
    finally:
        registry.close()
    token = fake_issuer.issue(plan, clock=SYSTEM_CLOCK)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    token_file = write_token(token, tmp_path)

    code, document = run(
        capsys, "--json", "--root", str(prepared.path), "approve", str(plan_file), "--token-file", str(token_file)
    )
    assert code == 0
    assert document["approval_id"] == token["approval_id"]

    replay_code, replay_document = run(
        capsys, "--json", "--root", str(prepared.path), "approve", str(plan_file), "--token-file", str(token_file)
    )
    assert replay_code == 4
    assert replay_document["reason_code"] == "APPROVAL_REPLAYED"


def test_approve_refuses_a_token_without_a_keyring(capsys, root, clock, tmp_path: Path) -> None:
    from airoot.registry import Registry
    from airoot.tx import create_plan

    registry = Registry.initialize(
        root.path, machine_id="host-0123456789abcdef", root_instance_id=root.root_instance_id, clock=clock
    )
    try:
        plan = create_plan(registry, version="1.0.0", clock=clock)
    finally:
        registry.close()
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    token_file = write_token(fake_issuer.issue(plan, clock=clock), tmp_path)

    code, document = run(
        capsys, "--json", "--root", str(root.path), "approve", str(plan_file), "--token-file", str(token_file)
    )
    assert code == 7
    assert document["reason_code"] == "PROVENANCE_FAILED"


def test_install_rejects_an_unknown_plan_file(capsys, prepared, tmp_path: Path) -> None:
    token_file = write_token({"schema_version": 1}, tmp_path, "whatever.json")
    code, document = run(
        capsys, "--json", "--root", str(prepared.path), "install", str(tmp_path / "absent.json"),
        "--token-file", str(token_file),
    )
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_repair_without_pending_work_is_a_no_op(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "repair")
    assert code == 0
    assert document["action"] == "no_action"


# --------------------------------------------------------------------------- #
# argument handling
# --------------------------------------------------------------------------- #


def test_usage_errors_are_invalid_input_not_degraded(capsys, prepared) -> None:
    """argparse's default exit code 2 would collide with 'degraded or drift'."""

    code, document = run(capsys, "--json", "--root", str(prepared.path), "where")
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_unknown_command_is_invalid_input(capsys, prepared) -> None:
    code, document = run(capsys, "--json", "--root", str(prepared.path), "frobnicate")
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# verbs the register declares and this build deliberately does not carry (draft §101)
# --------------------------------------------------------------------------- #


def registered_deferrals() -> dict:
    """The register an agent reads, rather than a second copy of it written out here."""

    meta = Path(__file__).resolve().parents[2] / "agents" / "airoot.json"
    return json.loads(meta.read_text(encoding="utf-8"))["deferred"]


def test_a_declared_absent_verb_is_refused_as_deferred_not_as_a_typo(capsys) -> None:
    """Measured in §101: all six used to answer `INVALID_INPUT` with `invalid choice: 'bootstrap'`.

    That message is what a *typo* gets, so the caller's next move was to re-read their spelling
    rather than the plan. The refusal now carries the register's two actionable facts in `details`,
    which is also what makes it checkable instead of prose.
    """

    for verb, entry in sorted(registered_deferrals().items()):
        code, refusal = run(capsys, "--json", *verb.split())
        assert code == 1, (verb, code)
        assert refusal["reason_code"] == "NOT_IMPLEMENTED", (verb, refusal)
        assert refusal["details"] == {
            "command": verb,
            "deferred_category": entry["category"],
            "unblocked_by": entry["unblocked_by"],
        }, (verb, refusal)
        assert entry["unblocked_by"] in " ".join(refusal["evidence"]), (verb, refusal)

        # The verb is absent in *every* root, so the answer must not depend on one resolving: the
        # refusal is decided before the root is, and a bogus `--root` in front cannot change it.
        with_root = run(capsys, "--json", "--root", str(Path.cwd() / "no-such-root"), *verb.split())
        assert with_root == (code, refusal), (verb, with_root)


def test_a_misspelled_verb_is_still_a_usage_error(capsys) -> None:
    """The new code is only worth having while the two answers stay distinguishable.

    If a typo and a deferral both came back as `NOT_IMPLEMENTED`, the code would be a synonym for
    "no such command" and §101 would have bought nothing but a longer message.
    """

    code, document = run(capsys, "--json", "bootstrapp")
    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT", document


def test_a_capability_named_like_a_deferred_verb_is_not_swallowed(capsys, prepared) -> None:
    """The refusal is decided by the *verb* position, not by the word.

    `bootstrap` is a word this build does not carry as a verb; it may still be the name of a
    capability or of a path. Only the leading positional token decides, so `where bootstrap` has to
    reach the lookup and answer as a lookup (`found: False`, exit 1) — a guard that fired on the
    word would make a legal capability name unusable.
    """

    code, document = run(capsys, "--json", "--root", str(prepared.path), "where", "bootstrap")
    assert code == 1, document
    assert document["found"] is False and document["reason_code"] == "NOT_FOUND", document

    code, document = run(
        capsys, "--json", "--root", str(prepared.path), "adopt", "bootstrap", "--mode", "reference"
    )
    assert document["reason_code"] != "NOT_IMPLEMENTED", document


# --------------------------------------------------------------------------- #
# the failure document itself (draft §102)
# --------------------------------------------------------------------------- #


def test_the_failure_document_the_cli_prints_satisfies_its_schema(capsys) -> None:
    """This is what `--json` prints whenever a command cannot answer — now a published contract.

    Until §102 it was the only outward document with no schema, which also made it the only one
    `validate_self` could not check while AGENTS.md §7 says the core validates before printing.
    """

    from airoot import schema_io

    code, document = run(capsys, "--json", "--root", str(Path.cwd() / "no-such-root"), "bootstrap")
    assert code == 1, document
    assert schema_io.errors_for("error-response", document) == [], document


def test_a_shape_defect_in_the_failure_document_does_not_mask_the_failure(capsys, monkeypatch) -> None:
    """The error path is the one place where self-validation must not replace what it validates.

    Every other outward document is validated *before* it can replace anything, so a defect is
    reported as `SELF_VALIDATION_FAILED`. Here that would swap the caller's reason code for the
    implementer's and hide the thing they asked about, so §102 appends the defect to `evidence` and
    keeps the original code.
    """

    from airoot.exits import AirootError

    original = AirootError.to_envelope

    def defective(self: AirootError) -> dict:
        document = original(self)
        document.pop("message")  # a real shape defect: a required key is gone
        return document

    monkeypatch.setattr(AirootError, "to_envelope", defective)
    code, document = run(capsys, "--json", "--root", str(Path.cwd() / "no-such-root"), "bootstrap")

    assert code == 1, document
    assert document["reason_code"] == "NOT_IMPLEMENTED", "the failure was masked by its own validation"
    assert any("self-validation failed" in line for line in document["evidence"]), document



def test_global_flags_work_after_the_subcommand(capsys, prepared) -> None:
    """The documented form is ``airoot doctor --json``, not ``airoot --json doctor``."""

    code, document = run(capsys, "doctor", "--json", "--root", str(prepared.path))
    assert code == 0
    assert document["status"] == "healthy"

    code, document = run(capsys, "--json", "--root", str(prepared.path), "doctor")
    assert code == 0
    assert document["status"] == "healthy"


def test_launcher_script_invokes_the_cli(prepared) -> None:
    """``cli/bin/airoot.cmd`` must work with the documented flag order."""

    import subprocess

    launcher = Path(__file__).resolve().parents[1] / "bin" / "airoot.cmd"
    assert launcher.is_file()
    completed = subprocess.run(
        ["cmd", "/c", str(launcher), "--root", str(prepared.path), "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "healthy"


def test_every_documented_command_accepts_json(capsys, prepared) -> None:
    for argv in (
        ("root", "status"),
        ("where", "fake-tool"),
        ("doctor",),
        ("inventory",),
        ("extension", "list"),
        ("extension", "status", "airoot-fake-extension"),
        ("repair",),
    ):
        code = main(["--json", "--root", str(prepared.path), *argv])
        captured = capsys.readouterr()
        assert isinstance(json.loads(captured.out), dict), argv
        assert code in range(10), argv


def test_human_output_goes_to_stdout_and_errors_to_stderr(capsys, prepared) -> None:
    main(["--root", str(prepared.path), "where", "fake-tool"])
    captured = capsys.readouterr()
    assert "found=False" in captured.out, "a not-found result is a result, not an error"

    code = main(["--root", str(prepared.path), "extension", "status", "nope"])
    captured = capsys.readouterr()
    assert code == 9
    assert captured.out == ""
    assert "error:" in captured.err
