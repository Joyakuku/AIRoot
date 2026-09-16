"""The stable entry: one static ``.cmd`` per capability (ADR-0050).

A capability that AIROOT installed should be callable without anyone editing the machine PATH and
without knowing which version is current. That is what a *stable entry* is, and ADR-0050 decided its
shape after overturning ADR-0025's D4 ("P1 writes no launcher"):

* **the registry stays the only authority.** The file contains no version and no instance id — it
  asks the CLI to resolve the active binding *at call time*. So installing a newer version rewrites
  the same file with **byte-identical content**, which is a promise this module's tests measure
  rather than a hope;
* **it is text, not a binary.** A ``.cmd`` that any same-user process can rewrite is honest about
  what it is worth; a compiled shim in the directory that is about to be on PATH would look like
  protection that does not exist (ADR-0044 measured that no such protection exists on this machine);
* **it is written inside the transaction** (the ``EXPOSED`` step of both runners), so "the launcher
  could not be written" means ``EXPOSED`` never happened and the existing rollback path runs. The
  write is idempotent, so replaying that step after a crash changes nothing.

**What it deliberately does not do**: no PATH write, no elevation, no environment injection, no
JSON parsing, no directory guessing. It forwards arguments and the child's exit code.

The bytes are CRLF on purpose — ``cmd.exe`` splits ``rem`` lines on LF and runs the pieces as
commands (AGENTS.md, "运行环境注意"). That is why this module deals in ``bytes`` and not ``str``.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ..exits import AirootError

#: Where the frozen contract puts the one AIROOT directory that may ever sit on the machine PATH
#: (AGENTS.md §5 item 8). The **directory** is the contract; this module only puts files in it.
LAUNCHER_DIRECTORY_RELATIVE = ("cli", "exposure", "bin")

LAUNCHER_SUFFIX = ".cmd"

#: A capability id is also a file name here, so it has to be one. Capability ids come from the frozen
#: list (`policy/capabilities.json`), which this module deliberately does not read: membership is
#: enforced upstream by `plan`/`boundary`, and duplicating that check here would make two places
#: responsible for one rule. What is checked here is only what a **file name** must satisfy.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def launcher_directory(root: Path) -> Path:
    return Path(root).joinpath(*LAUNCHER_DIRECTORY_RELATIVE)


def launcher_name(capability_id: str) -> str:
    """The file name for a capability, or a refusal that says why the name cannot be one."""

    if not _SAFE_NAME.match(capability_id or ""):
        raise AirootError(
            "INVALID_INPUT",
            f"capability id {capability_id!r} cannot be used as a file name",
            evidence=[
                "a stable entry is named after the capability it exposes",
                "allowed: letters, digits, dot, dash, underscore; must start with a letter or digit",
                "a name that needs cleaning is a name that will not match what a caller asks for",
            ],
        )
    return capability_id + LAUNCHER_SUFFIX


def launcher_path(root: Path, capability_id: str) -> Path:
    return launcher_directory(root) / launcher_name(capability_id)


def resolve_cli_app() -> Path:
    """The directory that has to be on ``PYTHONPATH`` for ``python -m airoot`` to work."""

    import airoot

    return Path(airoot.__file__).resolve().parents[1]


def render_launcher(
    *,
    capability_id: str,
    root: Path,
    python: str | None = None,
    cli_app: Path | None = None,
) -> bytes:
    """The exact bytes of the stable entry for one capability.

    Pure: same inputs, same bytes. Everything machine-specific (the interpreter, where this build's
    code lives, the root) is a parameter so a test can pin the bytes and a drift check can re-render
    them. Nothing version-specific appears: that is the whole point.
    """

    name = launcher_name(capability_id)
    interpreter = python or sys.executable
    code = Path(cli_app) if cli_app is not None else resolve_cli_app()
    root_text = str(Path(root))
    lines = [
        "@echo off",
        f"rem AIROOT stable entry for {capability_id} ({name}) -- written by the EXPOSED step of a",
        "rem managed install. Decided: ADR-0050. It contains no version and no instance id: the",
        "rem active binding is resolved from the registry when this file is called, so installing a",
        "rem newer version rewrites it with byte-identical content. Hand edits are reported as drift",
        "rem by `airoot path verify`, which measures this file instead of assuming it is intact.",
        f"rem root={root_text}",
        f"rem cli={code}",
        f"rem python={interpreter}",
        f"set \"PYTHONPATH={code}\"",
        # `--` is mandatory here: without it the caller's own flags are parsed by AIROOT instead
        # of being handed to the payload ("unrecognized arguments: --version" was the measured
        # failure). A stable entry for `cargo` has to behave like `cargo`.
        f"\"{interpreter}\" -m airoot --root \"{root_text}\" run --capability {capability_id} -- %*",
        "exit /b %ERRORLEVEL%",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


@dataclass(frozen=True)
class LauncherWrite:
    """What a write attempt did — reported, never assumed."""

    capability_id: str
    path: str
    digest: str
    created: bool
    rewritten: bool

    @property
    def changed(self) -> bool:
        return self.created or self.rewritten

    def to_document(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "path": self.path,
            "digest": f"sha256:{self.digest}",
            "created": self.created,
            "rewritten": self.rewritten,
        }


def write_launcher(
    root: Path,
    capability_id: str,
    *,
    python: str | None = None,
    cli_app: Path | None = None,
) -> LauncherWrite:
    """Write the stable entry for ``capability_id``, if the bytes are not already there.

    Idempotent on purpose: the ``EXPOSED`` step is replayed when a transaction is recovered, and a
    recovery that rewrote every launcher would make "the file changed" indistinguishable from "the
    step ran again". ``rewritten`` is therefore true only when the content actually differed.
    """

    path = launcher_path(root, capability_id)
    payload = render_launcher(capability_id=capability_id, root=root, python=python, cli_app=cli_app)
    digest = hashlib.sha256(payload).hexdigest()

    created = not path.is_file()
    rewritten = False
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif path.read_bytes() == payload:
        return LauncherWrite(capability_id, str(path), digest, created=False, rewritten=False)
    else:
        rewritten = True

    path.write_bytes(payload)
    return LauncherWrite(capability_id, str(path), digest, created=created, rewritten=rewritten)


@dataclass(frozen=True)
class LauncherInfo:
    """One file found in the launcher directory, and whether this build would still write it."""

    capability_id: str
    path: str
    digest: str
    matches_current: bool

    def to_document(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "path": self.path,
            "digest": f"sha256:{self.digest}",
            "matches_current": self.matches_current,
        }


def discover_launchers(root: Path, *, python: str | None = None, cli_app: Path | None = None) -> list[LauncherInfo]:
    """Every stable entry in the sanctioned directory, with a byte-level drift verdict.

    The verdict is a **re-render**: the file is compared with what this build would write for the same
    capability, same root, same interpreter. That catches both a hand edit and a moved interpreter or
    checkout without parsing a single line of ``cmd``.
    """

    directory = launcher_directory(root)
    if not directory.is_dir():
        return []

    found: list[LauncherInfo] = []
    for path in sorted(directory.glob("*" + LAUNCHER_SUFFIX)):
        capability_id = path.name[: -len(LAUNCHER_SUFFIX)]
        data = path.read_bytes()
        expected = render_launcher(capability_id=capability_id, root=root, python=python, cli_app=cli_app)
        found.append(
            LauncherInfo(
                capability_id=capability_id,
                path=str(path),
                digest=hashlib.sha256(data).hexdigest(),
                matches_current=data == expected,
            )
        )
    return found


__all__ = [
    "LAUNCHER_DIRECTORY_RELATIVE",
    "LAUNCHER_SUFFIX",
    "LauncherInfo",
    "LauncherWrite",
    "discover_launchers",
    "launcher_directory",
    "launcher_name",
    "launcher_path",
    "render_launcher",
    "resolve_cli_app",
    "write_launcher",
]
