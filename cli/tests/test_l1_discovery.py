"""L1: ``discover`` classification — read-only, evidence-based, bounded.

The scan must not execute anything, must not follow reparse points, and must leave
the data root byte-identical. Synthetic layouts are built by copying the running
interpreter (a real PE with real version resources) into them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from airoot.canon import tree_digest
from airoot.caps.discovery import (
    PE_PREDICATE_FIELDS,
    PE_PREDICATE_OPERATORS,
    PREDICATE_TYPES,
    VERSION_SOURCE_PREFIX,
    WHITELIST_PATH,
    WhitelistEntry,
    classify_object,
    discover_all,
    discover_data_root,
    load_whitelist,
    match_entry,
    slug,
)
from airoot.caps.probe_pe import PeMetadata, probe_executable
from airoot.exits import AirootError
from airoot.registry import DataRoot
from airoot.paths import volume_serial

PYTHON_PE = Path(sys.executable)


@pytest.fixture(autouse=True)
def _forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover - only hit on a violation
        raise AssertionError(f"discover must never execute anything: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "spawnv", forbidden, raising=False)


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "env"
    root.mkdir()
    return root


def place_python_pe(directory: Path, name: str = "python.exe") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    shutil.copy2(PYTHON_PE, target)
    return target


# --------------------------------------------------------------------------- #
# whitelist
# --------------------------------------------------------------------------- #


def test_a_data_root_below_max_path_is_scanned(tmp_path: Path) -> None:
    """Draft §38: discovery walks user data, so a deep root must not become "unreadable".

    The walk descends through the `\\\\?\\` form (shared with `search` via `paths.extended_path`) and
    reports **native** paths — the prefix is an implementation detail of the syscalls.
    """

    deep = tmp_path / "env"
    while len(str(deep)) < 300:
        deep = deep / ("d" * 40)
    object_dir = deep / "python"
    try:
        place_python_pe(object_dir)
    except OSError:  # pragma: no cover - depends on the machine's long-path policy
        pytest.skip("this machine cannot create a path longer than MAX_PATH")

    report = discover_data_root(path=deep, data_root_id="dr-deep")

    candidates = [item for item in report.candidates if item.capability_id == "python"]
    assert candidates, "a data root deeper than MAX_PATH was not scanned"
    assert all(not str(item.object_root).startswith("\\\\?\\") for item in report.candidates)
    assert all(not str(item.executable).startswith("\\\\?\\") for item in report.candidates)
    assert "\\\\?\\" not in json.dumps(report.to_document()), "the prefix must never reach the answer"


def test_bundled_whitelist_loads_and_is_evidence_based() -> None:
    rules = load_whitelist()
    assert rules.revision == "wl-5"
    assert {entry.capability_id for entry in rules.entries} >= {"python", "node", "java", "git", "archive"}
    for entry in rules.entries:
        has_static = any(item.get("type") == "pe_static" for item in entry.evidence_all + entry.evidence_any)
        assert has_static or entry.weak_evidence, entry.capability_id
        if entry.weak_evidence:
            assert entry.weak_reason, "weak evidence must state its reason"


def test_whitelist_is_loaded_from_the_release_layer() -> None:
    assert WHITELIST_PATH.is_file()
    assert WHITELIST_PATH.parent.name == "policy"


def test_name_only_entry_is_refused(tmp_path: Path) -> None:
    """A bare executable name is never sufficient evidence (§9.3:716)."""

    broken = tmp_path / "whitelist.json"
    broken.write_text(
        json.dumps(
            {
                "revision": "wl-test",
                "entries": [
                    {
                        "capability_id": "python",
                        "kind": "runtime",
                        "evidence_all": [{"type": "executable_name", "any_of": ["python.exe"]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(broken)
    assert err.value.reason_code == "INVALID_INPUT"
    assert "static evidence" in err.value.message


def test_weak_evidence_is_allowed_when_declared(tmp_path: Path) -> None:
    declared = tmp_path / "whitelist.json"
    declared.write_text(
        json.dumps(
            {
                "revision": "wl-test",
                "entries": [
                    {
                        "capability_id": "build",
                        "kind": "tool",
                        "weak_evidence": True,
                        "weak_reason": "vendor does not populate version resources",
                        "evidence_all": [{"type": "executable_name", "any_of": ["cmake.exe"]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rules = load_whitelist(declared)
    assert rules.entries[0].weak_evidence is True


def test_missing_whitelist_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as err:
        load_whitelist(tmp_path / "absent.json")
    assert err.value.reason_code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Flutter", "flutter"),
        ("Huawei_SDK", "huawei_sdk"),
        ("Clash.for.Windows-0.20.16.4-ikuuu", "clash.for.windows-0.20.16.4-ikuuu"),
        ("7Z", "7z"),
        ("中文目录", "object"),
    ],
)
def test_slug_produces_schema_valid_ids(value: str, expected: str) -> None:
    assert slug(value) == expected
    assert slug(value)[0].isalnum()


def test_matching_requires_every_predicate() -> None:
    rules = load_whitelist()
    entry = next(item for item in rules.entries if item.capability_id == "python")
    metadata = probe_executable(PYTHON_PE)

    assert match_entry(entry, executable_name="python.exe", metadata=metadata) is True
    assert match_entry(entry, executable_name="other.exe", metadata=metadata) is False, "name must match"


def test_a_name_alike_binary_with_wrong_product_is_not_matched(tmp_path: Path) -> None:
    """C-032: `python.exe` whose ProductName is not Python must not be registered."""

    rules = load_whitelist()
    entry = next(item for item in rules.entries if item.capability_id == "python")
    metadata = probe_executable(PYTHON_PE)
    metadata.product_name = "Something Else Ltd"
    assert match_entry(entry, executable_name="python.exe", metadata=metadata) is False


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def test_matching_object_becomes_a_reference_candidate(data_root: Path) -> None:
    place_python_pe(data_root / "python")

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    assert report.whitelist_revision == "wl-5"
    candidate = next(item for item in report.candidates if item.directory_name == "python")
    assert candidate.management == "external_reference"
    assert candidate.capability_id == "python"
    assert candidate.kind == "runtime"
    assert candidate.version, "PE version resource supplies the version"
    assert candidate.architecture == "x64"
    assert candidate.probe_level == 2
    assert candidate.source_kind == "pe_static"
    assert candidate.external_id == "external/dr-env/python"
    assert candidate.evidence and candidate.evidence[0]["kind"] == "pe_static"


def test_a_candidate_id_is_an_address_not_a_directory_name(data_root: Path) -> None:
    """§140 / ADR-0051: the id carries the object's path below the data root.

    `external/<root>/<name>` was enough while only direct children could be adopted. At any depth,
    two objects called `bin` under one root would have collided, so the path is part of the identity.
    A depth-1 object yields the identical string as before (a one-component path), which is why
    nothing recorded before this change moves — the assertion below pins both halves.
    """

    place_python_pe(data_root / "python")
    place_python_pe(data_root / "toolchains" / "stable-x86_64-pc-windows-msvc")
    place_python_pe(data_root / "other" / "bin")

    shallow = classify_object(data_root / "python", data_root_id="dr-env", data_root_path=data_root)
    deep = classify_object(
        data_root / "toolchains" / "stable-x86_64-pc-windows-msvc",
        data_root_id="dr-env",
        data_root_path=data_root,
    )
    sibling = classify_object(data_root / "other" / "bin", data_root_id="dr-env", data_root_path=data_root)

    assert shallow.external_id == "external/dr-env/python"
    assert deep.external_id == "external/dr-env/toolchains/stable-x86_64-pc-windows-msvc"
    assert sibling.external_id == "external/dr-env/other/bin"
    assert len({shallow.external_id, deep.external_id, sibling.external_id}) == 3
    # The path is per component, so a deep object does not get truncated into a shallow one's id.
    assert slug(deep.relative_path) != deep.relative_path


def test_classifying_an_object_for_a_root_that_does_not_contain_it_is_refused(
    data_root: Path, tmp_path: Path
) -> None:
    """Falling back to the directory name would be silent, and silence is how a collision happens.

    A data root is also refused as one of its own objects: the scope is not a capability.
    """

    place_python_pe(tmp_path / "elsewhere" / "python")

    with pytest.raises(AirootError) as outside:
        classify_object(tmp_path / "elsewhere" / "python", data_root_id="dr-env", data_root_path=data_root)
    assert outside.value.reason_code == "INVALID_INPUT"
    assert "not inside the data root" in outside.value.message

    with pytest.raises(AirootError) as itself:
        classify_object(data_root, data_root_id="dr-env", data_root_path=data_root)
    assert itself.value.reason_code == "INVALID_INPUT"
    assert "not one of its own objects" in itself.value.message


def test_unmatched_object_stays_unmanaged(data_root: Path) -> None:
    place_python_pe(data_root / "mystery", "mystery.exe")

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    candidate = report.candidates[0]
    assert candidate.management == "unmanaged"
    assert candidate.capability_id is None
    assert "no whitelist entry matched" in candidate.notes[0]


def test_excluded_directory_is_recorded_with_its_reason(data_root: Path) -> None:
    (data_root / "Everything").mkdir()
    place_python_pe(data_root / "Everything", "Everything.exe")

    report = discover_data_root(path=data_root, data_root_id="dr-tools")
    candidate = report.candidates[0]
    assert candidate.management == "excluded"
    assert any("native search" in note.lower() or "Everything" in note for note in candidate.notes)


def test_depth_limit_is_enforced(data_root: Path) -> None:
    deep = data_root / "wrapper" / "a" / "b" / "c" / "d" / "e"
    place_python_pe(deep)

    rules = load_whitelist()
    report = discover_data_root(path=data_root, data_root_id="dr-env", whitelist=rules)
    candidate = report.candidates[0]
    assert candidate.management == "unmanaged", "a binary beyond max_depth must not be claimed"


@pytest.fixture(scope="module")
def junction_layout(tmp_path_factory):
    """A data root containing a junction, built *before* the execution guard exists.

    The guard patches ``subprocess.Popen`` too, and ``subprocess.run`` looks ``Popen``
    up as a module global, so setup that must shell out has to happen in a
    higher-scoped fixture (module fixtures are set up before function fixtures).
    """

    base = tmp_path_factory.mktemp("junction")
    root = base / "env"
    root.mkdir()
    target = base / "outside"
    place_python_pe(target)
    link = root / "linked"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if created.returncode != 0 or not link.exists():
        pytest.skip(f"cannot create a junction here: {created.stderr.strip()}")
    return root


def test_reparse_point_is_quarantined_not_followed(junction_layout: Path) -> None:
    report = discover_data_root(path=junction_layout, data_root_id="dr-env")
    candidate = report.candidates[0]
    assert candidate.management == "quarantined"
    assert "reparse point" in candidate.notes[0]
    assert candidate.capability_id is None, "a junction must never be classified by its target"


def test_scan_is_read_only(data_root: Path) -> None:
    place_python_pe(data_root / "python")
    (data_root / "notes.txt").write_text("user data", encoding="utf-8")
    before = tree_digest(data_root)

    discover_data_root(path=data_root, data_root_id="dr-env")

    assert tree_digest(data_root) == before


def test_scan_never_deletes_excluded_or_unmatched(data_root: Path) -> None:
    (data_root / "Everything").mkdir()
    (data_root / "Everything" / "Everything.exe").write_bytes(b"MZ-not-real")
    (data_root / "junk").mkdir()
    (data_root / "junk" / "readme.txt").write_text("keep me", encoding="utf-8")

    discover_data_root(path=data_root, data_root_id="dr-env")

    assert (data_root / "Everything" / "Everything.exe").is_file()
    assert (data_root / "junk" / "readme.txt").read_text(encoding="utf-8") == "keep me"


def test_report_document_is_json_serialisable(data_root: Path) -> None:
    place_python_pe(data_root / "python")
    report = discover_data_root(path=data_root, data_root_id="dr-env")
    document = report.to_document()
    assert json.loads(json.dumps(document, ensure_ascii=True))["counts"] == {"external_reference": 1}
    assert document["data_root_id"] == "dr-env"


def test_missing_data_root_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as err:
        discover_data_root(path=tmp_path / "absent", data_root_id="dr-x")
    assert err.value.reason_code == "DATA_ROOT_MISSING"


def test_max_objects_limit_marks_the_report_truncated(data_root: Path, tmp_path: Path) -> None:
    for index in range(4):
        (data_root / f"obj{index}").mkdir()
    rules = load_whitelist(tmp_path / "wl.json") if False else load_whitelist()
    limited = type(rules)(
        revision=rules.revision,
        entries=rules.entries,
        exclusions=rules.exclusions,
        limits={**rules.limits, "max_objects": 2},
    )

    report = discover_data_root(path=data_root, data_root_id="dr-env", whitelist=limited)
    assert report.truncated is True
    assert report.scanned_objects == 2


# --------------------------------------------------------------------------- #
# regression: the false positives found by scanning a real D:\env
# --------------------------------------------------------------------------- #


def test_bundled_tool_deep_inside_an_object_is_not_the_object(data_root: Path) -> None:
    """`D:\\env\\flutter` ships `bin\\mingit\\cmd\\git.exe`; that must not make it a git.

    Verified against the real machine: Flutter bundles a Git distribution, and its own
    ``dart.exe`` carries no version resource, so the object is *not* statically provable.
    """

    place_python_pe(data_root / "flutter" / "bin" / "mingit" / "cmd", "git.exe")

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    candidate = report.candidates[0]
    assert candidate.management == "unmanaged", "a bundled tool must not classify its container"
    assert candidate.capability_id is None


def test_executable_at_the_object_root_still_matches(data_root: Path) -> None:
    place_python_pe(data_root / "python", "python.exe")
    report = discover_data_root(path=data_root, data_root_id="dr-env")
    assert report.candidates[0].management == "external_reference"


def test_executable_one_level_down_still_matches(data_root: Path) -> None:
    """A JDK/git layout keeps its entrypoint in ``bin\\``."""

    place_python_pe(data_root / "jdklike" / "bin", "python.exe")
    report = discover_data_root(path=data_root, data_root_id="dr-env")
    assert report.candidates[0].management == "external_reference"


def test_entrypoint_keeps_its_directory(data_root: Path) -> None:
    """A ``bin\\java.exe`` layout must record ``bin/java.exe``, not a bare ``java.exe``.

    Found on the real machine: the shipped whitelist declares ``entrypoints: ["java.exe"]``,
    and a name without its directory made session activation prepend ``D:\\env\\Java`` —
    a directory that holds no entrypoint. The observed location is the fact; the declared
    name is only the predicate that found it.
    """

    place_python_pe(data_root / "jdklike" / "bin", "python.exe")

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    candidate = report.candidates[0]
    assert candidate.entrypoints[0] == "bin/python.exe"
    assert (candidate.object_root / candidate.entrypoints[0]).is_file()


def test_cache_directory_is_excluded(data_root: Path) -> None:
    """A cached python.exe under `uv-cache` is derived data, not a runtime."""

    place_python_pe(data_root / "uv-cache" / "archive-v0" / "hash" / "Scripts", "python.exe")
    place_python_pe(data_root / "some-cache", "python.exe")

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    assert {candidate.management for candidate in report.candidates} == {"excluded"}


def test_shallowest_match_wins(data_root: Path) -> None:
    """When two entrypoints match, the one closest to the object root is the object's own."""

    place_python_pe(data_root / "tool", "python.exe")
    nested = data_root / "tool" / "vendor" / "python"
    shutil.copytree(data_root / "tool", nested)

    report = discover_data_root(path=data_root, data_root_id="dr-env")
    candidate = report.candidates[0]
    assert candidate.management == "external_reference"
    assert Path(candidate.evidence[0]["path"]).parent == data_root / "tool"


# --------------------------------------------------------------------------- #
# one object, several versions (nvm-style layouts)
# --------------------------------------------------------------------------- #

VS_FIXEDFILEINFO_SIGNATURE = (0xFEEF04BD).to_bytes(4, "little")


def write_pe_with_version(source: Path, destination: Path, version: tuple[int, int, int, int]) -> Path:
    """Copy a real PE and rewrite its version resource, so a version set can be synthesised.

    Patching all VS_FIXEDFILEINFO occurrences keeps this robust across languages, and it
    exercises the parser against bytes we control instead of against the host's binaries.
    """

    import struct

    data = bytearray(source.read_bytes())
    ms = (version[0] << 16) | version[1]
    ls = (version[2] << 16) | version[3]
    start = 0
    patched = 0
    while True:
        found = data.find(VS_FIXEDFILEINFO_SIGNATURE, start)
        if found < 0:
            break
        struct.pack_into("<I", data, found + 8, ms)   # FileVersionMS
        struct.pack_into("<I", data, found + 12, ls)  # FileVersionLS
        struct.pack_into("<I", data, found + 16, ms)  # ProductVersionMS
        struct.pack_into("<I", data, found + 20, ls)  # ProductVersionLS
        patched += 1
        start = found + 4
    assert patched, "the source binary must carry a version resource for this helper to work"

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(bytes(data))
    return destination


@pytest.fixture
def python_rule_whitelist(tmp_path: Path) -> Path:
    """A minimal whitelist, so these tests do not depend on the shipped one."""

    path = tmp_path / "wl-multiversion.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": "wl-test",
                "scan": {"max_depth": 4, "max_relative_depth": 2, "max_versions_per_object": 8},
                "entries": [
                    {
                        "capability_id": "python",
                        "kind": "runtime",
                        "evidence_all": [
                            {"type": "executable_name", "any_of": ["python.exe"]},
                            {"type": "pe_static", "field": "product_name", "contains": "Python"},
                        ],
                        "entrypoints": ["python.exe"],
                        "version_source": "pe_static:file_version",
                    }
                ],
                "exclusions": [],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def multi_version_layout(tmp_path_factory):
    """`nvm`-shaped layout: two versions plus a junction marking the active one.

    Built in a module-scoped fixture because creating the junction needs ``subprocess``
    before the execution guard is installed.
    """

    base = tmp_path_factory.mktemp("multiversion")
    root = base / "env"
    object_dir = root / "nvm-like"
    write_pe_with_version(PYTHON_PE, object_dir / "v1.0.0" / "python.exe", (1, 0, 0, 0))
    write_pe_with_version(PYTHON_PE, object_dir / "v2.0.0" / "python.exe", (2, 0, 0, 0))
    link = object_dir / "current"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(object_dir / "v2.0.0")], capture_output=True, text=True
    )
    if created.returncode != 0 or not link.exists():
        pytest.skip(f"cannot create a junction here: {created.stderr.strip()}")
    return root


def test_object_with_several_versions_yields_one_candidate_with_a_version_set(
    multi_version_layout: Path, python_rule_whitelist: Path
) -> None:
    rules = load_whitelist(python_rule_whitelist)
    report = discover_data_root(path=multi_version_layout, data_root_id="dr-env", whitelist=rules)

    assert len(report.candidates) == 1, "the object is one reference, not one per version"
    candidate = report.candidates[0]
    assert candidate.capability_id == "python"
    assert candidate.versions == ("1.0.0.0", "2.0.0.0")
    assert candidate.active_version == "2.0.0.0", "the junction target names the active version"
    assert candidate.version == "2.0.0.0", "`version` is what the object currently provides"
    assert any(item["kind"] == "version_set" for item in candidate.evidence)
    marker = next(item for item in candidate.evidence if item["kind"] == "junction_target")
    assert "current ->" in marker["detail"]


def test_versions_without_a_marker_leave_the_active_version_unknown(
    tmp_path: Path, python_rule_whitelist: Path
) -> None:
    """Nothing is guessed: no link means no observed active version."""

    root = tmp_path / "env"
    write_pe_with_version(PYTHON_PE, root / "plain" / "v1.0.0" / "python.exe", (1, 0, 0, 0))
    write_pe_with_version(PYTHON_PE, root / "plain" / "v3.0.0" / "python.exe", (3, 0, 0, 0))

    rules = load_whitelist(python_rule_whitelist)
    candidate = discover_data_root(path=root, data_root_id="dr-env", whitelist=rules).candidates[0]

    assert candidate.versions == ("1.0.0.0", "3.0.0.0")
    assert candidate.active_version is None
    assert candidate.version == "1.0.0.0", "without a marker, the primary entrypoint version is reported"
    assert all(item["kind"] != "junction_target" for item in candidate.evidence)


def test_single_version_object_reports_a_one_element_set(
    data_root: Path, python_rule_whitelist: Path
) -> None:
    place_python_pe(data_root / "python", "python.exe")
    rules = load_whitelist(python_rule_whitelist)
    candidate = discover_data_root(path=data_root, data_root_id="dr-env", whitelist=rules).candidates[0]

    assert len(candidate.versions) == 1
    assert candidate.active_version is None, "one version present is not the same as observed active"
    assert all(item["kind"] != "version_set" for item in candidate.evidence)


def test_version_collection_is_bounded(tmp_path: Path, python_rule_whitelist: Path) -> None:
    root = tmp_path / "env"
    for index in range(4):
        write_pe_with_version(PYTHON_PE, root / "many" / f"v{index}.0.0" / "python.exe", (index, 0, 0, 0))

    document = json.loads(python_rule_whitelist.read_text(encoding="utf-8"))
    document["scan"]["max_versions_per_object"] = 2
    limited_path = tmp_path / "wl-limited.json"
    limited_path.write_text(json.dumps(document), encoding="utf-8")

    candidate = discover_data_root(
        path=root, data_root_id="dr-env", whitelist=load_whitelist(limited_path)
    ).candidates[0]
    assert len(candidate.versions) == 2, "the per-object version cap is enforced"


@pytest.fixture(scope="module")
def outside_link_layout(tmp_path_factory):
    """An object whose link points *outside* itself — built before the execution guard."""

    base = tmp_path_factory.mktemp("outsidelink")
    root = base / "env"
    object_dir = root / "python"
    place_python_pe(object_dir, "python.exe")
    outside = base / "elsewhere"
    write_pe_with_version(PYTHON_PE, outside / "v9.0.0" / "python.exe", (9, 0, 0, 0))
    link = object_dir / "current"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True
    )
    if created.returncode != 0 or not link.exists():
        pytest.skip(f"cannot create a junction here: {created.stderr.strip()}")
    return root


def test_a_link_pointing_outside_the_object_is_not_used_as_a_marker(
    outside_link_layout: Path, python_rule_whitelist: Path
) -> None:
    rules = load_whitelist(python_rule_whitelist)
    candidate = discover_data_root(
        path=outside_link_layout, data_root_id="dr-env", whitelist=rules
    ).candidates[0]
    assert candidate.active_version is None, "9.0.0.0 is not one of this object's observed versions"
    assert all(item["kind"] != "junction_target" for item in candidate.evidence)


# --------------------------------------------------------------------------- #
# over registered data roots
# --------------------------------------------------------------------------- #


def test_discover_all_walks_registered_roots(registry, data_root: Path) -> None:
    place_python_pe(data_root / "python")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
            ),
        )

    reports = discover_all(registry)
    assert [report.data_root_id for report in reports] == ["dr-env"]
    assert reports[0].candidates[0].capability_id == "python"


def test_discover_all_is_empty_without_data_roots(registry) -> None:
    assert discover_all(registry) == []


# --------------------------------------------------------------------------- #
# the whitelist's own vocabulary
#
# `_matches` answers `False` for a predicate it does not recognise, which is the right default at
# match time but a **silent** one: a typo would simply switch a capability's detection off, and the
# machine would look like it does not have that capability. The loader therefore refuses vocabulary
# it cannot evaluate, at the earliest point, the way it already refuses an entry with no static
# evidence (draft §43).
# --------------------------------------------------------------------------- #


def write_whitelist(tmp_path: Path, entry: dict) -> Path:
    path = tmp_path / "wl.json"
    path.write_text(json.dumps({"revision": "wl-test", "entries": [entry]}), encoding="utf-8")
    return path


def test_unknown_predicate_type_is_refused_at_load(tmp_path: Path) -> None:
    path = write_whitelist(
        tmp_path,
        {
            "capability_id": "python",
            "kind": "runtime",
            "weak_evidence": True,
            "weak_reason": "isolate the vocabulary check from the static-evidence check",
            "evidence_all": [{"type": "exectuable_name", "any_of": ["python.exe"]}],
        },
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(path)
    assert "unknown evidence predicate" in err.value.message
    assert "could never be detected" in err.value.evidence[1]


def test_unknown_pe_field_is_refused_at_load(tmp_path: Path) -> None:
    path = write_whitelist(
        tmp_path,
        {
            "capability_id": "python",
            "kind": "runtime",
            "evidence_all": [{"type": "pe_static", "field": "prodcut_name", "contains": "Python"}],
        },
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(path)
    assert "unknown PE field" in err.value.message


@pytest.mark.parametrize(
    "predicate",
    [
        {"type": "pe_static", "field": "product_name"},
        {"type": "pe_static", "field": "product_name", "equals": "Python", "contains": "Python"},
    ],
    ids=["neither-operator", "both-operators"],
)
def test_pe_static_needs_exactly_one_operator(tmp_path: Path, predicate: dict) -> None:
    path = write_whitelist(
        tmp_path, {"capability_id": "python", "kind": "runtime", "evidence_all": [predicate]}
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(path)
    assert "exactly one of" in err.value.message


def test_name_predicate_without_candidates_is_refused(tmp_path: Path) -> None:
    path = write_whitelist(
        tmp_path,
        {
            "capability_id": "python",
            "kind": "runtime",
            "weak_evidence": True,
            "weak_reason": "isolate the vocabulary check from the static-evidence check",
            "evidence_all": [{"type": "executable_name"}],
        },
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(path)
    assert "without any_of" in err.value.message


@pytest.mark.parametrize(
    "source",
    ["pe_static", "sibling_file:product_version", f"{VERSION_SOURCE_PREFIX}:prodcut_version"],
    ids=["no-field", "wrong-prefix", "unknown-attribute"],
)
def test_unusable_version_source_is_refused(tmp_path: Path, source: str) -> None:
    path = write_whitelist(
        tmp_path,
        {
            "capability_id": "python",
            "kind": "runtime",
            "evidence_all": [{"type": "executable_name", "any_of": ["python.exe"]}],
            "weak_evidence": True,
            "weak_reason": "test",
            "version_source": source,
        },
    )
    with pytest.raises(AirootError) as err:
        load_whitelist(path)
    assert "unusable version_source" in err.value.message


def test_the_shipped_whitelist_uses_only_evaluable_vocabulary() -> None:
    """The load above is the assertion: `load_whitelist` now refuses anything it cannot evaluate."""

    rules = load_whitelist()
    assert rules.revision == "wl-5"
    for entry in rules.entries:
        for predicate in entry.evidence_all + entry.evidence_any:
            assert predicate["type"] in PREDICATE_TYPES


def test_rust_toolchain_needs_both_of_its_predicates_and_the_shim_case_is_why() -> None:
    """§121 / ADR-0048: the installer's products need the recognition half, and both halves matter.

    Measured on the real machine: the toolchain's `rustc.exe` carries
    `product_name="Rust Compiler"` and `file_version="1.98.1.0"`, while the three 12.7 MB
    **shims** in `.cargo\\bin` carry **no version resource at all**. So the entry requires the
    executable name *and* the product string, and this test holds both directions: without the
    product predicate the shims would be registered as toolchain references whose every fact is
    unreadable, and without the name predicate any Rust binary would match.
    """

    rules = load_whitelist()
    entry = next((item for item in rules.entries if item.capability_id == "rust-toolchain"), None)
    assert entry is not None, "the recognition half for rust-toolchain is gone"
    assert entry.kind == "tool"
    assert entry.entrypoints == ("rustc.exe",)
    assert entry.version_source == "pe_static:file_version", (
        "the version has to come from the file's own resource, not from a directory name"
    )
    assert [item["type"] for item in entry.evidence_all] == ["executable_name", "pe_static"]

    from airoot.caps.probe_pe import PeMetadata

    named = PeMetadata(path="rustc.exe", is_pe=True, product_name="Rust Compiler", file_version="1.98.1.0")
    shim = PeMetadata(path="rustc.exe", is_pe=True)  # the shim: right name, no version resource
    other = PeMetadata(path="python.exe", is_pe=True, product_name="Python")

    assert match_entry(entry, executable_name="rustc.exe", metadata=named, executable=Path("rustc.exe"))
    assert not match_entry(entry, executable_name="rustc.exe", metadata=shim, executable=Path("rustc.exe")), (
        "a name-only match would register three references that carry no readable fact"
    )
    assert not match_entry(entry, executable_name="python.exe", metadata=other, executable=Path("python.exe"))


def test_every_declared_predicate_type_is_actually_implemented(data_root: Path) -> None:
    """The dangerous direction: declared but unimplemented would load and then never match.

    Each predicate below *must* match a real interpreter directory, so a type that only exists in
    `PREDICATE_TYPES` — with no branch behind it — fails here instead of silently classifying
    nothing.
    """

    target = data_root / "python" / "python.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PYTHON_PE, target)
    (target.parent / "LICENSE.txt").write_text("sibling evidence\n", encoding="utf-8")
    metadata = probe_executable(target)

    expected = {
        "executable_name": {"type": "executable_name", "any_of": ["python.exe"]},
        "sibling_file": {"type": "sibling_file", "any_of": ["license.txt"]},
        "pe_static": {"type": "pe_static", "field": "product_name", "contains": "Python"},
    }
    assert sorted(expected) == sorted(PREDICATE_TYPES), "a predicate type was added without a case here"

    for kind, predicate in expected.items():
        rule = WhitelistEntry(capability_id="probe", kind="runtime", evidence_all=(predicate,))
        assert match_entry(
            rule, executable_name="python.exe", metadata=metadata, executable=target
        ), f"{kind} is declared but does not match anything — it is not really implemented"

    # And an unrecognised predicate stays fail-closed rather than matching everything.
    bogus = WhitelistEntry(capability_id="probe", kind="runtime", evidence_all=({"type": "nope"},))
    assert match_entry(bogus, executable_name="python.exe", metadata=metadata, executable=target) is False


def test_the_pe_field_vocabulary_is_the_probe_metadata() -> None:
    """`PE_PREDICATE_FIELDS` is derived, so this pins that it stayed derived (and non-empty)."""

    from dataclasses import fields

    assert PE_PREDICATE_FIELDS == tuple(sorted(item.name for item in fields(PeMetadata)))
    assert "product_name" in PE_PREDICATE_FIELDS
    assert set(PE_PREDICATE_OPERATORS) == {"equals", "contains"}
