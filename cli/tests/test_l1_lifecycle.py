"""L1: deletion semantics graded by ownership (draft §14, S-032…S-036).

The invariable under test is the one the whole steward model rests on: **AIROOT deletes only
what AIROOT installed**, and even then only through `retire → gc` with an approval.

Nothing here may touch a data root, and every test that deletes something deletes only a
payload the fake install backend created inside the CLI root's own `store/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fake_issuer
from airoot.canon import tree_digest
from airoot.caps.lifecycle import (
    GC_APPLY,
    apply_gc_plan,
    build_gc_plan,
    gc_candidates,
    is_owned,
    retire,
    require_owned,
    uninstall_target,
)
from airoot.clock import FakeClock
from airoot.exits import AirootError, exit_code_for
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "fake-tool"


def install(registry, clock, root, version: str = "1.0.0") -> str:
    """Install one owned instance through the fake backend and return its instance id."""

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return str(plan["target"]["instance_id"])


def approve_gc(registry, root, plan: dict) -> dict:
    fake_issuer.install_keyring(root.path)
    return fake_issuer.issue(plan, clock=FakeClock(start="2024-01-01T00:00:00Z"))


def store_dir(root, instance_id: str) -> Path:
    return Path(root.path) / "store" / instance_id


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #


def test_a_freshly_installed_instance_is_owned(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    row = registry.instance(instance_id)

    assert is_owned(row) is True
    assert str(row["store_path"]).replace("\\", "/").startswith("store/")


def test_a_reference_is_never_uninstallable(registry, clock, root) -> None:
    """S-032: a reference has no uninstall path at all — AIROOT does not own it."""

    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/dr-env/java', 'java', ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (str(Path(root.path) / "tools" / "java"),),
        )

    with pytest.raises(AirootError) as caught:
        require_owned(registry, "external/dr-env/java")

    assert caught.value.reason_code == "OWNERSHIP_REQUIRED"
    assert exit_code_for(caught.value.reason_code) == 7
    joined = " ".join(caught.value.evidence)
    assert "airoot forget external/dr-env/java" in joined, "the user gets the command that is legal"
    assert str(Path(root.path) / "tools" / "java") in joined, "and the absolute path of the files"


def test_an_unowned_instance_is_refused(registry, clock, root) -> None:
    """A 'foreign' row (declared but not under store/) must not be deletable either.

    Instance identity is immutable, so this declares a *separate* instance whose payload
    lives outside the store rather than mutating the installed one.
    """

    from airoot.registry.entities import Instance

    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id="foreign/tool/1.0.0/win-x64",
                kind="managed_tool",
                capability_id="foreign-tool",
                version="1.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id="external",
                artifact_digest="sha256:" + "f" * 64,
                store_path="tools/foreign-tool",
                lifecycle_status="installed",
                health="healthy",
                created_at="2024-01-01T00:00:00Z",
            ),
        )

    with pytest.raises(AirootError) as caught:
        require_owned(registry, "foreign/tool/1.0.0/win-x64")
    assert caught.value.reason_code == "OWNERSHIP_REQUIRED"


# --------------------------------------------------------------------------- #
# retire: binding gone, payload untouched
# --------------------------------------------------------------------------- #


def test_retire_clears_the_binding_and_keeps_the_payload(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    directory = store_dir(root, instance_id)
    digest_before = tree_digest(directory)
    generation_before = registry.generation

    document = retire(registry, instance_id, clock=clock)

    assert document["payload_removed"] is False
    assert registry.instance(instance_id)["lifecycle_status"] == "retired"
    assert registry.instance(instance_id)["retired_at"] is not None
    assert [row for row in registry.bindings(active_only=True) if row["instance_id"] == instance_id] == []
    assert registry.generation > generation_before, "clearing a binding changes the active state"
    assert directory.is_dir() and tree_digest(directory) == digest_before


def test_retire_is_idempotent(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    first = retire(registry, instance_id, clock=clock)
    second = retire(registry, instance_id, clock=clock)

    assert first.get("idempotent") is not True
    assert second["idempotent"] is True
    assert registry.instance(instance_id)["lifecycle_status"] == "retired"


def test_a_retired_instance_is_no_longer_selected(registry, clock, root) -> None:
    """S-036 (first half): where stops finding it once the binding is cleared."""

    from airoot.caps.where import WhereQuery, where

    instance_id = install(registry, clock, root)
    assert where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                 process_entries=[], machine_entries=[], user_entries=[])["found"] is True

    retire(registry, instance_id, clock=clock)

    document = where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                     process_entries=[], machine_entries=[], user_entries=[])
    assert document["found"] is False
    assert document["reason_code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# gc: admission, plan, approval
# --------------------------------------------------------------------------- #


def test_an_installed_instance_is_not_collectable(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)

    candidates = {item.instance_id: item for item in gc_candidates(registry)}

    assert candidates[instance_id].collectable is False
    assert any("must be retired first" in blocker for blocker in candidates[instance_id].blockers)
    with pytest.raises(AirootError) as caught:
        build_gc_plan(registry, instance_id, clock=clock)
    assert caught.value.reason_code == "REFERENCE_IN_USE"


def test_a_retired_instance_becomes_collectable(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)

    candidates = {item.instance_id: item for item in gc_candidates(registry)}

    assert candidates[instance_id].collectable is True
    plan = build_gc_plan(registry, instance_id, clock=clock, plan_id="plan/gc/test-fixture")
    assert plan["operation"] == GC_APPLY
    assert plan["operations"][0]["kind"] == "delete"
    assert plan["operations"][0]["source_mutation"] == "delete"
    assert plan["operations"][0]["reversible"] is False
    assert plan["metadata"]["store_path"].startswith("store/")


def test_an_unfinished_transaction_blocks_collection(registry, clock, root) -> None:
    """S-034: a payload still referenced by an unfinished transaction is refused."""

    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, instance_id, health="broken")
        connection.execute(
            """
            INSERT INTO transactions (transaction_id, plan_id, plan_hash, state, root_instance_id,
                                      machine_id, instance_id, generation_before, generation_after,
                                      created_at, updated_at, journal_seq, payload_json)
            VALUES ('tx/pending', 'plan/x', 'sha256:' || printf('%.64d', 0), 'PENDING', ?, ?, ?, 0, 0,
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z', 1, '{}')
            """,
            (registry.root_instance_id, registry.machine_id, instance_id),
        )

    candidates = {item.instance_id: item for item in gc_candidates(registry)}
    assert candidates[instance_id].collectable is False
    assert "unfinished_transaction_references_it" in candidates[instance_id].blockers


def test_gc_apply_removes_only_the_store_payload(registry, clock, root) -> None:
    """S-033/S-036: the payload goes, the row stays, and nothing else on disk changes."""

    instance_id = install(registry, clock, root)
    directory = store_dir(root, instance_id)
    root_tree_before = {
        path.relative_to(root.path).as_posix()
        for path in Path(root.path).rglob("*")
        if path.is_file()
    }
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)
    token = approve_gc(registry, root, plan)

    document = apply_gc_plan(registry, plan, token, root=root.path, clock=clock)

    assert document["payload_removed"] is True
    assert document["data_root_files_touched"] == 0
    assert not directory.exists()
    row = registry.instance(instance_id)
    assert row is not None, "the registry row is retained for binding history"
    assert row["collected_at"] is not None
    assert row["collected_approval_id"] == token["approval_id"]

    root_tree_after = {
        path.relative_to(root.path).as_posix()
        for path in Path(root.path).rglob("*")
        if path.is_file()
    }
    removed = root_tree_before - root_tree_after
    assert all(item.startswith(f"store/{instance_id}") for item in removed), removed
    assert any(item.endswith("logs/audit/events.json") for item in root_tree_after)

    states = [str(item["state"]) for item in registry.events()]
    assert "GC_INTENT" in states and "GC_APPLIED" in states


def test_gc_apply_requires_an_approval(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)

    with pytest.raises(AirootError) as caught:
        apply_gc_plan(registry, plan, {"approval_id": "x"}, root=root.path, clock=clock)

    assert caught.value.reason_code in {"INVALID_APPROVAL", "SCHEMA_UNSUPPORTED"}
    assert store_dir(root, instance_id).is_dir(), "nothing is deleted without a valid token"


def test_gc_apply_is_idempotent(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)
    token = approve_gc(registry, root, plan)
    apply_gc_plan(registry, plan, token, root=root.path, clock=clock)

    # A second apply with a consumed token is refused as a replay, not silently repeated.
    with pytest.raises(AirootError) as caught:
        apply_gc_plan(registry, plan, token, root=root.path, clock=clock)
    assert caught.value.reason_code in {"APPROVAL_REPLAYED", "INVALID_APPROVAL"}


def test_gc_apply_refuses_a_payload_that_changed_after_approval(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)
    token = approve_gc(registry, root, plan)
    (store_dir(root, instance_id) / "fake-tool.bin").write_bytes(b"changed after approval\n")

    with pytest.raises(AirootError) as caught:
        apply_gc_plan(registry, plan, token, root=root.path, clock=clock)

    assert caught.value.reason_code == "DIGEST_MISMATCH"
    assert store_dir(root, instance_id).is_dir()


def test_doctor_does_not_report_a_collected_payload_as_missing(registry, clock, root) -> None:
    """A deliberate collection must not look like a defect (draft §19.3-2)."""

    from airoot.caps.doctor import diagnostics_by_code, doctor

    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)
    token = approve_gc(registry, root, plan)
    apply_gc_plan(registry, plan, token, root=root.path, clock=clock)

    document = doctor(root.path, registry=registry, data_roots=False)

    assert "PAYLOAD_MISSING" not in diagnostics_by_code(document)
    assert document["status"] in {"healthy", "degraded"}


# --------------------------------------------------------------------------- #
# uninstall: the composed verb
# --------------------------------------------------------------------------- #


def test_uninstall_retires_then_waits_for_approval(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)

    outcome = uninstall_target(registry, instance_id, clock=clock)

    assert outcome["retire"]["payload_removed"] is False
    assert registry.instance(instance_id)["lifecycle_status"] == "retired"
    assert store_dir(root, instance_id).is_dir(), "uninstall never deletes before approval"
    assert outcome["plan"]["operation"] == GC_APPLY


def test_uninstall_on_a_reference_only_prints_the_path(registry, clock, root) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/dr-env/node', 'node', ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (str(Path(root.path) / "tools" / "node"),),
        )

    with pytest.raises(AirootError) as caught:
        uninstall_target(registry, "external/dr-env/node", clock=clock)

    assert caught.value.reason_code == "OWNERSHIP_REQUIRED"


def test_the_projection_exposes_collected_at(registry, clock, root) -> None:
    instance_id = install(registry, clock, root)
    retire(registry, instance_id, clock=clock)
    plan = build_gc_plan(registry, instance_id, clock=clock)
    token = approve_gc(registry, root, plan)
    apply_gc_plan(registry, plan, token, root=root.path, clock=clock)

    projection = json.loads((Path(root.path) / "state" / "registry.json").read_text(encoding="utf-8"))
    entry = next(item for item in projection["instances"] if item["instance_id"] == instance_id)
    assert entry["collected_at"] is not None
    assert entry["lifecycle_status"] == "retired"
