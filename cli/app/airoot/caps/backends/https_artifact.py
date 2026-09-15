"""``https_artifact``: fetch one script-free artifact over HTTPS and verify its digest.

This is the backend ADR-0001 needs (the Rust toolchain: ``rustup-init.exe`` + ``SHA256SUMS``
over HTTPS, no script, no admin). It is deliberately narrow:

* **https only** — a plaintext source is refused outright, and a redirect that lands anywhere
  but ``https`` is refused too, because a silent downgrade is a supply-chain entry point;
* **the digest is mandatory** — ``verify`` compares SHA256 against the plan, and a mismatch is
  ``DIGEST_MISMATCH`` before anything reaches the store;
* **bounded** — a declared maximum size and a timeout, so a hostile or broken server cannot
  fill the disk;
* **no script execution** — the bytes are written and hashed, never run.

v1 does no signature verification (that is a later layer, §4.4:368); it does *not* pretend that
a digest is a signature.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...exits import AirootError
from .base import (
    Artifact,
    BackendDeclaration,
    VerifyResult,
    assert_script_free,
    sha256_file,
)

BACKEND_ID = "https_artifact"

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
CHUNK = 1024 * 1024

DECLARATION = BackendDeclaration(
    backend_id=BACKEND_ID,
    required_privilege="none",
    network_access=True,
    executes_scripts=False,
    reversible=True,
    reboot_required=False,
    estimated_size=None,
    source_mutation="none",
    supports_resume=False,
    failure_cleanup="stage_only",
)


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect whose target is not https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        parsed = urlparse(newurl)
        if parsed.scheme != "https":
            raise AirootError(
                "PROVENANCE_FAILED",
                f"refusing a redirect to a non-https URL: {newurl}",
                evidence=["a silent downgrade to plaintext is treated as a supply-chain failure"],
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_HttpsOnlyRedirectHandler())


class HttpsArtifactBackend:
    declaration = DECLARATION

    def __init__(
        self,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        opener: Any = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._opener = opener

    def opener(self) -> Any:
        return self._opener if self._opener is not None else _opener()

    def discover(self) -> list[dict[str, Any]]:
        return []

    def plan(self, *, capability_id: str, version: str) -> dict[str, Any]:
        return {
            "backend_id": BACKEND_ID,
            "capability_id": capability_id,
            "version": version,
            "network_access": True,
            "instructions": "pass an https:// locator and its sha256 digest in the plan source",
        }

    # ------------------------------------------------------------------ bytes #

    def fetch(self, *, locator: str, destination: Path) -> Artifact:
        parsed = urlparse(locator)
        if parsed.scheme != "https":
            raise AirootError(
                "PROVENANCE_FAILED",
                f"only https sources are accepted, got {parsed.scheme or 'no scheme'!r}",
                evidence=[f"locator={locator}", "plaintext and file:// sources have their own backends"],
            )
        if not parsed.netloc:
            raise AirootError("INVALID_INPUT", f"the https source has no host: {locator}")
        assert_script_free(Path(parsed.path or locator))

        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(locator, headers={"User-Agent": "airoot/1"})
        digest = None
        size = 0
        try:
            with self.opener().open(request, timeout=self.timeout_seconds) as response:
                with open(destination, "wb") as handle:
                    while True:
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise AirootError(
                                "INVALID_INPUT",
                                f"the artifact exceeds this backend's limit ({self.max_bytes} bytes)",
                                evidence=[f"locator={locator}"],
                            )
                        handle.write(chunk)
        except AirootError:
            if destination.exists():
                destination.unlink()
            raise
        except (urllib.error.URLError, OSError, ValueError) as error:
            if destination.exists():
                destination.unlink()
            raise AirootError(
                "PROVENANCE_FAILED",
                f"could not fetch {locator}",
                evidence=[str(error)],
            ) from error
        if size == 0:
            destination.unlink(missing_ok=True)
            raise AirootError("PROVENANCE_FAILED", f"the server returned an empty body: {locator}")

        digest = sha256_file(destination)
        return Artifact(path=destination, digest=digest, size=size, fetched_from=locator)

    def verify(self, artifact: Artifact, *, expected_digest: str) -> VerifyResult:
        digest = sha256_file(artifact.path)
        problems: list[str] = []
        if not expected_digest or not expected_digest.startswith("sha256:"):
            problems.append(f"the plan digest is not a sha256 digest: {expected_digest!r}")
        if digest != expected_digest:
            problems.append("the downloaded bytes do not match the plan digest")
        return VerifyResult(
            ok=not problems and digest == expected_digest,
            digest=digest,
            size=artifact.path.stat().st_size if artifact.path.is_file() else 0,
            problems=problems,
        )

    def stage(self, artifact: Artifact, *, stage_dir: Path) -> Path:
        stage_dir.mkdir(parents=True, exist_ok=True)
        target = stage_dir / artifact.path.name
        shutil.copy2(artifact.path, target)
        return target

    def commit(self, stage_dir: Path, *, store_dir: Path) -> Path:
        from .portable_file import PortableFileBackend

        # The store layout is backend-independent on purpose: `where`, `doctor` and `gc` must not
        # need to know which backend produced a payload.
        return PortableFileBackend().commit(stage_dir, store_dir=store_dir)

    def expose(self, store_dir: Path) -> list[str]:
        from .portable_file import PortableFileBackend

        return PortableFileBackend().expose(store_dir)

    def inspect(self, store_dir: Path) -> dict[str, Any]:
        from .portable_file import PortableFileBackend

        return PortableFileBackend().inspect(store_dir)

    def rollback(self, store_dir: Path) -> None:
        return None


__all__ = [
    "BACKEND_ID",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "DECLARATION",
    "HttpsArtifactBackend",
]
