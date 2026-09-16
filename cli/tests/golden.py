"""Golden fixtures: the language-neutral acceptance corpus for the Rust port.

Every document here is produced by the P1 Python core, validated against the
published schema, and paired with the exit code the command must return. A Rust
implementation is expected to reproduce these byte for byte.

Run ``python cli/tests/golden.py`` to regenerate after an intentional change.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from airoot.caps.boundary import load_capabilities
from airoot.caps.doctor import INVARIANTS, doctor, status_exit_code
from airoot.caps.discovery import discover_data_root
from airoot.caps.exposure import ExposureTarget, build_reference_plan, request_from_entry
from airoot.caps.inventory import inventory
from airoot.caps.where import WhereQuery, where
from airoot.clock import FakeClock
from airoot.exits import REASON_EXIT, exit_code_for
from airoot.ext.fake import load_fake_extension
from airoot.registry import Registry, build_projection
from airoot.registry.entities import Binding, DataRoot, ExternalReference, Instance, binding_key
from airoot.root import init_root
from airoot.tx import create_plan
from airoot.tx.simulate import PAYLOAD_NAME, SimulationRunner
from airoot.tx.states import (
    ACTIVE_BINDING_COMMIT_STATE,
    HAPPY_PATH,
    PAYLOAD_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
)
from scenario_ledger import build_ledger
from execution_bounds import build_bounds

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
#: The line ending every fixture is stored with, on every platform (draft §84). CRLF because that is
#: what the committed corpus already is: `write_text` translated on Windows and nowhere else, so the
#: bytes in git were an accident of the machine that generated them. Pinning it turns that accident
#: into a decision, and `.gitattributes` (`* -text`) keeps git from rewriting it on checkout.
NEWLINE = "\r\n"
#: Repository root, needed by the catalogues that are derived from the documents rather than
#: from a simulated root (``scenario_ledger``, draft §49).
REPO = Path(__file__).resolve().parents[2]
CAPABILITY = "fake-tool"
MACHINE_ID = "host-0123456789abcdef0123456789abcdef"
ROOT_INSTANCE_ID = "root-4f2a9c1d8e3b"


def _instance(instance_id: str, *, digest: str, version: str, health: str = "healthy") -> Instance:
    payload = {
        "schema_version": 1,
        "tool_id": CAPABILITY,
        "instance_id": instance_id,
        "kind": "managed_tool",
        "capability_id": CAPABILITY,
        "version": version,
        "platform": "windows",
        "architecture": "x64",
        "artifact_digest": digest,
        "install_backend_id": "fake_fixture",
        "store_path": f"store/{instance_id}",
        "file_manifest_digest": "sha256:" + "b" * 64,
        "lifecycle_status": "active",
        "health": health,
        "entrypoints": [PAYLOAD_NAME],
        "bindings": [],
        "source": {
            "kind": "generated_fixture",
            "locator": f"cache/fixtures/{CAPABILITY}/{version}",
            "provenance": {"source_id": "fixture/fake-tool-v1", "publisher": "airoot-test"},
            "integrity": {"artifact_digest": digest, "file_manifest_digest": "sha256:" + "b" * 64},
            "signature": None,
        },
        "created_at": "2024-01-01T00:00:00Z",
    }
    return Instance(
        instance_id=instance_id,
        kind="managed_tool",
        capability_id=CAPABILITY,
        version=version,
        platform="windows",
        architecture="x64",
        install_backend_id="fake_fixture",
        artifact_digest=digest,
        store_path=f"store/{instance_id}",
        lifecycle_status="active",
        health=health,
        tool_id=CAPABILITY,
        file_manifest_digest="sha256:" + "b" * 64,
        entrypoints=(PAYLOAD_NAME,),
        payload=payload,
        created_at="2024-01-01T00:00:00Z",
    )


def _build_root(base: Path) -> tuple[Any, Registry, FakeClock]:
    root = init_root(
        base / "root",
        root_instance_id=ROOT_INSTANCE_ID,
        machine_id=MACHINE_ID,
        clock=FakeClock(start="2024-01-01T00:00:00Z"),
    )
    clock = FakeClock(start="2024-01-01T00:00:00Z")
    registry = Registry.initialize(
        root.path,
        machine_id=MACHINE_ID,
        root_instance_id=ROOT_INSTANCE_ID,
        clock=clock,
    )
    return root, registry, clock


def _normalize(value: Any, base: Path) -> Any:
    """Replace the throwaway root prefix so fixtures are byte-stable across runs."""

    if isinstance(value, dict):
        return {key: _normalize(item, base) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, base) for item in value]
    if isinstance(value, str):
        text = value.replace(str(base), "<BASE>")
        return text.replace(str(base).replace("\\", "/"), "<BASE>")
    return value


def build_documents(base: Path) -> dict[str, dict[str, Any]]:
    """Every golden document, keyed by fixture name, normalised against ``base``.

    ``base`` must be resolved first: the core canonicalises every path, so an 8.3
    short path (as ``tempfile`` may return) would not match anything.
    """

    base = Path(base).resolve()
    raw = _build_documents(base)
    return {
        name: {"document": _normalize(payload["document"], base), "exit_code": payload["exit_code"]}
        for name, payload in raw.items()
    }


def _build_documents(base: Path) -> dict[str, dict[str, Any]]:

    documents: dict[str, dict[str, Any]] = {}
    key = binding_key(CAPABILITY, "machine")

    # ---- where: not found -------------------------------------------------- #
    root, registry, clock = _build_root(base / "empty")
    documents["where_not_found"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("NOT_FOUND"),
    }
    registry.close()

    # ---- where: healthy, version unsatisfied, stale process ---------------- #
    root, registry, clock = _build_root(base / "catalogue")
    digest = "sha256:" + "a" * 64
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, _instance("fake-tool/fake-tool/1.0.0/win-x64", digest=digest, version="1.0.0"))
        registry.bind_active(connection, Binding(key, "fake-tool/fake-tool/1.0.0/win-x64", "machine", "R", "stable_launcher", 1, True))
    store_dir = str(root.path / "store" / "fake-tool/fake-tool/1.0.0/win-x64")

    documents["where_healthy"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[store_dir], machine_entries=[store_dir], user_entries=[]),
        "exit_code": 0,
    }
    documents["where_current_process_stale"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[], machine_entries=[store_dir], user_entries=[]),
        "exit_code": exit_code_for("CURRENT_PROCESS_ENV_OLD"),
    }
    documents["where_version_unsatisfied"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY, version=">=2.0"), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("VERSION_UNSATISFIED"),
    }

    # ---- where: broken managed, nothing else to answer with (§87) ---------- #
    # The plan's §8 list asks for one fixture per `where` *result*. `broken` (exit 3) is a different
    # result from the degradation below it, and had no fixture: the corpus only had the case where a
    # healthy reference answers instead.
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, "fake-tool/fake-tool/1.0.0/win-x64", health="broken")
    documents["where_broken"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("BROKEN"),
    }

    # ---- where: broken managed plus healthy external ----------------------- #
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/tools-fake-tool', ?, ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / PAYLOAD_NAME)),
        )
    documents["where_owned_broken_degrades_to_reference"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("CURRENT_SOURCE_DEGRADED"),
    }
    documents["where_deprecated_external_fallback_is_ignored"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY, allow_external_fallback=True), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("CURRENT_SOURCE_DEGRADED"),
    }
    registry.close()

    # ---- where: unmanaged candidates only (§87) ---------------------------- #
    # `UNMANAGED_ONLY` is a real selection reason (`caps/where.py`) and had no fixture. A row whose
    # management is not `external_reference` gets no steward slot, so it lands in the `external` list
    # without ever competing: the answer is "found nothing, but here is what I saw".
    root, registry, clock = _build_root(base / "unmanaged_only")
    with registry.write(expected_generation=0, bump=True) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('unmanaged/tools-fake-tool', ?, ?, 'unmanaged', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (CAPABILITY, str(root.path / "tools" / PAYLOAD_NAME)),
        )
    documents["where_unmanaged_only"] = {
        "document": where(registry, WhereQuery(capability_id=CAPABILITY), root=root.path,
                          process_entries=[], machine_entries=[], user_entries=[]),
        "exit_code": exit_code_for("NOT_FOUND"),
    }
    registry.close()

    # ---- doctor: healthy, degraded, broken, recovery_required -------------- #
    root, registry, clock = _build_root(base / "doctor_healthy")
    # A fresh root has no `state/registry.json` projection, so `doctor` reports REGISTRY_PROJECTION_STALE
    # and the run is **degraded**. Until §88 this fixture stored that degraded document under the name
    # `doctor_healthy` with an index entry of 0 — a fixture whose recorded exit code contradicted its
    # own bytes, and the reason `healthy` was the one status the plan's §14 could not point at.
    registry.update_projection()
    documents["doctor_healthy"] = {
        "document": doctor(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z"), registry=registry),
        "exit_code": status_exit_code("healthy"),
    }
    with registry.write(expected_generation=registry.generation, bump=True):
        pass
    documents["doctor_degraded_stale_projection"] = {
        "document": doctor(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z"), registry=registry),
        "exit_code": status_exit_code("degraded"),
    }
    registry.close()

    root, registry, clock = _build_root(base / "doctor_broken")
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, _instance("fake-tool/fake-tool/9.0.0/win-x64", digest=digest, version="9.0.0"))
    documents["doctor_broken_missing_payload"] = {
        "document": doctor(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z"), registry=registry),
        "exit_code": status_exit_code("broken"),
    }
    registry.close()

    root, registry, clock = _build_root(base / "doctor_recovery")
    (root.path / "state" / "root.json").unlink()
    documents["doctor_recovery_required"] = {
        "document": doctor(root.path, clock=FakeClock(start="2024-01-01T00:00:00Z")),
        "exit_code": status_exit_code("recovery_required"),
    }

    # ---- doctor with a stale search index (draft §33) ---------------------- #
    # The four `doctor_*` fixtures above must stay byte-identical: a machine that never searched has
    # nothing to report about a search index. This one exists to pin the opposite case.
    from airoot.caps import searchindex
    from airoot.caps.search import load_search_policy

    root, registry, clock = _build_root(base / "doctor_stale_search_index")
    search_tree = base / "doctor_stale_search_index" / "search-root"
    search_tree.mkdir(parents=True, exist_ok=True)
    (search_tree / "python.exe").write_bytes(b"MZ-golden\n")
    searchindex.build_index(
        root.path, roots=[str(search_tree)], policy=load_search_policy(), clock=clock
    )
    clock.advance(timedelta(hours=48))
    documents["doctor_stale_search_index"] = {
        "document": doctor(root.path, clock=clock, registry=registry),
        "exit_code": status_exit_code("degraded"),
    }
    registry.close()
    registry.close()

    # ---- inventory and the extension envelope ------------------------------ #
    root, registry, clock = _build_root(base / "inventory")
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_instance(connection, _instance("fake-tool/fake-tool/1.0.0/win-x64", digest=digest, version="1.0.0"))
        registry.bind_active(connection, Binding(key, "fake-tool/fake-tool/1.0.0/win-x64", "machine", "R", "stable_launcher", 1, True))
    documents["inventory_machine"] = {
        "document": inventory(registry, scope="machine"),
        "exit_code": 0,
    }
    documents["extension_probe_envelope"] = {
        "document": load_fake_extension(clock=FakeClock(start="2024-01-01T00:00:00Z")).run("probe"),
        "exit_code": 0,
    }
    registry.close()

    # ---- a committed transaction journal ---------------------------------- #
    root, registry, clock = _build_root(base / "transaction")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fake_issuer  # noqa: E402  (test-only issuer)

    fake_issuer.install_keyring(root.path)
    plan = create_plan(
        registry,
        version="1.0.0",
        clock=clock,
        plan_id="plan/fake-tool/1.0.0/golden-fixture",
    )
    token = fake_issuer.issue(plan, clock=clock, nonce="a" * 32, approval_id="approval/golden-fixture")
    tx = SimulationRunner(registry, clock=clock).commit(plan, token)
    documents["transaction_finalized"] = {"document": tx, "exit_code": 0}

    # ---- the two documents the transaction engine is about (draft §90) ----- #
    # Every document the core prints is self-validated first (`schema_io.validate_self`), and §90 found
    # that two of the eight had **no fixture at all**: the plan — what gets approved and executed — and
    # the managed instance payload — what gets registered and bound. They are recorded here rather than
    # in a block of their own because this is where both already exist.
    documents["plan_fake_tool"] = {"document": plan, "exit_code": 0}
    documents["managed_tool_instance"] = {
        "document": _instance("fake-tool/fake-tool/1.0.0/win-x64", digest=digest, version="1.0.0").payload,
        "exit_code": 0,
    }
    registry.close()

    # ---- steward domain: a data root plus a non-owning reference ----------- #
    root, registry, clock = _build_root(base / "steward")
    data_root_path = base / "steward" / "env"
    data_root_path.mkdir(parents=True, exist_ok=True)
    (data_root_path / "python").mkdir(exist_ok=True)
    (data_root_path / "python" / "python.exe").write_bytes(b"MZ-placeholder-for-golden")
    with registry.write(expected_generation=0, bump=True) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root_path),
                role="runtime",
                volume_serial="deadbeef",
                whitelist_revision="wl-4",
                added_at="2024-01-01T00:00:00Z",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/python",
                capability_id="python",
                path=str(data_root_path / "python"),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="3.11.11",
                architecture="x64",
                entrypoints=("python.exe",),
                observed_digest="sha256:" + "c" * 64,
                probe_level=2,
                source_kind="pe_static",
                evidence=(
                    {"kind": "pe_static", "detail": "ProductName=Python; architecture=x64"},
                    {"kind": "discovery", "detail": "entrypoint depth 0 below the object root"},
                ),
            ),
        )
    documents["registry_with_data_root"] = {
        "document": build_projection(registry),
        "exit_code": 0,
    }

    # ---- a reference exposure plan (draft §13.2, schema `reference-plan`) --- #
    # Deliberately built from fixed paths, not from `base`: the plan_hash covers the
    # target path, so a temp-directory root would make the fixture unreproducible.
    fixture_root = Path("D:/env")
    object_root = fixture_root / "python"
    documents["reference_plan"] = {
        "document": build_reference_plan(
            request_from_entry(
                target=ExposureTarget(
                    external_id="external/dr-env/python",
                    capability_id="python",
                    path=object_root,
                    object_root=object_root,
                    entrypoint_dir=object_root,
                ),
                entry={"variables": {"AIROOT_PYTHON_ROOT": "<object_root>"}, "path_prepend": []},
                scope="user",
                data_roots=(fixture_root,),
                requested_by="golden-fixture",
            ),
            registry=registry,
            clock=FakeClock(start="2024-01-01T00:00:00Z"),
            plan_id="plan/reference/python/golden-fixture",
        ),
        "exit_code": 0,
    }
    registry.close()

    # ---- discover report (versions normalised: they come from the host binary) ---- #
    root, registry, clock = _build_root(base / "discover")
    discovered_root = base / "discover" / "env"
    object_dir = discovered_root / "python"
    object_dir.mkdir(parents=True, exist_ok=True)
    import sys as _sys

    shutil.copy2(_sys.executable, object_dir / "python.exe")
    report = discover_data_root(path=discovered_root, data_root_id="dr-env")
    documents["discover_report"] = {
        "document": _normalize_versions(report.to_document()),
        "exit_code": 0,
    }
    registry.close()

    # ---- search profile response (draft §31) ------------------------------- #
    # A crawl over a tree this script creates, so the *shape* is under test rather than the host's
    # contents. File mtimes are host state, like PE versions, so they are normalised away.
    from airoot.caps.search import (
        build_request as build_search_request,
        execute_search,
        load_search_policy,
        resolve_roots,
    )

    search_root = base / "search" / "root"
    search_root.mkdir(parents=True, exist_ok=True)
    (search_root / "python.exe").write_bytes(b"MZ-golden\n")
    (search_root / "notes.txt").write_text("golden\n", encoding="utf-8")
    policy = load_search_policy()
    request = build_search_request(
        "python",
        policy=policy,
        roots=[str(search_root)],
        extensions=[".exe"],
        limit=5,
    )
    roots, _origin = resolve_roots(request["roots"], policy=policy)
    search_document, search_code = execute_search(
        request,
        roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        clock=clock,
        time_source=lambda: 0.0,
    )
    documents["search_response"] = {
        "document": _normalize_search(search_document),
        "exit_code": search_code,
    }

    # ---- search answered from the crawl-built index (draft §32) ------------ #
    # No `refresh` fixture: its `elapsed_ms` is measured with the monotonic clock, which cannot be
    # made deterministic here. The response shapes below cover what the port must reproduce.
    from airoot.caps import searchindex

    index_root = base / "search" / "airoot"
    searchindex.build_index(index_root, roots=[str(search_root)], policy=policy, clock=clock)
    index_state = searchindex.read_state(index_root, policy=policy)
    index_document, index_code = execute_search(
        request,
        roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        clock=clock,
        index_root=index_root,
    )
    documents["search_index_response"] = {
        "document": _normalize_search(index_document),
        "exit_code": index_code,
    }

    stale_request = dict(request)
    stale_request["max_staleness_ms"] = 1000
    clock.advance(timedelta(minutes=5))
    stale_document, stale_code = execute_search(
        stale_request,
        roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        clock=clock,
        index_root=index_root,
    )
    documents["search_stale_index_response"] = {
        "document": _normalize_search(stale_document),
        "exit_code": stale_code,
    }
    assert index_state.readable and index_code == 0 and stale_code == 2

    # ---- search: the crawl exhausts its duration budget (draft §89) -------- #
    # `timed_out` is a real result — `caps/search.py` sets it, with `reason_code=SEARCH_TIMEOUT` and
    # exit 2 — and had no fixture, so `timed_out` was the one search status the plan's §14 could not
    # point at (`error`/`cancelled` have no writer at all and are daggered in
    # `references/field-values.md`).
    #
    # The crawl takes an injectable `time_source`, so this is deterministic rather than a race: the
    # first call fixes the deadline and every later call is already past it, so the loop stops on its
    # first iteration. A timeout answer is partial and carries **no cursor** (search.py:947) — the two
    # facts that make it a different result from `degraded`.
    #
    # **Placed last on purpose.** `execute_search` reads the shared `FakeClock` twice, so one extra
    # call shifts every *later* fixture's timestamps by two seconds — a byte change to fixtures this
    # stage has no business touching. Appending the call keeps the diff to `index.json` plus the new
    # file.
    ticks = iter([0.0] + [10_000.0] * 64)
    timeout_document, timeout_code = execute_search(
        request,
        roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        clock=clock,
        time_source=lambda: next(ticks, 10_000.0),
    )
    documents["search_timeout_response"] = {
        "document": _normalize_search(timeout_document),
        "exit_code": timeout_code,
    }

    # ---- search: the index itself only partly covers the roots (draft §95) -- #
    # `data.freshness.coverage` has three values and one of them — `partial` — had no fixture: it is
    # written when the **index build** was truncated or ran out of time (`searchindex.py`: a build that
    # stopped at its record bound says `partial`, never `complete_for_roots`), which is a different
    # statement from `status`. The bound is data, so it can be injected: one record is enough to stop
    # the build, and the answer then comes from that incomplete index — `status=degraded`,
    # `reason_code=SEARCH_INDEX_DEGRADED`, exit 2, and `next_cursor=null` for the same reason a timed
    # out crawl has none (a page over a partial list would read as page one of a whole one).
    from dataclasses import replace as _replace

    truncating = _replace(policy, index={**policy.index, "max_records": 1})
    partial_root = base / "search" / "partial-index"
    searchindex.build_index(partial_root, roots=[str(search_root)], policy=truncating, clock=clock)
    partial_document, partial_code = execute_search(
        request,
        roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=truncating,
        index_root=partial_root,
        clock=clock,
    )
    documents["search_truncated_index_response"] = {
        "document": _normalize_search(partial_document),
        "exit_code": partial_code,
    }

    # ---- search: the two result values `kind` and `verification` were missing (draft §96) --- #
    # Walking **every** enum `search-response` declares (rather than the two §89/§95 had noticed) shows
    # three result values with neither a fixture nor a dagger: `results[].kind = directory` and
    # `results[].verification = verified` / `changed`. Each is producible, and each is produced by a
    # *different* request field, which is why neither showed up by accident:
    #
    #   * a directory is a result only when the caller asks (`include_directories`); the policy default
    #     is false, so every other fixture has no directory in it;
    #   * `verified`/`changed` are the `physical_verify` verdicts — `verify_records` re-stats each hit
    #     and marks `changed` when it moved, shrank or vanished between the crawl and the check. The
    #     protocol forbids dressing that up as current fact (§6.3), so it is worth a fixture of its own.
    directories_root = base / "search" / "with-directories"
    (directories_root / "nested").mkdir(parents=True, exist_ok=True)
    (directories_root / "python.exe").write_bytes(b"MZ-golden\n")
    directory_request = build_search_request(
        "nested", policy=policy, roots=[str(directories_root)], include_directories=True, limit=5
    )
    directory_roots, _directory_origin = resolve_roots(directory_request["roots"], policy=policy)
    directory_document, directory_code = execute_search(
        directory_request,
        directory_roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        clock=clock,
        time_source=lambda: 0.0,
    )
    documents["search_directories_response"] = {
        "document": _normalize_search(directory_document),
        "exit_code": directory_code,
    }

    # One index, one file rewritten after it: the answer carries the stale hit as `changed` and the
    # untouched one as `verified`, in a single document. Rewriting beats deleting here — a vanished
    # file is dropped by the index query's own accessibility re-check before it can reach the verdict,
    # while a size that no longer matches is exactly what `physical_verify` exists to report.
    verify_root = base / "search" / "verify"
    verify_root.mkdir(parents=True, exist_ok=True)
    (verify_root / "probe-keep.txt").write_text("keep\n", encoding="utf-8")
    rewritten = verify_root / "probe-rewritten.txt"
    rewritten.write_text("small\n", encoding="utf-8")
    verify_index = base / "search" / "verify-index"
    searchindex.build_index(verify_index, roots=[str(verify_root)], policy=policy, clock=clock)
    rewritten.write_text("a different length entirely\n", encoding="utf-8")
    verify_request = build_search_request(
        "probe", policy=policy, roots=[str(verify_root)], consistency="physical_verify", limit=5
    )
    verify_roots, _verify_origin = resolve_roots(verify_request["roots"], policy=policy)
    verify_document, verify_code = execute_search(
        verify_request,
        verify_roots,
        extension_id="airoot-native-search-extension",
        implementation_id="airoot-native-search-crawl",
        policy=policy,
        index_root=verify_index,
        clock=clock,
    )
    documents["search_physical_verify_response"] = {
        "document": _normalize_search(verify_document),
        "exit_code": verify_code,
    }

    # ---- the reason-code table itself ------------------------------------- #
    documents["reason_code_table"] = {
        "document": {
            "schema_version": 1,
            "reason_codes": {code: value for code, value in sorted(REASON_EXIT.items())},
        },
        "exit_code": 0,
    }
    # The legal-move table is part of the frozen contract (core contracts §4.1, ADR-0003) but had no
    # acceptance artifact: the document names the happy path and the exception states, not the
    # edges. Without this fixture a Rust port could reproduce every response and still disagree
    # about which moves are legal. Draft §45.
    documents["transaction_transitions"] = {
        "document": {
            "schema_version": 1,
            "happy_path": list(HAPPY_PATH),
            "terminal_states": sorted(TERMINAL_STATES),
            "payload_states": sorted(PAYLOAD_STATES),
            "active_binding_commit_state": ACTIVE_BINDING_COMMIT_STATE,
            "transitions": {state: list(targets) for state, targets in sorted(TRANSITIONS.items())},
        },
        "exit_code": 0,
    }
    # The D1-D10 catalogue is the diagnostic contract ("which invariant owns which code"), and had
    # no acceptance artifact either. Draft §46.
    documents["invariant_catalogue"] = {
        "document": {
            "schema_version": 1,
            "invariants": {name: list(codes) for name, codes in sorted(INVARIANTS.items())},
        },
        "exit_code": 0,
    }
    # The frozen capability list is the third catalogue the contracts declare (draft §15.2). Draft §47.
    frozen = load_capabilities()
    documents["frozen_capabilities"] = {
        "document": {
            "schema_version": 1,
            "revision": frozen.revision,
            "capabilities": [item.to_document() for item in frozen.capabilities],
        },
        "exit_code": 0,
    }
    # The fourth catalogue: every acceptance-scenario ID the two documents define, with the
    # disposition of the ones no test names. Until §49 the union existed only in a reader's head,
    # so a Rust port had no complete acceptance index. Draft §49.
    ledger = build_ledger(REPO)
    documents["scenario_ledger"] = {
        "document": {
            "schema_version": 1,
            "summary": {
                "total": len(ledger),
                "evidenced": sum(1 for entry in ledger if entry["status"] == "evidenced"),
                "uncited": sum(1 for entry in ledger if entry["status"] == "uncited"),
                "families": {
                    family: sum(1 for entry in ledger if entry["family"] == family)
                    for family in sorted({entry["family"] for entry in ledger})
                },
            },
            "entries": ledger,
        },
        "exit_code": 0,
    }
    # The sixth catalogue: every number that decides a refusal. Draft §46.4 named two of these and
    # deferred them; §50 closes the class. Without it a Rust port could reproduce every response here
    # and still disagree about whether request 2001 is accepted.
    documents["execution_bounds"] = {
        "document": build_bounds(),
        "exit_code": 0,
    }
    return documents


def _normalize_search(value: Any) -> Any:
    """Replace file mtimes, which are host state rather than contract.

    The same reason `_normalize_versions` exists: a golden fixture must be reproducible on another
    machine, and the modification time of a file this script just created is not.
    """

    if isinstance(value, dict):
        return {
            key: ("<MODIFIED_AT>" if key == "modified_at" and item else _normalize_search(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_search(item) for item in value]
    return value


def _normalize_versions(value: Any) -> Any:
    """Replace PE-derived versions so the fixture is not tied to the host interpreter."""

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "version" and isinstance(item, str) and item:
                normalized[key] = "<VERSION>"
            elif key == "versions" and isinstance(item, list):
                normalized[key] = ["<VERSION>" for _ in item]
            else:
                normalized[key] = _normalize_versions(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_versions(item) for item in value]
    return value


def render(document: dict[str, Any]) -> bytes:
    """The exact bytes a fixture is stored as — pinned, not left to the platform's text mode (§84).

    `Path.write_text` translates `\\n` to `\\r\\n` on Windows and to nothing anywhere else, so the
    bytes a regeneration produced used to depend on the operating system. That was invisible because
    the acceptance test compared *parsed* documents, and two byte sequences that parse to the same
    value are equal by that measure. This function is now the single definition: the generator writes
    what it returns, and the test compares against it byte for byte.
    """

    return (json.dumps(document, indent=2, sort_keys=True) + "\n").replace("\n", NEWLINE).encode("utf-8")


def write_all(directory: Path = GOLDEN_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="airoot-golden-")).resolve()
    try:
        documents = build_documents(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    for name, payload in documents.items():
        path = directory / f"{name}.json"
        path.write_bytes(render(payload["document"]))
        written.append(path)
    index = directory / "index.json"
    index.write_bytes(
        render({name: payload["exit_code"] for name, payload in sorted(documents.items())})
    )
    written.append(index)
    return written


if __name__ == "__main__":  # pragma: no cover - regeneration helper
    for path in write_all():
        print(path)
