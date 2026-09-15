"""L1: deterministic ``where`` selection and effective state.

Covers the verification plan §8 fixtures (healthy, not_found, unmanaged_only,
broken, version_unsatisfied, current_process_stale, recovery_required) and
S-005/S-014/S-019/S-020/S-021.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fake_issuer
from airoot import schema_io
from airoot.caps import WhereQuery, where
from airoot.caps.version import satisfies
from airoot.exits import exit_code_for
from airoot.registry.entities import Binding, Instance, binding_key
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

CAPABILITY = "fake-tool"
KEY = binding_key(CAPABILITY, "machine")
STORE = Path("C:/airoot")


def commit_version(registry, clock, root, version: str) -> dict:
    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version=version, clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    return plan


def query(**kwargs) -> WhereQuery:
    return WhereQuery(capability_id=kwargs.pop("capability_id", CAPABILITY), **kwargs)


def run(registry, root, *, process_entries=None, machine_entries=None, user_entries=None, **kwargs) -> dict:
    return where(
        registry,
        query(**kwargs),
        root=root.path,
        process_entries=process_entries or [],
        machine_entries=machine_entries or [],
        user_entries=user_entries or [],
    )


# --------------------------------------------------------------------------- #
# version constraints
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "version,constraint,expected",
    [
        ("3.12.7", ">=3.11,<3.13", True),
        ("3.13.0", ">=3.11,<3.13", False),
        ("3.11.0", ">=3.11,<3.13", True),
        ("1.7.1", "==1.7.1", True),
        ("1.7.1", "=1.7.1", True),
        ("1.7.1", ">1.7", True),
        ("1.7.1", None, True),
        ("22.1.0", ">=22", True),
        ("3.12", ">=3.12.1", False),
    ],
)
def test_version_constraints(version: str, constraint, expected: bool) -> None:
    assert satisfies(version, constraint) is expected


def test_unsupported_constraint_is_refused_rather_than_widened() -> None:
    from airoot.exits import AirootError

    with pytest.raises(AirootError) as err:
        satisfies("1.0.0", "~1.2")
    assert err.value.reason_code == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def test_no_declared_state_reports_not_found(registry, root) -> None:
    document = run(registry, root)
    schema_io.validate_self("where-response", document)
    assert document["found"] is False
    assert document["usable"] is False
    assert document["instance_id"] is None and document["executable"] is None and document["version"] is None
    assert document["candidates"] == []
    assert document["reason_code"] == "NOT_FOUND"
    assert exit_code_for(document["reason_code"]) == 1


def test_healthy_managed_binding_is_selected(registry, clock, root) -> None:
    plan = commit_version(registry, clock, root, "1.0.0")
    document = run(registry, root)
    schema_io.validate_self("where-response", document)

    assert document["found"] is True and document["usable"] is True
    assert document["instance_id"] == plan["target"]["instance_id"]
    assert document["management"] == "managed" and document["source"] == "registry"
    assert document["zone"] == "R" and document["scope"] == "machine" and document["health"] == "healthy"
    assert document["selection_reason"] == "MACHINE_MANAGED_HEALTHY"
    assert document["executable"].endswith("fake-tool.bin")
    assert document["effective_now"] is False and document["effective_new_process"] is False
    assert exit_code_for(document["reason_code"]) == 0


def test_version_constraint_selects_or_reports_unsatisfied(registry, clock, root) -> None:
    commit_version(registry, clock, root, "1.0.0")

    satisfied = run(registry, root, version=">=1.0,<2.0")
    assert satisfied["found"] is True

    unsatisfied = run(registry, root, version=">=2.0")
    assert unsatisfied["found"] is False
    assert unsatisfied["reason_code"] == "VERSION_UNSATISFIED"
    assert unsatisfied["selection_reason"] == "VERSION_UNSATISFIED"
    assert exit_code_for(unsatisfied["reason_code"]) == 1


def test_broken_managed_binding_is_reported_not_skipped(registry, clock, root) -> None:
    commit_version(registry, clock, root, "1.0.0")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, "fake-tool/fake-tool/1.0.0/win-x64", health="broken")

    document = run(registry, root)
    assert document["found"] is False
    assert document["reason_code"] == "BROKEN"
    assert document["health"] is None, "a broken candidate is never reported as healthy"
    assert document["candidates"][0]["health"] == "broken"
    assert exit_code_for(document["reason_code"]) == 3


def test_owned_broken_degrades_to_a_healthy_reference(registry, clock, root) -> None:
    """S-014 (rewritten): a broken owned binding plus a healthy reference is normal degradation.

    Under managed-first this was ``CONFLICT_MANAGED_BROKEN`` (exit 3). Under the steward model
    the reference is a first-class candidate, so the answer is usable with
    ``CURRENT_SOURCE_DEGRADED`` (exit 2) — and ``CONFLICT_MANAGED_BROKEN`` is no longer emitted
    on this path (draft §6, ADR-0006).
    """

    commit_version(registry, clock, root, "1.0.0")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, "fake-tool/fake-tool/1.0.0/win-x64", health="broken")
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/tools-fake-tool', ?, ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / "fake-tool.bin")),
        )

    degraded = run(registry, root)
    schema_io.validate_self("where-response", degraded)
    assert degraded["found"] is True
    assert degraded["usable"] is True
    assert degraded["management"] == "external_reference"
    assert degraded["selection_reason"] == "CURRENT_SOURCE_DEGRADED"
    assert degraded["reason_code"] == "CURRENT_SOURCE_DEGRADED"
    assert exit_code_for(degraded["reason_code"]) == 2, "degraded, not broken"
    assert degraded["reason_code"] != "CONFLICT_MANAGED_BROKEN"
    managements = {item["management"] for item in degraded["candidates"]}
    assert managements == {"managed", "external_reference"}, "both sides appear as evidence"
    assert any("degradation" in item["detail"] for item in degraded["evidence"])

    # The deprecated switch must not change the answer any more (draft §18.3-6).
    deprecated = run(registry, root, allow_external_fallback=True)
    assert deprecated["reason_code"] == degraded["reason_code"]
    assert deprecated["selection_reason"] == degraded["selection_reason"]
    assert any("deprecated_flag_ignored" in item["detail"] for item in deprecated["evidence"])


def test_owned_broken_without_any_reference_stays_broken(registry, clock, root) -> None:
    """The other half of the rewrite: with nothing to degrade to, ``BROKEN`` is still correct."""

    commit_version(registry, clock, root, "1.0.0")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, "fake-tool/fake-tool/1.0.0/win-x64", health="broken")

    document = run(registry, root)
    assert document["found"] is False
    assert document["reason_code"] == "BROKEN"
    assert exit_code_for(document["reason_code"]) == 3


def test_a_healthy_reference_is_selected_without_any_flag(registry, root) -> None:
    """The reference is no longer a fallback: no flag, no owned payload, no conflict."""

    with registry.write(expected_generation=0) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/tools-fake-tool', ?, ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / "fake-tool.bin")),
        )

    document = run(registry, root)
    assert document["found"] is True
    assert document["management"] == "external_reference"
    assert document["selection_reason"] == "STEWARD_REFERENCE_HEALTHY"
    assert exit_code_for(document["reason_code"]) in {0, 2}


def test_unusable_reference_is_not_reported_as_a_match(registry, root) -> None:
    with registry.write(expected_generation=0) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/drifted', ?, ?, 'external_reference', 'drifted', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / "fake-tool.bin")),
        )

    document = run(registry, root)
    assert document["found"] is False
    assert document["selection_reason"] == "REFERENCE_NOT_USABLE"
    assert document["candidates"][0]["usable"] is False


def test_unmanaged_only_is_reported_without_selection(registry, root) -> None:
    with registry.write(expected_generation=0) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/stray', ?, ?, 'unmanaged', 'healthy', NULL, '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / "stray.bin")),
        )

    document = run(registry, root)
    assert document["found"] is False
    assert document["selection_reason"] == "UNMANAGED_ONLY"
    assert document["candidates"][0]["management"] == "unmanaged"
    assert document["candidates"][0]["usable"] is False, "unmanaged is never a default executable result"


def test_effective_state_distinguishes_current_and_new_process(registry, clock, root) -> None:
    plan = commit_version(registry, clock, root, "1.0.0")
    store = Path(root.path) / "store" / plan["target"]["instance_id"]
    entry_dir = str(store)

    stale = run(registry, root, process_entries=[], machine_entries=[entry_dir], user_entries=[])
    assert stale["found"] is True
    assert stale["effective_now"] is False and stale["effective_new_process"] is True
    assert stale["reason_code"] == "CURRENT_PROCESS_ENV_OLD"
    assert exit_code_for(stale["reason_code"]) == 0, "the result is usable; only the shell is stale"

    fresh = run(registry, root, process_entries=[entry_dir], machine_entries=[entry_dir], user_entries=[])
    assert fresh["reason_code"] == "SUCCESS"
    assert fresh["effective_now"] is True and fresh["effective_new_process"] is True


def test_project_binding_wins_over_machine(registry, clock, root) -> None:
    machine_plan = commit_version(registry, clock, root, "1.0.0")

    project_instance = "fake-tool/fake-tool/9.0.0/win-x64"
    machine_row = registry.instance(machine_plan["target"]["instance_id"])
    with registry.write(expected_generation=registry.generation, bump=True) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id=project_instance,
                kind="managed_tool",
                capability_id=CAPABILITY,
                version="9.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id="fake_fixture",
                artifact_digest=machine_row["artifact_digest"],
                store_path=f"store/{project_instance}",
                lifecycle_status="active",
                health="healthy",
                tool_id="fake-tool",
                file_manifest_digest=machine_row["file_manifest_digest"],
                entrypoints=("fake-tool.bin",),
                created_at="2024-01-01T00:00:00Z",
            ),
        )
        project_key = f"project/proj-123/{CAPABILITY}/windows/x64"
        registry.bind_active(
            connection,
            Binding(project_key, project_instance, "project", "P", "project_binding", 2, True, project_id="proj-123"),
        )

    with_project = run(registry, root, project_id="proj-123")
    assert with_project["instance_id"] == project_instance
    assert with_project["selection_reason"] == "PROJECT_MANAGED_HEALTHY"
    assert with_project["scope"] == "project"

    without_project = run(registry, root)
    assert without_project["instance_id"] == machine_plan["target"]["instance_id"], "machine binding is the fallback"


def test_multiple_versions_coexist_but_only_one_is_active(registry, clock, root) -> None:
    commit_version(registry, clock, root, "1.0.0")
    commit_version(registry, clock, root, "1.1.0")

    assert len(registry.instances()) == 2, "both payloads are retained (S-021)"
    document = run(registry, root)
    assert document["instance_id"] == "fake-tool/fake-tool/1.1.0/win-x64"
    assert document["version"] == "1.1.0"
    assert len(registry.bindings(active_only=True)) == 1


def test_where_reads_only_the_registry(registry, root, monkeypatch) -> None:
    """``where`` must never scan the disk: it works with a registry alone."""

    def explode(*_args, **_kwargs):  # pragma: no cover - would be hit on a disk scan
        raise AssertionError("where must not walk the filesystem")

    monkeypatch.setattr(Path, "rglob", explode)
    document = run(registry, root)
    assert document["reason_code"] == "NOT_FOUND"


def test_capability_without_state_is_independent(registry, clock, root) -> None:
    commit_version(registry, clock, root, "1.0.0")
    other = run(registry, root, capability_id="ffmpeg")
    assert other["found"] is False
    assert other["reason_code"] == "NOT_FOUND"
    assert other["candidates"] == []


def test_where_response_is_json_serialisable_and_stable(registry, clock, root) -> None:
    commit_version(registry, clock, root, "1.0.0")
    first = json.dumps(run(registry, root), sort_keys=True)
    second = json.dumps(run(registry, root), sort_keys=True)
    assert first == second, "field order and content must be deterministic for a fixed state"


# --------------------------------------------------------------------------- #
# Zone W: not machine-discoverable, still reachable by explicit activation (ADR-0022)
# --------------------------------------------------------------------------- #


def test_S006_zone_w_is_not_machine_discoverable_but_answers_explicit_activation(
    registry, clock, root
) -> None:
    """S-006: Zone W takes no part in machine-level discovery, but explicit activation reaches it.

    Draft §53 found this frozen constant with **no execution point at all**: `where` filtered on
    `scope` and never read `zone`, while both `Binding(...)` producers hardcode `"R"`. The invariant
    was therefore unreachable *and* unenforced — "no code can violate it, and no code prevents
    violating it". ADR-0022 lands it, and this is the execution point's evidence.

    Both halves belong in one test, because either alone is satisfied by a wrong rule. Drop the first
    and "W is machine-discoverable" survives; drop the second and "W is never selectable" survives —
    which is a **different** rule. The contract permits W through explicit activation: "W 可以执行，
    但只能通过显式 session/project activation，不参与机器级发现" (三大核心契约 :466), and the master
    plan names the same two boundaries ("不会把这些路径加入 machine PATH、stable launcher 或默认 `where`
    结果").
    """

    plan = commit_version(registry, clock, root, "1.0.0")
    instance_id = plan["target"]["instance_id"]
    session_id = "sess-w"
    session_key = binding_key(CAPABILITY, "session", session_id=session_id)
    generation = registry.generation + 1

    with registry.write(expected_generation=registry.generation, bump=True) as connection:
        # One payload, two bindings, both in Zone W: a machine-level one and one tied to a session.
        registry.bind_active(
            connection, Binding(KEY, instance_id, "machine", "W", "none", generation, True)
        )
        registry.bind_active(
            connection,
            Binding(session_key, instance_id, "session", "W", "session_env", generation, True),
        )

    # Half 1 — machine-level discovery must not reach W, and must say why rather than go quiet.
    machine = run(registry, root)
    assert machine["found"] is False, machine
    assert machine["reason_code"] == "NOT_FOUND", machine
    assert machine["selection_reason"] == "NOT_FOUND", machine
    assert machine["zone"] is None, "nothing was selected, so no zone may be reported"
    excluded = [item for item in machine["candidates"] if not item["machine_discoverable"]]
    assert excluded, machine["candidates"]
    # The refusal is about the zone, not about health: claiming otherwise would be a different bug.
    assert excluded[0]["usable"] is True, excluded[0]
    assert excluded[0]["health"] == "healthy", excluded[0]
    # ...and it is a *machine-level* refusal, so it must not be dressed up as a degradation or a fault.
    assert machine["reason_code"] not in {"BROKEN", "CURRENT_SOURCE_DEGRADED", "VERSION_UNSATISFIED"}

    # Half 2 — the same Zone W binding answers when the caller names the session explicitly.
    activated = run(registry, root, session_id=session_id, scope="session")
    assert activated["found"] is True, activated
    assert activated["scope"] == "session", activated
    assert activated["zone"] == "W", "explicit activation is allowed to reach Zone W"
    assert activated["selection_reason"] == "SESSION_MANAGED_HEALTHY", activated
    assert activated["reason_code"] == "SUCCESS", activated
    # The machine-level binding is still in the candidate list, still marked non-discoverable.
    assert any(not item["machine_discoverable"] for item in activated["candidates"])
