"""L1/L2: a reader reports the lifecycle it can see, not the lifecycle that was recorded.

This file holds **only** guards for draft §166 (investigation F12 §1.1). Every one of them was
watched fail against the line it protects before it was kept; the report accompanying this change
names that line and the reading on both sides.

The defect is one shape in four places — a value allowed to describe something that is no longer
true. ``ACTIVE_BOUND`` writes the **incoming** instance's row and ``Registry.bind_active`` only
clears the binding it replaces, so a superseded row keeps ``lifecycle_status='active'`` forever
while ``bindings`` holds exactly one ``active=1``. Every reader that passed the column through then
asserted, inside one document, something that same document contradicted:

* ``tool list`` printed two rows reading ``active`` with ``bound=1``;
* ``tool status <superseded>`` invented a finding for it — ``lifecycle says active but no active
  binding exists`` — and exited 2 / ``DEGRADED`` for a version that was healthy and whose payload
  was present;
* ``run`` / ``uninstall --dry-run`` reported the same word for the same row;
* ``tool gc --plan`` printed two candidates reading ``active`` while only one of them carried an
  ``active_binding=`` blocker.

The correction is read-side only (draft §166): ``caps.lifecycle.projected_lifecycle_status`` is the
single derivation, the recorded column is carried beside it as ``recorded_lifecycle_status`` in the
one document that reads both, and the write side — the published enum and a migration — is
deliberately not touched. The ``gc`` **admission rule** is not part of the reading either:
``installed`` is still not ``retired``, so the verdict and the shape of every blocker are what they
were. What the guards below hold, in order: the reading; the recorded value surviving; the
neighbouring findings; the three other call sites; the gc report and its unchanged decision; the
one-spelling rule; and that observing changed nothing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import fake_issuer

from airoot.caps.toolstate import list_tools, tool_status
from airoot.cli import main
from airoot.exits import exit_code_for
from airoot.registry.entities import Instance
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "fake-tool"
REPO = Path(__file__).resolve().parents[2]

#: The spellings by which a report site used to hand the recorded column to a caller as if it were
#: a fact: the two document shapes (`"lifecycle_status": str(row…)`,
#: `lifecycle_status=str(row…)`) and `gc_candidates`' local `status = str(row["lifecycle_status"])`.
#: Named here rather than inlined so the source guard below reads as the sentence it is. The
#: derivation's own `recorded = str(row["lifecycle_status"])` is deliberately **not** a match: that
#: one line is the audit record being read on purpose, and it is where the two values part company.
RAW_REPORT_SPELLINGS = (
    '"lifecycle_status": str(row',
    "lifecycle_status=str(row",
    'status = str(row["lifecycle_status"])',
)


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


def install(registry, clock, root, version: str) -> str:
    """Commit one version through the simulate runner, the way `install` does."""

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock, ttl_minutes=600)
    token = fake_issuer.issue(plan, clock=clock, ttl_minutes=600)
    result = SimulationRunner(registry, clock=clock).commit(plan, token)
    assert result["state"] == "FINALIZED", result
    return str(plan["target"]["instance_id"])


def install_two(registry, clock, root) -> tuple[str, str]:
    """The F12 scenario: 9.9.9 first, 9.9.10 over it. Returns ``(superseded, bound)``."""

    superseded = install(registry, clock, root, "9.9.9")
    bound = install(registry, clock, root, "9.9.10")
    active = registry.bindings(active_only=True)
    assert [str(row["instance_id"]) for row in active] == [bound], (
        "the scenario is only the scenario if exactly one binding is active"
    )
    assert str(registry.instance(superseded)["lifecycle_status"]) == "active", (
        "the recorded column still says active for the superseded row; that is the premise"
    )
    return superseded, bound


# --------------------------------------------------------------------------- #
# A. the reading: `active` means "an active binding points here"
# --------------------------------------------------------------------------- #


def test_a_superseded_instance_reads_installed_and_keeps_its_recorded_value(
    registry, clock, root
) -> None:
    """§166/A: the two rows of `tool list` used to both read `active` with `bound=1`.

    Measured before the fix (F12 §1.1): the superseded row was
    ``lifecycle_status="active", active_binding=null`` in the same document that reported
    ``bound=1`` — one document, two contradictory fields. The reading is now derived from the
    binding and the record is carried beside it, byte for byte.
    """

    superseded, bound = install_two(registry, clock, root)

    document = list_tools(registry)

    rows = {item["instance_id"]: item for item in document["instances"]}
    assert document["count"] == 2 and document["bound"] == 1

    assert rows[superseded]["lifecycle_status"] == "installed"
    assert rows[superseded]["recorded_lifecycle_status"] == "active"
    assert rows[superseded]["active_binding"] is None

    assert rows[bound]["lifecycle_status"] == "active"
    assert rows[bound]["recorded_lifecycle_status"] == "active"
    assert rows[bound]["active_binding"]["binding_key"]

    # Write side untouched (draft §166/C): reading the table changed nothing on disk.
    assert str(registry.instance(superseded)["lifecycle_status"]) == "active", (
        "the record is the audit trail and is not rewritten by a reader"
    )
    assert str(registry.instance(bound)["lifecycle_status"]) == "active"


def test_a_hand_edited_active_row_without_a_binding_reads_installed(
    registry, clock, root
) -> None:
    """The same rule for a row nobody superseded: the column is not evidence of a binding.

    A hand-edited database (``lifecycle_status='active'`` on an instance that was never bound) is
    the case that says the derivation is about the binding rather than about install order, and it
    is also the case where the recorded value is the *only* thing that could be reported wrongly.
    """

    instance_id = "archive/probe/9.9.8/win-x64"
    store = Path(root.path) / "store" / instance_id
    store.mkdir(parents=True, exist_ok=True)
    (store / "fake-tool.bin").write_bytes(b"handmade\n")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id=instance_id,
                kind="managed_tool",
                capability_id="probe",
                version="9.9.8",
                platform="windows",
                architecture="x64",
                install_backend_id="fake_fixture",
                artifact_digest="sha256:" + "a" * 64,
                store_path=f"store/{instance_id}",
                lifecycle_status="installed",
                health="healthy",
                entrypoints=("fake-tool.bin",),
                created_at="2024-01-01T00:00:00Z",
            ),
        )
        registry.set_instance_status(connection, instance_id, lifecycle_status="active")

    document = list_tools(registry)
    row = next(item for item in document["instances"] if item["instance_id"] == instance_id)

    assert row["lifecycle_status"] == "installed"
    assert row["recorded_lifecycle_status"] == "active"
    assert row["active_binding"] is None
    assert str(registry.instance(instance_id)["lifecycle_status"]) == "active"


def test_every_other_recorded_value_passes_through_untouched(registry, clock, root) -> None:
    """The derivation has one special case, not a vocabulary of its own.

    `retired` / `broken` / an already-honest `installed` are read exactly as recorded — the rule
    only refuses to repeat the one value the binding table can contradict. Each of the three is
    unbound here, so the reading is the record for a reason the derivation had to decide.
    """

    from airoot.caps.lifecycle import retire

    retired = install(registry, clock, root, "1.0.0")
    retire(registry, retired, clock=clock)
    for instance_id, recorded in (
        ("archive/probe/9.9.7/win-x64", "broken"),
        ("archive/probe/9.9.6/win-x64", "installed"),
    ):
        with registry.write(expected_generation=registry.generation) as connection:
            registry.add_instance(
                connection,
                Instance(
                    instance_id=instance_id,
                    kind="managed_tool",
                    capability_id="probe",
                    version=instance_id.split("/")[2],
                    platform="windows",
                    architecture="x64",
                    install_backend_id="fake_fixture",
                    artifact_digest="sha256:" + "a" * 64,
                    store_path=f"store/{instance_id}",
                    lifecycle_status=recorded,
                    health="healthy",
                    entrypoints=("fake-tool.bin",),
                    created_at="2024-01-01T00:00:00Z",
                ),
            )

    rows = {item["instance_id"]: item for item in list_tools(registry)["instances"]}

    assert rows[retired]["lifecycle_status"] == "retired"
    assert rows[retired]["recorded_lifecycle_status"] == "retired"
    assert rows["archive/probe/9.9.7/win-x64"]["lifecycle_status"] == "broken"
    assert rows["archive/probe/9.9.6/win-x64"]["lifecycle_status"] == "installed"
    assert rows["archive/probe/9.9.6/win-x64"]["recorded_lifecycle_status"] == "installed"


# --------------------------------------------------------------------------- #
# B. `tool status`: the finding that reported the lie is gone, and only it
# --------------------------------------------------------------------------- #


def test_tool_status_of_a_superseded_instance_succeeds(capsys, registry, clock, root) -> None:
    """§166/B: this verb answered exit 2 / `DEGRADED` for every version a new one had replaced.

    Measured before the fix (F12 §1.1): `tool status 9.9.9` gave exit 2, `reason_code=DEGRADED`,
    findings ``[{warning, DEGRADED, "lifecycle says active but no active binding exists for this
    instance"}]`` for a healthy payload that was present. After the derivation that warning's
    condition can never hold, so it was deleted rather than downgraded — a healthy superseded
    version is not a degraded one.
    """

    superseded, _bound = install_two(registry, clock, root)

    code, document = run(capsys, "--json", "--root", str(root.path), "tool", "status", superseded)

    assert code == 0, document
    assert document["reason_code"] == "SUCCESS"
    assert document["findings"] == []
    assert document["payload_present"] is True
    assert document["entrypoints_missing"] == []
    assert document["instance"]["lifecycle_status"] == "installed"
    assert document["instance"]["recorded_lifecycle_status"] == "active"


def test_the_currently_bound_version_reads_exactly_as_before(capsys, registry, clock, root) -> None:
    """The other half of the same reading: nothing changed for the instance that *is* bound."""

    _superseded, bound = install_two(registry, clock, root)

    code, document = run(capsys, "--json", "--root", str(root.path), "tool", "status", bound)

    assert code == 0, document
    assert document["reason_code"] == "SUCCESS"
    assert document["findings"] == []
    assert document["instance"]["lifecycle_status"] == "active"
    assert document["instance"]["recorded_lifecycle_status"] == "active"
    assert document["instance"]["health"] == "healthy"
    assert document["instance"]["active_binding"]["binding_key"]
    assert document["payload_present"] is True


def test_the_neighbouring_findings_still_fire(registry, clock, root) -> None:
    """Deleting one warning must not quiet the verb: the other findings keep their severity.

    A superseded instance whose payload someone then removed still has to be an error — that is the
    regression a careless "make the finding stop firing" edit would cause.
    """

    superseded, _bound = install_two(registry, clock, root)
    shutil.rmtree(Path(root.path) / "store" / superseded)

    status = tool_status(registry, superseded, root=root.path)
    codes = [finding.code for finding in status.findings]

    assert "PAYLOAD_MISSING" in codes, status.to_document()
    assert status.reason_code == "PAYLOAD_MISSING"
    assert exit_code_for(status.reason_code) == 3


def test_a_bound_instance_whose_payload_is_gone_is_still_binding_target_missing(
    registry, clock, root
) -> None:
    """`BINDING_TARGET_MISSING` is the finding that is *about* a binding, and it is untouched."""

    _superseded, bound = install_two(registry, clock, root)
    shutil.rmtree(Path(root.path) / "store" / bound)

    status = tool_status(registry, bound, root=root.path)
    codes = [finding.code for finding in status.findings]

    assert "BINDING_TARGET_MISSING" in codes, status.to_document()
    assert status.instance["lifecycle_status"] == "active", (
        "the reading still says active: the binding is what makes it active, not the payload"
    )


# --------------------------------------------------------------------------- #
# C. the other three call sites read through the same derivation
# --------------------------------------------------------------------------- #


def test_the_run_target_reports_the_derived_value(registry, clock, root) -> None:
    """§166/C-1: `RunTarget.lifecycle_status` was `str(row["lifecycle_status"])`.

    §164/D settled that this verb's report is an envelope; the field inside it is this stage's.
    """

    from airoot.caps.runtime import resolve_run_target

    superseded, bound = install_two(registry, clock, root)

    assert resolve_run_target(registry, root.path, superseded).lifecycle_status == "installed"
    assert resolve_run_target(registry, root.path, bound).lifecycle_status == "active"


def test_uninstall_dry_run_reports_the_derived_value(capsys, registry, clock, root) -> None:
    """§166/C-2: the `uninstall --dry-run` report said `active` for a superseded instance.

    The `--dry-run` half itself is §160's and is not retested here; what is pinned is the word in
    the report, and that the report still carries no second lifecycle field.
    """

    superseded, _bound = install_two(registry, clock, root)

    code, document = run(
        capsys, "--json", "--root", str(root.path), "uninstall", superseded, "--dry-run"
    )

    assert code == 0, document
    assert document["dry_run"] is True
    assert document["lifecycle_status"] == "installed"
    assert "recorded_lifecycle_status" not in document, (
        "the recorded value is carried where both values are read together, not in every report"
    )


def test_gc_plan_no_longer_reports_two_rows_as_active(capsys, registry, clock, root) -> None:
    """§166/C-3: the fourth report site — `tool gc --plan` printed the recorded column per candidate.

    Measured before this line changed (F12 §1.1 follow-up): one ``tool gc --plan`` document carried
    ``lifecycle_status="active"`` for **both** the bound and the superseded instance, while only one
    of them also carried an ``active_binding=`` blocker. The admission verdict was never wrong; the
    sentence was. The blocker text is derived too, so it now says which state the row is really in.
    """

    superseded, bound = install_two(registry, clock, root)

    code, document = run(capsys, "--json", "--root", str(root.path), "tool", "gc", "--plan")

    assert code == 0, document
    rows = {item["instance_id"]: item for item in document["candidates"]}
    assert rows[superseded]["lifecycle_status"] == "installed"
    assert rows[bound]["lifecycle_status"] == "active"
    assert rows[superseded]["blockers"] == ["lifecycle_status=installed (must be retired first)"], (
        "the blocker is phrased from the reading; what it concludes is unchanged"
    )
    assert rows[bound]["blockers"][0] == "lifecycle_status=active (must be retired first)"
    assert any(item.startswith("active_binding=") for item in rows[bound]["blockers"]), rows[bound]


def test_the_gc_admission_decision_is_unchanged(registry, clock, root) -> None:
    """Non-vacuity for the guard above: the words changed, the verdict did not.

    Both candidates are still refused while neither is retired — the superseded one because
    ``installed`` is not ``retired``, the bound one because it is bound *and* not retired — and
    retiring the superseded instance still makes it collectable. Without this half, "report
    ``installed``" would be satisfied by a ``gc`` that refuses everything.
    """

    from airoot.caps.lifecycle import gc_candidates, retire

    superseded, bound = install_two(registry, clock, root)
    rows = {item.instance_id: item for item in gc_candidates(registry)}

    assert rows[superseded].collectable is False
    assert rows[superseded].lifecycle_status == "installed"
    assert rows[superseded].blockers == ["lifecycle_status=installed (must be retired first)"]
    assert rows[bound].collectable is False
    assert any(item.startswith("active_binding=") for item in rows[bound].blockers)

    retire(registry, superseded, clock=clock)

    after = {item.instance_id: item for item in gc_candidates(registry)}
    assert after[superseded].lifecycle_status == "retired"
    assert after[superseded].blockers == []
    assert after[superseded].collectable is True, after[superseded].blockers


def test_the_report_sites_do_not_spell_the_raw_column_as_a_report() -> None:
    """§166: one derivation, four report sites — and the old spelling cannot come back quietly.

    A source guard, because the defect is a *pass-through shape* rather than a value: the fix is
    undone by writing `str(row["lifecycle_status"])` into any of the four reports again, and no
    behavioural test would notice if the row happened to be bound. ``caps/lifecycle.py`` is in the
    list even though it *defines* the derivation, because that is where the fourth site lives.
    """

    readers = ("caps/lifecycle.py", "caps/toolstate.py", "caps/runtime.py", "cli.py")
    problems: list[str] = []
    for name in readers:
        text = (REPO / "cli" / "app" / "airoot" / name).read_text(encoding="utf-8")
        if "projected_lifecycle_status" not in text:
            problems.append("%s: does not go through caps.lifecycle.projected_lifecycle_status" % name)
        for spelling in RAW_REPORT_SPELLINGS:
            if spelling in text:
                problems.append("%s: reports the recorded column raw (%s)" % (name, spelling))
    assert not problems, "\n".join(problems)


def test_listing_the_table_asks_the_bindings_table_once_for_the_derivation(
    registry, clock, root
) -> None:
    """`bound` is computed once per listing rather than per row (draft §166).

    `_binding_for` already reads the same table once per row to fill `active_binding`; the
    derivation must not add a second per-row scan of the same question. The count is the evidence:
    ``rows + 1`` calls, not ``rows * 2``.

    The counter wraps the registry rather than the module so it observes exactly the calls the
    listing makes, whichever module makes them.
    """

    install_two(registry, clock, root)
    real = registry.bindings
    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    registry.bindings = counting
    try:
        document = list_tools(registry)
    finally:
        registry.bindings = real

    assert document["count"] == 2
    assert calls["n"] == document["count"] + 1, (
        "expected one scan for every row's own binding plus one for the derivation; got %d"
        % calls["n"]
    )


def test_observing_a_superseded_root_changes_nothing(registry, clock, root) -> None:
    """The read side stays read-only: no generation bump, no column written, payload untouched."""

    from airoot.canon import tree_digest

    superseded, bound = install_two(registry, clock, root)
    generation = registry.generation
    trees = {
        name: tree_digest(Path(root.path) / "store" / name) for name in (superseded, bound)
    }

    list_tools(registry)
    tool_status(registry, superseded, root=root.path)
    tool_status(registry, bound, root=root.path)

    assert registry.generation == generation
    assert {name: tree_digest(Path(root.path) / "store" / name) for name in trees} == trees
    assert str(registry.instance(superseded)["lifecycle_status"]) == "active"
    assert registry.integrity_problems() == []
def test_the_gc_candidates_report_the_derived_lifecycle_too(registry, clock, root) -> None:
    """§166: `tool gc --plan` was the **fourth** reader of the same column, and the last one.

    Its per-row report carried `lifecycle_status` straight from the table, so a candidate list showed
    two rows reading `active` with an `active_binding=` line justifying only one of them — the same
    sentence the other three readers stopped saying. It was found while auditing the readers for this
    stage rather than in the original reading, which is why it has a guard of its own here.

    The admission rule is deliberately **not** part of this change: `installed` is still not
    `retired`, so a superseded version stays uncollectable and every blocker keeps its reason. Only
    the reported word changed, because the word was wrong.
    """

    from airoot.caps.lifecycle import gc_candidates

    superseded, current = install_two(registry, clock, root)
    rows = {item.instance_id: item for item in gc_candidates(registry)}

    assert rows[superseded].lifecycle_status == "installed", rows[superseded].to_document()
    assert rows[current].lifecycle_status == "active"
    # One document, one `active`: a row that claims it has to carry the binding that says so.
    assert [row.instance_id for row in rows.values() if row.lifecycle_status == "active"] == [current]
    assert any(blocker.startswith("active_binding=") for blocker in rows[current].blockers), (
        rows[current].blockers
    )

    # Non-vacuity for the rule half: the verdict and the reason are the ones they always were.
    assert rows[superseded].collectable is False and rows[current].collectable is False
    assert "lifecycle_status=installed (must be retired first)" in rows[superseded].blockers
    assert "lifecycle_status=active (must be retired first)" in rows[current].blockers
