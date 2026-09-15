"""Root resolution, layout creation and the root identity guard.

P1 resolves a root that **already exists** and fails closed otherwise
(``--root`` then ``AIROOT_HOME``; no volume-label scanning, that belongs to the
P2 bootstrap). The root is confirmed by ``state/root.json`` plus the real volume
serial, never by the current directory — and P1 never creates a root at a system
location: :func:`init_root` is a test/bootstrap helper only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import (
    CACHE_DIR,
    ENV_DIR,
    EXPOSURE_DIR,
    LOGS_DIR,
    PROTOCOL_VERSION,
    QUARANTINE_DIR,
    ROOT_MARKER,
    STATE_DIR,
    STORE_DIR,
    TOOLS_DIR,
    TX_DIR,
)
from .canon import digest_text
from .clock import Clock, SYSTEM_CLOCK
from .exits import AirootError
from .paths import canonicalize, volume_serial
from .schema_io import validate_document

LAYOUT_DIRS = (
    STORE_DIR,
    TOOLS_DIR,
    ENV_DIR,
    EXPOSURE_DIR,
    f"{EXPOSURE_DIR}/bin",
    STATE_DIR,
    f"{STATE_DIR}/plans",
    f"{STATE_DIR}/approvals",
    TX_DIR,
    CACHE_DIR,
    f"{CACHE_DIR}/fixtures",
    LOGS_DIR,
    QUARANTINE_DIR,
)


@dataclass(frozen=True)
class RootInfo:
    """A resolved, identity-checked AIROOT root."""

    path: Path
    marker: dict[str, Any]

    @property
    def root_instance_id(self) -> str:
        return str(self.marker["root_instance_id"])

    @property
    def volume_serial(self) -> str:
        return str(self.marker["volume_serial"])

    def resolve(self, *parts: str) -> Path:
        return self.path.joinpath(*parts)


def marker_path(root: Path) -> Path:
    return Path(root) / ROOT_MARKER


def resolve_root(explicit: str | os.PathLike[str] | None = None, *, env: Mapping[str, str] | None = None) -> Path:
    """``--root`` → ``AIROOT_HOME`` → fail closed. Never falls back to the CWD."""

    source = "explicit" if explicit else "AIROOT_HOME"
    candidate = explicit if explicit else (env or os.environ).get("AIROOT_HOME")
    if not candidate:
        raise AirootError(
            "ROOT_NOT_RESOLVED",
            "no AIROOT root given: pass --root or set AIROOT_HOME",
            evidence=["AIROOT root resolution never falls back to the current directory"],
        )
    root = Path(candidate)
    if not root.is_dir():
        raise AirootError(
            "ROOT_NOT_RESOLVED",
            f"AIROOT root is not a directory: {root}",
            evidence=[f"source={source}"],
        )
    return canonicalize(root, must_exist=True)


def read_marker(root: Path) -> dict[str, Any]:
    path = marker_path(root)
    if not path.is_file():
        raise AirootError(
            "ROOT_MARKER_MISSING",
            f"root marker missing: {path}",
            evidence=["the root must be created by bootstrap (P2) or by a test fixture"],
        )
    import json

    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError("ROOT_MARKER_INVALID", f"root marker is unreadable: {path}", evidence=[str(exc)]) from exc
    validate_document("root-marker", marker, reason_code="ROOT_MARKER_INVALID")
    return marker


def open_root(
    explicit: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    verify_volume: bool = True,
) -> RootInfo:
    """Resolve a root, confirm its identity, and refuse to continue on drift."""

    path = resolve_root(explicit, env=env)
    marker = read_marker(path)
    if int(marker.get("protocol_version", 0)) != PROTOCOL_VERSION:
        raise AirootError(
            "SCHEMA_UNSUPPORTED",
            f"protocol_version {marker.get('protocol_version')} is not supported",
            evidence=[f"supported={PROTOCOL_VERSION}"],
        )
    if verify_volume:
        actual = volume_serial(path)
        if actual != marker["volume_serial"]:
            raise AirootError(
                "VOLUME_IDENTITY_MISMATCH",
                "the root volume identity changed; refusing to adopt this directory",
                evidence=[f"marker={marker['volume_serial']}", f"actual={actual}"],
            )
    return RootInfo(path=path, marker=marker)


def ensure_layout(root: Path) -> None:
    for relative in LAYOUT_DIRS:
        Path(root).joinpath(relative).mkdir(parents=True, exist_ok=True)


def init_root(
    root: Path,
    *,
    root_instance_id: str,
    machine_id: str,
    clock: Clock = SYSTEM_CLOCK,
    application_generation: int = 0,
) -> RootInfo:
    """Create the layout and the root marker.

    Test/bootstrap helper only: tests pass a ``MachineFixture`` identity, and P2
    bootstrap is the first caller allowed to do this for real. Machine identity is
    never auto-derived here (the generation algorithm is still an open item).
    """

    root = Path(root)
    if not root_instance_id or not machine_id:
        raise AirootError("INVALID_INPUT", "root_instance_id and machine_id are required")
    if marker_path(root).exists():
        raise AirootError("INVALID_INPUT", f"root already initialised: {root}")
    root.mkdir(parents=True, exist_ok=True)
    ensure_layout(root)
    marker = {
        "schema_version": 1,
        "root_instance_id": root_instance_id,
        "protocol_version": PROTOCOL_VERSION,
        "volume_serial": volume_serial(root),
        "canonical_path": str(canonicalize(root, must_exist=True)),
        "created_at": clock.timestamp(),
        "application_generation": application_generation,
    }
    validate_document("root-marker", marker, reason_code="ROOT_MARKER_INVALID")
    marker_path(root).write_text(_pretty(marker), encoding="utf-8")
    return RootInfo(path=canonicalize(root, must_exist=True), marker=marker)


def identity_fingerprint(machine_id: str, root_instance_id: str) -> str:
    """Stable digest used only for evidence, not for identity generation."""

    return digest_text(f"{machine_id}\n{root_instance_id}")


def _pretty(document: Any) -> str:
    import json

    return json.dumps(document, indent=2, sort_keys=True) + "\n"
