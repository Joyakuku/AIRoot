"""L1: what is true of a payload on disk, and who is allowed to decide with it (draft §147).

``health`` is a recorded fact that nothing re-derives, so a payload deleted by hand kept being
presented as ``health=healthy`` with no hint at all. This module covers the one definition of the
on-disk question (``caps/health.py``) and the two rules that make it safe to have:

1. the observation is **reported** by every reader that has the payload in hand, and those readers
   must agree — the defect was two observers with two copies of the rule (§147.1);
2. it is **not** a second selection rule: ``where`` stays registry-only, because making selection
   depend on the file being there is the tightened reading of a fact that admits both readings, and
   ADR-0021 settles that the relaxed one wins (§147.4).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import fake_issuer
import pytest

import importlib

from airoot.caps import WhereQuery

# `airoot.caps` re-exports the *function* `where`, which shadows the module of the same name for
# attribute lookup — so the module (and the observer bound inside it) has to be fetched by name.
where_module = importlib.import_module("airoot.caps.where")
from airoot.caps.health import BROKEN, DRIFTED, HEALTHY, PayloadObservation, observe_payload
from airoot.caps.toolstate import tool_status, tool_verify
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "fake-tool"


def commit_version(registry, clock, root, version: str) -> dict:
    """One simulated transaction, exactly as the other where tests build a binding."""

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return plan


def where_document(registry, root, **kwargs) -> dict:
    return where_module.where(
        registry,
        WhereQuery(capability_id=kwargs.pop("capability_id", CAPABILITY), **kwargs),
        root=root.path,
        process_entries=[],
        machine_entries=[],
        user_entries=[],
    )


def payload_evidence(document: dict) -> list[dict]:
    """Every `payload` evidence entry across the candidates `where` reported."""

    return [
        item
        for candidate in document["candidates"]
        for item in candidate.get("evidence", [])
        if item.get("kind") == "payload"
    ]


# --------------------------------------------------------------------------- #
# the derivation itself, one branch at a time
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("entrypoints", "body", "expected", "code"),
    [
        (["tool.bin"], ["tool.bin"], HEALTHY, None),
        (["tool.bin"], ["other.bin"], BROKEN, "PAYLOAD_MISSING"),
        (["tool.bin", "tool-extra.bin"], ["tool.bin"], BROKEN, "PAYLOAD_MISSING"),
        (["tool.bin"], [], BROKEN, "PAYLOAD_MISSING"),
        ([], [], HEALTHY, None),
    ],
)
def test_the_observation_reports_each_shape_of_a_payload(
    tmp_path: Path, entrypoints: list[str], body: list[str], expected: str, code: str | None
) -> None:
    """A real directory, one branch per case. The last row is the honest empty case: an instance
    that declares no entrypoint has nothing missing — `where` refuses that separately (a registered
    instance with no entrypoint is `PAYLOAD_MISSING` at `run` time), and this observation must not
    invent a finding for it.
    """

    store = tmp_path / "store" / "tool"
    store.mkdir(parents=True)
    for name in body:
        (store / name).write_bytes(b"payload")

    observation = observe_payload(
        root=tmp_path, store_path="store/tool", entrypoints=entrypoints
    )

    assert observation.observed_health == expected
    assert observation.missing == [name for name in entrypoints if name not in body]
    assert [item["code"] for item in observation.problems] == ([code] if code else [])
    assert observation.payload_present is True


def test_a_declaration_outside_the_store_is_drifted_not_broken(tmp_path: Path) -> None:
    """The files can be intact where they are; what is wrong is the declaration (§66).

    `drifted` is the one health value this build gives a writer to for the first time — recorded
    values are still only `healthy` and `broken`, because nothing here writes the registry.
    """

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "tool.bin").write_bytes(b"payload")

    observation = observe_payload(root=tmp_path, store_path="elsewhere", entrypoints=["tool.bin"])

    assert observation.in_store is False
    assert observation.observed_health == DRIFTED
    assert [item["code"] for item in observation.problems] == ["PAYLOAD_OUTSIDE_STORE"]


def test_a_missing_payload_directory_is_a_finding_and_not_a_raise(tmp_path: Path) -> None:
    """A gone payload is the normal case this exists for, so it must not throw (§62)."""

    observation = observe_payload(root=tmp_path, store_path="store/gone", entrypoints=["tool.bin"])

    assert observation.payload_present is False
    assert observation.observed_health == BROKEN
    assert observation.to_document()["entrypoints_missing"] == []


# --------------------------------------------------------------------------- #
# the two observers, on the same payload
# --------------------------------------------------------------------------- #


def test_both_observers_agree_once_the_entrypoint_is_deleted(registry, clock, root) -> None:
    """The defect this stage measured, from both sides (`test_where_reads_only_the_registry` stays)."""

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = str(plan["target"]["instance_id"])
    entrypoint = root.path / "store" / instance_id / "fake-tool.bin"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_bytes(b"payload")

    # While the payload is intact: nothing to report, from either observer.
    assert payload_evidence(where_document(registry, root)) == []
    assert tool_status(registry, instance_id, root=root.path).entrypoints_missing == []

    entrypoint.unlink()

    # `tool status` has always seen this...
    status = tool_status(registry, instance_id, root=root.path)
    assert status.entrypoints_missing == ["fake-tool.bin"], "the entrypoint is gone and it says so"
    assert status.reason_code == "MANIFEST_DIGEST_MISMATCH"

    # ...and `where` used to present the candidate as `health=healthy` with no hint at all. Now it
    # reports what the filesystem says, in evidence of its own rather than folded into `layout`.
    document = where_document(registry, root)
    reports = payload_evidence(document)
    assert len(reports) == 1, f"the deleted payload was not reported: {document['candidates']}"
    assert BROKEN in reports[0]["detail"]
    assert "missing" in reports[0]["detail"]

    # The decision is deliberately unchanged (ADR-0021, §147.4): `where` is a registry-only verb,
    # and this stage adds a report rather than a second selection rule.
    managed = [item for item in document["candidates"] if item["management"] == "managed"]
    assert len(managed) == 1, document["candidates"]
    assert managed[0]["usable"] is True, "selection stayed registry-only; see §147.4"
    assert document["health"] == HEALTHY, "the *recorded* health is still what the row says"


def test_an_absent_payload_directory_is_not_reported_by_where(registry, clock, root) -> None:
    """The golden corpus settled this one: `where_healthy` said the payload was broken.

    The corpus scenario is built from a registry alone, so the instance's directory does not exist —
    and a registry-only reader cannot tell "the directory is gone" apart from "nothing was ever put
    there". Reporting that made a fixture called *healthy* carry a broken-payload report, which is
    how the narrowing was found: the corpus went red, not a reading of the code (§147.5).

    So the report is limited to what **is** unambiguous — the directory is there and a declared
    entrypoint is not (`test_both_observers_agree_once_the_entrypoint_is_deleted`) — and this case
    pins the other half, including that `where` still answers.
    """

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = str(plan["target"]["instance_id"])
    payload_dir = root.path / "store" / instance_id

    # The simulated transaction materialises the payload, entries and all: nothing to report.
    assert payload_dir.is_dir() and (payload_dir / "fake-tool.bin").is_file()
    assert payload_evidence(where_document(registry, root)) == []

    shutil.rmtree(payload_dir)
    assert not payload_dir.exists()
    document = where_document(registry, root)
    assert payload_evidence(document) == [], (
        "an absent payload directory is not `where`'s to report; `tool status` covers it"
    )
    assert document["found"] is True, "and it is still not a reason to refuse to answer"


def test_the_payload_report_is_what_the_test_observes(registry, clock, root, monkeypatch) -> None:
    """Non-vacuity by mutation: silence the observer and the report must disappear.

    A guard that reads a fact it never actually consults would look exactly like this one on a clean
    tree, so the observer is replaced with one that always says `healthy` and the assertion above is
    required to fail.
    """

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = str(plan["target"]["instance_id"])
    entrypoint = root.path / "store" / instance_id / "fake-tool.bin"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_bytes(b"payload")
    entrypoint.unlink()

    assert payload_evidence(where_document(registry, root)), "the real observation reports it"

    def always_healthy(**_kwargs) -> PayloadObservation:
        return PayloadObservation(
            store_path="store/whatever",
            in_store=True,
            payload_present=True,
            observed_health=HEALTHY,
        )

    monkeypatch.setattr(where_module, "observe_payload", always_healthy)
    assert payload_evidence(where_document(registry, root)) == [], (
        "the report survived an observer that says the payload is fine, so it is not reading it"
    )


def test_tool_verify_uses_the_same_observation(registry, clock, root) -> None:
    """The third reader of the same fact, so the three cannot drift apart again."""

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = str(plan["target"]["instance_id"])
    entrypoint = root.path / "store" / instance_id / "fake-tool.bin"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_bytes(b"payload")
    entrypoint.unlink()

    document = tool_verify(registry, instance_id, root=root.path)

    assert document["verified"] is False
    assert any(
        item["code"] == "PAYLOAD_MISSING" and "fake-tool.bin" in item["detail"]
        for item in document["problems"]
    ), document["problems"]
