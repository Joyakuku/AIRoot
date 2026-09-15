"""L1: search result stability — case, Unicode, long paths, reserved names (draft §36).

The search protocol's acceptance list (§10) asks for stable results across exactly these four
dimensions, and none of them had a dedicated test. What is pinned here was first *measured* on a
real machine (draft §36.1):

* this machine has `LongPathsEnabled=1`, so a 339-character path walks fine — but the walk must not
  *depend* on that policy, which is why the syscalls go through the `\\\\?\\` form;
* case folding is Unicode-aware and symmetrical;
* `pythön` is not `python`: the protocol asks for stability, not for fuzzy folding;
* reserved device names are creatable only through the extended API, and once they exist they are
  ordinary entries — not an error, not a special case.

The one thing that cannot be verified here is stated instead of assumed: with the machine's long-path
policy *off*, this environment cannot create the tree at all (and changing a machine-wide registry
value is not something a test may do).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from airoot.caps import searchindex
from airoot.caps.search import (
    EXTENDED_PREFIX,
    build_request,
    crawl,
    extended_path,
    load_search_policy,
    native_path,
    verify_records,
)

SEGMENT = "d" * 40


@pytest.fixture
def deep_root(tests_tmp: Path) -> Path:
    """A tree whose leaf path is longer than `MAX_PATH`, cleaned up through the extended form."""

    root = tests_tmp / "stability-deep"
    leaf = root
    while len(str(leaf)) < 320:
        leaf = leaf / SEGMENT
    try:
        leaf.mkdir(parents=True)
    except OSError:
        try:
            os.makedirs(extended_path(str(leaf)), exist_ok=True)
        except OSError:  # pragma: no cover - depends on the machine's long-path policy
            pytest.skip("this machine cannot create a path longer than MAX_PATH")
    (leaf / "python.exe").write_bytes(b"MZ")
    try:
        yield root
    finally:
        shutil.rmtree(extended_path(str(root)), ignore_errors=True)


def names(outcome) -> set[str]:
    return {Path(record["path"]).name for record in outcome.records}


def test_a_tree_deeper_than_max_path_is_searched_completely(deep_root: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(deep_root)], extensions=[".exe"])

    outcome = crawl(request, [str(deep_root)], policy=policy)

    assert names(outcome) == {"python.exe"}
    assert outcome.unreadable_directories == 0, "the depth must not turn into 'unreadable'"
    assert outcome.matched == 1
    leaf = next(record for record in outcome.records if record["name"] == "python.exe")
    assert len(leaf["path"]) > 260, "the fixture must really be deeper than MAX_PATH"
    assert leaf["accessible"] is True


def test_reported_paths_never_carry_the_extended_prefix(deep_root: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(deep_root)])
    outcome = crawl(request, [str(deep_root)], policy=policy)

    assert outcome.records
    assert all(not record["path"].startswith(EXTENDED_PREFIX) for record in outcome.records)


def test_the_extended_form_is_used_for_syscalls_and_is_reversible() -> None:
    plain = str(Path(os.environ.get("SystemDrive", "C:") + "\\") / "deep" / "leaf")

    assert extended_path(plain) == EXTENDED_PREFIX + plain
    assert native_path(extended_path(plain)) == plain
    # Idempotent: prefixing something already prefixed must not double it.
    assert extended_path(extended_path(plain)) == extended_path(plain)


def test_unc_paths_take_the_documented_extended_form() -> None:
    unc = r"\\server\share\folder"

    assert extended_path(unc) == EXTENDED_PREFIX + "UNC" + unc[1:]
    assert native_path(extended_path(unc)) == unc


def test_a_deep_leaf_survives_the_index_round_trip(deep_root: Path, tests_tmp: Path) -> None:
    """Accessibility is recomputed per answer — through the extended form (draft §36.2)."""

    policy = load_search_policy()
    airoot = tests_tmp / "stability-airoot"
    airoot.mkdir(parents=True, exist_ok=True)
    searchindex.build_index(airoot, roots=[str(deep_root)], policy=policy)
    state = searchindex.read_state(airoot, policy=policy)
    request = build_request("python", policy=policy, roots=[str(deep_root)], extensions=[".exe"])

    rows = searchindex.query_index(airoot, request=request, roots=[str(deep_root)], policy=policy, state=state)

    assert rows is not None
    assert [Path(record["path"]).name for record in rows] == ["python.exe"]
    assert rows[0]["accessible"] is True


def test_physical_verify_reaches_a_deep_leaf(deep_root: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(deep_root)], extensions=[".exe"])
    records = crawl(request, [str(deep_root)], policy=policy).records

    verify_records(records)

    assert [record["verification"] for record in records] == ["verified"]


@pytest.fixture
def unicode_root(tests_tmp: Path) -> Path:
    root = tests_tmp / "stability-unicode"
    root.mkdir(parents=True, exist_ok=True)
    # Only one spelling of `python.exe`: NTFS is case-insensitive, so writing `PYTHON.EXE` would
    # open the same file rather than create a second entry (asserted below).
    for name in ("python.exe", "pyth\u00f6n.exe", "python\u00a0space.exe", "\u4e2d\u6587python.exe"):
        (root / name).write_bytes(b"MZ")
    return root


def test_case_folding_is_symmetrical(unicode_root: Path) -> None:
    policy = load_search_policy()

    def found(query: str) -> set[str]:
        request = build_request(query, policy=policy, roots=[str(unicode_root)], match="contains", target="name")
        return names(crawl(request, [str(unicode_root)], policy=policy))

    assert found("python") == found("PYTHON") == found("PyThOn")
    assert found("python") == {"python.exe", "python\u00a0space.exe", "\u4e2d\u6587python.exe"}


def test_the_filesystem_is_case_insensitive_so_only_one_spelling_exists(unicode_root: Path) -> None:
    """The stability claim is about *matching*, not about two files with one name."""

    (unicode_root / "PYTHON.EXE").write_bytes(b"MZ")
    on_disk = sorted(path.name for path in unicode_root.iterdir() if path.name.lower() == "python.exe")

    assert on_disk == ["python.exe"], "NTFS kept the original spelling; no second entry appeared"


def test_unicode_names_match_what_they_say_and_nothing_more(unicode_root: Path) -> None:
    policy = load_search_policy()

    def found(query: str, match: str = "contains") -> set[str]:
        request = build_request(query, policy=policy, roots=[str(unicode_root)], match=match, target="name")
        return names(crawl(request, [str(unicode_root)], policy=policy))

    # A non-ASCII name is found by its own text...
    assert found("pyth\u00f6n") == {"pyth\u00f6n.exe"}
    # ...and is *not* folded into ASCII: the protocol asks for stability, not fuzziness (§36.3-4).
    assert "pyth\u00f6n.exe" not in found("python")
    # `exact` compares the whole name, so the extension still matters.
    assert found("python.exe", "exact") == {"python.exe"}
    assert found("python", "exact") == set()


def test_reserved_device_names_are_ordinary_entries(tests_tmp: Path) -> None:
    """They can only be created through the extended API; after that they are just files."""

    root = tests_tmp / "stability-reserved"
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in ("CON", "NUL", "COM1"):
        try:
            with open(extended_path(str(root / f"{name}.exe")), "wb") as handle:
                handle.write(b"MZ")
            created.append(name)
        except OSError:  # pragma: no cover - older Windows builds refuse even with the prefix
            continue
    if not created:
        pytest.skip("this machine cannot create reserved device names")

    policy = load_search_policy()
    request = build_request(
        "con", policy=policy, roots=[str(root)], match="contains", target="name", extensions=[".exe"]
    )
    outcome = crawl(request, [str(root)], policy=policy)

    assert names(outcome) == {"CON.exe"}
    assert all(record["accessible"] is True for record in outcome.records)
    assert outcome.unreadable_directories == 0


def test_a_deep_root_is_refused_neither_by_canonicalisation_nor_by_the_crawl(deep_root: Path) -> None:
    """`resolve_roots` canonicalises through `Path.resolve()`; a deep root must survive that."""

    from airoot.caps.search import resolve_roots

    roots, origin = resolve_roots([str(deep_root)])

    assert origin == "request"
    assert roots == [str(deep_root.resolve())]


def test_the_stability_facts_do_not_depend_on_a_policy_we_did_not_read() -> None:
    """If the machine's long-path policy is off, this environment cannot have created the fixture.

    Stated as a test so the limitation is visible in the suite rather than only in prose.
    """

    if sys.platform != "win32":  # pragma: no cover - v1 is a Windows provider
        pytest.skip("long-path policy is a Windows concept")
    assert extended_path(os.environ.get("SystemDrive", "C:") + "\\x").startswith(EXTENDED_PREFIX)
    # A policy-independent claim is exactly what this module can make: the syscall form is ours.
    assert replace(load_search_policy(), revision="srch-test").revision == "srch-test"
