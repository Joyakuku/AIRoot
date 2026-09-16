"""`airoot run` — executing a managed payload once (ADR-0047, draft §120).

Two layers are tested here, and the split matters:

* `caps/runtime.py`'s resolution and refusal rules, called directly. These are the cases a caller
  meets when the ledger and the filesystem disagree (collected, missing, foreign, reference) and they
  must not need a child process to be checked.
* the CLI path, which is where `persisted: false` and the exit-code rule live. The child here is a
  real one — a `.cmd` file written into the store — because the whole point of this verb is that it
  starts something and reports what happened. A mocked `subprocess` would test the mock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from airoot.caps.runtime import OUTPUT_LIMIT, _bounded, resolve_run_target, run_once
from airoot.cli import CHILD_VERBS, _normalize_child_argv, main
from airoot.exits import AirootError, exit_code_for
from airoot.registry.entities import Instance

DIGEST = "sha256:" + "a" * 64
CAPABILITY = "probe-tool"
INSTANCE = "probe-tool/probe/1.0.0/win-x64"


def run_cli(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


def declare(
    registry,
    root,
    *,
    instance_id: str = INSTANCE,
    entrypoints: tuple[str, ...] = ("probe.cmd",),
    store_path: str | None = None,
    backend: str = "fake_fixture",
    body: str | None = "@echo off\r\necho hello from the payload\r\nexit /b 3\r\n",
) -> str:
    """An owned instance whose store payload is a real, runnable `.cmd`."""

    store_path = store_path or f"store/{instance_id}"
    if body is not None:
        store_dir = Path(root.path) / store_path
        store_dir.mkdir(parents=True, exist_ok=True)
        for name in entrypoints:
            (store_dir / name).write_text(body, encoding="utf-8")
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_instance(
            connection,
            Instance(
                instance_id=instance_id,
                kind="managed_tool",
                capability_id=CAPABILITY,
                version="1.0.0",
                platform="windows",
                architecture="x64",
                install_backend_id=backend,
                artifact_digest=DIGEST,
                store_path=store_path,
                lifecycle_status="installed",
                health="healthy",
                entrypoints=entrypoints,
                created_at="2024-01-01T00:00:00Z",
            ),
        )
    return instance_id


def declare_reference(registry, root) -> str:
    with registry.write(expected_generation=registry.generation) as connection:
        connection.execute(
            """
            INSERT INTO external_references (external_id, capability_id, path, management, health,
                                             observed_digest, observed_at, payload_json)
            VALUES ('external/dr-env/java', 'java', ?, 'external_reference', 'healthy', NULL,
                    '2024-01-01T00:00:00Z', '{}')
            """,
            (str(Path(root.path) / "tools" / "java"),),
        )
    return "external/dr-env/java"


# --------------------------------------------------------------------------- #
# the pre-parse rule, held for every verb that dispatches a child
# --------------------------------------------------------------------------- #


def test_every_child_verb_survives_the_argv_rewrite_as_itself() -> None:
    """§120's measured defect: the shared rewrite hard-coded `"exec"` as the verb.

    It was invisible while the helper served exactly one command. The moment `run` became the second
    one, `run <id> -- --version` silently became `exec <id> -- --version` and the caller got an
    "unknown reference" for a payload AIROOT owns. The guard is derived from `CHILD_VERBS` rather than
    listing the two names, so a third verb cannot arrive with the same hole.
    """

    assert len(CHILD_VERBS) >= 2, "this rule is about more than one verb; otherwise it decides nothing"

    for verb in CHILD_VERBS:
        normalized = _normalize_child_argv([verb, "some-id", "--json", "--", "--flag"])
        head = _command_token_index_of(normalized)
        assert normalized[head] == verb, (
            f"{verb} was rewritten into {normalized[head]!r}; every verb in CHILD_VERBS must survive "
            "the shared pre-parse rule as itself"
        )
        # Draft §123: the hoisted options belong **after** the verb, not in front of it. They are the
        # subparser's options, and the top-level parser does not know them; putting them first is what
        # made `run --capability X` and `exec --env X -- cmd` fail with "invalid choice: 'X'". What has
        # to hold is the property, not the position: AIROOT's options stay on AIROOT's side of the
        # separator, and never leak into the payload's command line.
        separator = normalized.index("--")
        assert "--json" in normalized[head + 1 : separator], (
            f"{verb}: AIROOT's own options must sit between the verb and the separator, where the "
            f"parser that defines them can read them: {normalized}"
        )
        assert "--json" not in normalized[separator:], "AIROOT's options never enter the payload's argv"
        assert normalized[head + 1 :] == ["--json", "some-id", "--", "--flag"], (
            "the identifier and everything after `--` must be left exactly as the caller wrote them"
        )


def _command_token_index_of(arguments: list[str]) -> int:
    from airoot.cli import _command_token_index

    index = _command_token_index(arguments)
    assert index is not None
    return index


# --------------------------------------------------------------------------- #
# resolution: the four facts that make a run impossible
# --------------------------------------------------------------------------- #


def test_a_declared_instance_resolves_to_its_main_entrypoint(registry, root) -> None:
    declare(registry, root)

    target = resolve_run_target(registry, root.path, INSTANCE)

    assert target.entrypoint.name == "probe.cmd"
    assert target.entrypoint.parent == Path(root.path) / "store" / INSTANCE
    assert target.entrypoint_relative == "probe.cmd"
    document = target.to_document()
    assert document["payload_digest_source"] == "registry", (
        "the digest in the report is the registered value, and the document has to say so"
    )


def test_a_capability_id_resolves_like_the_other_verbs_allow(registry, root) -> None:
    declare(registry, root)

    target = resolve_run_target(registry, root.path, CAPABILITY)

    assert target.instance_id == INSTANCE


def test_an_unknown_target_is_not_found(registry, root) -> None:
    declare(registry, root)

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, "no-such-instance")

    assert caught.value.reason_code == "NOT_FOUND"
    assert exit_code_for("NOT_FOUND") == 1
    assert INSTANCE in " ".join(caught.value.evidence), "the caller is shown what does exist"


def test_a_reference_is_refused_and_pointed_at_the_reference_side_verb(registry, root) -> None:
    external_id = declare_reference(registry, root)

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, external_id)

    assert caught.value.reason_code == "OWNERSHIP_REQUIRED"
    joined = " ".join(caught.value.evidence)
    assert f"airoot exec {external_id}" in joined, "the legal command for a reference is named"
    assert str(Path(root.path) / "tools" / "java") in joined


def test_an_instance_outside_the_store_is_refused(registry, root) -> None:
    declare(registry, root, instance_id="foreign/tool/1.0.0/win-x64", store_path="tools/foreign")

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, "foreign/tool/1.0.0/win-x64")

    assert caught.value.reason_code == "OWNERSHIP_REQUIRED"


def test_a_collected_instance_is_refused_by_name(registry, root) -> None:
    declare(registry, root)
    with registry.write(expected_generation=registry.generation) as connection:
        registry.set_instance_status(connection, INSTANCE, collected_at="2024-02-02T00:00:00Z")

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, INSTANCE)

    assert caught.value.reason_code == "PAYLOAD_MISSING"
    assert exit_code_for("PAYLOAD_MISSING") == 3
    assert any("collected_at" in item for item in caught.value.evidence)


def test_a_missing_payload_is_refused(registry, root) -> None:
    declare(registry, root, body=None)  # declared but nothing on disk

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, INSTANCE)

    assert caught.value.reason_code == "PAYLOAD_MISSING"
    assert any("does not exist" in item for item in [caught.value.message, *caught.value.evidence])


def test_a_missing_entrypoint_is_refused_with_the_declared_list(registry, root) -> None:
    declare(registry, root)
    (Path(root.path) / "store" / INSTANCE / "probe.cmd").unlink()

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, INSTANCE)

    assert caught.value.reason_code == "PAYLOAD_MISSING"
    joined = " ".join(caught.value.evidence)
    assert "probe.cmd" in joined, "the entrypoint the registry declares is quoted back"


def test_an_instance_that_declares_no_entrypoint_is_refused_rather_than_guessed(registry, root) -> None:
    declare(registry, root, entrypoints=())

    with pytest.raises(AirootError) as caught:
        resolve_run_target(registry, root.path, INSTANCE)

    assert caught.value.reason_code == "PAYLOAD_MISSING"
    assert any(
        "declares no entrypoint" in item for item in [caught.value.message, *caught.value.evidence]
    )


# --------------------------------------------------------------------------- #
# execution: status and output come back as they were
# --------------------------------------------------------------------------- #


def test_run_once_reports_the_childs_exit_status_and_output(registry, root) -> None:
    declare(registry, root)
    target = resolve_run_target(registry, root.path, INSTANCE)

    document = run_once(target, [], capture=True)

    assert document["exit_status"] == 3
    assert document["reason_code"] == "CHILD_PROCESS_FAILED"
    assert "hello from the payload" in document["stdout"]
    assert document["command"][0] == str(target.entrypoint)


def test_a_successful_child_is_success_and_still_reports_its_output(registry, root) -> None:
    declare(registry, root, body="@echo off\r\necho fine\r\nexit /b 0\r\n")
    target = resolve_run_target(registry, root.path, INSTANCE)

    document = run_once(target, [], capture=True)

    assert document["exit_status"] == 0
    assert document["reason_code"] == "SUCCESS"
    assert "fine" in document["stdout"]


def test_arguments_are_passed_through_untouched(registry, root) -> None:
    declare(registry, root, body="@echo off\r\necho args:%*\r\nexit /b 0\r\n")
    target = resolve_run_target(registry, root.path, INSTANCE)

    document = run_once(target, ["--dry-run", "two words"], capture=True)

    # The contract is the argument **list**; what cmd.exe then does with a space (`"two words"`) is the
    # child's business, so the assertion is on the list and on both parts surviving into the output.
    assert document["command"] == [str(target.entrypoint), "--dry-run", "two words"]
    assert "--dry-run" in document["stdout"] and "two words" in document["stdout"]


def test_a_payload_that_cannot_be_started_reports_the_operating_system(registry, root) -> None:
    """A present-but-unstartable file is `CHILD_PROCESS_FAILED` **with the OS's own words**.

    Without this conversion the caller gets a traceback (§62's lesson: a filesystem refusal is not an
    `AirootError` and used to escape the transaction drivers as one).
    """

    declare(registry, root, entrypoints=("probe.bin",), body="this is not an executable\r\n")
    target = resolve_run_target(registry, root.path, INSTANCE)

    with pytest.raises(AirootError) as caught:
        run_once(target, [], capture=False)

    assert caught.value.reason_code == "CHILD_PROCESS_FAILED"
    joined = " ".join(caught.value.evidence)
    assert "the payload is present but this machine refused to start it" in joined
    assert "winerror" in joined.lower() or "OSError" in joined


def test_the_report_carries_the_registered_digest_not_a_fresh_measurement(registry, root) -> None:
    declare(registry, root)
    target = resolve_run_target(registry, root.path, INSTANCE)

    document = run_once(target, [], capture=True)

    assert document["payload_digest"] == DIGEST
    assert document["payload_digest_source"] == "registry"


def test_bounded_keeps_the_tail_and_says_it_truncated() -> None:
    long_output = "x" * (OUTPUT_LIMIT + 100) + "THE-END"

    bounded = _bounded(long_output)

    assert bounded.startswith("...[truncated]...")
    assert bounded.endswith("THE-END")
    assert len(bounded) < len(long_output)


# --------------------------------------------------------------------------- #
# the CLI path
# --------------------------------------------------------------------------- #


def test_cli_run_reports_a_document_with_persisted_false(capsys, registry, root) -> None:
    declare(registry, root)

    # `--json` is AIROOT's option, so it is read before the payload's command line: both spellings
    # (`run <id> --json` and `--json run <id>`) must mean machine mode, and neither may hand the flag
    # to the child. That is what `_normalize_child_argv`'s hoisting buys (draft §120).
    code, document = run_cli(capsys, "--root", str(root.path), "run", INSTANCE, "--json")

    assert code == 2, "a non-zero child exits AIROOT as degraded, never as the child's own code"
    assert document["exit_status"] == 3
    assert document["reason_code"] == "CHILD_PROCESS_FAILED"
    assert document["persisted"] is False
    assert document["lifecycle_status"] == "installed"
    assert "hello from the payload" in document["stdout"]
    assert document["command"] == [document["entrypoint"]], (
        "`--json` is AIROOT's; the payload must not receive it as an argument"
    )


def test_cli_run_takes_arguments_after_the_separator(capsys, registry, root) -> None:
    declare(registry, root, body="@echo off\r\necho got:%1\r\nexit /b 0\r\n")

    code, document = run_cli(
        capsys, "--root", str(root.path), "run", INSTANCE, "--json", "--", "SEPARATED"
    )

    assert code == 0
    assert document["exit_status"] == 0
    assert "got:SEPARATED" in document["stdout"]
    assert document["command"][-1] == "SEPARATED", "arguments after `--` reach the payload verbatim"


def test_cli_run_does_not_change_the_registry_or_the_payload(capsys, registry, root) -> None:
    from airoot.canon import tree_digest

    declare(registry, root)
    store_dir = Path(root.path) / "store" / INSTANCE
    before_digest = tree_digest(store_dir)
    before_generation = registry.generation

    run_cli(capsys, "--root", str(root.path), "run", INSTANCE, "--json")

    assert registry.generation == before_generation, "a run is not a transaction"
    assert tree_digest(store_dir) == before_digest, "a run does not touch the payload"


def test_cli_run_refuses_with_the_documented_exit_code(capsys, registry, root) -> None:
    declare(registry, root)

    code, document = run_cli(capsys, "--json", "--root", str(root.path), "run", "no-such-instance")

    assert code == 1
    assert document["reason_code"] == "NOT_FOUND"


def test_a_leading_airoot_option_is_not_handed_to_the_payload(capsys, registry, root) -> None:
    """The footgun this rewrite exists for, in the form that has no `--` separator.

    Measured before the fix: `run <id> --json` passed `--json` to the payload *and* printed human
    text, because `REMAINDER` owned everything after the identifier. The escape hatch for a payload
    that really wants a flag AIROOT also knows is the separator.
    """

    declare(registry, root, body="@echo off\r\necho child-args:%*\r\nexit /b 0\r\n")

    code, document = run_cli(capsys, "--root", str(root.path), "run", INSTANCE, "--json")

    assert code == 0
    assert document["reason_code"] == "SUCCESS"
    assert "--json" not in document["command"]
    assert "child-args:" in document["stdout"]

    # And the escape hatch still works: after `--`, an identical flag belongs to the payload.
    code, document = run_cli(
        capsys, "--root", str(root.path), "run", INSTANCE, "--json", "--", "--json"
    )

    assert code == 0
    assert document["command"][-1] == "--json"
    assert "--json" in document["stdout"].split("child-args:", 1)[1]
