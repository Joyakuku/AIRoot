"""Schema introspection for the guards: the vocabularies a schema declares, and where they sit.

Two guards ask the same question of the same files — `test_l0_consistency` ("does every value of
`search-response` appear in the corpus?") and `test_l1_field_values` ("does every vocabulary of every
published schema have a documented row?") — and they answered it with **two** walkers. One of them
descended only `properties` and `items` while its docstring claimed "at any depth"; draft §104
measured what that cost: it missed **23 of the 61** enums in the published set. §105 then found that
neither walker followed `$ref`, so a vocabulary reachable only through a reference was covered
"because `common` happened to be in the inspected list" — with nothing guarding that. §106 added the
other half of the same question: a `const` is a one-valued vocabulary (`vocabularies_by_path`).

One walk, and the caller says which question it is asking:

* ``enums_by_path`` — what does this **file** declare, with or without `$ref` following;
* ``vocabularies_by_path`` — the same plus value-position ``const``s.

The paths spell the route a reader follows: `properties` joins with a dot, `items` appends `[]`, a
map-shaped object contributes `.*`, and the constraining keywords that do not change the subject
(`oneOf`/`anyOf`/`allOf`/`not`/`then`/`else`/`propertyNames`) keep the current path. The one keyword
that *does* change the subject is `if`, which is a test rather than a constraint (see
``TEST_KEYWORDS``). Two routes to one path merge rather than overwrite: a walk that silently drops a
value is the defect this module exists to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Every keyword that can carry a subschema. A walker that handles a subset of these is not wrong
#: about the subset — it is wrong about the word "every", which is what its callers rely on.
KEYWORDS_JOINING_WITH_A_DOT = ("properties", "$defs", "definitions", "dependentSchemas")
KEYWORDS_JOINING_WITH_A_WILDCARD = ("additionalProperties", "patternProperties")
KEYWORDS_DRAINING_A_LIST = ("items", "prefixItems", "contains")
KEYWORDS_KEEPING_THE_PATH = (
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "oneOf",
    "anyOf",
    "allOf",
    "unevaluatedItems",
    "unevaluatedProperties",
)

#: The one keyword whose contents are a **test** rather than a constraint: a `const` or `enum` under
#: `if` is a condition the document must satisfy for the branch to apply, not a value it carries.
#: Measured (§106): eight of the published set's forty-one consts sit there — `binding.scope =
#: "project"` does not mean "the scope is always project" but "when the scope is project,
#: `project_id` is required" — and counting them would demand rows for dispatch patterns.
TEST_KEYWORDS = ("if",)


def spell(item: Any) -> str:
    """The one spelling a value has in this repository's documents and tables.

    A JSON ``null`` is ``null``, never ``None``: §96 shipped `str(item)`, which turns a schema's
    `null` into `"None"` — a value no document can report, so demanding one would be a permanent
    false red (§97). Booleans are JSON-spelled (``true``/``false``) because a `const` can be one and
    the table writes it the way a reader meets it in the document (§106).
    """

    if item is None:
        return "null"
    if isinstance(item, bool):
        return "true" if item else "false"
    return str(item)


def _walk(schema: Any, *, resolve: Any, consts: bool) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}

    def record(path: str, values: list[str]) -> None:
        existing = found.setdefault(path, [])
        for value in values:
            if value not in existing:
                existing.append(value)

    def walk(node: Any, prefix: str, stack: frozenset[tuple[str, str]], document: Any, counts: bool) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, prefix, stack, document, counts)
            return
        if not isinstance(node, dict):
            return

        ref = node.get("$ref")
        if isinstance(ref, str) and resolve is not None:
            if (ref, prefix) in stack:
                return
            answer = resolve(ref, document)
            if answer is not None:
                target, target_document = answer
                walk(target, prefix, stack | {(ref, prefix)}, target_document, counts)
            return

        if isinstance(node.get("enum"), list):
            record(prefix, [spell(item) for item in node["enum"]])
        elif counts and "const" in node:
            # A `const` is a one-valued vocabulary, so it answers the same question (§106). `elif` on
            # purpose: a node carrying both `enum` and `const` is contradictory, and merging them
            # here would hide that from the schema catalog guard rather than from this walk.
            record(prefix, [spell(node["const"])])

        for keyword in KEYWORDS_JOINING_WITH_A_DOT:
            children = node.get(keyword)
            if isinstance(children, dict):
                for name, child in children.items():
                    walk(child, f"{prefix}.{name}" if prefix else name, stack, document, counts)
        for keyword in KEYWORDS_JOINING_WITH_A_WILDCARD:
            children = node.get(keyword)
            if isinstance(children, dict):
                # `additionalProperties` is one schema for every key; `patternProperties` is one per
                # pattern. Both are "the values of this object", so both get one wildcard segment.
                if keyword == "additionalProperties":
                    walk(children, f"{prefix}.*" if prefix else "*", stack, document, counts)
                else:
                    for _pattern, child in children.items():
                        walk(child, f"{prefix}.*" if prefix else "*", stack, document, counts)
        for keyword in KEYWORDS_DRAINING_A_LIST:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                walk(child, f"{prefix}[]", stack, document, counts)
        for keyword in KEYWORDS_KEEPING_THE_PATH:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                # `if` is a test: nothing under it is a value the document carries (§106).
                walk(child, prefix, stack, document, counts and keyword not in TEST_KEYWORDS)

    walk(schema, "", frozenset(), schema, consts)
    return found


def enums_by_path(schema: Any, *, resolve: Any = None) -> dict[str, list[str]]:
    """``{path: values}`` for every ``enum`` the schema declares, at any depth.

    ``resolve`` answers the second half of the question (§105). **Without it the walk reports what
    this file declares itself**, which is what the fixture-coverage rule needs: only a document's
    *own* vocabulary is one its producer controls, and demanding a fixture for an inherited shared
    vocabulary produces the false daggers §97 refused. **With it the walk reports everything the
    document can carry**, following `$ref` and keeping the *referencing* path
    (`where-response.health` therefore reports `common`'s five health values) — which is what the
    documentation rule needs. ``resolve(ref, document) -> (target, target_document) | None``; the
    second half of the answer is the file the target lives in, so a local `#/$defs/...` inside a
    referenced file still resolves. Returning None stops that branch, and a ref already on the
    current path is not followed again.
    """

    return _walk(schema, resolve=resolve, consts=False)


def vocabularies_by_path(schema: Any, *, resolve: Any = None) -> dict[str, list[str]]:
    """The same walk, plus every **value-position** ``const`` (§106).

    A `const` is a one-valued vocabulary, so the documentation question covers it; the exception is
    the one keyword that is a test (`TEST_KEYWORDS`), where a `const` is a dispatch condition. The
    fixture-coverage rule keeps calling `enums_by_path`: a fixture demonstrates an enum's *members*,
    while a const has exactly one value and needs no demonstration.
    """

    return _walk(schema, resolve=resolve, consts=True)


def schema_ref_resolver(schema_dir: Path) -> Any:
    """A ``resolve`` for the walks above, answering a ``$ref`` from the published set.

    Deliberately total: an unresolvable ref returns None (that branch contributes nothing) rather
    than raising, because "which references cannot be answered" is itself a thing the catalog guard
    measures — a resolver that raised here would turn that measurement into a crash.
    """

    cache: dict[str, dict] = {}

    def loaded(name: str) -> dict:
        if name not in cache:
            path = schema_dir / f"{name}.schema.json"
            cache[name] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return cache[name]

    def pointer(document: Any, target: str) -> Any:
        node = document
        for raw in [part for part in target.split("/") if part]:
            key = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node if isinstance(node, dict) else None

    def resolve(ref: str, document: Any) -> tuple[dict, dict] | None:
        file_part, _, fragment = ref.partition("#")
        if not file_part:
            target = pointer(document, fragment)
            return (target, document) if target is not None else None
        name = file_part[: -len(".schema.json")] if file_part.endswith(".schema.json") else file_part
        root = loaded(name)
        if not root:
            return None
        target = pointer(root, fragment)
        return (target, root) if target is not None else None

    return resolve
