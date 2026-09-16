"""The local approval signer: one explicit human step, outside the core (ADR-0046).

**What this module is for.** Since ADR-0044 there is no protected issuer, and since ADR-0045 security
belongs to whoever consumes the skill rather than to AIROOT. Without a signer, every path that consumes
an approval stops on "no keyring", so `install` can never complete on a real machine — which is what
blocks acquiring the Rust toolchain (ADR-0001). This module is the missing step: it mints the token that
`airoot approve` records and `install --token-file` consumes.

**What "approval" means here, stated first because it is the thing to get wrong.** It is an **audit
record, not a proof of permission** (ADR-0046). The private key lives in the root, a same-user process can
read it, and such a process could equally well generate its own key and re-provision the keyring. So a
token that verifies tells a reader *"this plan was signed by the key this root trusts, and it has not been
altered or replayed since"* — consistency and traceability. It does **not** tell them the caller was
authorised. AIROOT's answer to "is this allowed?" is the steward's own record of what it did, plus whatever
the upstream harness decided before calling in.

**Why the signer is not a CLI verb and not in the core.** The invariant is that the core never has a signing
side — `airoot approve` only *consumes* an approval — because a CLI that can mint consent has stopped being
a record and started being an authority. Keeping the signer a separate module that a human invokes is how
that invariant stays visible in the code's shape: signing is something done **to** AIROOT from outside, not
something AIROOT does.

**What it deliberately does not do.** It does not choose the policy revision, the machine id or the root
instance id — those are facts, so they are read from the plan and the root. It does not decide whether the
plan is a good idea. It does not hide the key: the private half sits beside the public half in the root and
is documented as readable by the same user, because pretending otherwise would be the false protection
ADR-0025 D1 and ADR-0044 both refused.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..canon import signable_payload
from ..clock import Clock, SYSTEM_CLOCK
from ..crypto import ed25519
from ..exits import AirootError
from ..schema_io import validate_self
from .approval import PRODUCTION_ALGORITHM, write_public_keys

#: Where the private half lives. In the root beside the keyring, because that is where the thing it signs
#: lives too — and because ADR-0044 measured that no stronger home exists on this platform (CNG has no
#: Ed25519; the software provider's container is readable by the owning token). Stated where a reader
#: looking for the key will be, rather than only in a decision record.
KEY_FILE_RELATIVE = "state/issuer-key.json"

#: The key id a provisioned key is registered under. Fixed rather than generated: a token names its key,
#: and one root has one signer, so a second id would only invite the question of which is authoritative.
ISSUER_KEY_ID = "airoot-local-issuer-1"

ISSUER_ID = "airoot-local-issuer"


def key_file(root: Path) -> Path:
    return Path(root) / KEY_FILE_RELATIVE


def _b64(value: bytes) -> str:
    return "base64:" + base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value[len("base64:") :])


def provision(root: Path, *, seed: bytes | None = None) -> dict[str, Any]:
    """Create the root's signing key and register its public half in the keyring.

    ``seed`` exists for tests and for a deterministic fixture; a real caller passes nothing and gets an
    unpredictable key from the OS.

    Refuses to overwrite an existing key. Provisioning twice would silently orphan every token signed by
    the first key, and "the key changed and nobody noticed" is exactly the failure a trust anchor must not
    have — so replacing a key is a deliberate act (delete the file, provision again), never a side effect.
    """

    path = key_file(root)
    if path.is_file():
        raise AirootError(
            "INVALID_INPUT",
            f"a signing key is already provisioned: {path}",
            evidence=[
                "refusing to overwrite it: every token signed by the existing key would stop verifying",
                "to replace it deliberately, delete that file and provision again",
            ],
        )

    if seed is None:
        # `generate_keypair` takes a 32-byte seed and derives the public half, so an unpredictable key
        # starts from an unpredictable seed. The key file stores the seed, which is the private key.
        seed = os.urandom(32)
    private, public = ed25519.generate_keypair(seed)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "key_id": ISSUER_KEY_ID,
                "algorithm": PRODUCTION_ALGORITHM,
                # The private half is `seed`-shaped (32 bytes), which is what `ed25519.sign` takes.
                "private_key": _b64(private),
                # Stated in the file itself, so a reader who finds the key is told what it is worth
                # without having to find a decision record first.
                "protection": (
                    "readable by any process running as this user; this is a known and accepted "
                    "consequence (ADR-0044 measured that no stronger home exists here, ADR-0045 puts "
                    "security with the consumer of this skill, ADR-0046 accepts it). A token signed "
                    "with this key is an audit record, not a proof of permission."
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_public_keys(root, {ISSUER_KEY_ID: public})
    return {"key_id": ISSUER_KEY_ID, "key_file": str(path), "public_key": _b64(public)}


def load_private_key(root: Path) -> bytes:
    path = key_file(root)
    if not path.is_file():
        raise AirootError(
            "PROVENANCE_FAILED",
            f"no signing key is provisioned in this root: {path}",
            evidence=[
                "run the provisioning step first (see this module's `provision`)",
                "the public half is registered separately, so a key file without a keyring entry cannot sign anything a verifier accepts",
            ],
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _unb64(str(payload["private_key"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AirootError(
            "PROVENANCE_FAILED", f"the signing key is unreadable: {path}", evidence=[str(exc)]
        ) from exc


def _isoformat(moment: Any) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def issue(
    plan: dict[str, Any],
    *,
    root: Path,
    clock: Clock = SYSTEM_CLOCK,
    mode: str = "policy",
    ttl_minutes: int = 5,
    approved_by_sid: str | None = None,
    approval_id: str | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Mint a schema-valid `approval-token` bound to ``plan`` and signed by this root's key.

    The token's facts are **read from the plan and the root**, never invented here: ``plan_hash``,
    ``root_instance_id``, ``machine_id`` and ``policy_revision`` all belong to the plan, and a signer that
    supplied its own would be able to authorise a plan it was not shown. `approval_id` and `nonce` are
    generated (or injected, for a reproducible fixture).

    A `human` mode token must carry `approved_by_sid` (the schema and `tx/approval.py` both require it), and
    this function refuses rather than inventing one — "an agent request is never a human approval".
    """

    if mode not in {"human", "policy"}:
        raise AirootError(
            "INVALID_INPUT",
            f"unknown approval mode: {mode!r}",
            evidence=["the published schema enumerates human|policy"],
        )
    if mode == "human" and not approved_by_sid:
        raise AirootError(
            "INVALID_APPROVAL",
            "human approval must record approved_by_sid",
            evidence=[
                "a human approval has to name the human; this signer will not invent a SID",
                "either pass approved_by_sid or sign as approval_mode=policy",
            ],
        )

    issued = clock.now()
    token: dict[str, Any] = {
        "schema_version": 1,
        "approval_id": approval_id or f"approval/{uuid.uuid4().hex[:12]}",
        "plan_hash": plan["plan_hash"],
        "root_instance_id": plan["root_instance_id"],
        "machine_id": plan["machine_id"],
        "policy_revision": plan["policy_revision"],
        "approval_mode": mode,
        "issuer": ISSUER_ID,
        "approved_by_sid": approved_by_sid if mode == "human" else None,
        "issued_at": _isoformat(issued),
        "expires_at": _isoformat(issued + timedelta(minutes=ttl_minutes)),
        "nonce": nonce or uuid.uuid4().hex,
    }
    signature = ed25519.sign(load_private_key(root), signable_payload(token))
    token["signature"] = {
        "algorithm": PRODUCTION_ALGORITHM,
        "key_id": ISSUER_KEY_ID,
        "value": _b64(signature),
    }
    validate_self("approval-token", token)
    return token


def write_token(root: Path, token: dict[str, Any], destination: Path) -> Path:
    """Write a token where `install --token-file` expects to find it."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(token, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
