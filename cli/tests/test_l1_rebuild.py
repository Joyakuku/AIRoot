"""L1: ``rebuild`` regenerates derived state and only derived state (draft §24).

The rule under test: **authority is never rebuilt from its own projection.** Everything else
follows from it — the database is untouched, unfinished transactions are reported rather than
repaired, unfamiliar objects are reported rather than adopted, and nothing is ever deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fake_issuer
from airoot.canon import digest_text
from airoot.caps.doctor import diagnostics_by_code, doctor
from airoot.caps.rebuild import REBUILD_DIR, apply_rebuild, rebuild_plan
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


def install(registry, clock, root) -> str:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return str(plan["target"]["instance_id"])


# --------------------------------------------------------------------------- #
# plan vs apply
# --------------------------------------------------------------------------- #


def test_a_fresh_root_has_nothing_stale_to_rebuild(registry, root) -> None:
    findings = rebuild_plan(registry, root.path)

    assert findings.stale_projection is False
    assert findings.stale_audit is False
    assert findings.orphans == []


def test_rebuild_rewrites_the_derived_files_and_archives_the_previous_ones(
    registry, clock, root
) -> None:
    install(registry, clock, root)
    projection = Path(root.path) / "state" / "registry.json"
    audit = Path(root.path) / "logs" / "audit" / "events.json"
    before_projection = projection.read_text(encoding="utf-8")
    before_audit = audit.read_text(encoding="utf-8")

    document = apply_rebuild(registry, root.path, clock=clock)

    assert document["database_touched"] is False
    assert document["files_deleted"] == 0
    archived = Path(document["archived_to"])
    assert (archived / "registry.json").read_text(encoding="utf-8") == before_projection
    assert (archived / "audit-events.json").read_text(encoding="utf-8") == before_audit
    # The rebuild is a rewrite of derived state, so a second run is stable in content.
    second = apply_rebuild(registry, root.path, clock=clock)
    assert second["rebuilt"] == document["rebuilt"]


def test_rebuild_repairs_a_stale_projection(registry, clock, root) -> None:
    install(registry, clock, root)
    projection = Path(root.path) / "state" / "registry.json"
    projection.write_text('{"schema_version": 1, "generation": 999}\n', encoding="utf-8")

    stalled = doctor(root.path, registry=registry, data_roots=False)
    assert "REGISTRY_PROJECTION_STALE" in diagnostics_by_code(stalled)

    apply_rebuild(registry, root.path, clock=clock)

    healed = doctor(root.path, registry=registry, data_roots=False)
    assert "REGISTRY_PROJECTION_STALE" not in diagnostics_by_code(healed)
    assert json.loads(projection.read_text(encoding="utf-8"))["generation"] == registry.generation


def test_rebuild_repairs_a_stale_audit_projection(registry, clock, root) -> None:
    install(registry, clock, root)
    audit = Path(root.path) / "logs" / "audit" / "events.json"
    audit.write_text('{"schema_version": 1, "digest": "sha256:' + "0" * 64 + '"}\n', encoding="utf-8")
    assert "AUDIT_PROJECTION_DRIFT" in diagnostics_by_code(doctor(root.path, registry=registry, data_roots=False))

    apply_rebuild(registry, root.path, clock=clock)

    assert "AUDIT_PROJECTION_DRIFT" not in diagnostics_by_code(
        doctor(root.path, registry=registry, data_roots=False)
    )


# --------------------------------------------------------------------------- #
# what rebuild must never do
# --------------------------------------------------------------------------- #


def test_a_broken_database_is_refused_not_laundered(registry, clock, root, monkeypatch) -> None:
    """The authority cannot be rebuilt from its own projection (draft §24.3-1).

    The database guards most inconsistencies with foreign keys and partial indexes, so the
    condition is injected: what is under test is *our* refusal, not SQLite's.
    """

    install(registry, clock, root)
    projection = Path(root.path) / "state" / "registry.json"
    before = digest_text(projection.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        type(registry), "integrity_problems", lambda self: ["multiple active bindings: synthetic"]
    )

    with pytest.raises(AirootError) as caught:
        apply_rebuild(registry, root.path, clock=clock)

    assert caught.value.reason_code == "REGISTRY_INTEGRITY_FAILED"
    assert any("never rebuilt from its own projection" in item for item in caught.value.evidence)
    assert not (Path(root.path) / REBUILD_DIR).exists(), "a refused rebuild archives nothing"
    assert digest_text(projection.read_text(encoding="utf-8")) == before


def test_orphan_payloads_are_reported_and_left_alone(registry, clock, root) -> None:
    install(registry, clock, root)
    orphan = Path(root.path) / "store" / "orphan" / "tool" / "9.9.9" / "win-x64"
    orphan.mkdir(parents=True)
    (orphan / "artifact.json").write_text("{}", encoding="utf-8")
    digest_before = digest_text((orphan / "artifact.json").read_text(encoding="utf-8"))

    findings = rebuild_plan(registry, root.path)
    document = apply_rebuild(registry, root.path, clock=clock)

    assert "store/orphan/tool/9.9.9/win-x64" in findings.orphans
    assert document["adopted"] == 0
    assert orphan.is_dir(), "rebuild never deletes an unfamiliar object"
    assert digest_text((orphan / "artifact.json").read_text(encoding="utf-8")) == digest_before


def test_unmanaged_references_are_reported_not_adopted(registry, root) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/dr-env/huawei', 'unknown', ?, 'unmanaged', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (str(Path(root.path) / "tools" / "huawei"),),
        )

    findings = rebuild_plan(registry, root.path)

    assert findings.unmanaged == ["external/dr-env/huawei"]
    assert registry.external_reference("external/dr-env/huawei")["management"] == "unmanaged"


def test_unfinished_transactions_are_reported_not_repaired(registry, clock, root) -> None:
    """`repair` owns transactions; `rebuild` only says they exist (draft §24.3-3)."""

    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO transactions (transaction_id, plan_id, plan_hash, state, root_instance_id,
                                      machine_id, instance_id, generation_before, generation_after,
                                      created_at, updated_at, journal_seq, payload_json)
            VALUES ('tx/pending', 'plan/x', 'sha256:' || printf('%.64d', 0), 'PENDING', ?, ?, NULL, 0, 0,
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z', 1, '{}')
            """,
            (registry.root_instance_id, registry.machine_id),
        )

    findings = rebuild_plan(registry, root.path)
    document = apply_rebuild(registry, root.path, clock=clock)

    assert findings.pending_transactions == ["tx/pending"]
    assert document["repaired_transactions"] == 0
    assert [str(row["state"]) for row in registry.transactions()] == ["PENDING"]


def test_the_database_file_is_byte_identical_after_a_rebuild(registry, clock, root) -> None:
    install(registry, clock, root)
    database = Path(registry.path)
    # Force WAL contents into the main file so the comparison is meaningful.
    registry._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = database.read_bytes()

    apply_rebuild(registry, root.path, clock=clock)
    registry._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    assert database.read_bytes() == before


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_rebuild_plan_writes_nothing(capsys, registry, clock, root) -> None:
    install(registry, clock, root)
    cli_root = Path(registry.path).parent.parent
    before = digest_text((cli_root / "state" / "registry.json").read_text(encoding="utf-8"))

    code, document = run(capsys, "--json", "--root", str(cli_root), "rebuild", "--plan")

    assert code == 0
    assert document["operation"] == "rebuild_plan"
    assert "state/registry.json" in document["would_rewrite"]
    assert not (cli_root / REBUILD_DIR).exists(), "a plan archives nothing"
    after = digest_text((cli_root / "state" / "registry.json").read_text(encoding="utf-8"))
    assert before == after


def test_cli_rebuild_applies_and_is_idempotent(capsys, registry, clock, root) -> None:
    install(registry, clock, root)
    cli_root = Path(registry.path).parent.parent

    code, first = run(capsys, "--json", "--root", str(cli_root), "rebuild")
    assert code == 0, first
    assert first["database_touched"] is False
    assert Path(first["archived_to"]).is_dir()

    code, second = run(capsys, "--json", "--root", str(cli_root), "rebuild")
    assert code == 0
    assert second["rebuilt"] == first["rebuilt"]
    assert Path(second["archived_to"]).name != Path(first["archived_to"]).name, "each run keeps its own snapshot"
