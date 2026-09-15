"""Runtime JSON Schema validation against ``AIROOT\\cli\\schema``.

The published schemas are the machine-executable contract (v0.3 §1.1 layer 1), so
the core validates both its inputs and **its own outputs** before printing them.
Relative ``$ref`` values such as ``common.schema.json#/$defs/digest`` resolve
against the referencing schema's ``$id``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from . import SCHEMA_DIR
from .exits import AirootError

try:  # jsonschema >= 4.18 ships `referencing`; fall back for older installs.
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    _HAS_REFERENCING = True
except ImportError:  # pragma: no cover - depends on installed jsonschema
    _HAS_REFERENCING = False


_SCHEMA_ID_BASE = "https://airoot.local/schema"


def schema_path(name: str) -> Path:
    if not name.endswith(".schema.json"):
        name = f"{name}.schema.json"
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise AirootError("SCHEMA_UNSUPPORTED", f"unknown schema: {name}", evidence=[str(path)])
    return path


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def schema_names() -> list[str]:
    return sorted(path.name for path in SCHEMA_DIR.glob("*.schema.json"))


@lru_cache(maxsize=1)
def _registry() -> Any:
    if not _HAS_REFERENCING:  # pragma: no cover - exercised only on old jsonschema
        return None
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        resources.append((document["$id"], resource))
        # The schema set references siblings by *filename* (`common.schema.json#/...`),
        # which resolves against the referencing schema's `$id` directory to
        # `https://airoot.local/schema/common.schema.json` — a different URI from the
        # file's own `$id` (`.../common-v1.json`). Register the filename URI too.
        resources.append((f"{_SCHEMA_ID_BASE}/{path.name}", resource))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _validator(name: str) -> Any:
    schema = load_schema(name)
    if _HAS_REFERENCING:
        return jsonschema.Draft202012Validator(schema, registry=_registry())
    common = load_schema("common.schema.json")  # pragma: no cover - old jsonschema
    store = {common["$id"]: common, "https://airoot.local/schema/common.schema.json": common}
    resolver = jsonschema.RefResolver.from_schema(schema, store=store)
    return jsonschema.Draft202012Validator(schema, resolver=resolver)


def errors_for(name: str, document: Any) -> list[str]:
    """Every validation error, as ``<json path>: <message>`` strings."""

    validator = _validator(name)
    collected = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    return [f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}" for error in collected]


def validate_document(name: str, document: Any, *, reason_code: str = "INVALID_INPUT") -> None:
    problems = errors_for(name, document)
    if problems:
        raise AirootError(reason_code, f"{name} rejected the document", evidence=problems[:16])


def validate_self(name: str, document: Any) -> None:
    """Validate a document the core is about to emit; failure is an implementation bug."""

    problems = errors_for(name, document)
    if problems:
        raise AirootError(
            "SELF_VALIDATION_FAILED",
            f"{name} rejected a document produced by this build",
            evidence=problems[:16],
        )


def check_schemas_are_meta_valid(names: Iterable[str] | None = None) -> int:
    """Meta-schema check used by tests and the fake slice keep-alive."""

    checked = 0
    for name in sorted(names or schema_names()):
        jsonschema.Draft202012Validator.check_schema(load_schema(name))
        checked += 1
    return checked
