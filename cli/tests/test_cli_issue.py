"""`airoot issue` — the explicit local signing step, as a verb (ADR-0049, draft §122).

The verb is a thin wrapper on `tx/issuer.py`, so these tests are about the wrapper's promises, not about
Ed25519: that the three steps line up (`provision → issue → approve`), that the refusals of the module
underneath are **passed through** rather than swallowed, that nothing is signed as a side effect of
anything else, and that the document a caller quotes back says what an approval is worth.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import fake_issuer
import pytest

from airoot.cli import main
from airoot.clock import SYSTEM_CLOCK
from airoot.schema_io import validate_document
from airoot.tx import create_plan
from airoot.tx.issuer import ISSUER_ID, ISSUER_KEY_ID, key_file


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def plan_file(registry, tmp_path: Path) -> Path:
    """Minted against real time: the CLI's own context evaluates expiry against it (see `test_cli.py`)."""

    plan = create_plan(registry, version="1.0.0", clock=SYSTEM_CLOCK)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_provision_then_issue_then_approve_is_the_whole_path(capsys, registry, root, plan_file) -> None:
    """The three steps the ADR names, in order, with the token actually consumed at the end."""

    token_file = Path(root.path) / "state" / "approvals" / "issued.json"

    code, issued = run(
        capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(token_file), "--provision"
    )

    assert code == 0, issued
    assert issued["provisioned"] is True
    assert issued["issuer"] == ISSUER_ID
    assert issued["key_id"] == ISSUER_KEY_ID
    assert token_file.is_file()
    validate_document("approval-token", json.loads(token_file.read_text(encoding="utf-8")))

    code, approved = run(
        capsys, "--json", "--root", str(root.path), "approve", str(plan_file), "--token-file", str(token_file)
    )

    assert code == 0, approved
    assert approved["approval_id"] == issued["approval_id"], "the consumed token is the issued one"


def test_the_document_says_the_approval_is_not_a_permission_proof(capsys, registry, root, plan_file) -> None:
    """§122's honesty requirement: the caller quotes this document back, so the caveat lives in it."""

    code, document = run(
        capsys,
        "--json",
        "--root",
        str(root.path),
        "issue",
        str(plan_file),
        "--out",
        str(Path(root.path) / "token.json"),
        "--provision",
    )

    assert code == 0
    assert document["permission_proof"] is False
    assert "ADR-0046" in document["note"] and "audit record" in document["note"], (
        "a lane that prints a token without saying what it is worth invites the reader to treat it "
        "as authorisation"
    )


def test_provision_refuses_to_overwrite_a_key_and_the_refusal_is_passed_through(
    capsys, registry, root, plan_file
) -> None:
    token_file = Path(root.path) / "token.json"
    first_code, _first = run(
        capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(token_file), "--provision"
    )
    assert first_code == 0
    before = key_file(root.path).read_bytes()

    code, document = run(
        capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(token_file), "--provision"
    )

    assert code == 8, "INVALID_INPUT is the module's own verdict and the verb must not soften it"
    assert document["reason_code"] == "INVALID_INPUT"
    assert key_file(root.path).read_bytes() == before, "nothing may rotate a key behind the operator's back"


def test_issuing_without_a_provisioned_key_names_the_missing_step(capsys, registry, root, plan_file) -> None:
    code, document = run(
        capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(Path(root.path) / "t.json")
    )

    assert code == 7, "PROVENANCE_FAILED"
    assert document["reason_code"] == "PROVENANCE_FAILED"
    assert "no signing key is provisioned" in document["message"], document
    assert "provision" in " ".join(document["evidence"]), (
        "the refusal has to name the step that fixes it, or the reader is left with a dead end"
    )
    assert not key_file(root.path).exists(), "a failed issue must not have created the key it could not find"


def test_human_mode_without_a_sid_is_refused_by_the_module(capsys, registry, root, plan_file) -> None:
    code, document = run(
        capsys,
        "--json",
        "--root",
        str(root.path),
        "issue",
        str(plan_file),
        "--out",
        str(Path(root.path) / "t.json"),
        "--provision",
        "--mode",
        "human",
    )

    assert code == 4, "INVALID_APPROVAL: the signer will not invent a SID"
    assert document["reason_code"] == "INVALID_APPROVAL"


def test_human_mode_records_the_sid_it_was_given(capsys, registry, root, plan_file) -> None:
    token_file = Path(root.path) / "human.json"

    code, document = run(
        capsys,
        "--json",
        "--root",
        str(root.path),
        "issue",
        str(plan_file),
        "--out",
        str(token_file),
        "--provision",
        "--mode",
        "human",
        "--approved-by-sid",
        "S-1-5-21-9-9-9",
    )

    assert code == 0, document
    token = json.loads(token_file.read_text(encoding="utf-8"))
    assert document["approval_mode"] == "human"
    assert token["approval_mode"] == "human"
    assert token["approved_by_sid"] == "S-1-5-21-9-9-9"


def test_ttl_minutes_reaches_the_token(capsys, registry, root, plan_file) -> None:
    token_file = Path(root.path) / "ttl.json"

    code, _document = run(
        capsys,
        "--json",
        "--root",
        str(root.path),
        "issue",
        str(plan_file),
        "--out",
        str(token_file),
        "--provision",
        "--ttl-minutes",
        "7",
    )

    assert code == 0
    token = json.loads(token_file.read_text(encoding="utf-8"))
    issued = datetime.fromisoformat(token["issued_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(token["expires_at"].replace("Z", "+00:00"))
    assert (expires - issued).total_seconds() == pytest.approx(7 * 60, abs=1), "the flag has to reach the token"
    assert expires > issued


def test_a_missing_plan_file_is_invalid_input(capsys, root) -> None:
    code, document = run(
        capsys, "--json", "--root", str(root.path), "issue", str(root.path / "nope.json"), "--out", "t.json"
    )

    assert code == 8
    assert document["reason_code"] == "INVALID_INPUT"


def test_a_plain_issue_rotates_nothing_and_writes_only_the_token(
    capsys, registry, root, plan_file
) -> None:
    """No `--provision` means read the key and write one file: no rotation, no second write."""

    token_file = Path(root.path) / "only-this.json"
    run(capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(token_file), "--provision")
    key_before = key_file(root.path).read_bytes()
    trust_before = {
        path.name: path.read_bytes() for path in sorted((Path(root.path) / "state").glob("*.json"))
    }

    code, document = run(capsys, "--json", "--root", str(root.path), "issue", str(plan_file), "--out", str(token_file))

    assert code == 0
    assert document["provisioned"] is False
    assert key_file(root.path).read_bytes() == key_before
    after = {path.name: path.read_bytes() for path in sorted((Path(root.path) / "state").glob("*.json"))}
    assert after == trust_before, "a run that provisions nothing may not rewrite the root's trust material"


def test_consuming_an_approval_never_provisions_a_signing_key(capsys, registry, root, plan_file) -> None:
    """The core never signs as a side effect — the two consuming verbs still need a token from outside."""

    fake_issuer.install_keyring(root.path)
    token = fake_issuer.issue(json.loads(plan_file.read_text(encoding="utf-8")), clock=SYSTEM_CLOCK)
    token_file = Path(root.path) / "fake-token.json"
    token_file.write_text(json.dumps(token), encoding="utf-8")

    code, document = run(
        capsys, "--json", "--root", str(root.path), "approve", str(plan_file), "--token-file", str(token_file)
    )

    assert code == 0, document
    assert not key_file(root.path).exists(), "approve must not become a signer on its way through"
