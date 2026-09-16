"""Schema introspection for the guards: every ``enum`` a schema declares, and where it sits.

Two guards ask the same question of the same files — `test_l0_consistency` ("does every value of
`search-response` appear in the corpus?") and `test_l1_field_values` ("does every value of every
in-scope schema have a documented row?") — and they answered it with **two** walkers. One of them
descended only `properties` and `items` while its docstring claimed "at any depth"; draft §104
measured what that cost: it missed **23 of the 61** enums in the published set — all 18 of
`common`'s (they live under `$defs`), plus enums sitting under `additionalProperties`,
`propertyNames`/`not` and `anyOf`.

`enum_value_sets` was already complete (it walks every dict and list), so the two disagreed about
what "every enum" means without either being wrong in the case it was actually applied to. This
module is the single definition, so the next schema that puts an enum somewhere unusual cannot be
visible to one guard and invisible to the other.
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


def spell(item: Any) -> str:
    """A JSON ``null`` is spelled ``null``, never ``None``.

    §96 shipped `str(item)`, which turns a schema's `null` into `"None"` — a value no document can
    report, so demanding one would be a permanent false red (§97).
    """

    return "null" if item is None else str(item)


def enums_by_path(schema: Any, *, resolve: Any = None) -> dict[str, list[str]]:
    """``{path: values}`` for every ``enum`` the schema declares, at any depth.

    The path spells the route a reader follows: `properties` joins with a dot, `items` appends `[]`,
    a map-shaped object (`additionalProperties`/`patternProperties`) contributes `.*`, and the
    constraining keywords that do not change the subject (`oneOf`/`anyOf`/`allOf`/`not`/`if`/`then`/
    `else`/`propertyNames`) keep the current path. Two routes to one path merge rather than overwrite:
    a walk that silently drops a value is the defect this module exists to end.

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

    found: dict[str, list[str]] = {}

    def record(path: str, values: list[str]) -> None:
        existing = found.setdefault(path, [])
        for value in values:
            if value not in existing:
                existing.append(value)

    def walk(node: Any, prefix: str, stack: frozenset[tuple[str, str]], document: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, prefix, stack, document)
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
                walk(target, prefix, stack | {(ref, prefix)}, target_document)
            return

        if isinstance(node.get("enum"), list):
            record(prefix, [spell(item) for item in node["enum"]])

        for keyword in KEYWORDS_JOINING_WITH_A_DOT:
            children = node.get(keyword)
            if isinstance(children, dict):
                for name, child in children.items():
                    walk(child, f"{prefix}.{name}" if prefix else name, stack, document)
        for keyword in KEYWORDS_JOINING_WITH_A_WILDCARD:
            children = node.get(keyword)
            if isinstance(children, dict):
                # `additionalProperties` is one schema for every key; `patternProperties` is one per
                # pattern. Both are "the values of this object", so both get one wildcard segment.
                if keyword == "additionalProperties":
                    walk(children, f"{prefix}.*" if prefix else "*", stack, document)
                else:
                    for _pattern, child in children.items():
                        walk(child, f"{prefix}.*" if prefix else "*", stack, document)
        for keyword in KEYWORDS_DRAINING_A_LIST:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                walk(child, f"{prefix}[]", stack, document)
        for keyword in KEYWORDS_KEEPING_THE_PATH:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                walk(child, prefix, stack, document)

    walk(schema, "", frozenset(), schema)
    return found


def schema_ref_resolver(schema_dir: Path) -> Any:
    """A ``resolve`` for :func:`enums_by_path` that answers a ``$ref`` from the published set.

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
