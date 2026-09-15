-- AIROOT P1 registry schema (declared state authority).
--
-- SQLite is the authoritative store for declared/historical state; the JSON
-- projection in state/registry.json is derived and never accepted as an edit
-- (v0.3 §9.11, §13.1). Invariants that must never be violable live here rather
-- than only in application code:
--   * one active binding per binding key  -> ux_bindings_active
--   * immutable instance identity         -> trg_instances_immutable

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    note       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    instance_id          TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL CHECK (kind IN ('managed_tool', 'runtime')),
    capability_id        TEXT NOT NULL,
    tool_id              TEXT,
    version              TEXT NOT NULL,
    platform             TEXT NOT NULL,
    architecture         TEXT NOT NULL,
    install_backend_id   TEXT NOT NULL,
    artifact_digest      TEXT NOT NULL,
    store_path           TEXT NOT NULL,
    file_manifest_digest TEXT,
    lifecycle_status     TEXT NOT NULL,
    health               TEXT NOT NULL,
    entrypoints_json     TEXT NOT NULL DEFAULT '[]',
    payload_json         TEXT NOT NULL,
    source_digest        TEXT,
    created_at           TEXT NOT NULL,
    retired_at           TEXT,
    -- v5: set only when `gc --apply` actually removed this instance's payload. The row stays
    -- because `bindings.instance_id` references it: deleting it would destroy the binding
    -- history that "retired" exists to preserve (draft §19.3-2). `doctor` uses this to tell a
    -- deliberate collection apart from a payload that vanished (PAYLOAD_MISSING).
    collected_at          TEXT,
    collected_approval_id TEXT
);

-- Identity and payload binding of an instance are immutable: an update creates a
-- new instance_id and a new generation (v0.3 §9.3, §9.7.1).
CREATE TRIGGER IF NOT EXISTS trg_instances_immutable
BEFORE UPDATE OF instance_id, artifact_digest, store_path, install_backend_id ON instances
BEGIN
    SELECT RAISE(ABORT, 'INSTANCE_IMMUTABLE: instance identity and payload digest cannot change');
END;

CREATE TABLE IF NOT EXISTS bindings (
    binding_key  TEXT NOT NULL,
    instance_id  TEXT NOT NULL REFERENCES instances (instance_id),
    scope        TEXT NOT NULL CHECK (scope IN ('system', 'machine', 'session', 'project')),
    zone         TEXT NOT NULL CHECK (zone IN ('R', 'W', 'P')),
    exposure     TEXT NOT NULL CHECK (exposure IN ('stable_launcher', 'session_env', 'project_binding', 'none')),
    generation   INTEGER NOT NULL,
    active       INTEGER NOT NULL CHECK (active IN (0, 1)),
    project_id   TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (binding_key, generation)
);

-- The frozen rule "one active implementation per binding key" (v0.3 §3.2, §23.6).
CREATE UNIQUE INDEX IF NOT EXISTS ux_bindings_active ON bindings (binding_key) WHERE active = 1;

CREATE TABLE IF NOT EXISTS external_references (
    external_id     TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    path            TEXT NOT NULL,
    management      TEXT NOT NULL CHECK (management IN ('external_reference', 'unmanaged', 'project_owned', 'quarantined')),
    health          TEXT NOT NULL CHECK (health IN ('healthy', 'degraded', 'broken', 'stale', 'drifted')),
    observed_digest TEXT,
    observed_at     TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    -- v2: the steward domain needs facts, not just a path. See ADR-0004 and the
    -- 管家模型 contract draft §4/§7 — a reference must be able to answer
    -- "which python is 3.12" without executing anything.
    capability_kind  TEXT CHECK (capability_kind IN ('runtime', 'tool')),
    data_root_id     TEXT,
    version          TEXT,
    -- v3: one object may host several versions (nvm keeps v22.14.0/v22.15.0/v26.8.1
    -- side by side). The object is one reference; the version set and the observed
    -- active version are facts about it (draft §11 open question 3).
    versions_json    TEXT NOT NULL DEFAULT '[]',
    active_version   TEXT,
    architecture     TEXT,
    entrypoints_json TEXT NOT NULL DEFAULT '[]',
    probe_level      INTEGER,
    source_kind      TEXT,
    evidence_json    TEXT NOT NULL DEFAULT '[]'
);

-- Data roots: user-declared protected top-level directories (e.g. D:\env, D:\tools)
-- whose contents are *observed*, never owned and never deleted (draft §3).
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

CREATE INDEX IF NOT EXISTS ix_external_references_capability ON external_references (capability_id);
CREATE INDEX IF NOT EXISTS ix_external_references_data_root ON external_references (data_root_id);

-- v4: every persisted environment variable AIROOT ever wrote. This is what makes
-- "env forget --all" exact: the complete old value is recorded before the write, so
-- removing AIROOT never leaves a half-applied environment behind (draft §13.4).
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

CREATE TABLE IF NOT EXISTS extensions (
    extension_id       TEXT PRIMARY KEY,
    extension_version  TEXT NOT NULL,
    protocol_version   INTEGER NOT NULL,
    capability_types   TEXT NOT NULL,
    implementation_id  TEXT NOT NULL,
    manifest_json      TEXT NOT NULL,
    loaded_at          TEXT NOT NULL,
    health             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id   TEXT PRIMARY KEY,
    plan_id          TEXT NOT NULL,
    plan_hash        TEXT NOT NULL,
    state            TEXT NOT NULL,
    root_instance_id TEXT NOT NULL,
    machine_id       TEXT NOT NULL,
    instance_id      TEXT,
    generation_before INTEGER,
    generation_after  INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    journal_seq      INTEGER NOT NULL,
    approval_id      TEXT,
    approval_nonce   TEXT,
    failure_code     TEXT,
    payload_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT,
    state           TEXT NOT NULL,
    detail          TEXT NOT NULL,
    actor           TEXT,
    approval_id     TEXT,
    plan_hash       TEXT,
    generation      INTEGER,
    before_state    TEXT,
    after_state     TEXT,
    artifact_digest TEXT,
    reason_code     TEXT,
    outcome         TEXT,
    occurred_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id      TEXT PRIMARY KEY,
    plan_hash        TEXT NOT NULL,
    root_instance_id TEXT NOT NULL,
    machine_id       TEXT NOT NULL,
    policy_revision  INTEGER NOT NULL,
    approval_mode    TEXT NOT NULL CHECK (approval_mode IN ('human', 'policy')),
    issuer           TEXT NOT NULL,
    approved_by_sid  TEXT,
    issued_at        TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    nonce            TEXT NOT NULL UNIQUE,
    signature_json   TEXT NOT NULL,
    consumed_at      TEXT,
    revoked_at       TEXT,
    payload_json     TEXT NOT NULL
);
