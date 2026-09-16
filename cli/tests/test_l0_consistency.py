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
PLANNING = REPO / "docs" / "AIROOT-总体方案规划-v0.3.md"
VALIDATION = REPO / "docs" / "AIROOT-v0.3-验证与测试方案.md"
REASON_TABLE = REPO / "docs" / "AIROOT-v0.3-诊断码与ReasonCode表.md"
SCHEMA_README = REPO / "docs" / "schema" / "README.md"
SKILL = REPO / "SKILL.md"
AGENT_META = REPO / "agents" / "airoot.json"
FIELD_VALUES = REPO / "references" / "field-values.md"
DRAFT = REPO / "docs" / "AIROOT-v0.3-管家模型与数据根契约草案.md"
PROTOCOL = REPO / "docs" / "AIROOT-搜索能力与工具集成协议方案.md"
REVIEW = REPO / "docs" / "AIROOT-v0.3-规范审查报告.md"
DECISION_LOG = REPO / "docs" / "AIROOT-v0.3-实现决策记录.md"
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
        DECISION_LOG,
    ]
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in documents)
    unreferenced = sorted(
        name for name in schema_names() if not names_token(corpus, name, word="A-Za-z0-9_-")
    )
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
    """The authoritative mapping is the code; the table must not lag behind it.

    §75: this used to be `code not in text`, which cannot tell `DEGRADED` from
    `CURRENT_SOURCE_DEGRADED` — a code that only survives as somebody else's substring reads as
    documented. The same defect was in the agent-facing reference guard below; there it was
    *hiding a real code* (`DEGRADED`), so both now match a whole word.
    """

    text = REASON_TABLE.read_text(encoding="utf-8")
    missing = sorted(code for code in REASON_EXIT if not names_code(text, code))
    assert missing == [], f"registered but undocumented reason codes: {missing}"


def names_token(text: str, token: str, *, word: str = "A-Za-z0-9_") -> bool:
    """True when `token` appears as itself, not as part of a longer word.

    §76: this is the one place a vocabulary check may ask "is it named here?". The old spelling in
    several guards was `token in text` or `token\\b`, and both are wrong in the same direction: the
    first because `DEGRADED` lives inside `CURRENT_SOURCE_DEGRADED`, the second because `\\b` only
    guards one side — `telescope` satisfied `scope\\b`, so a command could be "documented" by a word
    that merely ends with its name.
    """

    return (
        re.search(r"(?<![" + word + r"])" + re.escape(token) + r"(?![" + word + r"])", text) is not None
    )


def names_code(text: str, code: str) -> bool:
    """Reason codes are single tokens: a lowercase or hyphen continuation means a longer word."""

    return names_token(text, code)


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

    planning = PLANNING.read_text(encoding="utf-8")
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


# --- Guard group 27: what the repo map itself claims (draft §85) ---------------------------------
#
# Two of the map's directories were held complete by guards (`caps/*.py`, `policy/*.json`) and the
# rest were complete by luck. The map also *describes* the files it lists, and those descriptions were
# prose. §85 measured three claims that had gone wrong:
#
#   * the master plan's size (`2236 行` against 2249, nothing compared it);
#   * the decision log's row, which named three ADRs as if that were what the log contains — it has
#     twenty-five, and the newest one is the largest decision in P1;
#   * the draft's row, which called it "提案（未落地）" while §1 of the same file delegates the stage
#     records `§31`–`§85` to it. **Two sentences in one document describing one file differently** is
#     the shape this whole module exists for, and the map was the half nobody read twice.

REPO_MAP_HEADING = "## 2. 仓库地图"
REPO_MAP_ROW = re.compile(r"(?m)^\| `([^`]+)` \|(.+?)\|$")
FILE_SIZE_CLAIM = re.compile(r"（(\d+) 行）")
#: §1's delegation: the range, and the file the range lives in. This is the sentence the map's row for
#: that file has to agree with.
STAGE_DELEGATION = re.compile(
    r"逐阶段的实现记录\*\*（`§(\d+)`–`§(\d+)`[^）]*）\*\*全部在 `([^`]+)`"
)


def _repo_map_rows(text: str) -> dict[str, str]:
    """path -> description, from the repo-map table only and not from every table in the file."""

    section = _repo_map_section(text)
    if not section:
        return {}
    return {path: description.strip() for path, description in REPO_MAP_ROW.findall(section)}


def _repo_map_section(text: str) -> str:
    # `text.count(...)` rather than `HEADING not in text`: this module's own AST check (§76) treats
    # "a variable compared against a document" as the substring idiom it exists to replace, and there
    # is no reason to spend an exemption on a check that is not a vocabulary question at all.
    if text.count(REPO_MAP_HEADING) == 0:
        return ""
    return text.split(REPO_MAP_HEADING, 1)[1].split("\n## ", 1)[0]


def _repo_map_missing(groups: dict[str, list[Path]], map_text: str) -> list[str]:
    """Files whose *name* the map section never mentions.

    Name, not row: several files are named inside a directory row's description (`conftest.py` in the
    `cli/tests/` row, the three `references/` files in theirs), and demanding a row each would be a
    layout rule rather than a documentation one.
    """

    return [
        f"{label}/{path.name}"
        for label, paths in groups.items()
        for path in paths
        if path.name not in map_text
    ]


def _file_size_problems(rows: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for path, description in sorted(rows.items()):
        for stated in FILE_SIZE_CLAIM.findall(description):
            target = REPO / path
            if not target.is_file():
                problems.append(f"{path}: the map states a size for a path that is not a file")
                continue
            actual = len(target.read_text(encoding="utf-8").splitlines())
            if actual != int(stated):
                problems.append(f"{path}: the map says {stated} lines; the file has {actual}")
    return problems


def test_every_document_and_manifest_is_listed_in_the_repo_map() -> None:
    """The map's completeness was guarded for two directories and assumed for the others."""

    mapped = _repo_map_rows(AGENTS.read_text(encoding="utf-8"))
    map_text = _repo_map_section(AGENTS.read_text(encoding="utf-8"))
    assert mapped and map_text, "the repo map is gone; this guard is about nothing"

    groups = {
        "docs": sorted((REPO / "docs").glob("*.md"))
        + sorted((REPO / "docs" / "schema").glob("*.md"))
        + sorted((REPO / "docs" / "broker").glob("*.md")),
        "references": sorted((REPO / "references").glob("*.md")),
        "cli/extensions": sorted((REPO / "cli" / "extensions").glob("*.json")),
        "cli/tests": sorted(
            path for path in (REPO / "cli" / "tests").glob("*.py") if not path.name.startswith("test_")
        ),
    }
    for label, paths in groups.items():
        assert paths, f"{label} holds no files, so this guard is not looking at anything"

    missing = _repo_map_missing(groups, map_text)
    assert missing == [], f"files the repo map does not name: {missing}"

    # Non-vacuity: an unnamed file must be reported, and an already-mapped one must not be.
    assert _repo_map_missing({"seeded": [Path("seeded.md")]}, map_text) == ["seeded/seeded.md"]
    assert _repo_map_missing({"seeded": [Path("SKILL.md")]}, map_text) == []


def test_every_file_size_the_repo_map_states_is_the_file_size() -> None:
    """`（2236 行）` was thirteen lines out of date, and nothing was comparing it."""

    rows = _repo_map_rows(AGENTS.read_text(encoding="utf-8"))
    assert any(FILE_SIZE_CLAIM.search(description) for description in rows.values()), (
        "the map no longer states a file size; this guard is about nothing"
    )
    assert _file_size_problems(rows) == [], "; ".join(_file_size_problems(rows))

    # Non-vacuity: the claim §85 found, made stale again — **derived**, not written out. The literal
    # `主规划（2249 行）` was correct for one round; §86 changed the file and the mutation silently
    # stopped applying. Same lesson as the count-chain mutation twice over: a mutation copied from
    # today's text is a mutation that expires.
    claims = {
        path: int(match.group(1))
        for path, description in rows.items()
        for match in FILE_SIZE_CLAIM.finditer(description)
    }
    assert claims, "the map states no file size, so this mutation has nothing to shorten"
    text = AGENTS.read_text(encoding="utf-8")
    sized_path, stated = sorted(claims.items())[0]
    stale = text.replace(f"（{stated} 行）", f"（{stated - 1} 行）", 1)
    assert stale != text and _file_size_problems(_repo_map_rows(stale)) != [], (
        "a stale file size must be reported"
    )
    globbed = _repo_map_rows(text.replace(f"| `{sized_path}` |", "| `docs/*.md` |", 1))
    assert _file_size_problems(globbed) != [], "a size claimed for something that is not a file must be reported"


def _decision_log_range_problems(rows: dict[str, str], adrs: list[str]) -> list[str]:
    row = rows.get("docs/AIROOT-v0.3-实现决策记录.md", "")
    if not row:
        return ["the decision log is not in the repo map"]
    return [f"the row does not name {edge}" for edge in (adrs[0], adrs[-1]) if edge not in row]


def test_the_repo_map_states_the_decision_log_range_not_a_hand_copy_of_it() -> None:
    """The row named three ADRs of twenty-five, and read as though that were the contents."""

    rows = _repo_map_rows(AGENTS.read_text(encoding="utf-8"))
    log = (REPO / "docs" / "AIROOT-v0.3-实现决策记录.md").read_text(encoding="utf-8")
    adrs = re.findall(r"(?m)^## (ADR-\d+)", log)
    assert len(adrs) >= 2, "the decision log has no ADR range; this guard is about nothing"

    assert _decision_log_range_problems(rows, adrs) == [], "; ".join(
        _decision_log_range_problems(rows, adrs)
    )

    # Non-vacuity: the row §85 found, whose newest named ADR was twenty-one behind the log, and a map
    # with no row for the log at all.
    stale = dict(rows)
    stale["docs/AIROOT-v0.3-实现决策记录.md"] = rows["docs/AIROOT-v0.3-实现决策记录.md"].replace(
        adrs[-1], "ADR-0004"
    )
    assert _decision_log_range_problems(stale, adrs) == [f"the row does not name {adrs[-1]}"]
    assert _decision_log_range_problems({k: v for k, v in rows.items() if "决策记录" not in k}, adrs) == [
        "the decision log is not in the repo map"
    ]


def _delegation_problems(rows: dict[str, str], low: str, high: str, target: str) -> list[str]:
    row = rows.get(target, "")
    if not row:
        return [f"{target} is not in the repo map"]
    return [f"the row does not state §{edge}" for edge in (low, high) if f"§{edge}" not in row]


def test_the_repo_map_agrees_with_the_sentence_that_delegates_the_stage_records() -> None:
    """§1 hands the stage records to a file; that file's map row must not deny them."""

    text = AGENTS.read_text(encoding="utf-8")
    match = STAGE_DELEGATION.search(text)
    assert match is not None, "the entry document no longer delegates its stage records"
    low, high, target = match.group(1), match.group(2), match.group(3)

    rows = _repo_map_rows(text)
    assert _delegation_problems(rows, low, high, target) == [], "; ".join(
        _delegation_problems(rows, low, high, target)
    )

    # Non-vacuity in both directions: the row that denied the range (the §85 defect), and a
    # delegation that moved to a file whose row was never updated.
    denying = dict(rows)
    denying[target] = rows[target].replace(f"§{low}–§{high} 是每一阶段的实现记录", "**提案（未落地，第二版）**", 1)
    assert _delegation_problems(denying, low, high, target) == [
        f"the row does not state §{low}",
        f"the row does not state §{high}",
    ]
    assert _delegation_problems(rows, "31", "99", target) == ["the row does not state §99"]
    assert _delegation_problems({k: v for k, v in rows.items() if k != target}, low, high, target) == [
        f"{target} is not in the repo map"
    ]


# --- Guard group 28: the "not decided yet" lists and the ADR that decided them (draft §86) -------
#
# Two documents keep a list of what P1 has not decided: the master plan §23 ("实现前仍需由评审记录
# 以下选择") and the decision log's own list. §82 annotated the log's list with the ADR-0025 decisions
# that settled each item — and left the plan's list untouched, under a header that claimed the two
# lists were the same one. §86 measured it: they never were. Six items in the plan, eight in the log,
# three in common. The plan's three独有 items (the capability list, Everything, the first real
# artifact) had been **decided by ADR-0025 and were absent from the log's list entirely**, so a reader
# following the log would never learn they had been answered.
#
# Two rules. The plan's list is layer-3 authority and is now fully answered, so every item must say
# what settled it. And the nine decisions ADR-0025 took must each be reachable from one of the two
# lists — a decision nobody can find from the question it answered is a decision filed in a drawer.

#: The sentence that introduces the plan's open choices. Anchored on the sentence, not on `## 23.`,
#: so renumbering the section cannot make this guard look at the wrong list.
PLANNING_OPEN_CHOICES = "实现前仍需由评审记录以下选择"
DECISION_LOG_OPEN_QUESTIONS = "## 尚未决策"


def _numbered_items(block: str) -> list[str]:
    """Numbered items with their **indented** continuation lines joined.

    The annotations live on continuation lines in the log's list (item 4 names its decision three
    lines down), so an item read as one line would look unanswered.
    """

    items: list[str] = []
    for line in block.splitlines():
        if re.match(r"^\d+\. ", line):
            items.append(line)
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1] += " " + line.strip()
    return items


def _decision_ids(text: str) -> set[int]:
    """Top-level decision numbers: `D8-1` counts as `D8`, and `D10` is not read as `D1`."""

    return {int(value) for value in re.findall(r"\bD([1-9])\b", text)}


def _settling_adr_section(text: str) -> str:
    """ADR-0025's body, located by a **line-anchored** heading.

    Splitting on the bare string finds it inside ADR-0024 first: that entry's "明确不做" bullet names
    it in backticks. §82's own helper hit the same thing and the same fix applies.
    """

    match = re.search(r"(?m)^## ADR-0025", text)
    if match is None:
        return ""
    return re.split(r"(?m)^## ", text[match.end() :], maxsplit=1)[0]


#: A decision heading. `(?!\\w)` and not `\\b` after the digits: `### D4x` must not read as `### D4`,
#: which is exactly the mutation this guard's own non-vacuity check applies.
DECISION_HEADING = re.compile(r"(?m)^### D(\d+)(?!\w)")


def _unnamed_open_choices(block: str) -> list[int]:
    """1-based positions of list items that do not say which decision answered them."""

    return [index for index, item in enumerate(_numbered_items(block), 1) if not _decision_ids(item)]


def _unreachable_decisions(blocks: list[str], adr_text: str) -> list[str]:
    """Decisions the ADR took that no open-question list names, and names with no decision behind."""

    expected = {int(value) for value in DECISION_HEADING.findall(adr_text)}
    named: set[int] = set()
    for block in blocks:
        named |= _decision_ids(block)
    problems = [f"D{value} is not named by any open-question list" for value in sorted(expected - named)]
    problems += [f"D{value} is named but ADR-0025 has no such decision" for value in sorted(named - expected)]
    return problems


def test_the_plans_open_choices_each_name_what_settled_them() -> None:
    """§23 is layer-3 authority, so an item there that nobody answered is a rule with no answer."""

    text = PLANNING.read_text(encoding="utf-8")
    block = text.split(PLANNING_OPEN_CHOICES, 1)[1].split("\n## ", 1)[0]
    items = _numbered_items(block)
    assert len(items) >= 4, f"the plan's open-choices list has {len(items)} items; this guard is about it"

    unnamed = _unnamed_open_choices(block)
    assert unnamed == [], (
        f"items {unnamed} of the plan's open-choices list do not name the decision that settled them; "
        "a list of questions that does not say which ones were answered reads as all-open"
    )

    adr = _settling_adr_section((REPO / "docs" / "AIROOT-v0.3-实现决策记录.md").read_text(encoding="utf-8"))
    assert adr, "ADR-0025 is gone; these lists point at a decision that does not exist"
    settled = {int(value) for value in DECISION_HEADING.findall(adr)}
    dangling = sorted(_decision_ids(block) - settled)
    assert dangling == [], f"the plan names decisions ADR-0025 does not have: {dangling}"

    # Non-vacuity: the list §86 found, whose six items named no decision at all, and one item losing
    # its annotation while the others keep theirs.
    stripped = re.sub(r"（\*\*D\d+(?:-\d)?\*\*", "（", block)
    assert stripped != block and len(_unnamed_open_choices(stripped)) == len(items)
    one = re.sub(r"（\*\*D9\*\*", "（", block, count=1)
    assert one != block and _unnamed_open_choices(one) == [1]


def test_every_decision_is_reachable_from_an_open_question_list() -> None:
    """A decision filed away from the question it answered is a decision nobody will find."""

    plan = PLANNING.read_text(encoding="utf-8")
    log = (REPO / "docs" / "AIROOT-v0.3-实现决策记录.md").read_text(encoding="utf-8")
    blocks = [
        plan.split(PLANNING_OPEN_CHOICES, 1)[1].split("\n## ", 1)[0],
        log.split(DECISION_LOG_OPEN_QUESTIONS, 1)[1],
    ]
    adr = _settling_adr_section(log)
    assert adr and all(block.strip() for block in blocks), "an open-question list is gone"

    assert _unreachable_decisions(blocks, adr) == [], "; ".join(_unreachable_decisions(blocks, adr))

    # Non-vacuity in both directions: a decision whose heading stops being a heading, and a list that
    # stops naming one.
    assert _unreachable_decisions(blocks, adr.replace("### D4 ", "### D4x ", 1)) != [], (
        "a decision with no heading must be reported"
    )
    assert _unreachable_decisions([re.sub(r"\bD7\b", "D9", block) for block in blocks], adr) != [], (
        "an open-question list that stops naming a decision must be reported"
    )


# --- Guard group 29: the plan's `where` result list against the corpus (draft §87) ---------------
#
# The validation plan's §8 said "每一种结果固定 JSON fixture" and then listed eight names. §87 put
# that list beside the corpus: two of the eight (`session_required`, `recovery_required`) are **not
# `where` results at all** — one does not exist in this build, the other belongs to `doctor` and the
# transaction states — while two results that do exist (`broken` with no fallback, and
# `UNMANAGED_ONLY`) had no fixture, and two fixtures had no entry in the list. A coverage list that is
# never reconciled with the corpus ends up describing a smaller set than the code, in both directions:
# this is the §86 defect one layer down, in the test plan instead of the decision log.

#: The sentence that owns the table. Anchored on the sentence, not the heading, so renaming the
#: section cannot quietly point this guard at another table.
WHERE_FIXTURE_TABLE = "每种结果固定一个 JSON fixture"
WHERE_FIXTURE_ROW = re.compile(r"(?m)^\|\s*[^|]+\|\s*`(where_[a-z0-9_]+)`\s*\|\s*(\d+)\s*\|")


def _where_fixture_problems(block: str, corpus: dict[str, int]) -> list[str]:
    """Both directions, plus the exit code the table states for each fixture."""

    listed = {name: int(code) for name, code in WHERE_FIXTURE_ROW.findall(block)}
    problems = [f"{name} is in the plan but has no fixture" for name in sorted(set(listed) - set(corpus))]
    problems += [f"{name} is in the corpus but not in the plan" for name in sorted(set(corpus) - set(listed))]
    problems += [
        f"{name}: the plan says exit {code}, the corpus index says {corpus[name]}"
        for name, code in sorted(listed.items())
        if name in set(corpus) and corpus[name] != code
    ]
    return problems


def test_the_where_result_list_and_the_corpus_are_the_same_set() -> None:
    """A coverage list and the corpus it describes must be each other's whole truth."""

    plan = VALIDATION.read_text(encoding="utf-8")
    block = plan.split(WHERE_FIXTURE_TABLE, 1)[1].split("\n## ", 1)[0]
    index = json.loads((GOLDEN / "index.json").read_text(encoding="utf-8"))
    corpus = {name: code for name, code in index.items() if name.startswith("where_")}

    assert len(corpus) >= 4, f"the corpus has {len(corpus)} where fixtures; this guard is about them"
    assert _where_fixture_problems(block, corpus) == [], "; ".join(_where_fixture_problems(block, corpus))

    # Non-vacuity, one mutation per direction, including the exit-code column: a wrong exit code in an
    # agent-facing document is exactly the defect §36 found surviving a green suite.
    assert _where_fixture_problems(block.replace("`where_broken`", "`where_teleport`", 1), corpus) != []
    assert _where_fixture_problems(block.replace("| `where_healthy` | 0 |", "", 1), corpus) != []
    assert _where_fixture_problems(block.replace("| `where_healthy` | 0 |", "| `where_healthy` | 7 |", 1), corpus) != []


# --- Guard group 30: a fixture's recorded exit code against its own bytes (draft §88) ------------
#
# `index.json` records one exit code per fixture, and every document carries the machine-readable
# outcome that decides it. Nothing compared the two. So `doctor_healthy` shipped a document whose
# `status` was `degraded` with an index entry of **0** — a fixture whose recorded exit code
# contradicted its own bytes — and `healthy` was consequently the one doctor status the validation
# plan's §14 ("`where`/`doctor` 的所有机器可读结果都有固定 fixture") could not point at. A port that
# reproduced "exit 0 with a degraded document" would have been graded correct against this corpus.

GOLDEN_INDEX = GOLDEN / "index.json"
#: `reason = "..."` and `reason = SOME_CONSTANT` in `caps/where.py`; the constants are resolved, so
#: `DEGRADED_TO_REFERENCE` counts as the code it holds rather than as a second name for it. The
#: lookbehind matters: `selection_reason = UNMANAGED_ONLY` contains `reason = UNMANAGED_ONLY`, and
#: without it this scanner reports four selection reasons as exit-code-bearing outcomes.
WHERE_CONSTANT = re.compile(r"(?m)^([A-Z_]+) = \"([A-Z_]+)\"")
WHERE_REASON_LITERAL = re.compile(r"(?<![A-Za-z_])reason = \"([A-Z_]+)\"")
WHERE_REASON_NAME = re.compile(r"(?<![A-Za-z_])reason = ([A-Z_]+)\b")


def _where_reason_codes(source: str) -> set[str]:
    constants = dict(WHERE_CONSTANT.findall(source))
    codes = set(WHERE_REASON_LITERAL.findall(source))
    codes |= {constants[name] for name in WHERE_REASON_NAME.findall(source) if name in constants}
    return codes


def _recorded_exit_code_problems(index: dict[str, int], documents: dict[str, dict[str, Any]]) -> list[str]:
    """The code the index records must be the one the document's own outcome derives.

    Two derivations, because the two response families state their outcome differently: a
    `reason_code` (any document that carries one — `where` and `search` both do) or a doctor
    `status`. A document that states neither is not constrained here, and §88 records which one
    fixture that leaves out.
    """

    from airoot.caps.doctor import status_exit_code
    from airoot.exits import exit_code_for

    problems: list[str] = []
    for name, recorded in sorted(index.items()):
        document = documents.get(name)
        if document is None:
            problems.append(f"{name} is in the index but has no document")
            continue
        code = document.get("reason_code")
        if code is not None:
            derived, basis = exit_code_for(code), f"reason_code {code}"
        elif name.startswith("doctor_") and "status" in document:
            derived, basis = status_exit_code(document["status"]), f"status {document['status']}"
        else:
            continue
        if derived != recorded:
            problems.append(f"{name}: the index says {recorded}, its own {basis} says {derived}")
    return problems


def _outcome_coverage_problems(
    documents: dict[str, dict[str, Any]], statuses: set[str], reason_codes: set[str]
) -> list[str]:
    """§14's sentence, made checkable: every machine-readable result has a fixed fixture."""

    seen_statuses = {doc["status"] for name, doc in documents.items() if name.startswith("doctor_")}
    seen_codes = {doc["reason_code"] for name, doc in documents.items() if name.startswith("where_")}
    problems = [f"no doctor fixture reports status {value}" for value in sorted(statuses - seen_statuses)]
    problems += [f"no where fixture reports reason code {value}" for value in sorted(reason_codes - seen_codes)]
    return problems


def _golden_documents() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(GOLDEN.glob("*.json"))
        if path.name != "index.json"
    }


def test_every_fixture_records_the_exit_code_its_own_outcome_derives() -> None:
    """A corpus whose indices and bytes disagree grades a wrong port as correct."""

    index = json.loads(GOLDEN_INDEX.read_text(encoding="utf-8"))
    documents = _golden_documents()
    assert len(index) >= 20, f"the corpus index has {len(index)} entries; this guard is about them"

    assert _recorded_exit_code_problems(index, documents) == [], "; ".join(
        _recorded_exit_code_problems(index, documents)
    )

    # Non-vacuity: the exact defect §88 found, restored, plus a swapped reason code and an index
    # entry with no document.
    stale = dict(index, doctor_healthy=0)
    documents["doctor_healthy"] = {**documents["doctor_healthy"], "status": "degraded"}
    assert _recorded_exit_code_problems(stale, documents) != [], "a contradictory index entry must be reported"
    swapped = {**documents, "where_broken": {**documents["where_broken"], "reason_code": "NOT_FOUND"}}
    assert _recorded_exit_code_problems(index, swapped) != [], "a swapped reason code must be reported"
    assert _recorded_exit_code_problems({**index, "ghost_fixture": 0}, documents) != [], (
        "an index entry with no document must be reported"
    )


def test_every_machine_readable_outcome_has_a_fixture() -> None:
    """§14 requires a fixed fixture per result; the statuses and codes are the results."""

    documents = _golden_documents()
    schema = load_schema("doctor-response")
    statuses = set(schema["properties"]["status"]["enum"])
    reason_codes = _where_reason_codes((APP / "caps" / "where.py").read_text(encoding="utf-8"))
    assert statuses and reason_codes, "a vocabulary came back empty; this guard is about nothing"

    assert _outcome_coverage_problems(documents, statuses, reason_codes) == [], "; ".join(
        _outcome_coverage_problems(documents, statuses, reason_codes)
    )

    # Non-vacuity: a status and a reason code nothing covers.
    assert _outcome_coverage_problems(documents, statuses | {"unmanaged"}, reason_codes) != []
    assert _outcome_coverage_problems(documents, statuses, reason_codes | {"TELEPORT_FAILED"}) != []


#: Every enum `search-response` declares, as `{documented path: values}` — **derived from the schema**,
#: not listed here. §89 covered `status`, §95 covered `data.freshness.coverage`, and §95 recorded the
#: weakness of that: a third such field would arrive with nothing to remind anyone. Walking the schema
#: removes the hand list; §96 then found three values it had been hiding (`results[].kind = directory`,
#: `results[].verification = verified` / `changed`), each produced by a different request field.
def _search_response_enums() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def walk(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("enum"), list):
            found[prefix] = [str(item) for item in node["enum"]]
        for name, child in (node.get("properties") or {}).items():
            walk(child, f"{prefix}.{name}" if prefix else name)
        if "items" in node:
            walk(node["items"], f"{prefix}[]")

    walk(load_schema("search-response"), "")
    return found


def _values_at(document: dict[str, Any], path: str) -> set[str]:
    """Every value `path` (`a.b[].c`) holds in one document, with the `[]` segment draining lists."""

    nodes: list[Any] = [document]
    for segment in path.split("."):
        drained = segment.endswith("[]")
        key = segment[:-2] if drained else segment
        nxt: list[Any] = []
        for node in nodes:
            if not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if drained:
                if isinstance(value, list):
                    nxt.extend(value)
            else:
                nxt.append(value)
        nodes = nxt
    return {node for node in nodes if isinstance(node, str)}


def _search_field_coverage_problems(
    documents: dict[str, dict[str, Any]], path: str, values: set[str], daggered: set[str]
) -> list[str]:
    """Every value of `path` needs a fixture, unless the field table says nothing writes it."""

    seen: set[str] = set()
    for name, document in documents.items():
        if name.startswith("search_"):
            seen |= _values_at(document, path)
    return [f"no search fixture reports {path} = {value}" for value in sorted(values - seen - daggered)]


@pytest.mark.parametrize("path", sorted(_search_response_enums()))
def test_every_search_result_value_has_a_fixture_or_a_written_exception(path: str) -> None:
    """Every enum `search-response` declares, one case each, with the exceptions read from the doc.

    §89 asked this of `status` and §95 of `data.freshness.coverage`; both wrote their field list by
    hand. This walks the schema instead, so a new enum — or a new value on an existing one — is covered
    or red without anyone remembering to add a line. The values and the excuses still come from
    `references/field-values.md`, whose own guard ties them to the same schema.
    """

    from test_l1_field_values import ROWS

    declared = _search_response_enums()[path]
    row = next(
        (item for item in ROWS if item.schema == "search-response" and item.path == path), None
    )
    assert row is not None, f"field-values.md no longer documents {path}"
    values, daggered = set(row.values), set(row.daggers)
    # Not a restatement of the table: the two sources have to agree before either can be used.
    assert values == set(declared), f"{path}: doc={sorted(values)} schema={sorted(declared)}"

    documents = _golden_documents()
    assert _search_field_coverage_problems(documents, path, values, daggered) == [], "; ".join(
        _search_field_coverage_problems(documents, path, values, daggered)
    )

    # Non-vacuity, one mutation per direction: an uncovered value, and a corpus that answers nothing.
    assert _search_field_coverage_problems(documents, path, values | {"wat"}, daggered) != []
    assert _search_field_coverage_problems({}, path, values, set()) == [
        f"no search fixture reports {path} = {value}" for value in sorted(values)
    ], "an empty corpus has to report every value"
    if daggered:
        # Only meaningful where the document actually excuses a value: dropping the daggers must
        # reveal that the corpus does not carry them either.
        assert _search_field_coverage_problems(documents, path, values, set()) != [], (
            "the dagger list has to be load-bearing, or this guard is really about nothing"
        )
    else:
        assert _search_field_coverage_problems(documents, path, values, set()) == [], (
            f"{path} has no daggered value, so the corpus must already carry all of them"
        )
    assert _values_at({"a": {"b": [{"c": "x"}]}}, "a.b[].c") == {"x"}, (
        "the path walker has to reach a value through a list, or this guard checks nothing"
    )


# --- Guard group 32: printed documents and acceptance fixtures are the same set (draft §90) ------
#
# `schema_io.validate_self` is called before the core prints an outward document (AGENTS.md §7), so
# the names passed to it are exactly "the documents a port must reproduce" — that set is derived from
# the source, not restated here. Crossed with the corpus, §90 found it wrong in **both** directions:
#
#   * `plan` (what gets approved and executed) and `managed-tool-instance` (what gets registered and
#     bound) are self-validated and printed, and had no fixture at all;
#   * `reference-plan` had a fixture and was printed by `cli.py`, but the **built** path never
#     self-validated it — only the `--plan-file` path did, on load.
#
# So the rule is exact equality, which is the only form that catches both.

SELF_VALIDATED = re.compile(r'_?validate_self\("([^"]+)"')


def _self_validated_schemas() -> set[str]:
    found: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        found |= set(SELF_VALIDATED.findall(path.read_text(encoding="utf-8")))
    return found


def _printed_vs_fixture_problems(printed: set[str], covered: set[str]) -> list[str]:
    return [f"{name} is self-validated but has no fixture" for name in sorted(printed - covered)] + [
        f"{name} has a fixture but the core never self-validates it" for name in sorted(covered - printed)
    ]


def test_the_documents_the_core_prints_and_the_corpus_are_the_same_set() -> None:
    """A printed document with no fixture has no acceptance face; a fixture with no printer is a wish."""

    from test_golden import SCHEMA_FOR_FIXTURE

    printed = _self_validated_schemas()
    covered = set(SCHEMA_FOR_FIXTURE.values())
    assert len(printed) >= 5, f"only {len(printed)} self-validated schemas found; is the scan broken?"
    assert covered, "no fixture declares a schema"

    assert _printed_vs_fixture_problems(printed, covered) == [], "; ".join(
        _printed_vs_fixture_problems(printed, covered)
    )

    # Non-vacuity, one mutation per direction: a new printed document, and coverage that grew a wish.
    assert _printed_vs_fixture_problems(printed | {"ghost-response"}, covered) == [
        "ghost-response is self-validated but has no fixture"
    ]
    assert _printed_vs_fixture_problems(printed, covered | {"runtime-instance"}) == [
        "runtime-instance has a fixture but the core never self-validates it"
    ]


# --- Guard group 34: a schema is either used, or declared unwritten with a reason (draft §92) -----
#
# Group 32 asked which documents the core **prints**. This asks the wider question §90 answered in
# prose: which published schemas is *any* code using at all?
#
# §90 wrote "six are never named by any code at all" — a claim measured only from `validate_self` call
# sites, and it is not true of `desired-manifest`: `caps/desired.py` names it when it rejects a file
# that fails *that* schema, while the file it writes and reads is not that document at all (ADR-0026).
# A narrower measurement had been read as a wider statement, which is the same defect §85 found in the
# repo map's file-size literal.
#
# Two ways to be "used", both derived from artefacts: code passes the name to `validate_self` /
# `validate_document`, or another schema `$ref`s it (that is `common`). Everything left over has no
# writer in this build, and each of those says why in the catalog — because "published ahead of
# implementation" and "describes a document nobody writes" look identical from outside, and §92 found a
# reference document that had confused the two.

VALIDATORS = ("validate_self", "validate_document")

#: A declaration bullet in `docs/schema/README.md`: ``- `gc-plan.schema.json` — why``. A **bullet**, not
#: a table row: §80's boundary-table parser accepts any row shaped `| \`x.schema.json\` | ... |`, so a
#: second table there would silently join the boundary table (the same reason §90 used prose).
UNWRITTEN_DECLARATION = re.compile(r"(?m)^- `([a-z0-9-]+)\.schema\.json` — (.+)$")

#: The section of `references/field-values.md` that documents the envelope's `operation` label.
OPERATION_SECTION = re.compile(r"(?m)^### `operation`")
OPERATION_ROW = re.compile(r"(?m)^\| `([a-z_]+)` \| (.+?) \| `?([^|`]+)`? \|$")


def _schema_stems() -> set[str]:
    return {name[: -len(".schema.json")] for name in schema_names()}


def _used_schema_names() -> set[str]:
    """Derived: schemas code validates by literal name, plus schemas another schema `$ref`s."""

    import ast

    used: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in VALIDATORS and node.args and isinstance(node.args[0], ast.Constant):
                used.add(str(node.args[0].value))
    for stem in _schema_stems():
        body = json.dumps(load_schema(stem), sort_keys=True)
        used |= {
            match.group(1)
            for match in re.finditer(r'"\$ref": "([a-z0-9-]+)\.schema\.json', body)
            if match.group(1) != stem
        }
    return used


def _declared_unwritten_schemas() -> dict[str, str]:
    return {
        match.group(1): match.group(2).strip()
        for match in UNWRITTEN_DECLARATION.finditer(SCHEMA_README.read_text(encoding="utf-8"))
    }


def _unwritten_schema_problems(used: set[str], declared: dict[str, str], every: set[str]) -> list[str]:
    return (
        [f"{name} is declared to have no writer, but this build uses it" for name in sorted(used & set(declared))]
        + [
            f"{name} is neither used by any code nor declared as having no writer"
            for name in sorted(every - used - set(declared))
        ]
        + [
            f"{name} is declared as having no writer but is not a published schema"
            for name in sorted(set(declared) - every)
        ]
        + [
            f"{name} is declared as having no writer with no reason given"
            for name in sorted(name for name, reason in declared.items() if not reason)
        ]
    )


def test_every_published_schema_is_used_or_declared_unwritten() -> None:
    used = _used_schema_names()
    declared = _declared_unwritten_schemas()
    every = _schema_stems()

    problems = _unwritten_schema_problems(used, declared, every)
    assert problems == [], "; ".join(problems)
    assert len(used) >= 10, f"only {len(used)} schemas look used; the walk is no longer reaching them"
    assert declared, "no schema is declared unwritten; the derivation must have gone wrong"

    # Non-vacuity, one mutation per direction: a declaration dropped while the schema is still unused,
    # a schema that becomes used while remaining declared, and a declaration for nothing.
    assert _unwritten_schema_problems(used, {k: v for k, v in declared.items() if k != "gc-plan"}, every) != []
    assert _unwritten_schema_problems(used | {"gc-plan"}, declared, every) != []
    assert _unwritten_schema_problems(used, {**declared, "ghost-schema": "why not"}, every) != []
    assert _unwritten_schema_problems(used, {**declared, "runtime-instance": ""}, every) != []


def _producer_claim_problems(declared: dict[str, str]) -> list[str]:
    """A schema nobody writes must not be given writers by the value table (draft §92)."""

    from test_l1_field_values import ROWS

    return [
        f"field-values.md names {row.producers} as writers of {row.schema}, which no code builds"
        for row in ROWS
        if row.schema in declared and row.producers
    ]


def test_a_schema_declared_unwritten_is_not_claimed_to_have_a_writer() -> None:
    """§92's real defect: `gc-plan` was credited to three modules that write a **plan** instead.

    The value table's own guard searches the named files for the value *string*, so a field that
    reuses the same words (`managed_tool`, `runtime`) passes while the field it names is never written
    — the same-word false coverage the draft already records (§74/§77). This is the check that closes
    the direction that actually lied.
    """

    declared = _declared_unwritten_schemas()
    problems = _producer_claim_problems(declared)
    assert problems == [], "; ".join(problems)

    # Non-vacuity: a declared-unwritten schema that **does** have rows crediting writers must be
    # reported. `registry-projection` is the control — its rows legitimately name producers, so it
    # fails the moment it is declared unwritten, which is what makes this check load-bearing rather
    # than a restatement of the table. (§92 also verified it red against the real defect, by putting
    # `gc-plan`'s three modules back.)
    assert _producer_claim_problems({**declared, "registry-projection": "pretend"}) != [], (
        "a schema declared unwritten while the value table credits writers must be reported"
    )


def _operation_label_rows() -> dict[str, str]:
    """The envelope `operation` table from `references/field-values.md`: label -> meaning cell."""

    body = OPERATION_SECTION.split(FIELD_VALUES.read_text(encoding="utf-8"))[-1].split("\n### ", 1)[0]
    return {match.group(1): match.group(2) for match in OPERATION_ROW.finditer(body)}


def _printed_operation_labels() -> set[str]:
    """Every `"operation": "..."` literal the app writes — envelope labels and plan operations both.

    Deliberately a superset: `plan`'s own `operation` field is a *different* field with its own
    vocabulary (field-values.md says so), and including it can only make this check stricter.
    """

    import ast

    labels: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "operation"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    labels.add(value.value)
    return labels


def _label_collision_problems(labels: set[str], table: dict[str, str]) -> list[str]:
    """A label and a schema may share a name, but then the row has to say they are not the same thing.

    §92 measured exactly one such pair in this build: the `gc_plan` envelope label and
    `gc-plan.schema.json` — a batch collection plan that no code writes, while the slice's real
    collection plan is a `plan`. A reader who looks the label up in the catalog lands on the wrong
    contract, and nothing said so.
    """

    problems: list[str] = []
    for stem in sorted(_schema_stems()):
        for label in sorted(labels):
            if label.replace("_", "-") != stem:
                continue
            if label not in table:
                problems.append(
                    f"`{label}` is both an operation label and the name of {stem}.schema.json: "
                    "declare it in the value table and say which one it is"
                )
            elif f"{stem}.schema.json" not in table[label]:
                problems.append(
                    f"`{label}` shares its name with {stem}.schema.json: the row must name that schema "
                    "and say the label is not it"
                )
    every = _schema_stems()
    named = {
        match.group(1)
        for cell in table.values()
        for match in re.finditer(r"([a-z0-9-]+)\.schema\.json", cell)
    }
    problems += [
        f"the operation table names {name}.schema.json, which is not a published schema"
        for name in sorted(named - every)
    ]
    return problems


def test_an_operation_label_that_shares_a_schema_name_says_so() -> None:
    table = _operation_label_rows()
    labels = _printed_operation_labels()

    assert len(labels) >= 8, f"only {len(labels)} operation literals found; the walk is broken"
    assert len(table) >= 8, f"only {len(table)} operation rows parsed; the section moved"
    assert _label_collision_problems(labels, table) == [], "; ".join(
        _label_collision_problems(labels, table)
    )

    # Non-vacuity: the collision that exists, with its declaration removed; a new collision with no
    # row at all; and a row pointing at a schema that does not exist.
    stripped = dict(table)
    stripped["gc_plan"] = stripped["gc_plan"].replace("gc-plan.schema.json", "the gc report")
    assert _label_collision_problems(labels, stripped) != [], "a collision with no declaration must be reported"
    assert _label_collision_problems(labels | {"plan"}, table) != [], "a new collision must be reported"
    assert _label_collision_problems(labels, {**table, "gc_plan": "see ghost.schema.json"}) != [], (
        "a row naming a schema that does not exist must be reported"
    )


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
        PLANNING,
        DRAFT,
        DECISION_LOG,
        REPO / "docs" / "AIROOT-v0.3-三大核心契约方案.md",
        SKILL,
        AGENTS,
        AGENT_META,
    ] + sorted((REPO / "references").glob("*.md"))
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in documents)

    undocumented = sorted(
        entry for entry in implemented_commands() if not names_token(corpus, entry, word="A-Za-z0-9_-")
    )
    assert undocumented == [], f"implemented commands nobody documented: {undocumented}"


def test_the_vocabulary_helper_rejects_a_name_nested_in_a_longer_word() -> None:
    """§76: the helper above is the only thing standing between a vocabulary and a false pass.

    The negative cases cover **both** directions, which the first version of this test did not: it
    only nested the name at the *end* of a longer word (`telescope`, `checklist`), so a helper that
    had lost its trailing boundary — the one that catches `scopes` and `listings` — still passed.
    The mutation that dropped that boundary is how the gap was found (see §76 of the draft).
    """

    assert not names_token("a telescope is not a command", "scope")
    assert not names_token("telescope", "scope", word="A-Za-z0-9_-")
    assert not names_token("scopes are a different word", "scope", word="A-Za-z0-9_-")
    assert not names_token("checklist", "list", word="A-Za-z0-9_-")
    assert not names_token("listings", "list", word="A-Za-z0-9_-")
    assert not names_token("CURRENT_SOURCE_DEGRADED", "DEGRADED")
    assert not names_token("DEGRADED_SOMETHING", "DEGRADED")
    assert not names_token("needs-capability", "capability", word="A-Za-z0-9_-")

    assert names_token("run `airoot scope decide java`", "scope", word="A-Za-z0-9_-")
    assert names_token("`DEGRADED` is a code", "DEGRADED")
    assert names_token("data-root add D:\\env", "add", word="A-Za-z0-9_-")


#: Names in this module that hold *document text*. A membership test of a variable against one of
#: these is a substring check on prose, which is exactly what `names_token` exists to replace.
DOCUMENT_NAMES = frozenset(
    {"text", "agents", "corpus", "body", "skill_text", "draft_text", "prose", "document_text"}
)


def test_the_audit_module_asks_vocabulary_questions_through_the_helper() -> None:
    """§76: pinning the helper is not enough — a guard can quietly stop using it.

    Both idioms that produced §75 and §76 are visible in the module's own syntax, so this reads the
    module's AST rather than trusting a reviewer to notice a reverted line: a *variable* membership
    test against a document (`code not in text`), and a `re.search` whose pattern carries a
    one-sided `\\b`. Literal phrase checks (`"--token-file" in text`) are deliberately allowed — they
    ask "is this block still about that thing?", not "is this vocabulary documented?".
    """

    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Name) and operand.id in DOCUMENT_NAMES:
                    other = node.comparators[0] if operand is node.left else node.left
                    if isinstance(other, ast.Name):
                        offenders.append(ast.unparse(node))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "search"
            and node.args
        ):
            literal = node.args[0]
            pieces = [
                item.value
                for item in ast.walk(literal)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            if any("\\b" in piece for piece in pieces) and not any("(?<!" in piece for piece in pieces):
                offenders.append(ast.unparse(node)[:90])
    offenders = sorted(set(offenders) - SUBSTRING_CHECKS_ARE_FINE)
    assert offenders == [], (
        "these vocabulary checks bypass names_token (use it, or explain why a substring is the "
        "right question): " + "; ".join(offenders)
    )


#: `variable in document` checks that are deliberately **not** about a vocabulary token, so a plain
#: substring is the right question. Each needs a reason; a new one has to be added here on purpose.
SUBSTRING_CHECKS_ARE_FINE = frozenset(
    {
        # A whole-sentence honesty claim: the question is "is this sentence present?", and a longer
        # string cannot make a shorter sentence look present by accident.
        "ISSUER_PENDING in text",
        # Section headings and their neighbours: `heading in text` asks "did this section survive?",
        # and headings are full phrases, not names.
        "REVIEW_STATUS_HEADING in text",
        "following in text",
        "heading in text",
    }
)


def test_every_declared_unimplemented_command_is_still_unimplemented() -> None:
    document = json.loads(AGENT_META.read_text(encoding="utf-8"))
    implemented = implemented_commands()
    stale = sorted(entry for entry in document["not_implemented"] if entry in implemented)
    assert stale == [], f"these are implemented now and must be documented: {stale}"


# --- Guard group 23: a deferred path states its reason and what unblocks it (draft §60) ---------
#
# `not_implemented` was a bare list of six command paths. The reasons existed, but scattered across
# prose in three documents, in wording that had partly gone stale — and the agent-facing surface,
# which is where an agent asks "why not?", said nothing at all. Draft §60 also *audited* the reasons,
# which is a question a list cannot answer and a reason can: every one of the six was re-derived from
# the planning document, and one of them came back wrong (see `covered-elsewhere` below).
#
# The categories are the point. "We have not built it" is not a reason; the three below are, because
# each names a different thing that has to happen first — and they are the set actually in play, held
# to exact equality by the guard. Adding a fourth because it *sounds* like a reason is what §60's own
# audit did and then undid, so the vocabulary is not allowed to carry a member nobody is in.

#: Why a command path is deferred. `needs-admin` cannot be worked around; `needs-capability` waits on
#: a later phase.
#:
#: A third value, `needs-decision`, was **removed in draft §82**: its only member was `root adopt`, and
#: ADR-0025 decided the copy/verify/switch rules that the name had been waiting on. What is left for
#: `root adopt` is the switch itself, which is protected state — so it moved to `needs-admin` and the
#: category went with it, for the same reason `covered-elsewhere` went below: a value nobody is in is a
#: word a reader meets and cannot use.
#:
#: A fourth value, `covered-elsewhere` ("this verb would today be a synonym of an existing one"), was
#: added and removed in the same round: its only member was `reconcile`, and the audit re-derived that
#: path from 15.1 (`reconcile <manifest>`), the roadmap (P6, right after `project manifest`) and the
#: shipped CLI (`forget` reports `project_manifest_check: not_implemented_before_p6`) and found the
#: verb has no input it could consume. The overlap with `doctor`/`desired`/`plan` was real but was the
#: smaller half. It is recorded in the register's `why` — the value is gone because nothing is in it.
DEFERRAL_CATEGORIES: tuple[str, ...] = (
    "needs-admin",
    "needs-capability",
)

#: What has to happen before the path can be built. The phase spellings that the scenario ledger also
#: uses must match it exactly (`p2-protected-state` is in both registers); the rest follow the ledger's
#: `p<phase>-<capability>` shape so the two registers read alike. The guard below enforces the overlap
#: rather than trusting the comments.
#:
#: `decision` was removed with `needs-decision` in §82: once ADR-0025 decided the three paths that were
#: waiting on a裁决 (`root adopt`, `approve`, `install`), all three wait on the same thing as the rest —
#: the protected state P2 brings.
DEFERRAL_UNBLOCKERS: tuple[str, ...] = (
    "p2-protected-state",
    "p6-project-manifest",
)


def _deferral_problems(document: dict[str, Any]) -> list[str]:
    """Structure of the ``deferred`` block: it must describe exactly the paths it is keyed to."""

    problems: list[str] = []
    declared = list(document.get("not_implemented", []))
    deferred = document.get("deferred", {})
    if not isinstance(deferred, dict):
        return ["deferred must be an object keyed by command path"]

    missing = sorted(set(declared) - set(deferred))
    extra = sorted(set(deferred) - set(declared))
    if missing:
        problems.append(f"deferred paths with no stated reason: {missing}")
    if extra:
        problems.append(f"reasons for paths that are not declared unimplemented: {extra}")

    for path, entry in sorted(deferred.items()):
        if not isinstance(entry, dict):
            problems.append(f"{path}: entry must be an object")
            continue
        if entry.get("category") not in DEFERRAL_CATEGORIES:
            problems.append(f"{path}: category {entry.get('category')!r} is not in the vocabulary")
        if not str(entry.get("why", "")).strip():
            problems.append(f"{path}: no reason given")
        if entry.get("unblocked_by") not in DEFERRAL_UNBLOCKERS:
            problems.append(f"{path}: unblocked_by {entry.get('unblocked_by')!r} is not in the vocabulary")
    return problems


def test_every_deferred_command_path_states_a_reason_and_what_unblocks_it() -> None:
    document = json.loads(AGENT_META.read_text(encoding="utf-8"))

    assert _deferral_problems(document) == [], "; ".join(_deferral_problems(document))

    # Non-vacuity: every failure mode this helper claims to detect must actually be reported.
    assert _deferral_problems({**document, "deferred": {}}) , "an empty block must be reported"
    assert _deferral_problems(
        {**document, "deferred": {**document["deferred"], "teleport": {"category": "needs-admin", "why": "x", "unblocked_by": "p2-protected-state"}}}
    ), "a reason for an undeclared path must be reported"
    sample = sorted(document["deferred"])[0]
    for mutation, expected in (
        ({"category": "because"}, "category"),
        ({"why": "   "}, "no reason"),
        ({"unblocked_by": "someday"}, "unblocked_by"),
    ):
        broken = {**document["deferred"][sample], **mutation}
        reported = _deferral_problems({**document, "deferred": {**document["deferred"], sample: broken}})
        assert any(expected in item for item in reported), (mutation, reported)

    # The vocabulary has to be the set actually in play — exact equality, both ways. `used <= the
    # declared tuple` was the first version of this and it is the weaker check by exactly one
    # direction: it catches a *misspelled* category and waves through a declared one that no path is
    # in. That is not hypothetical here — this group's own vocabulary carried `covered-elsewhere` and
    # `p4-real-backend` with no members until §60's audit showed the one entry filed under
    # `covered-elsewhere` was filed wrong, and a category whose only member has just been re-derived
    # is a word the reader will meet and be unable to find a use for.
    used = {entry["category"] for entry in document["deferred"].values()}
    assert used == set(DEFERRAL_CATEGORIES), (
        f"declared categories with no deferred path in them: {sorted(set(DEFERRAL_CATEGORIES) - used)}; "
        f"categories used but not declared: {sorted(used - set(DEFERRAL_CATEGORIES))}"
    )

    unblockers = {entry["unblocked_by"] for entry in document["deferred"].values()}
    assert unblockers == set(DEFERRAL_UNBLOCKERS), (
        f"declared unblockers with no deferred path waiting on them: "
        f"{sorted(set(DEFERRAL_UNBLOCKERS) - unblockers)}; used but not declared: "
        f"{sorted(unblockers - set(DEFERRAL_UNBLOCKERS))}"
    )


def test_the_two_registers_spell_a_shared_phase_the_same_way() -> None:
    """The deferral register and the scenario ledger both name phases; a near-miss must not pass.

    `p2-protected-state` is in both vocabularies, and the two lists are written by hand in different
    files — the exact setup that produces `p2-broker` next to `p2-protected-state` and then two
    documents that look like they are talking about different things. Exact-equality-of-shared-phases
    is the check that can go red; "they use similar words" is not.

    Only the phase prefix is compared, and only where *both* registers have that phase: P6 has no
    ledger spelling yet, and inventing a constraint for a phase the other register has never heard of
    would be a check with nothing to compare.
    """

    from scenario_ledger import MISSING_CAPABILITIES

    ledger = {value for value in MISSING_CAPABILITIES if re.fullmatch(r"p\d+-.+", value)}
    ledger_phases = {value.split("-", 1)[0]: value for value in ledger}

    collisions: list[str] = []
    for value in DEFERRAL_UNBLOCKERS:
        match = re.fullmatch(r"(p\d+)-.+", value)
        if match is None:  # `decision` is not a phase name
            continue
        ledger_value = ledger_phases.get(match.group(1))
        if ledger_value is not None and ledger_value != value:
            collisions.append(f"{value} vs the ledger's {ledger_value}")
    assert collisions == [], f"the same phase is spelled two ways: {collisions}"

    # Non-vacuity: the rule has to fire on the near-miss it exists for, and stay quiet for a phase the
    # ledger does not know. Both directions are checked because a prefix rule that never matches
    # anything passes for the wrong reason.
    def _problems(unblockers: tuple[str, ...]) -> list[str]:
        found = []
        for value in unblockers:
            match = re.fullmatch(r"(p\d+)-.+", value)
            if match is None:
                continue
            ledger_value = ledger_phases.get(match.group(1))
            if ledger_value is not None and ledger_value != value:
                found.append(f"{value} vs the ledger's {ledger_value}")
        return found

    assert _problems(("p2-broker",)) , "a near-miss of a shared phase must be reported"
    assert _problems(("p6-project-manifest",)) == [], "an unknown phase has nothing to compare against"


def test_every_deferral_category_is_explained_in_the_entry_document() -> None:
    """The register's vocabulary must be readable from the entry document, not only from code.

    Without this, `needs-capability` would be a word an agent meets for the first time in a JSON file
    with no explanation of what it waits for.
    """

    agents = AGENTS.read_text(encoding="utf-8")
    for category in DEFERRAL_CATEGORIES:
        assert names_token(agents, category, word="A-Za-z0-9_-"), (
            f"{category} is used by the deferral register but not explained in AGENTS.md"
        )
    for unblocker in DEFERRAL_UNBLOCKERS:
        assert names_token(agents, unblocker, word="A-Za-z0-9_-") or names_token(
            DRAFT.read_text(encoding="utf-8"), unblocker, word="A-Za-z0-9_-"
        ), f"{unblocker} is used as an unblocker but named nowhere a reader can find it"


# --- Guard group 24: a decision that was taken may not stay advertised as open (draft §82) -------
#
# §82 took a decision (ADR-0025) that ADR-0024 had left open, and the class of defect this group
# exists for is the **stale pointer**. The refusal messages, the Skill, the reference documents and
# the register all name the decision, and every one of them is written by hand. When the decision is
# taken, a document that still says "提案 / 待裁决" sends the reader to look for something that will
# never arrive — which is worse than saying nothing, because the reader concludes the documents are
# simply out of date and stops trusting the rest of them. Same class as §67's two refusal messages,
# measured one layer up: there the *pointer* was missing, here it points at a state that has ended.
#
# Two halves, because the two failure directions are different:
#   * the log must say it is decided, and the ADR that closed it must name every decision it took —
#     a pointer to "D6" is only actionable if D6 has a section to land on;
#   * the register that waits on it must point back, because §82's whole content is that `root adopt`
#     stopped waiting for a decision and started waiting for P2. That only reads that way if the
#     `why` an agent sees names the ADR that made the change.

#: The ADR that closed ADR-0024. Named once here so the guards below and the pointer test in
#: `test_l1_transaction.py` cannot drift onto different ids.
SETTLING_ADR = "ADR-0025"

#: The ids that ADR must have taken a decision under. Per-id, not a count: `D1`–`D9` in prose would
#: satisfy a count while leaving "见 D7" with nowhere to land, and the draft has its *own* `D1`–`D10`
#: (the diagnostic invariants), so a bare substring test for the letters would pass on the wrong list.
DECISION_IDS: tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9")


def _settled_decision_problems(log: str) -> list[str]:
    """Structure of "the decision is settled": the closed ADR, its pointer target, and its ids.

    The settling ADR is located by a **line-anchored** heading, not by `str.split("## ADR-0025")`:
    ADR-0024's own pointer sentence names it, so a plain split lands inside the ADR it is supposed to
    leave. That is not hypothetical — it is what the first version of this helper did, and it made
    every assertion below report against the wrong section.
    """

    problems: list[str] = []
    marker = re.search(r"(?m)^## ADR-0024：", log)
    if marker is None:
        return ["ADR-0024 is gone; this guard is about the ADR whose status ADR-0025 settled"]

    heading = log[marker.end() :].splitlines()[0]
    if "已裁决" not in heading:
        problems.append("ADR-0024's heading still reads as an open question")
    if "待裁决" in heading:
        problems.append("ADR-0024's heading still advertises a pending decision")

    settling = re.search(rf"(?m)^## {re.escape(SETTLING_ADR)}", log)
    if settling is None:
        return problems + [f"the status points at {SETTLING_ADR}, which does not exist"]
    tail = log[settling.end() :]
    section = re.split(r"(?m)^## ", tail, maxsplit=1)[0]
    if "已裁决" not in section:
        problems.append(f"{SETTLING_ADR} does not state that it settled anything")
    for decision in DECISION_IDS:
        # `(?!\w)` rather than `\b`: the id is followed by a space in `### D1 …` and by `0` in
        # `### D10 …`, so the boundary that matters is exactly "no word character follows" — which
        # also rejects a renamed `### D1x …` that a bare `(?![0-9])` would happily accept. Spelled as
        # a lookahead it also stays clear of the §76 rule that a `re.search` pattern may not carry a
        # one-sided `\b` (the AST check above); making the boundary *visible* is the point either way.
        if not re.search(rf"(?m)^### {re.escape(decision)}(?!\w)", section):
            problems.append(f"{SETTLING_ADR} has no section for {decision}")
    return problems


def test_the_settled_decision_says_so_and_names_every_decision_it_took() -> None:
    log = DECISION_LOG.read_text(encoding="utf-8")
    assert _settled_decision_problems(log) == [], "; ".join(_settled_decision_problems(log))

    # Non-vacuity in every direction the helper claims to detect. The first mutation is the exact
    # pre-§82 text, so this guard is measured against the state it was written to catch.
    pending = log.replace(
        log[re.search(r"(?m)^## ADR-0024：", log).end() :].splitlines()[0],
        "生产批准签发方——**提案，待裁决**（P1 只能验、不能签）",
        1,
    )
    assert any("pending decision" in problem for problem in _settled_decision_problems(pending)), (
        "the heading this guard was written for must be reported"
    )
    assert any(
        "does not exist" in problem
        for problem in _settled_decision_problems(log[: re.search(rf"(?m)^## {SETTLING_ADR}", log).start()])
    ), "a status pointing at an ADR that is not there must be reported"
    for decision in DECISION_IDS:
        dropped = log.replace(f"### {decision} ", f"### {decision}x ", 1)
        assert any(
            decision in problem for problem in _settled_decision_problems(dropped)
        ), f"an ADR that stopped naming {decision} must be reported"


def test_the_agent_facing_surfaces_point_at_the_decision_that_was_taken() -> None:
    """§82: a pointer to the ADR that left a question open is now a dead end.

    §82 changed *why* `root adopt` is deferred (a decision → P2) and *why* `approve`/`install` have no
    lane (the issuer is not coming in P1). The register, the Skill and the three on-demand references
    are all hand-written and all name the ADR — and every one of them was written while ADR-0024 was
    still a proposal. A surface that keeps pointing at the proposal sends the agent to a document that
    says the question is open, which is the §67 defect class one layer up: there the *pointer* was
    missing, here it points at a state that has ended.

    The check is per **line**, not per file, because that is the granularity the failure has: one row
    of `references/field-values.md` said "ADR-0024 是待裁决项" while the rest of the file was fine.
    A file-level check would have passed on it. (It did: that row is what this guard was measured
    against — the first run reported it.)
    """

    surfaces = {
        str(AGENT_META.relative_to(REPO)): AGENT_META.read_text(encoding="utf-8"),
        str(SKILL.relative_to(REPO)): SKILL.read_text(encoding="utf-8"),
    }
    surfaces.update(
        {
            str(path.relative_to(REPO)): path.read_text(encoding="utf-8")
            for path in sorted((REPO / "references").glob("*.md"))
        }
    )

    def _stale_pointers(payload: dict[str, str]) -> list[str]:
        found = []
        for name, text in sorted(payload.items()):
            for number, line in enumerate(text.splitlines(), 1):
                if "ADR-0024" in line and SETTLING_ADR not in line:
                    found.append(f"{name}:{number}")
        return found

    assert _stale_pointers(surfaces) == [], (
        f"these lines point at the superseded proposal and stop there: {_stale_pointers(surfaces)}"
    )

    # Non-vacuity: the rule has to fire on the pointer shape it exists for, and only where it exists.
    seeded = dict(surfaces)
    seeded["seeded.md"] = "The rules are missing: ADR-0024 is the pending decision."
    assert _stale_pointers(seeded) == ["seeded.md:1"], (
        "a line that points only at the superseded proposal must be reported"
    )
    assert _stale_pointers(
        {"ok.md": f"ADR-0024 left it open; {SETTLING_ADR} settled it."}
    ) == [], "naming both ADRs on one line is the fix, not the defect"


def test_the_deferral_register_points_at_the_decision_that_moved_it() -> None:
    """§82 changed *why* `root adopt` is deferred, and the register is what an agent reads.

    Before §82 it waited on a decision; after §82 it waits on P2. A register that still presented the
    copy/verify/switch rules as an open question would send the agent to ask the user for something
    ADR-0025 has already answered — the §67 defect class, one register over.

    This one is deliberately narrower than the line sweep above: it pins the two *specific* moves §82
    made, so a future edit that reverts the reasoning (rather than the wording) still goes red.
    """

    document = json.loads(AGENT_META.read_text(encoding="utf-8"))
    adopt = document["deferred"]["root adopt"]

    assert SETTLING_ADR in adopt["why"], (
        "the register says what `root adopt` waits for without naming the ADR that settled the rules, "
        "so a reader cannot tell a decided rule from an open question"
    )
    assert adopt["unblocked_by"] == "p2-protected-state", (
        f"ADR-0025 settled the rules; what is left is the protected switch, not {adopt['unblocked_by']!r}"
    )

    # D1 is the decision that kept these two verbs from getting lanes, so the register's own
    # explanation of *why there is no lane* is where an agent meets it.
    for verb in ("approve", "install"):
        entry = document["uncovered_verbs"][verb]
        assert SETTLING_ADR in entry["why_no_lane"], (
            f"`{verb}` waits on the protected state D1 left it waiting on, without naming the decision"
        )


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
#:
#: **Empty, and that is the point.** It held exactly one entry — `DATA_ROOT_ACL_DRIFT`, which could
#: not be produced because the ACL baseline was P2 work — and draft §35 wrote down that the exception
#: "P2 落地后必须删除". Draft §58 landed the read side (`caps/acl.py` + the `doctor` check), so the
#: entry is gone and the guard below now requires the code to be genuinely producible like any other.
#: The list stays because the *shape* recurs: the next declared-but-unemittable code must be written
#: down here rather than left as a silent gap.
RESERVED_DIAGNOSTICS: dict[str, str] = {}


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
    missing = sorted(code for code in REASON_EXIT if not names_code(text, code))

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


def test_the_documented_test_count_is_the_same_everywhere(request: pytest.FixtureRequest) -> None:
    """Prose counts drift silently; they must at least be internally consistent — and true.

    `AGENTS.md` is the current-state document, so every total it states must be the *same* number.
    The contract draft's totals are per-stage historical records (§17.5, §18.6, …): they are
    allowed to differ, but none may **exceed** the current total — a historical record cannot have
    been written against a bigger tree than exists now.

    Draft §54 closed the other half, which had been open the whole time: **nothing compared that
    number with the tree**. Three documents could agree on a total that had been stale for rounds,
    and the only thing that ever moved it was someone noticing. The tell was the asymmetry right
    next to it — the *audit-check* count is derived from source (`_audit_check_count`), while the
    number an agent actually quotes back to a user was checked against nothing but its own copies.
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

    # ...and "the current total" has to mean the count this session actually collected.
    #
    # Guarded for partial runs: `pytest cli/tests/test_l0_consistency.py` collects one module, and
    # comparing a whole-suite number against it would be nonsense. Rather than silently passing, a
    # partial run says so.
    modules = {path.stem for path in (REPO / "cli" / "tests").glob("test_*.py")}
    collected_modules = {item.path.stem for item in request.session.items}
    if not modules <= collected_modules:
        pytest.skip(
            f"partial run: {len(collected_modules)} of {len(modules)} test modules collected, "
            "so the documented total cannot be compared with this session"
        )
    assert len(request.session.items) == now, (
        f"the documents say {now} tests and this session collected {len(request.session.items)}; "
        "update AGENTS.md, the review report (and SKILL.md if it states one) in the same change"
    )


# --- Guard group 25: the stage records' own count chain (draft §83) ------------------------------
#
# Every stage record states "the suite went from A to B". §54 ties the *current* total to the tree,
# but says nothing about history: `B` is anchored from both sides (the next stage's `A`, and the
# final total), while `A` was anchored to nothing at all. Same fact written twice — the previous
# stage's `B` and this stage's `A` — with only one of the two ever checked. Measured in §83: three of
# the four discontinuities on that chain were simply wrong (draft §60 said 758 where the previous
# stage ended at 756; §68 said 781 for 780; §76 said 808 for 807).
#
# The chain is allowed to break in exactly one situation: a commit that changed the count without
# adding a stage section. The draft declares those in its own table (draft §83.4), and this guard
# reads that table rather than carrying an exception list of its own — an exception nobody can read
# is indistinguishable from a typo, which is the whole defect.

_STAGE_HEADING = re.compile(r"(?m)^## (\d+)\. ")

#: How a stage record states its test total. The introducing token is *required*, and the arrow form
#: is kept separate from the solo form on purpose: `测试` also introduces sentences about the
#: **ledger** ("台账处置删除，`evidenced` 42 → 43"), about a test *file*, and about a test *function*,
#: and only one of those is a count. Two earlier versions of this parser read a ledger figure as the
#: suite size, which is how the stage ended up learning that a parser has to know what its sentence
#: is about (§83.5).
_STAGE_INTRO = r"(?:`pytest cli/tests`|测试\s*(?:\||\*\*))[^\d\n]{0,20}"
_STAGE_PAIR = re.compile(_STAGE_INTRO + r"(\d{2,4})\s*→\s*(?:\*\*)?(\d{2,4})")
_STAGE_SOLO = re.compile(_STAGE_INTRO + r"(\d{2,4})\s*项")

#: Where the draft declares a count change that has no stage section of its own. Anchored on the
#: heading *line* and on the title, not on its number: the title is also mentioned in §51's pointer
#: sentence, and splitting on the bare phrase finds that mention first — the same trap §82 hit when a
#: pointer sentence contained the very heading it pointed at.
_STAGE_JUMP_HEADING = re.compile(r"(?m)^#{2,4} .*跨阶段的计数变化")
_STAGE_JUMP_ROW = re.compile(
    r"^\|\s*§(\d+)\s*\|\s*§(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|(.+?)\|", re.M
)


def _stage_count_chain(text: str) -> list[tuple[int, int | None, int, int]]:
    """Per stage section: ``(stage, stated_from_or_None, to, heading_line)``.

    A section that states a single total (`**684 项全绿**`) has no `from` of its own; the pair form
    (`681 → **684 项**`) does. When a section holds several pairs — a stage record quoting an earlier
    stage's numbers, or (as in §84) explaining a mutation — the one that belongs to the section is the
    pair with the largest `to`: the chain is monotone, so a *quoted* historical pair always ends below
    the section's own end. Positional rules (first, last) are what §84 broke: its prose quotes
    `826 → 827` above its own row, and taking the first pair read the section as ending there.
    """

    starts = [(int(match.group(1)), match.start()) for match in _STAGE_HEADING.finditer(text)]
    chain: list[tuple[int, int | None, int, int]] = []
    for position, (stage, index) in enumerate(starts):
        end = starts[position + 1][1] if position + 1 < len(starts) else len(text)
        body = text[index:end]
        line = text.count("\n", 0, index) + 1
        pairs = _STAGE_PAIR.findall(body)
        if pairs:
            own = max(pairs, key=lambda pair: int(pair[1]))
            chain.append((stage, int(own[0]), int(own[1]), line))
            continue
        solos = _STAGE_SOLO.findall(body)
        if solos:
            chain.append((stage, None, int(solos[-1]), line))
    return chain


def _declared_stage_jumps(text: str) -> dict[tuple[int, int], tuple[int, int, str]]:
    """The draft's own declaration table: (from §, to §) -> (previous end, this start, reason)."""

    section = _STAGE_JUMP_HEADING.split(text)
    if len(section) < 2:
        return {}
    body = section[-1].split("\n### ", 1)[0]
    return {
        (int(before), int(after)): (int(previous), int(start), reason.strip())
        for before, after, previous, start, reason in _STAGE_JUMP_ROW.findall(body)
    }


def _stage_chain_problems(text: str, current: int | None = None) -> list[str]:
    """The five rules of draft §83.5, as a list of problems rather than an assertion."""

    problems: list[str] = []
    declared = _declared_stage_jumps(text)
    for (before, after), (previous, start, reason) in sorted(declared.items()):
        if not reason:
            problems.append(f"§{before}→§{after} is declared as a jump with no reason given")

    chain = _stage_count_chain(text)
    jumps: set[tuple[int, int]] = set()
    previous_stage: int | None = None
    previous_to: int | None = None
    for stage, stated_from, to, line in chain:
        if stated_from is not None and stated_from > to:
            problems.append(f"§{stage} (line {line}) reads {stated_from} → {to}: the suite cannot shrink")
        if previous_to is not None and to < previous_to:
            problems.append(
                f"§{stage} (line {line}) ends at {to}, below §{previous_stage}'s {previous_to}"
            )
        if previous_to is not None and stated_from is not None and stated_from != previous_to:
            jumps.add((previous_stage, stage))
            if (previous_stage, stage) in declared:
                want_previous, want_start, _ = declared[(previous_stage, stage)]
                if (want_previous, want_start) != (previous_to, stated_from):
                    problems.append(
                        f"§{previous_stage}→§{stage} declares the jump {want_previous}→{want_start}, but "
                        f"the chain says {previous_to}→{stated_from}; a declaration that is not true is "
                        "worse than none"
                    )
            else:
                problems.append(
                    f"§{stage} (line {line}) starts at {stated_from} but §{previous_stage} ended at "
                    f"{previous_to}; declare the jump in the draft's table or fix the number"
                )
        previous_stage, previous_to = stage, to

    stale = sorted(set(declared) - jumps)
    if stale:
        problems.append(f"declared jumps that the chain does not have: {stale}")

    if current is not None and chain:
        last_stage, _, last_to, _ = chain[-1]
        if last_to != current:
            problems.append(f"the chain ends at §{last_stage}'s {last_to}; the suite has {current} tests")
    return problems


def _current_test_total() -> int:
    match = re.search(r"\*\*(\d+)\s*项测试通过\*\*", AGENTS.read_text(encoding="utf-8"))
    assert match is not None, "AGENTS.md no longer states the current test total in a readable form"
    return int(match.group(1))


def test_the_stage_records_count_the_suite_without_an_unexplained_jump() -> None:
    """The chain of stage totals: ordered, monotone, contiguous, and ending where the suite is.

    A break is legal only when a commit changed the count without adding a stage section, and the
    draft has to say so in its own table. Read the table, not a list in this file: §82 learned that a
    hard-coded exception list is where a guard starts to lie, and the fix there was to make the
    exception a sentence a reader can find.
    """

    text = DRAFT.read_text(encoding="utf-8")
    assert _stage_chain_problems(text, _current_test_total()) == [], "; ".join(
        _stage_chain_problems(text, _current_test_total())
    )

    # Non-vacuity, one mutation per rule — including the two directions of the declaration rule,
    # because an exception mechanism has to be checked in both: a jump that is *not* declared, and a
    # declaration that does not match the chain.
    def problems(mutated: str) -> str:
        return "; ".join(_stage_chain_problems(mutated, _current_test_total()))

    restored = text.replace("`pytest cli/tests` 756 → **759 项**", "`pytest cli/tests` 758 → **759 项**", 1)
    assert restored != text and "§60" in problems(restored), (
        "the wrong `from` this guard was written for must be reported"
    )
    # The other two wrong `from` values §83 found, so this guard is measured against all three and
    # not just the one that happened to be first.
    for stage, correct, wrong in (("§68", "**780 → 782**", "**781 → 782**"), ("§76", "**807 → 809**", "**808 → 809**")):
        mutated = text.replace(correct, wrong, 1)
        assert mutated != text and stage in problems(mutated), f"the wrong `from` in {stage} must be reported"
    undeclared = text.replace("| §50 | §51 | 737 | 739 |", "| —— | —— | 737 | 739 |", 1)
    assert undeclared != text and "declare the jump" in problems(undeclared), (
        "a jump with no declaration must be reported"
    )
    wrong_declaration = text.replace("| §50 | §51 | 737 | 739 |", "| §50 | §51 | 737 | 740 |", 1)
    assert wrong_declaration != text and "declares the jump" in problems(wrong_declaration), (
        "a declaration that does not match the chain must be reported"
    )
    shrunk = text.replace("| 测试 | **792 → 803**", "| 测试 | **804 → 803**", 1)
    assert shrunk != text and "cannot shrink" in problems(shrunk), (
        "a stage that ends below where it started must be reported"
    )
    stale = text.replace("| §50 | §51 | 737 | 739 |", "| §50 | §52 | 737 | 739 |", 1)
    assert stale != text and "the chain does not have" in problems(stale), (
        "a declaration for a jump that is not there must be reported"
    )
    # The last-stage mutation is **derived**, not spelled out. §84 opened by re-pointing the literal
    # version of this line at the wrong stage: `**826 → 827**` was the last row when §83 wrote it,
    # and appending a stage made the same string match §83's own row instead, so the mutation still
    # went red while no longer testing the chain's *end*. A mutation anchored on a value the next
    # stage will reuse silently retargets itself; deriving it from the chain cannot. (§85 then found
    # the second half of the same problem: the derivation also assumed the row was `N-1 → N`, which
    # is only true when a stage adds one test.)
    last_stage, last_from, last_to, _ = _stage_count_chain(text)[-1]
    assert last_from is not None, f"§{last_stage} states a solo total, so it has no row to shorten"
    tail = f"| 测试 | **{last_from} → {last_to}**"
    assert text.count(tail) == 1, f"§{last_stage}'s own count row is not in the shape this mutation needs"
    shortened = text.replace(tail, f"| 测试 | **{last_from} → {last_to - 1}**", 1)
    assert shortened != text and "the suite has" in problems(shortened), (
        "a chain that ends below the real total must be reported"
    )


# --- Guard group 26: the bytes on disk (draft §84) -----------------------------------------------
#
# `.gitattributes` calls the golden corpus a **byte-for-byte** acceptance face and justifies its one
# line (`* -text`) with that claim plus the `airoot.cmd`/`cmd.exe` trap. All of it was prose. §84
# measured the hole: the acceptance test compared *parsed* documents, so every fixture could be
# rewritten from CRLF to LF — `index.json` goes from 840 bytes to 812 — with the whole suite green,
# and the generator's own line endings came from the platform's text mode rather than from a decision.
#
# These are the rules that claim needs. Deliberately **not** policed: the general CRLF/LF split across
# the tree. `* -text` makes both legal, and the split that exists (a few documents and the corpus are
# CRLF) is historical rather than load-bearing — a rule about it would be style, and a style rule is
# where a guard starts demanding rewrites nobody asked for.

#: Suffixes that are not read as text. Anything that fails to decode is skipped anyway; this only keeps
#: the walk from trying.
_BINARY_SUFFIXES = frozenset(
    {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".gz", ".exe", ".dll", ".whl"}
)
_WALK_SKIP = frozenset({".git", ".tmp", "__pycache__", ".pytest_cache", "node_modules"})

#: The one file whose ending is a functional contract: `cmd.exe` splits its `rem` lines into commands
#: if they are not CRLF-terminated.
COMMAND_LAUNCHER = "cli/bin/airoot.cmd"
GITATTRIBUTES = REPO / ".gitattributes"


def _endings(data: bytes) -> tuple[int, int, int]:
    """(CRLF, lone LF, lone CR). A file with two of these is a file some tool wrote twice."""

    crlf = data.count(b"\r\n")
    rest = data.replace(b"\r\n", b"")
    return crlf, rest.count(b"\n"), rest.count(b"\r")


def _walked_text_files() -> dict[str, bytes]:
    """Every readable text file in the tree, keyed by its repo-relative posix path."""

    files: dict[str, bytes] = {}
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        relative = path.relative_to(REPO)
        if any(part in _WALK_SKIP for part in relative.parts):
            continue
        data = path.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        files[relative.as_posix()] = data
    return files


def _byte_contract_problems(
    files: dict[str, bytes], attributes: str, fixtures: dict[str, bytes]
) -> list[str]:
    """BOMs, files with two endings, and the four byte rules the contracts state."""

    problems: list[str] = []
    for name, data in sorted(files.items()):
        if data.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{name} starts with a UTF-8 BOM")
        crlf, lone_lf, lone_cr = _endings(data)
        if crlf and (lone_lf or lone_cr):
            problems.append(f"{name} mixes CRLF with {lone_lf} lone LF / {lone_cr} lone CR")

    launcher = files.get(COMMAND_LAUNCHER)
    if launcher is None:
        problems.append(f"{COMMAND_LAUNCHER} is missing, so its ending cannot be checked")
    elif _endings(launcher) != (launcher.count(b"\r\n"), 0, 0) or launcher.count(b"\r\n") == 0:
        problems.append(f"{COMMAND_LAUNCHER} is not CRLF-only; cmd.exe splits its rem lines")

    for name, data in sorted(fixtures.items()):
        crlf, lone_lf, lone_cr = _endings(data)
        if lone_lf or lone_cr or crlf == 0:
            problems.append(f"golden fixture {name} is not CRLF-only")
        elif not data.endswith(b"\r\n"):
            problems.append(f"golden fixture {name} does not end with exactly one CRLF")

    directives = [
        line.strip() for line in attributes.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if directives != ["* -text"]:
        problems.append(
            f".gitattributes carries {directives}; `* -text` is the single line that keeps the corpus "
            "byte-identical and the launcher CRLF on a fresh clone"
        )
    return problems


def test_the_bytes_on_disk_are_the_ones_the_contracts_claim() -> None:
    """A byte contract with no byte check is a sentence, not a contract."""

    files = _walked_text_files()
    attributes = GITATTRIBUTES.read_text(encoding="utf-8")
    fixtures = {path.name: path.read_bytes() for path in sorted(GOLDEN.glob("*.json"))}

    assert files, "the tree walk found no text files, so this guard is about nothing"
    assert fixtures, "the golden corpus is gone, so the fixture rule is about nothing"
    assert _byte_contract_problems(files, attributes, fixtures) == [], "; ".join(
        _byte_contract_problems(files, attributes, fixtures)
    )

    # Non-vacuity, one mutation per rule. Every one of them is a byte sequence that would have passed
    # the pre-§84 test, which is the whole reason this group exists.
    def problems(
        files: dict[str, bytes] | None = None, attributes: str | None = None, fixtures: dict[str, bytes] | None = None
    ) -> str:
        return "; ".join(
            _byte_contract_problems(
                files if files is not None else _walked_text_files(),
                attributes if attributes is not None else GITATTRIBUTES.read_text(encoding="utf-8"),
                fixtures if fixtures is not None else {path.name: path.read_bytes() for path in sorted(GOLDEN.glob("*.json"))},
            )
        )

    bommed = dict(files)
    bommed["seeded.md"] = b"\xef\xbb\xbf# seeded\n"
    assert "UTF-8 BOM" in problems(files=bommed), "a BOM must be reported"

    mixed = dict(files)
    mixed["seeded.md"] = b"one\r\ntwo\n"
    assert "mixes CRLF" in problems(files=mixed), "a file with two endings must be reported"

    converted = dict(files)
    converted[COMMAND_LAUNCHER] = files[COMMAND_LAUNCHER].replace(b"\r\n", b"\n")
    assert "not CRLF-only" in problems(files=converted), "an LF launcher must be reported"

    lf_fixture = dict(fixtures)
    first = sorted(lf_fixture)[0]
    lf_fixture[first] = lf_fixture[first].replace(b"\r\n", b"\n")
    assert "not CRLF-only" in problems(fixtures=lf_fixture), "an LF fixture must be reported"

    assert "does not end with exactly one CRLF" in problems(
        fixtures={**fixtures, sorted(fixtures)[0]: fixtures[sorted(fixtures)[0]].rstrip(b"\r\n")}
    ), "a fixture with no trailing newline must be reported"

    assert "text=auto" in problems(attributes=attributes.replace("* -text", "* text=auto")), (
        "re-enabling conversion in .gitattributes must be reported"
    )
    assert "carries []" in problems(attributes=attributes.replace("* -text", "")), (
        "deleting the directive must be reported"
    )


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
#:
#: 9 -> 8 in draft §54: shrinking the entry document deleted the §8b stage-log sentence ("需要确认或
#: scope 提升时以退出码 4 停下"), which was one of the census entries. The *fact* is untouched and still
#: agent-facing — `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4）is stated in SKILL.md, is bound in
#: references/confirmation.md (guard group 19) and appears as the `(4)` marker on the `plan build`
#: invocation in AGENTS §6. So this is a deliberate census move, not a claim that stopped being checked.
UNBOUND_EXIT_WORDS = 8


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
    # §60 found the same rot in two more rows of that section while editing the counts around them:
    # `caps/acl.py` had been read-only-observing data roots since §58, and §59 had really fetched
    # 12 721 664 bytes from `static.rust-lang.org` — both still described as absent. The ACL entry is
    # the strong form (the artifact is the observation). The second is weaker and says so: the artifact
    # proves the code path exists, while the *run* is a recorded observation, so the phrase banned is
    # only the flat claim that it never happened. A guard that could not go red is not a guard, but a
    # guard that pretends to verify a network run would be the dishonest kind of green.
    "cli/app/airoot/caps/acl.py": "ACL 基线（`DATA_ROOT_ACL_DRIFT` 的发射）",
    "cli/tests/real_machine_acceptance.py": "尚未对真实上游执行过",
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
    # A row that *discusses* a scenario is not a definition either (draft §52.2-F4): the first cell
    # must be exactly the ID, so a disposition table or a bolded/backticked ID does not define one.
    assert _DEFINITION.match(f"| `{family}-007` | undesigned | a disposition |") is None
    assert _DEFINITION.match(f"| **{family}-007** | undesigned | a disposition |") is None
    assert _DEFINITION.match(f"| {family}-007（新） | setup | expectation |") is not None

    # And the load-bearing half: only a table headed `| ID | … |` (draft §52.2-F4). A table that merely
    # *discusses* scenarios defines none of them, which is how §52.1's disposition table silently
    # registered eight definitions under the old "first cell is an ID" rule.
    from scenario_ledger import _definition_rows

    discussion = ["| 编号 | 判断 | 结果 |", "|---|---|---|", f"| {family}-007 | undesigned | a disposition |"]
    assert _definition_rows(discussion) == [], "a discussion table is not a scenario table"
    scenario = ["| ID | 场景 | 预期 |", "|---|---|---|", f"| {family}-007（新） | setup | expectation |"]
    assert [number for number, _match in _definition_rows(scenario)] == [3]
    # A row before any header, and a row after the table ends, are both outside it.
    assert _definition_rows([f"| {family}-007 | setup | expectation |"]) == []
    after_the_table = [*scenario, "", f"| {family}-008 | setup | expectation |"]
    assert [number for number, _match in _definition_rows(after_the_table)] == [3]

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


def test_the_confirmation_reference_does_not_offer_a_step_this_build_cannot_perform() -> None:
    """The reference's approval shape used to end at a step with no implementation behind it.

    It lists four steps; steps 1-2 run today, steps 3-4 need an approval token and **nothing in
    this build can mint one**. That is a statement about the implementation, so it belongs to the
    same class as the read-only claim above (draft §48: a document may not assert what the code no
    longer — or never did — do). The dangerous direction is the honest block being deleted while the
    four-step shape stays, so both halves are asserted.
    """

    from airoot.tx.approval import ISSUER_PENDING

    text = CONFIRMATION_REFERENCE.read_text(encoding="utf-8")
    assert "--token-file" in text, "the approval shape vanished; this guard is now about nothing"
    assert ISSUER_PENDING in text, (
        "references/confirmation.md presents `install --token-file` without saying that this build "
        "cannot produce a token; an agent reading it will describe a flow that cannot run"
    )
    assert "ADR-0025" in text, "the honest block must point at the decision that was taken, not just apologise"
    assert "已裁决" in text or "维持现状" in text, (
        "the reference must say the decision was taken — otherwise the gap reads as still open"
    )


# --- Guard group 20: an "undesigned" claim needs a witness (draft §52) ---------------------------
#
# §49 wrote the ledger's dispositions as **authored judgements** and checked them only for structure —
# deliberately, because "is this behaviour really exercised?" is not a question a guard can answer.
# §52 did the thing that decision left open: it took the eight `undesigned` entries and checked them
# against the code, and **one was wrong** — a scenario the ledger called "the command it needs does not
# exist yet" was in fact implemented *and already tested*, the test simply never named it. Two lessons,
# both mechanical:
#
#   * `status` is a *measurement* (does any test name this ID?) and `blocked_by` is a *judgement*;
#     when they disagree, the fix may be to have the existing test name the scenario rather than to
#     rewrite the judgement;
#   * "the capability does not exist" is a claim nothing checks, which is how §35's unimplemented list
#     went stale. So every `undesigned` entry now names a **witness** — the thing that goes red the
#     moment the missing capability appears — or states why it cannot have one.
#
# The witness rules themselves live in `scenario_ledger._undesigned_problems`; this pins them and
# proves each failure mode is reported.


def test_every_undesigned_scenario_names_a_witness_or_says_why_it_cannot() -> None:
    from scenario_ledger import build_ledger, evidence_problems

    assert evidence_problems(REPO) == [], "; ".join(evidence_problems(REPO))

    entries = build_ledger(REPO)
    undesigned = [entry for entry in entries if entry["blocked_by"] == "undesigned"]
    assert undesigned, "no `undesigned` scenarios left; this guard is now about nothing"
    witnessed = [entry for entry in undesigned if entry["witness"]]
    assert witnessed, "no `undesigned` entry names a witness; the rule may have been dropped"
    assert any(entry["no_witness_reason"] for entry in undesigned), (
        "every `undesigned` entry has a witness — the 'no witness' branch is now untested"
    )

    # Non-vacuity: each failure mode the helper claims to detect must actually be reported.
    sample = dict(undesigned[0])
    provenance_free = {**sample, "witness": None, "no_witness_reason": None}
    assert any("neither a witness nor a reason" in item for item in evidence_problems(REPO, [provenance_free]))
    assert any(
        "pick one" in item
        for item in evidence_problems(
            REPO, [{**sample, "witness": f"test_l1_planner.py#{'C'}-021", "no_witness_reason": "also this"}]
        )
    )
    assert any(
        "does not exist" in item or "does not appear" in item
        for item in evidence_problems(REPO, [{**sample, "witness": "no_such_file.py#token", "no_witness_reason": None}])
    )


# --- Guard group 21: a pointer must name a test function, not a token (draft §53) ----------------
#
# Group 17 checked a pointer with `token in path.read_text()` — a **substring** test. It therefore
# could not fail in the direction that matters. §53 audited the forty-one entries that claim nothing
# is missing (`blocked_by=none`) by hand — read the pointed-at test, then the code — and found the
# *judgements* were almost all right while about thirty *pointers* resolved to a docstring, a
# comment, an import line, or a **different test than the one that exercises the expectation**. Three
# dispositions were wrong and are corrected in the table; every pointer now names a function.
#
# The rule lives in `scenario_ledger._evidence_pointer_problems` and covers `witness` as well as
# `evidence`, because a witness has the same job. What follows pins the rule and proves the failure
# modes are reported — including the one the old rule could not report at all.

_POINTER_SAMPLE = "test_l1_registry.py#payload"
_POINTER_ACCIDENTAL = "test_l1_rebuild.py#audit"


def test_every_evidence_and_witness_pointer_names_a_test_function() -> None:
    from scenario_ledger import _evidence_pointer_problems, evidence_problems

    assert evidence_problems(REPO) == [], "; ".join(evidence_problems(REPO))

    # Non-vacuity: every failure mode the helper claims to detect must actually be reported. The
    # first case is a pointer this ledger really carried before §53, so the regression is pinned
    # to a concrete historical value rather than an invented one.
    reported = _evidence_pointer_problems(REPO, "SYNTHETIC", _POINTER_SAMPLE)
    assert reported and "is not a test function name" in reported[0], reported
    assert _evidence_pointer_problems(REPO, "SYNTHETIC", "test_l1_rebuild.py#test_not_a_real_function")
    assert _evidence_pointer_problems(REPO, "SYNTHETIC", "no_such_file.py#test_x")
    assert _evidence_pointer_problems(REPO, "SYNTHETIC", "test_l1_rebuild.py")
    assert _evidence_pointer_problems(REPO, "SYNTHETIC", "test_l1_rebuild.py#test_unmanaged_references_are_reported_not_adopted") == []

    # The ledger must not have quietly lost its `none` entries, or the rule above would be vacuous.
    from scenario_ledger import build_ledger

    none_entries = [entry for entry in build_ledger(REPO) if entry["blocked_by"] == "none"]
    assert none_entries, "no `blocked_by=none` entries left; this guard is now about nothing"
    assert all(entry["evidence"] for entry in none_entries)


def test_the_old_substring_rule_could_not_go_red_and_the_new_one_does() -> None:
    """The defect stated executably, in its two shapes.

    **(a)** A token that names no test at all. `test_l1_registry.py#payload` — the pointer this
    ledger really carried — is satisfied under the substring rule by an **import line** and by local
    variables; `payload` never appears on a test-function line in that file. So the rule accepted a
    pointer to nothing, and it kept accepting it after the test it should have named was deleted.

    **(b)** A token that is accidentally part of a test's *name*. `test_l1_rebuild.py#audit` resolves
    partly because `test_rebuild_repairs_a_stale_audit_projection` contains the word — but the same
    word is also a local variable three lines away, so "it resolves" carries no information about
    whether a test exists. A rule that cannot tell these apart cannot fail.

    The checkability is the point: the function rule rejects both for a reason a reader can verify.
    """

    from scenario_ledger import _evidence_pointer_problems

    # (a) a token that is never a test name — the strongest form of the defect.
    name, token = _POINTER_SAMPLE.split("#", 1)
    text = (REPO / "cli" / "tests" / name).read_text(encoding="utf-8")
    lines = text.splitlines()
    occurrences = [number for number, line in enumerate(lines, 1) if token in line]
    assert occurrences, f"{token!r} no longer occurs in {name}; this test is about nothing"
    assert not any(re.match(r"\s*def test_", lines[number - 1]) for number in occurrences), (
        f"{token!r} now occurs on a test-function line, so it is no longer an example of defect (a)"
    )

    # ...and it survives deleting the test it should have pointed at, which is the load-bearing half:
    # the substring rule had no way to notice.
    first = text.index("def test_adding_the_same_instance_twice_is_idempotent")
    second = text.index("def test_same_instance_id_with_a_different_digest_is_refused")
    assert second > first
    assert token in text[:first] + text[second:], "deleting that test removes the token; claim (a) is wrong"

    # (b) the accidental case: the token is a substring of one test's name *and* of unrelated code.
    name2, token2 = _POINTER_ACCIDENTAL.split("#", 1)
    lines2 = (REPO / "cli" / "tests" / name2).read_text(encoding="utf-8").splitlines()
    hits = [number for number, line in enumerate(lines2, 1) if token2 in line]
    on_def = [number for number in hits if re.match(r"\s*def test_", lines2[number - 1])]
    assert len(on_def) == 1, f"{token2!r} now occurs on {len(on_def)} def lines; claim (b) needs updating"
    assert len(hits) > len(on_def), (
        f"{token2!r} occurs only on a def line now; it is no longer an example of defect (b)"
    )

    # Both are rejected for a checkable reason, not a stylistic one.
    for sample in (_POINTER_SAMPLE, _POINTER_ACCIDENTAL):
        reported = _evidence_pointer_problems(REPO, "SYNTHETIC", sample)
        assert reported and "is not a test function name" in reported[0], (sample, reported)


# --- Guard group 22: the entry document must be loadable in full (draft §54) ---------------------
#
# `AGENTS.md` is the onboarding document: an agent reads it first and then acts on it. In this
# deployment the workspace-instruction budget is 65 536 bytes, and exceeding it is **silent** — the
# loader truncates the file, so the agent receives a shorter document with no marker that anything is
# missing. §53 hit that wall for real: appending one paragraph took the file to 65 840 bytes, and what
# got cut was §8's last paragraph and the whole of §9. Nothing in the file said so.
#
# That is the same family as §48 (a document denying what it delivered) with a nastier shape: here the
# author wrote nothing false — the document was simply never read whole. Since the *consequence* lands
# on this project (an agent acting on half the rules cannot tell that it is), the project adopts the
# deployment's number as its own bound. The number's provenance is written down rather than implied,
# and it is not smeared into the assertion as a literal.
#
# Two checks, because the fix for a size problem has two ways to go wrong:
#   * growing past the budget again — silent, and nothing else in this file would notice;
#   * shrinking by deleting detail and leaving no route to it — which would turn the entry document
#     into a dead end, the opposite failure of the one being fixed.

ENTRY_DOC_BUDGET_BYTES = 65536

#: The document that holds the stage-by-stage implementation record (§31…§54).
STAGE_RECORD = DRAFT

#: The phrase by which the entry document delegates the stage-by-stage record. Deliberately a stable
#: contract phrase, so this guard expires if someone deletes the sentence rather than rewording it.
STAGE_RECORD_MARKER = "逐阶段的实现记录"

#: Where the delegated range begins: §31 is the first stage whose detail was ever written into the
#: entry document, so it is the honest lower bound.
FIRST_DELEGATED_SECTION = "§31"


def _newest_stage_section() -> str:
    """The highest ``## N.`` in the record document — derived, so this cannot go stale.

    A hard-coded upper bound rots the moment a stage is added, and it *did*: the delegation read
    "§31–§54" while §55 already existed. Worse, it rots in the direction that keeps passing — the
    guard would go on accepting a range that is no longer the whole record, which is precisely the
    "points somewhere real but no longer correct" failure this file exists to catch.
    """

    numbers = [
        int(match)
        for match in re.findall(r"(?m)^## (\d+)\.", STAGE_RECORD.read_text(encoding="utf-8"))
    ]
    assert numbers, "the record document has no numbered sections"
    return f"§{max(numbers)}"


def test_the_entry_document_fits_the_reader_budget() -> None:
    size = len(AGENTS.read_bytes())
    assert size <= ENTRY_DOC_BUDGET_BYTES, (
        f"AGENTS.md is {size} bytes and the reader budget is {ENTRY_DOC_BUDGET_BYTES}; exceeding it "
        "truncates the document **silently**, so its reader loses the tail with no marker at all. "
        "Move detail into the draft (draft §54) rather than growing the entry document."
    )
    # Not vacuous: a file that fits because it is nearly empty would satisfy the assertion above while
    # meaning nothing. The entry document has real work to do, so it has a floor as well as a ceiling.
    assert size > 10_000, "AGENTS.md is suspiciously small; the ceiling check above is now meaningless"


def _stage_record_delegation(text: str) -> list[str]:
    """Lines that hand the per-stage record off: the marker **and** the document name, together.

    Both halves are required, and requiring them on the *same line* is the point: a document that
    mentions the draft somewhere and says "stage record" somewhere else has not told a reader where
    to go. Separated out so the three failure modes can be exercised without touching `AGENTS.md`.
    """

    return [
        line
        for line in text.splitlines()
        if STAGE_RECORD_MARKER in line and STAGE_RECORD.name in line
    ]


def test_the_entry_document_says_where_the_stage_record_lives() -> None:
    """Shrinking must not turn the entry document into a dead end (draft §54.4-2b).

    This is the guard for the *fix itself*. Deleting thirty kilobytes of per-stage detail is only
    honest while the record stays reachable; without that, the next reader cannot find §31–§54 at all
    and the information is gone in practice even though it still exists on disk.
    """

    delegating = _stage_record_delegation(AGENTS.read_text(encoding="utf-8"))
    assert delegating, (
        f"no line of AGENTS.md both says {STAGE_RECORD_MARKER!r} and names {STAGE_RECORD.name!r}; "
        "the entry document must say where the per-stage detail went (draft §54)"
    )
    # The delegation must name the *range* it hands off, not merely the file: a reader has to be able
    # to tell that the record continues where this document stops. The upper bound is derived from the
    # record document, so adding a stage forces this sentence to keep up.
    newest = _newest_stage_section()
    assert int(newest[1:]) >= 31, f"{newest} is not a stage section; the derivation picked up a heading"
    assert FIRST_DELEGATED_SECTION in delegating[0] and newest in delegating[0], (
        f"the delegation must name the range it hands off "
        f"({FIRST_DELEGATED_SECTION}–{newest}): {delegating[0].strip()[:160]}"
    )

    # Non-vacuity: all three ways to fail must actually be reported, on synthetic input — so the
    # guard's red direction does not depend on anyone remembering to break the real document.
    name = STAGE_RECORD.name
    assert _stage_record_delegation(f"{STAGE_RECORD_MARKER}见草案 §31–§54") == [], (
        "naming no document must not count as a delegation"
    )
    assert _stage_record_delegation(f"细节在 {name} 里") == [], (
        "naming the document without saying what is in it must not count"
    )
    assert _stage_record_delegation(f"{STAGE_RECORD_MARKER}在 {name} 的 §31–§54 里") == [
        f"{STAGE_RECORD_MARKER}在 {name} 的 §31–§54 里"
    ], "a line carrying both halves must count"
