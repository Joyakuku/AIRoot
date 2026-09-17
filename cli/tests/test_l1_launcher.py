"""L1: the stable entry — a static `.cmd` per capability (ADR-0050).

The minimum version's judgement 10 asks for something callable **without editing the machine PATH**,
and the decision record (ADR-0050) fixed its shape. These tests are about the four promises that shape
makes, because each of them is the kind of claim that stays true only while something measures it:

1. **the bytes are version-independent** — so installing a newer version must not rewrite the file
   (measured, not assumed: the digest is compared across two installs);
2. **the registry stays the only authority** — nothing in the file names a version, an instance or a
   store path, and the file asks the CLI to resolve the *active binding* at call time;
3. **it is written inside the transaction** — `EXPOSED` is the exposure write side, so a root that has
   been through an install has the entry, and `path verify` sees it;
4. **drift is reported, never assumed away** — a hand-edited file, an entry with no binding and a
   binding with no entry are all `PATH_EXPOSURE_VIOLATION` (the frozen code; no new ones).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import fake_issuer  # noqa: F401  (the transaction path needs the test issuer)
import pytest

from airoot.caps.launcher import (
    LAUNCHER_SUFFIX,
    discover_launchers,
    launcher_directory,
    launcher_name,
    launcher_path,
    render_launcher,
    write_launcher,
)
from airoot.caps.pathexposure import verify_path_exposure
from airoot.cli import main
from airoot.exits import AirootError
from airoot.registry import Binding, Registry, binding_key
from airoot.registry.entities import Instance
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "probe-tool"


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


def declare_runnable(registry, root: Path, *, capability: str = CAPABILITY, instance_id: str | None = None):
    """An owned instance whose main entrypoint really starts, plus its active machine binding."""

    instance_id = instance_id or f"{capability}/probe/1.0.0/win-x64"
    store_dir = Path(root) / "store" / instance_id
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "probe.cmd").write_text("@echo off\r\necho launcher-child-ran\r\nexit /b 0\r\n", encoding="utf-8")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id=instance_id,
                kind="managed_tool",
                capability_id=capability,
                version="1.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id="https_artifact",
                artifact_digest="sha256:" + "b" * 64,
                store_path=f"store/{instance_id}",
                lifecycle_status="installed",
                health="healthy",
                entrypoints=("probe.cmd",),
                created_at="2024-01-01T00:00:00Z",
            ),
        )
        registry.bind_active(
            connection,
            Binding(
                binding_key(capability, "machine"),
                instance_id,
                "machine",
                "R",
                "stable_launcher",
                registry.generation + 1,
                True,
            ),
        )
    return instance_id


# --------------------------------------------------------------------------- #
# the bytes
# --------------------------------------------------------------------------- #


def test_the_launcher_bytes_are_crlf_and_name_no_version(tmp_path: Path) -> None:
    payload = render_launcher(
        capability_id="cargo", root=tmp_path, python=r"C:\py\python.exe", cli_app=Path(r"C:\repo\cli\app")
    )
    text = payload.decode("utf-8")

    assert payload.count(b"\r\n") == text.count("\n"), "cmd.exe splits `rem` lines on a bare LF"
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", ""), "no bare LF anywhere"
    assert "run --capability cargo -- %*" in text, (
        "the forwarding line is the point, and `--` is part of it: without the separator the "
        "caller's own flags (`cargo --version`) are parsed by AIROOT instead of the payload"
    )
    assert "exit /b %ERRORLEVEL%" in text, "the child's status has to come back"
    for forbidden in ("1.0.0", "store", "instance_id"):
        assert forbidden not in text, f"the stable entry must not contain {forbidden!r}: {forbidden} is the version"


def test_rendering_is_pure_and_the_name_is_the_capability(tmp_path: Path) -> None:
    first = render_launcher(capability_id="cargo", root=tmp_path, python="p", cli_app=Path("c"))
    second = render_launcher(capability_id="cargo", root=tmp_path, python="p", cli_app=Path("c"))

    assert first == second
    assert launcher_name("cargo") == "cargo" + LAUNCHER_SUFFIX
    assert launcher_path(tmp_path, "cargo") == launcher_directory(tmp_path) / "cargo.cmd"


def test_a_capability_id_that_cannot_be_a_file_name_is_refused(tmp_path: Path) -> None:
    """Cleaning a name up would produce a file a caller cannot ask for by the name it used."""

    for bad in ("../../escape", "a/b", "a\\b", "", ".hidden"):
        with pytest.raises(AirootError) as info:
            write_launcher(tmp_path, bad)
        assert info.value.reason_code == "INVALID_INPUT"
    assert not (tmp_path / "cli").exists(), "nothing may be created for a refused write"


# --------------------------------------------------------------------------- #
# writing, idempotence, drift
# --------------------------------------------------------------------------- #


def test_writing_twice_changes_nothing_and_a_hand_edit_is_repaired(tmp_path: Path) -> None:
    first = write_launcher(tmp_path, "cargo", python="p", cli_app=Path("c"))
    assert first.created is True and first.rewritten is False
    before = launcher_path(tmp_path, "cargo").read_bytes()

    again = write_launcher(tmp_path, "cargo", python="p", cli_app=Path("c"))
    assert again.created is False and again.rewritten is False, "a replay must not look like a change"
    assert launcher_path(tmp_path, "cargo").read_bytes() == before
    assert again.digest == first.digest

    path = launcher_path(tmp_path, "cargo")
    path.write_bytes(b"@echo off\r\nrem tampered\r\n")
    drifted = discover_launchers(tmp_path, python="p", cli_app=Path("c"))
    assert [item.matches_current for item in drifted] == [False], "a hand edit must be visible"

    repaired = write_launcher(tmp_path, "cargo", python="p", cli_app=Path("c"))
    assert repaired.rewritten is True and repaired.created is False
    assert path.read_bytes() == before


def test_discovering_an_absent_directory_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    assert discover_launchers(tmp_path) == []
    assert not launcher_directory(tmp_path).exists()


# --------------------------------------------------------------------------- #
# the transaction writes it, and switching a version does not
# --------------------------------------------------------------------------- #


def test_the_exposure_step_writes_the_stable_entry(capsys, registry, clock, root, monkeypatch) -> None:
    monkeypatch.setattr(sys, "executable", sys.executable)
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, fake_issuer.issue(plan, clock=clock))

    capability = str(plan["target"]["capability_id"])
    path = launcher_path(root.path, capability)
    assert path.is_file(), "EXPOSED is the exposure write side (ADR-0050)"
    assert path.read_bytes().count(b"\r\n") > 0

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "verify")
    assert document["launcher_present"] is True
    assert document["expected_launchers"] == [capability]
    assert document["violations"] == 0, document["findings"]
    assert code == 0


def test_a_new_version_rewrites_no_launcher_bytes(registry, clock, root) -> None:
    """The whole point of a *stable* entry: the version changes under it, the file does not."""

    fake_issuer.install_keyring(root.path)
    first = create_plan(registry, version="1.0.0", clock=clock)
    SimulationRunner(registry, clock=clock).commit(first, fake_issuer.issue(first, clock=clock))
    capability = str(first["target"]["capability_id"])
    path = launcher_path(root.path, capability)
    before = path.read_bytes()

    second = create_plan(registry, version="2.0.0", clock=clock)
    SimulationRunner(registry, clock=clock).commit(second, fake_issuer.issue(second, clock=clock))

    assert second["target"]["instance_id"] != first["target"]["instance_id"], "a different version was installed"
    assert path.read_bytes() == before, "a version switch must not touch the stable entry"


# --------------------------------------------------------------------------- #
# `run --capability`: what the launcher calls
# --------------------------------------------------------------------------- #


def test_run_with_a_capability_resolves_the_active_binding(capsys, registry, root) -> None:
    instance_id = declare_runnable(registry, root.path)

    code, document = run(capsys, "--json", "--root", str(root.path), "run", "--capability", CAPABILITY)

    assert code == 0, document
    assert document["instance_id"] == instance_id
    assert document["exit_status"] == 0
    assert document["reason_code"] == "SUCCESS"


def test_run_refuses_an_ambiguous_or_empty_target(capsys, registry, root) -> None:
    instance_id = declare_runnable(registry, root.path)

    both = run(
        capsys, "--json", "--root", str(root.path), "run", instance_id, "--capability", CAPABILITY
    )
    neither = run(capsys, "--json", "--root", str(root.path), "run")

    assert both[0] == 8 and both[1]["reason_code"] == "INVALID_INPUT"
    assert neither[0] == 8 and neither[1]["reason_code"] == "INVALID_INPUT"


def test_run_with_an_unbound_capability_is_not_found(capsys, registry, root) -> None:
    code, document = run(capsys, "--json", "--root", str(root.path), "run", "--capability", "nothing-bound")

    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_two_active_bindings_for_one_capability_are_refused_not_ordered(capsys, registry, root) -> None:
    """Ordering would be exactly the guess the stable entry exists to remove."""

    declare_runnable(registry, root.path)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id="probe-tool/probe/2.0.0/win-x64",
                kind="managed_tool",
                capability_id=CAPABILITY,
                version="2.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id="https_artifact",
                artifact_digest="sha256:" + "c" * 64,
                store_path="store/probe-tool/probe/2.0.0/win-x64",
                lifecycle_status="installed",
                health="healthy",
                entrypoints=("probe.cmd",),
                created_at="2024-01-01T00:00:00Z",
            ),
        )
        registry.bind_active(
            connection,
            Binding(
                binding_key(CAPABILITY, "session", session_id="s1"),
                "probe-tool/probe/2.0.0/win-x64",
                "session",
                "W",
                "session_env",
                registry.generation + 1,
                True,
            ),
        )

    code, document = run(capsys, "--json", "--root", str(root.path), "run", "--capability", CAPABILITY)

    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT"
    assert len(document["evidence"]) == 2, "both bindings are named, so the caller can choose"


def test_the_launcher_itself_forwards(registry, clock, root) -> None:
    """The strongest form of judgement 10: `cmd.exe` runs the file and the child's output comes back."""

    if sys.platform != "win32":  # pragma: no cover - v1 is Windows only
        pytest.skip("the stable entry is a .cmd")
    declare_runnable(registry, root.path)
    written = write_launcher(root.path, CAPABILITY)

    completed = subprocess.run(
        [str(written.path), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root.path),
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "launcher-child-ran" in completed.stdout, (
        "the file exists but does not forward; that is the failure mode this test is for: "
        f"{completed.stdout!r} {completed.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# drift is reported
# --------------------------------------------------------------------------- #


def test_path_verify_reports_a_binding_without_an_entry(tmp_path: Path) -> None:
    verification = verify_path_exposure(
        tmp_path, machine_entries=[], user_entries=[], expected_launchers=["cargo"]
    )

    assert verification.launcher_present is False
    assert verification.violations == 1
    assert verification.reason_code() == "PATH_EXPOSURE_VIOLATION"
    assert any("no stable entry" in finding.detail for finding in verification.findings)


def test_path_verify_reports_an_entry_without_a_binding(tmp_path: Path) -> None:
    # Written with this build's own interpreter and code path: the drift check is a re-render, so a
    # launcher written with fake paths is *itself* drift and would report two findings here.
    write_launcher(tmp_path, "cargo")

    verification = verify_path_exposure(tmp_path, machine_entries=[], user_entries=[], expected_launchers=[])

    assert verification.launcher_present is True
    assert verification.launchers[0]["matches_current"] is True
    assert verification.violations == 1
    assert any("resolves nothing" in finding.detail for finding in verification.findings)


def test_path_verify_reports_an_unreadable_registry_as_information(tmp_path: Path) -> None:
    """A read-only diagnosis must keep working on a root whose registry cannot be read."""

    verification = verify_path_exposure(
        tmp_path, machine_entries=[], user_entries=[], bindings_note="the registry is not readable (X)"
    )

    assert verification.violations == 0
    assert verification.reason_code() == "SUCCESS"
    assert any("not readable" in finding.detail for finding in verification.findings)


def test_the_reported_launcher_is_the_one_the_where_lane_can_read(capsys, registry, clock, root) -> None:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, fake_issuer.issue(plan, clock=clock))
    capability = str(plan["target"]["capability_id"])

    code, document = run(capsys, "--json", "--root", str(root.path), "where", capability)

    assert code == 0, document
    assert document["launcher"] == str(launcher_path(root.path, capability))
    assert Path(document["launcher"]).is_file()


# --------------------------------------------------------------------------- #
# `path repair`: the write half of the same surface (draft §165)
# --------------------------------------------------------------------------- #
#
# `path verify` could report "an active binding has no stable entry" while **nothing** could put the
# entry back: `rebuild` only rewrites its two projection files, and deleting (or creating) objects
# inside the root otherwise goes through a plan plus an approval. The operator's real root was in
# exactly that state. `path repair` is the write half, and these are its promises.


def test_path_repair_writes_the_missing_entry_and_verify_goes_green(capsys, registry, root) -> None:
    """The shape the fix exists for: a machine-level binding, no entry, and a verb that puts it back."""

    declare_runnable(registry, root.path)
    entry = launcher_path(root.path, CAPABILITY)
    assert not entry.is_file(), "the binding was declared without going through EXPOSED"

    code, before = run(capsys, "--json", "--root", str(root.path), "path", "verify")
    assert code == 2 and before["violations"] == 1, before
    assert before["expected_launchers"] == [CAPABILITY], before

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")

    assert code == 0, document
    assert document["status"] == "ok" and document["reason_code"] == "SUCCESS", (
        "a repair report is a success document; `OK` is not a code this build may emit (ADR-0003)"
    )
    assert document["expected"] == [CAPABILITY]
    assert document["repaired"] == [CAPABILITY]
    assert document["created"] == [CAPABILITY]
    assert document["rewritten"] == [] and document["already_present"] == []
    assert document["path_written"] is False, "writing machine PATH belongs to the P2 broker"
    assert document["bindings_changed"] is False
    assert document["files_deleted"] == 0
    assert entry.read_bytes() == render_launcher(capability_id=CAPABILITY, root=root.path), (
        "the repaired entry must be byte-identical to the one the EXPOSED step writes: same renderer, "
        "no second answer to 'what does a stable entry look like'"
    )

    code, after = run(capsys, "--json", "--root", str(root.path), "path", "verify")
    assert code == 0, after
    assert after["violations"] == 0 and after["launcher_present"] is True, after


def test_path_repair_is_idempotent_and_reports_already_present(capsys, registry, root) -> None:
    """A second run must change **no byte** and must say so instead of reporting a repair."""

    declare_runnable(registry, root.path)
    run(capsys, "--json", "--root", str(root.path), "path", "repair")
    entry = launcher_path(root.path, CAPABILITY)
    before = entry.read_bytes()

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")

    assert code == 0, document
    assert document["repaired"] == [] and document["created"] == [] and document["rewritten"] == []
    assert document["already_present"] == [CAPABILITY], "the report has to distinguish the two cases"
    assert entry.read_bytes() == before, "an already-consistent entry is not rewritten"


def test_path_repair_restores_an_entry_this_build_did_not_write(capsys, registry, root) -> None:
    """The other drift `path verify` reports: a hand edit (or a moved interpreter/checkout)."""

    declare_runnable(registry, root.path)
    entry = launcher_path(root.path, CAPABILITY)
    write_launcher(root.path, CAPABILITY)
    entry.write_bytes(b"@echo off\r\nrem tampered\r\n")

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")

    assert code == 0, document
    assert document["rewritten"] == [CAPABILITY] and document["created"] == []
    assert document["repaired"] == [CAPABILITY] and document["already_present"] == []
    assert entry.read_bytes() == render_launcher(capability_id=CAPABILITY, root=root.path)
    assert run(capsys, "--json", "--root", str(root.path), "path", "verify")[0] == 0


def test_path_repair_refuses_a_root_whose_registry_cannot_be_read(capsys, registry, root) -> None:
    """An unknown expectation is not an empty one — so the write half refuses (exit 6).

    The hole this closes: `_expected_launchers` answers ``([], note)`` when the registry cannot be
    read, and a verb that treated that as "nothing to do" would print a success document about a root
    it could not describe. It only writes, so it could not make the root worse — but it may not claim
    a consistency it never measured.
    """

    declare_runnable(registry, root.path)
    entry = launcher_path(root.path, CAPABILITY)
    registry.close()
    moved = Path(root.path) / "state" / "registry.db.away"
    (Path(root.path) / "state" / "registry.db").rename(moved)
    try:
        code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")
    finally:
        moved.rename(Path(root.path) / "state" / "registry.db")

    assert code == 6, document
    assert document["reason_code"] == "REGISTRY_MISSING", document
    assert any("not readable" in item for item in document["evidence"]), (
        "the note `path verify` carries has to travel into the refusal, or the two verbs describe "
        "the same root differently"
    )
    assert not entry.is_file(), "a refused repair must not have written anything"


def test_path_repair_leaves_a_root_with_nothing_expected_alone(capsys, registry, root) -> None:
    """No active machine-level binding **and** no entry: nothing is written and nothing is wrong.

    This is also the non-vacuity half of the orphan rule below: a root with no orphan must keep
    reporting `SUCCESS` and exit 0, or "always exit 2" would pass that guard.
    """

    entry = launcher_path(root.path, CAPABILITY)
    directory = launcher_directory(root.path)
    before = sorted(item.name for item in directory.glob("*")) if directory.is_dir() else []

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")

    assert code == 0, document
    assert document["status"] == "ok" and document["reason_code"] == "SUCCESS", document
    assert document["expected"] == [] and document["repaired"] == []
    assert document["already_present"] == [] and document["bindings_note"] is None
    assert document["orphans"] == [] and document["warnings"] == [], (
        "with no entry there is no orphan to report; the warning belongs to that case only"
    )
    assert not entry.exists(), "an empty expectation must not create an entry"
    after = sorted(item.name for item in directory.glob("*")) if directory.is_dir() else []
    assert after == before == [], "an empty expectation must not write anything at all"
    code, verification = run(capsys, "--json", "--root", str(root.path), "path", "verify")
    assert code == 0 and verification["violations"] == 0, verification


def test_path_repair_reports_an_orphan_entry_and_deletes_nothing(capsys, registry, root) -> None:
    """Write-only is the boundary, not an oversight: the orphan is reported — loudly, with exit 2.

    Removing a file inside the root is what 规划 §9.6 puts behind a plan plus an approval, and the
    other direction of this drift is prevented at its source (`retire` clears the entry it projected).
    What this guard pins is the *report*: a root that keeps an orphan is drifting, so this verb may not
    answer `SUCCESS` while `path verify` exits 2 about the same root — the two verbs have to tell one
    story (draft §165 ruling).
    """

    write_launcher(root.path, CAPABILITY)
    entry = launcher_path(root.path, CAPABILITY)
    before = entry.read_bytes()

    code, document = run(capsys, "--json", "--root", str(root.path), "path", "repair")

    assert code == 2, document
    assert document["status"] == "degraded", document
    assert document["reason_code"] == "PATH_EXPOSURE_VIOLATION", (
        "the same code and the same tier `path verify` uses for this drift"
    )
    assert document["orphans"] == [CAPABILITY], document
    assert document["expected"] == [] and document["repaired"] == []
    assert document["files_deleted"] == 0, "this verb has no delete path"
    assert document["warnings"], "a degraded report has to say why it did not fix what it reported"
    joined = " ".join(document["warnings"])
    assert "never deletes" in joined or "only writes" in joined, joined
    assert "retire" in joined, "the warning has to name the verb that clears an entry it projected"
    assert entry.is_file() and entry.read_bytes() == before, "not one byte was removed or changed"

    code, verification = run(capsys, "--json", "--root", str(root.path), "path", "verify")
    assert code == 2 and verification["violations"] == 1, (
        "the orphan is still drift — the verb that removes it is the one that made it (retire), "
        "not this one; the two commands now agree about that in their exit codes"
    )


def test_the_repair_report_refuses_to_print_a_document_that_disagrees_with_itself() -> None:
    """The self-check: no published schema covers this report face, so its own rule is enforced.

    `path verify`'s document has no schema either — both are CLI reports. What can be checked without
    one is checked: the status word pairs with the reason code, every expected capability is accounted
    for by exactly one outcome list, an orphan is never also expected, and nothing was deleted. A
    dropped capability, or an orphan quietly reported as success, would otherwise print as if the root
    were fine.
    """

    from airoot.caps.pathexposure import PathRepair, _require_self_consistent

    consistent = PathRepair(root="C:\\root", expected=[CAPABILITY], repaired=[CAPABILITY], created=[CAPABILITY])
    assert consistent.to_document()["reason_code"] == "SUCCESS"

    degraded = PathRepair(root="C:\\root", orphans=[CAPABILITY], warnings=["why"])
    assert degraded.to_document()["status"] == "degraded"
    assert degraded.to_document()["reason_code"] == "PATH_EXPOSURE_VIOLATION"

    dropped = PathRepair(root="C:\\root", expected=[CAPABILITY], repaired=[], created=[], already_present=[])
    with pytest.raises(AirootError) as caught:
        dropped.to_document()
    assert caught.value.reason_code == "SELF_VALIDATION_FAILED"
    assert any("account for" in item for item in caught.value.evidence), caught.value.evidence

    # An orphan that is also expected, and a status word that does not pair with the code: both are
    # reachable only by handing the checker a document directly, which is what a future writer would do.
    overlapping = PathRepair(root="C:\\root", expected=[CAPABILITY], created=[CAPABILITY], repaired=[CAPABILITY],
                             orphans=[CAPABILITY])
    with pytest.raises(AirootError) as overlap:
        overlapping.to_document()
    assert any("both expected and orphaned" in item for item in overlap.value.evidence), overlap.value.evidence

    with pytest.raises(AirootError) as unpaired:
        _require_self_consistent(
            {
                "status": "ok",
                "reason_code": "PATH_EXPOSURE_VIOLATION",
                "expected": [],
                "repaired": [],
                "created": [],
                "rewritten": [],
                "already_present": [],
                "orphans": ["x"],
                "files_deleted": 0,
            }
        )
    assert any("pairs with status" in item for item in unpaired.value.evidence), unpaired.value.evidence
