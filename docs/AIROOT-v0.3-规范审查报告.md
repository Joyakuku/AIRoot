# AIROOT v0.3 规范审查报告

## 审查范围

本报告审查以下方案文件的内部一致性、可实现性边界、Skill/CLI 规范性和测试可验证性：

- `AIROOT-总体方案规划-v0.3.md`
- `AIROOT-v0.3-三大核心契约方案.md`
- `AIROOT-搜索能力与工具集成协议方案.md`
- `AIROOT-v0.3-验证与测试方案.md`

审查基准是用户已经确认的产品边界：AIROOT 本身是 Skill 根目录，正式入口是 AIROOT/SKILL.md；AIROOT/cli 是 CLI、能力扩展和运行数据根；Capability Extension 代表能力协议模块，Managed Tool/Runtime Instance 代表 AIROOT 实际维护的具体 payload/运行时；手工脱离 Skill 安装的对象默认只发现、不自动迁移；同一能力 binding key 只有一个 active implementation。

附件 `AIROOT-PLAN-v0.2.md` 被作为历史设计材料和约束来源进行比对；其中的说明不会覆盖本轮用户明确确认的目录、Skill、能力扩展和“不自动迁移”边界。

本阶段没有创建生产 CLI、没有创建系统 PATH、没有注册表写入、没有 ACL 变更，也没有把 Everything 或其他外部软件放入 Skill。

## 结论

方案的产品边界和总体架构是合理的，已经可以作为实现前评审基线，但还不能称为“实现契约完全冻结”。当前状态应标记为：

```text
架构方向：通过
目录/对象边界：通过
跨文档核心状态机：通过
Extension/Install Backend 分离：通过
Managed Tool/Runtime 生命周期：通过
搜索 profile：通过，正式 JSON Schema 已生成并完成 meta-schema 校验
权限强制性：有条件通过，依赖受保护 broker 和签名应用层
生产实现就绪：未通过，需完成实现前门槛
```

最关键的判断是：AIROOT 可以做成 Skill，也可以做成 CLI；Skill 适合自然语言适配和行为约束，CLI/Core 才是状态、权限、索引、事务和恢复的可信执行面。只有 `AIROOT\SKILL.md` 位于根目录并调用稳定 CLI 协议时，才符合这个边界。

## 已解决的 P0 问题

| 问题 | 修正后的规范 | 状态 |
|---|---|---|
| Extension、Provider、Install Backend 混用 | `extension_id` 是能力模块，`implementation_id` 是 active 实现，`install_backend_id` 是安装后端；`provider` 不再作为新协议泛称 | 已解决 |
| 通用 Envelope 与搜索响应不一致 | 所有 Extension 使用 `schema_version/extension_id/operation/status/timing/data/warnings/evidence/reason_code`；Search 的结果放在 `data` | 已解决 |
| 事务顺序冲突 | `COMMITTED -> REGISTERED(inactive) -> ACTIVE_BOUND -> EXPOSED -> VERIFIED_AGAIN -> FINALIZED` | 已解决 |
| 搜索索引位置和 ACL 冲突 | 索引、卷游标和派生数据放在 `AIROOT\cli\cache\search`；SQLite registry 在 `state\registry.db` | 已解决 |
| Skill 根和 CLI 根含义不清 | `AIROOT\SKILL.md` 是入口，`AIROOT\cli` 是运行根；应用层和持久数据层分离 | 已解决 |
| 同一类型多个默认工具 | active 唯一性按完整 binding key 强制；fallback 是执行模式，不是第二个 active 实现 | 已解决 |
| 外部对象自动迁移风险 | `discover -> classify -> report -> explicit reference/import/recreate`；默认不移动、不接管、不改 PATH | 已解决 |
| `tools`/`env` 与 payload 关系不清 | `store` 是唯一 payload；`tools`/`env` 是 binding/view；`exposure` 由 registry active binding 解析 | 已解决 |
| “工具”被收窄为 Extension，遗漏实际受控 tools | Managed Tool/Runtime Instance 单独建模；通过 registry + store + binding/view + exposure 管理；External Reference 不自动升级 | 已解决 |

## 已补足的边界行为

### Managed Tool 与 Capability Extension 的边界

此前方案把“工具”单独解释为 Capability Extension，容易让读者误解为 AIROOT 不维护 jq.exe、ffmpeg.exe、Python、Node 等受控对象。现已固定以下关系：

    Capability Extension  -> 定义能力协议和 invoke/query 行为
    Managed Tool Instance -> 保存 AIROOT 实际维护的 payload、manifest、健康、binding 和版本
    Managed Runtime       -> 保存 Python/Node 等 runtime/environment 实例
    External Reference    -> 只记录外部对象，不拥有生命周期
    Install Backend       -> 获取、验证、stage、commit、rollback

Managed Tool 的更新使用新 instance + 新 generation；旧 active 在新版本通过验证前不得删除。retire 和 gc 分离，doctor 不自动删除；搜索发现的外部可执行文件不自动进入 managed tools。
生命周期图中的 `planned` 只由显式 `import`、`recreate` 或 `adopt --mode import` 计划产生；`discover`、`where`、`doctor` 和 `rebuild` 不会自动推进外部对象。`env\\runtimes` 只保存 Runtime Instance 的 binding/view 和 launcher metadata，runtime payload 仍统一存放在 `store`。

### 外部发现和迁移

- source 在 plan 后 hash、版本、架构、路径或权限改变：重新 probe，digest 不一致即失败，source 和旧 binding 保持不变。
- source 没有可靠 digest、来源或静态 manifest：可以 reference 和报告，但 import/recreate 只能生成待审批 plan，不能直接写入受保护区。
- 目标 instance 已存在且 digest 相同：幂等返回；digest 不同：拒绝覆盖，要求新的 instance identity。
- 目标 binding key 已占用：默认拒绝；替换必须有显式 policy、new generation 和批准记录。
- import/recreate 取消、超时或失败：清理 AIROOT stage，保留原 external reference 和 source。
- `unadopt` 只删除 AIROOT 引用，不删除 source；仍被项目声明使用时拒绝解除。
- AIROOT 根内无 registry 的对象：先标记 `orphaned/recovery_required`，不能用普通 import 覆盖。
- source 消失、权限改变或 hash 漂移：变为 `stale`/`drifted`，不自动修复、下载替换或删除。

### Active binding 和事务

- `REGISTERED` 明确表示 inactive，launcher 不能选择。
- 只有 `ACTIVE_BOUND` 事务点可以改变 active binding；generation、journal、event table 必须一起记录。
- `EXPOSED` 是 launcher/where 观察到新 binding，不是第二个权威状态。
- 验证失败只切换回旧 binding，新 instance 保留为 `broken`/`retired` 以便调查。
- 跨卷移动不假设文件系统原子性，使用 copy + verify + switch，旧 root 在新 root 验证前只读保留。

### 审批和副作用

- `airoot approve` 只消费受保护 issuer 签发的 token，不生成“人工批准”。
- token 绑定 `plan_hash`、root、machine、policy revision、issuer、签名、有效期和一次性 nonce。
- 重放、撤销、过期、签名无效、policy revision 改变或 plan 内容改变：拒绝提交并写审计事件。
- operation 必须声明 `operation_kind`、`target_scope`、`approval_required`、`overwrite_policy` 和 `cancellation_semantics`。
- source 的 delete/move/overwrite 不能隐藏在 Install Backend 的 commit 里，必须出现在 plan 和批准范围内。

### 搜索

- 搜索结果默认是调用者重新做 ACL 过滤后的结果；机器级 indexer 不等于跨用户可见。
- `roots` 为空不扫描整机；root 越界、UNC、未允许 reparse point 直接失败。
- cursor 绑定 query、root、scope、implementation generation 和 index generation，任一变化返回 `SEARCH_CURSOR_INVALID`。
- journal gap、卷卸载、权限变化和索引损坏进入 degraded/rebuild，不继续使用无法证明连续性的 cursor。
- crawl fallback 仍保留 Native Search active implementation，并明确 `status=degraded`、`fallback` 和 freshness。
- `physical_verify` 期间对象消失或重命名时，返回变化证据，不伪装为当前稳定事实。

### Shell 和 Skill

- `env activate` 不能修改已经存在的父 PowerShell/cmd 进程；只能输出 shell script/JSON，或由 `airoot exec` 创建带环境的子进程。
- session 记录 session ID、environment generation 和原始变量；generation 过期或 root identity 改变时激活失败。
- Skill 不直接写 PATH、registry、ACL、R 区或外部软件私有参数。

## 已生成的实现前基线

本轮已生成三项可审查基线：

1. `AIROOT\\cli\\schema\\`：18 个 JSON Schema（**审查时的数字；现为 20 个**，`reference-plan` 是第 19 个、`error-response` 是第 20 个，见文末状态节），覆盖 registry projection、root marker、managed tool/runtime、desired manifest、plan、approval、Broker IPC、transaction、Extension manifest/envelope、Search、where、doctor 和 GC；
2. `AIROOT\\cli\\broker\\AIROOT-受保护Broker方案-v1.md`：Protected machine mode 的信任边界、IPC、批准 token、ACL、提交算法、恢复和审计契约（实际位置为 `docs\\broker\\`）；
3. `AIROOT\\cli\\fake_vertical_slice\\`：固定 fake artifact、SQLite WAL simulation、fault injection、digest drift、approval replay 和 recovery runner。

Schema runner 已通过全部 18 个 Schema 的 meta-schema 检查（**审查时的数字；现为 19 个**），并验证 plan/approval fixture；fake vertical slice 已通过完整事务和负向测试。该 slice 使用 `test_hmac_sha256`，只代表测试 issuer，不代表生产签名实现。

## 仍需在实现前冻结的 P1 项

这些不是方向性缺陷，但不冻结就不应进入生产实现：

1. Schema 的正式迁移工具、registry/event 表结构、event 保留期限，以及 `logs\audit` 重建和导出协议。
2. 当前 Skill scaffold 目录还没有根级 `SKILL.md`；在创建它之前只能称为方案文档，不能称为可安装 Skill。
3. `state/events` SQLite 表结构、event 保留期限，以及 `logs\audit` 重建和导出协议。
4. Protected machine mode 的 bootstrap：broker 二进制、应用 manifest、签名/哈希来源、ACL 初始化和降级条件。
5. human approval 的具体 UI/IPC 通道；必须能证明 issuer、approved_by_sid 和一次性 token 消费。
6. Native Search 的进程模型：用户级 indexer、受限 broker 操作、跨用户索引隔离、USN Journal 读取权限。
7. `machine_id`、`session_id`、`project_id` 和 project root canonicalization 的生成算法。
8. v1 capability 清单，以及 `file_search` 是否只提供 Native Index 或允许显式 Everything adapter。
9. Windows 不支持 NTFS、卷卸载、journal reset、杀毒软件锁定和长路径的 fallback 策略。
10. 真实 Windows runner、ACL/UAC/named-pipe fault injector 和每个事务状态的系统级崩溃恢复 fixture；fake slice 已覆盖无特权的协议级验证。

## 规范性检查结果

已对方案文本执行跨文档关键词和状态扫描，当前确认：

- 方案正文没有旧盘符根目录、旧 skill-dir 或错误的 `cli\SKILL.md` 规范路径残留；
- 方案正文没有把搜索索引继续定义在旧的 state 搜索目录；
- 方案正文没有把旧 provider 标识作为新协议字段；
- 四份文档都使用 `ACTIVE_BOUND` 和同一事务顺序；
- 退出码已统一为 0-9，并要求 Search reason code 映射到通用退出码；
- Search 和通用 Extension 都使用 `data` 包裹 profile 结果；
- `where` 已包含 `management`、`usable`、`selection_reason`、`source`、`candidates`、`evidence` 和 `found=false` 语义；
- 测试方案已覆盖 active 唯一性、approval token、cache ACL、Skill 更新、root relocate、外部 source 变化和安全 probe。
- 测试方案已覆盖 Extension 与 Managed Tool 分离、多版本共存、旧 active 回滚、retire/GC 引用保护和搜索发现不自动 adopt；fake vertical slice 已执行其中的协议级子集。

`provider` 在少量地方仅作为 Terraform 等开源项目的原始术语、兼容字段负面测试或旧文档迁移说明出现，不属于 AIROOT 新 API 的泛称。

## 测试就绪判定

方案阶段可以称为“测试备齐”的最低条件是：

1. L0 能验证 canonical path、schema、plan hash、状态转移、退出码和 policy；
2. L1 能验证 SQLite WAL、generation CAS、store/cache 分离、幂等和故障注入；
3. L2 能验证 Windows ACL、machine PATH、UAC、PowerShell/cmd session 行为；
4. L3 能在 `ACTIVE_BOUND`、`EXPOSED`、registry 写入、验证失败等边界终止进程，并证明 repair 结果；
5. 每个 JSON fixture 都能区分 not found、degraded、broken、approval、privilege、recovery 和 extension unavailable；
6. 测试不修改开发机 PATH、注册表、ACL 或真实 AIROOT root。

在这些门槛完成前，不能声称“已经实现 AIROOT”或“已经具备 Everything 级性能”；可以声称“方案契约和验证计划已具备，生产实现仍待评审门槛通过”。

## 审查结论

AIROOT 的边界现在足够明确：它维护能力协议、状态、权限、索引和恢复，也维护明确纳入范围的 Managed Tool/Runtime Instance；它不接管所有外部软件。Skill 是适配层，CLI/Core 是执行层；外部手工安装对象默认被发现和分类，只有显式 reference、import、recreate 或安装计划成功后才进入相应生命周期。下一阶段可以进入受控实现评审：先完成真实 Windows Broker 的签名、ACL、named-pipe 和 UAC 方案，再把 fake vertical slice 的状态机替换为受保护执行面。当前交付仍不是可安装 Skill，也不是生产 CLI。

---

## P1 实现状态（实现后追加）

本节记录规划 §19 的实现结果，是本报告的后续状态，不改变前文的审查结论。
**本节随实现推进而更新：它描述的是当前状态，不是写入时的快照。** 前文（§「已生成的实现前基线」及以上）
是审查当时的记录，其中的计数按当时为准。

**当前规模**：`cli/schema/` **20** 个 JSON Schema；`pytest cli/tests` **1294 项**（含 **108** 项常驻跨工件
一致性审计 `cli/tests/test_l0_consistency.py`）；golden 语料 **42** 个 fixture
（`cli/tests/fixtures/golden/`，Rust 版逐字节验收面）；两份契约文档合计定义 **108 个场景编号**，
台账见 `cli/tests/fixtures/golden/scenario_ledger.json`；决定拒绝的每个数值（搜索上限三件套、
`MAX_ROOTS`、爬取与索引边界、白名单扫描边界、三选一、优先级、artifact 与 PE 检查字节上限）
见 `cli/tests/fixtures/golden/execution_bounds.json`。

**已交付**（Python 3.11，唯一第三方运行时依赖 `jsonschema`）：

| 交付项 | 位置 |
|---|---|
| root 解析、路径规范化（含 `\\?\` 扩展长度共享原语）、卷身份守卫 | `cli\app\airoot\{root,paths,canon,exits,clock,schema_io}.py` |
| SQLite registry、WAL、migration（至 v5）、generation CAS、JSON 投影 | `cli\app\airoot\registry\` |
| 事务状态机、journal、审批校验、模拟事务、repair | `cli\app\airoot\tx\` |
| `where`（**steward-first**）确定性选择、版本约束、effective state、`doctor` D1–D10、`inventory` | `cli\app\airoot\caps\` |
| Capability Extension 协议与假扩展 | `cli\app\airoot\ext\`、`cli\extensions\airoot-fake-extension.json` |
| CLI 与开发期 launcher | `cli\app\airoot\cli.py`、`cli\bin\airoot.cmd` |
| **管家域步骤 1–9、11–12**：数据根注册（可跨卷）、只读 PE 静态探测、能力白名单发现、`adopt --mode reference`、依赖分流与确认、会话级环境激活、**user 级环境变量持久化**（plan → approval → 写入 → 精确还原）、删除语义分级、能力边界、`rebuild`、来源清单、`desired` 层与 `tool pin`、只读观察面、session 快照栈 | `cli\app\airoot\caps\`、`policy\{discovery-whitelist,sources,selection-policy,capabilities}.json` |
| **`search` 协议面与 crawl 建的持久索引**（**不是** USN 索引）：请求/实现上限/root 规则/cursor 绑定索引 generation、有界 crawl、`cache\search\index.db` 整文件原子替换、索引状态接进 D7、**只读** USN 能力探测 | `cli\app\airoot\caps\{search,searchindex,usn}.py`、`policy\search-policy.json` |
| **Skill 适配层**：`SKILL.md` 是仓库根的唯一 Skill 入口，另有机器可读调用元数据与按需参考 | `SKILL.md`、`agents\airoot.json`、`references\` |
| L0/L1 测试与语言无关 golden 语料 | `cli\tests\`（**1294 项**）、`cli\tests\fixtures\golden\`（**42 个 fixture**）、`cli\tests\scenario_ledger.py`（**108 个场景编号**的解析器与处置表，含每条 `undesigned` 的证人）、`cli\tests\execution_bounds.py`（**决定拒绝的每个数值**的解析器与来源声明）、`references\confirmation.md`（三选一与"记忆只读"已绑到代码） |

**P1 退出条件已验证**：

1. 不接真实外部软件也能跑通状态和协议——`test_p1_exit_condition_one_*` 通过 CLI 完成
   plan → approve → install → where → doctor，并断言 payload 从未被执行；
2. registry 损坏与 generation 冲突有明确结果——损坏报 `REGISTRY_INTEGRITY_FAILED` 且
   **不重写证据**；过期 generation 报 `STALE_GENERATION`（退出码 2），旧状态不被覆盖。

**已修复的规范缺陷**（详见 `AIROOT-v0.3-实现决策记录.md`）：

- `transaction.schema.json` 补齐 `ROLLED_BACK`——原文无法表达 §14.1 的合法状态；
- 成功 reason code 由 `OK` 改为 `SUCCESS`——`OK` 只有 2 字符，无法满足所有 Schema 的
  `reason_code` 模式；
- Extension manifest 以 `operations` 为准（文档示例的 `operation_policies` 与
  `replace_derived_cache` 会被 Schema 拒绝）；
- `where-response`/`doctor-response`/`extension-envelope` 增加**可选**的
  `security_mode`/`enforcement`，使 P1 能诚实声明 `policy_only`（验证方案 P-013）；
- 搜索面另有三处「文档与 Schema 冲突」由 ADR-0017/ADR-0018 记录，派生缓存可替换的边界由
  ADR-0019 记录，USN 索引器的时机由 ADR-0020 记录。

**仍缺的项（当前状态）**：

- registry migration **工具**（列出/回滚）、event 保留期、`logs\audit` 导出协议——迁移本身已到 v5，
  缺的是这些外围；P1 只有 `migrations` 骨架与可重建的 audit 投影；
- **根级 `SKILL.md` 已于步骤 9b 交付**（本报告此前把它列为缺失，那条陈述已经过时）：`SKILL.md`
  现在是仓库根的 Skill 入口，并带 13 项漂移守卫；
- broker bootstrap、human approval 通道仍缺（属 P2）：目前只有测试用 `test_hmac_sha256` issuer。
  **`ed25519` 校验已落地**（§113 / ADR-0039，RFC 8032 纯 Python）：签名不对的 token 报
  `INVALID_APPROVAL`(4)，`PROVENANCE_FAILED`(7) 只剩"整个 root 没有 keyring"一种触发方式；
  缺的是**签发**——私钥还没有受保护的家；
- **P2 第一阶段的线路面（客户端那一半）已交付**（草案 §108）：`caps\identity.py` 只读本进程
  token、`broker\protocol.py` 造/验 `broker-request` 并解 `broker-response`、`broker_unavailable()`
  报 `NOT_IMPLEMENTED`(1)，外加第一份 broker 语料。**它不证明信任边界**：broker 本体、named pipe、
  对客户端 token 的校验、machine PATH / launcher 仍全部未交付；
- **P2 的第二阶段：受保护边界要问的第一个问题已作为库交付**（草案 §114 / ADR-0041）：
  `caps\identity.py` 对**别人**的观测补齐到九件事（SID / 完整性级别 / 是否提权 / 提权类型 / 会话 id /
  是否 AppContainer 及其 SID / 进程创建时间），其中两道**交叉核对**（`elevated` 与 `elevation_type`、
  `TokenSessionId` 与 `ProcessIdToSessionId`）不一致时报"这个事实读不出来"，而不是取其中一边；
  `broker\policy.py` 的 `admit_caller(identity, expectation)` **只吃观测到的 token**——请求里的 `client`
  块结构上进不来（`ProcessIdentity` 没有 `application_id` 字段，函数签名里也没有"声明"参数），拒绝时
  报新增的 `CALLER_NOT_AUTHORIZED`(5)，`details.rule` 指认是哪条规则。**它仍不是那条边界**：没有
  named pipe、没有提权进程、**没有任何动词走到它**——连 `allowed_sids` 与 `minimum_integrity` 都只是
  显式入参，本阶段刻意没有发明策略文件，也没有把根目录的 owner 当成"谁可以问"（Protected machine mode
  下它很可能是 Administrators，那会把合法用户拒掉）；
- 第 7 条（`machine_id`/`session_id`/`project_id` 生成算法）**刻意未发明**：只接受夹具注入或显式入参；
- **machine 级**环境变量持久化、machine PATH 写入、`exposure\bin` launcher：均属 P2；
- **ACL 的写一侧已作为库交付**（§113 / ADR-0040：baseline/apply/verify/restore，`SetSecurityInfo`，
  非空基线保护式写入、空基线作为发现被拒）；**缺的是调用者**——把基线**强加**回目录要有 broker，以及一条
  "调用者写完还活着"的判据（§114 只把那条判据要读的**别人 token**补齐了）。**读一侧已交付**（§58）：
  `cli\app\airoot\caps\acl.py` 只读 owner/DACL（`ctypes`，无需提权、无新依赖），`doctor` 在数据根基线
  漂移时发射 `DATA_ROOT_ACL_DRIFT`。ADR-0023 定下语义：基线是**观测**，所以"漂移"意为"与我们记录的不同"
  而不是"你违反了某条要求的 ACL"，`remediation=repair` 是**重新记录基线**，不是"把 ACL 恢复回去"
  （那会撤销一次用户有意做出的更改）；
- **真实 artifact 的下载与验证**：**已对真实上游执行过一次**（§59，证据是 `real_machine_acceptance.py
  --online` 的输出：`static.rust-lang.org` 的 `rustup-init.exe` **12 721 664 字节**，SHA256 与上游发布的
  校验和一致）。这是**一次记录下来的实测**而不是常驻检查——网络不进 `pytest`（套件必须自足），
  不传 `--online` 时脚本**自报 not run**。`stage`/`commit` **没跑过**，因为 P1 没有生产批准签发方；
- `file_search` 的 **USN 常驻索引器**：属 P2（初始全量枚举需要 broker）；协议面与受控 crawl 已可用；
- `reconcile`、`path backup|restore`、`root adopt|relocate`：已按命令路径登记为未实现，并且
  **每条都写明了"为什么"与"什么才能解锁它"**——`agents\airoot.json` 的 `deferred` 分三类
  （`needs-admin` / `needs-decision` / `needs-capability`），由守卫第二十三组与 `AGENTS.md` §8
  双向绑住（草案 §60）。其中 `reconcile` 是**审计改判过的那一条**：它先被记成"内容已被别的动词覆盖、
  今天会是同义词"，重新推导后改判为 `needs-capability`——规划把它定成 `reconcile <manifest>`、
  排在 P6 的 `project manifest` 之后，而 CLI 自己也这么说（`forget` 的输出里有
  `project_manifest_check: "not_implemented_before_p6"`）。它与 `doctor`/`desired`/`plan` 的重叠是真的，
  但那是这个命令较小的一半；第四类 `covered-elsewhere` 因此**在发明它的同一轮里被删掉**，因为不再有成员；
- `.ai/tooling.json` 的写入。
- **108 个场景编号里有 55 个没有任何测试点名**（台账 `scenario_ledger.json` 逐条给出结构性理由：
  4 个需要 P2 受保护状态、1 个需要 P4 真实 artifact、9 个依赖尚未设计的能力、40 个由某个测试覆盖
  但那个测试没有引用编号、1 个是重复登记）。**"没有被点名"不等于"没有被测"**，台账记录的是
  **证据指针**而不是覆盖判定；`S-010`/`S-015` 是同一个场景登记了两次，
  `S-014` 在两份文档里各定义一次（两处必须同时更新），`P-021` 的"另一个卷→拒绝"一句**已取代**。
  53 个被测试点名。**§61 把 17 条从未复核过的"缺某能力"判断逐条重新推导了一遍**，其中七条改判
  （P-002 / P-016 / T-013 不该记 P2/P4，另外 §60 的 `reconcile` 同属这一类），三条改成
  `undesigned`（P-012 / P-015 / P-017：它们各有一半已交付、另一半**根本没有实现对象**——
  `revoked_at` 无写入方、`events` 表当时没有 `approval_mode` 列、没有签发方身份证据），其余十条
  **维持原判**并补上"哪一半已交付"。复核还抓出一个真缺陷：同一 token 的第二次 `commit` 会把**已落盘的**
  transaction 覆盖回 `PROPOSED`，于是一条 transaction 的审计轨迹出现**两次 `ACTIVE_BOUND`**；
  现在 `journal.create` 是 get-or-create（重复即续做，与 `resume` 一致）。
  **§62 接着把 §61 只"记录下来"的那一族失败路径修掉了**：中断的下载、磁盘满、commit 时目标被占——
  `http.client.IncompleteRead` 与裸 `OSError` 现在都变成 `INSTALL_IO_FAILED`（退出码 2：计划没错，
  是环境拒绝了），会按失败点回滚并把 stage 清掉，`failure_cleanup` 这个"每个 backend 都声明、
  核心从不执行"的字段第一次真的被执行（`discard_stage` 在此之前没有任何调用方）。
  **§65 又把上面那句话里的一个分句修掉了**：`events` 表现在**有** `approval_mode` 列（迁移 v6），
  批准事件与事务的 `PROPOSED` 事件都记录它——三大核心契约 决策3 要求的"必须记录
  `approval_mode=policy`"因此从一句话变成可查的事实。P-012 仍记 `undesigned`，但缺的只剩**产生**
  策略批准的那一方（没有生产签发方）。
  **§63 把 C-028 从"缺一个命令形式"变成已交付**：`env forget --all` 现在存在（§13.4 一直要求它、
  `environment_persist` 的建表注释也一直写着它），`--all` 与"给一个 id"不能混用，全部还原只碰
  AIROOT 写过的变量。
  `unchecked-invariant` 这个词汇值在 §55–§57 之后**已经没有条目在用**，
  但留在词汇表里（这一类会复发，删掉它等于连"为什么需要第五个值"一起删掉）。
- 本报告与验证方案里写作 `AIROOT\cli\broker\...` 的路径已更正为实际位置 `docs\broker\`。

**测试就绪判定的变化**：L0（协议、canonical path、状态转移、退出码）与 L1（隔离文件系统、
SQLite WAL、generation CAS、故障注入、恢复）**已落地并通过**；L2（Windows ACL、machine
PATH、UAC、PowerShell session）与 L3（系统级崩溃恢复夹具）**仍缺**，属 P2 范围。

**口径**：现在可以说"协议级 Core 已通过 P1 验收，fake vertical slice 的状态机已被受控
Core 取代"；仍**不可**说"AIROOT 已实现"或"已具备 Everything 级性能"。`search` 尤其**不能**被说成
Everything 级性能：它的索引由一次目录遍历建立，`freshness.current` 只表示"这份清单是最近一次
遍历建立的"，与 USN journal 的"游标没断档"不是一回事。

