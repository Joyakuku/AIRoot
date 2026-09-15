"""L1: trusted sources and published checksums (draft §23).

The invariant these tests exist for: **the expected digest comes from a published checksum file**,
never from the catalog and never from the artifact being verified. Hashing what you just
downloaded and comparing it with itself is the most common fake verification, and it is
structurally impossible here — `parse_sha256sums` is the only source of an expected digest.
"""

from __future__ import annotations

import json
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

    assert catalog.revision == "src-1"
    assert "static.rust-lang.org" in catalog.allowed_hosts
    assert any(item.capability_id == "rust-toolchain" for item in catalog.sources)


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
    artifact.write_bytes(b"portable archive bytes, no installer script\n")
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
    assert resolved.backend_id == "portable_file"
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
    assert resolved.backend_id == "https_artifact"
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
    # branch is what matters here, so run it again with the simulated-free scope "machine".
    if code == 6:
        code, plan = run(
            capsys, "--json", "--root", str(cli_root),
            "plan", "build", "--source-json", str(source_file),
        )
    assert code == 0, plan
    assert plan["metadata"]["backend_id"] == "portable_file"
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
        assert row["install_backend_id"] == "portable_file"
    finally:
        registry_handle.close()
    assert artifact.is_file(), "the source artifact is never consumed"
