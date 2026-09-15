"""``inventory``: the read-only declared-state projection, optionally filtered.

The projection is derived, never edited by hand, and always shaped by
``registry-projection.schema.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..exits import AirootError
from ..registry.projection import build_projection
from ..schema_io import validate_self

INSTANCE_CLASSES = {"managed_tool", "runtime"}
EXTERNAL_CLASSES = {"external_reference", "unmanaged", "project_owned", "quarantined"}
SCOPES = {"system", "machine", "session", "project"}


def inventory(
    registry: Any,
    *,
    scope: str | None = None,
    klass: str | None = None,
) -> dict[str, Any]:
    """Declared state as a schema-valid projection, filtered on request."""

    if scope is not None and scope not in SCOPES:
        raise AirootError("INVALID_INPUT", f"unknown scope: {scope}", evidence=sorted(SCOPES))
    if klass is not None and klass not in INSTANCE_CLASSES | EXTERNAL_CLASSES:
        raise AirootError(
            "INVALID_INPUT",
            f"unknown class: {klass}",
            evidence=sorted(INSTANCE_CLASSES | EXTERNAL_CLASSES),
        )

    document = build_projection(registry)
    if klass in INSTANCE_CLASSES:
        document["instances"] = [item for item in document["instances"] if item["kind"] == klass]
        document["external_references"] = []
    elif klass in EXTERNAL_CLASSES:
        document["instances"] = []
        document["external_references"] = [
            item for item in document["external_references"] if item["management"] == klass
        ]
    if scope is not None:
        document["bindings"] = [item for item in document["bindings"] if item["scope"] == scope]

    validate_self("registry-projection", document)
    return document


def store_objects(root: Path) -> list[str]:
    """Root-relative store object directories, for reconciliation reports."""

    store = Path(root) / "store"
    if not store.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in store.rglob("*") if path.is_dir())
