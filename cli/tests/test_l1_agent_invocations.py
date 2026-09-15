"""L1 guard: the machine-readable invocation metadata against the CLI it tells an agent to call.

``agents/airoot.json`` is what an agent *calls* from: one entry per question, with the argv it should
run and the fields it should trust. Both halves had a guard — §42 resolves every ``read`` path
against the document the CLI prints, and one audit check resolves every documented ``--option``
against the parser — and both leave the same hole, in opposite directions:

* the option resolver **returns early on an unknown verb** ("an unknown verb is guard group 4's
  finding"), but guard group 4 scans the documents, not this file, so a verb this file names and the
  CLI does not have was checked by nobody;
* its children lookup only *descends* when the token matches: a misspelled sub-verb
  (``env activte``) leaves the resolver on the parent, where it validates the flags against the
  parent's options and finds nothing wrong.

Measured in draft §79: all 33 entries walk cleanly through the real parser today (52 nested
sub-verbs), so this is a guard added *before* the drift rather than after it. The second check is
the other direction — a verb the parser has and no lane names is a question the metadata cannot
answer, and the file now has to say why for each of them.
"""

from __future__ import annotations

import argparse
import json
import pathlib

from airoot.cli import build_parser

REPO = pathlib.Path(__file__).resolve().parents[2]
AGENT_META = REPO / "agents" / "airoot.json"


def metadata() -> dict:
    return json.loads(AGENT_META.read_text(encoding="utf-8"))


def _subcommands(container: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    found: dict[str, argparse.ArgumentParser] = {}
    for action in container._actions:
        if isinstance(action, argparse._SubParsersAction):
            found.update(action.choices)
    return found


def _options(container: argparse.ArgumentParser) -> set[str]:
    return {option for action in container._actions for option in action.option_strings}


def test_every_invocation_resolves_through_the_real_parser() -> None:
    """Verb, nested sub-verb, and option — each token resolved where it actually lives."""

    parser = build_parser()
    problems: list[str] = []
    walked = 0

    for entry in metadata()["invocation"]:
        argv = list(entry["command"])
        question = entry["question"]
        node = parser
        for token in argv:
            children = _subcommands(node)
            if token in children:
                node = children[token]
                walked += 1
            elif token == "--":
                break  # everything after the separator belongs to the child process
            elif token.startswith("--"):
                if token not in _options(node):
                    problems.append(f"{question}: {token} is not an option here ({' '.join(argv)})")
            elif token.startswith("<") and token.endswith(">"):
                continue  # a placeholder for a value the caller supplies
            elif _subcommands(node) and token not in children:
                problems.append(f"{question}: {token!r} is not a sub-verb of this command")
            # any other bare token is a positional; the parser accepts it by construction

    assert walked >= 40, f"the walk descended only {walked} times; it is not following sub-verbs"
    assert problems == [], "agents/airoot.json tells an agent to run something the CLI cannot:\n" + "\n".join(problems)


def test_every_verb_is_either_a_lane_or_explained() -> None:
    """The other direction: a verb with no lane is a question the metadata cannot answer.

    An entry here is a decision, not a gap: `approve` and `install` cannot be completed in this
    build at all (ADR-0024), `extension` is not on an agent's question surface, and `unadopt` is
    `forget`'s compatibility alias. The set has to match exactly, so shipping a lane for one of
    them — or adding a verb without one — turns this red.
    """

    parser = build_parser()
    document = metadata()
    lanes = {entry["command"][0] for entry in document["invocation"]}
    uncovered = document["uncovered_verbs"]

    every = set(_subcommands(parser))
    assert sorted(every - lanes - set(uncovered)) == [], (
        "these verbs have no lane and no explanation: %s" % sorted(every - lanes - set(uncovered))
    )
    assert sorted(set(uncovered) - (every - lanes)) == [], (
        "these are explained but either have a lane or are not verbs at all: %s"
        % sorted(set(uncovered) - (every - lanes))
    )

    unblockers = {entry["unblocked_by"] for entry in document["deferred"].values()}
    for verb, entry in sorted(uncovered.items()):
        assert len(entry["why_no_lane"]) > 40, f"{verb}: the reason is a placeholder"
        assert entry["unblocked_by"] is None or entry["unblocked_by"] in unblockers, (
            f"{verb}: unblocked_by must be one of the deferral register's words ({sorted(unblockers)}) or null"
        )
