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


def test_the_agent_metadata_does_not_offer_an_approval_it_cannot_obtain() -> None:
    """The third document with the same defect, and the one the agent reads *first*.

    `agents/airoot.json` is the machine-readable lane map; its import lane ended at "approve it
    and run install" — the same unavailable step SKILL.md pointed at. The guard is deliberately
    narrow: the caveat must sit in the *same note* that presumes approval, not merely somewhere in
    the file, or a reader of that lane is still sent to a step nobody can perform (draft §67).
    """

    from airoot.tx.approval import ISSUER_PENDING

    presuming = [
        entry.get("notes", "")
        for entry in agents_document()["invocation"]
        if "approve it and run install" in entry.get("notes", "")
    ]
    assert presuming, "no lane presumes approval any more; this guard is about nothing"
    for note in presuming:
        assert ISSUER_PENDING in note, f"lane note presumes approval without the boundary: {note!r}"
        assert "ADR-0024" in note, "the boundary must point at the pending decision"


def test_the_reference_set_is_present() -> None:
    for name in ("reason-codes.md", "confirmation.md"):
        assert (REFERENCES / name).is_file(), f"missing on-demand reference: {name}"


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
