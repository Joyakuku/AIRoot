"""L1: what is true of a payload on disk, and who is allowed to decide with it (draft §147).

``health`` is a recorded fact that nothing re-derives, so a payload deleted by hand kept being
presented as ``health=healthy`` with no hint at all. This module covers the one definition of the
on-disk question (``caps/health.py``) and the two rules that make it safe to have:

1. the observation is **reported** by every reader that has the payload in hand, and those readers
   must agree — the defect was two observers with two copies of the rule (§147.1);
2. it is **not** a second selection rule: an **absent payload directory** is neither reported nor
   decided on by ``where``, because a registry-only reader cannot tell it apart from "nothing was
   ever put there" (ADR-0021, §147.4).

§171 ④ sharpened rule 1 into "one row, one story": the case the observer *can* decide — the payload
directory is there and a declared entrypoint is not — is what the row now reports and decides on, so
a row can no longer say ``health: "healthy"``, ``usable: true`` and "the payload on disk is broken"
at once. The narrowing in rule 2 is untouched, and the tests below pin both halves.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import fake_issuer
import pytest

import importlib

from airoot.caps import WhereQuery

# `airoot.caps` re-exports the *function* `where`, which shadows the module of the same name for
# attribute lookup — so the module (and the observer bound inside it) has to be fetched by name.
where_module = importlib.import_module("airoot.caps.where")
from airoot.caps.health import BROKEN, DRIFTED, HEALTHY, PayloadObservation, observe_payload, observe_reference
from airoot.caps.toolstate import tool_status, tool_verify
from airoot.exits import exit_code_for
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
    """A deleted entrypoint is not a usable payload, and this is where that became a **decision**.

    The defect §147 measured was a reader that did not look (a payload with its entrypoint deleted by
    hand still read ``health=healthy`` to ``where``, with no hint at all); §147 answered it by
    *reporting* the filesystem's verdict while selection stayed on the recorded value. §147.4 argued
    that this way because the tightened reading — "the payload must exist on disk to be selected" —
    is not a layer-2 contract, an honesty rule, a steward invariant or an audit guard, and ADR-0021
    says the relaxed reading wins. §171 ④ then found what the relaxed reading cost: one row that said
    ``health: "healthy"``, ``usable: true`` and "the payload on disk is broken" **at the same time**,
    and an answer whose ``executable`` pointed at a file that is not there — ``run`` and ``tool
    verify`` both refuse that file, so "selectable" was the only reader that disagreed.

    So the row's ``health`` now follows the observation where the observation is **decisive**, and
    ``usable`` follows the row's ``health``: the two fields are one fact about one row, and a payload
    whose declared entrypoint is gone is reported ``broken`` and is **not** selected — the answer is
    ``BROKEN``(3) rather than exit 0 pointing at nothing. This is a correction, not a regression:
    nothing that used to work stops working (the file was already gone), and the change makes ``where``
    agree with the three verbs that act on the payload. §147.4's narrowing is untouched on purpose
    and is pinned next door (``test_an_absent_payload_directory_is_not_reported_by_where``): an
    **absent payload directory** is still neither reported nor decided on, because a registry-only
    reader cannot tell "it was deleted" from "nothing was ever put there". Only the case this test
    builds — the directory is there and the recorded entrypoint is not — is unambiguous, and only that
    case moved. Whether a *recorded* value may be overridden by the disk in general (an instance, not
    a candidate) is a separate ruling and is deliberately not widened here.
    """

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

    # The decision follows the report (draft §171 ④). §147.4 kept `usable` on the **recorded** health
    # so that `where` stayed a registry-only verb, and the price of that was a row saying
    # `health:"healthy"`, `usable:true` and "the payload on disk is broken" in the same breath. The
    # observation is decisive here — the payload directory is there and the declared entrypoint is
    # not — so the row reports it, and a payload whose entrypoint is gone is not usable either (it is
    # the same fact `tool status`, `tool verify` and `run` already refuse on). The narrowing §147.4
    # settled is untouched: an **absent payload directory** is still not reported or decided on.
    managed = [item for item in document["candidates"] if item["management"] == "managed"]
    assert len(managed) == 1, document["candidates"]
    assert managed[0]["health"] == BROKEN, "the row may not say healthy next to this evidence"
    assert managed[0]["usable"] is False, "and it may not call the same row usable"
    assert document["health"] is None, "nothing healthy was selected"
    assert document["found"] is False and document["reason_code"] == "BROKEN"
    assert exit_code_for(document["reason_code"]) == 3


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


# --------------------------------------------------------------------------- #
# §171 ④: one row, one story — the recorded value and the observed one
# --------------------------------------------------------------------------- #

#: A registered external reference whose object root is an *absolute* path, which is what `adopt`
#: records. `entrypoints_json` is the column that decides whether the row has a file to check.
REFERENCE_INSERT = """
INSERT INTO external_references (external_id, capability_id, path, management, health,
                                 observed_digest, observed_at, payload_json, version,
                                 active_version, entrypoints_json)
VALUES (?, ?, ?, 'external_reference', 'healthy', NULL, '2024-01-01T00:00:00Z', '{}',
        '6.1.1', '6.1.1', ?)
"""


def add_reference(registry, *, external_id: str, capability_id: str, path: Path, entrypoints: list[str]) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            REFERENCE_INSERT, (external_id, capability_id, str(path), json.dumps(entrypoints))
        )


def unlabelled_healthy_claims(row: dict) -> list[str]:
    """Evidence that calls the object healthy while the row says it is not.

    ``recorded health=healthy`` is not one of them: it names whose value it is, and the same line
    says the payload on disk disagrees. What the rule forbids is the bare claim — the row's headline
    and its own evidence telling two stories (§171 ④).
    """

    if row["health"] == HEALTHY:
        return []
    return [
        item["detail"]
        for item in row["evidence"]
        if "health=healthy" in item["detail"] and "recorded" not in item["detail"]
    ]


def test_a_reference_row_stops_saying_healthy_once_its_entrypoint_is_gone(registry, root) -> None:
    """The reading §171 opened with: the data root was renamed and the row still said `healthy`.

    Measured before the fix: ``{"health": "healthy", "usable": false, "evidence": [... "the recorded
    entrypoint ffmpeg.exe is missing; the object is stale"]}``. The row already refused to use the
    object, and its own evidence already said the file was gone — only the headline disagreed. The
    check that produces that verdict now comes from ``caps/health.py``'s ``observe_reference`` rather
    than a second copy of "is it there" written here, so `where` and the store observer cannot drift
    apart on what "the recorded entrypoint is missing" means.
    """

    tools = root.path / "tools" / "ffmpeg"
    tools.mkdir(parents=True)
    (tools / "ffmpeg.exe").write_bytes(b"MZ not a real image\n")
    add_reference(
        registry, external_id="external/dr-env/ffmpeg", capability_id="ffmpeg",
        path=tools, entrypoints=["ffmpeg.exe"],
    )

    intact = where_document(registry, root, capability_id="ffmpeg")
    assert intact["found"] is True and intact["health"] == HEALTHY
    assert intact["candidates"][0]["health"] == HEALTHY, "non-vacuity: the intact case still says healthy"
    assert intact["candidates"][0]["usable"] is True

    shutil.move(str(tools), str(root.path / "tools" / "ffmpeg-renamed"))

    document = where_document(registry, root, capability_id="ffmpeg")
    row = document["candidates"][0]
    assert row["health"] != HEALTHY, row
    assert row["health"] == BROKEN, row
    assert row["usable"] is False
    assert unlabelled_healthy_claims(row) == [], row
    details = " ".join(item["detail"] for item in row["evidence"])
    assert "entrypoint ffmpeg.exe is missing" in details, row
    assert document["health"] is None, "nothing healthy was selected"


def test_the_top_level_health_is_the_selected_row_and_nothing_else(registry, clock, root) -> None:
    """§171 ④: the two may differ, and the difference is exactly "no healthy row was selected".

    Before this stage both were read off the record, so they agreed by construction and the
    agreement proved nothing. They agree now because the *selected* row is where the top-level value
    comes from: while the payload is intact the answer is `healthy`; once the declared entrypoint is
    gone nothing healthy is selected, the top-level is `null`, and the row that says `broken` carries
    the evidence for it. Neither surface is allowed to be self-contradictory, which is what the
    helper above checks on the way through.
    """

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = str(plan["target"]["instance_id"])
    entrypoint = root.path / "store" / instance_id / "fake-tool.bin"
    assert entrypoint.is_file(), "the simulated transaction materialises the payload"

    intact = where_document(registry, root)
    selected = [row for row in intact["candidates"] if row["usable"]]
    assert intact["health"] == selected[0]["health"] == HEALTHY

    entrypoint.unlink()

    document = where_document(registry, root)
    row = document["candidates"][0]
    assert document["health"] is None, "no row is usable, so no row's health is the answer"
    assert document["found"] is False and document["reason_code"] == "BROKEN"
    assert row["health"] == BROKEN and row["usable"] is False
    assert unlabelled_healthy_claims(row) == [], row
    assert exit_code_for(document["reason_code"]) == 3
