"""L2: recovery must resume with the driver the transaction was created with (draft §128).

`repair` hard-coded `SimulationRunner`. A transaction created by the `ArtifactRunner` -- a real artifact
install -- was therefore resumed by the simulation driver: it walked the commit point a second time
(generation 1 -> 2), failed its own post-bind verification against an artifact instance, and rolled back
an install that would have completed. `test_every_boundary_is_recoverable` already states the rule for
the simulation runner; this is the same rule for the other one, which had no such coverage at all.
"""

from __future__ import annotations

from pathlib import Path

import fake_issuer
import pytest

from airoot.caps.backends import resolve_backend, sha256_file
from airoot.tx.artifact import ArtifactRunner, create_artifact_plan
from airoot.tx.simulate import repair


def build(registry, clock, root, tmp_path: Path, *, version: str = "2.0.0", boundary: str | None = None):
    source = tmp_path / f"payload-{version}.bin"
    source.write_bytes(b"AIROOT-REAL-ARTIFACT-" + version.encode() + b"\nscript-free payload\n")
    backend = resolve_backend("portable_file")
    plan = create_artifact_plan(
        registry,
        backend,
        capability_id="fake-tool",
        version=version,
        kind="managed_tool",
        locator=str(source),
        source_digest=sha256_file(source),
        clock=clock,
        plan_id=f"plan/real/{version}",
    )
    fake_issuer.install_keyring(root.path)
    token = fake_issuer.issue(plan, clock=clock)
    injector = None
    if boundary is not None:
        from conftest import FaultInjector

        injector = FaultInjector(stop_after=boundary)
    tx = ArtifactRunner(registry, backend, clock=clock, injector=injector).commit(plan, token)
    return tx, plan


def test_an_artifact_transaction_recovers_from_the_commit_point(registry, clock, root, tmp_path: Path) -> None:
    """The artifact runner's rule, which no test stated: parked at the commit point, repair finalizes."""

    tx, plan = build(registry, clock, root, tmp_path, boundary="ACTIVE_BOUND")
    assert tx["state"] == "ACTIVE_BOUND", "the interruption happens only after the state is durable"

    result = repair(registry, tx["transaction_id"], clock=clock, keyring=fake_issuer.keyring())

    assert result["state"] == "FINALIZED", (
        "an artifact transaction parked at the commit point must finish, not roll back: "
        f"{result.get('result', {}).get('failure')}"
    )
    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert active[0]["instance_id"] == plan["target"]["instance_id"]
    assert registry.generation == active[0]["generation"], "the commit point must not be walked twice"
    assert registry.integrity_problems() == []

    again = repair(registry, tx["transaction_id"], clock=clock, keyring=fake_issuer.keyring())
    assert again["action"] == "no_action", "repair is idempotent"


#: The boundaries this driver recovers from today. `FETCHED` is deliberately absent and has its own
#: test below: resuming there raises `AttributeError: _artifact`, which is draft \u00a7128.5.
@pytest.mark.parametrize("boundary", ["STAGED", "REGISTERED", "ACTIVE_BOUND", "EXPOSED"])
def test_an_artifact_transaction_recovers_from_every_boundary(
    registry, clock, root, tmp_path: Path, boundary: str
) -> None:
    """Same coverage the simulation runner has, so the two drivers cannot drift apart again."""

    tx, plan = build(registry, clock, root, tmp_path, version=f"3.{len(boundary)}.0", boundary=boundary)
    assert tx["state"] == boundary

    result = repair(registry, tx["transaction_id"], clock=clock, keyring=fake_issuer.keyring())

    assert result["state"] == "FINALIZED", (boundary, result.get("result", {}).get("failure"))
    active = registry.bindings(active_only=True)
    assert len(active) == 1 and active[0]["instance_id"] == plan["target"]["instance_id"]
    assert registry.integrity_problems() == []


@pytest.mark.xfail(
    reason="draft §128.5: resuming the artifact runner from FETCHED raises AttributeError: '_artifact' -- the resume path does not re-fetch, so the artifact is never set",
    strict=True,
)
def test_resuming_from_fetched_does_not_crash(registry, clock, root, tmp_path: Path) -> None:
    """`FETCHED` means the bytes are on disk and nothing is verified yet: it must recover too.

    `strict=True` is the point: this is a recorded defect, not a tolerance. The day the resume
    path re-fetches, this XPASSes and forces whoever fixed it to delete the marker.
    """

    tx, _plan = build(registry, clock, root, tmp_path, version="4.0.0", boundary="FETCHED")
    assert tx["state"] == "FETCHED"

    result = repair(registry, tx["transaction_id"], clock=clock, keyring=fake_issuer.keyring())

    assert result["state"] == "FINALIZED"
