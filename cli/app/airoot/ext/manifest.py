"""Extension manifest loading.

The published ``extension-manifest.schema.json`` is authoritative. Note that it
requires the per-operation map under ``operations`` while the planning document's
example calls it ``operation_policies`` — the schema wins (ADR-0003), and a
manifest carrying the document's spelling is rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import EXTENSIONS_DIR, PROTOCOL_VERSION
from ..exits import AirootError
from ..schema_io import validate_document


def manifest_paths(directory: Path | None = None) -> list[Path]:
    target = Path(directory) if directory is not None else EXTENSIONS_DIR
    if not target.is_dir():
        return []
    return sorted(target.glob("*.json"))


def require_supported_protocol(manifest: dict[str, Any]) -> None:
    declared = int(manifest.get("protocol_version", 0))
    if declared != PROTOCOL_VERSION:
        raise AirootError(
            "EXTENSION_VERSION_UNSUPPORTED",
            f"extension {manifest.get('extension_id')} speaks protocol_version {declared}",
            evidence=[f"supported={PROTOCOL_VERSION}"],
        )


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AirootError("EXTENSION_UNAVAILABLE", f"extension manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(
            "EXTENSION_MANIFEST_INVALID",
            f"extension manifest is unreadable: {path}",
            evidence=[str(exc)],
        ) from exc
    validate_document("extension-manifest", manifest, reason_code="EXTENSION_MANIFEST_INVALID")
    require_supported_protocol(manifest)
    return manifest


def load_manifests(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every valid manifest, keyed by ``extension_id``; failures are raised, not skipped."""

    manifests: dict[str, dict[str, Any]] = {}
    for path in manifest_paths(directory):
        manifest = load_manifest(path)
        extension_id = str(manifest["extension_id"])
        if extension_id in manifests:
            raise AirootError(
                "EXTENSION_MANIFEST_INVALID",
                f"duplicate extension_id {extension_id}",
                evidence=[str(path)],
            )
        manifests[extension_id] = manifest
    return manifests


def register_manifests(registry: Any, manifests: dict[str, dict[str, Any]]) -> list[str]:
    """Record the loaded manifests as declared state."""

    registered: list[str] = []
    with registry.write(expected_generation=registry.generation) as connection:
        for manifest in manifests.values():
            registry.upsert_extension(connection, manifest)
            registered.append(str(manifest["extension_id"]))
        registry.append_event(
            connection,
            state="EXTENSIONS_LOADED",
            detail=f"registered {len(registered)} capability extension(s)",
            outcome="ok",
        )
    return registered


def declared_operations(manifest: dict[str, Any]) -> set[str]:
    return set(manifest.get("operations", {}).keys())


def operation_policy(manifest: dict[str, Any], operation: str) -> dict[str, Any]:
    policies = manifest.get("operations", {})
    if operation not in policies:
        raise AirootError(
            "EXTENSION_OPERATION_UNKNOWN",
            f"extension {manifest.get('extension_id')} does not declare operation {operation}",
            evidence=sorted(policies),
        )
    return dict(policies[operation])
