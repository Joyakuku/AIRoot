"""§113: real Ed25519 approval verification, and the algorithm binding that comes with it.

The core still cannot **mint** an approval — that is the protected issuer's job and no such issuer
exists (ADR-0025's D1). What these tests prove is the other half: a token signed with a real Ed25519 key
verifies, every mutation of it does not, and a token cannot name an algorithm its registered key was
not registered for.

Signing here uses `airoot.crypto.ed25519` (the same module the verifier calls), which is why the
acceptance of *that* module is the RFC 8032 vectors in `test_l1_ed25519.py` rather than a round trip
against itself.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import fake_issuer
from airoot.crypto import ed25519
from airoot.clock import FakeClock
from airoot.exits import AirootError
from airoot.registry import Registry
from airoot.tx import create_plan
from airoot.tx.approval import (
    ISSUER_PENDING,
    PRODUCTION_ALGORITHM,
    TEST_ALGORITHM,
    keyring_path,
    load_keyring,
    signable_bytes,
    verify_approval,
    verify_signature,
    write_public_keys,
    write_test_keyring,
)
from airoot.tx.simulate import SimulationRunner

SEED = bytes(range(32))
KEY_ID = "prod-approver-1"


def signed_token(plan, clock, *, key_id: str = KEY_ID, seed: bytes = SEED, **changes):
    """A schema-valid token signed with a real Ed25519 key over its own contents."""

    token = fake_issuer.issue(plan, clock=clock)
    private, _public = ed25519.generate_keypair(seed)
    signature = ed25519.sign(private, signable_bytes(token))
    token = dict(token)
    token["signature"] = {
        "algorithm": PRODUCTION_ALGORITHM,
        "key_id": key_id,
        "value": "base64:" + base64.b64encode(signature).decode("ascii"),
    }
    token.update(changes)
    return token


@pytest.fixture
def production_keyring(root) -> Path:
    write_public_keys(root.path, {KEY_ID: ed25519.public_key_from_private(ed25519.generate_keypair(SEED)[0])})
    return root.path


def test_a_real_ed25519_token_verifies_and_commits(registry: Registry, clock, root, production_keyring) -> None:
    """The whole point of §113: a token signed by a real key is accepted by the real verifier."""

    plan = create_plan(registry, version="113.0.0", clock=clock)
    token = signed_token(plan, clock)

    verify_signature(token, load_keyring(root.path))
    verify_approval(registry, plan, token, keyring=load_keyring(root.path), clock=clock)

    committed = SimulationRunner(registry, clock=clock).commit(plan, token)
    assert committed["state"] == "FINALIZED"


@pytest.mark.parametrize(
    "change",
    [
        {"plan_hash": "sha256:" + "0" * 64},
        {"nonce": "f" * 32},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"machine_id": "host-ffffffffffffffffffffffffffffffff"},
        {"approval_id": "approval/other"},
    ],
)
def test_every_mutation_after_signing_is_refused(registry: Registry, clock, root, production_keyring, change) -> None:
    """A signature covers the token, so changing any signed field must invalidate it."""

    plan = create_plan(registry, version="113.1.0", clock=clock)
    token = signed_token(plan, clock, **change)

    with pytest.raises(AirootError) as err:
        verify_signature(token, load_keyring(root.path))
    assert err.value.reason_code == "INVALID_APPROVAL"
    assert "does not verify" in err.value.message


def test_a_token_may_not_claim_an_algorithm_its_key_was_not_registered_for(registry, clock, root) -> None:
    """The reason keyring entries are records: the token names the algorithm, the key decides.

    Without this check the *token* chooses which verification runs, so the same registered bytes are
    reinterpreted as whatever the token asks for. Both directions are tested because both are wrong.
    """

    plan = create_plan(registry, version="113.2.0", clock=clock)
    private, public = ed25519.generate_keypair(SEED)

    # An ed25519 token whose key id is registered for the HMAC test algorithm. The *same* key id has
    # to be registered, or the lookup fails first and the branch under test never runs.
    write_test_keyring(root.path, {KEY_ID: b"registered-for-the-other-algorithm"})
    ed_token = signed_token(plan, clock)
    with pytest.raises(AirootError) as err:
        verify_signature(ed_token, load_keyring(root.path))
    assert err.value.reason_code == "INVALID_APPROVAL"
    assert "different algorithm" in err.value.message

    # ...and the other direction: an HMAC token whose key id holds an ed25519 public key.
    write_public_keys(root.path, {fake_issuer.KEY_ID: public})
    hmac_token = fake_issuer.issue(plan, clock=clock)
    with pytest.raises(AirootError) as err:
        verify_signature(hmac_token, load_keyring(root.path))
    assert err.value.reason_code == "INVALID_APPROVAL"
    assert "different algorithm" in err.value.message
    assert private  # the keypair is used; silence the unused-name lint without a noqa


def test_the_old_bare_material_keyring_is_refused_rather_than_reinterpreted(root) -> None:
    """A file written in the pre-§113 shape must be an error, not a guess about its algorithm."""

    path = keyring_path(root.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"keys": {KEY_ID: base64.b64encode(b"x" * 32).decode("ascii")}}, indent=2),
        encoding="utf-8",
    )
    with pytest.raises(AirootError) as err:
        load_keyring(root.path)
    assert err.value.reason_code == "PROVENANCE_FAILED"
    assert "must be a record" in " ".join(err.value.evidence) or "must be a record" in err.value.message


def test_installing_a_public_key_does_not_clobber_the_test_key(root) -> None:
    """A production public key and a test secret coexist; each is used only for its own algorithm.

    The keyring is one file, and the two writers (``write_test_keyring`` for the simulation,
    ``write_public_keys`` for a production key) must merge rather than replace: a root that has both
    has to verify both kinds of token, and a writer that clobbered the other half would show up as
    "suddenly every test token is an unknown key".
    """

    fake_issuer.install_keyring(root.path)
    write_public_keys(root.path, {KEY_ID: ed25519.public_key_from_private(ed25519.generate_keypair(SEED)[0])})

    records = load_keyring(root.path)
    assert records[fake_issuer.KEY_ID].algorithm == TEST_ALGORITHM
    assert records[KEY_ID].algorithm == PRODUCTION_ALGORITHM


def test_a_missing_keyring_is_still_the_one_refusal_that_names_the_decision(root) -> None:
    with pytest.raises(AirootError) as err:
        load_keyring(root.path)
    assert err.value.reason_code == "PROVENANCE_FAILED"
    assert ISSUER_PENDING in err.value.message
    assert "never mints" in " ".join(err.value.evidence)
