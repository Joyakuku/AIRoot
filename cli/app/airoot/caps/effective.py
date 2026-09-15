"""Effective state: what the current process and a new process can actually find.

``effective`` is a fact of its own (v0.3 §9.1): a path can exist, be declared and be
healthy while the current shell still cannot see it. P1 reads machine and user PATH
**read-only** — it never writes PATH, ACL or the registry.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..exits import AirootError

_MACHINE_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USER_KEY = "Environment"


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    entries: list[str] = []
    for raw in value.split(";"):
        entry = raw.strip().strip('"')
        if entry:
            entries.append(os.path.normcase(os.path.normpath(os.path.expandvars(entry))))
    return entries


def process_path(env: dict[str, str] | None = None) -> list[str]:
    source = env if env is not None else os.environ
    return _split(source.get("PATH") or source.get("Path"))


def _registry_path(hive: int, subkey: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(hive, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, "Path")
        return str(value)
    except OSError:
        return None


def machine_path() -> list[str]:
    import winreg

    return _split(_registry_path(winreg.HKEY_LOCAL_MACHINE, _MACHINE_KEY))


def user_path() -> list[str]:
    import winreg

    return _split(_registry_path(winreg.HKEY_CURRENT_USER, _USER_KEY))


def is_visible(executable: Path | str | None, entries: list[str]) -> bool:
    """Is the executable discoverable through one of these PATH entries?"""

    if not executable:
        return False
    target = os.path.normcase(os.path.normpath(str(executable)))
    parent = os.path.dirname(target)
    normalized = {os.path.normcase(os.path.normpath(str(entry))) for entry in entries}
    return parent in normalized


def effective_state(
    executable: Path | str | None,
    *,
    process_entries: list[str] | None = None,
    machine_entries: list[str] | None = None,
    user_entries: list[str] | None = None,
) -> tuple[bool, bool]:
    """``(effective_now, effective_new_process)`` for one resolved executable."""

    now_entries = process_entries if process_entries is not None else process_path()
    new_entries = (machine_entries if machine_entries is not None else machine_path()) + (
        user_entries if user_entries is not None else user_path()
    )
    return is_visible(executable, now_entries), is_visible(executable, new_entries)


__all__ = [
    "AirootError",
    "effective_state",
    "is_visible",
    "machine_path",
    "process_path",
    "user_path",
]
