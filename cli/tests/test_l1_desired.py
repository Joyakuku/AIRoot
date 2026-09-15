"""L1: the ``desired`` layer and ``tool pin`` (规划 §17, §15.4:1604, draft §27).

The five-layer model had one layer with no code. What these tests pin down:

* ``desired`` is an input, not an authority: pinning writes ``state/desired.json`` and **never**
  touches the registry, a binding or a generation;
* ``pin`` produces a plan when the declared state does not satisfy the wish — and applying that
  plan still needs an approval, exactly like any other transaction;
* a manifest carries state, never a script (§17's explicit "must not contain" list).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fake_issuer
from airoot.caps.desired import (
    DESIRED_RELATIVE,
    DesiredManifest,
    clear_pin,
    desired_path,
    evaluate,
    load_desired,
    pin,
    save_desired,
)
from airoot.caps.sources import sha256_file
from airoot.cli import main
from airoot.exits import AirootError
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


def install(registry, clock, root, version: str = "1.0.0") -> str:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return str(plan["target"]["instance_id"])


# --------------------------------------------------------------------------- #
# the manifest
# --------------------------------------------------------------------------- #


def test_an_absent_manifest_is_empty_not_an_error(tmp_path: Path) -> None:
    manifest = load_desired(tmp_path)

    assert manifest.capabilities == []
    assert manifest.manifest_revision == 0
    assert not desired_path(tmp_path).exists()


def test_pinning_records_the_wish_and_bumps_the_revision(tmp_path: Path) -> None:
    manifest = pin(tmp_path, capability_id="python", version=">=3.11,<3.13")

    assert manifest.manifest_revision == 1
    assert desired_path(tmp_path).is_file()
    stored = load_desired(tmp_path)
    assert stored.find("python").version == ">=3.11,<3.13"

    again = pin(tmp_path, capability_id="python", version="3.12.7")
    assert again.manifest_revision == 2
    assert len(again.capabilities) == 1, "re-pinning replaces the entry, it does not duplicate it"
    assert again.find("python").version == "3.12.7"


def test_clearing_a_pin_that_does_not_exist_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as caught:
        clear_pin(tmp_path, capability_id="python")
    assert caught.value.reason_code == "NOT_FOUND"


def test_an_unparseable_constraint_is_refused_before_it_is_stored(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as caught:
        pin(tmp_path, capability_id="python", version="~>3.11")
    assert caught.value.reason_code == "INVALID_INPUT"
    assert not desired_path(tmp_path).exists(), "nothing is written when the constraint is wrong"


def test_a_manifest_may_not_carry_a_script(tmp_path: Path) -> None:
    """§17: a desired manifest expresses state; it never carries a command."""

    path = tmp_path / DESIRED_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "manifest_id": "x",
                "manifest_revision": 1,
                "capabilities": [{"id": "python", "install": "curl | sh"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AirootError) as caught:
        load_desired(tmp_path)
    assert "shell fragment" in caught.value.message


def test_a_manifest_with_the_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / DESIRED_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 99, "capabilities": []}), encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        load_desired(tmp_path)
    assert "schema_version" in caught.value.message


def test_the_fingerprint_changes_with_the_content(tmp_path: Path) -> None:
    first = pin(tmp_path, capability_id="python", version="3.11").fingerprint()
    second = pin(tmp_path, capability_id="python", version="3.12").fingerprint()

    assert first != second


# --------------------------------------------------------------------------- #
# desired vs declared
# --------------------------------------------------------------------------- #


def test_an_unsatisfied_pin_is_reported(registry, clock, root) -> None:
    manifest = DesiredManifest(capabilities=[])

    entries = evaluate(registry, manifest)
    assert entries == []

    from airoot.caps.desired import DesiredCapability

    manifest.capabilities.append(DesiredCapability(capability_id=CAPABILITY, version=">=9"))
    entries = evaluate(registry, manifest)
    assert entries[0].in_sync is False
    assert "nothing active" in entries[0].detail


def test_a_satisfied_pin_is_in_sync(registry, clock, root) -> None:
    install(registry, clock, root, version="1.0.0")
    from airoot.caps.desired import DesiredCapability

    manifest = DesiredManifest(
        capabilities=[DesiredCapability(capability_id=CAPABILITY, version=">=1.0,<2")]
    )

    entries = evaluate(registry, manifest)

    assert entries[0].in_sync is True
    assert entries[0].active_version == "1.0.0"


def test_a_pin_that_conflicts_with_what_is_installed_is_not_in_sync(registry, clock, root) -> None:
    install(registry, clock, root, version="1.0.0")
    from airoot.caps.desired import DesiredCapability

    manifest = DesiredManifest(
        capabilities=[DesiredCapability(capability_id=CAPABILITY, version=">=2")]
    )

    entry = evaluate(registry, manifest)[0]

    assert entry.in_sync is False
    assert "does not satisfy" in entry.detail


def test_evaluate_never_changes_the_generation(registry, clock, root) -> None:
    install(registry, clock, root)
    from airoot.caps.desired import DesiredCapability

    before = registry.generation
    evaluate(registry, DesiredManifest(capabilities=[DesiredCapability(CAPABILITY, ">=1")]))

    assert registry.generation == before


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_pin_records_desired_without_touching_the_binding(
    capsys, cli_root: Path, registry
) -> None:
    generation_before = registry.generation

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY, "--version", "1.0.0"
    )

    assert code == 0, document
    assert document["active_binding_changed"] is False
    assert document["manifest_revision"] == 1
    assert document["in_sync"] is False, "nothing is installed yet"
    assert Path(document["manifest_path"]).is_file()
    assert registry.generation == generation_before, "desired state is not declared state"


def test_cli_pin_writes_nothing_to_the_registry(capsys, cli_root: Path, registry) -> None:
    run(capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY, "--version", "1.0.0")

    assert registry.instances() == []
    assert registry.bindings() == []


def test_cli_pin_on_an_installed_capability_is_in_sync(capsys, cli_root: Path, installed) -> None:
    code, document = run(
        capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY, "--version", "1.0.0"
    )

    assert code == 0
    assert document["in_sync"] is True
    assert document["plan"] is None, "an already-satisfied pin needs no plan"


def test_cli_pin_without_a_version_is_refused(capsys, cli_root: Path) -> None:
    code, document = run(capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY)

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_cli_pin_offers_a_real_plan_when_a_source_exists(
    capsys, cli_root: Path, registry, tmp_path: Path
) -> None:
    """`build` has a trusted source, so pinning it can produce a plan (still unapplied)."""

    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "cmake-3.31.6-windows-x86_64.zip"
    artifact.write_bytes(b"portable archive\n")
    checksums = release / "SHA-256.txt"
    checksums.write_text(f"{sha256_file(artifact)[7:]}  {artifact.name}\n", encoding="utf-8")

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "tool", "pin", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
    )

    assert code == 0, document
    assert document["plan"] is not None, document
    assert document["plan"]["metadata"]["backend_id"] == "portable_file"
    assert Path(document["plan_file"]).is_file()
    assert document["active_binding_changed"] is False
    assert registry.instances() == [], "offering a plan installs nothing"


def test_cli_pin_reports_when_no_plan_can_be_built(capsys, cli_root: Path) -> None:
    """`java` has no trusted source, so the pin is recorded and the plan is honestly absent."""

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "tool", "pin", "java", "--version", "25.0.2.0"
    )

    assert code == 0, document
    assert document["plan"] is None
    assert document["sync"][0]["plan_blocked_by"] == "NOT_FOUND"
    assert Path(document["manifest_path"]).is_file(), "the wish is recorded even when no plan exists"


def test_cli_pin_clear(capsys, cli_root: Path) -> None:
    run(capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY, "--version", "1.0.0")

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "tool", "pin", CAPABILITY, "--clear"
    )

    assert code == 0
    assert document["desired"] == []


@pytest.fixture
def installed(registry, clock, root) -> str:
    return install(registry, clock, root)


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #


def test_doctor_is_silent_when_nothing_is_pinned(registry, clock, root) -> None:
    """A root that never pinned anything must diagnose exactly as before (draft §28.2-4)."""

    from airoot.caps.doctor import diagnostics_by_code, doctor

    document = doctor(root.path, registry=registry, data_roots=False)

    assert "DESIRED_NOT_SATISFIED" not in diagnostics_by_code(document)


def test_doctor_reports_an_unsatisfied_pin(registry, clock, root, cli_root: Path) -> None:
    from airoot.caps.doctor import diagnostics_by_code, doctor

    install(registry, clock, root, version="1.0.0")
    pin(cli_root, capability_id=CAPABILITY, version=">=2")

    document = doctor(root.path, registry=registry, data_roots=False)
    entry = diagnostics_by_code(document)["DESIRED_NOT_SATISFIED"]

    assert entry["severity"] == "warning"
    assert entry["capability"] == CAPABILITY
    assert document["status"] == "degraded", "an unsatisfied pin degrades, it does not break"
    assert any("does not satisfy" in item for item in entry["evidence"])


def test_doctor_is_silent_when_the_pin_is_satisfied(registry, clock, root, cli_root: Path) -> None:
    from airoot.caps.doctor import diagnostics_by_code, doctor

    install(registry, clock, root, version="1.0.0")
    pin(cli_root, capability_id=CAPABILITY, version=">=1.0,<2")

    document = doctor(root.path, registry=registry, data_roots=False)

    assert "DESIRED_NOT_SATISFIED" not in diagnostics_by_code(document)


def test_a_broken_manifest_is_diagnosed_not_raised(registry, clock, root, cli_root: Path) -> None:
    """A diagnostic tool answers for bad input instead of throwing (draft §28.2-3)."""

    from airoot.caps.doctor import diagnostics_by_code, doctor

    path = desired_path(cli_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    document = doctor(root.path, registry=registry, data_roots=False)

    entry = diagnostics_by_code(document)["DESIRED_NOT_SATISFIED"]
    assert entry["severity"] == "warning"
    assert "cannot be determined" in entry["impact"]


def test_tool_list_shows_the_pin_and_the_sync_state(registry, clock, root, cli_root: Path) -> None:
    from airoot.caps.toolstate import list_tools

    install(registry, clock, root, version="1.0.0")
    pin(cli_root, capability_id=CAPABILITY, version=">=2")

    document = list_tools(registry, root=cli_root)

    assert document["instances"][0]["desired_version"] == ">=2"
    assert document["instances"][0]["in_sync"] is False
    assert document["out_of_sync"] == [document["instances"][0]["instance_id"]]
    assert document["desired"] == [{"id": CAPABILITY, "scope": "machine", "version": ">=2"}]


def test_tool_list_without_a_root_reports_no_pins(registry, clock, root) -> None:
    from airoot.caps.toolstate import list_tools

    install(registry, clock, root)

    document = list_tools(registry)

    assert document["desired"] == []
    assert document["instances"][0]["desired_version"] is None
    assert document["instances"][0]["in_sync"] is None, "no pin means unknown, not False"
