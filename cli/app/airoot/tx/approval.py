"""Approval token verification — both algorithms, one keyring, one refusal left.

``airoot approve`` only *consumes* approvals; it never mints them (v0.3 §8.5 and the broker plan §5).
What that leaves here is verification, and since §113 **both** algorithms are real:

* ``test_hmac_sha256`` — the simulation issuer, ``docs/schema/README.md`` permits it only for the fake
  slice, which the simulation extends;
* ``ed25519`` — **RFC 8032** (``airoot.crypto.ed25519``, pure Python, no new dependency). A token signed
  by a key whose public half is registered is accepted; one that is not is refused as a bad token.

**Verification being real is not the same as this build being able to approve anything.** The signing
side still does not exist in the core — deliberately, so the CLI can never manufacture consent — and no
protected issuer exists either (ADR-0025's D1). So every path that consumes an approval (``approve``,
``install``, ``env persist``, ``tool gc --apply``, ``uninstall``) still stops, and what it stops on is
now exactly one thing: **a root with no keyring at all**, refused with :data:`ISSUER_PENDING` so the
reader is told it is a boundary waiting for the P2 broker rather than a bug.

**The verification key must come from the boundary, not from the document.** RFC 8032 does not
reject small-order public keys, so with a degenerate key `(R = [S]B, S)` verifies for *any*
message and no private key — pinned by a test in `test_l1_ed25519.py`. This module is therefore
only as trustworthy as its keyring, and today that keyring is a file in the root that any process
which can write the root can replace. **That is the gap the protected stage closes**, not
something a check here can fix; do not move this keyring's location into a token or a caller
argument.

The keyring is a map of **records**, not of bare key bytes: each entry says which algorithm its material
is for (``{"algorithm": "ed25519", "public_key": "base64:…"}``). That closes a hole the flat form had —
the token's own ``signature.algorithm`` field decided which verification ran, so a token could name an
algorithm the registered key was never registered for. Now the two must agree or the token is refused
(draft §113, ADR-0039).

**The keyring was renamed from ``state/test-keyring.json`` to ``state/keyring.json`` in ADR-0046.** The
old name was kept honest by the fact that its only *writer* was the test issuer, and ADR-0039 decision 4
said the rename belonged to the stage that gave it a production writer. That stage is ADR-0046: the
private half now lives in the root beside it and the local signer writes both. The old name is still
**read** (new name wins) so roots built by earlier runs keep working; migration here means "the old path
is still legible", not "the old file is deleted".

**What this keyring's integrity is worth, stated plainly** (ADR-0045/ADR-0046): it sits in the root, so a
same-user process can replace it — and with a local signer, such a process could sign with its own key
anyway. Verification therefore enforces **consistency** (this token is bound to this plan, not replayed,
not expired) and **not authorisation**. Approval is an audit record, not a proof of permission.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..canon import signable_payload
from ..crypto import ed25519
from ..clock import Clock, SYSTEM_CLOCK, parse_timestamp
from ..exits import AirootError
from ..schema_io import validate_document

KEYRING_RELATIVE = "state/keyring.json"

#: The name this file had before ADR-0046, still **read** so roots built by earlier runs keep working.
#: Not written any more: a writer that keeps both names alive would make "which one is authoritative"
#: a question with two answers.
LEGACY_KEYRING_RELATIVE = "state/test-keyring.json"
TEST_ALGORITHM = "test_hmac_sha256"
PRODUCTION_ALGORITHM = "ed25519"

#: Which field of a keyring record carries the material for each algorithm. A record says what its
#: material is *for*, so "the key is registered for another algorithm" is a refusal rather than a
#: silent reinterpretation of the same bytes.
ALGORITHM_FIELDS = {TEST_ALGORITHM: "secret", PRODUCTION_ALGORITHM: "public_key"}


@dataclass(frozen=True)
class ApprovalKey:
    """One registered key: which algorithm it signs for, and the material to verify with.

    For ``test_hmac_sha256`` the material is the shared secret; for ``ed25519`` it is the **public**
    key, which is why a production keyring is not itself a secret — the private half must live behind
    the protected boundary and never in the root (§113.3).
    """

    algorithm: str
    material: bytes

#: The one sentence every refusal caused by a missing keyring must contain. Five commands reach it.
#:
#: **Rewritten in ADR-0046.** Until then it read "no production approval issuer exists in this build
#: (decided: ADR-0025 keeps it waiting for the P2 broker)" — and that sentence became false the moment a
#: local signer existed: the build *does* have an issuer, and what is missing is that **this root** has no
#: key provisioned in it. A refusal that keeps blaming the build would send a reader to wait for a stage
#: that no longer unblocks anything, instead of running the one provisioning step that does. The pointer is
#: to ADR-0046 because that is now the decision a reader needs; `test_l1_transaction.py` holds this
#: sentence to the decision log so it cannot name an entry that does not exist.
ISSUER_PENDING = (
    "no approval keyring is installed in this root; provision a signing key first "
    "(decided: ADR-0046 — approval is an audit record, so the signer is a local, explicit step)"
)


def keyring_path(root: Path) -> Path:
    """The keyring this root uses: the current name if present, else the pre-ADR-0046 name.

    The fallback is read-only in effect — `_write_records` always writes `KEYRING_RELATIVE` — so a root
    still carrying the old name is upgraded the next time anything writes a record into it. That is what
    "migration" means here: the old path stays legible, it is not moved or deleted.
    """

    current = Path(root) / KEYRING_RELATIVE
    if current.is_file():
        return current
    legacy = Path(root) / LEGACY_KEYRING_RELATIVE
    if legacy.is_file():
        return legacy
    return current


def _b64(value: bytes) -> str:
    return "base64:" + base64.b64encode(value).decode("ascii")


def _write_records(root: Path, records: Mapping[str, ApprovalKey]) -> None:
    """Merge records into the keyring file, replacing only the ids given."""

    path = keyring_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = dict(json.loads(path.read_text(encoding="utf-8")).get("keys", {}))
        except (OSError, ValueError):
            existing = {}
    for key_id, record in records.items():
        field = ALGORITHM_FIELDS[record.algorithm]
        existing[key_id] = {"algorithm": record.algorithm, field: _b64(record.material)}
    path.write_text(
        json.dumps({"keys": existing}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_test_keyring(root: Path, keys: Mapping[str, bytes]) -> None:
    """Install test-issuer secrets (``test_hmac_sha256``) as keyring records."""

    _write_records(root, {key_id: ApprovalKey(TEST_ALGORITHM, secret) for key_id, secret in keys.items()})


def write_public_keys(root: Path, keys: Mapping[str, bytes]) -> None:
    """Install ``ed25519`` **public** keys — what a production keyring will hold.

    A public key is not a secret, so this file may sit in the root; the private half must not, which is
    why no function here signs anything.
    """

    _write_records(root, {key_id: ApprovalKey(PRODUCTION_ALGORITHM, key) for key_id, key in keys.items()})


def load_keyring(root: Path) -> dict[str, ApprovalKey]:
    path = keyring_path(root)
    if not path.is_file():
        raise AirootError(
            "PROVENANCE_FAILED",
            ISSUER_PENDING,
            evidence=[
                str(path),
                "the core never mints a token on its own: `airoot approve` only consumes one, so the "
                "signer is a separate, explicit local step (`airoot.tx.issuer`)",
                "provision this root's key, then hand the token to approve/install/env persist/"
                "tool gc --apply/uninstall — the missing thing is this root's key, not the build's issuer",
                "an approval is an audit record, not a proof of permission (decided: ADR-0046)",
            ],
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records: dict[str, ApprovalKey] = {}
        for key_id, record in dict(payload.get("keys", {})).items():
            if not isinstance(record, dict):
                raise ValueError(f"{key_id}: a keyring entry must be a record, not bare material")
            algorithm = str(record.get("algorithm", ""))
            if algorithm not in ALGORITHM_FIELDS:
                raise ValueError(f"{key_id}: unknown algorithm {algorithm!r}")
            value = str(record.get(ALGORITHM_FIELDS[algorithm], ""))
            if not value.startswith("base64:"):
                raise ValueError(f"{key_id}: {ALGORITHM_FIELDS[algorithm]} must be base64-prefixed")
            records[str(key_id)] = ApprovalKey(
                algorithm, base64.b64decode(value[len("base64:") :])
            )
        return records
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AirootError("PROVENANCE_FAILED", f"approval keyring is unreadable: {path}", evidence=[str(exc)]) from exc


def signable_bytes(token: dict[str, Any]) -> bytes:
    return signable_payload(token, omit=("signature", "consumed_at"))


def verify_signature(token: dict[str, Any], keyring: Mapping[str, ApprovalKey]) -> None:
    """Verify the token's signature, refusing the algorithm/key combinations that must not verify.

    Three refusals, each for a different reason, and the middle one is why the keyring holds records:

    1. an algorithm this build does not implement — refused by name;
    2. a key registered for a *different* algorithm — the token names the algorithm, so without this
       check the same registered bytes would be reinterpreted as whatever the token asked for;
    3. a signature that simply does not verify against the registered material.
    """

    signature = token.get("signature") or {}
    algorithm = signature.get("algorithm")
    key_id = str(signature.get("key_id", ""))
    value = str(signature.get("value", ""))

    if algorithm not in ALGORITHM_FIELDS:
        raise AirootError(
            "INVALID_APPROVAL",
            f"unsupported signature algorithm: {algorithm}",
            evidence=[f"algorithm={algorithm}", f"supported={sorted(ALGORITHM_FIELDS)}"],
        )
    if key_id not in keyring:
        # Key *ids* are not secrets (a token carries one), and "which keys are registered" is the one
        # question a reader of this refusal has: a keyring that is not the one they think it is looks
        # exactly like a bad token otherwise.
        raise AirootError(
            "INVALID_APPROVAL",
            f"unknown signing key: {key_id}",
            evidence=[
                f"key_id={key_id}",
                f"registered key ids: {', '.join(sorted(keyring)) or 'none'}",
            ],
        )
    registered = keyring[key_id]
    if registered.algorithm != algorithm:
        raise AirootError(
            "INVALID_APPROVAL",
            "the signing key is registered for a different algorithm",
            evidence=[
                f"token={algorithm}",
                f"keyring={registered.algorithm}",
                f"key_id={key_id}",
            ],
        )
    if not value.startswith("base64:"):
        raise AirootError("INVALID_APPROVAL", "signature value must be base64-prefixed")
    try:
        provided = base64.b64decode(value[len("base64:") :])
    except (ValueError, TypeError) as exc:
        raise AirootError("INVALID_APPROVAL", "signature value is not valid base64", evidence=[str(exc)]) from exc

    message = signable_bytes(token)
    if algorithm == PRODUCTION_ALGORITHM:
        if not ed25519.verify(registered.material, provided, message):
            raise AirootError(
                "INVALID_APPROVAL",
                "approval signature does not verify against the registered public key",
                evidence=[f"key_id={key_id}", f"algorithm={algorithm}"],
            )
        return

    expected = hmac.new(registered.material, message, hashlib.sha256).digest()
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
