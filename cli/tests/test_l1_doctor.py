"""L1: ``doctor`` invariants D1-D10, stable codes and honest remediation.

Covers verification plan §8 (each invariant has a healthy fixture, a minimal bad
case, evidence, and a re-check after repair) and the security-honesty test P-013.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

import fake_issuer
from airoot import schema_io
from airoot.caps.doctor import DIAGNOSTIC_CODES, INVARIANTS, diagnostics_by_code, doctor, status_exit_code
from airoot.exits import AirootError
from airoot.registry import Registry
from airoot.root import open_root
from airoot.tx import create_plan, repair
from airoot.tx.simulate import SimulationRunner


def install(registry, clock, root, version: str = "1.0.0") -> dict:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return plan


def codes(document: dict) -> set[str]:
    return {str(item["code"]) for item in document["diagnostics"]}


# --------------------------------------------------------------------------- #
# the catalogue itself
# --------------------------------------------------------------------------- #


def test_invariant_catalogue_covers_d1_to_d10() -> None:
    assert set(INVARIANTS) == {f"D{index}" for index in range(1, 11)}
    assert all(INVARIANTS[key] for key in INVARIANTS)


def test_health_and_broken_codes_map_to_documented_exit_codes() -> None:
    from airoot.exits import EXIT_BROKEN, EXIT_DEGRADED, EXIT_RECOVERY, EXIT_SUCCESS, REASON_EXIT

    assert status_exit_code("healthy") == EXIT_SUCCESS
    assert status_exit_code("degraded") == EXIT_DEGRADED
    assert status_exit_code("broken") == EXIT_BROKEN
    assert status_exit_code("recovery_required") == EXIT_RECOVERY
    for code in DIAGNOSTIC_CODES - {"POLICY_ONLY_MODE"}:
        assert code in REASON_EXIT, f"diagnostic code {code} has no exit-code mapping"


# --------------------------------------------------------------------------- #
# healthy and honesty
# --------------------------------------------------------------------------- #


def test_healthy_root_reports_only_the_policy_only_notice(registry, clock, root) -> None:
    install(registry, clock, root)
    document = doctor(root.path, clock=clock, registry=registry)

    schema_io.validate_self("doctor-response", document)
    assert document["status"] == "healthy"
    assert status_exit_code(document["status"]) == 0
    assert codes(document) == {"POLICY_ONLY_MODE"}


def test_policy_only_mode_is_declared_not_hidden(registry, clock, root) -> None:
    """P-013: without ACLs and a broker, the answer must not claim protection."""

    install(registry, clock, root)
    document = doctor(root.path, clock=clock, registry=registry)
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"
    notice = diagnostics_by_code(document)["POLICY_ONLY_MODE"]
    assert notice["severity"] == "info"
    assert "bypass" in notice["impact"]


def test_registry_generation_is_reported(registry, clock, root) -> None:
    install(registry, clock, root)
    document = doctor(root.path, clock=clock, registry=registry)
    assert document["root_instance_id"] == registry.root_instance_id
    assert document["registry_generation"] == registry.generation == 1


# --------------------------------------------------------------------------- #
# D1/D2
# --------------------------------------------------------------------------- #


def test_missing_root_marker_requires_recovery(root) -> None:
    marker = Path(root.path) / "state" / "root.json"
    marker.unlink()
    document = doctor(root.path)
    assert document["status"] == "recovery_required"
    assert status_exit_code(document["status"]) == 6
    assert "ROOT_MARKER_MISSING" in codes(document)
    assert diagnostics_by_code(document)["ROOT_MARKER_MISSING"]["remediation"] == "recover"


def test_corrupt_registry_is_broken_and_never_rebuilt_implicitly(root, registry) -> None:
    registry.close()
    Path(registry.path).write_bytes(b"not a database")
    document = doctor(root.path)
    assert document["status"] == "broken"
    assert status_exit_code(document["status"]) == 3
    assert "REGISTRY_INTEGRITY_FAILED" in codes(document)
    assert diagnostics_by_code(document)["REGISTRY_INTEGRITY_FAILED"]["remediation"] == "rebuild"
    assert Path(registry.path).read_bytes() == b"not a database", "doctor must not modify anything"


def test_S012_a_changed_root_volume_identity_requires_recovery_and_is_not_adopted(
    root, registry
) -> None:
    """S-012: a changed root volume identity needs recovery, and the directory is not adopted.

    Draft §49 created the ledger's `unchecked-invariant` value *because of this scenario*, and §57
    verified why it still applied: the guard exists on **two** paths and neither had ever been
    exercised.

    * `root.py::open_root` raises `VOLUME_IDENTITY_MISMATCH` when the marker's serial is not the
      volume's. This one is load-bearing rather than decorative — `cli.py` calls it for **every**
      command, so a swapped volume makes every invocation refuse instead of adopting whatever now
      sits at that path.
    * `doctor` reports it `critical` + `recover`, which is what turns the status into
      `recovery_required`.

    No test in the tree called `open_root` at all, and the nearest test on the doctor side
    (`test_missing_root_marker_requires_recovery`) covers a **missing** marker, not drift. The only
    test that ever touched a bad serial checks the schema's *format* rule, which is a different
    question.

    The dangerous direction is adoption — answering "yes, this is my root" to a stranger's directory
    — so the assertions are about refusal, and the negative control proves the guard keys on identity
    rather than refusing roots in general.
    """

    marker_path = Path(root.path) / "state" / "root.json"
    original = json.loads(marker_path.read_text(encoding="utf-8"))
    real = str(original["volume_serial"])

    # A *valid* serial that is necessarily not this one: the schema accepts `[A-Fa-f0-9-]{1,128}`,
    # so flipping a character keeps the document schema-valid and faults only the identity check.
    drifted_serial = ("0" if real[0] != "0" else "1") + real[1:]
    assert drifted_serial != real, "the drift must actually change the serial"
    marker_before = marker_path.read_text(encoding="utf-8")
    registry_before = Path(registry.path).read_bytes()
    marker_path.write_text(json.dumps(dict(original, volume_serial=drifted_serial)), encoding="utf-8")

    # Half 1 — the diagnosis, with a severity and a remediation that say what to do about it.
    document = doctor(root.path, registry=registry)
    assert document["status"] == "recovery_required", document["status"]
    assert status_exit_code(document["status"]) == 6
    assert "VOLUME_IDENTITY_MISMATCH" in codes(document)
    entry = diagnostics_by_code(document)["VOLUME_IDENTITY_MISMATCH"]
    assert entry["severity"] == "critical", entry
    assert entry["remediation"] == "recover", entry
    # Evidence has to say *from what to what*; "identity changed" alone cannot be acted on.
    assert any(drifted_serial in item for item in entry["evidence"]), entry["evidence"]
    assert any(real in item for item in entry["evidence"]), entry["evidence"]

    # Half 2 — not adopted: nothing was rewritten on the way to that verdict.
    assert Path(registry.path).read_bytes() == registry_before, "doctor must not modify anything"
    assert marker_path.read_text(encoding="utf-8") != marker_before, "the drift was really written"
    assert json.loads(marker_path.read_text(encoding="utf-8"))["volume_serial"] == drifted_serial

    # Half 3 — the guard on the real command path refuses too, with the recovery exit code.
    with pytest.raises(AirootError) as caught:
        open_root(root.path)
    assert caught.value.reason_code == "VOLUME_IDENTITY_MISMATCH"
    assert caught.value.exit_code == 6
    assert any(real in item for item in caught.value.evidence), caught.value.evidence

    # Negative control: restoring the real serial makes the same root acceptable again. Without this,
    # "refuse every root" would satisfy everything above — and that would refuse to start anywhere.
    marker_path.write_text(marker_before, encoding="utf-8")
    reopened = open_root(root.path)
    assert reopened.path == Path(root.path)
    assert reopened.marker["volume_serial"] == real


# --------------------------------------------------------------------------- #
# D3/D4
# --------------------------------------------------------------------------- #


def test_missing_payload_is_reported_as_broken(registry, clock, root) -> None:
    plan = install(registry, clock, root)
    store = Path(root.path) / "store" / plan["target"]["instance_id"]
    for child in sorted(store.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    store.rmdir()

    document = doctor(root.path, clock=clock, registry=registry)
    assert document["status"] == "broken"
    assert "PAYLOAD_MISSING" in codes(document)
    assert "store_path=" in " ".join(diagnostics_by_code(document)["PAYLOAD_MISSING"]["evidence"])


def test_payload_tampering_is_only_found_with_verify(registry, clock, root) -> None:
    plan = install(registry, clock, root)
    payload = Path(root.path) / "store" / plan["target"]["instance_id"] / "fake-tool.bin"
    payload.write_bytes(b"tampered\n")

    shallow = doctor(root.path, clock=clock, registry=registry)
    assert "MANIFEST_DIGEST_MISMATCH" not in codes(shallow), "a cheap run does not re-hash the store"

    deep = doctor(root.path, clock=clock, registry=registry, verify=True)
    assert document_has(deep, "MANIFEST_DIGEST_MISMATCH")
    assert deep["status"] == "broken"


def document_has(document: dict, code: str) -> bool:
    return code in codes(document)


def test_orphan_store_object_is_reported_and_preserved(registry, clock, root) -> None:
    orphan = Path(root.path) / "store" / "stray" / "1.0.0" / "win-x64"
    orphan.mkdir(parents=True)
    (orphan / "artifact.json").write_text("{}", encoding="utf-8")

    document = doctor(root.path, clock=clock, registry=registry)
    assert "ORPHANED_STORE_INSTANCE" in codes(document)
    assert document["status"] == "degraded"
    assert orphan.is_dir(), "doctor reports unfamiliar objects, it never deletes them"
    assert diagnostics_by_code(document)["ORPHANED_STORE_INSTANCE"]["remediation"] == "inspect"


# --------------------------------------------------------------------------- #
# D5/D6/D7/D10
# --------------------------------------------------------------------------- #


def test_broken_binding_target_is_reported(registry, clock, root) -> None:
    install(registry, clock, root)
    # The registry itself refuses this (foreign key); simulate an externally damaged
    # database by writing with a connection that has foreign keys disabled.
    import sqlite3

    raw = sqlite3.connect(str(Path(root.path) / "state" / "registry.db"))
    try:
        raw.execute("UPDATE bindings SET instance_id = 'ghost/1/win-x64' WHERE active = 1")
        raw.commit()
    finally:
        raw.close()

    document = doctor(root.path, clock=clock, registry=registry)
    assert "BINDING_TARGET_MISSING" in codes(document)
    assert document["status"] == "broken"


def test_pending_transaction_is_reported_and_repairable(registry, clock, root) -> None:
    from conftest import FaultInjector

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="2.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    tx = SimulationRunner(registry, clock=clock, injector=FaultInjector(stop_after="ACTIVE_BOUND")).commit(plan, token)

    pending = doctor(root.path, clock=clock, registry=registry)
    assert "PENDING_TRANSACTION" in codes(pending)
    assert pending["status"] == "degraded"
    assert status_exit_code(pending["status"]) == 2

    repair(registry, tx["transaction_id"], clock=clock, keyring={fake_issuer.KEY_ID: fake_issuer.TEST_SECRET})

    after = doctor(root.path, clock=clock, registry=registry)
    assert codes(after) == {"POLICY_ONLY_MODE"}, "repair leaves a clean bill of health"
    assert after["status"] == "healthy"


def test_stale_projection_is_reported_as_degraded(registry, clock, root) -> None:
    install(registry, clock, root)
    with registry.write(expected_generation=registry.generation, bump=True):
        pass

    document = doctor(root.path, clock=clock, registry=registry)
    assert "REGISTRY_PROJECTION_STALE" in codes(document)
    assert document["status"] == "degraded"
    diagnostic = diagnostics_by_code(document)["REGISTRY_PROJECTION_STALE"]
    assert any("generation" in item for item in diagnostic["evidence"])


def test_doctor_says_nothing_about_a_search_index_that_does_not_exist(registry, clock, root) -> None:
    """Draft §33.2-2: never having built an index is not a finding — it would be pure noise."""

    install(registry, clock, root)
    document = doctor(root.path, clock=clock, registry=registry)

    assert "SEARCH_INDEX_DEGRADED" not in codes(document)
    assert "SEARCH_RESULT_STALE" not in codes(document)
    assert not (Path(root.path) / "cache" / "search").exists()


def test_a_stale_search_index_is_reported_as_rebuildable(registry, clock, root, tests_tmp) -> None:
    from airoot.caps import searchindex
    from airoot.caps.search import load_search_policy

    install(registry, clock, root)
    tree = tests_tmp / "doctor-search-tree"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "python.exe").write_bytes(b"MZ")
    policy = load_search_policy()
    searchindex.build_index(Path(root.path), roots=[str(tree)], policy=policy, clock=clock)
    clock.advance(timedelta(hours=48))

    document = doctor(root.path, clock=clock, registry=registry)

    assert "SEARCH_RESULT_STALE" in codes(document)
    assert document["status"] == "degraded"
    diagnostic = diagnostics_by_code(document)["SEARCH_RESULT_STALE"]
    assert diagnostic["remediation"] == "rebuild"
    assert any("airoot search refresh" in item for item in diagnostic["evidence"])


def test_a_fresh_search_index_is_silent(registry, clock, root, tests_tmp) -> None:
    from airoot.caps import searchindex
    from airoot.caps.search import load_search_policy

    install(registry, clock, root)
    tree = tests_tmp / "doctor-fresh-search-tree"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "python.exe").write_bytes(b"MZ")
    searchindex.build_index(Path(root.path), roots=[str(tree)], policy=load_search_policy(), clock=clock)

    document = doctor(root.path, clock=clock, registry=registry)

    assert "SEARCH_RESULT_STALE" not in codes(document)
    assert "SEARCH_INDEX_DEGRADED" not in codes(document)


def test_a_corrupt_search_index_is_reported_without_crashing(registry, clock, root, tests_tmp) -> None:
    from airoot.caps import searchindex
    from airoot.caps.search import load_search_policy

    install(registry, clock, root)
    tree = tests_tmp / "doctor-corrupt-search-tree"
    tree.mkdir(parents=True, exist_ok=True)
    policy = load_search_policy()
    searchindex.build_index(Path(root.path), roots=[str(tree)], policy=policy, clock=clock)
    searchindex.index_path(Path(root.path), policy).write_bytes(b"not a database" * 16)

    document = doctor(root.path, clock=clock, registry=registry)

    assert "SEARCH_INDEX_DEGRADED" in codes(document)
    diagnostic = diagnostics_by_code(document)["SEARCH_INDEX_DEGRADED"]
    assert diagnostic["remediation"] == "rebuild"
    assert any("live crawl" in item for item in diagnostic["evidence"])


def test_audit_projection_drift_is_reported(registry, clock, root) -> None:
    install(registry, clock, root)
    audit = Path(root.path) / "logs" / "audit" / "events.json"
    assert audit.is_file(), "the projection is written when a transaction ends"
    audit.unlink()

    document = doctor(root.path, clock=clock, registry=registry)
    assert "AUDIT_PROJECTION_DRIFT" in codes(document)
    assert document["status"] == "degraded"
    assert diagnostics_by_code(document)["AUDIT_PROJECTION_DRIFT"]["remediation"] == "rebuild"


def test_audit_projection_is_rebuildable_from_events(registry, clock, root) -> None:
    install(registry, clock, root)
    audit = Path(root.path) / "logs" / "audit" / "events.json"
    audit.unlink()
    registry.update_projection()
    document = doctor(root.path, clock=clock, registry=registry)
    assert "AUDIT_PROJECTION_DRIFT" not in codes(document)


def test_doctor_does_not_require_write_access_to_the_root(registry, clock, root) -> None:
    """A diagnosis is read-only: it may not create files while inspecting."""

    install(registry, clock, root)
    before = sorted(path.relative_to(root.path).as_posix() for path in Path(root.path).rglob("*"))
    doctor(root.path, clock=clock, registry=registry, verify=True)
    after = sorted(path.relative_to(root.path).as_posix() for path in Path(root.path).rglob("*"))
    assert before == after


def test_registry_handle_is_reusable_across_doctor_runs(registry, clock, root) -> None:
    install(registry, clock, root)
    first = doctor(root.path, clock=clock, registry=registry)
    second = doctor(root.path, clock=clock, registry=registry)
    assert first["status"] == second["status"] == "healthy"
