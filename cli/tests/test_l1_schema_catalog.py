"""L1 guard: the schema catalog's own rules against the files it describes (draft §80).

`docs/schema/README.md` opens with the boundary table (one row per schema) and five compatibility
rules. Every one of them was **prose**: the file's only guard was a count ("19 schemas"), so a
twentieth schema could ship with no boundary row, no version pin and an object that accepts unknown
properties, and nothing would go red — while the same README tells every contributor that changing a
type or a required field "requires a new schema id".

Measured before writing this (§80): all nineteen files keep the rules today, in three shapes — the
document schemas pin `schema_version` through `common.schema.json#/$defs/schemaVersion` (`const: 1`),
`broker-request` pins the IPC envelope's `protocol_version` (which is what the broker design's
envelope carries), and `common` is the fragment file that *defines* both. This module is therefore a
guard added before the drift, not a repair: it changes no schema.
"""

from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "cli" / "schema"
CATALOG = REPO / "docs" / "schema" / "README.md"

#: The fragment file: it defines the pieces the others reference, so it has no version of its own.
FRAGMENT_FILE = "common"

#: The IPC envelope pins its version under the name the broker design gives it. A schema here has to
#: pin a version, not this particular spelling — but the set is asserted exactly, so dropping the pin
#: from `broker-request` (or adding a second protocol-versioned schema) is a deliberate edit.
VERSION_BY_PROTOCOL = {"broker-request": "the broker design's IPC envelope carries `protocol_version`, not `schema_version`"}

#: Nodes that constrain an existing object instead of describing one: a JSON Schema `if`/`then`/`else`
#: branch or an `allOf` member adds no properties, so `additionalProperties` there would be meaningless.
#: Mutating this set is one of this module's red checks.
CONDITIONAL_KEYWORDS = frozenset({"if", "then", "else", "allOf", "anyOf", "oneOf", "not"})

MIN_BOUNDARY_LENGTH = 20


def schemas() -> dict[str, dict]:
    return {
        path.name.replace(".schema.json", ""): json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }


def catalog_rows() -> dict[str, str]:
    """The boundary table: schema file name (without `.schema.json`) -> boundary description."""

    text = CATALOG.read_text(encoding="utf-8")
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*`([a-z0-9-]+)\.schema\.json`\s*\|\s*(.+?)\s*\|$", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _records(node: object, path: str = "") -> list[tuple[str, dict]]:
    """Every node that describes an object, i.e. that has `properties` and is not a conditional branch."""

    found: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        parent = path.rsplit("/", 1)[-1] if path else ""
        if "properties" in node and parent not in CONDITIONAL_KEYWORDS:
            found.append((path or "<root>", node))
        for key, value in node.items():
            found += _records(value, path + "/" + key)
    elif isinstance(node, list):
        for item in node:
            found += _records(item, path + "[]")
    return found


def test_the_catalog_table_and_the_schema_files_are_the_same_set() -> None:
    """Both directions, because each is a different lie: an unlisted schema, or a row for nothing."""

    every = set(schemas())
    listed = set(catalog_rows())

    assert sorted(every - listed) == [], (
        "these schemas have no boundary row in docs/schema/README.md: %s" % sorted(every - listed)
    )
    assert sorted(listed - every) == [], (
        "the catalog has rows for schemas that do not exist: %s" % sorted(listed - every)
    )

    short = {name: text for name, text in catalog_rows().items() if len(text) < MIN_BOUNDARY_LENGTH}
    assert short == {}, f"boundary rows that say nothing: {short}"

    descriptions = list(catalog_rows().values())
    assert len(set(descriptions)) == len(descriptions), "two boundary rows share one description"


def test_the_catalog_only_names_schemas_that_exist() -> None:
    """The corrections table refers to files by name; a renamed file must not leave a stale mention."""

    text = CATALOG.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"([a-z0-9-]+)\.schema\.json", text))
    unknown = sorted(name for name in mentioned if name not in schemas())
    assert unknown == [], f"docs/schema/README.md names schemas that do not exist: {unknown}"


def test_every_schema_pins_the_version_its_documents_carry() -> None:
    """Rule 1: `schema_version: 1` is the only accepted major version — wherever a document carries one."""

    every = schemas()
    version_def = every[FRAGMENT_FILE]["$defs"]["schemaVersion"]
    assert version_def.get("const") == 1, f"common.$defs.schemaVersion is no longer const 1: {version_def}"

    pinned, protocol = set(), set()
    for name, document in every.items():
        if name == FRAGMENT_FILE:
            continue
        property_ = document.get("properties", {}).get("schema_version")
        envelope = document.get("properties", {}).get("protocol_version")
        if property_ == {"$ref": "common.schema.json#/$defs/schemaVersion"}:
            pinned.add(name)
        elif envelope is not None:
            # Present is not enough: the first version of this guard accepted `{"type": "integer"}`,
            # which pins nothing. The IPC envelope has to pin 1 the way the documents do — and the
            # mutation that dropped the `const` is how the weaker check was found (§80).
            assert envelope.get("const") == 1, f"{name}: protocol_version is not pinned to 1: {envelope}"
            protocol.add(name)

    unpinned = sorted(set(every) - {FRAGMENT_FILE} - pinned - protocol)
    assert unpinned == [], f"these schemas carry no version pin at all: {unpinned}"
    assert protocol == set(VERSION_BY_PROTOCOL), (
        "the versioned-by-protocol set changed: %s (expected %s)"
        % (sorted(protocol), sorted(VERSION_BY_PROTOCOL))
    )


def test_every_record_object_rejects_unknown_properties() -> None:
    """Rule 3: unknown properties are rejected at every security boundary.

    `additionalProperties: false` is the rejection; a *schema* there is the map-shaped exception
    (a document whose keys are data, like `extension-manifest.operations`). Leaving it unset is the
    thing this catches: such an object silently accepts whatever the caller adds.
    """

    problems: list[str] = []
    examined = 0
    for name, document in sorted(schemas().items()):
        for path, node in _records(document):
            examined += 1
            if "additionalProperties" not in node:
                problems.append("%s%s" % (name, path))

    assert examined >= 50, f"only {examined} record objects were examined; the walk is not reaching them"
    assert problems == [], "these objects accept unknown properties:\n" + "\n".join(problems)
