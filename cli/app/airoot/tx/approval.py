"""Approval token verification.

``airoot approve`` only *consumes* approvals; it never mints them (v0.3 §8.5 and
the broker plan §5). P1 therefore implements verification only:

* ``test_hmac_sha256`` — the P1 simulation issuer, keyed by a test keyring under
  ``state/test-keyring.json``. ``docs/schema/README.md`` permits this algorithm
  only for the fake slice, which P1's simulation extends.
* ``ed25519`` — recognised but **not implemented in P1**; a token using it is
  refused with ``PROVENANCE_FAILED`` rather than silently accepted.

The signing side intentionally does not exist in the core: it lives in the test
issuer (``cli/tests/fake_issuer.py``) so the CLI can never manufacture consent.

Consequence, stated plainly: **this build cannot produce an approval.** Every path
that consumes one (``approve``, ``install``, ``env persist``, ``tool gc --apply``,
``uninstall``) therefore stops at :func:`load_keyring`, and an ``ed25519`` token stops
at :func:`verify_signature`. Both refusals carry :data:`ISSUER_PENDING` so that any one
of those paths tells the reader the same thing and names the pending decision (ADR-0024).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Mapping

from ..canon import signable_payload
from ..clock import Clock, SYSTEM_CLOCK, parse_timestamp
from ..exits import AirootError
from ..schema_io import validate_document

KEYRING_RELATIVE = "state/test-keyring.json"
TEST_ALGORITHM = "test_hmac_sha256"
PRODUCTION_ALGORITHM = "ed25519"

#: The one sentence every refusal caused by the absent production issuer must contain.
#: Five different commands reach it, and they used to explain themselves two different ways
#: ("no keyring" vs "ed25519 not implemented"), which reads like two unrelated gaps rather
#: than one decision that has not been taken. ADR-0024 is that decision brief; a test pins
#: this pointer so it cannot rot silently.
ISSUER_PENDING = "no production approval issuer exists in this build (ADR-0024 is the pending decision)"


def keyring_path(root: Path) -> Path:
    return Path(root) / KEYRING_RELATIVE


def write_test_keyring(root: Path, keys: Mapping[str, bytes]) -> None:
    """Test/bootstrap helper: install the P1 test issuer keyring."""

    path = keyring_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"keys": {key_id: base64.b64encode(secret).decode("ascii") for key_id, secret in keys.items()}}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_keyring(root: Path) -> dict[str, bytes]:
    path = keyring_path(root)
    if not path.is_file():
        raise AirootError(
            "PROVENANCE_FAILED",
            f"no approval keyring is installed; {ISSUER_PENDING}",
            evidence=[
                str(path),
                "the only issuer is the test one (cli/tests/fake_issuer.py); the core verifies but never mints",
                "approve/install/env persist/tool gc --apply/uninstall cannot complete on a real machine until this is decided",
            ],
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            str(key_id): base64.b64decode(str(value))
            for key_id, value in dict(payload.get("keys", {})).items()
        }
    except (OSError, ValueError, KeyError) as exc:
        raise AirootError("PROVENANCE_FAILED", f"approval keyring is unreadable: {path}", evidence=[str(exc)]) from exc


def signable_bytes(token: dict[str, Any]) -> bytes:
    return signable_payload(token, omit=("signature", "consumed_at"))


def verify_signature(token: dict[str, Any], keyring: Mapping[str, bytes]) -> None:
    signature = token.get("signature") or {}
    algorithm = signature.get("algorithm")
    key_id = str(signature.get("key_id", ""))
    value = str(signature.get("value", ""))

    if algorithm == PRODUCTION_ALGORITHM:
        raise AirootError(
            "PROVENANCE_FAILED",
            f"ed25519 approval verification is not implemented; {ISSUER_PENDING}",
            evidence=[
                "the protected approval issuer is P2 (caps/acl.py observes but never writes)",
                "a test_hmac_sha256 token proves the consumption side only",
            ],
        )
    if algorithm != TEST_ALGORITHM:
        raise AirootError("INVALID_APPROVAL", f"unsupported signature algorithm: {algorithm}")
    if key_id not in keyring:
        raise AirootError("INVALID_APPROVAL", f"unknown signing key: {key_id}")
    if not value.startswith("base64:"):
        raise AirootError("INVALID_APPROVAL", "signature value must be base64-prefixed")
    try:
        provided = base64.b64decode(value[len("base64:") :])
    except (ValueError, TypeError) as exc:
        raise AirootError("INVALID_APPROVAL", "signature value is not valid base64", evidence=[str(exc)]) from exc

    expected = hmac.new(keyring[key_id], signable_bytes(token), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, provided):
        raise AirootError("INVALID_APPROVAL", "approval signature does not match the token contents")


def verify_approval(
    registry: Any,
    plan: dict[str, Any],
    token: dict[str, Any],
    *,
    keyring: Mapping[str, bytes],
    clock: Clock = SYSTEM_CLOCK,
    check_consumed: bool = True,
    check_expiry: bool = True,
) -> None:
    """Reject anything that must not be committed, in a stable order.

    ``check_expiry`` is disabled when *resuming* an interrupted transaction: the
    plan and approval were valid when the commit began, and v0.3 §14.4 expects the
    journal to be reconciled rather than the work to be abandoned mid-flight.
    """

    validate_document("approval-token", token, reason_code="INVALID_APPROVAL")
    verify_signature(token, keyring)

    if token["plan_hash"] != plan["plan_hash"]:
        raise AirootError(
            "INVALID_APPROVAL",
            "approval is bound to a different plan hash",
            evidence=[f"approval={token['plan_hash']}", f"plan={plan['plan_hash']}"],
        )
    if token["root_instance_id"] != registry.root_instance_id or token["machine_id"] != registry.machine_id:
        raise AirootError(
            "INVALID_APPROVAL",
            "approval is bound to a different root or machine identity",
            evidence=[
                f"approval_root={token['root_instance_id']}",
                f"registry_root={registry.root_instance_id}",
                f"approval_machine={token['machine_id']}",
                f"registry_machine={registry.machine_id}",
            ],
        )
    if int(token["policy_revision"]) != registry.policy_revision:
        raise AirootError(
            "POLICY_REVISION_MISMATCH",
            "policy revision changed after the plan was approved",
            evidence=[f"approval={token['policy_revision']}", f"registry={registry.policy_revision}"],
        )
    if int(token["policy_revision"]) != int(plan.get("policy_revision", -1)):
        raise AirootError("INVALID_APPROVAL", "approval policy revision does not match the plan")

    now = clock.now()
    if check_expiry and parse_timestamp(str(token["expires_at"])) <= now:
        raise AirootError(
            "APPROVAL_EXPIRED",
            "the approval expired before the commit",
            evidence=[f"expires_at={token['expires_at']}", f"now={clock.timestamp()}"],
        )
    if check_expiry and parse_timestamp(str(plan["expires_at"])) <= now:
        raise AirootError(
            "APPROVAL_EXPIRED",
            "the plan expired before the commit; a new plan and approval are required",
            evidence=[f"plan_expires_at={plan['expires_at']}"],
        )

    if token["approval_mode"] == "human" and not token.get("approved_by_sid"):
        raise AirootError(
            "INVALID_APPROVAL",
            "human approval must record approved_by_sid",
            evidence=["an agent request is never a human approval"],
        )

    stored = registry.approval_by_nonce(str(token["nonce"]))
    if stored is not None and stored["consumed_at"]:
        raise AirootError(
            "APPROVAL_REPLAYED",
            "approval nonce was already consumed",
            evidence=[f"consumed_at={stored['consumed_at']}"],
        )
    if stored is not None and stored["revoked_at"]:
        raise AirootError("APPROVAL_REVOKED", "approval was revoked", evidence=[f"revoked_at={stored['revoked_at']}"])
    if check_consumed:
        row = registry.approval(str(token["approval_id"]))
        if row is not None and row["plan_hash"] != token["plan_hash"]:
            raise AirootError("INVALID_APPROVAL", "stored approval does not match the presented token")


def record_approval(registry: Any, token: dict[str, Any]) -> None:
    """Record a token for consumption; ``approve`` never creates one.

    Recording the same token twice is a replay (P-006), not an idempotent no-op:
    the second call would otherwise look like a fresh authorisation.
    """

    validate_document("approval-token", token, reason_code="INVALID_APPROVAL")
    existing = registry.approval(str(token["approval_id"]))
    if existing is not None:
        raise AirootError(
            "APPROVAL_REPLAYED",
            f"approval {token['approval_id']} was already recorded",
            evidence=[f"nonce={existing['nonce']}"],
        )
    by_nonce = registry.approval_by_nonce(str(token["nonce"]))
    if by_nonce is not None:
        raise AirootError(
            "APPROVAL_REPLAYED",
            "approval nonce was already recorded",
            evidence=[f"approval_id={by_nonce['approval_id']}"],
        )
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_approval(connection, token)
        registry.append_event(
            connection,
            state="APPROVED",
            detail=(
                f"approval {token['approval_id']} accepted for consumption "
                f"(mode={token['approval_mode']})"
            ),
            approval_id=token["approval_id"],
            plan_hash=token["plan_hash"],
            reason_code=None,
            outcome="ok",
            # The event that establishes the approval records *which kind* it was, so a policy
            # approval can never be read back as a human one (三大核心契约 决策3, draft §65). This is
            # the site that matters: every later event on this plan points here through `approval_id`.
            approval_mode=str(token["approval_mode"]),
        )
