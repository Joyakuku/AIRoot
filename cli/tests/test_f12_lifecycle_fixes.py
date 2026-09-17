"""L1/L2: the four F12 defects and their corrections (draft §164).

This file holds **only** guards for §164. Every one of them was watched fail against the line it
protects before it was kept; the report that accompanies this change names that line and the
measured reading on both sides.

The defects were one shape in four places — a value allowed to describe something that was no
longer true:

* a rollback picked its predecessor with a bare ``MAX(generation)`` query, so it could re-activate a
  binding whose payload an approved ``gc --apply`` had already deleted;
* a rollback rewrote the instance row of a payload that was live and healthy, because the failure
  was classified as "after the commit point" while nothing had actually been committed;
* ``collected_at`` was a one-way latch, so re-installing the same version brought the bytes back
  while ``tool verify`` went on saying "there is nothing left to verify";
* ``run --capability`` failures printed prose on stderr under ``--json`` instead of an envelope.

They share one file because they are one stage, and because the corrections interact: the
store-path refusal has to be decided *before* the payload moves for the rollback never to meet the
row it used to rewrite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import fake_issuer
from airoot.caps.backends import resolve_backend, sha256_file
from airoot.caps.lifecycle import retire
from airoot.caps.runtime import active_instance_for_capability, resolve_run_target
from airoot.caps.toolstate import tool_status, tool_verify
from airoot.cli import main
from airoot.exits import AirootError, exit_code_for
from airoot.registry import Registry
from airoot.tx.artifact import ArtifactRunner, create_artifact_plan
from airoot.tx.simulate import PAYLOAD_NAME, SimulationRunner, create_plan

CAPABILITY = "fake-tool"


class Tamper:
    """Mutate the payload at a named state, so post-bind verification fails for a real reason."""

    def __init__(self, *, at_state: str, store_dir: Path) -> None:
        self.at_state = at_state
        self.store_dir = store_dir
        self.fired = False

    def checkpoint(self, state: str) -> None:
        if state == self.at_state and not self.fired:
            self.fired = True
            (self.store_dir / PAYLOAD_NAME).write_bytes(b"tampered after ACTIVE_BOUND\n")


def _simulate(registry, clock, root, version: str, *, injector=None):
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock, ttl_minutes=600)
    token = fake_issuer.issue(plan, clock=clock, ttl_minutes=600)
    return SimulationRunner(registry, clock=clock, injector=injector).commit(plan, token), plan


def _evidence(tx: dict) -> str:
    return " ".join(str(item.get("detail", item)) for item in (tx["failure"] or {}).get("evidence", []))


# --------------------------------------------------------------------------- #
# A. a rollback must not revive a binding that no longer stands
# --------------------------------------------------------------------------- #


def test_a_rollback_without_a_live_predecessor_leaves_the_key_unbound(registry, clock, root) -> None:
    """§164/A: the predecessor query had no predicate on retire, collection or the payload.

    Measured before the fix (F12, pass 6): after ``retire 9.9.10`` -> ``gc --apply`` (the store
    directory really deleted) -> a re-install -> a failing re-install, the rollback restored the
    binding to generation 2 — the retired, collected generation whose payload was gone. Every later
    query about the capability then answered out of a broken registry object: ``where archive`` gave
    exit 3 / ``BROKEN`` / ``MANAGED_NOT_HEALTHY`` where the honest answer was ``NOT_FOUND`` (exit 1).

    Driven through the simulate runner in the same shape: gen 1's payload is removed from disk,
    gen 2 is retired, and the failing transaction is gen 3. Neither candidate may come back.
    """

    first, first_plan = _simulate(registry, clock, root, "1.0.0")
    assert first["state"] == "FINALIZED"
    second, second_plan = _simulate(registry, clock, root, "2.0.0")
    assert second["state"] == "FINALIZED"

    first_id = str(first_plan["target"]["instance_id"])
    second_id = str(second_plan["target"]["instance_id"])
    key = str(second_plan["target"]["binding_key"])
    retire(registry, second_id, clock=clock)

    # `gc --apply` is the verb that deletes a payload; its *effect* is what this guard is about, and
    # removing the directory keeps the test to one defect (the plan/approval half of gc has its own
    # guards in test_l1_lifecycle.py).
    shutil.rmtree(Path(root.path) / "store" / first_id)
    shutil.rmtree(Path(root.path) / "store" / second_id)

    third_plan = create_plan(registry, version="3.0.0", clock=clock, ttl_minutes=600)
    third_token = fake_issuer.issue(third_plan, clock=clock, ttl_minutes=600)
    third_store = Path(root.path) / "store" / str(third_plan["target"]["instance_id"])
    failed = SimulationRunner(
        registry, clock=clock, injector=Tamper(at_state="ACTIVE_BOUND", store_dir=third_store)
    ).commit(third_plan, third_token)

    assert failed["state"] == "ROLLED_BACK" and failed["outcome"] == "VERIFY_FAILED", failed
    assert registry.bindings(active_only=True) == [], (
        "no candidate below this generation still stands, so the key must end with no active "
        "binding rather than restored to a payload that is collected, retired or gone"
    )
    generations = sorted(int(row["generation"]) for row in registry.bindings())
    assert len(generations) == 3, "the rollback restores bindings; it never invents one"
    assert registry.active_binding(key) is None

    # The failure record says what was skipped and why, so the end state is auditable rather than
    # merely observable (draft §164 asks for exactly this).
    facts = _evidence(failed)
    assert "was skipped" in facts, facts
    assert second_id in facts and "retired on purpose" in facts, facts
    assert first_id in facts and "payload is not on disk" in facts, facts
    assert "no active binding" in facts, facts
    assert registry.integrity_problems() == []


def test_a_rollback_still_restores_a_predecessor_that_stands(registry, clock, root) -> None:
    """The narrowing is a filter, not a replacement: a live predecessor is still restored.

    Without this half, "never restore anything" would pass every test written for the defect above.
    """

    first, first_plan = _simulate(registry, clock, root, "1.0.0")
    assert first["state"] == "FINALIZED"
    first_id = str(first_plan["target"]["instance_id"])
    first_generation = int(registry.bindings(active_only=True)[0]["generation"])

    second_plan = create_plan(registry, version="2.0.0", clock=clock, ttl_minutes=600)
    second_token = fake_issuer.issue(second_plan, clock=clock, ttl_minutes=600)
    second_store = Path(root.path) / "store" / str(second_plan["target"]["instance_id"])
    failed = SimulationRunner(
        registry, clock=clock, injector=Tamper(at_state="ACTIVE_BOUND", store_dir=second_store)
    ).commit(second_plan, second_token)

    assert failed["state"] == "ROLLED_BACK" and failed["outcome"] == "VERIFY_FAILED"
    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert str(active[0]["instance_id"]) == first_id
    assert int(active[0]["generation"]) == first_generation
    assert "active binding restored to generation" in _evidence(failed), _evidence(failed)


# --------------------------------------------------------------------------- #
# B. a rollback may not rewrite a row it did not register
# --------------------------------------------------------------------------- #


def _install_artifact(registry, clock, root, source: Path, *, version: str, plan_id: str):
    backend = resolve_backend("portable_file")
    plan = create_artifact_plan(
        registry,
        backend,
        capability_id="archive",
        version=version,
        kind="managed_tool",
        locator=str(source),
        source_digest=sha256_file(source),
        clock=clock,
        plan_id=plan_id,
    )
    fake_issuer.install_keyring(root.path)
    token = fake_issuer.issue(plan, clock=clock)
    return ArtifactRunner(registry, backend, clock=clock).commit(plan, token), plan


def test_reinstalling_the_active_version_leaves_that_versions_row_alone(
    registry, clock, root, tmp_path: Path
) -> None:
    """§164/B: the duplicate install used to disable the version that was working.

    Measured before the fix (F12, pass 2): a byte-identical re-install of the active version exited
    7 / ``INSTANCE_CONFLICT`` / ``ROLLED_BACK`` with ``generation 2 -> 3`` and the *live* instance
    rewritten to ``lifecycle_status=broken`` / ``health=broken`` — the row that had been
    ``active``/``healthy`` one command earlier. The capability moved back to the version the
    successful install had displaced.

    The plan id differs from the successful install's, so this is not the idempotent
    same-plan-same-token path: it is a new plan that happens to produce the same instance id.
    """

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"AIROOT-F12-DUPLICATE-INSTALL\n")
    first, first_plan = _install_artifact(
        registry, clock, root, payload, version="9.9.10", plan_id="plan/real/first"
    )
    assert first["state"] == "FINALIZED", first

    instance_id = str(first_plan["target"]["instance_id"])
    key = str(first_plan["target"]["binding_key"])
    generation_before = registry.generation
    binding_before = {str(row["binding_key"]): dict(row) for row in registry.bindings(active_only=True)}

    second, _second_plan = _install_artifact(
        registry, clock, root, payload, version="9.9.10", plan_id="plan/real/duplicate"
    )

    assert second["outcome"] == "INSTANCE_CONFLICT", second
    assert exit_code_for("INSTANCE_CONFLICT") == 7
    assert second["generation_after"] is None, (
        "the payload was never moved, so no generation was committed"
    )
    assert registry.generation == generation_before

    row = registry.instance(instance_id)
    assert row is not None
    assert str(row["lifecycle_status"]) == "active" and str(row["health"]) == "healthy", (
        "a rollback may not take the working version out of service: this transaction did not "
        "register that row (draft §164/B)"
    )
    after = {str(item["binding_key"]): dict(item) for item in registry.bindings(active_only=True)}
    assert after == binding_before, "the active binding was not this attempt's to move"
    assert str(after[key]["instance_id"]) == instance_id

    # The refusal is a state, not a half-done move: the payload is still there and still the
    # registered bytes, and nothing is left asking for recovery.
    store_dir = Path(root.path) / "store" / instance_id
    assert (store_dir / payload.name).read_bytes() == payload.read_bytes()
    assert registry.transactions(unfinished_only=True) == []
    assert registry.integrity_problems() == []


def test_the_occupied_store_path_is_refused_before_the_commit_point(
    registry, clock, root, tmp_path: Path
) -> None:
    """§164/E: the same refusal, held to the stronger statement — decided *before* anything moved.

    ``INSTANCE_CONFLICT`` (exit 7) with the registry generation and the binding untouched, and a
    terminal transaction that never entered the rollback branch: the refusal is a fact about the
    store path, not a failed move that has to be undone.
    """

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"AIROOT-F12-OCCUPIED-STORE\n")
    first, first_plan = _install_artifact(
        registry, clock, root, payload, version="7.7.7", plan_id="plan/real/occupied-first"
    )
    assert first["state"] == "FINALIZED"

    instance_id = str(first_plan["target"]["instance_id"])
    generation_before = registry.generation

    second, _plan = _install_artifact(
        registry, clock, root, payload, version="7.7.7", plan_id="plan/real/occupied-second"
    )

    assert second["outcome"] == "INSTANCE_CONFLICT"
    assert exit_code_for(str(second["outcome"])) == 7
    assert registry.generation == generation_before, "no generation is committed for a refused move"
    assert second["state"] == "ROLLED_BACK"
    assert second["generation_after"] is None
    states = [str(event["state"]) for event in registry.events(transaction_id=str(second["transaction_id"]))]
    assert "STAGED" in states and "FAILED" in states
    assert "ROLLBACK_PENDING" not in states, (
        "nothing moved and nothing was bound: there is nothing to roll back, so the attempt must not "
        "enter the rollback branch (draft §164/E)"
    )
    assert (Path(root.path) / "store" / instance_id / payload.name).is_file()
    assert registry.transactions(unfinished_only=True) == []


# --------------------------------------------------------------------------- #
# C. re-installing a collected instance clears the latch
# --------------------------------------------------------------------------- #


def test_reinstalling_a_collected_instance_clears_collected_at(registry, clock, root) -> None:
    """§164/C: ``collected_at`` describes the bytes, and the bytes came back.

    Measured before the fix (F12, pass 6): after re-installing a collected version, ``tool verify``
    returned ``verified: false`` / ``actual_digest: null`` with the note "the payload was collected
    by an approved gc; there is nothing left to verify" while ``tool status`` said
    ``payload_present: true`` and reported ``PAYLOAD_COLLECTED`` — the registry asserting something
    false about a payload sitting in ``store/``.

    The collected state is produced the way ``gc --apply`` leaves it: payload deleted, row kept.
    """

    first, first_plan = _simulate(registry, clock, root, "1.0.0")
    assert first["state"] == "FINALIZED"
    instance_id = str(first_plan["target"]["instance_id"])
    store_dir = Path(root.path) / "store" / instance_id

    shutil.rmtree(store_dir)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(
            connection, instance_id, lifecycle_status="retired", collected_at="2024-02-02T00:00:00Z"
        )
    assert registry.instance(instance_id)["collected_at"] == "2024-02-02T00:00:00Z"
    assert not store_dir.exists()

    again = create_plan(registry, version="1.0.0", clock=clock, ttl_minutes=600)
    token = fake_issuer.issue(again, clock=clock, ttl_minutes=600)
    result = SimulationRunner(registry, clock=clock).commit(again, token)
    assert result["state"] == "FINALIZED", result

    row = registry.instance(instance_id)
    assert row["collected_at"] is None, (
        "the payload is on disk again, so 'collected by an approved gc' no longer describes it"
    )
    assert row["collected_approval_id"] is None
    assert store_dir.is_dir()

    verified = tool_verify(registry, instance_id, root=Path(root.path))
    assert verified["verified"] is True, verified
    assert verified["actual_digest"] == str(row["artifact_digest"])
    assert "collected" not in str(verified.get("note") or "")
    status = tool_status(registry, instance_id, root=Path(root.path))
    assert "PAYLOAD_COLLECTED" not in [finding.code for finding in status.findings], status.to_document()


# --------------------------------------------------------------------------- #
# D. a `run --capability` failure still answers with an envelope
# --------------------------------------------------------------------------- #


def test_run_capability_failure_is_an_envelope_under_json(capsys, registry, clock, root) -> None:
    """§164/D: "failure is data" must hold for the verb that starts a child process too.

    Measured (F12, pass 3): when the active payload had been collected, the command exited 3 with
    **stdout empty** and the diagnosis as prose on stderr. That probe called `run` without `--json`,
    so the prose was correct for it; what must never be true is that a `--json` failure has no
    envelope, and the resolution side is where this guard holds it — the error is raised before any
    child exists, so a caller cannot read it off a child's exit status.
    """

    first, plan = _simulate(registry, clock, root, "5.0.0")
    assert first["state"] == "FINALIZED"
    instance_id = str(plan["target"]["instance_id"])
    shutil.rmtree(Path(root.path) / "store" / instance_id)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, instance_id, collected_at="2024-03-03T00:00:00Z")

    assert active_instance_for_capability(registry, CAPABILITY) == instance_id
    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, Path(root.path), instance_id)
    assert caught.value.reason_code == "PAYLOAD_MISSING"
    assert exit_code_for("PAYLOAD_MISSING") == 3

    code = main(["--root", str(root.path), "run", "--capability", CAPABILITY, "--json", "--", "--version"])
    captured = capsys.readouterr()

    assert code == 3
    document = json.loads(captured.out)
    assert document["reason_code"] == "PAYLOAD_MISSING"
    assert document["schema_version"] == 1
    assert any("collected_at" in item for item in document["evidence"])
    assert captured.err == "", "a machine-readable failure does not also narrate on stderr"
