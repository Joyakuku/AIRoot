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

The pointer rule is itself part of that structure, and §53 tightened it: a
pointer must name a **module-level test function**, not a bare substring. The
first version accepted any token that appeared anywhere in the file, which meant
it could not fail when the test it named was deleted — a check that cannot go
red. See :func:`_evidence_pointer_problems`.

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
    #
    # **Currently unused, and deliberately kept.** §49 added it because of one entry, and §55/§56/§57
    # have since closed every entry that used it. The category is not gone — "the rule is written down,
    # the code implements it, and no test watches it" is a shape that recurs, and one audit found three
    # instances in a single pass. Removing the value now would remove the vocabulary's ability to name
    # the next one honestly (it would have to be filed as `undesigned` or `none`, both of which would be
    # false), and would erase why a fifth member was ever needed.
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

#: The header row of a **scenario** table. Definitions live only in a table the document declares as
#: one, and every scenario table in both contracts declares itself with `| ID | 场景 | 预期 |`.
#:
#: This is the load-bearing half of the rule, forced by a mistake (draft §52.2-F4): the rule used to
#: be "the row's first cell is an ID", so a *discussion* table — §52.1's list of eight dispositions —
#: registered eight phantom definitions, surfacing as a "defined in 2 places" error whose message had
#: nothing to do with the cause. Writing those IDs in backticks fixes that one table; requiring the
#: header fixes the class.
_HEADER = re.compile(r"^\s*\|\s*ID\s*\|")

#: A row's first cell must be **only** the ID, optionally with the two suffixes the contract tables
#: use (`（新）`, `改写`). A second lock, inside the scenario table: a summary or annotated row is not
#: a definition even if it sits in the right table.
_DEFINITION = re.compile(r"^\s*\|\s*([PSTC])-(\d{3})\s*(?:（新）|改写)?\s*\|([^|]*)\|([^|]*)\|")
_RANGE = re.compile(
    r"\b([PSTC])-(\d{3})\s*(?:" + "|".join(re.escape(m) for m in RANGE_MARKERS) + r")\s*(?:\1-)?(\d{3})\b"
)
_SINGLE = re.compile(r"\b([PSTC])-(\d{3})\b")
_SID = re.compile(r"\bS-\d+(?:-\d+)+")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _clean(cell: str) -> str:
    return " ".join(cell.replace("**", "").split())


def _definition_rows(lines: list[str]) -> list[tuple[int, re.Match[str]]]:
    """(line number, match) for every definition row: only rows of a table headed ``| ID | … |``.

    Split out so the rule can be exercised on synthetic input — the failure it exists for (a
    discussion table registering definitions) is invisible on the real documents, which is exactly
    why it needed a test of its own.
    """

    rows: list[tuple[int, re.Match[str]]] = []
    in_table = False
    awaiting_separator = False
    for number, line in enumerate(lines, 1):
        if _HEADER.match(line):
            in_table, awaiting_separator = True, True
            continue
        if in_table and awaiting_separator:
            # The `|---|---|---|` row that follows a header.
            if re.match(r"^\s*\|[\s:|-]+\|\s*$", line):
                awaiting_separator = False
                continue
            in_table, awaiting_separator = False, False
        if in_table and not line.lstrip().startswith("|"):
            in_table = False
        if not in_table:
            continue
        match = _DEFINITION.match(line)
        if match is not None:
            rows.append((number, match))
    return rows


def definitions(repo: Path) -> dict[str, list[dict[str, str]]]:
    """Every scenario a document *defines*, keyed by ID.

    A definition is a row of a **scenario table** (header ``| ID | … |``) whose first cell is only the
    ID. Both halves were found by getting them wrong:

    * prose that names an ID (``验证与测试方案`` line 130: "P-003 和 P-013 是防止安全口径夸大的测试")
      is not a row at all;
    * a row of a table that merely *discusses* scenarios (draft §52.1, headed ``| 编号 | … |``) is not
      a definition — under the old "first cell is an ID" rule it silently added eight of them.
    """

    found: dict[str, list[dict[str, str]]] = {}
    for relative in DOCUMENTS:
        document = repo / relative
        lines = document.read_text(encoding="utf-8").splitlines()
        for number, match in _definition_rows(lines):
            family, digits, setup, expectation = match.groups()
            key = f"{family}-{int(digits):03d}"
            found.setdefault(key, []).append(
                {
                    "document": relative,
                    "line": number,
                    "setup": _clean(setup),
                    "expectation": _clean(expectation),
                    "row": lines[number - 1],
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
#
# Two rules have been added to this table by review, each because a hand audit found the previous
# shape unable to be wrong (draft §52 then §53):
#
#   * every `undesigned` entry names a **witness** — what goes red the moment the missing
#     capability appears — or states why it cannot have one;
#   * every `evidence` / `witness` pointer names a **module-level test function**, never a bare
#     token. §53 audited the 41 `blocked_by=none` entries one by one and found about thirty
#     pointers resolving to a docstring, a comment, an import line, or a *different* test — the
#     judgements were mostly right, the pointers were not.
#
# Where an expectation has halves in different tests, the pointer names the one covering the
# **dangerous** direction and the `note` names the others and any half that nothing asserts.
# A `note` is free text and no guard checks it; it exists so that "we only proved half of this"
# is written down rather than implied.
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
    "P-003": {
        "blocked_by": "none",
        "evidence": "test_l1_toolstate.py#test_a_clean_path_has_no_violation",
        "note": (
            "该测试把一个**陌生用户项**（`C:\\Users\\me\\bin`）放进 user PATH，断言 0 violation、"
            "`path_written is False`——即 AIROOT 既不认领它、也不动它。期望里的'标记 unmanaged'"
            "在本面**没有对应字段**（`path verify` 只报违规，不给条目分类），所以真正的半边是'不声称它受保护'"
        ),
    },
    "P-018": {
        "blocked_by": "none",
        "evidence": "test_l1_registry.py#test_same_instance_id_with_a_different_digest_is_refused",
        "note": (
            "'不得覆盖旧 store instance'由它断言（同 id 换 digest → 拒绝）；"
            "另一半'payload 必须 immutable'由 `test_instance_identity_and_digest_are_immutable` 覆盖。"
            "`tools` 只生成 binding 是**结构性**的：binding 只存 `instance_id`，payload 只在 `store/<instance_id>`"
        ),
    },
    "P-019": {
        "blocked_by": "none",
        "evidence": "test_l1_extension.py#test_extension_does_not_touch_the_registry",
        "note": (
            "危险方向是'扩展自己去 adopt'，由它断言：扩展跑完 probe/status/invoke 后 `declared_state_digest` 不变。"
            "'只产生 unmanaged candidate'那一半由 `test_l1_discovery.py#test_unmatched_object_stays_unmanaged` 覆盖"
        ),
    },
    "P-020": {
        "blocked_by": "none",
        "evidence": "test_l1_toolstate.py#test_verify_refuses_an_unknown_instance",
        "note": (
            "reference 没有 `instance_id`，`tool verify <reference-id>` 因此是 `NOT_FOUND`——"
            "它**根本没有**'把引用升级为 managed'的路径。相邻规则由 `test_a_reference_is_never_uninstallable`"
            "（test_l1_lifecycle.py）与 `test_the_three_verbs_do_not_change_the_generation` 覆盖"
        ),
    },
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
    "S-001": {
        "blocked_by": "none",
        "evidence": "test_l1_desired.py#test_an_unsatisfied_pin_is_reported",
        "note": (
            "'报告 policy drift'由它断言；'可生成 plan'由 `test_cli_pin_offers_a_real_plan_when_a_source_exists`，"
            "'不自动安装'的观察点是 `test_cli_pin_writes_nothing_to_the_registry`——注意它证明的是"
            "*pin 不改 declared*，不是'没有安装路径'"
        ),
    },
    "S-002": {
        "blocked_by": "none",
        "evidence": "test_l1_doctor.py#test_missing_payload_is_reported_as_broken",
    },
    "S-003": {
        "blocked_by": "none",
        "evidence": "test_cli_steward.py#test_discover_record_persists_only_non_owning_observations",
    },
    "S-004": {
        "blocked_by": "none",
        "evidence": "test_l1_toolstate.py#test_verify_detects_a_tampered_payload_and_does_not_repair",
        "note": (
            "'状态为 broken + 保留证据 + 不修复'由它断言（`MANIFEST_DIGEST_MISMATCH` / 退出码 3 / "
            "`repaired is False` / 字节未动）。期望后半句'`where` 不返回 healthy'**没有**断言——"
            "`where` 对 broken 绑定的行为由 `test_l1_where.py#test_broken_managed_binding_is_reported_not_skipped` 覆盖，"
            "但那条测的是 machine 绑定，不是 digest 漂移后的同一条链"
        ),
    },
    # S-006 used to sit here as `unchecked-invariant` — §53's most serious finding: the frozen
    # constant "Zone W 永不进入 machine PATH" had no execution point at all, so it was unreachable
    # *and* unenforced. ADR-0022 decided it and §56 landed the filter plus a test that names the
    # scenario, so the disposition is **deleted rather than rewritten** (same treatment as C-021 in
    # §52 and C-024/C-026 in §55): leaving a "nothing checks this" note beside a test that now checks
    # it is the kind of record that reads as information while carrying none.
    "S-007": {
        "blocked_by": "none",
        "evidence": "test_l1_where.py#test_multiple_versions_coexist_but_only_one_is_active",
        "note": (
            "'两个版本共存 + 返回明确 instance（不是裸 python）'由它断言；"
            "约束解析那一半（`>=2.0` 不满足时如实报 `VERSION_UNSATISFIED`，而不是退回任意版本）由 "
            "`test_version_constraint_selects_or_reports_unsatisfied` 覆盖"
        ),
    },
    "S-008": {
        "blocked_by": "none",
        "evidence": "test_l1_rebuild.py#test_unmanaged_references_are_reported_not_adopted",
    },
    "S-011": {
        "blocked_by": "undesigned",
        "witness": "test_l0_consistency.py#test_every_declared_unimplemented_command_is_still_unimplemented",
        "note": "`reconcile` 在 agents/airoot.json 的未实现清单里，那条守卫断言清单里的每条都仍未实现",
    },
    # S-012 used to sit here as `unchecked-invariant` — the entry §49 created that value *for*, and
    # the last one left after §55 and §56 closed theirs. §57 verified it was genuine test debt (the
    # guard exists on two paths and no test called either) and wrote the tests, so the disposition is
    # deleted rather than rewritten. `status` is a measurement and flips on its own.
    #
    # With this, **no entry uses `unchecked-invariant` any more**. The value stays in
    # `MISSING_CAPABILITIES`: the category recurs (one audit found three instances of "documented,
    # implemented, unwatched" at once), and deleting the value would also delete the record of why
    # the vocabulary ever needed a fifth member. See the note on `MISSING_CAPABILITIES`.
    "S-013": {
        "blocked_by": "none",
        "evidence": "test_l1_registry.py#test_adding_the_same_instance_twice_is_idempotent",
        "note": (
            "'不会把同一 payload 复制两份'由它断言；'binding/view 可重建'由 "
            "`test_rebinding_switches_the_single_active_row`（同一文件）与 §24 的 `rebuild` 面覆盖。"
            "`tools`/`env` 里没有 payload 是**结构性**的：payload 路径只由 `store/<instance_id>` 推导"
        ),
    },
    "S-015": {
        "duplicate_of": "S-010",
        "note": (
            "与 S-010 是**同一个场景**（重复登记）：同为'project manifest 请求 machine scope'，期望也相同，"
            "只是措辞不同。规范方向取'被测试引用的那个'，即 test_l1_plan_routing.py 引用的 S-010。"
        ),
    },
    "S-016": {
        "blocked_by": "none",
        "evidence": "test_l1_transaction.py#test_source_drift_after_planning_is_refused",
        "note": (
            "原指针指 `test_l1_sources.py#digest`——那里只有 import 行与 docstring。真正的行为在事务面："
            "规划之后 source 变化 → `ROLLED_BACK` / `DIGEST_MISMATCH` / bindings 为空 / generation 不变"
        ),
    },
    "S-017": {
        "blocked_by": "none",
        "evidence": "test_l1_doctor_steward.py#test_drift_is_detected_by_re_observation",
        "note": "与 S-030 的分工：这条是**重观测发现漂移**（版本变了即报），S-030 是对象被删（`REFERENCE_STALE`）",
    },
    "S-023": {
        "blocked_by": "none",
        "evidence": "test_l1_lifecycle.py#test_a_retired_instance_is_no_longer_selected",
        "note": "'payload 保留到 GC 条件满足'由同文件的 `test_retire_clears_the_binding_and_keeps_the_payload` 覆盖",
    },
    "S-024": {
        "blocked_by": "none",
        "evidence": "test_l1_lifecycle.py#test_an_unfinished_transaction_blocks_collection",
    },
    "S-025": {"blocked_by": "none", "evidence": "test_l1_toolstate.py#test_verify_detects_a_tampered_payload_and_does_not_repair"},
    "S-026": {
        "blocked_by": "none",
        "evidence": "test_cli_steward.py#test_discover_record_persists_only_non_owning_observations",
        "note": (
            "'不写入 managed binding'由它断言（只记 `unmanaged`，且 `management != \"external_reference\"`）；"
            "'不生成 planned 状态'的邻居是 `test_adopt_import_is_not_implemented`"
        ),
    },
    "S-027": {
        "blocked_by": "undesigned",
        "no_witness_reason": (
            "没有 `env\\runtimes` 这个视图，也没有任何声明说它应该存在，所以没有任何测试会因为"
            "'它出现了'而变红。要造一个证人，先得决定这个视图存不存在（那是设计，不是守卫）"
        ),
    },
    "S-028": {
        "blocked_by": "none",
        "evidence": "test_l1_discovery.py#test_scan_is_read_only",
        "note": (
            "'文件树 digest 完全不变'由它断言（`tree_digest` 前后相等）；PATH 那一半由 "
            "`test_l1_toolstate.py#test_cli_path_verify_never_writes` 覆盖（`path_written is False`）"
        ),
    },
    "S-030": {
        "blocked_by": "none",
        "evidence": "test_l1_doctor_steward.py#test_a_reference_whose_object_vanished_is_stale",
    },
    "S-031": {
        "blocked_by": "undesigned",
        "witness": "test_cli_steward.py#test_forget_drops_the_record_and_keeps_the_file",
        "note": "`forget` 的输出里就有 `project_manifest_check: not_implemented_before_p6`，那条测试断言了它",
    },
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
    "C-004": {
        "blocked_by": "none",
        "evidence": "test_l1_searchindex.py#test_the_index_writes_nothing_outside_its_cache_directory",
        "note": (
            "'规范位置必须是 `cache\\search`'由它断言（整棵树里唯一被写的文件就是 `cache/search/index.db`）。"
            "前半句'写入 `state\\search` → **ACL/path conformance 失败**'没有断言：那条判据是 P2 的 ACL 基线"
            "（`DATA_ROOT_ACL_DRIFT` 至今不发），P1 只能用'写了什么'证明规范位置"
        ),
    },
    "C-005": {
        "blocked_by": "none",
        "evidence": "test_l1_transaction.py#test_registered_instance_is_inactive_until_active_bound",
        "note": (
            "'REGISTERED 不得被选中'由它断言（`bindings(active_only=True) == []`，消息就是 'REGISTERED must never "
            "be selectable'）。'被 **launcher** 查询'那一半没有对象：P1 **不写任何 launcher 文件**（§8），"
            "所以 launcher 面无从断言"
        ),
    },
    "C-006": {
        "blocked_by": "none",
        "evidence": "test_l1_rebuild.py#test_rebuild_repairs_a_stale_audit_projection",
        "note": (
            "由它断言：audit 投影被改坏 → `AUDIT_PROJECTION_DRIFT`；`rebuild` 之后该码消失——"
            "即 SQLite event 是权威、投影可重建。相邻两条在 doctor 面："
            "`test_l1_doctor.py#test_audit_projection_drift_is_reported` 与 `#test_audit_projection_is_rebuildable_from_events`"
        ),
    },
    "C-007": {
        "blocked_by": "undesigned",
        "no_witness_reason": (
            "没有 `airoot update` 这个命令，但也没有任何测试断言它不存在（`not_implemented` 清单里没有它，"
            "因为那条清单管的是**已登记的命令路径**）。加一条 `\"update\" not in known_commands()` 就能造出证人——"
            "本轮不造，是为了不让台账好看这件事自己生出一条自证的断言"
        ),
    },
    "C-008": {
        "blocked_by": "undesigned",
        "witness": "test_l0_consistency.py#test_every_declared_unimplemented_command_is_still_unimplemented",
        "note": "`root relocate` 在未实现清单里；实现了它，证人就红",
    },
    "C-009": {
        "blocked_by": "undesigned",
        "witness": "test_cli_steward.py#test_adopt_import_is_not_implemented",
    },
    "C-010": {
        "blocked_by": "none",
        "evidence": "test_cli_env.py#test_env_activate_prints_a_powershell_script_and_persists_nothing",
        "note": (
            "'只输出脚本'由它断言（脚本文本 + `environment_persist_records() == []`）；"
            "'或由 `exec` 创建子进程'由 `test_exec_injects_the_environment_into_one_child_process` 覆盖。"
            "**'不得声称父进程已改变'是措辞禁令，没有直接断言**：测试能观察的是'没有持久化记录'，"
            "而不是'输出里没有这句话'（pytest 自己的 `os.environ` 也未被断言）"
        ),
    },
    "C-011": {"blocked_by": "p2-protected-state"},
    "C-012": {
        "blocked_by": "none",
        "evidence": "test_l1_desired.py#test_cli_pin_reports_when_no_plan_can_be_built",
        "note": (
            "'没有可信来源 → 只能出诊断、`plan` 为 null、`plan_blocked_by=NOT_FOUND`'由它断言。"
            "**'不能 install'与'不能提升到 machine scope'两半没有断言**：前者没有安装入口可拒（desired 层只写 "
            "`state/desired.json`），后者属 P2（`--scope machine` 报 `PRIVILEGE_REQUIRED`，见 C-025）"
        ),
    },
    "C-013": {
        "blocked_by": "none",
        "evidence": "test_l1_skill.py#test_the_skill_entry_lives_at_the_skill_root",
        "note": (
            "'SKILL.md 必须在 Skill 根、不得在 `cli/` 下'由它断言。"
            "**'Skill 加载失败'那一半没有对象**：这个项目里**没有 Skill 加载器**——`SKILL.md` 由 agent 读，"
            "不是被代码载入的；所以'把目录误识别为可执行 Skill'没有可失败的代码路径"
        ),
    },
    "C-014": {
        "blocked_by": "none",
        "evidence": "test_l1_extension.py#test_extension_does_not_touch_the_registry",
        "note": (
            "'Extension implementation identity 与 managed payload identity 必须分离'在今天**是结构性的**："
            "extension manifest 里没有 instance/identity 字段，schema 也没有这个属性，所以'声明不同生命周期'"
            "根本无处安放。已断言的是它的后果——扩展跑完 `declared_state_digest` 不变。"
            "**没有**测试去构造一个'复用 instance identity 的 manifest'并期待被拒（那种输入今天无法表达）"
        ),
    },
    "C-015": {
        "blocked_by": "none",
        "evidence": "test_l1_desired.py#test_cli_pin_writes_nothing_to_the_registry",
        "note": (
            "'不能直接改 launcher 或覆盖 store'由它断言（pin 后 registry 一个字节不变）；"
            "'先生成新的 selection/generation plan'由 `test_cli_pin_offers_a_real_plan_when_a_source_exists` 覆盖"
        ),
    },
    "C-016": {
        "blocked_by": "none",
        "evidence": "test_l1_discovery.py#test_unmatched_object_stays_unmanaged",
        "note": (
            "**原指针把输入类型说宽了**：这条测试放的是一个 PE（`mystery.exe`），不是批处理/安装器/脚本——"
            "本次复核翻遍 `test_l1_discovery.py`，**没有任何测试用 `.bat`/`.cmd`/安装器作为输入**。"
            "期望仍然成立，但成立的理由不同：发现面**只有 PE 静态探测**（`probe_pe`），没有执行路径，"
            "这一点由 `test_scan_is_read_only` 与 `test_scan_never_deletes_excluded_or_unmatched` 断言"
        ),
    },
    "C-017": {
        "blocked_by": "none",
        "evidence": "test_l1_planner.py#test_project_declaration_wins_and_is_never_asked",
    },
    "C-018": {
        "blocked_by": "none",
        "evidence": "test_l1_planner.py#test_generic_tool_goes_to_the_data_root_without_asking",
    },
    "C-019": {
        "blocked_by": "none",
        "evidence": "test_l1_plan_routing.py#test_dry_run_reports_the_target_size_and_approval_without_writing",
    },
    # C-021 used to sit here as `undesigned`, and that was **wrong** (draft §52.2-F1): `decide_scope`
    # consults `.ai/tooling.json` first and returns `confirmation=False`, `origin="memory"`, and a test
    # was already exercising it. The fix was not a better note — it was to have that test *name* the
    # scenario, which is what the ledger measures. See `test_l1_planner.py`'s recorded-choice test.
    "C-022": {
        "blocked_by": "undesigned",
        "witness": "test_l0_consistency.py#test_the_tooling_memory_is_read_only_in_the_code_and_not_just_in_the_prose",
        "note": (
            "注记改准：实现的是「AIROOT 没有任何写入口」，**不是**「谁写它就拒绝」。§51 的普查钉住前者"
            "（加了写入口它就会红）。要让第三方（Skill/Agent 直接改文件）的写入被**拒绝**，前提是先有一个"
            "会写它的通道，而那属 P2 的人工批准通道（ADR-0004 §12.2）——所以这条仍算 `undesigned`"
        ),
    },
    "C-023": {
        "blocked_by": "none",
        "evidence": "test_cli_env.py#test_env_list_reports_what_was_persisted",
        "note": (
            "'写入成功'由它断言（持久化后 `env list` 报出 JAVA_HOME 与 Path）；"
            "'值指向数据根真实路径'由 `test_env_persist_dry_run_builds_the_plan_and_writes_nothing` 覆盖。"
            "**'新进程可见'在 pytest 里没有断言**——它断的是进程内可达；真正跨进程的观察在 "
            "`cli/tests/real_machine_acceptance.py`（`exec ... cmd /c echo %JAVA_HOME%`）"
        ),
    },
    # C-024 and C-026 used to sit here — the two §13.2 hard rules that §53's audit found implemented
    # but unwatched (C-024 as `blocked_by=none` pointing at a test about a *different* rule that shares
    # the reason code; C-026 as `unchecked-invariant`). §55 paid both debts by writing the missing
    # tests, so the dispositions are **deleted rather than rewritten** — the same treatment §52 gave
    # C-021. `status` is a measurement and flips to `evidenced` on its own once a test names the ID;
    # keeping a stale "nothing checks this" note next to a test that now checks it would be exactly the
    # kind of record that reads as information while carrying none.
    "C-025": {"blocked_by": "p2-protected-state"},
    "C-027": {
        "blocked_by": "none",
        "evidence": "test_cli_env.py#test_env_forget_dry_run_reports_without_changing_anything",
        "note": (
            "它断言的是**dry run 的语义**（报出要还原哪些变量、且不消费记录）。"
            "'精确恢复旧值、其它变量一字不动'的 apply 半边由 `test_l1_exposure.py#"
            "test_apply_then_forget_restores_the_exact_previous_value` 覆盖"
        ),
    },
    "C-028": {
        "blocked_by": "undesigned",
        "no_witness_reason": (
            "`env forget --all` **这个命令形式不存在**：`cmd_env_forget` 的 parser（cli.py）只接受 "
            "`external_id` / `--variable` / `--dry-run`，`agents/airoot.json` 登记的也是 `[\"env\", \"forget\", "
            "\"<external-id>\"]`，`not_implemented` 清单里没有它（那条清单管命令路径，`env forget` 本身是实现的）。"
            "原记 `blocked_by=none` 并指向 dry-run 测试，是**本次复核抓到的第一条判断错误**。"
            "证人造不出来：加一条'`--all` 不被接受'的断言只会在**别人把它实现出来**时变红，"
            "而那正是'台账好看这件事自己生出一条自证断言'——与 C-007 同一处置"
        ),
    },
    "C-029": {
        "blocked_by": "none",
        "evidence": "test_cli_env.py#test_env_persist_without_a_token_stops_at_the_approval_boundary",
    },
    "C-030": {
        "blocked_by": "none",
        "evidence": "test_cli_env.py#test_env_activate_prints_a_powershell_script_and_persists_nothing",
        "note": (
            "与 C-010 是**同一行为的两种措辞**（§16.4 与 §16.4 的引用），所以共用同一个证据函数——"
            "这不是重复登记（两条的定义行不同、期望不同：C-010 讲'试图修改父 shell'，"
            "C-030 讲'子进程不得声称改变了父进程'），而是同一断言同时支撑两条期望。"
            "'不得声称'那一半与 C-010 一样没有直接断言"
        ),
    },
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
            # A *witness* answers a different question from `evidence` (draft §52): `evidence` says
            # "this behaviour is exercised somewhere", `witness` says "here is what will go red the
            # moment the missing capability appears". Every `undesigned` entry needs one or an
            # explicit reason it cannot have one.
            "witness": None,
            "no_witness_reason": None,
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

        if blocked == "undesigned":
            problems.extend(_undesigned_problems(repo, key, entry))

        if entry["duplicate_of"] is not None and entry["duplicate_of"] == key:
            problems.append(f"{key}: cannot duplicate itself")

    duplicates = {entry["id"]: entry["duplicate_of"] for entry in entries if entry["duplicate_of"]}
    for key, target in duplicates.items():
        if duplicates.get(target):
            problems.append(f"{key}: duplicate_of chains through {target} instead of naming the canonical one")

    return problems


def _undesigned_problems(repo: Path, key: str, entry: dict[str, Any]) -> list[str]:
    """An `undesigned` claim needs a witness, or a stated reason it cannot have one (draft §52).

    `witness` is not a second evidence pointer: it names the thing that will **go red** the moment the
    missing capability appears (or the moment "missing" stops being true). That is the whole value —
    a note saying "not implemented" is a claim nothing checks, which is how §35's unimplemented list
    went stale in the first place.

    Why only `undesigned` and not the `p2`/`p4`/`p3`/`unchecked-invariant` entries: for those, "the
    capability is absent" is not a checkable statement about *this* tree (an ACL does not exist here
    because the whole protected mode does not), so requiring a witness would produce a dozen reasons
    that all say "because P2 is not built yet". Stated as a boundary rather than left implicit.
    """

    problems: list[str] = []
    witness, reason = entry["witness"], entry["no_witness_reason"]
    if witness is None and not str(reason or "").strip():
        problems.append(f"{key}: undesigned with neither a witness nor a reason it cannot have one")
    if witness is not None and str(reason or "").strip():
        problems.append(f"{key}: names a witness and also says it cannot have one; pick one")
    if witness is not None:
        problems.extend(_evidence_pointer_problems(repo, key, witness, kind="witness"))
    return problems


def _evidence_pointer_problems(repo: Path, key: str, pointer: str, *, kind: str = "evidence") -> list[str]:
    """A pointer is ``<test-file>#<module-level test function>`` and that function must exist.

    §53 replaced a **substring** rule with this one. The old rule — ``token in path.read_text()`` —
    could not fail in the dangerous direction: a pointer of ``test_l1_rebuild.py#audit`` stayed
    "valid" after the test it claimed to point at was deleted, because the word ``audit`` also
    occurs in an unrelated local variable. That is §50's tautology in a new place: a check that
    cannot go red is not a check.

    Auditing the 41 ``blocked_by=none`` entries by hand found that the *judgements* were almost all
    right and the *pointers* were the weak half — about thirty of them resolved to a docstring, a
    comment, an import line, or a different test entirely. Requiring a real function name makes the
    pointer expire the moment the test that carries the behaviour is renamed or deleted, which is
    the only thing a pointer is for. It also forces the author to decide: if no single function
    covers an expectation, name the one covering the **dangerous** direction and say in the ``note``
    which halves live elsewhere. Every such note written under this rule is a place where a
    half-covered expectation is now visible instead of implied.
    """

    if "#" not in pointer:
        return [f"{key}: {kind} {pointer!r} must be spelled <test-file>#<test-function>"]
    name, token = pointer.split("#", 1)
    path = repo / "cli" / "tests" / name
    if not path.is_file():
        return [f"{key}: {kind} names a test file that does not exist ({name})"]
    if not re.fullmatch(r"test_\w+", token):
        return [
            f"{key}: {kind} {token!r} is not a test function name — a bare token survives the "
            f"deletion of the test it claims to point at (draft §53)"
        ]
    if not re.search(rf"(?m)^def {re.escape(token)}\(", path.read_text(encoding="utf-8")):
        return [f"{key}: {kind} names a test function that {name} does not define ({token})"]
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
