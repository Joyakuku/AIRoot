"""``path verify``: check the frozen PATH-exposure invariant (规划 §8.1 rule 8).

The rule is old and absolute:

* the machine PATH may contain **exactly one** AIROOT entry, ``AIROOT\\cli\\exposure\\bin``;
* a version directory never goes on PATH directly;
* Zone W (user-writable) never enters the machine PATH.

Nothing checked it until this module existed, and an invariant that only exists in prose is not an
invariant. It reads the machine and user PATH and reports deviations. It **never writes PATH** — that
belongs to the protected broker (P2), so this verb is purely a diagnosis.

**ADR-0050 changed what ``launcher_present`` means.** It used to be "the sanctioned *directory*
exists", which is true on a freshly created root and says nothing about whether anything is callable.
It now means "**at least one stable entry exists**", which is what the minimum version's judgement 10
asks about, and the same step added the two ways that can drift: an active binding with no entry, and
an entry that is not the bytes this build would write (a hand edit, a moved interpreter, a moved
checkout). Both are ``PATH_EXPOSURE_VIOLATION`` — the exposure surface disagreeing with the bindings
is exactly the drift this verb is for, and the frozen code table is not widened for a second name.

One reason code is enough (``PATH_EXPOSURE_VIOLATION``, exit 2): this is *drift from the contract*,
not AIROOT being broken, and the per-finding ``severity`` already carries the gradation. Encoding
severity again as more reason codes would only blur the mapping table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import is_within
from .launcher import discover_launchers, launcher_directory

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
    launchers: list[dict[str, Any]] = field(default_factory=list)
    expected_launchers: list[str] = field(default_factory=list)

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
            "launchers": self.launchers,
            "expected_launchers": self.expected_launchers,
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
    expected_launchers: list[str] | tuple[str, ...] = (),
    bindings_note: str | None = None,
) -> PathVerification:
    """Report every AIROOT-owned PATH entry, every stable entry, and every deviation from the rule.

    ``expected_launchers`` is the caller's answer to "which capabilities have an active machine-level
    binding" (ADR-0050). It is passed in rather than read here so this module stays a pure function of
    its inputs: the PATH lists are inputs, and so is the expectation. ``bindings_note`` carries the
    case where the caller could not ask.
    """

    root = Path(root)
    expected = sanctioned_entry(root)
    verification = PathVerification(root=str(root), sanctioned=str(expected))

    found = discover_launchers(root)
    verification.launchers = [item.to_document() for item in found]
    verification.launcher_present = bool(found)
    verification.expected_launchers = sorted(set(str(item) for item in expected_launchers))

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
        # Still information rather than a defect: a root with no managed install legitimately has no
        # stable entry. What is *not* information is an expectation this directory fails to satisfy --
        # that is reported below, per capability.
        verification.findings.append(
            PathFinding(
                "info",
                "EXPOSURE_NOT_IMPLEMENTED",
                f"no stable entry exists in {launcher_directory(root)}; a managed install writes one "
                "when it exposes a binding (ADR-0050)",
            )
        )

    present = {str(item["capability_id"]) for item in verification.launchers}
    for capability_id in verification.expected_launchers:
        if capability_id not in present:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"{capability_id} has an active machine-level binding but no stable entry in "
                    f"{launcher_directory(root)}; nothing exposes it by a version-independent name",
                )
            )
    for capability_id in sorted(present - set(verification.expected_launchers)):
        verification.findings.append(
            PathFinding(
                "warning",
                "PATH_EXPOSURE_VIOLATION",
                f"a stable entry exists for {capability_id} but no active binding exposes it; the "
                "entry resolves nothing (retire clears the binding, not the file)",
            )
        )
    for item in verification.launchers:
        if not item["matches_current"]:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"the stable entry for {item['capability_id']} is not what this build writes "
                    f"({item['path']}): edited by hand, or the interpreter/checkout moved",
                )
            )

    if bindings_note is not None:
        verification.findings.append(PathFinding("info", "EXPOSURE_NOT_IMPLEMENTED", bindings_note))

    return verification


__all__ = [
    "PathFinding",
    "PathVerification",
    "SANCTIONED_RELATIVE",
    "sanctioned_entry",
    "verify_path_exposure",
]
