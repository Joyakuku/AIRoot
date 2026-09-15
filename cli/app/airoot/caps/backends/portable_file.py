"""``portable_file``: a local, script-free, single-artifact backend (v1 baseline).

This is the smallest backend that can create a *real* owned instance, and it is deliberately the
one that cannot touch the network: the artifact is a file the user already has, verified against
the digest recorded in the plan. Everything the contract demands of a v1 backend holds by
construction — no scripts, no source mutation, no privilege, reversible, stage-only cleanup.

It exists because "owned" has to be reachable without a network: without it, `gc`, `retire` and
the whole deletion grading (§14) could only ever be exercised against the fake fixture.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...exits import AirootError
from .base import (
    Artifact,
    BackendDeclaration,
    VerifyResult,
    assert_script_free,
    sha256_file,
)

BACKEND_ID = "portable_file"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB: a bound, not a policy

DECLARATION = BackendDeclaration(
    backend_id=BACKEND_ID,
    required_privilege="none",
    network_access=False,
    executes_scripts=False,
    reversible=True,
    reboot_required=False,
    estimated_size=None,
    source_mutation="none",
    supports_resume=False,
    failure_cleanup="stage_only",
)


class PortableFileBackend:
    """Fetch = read a local file; commit = move it into the immutable store."""

    declaration = DECLARATION

    def discover(self) -> list[dict[str, Any]]:
        """Nothing to discover: this backend only ever serves an explicit locator."""

        return []

    def plan(self, *, capability_id: str, version: str) -> dict[str, Any]:
        return {
            "backend_id": BACKEND_ID,
            "capability_id": capability_id,
            "version": version,
            "network_access": False,
            "instructions": "pass the artifact's path and its sha256 in the plan source",
        }

    # ------------------------------------------------------------------ bytes #

    def fetch(self, *, locator: str, destination: Path) -> Artifact:
        """Read the artifact. The source is only ever read — never moved or deleted."""

        source = Path(locator)
        if not source.is_file():
            raise AirootError(
                "NOT_FOUND",
                f"the artifact does not exist: {source}",
                evidence=["portable_file takes an existing local file as its source"],
            )
        assert_script_free(source)
        size = source.stat().st_size
        if size == 0:
            raise AirootError("INVALID_INPUT", f"the artifact is empty: {source}")
        if size > MAX_ARTIFACT_BYTES:
            raise AirootError(
                "INVALID_INPUT",
                f"the artifact is larger than this backend accepts ({size} bytes)",
                evidence=[f"limit={MAX_ARTIFACT_BYTES}"],
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return Artifact(
            path=destination,
            digest=sha256_file(destination),
            size=size,
            fetched_from=str(source),
        )

    def verify(self, artifact: Artifact, *, expected_digest: str) -> VerifyResult:
        digest = sha256_file(artifact.path)
        problems: list[str] = []
        if not expected_digest or not expected_digest.startswith("sha256:"):
            problems.append(f"the plan digest is not a sha256 digest: {expected_digest!r}")
        if digest != expected_digest:
            problems.append("the file's bytes do not match the plan digest")
        return VerifyResult(
            ok=not problems and digest == expected_digest,
            digest=digest,
            size=artifact.path.stat().st_size if artifact.path.is_file() else 0,
            problems=problems,
        )

    def stage(self, artifact: Artifact, *, stage_dir: Path) -> Path:
        """Materialise the entrypoint under the transaction's stage directory."""

        stage_dir.mkdir(parents=True, exist_ok=True)
        target = stage_dir / artifact.path.name
        shutil.copy2(artifact.path, target)
        return target

    def commit(self, stage_dir: Path, *, store_dir: Path) -> Path:
        """Move the staged payload into the immutable store (same volume, atomic rename)."""

        if store_dir.exists():
            raise AirootError(
                "INSTANCE_CONFLICT",
                f"the store path is already occupied: {store_dir}",
                evidence=["an existing payload is never overwritten; a new instance gets a new id"],
            )
        store_dir.parent.mkdir(parents=True, exist_ok=True)
        source = self._entrypoint(stage_dir)
        if source is None:
            raise AirootError("INVALID_INPUT", f"nothing staged in {stage_dir}")
        # A single-file payload lands as store/<instance>/<name>, mirroring the fake backend's
        # layout so `where`, `doctor` and `gc` treat both backends identically.
        store_dir.mkdir(parents=True)
        shutil.move(str(source), str(store_dir / source.name))
        return store_dir / source.name

    def expose(self, store_dir: Path) -> list[str]:
        """Report entrypoint names. Exposure itself belongs to the transaction, not here."""

        return sorted(item.name for item in store_dir.iterdir() if item.is_file())

    def inspect(self, store_dir: Path) -> dict[str, Any]:
        entrypoints = self.expose(store_dir)
        if not entrypoints:
            return {"present": store_dir.is_dir(), "entrypoints": [], "health": "broken"}
        return {
            "present": True,
            "entrypoints": entrypoints,
            "size": sum(item.stat().st_size for item in store_dir.iterdir() if item.is_file()),
            "health": "healthy",
        }

    def rollback(self, store_dir: Path) -> None:
        """Rollback keeps the payload as evidence; the transaction switches the binding."""

        return None

    # ----------------------------------------------------------------- helpers #

    @staticmethod
    def _entrypoint(stage_dir: Path) -> Path | None:
        files = sorted(item for item in stage_dir.iterdir() if item.is_file())
        return files[0] if files else None
