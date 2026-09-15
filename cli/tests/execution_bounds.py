"""The execution bounds: every number that decides whether AIROOT refuses a request.

Why this file exists
--------------------
ADR-0001 makes `cli/tests/fixtures/golden/` the language-neutral acceptance face. An implementation
that reads only that corpus and the published schemas still could not know that the shipped `limit`
bound is 2000, where the search index lives, that the three-way confirmation is
`("project-isolated", "data-root", "cancel")`, that a request may carry at most 64 roots, or that an
`https_artifact` fetch is capped at 2 GiB. Those numbers decide **refusals**, so two implementations
could reproduce every response in the corpus byte-for-byte and still disagree about whether request
2001 is accepted. Draft §46.4 named two of them and deferred them on purpose; §50 closes the whole
class.

The class, stated once: *change this number and some refusal changes.*

Where the numbers live (all three places matter)
------------------------------------------------
1. **`policy/*.json`** — shipped data with a `revision`.
2. **code constants** — `MAX_ROOTS`, `MAX_ARTIFACT_BYTES`, the index path, …
3. **code fallbacks** — the ceiling `build_request` uses when the policy *omits* a key. This is the
   one that is easy to miss, and it is not hypothetical: `load_search_policy` deliberately falls back
   to built-in defaults when the file is missing ("search must still work on a tree where someone
   deleted the policy"), so this third set describes real behaviour.

For that reason each search limit is recorded as **three** numbers — the policy value, the code
fallback, and the schema minimum — and each block carries where it came from. Recording only the
policy value would leave the deleted-policy case undetermined.

What is *not* here: the policy files themselves. They are source (comments, revisions, layout), and
copying them into the corpus would create a second copy that can drift. The corpus records the
**resolved numbers**, which are the behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from airoot.caps import search as search_module
from airoot.caps import searchindex
from airoot.caps.backends import https_artifact, portable_file
from airoot.caps.boundary import load_capabilities
from airoot.caps.discovery import load_whitelist
from airoot.caps.planner import CONFIRMATION_OPTIONS
from airoot.caps.probe_pe import MAX_HEADER_BYTES, MAX_RESOURCE_SECTION_BYTES
from airoot.caps.search import load_search_policy
from airoot.caps.selection import PRECEDENCES, load_selection_policy
from airoot.caps.sources import load_sources

#: Blocks whose bound is exercised for real: a request *at* the bound must be accepted and one
#: *above* it refused, built from the number in this artifact. Equality alone would only catch a
#: stale artifact; this catches an artifact that describes a bound nothing enforces (the §49 lesson:
#: check the thing that can be wrong, and check that it is actually in force).
EXERCISED: tuple[str, ...] = (
    "search_request",
    "confirmations",
)

#: Blocks recorded with exact equality only, each with the reason its enforcement is not exercised.
#: The split is data, and the guard requires these two tuples to *partition* the blocks — so "we did
#: not check this" can never look like "we checked it".
RECORDED_ONLY: dict[str, str] = {
    "crawl": "exercising `max_records` needs a 250 000-record tree, and `max_depth` is already covered behaviourally by the search suites",
    "index": "`max_records`/`max_age_ms` are exercised in `test_l1_searchindex.py` and `test_l1_doctor.py`; the artifact's job is to freeze the shipped numbers",
    "discovery_scan": "the whitelist limits are exercised in `test_l1_discovery.py`; a tree deep enough to hit `max_objects` would make the suite slow",
    "artifact_bounds": "the caps are 2 GiB / 8 GiB; a real oversize artifact is a P4 concern (needs a real backend), so only the numbers are frozen here",
    "inspection_bounds": "the PE inspection caps are 64 KiB / 8 MiB and are exercised by `test_l1_probe_pe.py` with synthetic headers",
    "policy_revisions": "a revision string is not a bound; it is recorded so a bump cannot go unnoticed in the corpus",
    "selection": "membership in `PRECEDENCES` is already enforced at load time by `load_selection_policy`, and the ordering behaviour is covered by `test_l1_selection.py`",
}


def build_bounds() -> dict[str, Any]:
    """The complete bounds artifact, resolved from the code and the shipped policy files."""

    search = load_search_policy()
    whitelist = load_whitelist()
    selection = load_selection_policy()
    capabilities = load_capabilities()
    sources = load_sources()

    return {
        "schema_version": 1,
        # Where each block's numbers come from, so a reader of the fixture can tell a shipped policy
        # value from a code constant without reading this module. The guard requires it to cover every
        # block, so a new block must declare its provenance rather than appear silently.
        "provenance": dict(sorted(SOURCES.items())),
        "search_request": {
            "max_roots": search_module.MAX_ROOTS,
            "max_cursor_length": search_module.MAX_CURSOR_LENGTH,
            "limits": dict(sorted(search.limits.items())),
            # The ceilings `build_request` falls back to when the policy omits the key. They are code,
            # not policy, and they decide behaviour whenever the policy file is absent or partial.
            "code_fallback_ceilings": {
                "limit": 10000,
                "max_duration_ms": 120000,
                "max_staleness_ms": 3600000,
            },
            # The published schema's own floor, below which the request is malformed (a different
            # refusal: `SEARCH_QUERY_INVALID`, not `EXTENSION_INPUT_INVALID`).
            "schema_minimums": {"limit": 1, "max_duration_ms": 1, "max_staleness_ms": 0},
            "defaults": dict(sorted(search.defaults.items())),
        },
        "crawl": {
            "max_depth": search.crawl_setting("max_depth", 12),
            "max_records": search.crawl_setting("max_records", 250000),
            "follow_reparse_points": search.crawl_setting("follow_reparse_points", False),
            "skip_directory_suffixes": list(search.crawl_setting("skip_directory_suffixes", [])),
        },
        "index": {
            "path": searchindex.DEFAULT_INDEX_PATH,
            "max_records": search.index_setting("max_records", 250000),
            "max_depth": search.index_setting("max_depth", 12),
            "max_age_ms": search.index_setting("max_age_ms", 86400000),
            "native_candidate": dict(search.index_setting("native_candidate", {})),
        },
        "discovery_scan": dict(sorted(whitelist.limits.items())),
        "confirmations": {"options": list(CONFIRMATION_OPTIONS)},
        "selection": {
            "precedences": list(PRECEDENCES),
            "shipped_precedence": selection.precedence,
        },
        "artifact_bounds": {
            "portable_file_max_artifact_bytes": portable_file.MAX_ARTIFACT_BYTES,
            "https_artifact_default_max_bytes": https_artifact.DEFAULT_MAX_BYTES,
        },
        "inspection_bounds": {
            "probe_pe_header_bytes": MAX_HEADER_BYTES,
            "probe_pe_resource_section_bytes": MAX_RESOURCE_SECTION_BYTES,
        },
        "policy_revisions": {
            "discovery_whitelist": whitelist.revision,
            "sources": sources.revision,
            "selection": selection.revision,
            "capabilities": capabilities.revision,
            "search": search.revision,
        },
    }


def blocks(document: dict[str, Any] | None = None) -> tuple[str, ...]:
    """The block names, excluding the schema version and the provenance map."""

    resolved = build_bounds() if document is None else document
    return tuple(sorted(key for key in resolved if key not in ("schema_version", "provenance")))


def coverage_problems(
    document: dict[str, Any] | None = None,
    *,
    exercised: tuple[str, ...] | None = None,
    recorded: dict[str, str] | None = None,
) -> list[str]:
    """The EXERCISED / RECORDED_ONLY split must partition the blocks, each miss having a reason.

    The split is injectable so the guard can prove each failure mode on synthetic input without
    mutating the real declaration — a test that edits module state to check a claim about module
    state proves less than it looks like it does.
    """

    resolved = build_bounds() if document is None else document
    names = set(blocks(resolved))
    exercised = set(EXERCISED if exercised is None else exercised)
    recorded = dict(RECORDED_ONLY if recorded is None else recorded)
    problems: list[str] = []

    problems += [f"{name}: a block that is neither exercised nor recorded as recorded-only" for name in sorted(names - exercised - set(recorded))]
    problems += [f"{name}: declared exercised, but no such block exists" for name in sorted(exercised - names)]
    problems += [f"{name}: declared recorded-only, but no such block exists" for name in sorted(set(recorded) - names)]
    problems += [f"{name}: a block cannot be both exercised and recorded-only" for name in sorted(exercised & set(recorded))]
    for name, reason in sorted(recorded.items()):
        if not str(reason).strip():
            problems.append(f"{name}: recorded-only without a reason, which is indistinguishable from a gap")

    provenance = set(resolved.get("provenance", {}))
    problems += [f"{name}: a block whose provenance nobody declared" for name in sorted(names - provenance)]
    problems += [f"{name}: provenance for a block that does not exist" for name in sorted(provenance - names)]
    return problems


#: Where the numbers come from, for the artifact's own documentation. Kept as data so a reader of the
#: fixture can tell a shipped policy value from a code constant without reading this module.
SOURCES: dict[str, str] = {
    "search_request": "policy/search-policy.json + caps/search.py (MAX_ROOTS, MAX_CURSOR_LENGTH, code fallbacks)",
    "crawl": "policy/search-policy.json `crawl` (code fallbacks are the same numbers in caps/search.py)",
    "index": "policy/search-policy.json `index` + caps/searchindex.py (DEFAULT_INDEX_PATH)",
    "discovery_scan": "policy/discovery-whitelist.json `limits`",
    "confirmations": "caps/planner.py (CONFIRMATION_OPTIONS)",
    "selection": "caps/selection.py (PRECEDENCES) + policy/selection-policy.json",
    "artifact_bounds": "caps/backends/{portable_file,https_artifact}.py",
    "inspection_bounds": "caps/probe_pe.py",
    "policy_revisions": "the five policy files' `revision` fields",
}
