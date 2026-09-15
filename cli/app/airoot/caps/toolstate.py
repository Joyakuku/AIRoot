"""Read-only inspection of AIROOT-owned instances (§15.4:1601-1603).

Three verbs live here, and all three are deliberately *observers*:

``tool list``
    every owned instance with its definition facts (capability, version, architecture),
    lifecycle, health, active binding and source.

``tool status <id>``
    "read and verify manifest, payload, binding and exposure; must not switch active".

``tool verify <id>``
    "run probe/digest/entrypoint verification; must not adopt or repair".

The boundary matters more than the output: none of these functions writes the registry, bumps a
generation, creates a file or runs a payload. Verification is a digest comparison plus an
entrypoint *existence* check — the evidence ladder (§9.9) never executes a candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canon import tree_digest
from ..exits import AirootError
from ..paths import from_root_relative
from ..registry.entities import load_json
from .version import satisfies


def _binding_for(registry: Any, instance_id: str) -> dict[str, Any] | None:
    for row in registry.bindings(active_only=True):
        if str(row["instance_id"]) == instance_id:
            return {
                "binding_key": str(row["binding_key"]),
                "scope": str(row["scope"]),
                "zone": str(row["zone"]),
                "exposure": str(row["exposure"]),
                "generation": int(row["generation"]),
            }
    return None


def _instance_document(registry: Any, row: Any) -> dict[str, Any]:
    instance_id = str(row["instance_id"])
    payload = load_json(row["payload_json"], {})
    source = payload.get("source") or {}
    return {
        "instance_id": instance_id,
        "capability_id": str(row["capability_id"]),
        "kind": str(row["kind"]),
        "version": str(row["version"]),
        "platform": str(row["platform"]),
        "architecture": str(row["architecture"]),
        "install_backend_id": str(row["install_backend_id"]),
        "lifecycle_status": str(row["lifecycle_status"]),
        "health": str(row["health"]),
        "store_path": str(row["store_path"]),
        "entrypoints": [str(item) for item in load_json(row["entrypoints_json"], [])],
        "artifact_digest": str(row["artifact_digest"]),
        "collected_at": row["collected_at"],
        "active_binding": _binding_for(registry, instance_id),
        "source": {
            "kind": source.get("kind"),
            "locator": source.get("locator"),
            "publisher": (source.get("provenance") or {}).get("publisher"),
        }
        if source
        else None,
    }


def _desired_index(root: Path | None) -> dict[str, Any]:
    """The pinned intent per capability, or an empty index when nothing is pinned."""

    if root is None:
        return {}
    from .desired import load_desired

    path = Path(root) / "state" / "desired.json"
    if not path.is_file():
        return {}
    try:
        manifest = load_desired(root)
    except AirootError:
        return {}
    return {item.capability_id: item for item in manifest.capabilities}


def list_tools(registry: Any, *, capability_id: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Every owned instance, newest state, no writes. Pins are shown when a root is given."""

    desired = _desired_index(root)
    instances = []
    for row in registry.instances():
        if capability_id is not None and str(row["capability_id"]) != capability_id:
            continue
        document = _instance_document(registry, row)
        pin = desired.get(str(row["capability_id"]))
        document["desired_version"] = pin.version if pin else None
        document["in_sync"] = (
            None if pin is None else bool(satisfies(str(row["version"]), pin.version))
        )
        instances.append(document)
    instances.sort(key=lambda item: item["instance_id"])
    return {
        "schema_version": 1,
        "instances": instances,
        "count": len(instances),
        "bound": sum(1 for item in instances if item["active_binding"]),
        "desired": [item.to_document() for item in desired.values()],
        "out_of_sync": sorted(
            item["instance_id"] for item in instances if item["in_sync"] is False
        ),
        "reason_code": "SUCCESS",
    }


@dataclass
class StatusFinding:
    severity: str
    code: str
    detail: str

    def to_document(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "detail": self.detail}


@dataclass
class ToolStatus:
    instance: dict[str, Any]
    findings: list[StatusFinding] = field(default_factory=list)
    payload_present: bool = False
    entrypoints_present: list[str] = field(default_factory=list)
    entrypoints_missing: list[str] = field(default_factory=list)

    @property
    def reason_code(self) -> str:
        for finding in self.findings:
            if finding.severity == "error":
                return finding.code
        for finding in self.findings:
            if finding.severity == "warning":
                return "DEGRADED"
        return "SUCCESS"

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "instance": self.instance,
            "payload_present": self.payload_present,
            "entrypoints_present": self.entrypoints_present,
            "entrypoints_missing": self.entrypoints_missing,
            "findings": [finding.to_document() for finding in self.findings],
            "reason_code": self.reason_code,
        }


def _resolve_instance(registry: Any, instance_id: str) -> Any:
    row = registry.instance(instance_id)
    if row is not None:
        return row
    for candidate in registry.instances():
        if str(candidate["capability_id"]) == instance_id:
            return candidate
    raise AirootError(
        "NOT_FOUND",
        f"no instance matches {instance_id}",
        evidence=sorted(str(item["instance_id"]) for item in registry.instances()),
    )


def tool_status(registry: Any, instance_id: str, *, root: Path) -> ToolStatus:
    """Read and verify manifest/payload/binding/exposure. Never switches active."""

    row = _resolve_instance(registry, instance_id)
    document = _instance_document(registry, row)
    finding_list: list[StatusFinding] = []
    store_dir = from_root_relative(str(row["store_path"]), Path(root))
    payload_present = store_dir.is_dir()
    present: list[str] = []
    missing: list[str] = []

    if row["collected_at"]:
        finding_list.append(
            StatusFinding("info", "PAYLOAD_COLLECTED", f"payload was collected at {row['collected_at']}")
        )
    elif not payload_present:
        # A declared instance whose payload is gone is a defect, not a state (D3).
        finding_list.append(
            StatusFinding("error", "PAYLOAD_MISSING", f"{row['store_path']} does not exist")
        )
    else:
        for name in document["entrypoints"]:
            target = store_dir / Path(str(name))
            (present if target.is_file() else missing).append(str(name))
        if missing:
            finding_list.append(
                StatusFinding(
                    "error",
                    "MANIFEST_DIGEST_MISMATCH",
                    f"declared entrypoints are missing from the payload: {', '.join(missing)}",
                )
            )

    binding = document["active_binding"]
    if binding is None and str(row["lifecycle_status"]) == "active" and not row["collected_at"]:
        finding_list.append(
            StatusFinding(
                "warning",
                "DEGRADED",
                "lifecycle says active but no active binding exists for this instance",
            )
        )
    if binding is not None and not payload_present and not row["collected_at"]:
        finding_list.append(
            StatusFinding("error", "BINDING_TARGET_MISSING", "an active binding points at a missing payload")
        )

    return ToolStatus(
        instance=document,
        findings=finding_list,
        payload_present=payload_present,
        entrypoints_present=sorted(present),
        entrypoints_missing=sorted(missing),
    )


def tool_verify(registry: Any, instance_id: str, *, root: Path) -> dict[str, Any]:
    """Digest/payload/entrypoint verification. Never adopts, never repairs."""

    row = _resolve_instance(registry, instance_id)
    document = _instance_document(registry, row)
    store_dir = from_root_relative(str(row["store_path"]), Path(root))
    problems: list[dict[str, Any]] = []

    if row["collected_at"]:
        return {
            "schema_version": 1,
            "instance_id": document["instance_id"],
            "verified": False,
            "collected_at": row["collected_at"],
            "expected_digest": str(row["artifact_digest"]),
            "actual_digest": None,
            "problems": [],
            "repaired": False,
            "adopted": False,
            "reason_code": "SUCCESS",
            "note": "the payload was collected by an approved gc; there is nothing left to verify",
        }

    if not store_dir.is_dir():
        problems.append({"code": "PAYLOAD_MISSING", "detail": f"{row['store_path']} does not exist"})

    actual = tree_digest(store_dir) if store_dir.is_dir() else None
    if actual is not None and actual != str(row["artifact_digest"]):
        problems.append(
            {
                "code": "MANIFEST_DIGEST_MISMATCH",
                "detail": f"store payload digest is {actual}, declared {row['artifact_digest']}",
            }
        )

    for name in document["entrypoints"]:
        if not (store_dir / Path(str(name))).is_file():
            problems.append({"code": "PAYLOAD_MISSING", "detail": f"entrypoint {name} is missing"})

    reason = problems[0]["code"] if problems else "SUCCESS"
    return {
        "schema_version": 1,
        "instance_id": document["instance_id"],
        "verified": not problems,
        "expected_digest": str(row["artifact_digest"]),
        "actual_digest": actual,
        "entrypoints": document["entrypoints"],
        "problems": problems,
        "repaired": False,
        "adopted": False,
        "reason_code": reason,
    }


__all__ = ["StatusFinding", "ToolStatus", "list_tools", "tool_status", "tool_verify"]
