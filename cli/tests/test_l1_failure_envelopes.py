"""The ``--json`` contract for calls that are *supposed* to fail (draft §167).

**This table is a guard over the ``--json`` contract, not a functional test.** Every row names a
call that cannot succeed, and what is asserted is what the *caller receives* — never what the verb
does with it. The distinction is the whole point: §167 found two defects (a bare ``OSError`` out of
``exec`` and a ``TypeError`` out of ``extension status``) that had lived through a 1450-case suite,
because every one of those cases asked "does the verb do the right thing?" and none asked "and what
does a *failing* call print?". A caller that gets a traceback cannot parse a reason code, so for an
agent-driven CLI a crash is not a worse answer than a wrong one — it is a *different kind* of answer,
and no domain test can see it.

So: add a row. One row is one line of evidence that this failure surface answers instead of dying,
and it costs nothing but the four facts already written down (the arguments, the exit code, the
reason code, and why the call is in the table at all).

The runner holds four things per row, plus the reason the row is legitimate rather than a bug:
``main([...])`` must return rather than raise, the exit code must be the row's own, stdout must be
parseable JSON, and ``reason_code`` must be present and must **not** be ``SELF_VALIDATION_FAILED`` —
that code means the *implementation* produced a defective document, and a failure a caller can
construct out of thin air is never that. (A row that is *supposed* to succeed belongs here too: give
it ``exit_code=0`` and ``reason_code=None``, which is this CLI's success spelling for the extension
envelope. None of the rows below are, because this table is about failures.)

Every row goes through a verb with arguments. ``--help``-shaped rows are refused by the table's own
guard below: argparse answers those with a usage text and exit 0, which would make every assertion
here vacuously green.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from airoot.cli import main
from airoot.paths import volume_serial
from airoot.registry import ExternalReference
from airoot.registry.entities import DataRoot

#: The one reference this module registers: a ``java`` object root whose ``bin`` is what
#: ``path_prepend`` exposes. A real launcher is not needed — ``exec`` starts a *name*, and the
#: ``.cmd`` below is a name only this reference's directory carries (§167 defect ②).
REFERENCE = "external/dr-env/java"

#: The bundled deterministic extension: it declares ``status``, ``probe`` and ``invoke``.
FAKE_EXTENSION = "airoot-fake-extension"

PROBE_NAME = "airoot-s167-probe"
PROBE_ANSWER = "S167-PROBE-OK"


@pytest.fixture
def cli_root(registry) -> Path:
    return Path(registry.path).parent.parent


@pytest.fixture
def data_root(tests_tmp: Path) -> Path:
    path = tests_tmp / "failure-envelope-data-root"
    (path / "java" / "bin").mkdir(parents=True, exist_ok=True)
    (path / "java" / "bin" / "java.exe").write_bytes(b"MZ-placeholder-never-executed\n")
    (path / "java" / "bin" / f"{PROBE_NAME}.cmd").write_bytes(
        b"@echo off\r\necho " + PROBE_ANSWER.encode("ascii") + b"\r\n"
    )
    return path


@pytest.fixture
def registered_reference(registry, data_root: Path) -> Path:
    object_root = data_root / "java"
    with registry.write(expected_generation=registry.generation) as connection:
        registry.add_data_root(
            connection,
            DataRoot(
                data_root_id="dr-env",
                path=str(data_root),
                role="runtime",
                volume_serial=volume_serial(data_root),
                added_at="2024-01-01T00:00:00Z",
                whitelist_revision="wl-6",
            ),
        )
        registry.upsert_external_reference(
            connection,
            ExternalReference(
                external_id=REFERENCE,
                capability_id="java",
                path=str(object_root),
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
    return object_root


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FailingCall:
    """One call that cannot succeed, and the four facts that make it checkable.

    ``argv`` is the command line **without** the leading program name; ``{root}`` is replaced with
    the test root, so a row never has to know where the fixture put it.
    """

    label: str
    argv: tuple[str, ...]
    exit_code: int
    #: ``None`` is the extension envelope's own spelling of success, for a row that is meant to pass.
    reason_code: str | None
    why: str


FAILING_CALLS: list[FailingCall] = [
    FailingCall(
        label="exec: a bare command name that resolves on no exposed directory",
        argv=(
            "--json", "--root", "{root}", "exec", REFERENCE, "--", "no-such-tool-xyz", "--version",
        ),
        exit_code=2,
        reason_code="CHILD_PROCESS_FAILED",
        why=(
            "§167 defect ②. The name is resolved against the PATH the *child* would see, so a name "
            "on none of those directories has to come back as an answer. Measured before the fix: "
            "a bare FileNotFoundError traceback, exit 1, empty stdout."
        ),
    ),
    FailingCall(
        label="exec: a path the operating system refuses to start",
        argv=(
            "--json", "--root", "{root}", "exec", REFERENCE, "--",
            r"{root}\no-such-tool.exe", "--version",
        ),
        exit_code=2,
        reason_code="CHILD_PROCESS_FAILED",
        why=(
            "§167 defect ①. A name carrying a directory part is handed to the OS as given, so this "
            "is the OSError path and not the PATH search — the same verdict `run` gives a payload "
            "the machine refuses to start, carrying the operating system's own words as evidence."
        ),
    ),
    FailingCall(
        label="extension status --operation invoke",
        argv=(
            "--json", "--root", "{root}", "extension", "status", FAKE_EXTENSION, "--operation", "invoke",
        ),
        exit_code=1,
        reason_code="NOT_IMPLEMENTED",
        why=(
            "§167 defect ③, and it must stay a *choice* of --operation: the manifest declares "
            "`invoke` (so not EXTENSION_OPERATION_UNKNOWN) and `status`/`probe` both run (so not "
            "EXTENSION_UNAVAILABLE). What is missing is a surface that can supply `echo`, which is "
            "what NOT_IMPLEMENTED(1) is for (ADR-0027). Measured before the fix: a TypeError out of "
            "the handler, exit 1, empty stdout."
        ),
    ),
    FailingCall(
        label="tool status <unregistered instance id>",
        argv=("--json", "--root", "{root}", "tool", "status", "no-such-instance"),
        exit_code=1,
        reason_code="NOT_FOUND",
        why=(
            "The instance id is a caller-supplied lookup key, so a miss is 'not found' and not "
            "'broken': nothing declared is unreadable, there simply is no such object."
        ),
    ),
    FailingCall(
        label="plan --scope data-root --target data-root:<unregistered>",
        argv=(
            "--json", "--root", "{root}", "plan", "archive", "--scope", "data-root",
            "--target", "data-root:dr-nope", "--dry-run",
        ),
        exit_code=1,
        reason_code="NOT_FOUND",
        why=(
            "Draft §163 / ADR-0062: a mistyped id is the same 'not there' as a mistyped path. "
            "DATA_ROOT_MISSING(6) is reserved for a *registered* data root whose directory has gone "
            "away, and its exit-code tier means 'transaction recovery required' — an agent that read "
            "6 here would go and run `repair`."
        ),
    ),
    FailingCall(
        label="extension status <unregistered extension id>",
        argv=("--json", "--root", "{root}", "extension", "status", "no-such-extension"),
        exit_code=9,
        reason_code="EXTENSION_UNAVAILABLE",
        why=(
            "The extension is not merely unknown to a lookup: nothing in this build can host the "
            "capability, which is the extension family's own tier (9), and the evidence is the list "
            "of extensions that do exist."
        ),
    ),
    FailingCall(
        label="root status with no root to resolve",
        argv=("--json", "root", "status"),
        exit_code=8,
        reason_code="ROOT_NOT_RESOLVED",
        why=(
            "Root resolution fails closed (ADR-0025 D5): no --root and no AIROOT_HOME is the "
            "caller's input, so it is 8 and not 1 — there is nothing to look for yet."
        ),
    ),
    FailingCall(
        label="root status on a --root that is not there",
        argv=("--json", "--root", r"{root}\no-such-root", "root", "status"),
        exit_code=8,
        reason_code="ROOT_NOT_RESOLVED",
        why=(
            "A different input from the row above and the same answer on purpose: a named root that "
            "does not exist is still the caller's input (8), not a missing object (1) and not a "
            "recovery state (6). The evidence says which of the two happened — `source=explicit` "
            "here, versus the bare 'pass --root or set AIROOT_HOME' there."
        ),
    ),
]


def test_every_known_failing_call_answers_with_a_failure_envelope(
    capsys, monkeypatch, cli_root: Path, registered_reference: Path
) -> None:
    """The guard this module exists for: a failing call prints a document, never a traceback."""

    # The no-root row decides on "no --root and no AIROOT_HOME"; the other rows pass --root, so
    # clearing it once is what makes that row mean what it says instead of depending on the host.
    monkeypatch.delenv("AIROOT_HOME", raising=False)

    problems: list[str] = []
    for row in FAILING_CALLS:
        argv = [part.format(root=str(cli_root)) for part in row.argv]
        assert "--help" not in argv and "-h" not in argv, (
            f"{row.label}: --help exits 0 with a usage text, so a help row asserts nothing"
        )
        code, document = run(capsys, *argv)
        if code != row.exit_code:
            problems.append(f"{row.label}: exit {code}, expected {row.exit_code}")
        if not document:
            problems.append(f"{row.label}: exit {code} printed nothing on stdout")
            continue
        if "reason_code" not in document:
            problems.append(f"{row.label}: the document carries no reason_code ({sorted(document)})")
            continue
        if document["reason_code"] != row.reason_code:
            problems.append(
                f"{row.label}: reason_code {document['reason_code']!r}, expected {row.reason_code!r}"
            )
        if document["reason_code"] == "SELF_VALIDATION_FAILED":
            problems.append(
                f"{row.label}: reported SELF_VALIDATION_FAILED — a failure a caller can construct is "
                "never an implementation defect, so this is a defect in the *verb*"
            )

    assert problems == [], "a known-failing call did not answer with a failure envelope:\n" + "\n".join(
        problems
    )


def test_the_table_still_covers_the_failure_classes_it_exists_for() -> None:
    """Deleting a row must not be a way to stay green.

    The table is only as strong as the surfaces it holds, and §167 named the classes it has to
    cover. This is a check on the *table*, not on the CLI: it fails the moment a row is dropped.
    """

    codes = {row.reason_code for row in FAILING_CALLS}
    assert {
        "CHILD_PROCESS_FAILED",
        "NOT_IMPLEMENTED",
        "NOT_FOUND",
        "EXTENSION_UNAVAILABLE",
        "ROOT_NOT_RESOLVED",
    } <= codes, f"the table dropped a failure class; it now covers {sorted(codes, key=str)}"
    assert len(FAILING_CALLS) >= 7, "the table is smaller than the surfaces §167 measured"


# --------------------------------------------------------------------------- #
# the three defects, one guard each, so breaking one line turns one guard red
# --------------------------------------------------------------------------- #


def test_exec_runs_a_bare_command_that_only_this_reference_exposes(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """§167 defect ②: ``exec <id> -- <command>`` has to work for the capability's own tools.

    ``SKILL.md`` teaches exactly this spelling, and until §167 the name was resolved by the
    *caller's* PATH — so a tool the reference exposes was reachable only behind ``cmd /c``. The
    probe below exists in the reference's ``bin`` directory and nowhere else, so a green result
    cannot be the host's PATH answering instead.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "exec", REFERENCE, "--", PROBE_NAME
    )

    assert code == 0, document
    assert document["exit_status"] == 0, document
    assert PROBE_ANSWER in document["stdout"], document
    # What the caller asked for is recorded as asked...
    assert document["command"] == [PROBE_NAME], document
    # ...and what was started is the absolute path inside the exposed directory, which is the fact
    # defect ② was missing: without it, "it ran" cannot be told apart from "something ran".
    exposed = Path(document["path_prepend"][0])
    assert Path(document["resolved_command"][0]).parent == exposed, document
    assert Path(document["resolved_command"][0]).stem.lower() == PROBE_NAME, document


def test_exec_reports_a_refused_command_with_the_operating_systems_own_words(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """§167 defect ①: the ``OSError`` branch is a *document*, and it keeps the OS's evidence.

    Distinct from the PATH-search refusal below: this one reached ``subprocess`` and was refused, so
    the winerror has to be there. Losing it would replace a diagnosable failure with a sentence.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "exec", REFERENCE, "--",
        str(cli_root / "no-such-tool.exe"), "--version",
    )

    assert code == 2, document
    assert document["reason_code"] == "CHILD_PROCESS_FAILED", document
    assert any("winerror=" in line for line in document["evidence"]), document
    assert any("the file exists: False" in line for line in document["evidence"]), document


def test_exec_refuses_a_bare_name_with_the_directories_it_searched(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """§167 defect ②'s refusal half: the evidence is *where it looked*, not a WinError.

    "Not on the PATH this reference exposes" and "the OS could not find a file" are the same event
    with different next moves — the first is answered by `env activate`/`where`, the second is not —
    so the searched directories are the evidence.

    The detail is **bounded** on purpose: the caller's next move needs the head of that list, and on
    a host with a long PATH the tail is noise. What the bound must not cost is the actionable half, so
    this holds three things rather than "every directory is listed" — which is the property that was
    deliberately given up: the reference's own directory is there, the line count is bounded, and the
    remainder line accounts for everything that was cut.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "exec", REFERENCE, "--", "no-such-tool-xyz"
    )

    assert code == 2, document
    assert document["reason_code"] == "CHILD_PROCESS_FAILED", document
    assert "not on the PATH this reference exposes" in document["message"], document
    evidence = document["evidence"]
    assert not any("winerror=" in line for line in evidence), document

    # The actionable half: the directory this reference exposes is visible.
    assert any(str(registered_reference / "bin") in line for line in evidence), evidence

    # Bounded: 1 count line + EVIDENCE_HEAD_LIMIT directories + 1 remainder/order line.
    assert len(evidence) <= 11, evidence
    head = re.match(r"^searched (\d+) director(?:y|ies), in this order:$", evidence[0])
    assert head, evidence[0]
    total = int(head.group(1))
    listed = [line for line in evidence[1:] if line.startswith("  ")]
    assert 0 < len(listed) <= 8, evidence
    # The remainder line has to add up to the count it is derived from, or "bounded" would just mean
    # "the rest was hidden".
    if len(listed) < total:
        assert evidence[-1] == (
            f"and {total - len(listed)} more from this process's PATH "
            "(the child's PATH is this reference's entries first, then this process's)"
        ), evidence[-1]
    else:
        assert evidence[-1] == (
            "the child's PATH is this reference's entries first, then this process's"
        ), evidence[-1]


def test_the_exposed_directories_are_visible_however_long_the_inherited_path_is() -> None:
    """The bound is only safe because of the *order*: the reference's entries are the head.

    Bounding a list is a way to lose the thing that mattered, so this is the other direction of the
    guard above: with 30 inherited entries behind them, the reference's own directory still shows up
    in what the caller reads. That holds because `cmd_exec` builds the child's PATH as "this
    reference's entries, then this process's" — which is exactly what makes taking the head safe, and
    what the count line keeps checkable.
    """

    from airoot.cli import _resolve_child_command
    from airoot.exits import AirootError

    exposed = r"D:\exposed\late\bin"
    inherited = [rf"D:\inherited\{index:02d}" for index in range(30)]
    child_path = ";".join([exposed, *inherited])

    with pytest.raises(AirootError) as raised:
        _resolve_child_command(["no-such-tool-xyz"], child_path)

    assert raised.value.reason_code == "CHILD_PROCESS_FAILED", raised.value
    evidence = raised.value.evidence
    assert any(exposed in line for line in evidence), evidence
    assert len(evidence) <= 11, evidence
    listed = [line for line in evidence[1:] if line.startswith("  ")]
    # `.` (the current directory, searched first on Windows) + the exposed entry are the head; the
    # inherited tail is what got cut, and the count line still says how much of it there was.
    assert listed[:2] == ["  .", f"  {exposed}"], listed
    total = int(re.match(r"^searched (\d+) director", evidence[0]).group(1))  # type: ignore[union-attr]
    assert total == 1 + 1 + len(inherited), evidence[0]
    assert len(listed) + int(re.match(r"^and (\d+) more", evidence[-1]).group(1)) == total, evidence  # type: ignore[union-attr]


def test_extension_status_names_the_input_it_has_no_flag_for(
    capsys, cli_root: Path, registered_reference: Path
) -> None:
    """§167 defect ③: the refusal names ``echo``, and it takes nothing away from the other operations.

    Two failures are one line apart and both would be wrong: deleting ``invoke`` from
    ``--operation``'s choices turns ADR-0027's honest deferral into argparse's "invalid choice"
    (a typo answer), while leaving the dispatch as it was crashes. So the same test holds all four
    edges: ``invoke`` is still a *choice*, it answers ``NOT_IMPLEMENTED``(1), ``status``/``probe``
    still run, and an actually unknown operation is still a usage error.
    """

    code, document = run(
        capsys, "--json", "--root", str(cli_root), "extension", "status", FAKE_EXTENSION,
        "--operation", "invoke",
    )
    assert code == 1, document
    assert document["reason_code"] == "NOT_IMPLEMENTED", document
    assert "echo" in document["message"], document
    assert document["details"]["missing_inputs"] == "echo", document

    for operation in ("status", "probe"):
        code, document = run(
            capsys, "--json", "--root", str(cli_root), "extension", "status", FAKE_EXTENSION,
            "--operation", operation,
        )
        assert code == 0, (operation, document)
        assert document["operation"] == operation, document

    # An operation that is not on the list is still refused by argparse as a usage error (8), which
    # is what makes `invoke`'s presence on that list a decision rather than an accident.
    code, document = run(
        capsys, "--json", "--root", str(cli_root), "extension", "status", FAKE_EXTENSION,
        "--operation", "no-such-operation",
    )
    assert code == 8, document
    assert document["reason_code"] == "INVALID_INPUT", document
