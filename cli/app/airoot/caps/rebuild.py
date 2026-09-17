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
    projection_is_current,
    projection_path,
)
from .layout import misdeclared_payloads, mislocated_payloads, store_orphans

REBUILD_DIR = "state/rebuild"
REGISTRY_BACKUP = "registry.json"
AUDIT_BACKUP = "audit-events.json"

#: The derived file `rebuild` does **not** own. `doctor`'s D7 gives the search index the same
#: `remediation: rebuild` as the projections, so an operator who follows that word literally runs
#: this verb — which rewrites two other files and leaves `cache/search/index.db` exactly as broken
#: as it found it (measured §168). The pointer belongs in this output, or the one word the diagnosis
#: offers sends its reader to a command that cannot help.
SEARCH_INDEX_PATH = "cache/search/index.db"
SEARCH_INDEX_REFRESH_COMMAND = "airoot search refresh"
NOT_REBUILT = (
    f"{SEARCH_INDEX_PATH} is not derived state this verb owns; "
    f"rebuild it with: {SEARCH_INDEX_REFRESH_COMMAND}",
)


@dataclass
class RebuildFindings:
    """What a rebuild would do, and what it noticed but will not touch."""

    stale_projection: bool = False
    stale_audit: bool = False
    orphans: list[str] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)
    pending_transactions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # §71: the other two shapes of the same D4 fact. `orphans` covers payload markers under `store/`
    # that nobody declares; these cover a *declaration* pointing outside the store and a payload
    # marker sitting in a view directory. `doctor` reported all three and this report none of the
    # last two, so an operator could read "no orphans, no problems" after a recovery while a frozen
    # contract was violated (measured in §71.2).
    misdeclared_payloads: list[str] = field(default_factory=list)
    mislocated_payloads: list[str] = field(default_factory=list)

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
            "misdeclared_payloads": sorted(self.misdeclared_payloads),
            "mislocated_payloads": sorted(self.mislocated_payloads),
            "derived_state_stale": self.derived_stale,
        }


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
    findings.orphans = store_orphans(registry, root)
    findings.mislocated_payloads = mislocated_payloads(registry, root)
    # `<instance_id> -> <store_path>`, because `rebuild`'s document has no published schema and a
    # report an operator reads should not require a second command to name the path. `doctor` keeps
    # the two apart, since its diagnostics are fielded.
    findings.misdeclared_payloads = [
        f"{instance_id} -> {store_path}" for instance_id, store_path in misdeclared_payloads(registry)
    ]
    findings.unmanaged = _unmanaged_references(registry)
    findings.pending_transactions = [
        str(row["transaction_id"]) for row in registry.transactions(unfinished_only=True)
    ]

    # Content, not just the generation stamp (draft §168, defect 6): the stamp says the registry did
    # not move, not that the file is what this build would write. Both readers of this verdict use
    # `projection_is_current` so `doctor`'s D7 and `rebuild --plan` can never disagree about it.
    if not projection_is_current(registry, root):
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
        "not_rebuilt": list(NOT_REBUILT),
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
