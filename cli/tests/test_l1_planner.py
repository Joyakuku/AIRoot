"""The dependency-routing decision layer (draft §12) and its CLI.

The point of these tests is that the *decision* is deterministic and honest: the obvious
cases are never asked about, the risky ones always are, and an unknown size stays unknown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.discovery import load_whitelist
from airoot.caps.planner import (
    CAPABILITY_ALIASES,
    CONFIRMATION_OPTIONS,
    DEFAULT_SIZE_THRESHOLD_BYTES,
    DEPENDENCY_CONTAINERS,
    PROJECT_MANIFESTS,
    SCOPE_DATA_ROOT,
    SCOPE_PROJECT,
    SCOPE_REFERENCE_ONLY,
    SCOPE_UNSUPPORTED,
    ScopeRequest,
    TOOLING_MEMORY_RELATIVE,
    declared_capabilities,
    decide_scope,
    manifest_fingerprint,
    manifest_hits,
    manifest_paths,
    read_tooling_memory,
    tooling_memory_path,
)
from airoot.cli import main


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


def write_memory(project_root: Path, capability: str, scope: str) -> Path:
    path = tooling_memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "manifest_fingerprint": manifest_fingerprint(project_root),
                "choices": {capability: {"scope": scope, "decided_by": "human"}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# manifest detection
# --------------------------------------------------------------------------- #


def test_manifest_paths_only_reports_present_files(project: Path) -> None:
    assert manifest_paths(project) == []
    (project / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    assert [path.name for path in manifest_paths(project)] == ["pyproject.toml"]


def test_project_declaration_wins_and_is_never_asked(project: Path) -> None:
    """§170 moved this fixture onto a declaration the rule reads.

    It used to declare ``nodeenv`` / ``python-dotenv`` and rely on a substring match reading
    ``python`` inside ``python-dotenv``. The rule now compares whole names, so the fixture names the
    capability it means; that ``python-dotenv`` is *not* a declaration is asserted separately below.
    """

    (project / "requirements.txt").write_text("python>=3.11\n", encoding="utf-8")

    decision = decide_scope(ScopeRequest(capability_id="python", project_root=project))

    assert decision.scope == SCOPE_PROJECT
    assert decision.confirmation_required is False
    assert decision.origin == "project_manifest"
    assert decision.reason_code == "SUCCESS"
    assert decision.options == ()
    assert "requirements.txt" in decision.evidence[1]["detail"]


def test_a_toml_section_name_is_not_a_project_dependency(project: Path) -> None:
    """§170: `[build-system]` is the table that carries `requires`; it is not a dependency on `build`.

    Measured before this rule: a `pyproject.toml` holding **only** a `[build-system]` table was read
    as "this project depends on cmake", so `scope decide build --project` answered
    `origin=project_manifest / scope=project` with `manifest_hits=["build"]`, and
    `plan build --scope data-root --target data-root:<id> --project <proj>` exited 4 with
    `SCOPE_UPGRADE_REQUIRES_APPROVAL`. The cause was a plain substring match over the concatenated
    manifest text, where the section name contains the capability id. A gate that fires on a section
    name is exactly the noise that makes the confirmations that matter meaningless (draft §12.1).
    """

    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=61.0", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )

    decision = decide_scope(ScopeRequest(capability_id="build", project_root=project))

    assert decision.manifest_hits == ()
    assert decision.origin != "project_manifest"
    assert decision.scope == SCOPE_DATA_ROOT
    assert decision.confirmation_required is False, "a single-file tool must not gain a confirmation gate"
    assert [item["kind"] for item in decision.evidence] == ["query", "generic_tool"]


def test_a_declared_dependency_is_still_a_project_dependency(project: Path) -> None:
    """§170 non-vacuity: fixing the section-name reading must not turn every manifest into a miss.

    One positive per declaration shape the rule reads — a TOML dependency array naming the
    capability's **alias** (`cmake` for `build`, which is the case an alias table exists for), a TOML
    array naming the id, a `package.json` dependency, its `engines` block, a `devDependencies` entry
    naming node by its other common name, and a line in a `requirements.txt`.
    """

    cases = {
        "pyproject-alias": ('pyproject.toml', '[build-system]\nrequires = ["cmake>=3.31"]\n', "build"),
        "pyproject-id": ("pyproject.toml", 'dependencies = ["python"]\n', "python"),
        "package-json-dependency": ("package.json", '{"dependencies": {"node": "^20"}}', "node"),
        "package-json-engines": ("package.json", '{"engines": {"node": ">=18"}}', "node"),
        "package-json-npm": ("package.json", '{"devDependencies": {"npm": "^10"}}', "node"),
        "requirements-line": ("requirements.txt", "python3>=3.11\n", "python"),
    }

    for label, (name, content, capability) in sorted(cases.items()):
        case = project / label
        case.mkdir()
        (case / name).write_text(content, encoding="utf-8")

        decision = decide_scope(ScopeRequest(capability_id=capability, project_root=case))

        assert decision.scope == SCOPE_PROJECT, label
        assert decision.origin == "project_manifest", label
        assert decision.manifest_hits == (capability,), label


def test_a_name_that_only_contains_the_capability_is_not_a_declaration(project: Path) -> None:
    """§170: whole names, not substrings — which is the property the section-name fix rests on.

    `python-dotenv` is a dependency on dotenv, not on the interpreter, and reading it as `python` is
    the same mistake as reading `[build-system]` as `build`. The pairing matters: the section-name
    guard above would pass just as well if the whole classifier returned "no hit" always.
    """

    (project / "requirements.txt").write_text("nodeenv\npython-dotenv\n", encoding="utf-8")

    assert declared_capabilities(project, load_whitelist()) == ()
    decision = decide_scope(ScopeRequest(capability_id="python", project_root=project))
    assert decision.origin != "project_manifest"


def declare_for(project: Path, capability_id: str) -> tuple[str, ...]:
    """The manifest hits a decision reports, or ``()`` when the routing did not come from a manifest."""

    decision = decide_scope(ScopeRequest(capability_id=capability_id, project_root=project))
    return decision.manifest_hits if decision.origin == "project_manifest" else ()


def test_a_dependency_hidden_behind_a_script_or_project_name_is_not_read(project: Path) -> None:
    """§170: only dependency *declarations* are read, not every key of a manifest.

    `{"scripts": {"build": ...}}` is a task, and `{"name": "build"}` is what the project is called;
    reading either as "this project depends on cmake" is the section-name mistake one level down.
    """

    (project / "package.json").write_text(
        '{"name": "build", "scripts": {"build": "webpack"}, "dependencies": {"left-pad": "1.0.0"}}',
        encoding="utf-8",
    )

    assert declare_for(project, "build") == ()
    assert declare_for(project, "node") == ()


def test_the_alias_table_is_what_the_rule_consults(project: Path) -> None:
    """§170: the old docstring claimed "the common alias forms" while the code had none.

    A claim in the source is worth what a test can read back, so this reads both directions. The
    docstring and the table must agree about whether aliases exist at all — a table with a docstring
    that denies it and a promise with no table are the two ways the §170 defect comes back — and every
    alias the table declares has to produce a hit from a manifest that names it.
    """

    from airoot.caps import planner

    docstring = declared_capabilities.__doc__ or ""
    claims_aliases = "alias" in docstring.lower()
    assert claims_aliases == bool(CAPABILITY_ALIASES), (
        "the docstring and the alias table disagree: either the rule consults alias forms and says so, "
        "or it consults none and claims none"
    )
    assert CAPABILITY_ALIASES, "an empty table under a docstring that promises aliases is the §170 defect"
    whitelist = load_whitelist()
    for capability_id, aliases in sorted(CAPABILITY_ALIASES.items()):
        for alias in aliases:
            case = project / f"alias-{capability_id}-{alias}"
            case.mkdir()
            (case / "requirements.txt").write_text(f"{alias}==1.0\n", encoding="utf-8")
            assert capability_id in declared_capabilities(case, whitelist), (
                f"{alias} is declared as an alias of {capability_id}, but the rule does not read it"
            )
    assert DEPENDENCY_CONTAINERS, "the rule reads dependency containers; the set cannot be empty"


def test_every_hit_says_which_declaration_it_came_from(project: Path) -> None:
    """§170: "referenced by a project manifest" has to be answerable, not just assertable.

    The hit carries the file, the 1-based line and the text it was read from, and the routing evidence
    repeats it, so a caller can check the claim instead of trusting it.
    """

    (project / "requirements.txt").write_text("left-pad\ncmake==3.31.6\n", encoding="utf-8")

    hits = manifest_hits(project, load_whitelist())

    assert [(hit.capability_id, hit.manifest, hit.line) for hit in hits] == [
        ("build", "requirements.txt", 2)
    ]
    assert hits[0].declaration == "cmake==3.31.6"
    assert "requirements.txt:2" in hits[0].describe()
    decision = decide_scope(ScopeRequest(capability_id="build", project_root=project))
    assert "requirements.txt:2" in decision.evidence[1]["detail"]
    assert "cmake" in decision.evidence[1]["detail"]


def test_a_manifest_without_the_capability_does_not_pull_it_into_the_project(project: Path) -> None:
    (project / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}', encoding="utf-8")

    decision = decide_scope(ScopeRequest(capability_id="python", project_root=project))

    assert decision.scope != SCOPE_PROJECT
    assert decision.manifest_hits == ()


def test_manifest_fingerprint_changes_when_a_manifest_changes(project: Path) -> None:
    (project / "pyproject.toml").write_text("a", encoding="utf-8")
    first = manifest_fingerprint(project)
    (project / "pyproject.toml").write_text("b", encoding="utf-8")
    assert manifest_fingerprint(project) != first
    assert manifest_fingerprint(project) is not None


def test_no_manifests_means_no_fingerprint(project: Path) -> None:
    assert manifest_fingerprint(project) is None


def test_project_manifest_list_is_documented() -> None:
    assert "pyproject.toml" in PROJECT_MANIFESTS
    assert "package.json" in PROJECT_MANIFESTS
    assert "poetry.lock" in PROJECT_MANIFESTS


# --------------------------------------------------------------------------- #
# routing rules
# --------------------------------------------------------------------------- #


def test_generic_tool_goes_to_the_data_root_without_asking() -> None:
    decision = decide_scope(ScopeRequest(capability_id="archive"))

    assert decision.scope == SCOPE_DATA_ROOT
    assert decision.confirmation_required is False
    assert decision.origin == "generic_tool"


def test_a_runtime_asks_because_it_creates_an_environment() -> None:
    """A runtime is the "creates an environment" case, and project-vs-data-root is a real choice.

    The closed must-confirm set (draft §12.1) is about packaging / environment creation /
    size / CUDA. Installing a runtime establishes an interpreter environment AND is the one
    case where "project-isolated" is a genuine alternative to the shared data root, so it
    stays on the asking side. The noise the draft warns about comes from confirming
    capabilities with only one sensible answer (`rule.kind == "tool"`) — not from this.
    """

    decision = decide_scope(ScopeRequest(capability_id="node"))

    assert decision.scope == SCOPE_DATA_ROOT
    assert decision.confirmation_required is True
    assert decision.origin == "high_risk"
    assert decision.reason_code == "SCOPE_CONFIRMATION_REQUIRED"
    assert decision.options == CONFIRMATION_OPTIONS
    assert "runtime" in " ".join(item["detail"] for item in decision.evidence)


def test_a_runtime_that_builds_an_environment_still_asks() -> None:
    decision = decide_scope(ScopeRequest(capability_id="node", creates_environment=True))

    assert decision.scope == SCOPE_DATA_ROOT
    assert decision.confirmation_required is True
    assert decision.origin == "high_risk"
    assert decision.reason_code == "SCOPE_CONFIRMATION_REQUIRED"
    assert decision.options == CONFIRMATION_OPTIONS


def test_creating_an_environment_always_asks() -> None:
    decision = decide_scope(ScopeRequest(capability_id="archive", creates_environment=True))
    assert decision.confirmation_required is True
    assert "creates an environment" in " ".join(item["detail"] for item in decision.evidence)


def test_cuda_or_native_binaries_always_ask() -> None:
    decision = decide_scope(ScopeRequest(capability_id="archive", requires_cuda_or_native=True))
    assert decision.confirmation_required is True


def test_size_over_the_threshold_asks_and_size_at_the_threshold_does_not() -> None:
    over = decide_scope(
        ScopeRequest(capability_id="archive", declared_size_bytes=DEFAULT_SIZE_THRESHOLD_BYTES + 1)
    )
    at = decide_scope(
        ScopeRequest(capability_id="archive", declared_size_bytes=DEFAULT_SIZE_THRESHOLD_BYTES)
    )
    assert over.confirmation_required is True
    assert at.confirmation_required is False


def test_unknown_size_is_reported_as_unknown_and_never_guessed() -> None:
    decision = decide_scope(ScopeRequest(capability_id="node"))

    assert decision.size_estimate_bytes is None
    assert decision.size_source == "unknown"
    assert decision.threshold_bytes == DEFAULT_SIZE_THRESHOLD_BYTES


def test_unverifiable_source_is_reference_only_and_never_installed() -> None:
    decision = decide_scope(ScopeRequest(capability_id="node", source_verifiable=False))

    assert decision.scope == SCOPE_REFERENCE_ONLY
    assert decision.confirmation_required is False
    assert decision.origin == "unverifiable_source"


def test_missing_capability_cannot_be_routed() -> None:
    decision = decide_scope(ScopeRequest(capability_id="not-a-capability"))

    assert decision.scope == SCOPE_UNSUPPORTED
    assert decision.reason_code == "CAPABILITY_NOT_DECLARED"
    assert decision.confirmation_required is False


def test_the_three_options_are_fixed() -> None:
    assert CONFIRMATION_OPTIONS == ("project-isolated", "data-root", "cancel")
    decision = decide_scope(ScopeRequest(capability_id="node"))
    assert decision.options == CONFIRMATION_OPTIONS, "options may not be renamed or extended"


def test_decisions_are_deterministic() -> None:
    request = ScopeRequest(capability_id="node", creates_environment=True)
    first = json.dumps(decide_scope(request).to_document(), sort_keys=True)
    second = json.dumps(decide_scope(request).to_document(), sort_keys=True)
    assert first == second


# --------------------------------------------------------------------------- #
# the recorded choice
# --------------------------------------------------------------------------- #


def test_recorded_choice_short_circuits_the_question(project: Path) -> None:
    """C-021: a second install in the same project reads `.ai/tooling.json` and does not ask again.

    Draft §52 found that the ledger had this scenario marked `undesigned` — wrong, and worse than a
    bad note: the behaviour was already implemented *and already tested here*, it just never named
    the scenario. Naming it is the fix; the ledger's `status` is a measurement, not a judgement.
    """

    (project / "requirements.txt").write_text("nothing-relevant\n", encoding="utf-8")
    write_memory(project, "node", SCOPE_DATA_ROOT)

    decision = decide_scope(
        ScopeRequest(capability_id="node", project_root=project),
        memory=read_tooling_memory(project),
    )

    assert decision.origin == "memory"
    assert decision.scope == SCOPE_DATA_ROOT
    assert decision.confirmation_required is False
    assert decision.memory is not None


def test_a_stale_recorded_choice_is_ignored(project: Path) -> None:
    """A stale answer is worse than no answer: the question is asked again."""

    (project / "pyproject.toml").write_text("node\n", encoding="utf-8")
    write_memory(project, "node", SCOPE_DATA_ROOT)
    (project / "pyproject.toml").write_text("node and something new\n", encoding="utf-8")

    decision = decide_scope(
        ScopeRequest(capability_id="node", project_root=project),
        memory=read_tooling_memory(project),
    )

    assert decision.origin != "memory"
    assert decision.scope == SCOPE_PROJECT, "the fresh manifest now declares it"


def test_unreadable_memory_degrades_to_no_memory(project: Path) -> None:
    path = tooling_memory_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert read_tooling_memory(project) is None
    decision = decide_scope(ScopeRequest(capability_id="node", project_root=project), memory=None)
    assert decision.origin != "memory"


def test_memory_file_location_is_project_local(project: Path) -> None:
    assert TOOLING_MEMORY_RELATIVE == ".ai/tooling.json"
    assert tooling_memory_path(project) == project / ".ai" / "tooling.json"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_import_routing_asks_about_the_two_facts_an_import_can_observe() -> None:
    """`adopt --mode import` must ask what `plan` asks, from the facts it actually has (draft §70).

    A named seam rather than an inline call, so the over-threshold trigger is testable without
    writing a 300 MB file — and so "what an import is allowed to know" is one thing to read.
    """

    import inspect

    from airoot.caps.planner import import_scope_decision

    # A generic single-file tool under the threshold: the row import belongs to (§12.1 row 2 —
    # "single-file generic CLI -> the data root, never ask").
    tool = import_scope_decision("archive", source_bytes=1_000)
    assert tool.confirmation_required is False
    assert tool.scope == SCOPE_DATA_ROOT

    # A frozen runtime: high risk, because *where it lives* is a real choice.
    runtime = import_scope_decision("python", source_bytes=1_000)
    assert runtime.confirmation_required is True
    assert runtime.reason_code == "SCOPE_CONFIRMATION_REQUIRED"
    assert any("runtime" in item["detail"] for item in runtime.evidence)

    # Over the threshold, decided with a tiny threshold so the test needs no 300 MB file.
    big = import_scope_decision("archive", source_bytes=11, threshold_bytes=10)
    assert big.confirmation_required is True
    assert any("exceeds" in item["detail"] for item in big.evidence)

    # The seam deliberately exposes no way to *declare* the risk away: the measured size and the
    # frozen kind are the only inputs. If someone later adds a `creates_environment` flag here, this
    # assertion is the place that says why it was left out — the command that copies the payload must
    # not be the command that grades its own risk.
    assert set(inspect.signature(import_scope_decision).parameters) == {
        "capability_id",
        "source_bytes",
        "threshold_bytes",
    }


def test_cli_decides_project_isolation_and_exits_zero(capsys, registry, project: Path) -> None:
    (project / "pyproject.toml").write_text('dependencies = ["python"]\n', encoding="utf-8")

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "decide", "python", "--project", str(project),
    )

    assert code == 0
    assert document["scope"] == SCOPE_PROJECT
    assert document["confirmation_required"] is False


def test_cli_confirmation_required_exits_four(capsys, registry) -> None:
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent), "scope", "decide", "node"
    )

    assert code == 4
    assert document["reason_code"] == "SCOPE_CONFIRMATION_REQUIRED"
    assert document["confirmation_required"] is True
    assert document["options"] == list(CONFIRMATION_OPTIONS)


def test_cli_a_generic_tool_does_not_ask(capsys, registry) -> None:
    """The other half of §12.1: the obvious case must not ask, or asking stops meaning anything."""

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent), "scope", "decide", "archive"
    )

    assert code == 0
    assert document["scope"] == SCOPE_DATA_ROOT
    assert document["confirmation_required"] is False
    assert document["options"] == []


def test_cli_unknown_capability_exits_nine(capsys, registry) -> None:
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "decide", "definitely-not-a-capability",
    )

    assert code == 9
    assert document["reason_code"] == "CAPABILITY_NOT_DECLARED"


def test_cli_reports_unknown_size_honestly(capsys, registry) -> None:
    _code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent), "scope", "decide", "node"
    )
    assert document["size_estimate_bytes"] is None
    assert document["size_source"] == "unknown"


def test_cli_accepts_a_declared_size(capsys, registry) -> None:
    _code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "decide", "archive", "--size-bytes", str(DEFAULT_SIZE_THRESHOLD_BYTES * 2),
    )
    assert document["size_estimate_bytes"] == DEFAULT_SIZE_THRESHOLD_BYTES * 2
    assert document["size_source"] == "declared"
    assert document["confirmation_required"] is True


def test_cli_memory_is_read_only_and_reports_the_fingerprint(capsys, registry, project: Path) -> None:
    (project / "package.json").write_text("{}", encoding="utf-8")
    write_memory(project, "node", SCOPE_DATA_ROOT)

    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "memory", "--project", str(project),
    )

    assert code == 0
    assert document["present"] is True
    assert document["writable"] is False, "writing the memory belongs to the approval channel"
    assert document["memory"]["choices"]["node"]["scope"] == SCOPE_DATA_ROOT


def test_cli_memory_absent_is_not_an_error(capsys, registry, project: Path) -> None:
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "memory", "--project", str(project),
    )
    assert code == 0
    assert document["present"] is False
    assert document["manifest_fingerprint"] is None


def test_cli_rejects_a_missing_project_directory(capsys, registry, tmp_path: Path) -> None:
    code, document = run(
        capsys, "--json", "--root", str(Path(registry.path).parent.parent),
        "scope", "decide", "node", "--project", str(tmp_path / "absent"),
    )
    assert code == 8
    assert document["reason_code"] in {"INVALID_INPUT", "ROOT_NOT_RESOLVED"}
