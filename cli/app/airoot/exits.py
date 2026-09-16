"""Reason codes, exit codes and the single error type.

Exit codes are frozen by ``AIROOT-总体方案规划-v0.3.md`` §15.6: 0-9, decided by
the command's final state, never invented by a submodule. Every reason code used
anywhere in the core must appear in :data:`REASON_EXIT`; ``tests`` assert that
exhaustively, and the table is mirrored in
``docs/AIROOT-v0.3-诊断码与ReasonCode表.md``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

EXIT_SUCCESS = 0
EXIT_NOT_FOUND = 1
EXIT_DEGRADED = 2
EXIT_BROKEN = 3
EXIT_APPROVAL = 4
EXIT_PRIVILEGE = 5
EXIT_RECOVERY = 6
EXIT_INVALID_PLAN = 7
EXIT_INVALID_INPUT = 8
EXIT_EXTENSION = 9

EXIT_MEANINGS = {
    EXIT_SUCCESS: "healthy/success",
    EXIT_NOT_FOUND: "not found",
    EXIT_DEGRADED: "degraded or drift",
    EXIT_BROKEN: "broken",
    EXIT_APPROVAL: "approval required/expired",
    EXIT_PRIVILEGE: "privilege required",
    EXIT_RECOVERY: "transaction recovery required",
    EXIT_INVALID_PLAN: "invalid plan or provenance",
    EXIT_INVALID_INPUT: "invalid input/schema",
    EXIT_EXTENSION: "extension unavailable",
}

# reason_code -> exit code. Documented examples (v0.3 §15.6) plus the codes P1
# introduces; see ADR-0002/ADR-0003 and the reason-code table doc.
REASON_EXIT: dict[str, int] = {
    # 0 - success. v0.3 §15.6 names this code `OK`, but `OK` is two characters and
    # every published schema constrains reason_code to `^[A-Z][A-Z0-9_]{2,63}$`,
    # so `OK` can never be emitted through a schema-validated field. The published
    # schema is layer 1 of the spec hierarchy, hence `SUCCESS` (see ADR-0003).
    # CURRENT_PROCESS_ENV_OLD means "result is usable but this shell has not picked
    # the new generation up yet" (v0.3 §15.6).
    "SUCCESS": EXIT_SUCCESS,    "CURRENT_PROCESS_ENV_OLD": EXIT_SUCCESS,
    "POLICY_ONLY_MODE": EXIT_SUCCESS,
    # Steward model (ADR-0004) informational codes: "unknown" is a legitimate answer.
    "SIZE_ESTIMATE_UNAVAILABLE": EXIT_SUCCESS,
    "REFERENCE_UNPROBED": EXIT_SUCCESS,
    "UNMANAGED_OBJECT_PRESENT": EXIT_SUCCESS,
    "WHITELIST_REVISION_STALE": EXIT_SUCCESS,
    # 1 - nothing usable
    "NOT_FOUND": EXIT_NOT_FOUND,
    "VERSION_UNSATISFIED": EXIT_NOT_FOUND,
    # A verb the planning document names and this build deliberately does not carry (ADR-0027).
    # Draft §101 measured what used to happen: all six declared-absent verbs died in argparse as
    # `INVALID_INPUT` (8) with "invalid choice: 'bootstrap'", which reads as a typo. Neither of the
    # two neighbouring codes is true: 8 blames the caller's input, and 5 (`PRIVILEGE_REQUIRED`,
    # "elevate and retry") promises something elevation cannot deliver while the broker does not
    # exist. What is true is that nothing usable came back, which is exit 1.
    "NOT_IMPLEMENTED": EXIT_NOT_FOUND,
    # 2 - usable but needs explanation/repair
    "DEGRADED": EXIT_DEGRADED,
    "DRIFT_DETECTED": EXIT_DEGRADED,
    "SEARCH_RESULT_STALE": EXIT_DEGRADED,
    "STALE_GENERATION": EXIT_DEGRADED,
    "SESSION_STATE_STALE": EXIT_DEGRADED,
    "PATH_EXPOSURE_VIOLATION": EXIT_DEGRADED,
    "DESIRED_NOT_SATISFIED": EXIT_DEGRADED,
    "ORPHANED_STORE_INSTANCE": EXIT_DEGRADED,
    "REGISTRY_PROJECTION_STALE": EXIT_DEGRADED,
    "AUDIT_PROJECTION_DRIFT": EXIT_DEGRADED,
    "CURRENT_SOURCE_DEGRADED": EXIT_DEGRADED,
    "REFERENCE_IN_USE": EXIT_DEGRADED,
    "REFERENCE_STALE": EXIT_DEGRADED,
    "REFERENCE_DRIFTED": EXIT_DEGRADED,
    "DATA_ROOT_ACL_DRIFT": EXIT_DEGRADED,
    # A payload where the contract says payloads may not be (draft §66). `store` is the only payload
    # storage and `tools`/`env` are binding/view directories that carry none (冻结契约 §5.3,
    # 规划 §7:590) — so an `artifact.json` under a view, or an instance row whose `store_path` points
    # outside `store/`, is **layout drift**: exit 2, not 3, because nothing that *is* declared is
    # broken; what is broken is the declaration's right to be honoured, and `where` refuses to
    # activate it for exactly that reason.
    "PAYLOAD_OUTSIDE_STORE": EXIT_DEGRADED,
    # The filesystem refused a step of an install (draft §62). Exit 2 rather than 3 or 7 because
    # neither is true: no declared object is broken (the previous generation is untouched, and the
    # rollback restores it), and the plan is not invalid (a full disk or a locked file says nothing
    # about the plan). Same shape as `CHILD_PROCESS_FAILED`: AIROOT did its part, the environment did
    # not. `DISK_FULL`/`FILE_LOCKED` were deliberately **not** split out — the evidence carries the
    # errno and the failing path, and the caller's next move (free space / release the lock / fix the
    # permission, then re-plan) is the same for all of them (draft §62.4).
    "INSTALL_IO_FAILED": EXIT_DEGRADED,
    # The search profile's degraded tier (search protocol §6.6). `SEARCH_RESULT_STALE` was
    # reserved in P1; the rest arrive with the protocol surface (§31).
    "SEARCH_INDEX_DEGRADED": EXIT_DEGRADED,
    "SEARCH_JOURNAL_GAP": EXIT_DEGRADED,
    "SEARCH_ROOT_UNAVAILABLE": EXIT_DEGRADED,
    "SEARCH_PERMISSION_FILTERED": EXIT_DEGRADED,
    "SEARCH_TIMEOUT": EXIT_DEGRADED,
    "SEARCH_FALLBACK_USED": EXIT_DEGRADED,
    # Generic extension family: a timeout or a cancelled operation is not a broken extension,
    # but the answer is incomplete — same tier as `SEARCH_TIMEOUT` (ADR-0017).
    "EXTENSION_TIMEOUT": EXIT_DEGRADED,
    "EXTENSION_CANCELLED": EXIT_DEGRADED,
    "EXTENSION_HEALTH_DEGRADED": EXIT_DEGRADED,
    # 3 - declared object cannot be used safely
    "BROKEN": EXIT_BROKEN,
    "CONFLICT_MANAGED_BROKEN": EXIT_BROKEN,
    "REGISTRY_INTEGRITY_FAILED": EXIT_BROKEN,
    "PAYLOAD_MISSING": EXIT_BROKEN,
    "MANIFEST_DIGEST_MISMATCH": EXIT_BROKEN,
    "BINDING_TARGET_MISSING": EXIT_BROKEN,
    "MULTIPLE_ACTIVE_BINDINGS": EXIT_BROKEN,
    "EXTERNAL_REFERENCE_DRIFTED": EXIT_BROKEN,
    # 4 - approval
    "APPROVAL_REQUIRED": EXIT_APPROVAL,
    "APPROVAL_EXPIRED": EXIT_APPROVAL,
    "APPROVAL_REPLAYED": EXIT_APPROVAL,
    "APPROVAL_REVOKED": EXIT_APPROVAL,
    "INVALID_APPROVAL": EXIT_APPROVAL,
    "POLICY_REVISION_MISMATCH": EXIT_APPROVAL,
    "SCOPE_UPGRADE_REQUIRES_APPROVAL": EXIT_APPROVAL,
    "SCOPE_CONFIRMATION_REQUIRED": EXIT_APPROVAL,
    "PERSISTENCE_REQUIRES_APPROVAL": EXIT_APPROVAL,
    # 5 - privilege
    "PRIVILEGE_REQUIRED": EXIT_PRIVILEGE,
    "ACL_MISMATCH": EXIT_PRIVILEGE,
    # 6 - recovery
    "RECOVERY_REQUIRED": EXIT_RECOVERY,
    "PENDING_TRANSACTION": EXIT_RECOVERY,
    "REGISTRY_MISSING": EXIT_RECOVERY,
    "ROOT_MARKER_INVALID": EXIT_RECOVERY,
    "ROOT_MARKER_MISSING": EXIT_RECOVERY,
    "VOLUME_IDENTITY_MISMATCH": EXIT_RECOVERY,
    "JOURNAL_TRUNCATED": EXIT_RECOVERY,
    "DATA_ROOT_MISSING": EXIT_RECOVERY,
    "DATA_ROOT_VOLUME_MISMATCH": EXIT_RECOVERY,
    # 7 - plan/provenance
    "INVALID_PLAN": EXIT_INVALID_PLAN,
    "PROVENANCE_FAILED": EXIT_INVALID_PLAN,
    "DIGEST_MISMATCH": EXIT_INVALID_PLAN,
    "INSTANCE_CONFLICT": EXIT_INVALID_PLAN,
    "UNSUPPORTED_BACKEND": EXIT_INVALID_PLAN,
    "ILLEGAL_TRANSITION": EXIT_INVALID_PLAN,
    "OWNERSHIP_REQUIRED": EXIT_INVALID_PLAN,
    # 8 - input/schema
    "INVALID_INPUT": EXIT_INVALID_INPUT,
    "SCHEMA_UNSUPPORTED": EXIT_INVALID_INPUT,
    "ROOT_NOT_RESOLVED": EXIT_INVALID_INPUT,
    "PATH_ESCAPES_ROOT": EXIT_INVALID_INPUT,
    "UNC_NOT_ALLOWED": EXIT_INVALID_INPUT,
    "REPARSE_POINT_REJECTED": EXIT_INVALID_INPUT,
    "SELF_VALIDATION_FAILED": EXIT_INVALID_INPUT,
    "PERSISTENCE_TARGET_FORBIDDEN": EXIT_INVALID_INPUT,
    "ENVIRONMENT_PERSIST_NOT_FOUND": EXIT_INVALID_INPUT,
    # Search profile: a request field or a cursor that the caller got wrong (protocol §6.3).
    "SEARCH_QUERY_INVALID": EXIT_INVALID_INPUT,
    "SEARCH_CURSOR_INVALID": EXIT_INVALID_INPUT,
    # An answer that violates the published response schema is a schema failure, like
    # `SELF_VALIDATION_FAILED` — the input was fine, the *output* is not (ADR-0017).
    "EXTENSION_INPUT_INVALID": EXIT_INVALID_INPUT,
    "EXTENSION_OUTPUT_INVALID": EXIT_INVALID_INPUT,
    # 2 - degraded (the AIROOT operation succeeded; the child it launched did not)
    "CHILD_PROCESS_FAILED": EXIT_DEGRADED,
    # 9 - extension
    "EXTENSION_UNAVAILABLE": EXIT_EXTENSION,
    "EXTENSION_VERSION_UNSUPPORTED": EXIT_EXTENSION,
    "EXTENSION_MANIFEST_INVALID": EXIT_EXTENSION,
    "EXTENSION_OPERATION_UNKNOWN": EXIT_EXTENSION,
    "CAPABILITY_NOT_DECLARED": EXIT_EXTENSION,
    # Search profile: the active implementation cannot be invoked at all (protocol §6.6).
    "SEARCH_NOT_READY": EXIT_EXTENSION,
    "SEARCH_BACKEND_UNAVAILABLE": EXIT_EXTENSION,
    # Generic extension family (`EXTENSION_NOT_FOUND` = no extension provides the capability,
    # `EXTENSION_UNAVAILABLE` = the selected one cannot be invoked right now; ADR-0017).
    "EXTENSION_NOT_FOUND": EXIT_EXTENSION,
    "EXTENSION_PERMISSION_DENIED": EXIT_EXTENSION,
    "EXTENSION_DEPENDENCY_MISSING": EXIT_EXTENSION,
    "EXTENSION_SIDE_EFFECT_BLOCKED": EXIT_EXTENSION,
}

_REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class AirootError(Exception):
    """A failure that carries a frozen reason code and its evidence."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        evidence: Iterable[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if reason_code not in REASON_EXIT:
            raise KeyError(f"unregistered reason code: {reason_code}")
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.evidence = list(evidence or [])
        self.details = dict(details or {})

    @property
    def exit_code(self) -> int:
        return REASON_EXIT[self.reason_code]

    def to_envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "status": "failed",
            "reason_code": self.reason_code,
            "message": self.message,
            "evidence": self.evidence,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def exit_code_for(reason_code: str) -> int:
    try:
        return REASON_EXIT[reason_code]
    except KeyError as exc:  # pragma: no cover - guarded by construction
        raise AirootError("INVALID_INPUT", f"unregistered reason code: {reason_code}") from exc


def is_valid_reason_code(code: str) -> bool:
    return bool(_REASON_PATTERN.match(code))
