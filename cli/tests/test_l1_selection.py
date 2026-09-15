"""The selection policy surface (draft §6, draft §18.3-1).

The policy decides the order of the two machine-level candidate sources. Its failure behaviour
is the part worth testing: a **missing** file must fall back to the steward default (the
premise of ADR-0004), while an **invalid** file must be loud rather than reinterpreted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.selection import (
    PRECEDENCE_OWNED,
    PRECEDENCE_STEWARD,
    SelectionPolicy,
    load_selection_policy,
)
from airoot.caps.where import WhereQuery, where
from airoot.exits import AirootError

CAPABILITY = "fake-tool"


def write_policy(directory: Path, document: dict) -> Path:
    path = directory / "selection-policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_shipped_policy_is_steward_first() -> None:
    policy = load_selection_policy()

    assert policy.precedence == PRECEDENCE_STEWARD
    assert policy.steward_first is True
    assert policy.source == "file"
    assert policy.revision


def test_a_missing_file_falls_back_to_steward(tmp_path: Path) -> None:
    policy = load_selection_policy(tmp_path / "absent.json")

    assert policy.precedence == PRECEDENCE_STEWARD
    assert policy.source == "default", "the degradation must be visible to the caller"


def test_an_unreadable_file_falls_back_to_steward(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_selection_policy(path).source == "default"


def test_an_explicit_owned_preference_is_honoured(tmp_path: Path) -> None:
    policy = load_selection_policy(write_policy(tmp_path, {"precedence": PRECEDENCE_OWNED}))

    assert policy.precedence == PRECEDENCE_OWNED
    assert policy.steward_first is False


def test_an_invalid_precedence_is_refused_not_reinterpreted(tmp_path: Path) -> None:
    path = write_policy(tmp_path, {"precedence": "whatever"})

    with pytest.raises(AirootError) as caught:
        load_selection_policy(path)
    assert caught.value.reason_code == "INVALID_INPUT"


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    """A typo must not silently fall back to the default: that is how a policy disappears."""

    path = write_policy(tmp_path, {"precedence ": PRECEDENCE_OWNED})

    with pytest.raises(AirootError) as caught:
        load_selection_policy(path)
    assert "unknown keys" in caught.value.message


# --------------------------------------------------------------------------- #
# the policy actually decides the answer
# --------------------------------------------------------------------------- #


def _seed_a_healthy_reference(registry, root) -> None:
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/tools-fake-tool', ?, ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(Path(root.path) / "tools" / "fake-tool.bin")),
        )


def _run(registry, root, policy=None) -> dict:
    return where(
        registry,
        WhereQuery(capability_id=CAPABILITY),
        root=root.path,
        process_entries=[],
        machine_entries=[],
        user_entries=[],
        policy=policy,
    )


def test_steward_first_picks_the_reference_over_the_owned_payload(registry, clock, root) -> None:
    import fake_issuer
    from airoot.tx import create_plan
    from airoot.tx.simulate import SimulationRunner

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    _seed_a_healthy_reference(registry, root)

    document = _run(registry, root)

    assert document["management"] == "external_reference"
    assert document["selection_reason"] == "STEWARD_REFERENCE_HEALTHY"
    assert document["reason_code"] == "SUCCESS", "a healthy owned payload is not degraded, just outranked"
    assert any("precedence=steward" in item["detail"] for item in document["evidence"])


def test_owned_precedence_picks_the_owned_payload(registry, clock, root) -> None:
    """The explicit policy is the only way to invert the default, and it is visible in evidence."""

    import fake_issuer
    from airoot.tx import create_plan
    from airoot.tx.simulate import SimulationRunner

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    token = fake_issuer.issue(plan, clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, token)
    _seed_a_healthy_reference(registry, root)

    document = _run(registry, root, policy=SelectionPolicy(precedence=PRECEDENCE_OWNED, revision="sp-test"))

    assert document["management"] == "managed"
    assert document["selection_reason"] == "MACHINE_MANAGED_HEALTHY_BY_POLICY"
    assert any("precedence=owned" in item["detail"] for item in document["evidence"])


def test_an_unknown_version_never_satisfies_a_constraint(registry, root) -> None:
    """A reference whose version was never probed must not be reported as a version match."""

    _seed_a_healthy_reference(registry, root)

    document = where(
        registry,
        WhereQuery(capability_id=CAPABILITY, version=">=3.11"),
        root=root.path,
        process_entries=[],
        machine_entries=[],
        user_entries=[],
    )

    assert document["found"] is False
    assert document["reason_code"] == "VERSION_UNSATISFIED"
    assert any("cannot be proven to satisfy" in item["detail"] for item in document["candidates"][0]["evidence"])

