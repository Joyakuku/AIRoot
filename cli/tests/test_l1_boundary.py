"""L1: the capability boundary (draft §15, C-031…C-033).

"Omnipotent about capabilities, not about software" is only credible if the boundary is
machine-checkable. These tests hold the three admission conditions and, most importantly, the
drift guard between the two policy files: a whitelist predicate without a frozen capability
would silently widen what AIROOT may touch.
"""

from __future__ import annotations

import json
import re
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
from airoot.cli import build_parser, main
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

    assert frozen.revision == "cap-4"
    assert "python" in frozen.ids()
    # cap-3 (ADR-0047): `rust-toolchain` was frozen to close a gap — `policy/sources.json` had already
    # declared a trusted source for it (and §59 had verified resolution plus a real 12721664-byte
    # download against upstream) while no frozen capability named it, so `plan rust-toolchain` refused
    # with CAPABILITY_NOT_DECLARED. Pinned by name here so unfreezing it again is a red test rather
    # than a quietly narrower list.
    assert "rust-toolchain" in frozen.ids()
    assert all(item.kind in {"tool", "runtime"} for item in frozen.capabilities)
    assert all(item.entry for item in frozen.capabilities), "a capability without an entry is unusable"


#: Documents that show a *concrete* `--capability <id>` example. A placeholder (`<id>`) is not an
#: example; a literal id is a promise that this command runs.
CAPABILITY_EXAMPLE_DOCUMENTS = ("AGENTS.md", "SKILL.md", "agents/airoot.json", "cli/app/airoot/cli.py")

_CAPABILITY_EXAMPLE = re.compile(r"--capability[ \t\"']{1,3}([A-Za-z][A-Za-z0-9._-]*)")


def test_every_documented_capability_example_names_a_frozen_capability() -> None:
    """An example is a promise that the command runs (draft §69).

    AGENTS.md and the CLI's own evidence string both showed `--capability jq` — and `jq` is not in the
    frozen list. That was harmless while `adopt --mode import` skipped the boundary, and became a
    command that fails the moment the boundary was enforced. The example is the part a reader copies,
    so it is the part worth checking (the same rule §68 applied to caveats: they sit where the
    instruction is).
    """

    frozen = load_capabilities().ids()
    repo = Path(__file__).resolve().parents[2]
    checked = 0
    for name in CAPABILITY_EXAMPLE_DOCUMENTS:
        text = (repo / name).read_text(encoding="utf-8")
        for match in _CAPABILITY_EXAMPLE.finditer(text):
            checked += 1
            assert match.group(1) in frozen, (
                f"{name} shows `--capability {match.group(1)}`, which is not in the frozen list "
                f"{frozen}; the example would fail with CAPABILITY_NOT_DECLARED"
            )
    assert checked, "no concrete capability example was found; this guard is now about nothing"


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
# §169: the question is asked *about an object*, and a data root is not one
# --------------------------------------------------------------------------- #


@pytest.fixture
def registered_data_root(capsys, registry, tests_tmp: Path, request) -> Path:
    """A registered data root holding one real PE object (`python/python.exe`).

    `capability check` has to pass the owning data root into discovery; the only way to see whether it
    does is to ask about paths that are *inside* one, and about the root itself. Each test gets its
    own root because the registry — and therefore the registration — is per-test.
    """

    from test_l1_discovery import place_python_pe

    slug = re.sub(r"[^a-z0-9]+", "-", request.node.name.lower()).strip("-")[:40]
    path = tests_tmp / f"boundary-data-root-{slug}"
    place_python_pe(path / "python", "python.exe")
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "data-root", "add", str(path), "--id", f"dr-{slug}", "--role", "runtime",
    )
    assert code == 0, document
    return path


def test_capability_check_refuses_a_registered_data_root(
    capsys, registry, registered_data_root: Path
) -> None:
    """§169: a data root is a **scope**, and discovery already refuses to call it its own object.

    `capability check` used to call `classify_object` without passing the data root at all, which
    bypassed that refusal: `D:\\env` itself came back `adoptable` with `capability_id=ffmpeg`, and the
    remediation it printed (`airoot adopt D:\\env --mode reference`) is a command `adopt` refuses. The
    answer now comes from the same rule, quoted from the same place.
    """

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "capability", "check", str(registered_data_root),
    )

    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT"
    assert document.get("verdict") != "adoptable", "a scope is not one of its own objects"
    assert any("data root is a scope" in item for item in document["evidence"]), document["evidence"]
    assert any(str(registered_data_root) in item for item in document["evidence"]), document["evidence"]


def test_capability_check_still_adopts_a_directory_inside_a_data_root(
    capsys, registry, registered_data_root: Path
) -> None:
    """Non-vacuity for the refusal above: the object *inside* the root is unchanged."""

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "capability", "check", str(registered_data_root / "python"),
    )

    assert code == 0, document
    assert document["verdict"] == "adoptable"
    assert document["capability_id"] == "python"
    assert document["capability_source"] == "observed (external_reference)"


def test_capability_check_on_a_file_names_the_directory_that_can_be_adopted(
    capsys, registry, registered_data_root: Path
) -> None:
    """§169: a file is never the object, but "no capability matched" is not an answer either.

    `adopt --mode reference` registers a directory, so the only useful answer about a file names the
    directory that *could* be adopted and how it classifies. The verdict stays honest: the file is
    still `unmanaged` — nothing here claims the file is adoptable.
    """

    executable = registered_data_root / "python" / "python.exe"

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "capability", "check", str(executable),
    )

    assert code == 9, document
    assert document["verdict"] == "unmanaged"
    assert document["capability_id"] is None, "the file itself has no capability"
    assert any(
        str(registered_data_root / "python") in item and "capability_id=python" in item
        for item in document["evidence"]
    ), document["evidence"]
    assert any("adopt" in item for item in document["evidence"]), document["evidence"]


def test_capability_check_on_a_file_says_when_the_parent_is_not_adoptable_either(
    capsys, registry, registered_data_root: Path
) -> None:
    """The other half of the same answer: when the parent is not adoptable, say so rather than point
    at a directory that would be refused too."""

    directory = registered_data_root / "mystery"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "python.exe").write_bytes(b"MZ-not-a-pe-at-all\n")

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "capability", "check", str(directory / "python.exe"),
    )

    assert code == 9, document
    assert document["verdict"] == "unmanaged"
    assert any(
        str(directory) in item and "not an adoptable object either" in item
        for item in document["evidence"]
    ), document["evidence"]


def test_adopt_on_a_file_refuses_with_evidence(
    capsys, registry, registered_data_root: Path
) -> None:
    """§169: this `INVALID_INPUT` shipped with `evidence: []`, which is the same as no next step."""

    executable = registered_data_root / "python" / "python.exe"

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "adopt", str(executable), "--mode", "reference",
    )

    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT"
    assert document["evidence"], "an empty evidence list leaves the caller with nothing to do"
    assert any(str(registered_data_root / "python") in item for item in document["evidence"]), (
        document["evidence"]
    )


# --------------------------------------------------------------------------- #
# the boundary at every entry point (draft §91)
# --------------------------------------------------------------------------- #
#
# The frozen list is the boundary of what AIROOT may manage *at all*, and §91 measured that one of the
# eight commands that name a capability did not apply it: `tool pin` recorded an intent for a name no
# plan can ever satisfy, exited 0, and then blamed the **second** blocker ("no trusted source is
# declared for X") while the first one — that X is not a capability — stayed unsaid. A pin is *state*,
# so the same boundary the plan path applies belongs here.
#
# The set is derived from the parser, not listed: an eighth command arriving later has to be classified
# or this goes red, which is the shape §79/§81 used for verbs and lanes.


def capability_naming_commands() -> set[str]:
    """Every command path whose parser carries a capability id (positional or `--capability`)."""

    parser = build_parser()
    top = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ][0].choices

    found: set[str] = set()
    for verb, subparser in top.items():
        nested = [
            action
            for action in getattr(subparser, "_actions", [])
            if getattr(action, "choices", None) and action.dest == "subcommand"
        ]
        branches = [(verb, subparser)] + [
            (f"{verb} {sub}", child) for sub, child in (nested[0].choices.items() if nested else [])
        ]
        for path, branch in branches:
            if any(
                getattr(action, "dest", None) == "capability" for action in getattr(branch, "_actions", [])
            ):
                found.add(path)
    return found


#: Commands that **act** on the id: they write state or produce a plan, so the frozen boundary applies
#: and the refusal must use the code `plan` uses.
CAPABILITY_ACTING: dict[str, list[str]] = {
    "plan": ["plan", "{id}"],
    "tool pin": ["tool", "pin", "{id}", "--version", "1.0"],
    "scope decide": ["scope", "decide", "{id}"],
    "adopt": ["adopt", "{dir}", "--mode", "import", "--capability", "{id}", "--version", "1.0"],
}

#: Read-only queries. "Where is X" has a true answer for an unknown X — nowhere — so they answer
#: instead of refusing, and the test below measures that rather than asserting it.
CAPABILITY_QUERIES: dict[str, list[str]] = {
    "where": ["where", "{id}"],
    "tool list": ["tool", "list", "--capability", "{id}"],
}

#: Neither: `capability check` **is** the boundary (deciding adoptability is its job, and it already
#: answers `CAPABILITY_NOT_DECLARED` for a name that is not frozen); `source resolve` resolves a
#: download recipe rather than acting on a capability, and `sources.json` registers `rust-toolchain`
#: ahead of its freeze by ADR-0001, with its own verification note (draft §59).
#:
#: `run --capability <id>` (ADR-0050) joins them for a different reason: it **resolves an existing
#: binding** rather than deciding whether a name may become one, so the frozen boundary was already
#: applied by `plan`/`install` -- refusing at execution would let the ledger accept a payload that
#: the verb then declines to start. It is not a query either (it really runs something when a
#: binding exists); an unfrozen id answers `NOT_FOUND`, which `test_l1_launcher.py` measures.
CAPABILITY_EXEMPT = frozenset({"capability check", "source resolve", "run"})

UNFROZEN = "totally-not-a-frozen-capability"


def test_every_capability_naming_command_is_classified() -> None:
    """The denominator is derived, so a new command cannot quietly join the ungated group."""

    derived = capability_naming_commands()
    classified = set(CAPABILITY_ACTING) | set(CAPABILITY_QUERIES) | set(CAPABILITY_EXEMPT)

    assert len(derived) >= 6, f"only {len(derived)} capability-naming commands found; is the walk broken?"
    assert derived - classified == set(), (
        f"these commands name a capability and are not classified: "
        f"{sorted(derived - classified)}; a command that acts on an id must apply the frozen boundary"
    )
    assert classified - derived == set(), (
        f"these are classified but no longer name a capability: {sorted(classified - derived)}"
    )


@pytest.mark.parametrize("name", sorted(CAPABILITY_ACTING), ids=sorted(CAPABILITY_ACTING))
def test_every_command_that_acts_on_a_capability_applies_the_frozen_boundary(
    capsys, registry, tests_tmp: Path, name: str
) -> None:
    directory = tests_tmp / "frozen-gate"
    directory.mkdir(parents=True, exist_ok=True)
    argv = [part.format(id=UNFROZEN, dir=str(directory)) for part in CAPABILITY_ACTING[name]]

    code, document = run(capsys, "--json", "--root", str(Path(registry.path).parent.parent), *argv)

    assert code == 9, f"`airoot {' '.join(argv)}` did not refuse an unfrozen capability: exit {code}"
    assert document["reason_code"] == "CAPABILITY_NOT_DECLARED", document


@pytest.mark.parametrize("name", sorted(CAPABILITY_QUERIES), ids=sorted(CAPABILITY_QUERIES))
def test_a_query_answers_about_an_unfrozen_name_instead_of_refusing(
    capsys, registry, name: str
) -> None:
    argv = [part.format(id=UNFROZEN) for part in CAPABILITY_QUERIES[name]]

    code, document = run(capsys, "--json", "--root", str(Path(registry.path).parent.parent), *argv)

    assert code != 9 and document["reason_code"] != "CAPABILITY_NOT_DECLARED", (
        f"`airoot {' '.join(argv)}` is a read-only query, so `nowhere` is an answer and not a boundary "
        f"refusal; it returned exit {code} / {document['reason_code']}"
    )
    # `where` echoes the name it was asked about; `tool list` answers with a filtered set instead, so
    # the check is conditional rather than inventing a field for it.
    if "capability_id" in document:
        assert document["capability_id"] == UNFROZEN, "the query must answer about the name it was given"


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
