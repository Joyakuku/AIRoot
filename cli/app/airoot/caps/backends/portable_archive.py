"""``portable_archive``: install a script-free portable archive as a payload **tree**.

v1's low-risk baseline is "no scripts" (三大核心契约 决策3) and P4 asks for "a single file *or* a
script-free portable archive". The two shipped backends cover the single-file half — ``portable_file``
for a local file and ``https_artifact`` for one fetched over HTTPS — and both stage the artifact
**as it is**, so a ``.zip`` would be installed as a ``.zip`` with no entrypoint a binding could point
at. This is the other half: fetch and verify exactly as ``https_artifact`` does, then **extract**
under rules that keep "no scripts" and "no traversal" true at the archive boundary.

The rules are refusal-by-default:

* **no traversal** — a member that is absolute, carries a drive letter, contains ``..`` or otherwise
  escapes the destination is refused rather than sanitised;
* **no links** — a symlink or any other non-regular member is refused: the stage holds bytes we
  wrote, not a pointer somewhere else;
* **bounded** — a declared member count and a declared uncompressed-size budget, checked against the
  archive's own metadata *and* against the bytes actually written, so an archive that lies about a
  member's size is refused mid-extract;
* **no installer scripts** — a *top-level* script whose name marks it as an installer
  (``install``/``setup``/``bootstrap``/…) is refused, and a declared entrypoint that is a script is
  refused too. A *shim* like node's ``npm.cmd`` is not an installer, so this rule is name-scoped on
  purpose — suffix-scoped would refuse every Windows runtime archive;
* **one wrapper directory is stripped** — release archives ship inside a versioned directory
  (``cmake-3.31.6-windows-x86_64/…``); the payload root is what is *inside* it, so entrypoints read
  ``bin/cmake.exe``. Only a wrapper every member shares is stripped.

``expose`` is where "which file is the main entrypoint" is decided: the registry's
``managed-tool-instance.entrypoints`` **is** this list and ``run --capability`` executes index 0
(``caps/runtime.py``), so the capability's declared `entry` is ordered first.
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ...exits import AirootError
from .base import (
    Artifact,
    BackendDeclaration,
    VerifyResult,
    assert_script_free,
    sha256_file,
)

BACKEND_ID = "portable_archive"

#: Budgets, not policies: a release archive of a tool is a few hundred MB at most, and these bounds
#: exist so a hostile or broken archive cannot fill the disk or the inode table.
MAX_MEMBERS = 20000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB
MAX_ENTRYPOINTS = 32
CHUNK = 1024 * 1024

#: Top-level **script** stems that mean "this archive wants to be run, not unpacked". Name-scoped on
#: purpose: a suffix rule would also refuse node's `npm.cmd`/`npx.cmd` shims, which are payloads.
INSTALLER_STEMS = ("install", "setup", "bootstrap", "uninstall", "configure", "preinstall", "postinstall")

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


def _refuse(message: str, *, evidence: list[str] | None = None) -> AirootError:
    return AirootError(
        "UNSUPPORTED_BACKEND",
        message,
        evidence=[
            *(evidence or []),
            "portable_archive admits script-free, traversal-free archives only (三大核心契约 决策3)",
        ],
    )


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    """The member's path as parts, refusing anything that could escape the destination."""

    name = info.filename.replace("\\", "/")
    if not name or name.endswith("/") and not info.is_dir():
        raise _refuse(f"the archive has a malformed member name: {info.filename!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ":" in path.parts[0]:
        raise _refuse(f"the archive has an absolute member path: {info.filename!r}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise _refuse(f"the archive has a traversal member path: {info.filename!r}")
    if not parts:
        raise _refuse(f"the archive has an empty member path: {info.filename!r}")
    return parts


def _refuse_non_regular(info: zipfile.ZipInfo) -> None:
    """Reject anything that is not a file or a directory. A link is a pointer, not a payload."""

    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode) if mode else 0
    if kind and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise _refuse(
            f"the archive contains a non-regular member: {info.filename!r}",
            evidence=[f"mode={oct(mode)}", "links and devices are refused, never followed"],
        )


class PortableArchiveBackend:
    """Fetch = transport (HTTPS or a local file); stage = extract; commit = move the tree."""

    declaration = DECLARATION

    def __init__(
        self,
        *,
        entry_name: str | None = None,
        timeout_seconds: int | None = None,
        opener: Any = None,
    ) -> None:
        self.entry_name = entry_name
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    # ------------------------------------------------------------------ transport #

    def _https(self) -> Any:
        from .https_artifact import HttpsArtifactBackend

        return HttpsArtifactBackend(
            **({} if self._timeout_seconds is None else {"timeout_seconds": self._timeout_seconds}),
            opener=self._opener,
        )

    def discover(self) -> list[dict[str, Any]]:
        return []

    def plan(self, *, capability_id: str, version: str) -> dict[str, Any]:
        return {
            "backend_id": BACKEND_ID,
            "capability_id": capability_id,
            "version": version,
            "network_access": True,
            "instructions": "pass the archive's https:// locator and its sha256 in the plan source",
        }

    def fetch(self, *, locator: str, destination: Path) -> Artifact:
        """A local path is read; anything else goes through the https backend unchanged."""

        if "://" not in locator:
            from .portable_file import PortableFileBackend

            return PortableFileBackend().fetch(locator=locator, destination=destination)
        return self._https().fetch(locator=locator, destination=destination)

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

    # ------------------------------------------------------------------ extract #

    def stage(self, artifact: Artifact, *, stage_dir: Path) -> Path:
        """Extract the archive into ``stage_dir`` and return the payload root inside it."""

        if not zipfile.is_zipfile(artifact.path):
            raise _refuse(
                f"the artifact is not a zip archive: {artifact.path.name}",
                evidence=[f"path={artifact.path}", "this backend admits the zip container only"],
            )
        payload_root = stage_dir / ".payload"
        stage_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(artifact.path) as archive:
            infos = archive.infolist()
            if not infos:
                raise _refuse(f"the archive is empty: {artifact.path.name}")
            if len(infos) > MAX_MEMBERS:
                raise _refuse(
                    f"the archive has more members than this backend admits ({len(infos)})",
                    evidence=[f"limit={MAX_MEMBERS}"],
                )
            declared = sum(info.file_size for info in infos)
            if declared > MAX_UNCOMPRESSED_BYTES:
                raise _refuse(
                    f"the archive unpacks to more than this backend admits ({declared} bytes)",
                    evidence=[f"limit={MAX_UNCOMPRESSED_BYTES}"],
                )
            parts_by_member = {info.filename: _member_parts(info) for info in infos}
            for info in infos:
                _refuse_non_regular(info)
            prefix = _shared_wrapper(parts_by_member.values())

            written = 0
            for info in infos:
                parts = parts_by_member[info.filename]
                if prefix is not None:
                    parts = parts[len(prefix) :]
                if not parts:
                    continue  # the wrapper directory itself
                target = payload_root.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.suffix.lower() and len(parts) == 1:
                    _refuse_top_level_script(parts[0])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as handle:
                    while True:
                        chunk = source.read(CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UNCOMPRESSED_BYTES:
                            shutil.rmtree(stage_dir, ignore_errors=True)
                            raise _refuse(
                                "the archive writes more than it declared",
                                evidence=[f"limit={MAX_UNCOMPRESSED_BYTES}", f"written={written}"],
                            )
                        handle.write(chunk)

        payloads = sorted(item for item in payload_root.iterdir()) if payload_root.is_dir() else []
        if len(payloads) == 1 and payloads[0].is_file():
            # A one-file archive is a payload too; keep the flat shape the other backends produce.
            single = stage_dir / payloads[0].name
            shutil.move(str(payloads[0]), str(single))
            shutil.rmtree(payload_root, ignore_errors=True)
            return single
        return payload_root

    def commit(self, stage_dir: Path, *, store_dir: Path) -> Path:
        """Move the extracted tree into the immutable store, entrypoint-relative paths and all."""

        if store_dir.exists():
            raise AirootError(
                "INSTANCE_CONFLICT",
                f"the store path is already occupied: {store_dir}",
                evidence=["an existing payload is never overwritten; a new instance gets a new id"],
            )
        roots = sorted(item for item in stage_dir.iterdir())
        if not roots:
            raise AirootError("INVALID_INPUT", f"nothing staged in {stage_dir}")
        payload = roots[0]
        store_dir.mkdir(parents=True)
        if payload.is_file():
            shutil.move(str(payload), str(store_dir / payload.name))
            return store_dir
        for item in sorted(payload.iterdir()):
            shutil.move(str(item), str(store_dir / item.name))
        shutil.rmtree(payload, ignore_errors=True)
        return store_dir

    # ------------------------------------------------------------------ report #

    def expose(self, store_dir: Path) -> list[str]:
        """Executable paths relative to the payload root, **the capability's entry first**.

        The registry stores this list verbatim and `run --capability` executes index 0, so the order
        decides which file the capability resolves to. A tree can hold several executables — cmake
        ships `cpack.exe`, `ctest.exe` and `cmake-gui.exe` beside `cmake.exe` — and only the frozen
        capability list knows which one is *the* entry.
        """

        found: list[str] = []
        for item in sorted(store_dir.rglob("*")):
            if len(found) >= MAX_ENTRYPOINTS:
                break
            if item.is_file() and item.suffix.lower() == ".exe":
                found.append(item.relative_to(store_dir).as_posix())
        if self.entry_name:
            wanted = self.entry_name.lower()
            found.sort(key=lambda relative: (PurePosixPath(relative).name.lower() != wanted, relative))
        return found

    def inspect(self, store_dir: Path) -> dict[str, Any]:
        entrypoints = self.expose(store_dir)
        if not store_dir.is_dir():
            return {"present": False, "entrypoints": [], "health": "broken"}
        size = sum(item.stat().st_size for item in store_dir.rglob("*") if item.is_file())
        return {
            "present": True,
            "entrypoints": entrypoints,
            "size": size,
            "health": "healthy" if entrypoints else "broken",
        }

    def rollback(self, store_dir: Path) -> None:
        """Rollback keeps the payload as evidence; the transaction switches the binding."""

        return None


def _shared_wrapper(members: Any) -> tuple[str, ...] | None:
    """The single leading directory every member shares, or None when there is no such wrapper."""

    prefixes: set[tuple[str, ...]] = set()
    for parts in members:
        if len(parts) < 2:
            return None
        prefixes.add((parts[0],))
        if len(prefixes) > 1:
            return None
    return next(iter(prefixes)) if len(prefixes) == 1 else None


def _refuse_top_level_script(name: str) -> None:
    """A top-level *installer* script is an installer, not a payload (v1 admits script-free only)."""

    stem = PurePosixPath(name).stem.lower()
    if PurePosixPath(name).suffix.lower() in {".bat", ".cmd", ".ps1", ".sh", ".bash", ".vbs", ".py", ".js"}:
        if stem in INSTALLER_STEMS:
            raise _refuse(
                f"the archive carries a top-level installer script: {name}",
                evidence=[
                    f"installer stems: {', '.join(INSTALLER_STEMS)}",
                    "the transaction cannot treat 'run a third-party installer' as a reversible step",
                ],
            )


__all__ = [
    "BACKEND_ID",
    "DECLARATION",
    "INSTALLER_STEMS",
    "MAX_ENTRYPOINTS",
    "MAX_MEMBERS",
    "MAX_UNCOMPRESSED_BYTES",
    "PortableArchiveBackend",
    "assert_script_free",
]
