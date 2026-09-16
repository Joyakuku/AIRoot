# 字段取值表（AIROOT 词汇表）

**什么时候读**：你在 AIROOT 的 JSON 输出或某份 manifest 里看到一个**知道拼写但不知道怎么解释**的值（`degraded`？`REG_EXPAND_SZ`？`external_reference`？`unverified`？）时读这张表。它只回答一件事：**这个字段的每一个合法取值，在这一版里是什么意思、这一版会不会真的写出来。**

**权威在哪**：取值**域**（哪些值是合法的）的权威永远是 `cli/schema/*.schema.json`——本表逐行从 schema 里解析出来，`cli/tests/test_l1_field_values.py` 每次跑测试都会把两者对一遍，对不上就红。取值**语义**的权威是核心契约（`docs/AIROOT-v0.3-三大核心契约方案.md`）与规划；本表只给"够做决定"的那一句，不复制契约正文。

**怎么读这一行**：`字段 | 取值 | 含义 | 本版谁写出`。

- `字段` 是相对该 schema 根的位置；`[]` 表示数组元素，`*` 表示 map 的每个值。
- `取值` 里每个值是一个合法取值；带 **†** 的值表示**这一版没有任何代码会写出它**——它合法、可被 schema 校验通过，但你在真机上不会遇到，所以**不要为它写分支**（遇到它意味着有人在手写 JSON 或版本已经变了，该去核对而不是猜）。
- `本版谁写出` 是决定 † 的那组文件（相对仓库根）。它**只列写这个字段的代码**，不列只读它、或只在过滤/校验集合里点到这个名字的地方。所以它是一份**证据指针**，不是"这个字段的全部相关代码"。
- `null`（JSON 空值）**不参与** † 扫描：空值在代码里由 `None` 写出，不是字符串字面量，用字面量扫描既证明不了"会写"也证明不了"不会写"。凡取值列里出现 `null`，它的含义都单独写在 `含义` 里。

**三套 `scope` 不要混**（它们拼写相同、含义不同）：绑定的是 `system|machine|session|project`，环境变量持久化的是 `user|machine`，依赖分流的是 `project|data-root`。本表按字段出现的位置给，看到 `scope` 先去认它属于哪一套。

---

## `where-response.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `source` | `registry` / `path` / `project`† / `search`† / `null` | 这个候选是从哪儿来的：`registry` 是有绑定的实例，`path` 是在 PATH 上找到的。`null` 表示**没找到**（`reason_code` 说明为什么，通常是 `NOT_FOUND`(1) 或 `VERSION_UNSATISFIED`(1)），不是"找到了但不知道从哪来" | `caps/where.py` |

`where` 的候选行还带 `zone`（见 `common`）与 `machine_discoverable`（= `zone != "W"`）：**Zone W 的候选不会被机器级发现**（ADR-0022），要它就用 `--project` / 显式激活。

## `doctor-response.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `status` | `healthy` / `degraded` / `broken` / `recovery_required` | 一次 `doctor` 的总结论，由诊断的 `severity` 集合推出：有 `error`/`critical` → `broken`，只有 `warning` → `degraded`；根标记/卷身份/日志损坏这类先于一切的问题 → `recovery_required`。退出码分别 0 / 2 / 3 / 4 | `caps/doctor.py` |
| `diagnostics[].severity` | `info` / `warning` / `error` / `critical` | 单条诊断的严重度。`critical` 是"AIROOT 连自己在哪台机器上都不确定"这一档（根标记/卷身份/registry 元数据读不出来）——它和 `error` 一样把 `status` 推成 `broken`，但**repair 不再是它建议的动作** | `caps/doctor.py` |
| `diagnostics[].remediation` | `none` / `inspect` / `repair` / `rebuild` / `reapprove`† / `recover` | 建议你下一步做什么：`none` 不用管；`inspect` 人看一眼（AIROOT 没有对应命令）；`repair` → `airoot repair`；`rebuild` → `airoot rebuild`（**只重建派生投影，数据库永不重建**）；`recover` → 按 journal 做恢复。`reapprove` 表示"需要重新拿一次批准"，但**这个 build 里没有任何 doctor 检查会产生它**（所以它带 †）：批准本身现在签得出来（ADR-0046 的本机签发），缺的是**判定"这一条得重新批"的那条检查**——遇到它请当作版本不一致 | `caps/doctor.py` |

## `error-response.schema.json`

一份**失败文档**的取值。它没有枚举字段，但有一个"唯一值"字段（schema 写的是 `const`，§106 起这种字段也进这张表）：

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `status` | `failed` | 恒为 `failed`：这份文档只表示"命令没能回答"。`reason_code` 的**取值表不在这里**——它是 `docs/AIROOT-v0.3-诊断码与ReasonCode表.md`（权威，速查在 `references/reason-codes.md`）；`details` 的键是**数据**，按码而不同（§101 的 `NOT_IMPLEMENTED` 是第一个写它的） | `exits.py` |

## `search-request.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `match` | `exact` / `prefix` / `contains` | 名字怎么比。默认 `contains`；`exact` 是整名相等 | `caps/search.py` |
| `target` | `name` / `path` / `name_and_path` | 比什么：文件名、完整路径、两者都试 | `caps/search.py` |
| `consistency` | `best_effort` / `bounded_staleness` / `refresh_then_read` / `physical_verify` | 你能接受多旧 / 多贵：`best_effort` 有索引就用、没有再 crawl；`bounded_staleness` 用索引但超过 `max_staleness_ms` 就报 `SEARCH_RESULT_STALE`(2)；`refresh_then_read` 先整次遍历建索引再读；`physical_verify` 对**返回的那一页**再核一次大小/时间（`verification` 因此可能变成 `changed`） | `caps/search.py` |

## `search-response.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `status` | `ok` / `degraded` / `error`† / `cancelled`† / `timed_out` | 这次搜索的结论。`ok` = 索引答的、覆盖完整且没过期；`degraded` = 索引旧了/覆盖不足/没有索引只好 crawl（`reason_code` 会说是 `SEARCH_RESULT_STALE`、`SEARCH_INDEX_DEGRADED` 还是 `SEARCH_FALLBACK_USED`）；`timed_out` = crawl 撞上 `max_duration_ms`，答案是**部分**的且**没有 cursor** | `caps/search.py` |
| `data.freshness.state` | `current` / `stale` / `degraded`† / `rebuilding`† / `unknown` | `current` 只表示"这份清单是最近一次遍历建立的"——**它不等于 USN 游标没断档**（这个 build 没有 USN 索引）；`stale` = 比 `max_staleness_ms` 旧；`unknown` = 没有索引答这次请求（此时 `coverage` 是 `none`） | `caps/searchindex.py`, `caps/search.py` |
| `data.freshness.coverage` | `complete_for_roots` / `partial` / `none` | 索引覆盖请求的这些 root 的程度：`partial` = 建索引那次遍历被记录数上限截断或超时（`truncated`/`timed_out`），`none` = 没有可用的索引 | `caps/searchindex.py`, `caps/search.py` |
| `data.results[].kind` | `file` / `directory` | 命中的是文件还是目录 | `caps/search.py` |
| `data.results[].verification` | `indexed` / `verified` / `changed` / `unverified` | 这条结果有多可信：`indexed` 来自索引、**没有再核过**；`verified` 这一轮核过且一致；`changed` 核过但**已经和索引不一样**（不能当 current 事实用）；`unverified` crawl 直接给的、没核过 | `caps/search.py`, `caps/searchindex.py` |
| `operation` | `search` | 这份文档是**搜索**的回答（信封里区分文档种类的那个字段）。它与 `plan`/`reference-plan`/`extension-envelope` 里的同名 `operation` 是**同形不同域**：各自枚举自己的操作名 | `caps/search.py` |
| `data.fallback.kind` | `crawl` | 回落方式**只有一个取值**（schema 写的是 `const`）：这一版没有 native 索引，回落就只有受控遍历这一条路。`data.fallback` 为 `null` 时它不出现 | `caps/search.py` |

`data.fallback` 的 `reason` 是一句人话，说明为什么没走索引。

## `plan.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `operation` | `install_tool` / `install_runtime` / `import_tool`† / `recreate_runtime`† / `retire_tool` / `gc_apply` / `root_relocate`† | 这份计划想干的事：装一个受控工具 / 装一个运行时（P5）/ 退役一个实例（清 active binding，**payload 留着**）/ 回收已退役且无人引用的 payload / 迁移 root（要提权）。`import_tool` 与 `recreate_runtime` 这一版没有写者：`adopt --mode import` 出的是自己的计划形状（`metadata.import`），`recreate` 属 P5 | `tx/artifact.py`, `tx/simulate.py`, `caps/lifecycle.py` |
| `target.kind` | `managed_tool` / `runtime`† | 计划作用的对象是受控工具实例还是运行时实例。这一版没有 runtime 实例，所以 `runtime` 不会出现 | `registry/entities.py`, `tx/artifact.py`, `tx/simulate.py`, `caps/lifecycle.py` |
| `operations[].kind` | `fetch` / `verify` / `stage` / `commit` / `expose` / `rollback`† / `delete` | 九步协议里的步骤名：取回 / 校验来源与摘要 / 进暂存区 / 提交进 store / 暴露（绑定或环境）/ 删除 payload。`rollback` 不会出现在计划里——回滚是状态机在失败/恢复时做的事（`ROLLBACK_PENDING`），不是计划的一步 | `tx/artifact.py`, `tx/simulate.py`, `caps/lifecycle.py`, `caps/exposure.py` |
| `operations[].source_mutation` | `none` / `delete`† / `move`† / `overwrite`† | 这一步对**源**做了什么。这一版两个 backend 都声明 `none` 并被强制要求是 `none`（`caps/backends/base.py` 会拒绝别的）——**AIROOT 不动你的源文件** | `caps/backends` |
| `canonicalization` | `jcs-rfc8785-compatible` | `plan_hash` 用哪个规范化算法算（README 规则 4）。**它不是可选项、也不是标签**：换一个算法，同一份计划就是另一个 hash，而批准是绑在那个 hash 上的 | `tx/artifact.py`, `tx/simulate.py`, `caps/exposure.py`, `caps/lifecycle.py` |

## `reference-plan.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `operations[].kind` | `expose` / `rollback`† | 引用型（不拥有 payload）的计划只有暴露一步；撤销暴露走 `env forget` 的还原路径，不写 `rollback` 操作 | `caps/exposure.py` |
| `operations[].target_scope` | `user` / `machine` | 写到哪一级环境：`user`（HKCU，现在就能做）/ `machine`（要提权，现在以 `PRIVILEGE_REQUIRED`(5) 收场） | `caps/exposure.py` |
| `exposure.scope` | `user` / `machine` | 与上一条同义，这是计划顶层的那个字段 | `caps/exposure.py`, `caps/environment.py` |
| `exposure.value_kind` | `REG_SZ` / `REG_EXPAND_SZ` | 用哪种注册表值类型写：`REG_SZ` 按字面存；`REG_EXPAND_SZ` 会展开 `%VAR%`。**值里含 `%...%` 就必须是 `REG_EXPAND_SZ`**（声明成 `REG_SZ` 会被拒，否则你会得到一个永远不展开的路径） | `caps/exposure.py`, `caps/environment.py`, `registry/db.py` |
| `exposure.variables.propertyNames.not` | `PYTHONPATH` / `PYTHONHOME` / `PYTHONSTARTUP` / `NODE_OPTIONS` / `NODE_PATH` / `LD_PRELOAD` / `LD_LIBRARY_PATH` / `DYLD_INSERT_LIBRARIES` / `GIT_SSH_COMMAND` / `GIT_EXTERNAL_DIFF` / `GIT_CONFIG_GLOBAL` / `PATHEXT` / `COMSPEC` / `BASH_ENV` / `ENV` / `ZDOTDIR` / `PROMPT_COMMAND` | **禁止持久化**的变量名：它们要么"加载任意代码"，要么"加一个执行触发点"，写到 user/machine 级就是全局代码注入面。用这些名字持久化会被 `INVALID_INPUT`(8) 拒 | `caps/environment.py` |
| `operation` | `record_reference_exposure` | 这份计划只做一件事：把一次暴露记进声明（`caps/exposure.py` 的 `PLAN_OPERATION`）。它**不碰 payload**——外部引用没有 AIROOT 拥有的载荷 | `caps/exposure.py` |
| `target.management` | `external_reference` | 计划作用的对象恒是外部引用：这份 schema 存在的理由就是引用域没有自己的计划形状（ADR-0003 的 `reference-plan` 边界） | `caps/exposure.py` |

## `gc-plan.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `items[].kind` | `managed_tool`† / `runtime`† | 待回收对象的种类。判据是"AIROOT 装的 + 已退役 + 无人引用"三者同时成立。**这一版连这份文档本身都没有写者**：`gc-plan` 是一份**批量**回收计划，而本版的 `build_gc_plan` 出的是 `plan.schema.json`（一次一份 payload，`operation=gc_apply`）；`tool gc --plan` 打印的那份 `operation=gc_plan` 是**报告信封**，不是这份 schema。这一栏原来写的三个模块写的是 `plan` 的 `target.kind`——**同样两个词、不同字段**，于是那是一次**同名词假覆盖**：（§74/§77 记的正是这条盲点，§92 把它修掉） | （没有写者） |
| `items[].reason` | `retired_and_unreferenced`† | 为什么可以回收：已退役**且**没有任何 binding 引用。这个字段只有**一个取值**（schema 写的是 `const`），所以它不是枚举而是"唯一值"；† 的理由与上一行相同——这一版没有任何代码写出这份文档 | （没有写者） |
| `requires_approval` | `true`† | 批量回收必须走批准：`gc_plan` 这份文档的语义就是"批量、要批"，与 `plan` 的 `operation=gc_apply`（一次一份）不同。† 同上 | （没有写者） |

## `managed-tool-instance.schema.json`

这个文件**自己**没有枚举字段，但有两个"唯一值"字段（`const`，§106 起也进这张表）：

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `kind` | `managed_tool` | 受控工具实例。`runtime-instance` 那份 schema 的同一个字段是 `runtime`——两份 schema 靠这个字段把自己和对方分开 | `registry/entities.py`, `tx/simulate.py`, `cli.py` |

`instances[].kind` 在 `registry-projection` 那一节有枚举形式的行；这里说的是**这份 schema 自己的**那个常量字段。

## `registry-projection.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `instances[].kind` | `managed_tool` / `runtime`† | 受控工具实例 / 运行时实例（P5） | `registry/entities.py`, `tx/artifact.py`, `tx/simulate.py` |
| `external_references[].management` | `external_reference` / `unmanaged` / `project_owned`† / `quarantined` | 这条外部对象和 AIROOT 的关系：`external_reference` = 已登记、只读、**永不 `uninstall`**；`unmanaged` = 看到了但没登记；`quarantined` = 可疑（比如 reparse point 指向别处），只报告永不删。`project_owned` 这一版没有写者 | `caps/discovery.py`, `caps/where.py`, `registry/entities.py` |
| `external_references[].capability_kind` | `runtime` / `tool` / `null` | 这条引用提供的是运行时还是工具；`null` = 白名单没判出来（**判不出来就说不知道，不猜**） | `caps/discovery.py`, `policy/discovery-whitelist.json` |
| `external_references[].source_kind` | `declared`† / `pe_static` / `public_locator`† / `extension_handler`† / `approved_execution`† | 这条引用的身份是怎么来的：这一版**只有** `pe_static`（只读 PE 静态探测）。`declared`（由别人声明）与其余三个都没有写者——所以一条 reference 声称自己是 `declared` 时，那是手写进去的 | `caps/discovery.py`, `registry/entities.py`, `cli.py` |

## `extension-envelope.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `status` | `ok` / `degraded` / `error` / `cancelled`† / `timed_out` | Extension 调用的信封结论，语义与 `search` 的 `status` 一致（这是被所有扩展共用的外壳） | `ext/envelope.py`, `caps/search.py`, `caps/exposure.py` |

## `extension-manifest.schema.json`

这一节管的是 **manifest 自己的词汇**（扩展作者写的声明），不是命令输出。

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `implementation_kind` | `builtin` / `native` / `adapter`† / `fallback` | 实现的性质：`builtin` 是核心自带，`native` 是需要卷/索引器支持的那种（这个 build 里 `file_search` 的 native 候选**只是个声明**，前置条件在 `search-policy.json` 里写着），`fallback` 是"有索引用索引、没索引受控 crawl"。`adapter`（包一个外部程序）这一版没有 manifest 用它 | `cli/extensions`, `ext/fake.py`, `caps/usn.py`, `policy/search-policy.json` |
| `required_privilege` | `user` / `broker`† / `user_or_broker`† | 这个实现要什么权限才能跑。这一版所有实现都只要 `user` | `cli/extensions`, `ext/fake.py` |
| `health_checks[]` | `probe` / `version` / `self_test` / `index_integrity` / `permission` | 声明自己能被怎么体检；这些是**声明**，不代表都实现了 | `cli/extensions`, `ext/fake.py` |
| `operations.*.operation_kind` | `read` / `write` / `execute`† / `mutate_system`† | 这个操作的副作用类别。`execute`（起进程）与 `mutate_system`（改机器状态）这一版没有实现声明它们 | `cli/extensions`, `ext/fake.py` |
| `operations.*.overwrite_policy` | `deny` / `replace_same_instance` / `explicit_generation`† | 同名目标已存在时怎么办：拒绝 / 换掉同一个实例 / 要求显式给 generation。`explicit_generation` 这一版没人用 | `cli/extensions` |
| `operations.*.cancellation_semantics` | `best_effort` / `stop_before_commit`† / `resume_or_rebuild` | 取消时能保证什么：尽力停 / 保证在提交前停 / 要么续要么重建。`stop_before_commit` 这一版没人用 | `cli/extensions` |

## `approval-token.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `approval_mode` | `human` / `policy` | 这次批准是人给的还是策略自动给的。**自 ADR-0046 起本机可以签**：`tx/issuer.py` 的 `issue` 把这个字段写成它收到的 `mode`，默认 `policy`；`human` 要求显式给 `approved_by_sid`，签发方拒绝替人编一个 SID（"an agent request is never a human approval"）。**两个值都不再打 †**——这一版真的会写出它们。**批准是账本，不是授权证明**：私钥就在 root 里，同用户进程读得到也签得出，验签只证明一致性（ADR-0046） | `tx/issuer.py` |
| `signature.algorithm` | `ed25519` / `test_hmac_sha256`† | 签名算法。`ed25519` 这一版真的会写出：`tx/issuer.py` 把 `algorithm` 写成 `PRODUCTION_ALGORITHM`，而那个常量定义在 `tx/approval.py`（`PRODUCTION_ALGORITHM = "ed25519"`）。`test_hmac_sha256` **仍然打 †**：核心只把它当校验器**接受**的一个常量，没有任何核心代码把它写进一份文档——写它的是测试路径的 `cli/tests/fake_issuer.py`。§119 复核了 §93 的结论：当时"这一版没有任何代码构造出一份 token"是对的，ADR-0046 造出 `tx/issuer.py` 之后它不再对 | `tx/issuer.py`, `tx/approval.py` |

## `broker-request.schema.json`

**这一版第一次有了写者**（§108）：`broker/protocol.py` 的 `build_request` 造这份文档，返回前过
`validate_self`。它与其他每一份都不同——**它是发出去的，不是打印出来的**：这个 build 没有 broker 应答它，
`broker_unavailable()` 就是这件事的诚实回答（`NOT_IMPLEMENTED`(1)，ADR-0025 的 D1 维持现状）。所以下表问的
"谁写出"是"谁能把值放进这份文档里"，而不是"哪条命令会把它打印出来"。

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `operation` | `commit_plan` / `recover_transaction` / `probe_root` / `gc_apply` | 请 broker 做哪一件事。四个值**都是声明**，四个都没有真的执行过：没有 broker，`build_request` 只造文档、`broker_unavailable()` 拒绝发送。设计文档只给了 `commit_plan` 的算法（`docs/broker/` 的 17 步提交）；`probe_root` 在设计里只以一个词出现；`gc_apply` 与 `recover_transaction` 的**名字在设计文档里根本不存在**，是本层照进程内同名动作定的（§108 实测） | `broker/protocol.py` |
| `client.integrity` | `low` / `medium` / `high` / `system` | 调用进程的完整性级别：token 的 integrity SID 按 RID 阈值映射到这四个词。它是**自述**——`broker.md` 要求 broker 自己校验客户端进程 token，所以这一格是"我是谁"的声明，不是"我够格"的证明（读别人 token 的那一半不存在，见本节末尾） | `caps/identity.py` |

这一节的两行各自的**边界**，一并写清楚，免得被读成更强的话：

- `client.sid` / `client.pid` 没有自己的词汇（一个是 SID 的 pattern，一个是整数），所以没有行；
  但**它们同样只是自述**，来源与 `integrity` 一样是 `caps/identity.py`。
- `requested_at` / `plan_ref` / `approval_ref` / `transaction_id` 是 pattern 或自由字符串，没有枚举。
- **`gc_apply` 那一格还有一个 schema 没写的要求**：`broker-request` 的两个 `allOf` 分支只覆盖
  `commit_plan` 与 `recover_transaction`，所以一个既无 `plan_ref` 也无 `approval_ref` 的 `gc_apply`
  **是 schema 合法的**；本层仍然拒绝它（`INVALID_INPUT`(8)），因为进程内同名动作
  `caps/lifecycle.py` 的 `apply_gc_plan` 要 plan hash 与 approval token。这条不对称记在
  `docs/schema/README.md` 的已知缺口里（§108）。

## `broker-response.schema.json`

**核心也有了写者**（§115）：`broker/pipe.py` 的 `_answer` 造这份文档，返回前过 `validate_self`——
它的第一份 golden 语料就是那次拒绝本身（`broker_response_pipe_refusal.json`）。§108 起它还有读者
（`broker/protocol.py` 的 `parse_response` 会校验它、按 `status` 决定是否抛错），§110 起测试替身
（`cli/tests/fake_broker.py`）也在造它——但**那些都不是这一节的写者**：这一节问的是核心。

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `status` | `ok` / `rejected` / `failed`† / `recovery_required`† | 这次请求的裁决。**这个服务器只会写前两个**：`_status_for` 只按"退出码是不是成功"分流，成功是 `ok`、其余一律 `rejected`。`failed`（执行中失败）与 `recovery_required`（事务停在中间）需要这个服务器**没有的东西**——它最多跑一个只读操作，没有中途状态，所以那两个值是测试替身 `cli/tests/fake_broker.py` 才会写的；† 的含义与别处一致：**核心没有任何代码写出它们** | `broker/pipe.py` |

## `desired-manifest.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `source.kind` | `local_file`† / `remote_signed`† / `project`† | 这个愿望的来源。**三个值这一版一个都不会出现**：`tool pin` 只记 capability/version/scope，manifest 里的 `source` 恒为 `null`；`remote_signed`（要来源证明）等的是 P2 的受保护存储，而 ADR-0024 已由 ADR-0025 的 D1 裁决为维持现状 | （没有写者） |

## `runtime-instance.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `runtime_family` | `python`† / `node`† / `java`† / `dotnet`† / `custom`† | 运行时家族。**这五个值这一版一个都不会出现**：runtime 实例要到 P5 才创建（现在 `runtime` 这个词只出现在计划与清单的**种类**里，不出现为实例） | （没有写者） |
| `kind` | `runtime`† | 唯一取值（`const`）：这份 schema 只描述运行时实例；受控工具实例是 `managed-tool-instance`，同一个字段的值是 `managed_tool`。**† 是量出来的**：这一版没有任何函数构造出 `runtime-instance` 文档（`managed_tool` 那一份有），所以它与上面的 `runtime_family` 同属"P5 才有写者" | （没有写者） |

## `transaction.schema.json`

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `state` | `PROPOSED` / `APPROVED` / `FETCHED` / `VERIFIED` / `STAGED` / `COMMITTED` / `REGISTERED` / `ACTIVE_BOUND` / `EXPOSED` / `VERIFIED_AGAIN` / `FINALIZED` / `FAILED` / `ROLLBACK_PENDING` / `ROLLED_BACK` / `RECOVERY_REQUIRED` / `EXPIRED` | 事务的持久状态。**`ACTIVE_BOUND` 是唯一可以改变 active binding 的提交点**（与 generation 同一个 SQLite 事务）；`FINALIZED` 是死胡同；`RECOVERY_REQUIRED` 表示需要按 journal 恢复（`airoot repair`）。完整迁移表是 `tx/states.py`，语义权威在核心契约 §14.1——本表只给最短解释，`repair --json` 会把某个事务当前的 `state` 交出来 | `tx/states.py` |

## `common.schema.json`

这一节是**定义处**：下面这些值定义在 `common` 里，被上面多个输出复用。你在别处看到同名同值的字段，回这里查。**例外**：`$defs.externalReference` 的三个字段（`capability_kind` / `management` / `source_kind`）与 `registry-projection` 的 `external_references[]` 是同一组词汇，行写在 `registry-projection` 那一节，这里不重复。

| 字段 | 取值 | 含义 | 本版谁写出 |
|---|---|---|---|
| `$defs.platform` | `windows` | v1 只有 Windows provider；别的平台保留接口不实现 | `registry/entities.py`, `registry/db.py` |
| `$defs.architecture` | `x64` / `arm64` / `x86` | 目标架构。`x64` 是这一版真的会构建的 | `registry/entities.py`, `caps/probe_pe.py` |
| `$defs.scope` | `system`† / `machine` / `session` / `project` | **绑定**的作用域：`system`（整台机器所有用户，P2 才有）/ `machine`（本机机器级绑定，`where` 与 machine PATH 的来源）/ `session`（只活在会话快照栈里，`deactivate` 会还原）/ `project`（项目自治，要 `--project`） | `caps/exposure.py`, `caps/planner.py`, `registry/entities.py` |
| `$defs.zone` | `R` / `W`† / `P`† | 分区：`R` 受保护可执行（**唯一能进 machine PATH 的分区**）/ `W` 可写但不参与机器级发现（ADR-0022）/ `P` 项目自治。**`W` 与 `P` 这一版都没有写者**：会话激活写的是会话快照栈、不建 binding，项目分区要 P6。也就是说 `where` 的 `machine_discoverable` 这一版**恒为真**——那条规则（"W 不做机器级发现"）已经生效，只是还没有任何对象落在 W 里 | `tx/artifact.py`, `tx/simulate.py`, `caps/where.py`, `registry/entities.py` |
| `$defs.health` | `healthy` / `degraded`† / `broken` / `stale`† / `drifted`† | 对象健康度。这一版只写 `healthy` 与 `broken`（坏掉但**保留为证据**）。注意：ACL 漂移与"索引过旧"**不写进这个字段**——它们分别是 `DATA_ROOT_ACL_DRIFT` 诊断与 `freshness.state=stale`，所以 `drifted`/`stale` 在这里没有写者 | `tx/artifact.py`, `tx/simulate.py`, `cli.py`, `registry/db.py`, `registry/entities.py` |
| `$defs.management` | `managed` / `external_reference` / `unmanaged` / `project_owned`† / `orphaned`† / `excluded` / `quarantined` | 与 AIROOT 的关系（`managed` 用于 AIROOT 拥有的实例行）。`orphaned` 这一版没有写者：孤儿是 `doctor`/`rebuild` 的**发现**，不是行上的取值 | `caps/discovery.py`, `caps/where.py`, `registry/entities.py` |
| `$defs.lifecycle` | `installed` / `active` / `retired` / `broken`（这四个是本版会写的）；`discovered`† / `external_reference`† / `unmanaged`† / `planned`† / `staged`† / `verified`† / `garbage_collectable`† | 生命周期：`installed` 已提交进 store / `active` 有 active binding / `retired` 已退役（**payload 还在**）/ `broken` 坏掉但留作证据。其余七个这一版不写：登记进 store 就直接是 `installed`/`active`，没有中间态；"可回收"是 `tool gc --plan` **算出来的**判据，不是行上的状态 | `tx/artifact.py`, `tx/simulate.py`, `caps/lifecycle.py` |
| `$defs.binding.exposure` | `stable_launcher` / `session_env`† / `project_binding`† / `none`† | 绑定是怎么暴露的：`stable_launcher` 是这一版**唯一**写出的（机器级绑定指向 shim）。`session_env`/`project_binding`/`none` 这一版都没有写者——会话激活**不建 binding**，它写的是会话快照栈；而不暴露的实例在这版里根本不建绑定行 | `tx/artifact.py`, `tx/simulate.py` |
| `$defs.source.kind` | `local_file` / `local_directory`† / `https` / `registry`† / `generated_fixture` | 计划里的来源种类：本地文件 / 本地目录（没用上）/ HTTPS（`https_artifact` 后端）/ 注册表（没用上）/ 测试生成的 fixture | `caps/sources.py`, `tx/artifact.py`, `tx/simulate.py` |
| `$defs.source.signature.algorithm` | `ed25519`† / `test_hmac_sha256`† | 来源证明的算法（与 `approval-token` 同词汇）。`ed25519` 这一版会显式报 `PROVENANCE_FAILED`(7)（ADR-0025 的 D1 维持现状：真实签名等 P2 的受保护存储）。**两个值都打 †**：这一版没有任何代码构造带签名的来源声明，`tx/approval.py` 只是**校验器**（§93） | （没有写者） |
| `$defs.fileManifestEntry.mode` | `file` / `directory`† | 文件清单条目是文件还是目录。`caps/canon.py` **只产文件条目**（目录不进摘要），所以 `directory` 没有写者 | `canon.py` |
| `$defs.sideEffect` | `none` / `derived_cache` / `writes_store` / `writes_registry` / `writes_path` / `executes_scripts` / `mutates_system` / `deletes_source` / `moves_source` | 副作用的**最坏情况词汇**。这个 build 里九个值都有声明者（能力清单与两个扩展 manifest）。`caps/boundary.py` 的准入判据是"按最坏的一个算"：声明里出现 `executes_scripts`/`mutates_system`/`deletes_source`/`moves_source`/`writes_path` 里的任何一个，就不是低风险 | `policy/capabilities.json`, `cli/extensions`, `caps/boundary.py` |
| `$defs.securityMode` | `protected_machine`† / `policy_only` | 安全模式。这一版写的一律是 `policy_only`：没有 ACL、broker、machine PATH，**同用户进程可以绕过**。`protected_machine` **没有写者**——没有任何调用点传过它（`caps/doctor.py` 与 `ext/envelope.py` 各有一个 `security_mode` 参数、默认就是 `policy_only`，`cli.py` 传的也是 `policy_only`）。**这个 † 是 §115 才补上的**：在那之前，这一行"有写者"的证据是 `doctor.py`/`envelope.py` 里那句 `security_mode == "protected_machine"`——一次**比较**被读成了**写出**；那条规则现在住在 `cli/app/airoot/posture.py`，而它是**映射**，不是写者。§115 之后"写者"这一栏要读成两半：`posture.py` 是**词汇的唯一处所**（`SECURITY_MODE` 只在那里拼写一次），另外三条是**把值写进文档的调用点** | `posture.py`, `caps/doctor.py`, `ext/envelope.py`, `cli.py` |
| `$defs.enforcement` | `acl_enforced`† / `same_user_can_bypass` | 上面那个模式对应的执行方式。这一版写的是 `same_user_can_bypass`——它不是"没有规则"，而是"规则靠约定与审计"。`acl_enforced` **没有写者**，理由与上一条同一个：这条映射的唯一权威是 `posture.py` 的 `ENFORCEMENT_BY_MODE`，而没有任何调用点以 `protected_machine` 问过它（问了才会有这个值，而"能算出"不等于"写出过"） | `posture.py`, `caps/doctor.py`, `ext/envelope.py`, `cli.py` |
| `$defs.dataRoot.role` | `runtime` / `tool` / `mixed` | 数据根的用途声明（默认 `mixed`）。它**只是声明**：`where`/`discover` 不会因为 `--role runtime` 就只找运行时 | `cli.py`, `registry/db.py`, `registry/entities.py` |

---

## schema 没有枚举的标签（判据在代码里）

有两组取值是**代码自由写的字符串**：schema 只声明 `type: string`／`type: [string, null]`，**没有枚举**。于是"合法取值"这件事没有 schema 可查，这一节的权威只能是**代码**——判据是"这个 build 真的会写出哪些值"，`cli/tests/test_l1_label_vocabularies.py` 每次跑测试把两边对一遍（**两个方向**都查）。

带 **∘** 的值表示它**同时**是注册过的 reason code（`cli/app/airoot/exits.py` 的映射表），遇到它可以再去 `references/reason-codes.md` 查；不带 ∘ 的只在本节有解释。

### `evidence[].kind`

每个命令的响应都带 `evidence[]`：`detail` 是人话，`kind` 说明这行是哪一类证据。**27 个值**：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `query` | 这次查询/请求的条件本身（`where` 的 capability/version/scope，`plan` 的 scope/project） | `caps/where.py`, `caps/planner.py` |
| `whitelist` | 能力白名单里没有这个条目，所以落到规则判断 | `caps/planner.py` |
| `memory` | `.ai/tooling.json` 里记录过这个能力的 scope 声明 | `caps/planner.py` |
| `project_manifest` | 由项目清单声明 —— 因此项目内隔离、**不询问** | `caps/planner.py` |
| `source` | `source resolve` 取不到可信来源：只能 reference/import，不能装 | `caps/planner.py` |
| `high_risk` | 命中高风险三类（装包 / 建环境 / 超阈值），**必须确认** | `caps/planner.py` |
| `generic_tool` | 命中了"单文件通用 CLI"规则：装到数据根，**不询问** | `caps/planner.py` |
| `fallback` | 没有规则能证明这是显然情况 —— 所以按"需要看"处理，而不是硬猜 | `caps/planner.py` |
| `discovery` | 白名单发现时的观测（例如入口点在对象根之下几层） | `caps/discovery.py`, `cli.py` |
| `pe_static` | 只读 PE 静态探测到的元数据（不执行它） | `caps/discovery.py` |
| `version_set` | 对象内观测到的版本集合（用来判断"有满足约束的版本但未激活"） | `caps/discovery.py`, `caps/where.py` |
| `weak_evidence` | 证据弱（例如只靠名字匹配）—— 所以版本/身份只能说"未知" | `caps/discovery.py`, `caps/where.py` |
| `junction_target` | reparse point 指向哪里（这是它可疑、要被隔离的原因） | `caps/discovery.py` |
| `binding` | 选中的绑定与它的 generation | `caps/where.py` |
| `registry` | 声明/registry 侧的事实，含"声明了但不健康"与"没有满足约束的候选" | `caps/where.py` |
| `layout` | 布局问题：payload 标记不在该在的地方 | `caps/where.py` |
| `external_reference` | 引用侧的观测：观测时间与 management，或"记录的入口点不见了" | `caps/where.py` |
| `policy` | 生效的选择策略（precedence / revision） | `caps/where.py` |
| `effective` | 有效事实复核：新进程看到的与当前进程看到的可能不同 | `caps/where.py` |
| `search_roots` | 这次搜索**实际**用的 root 列表 | `caps/search.py` |
| `search_policy` | 生效的搜索策略 revision 与 `consistency` | `caps/search.py` |
| `search_source` | 答案来自**索引**还是**实时遍历**（以及索引 generation） | `caps/search.py` |
| `search_index` | 索引自己的读数：`built_at` / `records` / `coverage` | `caps/search.py` |
| `search_scope` | root 是从哪来的（显式 `--search-root`，还是注册的数据根） | `cli.py` |
| `manifest` | 这个扩展是从已发布 schema 载入的（假扩展用它证明通信协议） | `ext/fake.py` |
| `self_test` | 假扩展的自检：不碰任何外部状态 | `ext/fake.py` |
| `failure` | 失败清理项（模拟事务留下的） | `tx/simulate.py` |

### `where.selection_reason`

`where` 的响应里有**两个像码的字段**：`reason_code`（注册词表里的码）与 `selection_reason`（这一节）。**它们不是同一套词表**：`selection_reason` 只回答"为什么选了它 / 为什么没选"，12 个值里只有 3 个同时也注册为 reason code（下表带 ∘ 的那三个）。别再把这 12 个当成 `reason_code` 的取值去查表。

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `PROJECT_MANAGED_HEALTHY` | 项目内有绑定 —— 项目优先（冻结的先后） | `caps/where.py` |
| `SESSION_MANAGED_HEALTHY` | 会话内有绑定（仅次于项目） | `caps/where.py` |
| `STEWARD_REFERENCE_HEALTHY` | 选中的是一个**健康的外部引用**（steward-first 的正常结果） | `caps/where.py` |
| `MACHINE_MANAGED_HEALTHY` | 机器级 owned payload 健康，且策略默认 `steward` | `caps/where.py` |
| `MACHINE_MANAGED_HEALTHY_BY_POLICY` | 同上，但策略显式把 owned 排在引用前面 | `caps/where.py` |
| `CURRENT_SOURCE_DEGRADED`∘ | owned payload 坏了，**正常降级**到健康引用 —— **不是失败**（退出码见 `reason-codes.md` 的同名条目，本表不重复记它） | `caps/where.py` |
| `MANAGED_NOT_HEALTHY` | 机器级 owned 被声明了但 health 不健康，且没有引用能顶上 | `caps/where.py` |
| `VERSION_UNSATISFIED`∘ | 有候选，但不满足版本约束 | `caps/where.py` |
| `VERSION_AVAILABLE_BUT_INACTIVE` | 对象里有满足约束的版本，但它不是活跃版本 —— **切不切是用户的决定** | `caps/where.py` |
| `REFERENCE_NOT_USABLE` | 引用存在但不可用（入口点缺失等） | `caps/where.py` |
| `UNMANAGED_ONLY` | 只有没登记的东西可指 | `caps/where.py` |
| `NOT_FOUND`∘ | 没有候选 | `caps/where.py` |

### `operation`（每个响应信封的顶层）

`--json` 的顶层 `operation` 回答"**我手里这份文档是哪条命令产出的**"。schema 只在 `search-response` 与 `broker-request` 里枚举过它，所以其余文档没有权威可查。**13 个值**（`plan` 自己的 `operation` 是另一个字段，见上文 `plan.schema.json` 那一节）：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `search` | `search <query>` 的响应信封 | `caps/search.py` |
| `status` | `search status`：索引状态 + 新鲜度 | `cli.py` |
| `explain` | `search explain`：会用索引还是实时遍历 | `cli.py` |
| `refresh` | `search refresh`：重建索引 | `cli.py` |
| `pin` | `tool pin`：记下一个愿望 | `cli.py` |
| `gc_plan` | `tool gc --plan`：回收预演。**这个名字和已发布的 `gc-plan.schema.json` 只差一个连字符，指的却不是它**——那份 schema 描述的是一份**批量**回收计划（`items`/`blocked_items`/`requires_approval`），而这条是报告信封；本版真正的回收计划是 `plan.schema.json`（`operation=gc_apply`，一次一份 payload）。两者同名不同物，交叉记录在 `docs/schema/README.md`，守卫第三十四组两个方向都查 | `cli.py` |
| `rebuild_plan` | `rebuild --plan`：重建预演 | `cli.py` |
| `rebuild` | `rebuild`：真的重写派生投影 | `caps/rebuild.py` |
| `plan_dry_run` | `plan --dry-run`：只出计划不落盘 | `cli.py` |
| `uninstall` | `uninstall`（含 `--dry-run`） | `cli.py` |
| `apply_reference_exposure` | 应用一次引用暴露（`env persist` 的写入阶段） | `caps/exposure.py` |
| `forget_reference_persist` | `env forget <ref>`：还原一个能力写过的环境 | `caps/exposure.py` |
| `forget_all_persist` | `env forget --all`：还原 AIROOT 写过的**全部**环境 | `caps/exposure.py` |

### `origin`（**两个意思，别混**）

这个名字在两种文档里各有一个意思：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `generic_tool` | 分流 origin：单文件通用 CLI → 数据根，不询问 | `caps/planner.py` |
| `high_risk` | 分流 origin：命中高风险三类 → 必须确认 | `caps/planner.py` |
| `memory` | 分流 origin：`.ai/tooling.json` 里的既有选择优先 | `caps/planner.py` |
| `project_manifest` | 分流 origin：项目清单声明了它 → 项目内隔离 | `caps/planner.py` |
| `no_capability` | 分流 origin：能力没在任何地方声明过 → 根本不可路由 | `caps/planner.py` |
| `unclassified` | 分流 origin：所有规则都没命中，也没有显然的兜底 | `caps/planner.py` |
| `unverifiable_source` | 分流 origin：来源不可验证（不能装，只能 reference/import） | `caps/planner.py` |
| `local_file` | **载荷** origin（只在 `metadata.import.origin`）：这次导入的源是本地文件 | `cli.py` |

路由 origin 出现在 `scope decide --json` 的顶层 `origin`、以及计划的 `metadata.import.routing.origin`；载荷 origin 只在 `metadata.import.origin`。**看到 `origin` 先看它在哪一层**。

### `size_source`

`size_source` 回答"这个体积数字是**哪来的**"——批准一个人要复制 250 MB 的人有权知道它是量出来的还是别人报的。**3 个值**：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `declared` | 调用方**声明**的体积（没有量过） | `caps/planner.py` |
| `unknown` | 没有体积信息 —— **不猜** | `caps/planner.py` |
| `measured-from-the-file` | 这个命令**真的量过**（`adopt --mode import` 逐字节读过） | `cli.py` |

### `version_source`

同上的问题，问的是**版本**从哪来。**2 个值**：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `declared-by-caller` | 调用方给了 `--version` | `cli.py` |
| `no-version-in-the-file` | 文件里没有可读的版本 —— 如实说没有 | `cli.py` |

### `outcome`

一次**结果**的粗结论（`env persist` / `env forget` 的结果文档，以及 `logs/audit/events.json` 的事件）。**2 个值**：

| 取值 | 含义 | 本版谁写出 |
|---|---|---|
| `ok` | 这一步做成了 | `caps/exposure.py` |
| `failed` | 这一步失败了（细节在 `errors`/`reason_code` 里） | `tx/journal.py` |

---

## 不在这张表里的 schema

| schema | 类别 | 为什么不列 |
|---|---|---|
| `root-marker.schema.json` | `no-own-vocabulary` | **这一版会写出它**——根标记文件是被构造的 schema 之一（`state/root.json`）。它被列在这里是因为**它自己没有词汇**：`schema_version` 与 `protocol_version` 两个字段都是**版本钉**（见本节末尾）。**原先这里写的是"同上"**（即按两个 `broker-*` 那样归到"P2 未实现"），那句话是**错的**，§107 量出来后改的 |

**这张表现在只剩一类，因为另一类**（`unbuilt`，"核心没有构造者"）**在 §119 失去了最后一个成员**。
它的两个成员是 §108/§110 时代的 `broker-request` 与 `broker-response`：前者在 §108 离开（`protocol.py`
的 `build_request` 成了写者），后者一直留在这里，理由是"§110 的替身造它不算核心造它"——而 **§115 的
`broker/pipe.py` 的 `_answer` 就是核心的构造者**，那句话从此不成立。**这个漏洞被守卫漏了一整轮**：
`_produced_schemas` 当时看不见 `document.update(posture())` 合进来的两个键，所以它把这份文档报成
"没人造"，与文档的说法一致地错着；§119 补上了这条合并形状之后，判据才第一次说出实话。**一个没有成员的
类别就是一个读者会遇到却查不到用处的词**，所以 `unbuilt` 这个词整个删掉（与 §82 删掉 `needs-decision`
同一个理由）。**"谁写出"这个问题没有失去覆盖**：它由 `docs/schema/README.md` 那份**没有写者的 schema
清单**接着答，那一份由守卫第三十四组双向钉死（依据是校验调用点与 `$ref` 图，不是语法走法）。

这张表只回答"**为什么不给它单独一节**"。"有没有人记录"由另一条判据守着（§105/§106）：**任何一个已发布
schema 能携带的词汇**（跟 `$ref`、含唯一取值的 `const`），要么在表里有行，要么在 `UNDOCUMENTED_BY_DESIGN`
（`test_l1_field_values.py`）里被**点名**。**今天那个集合是空的**：最后一条 `broker-response.status`
在 §119 跟着这份文档的 `broker-response` 一节一起走了——§108 的 `broker-request` 与 §119 的
`broker-response` 都是"有了写者就给词汇一行"的同一个动作。空集合是**允许**的（守卫两个方向都断言：
没被点名的未记录词汇必须为零，被点名的必须真的还没记录），但它不是"没有这条判据"。

**一套词汇"不在表里"只有三种合法理由**，而且这三种各有判据：

1. **它在别处有行**——共享词汇（`common` 的 `$defs`）写在"定义处"那一节，别的文件不重复；
2. **它被点名豁免**——`UNDOCUMENTED_BY_DESIGN`，双向断言（**今天一条都没有**）；
3. **它是版本钉**——`VERSION_PIN_FIELDS` 里的字段名（`schema_version`/`protocol_version`/`schemaVersion`），
   值恒为 `1`，由 `docs/schema/README.md` 规则 1 与 `test_l1_schema_catalog` 守着，**不写表行**。

类别那一列（今天只剩 `no-own-vocabulary`）不是给人看的标签：它是**判据读的那一格**。
"为什么不在表里"这件事的**声明**在文档里，**度量**在 `test_l1_field_values.py` 的
`_measured_exempt_class` 里（"这一版有没有构造者" + "它自己有没有词汇"），两者必须一致——
所以"给它编一个理由"这件事现在是做不到的。

**"构造者"这个词是字面意思**（§108 量出来的一个后果）：这份表与它的守卫都只看 `cli/app/` 里的函数
有没有把一份文档**造出来**。一个**读者**不算写者，**测试替身也不算**——§110 把这条语义实测过一次：
进程内 loopback harness 按设计就住在 `cli/tests/`（它不该住进那个用户可写、不被信任的包里），它开始
构造 `broker-response` 之后，那一格**仍然是** `unbuilt`，因为测试替身不是产品文档的写者。**§119 把它翻
过来纠正了一次，方向正好相反**：读法与替身都没让 `broker-response` 有写者，而 §115 的 `pipe.py`
（**核心**里那个真的 `_answer`）有——所以那一格该走。同一个词的两个方向都量过，才是它现在的意思。
