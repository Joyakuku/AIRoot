"""CLI contract for `search` (draft §31, ADR-0017).

The module-level contract is covered in `test_l1_search.py`. What is pinned *here* is the part only
the CLI can get wrong:

* the reserved words (`status`/`explain`/`implementations`/`refresh`/`rebuild`) are reserved **only**
  as the first token, so `airoot search --query status` still searches for a file;
* the answer an agent gets from the command line carries the fallback confession in all four places;
* `search` invents no roots, writes nothing, and does not quietly widen its bounds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.cli import main
from airoot.paths import volume_serial
from airoot.registry import ExternalReference
from airoot.registry.entities import DataRoot
from airoot.schema_io import errors_for


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def data_root(tests_tmp: Path) -> Path:
    """A data root with one adopted object and one loose file."""

    path = tests_tmp / "cli-search-data-root"
    (path / "java" / "bin").mkdir(parents=True, exist_ok=True)
    (path / "java" / "bin" / "java.exe").write_bytes(b"MZ-placeholder\n")
    (path / "loose.exe").write_bytes(b"MZ-placeholder\n")
    return path


@pytest.fixture
def registered(registry, data_root: Path) -> Path:
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-search",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-3",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-search/java",
                capability_id="java",
                path=str(data_root / "java"),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-search",
                version="25.0.2.0",
                architecture="x64",
                entrypoints=("bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )
    return data_root


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip().startswith("{") else {"raw": captured.out}
    return code, document


def search(capsys, cli_root: Path, *argv: str) -> tuple[int, dict]:
    return run(capsys, "--json", "--root", str(cli_root), "search", *argv)


# --------------------------------------------------------------------------- #
# the search itself
# --------------------------------------------------------------------------- #


def test_search_reports_the_fallback_in_all_four_places(capsys, cli_root: Path, registered: Path) -> None:
    code, document = search(capsys, cli_root, "java", "--ext", ".exe")

    assert code == 2
    assert document["status"] == "degraded"
    assert document["reason_code"] == "SEARCH_FALLBACK_USED"
    assert document["data"]["fallback"]["kind"] == "crawl"
    assert document["data"]["freshness"]["state"] == "unknown"
    assert document["data"]["freshness"]["coverage"] == "none"
    assert document["data"]["stats"]["returned"] == 1
    assert errors_for("search-response", document) == []


def test_search_uses_the_registered_data_roots_by_default(capsys, cli_root: Path, registered: Path) -> None:
    code, document = search(capsys, cli_root, "java")

    assert code == 2
    assert any(registered.name in item["path"] for item in document["data"]["results"])
    assert any("roots came from the policy" in json.dumps(item) for item in document["evidence"])


def test_without_a_data_root_search_refuses_instead_of_scanning_everything(capsys, cli_root: Path) -> None:
    code, document = search(capsys, cli_root, "java")

    assert code == 2
    assert document["reason_code"] == "SEARCH_ROOT_UNAVAILABLE"
    assert any("data-root add" in item for item in document["evidence"])


def test_an_explicit_search_root_wins_over_the_data_roots(
    capsys, cli_root: Path, registered: Path, tests_tmp: Path
) -> None:
    other = tests_tmp / "cli-search-other-root"
    other.mkdir(parents=True, exist_ok=True)
    (other / "java.exe").write_bytes(b"MZ")

    code, document = search(capsys, cli_root, "java", "--search-root", str(other))

    assert code == 2
    assert [Path(item["path"]).parent.name for item in document["data"]["results"]] == [other.name]
    assert any("roots came from the request" in json.dumps(item) for item in document["evidence"])


def test_a_limit_above_the_policy_bound_is_refused(capsys, cli_root: Path, registered: Path) -> None:
    code, document = search(capsys, cli_root, "java", "--limit", "9999")

    assert code == 8
    assert document["reason_code"] == "EXTENSION_INPUT_INVALID"


def test_no_matches_is_still_a_degraded_answer(capsys, cli_root: Path, registered: Path) -> None:
    code, document = search(capsys, cli_root, "definitely-not-here")

    assert code == 2
    assert document["data"]["results"] == []
    assert document["data"]["stats"]["matched"] == 0


def test_results_are_tagged_from_declared_state(capsys, cli_root: Path, registered: Path) -> None:
    code, document = search(capsys, cli_root, "java", "--ext", ".exe")

    assert code == 2
    tagged = [item for item in document["data"]["results"] if item["management"] == "external_reference"]
    assert tagged, "the adopted reference's payload must be recognised"
    assert {item["capability_id"] for item in tagged} == {"java"}


def test_managed_only_hides_unmanaged_matches_and_says_so(capsys, cli_root: Path, registered: Path) -> None:
    everything = search(capsys, cli_root, "exe", "--ext", ".exe")[1]
    managed = search(capsys, cli_root, "exe", "--ext", ".exe", "--managed-only")[1]

    assert everything["data"]["stats"]["matched"] == 2  # java.exe and loose.exe
    assert managed["data"]["stats"]["matched"] == 1
    assert all(item["management"] != "unmanaged" for item in managed["data"]["results"])
    assert any("hidden by the caller's filter" in warning for warning in managed["warnings"])


def test_paging_through_the_cli_is_bound_to_the_query(capsys, cli_root: Path, registered: Path) -> None:
    first = search(capsys, cli_root, "exe", "--ext", ".exe", "--limit", "1")[1]
    cursor = first["data"]["next_cursor"]
    assert cursor

    second = search(capsys, cli_root, "exe", "--ext", ".exe", "--limit", "1", "--cursor", cursor)[1]
    assert first["data"]["results"][0]["path"] != second["data"]["results"][0]["path"]

    mismatched = search(capsys, cli_root, "java", "--ext", ".exe", "--cursor", cursor)
    assert mismatched[0] == 8
    assert mismatched[1]["reason_code"] == "SEARCH_CURSOR_INVALID"


def test_search_writes_no_index_and_no_cache(capsys, cli_root: Path, registered: Path) -> None:
    search(capsys, cli_root, "java")

    assert not (cli_root / "cache" / "search").exists()


# --------------------------------------------------------------------------- #
# reserved words and the parser rule
# --------------------------------------------------------------------------- #


def test_reserved_words_are_reserved_only_as_the_first_token(
    capsys, cli_root: Path, registered: Path
) -> None:
    (registered / "status").write_text("a file named status", encoding="utf-8")

    code, document = search(capsys, cli_root, "status")
    assert document["operation"] == "status"
    assert code == 9  # SEARCH_NOT_READY: the index implementation cannot be invoked

    code, document = search(capsys, cli_root, "--query", "status")
    assert code == 2
    assert [Path(item["path"]).name for item in document["data"]["results"]] == ["status"]


def test_search_status_reports_no_index_as_unknown_and_not_as_stale(
    capsys, cli_root: Path, registered: Path
) -> None:
    code, document = search(capsys, cli_root, "status")

    assert code == 9  # SEARCH_NOT_READY: the index implementation cannot be invoked yet
    assert document["reason_code"] == "SEARCH_NOT_READY"
    assert document["freshness"] == {
        "state": "unknown",
        "last_indexed_at": None,
        "lag_ms": None,
        "coverage": "none",
    }
    assert document["index"]["present"] is False
    assert document["implementation_id"] == "airoot-native-search-crawl"
    # The native candidate is always named; its probe is only run when explicitly asked for (§34).
    assert document["native_index"]["available"] is False
    assert document["native_index"]["checked"] is False
    assert "probe" not in document["native_index"]


def test_probing_the_native_index_is_explicit_and_never_raises(capsys, cli_root: Path, registered: Path) -> None:
    """The only path that opens a volume handle, and a failure there is data rather than a crash."""

    code, document = search(capsys, cli_root, "status", "--probe-native-index")

    assert code in (0, 2, 9)
    probe = document["native_index"]["probe"]
    assert document["native_index"]["checked"] is True
    assert probe["checked"] is True
    assert probe["volume"]
    assert isinstance(probe["observations"], list)
    # Whatever the machine says, the report must not collapse it into an unverified verdict.
    assert probe["elevated"] in (True, False, None)
    assert probe["reason"]


def test_search_explain_names_the_crawl_and_the_missing_index(
    capsys, cli_root: Path, registered: Path
) -> None:
    code, document = search(capsys, cli_root, "explain", "java")

    assert code == 2
    assert document["reason_code"] == "SEARCH_FALLBACK_USED"
    assert document["selected"] == "airoot-native-search-crawl"
    assert document["query"] == "java"
    assert document["fallback"] == {"used": True, "kind": "crawl"}
    assert "index" in document["reason"]


def test_search_implementations_lists_the_declared_one_and_the_unavailable_candidate(
    capsys, cli_root: Path
) -> None:
    """Draft §34: the protocol's target implementation is named even though it cannot be built here."""

    code, document = search(capsys, cli_root, "implementations")

    assert code == 0
    by_id = {item["implementation_id"]: item for item in document["implementations"]}
    assert set(by_id) == {"airoot-native-search-crawl", "airoot-native-search-native-index"}
    assert by_id["airoot-native-search-crawl"]["implementation_kind"] == "fallback"
    assert by_id["airoot-native-search-crawl"]["available"] is True
    native = by_id["airoot-native-search-native-index"]
    assert native["implementation_kind"] == "native"
    assert native["available"] is False
    assert native["reason_code"] == "SEARCH_BACKEND_UNAVAILABLE"
    assert "privileged" in native["reason"]


def test_refresh_builds_the_index_and_then_search_answers_from_it(
    capsys, cli_root: Path, registered: Path
) -> None:
    """Draft §32.4-2: after a refresh the answer is an *index* answer — exit code 0 for the first time."""

    code, document = search(capsys, cli_root, "refresh")
    assert code == 0
    assert document["operation"] == "refresh"
    assert document["records"] > 0
    assert document["coverage"] == "complete_for_roots"
    assert document["index_replaced"] is True
    assert document["data_root_files_touched"] == 0, "no file inside a data root may be touched"

    cache = cli_root / "cache" / "search"
    assert sorted(path.name for path in cache.iterdir()) == ["index.db"], "no -wal/-shm/*.tmp leftovers"

    code, document = search(capsys, cli_root, "java", "--ext", ".exe")
    assert code == 0, "an index answer is a healthy answer"
    assert document["status"] == "ok"
    assert document["reason_code"] is None
    assert document["data"]["fallback"] is None
    assert document["data"]["freshness"]["state"] == "current"
    assert document["data"]["freshness"]["coverage"] == "complete_for_roots"
    assert document["data"]["freshness"]["last_indexed_at"]
    assert {item["verification"] for item in document["data"]["results"]} == {"indexed"}
    assert any("index (" in json.dumps(item.get("detail")) for item in document["evidence"])

    status_code, status = search(capsys, cli_root, "status")
    assert status_code == 0
    assert status["reason_code"] == "SUCCESS"
    assert status["index"]["readable"] is True
    assert status["index"]["records"] == document["data"]["stats"]["index_records_examined"]

    explain_code, explain = search(capsys, cli_root, "explain", "java")
    assert explain_code == 0
    assert explain["would_answer_from"] == "index"
    assert explain["fallback"] == {"used": False, "kind": None}


def test_rebuild_is_the_protocol_spelling_of_a_full_refresh(capsys, cli_root: Path, registered: Path) -> None:
    first_code, first = search(capsys, cli_root, "refresh")
    # A change on disk is the only honest way to see a rebuild: the crawl index has no incremental
    # mode, so a rebuild must notice the new file.
    (registered / "added-after-the-first-build.exe").write_bytes(b"MZ")

    second_code, second = search(capsys, cli_root, "rebuild")

    assert first_code == second_code == 0
    assert second["records"] == first["records"] + 1
    assert second["index_replaced"] is True


def test_a_corrupt_index_falls_back_to_a_crawl_and_says_so(
    capsys, cli_root: Path, registered: Path
) -> None:
    search(capsys, cli_root, "refresh")
    (cli_root / "cache" / "search" / "index.db").write_bytes(b"not a database" * 32)

    code, document = search(capsys, cli_root, "java")
    assert code == 2
    assert document["reason_code"] == "SEARCH_FALLBACK_USED"
    assert document["data"]["fallback"]["kind"] == "crawl"
    assert any("unusable" in warning for warning in document["warnings"])

    status_code, status = search(capsys, cli_root, "status")
    assert status_code == 2
    assert status["reason_code"] == "SEARCH_INDEX_DEGRADED"
    assert status["index"]["problem"]


def test_refresh_without_a_root_to_walk_is_refused(capsys, cli_root: Path) -> None:
    code, document = search(capsys, cli_root, "refresh")

    assert code == 2
    assert document["reason_code"] == "SEARCH_ROOT_UNAVAILABLE"


def test_search_needs_a_query(capsys, cli_root: Path) -> None:
    code, document = search(capsys, cli_root)

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert any("--query status" in item for item in document["evidence"])


def test_two_positional_tokens_are_refused(capsys, cli_root: Path) -> None:
    code, document = search(capsys, cli_root, "java", "python")

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_query_given_twice_is_refused(capsys, cli_root: Path) -> None:
    code, document = search(capsys, cli_root, "java", "--query", "python")

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
