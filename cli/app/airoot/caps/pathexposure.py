"""``path verify``: check the frozen PATH-exposure invariant (规划 §8.1 rule 8).

The rule is old and absolute:

* the machine PATH may contain **exactly one** AIROOT entry, ``AIROOT\\cli\\exposure\\bin``;
* a version directory never goes on PATH directly;
* Zone W (user-writable) never enters the machine PATH.

Nothing checked it until now, and an invariant that only exists in prose is not an invariant.
This module reads the machine and user PATH and reports deviations. It **never writes PATH** —
that belongs to the protected broker (P2), so this verb is purely a diagnosis.

One reason code is enough (``PATH_EXPOSURE_VIOLATION``, exit 2): this is *drift from the
contract*, not AIROOT being broken, and the per-finding ``severity`` already carries the
gradation. Encoding severity again as more reason codes would only blur the mapping table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import is_within

SANCTIONED_RELATIVE = ("cli", "exposure", "bin")


def sanctioned_entry(root: Path) -> Path:
    """The one directory allowed on the machine PATH (规划 §8.1 rule 8)."""

    return Path(root).joinpath(*SANCTIONED_RELATIVE)


def _normalize(entry: str) -> str:
    return os.path.normcase(os.path.normpath(entry.strip().strip('"')))


@dataclass
class PathFinding:
    severity: str
    code: str
    detail: str

    def to_document(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "detail": self.detail}


@dataclass
class PathVerification:
    root: str
    sanctioned: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    findings: list[PathFinding] = field(default_factory=list)
    launcher_present: bool = False

    @property
    def violations(self) -> int:
        return sum(1 for finding in self.findings if finding.severity in {"warning", "error"})

    def reason_code(self) -> str:
        return "PATH_EXPOSURE_VIOLATION" if self.violations else "SUCCESS"

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "root": self.root,
            "sanctioned_entry": self.sanctioned,
            "launcher_present": self.launcher_present,
            "entries": self.entries,
            "findings": [finding.to_document() for finding in self.findings],
            "violations": self.violations,
            "path_written": False,
            "reason_code": self.reason_code(),
        }


def verify_path_exposure(
    root: Path,
    *,
    machine_entries: list[str],
    user_entries: list[str],
) -> PathVerification:
    """Report every AIROOT-owned PATH entry and every deviation from the rule."""

    root = Path(root)
    expected = sanctioned_entry(root)
    verification = PathVerification(root=str(root), sanctioned=str(expected))
    verification.launcher_present = expected.is_dir()

    seen: dict[str, list[str]] = {}
    for scope, entries in (("machine", machine_entries), ("user", user_entries)):
        for raw in entries:
            if not raw.strip():
                continue
            candidate = Path(raw.strip().strip('"'))
            try:
                inside = is_within(candidate, root)
            except Exception:  # pragma: no cover - a malformed entry is still worth reporting
                inside = False
            if not inside:
                continue
            is_sanctioned = _normalize(str(candidate)) == _normalize(str(expected))
            verification.entries.append(
                {
                    "scope": scope,
                    "path": str(candidate),
                    "sanctioned": is_sanctioned,
                }
            )
            seen.setdefault(_normalize(str(candidate)), []).append(scope)
            store_dir = root / "store"
            if is_within(candidate, store_dir):
                # A store/version directory on PATH is the exact thing the rule forbids: it pins a
                # single version and bypasses the launcher indirection.
                verification.findings.append(
                    PathFinding(
                        "error",
                        "PATH_EXPOSURE_VIOLATION",
                        f"{scope} PATH contains a store payload directory: {candidate}",
                    )
                )
            elif not is_sanctioned:
                verification.findings.append(
                    PathFinding(
                        "warning",
                        "PATH_EXPOSURE_VIOLATION",
                        f"{scope} PATH contains an AIROOT directory that is not the sanctioned entry: {candidate}",
                    )
                )

    for normalized, scopes in seen.items():
        if len(scopes) > 1:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"{normalized} appears {len(scopes)} times ({', '.join(scopes)}); the rule allows exactly one",
                )
            )

    if not verification.launcher_present:
        # P1 does not write launchers (`exposure\bin` is P2). Report the absence as information
        # rather than as a defect, so the number is honest about what exists.
        verification.findings.append(
            PathFinding(
                "info",
                "EXPOSURE_NOT_IMPLEMENTED",
                f"the launcher directory {expected} does not exist; P1 writes no launchers",
            )
        )
    return verification


__all__ = [
    "PathFinding",
    "PathVerification",
    "SANCTIONED_RELATIVE",
    "sanctioned_entry",
    "verify_path_exposure",
]
