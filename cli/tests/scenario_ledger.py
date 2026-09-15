"""The scenario ledger: every acceptance-scenario ID the two documents define, with its disposition.

Why this file exists
--------------------
``docs/AIROOT-v0.3-验证与测试方案.md`` and
``docs/AIROOT-v0.3-管家模型与数据根契约草案.md`` together define this project's
acceptance scenarios — **two tables in two documents**. Nothing enumerated the
union. A Rust port following ADR-0001 therefore had no complete acceptance
index, no test knew how many scenarios exist, and §48.5 recorded the resulting
worry ("only ~30 of the 79 IDs are named by tests") with no way to settle it.

This module turns that worry into a ledger with two halves, deliberately
separated:

* **derived** — the ID set, which document defines each one, and which test
  files name it. Pure functions of the repository. The guard recomputes them and
  requires the fixture to match, so the ledger cannot drift from either side.
* **authored** — the *disposition* of the IDs no test names: what is missing.
  Those are judgements, so they live here as a reviewable table with a stated
  reason each; the guard checks only their **structure** (declared vocabulary,
  resolvable evidence pointer). A judgement is never dressed up as a
  measurement.

``status`` is therefore ``evidenced`` (at least one test file names the ID) or
``uncited`` (none does). There is deliberately **no** "covered" value: no guard
can decide coverage, so calling 68 IDs "untested" would be as unfounded as
calling them "tested". What the ledger buys is that "no test names this ID" is
now a *number with reasons* instead of a vague worry.

Citation syntax is not uniform, and that matters
------------------------------------------------
The tests spell ID ranges five different ways (``S-032…S-036``, ``P-004..P-008``,
``...``, ``~``, ``至``), and sometimes as slash lists (``T-002/T-006/…``). The
first probe written for this round knew four of the five spellings, missed
``..``, and consequently under-reported the evidence count by exactly three IDs
(P-005/P-006/P-007). ``EVIDENCE_*`` below is the corrected rule, and the parser
self-tests pin all five markers so the next hand-rolled regex cannot repeat that
mistake. Windows SIDs (``S-1-5-21-1000``) look like scenario IDs to a loose
pattern; ``_SID`` neutralises them first.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The two documents that define acceptance scenarios. There is no third: a
#: scan of every ``docs/*.md`` table row (``^\\s*\\|\\s*[A-Z]{1,3}-\\d{3}``)
#: finds these two and nothing else.
VERIFICATION_PLAN = "docs/AIROOT-v0.3-验证与测试方案.md"
STEWARD_DRAFT = "docs/AIROOT-v0.3-管家模型与数据根契约草案.md"
DOCUMENTS: tuple[str, ...] = (VERIFICATION_PLAN, STEWARD_DRAFT)

FAMILIES: tuple[str, ...] = ("P", "S", "T", "C")

#: Every range spelling actually used by the tests. Kept as data so the parser
#: self-test can assert the list is the whole set (see the module docstring).
RANGE_MARKERS: tuple[str, ...] = ("…", "...", "..", "~", "至")

#: What is missing for an ID no test names. ``none`` is a member of the
#: *allowed* vocabulary but not of :data:`MISSING_CAPABILITIES`: it means no
#: capability is missing, the behaviour is exercised by a test that simply does
#: not name the ID, and such an entry must carry an evidence pointer instead.
MISSING_CAPABILITIES: tuple[str, ...] = (
    "p2-protected-state",  # ACL / UAC / elevated broker / machine PATH / machine-scope env
    "p4-real-backend",  # a real artifact: download, extraction, stage, commit, disk, locks
    "p3-native-indexer",  # the resident USN indexer
    "undesigned",  # the command the scenario needs does not exist or has no semantics yet
    # The invariant is documented (sometimes even implemented) but nothing drives
    # it, and writing that test is possible today — this value is the ledger's
    # honest name for test debt, as opposed to work that is actually blocked.
    "unchecked-invariant",
)
BLOCKED_BY_VALUES: tuple[str, ...] = MISSING_CAPABILITIES + ("none",)

#: Marker required in *every* defining row of an ID that more than one document
#: defines, so a reader of either document learns the other one exists.
DUAL_DEFINITION_MARKER = "同时更新"

#: Marker required in the defining row of a scenario whose documented
#: expectation has been superseded by a later decision.
SUPERSEDED_MARKER = "已取代"

#: Marker required in the defining row of a scenario that is a duplicate
#: registration of another one — the row itself must say so, because a reader
#: choosing which ID to cite only ever sees that row.
DUPLICATE_MARKER = "重复登记"

_DEFINITION = re.compile(r"^\s*\|\s*([PSTC])-(\d{3})([^|]*)\|([^|]*)\|([^|]*)\|")
_RANGE = re.compile(
    r"\b([PSTC])-(\d{3})\s*(?:" + "|".join(re.escape(m) for m in RANGE_MARKERS) + r")\s*(?:\1-)?(\d{3})\b"
)
_SINGLE = re.compile(r"\b([PSTC])-(\d{3})\b")
_SID = re.compile(r"\bS-\d+(?:-\d+)+")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _clean(cell: str) -> str:
    return " ".join(cell.replace("**", "").split())


def definitions(repo: Path) -> dict[str, list[dict[str, str]]]:
    """Every scenario a document *defines*, keyed by ID.

    Only a **table row whose first cell is the ID** counts. Prose that names an
    ID (``验证与测试方案`` line 130: "P-003 和 P-013 是防止安全口径夸大的测试")
    is a citation-like mention, not a definition, and treating it as one would
    corrupt the catalogue with duplicate rows that carry no expectation.
    """

    found: dict[str, list[dict[str, str]]] = {}
    for relative in DOCUMENTS:
        document = repo / relative
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            match = _DEFINITION.match(line)
            if match is None:
                continue
            family, digits, _suffix, setup, expectation = match.groups()
            key = f"{family}-{int(digits):03d}"
            found.setdefault(key, []).append(
                {
                    "document": relative,
                    "line": number,
                    "setup": _clean(setup),
                    "expectation": _clean(expectation),
                    "row": line,
                }
            )
    return found


def citations(repo: Path) -> dict[str, set[str]]:
    """Every scenario ID the test suite names, mapped to the test files naming it.

    Deliberately **file-level, not line-level**: line numbers would make the
    frozen fixture churn on any unrelated edit to a test file, which would train
    everyone to regenerate it without reading the diff. Which *files* name an ID
    is the fact worth freezing.

    Only ``test_*.py`` counts. That is not a detail: this module lives in
    ``cli/tests/`` and its disposition table names **every** ID, so a scan of
    "all Python files here" makes the ledger cite itself and reports 107/107 as
    evidenced — which is exactly the kind of silent, self-confirming
    measurement the ledger exists to prevent.
    """

    found: dict[str, set[str]] = {}
    for test in sorted((repo / "cli" / "tests").glob("test_*.py")):
        for line in test.read_text(encoding="utf-8").splitlines():
            line = _SID.sub("<sid>", line)
            keys: set[str] = set()
            for a, b, c in _RANGE.findall(line):
                keys.update(f"{a}-{n:03d}" for n in range(int(b), int(c) + 1))
            keys.update(f"{a}-{int(b):03d}" for a, b in _SINGLE.findall(line))
            for key in keys:
                found.setdefault(key, set()).add(test.name)
    return found


def evidence_token(text: str) -> str | None:
    """The first inline-code token in a row's expectation, if any (diagnostics only)."""

    match = _INLINE_CODE.search(text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Authored dispositions. Every key here is an ID that no test names.
# ---------------------------------------------------------------------------

DISPOSITIONS: dict[str, dict[str, Any]] = {
    # --- P: privilege and provenance. Without a broker there is nothing to test
    # against: the scenario is about a boundary that P2 builds.
    "P-001": {"blocked_by": "p2-protected-state"},
    "P-002": {"blocked_by": "p2-protected-state"},
    "P-009": {"blocked_by": "p2-protected-state"},
    "P-010": {"blocked_by": "p2-protected-state"},
    "P-012": {"blocked_by": "p2-protected-state"},
    "P-015": {"blocked_by": "p2-protected-state"},
    "P-016": {"blocked_by": "p2-protected-state"},
    "P-017": {"blocked_by": "p2-protected-state"},
    "P-003": {"blocked_by": "none", "evidence": "test_l1_toolstate.py#PATH"},
    "P-018": {"blocked_by": "none", "evidence": "test_l1_registry.py#managed_tool_payload"},
    "P-019": {"blocked_by": "none", "evidence": "test_l1_discovery.py#unmanaged"},
    "P-020": {"blocked_by": "none", "evidence": "test_l1_toolstate.py#test_verify_refuses_an_unknown_instance"},
    "P-021": {
        "blocked_by": "none",
        "evidence": "test_cli_steward.py#test_data_root_on_another_volume_is_accepted",
        "superseded": "跨卷数据根已改为**允许**（ADR-0004），只有 UNC 与 reparse point 仍被拒绝",
        "note": (
            "该行的前半（UNC / reparse point → 拒绝，退出码 8）仍成立，由 test_l0_protocol.py 的 "
            "UNC_NOT_ALLOWED / REPARSE_POINT_REJECTED 覆盖；后半（'另一个卷'）已被 ADR-0004 取代："
            "cli.py 明确写着数据根的卷身份不必与 CLI root 相同，test_cli_steward.py 直接断言跨卷可接受。"
        ),
    },
    # --- S: steward/registry behaviour that is exercised, plus a few that need
    # a capability that does not exist yet.
    "S-001": {"blocked_by": "none", "evidence": "test_l1_desired.py#desired"},
    "S-002": {"blocked_by": "none", "evidence": "test_l1_doctor.py#broken"},
    "S-003": {"blocked_by": "none", "evidence": "test_cli_steward.py#discover never adopts"},
    "S-004": {"blocked_by": "none", "evidence": "test_l1_toolstate.py#MANIFEST_DIGEST_MISMATCH"},
    "S-006": {"blocked_by": "none", "evidence": "test_l1_where.py#effective"},
    "S-007": {"blocked_by": "none", "evidence": "test_l1_where.py#VERSION_UNSATISFIED"},
    "S-008": {"blocked_by": "none", "evidence": "test_l1_rebuild.py#unmanaged"},
    "S-011": {"blocked_by": "undesigned"},
    "S-012": {"blocked_by": "unchecked-invariant"},
    "S-013": {"blocked_by": "none", "evidence": "test_l1_registry.py#payload"},
    "S-015": {
        "duplicate_of": "S-010",
        "note": (
            "与 S-010 是**同一个场景**（重复登记）：同为'project manifest 请求 machine scope'，期望也相同，"
            "只是措辞不同。规范方向取'被测试引用的那个'，即 test_l1_plan_routing.py 引用的 S-010。"
        ),
    },
    "S-016": {"blocked_by": "none", "evidence": "test_l1_sources.py#digest"},
    "S-017": {"blocked_by": "none", "evidence": "test_l1_doctor_steward.py#REFERENCE_STALE"},
    "S-023": {"blocked_by": "none", "evidence": "test_l1_lifecycle.py#retire"},
    "S-024": {"blocked_by": "none", "evidence": "test_l1_lifecycle.py#gc"},
    "S-025": {"blocked_by": "none", "evidence": "test_l1_toolstate.py#test_verify_detects_a_tampered_payload_and_does_not_repair"},
    "S-026": {"blocked_by": "none", "evidence": "test_l1_discovery.py#candidate"},
    "S-027": {"blocked_by": "undesigned"},
    "S-028": {"blocked_by": "none", "evidence": "test_cli_steward.py#digest"},
    "S-030": {"blocked_by": "none", "evidence": "test_l1_doctor_steward.py#stale"},
    "S-031": {"blocked_by": "undesigned"},
    # --- T: transaction cases that need a real artifact to interrupt.
    "T-001": {"blocked_by": "p4-real-backend"},
    "T-003": {"blocked_by": "p4-real-backend"},
    "T-004": {"blocked_by": "p4-real-backend"},
    "T-005": {"blocked_by": "p4-real-backend"},
    "T-013": {"blocked_by": "p4-real-backend"},
    "T-016": {"blocked_by": "p4-real-backend"},
    "T-017": {"blocked_by": "p4-real-backend"},
    # --- C: contract-conformance cases.
    "C-003": {"blocked_by": "none", "evidence": "test_l1_registry.py#test_only_one_active_binding_per_key_is_accepted"},
    "C-004": {"blocked_by": "none", "evidence": "test_l1_searchindex.py#index.db"},
    "C-005": {"blocked_by": "none", "evidence": "test_l1_transaction.py#REGISTERED"},
    "C-006": {"blocked_by": "none", "evidence": "test_l1_rebuild.py#audit"},
    "C-007": {"blocked_by": "undesigned"},
    "C-008": {"blocked_by": "undesigned"},
    "C-009": {"blocked_by": "undesigned"},
    "C-010": {"blocked_by": "none", "evidence": "test_cli_env.py#script"},
    "C-011": {"blocked_by": "p2-protected-state"},
    "C-012": {"blocked_by": "none", "evidence": "test_l1_desired.py#plan_blocked_by"},
    "C-013": {"blocked_by": "none", "evidence": "test_l1_skill.py#SKILL.md"},
    "C-014": {"blocked_by": "none", "evidence": "test_l1_extension.py#test_extension_does_not_touch_the_registry"},
    "C-015": {"blocked_by": "none", "evidence": "test_l1_desired.py#pin"},
    "C-016": {"blocked_by": "none", "evidence": "test_l1_discovery.py#unmanaged"},
    "C-017": {"blocked_by": "none", "evidence": "test_l1_planner.py#SCOPE_PROJECT"},
    "C-018": {"blocked_by": "none", "evidence": "test_l1_planner.py#SCOPE_DATA_ROOT"},
    "C-019": {"blocked_by": "none", "evidence": "test_l1_plan_routing.py#SIZE_ESTIMATE_UNAVAILABLE"},
    "C-021": {"blocked_by": "undesigned"},
    "C-022": {"blocked_by": "undesigned"},
    "C-023": {"blocked_by": "none", "evidence": "test_cli_env.py#test_env_list_reports_what_was_persisted"},
    "C-024": {"blocked_by": "none", "evidence": "test_cli_env.py#PERSISTENCE_TARGET_FORBIDDEN"},
    "C-025": {"blocked_by": "p2-protected-state"},
    "C-026": {"blocked_by": "none", "evidence": "test_l1_exposure.py#template"},
    "C-027": {"blocked_by": "none", "evidence": "test_cli_env.py#test_env_forget_dry_run_reports_without_changing_anything"},
    "C-028": {"blocked_by": "none", "evidence": "test_cli_env.py#test_env_forget_dry_run_reports_without_changing_anything"},
    "C-029": {"blocked_by": "none", "evidence": "test_cli_env.py#PERSISTENCE_REQUIRES_APPROVAL"},
    "C-030": {"blocked_by": "none", "evidence": "test_cli_env.py#script"},
}


def build_ledger(repo: Path) -> list[dict[str, Any]]:
    """The complete ledger: derived facts merged with the authored dispositions."""

    defined = definitions(repo)
    cited = citations(repo)
    ledger: list[dict[str, Any]] = []
    for key in sorted(defined, key=lambda k: (k[0], int(k[2:]))):
        rows = defined[key]
        named_by = sorted(cited.get(key, set()))
        entry: dict[str, Any] = {
            "id": key,
            "family": key[0],
            "status": "evidenced" if named_by else "uncited",
            "cited_by": named_by,
            "defined_in": sorted({row["document"] for row in rows}),
            "duplicate_of": None,
            "blocked_by": None,
            "evidence": None,
            "superseded": None,
            "note": None,
        }
        entry.update(DISPOSITIONS.get(key, {}))
        ledger.append(entry)
    return ledger


def undisposed(repo: Path) -> list[str]:
    """Uncited IDs with no authored disposition — the ledger would be silent about them."""

    problems: list[str] = []
    for entry in build_ledger(repo):
        if entry["status"] == "uncited" and entry["duplicate_of"] is None and entry["blocked_by"] is None:
            problems.append(entry["id"])
    return problems


def orphan_dispositions(repo: Path) -> list[str]:
    """Authored dispositions for IDs nothing defines any more."""

    defined = definitions(repo)
    return sorted(key for key in DISPOSITIONS if key not in defined)


def evidence_problems(repo: Path, ledger: list[dict[str, Any]] | None = None) -> list[str]:
    """Structural problems with the ledger's judgements (never their truth)."""

    problems: list[str] = []
    entries = build_ledger(repo) if ledger is None else ledger
    for entry in entries:
        key = entry["id"]
        duplicate, blocked = entry["duplicate_of"], entry["blocked_by"]

        if duplicate is not None:
            if blocked is not None:
                problems.append(f"{key}: a duplicate carries both duplicate_of and blocked_by")
            if duplicate not in {item["id"] for item in entries}:
                problems.append(f"{key}: duplicate_of names an ID nothing defines ({duplicate})")
            if entry["evidence"] is not None:
                problems.append(f"{key}: a duplicate must not carry its own evidence pointer")
        elif entry["status"] == "uncited":
            if blocked not in BLOCKED_BY_VALUES:
                problems.append(f"{key}: uncited with blocked_by={blocked!r}, which is not in the vocabulary")
            if blocked == "none":
                if entry["evidence"] is None:
                    problems.append(f"{key}: blocked_by=none claims nothing is missing but names no evidence")
                else:
                    problems.extend(_evidence_pointer_problems(repo, key, entry["evidence"]))
            elif entry["evidence"] is not None:
                problems.append(f"{key}: blocked_by={blocked!r} is a missing capability, so it cannot have evidence")

        if entry["duplicate_of"] is not None and entry["duplicate_of"] == key:
            problems.append(f"{key}: cannot duplicate itself")

    duplicates = {entry["id"]: entry["duplicate_of"] for entry in entries if entry["duplicate_of"]}
    for key, target in duplicates.items():
        if duplicates.get(target):
            problems.append(f"{key}: duplicate_of chains through {target} instead of naming the canonical one")

    return problems


def _evidence_pointer_problems(repo: Path, key: str, pointer: str) -> list[str]:
    """An evidence pointer is ``<test-file>#<literal token>`` and both halves must resolve.

    This is the only part of an authored judgement a guard can decide, and it is
    worth deciding: it catches a pointer to a test that was renamed, or to a
    token that was deleted, without pretending to judge whether the behaviour is
    really exercised.
    """

    if "#" not in pointer:
        return [f"{key}: evidence {pointer!r} must be spelled <test-file>#<token>"]
    name, token = pointer.split("#", 1)
    path = repo / "cli" / "tests" / name
    if not path.is_file():
        return [f"{key}: evidence names a test file that does not exist ({name})"]
    if token not in path.read_text(encoding="utf-8"):
        return [f"{key}: evidence token {token!r} does not appear in {name}"]
    return []


def row_marker_problems(repo: Path) -> list[str]:
    """Documented annotations the ledger's judgements depend on.

    An ID defined in two documents is a standing hazard (change one, miss the
    other) unless both rows say so — that is §46's lesson applied to scenario
    rows. A scenario whose expectation a later decision superseded is the same
    hazard in a different shape (§48's lesson: a document may not go on
    asserting what the project no longer does).
    """

    problems: list[str] = []
    defined = definitions(repo)
    for entry in build_ledger(repo):
        rows = defined[entry["id"]]
        if len(entry["defined_in"]) > 1:
            for row in rows:
                if DUAL_DEFINITION_MARKER not in row["row"]:
                    problems.append(
                        f"{entry['id']}: defined in {len(rows)} places but the row at "
                        f"{row['document']}:{row['line']} does not mention {DUAL_DEFINITION_MARKER}"
                    )
        if entry["superseded"] is not None:
            for row in rows:
                if SUPERSEDED_MARKER not in row["row"]:
                    problems.append(
                        f"{entry['id']}: expectation is superseded but the row at "
                        f"{row['document']}:{row['line']} does not say {SUPERSEDED_MARKER}"
                    )
        if entry["duplicate_of"] is not None:
            for row in rows:
                if DUPLICATE_MARKER not in row["row"]:
                    problems.append(
                        f"{entry['id']}: is a duplicate of {entry['duplicate_of']} but the row at "
                        f"{row['document']}:{row['line']} does not say {DUPLICATE_MARKER}"
                    )
    return problems
