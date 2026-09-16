"""L1 guard: registered reason codes vs the codes this build can actually produce (draft §75).

`exits.py` maps every code it registers to an exit code, and a registered code is a *promise about
meaning*, not a promise that the code happens. Which codes this build can actually produce cannot be
read off a call site — many arrive through variables and constants, and one arrives from a library no
verb reaches (§114's `CALLER_NOT_AUTHORIZED`) — so the tree is asked a narrower question: does the
literal string appear anywhere **other than the mapping table**? A code that does not is one an agent
should not write a branch for, and `references/reason-codes.md` has to say so.
"""

from __future__ import annotations

import pathlib
import re

from airoot.exits import REASON_EXIT

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "cli" / "app" / "airoot"
REFERENCE = REPO / "references" / "reason-codes.md"
SECTION = "## 这一版发不出来的码"

DAGGER = "\u2020"


def names_code(text: str, code: str) -> bool:
    return re.search(r"(?<![A-Z0-9_])" + re.escape(code) + r"(?![A-Z0-9_])", text) is not None


def codes_produced_by_the_app() -> set[str]:
    """Registered codes whose literal appears somewhere other than `exits.py`."""

    text = ""
    for path in sorted(APP.rglob("*.py")):
        if path.name == "exits.py":
            continue
        text += "\n" + path.read_text(encoding="utf-8")
    return {code for code in REASON_EXIT if re.search(r"[\"']" + re.escape(code) + r"[\"']", text)}


def recorded_as_unproducible() -> list[str]:
    text = REFERENCE.read_text(encoding="utf-8")
    start = text.index(SECTION)
    rows = []
    for line in text[start:].splitlines():
        if not line.startswith("|"):
            continue
        cell = line.strip().strip("|").split("|")[0].strip()
        match = re.match(r"`([A-Z][A-Z0-9_]+)`" + re.escape(DAGGER) + r"?$", cell)
        if match:
            rows.append(match.group(1))
    return rows


def test_the_section_this_guard_reads_is_the_one_it_thinks_it_is() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    assert SECTION in text, "the unproducible-codes section was renamed or removed"
    assert DAGGER in text, "the dagger convention has to be explained to the reader"
    rows = recorded_as_unproducible()
    assert rows, "no codes parsed out of the section"
    assert all(code in REASON_EXIT for code in rows), "the section names an unregistered code"


def test_a_registered_code_is_either_producible_or_recorded_as_unproducible() -> None:
    """Both directions, because each of them is a different lie.

    Missing from the section: an agent reads an explanation for a code that cannot happen and
    writes a branch for it. Wrongly in the section: an agent stops handling a code that does
    happen — the worse of the two.
    """

    producible = codes_produced_by_the_app()
    recorded = set(recorded_as_unproducible())

    should_be_recorded = set(REASON_EXIT) - producible
    stale = sorted(should_be_recorded - recorded)
    wrong = sorted(recorded - should_be_recorded)

    assert stale == [], f"no writer produces these, but the reference does not say so: {stale}"
    assert wrong == [], f"the reference calls these unproducible, but the app writes them: {wrong}"


def test_every_recorded_code_is_also_look_up_able_in_the_document() -> None:
    """The list is a pointer, not the explanation: each code still needs its own entry above."""

    text = REFERENCE.read_text(encoding="utf-8")
    body = text[: text.index(SECTION)]
    problems = [code for code in recorded_as_unproducible() if not names_code(body, code)]
    assert problems == [], f"listed as unproducible but never explained: {problems}"


def test_the_unproducible_section_is_not_a_second_copy_of_the_whole_table() -> None:
    """A section that swallows everything stops being information and starts being noise."""

    recorded = recorded_as_unproducible()
    assert 0 < len(recorded) < len(REASON_EXIT) / 2, f"{len(recorded)} of {len(REASON_EXIT)} codes"
