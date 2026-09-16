# AGENTS.md — AIROOT

面向在本仓库工作的 AI Agent / 协作者。第一次进入请按顺序读完本文件，再读 `docs/AIROOT-总体方案规划-v0.3.md` 的 §1.1（规范层级）。

## 1. 这是什么项目

**AIROOT 是面向 AI Agent 的本机能力控制平面（Local Capability Control Plane）。**

它要解决的唯一核心问题是：

> 如何让 AI Agent 在长期使用的 Windows 电脑上，以确定、可审计、可诊断、可恢复的方式获得本机能力。

它**不是**：通用包管理器、Everything 安装器、外部软件维护平台、容器/沙箱、企业分发系统，也不是能对抗管理员权限的安全产品。

形态固定为：

```text
AIROOT
  = Skill 适配层（AIROOT\SKILL.md）
  + 稳定 CLI/API（AIROOT\cli）
  + Windows 权限边界（ACL + elevated broker）
  + 状态注册表（SQLite registry/event）
  + 事务/恢复引擎（journal + generation）
  + 能力扩展运行时（Capability Extension）
```

### 当前阶段：P1「最小 Core」已实现，P2 未开始

**已实现（协议级，Python 3.11）**：**P1 最小 Core**（root 解析与卷身份守卫、SQLite registry 与迁移、只读 JSON 投影、`where` 确定性选择、`doctor` D1–D10、模拟事务与 journal 驱动恢复、CLI 与 golden 语料）；**管家域的全部步骤 1–9、11–12**（数据根注册与只读 PE 静态探测、能力白名单发现、`adopt --mode reference` 非拥有式登记、依赖分流与 `scope` 决策、会话激活与 **user 级环境变量持久化**（plan → approval → 写入 → 精确还原）、**steward-first `where`** 与数据根/reference 重观测诊断、**删除分级**（只删 AIROOT 自己装的，reference 永不 `uninstall`）、**能力边界**）；**Skill 适配层**（`SKILL.md` + `agents/airoot.json` + `references/`，带漂移守卫）；**可信来源清单与 Install Backend**；**`rebuild`**（派生状态可重建、权威不可自重建）；以及 **`search` 的协议面与 crawl 建的持久索引**（**它不是 USN 索引**：`freshness.current` 只表示"这份清单是最近一次遍历建立的"，与 journal 的"游标没断档"不是一回事）。**872 项测试通过**（含 101 项常驻跨工件一致性检查 `cli/tests/test_l0_consistency.py`）。

**逐阶段的实现记录**（`§31`–`§106`：每一阶段做了什么、发现什么缺陷、加了哪组守卫、怎么验红、哪些边界没有做）**全部在 `docs/AIROOT-v0.3-管家模型与数据根契约草案.md` 的对应小节里**；本文只保留当前状态、仓库地图与操作面。这是**有意**的：本文是**入口文档**，必须能被读者**完整**载入，而逐阶段细节曾让它长到超过读取预算并被**静默截断**（见 §54）——把细节留在这里，等于让读者拿到半份规则；转写到草案里则**不丢任何信息**（§1 里原来的 23 段在草案里各有一节，实测零独有，见 §54.2）。

**尚未实现**：ACL、UAC、elevated broker、named-pipe、machine PATH 写入、**machine 级环境变量持久化**（`--scope machine` 现在报 `PRIVILEGE_REQUIRED`）、`exposure\bin` launcher、**签名/来源证明**（TUF / Sigstore 级；来源清单与摘要校验已就绪，v1 只做 digest）、**真实工具的安装**（**下载与摘要验证已经对真实上游跑过**——§59：从 `static.rust-lang.org` 取到 **12 721 664 字节**的 `rustup-init.exe`，SHA256 与上游发布的校验和一致；**`stage`/`commit` 没跑过**，因为 P1 没有生产批准签发方，唯一的签发方是 `cli/tests/fake_issuer.py`。所以这里不再写"机制已通"这种关于代码的话，而是写**哪几步跑过、哪几步没跑**）、**`file_search` 的 Native Index**（USN journal / NTFS 元数据 / 常驻索引器；协议面 §31 与 crawl 建的持久索引 §32 已可用）、Everything adapter、内容搜索、跨用户 ACL 过滤、`reconcile`、`path backup\|restore`、`root adopt\|relocate`（已按命令路径登记在 `agents/airoot.json`）、真实 Ed25519 签名、`.ai/tooling.json` 的写入。管家域 §3–§15 的**全部步骤 1–9、11–12 均已落地**；只剩依赖 P2 的 machine 级环境变量（步骤 10），以及 **ACL 的写一侧**——`doctor` 侧的**观测与漂移发射已落地**（§58 / ADR-0023：`caps/acl.py` 只读 DACL，无需提权），仍缺的是把基线**强加**回目录（`WRITE_DAC` + broker）。

**禁止把本项目描述成“已实现 AIROOT”或“已具备 Everything 级性能”。** 允许的说法见 §8。

**正在提案中的方向变更**：ADR-0004 把定位从“仓库主人”改为**受聘管家**（reference-first：默认不拥有用户已有环境，删掉本体不得带走环境）。契约草案第二版见 `docs/AIROOT-v0.3-管家模型与数据根契约草案.md`（§12–§15 为新增：依赖分流与确认、环境变量持久化、删除语义分级、能力边界）。**管家域的步骤 1–9、11–12 全部落地**：steward-first `where`（ADR-0006）、数据根/reference 诊断、删除分级与能力边界（ADR-0007）。提案里**只剩**依赖 P2 的 machine 级环境变量（步骤 10）与 ACL **写入**未落地。

提案里三条硬规则**现已全部落地并成为实现行为**：

1. **环境变量持久化的值禁止指向 `AIROOT\cli\exposure\bin` 的 shim**，必须指向数据根内真实路径（§13.2）；owned 对象**禁止持久化**。指向 shim 会在删掉本体后留下指向空目录的垃圾。
2. **reference 永不提供 `uninstall`**，只有 `forget`；数据根内任何目录永不被删除（§14.2）。
3. **依赖分流判据复活**：被项目清单引用 → 项目内隔离不询问；单文件通用 CLI → 数据根不询问；**装包/建环境/超阈值才是必须确认的三类**（§12.1）。确认退化成噪音会让高风险确认一起失效。

## 2. 仓库地图

| 路径 | 内容 |
|---|---|
| `docs/AIROOT-总体方案规划-v0.3.md` | 主规划（2258 行）：对象模型、目录、CLI、Skill、路线图、验收 |
| `docs/AIROOT-v0.3-三大核心契约方案.md` | 权限与批准、五层状态、事务状态机与崩溃恢复 |
| `docs/AIROOT-搜索能力与工具集成协议方案.md` | `file_search` / Extension profile、Native Index、降级 |
| `docs/AIROOT-v0.3-验证与测试方案.md` | L0–L3 测试分层、必测场景（P-/S-/T-/C- 编号）、质量门槛 |
| `docs/AIROOT-v0.3-规范审查报告.md` | 审查结论、已解决 P0、**文末追加的当前状态节**（自称描述当前状态，故不得否认已交付的产物——守卫第十六组，见契约草案 §48；前文快照与计数是审查当时的事实，仍保留原样并标注"审查时的数字"） |
| `docs/AIROOT-v0.3-实现决策记录.md` | **ADR 日志（ADR-0001 … ADR-0032）**：语言路线（Python P1 → Rust P2）、三处 Schema 缺陷处置、**ADR-0004 管家模型**、ADR-0021 放宽优先、ADR-0025 的 D1–D9、**ADR-0026 的"有 5 个 schema 这一版没有写者"**、**ADR-0027 的"已声明缺席的动词报 `NOT_IMPLEMENTED`(1)，不报成拼写错误"**、**ADR-0028 的"失败文档也有契约，而计数守卫不得要求重写历史"**、**ADR-0029 的"折叠进成功文档的失败要带上自己的码"**、**ADR-0030 的"schema 枚举只能有一个走法，完整性用形状证明"**、**ADR-0031 的"同一个走法两个问题：文件自己声明的 vs 文档能携带的"**、**ADR-0032 的"`const` 也是词汇，但 `if` 里的不是"**。**括号里是范围，不是内容清单**——每一条 ADR 自己的标题才是它的权威，别在这里补一份手抄的目录（§85） |
| `docs/AIROOT-v0.3-管家模型与数据根契约草案.md` | **提案 + 逐阶段实现记录（§1–§30 是契约章节，§31–§106 是每一阶段的实现记录）**：reference-first 管家模型、数据根、能力白名单、`where` 优先级改写、**依赖分流与确认、环境变量持久化、删除语义分级、能力边界声明** |
| `docs/AIROOT-v0.3-诊断码与ReasonCode表.md` | **D1–D10 不变量定义 + reason code → 退出码权威映射** |
| `docs/broker/AIROOT-受保护Broker方案-v1.md` | Protected machine mode 信任边界、IPC、提交算法、审计 |
| `cli/schema/*.schema.json` | **20** 个 JSON Schema（draft 2020-12）= 第 1 层契约 |
| `cli/app/airoot/` | **P1 CLI Core**（Python 包）：`cli.py` 入口、`registry/`、`tx/`、`caps/`、`ext/` |
| `cli/app/airoot/caps/` | **能力域模块总览**（24 个）：`where.py` 选择与降级、`discovery.py` 白名单发现、`probe_pe.py` 只读 PE 静态探测、`version.py` 版本序与约束、`selection.py` 选择策略、`doctor.py` D1–D10 诊断、`layout.py` **payload 标记扫描的唯一定义**（D4 的三个形状；`doctor` 与 `rebuild` 共读同一个走法，§71）、`acl.py` **只读 ACL 观测**（`ctypes` 读 owner/DACL，失败即数据；支撑 `DATA_ROOT_ACL_DRIFT`，无提权、无新依赖，ADR-0023）、`inventory.py`/`effective.py` 声明与有效事实视图、`environment.py` 环境变量与模板、`exposure.py` reference 暴露计划、`planner.py` 计划与路由（`CONFIRMATION_OPTIONS`）、`lifecycle.py` 生命周期、`sources.py` 上游校验和解析、`boundary.py` 能力边界准入、`search.py` 搜索协议面（请求/上限/root/cursor/有界 crawl；syscall 走 `\\?\` 扩展长度形式，报告路径仍是普通形式，§31/§36）、`searchindex.py` **crawl 建的持久索引**（SQLite `cache/search/index.db`，整文件原子替换，无 WAL；**不是 USN 索引**，§32）、`usn.py` **只读 USN 能力探测**（默认惰性、失败即数据；USN 索引器本身属 P2，ADR-0020）、`desired.py`/`toolstate.py`/`pathexposure.py`/`session.py`/`rebuild.py`（各自见下行） |
| `SKILL.md` | **Skill 入口**（Skill 根 = 仓库根 `AIROOT`，**不能**放在 `cli/` 下）：行为边界、命令地图、三选一、退出码、绝不做的清单。命令地图与 `agents/airoot.json` 的 lane、以及 CLI 的动词**三方对账**（§79/§81）：教的必须有 lane，有 lane 的必须被教或写清为什么不教 |
| `agents/airoot.json` | Skill 的机器可读调用元数据：问题 → 命令 → 该读哪些字段 + **结构化的 `never`**（每条带稳定 `id`、`statement` 与锚点 `skill_item`/`stated_in`，与 `SKILL.md` 的《绝不做的清单》**双向对应**，见契约草案 §41）/`not_implemented`/`deferred`（每条带理由与解锁词）/`uncovered_verbs`（**没有 lane 的动词**及理由，§79）/`unmapped_verbs`（**有 lane 但命令地图不教**的动词及理由，§81） |
| `references/` | 按需参考：`reason-codes.md`（按退出码分组的速查 + **《这一版发不出来的码》**：注册了但没有任何写者的码，§75）、`confirmation.md`（确认与批准）、`field-values.md`（**字段取值表**：每个 schema 的每个枚举取值 + 这一版会不会真的写出它，带 † 标记与"谁写出"证据指针，§74；外加《schema 没有枚举的标签》——`evidence[].kind`、`where` 的 `selection_reason`、信封的 `operation`/`origin`/`size_source`/`version_source`/`outcome`，那一节的权威是**代码**而不是 schema，§77/§78） |
| `cli/bin/airoot.cmd` | 开发期 launcher（CRLF！只设进程内 `PYTHONPATH`，绝不写 PATH） |
| `cli/extensions/airoot-fake-extension.json` | 假扩展 manifest（通信协议演示，无副作用） |
| `cli/tests/` | pytest L0/L1 套件（872 项，含 101 项常驻跨工件一致性审计 `test_l0_consistency.py` 与 `test_l1_agent_read_fields.py`——后者用真实 root 逐个跑出 agent 面文档，验证 `agents/airoot.json` 的 **141 条 `read` 路径**全部存在）+ `fixtures/golden/`（Rust 版验收语料，**36 个 fixture**，含七份 `search_*_response.json`、`doctor_stale_search_index.json`、`error_not_implemented.json`，以及五个**契约目录**：`transaction_transitions.json`（合法移动表）、`invariant_catalogue.json`（D1–D10 分组）、`frozen_capabilities.json`（冻结能力清单）、`scenario_ledger.json`（**108 个场景编号**的完整台账）、`execution_bounds.json`（**决定拒绝的每个数值**：搜索上限三件套 / `MAX_ROOTS` / 爬取与索引边界 / 白名单扫描边界 / 三选一 / 优先级 / artifact 与 PE 检查字节上限 / 五个 policy revision）；见 §45–§50）+ `conftest.py`（注入 `cli/app` 到 `sys.path`、创建并清理 `cli/tests/.tmp/`、会话级"不污染宿主机"断言）+ `golden.py`（重新生成 golden 语料）+ `fake_issuer.py`（**唯一的**批准签发方，只许出现在测试路径）+ `schema_walk.py`（**schema 枚举遍历的唯一定义**：两个守卫共用同一个走法，§104）+ `scenario_ledger.py`、`execution_bounds.py`（这两个目录的解析器与处置表）+ `real_machine_acceptance.py`（真机验收脚本，非 pytest）+ `dump_tables.py`（把 reason code 与不变量两张冻结表打成 Markdown 的开发辅助，不参与断言） |
| `cli/app/airoot/policy/discovery-whitelist.json` | **能力白名单**：按 `capability_id` 的证据谓词（不是目录名）；缓存/GUI/服务排除名单（revision `wl-4`） |
| `cli/app/airoot/policy/sources.json` | **可信来源清单**（`src-1`）：允许的 host、每个能力的 artifact 与校验和 URL 模板。**不含 digest**——摘要只来自上游校验和文件 |
| `cli/app/airoot/policy/selection-policy.json` | **选择策略**（`sp-1`）：`where` 里 steward reference 与 owned payload 的机器级先后（`precedence`，默认 `steward`） |
| `cli/app/airoot/policy/search-policy.json` | **搜索策略**（`srch-4`）：`limit`/`max_duration_ms`/`max_staleness_ms` 的**实现上限**、默认值、crawl 边界（深度/记录数/不跟随 reparse）、索引块（`index.path=cache/search/index.db`、记录上限、`max_age_ms`=**doctor 判"索引过旧"的判据**、`native_candidate`=协议里那个 USN 实现的身份与前置条件）。上限是数据不是代码，超出即 `EXTENSION_INPUT_INVALID`(8)（ADR-0017/0019/0020） |
| `cli/extensions/airoot-native-search-extension.json` | **`file_search` 的扩展声明**：`implementation_id=airoot-native-search-crawl`、`implementation_kind=fallback`（这个 build 只有受控 crawl + 它建的索引，**没有 USN 索引**）；声明 `search`/`status`/`explain` 三个只读操作与 `refresh`（写 `cache\search` 的派生缓存） |
| `cli/app/airoot/caps/backends/` | **Install Backend**：`base.py` 九个冻结声明 + 九步协议；`portable_file`（本地无脚本 artifact）、`https_artifact`（HTTPS + 强制 SHA256） |
| `cli/app/airoot/caps/desired.py` | **desired 层**（`state/desired.json`；**它不是**已发布的 `desired-manifest` 那份文档——同名字段、少了 `source` 与 `policies.auto_approve`，两者的处置见 ADR-0026）：`pin`/`clear`/`evaluate`；只写意图，不写 registry、不改 binding |
| `cli/app/airoot/caps/toolstate.py` | **只读观察面**：`tool list/status/verify`（整树 digest + 入口点存在性，绝不执行 payload、绝不修复） |
| `cli/app/airoot/caps/pathexposure.py` | **PATH 不变量检查**：重复 AIROOT 条目 / store 版本目录 / 非授权目录 / launcher 缺失；永不写 PATH |
| `cli/app/airoot/caps/session.py` | **会话快照栈**（`state/sessions/<id>.json`）：嵌套激活、`deactivate` 恢复旧值、`SESSION_STATE_STALE`。不是凭据，不进 registry |
| `cli/app/airoot/caps/rebuild.py` | **派生状态重建**：只重写 `state/registry.json` 与 `logs/audit/events.json`，旧投影归档；数据库永不重建，陌生对象只报告 |
| `cli/app/airoot/policy/capabilities.json` | **冻结能力清单**（`cap-2`）：能力身份/kind/入口/副作用上限/scope。白名单加载期校验"条目必须在此清单内" |
| `cli/fake_vertical_slice/fake_vertical_slice.py` | 旧协议切片，**保留为协议 oracle，不要删** |
| `docs/schema/README.md` | schema 边界表、兼容规则、P1 期间的修正记录。**它自己的规则也被守着**（§80）：边界表 ⟷ schema 文件双向相等、每行都说了边界、每个 schema 都把版本钉死在 1、每个记录型对象都拒绝未知属性 |

## 3. 文档权威层级（改文档前必读）

冲突时按此优先级，**不要静默改高层文档来迁就低层实现**：

1. 已发布的 `cli/schema/*.schema.json` 与 SQLite migration —— 机器可执行的最终契约；
2. `docs/AIROOT-v0.3-三大核心契约方案.md` —— 权限、批准、状态事实、事务恢复；
3. `docs/AIROOT-总体方案规划-v0.3.md` —— 目录、对象模型、CLI、Skill、生命周期；
4. `docs/AIROOT-搜索能力与工具集成协议方案.md` —— `file_search` 与 Extension profile；
5. `docs/AIROOT-v0.3-验证与测试方案.md` —— 只定义验收证据，不改变运行时语义。

规范用语：`必须` = 实现与测试都要满足；`应该` = 默认行为，偏离必须记录；`可以` = 兼容性/实现选择。

**已发现的三处“文档与 Schema 冲突”，一律以 Schema 为准**（详见 ADR-0003）：`transaction` 的 `ROLLED_BACK`、成功码不能用 `OK`、Extension manifest 用 `operations` 而非 `operation_policies`。重复踩这些坑等于浪费一轮。**同类冲突后来又有三处**（ADR-0017/0018）：搜索协议 §6.1 示例 manifest 的 `operation_policies`（同一条规则）、其 `target_scope: declared_roots`（scope 枚举里没有这个值）、以及 §8 示例的 `airoot search --root D:\Projects`（`--root` 在本项目里是 AIROOT root，搜索 root 用 `--search-root`）。

**一处已于步骤 5–6 落地的契约变更**：`where` 的选择优先级（规划 §3.2:174、§9.10）已从 **managed-first** 改为 **steward-first**（ADR-0006）：健康的 external reference 是一等候选，机器级它与 owned payload 的先后由 `policy/selection-policy.json` 的 `precedence` 决定（默认 `steward`）；"owned 坏了 + reference 健康"是**正常降级** `CURRENT_SOURCE_DEGRADED`（退出码 2），`CONFLICT_MANAGED_BROKEN` 不再由 `where` 发射（码仍注册）。`doctor` 也已扩展到数据根与 reference（D1/D3、`--include-unmanaged`）。

## 4. 术语（严格区分，混用即算错误）

| 术语 | 含义 |
|---|---|
| `capability_id` | 对 Agent 暴露的稳定能力，如 `file_search`、`python` |
| `extension_id` | 提供能力协议的模块身份 |
| `implementation_id` | 某 capability 在某 binding key 下的具体实现身份 |
| `install_backend_id` | 获取/验证/stage/commit 受控实例的后端（**不是**能力入口） |
| `tool_id` / `instance_id` | 受控工具逻辑名 / 不可变的版本·平台·架构对象身份 |
| `managed_tool_instance` / `runtime_instance` | AIROOT 拥有生命周期的 payload / 受控运行时 |
| `external_reference` | 指向外部对象的只读引用，**不拥有生命周期** |

**`provider` 不得作为新协议字段。** 两组必须分清：能力扩展（定义“怎么调用”）vs 受控工具实例（定义“哪份 payload 由 AIROOT 维护”）；五层事实 `desired`/`declared`/`physical`/`effective`/`historical`。分区 **R** 受保护可执行、**W** 可写但不参与机器级发现、**P** 项目自治。恒定式：**Zone W 永不进入 machine PATH**。

P1 中 `capability_id` 与 `extension_id` 的实例：能力 `fake-echo` 由假扩展提供；模拟受控工具的能力是 `fake-tool`，其 `install_backend_id` 是 `fake_fixture`。

## 5. 已冻结契约（不得在编码中途改变）

1. v1 默认 **Protected machine mode**；未实现前必须标 `security_mode=policy_only` + `enforcement=same_user_can_bypass`（P1 当前状态）。
2. Skill 根是 `AIROOT`，入口是 `AIROOT\SKILL.md`（**不能**放在 `cli\` 下）；CLI 与运行数据根是 `AIROOT\cli`。
3. `store` 是唯一 payload 存储；`tools`/`env` 只是 binding/view；`cache\search` 是可重建索引。
4. SQLite registry/event 是 declared/historical 权威；JSON 投影与 `logs\audit` 是派生物。
5. 同一 binding key **只有一个 active implementation**（DB 层用 partial unique index 保证）。
6. 事务顺序：

   ```text
   PROPOSED -> APPROVED -> FETCHED -> VERIFIED -> STAGED -> COMMITTED
     -> REGISTERED(inactive) -> ACTIVE_BOUND -> EXPOSED -> VERIFIED_AGAIN -> FINALIZED
   ```

   **`ACTIVE_BOUND` 是唯一可以改变 active binding 的提交点**（与 generation 同一个 SQLite 事务）。回滚只切 binding，新 payload 标 `broken` 并保留为证据。
7. 外部发现**默认不迁移**；`rebuild`/`discover`/`doctor` 发现陌生对象只能报 `unmanaged`/`orphan`/`quarantine`，**永不删除**。
8. 机器 PATH 只允许一个 AIROOT 条目：`AIROOT\cli\exposure\bin`；版本目录绝不直接进 PATH。
9. 退出码 0–9 与 reason code 的映射见 `docs/AIROOT-v0.3-诊断码与ReasonCode表.md`（代码是 `cli/app/airoot/exits.py`）。**成功码是 `SUCCESS`，不是 `OK`。**
10. `where` / Extension envelope / `doctor` 的字段名与 `reason_code` 已冻结；`doctor` 的 `severity`/`code`/`evidence`/`impact`/`remediation` 稳定。
11. `plan_hash` 用 `jcs-rfc8785-compatible` 标签、计算时排除 `plan_hash` 本身（P1 用确定性排序 JSON 作测试替代，已在 schema README 记录）。

## 6. 环境与可运行命令

工具链（实测）：Python **3.11.11**（miniconda `envs\model`）、`jsonschema` **4.25.0**、`pytest` 9.0.2。**没有** requirements/锁文件；唯一第三方运行时依赖是 `jsonschema`。

```powershell
# 全部测试（L0 协议 + L1 registry/事务/where/doctor/扩展/管家域 + CLI + 端到端验收）
python -m pytest cli/tests -q

# 旧协议切片（回归基线，必须保持绿灯；schema_count 现在是 20）
python .\cli\fake_vertical_slice\fake_vertical_slice.py validate-schemas
python .\cli\fake_vertical_slice\fake_vertical_slice.py test

# 真实 CLI（需要先有一个 root；P1 不会自己创建系统位置的 root）
python -m airoot --root <root> doctor --json          # 或设 AIROOT_HOME
cmd /c .\cli\bin\airoot.cmd --root <root> root status --json

# 管家域：声明数据根 → 只读扫描 → 登记引用（全程不拥有、不删文件）
python -m airoot --root <root> data-root add D:\env --role runtime --json
python -m airoot --root <root> discover --json
python -m airoot --root <root> adopt D:\env\java --mode reference --json
python -m airoot --root <root> adopt D:\downloads\7z.exe --mode import --capability archive --version 24.09 --json
#   `--capability` 必须是**冻结能力清单**里的名字（`airoot capability list`）：不在清单里就返回
#   `CAPABILITY_NOT_DECLARED`(9)，与 `plan` 同一口径。没冻结过的名字（`jq` 之类）先走规划 §15.4 的
#   "提议 → 冻结 → 白名单"，不能用 import 抄近路
#   另外，**高风险类会被拒**（`SCOPE_CONFIRMATION_REQUIRED`(4)）：冻结 `kind=runtime`（python/node/java）
#   或体积超 300 MB 阈值。§12.1 说这几类"必须确认"，而 import 固定绑机器级、问不了"装哪儿"——
#   所以它拒绝，而不是把这道门变成装饰。计划里写明**实测体积**（`metadata.import.size_bytes`）
#   `import` 产出**计划**（还不复制）：approve 之后 `install` 才把 payload 复制进 store 并绑定
python -m airoot --root <root> inventory --class external_reference --json
python -m airoot --root <root> forget external/dr-env/java --json

# 步骤 8：依赖分流 + 计划（路由结论进 metadata.routing，未确认不落盘）
python -m airoot --root <root> scope decide java --json
python -m airoot --root <root> scope decide python --project <项目> --json
python -m airoot --root <root> scope memory --project <项目> --json
python -m airoot --root <root> plan archive --scope data-root --target data-root:dr-env --dry-run --json
python -m airoot --root <root> plan build --scope project --project <项目> --json
python -m airoot --root <root> plan build --scope data-root --target data-root:dr-env --project <项目> --json  # SCOPE_UPGRADE_REQUIRES_APPROVAL(4)

# 步骤 9：环境暴露（会话级只打印脚本；持久化必须带 approval token）
python -m airoot --root <root> env activate external/dr-env/java --shell powershell
python -m airoot --root <root> exec external/dr-env/java -- java -version
python -m airoot --root <root> exec --env external/dr-env/java -- java -version   # §15.1 的写法，同义
# `exec` 的 `--` 之后原样交给子进程；之前的选项属于 AIROOT（写在 id 之后的 `--json` 也认）
python -m airoot --root <root> env persist external/dr-env/java --dry-run --json
python -m airoot --root <root> env persist external/dr-env/java --token-file <token.json>
#   **注意**：这一条与 `tool gc --apply` / `install` / `uninstall` 的 `--token-file` 形式在这个
#   build 里**走不到底**——没有生产批准签发方，一律 `PROVENANCE_FAILED`(7)。消息里带这一句：
#   `no production approval issuer exists in this build (decided: ADR-0025 keeps it waiting for the P2 broker)`。
#   前两步（`plan --dry-run` / `plan`）是**可用**的；裁决见 §8 与 `docs/AIROOT-v0.3-实现决策记录.md`
#   的 ADR-0024（状态：**已裁决：A 维持现状**）与其裁决 **ADR-0025** 的 D1。
python -m airoot --root <root> env list --json
python -m airoot --root <root> env forget external/dr-env/java --dry-run --json   # 先看要还原什么
python -m airoot --root <root> env forget external/dr-env/java
python -m airoot --root <root> env forget --all --dry-run --json   # 管家离场：AIROOT 写过的**全部**还原
python -m airoot --root <root> env forget --all

# 步骤 5：steward-first 选择 + 数据根诊断
python -m airoot --root <root> where java --json                  # 直接命中 reference
python -m airoot --root <root> where java --version ">=99" --json  # VERSION_UNSATISFIED，不猜
python -m airoot --root <root> doctor --json                       # D1 覆盖每个数据根
python -m airoot --root <root> discover --record --json            # 记录 unmanaged 观测
python -m airoot --root <root> doctor --include-unmanaged --json   # 才看得到 UNMANAGED_OBJECT_PRESENT
python -m airoot --root <root> doctor --verify --json              # 追加 entrypoint 整文件 digest

# 重新生成 golden 语料（仅在有意变更输出后）
$env:PYTHONPATH='D:\AIRoot\cli\app'; python cli/tests/golden.py

# 步骤 9d：派生状态重建（doctor 的 remediation 就是它）
python -m airoot --root <root> rebuild --plan --json    # 只读预演
python -m airoot --root <root> rebuild --json           # 重写投影 + 归档旧快照；数据库不动

# 步骤 9c：可信来源与上游校验和（digest 来自上游，不是自己算的）
python -m airoot --root <root> source list --json
python -m airoot --root <root> source resolve build --version 3.31.6 --offline-checksum <SHA256SUMS> --source-out <resolved.json> --json
#   清单里只有**验证过的**条目（§72）：`archive` 因为没有可取的上游校验和文件而被移除，
#   于是它是"没有来源的能力"——`source resolve archive` 返回 `NOT_FOUND`(1)，只能 reference/import
python -m airoot --root <root> plan build --source-json <resolved.json> --json   # 构造真实 artifact 计划

# 步骤 9f：只读观察面 + PATH 不变量
python -m airoot --root <root> tool list --json
python -m airoot --root <root> tool status <instance-id> --json
python -m airoot --root <root> tool verify <instance-id> --json     # 整树 digest，不修复
python -m airoot --root <root> path verify --json                   # 只读，永不写 PATH

# 步骤 9g：desired 层（表达意图，不改变 declared）
python -m airoot --root <root> tool pin build --version 3.31.6 --offline-checksum <SHA256SUMS> --json
python -m airoot --root <root> tool pin java --version 25.0.2.0 --json   # 记下愿望；无来源则如实报 plan_blocked_by
python -m airoot --root <root> tool pin build --clear --json

# 步骤 11-12：删除分级（只删 AIROOT 自己装的）+ 能力边界
python -m airoot --root <root> capability list --json
python -m airoot --root <root> capability check D:\env\java --json
python -m airoot --root <root> tool retire <instance-id> --json
python -m airoot --root <root> tool gc --plan --json
python -m airoot --root <root> tool gc --apply --token-file <token.json>
python -m airoot --root <root> uninstall <instance-id> --dry-run --json
python -m airoot --root <root> uninstall <reference-id> --json   # OWNERSHIP_REQUIRED(7)，只输出路径

# 步骤 31-32：搜索协议面 + crawl 建的持久索引（**不是 USN 索引**）
python -m airoot --root <root> search refresh --json               # 建索引：records/coverage/替换了哪个派生文件
python -m airoot --root <root> search java --ext .exe --json       # 有索引 → 退出码 0 / status=ok / fallback=null
python -m airoot --root <root> search --search-root D:\env --json  # 显式搜索 root（--root 仍是 AIROOT root）
python -m airoot --root <root> search java --managed-only --json   # 只要 AIROOT 认识的路径（reference / owned）
python -m airoot --root <root> search --query status --json        # 搜一个叫 status 的文件（保留字只在首位生效）
python -m airoot --root <root> search status --json                # 索引状态 + freshness + 记录数
python -m airoot --root <root> search status --probe-native-index --json  # 只读探一次卷：为什么 native 索引不可用（§34）
python -m airoot --root <root> search explain java --json          # 会用索引还是 crawl 回答
python -m airoot --root <root> search implementations --json       # 声明 file_search 的实现清单
python -m airoot --root <root> search rebuild --json               # refresh 的协议拼写（同样是整次遍历）
python -m airoot --root <root> search java --max-staleness-ms 1 --json  # 索引太旧 → SEARCH_RESULT_STALE(2)

# 真机验收（步骤 5-6 + 8-9 + 31-32 全路径；对真实 D:\env 只读，临时 root 自清理，不写 HKCU）
python cli\tests\real_machine_acceptance.py
python cli\tests\real_machine_acceptance.py --online   # 外加 §59：对真实上游解析 + 下载 + 摘要校验（不跑 stage/commit）
```

`pytest` 由 `cli/tests/conftest.py` 注入 `cli/app` 到 `sys.path`；直接 `python -m airoot` 需要自行设置 `PYTHONPATH=cli/app`（或走 `cli\bin\airoot.cmd`）。

**测试根只在 `cli/tests/.tmp/` 下创建并自动清理**。测试必须不污染宿主机的 PATH / 注册表 / ACL / 真实 root —— `conftest.py` 有会话级守卫断言，破坏它测试会失败。

### 运行环境注意（会真实咬人）

- 受限文件沙箱（workspace-write）下，临时目录的 `scandir`/`chmod` 会被拒绝，测试与切片会报 `[WinError 5] 拒绝访问`；需要 `danger-full-access` 或非沙箱终端。
- 受限沙箱下不要用管道捕获外部程序输出（如 `python ... | Select-Object`），会报 `Program 'python.exe' failed to run: Access is denied`。
- **`cli/bin/airoot.cmd` 必须是 CRLF 行尾**，否则 `cmd.exe` 会把 `rem` 行切碎成命令。用文件工具重写它之后要确认行尾。
- **不要用 `Get-Content` 校验 UTF-8 中文文件**：本环境的 pwsh 把它按 GBK 解码，会输出 `锛氱瀹舵ā鍨` 这类乱码并**少算行数**（实测同一文件 `ReadAllLines=630` 而 `Get-Content=465`）。要核对内容用 `read`/`grep` 工具，或用 `[System.IO.File]::ReadAllText(...)` / `ReadAllLines(...)`。
- **标准输出也是 GBK**：`print()` 非 GBK 字符（如 `®`）会抛 `UnicodeEncodeError`。CLI 的 JSON 用 `ensure_ascii` 默认值所以安全；自己写调试脚本时用 `.encode('ascii','replace').decode()` 包一层。
- **`cli/tests/.tmp/` 会被 pytest 的会话夹具整个删掉**：不要把任何需要跨运行保留的 root/数据根放进去（放进去的示例 root 会在下次跑测试时消失）。它也是唯一允许建测试 root 的地方。

## 7. 改动约定

- **改 Schema**：跑 `validate-schemas`（20 个）+ `pytest`，并同步 `docs/schema/README.md` 的边界表与兼容规则。`schema_version: 1` 是唯一主版本；新增**可选**属性是 minor 兼容；改类型/枚举/必填/digest 算法/状态含义需新 schema id（ADR-0003 记录了唯一的例外及其理由；`reference-plan` 是新增边界而非放宽枚举）。
- **改对外 JSON 输出**：必须重新生成 golden 语料（`cli/tests/golden.py`）并在同一个变更里提交，否则 `test_golden_fixtures_reproduce_exactly` 会失败——这是 Rust 迁移的验收面。
- **自校验**：核心在打印任何对外 JSON 之前调用 `schema_io.validate_self`；自校验失败是**实现缺陷**（`SELF_VALIDATION_FAILED`，退出码 8），永远不要把它降级成领域性回滚。`simulate.PROPAGATING_CODES` 就是这条规则。**唯一的例外是失败文档本身**（ADR-0028，§102）：它由 `error-response.schema.json` 钉住，但校验失败时**只往 `evidence` 追加一行缺陷说明**、保留原来的 `reason_code`——把失败换成"实现者的错"会把它要问的那件事藏起来。**精确说法（§94 量过）**：自校验的是**核心构造的那份文档**——打印出来的信封**可能不等于它**：`env persist` 与 `adopt --mode import` 打印的是自校验过的 `plan`/`reference-plan` **加上** `plan_file`/`required_action`/`reason_code` 三个报告键（那三个刻意不属于计划契约，可批准物是文件、这份文档携带的是它的 hash）。**34 条 agent lane 里只有 5 条读的文档被已发布 schema 描述**，其余读的是 CLI 自己的报告面——每条 lane 的 `document_schema`（`agents/airoot.json`）就是这件事的可查记录，由测试逐条实测。
- **改状态机**：`cli/app/airoot/tx/states.py` 的迁移表是 §14.1 的转录；改它必须同步跨文档测试 `test_every_documented_state_is_representable_in_the_schema`。
- `test_hmac_sha256` **只允许出现在测试/模拟路径**；核心只做**校验**，签名实现放在 `cli/tests/fake_issuer.py`，`airoot approve` 永远不能凭空制造批准。
- 平台范围：v1 只做 Windows provider；macOS/Linux 保留抽象接口。
- 语言风格：文档中文为主、英文术语保留原文；代码标识符与注释英文。新增文档沿用 `docs/AIROOT-*.md` 命名。
- 变更设计语义时，**同时**改契约层文档、Schema 与（如适用）ADR；不要只改一处。
- **取舍默认"放宽"**（**ADR-0021**）：一个问题同时存在"放宽"和"收紧"两种读法或选项时，**默认取放宽的那个**，不必每次再问。它**不**适用于四种情况——图层级（不得违反已发布 Schema / layer-2 冻结契约）、诚实规则（§8 的禁令、"不编造确定性"、"失败即数据"）、管家模型的结构性不变量（数据根内文件永不删除、reference 永不 `uninstall`）、审计守卫（把一致性检查加严不是"收紧权限"）。**当放宽会摧毁一条更强的规则时，不机械照做，按这四种情况处理并如实报出来**：§4.1 的边集就是按放宽落地的（29 → **62** 条边），但 `FINALIZED` 保持死胡同、`EXPIRED` 不从已改过 active binding 的阶段进入。

## 8. 已知缺口与下一步

**已延后的命令路径带理由**（`agents/airoot.json` 的 `deferred`，守卫第二十三组）：`not_implemented`
里的 6 条各自写着**为什么**与**什么才能解锁它**。理由分两类，因为这两类要等的东西不一样：

- `needs-admin`——要提权或受保护 broker（`bootstrap`、`path backup`、`path restore`、`root relocate`、
  `root adopt`：它的 copy/verify/switch 规则**已由 ADR-0025 定下**，剩下的是切换本身——那会动 active
  root 指针与 machine PATH 条目，是受保护状态）；
- `needs-capability`——等后面阶段的能力（`reconcile`：规划把它定成 `reconcile <manifest>` 并排在 P6
  的 `project manifest` 之后，CLI 自己也这么说——`forget` 的输出里有
  `project_manifest_check: "not_implemented_before_p6"`）。

**这两类是被守卫要求"恰好等于"实际用法**的：一个没人用的类别就是一个读者会遇到却查不到用处的词。
两个**曾经存在、现在没有成员**的值都被删掉了，理由都是"没有人落在里面"：

- `covered-elsewhere`（"这个动词今天会是同义词"）**在同一轮里被加进又被删掉**：它唯一的成员
  `reconcile` 被 §60 的审计改判成 `needs-capability`（详见括号里的证据）。它的内容与 `doctor`
  （重观测漂移）/ `desired`（desired vs declared）/ `plan`（收敛计划）确实重叠，但那是这个命令**较小**
  的一半——它记在登记表的 `why` 里，而不是拿来当类别；
- `needs-decision` 在 **§82** 被删掉：它唯一的成员 `root adopt` 等的东西已经由 **ADR-0025** 裁决
  （源目录不动、先验后切、只有一个原子切换点、adopt 自己不写机器 PATH），剩下的阻塞是受保护状态 ⇒
  它改判成 `needs-admin`。**裁决把一个"等人"的类别变成了"等 P2"的类别**，类别本身随之消失。

解锁词只有两个：`p2-protected-state`、`p6-project-manifest`。带阶段前缀的写法与场景台账
**必须完全一致**（守卫不比较"像不像"，只比较同一阶段在两边是否同一个字符串）；第三个词 `decision`
随 `needs-decision` 一起消失——`approve`/`install` 的 lane 现在也等 `p2-protected-state`。

**调用这六条不会得到一句拼写错误**（ADR-0027，§101）：CLI 在**动词位置**上拦下它们，报
`NOT_IMPLEMENTED`（退出码 1）——不是 `INVALID_INPUT`(8)（那说"你的输入错了"），也不是
`PRIVILEGE_REQUIRED`(5)（那说"提权再试"，而提权变不出这个命令）。`details` 里带
`deferred_category` 与 `unblocked_by`，与上面的登记表**逐动词双向钉死**（守卫第三十五组）。

P1 刻意**没有**发明的东西（**ADR-0025 已逐条裁决：全部维持现状**，理由见该条）：

- `machine_id` / `session_id` / `project_id` 的生成算法——P1 只接受注入或显式入参（**不从硬件指纹推导**）；
- 根定位的卷标扫描（P1 只有 `--root` / `AIROOT_HOME`，失败即关闭；**不猜"最像的那个卷"**）；
- registry migration 工具、event 保留期、`logs\audit` 导出协议（只有骨架；**不自动迁移、不自动裁剪**）；
- `exposure\bin` launcher——P1 的 `EXPOSED` 是“用一次全新的 registry 读取观察到新 binding”，**没有写任何 launcher 文件**；
- 真实 `ed25519` 校验与受保护 issuer（P1 遇到 `ed25519` token 会显式报 `PROVENANCE_FAILED`）——**ADR-0025 已裁决取 A：维持现状，等 P2 的受保护 broker**（否决了"本地人类通道"与"现在就做真实签名"两条路，理由见该条）。它挡住的不只是签名算法，而是 `approve`/`install`/`env persist`/`tool gc --apply`/`uninstall` 五条路径在真机上的完成；拒绝消息里带着指向该条的指针；
- `file_search` 的 **Native Index 进程模型**（USN journal 消费、常驻索引器、`cache\search` 事务）——协议面与受控 crawl 已交付（§31），**形状已由 ADR-0025 定下**（broker 做初始枚举、索引永远是派生缓存、断档即拒答、非 NTFS 回落 crawl），常驻进程仍等 P2。
- `.ai/tooling.json` 的**写入**通道——**维持只读**直到 P2 的人类通道：它喂的 `memory` 规则会**跳过确认**，写它等于让 Agent 自己积累授权。


路线图（规划 §19）：**P0 冻结契约 → P1 最小 Core（已完成）→ P2 Windows Protected State** → P3 `file_search` → P4 Portable Transaction → P5 Runtime → P6 Session/Project → P7 加固 → P8 其他平台。

P2 的第一件事就是 **ADR-0001 的语言切换**：用 AIROOT 自己的 plan/approval/fetch/verify/digest 规范获取 Rust 工具链（推荐 `rustup-init.exe` + HTTPS + `SHA256SUMS` + `--no-modify-path`，免管理员），然后以 `cli/tests/fixtures/golden/` 为验收面逐字节对齐。**MSVC Build Tools 路线不符合 v1 无脚本 portable 基线**，不要默默采用。

**下一步的排序问题**：管家域步骤 1–9、11–12 与 **P3 的协议面 + crawl 建的持久索引（§31–§32）**
已全部交付且不需要管理员权限；剩下的都需要 P2 的受保护状态（ACL / broker / machine PATH /
machine 级环境变量）、P4 的真实安装后端（真实 artifact 下载 + `gc` 作用于真实 payload），
或 P3 本体的**USN 常驻索引器**（journal 消费 + 后台进程模型——它需要 broker 做初始的全量枚举，
因为那一步要提权；§34 已经把"这台机器能不能"变成可探测的事实，见 ADR-0020）。

### 完成判定的口径

允许说：“方案契约与验证计划已具备，P1 协议级 Core 已通过验收；管家域的**非拥有式登记**（数据根 → 只读发现 → reference，含多版本与活跃版本观测）、**依赖分流决策**与 **user 级环境变量持久化**（plan → approval → 写入 → 精确还原）、**steward-first `where`** 与**数据根/reference 诊断**、**删除分级**（只删 AIROOT 自己装的，reference 永不 `uninstall`）、**能力边界**、**Skill 适配层**（`SKILL.md` 是 Skill 根的唯一入口，带漂移守卫）与**可信来源清单**（digest 只来自上游校验和文件）、**`rebuild`**（派生状态可重建、权威不可自重建）、**session 激活的完整语义**（快照栈 + 恢复式 `deactivate` + `SESSION_STATE_STALE`），以及 **`search` 的协议面与 crawl 建的持久索引**（请求/响应按已发布 Schema；实现上限、cursor 绑定索引 generation、root 规则齐备；索引健康时退出码 0 并给出 `freshness=current`，覆盖不足或损坏时如实回落 crawl）已可对真实 `D:\env` 运行（872 项测试、旧切片无回归、`cli/tests/real_machine_acceptance.py` 全项通过，真机上 `search refresh` 建了 51 073 条记录，`search status --probe-native-index` 报出真实卷读数）。生产实现（受保护 broker/ACL、USN 常驻索引器）与依赖它们的 machine 级持久化待后续。”

不允许说：“AIROOT 已实现 / 已可用 / 已具备 Everything 级性能。”`search` 尤其**不能**被说成 Everything 级性能：它的索引由一次目录遍历建立，`freshness.current` 只表示"这份清单是最近一次遍历建立的"，与 USN journal 的"游标没断档"不是一回事。

任何测试都不得污染开发机的 PATH、注册表、ACL 或真实 AIROOT root。

## 9. 维护远端仓库

远端 **`https://github.com/Joyakuku/AIRoot.git`**（**public**），默认分支 `main`；远端仓库根就是 Skill 根
（即本仓库根）。`gh` 已登录（账号 `Joyakuku`）；在新机器上先跑 `gh auth setup-git` 配置 https 的
credential helper。

```powershell
git add -A
git commit -m "..."      # 提交信息用祈使句说明"为什么"，不要写 "update files"
git push
```

- **`.gitattributes` 是 `* -text`，不要改成 `text=auto`。** golden 语料是**逐字节**验收面
  （`test_golden_fixtures_reproduce_exactly`），而 `cli/bin/airoot.cmd` 必须是 CRLF；树里本来就混着
  CRLF 与 LF，任何 EOL 自动转换都会让新克隆与工作树不一致，从而让"语料逐字节可复现"变成一句假话。
- **提交前跑 `python -m pytest cli/tests -q`。** 改了对外 JSON、语料、或文档里的计数时，**在同一个提交里**
  重生语料并同步所有计数——守卫会红，那是设计好的行为，不要靠改守卫绕过。
- 不提交 `cli/tests/.tmp/`、`__pycache__/`、`.pytest_cache/`（见 `.gitignore`）。
- 远端是 **public**：不要把本机凭据、卷序列号、USN journal ID、真实用户名等指纹写进任何被提交的文件。
  当前树里没有这些（已实测扫描过），以后新增"实测事实"时留意这一点。
