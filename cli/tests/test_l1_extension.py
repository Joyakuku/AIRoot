"""L1: the Capability Extension protocol (manifest, envelope, dispatch).

Covers verification plan C-001 (envelope fields are mandatory; no search-specific
top-level fallbacks) and the exit-code-9 paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot import schema_io
from airoot.exits import AirootError, exit_code_for
from airoot.ext import FakeExtension, load_manifest, load_manifests, register_manifests
from airoot.ext.envelope import envelope, failure_envelope
from airoot.ext.fake import load_fake_extension
from airoot.ext.manifest import declared_operations, operation_policy

MANIFEST = Path(__file__).resolve().parents[1] / "extensions" / "airoot-fake-extension.json"


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


def test_bundled_manifest_satisfies_the_published_schema() -> None:
    manifest = load_manifest(MANIFEST)
    schema_io.validate_document("extension-manifest", manifest)
    assert manifest["extension_id"] == "airoot-fake-extension"
    assert manifest["capability_types"] == ["fake-echo"]
    assert manifest["executes_scripts"] is False
    assert manifest["network_access"] is False


def test_documented_operation_policies_spelling_is_rejected() -> None:
    """The planning doc says ``operation_policies``; the schema requires ``operations``."""

    manifest = load_manifest(MANIFEST)
    moved = dict(manifest)
    moved["operation_policies"] = moved.pop("operations")
    with pytest.raises(AirootError) as err:
        schema_io.validate_document("extension-manifest", moved, reason_code="EXTENSION_MANIFEST_INVALID")
    assert err.value.reason_code == "EXTENSION_MANIFEST_INVALID"

    unknown_enum = dict(manifest)
    unknown_enum["operations"] = {
        "probe": dict(manifest["operations"]["probe"], overwrite_policy="replace_derived_cache")
    }
    with pytest.raises(AirootError):
        schema_io.validate_document("extension-manifest", unknown_enum, reason_code="EXTENSION_MANIFEST_INVALID")


def test_manifest_missing_a_required_field_is_refused(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    broken = {key: value for key, value in manifest.items() if key != "side_effects"}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(AirootError) as err:
        load_manifest(path)
    assert err.value.reason_code == "EXTENSION_MANIFEST_INVALID"
    assert err.value.exit_code == 9


def test_unsupported_protocol_version_is_refused(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    manifest["protocol_version"] = 2
    path = tmp_path / "future.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AirootError) as err:
        load_manifest(path)
    assert err.value.reason_code in {"EXTENSION_MANIFEST_INVALID", "EXTENSION_VERSION_UNSUPPORTED"}


def test_unreadable_manifest_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "garbage.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AirootError) as err:
        load_manifest(path)
    assert err.value.reason_code == "EXTENSION_MANIFEST_INVALID"


def test_missing_manifest_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as err:
        load_manifest(tmp_path / "absent.json")
    assert err.value.reason_code == "EXTENSION_UNAVAILABLE"


def test_duplicate_extension_ids_are_refused(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    (tmp_path / "a.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AirootError) as err:
        load_manifests(tmp_path)
    assert err.value.reason_code == "EXTENSION_MANIFEST_INVALID"


def test_declared_operations_are_the_policy_map() -> None:
    manifest = load_manifest(MANIFEST)
    assert declared_operations(manifest) == {"probe", "status", "invoke"}
    policy = operation_policy(manifest, "invoke")
    assert policy["operation_kind"] == "read"
    assert policy["approval_required"] is False
    with pytest.raises(AirootError) as err:
        operation_policy(manifest, "install")
    assert err.value.reason_code == "EXTENSION_OPERATION_UNKNOWN"


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #


def test_envelope_uses_the_common_shape(clock) -> None:
    document = envelope("airoot-fake-extension", "probe", data={"available": True}, clock=clock)
    schema_io.validate_document("extension-envelope", document)
    assert set(document) >= {"schema_version", "extension_id", "operation", "status", "timing", "data", "warnings", "evidence", "reason_code"}
    assert document["timing"]["elapsed_ms"] >= 0
    assert document["status"] == "ok"


def test_envelope_requires_the_contract_fields(clock) -> None:
    """C-001: a response missing extension_id/data/reason_code must be rejected."""

    incomplete = {
        "schema_version": 1,
        "operation": "search",
        "status": "ok",
        "timing": {"started_at": "2024-01-01T00:00:00Z", "finished_at": "2024-01-01T00:00:00Z", "elapsed_ms": 0},
        "warnings": [],
        "evidence": [],
    }
    with pytest.raises(AirootError) as err:
        schema_io.validate_document("extension-envelope", incomplete)
    assert "extension_id" in " ".join(err.value.evidence)


def test_search_specific_top_level_fields_are_rejected(clock) -> None:
    """C-002: profile results go in ``data``; unknown top-level keys are refused."""

    document = envelope("airoot-fake-extension", "probe", data={}, clock=clock)
    with_results = dict(document, results=[], provider_id="everything")
    with pytest.raises(AirootError):
        schema_io.validate_document("extension-envelope", with_results)


def test_envelope_declares_policy_only_enforcement(clock) -> None:
    document = envelope("airoot-fake-extension", "probe", data={}, clock=clock)
    assert document["security_mode"] == "policy_only"
    assert document["enforcement"] == "same_user_can_bypass"


def test_failure_envelope_carries_a_stable_code(clock) -> None:
    document = failure_envelope(
        "airoot-fake-extension", "probe", clock=clock, reason_code="EXTENSION_UNAVAILABLE"
    )
    schema_io.validate_document("extension-envelope", document)
    assert document["status"] == "error"
    assert exit_code_for(document["reason_code"]) == 9


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


def test_fake_extension_probe_reports_its_declarations(clock) -> None:
    extension = load_fake_extension(clock=clock)
    document = extension.run("probe")
    schema_io.validate_document("extension-envelope", document)
    assert document["extension_id"] == "airoot-fake-extension"
    assert document["data"]["capability_types"] == ["fake-echo"]
    assert document["data"]["available"] is True


def test_fake_extension_status_runs_a_self_test(clock) -> None:
    extension = load_fake_extension(clock=clock)
    document = extension.run("status")
    assert document["data"]["health"] == "healthy"
    assert document["data"]["self_test"]["passed"] is True
    assert document["data"]["operations"] == ["invoke", "probe", "status"]


def test_invoke_echoes_without_side_effects(clock) -> None:
    extension = load_fake_extension(clock=clock)
    document = extension.run("invoke", echo={"hello": "airoot"})
    assert document["data"]["echo"] == {"hello": "airoot"}
    assert document["data"]["side_effects"] == ["none"]


def test_undeclared_operation_is_refused(clock) -> None:
    extension = load_fake_extension(clock=clock)
    with pytest.raises(AirootError) as err:
        extension.run("delete_everything")
    assert err.value.reason_code == "EXTENSION_OPERATION_UNKNOWN"
    assert err.value.exit_code == 9


def test_extension_is_deterministic(clock) -> None:
    def payload(document: dict) -> str:
        without_timing = {key: value for key, value in document.items() if key != "timing"}
        return json.dumps(without_timing, sort_keys=True)

    first = FakeExtension(load_manifest(MANIFEST), clock=clock).run("probe")
    second = FakeExtension(load_manifest(MANIFEST), clock=clock).run("probe")
    assert payload(first) == payload(second), "only the timing block may differ between calls"


def test_extension_does_not_touch_the_registry(registry, clock) -> None:
    extension = FakeExtension(load_manifest(MANIFEST), clock=clock)
    before = registry.declared_state_digest()
    extension.run("probe")
    extension.run("status")
    extension.run("invoke", echo="x")
    assert registry.declared_state_digest() == before


def test_manifests_are_registered_as_declared_state(registry, monkeypatch) -> None:
    manifests = load_manifests(MANIFEST.parent)
    registered = register_manifests(registry, manifests)
    # Exactly the bundled set: adding an extension must be a deliberate change here (draft §31).
    assert registered == ["airoot-fake-extension", "airoot-native-search-extension"]

    row = registry.extension("airoot-fake-extension")
    assert row is not None
    assert row["protocol_version"] == 1
    assert json.loads(row["manifest_json"])["capability_types"] == ["fake-echo"]
    assert registry.event_count() == 1
