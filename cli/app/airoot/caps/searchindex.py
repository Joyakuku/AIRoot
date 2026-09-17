"""The durable search index (draft §32): what a directory walk saw, remembered.

This is **not** the Native Index the search protocol describes. There is no USN journal consumer and
no NTFS metadata reader here; the index is built by one bounded crawl and its freshness means "when
that walk last finished" — never "the journal cursor is caught up" (ADR-0019, draft §32.2-1).

Storage rules that matter:

* the database lives at ``cache/search/index.db`` (protocol §3.1) and is **derived state**;
* it is built into ``index.db.tmp`` inside one transaction and then **atomically replaced** — never
  delete-then-create, never per-record deletion (ADR-0019 decision 2);
* ``journal_mode=DELETE``, not WAL: a ``-wal``/``-shm`` side file would be one more thing to leak and
  one more deletion path to reason about;
* a corrupt index is a **diagnosis**, not a crash: readers report ``SEARCH_INDEX_DEGRADED`` and the
  caller falls back to a live crawl (draft §32.4-5).

Two properties keep the two answering paths from drifting apart:

* the index stores an unfiltered superset (directories, hidden entries, every size); all request
  filters are applied at query time through the *same* function the crawl uses;
* accessibility is never stored — it belongs to the calling process and is recomputed per answer.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..clock import Clock, SYSTEM_CLOCK, parse_timestamp
from .search import (
    CrawlOutcome,
    SearchPolicy,
    build_request,
    crawl,
    load_search_policy,
    record_passes_filters,
    _accessible,
)

INDEX_SCHEMA_VERSION = 1
DEFAULT_INDEX_PATH = "cache/search/index.db"
INDEX_GENERATION_PREFIX = "crawl-index"

_DDL = (
    """
    CREATE TABLE meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE entries (
        path TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        size INTEGER,
        modified_at TEXT,
        attributes TEXT NOT NULL
    )
    """,
)


def _root_key(value: str) -> str:
    """The spelling roots and paths are compared in: lowercase, no trailing separator."""

    return str(value).lower().rstrip("\\")


def path_within_roots(path: str, roots: Iterable[str]) -> bool:
    """True when `path` **is** one of `roots` or sits below one of them.

    Both answering paths narrow an answer through this function, so "which files are in scope" has
    one definition. It is the spelling rule `covers` always used — lowercase, trailing separators
    stripped, a `\\` boundary so `...\\sub` never matches `...\\subway` — not a new path comparison
    (draft §111 measured the spellings that matter).

    Why it exists: `covers()` answers "is the request inside what the index walked", which is *true*
    for a request that is a **subdirectory** of an indexed root. Treating that as "the index can
    answer this request" made the index path return rows from outside the request — the live crawl
    filters by the requested roots, so the same query returned different files depending on whether
    an index happened to exist (draft §168, defect 1).
    """

    needle = _root_key(path)
    return any(needle == root or needle.startswith(root + "\\") for root in map(_root_key, roots))


@dataclass(frozen=True)
class IndexState:
    """What is on disk right now — including the answer "something is wrong with it"."""

    present: bool = False
    readable: bool = False
    built_at: str | None = None
    records: int = 0
    roots: tuple[str, ...] = ()
    truncated: bool = False
    coverage: str = "none"
    policy_revision: str | None = None
    schema_version: int | None = None
    problem: str | None = None

    @property
    def generation(self) -> str:
        """The value a cursor is bound to: a rebuild must invalidate old cursors."""

        if not self.readable or not self.built_at:
            return "no-index"
        return f"{INDEX_GENERATION_PREFIX}:{self.built_at}:{self.records}"

    def covers(self, roots: Iterable[str]) -> bool:
        """True when every requested root is inside a root this index walked.

        **Inclusion only** — this says the index *may* be able to answer, never that it did. It is
        deliberately not the scope filter: an index that walked a parent of the request covers it,
        and the rows still have to be narrowed to the request itself (see `path_within_roots`).
        """

        if not self.readable or not self.roots:
            return False
        return all(path_within_roots(root, self.roots) for root in roots)

    def to_document(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "readable": self.readable,
            "schema_version": self.schema_version,
            "built_at": self.built_at,
            "records": self.records,
            "roots": list(self.roots),
            "truncated": self.truncated,
            "coverage": self.coverage,
            "policy_revision": self.policy_revision,
            "generation": self.generation,
            "problem": self.problem,
        }


@dataclass(frozen=True)
class IndexBuild:
    built_at: str
    records: int
    roots: tuple[str, ...]
    truncated: bool
    timed_out: bool
    unreadable_directories: int
    elapsed_ms: int

    @property
    def coverage(self) -> str:
        return "complete_for_roots" if not (self.truncated or self.timed_out) else "partial"


def index_path(root: Path, policy: SearchPolicy | None = None) -> Path:
    rules = policy or load_search_policy()
    relative = str(rules.index_setting("path", DEFAULT_INDEX_PATH))
    return Path(root) / Path(relative.replace("\\", "/"))


def read_state(root: Path, *, policy: SearchPolicy | None = None) -> IndexState:
    """Inspect the index without trusting it; a corrupt file becomes a state, not an exception."""

    target = index_path(root, policy)
    if not target.is_file():
        return IndexState()
    try:
        # `closing`, not `with`: a sqlite3 connection is not a context manager for *closing*, and on
        # Windows an open handle makes the next atomic replace fail with "access denied".
        with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as connection:
            meta = {str(key): str(value) for key, value in connection.execute("SELECT key, value FROM meta")}
            records = int(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
    except (sqlite3.Error, OSError, ValueError) as error:
        return IndexState(present=True, readable=False, problem=str(error))
    if int(meta.get("schema_version", 0)) != INDEX_SCHEMA_VERSION:
        return IndexState(
            present=True,
            readable=False,
            schema_version=int(meta.get("schema_version", 0) or 0),
            problem="index was written by a different schema version; rebuild it",
        )
    try:
        roots = tuple(json.loads(meta.get("roots", "[]")))
    except ValueError:
        return IndexState(present=True, readable=False, problem="index root list is unreadable")
    return IndexState(
        present=True,
        readable=True,
        built_at=meta.get("built_at"),
        records=records,
        roots=roots,
        truncated=meta.get("truncated") == "true",
        coverage=str(meta.get("coverage", "none")),
        policy_revision=meta.get("policy_revision"),
        schema_version=INDEX_SCHEMA_VERSION,
    )


def build_index(
    root: Path,
    *,
    roots: Iterable[str],
    policy: SearchPolicy | None = None,
    clock: Clock = SYSTEM_CLOCK,
    time_source: Any = None,
) -> IndexBuild:
    """Walk the roots once and atomically replace the index with what was seen.

    The walk is deliberately permissive — directories, hidden entries, every size — because the
    index is a *superset* store; filtering happens per request at query time (module docstring).
    """

    import time as _time

    rules = policy or load_search_policy()
    source = time_source or _time.monotonic
    resolved = [str(item) for item in roots]

    request = build_request(
        "index",
        policy=rules,
        match="contains",
        target="name",
        roots=resolved,
        include_directories=True,
        include_hidden=True,
        accessible_only=False,
        allow_reparse_points=False,
        limit=1,
        max_duration_ms=rules.limit("max_duration_ms", 15000),
    )
    # The index owns its own crawl bounds (policy `index.*`), so a request-level cap cannot shrink it.
    request["max_duration_ms"] = int(rules.index_setting("max_duration_ms", request["max_duration_ms"]))

    started = source()
    outcome: CrawlOutcome = crawl(
        request,
        resolved,
        policy=_index_crawl_policy(rules),
        time_source=source,
        collect_all=True,
    )
    finished = source()
    built_at = clock.timestamp()

    target = index_path(root, rules)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        # A leftover temp file is a previous failed build, never a live index: replace it.
        temporary.unlink()
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            for statement in _DDL:
                connection.execute(statement)
            connection.executemany(
                "INSERT OR REPLACE INTO entries (path, name, kind, size, modified_at, attributes)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        record["path"],
                        record["name"],
                        record["kind"],
                        record["size"],
                        record["modified_at"],
                        json.dumps(record["attributes"]),
                    )
                    for record in outcome.records
                ),
            )
            meta = {
                "schema_version": str(INDEX_SCHEMA_VERSION),
                "policy_revision": rules.revision,
                "built_at": built_at,
                "roots": json.dumps(sorted(resolved)),
                "truncated": "true" if outcome.truncated or outcome.timed_out else "false",
                "coverage": "complete_for_roots"
                if not (outcome.truncated or outcome.timed_out)
                else "partial",
                "examined": str(outcome.examined),
            }
            connection.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", sorted(meta.items())
            )
            connection.commit()
        finally:
            connection.close()
        # One atomic step: readers see either the old index or the new one, never a half-written file.
        # (On Windows a reader still holding the file open makes this fail; the failure propagates
        # and the previous index stays intact — the safe direction, draft §32.4-7.)
        os.replace(temporary, target)
    except (sqlite3.Error, OSError):
        if temporary.exists():
            temporary.unlink()
        raise

    return IndexBuild(
        built_at=built_at,
        records=len(outcome.records),
        roots=tuple(sorted(resolved)),
        truncated=outcome.truncated,
        timed_out=outcome.timed_out,
        unreadable_directories=outcome.unreadable_directories,
        elapsed_ms=max(0, int((finished - started) * 1000)),
    )


def _index_crawl_policy(rules: SearchPolicy) -> SearchPolicy:
    """The crawl bounds the *index* uses, which are its own policy block (draft §32.2-8)."""

    from dataclasses import replace

    return replace(
        rules,
        crawl={
            **rules.crawl,
            "max_records": int(rules.index_setting("max_records", rules.crawl_setting("max_records", 250000))),
            "max_depth": int(rules.index_setting("max_depth", rules.crawl_setting("max_depth", 12))),
        },
    )


def query_index(
    root: Path,
    *,
    request: dict[str, Any],
    roots: Iterable[str],
    policy: SearchPolicy | None = None,
    state: IndexState | None = None,
) -> list[dict[str, Any]] | None:
    """Return the matching records, or `None` when this index cannot answer this request.

    `None` is not an error: it means "ask the live crawl instead", and the caller says so in
    `data.fallback`. The two reasons are a corrupt index and a root set the index never walked —
    pretending either one was covered would be the failure this whole module is written to avoid.

    Rows outside the **requested** roots are dropped (`path_within_roots`): being inside the walk is
    what `covers` establishes, and only that filter makes the index answer the same scope as a crawl
    of the same roots (draft §168, defect 1).
    """

    rules = policy or load_search_policy()
    current = state or read_state(root, policy=rules)
    scope = [str(item) for item in roots]
    if not current.readable or not current.covers(scope):
        return None

    target = index_path(root, rules)
    try:
        with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT path, name, kind, size, modified_at, attributes FROM entries"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return None

    matched: list[dict[str, Any]] = []
    for path, name, kind, size, modified_at, attributes in rows:
        if not path_within_roots(str(path), scope):
            continue
        try:
            parsed_attributes = list(json.loads(attributes))
        except ValueError:
            parsed_attributes = []
        record = {
            "path": str(path),
            "name": str(name),
            "kind": str(kind),
            "size": None if size is None else int(size),
            "modified_at": None if modified_at is None else str(modified_at),
            "attributes": parsed_attributes,
            "management": "unmanaged",
            "capability_id": None,
            "accessible": None,
            # The protocol's own word for "this came out of an index, nobody re-checked it".
            "verification": "indexed",
        }
        if not record_passes_filters(record, request, rules):
            continue
        accessible = _accessible(record["path"], record["kind"] == "directory")
        record["accessible"] = accessible
        if request["accessible_only"] and not accessible:
            continue
        matched.append(record)

    matched.sort(key=lambda item: (item["path"].lower(), item["path"]))
    return matched


def freshness_for(
    state: IndexState,
    request: dict[str, Any],
    *,
    clock: Clock = SYSTEM_CLOCK,
) -> tuple[dict[str, Any], str | None]:
    """`(freshness, reason_code)` for an index-backed answer.

    `stale` is the honest verdict when the index is older than the caller's `max_staleness_ms`: the
    index is real, it is simply not recent enough for what was asked (protocol §6.5).
    """

    fresh: dict[str, Any] = {
        "state": "unknown",
        "last_indexed_at": None,
        "lag_ms": None,
        "coverage": "none",
    }
    if not state.readable or not state.built_at:
        return fresh, None
    now = parse_timestamp(clock.timestamp())
    built = parse_timestamp(state.built_at)
    lag = max(0, int((now - built).total_seconds() * 1000))
    fresh["last_indexed_at"] = state.built_at
    fresh["lag_ms"] = lag
    fresh["coverage"] = state.coverage
    if lag > int(request["max_staleness_ms"]):
        fresh["state"] = "stale"
        return fresh, "SEARCH_RESULT_STALE"
    fresh["state"] = "current"
    return fresh, None


__all__ = [
    "DEFAULT_INDEX_PATH",
    "INDEX_GENERATION_PREFIX",
    "INDEX_SCHEMA_VERSION",
    "IndexBuild",
    "IndexState",
    "build_index",
    "freshness_for",
    "index_path",
    "path_within_roots",
    "query_index",
    "read_state",
]
