"""L0: pure protocol tests - canonicalization, digests, paths, exit codes, schemas.

No registry, no transaction, no host mutation (verification plan §2 "L0").
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from airoot import exits, schema_io
from airoot.canon import (
    canonical_bytes,
    canonical_json,
    digest_bytes,
    file_manifest,
    file_manifest_digest,
    plan_hash,
    tree_digest,
)
from airoot.clock import FakeClock, isoformat, parse_timestamp
from airoot.exits import AirootError, REASON_EXIT, exit_code_for, is_valid_reason_code
from airoot.paths import (
    canonicalize,
    from_root_relative,
    is_reparse_point,
    is_unc,
    is_within,
    relative_to_root,
    reject_reparse_chain,
)


# --------------------------------------------------------------------------- #
# canonicalization and digests
# --------------------------------------------------------------------------- #


def test_canonical_json_is_key_order_independent() -> None:
    left = {"b": 1, "a": [3, 2, {"z": True, "y": None}]}
    right = {"a": [3, 2, {"y": None, "z": True}], "b": 1}
    assert canonical_json(left) == canonical_json(right)


def test_canonical_json_has_no_insignificant_whitespace() -> None:
    assert canonical_json({"a": 1, "b": "x y"}) == '{"a":1,"b":"x y"}'
    assert canonical_bytes({"a": 1}) == b'{"a":1}'


def test_plan_hash_ignores_field_order_and_its_own_field() -> None:
    plan = {
        "schema_version": 1,
        "plan_id": "plan/fake-tool/1.0.0/abc",
        "operation": "install_tool",
        "plan_hash": "sha256:" + "0" * 64,
    }
    reordered = {
        "operation": "install_tool",
        "plan_id": "plan/fake-tool/1.0.0/abc",
        "schema_version": 1,
        "plan_hash": "sha256:" + "f" * 64,
    }
    assert plan_hash(plan) == plan_hash(reordered)


def test_digests_match_the_schema_patterns() -> None:
    import re

    digest = digest_bytes(b"airoot")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert file_manifest_digest([{"path": "a", "size": 1, "digest": digest, "mode": "file"}]).startswith("sha256:")


def test_tree_digest_and_manifest_track_content(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("alpha", encoding="utf-8")
    first = tree_digest(tmp_path)
    manifest = file_manifest(tmp_path)
    assert [entry["path"] for entry in manifest] == ["sub/a.txt"]
    assert manifest[0]["mode"] == "file"

    (tmp_path / "sub" / "a.txt").write_text("beta", encoding="utf-8")
    assert tree_digest(tmp_path) != first


# --------------------------------------------------------------------------- #
# clock
# --------------------------------------------------------------------------- #


def test_fake_clock_is_deterministic_and_advances() -> None:
    clock = FakeClock(start="2024-05-05T00:00:00Z")
    first = clock.timestamp()
    second = clock.timestamp()
    assert first == "2024-05-05T00:00:00Z"
    assert second == "2024-05-05T00:00:01Z"
    assert isoformat(parse_timestamp(first)) == first


# --------------------------------------------------------------------------- #
# exit codes and reason codes
# --------------------------------------------------------------------------- #


def test_exit_code_meanings_cover_zero_through_nine() -> None:
    assert sorted(exits.EXIT_MEANINGS) == list(range(10))


def test_every_reason_code_is_well_formed_and_maps_to_a_known_exit() -> None:
    for code, value in REASON_EXIT.items():
        assert is_valid_reason_code(code), code
        assert value in exits.EXIT_MEANINGS, code


def test_documented_reason_examples_keep_their_documented_exit_codes() -> None:
    documented = {
        # `OK` per v0.3 §15.6 is not representable under the published reason_code
        # pattern (min length 3); P1 emits `SUCCESS` instead. See ADR-0003.
        "SUCCESS": 0,
        "CURRENT_PROCESS_ENV_OLD": 0,
        "NOT_FOUND": 1,
        "VERSION_UNSATISFIED": 1,
        "DEGRADED": 2,
        "DRIFT_DETECTED": 2,
        "BROKEN": 3,
        "CONFLICT_MANAGED_BROKEN": 3,
        "APPROVAL_REQUIRED": 4,
        "APPROVAL_EXPIRED": 4,
        "APPROVAL_REPLAYED": 4,
        "PRIVILEGE_REQUIRED": 5,
        "ACL_MISMATCH": 5,
        "RECOVERY_REQUIRED": 6,
        "PENDING_TRANSACTION": 6,
        "INVALID_PLAN": 7,
        "PROVENANCE_FAILED": 7,
        "DIGEST_MISMATCH": 7,
        "INVALID_INPUT": 8,
        "SCHEMA_UNSUPPORTED": 8,
        "EXTENSION_UNAVAILABLE": 9,
        "EXTENSION_VERSION_UNSUPPORTED": 9,
    }
    for code, expected in documented.items():
        assert exit_code_for(code) == expected, code


def test_airoot_error_rejects_unregistered_codes() -> None:
    with pytest.raises(KeyError):
        AirootError("NOT_A_REAL_CODE", "boom")


def test_airoot_error_envelope_is_stable() -> None:
    error = AirootError("DIGEST_MISMATCH", "source changed", evidence=["plan=sha256:..."])
    assert error.exit_code == 7
    assert error.to_envelope() == {
        "schema_version": 1,
        "status": "failed",
        "reason_code": "DIGEST_MISMATCH",
        "message": "source changed",
        "evidence": ["plan=sha256:..."],
    }


def test_every_registered_reason_code_produces_a_schema_valid_failure_document() -> None:
    """The failure document is the one outward document every command can print (draft §102).

    One schema covers all of them because `to_envelope` is the single writer and it puts the same
    keys on every code — measured, not assumed, and the opposite of §100's 29 distinct lane shapes.
    Both shapes are exercised: with `details` (only §101's refusal uses it today) and without.
    """

    problems: list[str] = []
    for code in sorted(REASON_EXIT):
        for details in (None, {"command": "bootstrap", "unblocked_by": "p2-protected-state"}):
            document = AirootError(code, "a message", evidence=["one line"], details=details).to_envelope()
            for problem in schema_io.errors_for("error-response", document):
                problems.append(f"{code}{' +details' if details else ''}: {problem}")

    assert problems == [], "failure documents the published schema rejects:\n" + "\n".join(problems)
    # Non-vacuity: the walk has to be reaching the documents, not an empty loop.
    assert len(REASON_EXIT) >= 50, f"only {len(REASON_EXIT)} codes were walked"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #


def test_is_within_is_case_insensitive_and_drive_aware(tmp_path: Path) -> None:
    root = tmp_path / "Root"
    root.mkdir()
    child = root / "store" / "x"
    assert is_within(child, root)
    assert is_within(str(child).upper(), root)
    assert not is_within(tmp_path / "other", root)


def test_unc_and_relative_paths_are_rejected(tmp_path: Path) -> None:
    assert is_unc(r"\\server\share")
    with pytest.raises(AirootError) as unc:
        canonicalize(r"\\server\share\x")
    assert unc.value.reason_code == "UNC_NOT_ALLOWED"
    with pytest.raises(AirootError) as relative:
        canonicalize("relative/path")
    assert relative.value.reason_code == "INVALID_INPUT"


def test_paths_escaping_the_root_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(AirootError) as err:
        canonicalize(outside / "payload.bin", root=root)
    assert err.value.reason_code == "PATH_ESCAPES_ROOT"


def test_root_relative_round_trip() -> None:
    root = Path("C:/airoot").resolve()
    stored = "store/fake-tool/1.0.0/win-x64/fake-tool.bin"
    assert relative_to_root(from_root_relative(stored, root), root) == stored


@pytest.mark.parametrize("bad", ["../escape.bin", "store/../../escape.bin", "/abs.bin", r"\\server\share\x"])
def test_root_relative_rejects_traversal_and_absolute(bad: str) -> None:
    with pytest.raises(AirootError) as err:
        from_root_relative(bad, Path("C:/airoot").resolve())
    assert err.value.reason_code in {"PATH_ESCAPES_ROOT", "UNC_NOT_ALLOWED"}


def test_plain_directories_are_not_reparse_points(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not is_reparse_point(plain)
    reject_reparse_chain(plain, root=tmp_path)


def test_junction_under_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    junction = root / "link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0 or not junction.exists():
        pytest.skip(f"cannot create a junction in this environment: {created.stderr.strip()}")
    assert is_reparse_point(junction)
    with pytest.raises(AirootError) as err:
        reject_reparse_chain(junction, root=root)
    assert err.value.reason_code == "REPARSE_POINT_REJECTED"


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #


def test_all_published_schemas_are_meta_valid() -> None:
    assert schema_io.check_schemas_are_meta_valid() == 20


def test_relative_refs_resolve_against_common() -> None:
    marker = {
        "schema_version": 1,
        "root_instance_id": "root-4f2a9c1d8e3b",
        "protocol_version": 1,
        "volume_serial": "1a2b3c4d",
        "canonical_path": "C:/airoot/cli",
        "created_at": "2024-01-01T00:00:00Z",
    }
    schema_io.validate_document("root-marker", marker)

    broken = dict(marker, volume_serial="not a serial!!")
    with pytest.raises(AirootError) as err:
        schema_io.validate_document("root-marker", broken)
    assert err.value.reason_code == "INVALID_INPUT"


def test_unknown_fields_are_rejected_at_security_boundaries() -> None:
    marker = {
        "schema_version": 1,
        "root_instance_id": "root-4f2a9c1d8e3b",
        "protocol_version": 1,
        "volume_serial": "1a2b3c4d",
        "canonical_path": "C:/airoot/cli",
        "created_at": "2024-01-01T00:00:00Z",
        "unexpected": True,
    }
    with pytest.raises(AirootError) as err:
        schema_io.validate_document("root-marker", marker)
    assert "unexpected" in " ".join(err.value.evidence)


def test_self_validation_failure_is_distinguishable() -> None:
    with pytest.raises(AirootError) as err:
        schema_io.validate_self("root-marker", {"schema_version": 1})
    assert err.value.reason_code == "SELF_VALIDATION_FAILED"


def test_schema_catalog_is_unexpectedly_stable() -> None:
    names = schema_io.schema_names()
    assert len(names) == 20
    assert "common.schema.json" in names and "where-response.schema.json" in names
    assert "reference-plan.schema.json" in names, "the reference-domain plan is a published boundary"
    assert "error-response.schema.json" in names, "the failure document is a published boundary (§102)"
