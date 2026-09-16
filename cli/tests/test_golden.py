"""Golden fixtures must be reproducible by any implementation.

These files are the language-neutral acceptance corpus for the planned Rust port
(ADR-0001): if the Rust core produces the same documents and exit codes, the wire
contract is intact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot import schema_io
from golden import GOLDEN_DIR, build_documents, render

SCHEMA_FOR_FIXTURE = {
    "where_not_found": "where-response",
    "where_healthy": "where-response",
    "where_current_process_stale": "where-response",
    "where_version_unsatisfied": "where-response",
    "where_broken": "where-response",
    "where_unmanaged_only": "where-response",
    "where_owned_broken_degrades_to_reference": "where-response",
    "where_deprecated_external_fallback_is_ignored": "where-response",
    "doctor_healthy": "doctor-response",
    "doctor_degraded_stale_projection": "doctor-response",
    "doctor_broken_missing_payload": "doctor-response",
    "doctor_recovery_required": "doctor-response",
    "doctor_stale_search_index": "doctor-response",
    "inventory_machine": "registry-projection",
    "registry_with_data_root": "registry-projection",
    "extension_probe_envelope": "extension-envelope",
    "transaction_finalized": "transaction",
    "plan_fake_tool": "plan",
    "managed_tool_instance": "managed-tool-instance",
    "reference_plan": "reference-plan",
    "search_response": "search-response",
    "search_timeout_response": "search-response",
    "search_truncated_index_response": "search-response",
    "search_directories_response": "search-response",
    "search_physical_verify_response": "search-response",
    "search_index_response": "search-response",
    "search_stale_index_response": "search-response",
}


def _fixture_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def test_golden_directory_is_populated() -> None:
    assert GOLDEN_DIR.is_dir(), "run: python cli/tests/golden.py"
    assert _fixture_path("index").is_file()


def test_discover_report_fixture_has_the_steward_shape() -> None:
    """``discover`` has no published schema yet, so its shape is pinned here."""

    document = json.loads(_fixture_path("discover_report").read_text(encoding="utf-8"))
    assert document["data_root_id"] == "dr-env"
    assert document["counts"] == {"external_reference": 1}
    candidate = document["candidates"][0]
    assert candidate["management"] == "external_reference"
    assert candidate["capability_id"] == "python"
    assert candidate["version"] == "<VERSION>", "the host binary version is normalised away"
    assert candidate["probe_level"] == 2
    assert candidate["source_kind"] == "pe_static"
    assert candidate["external_id"] == "external/dr-env/python"


def test_golden_fixtures_reproduce_exactly(tmp_path: Path) -> None:
    """Byte for byte, not "the same document once parsed" (draft §84).

    `.gitattributes` and `AGENTS.md` §9 both call this corpus the **byte-for-byte** acceptance face
    for the port, and until §84 the check was `json.loads(committed) == generated` — a comparison two
    different byte sequences can win. Measured: `index.json` as CRLF is 840 bytes and as LF is 812,
    and both parse to the same value, so converting every fixture to LF would have left this test
    green while the corpus stopped matching what the port is supposed to reproduce.

    The parsed comparison is kept *after* the byte one, so a failure says what drifted and not merely
    that two blobs differ.
    """

    documents = build_documents(tmp_path)
    for name, payload in documents.items():
        path = _fixture_path(name)
        assert path.is_file(), f"missing golden fixture {name}; regenerate with python cli/tests/golden.py"
        assert path.read_bytes() == render(payload["document"]), (
            f"{name} drifted from the current core at the byte level; regenerate with "
            "python cli/tests/golden.py (and check that .gitattributes still says `* -text`)"
        )
        assert json.loads(path.read_text(encoding="utf-8")) == payload["document"]

    index = {name: payload["exit_code"] for name, payload in sorted(documents.items())}
    assert _fixture_path("index").read_bytes() == render(index)
    assert json.loads(_fixture_path("index").read_text(encoding="utf-8")) == index


def test_every_golden_document_satisfies_its_schema() -> None:
    for name, schema in SCHEMA_FOR_FIXTURE.items():
        document = json.loads(_fixture_path(name).read_text(encoding="utf-8"))
        schema_io.validate_document(schema, document)

    table = json.loads(_fixture_path("reason_code_table").read_text(encoding="utf-8"))
    from airoot.exits import REASON_EXIT

    assert table["reason_codes"] == {code: value for code, value in sorted(REASON_EXIT.items())}


def test_the_invariant_catalogue_fixture_is_the_whole_catalogue() -> None:
    """`doctor`'s D1-D10 grouping is contract, so the port gets it as an artifact (draft §46).

    A port could reproduce every diagnostic document and still group codes differently, which is
    what decides whether a finding reads as a D3 drift or a D7 staleness.
    """

    from airoot.caps.doctor import DIAGNOSTIC_CODES, INVARIANTS

    document = json.loads(_fixture_path("invariant_catalogue").read_text(encoding="utf-8"))
    assert document["invariants"] == {name: list(codes) for name, codes in sorted(INVARIANTS.items())}
    assert sorted(document["invariants"]) == sorted(f"D{index}" for index in range(1, 11))
    listed = [code for codes in document["invariants"].values() for code in codes]
    assert sorted(listed) == sorted(DIAGNOSTIC_CODES)
    assert len(listed) == len(set(listed)), "a code appears under two invariants"


def test_the_frozen_capability_fixture_is_the_whole_list() -> None:
    """The frozen capability list is contract too, and a port must reproduce it (draft §47)."""

    from airoot.caps.boundary import BINDING_SCOPES, load_capabilities

    frozen = load_capabilities()
    document = json.loads(_fixture_path("frozen_capabilities").read_text(encoding="utf-8"))
    assert document["revision"] == frozen.revision
    assert document["capabilities"] == [item.to_document() for item in frozen.capabilities]

    # Every declared scope is a binding scope: the fixture would otherwise pin a vocabulary the
    # machine can never be in.
    declared = {scope for item in document["capabilities"] for scope in item["scope"]}
    assert declared and declared <= BINDING_SCOPES
    assert all(item["kind"] in {"tool", "runtime"} for item in document["capabilities"])


@pytest.mark.parametrize(
    "name",
    [
        "where_healthy",
        "where_owned_broken_degrades_to_reference",
        "doctor_healthy",
        "doctor_broken_missing_payload",
        "doctor_recovery_required",
        "search_response",
        "search_index_response",
        "search_stale_index_response",
        "doctor_stale_search_index",
    ],
)
def test_golden_exit_codes_are_stable(name: str) -> None:
    index = json.loads(_fixture_path("index").read_text(encoding="utf-8"))
    documented = {
        "where_healthy": 0,
        # Steward-first (draft §6): a broken owned payload degrading to a healthy reference is
        # "degraded or drift" (2), not "broken" (3).
        "where_owned_broken_degrades_to_reference": 2,
        "doctor_healthy": 0,
        "doctor_broken_missing_payload": 3,
        "doctor_recovery_required": 6,
        # A crawl answer is degraded by definition: it is not an index answer (ADR-0017).
        "search_response": 2,
        # An index answer can be healthy (0); an index that is too old for the caller's
        # `max_staleness_ms` is degraded (2) with SEARCH_RESULT_STALE (draft §32).
        "search_index_response": 0,
        "search_stale_index_response": 2,
        # ...and `doctor` reports the same staleness under D7 (draft §33).
        "doctor_stale_search_index": 2,
    }
    assert index[name] == documented[name]
