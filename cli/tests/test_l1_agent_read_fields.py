"""L1: the agent's ``read`` contract — every field it names must exist in the real document.

``agents/airoot.json`` tells an agent *which output fields to trust* for each question
(``"read": ["found", "executable", "selection_reason", ...]``). Until now the only check on that
list was that it was **non-empty** (``test_l1_skill.py``). A renamed field therefore sends the
agent looking for something that is not there — and that is worse than a missing command, because
the agent cannot tell "absent" from "false": a missing ``in_sync`` reads as "not in sync".

This module builds one realistic root and resolves every ``read`` path against the document the
CLI actually prints. Two consequences worth naming:

* the check is exact rather than a keyword guess — the paths are resolved structurally;
* the same root is an **acceptance face**: it produces the agent-facing documents whose shape the
  Rust port has to reproduce, which is what the golden corpus is for (ADR-0001), for the verbs the
  golden corpus does not yet cover.

An invocation with no scenario here is not silently skipped: ``UNCOVERED`` must name it *and* say
why, and a test asserts the union is complete. A new entry in ``agents/airoot.json`` therefore
cannot appear without either a scenario or a written excuse (draft §42).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

import fake_issuer

from airoot.caps.environment import InMemoryEnvironmentStore
from airoot.caps.exposure import (
    ExposureTarget,
    apply_reference_plan,
    build_reference_plan,
    request_from_entry,
)
from airoot.cli import main
from airoot.tx import create_plan
from airoot.tx.simulate import SimulationRunner

REPO = Path(__file__).resolve().parents[2]
AGENT_META = REPO / "agents" / "airoot.json"

CAPABILITY = "fake-tool"
REFERENCE = "external/dr-env/python"
SESSION = "read-contract"
DATA_ROOT_ID = "dr-env"

#: A real PE so the capability whitelist can classify it by evidence, not by file name.
PE = Path(sys.executable)

#: Invocation → why it cannot be produced here. Empty is the goal; a non-empty entry is a promise
#: to a reader that the omission was considered, not overlooked.
UNCOVERED: dict[str, str] = {}


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[\])?$")


def unresolved(document: dict, paths: list[str]) -> list[str]:
    """The subset of ``paths`` (``a.b[].c`` notation) that does not resolve in ``document``.

    An ``[]`` segment over an **empty** list counts as resolved: there is nothing to check, and a
    healthy ``doctor`` legitimately has no diagnostics to carry ``diagnostics[].code``. Treating
    that as a failure would make the guard unusable on the common case.
    """

    missing: list[str] = []
    for path in paths:
        nodes: list[object] = [document]
        found = True
        for raw in path.split("."):
            match = _SEGMENT.match(raw)
            if match is None:
                found = False
                break
            key, drains_a_list = match.group(1), bool(match.group(2))
            collected: list[object] = []
            for node in nodes:
                if not isinstance(node, dict) or key not in node:
                    found = False
                    break
                value = node[key]
                if drains_a_list:
                    if not isinstance(value, list):
                        found = False
                        break
                    collected.extend(value)
                else:
                    collected.append(value)
            if not found:
                break
            nodes = collected
        if not found:
            missing.append(path)
    return missing


def invocations() -> list[dict]:
    return json.loads(AGENT_META.read_text(encoding="utf-8"))["invocation"]


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def data_root(tests_tmp: Path) -> Path:
    path = tests_tmp / "read-contract-env"
    shutil.rmtree(path, ignore_errors=True)
    (path / "python").mkdir(parents=True)
    shutil.copy2(PE, path / "python" / "python.exe")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def checksums(tmp_path: Path) -> Path:
    """An air-gapped release: the digest comes from a published file, never from the catalog."""

    artifact = tmp_path / "cmake-3.31.6-windows-x86_64.zip"
    artifact.write_bytes(b"portable archive bytes, no installer script\n")
    path = tmp_path / "cmake-3.31.6-SHA-256.txt"
    path.write_text(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n", encoding="utf-8")
    return path


def test_the_documented_read_path_count_is_the_number_this_module_resolves() -> None:
    """`AGENTS.md` states how many `read` paths exist; that number was true once and then drifted.

    Measured in draft §73: it said 117, which *was* the count when §1 was written (commit
    `40f4937`); every later stage that added a lane or a field left the prose alone, so §73 read 129
    before it touched anything. The **test** count has been tied to reality since §54 — this is the
    same tie for the count an agent quotes when it says what it reads.
    """

    agents_text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    stated = {int(value) for value in re.findall(r"(\d+)\s*条\s*`read`\s*路径", agents_text)}
    assert stated, "AGENTS.md no longer states the read-path count; this guard is about nothing"
    actual = sum(len(entry.get("read", [])) for entry in invocations())
    assert stated == {actual}, f"AGENTS.md states {sorted(stated)} read paths; the metadata has {actual}"


def test_every_read_path_resolves_in_the_document_the_cli_prints(
    capsys, root, registry, clock, cli_root: Path, data_root: Path, checksums: Path, tmp_path: Path
) -> None:
    """One root, every agent-facing invocation, every ``read`` path resolved structurally."""

    fake_issuer.install_keyring(root.path)
    plan = create_plan(registry, version="1.0.0", clock=clock)
    SimulationRunner(registry, clock=clock).commit(plan, fake_issuer.issue(plan, clock=clock))
    owned = str(plan["target"]["instance_id"])

    base = ["--json", "--root", str(cli_root)]
    results: dict[str, tuple[int, dict]] = {}

    def record(command: str, *argv: str) -> None:
        results[command] = run(capsys, *base, *argv)

    # --- setup that the observation verbs need (also the `adopt` scenario) --------------------
    # §79 gave `data-root add` its own lane, so this invocation is recorded rather than run: the
    # field list in `agents/airoot.json` now has to resolve against the document this prints.
    record(
        "data-root add <path> --id <data_root_id> --role <runtime|tool|mixed>",
        "data-root", "add", str(data_root), "--id", DATA_ROOT_ID, "--role", "runtime",
    )
    record("adopt <path> --mode reference", "adopt", str(data_root / "python"), "--mode", "reference")
    # `import` is a different question and a different document (a plan, not a reference), so it gets
    # its own recorded scenario. A real PE file is the input: the backend refuses script payloads.
    # The capability is a frozen *tool* on purpose — since §70 the routing gate refuses the high-risk
    # classes (a frozen `runtime`, or a payload over the confirmation threshold) exactly as `plan`
    # does, and `python` is a runtime.
    import_source = tmp_path / "imported-7z.exe"
    shutil.copy2(PE, import_source)
    record(
        "adopt <file> --mode import --capability <id>",
        "adopt",
        str(import_source),
        "--mode",
        "import",
        "--capability",
        "archive",
        "--version",
        "24.09",
    )

    # A persisted record has to be seeded through the module API with an *injected* store: the CLI
    # would write the real HKCU, which tests may never do (conftest's host-mutation guard). The CLI
    # then reads the record back out of the registry, which is what `env forget` reports on.
    object_root = data_root / "python"
    target = ExposureTarget(
        external_id=REFERENCE,
        capability_id="python",
        path=object_root,
        object_root=object_root,
        entrypoint_dir=object_root,
    )
    entry = {"variables": {"AIROOT_PYTHON_ROOT": "<object_root>"}, "path_prepend": []}
    request = request_from_entry(target=target, entry=entry, data_roots=(data_root,))
    apply_reference_plan(
        build_reference_plan(request, registry=registry),
        registry=registry,
        store=InMemoryEnvironmentStore(),
    )

    # --- observation first: the mutating verbs below run last, on purpose ---------------------
    record("root status", "root", "status")
    record("doctor", "doctor")
    record("where <capability>", "where", "python")
    record("where <capability> --version <constraint>", "where", "python", "--version", ">=3")
    record(
        "inventory --class <managed_tool|runtime|external_reference|unmanaged>",
        "inventory",
        "--class",
        "external_reference",
    )
    record("discover", "discover")
    record("capability list", "capability", "list")
    record("capability check <path>", "capability", "check", str(data_root / "python"))
    record(
        "plan <capability> --scope <project|data-root> --target <dir|data-root:id> --dry-run",
        "plan",
        CAPABILITY,
        "--scope",
        "data-root",
        "--target",
        f"data-root:{DATA_ROOT_ID}",
        "--dry-run",
    )
    record("scope decide <capability>", "scope", "decide", "python")
    record("source list", "source", "list")
    record(
        "source resolve <capability> --version <v>",
        "source",
        "resolve",
        "build",
        "--version",
        "3.31.6",
        "--offline-checksum",
        str(checksums),
        "--source-out",
        str(tmp_path / "resolved.json"),
    )
    record("tool list", "tool", "list")
    record("tool status <instance-id>", "tool", "status", owned)
    record("tool verify <instance-id>", "tool", "verify", owned)
    record("path verify", "path", "verify")
    record("env activate <external-id> --shell <powershell|cmd>", "env", "activate", REFERENCE, "--shell", "powershell")
    # The session form is not a separate invocation entry; it is set up because `env deactivate`
    # needs a snapshot to pop.
    run(capsys, *base, "env", "activate", "--session", SESSION, REFERENCE)
    record("env list", "env", "list")
    record("exec <external-id> -- <command>", "exec", REFERENCE, "--", str(PE), "-c", "print(1)")
    # Without a token this stops at the approval boundary and hands the caller the plan to approve.
    record("env persist <external-id>", "env", "persist", REFERENCE)
    record("env forget <external-id>", "env", "forget", REFERENCE, "--dry-run")
    # `--all` answers a different question, so it is a separate invocation entry, and its dry run is
    # what can be recorded here: the real one writes HKCU (draft §63).
    record("env forget --all", "env", "forget", "--all", "--dry-run")
    record("env deactivate --session <id>", "env", "deactivate", "--session", SESSION)
    record("tool pin <capability>", "tool", "pin", CAPABILITY, "--version", "1.0.0")
    record("search <query>", "search", "--query", "python")
    record("rebuild", "rebuild")
    record("repair", "repair")

    # --- mutations last, so nothing above loses its precondition ------------------------------
    record("tool retire <instance-id>", "tool", "retire", owned)
    record("tool gc --plan", "tool", "gc", "--plan")
    record("uninstall <instance-id>", "uninstall", owned, "--dry-run")
    record("forget <external-id>", "forget", REFERENCE)

    problems: list[str] = []
    covered: set[str] = set()
    for entry in invocations():
        command = " ".join(entry["command"])
        if command in UNCOVERED:
            continue
        if command not in results:
            problems.append(f"{command}: no scenario produces this document")
            continue
        covered.add(command)
        code, document = results[command]
        if not document:
            problems.append(f"{command}: exit {code} printed nothing")
            continue
        missing = unresolved(document, entry["read"])
        if missing:
            problems.append(f"{command}: exit {code} does not carry {missing}")

    assert problems == [], "agents/airoot.json names fields the output does not have:\n" + "\n".join(problems)
    # Exact, not a threshold: every invocation either produced a document or is declared above.
    assert len(covered) == len(invocations()) - len(UNCOVERED)


def test_every_invocation_is_either_exercised_or_declared_uncovered() -> None:
    """An unexercised invocation must be a written decision, not an omission (draft §42.4)."""

    commands = {" ".join(entry["command"]) for entry in invocations()}
    unknown = sorted(set(UNCOVERED) - commands)
    assert unknown == [], f"UNCOVERED names invocations that no longer exist: {unknown}"

    # Every entry also has a non-empty `read` list; an empty one would make the check vacuous.
    empty = sorted(" ".join(entry["command"]) for entry in invocations() if not entry.get("read"))
    assert empty == [], f"these invocations tell the agent to read nothing: {empty}"


def test_the_read_path_resolver_reports_what_is_actually_missing() -> None:
    """A resolver that returns [] for everything would make the whole module a no-op."""

    document = {"data": {"results": [{"path": "a", "management": "managed"}]}, "found": True}

    assert unresolved(document, ["found", "data.results[].path"]) == []
    assert unresolved(document, ["absent"]) == ["absent"]
    assert unresolved(document, ["data.absent"]) == ["data.absent"]
    assert unresolved(document, ["data.results[].absent"]) == ["data.results[].absent"]
    # A list path over an empty list is covered (there is simply nothing to check), but a
    # non-list where the notation promises one is not.
    assert unresolved({"data": {"results": []}}, ["data.results[].path"]) == []
    assert unresolved({"data": {"results": {}}}, ["data.results[].path"]) == ["data.results[].path"]
    assert unresolved({"found": None}, ["found"]) == []
