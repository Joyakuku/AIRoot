"""The SQLite registry: declared state authority, single write entry, generation CAS.

Design rules enforced here (v0.3 §9.11, §13.1, §13.2, §14.2):

* every write goes through :meth:`Registry.write`, which takes ``BEGIN IMMEDIATE``
  and a compare-and-swap on the registry generation — a stale writer is rejected
  with ``STALE_GENERATION`` and the old state is left untouched;
* the generation only advances when a binding/active state changes;
* events are appended inside the same transaction that changes state;
* the JSON projection is derived and rewritten by the core, never edited by hand.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import REGISTRY_DB
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError

DDL_PATH = Path(__file__).resolve().parent / "ddl.sql"

META_SCHEMA_VERSION = "schema_version"
META_PROTOCOL_VERSION = "protocol_version"
META_GENERATION = "generation"
META_POLICY_REVISION = "policy_revision"
META_MACHINE_ID = "machine_id"
META_ROOT_INSTANCE_ID = "root_instance_id"

MIGRATION_VERSION = 1

# v2 adds the steward domain: data roots plus the facts a reference must carry
# (ADR-0004). v3 adds the version set and the observed active version, because one
# object may host several versions (nvm keeps three side by side). Fresh databases get
# both from ``ddl.sql``; existing ones are altered in place. The two paths must stay
# identical — a test compares ``PRAGMA table_info`` between a migrated v1 root and a
# fresh root.
TARGET_MIGRATION = 5

DATA_ROOTS_SQL = """
CREATE TABLE IF NOT EXISTS data_roots (
    data_root_id       TEXT PRIMARY KEY,
    path               TEXT NOT NULL,
    role               TEXT NOT NULL CHECK (role IN ('runtime', 'tool', 'mixed')),
    volume_serial      TEXT NOT NULL,
    acl_baseline_json  TEXT,
    whitelist_revision TEXT,
    added_at           TEXT NOT NULL,
    active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_data_roots_path ON data_roots (path);
"""

# Created only after the v2 columns exist (ALTER TABLE comes first).
EXTERNAL_REFERENCE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_external_references_capability ON external_references (capability_id);
CREATE INDEX IF NOT EXISTS ix_external_references_data_root ON external_references (data_root_id);
"""

EXTERNAL_REFERENCE_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("capability_kind", "TEXT CHECK (capability_kind IN ('runtime', 'tool'))"),
    ("data_root_id", "TEXT"),
    ("version", "TEXT"),
    ("architecture", "TEXT"),
    ("entrypoints_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("probe_level", "INTEGER"),
    ("source_kind", "TEXT"),
    ("evidence_json", "TEXT NOT NULL DEFAULT '[]'"),
)

EXTERNAL_REFERENCE_V3_COLUMNS: tuple[tuple[str, str], ...] = (
    ("versions_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("active_version", "TEXT"),
)


def _add_columns(connection: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns:
        if name not in present:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_to_2(connection: sqlite3.Connection, clock: Clock) -> None:
    connection.executescript(DATA_ROOTS_SQL)
    _add_columns(connection, "external_references", EXTERNAL_REFERENCE_V2_COLUMNS)
    connection.executescript(EXTERNAL_REFERENCE_INDEX_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
        (2, clock.timestamp(), "ADR-0004: data roots and steward reference fields"),
    )


def _migrate_to_3(connection: sqlite3.Connection, clock: Clock) -> None:
    _add_columns(connection, "external_references", EXTERNAL_REFERENCE_V3_COLUMNS)
    connection.execute(
        "INSERT OR REPLACE INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
        (3, clock.timestamp(), "one object may host several versions: version set + observed active version"),
    )


ENVIRONMENT_PERSIST_SQL = """
CREATE TABLE IF NOT EXISTS environment_persist (
    capability_id TEXT NOT NULL,
    scope         TEXT NOT NULL CHECK (scope IN ('user', 'machine')),
    variable      TEXT NOT NULL,
    external_id   TEXT,
    value         TEXT NOT NULL,
    value_kind    TEXT NOT NULL CHECK (value_kind IN ('REG_SZ', 'REG_EXPAND_SZ')),
    old_value     TEXT,
    old_kind      TEXT,
    plan_id       TEXT,
    plan_hash     TEXT,
    approval_id   TEXT,
    written_at    TEXT NOT NULL,
    forgotten_at  TEXT,
    PRIMARY KEY (capability_id, scope, variable)
);
CREATE INDEX IF NOT EXISTS ix_environment_persist_active ON environment_persist (forgotten_at);
"""


def _migrate_to_4(connection: sqlite3.Connection, clock: Clock) -> None:
    connection.executescript(ENVIRONMENT_PERSIST_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
        (4, clock.timestamp(), "persisted environment variables with an exact old-value backup"),
    )


def _migrate_to_5(connection: sqlite3.Connection, clock: Clock) -> None:
    _add_columns(connection, "instances", (("collected_at", "TEXT"), ("collected_approval_id", "TEXT")))
    connection.execute(
        "INSERT OR REPLACE INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
        (5, clock.timestamp(), "payload collection is recorded, never by deleting the row"),
    )


EVENTS_V6_COLUMNS: tuple[tuple[str, str], ...] = (("approval_mode", "TEXT"),)


def _migrate_to_6(connection: sqlite3.Connection, clock: Clock) -> None:
    _add_columns(connection, "events", EVENTS_V6_COLUMNS)
    connection.execute(
        "INSERT OR REPLACE INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
        (
            6,
            clock.timestamp(),
            "the audit trail can tell a policy approval from a human one (draft §65)",
        ),
    )


MIGRATIONS = {2: _migrate_to_2, 3: _migrate_to_3, 4: _migrate_to_4, 5: _migrate_to_5, 6: _migrate_to_6}


class Registry:
    """A registry handle bound to one root's ``state/registry.db``."""

    def __init__(self, connection: sqlite3.Connection, path: Path, clock: Clock) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self.path = path
        self.clock = clock

    # ------------------------------------------------------------------ open #

    @staticmethod
    def db_path(root: Path) -> Path:
        return Path(root) / REGISTRY_DB

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            # §139: a connection this function opened does not outlive a failed connect.
            # This is where `file is not a database` lands, so the leaked handle used to be
            # exactly the corrupted-database path — and the lock it left behind is what made a
            # test root undeletable, silently, until the cycle collector got to it.
            connection.close()
            raise
        return connection

    @classmethod
    def initialize(
        cls,
        root: Path,
        *,
        machine_id: str,
        root_instance_id: str,
        clock: Clock = SYSTEM_CLOCK,
        policy_revision: int = 1,
    ) -> "Registry":
        """Create ``state/registry.db`` for a freshly initialised root."""

        path = cls.db_path(root)
        if path.exists():
            raise AirootError("INVALID_INPUT", f"registry already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = cls._connect(path)
        registry = cls(connection, path, clock)
        connection.executescript(DDL_PATH.read_text(encoding="utf-8"))
        registry._seed_meta(
            {
                META_SCHEMA_VERSION: "1",
                META_PROTOCOL_VERSION: "1",
                META_GENERATION: "0",
                META_POLICY_REVISION: str(policy_revision),
                META_MACHINE_ID: machine_id,
                META_ROOT_INSTANCE_ID: root_instance_id,
            }
        )
        # A database created from ddl.sql is already at TARGET_MIGRATION, so every
        # version is recorded here rather than re-running the migration path.
        stamp = clock.timestamp()
        for version, note in (
            (MIGRATION_VERSION, "P1 initial registry schema"),
            (2, "ADR-0004: data roots and steward reference fields"),
            (3, "one object may host several versions: version set + observed active version"),
            (4, "persisted environment variables with an exact old-value backup"),
            (5, "payload collection is recorded, never by deleting the row"),
        ):
            connection.execute(
                "INSERT INTO migrations (version, applied_at, note) VALUES (?, ?, ?)",
                (version, stamp, note),
            )
        return registry

    @classmethod
    def open(cls, root: Path, *, clock: Clock = SYSTEM_CLOCK, create: bool = False,
             machine_id: str | None = None, root_instance_id: str | None = None) -> "Registry":
        path = cls.db_path(root)
        if not path.is_file():
            if not create:
                raise AirootError(
                    "REGISTRY_MISSING",
                    f"registry database missing: {path}",
                    evidence=["the root marker exists but the registry does not"],
                )
            if not machine_id or not root_instance_id:
                raise AirootError("INVALID_INPUT", "creating a registry requires machine_id and root_instance_id")
            return cls.initialize(root, machine_id=machine_id, root_instance_id=root_instance_id, clock=clock)
        connection: sqlite3.Connection | None = None
        try:
            connection = cls._connect(path)
            registry = cls(connection, path, clock)
            registry._require_tables()
            registry._apply_migrations()
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise AirootError(
                "REGISTRY_INTEGRITY_FAILED",
                "registry database is unreadable",
                evidence=[str(exc)],
            ) from exc
        except BaseException:
            # §139: every refusal closes what this call opened — an unreadable database, a
            # missing table, a migration that would not apply. A leaked handle here is not
            # cosmetic: it keeps `state/registry.db` locked, so the caller cannot even delete
            # the root it just failed to open.
            if connection is not None:
                connection.close()
            raise
        return registry

    def _apply_migrations(self) -> None:
        """Bring an existing database up to :data:`TARGET_MIGRATION` (idempotent)."""

        try:
            applied = {int(row["version"]) for row in self._conn.execute("SELECT version FROM migrations")}
        except sqlite3.DatabaseError:
            applied = set()
        for version in sorted(MIGRATIONS):
            if version not in applied:
                MIGRATIONS[version](self._conn, self.clock)

    def migration_versions(self) -> list[int]:
        return sorted(int(row["version"]) for row in self._conn.execute("SELECT version FROM migrations"))

    def _require_tables(self) -> None:
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {row["name"] for row in rows}
        missing = {"meta", "instances", "bindings", "transactions", "events"} - names
        if missing:
            raise AirootError(
                "REGISTRY_INTEGRITY_FAILED",
                "registry is missing required tables",
                evidence=sorted(missing),
            )

    def _seed_meta(self, values: dict[str, str]) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for key, value in values.items():
                self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ meta #

    def meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _require_meta(self, key: str) -> str:
        value = self.meta(key)
        if value is None:
            raise AirootError("REGISTRY_INTEGRITY_FAILED", f"registry meta key missing: {key}", evidence=[str(self.path)])
        return value

    @property
    def generation(self) -> int:
        return int(self._require_meta(META_GENERATION))

    @property
    def policy_revision(self) -> int:
        return int(self._require_meta(META_POLICY_REVISION))

    @property
    def machine_id(self) -> str:
        return self._require_meta(META_MACHINE_ID)

    @property
    def root_instance_id(self) -> str:
        return self._require_meta(META_ROOT_INSTANCE_ID)

    @property
    def schema_version(self) -> int:
        return int(self._require_meta(META_SCHEMA_VERSION))

    # ----------------------------------------------------------------- write #

    @contextmanager
    def write(self, *, expected_generation: int, bump: bool = False) -> Iterator[sqlite3.Connection]:
        """One atomic declared-state change, guarded by generation compare-and-swap."""

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = int(
                self._conn.execute("SELECT value FROM meta WHERE key = ?", (META_GENERATION,)).fetchone()["value"]
            )
            if current != expected_generation:
                raise AirootError(
                    "STALE_GENERATION",
                    "registry generation advanced since the caller read it; re-read state and retry",
                    evidence=[f"expected={expected_generation}", f"actual={current}"],
                )
            yield self._conn
            if bump:
                self._conn.execute(
                    "UPDATE meta SET value = ? WHERE key = ?", (str(current + 1), META_GENERATION)
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def append_event(
        self,
        connection: sqlite3.Connection,
        *,
        state: str,
        detail: str,
        transaction_id: str | None = None,
        actor: str | None = None,
        approval_id: str | None = None,
        plan_hash: str | None = None,
        generation: int | None = None,
        before_state: str | None = None,
        after_state: str | None = None,
        artifact_digest: str | None = None,
        reason_code: str | None = None,
        outcome: str | None = None,
        approval_mode: str | None = None,
    ) -> None:
        """Append an authoritative historical fact (state/events).

        ``approval_mode`` is passed by the events that **establish** an approval — the approval
        itself and the transaction it authorises (draft §65). Later state events carry
        ``approval_id`` instead: recording the fact once and referencing it is how an audit trail
        stays readable, and it is what makes 三大核心契约's "必须记录 ``approval_mode=policy``"
        checkable without a join into a table the reader may not have.
        """

        connection.execute(
            """
            INSERT INTO events (transaction_id, state, detail, actor, approval_id, plan_hash, generation,
                                before_state, after_state, artifact_digest, reason_code, outcome, occurred_at,
                                approval_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                state,
                detail,
                actor,
                approval_id,
                plan_hash,
                generation,
                before_state,
                after_state,
                artifact_digest,
                reason_code,
                outcome,
                self.clock.timestamp(),
                approval_mode,
            ),
        )

    def event_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])

    def events(self, *, transaction_id: str | None = None) -> list[sqlite3.Row]:
        if transaction_id is None:
            return self._conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        return self._conn.execute(
            "SELECT * FROM events WHERE transaction_id = ? ORDER BY seq", (transaction_id,)
        ).fetchall()

    # ------------------------------------------------------------------ read #

    def instances(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM instances ORDER BY instance_id").fetchall()

    def instance(self, instance_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM instances WHERE instance_id = ?", (instance_id,)).fetchone()

    def bindings(self, *, active_only: bool = False) -> list[sqlite3.Row]:
        if active_only:
            return self._conn.execute("SELECT * FROM bindings WHERE active = 1 ORDER BY binding_key").fetchall()
        return self._conn.execute("SELECT * FROM bindings ORDER BY binding_key, generation").fetchall()

    def active_binding(self, binding_key_value: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM bindings WHERE binding_key = ? AND active = 1", (binding_key_value,)
        ).fetchone()

    def active_bindings_for_capability(self, capability_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT b.* FROM bindings b
            JOIN instances i ON i.instance_id = b.instance_id
            WHERE b.active = 1 AND i.capability_id = ?
            ORDER BY b.binding_key
            """,
            (capability_id,),
        ).fetchall()

    def external_references(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM external_references ORDER BY external_id").fetchall()

    def external_reference(self, external_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM external_references WHERE external_id = ?", (external_id,)
        ).fetchone()

    def external_references_for_data_root(self, data_root_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM external_references WHERE data_root_id = ? ORDER BY path", (data_root_id,)
        ).fetchall()

    def data_roots(self, *, active_only: bool = True) -> list[sqlite3.Row]:
        if active_only:
            return self._conn.execute("SELECT * FROM data_roots WHERE active = 1 ORDER BY path").fetchall()
        return self._conn.execute("SELECT * FROM data_roots ORDER BY path").fetchall()

    def data_root(self, data_root_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM data_roots WHERE data_root_id = ?", (data_root_id,)).fetchone()

    def data_root_by_path(self, path: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM data_roots WHERE path = ?", (path,)).fetchone()

    def environment_persist_records(self, *, active_only: bool = True) -> list[sqlite3.Row]:
        if active_only:
            return self._conn.execute(
                "SELECT * FROM environment_persist WHERE forgotten_at IS NULL "
                "ORDER BY capability_id, scope, variable"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM environment_persist ORDER BY capability_id, scope, variable"
        ).fetchall()

    def environment_persist_record(
        self, capability_id: str, scope: str, variable: str
    ) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM environment_persist WHERE capability_id = ? AND scope = ? AND variable = ?",
            (capability_id, scope, variable),
        ).fetchone()

    def extensions(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM extensions ORDER BY extension_id").fetchall()

    def extension(self, extension_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM extensions WHERE extension_id = ?", (extension_id,)).fetchone()

    def transactions(self, *, unfinished_only: bool = False) -> list[sqlite3.Row]:
        if unfinished_only:
            return self._conn.execute(
                "SELECT * FROM transactions WHERE state NOT IN ('FINALIZED', 'ROLLED_BACK', 'EXPIRED') "
                "ORDER BY created_at, transaction_id"
            ).fetchall()
        return self._conn.execute("SELECT * FROM transactions ORDER BY created_at, transaction_id").fetchall()

    def transaction(self, transaction_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)
        ).fetchone()

    def approval(self, approval_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()

    def approval_by_nonce(self, nonce: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM approvals WHERE nonce = ?", (nonce,)).fetchone()

    # ------------------------------------------------------------- mutations #
    # All of these take an open write connection so the caller controls the
    # transaction boundary and the generation bump.

    def add_instance(self, connection: sqlite3.Connection, instance: Any, *, source_digest: str | None = None) -> bool:
        """Register an immutable instance. Idempotent for an identical digest."""

        existing = self.instance(instance.instance_id)
        if existing is not None:
            if existing["artifact_digest"] != instance.artifact_digest:
                raise AirootError(
                    "INSTANCE_CONFLICT",
                    f"instance {instance.instance_id} already exists with a different digest",
                    evidence=[
                        f"existing={existing['artifact_digest']}",
                        f"incoming={instance.artifact_digest}",
                        "an update must create a new instance_id",
                    ],
                )
            return False
        payload = instance.payload if instance.payload is not None else {}
        connection.execute(
            """
            INSERT INTO instances (instance_id, kind, capability_id, tool_id, version, platform, architecture,
                                   install_backend_id, artifact_digest, store_path, file_manifest_digest,
                                   lifecycle_status, health, entrypoints_json, payload_json, source_digest,
                                   created_at, retired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instance.instance_id,
                instance.kind,
                instance.capability_id,
                instance.tool_id,
                instance.version,
                instance.platform,
                instance.architecture,
                instance.install_backend_id,
                instance.artifact_digest,
                instance.store_path,
                instance.file_manifest_digest,
                instance.lifecycle_status,
                instance.health,
                json.dumps(list(instance.entrypoints)),
                json.dumps(payload, sort_keys=True),
                source_digest,
                instance.created_at or self.clock.timestamp(),
                instance.retired_at,
            ),
        )
        return True

    def set_instance_status(
        self,
        connection: sqlite3.Connection,
        instance_id: str,
        *,
        health: str | None = None,
        lifecycle_status: str | None = None,
        retired_at: str | None = None,
        collected_at: str | None = None,
        collected_approval_id: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        if health is not None:
            assignments.append("health = ?")
            values.append(health)
        if lifecycle_status is not None:
            assignments.append("lifecycle_status = ?")
            values.append(lifecycle_status)
        if retired_at is not None:
            assignments.append("retired_at = ?")
            values.append(retired_at)
        if collected_at is not None:
            assignments.append("collected_at = ?")
            values.append(collected_at)
        if collected_approval_id is not None:
            assignments.append("collected_approval_id = ?")
            values.append(collected_approval_id)
        if not assignments:
            return
        values.append(instance_id)
        connection.execute(f"UPDATE instances SET {', '.join(assignments)} WHERE instance_id = ?", values)

    def instance_has_collected_payload(self, instance_id: str) -> bool:
        row = self.instance(instance_id)
        return bool(row is not None and row["collected_at"])

    def clear_collected(self, connection: sqlite3.Connection, instance_id: str) -> bool:
        """Drop the "collected" latch, because the payload is back on disk (draft §164).

        ``collected_at`` records one thing: an approved ``gc --apply`` removed this instance's
        payload. Everything downstream reads it that way — ``tool verify`` refuses to verify and
        ``tool status`` reports ``PAYLOAD_COLLECTED`` — so it is a statement about the bytes, not a
        permanent property of the row. Re-installing the same version puts the same bytes back (the
        runner's commit step wrote them), and leaving the latch set made the registry assert
        something false about a payload that was sitting in ``store/``. `set_instance_status` cannot
        express this: it treats ``None`` as "leave this column alone", which is what every other
        caller needs.
        """

        cursor = connection.execute(
            "UPDATE instances SET collected_at = NULL, collected_approval_id = NULL "
            "WHERE instance_id = ? AND collected_at IS NOT NULL",
            (instance_id,),
        )
        return cursor.rowcount > 0

    def bind_active(self, connection: sqlite3.Connection, binding: Any) -> None:
        """Make ``binding`` the single active binding for its key (deactivate others)."""

        connection.execute(
            "UPDATE bindings SET active = 0 WHERE binding_key = ? AND active = 1", (binding.binding_key,)
        )
        row = connection.execute(
            "SELECT 1 FROM bindings WHERE binding_key = ? AND generation = ?",
            (binding.binding_key, binding.generation),
        ).fetchone()
        updated_at = binding.updated_at or self.clock.timestamp()
        if row is None:
            connection.execute(
                """
                INSERT INTO bindings (binding_key, instance_id, scope, zone, exposure, generation, active,
                                      project_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    binding.binding_key,
                    binding.instance_id,
                    binding.scope,
                    binding.zone,
                    binding.exposure,
                    binding.generation,
                    binding.project_id,
                    updated_at,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE bindings SET instance_id = ?, scope = ?, zone = ?, exposure = ?, active = 1,
                                    project_id = ?, updated_at = ?
                WHERE binding_key = ? AND generation = ?
                """,
                (
                    binding.instance_id,
                    binding.scope,
                    binding.zone,
                    binding.exposure,
                    binding.project_id,
                    updated_at,
                    binding.binding_key,
                    binding.generation,
                ),
            )

    def clear_active_binding(self, connection: sqlite3.Connection, binding_key_value: str) -> None:
        connection.execute(
            "UPDATE bindings SET active = 0 WHERE binding_key = ? AND active = 1", (binding_key_value,)
        )

    def upsert_external_reference(self, connection: sqlite3.Connection, reference: Any) -> None:
        """Record steward-domain facts about someone else's object (never the object)."""

        connection.execute(
            """
            INSERT OR REPLACE INTO external_references (external_id, capability_id, path, management, health,
                                                        observed_digest, observed_at, payload_json, capability_kind,
                                                        data_root_id, version, versions_json, active_version,
                                                        architecture, entrypoints_json, probe_level, source_kind,
                                                        evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference.external_id,
                reference.capability_id,
                reference.path,
                reference.management,
                reference.health,
                reference.observed_digest,
                reference.observed_at,
                json.dumps(reference.to_schema(), sort_keys=True),
                reference.capability_kind,
                reference.data_root_id,
                reference.version,
                json.dumps(list(reference.versions)),
                reference.active_version,
                reference.architecture,
                json.dumps(list(reference.entrypoints)),
                reference.probe_level,
                reference.source_kind,
                json.dumps([dict(item) for item in reference.evidence], sort_keys=True),
            ),
        )

    def forget_external_reference(self, connection: sqlite3.Connection, external_id: str) -> bool:
        """Drop AIROOT's record only; the referenced file is never touched."""

        cursor = connection.execute("DELETE FROM external_references WHERE external_id = ?", (external_id,))
        return cursor.rowcount > 0

    def add_data_root(self, connection: sqlite3.Connection, data_root: Any) -> None:
        """Register a data root.

        ``ON CONFLICT(data_root_id)`` (not ``INSERT OR REPLACE``): re-adding the same
        root updates it, while the same *path* under a different id must raise
        instead of silently unregistering the previous root.
        """

        connection.execute(
            """
            INSERT INTO data_roots (data_root_id, path, role, volume_serial, acl_baseline_json,
                                    whitelist_revision, added_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (data_root_id) DO UPDATE SET
                path = excluded.path,
                role = excluded.role,
                volume_serial = excluded.volume_serial,
                acl_baseline_json = excluded.acl_baseline_json,
                whitelist_revision = excluded.whitelist_revision,
                active = excluded.active
            """,
            (
                data_root.data_root_id,
                data_root.path,
                data_root.role,
                data_root.volume_serial,
                json.dumps(data_root.acl_baseline, sort_keys=True) if data_root.acl_baseline else None,
                data_root.whitelist_revision,
                data_root.added_at,
                1 if data_root.active else 0,
            ),
        )

    def forget_data_root(self, connection: sqlite3.Connection, data_root_id: str) -> bool:
        cursor = connection.execute("DELETE FROM data_roots WHERE data_root_id = ?", (data_root_id,))
        return cursor.rowcount > 0

    def record_environment_persist(self, connection: sqlite3.Connection, record: Any) -> None:
        """Record a persisted variable together with the exact value it replaced."""

        connection.execute(
            """
            INSERT OR REPLACE INTO environment_persist (capability_id, scope, variable, external_id, value,
                                                        value_kind, old_value, old_kind, plan_id, plan_hash,
                                                        approval_id, written_at, forgotten_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                record.capability_id,
                record.scope,
                record.variable,
                record.external_id,
                record.value,
                record.value_kind,
                record.old_value,
                record.old_kind,
                record.plan_id,
                record.plan_hash,
                record.approval_id,
                record.written_at,
            ),
        )

    def mark_environment_persist_forgotten(
        self, connection: sqlite3.Connection, capability_id: str, scope: str, variable: str
    ) -> bool:
        cursor = connection.execute(
            "UPDATE environment_persist SET forgotten_at = ? "
            "WHERE capability_id = ? AND scope = ? AND variable = ? AND forgotten_at IS NULL",
            (self.clock.timestamp(), capability_id, scope, variable),
        )
        return cursor.rowcount > 0

    def upsert_extension(self, connection: sqlite3.Connection, manifest: dict[str, Any], *, health: str = "healthy") -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO extensions (extension_id, extension_version, protocol_version, capability_types,
                                               implementation_id, manifest_json, loaded_at, health)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest["extension_id"],
                manifest["extension_version"],
                int(manifest["protocol_version"]),
                json.dumps(manifest.get("capability_types", [])),
                manifest["implementation_id"],
                json.dumps(manifest, sort_keys=True),
                self.clock.timestamp(),
                health,
            ),
        )

    def upsert_transaction(self, connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        failure = payload.get("failure") or {}
        connection.execute(
            """
            INSERT OR REPLACE INTO transactions (transaction_id, plan_id, plan_hash, state, root_instance_id,
                                                 machine_id, instance_id, generation_before, generation_after,
                                                 created_at, updated_at, journal_seq, approval_id, approval_nonce,
                                                 failure_code, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["transaction_id"],
                payload["plan_id"],
                payload["plan_hash"],
                payload["state"],
                payload["root_instance_id"],
                payload["machine_id"],
                payload.get("instance_id"),
                payload.get("generation_before"),
                payload.get("generation_after"),
                payload["created_at"],
                payload["updated_at"],
                int(payload["journal_seq"]),
                payload.get("approval_id"),
                payload.get("approval_nonce"),
                failure.get("code") if isinstance(failure, dict) else None,
                json.dumps(payload, sort_keys=True),
            ),
        )

    def upsert_approval(self, connection: sqlite3.Connection, token: dict[str, Any]) -> None:
        signature = token["signature"]
        connection.execute(
            """
            INSERT OR REPLACE INTO approvals (approval_id, plan_hash, root_instance_id, machine_id, policy_revision,
                                              approval_mode, issuer, approved_by_sid, issued_at, expires_at, nonce,
                                              signature_json, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token["approval_id"],
                token["plan_hash"],
                token["root_instance_id"],
                token["machine_id"],
                int(token["policy_revision"]),
                token["approval_mode"],
                token["issuer"],
                token.get("approved_by_sid"),
                token["issued_at"],
                token["expires_at"],
                token["nonce"],
                json.dumps(signature, sort_keys=True),
                json.dumps(token, sort_keys=True),
            ),
        )

    def consume_approval(self, connection: sqlite3.Connection, approval_id: str) -> None:
        """Single-use consumption; a second attempt is ``APPROVAL_REPLAYED``."""

        row = connection.execute(
            "SELECT consumed_at FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise AirootError("INVALID_APPROVAL", f"unknown approval: {approval_id}")
        if row["consumed_at"]:
            raise AirootError(
                "APPROVAL_REPLAYED",
                f"approval {approval_id} was already consumed",
                evidence=[f"consumed_at={row['consumed_at']}"],
            )
        connection.execute(
            "UPDATE approvals SET consumed_at = ? WHERE approval_id = ?", (self.clock.timestamp(), approval_id)
        )

    # ------------------------------------------------------------ integrity #

    def integrity_problems(self) -> list[str]:
        """``PRAGMA integrity_check`` plus the invariants the DB cannot index."""

        problems: list[str] = []
        try:
            rows = self._conn.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            return [f"integrity_check failed: {exc}"]
        for row in rows:
            value = str(row[0])
            if value.lower() != "ok":
                problems.append(f"integrity_check: {value}")

        for row in self._conn.execute(
            "SELECT binding_key, COUNT(*) AS n FROM bindings WHERE active = 1 GROUP BY binding_key HAVING n > 1"
        ).fetchall():
            problems.append(f"multiple active bindings for {row['binding_key']}: {row['n']}")

        for row in self._conn.execute(
            """
            SELECT b.binding_key, b.instance_id FROM bindings b
            LEFT JOIN instances i ON i.instance_id = b.instance_id
            WHERE i.instance_id IS NULL
            """
        ).fetchall():
            problems.append(f"binding {row['binding_key']} points at missing instance {row['instance_id']}")
        return problems

    def declared_state_digest(self) -> str:
        """Digest over declared rows, used by doctor to detect projection drift."""

        from ..canon import digest_bytes, canonical_bytes

        payload = {
            "generation": self.generation,
            "instances": [dict(row) for row in self.instances()],
            "bindings": [dict(row) for row in self.bindings()],
            "external_references": [dict(row) for row in self.external_references()],
        }
        return digest_bytes(canonical_bytes(payload))

    def update_projection(self) -> dict[str, Any]:
        """Rewrite ``state/registry.json`` and ``logs/audit`` from authoritative rows.

        The projection is this build's **own** document, so it is checked against the schema that
        describes it before the file is written (§169). Without this, `root init` wrote a
        `state/registry.json` its own `registry-projection` schema rejects — the root answered
        `root status` with `SUCCESS` and refused every plan as `SELF_VALIDATION_FAILED`. The check
        is `validate_self`, not `validate_document`: a failure here is this build's defect, not the
        caller's input.
        """

        from ..schema_io import validate_self
        from .projection import build_projection, write_audit_projection

        projection = build_projection(self)
        validate_self("registry-projection", projection)
        target = Path(self.path).parent / "registry.json"
        target.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_audit_projection(self)
        return projection
