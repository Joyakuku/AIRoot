"""L1: the Capability Extension protocol (manifest, envelope, dispatch).

Covers verification plan C-001 (envelope fields are mandatory; no search-specific
top-level fallbacks) and the exit-code-9 paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot import schema_io
from airoot.cli import main
from airoot.exits import AirootError, exit_code_for
from airoot.ext import FakeExtension, load_manifest, load_manifests, register_manifests
from airoot.ext.envelope import envelope, failure_envelope
from airoot.ext.fake import load_fake_extension
from airoot.ext.hosts import hosted_here
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


# --------------------------------------------------------------------------- #
# the CLI must answer with *that* extension's facts, not the fake host's
# --------------------------------------------------------------------------- #

#: A bundled manifest this build cannot run: `file_search` is `caps/search.py` here, not a host.
SEARCH_EXTENSION = "airoot-native-search-extension"
SEARCH_MANIFEST = Path(__file__).resolve().parents[1] / "extensions" / f"{SEARCH_EXTENSION}.json"

#: `ext/fake.py`'s hardcoded self-test word, verbatim. It belongs to `airoot-fake-extension` and
#: must never show up in the answer about another extension — that is the whole defect.
FAKE_SELF_TEST_WORD = "deterministic fake extension; no external state touched"


def run_cli(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


def test_the_host_criterion_is_the_implementation_id_not_the_extension_name() -> None:
    """The decision must survive a rename, so it cannot be a lookup on ``extension_id``."""

    fake = load_manifest(MANIFEST)
    assert hosted_here(fake) is True
    assert hosted_here(dict(fake, extension_id="airoot-renamed-fake")) is True, (
        "a rename does not change which implementation would run"
    )

    search = load_manifest(SEARCH_MANIFEST)
    assert search["implementation_id"] == "airoot-native-search-crawl"
    assert hosted_here(search) is False


def test_extension_status_does_not_answer_for_an_extension_this_build_cannot_host(capsys, cli_root) -> None:
    """F2: ``extension status <id>`` used to run *every* manifest through the fake host.

    The envelope carried ``extension_id=airoot-native-search-extension`` while its ``data`` and
    ``evidence`` were ``ext/fake.py``'s hardcoded self-test — a health report about a different
    implementation, delivered under this id.
    """

    code, document = run_cli(capsys, "--json", "--root", str(cli_root), "extension", "status", SEARCH_EXTENSION)

    assert not (code == 0 and document.get("status") == "ok"), "a manifest nobody here can run is not healthy"
    assert code == 9
    assert document["reason_code"] == "EXTENSION_UNAVAILABLE"

    rendered = json.dumps(document)
    assert FAKE_SELF_TEST_WORD not in rendered, "the fake host's self-test word is not this extension's answer"
    assert "self_test" not in rendered, "`ext/fake.py`'s check list is not this extension's check list"

    assert "cli/app/airoot/caps/search.py" in rendered, "the evidence must name where file_search really lives"
    assert "search implementations" in rendered and "search status" in rendered
    assert "deterministic fake extension" in document["message"], (
        "the message must name the only extension host this build has"
    )


def test_extension_status_still_runs_the_hosted_extension(capsys, cli_root) -> None:
    """The other half: the fix must not turn the one runnable extension into an error."""

    code, document = run_cli(capsys, "--json", "--root", str(cli_root), "extension", "status", "airoot-fake-extension")
    assert code == 0
    assert document["status"] == "ok"
    assert document["extension_id"] == "airoot-fake-extension"
    assert document["data"]["self_test"]["passed"] is True
    assert document["evidence"] == [{"kind": "self_test", "detail": FAKE_SELF_TEST_WORD}]


def test_extension_list_still_declares_the_extension_this_build_cannot_host(capsys, cli_root) -> None:
    """Unavailable is not erased: the catalog still declares it (the fix did not touch ``list``)."""

    code, document = run_cli(capsys, "--json", "--root", str(cli_root), "extension", "list")
    assert code == 0
    assert [item["extension_id"] for item in document["extensions"]] == [
        "airoot-fake-extension",
        SEARCH_EXTENSION,
    ]
    assert [item["implementation_id"] for item in document["extensions"]] == [
        "airoot-fake-deterministic",
        "airoot-native-search-crawl",
    ]
