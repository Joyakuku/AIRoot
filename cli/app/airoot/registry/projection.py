"""The read-only JSON projection of declared state.

``state/registry.json`` is a projection: it is produced by the core and never
accepted as an edit (v0.3 §9.11). Its shape is fixed by
``registry-projection.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import REGISTRY_JSON
from ..exits import AirootError
from .entities import Binding, binding_from_row, data_root_from_row, external_reference_from_row

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .db import Registry


def binding_to_schema(row: Any) -> dict[str, Any]:
    return Binding(
        binding_key=row["binding_key"],
        instance_id=row["instance_id"],
        scope=row["scope"],
        zone=row["zone"],
        exposure=row["exposure"],
        generation=int(row["generation"]),
        active=bool(row["active"]),
        project_id=row["project_id"],
        updated_at=row["updated_at"],
    ).to_schema()


def build_projection(registry: "Registry") -> dict[str, Any]:
    """Declared state as the published projection document."""

    instances = [
        {
            "instance_id": row["instance_id"],
            "kind": row["kind"],
            "capability_id": row["capability_id"],
            "version": row["version"],
            "lifecycle_status": row["lifecycle_status"],
            "health": row["health"],
            "artifact_digest": row["artifact_digest"],
            "store_path": row["store_path"],
            # Optional: present only once `gc --apply` removed the payload. The row itself is
            # kept for binding history, so this field is how a consumer tells "deliberately
            # collected" from "still installed" (draft §19.3-2).
            "collected_at": row["collected_at"],
        }
        for row in registry.instances()
    ]
    external_references = [
        external_reference_from_row(row).to_schema() for row in registry.external_references()
    ]
    data_roots = [data_root_from_row(row).to_schema() for row in registry.data_roots()]
    return {
        "schema_version": 1,
        "root_instance_id": registry.root_instance_id,
        "machine_id": registry.machine_id,
        "generation": registry.generation,
        "policy_revision": registry.policy_revision,
        "instances": instances,
        "bindings": [binding_to_schema(row) for row in registry.bindings()],
        "external_references": external_references,
        "generated_at": registry.clock.timestamp(),
        "data_roots": data_roots,
    }


def projection_path(root: Path) -> Path:
    return Path(root) / REGISTRY_JSON


def audit_projection(registry: "Registry") -> dict[str, Any]:
    """Derived view of ``state/events``.

    ``state/events`` is the authoritative historical record; ``logs/audit`` may be
    lost and rebuilt from it, never the other way round (v0.3 §13.1).
    """

    from ..canon import canonical_bytes, digest_bytes

    records = [
        {
            "seq": int(row["seq"]),
            "transaction_id": row["transaction_id"],
            "state": row["state"],
            "detail": row["detail"],
            "generation": row["generation"],
            "outcome": row["outcome"],
            "occurred_at": row["occurred_at"],
        }
        for row in registry.events()
    ]
    return {
        "schema_version": 1,
        "generated_at": registry.clock.timestamp(),
        "event_count": len(records),
        "last_seq": records[-1]["seq"] if records else 0,
        "digest": digest_bytes(canonical_bytes(records)),
    }


def audit_projection_path(root: Path) -> Path:
    from .. import LOGS_DIR

    return Path(root) / LOGS_DIR / "audit" / "events.json"


def write_audit_projection(registry: "Registry") -> dict[str, Any]:
    document = audit_projection(registry)
    path = audit_projection_path(Path(registry.path).parent.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def read_projection(root: Path) -> dict[str, Any] | None:
    path = projection_path(root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def projection_generation(root: Path) -> int | None:
    document = read_projection(root)
    if document is None:
        return None
    generation = document.get("generation")
    return int(generation) if isinstance(generation, int) else None


def require_projection(root: Path) -> dict[str, Any]:
    document = read_projection(root)
    if document is None:
        raise AirootError(
            "REGISTRY_PROJECTION_STALE",
            "the JSON projection is missing or unreadable",
            evidence=[str(projection_path(root))],
        )
    return document


__all__ = [
    "binding_from_row",
    "binding_to_schema",
    "build_projection",
    "projection_generation",
    "projection_path",
    "read_projection",
    "require_projection",
]
