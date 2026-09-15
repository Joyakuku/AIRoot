"""A deterministic, side-effect-free Capability Extension.

This is P1's stand-in for a real capability: it proves the protocol (manifest →
declared operations → envelope → stable reason codes) without touching the host.
Undeclared operations are refused, and nothing here writes registry or store.
"""

from __future__ import annotations

from typing import Any

from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from .envelope import envelope, failure_envelope
from .manifest import declared_operations, operation_policy


class FakeExtension:
    """Capability ``fake-echo``; deterministic output, no side effects."""

    def __init__(self, manifest: dict[str, Any], *, clock: Clock = SYSTEM_CLOCK) -> None:
        self.manifest = manifest
        self.clock = clock

    @property
    def extension_id(self) -> str:
        return str(self.manifest["extension_id"])

    @property
    def capability_types(self) -> list[str]:
        return list(self.manifest.get("capability_types", []))

    # ------------------------------------------------------------ operations #

    def probe(self) -> dict[str, Any]:
        """Availability, capability coverage and declared health checks."""

        return envelope(
            self.extension_id,
            "probe",
            clock=self.clock,
            data={
                "available": True,
                "capability_types": self.capability_types,
                "implementation_id": self.manifest["implementation_id"],
                "implementation_kind": self.manifest["implementation_kind"],
                "health_checks": list(self.manifest.get("health_checks", [])),
                "platforms": list(self.manifest.get("platforms", [])),
                "scopes": list(self.manifest.get("scopes", [])),
            },
            evidence=[{"kind": "manifest", "detail": f"{self.extension_id} loaded from the published schema"}],
        )

    def self_test(self) -> dict[str, Any]:
        """Deterministic self check: the envelope contract, not a real capability."""

        return envelope(
            self.extension_id,
            "status",
            clock=self.clock,
            data={
                "health": "healthy",
                "freshness": "static",
                "operations": sorted(declared_operations(self.manifest)),
                "self_test": {"passed": True, "checks": ["envelope", "manifest", "operation-policy"]},
            },
            evidence=[{"kind": "self_test", "detail": "deterministic fake extension; no external state touched"}],
        )

    def invoke(self, *, echo: Any, declared_side_effects: bool = False) -> dict[str, Any]:
        """Return the caller's payload unchanged, with the declared operation policy."""

        policy = operation_policy(self.manifest, "invoke")
        if policy["operation_kind"] != "read" or policy["approval_required"]:
            raise AirootError(
                "EXTENSION_OPERATION_UNKNOWN",
                "this build only implements read operations without approval",
                evidence=[str(policy)],
            )
        return envelope(
            self.extension_id,
            "invoke",
            clock=self.clock,
            data={
                "echo": echo,
                "operation_kind": policy["operation_kind"],
                "target_scope": policy["target_scope"],
                "side_effects": list(self.manifest.get("side_effects", [])),
            },
        )

    # -------------------------------------------------------------- dispatch #

    def run(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Dispatch, refusing anything the manifest does not declare (exit code 9)."""

        if operation not in declared_operations(self.manifest):
            raise AirootError(
                "EXTENSION_OPERATION_UNKNOWN",
                f"{self.extension_id} does not declare operation {operation}",
                evidence=sorted(declared_operations(self.manifest)),
            )
        handlers = {"probe": self.probe, "status": self.self_test, "invoke": self.invoke}
        handler = handlers.get(operation)
        if handler is None:
            return failure_envelope(
                self.extension_id,
                operation,
                clock=self.clock,
                status="error",
                reason_code="EXTENSION_UNAVAILABLE",
                warnings=[f"declared but not implemented in P1: {operation}"],
            )
        return handler(**kwargs)


def load_fake_extension(clock: Clock = SYSTEM_CLOCK, directory: Any = None) -> FakeExtension:
    """Load the bundled fake extension from ``cli/extensions``."""

    from .manifest import load_manifests

    manifests = load_manifests(directory)
    for manifest in manifests.values():
        if "fake-echo" in manifest.get("capability_types", []):
            return FakeExtension(manifest, clock=clock)
    raise AirootError(
        "EXTENSION_UNAVAILABLE",
        "no extension declaring capability fake-echo was found",
        evidence=sorted(manifests),
    )
