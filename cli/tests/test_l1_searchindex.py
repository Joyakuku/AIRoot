"""L1: the durable search index (draft §32, ADR-0019).

The index is derived state that lives inside the AIROOT root, so the tests here are about bounds and
honesty rather than speed:

* it stores an **unfiltered superset** and applies the request's filters at query time through the
  same function the crawl uses — otherwise the same query would answer differently depending on
  whether an index happened to exist;
* it is replaced **atomically**, leaves no `-wal`/`-shm`/`*.tmp` behind, and never writes outside
  `cache/search`;
* a corrupt index is a **diagnosis** (`SEARCH_INDEX_DEGRADED`) that falls back to a live crawl, and a
  failed build leaves the previous index queryable;
* `freshness` carries real values, and `stale` means "older than the caller asked for".
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from airoot.caps import searchindex as si
from airoot.caps.search import SearchPolicy, build_request, crawl, load_search_policy
from airoot.clock import FakeClock, parse_timestamp
from datetime import timedelta


@pytest.fixture
def tree(tests_tmp: Path) -> Path:
    root = tests_tmp / "index-tree"
    for relative in ("apps", "apps/deep", "uv-cache"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "apps" / "python.exe").write_bytes(b"MZ" + b"\0" * 32)
    (root / "apps" / "python.txt").write_text("notes", encoding="utf-8")
    (root / "apps" / "deep" / "python.exe").write_bytes(b"MZ")
    (root / "uv-cache" / "python.exe").write_bytes(b"MZ")
    return root


@pytest.fixture
def airoot(tests_tmp: Path, request: pytest.FixtureRequest) -> Path:
    """One AIROOT root per test: these tests replace an index in place, so they must not share one."""

    path = tests_tmp / "index-airoot" / request.node.name.replace("[", "_").replace("]", "")
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_building_stores_a_superset_and_leaves_no_side_files(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    build = si.build_index(airoot, roots=[str(tree)], policy=policy)

    # Every file, every directory, and no cache directory: apps, apps/deep, apps/deep/python.exe,
    # apps/python.exe, apps/python.txt — `uv-cache` is structurally excluded (derived data).
    assert build.records == 5
    state = si.read_state(airoot, policy=policy)
    assert state.readable and state.records == build.records
    assert state.coverage == "complete_for_roots"

    cache = airoot / "cache" / "search"
    assert sorted(path.name for path in cache.iterdir()) == ["index.db"], "no -wal/-shm/*.tmp leftovers"


def test_the_index_writes_nothing_outside_its_cache_directory(airoot: Path, tree: Path) -> None:
    before = sorted(str(path.relative_to(tree)) for path in tree.rglob("*"))
    si.build_index(airoot, roots=[str(tree)], policy=load_search_policy())
    after = sorted(str(path.relative_to(tree)) for path in tree.rglob("*"))

    assert before == after, "building an index must not touch the data root"
    written = sorted(str(path.relative_to(airoot)) for path in airoot.rglob("*") if path.is_file())
    assert written == [str(Path("cache/search/index.db"))]


def test_query_applies_the_same_filters_as_the_crawl(airoot: Path, tree: Path) -> None:
    """The anti-drift test: index answers and crawl answers must agree on *which* records match."""

    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    state = si.read_state(airoot, policy=policy)

    cases = [
        {"query": "python", "extensions": [".exe"]},
        {"query": "python", "include_directories": True},
        {"query": "PYTHON.EXE", "match": "exact", "target": "name"},
        {"query": "apps", "target": "path"},
        {"query": "notes", "extensions": [".txt"]},
        {"query": "python", "min_size": 10},
        {"query": "nothing-matches-this"},
    ]
    for case in cases:
        request = build_request(policy=policy, roots=[str(tree)], **case)
        from_index = si.query_index(airoot, request=request, roots=[str(tree)], policy=policy, state=state)
        from_crawl = crawl(request, [str(tree)], policy=policy)
        assert from_index is not None
        assert [item["path"] for item in from_index] == [item["path"] for item in from_crawl.records], case


def test_a_request_outside_the_indexed_roots_is_not_answered_by_the_index(
    airoot: Path, tree: Path, tests_tmp: Path
) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    state = si.read_state(airoot, policy=policy)
    other = tests_tmp / "index-other-root"
    other.mkdir(parents=True, exist_ok=True)
    request = build_request("python", policy=policy, roots=[str(other)])

    assert state.covers([str(tree / "apps")]), "a nested root is covered"
    assert not state.covers([str(other)])
    assert si.query_index(airoot, request=request, roots=[str(other)], policy=policy, state=state) is None


def test_a_corrupt_index_is_a_diagnosis_not_a_crash(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    target = si.index_path(airoot, policy)
    target.write_bytes(b"this is not a database" * 64)

    state = si.read_state(airoot, policy=policy)
    assert state.present is True
    assert state.readable is False
    assert state.problem
    request = build_request("python", policy=policy, roots=[str(tree)])
    assert si.query_index(airoot, request=request, roots=[str(tree)], policy=policy, state=state) is None


def test_a_schema_mismatch_is_reported_rather_than_read(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    target = si.index_path(airoot, policy)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        connection.commit()

    state = si.read_state(airoot, policy=policy)
    assert state.readable is False
    assert "schema version" in str(state.problem)


def test_freshness_is_current_then_stale(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    clock = FakeClock(start="2024-01-01T00:00:00Z", step=timedelta(seconds=0))
    si.build_index(airoot, roots=[str(tree)], policy=policy, clock=clock)
    state = si.read_state(airoot, policy=policy)
    request = build_request("python", policy=policy, roots=[str(tree)], max_staleness_ms=10_000)

    fresh, code = si.freshness_for(state, request, clock=clock)
    assert fresh == {
        "state": "current",
        "last_indexed_at": state.built_at,
        "lag_ms": 0,
        "coverage": "complete_for_roots",
    }
    assert code is None

    clock.advance(timedelta(seconds=30))
    stale, code = si.freshness_for(state, request, clock=clock)
    assert stale["state"] == "stale"
    assert stale["lag_ms"] == 30_000
    assert code == "SEARCH_RESULT_STALE"


def test_an_absent_index_has_no_freshness_to_report(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    state = si.read_state(airoot, policy=policy)
    request = build_request("python", policy=policy, roots=[str(tree)])

    fresh, code = si.freshness_for(state, request)

    assert fresh == {"state": "unknown", "last_indexed_at": None, "lag_ms": None, "coverage": "none"}
    assert code is None
    assert state.generation == "no-index"


def test_the_generation_changes_when_the_index_is_rebuilt(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    first = si.read_state(airoot, policy=policy).generation
    (tree / "apps" / "python3.exe").write_bytes(b"MZ")
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    second = si.read_state(airoot, policy=policy).generation

    assert first != second, "a rebuild must invalidate cursors minted against the old index"


def test_a_failed_build_leaves_the_previous_index_queryable(
    airoot: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    state = si.read_state(airoot, policy=policy)

    def explode(*_args, **_kwargs):
        raise OSError("simulated failure while replacing the index")

    monkeypatch.setattr(si.os, "replace", explode)
    with pytest.raises(OSError):
        si.build_index(airoot, roots=[str(tree)], policy=policy)

    assert si.read_state(airoot, policy=policy).records == state.records
    request = build_request("python", policy=policy, roots=[str(tree)], extensions=[".exe"])
    assert si.query_index(airoot, request=request, roots=[str(tree)], policy=policy) is not None
    cache = airoot / "cache" / "search"
    assert sorted(path.name for path in cache.iterdir()) == ["index.db"], "the temp file must be cleaned up"


def test_a_truncated_build_reports_partial_coverage(airoot: Path, tree: Path) -> None:
    policy = replace(load_search_policy(), index={"path": si.DEFAULT_INDEX_PATH, "max_records": 2})
    build = si.build_index(airoot, roots=[str(tree)], policy=policy)

    assert build.truncated is True
    assert build.coverage == "partial"
    assert si.read_state(airoot, policy=policy).coverage == "partial"


def test_accessibility_is_not_stored_in_the_index(airoot: Path, tree: Path) -> None:
    """It is a property of the caller, so it must be recomputed per answer (draft §32.2)."""

    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    with closing(sqlite3.connect(si.index_path(airoot, policy))) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(entries)")}

    assert "accessible" not in columns
    assert {"path", "name", "kind", "size", "modified_at", "attributes"} <= columns


def test_query_recomputes_accessibility_for_every_answer(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    state = si.read_state(airoot, policy=policy)
    request = build_request("python", policy=policy, roots=[str(tree)], extensions=[".exe"])

    rows = si.query_index(airoot, request=request, roots=[str(tree)], policy=policy, state=state)

    assert rows
    assert all(item["accessible"] is True for item in rows), "these files are readable right now"
    assert {item["verification"] for item in rows} == {"indexed"}


def test_the_index_never_contains_a_cache_directory(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    state = si.read_state(airoot, policy=policy)
    request = build_request("python", policy=policy, roots=[str(tree)], include_directories=True)

    rows = si.query_index(airoot, request=request, roots=[str(tree)], policy=policy, state=state)

    assert rows
    assert not any("uv-cache" in item["path"] for item in rows)


def test_index_meta_records_what_it_was_built_from(airoot: Path, tree: Path) -> None:
    policy = load_search_policy()
    si.build_index(airoot, roots=[str(tree)], policy=policy)
    with closing(sqlite3.connect(si.index_path(airoot, policy))) as connection:
        meta = {key: value for key, value in connection.execute("SELECT key, value FROM meta")}

    assert meta["policy_revision"] == policy.revision
    assert meta["schema_version"] == str(si.INDEX_SCHEMA_VERSION)
    assert parse_timestamp(meta["built_at"])
    assert meta["truncated"] == "false"
    assert tree.name in meta["roots"]
