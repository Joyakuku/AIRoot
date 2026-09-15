# AIROOT v0.3：诊断码与 Reason Code 表

本文件是 `docs/AIROOT-v0.3-验证与测试方案.md` §8 引用、但文档组从未定义的
**D1–D10 不变量**定义，以及**退出码 0–9 与 reason code 的权威映射**。

权威来源是代码：`cli/app/airoot/exits.py`（`REASON_EXIT`）与
`cli/app/airoot/caps/doctor.py`（`INVARIANTS`）。测试
`test_health_and_broken_codes_map_to_documented_exit_codes` 断言**每个诊断码都有退出码
映射**，`cli/tests/fixtures/golden/reason_code_table.json` 是给 Rust 版对照的机器可读副本。
修改代码后请用 `python cli/tests/dump_tables.py` 重新导出本文件的两张表并同步。

## 1. 退出码 → reason code

退出码由命令的**最终状态**决定，任何子模块都不得自行发明。

| 退出码 | 含义 | reason code |
|---:|---|---|
| 0 | healthy/success | `CURRENT_PROCESS_ENV_OLD`, `POLICY_ONLY_MODE`, `REFERENCE_UNPROBED`, `SIZE_ESTIMATE_UNAVAILABLE`, `SUCCESS`, `UNMANAGED_OBJECT_PRESENT`, `WHITELIST_REVISION_STALE` |
| 1 | not found | `NOT_FOUND`, `VERSION_UNSATISFIED` |
| 2 | degraded or drift | `AUDIT_PROJECTION_DRIFT`, `CHILD_PROCESS_FAILED`, `CURRENT_SOURCE_DEGRADED`, `DATA_ROOT_ACL_DRIFT`, `DESIRED_NOT_SATISFIED`, `DEGRADED`, `DRIFT_DETECTED`, `EXTENSION_CANCELLED`, `EXTENSION_HEALTH_DEGRADED`, `EXTENSION_TIMEOUT`, `INSTALL_IO_FAILED`, `ORPHANED_STORE_INSTANCE`, `PAYLOAD_OUTSIDE_STORE`, `REFERENCE_DRIFTED`, `REFERENCE_IN_USE`, `REFERENCE_STALE`, `REGISTRY_PROJECTION_STALE`, `PATH_EXPOSURE_VIOLATION`, `SEARCH_FALLBACK_USED`, `SEARCH_INDEX_DEGRADED`, `SEARCH_JOURNAL_GAP`, `SEARCH_PERMISSION_FILTERED`, `SEARCH_RESULT_STALE`, `SEARCH_ROOT_UNAVAILABLE`, `SEARCH_TIMEOUT`, `SESSION_STATE_STALE`, `STALE_GENERATION` |
| 3 | broken | `BINDING_TARGET_MISSING`, `BROKEN`, `CONFLICT_MANAGED_BROKEN`, `EXTERNAL_REFERENCE_DRIFTED`, `MANIFEST_DIGEST_MISMATCH`, `MULTIPLE_ACTIVE_BINDINGS`, `PAYLOAD_MISSING`, `REGISTRY_INTEGRITY_FAILED` |
| 4 | approval required/expired | `APPROVAL_EXPIRED`, `APPROVAL_REPLAYED`, `APPROVAL_REQUIRED`, `APPROVAL_REVOKED`, `INVALID_APPROVAL`, `PERSISTENCE_REQUIRES_APPROVAL`, `POLICY_REVISION_MISMATCH`, `SCOPE_CONFIRMATION_REQUIRED`, `SCOPE_UPGRADE_REQUIRES_APPROVAL` |
| 5 | privilege required | `ACL_MISMATCH`, `PRIVILEGE_REQUIRED` |
| 6 | transaction recovery required | `DATA_ROOT_MISSING`, `DATA_ROOT_VOLUME_MISMATCH`, `JOURNAL_TRUNCATED`, `PENDING_TRANSACTION`, `RECOVERY_REQUIRED`, `REGISTRY_MISSING`, `ROOT_MARKER_INVALID`, `ROOT_MARKER_MISSING`, `VOLUME_IDENTITY_MISMATCH` |
| 7 | invalid plan or provenance | `DIGEST_MISMATCH`, `ILLEGAL_TRANSITION`, `INSTANCE_CONFLICT`, `INVALID_PLAN`, `OWNERSHIP_REQUIRED`, `PROVENANCE_FAILED`, `UNSUPPORTED_BACKEND` |
| 8 | invalid input/schema | `ENVIRONMENT_PERSIST_NOT_FOUND`, `EXTENSION_INPUT_INVALID`, `EXTENSION_OUTPUT_INVALID`, `INVALID_INPUT`, `PATH_ESCAPES_ROOT`, `PERSISTENCE_TARGET_FORBIDDEN`, `REPARSE_POINT_REJECTED`, `ROOT_NOT_RESOLVED`, `SCHEMA_UNSUPPORTED`, `SEARCH_CURSOR_INVALID`, `SEARCH_QUERY_INVALID`, `SELF_VALIDATION_FAILED`, `UNC_NOT_ALLOWED` |
| 9 | extension unavailable | `CAPABILITY_NOT_DECLARED`, `EXTENSION_DEPENDENCY_MISSING`, `EXTENSION_MANIFEST_INVALID`, `EXTENSION_NOT_FOUND`, `EXTENSION_OPERATION_UNKNOWN`, `EXTENSION_PERMISSION_DENIED`, `EXTENSION_SIDE_EFFECT_BLOCKED`, `EXTENSION_UNAVAILABLE`, `EXTENSION_VERSION_UNSUPPORTED`, `SEARCH_BACKEND_UNAVAILABLE`, `SEARCH_NOT_READY` |

`CURRENT_SOURCE_DEGRADED`、`DATA_ROOT_*`、`REFERENCE_*`、`UNMANAGED_OBJECT_PRESENT`、
`WHITELIST_REVISION_STALE`、`CAPABILITY_NOT_DECLARED`、`OWNERSHIP_REQUIRED`、
`PERSISTENCE_*`、`SCOPE_*`、`SIZE_ESTIMATE_UNAVAILABLE`、`REFERENCE_IN_USE` 是 **ADR-0004
管家模型草案新增的码**。它们已注册在 `exits.py`（因此都有退出码映射），但**部分尚未被任何
命令发射**——随施工顺序逐项生效。

说明：

- **`OK` 不存在**。文档 §15.6 的成功码写作 `OK`，但它只有 2 个字符，无法满足所有
  Schema 的 `reason_code` 模式 `^[A-Z][A-Z0-9_]{2,63}$`；P1 发射 `SUCCESS`（ADR-0003）。
- `CURRENT_PROCESS_ENV_OLD` 的退出码是 **0**：结果可用，只是当前 shell 还没刷新。
- `PRIVILEGE_REQUIRED` / `ACL_MISMATCH`（5）在 P1 不会被发射——P1 没有 ACL 与 broker；
  保留映射以便 P2 直接使用。
- `SEARCH_RESULT_STALE`（2）与其余 `SEARCH_*` 码由搜索协议面（契约草案 §31）与 crawl 建的索引
  （§32）发射：`SEARCH_FALLBACK_USED` = 回答来自实时遍历；`SEARCH_RESULT_STALE` = 索引比调用方要的
  `max_staleness_ms` 更旧；`SEARCH_INDEX_DEGRADED` = 索引损坏或只覆盖了一部分 root；
  `SEARCH_NOT_READY` = 还没有索引（`search status` 的诚实回答）。协议 §6.6 的退出码表逐条照抄。
- 通用 `EXTENSION_*` 家族（协议 §6.6 下半段）**协议文档只列了码、没给退出码**，本项目的映射由
  ADR-0017 给出并遵守既有约定：选中的实现不可调用 → 9；调用方输入错 → 8；超时/取消/健康度下降 →
  2（结果不完整但不是坏掉的扩展）；**扩展返回的文档不符合 published schema → 8**（与
  `SELF_VALIDATION_FAILED` 同族：输入没问题，**输出**不合法）。
  `EXTENSION_NOT_FOUND` 与 `EXTENSION_UNAVAILABLE` 是两条码：前者是"没有任何扩展提供这个能力"，
  后者是"选中的扩展当前不可调用"。
- `DESIRED_NOT_SATISFIED`（2）由 `doctor` 发射：`state/desired.json` 里有 pin，而当前的 active
  binding 不满足它（或 manifest 不可读，无法判断）。**这不是 `DRIFT_DETECTED`**——后者是观测与声明
  不一致，这条是**意图**与声明不一致，下一步该动的文件不同。`doctor` 绝不自动修补。
- `PATH_EXPOSURE_VIOLATION`（2）由 `airoot path verify` 发射：PATH 上出现了违反冻结规则的东西
  （重复的 AIROOT 条目、`store/` 版本目录、非授权的 AIROOT 目录）。**这是环境偏离契约，不是 AIROOT
  自身损坏**；严重度在 `findings[].severity` 里，`path verify` 永不写 PATH。
- `SESSION_STATE_STALE`（2）由 `env activate`/`env deactivate` 发射：会话栈记录时的 generation 与
  现在不一致（或栈文件不可读/版本不符）。**不是输入错误**——会话本身没写错，是世界变了；
  与 `CURRENT_PROCESS_ENV_OLD` 同属"事实变了"这一类。
- `CHILD_PROCESS_FAILED`（2）只由 `airoot exec` 发射：AIROOT 自己的操作成功了，它启动的
  子进程失败了。子进程的状态码放在 `exit_status` 字段里，**绝不泄漏进 AIROOT 的退出码**——
  冻结的退出码空间只有 0–9。
- `ENVIRONMENT_PERSIST_NOT_FOUND`（8）由 `airoot env forget` 发射：没有任何已记录的持久化
  环境可供还原（`forget` 是还原，不是无条件删除）。
- **`CONFLICT_MANAGED_BROKEN`（3）在 steward-first 之后不再被 `where` 发射**（ADR-0006）：
  "owned 坏了 + reference 健康"现在是正常降级 `CURRENT_SOURCE_DEGRADED`（2）。
  该码仍注册、仍表示"已声明对象不可安全使用"这一语义，保留给 policy 显式要求冲突的将来用途，
  以及权威映射表的向下兼容。
- `SELF_VALIDATION_FAILED`（8）表示**本实现自己产出的文档没通过 Schema 校验**，属于
  实现缺陷，绝不能被当作领域失败被静默回滚。
- `SEARCH_INDEX_DEGRADED` / `SEARCH_RESULT_STALE`（2）除由 `search` 发射外，也由 **`doctor` 的 D7**
  发射（契约草案 §33）：前者是索引读不出来，后者是索引比 `index.max_age_ms` 更旧。
  **没有索引时一条都不报**——没建索引不是缺陷，否则每台没用过搜索的机器都会多一条噪音；
  `doctor` 也**绝不自己去建索引**（诊断只报告，不做全盘遍历）。

## 2. D1–D10 不变量（doctor）

| 编号 | 不变量 | 诊断码 |
|---|---|---|
| D1 | root 身份可证明（marker + 卷身份）；**每个数据根的路径与卷身份也可证明** | `ROOT_MARKER_MISSING`, `ROOT_MARKER_INVALID`, `VOLUME_IDENTITY_MISMATCH`, `DATA_ROOT_MISSING`, `DATA_ROOT_VOLUME_MISMATCH`, `DATA_ROOT_ACL_DRIFT` |
| D2 | declared state 可读且自洽 | `REGISTRY_MISSING`, `REGISTRY_INTEGRITY_FAILED`, `SCHEMA_UNSUPPORTED` |
| D3 | **两个所有权域两种证明**：owned payload 存在且在 `--verify` 时对得上安装期 digest 基线；steward reference 与一次**有界只读重观测**对比（版本/活跃版本/架构/入口点），entrypoint 整文件 digest 同样只在 `--verify` 时算 | `PAYLOAD_MISSING`, `MANIFEST_DIGEST_MISMATCH`, `REFERENCE_STALE`, `REFERENCE_DRIFTED`, `REFERENCE_UNPROBED`, `WHITELIST_REVISION_STALE` |
| D4 | 根内/数据根内无可用登记结论的对象只报告不接管（`UNMANAGED_OBJECT_PRESENT` 仅 `--include-unmanaged`）；**payload 只允许在 `store/`**（声明指到别处记 `error`，视图目录里出现载荷标记记 `warning`） | `ORPHANED_STORE_INSTANCE`, `UNMANAGED_OBJECT_PRESENT`, `PAYLOAD_OUTSIDE_STORE` |
| D5 | binding 引用完整、每个 binding key 至多一个 active；**active 不满足 desired 时如实报告** | `BINDING_TARGET_MISSING`, `MULTIPLE_ACTIVE_BINDINGS`, `DESIRED_NOT_SATISFIED` |
| D6 | 未完成事务必须被解释，不猜测 | `PENDING_TRANSACTION`, `RECOVERY_REQUIRED`, `JOURNAL_TRUNCATED` |
| D7 | **派生状态与权威一致**：JSON 投影漂移要报告；crawl 建的搜索索引**不可读**或**比 `index.max_age_ms` 更旧**同样报告（没有索引不是问题，不报） | `REGISTRY_PROJECTION_STALE`, `SEARCH_INDEX_DEGRADED`, `SEARCH_RESULT_STALE` |
| D8 | extension manifest 合规 | `EXTENSION_MANIFEST_INVALID`, `EXTENSION_VERSION_UNSUPPORTED` |
| D9 | 安全口径诚实 | `POLICY_ONLY_MODE` |
| D10 | `logs/audit` 可从 `state/events` 重建 | `AUDIT_PROJECTION_DRIFT` |

## 3. `doctor` 状态 → 退出码

| status | 触发条件 | 退出码 |
|---|---|---:|
| `healthy` | 只有 `info` | 0 |
| `degraded` | 存在 `warning` | 2 |
| `broken` | 存在 `error`/`critical` | 3 |
| `recovery_required` | 命中 `ROOT_MARKER_*`、`VOLUME_IDENTITY_MISMATCH`、`RECOVERY_REQUIRED`、`JOURNAL_TRUNCATED` | 6 |

**`doctor` 永不删除、永不修复**：陌生对象只产生 `unmanaged`/`orphan` 结论，修复必须由
独立的 `repair`/`reconcile` 动作发起。

**同一个码在两个位置可以有不同粒度**（§2 与 §1 的关系）：`DATA_ROOT_MISSING` 作为
**reason_code** 是退出码 6（需要恢复——`discover` 找不到数据根就无法继续）；作为 **doctor
诊断**它的严重度是 `error`，使整体 status 变成 `broken`（退出码 3），因为 doctor 的退出码
由 status 决定，不由单条码决定。`DATA_ROOT_VOLUME_MISMATCH` / `REFERENCE_*` 同理。
