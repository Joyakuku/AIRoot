"""L1: the steward domain — data roots and external references (ADR-0004).

Covers draft §3 (data roots), §5 (reference lifecycle), §7 (registry/Schema) and
the migration path from the v1 registry shape. No real directory outside the test
root is ever registered or written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from airoot import schema_io
from airoot.exits import AirootError
from airoot.registry import DataRoot, ExternalReference, Registry
from airoot.registry.entities import data_root_from_row, external_reference_from_row
from airoot.paths import volume_serial

# The frozen v1 shape, kept verbatim so the migration is tested against history
# rather than against the current ddl.sql.
V1_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE instances (
    instance_id TEXT PRIMARY KEY, kind TEXT NOT NULL, capability_id TEXT NOT NULL, tool_id TEXT,
    version TEXT NOT NULL, platform TEXT NOT NULL, architecture TEXT NOT NULL,
    install_backend_id TEXT NOT NULL, artifact_digest TEXT NOT NULL, store_path TEXT NOT NULL,
    file_manifest_digest TEXT, lifecycle_status TEXT NOT NULL, health TEXT NOT NULL,
    entrypoints_json TEXT NOT NULL DEFAULT '[]', payload_json TEXT NOT NULL, source_digest TEXT,
    created_at TEXT NOT NULL, retired_at TEXT);
CREATE TABLE bindings (
    binding_key TEXT NOT NULL, instance_id TEXT NOT NULL, scope TEXT NOT NULL, zone TEXT NOT NULL,
    exposure TEXT NOT NULL, generation INTEGER NOT NULL, active INTEGER NOT NULL, project_id TEXT,
    updated_at TEXT NOT NULL, PRIMARY KEY (binding_key, generation));
CREATE TABLE external_references (
    external_id TEXT PRIMARY KEY, capability_id TEXT NOT NULL, path TEXT NOT NULL,
    management TEXT NOT NULL, health TEXT NOT NULL, observed_digest TEXT, observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL);
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan_hash TEXT NOT NULL, state TEXT NOT NULL,
    root_instance_id TEXT NOT NULL, machine_id TEXT NOT NULL, instance_id TEXT,
    generation_before INTEGER, generation_after INTEGER, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, journal_seq INTEGER NOT NULL, approval_id TEXT, approval_nonce TEXT,
    failure_code TEXT, payload_json TEXT NOT NULL);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT, state TEXT NOT NULL, detail TEXT NOT NULL,
    actor TEXT, approval_id TEXT, plan_hash TEXT, generation INTEGER, before_state TEXT,
    after_state TEXT, artifact_digest TEXT, reason_code TEXT, outcome TEXT, occurred_at TEXT NOT NULL);
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL, root_instance_id TEXT NOT NULL,
    machine_id TEXT NOT NULL, policy_revision INTEGER NOT NULL, approval_mode TEXT NOT NULL,
    issuer TEXT NOT NULL, approved_by_sid TEXT, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE, signature_json TEXT NOT NULL, consumed_at TEXT, revoked_at TEXT,
    payload_json TEXT NOT NULL);
CREATE TABLE extensions (
    extension_id TEXT PRIMARY KEY, extension_version TEXT NOT NULL, protocol_version INTEGER NOT NULL,
    capability_types TEXT NOT NULL, implementation_id TEXT NOT NULL, manifest_json TEXT NOT NULL,
    loaded_at TEXT NOT NULL, health TEXT NOT NULL);
"""


def make_data_root(root_path: Path, *, identifier: str = "dr-env", role: str = "runtime") -> DataRoot:
    return DataRoot(
        data_root_id=identifier,
        path=str(root_path),
        role=role,
        volume_serial=volume_serial(root_path),
        added_at="2024-01-01T00:00:00Z",
        whitelist_revision="wl-1",
    )


def make_reference(data_root_id: str | None = "dr-env") -> ExternalReference:
    return ExternalReference(
        external_id="external/env-flutter",
        capability_id="dart",
        path="D:\\env\\flutter",
        management="external_reference",
        health="healthy",
        observed_at="2024-01-01T00:00:00Z",
        capability_kind="runtime",
        data_root_id=data_root_id,
        version="3.24.0",
        architecture="x64",
        entrypoints=("bin/flutter.bat", "bin/dart.exe"),
        probe_level=2,
        source_kind="pe_static",
        evidence=({"kind": "pe_static", "detail": "ProductName=Flutter"},),
    )


# --------------------------------------------------------------------------- #
# data roots
# --------------------------------------------------------------------------- #


def test_data_root_round_trip(registry: Registry, tmp_path: Path) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path))

    row = registry.data_root("dr-env")
    assert row is not None
    root = data_root_from_row(row)
    assert root.role == "runtime"
    assert root.path == str(tmp_path)
    assert root.whitelist_revision == "wl-1"
    assert root.active is True
    assert [item.data_root_id for item in (data_root_from_row(r) for r in registry.data_roots())] == ["dr-env"]


def test_data_root_path_is_unique(registry: Registry, tmp_path: Path) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path, identifier="dr-env"))
    with pytest.raises(sqlite3.IntegrityError):
        with registry.write(expected_generation=registry.generation) as connection:
            registry.add_data_root(connection, make_data_root(tmp_path, identifier="dr-other"))


def test_forgetting_a_data_root_removes_only_the_registration(registry: Registry, tmp_path: Path) -> None:
    payload = tmp_path / "flutter"
    payload.mkdir()
    (payload / "marker.txt").write_text("user file", encoding="utf-8")

    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path))
    with registry.write(expected_generation=registry.generation) as connection:
        assert registry.forget_data_root(connection, "dr-env") is True

    assert registry.data_root("dr-env") is None
    assert (payload / "marker.txt").read_text(encoding="utf-8") == "user file"


def test_data_root_identifier_and_role_are_constrained(registry: Registry, tmp_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with registry.write(expected_generation=registry.generation) as connection:
            registry.add_data_root(connection, make_data_root(tmp_path, role="nonsense"))


# --------------------------------------------------------------------------- #
# external references
# --------------------------------------------------------------------------- #


def test_reference_round_trip_keeps_every_fact(registry: Registry) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(connection, make_reference())

    row = registry.external_reference("external/env-flutter")
    assert row is not None
    reference = external_reference_from_row(row)
    assert reference.capability_id == "dart"
    assert reference.capability_kind == "runtime"
    assert reference.version == "3.24.0"
    assert reference.versions == ()
    assert reference.active_version is None
    assert reference.architecture == "x64"
    assert reference.entrypoints == ("bin/flutter.bat", "bin/dart.exe")
    assert reference.probe_level == 2
    assert reference.source_kind == "pe_static"
    assert reference.evidence[0]["kind"] == "pe_static"
    assert reference.owned is False, "the steward domain never owns"


def test_reference_keeps_a_version_set_and_the_observed_active_one(registry: Registry) -> None:
    multi = ExternalReference(
        external_id="external/dr-env/nvm4w",
        capability_id="node",
        path="D:\\env\\nvm4w",
        management="external_reference",
        health="healthy",
        observed_at="2024-01-01T00:00:00Z",
        capability_kind="runtime",
        data_root_id="dr-env",
        version="26.8.1.0",
        versions=("22.14.0.0", "22.15.0.0", "26.8.1.0"),
        active_version="26.8.1.0",
        architecture="x64",
        entrypoints=("node.exe",),
        probe_level=2,
        source_kind="pe_static",
        evidence=({"kind": "junction_target", "detail": "nodejs -> \\\\?\\D:\\env\\nvm4w\\v26.8.1"},),
    )
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(connection, multi)

    round_tripped = external_reference_from_row(registry.external_reference(multi.external_id))
    assert round_tripped.versions == ("22.14.0.0", "22.15.0.0", "26.8.1.0")
    assert round_tripped.active_version == "26.8.1.0"
    assert round_tripped.version == "26.8.1.0"

    schema_io.validate_self(
        "registry-projection",
        {
            "schema_version": 1,
            "root_instance_id": registry.root_instance_id,
            "machine_id": registry.machine_id,
            "generation": 0,
            "policy_revision": 1,
            "instances": [],
            "bindings": [],
            "external_references": [round_tripped.to_schema()],
            "generated_at": "2024-01-01T00:00:00Z",
            "data_roots": [],
        },
    )


def test_reference_schema_document_is_complete(registry: Registry) -> None:
    document = make_reference().to_schema()
    schema_io.validate_self("registry-projection", {
        "schema_version": 1,
        "root_instance_id": registry.root_instance_id,
        "machine_id": registry.machine_id,
        "generation": 0,
        "policy_revision": 1,
        "instances": [],
        "bindings": [],
        "external_references": [document],
        "generated_at": "2024-01-01T00:00:00Z",
        "data_roots": [],
    })
    assert set(document) == {
        "external_id", "capability_id", "capability_kind", "data_root_id", "path", "management",
        "health", "version", "versions", "active_version", "architecture", "entrypoints",
        "observed_digest", "observed_at", "probe_level", "source_kind", "evidence",
    }, "every key is always present so the shape is stable for the Rust port"


def test_unknown_facts_are_null_not_invented(registry: Registry) -> None:
    bare = ExternalReference(
        external_id="external/stray",
        capability_id="unknown",
        path="D:\\tools\\stray",
        management="unmanaged",
        health="healthy",
        observed_at="2024-01-01T00:00:00Z",
    )
    schema_io.validate_self(
        "registry-projection",
        {
            "schema_version": 1,
            "root_instance_id": registry.root_instance_id,
            "machine_id": registry.machine_id,
            "generation": 0,
            "policy_revision": 1,
            "instances": [],
            "bindings": [],
            "external_references": [bare.to_schema()],
            "generated_at": "2024-01-01T00:00:00Z",
            "data_roots": [],
        },
    )
    assert bare.to_schema()["version"] is None
    assert bare.to_schema()["probe_level"] is None
    assert bare.to_schema()["source_kind"] is None


def test_forgetting_a_reference_never_touches_the_file(registry: Registry, tmp_path: Path) -> None:
    target = tmp_path / "flutter"
    target.mkdir()
    (target / "dart.exe").write_bytes(b"MZ-not-a-real-executable")

    reference = make_reference(data_root_id=None)
    reference = ExternalReference(**{**reference.__dict__, "path": str(target)})
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(connection, reference)
    with registry.write(expected_generation=registry.generation) as connection:
        assert registry.forget_external_reference(connection, reference.external_id) is True

    assert registry.external_reference(reference.external_id) is None
    assert (target / "dart.exe").read_bytes() == b"MZ-not-a-real-executable"


def test_reference_listing_by_data_root(registry: Registry, tmp_path: Path) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path))
        registry.upsert_external_reference(connection, make_reference("dr-env"))
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/elsewhere",
                capability_id="node",
                path="D:\\tools\\node",
                management="unmanaged",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
            ),
        )

    assert [row["external_id"] for row in registry.external_references_for_data_root("dr-env")] == [
        "external/env-flutter"
    ]
    assert len(registry.external_references()) == 2


# --------------------------------------------------------------------------- #
# projection
# --------------------------------------------------------------------------- #


def test_projection_carries_data_roots_and_full_references(registry: Registry, tmp_path: Path) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path))
        registry.upsert_external_reference(connection, make_reference())
    projection = registry.update_projection()

    schema_io.validate_self("registry-projection", projection)
    assert [item["data_root_id"] for item in projection["data_roots"]] == ["dr-env"]
    assert projection["external_references"][0]["version"] == "3.24.0"
    on_disk = json.loads((Path(registry.path).parent / "registry.json").read_text(encoding="utf-8"))
    assert on_disk["data_roots"] == projection["data_roots"]


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #


def build_v1_registry(root: Path) -> Path:
    path = root / "state" / "registry.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(V1_DDL)
        for key, value in {
            "schema_version": "1",
            "protocol_version": "1",
            "generation": "0",
            "policy_revision": "1",
            "machine_id": "host-0123456789abcdef",
            "root_instance_id": "root-migration-test",
        }.items():
            connection.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        connection.execute(
            "INSERT INTO migrations (version, applied_at, note) VALUES (1, '2024-01-01T00:00:00Z', 'v1')"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_v1_database_is_migrated_in_place(root, tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    build_v1_registry(legacy_root)

    migrated = Registry.open(legacy_root)
    try:
        assert migrated.migration_versions() == [1, 2, 3, 4, 5, 6]
        assert migrated.data_roots() == []
        # Steward columns are usable immediately after the migration.
        with migrated.write(expected_generation=migrated.generation) as connection:
            migrated.upsert_external_reference(connection, make_reference(data_root_id=None))
        assert migrated.external_reference("external/env-flutter")["version"] == "3.24.0"
    finally:
        migrated.close()


def test_migrated_and_fresh_databases_have_identical_shape(root, tmp_path: Path) -> None:
    """The migration and ddl.sql must not drift; this compares them directly."""

    legacy_root = tmp_path / "legacy"
    build_v1_registry(legacy_root)
    migrated = Registry.open(legacy_root)
    fresh = Registry.initialize(tmp_path / "fresh", machine_id="host-1", root_instance_id="root-1")
    try:
        migrated_shape = {row["name"]: row["type"] for row in migrated._conn.execute("PRAGMA table_info(external_references)")}
        fresh_shape = {row["name"]: row["type"] for row in fresh._conn.execute("PRAGMA table_info(external_references)")}
        assert migrated_shape == fresh_shape
        assert "data_root_id" in migrated_shape

        migrated_tables = {row["name"] for row in migrated._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        fresh_tables = {row["name"] for row in fresh._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert migrated_tables == fresh_tables

        migrated_persist = {row["name"]: row["type"] for row in migrated._conn.execute("PRAGMA table_info(environment_persist)")}
        fresh_persist = {row["name"]: row["type"] for row in fresh._conn.execute("PRAGMA table_info(environment_persist)")}
        assert migrated_persist == fresh_persist
        assert "old_value" in migrated_persist

        migrated_instances = {row["name"]: row["type"] for row in migrated._conn.execute("PRAGMA table_info(instances)")}
        fresh_instances = {row["name"]: row["type"] for row in fresh._conn.execute("PRAGMA table_info(instances)")}
        assert migrated_instances == fresh_instances
        assert "collected_at" in migrated_instances

        # The events table is compared by **order**, not just by name: `ALTER TABLE ADD COLUMN`
        # appends, so a column added to `ddl.sql` anywhere but the end would give migrated and fresh
        # databases the same names in different positions — invisible to a name-keyed dict, and fatal
        # to anything reading a row positionally (draft §65).
        migrated_events = [row["name"] for row in migrated._conn.execute("PRAGMA table_info(events)")]
        fresh_events = [row["name"] for row in fresh._conn.execute("PRAGMA table_info(events)")]
        assert migrated_events == fresh_events
        assert migrated_events[-1] == "approval_mode", migrated_events
    finally:
        migrated.close()
        fresh.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    build_v1_registry(legacy_root)
    for _ in range(3):
        handle = Registry.open(legacy_root)
        try:
            assert handle.migration_versions() == [1, 2, 3, 4, 5, 6]
        finally:
            handle.close()


def test_migrated_database_still_reports_integrity(registry: Registry, tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    build_v1_registry(legacy_root)
    handle = Registry.open(legacy_root)
    try:
        assert handle.integrity_problems() == []
    finally:
        handle.close()


# --------------------------------------------------------------------------- #
# schema drift guard
# --------------------------------------------------------------------------- #


def test_projection_reference_properties_stay_within_the_shared_definition() -> None:
    """`registry-projection` duplicates the reference shape for compatibility reasons.

    That duplication is a drift risk, so it is asserted rather than trusted.
    """

    shared = schema_io.load_schema("common")["$defs"]["externalReference"]["properties"]
    projected = schema_io.load_schema("registry-projection")["properties"]["external_references"]["items"]["properties"]

    extra = set(projected) - set(shared)
    assert not extra, f"projection declares reference fields the shared definition lacks: {sorted(extra)}"

    shared_required = set(schema_io.load_schema("common")["$defs"]["externalReference"]["required"])
    projected_required = set(
        schema_io.load_schema("registry-projection")["properties"]["external_references"]["items"]["required"]
    )
    assert projected_required <= shared_required


def test_data_root_is_shared_not_duplicated() -> None:
    projection = schema_io.load_schema("registry-projection")
    assert projection["properties"]["data_roots"]["items"]["$ref"] == "common.schema.json#/$defs/dataRoot"


def test_readding_the_same_id_updates_instead_of_duplicating(registry: Registry, tmp_path: Path) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path, role="runtime"))
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(connection, make_data_root(tmp_path, role="mixed"))

    assert len(registry.data_roots()) == 1
    assert registry.data_root("dr-env")["role"] == "mixed"
