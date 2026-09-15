"""Session activation with a real snapshot stack (规划 §16.4).

A child process cannot change its parent shell's environment — that is a physical fact, so
`env activate` prints a script. But §16.4:1783 asks for more than a script: the JSON form must
carry the **environment diff**, the **source generation**, a **pre-activation snapshot id** and
the **deactivate information**, and activation must support a **nesting stack** and report
``SESSION_STATE_STALE`` when the world moved underneath it.

This module owns that stack. Three boundaries are deliberate:

* the snapshot lives under ``state/sessions/`` in the CLI root — **it is not a credential**, not
  part of the registry, and it never changes declared state or the generation;
* the session id is **injected** (``--session``), never invented: the generation algorithm for
  ``session_id`` is an explicitly open item, and a made-up id would make ``deactivate`` unable to
  find its own stack;
* deactivation **restores** rather than deletes: a variable that existed before goes back to its
  old value, one that did not exist is removed, and PATH loses only the entries this session
  added.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError

SESSIONS_DIR = "state/sessions"
SESSION_SCHEMA_VERSION = 1


@dataclass
class VariableChange:
    name: str
    new: str
    old: str | None = None
    kind: str = "REG_EXPAND_SZ"

    @property
    def existed(self) -> bool:
        return self.old is not None

    def to_document(self) -> dict[str, Any]:
        return {"old": self.old, "new": self.new, "existed": self.existed, "kind": self.kind}


@dataclass
class Frame:
    """One activation: what it set, and what it replaced."""

    external_id: str
    capability_id: str
    variables: dict[str, VariableChange] = field(default_factory=dict)
    path_variable: str = "Path"
    path_entries: list[str] = field(default_factory=list)
    path_old: str | None = None
    generation: int = 0
    snapshot_id: str = ""
    activated_at: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "capability_id": self.capability_id,
            "variables": {name: change.to_document() for name, change in self.variables.items()},
            "path_variable": self.path_variable,
            "path_entries": list(self.path_entries),
            "path_old": self.path_old,
            "generation": self.generation,
            "snapshot_id": self.snapshot_id,
            "activated_at": self.activated_at,
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "Frame":
        return cls(
            external_id=str(document["external_id"]),
            capability_id=str(document.get("capability_id", "")),
            variables={
                str(name): VariableChange(
                    name=str(name),
                    new=str(item.get("new", "")),
                    old=item.get("old"),
                    kind=str(item.get("kind", "REG_EXPAND_SZ")),
                )
                for name, item in (document.get("variables") or {}).items()
            },
            path_variable=str(document.get("path_variable", "Path")),
            path_entries=[str(item) for item in document.get("path_entries", [])],
            path_old=document.get("path_old"),
            generation=int(document.get("generation", 0)),
            snapshot_id=str(document.get("snapshot_id", "")),
            activated_at=str(document.get("activated_at", "")),
        )


def session_path(root: Path, session_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session_id)
    if not safe:
        raise AirootError("INVALID_INPUT", "a session id may not be empty or entirely unsafe characters")
    return Path(root) / SESSIONS_DIR / f"{safe}.json"


def read_stack(root: Path, session_id: str) -> list[Frame]:
    """The activation stack, oldest first. A missing file is an empty stack, not an error."""

    path = session_path(root, session_id)
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(
            "SESSION_STATE_STALE",
            f"the session state for {session_id} is unreadable",
            evidence=[str(path), str(exc), "run 'env deactivate --all' and activate again"],
        ) from exc
    if int(document.get("schema_version", 0)) != SESSION_SCHEMA_VERSION:
        raise AirootError(
            "SESSION_STATE_STALE",
            f"the session state for {session_id} was written by another version",
            evidence=[
                f"file schema_version={document.get('schema_version')}",
                f"this build writes {SESSION_SCHEMA_VERSION}",
            ],
        )
    return [Frame.from_document(item) for item in document.get("frames", [])]


def write_stack(root: Path, session_id: str, frames: list[Frame]) -> Path:
    path = session_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        path.unlink(missing_ok=True)
        return path
    document = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "note": "session activation state; not a credential, not part of the registry",
        "frames": [frame.to_document() for frame in frames],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# activate
# --------------------------------------------------------------------------- #


def new_snapshot_id() -> str:
    return "snap/" + uuid.uuid4().hex[:16]


def push_frame(
    root: Path,
    *,
    session_id: str,
    external_id: str,
    capability_id: str,
    variables: dict[str, str],
    path_variable: str,
    path_entries: list[str],
    read_current: Any,
    generation: int,
    clock: Clock = SYSTEM_CLOCK,
) -> tuple[Frame, list[Frame]]:
    """Record one activation. ``read_current(name)`` returns the pre-activation value or None."""

    frames = read_stack(root, session_id)
    for frame in frames:
        if frame.external_id == external_id:
            # Re-activating the same reference in the same session is a no-op, not a second layer:
            # a stack that grows on repeat would make `deactivate` need as many pops as keystrokes.
            return frame, frames

    change_map: dict[str, VariableChange] = {}
    for name, value in sorted(variables.items()):
        current = read_current(name)
        change_map[name] = VariableChange(
            name=name,
            new=value,
            old=getattr(current, "value", None) if current is not None else None,
            kind=getattr(current, "kind", "REG_EXPAND_SZ") if current is not None else "REG_EXPAND_SZ",
        )
    previous_path = read_current(path_variable) if path_entries else None
    frame = Frame(
        external_id=external_id,
        capability_id=capability_id,
        variables=change_map,
        path_variable=path_variable,
        path_entries=list(path_entries),
        path_old=getattr(previous_path, "value", None) if previous_path is not None else None,
        generation=int(generation),
        snapshot_id=new_snapshot_id(),
        activated_at=clock.timestamp(),
    )
    frames.append(frame)
    write_stack(root, session_id, frames)
    return frame, frames


def require_fresh(root: Path, session_id: str, *, current_generation: int) -> list[Frame]:
    """Refuse to deactivate against a stack from another generation."""

    frames = read_stack(root, session_id)
    stale = [frame for frame in frames if frame.generation != current_generation]
    if stale:
        raise AirootError(
            "SESSION_STATE_STALE",
            "the declared state changed after this session was activated",
            evidence=[
                *[
                    f"{frame.external_id}: activated at generation {frame.generation}, now {current_generation}"
                    for frame in stale[:4]
                ],
                "the resolved paths may no longer exist; activate again in a fresh session",
            ],
        )
    return frames


# --------------------------------------------------------------------------- #
# deactivate
# --------------------------------------------------------------------------- #


def pop_frames(
    root: Path, *, session_id: str, all_frames: bool = False
) -> tuple[list[Frame], list[Frame]]:
    """Pop the top frame (or the whole stack). Returns ``(popped, remaining)``."""

    frames = read_stack(root, session_id)
    if not frames:
        return [], []
    popped = list(frames) if all_frames else [frames[-1]]
    remaining = [] if all_frames else frames[:-1]
    write_stack(root, session_id, remaining)
    return popped, remaining


def restore_script(frames: list[Frame], *, shell: str) -> str:
    """The script that undoes these frames, newest first."""

    lines: list[str] = []
    for frame in reversed(frames):
        for name in sorted(frame.variables):
            change = frame.variables[name]
            if shell == "powershell":
                lines.append(
                    f"$env:{name} = '{change.old}'" if change.existed else f"Remove-Item Env:{name} -ErrorAction SilentlyContinue"
                )
            else:
                lines.append(f'set "{name}={change.old}"' if change.existed else f'set "{name}="')
        if frame.path_entries:
            from .environment import remove_path_value

            if shell == "powershell":
                if frame.path_old is None:
                    lines.append("$env:Path = ($env:Path -split ';' | Where-Object { $_ -notin @(" +
                                 ",".join(f"'{item}'" for item in frame.path_entries) + ") }) -join ';'")
                else:
                    lines.append(f"$env:Path = '{frame.path_old}'")
            else:
                lines.append(f'set "Path={frame.path_old or ""}"')
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "Frame",
    "SESSIONS_DIR",
    "SESSION_SCHEMA_VERSION",
    "VariableChange",
    "new_snapshot_id",
    "pop_frames",
    "push_frame",
    "read_stack",
    "require_fresh",
    "restore_script",
    "session_path",
    "write_stack",
]
