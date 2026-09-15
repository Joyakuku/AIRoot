"""Environment exposure for steward-domain references (draft §13).

Three layers are kept strictly separate, because conflating them is how "set an
environment variable" becomes an unaccountable machine-wide change:

``session``
    Script output or a child process. Never touches the registry, never modifies the
    parent shell (which is physically impossible — v0.3 §16.4:1760).

``user`` persistent
    ``HKCU\\Environment``. Needs an approval token, is recorded, and is exactly
    reversible.

``machine`` persistent
    ``HKLM``. Requires the P2 broker; until then it reports ``PRIVILEGE_REQUIRED``.

The one rule that matters most (draft §13.2): a persisted value must point at the
**object inside a data root**. Persisting a pointer to ``exposure\\bin`` would leave a
dangling path behind the moment AIROOT is uninstalled — the exact opposite of the
steward model's promise.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..canon import plan_hash, canonical_bytes
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..paths import is_within

# Draft §13.5. These are "load arbitrary code" or "add an execution trigger" variables:
# setting them machine-wide or user-wide is a global code-injection surface. The list is
# duplicated in reference-plan.schema.json; a test asserts the two agree.
FORBIDDEN_PERSISTENT_VARIABLES: tuple[str, ...] = (
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "NODE_OPTIONS", "NODE_PATH",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "GIT_SSH_COMMAND", "GIT_EXTERNAL_DIFF", "GIT_CONFIG_GLOBAL",
    "PATHEXT", "COMSPEC", "BASH_ENV", "ENV", "ZDOTDIR", "PROMPT_COMMAND",
)

VALUE_KINDS = ("REG_SZ", "REG_EXPAND_SZ")
PATH_VARIABLES = ("PATH", "Path")

# Templates a whitelist may use. All three are *observed* facts, never guesses:
#   <object_root>          the directory the capability was discovered under
#   <entrypoint_dir>       the directory the primary entrypoint was actually found in
#   <entrypoint_dir_parent> its parent — the runtime's home for a `.../jdk-x/bin/java.exe`
#                          layout, and equal to <object_root> for a flat `.../python.exe` one
#   <active_version_dir>   the observed active version directory, when a marker proved one
TEMPLATES = (
    "<object_root>",
    "<entrypoint_dir>",
    "<entrypoint_dir_parent>",
    "<active_version_dir>",
)

_EXPANDABLE = re.compile(r"%(?:[^%]+)%")

# `<object_root>`, `<entrypoint_dir>` … — anything shaped like a template token.
_TOKEN = re.compile(r"<[^<>]*>")


# --------------------------------------------------------------------------- #
# declared environment (from the capability whitelist)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EnvironmentSpec:
    """What a capability declares about its environment. Data, not code."""

    capability_id: str
    variables: tuple[tuple[str, str], ...] = ()
    path_prepend: tuple[str, ...] = ()
    value_kind: str = "REG_EXPAND_SZ"

    def variable_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.variables)


def spec_from_entry(capability_id: str, entry: dict[str, Any] | None) -> EnvironmentSpec:
    """Build a spec from a whitelist entry's ``environment`` block, validating it."""

    if not entry:
        return EnvironmentSpec(capability_id=capability_id)
    variables = tuple((str(name), str(value)) for name, value in (entry.get("variables") or {}).items())
    path_prepend = tuple(str(item) for item in (entry.get("path_prepend") or []))
    kind = str(entry.get("value_kind", "REG_EXPAND_SZ"))
    spec = EnvironmentSpec(
        capability_id=capability_id, variables=variables, path_prepend=path_prepend, value_kind=kind
    )
    validate_spec(spec)
    return spec


def validate_spec(spec: EnvironmentSpec) -> None:
    """A whitelist may not request a forbidden variable, and paths must be relative."""

    if spec.value_kind not in VALUE_KINDS:
        raise AirootError(
            "INVALID_INPUT",
            f"whitelist environment for {spec.capability_id} declares an unknown value_kind: {spec.value_kind}",
            evidence=list(VALUE_KINDS),
        )
    for name, template in spec.variables:
        if name.upper() in FORBIDDEN_PERSISTENT_VARIABLES:
            raise AirootError(
                "PERSISTENCE_TARGET_FORBIDDEN",
                f"whitelist environment for {spec.capability_id} requests the forbidden variable {name}",
                evidence=["injection-type variables are never persisted (draft §13.5)"],
            )
        if not template:
            raise AirootError(
                "INVALID_INPUT",
                f"whitelist environment for {spec.capability_id} has an empty value for {name}",
            )
        # An unknown token would stay in the value literally and silently point nowhere.
        for token in _TOKEN.findall(template):
            if token not in TEMPLATES:
                raise AirootError(
                    "INVALID_INPUT",
                    f"whitelist environment for {spec.capability_id} uses an unknown template {token}",
                    evidence=[f"known templates: {', '.join(TEMPLATES)}"],
                )
    for relative in spec.path_prepend:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AirootError(
                "INVALID_INPUT",
                f"whitelist environment for {spec.capability_id} has a non-relative path_prepend: {relative}",
            )


def resolve_variables(
    spec: EnvironmentSpec,
    *,
    object_root: Path,
    entrypoint_dir: Path,
    active_version_dir: Path | None = None,
) -> dict[str, str]:
    """Expand the declared templates into concrete values."""

    replacements = {
        "<object_root>": str(object_root),
        "<entrypoint_dir>": str(entrypoint_dir),
        "<entrypoint_dir_parent>": str(Path(entrypoint_dir).parent),
        "<active_version_dir>": str(active_version_dir or object_root),
    }
    resolved: dict[str, str] = {}
    for name, template in spec.variables:
        value = template
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        resolved[name] = value
    return resolved


def resolve_path_entries(
    spec: EnvironmentSpec,
    *,
    object_root: Path,
    entrypoint_dir: Path,
) -> list[Path]:
    """Directories to put on PATH for this capability.

    Defaults to the entrypoint's own directory — the one piece of evidence we always
    have — plus any declared relative subdirectories.
    """

    entries = [Path(object_root) / relative for relative in spec.path_prepend]
    if not any(Path(object_root) / relative == entrypoint_dir for relative in spec.path_prepend):
        if entrypoint_dir not in entries:
            entries.insert(0, entrypoint_dir)
    return entries


# --------------------------------------------------------------------------- #
# value validation
# --------------------------------------------------------------------------- #


def validate_variable_name(name: str) -> None:
    if not name or "=" in name or "\x00" in name:
        raise AirootError("PERSISTENCE_TARGET_FORBIDDEN", f"invalid variable name: {name!r}")
    if name.upper() in FORBIDDEN_PERSISTENT_VARIABLES:
        raise AirootError(
            "PERSISTENCE_TARGET_FORBIDDEN",
            f"{name} is an injection-type variable and is never persisted",
            evidence=["draft §13.5: these variables name code to load or triggers to run"],
        )


def validate_value(value: str, kind: str) -> None:
    if not value or len(value) > 2048:
        raise AirootError("PERSISTENCE_TARGET_FORBIDDEN", f"value length is invalid ({len(value)})")
    if "\r" in value or "\n" in value:
        raise AirootError(
            "PERSISTENCE_TARGET_FORBIDDEN",
            "value contains a newline",
            evidence=["a newline can inject a second statement into a shell profile"],
        )
    if value.count('"') % 2 != 0:
        raise AirootError("PERSISTENCE_TARGET_FORBIDDEN", "value contains an unpaired quote")
    has_percent = "%" in value
    if has_percent and kind == "REG_SZ":
        raise AirootError(
            "PERSISTENCE_TARGET_FORBIDDEN",
            "value contains %...% but the kind is REG_SZ, which would store it literally",
            evidence=["declare REG_EXPAND_SZ when the value is meant to expand"],
        )
    if has_percent:
        without_pairs = _EXPANDABLE.sub("", value)
        if "%" in without_pairs:
            raise AirootError(
                "PERSISTENCE_TARGET_FORBIDDEN",
                "value contains an ambiguous % expansion",
                evidence=[f"value={value!r}"],
            )


def validate_target_in_data_root(target: Path, data_roots: list[Path]) -> None:
    """The persisted value must point inside a registered data root."""

    resolved = Path(target)
    for root in data_roots:
        if is_within(resolved, root):
            return
    raise AirootError(
        "PERSISTENCE_TARGET_FORBIDDEN",
        f"{resolved} is not inside any registered data root",
        evidence=[
            "a persisted pointer outside a data root would dangle once AIROOT is removed",
            f"registered roots: {[str(root) for root in data_roots]}",
        ],
    )


# --------------------------------------------------------------------------- #
# the persistent store
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StoredValue:
    name: str
    value: str
    kind: str


class EnvironmentStore(Protocol):
    """Read/write one environment scope. Kept abstract so tests never touch HKCU."""

    def read(self, name: str) -> StoredValue | None: ...

    def write(self, name: str, value: str, kind: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def names(self) -> list[str]: ...


@dataclass
class InMemoryEnvironmentStore:
    """A store that models Windows' case-insensitive variable names."""

    values: dict[str, StoredValue] = field(default_factory=dict)
    writes: list[tuple[str, str, str]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)

    def _key(self, name: str) -> str:
        return name.upper()

    def read(self, name: str) -> StoredValue | None:
        return self.values.get(self._key(name))

    def write(self, name: str, value: str, kind: str) -> None:
        self.values[self._key(name)] = StoredValue(name=name, value=value, kind=kind)
        self.writes.append((name, value, kind))

    def delete(self, name: str) -> None:
        self.values.pop(self._key(name), None)
        self.deletes.append(name)

    def names(self) -> list[str]:
        return sorted(stored.name for stored in self.values.values())


class WindowsEnvironmentStore:
    """``HKCU\\Environment`` (or an injected key, so tests stay off the real one)."""

    def __init__(self, *, hive: int | None = None, subkey: str = "Environment") -> None:
        if sys.platform != "win32":  # pragma: no cover - Windows-only product
            raise AirootError("INVALID_INPUT", "the Windows environment store needs Windows")
        import winreg

        self._winreg = winreg
        self.hive = winreg.HKEY_CURRENT_USER if hive is None else hive
        self.subkey = subkey

    def _kind(self, stored_kind: int) -> str:
        return "REG_EXPAND_SZ" if stored_kind == self._winreg.REG_EXPAND_SZ else "REG_SZ"

    def read(self, name: str) -> StoredValue | None:
        try:
            with self._winreg.OpenKey(self.hive, self.subkey, 0, self._winreg.KEY_READ) as key:
                value, kind = self._winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return StoredValue(name=name, value=str(value), kind=self._kind(kind))

    def write(self, name: str, value: str, kind: str) -> None:
        with self._winreg.CreateKeyEx(self.hive, self.subkey, 0, self._winreg.KEY_SET_VALUE) as key:
            self._winreg.SetValueEx(
                key, name, 0, self._winreg.REG_SZ if kind == "REG_SZ" else self._winreg.REG_EXPAND_SZ, value
            )
        broadcast_environment_change()

    def delete(self, name: str) -> None:
        try:
            with self._winreg.OpenKey(self.hive, self.subkey, 0, self._winreg.KEY_SET_VALUE) as key:
                self._winreg.DeleteValue(key, name)
        except OSError:
            return
        broadcast_environment_change()

    def names(self) -> list[str]:
        collected: list[str] = []
        try:
            with self._winreg.OpenKey(self.hive, self.subkey, 0, self._winreg.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        collected.append(self._winreg.EnumValue(key, index)[0])
                        index += 1
                    except OSError:
                        break
        except OSError:
            return []
        return sorted(collected)


def broadcast_environment_change() -> None:
    """Tell running processes the environment block changed (v0.3 §8.3)."""

    if sys.platform != "win32":  # pragma: no cover
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            ctypes.c_wchar_p("Environment"),
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception:  # pragma: no cover - a broadcast failure must not corrupt state
        return


# --------------------------------------------------------------------------- #
# merge semantics for PATH-like values
# --------------------------------------------------------------------------- #


def merge_path_value(old_value: str | None, entries: list[Path]) -> str:
    """Prepend entries that are not already present, case-insensitively."""

    existing = [part for part in (old_value or "").split(";") if part]
    seen = {os.path.normcase(os.path.normpath(part)) for part in existing}
    additions: list[str] = []
    for entry in entries:
        key = os.path.normcase(os.path.normpath(str(entry)))
        if key not in seen:
            seen.add(key)
            additions.append(str(entry))
    return ";".join(additions + existing)


def remove_path_value(value: str, entries: list[Path]) -> str:
    want = {os.path.normcase(os.path.normpath(str(entry))) for entry in entries}
    keep = [
        part
        for part in value.split(";")
        if part and os.path.normcase(os.path.normpath(part)) not in want
    ]
    return ";".join(keep)


__all__ = [
    "EnvironmentSpec",
    "EnvironmentStore",
    "FORBIDDEN_PERSISTENT_VARIABLES",
    "InMemoryEnvironmentStore",
    "PATH_VARIABLES",
    "StoredValue",
    "TEMPLATES",
    "VALUE_KINDS",
    "WindowsEnvironmentStore",
    "broadcast_environment_change",
    "canonical_bytes",
    "merge_path_value",
    "plan_hash",
    "remove_path_value",
    "resolve_path_entries",
    "resolve_variables",
    "spec_from_entry",
    "validate_spec",
    "validate_target_in_data_root",
    "validate_value",
    "validate_variable_name",
]
