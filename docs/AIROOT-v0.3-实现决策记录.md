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

## 尚未决策（仍属规划 §23 的未冻结项）

以下 P1 明确**没有**自行发明算法或语义，需要单独决策：

1. `machine_id`、`session_id`、`project_id` 的生成算法——P1 只接受**注入**或显式入参，
   不自动推导；
2. registry SQLite migration 工具、event 保留期、`logs/audit` 导出协议；
3. 根定位的卷标扫描（P1 只支持 `--root` 与 `AIROOT_HOME`，且失败即关闭）；
4. 受保护 issuer 的真实签名（P1 只有 `test_hmac_sha256`，`ed25519` 校验显式未实现）；
5. `exposure\bin` 中的 launcher（P1 的 `EXPOSED` 是"通过一次全新的 registry 读取观察到
   新 binding"，尚未写任何 launcher 文件）；
6. Native Search 的进程模型与 `file_search` capability 清单；
7. `.ai/tooling.json` 的**写入**通道——ADR-0004 §12.2 的"记住这次选择"目前只有读取路径；
   写入被刻意留给 P2 的 human approval 通道，否则等于给 Agent 一条绕过确认的路；
8. machine 级环境变量持久化（HKLM + 广播 + 新进程读回校验）——依赖 P2 的受保护 broker；
   `env persist --scope machine` 现在明确报 `PRIVILEGE_REQUIRED`（5），不假装成功。
