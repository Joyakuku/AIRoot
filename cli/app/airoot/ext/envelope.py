"""The common Capability Extension response envelope.

Every extension answers with the same envelope (v0.3 §11.3): profile-specific
results go inside ``data``. P1 always declares ``security_mode=policy_only``
because there is no ACL-enforcing broker yet.
"""

from __future__ import annotations

from typing import Any

from ..clock import Clock, SYSTEM_CLOCK, parse_timestamp
from ..schema_io import validate_self


def envelope(
    extension_id: str,
    operation: str,
    *,
    data: Any,
    clock: Clock = SYSTEM_CLOCK,
    status: str = "ok",
    reason_code: str | None = None,
    warnings: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    security_mode: str = "policy_only",
) -> dict[str, Any]:
    """Build and self-validate an envelope; ``elapsed_ms`` comes from the clock."""

    started_at = clock.timestamp()
    finished_at = clock.timestamp()
    elapsed = parse_timestamp(finished_at) - parse_timestamp(started_at)

    document: dict[str, Any] = {
        "schema_version": 1,
        "extension_id": extension_id,
        "operation": operation,
        "status": status,
        "timing": {
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_ms": max(0, int(elapsed.total_seconds() * 1000)),
        },
        "data": data,
        "warnings": list(warnings or []),
        "evidence": list(evidence or []),
        "reason_code": reason_code,
        "security_mode": security_mode,
        "enforcement": "acl_enforced" if security_mode == "protected_machine" else "same_user_can_bypass",
    }
    validate_self("extension-envelope", document)
    return document


def failure_envelope(
    extension_id: str,
    operation: str,
    *,
    clock: Clock = SYSTEM_CLOCK,
    status: str = "error",
    reason_code: str,
    warnings: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return envelope(
        extension_id,
        operation,
        data=None,
        clock=clock,
        status=status,
        reason_code=reason_code,
        warnings=warnings,
        evidence=evidence,
    )
