"""L1: the capability boundary (draft §15, C-031…C-033).

"Omnipotent about capabilities, not about software" is only credible if the boundary is
machine-checkable. These tests hold the three admission conditions and, most importantly, the
drift guard between the two policy files: a whitelist predicate without a frozen capability
would silently widen what AIROOT may touch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.boundary import (
    BINDING_SCOPES,
    CAPABILITIES_PATH,
    IRREVERSIBLE_EFFECTS,
    SIDE_EFFECTS,
    check_admission,
    check_whitelist_capabilities,
    load_capabilities,
)
from airoot.caps.discovery import load_whitelist
from airoot.cli import main
from airoot.exits import AirootError


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


# --------------------------------------------------------------------------- #
# the list itself
# --------------------------------------------------------------------------- #


def test_the_shipped_capability_list_is_valid_and_revisioned() -> None:
    frozen = load_capabilities()

    assert frozen.revision == "cap-1"
    assert "python" in frozen.ids()
    assert all(item.kind in {"tool", "runtime"} for item in frozen.capabilities)
    assert all(item.entry for item in frozen.capabilities), "a capability without an entry is unusable"


def test_every_whitelist_entry_names_a_frozen_capability() -> None:
    """The drift guard: recognition rules may never outrun the frozen boundary."""

    assert check_whitelist_capabilities(load_whitelist()) == []


def test_side_effect_ceiling_uses_the_published_enum() -> None:
    frozen = load_capabilities()

    for item in frozen.capabilities:
        assert set(item.side_effects) <= set(SIDE_EFFECTS)
        assert not (set(item.side_effects) & IRREVERSIBLE_EFFECTS), (
            f"{item.capability_id} would require an effect outside the boundary"
        )


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"schema_version": 1, "revision": "x", "capabilities": [], "typo": 1}))

    with pytest.raises(AirootError) as caught:
        load_capabilities(path)
    assert "unknown keys" in caught.value.message


def test_a_capability_requiring_an_irreversible_effect_cannot_be_frozen(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": "x",
                "capabilities": [
                    {
                        "capability_id": "sneaky",
                        "kind": "tool",
                        "entry": "sneaky.exe",
                        "side_effects": ["executes_scripts"],
                    }
                ],
            }
        )
    )

    with pytest.raises(AirootError) as caught:
        load_capabilities(path)
    assert "outside the boundary" in caught.value.message


def test_a_duplicate_capability_is_refused(tmp_path: Path) -> None:
    entry = {"capability_id": "dup", "kind": "tool", "entry": "dup.exe", "side_effects": ["none"]}
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps({"schema_version": 1, "revision": "x", "capabilities": [entry, dict(entry)]})
    )

    with pytest.raises(AirootError) as caught:
        load_capabilities(path)
    assert "declared twice" in caught.value.message


# --------------------------------------------------------------------------- #
# the three admission conditions
# --------------------------------------------------------------------------- #


def test_a_frozen_capability_with_verifiable_source_is_adoptable(tmp_path: Path) -> None:
    admission = check_admission(target=tmp_path / "python", capability_id="python")

    assert admission.verdict == "adoptable"
    assert all(admission.conditions.values())


def test_an_unknown_capability_is_only_reported(tmp_path: Path) -> None:
    """C-031: no capability -> unmanaged, plus the documented growth path."""

    admission = check_admission(target=tmp_path / "something", capability_id="proxy-tool")

    assert admission.verdict == "unmanaged"
    assert admission.conditions["has_frozen_capability"] is False
    assert "policy/capabilities.json" in (admission.remediation or "")
    assert admission.to_document()["reason_code"] == "CAPABILITY_NOT_DECLARED"


def test_an_unverifiable_source_can_only_be_referenced(tmp_path: Path) -> None:
    admission = check_admission(
        target=tmp_path / "python", capability_id="python", source_verifiable=False
    )

    assert admission.verdict == "reference_only"
    assert "never import or recreate" in (admission.remediation or "")


def test_an_irreversible_requirement_is_excluded(tmp_path: Path) -> None:
    admission = check_admission(
        target=tmp_path / "python", capability_id="python", requires_irreversible_effect=True
    )

    assert admission.verdict == "excluded"
    assert admission.conditions["no_irreversible_effect_required"] is False


def test_missing_capability_list_is_not_silently_ignored(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as caught:
        load_capabilities(tmp_path / "absent.json")
    assert caught.value.reason_code == "CAPABILITY_NOT_DECLARED"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_capability_list(capsys, registry) -> None:
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent), "capability", "list"
    )

    assert code == 0
    assert document["count"] >= 6
    assert any(item["capability_id"] == "java" for item in document["capabilities"])


def test_cli_capability_check_detects_then_decides(capsys, registry, tests_tmp: Path) -> None:
    """C-032: a name that looks right but whose evidence does not match is not adopted."""

    from test_l1_discovery import place_python_pe

    object_root = place_python_pe(tests_tmp / "capability-check" / "python", "python.exe").parent

    code, document = run(
        capsys,
        "--json",
        "--root",
        str(Path(registry.path).parent.parent),
        "capability",
        "check",
        str(object_root),
    )

    assert code == 0, document
    assert document["verdict"] == "adoptable"
    assert document["capability_id"] == "python"
    assert "observed" in document["capability_source"]
    assert document["managed_files_touched"] == 0


def test_cli_capability_check_rejects_a_lookalike(capsys, registry, tests_tmp: Path) -> None:
    directory = tests_tmp / "capability-lookalike"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "python.exe").write_bytes(b"MZ-not-a-pe-at-all\n")

    code, document = run(
        capsys,
        "--json",
        "--root",
        str(Path(registry.path).parent.parent),
        "capability",
        "check",
        str(directory),
    )

    assert code == 9
    assert document["verdict"] == "unmanaged"
    assert document["reason_code"] == "CAPABILITY_NOT_DECLARED"


# --------------------------------------------------------------------------- #
# the scope vocabulary of the frozen list (draft §47)
#
# `kind` and `side_effects` were validated at load; `scope` was not — it was read with
# `tuple(str(value) for value in item.get("scope", []))`, which turns a bare string into one entry
# per character. Nothing consumes the declared scope yet (draft §47.5), so a corruption here would
# surface only in `capability list`'s agent-facing answer.
# --------------------------------------------------------------------------- #


def frozen_with(tmp_path: Path, **overrides) -> Path:
    entry = {
        "capability_id": "thing",
        "kind": "tool",
        "entry": "thing.exe",
        "side_effects": ["none"],
        "scope": ["machine"],
    }
    entry.update(overrides)
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps({"schema_version": 1, "revision": "x", "capabilities": [entry]}), encoding="utf-8"
    )
    return path


def test_an_unknown_scope_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AirootError) as caught:
        load_capabilities(frozen_with(tmp_path, scope=["projet"]))
    assert "unknown scopes" in caught.value.message
    assert "known scopes" in caught.value.evidence[0]


@pytest.mark.parametrize("scope", ["machine", ["machine", 7], {"machine": True}], ids=["bare-string", "non-string", "not-a-list"])
def test_a_scope_that_is_not_a_list_of_names_is_refused(tmp_path: Path, scope) -> None:
    """A bare string is the interesting case: iterating it yields one entry per character."""

    with pytest.raises(AirootError) as caught:
        load_capabilities(frozen_with(tmp_path, scope=scope))
    assert "list of names" in caught.value.message


def test_every_shipped_scope_is_a_real_binding_scope() -> None:
    """The dangerous direction: a scope the machine can never be in is not a scope."""

    frozen = load_capabilities()
    declared = {scope for item in frozen.capabilities for scope in item.scope}
    assert declared, "the frozen list declares no scope at all"
    assert declared <= set(BINDING_SCOPES)


def test_the_declared_scope_is_a_declaration_and_not_an_enforcement(monkeypatch) -> None:
    """ADR-0021 answered §47.5's open question by **not** enforcing the declared scope.

    §47.5 recorded the question ("may a capability that never declares `machine` still be bound at
    machine level?") without an answer, because enforcing it would change admission and routing
    semantics. Under the standing 放宽-first policy the answer is no enforcement: the declaration
    stays a declaration. That is exactly the kind of fact that rots silently, so this rewrites every
    capability's scope to the minimal legal list and asserts nothing observable moves. Adding
    enforcement later therefore has to be a decision (and an ADR), not an accident.

    Strength, stated honestly: the admission half is a genuine comparison. The routing half is
    weaker than it looks — `decide_scope` answers from the discovery whitelist's `kind` first and
    only falls back to the frozen list, so for a capability that has a whitelist entry the frozen
    `scope` is not consulted on either path. This test does **not** yet pin "no module reads the
    declared scope at all" (a source census); admission invariance is the part that is really
    checked. See ADR-0021's "not done" list.
    """

    from dataclasses import replace

    from airoot.caps import boundary
    from airoot.caps.planner import ScopeRequest, decide_scope

    frozen = load_capabilities()
    # `session` is in the binding vocabulary, so the altered list is legal — just different.
    altered = replace(
        frozen,
        capabilities=tuple(replace(capability, scope=("session",)) for capability in frozen.capabilities),
    )
    assert altered != frozen, "the probe did not actually change any declared scope"

    target = Path("D:/not-a-real-object")
    before_routing = {
        capability.capability_id: decide_scope(ScopeRequest(capability_id=capability.capability_id)).scope
        for capability in frozen.capabilities
    }

    monkeypatch.setattr(boundary, "load_capabilities", lambda *args, **kwargs: altered)
    for capability in frozen.capabilities:
        original = check_admission(target=target, capability_id=capability.capability_id, capabilities=frozen)
        rewritten = check_admission(target=target, capability_id=capability.capability_id, capabilities=altered)
        assert original.verdict == rewritten.verdict, (
            f"{capability.capability_id}: admission moved when only its declared scope changed"
        )
        after = decide_scope(ScopeRequest(capability_id=capability.capability_id)).scope
        assert after == before_routing[capability.capability_id], (
            f"{capability.capability_id}: routing moved when only its declared scope changed"
        )
