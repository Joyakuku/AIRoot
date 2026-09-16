"""L1 guard: `references/field-values.md` against the schemas and against the code that writes them.

Three independent claims are checked, and each of them is checked in a direction that can fail:

1. **The doc's value set is the schema's value set** — for every documented field, resolving
   `cli/schema/<name>.schema.json` at the documented path must give exactly the documented values.
   A schema enum gaining a value, or the doc inventing one, turns this red.
2. **A dagger (†) is not stale** — the doc says "no code in this build writes this value". That claim
   is checked against the files the doc itself names, with a *write-shaped* pattern per field, so an
   unrelated homonym (`status = "degraded"` is not `freshness.state = "degraded"`) does not count as
   evidence. Implementing one of the daggered values turns this red.
3. **Nothing in an enabled schema is left undocumented** — every enum value set in every in-scope
   schema has to appear in the doc; the schemas deliberately left out are listed *in the doc*, and
   the two lists together must cover `cli/schema/*.schema.json` exactly.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

import schema_walk

REPO = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "cli" / "schema"
DOC = REPO / "references" / "field-values.md"
APP = REPO / "cli" / "app" / "airoot"

DAGGER = "\u2020"

#: Answers a `$ref` from the published set, so the coverage guard can ask what a document can
#: **carry** rather than only what its own text declares (§105).
RESOLVER = schema_walk.schema_ref_resolver(SCHEMA_DIR)

#: Values whose token could not be evidence even if it appeared: a JSON empty value is written as
#: `None`, not as a string literal, so a literal scan can say nothing about it either way.
UNTESTABLE = {"null"}

#: The write shape of a field, used to decide the daggers. Fields with no entry are decided by
#: plain literal presence in the named files — which is only safe when the token is not also the
#: value of some *other* field in the same file (see §74 of the draft).
WRITE_PATTERN = {
    "common::$defs.health": r"\bhealth\s*=\s*[\"']([A-Za-z_]+)[\"']",
    "common::$defs.lifecycle": r"lifecycle_status\s*=\s*[\"']([A-Za-z_]+)[\"']",
    "common::$defs.management": r"management\s*=\s*[\"']([A-Za-z_]+)[\"']",
    "common::$defs.zone": r"zone\s*=\s*[\"']([RWP])[\"']|Binding\([^)]*?[\"']([RWP])[\"']",
    "common::$defs.binding.exposure": r"Binding\([^)]*[\"']([a-z_]+)[\"']",
    "common::$defs.fileManifestEntry.mode": r"[\"']mode[\"']\s*:\s*[\"']([a-z_]+)[\"']",
    "search-response::data.freshness.state": r"\[?[\"']state[\"']\]?\s*[:=]\s*[\"']([a-z_]+)[\"']",
    "where-response::source": r"\bsource\s*=\s*[\"']([a-z_]+)[\"']",
    "registry-projection::external_references[].capability_kind":
        r"get\(\s*[\"']kind[\"']\s*,\s*[\"']([a-z_]+)[\"']\)"
        r"|[\"']kind[\"']\s*:\s*[\"']([a-z_]+)[\"']",
    "registry-projection::external_references[].source_kind":
        r"[\"']kind[\"']\s*:\s*[\"']([a-z_]+)[\"']|source_kind\s*=\s*[\"']([a-z_]+)[\"']",
}

#: Daggers whose token is distinctive enough that "nowhere in the app" is a meaningful claim: it
#: cannot be the value of a different field, so finding it anywhere as a string literal means the
#: dagger is stale. Tokens that are ordinary identifiers (`delete`, `registry`, `system`) are
#: deliberately absent — for those, claim 2's file-scoped check is the whole evidence. So is
#: `rebuilding`: nothing writes it, but `caps/search.py`'s `FRESHNESS_STATES` tuple *declares* the
#: whole vocabulary, and a declaration is not a write.
DISTINCTIVE = (
    "reapprove",
    "cancelled",
    "import_tool",
    "recreate_runtime",
    "root_relocate",
    "orphaned",
    "garbage_collectable",
    "session_env",
    "project_binding",
    "local_directory",
    "public_locator",
    "extension_handler",
    "approved_execution",
    "remote_signed",
    "adapter",
    "broker",
    "user_or_broker",
    "mutate_system",
    "explicit_generation",
    "stop_before_commit",
)

SECTION_RE = re.compile(r"^##\s+`([a-z0-9-]+)\.schema\.json`\s*$", re.MULTILINE)
EXEMPT_RE = re.compile(r"^##\s+不在这张表里的 schema\s*$", re.MULTILINE)
#: A value token in the 取值 column. The character class used to be `[A-Za-z0-9_.]`, which cannot see
#: a hyphen — so `jcs-rfc8785-compatible` (§106's `canonicalization` row) parsed as **no value at
#: all**: the row looked empty and three guards went quiet about it. Widened, and the reason is the
#: same one §104/§105 kept finding: the reader was narrower than the document it reads.
ROW_VALUE_RE = re.compile(r"`([A-Za-z0-9_.\-]+)`(" + DAGGER + r"?)")
BACKTICK_RE = re.compile(r"`([^`]+)`")


# --------------------------------------------------------------------------------------------
# parsing the document
# --------------------------------------------------------------------------------------------


class Row:
    def __init__(self, schema: str, path: str, values: list[str], daggers: set[str], producers: list[str]):
        self.schema = schema
        self.path = path
        self.values = values
        self.daggers = daggers
        self.producers = producers

    @property
    def key(self) -> str:
        return "%s::%s" % (self.schema, self.path)

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return "<Row %s %s>" % (self.key, "|".join(self.values))


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_document() -> tuple[list[Row], dict[str, list[Row]], list[str]]:
    text = DOC.read_text(encoding="utf-8")
    exempt_at = EXEMPT_RE.search(text)
    assert exempt_at is not None, "the doc must carry the '不在这张表里的 schema' section"
    body, tail = text[: exempt_at.start()], text[exempt_at.end():]

    rows: list[Row] = []
    by_schema: dict[str, list[Row]] = {}
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("## "):
            # §78: a heading that is not a schema heading *ends* the previous section. Without this
            # the tables sitting under the label section were read as rows of `common` — a parser
            # that does not know where a section stops is the same defect §75 found in the guards.
            heading = SECTION_RE.match(line)
            current = heading.group(1) if heading else None
            if current:
                by_schema.setdefault(current, [])
            continue
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = _cells(line)
        if len(cells) != 4 or cells[0] in ("字段",):
            continue
        if current is None:
            continue
        path = BACKTICK_RE.search(cells[0])
        if path is None:
            continue
        values: list[str] = []
        daggers: set[str] = set()
        for value, dagger in ROW_VALUE_RE.findall(cells[1]):
            values.append(value)
            if dagger:
                daggers.add(value)
        producers = [item for item in BACKTICK_RE.findall(cells[3]) if item != "（没有写者）"]
        row = Row(current, path.group(1), values, daggers, producers)
        rows.append(row)
        by_schema[current].append(row)

    exempt: list[str] = []
    for line in tail.splitlines():
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = _cells(line)
        if len(cells) != 2 or cells[0] in ("schema",):
            continue
        name = BACKTICK_RE.search(cells[0])
        if name is not None:
            exempt.append(name.group(1).replace(".schema.json", ""))
    assert rows, "no rows parsed out of the doc — the table shape changed"
    return rows, by_schema, exempt


ROWS, BY_SCHEMA, EXEMPT = parse_document()


# --------------------------------------------------------------------------------------------
# resolving the schemas
# --------------------------------------------------------------------------------------------


def schema_document(name: str) -> dict:
    return json.loads((SCHEMA_DIR / (name + ".schema.json")).read_text(encoding="utf-8"))


def _deref(node: dict, seen: int = 0) -> dict:
    while isinstance(node, dict) and "$ref" in node and seen < 8:
        file, _, fragment = node["$ref"].partition("#")
        target = schema_document(pathlib.Path(file).stem)
        for part in [item for item in fragment.split("/") if item]:
            target = target[part]
        node, seen = target, seen + 1
    return node


def resolve(name: str, path: str) -> list:
    node = schema_document(name)
    for raw in path.split("."):
        if raw == "$defs":
            continue
        if raw == "*":
            node = _deref(node).get("additionalProperties", {})
            continue
        many = raw.endswith("[]")
        key = raw[:-2] if many else raw
        node = _deref(node)
        node = (
            node.get("properties", {}).get(key)
            or node.get("$defs", {}).get(key)
            or node.get(key)  # JSON Schema keywords that carry a subschema: propertyNames, not, ...
        )
        assert isinstance(node, dict), "unresolved path %s in %s" % (path, name)
        if many:
            node = _deref(node).get("items", {})
    node = _deref(node)
    if "enum" in node:
        return list(node["enum"])
    if "const" in node:
        # §106: a `const` is a one-valued vocabulary, so the table documents it too.
        return [node["const"]]
    if "anyOf" in node:
        values: list = []
        for branch in node["anyOf"]:
            branch_node = _deref(branch)
            values.extend(branch_node.get("enum", []))
            if "const" in branch_node:
                values.append(branch_node["const"])
        return values
    raise AssertionError("no enum or const at %s in %s" % (path, name))


def spell(value: object) -> str:
    """One spelling for a schema value, shared with the walk (§104's "one definition").

    Booleans are JSON-spelled (`true`), because a `const` can be one and the table writes it the way
    the document does; a JSON `null` is `null` and never `None` (§97).
    """

    return schema_walk.spell(value)


def vocabulary_with_paths(name: str) -> dict[str, list[str]]:
    """Every vocabulary the schema **can carry** — `$ref` followed, `const` included (§105, §106).

    The question here is the document's, not the file's: a reader of `where-response` may meet
    `common`'s five health values, so `where-response.health` reports them; and a `const` is a
    one-valued vocabulary, so `error-response.status` reports `failed`. The path is kept because an
    undocumented vocabulary has to be *named* to be exempted, and a value-set alone cannot say which
    field it came from.

    The fixture-coverage guard deliberately does **not** follow refs and does not count consts: only
    a document's own vocabulary is one its producer controls, a fixture demonstrates an enum's
    members, and demanding a fixture for either of the other two is the false-dagger pile §97
    refused. Same walk, three questions.
    """

    return schema_walk.vocabularies_by_path(schema_document(name), resolve=RESOLVER)


def schema_files() -> list[str]:
    return sorted(item.name.replace(".schema.json", "") for item in SCHEMA_DIR.glob("*.schema.json"))


# --------------------------------------------------------------------------------------------
# reading the code the doc points at
# --------------------------------------------------------------------------------------------


def producer_files(spec: str) -> list[pathlib.Path]:
    base = REPO / spec if spec.startswith("cli/") else APP / spec
    if base.is_dir():
        return sorted(item for item in base.rglob("*") if item.suffix in (".py", ".json"))
    return [base]


def producer_text(spec: str) -> str:
    return "".join(item.read_text(encoding="utf-8") for item in producer_files(spec) if item.is_file())


def written_values(row: Row) -> set[str]:
    pattern = WRITE_PATTERN.get(row.key)
    found: set[str] = set()
    for spec in row.producers:
        text = producer_text(spec)
        if pattern is None:
            found.update(value for value in row.values if re.search(r"[\"']" + re.escape(value) + r"[\"']", text))
            continue
        for match in re.finditer(pattern, text):
            for group in match.groups():
                if group:
                    found.add(group)
    return found


# --------------------------------------------------------------------------------------------
# claim 1: the doc's values are the schema's values
# --------------------------------------------------------------------------------------------


def test_every_documented_field_resolves_to_exactly_the_documented_values() -> None:
    problems = []
    for row in ROWS:
        expected = [spell(item) for item in resolve(row.schema, row.path)]
        if sorted(expected) != sorted(row.values):
            problems.append("%s: schema=%s doc=%s" % (row.key, sorted(expected), sorted(row.values)))
    assert not problems, "documented values and schema values disagree:\n" + "\n".join(problems)


def test_every_documented_producer_path_exists() -> None:
    missing = []
    for row in ROWS:
        if not row.producers and not row.daggers:
            missing.append("%s: no producer and no dagger" % row.key)
            continue
        for spec in row.producers:
            if not (REPO / spec).exists() and not (APP / spec).exists():
                missing.append("%s: %s" % (row.key, spec))
    assert not missing, "the doc points at code that is not there:\n" + "\n".join(missing)


def test_no_value_is_documented_without_appearing_in_the_schema() -> None:
    """The other direction of claim 1: a value the schema never allowed is a doc bug, not a table row."""

    problems = []
    for row in ROWS:
        allowed = {spell(item) for item in resolve(row.schema, row.path)}
        extra = [value for value in row.values if value not in allowed]
        if extra:
            problems.append("%s: %s" % (row.key, extra))
    assert not problems, "documented values the schema does not allow:\n" + "\n".join(problems)


# --------------------------------------------------------------------------------------------
# claim 2: the daggers are not stale
# --------------------------------------------------------------------------------------------


def test_each_daggered_value_has_no_writer_in_its_own_field() -> None:
    problems = []
    for row in ROWS:
        written = written_values(row)
        for value in row.values:
            if value in UNTESTABLE:
                continue
            if value in row.daggers and value in written:
                problems.append("%s: %s is daggered but %s writes it" % (row.key, value, row.producers))
            if value not in row.daggers and value not in written:
                problems.append("%s: %s is not daggered but nothing in %s writes it" % (row.key, value, row.producers))
    assert not problems, "the daggers no longer match the code:\n" + "\n".join(problems)


# --------------------------------------------------------------------------------------------
# claim 4: "who writes it in this version" needs the document to be *produced* at all
# --------------------------------------------------------------------------------------------
#
# §92 found the first casualty of the file-scoped check: `gc-plan` credited to three modules that write
# a **plan**, because `plan.target.kind` reuses the same two words. §93 asked the same question one level
# up: which published schemas does this build *build* at all? The answer is syntax, not text -- a
# document is produced when one function's own keys (dict literals plus subscript assignments) cover
# every property the schema requires. That is what building one looks like, and it is how the twelve
# produced schemas are produced.
#
# `approval-token` is not among them: nothing in the app builds a token (ADR-0025's D1 leaves the
# production issuer to P2). Its row nevertheless named `tx/approval.py` as the writer of two fields,
# while the cell next to it said "这一版没有生产签发方" -- the two occurrences are the constants a
# **verifier accepts**, `PRODUCTION_ALGORITHM = "ed25519"` and `TEST_ALGORITHM = "test_hmac_sha256"`.
#
# Two derived exemptions, neither a hand list:
#   * a schema with no `required` properties is a fragment (`common`), not a document;
#   * a row that names a producer resolving to a **data** file is authored as data (the extension
#     manifests under `cli/extensions/`), not built by code.

REQUIRED_KEYS = "required"


def _produced_schemas() -> dict[str, list[str]]:
    """Schemas some function builds: its own keys cover every required property."""

    produced: dict[str, list[str]] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        required = set(json.loads(path.read_text(encoding="utf-8")).get(REQUIRED_KEYS, []))
        if not required:
            continue
        for module in sorted(APP.rglob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                keys: set[str] = set()
                for child in ast.walk(node):
                    if isinstance(child, ast.Dict):
                        keys |= {
                            key.value
                            for key in child.keys
                            if isinstance(key, ast.Constant) and isinstance(key.value, str)
                        }
                    if isinstance(child, ast.Assign):
                        for target in child.targets:
                            if isinstance(target, ast.Subscript):
                                slice_ = target.slice
                                if isinstance(slice_, ast.Constant) and isinstance(slice_.value, str):
                                    keys.add(slice_.value)
                if required <= keys:
                    produced.setdefault(path.name[: -len(".schema.json")], []).append(
                        "%s:%d:%s" % (module.name, node.lineno, node.name)
                    )
    return produced


def _data_authored(row: Row) -> bool:
    """A row that names a data file is authored as data: the manifest JSON *is* the document."""

    for spec in row.producers:
        for item in producer_files(spec):
            if item.suffix != ".py":
                return True
    return False


def _unproduced_schema_problems(produced: set[str]) -> list[str]:
    return [
        "%s: no function builds any %s document, so the writer column cannot name code"
        % (row.key, row.schema)
        for row in ROWS
        if row.producers
        and not _data_authored(row)
        and row.schema not in produced
        and json.loads((SCHEMA_DIR / (row.schema + ".schema.json")).read_text(encoding="utf-8")).get(
            REQUIRED_KEYS
        )
    ]


def test_no_row_claims_a_writer_for_a_document_this_build_never_builds() -> None:
    produced = _produced_schemas()
    every = {path.name[: -len(".schema.json")] for path in SCHEMA_DIR.glob("*.schema.json")}

    assert len(produced) >= 8, "only %d schemas look produced; the construction walk is broken" % len(
        produced
    )
    assert len(every - set(produced)) >= 3, "nothing is unproduced; this rule would be about nothing"

    problems = _unproduced_schema_problems(set(produced))
    assert not problems, "the writer column credits code that only validates:\n" + "\n".join(problems)

    # Non-vacuity: the defect §93 found, restored, and a produced schema made to look unproduced.
    assert _unproduced_schema_problems(set(produced) - {"search-request"}) != [], (
        "a documented document with no producer must be reported"
    )


def test_a_daggered_value_that_could_mean_only_one_thing_is_written_nowhere() -> None:
    """§74's closing rule: the file-scoped check cannot see a writer the doc did not name."""

    everything = "".join(item.read_text(encoding="utf-8") for item in APP.rglob("*.py"))
    problems = []
    for token in DISTINCTIVE:
        if token not in {value for row in ROWS for value in row.daggers}:
            problems.append("%s is in DISTINCTIVE but no row daggers it" % token)
            continue
        if re.search(r"[\"']" + re.escape(token) + r"[\"']", everything):
            problems.append("%s is daggered but the app writes it somewhere" % token)
    assert not problems, "stale daggers:\n" + "\n".join(problems)


def test_every_daggered_value_is_named_in_the_daggers_note() -> None:
    """A reader who does not know † would misread every one of them, so the note has to be there."""

    text = DOC.read_text(encoding="utf-8")
    assert DAGGER in text
    assert "没有任何代码会写出它" in text
    assert "不参与" in text and "null" in text


# --------------------------------------------------------------------------------------------
# claim 3: nothing enabled is left undocumented
# --------------------------------------------------------------------------------------------


#: Vocabularies a published schema **can carry** that this table deliberately does not document, as
#: `(schema, path)` pairs. They all come from the two schemas P2 has not built — no code writes or
#: reads them, so a row would explain a value no reader can be handed yet. Measured, not assumed
#: (§105): the reachable walk finds exactly these four, and the guard holds the set **both ways**, so
#: a fifth cannot appear silently and a stale entry cannot linger.
UNDOCUMENTED_BY_DESIGN = {
    ("broker-request", "operation"),
    ("broker-request", "client.integrity"),
    ("broker-response", "status"),
    ("broker-response", "enforcement"),
}


#: Field names whose only value is the envelope version pin: `{"const": 1}` on all twenty schemas,
#: one shared fact rather than twenty vocabularies. Documented by `docs/schema/README.md` rule 1
#: ("`schema_version: 1` is the only accepted major version") and machine-checked in
#: `test_l1_schema_catalog.test_every_schema_pins_the_version_its_documents_carry`, so it belongs to
#: that guard rather than to a table row — naming it here keeps the decision visible (draft §106).
VERSION_PIN_FIELDS = ("schema_version", "protocol_version", "schemaVersion")


def test_every_vocabulary_a_published_schema_can_carry_is_documented_or_named() -> None:
    """§105-§106: the coverage rule asks the **document's** question, and a `const` is a vocabulary.

    It used to walk each in-scope schema's *own* text and compare against the whole table, which
    made the answer depend on which list a schema was in: a vocabulary reachable only through
    `$ref` (`where-response` inherits `health` from `common`) was covered **because `common` happened
    to be in scope**, and nothing said so. Measured: following `$ref` takes `where-response` from 1
    to 7 vocabularies, `plan` from 4 to 10, `managed-tool-instance` from 0 to 10.

    §106 then found the same question still stopped at `enum`: the published set carries **33**
    value-position `const`s, and ten of them are meaningful single-valued vocabularies
    (`error-response.status = "failed"`, `search-response.data.fallback.kind = "crawl"`,
    `plan.canonicalization`, …) that no row mentioned. A `const` under `if` is *not* one of them —
    that is a dispatch condition, not a value the document carries — which is why the walk skips it
    rather than counting eight dispatch patterns as vocabularies.

    Both directions are asserted: an undocumented vocabulary must be documented, named in
    `UNDOCUMENTED_BY_DESIGN`, or be a version pin; and every name in `UNDOCUMENTED_BY_DESIGN` must
    still be an undocumented vocabulary — a stale exemption is how a deliberate hole becomes an
    accidental one.
    """

    assert schema_files(), "no published schemas; this guard is about nothing"
    everywhere = {frozenset(row.values) for row in ROWS}
    assert everywhere, "the table parsed no rows; the comparison below would be vacuous"

    found_undocumented: set[tuple[str, str]] = set()
    pins_seen: set[str] = set()
    examined = 0
    for name in schema_files():
        for path, values in vocabulary_with_paths(name).items():
            examined += 1
            if frozenset(values) in everywhere:
                continue
            if path.split(".")[-1] in VERSION_PIN_FIELDS:
                pins_seen.add(path.split(".")[-1])
                assert values == ["1"], (
                    "%s.%s is treated as a version pin but its value is %s" % (name, path, values)
                )
                continue
            found_undocumented.add((name, path))

    assert examined >= 90, "only %d vocabularies were walked; the reachable walk is not reaching" % examined
    assert set(VERSION_PIN_FIELDS) <= pins_seen, (
        "no schema carries these as a version pin any more: %s" % sorted(set(VERSION_PIN_FIELDS) - pins_seen)
    )

    unnamed = sorted(found_undocumented - UNDOCUMENTED_BY_DESIGN)
    stale = sorted(UNDOCUMENTED_BY_DESIGN - found_undocumented)
    assert unnamed == [], "undocumented vocabularies nobody named:\n" + "\n".join(
        "%s.%s" % pair for pair in unnamed
    )
    assert stale == [], "named as undocumented, but the table documents them now:\n" + "\n".join(
        "%s.%s" % pair for pair in stale
    )


def test_the_documented_and_exempt_schemas_cover_every_schema_exactly_once() -> None:
    in_scope = set(BY_SCHEMA)
    exempt = set(EXEMPT)
    every = set(schema_files())
    assert not (in_scope & exempt), "a schema is both documented and exempt: %s" % sorted(in_scope & exempt)
    assert in_scope | exempt == every, "unaccounted schemas: %s" % sorted(every - in_scope - exempt)
    for name in in_scope | exempt:
        assert (SCHEMA_DIR / (name + ".schema.json")).is_file(), name


def test_every_row_belongs_to_a_schema_that_exists() -> None:
    for row in ROWS:
        assert (SCHEMA_DIR / (row.schema + ".schema.json")).is_file(), row.key
        assert row.values, row.key


def test_the_in_scope_schemas_are_the_ones_an_agent_reads() -> None:
    """A guard so that dropping a section has to be a deliberate edit with a reason, not a diff accident."""

    assert set(BY_SCHEMA) == {
        "where-response",
        "doctor-response",
        "search-request",
        "search-response",
        "plan",
        "reference-plan",
        "gc-plan",
        "registry-projection",
        "extension-envelope",
        "extension-manifest",
        "approval-token",
        "desired-manifest",
        "runtime-instance",
        "transaction",
        "common",
        # §106: both joined because a `const` is a vocabulary too — `managed-tool-instance.kind`
        # (`managed_tool`) and `error-response.status` (`failed`) had nowhere to be documented.
        "managed-tool-instance",
        "error-response",
    }
    # `managed-tool-instance` and `error-response` left this list in §106 (they have const rows now);
    # what is left is the unbuilt `broker-*` and the marker file, whose vocabularies are either named
    # in `UNDOCUMENTED_BY_DESIGN` or are version pins.
    assert set(EXEMPT) == {"broker-request", "broker-response", "root-marker"}
    assert len(ROWS) >= 50, "the table lost rows: %d" % len(ROWS)


if __name__ == "__main__":  # pragma: no cover - manual run
    raise SystemExit(pytest.main([__file__, "-q"]))
