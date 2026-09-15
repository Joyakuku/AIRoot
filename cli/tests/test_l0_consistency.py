"""L0: cross-artifact consistency — the audit, made permanent.

Every stage of this project added a contract, a code path, a policy file and a paragraph of prose.
Each of those was verified in isolation; **nothing checked the four against each other**. That is
exactly how a project ends up with a reason code in the docs that the code never emits, a module
nobody documented, or a schema filename that no longer exists.

These tests are deliberately cheap and structural — no behaviour, just "do the artifacts agree".
When one fails it names the artifact and the disagreement, so the fix is mechanical.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest

from airoot import SCHEMA_DIR
from airoot.caps.doctor import DIAGNOSTIC_CODES, INVARIANTS
from airoot.caps.exposure import PERSIST_SCOPES
from airoot.caps.inventory import SCOPES as BINDING_SCOPES
from airoot.caps.planner import SCOPE_DATA_ROOT, SCOPE_PROJECT
from airoot.cli import EXEC_ALIAS_FLAG, build_parser
from airoot.exits import EXIT_MEANINGS, REASON_EXIT
from airoot.schema_io import load_schema, schema_names

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "cli" / "app" / "airoot"
AGENTS = REPO / "AGENTS.md"
REASON_TABLE = REPO / "docs" / "AIROOT-v0.3-诊断码与ReasonCode表.md"
SCHEMA_README = REPO / "docs" / "schema" / "README.md"
SKILL = REPO / "SKILL.md"
AGENT_META = REPO / "agents" / "airoot.json"
DRAFT = REPO / "docs" / "AIROOT-v0.3-管家模型与数据根契约草案.md"
PROTOCOL = REPO / "docs" / "AIROOT-搜索能力与工具集成协议方案.md"
REVIEW = REPO / "docs" / "AIROOT-v0.3-规范审查报告.md"
GOLDEN = REPO / "cli" / "tests" / "fixtures" / "golden"


def test_the_cli_schema_count_matches_every_document_that_states_it() -> None:
    actual = len(schema_names())
    assert actual == 19, "the published schema set changed; update the docs and this test together"

    # Only documents that *state* a count are checked (SKILL.md deliberately states none).
    # A line may legitimately describe the transition ("18 → 19") or be marked superseded; what it
    # may not do is present 18 as the current value on a line that never mentions 19.
    #
    # The number has to be a real standalone count, not a `§17.6` section reference, a
    # `draft 2020-12` date or an `ADR-0003` identifier — hence the delimiter guards.
    standalone = re.compile(r"(?<![\w§.\-/])(\d{2,3})(?![\w.\-/])")
    checked = 0
    for path in (AGENTS, SCHEMA_README, DRAFT, REVIEW):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "schema_count" not in line and "JSON Schema" not in line:
                continue
            numbers = {int(value) for value in standalone.findall(line)}
            if not numbers:
                continue
            checked += 1
            assert actual in numbers or "取代" in line or "原提案" in line, (
                f"{path.name}: a schema-count line states {sorted(numbers)} "
                f"and never the current {actual}: {line.strip()[:120]}"
            )
    assert checked, "no document states the schema count at all"


def test_every_schema_file_is_referenced_somewhere_outside_itself() -> None:
    """A published schema nobody mentions is either unused or undocumented."""

    documents = [
        AGENTS,
        SCHEMA_README,
        DRAFT,
        REPO / "docs" / "AIROOT-v0.3-实现决策记录.md",
    ]
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in documents)
    unreferenced = sorted(name for name in schema_names() if name not in corpus)
    assert unreferenced == [], f"schemas never mentioned in the docs: {unreferenced}"


def test_every_reason_code_the_app_can_raise_is_registered() -> None:
    """The bug this exists for: a new code that `exit_code_for` has never heard of.

    `AirootError` fails fast on an unregistered code, so the failure surfaces at runtime — but
    only if the path is exercised. A static scan catches it before that.
    """

    registered = set(REASON_EXIT)
    pattern = re.compile(r'(?:AirootError\(\s*|"reason_code":\s*)"([A-Z][A-Z0-9_]{2,63})"')
    unknown: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        found = {code for code in pattern.findall(path.read_text(encoding="utf-8")) if code not in registered}
        if found:
            unknown[str(path.relative_to(REPO))] = found
    assert unknown == {}, f"unregistered reason codes: {unknown}"


def test_the_reason_code_table_documents_every_registered_code() -> None:
    """The authoritative mapping is the code; the table must not lag behind it."""

    text = REASON_TABLE.read_text(encoding="utf-8")
    missing = sorted(code for code in REASON_EXIT if code not in text)
    assert missing == [], f"registered but undocumented reason codes: {missing}"


def test_the_reason_code_table_invents_nothing() -> None:
    text = REASON_TABLE.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`([A-Z][A-Z0-9_]{2,63})`", text))
    unknown = sorted(code for code in mentioned if code not in REASON_EXIT and code not in DIAGNOSTIC_CODES)
    # A name that is a real symbol in `exits` is prose (`REASON_EXIT`, `EXIT_MEANINGS`), not a code;
    # and a trailing underscore is a family shorthand (`PERSISTENCE_*`).
    import airoot.exits as exits_module

    suspicious = sorted(
        code
        for code in unknown
        if "_" in code and not code.endswith("_") and not hasattr(exits_module, code)
    )
    assert suspicious == [], f"the reason-code table names codes that do not exist: {suspicious}"


def test_every_diagnostic_code_is_a_registered_reason_code() -> None:
    for code in DIAGNOSTIC_CODES:
        assert code in REASON_EXIT, f"diagnostic {code} has no exit-code mapping"


def test_the_invariant_catalogue_is_exactly_d1_to_d10() -> None:
    assert set(INVARIANTS) == {f"D{i}" for i in range(1, 11)}


def test_every_exit_code_has_a_documented_meaning() -> None:
    assert set(EXIT_MEANINGS) == {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}
    assert set(REASON_EXIT.values()) <= set(EXIT_MEANINGS)


def test_the_reason_code_table_and_the_code_agree_on_exit_codes() -> None:
    """Each code must be listed on the row of the exit code it actually maps to."""

    text = REASON_TABLE.read_text(encoding="utf-8")
    mismatched: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\|\s*(\d)\s*\|[^|]*\|(.*)\|\s*$", line)
        if not match:
            continue
        documented_exit = int(match.group(1))
        for code in re.findall(r"`([A-Z][A-Z0-9_]{2,63})`", match.group(2)):
            if code in REASON_EXIT and REASON_EXIT[code] != documented_exit:
                mismatched.append(f"{code}: table says {documented_exit}, code says {REASON_EXIT[code]}")
    assert mismatched == [], mismatched


def test_every_caps_module_is_listed_in_the_repo_map() -> None:
    """A module nobody documented is a module the next contributor will not find."""

    agents_text = AGENTS.read_text(encoding="utf-8")
    undocumented = sorted(
        path.name
        for path in (APP / "caps").glob("*.py")
        if path.name != "__init__.py" and path.name not in agents_text
    )
    assert undocumented == [], f"caps modules missing from the AGENTS.md repo map: {undocumented}"


def test_every_policy_file_is_listed_in_the_repo_map() -> None:
    agents_text = AGENTS.read_text(encoding="utf-8")
    undocumented = sorted(
        path.name for path in (APP / "policy").glob("*.json") if path.name not in agents_text
    )
    assert undocumented == [], f"policy files missing from the AGENTS.md repo map: {undocumented}"


def implemented_commands() -> set[str]:
    """Every command *path* the CLI really has.

    Path granularity, not verb granularity: `tool retire` exists, so a bare `tool` would look
    implemented while `tool pin` did not. A command with no subcommands contributes its bare name.
    """

    parser = build_parser()
    top = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ][0].choices
    paths: set[str] = set()
    for name, subparser in top.items():
        nested = [
            action
            for action in getattr(subparser, "_actions", [])
            if getattr(action, "choices", None) and action.dest == "subcommand"
        ]
        if not nested:
            paths.add(name)
        else:
            paths |= {f"{name} {sub}" for sub in nested[0].choices}
    # The bare verb counts too: a declared-unimplemented `search` must be caught even if the command
    # ever grows subcommands.
    return paths | set(top)


def planning_15_1_commands() -> set[str]:
    """The frozen plan/plan table §15.1 — the CLI surface as designed, not as implemented."""

    planning = (REPO / "docs" / "AIROOT-总体方案规划-v0.3.md").read_text(encoding="utf-8")
    section = planning.split("### 15.1", 1)[1].split("### 15.2", 1)[0]
    listed: set[str] = set()
    for line in section.splitlines():
        match = re.match(r"^airoot\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", line)
        if match:
            verb, sub = match.group(1), match.group(2)
            listed.add(f"{verb} {sub}" if sub and not sub.startswith("<") else verb)
    return listed


def test_every_policy_revision_is_named_in_the_repo_map() -> None:
    """A policy revision identifier that nobody can read is not a revision identifier.

    The whitelist revision is cited in prose all over the docs, but `sp-1` (`selection-policy.json`)
    was not written down anywhere — the one policy whose precedence rule changes `where` behaviour.
    """

    agents_text = AGENTS.read_text(encoding="utf-8")
    missing: list[str] = []
    for path in sorted((APP / "policy").glob("*.json")):
        revision = json.loads(path.read_text(encoding="utf-8")).get("revision")
        if revision and revision not in agents_text:
            missing.append(f"{path.name}:{revision}")
    assert missing == [], f"policy revisions missing from the AGENTS.md repo map: {missing}"


def test_the_frozen_command_list_is_either_implemented_or_declared_unimplemented() -> None:
    """§15.1 lists the CLI surface; every entry must be one of the two, never neither.

    This is the check that would have caught `rebuild` being referenced by `doctor`'s remediation
    while not existing at all.
    """

    implemented = implemented_commands()
    declared = set(json.loads(AGENT_META.read_text(encoding="utf-8"))["not_implemented"])
    neither = sorted(
        entry
        for entry in planning_15_1_commands()
        if entry not in implemented and entry not in declared and entry.split()[0] not in declared
    )
    assert neither == [], f"frozen commands that are neither implemented nor declared missing: {neither}"


def test_every_implemented_command_is_named_in_the_documentation() -> None:
    """The reverse direction: the CLI must not grow a verb nobody wrote down.

    §15.1 is deliberately *not* the whole surface — the steward chapters add `data-root`, `scope`,
    `source`, `capability`, `extension`… — so the requirement is weaker but still real: every
    implemented command path must be named somewhere an author would actually look.
    """

    documents = [
        REPO / "docs" / "AIROOT-总体方案规划-v0.3.md",
        DRAFT,
        REPO / "docs" / "AIROOT-v0.3-实现决策记录.md",
        REPO / "docs" / "AIROOT-v0.3-三大核心契约方案.md",
        SKILL,
        AGENTS,
        AGENT_META,
    ] + sorted((REPO / "references").glob("*.md"))
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in documents)

    undocumented = sorted(
        entry
        for entry in implemented_commands()
        if not re.search(r"(?:airoot\s+)?" + re.escape(entry) + r"\b", corpus)
    )
    assert undocumented == [], f"implemented commands nobody documented: {undocumented}"


def test_every_declared_unimplemented_command_is_still_unimplemented() -> None:
    document = json.loads(AGENT_META.read_text(encoding="utf-8"))
    implemented = implemented_commands()
    stale = sorted(entry for entry in document["not_implemented"] if entry in implemented)
    assert stale == [], f"these are implemented now and must be documented: {stale}"


def test_every_reason_code_the_search_protocol_lists_is_registered() -> None:
    """The protocol document is a specification, not a wish list: its code families must exist.

    `SEARCH_*` and the generic `EXTENSION_*` family were named in the protocol long before any of
    them was registered (draft §31 registered them); this is the guard that keeps that from
    drifting apart again.
    """

    text = PROTOCOL.read_text(encoding="utf-8")
    section = text.split("### 6.6", 1)[1].split("### 七", 1)[0]
    listed = set(re.findall(r"\b((?:SEARCH|EXTENSION)_[A-Z_]+)\b", section))

    assert listed, "the protocol's error-code section names no codes"
    assert sorted(code for code in listed if code not in REASON_EXIT) == []


def test_the_search_subcommands_the_protocol_reserves_are_the_ones_we_reserve() -> None:
    """`status`/`refresh`/`rebuild`/`implementations`/`explain` — reserved words, never queries."""

    from airoot.cli import SEARCH_RESERVED

    text = PROTOCOL.read_text(encoding="utf-8")
    listed: set[str] = set()
    for heading, following in (
        ("### 索引管理", "### 解释后端选择"),
        ("### 解释后端选择", "### Agent 场景"),
    ):
        assert heading in text and following in text, f"the protocol lost a section: {heading}"
        section = text.split(heading, 1)[1].split(following, 1)[0]
        listed |= set(re.findall(r"airoot search ([a-z]+)", section))

    assert listed == {"explain", "implementations", "rebuild", "refresh", "status"}, listed
    assert listed <= set(SEARCH_RESERVED), "the code reserves fewer words than the protocol does"


#: Diagnostic codes that are declared but cannot be emitted yet, and why. `doctor` must not promise
#: a diagnosis it cannot produce, so this is an explicit, documented exception list rather than a
#: silent gap — every entry also has to be named as pending in `AGENTS.md`.
RESERVED_DIAGNOSTICS = {
    "DATA_ROOT_ACL_DRIFT": "needs the P2 ACL baseline (AGENTS §1 lists it under 尚未实现)",
}


def _commands_in(text: str) -> set[tuple[str, str | None]]:
    """Every `airoot <verb> [<sub>]` mention in a document."""

    return {
        (match.group(1), match.group(2))
        for match in re.finditer(r"airoot\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", text)
    }


def strict_command_paths() -> set[str]:
    """Implemented paths only — a verb with subcommands does **not** count as itself.

    `implemented_commands()` deliberately includes bare verbs as well; for this guard that would be
    too generous (`tool nonsense` would pass because `tool` exists), while excluding bare verbs
    altogether would flag `plan build`, where `build` is a *capability argument*, not a subcommand.
    The rule below gets both right: `entry` must be a real path, or its verb must be a command that
    takes no subcommands at all.
    """

    parser = build_parser()
    top = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ][0].choices
    paths: set[str] = set()
    for name, subparser in top.items():
        nested = [
            action
            for action in getattr(subparser, "_actions", [])
            if getattr(action, "choices", None) and action.dest == "subcommand"
        ]
        if nested:
            paths |= {f"{name} {sub}" for sub in nested[0].choices}
        else:
            paths.add(name)
    return paths


def test_every_command_the_docs_tell_an_agent_to_run_exists() -> None:
    """The Skill has had this guard for a while; `AGENTS.md` — the onboarding document — did not.

    A document that tells an agent to run `airoot something` that does not exist is worse than a
    missing document: the agent will report a failure that never happened.

    Scope is the *operational* documents (AGENTS.md, `references/*.md`). The planning document and
    the contract draft are proposals: naming a command that does not exist yet is what a roadmap
    does, and the frozen-list test already covers that direction. Anywhere a proposal's spelling
    differs from the shipped one, the draft says so in place (§8 records the `tool retire` case).
    """

    declared = set(json.loads(AGENT_META.read_text(encoding="utf-8"))["not_implemented"])
    paths = strict_command_paths()
    offenders: list[str] = []
    for path in (AGENTS, *sorted((REPO / "references").glob("*.md"))):
        for verb, sub in sorted(
            _commands_in(path.read_text(encoding="utf-8")), key=lambda item: (item[0], item[1] or "")
        ):
            entry = f"{verb} {sub}" if sub else verb
            if entry in paths or verb in paths or entry in declared or verb in declared:
                continue
            offenders.append(f"{path.name}: airoot {entry}")

    assert offenders == [], f"documents tell an agent to run commands that do not exist: {offenders}"


def test_the_agent_facing_reason_code_reference_names_every_registered_code() -> None:
    """`references/reason-codes.md` is what an agent reads *before* the authoritative table.

    It had drifted badly: 41 of 93 codes were missing, including almost the whole `SEARCH_*` family —
    the codes an agent actually sees when it runs `search`. A quick reference that omits the codes
    you will hit is worse than no quick reference, because the agent invents an explanation instead.
    """

    text = (REPO / "references" / "reason-codes.md").read_text(encoding="utf-8")
    missing = sorted(code for code in REASON_EXIT if code not in text)

    assert missing == [], f"codes an agent can see but cannot look up: {missing}"


#: Modules that touch **user data** (registered data roots) and therefore must survive a path longer
#: than `MAX_PATH`: they go through the shared `paths.extended_path` (draft §38). AIROOT-root-internal
#: scans (`store/`, `state/`, `logs/`) are deliberately out of scope — their depth is bounded by the
#: root's own path plus a few short segments, and forcing the prefix there would churn every writer
#: for no evidence. The point of this list is that a *new* user-data walk cannot quietly skip it.
DATA_PATH_USERS = ("caps/search.py", "caps/discovery.py", "caps/probe_pe.py")


def test_every_user_data_walk_uses_the_shared_extended_path_helper() -> None:
    """The prefix logic lives in exactly one place; a second copy is how walkers drift apart.

    Only two things are asserted, because anything cleverer would be brittle: the module *uses* the
    helper, and it does not carry its own copy of the constant. (Scanning for hand-rolled `\\\\?\\`
    literals was tried and dropped: docstrings legitimately mention the prefix in prose.)
    """

    missing: list[str] = []
    for relative in DATA_PATH_USERS:
        source = (APP / relative).read_text(encoding="utf-8")
        if "extended_path" not in source:
            missing.append(relative)
        if "EXTENDED_PREFIX =" in source:
            missing.append(f"{relative} (defines its own prefix)")
    assert missing == [], f"these modules must use paths.extended_path: {missing}"

    helper = (APP.parent / "airoot" / "paths.py").read_text(encoding="utf-8")
    assert "def extended_path" in helper and "def native_path" in helper


def test_no_diagnostic_code_is_promised_without_a_path_that_can_emit_it() -> None:
    """A code in `INVARIANTS` is a promise that `doctor` can report it.

    The check is "can anything produce this code", not "does doctor.py contain this literal":
    `_check_registry` deliberately forwards `error.reason_code` from the registry layer, so
    `REGISTRY_MISSING` is emitted without ever appearing as a literal in doctor.py.

    `DATA_ROOT_ACL_DRIFT` has been in the catalogue since the steward draft and still cannot be
    emitted (it needs the P2 ACL baseline). That is fine — as long as it is an *exception with a
    reason that is written down*, not an unnoticed promise.
    """

    doctor_source = (APP / "caps" / "doctor.py").read_text(encoding="utf-8")
    after_catalogue = doctor_source.split("DIAGNOSTIC_CODES = frozenset", 1)[1]
    others = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(APP.rglob("*.py"))
        if path.name not in {"exits.py", "doctor.py"}
    )
    unproducible = sorted(
        code
        for code in DIAGNOSTIC_CODES
        if f'"{code}"' not in after_catalogue and f'"{code}"' not in others
    )
    assert unproducible == sorted(RESERVED_DIAGNOSTICS), (
        "every diagnostic code must either have a way to be produced or be a documented exception; "
        f"unproducible={unproducible} documented={sorted(RESERVED_DIAGNOSTICS)}"
    )
    agents_text = AGENTS.read_text(encoding="utf-8")
    for code, reason in RESERVED_DIAGNOSTICS.items():
        assert reason, f"{code} needs a written reason"
        assert code in agents_text, f"{code} is reserved but not named as pending in AGENTS.md"


def test_the_skill_and_the_machine_readable_list_agree_on_what_is_missing() -> None:
    """Two agent-facing declarations of "not implemented yet" that drift apart are a trap."""

    declared = set(json.loads(AGENT_META.read_text(encoding="utf-8"))["not_implemented"])
    block = SKILL.read_text(encoding="utf-8").split("## 未实现的命令", 1)[1].split("```text", 1)[1]
    block = block.split("```", 1)[0]

    listed: set[str] = set()
    for line in block.splitlines():
        match = re.match(r"\s*airoot\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*(?:\|[a-z][a-z-]*)*))?", line)
        if not match:
            continue
        verb, alternatives = match.group(1), match.group(2)
        if alternatives:
            listed |= {f"{verb} {item}" for item in alternatives.split("|") if item}
        else:
            listed.add(verb)

    assert listed == declared, (
        f"SKILL.md's unimplemented block {sorted(listed)} and agents/airoot.json {sorted(declared)} disagree"
    )


def test_the_honesty_prohibitions_are_still_in_both_documents() -> None:
    """The forbidden claims are contract text, so their absence must be a test failure, not a diff."""

    for path in (AGENTS, SKILL):
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if "Everything 级性能" in line]
        assert lines, f"{path.name} no longer states the Everything-class prohibition"
        assert any(re.search(r"不|绝不|never", line) for line in lines), (
            f"{path.name} mentions Everything-class performance without forbidding the claim"
        )


def test_every_golden_fixture_is_known_to_the_golden_test() -> None:
    """An orphan fixture is either a lost acceptance case or a file nobody checks."""

    from test_golden import SCHEMA_FOR_FIXTURE

    # Fixtures with no published schema are named here instead; each one has a hand-written shape
    # assertion somewhere (`discover_report` and `transaction_transitions` in the L1 suites,
    # `index`/`reason_code_table` by equality in `test_golden.py`).
    known = set(SCHEMA_FOR_FIXTURE) | {
        "index",
        "reason_code_table",
        "discover_report",
        "transaction_transitions",
        "invariant_catalogue",
        "frozen_capabilities",
        "scenario_ledger",
        "execution_bounds",
    }
    fixtures = {path.stem for path in (REPO / "cli" / "tests" / "fixtures" / "golden").glob("*.json")}
    orphans = sorted(fixtures - known)
    assert orphans == [], f"golden fixtures no test references: {orphans}"


def test_the_documented_test_count_is_the_same_everywhere() -> None:
    """Prose counts drift silently; they must at least be internally consistent.

    `AGENTS.md` is the current-state document, so every total it states must be the *same* number.
    The contract draft's totals are per-stage historical records (§17.5, §18.6, …): they are
    allowed to differ, but none may **exceed** the current total — a historical record cannot have
    been written against a bigger tree than exists now.
    """

    def totals(path: Path) -> list[int]:
        return [int(value) for value in re.findall(r"(\d{3})\s*项", path.read_text(encoding="utf-8"))]

    current = set(totals(AGENTS))
    assert len(current) == 1, f"AGENTS.md states more than one current test total: {sorted(current)}"
    (now,) = current
    for path in (AGENTS, DRAFT):
        ahead = sorted(value for value in set(totals(path)) if value > now)
        assert ahead == [], f"{path.name} claims {ahead} tests, more than the current {now}"

    # The review report's appended section describes *current* status (it says so), unlike the draft's
    # per-stage history — so its total must be the current one, not merely not-ahead-of it.
    review = set(totals(REVIEW))
    assert review == {now}, f"{REVIEW.name} states {sorted(review)} tests; the current total is {now}"


def test_selection_policy_values_match_the_code_constants() -> None:
    from airoot.caps.selection import PRECEDENCES, load_selection_policy

    policy = load_selection_policy()
    assert policy.precedence in PRECEDENCES
    assert policy.revision

    raw = json.loads((APP / "policy" / "selection-policy.json").read_text(encoding="utf-8"))
    assert set(raw) <= {"schema_version", "revision", "precedence", "notes"}


def test_the_whitelist_and_the_frozen_capability_list_agree() -> None:
    from airoot.caps.boundary import check_whitelist_capabilities, load_capabilities
    from airoot.caps.discovery import load_whitelist

    assert check_whitelist_capabilities(load_whitelist()) == []
    frozen = load_capabilities()
    whitelist = load_whitelist()
    # Every *discoverable* capability must be frozen; the reverse is allowed (a capability AIROOT
    # installs itself has no discovery predicate).
    missing = sorted(
        entry.capability_id for entry in whitelist.entries if frozen.by_id(entry.capability_id) is None
    )
    assert missing == []


def test_the_source_catalog_never_stores_a_digest() -> None:
    raw = (APP / "policy" / "sources.json").read_text(encoding="utf-8")
    assert "sha256:" not in raw


def test_the_skill_states_the_same_test_count_as_the_repo() -> None:
    """SKILL.md is what an agent reads; a stale number there is an agent-facing lie."""

    agents_text = AGENTS.read_text(encoding="utf-8")
    skill_text = SKILL.read_text(encoding="utf-8")
    agents_counts = set(re.findall(r"(\d{3})\s*项测试", agents_text))
    skill_counts = set(re.findall(r"(\d{3})\s*项测试", skill_text))
    assert not skill_counts or skill_counts == agents_counts, (
        f"SKILL.md says {skill_counts}, AGENTS.md says {agents_counts}"
    )


@pytest.mark.parametrize("name", ["common.schema.json", "plan.schema.json", "transaction.schema.json"])
def test_core_schemas_still_parse_and_carry_an_id(name: str) -> None:
    document = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    assert document["$id"].startswith("https://airoot.local/schema/")
    assert document["$schema"].endswith("2020-12/schema")


# --- Guard group 7: document structure and citation resolution (draft §39) ---------------------
#
# Section numbers in these documents are a *reference surface*, not decoration: AGENTS.md, the
# draft, the ADRs and the diagnostic table all cite "协议 §6.6", "规划 §3.2:174" and so on. A
# subsection that sits under the wrong `##` — or a renumbering that leaves a citation dangling —
# turns those citations into lies, and no other check in this file would notice.
#
# Two heading conventions exist and both must parse:
#   * `## 12.` — arabic; the draft and the master plan.
#   * `## 五、` — Chinese numerals; the three-contract doc, the protocol doc and the validation
#     plan. `## 五、` legitimately owns `### 5.1`, which is exactly why a scan that only understands
#     arabic H2s reports three healthy documents as broken.
# `### 8.1.1` is a three-level number whose owning section is still `8`; counting it as a sibling of
# `### 8.1` is how a naive scan invents a duplicate and then demands a pointless rewrite.
#
# Scope, deliberately narrow: only *numbered* `###` headings carry section identity. These documents
# also use unnumbered list headings (`### 1. 搜索目标…`) and named ones (`### v1 支持`); both are fine.

_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_H2_ARABIC = re.compile(r"^## (\d+)[.、]")
_H2_CHINESE = re.compile(r"^## ([一二三四五六七八九十]+)、")
_H3_NUMBERED = re.compile(r"^### (\d+)\.(\d+)(?:\.(\d+))?(?:\s|$)")
#: A citation of the search protocol by section, e.g. `协议 §5.3/§7`. The `\s*` deliberately spans a
#: line break, because the ADR wraps it as "搜索协议\n§5.1". A bare `§5.1` is *not* matched: without
#: the document name it is ambiguous, since the draft, the master plan and the ADRs number their own.
_PROTOCOL_CITATION = re.compile(r"协议\s*(§[\d.]+(?:[/、]\s*§[\d.]+)*)")

#: Every document whose headings and cross-references are part of the agent-facing surface.
STRUCTURED_DOCUMENTS = (
    sorted((REPO / "docs").rglob("*.md"))
    + sorted((REPO / "references").rglob("*.md"))
    + [AGENTS, SKILL]
)


def _chinese_number(text: str) -> int:
    """Parse the Chinese numerals this documentation actually uses (1–99)."""

    if "十" not in text:
        return _CHINESE_DIGITS[text]
    tens, _, ones = text.partition("十")
    return (_CHINESE_DIGITS[tens] if tens else 1) * 10 + (_CHINESE_DIGITS[ones] if ones else 0)


def _document_headings(path: Path) -> tuple[set[int], list[tuple[int, int, str | None, int, int | None]]]:
    """Return (top-level section numbers, [(declared, major, minor, line, enclosing_section)])."""

    tops: set[int] = set()
    entries: list[tuple[int, int, str | None, int, int | None]] = []
    enclosing: int | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        arabic, chinese = _H2_ARABIC.match(line), _H2_CHINESE.match(line)
        if arabic:
            enclosing = int(arabic.group(1))
            tops.add(enclosing)
            continue
        if chinese:
            enclosing = _chinese_number(chinese.group(1))
            tops.add(enclosing)
            continue
        heading = _H3_NUMBERED.match(line)
        if heading:
            entries.append(
                (int(heading.group(1)), int(heading.group(2)), heading.group(3), number, enclosing)
            )
    return tops, entries


def test_every_numbered_subsection_sits_under_its_own_section() -> None:
    """`### 36.6` under `## 38` is not a typo — it is a broken reference (draft §39.1)."""

    problems: list[str] = []
    for path in STRUCTURED_DOCUMENTS:
        _tops, entries = _document_headings(path)
        order: dict[int, list[int]] = {}
        parents: set[tuple[int, int]] = set()
        for declared, major, minor, line, enclosing in entries:
            if enclosing != declared:
                problems.append(
                    f"{path.name}:{line}: '### {declared}.{major}' sits under ## {enclosing}"
                )
            if minor is None:
                order.setdefault(declared, []).append(major)
            else:
                parents.add((declared, major))
        for declared, majors in sorted(order.items()):
            if majors != sorted(majors) or len(majors) != len(set(majors)):
                problems.append(
                    f"{path.name}: ## {declared} subsections out of order or duplicated: {majors}"
                )
        orphans = sorted(
            parents
            - {(declared, major) for declared, majors in order.items() for major in majors}
        )
        for declared, major in orphans:
            problems.append(
                f"{path.name}: '### {declared}.{major}.x' has no '### {declared}.{major}' parent"
            )
    assert problems == [], "document section numbering drifted:\n" + "\n".join(problems)


def test_every_citation_of_the_search_protocol_resolves_to_a_real_heading() -> None:
    """Renumbering a protocol subsection must update every citation of it (draft §39.2).

    The protocol's section numbers are agent-facing: `references/reason-codes.md` and the ADRs send
    a reader to "协议 §6.6" for the code table and "协议 §8" for the reserved subcommands. Renumber
    the headings and those directions start pointing at unrelated prose.
    """

    tops, entries = _document_headings(PROTOCOL)
    exact = {(declared, major) for declared, major, minor, _line, _enc in entries if minor is None}
    problems: list[str] = []
    for path in STRUCTURED_DOCUMENTS:
        text = path.read_text(encoding="utf-8")
        for match in _PROTOCOL_CITATION.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            for token in re.findall(r"§([\d.]+)", match.group(1)):
                parts = token.split(".")
                number = int(parts[0])
                if len(parts) == 1 and number not in tops:
                    problems.append(f"{path.name}:{line}: cites 协议 §{token}, not a ## section")
                elif len(parts) == 2 and (number, int(parts[1])) not in exact:
                    problems.append(f"{path.name}:{line}: cites 协议 §{token}, not a ### heading")
                elif len(parts) > 2:
                    problems.append(f"{path.name}:{line}: cites 协议 §{token} at three levels")
    assert problems == [], "protocol citations no longer resolve:\n" + "\n".join(problems)


def test_the_heading_parser_handles_both_numbering_conventions() -> None:
    """The audit is only as good as its parser — pin the conventions and the false positives.

    Each assertion below corresponds to a defect the first draft of this scan produced against
    perfectly healthy documents (draft §39.3).
    """

    assert _chinese_number("五") == 5
    assert _chinese_number("十") == 10
    assert _chinese_number("十一") == 11
    assert _chinese_number("十二") == 12

    assert _H2_ARABIC.match("## 12. 阶段计划") is not None
    assert _H2_CHINESE.match("## 五、AIROOT Native Index 设计") is not None

    three_level = _H3_NUMBERED.match("### 8.1.1 Skill 更新、复制和 root relocate")
    assert three_level is not None
    assert (three_level.group(1), three_level.group(2), three_level.group(3)) == ("8", "1", "1")

    assert _H3_NUMBERED.match("### 1. 搜索目标是文件名和路径元数据") is None
    assert _H3_NUMBERED.match("### v1 支持") is None
    assert _H3_NUMBERED.match("#### 8.1.1 a deeper list heading") is None

    assert PROTOCOL in STRUCTURED_DOCUMENTS
    assert DRAFT in STRUCTURED_DOCUMENTS


# --- Guard group 8: documented options must be real options (draft §40) ------------------------
#
# Guard group 4 checks that every `airoot <verb>` the documents name exists. It stops one token
# short: the flags. `airoot search java --max-staleness-ms 1` passes group 4 even if that flag was
# renamed, and the agent then reports a failure that never happened — the same harm as a missing
# command, one token further in.
#
# Three slicing rules, each learned from a false positive against a healthy document rather than
# guessed up front:
#   * A line may hold **several** invocations (SKILL.md's answer table does), so each window ends at
#     the next `airoot` on the line — otherwise `adopt --mode` gets blamed on `discover`.
#   * A markdown table **cell** is a unit: the "make it permanent" row keeps the command in one cell
#     and the prohibition "不发明 `--force`" in the next. Only an *unescaped* pipe splits a cell
#     (`path backup\|restore` must survive).
#   * Everything after the ` -- ` separator belongs to the child process, never to AIROOT.
#
# Deliberately NOT checked: "every `--flag` token anywhere in these files must exist". That was tried
# and is wrong — the documents legitimately name flags that are not AIROOT's at all (`--no-modify-path`
# is rustup's, in the P2 paragraph of AGENTS.md) and flags named only to forbid them (`--force` in
# SKILL.md: "不发明 --force"). The per-invocation check is the honest one: a flag an agent is *told to
# type* must exist.

#: The documents an agent is told to *operate* from. The planning document, the contract draft and the
#: ADRs are proposals and records — they quote the frozen table's spelling, including forms this build
#: has not implemented, and that direction is already covered by the frozen-list guard.
AGENT_OPERATIONAL_DOCS = (AGENTS, SKILL, *sorted((REPO / "references").glob("*.md")))

#: Options accepted *before* argparse runs, so they are real to a user but invisible to the parser.
#: `exec --env <id> -- <cmd>` is the spelling the frozen planning table (§15.1) uses; the rewrite is
#: `cli._normalize_exec_argv` and the name is declared there as `EXEC_ALIAS_FLAG`, so this guard reads
#: the same source instead of hardcoding a second copy of the literal.
PRE_PARSE_ALIASES = {"exec": {EXEC_ALIAS_FLAG}}

_AIROOT_INVOCATION = re.compile(r"airoot(?:\.cmd)?\s")
_MARKDOWN_CELL = re.compile(r"(?<!\\)\|")
_CHILD_SEPARATOR = re.compile(r"\s+--\s+")
_LONG_FLAG = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)")


def _option_arity(parser: argparse.ArgumentParser) -> dict[str, bool]:
    """Option string -> whether it consumes the next token (needed to find the command word)."""

    return {opt: action.nargs != 0 for action in parser._actions for opt in action.option_strings}


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    nested = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "subcommand"
    ]
    return nested[0].choices if nested else {}


def _command_word(token: str) -> str:
    """The bare command word, surviving backticked prose like `` `airoot repair` ``."""

    match = re.match(r"[`*]*([a-z][a-z-]*)", token)
    return match.group(1) if match else ""


def _skip_options(tokens: list[str], position: int, arity: dict[str, bool]) -> int:
    while position < len(tokens) and tokens[position].startswith("-"):
        position += 2 if arity.get(tokens[position]) else 1
    return position


def _invocation_windows(line: str) -> list[str]:
    """Each `airoot ...` invocation on one line, cut at the child separator and the table cell."""

    body = line.split("#", 1)[0]
    starts = [match.end() for match in _AIROOT_INVOCATION.finditer(body)]
    windows: list[str] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(body)
        windows.append(_MARKDOWN_CELL.split(_CHILD_SEPARATOR.split(body[start:end])[0])[0])
    return windows


def _documented_option_problems() -> list[str]:
    """Resolve every documented invocation against the real parser; report options it rejects."""

    parser = build_parser()
    top = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ][0].choices

    options: dict[str, set[str]] = {}
    arities: dict[str, dict[str, bool]] = {}
    for name, subparser in top.items():
        options[name] = {opt for action in subparser._actions for opt in action.option_strings}
        arities[name] = _option_arity(subparser)
        for sub_name, leaf in _subcommands(subparser).items():
            key = f"{name} {sub_name}"
            options[key] = {opt for action in leaf._actions for opt in action.option_strings}
            arities[key] = _option_arity(leaf)
    for command, aliases in PRE_PARSE_ALIASES.items():
        options[command] |= aliases

    verbs = {key for key in options if " " not in key}
    top_arity = _option_arity(parser)

    def resolve(window: str) -> tuple[str | None, list[str]]:
        tokens = window.split()
        position = _skip_options(tokens, 0, top_arity)
        if position >= len(tokens):
            return None, []
        verb = _command_word(tokens[position])
        if verb not in verbs:
            return None, []  # an unknown verb is guard group 4's finding, not this one's
        key, position = verb, position + 1
        children = {candidate.split(" ", 1)[1] for candidate in options if candidate.startswith(f"{verb} ")}
        if children:
            candidate = _skip_options(tokens, position, arities[verb])
            if candidate < len(tokens) and _command_word(tokens[candidate]) in children:
                key = f"{verb} {_command_word(tokens[candidate])}"
        unknown = [flag for flag in _LONG_FLAG.findall(" ".join(tokens)) if flag not in options[key]]
        return key, unknown

    problems: list[str] = []
    for path in AGENT_OPERATIONAL_DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for window in _invocation_windows(line):
                key, unknown = resolve(window)
                problems += [f"{path.name}:{number}: airoot {key} ... {flag}" for flag in unknown]

    meta = json.loads(AGENT_META.read_text(encoding="utf-8"))
    for entry in meta["invocation"]:
        key, unknown = resolve(" ".join(entry["command"]))
        problems += [f"agents/airoot.json: airoot {key} ... {flag}" for flag in unknown]

    # A guard that matches nothing is a guard that proves nothing.
    assert len(AGENT_OPERATIONAL_DOCS) >= 3
    return problems


def test_every_option_the_docs_tell_an_agent_to_use_exists() -> None:
    """`--max-staleness-ms` renamed to `--staleness-ms` would leave AGENTS.md lying, silently."""

    problems = _documented_option_problems()
    assert problems == [], "documents name options the CLI does not accept:\n" + "\n".join(problems)


def test_the_invocation_slicer_handles_the_three_ambiguities() -> None:
    """Each rule was learned from a false positive, so each one is pinned (draft §40.3)."""

    # A line may hold several invocations: the flag belongs to the second one, not the first.
    windows = _invocation_windows("`airoot discover --json` → `airoot adopt <path> --mode reference`")
    assert len(windows) == 2
    assert "--mode" not in windows[0]
    assert "--mode" in windows[1]

    # A markdown table cell is a unit, so the prohibition in the next cell is not the command's flag.
    windows = _invocation_windows("| `airoot env persist <id> --dry-run` | 不发明 `--force` |")
    assert len(windows) == 1
    assert "--force" not in windows[0]
    assert "--dry-run" in windows[0]

    # Everything after the separator belongs to the child process.
    windows = _invocation_windows("airoot exec <id> -- env --json")
    assert len(windows) == 1
    assert "--json" not in windows[0]

    # Only an *unescaped* pipe splits a cell.
    windows = _invocation_windows("`airoot tool gc --plan\\|--apply`")
    assert len(windows) == 1
    assert "--apply" in windows[0]


# --- Guard group 9: the two prohibition lists must be one list (draft §41) ---------------------
#
# Guard group 5 ties the two "not implemented" declarations together. The *other* half of the
# safety surface — the prohibitions — had no such tie: `agents/airoot.json`'s `never` was asserted
# only to be non-empty, and it had drifted. Two prohibitions SKILL.md states were absent from the
# machine-readable list, **including this project's headline honesty rule** ("never claim AIROOT is
# implemented / Everything-class"), while two entries anchored outside SKILL's checklist were the
# only reason both lists still counted eight — an accident that invited the reading that they were
# the same list.
#
# The correspondence is now *declared* rather than assumed: every `never` entry carries a stable
# `id` and either the 1-based `skill_item` it mirrors, or a `stated_in` naming the SKILL heading
# that says it elsewhere. That makes the check exact across two languages instead of a keyword
# guess. A checklist item with no counterpart is a prohibition an agent reading the metadata never
# learns; a counterpart pointing at nothing is a rule nobody states.

SKILL_CHECKLIST_HEADING = "## 绝不做的清单"


def _skill_prohibitions() -> list[str]:
    """SKILL.md's numbered never-do items, in document order."""

    section = SKILL.read_text(encoding="utf-8").split(SKILL_CHECKLIST_HEADING, 1)[1]
    section = section.split("\n## ", 1)[0]
    return [line.strip() for line in section.splitlines() if re.match(r"^\d+\.\s", line.strip())]


def _prohibition_problems(
    items: list[str], skill_text: str, entries: list[dict[str, object]]
) -> list[str]:
    """Compare a checklist against machine-readable entries. Pure, so the failure modes are testable."""

    problems: list[str] = []
    ids = [entry.get("id") for entry in entries]
    for identifier in ids:
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
            problems.append(f"{identifier!r} is not a stable snake_case id")
    for identifier in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{identifier} is used by more than one entry")

    anchored: list[int] = []
    for entry in entries:
        name = entry.get("id")
        if not str(entry.get("statement", "")).strip():
            problems.append(f"{name}: empty statement")
        item = entry.get("skill_item")
        if item is None:
            stated_in = entry.get("stated_in")
            if not stated_in:
                problems.append(f"{name}: needs a skill_item or a stated_in")
            elif f"## {stated_in}" not in skill_text:
                problems.append(f"{name}: stated_in {stated_in!r} is not a SKILL.md heading")
        elif not isinstance(item, int) or not 1 <= item <= len(items):
            problems.append(f"{name}: skill_item {item!r} is outside 1..{len(items)}")
        else:
            anchored.append(item)

    for item in sorted(set(range(1, len(items) + 1)) - set(anchored)):
        problems.append(f"SKILL.md item {item} has no machine-readable counterpart")
    for item in sorted({i for i in anchored if anchored.count(i) > 1}):
        problems.append(f"SKILL.md item {item} is claimed by more than one entry")
    return problems


def _documented_prohibition_problems() -> list[str]:
    items = _skill_prohibitions()
    if not items:
        return [f"SKILL.md no longer carries a {SKILL_CHECKLIST_HEADING!r} checklist"]
    entries = json.loads(AGENT_META.read_text(encoding="utf-8"))["never"]
    return _prohibition_problems(items, SKILL.read_text(encoding="utf-8"), entries)


def test_the_skill_checklist_and_the_machine_readable_prohibitions_agree() -> None:
    """Two agent-facing declarations of "never do this" that drift apart are a trap."""

    problems = _documented_prohibition_problems()
    assert problems == [], "the two prohibition lists disagree:\n" + "\n".join(problems)


def test_the_prohibition_check_reports_each_failure_mode() -> None:
    """Exercise the check against synthetic drift, so its silence on the real files means something.

    Each case below is a drift that actually happened, or would have: a rule added to SKILL.md alone
    (the headline honesty rule was in this state), an entry dangling past the end of the checklist,
    and an anchor naming a heading nobody wrote (draft §41.5).
    """

    skill_text = "## 绝不做的清单（§16.2）\n\n1. one\n2. two\n\n## 未实现的命令（不要调用）\n"
    items = ["1. one", "2. two"]
    good = [
        {"id": "alpha", "skill_item": 1, "statement": "a"},
        {"id": "beta", "skill_item": 2, "statement": "b"},
    ]
    assert _prohibition_problems(items, skill_text, good) == []

    # a rule stated only in the checklist: no entry mirrors item 2
    drift = [_prohibition_problems(items, skill_text, [good[0]])]
    assert any("item 2 has no machine-readable counterpart" in p for p in drift[0])

    # an entry pointing past the end of the checklist
    drift = [_prohibition_problems(items, skill_text, [*good, {"id": "gamma", "skill_item": 3, "statement": "c"}])]
    assert any("outside 1..2" in p for p in drift[0])

    # two entries claiming the same item
    drift = [_prohibition_problems(items, skill_text, [good[0], good[0]])]
    assert any("item 1 is claimed by more than one entry" in p for p in drift[0])

    # an unanchored entry that names a heading nobody wrote, and one that names nothing
    drift = [
        _prohibition_problems(
            items,
            skill_text,
            [
                *good,
                {"id": "gamma", "skill_item": None, "stated_in": "不存在的章节", "statement": "c"},
                {"id": "delta", "skill_item": None, "statement": "d"},
            ],
        )
    ]
    assert any("not a SKILL.md heading" in p for p in drift[0])
    assert any("needs a skill_item or a stated_in" in p for p in drift[0])

    # a machine-readable entry restated twice under the same id
    drift = [_prohibition_problems(items, skill_text, [good[0], {**good[1], "id": "alpha"}])]
    assert any("used by more than one entry" in p for p in drift[0])

    # and the real files must be the clean case
    assert _documented_prohibition_problems() == []


# --- Guard group 12: a prose exit number must match the frozen mapping (draft §44) -------------
#
# The catalogue has a machine-checked table, and SKILL.md has a machine-checked exit-code table.
# What neither covers is **prose**: AGENTS.md and the references state exit numbers inline —
# "`SEARCH_FALLBACK_USED`(2)", "`OWNERSHIP_REQUIRED`(7)", "`SELF_VALIDATION_FAILED`，退出码 8".
# Those are the numbers an agent quotes back to a user, and a changed mapping would leave every one
# of them stale with nothing to notice.
#
# Deliberately narrow. A bare number near a code proves nothing — `D7` covers three codes and
# `§17.6` is a section reference — so only an explicit marker counts: a parenthesised single digit,
# or the words 退出码 followed by one. Widening this to "any digit near a code" is how an audit
# turns into a source of noise (see the rejected alternatives in §40.4 and §42.4).

_REASON_CODE_ALTERNATION = "|".join(
    re.escape(code) for code in sorted(REASON_EXIT, key=len, reverse=True)
)

_PROSE_EXIT_FORMS = (
    re.compile(rf"(?<![A-Z0-9_])({_REASON_CODE_ALTERNATION})(?![A-Z0-9_])`?\s*[（(]\s*(\d)\s*[)）]"),
    re.compile(rf"(?<![A-Z0-9_])({_REASON_CODE_ALTERNATION})(?![A-Z0-9_])`?\s*[，,]\s*退出码\s*(\d)"),
    # The third spelling (draft §51): a parenthesised "退出码 N" right after the code. It was
    # invisible to the two forms above — `（退出码 4）` is neither a bare parenthesised digit nor a
    # comma-then-退出码 — and a mutation proved it: swapping `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码
    # 4）to 5 left all 739 tests green. Note the *adjacency* requirement: the code must sit immediately
    # before the group. Pairing "a code and a number somewhere on the same line" was measured to
    # produce 135 false positives and not one true finding.
    re.compile(rf"(?<![A-Z0-9_])({_REASON_CODE_ALTERNATION})(?![A-Z0-9_])`?\s*[（(]\s*退出码\s*(\d)\s*[)）]"),
)

#: `退出码 N` occurrences (draft §51) — every one, bound or not.
_ANY_EXIT_WORDS = re.compile(r"退出码\s*\*{0,2}(\d)")

#: How many `退出码 N` claims have **no** reason code bound to them (draft §51.4 decision 2).
#:
#: This is a *census*, not a list of mistakes: some of these numbers legitimately belong to a code
#: named in another sentence ("需要确认或 scope 提升时…以退出码 4 停下"), and two of them describe an
#: exit code rather than a reason code at all. What it buys is that "how many exit numbers does
#: nothing check?" is a recorded number instead of a silence — adding such a sentence now either
#: binds it with an explicit marker or makes this number wrong on purpose.
#:
#: Consequence for prose: when *describing the spellings themselves*, write the placeholder
#: (`` `CODE`，退出码 N ``) rather than a real digit. A digit there is not a claim about any code, so
#: counting it would inflate the census with something that says nothing — and it is cheaper to write
#: the shape accurately than to explain a number that moved for no behavioural reason.
UNBOUND_EXIT_WORDS = 9


def _exit_word_claims(text: str) -> list[tuple[int, int, int]]:
    """(value, line, offset) for every ``退出码 N`` in ``text``."""

    return [
        (int(match.group(1)), text.count("\n", 0, match.start()) + 1, match.start())
        for match in _ANY_EXIT_WORDS.finditer(text)
    ]


def _unbound_exit_words(text: str) -> list[tuple[int, int]]:
    """``退出码 N`` claims that no explicit code marker covers, as (value, line)."""

    spans = [m.span() for form in _PROSE_EXIT_FORMS[1:] for m in form.finditer(text)]
    return [
        (value, line)
        for value, line, offset in _exit_word_claims(text)
        if not any(start <= offset < end for start, end in spans)
    ]


def _prose_exit_claims(text: str) -> list[tuple[str, int, int]]:
    """(code, claimed exit, line number) for every inline exit-number claim in ``text``."""

    claims: list[tuple[str, int, int]] = []
    for form in _PROSE_EXIT_FORMS:
        for match in form.finditer(text):
            claims.append((match.group(1), int(match.group(2)), text.count("\n", 0, match.start()) + 1))
    return claims


def test_every_prose_exit_number_matches_the_frozen_mapping() -> None:
    """An agent repeating "exit 2" from AGENTS.md must be repeating the truth."""

    problems: list[str] = []
    claims = 0
    for path in (*AGENT_OPERATIONAL_DOCS, REASON_TABLE):
        for code, claimed, line in _prose_exit_claims(path.read_text(encoding="utf-8")):
            claims += 1
            actual = REASON_EXIT.get(code)
            if actual != claimed:
                problems.append(
                    f"{path.name}:{line}: {code} is quoted as exit {claimed}, but the mapping says {actual}"
                )
    assert problems == [], "prose exit numbers contradict the frozen mapping:\n" + "\n".join(problems)
    # A non-vacuity floor, not a coverage threshold: the regex forms are narrow by design, so this
    # fails if they ever stop matching the documents rather than silently checking nothing.
    assert claims >= 15, f"only {claims} inline exit claims were found — did the wording change?"


def test_the_prose_exit_patterns_only_match_explicit_markers() -> None:
    """Pin the narrowness, because widening it is the tempting "improvement"."""

    # A parenthesised number after the code is a claim.
    assert _prose_exit_claims("`SEARCH_FALLBACK_USED`(2)") == [("SEARCH_FALLBACK_USED", 2, 1)]
    assert _prose_exit_claims("`OWNERSHIP_REQUIRED`，退出码 7") == [("OWNERSHIP_REQUIRED", 7, 1)]
    # ...and so is a parenthesised "退出码 N" (draft §51 — the spelling that was invisible).
    assert _prose_exit_claims("`SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4）") == [
        ("SCOPE_UPGRADE_REQUIRES_APPROVAL", 4, 1)
    ]

    # A code next to an unrelated number is not: `D7` is an invariant and the counts are prose.
    assert _prose_exit_claims("`INVARIANTS` 的 **D7** 新增两个码（3 个）") == []
    assert _prose_exit_claims("`SEARCH_INDEX_DEGRADED` 读不出来 / 2 个码") == []
    # **Adjacency is required**: an exit number several words after the code is a census entry, not a
    # claim about that code. Measured: pairing "same line" produced 135 false positives, zero true.
    assert _prose_exit_claims("`SEARCH_FALLBACK_USED` 是降级，并以退出码 2 停下") == []
    # A code name inside a longer identifier must not match.
    assert _prose_exit_claims("`SEARCH_FALLBACK_USED_EXTRA`(2)") == []
    # And the line number is the claim's line, not the first line of the document.
    text = "line one\n\n`SEARCH_FALLBACK_USED`(2)\n"
    assert _prose_exit_claims(text)[0][2] == 3


def test_every_unbound_exit_number_is_a_census_and_not_a_silence() -> None:
    """`退出码 N` with no adjacent code is allowed — being *uncounted* is not (draft §51.4-2).

    Four of the numbers this counts are claims about a code named in another sentence, and the rest
    describe an exit code without naming one. None of them can be bound automatically without
    guessing, which the 135-false-positive measurement rules out. So the count is frozen instead, and
    a new unbound sentence has to either bind itself or move the number deliberately.
    """

    total = 0
    for path in (*AGENT_OPERATIONAL_DOCS, REASON_TABLE):
        total += len(_unbound_exit_words(path.read_text(encoding="utf-8")))

    assert total == UNBOUND_EXIT_WORDS, (
        f"{total} `退出码 N` claims have no adjacent reason code, but the census says {UNBOUND_EXIT_WORDS}. "
        "Bind it with an explicit marker (`` `CODE`（退出码 N） ``) or bump UNBOUND_EXIT_WORDS — "
        "an unbound number is fine, an uncounted one is not."
    )

    # Non-vacuity: the counter must actually see a bound claim as bound and an unbound one as unbound.
    assert _unbound_exit_words("`OWNERSHIP_REQUIRED`，退出码 7") == []
    assert _unbound_exit_words("以退出码 4 停下") == [(4, 1)]


# --- Guard group 14: the D1-D10 catalogue must be the same in both places (draft §46) ----------
#
# The catalogue exists twice: as the authoritative table in
# `docs/AIROOT-v0.3-诊断码与ReasonCode表.md` §2, and as `caps/doctor.py`'s `INVARIANTS`. The
# existing guards check that the catalogue is exactly D1-D10, that every code is a registered
# reason code, and that every code has a path that can emit it. **None of them compares the two
# declarations with each other**, so a code could be moved from one invariant to another in code
# and the table would go on saying otherwise — which is what a reader consults to decide whether a
# diagnostic is a D3 drift or a D7 staleness.
#
# Only the *third* cell of a row carries codes: D4's description quotes `UNMANAGED_OBJECT_PRESENT`
# in prose, and D7's quotes `index.max_age_ms`. Reading the whole row would import prose as
# catalogue entries, so the parse is cell-scoped and the self-test below pins that.

_INVARIANT_ROW = re.compile(r"^\|\s*(D\d+)\s*\|")
_TABLE_CODE = re.compile(r"`([A-Z][A-Z0-9_]{2,63})`")


def _documented_invariants(text: str) -> dict[str, list[str]]:
    """D-number -> the codes listed for it in the table's third column."""

    documented: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _INVARIANT_ROW.match(line)
        if not match:
            continue
        cells = line.split("|")
        if len(cells) < 4:
            continue
        documented[match.group(1)] = _TABLE_CODE.findall(cells[3])
    return documented


def test_the_documented_invariant_table_matches_the_code_catalogue() -> None:
    """Two declarations of "which invariant owns which code" that drift apart are a trap."""

    documented = _documented_invariants(REASON_TABLE.read_text(encoding="utf-8"))
    problems: list[str] = []

    for name in sorted(set(documented) ^ set(INVARIANTS), key=lambda item: int(item[1:])):
        problems.append(f"{name} is declared in only one of the two catalogues")
    for name in sorted(set(documented) & set(INVARIANTS), key=lambda item: int(item[1:])):
        only_table = sorted(set(documented[name]) - set(INVARIANTS[name]))
        only_code = sorted(set(INVARIANTS[name]) - set(documented[name]))
        if only_table or only_code:
            problems.append(f"{name}: table-only={only_table} code-only={only_code}")

    assert problems == [], "the documented invariant table and INVARIANTS disagree:\n" + "\n".join(problems)
    assert len(documented) == 10, f"expected D1-D10 in the table, parsed {sorted(documented)}"


def test_no_diagnostic_code_has_two_owners() -> None:
    """A code belonging to two invariants would make `doctor`'s grouping ambiguous."""

    owners: dict[str, list[str]] = {}
    for name, codes in INVARIANTS.items():
        for code in codes:
            owners.setdefault(code, []).append(name)
    shared = sorted(f"{code}: {names}" for code, names in owners.items() if len(names) > 1)
    assert shared == [], f"diagnostic codes claimed by more than one invariant: {shared}"
    assert len(DIAGNOSTIC_CODES) == sum(len(codes) for codes in INVARIANTS.values())


def test_the_invariant_table_parser_reads_only_the_code_column() -> None:
    """Pin the cell scoping: D4's prose quotes a code, and prose is not catalogue."""

    row = "| D4 | 只报告不接管（`UNMANAGED_OBJECT_PRESENT` 仅 `--include-unmanaged`） | `ORPHANED_STORE_INSTANCE`, `UNMANAGED_OBJECT_PRESENT` |"
    assert _documented_invariants(row) == {
        "D4": ["ORPHANED_STORE_INSTANCE", "UNMANAGED_OBJECT_PRESENT"]
    }
    # A matched row always yields a key. When the code cell is empty the list is empty — which the
    # main guard reports as "declared in only one catalogue"/"code-only" rather than skipping the
    # row, so a table that lost its code column fails instead of quietly checking nothing.
    assert _documented_invariants("| D9 | `POLICY_ONLY_MODE` |") == {"D9": []}


# --- Guard group 15: the three "scope" vocabularies stay distinct and published (draft §47) ----
#
# The word `scope` carries three different vocabularies here, and each shares spellings with
# another:
#   * binding     — where a capability may be bound      (system|machine|session|project)
#   * persistence — how far a persisted value reaches    (user|machine)
#   * routing     — where a dependency belongs           (project|data-root)
# `machine` means one thing in the first and something else in the second; `project` means one
# thing in the first and something else in the third. Each vocabulary is fine on its own — mixing
# them is not — and the CLI already exposes five different `--scope` choice sets. None of that was
# pinned: the sets were hand-written literals next to the constants they mirror, so a drift would
# have shown up as a verb silently refusing (or accepting) a value.

ROUTING_SCOPES = frozenset({SCOPE_PROJECT, SCOPE_DATA_ROOT})


def _scope_choices_by_command() -> dict[str, list[str]]:
    """command path -> the choices its `--scope` option accepts."""

    parser = build_parser()
    top = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ][0].choices
    found: dict[str, list[str]] = {}
    for name, subparser in top.items():
        nodes = [(name, subparser)]
        nodes += [(f"{name} {sub}", leaf) for sub, leaf in _subcommands(subparser).items()]
        for key, node in nodes:
            for action in node._actions:
                if "--scope" in action.option_strings:
                    found[key] = list(action.choices or [])
    return found


def test_the_binding_scope_vocabulary_is_the_published_enum() -> None:
    assert BINDING_SCOPES == {"system", "machine", "session", "project"}
    assert set(load_schema("common")["$defs"]["scope"]["enum"]) == BINDING_SCOPES


def test_the_persistence_scope_vocabulary_is_the_published_enum() -> None:
    assert set(PERSIST_SCOPES) == {"user", "machine"}
    reference_plan = load_schema("reference-plan")
    published = reference_plan["properties"]["operations"]["items"]["properties"]["target_scope"]["enum"]
    assert set(published) == set(PERSIST_SCOPES)


def test_every_scope_option_names_one_of_the_three_vocabularies() -> None:
    """Five `--scope` sets, each pinned to the vocabulary it means — including the two subsets."""

    choices = _scope_choices_by_command()
    assert set(choices) == {"where", "inventory", "plan", "env persist", "tool pin"}, sorted(choices)

    # The two observation verbs offer the whole binding vocabulary.
    assert set(choices["where"]) == BINDING_SCOPES
    assert set(choices["inventory"]) == BINDING_SCOPES
    # `tool pin` deliberately withholds `system`: a pin binds a capability for a user, and the
    # system scope is not something a pin may claim. Pinned exactly so a change here is noticed.
    assert set(choices["tool pin"]) == BINDING_SCOPES - {"system"}
    assert set(choices["plan"]) == ROUTING_SCOPES | {"machine"}
    assert set(choices["env persist"]) == set(PERSIST_SCOPES)


def test_the_shared_spellings_between_vocabularies_are_the_known_ones() -> None:
    """The overlaps are the hazard. Asserting them keeps them a known fact, not an accident."""

    assert BINDING_SCOPES & set(PERSIST_SCOPES) == {"machine"}
    assert BINDING_SCOPES & ROUTING_SCOPES == {"project"}
    assert set(PERSIST_SCOPES) & ROUTING_SCOPES == set()


# --- Guard group 16: a status document may not deny what has been delivered (draft §48) ---------
#
# The review report carries an appended section that describes **current** status — and it said the
# repository "is still not an installable Skill" for twenty-odd rounds after `SKILL.md` was
# delivered in the Skill-adapter stage (draft §22). Nothing noticed, because count guards check
# numbers and no guard reads prose for factual denials.
#
# Scope is the **appended status section only**, and that boundary is the point: the review above it
# is a snapshot, and it was true there ("当前 Skill scaffold 目录还没有根级 `SKILL.md`"). A guard
# that also policed the snapshot would demand rewriting a historical record to match today.
#
# Only denials that are *checkable* are guarded: each entry names an artifact that must exist and a
# phrase that must then be absent from the status section.

REVIEW_STATUS_HEADING = "## P1 实现状态"
REVIEW_FALSE_CLAIMS = {
    "SKILL.md": "不是可安装 Skill",
}


def _bounds_fixture() -> dict[str, Any]:
    return json.loads((GOLDEN / "execution_bounds.json").read_text(encoding="utf-8"))


# --- Guard group 18: every number that decides a refusal is in the corpus (draft §50) ------------
#
# §46.4 named two of these (`CONFIRMATION_OPTIONS`, the search policy's bounds) and deferred them on
# purpose. §50 closed the **class** rather than the two names: a Rust port reading only the corpus and
# the published schemas had no way to learn the shipped `limit` bound, where the index lives, the
# three-way confirmation, `MAX_ROOTS`, or the 2 GiB artifact cap — so two implementations could
# reproduce every response in the corpus byte-for-byte and still disagree about whether request 2001
# is accepted.
#
# Equality alone would only catch a stale artifact. The load-bearing checks are the **executed** ones:
# a request *at* the bound named in the artifact is accepted and one *above* it refused. Every bound
# that is not exercised that way says so with a reason (`execution_bounds.RECORDED_ONLY`), because
# "not checked" and "looks checked" must not be indistinguishable — the rule §49 applied to evidence.


def test_the_execution_bounds_artifact_is_the_resolved_bounds() -> None:
    from execution_bounds import build_bounds

    assert _bounds_fixture() == build_bounds(), (
        "execution_bounds.json is stale: run `python cli/tests/golden.py` after changing a bound"
    )


def test_every_execution_bound_declares_its_provenance_and_whether_it_is_exercised() -> None:
    from execution_bounds import EXERCISED, coverage_problems

    document = _bounds_fixture()
    problems = coverage_problems(document)
    assert problems == [], "; ".join(problems)

    # Non-vacuity: every failure mode the helper claims to detect must actually be reported. The
    # split is passed in rather than mutated, so the check does not rest on editing module state.
    undeclared = {**document, "a_block_nobody_declared": {}}
    assert any("neither exercised nor recorded" in item for item in coverage_problems(undeclared))
    assert any("provenance nobody declared" in item for item in coverage_problems({**undeclared, "provenance": {}}))
    assert any(
        "provenance for a block that does not exist" in item
        for item in coverage_problems({**document, "provenance": {**document["provenance"], "ghost": "nowhere"}})
    )
    first = EXERCISED[0]
    assert any(
        "cannot be both exercised and recorded-only" in item
        for item in coverage_problems(document, recorded={first: "synthetic"})
    )
    assert any(
        "recorded-only without a reason" in item
        for item in coverage_problems(document, recorded={"crawl": "", first: ""})
    )


def test_the_search_bounds_in_the_artifact_are_the_bounds_actually_enforced() -> None:
    """Accept at the bound, refuse above it — built from the artifact's numbers, not the code's."""

    from airoot.caps.search import build_request, load_search_policy
    from airoot.exits import AirootError

    block = _bounds_fixture()["search_request"]
    policy = load_search_policy()

    for name in ("limit", "max_duration_ms", "max_staleness_ms"):
        bound = block["limits"][name]
        assert bound == policy.limit(name, block["code_fallback_ceilings"][name]), (
            f"{name}: the artifact and the shipped policy disagree"
        )
        build_request("python", policy=policy, **{name: bound})
        with pytest.raises(AirootError) as error:
            build_request("python", policy=policy, **{name: bound + 1})
        assert error.value.reason_code == "EXTENSION_INPUT_INVALID", (
            f"{name} above the bound must be refused outright, never clamped"
        )

    # `MAX_ROOTS` and `MAX_CURSOR_LENGTH` are code constants with no policy key, so the artifact is
    # their only record — which is exactly why the refusal has to be executed here.
    roots = block["max_roots"]
    build_request("python", policy=policy, roots=[f"r{index}" for index in range(roots)])
    with pytest.raises(AirootError):
        build_request("python", policy=policy, roots=[f"r{index}" for index in range(roots + 1)])

    width = block["max_cursor_length"]
    build_request("python", policy=policy, cursor="c" * width)
    with pytest.raises(AirootError) as error:
        build_request("python", policy=policy, cursor="c" * (width + 1))
    # A malformed request, not an over-large well-formed one: a different code on purpose.
    assert error.value.reason_code == "SEARCH_QUERY_INVALID"


def test_the_confirmation_options_in_the_artifact_are_the_ones_returned() -> None:
    from airoot.caps.planner import ScopeRequest, decide_scope

    options = _bounds_fixture()["confirmations"]["options"]
    decision = decide_scope(ScopeRequest(capability_id="node"))
    assert decision.confirmation_required is True
    assert list(decision.options) == options, "an agent offering different wording is a different product"


def test_the_shipped_policy_revisions_are_the_ones_reportable() -> None:
    """Revision strings are not bounds, but a bump that skips the corpus must not go unnoticed."""

    from execution_bounds import build_bounds

    revisions = _bounds_fixture()["policy_revisions"]
    assert revisions == build_bounds()["policy_revisions"]
    assert all(str(value).strip() for value in revisions.values()), "an empty revision cannot be reported"


def test_the_review_status_section_does_not_deny_a_delivered_artifact() -> None:
    text = REVIEW.read_text(encoding="utf-8")
    assert REVIEW_STATUS_HEADING in text, "the review's status section is gone"
    status = text.split(REVIEW_STATUS_HEADING, 1)[1]

    for artifact, denial in REVIEW_FALSE_CLAIMS.items():
        assert (REPO / artifact).exists(), f"{artifact} does not exist, so the denial would be true"
        assert denial not in status, (
            f"{REVIEW.name} still says {artifact} {denial!r} in its status section; "
            "it was delivered after that was written"
        )


# --- Guard group 17: every acceptance scenario has a disposition (draft §49) ---------------------
#
# The two documents define 107 scenario IDs between them, in two tables, and nothing enumerated the
# union — so ADR-0001's acceptance face was incomplete and §48.5 could only record "only ~30 are
# named by tests" as an unanswerable worry. `cli/tests/scenario_ledger.py` is the answer: derived
# facts (which document defines each ID, which test files name it) plus authored dispositions for
# the ones no test names.
#
# The guards below hold the two halves to different standards, on purpose:
#   * derived facts are checked for **exact equality** in both directions — but against the *frozen
#     ledger*, never against the derivation (which would be a tautology: `build_ledger` reads the
#     documents, so comparing it to them can never fail). A mutation test caught that mistake, and
#     it is why the frozen artifact, not the function, is what every guard here reads;
#   * authored judgements are checked only for **structure** — a declared vocabulary, and an
#     evidence pointer whose file and token both resolve. Whether a behaviour is really exercised
#     is not a question a guard can answer, so the ledger does not pretend it can: it makes the
#     judgement visible and reviewable instead.


def _frozen_ledger() -> list[dict[str, Any]]:
    return json.loads((GOLDEN / "scenario_ledger.json").read_text(encoding="utf-8"))["entries"]


def _ledger_identity_problems(defined: set[str], frozen: set[str]) -> list[str]:
    return [f"the frozen ledger invents a scenario nothing defines: {key}" for key in sorted(frozen - defined)] + [
        f"a defined scenario the frozen ledger does not account for: {key}" for key in sorted(defined - frozen)
    ]


def _ledger_evidence_problems(named: dict[str, set[str]], frozen: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for entry in frozen:
        expected = sorted(named.get(entry["id"], set()))
        if entry["cited_by"] != expected:
            problems.append(f"{entry['id']}: the ledger says {entry['cited_by']}, the tests name it from {expected}")
        want = "evidenced" if expected else "uncited"
        if entry["status"] != want:
            problems.append(f"{entry['id']}: status is {entry['status']!r}, should be {want!r}")
    return problems


def test_the_frozen_ledger_accounts_for_every_defined_scenario() -> None:
    """Both directions, against the **frozen** ledger — not against the derivation.

    The distinction is the whole guard. `build_ledger` walks the documents to build its ID set, so
    comparing *it* to the documents is a tautology that can never fail — and the first draft of this
    check was exactly that, which a mutation test caught (a scenario added to a document left it
    green). Only the frozen artifact can be out of date, so only the frozen artifact is worth
    comparing. The helper is exercised on synthetic input below for the same reason.
    """

    from scenario_ledger import definitions

    defined = set(definitions(REPO))
    frozen = {entry["id"] for entry in _frozen_ledger()}
    problems = _ledger_identity_problems(defined, frozen)
    assert problems == [], "; ".join(problems)

    # Non-vacuity: the helper must report a scenario that exists on only one side. The family letter
    # is assembled at runtime because this file matches `test_*.py` (see the sibling test below).
    probe = f"{'P'}-001"
    assert _ledger_identity_problems({probe}, set()) == [
        f"a defined scenario the frozen ledger does not account for: {probe}"
    ]
    assert _ledger_identity_problems(set(), {probe}) == [
        f"the frozen ledger invents a scenario nothing defines: {probe}"
    ]


def test_the_frozen_ledger_status_matches_the_citations_the_tests_actually_make() -> None:
    """`evidenced` must mean a test file names the ID — checked against the frozen ledger."""

    from scenario_ledger import citations

    named = citations(REPO)
    problems = _ledger_evidence_problems(named, _frozen_ledger())
    assert problems == [], "; ".join(problems)

    # Non-vacuity, both directions, on synthetic input.
    probe = f"{'S'}-001"
    stale = [{"id": probe, "cited_by": [], "status": "uncited"}]
    assert _ledger_evidence_problems({probe: {"test_x.py"}}, stale) == [
        f"{probe}: the ledger says [], the tests name it from ['test_x.py']",
        f"{probe}: status is 'uncited', should be 'evidenced'",
    ]
    assert _ledger_evidence_problems({}, stale) == []


def test_the_scenario_ledger_fixture_is_the_whole_ledger() -> None:
    """The fixture is the Rust port's acceptance index, so it must be the ledger, not a summary."""

    from scenario_ledger import build_ledger

    fixture = json.loads((GOLDEN / "scenario_ledger.json").read_text(encoding="utf-8"))
    ledger = build_ledger(REPO)
    assert fixture["entries"] == ledger, "scenario_ledger.json is stale: run `python cli/tests/golden.py`"

    summary = fixture["summary"]
    assert summary["total"] == len(ledger)
    assert summary["evidenced"] == sum(1 for entry in ledger if entry["status"] == "evidenced")
    assert summary["uncited"] == sum(1 for entry in ledger if entry["status"] == "uncited")
    assert summary["families"] == {
        family: sum(1 for entry in ledger if entry["family"] == family)
        for family in sorted({entry["family"] for entry in ledger})
    }


def test_every_uncited_scenario_has_a_structural_disposition() -> None:
    from scenario_ledger import build_ledger, evidence_problems, undisposed

    assert undisposed(REPO) == [], "these scenarios no test names and the ledger says nothing about them"
    problems = evidence_problems(REPO)
    assert problems == [], "the ledger's judgements are structurally broken: " + "; ".join(problems)

    # The check must be able to fail: a disposition that claims nothing is missing, names no
    # evidence, must be reported rather than accepted.
    entries = build_ledger(REPO)
    by_id = {entry["id"]: entry for entry in entries}
    sample = next(entry for entry in entries if entry["blocked_by"] == "none")
    broken = [dict(entry) for entry in entries]
    for entry in broken:
        if entry["id"] == sample["id"]:
            entry["evidence"] = None
    assert any(sample["id"] in problem for problem in evidence_problems(REPO, broken))

    # ...and a pointer whose file or token does not resolve must be reported too.
    for entry in broken:
        if entry["id"] == sample["id"]:
            entry["evidence"] = by_id[sample["id"]]["evidence"] + "-not-a-real-token"
    assert any(sample["id"] in problem for problem in evidence_problems(REPO, broken))
    assert broken != entries, "the synthetic inputs above did not modify the ledger"


def test_a_scenario_defined_twice_says_so_in_both_rows() -> None:
    """A duplicated ID is not fixed by deleting a row — it is fixed by making both rows say so."""

    from scenario_ledger import row_marker_problems

    problems = row_marker_problems(REPO)
    assert problems == [], "; ".join(problems)


def test_the_scenario_parser_handles_the_citation_syntaxes_actually_in_use() -> None:
    """Five range spellings and a slash list are in use, and one of them was missed once.

    The first probe written for §49 knew ``…``, ``...``, ``~`` and ``至`` but not ``..`` — so it
    under-reported the evidenced count by three, all of them endpoints of the two-dot range in
    ``test_l1_transaction.py``. Every spelling is pinned here, plus that concrete casualty by name,
    so the same regression cannot come back silently.

    **The family letter is assembled at runtime on purpose.** This file matches ``test_*.py``, so a
    literal scenario ID written here — in code *or* in prose — is counted as a test *naming* that
    scenario. That is the trap that produced two wrong answers while building §49: the ledger module
    reporting itself as evidence for every ID, and this audit file reporting itself as evidence for
    a syntax example. Both were technically true ("a test file names it") and both were worthless,
    which is why :func:`test_the_audit_file_does_not_appear_to_cite_a_scenario` exists.
    """

    from scenario_ledger import RANGE_MARKERS, _DEFINITION, _RANGE, _SID, citations

    family = "P"
    for marker in ("…", "...", "..", "~", "至"):
        assert marker in RANGE_MARKERS, f"{marker!r} is used by the tests but not declared"
        probe = f"see {family}-004{marker}{family}-008 in the list"
        assert _RANGE.findall(probe) == [(family, "004", "008")], f"{marker!r} does not expand"

    # A Windows SID is not a scenario ID; the families collide on `S`, and the SID must be consumed
    # whole — a partial match would leave `-21-1000` behind and could masquerade as a range endpoint.
    assert _SID.sub("<sid>", "user S-1-5-21-1000") == "user <sid>"

    # Only a table row whose *first* cell is the ID defines a scenario: prose that names one is a
    # citation, and treating it as a definition would fill the catalogue with empty rows.
    assert _DEFINITION.match(f"| {family}-001 | setup | expectation |") is not None
    assert _DEFINITION.match(f"{family}-003 和 {family}-013 是防止安全口径夸大的测试。") is None
    assert _DEFINITION.match(f"| 说明 | {family}-003 出现在句子里 | 期望 |") is None

    named = citations(REPO)
    for digits in ("005", "006", "007"):
        key = f"{family}-{digits}"
        assert named.get(key), f"{key} comes from the `{family}-004..{family}-008` range"


def test_the_audit_file_does_not_appear_to_cite_a_scenario() -> None:
    """A syntax example in a `test_*.py` file is indistinguishable from evidence.

    This is the trap that produced two wrong answers while building §49 — the ledger module
    reporting itself as evidence for all 107 IDs, and this audit file reporting itself as evidence
    for a syntax example. Both were *technically* true ("a test file names it") and both were
    worthless, which is why the rule needs a guard rather than a convention.
    """

    from scenario_ledger import citations

    named = sorted(key for key, files in citations(REPO).items() if Path(__file__).name in files)
    assert named == [], (
        f"{Path(__file__).name} appears to cite {named}; every scenario ID in this file is a syntax "
        "example, not evidence — compose such strings at runtime (see the sibling test)"
    )


def test_the_corpus_counts_are_the_same_everywhere() -> None:
    """Three counts that nothing checked: golden fixtures, acceptance scenarios, audit checks.

    All three had already drifted, which is the argument for guarding them: `AGENTS.md` and the
    review report said 27 golden fixtures when 25 existed; no document said how many acceptance
    scenarios the two contracts define; and the review report still said 48 audit checks after §48
    had raised the number to 49, because only the *test* count was ever compared.

    The draft is exempt from the fixture check because its mentions are per-stage history ("第 19 个
    fixture", "23 个既有 fixture 逐字节不变") — the same boundary §48 drew for the review snapshot.
    """

    fixtures = len(list(GOLDEN.glob("*.json")))
    for path in (AGENTS, REVIEW):
        stated = {
            int(value)
            for value in re.findall(r"(\d+)\*{0,2}\s*个\s*fixture", path.read_text(encoding="utf-8"))
        }
        assert stated == {fixtures}, f"{path.name} states {sorted(stated)} golden fixtures; there are {fixtures}"

    from scenario_ledger import build_ledger

    total = len(build_ledger(REPO))
    for path in (AGENTS, REVIEW):
        stated = {int(value) for value in re.findall(r"(\d+)\s*个场景编号", path.read_text(encoding="utf-8"))}
        assert stated, f"{path.name} no longer states how many acceptance scenarios exist"
        assert stated == {total}, f"{path.name} states {sorted(stated)} scenarios; the documents define {total}"

    checks = _audit_check_count(Path(__file__).read_text(encoding="utf-8"))
    for path in (AGENTS, REVIEW):
        stated = {
            int(value)
            for value in re.findall(r"(\d+)\*{0,2}\s*项常驻跨工件", path.read_text(encoding="utf-8"))
        }
        assert stated == {checks}, f"{path.name} states {sorted(stated)} audit checks; this module collects {checks}"


_PARAMETRIZE_LIST = re.compile(r"(?m)^@pytest\.mark\.parametrize\([^)]*?\[([^\]]*)\]")


def _audit_check_count(text: str) -> int:
    """How many checks this module collects, derived from its own source.

    Almost every check is one function; the schema check is parametrized over a literal list, so its
    extra cases are counted from the decorator. Deriving the number keeps it a fact of the file
    rather than a constant someone must remember to bump — and a decorator whose list is a *variable*
    makes this count wrong and the guard loud, which is the right failure direction.
    """

    functions = len(re.findall(r"(?m)^def test_", text))
    extra = sum(len([item for item in group.split(",") if item.strip()]) - 1 for group in _PARAMETRIZE_LIST.findall(text))
    return functions + extra


# --- Guard group 19: the on-demand reference must agree with the code (draft §51) ---------------
#
# `references/confirmation.md` is the file the Skill tells an agent to consult before it asks the user
# anything, and two things in it had no guard at all:
#
#   * the **three-way choice**. `test_the_confirmation_triple_is_the_frozen_one` binds the Skill text
#     and the machine-readable metadata to `CONFIRMATION_OPTIONS`, but not this file — measured:
#     renaming `data-root` to `data_root` left all 739 tests green, and this is the file that tells
#     the agent which words to offer;
#   * the claim that `.ai/tooling.json` is **read-only**. That is a statement about the
#     implementation, and nothing compared it with the implementation (§48's class: a document may
#     not go on asserting what the code no longer does — here, the reverse direction).

CONFIRMATION_REFERENCE = REPO / "references" / "confirmation.md"


def test_the_confirmation_reference_lists_exactly_the_frozen_triple() -> None:
    """Exact equality in both directions: an extra option is as wrong as a renamed one."""

    from airoot.caps.planner import CONFIRMATION_OPTIONS

    text = CONFIRMATION_REFERENCE.read_text(encoding="utf-8")
    assert "三选一" in text, "the three-way section is gone; this guard is now about nothing"
    block = text.split("三选一", 1)[1].split("```text", 1)[1].split("```", 1)[0]
    listed = [line.split()[0] for line in block.strip().splitlines() if line.split()]

    assert listed == list(CONFIRMATION_OPTIONS), (
        f"references/confirmation.md offers {listed}; the frozen options are {list(CONFIRMATION_OPTIONS)}. "
        "The reference is what the agent reads before it speaks, so an extra or renamed word here is a "
        "different product."
    )


def test_the_tooling_memory_is_read_only_in_the_code_and_not_just_in_the_prose() -> None:
    """`confirmation.md` says the memory is read-only; the code must still agree (draft §51.2-F3).

    The dangerous direction is "did it and did not say so": the day a write channel lands (it belongs
    to P2's human-approval path), this fails until the reference and ADR-0004 §12.2 are updated
    together. The read path is asserted too, so the guard cannot pass by the feature being deleted.
    """

    planner = (APP / "caps" / "planner.py").read_text(encoding="utf-8")
    assert "read_tooling_memory" in planner, "the read path vanished; this guard is now about nothing"
    assert "TOOLING_MEMORY_RELATIVE" in planner

    writes: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        for pattern in (r"write_text", r"\.write\(", r"json\.dump\(", r"open\([^)]*['\"][wa]"):
            for match in re.finditer(pattern, body):
                window = body[max(0, match.start() - 400) : match.end() + 400].lower()
                if "tooling" in window:
                    writes.append(f"{path.name}:{body.count(chr(10), 0, match.start()) + 1}")

    assert writes == [], (
        f"something now writes `.ai/tooling.json` ({writes}); references/confirmation.md still says the "
        "memory is read-only and the write channel is deliberately P2's, so update both or revert"
    )
