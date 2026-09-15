"""L1: the Skill adapter must not drift from the CLI (draft §22).

A Skill document that is never tested is wrong within two weeks. These tests pin the three
things an agent actually depends on:

* **every command the Skill tells an agent to run exists** (and commands that do *not* exist are
  listed explicitly as unimplemented rather than quietly documented as available);
* the confirmation triple equals `CONFIRMATION_OPTIONS` — an agent that offers different wording
  is offering a different contract;
* the exit-code table equals `REASON_EXIT`/`EXIT_MEANINGS`.

They also hold the honesty rules: no variable run state in the Skill text, and `policy_only`
must not be described as protected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from airoot.caps.planner import CONFIRMATION_OPTIONS
from airoot.cli import build_parser
from airoot.exits import EXIT_MEANINGS, REASON_EXIT

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "SKILL.md"
AGENTS = REPO / "agents" / "airoot.json"
REFERENCES = REPO / "references"

#: The marker `references/field-values.md` uses for "no writer in this build writes this value".
DAGGER = "\u2020"

# Every verb that may appear as `airoot <verb>` in the Skill text. Derived from the parser at
# test time, so a removed command fails here instead of misleading an agent later.
def known_verbs() -> set[str]:
    parser = build_parser()
    subparsers = [
        action for action in parser._actions if getattr(action, "choices", None) and action.dest == "command"
    ]
    assert subparsers, "the CLI has no subcommand table"
    return set(subparsers[0].choices)


def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def agents_document() -> dict:
    return json.loads(AGENTS.read_text(encoding="utf-8"))


def known_commands() -> set[str]:
    """Every ``(command, subcommand)`` path the CLI actually has.

    Verb-level granularity would let an unimplemented subcommand hide behind an implemented verb
    (`tool retire` exists, so "tool" would look documented while `tool pin` does not exist). The
    guard therefore compares command *paths*.
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
            continue
        for sub_name in nested[0].choices:
            paths.add(f"{name} {sub_name}")
    return paths


def invoked_verbs(text: str, *, unimplemented: set[str]) -> set[str]:
    """Every `airoot <verb> [<sub>]` occurrence. Front-matter prose ("name: airoot\\ndescription: …")
    must not be mistaken for a command, so only a same-line space-separated verb counts."""

    found: set[str] = set()
    for match in re.finditer(r"airoot[ \t]+([a-z][a-z-]*)\b(?!\s*:)(?:[ \t]+([a-z][a-z-]*)\b)?", text):
        verb, sub = match.group(1), match.group(2)
        path = f"{verb} {sub}" if sub else verb
        if sub and path in unimplemented:
            # A documented-but-unimplemented subcommand must be matched as a *path*, or the bare
            # verb would be reported as unknown and the guard would fail for the wrong reason.
            found.add(path)
        elif sub and path in known_commands():
            found.add(path)
        else:
            found.add(verb)
    return {item for item in found if item not in unimplemented}


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #


def test_the_skill_entry_lives_at_the_skill_root() -> None:
    """§5.2:495 — AIROOT *is* the Skill directory; `cli/` is a subdirectory of it."""

    assert SKILL.is_file(), "AIROOT\\SKILL.md is the Skill entry point"
    assert not (REPO / "cli" / "SKILL.md").exists(), "SKILL.md must not live under cli/"


def test_the_entry_point_declares_name_and_description() -> None:
    text = skill_text()
    assert text.startswith("---\n"), "a Skill loader needs front-matter first"
    front_matter = text.split("---", 2)[1]
    assert re.search(r"^name:\s*\S+", front_matter, re.M), "front-matter needs a name"
    assert re.search(r"^description:\s*\S+", front_matter, re.M), "front-matter needs a description"

    document = agents_document()
    assert document["skill_entry"] == "SKILL.md"
    assert document["name"] == re.search(r"^name:\s*(\S+)", front_matter, re.M).group(1)


def test_the_skill_does_not_embed_variable_run_state() -> None:
    """§16.0:1715 — run state belongs in the CLI, not in the Skill text."""

    text = skill_text()
    assert not re.search(r"[A-Za-z]:\\\\?AIRoot", text, re.I), "no absolute root paths in the Skill"
    assert "registry.json" not in text, "the Skill must not describe derived state as if it were input"
    assert not re.search(r"protocol_version:\s*\d", text)


def test_the_skill_refers_to_the_machine_readable_contract() -> None:
    text = skill_text()
    assert "cli/schema" in text, "the Skill must point at the published schemas"
    assert "references/" in text, "and at the on-demand references"


def test_policy_only_is_never_described_as_protected() -> None:
    text = skill_text()
    assert "policy_only" in text
    assert re.search(r"policy_only[^.]*不能", text) or "不是 ACL" in text
    document = agents_document()
    assert "policy_only" in document["honesty"]["security_mode_note"]


# --------------------------------------------------------------------------- #
# the drift guard
# --------------------------------------------------------------------------- #


def test_every_command_the_skill_names_exists() -> None:
    document = agents_document()
    unimplemented = set(document["not_implemented"])
    verbs = known_commands()

    mentioned = invoked_verbs(skill_text(), unimplemented=unimplemented)
    mentioned |= invoked_verbs(json.dumps(document), unimplemented=unimplemented)
    for path in sorted(REFERENCES.glob("*.md")):
        mentioned |= invoked_verbs(path.read_text(encoding="utf-8"), unimplemented=unimplemented)

    unknown = sorted(mentioned - verbs)
    assert unknown == [], f"the Skill tells an agent to run commands that do not exist: {unknown}"


def test_unimplemented_commands_are_listed_and_really_unimplemented() -> None:
    """The list must be *true* in both directions: a command that exists belongs in the map.

    `search` used to live here (file_search was P3). Its protocol surface now exists (draft §31), so
    it moved to the command map — and the guards in this file are what forced that move.
    """

    document = agents_document()
    listed = set(document["not_implemented"])
    assert listed, "there is at least one documented-but-unimplemented command"
    verbs = known_commands()
    still_missing = {verb for verb in listed if verb not in verbs}
    assert still_missing == listed, f"these are implemented now and must be documented: {listed - still_missing}"
    assert "search" not in listed, "the search protocol surface exists; it must not be declared missing"
    assert "search" in verbs, "and it must be reachable from the parser"
    # What is left needs P2 (broker / PATH writes) or semantics the project has not frozen yet.
    assert {"path backup", "path restore", "root adopt", "root relocate"} <= listed


def test_every_documented_invocation_maps_to_a_real_command() -> None:
    paths = known_commands()
    for entry in agents_document()["invocation"]:
        parts = [part for part in entry["command"] if not part.startswith("<")]
        verb = " ".join(parts[:2]) if " ".join(parts[:2]) in paths else parts[0]
        assert verb in paths, f"invocation table names an unknown command: {verb}"
        assert entry["question"], "every invocation needs the question it answers"
        assert entry["read"], "and the fields the agent should read out"


def test_the_confirmation_triple_is_the_frozen_one() -> None:
    document = agents_document()
    assert tuple(document["confirmation_options"]) == CONFIRMATION_OPTIONS

    text = skill_text()
    body = text.split("确认协议", 1)[1]
    for option in CONFIRMATION_OPTIONS:
        assert option in body, f"the Skill text omits the {option!r} option"


def test_the_documented_exit_codes_match_the_frozen_mapping() -> None:
    document = agents_document()
    assert set(document["exit_codes"]) == {str(code) for code in sorted(set(REASON_EXIT.values()))}
    for code, meaning in document["exit_codes"].items():
        assert EXIT_MEANINGS[int(code)] in meaning or meaning.split(" ")[0] in EXIT_MEANINGS[int(code)]


def test_the_reason_code_reference_only_lists_real_codes() -> None:
    """A made-up reason code in the reference would send an agent hunting for nonsense."""

    text = (REFERENCES / "reason-codes.md").read_text(encoding="utf-8")
    # `CONFLICT_MANAGED_BROKEN` is deliberately mentioned as "no longer emitted"; it is still a
    # real registered code, so the assertion below covers it too.
    mentioned = set(re.findall(r"`([A-Z][A-Z0-9_]{2,63})`", text))
    unknown = sorted(code for code in mentioned if code not in REASON_EXIT)
    assert unknown == [], f"the reference names unregistered reason codes: {unknown}"


def test_the_skill_does_not_offer_an_approval_it_cannot_obtain() -> None:
    """SKILL.md tells the agent to hand the plan and hash to the user — and then stops.

    That hand-off presumes a channel that can approve, and this build has none: the core only
    verifies, the sole issuer is the test one. An agent following the Skill as written would ask
    the user for an approval the user has no way to give (draft §67). Same class as the read-only
    claim in guard group 19: a document may not present an unavailable step as available.
    """

    from airoot.tx.approval import ISSUER_PENDING

    text = skill_text()
    assert "plan 文件路径与 hash 交给用户" in text, "the approval hand-off is gone; this guard is about nothing"
    assert ISSUER_PENDING in text, (
        "SKILL.md tells the agent to hand the plan to the user for approval without saying that this "
        "build cannot mint a token; the agent will describe a step that cannot be performed"
    )
    assert "ADR-0024" in text, "the honest block must point at the pending decision"
    assert "提案" in text, "ADR-0024 is a proposal, not a decision; the Skill must not overstate it"

    # Widened in §68: one caveat in the 批准 section is ~50 lines away from the command map the agent
    # *acts on*, and §67's own lesson was that a caveat must sit where the presumption is. So every
    # site that prescribes approval carries this marker. Exempt by reason, not by line number: the
    # 批准 section *is* the pointer, the 绝不做的清单 lists prohibitions (no "how" to point at), and a
    # fenced block is a legend. Backticked spans are stripped first, or a reason code whose *name*
    # contains APPROVAL would look like a prescription.
    for line_no, line in _outside_approval_lines(text):
        if re.search(r"批准|approval|token", re.sub(r"`[^`]*`", "", line), re.I):
            assert APPROVAL_POINTER in line, (
                f"SKILL.md:{line_no} prescribes approval without the pointer, so an agent reading that "
                f"line alone describes a step this build cannot perform: {line.strip()[:120]!r}"
            )


#: The compact per-site pointer. It is short on purpose: it has to fit inside a table cell that an
#: agent reads while deciding what to run.
APPROVAL_POINTER = "（这个 build 签不出 token：见《批准》）"


def _outside_approval_lines(text: str) -> list[tuple[int, str]]:
    """Lines that may prescribe something, i.e. outside the 批准 section, 绝不做的清单 and fences."""

    exempt_headings = ("## 批准", "## 绝不做的清单")
    exempt = False
    in_fence = False
    kept: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if line.startswith("## "):
            exempt = line.startswith(exempt_headings)
        if not exempt and not in_fence:
            kept.append((line_no, line))
    return kept


def test_the_agent_metadata_does_not_offer_an_approval_it_cannot_obtain() -> None:
    """The third document with the same defect, and the one the agent reads *first*.

    `agents/airoot.json` is the machine-readable lane map; its import lane ended at "approve it
    and run install" — the same unavailable step SKILL.md pointed at. The guard is deliberately
    narrow: the caveat must sit in the *same note* that presumes approval, not merely somewhere in
    the file, or a reader of that lane is still sent to a step nobody can perform (draft §67).

    §68 widened it from that one lane to **every** lane whose note so much as mentions a token,
    because the same measurement found two more (`env persist`, `tool gc --plan`) that were only
    fixed by hand — a guard that names one lane cannot protect the next one.
    """

    from airoot.tx.approval import ISSUER_PENDING

    presuming = [
        entry["notes"]
        for entry in agents_document()["invocation"]
        if re.search(r"token|approv", entry.get("notes", ""), re.I)
    ]
    assert any("approve it and run install" in note for note in presuming), (
        "the import lane this guard was written for is gone; the guard is now about something else"
    )
    for note in presuming:
        assert ISSUER_PENDING in note, f"lane note presumes approval without the boundary: {note!r}"
        assert "ADR-0024" in note, "the boundary must point at the pending decision"


def test_the_reason_code_reference_does_not_prescribe_an_approval_nobody_can_give() -> None:
    """Exit 4's reference page told the agent to wait for a human approval, full stop (draft §68).

    `references/reason-codes.md` already carried this kind of caveat for exit 5 ("P1 has no broker,
    do not tell the user to edit HKLM") — exit 4 was the section that had none, and an agent that
    consulted it would ask the user to produce something nothing in this build can produce. The
    boundary must sit in that section, not merely in the file.
    """

    from airoot.tx.approval import ISSUER_PENDING

    text = (REFERENCES / "reason-codes.md").read_text(encoding="utf-8")
    section = text.split("## 4 —", 1)[1].split("\n## ", 1)[0]
    assert "等人工批准" in section, "the hand-off prescription is gone; this guard is about nothing"
    assert ISSUER_PENDING in section, (
        "the exit-4 reference prescribes waiting for an approval without saying that this build "
        "cannot issue one"
    )
    assert "ADR-0024" in section and "提案" in section, "it must point at the pending decision, as a proposal"


def test_the_entry_document_marks_the_commands_it_cannot_complete() -> None:
    """AGENTS.md §6 is the block commands get copied from, and it carries `--token-file` (draft §68).

    §1 and §8 both state the boundary, but the rule since §67 is that the caveat sits where the
    instruction is — and this is the document an operator opens first. Same rule as the Skill's
    command map and the metadata lanes.
    """

    from airoot.tx.approval import ISSUER_PENDING

    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "--token-file" in text, "the token commands are gone; this guard is about nothing"
    assert ISSUER_PENDING in text, (
        "AGENTS.md shows `--token-file` commands without saying that this build cannot issue a token"
    )
    assert "ADR-0024" in text, "and without pointing at the pending decision"


def test_the_reference_set_is_present() -> None:
    for name in ("reason-codes.md", "confirmation.md", "field-values.md"):
        assert (REFERENCES / name).is_file(), f"missing on-demand reference: {name}"


def test_the_skill_points_at_the_field_value_table_it_now_depends_on() -> None:
    """§74: `references/field-values.md` is only on-demand if the entry document says when to open it.

    Measured before §74: 58 enums across the schemas, 14 of them with no agent-facing document at
    all — an agent could read `verification`/`source_kind`/`value_kind` and have nowhere to look.
    A reference nobody is told to open is a file, not a reference.
    """

    text = skill_text()
    assert "references/field-values.md" in text
    assert DAGGER in text, "the entry document has to explain what a daggered value means"
    assert "三套 `scope`" in text
    # §77: not every label a response carries comes from a schema, and the two code-like fields of
    # `where` are the place an agent is most likely to look one up in the other's table.
    assert "evidence[].kind" in text and "selection_reason" in text
    assert "自由字符串" in text


def test_the_skill_explains_the_zone_vocabulary_its_responses_carry() -> None:
    """ADR-0022 made the exclusion visible; a visible field still needs a documented meaning.

    Draft §73 measured that `zone` appeared **zero** times in every agent-facing document, while two
    responses carried it — `where`'s `candidates[].machine_discoverable` and `inventory`'s
    `bindings[].zone` — and neither lane that exposes the fact read the field. An agent could print
    `machine_discoverable: false` with nothing to say about it. This is §67's rule applied to a
    vocabulary rather than a caveat: what a response reports, the document that interprets it names.
    """

    text = skill_text()
    assert "machine_discoverable" in text, "the flag `where` reports has no documented meaning"
    assert "zone" in text and "R" in text and "W" in text and "P" in text, "the vocabulary is unread"
    # Both boundaries, because either alone is a different rule: W is never machine-discovered, and
    # W *is* reachable through explicit activation.
    assert "--session" in text or "--project" in text, "the second boundary (explicit activation) is gone"

    lanes = agents_document()["invocation"]
    where_lane = next(entry for entry in lanes if entry.get("command", [None])[0] == "where")
    inventory_lane = next(entry for entry in lanes if entry.get("command", [None])[0] == "inventory")
    assert any("machine_discoverable" in path for path in where_lane["read"]), where_lane
    assert any("zone" in path for path in inventory_lane["read"]), inventory_lane


def test_the_skill_states_that_a_digest_is_not_a_signature() -> None:
    """v1 does no signature verification; implying otherwise would be worse than admitting it."""

    text = skill_text()
    document = agents_document()
    assert "签名" in text and "不等于" in text
    assert "signature_note" in document["honesty"]
    assert "not a signature" in document["honesty"]["signature_note"]


def test_the_skill_never_promises_the_forbidden_claims() -> None:
    text = skill_text()
    document = agents_document()
    assert "绝不做的清单" in text
    # The exact forbidden phrasing from AGENTS.md §8 must appear verbatim in the Skill too, so an
    # agent reading only the Skill still knows what it may not claim.
    assert "AIROOT 已实现 / 已可用 / 已具备 Everything 级性能" in text
    assert document["never"], "the machine-readable form needs the same list"
    assert "AGENTS.md" in document["honesty"]["allowed_claims"]
