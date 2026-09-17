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


---

## ADR-0044：**没有密钥**——受保护签发方这一步被实测与裁决一起关掉

**背景**：ADR-0039 的后果一节把"真实私钥没有任何被保护的家"列为待做项，`docs/broker` §9 又把"真实签名
密钥的部署"排除在自己的范围外，于是这一步从来没有任何设计。本轮第一次去量它，量到的结果比预想的硬：
**它在本机做不到，而且做不到的原因与实现水平无关。** 本条把这个结论、它的证据、以及被否决的三条路
一起记下来，并据此**不做**这件事——免得它每隔几个阶段被当成"下一步"重新捡起来。

**量到的事实（两台独立探测，一台 `ctypes` 直调 `ncrypt.dll`、一个子 agent 独立复核，结论一致）：**

| 事实 | 读数 |
|---|---|
| 本机 CNG 支持 `ED25519` 吗 | **不支持**：软件 KSP 与 TPM KSP 都是 `0x80090029`（`NTE_NOT_SUPPORTED`） |
| 那它支持什么 | `ECDSA_P256/P384/P521`、`RSA`、`DSA`、`ML-DSA`（后量子）、`Composite-ML-DSA`；**没有 Ed25519** |
| "OS 持有不可导出密钥"这个机制通不通 | **通**：`ECDSA_P256` 两个 KSP 都能 create+finalize+sign（64 字节签名），且 `NCryptExportKey` 取私钥**被拒**（软件 KSP `0x80090029`、TPM KSP `0x8009000A`） |
| 软件 KSP 的密钥容器 ACL | 拥有者 token **Full**；也就是说那个"不可导出"的密钥，**同一用户的进程读得到容器** |

**为什么这件事与实现水平无关（本条的核心判断）**：目标的原话是"私钥有一个**不可被用户自己的进程读取**的
家"。这句话在本机**不可能成立**，理由是一条推论而不是一次实验：

> **任何"用户自己的进程能拿来签名"的密钥，同用户身份的任何进程都能读。** 签发者若是一个用户进程，它以
> 用户身份读密钥；既然同身份，别的进程也能读同一份东西。

三条路因此各自被否掉：

* **文件式密钥库（含"只有 Administrators 能读的目录"）**：签名者读得到 → 攻击者作为同一 token 也读得到；
  签名者读不到 → 它签不了。**ACL 只能把"能签的人"与"能读密钥的人"设成同一个人。**
* **OS 持有的非导出密钥（CNG 软件 KSP）**：实测容器对拥有者开放。CNG 买到的是**不可导出**（字节拷不走），
  **不是**"同用户进程不能用它签名"。
* **DPAPI / 用户级加密**：以该用户身份运行的任何进程都能解密。它防的是"磁盘被拷走"，防不了同一台机器上的
  同用户进程。

**唯一两条真能挡住同用户进程的路**，都不在 CNG 里：**一个人才知道的秘密**（口令/PIN 解锁，签发时才把私钥
带进内存），或**一个 OS 强制的权威边界**（提权 + 受保护服务；注意 TPM 本身不是这条边界——实测其密钥仍可由
同一用户调用，TPM 加的是"密钥不能被拷走"）。**而按 ADR-0045 的归属，这两条都属于使用方（上游 harness），
所以它们不是 AIROOT 欠下的债——这一句写在 ADR-0045，因为本条第一次写下时把它读成了缺陷。**

**决策：不做。没有密钥，也就没有签发方、没有 keyring 迁移、没有算法变更。**

理由是**裁决**而不是"太难所以跳过"：

1. 这条路的**全部价值**在于"同用户进程不能伪造批准"。而上面那条推论说明：只要签发者是一个用户进程可调用的
   东西，这个价值就**买不到**；能买到它的两个机制（人口令、提权边界）都属于 P2 之后的阶段。
2. ADR-0025 的 D1 已经就这一点选过 A（维持现状），并**明文否决**了"先做起来、以后再保护"：在没有受保护存储
   的机器上做真实签名，等于把"私钥就放在 root 里"变成事实上的设计，还给它披上"已签名"的外衣。本条的实测
   只是给那次裁决补上了它当时缺的那块证据——**本机连"一个不可导出的 Ed25519 密钥"这个前提都没有**。
3. 三个待选项的代价都不划算，且**没有一个能兑现目标里那条性质**：
   * **扩 schema 改用 `ECDSA_P256`**：本机立刻可行，但 `approval-token` 的算法 enum 是**已发布契约**
     （`schema_version: 1`），扩它要新 schema id、改边界表、重生语料——而买到的东西里**仍然没有**"同用户
     进程不能签"这一条。
   * **口令短语解锁的 Ed25519**：schema 一个字不用改，也是唯一真能挡住同用户进程的路，但它要求**明文推翻**
     `crypto/ed25519.py` 与草案 §113.6 第 6 条那句"长期签发密钥不得由本模块签"。用一条 ADR 换掉另一条 ADR
     的硬约束，不该顺手做，且它仍然需要一个**人在场**的通道（`approve` 在 CLI 里没有）。
   * **把签名留到 Rust**：与 D1 一致，但**P2 内不闭环**——Rust 切换本身要 stage/commit，而 stage/commit 要批准。

**后果（要说清楚，因为这是一个"少做"的裁决）：**

1. **`approve`/`install`/`env persist`/`tool gc --apply`/`uninstall` 五条路径在真机上继续走不到底**，报
   `PROVENANCE_FAILED`(7) 并带 `ISSUER_PENDING`。这不是回归，是**承认**；那条消息的措辞（"等 P2 的 broker"）
   仍然准确——只不过现在还要加一句"而且本机没有可用的 Ed25519 密钥托管"。
2. **`state/test-keyring.json` 的名字不改。** ADR-0039 决策四说改名属于"给它一个生产写者"的那一阶段；本阶段
   决定**没有**生产写者，所以改名失去理由，名字保持诚实。
3. **keyring 的完整性缺口照旧存在**（ADR-0039 决策五：任何能写 root 的进程都能换掉它）。本条没有关它，也
   **不假装**关了它；关它需要的是 ACL 强制（`caps/acl.py` 的写一侧接上调用者），那是另一个阶段。
4. **本机可用的算法集合被记下来了**（`ECDSA_P256/P384/P521`、`RSA`、`DSA`、`ML-DSA`、`Composite-ML-DSA`，
   无 Ed25519）。将来若真要换算法，这张表决定了可选项**不只有 P-256**，而换算法是一个独立的、要动已发布
   schema 的裁决。
5. **探测没有留下任何痕迹**：两轮都自清理，`%APPDATA%\Microsoft\Crypto\Keys` 前后都是 47 个文件、名字集合
   逐字相同。TPM provider 上**只做了只读的算法查询，没有创建密钥**（TPM 写入不可擦除）。

**本条没有决定的事**：将来要不要走"人口令解锁"那条路；算法要不要扩 enum；以及 `docs/broker` §5 里那些
`approval_mode=human` 的语义（`approved_by_sid` 等）在没有签发方时怎么读——它们今天只被测试签发方产出。


---

## ADR-0045：安全归属在使用方——P2 重定为 Managed State，受保护状态降为可选 P9

**背景**：ADR-0044 关掉受保护签发方之后，项目的部署现实被正式说清：**AIROOT 面向家用主机上的单用户场景，
"是否可信、是否放行、要不要在执行前拦一下"由使用它的上游承担**——例如 DeepSeek Harness 这类 harness，
它有自己的确认、审批与沙箱策略。AIROOT 交出的是**可核对的账**（state、journal、audit、diagnostic 与验收语料），
不是 **enforceable 的边界**。

**这条不是新政策，而是把一处自相矛盾纠正过来。** 规划 §2.3 从第一版起就把"用 Skill 规则代替 Windows ACL
的安全系统"与"对抗已经拥有管理员权限的恶意进程的安全产品"列为**非目标**；§8 也写着"Skill 目录不是天然安全
边界……只能提供 User compatibility mode 的安全语义"。**但路线图的 P2「Windows Protected State」的交付清单与
退出条件，要求的正是这两条非目标**：ACL 基线、elevated broker、"普通用户无法直接写 R"。文档的一头说
"这不是我们要做的"，另一头把"做到它"当成一个阶段的退出条件。

**决策一：P2 重定为 Managed State，只保留不需要提权、与安全无关的那部分。**

保留：`AIROOT\cli` 专用目录、machine PATH 单一 exposure、可审计的批准记录。
退出条件里只保留两条，且**都改读法**：`policy_only` 标记是**永久自述**（不是待消除的临时状态）；
"UAC 取消不破坏旧 active generation"保留，因为它是**事务**性质而非安全性质。

**决策二：被移出的四项进"可选的 P9 加固"，并写明它不是欠债。**

ACL 基线与其强制、elevated broker、`path backup|restore`、以及"普通用户无法直接写 R"这个退出条件。
P9 的触发条件写成一句可判断的话：**只有当有人真的需要 AIROOT 自己扛住同用户进程时才做**。

**决策三：`p2-protected-state` 这个解锁词不改拼写。**

它同时出现在 `cli.py` 的 `DECLARED_ABSENT`、`agents/airoot.json` 的 `deferred`、golden 语料
`scenario_ledger.json`、以及四个守卫里，而守卫比较的是"两个登记表是不是同一个字符串"（§83/§101 的
`test_the_two_registers_spell_a_shared_phase_the_same_way` 就是为这类近似写法存在的）。改它要连带改语料与
守卫，**而它指向的阶段还没有承诺要做**——给一件没人接的活先付改名成本是错的顺序。代价是**词与它指向的阶段
之间不再贴合**（那五条动词真正等的是 P9），这一点已写进 `AGENTS.md` §8 而不是留给读者猜。

**决策四：这次重定没有改任何代码、schema 或语料。**

`security_mode=policy_only`、`enforcement=same_user_can_bypass`、五条 `NOT_IMPLEMENTED` 的行为、
keyring 的位置与缺口——**一个字节都没动**。改的只有文档：规划 §2.3 与 P2/P9 两节、`AGENTS.md` 的状态段与
"下一步的排序"，以及本条。**这是有意的**：重定的是"哪些事属于我们"，不是"已经做出来的东西是什么"。

**后果与边界：**

1. **"尚未实现"这个清单要换个读法。** ACL 强制、受保护 broker、machine 级写入、`exposure\bin` launcher、
   跨用户过滤——它们不再是"该做而没做"，而是**按归属不做的**。继续把它们列在"待办"里会让后来的读者
   以为欠着债，从而去补一个不该补的边界。
2. **P4（真实安装后端）与 P5（Runtime）成为最有价值的方向。** 它们才是"家用主机上管好环境与工具"的主体，
   而此前一直排在受保护状态后面等。**注意 P4 的 stage/commit 仍要批准**——`PROVENANCE_FAILED` 那条路
   （ADR-0044）没有因为本条而消失，所以 P4 在真机上仍然走不到底；**这是一个尚未解决的张力，标记在此。**
3. **USN 常驻索引器的"要提权"前提需要重新论证**（`AGENTS.md` §8 已标为未裁决）。它当初的阻塞理由是
   "初始枚举要 broker 提权"；若安全归属上游，这个前提本身就该重估，而不是继续当既定阻塞。
4. **这不动摇任何已冻结契约。** Zone R/W/P、单一提交点、永不删除、reference 永不 `uninstall`、
   `policy_only` 标记——全部照旧。**被降级的是"靠机制挡住同用户进程"这个目标，不是"确定、可审计、
   可诊断、可恢复"这个目标。**


---

## ADR-0046：本机签发——批准是**账本**，不是授权证明（推翻 §113.6 第 6 条）

**背景**：ADR-0044 关掉了受保护签发方，ADR-0045 把安全归属移到使用方。之后为了让**真机上的
`install` 能跑完**（也就是真正获取 Rust 工具链），必须回答"谁给签发"。契约 `approval-token.schema.json`
把 `signature` 列为**必填**且 `algorithm` 的 enum 只有 `ed25519`/`test_hmac_sha256`，而核心永远没有签名侧
——所以**凡是能签发的东西都必须持有一把私钥**，不存在"无签名的批准"这条路（那要改已发布 schema）。

**先记一条走不通的路，因为它一度看起来可行。** "上游（DSH 那类 harness）签发，公钥作为信任锚随 AIROOT
分发"曾被推荐为最优解，理由是它不改 schema、且在新威胁模型下那把私钥不需要强保护。**实测否掉了它**：
keyring 在 root 里（`state/test-keyring.json`），而三大核心契约第 22 行自己写着 user compatibility mode
**不能声称能阻止同用户 Agent 直接修改用户目录**。于是本机进程可以把信任锚换成**自己的**公钥、用**自己**的
私钥签一份合法 plan，而 AIROOT 验签**通过**。**"密钥不需要强保护"对密钥成立，对信任锚不成立**——锚必须
比它约束的东西更强，而这需要提权或 ACL，正是 ADR-0045 决定不做的。所以上游签发这条路在本项目范围内
**不成立**，不是"代价高"，是**它买不到它声称的那件事**。

**决策一：推翻草案 §113.6 第 6 条——那把签发密钥由本模块的 `sign` 签。**

原文（**曾经正确**）："Ed25519 不是常数时间的，`sign` 用私钥时有数据相关的加法链，因此**不得**用它保管
长期签发密钥（P2/Rust 才拥有那把密钥）"。它当时对着"要挡住同用户进程"的威胁模型写——在那个模型下，
签名耗时可被本机进程测量，所以非常数时间是真实缺陷。

**ADR-0045 换掉了那个威胁模型**：AIROOT 不再声称挡住同用户进程，安全归属使用方。于是：
**签发方不再需要抵抗本机进程**，而"非常数时间"防的正是本机进程的计时观测。**约束的前提消失了，约束
随之失效**——这不是"为了推进而放宽"，是**承认它守的那扇门已经不在我们的墙上**。

代价照实写：私钥在 root 里，**同用户进程读得到、也能自己签**。这是**已知且接受**的后果（ADR-0045），
不是缺陷。

**决策二：`approve` 的语义从"提交授权"降级为"记录一次批准"。**

这是本条最需要读者看清的一处：**批准不再是一份证明，而是一笔账**。它仍然入 registry、仍然由 journal
驱动整个事务、仍然可审计可复查——但**它不证明调用方被允许做这件事**，因为同用户进程可以自己造一份
一模一样的。`approval_id`/`nonce`/`plan_hash` 仍然被校验（防重放、防换 plan），那些是**一致性**检查，
不是**权限**检查。

**决策三：签发方仍然不进核心。**

恒定式不动：**核心永远没有签名侧**——`airoot approve` 只*消费*批准。真机上那个签发者是一个**显式的人
工步骤**，与核心分开：它读 plan、用 root 里的私钥签、写出一份 token 文件，再由 `install --token-file` 消费。
把它做成独立模块而不是一个 CLI 动词，是为了让"谁签的"在代码结构上就看得见——**一个动词会让签名看起来
像 CLI 自己就能做的事**，而它恰恰不是。

**决策四：keyring 从 `state/test-keyring.json` 改名为 `state/keyring.json`，并保留旧名兼容。**

ADR-0039 决策四当初说"改名属于给它一个生产写者"的那一阶段，因为那时它唯一的写者是测试签发方。**现在生产
写者存在了**，所以那一阶段的理由成熟了，改名随本阶段一起做。加载期两个名字都认（新名优先），因为
`cli/tests/.tmp/` 下的既有 root 与语料引用了旧名；**迁移不是删旧名，而是"旧名仍可读、新名是规范"**。

**后果与边界：**

1. **真机上的 `install` 可以跑完了**——包括真实获取 Rust 工具链，前提是先跑一次显式签发。这不是
   "P1 走通了"，是**一个被接受的弱化**换来的可完成性。
2. **`test_hmac_sha256` 仍然只许出现在测试路径**（`docs/schema/README.md` 的边界不变）：本机签发用
   `ed25519`，不是把测试算法搬进生产。
3. **ADR-0044 的实测结论全部仍然成立**（CNG 不支持 Ed25519、软件 KSP 容器对拥有者开放、同用户进程读得到
   容器）。**变的不是读数，是"这算不算缺陷"**——在旧模型下它是阻塞，在新模型下它是被接受的后果。
4. **本次改的是语义与一处路径名，不是 schema。** `approval-token` 的必填项与 enum 一个字节没动；
   golden 语料里若有 keyring 路径的引用则随之重生。

## ADR-0047：`run` 是"执行一次受管 payload"，不是"持久化暴露"

**背景**：最小版本的定义（`docs/AIROOT-最小版本-v1.md` 判据 #9）要求"**有一个动词**真的把这份受管
payload 跑起来并如实报告它的退出码与输出"。§118 完成了这件事，但方式是**手跑绝对路径**——那不是动词，
不是验收面，也不在账本上。本条裁决那个动词的形状与边界。

**决策一：动词形状固定为 `airoot run <instance-id> [-- args...]`。**

名字与形状由最小版本的定义钉死，不提供同义词、不做别名。`--` 之后的参数**原样**交给子进程（与 `exec`
同一约定：`--` 之前属于 AIROOT，`--` 之后属于那个 payload）。

**决策二：它是"跑一次"，不是"暴露"。**

| | `run` | `env persist` | `env activate` / `exec` |
|---|---|---|---|
| 作用范围 | **一个子进程**，跑完就结束 | 用户级环境变量（持久） | 会话内 / 一个子进程（给 reference 注入环境） |
| 需要批准 | **不需要** | 需要（`PERSISTENCE_REQUIRES_APPROVAL`） | 不需要 |
| 动 registry 吗 | 不动（只读） | 写 `env_vars` 记录 | 写会话快照栈 |
| 对象 | **owned** 受管 payload | reference（owned 禁止持久化） | reference |

三条差别合起来就是这条裁决：`run` 不写任何持久状态，所以它**不需要批准**——"需要批准的动作藏在只读
动词后面"是这一层最该避免的形状。**代价**：想"激活一次、后面随手用"的调用方仍然要走 `env activate`
那条路（它服务 reference），而 owned payload 的"随手用"要等 W5 的稳定入口；`run` 本身不提供那个便利。

**决策三：cwd 与 env **都继承调用方**，绝不注入 PATH。**

**不把 cwd 设成 store 目录**，因为一个会往 cwd 写文件的工具会**弄坏 store 的整树摘要**，而 store 的
不可变性是 `tool verify` / `doctor` / `gc` 的判据（`artifact_digest` 是整树摘要）。**代价照实写**：
`run` 的行为因此依赖调用方的 cwd——一个往 cwd 写东西的 payload 会把文件写到调用方那边。这是刻意的：
AIROOT 是管家，不是替 payload 选一个数据目录的人。

**不注入 PATH**，理由有两条且都不是洁癖：冻结契约第 8 条（机器 PATH 里 AIROOT 条目恰好一个）、契约
§13.2/§14.2（禁止把持久化值指向 shim）。入口由 `run` **以绝对路径**给出，所以"跑的是哪一份"在命令里
就看得见，不需要靠 PATH 的先后去猜。

**决策四：`run` **不**复核摘要。**

复核属于 `tool verify`（它算整树摘要、只报告不修复）。`run` 的职责是"执行并如实报告"，它文档里的
`artifact_digest` 是**登记值**而不是重算值——字段的取值与证据行都要让读者看得出这一点。**被否决的替代
方案**：`run` 先 `tree_digest` 再执行。对 12 MB 的安装器它便宜，对将来的几百 MB runtime 它就是每次运行
的固定税，而且**它并没有让"执行"更安全**：漂移检测是完整性问题，不是执行问题。代价：一个已经漂移的
payload 仍然会被执行；要拦它，调用方在 `run` 之前自己 `tool verify`。

**决策五：拒绝只建立在**事实**上。**

拒绝只有三种：**未登记**（`NOT_FOUND`(1)）、**不是 AIROOT 拥有的**（`OWNERSHIP_REQUIRED`(7)，证据指向
`exec`——reference 那条路）、**载荷或入口点缺失**（`PAYLOAD_MISSING`(3)，证据里点名缺的那个绝对路径，
以及 `collected_at`（若这个实例已被 `gc` 回收））。

**不**拒绝 `retired` / `broken` 的实例：那是**报告**的事实（`lifecycle_status` / `health` / `collected_at`
都进文档），不是策略。按 id 点名一个载荷是调用方的决定，"如实报告"是本项目的规矩——把一个能跑的东西
藏起来比让它跑并说清它的状态更糟。**代价**：调用方要自己读那两个字段。

**决策六：不新增 reason code，也不新增 schema。**

子进程失败用既有的 `CHILD_PROCESS_FAILED`(2)（§115 的教训：不借一个只因为退出码相同的码，也不为了
整齐造一个新码）。`run` 打印的是**报告面**，像 `exec` 一样没有已发布 schema——§100 量过 34 条 lane 里
只有 5 条读的文档被 schema 描述，而报告面各有不同的顶层形状。将来若要给它一份 schema，那是**新增边界**
（一次 schema 目录的扩张），不是"放宽枚举"。

**被否决的路（各一条，附代价）：**

1. **做成 `exec --instance`**：`exec` 的语义是"给 reference 注入环境后跑**任意**命令"，把受管实例塞进去
   会让同一个动词按参数的**种类**改变"跑的是谁的主入口"这件事——动词契约被削弱。代价：多一个动词。
2. **让 `run` 也做持久暴露**（写环境变量或 PATH）：那是 `env persist` 的活，而它需要批准与精确还原；
   合并等于把需要批准的动作藏在只读动词后面。
3. **不提供 `run`，让 agent 自己拼 store 路径**：§118 就是这样做的。代价是"AIROOT 装了什么、哪一份是
   活跃的"整件事**绕过账本**——`run` 存在的理由正是把那次手跑变成账本上可复核的一次。

**后果**：最小版本的判据 #9 成立；它**不是**"受管运行时"的完整形态——runtime health 的完整体系、多
runtime 的版本矩阵、以及任何形式的持久化，都在本轮的显式排除清单里（见最小版本文档 §3）。

**状态：已裁决。** 实现与实测读数记在草案 §120。

## ADR-0048：安装器装的产物要被账本认出来——给已冻结的 `rust-toolchain` 补上"识别"那一半

**背景**：最小版本的定义（`docs/AIROOT-最小版本-v1.md` 最后那段）要求**账本能表述"AIROOT 装的安装器，
又装了别的东西"**：那批产物可以被登记为 reference。§118 之后本机正好有这个真实例子——AIROOT 把
`rustup-init.exe` 装进 store，而它把 rustc/cargo/rustup 写进了 `%USERPROFILE%\.cargo` 与 `.rustup`。
W3 就是去把这件事登记进账本，而它**当场失败了**。

**实测（真机，`D:\env\.airoot`）**：

| 观察 | 读数 |
|---|---|
| `discover` 对 `.cargo` | 扫 4 个对象、1 个可执行文件；候选 `bin` 报 **"no whitelist entry matched (1 executable(s) inspected)"** |
| `discover` 对 `.rustup` | 扫 5 个对象、6 个可执行文件；候选 `downloads`/`tmp`/`toolchains`/`update-hashes` 全部同一条结论（`toolchains` 那一条检视了 **6** 个可执行文件） |
| 排除名单 | **一条都没命中**——被挡下来的不是它们 |
| `adopt C:\...\.cargo\bin --mode reference --capability rust-toolchain` | **`CAPABILITY_NOT_DECLARED`(9)**，消息 `no whitelisted capability matches bin`，证据是那两条：`no whitelist entry matched (1 executable(s) inspected)` 与 **`freeze the capability and add a whitelist entry first`** |
| `rustc.exe` 的 PE 静态事实 | `product_name="Rust Compiler"`、`file_description="rustc"`、**`file_version="1.98.1.0"`** |
| `cargo.exe`（31 MB 真身）、`rustdoc.exe`、以及 `.cargo\bin` 下那三个 12.7 MB 的 rustup shim | **一个版本资源都没有**（六个字段全空） |

**所以"缺的"不是能力名：`rust-toolchain` 已经在 `cap-3` 里冻结了。缺的是 §15.4 成长路径的第二步
——白名单证据谓词。** 而 §117.5-3 当初**刻意**没给它写谓词，理由是"它是从可信来源装进来的，不需要在
用户目录里被认出来"。**那条理由没有错，它只是没预见到今天这个局面**：§117 只把**安装器**放进账本，
所以当时确实没有东西需要被认出来；而现在要登记的是**那个安装器写出来的东西**，它在用户目录里，账本要
认它就必须有谓词。

**决策：给已冻结的 `rust-toolchain` 加上识别谓词（白名单 `wl-4` → `wl-5`）。**

```json
{
  "capability_id": "rust-toolchain",
  "kind": "tool",
  "evidence_all": [
    {"type": "executable_name", "any_of": ["rustc.exe"]},
    {"type": "pe_static", "field": "product_name", "contains": "Rust Compiler"}
  ],
  "entrypoints": ["rustc.exe"],
  "version_source": "pe_static:file_version"
}
```

**为什么是这两条谓词，而不是别的**：它们是**量出来的**，而且两条**都必须**成立才说明问题——
`executable_name` 单独一条会把 `.cargo\bin` 里那三个**没有版本资源的 shim** 也算成 Rust 工具链
（登记出来会是一条版本读不出来、证据只证明文件名的引用）；加上 `product_name contains "Rust Compiler"`
之后，命中的是**真的编译器**（`rustc.exe`，版本 1.98.1.0），而 shim 因为 PE 里什么都没写而不命中。
**这条不对称是这一段的重点**：`.rustup\toolchains` 会被认出来，`.cargo\bin` **不会**——后者留在
`unmanaged` 里被如实报告，而不是被凑成一个空壳引用。

**它不意味着什么（写清以免被读大）**：

1. **对象不因此变成 AIROOT 拥有的**。登记走 `adopt --mode reference`，`uninstall`/`gc` 对它一律
   `OWNERSHIP_REQUIRED`(7)——`is_owned` 的两个信号（`store_path` 在 `store/` 下、后端不是外部后端）
   一条都不成立。`discover` 只是把它报成 **reference 候选**。
2. **优先级一个字没改**（规划 §3.2 / §9.10、ADR-0006 的 steward-first）。但**结果会变**：`where
   rust-toolchain` 之后会选中那条**健康的 reference**（真的 `rustc.exe`）而不是 store 里的**安装器**。
   这是优先级规则早就写好的行为（"健康的 external reference 是一等候选"），不是这次新加的偏好；
   读数记在 §121。
3. **`rust-toolchain` 的冻结条目形状没动**：`kind`/`entry`/`side_effects`/`scope` 一个字节没改。

**被否决的路（各一条附代价）**：

- **发明一个新能力名**（例如 `rust` 运行时）：§15.4 明说不得为了让某个对象能被管而临时放宽/新增名字，
  而"提议 → 冻结"是**另一件事**，不该混在一次登记里做完。代价：这条路的产出会是一个只有一行谓词、
  没有真实安装路径的能力——正是 §117.3 那次"来源清单为一个不存在的能力声明来源"的镜像错误。
- **只按可执行文件名匹配**（`cargo.exe`/`rustc.exe`/`rustup.exe`，不要求 PE 证据）：会把 `.cargo\bin`
  的三个 shim 一起认成工具链，而它们**没有版本资源**（实测），于是登记出一条版本为 `null`、
  证据只有文件名的引用。代价：账本里多出三份**看起来像能力、实际读不出任何事实**的记录。
- **什么都不做，只报告"账本表达不了"**：这是**诚实的第一版答案**（W3 第一次跑出来的就是它），但它让
  最小版本定义的最后一段永远不成立。代价：定义里那条被标成"已交付"的判据是假的。

**代价照实说**：白名单 revision 从 `wl-4` 变成 `wl-5`（`execution_bounds.json` 逐个钉着五个 policy
revision，所以 golden 要重生）；任何装了 Rust 的机器上，`discover` 会多报一个 reference 候选；
谓词进入**加载期校验**的那张表（未知谓词类型 / 没有静态证据 / 引用未冻结能力都会在加载时抛错）。

**状态：已裁决。** 落地读数（`discover` / `adopt` / `uninstall` / `forget` / `doctor` / `where` 六项断言
与扫描成本）记在草案 §121。

## ADR-0049：**签发成为一个 CLI 动词**（`airoot issue`）——推翻 §116.2 的"签发不是动词"

**被推翻的原话**（`cli/app/airoot/tx/issuer.py` 模块 docstring 第 17–21 行，写于 §116 / ADR-0046）：

> **Why the signer is not a CLI verb and not in the core.** The invariant is that the core never has a
> signing side — `airoot approve` only *consumes* an approval — because a CLI that can mint consent has
> stopped being a record and started being an authority. Keeping the signer a separate module that a
> human invokes is how that invariant stays visible in the code's shape: signing is something done
> **to** AIROOT from outside, not something AIROOT does.

**那句话当时是对的**，而且它守的是 ADR-0046 的核心结论——**批准是账本，不是授权证明**。它错的地方只有
一处：**"人工步骤"被写成了"只能由人写 Python 调用"**。它把"这件事必须由一个明确的人发起"和"这件事不能
由 CLI 表达"当成了同一件事，而它们不是。

**今天为什么不再成立**：最小版本的定义要求"**通过 CLI 签发批准**"（判据 #4），理由是**没有动词就没有
agent lane**——一个 agent 读 `agents/airoot.json` 时看到 `approve`/`install` 都没有 lane，理由是"没有
任何 CLI 动词会签发 token"，于是**整条安装路径对 agent 不存在**，只能由人在旁边敲 Python。那不是"更安全"，
那是**把能力藏起来**：工具能做而接口不说，读文档的人只会以为这条路还没做（而它已经做完了，§117 走通过）。

**决策一：新增动词 `airoot issue <plan_file> --out <token.json>`。**

- `--provision`：先调 `tx.issuer.provision(root)`。**已存在密钥时报 `INVALID_INPUT`，原样透传这个拒绝**
  ——不吞、不自动换钥（"密钥换了而没人发现"正是 §116.3 那条守卫在防的事）。
- `--mode`：只允许 `policy`（默认）与 `human`；`human` 而不给 `--approved-by-sid` 时**透传**
  `tx/issuer.py` 的 `INVALID_APPROVAL`（原话："它不会凭空造一个 SID"）。
- `--ttl-minutes` 默认 **5**，直接转发给 `issue` 的 `ttl_minutes`。
- 输出文档里**必须**带一句：**签发是审计记录，不是授权证明**；私钥在本 root 内、同用户进程可读可签。
- **不做**：不提权、不写受保护状态、不动密钥文件、不自动 provision、**不把批准塞进 `install`**。

**决策二：它不给出任何新的权力，这一点要写在动词的文档里。**

同用户进程本来就能 `import airoot.tx.issuer`、读 `state/issuer-key.json`、或者干脆换掉 keyring ——
`issue` 只是把**同一个动作**换成一个可被文档与 agent 面引用的入口。**所以它带来的不是权限，而是可发现性**：
从此"签发"在命令地图、lane 表与 golden 语料里都是有名字的东西。**代价照实说**：agent 驱动它的门槛降低了
（以前要写 Python，现在一行命令）。这条代价是**被接受的**，因为按 ADR-0045/ADR-0046 的归属，**挡同用户进程
不是 AIROOT 的职责**——上游 harness 才是决定"要不要让 agent 自己签"的那一方，而它现在**有了可用的动词**
可以拒绝或批准。

**决策三：三段一一对应，动词名固定为 `issue`。** `provision → issue → approve`：`--provision` 是第一步，
`issue` 是第二步（写出一份 token 文件），`approve` 是第三步（消费并记账）。**三个动词各自只做一件事**，
不合并。

**被否决的路（各一条附代价）：**

1. **把签发藏进 `install`**：最省事，但它抹掉 ADR-0046 的结论——"签发是一次**显式**的步骤"。那会让
   "AIROOT 在装东西的时候顺便批准了它"，即 ADR-0046 明确拒绝的形状（核心有签名侧）。代价：这条路要重开
   ADR-0046 的裁决。
2. **给 `approve` 加一个 `--issue`**：同一个动词有两种输入形状（给 token 文件 / 造一份 token），
   `--token-file` 从必填变成条件必填，动词契约被削弱；而 §120 已经在 `exec` 上踩过同一类问题的反面——
   **一个动词按参数的种类改变语义**是这一层最该避免的形状。代价：省一个动词名。

**后果**：判据 #4 成立；`approve`/`install` 的 lane 从"没有动词"变成"有动词但需要一次显式签发"，两者都要
在 agent 面（`agents/airoot.json` / `SKILL.md`）与 `references/confirmation.md` 里如实写清。
`cli/tests/fake_issuer.py` **保持原样**：它服务测试路径，核心的签发方是 `tx/issuer.py`。

**状态：已裁决。** 实现与实测读数记在草案 §122。

## ADR-0050 — **稳定入口是一个静态 `.cmd`，"写它"不需要受保护状态**（推翻 ADR-0025 的 D4）

**被推翻的原文。** ADR-0025 的 **D4**（`docs/AIROOT-v0.3-实现决策记录.md`）写着：

> ### D4 launcher（`exposure\bin`）：P1 不写任何文件，形状先定下
> - **决定**：P1 继续不写 launcher（`EXPOSED` 的语义是"用一次全新的 registry 读取观察到新 binding"）；
> - **为什么现在只写形状**：launcher 是"被 PATH 找到"的东西，写它的那一侧需要受保护状态。

**D4 把两件事合在一句话里，而它们的前提不同。** 第一件是"**让那个文件存在**"：`exposure\bin` 位于
**用户自己的数据根内**，写一个 `.cmd` 进去与写 `store` 里的 payload、写 `state` 里的快照是同一类动作，
在本项目的 `security_mode=policy_only` + `enforcement=same_user_can_bypass` 自我描述下**本来就不受保护**
——同一个用户身份的任何进程都能写。第二件是"**把这一条放进 machine PATH**"：那要动 HKLM，是受保护状态，
**AIROOT 今天仍然不写它，`path verify` 至今只读**。D4 的结论对第二件事是对的，对第一件事是把
"没法保护"误读成"不能做"。**"这条路径被 PATH 找到"需要受保护状态；"这个文件存在"不需要。**

**最小版本的定义直接要求第一件事。** `docs/AIROOT-最小版本-v1.md` 的判据 #10：**通过不依赖 machine PATH
改动的稳定入口也可调用它**——观察方式是"`cli\exposure\bin\<entry>.cmd` 真实存在且能转发；
`path verify` 的 `launcher_present=true`"，而它记下的今日状态是"❌ 目录不存在（`launcher_present: false`）"。
一份说自己"做到哪里算做完"的文档把这一条写成必须项，就不能再用 D4 的读法把它划到受保护状态里去。

**规划 §1482 那句话仍然成立，而且它约束这个决定**："v1 不使用一个独立、可被 launcher 单独修改的
`current` 文件作为 active 真相……`exposure` 中的 launcher 是静态受保护代码；它读取 registry 的 active
binding。" 本节**不推翻它**，而是把设计做成它的字面意思。

### 具体决定

1. **形状：一个能力一个文件，`<root>\cli\exposure\bin\<capability_id>.cmd`。** 不是每个版本一个、不是每个
   instance 一个。文件名就是被冻结的能力名（`policy/capabilities.json` 里那些），并且必须是**安全的文件名**
   （不得含路径分隔符或 `..`）；不符合就拒绝写，而不是清洗成一个"差不多"的名字。
2. **行尾必须是 CRLF。** `cmd.exe` 对 `rem` 行的处理见 `AGENTS.md` 的"运行环境注意"：LF 会让它把 `rem`
   行切碎成命令。这条不是风格，是能不能跑的问题，所以写入的是**字节**而不是文本。
3. **权威仍然只有 registry。** launcher 里**没有**版本号、**没有** instance id、**没有**任何指向
   `store/` 的路径。它调用 CLI，由 CLI 在**调用时**从 registry 解析 active binding。因此**换版本不需要重写
   launcher**：新旧版本的 launcher 内容**逐字节相同**，这一点是可测的承诺（见草案 §123 的守卫）。
4. **写它的地方是事务的 `EXPOSED` 步**，在两个 runner（`tx/simulate.py`、`tx/artifact.py`）里由**同一个函数**
   完成，且在 journal 推进到 `EXPOSED` **之前**。这样"launcher 写不出来"就等于"`EXPOSED` 没有发生"，
   走既有的回滚路径；崩溃恢复重放该步是幂等的（内容相同就不重写字节）。**规划 §2088 禁止"不产生半个
   launcher"**，把写入放进状态机内部而不是 `cmd_install` 的末尾，是这条禁令的实现方式。
5. **只有 owned + machine 级绑定才有 launcher。** reference 的稳定入口是用户自己的环境（§14.2：AIROOT 不拥有
   它，也不替它建入口）；session/project 绑定属于 W/P 区，**永远不进 machine PATH**（§5 第 8 条），所以也不写。
6. **launcher 调用的动词是 `airoot run --capability <id> [-- args...]`。** 不新造"稳定入口"动词：`run` 已经是
   "跑一次 AIROOT 装的那个东西"，`--capability` 只改变**选哪一个**——从"按 instance id 指名"变成"按当前
   active binding 解析"。`run` 的既有拒绝全部保留：能力没有 active binding → `NOT_FOUND`(1)；解析到的是
   reference → `OWNERSHIP_REQUIRED`(7) 并指向 `exec`；同时给了位置参数和 `--capability` →
   `INVALID_INPUT`(8)（歧义不猜）。
7. **launcher 的内容是**：`@echo off`、一段 `rem` 说明（谁写的、为哪个能力、这个文件**不随版本变化**）、
   一行绝对路径的调用（python 解释器、`cli\app` 进 `PYTHONPATH`、`--root`、`run --capability`、**一个 `--` 分隔符**）、
   `%*` 原样转发、`exit /b %ERRORLEVEL%`。**`--` 不是装饰**：没有它，调用方自己的旗标（`cargo --version`）
   会被 AIROOT 解析而不是交给 payload——这是实现时实测到的那句 `unrecognized arguments: --version`，
   一个叫 `cargo` 的稳定入口必须表现得像 `cargo`。**没有别的**：不解析 JSON、不查 PATH、不猜目录、不注入环境变量
   ——最后一条是 `run` 自己的边界（ADR-0047）。
8. **`where` 报 `launcher`（`string|null`），`path verify` 报 `launcher_present` 与漂移。** `launcher` 在
   schema 里是**可选**属性而不是必填：把必填加进 `where-response` 是破坏性变更，按 `AGENTS.md` §7 要新
   schema id，而这一版不值得为一个字段改契约身份。**它总是被写出来**，由测试钉住（"可选"与"总是发"是两件
   事，后者才是给调用方的承诺）。`path verify` 新增"active binding 没有对应 launcher"的漂移发现（仍是
   冻结码 `PATH_EXPOSURE_VIOLATION`），并把 `launcher_present` 的含义从"那个目录在不在"改成
   "**至少有一个稳定入口存在**"——判据 #10 要的是后者。

### 这是不是新权力／新边界？

**不是。** 它不写 PATH、不写注册表、不写 ACL、不提权、不引入任何持久化环境变量；它写的是一个**文本文件**，
位置在用户自己的 root 内，内容是绝对的、可逐字节复现的。**它也不假装是保护**：那个 `.cmd` 同用户进程可以
随意改写，所以它不是信任边界——所以 `path verify` 必须**度量**它（存在性 + 漂移），而不是假设它。
"launcher 是受保护代码"这句话在**今天这个模式下是假的**，本节把它写成假的而不是留给人猜。

### 被否决的路线

1. **写一个 `.exe` shim。** 要真编译一个 PE：引入编译器依赖（与 ADR-0001 的语言路线和"唯一第三方依赖是
   `jsonschema`"冲突），而且一个**没有签名**的 `.exe` 坐在将要进 PATH 的目录里，比一个明摆着是文本的
   `.cmd` 更容易被当成"受保护的东西"。**形状要诚实于它的强度**：`.cmd` 一眼看出可改写，`.exe` 不会。
2. **`current` 文件 + launcher 读它。** 直接违反规划 §1482，并且制造**第二个权威**：registry 说一套、
   文件说一套时谁赢？本项目对"权威 vs 派生"的规则（AGENTS.md §5 第 4 条）要求派生物可重建、且永不反过来
   裁决权威——为省一次 registry 读取而引入一个会漂移的真相，代价比收益大。
3. **按版本重写 launcher（把版本或 store 路径烧进文件）。** 那样 launcher 就不"静态"了，换版本变成一次
   可能中途失败的文件写；而且它把"哪个版本在用"复制到了两处，第二处必然是错的。
4. **在 `cmd_install` 的末尾写 launcher。** 事务会在 `FINALIZED` 之后返回，那时写入失败就没有回滚点，
   结果是"binding 是 active 的、入口不存在"——正是规划 §2088 禁止的"半个 launcher"，也正是把 EXPOSED
   定义成一个状态而不是一句描述的理由。

### 后果

- `EXPOSED` 从"只观察"变成"**观察 + 写入口**"，与 §5 第 6 条的事务顺序**不冲突**：`ACTIVE_BOUND` 仍然是
  唯一能改 active binding 的提交点，launcher 只是那个 binding 的**派生产物**。
- `path verify` 的 `launcher_present` 变了含义（目录 → 入口），旧读法是错的：它当时能报 `true` 只是因为
  一个空目录存在。**这是一次语义修正，不是放宽**。
- `references/field-values.md` 的 `stable_launcher` 行从"指向 shim"变成"指向那个 `.cmd`"，`EXPOSURE_NOT_IMPLEMENTED`
  那条 evidence 的措辞必须跟着改（它原来的话"P1 writes no launchers"从本节起是假话）。
- 需要新增一个模块（`caps/launcher.py`）与一条 CLI 参数（`run --capability`），**没有新 schema、没有新码**。

**状态：已裁决（B —— 写静态 `.cmd`，权威留在 registry）。** 实现与实测记录见草案 **§123**。

## ADR-0051 — **引用可以落在数据根的任何深度**（`adopt` 的归属规则：从"父目录相等"改为"最深包含"）

### 背景

账本当时的对象模型是"**一个数据根的直接子项 = 一个对象**"：`adopt --mode reference` 要求
`target.parent` 恰好等于某个已登记数据根。这条规则在小数据根上够用（`D:\env\java` 正好是直接子项），
对**用户目录里的工具树**不够用——那些树的"对象"天生在第二层：`~/.rustup/toolchains/<triple>`、
`~/.cargo/bin/cargo.exe`。

实测（临时 root；`dr-cargo`/`dr-rustup` 已按 §121 登记；白名单 `wl-5`）：

| 目标 | 当时 | 读数 |
|---|---|---|
| `discover` | `dr-rustup`: `external_reference=1`（`toolchains`，cap=`rust-toolchain`）+ unmanaged 3；`dr-cargo`: unmanaged 1 | `files_touched=0` |
| `adopt ~/.rustup/toolchains`（**直接子项**） | ✅ `SUCCESS` | 随后 `where rust-toolchain` 命中**真工具链**（`active_version=1.98.1.0`） |
| `adopt ~/.rustup/toolchains/stable-x86_64-pc-windows-msvc`（孙项） | ❌ `INVALID_INPUT`(8) | `target.parent == 数据根` 不成立 |
| `adopt ~/.cargo/bin`（直接子项） | ❌ `CAPABILITY_NOT_DECLARED`(9) | 三个 shim **没有版本资源**（`wl-5` 的注释早记过） |
| `adopt ~/.cargo/bin/cargo.exe`（孙项） | ❌ `INVALID_INPUT`(8) | 同一条深度规则 |

**顺带纠正一处旧叙述**：`where rust-toolchain` 不是"只能指到 store 里的安装器"。把容器 `toolchains`
登记成引用之后，`where` 就指向真实工具链了（多版本与活跃版本本来就在那个容器对象上）。缺的是**深度**，
不是"数据根之外"。

两处**决定改动大小**的核心事实（读代码得到，不是推断）：

- `doctor` 的 reference 重观测**按路径**做（`caps/doctor.py` 的 `_check_references` →
  `classify_object(path, data_root_id=…)`），**不含任何深度假设**；
- `external_id` 当时是 `external/<数据根 slug>/<对象目录名 slug>`（`caps/discovery.py` 的
  `Candidate.external_id`）——它默认了"一层"，深度放开后同一数据根下两个同名目录会撞 id。

### 决定

**B —— 归属 = 最深的、包含目标的已登记数据根；id 变成地址。**

1. `adopt --mode reference <path>` 接受**任何**落在某个已登记数据根**之内**的目标；归属取**最深**的那个
   数据根（数据根可以嵌套，内层细化外层，而不是与外层竞争）。目标必须**严格在里面**：**数据根本身不是
   对象**（它是作用域），`adopt <数据根>` 被明确拒绝并说明原因。
2. `external_id` 由"数据根 + 目录名"改为"数据根 + **对象在其中的相对路径**"（逐段 slug、`/` 连接）。
   **深度-1 的 id 逐字节不变**（一段路径 join 出来的就是原来那个字符串），所以这条裁决之前登记的引用
   一个都不动，golden 语料也不需要重生。
3. `discover` 的对象粒度**不变**（仍是数据根的直接子项、即容器）。引用可以比它更细——这是两个问题：
   `discover` 回答"这棵树里有哪些对象"，`adopt` 回答"我要登记哪一个"。

### 被否决的路

- **自由引用（不在任何数据根里的对象）**：数据根是 `doctor` 重观测的上下文与**卷守卫**（`volume_serial`）
  的来源；没有它就没有"我在观察这棵树、但不拥有它"这句陈述，一条没有观测面的引用只能证明"这个路径当时存在"。
- **每个深度各登记一个数据根**：也不成立——要引用 `toolchains/<triple>` 本身，得登记它的**父目录**，
  而那正是已经登记过的 `~/.rustup`；再往下登记只能引用更深一层，永远差一层。
- **改 `discover` 的对象模型（把每一层都当对象）**：那会让 `D:\env` 这样的大根对象数爆炸，也会让
  "一个能力一个对象"的等价关系失效。容器粒度是对的，缺的只是引用粒度。

### 后果

- 现有 id 不动（逐字节），`test_matching_object_becomes_a_reference_candidate` 那类断言原样成立；
- `doctor`、`where`、`inventory`、`search` 都**不需要改**：它们按路径工作；
- **一处新增拒绝**：`adopt <数据根本身>`（以前掉进"不在数据根里"的模糊消息，现在说清是作用域不是对象）；
- 不传数据根的调用点（`capability check`）保持旧 id 形态（`relative_path` 为 `None` 时回落到目录名）；
- **没有新 schema、没有新 reason code、没有新 CLI 动词**；`Candidate` 多一个内部字段 `relative_path`，
  它**不进文档**（`to_document` 未变，故 discover 的 schema 与 golden 语料不动）。

**状态：已裁决（B —— 最深包含 + 地址化 id）。** 实现与实测记录见草案 **§140**。

## ADR-0052 — **owned payload 的入口点允许是相对路径**（把两个 schema 与其余部分对齐）

### 背景

`managed-tool-instance.entrypoints` 与 `runtime-instance.entrypoints` 每一项必须匹配 `^[^\\/]+$`，也就是
**裸文件名**；而 §144 把归档后端接上主线时，真机上第一次 `install` 就报
`SELF_VALIDATION_FAILED … entrypoints/0: 'bin/cmake.exe' does not match '^[^\\/]+$'`。

查了一圈之后，这条约束**是唯一的例外，而不是一条设计决定**：

| 位置 | 对入口点的处理 | 读数 |
|---|---|---|
| `caps/where.py` | `from_root_relative(f"{store_path}/{entrypoints[0]}", root)` | **在拼路径** |
| `caps/runtime.py` | `entrypoint = store_dir / Path(entrypoint_relative)` | **在拼路径** |
| `caps/toolstate.py` | 按 `entrypoints` 逐个查载荷里的文件 | 名字即相对路径 |
| `caps/discovery.py` | 观测到的入口点**就是相对路径**（`bin/python.exe`） | 见 `test_l1_discovery` |
| `caps/doctor.py` | 把**记录的**入口点与**重新观测的**（相对路径）逐个比较 | 两边必须同形 |
| **reference 一侧** | 测试里一直写着 `bin/java.exe`、`bin/flutter.bat`、`jdk-25.0.2/bin/java.exe` | **没有 schema 约束它** |
| 两个 owned-instance schema | `^[^\\/]+$` | **只有它说"裸名"** |

所以这**不是放宽一条有意为之的约束**，而是**修一处不一致**：除这两个 schema 之外，整个项目早就把入口点
当成"载荷根之下的相对路径"。

### 决定

**A —— 把 `managed-tool-instance` 与 `runtime-instance` 的入口点 pattern 改成相对路径，并显式拒绝能离开载荷根的形状。**

新 pattern（两份文件**同一条**，由测试钉住）：

```text
^(?!.*:)(?!.*(?:^|[\\/])\.\.(?:[\\/]|$))[^\\/]+(?:[\\/][^\\/]+)*$
```

- **接受**：`cmake.exe`、`bin/cmake.exe`、`bin\cmake.exe`、`jdk-25.0.2/bin/java.exe`；
- **拒绝**：`../escape.exe`、`bin/../escape.exe`、`/escape.exe`、`bin/`、`a//b`、空串；
- **拒绝任何含 `:` 的值**——这一条是写 pattern 时**试出来的**：第一版接受了 `C:/abs.exe`，而
  `Path("C:/abs.exe")` 在 Windows 上是**绝对路径**，`store_dir / entrypoint` 会**丢掉 store 前缀**去跑载荷外的东西。
  Windows 文件名本来也不允许冒号，所以这条拒绝没有代价。

**纯放宽**：新 pattern 是旧 pattern 的超集（每个裸名仍然匹配），所以**没有任何既有文档因此失效**，
不需要新 schema id，也不需要语料重生（实测：`golden.py` 重生后 **0 个 fixture 变化**）。

### 被否决的路

- **新增可选字段 `payload_root`**（§144.5 曾倾向的那条）：表达力相同，代价却是**一个 DB 列 + 一次
  migration + `runtime`/`toolstate`/`launcher` 三处 join**，而且要把"载荷根"这个概念在 schema、DDL 与
  文档里各说一遍。**§144.5 当时把 (c) 排在前面，是因为把这条 pattern 当成了有意为之的约束；它不是。**
- **解压后扁平化**：cmake 这类"exe 靠同级/上级 `share`"的工具会跑不起来（§144.5 已记）。
- **让后端只报裸名**：那就得把树压平，或者让 `expose` 撒谎——后者比前一条更糟。

### 后果

- 归档载荷可以被**如实描述**：实例的 `entrypoints` 是 `["bin/cmake.exe"]`，`store/<instance>/bin/cmake.exe`；
- 参考一侧与 owned 一侧终于同形，`doctor` 的"记录 vs 重观测"比较不再依赖两个不同的约定；
- **两个 schema 文件各改一处 pattern**，`docs/schema/README.md` 的兼容性修正节记一条；
- 没有新 reason code、没有新动词、没有 DB 变更。

**状态：已裁决（A —— 相对路径，且拒绝 `..` 与冒号）。** 实现与实测记录见草案 **§145**。

## ADR-0053 — **脚本入口点这一版不冻结**（身份词表没有能说它的值）

### 背景

能力面「冻结并识别真正在用的能力」的结账读数（§149 实测，临时 root，全程只读）：

| 数据根 | 识别到的对象 |
|---|---|
| `D:\env` | `build`(cmake)、`ffmpeg`、`java`(Java)、`node`(nvm4w) |
| `D:\env_apps` | `git`(Git)、`python`(miniconda3) |

**这台机器上每一个以 PE 镜像为入口点的能力都已经被冻结并被识别。** 唯一在用的例外是 `flutter` 与
`flutter_oh`：它们的入口点是 `bin\flutter.bat` 与 `bin\dart.bat`，是**脚本**。

三处实测，都不是推断：

1. `caps/discovery.py` 的对象入口点匹配在 `if not metadata.is_pe: continue` 处**跳过任何非 PE 候选**，
   所以 `.bat` 无论白名单条目怎么写都不会被匹配；`probe_executable` 对脚本返回 `is_pe=False`、其余字段全空。
2. **执行面不需要改**：`caps/runtime.run_once` 拼的命令是 `[entrypoint, *arguments]`，而实测
   `subprocess.run(["<synthetic>.bat", "arg"])` 在这台 Windows 上**成功**（退出码与 stdout 都对）。
   所以"脚本能不能被 AIROOT 跑起来"不是障碍。
3. 障碍是**身份词表**：`common.schema.json` 的 `externalReference.source_kind` 只有
   `declared† / pe_static / public_locator† / extension_handler† / approved_execution†` 五个值，而这一版
   **只写 `pe_static`**，它的含义是"这条引用的身份来自只读 PE 静态探测"。一个脚本的身份来自"名字 + 同目录
   下的兄弟文件"，**没有值能说这件事**。把 `pe_static` 借给它，正是 §115 记下的那个模式：借一个值只因为
   看起来差不多，正是词表开始失去意义的方式。

### 决定

**A —— `flutter` 与 `dart` 这一版不冻结；障碍记在词表上，不在扫描上。**

分工要写清楚：**不是**"脚本不能当入口点"（第 2 条实测说能跑），**不是**"谓词写不出来"（既有的
`executable_name` + `sibling_file` 两条就够：两份发行版的 `bin\` 共有 `dart`/`dart.bat`/`flutter`），
而是"识别出来的东西没法被如实记账"。

**B —— 放宽扫描的门要连带改词表，所以不顺手做。** 把那条 `is_pe` 门放宽成"条目只要声明了非 PE 证据就允许
匹配"是很小的改动（`pe_static` 谓词对非 PE 本来就返回 `False`，那条门对正确性是冗余的）——但改完之后
`source_kind` 只能填 `pe_static`，而那是假的；改枚举按 AGENTS §7 要**新的 schema id**，那是一次契约动作，
不该塞在一个"识别 flutter"的条目里捎带做。

**C —— 但把缺口做成可查的读数。** 真机验收脚本现在（§149）：

1. 把 `D:\env_apps` **加进隔离快照**（顶层清单）——它此前既不是数据根、也不在快照里，而 `python`/`git`
   就住在那里；
2. 登记它（只读）并断言能力账本 `{build, ffmpeg, java, node, git, python}`；
3. 断言 `flutter`/`flutter_oh` **被看见但没有被命名**——这既是诚实的当前状态，也是词表将来长出新值时的
   **绊线**：那一天这条断言会红，提示去把它认下来，而不是去放宽过滤。

### 代价与解锁

- 代价：这台机器上有一个在用的能力面对象**记不进账**。§141 修的是"看得见却没人命名"，这一条是
  "看得见、也说得出为什么还不能命名"。
- 解锁的最小一步：给身份字段加一个"静态名字 + 兄弟文件匹配"的值 → 新 schema id → 加白名单条目 → 冻结
  `flutter`。**`dart` 不单独冻结**：它随 Flutter SDK 一起发布，与草案 §11 把 conda 发行版当"既 runtime
  又是包管理器"属于同一类问题，先按"属于 flutter 发行版"处理。

**状态：已裁决（A —— 不冻结；B —— 不顺手放宽扫描门；C —— 缺口进真机验收）。** 实现与实测记录见草案 **§149**。

## ADR-0054 — **计划的 `kind` 从冻结能力清单推出，`runtime-instance` 因此第一次有了写者**

### 背景

`runtime-instance.schema.json` 是一份**已发布却没有写者**的契约：没有任何代码构造过它（ADR-0026 与
`docs/schema/README.md` 都写着这一条）。追下去发现原因不在运行时那一侧，而在**计划层**：

| 位置 | 读数 |
|---|---|
| `cli.py` 三处 + `tx/simulate.py` 一处 | `create_artifact_plan(..., kind="managed_tool")` **写死四处** |
| `capabilities.json`（`cap-4`） | `python`/`node`/`java` 的 `kind` 是 **`runtime`** |
| `tx/artifact.py` / `tx/simulate.py` | payload 一律用 `managed_tool_payload` 构造，`validate_self("managed-tool-instance", ...)` 也写死 |

所以**两个 artifact 在互相矛盾**：冻结清单说 `python` 是 runtime，而给它建的计划自称 managed tool。
后果是 `runtime-instance` 永远不可能被生产——不是因为运行时做不了，而是因为**没人会计划一个运行时**。

### 决定

**A —— 计划的 `kind` 由冻结能力清单推出，不再写死。** `caps/boundary.py` 的
`plan_kind_for(capability_id)` 读那份清单（`load_capabilities()`，白名单加载期已经在用同一个来源）。

**B —— 两套词汇不许混用，映射写在唯一一处。** 能力清单说的是**能力**的种类（`tool` / `runtime`），
计划与实例说的是**AIROOT 拥有什么**（`managed_tool` / `runtime`）。§150 的第一版把
`declared_kind` 的结果直接当实例 kind 用——**七个守卫同时红**（`test_cli_steward`、`test_l1_desired`、
`test_l1_sources`、`test_l1_agent_read_fields` 与三个 census 守卫），因为 `tool` 不是任何 owned schema
描述的种类。映射 `INSTANCE_KIND_BY_CAPABILITY_KIND` 因此单独存在，且对未知种类**失败关闭**。

**C —— `runtime-instance` 有了写者：`registry/entities.py::runtime_instance_payload`。** 它按**那份
schema 自己的**形状构造（`runtime_id`/`runtime_family` 而不是 `tool_id`，且那份 schema
`additionalProperties: false`），不是把 managed-tool 的构造器换个标签。

**D —— 校验按 kind 走，但两个 schema 名保持字面量。** 两个 runner 各写死一次
`validate_self("managed-tool-instance", …)`；§150 第一版把它换成经变量的间接调用，结果**两个 census
守卫都红**，报"两个 instance schema 都没有写者"——因为那份审计是**从校验调用点上的字面量**推导的。
所以只有一个 `validate_instance(payload, kind=…)`，两个名字**都字面写在里面**：一处定义，且推导仍然
看得见（改守卫去迁就变量是本项目禁止的那条路）。

### 代价与边界

- **写者有了，操作者的路还没有。** `runtime-instance` 现在由任何 `kind=runtime` 的计划生产，而
  `plan`/`pin` 会真的按 `python`/`node`/`java` 产出 `kind=runtime`；但**要让这个计划在一个真实运行时上
  成立**，还缺两样：`policy/sources.json` 里没有任何 runtime 的来源，而 `adopt --mode import` 对
  `kind=runtime` 的能力**按 §12.1 拒绝**（它绑机器级、问不了"装哪儿"，所以拒绝而不是把门做成装饰）。
  这一条**不在本 ADR 的范围内**，它是下一个决策。
- 语料多一份 fixture（`runtime_instance.json`，43 → 44），因为一份自校验却没有 fixture 的文档没有验收面。
- `test_l0_consistency` 里那个用来证明"有 fixture 却没人自校验"方向的**合成变异**因此失效了（它点名的
  `runtime-instance` 现在两边都在），换成了一个仍然两边都不在的名字——**变异不再变异就必须换掉**。

**状态：已裁决（A/B/C/D 全部落地）。** 实现与读数见草案 **§150**。

## ADR-0055 — **§12.1 的确认门这一版没有作答机制**（一个"必须确认"的类，却给不出答案）

### 背景

P5 的最后一步是让操作者装一个**真实运行时**。§151 把来源接上了，`source resolve node` 真的解析成功，
而 `plan` 报：

```text
exit=4  SCOPE_CONFIRMATION_REQUIRED
message: installing node needs confirmation before a plan may be produced
evidence: installing a runtime creates an environment, and where it lives is a real choice
          options: project-isolated / data-root / cancel
          origin=high_risk
```

于是**再给一次显式答案**：`plan node --scope data-root --target data-root:dr-node`。**结果一样**——
`SCOPE_CONFIRMATION_REQUIRED`。查代码：

| 位置 | 读数 |
|---|---|
| `caps/planner.py:347-366` | 高危类（`creates_environment` / 超阈值 / **`kind=runtime`**）一律 `confirmation=True`，**与调用者给了什么无关** |
| `cli.py:2249-2260` | `if decision.confirmation_required:` 直接抛，**从不读 `args.scope`** |
| `cli.py:2250-2251` | 拒绝的理由是"未确认的计划落到磁盘上可能被另一条路批准" —— 那是**没作答**的情形，不是**已作答**的情形 |

所以 §12.1 那三类"必须确认"里，**运行时装环境这一类在整个 build 里无法被确认**：它打印三个选项，
而**三个选项一个都给不出来**。`adopt --mode import` 那条拒绝（§12.1 的同一道门）当时写下的理由是
"import 固定绑机器级、问不了'装哪儿'，所以拒绝" —— 那一句是**对的**，错的是它的推论：`plan` **能**问，
但没人接得住答案。

### 决定

**A —— 这是**产品面**的缺口，不是能力缺口，而且它必须被记成缺口而不是绕过去。** 运行时本身没有做不了的事：
来源、后端、解包、`kind=runtime`、`runtime-instance` 写者、`gc` 全都就位，**只差一次确认**。绕过它的
两条近路都被否决：
- **在 `import` 上开一个 `--confirm`**：那正是 §12.1 拒绝过的东西（把三道高风险门里的一道变成装饰）；
- **让 `--scope` 隐式作答而不记录**：那会把"谁决定它住在哪"从账本里抹掉，而 §20.3-2 的整条理由就是要
  让它可查。

**B —— 最小修法写在这里，留给下一个阶段落地（不在本 ADR 里实现）**：`cmd_plan` 的拒绝条件是
**"没作答"**而不是"这个类需要确认"，也就是

```text
if decision.confirmation_required and not args.scope:   # 才是没作答
```

并且当调用者**给了** scope 时，把"这次确认是显式给出的"写进计划的 `metadata.routing`（现在那里只有
`confirmation_required: true`，读起来像"没人确认过"）。这一条改的是**一次拒绝的触发条件**，
不是那道门本身——没给 scope 的调用**照旧**被拒。

**C —— 缺口进账本。** `AGENTS.md` §8 的"尚未实现"里多一条（在这一版，`kind=runtime` 的能力**无法**产生计划）；
草案 §152 记读数与那条最小修法。

### 代价

- 代价：P5 的"装一个真实运行时"这一条**这一版仍然走不到头**，而且原因是一处**表面缺口**——读代码的人
  很容易把 `SCOPE_CONFIRMATION_REQUIRED` 读成"设计如此"，而实际是"设计如此、但没人接答案"。
- 不在本 ADR 里实现它的理由：它要改的是一个**已经发布并被守卫盯住的拒绝路径**，而这一轮的上下文预算
  不足以把它连同真机整链一起验完；**分两轮做，比一轮做一半好**。

**状态：已裁决（A —— 记成缺口；B —— 最小修法给出；C —— 进账本）。B 已于 **§153** 落地**：确认门现在接受显式答案（`--scope`/`--target`），没作答的调用照旧被拒，答案记进 `metadata.routing.confirmation_answered_by`；真机上第一个 owned runtime 因此装成（§153.3）。

## ADR-0056 — **回答不等于改写路由**：作答的 scope 与路由决定不一致时拒绝，`--target` 自己就能说明它是什么

### 背景

ADR-0055 的 B 让确认门接受显式答案，§153 落地之后，§154 量了"给了答案会怎样"，结果是**答案被收下、
却没有被执行**（临时 root，`plan node`）：

| 调用 | `routing.requested_scope` | `routing.decided_scope` | `routing.target_path` | `target.binding_key` | 退出码 |
|---|---|---|---|---|---|
| `--scope data-root --target data-root:dr-node` | `data-root` | `data-root` | 那个数据根目录 | `machine/node/windows/x64` | 0 |
| `--scope project --project <dir>` | `project` | **`data-root`** | 那个 project 目录 | `machine/node/windows/x64` | **0** |
| `--scope machine` | `machine` | **`data-root`** | **空字符串** | `machine/node/windows/x64` | **0** |
| `--target <dir>`（不给 `--scope`） | `machine`（**默认值，没人说过**） | `data-root` | 那个目录 | `machine/node/windows/x64` | **0** |
| `--target data-root:dr-node`（不给 `--scope`） | `machine`（同上） | `data-root` | **字面串 `data-root:dr-node`** | `machine/node/windows/x64` | **0** |

两件事因此分开：

1. **`data-root` 不是"绑定 scope"**（`caps/inventory.py` 的 `SCOPES` 里没有它，`tx/artifact.py:506` 硬写
   `binding_key(capability_id, "machine")`）。计划决定的"装给谁用"与 binding key 的"哪个 key 上的 active
   实现"是**两套词汇**，它们不一致不是缺陷；
2. **但"作答的 scope"与"路由决定的 scope"是同一套词汇里的同一个问题**，而上面五行里四行都在回答之后
   被别的规则覆盖掉了：`planner.py` 的高危规则**无条件**返回 `SCOPE_DATA_ROOT`，从不读请求的 scope；
   `cli.py` 只覆盖了**一个方向**（请求 `data-root`、决定 `project` 算放宽，要批准）。§154 记成缺陷。

### 决定

**A —— 一道门，两个方向。** `upgrading`（请求 `data-root`、决定 `project`）已经是"作答与决定不一致 ⇒ 拒绝"，
只是拼成了一个方向。补上另一半：**`confirmation_required` 且已作答、且作答的 scope ≠ 决定的 scope ⇒ 拒绝**，
复用 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4），证据里写清 `requested_scope` 与 `decided_scope`
（ADR-0055 选这个码而不是新码，这里沿用同一条理由：**拒绝是诚实的结果，而新码要连带改码表、守卫与语料**）。
**不选的方案**：按请求的 scope 做——那会把 `planner.py` 的高危规则架空，而那条规则是 §12.1 的一部分。

**B —— `--target` 自己就说明了它是什么。** `--target <dir>` 是**项目**、`--target data-root:<id>` 是**数据根**；
`--scope` 是同一个问题的另一种拼法，两者同时给出且互相矛盾时报 `INVALID_INPUT`(8)（不是 4：那是"你的输入
自相矛盾"，不是"要批准"）。这一条不是顺手改的：**它是 A 的证据要成立的前提**。不补它，上表第三、四行
的 `requested_scope=machine` 就是**默认值冒充调用者的意思**，拒绝时打印"你答的是 machine"会是编的；
第五行的 `target_path` 也仍然是一个**不指向任何目录的路径**。

**C —— 报告必须说这次调用会做什么。** `--dry-run` 的裁决必须与真调用一致，**两个方向都算**：
- 作答与决定不一致 ⇒ dry run 报 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（原先报 `SCOPE_CONFIRMATION_REQUIRED`，
  读起来像"再答一次就好了"，而真调用会拒绝）；
- **作答与决定一致** ⇒ dry run 报 `SUCCESS`、`required_approval=none`（原先报 `SCOPE_CONFIRMATION_REQUIRED`，
  读起来像"还没人确认"，而真调用**会**产出计划）。

第二半是 A/B 之外**必须一起做**的：不做它，"`--scope` + `--dry-run`"与"`--scope`（不带 dry-run）"
这一对调用会给出两个相反的裁决，而真机验收脚本里那一对是**相邻两行**——它自 §153 起就在断言旧的
`code == 4`（见代价）。

### 代价

- **两条曾经能出计划的调用现在被拒**（`--scope project`、`--scope machine` 落在 data-root 决定上）。
  这是有意的，但它是**行为收紧**，而 ADR-0021 的默认是放宽：这里按"审计守卫 + 诚实规则"处理——
  被拒的不是权限，是**一句假话**（记下 `requested_scope=project` 然后绑机器级）。
- **`machine` 不再被静默接受**：`--scope machine` 对 `kind=runtime` 的能力过去出计划、绑定也是 machine，
  两个字段一致所以看着没问题；但它跳过了 §12.1 那道门（"运行时装哪儿"是个真实选择），现在被拒。
- **真机验收脚本里两条断言自 §153 起就是假的**，而它不进 pytest，所以没有任何守卫变红：`plan java
  --scope data-root ... --creates-environment` 与它的 `--dry-run` 版本都被断言成"必须确认"（退出码 4），
  而 §153 之后真调用已经出计划（退出码 0）。§155 量到它、改了它，并把"没作答才被拒"这一条**换成**
  脚本里真正要盯的那个调用。**这是一条可以被复用的教训**：`real_machine_acceptance.py` 不是 pytest 模块，
  所以任何"改了 `plan` 的裁决"的阶段都必须**手动重跑它**，§153 没有跑。
- **没有做**：`--scope data-root` 仍然绑 machine 级（上表第一行，§154.1 已裁决为两套词汇、不是缺陷）；
  计划 scope 与 binding scope 的合并**不在本 ADR 的范围**内。

**状态：已裁决并已落地（A、B、C，草案 §155）**：`plan` 的作答与决定不一致时拒绝并报两个 scope；`--target`
单独给出时自己说明是项目还是数据根；`--dry-run` 的裁决与真调用一致。守卫是 `cli/tests/test_l1_plan_routing.py`
的五条新用例，五条都做了合成变异验红（两半各自把自己的用例变红）。**同一轮里修掉并重跑了真机验收脚本那两条
陈旧断言**（§155.4）。

## ADR-0057 — **计划的版本来自解析结果，而不是一个默认值**（一个"是 3.31.6 却登记成 1.0.0"的实例）

### 背景

§148、§153、§155 三次真实 artifact 链**每次都显式传了 `--version`**（§148 的 `plan build --version 3.31.6
--source-json`、§153.3 的 `--version 22.14.0`），所以"不传会怎样"从来没被量过。而 `AGENTS.md` §6 教的命令
恰恰是不传的那一条：

```text
python -m airoot --root <root> plan build --source-json <resolved.json> --json   # 构造真实 artifact 计划
```

§156 量了它（离线本地 release，`build` 3.31.6；`--version` 的默认值是 `1.0.0`）：

| 调用 | `target.version` | `target.instance_id` | `plan_id` |
|---|---|---|---|
| `plan build --source-json <resolved>` | **`1.0.0`** | `build/cmake-3.31.6-windows-x86_64/**1.0.0**/win-x64` | `plan/build/1.0.0/…` |
| `plan build --version 3.31.6 --source-json <resolved>` | `3.31.6` | `…/3.31.6/win-x64` | `plan/build/3.31.6/…` |
| `plan build --version 9.9.9 --source-json <resolved>` | **`9.9.9`** | `build/cmake-3.31.6-windows-x86_64/**9.9.9**/win-x64` | `plan/build/9.9.9/…` |

解析结果**自己带着 `version`**（`ResolvedSource.to_document()` 第 142 行），而计划里没有任何一处读它：
`metadata.source_catalog` 只有 `checksum_url`/`checksum_format`/`offline`/`provenance_and_integrity_are_separate`/
`signature`。于是**一份文档里两个版本可以不一致**，而**没有任何一处比较它们**。

**用户可见的症状**（同一次实测，装完之后）：

```text
install → FINALIZED  instance=build/cmake-3.31.6-windows-x86_64/1.0.0/win-x64
where build                      → version=1.0.0
where build --version ">=3.31"   → exit=1 VERSION_UNSATISFIED
tool list                        → version=1.0.0
```

**刚装的 cmake 3.31.6 用版本查询找不到。** 这不是显示问题：artifact 的名字是 3.31.6，实例的身份说 1.0.0，
而 `where` 的版本选择读的是身份。

### 决定

**A —— 有解析结果时，解析结果是版本的唯一来源。** `--source-json` 给出时：`--version` 缺省 ⇒ 取
`source_document["version"]`；两者都给且不一致 ⇒ `INVALID_INPUT`(8)，证据写 `requested_version` 与
`resolved_version`（同 §155 对 `--scope`/`--target` 矛盾的处置：**两个版本是一条调用的两个答案**，
不是偏好问题，所以按诚实规则走，不走 ADR-0021 的放宽）。

**B —— 没有解析结果时保持 `1.0.0`。** 模拟路径的 payload 就是 `cache/fixtures/<cap>/1.0.0`，语料钉着
`plan/fake-tool/1.0.0`；把默认值改成 `None` 只是为了让"这个 `1.0.0` 是兜底"这件事写在代码里，而不是
写在 argparse 的默认参数里。

**C —— dry run 也读这份解析文档**（在路由之后、dry run 之前），所以它报的版本就是真调用会用的版本
（§155 的同一条规则）。代价是一处收紧：`--dry-run --source-json <不存在的文件>` 从"静默通过"变成
`INVALID_INPUT`(8)——而那正是真调用会给出的答案。

### 代价

- **`--version` 的默认值不再是 `1.0.0`**：给了 `--source-json` 的调用会拿到解析结果的版本（这正是要修的），
  没给的调用行为不变。
- **一次"三次真机链都传了参数"的教训**：§148/§153/§155 都没有漏，所以这条缺陷在**文档教的那条命令**上
  活了很久。**链上每一处都传了某个参数，不等于不传也对**——要验的是文档上的写法。
- **没有做**：`plan` 仍不校验 `--version` 与 `artifact_url` 里嵌的版本（URL 由 `sources.json` 的模板拼装，
  与解析结果自洽）；不新增来源签名/证明；`metadata` 里仍然没有"版本从哪来"的字段（`version_source` 只属于
  `adopt` 的信封，见 `references/field-values.md`）——**这一条留在这里，免得下一个读者以为它被声明过**。

**状态：已裁决并已落地（A、B、C，草案 §156）**：守卫是 `cli/tests/test_l1_sources.py` 的四条新用例
加全链测试里的 `where --version ">=3.31"` 断言，三处合成变异各自只把自己那一半变红。

## ADR-0058 — **建 root 的文件那一半不需要受保护状态**（`root init`，不是 `bootstrap`）

### 背景

"用 skill + cli 到底能做到什么"这个问题被追问到第一分钟时，答案是没有第一分钟：**每一条命令都要求先有
一个 root，而建 root 没有动词**。实测（空目录）：

```text
$ airoot --root <空目录> root status --json
ROOT_MARKER_MISSING (exit 6)
message : root marker missing: <...>\state\root.json
evidence: the root must be created by bootstrap (P2) or by a test fixture
```

`bootstrap` 在动词位置被拦下（`NOT_IMPLEMENTED`(1)，ADR-0027），`agents/airoot.json` 给的理由是：

> A real bootstrap creates the root marker and the ACL layout, and that is protected state.

**这句话把两件事捆在一起，而它们的归属在 ADR-0045 之后已经分开了**：
- 写一个 root marker（+ 目录布局 + 一个空 registry）在 `policy_only` 下**本来就不受保护**——同一个用户
  身份写一个 JSON 文件而已，正是 **ADR-0050** 用来把"稳定入口"从 P2 里放出来的那条论证；
- ACL 布局与 machine PATH 条目**仍然**是受保护状态（ADR-0045 归于使用方 / 可选 P9）。

同型论证没有应用到 root marker 上，于是这一半在整个 build 里都没有动词，而 `SKILL.md` 的第一步恰好就是
`airoot root status --json`——**一个第一次拿到 AIROOT 的人/上游 harness 读完 skill 后无处可去。**

### 决定

**A —— 新增 `airoot root init <path> --root-instance-id <id> --machine-id <id>`，只做文件那一半。**
建 `LAYOUT_DIRS` + `state/root.json` + `state/registry.db`（含投影），报告 `directories_created`、
`marker`、`registry`、`volume_serial`、`path_written: false`。**不布 ACL、不写 PATH、不提权。**

**B —— `bootstrap` 这个名字不动，也照旧报 `NOT_IMPLEMENTED`。** 它在规划 §15.1 里带着提权语义，而
`not_implemented` 的六条与守卫第三十五组逐字盯着它。把文件那一半塞进这个名字，等于让"受保护的那半"
看起来已经做了——那正是这个项目一直在防的事（§119 的措辞漂移、§115 的借码）。`deferred["bootstrap"]`
的 `why` 改写为"marker 那一半是 `root init`，剩下的是 ACL/machine PATH"，`category` 与 `unblocked_by`
**不变**（两个登记表必须逐字相同）。

**C —— 两个身份是必填入参，不设默认值。** `machine_id`/`root_instance_id` 的生成算法是 ADR-0025 明确
留下的开放项（"只接受注入或显式入参，不从硬件指纹推导"）。一个"贴心的默认值"就是 CLI 替项目回答一个
刻意没回答的问题——ADR-0057 记的是同一种病的另一种形态（默认值冒充调用者的意思）。

**D —— 拒绝"非空且没有 marker"的目录。** `init_root` 只拒绝已存在的 marker；没有这条守卫时，把
`D:\env` 误当 root 传进来的调用者会得到 AIROOT 的 layout 铺在自己那棵树的顶层——**非破坏、但静默**。
这条收紧按 ADR-0021 的"诚实规则"处理：它挡的不是权限，是一次会让用户事后才发现的事故。

**E —— 这是唯一一条不需要 root 就能跑的命令**（`Context(resolve=False)`）。它的路径来自自己的位置参数；
root 解析那条"**绝不回落到当前目录**"的规则没有被放宽。

### 代价

- `Context` 多了一个模式，`root_path` 变成可选：`registry()`/`path()` 在无 root 时改报 `ROOT_NOT_RESOLVED`(8)
  而不是把 `None` 传下去（这一条同时让"哪条命令在没有 root 时失败"变成**一个**答案）。
- **`bootstrap` 仍然不可调用**，所以"受保护模式的一次性窗口"仍然不存在。这一条不假装：`root init`
  建出来的 root 是 `policy_only`，文档与它的输出都这么写。
- **没有做**：身份生成算法（仍开放）、`root relocate`/`root adopt`（受保护状态）、ACL 布局、machine PATH。

**状态：已裁决并已落地（A–E，草案 §157）**：五条新用例在 `cli/tests/test_cli.py`，三处合成变异各自
只把自己那一半变红；`SKILL.md` 的第一步与命令地图各多一条出口；lane 36 → 37。

## ADR-0059 — **`--dry-run` 在四条命令上是一个意思：什么都不改**（`uninstall` 是第五条）

### 背景

§159 的全表面真机测试量到（我在临时 root 上逐条复现）：

```text
$ airoot uninstall <owned instance> --dry-run --json
exit=0  dry_run=true  payload_removed=false
        lifecycle_status=retired        ← 绑定真的被撤了
        plan={…}  plan_file=…           ← 而且计划也落盘了
$ airoot where archive → NOT_FOUND (exit 1)
```

代码在 `cli.py` 的 `cmd_uninstall`：`uninstall_target()`（它调 `retire()`）在 `if args.dry_run` **之前**
无条件执行；`--help` 写的是 `--dry-run  retire and print the plan`——**帮助文本描述的是"撤绑定并打印计划"，
而 flag 的名字承诺的是"什么都不发生"，两者被当成了同一件事。**

**恢复路径实测是断的**（四连败）：

| 尝试 | 结果 |
|---|---|
| 同一份计划 + 同一 token | `INVALID_APPROVAL`（token 已消费） |
| 同一份计划 + 新 token | `INSTANCE_CONFLICT` / `ROLLED_BACK` |
| **同版本**的新计划 + 新 token | `INSTANCE_CONFLICT` / `ROLLED_BACK` |
| **换一个版本**（9.9.10） | `FINALIZED`，`where` 恢复 |

也就是说：一次"只想看一眼"的调用，代价是**必须装另一个版本**才能拿回可用绑定。

### 决定

**A —— `--dry-run` 必须先分支、且不碰 registry。** 输出 `dry_run=true`、`lifecycle_status`（原样）、
`payload_removed=false`、`plan=null`、`would_retire=true`，并在 `required_action` 里点名
**`airoot tool retire <id>`**：gc 计划是**从已 retired 的实例**建出来的，所以 dry run 给不出计划——
给不出就如实说，而不是先替调用方把状态改掉再给。

**B —— `--help` 跟着改**：新文案 `report what uninstall would do and change nothing (a gc plan needs a
retired instance)`。**帮助文本与 flag 名不一致，本身就是这条缺陷的一半。**

**C —— `uninstall <id>`（不带 `--dry-run`、不带 token）行为不变**：completed retire 半场 + 计划落盘 +
`APPROVAL_REQUIRED`(4)。那是 skill 教的**分级路径**里"显式 retire"的合体写法，输出用 `required_action`
说清下一步；"删除"仍然只发生在带 token 的 `--apply`。

**D —— 不采用"改名成 `--retire-and-plan`"。** 它会保留副作用、新增一个只在这一条命令上存在的拼写，
并让历史文档里所有 `uninstall … --dry-run` 变成未定义。**`--dry-run` 这个词在四处（`plan`、`env persist`、
`env forget`、`tool gc --plan`）都是"什么都不改"**，把第五处对齐比给它开一个新名字便宜。

### 代价

- **`--dry-run` 不再给出 gc 计划**：想要计划的调用方必须多跑一步 `tool retire`。这正是分级路径本来的
  形状（skill 就是这么教的），但它是**行为收紧**——按 ADR-0021 的四种例外处理：它挡的不是权限，是
  **一次会静默撤绑定的副作用**（诚实规则）。
- **没有做的**：给 `--dry-run` 加"预览会删掉什么"的新能力（那需要从实例反推 store 路径，属另一件事）；
  F2/F3/F5/F6 与 F4 的裁决（各自阶段）。

**状态：已裁决并已落地（A–D，草案 §160）**：新守卫 `test_cli_uninstall_dry_run_changes_nothing` 覆盖
"什么都没改 + 没留下半成品"；一处合成变异（回到先 retire 再判）把它变红（`assert 4 == 0`）；
`test_l1_agent_read_fields.py` 里 `uninstall` lane 的记录方式随之改为不带该 flag 的分级路径。

## ADR-0060 — **没人作答就别记答案，而 `recover` 是两种情形共用的一个词**

### 背景

§159 的全表面测试量到两条同型的"**记下的话不是真的**"（我在临时 root 上复现）：

**A —— `requested_scope` 记了一个没人给过的答案。** `_resolve_plan_target` 为了让 planner 总有目的地，
把缺省 scope 落成 `machine`；这个**缺省值**被写进 `routing.requested_scope`：

```text
$ airoot plan node --dry-run --json          # 谁都没作答
routing.requested_scope = "machine"          ← 没人说过
routing.confirmation_required = true
target.scope = "machine"
（没有 confirmation_answered_by）             ← 唯一的诚实信号
```

§153 加 `confirmation_answered_by` 正是为了区分"没人答"与"有人答"，而同一个文档的另一半仍在声称一个
调用方从未做出的选择。

**B —— `remediation: "recover"` 覆盖两件出路完全不同的事。** `caps/doctor.py` 里它出现 7 处：

| 情形 | 诊断 | 能动它的动词 |
|---|---|---|
| journal 可回放 | `PENDING_TRANSACTION` / `RECOVERY_REQUIRED` / `JOURNAL_TRUNCATED` | `airoot repair` |
| **权威或身份坏了** | `ROOT_MARKER_MISSING` / `ROOT_MARKER_INVALID` / `VOLUME_IDENTITY_MISMATCH` / `DATA_ROOT_VOLUME_MISMATCH` / `REGISTRY_MISSING` | **没有** |

实测第二类：删掉 `registry.db` → `repair` 与 `rebuild` 都报 `REGISTRY_MISSING`(6)；marker 坏 → `repair`
报 `ROOT_MARKER_INVALID`(6)。而 agent 面文档**只讲第一类**（`field-values.md` 说 "`recover` → 按 journal 做恢复"，
`SKILL.md` 说 `broken/recovery_required → 先跑 repair`）——一个 agent 照做会拿到硬失败，然后没有下一步。

### 决定

**A —— `requested_scope` 只在真的被请求时才记**（`answered` 为假时记 `null`）。dry run 报的
`target.scope` 改用**路由器决定的**那个 scope：没人请求时它等于 `decided_scope`，而不是缺省值。
判据很简单——`requested_scope` 的字面意思就是"调用方请求的"。

**B —— `recover` 的两种情形写进解释与 skill，但不改枚举。** `doctor-response.schema.json` 把
`remediation` 的取值钉死为 `["none","inspect","repair","rebuild","reapprove","recover"]`，**加一个值是
破坏性变更**（要新 schema id，ADR-0003）。所以这一版改的是**解释**：`references/field-values.md` 写明
`recover` 分两种；`SKILL.md` 的坏 root 分支由一条拆成两条（journal 三种 → `repair`；权威/身份四种 →
停下来交给操作者）。**不改代码**：`doctor` 的判断本身是对的（它不知道"有没有动词能修"，而
`repair` 修不了的那些它照实报）——错的是那份把它统一解释成"按 journal 恢复"的说明。

### 代价

- **A 是一处对外 JSON 的变化**：`routing.requested_scope` 从 `"machine"` 变成 `null`（只在**没人请求**时）。
  语料里没有 `requested_scope`（§161.3 查过），所以不需要重生；只读该字段的调用方本来也该先看
  `confirmation_answered_by`。
- **B 没有加守卫**：这两处是解释而不是枚举，真正的判据在 `doctor` 的代码里。把"解释与代码一致"做成守卫，
  需要给每条诊断登记一个结构化的"可否修复"，那是另一件事——**记下来，不假装做了**。
- **没有做的**：F2（`extension status` 的身份错位）与 F6/F4（各要一次裁决）、F7–F11（文档收口）。

**状态：已裁决并已落地（A、B，草案 §161）**

## ADR-0061 — **`extension status` 回答的是它自己的实现，而编码前缀不是身份**

### 背景

§159 的全表面测试留下三条"**报告讲的是别人家的事实**"（我在临时 root 上复现）：

**A —— `extension status <id>` 用另一个扩展的身份作答**（§159 F2）。`cmd_extension_status` 对**任意**
已知 id 都构造 `FakeExtension(manifest)`，于是假扩展硬编码的那份自证词被挂在别人的 `extension_id` 下：

```text
$ airoot extension status airoot-native-search-extension --json
exit=0  status=ok  reason_code=null
data.health=healthy  data.freshness=static
data.self_test.checks=["envelope","manifest","operation-policy"]   ← 假扩展的常量
evidence[0].detail="deterministic fake extension; no external state touched"  ← 逐字节相同
data.operations=["explain","refresh","search","status"]            ← 这条来自 manifest，所以信封"看着"合理
```

第二个 id 的 manifest 里 `health_checks` 是 `["probe","permission","index_integrity"]`，**没有 `self_test`**。
一个 agent 读到 `health=healthy`，会以为 `file_search` 在这台机器上是健康的——那是一句它无从判断的话。

**B —— `health` 是登记时记下的值，而读的人当它是重测值**（§159 F7）。`caps/toolstate.py:61` 就是
`str(row["health"])`：payload 已被 `gc` 收走的实例仍读 `healthy`，同一份文档里的 `payload_present=false`
与 `findings=[PAYLOAD_COLLECTED]` 才是那一刻的事实。这一条同一个 root 上还有一个**姐妹字段**：
`lifecycle_status` 也出现了两行同时读 `active` 而只有一个 binding 的情形（§162.7，记为候选）。

**C —— root marker 的 BOM 被当成身份问题**（§159 F9）。`read_marker` 用 `utf-8` 读自己写的文件；带 BOM
意味着**别人**编辑过它（Windows 上多数编辑器的默认产物），而结果是一份**可读**的 marker 报
`ROOT_MARKER_INVALID`（`Unexpected UTF-8 BOM`）。

### 决定

**A —— 判据是 manifest 自己的 `implementation_id`，不是扩展的名字。** 本 build 只宿主
`airoot-fake-deterministic`（`ext/fake.py` 里那个假扩展）；其余 manifest 的 `extension status` 报
`EXTENSION_UNAVAILABLE`(9)，并给出**推导出来的**证据（按该 manifest 自己的 `capability_types` 查
"这个能力在本 build 真的在哪里"）。两条没有采用的路：按扩展名字判（改名即失效，而名字与"谁会跑它"
没有关系）、改报 `EXTENSION_NOT_FOUND`（语义是"没有扩展提供这个 capability"，被 §159.3 记为"可辩护
但不准"）。**不可用 ≠ 被抹掉**：`extension list` 照旧列出它。

**B —— `health` 这一版保持"登记时记下的值"，不加重测写者。** 把记录值改成每次读盘重算是一次语义
变更：它会让 `health` 与"最后一次事务的结论"脱钩，而重测这件事在这个 build 里已经有它的位置——`where`
（会给 `NOT_FOUND`）与 `caps/health.py::observe_payload`（§147）。所以这一版改的是**解释**：
`references/field-values.md` 写明它是记录值并点名那两个真正重测的地方。**同一个问法对
`lifecycle_status` 也成立**（§162.7 的候选读数），那一半要一次专门的复现，不在本裁决内。

**C —— 用 `utf-8-sig` 读 marker。** marker 的身份是**字段值 + 卷序列号**，不是它的编码前缀；AIROOT 自己
写的那份不带 BOM，所以这条只影响"有人手工编辑过"的情形，而那正是诊断最不该消失的情形。这是 ADR-0021
"默认放宽"的直接应用。

### 代价

- **A 是一处对外行为的变更**：两个已知 id 里有一个从 `ok` 变成 `EXTENSION_UNAVAILABLE`(9)；不可宿主 id 的
  `--operation probe|invoke` 的**码**也由 `EXTENSION_OPERATION_UNKNOWN` 变成同一个码（两条都是真话，
  退出码都还是 9）。`EXTENSION_UNAVAILABLE` 早已注册、早已有写者，**不需要动码表、不需要重生语料**。
- **B 不加守卫**：文档与代码一致这件事今天只能靠读；做成守卫要给每个字段登记一个"记录值 / 派生值"的
  结构化标记，那是另一件事——**记下来，不假装做了**。
- **没有一个 schema 描述 `extension status` 的信封与 `root-marker` 的编码**，所以这一条不动契约层。
- **没有做的**：F6（稳定入口的第二个写者）与 F4（`DATA_ROOT_MISSING` 的档位）各要一次裁决；F12 候选
  （`lifecycle_status` 的两行 active）的完整复现。

**状态：已裁决并已落地（A、B、C，草案 §162）**：A 的守卫是 `test_l1_extension.py` 的四条（含一条
"改名不改变结论"的判据守卫与一条"不可用≠被抹掉"），验红为 `if False and not hosted_here(manifest):`；
C 的守卫是 `test_cli.py::test_a_root_marker_with_a_bom_is_still_readable`，验红为把编码改回 `utf-8`。

## ADR-0062 — **同一个"这个 id 没注册"，三个动词只能有一个答案：`NOT_FOUND`(1)**

### 背景

§159 F4 量到 `plan --scope data-root --target data-root:<没注册的 id>` 报 `DATA_ROOT_MISSING`，退出码
**6**，而 `exits.py` 给 6 的含义是 **transaction recovery required**（`EXIT_MEANINGS[6]`，诊断码表第 24 行
原文），`references/reason-codes.md` §6 给它的下一步是"**停止**，先 `repair`"。同一个"这个 id 没注册"，
另外两个动词早就报 `NOT_FOUND`(1)：`discover --data-root <id>`（`cli.py:471`）与
`data-root forget <id>`（`cli.py:421`），三者 message **一字不差**（`unknown data root: <id>`）。

调查把这一族拆成了**三种**情形，而不是两种：

| 情形 | 例子 | 由什么回答 | 该报什么 |
|---|---|---|---|
| 名字合法但**不存在**（注册表） | `plan --target data-root:dr-nope` | 注册表 | `NOT_FOUND`(1) ✅（本次改的就是这一格） |
| 形态**不合法**（读注册表之前） | `--target D:/somewhere` 配 `--scope data-root` | 语法 | `INVALID_INPUT`(8)（不变） |
| 名字合法、路径**不存在** | `--target D:/somewhere`（被当成 project） | 文件系统 | `INVALID_INPUT`(8)（不变） |
| **已声明**的数据根目录不见了 | 删掉 `D:\env\...` 之后跑 `discover` | 文件系统 + 注册表 | `DATA_ROOT_MISSING`(6)（**保留**，由 `discover`/`doctor` 发射） |

**`REASON_EXIT` 是 `dict[str, int]`**（实测 97 键、无重复、`code → exit` 单值），所以"同一个码在两个档位"
做不到；而 `plan --target` 那条路径**根本不去看**那个目录在不在（它只查注册表），所以它答的不是第四种情形。

### 决定

**没注册的 id 报 `NOT_FOUND`(1)，证据带标签。** 三条路径共用一个构造函数
(`cli.py::_unknown_data_root`)，message 与证据**逐字节相同**，证据从"裸 id 列表"改成
`known data roots: [...]`——一个裸列表没说清它是**什么**的列表。`DATA_ROOT_MISSING`(6) 保留它
真正的职责（已注册的数据根读不了），所以这一改不是"把两个情形合并"，而是**把第三个情形放到它该在的档位**。

**没有采用的三条路**：① 新增 `DATA_ROOT_NOT_REGISTERED`——要动 `exits.py` + 码表 + 两个 references
+ 重生成 golden 语料，而两类情形对调用方的**下一步动作完全相同**，买到的是更精确的报告不是不同的行为
（ADR-0055 拒绝同类扩张用的就是这份代价清单）；② 只改解释——只读退出码的调用方仍会被 6 引向 `repair`；
③ 收敛到 `INVALID_INPUT`(8)——与本条已有的"形态问题"混在一起，且与两个兄弟动词仍不一致。

### 代价

- 这是一处**对外行为变更**：`plan` 对未注册 id 的退出码由 6 变 1。钉住旧行为的用例只有一条
  （`test_l1_plan_routing.py::test_an_unknown_data_root_target_is_refused`），它在同一个提交里改写并
  加强了（新增"三条路径的 code / reason_code / message / 证据必须一致"）。
- **不动** `exits.py`、码表、schema、golden 语料：`NOT_FOUND` 早已注册且有多个写者。
- 它**推翻**了契约草案 §20.3-5 记下的错误码（原文保留并就地标注，理由与 ADR-0046 推翻 §113.6-6 同一做法）。
- **没有做的**：`plan --target <dir>` 被当成 project 时那条 `INVALID_INPUT` 的证据里只有 WinError 文本、
  没有"已知数据根/项目"这类指针（§159 F4 的附带读数）——它不属于本次那一格，**记在这里，不假装做了**。

**状态：已裁决并已落地（草案 §163）**：守卫是改写后的
`test_l1_plan_routing.py::test_an_unknown_data_root_target_is_refused`（含三动词一致性），验红为把那一行
改回 `DATA_ROOT_MISSING` → `assert 6 == 1`。：A 的守卫是
`test_l1_plan_routing.py::test_an_unanswered_plan_records_no_requested_scope`（两处断言 + 一处验红
`assert 'machine' is None`）；B 落在 `references/field-values.md` 与 `SKILL.md` 两处。





