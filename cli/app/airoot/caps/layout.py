"""Where a controlled payload's marker may live, and which markers are declared (draft §71).

三大核心契约 §5.3 states the rule in one sentence: ``store`` is the only payload storage, and
``tools``/``env`` are binding/view directories that carry no payload. Three readers act on that
sentence — `doctor` (D4 diagnostics), `rebuild` (the report handed to an operator after a recovery)
and `where` (which refuses to select a mislocated payload) — and §71 found the *scan* spelled twice:
``doctor._check_orphans`` and ``rebuild._store_orphans`` walked the same tree with the same predicate,
and only one of the two readers knew the view directories existed at all. It lives here once.

The predicate is a directory containing ``artifact.json`` — the marker this project gives a controlled
instance, and the one ``_check_orphans`` has always used. A directory the user copied in by hand
carries no marker and is therefore **not** reported: reporting "anything that looks like a runtime"
would manufacture false alarms inside a data root (draft §66.6 pt 2 keeps that boundary).

Depth matters and used to be inconsistent. The old store scan took each immediate child of ``store/``
and then searched *its* descendants, so a marked directory at depth 1 (``store/handmade/``) was
invisible to both readers — measured, not inferred (§71.2). This scan covers every depth under every
place it is asked about, which reports that shape as the orphan it is.

``is_store_path`` (``registry.entities``) remains the one spelling of "inside the store"; this module
only decides *where to look* and *who declared what*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import ENV_DIR, STORE_DIR, TOOLS_DIR
from ..registry.entities import is_store_path

#: The marker a controlled instance's payload directory carries. Spelled once, like `STORE_PREFIX`.
PAYLOAD_MARKER = "artifact.json"

#: Directories that hold a binding/view (and therefore no payload).
PAYLOAD_VIEWS: tuple[str, ...] = (TOOLS_DIR, ENV_DIR)

#: Every place the scan looks: the store first, then the views.
PAYLOAD_PLACES: tuple[str, ...] = (STORE_DIR, *PAYLOAD_VIEWS)


def declared_payload_paths(registry: Any) -> set[str]:
    """Every declared ``store_path``, normalised to forward slashes."""

    return {str(row["store_path"]).replace("\\", "/") for row in registry.instances()}


def marked_directories(
    root: Path, places: Iterable[str] = PAYLOAD_PLACES
) -> list[tuple[str, str]]:
    """``(relative path, place)`` for every marked directory, at any depth, sorted."""

    root = Path(root)
    found: list[tuple[str, str]] = []
    for place in places:
        base = root / place
        if not base.is_dir():
            continue
        for leaf in base.rglob("*"):
            if leaf.is_dir() and (leaf / PAYLOAD_MARKER).is_file():
                found.append((leaf.relative_to(root).as_posix(), place))
    return sorted(found)


def store_orphans(registry: Any, root: Path) -> list[str]:
    """Marked directories under ``store/`` that no instance declares (D4's orphan half)."""

    declared = declared_payload_paths(registry)
    return [relative for relative, _ in marked_directories(root, (STORE_DIR,)) if relative not in declared]


def mislocated_payloads(registry: Any, root: Path) -> list[str]:
    """Marked directories under a view directory that no instance declares (D4's drift half)."""

    declared = declared_payload_paths(registry)
    return [relative for relative, _ in marked_directories(root, PAYLOAD_VIEWS) if relative not in declared]


def misdeclared_payloads(registry: Any) -> list[tuple[str, str]]:
    """``(instance_id, store_path)`` for every declaration whose payload is not under ``store/``."""

    return [
        (str(row["instance_id"]), str(row["store_path"]))
        for row in registry.instances()
        if not is_store_path(row["store_path"])
    ]


__all__ = [
    "PAYLOAD_MARKER",
    "PAYLOAD_PLACES",
    "PAYLOAD_VIEWS",
    "declared_payload_paths",
    "marked_directories",
    "misdeclared_payloads",
    "mislocated_payloads",
    "store_orphans",
]
