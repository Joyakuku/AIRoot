"""L1: the `search` protocol surface (draft §31, ADR-0017).

Two things are being pinned here, and they are different in kind:

* the **contract half** — a request that the published schema accepts, implementation bounds that
  refuse instead of clamping, root rules that never widen on their own, and a cursor that is
  refused when the world moved;
* the **honesty half** — this build has no index, so every answer must say so in all four places
  (`status`, `reason_code`, `data.fallback`, `freshness`). A test that only checked "some results
  came back" would pass just as happily on a build that lied about having an index.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from airoot.caps.search import (
    NO_INDEX_GENERATION,
    SearchPolicy,
    build_request,
    crawl,
    decode_cursor,
    encode_cursor,
    execute_search,
    load_search_policy,
    paginate,
    request_fingerprint,
    resolve_roots,
    verify_records,
)
from airoot.exits import AirootError
from airoot.schema_io import errors_for

EXTENSION_ID = "airoot-native-search-extension"
IMPLEMENTATION_ID = "airoot-native-search-crawl"

FILE_ATTRIBUTE_HIDDEN = 0x2


@pytest.fixture
def tree(tests_tmp: Path) -> Path:
    """A small tree with one match per filter dimension."""

    root = tests_tmp / "search-tree"
    for relative in ("apps", "apps/deep/deeper", "uv-cache"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "apps" / "python.exe").write_bytes(b"MZ" + b"\0" * 32)
    (root / "apps" / "python.txt").write_text("not an executable", encoding="utf-8")
    (root / "apps" / "deep" / "python.exe").write_bytes(b"MZ" + b"\0" * 8)
    (root / "apps" / "deep" / "deeper" / "python.exe").write_bytes(b"MZ")
    (root / "uv-cache" / "python.exe").write_bytes(b"MZ")
    (root / "README.md").write_text("nothing here", encoding="utf-8")
    return root


def run(tree: Path, **kwargs):
    policy = kwargs.pop("policy", None) or load_search_policy()
    request = build_request(roots=[str(tree)], **kwargs)
    roots, _origin = resolve_roots(request["roots"], policy=policy)
    return execute_search(
        request,
        roots,
        extension_id=EXTENSION_ID,
        implementation_id=IMPLEMENTATION_ID,
        policy=policy,
    )


# --------------------------------------------------------------------------- #
# the request: what it refuses
# --------------------------------------------------------------------------- #


def test_a_minimal_request_is_filled_from_the_policy_and_validates(tree: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(tree)])

    assert request["match"] == policy.default("match", "contains")
    assert request["target"] == policy.default("target", "name_and_path")
    assert request["consistency"] == policy.default("consistency", "bounded_staleness")
    assert request["limit"] == policy.default("limit", 100)
    assert request["include_hidden"] is False
    assert request["allow_reparse_points"] is False
    assert errors_for("search-request", request) == []


def test_wildcards_are_refused_rather_than_read_as_literal_names() -> None:
    with pytest.raises(AirootError) as error:
        build_request("*.onnx")

    assert error.value.reason_code == "SEARCH_QUERY_INVALID"
    assert error.value.exit_code == 8


def test_a_limit_above_the_policy_bound_is_refused_not_clamped() -> None:
    policy = load_search_policy()
    bound = policy.limit("limit", 2000)

    with pytest.raises(AirootError) as error:
        build_request("python", policy=policy, limit=bound + 1)

    assert error.value.reason_code == "EXTENSION_INPUT_INVALID"
    assert error.value.exit_code == 8
    assert any(str(bound) in item for item in error.value.evidence)


def test_the_bound_is_data_not_code() -> None:
    """A policy with a lower bound must move the refusal threshold."""

    strict = SearchPolicy(limits={"limit": 5}, defaults={"limit": 5}, revision="srch-test")
    build_request("python", policy=strict, limit=5)
    with pytest.raises(AirootError) as error:
        build_request("python", policy=strict, limit=6)
    assert error.value.reason_code == "EXTENSION_INPUT_INVALID"


def test_schema_bounds_are_enforced_as_well() -> None:
    for kwargs in ({"limit": 0}, {"max_duration_ms": 0}, {"max_staleness_ms": -1}):
        with pytest.raises(AirootError) as error:
            build_request("python", **kwargs)
        assert error.value.reason_code == "SEARCH_QUERY_INVALID"


def test_an_inverted_size_range_is_refused() -> None:
    with pytest.raises(AirootError) as error:
        build_request("python", min_size=100, max_size=10)

    assert error.value.reason_code == "SEARCH_QUERY_INVALID"


def test_extension_filters_must_look_like_extensions() -> None:
    with pytest.raises(AirootError) as error:
        build_request("python", extensions=["exe"])

    assert error.value.reason_code == "SEARCH_QUERY_INVALID"


def test_modified_after_must_be_a_timestamp() -> None:
    with pytest.raises(AirootError) as error:
        build_request("python", modified_after="last tuesday")

    assert error.value.reason_code == "SEARCH_QUERY_INVALID"


def test_too_many_roots_is_refused() -> None:
    with pytest.raises(AirootError) as error:
        build_request("python", roots=[f"C:\\r{index}" for index in range(65)])

    assert error.value.reason_code == "SEARCH_QUERY_INVALID"


# --------------------------------------------------------------------------- #
# roots
# --------------------------------------------------------------------------- #


def test_empty_roots_use_the_registered_data_roots_and_never_every_volume(tests_tmp: Path) -> None:
    data_root = tests_tmp / "search-data-root"
    data_root.mkdir(parents=True, exist_ok=True)

    roots, origin = resolve_roots([], data_roots=[str(data_root)])

    assert origin == "policy"
    assert roots == [str(data_root.resolve())]


def test_an_empty_root_set_with_nothing_declared_is_refused() -> None:
    with pytest.raises(AirootError) as error:
        resolve_roots([], data_roots=[])

    assert error.value.reason_code == "SEARCH_ROOT_UNAVAILABLE"
    assert error.value.exit_code == 2
    assert any("data-root add" in item for item in error.value.evidence)


def test_a_unc_root_is_refused_with_the_search_code() -> None:
    """The search protocol names this case, so the search code wins over the generic one."""

    with pytest.raises(AirootError) as error:
        resolve_roots([r"\\server\share"])

    assert error.value.reason_code == "SEARCH_ROOT_UNAVAILABLE"


def test_a_missing_or_file_root_is_refused(tests_tmp: Path) -> None:
    missing = tests_tmp / "search-missing-root"
    a_file = tests_tmp / "search-a-file.txt"
    a_file.write_text("x", encoding="utf-8")

    for candidate in (missing, a_file):
        with pytest.raises(AirootError) as error:
            resolve_roots([str(candidate)])
        assert error.value.reason_code == "SEARCH_ROOT_UNAVAILABLE"


def test_a_policy_root_that_vanished_is_skipped_not_fatal(tests_tmp: Path) -> None:
    good = tests_tmp / "search-good-root"
    good.mkdir(parents=True, exist_ok=True)

    roots, origin = resolve_roots([], data_roots=[str(tests_tmp / "gone"), str(good)])

    assert origin == "policy"
    assert roots == [str(good.resolve())]


# --------------------------------------------------------------------------- #
# cursor
# --------------------------------------------------------------------------- #


def test_a_cursor_from_another_query_is_refused() -> None:
    request = build_request("python")
    other = build_request("cmake")
    fingerprint = request_fingerprint(
        request, ["C:\\one"], scope="machine", implementation_id=IMPLEMENTATION_ID
    )
    foreign = request_fingerprint(other, ["C:\\one"], scope="machine", implementation_id=IMPLEMENTATION_ID)

    with pytest.raises(AirootError) as error:
        decode_cursor(encode_cursor(foreign, 10), fingerprint=fingerprint)

    assert error.value.reason_code == "SEARCH_CURSOR_INVALID"
    assert error.value.exit_code == 8


def test_a_cursor_from_a_different_root_set_is_refused() -> None:
    request = build_request("python")
    one = request_fingerprint(request, ["C:\\one"], scope="machine", implementation_id=IMPLEMENTATION_ID)
    two = request_fingerprint(request, ["C:\\two"], scope="machine", implementation_id=IMPLEMENTATION_ID)

    with pytest.raises(AirootError) as error:
        decode_cursor(encode_cursor(one, 1), fingerprint=two)

    assert error.value.reason_code == "SEARCH_CURSOR_INVALID"


def test_an_undecodable_cursor_is_refused_not_treated_as_page_one() -> None:
    request = build_request("python")
    fingerprint = request_fingerprint(
        request, ["C:\\one"], scope="machine", implementation_id=IMPLEMENTATION_ID
    )

    for token in ("not-a-cursor", "", "eyJ2IjoyfQ=="):
        with pytest.raises(AirootError) as error:
            decode_cursor(token, fingerprint=fingerprint)
        assert error.value.reason_code == "SEARCH_CURSOR_INVALID"


def test_paging_covers_the_match_list_exactly_once(tree: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(tree)], limit=2)
    roots, _origin = resolve_roots(request["roots"], policy=policy)

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        current = dict(request)
        current["cursor"] = cursor
        document, code = execute_search(
            current,
            roots,
            extension_id=EXTENSION_ID,
            implementation_id=IMPLEMENTATION_ID,
            policy=policy,
        )
        assert code == 2
        assert document["data"]["implementation_id"] == IMPLEMENTATION_ID
        seen += [item["path"] for item in document["data"]["results"]]
        pages += 1
        cursor = document["data"]["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "paging must terminate"

    assert len(seen) == len(set(seen)), "two pages must not repeat a result"
    assert len(seen) == document["data"]["stats"]["matched"]
    assert pages == 2


def test_a_truncated_crawl_never_hands_out_a_cursor(tree: Path) -> None:
    policy = SearchPolicy(crawl={"max_records": 2, "skip_directory_suffixes": ["cache-uv"]})
    request = build_request("python", policy=policy, roots=[str(tree)], limit=1)

    document, code = execute_search(
        request,
        [str(tree)],
        extension_id=EXTENSION_ID,
        implementation_id=IMPLEMENTATION_ID,
        policy=policy,
    )

    assert code == 2
    assert document["data"]["next_cursor"] is None
    assert any("partial" in warning for warning in document["warnings"])


# --------------------------------------------------------------------------- #
# the crawl
# --------------------------------------------------------------------------- #


def test_filters_select_the_expected_matches(tree: Path) -> None:
    policy = load_search_policy()

    def paths(**kwargs) -> set[str]:
        request = build_request("python", policy=policy, roots=[str(tree)], **kwargs)
        outcome = crawl(request, [str(tree)], policy=policy)
        return {Path(record["path"]).name for record in outcome.records}

    assert paths() == {"python.exe", "python.txt"}
    assert paths(extensions=[".exe"]) == {"python.exe"}
    assert paths(min_size=20) == {"python.exe"}  # only the largest of the three .exe files
    assert paths(target="name") == {"python.exe", "python.txt"}
    assert all(
        "uv-cache" not in Path(record["path"]).parts
        for record in crawl(
            build_request("python", policy=policy, roots=[str(tree)]), [str(tree)], policy=policy
        ).records
    ), "cache directories are derived data, not capability objects"


def test_match_modes_differ(tree: Path) -> None:
    policy = load_search_policy()

    def count(**kwargs) -> int:
        request = build_request(roots=[str(tree)], policy=policy, **kwargs)
        return len(crawl(request, [str(tree)], policy=policy).records)

    assert count(query="python.exe", match="exact", target="name") == 3
    assert count(query="python", match="prefix", target="name") == 4  # three .exe and one .txt
    assert count(query="ytho", match="contains", target="name") == 4
    assert count(query="python", match="exact", target="name") == 0
    assert count(query="python", match="contains", target="path", extensions=[".txt"]) == 1


def test_directories_are_only_returned_when_asked(tree: Path) -> None:
    policy = load_search_policy()
    without = build_request("deep", policy=policy, roots=[str(tree)], target="name")
    with_directories = build_request(
        "deep", policy=policy, roots=[str(tree)], target="name", include_directories=True
    )

    assert crawl(without, [str(tree)], policy=policy).matched == 0
    assert crawl(with_directories, [str(tree)], policy=policy).matched == 2


def test_hidden_entries_are_excluded_by_default(tests_tmp: Path) -> None:
    if sys.platform != "win32":  # pragma: no cover - v1 is a Windows provider
        pytest.skip("the hidden attribute is a Windows concept")

    root = tests_tmp / "search-hidden"
    root.mkdir(parents=True, exist_ok=True)
    visible = root / "python.exe"
    visible.write_bytes(b"MZ")
    hidden = root / "python-hidden.exe"
    hidden.write_bytes(b"MZ")
    if not ctypes.windll.kernel32.SetFileAttributesW(str(hidden), FILE_ATTRIBUTE_HIDDEN):
        pytest.skip("could not set the hidden attribute in this environment")

    policy = load_search_policy()
    default = build_request("python", policy=policy, roots=[str(root)])
    including = build_request("python", policy=policy, roots=[str(root)], include_hidden=True)

    assert {Path(r["path"]).name for r in crawl(default, [str(root)], policy=policy).records} == {
        "python.exe"
    }
    assert len(crawl(including, [str(root)], policy=policy).records) == 2


def test_a_reparse_point_is_not_followed_by_default(tests_tmp: Path) -> None:
    root = tests_tmp / "search-reparse"
    outside = tests_tmp / "search-reparse-outside"
    (root).mkdir(parents=True, exist_ok=True)
    (outside).mkdir(parents=True, exist_ok=True)
    (outside / "python.exe").write_bytes(b"MZ")
    link = root / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs a privilege we may not have
        pytest.skip("cannot create a directory link in this environment")

    policy = load_search_policy()
    default = build_request("python", policy=policy, roots=[str(root)])
    followed = build_request(
        "python", policy=policy, roots=[str(root)], allow_reparse_points=True
    )

    assert crawl(default, [str(root)], policy=policy).matched == 0
    # `allow_reparse_points` needs the policy to agree as well (crawl.follow_reparse_points).
    assert crawl(followed, [str(root)], policy=policy).matched == 0
    permissive = SearchPolicy(crawl={"follow_reparse_points": True, "skip_directory_suffixes": []})
    assert crawl(followed, [str(root)], policy=permissive).matched == 1


def test_a_timeout_is_reported_as_a_timeout(tests_tmp: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, max_duration_ms=200)

    # A coarse ticker: the first reading is already past a 200 ms deadline.
    ticks = iter([0.0] + [1.0] * 50)
    outcome = crawl(request, [str(tests_tmp)], policy=policy, time_source=lambda: next(ticks))

    assert outcome.timed_out is True


# --------------------------------------------------------------------------- #
# physical verification and tagging
# --------------------------------------------------------------------------- #


def test_physical_verify_marks_a_vanished_result_as_changed(tests_tmp: Path) -> None:
    target = tests_tmp / "search-verify" / "python.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"MZ")

    records = [{"path": str(target), "kind": "file", "size": 2, "verification": "unverified"}]
    verify_records(records)
    assert records[0]["verification"] == "verified"

    target.unlink()
    verify_records(records)
    assert records[0]["verification"] == "changed"


def test_results_carry_management_and_capability_tags(tree: Path) -> None:
    policy = load_search_policy()
    request = build_request("python", policy=policy, roots=[str(tree)], extensions=[".exe"])

    document, _code = execute_search(
        request,
        [str(tree)],
        extension_id=EXTENSION_ID,
        implementation_id=IMPLEMENTATION_ID,
        policy=policy,
        classify=lambda path: ("external_reference", "python"),
    )

    assert document["data"]["results"]
    assert {item["management"] for item in document["data"]["results"]} == {"external_reference"}
    assert {item["capability_id"] for item in document["data"]["results"]} == {"python"}


def test_unclassified_results_are_unmanaged(tree: Path) -> None:
    document, _code = run(tree, query="python", extensions=[".exe"])

    assert {item["management"] for item in document["data"]["results"]} == {"unmanaged"}
    assert {item["capability_id"] for item in document["data"]["results"]} == {None}


# --------------------------------------------------------------------------- #
# the honesty invariant (ADR-0017)
# --------------------------------------------------------------------------- #


def test_the_answer_says_it_is_a_fallback_in_four_places(tree: Path) -> None:
    document, code = run(tree, query="python", extensions=[".exe"])

    assert code == 2
    assert document["status"] == "degraded"
    assert document["reason_code"] == "SEARCH_FALLBACK_USED"
    assert document["data"]["fallback"]["kind"] == "crawl"
    assert document["data"]["fallback"]["reason"]
    assert document["data"]["freshness"]["state"] == "unknown"
    assert document["data"]["freshness"]["coverage"] == "none"
    assert document["data"]["freshness"]["last_indexed_at"] is None
    assert NO_INDEX_GENERATION not in ("", None)


def test_the_response_passes_the_published_schema(tree: Path) -> None:
    document, _code = run(tree, query="python")

    assert errors_for("search-response", document) == []
    assert document["data"]["stats"]["returned"] == len(document["data"]["results"])


def test_a_bounded_staleness_request_is_told_that_nothing_bounded_it(tree: Path) -> None:
    document, _code = run(tree, query="python", consistency="bounded_staleness")

    assert any("max_staleness_ms was not applied" in warning for warning in document["warnings"])


def test_physical_verify_is_honoured_when_asked(tree: Path) -> None:
    document, _code = run(tree, query="python", extensions=[".exe"], consistency="physical_verify")

    assert {item["verification"] for item in document["data"]["results"]} == {"verified"}


def test_no_matches_is_a_degraded_answer_not_an_empty_success(tree: Path) -> None:
    document, code = run(tree, query="definitely-absent-name")

    assert code == 2
    assert document["data"]["results"] == []
    assert document["data"]["stats"]["matched"] == 0
    assert document["reason_code"] == "SEARCH_FALLBACK_USED"


def test_stats_separate_matched_from_returned(tree: Path) -> None:
    document, _code = run(tree, query="python", extensions=[".exe"], limit=1)

    assert document["data"]["stats"]["returned"] == 1
    assert document["data"]["stats"]["matched"] == 3
    assert document["data"]["stats"]["index_records_examined"] > 0


def test_the_envelope_does_not_reuse_the_generic_extension_shape(tree: Path) -> None:
    """The search profile flattens the timing and has no `security_mode` (ADR-0017 decision 8)."""

    document, _code = run(tree, query="python")

    assert "timing" not in document
    assert "security_mode" not in document
    assert {"started_at", "finished_at", "elapsed_ms"} <= set(document)
