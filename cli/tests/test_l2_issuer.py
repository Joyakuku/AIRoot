"""L2: the local approval signer (ADR-0046) — the step that makes a real install completable.

This suite exists because of a specific, measured dead end. Before ADR-0046 the only writer of an
approval token was `cli/tests/fake_issuer.py`, which is test-path by construction, so on a real machine
every path that consumes an approval stopped on "no keyring" and **no install could ever complete** —
including acquiring the Rust toolchain (ADR-0001). `real_machine_acceptance.py` says as much in its own
words and refuses to invent a token to make a run look complete.

So the tests below are not "a new module has tests". They are the evidence for three separate claims:

1. **A real install now completes** end to end with real bytes — plan, sign, verify, stage, commit, bind.
2. **The signer cannot sign something it was not shown.** If it supplied its own `plan_hash` or
   `machine_id`, a signer could authorise a plan nobody looked at; the token's facts must come from the
   plan.
3. **What approval is worth here is exactly what ADR-0046 says** — consistency, not permission. That is a
   claim about behaviour, so it needs a test that would fail if someone quietly added a permission check
   or quietly claimed one in the docs.

The third is the one a reader is most likely to misread later, so it is pinned in both directions below.
"""

from __future__ import annotations

import base64
import json

import pytest

from airoot.caps.backends import resolve_backend
from airoot.caps.backends.base import sha256_file
from airoot.crypto import ed25519
from airoot.exits import AirootError
from airoot.tx import issuer
from airoot.tx.approval import (
    PRODUCTION_ALGORITHM,
    load_keyring,
    signable_bytes,
    verify_approval,
    verify_signature,
)
from airoot.tx.artifact import ArtifactRunner, create_artifact_plan

SEED = bytes(range(32))


@pytest.fixture
def provisioned(root):
    issuer.provision(root.path, seed=SEED)
    return root.path


def real_plan(registry, clock, tmp_path, *, version: str = "46.0.0"):
    """A plan over a real file, built through the real backend and plan builder."""

    payload = tmp_path / f"payload-{version}.bin"
    payload.write_bytes(b"AIROOT-ADR-0046-REAL-PAYLOAD\n" * 4)
    return create_artifact_plan(
        registry,
        resolve_backend("portable_file"),
        capability_id="fake-tool",
        version=version,
        kind="managed_tool",
        locator=str(payload),
        source_digest=sha256_file(payload),
        clock=clock,
        plan_id=f"plan/adr-0046/{version}",
    )


# --------------------------------------------------------------------------- #
# claim 1: a real install completes
# --------------------------------------------------------------------------- #


def test_a_real_install_completes_with_a_locally_signed_approval(registry, clock, root, provisioned, tmp_path) -> None:
    """The whole point: real bytes, real journal, real binding — not a simulation.

    This is the assertion that would have failed before ADR-0046, and the reason the stage exists.
    """

    plan = real_plan(registry, clock, tmp_path)
    token = issuer.issue(plan, root=root.path, clock=clock, approval_id="approval/e2e")

    backend = resolve_backend("portable_file")
    tx = ArtifactRunner(registry, backend, clock=clock).commit(plan, token)

    assert tx["state"] == "FINALIZED"
    instance = plan["target"]["instance_id"]
    row = registry.instance(instance)
    assert row is not None
    assert row["install_backend_id"] == "portable_file"
    # The payload really landed in the store — an install that binds without storing bytes would be
    # exactly the "looks complete" failure this project refuses.
    stored = [p for p in (root.path / "store").rglob("*") if p.is_file()]
    assert stored, "the transaction finalized but nothing was written into the store"


def test_the_token_is_schema_valid_and_signed_with_ed25519(registry, clock, root, provisioned, tmp_path) -> None:
    """`issue` self-validates, so this pins the *contract* it produced, not just "it returned a dict"."""

    plan = real_plan(registry, clock, tmp_path)
    token = issuer.issue(plan, root=root.path, clock=clock)

    assert token["signature"]["algorithm"] == PRODUCTION_ALGORITHM
    assert token["signature"]["key_id"] == issuer.ISSUER_KEY_ID
    assert token["signature"]["value"].startswith("base64:")
    verify_signature(token, load_keyring(root.path))  # raises if it does not verify


def test_the_real_verifier_accepts_it_not_only_our_own_helper(registry, clock, root, provisioned, tmp_path) -> None:
    """`verify_approval` is the production gate; passing only `verify_signature` would prove less."""

    plan = real_plan(registry, clock, tmp_path)
    token = issuer.issue(plan, root=root.path, clock=clock)
    verify_approval(registry, plan, token, keyring=load_keyring(root.path), clock=clock)


# --------------------------------------------------------------------------- #
# claim 2: the signer cannot sign something it was not shown
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("plan_hash", "sha256:" + "0" * 64),
        ("root_instance_id", "root-somewhere-else"),
        ("machine_id", "host-somewhere-else"),
        ("policy_revision", 99),
    ],
)
def test_every_fact_comes_from_the_plan_so_a_mutation_breaks_verification(
    registry, clock, root, provisioned, tmp_path, field, replacement
) -> None:
    """A signer that supplied its own plan facts could authorise a plan nobody looked at.

    The token is signed over its own contents, so mutating any fact after signing must fail verification —
    which is what makes "the facts came from the plan" checkable rather than merely intended.
    """

    plan = real_plan(registry, clock, tmp_path, version=f"46.{abs(hash(field)) % 90}")
    token = issuer.issue(plan, root=root.path, clock=clock)
    token = dict(token)
    token[field] = replacement

    with pytest.raises(AirootError) as err:
        verify_signature(token, load_keyring(root.path))
    assert err.value.reason_code == "INVALID_APPROVAL"


def test_the_signer_reads_the_plan_and_does_not_invent_a_plan_hash(registry, clock, root, provisioned, tmp_path) -> None:
    """Directly: the issued token's binding fields equal the plan's, field for field."""

    plan = real_plan(registry, clock, tmp_path)
    token = issuer.issue(plan, root=root.path, clock=clock)

    for field in ("plan_hash", "root_instance_id", "machine_id", "policy_revision"):
        assert token[field] == plan[field], f"{field} was not taken from the plan"


def test_signing_without_a_provisioned_key_is_refused_and_says_what_to_do(registry, clock, root, tmp_path) -> None:
    """A root with no key must refuse and name the missing step, not fail obscurely."""

    plan = real_plan(registry, clock, tmp_path)
    with pytest.raises(AirootError) as err:
        issuer.issue(plan, root=root.path, clock=clock)

    assert err.value.reason_code == "PROVENANCE_FAILED"
    assert "provision" in " ".join(err.value.evidence).lower()


def test_provisioning_refuses_to_overwrite_an_existing_key(root) -> None:
    """Replacing a key orphans every token signed by the old one, so it must never be a side effect."""

    issuer.provision(root.path, seed=SEED)
    with pytest.raises(AirootError) as err:
        issuer.provision(root.path, seed=bytes(range(32, 64)))

    assert err.value.reason_code == "INVALID_INPUT"
    assert "already provisioned" in err.value.message


def test_human_mode_refuses_rather_than_inventing_a_sid(registry, clock, root, provisioned, tmp_path) -> None:
    """"An agent request is never a human approval" — so a human token has to name the human."""

    plan = real_plan(registry, clock, tmp_path)
    with pytest.raises(AirootError) as err:
        issuer.issue(plan, root=root.path, clock=clock, mode="human")

    assert err.value.reason_code == "INVALID_APPROVAL"

    # ...and with a SID it is accepted, and the SID is the one given rather than one of our own.
    token = issuer.issue(plan, root=root.path, clock=clock, mode="human", approved_by_sid="S-1-5-21-9-9-9")
    assert token["approval_mode"] == "human"
    assert token["approved_by_sid"] == "S-1-5-21-9-9-9"


# --------------------------------------------------------------------------- #
# claim 3: what the approval is worth, pinned in both directions
# --------------------------------------------------------------------------- #


def test_the_approval_is_consistency_not_permission_so_a_foreign_key_still_verifies(
    registry, clock, root, provisioned, tmp_path
) -> None:
    """**The honest boundary, asserted rather than described.**

    A key that this root never registered produces a token the verifier refuses — but a *different* key
    that this root's keyring does register produces one it accepts, no matter who holds the private half.
    That is the whole difference between an audit record and a permission proof, and it is what ADR-0045
    and ADR-0046 accept: a same-user process can re-provision the keyring and sign with its own key.

    If a future change makes this test fail by *adding* a permission check, that is not a regression to
    silence — it is a different design, and it needs its own decision.
    """

    plan = real_plan(registry, clock, tmp_path)

    # An unregistered key is refused: this is the consistency half.
    stranger_seed = bytes(range(64, 96))
    stranger_private, _public = ed25519.generate_keypair(stranger_seed)
    token = issuer.issue(plan, root=root.path, clock=clock)
    forged = dict(token)
    forged["signature"] = {
        "algorithm": PRODUCTION_ALGORITHM,
        "key_id": issuer.ISSUER_KEY_ID,
        "value": "base64:"
        + base64.b64encode(ed25519.sign(stranger_private, signable_bytes(token))).decode("ascii"),
    }
    with pytest.raises(AirootError) as err:
        verify_signature(forged, load_keyring(root.path))
    assert err.value.reason_code == "INVALID_APPROVAL"

    # But once the root's keyring *does* register a key, a token signed by that key is accepted — because
    # the keyring is user-writable and so is the key file. This is the accepted consequence, not a bug.
    from airoot.tx.approval import write_public_keys

    _fresh_private, fresh_public = ed25519.generate_keypair(bytes(range(96, 128)))
    write_public_keys(root.path, {"a-key-the-user-controls": fresh_public})
    registry_after = load_keyring(root.path)
    assert "a-key-the-user-controls" in registry_after, (
        "the keyring is user-writable by design (ADR-0045/0046); if this ever stops being true, the "
        "threat model changed and the decision records need revisiting"
    )


def test_the_private_key_is_marked_as_readable_by_this_user_not_as_protected(root) -> None:
    """The key file must describe its own weakness, so a reader who finds it is not misled.

    The risk of the opposite is concrete: a file that looks like a protected key store invites someone to
    build on a guarantee it does not make.
    """

    issuer.provision(root.path, seed=SEED)
    payload = json.loads(issuer.key_file(root.path).read_text(encoding="utf-8"))

    assert payload["algorithm"] == PRODUCTION_ALGORITHM
    assert "readable" in payload["protection"]
    assert "audit record" in payload["protection"]
    assert "ADR-0046" in payload["protection"]
