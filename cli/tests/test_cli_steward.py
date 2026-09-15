"""CLI contract for the steward domain: data-root / discover / adopt / forget.

These commands must **never** write to the data root: the only permitted side effects
are registry rows and events inside the CLI root.

Note the ``cli_root`` fixture: the plain ``root`` fixture creates only a root marker,
and every steward command needs an initialised registry (otherwise it correctly fails
with ``REGISTRY_MISSING``).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from airoot.canon import digest_text
from airoot.cli import main
from airoot.registry import Registry

PYTHON_PE = Path(sys.executable)


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def data_root(tests_tmp: Path) -> Path:
    """A data root beside (not inside) the CLI root, on the same volume."""

    path = tests_tmp / "dr-env"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def place_pe(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    shutil.copy2(PYTHON_PE, target)
    return target


def add_root(capsys, cli_root: Path, path: Path, *extra: str) -> tuple[int, dict]:
    """Register ``path`` as data root ``dr-env`` (explicit id, so assertions are stable)."""

    return run(capsys, "--json", "--root", str(cli_root), "data-root", "add", str(path), "--id", "dr-env", *extra)


# --------------------------------------------------------------------------- #
# data-root
# --------------------------------------------------------------------------- #


def test_data_root_add_registers_without_touching_files(capsys, cli_root: Path, data_root: Path) -> None:
    (data_root / "keep.txt").write_text("user data", encoding="utf-8")
    before = digest_text("user data")

    code, document = add_root(capsys, cli_root, data_root, "--role", "runtime")

    assert code == 0
    assert document["data_root"]["path"] == str(data_root)
    assert document["data_root"]["role"] == "runtime"
    assert document["data_root"]["whitelist_revision"] == "wl-3"
    assert document["files_touched"] == 0
    assert digest_text((data_root / "keep.txt").read_text(encoding="utf-8")) == before


def test_data_root_add_is_idempotent_for_the_same_path(capsys, cli_root: Path, data_root: Path) -> None:
    assert add_root(capsys, cli_root, data_root, "--role", "runtime")[0] == 0
    code, document = add_root(capsys, cli_root, data_root, "--role", "mixed")
    assert code == 0
    assert document["data_root"]["role"] == "mixed"

    listing = run(capsys, "--json", "--root", str(cli_root), "data-root", "list")[1]
    assert len(listing["data_roots"]) == 1


def test_data_root_inside_the_cli_root_is_refused(capsys, cli_root: Path) -> None:
    code, document = add_root(capsys, cli_root, cli_root / "store")
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert "outside the CLI data root" in document["message"]


def test_data_root_unknown_path_is_refused(capsys, cli_root: Path, tests_tmp: Path) -> None:
    code, document = add_root(capsys, cli_root, tests_tmp / "absent-dir")
    assert code in {8, 6}
    assert document["reason_code"] in {"INVALID_INPUT", "ROOT_NOT_RESOLVED"}


def test_relative_data_root_is_refused(capsys, cli_root: Path) -> None:
    code, document = add_root(capsys, cli_root, Path("relative/path"))
    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_data_root_on_another_volume_is_accepted(capsys, cli_root: Path, tests_tmp: Path) -> None:
    """Nothing in the steward path moves a payload, so the volume is not a constraint.

    Same-volume atomicity only matters for the owned store, which lives inside the CLI
    root by construction. Each data root records **its own** volume serial so cross-drive
    drift stays detectable.
    """

    import tempfile

    from airoot.paths import volume_serial

    cli_volume = volume_serial(cli_root)
    elsewhere = Path(tempfile.mkdtemp(prefix="airoot-crossvol-"))
    try:
        target_volume = volume_serial(elsewhere)
        if target_volume == cli_volume:
            pytest.skip("this environment has only one volume")

        place_pe(elsewhere / "python", "python.exe")
        code, document = add_root(capsys, cli_root, elsewhere, "--role", "runtime")
        assert code == 0, document
        assert document["data_root"]["volume_serial"] == target_volume
        assert document["data_root"]["volume_serial"] != cli_volume

        # Fully usable, not merely registrable.
        code, discovered = run(capsys, "--json", "--root", str(cli_root), "discover")
        assert code == 0
        assert discovered["reports"][0]["counts"].get("external_reference") == 1

        code, adopted = run(capsys, "--json", "--root", str(cli_root), "adopt", str(elsewhere / "python"))
        assert code == 0
        assert adopted["reference"]["data_root_id"] == "dr-env"
        assert adopted["reference"]["observed_digest"]
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_data_root_forget_unknown_is_not_found(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "data-root", "forget", "dr-nope")
    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_data_root_forget_keeps_files(capsys, cli_root: Path, data_root: Path) -> None:
    marker = data_root / "keep.txt"
    marker.write_text("user data", encoding="utf-8")
    add_root(capsys, cli_root, data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "data-root", "forget", "dr-env")

    assert code == 0
    assert document["files_touched"] == 0
    assert marker.read_text(encoding="utf-8") == "user data"


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #


def test_discover_reports_candidates_and_exit_zero(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    add_root(capsys, cli_root, data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "discover")

    assert code == 0
    assert document["reason_code"] == "SUCCESS"
    assert document["files_touched"] == 0
    report = document["reports"][0]
    assert report["counts"] == {"external_reference": 1}
    candidate = report["candidates"][0]
    assert candidate["capability_id"] == "python"
    assert candidate["external_id"] == "external/dr-env/python"


def test_discover_without_data_roots_is_empty(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "discover")
    assert code == 0
    assert document["reports"] == []


def test_discover_unknown_data_root_is_not_found(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "discover", "--data-root", "dr-nope")
    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_discover_missing_directory_is_recovery_required(capsys, cli_root: Path, data_root: Path) -> None:
    add_root(capsys, cli_root, data_root)
    shutil.rmtree(data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "discover")

    assert code == 6
    assert document["reason_code"] == "DATA_ROOT_MISSING"
    assert document["missing"]


def test_discover_is_read_only_by_default(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    add_root(capsys, cli_root, data_root)

    run(capsys, "--json", "--root", str(cli_root), "discover")

    registry = Registry.open(cli_root)
    try:
        assert registry.external_references() == []
    finally:
        registry.close()


def test_discover_record_persists_only_non_owning_observations(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    place_pe(data_root / "mystery", "mystery.exe")
    add_root(capsys, cli_root, data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "discover", "--record")

    assert code == 0
    assert document["recorded"] == 1, "only the unmatched object is recorded"

    registry = Registry.open(cli_root)
    try:
        rows = registry.external_references()
        assert [row["management"] for row in rows] == ["unmanaged"]
        assert rows[0]["external_id"] == "external/dr-env/mystery"
        assert all(row["management"] != "external_reference" for row in rows), "discover never adopts"
    finally:
        registry.close()


# --------------------------------------------------------------------------- #
# adopt / forget
# --------------------------------------------------------------------------- #


def test_adopt_reference_records_facts_without_ownership(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    add_root(capsys, cli_root, data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"))

    assert code == 0
    assert document["ownership"] == "none"
    assert document["files_touched"] == 0
    reference = document["reference"]
    assert reference["capability_id"] == "python"
    assert reference["management"] == "external_reference"
    assert reference["probe_level"] == 2
    assert reference["observed_digest"], "the entrypoint digest is the bounded observation"
    assert reference["data_root_id"] == "dr-env"


def test_adopted_reference_appears_in_inventory(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    add_root(capsys, cli_root, data_root)
    run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"))

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "inventory", "--class", "external_reference"
    )

    assert code == 0
    assert [item["capability_id"] for item in document["external_references"]] == ["python"]
    assert document["instances"] == []


def test_adopt_without_a_matching_capability_is_refused(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "mystery", "mystery.exe")
    add_root(capsys, cli_root, data_root)

    code, document = run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "mystery"))

    assert code == 9
    assert document["reason_code"] == "CAPABILITY_NOT_DECLARED"


def test_adopt_outside_a_data_root_is_refused(capsys, cli_root: Path, tests_tmp: Path) -> None:
    loose = tests_tmp / "loose-object"
    place_pe(loose, "python.exe")

    code, document = run(capsys, "--json", "--root", str(cli_root), "adopt", str(loose))

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert "registered data root" in document["message"]


def test_adopt_import_is_not_implemented(capsys, cli_root: Path, data_root: Path) -> None:
    place_pe(data_root / "python", "python.exe")
    add_root(capsys, cli_root, data_root)

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"), "--mode", "import"
    )

    assert code == 7
    assert document["reason_code"] == "UNSUPPORTED_BACKEND"
    assert "portable" in " ".join(document["evidence"])


def test_forget_drops_the_record_and_keeps_the_file(capsys, cli_root: Path, data_root: Path) -> None:
    executable = place_pe(data_root / "python", "python.exe")
    before = executable.read_bytes()
    add_root(capsys, cli_root, data_root)
    run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"))

    code, document = run(capsys, "--json", "--root", str(cli_root), "forget", "external/dr-env/python")

    assert code == 0
    assert document["files_touched"] == 0
    assert document["source_unchanged"] is True
    assert executable.read_bytes() == before
    assert document["project_manifest_check"] == "not_implemented_before_p6"


@pytest.fixture(scope="module")
def version_marker_layout(tmp_path_factory):
    """An object with a version marker, built before the execution guard exists.

    Two identical copies of the host interpreter are enough to exercise the wiring: the
    version set has one entry, and the junction target proves which copy is active.
    """

    import subprocess

    base = tmp_path_factory.mktemp("versionmarker")
    root = base / "env"
    object_dir = root / "tool"
    for name in ("a", "b"):
        (object_dir / name).mkdir(parents=True)
        shutil.copy2(PYTHON_PE, object_dir / name / "python.exe")
    link = object_dir / "current"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(object_dir / "b")], capture_output=True, text=True
    )
    if created.returncode != 0 or not link.exists():
        pytest.skip(f"cannot create a junction here: {created.stderr.strip()}")
    return root


def test_adopt_records_the_version_set_and_the_observed_active_version(
    capsys, cli_root: Path, version_marker_layout: Path
) -> None:
    add_root(capsys, cli_root, version_marker_layout)

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "adopt", str(version_marker_layout / "tool")
    )

    assert code == 0, document
    reference = document["reference"]
    assert reference["capability_id"] == "python"
    assert reference["versions"], "the version set is recorded"
    assert reference["active_version"] == reference["versions"][0], "the junction target is the active version"
    assert reference["version"] == reference["active_version"]
    assert any(item["kind"] == "junction_target" for item in reference["evidence"])

    # And it survives the projection round trip.
    listing = run(
        capsys, "--json", "--root", str(cli_root), "inventory", "--class", "external_reference"
    )[1]
    assert listing["external_references"][0]["active_version"] == reference["active_version"]


def test_forget_unknown_reference_is_not_found(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "forget", "external/nope")
    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_steward_commands_appear_in_help() -> None:
    from airoot.cli import build_parser

    help_text = build_parser().format_help()
    for command in ("data-root", "discover", "adopt", "forget"):
        assert command in help_text


# --------------------------------------------------------------------------- #
# steward-first where + data-root doctor (draft §6, §9)
# --------------------------------------------------------------------------- #


def test_cli_discover_record_keeps_derived_state_in_step(
    capsys, cli_root: Path, data_root: Path
) -> None:
    """Recording observations appends events, so the derived projections must be rewritten too.

    Found on the real machine: `discover --record` used to update the projection only when a
    data root was missing, so `doctor` reported AUDIT_PROJECTION_DRIFT immediately afterwards.
    """

    place_pe(data_root / "mystery", "unknown-tool.exe")
    assert add_root(capsys, cli_root, data_root, "--role", "mixed")[0] == 0

    code, document = run(capsys, "--json", "--root", str(cli_root), "discover", "--record")
    assert code == 0
    assert document["recorded"] >= 1

    doctor_code, diagnosis = run(capsys, "--json", "--root", str(cli_root), "doctor")
    codes = {item["code"] for item in diagnosis["diagnostics"]}
    assert "AUDIT_PROJECTION_DRIFT" not in codes
    assert "REGISTRY_PROJECTION_STALE" not in codes
    assert doctor_code in {0, 2}


def test_cli_where_selects_a_healthy_reference_without_a_flag(
    capsys, cli_root: Path, data_root: Path
) -> None:
    """The reference is a first-class candidate now: no owned payload, no flag, no fallback."""

    place_pe(data_root / "python", "python.exe")
    assert add_root(capsys, cli_root, data_root, "--role", "runtime")[0] == 0
    assert run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"))[0] == 0

    code, document = run(capsys, "--json", "--root", str(cli_root), "where", "python")

    assert code in {0, 2}, document
    assert document["found"] is True
    assert document["management"] == "external_reference"
    assert document["selection_reason"] == "STEWARD_REFERENCE_HEALTHY"
    assert any("precedence=steward" in item["detail"] for item in document["evidence"])


def test_cli_doctor_reports_data_roots_and_gates_unmanaged_noise(
    capsys, cli_root: Path, data_root: Path
) -> None:
    place_pe(data_root / "python", "python.exe")
    place_pe(data_root / "mystery", "unknown-tool.exe")
    assert add_root(capsys, cli_root, data_root, "--role", "mixed")[0] == 0
    assert run(capsys, "--json", "--root", str(cli_root), "adopt", str(data_root / "python"))[0] == 0
    assert run(capsys, "--json", "--root", str(cli_root), "discover", "--record")[0] == 0

    quiet = run(capsys, "--json", "--root", str(cli_root), "doctor")[1]
    quiet_codes = {item["code"] for item in quiet["diagnostics"]}
    assert "DATA_ROOT_MISSING" not in quiet_codes
    assert "UNMANAGED_OBJECT_PRESENT" not in quiet_codes, "unmanaged noise is opt-in (S-029)"

    loud = run(capsys, "--json", "--root", str(cli_root), "doctor", "--include-unmanaged")[1]
    loud_codes = {item["code"] for item in loud["diagnostics"]}
    assert "UNMANAGED_OBJECT_PRESENT" in loud_codes
    assert all(
        item["remediation"] == "none"
        for item in loud["diagnostics"]
        if item["code"] == "UNMANAGED_OBJECT_PRESENT"
    )
