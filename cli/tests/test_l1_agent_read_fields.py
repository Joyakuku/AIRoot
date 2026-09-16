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
from airoot.schema_io import load_schema
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


def vacuous_paths(document: dict, paths: list[str]) -> list[str]:
    """Read paths whose `[]` segment drained an **empty** list (draft §99).

    `unresolved` deliberately counts an empty list as resolved — a healthy `doctor` has no diagnostics
    to carry `diagnostics[].code` — which makes such a path *checked vacuously*: the key could be
    renamed and the check would stay green, because there is nothing to look at either way. A lane that
    tells an agent to read `x[].y` therefore needs a scenario in which `x` is not empty, and this is how
    that requirement is stated rather than assumed.
    """

    empty: list[str] = []
    for path in paths:
        nodes: list[object] = [document]
        drained_empty = False
        for raw in path.split("."):
            match = _SEGMENT.match(raw)
            if match is None:
                break
            key, drains = match.group(1), bool(match.group(2))
            nxt: list[object] = []
            for node in nodes:
                if not isinstance(node, dict) or key not in node:
                    continue
                value = node[key]
                if drains:
                    if isinstance(value, list):
                        if not value:
                            drained_empty = True
                        nxt.extend(value)
                else:
                    nxt.append(value)
            nodes = nxt
        if drained_empty:
            empty.append(path)
    return empty


#: Every published schema file name, for the check below. Derived, never listed.
def _schema_stems() -> list[str]:
    from airoot import SCHEMA_DIR

    return sorted(path.name[: -len(".schema.json")] for path in SCHEMA_DIR.glob("*.schema.json"))


def _document_schemas() -> list[str]:
    """Schemas that describe a *document*: a fragment (`common`) has no `required`, so **everything**
    validates against it and it can decide nothing here (the same rule §93 uses)."""

    return [stem for stem in _schema_stems() if load_schema(stem).get("required")]


def document_schema_problems(command: str, entry: dict, document: dict) -> list[str]:
    """§94: `document_schema` must be true of the document the CLI actually printed.

    The lane tells an agent which fields to read; this says whether a published contract describes the
    document those fields live in. Both directions matter:

    * a declared schema must **validate** the document, and at least one read path must be a top-level
      property of it — otherwise the declaration points at a nested contract and reads as a promise the
      lane does not make (`tool pin` produces a `plan`, but the lane reads the pin *report*);
    * a `null` must be measured, not assumed: if some published schema does describe the document, the
      lane is pinned and saying "no contract" is the same defect in the other direction.
    """

    from airoot.schema_io import errors_for

    declared = entry.get("document_schema", "<missing>")
    if declared == "<missing>":
        return [f"{command}: no document_schema key (draft §94)"]

    validating = [stem for stem in _document_schemas() if not errors_for(stem, document)]

    if declared is None:
        if validating:
            return [f"{command}: declares no schema, but {validating} describes this document exactly"]
        return []

    problems: list[str] = []
    if declared not in _document_schemas():
        return [f"{command}: document_schema {declared!r} is not a published document schema"]
    if errors_for(declared, document):
        problems.append(f"{command}: document_schema {declared} does not validate the printed document")
    tops = {str(path).split(".")[0].split("[")[0] for path in entry.get("read") or []}
    properties = set(load_schema(declared).get("properties", {}))
    if not tops & properties:
        problems.append(
            f"{command}: document_schema {declared} is not the document the read paths live in "
            f"(none of {sorted(tops)} is a top-level property of it)"
        )
    return problems


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
    # `discover` reads `missing[].data_root_id` / `missing[].reason_code` since §103, and §99's screen
    # refuses a read path whose list is empty ("a renamed key would pass"). So a second data root is
    # declared and its directory removed for exactly this one command, then put back: the registry row
    # stays, so every verb recorded after this one sees a healthy root again.
    ghost = tmp_path / "ghost-root"
    ghost.mkdir()
    run(capsys, *base, "data-root", "add", str(ghost), "--id", "dr-ghost", "--role", "runtime")
    shutil.rmtree(ghost)
    record("discover", "discover")
    ghost.mkdir()
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
    # `run` executes a payload AIROOT **owns**, so the fake fixture (a data file that is never
    # executed) cannot serve this lane: it must have a real, startable payload. A second instance is
    # declared for exactly this, with a `.cmd` in its store directory — the same shape
    # `test_l1_runtime.py` uses, and for the same reason: the verb's whole point is that it starts
    # something, so a mocked child would only test the mock.
    runnable_id = "probe-tool/probe/1.0.0/win-x64"
    runnable_dir = Path(root.path) / "store" / runnable_id
    runnable_dir.mkdir(parents=True, exist_ok=True)
    (runnable_dir / "probe.cmd").write_text(
        "@echo off\r\necho ran from the store\r\nexit /b 0\r\n", encoding="utf-8"
    )
    from airoot.registry.entities import Instance

    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id=runnable_id,
                kind="managed_tool",
                capability_id="probe-tool",
                version="1.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id="https_artifact",
                artifact_digest="sha256:" + "b" * 64,
                store_path=f"store/{runnable_id}",
                lifecycle_status="installed",
                health="healthy",
                entrypoints=("probe.cmd",),
                created_at="2024-01-01T00:00:00Z",
            ),
        )
    record("run", "run", runnable_id, "--", "--version")
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
    # `repair` reads `repaired[].action` / `repaired[].state`, and an empty `repaired` array makes those
    # reads **vacuous** — the resolver treats an empty list as resolved, so a renamed key would leave the
    # check green (draft §99). Leaving a transaction interrupted at `ACTIVE_BOUND` gives the repair
    # something to report, which is the only way those two paths are checked at all.
    interrupted = create_plan(registry, version="1.0.0", clock=clock)
    from conftest import FaultInjector

    SimulationRunner(registry, clock=clock, injector=FaultInjector(stop_after="ACTIVE_BOUND")).commit(
        interrupted, fake_issuer.issue(interrupted, clock=clock)
    )
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
        holes = vacuous_paths(document, entry["read"])
        if holes:
            problems.append(
                f"{command}: exit {code} makes {holes} vacuous — the list is empty, so a renamed key "
                "would pass; give this lane a scenario that produces one"
            )
        problems += document_schema_problems(command, entry, document)

    unpinned = {
        " ".join(entry["command"]): results[" ".join(entry["command"])][1]
        for entry in invocations()
        if not entry.get("document_schema") and " ".join(entry["command"]) in results
    }
    problems += report_envelope_problems(unpinned, REPORT_ENVELOPE_KEYS)

    assert problems == [], "agents/airoot.json names fields the output does not have:\n" + "\n".join(problems)
    # Exact, not a threshold: every invocation either produced a document or is declared above.
    assert len(covered) == len(invocations()) - len(UNCOVERED)


def test_the_documented_document_schema_count_is_the_number_measured() -> None:
    """The honesty note says how many lanes are pinned; a drifted sentence would be a small lie.

    Same tie §54 gave the test count and §73 the read-path count: the number an agent quotes has to be
    the number the metadata actually carries.
    """

    note = json.loads(AGENT_META.read_text(encoding="utf-8"))["honesty"]["document_schema_note"]
    lanes = invocations()
    pinned = sum(1 for entry in lanes if entry.get("document_schema"))

    stated = re.findall(r"(\d+) of (\d+) lanes", note)
    assert stated, "the honesty note no longer states how many lanes are pinned; this guard is about nothing"
    assert {tuple(int(part) for part in pair) for pair in stated} == {(pinned, len(lanes))}, (
        f"the note says {stated}; the metadata has {pinned} pinned of {len(lanes)} lanes"
    )


def test_the_document_schema_check_reports_both_directions() -> None:
    """A check that only ever agrees would leave every lane green while telling an agent nothing."""

    healthy = {
        "schema_version": 1,
        "status": "healthy",
        "root_instance_id": "root-x",
        "registry_generation": 0,
        "diagnostics": [],
        "checked_at": "2026-01-01T00:00:00Z",
    }
    reader = {"read": ["status"]}

    # Declared and true.
    assert document_schema_problems("doctor", dict(reader, document_schema="doctor-response"), healthy) == []
    # Declared but the document does not satisfy it.
    assert document_schema_problems(
        "doctor", dict(reader, document_schema="where-response"), healthy
    ), "a schema that does not validate the document must be reported"
    # Declared but the read paths live in a different document (the `tool pin` shape: reachable, not read).
    assert document_schema_problems(
        "doctor",
        {"read": ["in_sync"], "document_schema": "doctor-response"},
        healthy,
    ), "a schema whose top-level properties the read paths do not name must be reported"
    # Declared as unpinned while a schema describes it exactly.
    assert document_schema_problems(
        "doctor", {"read": ["status"], "document_schema": None}, healthy
    ), "a measured pin declared as no-contract must be reported"
    # Nothing declared at all.
    assert document_schema_problems("doctor", dict(reader), healthy)
    # And a name that is not a published document schema (`common` is a fragment).
    assert document_schema_problems("doctor", dict(reader, document_schema="common"), healthy)


def test_every_invocation_is_either_exercised_or_declared_uncovered() -> None:
    """An unexercised invocation must be a written decision, not an omission (draft §42.4)."""

    commands = {" ".join(entry["command"]) for entry in invocations()}
    unknown = sorted(set(UNCOVERED) - commands)
    assert unknown == [], f"UNCOVERED names invocations that no longer exist: {unknown}"

    # Every entry also has a non-empty `read` list; an empty one would make the check vacuous.
    empty = sorted(" ".join(entry["command"]) for entry in invocations() if not entry.get("read"))
    assert empty == [], f"these invocations tell the agent to read nothing: {empty}"


def test_the_vacuity_check_reports_what_it_is_for() -> None:
    """§99: the resolver counts an empty list as resolved, so `x[].y` can be checked vacuously.

    This pins the helper that finds those: an empty list is reported, a non-empty one is not, and a
    path with no list segment at all is never reported (or every scalar read would look vacuous).
    """

    assert vacuous_paths({"diagnostics": []}, ["diagnostics[].code"]) == ["diagnostics[].code"]
    assert vacuous_paths({"diagnostics": [{"code": "x"}]}, ["diagnostics[].code"]) == []
    assert vacuous_paths({"found": True}, ["found"]) == []
    # Only a **drained** segment counts. `reports[].candidates` reads an empty list as its value, which
    # is a legitimate answer — the vacuity is when the list you page through is itself empty, because
    # then no row's keys are looked at. A nested drain is caught the same way.
    assert vacuous_paths({"reports": [{"candidates": []}]}, ["reports[].candidates"]) == []
    assert vacuous_paths({"reports": []}, ["reports[].candidates"]) == ["reports[].candidates"]
    assert vacuous_paths({"a": [{"b": []}]}, ["a[].b[].c"]) == ["a[].b[].c"]
    assert vacuous_paths({"a": [{"b": [{"c": 1}]}]}, ["a[].b[].c"]) == []
    # An `[]` segment the document does not carry at all is `unresolved`'s finding, not this one's.
    assert vacuous_paths({}, ["diagnostics[].code"]) == []


#: The keys **every** unpinned report carries, and nothing else (draft §100).
#:
#: §94 recorded the unlock for the 29 unpinned lanes as "one report-envelope schema". Measuring it says
#: that unlock does not exist: the 29 reports have **29 distinct top-level shapes**, so one schema with
#: `additionalProperties: false` cannot describe them, and one with `additionalProperties: true` would be
#: the "capability that only looks safe" the doctrine refuses — a published contract that accepts any
#: extra key pins nothing worth pinning. What *is* shared is exactly these two keys, which is a real
#: (if small) envelope property, and it is what this guard holds: no report may drop one, and no third
#: key may become universal without the note in `agents/airoot.json` being rewritten.
REPORT_ENVELOPE_KEYS = ("reason_code", "schema_version")


def report_envelope_problems(documents: dict[str, dict], declared: tuple[str, ...]) -> list[str]:
    """The keys common to every unpinned report must be exactly `declared`."""

    if len(documents) < 20:
        return [
            f"only {len(documents)} unpinned reports were produced; this guard is about the whole set"
        ]
    common: set[str] | None = None
    for document in documents.values():
        common = set(document) if common is None else common & set(document)
    common = common or set()
    return [f"{name} is carried by every report but is not declared" for name in sorted(common - set(declared))] + [
        f"{name} is declared as an envelope key but not every report carries it"
        for name in sorted(set(declared) - common)
    ]


def test_the_unpinned_reports_share_exactly_the_declared_envelope() -> None:
    """§100: one schema cannot pin 29 distinct shapes, so what is pinned is what they share."""

    documents = {"a" * 1: {}, "b": {}}
    assert report_envelope_problems(documents, REPORT_ENVELOPE_KEYS) != [], (
        "a population below the floor must be reported, or this guard checks nothing"
    )

    twenty = {
        f"report-{index}": {"schema_version": 1, "reason_code": "SUCCESS", f"only_{index}": index}
        for index in range(20)
    }
    assert report_envelope_problems(twenty, REPORT_ENVELOPE_KEYS) == []
    # A third key becoming universal has to be declared.
    widened = {name: dict(document, extra=True) for name, document in twenty.items()}
    assert report_envelope_problems(widened, REPORT_ENVELOPE_KEYS) == [
        "extra is carried by every report but is not declared"
    ]
    # And a report dropping one of the two has to be reported.
    dropped = {name: {k: v for k, v in document.items() if k != "reason_code"} for name, document in twenty.items()}
    assert report_envelope_problems(dropped, REPORT_ENVELOPE_KEYS) == [
        "reason_code is declared as an envelope key but not every report carries it"
    ]


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
