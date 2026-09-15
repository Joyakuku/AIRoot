"""The Install Backend layer: who actually moves bytes (三大核心契约 §4.4).

A backend is responsible for exactly two of the frozen steps — **fetch and commit** — plus the
declarations that let a plan be judged before anything runs. It deliberately has no way to touch
a binding: ``ACTIVE_BOUND`` remains the only commit point for active state (§决策3:47).

Two rules from the contract are enforced *here* rather than trusted to callers:

* **no scripts** — v1 only admits script-free artifacts (§决策3); a backend that would need to
  execute something must say ``executes_scripts=True`` and is then barred from the low-risk path;
* **never mutate the source** — ``source_mutation`` is always ``none``; deleting or moving a
  user's file must be an explicit, separately approved plan step (§4.4:352).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...exits import AirootError

# Script families that make an artifact an *installer* rather than a payload. A single-file
# executable is fine (jq.exe is the canonical example); a .bat/.cmd/.ps1/.sh as the payload, or a
# top-level install script inside an archive, is not (三大核心契约 决策3).
SCRIPT_SUFFIXES = (".bat", ".cmd", ".ps1", ".sh", ".bash", ".py", ".vbs", ".js")

# Steps every backend must be able to answer for, in the frozen order (§4.4:316-326).
BACKEND_OPERATIONS = (
    "discover",
    "plan",
    "fetch",
    "verify",
    "stage",
    "commit",
    "expose",
    "inspect",
    "rollback",
)

# Declarations every backend must publish (§4.4:328-340).
DECLARED_FIELDS = (
    "required_privilege",
    "network_access",
    "executes_scripts",
    "reversible",
    "reboot_required",
    "estimated_size",
    "source_mutation",
    "supports_resume",
    "failure_cleanup",
)

PRIVILEGES = ("none", "elevated")
FAILURE_CLEANUPS = ("stage_only", "manual", "none")


@dataclass(frozen=True)
class BackendDeclaration:
    """The nine frozen fields. Everything a plan needs to judge risk before running."""

    backend_id: str
    required_privilege: str = "none"
    network_access: bool = False
    executes_scripts: bool = False
    reversible: bool = True
    reboot_required: bool = False
    estimated_size: int | None = None
    source_mutation: str = "none"
    supports_resume: bool = False
    failure_cleanup: str = "stage_only"

    def __post_init__(self) -> None:
        if self.required_privilege not in PRIVILEGES:
            raise AirootError(
                "INVALID_INPUT",
                f"backend {self.backend_id} declares an unknown required_privilege: {self.required_privilege}",
                evidence=[f"known: {', '.join(PRIVILEGES)}"],
            )
        if self.source_mutation != "none":
            raise AirootError(
                "UNSUPPORTED_BACKEND",
                f"backend {self.backend_id} declares source_mutation={self.source_mutation}",
                evidence=[
                    "a backend may never delete, move or overwrite the source implicitly",
                    "an explicit plan step with its own approval is required (三大核心契约 §4.4:352)",
                ],
            )
        if self.failure_cleanup not in FAILURE_CLEANUPS:
            raise AirootError(
                "INVALID_INPUT",
                f"backend {self.backend_id} declares an unknown failure_cleanup: {self.failure_cleanup}",
                evidence=[f"known: {', '.join(FAILURE_CLEANUPS)}"],
            )

    @property
    def low_risk_eligible(self) -> bool:
        """Whether this backend may ever take the low-risk automatic approval path."""

        return not self.executes_scripts and self.reversible and self.source_mutation == "none"

    def to_document(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "required_privilege": self.required_privilege,
            "network_access": self.network_access,
            "executes_scripts": self.executes_scripts,
            "reversible": self.reversible,
            "reboot_required": self.reboot_required,
            "estimated_size": self.estimated_size,
            "source_mutation": self.source_mutation,
            "supports_resume": self.supports_resume,
            "failure_cleanup": self.failure_cleanup,
            "low_risk_eligible": self.low_risk_eligible,
        }


@dataclass(frozen=True)
class Artifact:
    """A fetched, verified payload. ``path`` is the materialised artifact on disk."""

    path: Path
    digest: str
    size: int
    media_type: str = "application/octet-stream"
    fetched_from: str | None = None


@dataclass
class VerifyResult:
    ok: bool
    digest: str
    size: int
    problems: list[str] = field(default_factory=list)

    def require_ok(self, *, expected: str) -> None:
        if not self.ok:
            raise AirootError(
                "DIGEST_MISMATCH",
                "the fetched artifact does not match the plan digest",
                evidence=[f"expected={expected}", f"actual={self.digest}", f"size={self.size}", *self.problems],
            )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def assert_script_free(path: Path) -> None:
    """Refuse a payload that *is* a script: v1 admits script-free artifacts only.

    An executable is a payload; a ``.bat``/``.ps1`` is a program that runs other programs, and
    the core transaction cannot treat "execute arbitrary third-party script" as an atomically
    reversible step (三大核心契约 决策3:49).
    """

    if path.suffix.lower() in SCRIPT_SUFFIXES:
        raise AirootError(
            "UNSUPPORTED_BACKEND",
            f"{path.name} is a script, not a script-free artifact",
            evidence=[
                f"refused suffixes: {', '.join(SCRIPT_SUFFIXES)}",
                "script-type installers must be modelled as their own backend with declared side effects",
            ],
        )


class InstallBackend(Protocol):
    """The frozen operation set (§4.4). Only fetch/verify/stage/commit move bytes."""

    declaration: BackendDeclaration

    def discover(self) -> list[dict[str, Any]]: ...

    def plan(self, *, capability_id: str, version: str) -> dict[str, Any]: ...

    def fetch(self, *, locator: str, destination: Path) -> Artifact: ...

    def verify(self, artifact: Artifact, *, expected_digest: str) -> VerifyResult: ...

    def stage(self, artifact: Artifact, *, stage_dir: Path) -> Path: ...

    def commit(self, stage_dir: Path, *, store_dir: Path) -> Path: ...

    def expose(self, store_dir: Path) -> list[str]: ...

    def inspect(self, store_dir: Path) -> dict[str, Any]: ...

    def rollback(self, store_dir: Path) -> None: ...


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

BACKEND_IDS = ("fake_fixture", "portable_file", "https_artifact")


def resolve_backend(backend_id: str, *, root: Path | None = None) -> InstallBackend:
    """Instantiate a backend by id. Unknown ids are refused, never guessed."""

    if backend_id == "fake_fixture":
        from ...tx.simulate import SimulationBackend

        return SimulationBackend(Path(root))  # type: ignore[arg-type]
    if backend_id == "portable_file":
        from .portable_file import PortableFileBackend

        return PortableFileBackend()
    if backend_id == "https_artifact":
        from .https_artifact import HttpsArtifactBackend

        return HttpsArtifactBackend()
    raise AirootError(
        "UNSUPPORTED_BACKEND",
        f"unknown install backend: {backend_id}",
        evidence=[f"known backends: {', '.join(BACKEND_IDS)}", "v1 admits script-free artifact backends only"],
    )


def declaration_for(backend_id: str) -> BackendDeclaration:
    """The declaration alone, without constructing the backend (used by `plan`)."""

    if backend_id == "portable_file":
        from .portable_file import DECLARATION

        return DECLARATION
    if backend_id == "https_artifact":
        from .https_artifact import DECLARATION

        return DECLARATION
    if backend_id == "fake_fixture":
        return BackendDeclaration(
            backend_id="fake_fixture",
            executes_scripts=False,
            reversible=True,
            failure_cleanup="stage_only",
        )
    resolve_backend(backend_id)  # raises with the known list
    raise AssertionError("unreachable")


__all__ = [
    "Artifact",
    "BACKEND_IDS",
    "BACKEND_OPERATIONS",
    "BackendDeclaration",
    "DECLARED_FIELDS",
    "FAILURE_CLEANUPS",
    "InstallBackend",
    "PRIVILEGES",
    "SCRIPT_SUFFIXES",
    "VerifyResult",
    "assert_script_free",
    "declaration_for",
    "resolve_backend",
    "sha256_file",
]
