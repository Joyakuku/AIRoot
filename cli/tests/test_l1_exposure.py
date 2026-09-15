"""Environment exposure for references (draft §13, schema ``reference-plan``).

The invariants under test are the ones that keep "set an environment variable" from
becoming an unaccountable machine-wide change:

* a persisted value points inside a **registered data root** — never at a shim, never
  at something AIROOT owns;
* the previous value is recorded before the write, so ``env forget`` is exact;
* injection-type variables (``PYTHONPATH``, ``NODE_OPTIONS``, …) are refused, in the
  code *and* in the published schema;
* the machine scope needs the P2 broker and reports ``PRIVILEGE_REQUIRED``.

Nothing here writes to the host: the environment store is always injected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from airoot import SCHEMA_DIR
from airoot.canon import plan_hash
from airoot.caps.environment import (
    FORBIDDEN_PERSISTENT_VARIABLES,
    InMemoryEnvironmentStore,
    EnvironmentSpec,
)
from airoot.caps.exposure import (
    ExposureRequest,
    ExposureTarget,
    SCOPE_MACHINE,
    apply_reference_plan,
    build_reference_plan,
    forget_reference_persist,
    request_from_entry,
    resolve_exposure,
)
from airoot.exits import AirootError
from airoot.registry import ExternalReference
from airoot.registry.entities import DataRoot, environment_persist_from_row
from airoot.paths import volume_serial
from airoot.schema_io import errors_for


JAVA_ENTRY = {
    "variables": {"JAVA_HOME": "<entrypoint_dir_parent>"},
    "path_prepend": [],
}

# A container-style declaration: the variable points at the object root and the PATH entry
# is a declared subdirectory. Both forms must stay expressible (draft §13.2).
CONTAINER_ENTRY = {"variables": {"CONTAINER_ROOT": "<object_root>"}, "path_prepend": ["bin"]}

# A capability whose environment is a plain literal — the value is not a path, so it
# must not be forced inside a data root.
LITERAL_ENTRY = {"variables": {"AIROOT_TEST_MODE": "1"}, "path_prepend": []}


@pytest.fixture
def data_root_dir(tests_tmp: Path) -> Path:
    path = tests_tmp / "exposure-data-root"
    (path / "java" / "bin").mkdir(parents=True, exist_ok=True)
    (path / "java" / "bin" / "java.exe").write_bytes(b"MZ-placeholder-never-executed\n")
    return path


@pytest.fixture
def registered(registry, data_root_dir: Path) -> Path:
    """A data root plus one reference to ``java`` inside it."""

    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root_dir),
                role="runtime",
                volume_serial=volume_serial(data_root_dir),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-3",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/java",
                capability_id="java",
                path=str(data_root_dir / "java"),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="25.0.2.0",
                architecture="x64",
                entrypoints=("bin/java.exe",),
                probe_level=2,
                source_kind="pe_static",
            ),
        )
    return data_root_dir / "java"


def make_target(object_root: Path, *, capability_id: str = "java", entrypoint: str = "bin/java.exe"):
    return ExposureTarget(
        external_id=f"external/dr-env/{capability_id}",
        capability_id=capability_id,
        path=object_root,
        object_root=object_root,
        entrypoint_dir=object_root / Path(entrypoint).parent,
    )


def make_request(
    object_root: Path,
    *,
    entry: dict | None = JAVA_ENTRY,
    data_roots: tuple[Path, ...] | None = None,
    capability_id: str = "java",
    scope: str = "user",
):
    if data_roots is None:
        data_roots = (object_root.parent,)
    return request_from_entry(
        target=make_target(object_root, capability_id=capability_id),
        entry=entry,
        scope=scope,
        data_roots=data_roots,
    )


# --------------------------------------------------------------------------- #
# declared environment -> values
# --------------------------------------------------------------------------- #


def test_declared_environment_expands_to_paths_inside_the_data_root(registered: Path) -> None:
    resolved = resolve_exposure(make_request(registered))

    # The entrypoint is `bin/java.exe`, so its home is the object root and the PATH entry
    # is the directory the entrypoint was actually found in.
    assert resolved.variables["JAVA_HOME"] == str(registered)
    assert resolved.path_entries == (registered / "bin",)
    assert resolved.value_kind == "REG_EXPAND_SZ"


def test_a_versioned_layout_points_at_the_version_directory(tests_tmp: Path) -> None:
    """Real ``D:\\env\\Java`` holds ``jdk-25.0.2/bin/java.exe``: the home is the *version* dir.

    Declaring ``JAVA_HOME=<object_root>`` would have set the container as the JDK home, which
    is not a JDK home at all — found by running the acceptance path against the real data root.
    """

    data_root = tests_tmp / "exposure-versioned"
    object_root = data_root / "Java"
    entrypoint = object_root / "jdk-25.0.2" / "bin" / "java.exe"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_bytes(b"MZ-placeholder-never-executed\n")

    target = ExposureTarget(
        external_id="external/dr-env/java",
        capability_id="java",
        path=object_root,
        object_root=object_root,
        entrypoint_dir=entrypoint.parent,
    )
    request = request_from_entry(target=target, entry=JAVA_ENTRY, data_roots=(data_root,))
    resolved = resolve_exposure(request)

    assert resolved.variables["JAVA_HOME"] == str(object_root / "jdk-25.0.2")
    assert resolved.path_entries == (object_root / "jdk-25.0.2" / "bin",)


def test_a_container_declaration_still_works(registered: Path) -> None:
    resolved = resolve_exposure(
        make_request(registered, entry=CONTAINER_ENTRY, capability_id="container")
    )

    assert resolved.variables["CONTAINER_ROOT"] == str(registered)
    assert resolved.path_entries == (registered / "bin",)


def test_a_literal_value_is_not_forced_inside_a_data_root(registered: Path) -> None:
    """Only path-shaped values are location-checked; a flag like ``1`` has no location."""

    resolved = resolve_exposure(
        make_request(registered, entry=LITERAL_ENTRY, capability_id="airoot-test")
    )
    assert resolved.variables == {"AIROOT_TEST_MODE": "1"}
    assert resolved.path_entries == (registered / "bin",)


def test_persisting_outside_every_data_root_is_refused(registered: Path, tests_tmp: Path) -> None:
    outside = tests_tmp / "not-a-data-root"
    outside.mkdir(exist_ok=True)

    with pytest.raises(AirootError) as caught:
        resolve_exposure(make_request(registered, data_roots=(outside,)))
    assert caught.value.reason_code == "PERSISTENCE_TARGET_FORBIDDEN"


def test_no_registered_data_root_means_no_persistence(registered: Path) -> None:
    with pytest.raises(AirootError) as caught:
        resolve_exposure(make_request(registered, data_roots=()))
    assert caught.value.reason_code == "DATA_ROOT_MISSING"


def test_an_unknown_template_is_refused_not_left_literal(registered: Path) -> None:
    """An unknown token would survive into the value and point at a path that never exists."""

    entry = {"variables": {"JAVA_HOME": "<object_rootd>\\jdk"}, "path_prepend": []}
    with pytest.raises(AirootError) as caught:
        resolve_exposure(make_request(registered, entry=entry))
    assert caught.value.reason_code == "INVALID_INPUT"
    assert "<object_rootd>" in caught.value.message
    assert "<entrypoint_dir_parent>" in caught.value.evidence[0]


def test_a_forbidden_variable_is_refused_by_the_code(registered: Path) -> None:
    entry = {"variables": {"PYTHONPATH": "<object_root>"}, "path_prepend": []}
    with pytest.raises(AirootError) as caught:
        resolve_exposure(make_request(registered, entry=entry))
    assert caught.value.reason_code == "PERSISTENCE_TARGET_FORBIDDEN"


# --------------------------------------------------------------------------- #
# The two §13.2 hard rules that draft §53 found implemented-but-unwatched.
# --------------------------------------------------------------------------- #

REPO = Path(__file__).resolve().parents[2]


def test_C026_a_value_that_could_inject_a_statement_is_refused(
    registered: Path, tests_tmp: Path
) -> None:
    """C-026: a persisted value carrying a newline or an ambiguous `%` is refused, exit 8.

    Draft §53 audited the forty-one scenarios the ledger called "nothing is missing" and found this
    rule **implemented and completely untested**: `caps/environment.py::validate_value` refuses four
    shapes, and nothing in the tree had ever built a value containing a newline. The ledger carried it
    as `unchecked-invariant` — test debt, not a missing capability — and §55 is the stage that pays it.

    The assertions are about *why* each value is refused, not merely that it is. All four shapes share
    the reason code `PERSISTENCE_TARGET_FORBIDDEN` with the unrelated "outside every data root" rule,
    so a reason code alone would also be satisfied by an implementation that refused everything — and
    a rule that refuses everything protects nothing.
    """

    def refusal(
        object_root: Path,
        variables: dict[str, str],
        *,
        kind: str = "REG_EXPAND_SZ",
        data_roots: tuple[Path, ...] | None = None,
    ) -> AirootError:
        entry = {"variables": variables, "path_prepend": [], "value_kind": kind}
        with pytest.raises(AirootError) as caught:
            resolve_exposure(make_request(object_root, entry=entry, data_roots=data_roots))
        return caught.value

    # (1) a newline written into the declaration itself ...
    declared = refusal(registered, {"JAVA_HOME": "<object_root>\nset EVIL=1"})
    assert declared.reason_code == "PERSISTENCE_TARGET_FORBIDDEN"
    assert declared.exit_code == 8
    assert "newline" in declared.message, declared.message

    # (2) ... and a newline arriving through a **path**, which is the vector that actually matters:
    # the declaration is shipped data, a path is the user's. Measured while writing this: Win32 will
    # not create a directory whose name contains a newline (`WinError 123`), so on this filesystem the
    # vector is a *path string* rather than a directory that exists. The assertion still means
    # something precise, because `validate_value` runs **before** the containment check — the refusal
    # below can only be about the newline. If a future filesystem does allow the name, the `else`
    # branch uses the real directory and the case gets strictly stronger.
    hostile_root = tests_tmp / "exposure-newline-root"
    hostile_root.mkdir(parents=True, exist_ok=True)
    real_directory = hostile_root / "jdk\nset EVIL=1"
    try:
        real_directory.mkdir()
    except OSError:
        object_root = Path(f"{hostile_root}\\jdk\nset EVIL=1")
    else:
        object_root = real_directory
    through_path = refusal(object_root, {"JAVA_HOME": "<object_root>"}, data_roots=(hostile_root,))
    assert through_path.reason_code == "PERSISTENCE_TARGET_FORBIDDEN"
    assert through_path.exit_code == 8
    assert "newline" in through_path.message, through_path.message

    # (3) an unpaired quote: half a quoted string is how a value escapes into the next token.
    quoted = refusal(registered, {"AIROOT_TEST_MODE": 'a"b'})
    assert "unpaired quote" in quoted.message, quoted.message

    # (4) `%...%` under REG_SZ, which would freeze the expansion as a literal.
    literal = refusal(registered, {"AIROOT_TEST_MODE": "%SystemRoot%"}, kind="REG_SZ")
    assert "REG_SZ" in literal.message and "literally" in literal.message, literal.message

    # (5) an ambiguous expansion: `100%` has a `%` that pairs with nothing.
    ambiguous = refusal(registered, {"AIROOT_TEST_MODE": "100%"})
    assert "ambiguous" in ambiguous.message, ambiguous.message

    # The negative control, and the reason this test is worth having: `%VAR%` is *legitimate* under
    # REG_EXPAND_SZ. Without this case, (4) above would also be satisfied by "refuse any value with a
    # percent sign" — which is not the rule and would break every real declaration.
    resolved = resolve_exposure(
        make_request(registered, entry={"variables": {"AIROOT_TEST_MODE": "%SystemRoot%"}, "path_prepend": []})
    )
    assert resolved.variables["AIROOT_TEST_MODE"] == "%SystemRoot%"


def test_C024_persisting_a_pointer_at_the_exposure_shim_is_refused(registered: Path) -> None:
    """C-024: a persisted value may not point at `AIROOT\\cli\\exposure\\bin`, exit 8.

    Draft §53 found that **no test had ever persisted a value at the shim path** — the ledger's
    pointer named a test about a *different* rule that happens to share the reason code, and the only
    two `shim` occurrences in the tree were in docstrings. §13.2 is one of the three hard rules the
    steward model promises, and this is the one that keeps AIROOT's removal from leaving dangling
    pointers: the shim lives inside the CLI root, which is never a data root, so it goes away with
    AIROOT and anything pointing at it would rot.
    """

    shim = REPO / "cli" / "exposure" / "bin"

    # Route 1 — as a **value**. Refused by §13.2's own rule: a persisted pointer must live inside a
    # registered data root, and this one is inside the CLI root.
    with pytest.raises(AirootError) as caught:
        resolve_exposure(
            make_request(registered, entry={"variables": {"JAVA_HOME": str(shim)}, "path_prepend": []})
        )
    assert caught.value.reason_code == "PERSISTENCE_TARGET_FORBIDDEN"
    assert caught.value.exit_code == 8
    assert str(shim) in caught.value.message, caught.value.message
    assert "data root" in caught.value.message, caught.value.message

    # Route 2 — as a `path_prepend` climbing out of the object root. A **different** rule refuses it
    # (a declaration may not use an absolute path or `..`), so it carries a different code. Recorded
    # as its own route rather than folded into route 1: merging two rules under one assertion is how
    # a suite starts reporting a refusal without knowing which rule produced it.
    with pytest.raises(AirootError) as climbed:
        resolve_exposure(
            make_request(
                registered,
                entry={"variables": {}, "path_prepend": ["../../../cli/exposure/bin"]},
            )
        )
    assert climbed.value.reason_code == "INVALID_INPUT"
    assert "non-relative" in climbed.value.message, climbed.value.message


def test_machine_scope_needs_the_protected_broker(registry, registered: Path) -> None:
    request = make_request(registered, scope=SCOPE_MACHINE)
    with pytest.raises(AirootError) as caught:
        build_reference_plan(request, registry=registry)
    assert caught.value.reason_code == "PRIVILEGE_REQUIRED"


# --------------------------------------------------------------------------- #
# the published schema and the code must not drift (S9.1)
# --------------------------------------------------------------------------- #


def _schema_forbidden_variables() -> set[str]:
    document = json.loads((SCHEMA_DIR / "reference-plan.schema.json").read_text(encoding="utf-8"))
    return set(
        document["properties"]["exposure"]["properties"]["variables"]["propertyNames"]["not"]["enum"]
    )


def test_schema_forbidden_list_matches_the_code() -> None:
    assert _schema_forbidden_variables() == set(FORBIDDEN_PERSISTENT_VARIABLES)


def test_schema_rejects_a_pythonpath_exposure(registry, registered: Path) -> None:
    plan = build_reference_plan(make_request(registered), registry=registry)
    plan["exposure"]["variables"]["PYTHONPATH"] = "C:\\somewhere"
    plan["plan_hash"] = plan_hash(plan)

    problems = errors_for("reference-plan", plan)
    assert any("PYTHONPATH" in problem for problem in problems), problems


# --------------------------------------------------------------------------- #
# plan -> apply -> forget
# --------------------------------------------------------------------------- #


def build_plan(registry, registered: Path, **kwargs):
    return build_reference_plan(make_request(registered, **kwargs), registry=registry)


def test_plan_is_schema_valid_and_reversible(registry, registered: Path) -> None:
    plan = build_plan(registry, registered)

    assert plan["operation"] == "record_reference_exposure"
    assert plan["exposure"]["scope"] == "user"
    assert plan["plan_hash"] == plan_hash(plan)
    assert errors_for("reference-plan", plan) == []
    assert all(step["reversible"] for step in plan["operations"])
    assert "writes_path" in plan["side_effects"]


def test_apply_then_forget_restores_the_exact_previous_value(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    store.write("JAVA_HOME", "C:\\old-java", "REG_EXPAND_SZ")
    plan = build_plan(registry, registered)

    result = apply_reference_plan(plan, registry=registry, store=store, approval_id="approval-1")
    assert result.variables and "JAVA_HOME" in result.variables
    assert store.read("JAVA_HOME").value == str(registered)

    records = [environment_persist_from_row(row) for row in registry.environment_persist_records()]
    assert [record.variable for record in records] == ["JAVA_HOME", "Path"]
    java_home = next(record for record in records if record.variable == "JAVA_HOME")
    assert java_home.old_value == "C:\\old-java"
    assert java_home.had_previous_value
    assert java_home.plan_hash == plan["plan_hash"]
    assert java_home.approval_id == "approval-1"

    forgotten = forget_reference_persist(registry=registry, capability_id="java", store=store)
    assert "JAVA_HOME" in forgotten.restored
    assert store.read("JAVA_HOME").value == "C:\\old-java"
    assert registry.environment_persist_records(active_only=True) == []
    assert len(registry.environment_persist_records(active_only=False)) == 2


def test_forget_removes_a_variable_that_did_not_exist_before(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    apply_reference_plan(build_plan(registry, registered), registry=registry, store=store)
    assert store.read("JAVA_HOME") is not None

    forget_reference_persist(registry=registry, capability_id="java", store=store)
    assert store.read("JAVA_HOME") is None


def test_forget_is_surgical_for_path(registry, registered: Path) -> None:
    """A path another tool added after us must survive ``env forget``."""

    store = InMemoryEnvironmentStore()
    store.write("Path", "C:\\Windows;C:\\existing", "REG_EXPAND_SZ")
    apply_reference_plan(build_plan(registry, registered), registry=registry, store=store)

    after_apply = store.read("Path").value
    assert after_apply.endswith("C:\\Windows;C:\\existing")
    store.write("Path", after_apply + ";C:\\added-later", "REG_EXPAND_SZ")

    forget_reference_persist(registry=registry, capability_id="java", store=store)
    remaining = store.read("Path").value
    assert str(registered / "bin") not in remaining
    assert "C:\\added-later" in remaining
    assert "C:\\existing" in remaining


def test_an_external_edit_is_reported_as_drift_not_overwritten(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    store.write("JAVA_HOME", "C:\\old-java", "REG_EXPAND_SZ")
    apply_reference_plan(build_plan(registry, registered), registry=registry, store=store)

    store.write("JAVA_HOME", "D:\\someone-elses-java", "REG_EXPAND_SZ")
    result = forget_reference_persist(registry=registry, capability_id="java", store=store)

    assert result.drifted == ["JAVA_HOME"]
    # The recorded previous value still wins: forget is a restore, not a merge.
    assert store.read("JAVA_HOME").value == "C:\\old-java"


def test_forgetting_something_never_persisted_is_refused(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    with pytest.raises(AirootError) as caught:
        forget_reference_persist(registry=registry, capability_id="java", store=store)
    assert caught.value.reason_code == "ENVIRONMENT_PERSIST_NOT_FOUND"


def test_a_tampered_plan_cannot_be_applied(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    plan = build_plan(registry, registered)
    plan["exposure"]["variables"]["JAVA_HOME"] = "C:\\somewhere-else"

    with pytest.raises(AirootError) as caught:
        apply_reference_plan(plan, registry=registry, store=store)
    assert caught.value.reason_code == "INVALID_INPUT"
    assert store.writes == []


def test_apply_is_recorded_in_the_event_log(registry, registered: Path) -> None:
    store = InMemoryEnvironmentStore()
    apply_reference_plan(build_plan(registry, registered), registry=registry, store=store)

    states = [row["state"] for row in registry.events()]
    assert "EXPOSED" in states


def test_spec_value_kind_is_validated(registry, registered: Path) -> None:
    bogus = EnvironmentSpec(
        capability_id="java", variables=(("JAVA_HOME", "<object_root>"),), value_kind="REG_DWORD"
    )
    request = ExposureRequest(target=make_target(registered), spec=bogus, data_roots=(registered.parent,))
    with pytest.raises(AirootError) as caught:
        resolve_exposure(request)
    assert caught.value.reason_code == "INVALID_INPUT"


# --------------------------------------------------------------------------- #
# the real Windows store, on a throwaway key
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only product")
def test_windows_environment_store_round_trip_on_a_throwaway_key(registry, registered: Path) -> None:
    """The only sanctioned registry write in the suite, and it is undone and asserted.

    ``HKCU\\Environment`` itself is never touched — the store is pointed at a random
    subkey that this test creates and then deletes.
    """

    import winreg

    from airoot.caps.environment import WindowsEnvironmentStore

    subkey = f"Software\\AIROOT-Test-{uuid4().hex[:12]}"
    winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE).Close()
    try:
        store = WindowsEnvironmentStore(subkey=subkey)
        store.write("JAVA_HOME", "C:\\old-java", "REG_EXPAND_SZ")
        assert store.read("JAVA_HOME").kind == "REG_EXPAND_SZ"

        plan = build_plan(registry, registered)
        apply_reference_plan(plan, registry=registry, store=store)
        assert store.read("JAVA_HOME").value == str(registered)
        assert any(name.upper() == "PATH" for name in store.names())

        forget_reference_persist(registry=registry, capability_id="java", store=store)
        assert store.read("JAVA_HOME").value == "C:\\old-java"
        assert store.read("JAVA_HOME").kind == "REG_EXPAND_SZ"
    finally:
        # Assert the key really is gone: a leaked test key would poison later runs.
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey)
