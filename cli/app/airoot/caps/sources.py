"""Trusted sources and published checksums (draft §23, 三大核心契约 §4.4:368).

The install backend (§21) can fetch and hash bytes, but it has to be *told* the expected digest.
This module is where that digest comes from, and the whole design exists to prevent the classic
fake verification — hashing what you just downloaded and comparing it with itself:

* the **expected digest is parsed out of a published checksum file**, never taken from the
  catalog and never computed from the artifact being checked (`parse_sha256sums` is the only
  source of an expected digest);
* **provenance and integrity stay separate**: `source_id`/`publisher` come from the catalog,
  `integrity.artifact_digest` comes from the checksum file. They are guaranteed by different
  mechanisms, and merging them hides "trusted publisher, swapped bytes";
* **the host allowlist is the point**: an arbitrary https host only proves the channel was
  encrypted, not that the publisher is anyone in particular.

v1 has no signature verification. `signature` stays null, and a digest is never described as a
signature — that would be worse than admitting there is none.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .. import CLI_ROOT
from ..exits import AirootError
from .backends.base import sha256_file

SOURCES_PATH = CLI_ROOT / "app" / "airoot" / "policy" / "sources.json"

CHECKSUM_FORMATS = ("sha256sums", "single")

# `sha256sum` output: "<64 hex><space><space-or-*><name>". BSD style ("SHA256 (name) = hash")
# is accepted too, because "the publisher chose the other spelling" is not a reason to guess.
_GNU_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?(.+?)\s*$")
_BSD_LINE = re.compile(r"^SHA256\s*\((.+?)\)\s*=\s*([0-9a-fA-F]{64})\s*$")

_ALLOWED_KEYS = frozenset({"schema_version", "revision", "sources", "allowed_hosts", "notes"})
_ALLOWED_SOURCE_KEYS = frozenset(
    {"capability_id", "kind", "artifact_url", "checksum", "filename", "publisher", "notes"}
)
_ALLOWED_CHECKSUM_KEYS = frozenset({"kind", "url", "format"})

#: The substitutions `resolve_source` performs. A template may use only these: `str.format` raises
#: on anything else, so a typo like `{ver}` would surface only at the moment somebody tried to
#: install that capability — a URL with a literal `{ver}` in it, rather than an error about the
#: catalog. Validated at load time for the same reason the checksum format is.
SUBSTITUTION_KEYS = ("version", "version_nodots", "arch")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def _validate_placeholders(capability_id: str, entry: "SourceEntry") -> None:
    """Every template may name only a substitution `resolve_source` actually performs."""

    templates = (
        ("artifact_url", entry.artifact_url),
        ("checksum.url", entry.checksum.url),
        ("filename", entry.filename),
    )
    for name, template in templates:
        for placeholder in _PLACEHOLDER.findall(template):
            if placeholder not in SUBSTITUTION_KEYS:
                raise AirootError(
                    "PROVENANCE_FAILED",
                    f"source {capability_id} uses an unknown placeholder {{{placeholder}}} in {name}",
                    evidence=[
                        f"known placeholders: {', '.join(SUBSTITUTION_KEYS)}",
                        "an unknown one is not substituted, so the URL would be wrong at fetch time",
                    ],
                )


@dataclass(frozen=True)
class ChecksumSource:
    kind: str
    url: str
    format: str = "sha256sums"

    def to_document(self) -> dict[str, Any]:
        return {"kind": self.kind, "url": self.url, "format": self.format}


@dataclass(frozen=True)
class SourceEntry:
    capability_id: str
    kind: str
    artifact_url: str
    checksum: ChecksumSource
    filename: str
    publisher: str = ""
    notes: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind,
            "artifact_url": self.artifact_url,
            "filename": self.filename,
            "publisher": self.publisher,
            "checksum": self.checksum.to_document(),
        }


@dataclass(frozen=True)
class SourceCatalog:
    revision: str
    allowed_hosts: tuple[str, ...]
    sources: tuple[SourceEntry, ...] = ()

    def by_capability(self, capability_id: str) -> SourceEntry | None:
        return next((item for item in self.sources if item.capability_id == capability_id), None)

    def hosts(self) -> tuple[str, ...]:
        return self.allowed_hosts


@dataclass
class ResolvedSource:
    """Everything a plan needs, with the two guarantees kept visibly apart."""

    capability_id: str
    version: str
    source: dict[str, Any]
    backend_id: str
    expected_digest: str
    artifact_url: str
    checksum_url: str
    checksum_format: str
    publisher: str | None = None
    offline: bool = False
    evidence: list[str] = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "capability_id": self.capability_id,
            "version": self.version,
            "backend_id": self.backend_id,
            "expected_digest": self.expected_digest,
            "artifact_url": self.artifact_url,
            "checksum_url": self.checksum_url,
            "checksum_format": self.checksum_format,
            "offline": self.offline,
            "provenance": {
                "source_id": self.source["provenance"]["source_id"],
                "publisher": self.source["provenance"]["publisher"],
            },
            "integrity": {"artifact_digest": self.expected_digest},
            "note": "provenance and integrity come from different mechanisms; a digest is not a signature",
            "evidence": list(self.evidence),
            "reason_code": "SUCCESS",
        }


# --------------------------------------------------------------------------- #
# catalog loading
# --------------------------------------------------------------------------- #


def load_sources(path: Path | None = None) -> SourceCatalog:
    target = Path(path) if path is not None else SOURCES_PATH
    if not target.is_file():
        raise AirootError(
            "PROVENANCE_FAILED",
            f"the trusted source catalog is missing: {target}",
            evidence=["without it no download can be attributed, so nothing may be installed"],
        )
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(
            "PROVENANCE_FAILED", f"the source catalog is unreadable: {target}", evidence=[str(exc)]
        ) from exc
    if not isinstance(document, dict):
        raise AirootError("PROVENANCE_FAILED", f"the source catalog is not an object: {target}")
    unknown = sorted(set(document) - _ALLOWED_KEYS)
    if unknown:
        raise AirootError(
            "PROVENANCE_FAILED",
            f"source catalog has unknown keys: {', '.join(unknown)}",
            evidence=[f"allowed: {', '.join(sorted(_ALLOWED_KEYS))}"],
        )

    hosts = tuple(str(item).lower() for item in document.get("allowed_hosts", []))
    if not hosts:
        raise AirootError(
            "PROVENANCE_FAILED",
            "the source catalog has no allowed_hosts",
            evidence=["an allowlist is what makes 'verified origin' mean something"],
        )

    entries: list[SourceEntry] = []
    seen: set[str] = set()
    for item in document.get("sources", []):
        if not isinstance(item, dict):
            raise AirootError("PROVENANCE_FAILED", "every source entry must be an object")
        extra = sorted(set(item) - _ALLOWED_SOURCE_KEYS)
        if extra:
            raise AirootError(
                "PROVENANCE_FAILED",
                f"source entry has unknown keys: {', '.join(extra)}",
                evidence=[f"entry={item.get('capability_id')}"],
            )
        capability_id = str(item.get("capability_id", ""))
        if not capability_id:
            raise AirootError("PROVENANCE_FAILED", "a source entry has no capability_id")
        if capability_id in seen:
            raise AirootError("PROVENANCE_FAILED", f"capability {capability_id} has two source entries")
        seen.add(capability_id)
        checksum_document = item.get("checksum")
        if not isinstance(checksum_document, dict):
            raise AirootError(
                "PROVENANCE_FAILED",
                f"source {capability_id} declares no checksum source",
                evidence=[
                    "an expected digest must come from a published checksum file",
                    "hashing the downloaded artifact and comparing it with itself verifies nothing",
                ],
            )
        checksum_extra = sorted(set(checksum_document) - _ALLOWED_CHECKSUM_KEYS)
        if checksum_extra:
            raise AirootError(
                "PROVENANCE_FAILED",
                f"source {capability_id} checksum has unknown keys: {', '.join(checksum_extra)}",
            )
        checksum_format = str(checksum_document.get("format", "sha256sums"))
        if checksum_format not in CHECKSUM_FORMATS:
            raise AirootError(
                "PROVENANCE_FAILED",
                f"source {capability_id} declares an unknown checksum format: {checksum_format}",
                evidence=[f"known: {', '.join(CHECKSUM_FORMATS)}"],
            )
        entry = SourceEntry(
            capability_id=capability_id,
            kind=str(item.get("kind", "https")),
            artifact_url=str(item.get("artifact_url", "")),
            checksum=ChecksumSource(
                kind=str(checksum_document.get("kind", "https")),
                url=str(checksum_document.get("url", "")),
                format=checksum_format,
            ),
            filename=str(item.get("filename", "")),
            publisher=str(item.get("publisher", "")),
            notes=str(item.get("notes", "")),
        )
        if not entry.artifact_url or not entry.checksum.url:
            raise AirootError(
                "PROVENANCE_FAILED", f"source {capability_id} is missing an artifact or checksum URL"
            )
        _validate_placeholders(capability_id, entry)
        entries.append(entry)
    return SourceCatalog(
        revision=str(document.get("revision", "src-0")), allowed_hosts=hosts, sources=tuple(entries)
    )


# --------------------------------------------------------------------------- #
# host policy
# --------------------------------------------------------------------------- #


def require_allowed_url(url: str, catalog: SourceCatalog, *, offline: bool = False) -> str:
    """Return the URL if it is https and on the allowlist; otherwise refuse with a reason."""

    if offline:
        path = Path(url)
        if url.startswith("http://") or url.startswith("https://"):
            raise AirootError(
                "PROVENANCE_FAILED",
                f"offline resolution cannot reach a network URL: {url}",
                evidence=["pass a local checksum file when resolving offline"],
            )
        if not path.is_file():
            raise AirootError("NOT_FOUND", f"the local checksum file does not exist: {path}")
        return str(path)

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AirootError(
            "PROVENANCE_FAILED",
            f"only https sources are accepted, got {parsed.scheme or 'no scheme'!r}",
            evidence=[f"url={url}"],
        )
    host = (parsed.hostname or "").lower()
    if host not in catalog.allowed_hosts:
        raise AirootError(
            "PROVENANCE_FAILED",
            f"{host or 'the URL'} is not in the allowed host list",
            evidence=[
                f"allowed: {', '.join(catalog.allowed_hosts)}",
                "an arbitrary https host only proves the channel was encrypted",
                "to trust it, add it to policy/sources.json deliberately",
            ],
        )
    return url


# --------------------------------------------------------------------------- #
# checksum parsing
# --------------------------------------------------------------------------- #


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse a published checksum file into ``{filename: sha256:...}``.

    Accepts GNU ``sha256sum`` output and BSD ``SHA256 (name) = hash`` lines, ignores comments and
    blank lines, and tolerates CRLF — the publishers differ, and refusing a format is not the
    same as refusing a digest.
    """

    digests: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        bsd = _BSD_LINE.match(line)
        if bsd:
            name, digest = bsd.group(1).strip(), bsd.group(2)
            digests[name.lstrip("*")] = "sha256:" + digest.lower()
            continue
        gnu = _GNU_LINE.match(line)
        if gnu:
            digest, name = gnu.group(1), gnu.group(2).strip()
            digests[name] = "sha256:" + digest.lower()
    return digests


def parse_single_digest(text: str) -> dict[str, str]:
    """A checksum file that is *just* the hash (some publishers ship one per artifact)."""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split()[0]
        if re.fullmatch(r"[0-9a-fA-F]{64}", token):
            return {"": "sha256:" + token.lower()}
    return {}


def digest_for(
    checksum_text: str,
    *,
    filename: str,
    checksum_format: str = "sha256sums",
) -> str:
    """The expected digest for ``filename``, or ``NOT_FOUND`` — never a guess."""

    if checksum_format == "single":
        single = parse_single_digest(checksum_text)
        if single:
            return next(iter(single.values()))
        raise AirootError(
            "PROVENANCE_FAILED",
            "the checksum file contains no sha256 digest",
            evidence=["expected a bare 64-hex digest"],
        )
    digests = parse_sha256sums(checksum_text)
    if not digests:
        raise AirootError(
            "PROVENANCE_FAILED",
            "the checksum file contains no parseable digest lines",
            evidence=["accepted: GNU sha256sum output and BSD 'SHA256 (name) = hash'"],
        )
    if filename in digests:
        return digests[filename]
    lowered = {name.lower(): digest for name, digest in digests.items()}
    if filename.lower() in lowered:
        return lowered[filename.lower()]
    raise AirootError(
        "NOT_FOUND",
        f"the checksum file does not list {filename}",
        evidence=[
            f"listed names: {', '.join(sorted(digests)[:8])}",
            "a name mismatch is never resolved by guessing the closest entry",
        ],
    )


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def resolve_source(
    *,
    capability_id: str,
    version: str,
    catalog: SourceCatalog | None = None,
    offline_checksum_path: str | None = None,
    fetch_text: Callable[[str], str] | None = None,
    artifact_sha256: str | None = None,
) -> ResolvedSource:
    """Map ``(capability, version)`` to a concrete artifact plus its published digest.

    ``offline_checksum_path`` and ``fetch_text`` exist so the same code path serves both the
    network case and a hermetic test/air-gapped case. They change *where* the checksum file comes
    from, never *whether* one is required.
    """

    from .backends.base import BackendDeclaration  # noqa: F401  (documents the coupling)

    catalog = catalog or load_sources()
    entry = catalog.by_capability(capability_id)
    if entry is None:
        raise AirootError(
            "NOT_FOUND",
            f"no trusted source is declared for {capability_id}",
            evidence=[
                f"declared capabilities: {', '.join(item.capability_id for item in catalog.sources)}",
                "a capability without a source is a reference-only capability, not an installable one",
            ],
        )

    substitutions = {
        "version": version,
        "version_nodots": version.replace(".", ""),
        "arch": "x86_64",
    }
    artifact_url = entry.artifact_url.format(**substitutions)
    checksum_url_template = entry.checksum.url.format(**substitutions)
    filename = entry.filename.format(**substitutions)
    offline = offline_checksum_path is not None

    evidence = [
        f"catalog revision={catalog.revision}",
        f"artifact={artifact_url}",
        f"checksum_source={checksum_url_template}",
        f"expected_filename={filename}",
    ]

    if offline:
        checksum_location = require_allowed_url(offline_checksum_path or "", catalog, offline=True)
        checksum_text = Path(checksum_location).read_text(encoding="utf-8", errors="replace")
    else:
        require_allowed_url(artifact_url, catalog)
        checksum_location = require_allowed_url(checksum_url_template, catalog)
        if fetch_text is None:
            checksum_text = _fetch_text(checksum_location)
        else:
            checksum_text = fetch_text(checksum_location)

    expected = digest_for(checksum_text, filename=filename, checksum_format=entry.checksum.format)
    evidence.append(f"expected_digest={expected} (from the published checksum file)")

    if artifact_sha256 is not None and artifact_sha256 != expected:
        # Useful when the caller already has the artifact locally: it makes the mismatch a
        # planning-time fact instead of a failed transaction later.
        raise AirootError(
            "DIGEST_MISMATCH",
            "the local artifact does not match the published checksum",
            evidence=[f"published={expected}", f"local={artifact_sha256}", *evidence],
        )

    locator = Path(offline_checksum_path).parent.joinpath(filename) if offline else artifact_url
    if offline and not Path(str(locator)).is_file():
        evidence.append(f"warning: the artifact was not found next to the checksum file at {locator}")

    source = {
        "kind": "local_file" if offline else "https",
        "locator": str(locator),
        "provenance": {
            "source_id": f"source/{entry.capability_id}",
            "publisher": entry.publisher or None,
        },
        "integrity": {"artifact_digest": expected},
        "signature": None,
    }
    return ResolvedSource(
        capability_id=capability_id,
        version=version,
        source=source,
        backend_id=_backend_for(locator, offline=offline),
        expected_digest=expected,
        artifact_url=str(locator),
        checksum_url=checksum_url_template if not offline else checksum_location,
        checksum_format=entry.checksum.format,
        publisher=entry.publisher or None,
        offline=offline,
        evidence=evidence,
    )


def _backend_for(locator: Path | str, *, offline: bool) -> str:
    """Which install backend can stage this artifact (draft §144/§145).

    A ``.zip`` is a payload *tree*: staging it as a single file would put a zip in the store
    with no entrypoint to bind. Everything else keeps the single-artifact backends they were
    built for.
    """

    if Path(str(locator)).suffix.lower() == ".zip":
        return "portable_archive"
    return "portable_file" if offline else "https_artifact"


def _fetch_text(url: str) -> str:
    """Fetch a small text file (a checksum list) with the same https-only boundary as §21."""

    import urllib.error
    import urllib.request

    from .backends.https_artifact import _HttpsOnlyRedirectHandler

    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    try:
        with opener.open(url, timeout=30) as response:
            payload = response.read(1024 * 1024)
    except AirootError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AirootError(
            "PROVENANCE_FAILED", f"could not fetch the checksum file {url}", evidence=[str(exc)]
        ) from exc
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AirootError("PROVENANCE_FAILED", f"the checksum file is not decodable text: {url}")


__all__ = [
    "CHECKSUM_FORMATS",
    "ChecksumSource",
    "ResolvedSource",
    "SOURCES_PATH",
    "SourceCatalog",
    "SourceEntry",
    "digest_for",
    "load_sources",
    "parse_sha256sums",
    "parse_single_digest",
    "require_allowed_url",
    "resolve_source",
    "sha256_file",
]
