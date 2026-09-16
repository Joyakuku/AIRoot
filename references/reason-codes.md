# Reason code 速查（按需参考）

**先读 `reason_code`，再读退出码。** 退出码只有 0–9，是粗粒度的；`reason_code` 才告诉你
"是未知还是没有""是降级还是损坏"。权威表在 `docs/AIROOT-v0.3-诊断码与ReasonCode表.md`，
代码在 `cli/app/airoot/exits.py`（**每个注册的码都在这份速查里作为一个独立的词出现**，有测试守这条；
"作为独立的词"这四个字是 §75 加的：之前那条守卫用的是子串匹配，于是 `DEGRADED` 一直躺在
`CURRENT_SOURCE_DEGRADED` 里冒充"已文档化"，而它是 `tool status`/`tool verify` 真的会报的码）。

**这一版发不出来的码**在文末单独列了一节——符合它们不代表你会遇到它们。带 † 的写法与
`references/field-values.md` 是同一个约定。

## 0 — 成功（含"结论是负面的"与"信息级")

| code | 什么时候会出现 | 你要怎么说 |
|---|---|---|
| `SUCCESS` | 一切正常 | 照实汇报 |
| `CURRENT_PROCESS_ENV_OLD` | 新进程能看到，当前 shell 看不到 | 让用户重开 shell；**不是**失败 |
| `POLICY_ONLY_MODE` | 每次 `doctor` 都会带 | 说清"强制手段是约定与审计，不是 ACL" |
| `UNMANAGED_OBJECT_PRESENT` | `doctor --include-unmanaged` | 只是观测，AIROOT 不接管 |
| `REFERENCE_UNPROBED` | 只有路径、没有版本证据 | 说"版本未知"，不要编 |
| `WHITELIST_REVISION_STALE` | 数据根记录的判定版本落后 | 建议重跑 `discover` |
| `SIZE_ESTIMATE_UNAVAILABLE` | `--dry-run` 拿不到体积 | 照实说未知 |

## 1 — 没找到

`NOT_FOUND`、`VERSION_UNSATISFIED`。区分"机器上没有"与"有但不满足约束"：
后者看 `where` 的 `evidence`，它可能写着"对象内有满足约束的版本但未激活"——
**切不切活跃版本是用户的决定**，不要替他切。

`NOT_IMPLEMENTED`（退出码 1）也在这里，但它说的是另一件事：**这个动词规划里有、这一版刻意没有**
（`bootstrap`、`reconcile`、`path backup|restore`、`root adopt|relocate`）。`details` 里带着
`deferred_category` 与 `unblocked_by`（解锁词），`evidence` 指向 `agents/airoot.json` 里那段理由。
**不要**把它当成"命令打错了"（那是 `INVALID_INPUT`，退出码 8），也**不要**建议用户提权重试
（那不是 `PRIVILEGE_REQUIRED`：再高的权限也变不出这个命令）。照实说"这一版没有，等 <解锁词>"。

## 2 — 降级或漂移（结果可用）

| code | 含义 |
|---|---|
| `CURRENT_SOURCE_DEGRADED` | owned payload 不可用，已正常降级到健康 reference。**不是失败** |
| `DEGRADED` | `tool status` / `tool verify` 撞到 **warning 级**问题（例如 `lifecycle` 说 `active` 却没有 active binding）：对象**可用**，只是状态不完美。有 `error` 级问题时它报的是那个问题的码，不是这个。**不要**把 `DEGRADED` 念成 `BROKEN` |
| `STALE_GENERATION` | 状态在你读取后被别人改了：重读再试，不要重放旧计划 |
| `REFERENCE_STALE` / `REFERENCE_DRIFTED` | 被引用对象消失／观测事实变了 |
| `REFERENCE_IN_USE` | 仍被引用，拒绝 `gc`/`forget` |
| `CHILD_PROCESS_FAILED` | `exec` 的子进程失败；子进程状态在 `exit_status` |
| `INSTALL_IO_FAILED` | **文件系统拒绝了一次安装步骤**（磁盘满、文件被锁、权限不够，或下载中断）。计划没错、也没有东西损坏——出错前的那一代仍然可用，失败时绑定会切回上一代。**下一步是修环境（腾空间／解锁／改权限）再重新出一个计划**；同一个 plan+token 不能重放，别重试它 |
| `REGISTRY_PROJECTION_STALE` / `AUDIT_PROJECTION_DRIFT` | 派生投影落后，权威是 registry/events |
| `DRIFT_DETECTED` | **观测**与声明不一致（与"意图未满足"不同，见 `DESIRED_NOT_SATISFIED`） |
| `DESIRED_NOT_SATISFIED` | `state/desired.json` 里有 pin，但当前 active binding 不满足它。**下一步是改 manifest 或重装，不是修环境** |
| `SESSION_STATE_STALE` | 会话栈记录时的 generation 与现在不一致；重开会话，不要手工改环境变量 |
| `PATH_EXPOSURE_VIOLATION` | PATH 上出现违反冻结规则的东西（重复 AIROOT 条目 / `store` 版本目录 / 非授权目录） |
| `DATA_ROOT_ACL_DRIFT` | 数据根的 ACL 与基线不一致（**P2 才可能发射**；现在只是注册） |
| `ORPHANED_STORE_INSTANCE` | `store/` 里有登记不上的对象：只报告，**不删** |
| `PAYLOAD_OUTSIDE_STORE` | **payload 出现在 `store/` 之外**（`store` 是唯一 payload 存储，`tools`/`env` 只是 binding/view）。声明指到别处 → `doctor` 记 `error` 且 `where` **不选它**；只是没人声明的载荷标记落在视图目录里 → `warning`。下一步是把它移进 `store`，或者停止声明它 |
| `EXTENSION_TIMEOUT` / `EXTENSION_CANCELLED` / `EXTENSION_HEALTH_DEGRADED` | 扩展超时／被取消／健康度下降：结果不完整，但扩展本身没坏 |
| `SEARCH_FALLBACK_USED`、`SEARCH_RESULT_STALE`、`SEARCH_INDEX_DEGRADED`、`SEARCH_JOURNAL_GAP`、`SEARCH_PERMISSION_FILTERED`、`SEARCH_ROOT_UNAVAILABLE`、`SEARCH_TIMEOUT` | **搜索专属**，逐条解释见下方《搜索的降级阶梯》 |

## 3 — 损坏

`BROKEN`（声明了但不可用且没有可替代的健康候选）、`PAYLOAD_MISSING`、
`MANIFEST_DIGEST_MISMATCH`、`BINDING_TARGET_MISSING`、`MULTIPLE_ACTIVE_BINDINGS`、
`REGISTRY_INTEGRITY_FAILED`、`EXTERNAL_REFERENCE_DRIFTED`。**不要自己修**：跑 `airoot repair`，
或把证据交给用户。

> `CONFLICT_MANAGED_BROKEN` 仍注册但**不再由 `where` 发射**：steward-first 之后，
> "owned 坏了 + reference 健康"是正常降级（见上表）。

## 4 — 需要批准

`SCOPE_CONFIRMATION_REQUIRED`（三选一）、`SCOPE_UPGRADE_REQUIRES_APPROVAL`（项目 → 数据根）、
`PERSISTENCE_REQUIRES_APPROVAL`、`APPROVAL_REQUIRED`、`APPROVAL_EXPIRED`、`APPROVAL_REPLAYED`、
`APPROVAL_REVOKED`、`INVALID_APPROVAL`、`POLICY_REVISION_MISMATCH`。
**唯一正确的行为**：把 `plan_hash` 与 plan 文件路径交给用户，等人工批准。
`airoot approve` 只消费批准，永远不制造它。

**但这个 build 里没有东西能签发批准**：核心只校验，唯一的签发方是测试用的
`cli/tests/fake_issuer.py`。所以上面那一步在真机上**走不到底**——`approve` / `install` /
`env persist` / `tool gc --apply` / `uninstall` 带 `--token-file` 时返回 `PROVENANCE_FAILED`（退出码 7），
消息里带 `no production approval issuer exists in this build (decided: ADR-0025 keeps it waiting for the P2 broker)`。
**能跑到的是前两步**（`plan --dry-run` / `plan`）。待裁决项与三条路见
`docs/AIROOT-v0.3-实现决策记录.md` 的 **ADR-0024**（状态：**已裁决：A 维持现状**，见 ADR-0025）；
**不要**试图自己造 token——伪造正是消费侧要拒绝的东西。

## 5 — 需要权限

`PRIVILEGE_REQUIRED`（如 `env persist --scope machine`）、`ACL_MISMATCH`、
`CALLER_NOT_AUTHORIZED`（受保护 broker 拒绝一个它不肯服务的调用方：SID 不在允许集合里、调用方是
AppContainer 之类的受限进程、完整性级别低于要求、或者调用方自己报的身份与 broker 观测到的 token 对不上）。
**这一条与 `PRIVILEGE_REQUIRED` 只是同一层，不是同一件事**：提权**不会**把 `S-1-5-21-…` 换成另一个 SID，
所以"提权再试"不是这个码的下一步——照实说"这个调用方没被授权"，不要建议用户去提权。
P1 没有 broker，所以 machine 级写入一定报 `PRIVILEGE_REQUIRED`；**不要**建议用户手工改 HKLM 绕过。

## 6 — 需要恢复

`ROOT_MARKER_MISSING`、`ROOT_MARKER_INVALID`、`VOLUME_IDENTITY_MISMATCH`、`REGISTRY_MISSING`、
`DATA_ROOT_MISSING`、`DATA_ROOT_VOLUME_MISMATCH`、`PENDING_TRANSACTION`、
`RECOVERY_REQUIRED`、`JOURNAL_TRUNCATED`。
**停止**，先 `repair`；不要在身份不可证明的 root 上继续任何操作。

`discover` 报的 `DATA_ROOT_MISSING`（退出码 6）是一个**聚合**："某个已声明的数据根读不了"是**状态**问题，
**具体原因**在 `missing[].reason_code`（每条还带 `data_root_id` 与 `detail`）——报的时候要说清是哪个数据根、
为什么，不要只说一句"有数据根缺失"（§103）。

## 7 — 计划/来源问题

`INVALID_PLAN`（hash 不匹配、缺 digest）、`DIGEST_MISMATCH`、`PROVENANCE_FAILED`
（含"拒绝非 https 来源"；**`ed25519` 不再是它的理由**——§113 起签名不对的 token 报 `INVALID_APPROVAL`(4)，这个码只剩"整个 root 没有 keyring"）、`OWNERSHIP_REQUIRED`
（**对 reference 调 `uninstall`**：把绝对路径和 `airoot forget` 建议交给用户）、
`INSTANCE_CONFLICT`、`UNSUPPORTED_BACKEND`、`ILLEGAL_TRANSITION`（状态机不允许的迁移）。

## 8 — 输入或 Schema 非法

`INVALID_INPUT`、`SCHEMA_UNSUPPORTED`、`PERSISTENCE_TARGET_FORBIDDEN`
（值指向数据根之外、或注入型变量）、`PATH_ESCAPES_ROOT`、`REPARSE_POINT_REJECTED`、
`UNC_NOT_ALLOWED`、`ROOT_NOT_RESOLVED`、`ENVIRONMENT_PERSIST_NOT_FOUND`
（没有任何已记录的持久化环境可还原）、`SELF_VALIDATION_FAILED`
（**这是实现缺陷**，不是用户错误；请如实报告并附 `evidence`）。
搜索专属：`SEARCH_QUERY_INVALID`、`SEARCH_CURSOR_INVALID`。
扩展专属：`EXTENSION_INPUT_INVALID`（调用方越过了实现上限）、`EXTENSION_OUTPUT_INVALID`
（扩展返回的文档不符合 published schema：**输出**不合法，输入没问题）。

## 9 — 能力未冻结 / 扩展不可用

`CAPABILITY_NOT_DECLARED`（对象没有已冻结的能力：只报告，不接管；增长路径见
`policy/capabilities.json` 的说明）、`EXTENSION_UNAVAILABLE`、`EXTENSION_NOT_FOUND`
（没有任何扩展提供这个能力）、`EXTENSION_VERSION_UNSUPPORTED`、`EXTENSION_MANIFEST_INVALID`、
`EXTENSION_OPERATION_UNKNOWN`（该扩展没声明这个操作）、`EXTENSION_PERMISSION_DENIED`、
`EXTENSION_DEPENDENCY_MISSING`、`EXTENSION_SIDE_EFFECT_BLOCKED`（越过了声明的副作用上限）、
`SEARCH_NOT_READY`、`SEARCH_BACKEND_UNAVAILABLE`。

## 搜索的降级阶梯（`airoot search`）

`search` 的答案有两种来源，**必须能分辨**：索引（快，但可能旧）与实时遍历（crawl，慢，但新鲜）。
协议要求这两者不能返回同一种"确定正确"的语义：

| reason_code | 含义 | 你要怎么说 |
|---|---|---|
| 无（`reason_code: null`） | 索引新鲜且完整覆盖 | "这是索引答案，新鲜度 `freshness`" |
| `SEARCH_FALLBACK_USED` | 回答来自**实时遍历**（没有索引／索引不覆盖这些 root） | 说"这是实时遍历的结果，不是索引"；`data.fallback.kind=crawl` |
| `SEARCH_RESULT_STALE` | 索引比 `--max-staleness-ms` 更旧 | 建议 `airoot search refresh` 或放宽约束；**不要说索引坏了** |
| `SEARCH_INDEX_DEGRADED` | 索引读不出来或只覆盖了一部分 root | 已回落遍历；建议 refresh；`doctor` 的 D7 也会报 |
| `SEARCH_NOT_READY` | 还没有索引（`search status` 的诚实回答） | 说"还没建索引"，不要假装有 |
| `SEARCH_BACKEND_UNAVAILABLE` | 协议里的 native（USN）索引在这个 build 里建不出来 | 见 `search status --probe-native-index`；**不要**说 AIROOT 已具备 Everything 级性能 |
| `SEARCH_TIMEOUT` | 遍历撞上 `max_duration_ms` | 缩小 root 或降低 `--limit` 后重试 |
| `SEARCH_CURSOR_INVALID` | cursor 与当前查询／root／索引 generation 不匹配 | 重跑（去掉 `--cursor`）；**绝不**把旧 cursor 当第一页 |
| `SEARCH_QUERY_INVALID` | 请求字段本身不合法（通配符、反向区间、枚举写错） | 按 `evidence` 改请求 |
| `SEARCH_ROOT_UNAVAILABLE` | root 不存在／是 UNC／不是目录，或没有已注册的数据根 | 让用户注册数据根或显式给 `--search-root` |
| `SEARCH_PERMISSION_FILTERED` | 部分结果因权限被过滤 | 只说被过滤了，**不要泄露**被过滤的路径 |
| `SEARCH_JOURNAL_GAP` | USN journal 断档（**P2 的 native 索引才可能发射**） | 现在不会出现；出现即说明 native 索引已启用 |

## 这一版发不出来的码（†）

下面这些码**注册在 `exits.py` 的映射表里、也在上文的解释里**，但**这个 build 没有任何代码写出它们**：除了那张映射表，`cli/app/airoot` 里再也找不到这个字符串。所以你在真机上不会遇到它们；**不要为它们写分支**——遇到就说明版本变了（或者有人在手写 JSON），去核对，不要猜。

判据是一句可以机械核对的话：**"除 `exits.py` 外，`cli/app/airoot/` 里没有再出现这个字符串"**（`cli/tests/test_l1_reason_codes.py` 每次跑测试都把这张表与这句话对一遍，两个方向都查）。它**不**包括"出现在声明里"的情况——例如 `DATA_ROOT_ACL_DRIFT` 出现在 `caps/doctor.py` 的不变量声明表与诊断里，所以它**不在**下面这张表里，它靠上文那一行自己的旁注说明"作为 `reason_code` 要等 P2"。这是判断，不是遗漏。

| code | 退出码 | 为什么这一版没有写者 |
|---|---|---|
| `ACL_MISMATCH` | 5 | ACL 的**写**一侧已经作为**库**落地（§113 / ADR-0040：baseline/apply/verify/restore），但它**不接任何动词、不接任何 schema**——没有 broker 就接上去等于给同用户进程一条改 DACL 的路，所以这个码仍然没有写者 |
| `DRIFT_DETECTED` | 2 | 泛化的漂移码；这一版用的是更具体的 `REFERENCE_DRIFTED` / `DATA_ROOT_ACL_DRIFT` 等 |
| `EXTENSION_TIMEOUT` | 2 | 扩展的**进程**运行时还没落地：这一版 `ext/` 只有 manifest、envelope 与假扩展；超时的是搜索自己（`SEARCH_TIMEOUT`），不是扩展 |
| `EXTENSION_CANCELLED` | 2 | 同上：没有可取消的扩展进程 |
| `EXTENSION_HEALTH_DEGRADED` | 2 | 同上：扩展健康检查是 manifest 里的**声明**（`health_checks`），没有执行者 |
| `EXTENSION_OUTPUT_INVALID` | 8 | 没有"扩展返回的文档"可校验（把外部程序的输出收进来要等扩展运行时） |
| `EXTENSION_PERMISSION_DENIED` | 9 | 同上 |
| `EXTENSION_DEPENDENCY_MISSING` | 9 | 同上 |
| `EXTENSION_SIDE_EFFECT_BLOCKED` | 9 | 副作用上限**已经在准入时判**（`caps/boundary.py` 报 `CAPABILITY_NOT_DECLARED`），但"运行时越界"还没有执行者 |
| `EXTERNAL_REFERENCE_DRIFTED` | 3 | 更具体的 `REFERENCE_DRIFTED`（退出码 2）取代了它：观测漂移不是"损坏" |
| `SEARCH_JOURNAL_GAP` | 2 | USN journal 断档是 **P2 的 native 索引**才会有的状态（现在没有 journal 消费者） |
| `SEARCH_PERMISSION_FILTERED` | 2 | 这一版的 crawl 读不动的目录报在 `warnings` 与人可读的证据里，不减少结果集 |

**还有一类，不在这张表里，但你在真机上也遇不到**：**有写者、没有任何动词能走到**。判据是"这个字符串在
`cli/app/airoot` 里有没有写者"，而"有没有路"是另一个问题。目前唯一的成员是
`CALLER_NOT_AUTHORIZED`（5）：`broker/policy.py` 的 `admit_caller` 是这个 build 里唯一写出它的地方，而它
**只被测试调用**——没有 broker、没有 named pipe、没有任何动词问它（§114）。所以它既不该进上表（它有写者），
也不该被写进你的分支逻辑（你收不到它）。**两张表加起来才是"这一版能看到的码"**；`ACL_MISMATCH` 的处置
（写一侧已落地但不接动词）是同一种情况的另一半。

