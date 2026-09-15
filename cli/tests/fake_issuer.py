"""Test-only approval issuer.

The core deliberately cannot mint approvals (``airoot approve`` only consumes one),
so the P1 simulation obtains its token from here. Mirrors the fake vertical slice:
``test_hmac_sha256`` and a well-known test secret, neither of which is a production
mechanism — ``docs/schema/README.md` permits this algorithm only inside the fake
slice, and P1's simulation is the same protocol-level layer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from pathlib import Path
from typing import Any

from airoot.clock import Clock
from airoot.schema_io import validate_self
from airoot.tx.approval import signable_bytes, write_test_keyring

TEST_SECRET = b"airoot-test-only-secret-v1"
KEY_ID = "fixture-key"
ISSUER = "test-protected-issuer"


def install_keyring(root: Path, secret: bytes = TEST_SECRET) -> None:
    write_test_keyring(root, {KEY_ID: secret})


def sign(token: dict[str, Any], secret: bytes = TEST_SECRET) -> dict[str, Any]:
    signature = hmac.new(secret, signable_bytes(token), hashlib.sha256).digest()
    signed = dict(token)
    signed["signature"] = {
        "algorithm": "test_hmac_sha256",
        "key_id": KEY_ID,
        "value": "base64:" + base64.b64encode(signature).decode("ascii"),
    }
    return signed


def issue(
    plan: dict[str, Any],
    *,
    clock: Clock,
    mode: str = "policy",
    ttl_minutes: int = 5,
    secret: bytes = TEST_SECRET,
    plan_hash: str | None = None,
    machine_id: str | None = None,
    root_instance_id: str | None = None,
    policy_revision: int | None = None,
    approved_by_sid: str | None = None,
    nonce: str | None = None,
    approval_id: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Issue a schema-valid, correctly signed token bound to ``plan``."""

    from datetime import timedelta

    issued = clock.now()
    token: dict[str, Any] = {
        "schema_version": 1,
        "approval_id": approval_id or f"approval/{uuid.uuid4().hex[:12]}",
        "plan_hash": plan_hash if plan_hash is not None else plan["plan_hash"],
        "root_instance_id": root_instance_id or plan["root_instance_id"],
        "machine_id": machine_id or plan["machine_id"],
        "policy_revision": plan["policy_revision"] if policy_revision is None else policy_revision,
        "approval_mode": mode,
        "issuer": ISSUER,
        "approved_by_sid": approved_by_sid if approved_by_sid is not None else ("S-1-5-21-1000" if mode == "human" else None),
        "issued_at": issued.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at
        or (issued + timedelta(minutes=ttl_minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "nonce": nonce or uuid.uuid4().hex,
    }
    signed = sign(token, secret)
    validate_self("approval-token", signed)
    return signed


def tamper(token: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Change a signed field without re-signing, to prove verification."""

    altered = dict(token)
    altered.update(changes)
    return altered
