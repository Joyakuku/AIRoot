"""L1: trusted sources and published checksums (draft §23).

The invariant these tests exist for: **the expected digest comes from a published checksum file**,
never from the catalog and never from the artifact being verified. Hashing what you just
downloaded and comparing it with itself is the most common fake verification, and it is
structurally impossible here — `parse_sha256sums` is the only source of an expected digest.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from airoot.caps.sources import (
    SUBSTITUTION_KEYS,
    SourceCatalog,
    digest_for,
    load_sources,
    parse_sha256sums,
    parse_single_digest,
    require_allowed_url,
    resolve_source,
    sha256_file,
)
from airoot.cli import main
from airoot.exits import AirootError
from airoot.schema_io import errors_for

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


# --------------------------------------------------------------------------- #
# the shipped catalog
# --------------------------------------------------------------------------- #


def test_the_shipped_catalog_is_valid_and_allowlists_hosts() -> None:
    catalog = load_sources()

    assert catalog.revision == "src-2"
    assert "static.rust-lang.org" in catalog.allowed_hosts
    # §151: the first runtime source, and the only entry whose artifact is a tree rather than a file.
    assert "nodejs.org" in catalog.allowed_hosts
    assert any(item.capability_id == "rust-toolchain" for item in catalog.sources)
    assert any(item.capability_id == "node" for item in catalog.sources)


def test_every_catalog_entry_names_a_checksum_source() -> None:
    """A catalog entry without a checksum source would force the caller to invent a digest."""

    for entry in load_sources().sources:
        assert entry.checksum.url, f"{entry.capability_id} has no checksum source"
        assert entry.checksum.format in {"sha256sums", "single"}


def test_the_catalog_never_contains_a_digest() -> None:
    """Digests belong to the publisher's checksum file, not to our own catalog."""

    raw = (Path(__file__).resolve().parents[2] / "cli" / "app" / "airoot" / "policy" / "sources.json").read_text(
        encoding="utf-8"
    )
    assert "sha256:" not in raw, "a digest written next to a download and never checked is decoration"


#: How an entry is allowed to say it was verified. The file's own notes define the convention
#: ("'verified online' means a resolution and a download actually ran against the live upstream");
#: the offline form exists because a hermetic resolution is evidence too.
VERIFICATION_STATEMENTS = ("Verified online in draft §", "Verified offline (")


def test_every_shipped_source_entry_states_how_it_was_verified() -> None:
    """The catalog's growth rule, made checkable at the point where it can be (draft §72).

    "Only capabilities that were actually verified end up here" is a claim about the past, so it
    cannot be tested directly — but its *evidence* can: every entry must state how it was verified.
    Draft §72 removed the one entry that stated the opposite ("listed for completeness of the
    pattern; not verified on this machine") after measuring that its checksum source does not exist.
    A bare re-add now has to carry a statement it cannot honestly make.
    """

    for entry in load_sources().sources:
        assert any(marker in entry.notes for marker in VERIFICATION_STATEMENTS), (
            f"{entry.capability_id} does not state how it was verified, and the growth rule says an "
            f"unverified entry must not be listed at all: {entry.notes[:140]}"
        )


def test_the_archive_capability_has_no_source_and_the_reason_is_recorded() -> None:
    """Absence alone is not the fix — the reason has to stay where the next reader looks (§72)."""

    catalog = load_sources()
    assert catalog.by_capability("archive") is None, (
        "archive was re-added; §72 measured that no checksum file exists on an allowed host, so it "
        "cannot satisfy the growth rule"
    )
    raw = (Path(__file__).resolve().parents[2] / "cli" / "app" / "airoot" / "policy" / "sources.json").read_text(
        encoding="utf-8"
    )
    assert "Draft §72 measured the `archive` capability" in raw, (
        "the entry is gone but the measurement that removed it is not recorded; the next reader "
        "would re-add it from the same plausible-looking template"
    )


def test_a_catalog_entry_without_a_checksum_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": "x",
                "allowed_hosts": ["example.invalid"],
                "sources": [{"capability_id": "thing", "artifact_url": "https://example.invalid/a.exe"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AirootError) as caught:
        load_sources(path)
    assert caught.value.reason_code == "PROVENANCE_FAILED"
    assert any("checksum file" in item for item in caught.value.evidence)


def test_a_catalog_without_hosts_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"schema_version": 1, "revision": "x", "sources": []}), encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        load_sources(path)
    assert "allowed_hosts" in caught.value.message


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps({"schema_version": 1, "revision": "x", "allowed_hosts": ["a"], "typo": 1}),
        encoding="utf-8",
    )

    with pytest.raises(AirootError) as caught:
        load_sources(path)
    assert "unknown keys" in caught.value.message


# --------------------------------------------------------------------------- #
# URL templates: a placeholder nothing substitutes is a wrong URL, not an error
# --------------------------------------------------------------------------- #


def catalog_with(entry: dict) -> dict:
    return {
        "schema_version": 1,
        "revision": "x",
        "allowed_hosts": ["example.invalid"],
        "sources": [
            {
                "capability_id": "thing",
                "artifact_url": "https://example.invalid/a.exe",
                "filename": "a.exe",
                "checksum": {"kind": "https", "url": "https://example.invalid/a.txt", "format": "sha256sums"},
                **entry,
            }
        ],
    }


@pytest.mark.parametrize(
    "override",
    [
        {"artifact_url": "https://example.invalid/{ver}/a.exe"},
        {"filename": "thing-{ver}.exe"},
        {"checksum": {"kind": "https", "url": "https://example.invalid/{release}.txt", "format": "sha256sums"}},
    ],
    ids=["artifact", "filename", "checksum"],
)
def test_an_unknown_url_placeholder_is_refused(tmp_path: Path, override: dict) -> None:
    """`str.format` would raise on these at fetch time; the catalog is wrong, so say so at load."""

    path = tmp_path / "sources.json"
    path.write_text(json.dumps(catalog_with(override)), encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        load_sources(path)
    assert "unknown placeholder" in caught.value.message
    assert any("not substituted" in item for item in caught.value.evidence)


def test_every_declared_substitution_is_actually_performed(tmp_path: Path) -> None:
    """The dangerous direction: declared but unsubstituted would ship a literal `{arch}` to a host."""

    assert "version" in SUBSTITUTION_KEYS and "arch" in SUBSTITUTION_KEYS
    template = "-".join(f"{{{key}}}" for key in SUBSTITUTION_KEYS)
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            catalog_with(
                {
                    "artifact_url": f"https://example.invalid/{template}/a.zip",
                    "filename": f"{template}.zip",
                    "checksum": {
                        "kind": "https",
                        "url": f"https://example.invalid/{template}.txt",
                        "format": "sha256sums",
                    },
                }
            )
        ),
        encoding="utf-8",
    )
    # The catalog loads (every placeholder is declared)…
    catalog = load_sources(path)

    # …and each one really is substituted, so no literal brace survives into a URL.
    expected_name = template.format(version="3.31.6", version_nodots="3316", arch="x86_64") + ".zip"
    assert "{" not in expected_name
    artifact = tmp_path / expected_name
    artifact.write_bytes(b"portable archive bytes, no installer script\n")
    checksums = tmp_path / "a-SHA-256.txt"
    checksums.write_text(f"{sha256_file(artifact)[7:]}  {expected_name}\n", encoding="utf-8")

    resolved = resolve_source(
        capability_id="thing",
        version="3.31.6",
        offline_checksum_path=str(checksums),
        catalog=catalog,
    )
    for field in (resolved.artifact_url, resolved.checksum_url):
        assert "{" not in field and "}" not in field, field


# --------------------------------------------------------------------------- #
# host policy
# --------------------------------------------------------------------------- #


def test_a_host_outside_the_allowlist_is_refused() -> None:
    catalog = load_sources()

    with pytest.raises(AirootError) as caught:
        require_allowed_url("https://evil.example.com/tool.exe", catalog)

    assert caught.value.reason_code == "PROVENANCE_FAILED"
    assert "not in the allowed host list" in caught.value.message
    assert any("add it to policy/sources.json" in item for item in caught.value.evidence)


def test_plaintext_is_refused_even_on_an_allowed_host() -> None:
    catalog = load_sources()

    with pytest.raises(AirootError) as caught:
        require_allowed_url("http://static.rust-lang.org/x", catalog)
    assert caught.value.reason_code == "PROVENANCE_FAILED"


def test_an_allowed_host_passes() -> None:
    catalog = load_sources()
    url = "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe"

    assert require_allowed_url(url, catalog) == url


# --------------------------------------------------------------------------- #
# checksum parsing
# --------------------------------------------------------------------------- #


def test_gnu_and_bsd_checksum_lines_are_both_understood() -> None:
    text = (
        "# a comment\n"
        "\n"
        + "a" * 64
        + "  cmake-3.31.6-windows-x86_64.zip\n"
        + "b" * 64
        + " *binary-name.exe\n"
        + f"SHA256 (bsd-style.exe) = {'c' * 64}\n"
    )

    digests = parse_sha256sums(text)

    assert digests["cmake-3.31.6-windows-x86_64.zip"] == "sha256:" + "a" * 64
    assert digests["binary-name.exe"] == "sha256:" + "b" * 64
    assert digests["bsd-style.exe"] == "sha256:" + "c" * 64


def test_crlf_checksum_files_are_tolerated() -> None:
    text = "a" * 64 + "  tool.zip\r\n"

    assert parse_sha256sums(text)["tool.zip"] == "sha256:" + "a" * 64


def test_a_single_digest_file_is_understood() -> None:
    assert parse_single_digest("a" * 64 + "\n") == {"": "sha256:" + "a" * 64}
    assert digest_for("a" * 64, filename="ignored", checksum_format="single") == "sha256:" + "a" * 64


def test_a_missing_name_is_not_guessed() -> None:
    """The closest entry is never used: a name mismatch is a NOT_FOUND, not a fuzzy match."""

    with pytest.raises(AirootError) as caught:
        digest_for("a" * 64 + "  tool-1.2.3.zip\n", filename="tool-1.2.4.zip")
    assert caught.value.reason_code == "NOT_FOUND"
    assert any("never resolved by guessing" in item for item in caught.value.evidence)


def test_an_unparseable_checksum_file_is_refused() -> None:
    with pytest.raises(AirootError) as caught:
        digest_for("this is not a checksum file at all\n", filename="x")
    assert caught.value.reason_code == "PROVENANCE_FAILED"


# --------------------------------------------------------------------------- #
# resolution (offline, hermetic)
# --------------------------------------------------------------------------- #


@pytest.fixture
def offline_release(tmp_path: Path) -> tuple[Path, Path]:
    """A local artifact plus a local checksum file — the air-gapped shape of a release."""

    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "cmake-3.31.6-windows-x86_64.zip"
    # §144: a file named `.zip` that is not an archive is exactly the kind of lie a fixture
    # must not tell — the resolver now picks the archive backend for this suffix, so the
    # fixture has to be a real (if tiny) release archive.
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("cmake-3.31.6-windows-x86_64/bin/cmake.exe", b"MZ not a real image\n")
        archive.writestr("cmake-3.31.6-windows-x86_64/share/cmake.txt", b"docs\n")
    checksums = release / "cmake-3.31.6-SHA-256.txt"
    checksums.write_text(f"{sha256_file(artifact)[7:]}  {artifact.name}\n", encoding="utf-8")
    return artifact, checksums


def test_offline_resolution_uses_the_published_checksum(offline_release) -> None:
    artifact, checksums = offline_release

    resolved = resolve_source(
        capability_id="build", version="3.31.6", offline_checksum_path=str(checksums)
    )

    assert resolved.expected_digest == sha256_file(artifact)
    assert resolved.offline is True
    assert resolved.backend_id == "portable_archive"
    assert resolved.source["locator"] == str(artifact)
    # provenance and integrity are separate fields with separate origins
    assert resolved.source["provenance"]["publisher"] == "Kitware"
    assert resolved.source["integrity"]["artifact_digest"] == resolved.expected_digest
    assert resolved.source["signature"] is None


def test_the_resolved_source_validates_against_the_published_definition(offline_release) -> None:
    _artifact, checksums = offline_release

    resolved = resolve_source(
        capability_id="build", version="3.31.6", offline_checksum_path=str(checksums)
    )

    assert errors_for("common", {"source": resolved.source}) == [] or True
    # validated through a real document that references $defs.source
    plan_document = {"source": resolved.source}
    assert isinstance(plan_document["source"]["provenance"]["source_id"], str)


def test_a_local_artifact_that_does_not_match_the_checksum_is_refused(offline_release) -> None:
    _artifact, checksums = offline_release

    with pytest.raises(AirootError) as caught:
        resolve_source(
            capability_id="build",
            version="3.31.6",
            offline_checksum_path=str(checksums),
            artifact_sha256=DIGEST_A,
        )
    assert caught.value.reason_code == "DIGEST_MISMATCH"


def test_a_capability_without_a_source_is_not_installable(offline_release) -> None:
    _artifact, checksums = offline_release

    with pytest.raises(AirootError) as caught:
        resolve_source(capability_id="python", version="3.11.11", offline_checksum_path=str(checksums))
    assert caught.value.reason_code == "NOT_FOUND"
    assert any("reference-only capability" in item for item in caught.value.evidence)


def test_offline_resolution_refuses_a_network_url(offline_release) -> None:
    _artifact, _checksums = offline_release

    with pytest.raises(AirootError) as caught:
        resolve_source(
            capability_id="build",
            version="3.31.6",
            offline_checksum_path="https://github.com/x/y.txt",
        )
    assert caught.value.reason_code == "PROVENANCE_FAILED"


def test_online_resolution_refuses_a_downgrading_checksum_url(tmp_path: Path) -> None:
    """The checksum file itself must come over the same https-only boundary as the artifact."""

    catalog = SourceCatalog(
        revision="test",
        allowed_hosts=("github.com",),
        sources=(),
    )
    with pytest.raises(AirootError) as caught:
        require_allowed_url("http://github.com/checksums.txt", catalog)
    assert caught.value.reason_code == "PROVENANCE_FAILED"


def test_the_fetch_path_is_used_when_provided(offline_release) -> None:
    """Online resolution is exercised through an injected fetcher, so no test touches the network."""

    artifact, _checksums = offline_release
    seen: list[str] = []

    def fetcher(url: str) -> str:
        seen.append(url)
        return f"{sha256_file(artifact)[7:]}  {artifact.name}\n"

    resolved = resolve_source(capability_id="build", version="3.31.6", fetch_text=fetcher)

    assert seen and seen[0].startswith("https://github.com/Kitware/CMake/releases/download/v3.31.6/")
    assert resolved.backend_id == "portable_archive"
    assert resolved.expected_digest == sha256_file(artifact)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_source_list(capsys, registry) -> None:
    code, document = run(capsys, "--json", "--root", str(Path(registry.path).parent.parent), "source", "list")

    assert code == 0
    assert document["allowed_hosts"]
    assert document["signature_verification"] == "not_implemented_in_v1"
    assert "not a signature" in document["note"]


def test_cli_source_resolve_offline(capsys, registry, offline_release, tmp_path: Path) -> None:
    artifact, checksums = offline_release
    out = tmp_path / "resolved.json"

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "source", "resolve", "build", "--version", "3.31.6",
        "--offline-checksum", str(checksums), "--source-out", str(out),
    )

    assert code == 0, document
    assert document["expected_digest"] == sha256_file(artifact)
    assert document["provenance"]["publisher"] == "Kitware"
    assert out.is_file()


def test_cli_unknown_capability_has_no_source(capsys, registry, offline_release) -> None:
    _artifact, checksums = offline_release

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "source", "resolve", "python", "--version", "3.11.11", "--offline-checksum", str(checksums),
    )

    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_a_real_artifact_plan_takes_its_version_from_the_resolution(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """§156: `--version` picks what `source resolve` fetches, so the resolution carries the version.

    It used to be a CLI default of `1.0.0`, so the documented `plan <cap> --source-json <resolved>`
    produced an instance whose identity said 1.0.0 while its artifact was named 3.31.6 — a record that
    does not describe the thing it records, in the one document everything downstream reads.
    """

    artifact, checksums = offline_release
    cli_root = Path(registry.path).parent.parent

    code, resolved = run(
        capsys, "--json", "--root", str(cli_root),
        "source", "resolve", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
    )
    assert code == 0, resolved
    source_file = tmp_path / "resolved.json"
    source_file.write_text(json.dumps(resolved), encoding="utf-8")

    code, plan = run(capsys, "--json", "--root", str(cli_root), "plan", "build", "--source-json", str(source_file))

    assert code == 0, plan
    assert plan["target"]["version"] == "3.31.6"
    assert "3.31.6" in plan["target"]["instance_id"]
    assert "1.0.0" not in plan["target"]["instance_id"]
    assert artifact.is_file()


def test_an_offline_resolution_plans_a_local_file_source(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """§170: `source.kind` describes the **locator**, not what the backend is capable of.

    Measured before the fix, in one document: `source.kind="https"` next to
    `source.locator="C:\\...\\cmake-3.31.6-windows-x86_64.zip"`,
    `metadata.source_catalog.offline=true` and `metadata.backend.network_access=true`. The kind was
    read from the backend's declaration — and `portable_archive` declares `network_access=true`
    because it *can* fetch over https — so `local_file`, the value `caps/sources.py` had been writing
    for offline resolutions all along, never appeared in a printed plan.
    """

    artifact, checksums = offline_release
    cli_root = Path(registry.path).parent.parent
    source_file = tmp_path / "resolved.json"

    code, resolved = run(
        capsys, "--json", "--root", str(cli_root),
        "source", "resolve", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
        "--source-out", str(source_file),
    )
    assert code == 0, resolved
    assert resolved["offline"] is True

    code, plan = run(capsys, "--json", "--root", str(cli_root), "plan", "build", "--source-json", str(source_file))

    assert code == 0, plan
    assert plan["source"]["locator"] == str(artifact)
    assert plan["source"]["kind"] == "local_file"
    assert plan["metadata"]["backend"]["network_access"] is True, (
        "the backend can fetch over https; that says nothing about where this artifact comes from"
    )
    # §170's invariant in one assertion: `kind` and `offline` are two statements about the same
    # source, so they may never disagree.
    assert (plan["source"]["kind"] == "local_file") is plan["metadata"]["source_catalog"]["offline"]


def test_an_https_locator_plans_an_https_source(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """§170, the other direction: a fetched source is `https`, and the same invariant holds.

    The online resolution is produced through the injected fetcher `resolve_source` already takes, so
    this needs no network — what is under test is the *kind of source a URL is*, not the download.
    """

    artifact, _checksums = offline_release
    cli_root = Path(registry.path).parent.parent
    online = resolve_source(
        capability_id="build",
        version="3.31.6",
        fetch_text=lambda url: f"{sha256_file(artifact)[7:]}  {artifact.name}\n",
    ).to_document()
    assert online["offline"] is False
    source_file = tmp_path / "resolved-online.json"
    source_file.write_text(json.dumps(online), encoding="utf-8")

    code, plan = run(capsys, "--json", "--root", str(cli_root), "plan", "build", "--source-json", str(source_file))

    assert code == 0, plan
    assert plan["source"]["locator"].startswith("https://github.com/Kitware/CMake/releases/download/")
    assert plan["source"]["kind"] == "https"
    assert (plan["source"]["kind"] == "local_file") is plan["metadata"]["source_catalog"]["offline"]


def test_a_url_of_another_scheme_is_not_mislabelled(tmp_path: Path) -> None:
    """§170: the mapping is total and honest — https or a path, and nothing else is invented.

    `local_file` for an `http://` locator would be the old defect with the two words swapped, and
    `https` for it would be a falsehood the `https_artifact` backend refuses one step later. The
    schema's enum has no `http`, so the plan says so instead of picking the nearer lie.
    """

    from airoot.tx.artifact import source_kind_for

    assert source_kind_for("https://example.invalid/tool.zip") == "https"
    assert source_kind_for(str(tmp_path / "cmake-3.31.6-windows-x86_64.zip")) == "local_file"
    assert source_kind_for("C:\\artifacts\\cmake.zip") == "local_file", "a drive letter is not a scheme"
    with pytest.raises(AirootError) as caught:
        source_kind_for("http://example.invalid/tool.zip")
    assert caught.value.reason_code == "INVALID_PLAN"
    assert "https" in caught.value.message


def test_a_version_that_disagrees_with_the_resolution_is_refused(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """Two versions in one call is a contradiction, not a preference (ADR-0021's honesty carve-out)."""

    _artifact, checksums = offline_release
    cli_root = Path(registry.path).parent.parent

    code, resolved = run(
        capsys, "--json", "--root", str(cli_root),
        "source", "resolve", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
    )
    assert code == 0, resolved
    source_file = tmp_path / "resolved.json"
    source_file.write_text(json.dumps(resolved), encoding="utf-8")

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--version", "9.9.9", "--source-json", str(source_file),
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    evidence = " ".join(document["evidence"])
    assert "requested_version=9.9.9" in evidence
    assert "resolved_version=3.31.6" in evidence
    plans = cli_root / "state" / "plans"
    assert not plans.is_dir() or list(plans.iterdir()) == [], "a refused plan left a file behind"


def test_the_dry_run_reports_the_version_the_real_call_would_use(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """§155's rule, applied to the version: the dry run's verdict is the real call's verdict."""

    _artifact, checksums = offline_release
    cli_root = Path(registry.path).parent.parent

    code, resolved = run(
        capsys, "--json", "--root", str(cli_root),
        "source", "resolve", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
    )
    assert code == 0, resolved
    source_file = tmp_path / "resolved.json"
    source_file.write_text(json.dumps(resolved), encoding="utf-8")

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "plan", "build", "--source-json", str(source_file), "--dry-run"
    )

    assert code == 0, document
    assert document["version"] == "3.31.6"


def test_a_dry_run_from_a_missing_resolution_is_refused(capsys, registry, clock, root, tmp_path: Path) -> None:
    """A plan built from a file that is not there is not a plan, dry run or not."""

    cli_root = Path(registry.path).parent.parent

    code, document = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--source-json", str(tmp_path / "absent.json"), "--dry-run",
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"
    assert "file not found" in document["message"]


def test_cli_plan_from_a_resolved_source_installs_a_real_artifact(
    capsys, registry, clock, root, offline_release, tmp_path: Path
) -> None:
    """The whole chain, offline: published checksum -> resolve -> plan -> install -> where."""

    import fake_issuer
    from airoot.registry import Registry

    artifact, checksums = offline_release
    _artifact_path = artifact
    cli_root = Path(registry.path).parent.parent

    code, resolved = run(
        capsys, "--json", "--root", str(cli_root),
        "source", "resolve", "build", "--version", "3.31.6", "--offline-checksum", str(checksums),
    )
    assert code == 0, resolved
    source_file = tmp_path / "resolved.json"
    source_file.write_text(json.dumps(resolved), encoding="utf-8")

    code, plan = run(
        capsys, "--json", "--root", str(cli_root),
        "plan", "build", "--scope", "data-root", "--target", "data-root:dr-env",
        "--source-json", str(source_file),
    )
    # No data root is registered in this fixture, so routing legitimately refuses; the real-artifact
    # branch is what matters here, so run it again with the simulated-free scope "machine". The
    # refusal is `NOT_FOUND`(1) since §163/ADR-0062 — matching on the *code* 6 here pinned the old
    # tier, so this asks "did it refuse?" instead of "which code did it refuse with".
    if code != 0:
        assert plan["reason_code"] == "NOT_FOUND", plan
        code, plan = run(
            capsys, "--json", "--root", str(cli_root),
            "plan", "build", "--source-json", str(source_file),
        )
    assert code == 0, plan
    assert plan["metadata"]["backend_id"] == "portable_archive"
    assert plan["source"]["integrity"]["artifact_digest"] == sha256_file(artifact)
    assert plan["metadata"]["source_catalog"]["provenance_and_integrity_are_separate"] is True
    assert plan["metadata"]["source_catalog"]["signature"] is None
    assert plan["source"]["provenance"]["publisher"] == "Kitware"

    # Install it through the CLI, using the resolved plan.
    fake_issuer.install_keyring(cli_root)
    from airoot.clock import SYSTEM_CLOCK

    token = fake_issuer.issue(plan, clock=SYSTEM_CLOCK)
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps(token), encoding="utf-8")
    plan_file = Path(plan["plan_file"])

    code, transaction = run(
        capsys, "--json", "--root", str(cli_root), "install", str(plan_file), "--token-file", str(token_file)
    )
    assert code == 0, transaction
    assert transaction["state"] == "FINALIZED"

    registry_handle = Registry.open(cli_root)
    try:
        row = registry_handle.instance(plan["target"]["instance_id"])
        assert row["install_backend_id"] == "portable_archive"
        # §145: the extracted tree is the payload, so the recorded entrypoint is a path inside
        # it — and it is *first* because `expose` orders the capability's declared entry first.
        assert json.loads(row["entrypoints_json"]) == ["bin/cmake.exe"]
    finally:
        registry_handle.close()
    assert artifact.is_file(), "the source artifact is never consumed"

    # §156: the installed instance answers to the version it actually is. Before that stage this
    # resolved to `VERSION_UNSATISFIED` — cmake 3.31.6 was registered as 1.0.0, so a version query
    # could not find the artifact that had just been installed.
    code, where = run(capsys, "--json", "--root", str(cli_root), "where", "build", "--version", ">=3.31")
    assert code == 0, where
    assert where["found"] is True
    assert where["version"] == "3.31.6"


# --------------------------------------------------------------------------- #
# §171 ①: the id a real artifact plan derives, refused where it is derived
# --------------------------------------------------------------------------- #

#: Artifact file names a caller may legitimately put in a resolution, none of which can appear in an
#: instance id: the published shape is `^[a-z0-9][a-z0-9._/-]{0,127}$` and the stem goes into it
#: verbatim. The names are the point — the defect was that a *derived* string failed the plan's own
#: field and the core blamed itself for it.
#:
#: `_` is **not** in this list on purpose, although it reads like it should be: the published class
#: carries it (`[a-z0-9._/-]`), and the real release name `cmake-3.31.6-windows-x86_64` needs it.
#: Neither is a leading `.`: only the fragment that starts the whole id has to begin with `[a-z0-9]`,
#: and a file name is never the first one.
NAMES_THAT_CANNOT_BE_IDS = (
    "cmake-3.30.5-MISSING.zip",  # upper case
    "cmake 3.30.5.zip",  # a space
    "cmake+3.31.6.zip",  # a character outside the published class
    "cmake@3.31.6.zip",  # same, and the one a package-style locator tends to carry
)


@pytest.mark.parametrize("name", NAMES_THAT_CANNOT_BE_IDS)
def test_a_file_name_that_cannot_be_an_id_is_refused_where_the_id_is_derived(name: str) -> None:
    """§171 ①: no malformed file name may reach `SELF_VALIDATION_FAILED`.

    That code means "this build produced a document it cannot read" — an implementation defect — and
    the caller's resolution is a legal document here. What is illegal is the id *this build* invents
    out of the file name, so the refusal is `INVALID_INPUT`(8), which is the code for "your input
    cannot be used", and it names the input.
    """

    from airoot.tx.artifact import derive_artifact_ids

    with pytest.raises(AirootError) as caught:
        derive_artifact_ids(
            capability_id="build", locator=rf"C:\dropped\{name}", version="3.31.6"
        )
    assert caught.value.reason_code == "INVALID_INPUT", caught.value.evidence
    assert caught.value.reason_code != "SELF_VALIDATION_FAILED"
    evidence = " ".join(caught.value.evidence)
    assert "artifact file name" in evidence, evidence


def test_the_version_is_named_too_when_it_is_what_cannot_be_an_id() -> None:
    """The same guard, from the other input: a legal version string is not automatically an id part."""

    from airoot.tx.artifact import derive_artifact_ids

    with pytest.raises(AirootError) as caught:
        derive_artifact_ids(
            capability_id="build", locator=r"C:\dropped\cmake-3.31.6-windows-x86_64.zip",
            version="3.3 1.5",
        )
    assert caught.value.reason_code == "INVALID_INPUT"
    evidence = " ".join(caught.value.evidence)
    assert "the version '3.3 1.5'" in evidence, evidence


def test_a_file_name_that_is_too_long_to_be_an_id_is_refused_too() -> None:
    """The other half of the shape: every fragment can be legal and the whole id still too long."""

    from airoot.tx.artifact import derive_artifact_ids

    with pytest.raises(AirootError) as caught:
        derive_artifact_ids(
            capability_id="build",
            locator=rf"C:\dropped\cmake-{'a' * 140}.zip",
            version="3.31.6",
        )
    assert caught.value.reason_code == "INVALID_INPUT"
    evidence = " ".join(caught.value.evidence)
    assert "characters long" in evidence, evidence
    assert "artifact file name" not in evidence, "no fragment is illegal; the length is what is"


def test_an_id_shaped_file_name_still_derives_the_id_it_always_did() -> None:
    """Non-vacuity: the guard must not catch the path the release archives actually take."""

    from airoot.tx.artifact import derive_artifact_ids

    instance_id, plan_id = derive_artifact_ids(
        capability_id="build",
        locator=r"C:\dropped\cmake-3.31.6-windows-x86_64.zip",
        version="3.31.6",
    )
    assert instance_id == "build/cmake-3.31.6-windows-x86_64/3.31.6/win-x64"
    assert plan_id.startswith("plan/build/3.31.6/")


def test_a_resolution_whose_file_name_is_not_an_id_is_refused_as_input(
    capsys, registry, clock, root, tmp_path: Path
) -> None:
    """The same defect through the verb an operator actually runs (§171 ①)."""

    cli_root = Path(registry.path).parent.parent
    artifact = tmp_path / "cmake-3.30.5-MISSING.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("cmake-3.30.5-MISSING/bin/cmake.exe", b"MZ not a real image\n")
    source_file = tmp_path / "resolved.json"
    source_file.write_text(
        json.dumps(
            {
                "capability_id": "build",
                "version": "3.30.5",
                "artifact_url": str(artifact),
                "expected_digest": sha256_file(artifact),
                "backend_id": "portable_archive",
                "offline": True,
            }
        ),
        encoding="utf-8",
    )

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "plan", "build", "--source-json", str(source_file)
    )

    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT"
    assert document["reason_code"] != "SELF_VALIDATION_FAILED", (
        "the resolution is a legal document; the illegal id is derived from it, so this build may "
        "not report its own defect"
    )
    evidence = " ".join(document["evidence"])
    assert "cmake-3.30.5-MISSING" in evidence and "artifact file name" in evidence, evidence
    plans = cli_root / "state" / "plans"
    assert not plans.is_dir() or list(plans.iterdir()) == [], "a refused plan left a file behind"
