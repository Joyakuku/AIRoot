"""L1: registry, generation compare-and-swap, projection and declared-state invariants.

Isolated temp roots only; no host PATH, ACL or real AIROOT root is touched
(verification plan §2 "L1" and §6.2 T-010/T-014).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from airoot import schema_io
from airoot.exits import AirootError
from airoot.registry import Registry, binding_key
from airoot.registry.entities import Binding, Instance, managed_tool_payload
from airoot.registry.projection import projection_generation, read_projection

ARTIFACT = "sha256:" + "a" * 64
MANIFEST = "sha256:" + "b" * 64
KEY = binding_key("fake-tool", "machine")


def make_instance(instance_id: str = "fake-tool/fake-tool/1.0.0/win-x64", digest: str = ARTIFACT) -> Instance:
    payload = managed_tool_payload(
        tool_id="fake-tool",
        instance_id=instance_id,
        capability_id="fake-tool",
        version="1.0.0",
        install_backend_id="fake_fixture",
        artifact_digest=digest,
        store_path=f"store/{instance_id}",
        file_manifest_digest=MANIFEST,
        lifecycle_status="installed",
        health="healthy",
        entrypoints=["fake-tool.bin"],
        source={
            "kind": "generated_fixture",
            "locator": "cache/fixtures/fake-tool/1.0.0",
            "provenance": {"source_id": "fixture/fake-tool-v1", "publisher": "airoot-test"},
            "integrity": {"artifact_digest": digest, "file_manifest_digest": MANIFEST},
        },
        created_at="2024-01-01T00:00:00Z",
    )
    return Instance(
        instance_id=instance_id,
        kind="managed_tool",
        capability_id="fake-tool",
        version="1.0.0",
        platform="windows",
        architecture="x64",
        install_backend_id="fake_fixture",
        artifact_digest=digest,
        store_path=f"store/{instance_id}",
        lifecycle_status="installed",
        health="healthy",
        tool_id="fake-tool",
        file_manifest_digest=MANIFEST,
        entrypoints=("fake-tool.bin",),
        payload=payload,
        created_at="2024-01-01T00:00:00Z",
    )


# --------------------------------------------------------------------------- #
# lifecycle of the database itself
# --------------------------------------------------------------------------- #


def test_fresh_registry_reports_seeded_meta(registry: Registry, machine) -> None:
    assert registry.generation == 0
    assert registry.policy_revision == 1
    assert registry.schema_version == 1
    assert registry.machine_id == machine.machine_id
    assert registry.root_instance_id == machine.root_instance_id
    assert registry.integrity_problems() == []


def test_registry_cannot_be_initialised_twice(registry: Registry, root) -> None:
    with pytest.raises(AirootError) as err:
        Registry.initialize(root.path, machine_id="host-x", root_instance_id="root-x")
    assert err.value.reason_code == "INVALID_INPUT"


def test_missing_registry_is_reported_distinctly(root) -> None:
    with pytest.raises(AirootError) as err:
        Registry.open(root.path)
    assert err.value.reason_code == "REGISTRY_MISSING"
    assert err.value.exit_code == 6


def test_corrupt_registry_is_reported_as_integrity_failure(root, registry) -> None:
    registry.close()
    Path(registry.path).write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(AirootError) as err:
        Registry.open(root.path)
    assert err.value.reason_code == "REGISTRY_INTEGRITY_FAILED"
    assert err.value.exit_code == 3


def test_open_reads_back_the_same_declared_state(root, registry) -> None:
    machine_id = registry.machine_id
    registry.close()
    reopened = Registry.open(root.path)
    try:
        assert reopened.generation == 0
        assert reopened.machine_id == machine_id
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# generation compare-and-swap
# --------------------------------------------------------------------------- #


def test_generation_bump_is_visible_and_monotonic(registry: Registry) -> None:
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, make_instance())
    assert registry.generation == 1
    with registry.write(expected_generation=1, bump=True):
        pass
    assert registry.generation == 2


def test_stale_generation_write_is_rejected_and_state_is_untouched(registry: Registry) -> None:
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, make_instance())

    with pytest.raises(AirootError) as err:
        with registry.write(expected_generation=0, bump=True) as connection:
            registry.add_instance(connection, make_instance("fake-tool/fake-tool/9.9.9/win-x64"))
    assert err.value.reason_code == "STALE_GENERATION"
    assert err.value.exit_code == 2
    assert registry.generation == 1
    assert registry.instance("fake-tool/fake-tool/9.9.9/win-x64") is None


def test_stale_generation_does_not_advance_the_registry(registry: Registry) -> None:
    for expected in (0, 0, 0):
        if expected == 0 and registry.generation == 1:
            break
        with registry.write(expected_generation=expected, bump=True):
            pass
    with pytest.raises(AirootError):
        with registry.write(expected_generation=99, bump=True):
            pass
    assert registry.generation == 1


# --------------------------------------------------------------------------- #
# declared-state invariants
# --------------------------------------------------------------------------- #


def test_only_one_active_binding_per_key_is_accepted(registry: Registry) -> None:
    first = make_instance("fake-tool/fake-tool/1.0.0/win-x64")
    second = make_instance("fake-tool/fake-tool/1.1.0/win-x64", digest="sha256:" + "c" * 64)

    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, first)
        registry.add_instance(connection, second)
        registry.bind_active(
            connection, Binding(KEY, first.instance_id, "machine", "R", "stable_launcher", 1, True)
        )

    # A second active row for the same binding key is refused by the database itself.
    with pytest.raises(sqlite3.IntegrityError):
        with registry.write(expected_generation=1, bump=True) as connection:
            connection.execute(
                """
                INSERT INTO bindings (binding_key, instance_id, scope, zone, exposure, generation, active,
                                      project_id, updated_at)
                VALUES (?, ?, 'machine', 'R', 'stable_launcher', 99, 1, NULL, '2024-01-01T00:00:00Z')
                """,
                (KEY, second.instance_id),
            )
    assert registry.active_binding(KEY)["instance_id"] == first.instance_id


def test_rebinding_switches_the_single_active_row(registry: Registry) -> None:
    first = make_instance("fake-tool/fake-tool/1.0.0/win-x64")
    second = make_instance("fake-tool/fake-tool/1.1.0/win-x64", digest="sha256:" + "c" * 64)

    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, first)
        registry.add_instance(connection, second)
        registry.bind_active(connection, Binding(KEY, first.instance_id, "machine", "R", "stable_launcher", 1, True))

    with registry.write(expected_generation=1, bump=True) as connection:
        registry.bind_active(connection, Binding(KEY, second.instance_id, "machine", "R", "stable_launcher", 2, True))

    assert registry.generation == 2
    active = registry.bindings(active_only=True)
    assert [row["instance_id"] for row in active] == [second.instance_id]
    assert len(registry.bindings()) == 2, "the previous generation stays as history"


def test_instance_identity_and_digest_are_immutable(registry: Registry) -> None:
    instance = make_instance()
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, instance)
    with pytest.raises(sqlite3.IntegrityError):
        with registry.write(expected_generation=1, bump=True) as connection:
            connection.execute(
                "UPDATE instances SET artifact_digest = ? WHERE instance_id = ?",
                ("sha256:" + "d" * 64, instance.instance_id),
            )
    assert registry.instance(instance.instance_id)["artifact_digest"] == ARTIFACT


def test_adding_the_same_instance_twice_is_idempotent(registry: Registry) -> None:
    instance = make_instance()
    with registry.write(expected_generation=0, bump=True) as connection:
        assert registry.add_instance(connection, instance) is True
    with registry.write(expected_generation=1, bump=True) as connection:
        assert registry.add_instance(connection, instance) is False


def test_same_instance_id_with_a_different_digest_is_refused(registry: Registry) -> None:
    instance = make_instance()
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, instance)
    conflicting = make_instance(digest="sha256:" + "e" * 64)
    with pytest.raises(AirootError) as err:
        with registry.write(expected_generation=1, bump=True) as connection:
            registry.add_instance(connection, conflicting)
    assert err.value.reason_code == "INSTANCE_CONFLICT"
    assert err.value.exit_code == 7


def test_orphan_binding_is_detected(registry: Registry, root) -> None:
    raw = sqlite3.connect(str(Path(root.path) / "state" / "registry.db"))
    try:
        raw.execute(
            """
            INSERT INTO bindings (binding_key, instance_id, scope, zone, exposure, generation, active,
                                  project_id, updated_at)
            VALUES ('machine/ghost/windows/x64', 'ghost/1/win-x64', 'machine', 'R', 'stable_launcher', 1, 1,
                    NULL, '2024-01-01T00:00:00Z')
            """
        )
        raw.commit()
    finally:
        raw.close()
    problems = registry.integrity_problems()
    assert any("missing instance" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# projection
# --------------------------------------------------------------------------- #


def test_projection_matches_its_schema_and_generation(registry: Registry, root) -> None:
    instance = make_instance()
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, instance)
        registry.bind_active(connection, Binding(KEY, instance.instance_id, "machine", "R", "stable_launcher", 1, True))
    projection = registry.update_projection()

    schema_io.validate_self("registry-projection", projection)
    assert projection["generation"] == 1
    assert projection["root_instance_id"] == registry.root_instance_id
    assert [item["instance_id"] for item in projection["instances"]] == [instance.instance_id]
    assert projection["bindings"][0]["active"] is True

    on_disk = read_projection(root.path)
    assert on_disk == projection
    assert projection_generation(root.path) == 1


def test_projection_is_detected_as_stale_after_a_generation_bump(registry: Registry, root) -> None:
    registry.update_projection()
    assert projection_generation(root.path) == 0
    with registry.write(expected_generation=0, bump=True):
        pass
    assert projection_generation(root.path) == 0
    assert registry.generation == 1, "doctor compares these two to report REGISTRY_PROJECTION_STALE"


def test_projection_file_is_valid_json_text(registry: Registry, root) -> None:
    registry.update_projection()
    path = Path(root.path) / "state" / "registry.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1


def test_the_projection_writer_refuses_a_document_its_own_schema_rejects(
    clock, tests_tmp: Path
) -> None:
    """§169: `state/registry.json` is this build's own document, so it is checked before it is written.

    `root-marker` was already validated on the way out; the projection was not, and that is why an
    identity the published schemas reject could be written down and only surface later, as
    `SELF_VALIDATION_FAILED` from an unrelated verb. An identity is only refused here because the
    registry itself accepts it — that is the point: the guard has to be at the write, not only at
    `root init`.

    Non-vacuity: the very same rows write a **valid** projection once the identity is a legal one.
    """

    from airoot.exits import AirootError

    # `Registry.initialize` does not police the identity (the CLI verb does); this is the state a
    # caller that skipped the verb would be in.
    handle = Registry.initialize(
        tests_tmp / "projection-identity-guard",
        machine_id="host-ok-1234",
        root_instance_id="root-ok-1234",
        clock=clock,
    )
    try:
        assert handle.update_projection()["machine_id"] == "host-ok-1234"
        handle._conn.execute("UPDATE meta SET value = ? WHERE key = ?", ("short", "machine_id"))
        handle._conn.commit()
        with pytest.raises(AirootError) as caught:
            handle.update_projection()
    finally:
        handle.close()

    assert caught.value.reason_code == "SELF_VALIDATION_FAILED", caught.value.reason_code
    assert any("machine_id" in item for item in caught.value.evidence), caught.value.evidence


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #


def test_events_are_appended_inside_the_write_transaction(registry: Registry) -> None:
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, make_instance())
        registry.append_event(
            connection,
            state="REGISTERED",
            detail="instance registered inactive",
            transaction_id="tx/fake-tool/abc",
            generation=1,
            outcome="ok",
        )
    events = registry.events(transaction_id="tx/fake-tool/abc")
    assert [row["state"] for row in events] == ["REGISTERED"]
    assert events[0]["generation"] == 1


def test_event_rollback_keeps_history_consistent(registry: Registry) -> None:
    with pytest.raises(AirootError):
        with registry.write(expected_generation=5, bump=True) as connection:
            registry.append_event(connection, state="PROPOSED", detail="never committed")
    assert registry.event_count() == 0


def test_declared_state_digest_changes_with_state(registry: Registry) -> None:
    before = registry.declared_state_digest()
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, make_instance())
    assert registry.declared_state_digest() != before
