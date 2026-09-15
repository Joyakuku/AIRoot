"""Registry entities and the builders that shape them for the published schemas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..exits import AirootError


@dataclass(frozen=True)
class Instance:
    instance_id: str
    kind: str
    capability_id: str
    version: str
    platform: str
    architecture: str
    install_backend_id: str
    artifact_digest: str
    store_path: str
    lifecycle_status: str
    health: str
    tool_id: str | None = None
    file_manifest_digest: str | None = None
    entrypoints: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None
    created_at: str | None = None
    retired_at: str | None = None

    @property
    def entrypoint_path(self) -> str | None:
        return self.entrypoints[0] if self.entrypoints else None


@dataclass(frozen=True)
class DataRoot:
    """A user-declared protected directory whose contents AIROOT observes but never owns."""

    data_root_id: str
    path: str
    role: str
    volume_serial: str
    added_at: str
    whitelist_revision: str | None = None
    acl_baseline: dict[str, Any] | None = None
    active: bool = True

    def to_schema(self) -> dict[str, Any]:
        return {
            "data_root_id": self.data_root_id,
            "path": self.path,
            "role": self.role,
            "volume_serial": self.volume_serial,
            "whitelist_revision": self.whitelist_revision,
            "added_at": self.added_at,
            "active": self.active,
        }


@dataclass(frozen=True)
class ExternalReference:
    """A steward-domain object: facts about someone else's file, never the file."""

    external_id: str
    capability_id: str
    path: str
    management: str
    health: str
    observed_at: str
    capability_kind: str | None = None
    data_root_id: str | None = None
    version: str | None = None
    versions: tuple[str, ...] = ()
    active_version: str | None = None
    architecture: str | None = None
    entrypoints: tuple[str, ...] = ()
    observed_digest: str | None = None
    probe_level: int | None = None
    source_kind: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()

    @property
    def owned(self) -> bool:
        """The steward domain never owns; only owned instances may be uninstalled."""

        return False

    def to_schema(self) -> dict[str, Any]:
        """Full shape (all keys present, ``null`` where unknown) for stable output."""

        return {
            "external_id": self.external_id,
            "capability_id": self.capability_id,
            "capability_kind": self.capability_kind,
            "data_root_id": self.data_root_id,
            "path": self.path,
            "management": self.management,
            "health": self.health,
            "version": self.version,
            "versions": list(self.versions),
            "active_version": self.active_version,
            "architecture": self.architecture,
            "entrypoints": list(self.entrypoints),
            "observed_digest": self.observed_digest,
            "observed_at": self.observed_at,
            "probe_level": self.probe_level,
            "source_kind": self.source_kind,
            "evidence": [dict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class EnvironmentPersist:
    """One variable AIROOT wrote, plus the exact value it replaced."""

    capability_id: str
    scope: str
    variable: str
    value: str
    value_kind: str
    written_at: str
    external_id: str | None = None
    old_value: str | None = None
    old_kind: str | None = None
    plan_id: str | None = None
    plan_hash: str | None = None
    approval_id: str | None = None
    forgotten_at: str | None = None

    @property
    def had_previous_value(self) -> bool:
        return self.old_value is not None

    def to_document(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "scope": self.scope,
            "variable": self.variable,
            "value": self.value,
            "value_kind": self.value_kind,
            "external_id": self.external_id,
            "had_previous_value": self.had_previous_value,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "approval_id": self.approval_id,
            "written_at": self.written_at,
            "forgotten_at": self.forgotten_at,
        }


def environment_persist_from_row(row: Any) -> EnvironmentPersist:
    return EnvironmentPersist(
        capability_id=row["capability_id"],
        scope=row["scope"],
        variable=row["variable"],
        value=row["value"],
        value_kind=row["value_kind"],
        written_at=row["written_at"],
        external_id=row["external_id"],
        old_value=row["old_value"],
        old_kind=row["old_kind"],
        plan_id=row["plan_id"],
        plan_hash=row["plan_hash"],
        approval_id=row["approval_id"],
        forgotten_at=row["forgotten_at"],
    )


@dataclass(frozen=True)
class Binding:
    binding_key: str
    instance_id: str
    scope: str
    zone: str
    exposure: str
    generation: int
    active: bool
    project_id: str | None = None
    updated_at: str | None = None

    def to_schema(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "binding_key": self.binding_key,
            "scope": self.scope,
            "zone": self.zone,
            "active": self.active,
            "generation": self.generation,
            "exposure": self.exposure,
        }
        if self.scope == "project":
            document["project_id"] = self.project_id
        return document


def binding_key(
    capability_id: str,
    scope: str,
    *,
    machine_id: str | None = None,
    root_instance_id: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    platform: str = "windows",
    architecture: str = "x64",
) -> str:
    """Canonical binding key (v0.3 §9.10).

    ``machine`` = capability/machine scope; ``session``/``project`` fold in their
    identity. P1 never derives session/project identity itself — the caller must
    supply it — because the derivation algorithm is still an open item.
    """

    if scope == "machine":
        return f"machine/{capability_id}/{platform}/{architecture}"
    if scope == "session":
        if not session_id:
            raise AirootError("INVALID_INPUT", "session scope requires a session_id")
        return f"session/{session_id}/{capability_id}/{platform}/{architecture}"
    if scope == "project":
        if not project_id:
            raise AirootError("INVALID_INPUT", "project scope requires a project_id")
        return f"project/{project_id}/{capability_id}/{platform}/{architecture}"
    raise AirootError("INVALID_INPUT", f"scope is not bindable: {scope}")


def load_json(column: str | None, default: Any) -> Any:
    if not column:
        return default
    return json.loads(column)


def managed_tool_payload(
    *,
    tool_id: str,
    instance_id: str,
    capability_id: str,
    version: str,
    install_backend_id: str,
    artifact_digest: str,
    store_path: str,
    file_manifest_digest: str,
    lifecycle_status: str,
    health: str,
    entrypoints: list[str],
    source: dict[str, Any],
    file_manifest: list[dict[str, Any]] | None = None,
    created_at: str | None = None,
    retired_at: str | None = None,
    platform: str = "windows",
    architecture: str = "x64",
) -> dict[str, Any]:
    """Instance document shaped for ``managed-tool-instance.schema.json``."""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "tool_id": tool_id,
        "instance_id": instance_id,
        "kind": "managed_tool",
        "capability_id": capability_id,
        "version": version,
        "platform": platform,
        "architecture": architecture,
        "artifact_digest": artifact_digest,
        "install_backend_id": install_backend_id,
        "store_path": store_path,
        "file_manifest_digest": file_manifest_digest,
        "lifecycle_status": lifecycle_status,
        "health": health,
        "entrypoints": list(entrypoints),
        "bindings": [],
        "source": source,
    }
    if file_manifest is not None:
        payload["file_manifest"] = list(file_manifest)
    if created_at is not None:
        payload["created_at"] = created_at
    if retired_at is not None:
        payload["retired_at"] = retired_at
    return payload


def instance_from_row(row: Any) -> Instance:
    payload = load_json(row["payload_json"], {})
    return Instance(
        instance_id=row["instance_id"],
        kind=row["kind"],
        capability_id=row["capability_id"],
        version=row["version"],
        platform=row["platform"],
        architecture=row["architecture"],
        install_backend_id=row["install_backend_id"],
        artifact_digest=row["artifact_digest"],
        store_path=row["store_path"],
        lifecycle_status=row["lifecycle_status"],
        health=row["health"],
        tool_id=row["tool_id"],
        file_manifest_digest=row["file_manifest_digest"],
        entrypoints=tuple(load_json(row["entrypoints_json"], [])),
        payload=payload,
        created_at=row["created_at"],
        retired_at=row["retired_at"],
    )


def binding_from_row(row: Any) -> Binding:
    return Binding(
        binding_key=row["binding_key"],
        instance_id=row["instance_id"],
        scope=row["scope"],
        zone=row["zone"],
        exposure=row["exposure"],
        generation=row["generation"],
        active=bool(row["active"]),
        project_id=row["project_id"],
        updated_at=row["updated_at"],
    )


def data_root_from_row(row: Any) -> DataRoot:
    return DataRoot(
        data_root_id=row["data_root_id"],
        path=row["path"],
        role=row["role"],
        volume_serial=row["volume_serial"],
        added_at=row["added_at"],
        whitelist_revision=row["whitelist_revision"],
        acl_baseline=load_json(row["acl_baseline_json"], None),
        active=bool(row["active"]),
    )


def external_reference_from_row(row: Any) -> ExternalReference:
    return ExternalReference(
        external_id=row["external_id"],
        capability_id=row["capability_id"],
        path=row["path"],
        management=row["management"],
        health=row["health"],
        observed_at=row["observed_at"],
        capability_kind=row["capability_kind"],
        data_root_id=row["data_root_id"],
        version=row["version"],
        versions=tuple(load_json(row["versions_json"], [])),
        active_version=row["active_version"],
        architecture=row["architecture"],
        entrypoints=tuple(load_json(row["entrypoints_json"], [])),
        observed_digest=row["observed_digest"],
        probe_level=row["probe_level"],
        source_kind=row["source_kind"],
        evidence=tuple(load_json(row["evidence_json"], [])),
    )
