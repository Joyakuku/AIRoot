"""``rebuild``: regenerate derived state from authoritative rows (规划 §9.11, §3.6).

What this verb may touch is exactly two files:

* ``state/registry.json`` — the declared-state projection (D7);
* ``logs/audit/events.json`` — the audit projection (D10).

What it may **not** touch is the point of the whole design:

* ``state/registry.db`` is the authority. Rebuilding it from its own projection would launder a
  corruption instead of reporting it, so a broken database is refused with
  ``REGISTRY_INTEGRITY_FAILED`` and a remediation pointing at a human/backup decision;
* unfinished transactions belong to ``repair``. ``rebuild`` reports them and changes nothing, or
  the two verbs blur into one and nobody knows which to run;
* unfamiliar objects are reported as ``orphan``/``unmanaged`` and are **never** adopted and never
  deleted (规划 §3.6:217, §9.6:716, §9.7:839).

The previous projections are archived under ``state/rebuild/<seq>/`` rather than discarded: the
§9.11 rule "keep the old registry as read-only evidence" has a projection-level equivalent, and
deleting the snapshot would erase "what it looked like a moment ago".
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..registry.projection import (
    audit_projection,
    audit_projection_path,
    projection_path,
    read_projection,
)

REBUILD_DIR = "state/rebuild"
REGISTRY_BACKUP = "registry.json"
AUDIT_BACKUP = "audit-events.json"


@dataclass
class RebuildFindings:
    """What a rebuild would do, and what it noticed but will not touch."""

    stale_projection: bool = False
    stale_audit: bool = False
    orphans: list[str] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)
    pending_transactions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def derived_stale(self) -> bool:
        return self.stale_projection or self.stale_audit

    def to_document(self) -> dict[str, Any]:
        return {
            "stale_projection": self.stale_projection,
            "stale_audit": self.stale_audit,
            "orphans": sorted(self.orphans),
            "unmanaged": sorted(self.unmanaged),
            "pending_transactions": sorted(self.pending_transactions),
            "problems": list(self.problems),
            "derived_state_stale": self.derived_stale,
        }


def _store_orphans(registry: Any, root: Path) -> list[str]:
    """Store payloads with no registry row. Reported, never adopted (D4)."""

    store = Path(root) / "store"
    if not store.is_dir():
        return []
    declared = {str(row["store_path"]).replace("\\", "/") for row in registry.instances()}
    found: list[str] = []
    for entry in sorted(path for path in store.iterdir() if path.is_dir()):
        for leaf in sorted(path for path in entry.rglob("*") if path.is_dir() and (path / "artifact.json").is_file()):
            relative = leaf.relative_to(root).as_posix()
            if relative not in declared:
                found.append(relative)
    return found


def _unmanaged_references(registry: Any) -> list[str]:
    return [
        str(row["external_id"])
        for row in registry.external_references()
        if str(row["management"]) in {"unmanaged", "quarantined"}
    ]


def rebuild_plan(registry: Any, root: Path) -> RebuildFindings:
    """Inspect (read-only) before rebuilding anything."""

    root = Path(root)
    findings = RebuildFindings()
    findings.orphans = _store_orphans(registry, root)
    findings.unmanaged = _unmanaged_references(registry)
    findings.pending_transactions = [
        str(row["transaction_id"]) for row in registry.transactions(unfinished_only=True)
    ]

    projection = read_projection(root)
    if projection is None or int(projection.get("generation", -1)) != registry.generation:
        findings.stale_projection = True

    expected_audit = audit_projection(registry)["digest"]
    path = audit_projection_path(root)
    if not path.is_file():
        findings.stale_audit = bool(expected_audit)
    else:
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
            findings.stale_audit = actual.get("digest") != expected_audit
        except (OSError, ValueError):
            findings.stale_audit = True
    return findings


def _archive(root: Path, sequence: int) -> Path:
    """Move the current derived files aside as read-only evidence."""

    directory = Path(root) / REBUILD_DIR / f"{sequence:04d}"
    directory.mkdir(parents=True, exist_ok=True)
    projection = projection_path(root)
    if projection.is_file():
        shutil.copy2(projection, directory / REGISTRY_BACKUP)
    audit = audit_projection_path(root)
    if audit.is_file():
        shutil.copy2(audit, directory / AUDIT_BACKUP)
    return directory


def _next_sequence(root: Path) -> int:
    directory = Path(root) / REBUILD_DIR
    if not directory.is_dir():
        return 1
    existing = [int(item.name) for item in directory.iterdir() if item.is_dir() and item.name.isdigit()]
    return max(existing, default=0) + 1


def apply_rebuild(
    registry: Any,
    root: Path,
    *,
    clock: Clock = SYSTEM_CLOCK,
    archive: bool = True,
) -> dict[str, Any]:
    """Rewrite the derived projections from authoritative rows. Never touches the authority."""

    root = Path(root)
    if registry.integrity_problems():
        # The authority itself is broken; rebuilding derived state from it would produce a
        # confident-looking projection of corrupt data. Refuse and say why (draft §24.3-1).
        raise AirootError(
            "REGISTRY_INTEGRITY_FAILED",
            "the registry database is not self-consistent; rebuild cannot repair authority",
            evidence=[
                *registry.integrity_problems()[:4],
                "state/registry.db is the authority; it is never rebuilt from its own projection",
                "restore a backup, or run 'doctor' and decide explicitly",
            ],
        )

    findings = rebuild_plan(registry, root)
    sequence = _next_sequence(root)
    archived = _archive(root, sequence) if archive else None
    registry.update_projection()
    return {
        "schema_version": 1,
        "operation": "rebuild",
        "generation": registry.generation,
        "rebuilt": ["state/registry.json", "logs/audit/events.json"],
        "archived_to": str(archived) if archived else None,
        "findings": findings.to_document(),
        "database_touched": False,
        "files_deleted": 0,
        "adopted": 0,
        "repaired_transactions": 0,
        "rebuilt_at": clock.timestamp(),
        "note": (
            "derived state only: the registry database, transaction journals and unfamiliar "
            "objects are reported, never rewritten, adopted or deleted"
        ),
        "reason_code": "SUCCESS",
    }


__all__ = ["REBUILD_DIR", "RebuildFindings", "apply_rebuild", "rebuild_plan"]
