"""The ``search`` protocol surface (draft §31): request, bounds, roots, cursor and bounded crawl.

`search-request.schema.json` / `search-response.schema.json` are the frozen contract; the search
protocol document (layer 4) fixes the boundary behaviour. What this module deliberately does **not**
contain is the fast path: a resident NTFS index built from volume metadata and the USN journal is
P3 proper. There are two answering paths, and each answer says which one it was: the crawl-built
index of `caps/searchindex.py` when it covers the request, otherwise a bounded directory walk that
confesses in `status=degraded`, `reason_code=SEARCH_FALLBACK_USED` (or `SEARCH_INDEX_DEGRADED` when
the index is present but unreadable), `data.fallback.kind="crawl"` and `freshness.coverage="none"`
(ADR-0017). A slow path dressed up as an index would be the one unforgivable outcome here.

Two shapes in this file are easy to get wrong and are called out where they matter:

* the search envelope is **flat** (`started_at`/`finished_at`/`elapsed_ms` at the top level, no
  `security_mode`) unlike the generic extension envelope, so `ext/envelope.py` is not reused;
* `management` on a result is the *shared* enum from `common.schema.json`, where "not AIROOT-known"
  is spelled `unmanaged`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat as stat_module
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import CLI_ROOT
from ..clock import Clock, SYSTEM_CLOCK, isoformat, parse_timestamp
from ..exits import AirootError
from ..paths import EXTENDED_PREFIX, extended_path, native_path
from ..schema_io import validate_document, validate_self

POLICY_PATH = CLI_ROOT / "app" / "airoot" / "policy" / "search-policy.json"

MATCHES = ("exact", "prefix", "contains")
TARGETS = ("name", "path", "name_and_path")
CONSISTENCIES = ("best_effort", "bounded_staleness", "refresh_then_read", "physical_verify")
FRESHNESS_STATES = ("current", "stale", "degraded", "rebuilding", "unknown")
COVERAGES = ("complete_for_roots", "partial", "none")

#: The generation a **crawl** answer is bound to (nothing was read from an index). It is still part
#: of a cursor's binding, so a cursor minted against a crawl can never be replayed against an index
#: answer, and vice versa.
NO_INDEX_GENERATION = "no-index"

MAX_ROOTS = 64
MAX_CURSOR_LENGTH = 4096

DEFAULT_REVISION = "srch-0"

_EXTENSION_PATTERN = r"^\.[A-Za-z0-9._-]{1,32}$"

_ALLOWED_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "notes",
        "limits",
        "defaults",
        "roots",
        "crawl",
        "index",
        "hidden_attribute_names",
    }
)

_ATTRIBUTE_NAMES = {
    "FILE_ATTRIBUTE_READONLY": "readonly",
    "FILE_ATTRIBUTE_HIDDEN": "hidden",
    "FILE_ATTRIBUTE_SYSTEM": "system",
    "FILE_ATTRIBUTE_DIRECTORY": "directory",
    "FILE_ATTRIBUTE_ARCHIVE": "archive",
    "FILE_ATTRIBUTE_DEVICE": "device",
    "FILE_ATTRIBUTE_NORMAL": "normal",
    "FILE_ATTRIBUTE_TEMPORARY": "temporary",
    "FILE_ATTRIBUTE_SPARSE_FILE": "sparse_file",
    "FILE_ATTRIBUTE_REPARSE_POINT": "reparse_point",
    "FILE_ATTRIBUTE_COMPRESSED": "compressed",
    "FILE_ATTRIBUTE_OFFLINE": "offline",
    "FILE_ATTRIBUTE_NOT_CONTENT_INDEXED": "not_content_indexed",
    "FILE_ATTRIBUTE_ENCRYPTED": "encrypted",
}


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SearchPolicy:
    revision: str = DEFAULT_REVISION
    limits: dict[str, int] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    roots: dict[str, Any] = field(default_factory=dict)
    crawl: dict[str, Any] = field(default_factory=dict)
    index: dict[str, Any] = field(default_factory=dict)
    hidden_attribute_names: tuple[str, ...] = ("hidden", "system")
    source: str = "default"

    def limit(self, name: str, fallback: int) -> int:
        value = self.limits.get(name, fallback)
        return int(value)

    def default(self, name: str, fallback: Any) -> Any:
        return self.defaults.get(name, fallback)

    def crawl_setting(self, name: str, fallback: Any) -> Any:
        return self.crawl.get(name, fallback)

    def index_setting(self, name: str, fallback: Any) -> Any:
        return self.index.get(name, fallback)


def load_search_policy(path: Path | None = None) -> SearchPolicy:
    """Read the policy file.

    Same asymmetry as `caps/selection.py`: a **missing** file falls back to the built-in defaults
    (search must still work on a tree where someone deleted the policy), while a file that exists
    and says something invalid is loud — a typo must not be reinterpreted silently.
    """

    target = Path(path) if path is not None else POLICY_PATH
    if not target.is_file():
        return SearchPolicy()
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SearchPolicy()
    if not isinstance(document, dict):
        raise AirootError(
            "INVALID_INPUT",
            f"search policy is not a JSON object: {target}",
            evidence=[f"expected one of {', '.join(sorted(_ALLOWED_POLICY_KEYS))}"],
        )
    unknown = sorted(set(document) - _ALLOWED_POLICY_KEYS)
    if unknown:
        raise AirootError(
            "INVALID_INPUT",
            f"search policy has unknown keys: {', '.join(unknown)}",
            evidence=[f"allowed: {', '.join(sorted(_ALLOWED_POLICY_KEYS))}", str(target)],
        )
    return SearchPolicy(
        revision=str(document.get("revision", DEFAULT_REVISION)),
        limits=dict(document.get("limits", {})),
        defaults=dict(document.get("defaults", {})),
        roots=dict(document.get("roots", {})),
        crawl=dict(document.get("crawl", {})),
        index=dict(document.get("index", {})),
        hidden_attribute_names=tuple(document.get("hidden_attribute_names", ("hidden", "system"))),
        source="file",
    )


# --------------------------------------------------------------------------- #
# request
# --------------------------------------------------------------------------- #


def _invalid_query(message: str, *evidence: str) -> AirootError:
    return AirootError("SEARCH_QUERY_INVALID", message, evidence=list(evidence))


def _over_limit(message: str, *evidence: str) -> AirootError:
    # Not `SEARCH_QUERY_INVALID`: the caller's request is well formed but asks for more than this
    # implementation will do, and the protocol requires that to be an explicit refusal
    # (protocol §6.3, ADR-0017 decision 4) rather than a silent clamp.
    return AirootError("EXTENSION_INPUT_INVALID", message, evidence=list(evidence))


def build_request(
    query: str,
    *,
    policy: SearchPolicy | None = None,
    match: str | None = None,
    target: str | None = None,
    roots: Iterable[str] = (),
    extensions: Iterable[str] = (),
    include_directories: bool | None = None,
    accessible_only: bool | None = None,
    include_hidden: bool | None = None,
    allow_reparse_points: bool | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    modified_after: str | None = None,
    limit: int | None = None,
    max_duration_ms: int | None = None,
    cursor: str | None = None,
    consistency: str | None = None,
    max_staleness_ms: int | None = None,
) -> dict[str, Any]:
    """Build a request that `search-request.schema.json` accepts, refusing what it cannot honour."""

    rules = policy or load_search_policy()

    text = str(query)
    if not 1 <= len(text) <= 1024:
        raise _invalid_query("query must be 1..1024 characters", f"length={len(text)}")
    if "*" in text or "?" in text:
        # The protocol has no glob or regex dialect, and quietly treating `*.onnx` as a literal
        # would return nothing while looking like a successful search of a weird name.
        raise _invalid_query(
            "wildcards are not part of the search dialect",
            "use --match prefix/contains, or --ext for a suffix",
        )

    resolved_match = str(match or rules.default("match", "contains"))
    if resolved_match not in MATCHES:
        raise _invalid_query(f"match must be one of {', '.join(MATCHES)}: {resolved_match!r}")
    resolved_target = str(target or rules.default("target", "name_and_path"))
    if resolved_target not in TARGETS:
        raise _invalid_query(f"target must be one of {', '.join(TARGETS)}: {resolved_target!r}")
    resolved_consistency = str(consistency or rules.default("consistency", "bounded_staleness"))
    if resolved_consistency not in CONSISTENCIES:
        raise _invalid_query(f"consistency must be one of {', '.join(CONSISTENCIES)}: {resolved_consistency!r}")

    resolved_extensions = sorted({str(item) for item in extensions})
    for item in resolved_extensions:
        if not item.startswith(".") or not 2 <= len(item) <= 33:
            raise _invalid_query(f"extension filter must look like '.exe': {item!r}")
        body = item[1:]
        if not all(character.isalnum() or character in "._-" for character in body):
            raise _invalid_query(f"extension filter has invalid characters: {item!r}")

    resolved_roots = [str(item) for item in roots]
    if len(resolved_roots) > MAX_ROOTS:
        raise _invalid_query(f"at most {MAX_ROOTS} roots per request", f"given={len(resolved_roots)}")

    if min_size is not None and int(min_size) < 0:
        raise _invalid_query("min_size cannot be negative", f"min_size={min_size}")
    if max_size is not None and int(max_size) < 0:
        raise _invalid_query("max_size cannot be negative", f"max_size={max_size}")
    if min_size is not None and max_size is not None and int(min_size) > int(max_size):
        # The published schema only pins `max_size >= 0`; min<=max is the arithmetic the caller
        # means, and an inverted range silently returning nothing is worse than a refusal.
        raise _invalid_query("min_size cannot exceed max_size", f"min_size={min_size}", f"max_size={max_size}")

    resolved_modified_after: str | None = None
    if modified_after is not None:
        try:
            resolved_modified_after = isoformat(parse_timestamp(str(modified_after)))
        except ValueError as error:
            raise _invalid_query("modified_after must be an RFC 3339 timestamp", str(error)) from error

    resolved_limit = int(limit if limit is not None else rules.default("limit", 100))
    resolved_duration = int(
        max_duration_ms if max_duration_ms is not None else rules.default("max_duration_ms", 5000)
    )
    resolved_staleness = int(
        max_staleness_ms if max_staleness_ms is not None else rules.default("max_staleness_ms", 30000)
    )
    if resolved_limit < 1:
        raise _invalid_query("limit must be at least 1", f"limit={resolved_limit}")
    if resolved_duration < 1:
        raise _invalid_query("max_duration_ms must be at least 1", f"max_duration_ms={resolved_duration}")
    if resolved_staleness < 0:
        raise _invalid_query("max_staleness_ms cannot be negative", f"max_staleness_ms={resolved_staleness}")

    for name, value, ceiling in (
        ("limit", resolved_limit, 10000),
        ("max_duration_ms", resolved_duration, 120000),
        ("max_staleness_ms", resolved_staleness, 3600000),
    ):
        published = rules.limit(name, ceiling)
        if value > published:
            raise _over_limit(
                f"{name} exceeds this implementation's bound",
                f"{name}={value}",
                f"bound={published} (policy/search-policy.json {rules.revision})",
            )

    if cursor is not None and len(str(cursor)) > MAX_CURSOR_LENGTH:
        raise _invalid_query("cursor is too long", f"length={len(str(cursor))}")

    request: dict[str, Any] = {
        "schema_version": 1,
        "query": text,
        "match": resolved_match,
        "target": resolved_target,
        "roots": resolved_roots,
        "extensions": resolved_extensions,
        "include_directories": _flag(include_directories, rules, "include_directories", False),
        "accessible_only": _flag(accessible_only, rules, "accessible_only", True),
        "include_hidden": _flag(include_hidden, rules, "include_hidden", False),
        "allow_reparse_points": _flag(allow_reparse_points, rules, "allow_reparse_points", False),
        "min_size": None if min_size is None else int(min_size),
        "max_size": None if max_size is None else int(max_size),
        "modified_after": resolved_modified_after,
        "limit": resolved_limit,
        "max_duration_ms": resolved_duration,
        "cursor": None if cursor is None else str(cursor),
        "consistency": resolved_consistency,
        "max_staleness_ms": resolved_staleness,
    }
    validate_document("search-request", request, reason_code="SEARCH_QUERY_INVALID")
    return request


def _flag(value: bool | None, policy: SearchPolicy, name: str, fallback: bool) -> bool:
    if value is not None:
        return bool(value)
    return bool(policy.default(name, fallback))


# --------------------------------------------------------------------------- #
# roots
# --------------------------------------------------------------------------- #


def _root_unavailable(message: str, *evidence: str) -> AirootError:
    return AirootError("SEARCH_ROOT_UNAVAILABLE", message, evidence=list(evidence))


def is_unc(path: str) -> bool:
    return str(path).startswith("\\\\")


def resolve_roots(
    requested: Iterable[str],
    *,
    policy: SearchPolicy | None = None,
    data_roots: Iterable[str] = (),
) -> tuple[list[str], str]:
    """Canonical absolute roots plus where they came from (`request` or `policy`).

    An empty `roots` means the policy roots — the registered data roots — and **never** "every
    volume" (protocol §6.3). An explicit root is the caller's own choice and is therefore honoured
    even outside the data roots (policy `roots.allow_explicit_roots_outside_data_roots`); it is
    never invented by AIROOT, and every result carries its `management` tag so the difference stays
    visible in the answer.
    """

    rules = policy or load_search_policy()
    explicit = [str(item) for item in requested if str(item).strip()]

    if not explicit:
        available: list[str] = []
        for candidate in data_roots:
            resolved = _canonical_root(candidate, policy=rules, from_request=False)
            if resolved is not None:
                available.append(resolved)
        if not available:
            raise _root_unavailable(
                "no root to search and no registered data root to fall back to",
                "register a data root (airoot data-root add <dir>) or pass --search-root <dir>",
                "--search-root <dir> searches that directory; --root is the AIROOT root, not a "
                "search root (ADR-0017/0018)",
            )
        return available, "policy"

    resolved_roots: list[str] = []
    for candidate in explicit:
        resolved = _canonical_root(candidate, policy=rules, from_request=True)
        if resolved is None:
            continue
        if resolved not in resolved_roots:
            resolved_roots.append(resolved)
    if not resolved_roots:
        raise _root_unavailable("none of the requested roots is a usable local directory", *explicit[:8])
    return resolved_roots, "request"


def _canonical_root(candidate: str, *, policy: SearchPolicy, from_request: bool) -> str | None:
    raw = str(candidate)
    if is_unc(raw):
        # The search protocol names this case explicitly and gives it this code; the generic
        # `UNC_NOT_ALLOWED` belongs to path-scoped commands, not to search roots (ADR-0017).
        raise _root_unavailable("a UNC root is not a local directory", raw)
    path = Path(raw)
    try:
        if not path.exists():
            if from_request:
                raise _root_unavailable("root does not exist", raw)
            return None
        resolved = path.resolve()
    except OSError as error:
        raise _root_unavailable("root is not usable", raw, str(error)) from error
    if not resolved.is_dir():
        if from_request:
            raise _root_unavailable("root is not a directory", raw)
        return None
    return str(resolved)


# --------------------------------------------------------------------------- #
# cursor
# --------------------------------------------------------------------------- #


def request_fingerprint(
    request: dict[str, Any],
    roots: Iterable[str],
    *,
    scope: str,
    implementation_id: str,
    index_generation: str = NO_INDEX_GENERATION,
) -> str:
    """The binding a cursor is minted against (protocol §6.3).

    Any change to the query, the filters, the root set, the scope, the implementation **or the index
    generation** invalidates a cursor; it must then be *refused*, never silently read as "page one".
    The index generation is why a cursor minted against one index build cannot be replayed against
    the next one (draft §32).
    """

    binding = {
        "query": request["query"],
        "match": request["match"],
        "target": request["target"],
        "extensions": list(request["extensions"]),
        "include_directories": request["include_directories"],
        "include_hidden": request["include_hidden"],
        "allow_reparse_points": request["allow_reparse_points"],
        "accessible_only": request["accessible_only"],
        "min_size": request["min_size"],
        "max_size": request["max_size"],
        "modified_after": request["modified_after"],
        "roots": sorted(str(item) for item in roots),
        "scope": scope,
        "implementation_id": implementation_id,
        "index_generation": str(index_generation),
    }
    canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(fingerprint: str, offset: int) -> str:
    payload = json.dumps({"v": 1, "f": fingerprint, "o": int(offset)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, fingerprint: str) -> int:
    """Return the offset a cursor points at, or refuse it (`SEARCH_CURSOR_INVALID`, exit 8)."""

    def refuse(detail: str) -> AirootError:
        return AirootError(
            "SEARCH_CURSOR_INVALID",
            "the cursor does not belong to this query",
            evidence=[detail, "re-run without --cursor to start from the first page"],
        )

    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise refuse(f"undecodable cursor: {error}") from error
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise refuse("unsupported cursor version")
    if str(payload.get("f")) != fingerprint:
        raise refuse("query, roots, scope or implementation changed since this cursor was issued")
    offset = payload.get("o")
    if not isinstance(offset, int) or offset < 0:
        raise refuse("cursor offset is not a non-negative integer")
    return offset


# --------------------------------------------------------------------------- #
# bounded crawl
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CrawlOutcome:
    records: list[dict[str, Any]]
    examined: int
    matched: int
    truncated: bool
    timed_out: bool
    unreadable_directories: int


def crawl(
    request: dict[str, Any],
    roots: Iterable[str],
    *,
    policy: SearchPolicy | None = None,
    time_source: Callable[[], float] = time.monotonic,
    collect_all: bool = False,
) -> CrawlOutcome:
    """Walk the roots under every bound, returning matches in a deterministic order.

    This is the fallback path, not an index: it is bounded by depth, by the number of records it
    will examine and by `max_duration_ms`, and it reports which bound it hit.

    `collect_all` exists for the index builder (draft §32): the index stores an *unfiltered superset*
    — directories, hidden entries, every size — so that request filters can be applied at query time
    by the very same `record_passes_filters`. Structural exclusions (cache-like directories, reparse
    points when policy forbids following them) still apply either way, because they are properties of
    the walk rather than of the request.
    """

    rules = policy or load_search_policy()
    max_depth = int(rules.crawl_setting("max_depth", 12))
    max_records = int(rules.crawl_setting("max_records", 250000))
    follow_reparse = bool(request["allow_reparse_points"]) and bool(
        rules.crawl_setting("follow_reparse_points", False)
    )
    skip_suffixes = tuple(str(item).lower() for item in rules.crawl_setting("skip_directory_suffixes", []))

    deadline = time_source() + (int(request["max_duration_ms"]) / 1000.0)

    records: list[dict[str, Any]] = []
    examined = 0
    truncated = False
    timed_out = False
    unreadable = 0

    for root in roots:
        stack: list[tuple[str, int]] = [(str(root), 0)]
        while stack:
            if time_source() >= deadline:
                timed_out = True
                break
            directory, depth = stack.pop()
            if depth > max_depth:
                truncated = True
                continue
            try:
                with os.scandir(extended_path(directory)) as entries:
                    ordered = sorted(entries, key=lambda item: item.name.lower())
            except OSError:
                unreadable += 1
                continue
            for entry in ordered:
                if examined >= max_records:
                    truncated = True
                    break
                if time_source() >= deadline:
                    timed_out = True
                    break
                examined += 1
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_directory = entry.is_dir(follow_symlinks=False)
                attributes = _attribute_names(info)
                if is_directory and any(entry.name.lower().endswith(suffix) for suffix in skip_suffixes):
                    # Cache-like directories are derived data, not capability objects (§7.1/§13.3).
                    continue
                if "reparse_point" in attributes:
                    if not follow_reparse:
                        continue
                    # Following it is allowed, so what it points at decides file vs directory:
                    # `is_dir(follow_symlinks=False)` says "not a directory" for a link by design.
                    is_directory = entry.is_dir()
                if is_directory and depth + 1 <= max_depth:
                    stack.append((entry.path, depth + 1))
                moment = isoformat(datetime.fromtimestamp(info.st_mtime, tz=timezone.utc))
                record = {
                    # `native_path` here and nowhere else: the walk keeps the extended form, the
                    # answer never shows it (draft §36.3-2).
                    "path": native_path(entry.path),
                    "name": entry.name,
                    "kind": "directory" if is_directory else "file",
                    "size": None if is_directory else int(info.st_size),
                    "modified_at": moment,
                    "attributes": attributes,
                    "management": "unmanaged",
                    "capability_id": None,
                    "accessible": None,
                    "verification": "unverified",
                }
                if not collect_all:
                    if not record_passes_filters(record, request, rules):
                        continue
                    accessible = _accessible(entry.path, is_directory)
                    record["accessible"] = accessible
                    if request["accessible_only"] and not accessible:
                        continue
                records.append(record)
            if truncated or timed_out:
                break
        if truncated or timed_out:
            break

    # Every match is kept and the page is cut *after* sorting: slicing during the walk would make
    # "which matches survive the limit" depend on traversal order, and then two pages of the same
    # query could overlap or skip results.
    records.sort(key=lambda item: (item["path"].lower(), item["path"]))
    return CrawlOutcome(
        records=records,
        examined=examined,
        matched=len(records),
        truncated=truncated,
        timed_out=timed_out,
        unreadable_directories=unreadable,
    )


def record_passes_filters(
    record: dict[str, Any], request: dict[str, Any], policy: SearchPolicy | None = None
) -> bool:
    """The request's filters, applied to one record.

    Shared by the live crawl and by an index answer on purpose: if these two paths filtered
    differently, the same query would return different results depending on whether an index
    happened to exist — the worst kind of inconsistency, because it looks like a cache bug.

    Accessibility is deliberately **not** here: it is a property of the calling process, recomputed
    at answer time, never trusted from an index built earlier (draft §32.2).
    """

    rules = policy or load_search_policy()
    attributes = record.get("attributes") or []
    if not request["include_hidden"] and any(
        name in attributes for name in rules.hidden_attribute_names
    ):
        return False
    if record["kind"] == "directory" and not request["include_directories"]:
        return False
    if not matches(record["name"], record["path"], request):
        return False
    size = record.get("size")
    if request["min_size"] is not None and (size is None or int(size) < int(request["min_size"])):
        return False
    if request["max_size"] is not None and (size is None or int(size) > int(request["max_size"])):
        return False
    modified_after = request.get("modified_after")
    if modified_after is not None and str(record.get("modified_at") or "") < str(modified_after):
        return False
    return True


def paginate(
    records: list[dict[str, Any]], *, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Cut one page out of the sorted match list; `None` means the list is exhausted."""

    window = records[offset : offset + max(0, int(limit))]
    following = offset + max(0, int(limit))
    return window, (following if following < len(records) else None)


def matches(name: str, path: str, request: dict[str, Any]) -> bool:
    query = str(request["query"]).lower()
    target = request["target"]
    needle_name = name.lower()
    needle_path = path.replace("\\", "/").lower()
    haystacks: list[str] = []
    if target in ("name", "name_and_path"):
        haystacks.append(needle_name)
    if target in ("path", "name_and_path"):
        haystacks.append(needle_path)
    extensions = [str(item).lower() for item in request["extensions"]]
    if extensions and not any(needle_name.endswith(item) for item in extensions):
        return False
    mode = request["match"]
    for haystack in haystacks:
        if mode == "exact" and haystack == query:
            return True
        if mode == "prefix" and haystack.startswith(query):
            return True
        if mode == "contains" and query in haystack:
            return True
    return False


def _attribute_names(info: os.stat_result) -> list[str]:
    raw = getattr(info, "st_file_attributes", 0)
    names: list[str] = []
    if not raw:
        return names
    for constant, label in _ATTRIBUTE_NAMES.items():
        value = getattr(stat_module, constant, None)
        if value is not None and raw & value:
            names.append(label)
    return names


def _accessible(path: str, is_directory: bool) -> bool:
    """The only accessibility answer this build can give: what *this* process can reach.

    Real ACL filtering for other identities needs the broker; pretending otherwise would be worse
    than a documented single-process check (draft §31.3). A deep path is probed through its
    extended-length form, otherwise a reachable leaf would be reported as unreachable.
    """

    target = extended_path(path)
    if is_directory:
        try:
            with os.scandir(target):
                return True
        except OSError:
            return False
    return os.access(target, os.R_OK)


def verify_records(records: list[dict[str, Any]]) -> None:
    """Re-check existence, type and size in place (`physical_verify`).

    An object that vanished or changed during the query keeps its place in the answer but is marked
    `verification="changed"` — the protocol forbids dressing that up as current fact (§6.3).
    """

    for record in records:
        path = Path(record["path"])
        try:
            info = os.stat(extended_path(record["path"]))
        except OSError:
            record["verification"] = "changed"
            continue
        kind = "directory" if path.is_dir() else "file"
        if kind != record["kind"]:
            record["verification"] = "changed"
            continue
        if record["size"] is not None and int(info.st_size) != int(record["size"]):
            record["verification"] = "changed"
            continue
        record["verification"] = "verified"


# --------------------------------------------------------------------------- #
# envelope
# --------------------------------------------------------------------------- #


def search_envelope(
    *,
    extension_id: str,
    implementation_id: str,
    request: dict[str, Any],
    page: list[dict[str, Any]],
    outcome: CrawlOutcome,
    next_cursor: str | None,
    status: str,
    reason_code: str | None,
    warnings: list[str],
    evidence: list[dict[str, Any]],
    freshness: dict[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    clock: Clock = SYSTEM_CLOCK,
    fallback_reason: str | None = "no resident index in this build; results come from a bounded crawl",
) -> dict[str, Any]:
    """Build and self-validate the search profile envelope (note: **flat** timing, no security_mode)."""

    start = started_at or clock.timestamp()
    finish = finished_at or clock.timestamp()
    elapsed = max(0, int((parse_timestamp(finish) - parse_timestamp(start)).total_seconds() * 1000))

    results = [{key: value for key, value in record.items() if not key.startswith("_")} for record in page]
    document: dict[str, Any] = {
        "schema_version": 1,
        "extension_id": extension_id,
        "operation": "search",
        "status": status,
        "started_at": start,
        "finished_at": finish,
        "elapsed_ms": elapsed,
        "data": {
            "implementation_id": implementation_id,
            "query": request["query"],
            "freshness": freshness
            or {"state": "unknown", "last_indexed_at": None, "lag_ms": None, "coverage": "none"},
            "results": results,
            "next_cursor": next_cursor,
            "stats": {
                "matched": outcome.matched,
                "returned": len(results),
                "index_records_examined": outcome.examined,
            },
            "fallback": None if fallback_reason is None else {"kind": "crawl", "reason": fallback_reason},
        },
        "warnings": warnings,
        "evidence": evidence,
        "reason_code": reason_code,
    }
    validate_self("search-response", document)
    return document


CRAWL_FALLBACK_REASON = "no resident index covers this request; results come from a bounded crawl"


def execute_search(
    request: dict[str, Any],
    roots: Iterable[str],
    *,
    extension_id: str,
    implementation_id: str,
    policy: SearchPolicy | None = None,
    classify: Callable[[str], tuple[str, str | None]] | None = None,
    keep: Callable[[dict[str, Any]], bool] | None = None,
    index_root: Any = None,
    clock: Clock = SYSTEM_CLOCK,
    time_source: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    """Run one search request and return `(envelope, exit_code)`.

    Answering order (draft §32): **the index if it covers this request**, otherwise a live crawl that
    says so. `index_root` is the AIROOT root that holds `cache/search/index.db`; without it this
    behaves exactly as §31 did (crawl-only), which is what keeps the no-index path byte-stable.

    Two orderings here are deliberate. Classification happens for **every** match, not just the
    page, so a caller-side filter (`keep`, e.g. `--managed-only`) can drop any of them; and the
    filter runs **before** pagination, because slicing first would make a page depend on which
    matches the filter later removed.
    """

    from ..exits import EXIT_DEGRADED, EXIT_SUCCESS

    rules = policy or load_search_policy()
    resolved = [str(item) for item in roots]

    warnings: list[str] = []
    index_state = None
    if index_root is not None:
        from . import searchindex

        if request["consistency"] == "refresh_then_read":
            try:
                searchindex.build_index(index_root, roots=resolved, policy=rules, clock=clock)
            except Exception as error:  # noqa: BLE001 - a failed refresh must not lose the answer
                warnings.append(f"index refresh failed ({error.__class__.__name__}); answering from a live crawl")
        index_state = searchindex.read_state(index_root, policy=rules)

    indexed: list[dict[str, Any]] | None = None
    index_unusable = False
    if index_state is not None:
        from . import searchindex

        if index_state.present and not index_state.readable:
            # Remember *why* the index did not answer: "present but unreadable" and "there is no
            # index for these roots" are two different states, and the answer names the right one
            # (draft §168, defect 3).
            index_unusable = True
            warnings.append(
                f"the search index is unusable ({index_state.problem}); answering from a live crawl"
            )
        elif index_state.readable:
            if not index_state.covers(resolved):
                warnings.append(
                    "the index does not cover the requested roots; answering from a live crawl"
                )
            else:
                indexed = searchindex.query_index(
                    index_root, request=request, roots=resolved, policy=rules, state=index_state
                )

    source = "index" if indexed is not None else "crawl"
    generation = index_state.generation if source == "index" else NO_INDEX_GENERATION
    fingerprint = request_fingerprint(
        request, resolved, scope="machine", implementation_id=implementation_id, index_generation=generation
    )

    offset = 0
    if request["cursor"]:
        offset = decode_cursor(str(request["cursor"]), fingerprint=fingerprint)

    started_at = clock.timestamp()
    freshness: dict[str, Any] = {
        "state": "unknown",
        "last_indexed_at": None,
        "lag_ms": None,
        "coverage": "none",
    }
    stale_reason: str | None = None
    if source == "index":
        from . import searchindex

        freshness, stale_reason = searchindex.freshness_for(index_state, request, clock=clock)
        examined = int(index_state.records)
        outcome = CrawlOutcome(
            records=list(indexed or []),
            examined=examined,
            matched=len(indexed or []),
            truncated=bool(index_state.truncated),
            timed_out=False,
            unreadable_directories=0,
        )
    else:
        outcome = crawl(request, resolved, policy=rules, time_source=time_source)

    tag = classify or (lambda _path: ("unmanaged", None))
    for record in outcome.records:
        management, capability_id = tag(record["path"])
        record["management"] = management
        record["capability_id"] = capability_id

    hidden_by_filter = 0
    if keep is not None:
        kept = [record for record in outcome.records if keep(record)]
        hidden_by_filter = len(outcome.records) - len(kept)
        outcome = replace(outcome, records=kept, matched=len(kept))

    page, following = paginate(outcome.records, offset=offset, limit=int(request["limit"]))
    if request["consistency"] == "physical_verify":
        verify_records(page)

    evidence: list[dict[str, Any]] = [
        {"kind": "search_roots", "detail": "; ".join(resolved)},
        {
            "kind": "search_policy",
            "detail": f"{rules.revision} (source={rules.source}); consistency={request['consistency']}",
        },
        {"kind": "search_source", "detail": f"{source} (generation={generation})"},
    ]
    if source == "index":
        evidence.append(
            {
                "kind": "search_index",
                "detail": f"built_at={index_state.built_at} records={index_state.records} "
                f"coverage={index_state.coverage}",
            }
        )
    if outcome.truncated:
        warnings.append(
            "the walk stopped at this implementation's record or depth bound; the answer is partial"
        )
    if outcome.unreadable_directories:
        warnings.append(
            f"{outcome.unreadable_directories} director"
            f"{'y' if outcome.unreadable_directories == 1 else 'ies'} could not be read"
        )
    if hidden_by_filter:
        warnings.append(f"{hidden_by_filter} match(es) hidden by the caller's filter")
    if source == "crawl":
        if request["consistency"] in ("bounded_staleness", "refresh_then_read"):
            # No index answered this request, so `max_staleness_ms` had nothing to apply to.
            warnings.append(
                "no index answered this request; the result came from a live crawl, "
                "so max_staleness_ms was not applied"
            )
    elif stale_reason == "SEARCH_RESULT_STALE":
        warnings.append(
            f"the index is older than max_staleness_ms ({freshness['lag_ms']} ms); "
            "run `airoot search refresh` or raise --max-staleness-ms"
        )

    if source == "index" and stale_reason is None and not outcome.truncated:
        status = "ok"
        reason_code = None
        exit_code = EXIT_SUCCESS
    elif source == "index":
        # The index answered, but it is either too old or only partly covered these roots: the data
        # is usable, the state is not perfect — which is exactly what exit code 2 is for.
        status = "degraded"
        reason_code = stale_reason or "SEARCH_INDEX_DEGRADED"
        exit_code = EXIT_DEGRADED
    else:
        status = "degraded"
        # Which word is right depends on *why* the index did not answer (draft §168, defect 3): an
        # index that is **present but unreadable** is a degraded index — the code `search status`,
        # `search explain` and `doctor` already give that state — while "no index at all" or "the
        # index does not cover these roots" is the fallback. Both are exit 2, so this is which word
        # is accurate, not which tier.
        reason_code = "SEARCH_INDEX_DEGRADED" if index_unusable else "SEARCH_FALLBACK_USED"
        exit_code = EXIT_DEGRADED
        if outcome.timed_out:
            status = "timed_out"
            reason_code = "SEARCH_TIMEOUT"
            warnings.append("the crawl hit max_duration_ms; narrow the roots or lower --limit")

    next_cursor = None
    if following is not None and not outcome.truncated and status != "timed_out":
        # A cursor is only meaningful when the match list itself is complete: paging through a
        # truncated list would present a partial answer as page one of a whole one.
        next_cursor = encode_cursor(fingerprint, following)

    document = search_envelope(
        extension_id=extension_id,
        implementation_id=implementation_id,
        request=request,
        page=page,
        outcome=outcome,
        next_cursor=next_cursor,
        status=status,
        reason_code=reason_code,
        warnings=warnings,
        evidence=evidence,
        freshness=freshness,
        started_at=started_at,
        clock=clock,
        fallback_reason=CRAWL_FALLBACK_REASON if source == "crawl" else None,
    )
    if offset and not page:
        # An offset past the end is not an error, but it must not read as "no matches at all".
        document["warnings"].append("cursor offset is past the end of the match list")
    return document, exit_code


__all__ = [
    "CONSISTENCIES",
    "COVERAGES",
    "CrawlOutcome",
    "DEFAULT_REVISION",
    "FRESHNESS_STATES",
    "MATCHES",
    "MAX_CURSOR_LENGTH",
    "MAX_ROOTS",
    "NO_INDEX_GENERATION",
    "POLICY_PATH",
    "SearchPolicy",
    "TARGETS",
    "build_request",
    "crawl",
    "decode_cursor",
    "encode_cursor",
    "execute_search",
    "extended_path",
    "is_unc",
    "load_search_policy",
    "native_path",
    "paginate",
    "request_fingerprint",
    "resolve_roots",
    "search_envelope",
    "verify_records",
]
