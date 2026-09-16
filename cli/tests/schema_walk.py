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


def enums_by_path(schema: Any) -> dict[str, list[str]]:
    """``{path: values}`` for every ``enum`` the schema declares, at any depth.

    The path spells the route a reader follows: `properties` joins with a dot, `items` appends `[]`,
    a map-shaped object (`additionalProperties`/`patternProperties`) contributes `.*`, and the
    constraining keywords that do not change the subject (`oneOf`/`anyOf`/`allOf`/`not`/`if`/`then`/
    `else`/`propertyNames`) keep the current path. Two routes to one path merge rather than overwrite:
    a walk that silently drops a value is the defect this module exists to end.
    """

    found: dict[str, list[str]] = {}

    def record(path: str, values: list[str]) -> None:
        existing = found.setdefault(path, [])
        for value in values:
            if value not in existing:
                existing.append(value)

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, prefix)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("enum"), list):
            record(prefix, [spell(item) for item in node["enum"]])

        for keyword in KEYWORDS_JOINING_WITH_A_DOT:
            children = node.get(keyword)
            if isinstance(children, dict):
                for name, child in children.items():
                    walk(child, f"{prefix}.{name}" if prefix else name)
        for keyword in KEYWORDS_JOINING_WITH_A_WILDCARD:
            children = node.get(keyword)
            if isinstance(children, dict):
                # `additionalProperties` is one schema for every key; `patternProperties` is one per
                # pattern. Both are "the values of this object", so both get one wildcard segment.
                if keyword == "additionalProperties":
                    walk(children, f"{prefix}.*" if prefix else "*")
                else:
                    for _pattern, child in children.items():
                        walk(child, f"{prefix}.*" if prefix else "*")
        for keyword in KEYWORDS_DRAINING_A_LIST:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                walk(child, f"{prefix}[]")
        for keyword in KEYWORDS_KEEPING_THE_PATH:
            child = node.get(keyword)
            if isinstance(child, (dict, list)):
                walk(child, prefix)

    walk(schema, "")
    return found


def enum_value_sets(schema: Any) -> set[frozenset[str]]:
    """The same walk, as the value sets the field-values table is checked against.

    A set of sets rather than a path-keyed map because the table's question is "is this vocabulary
    documented somewhere", not "under which key" — a shared vocabulary like `management` is
    documented where an agent meets it, not once per schema.
    """

    return {frozenset(values) for values in enums_by_path(schema).values()}
