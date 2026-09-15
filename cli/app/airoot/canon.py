"""Canonical JSON, digests and plan hashing.

``plan_hash`` is documented as ``jcs-rfc8785-compatible``. P1 uses deterministic
sorted JSON as the test substitute, exactly as the fake vertical slice does and
as ``docs/schema/README.md`` records. Production must implement RFC 8785; the
label is carried in the plan itself so the substitute is never mistaken for it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

CANONICALIZATION_LABEL = "jcs-rfc8785-compatible"

_PLAN_HASH_FIELD = "plan_hash"


def canonical_json(value: Any) -> str:
    """Deterministic JSON text: sorted keys, no insignificant whitespace, ASCII."""

    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def digest_file(path: Path) -> str:
    return digest_bytes(Path(path).read_bytes())


def tree_digest(root: Path) -> str:
    """Digest of a directory tree: one record per file, path/size/digest."""

    root = Path(root)
    records = [
        {"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "digest": digest_file(path)}
        for path in sorted(p for p in root.rglob("*") if p.is_file())
    ]
    return digest_bytes(canonical_bytes(records))


def file_manifest(root: Path) -> list[dict[str, Any]]:
    """Manifest entries shaped for ``common.schema.json#/$defs/fileManifestEntry``."""

    root = Path(root)
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "digest": digest_file(path),
            "mode": "file",
        }
        for path in sorted(p for p in root.rglob("*") if p.is_file())
    ]


def file_manifest_digest(entries: Iterable[dict[str, Any]]) -> str:
    return digest_bytes(canonical_bytes(list(entries)))


def plan_hash(plan: dict[str, Any]) -> str:
    """Digest of the canonical plan with ``plan_hash`` itself omitted."""

    unsigned = {key: value for key, value in plan.items() if key != _PLAN_HASH_FIELD}
    return digest_bytes(canonical_bytes(unsigned))


def signable_payload(document: dict[str, Any], *, omit: Iterable[str] = ()) -> bytes:
    """Canonical bytes for signature computation, omitting the listed fields."""

    omitted = set(omit)
    unsigned = {key: value for key, value in document.items() if key not in omitted}
    return canonical_bytes(unsigned)
