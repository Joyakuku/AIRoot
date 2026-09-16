# AIROOT v0.3：实现决策记录（ADR）

本文件记录实现阶段做出的、会影响契约或长期路线的决策。规范层级见
`AIROOT-总体方案规划-v0.3.md` §1.1：**已发布的 JSON Schema 是第 1 层**，本文件的
决策不得与之冲突；一旦冲突，以 Schema 为准并修正本文件。

| ADR | 主题 | 状态 |
|---|---|---|
| 0001 | P1 用 Python、P2 切换到 Rust | 采纳 |
| 0002 | P1 明确标记 `policy_only`，并为此新增可选 Schema 字段 | 采纳 |
| 0003 | 三处"文档与已发布 Schema 不一致"的处置 | 采纳 |
| 0004 | **reference-first 管家模型**：数据根 + 能力白名单，`store` 降级为可选自置域 | 采纳（规范层落地待评审，见契约草案） |

---

## ADR-0001：P1 使用 Python，P2 迁移到 Rust

**背景**：文档从不指定实现语言。P1 的退出条件是"不接真实外部软件也能跑通状态和协议"，
本质是协议级 Core，而不是产品二进制。

**决策**：

1. **P1 用 Python 3.11 实现**（`cli/app/airoot`），唯一第三方运行时依赖是 `jsonschema`；
   pytest 仅作开发期依赖。
2. **P2 的目标语言是 Rust**，届时替换整条 CLI 链。
3. Rust 工具链**通过 AIROOT 自己的 plan/approval/fetch/verify/digest 规范获取**。

**为什么 P2 才切**：

- P1 是契约塑形期，改一轮 Python 几秒、Rust 一次全量编译几十秒起——契约定型前，
  Rust 的类型收益还没兑现，摩擦先到；
- 真正需要"受保护原生二进制"的是 P2（ACL、machine PATH、elevated broker、签名应用清单）；
- 18 个 Schema 已经冻住了线格式，换语言**不动契约面**。

**为什么长期应当是 Rust**（三条项目特有的理由）：

1. **自举悖论**：AIROOT 的产品形态是受保护入口 + application manifest。若 CLI 自身
   依赖一个它不管理的解释器，"解析本机有什么运行时"的能力自己就需要运行时；
   `bin/airoot.cmd` 就是这个问题当前的具体形态（见下）。
2. **核心不变量正是类型系统擅长的**：只有 `ACTIVE_BOUND` 能改 active binding、
   `installed ≠ verified ≠ active`、payload 与 binding 分离——Rust 可以把非法状态
   变成编译错误，Python 只能靠运行时断言与评审。
3. **P3 的 Native Search 是性能工程**：NTFS MFT、USN Journal、常驻倒排索引。

**Rust 工具链的获取路径（P2 执行，尚未执行）**：

| 方案 | 判定 |
|---|---|
| (a) **推荐**：`rustup-init.exe`，HTTPS + `SHA256SUMS` + `--no-modify-path`，装到用户目录（免管理员），plan/approval/digest 全程留档 | 符合 v1 无脚本 portable 基线 |
| (b) 等 P4 完整 portable backend 落地后再装 | 最纯正的 dogfooding，但 P2 broker 只能先标 `policy_only` |
| (c) MSVC Build Tools | **不符合 v1 基线**：GUI 安装器、多 GB、管理员、注册表/服务副作用、不可回滚。应作独立设计或进 `excluded` |

本机实测（决策当时）：`cargo`/`rustc`/`rustup` 全缺，且没有任何 C/C++ 编译器
（`cl`/`link`/`gcc`/`clang` 全缺）；Windows SDK 只有头文件与库；`dotnet` 只有运行时没有
SDK。因此 Rust 路线**第一步就是一次管理员级安装**，这恰恰是 AIROOT 自己规定必须走
plan/approval 的动作类别。

**排序澄清**：AIROOT 的受控安装路径（`plan → approve → fetch → verify → stage →
commit → expose`）到 **P4** 才交付。因此"用 AIROOT 自己下载 Rust"的现实落点是
**P2 的 bootstrap 窗口**（文档规定的一次性人工提权窗口），而不是 P1/P2 早期的自动事务。

**为使迁移便宜而在 P1 刻意做的三件事**：

1. `cli/tests/fixtures/golden/` —— 语言无关的 golden 语料（含每个命令的期望退出码），
   Rust 版应逐字节复现；用 `python cli/tests/golden.py` 重新生成；
2. 所有对外 JSON 在**打印前**做 Schema 自校验（`schema_io.validate_self`），
   线格式不可能悄悄漂移；
3. 现有 `fake_vertical_slice.py` 保留为**协议 oracle**，不随 P1 删除。

**遗留弱点（有意保留，作为 Rust 迁移的动因证据）**：`cli/bin/airoot.cmd` 依赖
`AIROOT_PYTHON` 或 PATH 上的 `python`。它只为本进程设置 `PYTHONPATH`，绝不写 PATH——
但它确实是"AIROOT 依赖一个它不管理的运行时"的活样本。

---

## ADR-0002：P1 必须标记 `policy_only`

**背景**：P1 没有 ACL、没有 broker、没有 machine PATH。文档要求
User compatibility mode 的响应必须携带 `security_mode=policy_only` 与
`same_user_can_bypass`（验证方案 P-013），但 `where-response`、`doctor-response`、
`extension-envelope` 都是 `additionalProperties: false`，没有承载该字段的位置。

**决策**：按 `docs/schema/README.md` 规则 2"**新增可选属性是 minor 兼容**"：

- 在 `common.schema.json` 增加 `$defs.securityMode`（`protected_machine|policy_only`）
  与 `$defs.enforcement`（`acl_enforced|same_user_can_bypass`）；
- 由 `doctor-response`、`where-response`、`extension-envelope` **可选**引用；
- P1 恒为 `policy_only` / `same_user_can_bypass`，并由测试 P-013 钉住。

**不新增 Schema 文件**：`schema_count` 保持 18。

**后果**：任何 P1 输出都不会被误读为"操作系统级强制"。P2 实现 ACL + broker 后，
同一个字段切换为 `protected_machine`，契约不变。

---

## ADR-0003：三处文档与 Schema 不一致的处置

规范层级规定已发布 Schema 是第 1 层。实现时发现三处冲突，全部**以 Schema 为准**并记录：

### 1. `transaction.schema.json` 无法表达 `ROLLED_BACK`（缺陷修正）

文档 §14.1 的合法状态含 `ROLLED_BACK`（`ROLLBACK_PENDING → ROLLED_BACK`、
`FAILED → ROLLED_BACK`、`ROLLED_BACK → FINALIZED`），但 Schema 的 `state` 枚举漏了它——
即**合法状态无法被表达**。

处置：**原地补齐**，理由是这属于"补全一处转录遗漏"而非语义变更：不改变任何既有枚举
成员的含义、不使任何既有 fixture 失效、`schema_version` 仍为 1 且尚未发布。
配套测试 `test_every_documented_state_is_representable_in_the_schema` 断言
§14.1 的每个状态都能在 Schema 中表达。若评审认为必须走新 schema id，改动仅限本项。

### 2. 成功 reason code `OK` 无法通过任何 Schema 字段

文档 §15.6 把成功码写作 `OK`，但所有已发布 Schema 的 `reason_code` 都是
`^[A-Z][A-Z0-9_]{2,63}$`——**最短 3 字符，`OK` 只有 2 个字符**，永远无法写入
`where-response.reason_code`、`doctor` 的 `code` 或 envelope 的 `reason_code`。

处置：P1 的实际成功码为 **`SUCCESS`**（退出码 0），并在 `OK` 的名字下不再发射任何值；
`CURRENT_PROCESS_ENV_OLD` 与 `POLICY_ONLY_MODE` 同为退出码 0。映射见
`AIROOT-v0.3-诊断码与ReasonCode表.md`。

### 3. Extension manifest 的 operation 映射名与枚举

文档 §11.1 的示例用 `operation_policies`，且 `overwrite_policy` 出现了
`replace_derived_cache`；但 `extension-manifest.schema.json` 要求该映射名为
`operations`，且 `overwrite_policy` 只允许 `deny | replace_same_instance |
explicit_generation`——**照文档示例写的 manifest 会被 Schema 拒绝**。

处置：以 Schema 为准，`cli/extensions/airoot-fake-extension.json` 使用 `operations`；
新增负向测试证明文档的两种写法都会被拒绝。文档示例应在下一次修订时更新。

---

## ADR-0004：reference-first 管家模型

**背景**：现行 v0.3 契约把 `store` 定义为"唯一 payload 存储"（§8.1:530），并把
"managed tool = registry instance + **store payload** + tools binding/view + exposure
launcher"（§5.4:354）作为受控对象的标准形态。其隐含假设是"AIROOT 装的东西才归它管"。

但实际使用意图是相反的：**AIROOT 是受聘管家，不是仓库主人**。它管理用户已有的全部环境
（`D:\env`、`D:\tools`、`D:\env_apps`），而**解雇管家不能带走或删除环境**。在
`store` 优先的模型下，这个目标无法达成：payload 在 `store` 里，删掉本体就一起没了。

**决策**：

1. **默认不拥有**。AIROOT 拥有的是**关于环境的事实与入口**（路径、版本、架构、观测
   digest、健康、漂移、active 选择、能力入口、会话激活记录），**不拥有文件本身**。
2. **对象域重排**：

   | 域 | 内容 | 默认 | 删掉本体后 |
   |---|---|---|---|
   | **管家域**（external reference） | 观测 + active binding + 入口 | **主体** | 文件完好，只失去"指路的人" |
   | **自置域**（`store`） | AIROOT 自己下载并验证过的 payload | **可选，默认为空** | 会丢失（这是主动要求它安装的代价） |

3. **`store` 保留但降级**：概念不删除——P4 的 portable install 仍需要它；但它不再是受控
   对象的默认归宿，日常的 python / java / flutter / git / 7z 全部走 reference。
4. **数据根 + 能力白名单**：`D:\env`、`D:\tools` 这类顶层目录注册为**数据根**；
   根内对象**只有在匹配已知 capability 时才升级为 reference**，其余一律 `unmanaged`
   只报告、不进 inventory。理由见契约草案（`D:\env` 里混着 Huawei SDK / KeyStore /
   nssm，`D:\tools` 里混着 texstudio / WeMeet / Everything，不收窄会让 `doctor` 变成噪音源）。
5. **可测试不变量**（模型是否兑现的判据）：
   > 移走或删除 `D:\AIRoot` 后，数据根的文件树 digest 与 PATH 完全不变。

   用临时目录做成 L1 测试，不碰真机。

**与现行契约的冲突**（必须在规范层更新，不在实现里偷偷绕过）：

| 现行条文 | 冲突 | 处置方向 |
|---|---|---|
| §3.2:174 "健康的 managed binding **优先于** external reference" | 主次颠倒 | 改为按 policy + 健康 + 版本约束并列选择 |
| §9.10:887-896 `where` 顺序把 external 放在最后且仅在 fallback 时 | 管家域成了退路 | reference 升为一等候选 |
| §9.10:896 "managed 坏了 + external 健康 ⇒ `CONFLICT_MANAGED_BROKEN`" | 正常降级被当成冲突 | 在 reference-first 下改为正常降级（可保留显式 policy 才报冲突） |
| §8.1:530 / §5.4:354 "store 是唯一 payload 存储" | 与"管家不拥有"并列时语义冲突 | 改为"**自置域**的唯一 payload 存储，默认可空" |

**未改变的部分**（避免误伤）：同一 binding key 只有一个 active、`ACTIVE_BOUND` 是唯一
提交点、退出码 0–9 与 reason code 表、五层事实模型、`discover`/`doctor` 永不删除陌生对象、
`test_hmac_sha256` 仅限测试路径、`store_path` 的 `^store/` Schema pattern——**在
reference-first 下 external reference 用自己独立的 `path` 字段，owned payload 仍然只在
`store` 里，所以两个 instance Schema 的 `store_path` pattern 无需改动**。

**代价与风险**：

- 可证明性下降：reference 对象的"何时/从哪/什么版本/hash"取决于**只读探测**，无法像
  owned payload 那样有安装时的 digest 基线；漂移只能靠观测对比，且**不得自动修复**。
- 去重与共享变难：同一 runtime 被多个项目复用时不再有 AIROOT 自己的权威副本。
- 收窄规则是新的策略面：白名单本身需要版本化、可审计，并要有"未匹配对象"的降噪路径。

详细契约见 `AIROOT-v0.3-管家模型与数据根契约草案.md`。

---

## ADR-0005：reference 域变更的授权载体与 `env` 命令的身份选择

**背景**：ADR-0004 的步骤 8–9 要让"用某个已登记的环境"这件事变得可授权、可记账、可精确撤销。
这触发了四个必须在编码前定下来的问题。

**决策**：

1. **`reference-plan.schema.json` 作为授权载体**（第 19 个 schema）。`plan.schema.json` 的
   `target.kind` 只允许 `managed_tool|runtime`、`operations[].kind` 只允许
   `fetch|verify|stage|commit|expose|rollback|delete`，**无法表达"改一个 external reference 的
   暴露方式"**。按 `docs/schema/README.md` 规则 2（改枚举要新 schema id），新增独立 schema
   而不是放宽既有枚举。`plan_hash` 语义与 install plan 完全一致，approval token 无需区分两种 plan。
2. **`env` 命令的第一个参数是 `external_id`，不是 `capability_id`**。同一 capability 可能存在
   多个 reference（不同数据根、不同版本）；按 capability 选择等于替用户猜一个，正是管家模型要
   避免的行为。**按 capability 选择是 `where` 的职责**，`env` 只作用于一个已确定的引用。
3. **无 token 时不报错，而是落盘 plan 并以退出码 4 停下**。`env persist` 未给 `--token-file` 时
   把 canonical plan 写入 `state/plans/`，返回 `PERSISTENCE_REQUIRES_APPROVAL`（4）及
   `required_action`。理由：拒绝执行的同时必须**交出可被批准的产物**，否则人类无从批准；
   同时**不存在任何无 token 的写入路径**，包括"帮助"性质的 `--force`。
4. **`reference-plan.schema.json` 的 `operations[].target_scope` 用内联 `user|machine` 枚举**，
   不复用 `common.schema.json#/$defs/scope`。`$defs.scope` 描述的是 **binding** 作用域
   （`system/machine/session/project`），没有"每用户持久化值"的拼写；同一文档里的
   `exposure.scope` 已经用 `user|machine`，两处不一致会让同一步骤有两种说法。
5. **子进程的退出码不得泄漏进 AIROOT 的退出码**。`airoot exec` 把子进程状态放在
   `exit_status` 字段，自身只返回 0（成功）或 2（`CHILD_PROCESS_FAILED`，新增码）。
   理由：冻结的退出码空间是 0–9，语义是 AIROOT 的结果，不是被调用程序的结果。
6. **`forget` 是还原，不是删除**。每次写入前记录**完整旧值**（`old_value`/`old_kind`）；
   `env forget` 据此精确还原，PATH 用"现值 − 记录旧值"求出本次真正新增的条目后定点移除，
   别人后来追加的路径必须活着；与记录值不一致时报告 `drifted` 但**仍按记录还原**。
   任何情况下都不删除数据根内的文件。

**暴露并修掉的一个真实缺陷**：PATH 的 `value` 记录的是**合并后**的值。若直接拿它反合并，会把
用户原有的 `C:\Windows` 等条目一并删除。现在 `added_path_entries()` 用差值求出新增项，
`test_forget_is_surgical_for_path` 断言"后来别人追加的路径必须活着"。

**未改变的部分**：`plan_hash` 算法与标签、approval token 的绑定字段、事务状态机、
`ACTIVE_BOUND` 唯一提交点、退出码 0–9 的既有映射、`test_hmac_sha256` 仅限测试路径。

**测试不得写真实 HKCU**：`EnvironmentStore` 抽象出真实/内存两种实现；逻辑测试全部用内存实现；
真实实现只允许出现在一个集成测试里，它写 `HKCU\Software\AIROOT-Test-<random>` 并在 `finally`
删除且断言已删除。宿主机的 PATH 有会话级断言守卫（`conftest.py`）。

---

## ADR-0006：`where` 改为 steward-first，冲突语义改为正常降级

**背景**：ADR-0004 把定位从"仓库主人"改成"受聘管家"，但冻结契约仍是 managed-first：
规划 §3.2:174 写"健康的 managed binding 优先于 external reference"，§9.10:887-896 把 external 放在
第 4 位且仅作显式 fallback，并把"managed 坏了 + external 健康"定义为 `CONFLICT_MANAGED_BROKEN`。
在这套语义下，用户**自己已有的** java/node 永远排在 AIROOT 装的东西后面，而"已有环境更健康"反而
被报成冲突——与管家模型直接矛盾。用户已批准修改这三处冻结文本。

**决策**：

1. **候选序改写**（规划 §9.10）：project binding → session binding → **healthy external reference**
   → machine owned binding → unmanaged（仅诊断）→ broken/stale/drifted 永不作为 healthy。
2. **机器级两者的先后是 policy，不是常量**：`policy/selection-policy.json` 的
   `precedence: steward|owned`，默认 `steward`。理由：默认值必须是模型的前提（reference 优先），
   但"我确实把这个能力交给 AIROOT 管"是一个合法意图，必须有不改代码的出口；因此写成文件并在
   `where` 的 `evidence` 里报出所用 `revision`。**文件缺失或不可读 → 回落 steward**；
   **文件存在但非法 → `INVALID_INPUT`**（打错字必须响，不能被静默重新解释）。
3. **冲突语义改写**（规划 §9.10:896、验证方案 S-014）：owned 坏了 + reference 健康 →
   `found=true`、`usable=true`、`reason_code=CURRENT_SOURCE_DEGRADED`（退出码 2），
   `selection_reason` 同名，`candidates` 保留两者，并附 `degraded_from=<binding_key>` 证据。
   只有**没有任何可用候选**且存在损坏的 owned binding 时才 `BROKEN`（退出码 3）。
   `CONFLICT_MANAGED_BROKEN` 不再由 `where` 发射——码仍注册、语义仍保留，供将来 policy 显式要求
   冲突时使用，权威映射表保持向下兼容。
4. **`--allow-external-fallback` 降级为 deprecated 兼容别名**：reference 已不是 fallback，该开关
   不再改变结果，只在 `evidence` 记 `deprecated_flag_ignored`。直接删除会让已写好的 Skill/脚本
   静默改变行为；保留但无效是最诚实的处置。
5. **版本约束按"活跃版本"判定，不替用户切版本**：多版本对象（nvm）里存在满足约束但未激活的版本时，
   返回 `VERSION_UNSATISFIED` + `selection_reason=VERSION_AVAILABLE_BUT_INACTIVE` + 证据说明该版本
   存在但未激活。选中一个未激活的版本等于答非所问；切换活跃版本是用户的决定。
   **版本未知不等于满足约束**：`where --version` 遇到未探测过版本的 reference 视为不满足，并在证据里
   说明"没有版本证据"。这是"未知就是未知"原则在选择层的落地。

**为什么不改 Schema**：`where-response.schema.json` 的 `selection_reason` 是自由 string，
`reason_code` 是自由 pattern，`evidence` 是数组——新语义完全落在既有字段里。`schema_count` 保持 19，
Rust 迁移的验收面只体现在 golden fixture 的**内容与命名**上。

**代价与风险**：

- 行为变化是**语义级**的，调用方若依赖 `CONFLICT_MANAGED_BROKEN` 或 `found=false` 需要改；
  处置：两个 golden fixture **改名**（旧名描述的行为已不存在），退出码断言改写为 2。
- 策略面新增一个可被误删的文件；处置：缺失即回落默认，且 evidence 明说 `source=default`。

---

## ADR-0007：删除分级与能力边界的落地方式

**背景**：ADR-0004 的步骤 11–12 要把"管家能删什么"与"管家能管什么"从文档条文变成代码。
落地时遇到两个必须定下来的问题：删除之后 registry 怎么记，以及"能力清单"与"白名单"的职责边界。

**决策**：

1. **`plan.schema.json` 不用改**：它早已包含 `operation: retire_tool | gc_apply` 与
   `operations[].kind: delete`（含 `source_mutation: delete`）。步骤 11 因此完全落在既有契约内，
   `schema_count` 仍为 19。这是"先冻结契约"带来的直接好处——删除语义早就被预留了。
2. **删除后保留 instance 行，新增 `collected_at`**（registry v5 + projection 可选字段）。
   `bindings.instance_id` 有外键指向 `instances`；删行会连带破坏 binding 历史，而 `retired`
   的全部意义就是"保留用于回滚/审计/调查"。`lifecycle_status` 枚举**不动**，`doctor` 依据
   `collected_at` 区分**主动回收**（不是缺陷）与 **payload 消失**（`PAYLOAD_MISSING`，缺陷）。
3. **`retire` 是声明式变更，`gc` 才是事务性删除**。`retire` 不碰文件，只在**一个** SQLite 事务里
   清 binding、改状态、bump generation——这满足"唯一提交点"保护的实质（active binding 与
   generation 原子变更）。`gc` 会删文件，因此必须 `plan → approval → apply`，并在 apply 时
   **重新计算**准入判据：token 授权的是一个 plan hash，不是当时那张清单。
4. **中断可识别**：删除发生在 `GC_INTENT` 与 `GC_APPLIED` 之间，两种半途状态都能被识别并幂等收敛
   （`collected_at` 已写入时再次 apply 返回 `already_collected`）。
5. **`uninstall` 是 `retire` + `gc`，没有 `--force`**；对 reference 调用只报
   `OWNERSHIP_REQUIRED`(7) 并输出绝对路径与合法的 `forget` 命令——"不拥有就不代劳"。
6. **能力清单与白名单职责分离**：`policy/capabilities.json` 声明**能力是否存在**（身份、kind、
   入口、副作用上限、scope），`discovery-whitelist.json` 声明**如何识别**一个对象。
   两者都由 `caps/boundary.py` 校验，且**白名单加载期**就拒绝"引用了未冻结能力"的条目——
   否则识别规则会悄悄越过边界。需要不可回滚副作用的能力**不允许被冻结**。

**代价与风险**：`gc` 是 AIROOT 唯一会删文件的路径，因此它的准入判据被写在两处（plan 与 apply），
并且只能删 `store/<instance_id>` 这一个目录（`is_within` 二次校验）；测试断言"root 内被删的文件
全部属于该 instance 的 store 子目录，数据根零改动"。

---

## ADR-0008：Install Backend 抽象只实现"获取与提交"

**背景**：三大核心契约 §4.4 冻结了后端的九步操作与九个声明字段，但直到步骤 8c 之前，`install`
只能跑 `fake_fixture`。ADR-0001 的 Rust 工具链获取、P4 的真实安装、以及"`gc` 作用于真实 payload"
全部卡在这一层。

**决策**：

1. **只做 artifact 型后端**：`portable_file`（本地无脚本单文件）与 `https_artifact`（HTTPS +
   强制 SHA256）。v1 的安全基线是"无脚本 artifact"（§决策3），因此
   `assert_script_free()` 拒绝 `.bat/.cmd/.ps1/.sh/.py/.vbs/.js` 作为 payload，
   `executes_scripts=true` 的后端**连构造都过不去**、也不得进入核心事务。
2. **`source_mutation` 只能是 `none`**：`BackendDeclaration.__post_init__` 直接拒绝其他值。
   删除/移动用户的源文件必须由 plan 显式列出并单独批准（§4.4:352）。
3. **两个 digest 含义分开**（本阶段最重要的判断）：
   `plan.source.integrity.artifact_digest` = 源文件 sha256（`fetch`/`verify` 用）；
   `instance.artifact_digest` = `store/<instance>` 的树摘要（`doctor` 事后复查、
   `MANIFEST_DIGEST_MISMATCH` 的依据）。合并它们会让"安装后 payload 被改"对真实 artifact
   不可检测，而那正是 D3 的核心能力。
4. **HTTPS 只允许 https，且拒绝重定向降级**：明文或"重定向到 http"一律 `PROVENANCE_FAILED`。
   v1 不做签名验证，**但绝不说 digest 等于签名**（§4.4:368）。
5. **`fake_fixture` 不改造**：golden 语料与既有事务测试逐字节钉在它上面；frozen 接口是对
   artifact 后端的要求。`install` / `repair` 按 plan 的 `metadata.backend_id` 分发，
   **`repair` 也必须用同一个后端 resume**，否则会重新物化出与批准内容不同的字节。
6. **声明写进 `metadata.backend`**（自由对象），因此 `schema_count` 仍为 19。

**未改变**：事务状态机、`ACTIVE_BOUND` 唯一提交点、journal 语义、golden 输出、
`store/` 布局（`where`/`doctor`/`gc` 不需要知道是哪个后端产出的 payload）。

---

## ADR-0009：来源清单与"摘要来自上游校验和文件"

**背景**：ADR-0001 要求用 AIROOT 自己的规范获取 Rust 工具链（`rustup-init.exe` + HTTPS +
`SHA256SUMS`）。步骤 8c 的 `https_artifact` 会下载并校验，但它需要调用方**告诉它**一个 digest。
§4.4:368 早已划出边界——v1 可以只做 digest + allowlist，但**来源与 hash 不能混**。

**决策**：

1. **expected digest 的唯一来源是上游发布的校验和文件**：`caps/sources.py` 的
   `parse_sha256sums()` / `parse_single_digest()`；`resolve_source()` 没有校验和来源直接拒；
   `policy/sources.json` **不允许出现 digest**（有测试断言）。拒绝的正是"下载完自己 hash 一遍
   再和自己比"这种假验证——靠约定防不住，所以做成结构性的。
2. **provenance 与 integrity 分开**：`source.provenance.source_id/publisher` 来自清单，
   `source.integrity.artifact_digest` 来自校验和文件。两者机制不同；合并会掩盖"来源可信但内容被换"。
3. **host 允许列表是清单的核心**：清单外的 host 一律 `PROVENANCE_FAILED`。任意 https 主机只证明
   "通道加密了"，不证明发布者是谁。
4. **离线模式（`--offline-checksum`）只是取校验和文件的另一条路径，不是豁免**：仍然必须有校验和
   来源、仍然要解析、仍然要与 artifact 对照。它同时让整条链路可以脱离网络被端到端测试。
5. **名称不匹配 → `NOT_FOUND`，不猜最接近的一条。**
6. **v1 不做签名校验**，`signature: null`，并在 `source list`、`SKILL.md`、`agents/airoot.json`
   三处明说**"摘要不等于签名"**。把 digest 说成签名比不做签名更糟。
7. **校验和文件复用 `https_artifact` 的网络边界**（同一套 only-https + 拒绝降级重定向），
   不新写下载器：一套边界，一处审计。

**未改变**：`plan.schema.json` 与 `common.schema.json#/$defs/source`（早已冻结）、事务状态机、
`fake_fixture` 的 golden 输出、`schema_count` 仍为 19。

---

## ADR-0010：`rebuild` 只重建派生状态，权威不从自身重建

**背景**：规划 §2.3 把 `rebuild` 列为核心动词，§3.6:217 要求它给出证据与 reason code、
发现陌生对象只报 `unmanaged`/`quarantine`，§9.6:714 与 §9.7:839 两次禁止它删除或 GC，
§9.11:968 要求保留旧 registry 作为只读证据。而 `doctor` 的 `remediation` **已经在让用户跑
`rebuild`**——那条命令却不存在。产品引用不存在的动词，比缺一个动词更糟。

**决策**：

1. **重建范围只有两个派生文件**：`state/registry.json`（D7）与 `logs/audit/events.json`（D10）。
   `state/registry.db` 是权威，**永不由 `rebuild` 重写**；`rebuild` 的响应里带
   `database_touched: false`，并有测试断言数据库文件字节不变。
2. **权威不一致时拒绝**（`REGISTRY_INTEGRITY_FAILED`），不"尽力重建"。从自己的投影反推权威
   等于把损坏洗白：会产出一份读起来很自信、内容却是坏数据的投影。
3. **归档而非丢弃**：重建前的投影与审计快照存进 `state/rebuild/<seq>/`。§9.11:968 的
   "保留旧 registry 作为只读证据"在投影层就是这个意思；删掉快照等于抹掉"刚才是什么样"。
4. **不修事务**：未完成事务只报告（`repair` 的职责）。两个动词职责重叠会让人不知道该找谁。
5. **陌生对象永不接管**（§3.6:217、§9.6:716）：store 内无登记 → `orphan`；未匹配白名单 →
   `unmanaged`；响应里 `adopted: 0`、`files_deleted: 0` 并有测试断言文件仍在。
6. **裸 `airoot rebuild` 直接执行**（规划 §15.1 的命令签名没有子命令），`--plan` 提供只读预演。
   判据是"会不会改变权威或删东西"，不是"动词听起来凶不凶"——`gc` 会删文件所以必须批准，
   `rebuild` 只重写派生文件所以不需要。

**未改变**：事务状态机、`ACTIVE_BOUND`、`doctor` 的 D1–D10 定义、golden 输出、`schema_count`。

---

## ADR-0011：会话激活的落盘快照，以及"反激活是恢复不是删除"

**背景**：规划 §16.4:1783 要求 `env activate --json` 返回"环境 diff、来源 generation、**激活前
快照 ID** 和 **deactivate 信息**"，并要求"激活支持**嵌套栈**和 `deactivate`"，环境失效时返回
`SESSION_STATE_STALE`。步骤 9 只交付了第一条（打印脚本），其余是欠账。

**决策**：

1. **快照落盘在 `state/sessions/<session-id>.json`**。这是对步骤 9"会话激活什么都不写"的**修正**：
   "激活前快照 ID"必然要落盘。落点选在 CLI 根内、按 session 分文件；它**不是权限凭据、不进
   registry、不 bump generation**（测试断言激活+反激活全程 generation 不变、持久化记录为空）。
2. **会话 ID 显式注入**（`--session`），不自动生成：`session_id` 的生成算法是规划里的未冻结项，
   P1 只接受注入或显式入参；自动编一个 ID 会让 `deactivate` 找不到自己的栈。ID 会被清洗成
   安全文件名。
3. **反激活是恢复而不是删除**：原先存在的变量回填旧值，原先不存在的移除，PATH 只卸下本次加入的
   条目。否则用户的 `JAVA_HOME` 会被"反激活"删掉——只在用户真有旧值时才暴露的 bug。
4. **`SESSION_STATE_STALE` 用退出码 2**（降级/漂移），不是 8：会话本身没写错，是世界变了
   （generation 前进、栈文件不可读、版本不符）。与 `CURRENT_PROCESS_ENV_OLD` 同类。
5. **重复激活同一 external_id 是幂等空操作**，不压第二帧——否则 `deactivate` 需要按按键次数弹栈。
6. **`unadopt` 作为 `forget` 的兼容别名**（规划 §15.1 的原名，草案 §8 承诺保留）：同一实现，
   不复制第二份逻辑。

**副产品（比功能本身更重要）**：Skill 漂移守卫从**动词**粒度升级为**命令路径**粒度，因为
"`tool retire` 存在"曾让"`tool pin`"看起来已文档化。升级中修掉提取器一个真 bug：否定前瞻
`(?!\s*[-:=])` 会把 `airoot root status --json` 的 `status` 也排除，导致守卫把 `root` 报成未知
动词——**守卫误报比不报更糟**。

---

## ADR-0012：只读观察面与 PATH 不变量的检查器

**背景**：规划 §15.1 的命令清单里还有 11 条未实现。其中四条语义清楚、只读、不需要管理员权限：
`tool list`/`tool status`/`tool verify`（§15.4:1601-1603）与 `path verify`（§15.1:1554）。

**决策**：

1. **`tool list/status/verify` 是观察者，不是操作者**：不写 registry、不 bump generation、
   不 adopt、不修复。§15.4:1602 的"不能切换 active"与 §15.4:1603 的"不能自动 adopt 或修复"
   用测试固定下来（篡改 payload 后 `verify` 报 `MANIFEST_DIGEST_MISMATCH`，且**文件原样保留**）。
2. **`tool verify` 的判据是整树 digest + 入口点存在性**，与 `doctor --verify` 同一基准
   （`instance.artifact_digest` = `store/<id>` 的树摘要）。**绝不执行 payload**（§9.9 证据阶梯）。
3. **`path verify` 检查一条此前只有文字、没有代码的不变量**：机器 PATH 只允许一个 AIROOT 条目
   `cli\exposure\bin`；版本/store 目录绝不直接进 PATH。分类：重复条目 warning、
   `store/` 版本目录 error、非授权 AIROOT 目录 warning、launcher 目录缺失 info。
4. **只用一个 reason code `PATH_EXPOSURE_VIOLATION`（退出码 2）**，严重度由 `findings[].severity`
   表达。这是"环境偏离契约"的诊断，不是 AIROOT 自身损坏。
5. **`path verify` 不写 PATH**：写机器 PATH 属 P2 的受保护 broker；响应里带 `path_written: false`。

**暴露出的一个真缺陷（值得记下来）**：新加的 reason code 忘了在 `exits.py` 注册，第一次调用
`exit_code_for` 就抛 `INVALID_INPUT`。`AirootError` 对未注册码是 fail-fast 的——这条规则的价值
就在这里：漏注册当场炸，而不是悄悄退化成别的退出码。

---

## ADR-0013：`desired` 层是输入，`pin` 只表达意图

**背景**：五层事实模型 `desired`/`declared`/`physical`/`effective`/`historical` 里，只有 `desired`
一层完全没有代码。规划 §17 给了它的形状，§15.4:1604 给了 `tool pin` 的边界："修改 desired/selection
policy，生成新的 generation plan，**不能直接改 launcher**"。

**决策**：

1. **desired 是输入，不是权威**：写在 `state/desired.json`（§17 的形状），**不写 registry、
   不改 binding、不 bump generation**。测试断言 pin 前后 `generation`、`instances()`、
   `bindings()` 完全不变——否则"我想要 X"会变成"X 已存在"，五层模型立刻塌成一层。
2. **`pin` 表达意图并给出计划，不执行计划**：记录 desired → 若 declared 不满足则构造一个真实
   artifact plan → **应用仍需正常批准**。响应带 `active_binding_changed: false`。
3. **没有可信来源就如实说造不出计划**（`sync[].plan_blocked_by`），不伪造 locator——
   来源只能来自 `policy/sources.json`（ADR-0009）。
4. **约束语法与 `where --version` 同一套**，且在**写入前**校验；非法约束时文件不被创建。
5. **manifest 不得携带脚本**（§17 的明列）：加载期按名字拒绝 `curl | sh`、`pip install`、
   任意 PowerShell 等片段。

**未改变**：`plan`/`approve`/`install` 的事务路径、`schema_count`（desired 是输入文件，
不是对外协议文档）、没有新增 reason code。

---

## ADR-0014：意图不满足是一条**独立**诊断，不是 drift

**背景**：ADR-0013 让 `desired` 层存在，但除了 `tool pin` 自己的响应，没有任何诊断会告诉你
"你的意图没被满足"。一层状态如果只在写入它的那条命令里可见，它就没有真正进入模型。

**决策**：

1. **新码 `DESIRED_NOT_SATISFIED`（退出码 2），不复用 `DRIFT_DETECTED`**：后者在本项目里指
   "**观测**与声明不一致"，这里是"**意图**与声明不一致"。两者的下一步动作不同——前者修环境，
   后者改 manifest——所以混用会让人不知道该动哪边。
2. **挂进 `INVARIANTS["D5"]`**：D1–D10 是冻结的十个不变量（有测试断言恰好十个），新码必须
   归属于其中之一；"某能力的 active binding 不满足意图"是 binding 选择层面的问题。
3. **manifest 不可读时报同一条码**（warning）：无法判断意图是否满足，与"确实没被满足"同属不确定；
   **`doctor` 绝不因此抛异常**——诊断工具在输入损坏时要给结论。
4. **没有 manifest 时一条都不报**，并有测试断言未 pin 的 root 诊断输出与以前完全一致
   （golden 与既有 D1–D10 测试都钉在这上面）。
5. **`tool list` 在无 pin 时 `in_sync` 为 `None` 而非 `False`**：`None` = 没有意图可比，
   `False` = 有意图且未满足。压平会让"这台机器什么都没声明"看起来像"有东西坏了"。

---

## ADR-0015：跨工件一致性审计是常驻测试，不是一次性检查

**背景**：每个阶段都会同时新增四样东西——契约文字、代码路径、策略文件、散文段落——而
它们各自只被"自己那一层"的测试覆盖。**没有任何测试检查这四者是否互相一致。** 步骤 11–12
之后做了一次人工审计，立刻发现 `bootstrap` 出现在冻结的规划 §15.1 命令表里，却既没有实现、
也没有进 `agents/airoot.json` 的 `not_implemented`：它是被"没人检查"漏掉的，不是被设计漏掉的。

**决策**：

1. **新增 `cli/tests/test_l0_consistency.py`（24 项）作为常驻守卫**，归 L0（工件与协议层）。
   它不做行为检查（那是 L1 的职责），只回答"这些工件彼此还同意吗"。
2. **覆盖的互证关系**：schema 计数 ↔ 文档声明；每个 schema 文件在文档中被提到；reason code ↔
   诊断码表 ↔ 退出码映射 ↔ 实际抛出点；`caps/*.py`、`policy/*.json` 与**每个策略 revision**
   ↔ 仓库地图；冻结命令表 ⊆ 已实现 ∪ 已声明未实现，且已实现的命令必须在文档里被写过；
   golden fixture 无孤儿；策略文件取值 ↔ 代码常量。
3. **`bootstrap` 补进 `not_implemented`**，并在 `SKILL.md` 里同一处写明：没有 `bootstrap` 时
   处于 `policy_only`，那是约定与审计，**不是 ACL 强制**。
4. **测试计数采用新口径**：`AGENTS.md` 是当前态文档，它声明的所有总数必须彼此相同；契约草案里的
   总数是**分阶段历史记录**，允许不同，但**任何一个都不得超过当前值**。

**被否决的替代方案**：

- **把历史数字改成当前值**（例如把 §28.5 的 535 改成 564）：那会让同一句里"新增 6 项诊断测试"
  变成假话。历史记录写的是当时的事实，只能保留。
- **把审计写成脚本**：不随 `pytest` 跑的检查等于没有检查，漂移会照旧发生。
- **升级为 L1 行为测试**：那会让 24 项便宜的结构检查变成昂贵的端到端测试，失去"随时可跑"的价值。

**代价**：任何新增 schema / reason code / caps 模块 / 命令，都必须同步文档，否则 L0 立刻红。
这正是它存在的理由。

---

## ADR-0016：`exec` 的两种写法同义，且 `--` 之前的一切都属于 AIROOT

**背景**：审计（§30）在冻结的规划 §15.1 / §16.4 与实现之间发现一处真实分歧——文档写
`airoot exec --env <name> -- <command>`，实现只接受位置参数 `airoot exec <external-id> -- <command>`，
于是**文档里那条命令根本不能跑**（`unrecognized arguments: --env`）。同一次审计还发现一个更隐蔽的
缺陷：`exec` 用 `argparse.REMAINDER` 接收子进程参数，导致写在外部引用 id **之后**的 `--json`
被塞进子进程的参数表，并且 AIROOT 自己退回人类可读模式——一个"机器模式静默失效"的坑。

**决策**：

1. **`<name>` 就是 external reference id**：管家模型里没有"命名环境"这种对象（ADR-0004），
   `env activate <name>` 与 §15.1 同形，`<name>` = external id。
2. **`exec --env <id> -- <cmd>` 与 `exec <id> -- <cmd>` 同义**，两者在解析前被归一到同一形状，
   因此只有一条解析路径、一种语义。归一发生在 `main()` 进入 argparse 之前。
3. **`--` 是归属分界线**：`--` 之前的一切（`--json`、`--root`）都是 AIROOT 的选项，即使写在
   id 之后也会被提到命令前面；`--` 之后的一切原样交给子进程。没有 `--` 时保持 REMAINDER 行为
   （子进程参数紧跟 id，兼容既有用法）。
4. **缺子进程命令是 `INVALID_INPUT`（退出码 8）**：此前会走到 `subprocess.run([])` 并抛
   `IndexError` 回溯——工具自己的缺陷不该以回溯的形式出现。

**代价与理由**：一个命令有两种写法是冗余，但冗余的这一侧是**冻结文档的写法**；让文档写法失效
比多认一种别名更糟。归一逻辑集中在 `_normalize_exec_argv`，有 5 项测试钉住（含"两种写法产生
逐字节相同的文档"）。

---

## ADR-0017：`search` 先做协议面，crawl fallback 必须自报家门

**背景**：P2 的受保护状态需要管理员权限，暂时无法推进；而 `search` 的请求/响应**早已是第 1 层
冻结契约**（`search-request/response.schema.json`），协议方案 §4–§8 也把请求字段、边界行为、
一致性等级、错误码表和 CLI 形态都写清楚了。于是可以在不动特权的前提下把 `search` 从"文档里有、
命令报不存在"变成"命令存在、语义诚实"。计划见契约草案 §31。

**决策**：

1. **`file_search` 不写进 `policy/capabilities.json`**。那份清单是*能力边界*（哪些**对象**可以被
   discover/adopt）的输入：条目都带 `entry`（可执行文件）、`kind: tool|runtime`，并且直接决定
   `scope decide` 的路由。`file_search` 不是可 adopt 的 payload，而是 extension 提供的能力；
   混进去会让 `scope decide file_search` 得出毫无意义的路由结论。它的冻结位置是两个 published
   schema（第 1 层）。
2. **没有索引时 `freshness` 只能是 `state=unknown` + `coverage=none`**。`stale` 与 `degraded`
   都隐含"存在一个索引"：前者是索引落后，后者是索引不可信。在没有索引的机器上报它们等于伪造事实。
3. **crawl fallback 的回答必须同时满足**：`status=degraded`、
   `reason_code=SEARCH_FALLBACK_USED`(2)、`data.fallback={"kind": "crawl", …}`、
   `freshness.coverage=none`。协议 §5.3/§7 的原话是"不把慢路径伪装成高速索引"；这四项就是它的
   可检验形式。
4. **实现上限是数据，不是代码**：`limit`/`max_duration_ms`/`max_staleness_ms` 的上限写在
   `policy/search-policy.json`（`srch-1`）里，超过上限报 `EXTENSION_INPUT_INVALID`(8)，
   **不静默截断**（协议 §6.3 要求调用者不能无限放大）。
5. **cursor 绑定 query hash + root 集合 + scope + implementation_id + index generation**，
   任一处变化即 `SEARCH_CURSOR_INVALID`(8)：**绝不把旧 cursor 静默解释成第一页**。
6. **`search refresh|rebuild` 本阶段报 `EXTENSION_OPERATION_UNKNOWN`(9)**，实现留给 P3 本体
   （后台索引器 + `cache\search` 事务）。不发明一个假装刷新过的 refresh。
7. **通用 `EXTENSION_*` 家族的退出码由本 ADR 定**：协议 §6.6 只给了 `SEARCH_*` 的退出码表，
   下半段的通用码只有名字。映射遵守既有约定——选中的实现不可调用 → 9；调用方输入错 → 8；
   超时/取消/健康度下降 → 2；**扩展返回的文档不符合 published schema → 8**（与
   `SELF_VALIDATION_FAILED` 同族：输入没错，**输出**不合法）。
8. **search profile 的信封与通用 extension envelope 不是同一个形状**：通用 envelope 是
   `timing: {started_at, finished_at, elapsed_ms}` 且带 `security_mode`/`enforcement`；
   `search-response.schema.json` 要求这三项**平铺在顶层**且**没有** security_mode 字段。
   实现必须按 profile 自校验，**不能复用 `ext/envelope.py` 的构造器**——这一点在写代码时最容易
   顺手搞错，所以在这里写明。
9. **manifest 只声明这个 build 真的能做的事**：`search`/`status`/`explain` 三个只读操作，
   **不声明 `refresh`**（那是 P3 本体才有的写操作），所以 `search refresh` 得到的是
   `EXTENSION_OPERATION_UNKNOWN`(9)——"没声明"比"声明了但做不到"更诚实。另外协议 §6.1 的示例
   manifest 里 `target_scope` 写的是 `declared_roots`，而 published schema 的 scope 枚举只有
   `system|machine|session|project`：**照 schema 写 `machine`**（ADR-0003 的规则），
   查询的真实范围由请求的 `roots` 与 scope 决定，不由这个字段决定。

---

## ADR-0018：`search` 的 CLI 形状——`--search-root`、保留字只在首位、以及两个退出码

**背景**：把 §31 的协议面接到 CLI 上时，有三处只能二选一的地方：搜索 root 用哪个 flag、保留
子命令怎么和"搜一个叫 status 的文件"共存、`search status` 与 `search <query>` 的退出码为什么不同。

**决策**：

1. **搜索 root 是 `--search-root`，`--root` 仍然是 AIROOT root**。协议 §8 的示例写
   `airoot search "*.onnx" --root D:\Projects`，但全局 `--root <AIROOT root>` 在规划、核心契约、
   `SKILL.md`、`agents/airoot.json` 里**每一条命令**都是那个意思。让同一个 flag 在不同命令里
   指两个东西，正是本项目一直在拒绝的那种"聪明"。层级上第 1–3 层文档的用法压过第 4 层的示例，
   代价是协议示例里的那一个词要改成 `--search-root`——**这个代价是可见的、一次性的**。
2. **保留字只在"第一个位置参数"的位置保留**（协议 §6.3/§8 的原文要求）：`airoot search status`
   是管理索引，`airoot search --query status` 是搜一个叫 `status` 的文件。**没有任何上下文推断**：
   不做"看起来像文件名就当查询"这类启发式，因为它会在最需要确定性的地方变得不可预测。
3. **`search status` 退出码 9、`search <query>` 退出码 2**，两者都直接来自冻结的 reason → 退出码表：
   `SEARCH_NOT_READY` 是 9（"active implementation 不可调用"），`SEARCH_FALLBACK_USED` 是 2
   （"fallback 成功但结果不是索引健康语义"）。`status` 回答"这个能力现在没准备好"，而一次查询
   **确实拿到了数据**——把两者压成同一个码会抹掉这个区别。
4. **结果打标用声明状态的快照，不在遍历期间查 registry**：crawl 可能跑几秒，期间既不该握着
   数据库，也不该每个结果查一次库；快照按**最长前缀**匹配，嵌套在别的对象里的 reference 也能
   拿到自己的标记。
5. **`--managed-only` 在分类之后、分页之前过滤，并回报它藏掉了多少条**：先分页再过滤会让一页的
   内容取决于过滤顺序；而过滤后的计数如果不说明，就会看起来像"全部匹配"。

---

## ADR-0019：派生缓存可以被替换，用户文件永远不能——`gc` 的唯一性边界

**背景**：AGENTS.md 与 ADR-0007 里有一句绝对的话："`gc` 是 AIROOT 唯一删文件的路径"。而搜索协议
§5.1 又明说 `cache\search\index.db` "**损坏后可以删除并重建**"。只要开始真正建索引
（契约草案 §32），这两句话就会正面相撞：重建索引必然要替换旧文件。

**决策**：

1. **把"删除"区分为两类**：**用户文件**（数据根内的一切、项目文件、owned payload 之外的任何
   东西）**永不**由 AIROOT 删除；**派生缓存**（`cache\search\**` 之类，协议明确定义为"损坏后可
   重建、不是真相源"）可以被替换。`gc` 的"唯一性"约束**作用域是 owned payload**：它仍是删除
   owned payload 的唯一路径。
2. **替换只允许是整文件原子替换**（写 `index.db.tmp` → `os.replace` → 完成），**不允许**先删后建、
   不允许逐个删记录文件、不允许触碰 `cache\search` 之外的任何路径。
3. **永不触碰数据根**：索引构建只读数据根，写只落 `cache\search`。这一条有测试（§32.4-8 的真机行）。
4. **不用 WAL**：`-wal`/`-shm` 旁文件会引入新的删除路径与"谁的残片"问题，所以搜索索引用
   `journal_mode=DELETE` + 单事务构建 + 整文件替换。
5. **措辞一起改**：AGENTS.md 与 ADR-0007 的那句话补上作用域，避免下一个人再次撞上这个矛盾。

**被否决的替代方案**：

- **重建索引也要 approval token**：协议 §6.1 的 manifest 示例本身写的就是 `refresh` 无需批准，
  而它是**派生缓存**——要求批准会让"索引坏了"变成需要人工介入的事故。
- **按记录粒度增量删除**：等于把删文件的能力铺开，正是这条规则要防的。
- **保留所有历史索引**：磁盘无界增长，且需要另一套清理规则。

---

## ADR-0020：USN 索引器仍属 P2——先测清楚"能不能"，再谈"做不做"

**背景**：协议把 `airoot-native-search-native-index`（NTFS 元数据 + USN journal）定为 `file_search`
的目标实现。§31–§33 交付了协议面、crawl 建的索引与诊断集成，但那个"快索引"一直缺席，而缺席的
理由只写在文档里。动手之前先用一次性探针把**能不能做**测清楚（契约草案 §34.1）。

**实测（本机，`python 3.11.11`，`IsUserAnAdmin()=yes`，`C:` 为 NTFS）**：

- `CreateFileW(\\.\C:, GENERIC_READ)` 成功；`FSCTL_QUERY_USN_JOURNAL` 成功（读出 journal id / FirstUsn /
  NextUsn / MaxUsn）；`FSCTL_READ_USN_JOURNAL` 成功（65 520 字节）；**`FSCTL_ENUM_USN_DATA` 成功**
  （65 496 字节，这就是全量枚举）。
- 把句柄降为 `FILE_READ_ATTRIBUTES` 后，上述 FSCTL 全部 `err=1`（`ERROR_INVALID_FUNCTION`）。
- `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL` 被系统**识别**（返回 `87` 而不是 `1`），但 V0/V1 两种输入
  都被拒为 `ERROR_INVALID_PARAMETER`——**未解观察，不当结论**。

**决策**：

1. **不在本阶段实现 USN 索引器**。它的第一步是全量枚举，而这需要以 `GENERIC_READ` 打开卷句柄；
   本次会话是提权进程，**无法验证非管理员能否做到**。把一半能力建在"开发机恰好提权"之上，正是
   这个项目一直在拒绝的事。USN 索引器因此与 P2 的 broker 绑定：由 broker 做初始枚举，普通进程
   只消费。
2. **把"为什么没有快索引"变成工具能回答的问题**：新增**只读**探测（`caps/usn.py`）与
   `search status --probe-native-index`，`explain`/`implementations` 报出 native 候选与不可用理由。
3. **探测默认惰性**：任何命令的默认路径都不开卷句柄；只有显式旗标才探测。副作用必须由调用方点单。
4. **失败是数据**：卷不存在 / 非 NTFS / 句柄打不开 / FSCTL 被拒 → 结构化字段 + `reason`，
   绝不抛异常。
5. **不声称未验证的事**：报告里保留 `elevated`、句柄访问方式与 `unprivileged_read` 的**原始观察**，
   而不是"支持/不支持"的二元判断。
6. **教训的兑现**：本项目曾把"工具的失败"当成"数据的事实"，把错误结论写进契约（§16.0 自我纠错）。
   所以本轮先把**能证明的**与**不能证明的**分开记录，再决定做什么。

## ADR-0021：取舍默认"放宽"——以及它**不**适用的四种情况

**背景**：项目进行到 §50 时，桌面上积了几处"放宽还是收紧"的开放问题（最有名的是 §4.1 的
"任何阶段都可能进入"比合法移动表宽，以及冻结能力清单里的 `scope` 要不要变成强制）。
维护者给出的长期指令是：

> 以后放宽和收紧一律选放宽

**决策**：

1. **当一个取舍同时存在"放宽"和"收紧"两种读法或选项时，默认取放宽的那个**，不必每次再问。
2. **适用范围**：准入/拒绝、允许的状态迁移、枚举取值、是否把一条声明变成强制、阈值方向。
   一句话：**凡是"AIROOT 多接受一点还是多拒绝一点"的问题。**
3. **它不适用的四种情况**——因为这四种情况里的"收紧"不是权限，而是别的东西：
   - **图层级**：不得以违反已发布 Schema 或 layer-2 冻结契约为代价去放宽（AGENTS §3 的层级仍然有效）；
   - **诚实规则**：§8 的禁令、"不编造确定性"、"失败即数据"——放宽这些等于允许说假话；
   - **管家模型的结构性不变量**：数据根内文件永不删除、reference 永不 `uninstall`、
     owned 与 reference 的区分（ADR-0004）。这些不是"松紧"，是"是不是管家"；
   - **审计守卫**：把一致性检查加严不是"收紧用户权限"——守卫查的是工件之间是否自相矛盾。
4. **当放宽会摧毁一条更强的规则时，不机械照做**，按 3 处理并把这件事如实报出来（见落地 1）。

**本轮落地**：

1. **§4.1 的边集歧义 → 放宽表（29 条边 → 62 条）。** 文档说五个异常状态"任何阶段都可能进入"，
   而表只实现了其中一部分；放宽意味着**散文赢**，把边补上（`tx/states.py` 的 `_widen` 按规则
   **生成**，不手抄 33 条边，这样读者检查的是规则本身）。两条边界没有放宽，因为它们是更强的规则：
   - `FINALIZED` 仍然没有出边——它是 happy path 的成功终点，"终态 ⟺ 死胡同"这条不变量比
     "任何阶段"更强（而且 `FINALIZED` 本身就在 happy path 里，机械照做会直接把它变成非终态）；
   - `EXPIRED` 不能从**已经改过 active binding 的阶段**进入——§4.1 说它是终态且"不改变
     active binding"，§14.2 要求改过的绑定永远留一条回滚路径。放它进来不会让权限更松，
     只会在改过绑定之后**断掉恢复路径**，所以按决策 4 不放宽。
   边界由 `test_the_any_stage_exception_rule_is_implemented_and_bounded` 钉住：它检查**规则**
   （每个非终态阶段都能进那四个异常状态、`EXPIRED` 的边界是故意的、放宽**只**增加了异常目标、
   happy path 的前进纪律一条没松），而不是 62 条边本身。
2. **冻结能力清单的 `scope` → 只声明、不强制（documentation-only）。** §47.5 把这个开放问题记成
   "没有答案"，因为它会改变准入与路由语义。按决策 1 选放宽：**不加强制**，把"这是声明"写进
   本 ADR 与守卫。`test_the_declared_scope_is_a_declaration_and_not_an_enforcement` 把所有能力的
   `scope` 改写成最小合法集合，断言准入与路由**一动不动**——并且**验红过**：让 `check_admission`
   读一次 `scope`，它立刻变红。

**明确不做（本轮）**：

- **不**把"无读者普查"做掉：现在只钉住了"准入不读 `scope`"，没有钉住"没有任何模块读 `scope`"
  （那需要一次源码普查）。测试的 docstring 里如实写明了它的强度弱于看起来的样子。
- **不**顺手去放宽别的拒绝（`UNC_NOT_ALLOWED` / `REPARSE_POINT_REJECTED` / 数据根必须在 CLI root
  之外 / `PERSISTENCE_TARGET_FORBIDDEN`）：它们属决策 3 的结构性不变量，不是本轮被问到的开放问题。
  需要时请明确说"这些也放宽"，那是一次单独的裁决。
- **不**因此改任何 Schema、退出码或码表。

**后果**：`transaction_transitions.json` 重生（29 → 62 条边；既有 fixture 逐字节不变）；
新增两项测试；"放宽优先"从此是一条**默认**，而不是每次重新讨论的开放问题。如果维护者想要**更**宽的
读法（例如 `EXPIRED` 也从 `COMMITTED` 起可达），那必须与 §14.2 的回滚保证一起裁决，不能只改表。

---

## ADR-0022：`where` 不得机器级发现 Zone W——把一条恒定式落到执行点

**背景**：§53 复核场景台账时发现，`AGENTS.md` §4 与 §5.8 里写着的恒定式"**Zone W 永不进入 machine PATH**"
**没有任何执行点**：

* `where.py::_managed_candidates` 遍历绑定后只按 **`scope`** 过滤，**从不读 `zone`**；
* `Binding(...)` 的生产者只有两个（`tx/simulate.py`、`tx/artifact.py`），**都硬编码 `"R"`**。

所以这个状态今天**不可达**，但**没有被强制执行**——没有代码能违反它（因为没有代码产生 W），
也没有代码阻止它被违反（因为没有过滤器）。§53 把它记成 `unchecked-invariant`，并写明
"补 `zone` 过滤会改变 `where` 的选择语义，属单独决策"。本 ADR 就是那次决策。

**契约怎么说**（本 ADR 不发明规则，只把它落到执行点）。四处措辞一致，而且**两条边界都写下来了**：

| 出处 | 原文 |
|---|---|
| 规划 §3.2 分区表 | `\| W \| 用户可写执行上下文 \| cache、session env、共享环境 \| 不允许进入 machine PATH \|` |
| 规划 §5（:603） | "AIROOT 不会把这些路径加入 machine PATH、stable launcher 或**默认 `where` 结果**；如果用户显式提供绝对路径，执行责任由用户和调用方承担" |
| 三大核心契约（:466） | "W 可以执行，但**只能通过显式 session/project activation**，不参与机器级发现" |
| 验证方案 `P-002` | "允许写入，但**不能被 machine `where` 或 machine PATH 发现**" |

即：W **不进默认 `where` 结果**；W **可以**通过**显式** session/project activation 执行。

**决策**：

1. **`where` 的机器级槽位（steward / machine）不得选中 `zone == "W"` 的绑定。** 这条是承重的：
   契约说的是"发现"，而发现发生在**读者这一侧**。
2. **W 仍然可以通过显式 project/session 激活被选中**——槽位 1–2 本来就要求 `identity_match`
   （即调用方给了匹配的 `--project` / `--session`），那正是"显式激活"。所以修法**只**动机器级槽位。
3. **不新增 reason code。** 一个健康的 W 绑定被机器级 `where` 跳过时，没有任何东西出错：它合法、健康，
   只是**不参与机器级发现**。回落到既有的 `found: false` + `NOT_FOUND` 是诚实的；
   为此发明一个诊断码等于**声称这里有一次策略违规**，而这里没有。
4. **不把它标成 `usable: false`。** 那会经由 `where.py` 的 `owned_unusable` 把一个正常的 reference
   结果说成 `DEGRADED_TO_REFERENCE`——**凭空制造一次降级**。
5. **让排除可见。** `where` 响应的 `candidates[]` 每行新增**可选**布尔
   `machine_discoverable`（`= zone != "W"`）。否则一个健康、`usable: true` 的候选行被跳过而
   **读者看不到原因**，那正是本项目最反对的静默。按 `AGENTS.md` §7，新增**可选**属性是 minor 兼容。

**被否掉的替代方案（连同理由）**：

| 方案 | 为什么不做 |
|---|---|
| 只在**绑定准入**处拒绝 `scope=machine` + `zone=W` | 拒掉的是"制造矛盾状态"，不是"发现"。§53 的发现恰恰是**读者侧**没有过滤；一行历史数据、一次迁移或直接写库都会绕过准入。准入检查可以以后再加，但它替不了这一条 |
| 新增一个 reason code（如 `ZONE_NOT_MACHINE_DISCOVERABLE`） | 见决策 3：没有出错，却要付"码表 + 退出码表 + `references/reason-codes.md`（守卫第五组要求每个码被点名）+ 语料重生"的代价 |
| 把 W 候选从 `candidates[]` 里整个删掉 | 删掉的是读者**唯一**能看出"为什么没选它"的地方；排除必须可见 |
| 给 W 候选 `usable: false` | 见决策 4：会凭空制造 `DEGRADED_TO_REFERENCE` |
| 顺手让 `search --managed-only` 也排除 W 路径 | 那是一次**单独的**裁决：`search` 是显式查询，不是"机器级默认发现"；本轮不扩大范围 |

**本轮落地**：

1. `where.py`：`_Candidate` 增加 `machine_discoverable`（由 `zone` 推导），机器级槽位跳过不可发现的候选，
   `_candidate_document` 投影该字段。
2. `where-response.schema.json` 的 `candidates[]` 增加**可选** `machine_discoverable`；
   `docs/schema/README.md` 记录这次 minor 变更；`where` 的 golden 语料按规则重生。
3. `test_l1_where.py` 新增两项：机器级 `where` **不**选中 W 绑定（且候选行说明原因），
   以及**显式** session 激活**能**选中同一个 W 绑定。后一半是**负向对照**——没有它，
   "W 永远不可用"这种过度收紧也会通过，而那是**另一条**规则。
4. 台账 `S-006` 由 `unchecked-invariant` 转 `evidenced`（测试点名它），处置删除。

**后果**：恒定式第一次有了执行点；`zone` 从"只被携带和上报"变成一个**参与判定**的字段。
`where` 的候选行多一个字段，Rust 版按语料对齐即可。W 依然不可达（生产者仍写 `"R"`）——
本轮给的不是"新的 W 来源"，而是"如果 W 出现，它会被挡住"。

**明确不做（本轮）**：

- **不**动 `Binding(...)` 的两个生产者（它们本来就写 `"R"`；让它们产生 W 是**新功能**，不是本 ADR 的事）；
- **不**让 `search` / `inventory` / `effective` 过滤 `zone`：那些面报告的是**事实**
  （W 绑定存在、某个引用指向 W 路径），不是机器级发现；
- **不**新增 reason code、退出码或码表条目；
- **不** CLAIM "W 现在不可达"作为本轮的成果——它本来就不可达；本轮的成果是**执行点**。

---

## ADR-0023：ACL 基线是**观测值**，`DATA_ROOT_ACL_DRIFT` 是"变了"不是"违规"

**背景**：`DATA_ROOT_ACL_DRIFT` 自管家草案起就在 `INVARIANTS["D1"]` 里，却**产生不出来**——
§35 把它记成"写明理由的例外"，并写明"**P2 落地后这条例外必须删除**"。规划 §8.2 则直接要求
"`doctor` 必须检查 ACL 是否偏离"。§58 落地了**读**的那一半（不需要管理员）；写那一半仍属 P2 broker。

**实测（先探针后设计）**：`advapi32.GetNamedSecurityInfoW` + `GetAce` + `ConvertSidToStringSidW`
可以**只读**拿到 owner SID 与每条 ACE 的 `type`/`flags`/`mask`/`SID`，**不需要提权**，也**不需要新依赖**
（`jsonschema` 仍是唯一的第三方运行时依赖）。同一探针也测出：输出里全是**真实机器 SID**——
远端仓库是 public，所以 `acl_baseline` 只进 registry，**绝不进任何被提交的语料**。

**决策**：

1. **基线是观测值，不是要求。** 草案 §3.1 自己写着 "`acl_baseline`（**观测值**）"。所以漂移的含义是
   "现在看到的和当初记下的不一样"，而**不是**"你违反了某条要求的 ACL"。管家不拥有数据根，
   不能规定它的 ACL；它能做的是**注意到它变了**。
2. **`repair` 的含义是"重新记录基线"**，与同表的 `WHITELIST_REVISION_STALE → repair` 同一读法。
   **不是**"把用户的 ACL 改回去"——那会**撤销用户有意的改动**，与 ADR-0004 直接冲突。
   这是本 ADR 最要紧的一条：它决定 `doctor` 向用户承诺什么。
3. **ACE 顺序不排序。** Windows 的 ACE 顺序**有语义**（允许/拒绝的先后）。排序会把一次**有意义的**
   权限改动洗成"没变"。宁可对一次纯重排报漂移（那确实变过），也不要漏报一次权限收紧。
4. **不新增 reason code。** 读不出来时按 `doctor.py` 自己的惯例报同一个码——它对"卷身份**读不出来**"
   用的就是 `VOLUME_IDENTITY_MISMATCH`。但 `evidence` **必须说清是"读不出来"而不是"变了"**。
5. **没记基线 → 不报。** 无法比较就不说漂移。这也给"P2 之前不发"一个精确含义：P2 之前**根本没有基线**。

**读不出来时的第三种情形，也要说清**：基线记录了"当时读不出来"时，存下来的文档带 `observed: false`
与 `reason`，所以它**不会**在下次比较时被当成"这个目录什么都不授予"——那是这份数据最危险的误读。

**被否掉的替代方案**：

| 方案 | 为什么不做 |
|---|---|
| 把 `remediation` 改成 `inspect` | 文档表里写的是 `repair`；改它等于让实现去迁就自己。真正的修法是**把 `repair` 的读法写清楚**（决策 2），而不是改一个字让它看起来不那么像承诺 |
| 记一个**摘要**而不是整份观测 | 摘要比较不出"**哪里**变了"，而答案必须能说清；而且无法区分"读不出来"与"空 ACL"。摘要只用于 evidence 里给个短标识 |
| ACE 排序后比较（更"稳定"） | 见决策 3：那是在洗掉信息。稳定性的正确来源是"不变就不动"，不是"把变化归一化掉" |
| 在 `data-root add` 之外再提供一条"重新记基线"的命令 | 那需要一个新动词（`env forget --all` 的同类问题）。本阶段不发明命令；`repair` 的落点是"重新登记"，记在 stage 记录里 |

**后果**：

- 新模块 `caps/acl.py`（只读、`ctypes`、失败即数据）；`data-root add` 记录基线；`doctor` 的 D1 数据根检查
  增加漂移判定（`warning`/`repair`）。
- **那条例外被删除**：`RESERVED_DIAGNOSTICS` 现在是**空表**，守卫于是要求这个码像别的码一样**真能产生**。
  表本身保留，因为"声明了却发不出来"这种形状会复发，下次必须写在那里而不是留白。
- 新增场景 **`C-034`**（数据根 ACL 与观测基线不同 → 漂移；`repair` = 重新记录；没基线就不报）——
  在此之前，这个行为会**带着零个场景**上线。
- **既有 doctor 语料逐字节不变**：那些数据根没有基线，所以不发诊断。这既是"没基线不报"的直接验证，
  也说明这次语料重生是外科式的（只有 `scenario_ledger.json` 变）。

**明确不做**：

- **不写 ACL**（`SetNamedSecurityInfoW` + `WRITE_DAC` + broker）——P2 的写一侧；
- **不对 CLI root 自己的目录做基线**（规划 §8.2 那张表是受保护模式的必需 ACL，属 P2）；
- **不新增 reason code、退出码或码表条目**；
- **不把任何真实 SID 写进被提交的文件**。

---

## ADR-0024：生产批准签发方——**已裁决：A 维持现状**（见 ADR-0025）

**状态：已裁决。** ADR-0025 的 D1 **选了 A（维持现状，等 P2 的受保护 broker）**，并写下了否决
B（本地人类通道）与 C（现在就做真实 `ed25519`）的理由。本条目保留下面的背景、实测挡住表与三条路：
它们是那次裁决的**依据**，不是作废的提案。正文里的"今天"指的是提交 `§67` 时的工作树状态；
下面每一处"提案"二字指的都是本条目**写下时**的那一版，不是它现在的状态。

**指针**：裁决见 **ADR-0025** 的 D1。两条拒绝消息今天统一指向 **ADR-0025**，
因为它们说的是同一件事：这个 build 没有生产签发方。

### 背景：批准在 P1 是"只能验、不能签"

三大核心契约 §8.5 与 broker 方案 §5 把**签发**批准这件事放在受保护一侧，`AGENTS.md` §7 把它
写成一条硬规则：

> `test_hmac_sha256` 只允许出现在测试/模拟路径；核心只做**校验**，签名实现放在
> `cli/tests/fake_issuer.py`，**`airoot approve` 永远不能凭空制造批准**。

P1 如实照做：核心只有验证侧（`tx/approval.py`），唯一的签发方是
`cli/tests/fake_issuer.py`（测试用），受保护 issuer 属 P2。于是**每一个"要批准才能做"的动作，
在真机上都走不到底**——不是"少一个算法"，而是**少一个信任根**。

`state/test-keyring.json` 里的密钥是**测试**签发方的密钥，就写在 root 内：任何能写这个 root 的
进程都能签出"有效"token。它作为测试签发方完全够用（它要证的恰恰是消费侧会拒绝伪造、重放、
过期、跨 root 的 token），但它**不能**被升格成生产签发方——那样"批准"就退化成"任何同用户进程
都能做的事"，而这正是这套设计存在的理由。`security_mode=policy_only` 这个诚实口径也据此保留。

### 今天被它挡住的东西（实测，不是推断）

每一条的失败点都是同一个 `load_keyring()`（`tx/approval.py`）**或**它的 `ed25519` 分支；
只要 root 里没有测试 keyring，或 token 用的是 `ed25519`，就到此为止。两类拒绝在 §67 之后都带同一句
`no production approval issuer exists in this build (ADR-0024 is the pending decision)`，
所以下表只列各自**不同的前半句**——成因不同，下一步动作也不同。

**§82 改判**：上面那句是 §67 当时的原文，其中 `ADR-0024 is the pending decision` 已经因为
ADR-0025 的 D1 变成假话，所以它被换成 `decided: ADR-0025 keeps it waiting for the P2 broker`
（措辞统一的那条守卫保留，只换指针）。**这张表本身一个字没改**——五条路径、五条前半句、
同一个退出码，§82 没有解锁其中任何一条。

（表里写的是**函数名**而不是行号：行号会被任何一次无关改动作废，而入口的名字不会。
代价是读者要自己 grep 一次；收益是这张表不会在两周后悄悄变成假话。）

| 执行点 | 到达签发方的路径 | 前半句 | 结果 |
|---|---|---|---|
| `approve <plan> --token-file <t>` | `cli.py` 的 `cmd_approve` | `no approval keyring is installed` | `PROVENANCE_FAILED`(7) |
| `install <plan> --token-file <t>` | `cmd_install` → `runner.commit()` → `tx/artifact.py` / `tx/simulate.py` 的 `keyring()` 回退 | 同上（同一个 `load_keyring`） | 同上 |
| `env persist <ref> --token-file <t>` | `cli.py` 的 `cmd_env_persist` | 同上 | 同上 |
| `tool gc --apply --token-file <t>` | `cmd_tool_gc` → `caps/lifecycle.py` 的 `apply_gc_plan` | 同上 | 同上 |
| `uninstall <owned-instance> --token-file <t>` | `cmd_uninstall` → `caps/lifecycle.py` 的 `apply_gc_plan` | 同上 | 同上 |
| 任何 `ed25519` token（有 keyring 也一样） | `tx/approval.py` 的 `verify_signature` 算法分支 | `ed25519 approval verification is not implemented` | 同上 |

台账侧同样对得上（`cli/tests/scenario_ledger.py`）：**P-012**（policy 批准**没有生产者**）、
**P-015**（撤销那一半）、**P-017**（没有 issuer 身份证据）都是 `undesigned`，**C-022**
（人类批准通道）是 `undesigned` 且注明属 P2。它们今天**不能**被判成 `evidenced`，
因为要证的那件事缺少生产侧的一半。

**注意"没跑过"与"跑不通"是两件事**：§59 已经对真实上游跑过解析 + 下载 + 摘要校验
（`rustup-init.exe`，12 721 664 字节，SHA256 与上游发布值一致），但 `stage`/`commit` 这两步
**没跑过**，原因就是这条：真机上没有能签发批准的东西。`policy/sources.json` 的注解里
已经如实记着这一点。

### 三条路

| 选项 | 是什么 | 解锁什么 | 代价 / 风险 |
|---|---|---|---|
| **A 维持现状**（默认）：生产签发方等 P2 的受保护 broker | 不新增任何东西；要批准的五条路径在真机上继续不可完成 | 无 | **不新增任何风险**，也不新增任何假保证；代价是"AIROOT 能装东西/能持久化环境"这句话在真机上始终不成立（§8 已如实这么写） |
| **B 本地人类通道**：`approve --interactive` 在同机提示人类，签发 `approval_mode=human` 的 token（密钥仍由 root 内文件承载，安全级别等同今天的 `test_hmac_sha256`） | 真机可用；`human` 模式语义真实（`approved_by_sid` 有来源） | 那五条路径在真机可走完；C-022 的写入通道有落点 | **需要一次契约文本裁决**：`AGENTS.md` §7 的字面规则禁的是"`approve` 凭空制造批准"，而 B 让 `approve` 在**有人在场**时签发——这是把规则读成"不得在**没有人在场**时制造批准"。密钥可写 ⇒ 同用户仍可伪造，所以 B **不提供任何对抗同用户的保证**，`security_mode=policy_only` / `enforcement=same_user_can_bypass` 一个字都不能改 |
| **C 现在就做真实 `ed25519` 签发**：私钥放受保护位置或由用户显式提供，`ed25519` 校验落地 | `ed25519` 分支不再报未实现；签名成为真的**来源证明** | 同 B，且来源证明向前一步 | 私钥放哪、由谁保护，正是 P2 broker 要解决的问题。在没有受保护存储的机器上做 C，等于把"私钥就放在 root 里"变成事实上的设计——**它比 B 更糟**，因为它给同一件事披上"已签名"的外衣 |

**推荐：A 为默认，B 是唯一"今天能落地且不新增假保证"的选项，但它以一次契约文本裁决为前提；
C 应等 P2 的受保护存储。** 这条推荐不改变任何代码行为——它只是把 A 明确下来，
免得读者以为"迟早会有人补上"。

**为什么 B 也需要裁决，而不是照"放宽优先"（ADR-0021）自动落地**：ADR-0021 的四类豁免里，
"图层级"与"诚实规则"两类在这里同时被触发——B 会改动一条 layer-2 级规则（`AGENTS.md` §7 的
批准语义）的**读法**。按 §7 最后一段，放开一条规则时若会摧毁一条更强的规则（这里是
"批准必须来自人类，而不是来自 Agent 能自己跑的命令"），不能机械照做，而要如实报出来。
本条目就是那份"报出来"。

### 后果（无论选哪条）

- 两条拒绝消息今天**措辞不一致**（一条说"没有 keyring"，一条说"`ed25519` 未实现"），
  读者看不出它们是同一件事；§67 让它们**说同一件事**并把读者指向本 ADR，且用测试钉住这个指针；
- `references/confirmation.md` 的"批准的形状"第 3 步（人工批准 `plan_hash`）在这个 build 里
  **没有可用实现**；§67 把这一点写进该文件，免得 Agent 对用户描述一个走不通的流程；
  注意第 1、2 步（`plan --dry-run` / `plan`）**是**可用的，别把整条路径一起说成不可用；
- 上面四条台账条目在裁决之前**保持 `undesigned`**——不能因为"我们讨论过了"就改判成已证据。
  **§82 复核：裁决之后仍是 `undesigned`。** 选了 A 意味着这四条等的东西一条都没到；
  "被裁决过"与"被证据过"是两件事，改判它们才是把一次讨论冒充成一次实现。

### 明确不做

- **不在本条目里实现任何签发方**（本条目是那次裁决的**依据**，裁决在 `## ADR-0025` 的 D1）；
- **不把测试 keyring 升格成生产 keyring**，也不放宽 `test_hmac_sha256` 只许出现在测试路径的规则；
- **不让 `approve` 在没有裁决的情况下开始签发**——D1 之后仍然如此，因为裁决的内容**就是**"不签发"；
- **不把"措辞统一"当成"问题解决"**：§67 只让拒绝消息诚实且一致，它不解锁任何执行点。

---

## ADR-0025：P1 收口期的安全与稳定判据（七条教义 + D1–D9）

**状态：已裁决。** 本条目记录一组**已经做出**的决定，并说明每条**改变了什么、没有改变什么**。
它同时回答两件事：上一条 ADR-0024 的三条路选哪条（**选 A**），以及 P1 剩下的每一个开口
以后按什么判据裁决。

这是一次**成组**裁决，因为它处理的是一类问题——"在还没有受保护状态之前，哪些东西可以落地、
哪些必须等"。逐条问会得到互相矛盾的答案：单独看，`approve --interactive`（本地人类通道）
比"什么都做不了"好；单独看，给 `machine_id` 加个硬件指纹比"必须注入"方便。只有把它们
放在同一套判据下，才能看出它们都在**扩大信任基**或**制造假的保证**。

### 七条教义（以后同类问题按此裁决，不再逐条问）

这七条不是新发明，而是把 P1 已经反复用到的判断**写下来**，好让下一次不必从头再推一遍。

1. **不新增"看起来安全"的能力。** 一个能力如果只能做到"看起来挡住了"，就不要加——它比没有更糟，
   因为它会让人**停止寻找真正的边界**。宁可失败即关闭（fail-closed），也不要一个**假的保证**。
2. **派生状态可重建，权威不自我修改。** 谁都能删掉重建的东西（JSON 投影、`logs/audit`、
   `cache/search` 索引）可以随便重写；权威（SQLite registry、批准、ACL 基线）**只能由提交点写**，
   而且**不能由它自己的消费者去修**。
3. **只有一个提交点，且先验后切。** 改变生效状态的写操作必须落在**一个**已经命名的地方；
   切换之前先把新东西验完。两个提交点等于没有提交点。
4. **不扩大信任基。** 不引入第三方提权组件、不把硬件指纹当身份、不靠卷标猜"最像的那个卷"。
   信任基每扩大一次，"P1 不假装能对抗同用户"这句话就更假一分。
5. **永不删除、永不自动迁移、永不静默裁剪。** 数据根内任何文件不删；registry 不自动迁移；
   审计事件不因"太旧"被裁掉。要清理就**显式**清理，且只清 AIROOT 自己造的那一份。
6. **网络只出现在已验证的边界内，且永远不是测试的依赖。** 允许的联网是"取一个已经写好校验和的
   artifact"；测试不得因为断网而变红，也不得因为联网而变绿。
7. **契约变更最小化。** 已经冻结的字段名、退出码、枚举、状态含义，能不动就不动；必须动时按
   §7 的层级规则走（Schema > 三大契约 > 规划），并同步三个地方。

**放宽优先（ADR-0021）与这七条的关系**：ADR-0021 仍是默认规则；上面七条里的第 1、4、6 条
落在它的"诚实规则"豁免里，第 2、3、5 条落在"图层级"与"管家模型结构性不变量"里，第 7 条
落在"图层级"。所以本条目**不是**在推翻 ADR-0021，而是把它的四类豁免**展开成可执行的判据**——
以后遇到同类问题，先看这里有没有现成的答案。

### D1 生产批准签发方：**选 A，维持现状**

- **决定**：本 build 不实现任何生产签发方。`approve` / `install` / `env persist` /
  `tool gc --apply` / `uninstall` 五条路径在真机上继续走不到底（`PROVENANCE_FAILED`(7)），
  拒绝消息里那句指针继续把读者送到 ADR-0024/ADR-0025。
- **否决 B（本地人类通道）**：它需要把 `AGENTS.md` §7 的规则读成"不得在**没有人在场**时制造批准"，
  而密钥仍由 root 内文件承载——**同用户进程照样能伪造**。它换来的不是安全性，而是
  "有人点过一次"这个**看起来很安全**的记录（教义 1）。
- **否决 C（现在就做真实 `ed25519`）**：在没有受保护存储的机器上做 C，等于把
  "私钥就放在 root 里（或用户随手给的地方）"变成事实上的设计，还给同一件事披上"已签名"的外衣——
  **比 B 更糟**（教义 1、4）。
- **没有改变什么**：执行路径一行没改（措辞统一已在 §67 完成）。台账的 **P-012**（策略批准没有
  生产者）、**P-015**（撤销那一半）、**P-017**（没有 issuer 身份证据）、**C-022**（人类批准通道）
  保持 `undesigned`——**讨论过不等于已证据**。
- **仍然挡住的**：真机上的 `stage`/`commit`（§59 只跑过解析 + 下载 + 摘要校验）；
  `policy/sources.json` 的注解已如实记着这一点。

### D2 `root adopt` 的四条规则（copy / verify / switch）

- **决定**：`root adopt` 的规则定死为——(1) **源目录不动**（adopt 不搬、不删、不改源）；
  (2) **先验后切**（候选根自校验通过之后才进入切换）；(3) **只有一个原子切换点**
  （active root 指针的改写落在**一个**提交点，与 generation 同一个事务）；
  (4) **adopt 自己不写机器 PATH**（`exposure\bin` 那个唯一条目的写入属于 `path`/`bootstrap`，
  不混进 adopt）。
- **为什么值得先把形状定下来**：这四条就是"先验后切 + 一个提交点"（教义 3）在这个命令上的
  具体形状。形状定下之后，剩下的阻塞从"**一个还没做的决定**"变成"**一个还没有的能力**"。
- **没有改变什么**：`root adopt` 仍然不可执行（`not_implemented`）。`agents/airoot.json` 的
  `deferred["root adopt"]` 因此从**已被删掉的** `needs-decision` 改判成 `needs-admin`，
  `unblocked_by` 从 `decision` 变成 `p2-protected-state`。**它今天解锁不了任何东西**——
  只是它等的东西换了个名字，而那个名字是诚实的。

### D3 `.ai/tooling.json` 维持只读

- **决定**：不实现写入通道（直到 P2 的人类通道）。
- **理由**：它喂的 `memory` 规则会**跳过确认**。写它等于让 Agent 自己积累授权——这是教义 1 与
  教义 4 同时触发的最坏形状：一个看起来是"记住用户偏好"的功能，实际是一条绕过确认的路。
- **守卫**：`cli/tests/test_l0_consistency.py` 里那条"有没有别的地方开始写它"的检查保持有效
  （实测：没有任何地方写它）。

### D4 launcher（`exposure\bin`）：P1 不写任何文件，形状先定下

- **决定**：P1 继续不写 launcher（`EXPOSED` 的语义是"用一次全新的 registry 读取观察到新
  binding"）。当它上线时，形状必须是：(1) 机器 PATH 里**只有一个** AIROOT 条目；
  (2) shim 的内容**确定性**且 digest 被记下来（可复现、可校验，而不是"生成一次算一次"）；
  (3) 替换是**原子**的，并与 binding/generation 绑定（哪个 shim 属于哪个 generation 是可读的
  事实）；(4) `path verify` 能发现缺失与不匹配。
- **为什么现在只写形状**：launcher 是"被 PATH 找到"的东西，写它的那一侧需要受保护状态
  （教义 3）。形状先定下来，P2 就不必再裁决一次"shim 算什么"。

### D5 machine 级环境变量持久化：继续 `PRIVILEGE_REQUIRED`(5)

- **决定**：`env persist --scope machine` 继续报 `PRIVILEGE_REQUIRED`(5)，不假装成功，
  也不退化成"写 HKCU 然后说这是机器级"。
- **理由**：机器级写入（HKLM + 广播 + 新进程读回校验）是受保护状态；在拿到 broker 之前，
  任何"近似实现"都会制造一个**假的机器级**（教义 1）。user 级持久化
  （plan → approval → 写入 → 精确还原）已经落地，那条路不受影响。

### D6 Native Index：等 P2，形状先定下

- **决定**：USN 常驻索引器仍然属于 P2。它的形状定死为：(1) **broker 做初始的全量枚举**
  （那一步要提权）；(2) **索引永远是派生缓存**（`cache/search`，可删可重建，永远不是权威）；
  (3) **journal 断档即拒答**（宁可不回答，也不回答一份不知道漏了什么的结果）；
  (4) 计数按调用者可读的权限过滤后报告，不报"总数"再减；非 NTFS 卷**回落 crawl**；
  (5) 索引器有自己的 `implementation_id`，并带独立的 `index_integrity`。
- **第 3 条是这套形状里最重要的一条**：它是"失败即数据"在索引上的具体形状——一个不知道
  自己漏了什么的搜索结果，比一个明说"我不知道"的失败更危险。
- **没有改变什么**：协议面 §31 与 crawl 建的持久索引 §32 已经可用，且**不是** USN 索引——
  `AGENTS.md` §8 的禁令不变：`freshness.current` 只表示"这份清单是最近一次遍历建立的"。

### D7 Everything：只做基准，不做 adapter

- **决定**：不写 Everything adapter。Everything 只作为**性能基准**（"同类问题别人做到什么
  程度"），不作为实现或依赖。
- **理由**：adapter 会把一个第三方常驻服务拉进信任基（教义 4），而它提供的能力已经被受控 crawl +
  索引覆盖——覆盖度差一截，但**差在哪是可测的、可报的**。基准与依赖是两件事：把基准当依赖，
  就等于让"性能看起来差不多"变成"语义也差不多"。

### D8 四件"不做"（每条对应一条教义）

- **D8-1 身份只接受注入，永不由硬件推导。** `machine_id` / `session_id` / `project_id` 的生成
  算法继续不存在（规划 §23 的未冻结项）：P1 只接受注入或显式入参。**不从硬件指纹推导**——
  卷序列号、主板 UUID、MAC 都不是身份，它们只是**指纹**：既会变，也会被换，还会把本机事实
  写进被提交的文件（教义 4；远端是 public）。
- **D8-2 根定位继续失败即关闭。** 只有 `--root` / `AIROOT_HOME`；**不扫卷标、不猜"最像的那个
  卷"**（教义 1、4）。猜错的代价是"把别人的数据当成自己的 root"，而这个代价不会在猜中的
  那一刻显形。
- **D8-3 不自动迁移、不自动裁剪；导出只读。** registry migration 工具、event 保留期、
  `logs/audit` 导出协议继续只有骨架：**不自动迁移**（迁移是一次显式动作）、
  **不自动裁剪**审计事件（教义 5）、导出是**只读**的（导出器不改任何权威）。
- **D8-4 第一个真实 artifact 是 `build`。** 理由不是"build 更重要"，而是它是**冻结清单里唯一
  有已注册可信来源的能力**（`policy/sources.json`）。runner 继续是 `cli/bin/airoot.cmd`
  （开发期 launcher）；故障注入只用**本地 fixture**，不联网、不依赖外部服务（教义 6）。

### D9 冻结能力清单：批准 **减去 `media_probe`**

- **决定**：冻结清单以 `cap-2` 为准——**7 个能力**，`media_probe` **移除**（用户选择"解冻"
  而不是"保留"）。白名单随之升到 `wl-4`（**6 条**）。
- **为什么移除它而不是留着**：它是"看起来安全"的典型——名字暗示只读探测，而 P1 没有任何东西
  消费它、也没有来源清单条目。留一条没人用、没人验的冻结项，等于给读者一个"这台机器支持媒体
  探测"的假保证（教义 1；与 `needs-decision` 被删是同一个判据：**一个没人落在里面的条目/
  词不该存在**）。
- **它的移除是级联的**：`capabilities.json` 的 revision、`execution_bounds.json` 里冻的
  revision、golden 语料、`AGENTS.md` §2 的计数都必须同批改——这本身就是"改一处必须改三处"
  （§7）的一次实测。
- **顺带记下每条能力的来源状态**：只有 `build` 在来源清单里有条目；其余六个只有
  reference/import 一条路（`source resolve <cap>` 返回 `NOT_FOUND`(1)）。这不是缺陷，是**事实**：
  清单里没有验证过的来源，就不该有一条看起来能下载的路径。

### 这一版仍然挡住的（诚实清单）

- D1 与 D2 **今天都解锁不了任何可执行的步骤**：五条消费批准的路径全部停在
  `PROVENANCE_FAILED`(7)；`root adopt` 只是把等的东西从"一个决定"换成"P2"；
- **P-012 / P-015 / P-017 / C-022 保持 `undesigned`**；
- machine 级环境变量、ACL 写一侧、`exposure\bin` launcher、USN 常驻索引器、
  `.ai/tooling.json` 写入：**全部等 P2**，与 `AGENTS.md` §1 的"尚未实现"清单逐条对得上；
- 这份清单本身**不是**"以后会补上"的承诺：它只说明每条等的是什么。

### 明确不做

- **不在本条目里改任何执行路径的行为**：D1–D9 里只有 D9 改了数据（冻结清单与白名单），
  其余都是"维持现状"的裁决——把"没做"写成"决定不做"，是为了让下一个人知道它**已经被想过**，
  而不是被忘了；
- **不把"放宽优先"当成本条目的依据**：D1/D3/D4/D5/D6/D7/D8 都是**不放宽**的选择，
  理由逐条引上面的教义；
- **不用"讨论过"改台账**（见 D1 的最后两条）。

## ADR-0026：`state/desired.json` **不是** `desired-manifest`；这一版有 5 个 schema 没有写者

**状态：已裁决。** 本条处理一类冲突：**一份已发布的 schema、一份真实存在的文件、和代码里对它的
称呼，三者说的不是同一件事。** 它由 §92 的实测触发，裁决是**两侧都不改形状**：schema 维持原样，
文件维持原样，改的是"谁在说它是什么"。

### 冲突是什么（实测，不是推断）

- **发布侧**：`desired-manifest.schema.json` 要求 `source` 是**对象**（`kind`/`identity`/`signer`），
  并要求 `policies.auto_approve` **存在**。规划 §17 的示例与它一致（manifest 要有来源、版本、平台、
  架构、digest、策略版本）。
- **实现侧**：`caps/desired.py` 写出的 `state/desired.json` 是
  `{"schema_version":1,"manifest_id":...,"manifest_revision":1,"platform":"windows",`
  `"architectures":["x64"],"capabilities":[...],"policies":{},"source":null}`。
  拿它去跑 `validate_self("desired-manifest", ...)`，**被拒**：`source: None is not of type 'object'`
  与 `policies: 'auto_approve' is a required property`。**这个文件从来没有被任何代码校验过**，
  所以这个分歧一直不可见。
- **称呼侧**：三处都把它当成那份 manifest——模块 docstring 说它"following the shape 规划 §17
  prescribes"、`load_desired` 的报错写的是 `unsupported desired-manifest schema_version`、
  schema 目录的边界行写的是 "Desired state supplied by project or signed manifest"。
  **三处都指向一份它并不满足的契约。**

### 裁定：不改形状，改称呼，并把"没有写者"变成可查的清单

1. **不把 `state/desired.json` 改成 schema-valid。** 要满足 schema 就得往里写一个 `source` 对象——
   而这一版**没有**来源证明（ADR-0025 的 D1：维持现状，等 P2 的受保护 broker），写进去就是
   **编造来源**，违反 §8 的诚实规则。`policies.auto_approve`（跳过确认的 `memory` 规则）同理：
   `.ai/tooling.json` 刻意保持只读（ADR-0025 的 D3）。**为了让校验变绿而发明字段，比校验不绿更糟。**
2. **不改 schema。** 把 `source` 变成可空、把 `auto_approve` 变成可选，是**改必填字段**——
   README 规则 2 说这需要新的 schema id，代价落在**图层级**（ADR-0021 的豁免之一）。
   这份 schema 描述的是**真正的 manifest 边界**（`reconcile <manifest>`、项目清单、带签名的远程
   manifest），它是**对的**，只是这一版没有实现它。
3. **所以：`state/desired.json` 是"本 build 自己的 desired 层状态"，不是那份 manifest。**
   改掉代码与文档里的称呼，让读者不再去查一份不描述它的契约。
4. **把"这一版没有写者"从散文变成清单。** 派生规则：一个 schema 只要**没有任何
   `validate_self`/`validate_document` 调用点**、也**不被别的 schema `$ref`**，它就是"没有写者"。
   19 个里正好 **5 个**：`broker-request`、`broker-response`、`desired-manifest`、`gc-plan`、
   `runtime-instance`（`common` 被 `$ref`，算在用）。清单与理由写在 `docs/schema/README.md`，
   守卫第三十四组把它与推导结果**双向**钉死。
5. **顺手修掉这条盲点已经造成的一处假声明**：`references/field-values.md` 的 `gc-plan` 一节原先
   把 `caps/lifecycle.py`/`tx/artifact.py`/`tx/simulate.py` 写成 `items[].kind` 的写者——那三个模块
   写的是 `plan` 的 `target.kind`，**同样两个词、不同字段**。取值表的守卫按"文件里有没有出现这个
   字符串"判，于是**同名词假覆盖**（§74/§77 已记下这条已知漏法）让它一路绿到现在。改判为
   `（没有写者）`，两个取值都打 †。

### 没有改变什么

- **没有改任何 schema、任何必填字段、任何枚举**；19 个文件一个字节没动。
- **没有改任何对外 JSON 形状**：`state/desired.json` 的内容与 `tool pin` 的输出一字未改，
  改的是一句错误消息（`reason_code` 仍是 `INVALID_INPUT`(8)）与三处关于"这是什么"的说明。
- **没有新增命令、没有改变任何退出码。**

### 这一版仍然挡住的（诚实清单）

- **`state/desired.json` 的形状这一版只有代码定义**（`caps/desired.py` 的 `to_document()`），
  **没有任何已发布契约钉它**。这是本条目**如实记录的缺口**，不是"以后会补上"的承诺：
  解锁它要么是一条新的 desired-state schema（P6 的项目/会话层），要么按 README 规则 2 给
  `desired-manifest` 发一个新 id——两条都是**契约变更**，不在 P1 收口期做。
- **5 个"没有写者"的 schema 仍然没有写者。** 本条目做的是让这件事**可查、可守**，
  不是让它们有写者：`broker-*` 属 P2，`runtime-instance` 属 P5，`desired-manifest` 等来源证明与
  记忆通道，`gc-plan` 等 P4 的批量回收。
- **`field-values.md` 取值表的守卫仍然按"字符串出现在被点名的文件里"判断**，所以同名词假覆盖
  这一类**总体上仍然可能发生**；本条只清掉了已经发生的那一处，并把机制写在取值表里。
  要根治需要按**字段路径**而不是按字符串查写者，那是一次独立的改动。

## ADR-0027：规划里有名字、这一版刻意没有的动词，拒绝时要报 `NOT_IMPLEMENTED`（退出码 1）

**状态：已裁决。** 本条处理一件**声称与实际不一致**的事：登记表（`agents/airoot.json` 的
`deferred`）从 §60 起就为六条命令路径写着"为什么没有"与"什么才能解锁它"，而 CLI 从来没有把这句话
说出来过。§101 实测了调用它们的实际结果，本条裁决拒绝的形状。

### 实测（不是推断）

```
$ airoot bootstrap
   exit   : 8
   stderr : error: INVALID_INPUT: argument command: invalid choice: 'bootstrap' (choose from ...)
```

六条（`bootstrap`、`reconcile`、`path backup`、`path restore`、`root adopt`、`root relocate`）
**全部**如此：退出码 8、`INVALID_INPUT`、消息是 argparse 的 `invalid choice`，`evidence` 是 usage 行。
没有 `reason_code` 之外的任何信息，没有类别，没有解锁词。

三件事因此同时是假的或不存在的：

1. **它读起来像拼错。** `invalid choice: 'bootstrap' (choose from 'root', 'where', ...)` 是**打字错误**
   得到的同一句话，于是调用方的下一步是回去检查拼写，而不是去查规划；
2. **两个相邻的码都不成立。** `INVALID_INPUT`(8) 说"你的输入错了"——调用方的输入没有错；
   `PRIVILEGE_REQUIRED`(5) 的读法是"提权再试"（`caps/exposure.py` 自己写着这句），而在这一版里
   **再高的权限也变不出这个命令**——本条裁决是在一个**已经提权**的会话里做的，这条路径仍然不通；
3. **登记表与 CLI 之间没有任何关系。** 双方可以永远各说各话，而且**不会有任何守卫变红**——
   这正是 §98 那条镜头（"红不了的检查不是检查"）的又一个实例：登记表承诺"调用方会被告知为什么"，
   而 CLI 一个字都没说。

### 裁定

1. **新增 reason code `NOT_IMPLEMENTED`，映射到退出码 1**（簇 1「没找到」）。理由就是上面第 2 点：
   真正为真的那句话是"**没有任何可用的东西回来**"，那是退出码 1 的定义。它不是 8（输入没错），
   也不是 5（提权无用）。在一个已有 95 个码的表里加一条**落在既有退出码内**的码，是 ADR-0002/0003
   已经走过的路（P1 自己引入的码都这么进来的），退出码 0–9 本身一个没动。
2. **六条路径在**动词位置**上被拦下**，由正常错误路径拒绝，因此 `--json` 得到的是标准错误信封。
   `details` 里带两个**机器可读**的事实：`deferred_category` 与 `unblocked_by`；`evidence` 里带
   同一件事的人读版本与指向登记表那段理由的指针。
3. **判断只看开头的动词 token。** `bootstrap` 仍然可以是能力名或路径名：`airoot where bootstrap`
   必须走到查找并如实回答"没找到"，而不是拒绝这个**词**。守卫把这一点单独钉住。
4. **两个事实被有意复制进核心**（`cli.DECLARED_ABSENT`）。核心**不得**在运行期读 Skill 层文件：
   受保护 broker 的信任边界（规划 §8.1）要求它不信任用户可写的 Skill 代码。**有意的复制必须配一条
   让单侧改动不可能的守卫**，所以 `test_l0_consistency.py` 新增第三十五组，把核心的表与登记表
   按动词、类别、解锁词**双向**钉死——两个方向各自是一条不同的谎。

### 没有改变什么

- **没有新增命令、没有新增动词到 parser 里**：`path --help` 仍然只列 `verify`，`root --help` 仍然只列
  `status`。教一个不存在的命令面是另一种"文档不知道自己的输出是什么"；拒绝发生在动词位置，
  不假装那里有一个命令。
- **退出码 0–9 一个没动**；`NOT_IMPLEMENTED` 落在 1 里。
- **六条路径仍然没有实现**，本条只改**拒绝的形状**，不改它们的状态。

### 顺手修掉的两处陈述

- **`SKILL.md` 说 `root adopt|relocate`"需要完整的 copy/verify/switch 规则"**——那句在 **ADR-0025
  之后已经过期**：规则已经决定（源目录不动、先验后切、一个原子切换点、adopt 自己不写机器 PATH），
  剩下的是切换本身，属受保护状态。§82 已经按这个理由把登记表的类别从 `needs-decision` 改成
  `needs-admin`，但 Skill 入口那段散文没跟上。现在两处一致：**等的是 P2，不是一条还没写的规则。**
- **`SKILL.md` 说 `reconcile`"语义在规划里只有一句"**——登记表里的理由更准确（它要读**项目清单**，
  而这一版没有任何东西产出或读它，属 P6）。入口文档改为与登记表同一个理由。

### 这一版仍然挡住的（诚实清单）

- **`--help` 仍然不会告诉读者这六条存在。** 它们只在登记表与 `SKILL.md` 的《未实现的命令》一节里。
  这是**有意的**（不教不存在的命令面），代价是：一个直接敲 `airoot --help` 的人不会知道
  `bootstrap` 是被想过并被推迟的，只会觉得它不存在。要改就是另一条裁决（把"已声明缺席"放进 help）。
- **`reconcile` 的类别依赖 P6 的 `project manifest` 真的会在 P6 落地。** 如果 P6 换了形状，
  登记表的类别与核心的表要**一起**改（守卫会红，但红的处理方式是人选的）。
- **核心与登记表的复制仍然是一份复制。** 守卫让它们不可能悄悄分叉，但它没有消除复制本身；
  消除它需要核心在运行期读 Skill 层，而那正是受保护模式不允许的。这是**取舍**，不是遗漏。
- **错误信封没有已发布的 schema 描述**（19 个 schema 里没有 error-response 那一份），所以
  `NOT_IMPLEMENTED` 的 `details` 形状是**代码定义的**，与 §92.9-6 记下的那条边界同一个来源。

## ADR-0028：每一次失败打印的那份文档也要有契约；计数守卫不得要求重写历史

**状态：已裁决。** 本条处理两件被 §102 实测连在一起的事：**唯一没有任何 schema 描述的对外文档**
（失败信封），以及**加第 20 个 schema 今天要付的代价**（计数的守卫会要求改写 38 行历史）。

### 实测一：失败文档是唯一"没人钉"的对外文档，而它恰恰是最常被打印的那份

`AirootError.to_envelope` 是它**唯一**的写者，`_report_error` 是它**唯一**的打印点。把 96 个已注册
reason code 各走一遍，**全部**产出同一组键：

```
('evidence','message','reason_code','schema_version','status')  ->  96 个 code
```

外加一个可选键 `details`（今天只有 §101 的 `NOT_IMPLEMENTED` 会写它）。这和 §100 的结论**正好相反**：
那里 29 条 lane 有 29 个互不相同的形状，所以"一份信封 schema"是被测量否掉的；这里一个形状覆盖全部，
所以一份 schema 是**量出来的可行**，不是猜的。

三条由此同时成立：

1. **19 个 schema 里没有一份描述它**，而 AGENTS.md §7 写着"核心在打印任何对外 JSON 之前调用
   `validate_self`"——对这份文档，那句话是**假的**：它没有 schema 可校验；
2. 它是**每次失败**都会打印的文档，也就是 agent 出错时唯一会读的那份；
3. §101 刚刚往它里面加了一个键（`details`），加在一个**没有任何契约**的形状上——这正是"下一个人
   会再改一次而没人看得见"的成因。

### 实测二：加第 20 个 schema，今天会让 41 行变红，其中 38 行是历史

`test_the_cli_schema_count_matches_every_document_that_states_it` 原先对**四份文档一视同仁**：
任何一行只要提到 schema 计数，就必须出现**当前**的数字（或用 `取代`/`原提案` 标注）。把 `actual`
从 19 改成 20 之后，红的行数是 **41**，其中 **38 行在契约草案里**——它们是 §21/§31/§49… 的
逐阶段记录，写着"`schema_count` 仍为 19"或者"`schema_count: 19`"。**那些话在写下的时候都是真的**，
而满足这条守卫的唯一办法就是**改写历史**。

同一个仓库里，**测试计数**的守卫早就有正确的写法：草案里的总数是逐阶段记录，
**允许不同，但不允许超过当前值**。两条守卫、同一种文档、两套规则——这个不一致本身就是缺陷，
而且它会**卡住整个路线图**：P4/P5/P6 每个阶段都可能新增 schema，每次都要求重写一遍历史。

### 裁定

1. **发布 `error-response.schema.json`（第 20 个）**，把失败文档变成第 1 层契约：
   `schema_version` 钉 1、`status` 是 `const: "failed"`、`reason_code` 用
   `common.$defs.errorCode`（第一次被 `$ref`，pattern 从此只有一处定义）、`message` 非空、
   `evidence` 是**短字符串数组**、`details` 是**键为数据、值为标量**的可选对象。
2. **`evidence` 就是字符串数组，不是 `common.$defs.evidence` 的 `{kind, detail}` 对象。**
   这不是疏忽也不是新分歧：**已发布的 `doctor-response` 对单条诊断的 `evidence` 用的就是字符串数组**。
   两类文档本来就是两种意思——**结构化发现**（`where`/`doctor`/`search`/`transaction`/扩展信封/
   `broker-response`/`registry-projection`/`common.externalReference` 八处用对象）与
   **一句话说明**（`doctor-response` 的诊断 + 现在这份）。本条把这个**同形不同义**写成契约里的
   一句 `description`，而不是让它继续当巧合。
3. **`_report_error` 自校验，但永不遮蔽失败。** 别的对外文档都是"校验通过才替换上一个"，
   所以失败可以报成 `SELF_VALIDATION_FAILED`；在这里那样做是**反的**：被校验的文档就是"失败的报告"，
   把调用方的 `reason_code` 换成实现者的，等于把他要问的那件事藏起来。所以缺陷**追加到 `evidence`**
   （人读输出也带它），`reason_code` 保持命令真的产生的那个。
4. **计数守卫按文档分档**：**当前状态文档**（`AGENTS.md`、`docs/schema/README.md`、审查报告的状态节）
   必须写出当前计数；**契约草案**是逐阶段记录，只要求**不声称一个超过当前值的数**。这与测试计数的
   守卫**同一条规则**。**这是一次对审计守卫的修改，因此按 ADR-0021 的第四条例外如实报出来**：
   放宽的是"历史行必须重述当前值"这一条，收紧的是"草案不得声称超过当前的数"这一条（新加）。
5. **失败的文档进验收语料。** 守卫第三十二组要求"被 `validate_self` 校验的 schema"与"golden 里有
   fixture 的 schema"双向相等，所以新增 `error_not_implemented.json`。它**从工件推导**（取
   `DECLARED_ABSENT` 的第一条 + `main` 用的同一个构造器），不是把手写句子抄进生成器——手抄的
   fixture 会让"逐字节可复现"这句话变空。

### 没有改变什么

- **没有任何已发布的 schema 被改动**（只新增一个文件）；退出码 0–9、reason code 表、事务状态机、
  成功路径的任何对外文档**一个字节没动**。
- **没有新增 reason code、没有新增命令**：失败信封里放什么，和"哪些码存在"是两件事。
- **`state/desired.json`、`.ai/tooling.json`、broker 的五个"没有写者"的 schema** 全部维持原样
  （后者的数量仍是 5：`error-response` 有写者，不在这份名单里）。

### 这一版仍然挡住的（诚实清单）

- **计数守卫只扫"提到 `schema_count` 或 `JSON Schema`"的行。** 草案里那些"19 个 schema"的散文
  （不含这两个字面量）**从来没被它扫到**；`docs/schema/README.md` 里"从 18 涨到 19 个文件"那句
  本来也逃过了（§102 顺手改对了，但不是守卫逼的）。**一句话换个说法就能绕过这条守卫**——这是它的
  已知粒度，不是新缺陷。
- **`details` 的值被钉成标量。** 将来某个码要放一个对象进去，按 README 规则 2 那是一次需要新
  schema id 的改动——这是**有意的**：今天唯一写它的地方（§101）全是字符串，而"接受任何东西"的
  契约等于没有契约。
- **只有"顶层打印器"这一条路被钉住。** 三条命令把错误**折进领域文档**（`root status` 的
  `reason_code`、`discover` 的 `missing[]`、`tool pin` 的 `sync[].plan_blocked_by`），它们不走
  `_report_error`，也**没有被这条契约覆盖**——它们各自的文档由各自的 schema 管（或不管）。
  这一轮**没有**去量那三条折叠出来的形状是否也有契约。
  **§103 更正了这里的一处错**：本条最初写的是"四条命令"并多列了一个"`session` 的老值"——那是**没有量过
  就写进一张量过的表**里的一项，实测（`grep 'except AirootError'`：CLI 里只有 99/156/373/1872/3334
  五个站点）只支持**三条**折叠，外加 `doctor` 一次**降级**（catch 到 registry 错误后不带 registry
  继续跑，把原因作为诊断报出来，所以它**也保留了码**）。
- **失败文档不是任何 lane 读的文档**：没有 lane 会在失败时读它（lane 读的是成功回答），
  所以它的字段是**被契约钉住**，不是**被 lane 覆盖**。这两件事在 §94 之后一直分开记。
- **"每一次失败都校验"这句话仍然只对顶层打印器成立**：被折进领域文档的那四条路径、
  以及 `--json` 之外的人读输出，都不经过它。

## ADR-0029：折叠进成功文档的失败必须带上它自己的码；聚合码只在顶层

**状态：已裁决。** 本条处理 §102 记下的那条边界（"被折进领域文档的错误，折出来的形状有没有契约"），
并**更正 ADR-0028 里的一处未测量陈述**。

### 实测（不是推断）

`grep -n 'except AirootError' cli/app/airoot` 在 CLI 里只有五个站点（99/156/373/1872/3334），
它们分成三类，**不是** ADR-0028 原先写的四类：

| 站点 | 做法 | 码还在吗 |
|---|---|---|
| `cmd_root_status`(99) | 折进**文档自己的** `reason_code`，退出码跟着变 | ✅ |
| `cmd_tool_pin`(1872) | 折进 `sync[].plan_blocked_by` / `plan_blocked_detail`，文档码仍是 `SUCCESS`、退出 0（"愿望记下了"是真话），由 `test_l1_desired.py` 与真机验收断言 | ✅ |
| `cmd_discover`(373) | 折进 `missing[]`：**只留 `f"{id}: {message}"`**，顶层码写死 `DATA_ROOT_MISSING` | ❌ |
| `cmd_doctor`(156) | **不是折叠**：catch 到 registry 错误后**不带 registry 继续跑**，把原因作为一条诊断报出来（`code`/`severity`/`evidence` 齐备） | ✅ |

**§103 更正 ADR-0028**：它写的是"四条命令把错误折进领域文档"，第四项列的是"`session` 的老值"。
`caps/session.py` 里那两个 `SESSION_STATE_STALE` 是 **`raise`**，不是折叠；CLI 里也没有 session 的
handler。**那是一项没有量过就写进一张量过的表里的事实**——本条如实记下，并把两处文档一起改对。

`discover` 是**三处折叠里唯一的异类**，而且它今天**恰好是对的**：`discover_data_root` 只有**一个**
`raise`（`DATA_ROOT_MISSING`，两个站点），白名单在循环外加载，所以写死的码总是等于真正的码。
**"恰好对"和"被守住"不是一回事**：这一轮把第二处 `raise` 驱动出来（monkeypatch 成
`REPARSE_POINT_REJECTED`），折叠后的文档里**除了那句散文什么都没有**——

```
missing: ['dr-env: a data root may not be a reparse point']
reason_code: DATA_ROOT_MISSING        # 退出码 6
```

也就是说：**调用方拿不到真正的码**，而退出码**永远不会**随原因改变。

### 裁定

1. **`missing[]` 的每一项带上它自己的码**：`{data_root_id, reason_code, detail}`。
2. **顶层码保持 `DATA_ROOT_MISSING`（聚合）**，理由写进代码与速查：好几个数据根可能同时失败，
   顶层要回答的是**类**（"某个已声明的数据根读不了"——状态问题，退出码 6），而**原因**在行上。
   这与 `root status`（文档谈的就是那一个 registry，所以折进自己的码）和 `tool pin`（一次意图写入成功、
   一件计划被挡，所以码落在字段里）是同一条原则的第三个实例：**折进哪个位置取决于文档在谈什么**。
3. **不是折叠的那一处不动**：`doctor` 的降级本来就带着码（诊断里），不新增形状。
4. **agent 面跟着改**：`discover` 的 lane 增加 `missing[].data_root_id` / `missing[].reason_code`
   两条 `read`。**这两条立刻被 §99 的空穿判据逮住**——该 lane 的场景里 `missing` 是空的，
   "改个键名也会绿"。所以场景补了一个**读不到的数据根**（声明后删掉目录，量完再建回来，
   后面的测试不受影响）。这正是 §99 想要的效果：**说要读 `x[].y`，就得有非空的 `x`。**

### 没有改变什么

- **没有新增 reason code、没有新增 schema、没有改退出码 0–9**：变的是**折叠里带什么**，
  不是"哪些码存在"或"哪个码映射到哪个退出码"。
- `DATA_ROOT_MISSING` 的**聚合用法**是既有行为（既有测试与真机验收都断言退出码 6），本条只补上原因。
- 顶层 `reason_code`、`missing` 这个键名、`files_touched` 等字段**都在**，只有 `missing[]` 的**项**
  从字符串变成对象。

### 这一版仍然挡住的（诚实清单）

- **`missing[]` 的形状没有已发布 schema**：`discover` 的文档是 §100 量到的 29 份未钉住的报告之一，
  本条只让它**自洽**（码是真的码），没有给它契约。§100 的结论（一份信封盖不住 29 个形状）没有被推翻。
- **`tool pin` 的折叠仍然报 `SUCCESS`(0)**：这是**有意的**（愿望写下了），而且有测试与真机验收；
  但"退出码 0 + 一个被挡的计划"这件事**只有读 `sync[].plan_blocked_by` 的调用方**才知道。
  本条**没有**改它——改它就是改一条已被断言、且理由记在 §27 的行为。
- **三条折叠里只有一条被本条修过**：`root status` 与 `tool pin` 本来就带码，本条只是**量了它们**，
  并没有给它们加守卫。将来它们丢码，能红的只有它们各自的测试。
- **"折叠"这件事本身没有台账**：哪些命令允许把失败折进成功文档、折在哪个字段，散在代码与各阶段记录里，
  没有一份清单。本条只把**今天存在的三处**列全。

## ADR-0030：同一句"schema 里的每个 enum"只能有一个走法，完整性用**形状**证明

**状态：已裁决。** 本条处理 §97.7-4 记下的那条边界（"`_enums_in` 不管 `oneOf`/`allOf`"），
量出来的洞比那条记录写的更大。

### 实测（不是推断）

仓库里有**两个**走法在回答同一个问题——"这个 schema 声明了哪些 enum"：

| 走法 | 位置 | 下降的关键字 | 看到 / 全部 |
|---|---|---|---|
| `_enums_in` | `test_l0_consistency.py`（fixture 覆盖规则 + 合成自检） | 只有 `properties`、`items` | **38 / 61** |
| `enum_value_sets` | `test_l1_field_values.py`（取值表完整性） | **每一个 dict/list**（完整） | 61 / 61 |

`_enums_in` 的 docstring 写着"every `enum` the schema declares, **at any depth**"——**这句话是假的**。
它看不见的 23 个：`common` 的**全部 18 个**（都在 `$defs` 里）、`extension-manifest` 的 3 个
（在 `operations.additionalProperties` 下）、`reference-plan` 的注入黑名单
（`exposure.variables.propertyNames`/`not`）、`registry-projection` 的 `source_kind.anyOf`。

**而今天没有任何东西依赖那看不见的一半**：它只被用来走 `search-response`（那里 5/5 都可见），
取值表的完整性用的是**另一个**走法（完整的那个）。**守卫是对的，对的理由却没人写下来**——
这正是 §104 要修的东西：一次"把规则拓宽到第二份 schema"的尝试（§97 就考虑过）会直接继承这份失明，
而那时它的答案会**静默地**只覆盖一半。

### 裁定

1. **一个走法**：`cli/tests/schema_walk.py` 的 `enums_by_path`（路径 → 取值）是唯一定义，
   `enum_value_sets` 由它派生。`test_l0_consistency` 与 `test_l1_field_values` 都改为委托。
2. **路径拼法保持原样**（`properties` 用 `.` 连接、`items` 追加 `[]`），新可见的关键字各有约定：
   map 形状（`additionalProperties`/`patternProperties`）贡献一段 `.*`，
   `constraining` 关键字（`oneOf`/`anyOf`/`allOf`/`not`/`if`/`then`/`else`/`propertyNames`）**保持当前路径**，
   `$defs`/`definitions`/`dependentSchemas` 用名字连接。
3. **两条路到同一个键要合并，不许覆盖**：一次静默丢值的走法正是本条要终结的缺陷。
4. **完整性由形状证明，不由计数证明**：一张合成 schema 在每个能带 subschema 的关键字下各放一个 enum，
   断言**路径集合恰好等于**预期（双向）。计数会随"任何地方多一个 enum"而变，形状只在这个关键字
   停止被下降时变红——后者才是要守的那件事。

### 没有改变什么

- **没有改任何 schema 文件、没有改任何对外输出、没有改退出码**：改的是**读者**，不是**契约**。
- **fixture 覆盖规则仍然只覆盖 `search-response`**：§97 拒绝拓宽的理由（别的 schema 会产生大量假 †）
  没有被本条推翻；本条让"要不要拓宽"这个决定在**信息完整**的前提下重新可做。
- 取值表的 30 行一条没动——它本来就完整（人工读过 `$defs`），这一轮让**机器**也能看见那些行。

### 这一版仍然挡住的（诚实清单）

- **新可见关键字的路径拼法（`operations.*.operation_kind`、`exposure.variables`）是这一轮引入的约定**，
  今天没有第二个消费者，所以它**不是契约**；将来谁要按路径引用它们，得先把约定写下来。
- **`enum_value_sets` 把路径丢掉了**：两个不同字段共用同一套词汇（`management` 出现在三处）在表里
  是**一行**。这正是取值表需要的（一处解释、多处引用），代价是它**查不出**"只documented 了一次、
  却用在三处"。
- **走法只认 `enum`，不认 `const`**：`status: {"const": "failed"}` 这样的字段**永远**进不了这个问题
  （`error-response` 就是，§102 的处理是把它放进取值表的 EXEMPT 一节人工说明）。
- **走法不跟 `$ref`**：它问的是"**这个文件里**声明了哪些 enum"。`where-response.health` 是
  `{"$ref": "common.schema.json#/$defs/health"}`，所以在 `where-response` 这一侧**看不到**那 5 个取值——
  完整性靠"每个文件各自被走一遍"的**并集**成立，而 `common` 自己在受检名单里。

## ADR-0031：同一个走法，两个问题——"文件自己声明的"与"文档能携带的"

**状态：已裁决。** 本条处理 §104.7-4 记下的那条边界：走法**不跟 `$ref`**，于是取值表的覆盖
**依赖"每个文件各走一遍"的并集**，而"`common` 在受检名单里"这件事**没有守卫**。

### 实测（不是推断）

给共享走法加一个"跟 `$ref`"的模式，逐 schema 量两种口径的差：

| schema | 自己声明 | 跟 `$ref` 后 | 差在哪 |
|---|---:|---:|---|
| `where-response` | 1 | **9** | `health`/`scope`/`zone`/`management`/… 全部来自 `common` |
| `plan` | 4 | **10** | `target.platform`/`architecture`/`operations[].target_scope` |
| `managed-tool-instance` | **0** | **10** | 它自己一个 enum 都没有，却**能携带** 10 套 |
| `registry-projection` | 4 | **11** | `instances[].lifecycle_status`/`health`/`bindings[].exposure` |
| `search-response` | 5 | **6** | `data.results[].management` |
| `common` | 18 | 22 | 它自己内部的本地 `$ref` |

两条结论：

1. **受检 schema 的可达词汇今天全部有记录**（0 条漏）——所以"把判据换成可达口径"**不会引入假 †**，
   却让覆盖**不再依赖名单**：即便 `common` 哪天被移出受检名单，`where-response` 仍会替它要 `health`
   那一行。
2. **`search-response` 会从 5 变 6**（多出 `management`）。它是 **fixture 覆盖规则**用的 schema——
   所以"跟不跟 `$ref`"不是实现细节，而是**换一个问题**：那条规则只能要求"文档自己的生产者能产出的
   值"，把继承来的共享词汇也塞进去，正是 §97 拒绝过的假 † 堆。

### 裁定

1. **一个走法，两种问法，用参数说明是哪一种**：`enums_by_path(schema)` = **这个文件自己声明了哪些**；
   `enums_by_path(schema, resolve=...)` = **这个文档能携带哪些**。不是两个走法（§104 刚合并掉两个），
   而是**同一个走法的两种用途**，而用途必须写在调用处。
2. **解析器回答一对值**：`resolve(ref, document) -> (target, target_document) | None`。第二半是**目标
   所在的文件**，因为被引用文件里的**本地** `$ref`（`common` 的 18 → 22 就靠它）要能在那个文件里解析；
   同一条引号在同一路径上出现两次即停（环不递归）。
3. **取值表的覆盖判据改成"可达"口径，并覆盖每一个已发布 schema**（不再分受检/豁免）：任何一条
   可达词汇要么有行，要么在 `UNDOCUMENTED_BY_DESIGN` 里**被点名**——那个集合**双向**断言，
   所以第五条不可能悄悄出现，过期的一条也不可能留着。今天被点名的恰好四条，全部来自 `broker-*`。
4. **fixture 覆盖规则保持"自己声明"口径**，并且把理由写在代码里（只有文档自己的词汇才由它的生产者
   控制；继承来的共享词汇会产生假 †）。
5. **删掉 ADR-0030 引入的 `enum_value_sets`**：给一条没被记录的词汇**点名**需要**路径**，
   而值集合说不出它来自哪个字段。走法仍然只有一个。
6. **新增目录级判据**：每一个已发布 schema 写出的 `$ref` 都要在**集合内**解析得到——守卫用的解析器
   是**故意全函数**的（解析不到就返回 None，那支什么都不贡献），所以"哪些引用答不上来"必须由别处
   量，而不是让它崩。

### 没有改变什么

- **没有改任何 schema 文件、没有改任何对外输出、没有改退出码**：改的还是**读者**。
- **取值表的 30 行一条没动**：它本来就是完整的（人工读过 `$defs`）；这一轮让**机器**也能证明这件事，
  并且不再依赖"哪个 schema 在哪个名单里"。
- **fixture 覆盖规则的范围没变**（仍然只有 `search-response`）：§97 拒绝拓宽的理由没有被推翻。

### 这一版仍然挡住的（诚实清单）

- **`managed-tool-instance` 的豁免理由原先是错的**：它写"这个文件里没有枚举字段"，那对**文件**为真、
  对**文档**为假（可达 10 套）。§105 改成了测出来的说法。**类似的"理由说的是文件、表问的是文档"
  的混淆，只有这一处被量过**。
- **`broker-*` 的 4 套词汇仍然不记录**（P2 未实现，没有读者会被交给这些值）；它们是**点名豁免**，
  不是"没人发现"。
- **可达口径用的是"引用方"的路径**，所以同一套词汇会在许多路径下重复出现。覆盖问题是"值集合有没有
  记录"，路径只用来**给豁免命名**——所以这里不存在"同一事实两处记录"的问题，但也意味着
  **判据说不出"某个共享词汇只被一个 schema 用到"**。
- **走法仍然只认 `enum`，不认 `const`**（`error-response.status` 是唯一一处，§102 用 EXEMPT 的散文处理）。
- **跨文件的 `$ref` 只解析"集合内"的文件**：指向集合外的引用不会报错，只会**什么都不贡献**——
  正是因此才新增了目录级判据（实测目前一个都没有）。

## ADR-0032：`const` 也是词汇，但 `if` 里的不是；版本钉由别的判据守

**状态：已裁决。** 本条关掉 §105.8 留下的最后一个洞：走法只认 `enum`，而**一个 `const` 就是一套
只有唯一取值的词汇**。

### 实测（不是推断）

把 20 个已发布 schema 里的 `const` 全走一遍（跟 `$ref`）：

| 类 | 数 | 例子 / 说明 |
|---|---:|---|
| 可达的 `const` 总数 | **41** | |
| 值位置（`properties`/`items`/`then`/…） | **33** | 真的是文档携带的值 |
| 测试位置（`if` 之下） | **8** | `binding.scope = "project"` 的含义是"**当** scope 是 project 时 `project_id` 必填"，不是"scope 恒为 project" |
| 其中版本钉（`schema_version`/`protocol_version`/`schemaVersion` = `1`） | **22** | 同一个事实出现在 19 份文档里 |
| 有意义的唯一取值（去掉版本钉） | **11** | `error-response.status="failed"`、`search-response.operation="search"`、`data.fallback.kind="crawl"`、`plan.canonicalization`、`reference-plan.operation`/`target.management`、`managed-tool-instance.kind`、`runtime-instance.kind`、`gc-plan.items[].reason`/`requires_approval` |
| 这 11 条里**表里一行都没有的** | **10** | 只有 `error-response.status` 在一句豁免理由里被提过 |

**顺手量出两个别的缺陷**：

1. **取值表的取值正则看不见连字符**（`[A-Za-z0-9_.]`）。于是 `jcs-rfc8785-compatible`（§106 加的那一行）
   被解析成**空取值**——三条守卫（值对齐、† 是否过期、取值是否在 schema 里）**同时安静了**。
   第四次同一类：**读的人比被读的文档窄**（§104/§105 也是这个形状）。
2. **`runtime-instance` 这一版根本没有构造者**（`managed-tool-instance` 有）。所以它的 `kind` 与
   `runtime_family` 一样是"P5 才有写者"——守卫 `test_no_row_claims_a_writer_for_a_document_this_build_never_builds`
   当场把它纠了出来（第一版那一行把 `registry/entities.py` 记成写者，是错的）。

### 裁定

1. **走法多一个问法**：`vocabularies_by_path` = `enum` **加上值位置的 `const`**，
   **测试位置（`if` 之下）一律不算**。不是第二个走法：同一个 `_walk`，`consts=True/False` 与
   §105 的 `resolve` 参数并列。
2. **fixture 覆盖规则仍然只数 `enum`**：fixture 证明的是**枚举的成员**，而一个 `const` 只有一个取值、
   没有"成员"可证。三个问法、一个走法。
3. **版本钉是一类，不是一个值**：凡字段名在 `VERSION_PIN_FIELDS` 里的，按"由 README 规则 1 与
   `test_l1_schema_catalog` 守着"处理，不写表行——一份 22 次的同一件事写成 22 行是噪声。
   守卫**同时断言这类字段的值就是 `1`**，所以"叫 `schema_version` 但不是版本钉"的字段不能混进来。
4. **10 条有意义的 `const` 进表**：`managed-tool-instance` 与 `error-response` **从豁免变成有自己的一节**
   （它们各自有一个 `const` 字段），豁免表缩到 3 个（两个 `broker-*` + `root-marker`）。
5. **取值的唯一拼法**：`schema_walk.spell` 是唯一定义（`null` 不是 `None`，布尔写 JSON 的 `true`/`false`），
   表这边的 `spell` 也改成调它——`const` 让布尔第一次真的出现在表里。
6. **取值正则放宽到连字符**，并把理由写进注释。

### 没有改变什么

- **没有改任何 schema 文件、没有改任何对外输出、没有改退出码**：改的还是**读者**与**表**。
- **`enum` 那部分一条没动**：`enums_by_path` 的行为与 §105 完全一致（`vocabularies_by_path` 是它的超集）。

### 这一版仍然挡住的（诚实清单）

- **`if` 的排除是按关键字，不是按语义**：今天 `if` 之下没有任何"文档携带的值"（量的就是这个），
  但这条判据说不出"某个 `if` 里的 `const` 其实是取值"——那种写法需要人来判。
- **`then`/`else`/`not` 一律算约束**：`not` 之下今天只有 `propertyNames` 的黑名单（本来就有行），
  但如果有人写 `not: {const: ...}` 表示"禁止这个值"，它会被当成一套词汇——**这条没量过**。
- **一个节点同时有 `enum` 与 `const` 是自相矛盾的**，走法取 `enum`；**没有任何判据拦这种 schema**
  （目录守卫只查 meta-schema 合法性与版本钉）。
- **取值正则仍然只认 `[A-Za-z0-9_.-]`**：带 `:`、`/`、`%` 的取值（例如 digest、URL 片段）在表里
  **仍然看不见**——今天没有这样的行，但这条边界现在是写下来的，而不是靠没人写。
- **新增的两节（`managed-tool-instance`/`error-response`）让"受检 schema"从 15 变 17**：
  "有没有一节"这件事仍然是人定的，判据只能保证"定了之后两边一致"。

## ADR-0033：豁免的理由是**关于文档的断言**，所以由文档声明、由构建度量

**状态：已裁决。** 本条处理 §106.7-1 留下的那条边界（"豁免理由说的是文件、表问的是文档"），
并顺带修掉一处**悬空指针**。

### 实测（不是推断）

三条豁免理由逐条对**文档**量一遍（`_produced_schemas` 回答"这一版有没有函数构造它"，
走法回答"它自己有没有词汇"）：

| schema | 自己的 enum | 可达词汇 | 没记录的 | **这一版构造它吗** | 原来的理由 |
|---|---:|---:|---:|---|---|
| `broker-request` | 2 | 3 | 3 | **否** | "P2 未实现" ✅ 准确 |
| `broker-response` | 3 | 4 | 3 | **否** | "同上" ✅ 准确 |
| `root-marker` | 0 | 2 | 2（**两条都是版本钉**） | **是** | **"同上" —— 假的** |

两件事因此成立：

1. **`root-marker` 的理由是错的。** 它那一行写"同上"，即按两个 `broker-*` 归到"P2 未实现"——
   而这一版**真的会写出根标记文件**（它是十三个被构造的 schema 之一）。它被列在豁免表里的真正
   原因是**它自己没有词汇**：两个字段都是版本钉。
2. **`root-marker` 那一行还指向一段不存在的东西**（"见取值表末尾那段"）——§106 写那句话时，
   版本钉这一类只存在于**测试模块**里，表里并没有那一段。**文档指向了一段没人写的说明。**

### 裁定

1. **豁免表多一列**：`类别`（`unbuilt` / `no-own-vocabulary`），**由文档声明**。
2. **判据持有度量**：`_measured_exempt_class(name)` 从构建本身推出这一类（有没有构造者、自己有没有
   词汇），与文档声明的那一格**必须相等**；两类的成员集合由表驱动（不再是人手写的一份副本），
   且**两类都不能为空**（没人落的类别是读者遇到却用不上的词——延后类别早就是这条规矩）。
3. **把缺的那一段补上**：表尾现在写明"一套词汇不在表里"的**三种**合法理由（在别处有行 / 被点名豁免 /
   是版本钉），各自指向守着它的判据。悬空指针因此消失，而且它指向的内容**真的存在了**。
4. **`root-marker` 的理由改成测出来的事实**，并**明说原先那句是错的**（这是 §107 的产出之一）。

### 没有改变什么

- **没有改任何 schema、任何对外输出、任何退出码、任何表行**：改的是**豁免表的一列理由**与**表尾那段**。
- **`UNDOCUMENTED_BY_DESIGN`（§105）与 `VERSION_PIN_FIELDS`（§106）都没动**：本条让"豁免的理由"
  与它们对齐，而不是替换它们。

### 这一版仍然挡住的（诚实清单）

- **"在哪一节有行"仍然是人定的。** 判据能保证"定了之后两边一致"（受检/豁免恰好覆盖 20 个 schema），
  但**说不出**某个 schema 该不该有自己的一节——`managed-tool-instance` 在 §106 因为一个 `const`
  升级成一节，这个判断是人做的。
- **`unbuilt` 是"这一版没有构造者"，不是"永远不会有"。** 判据按 AST 找构造者，所以 P2 把 broker
  写出来之后，这一格会**自动**变红（那时它该有一节或该被点名），但**没有人会被提醒去改文档的散文**——
  红的是表，不是那段话。
- **表尾那三种理由的**措辞**没有被判据读**：判据读的是表格里的类别那一格，散文仍然可能写得比它宽
  （例如把"版本钉"写成"约定俗成"）。这一条与 §104–§106 一直处理的东西同类：**能被机器读的部分
  越来越小，剩下的靠人**。
- **"有没有别人也在指向不存在的东西"没有查。** 这一轮只修了 `root-marker` 那一处指针；
  `references/` 与两份大文档里还有大量"见 §X"式引用，**没有任何判据在查它们**（§103.7-4 记过同类）。

## 尚未决策（本日志自己的一份清单）

**§86 更正了标题。** 它原来写的是"（仍属规划 §23 的未冻结项）"——**那句话从来不是真的**：规划 §23
那份是六项，这下面是十二项，两边只有三项重合。两份清单被当成一份用了很久，代价是规划独有的
三项（capability 清单、Everything、第一个真实 artifact）**在这里一个字都没有**，而它们
同样被 ADR-0025 裁决了。§86 把四项补进来，并给规划 §23 的六项各标了裁决位置。

以下 P1 明确**没有**自行发明算法或语义，需要单独决策：

> **§82 复核（ADR-0025）：这八项**（当时是八项；§86 补到十二项）**一个都没变，但它们的性质变了。** ADR-0025 的
> D8-1/D8-2/D8-3/D5/D4/D6/D3/D1 **逐条裁决：维持现状**——也就是说，下面每一条
> 从"**还没想过**"变成了"**决定不做，等某个具体的东西**"。这不是语义变化（一字未改），
> 而是**可读性**变化：读者不必再猜这些是遗漏还是选择。每条的裁决位置标在括号里。

> **§86 复核：补上规划 §23 独有、而这里漏掉的那几项。** 判决都在 ADR-0025 里，只是这份清单
> 没收它们：`root adopt` 的切换规则（**D2**）、Everything（**D7**）、第一个真实 artifact 与
> runner（**D8-4**）、冻结能力清单本身（**D9**）。补进来之后，**ADR-0025 的九条决定每一条都能
> 从"还没决定"的清单里找到入口**——这一条现在是守卫（第二十八组）。

1. `machine_id`、`session_id`、`project_id` 的生成算法——P1 只接受**注入**或显式入参，
   不自动推导；（**D8-1**：也不由硬件指纹推导，理由是教义 4）
2. registry SQLite migration 工具、event 保留期、`logs/audit` 导出协议；（**D8-3**：
   不自动迁移、不自动裁剪、导出只读）
3. 根定位的卷标扫描（P1 只支持 `--root` 与 `AIROOT_HOME`，且失败即关闭）；（**D8-2**：
   不猜"最像的那个卷"）
4. 受保护 issuer 的真实签名（P1 只有 `test_hmac_sha256`，`ed25519` 校验显式未实现）；
   **已升级成一条独立的裁决项并已裁决，见 ADR-0024（状态：已裁决：A 维持现状）与其裁决
   `## ADR-0025` 的 D1**——它挡住的不只是签名算法，而是
   `approve`/`install`/`env persist`/`tool gc --apply`/`uninstall` 这五条路径在真机上的完成；
5. `exposure\bin` 中的 launcher（P1 的 `EXPOSED` 是"通过一次全新的 registry 读取观察到
   新 binding"，尚未写任何 launcher 文件）；（**D4**：形状已定，P1 仍不写文件）
6. Native Search 的进程模型与 `file_search` capability 清单；（**D6**：形状已定，常驻索引器属 P2）
7. `.ai/tooling.json` 的**写入**通道——ADR-0004 §12.2 的"记住这次选择"目前只有读取路径；
   写入被刻意留给 P2 的 human approval 通道，否则等于给 Agent 一条绕过确认的路；（**D3**：维持只读）
8. machine 级环境变量持久化（HKLM + 广播 + 新进程读回校验）——依赖 P2 的受保护 broker；
   `env persist --scope machine` 现在明确报 `PRIVILEGE_REQUIRED`（5），不假装成功。（**D5**：同上）
9. `root adopt` 的 copy/verify/switch 规则——规划 §15.5 只点名了**校验**，没规定 copy 与切换；
   §8.1.1 把 adopt 与"恢复备份"并列为两种处置。（**D2**：源目录不动、先验后切、只有一个原子
   切换点、adopt 自己不写机器 PATH；剩下的是切换本身，那会动 active root 指针，属 P2）
10. Everything 是基准还是 adapter——一个第三方常驻服务进不进信任基。（**D7**：只做基准。
    把基准当依赖，等于让"性能看起来差不多"变成"语义也差不多"）
11. 第一个真实 artifact、Windows runner 与故障注入夹具。（**D8-4**：`build`（cmake portable zip）
    是第一个，因为它是冻结清单里唯一有已注册可信来源的能力；runner 仍是 `cli/bin/airoot.cmd`；
    故障注入只用本地 fixture）
12. v1 进入核心的 capability 清单本身。（**D9**：取 `cap-2`，七个能力；清单里只有 `build` 有来源条目，
    其余六个只有 reference/import 一条路——这不是缺陷，是事实）

---

## ADR-0034：P2 从客户端那一半开始——线路面先行，受保护状态仍等语言切换

**背景**：P2 是「Windows Protected State」，规划把它的第一件事定成 ADR-0001 的语言切换（用 AIROOT
自己的 plan/approval/fetch/verify/digest 规范取 Rust 工具链）。动手前先量了一次现状，量出四件事：

1. `broker-request` / `broker-response` 是 ADR-0026 记下的「五个没有任何写者」中的两个：**没有任何代码
   构造或校验它们**，golden 语料里一条 broker 文档都没有（当时 36 个 fixture 实测）。一份**没有任何
   读者**的契约，它的缺陷没人会碰到——这正是第 3 条能一直存活的原因；
2. 客户端那一半**不需要提权**：一个进程永远可以打开**自己**的 token。所以"给这两个 schema 一个使用者"
   可以今天做完、今天测完，而且不碰宿主机；
3. 两个已发布 schema 对 protected 模式的 `enforcement` **互相矛盾**：`broker-response` 写的是
   `acl_and_broker`，而 `common.$defs.enforcement`——以及 `$ref` 它的另外三份 schema、以及本 build 到处
   打印的值（`cli.py`、`ext/envelope.py`、`caps/doctor.py`）——写的是 `acl_enforced`。全树实测：
   `acl_and_broker` **只出现在那一行 schema 里**，没有任何文档、fixture 或代码含它；
4. 设计文档自己的示例信封过不了自己的 schema：`docs/broker/` 用 `plan` / `approval` 两个键，而
   `broker-request` 要求 `plan_ref` / `approval_ref` 且 `additionalProperties: false`。

**决策**：P2 的第一阶段只做**线路面的客户端一半**，交付三样东西：

- `caps/identity.py`：只读本进程 token 得出 `sid` / `pid` / `integrity` / `elevated`
  （`ctypes` 读 `TOKEN_USER` / `TOKEN_INTEGRITY_LEVEL` / `TOKEN_ELEVATION`），**失败即数据、绝不抛异常**；
  它**只回答"我是谁"**，不判断"我够不够格"——那是 broker 的事；
- `broker/protocol.py`：`build_request` 造一份 `broker-request`，返回前先 `validate_document` 再
  `validate_self`；`parse_response` 校验 `broker-response`，**只在 `status=ok` 时返回**；
  `broker_unavailable()` 是"这一版没有 broker"的诚实回答（`NOT_IMPLEMENTED`(1)，**ADR-0025 的 D1**）；
- 它**不含**：任何传输（named pipe 不存在）、任何服务器、任何对**别人** token 的校验、任何新动词。
  「broker 不信任客户端」这件事本身**没有被证明**——这一阶段只把两个 schema 从"没有使用者"变成
  "有使用者"，仅此而已。

**附带一个必然结果**：`broker-request` 因此成为**发出去而不是打印出来**的文档，而"自校验的集合 ⟺ 语料
的集合"是守卫第三十二组的等式，所以给它一个写者就等于要求它有一份**逐字节验收面**：
`fixtures/golden/broker_request_commit_plan.json`（`client` 块是合成的——public 仓库里不放本机 SID）。

**代码放在哪（对 §E3 的精确化）**：`docs/broker/` 只给了进程名 `airoot-elevated`，没说仓库位置。这里的
区分按**信任方向**：**客户端**那一半本来就属于那个"不被信任、用户可写"的包，所以它住
`cli/app/airoot/broker/`；**服务器**那一半是受保护二进制，**不能**住这里，位置留到 ADR-0001 的语言切换。

**四条连带处置**（都属于「改一处必须改三处」）：

1. **原地修 schema**：`broker-response` 的 `security_mode` / `enforcement` 改为 `$ref` `common` 的定义。
   依据是 **ADR-0003 的先例**——这是一处**转录缺陷**（手抄一份共享定义，抄错了一个成员），不改变任何
   既有成员的含义、不使任何 fixture 失效、没有写者会因此受影响（本来就没有写者）。判据不是"看起来
   像"：`acl_and_broker` 在全树实测为零；
2. **加一条守卫**（`test_l1_schema_catalog.py`）：凡声明 `security_mode` / `enforcement` 的 schema，取值
   必须等于共享定义，且两个字段必须**成对**出现。**更宽的规则被量过并否决**："同一个字段名出现在多份
   schema 里就必须同值"会红五处，其中四处是**正当的**（`management` / `scope` / `source` /
   `target_scope` 在不同文档里是不同的概念）——会因为正当理由变红的检查是噪音；
3. **schema README 记下已知不对称**：`gc_apply` 在 schema 的 `allOf` 里没有任何条件要求，所以"既无
   `plan_ref` 也无 `approval_ref` 的 `gc_apply`"是**合法**的，而进程内同名动作（`caps/lifecycle.py` 的
   `apply_gc_plan`）两样都要。本层**拒绝**它（`INVALID_INPUT`(8)），于是它比已发布契约**更严，而不是
   不同**（它造的每份文档仍然合法）。给 `gc_apply` 补 `allOf` 分支等于把可选变必填，按 README 规则 2
   要**新 schema id**；等第二个消费方出现再花这个 id；
4. **改两处过期或自相矛盾的散文**：`docs/broker/` 的示例键名照 schema 改正，并写明"字段名的权威是
   schema"；诊断码表里"`PRIVILEGE_REQUIRED` / `ACL_MISMATCH` 在 P1 不会被发射"这**半句**是错的
   （`env persist --scope machine` 正在发射前者），改为只对 `ACL_MISMATCH` 成立，并把"哪些码发不出来"
   的权威指回 `references/reason-codes.md`——重复一份已被守卫钉死的事实，就是它出错的方式。

**取值表随之发生的三件事**（`references/field-values.md`，由 `test_l1_field_values.py` 双向钉死）：
`broker-request` 因为**第一次有了构造者**而离开豁免表、拿到自己的两行；`broker-response.enforcement`
因为改成 `$ref` 而不再需要豁免（`common` 那一节有行）；`UNDOCUMENTED_BY_DESIGN` 只剩
`broker-response.status` 一条。**顺带钉住一个语义**：这份表的"构造者"是字面意思，**一个读者不算写者**
——所以 `broker-response` 有了 `parse_response` 之后仍属 `unbuilt`；下一阶段把进程内 loopback harness
放进 `cli/tests/`（它该在那里）时，那份 harness **也不会**让任何 schema 变成"已构造"，因为测试替身不是
产品文档的写者。

**`parse_response` 的失败语义**（三种，都是判据）：响应过不了 `broker-response` → `INVALID_INPUT`(8)
（**收到的文档对我们来说是非法输入**），证据是 schema 的报错；`status != "ok"` 且带**已注册**的
`reason_code` → **抛那个码**，把对方的裁决与退出码原样带回来；`status != "ok"` 但码缺失/未注册、或
**退出码为 0**（`SUCCESS`、`POLICY_ONLY_MODE`）→ `INVALID_INPUT`(8)。最后一条是刻意的：把一个"被拒绝"
折成退出码 0，正是 ADR-0027 记下的那个缺陷。另外响应里的 `evidence` 是**对象**（`common.$defs.evidence`），
而错误信封的 `evidence` 是**字符串**，所以渲染时必须转换——照抄现成的错误信封当 broker 响应会校验不过。

**记下一个不修的洞**：`requested_at` 在 schema 里是 `format: date-time`，而 `schema_io` 没有配 format
checker，所以它**不被检查**（JSON Schema 规范里 `format` 默认只是注解）。本层不手写时间戳正则——那会把
schema 的权威挪进代码；这是**全树**决定（要不要给 `schema_io` 加 format checker），不是这一层的。

**这一阶段明确不能证明什么**（对着设计自己的话逐条列，见 `_p2_brief.md` 的 F 段）：ACL 强制性、IPC 与
named pipe impersonation、**对客户端 token 的校验**、UAC 取消不破坏旧 generation、审批链（ADR-0025 的
D1 仍无生产签发方）、受保护启动与 `recovery_required`、machine PATH / launcher / machine 级环境变量，
以及最要紧的一条——**broker 不信任客户端**。

---

## ADR-0035：回滚只撤销自己那一次激活，并发提交的赢家不受影响

**背景**：一次排查"测试偶发红灯"的例行工作，把一个**真实的产品缺陷**翻了出来。
`test_concurrent_commits_keep_a_single_active_binding` 单跑 60 次红 1 次（隔离临时目录、只跑这一个
用例实测）。确定性复现后机制很清楚：让提交 A 在 `ACTIVE_BOUND` / `EXPOSED` 之间被打断，让提交 B
（同 key、不同版本）完整提交，再 `repair` A——结果是 **A=ROLLED_BACK、B=FINALIZED、活动绑定数为 0**：
**A 的回滚把 B 已经提交的绑定一起拿掉了。**

三行代码里有两个缺陷，而这两行在**两个 runner 里各写了一遍**（`tx/simulate.py` 与 `tx/artifact.py`），
互相之间从来没有对照读过：

1. **回滚停用是"整把 key"级的**：`registry.clear_active_binding(key)` 停用该 key 的**每一行**活动绑定，
   不只是本事务装上的那一行；
2. **恢复用的是错的 generation**：`tx["generation_before"]` 是 `journal.create` 记下的**注册表全局**
   generation（`journal.py:134`），不是这把 key 上一行的 generation。只要中间有别的 key 提交过一次，
   `(key, generation_before)` 就**指不到任何一行**，回滚会**静默地什么都没恢复**。

两处都不是"边界情况"：第 1 条摧毁另一个合法提交的结果，第 2 条让回滚悄悄失效——而它们的形状都是
"**同一个事实在两处各写一遍**"（ADR-0025 教义 1 早就点过这一类）。

**决策**（回滚的语义，写成一个共享实现 `tx/rollback.py`，两个 runner 都调它）：

1. 该 key **没有**活动行 → 这里没有属于本事务的东西可撤（`retire` / `uninstall` 才是那个状态的主人）；
2. 活动行属于**别的实例** → 那是**在我们之后**合法提交的事务；本事务如实报告自己的失败，
   **不碰赢家的绑定**；
3. 活动行**是本事务的** → **只**停用**本事务自己那一行**，再重新激活该 key 中 **generation 严格小于
   本行且最大**的那一行（即被本事务顶掉的那一次绑定）；如果不存在，那么"这个 key 没有活动绑定"
   就是诚实的结果，因为本事务之前本来就没有。

`generation_before` **保留原意**（journal、`repair` 报错与 `cli.py` 都在读它），只是**不再**被用来
辨认"被顶掉的那一行"——那一行从 `bindings` 表本身推出来。

**依据**：§5.6 的冻结规则本来就写着"**回滚只切 binding**"，§5.5 写着"同一 binding key **只有一个**
active implementation"。验证方案 T-010（`:206`）说 DB 锁"guarantee ONE commit, the other retries or
exits"——**输家回滚是允许的，赢家的绑定被摧毁不是**。所以这不是新语义，是既有契约没被实现。

**后果与如实记录的边界**：

- **规则 3 有一个已知的弱处，明写在这里而不是装作没有**：它重新激活的是"本行之下最大的那一行"，
  而**不一定**是"本事务开始时处于活动的那一行"。因此一个在本事务提交前被**故意**停用的前任
  （例如 `retire` 过）会被回滚重新激活。要记下**确切**被顶掉的那一行，需要 `transaction.schema.json`
  里有一个今天没有的字段——那是**契约变更，不是缺陷修复**，所以不做；
- **那条已有的并发守卫不是这个性质的证明**：它单跑 60 次才红 1 次，也就是说它一直**靠时序**才绿。
  修完之后另加**确定性**用例（驱动那个交错：A 在 `ACTIVE_BOUND` 被打断 → B 完整提交 → `repair` A，
  断言恰好一个活动绑定且是 B 的；再一条两个 key 的交错，专门覆盖第 2 个缺陷）；
- **同一批工作里还有两处测试卫生问题**（`cli/tests/test_cli_search.py` 的固定路径数据根被跨用例复用、
  遗留文件让绝对计数失效）。它们**不是产品缺陷**，是"会因为正当理由变红的检查"的反面——
  会**无缘无故**变红的检查。一并修掉，并说明它们是偶发红灯的另一半来源。


---

## ADR-0036：`broker-response` 的 `status` 由 `reason_code` 推出，而"拒绝"与"失败"的边界是契约

**背景**：P2 第二阶段给了 `broker-response` 第一个生产者（`cli/tests/fake_broker.py`，进程内
loopback harness，§110）。响应 schema 的 `status` 是四值枚举（`ok` / `rejected` / `failed` /
`recovery_required`），而 `reason_code` 只是形状（`^[A-Z][A-Z0-9_]{2,63}$`，**不枚举**）——
**没有任何已发布的东西说哪个码配哪个 status**。缺了这条规则，两个 broker 会对同一次失败给出不同的
`status`，而调用方的分支（重试 / 先 repair / 放弃）完全建立在它上面。

**决策**：

1. `status` **只由 `reason_code` 推出**，只用一个函数（`_status_class`），成功路径与拒绝路径共用它。
   "同一次失败按两条规则分类"是缺陷的温床。
2. **退出码 0 的码 → `ok`**（`SUCCESS` 与其余信息性码）。把成功报成失败是最响的假话。
3. **`RECOVERY_REQUIRED` → `recovery_required`，点名的，不由退出码推出**：退出码 **6** 同时承载
   `JOURNAL_TRUNCATED`（"日志读不出来，什么都不该继续"），那是**失败**而不是可恢复状态。按退出码推
   status 会把这两件事混为一谈。
4. 其余按"**裁定**还是**出错**"分：操作**开跑之前**下的判决（缺失、越权、来源、凭证、路径、不支持的
   实现）→ `rejected`；**开跑之后**坏掉的（摘要漂移、载荷不见、实例冲突、日志被截断）→ `failed`。
5. 这条边界存在**一处被记录下来的例外**：第一版把 `INSTANCE_CONFLICT` 放在"拒绝"集合里，但它由
   `tx/simulate.py` 的 `_fail` 在**操作已经开始之后**发射（store 路径被比较过，里面是别的字节），
   按第 4 条它是 `failed`。已移出集合，并把理由写在集合旁边——**规则与表不一致**正是本项目反复量的
   那类缺陷。

**后果**：这条映射成为 Rust broker 必须逐字复现的契约（调用方按 `status` 决定重试、恢复还是放弃）。
ADR-0034 记下的形状陷阱依旧成立：响应的 `evidence` 是**对象**，错误信封的 `evidence` 是**字符串**。
**未定**：`status` 之外只有 `retryable` 一个布尔承载"能不能重试"（`PENDING_TRANSACTION` /
`STALE_GENERATION` / `RECOVERY_REQUIRED` 为真），更细的重试语义**不提前发明**；`probe_root` 与
`gc_apply` 没有事务行，所以它们的 `transaction_id`/`state` 恒为 `null`——编一个 id 等于替没有写过的
历史作证。


---

## ADR-0037：路径声明按**规范拼写**比较——`resolve()` 是唯一该动手的那一侧

**背景**：§110 收尾时把推送后的仓库克隆到两处跑全量：`D:\` 下 980 passed / 0 failed，
`%TEMP%` 下（本会话拼作 `C:\Users\PROFIL~1\…`）**4 failed**，而且红的名字在两次运行之间还会变。
机制查清后是两个不同的问题：

* **(a) 测试健壮性**：每个产品写入者都 `resolve()`（`paths.canonicalize`），爬取也 `resolve()`
  （`caps/search.py` 的 `_canonical_root`），而测试夹具交出的是**原始**拼写——同一个目录因此有两种
  拼写，断言只在"临时根含 8.3 短名"的位置上才红。pytest 9 的文本 diff 把**右**操作数标 `-`，这一点还
  额外让现场看起来像"一个对象两种拼写"。
* **(b) 一处真实的产品内部不一致**：`search explain` 用**未解析**的注册表根问"索引覆盖了吗"，而它的兄弟
  `search` 用解析后的根问同一个问题——于是 `explain` 可以预测"实时遍历"（退出码 2）而 `search` 从索引
  回答（退出码 0）。今天是**潜伏**的（CLI 的每个写入者都规范化），但两个命令对同一个索引说法不同，是
  谁被相信谁就错的那种 bug。

**决策**：

1. **路径声明在比较前规范化一次**（`resolve()`）：分类器对注册表里每条声明做一次（`cli.py`
   `_claim_spelling`），`explain` 的覆盖问题走与 `search` 相同的 `resolve_roots`
   （`cli.py` `_canonical_data_roots`），`adopt` 认领数据根时的相等比较同样规范化。
2. **不改 `canonicalize()` 与 `_canonical_root()`**：长形式才是正确的规范形式（`resolve()` 把 8.3 别名
   展开成长名）。要改的是**比较的另一侧**，不是规范形式本身。
3. **代价与结果成比例**：分类器的规范化是"每注册表行一次"，不是"每搜索结果一次"；解析不出来的根**保留
   原样**而不是丢弃——`explain` 是**预测**，偷偷少算几个根会成为第二种不一致。
4. **夹具交出的拼写 = 产品写出的拼写**：夹具给 `resolve()` 之后的值，而不是"会话临时目录碰巧长什么样"。
   这是测试健壮性，**不是**放宽断言——红的方向由两条**故意构造**的用例保证。

**后果**：`search` / `search explain` / `--managed-only` 对同一条注册表行给出同一个答案，不论那一行是哪种
拼写；套件不再依赖 checkout 所在路径的形状。**如实记录一条测量结论**：`\.` / `..` 这类拼写**不能**给
"覆盖问题"那一半验红（链路上某处把它们折叠了，实测两次），所以守卫改用**正斜杠**拼写——`resolve()` 会
规范化它，而 `caps/searchindex.covers` 只做小写与去尾部反斜杠。**`doctor` 不受影响**：它比较卷序列号与
观测到的版本/架构/入口点，不比较路径字符串。**未定**：broker 侧将来若也写注册表，是否同样规范化——
那是设计问题而不是测量问题，触发条件（出现第二个写入者）今天还不存在。


---

## ADR-0038：语料可以有两个来源，但一个 fixture 只能有一个出处

**背景**：§112 要给 `broker-response` 一份逐字节语料。问题是它与现有那条规则的关系：
`test_l0_consistency.py` 的"核心打印的文档 ⟺ 语料"是**精确相等**，而 `broker-response` 的**唯一**生产者是
`cli/tests/fake_broker.py`（§110 的进程内 harness，测试路径）。把它塞进 `SCHEMA_FOR_FIXTURE` 会让那条规则
两边都说谎（一个"核心从不打印"的文档混进"核心打印的集合"）；不塞进去，孤儿 fixture 判据又会把它当成
"没人检查的文件"。

**决策**：

1. **第二个 map**：`test_golden.py` 的 `SCHEMA_FOR_HARNESS_FIXTURE` 声明测试路径生产者造出来的语料，并由它
   自己的判据逐份过 schema；"核心打印 ⟺ 语料"那条**精确相等**的规则继续只管 `SCHEMA_FOR_FIXTURE`。
2. **孤儿判据认两个 map**：两个 map 都算"已知"，它拒绝的是**两个都不在**的文件。一个 fixture 因此只能
   有一个出处，而"这是谁造的"这件事在文件里是**读得出来**的。
3. **哪些响应可以进语料，是量出来的**：四个 operation 加一条拒绝在**两个独立根**上各造一次并比较——
   `gc_apply`、`refused_missing_plan`、`probe_root` 逐字节相同；`commit_plan` / `recover_transaction`
   只在 `transaction_id`/`approval_id` 上不同，而那是**可以钉死**的（`plan_id`、`nonce`、`approval_id`
   全都是入参）。所以前四个进语料，`probe_root` **不进**：它的答案是**机器观测**（ACL 条目数与 DACL 摘要），
   换一台机器就不同，fixture 要么嵌入某台机器的数字，要么撒谎。**"在这台机器上稳定"不是"可复现"**。
4. **顺带修掉一个真实的泄漏**：`probe_root` 的 `acl_trustees` 把排好序的受托者 SID 列表当 `detail` 发出去，
   而同一个函数的 docstring 写着"SID 不进证据"。返回的文档里带机器身份违反 AGENTS.md §9，也让它的答案
   永远不可能成为可复现语料。改成只报**条目数**；守卫是"整份答案里不出现 `S-1-`"。

**后果**：`broker-response` 有了四份逐字节答案（成功提交、中断后恢复、载荷回收、一次 `NOT_FOUND` 拒绝），
Rust broker 有了要复现的东西；`probe_root` 的缺席是**记录在案的决定**而不是遗漏。**一条施工教训也记下来**：
那段代码插在 `_build_documents` 里，第一版复用了 `plan`/`root`/`registry`/`clock` 这几个名字，把下面的
`plan_fake_tool` fixture **悄悄改写成 broker 的计划**——抓住它的不是任何文档判据，而是 golden 语料的
**逐字节再生检查**（`plan_fake_tool.json` 出现在 diff 里）。共享函数里插入的块必须用自己的名字。


---

## ADR-0039：批准签名真了，但**签发**仍然是那条边界

**背景**：P2 的第一批工作（§113）刻意只做「受保护边界需要用、但**不需要先有那个边界**就能建成并验证」的
三件事，本条记录其中两处改动了契约的决定。

* **真实 Ed25519 校验**（`airoot/crypto/ed25519.py`，RFC 8032，**纯 Python**）：不加依赖的代价是自己写曲线
  运算，收益是"来源证明"第一次有了可执行的判据；它的验收面是 RFC §7.1 的官方向量，不是"自己签自己验"。
* **读另一个进程的 token**（`caps/identity.py` 的 `probe_process`）：broker 要判"谁在问"，而在此之前这个
  仓库连**别人**的 SID 都读不出来。它是**观测**，不是授权——读到一个调用方是提权的，不等于它被允许。
* **ACL 的写一侧**（`caps/acl.py` 的 baseline/apply/verify/restore）：**只作为库**，不接任何动词、不接任何
  schema；没有 broker 的今天，接上去等于给同用户进程一条改 DACL 的路。

**决策一：keyring 的每一条是「记录」，不是裸材料。**

```json
{"keys": {"airoot-approver-1": {"algorithm": "ed25519", "public_key": "base64:…"}}}
```

理由是旧形状有一个真实的洞：**由 token 自己那个 `signature.algorithm` 字段决定跑哪种校验**，于是同一份注册
材料会被按 token 的声明**重新解释**（登记为 HMAC 的密钥被当成 Ed25519 公钥去用）。现在两边的算法必须一致，
否则 `INVALID_APPROVAL`(4)；两个方向各有测试。`load_keyring` 拒绝旧形状（报 `PROVENANCE_FAILED`）而不是
猜它的算法——本仓库没有任何已落盘的旧 keyring（实测：`state/test-keyring.json` 只在测试运行时写），所以
不需要迁移。

**决策二：`ed25519` 的"未实现"分支删掉，`PROVENANCE_FAILED` 只剩一种触发方式。**

签名不对 → `INVALID_APPROVAL`(4)（"你的 token 不对"）；**整个 root 没有 keyring** → `PROVENANCE_FAILED`(7)
且带 `ISSUER_PENDING`（"这里没有签发方"）。这两件事以前在两条消息里用同一句话表达，读起来像两个互不相干的
缺口；现在它们是两件事，各说各的。**这是本轮唯一被删掉的拒绝理由**，也是本文档必须说清的那件事。

**决策三（没变，因此更要说）：本 build 仍然签不出一份批准。**

核心永远不会有签名侧——那是"CLI 不能凭空制造同意"的实现方式。唯一的写者还是测试签发方，
`approve`/`install`/`env persist`/`tool gc --apply`/`uninstall` 五条路径在真机上**仍然**停在
`PROVENANCE_FAILED`。**"校验已实现"与"本机可以批准"是两件事**，任何把它们混起来的说法都是假的；
私钥的受保护存放（`caps/acl.py` 的写一侧 + broker）是下一阶段。

**决策四：keyring 的路径名照旧（`state/test-keyring.json`）。** 它唯一的**写者**仍是测试签发方，
而 ADR-0024/0025 与若干阶段记录都引用了这个名字；改名属于"给它一个生产写者"的那一阶段，到时候连同迁移
一起做。这一条写下来，是为了让后来的读者知道这个名字不是被忽略的。

**决策五：验证用的公钥必须由边界钉住，不能来自被检查的文档。** RFC 8032 **不**拒绝小阶公钥，所以在退化公钥下 `(R = [S]B, S)` 对**任意**消息都能通过——这不是实现的疏漏，是算法的边界，已由一条点名测试钉住（§113.2）。因此 `verify` 的强度上限等于它的 keyring：今天 keyring 是 root 里一个文件，**任何能写 root 的进程都能换掉它**。这正是受保护阶段要关的那道口，也是为什么本次**没有**把 keyring 或公钥挪到 token、调用方参数或任何被检查的文档里。

**后果与边界**：`probe_process` 仍然回答不了 `application_id`（application identity 得由 broker 自己确立，
而且 `client` 块本身仍然只是**自述**）；ACL 的写一侧在受保护边界存在之前**不得**接上调用者；真实私钥今天
没有任何被保护的家，所以"来源证明"这条路在真机上仍走不到底。


---

## ADR-0040：ACL 写一侧的两条边界——非空基线**保护式**写入，空基线**拒绝**

**背景**：§113 把 ACL 的写一侧（`caps/acl.py` 的 `acl_baseline`/`verify_baseline`/`apply_baseline`/
`restore_acl`）作为**库**建起来（不接任何动词、不接任何 schema），过程中量到两件必须在写之前定下的事。

**决策一：非空基线一律用 `PROTECTED_DACL_SECURITY_INFORMATION` 写。** 实测：不带这个标志时，一条显式 ACE
的基线在 `%TEMP%` 下被**存成 12 条**（1 条显式 + 从父目录活继承来的 11 条），于是 `verify_baseline` 永远
不可能返回"干净"——**不保护，基线根本落不下去**。代价写进 `apply_baseline` 的 docstring：继承来的 ACE 变成
显式存储（顺序与掩码不变，`INHERITED_ACE` 位消失），目录不再跟随父目录，既有的子对象保留它们已经继承到的
东西；第一次写会对每条 ACE 报一条差异，之后每次写都是干净的。

**决策二：空基线（`entries=()` 且 `dacl_present=true`）**拒绝**，作为 finding 而不是异常。** 理由是实测的，
而且它是本阶段最值得记的一条：**保护式空 DACL 是一扇单向门**。它确实能落下去（0 条、验证通过），随后
`CreateFileW(path, WRITE_DAC)` 返回 **`ERROR_ACCESS_DENIED`(5)**、`restore_acl` 被同样拒绝、目录既删不掉也
改不回去——**在提权 token 下也一样**，因为空 DACL 连所有者的隐式 `WRITE_DAC` 都拒。逃生口是"所有者 +
特权"（备份/还原语义），而**那正是本 build 没有的提权**（§5 第 1 条）。于是一次不经意的库调用会在调用者
身后关上一扇门，而 `restore_acl` 存在的意义恰恰是承诺"施加基线不是单向门"。**同一个函数里另一条被拒的
姿态**（`dacl_present=false`，NULL DACL）保持不变：这类东西本模块**报告**而不近似。

**这一决策的实物证据**：那 9 个测试目录（3 个目标 + 6 个探针）带着受保护的空 DACL 留在**被 gitignore 的**
`cli/tests/.tmp/<agent>/` 里，本进程**无法打开、删除或重新设 ACL**——它们就是"空基线不可回收"的现场；新的
运行不受影响（夹具的父目录名带每次运行的标签）。

**后果与边界（reviewer 必须先知道）**：**`SetSecurityInfo` 成功不等于调用者活下来**——一条没有匹配调用者
token 的 allow 的保护式 DACL，会让调用者自己再也打不开那个目录（实测：给 Everyone / BUILTIN\Users /
Authenticated Users 的 allow 足够；只给 SYSTEM 的 allow、或一条 deny 就不够）。"这条基线是否让调用者活着"
需要一个关于主体的策略，而那个策略**不在这一层**，属于 broker。本层只保证：畸形输入是 finding/`ValueError`、
写后可以读回、`restore_acl` 尽力还原并要求调用者自己核对；**一个本模块无法写的 ACE 类型（对象/回调 ACE，
真实数据根里会有）会让 apply 与 restore 都抛 `ValueError`**，也就是说这类目录的 ACL 本模块还原不了——已写在
docstring 里，没有在真机上测过。

## ADR-0041：边界要问的第一个问题是「谁在问」，而请求里的 `client` 块答不了它

**背景**：§113 打好了三块地基（Ed25519 校验、读**别人**的 token、ACL 写一侧），接着要建的是
`docs/broker` §3 要求的那件事：broker 校验客户端进程的 token、用户 SID、完整性级别与 application identity。
named pipe 与提权服务本身没法在它自己不存在时验证，但**那条校验的判定**可以——于是 §114 先把它写成库，顺手
量了一件必须记下来的事，并据此作了五条决策。

**量到的不对称（这条 ADR 的由来）**：`broker-request` 的 `client` 块有**四个**字段（`sid`、`pid`、
`integrity`、`application_id`）且 `additionalProperties: false`；而服务端对同一个调用方能读到的**事实**有
**九个**（`pid`、`sid`、`integrity`、`elevated`、`elevation_type`、`session_id`、`is_app_container`、
`app_container_sid`、`creation_time`）。两个结论：(1) 前三个字段是**冗余**的——服务端自己就能读，而且只能
信自己读的那一份；(2) `application_id` 在普通 Win32 进程的 token 里**没有对应物**——没有任何一个
`TOKEN_INFORMATION_CLASS` 回答"这是哪个程序"，只有 AppContainer 进程带得动一个 application identity
（`TokenAppContainerSid`）。所以 `docs/broker` §3 写的"校验 application identity"，对普通调用方**无从校验**：
真话是"这个事实在 token 里不存在"，不是"它和声明一致"。**一个必填字段，要么冗余、要么是自述，而文档没有
说它是哪一种**——这就是本条要裁决的事。

**决策一：判定只吃观测，`client` 块结构上进不来。** `broker/policy.py` 的 `admit_caller(identity,
expectation)` 的第一个参数是 `caps/identity.py` 的 `ProcessIdentity`——那个类型**没有** `application_id`
字段，函数签名里也没有任何"声明 / claim / request"参数。于是"读请求里的自述来决定"不是一条被禁止的写法，
而是一件**写不出来**的事（与 ADR-0030 / §104 同一个做法：完整性用形状证明，不靠散文禁令）。`client` 块因此
降级为**诊断信息**：它解释调用方以为自己是谁，不参与任何判定。测试钉住三件事：`ProcessIdentity` 的字段名里
没有 `application`；`admit_caller` 恰好两个参数且都不是 `client`/`claim`/`request`；一个**谎报**
`application_id`、并把 SID 说成允许集合里那一个的请求，在观测到的是别人时**照样被拒**。

**决策二：不知道就是不允许。** 判定要读的每个事实（`sid`、`integrity`、`elevated`、`elevation_type`、
`is_app_container`、`session_id`，以及给了预期创建时间时的 `creation_time`）只要有一个是 `None` 就拒绝，
并在证据里逐条点名缺了哪个。这不是新规则——"不编造确定性"与"失败即数据"在本仓库是既有的（`probe_process`
里 `elevated=False` 与 `elevated=None` 是两件事，正是为了这里）。它的直接后果：**这个 build 里一个事实也读
不到的调用方会被拒**，而这是对的——读不到 token 的进程可能是受保护的、可能已经退出、可能根本不存在，三种
情况都不该被读成"它没问题"。

**决策三：观测到是 AppContainer 的调用方一律拒绝。** 依据是 `docs/broker` §2 的表：能力扩展与 Install
Backend 是"用户或**受限**进程"，明确不能写 R、不能写 `store/tools/env/exposure/state`；而 AppContainer 正是
Windows 自己给"受限调用方"的名字。这是一条**不随参数变化的固定规则**，它的理由与被拒时的证据都写在模块的
`REFUSAL_REASONS` 表里，规则 id 与理由一一对应，由测试双向钉死（与 `protocol.py` 的
`ADDITIONAL_REQUIREMENT_REASONS` 同一个做法：**理由是可复核的数据，不是只靠"删掉它会红"来自卫的规则**）。

**决策四：新增 reason code `CALLER_NOT_AUTHORIZED`（退出码 5）。** 量过：冻结的码表里**没有一条**说的是
"谁在问"——`ACL_MISMATCH` 讲目录的描述符，`OWNERSHIP_REQUIRED` 讲 AIROOT 不拥有的 payload，
`PRIVILEGE_REQUIRED` 讲"这个操作要一个你没有的权限"，三条都不是对**调用方**的判断。落 **5** 是因为 0–9 里
"操作需要调用方不具备的权限/授权"就是这一层，**而不是**因为它与 `PRIVILEGE_REQUIRED` 同义：提权不会把
`S-1-5-21-…` 换成另一个 SID，所以"提权再试"**不是**它的下一步——这句话必须出现在证据里，也必须出现在
`references/reason-codes.md` 的退出码 5 一节里（ADR-0027 的同一条教训：一个码不得承诺提权能办到它办不到的
事）。五条规则共用一个码，用 `details.rule` 区分是哪一条，理由同 §101 的 `deferred_category`：调用方的下一步
对五条是同一个（"这个调用方没被授权"），而**是哪一条**是给复核的人看的。

**决策五（没有变，所以更要说）：这仍然不是那条边界。** 没有 named pipe、没有提权进程、**没有任何动词走到
`admit_caller`**；它今天只被测试调用。它在 `references/reason-codes.md` 里因此属于**《有写者，但没有任何
动词能走到》**那一类，**不**进《这一版发不出来的码》——判据是"有没有写者"，不是"有没有路"，而这是第一次
这两件事分了家（§114.4 量到并写清了）。

**本条 ADR 没有决定的事**（留给下一阶段，但形状已被这里定死）：`allowed_sids` 与 `minimum_integrity`
**从哪里来**——本模块只接受它们作为**显式入参**，不读任何策略文件、不带默认阈值、更不从根目录的 owner 推
（Protected machine mode 下 root 目录的 owner 很可能是 Administrators，拿它当"谁可以问"会把合法用户拒掉，
§114 量过）；named pipe 的 DACL；"调用者写完还活着"的判据（§113.6-4 留给 broker 的那条）；私钥的受保护存放。

## ADR-0042：自指的守卫在照镜子——按被守卫的那个集合取用例的性质测试，删掉一个成员也删掉它自己的用例

**背景**：§114 的判定模块有一条"必读事实"集合（`REQUIRED_FACTS`），以及一条按字段参数化的性质测试：
"把这个字段置 `None`，调用必须被拒"。红验证里有一格是**从集合里去掉一个事实**，预期它红——它跑出
**0 红**（54 绿）。原因不是那条规则没被检查，而是**检查在照镜子**：参数化的用例**来自那个集合本身**，
于是删掉成员与删掉用例同时发生，测试文件仍然是全绿。`elevated` 与 `session_id` 有同一个洞。

**决策：被守卫的集合必须由字面量钉住，性质测试的用例取字面量，不取被测对象。**
具体做法：`REQUIRED_FACTS_EXPECTED` 写成字面量（六项），一条测试**双向**比较它与 `REQUIRED_FACTS`；那条性质
测试与"这些字段真的是 `ProcessIdentity` 的字段"那条都指向字面量。修完，那个变异红 **3** 条。

**为什么这条值得单独立一条 ADR**：一个从被测对象取用例的守卫，检查的是"被测对象与它自己一致"——它**永远
为真**，而且它比"没有守卫"更危险，因为它会让读者以为那里有守卫。这与 ADR-0033 是同一族（豁免的理由要由
**构建**度量，不能由被豁免者自己声明），但方向相反：那一条防的是"谁说了算"，这一条防的是"用例从哪来"。

**适用范围与不适用**：适用于任何"遍历 X 的成员生成用例"的测试，以及任何"从被测数据里读出预期值"的断言
（§75 的"哪些码发不出来"就是**故意**用构建度量而不是字面量的那一半，它的权威是一次对全树的扫描）。
**不**适用于"被测对象就是权威、测试要证明它没变"的场合——但那种时候**必须有一处是字面量**，否则没有任何
东西可以红。判据是一句话：**把被测对象改坏，测试必须红；如果改坏它的方式同时改坏了用例，那这个测试守不住
任何东西。**

## ADR-0043：线路本身——一帧一个完整 envelope、只接受本机客户端，而它仍然不是那条边界

**背景**：§113 给了"读**别人** token"，§114 给了"谁可以问"的判定，两者之间还差一条线。§115 按
`docs/broker` §2 与 §3 把它建出来：§2 明确允许"无 elevated Broker 的同用户模拟"，但要求响应**必须**带
`security_mode` 与 `enforcement`；§3 规定 IPC 是受 ACL 保护的 named pipe、只接受本机客户端，并且**一次
提交请求必须是一个完整 envelope，不能由客户端分段拼接安全字段**。

**决策一：帧是数据，不是代码；而且上界在「读」之前生效。** 一帧 = 4 字节小端长度 + 该长度的 UTF-8 JSON。
`MAX_FRAME_BYTES` 不是取整数的好看值，而是从文档自己的界推出来的：`broker-request` 的字段除
`plan_ref`/`approval_ref`/`transaction_id` 外都被 schema 限长，而那两个 ref 是 root 相对路径（≤32 767 个
UTF-16 码元 ⇒ 每个 ≤65 534 字节，一对 ≤~131 KiB），256 KiB 是覆盖这个最坏情况加 JSON 开销的最小 2 的幂；
实测最大的现实 `commit_plan` 请求只有 **879 字节**（低于上界 298 倍），测试要求它保持在 1/64 上界以下，
所以"上界"不会悄悄不再是现实文档的上界。三条同族的决定：**超限时在读 body 之前就拒**（读完 4 GiB 再检查
不叫上界）、**`encode_frame` 用同一个上界**（发送方不该发出自己的读者会拒的帧）、**`decode_frame` 刻意
不校验 `broker-request` 的 schema**（帧 ≠ 协议，"字节能解出来"永远不能读成"请求合法"）。规范化字节序是
发送方的礼貌，读者**不得**依赖 key 顺序——两个方向都有测试。

**决策二：只接受本机客户端在 Windows 上是一个参数，而 MSDN 指错了它。** `PIPE_REJECT_REMOTE_CLIENTS`
（`0x00000008`）在 MSDN 的 `CreateNamedPipeW` 页面里列在 `dwOpenMode` 下，**而这台机器不接受放在那里**：
实测（`ctypes` 与 C# P/Invoke 两条独立路径）放在 `dwOpenMode` 里 `CreateNamedPipeW` 直接返回
`INVALID_HANDLE_VALUE`、`GetLastError()=87`，pipe **根本没被创建**；放在 `dwPipeMode` 里成功，且
`GetNamedPipeInfo` 把这一位读回来（`0x9` 对 `0x1`）。Windows SDK 头文件也把它归在 `dwPipeMode` 一节。
**MSDN 与头文件不一致，OS 说了算**——而且这个位**是可以验证的**，所以"只接受本机客户端"在这台机器上是
读数，不是声明。（本阶段之前我把这条判成"本地不可验证"，那句话被这次实测推翻。）

**决策三：判定只吃观测，而观测从 OS 来。** 服务端用 `GetNamedPipeClientProcessId` 拿**对端进程号**，再用
`probe_process` 读那个 token，再问 `admit_caller`。**请求里的 `client` 块在任何一步都不参与**——这是
ADR-0041 那条不对称在真机上的第一次执行：一个谎报 SID 与 `application_id` 的请求，与一个诚实的请求得到
同一个裁决。

**而"从 OS 来"只对**本机**对端成立**（§115.4 量过）：loopback-SMB 对端的 `GetNamedPipeClientProcessId` 返回
**65279**——那不是连接进程，也不是任何一个本机进程（`OpenProcess(65279)` 报 87，`probe_process` 读不到任何
事实）。所以远端的"进程号"是客户端那一侧的一个数，而**如果它恰好撞上一个活着的本机 pid，admission 会把这次
调用归给一个无关的本机进程**。因此判定之前必须先断言**本机**，而这件事是可测的：
`GetNamedPipeClientComputerNameW` 对本机对端返回 `FALSE` 且 `err=229 (ERROR_PIPE_LOCAL)`，对远端返回真和一个
非空名字。**远端的对端没有可信的进程号，所以没有任何东西可以用来准入它**——这是拒绝的理由，不是保守。

**决策四：每一份响应都必须说自己是什么。** `security_mode` 与 `enforcement` 是 `broker-response` 的
**必填**字段，不是礼貌：一个不受保护的服务声称 `protected_machine` 是**假话**，而 `same_user_can_bypass`
是**真话**。已发布的 schema 允许四对组合、且一对都禁不掉（跨字段约束会拒掉今天合法的文档，而已发布 schema
不能在 `schema_version: 1` 里加这种约束），所以自洽性住在 `cli/app/airoot/posture.py` 这一处，由守卫保证：
`posture.py` 是**唯一**拼写那四个词（`policy_only` / `protected_machine` / `same_user_can_bypass` /
`acl_enforced`）的 app 模块。这次收拢顺带量到一条：`field-values.md` 里 `protected_machine` 与
`acl_enforced` 原本**没有** †，而它的"有写者"证据是 `doctor.py`/`envelope.py` 里那句
`security_mode == "protected_machine"`——**一次比较被读成了写出**；† 是 §115 补上的。

**决策五：这不是那条边界，而且它知道自己不是。** 没有提权、没有 Rust 服务器、没有跨用户的强制：pipe 的
DACL **按设计**允许当前用户（否则这个模式根本无法工作），所以它只能在"同用户进程本来就能做同样的事"的
地方运行。**而且这条"不是边界"是量出来的，不是声称的**：pipe 的名字**永远不是独占的**——实测一个后到的
进程可以给同一个名字挂上自己的实例，而 `FILE_FLAG_FIRST_PIPE_INSTANCE` 只让**你自己**的创建在名字已被占用时
失败（那是防蹲，不是独占）；名字在叶子与 `\pipe\` 两段都**大小写不敏感**，所以它也不能当身份令牌。DACL 拦不住
同用户进程，因为 DACL 管的是**连接的权利**，而那些进程本来就有这个权利。**跨用户那一半仍然是未测量的**
（§115.4 写了它需要什么，以及为什么这些事被 `AGENTS.md` §8 禁止）——所以 docs/broker §2 的兼容模式**不能**
被读成关于跨用户行为的证据。`allowed_sids` 与 `minimum_integrity` 仍然只是显式入参——**它们该从哪来，本条没有决定**，因为
那需要一个 root 的声明，而那个声明还不存在。

**本条没有决定的事**：`allowed_sids`/`minimum_integrity` 的来源；受保护的 Rust 服务器（ADR-0001 的语言切换，
而它要等一个生产签发方）；pipe 是否变成一个 CLI 动词（今天它是库加测试入口，没有动词）。
