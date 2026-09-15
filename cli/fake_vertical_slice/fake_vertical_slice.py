#!/usr/bin/env python3
"""Test-only AIROOT vertical slice.

This file intentionally uses test_hmac_sha256 and a temporary directory. It does
not install a service, touch ACLs, edit PATH, or execute the fake payload.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT.parent / "schema"
FIXTURE_DIR = ROOT / "fixtures" / "fake-tool"
UTC = timezone.utc
SECRET = b"airoot-test-only-secret-v1"


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def tree_digest(root: Path) -> str:
    records: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        records.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "digest": digest_file(path)})
    return digest_bytes(canonical(records))


def file_manifest(root: Path) -> list[dict[str, Any]]:
    return [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size, "digest": digest_file(p), "mode": "file"} for p in sorted(p for p in root.rglob("*") if p.is_file())]


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate(name: str, value: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment setup error
        raise RuntimeError("jsonschema is required for the fake vertical slice") from exc
    schema = load_schema(name)
    common = load_schema("common.schema.json")
    store = {common["$id"]: common, "https://airoot.local/schema/common.schema.json": common}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resolver = jsonschema.RefResolver.from_schema(schema, store=store)
    validator = jsonschema.Draft202012Validator(schema, resolver=resolver)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        locations = "; ".join(f"{list(error.path)}: {error.message}" for error in errors)
        raise ValueError(f"{name} rejected: {locations}")


def sign_approval(unsigned: dict[str, Any]) -> str:
    return "base64:" + base64.b64encode(hmac.new(SECRET, canonical(unsigned), hashlib.sha256).digest()).decode("ascii")


def artifact_source(path: Path) -> dict[str, Any]:
    return {
        "kind": "generated_fixture",
        "locator": str(path.resolve()),
        "provenance": {"source_id": "fixture/fake-tool-v1", "publisher": "airoot-test"},
        "integrity": {"artifact_digest": tree_digest(path), "file_manifest_digest": digest_bytes(canonical(file_manifest(path)))},
        "signature": {"algorithm": "test_hmac_sha256", "key_id": "fixture-key", "value": "base64:" + base64.b64encode(hmac.new(SECRET, tree_digest(path).encode(), hashlib.sha256).digest()).decode("ascii")},
    }


def make_plan(root_id: str, machine_id: str, source: dict[str, Any], version: str, generation: int) -> dict[str, Any]:
    timestamp = datetime.now(UTC).replace(microsecond=0)
    target = {
        "capability_id": "fake-tool",
        "instance_id": f"fake-tool/fake-tool/{version}/win-x64",
        "kind": "managed_tool",
        "version": version,
        "platform": "windows",
        "architecture": "x64",
        "binding_key": "machine/fake-tool/windows/x64",
    }
    plan = {
        "schema_version": 1,
        "plan_id": f"plan/fake-tool/{version}/{uuid.uuid4().hex[:12]}",
        "plan_hash": "sha256:" + "0" * 64,
        "operation": "install_tool",
        "root_instance_id": root_id,
        "machine_id": machine_id,
        "policy_revision": 1,
        "created_at": timestamp.isoformat().replace("+00:00", "Z"),
        "expires_at": (timestamp + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "requested_by": "fake-test",
        "target": target,
        "operations": [
            {"step_id": "fetch", "kind": "fetch", "description": "read deterministic fixture", "target_scope": "machine", "reversible": True, "source_mutation": "none", "artifact_digest": source["integrity"]["artifact_digest"]},
            {"step_id": "verify", "kind": "verify", "description": "verify tree digest and manifest", "target_scope": "machine", "reversible": True, "source_mutation": "none", "artifact_digest": source["integrity"]["artifact_digest"]},
            {"step_id": "stage", "kind": "stage", "description": "copy to transaction stage", "target_scope": "machine", "reversible": True, "source_mutation": "none", "artifact_digest": source["integrity"]["artifact_digest"]},
            {"step_id": "commit", "kind": "commit", "description": "move immutable payload into store", "target_scope": "machine", "reversible": True, "source_mutation": "none", "artifact_digest": source["integrity"]["artifact_digest"]},
            {"step_id": "expose", "kind": "expose", "description": "write stable launcher metadata", "target_scope": "machine", "reversible": True, "source_mutation": "none", "artifact_digest": source["integrity"]["artifact_digest"]},
        ],
        "source": source,
        "side_effects": ["writes_store", "writes_registry"],
        "canonicalization": "jcs-rfc8785-compatible",
        "metadata": {"test_generation_before": generation},
    }
    unsigned = dict(plan)
    unsigned.pop("plan_hash")
    plan["plan_hash"] = digest_bytes(canonical(unsigned))
    validate("plan.schema.json", plan)
    return plan


def make_approval(plan: dict[str, Any], root_id: str, machine_id: str, nonce: str | None = None) -> dict[str, Any]:
    issued = datetime.now(UTC).replace(microsecond=0)
    unsigned = {
        "schema_version": 1,
        "approval_id": f"approval/{uuid.uuid4().hex[:12]}",
        "plan_hash": plan["plan_hash"],
        "root_instance_id": root_id,
        "machine_id": machine_id,
        "policy_revision": 1,
        "approval_mode": "policy",
        "issuer": "test-approval-issuer",
        "approved_by_sid": None,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "nonce": nonce or base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("="),
    }
    approval = dict(unsigned)
    approval["signature"] = {"algorithm": "test_hmac_sha256", "key_id": "test-approval-key", "value": sign_approval(unsigned)}
    approval["consumed_at"] = None
    validate("approval-token.schema.json", approval)
    return approval


class FakeBroker:
    def __init__(self, root: Path, root_id: str = "root/fake-test", machine_id: str = "machine-fake-0001"):
        self.root = root
        self.root_id = root_id
        self.machine_id = machine_id
        self.store = root / "store"
        self.tools = root / "tools"
        self.exposure = root / "exposure" / "bin"
        self.tx_dir = root / "tx"
        self.state = root / "state"
        for directory in (self.store, self.tools, self.exposure, self.tx_dir, self.state):
            directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / "registry.db")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS instances (instance_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, active INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS bindings (binding_key TEXT PRIMARY KEY, instance_id TEXT NOT NULL, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, consumed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
        """)
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('generation', '0')")
        self.db.commit()

    def generation(self) -> int:
        return int(self.db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0])

    def tx_path(self, txid: str) -> Path:
        return self.tx_dir / f"{txid.replace('/', '_')}.json"

    def write_tx(self, tx: dict[str, Any]) -> None:
        validate("transaction.schema.json", tx)
        self.tx_path(tx["transaction_id"]).write_text(json.dumps(tx, indent=2, sort_keys=True), encoding="utf-8")

    def event(self, txid: str, state: str, detail: str) -> None:
        self.db.execute("INSERT INTO events(transaction_id, state, detail) VALUES(?, ?, ?)", (txid, state, detail))
        self.db.commit()

    def check_approval(self, plan: dict[str, Any], approval: dict[str, Any]) -> None:
        validate("approval-token.schema.json", approval)
        if approval["plan_hash"] != plan["plan_hash"]:
            raise ValueError("INVALID_APPROVAL: plan hash mismatch")
        if approval["root_instance_id"] != self.root_id or approval["machine_id"] != self.machine_id:
            raise ValueError("INVALID_APPROVAL: root or machine mismatch")
        if approval["signature"]["algorithm"] != "test_hmac_sha256":
            raise ValueError("INVALID_APPROVAL: unsupported test signature")
        unsigned = dict(approval)
        unsigned.pop("signature")
        unsigned.pop("consumed_at", None)
        expected = sign_approval(unsigned)
        if not hmac.compare_digest(expected, approval["signature"]["value"]):
            raise ValueError("INVALID_APPROVAL: signature mismatch")
        if self.db.execute("SELECT 1 FROM nonces WHERE nonce=?", (approval["nonce"],)).fetchone():
            raise ValueError("APPROVAL_REPLAYED: nonce already consumed")

    def commit(self, plan: dict[str, Any], approval: dict[str, Any], stop_after: str | None = None) -> dict[str, Any]:
        validate("plan.schema.json", plan)
        self.check_approval(plan, approval)
        txid = f"tx/fake-tool/{uuid.uuid4().hex[:12]}"
        instance_id = plan["target"]["instance_id"]
        source = Path(plan["source"]["locator"])
        timestamp = now()
        tx = {"schema_version": 1, "transaction_id": txid, "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"], "state": "PROPOSED", "root_instance_id": self.root_id, "machine_id": self.machine_id, "instance_id": instance_id, "generation_before": self.generation(), "generation_after": None, "created_at": timestamp, "updated_at": timestamp, "journal_seq": 0, "approval_id": approval["approval_id"], "approval_nonce": approval["nonce"], "failure": None}
        stage = self.tx_dir / txid.replace("/", "_") / "payload"
        try:
            self.write_tx(tx)
            self.event(txid, "PROPOSED", "accepted canonical plan")
            if tree_digest(source) != plan["source"]["integrity"]["artifact_digest"]:
                raise ValueError("DIGEST_MISMATCH: source changed after planning")
            tx["state"] = "APPROVED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "APPROVED", "test approval accepted")
            tx["state"] = "FETCHED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "FETCHED", "fixture read")
            if stop_after == "FETCHED": raise InterruptedError("fault injected after FETCHED")
            stage.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, stage, dirs_exist_ok=True)
            if tree_digest(stage) != plan["source"]["integrity"]["artifact_digest"]:
                raise ValueError("DIGEST_MISMATCH: stage changed")
            tx["state"] = "VERIFIED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "VERIFIED", "stage digest verified")
            tx["state"] = "STAGED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "STAGED", "stage manifest written")
            if stop_after == "STAGED": raise InterruptedError("fault injected after STAGED")
            store_path = self.store / instance_id
            store_path.parent.mkdir(parents=True, exist_ok=True)
            if store_path.exists():
                if tree_digest(store_path) != plan["source"]["integrity"]["artifact_digest"]:
                    raise ValueError("INSTANCE_CONFLICT: immutable store path has a different digest")
            else:
                os.replace(stage, store_path)
            tx["state"] = "COMMITTED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "COMMITTED", "immutable store committed")
            if stop_after == "COMMITTED": raise InterruptedError("fault injected after COMMITTED")
            instance = {"schema_version": 1, "tool_id": "fake-tool", "instance_id": instance_id, "kind": "managed_tool", "capability_id": "fake-tool", "version": plan["target"]["version"], "platform": "windows", "architecture": "x64", "artifact_digest": plan["source"]["integrity"]["artifact_digest"], "install_backend_id": "fake_fixture", "store_path": f"store/{instance_id}", "file_manifest_digest": plan["source"]["integrity"]["file_manifest_digest"], "lifecycle_status": "installed", "health": "healthy", "entrypoints": ["fake-tool.bin"], "bindings": [], "source": plan["source"], "created_at": now(), "retired_at": None, "file_manifest": file_manifest(store_path)}
            validate("managed-tool-instance.schema.json", instance)
            self.db.execute("INSERT OR REPLACE INTO instances(instance_id, payload_json, active) VALUES(?, ?, 0)", (instance_id, json.dumps(instance, sort_keys=True)))
            self.db.commit()
            tx["state"] = "REGISTERED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "REGISTERED", "instance registered inactive")
            if stop_after == "REGISTERED": raise InterruptedError("fault injected after REGISTERED")
            binding_key = plan["target"]["binding_key"]
            old = self.db.execute("SELECT instance_id, generation FROM bindings WHERE binding_key=?", (binding_key,)).fetchone()
            previous_id = old[0] if old else None
            new_generation = self.generation() + 1
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT OR REPLACE INTO bindings(binding_key, instance_id, generation) VALUES(?, ?, ?)", (binding_key, instance_id, new_generation))
            self.db.execute("UPDATE instances SET active=0 WHERE instance_id != ?", (instance_id,))
            self.db.execute("UPDATE instances SET active=1 WHERE instance_id=?", (instance_id,))
            self.db.execute("UPDATE meta SET value=? WHERE key='generation'", (str(new_generation),))
            self.db.commit()
            instance["lifecycle_status"] = "active"; instance["bindings"] = [{"binding_key": binding_key, "scope": "machine", "zone": "R", "active": True, "generation": new_generation, "exposure": "stable_launcher"}]
            self.db.execute("UPDATE instances SET payload_json=? WHERE instance_id=?", (json.dumps(instance, sort_keys=True), instance_id)); self.db.commit()
            tx["state"] = "ACTIVE_BOUND"; tx["generation_after"] = new_generation; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "ACTIVE_BOUND", "binding generation switched")
            if stop_after == "ACTIVE_BOUND": raise InterruptedError("fault injected after ACTIVE_BOUND")
            launcher = self.exposure / "fake-tool.json"
            launcher.write_text(json.dumps({"capability_id": "fake-tool", "instance_id": instance_id, "generation": new_generation, "target": f"../..\\store\\{instance_id}\\fake-tool.bin"}, indent=2, sort_keys=True), encoding="utf-8")
            tx["state"] = "EXPOSED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "EXPOSED", "stable launcher metadata written")
            if tree_digest(store_path) != plan["source"]["integrity"]["artifact_digest"] or not launcher.exists():
                raise ValueError("VERIFY_FAILED: exposure or payload verification failed")
            tx["state"] = "VERIFIED_AGAIN"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "VERIFIED_AGAIN", "post-bind verification passed")
            self.db.execute("INSERT INTO nonces(nonce, consumed_at) VALUES(?, ?)", (approval["nonce"], now()))
            self.db.commit()
            tx["state"] = "FINALIZED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "FINALIZED", "approval consumed and transaction finalized")
            return {"transaction_id": txid, "state": tx["state"], "instance_id": instance_id, "previous_instance_id": previous_id, "generation": new_generation}
        except InterruptedError:
            return {"transaction_id": txid, "state": tx["state"], "instance_id": instance_id, "interrupted": True}
        except Exception as exc:
            tx["state"] = "FAILED"; tx["failure"] = {"code": str(exc).split(":", 1)[0], "message": str(exc), "retryable": False, "evidence": []}; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "FAILED", str(exc))
            if stage.exists(): shutil.rmtree(stage.parent, ignore_errors=True)
            raise

    def recover(self, txid: str) -> dict[str, Any]:
        tx = json.loads(self.tx_path(txid).read_text(encoding="utf-8"))
        if tx["state"] != "ACTIVE_BOUND":
            return {"transaction_id": txid, "state": tx["state"], "action": "no_action"}
        launcher = self.exposure / "fake-tool.json"
        if not launcher.exists():
            raise RuntimeError("RECOVERY_REQUIRED: active binding has no exposure metadata")
        tx["state"] = "EXPOSED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "EXPOSED", "recovery observed exposure")
        tx["state"] = "VERIFIED_AGAIN"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "VERIFIED_AGAIN", "recovery verification passed")
        tx["state"] = "FINALIZED"; tx["journal_seq"] += 1; tx["updated_at"] = now(); self.write_tx(tx); self.event(txid, "FINALIZED", "recovery finalized")
        self.db.execute("INSERT OR IGNORE INTO nonces(nonce, consumed_at) VALUES(?, ?)", (tx["approval_nonce"], now()))
        self.db.commit()
        return {"transaction_id": txid, "state": tx["state"], "action": "finalized"}

    def close(self) -> None:
        self.db.close()


def ensure_fixture() -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    payload = FIXTURE_DIR / "fake-tool.bin"
    if not payload.exists():
        payload.write_bytes(b"AIROOT-FAKE-TOOL-V1\nThis payload is data and is never executed.\n")
    metadata = FIXTURE_DIR / "artifact.json"
    if not metadata.exists():
        metadata.write_text(json.dumps({"name": "fake-tool", "version": "1.0.0", "kind": "managed_tool", "test_only": True}, indent=2, sort_keys=True), encoding="utf-8")
    return FIXTURE_DIR


def run_demo() -> dict[str, Any]:
    original_path = os.environ.get("PATH")
    fixture = ensure_fixture()
    with tempfile.TemporaryDirectory(prefix="airoot-fake-") as temp:
        broker = FakeBroker(Path(temp))
        source = artifact_source(fixture)
        plan = make_plan(broker.root_id, broker.machine_id, source, "1.0.0", broker.generation())
        approval = make_approval(plan, broker.root_id, broker.machine_id)
        first = broker.commit(plan, approval)
        replay_error = None
        try:
            broker.commit(plan, approval)
        except ValueError as exc:
            replay_error = str(exc)
        if not replay_error or "APPROVAL_REPLAYED" not in replay_error:
            raise AssertionError("approval replay was not rejected")
        before_drift = tree_digest(fixture)
        drift_plan = make_plan(broker.root_id, broker.machine_id, source, "1.0.1", broker.generation())
        drift_approval = make_approval(drift_plan, broker.root_id, broker.machine_id)
        drift_file = fixture / "drift-marker.txt"
        drift_file.write_text("changed after plan", encoding="utf-8")
        try:
            broker.commit(drift_plan, drift_approval)
        except ValueError as exc:
            drift_error = str(exc)
        finally:
            drift_file.unlink()
        if "DIGEST_MISMATCH" not in drift_error:
            raise AssertionError("artifact drift was not rejected")
        second_plan = make_plan(broker.root_id, broker.machine_id, artifact_source(fixture), "1.1.0", broker.generation())
        second_approval = make_approval(second_plan, broker.root_id, broker.machine_id)
        interrupted = broker.commit(second_plan, second_approval, stop_after="ACTIVE_BOUND")
        recovered = broker.recover(interrupted["transaction_id"])
        if recovered["state"] != "FINALIZED":
            raise AssertionError("ACTIVE_BOUND recovery did not finalize")
        if os.environ.get("PATH") != original_path:
            raise AssertionError("fake slice changed PATH")
        broker.close()
        return {"status": "passed", "first": first, "replay_error": replay_error, "drift_before": before_drift, "drift_error": drift_error, "interrupted": interrupted, "recovered": recovered, "path_unchanged": True}


def validate_schemas() -> dict[str, Any]:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - environment setup error
        raise RuntimeError("jsonschema is required for schema validation") from exc
    schema_files = sorted(SCHEMA_DIR.glob("*.schema.json"))
    for schema_file in schema_files:
        jsonschema.Draft202012Validator.check_schema(json.loads(schema_file.read_text(encoding="utf-8")))
    fixture = ensure_fixture()
    source = artifact_source(fixture)
    with tempfile.TemporaryDirectory(prefix="airoot-schema-") as temp:
        broker = FakeBroker(Path(temp))
        plan = make_plan(broker.root_id, broker.machine_id, source, "1.0.0", broker.generation())
        approval = make_approval(plan, broker.root_id, broker.machine_id)
        validate("plan.schema.json", plan)
        validate("approval-token.schema.json", approval)
        broker.close()
    return {"status": "passed", "schema_count": len(schema_files), "fixture_validated": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="AIROOT test-only fake vertical slice")
    parser.add_argument("command", choices=["run", "test", "validate-schemas"])
    args = parser.parse_args()
    try:
        result = validate_schemas() if args.command == "validate-schemas" else run_demo()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
