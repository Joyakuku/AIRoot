"""L1 guard: the label vocabularies the schemas never enumerated (draft §77).

`evidence[].kind` and `where`'s `selection_reason` are plain strings in the published schemas, so
there is no schema authority to read the values off — the only authority is the code that writes
them, and until §77 nothing checked that the two agreed. Both directions are checked here, because
each is a different lie: an undocumented label an agent cannot interpret, and a documented label the
build never writes (an agent writing a branch for something that cannot happen).
"""

from __future__ import annotations

import pathlib
import re

from airoot.exits import REASON_EXIT

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "cli" / "app" / "airoot"
DOC = REPO / "references" / "field-values.md"

SECTION = "## schema 没有枚举的标签"
CIRCLE = "\u2218"

#: field -> how its values are written. `operation` is scoped to the four response builders: the
#: plan's own `operation` is the published `plan.schema.json` field, documented in the schema
#: section of the reference, and mixing the two is exactly the confusion this section exists to
#: stop (draft §78).
FIELD_FILES: dict[str, tuple[str, ...]] = {
    "operation": ("cli.py", "caps/search.py", "caps/exposure.py", "caps/rebuild.py"),
}
EXPRESSION_PROBES: dict[str, tuple[str, ...]] = {
    "operation": (r'"operation":\s*([^,\n]+)',),
    "origin": (r"\borigin=([^,\n]+)", r'"origin":\s*([^,\n]+)'),
    "size_source": (r"\bsize_source=([^,\n]+)", r'"size_source":\s*([^,\n]+)'),
    "version_source": (r'"version_source":\s*([^,\n]+)',),
    "outcome": (r'"outcome":\s*([^,\n]+)', r"\boutcome=([^,\n]+)"),
}
WRITER = {"evidence[].kind", "where.selection_reason"} | set(EXPRESSION_PROBES)

ROW_RE = re.compile(r"`([A-Za-z_][A-Za-z_0-9-]*)`(" + CIRCLE + r")?")
HEADING_RE = re.compile(r"^###\s+`([^`]+)`", re.MULTILINE)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_section() -> dict[str, dict[str, set[str]]]:
    """field -> {value: marks}, parsed out of the doc's own table.

    §78: chunks are bounded by **every** `###` heading, including ones with trailing prose after the
    backticked name. The first version only recognised bare headings, so the rows of the next
    subsection were read as values of the previous field — the same "where does a section end"
    defect §75 found in the guards, this time in the parser.
    """

    text = DOC.read_text(encoding="utf-8")
    start = text.index(SECTION)
    end = text.index("\n## ", start + len(SECTION))
    body = text[start:end]

    starts = [(match.group(1), match.start(), match.end()) for match in HEADING_RE.finditer(body)]
    found: dict[str, dict[str, set[str]]] = {}
    for index, (field, _, heading_end) in enumerate(starts):
        if field not in WRITER:
            continue
        chunk_end = starts[index + 1][1] if index + 1 < len(starts) else len(body)
        rows: dict[str, set[str]] = {}
        for line in body[heading_end:chunk_end].splitlines():
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            cells = _cells(line)
            if len(cells) != 3 or cells[0] == "取值":
                continue
            for value, circle in ROW_RE.findall(cells[0]):
                rows[value] = {"∘"} if circle else set()
        found[field] = rows
    return found


def _evidence_kinds_written() -> set[str]:
    found: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'\{\s*"kind":\s*"([a-z_]+)"\s*,\s*"detail"', text, flags=re.S))
        found |= set(re.findall(r'"kind":\s*"([a-z_]+)",\s*\n\s*"detail"', text))
    return found


def _selection_reasons_written() -> set[str]:
    """The values `where` can put in `selection_reason`, read off the two places that decide it.

    Only two sites assign it: the helper that names why a candidate was chosen, and the branch that
    explains why nothing was. A constant is followed to its literal, because that is how the
    degraded case is spelled — and everything else in the module (other codes, other constants) is
    deliberately *not* collected: this field has its own vocabulary, and the guard has to say so.
    """

    text = (APP / "caps/where.py").read_text(encoding="utf-8")
    constants = dict(re.findall(r'^([A-Z][A-Z0-9_]+) = "([A-Z][A-Z0-9_]+)"', text, re.M))

    helper = text.split("def _selection_reason_for(", 1)[1].split("\ndef ", 1)[0]
    found = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', helper))

    assignment = text.split("selection_reason: str | None = None", 1)[1]
    for literal, name in re.findall(r'selection_reason = (?:"([A-Z][A-Z0-9_]+)"|([A-Z][A-Z0-9_]+))', assignment):
        found.add(literal or constants[name])
    return found


def _envelope_labels_written(field: str) -> set[str]:
    """Every literal in the right-hand side that assigns `field`, so a ternary contributes both sides.

    Reading the whole expression (rather than `field="literal"`) is what makes `size_source` come out
    as three values and `version_source` as two: `"declared" if ... else "unknown"` is one assignment
    with two literals, and a probe that stopped at the first one would have "documented" a vocabulary
    that is missing half its words.
    """

    wanted = FIELD_FILES.get(field)
    found: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if wanted is not None and rel not in wanted:
            continue
        text = path.read_text(encoding="utf-8")
        for probe in EXPRESSION_PROBES[field]:
            for expression in re.findall(probe, text):
                # A literal that is a *key* (`"outcome": str(row[...])`) is the field naming itself,
                # not a value; so is the field's own name when it is read back out of a document.
                cleaned = re.sub(r"[\"'][a-z_0-9]+[\"']\s*:", " ", expression)
                found |= set(re.findall(r'"([a-z][a-z_0-9-]*)"', cleaned)) - {field}
    return found


def test_the_section_this_guard_reads_is_the_one_it_thinks_it_is() -> None:
    parsed = parse_section()
    assert set(parsed) == WRITER, f"the doc's label section changed shape: {sorted(parsed)}"
    for field, rows in parsed.items():
        assert rows, f"{field} has no rows"


def test_every_envelope_label_the_app_writes_is_the_documented_one() -> None:
    """Every field in the label section, both directions, against the code that assigns it."""

    parsed = parse_section()
    problems: list[str] = []
    for field in sorted(WRITER - {"evidence[].kind", "where.selection_reason"}):
        documented = set(parsed[field])
        written = _envelope_labels_written(field)
        for missing in sorted(written - documented):
            problems.append("%s: %s is written but not explained" % (field, missing))
        for invented in sorted(documented - written):
            problems.append("%s: the doc explains %s, which no code writes" % (field, invented))
    assert problems == [], "\n".join(problems)


def test_the_evidence_kinds_the_app_writes_are_exactly_the_documented_ones() -> None:
    documented = set(parse_section()["evidence[].kind"])
    written = _evidence_kinds_written()
    assert sorted(written - documented) == [], (
        "these evidence kinds are written but not explained anywhere: %s" % sorted(written - documented)
    )
    assert sorted(documented - written) == [], (
        "the doc explains evidence kinds this build never writes: %s" % sorted(documented - written)
    )


def test_the_selection_reasons_the_app_writes_are_exactly_the_documented_ones() -> None:
    documented = set(parse_section()["where.selection_reason"])
    written = _selection_reasons_written()
    assert sorted(written - documented) == [], (
        "these selection reasons are written but not explained anywhere: %s" % sorted(written - documented)
    )
    assert sorted(documented - written) == [], (
        "the doc explains selection reasons this build never writes: %s" % sorted(documented - written)
    )


def test_a_selection_reason_marked_as_a_reason_code_really_is_one() -> None:
    """`where` carries two code-like fields; the marker is how a reader tells them apart."""

    rows = parse_section()["where.selection_reason"]
    wrong = sorted(
        value
        for value, marks in rows.items()
        if (CIRCLE in marks) != (value in REASON_EXIT)
    )
    assert wrong == [], f"the ∘ marks and exits.REASON_EXIT disagree about these: {wrong}"
    assert any(CIRCLE in marks for marks in rows.values()), "the marker is gone, so the note is vacuous"


def test_every_documented_label_names_a_writer_that_exists() -> None:
    text = DOC.read_text(encoding="utf-8")
    start = text.index(SECTION)
    body = text[start: text.index("\n## ", start + len(SECTION))]
    missing = []
    for line in body.splitlines():
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = _cells(line)
        if len(cells) != 3 or cells[0] == "取值":
            continue
        for spec in re.findall(r"`([^`]+)`", cells[2]):
            if not (APP / spec).exists():
                missing.append(spec)
    assert missing == [], f"the doc points at code that is not there: {sorted(set(missing))}"
