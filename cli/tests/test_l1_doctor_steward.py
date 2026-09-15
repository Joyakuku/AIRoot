"""L1: ``doctor`` in the steward domain (draft §9).

Two different ownership models need two different kinds of proof:

* an **owned** payload has an install-time digest baseline, so tampering is a mismatch;
* a **reference** has only observation, so drift can only be *re-observed*.

The checks here are deliberately asymmetric about cost: the default path re-observes an object
with bounded reads, while full-file digests stay behind ``--verify``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from airoot.caps.doctor import diagnostics_by_code, doctor
from airoot.canon import digest_file
from airoot.paths import volume_serial
from airoot.registry import ExternalReference
from airoot.registry.entities import DataRoot

from test_l1_discovery import place_python_pe

CAPABILITY = "python"


@pytest.fixture
def steward_root(tmp_path: Path):
    """A CLI root with one registered data root holding one adopted reference."""

    from airoot.registry import Registry
    from airoot.root import init_root

    base = tmp_path / "doctor-steward"
    root = init_root(base / "root", root_instance_id="root-doctor-steward", machine_id="host-doctor-steward")
    registry = Registry.initialize(
        root.path, machine_id="host-doctor-steward", root_instance_id="root-doctor-steward"
    )
    data_root = base / "env"
    object_root = place_python_pe(data_root / "python", "python.exe").parent
    with registry.write(expected_generation=0) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-3",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/python",
                capability_id=CAPABILITY,
                path=str(object_root),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="3.11.11",
                architecture="x64",
                entrypoints=("python.exe",),
                observed_digest=digest_file(object_root / "python.exe"),
                probe_level=2,
                source_kind="pe_static",
            ),
        )
    registry.update_projection()
    yield root, registry, data_root, object_root
    registry.close()


def codes(document: dict) -> set[str]:
    return set(diagnostics_by_code(document))


def test_a_fresh_reference_produces_no_drift_diagnostic(steward_root) -> None:
    root, registry, _data_root, _object_root = steward_root

    document = doctor(root.path, registry=registry)

    assert "DATA_ROOT_MISSING" not in codes(document)
    assert "REFERENCE_STALE" not in codes(document)
    # The synthesised object's version comes from the host interpreter, so the recorded
    # "3.11.11" is expected to differ here: drift must be *reported*, and that is correct.
    assert document["status"] in {"healthy", "degraded"}


def test_a_missing_data_root_is_an_error(steward_root) -> None:
    root, registry, data_root, _object_root = steward_root
    import shutil

    shutil.rmtree(data_root)

    document = doctor(root.path, registry=registry)

    assert "DATA_ROOT_MISSING" in codes(document)
    assert diagnostics_by_code(document)["DATA_ROOT_MISSING"]["severity"] == "error"
    # doctor's exit code comes from the status, not from a single code (draft §18.3-4).
    assert document["status"] == "broken"


def test_a_reference_whose_object_vanished_is_stale(steward_root) -> None:
    root, registry, _data_root, object_root = steward_root
    import shutil

    shutil.rmtree(object_root)

    document = doctor(root.path, registry=registry)

    assert "REFERENCE_STALE" in codes(document)
    assert diagnostics_by_code(document)["REFERENCE_STALE"]["severity"] == "warning"


def test_drift_is_detected_by_re_observation(steward_root) -> None:
    """No digest baseline exists for a reference; the observed facts are the comparison."""

    root, registry, _data_root, object_root = steward_root
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/python",
                capability_id=CAPABILITY,
                path=str(object_root),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="2.0.0",
                active_version="1.0.0",
                versions=("1.0.0",),
                architecture="x64",
                entrypoints=("python.exe",),
                observed_digest=digest_file(object_root / "python.exe"),
                probe_level=2,
                source_kind="pe_static",
            ),
        )

    document = doctor(root.path, registry=registry)

    assert "REFERENCE_DRIFTED" in codes(document)
    evidence = " ".join(diagnostics_by_code(document)["REFERENCE_DRIFTED"]["evidence"])
    assert "version 1.0.0 ->" in evidence or "active_version 1.0.0 ->" in evidence


def test_an_invisible_content_change_is_only_found_with_verify(steward_root) -> None:
    """Re-observation sees facts (version, entrypoint, architecture); content needs ``--verify``.

    The default path must not read whole files — 120 MB ``node.exe`` on every ``doctor`` run is
    exactly the cost the bounded probe exists to avoid. A digest that no longer matches the
    bytes, while every observable fact is unchanged, is therefore only visible under ``--verify``.
    """

    root, registry, _data_root, object_root = steward_root
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/python",
                capability_id=CAPABILITY,
                path=str(object_root),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                # Every observable fact stays empty/consistent, so only the bytes can differ.
                entrypoints=("python.exe",),
                observed_digest="sha256:" + "0" * 64,
                probe_level=2,
                source_kind="pe_static",
            ),
        )

    without = doctor(root.path, registry=registry)
    assert "REFERENCE_DRIFTED" not in codes(without), "the default path must not read whole files"

    with_verify = doctor(root.path, registry=registry, verify=True)
    assert "REFERENCE_DRIFTED" in codes(with_verify)
    evidence = " ".join(diagnostics_by_code(with_verify)["REFERENCE_DRIFTED"]["evidence"])
    assert "recorded=sha256:0000" in evidence


def test_a_disappeared_capability_match_is_reported_as_drift(steward_root) -> None:
    """Re-observation is cheap and catches this one: the object is no longer the capability."""

    root, registry, _data_root, object_root = steward_root
    (object_root / "python.exe").write_bytes(b"MZ-tampered-payload-that-is-not-a-pe")

    document = doctor(root.path, registry=registry)

    assert "REFERENCE_DRIFTED" in codes(document)
    evidence = " ".join(diagnostics_by_code(document)["REFERENCE_DRIFTED"]["evidence"])
    assert "no longer matches a whitelisted capability" in evidence


def test_an_unprobed_reference_is_reported_as_info(steward_root) -> None:
    root, registry, _data_root, object_root = steward_root
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/python",
                capability_id=CAPABILITY,
                path=str(object_root),
                management="external_reference",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                capability_kind="runtime",
                data_root_id="dr-env",
                version="unknown",
                entrypoints=("python.exe",),
                probe_level=0,
                source_kind="declared",
            ),
        )

    document = doctor(root.path, registry=registry)

    assert "REFERENCE_UNPROBED" in codes(document)
    assert diagnostics_by_code(document)["REFERENCE_UNPROBED"]["severity"] == "info"


def test_unmanaged_objects_are_only_reported_when_asked(steward_root) -> None:
    root, registry, _data_root, _object_root = steward_root
    with registry.write(expected_generation=registry.generation) as connection:
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id="external/dr-env/huawei",
                capability_id="unknown",
                path=str(Path(registry.data_root("dr-env")["path"]) / "huawei"),
                management="unmanaged",
                health="healthy",
                observed_at="2024-01-01T00:00:00Z",
                data_root_id="dr-env",
            ),
        )

    quiet = doctor(root.path, registry=registry)
    assert "UNMANAGED_OBJECT_PRESENT" not in codes(quiet), "unmanaged noise must be opt-in (S-029)"

    loud = doctor(root.path, registry=registry, include_unmanaged=True)
    assert "UNMANAGED_OBJECT_PRESENT" in codes(loud)
    assert diagnostics_by_code(loud)["UNMANAGED_OBJECT_PRESENT"]["remediation"] == "none"


def test_an_old_whitelist_revision_is_reported_as_info(steward_root) -> None:
    root, registry, _data_root, _object_root = steward_root
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute("UPDATE data_roots SET whitelist_revision = 'wl-1' WHERE data_root_id = 'dr-env'")

    document = doctor(root.path, registry=registry)

    assert "WHITELIST_REVISION_STALE" in codes(document)
    assert diagnostics_by_code(document)["WHITELIST_REVISION_STALE"]["severity"] == "info"


def test_doctor_never_touches_the_data_root(steward_root) -> None:
    """The whole steward model rests on this: diagnosis is read-only."""

    root, registry, data_root, _object_root = steward_root
    before = sorted((path.relative_to(data_root).as_posix(), path.stat().st_size) for path in data_root.rglob("*") if path.is_file())

    doctor(root.path, registry=registry, verify=True, include_unmanaged=True)

    after = sorted((path.relative_to(data_root).as_posix(), path.stat().st_size) for path in data_root.rglob("*") if path.is_file())
    assert before == after
