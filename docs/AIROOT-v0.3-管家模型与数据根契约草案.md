# AIROOT v0.3：管家模型与数据根契约草案

> 状态：**提案（待评审）**。本文件定义把 ADR-0004（reference-first 管家模型）落入规范层
> 所需的全部契约变更。在评审通过前，**不得**据本文件修改 `AIROOT-总体方案规划-v0.3.md`
> 或 `AIROOT-v0.3-三大核心契约方案.md` 的正文；本文件只列出应改的行与改法。
>
> **第二版**新增四块协议（§12–§15）：依赖分流与确认、环境变量持久化、删除语义分级、
> 能力边界声明。这四块解决"管家能否下载/删除/记录/管理"的完整闭环。

## 1. 定位与术语

AIROOT 是**受聘管家**，不是仓库主人。它管理用户已有的全部环境，但**解雇管家不得带走环境**。

| 术语 | 定义 |
|---|---|
| **steward 域（管家域）** | AIROOT 观测并登记、但**不拥有文件**的对象。默认主体 |
| **owned 域（自置域）** | AIROOT 自己下载、校验并拥有的 payload。**默认为空**，只有用户明确要求安装时才产生 |
| **data_root（数据根）** | 用户声明的、存放 steward 域对象的受保护顶层目录，例如 `D:\env`、`D:\tools` |
| **capability whitelist（能力白名单）** | 把"数据根里的某个对象"识别为某个 `capability_id` 的**证据谓词表** |
| **reference** | 一条 `external_reference` 记录：路径 + 观测事实 + 健康 + 漂移证据。**不取得所有权** |

两种域的差别只有一句话：**owned 的可证明性来自安装时的 digest 基线；steward 的可证明性
来自只读观测**。因此 owned 可以回滚，steward 只能报告漂移。

## 2. 对象域重排

```text
steward（默认，主体）
    discovered → classified → referenced → (stale | drifted | incompatible)
                                          → unadopted（只删引用）

owned（可选，默认为空）
    discovered → planned → staged → installed → verified → active
                                                        → retired → garbage_collectable
```

**硬规则**：

1. `store` 是 **owned 域**的唯一 payload 存储，且**默认可空**；它不再是受控对象的默认归宿。
2. steward 域对象**永不进入 `store`**，除非用户显式 `import`/`recreate`（那是一次所有权转移，
   必须经 plan + 批准）。
3. 删掉整个本体目录（`D:\AIRoot`）后，data_root 内的文件树与 PATH **必须完全不变**。

## 3. 数据根契约

### 3.1 注册

数据根必须**显式注册**，不能靠扫描所有磁盘（§9.4:750 "不默认递归扫描所有磁盘"）：

```bash
airoot data-root add D:\env   --role runtime --json
airoot data-root add D:\tools --role tool    --json
airoot data-root list --json
airoot data-root forget D:\env --json        # 只删登记，绝不动文件
```

每个数据根登记：`path`（canonical）、`role`（runtime|tool|mixed）、`volume_serial`、
`acl_baseline`（观测值）、`whitelist_revision`、`added_at`。

### 3.2 身份与守卫

- 数据根**不要求**放 root marker（它们是用户已有的目录，不是 AIROOT 创建的）；
  改为在 **registry** 里记录 `path + volume_serial`，由 `doctor` 校验一致性。
- **数据根可以位于任意本地卷**，不要求与 CLI root 同卷。理由：
  - steward 域的每个动作（`discover` 纯读、`adopt` 只记路径与入口 digest、`forget` 只删记录）
    **都不移动任何 payload**，卷与它们无关；
  - 需要同卷的只有 **owned 域的 staged → store 原子移动**，而 `store` 与 `tx` 都在 CLI root
    内部，天然同卷——所以那是"CLI root 内部"的约束，不是数据根的约束；
  - 记录**每个数据根自己的** volume serial，才能检测"这个数据根所在卷变了"
    （`DATA_ROOT_VOLUME_MISMATCH`）。强行要求同卷反而丢掉了跨盘漂移检测。
- 路径解析遵循 §52 的 root-relative 规则需要扩展：**steward 对象按 `data_root_id + 相对路径`
  序列化**，例如 `env:D:\env` 上的 `Java` → `data_root=dr-env, rel=Java`。JSON 输出默认给
  绝对 canonical path。
- 数据根**必须位于 CLI root 之外**——这一条保留（否则"本体 / 被维护对象"的分离就没有意义）。

### 3.3 禁止动作（必须可测试）

对数据根内的对象，AIROOT：

- **不得**删除、移动、重命名、覆盖、升级、修复；
- **不得**把数据根放进 machine PATH（只有 `exposure\bin` 进 PATH）；
- **不得**在没有显式 plan + 批准时写任何字节；
- **不得**把"未匹配白名单"的对象塞进 `inventory` 的主流列表（见 §4.3）。

## 4. 收窄规则：数据根 + 能力白名单

### 4.1 为什么必须收窄

`D:\env` 顶层混有 `Huawei_SDK`、`HuaweiDev_KeyStoreFile`、`nssm-2.24`；
`D:\tools` 顶层混有 `texstudio`、`WeMeet`、`Everything`、`Raspberry Pi Ltd`。
若不收窄，registry 与 `doctor` 会被无关对象灌满噪音，且 §9.6:789 明确要求
"服务、驱动、计划任务 → `excluded` 或 `quarantined`，不走普通迁移流程"。

### 4.2 白名单的键是 capability，不是目录名

**禁止**"名字像 Python 就是 Python"（§9.3:716）。每条白名单条目形如：

```json
{
  "capability_id": "python",
  "kind": "runtime",
  "evidence_all": [
    {"type": "executable_name", "any_of": ["python.exe", "python3.exe", "py.exe"]},
    {"type": "pe_static", "field": "ProductName", "contains": "Python"}
  ],
  "entrypoint_hint": ["python.exe"],
  "version_source": "pe_static:FileVersion | nearby:PYTHON_VERSION"
}
```

识别只允许使用 §9.9 的证据强度阶梯的**前三级**：

```text
1. 用户或受信 manifest 明确声明
2. PE/ELF 静态元数据、版本资源、架构        ← 主要手段（只读解析，不执行）
3. 已知公开 locator 的只读查询              ← 例：py.exe -0p（若用户显式开启）
4. 已注册 extension 的受限 inspect handler
5. 显式批准后执行版本查询                    ← v1 默认关闭
```

**P1/P2 都不实现第 4、5 级。** 第 2 级需要实现只读 PE 版本资源解析（P1 未实现，属本草案
新增工作量）。

### 4.3 未匹配对象

未匹配白名单的对象 → `unmanaged`（§9.5:762"只报告，不改变"），并且：

- 出现在 `airoot inventory --class unmanaged --json`（显式查询时才列出）；
- 不出现在 `doctor` 的常规诊断里，除非用户开启 `doctor --include-unmanaged`；
- 永不自动升级为 reference 或 owned。

### 4.4 白名单初稿（按 capability）

| capability | 识别证据（只读） | 你机器上的候选 | 建议分类 |
|---|---|---|---|
| `runtime:python` | `python.exe` + PE ProductName 含 Python | `D:\env_apps\miniconda3` | reference（注意：conda 发行版需单独判定见 §11） |
| `runtime:node` | `node.exe` + PE ProductName 含 Node.js | `D:\env\nvm4w` | reference（nvm 多版本 → 每版本一条） |
| `runtime:java` | `java.exe` / `javac.exe` | `D:\env\Java` | reference |
| `runtime:dart` | `dart.exe` / `flutter.bat` | `D:\env\flutter`, `D:\env\flutter_oh` | reference（两个发行版 → 两条） |
| `git` | `git.exe` + PE CompanyName 含 Git | `D:\env_apps\Git` | reference |
| `archive` | `7z.exe` / `7za.exe` | `D:\tools\7Z` | reference |
| `media_probe` | `ffmpeg.exe` / `ffprobe.exe` | `D:\env\ffmpeg-master-latest-win64-gpl-shared` | reference |
| `build`（待定 capability） | `cmake.exe` | `D:\env\cmake` | **待你确认**（§6 无此 capability） |
| `content_search` | `rg.exe` / `fd.exe` | 未发现 | — |
| `file_search` | — | `D:\tools\Everything` | **永不接管**：§12/§21 明确 Everything 只作 Native Search 的机制参考与性能基准 |

**默认 `excluded` 名单**（GUI / 服务 / 驱动，符合 §2.3 与 §9.6:789）：

```
texstudio  texstudio-source  WeMeet  Pencil  lceda-pro  Raspberry Pi Ltd
Xshell  Xftp  Clash.for.Windows-*  FlClash  nssm-2.24
Huawei_SDK  HuaweiDev_KeyStoreFile  hap_installer-*  WorkBuddy
```

**待你确认**（机器上有、但不属于现行 capability 清单）：`llama-swap`、`llamacpp-adaptive`、
`llm-guard`、`cc_switch`、`FlClash`/`Clash`（代理类）、`tectonic`（LaTeX）。
按 §6 的 capability 清单，它们**没有对应能力**，因此默认 `unmanaged`。
若要管家管它们，需要**先新增 capability**——而 capability 清单本身是规划 §23 的未冻结项。

### 4.5 白名单的存放与版本

- 位置：`cli\app\airoot\policy\discovery-whitelist.json`（**发布层**，随升级替换，ACL 只读）；
- 每个数据根登记 `whitelist_revision`，白名单升级后 `doctor` 报告"待重新对账"；
- 用户可在数据根内放 `.airoot-overrides.json` 覆盖/例外（**数据层**，用户可写，AIROOT 只读）。

## 5. reference 生命周期

```text
discovered → classified → referenced
                            ↓
              stale | drifted | incompatible
                            ↓
                        unadopted
```

| 动作 | 语义 | 禁止 |
|---|---|---|
| `airoot discover` | 只读扫描**已注册数据根**，按白名单分类 | 不写文件、不执行候选、不跑全盘 |
| `airoot adopt <path> --mode reference` | 建立 reference；不移动、不复制 | 不取得所有权 |
| `airoot unadopt <external-id>` | 只删 AIROOT 的记录 | **不删 source**；仍被 project manifest 引用时拒绝 |
| 漂移 | path 消失 / digest 变 / 版本变 / 架构不符 → `stale`/`drifted`/`incompatible` | **不自动修复、不自动升级、不自动删除**（§9.7:837） |

**一条 reference 的字段语义**（对应 `common.schema.json#/$defs/externalReference`）：

| 字段 | 含义 |
|---|---|
| `path` | 对象目录的绝对 canonical 路径（reference 指向**目录**，不是某个 exe） |
| `version` | 该对象**当前提供**的版本：有活跃标记时取标记指向的版本，否则取最浅入口的版本 |
| `versions` | 观测到的**全部**版本（去重、排序、有上限） |
| `active_version` | **仅在观测到活跃标记时非空**（如 `nodejs -> v26.8.1`）；观测不到就是 `null`，不猜 |
| `entrypoints` | 白名单声明的入口文件名 |
| `observed_digest` | 入口可执行文件的 digest（有界观测，不是整棵树的 digest） |
| `probe_level` / `source_kind` | 证据强度（§9.9 阶梯）与来源类型 |

## 6. `where` 选择契约（本次最关键的改动）

现行 §9.10:887-896 把外部对象放在第 4 位且仅在 policy 允许时使用，并把
"managed 坏了 + external 健康"定义为 `CONFLICT_MANAGED_BROKEN`。管家模型下改为：

```text
1. project binding（约束满足、健康）
2. session binding（约束满足、健康）
3. steward 域 reference（约束满足、健康）      ← 从第 4 位提到这里
4. owned 域 machine active binding（约束满足、健康）
5. unmanaged 候选：只作诊断，永不作为默认可执行结果
6. broken / stale / drifted / quarantined：不得被选为 healthy
```

**优先级 3 与 4 的顺序由 policy 决定**，默认 **steward 优先**（管家模型的核心），
但允许 `policy.precedence = steward|owned`。理由：如果用户已经把某个 python 交给 AIROOT
安装并拥有（owned），他可能希望 owned 优先；这必须是一个显式策略，而不是隐式默认。

**冲突语义改写**：

| 情况 | 现行 | 改后 |
|---|---|---|
| owned 健康 + reference 健康 | owned 胜出 | 按 `policy.precedence`（默认 reference） |
| owned 坏了 + reference 健康 | `CONFLICT_MANAGED_BROKEN`（退出码 3，不换） | **正常降级到 reference**，`reason_code=CURRENT_SOURCE_DEGRADED`（退出码 2），并在 `candidates` 保留两者证据 |
| owned 坏了 + 无 reference | `BROKEN` | 不变（`BROKEN`，退出码 3） |
| 仅 reference 存在但未显式 fallback | `NOT_FOUND` + `EXTERNAL_REFERENCE_REQUIRES_FALLBACK` | **直接可选**（`selection_reason=STEWARD_REFERENCE_HEALTHY`） |

`--allow-external-fallback` 这个开关在新模型下**失去意义**（reference 不再是 fallback），
保留为兼容别名并标记 deprecated。

## 7. registry 与 Schema 变更

### 7.1 registry

- 新表 `data_roots`：`data_root_id`、`path`、`role`、`volume_serial`、`acl_baseline_json`、
  `whitelist_revision`、`added_at`、`active`。
- 扩展 `external_references`：新增 `version`、`architecture`、`entrypoints_json`、
  `capability_kind`、`data_root_id`、`evidence_json`、`probe_level`、`source_kind`。
- 新事件类型：`DATA_ROOT_ADDED`、`DATA_ROOT_FORGOTTEN`、`REFERENCE_ADDED`、
  `REFERENCE_DRIFTED`、`REFERENCE_REMOVED`。

### 7.2 Schema（原提案：**不新增文件，`schema_count` 保持 18** —— 已被 §17.6 取代：实际为 **19**，理由见 ADR-0003 的新增边界规则）

| Schema | 变更 | 兼容性 |
|---|---|---|
| `common.schema.json` | 新增 `$defs.externalReference`（含上面全部字段） | 纯新增 |
| `registry-projection.schema.json` | `external_references.items.properties` 增加上述**可选**字段 | 规则 2：新增可选属性 = minor |
| `where-response.schema.json` | `selection_reason` 是自由 string，**无需改**；`reason_code` 新增 `CURRENT_SOURCE_DEGRADED`（自由 pattern，无需改） | 无需改 |
| `managed-tool-instance.schema.json` / `runtime-instance.schema.json` | **不改**。`store_path` 的 `^store/` pattern 仍然正确，因为 steward 对象不进 store | 无需改 |
| `extension-manifest.schema.json` | 可选新增 capability `kind: steward|owned` | 规则 2 |

> 之所以不用改两个 instance Schema，是管家模型的一个直接好处：**owned 与 steward 走完全
> 不同的字段**（`store_path` vs `path`），Schema 天然容纳，不需要放宽任何 pattern。

### 7.3 若日后仍要外置 owned payload

那才会触发 `store_path` pattern 变更（属规则 2 的枚举/语义变更）。**本草案不包含该项**，
它由 ADR-0004 的"数据根"解决：steward 对象根本不需要 `store_path`。

## 8. CLI 变更

| 命令 | 状态 |
|---|---|
| `airoot data-root add\|list\|forget` | **新增**（§3.1） |
| `airoot discover [--data-root <id>] [--json]` | **新增**（只读扫描 + 分类） |
| `airoot adopt <path> --mode reference [--json]` | 已文档化（§15.1），本次实现 |
| `airoot unadopt <external-id>` / `airoot forget <external-id>` | 已文档化，本次实现（`forget` 为更清晰的新名，`unadopt` 保留兼容） |
| `airoot inventory --class external_reference\|unmanaged` | 已文档化（§15.1），本次让 external 表真正有数据 |
| `airoot plan <capability> --scope project\|data-root [--dry-run] [--json]` | **行为扩展**（§12） |
| `airoot where <capability>` | **行为变更**（§6），命令签名不变 |
| `airoot doctor` | 新增数据根身份/ACL 诊断与 `--include-unmanaged` |
| `airoot env activate\|deactivate\|exec` | 已文档化（§16.4），本次实现（§13） |
| `airoot env persist\|forget\|list` | **新增**（§13，持久化环境变量） |
| `airoot retire <capability>` / `airoot uninstall <capability>` | **新增**（§14，删除语义分级） |

> **实现落地时的拼写（与上表提案形态的差异，记录在案）**：`retire` 与 `gc` 最终挂在 `tool`
> 子命令下——`airoot tool retire <instance-id>`、`airoot tool gc --plan|--apply`——因为它们作用在
> **受控实例**上而不是能力名上；`uninstall` 保持顶层（它是"retire + gc"的组合意图）。
> 上表是提案期的形态，保留原样以保持记录连续；**可执行的拼写以 `SKILL.md` 的命令地图与
> `agents/airoot.json` 为准**，两者都有漂移守卫（`cli/tests/test_l0_consistency.py`）。

## 9. `doctor` 诊断扩展

| 新增码 | 严重度 | 含义 | remediation |
|---|---|---|---|
| `DATA_ROOT_MISSING` | error | 已注册数据根路径不存在 | `inspect` |
| `DATA_ROOT_VOLUME_MISMATCH` | error | 数据根所在卷身份变化 | `recover` |
| `DATA_ROOT_ACL_DRIFT` | warning | 数据根 ACL 偏离基线（P2 起生效） | `repair` |
| `REFERENCE_STALE` | warning | reference 对象消失或不可读 | `inspect` |
| `REFERENCE_DRIFTED` | warning | 观测 digest / 版本 / 架构变化 | `inspect` |
| `REFERENCE_UNPROBED` | info | 只有路径、没有版本证据（证据强度不足） | `inspect` |
| `WHITELIST_REVISION_STALE` | info | 数据根的 `whitelist_revision` 落后 | `repair` |
| `UNMANAGED_OBJECT_PRESENT` | info | 存在未匹配白名单的对象（仅 `--include-unmanaged`） | `none` |

D1 不变量扩展为"**本体根 + 每个数据根**的身份都可证明"；D3 区分 owned（digest 基线）与
steward（观测对比）。新增 exit code 映射：`CURRENT_SOURCE_DEGRADED` → 2（drift）。

## 10. 可测试不变量

| ID | 场景 | 预期 |
|---|---|---|
| S-028（新） | 移走本体目录后扫描数据根 | 文件树 digest 与 PATH **完全不变** |
| S-029（新） | 未匹配白名单的对象出现在数据根 | 只进 `inventory --class unmanaged`，不进 `doctor` 常规诊断 |
| S-030（新） | reference 指向的文件被外部删除 | `REFERENCE_STALE`，**不自动删除记录、不自动替换** |
| S-031（新） | `unadopt` 一个仍被 project manifest 使用的 reference | 拒绝，退出码 2 |
| C-016（新） | `discover` 遇到批处理/安装器/未知脚本 | 不执行、不登记为 reference（只 `unmanaged`） |
| P-021（新） | `data-root add` 指向 UNC / reparse point / ~~另一个卷~~ | UNC 与 reparse point **仍**被拒绝，退出码 8（`UNC_NOT_ALLOWED` / `REPARSE_POINT_REJECTED`）。**"另一个卷"一句已取代**：跨卷数据根是**明确允许**的（ADR-0004；`cli.py` 的注释写着数据根的卷身份 *explicitly not* required to match CLI root 的卷），`test_cli_steward.py::test_data_root_on_another_volume_is_accepted` 直接断言这一点 |
| S-014 改写 | owned 坏 + reference 健康 | 降级到 reference，`CURRENT_SOURCE_DEGRADED`（退出码 2），**不再**报 `CONFLICT_MANAGED_BROKEN`。**同一编号在验证与测试方案另有一条定义，两处必须同时更新**（守卫第十七组） |

§12–§15 各自列出的测试编号（C-017…C-033、S-032…S-036）同属本不变量集合，不在此重复。

### 本草案新增的 reason code（实现时须同步 `exits.py` 与诊断码表文档）

| 新码 | 退出码 | 触发 |
|---|---:|---|
| `SIZE_ESTIMATE_UNAVAILABLE` | 0 | `--dry-run` 拿不到体积（**"未知"是合法答案**） |
| `SCOPE_UPGRADE_REQUIRES_APPROVAL` | 4 | 项目 scope → 数据根 scope 未获批准 |
| `SCOPE_CONFIRMATION_REQUIRED` | 4 | 命中"高风险三类"，必须确认 |
| `PERSISTENCE_REQUIRES_APPROVAL` | 4 | 请求持久化但没有批准或预授权 |
| `PERSISTENCE_TARGET_FORBIDDEN` | 8 | 值指向 shim / 注入型变量 / 含换行等非法值 |
| `OWNERSHIP_REQUIRED` | 7 | 对 reference 调 `uninstall` |
| `REFERENCE_IN_USE` | 2 | `forget`/`uninstall` 命中仍在使用的引用 |
| `CAPABILITY_NOT_DECLARED` | 9 | 对象没有已冻结的 capability，无法 adopt |
| `CURRENT_SOURCE_DEGRADED` | 2 | owned 不可用但 reference 健康，已降级 |
| `DATA_ROOT_MISSING` / `DATA_ROOT_VOLUME_MISMATCH` | 6 | 数据根身份问题（§9） |
| `REFERENCE_STALE` / `REFERENCE_DRIFTED` | 2 | 观测漂移（§9） |
| `UNMANAGED_OBJECT_PRESENT` / `REFERENCE_UNPROBED` | 0 | info 级，仅 `--include-unmanaged` |

## 11. 需要评审确认的开放问题

1. **conda 发行版怎么算**：`D:\env_apps\miniconda3` 既是 runtime 又是包管理器，还带
   `envs/`。建议：本体登记为 `runtime:python` 的 reference，`envs/*` 一律 `project_owned`
   不接管（§9.5:764）。
2. **同一 capability 多个 reference 的 active 选择**：`D:\env\flutter` 与 `D:\env\flutter_oh`
   都存在时，谁 active？建议沿用 §3.2 的 binding key（machine scope 一个 active），
   由 `airoot where flutter --pin <external-id>` 切换（属新增命令，需评审）。
3. ~~**nvm 多版本**~~ **已定（并已实现）**：**一个对象 = 一条 reference**，版本集合与"观测到的
   活跃版本"是这条 reference 的**事实字段**，而不是拆成多条 reference。理由：
   - 磁盘上真实存在的是那个目录；把同一个目录登记成三条 reference 会让"一个对象"这个概念碎裂，
     并且当只发现一个版本后再发现第二个时，所有 id 都会变（漂移）；
   - 版本管理器（nvm 的 `nodejs`、pyenv 的 `current`、conda 的 `envs`）用**链接**表达活跃版本，
     读链接目标是**单次只读 `readlink`**，不跟随、不递归，符合 §9.4 的"不默认 follow reparse point"；
   - 于是 `version` = **该对象当前提供的版本**（有活跃标记时取标记指向的那个，否则取最浅入口的），
     `versions` = 观测到的全部版本，`active_version` = **仅在观测到标记时才非空**——
     一个版本存在 ≠ 它就是活跃的，不能猜。

   实测：`D:\env\nvm4w` → `versions=[22.14.0.0, 22.15.0.0, 26.8.1.0]`、`active_version=26.8.1.0`
   （与机器上 `nodejs -> v26.8.1` 一致）。每个对象的版本数有上限
   （`scan.max_versions_per_object`，默认 8），超出即标记为截断。
4. **`build` / `network_proxy` / `llm_runtime` 等 capability 是否纳入**——这要先冻
   capability 清单（规划 §23 未冻结项），本草案不擅自扩充。
5. **白名单的匹配是"任一证据"还是"全部证据"**：建议 `evidence_all`（全部满足）以防误认，
   个别条目可降级为 `evidence_any` 并注明理由。
6. **数据根的 ACL 基线由谁设定**：P2 的 broker 需要为每个数据根建立/校验 ACL 吗？
   若数据根是用户自己创建的目录，AIROOT 是否有权改其 ACL？（安全与侵入性的取舍）
7. ~~**"必须确认"的体积阈值取多少**~~ **已定：300 MB**，policy 可覆盖（§12.1）。
8. **`uninstall` 是独立命令还是 `retire` + `gc` 的封装**：本草案建议封装（不给第二步留后门），
   但这样用户无法"只退役不删"。需要确认是否保留两个独立入口。
9. **机器级环境变量的白名单范围**：除"指向数据根路径的变量"外，是否允许 `PATH` 追加以外的
   系统变量（如 `JAVA_HOME`、`FLUTTER_ROOT`、`ANDROID_HOME`）？建议允许，但必须逐个列入白名单。
10. **持久化的 owner 判定**：同一 capability 同时存在 owned 与 reference 时（例如 AIROOT 装过
   一份 python，你又自己装了一份），`env persist` 应该指向谁？建议按 §6 的 `policy.precedence`
    决定，并在记录里写明选择理由。
11. **`.ai/tooling.json` 是否算项目内契约**：它由 CLI 写、由 Skill 读，但项目可能被 git 跟踪。
    需要确认它是否应进 `.gitignore` 建议、以及是否允许多分支并存（建议：单文件、只记最后选择）。
12. **`discover` 的响应没有 Schema**：它目前是一个无 schema 的 CLI 文档（golden 语料里只钉形状）。
    若要给 Agent 稳定的机器契约，应新增 `discover-response.schema.json`——那会把
    `schema_count` 从 18 变成 19，需同步测试与文档断言。建议在 §12 实现时一并决定。
13. **可移动卷 / 网络卷的数据根**：现在只记录 volume serial，不限制卷类型。是否需要拒绝
    （或只是标记）可移动盘与网络盘——搜索、digest 与"漂移"语义在 SMB 与拔插盘上都不同——
    需要确认。建议：注册允许，但在 `doctor` 里标记卷类型。

## 12. 依赖分流与确认协议

> **这是 v0.1 §6 的复活。** v0.3 全文没有这块（"项目隔离 / dry-run / 只读记忆"零命中），
> 而它正是"管家问我要装哪里"的实现依据。判据沿用 v0.1，落点改为 ADR-0004 的数据根。

### 12.1 唯一分流判据：这个东西被项目清单引用了吗

| 情形 | 判定 | 动作 |
|---|---|---|
| 被 `pyproject.toml` / `requirements*.txt` / `package.json` / lock 引用 | 属于**项目** | **项目内隔离，不询问**（Zone P，§7.1） |
| 单文件通用 CLI（jq / rg / ffmpeg / 7z） | 属于**机器** | 装到数据根，**不询问** |
| 需要往运行时装包 / 创建环境 / 体积超阈值 / 涉及 CUDA 或非 Python 二进制 | **高风险** | **必须确认** |
| 共享 conda 环境 | 安全单点 | **必须确认**，且只读冻结 |
| 来源或完整性不可验证 | 不可信 | **只允许 reference**，禁止 import/recreate（§4.3、§9.6:789） |

**为什么"简单的必须不问"**：严的边界只能划在"往运行时装包、创建环境、下大体积"这三类动作上。
对"装个 jq"这类**正确答案永远一致**的操作必须不询问，否则确认会退化成噪音，用户在两周内就会
习惯性点"同意"——那时高风险确认也一起失效。这是本协议最重要的一条设计约束。

**体积阈值**：`plan` 的 scope 决策与"是否需要确认"共用同一个阈值。
**已定：300 MB**（用户决定）。它由 policy 可配置覆盖，但默认值是 300 MB——`jq`/`7z`/`rg`
这类小工具永远不会触发确认，JDK / conda 环境 / Flutter SDK 这类会。

### 12.2 确认形态（三步，不可变）

```text
1. 先 --dry-run 拿真实体积
     拿不到 → 明说"未知"，绝不编造
     reason_code=SIZE_ESTIMATE_UNAVAILABLE，退出码 0
2. 选项固定为三选一（不得增删、不得改名）
     项目隔离 / 数据根（全局） / 取消
3. 结果写入项目内 .ai/tooling.json（只读记忆）
     AI 与 Skill 不得写入该文件；下次同一 capability 不再询问
```

`.ai/tooling.json` 的语义与 v0.1 一致：**只读记忆**。它记录"上次用户选了什么"，**不是授权凭据**；
真正的授权仍是 approval token（§8.5）。记忆只在**同一项目 + 同一 capability + 同一版本约束**
下生效；项目清单变化即失效。

### 12.3 与 v0.3 既有机制的接法

| 环节 | 由谁负责 | 依据 |
|---|---|---|
| 把"装哪里"变成结构化选项、解释影响与副作用 | **Skill** | §16.1:1714-1721 |
| plan / `--dry-run` / 体积估算 | CLI | §15.1、§17 |
| 三选一的**用户决策** | human approval | §8.5、三大核心契约 §2.5 |
| **项目 → 数据根的 scope 提升** | **必须显式批准** | §17:1818、验证方案 S-010/S-015 |
| 实际下载/校验/stage/commit | Install Backend | §11.5:1158-1199 |
| 记录 | registry instance + binding | §5.4、§10 |

**scope 提升是独立的受保护动作**：项目目录里的 manifest 只能申请 `project`/`session` scope；
要写进数据根（机器可见）必须另拿批准。直接沿用 §17:1818 与 S-010/S-015，不新增概念。

### 12.4 命令形态

```bash
airoot plan <capability> --scope project   --target <project-root>      --dry-run --json
airoot plan <capability> --scope data-root --target data-root:dr-env    --dry-run --json
airoot plan <capability> --scope data-root --target data-root:dr-env    --json
airoot approve <plan> --token-file <token>
airoot install <plan> --token-file <token>
```

- `--dry-run` 必须**无副作用**，且必须返回：目标位置、预计体积（或 `null` +
  `SIZE_ESTIMATE_UNAVAILABLE`）、是否需要确认、需要哪个 scope 的批准、`side_effects` 列表。
- **v1 Install Backend 基线不变**：只接受单文件 / 无脚本 portable zip / 可验证的简单 runtime
  archive（三大核心契约 决策3:39-49）。脚本型安装器（flutter / JDK / conda 安装器）**不在 v1
  基线内**；要纳入必须先声明 `executes_scripts=true`、`reversible=false`，并因此**不能走低风险
  自动批准**（三大核心契约 §4.4:352）。

### 12.5 测试

| ID | 场景 | 预期 |
|---|---|---|
| C-017（新） | 项目清单引用的依赖安装 | **不询问**，项目内隔离，`scope=project` |
| C-018（新） | 单文件通用 CLI（jq）安装 | **不询问**，装到数据根 |
| C-019（新） | `--dry-run` 拿不到体积 | 返回 `null` + `SIZE_ESTIMATE_UNAVAILABLE`，**不编造数字**，退出码 0 |
| C-020（新） | 项目 manifest 请求 data-root scope | `SCOPE_UPGRADE_REQUIRES_APPROVAL`，退出码 4，未批准不写入 |
| C-021（新） | 同项目同 capability 第二次安装 | 读 `.ai/tooling.json`，不再询问 |
| C-022（新） | Skill/Agent 尝试写 `.ai/tooling.json` | 拒绝（只读记忆） |

---

## 13. 环境变量持久化策略

### 13.1 先讲物理事实

> 普通子进程**无法修改已经存在的父 PowerShell/cmd 环境**（§16.4:1760）。

因此"写入环境变量"只有三种真实含义，契约必须分开命名，不能混称"设置环境变量"：

| 层次 | 机制 | 生效范围 | 持久 | 批准 |
|---|---|---|---|---|
| **session** | `env activate --shell powershell\|cmd` 输出脚本；或 `airoot exec --env <name> -- <cmd>` | 当前 shell（用户 source）或子进程 | 否 | 不需要 |
| **user persistent** | 写 `HKCU\Environment` | 该用户的**新**进程 | 是 | **需要**（policy 可自动） |
| **machine persistent** | 由 broker 写 HKLM（保持 `REG_EXPAND_SZ` + 广播） | 所有新进程（含 SYSTEM 服务） | 是 | **需要，且必须 human** |

### 13.2 指向规则（本协议最关键的判定）

| 被持久化的对象 | 允许的值 | 理由 |
|---|---|---|
| **reference**（你自己装的环境） | **直接指向数据根内的真实路径**，如 `D:\env\flutter\bin` | 删掉 `D:\AIRoot` 后该值**依然有效**——这正是"解雇管家不带走环境" |
| **owned**（AIROOT 装的那一次） | **禁止持久化**，只允许 session 激活 | 它的唯一稳定入口是 `exposure\bin` 的 shim；持久化 shim 等于"AIROOT 在你环境里留了一份它会带走的指向" |

**硬规则**：持久化值**不得指向** `AIROOT\cli\exposure\bin` 或 root 内任何路径。
违反 → `PERSISTENCE_TARGET_FORBIDDEN`（退出码 8）。

理由说透：如果那条 PATH 指向 shim，删掉 AIROOT 之后它会指向一个不存在的目录——**不但没保住
环境，反而在系统里留下垃圾**，与管家模型的目的正好相反。

### 13.3 "自动写入"的合法形态

契约**不提供**"第二次调用就自动持久化"这种隐式策略。合法路径只有两条：

1. **显式命令**：`airoot env persist <capability> --scope user|machine`（+ 批准）。
2. **预先授权的自动**：用户在自己的 policy 里写 `auto_approve`，且**必须绑定
   capability + operation + scope + 副作用上限**，不能只按 capability 名称放行（§17:1819）。
   这样"自动"是**你批准过的自动**，审计里 `approval_mode=policy` 可区分（§8.5:660）。

### 13.4 记账要求（"开除管家能干净退出"的前提）

- 每次持久化写入必须在 registry 留一条 `environment_persist` 记录：
  `variable` / `old_value`（完整快照）/ `new_value` / `scope` / `plan_hash` / `approval_id` / `written_at`。
- `airoot env list --json` 列出 **AIROOT 写过**的全部持久化项。
- `airoot env forget <capability> --scope ...` 与 `airoot env forget --all`：**精确恢复旧值**，
  **绝不触碰 AIROOT 没写过的变量**。
- 没有这条记账，"删掉 AIROOT 不留痕"就做不到——环境里会残留一批无法判断来源的变量。

### 13.5 禁止项（复活 v0.1 的注入型变量清单，修正为"机器级禁止"）

以下变量**禁止**写入 machine/user persistent（session 级允许）：

```text
PYTHONPATH  PYTHONHOME  PYTHONSTARTUP
NODE_OPTIONS  NODE_PATH
LD_PRELOAD  LD_LIBRARY_PATH  DYLD_INSERT_LIBRARIES
GIT_SSH_COMMAND  GIT_EXTERNAL_DIFF  GIT_CONFIG_GLOBAL
PATHEXT  COMSPEC  BASH_ENV  ENV  ZDOTDIR  PROMPT_COMMAND
```

理由与 v0.1 相同：这些变量等价于"直接指定要加载的代码"或"增加一个执行触发器"，机器级设置等于
全局代码注入面。违反 → `PERSISTENCE_TARGET_FORBIDDEN`。

**与 v0.1 的差别（必须记录）**：v0.1 的规则是"机器级**只允许 PATH**、其余全部会话级注入"；
本协议放宽为"机器级允许**指向数据根路径**的变量 + 显式白名单，但注入型变量仍禁止"。
放宽的理由是用户明确要求"能写入系统环境变量"；收紧的部分（注入型变量）原样保留。

### 13.6 值的安全要求

- 写入前必须转义；拒绝含换行、未配对引号、`%VAR%` 展开歧义的值；
- user 级用 `REG_EXPAND_SZ` 还是 `REG_SZ` 必须显式声明并记录；
- 路径必须 canonical、必须在已注册数据根内、必须存在（写之前校验）；
- 广播（`WM_SETTINGCHANGE`）与"新进程读回校验"是**必要步骤**，不是可选优化。

### 13.7 测试

| ID | 场景 | 预期 |
|---|---|---|
| C-023（新） | 持久化一个 reference 环境的路径 | 写入成功；新进程可见；值指向数据根真实路径 |
| C-024（新） | 尝试持久化 `exposure\bin` shim | `PERSISTENCE_TARGET_FORBIDDEN`，退出码 8 |
| C-025（新） | 尝试持久化 `PYTHONPATH`（机器级） | 拒绝，退出码 8 |
| C-026（新） | 值含换行 / `%VAR%` | 拒绝，退出码 8 |
| C-027（新） | `env forget` 单项 | 精确恢复旧值，其它变量一字不动 |
| C-028（新） | `env forget --all` | AIROOT 写过的全部恢复；**用户自己的变量不变** |
| C-029（新） | 未预先 `auto_approve` 时请求持久化 | `PERSISTENCE_REQUIRES_APPROVAL`，退出码 4 |
| C-030（新） | 子进程试图改变父 shell | 只输出脚本/JSON；**不得声称父进程已改变**（§16.4、C-010） |

---

## 14. 删除语义分级

v0.3 **没有**"删除环境"这个用户级动作：`retire` 不删 payload、`gc` 只回收无引用者、
`unadopt` 只删引用，而且 §5.2:322 把"外部软件卸载器"列为 Extension **不默认包含**。
本节补齐它，并把"管家能删什么"钉死在所有权上。

### 14.1 四个动词，语义不得混用

| 命令 | 作用 | 是否删文件 | 批准 | 适用对象 |
|---|---|---|---|---|
| `airoot forget <external-id>` | 删 AIROOT 的登记（原 `unadopt`） | **否** | 否 | 仅 reference |
| `airoot retire <capability>` | 取消 active binding，payload 保留 | **否** | 否 | 仅 owned |
| `airoot uninstall <capability>` | 删除 **owned** payload + 记录 | **是** | **是** | 仅 owned |
| `airoot env forget` | 撤销 AIROOT 写的持久化变量 | 否（恢复旧值） | 否 | 仅 AIROOT 写过的项 |

### 14.2 硬规则

1. **reference 永不提供 uninstall**。用户想删自己装的东西时，AIROOT **只输出**要删的绝对路径
   与建议命令，**不代劳**——因为它不拥有那个对象。
2. **数据根内的任何目录永不被 AIROOT 删除**，即使它匹配白名单并已登记为 reference。
3. `uninstall` 的准入判据写死为 `ownership = owned`（`install_backend_id != external` 且
   source 与 digest 可验证）。不满足 → `OWNERSHIP_REQUIRED`（退出码 7）。
4. `uninstall` **不得一步删**。必须走 `retire → gc` 的既有路径并复用同一事务状态机；
   禁止 `--force` 绕过。
5. 删除前必须检查引用：仍被 binding / rollback 保留 / transaction / audit retention /
   project manifest 引用 → **拒绝**（延续 §15.3:1594 与验证方案 S-024）。
6. 失败或中断 → 保留 payload 与 journal，由 `repair` 恢复，**不产生半删除**。
7. `forget` 一个仍被 project manifest 使用的 reference → 拒绝（§9.7:836），退出码 2。

### 14.3 与"解雇管家"的关系

| 动作 | 对 `D:\env`、`D:\tools` 的影响 |
|---|---|
| 删掉整个 `D:\AIRoot` | **无**（文件完好） |
| `airoot forget --all` | **无**（只删登记） |
| `airoot uninstall <owned>` | 只影响 AIROOT 自己装过的那些 |
| 任何命令作用于 reference / 数据根内容 | **无**——这是可测试的不变量 |

### 14.4 测试

| ID | 场景 | 预期 |
|---|---|---|
| S-032（新） | 对 reference 调 `uninstall` | `OWNERSHIP_REQUIRED`，退出码 7，文件不动 |
| S-033（新） | 对数据根内容依次执行 forget / retire / gc / uninstall | 文件树 digest **完全不变** |
| S-034（新） | `uninstall` 仍被 rollback 保留引用的 instance | 拒绝，退出码 2 |
| S-035（新） | `uninstall` 中途中断 | payload 保留、journal 可 repair、无半删除 |
| S-036（新） | `uninstall` 后再次 `where` | `NOT_FOUND`；`doctor` 无残留诊断 |

---

## 15. 能力边界声明：全能于能力，不全能于软件

规划 §2.3:116-125 明确"AIROOT 不是通用包管理器""不接管整台电脑所有软件"，§9.6:789 与
§5.2:318-324 进一步排除服务、驱动、GUI 与"外部软件卸载器/安装包"。"全能管家"必须在这条边界内
定义，否则第一周就会撞上它。

### 15.1 声明

> **AIROOT 全能于 capability，不全能于 software。**
> 它能下载、删除、记录、管理的是「**有明确 capability 且来源可验证**」的对象；
> 对没有 capability 或来源不可验证的对象，只发现、只报告、永不接管。

### 15.2 三个准入条件（必须同时满足）

| # | 条件 | 不满足时 |
|---|---|---|
| 1 | 存在对应 `capability_id`（在冻结的 capability 清单内） | `unmanaged` 只报告；要管必须先新增 capability |
| 2 | 来源与完整性可验证（artifact digest，或只读静态元数据证据，§9.9） | 只能 `reference` **或** `quarantined` |
| 3 | 不需要 AIROOT 取得不可回滚的副作用（服务 / 驱动 / 持久提权 / 计划任务） | `excluded` |

### 15.3 明确排除（引用现行条文，不是新增）

GUI 软件、服务、驱动、计划任务、需要注册表副作用的安装器、来源不可验证的目录、无对应
capability 的对象、被 §15.2 条件 3 命中的对象。落点见 §4.4 的 `excluded` 名单。

### 15.4 "全能"如何增长（避免无边界）

想管一个当前没有 capability 的对象（例如你机器上的代理工具、LLM 运行时），路径是：

```text
提出 capability（名称、CLI 入口、请求/响应 schema、side_effects、scope 语义）
  → 冻结进 capability 清单（规划 §23 的未冻结项）
  → 写白名单条目（§4.2 的证据谓词）
  → 才能被 discover 识别为候选、被 adopt 登记
```

**不得**为了让某个对象"能被管"而临时放宽白名单的证据要求，也不得按目录名硬编码。这样"全能"
是**可增长且可审计的**，而不是无边界扩张。

### 15.5 测试

| ID | 场景 | 预期 |
|---|---|---|
| C-031（新） | 对象匹配白名单但 capability 未冻结 | 只 `unmanaged`；`adopt` 拒绝并提示先冻 capability |
| C-032（新） | 名字极像（`python.exe` 但 PE ProductName 不符） | **不登记**（§9.3:716 禁止按名字认定） |
| C-033（新） | 服务 / 驱动 / 计划任务类对象 | `excluded`，不进 inventory 主列表 |
| C-034（新） | 已注册数据根的 ACL 与登记时记录的**观测基线**不同（或读不出来） | `DATA_ROOT_ACL_DRIFT`（warning）；`repair` = **重新记录基线**（不是覆盖用户的 ACL）；没记基线就不报（§58、ADR-0023） |

---

## 16. 实现影响面（评审通过后的施工顺序）

### 16.0 施工进度（截至本次实现）

| 步骤 | 状态 | 证据 |
|---|---|---|
| 1 Schema（`$defs.externalReference` / `$defs.dataRoot` / projection 可选字段） | ✅ 已完成 | `schema_count` 现为 **19**（新增 `reference-plan.schema.json`）；`test_projection_reference_properties_stay_within_the_shared_definition` 防漂移 |
| 2 registry（`data_roots` 表 + reference 扩字段 + 迁移 v2） | ✅ 已完成 | v1 库可就地迁移；`test_migrated_and_fresh_databases_have_identical_shape` 断言迁移与 `ddl.sql` 不漂移 |
| 3 只读 PE 探测 + 白名单 + discovery | ✅ 已完成 | **只读有界**：只读头部与 `.rsrc` 段，不再整文件读入（`node.exe` 120 MB 可探测）；白名单 revision 现为 **`wl-3`**（步骤 9 修正了 java 的 `environment` 块） |
| 4 `data-root` / `discover` / `adopt --mode reference` / `forget` | ✅ 已完成 | `test_cli_steward.py` 21 项 |
| 13 不变量测试 + golden 语料 | ✅ 已完成 | 含 `test_firing_the_butler_does_not_take_the_environment` |
| 8c Install Backend 抽象（`portable_file` / `https_artifact` + 九声明 + 无脚本守卫） | ✅ 已完成 | 见 §21.6；真实 artifact 可端到端装成 owned instance 并被 `gc` 收掉 |
| 9b Skill 适配层（`AIROOT\SKILL.md` + `agents/` + `references/` + 漂移守卫） | ✅ 已完成 | 见 §22.6；零运行时代码改动，13 项一致性守卫 |
| 9c 来源清单（可信 host + 上游校验和解析 + `plan --source-json`） | ✅ 已完成 | 见 §23.6；离线端到端可复现，v1 不做签名且如实声明 |
| 9d `rebuild`（派生状态重建；`doctor` 的 remediation 终于有对应命令） | ✅ 已完成 | 见 §24.6；权威不从自身重建，旧投影归档为只读证据 |
| 9f 只读观察面 + PATH 不变量检查（`tool list/status/verify`、`path verify`） | ✅ 已完成 | 见 §26.6；这是 `path` 规则第一次有代码在检查 |
| 9g `desired` 层 + `tool pin`（五层模型最后空缺的一层） | ✅ 已完成 | 见 §27.5；pin 只写 desired、给出计划、不改 binding |
| 9h desired vs declared 接进诊断（`DESIRED_NOT_SATISFIED` @ D5） | ✅ 已完成 | 见 §28.5；未 pin 的机器诊断输出逐字节不变 |
| 9e session 激活的完整语义（快照 + 嵌套栈 + `deactivate` + `SESSION_STATE_STALE`） | ✅ 已完成 | 见 §25.6；`env activate --json` 补齐 §16.4 要求的 diff/generation/快照/deactivate 四项 |
| 3b 多版本对象（版本集合 + 观测到的活跃版本） | ✅ 已完成 | 迁移 v3；`versions`/`active_version`；实测 `nvm4w` → 3 版本 + active 26.8.1.0 |
| 5 `where` 优先级与冲突语义 | ✅ 已完成 | 见 §18.6；steward-first 落地，`CONFLICT_MANAGED_BROKEN` 不再发射；两个 golden fixture 改名 |
| 6 `doctor` 数据根诊断（D1/D3 扩展、`--include-unmanaged`） | ✅ 已完成 | 见 §18.6；reference 重观测用有界只读，整文件 digest 只在 `--verify` |
| 8–9 依赖分流与确认、环境变量持久化（user 级） | ✅ 已完成 | 见 §17.6；`reference-plan.schema.json` 为第 19 个 schema |
| 8b `plan` 的 scope/target 形态（§12.4） | ✅ 已完成 | 见 §20.5；路由决定进 `metadata.routing`，未获确认不落盘计划 |
| 10 machine 级环境变量 | ⛔ 未做 | 依赖 P2 的 broker；`--scope machine` 现在报 `PRIVILEGE_REQUIRED` |
| 11–12 删除分级、能力边界增长流程 | ✅ 已完成 | 见 §19.6；`plan.schema.json` 早已有 `retire_tool`/`gc_apply`，无需改 Schema |
| 14 规范层落地（改 §3.2 / §9.10 / §5.4 / §8.1 措辞） | ⛔ 未做 | 需先评审 |

**首次真实扫描（`D:\env`）的结论**——白名单在真实数据上的表现（`wl-3` 复测一致）：

```text
REF      java 25.0.2.0 · cmake(build) 3.31.6.0 · ffmpeg(media_probe, 版本 unknown) · node 22.14.0.0
unmanaged VisualStudio · flutter · flutter_oh
excluded  9 项（GUI/服务）+ pip-cache / uv-cache（缓存不是能力对象）
counts   {'excluded': 9, 'external_reference': 4, 'unmanaged': 3}
```

扫描落地时修正了三个由**真实数据**暴露的问题（都已成为回归测试）：

1. **容器被内部捆绑的工具误判**：`D:\env\flutter` 自带 `bin\mingit\cmd\git.exe`，而它自己的
   `dart.exe` 没有版本资源。现在要求匹配的入口在对象根的 `max_relative_depth`（默认 2）之内，
   且**最浅者胜**——捆绑工具不再给容器背书。
2. **缓存被误判为运行时**：`uv-cache` 里的 `python.exe` 是构建产物。缓存目录按后缀排除
   （§7.1/§13.3：派生数据不是能力对象）。
3. **大二进制读不了**：原先整文件读入 + 32 MB 上限，导致 120 MB 的 `node.exe` 被判为"非 PE"。
   现在只读头部与资源段两个有界区间。

另有两个**证据事实**必须记录（它们改变了白名单的写法，白名单 revision 因此从 `wl-1` 升到 `wl-2`）：
- **ffmpeg / ffprobe 确实没有版本资源**（实测 `no RT_VERSION resource`），故 `media_probe`
  条目使用 `sibling_file`（互为兄弟）并声明 `weak_evidence` + 理由，`version` 如实返回
  `unknown` 而不是猜；
- **`dart.exe` 同样没有 `RT_VERSION` 资源**，因此 `dart` 条目已**移除**——无法用静态证据证明
  就不该有条目；`flutter` / `flutter_oh` 正确停留在 `unmanaged`。

> **一处必须记录的自我纠错**：曾有一版白名单声称"Node.js 不携带 VS_VERSIONINFO"，那是
> **错的**。错因是当时 PE 探测会整文件读入并拒绝超过 32 MB 的文件，120 MB 的 `node.exe`
> 因此返回 `ProductName=None`，被误读成"没有版本资源"。改成有界读取后实测：
> `ProductName=Node.js`、`FileVersion=22.14.0.0 / 26.8.1.0`，**有**版本资源。
> `node` 条目已改回纯 `pe_static` 谓词，弱证据声明被移除。
> 教训：把工具的失败当作数据的事实，会把错误结论写进契约；`PeMetadata.errors` 必须参与判断。

| 步骤 | 内容 | 触及 | 依赖 |
|---|---|---|---|
| 1 | `common.schema.json` 加 `$defs.externalReference`；`registry-projection` 加可选字段 | Schema + `docs/schema/README.md` | 评审通过 |
| 2 | registry：`data_roots` 表、`external_references` 扩字段、新事件 | `registry/ddl.sql`、`db.py`、`entities.py`、`projection.py` | 1 |
| 3 | 只读 PE 版本资源解析 + 白名单匹配 | **新** `caps/probe_pe.py`、`caps/discovery.py`、`policy/discovery-whitelist.json` | 2 |
| 4 | `data-root` / `discover` / `adopt reference` / `unadopt` 命令 | `cli.py` | 2,3 |
| 5 | `where` 优先级与冲突语义改写 | `caps/where.py`、`exits.py`（新码） | 2 |
| 6 | `doctor` 新诊断 + D1/D3 扩展 | `caps/doctor.py`、诊断码表文档 | 2 |
| 7 | 新不变量测试（S-028..031、C-016、P-021）+ S-014 改写 + golden 语料重生成 | `cli/tests/`、`fixtures/golden/` | 4,5,6 |
| 8 | **依赖分流与确认**：`plan --scope/--target/--dry-run`、体积估算、`.ai/tooling.json` 只读记忆 | **新** `caps/planner.py`、`cli.py`、`policy` | 1..7 |
| 9 | **环境变量持久化（user 级）**：`environment_persist` 表、HKCU 写入与恢复、`env activate/exec/persist/forget/list` | **新** `caps/environment.py`、`registry`、`cli.py` | 2 |
| 10 | **环境变量持久化（machine 级）**：HKLM 写入 + 广播 + 新进程读回校验 | 需要 **P2 的 broker**；在此之前 `--scope machine` 必须报 `PRIVILEGE_REQUIRED` | P2 |
| 11 | **删除分级**：`retire` / `uninstall`（组合 retire+gc）/ `forget` + 引用检查 | `tx/`、`registry`、`cli.py` | 2 |
| 12 | **能力边界**：白名单证据谓词（含只读 PE 解析）、capability 清单冻结流程 | `policy/`、`caps/probe_pe.py`、文档 | 3 |
| 13 | 测试 C-017…C-033、S-032…S-036 + golden 语料重生成 | `cli/tests/`、`fixtures/golden/` | 8..12 |
| 14 | 规范层落地：改 §3.2 / §9.10 / §5.4 / §8.1 的措辞；复活 v0.1 §6 的确认协议 | 规划、三大核心契约 | 1..13 验证后 |

**可以先做而不依赖 P2 的部分**：步骤 1–9、11–13（都不需要管理员权限）。只有步骤 10
（machine 级环境变量）依赖 P2 的 broker。P2 继续阻塞时，另一块不需要特权的工作是规划 §19 的
**P3 协议面**（`search` 的请求/响应契约与受控 crawl fallback，见本文件 §31）。

**明确不动**：`store_path` 相关 Schema pattern、事务状态机、`ACTIVE_BOUND` 语义、退出码
0–9 的既有映射、五层事实模型、`doctor` 的"永不删除"原则、v1 的"无脚本 portable"安装基线。

## 17. 阶段计划：步骤 8–9（依赖分流与确认 + user 级环境变量持久化）

> 本节是**实现计划**，不是契约条文。它记录本次施工的目标、子阶段、需要做出的决策与验收标准。
> 完成情况回写到 §16.0 的进度表。

### 17.1 范围与理由

选步骤 8 + 9 作为同一阶段，因为它们是**同一条用户路径**的两半，合起来才闭环：

```text
需要 X 能力 → 决定装哪里（步骤 8，问或不等问）→ 记录选择
           → 使用时把已登记的环境指向会话/系统（步骤 9）
```

两步都**不需要管理员权限**（machine 级环境变量除外，它被显式排除到步骤 10）。

### 17.2 子阶段与顺序

| # | 子阶段 | 交付物 | 依赖 |
|---|---|---|---|
| S8.1 | 分流与确认的**决策层** | 新 `caps/planner.py`：项目清单探测、唯一分流判据、三选一选项、体积估算策略、`.ai/tooling.json` 读取 | — |
| S8.2 | CLI `scope decide` / `scope memory` | `cli.py`；只读 | S8.1 |
| S8.3 | 白名单的**环境声明** | `policy/discovery-whitelist.json` 增加 `environment` 块 + 加载期校验（禁止注入型变量） | — |
| S9.1 | reference 域变更的**授权形状** | **新第 19 个 schema** `reference-plan.schema.json`：一次 reference exposure 变更的 canonical plan（`plan_hash` 绑定不变） | — |
| S9.2 | registry v4 | `environment_persist` 表 + 实体 + 迁移 + 投影不含它（属运行记录，不是 declared 对象） | S9.1 |
| S9.3 | 环境持久化核心 | 新 `caps/environment.py`：`EnvironmentStore` 抽象（真实 HKCU / 内存实现）、指向规则校验、禁止项、精确恢复 | S9.2 |
| S9.4 | 会话激活 | `env activate --json/--shell powershell|cmd`、`airoot exec --env`：**只输出脚本或起子进程**，绝不改父 shell | S8.3 |
| S9.5 | CLI `env list/persist/forget` | `cli.py`；`persist` 消费 approval token；`--scope machine` → `PRIVILEGE_REQUIRED` | S9.3 |
| S9.6 | 验收与文档 | 全套测试、golden 语料、§16.0 进度、AGENTS.md | 全部 |

每个子阶段结束时必须：`pytest` 全绿 + 旧切片无回归。

### 17.3 本阶段需要做出的决策（含理由）

1. **`.ai/tooling.json` 的写入时机**：契约说它是"只读记忆，AI 不得自行写入"。本阶段只实现**读取**；
   写入留给 P2 的 human approval 通道（审查报告已把该通道列为未冻结项）。
   理由：把它做成"CLI 可自行写"就等于给 Agent 一条绕过确认的路。
   → 记为本阶段**显式延后**，不假装完成。
2. **reference 域变更的授权载体**：README 规则 2 说改枚举要新 schema id，而现有 `plan.schema.json`
   的 `target.kind` 只允许 `managed_tool|runtime`、`operations[].kind` 只允许
   `fetch|verify|stage|commit|expose|rollback|delete` —— **无法表达"改一个 external reference 的
   暴露方式"**。因此新增 `reference-plan.schema.json`（第 19 个 schema），而不是放宽现有枚举。
   代价：`schema_count` 18 → 19，需同步 3 处断言与 2 处文档；旧切片会自动打印 19。
3. **持久化的授权**：`env persist` 必须消费 approval token，token 的 `plan_hash` 指向
   `reference-plan.schema.json` 文档的 canonical hash。**不提供任何无 token 的写入路径**，
   包括"帮助"性质的 `--force`。
4. **测试不得写真实 HKCU**：`EnvironmentStore` 抽象出真实/内存两种实现；逻辑测试用内存实现。
   真实实现只允许一个**显式的集成测试**，它写 `HKCU\Software\AIROOT-Test-<random>` 并在
   `finally` 里删除并断言已删除——这是仓库里**唯一被批准的注册表写入**。
5. **会话激活不落盘**：`env activate` 只输出脚本/JSON，`exec` 只影响子进程；两者都不写注册表、
   不改父进程环境。父 shell 无法被子进程修改是物理事实（§16.4:1760），必须如实呈现。

### 17.4 风险与处置

| 风险 | 处置 |
|---|---|
| 体积估算拿不到 → 编造数字 | 契约明确：返回 `null` + `SIZE_ESTIMATE_UNAVAILABLE`，退出码 0；测试断言"不编造" |
| 持久化值指向 shim → 删本体后留垃圾 | 硬规则 + 测试：只允许指向**数据根内**的真实路径；指向 CLI root 或 `exposure\bin` → `PERSISTENCE_TARGET_FORBIDDEN` |
| 注入型变量被持久化 | 白名单加载期与写入期**双重**校验（`PYTHONPATH`/`NODE_OPTIONS`/`PATHEXT`…） |
| 恢复旧值时误删用户自己的变量 | 每次写入前记录完整旧值快照；`env forget --all` 只恢复 AIROOT 写过的项；测试断言"其它变量一字不动" |
| 项目清单探测误判 | 只读解析；解析失败 → 不视为"被引用"，而进入"需要确认"分支（fail 到更谨慎的一侧） |
| 新增 schema 破坏既有断言 | 同步 `test_l0_protocol.py` 的两处、`docs/schema/README.md`、`AGENTS.md` |

### 17.5 验收标准（本检查点）

1. `pytest cli/tests` 全绿；旧切片 `validate-schemas` + `test` 全绿，`schema_count` 为 **19**。
2. 真实机器上可复现这条路径（无需管理员权限）——**下方是草稿拼写，实际命令见 §17.6**：

   ```text
   airoot scope decide python --project <某项目> --json   # 判定"项目内隔离，不询问"
   airoot scope decide jq --json                          # 判定"数据根，不询问"
   airoot scope decide <大体积对象> --json                 # 判定"必须确认"，体积未知时如实说未知
   airoot adopt D:\env\java --mode reference --json       # 已有
   airoot env activate java --json                        # 会话级，输出 diff，不改父 shell
   airoot exec --env java -- java -version                 # 子进程可见
   airoot env persist java --scope user --token-file …     # 写 HKCU + 记账（需 token）
   airoot env list --json                                  # 列出 AIROOT 写过的持久化项
   airoot env forget java --scope user                     # 精确恢复旧值
   ```

3. 每条新增不变量都有测试：C-017…C-030 中本阶段覆盖的编号 + 新增的环境变量测试。
4. 文档与实现一致：§16.0 进度表、§11 开放问题、`AGENTS.md`、`docs/schema/README.md`。
5. 测试不污染宿主机（除 §17.3-4 那一处被批准的临时注册表键，且必须自清理）。

### 17.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **334 项全绿**（新增 `test_l1_exposure.py` 22 项 +
`test_cli_env.py` 12 项）；旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；
golden 语料重新生成（新增 `reference_plan.json`，`reason_code_table.json` 增加两个码）。
真实机器验收脚本：`python cli/tests/real_machine_acceptance.py`（对真实 `D:\env` 跑完整路径，
**全项 PASS**；临时 root 在系统 temp 下并自清理，不写 HKCU）。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S8.1 | `caps/planner.py`：项目清单探测、唯一分流判据、三选一确认、体积阈值（300 MB，未知即未知）、`.ai/tooling.json` **只读**记忆 | ✅ |
| S8.2 | `airoot scope decide` / `scope memory` | ✅ |
| S8.3 | 白名单 `environment` 块 + 加载期校验（`java`：`JAVA_HOME=<entrypoint_dir_parent>`）。**revision 由 `wl-2` 升到 `wl-3`** | ✅ |
| S9.1 | `reference-plan.schema.json`（第 19 个 schema）：`operation=record_reference_exposure`、`exposure.{scope,variables,path_prepend,value_kind}`、`propertyNames.not.enum` = 17 个注入型变量 | ✅ |
| S9.2 | registry v4：`environment_persist` 表（`capability_id+scope+variable` 主键）、`EnvironmentPersist` 实体、`record/list/forget` 方法、迁移戳 v4；`test_migrated_and_fresh_databases_have_identical_shape` 覆盖新表 | ✅ |
| S9.3 | 新 `caps/environment.py`（`EnvironmentStore` 抽象 + 内存/HKCU 实现 + 有界校验）与 `caps/exposure.py`（plan → apply → forget） | ✅ |
| S9.4 | `airoot env activate --shell powershell\|cmd`、`airoot exec <external_id> -- <cmd>`：只输出脚本或起子进程 | ✅ |
| S9.5 | `airoot env list / persist / forget`：`persist` 消费 approval token（`reference-plan` 校验 + 绑定 `plan_hash`），无 token 时**写出 plan 文件并以 `PERSISTENCE_REQUIRES_APPROVAL`（退出码 4）停下**，`--scope machine` → `PRIVILEGE_REQUIRED`（5） | ✅ |
| S9.6 | 本文档、`docs/schema/README.md`、`AGENTS.md`、诊断码表 | ✅ |

**三处与 §17.5 草稿命令的偏差**（实现时按更精确的语义定了，如实记录）：

1. `env activate` / `env persist` / `env forget` / `exec` 的第一个参数是 **`external_id`**
   （如 `external/dr-env/java`），不是 `capability_id`。理由：同一 capability 可能有多个
   reference（不同数据根、不同版本），按 capability 选择等于隐式挑一个，正是本模型要避免的
   猜测；`where` 才是按 capability 选择的地方。
2. `exec` 的拼写是 **`airoot exec <external_id> -- <cmd…>`**（不是 `exec --env <cap>`）。
3. 新增了草稿没写的 `--dry-run`：`env persist --dry-run` 只打印 plan；`env forget --dry-run`
   只做读取并报告将要还原/移除的变量，**不消费记录**——这是 `env forget` 唯一可在测试里跑
   完整路径的方式（真实 HKCU 写入只允许出现在 §17.3-4 的临时键集成测试里）。

**额外发现并修掉的一个真实缺陷**：`PATH` 记录的 `value` 是**合并后**的值，若直接拿它去反合并，
会把用户原有的 `C:\Windows` 等条目一起删掉。现在 `added_path_entries()` 用「现值 − 记录中的旧值」
求出**本次真正新增的条目**再定点移除；`test_forget_is_surgical_for_path` 断言"后来别人追加的
路径必须活着"。

### 17.7 真实机器验收暴露的三个问题（都已修 + 有回归测试）

这一步的价值就在这里：**契约在纸面上自洽，不等于在真实 `D:\env` 上成立**。

1. **入口点丢掉了所在目录**（真机：`D:\env\Java\jdk-25.0.2\bin\java.exe`）。
   白名单声明 `entrypoints: ["java.exe"]`，而登记时写成 `rule.entrypoints or (executable.name,)`，
   于是 reference 里只剩一个裸文件名 `java.exe`。后果：`env activate` 会把
   `D:\env\Java`（**不含任何入口点的容器目录**）加进 PATH，`where` 报的入口点也无法定位。
   **修法**：登记的是**观测到的事实**——相对对象根的路径 `jdk-25.0.2/bin/java.exe`；
   白名单里的名字只是"用来发现它的谓词"，不是位置。回归测试
   `test_entrypoint_keeps_its_directory`。
2. **`JAVA_HOME` 指向容器而非版本目录**。原白名单写 `JAVA_HOME=<object_root>` +
   `path_prepend: ["bin"]`，这只对扁平的 `…\Java\bin\java.exe` 成立；真实布局是
   `…\Java\jdk-25.0.2\bin\java.exe`，于是 `JAVA_HOME=D:\env\Java`（**不是一个 JDK home**），
   PATH 里还多了一条不存在的 `D:\env\Java\bin`。
   **修法**：新增模板 `<entrypoint_dir_parent>`（入口点目录的父目录），两者都变成事实：
   版本化布局 → `…\jdk-25.0.2`；扁平布局 → 恰好等于 `<object_root>`。**同一个模板适配两种布局，
   没有任何"看见 `bin` 就往上跳一层"的猜测**。`<object_root>` 保留给真正的容器型变量
   （`test_a_container_declaration_still_works` 证明这种写法仍然可用）。
3. **`exec --json` 会被子进程输出污染**。子进程继承 stdout，`cmd /c echo %JAVA_HOME%` 的输出
   直接打在 JSON 文档前面，调用方无法解析自己的工具。
   **修法**：`--json` 是机器模式——捕获子进程 `stdout`/`stderr`（各留最后 64 KB）并放进文档的
   `stdout`/`stderr` 字段；不带 `--json` 时仍然继承流（交互式调用要的就是这个）。

**一处被真机否掉、但我最后**没有**改的行为**（记录下来，避免下次重复摇摆）：`scope decide java`
是**要询问**的。设计之初我把"凡是 runtime 都询问"当成噪音来源想去掉，真机验证时又读回 §12.1：
"装 runtime"正属于那条封闭清单里的"**创建环境**"，而且它是**唯一**一个"项目内隔离 vs 数据根"
真会改变结果的类别（每项目的 nvm/conda vs 一份共享解释器）。**必须不询问的是"正确答案永远一致"
的那一类**——白名单里 `kind: "tool"` 的单文件工具（jq / 7z / rg / ffmpeg）。
判据已写进 `caps/planner.py` 的注释与 `test_a_runtime_asks_because_it_creates_an_environment` /
`test_cli_a_generic_tool_does_not_ask` 两侧断言。

**仍然延后**（不要假装完成）：`.ai/tooling.json` 的**写入**（P2 human approval 通道）；
machine 级持久化（P2 broker）；`where` 的 steward-first 改写与 `doctor` 数据根诊断（步骤 5/6，
改冻结契约，需评审）；删除分级（步骤 11）与能力边界增长流程（步骤 12）。

**验收标准 2 的实际跑法**（真实机器，无需管理员权限）：

```text
airoot scope decide java --json                       # 数据根，不询问
airoot data-root add D:\env --role runtime --json
airoot discover --json
airoot adopt D:\env\java --mode reference --json
airoot env activate external/dr-env/java --shell powershell   # 只打印脚本
airoot env persist external/dr-env/java --dry-run --json      # 只打印 plan
airoot env persist external/dr-env/java --token-file <t.json> # 写 HKCU + 记账
airoot env list --json
airoot env forget external/dr-env/java --dry-run --json       # 先看将要还原什么
airoot env forget external/dr-env/java                        # 精确还原
```

---

## 18. 阶段计划：步骤 5–6（steward-first `where` + `doctor` 数据根诊断）

> **本节是实现计划，不是契约条文。** 步骤 5/6 **要改冻结契约文本**，已获用户批准；改动会同时落到
> 规划 §3.2 / §9.10、本草案 §6/§9、ADR-0006 与代码。完成情况回写到 §16.0。

### 18.1 为什么这两步必须一起做

`where` 与 `doctor` 是同一件事的两面：`where` 回答"用哪一个"，`doctor` 回答"这个答案现在还成不成立"。
只把 reference 提为 `where` 的一等候选、却让 `doctor` 对数据根一无所知，就会出现最坏的组合——
**选了一个没人验证过的对象，而且没有任何诊断能发现它已经漂移**。

### 18.2 子阶段与顺序

| # | 子阶段 | 交付物 | 依赖 |
|---|---|---|---|
| S5.1 | 选择策略面 | **新** `policy/selection-policy.json`（`precedence: steward\|owned` + `revision`）+ `caps/selection.py` 加载期校验 | — |
| S5.2 | `where` 候选序改写 | `caps/where.py`：project → session → **steward reference** ↔ owned machine（由 policy 决定次序）；冲突语义按 §6 表改写；reference 用**活跃版本**参与版本约束 | S5.1 |
| S5.3 | 冻结文本与 ADR | 规划 §3.2:174、§9.10:887-896 改写；ADR-0006 | S5.2 |
| S5.4 | `where` 测试与 golden | 单元 + CLI + 跨文档；两个 golden fixture **改名**（旧名描述的行为已不存在） | S5.2 |
| S6.1 | 数据根诊断（D1 扩展） | `caps/doctor.py`：`DATA_ROOT_MISSING` / `DATA_ROOT_VOLUME_MISMATCH` / `WHITELIST_REVISION_STALE` | — |
| S6.2 | reference 重观测（D3 扩展） | `REFERENCE_STALE` / `REFERENCE_DRIFTED` / `REFERENCE_UNPROBED`；`--verify` 追加 entrypoint digest 对比 | S6.1 |
| S6.3 | `--include-unmanaged` | `doctor --include-unmanaged` → `UNMANAGED_OBJECT_PRESENT`（info，remediation `none`） | S6.1 |
| S6.4 | 码表与验收 | `INVARIANTS` 扩展 → 诊断码表重生成、golden、真机验收脚本加 doctor 段 | 全部 |

每个子阶段结束时必须：`pytest` 全绿 + 旧切片无回归。

### 18.3 本阶段需要做出的决策（含理由）

1. **策略面必须是文件，不能是隐式默认。** §6 说"优先级由 policy 决定"——如果代码里写死 steward，
   那"用户把某个 python 交给 AIROOT 装并希望 owned 优先"就没有出口。因此
   `policy/selection-policy.json` 明写 `precedence` 与 `revision`，并**在 `where` 的 evidence 里
   报出所用 revision**（`where-response` 是 `additionalProperties: false`，不能加字段；`selection_reason`
   与 `evidence` 足够承载）。文件缺失或损坏 → **回落到 steward**（fail-safe 到管家模型的核心，
   而不是回落成"谁都行"）。
2. **reference 的版本约束按"活跃版本"判定，不替用户切版本。** 一个 nvm 对象里可能同时有
   3.11 与 3.12，而 active 是 3.11。若查询要 `>=3.12`，**不选它**：选中意味着把入口指向 active，
   那就答非所问了。正确做法是返回 `VERSION_UNSATISFIED` + 证据
   `"对象内有满足约束的版本但未激活"`——切换活跃版本是用户的决定（且属 owned/受保护动作）。
3. **`REFERENCE_DRIFTED` 必须能在默认 `doctor` 里被发现，但不得整文件读大二进制。**
   owned 的漂移靠安装时 digest 基线（`MANIFEST_DIGEST_MISMATCH`）；steward 没有基线，只有观测对比。
   因此默认路径用 `classify_object` **重观测对象根**（有界只读，只读 PE 头部与 `.rsrc`），
   比较 `version`/`active_version`/`versions`/`architecture`/`entrypoints`；**不**对 entrypoint 做
   整文件 digest —— 那等于每次 `doctor` 都读 120 MB 的 `node.exe`。entrypoint digest 对比放在
   `doctor --verify`（与 owned 的 digest 校验同一个开关，用户显式要求才付这个代价）。
4. **`DATA_ROOT_MISSING` 在两个位置的含义不同，必须写清楚。** 作为 **reason_code** 它是退出码 6
   （需要恢复：`discover` 找不到数据根就没法继续）。作为 **doctor 诊断**它的严重度是 `error`，
   使整体 status 变成 `broken`（退出码 3）——因为 doctor 的退出码**由 status 决定**（冻结规则），
   不由单条码决定。两处都不是 bug，是粒度不同。
5. **`DATA_ROOT_ACL_DRIFT` 现在不发射。** ACL 基线属于 P2（§9 表格自己标了"P2 起生效"）。
   映射已注册，代码里不猜一个基线出来。
6. **`--allow-external-fallback` 保留为 deprecated 兼容别名。** 新模型下 reference 不再是 fallback，
   这个开关**不再改变选择结果**，只在 evidence 里记一条 `deprecated_flag_ignored`。
   直接删掉会让已写好的 Skill/脚本静默改变行为，保留但无效是最诚实的选择。
7. **`doctor --include-unmanaged` 读的是"已记录的观测"，不自己扫数据根。**
   理由：`doctor` 的代价模型是"只重观测已登记的对象"，扫描数据根是 `discover` 的职责（它才需要
   遍历与排除规则）。因此该开关展示的是 `discover --record` 记下的 `unmanaged`/`quarantined`
   行；没有记录就什么都不报，并且**不会**假装"机器上没有未匹配对象"。取舍写在命令帮助里：
   `run 'discover --record' first, doctor never scans data roots on its own`。
   （这条是验收脚本第一次跑出来的：没有 `--record` 时该开关空手而归，是**正确**行为，
   但必须说清楚，否则用户会以为数据根是干净的。）

### 18.4 风险与处置

| 风险 | 处置 |
|---|---|
| 真机重观测产生假阳性 drift | "活跃版本由 A 变为 B"是**用户可见的事实变化**，报出来是对的；真机验收脚本断言"无 error 级诊断"，并在报告里打印每条 drift 的具体字段 |
| `where` 行为变化静默影响既有调用方 | 两个 golden fixture 改名 + 退出码断言改写；`selection_reason` 变更全部落在 fixture 里，Rust 迁移时逐字节对齐 |
| policy 文件被误删导致行为翻转 | 缺失 = steward（模型核心），且 evidence 明说"策略文件缺失，已按默认 steward" |
| doctor 变慢 | 只重观测**已登记**的 reference 对象（不是整个数据根）；`doctor` 不会自动扫描数据根 |
| INVARIANTS 扩展破坏跨文档测试 | 同步重生成诊断码表文档；`test_every_invariant_code_is_registered` 已存在，扩展后必须仍绿 |

### 18.5 验收标准（本检查点）

1. `pytest cli/tests` 全绿；旧切片 `validate-schemas` + `test` 全绿，`schema_count` 仍为 **19**
   （§7.2 结论：`where-response` 与 `doctor-response` **都不需要改 Schema**——`selection_reason` 是自由
   string、`doctor` 的 `code`/`severity`/`remediation` 早已容纳新码）。
2. 真实机器上（无需管理员权限）：

   ```text
   airoot where java --json          # 直接命中 reference，selection_reason=STEWARD_REFERENCE_HEALTHY
   airoot doctor --json              # 无 error 级诊断；数据根与 4 个 reference 被逐一验证
   airoot doctor --include-unmanaged --json   # 多出 3 条 UNMANAGED_OBJECT_PRESENT (info)
   ```

3. 改写后的 S-014：**owned 坏 + reference 健康 → `CURRENT_SOURCE_DEGRADED`（退出码 2），
   `CONFLICT_MANAGED_BROKEN` 不再出现在这条路径上**（该码仍保留给"policy 显式要求冲突"的将来用途，
   或彻底降级为未使用——见 §18.3-1 的登记说明）。
4. 文档与实现一致：规划 §3.2 / §9.10、本草案 §6 / §9、ADR-0006、诊断码表、§16.0。
5. 测试不污染宿主机（同步骤 8–9 的约束）。

### 18.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **359 项全绿**（新增 `test_l1_selection.py` 9 项 +
`test_l1_doctor_steward.py` 10 项 + `test_cli_steward.py` 3 项 + `test_l1_where.py` 3 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；`schema_count` **未变**——
`where-response` / `doctor-response` 都不需要改 Schema（§7.2 的结论被实现证实）。
golden 语料重生成，其中两个 fixture **改名**：`where_conflict_managed_broken` →
`where_owned_broken_degrades_to_reference`（退出码 3 → 2）、`where_explicit_external_fallback` →
`where_deprecated_external_fallback_is_ignored`（两个文档现在只在 deprecated 证据上不同）。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S5.1 | `policy/selection-policy.json`（`precedence` + `revision`，默认 steward）+ `caps/selection.py`：缺失→默认、非法→`INVALID_INPUT`、未知键拒绝 | ✅ |
| S5.2 | `caps/where.py` 重写候选序；`_version_ok()` 让"版本未知"永不满足约束；多版本对象报告 `VERSION_AVAILABLE_BUT_INACTIVE`；`owned_unusable` 独立于扫描顺序计算（否则 steward 先命中时降级分支永不触发） | ✅ |
| S5.3 | 规划 §3.2:174、§9.10:887-896、§9.10:946、验证方案 S-014 改写；**ADR-0006** | ✅ |
| S5.4 | `where` 单元/CLI/golden 测试；两个 fixture 改名 + 退出码断言 | ✅ |
| S6.1 | `_check_data_roots`：`DATA_ROOT_MISSING` / `DATA_ROOT_VOLUME_MISMATCH` / `WHITELIST_REVISION_STALE` | ✅ |
| S6.2 | `_check_references`：`classify_object` 重观测对比（版本/活跃版本/版本集合/架构/入口点/是否仍匹配能力）→ `REFERENCE_DRIFTED`；对象消失 → `REFERENCE_STALE`；无版本证据 → `REFERENCE_UNPROBED`；整文件 digest 只在 `--verify` | ✅ |
| S6.3 | `doctor --include-unmanaged` → `UNMANAGED_OBJECT_PRESENT`（info、remediation `none`） | ✅ |
| S6.4 | `INVARIANTS` 扩展（D1/D3/D4）→ 诊断码表重生成；`test_doctor_never_touches_the_data_root` 断言诊断全程只读 | ✅ |

**实现里两处必须记录的判断**（都是写代码时才暴露的）：
1. **降级判据不能依赖扫描顺序**：steward 槽先命中时循环就 break 了，损坏的 owned binding
   根本没被记进 `unhealthy`，于是"降级"永远不触发、答案变成普通的 `SUCCESS`。改成独立计算
   `owned_unusable`（与 policy 顺序无关）。这一条如果只跑 `precedence=steward` 的测试是发现不了的，
   所以 `test_l1_selection.py` 同时覆盖两种 precedence。
2. **"contents 变了"与"facts 变了"是两件事**：把 `python.exe` 换成非 PE 字节时，**廉价重观测**
   就能发现（对象不再匹配任何能力）——这是好事，写进 `test_a_disappeared_capability_match_is_reported_as_drift`；
   而"事实全没变、只有字节变了"必须留给 `--verify`，因为默认路径不读整文件，
   写进 `test_an_invisible_content_change_is_only_found_with_verify`。两条测试一起才说明白
   `doctor` 的代价模型。

3. **`where` 的 `executable` 必须是入口点本身，不能是对象根**（真机验收发现）。
   reference 的 `path` 是对象根（`D:\env\Java`），而 `where` 的 `executable` 字段喂给
   `effective_state` ——那个函数比较的是**可执行文件的父目录**是否在 PATH 里。指向对象根会让
   "已经在 PATH 上"被报成"看不见"。现在解析成 `D:\env\Java\jdk-25.0.2\bin\java.exe`，
   并顺带在 entrypoint 缺失时把该 reference 判为不可用（stale 证据写进 candidates）。

4. **`discover --record` 少刷新派生状态**（真机验收发现）。它只在"数据根缺失"时调用
   `update_projection()`，于是刚记录完 3 条观测，`doctor` 立刻报 `AUDIT_PROJECTION_DRIFT`。
   权威事件变了、派生投影没跟上——这正是 D10 要抓的东西，而抓到的原因是命令自己的缺陷。
   现在只要 `recorded` 非空（或有缺失数据根）就刷新投影，并加了回归测试
   `test_cli_discover_record_keeps_derived_state_in_step`。

**仍然延后**：删除分级（步骤 11，§14）、能力边界增长流程（步骤 12，§15）、machine 级环境变量
（步骤 10，依赖 P2 broker）。`DATA_ROOT_ACL_DRIFT` 的映射已注册但**不发射**（ACL 基线属 P2）。

---

## 19. 阶段计划：步骤 11–12（删除语义分级 + 能力边界增长流程）

> **本节是实现计划，不是契约条文。** §14/§15 的条文在本次施工前**尚未落地**；本阶段把它们
> 变成代码与测试，并把实现中必须做的判断回写到 §14/§15 与 ADR-0007。完成情况回写 §16.0。

### 19.1 范围与理由

步骤 11 与 12 是同一条边界的两个方向：

```text
步骤 11（删除分级）：AIROOT 可以删什么、以什么代价删、删不掉时怎么办
步骤 12（能力边界）：AIROOT 可以管什么、新能力怎么进来、凭证是什么
```

只做 11 而不做 12，会留下"能删但没有准入判据"的口子；只做 12 而不做 11，则"不可删"只是口号。
两者都不需要管理员权限。

### 19.2 子阶段与顺序

| # | 子阶段 | 交付物 | 依赖 |
|---|---|---|---|
| S11.1 | registry v5 + 投影字段 | `instances.collected_at` / `collected_approval_id`；迁移 v5；`registry-projection.schema.json` 加两个**可选**字段 | — |
| S11.2 | 所有权判据 | `caps/lifecycle.py`：`is_owned()`（`store_path` 在 `store/` 前缀内且 `install_backend_id != external`）；对 reference 调 `uninstall` → `OWNERSHIP_REQUIRED`(7) | — |
| S11.3 | `retire` | 取消 active binding + `lifecycle_status=retired`，**payload 不动**；generation CAS + 单事务；无需批准 | S11.2 |
| S11.4 | `gc --plan` | 可回收清单 + 每个 instance 一份 canonical `plan`（`operation=gc_apply`、`operations[].kind=delete`、`source_mutation=delete`） | S11.3 |
| S11.5 | `gc --apply` | 消费 approval token；**重新检查**准入判据；删 `store/<instance>`；写 `collected_at`；`GC_APPLIED` 事件；刷新投影 | S11.4 |
| S11.6 | `uninstall` | 组合动词 = `retire` + `gc`；无 token 时落盘 plan 并以 `APPROVAL_REQUIRED`(4) 停下 | S11.5 |
| S11.7 | 引用检查 | 仍被 active binding / rollback 保留 / 未完成 transaction / project manifest 引用 → 拒绝（`REFERENCE_IN_USE`(2) 或 `INSTANCE_CONFLICT`(7)），不删任何东西 | S11.4 |
| S12.1 | 能力清单的机器可读冻结 | `policy/capabilities.json`：已冻结的 `capability_id` 清单 + 每个能力的 `kind`/CLI 入口/`side_effects` 上限；白名单加载期校验"条目必须有冻结能力" | — |
| S12.2 | 准入判据落码 | `caps/boundary.py`：§15.2 三条件 + §15.3 排除项判定，产出 `admission` 结论与证据 | S12.1 |
| S12.3 | 增长流程 | `airoot capability list` / `airoot capability check <path>`：只读地说明"这个对象能不能被管、缺哪一条" | S12.2 |
| S12.4 | 验收与文档 | 全套测试、golden、§14/§15 回写、ADR-0007、真机验收脚本加一段 | 全部 |

### 19.3 本阶段需要做出的决策（含理由）

1. **`retire` 不是事务，`gc` 才是。** `retire` 只改 binding 与状态、payload 不动，因此它是一条
   **声明式变更**：单次 `registry.write(bump=True)`（binding 与 generation 同一个 SQLite 事务，
   满足"唯一提交点"的实质），幂等，不需要批准。`gc` 会删文件，所以它必须是 **plan → approval →
   apply**（§9:847），且 apply 时**重新检查**准入判据——批准的是"这个 hash 的操作"，不是"当时那张清单"。
2. **删除后不删行，只记 `collected_at`。** `bindings.instance_id` 有外键指向 `instances`，
   删 instance 行会连带破坏 binding 历史；而"历史"正是 `retired` 存在的理由。因此 payload 删除后
   instance 行保留，用 `collected_at` 表达"这份 payload 已被主动回收"。
   `doctor` 依据它区分**主动回收**（不是缺陷）与**payload 消失**（`PAYLOAD_MISSING`，缺陷）。
   这是唯一需要迁移与 Schema 可选字段的地方，`lifecycle_status` 枚举**不动**。
3. **`uninstall` 不是新机制，是组合**。它必须走 `retire → gc`，并拒绝 `--force`
   （§14.2-4）。代价是"一条命令两个阶段"，好处是"删不掉时永远是 retire 成功的状态"。
4. **reference 永远没有 `uninstall`；用户想删自己的东西时 AIROOT 只输出路径与建议命令**
   （§14.2-1）。这条是"不拥有就不代劳"的直接体现，用 `OWNERSHIP_REQUIRED`(7) 表达，
   并在响应里给出要删的绝对路径。
5. **能力边界必须机器可读**（S12.1）。§15.4 的"提出 capability → 冻结 → 写白名单"流程里，
   "冻结"目前只是文档里的一句话；`policy/capabilities.json` 把它变成可校验的事实，
   并让白名单加载期就能拒绝"引用了未冻结能力"的条目。这样"全能"是可增长的，而不是无边界扩张。
6. **`capability check` 只回答三个准入条件，不安装、不改任何状态。** 它是给 Skill 用的
   "能不能管"的确定性判据，避免 Agent 自己去猜。

### 19.4 风险与处置

| 风险 | 处置 |
|---|---|
| `gc --apply` 删错东西 | 准入判据在 plan 与 apply 各算一次；只删 `store/<instance_id>` 这一个目录；路径先经 `from_root_relative` 证明在 root 内；测试断言"数据根与 root 之外的文件一字不动" |
| 半删除 | 先写 `GC_APPLIED` 事件与 `collected_at` 的**意图**记录，再删目录，最后确认；中断后 `repair` 看到的是"标记了未删"或"删了未标记"，两种都可幂等收敛 |
| 误把 reference 当 owned | `is_owned()` 只认 `store/` 前缀 + 非 external 后端；测试覆盖"reference 调 uninstall → 7，文件不动" |
| 能力清单变成第二个白名单（重复真相） | `capabilities.json` 只声明**能力身份与副作用上限**，证据谓词仍只在白名单里；两者职责写进文件头，并有测试断言"白名单里的每个能力都在清单里" |

### 19.5 验收标准（本检查点）

1. `pytest cli/tests` 全绿；旧切片 `validate-schemas` + `test` 全绿，`schema_count` 仍为 **19**
   （`registry-projection` 加的是**可选**字段；`plan.schema.json` 早已有 `retire_tool`/`gc_apply`
   与 `delete` 操作，无需改）。
2. 真实机器上：

   ```text
   airoot tool retire <owned-capability> --json     # binding 消失，payload 仍在
   airoot tool gc --plan --json                     # 只有 retired 且无引用者进入清单
   airoot tool gc --apply --token-file <token>      # 需要批准；删后 where=NOT_FOUND、doctor 无残留
   airoot uninstall <reference> --json              # OWNERSHIP_REQUIRED(7)，只输出路径
   airoot capability check D:\env\java --json        # 三个准入条件逐条给结论
   ```

3. S-032…S-036 与 C-031…C-033 全部有测试：文件树 digest 不变（对 reference/数据根）、
   拒绝未冻结能力、拒绝名字像但证据不符。
4. 文档与实现一致：§14/§15 回写、ADR-0007、诊断码表（若新增码）、§16.0 进度表、`AGENTS.md`。

### 19.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **394 项全绿**（新增 `test_l1_lifecycle.py` 17 项 +
`test_cli_lifecycle.py` 4 项 + `test_l1_boundary.py` 14 项）；旧切片 `validate-schemas`
（`schema_count: 19`）与 `test` 全绿。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S11.1 | registry **v5**：`instances.collected_at` / `collected_approval_id`；`registry-projection.schema.json` 加**可选** `collected_at`；迁移与 fresh 库的 `instances` 列形状由既有测试逐一比对 | ✅ |
| S11.2 | `caps/lifecycle.py`：`is_owned()`（`store/` 前缀 **且** 非 external 后端双信号）；对 reference 调 uninstall → `OWNERSHIP_REQUIRED`(7)，响应里给出**绝对路径**与合法的 `forget` 命令 | ✅ |
| S11.3 | `retire`：单事务清 binding + `lifecycle_status=retired` + generation bump + `RETIRED` 事件；幂等；payload 与 store digest 不变 | ✅ |
| S11.4 | `gc --plan`：准入判据 + 每个 instance 一份 canonical `plan`（`operation=gc_apply`、`delete`、`source_mutation=delete`、`reversible=false`），落盘 `state/plans/` | ✅ |
| S11.5 | `gc --apply`：消费 token；**重新**检查准入与 digest；`GC_INTENT` → 删目录 → `GC_APPLIED` + `collected_at`；`doctor` 不再把主动回收报成 `PAYLOAD_MISSING` | ✅ |
| S11.6 | `uninstall` = `retire` + `gc`；无 token 时落盘 plan 并以退出码 4 停下；**没有 `--force`** | ✅ |
| S11.7 | 引用检查：active binding / 未完成 transaction / 未 retired / 非 owned / 已回收 逐条给出 blocker，任一命中即拒绝且不删任何文件 | ✅ |
| S12.1 | `policy/capabilities.json`（revision `cap-1`，8 个能力）+ `caps/boundary.py`；**白名单加载期**校验"条目必须在清单里"，未知键/重复/超界副作用一律拒绝 | ✅ |
| S12.2 | §15.2 三条件判定 `check_admission()`，逐条给结论与证据；不可回滚副作用的能力**不允许被冻结** | ✅ |
| S12.3 | `airoot capability list` / `capability check <path>`：只读；后者会先做一次只读发现再判定 | ✅ |

**§82 已改判**：上表 S12.1 的 `cap-1` / 8 个能力是 §12 当时的事实。**今天冻结清单是 `cap-2` / 7 个能力**——`media_probe` 在 §82 被移除（ADR-0025 的 D9），理由写在清单文件自己的注解里。原数字不改，因为它记的是那一轮。

**实现里三处必须记录的判断**：

1. **删除后不删 registry 行，只记 `collected_at`**。`bindings.instance_id` 有外键指向 `instances`，
   删行会连带毁掉 binding 历史——而历史正是 `retired` 存在的理由。因此 `lifecycle_status` 枚举
   **不动**，用新列表达"这份 payload 已被主动回收"，`doctor` 据此区分主动回收与 payload 消失。
   这是唯一需要迁移 v5 与 Schema 可选字段的地方（规则 2：新增可选属性 = minor）。
2. **`retire` 故意不是事务**：它不移动、不删除任何文件，是一条声明式变更；但 binding 写入与
   generation bump 仍在**同一个 SQLite 事务**里，这才是"唯一提交点"保护的实质。
   `gc` 会删文件，所以它必须是 plan → approval → apply，且 apply 时**重算**准入判据——
   批准的是一份 plan hash，不是当时那张清单。
3. **`GC_INTENT` 与 `GC_APPLIED` 之间才是删除**：中断后要么"标记了没删"、要么"删了没标记"，
   两种都能被识别并幂等收敛（`collected_at` 已存在时 `apply` 直接返回 `already_collected`）。

**仍然延后**：machine 级环境变量（步骤 10，依赖 P2 broker）；`DATA_ROOT_ACL_DRIFT` 的发射
（ACL 基线属 P2）；真实 Install Backend（步骤 11 的 `gc` 已能收集，但 P1 只有 `fake_fixture`
一个后端，所以"删真实下载物"要等 P4）。

---

## 20. 阶段计划：§12.4 的 `plan` 形态（分流 → 计划 → 批准）

> **本节是实现计划。** §12.4 定义了命令形态与 `--dry-run` 的交付内容，但步骤 8 只实现了
> **决策层**（`scope decide`）与既有 `plan`（无 scope/target）。本阶段把两者接成一条命令，
> 让"装哪里"这件事在**一个** plan 文件里被批准与审计。完成情况回写 §14/§16.0。

### 20.1 为什么必须接起来

现状是两段互不相干的回答："`scope decide` 说这该放数据根"和"`plan` 生成一份安装计划"。
中间没有约束关系，于是**分流结论可以不被任何审批覆盖**——`plan` 不记录 scope，`approve` 也就
不知道自己在批"装到全局"还是"装进项目"。§12.3 明确要求"项目 → 数据根的 scope 提升**必须显式批准**"，
这条只有把 scope/target 写进 plan 才可能成立。

### 20.2 交付物

| # | 内容 |
|---|---|
| S20.1 | `airoot plan <capability> --scope project\|data-root --target <path\|data-root:id> --dry-run` |
| S20.2 | `--dry-run` 无副作用，返回：目标位置、预计体积（或 `null` + `SIZE_ESTIMATE_UNAVAILABLE`）、是否需要确认、需要哪个 scope 的批准、`side_effects` |
| S20.3 | 需要确认时**不生成计划文件**，返回三选一 + `SCOPE_CONFIRMATION_REQUIRED`(4) |
| S20.4 | 显式 `--scope data-root` 覆盖一份"project 结论"时 → `SCOPE_UPGRADE_REQUIRES_APPROVAL`(4)（S-010/C-020） |
| S20.5 | 非 `--dry-run` 且无需确认时：生成计划并把**路由决定**写进 `metadata.routing`（plan Schema 的 `metadata` 是自由对象，无需改 Schema） |
| S20.6 | 测试（C-017…C-022 中可覆盖的部分）+ 文档回写 |

### 20.3 需要做出的决策（含理由）

1. **路由决定写在 `metadata.routing`，不新增顶层字段。** `plan.schema.json` 是
   `additionalProperties: false`，加字段要新 schema id；而 `metadata` 本来就是自由对象，
   且路由是**决策证据**而非安装语义。`schema_count` 因此仍为 19。
2. **"需要确认"时拒绝产出计划文件。** 一份未获确认的计划一旦落盘，就可能被别的路径 `approve`；
   不落盘是让"还没决定"这件事在文件系统上也可证明。
3. **`--dry-run` 的体积未知必须如实说未知**（`null` + `SIZE_ESTIMATE_UNAVAILABLE`，退出码 0）。
   §12.2 把"编造体积"列为要避免的行为。
4. **显式 scope 与策略结论冲突时，一律取更谨慎的一侧**：请求 global 而结论是 project →
   `SCOPE_UPGRADE_REQUIRES_APPROVAL`；请求 project 而结论是 data-root → 允许（收窄自己不需要批准）。
5. **`--target data-root:<id>` 必须存在于 registry**，否则 `DATA_ROOT_MISSING`；路径形式的 target
   必须是一个真实目录（`INVALID_INPUT`），因为"目标不存在"与"目标写错"要分开。

### 20.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**。
2. 真机上：

   ```text
   airoot plan <cap> --scope data-root --target data-root:dr-env --dry-run --json   # 体积未知就说未知
   airoot plan <cap> --project <有清单的项目> --dry-run --json                       # project，不询问
   airoot plan <cap> --scope data-root --project <有清单的项目> --json               # SCOPE_UPGRADE_REQUIRES_APPROVAL(4)
   airoot plan <cap> --creates-environment --json                                    # SCOPE_CONFIRMATION_REQUIRED(4)，无计划文件
   ```
3. 未获确认时 `state/plans/` 里**没有**新文件。

### 20.5 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **407 项全绿**（新增 `test_l1_plan_routing.py` 13 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿。`plan.schema.json` **未改**——
路由决定写在自由的 `metadata.routing` 里（§20.3-1）。

**实现里必须记录的一处判断（它是本阶段最有价值的发现）**：路由原先**只**看发现白名单，
于是 `plan fake-tool` 直接报 `CAPABILITY_NOT_DECLARED`——因为 `fake-tool` 是"AIROOT 自己装的"
能力，**没有也不需要**发现谓词。这说明两件事被混在了一起：

```text
能力是否存在      → policy/capabilities.json（冻结清单，步骤 12 才建立）
对象如何被识别    → discovery-whitelist.json（只读扫描用的证据谓词）
```

现在 `decide_scope` 先查白名单，查不到再查冻结清单取 `kind`；两者都没有才拒绝。
**这不是为了通过测试，而是把 §15.2-1 的"存在性判据"真正接进了分流层**——否则"AIROOT 自己
安装的工具"永远无法被路由，而那恰恰是 owned 域存在的原因。

**仍然延后**：Install Backend 抽象与真实下载（P4）；`SKILL.md` 适配层；machine 级环境变量（P2）。

---

## 21. 阶段计划：Install Backend 抽象（获取与提交步骤）

> **本节是实现计划。** 到本轮为止，`install` 只能跑 `fake_fixture`——一个确定性的假 payload。
> 三大核心契约 §4.4 早就冻结了后端接口与九个声明字段，ADR-0001 的 Rust 工具链获取也依赖它。
> 本阶段把"获取 + 提交"变成可插拔且有声明的一层。完成情况回写 §16.0。

### 21.1 为什么这是关键路径

`plan`/`approve`/`install` 的事务骨架已经完整，缺的是**谁把字节搬到 stage**。
只要这一层没有真实实现：

- 路线图 P4（真实安装）无法开始，ADR-0001 的 Rust 工具链无法按规范获取；
- `gc` 已经能收集，但没有真实 payload 可收（§19.6 记录过这一点）；
- "无脚本 artifact"这条 v1 安全基线**没有任何代码在守**。

### 21.2 交付物

| # | 内容 |
|---|---|
| S21.1 | `caps/backends/base.py`：九个冻结声明字段 + `discover/plan/fetch/verify/stage/commit/expose/inspect/rollback` 协议 + `resolve_backend()` |
| S21.2 | `caps/backends/portable_file.py`：本地**单文件无脚本** artifact（`network_access=false`、`source_mutation=none`） |
| S21.3 | `caps/backends/https_artifact.py`：HTTPS 单文件下载 + 强制 SHA256 校验（`network_access=true`），ADR-0001 需要它 |
| S21.4 | 无脚本守卫：拒绝脚本型 payload 与含安装脚本的归档；拒绝 `executes_scripts=true` 的声明走低风险路径 |
| S21.5 | 事务接缝：`SimulationRunner` 改为接受一个 backend，状态机/日志/generation 语义**一字不改**；`install` 按 plan 的 source 解析后端 |
| S21.6 | 测试 + 文档（§21.6、ADR-0008、`AGENTS.md`、`docs/schema/README.md` 若触及契约） |

### 21.3 需要做出的决策（含理由）

1. **后端只做"获取与提交"，绝不碰 binding。** §决策3:47 明说"Install Backend 只负责获取和提交步骤，
   不能自行取得 active binding"。因此后端接口里没有 `bind`，而 `expose` 只**报告**可暴露的入口，
   真正的 active binding 仍由 `ACTIVE_BOUND` 那一步写。
2. **`source_mutation` 永远是 `none`。** 后端不得在 `commit` 里顺手删除/移动用户的源文件；
   需要 source 变更必须由 plan 显式列出并单独批准（§4.4:352）。
3. **HTTPS 只允许 https://，且禁止重定向到 http。** 明文回退是供应链攻击的常见入口；
   任何重定向后的最终 scheme 必须是 https，否则 `PROVENANCE_FAILED`。
4. **SHA256 是必填项，不是可选项。** 没有 digest 就没有 `verify`，也就没有 `commit`
   （`DIGEST_MISMATCH`/`INVALID_PLAN`）。v1 不做签名，但绝不做"只验长度"。
5. **`fake_fixture` 保持原样**：golden 语料与既有测试依赖它，不能为了统一而改它的输出。
   真实后端是**新增**，不是替换。
6. **后端声明进 plan 的 `metadata.backend`**（`plan.schema.json` 的 `metadata` 是自由对象），
   这样"批准的是哪个后端"可审计，且 `schema_count` 仍为 19。

### 21.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**。
2. 端到端（离线即可复现）：把一个本地无脚本 artifact 通过 `portable_file` 装成 owned instance，
   `where` 命中、`doctor` 无异常、`tool retire` + `gc --apply` 收掉它，**源文件全程未被改动**。
3. 篡改字节 → `DIGEST_MISMATCH`，且 store 里不留半成品。
4. 脚本型 payload（`.bat/.cmd/.ps1/.sh`）与声明 `executes_scripts=true` → 拒绝。
5. HTTPS 后端：本地回环服务器验证成功路径与 digest 不符路径；`http://` 与"重定向到 http"被拒。

### 21.5 明确不做（避免假完成）

- 不做 TLS pinning/签名验证（v1 只做 digest + allowlist；§4.4:368 的签名属后续）；
- 不做断点续传（`supports_resume` 声明为 `false`，如实返回）；
- 不做 pip/conda/npm/厂商安装器（§决策3:49：脚本型后端另行建模）；
- 不改事务状态机、不改 `ACTIVE_BOUND` 语义、不改 golden 输出。

### 21.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **430 项全绿**（新增 `test_l2_backends.py` 23 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；真机验收 PASS。
**`schema_count` 未变**——后端声明写在 plan 的 `metadata.backend`（自由对象，§21.3-6）。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S21.1 | `caps/backends/base.py`：九个冻结声明 + 九步操作协议 + `resolve_backend()`；`source_mutation != none` 的后端**根本无法构造** | ✅ |
| S21.2 | `portable_file`：本地单文件无脚本 artifact（`network_access=false`、`source_mutation=none`、8 GiB 上限） | ✅ |
| S21.3 | `https_artifact`：HTTPS 下载 + 强制 SHA256 + 有界大小/超时 + **重定向降级到 http 即拒** | ✅ |
| S21.4 | 无脚本守卫 `assert_script_free()`：`.bat/.cmd/.ps1/.sh/.py/.vbs/.js` 一律拒；`executes_scripts=true` 的后端不得进入核心事务 | ✅ |
| S21.5 | `tx/artifact.py` 的 `ArtifactRunner`（同一状态机/journal/generation 语义）+ `install`/`repair` 按 plan 的 backend 分发（`fake_fixture` 原样保留，golden 不动） | ✅ |
| S21.6 | 测试 + §21.6 + ADR-0008 + `AGENTS.md` | ✅ |

**实现里必须记录的三处判断**：

1. **两个 digest 含义必须分开**：`plan.source.integrity.artifact_digest` 是**源文件**的 sha256
   （`fetch`/`verify` 对照 plan 检查），`instance.artifact_digest` 是**自有 payload**的树摘要
   （`doctor` 事后复查、`MANIFEST_DIGEST_MISMATCH` 的依据）。把它们合并会让"安装后 payload 被改"
   对真实 artifact 变得不可检测——而那是 D3 的核心能力。
2. **`fake_fixture` 不做接口改造**：golden 语料与既有事务测试逐字节钉在它上面；frozen 接口是对
   **artifact 后端**的要求，测试里如实区分（`test_backend_operation_set_is_the_frozen_set` 有注释）。
   为了"统一"去改它会污染 Rust 迁移的验收面。
3. **`repair` 也按 plan 的 backend 分发**：否则 resume 会用另一个后端重新物化出与批准内容不同的
   字节。这是"批准的是 plan hash"的直接推论。

**"真的能装真东西"的端到端证据**：把一个本地无脚本 artifact 经 `portable_file` 装成 owned
instance（`FINALIZED`、`lifecycle_status=active`），`where` 命中、`doctor`（含 `--verify`）无异常、
`tool retire` + `gc --apply` 收掉它，**源文件全程未被改动**（§21.4-2 的五条断言都在测试里）。

**仍然延后**：TLS pinning / 签名验证（v1 只做 digest，§4.4:368）、断点续传（`supports_resume=false`）、
pip/conda/npm/厂商安装器等脚本型后端（§决策3:49）。真实下载需要网络，本阶段只证明**机制**成立。

---

## 22. 阶段计划：Skill 适配层（`AIROOT\SKILL.md`）

> **本节是实现计划。** 规划 §5.2:495-553 与 §16.0–§16.4 早已冻结 Skill 的目录位置、行为边界与
> "负责/不负责"清单，但 `AIROOT\SKILL.md` 至今不存在——形态的第一条一直是空的。
> 完成情况回写 §22.6。

### 22.1 为什么"缺 Skill"是实质缺口而不是装饰

形态的六项里只有 CLI 存在：

```text
AIROOT = Skill 适配层（AIROOT\SKILL.md） + 稳定 CLI/API + 权限边界 + 注册表 + 事务引擎 + 扩展运行时
```

没有 Skill 时，Agent 面对的是一个**没有使用说明的接口**：它不知道先问 `doctor` 还是先 `where`、
看到 `policy_only` 该说什么、`--allow-external-fallback` 已经失效、`env persist` 需要 token。
结果就是把"解释契约"留给每个 Agent 即兴发挥——而 §16.2 明说这件事不能即兴。

### 22.2 交付物

| # | 内容 |
|---|---|
| S22.1 | `AIROOT\SKILL.md`：front-matter（`name`/`description`）+ 行为边界 + 命令地图 + 绝不做的清单 |
| S22.2 | `AIROOT\agents\airoot.json`：机器可读的调用元数据（问题 → 命令 → 退出码语义） |
| S22.3 | `AIROOT\references\`：按需参考（reason code 速查、确认协议），**不复制可变运行状态** |
| S22.4 | **漂移守卫测试**：文档里的每条命令必须真实存在于 CLI；三选一必须等于 `CONFIRMATION_OPTIONS`；列出的退出码必须等于 `REASON_EXIT` |
| S22.5 | 文档回写（§22.6、`AGENTS.md`） |

### 22.3 需要做出的决策（含理由）

1. **Skill 根是仓库根 `D:\AIRoot`，入口是 `D:\AIRoot\SKILL.md`**——§5.2:495 明写"AIROOT 本身就是
   Skill 目录"，`cli\` 只是它的子目录。写进 `cli\` 是明确的违规。
2. **`search` 必须标为"未实现（P3）"而不是当作可用命令。** §16.3 的示例用了 `airoot search`，
   但 P1 没有它。漂移守卫因此需要一份**显式的"已文档化但尚未实现"清单**并检查它本身——
   不能静默跳过，否则 SKILL.md 会教 Agent 调用不存在的命令。
3. **可变运行状态不进 Skill 文本**（§16.0:1715）：不写 root 路径、不写版本号、不写 registry 内容；
   只写"怎么问、怎么解释、什么时候停"。
4. **`policy_only` 与降级必须原样解释**（§16.0:1721）：不得把 `policy_only` 说成"已保护"，
   也不得把 `CURRENT_SOURCE_DEGRADED` 说成失败。
5. **调用元数据要机器可读**：Agent 不该靠读散文选命令。`agents/airoot.json` 给映射表，
   SKILL.md 讲原则。
6. **漂移守卫才是这一阶段的真正产物**：不会被测试的 Skill 文档，两周后必然与 CLI 不一致。

### 22.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**。
2. `D:\AIRoot\SKILL.md` 存在，`name`/`description` 可被加载器识别；不含绝对路径与运行状态。
3. 漂移守卫覆盖：命令存在性、三选一、退出码映射、未实现命令白名单。
4. 本轮**零运行时代码改动**（只新增文档与测试），真机行为不变。

### 22.5 明确不做

不实现 `search`（属 P3，本轮只如实标注）；不写 `agents/` 的 UI 渲染；不改任何 CLI 行为。

### 22.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **443 项全绿**（新增 `test_l1_skill.py` 13 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿。**本轮零运行时代码改动**——
只补适配层与它的一致性守卫，CLI 行为一字未改（§22.5 的约定被遵守）。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S22.1 | `D:\AIRoot\SKILL.md`（Skill 根 = 仓库根）：front-matter + 行为边界 + 命令地图 + 三选一 + 退出码 + 绝不做的清单 | ✅ |
| S22.2 | `agents\airoot.json`：21 条"问题 → 命令 → 该读哪些字段"的机器可读映射 + `never` + `not_implemented` + `honesty` | ✅ |
| S22.3 | `references\reason-codes.md`（按退出码分组的速查）+ `references\confirmation.md`（判据、三选一、批准形状、记忆语义） | ✅ |
| S22.4 | **漂移守卫 13 项**：命令存在性、`not_implemented` 为真且非空、三选一 == `CONFIRMATION_OPTIONS`、退出码表 == `REASON_EXIT`/`EXIT_MEANINGS`、reference 里不出现未注册码、front-matter 可识别、无绝对路径/运行状态、`policy_only` 不得被说成"已保护"、禁用措辞逐字出现 | ✅ |
| S22.5 | §22.6 + §16.0 + `AGENTS.md` | ✅ |

**实现里三处必须记录的判断**：

1. **`search` 必须被显式列为"未实现"，而且这个清单本身要被测试。** 规划 §16.3 的示例用了
   `airoot search`，但 `file_search` 属 P3。漂移守卫因此不是"跳过未实现命令"，而是
   **断言这份白名单为真**：一旦 `search` 真的实现，`test_unimplemented_commands_are_listed_and_really_unimplemented`
   会失败，逼着把文档补上。文档与实现的漂移在两个方向上都应该响。
2. **命令提取不能靠宽松正则。** 第一版 `airoot\s+(\w+)` 把 front-matter 里的
   `name: airoot\ndescription: …` 里的 `description` 当成了命令——**这正是"文档被当成数据解析"
   的经典翻车方式**。现在只认同行、空格分隔、且后面不跟冒号的动词，并把"必须存在"的断言留给
   真正的命令表（从 parser 的 `choices` 取，而不是手写一份）。
3. **`.ai/tooling.json` 的"只读"性质写进了 Skill**：它记录"上次选了什么"，不是授权凭据；
   连 AIROOT 自己都不写它。Agent 最常见的越权想像就是"我之前选过，所以这次不用问"。

**与 §22.4-4 的偏差（如实记录）**：原计划"真机验收脚本加一段按 SKILL.md 解释输出"没有加。
理由：那段是**给人读的**，脚本无法断言"解释得对不对"；而机器可断言的部分（命令存在、选项一致、
退出码一致）已经全部落在 `test_l1_skill.py` 里，且比脚本更强的约束。真机验收现有的 `doctor`
段落已经产出被解释的那份输出。

**仍然延后**：`search` / `rebuild` / `reconcile`（P3 及以后），`agents/` 的 UI 渲染细节，
以及 P2 的受保护状态（ACL / broker / machine PATH）。

---

## 23. 阶段计划：来源清单（可信来源 + 校验和解析）

> **本节是实现计划。** §21 让"拿到字节→验证→提交"成立，但没有回答"**从哪拿**、**拿什么**、
> **凭什么相信这一份**"。三大核心契约 §4.4:368 已给出边界：v1 可以只做 digest + allowlist，
> 但**来源与 hash 不能混为一谈**。完成情况回写 §23.6。

### 23.1 为什么这是 ADR-0001 的最后一块

ADR-0001 要求用 AIROOT 自己的 plan/approval/fetch/verify/digest 规范获取 Rust 工具链
（`rustup-init.exe` + HTTPS + `SHA256SUMS`）。§21 的 `https_artifact` 已经能下载并校验，
但它需要调用方**告诉它**一个 digest。真实场景里那个 digest 来自上游发布的校验和文件——
没有这一层，"verify"就退化成"把刚下载的东西自己 hash 一遍再和自己比"，等于什么都没验。

### 23.2 交付物

| # | 内容 |
|---|---|
| S23.1 | `policy/sources.json`：可信来源清单（revision、允许的 host、每个能力的发布模式、校验和来源模板） |
| S23.2 | `caps/sources.py`：清单加载期校验、`resolve_source()`、`parse_sha256sums()`、`digest_for()` |
| S23.3 | CLI `source list` / `source resolve <capability> --version <v>` → 可直接喂给 `plan` 的 `source` 文档 |
| S23.4 | `plan --source-json <file>`：从已解析的来源构造**真实 artifact 计划**（接上 §21 runner 与 §20 路由） |
| S23.5 | 测试：清单校验、校验和解析（GNU/BSD、CRLF、注释）、篡改拒收、允许列表拒绝、离线端到端 |
| S23.6 | 文档（§23.6、ADR-0009、`AGENTS.md`） |

### 23.3 需要做出的决策（含理由）

1. **digest 只能来自校验和文件，不能来自清单、更不能来自刚下载的文件本身。**
   把下载物自己 hash 一遍再和自己比，是"验证"最常见的假动作。结构上强制：
   `resolve_source()` 没有校验和来源就直接拒绝，`parse_sha256sums()` 是 expected digest 的**唯一**出处。
2. **来源（provenance）与完整性（integrity）必须分开**（§4.4:368）：`provenance.source_id`/`publisher`
   来自清单，`integrity.artifact_digest` 来自校验和文件。两者由不同机制保证；合并会掩盖
   "来源可信但内容被换"。
3. **host 允许列表是清单的核心，不是装饰。** 清单外的 host 一律 `PROVENANCE_FAILED`——
   否则"来源可验证"就是空话，而 `https_artifact` 只保证"传输是加密的"。
4. **非 https 一律拒**（沿用 §21.3-3）；本地路径只允许在**显式的离线模式**下使用，且仍必须有校验和来源。
5. **v1 不做签名**：`signature` 保持 `null`，并**明确写出"digest 不是签名"**。
   把 digest 说成签名，比不做签名更糟。
6. **校验和文件的获取复用 `https_artifact`**，不新写下载器——一套网络边界，一处审计。

### 23.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**（`source` 已冻结，无需改）。
2. 离线可复现：本地 artifact + 本地 `SHA256SUMS` → `source resolve` → `plan --source-json` →
   `install` → `where` 命中；把 artifact 换掉 → 计划 digest 与实文件不符 → `DIGEST_MISMATCH`。
3. 清单外 host、`http://`、清单外能力、缺校验和来源，四种情况各有明确 reason code。
4. `resolve` 的输出**原样**通过 `common.schema.json#/$defs/source` 校验。

### 23.5 明确不做

不实现签名验证（`ed25519` 校验仍显式未实现）；不做镜像/重试；不做版本发现
（"最新版是多少"由清单模板 + 调用方给的版本号决定，不猜）。

### 23.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **469 项全绿**（新增 `test_l1_sources.py` 25 项 +
Skill 漂移守卫加 1 项）；旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿。
**`schema_count` 未变**——`source` 早已在 `common.schema.json` 里冻结，本轮只是把它填实。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S23.1 | `policy/sources.json`（`src-1`）：4 个允许 host、3 个来源条目（`rust-toolchain` / `build` / `archive`），**不含任何 digest** | ✅ **§72 改为 2 条**：`archive` 被移除，因为它取不到上游校验和文件（实测结论见 §72.2）。本行保留 §23 当时的数字，只加这一句指向改判 |
| S23.2 | `caps/sources.py`：清单加载期校验（未知键/重复/缺校验和来源/未知格式全拒）、host 允许列表、`parse_sha256sums()` + `parse_single_digest()`、`digest_for()`、`resolve_source()` | ✅ |
| S23.3 | `airoot source list` / `airoot source resolve <cap> --version <v> [--offline-checksum <file>] [--source-out <file>]` | ✅ |
| S23.4 | `airoot plan <cap> --source-json <file>`：构造**真实 artifact 计划**，接上 §20 路由与 §21 runner | ✅ |
| S23.5 | 25 项测试：GNU/BSD/CRLF/单摘要四种格式、名称不符**不猜**、允许列表、明文、离线解析、篡改拒收、离线端到端（resolve → plan → install → 实例带 `portable_file`） | ✅ |
| S23.6 | §23.6 + §16.0 + ADR-0009 + `SKILL.md`/`agents` 补 `source` + `AGENTS.md` | ✅ |

**实现里三处必须记录的判断**：

1. **"expected digest 只能来自校验和文件"是结构性的，不是纪律性的。** `parse_sha256sums()`
   是 expected digest 的**唯一**出处；`resolve_source()` 没有校验和来源直接拒；清单里**不允许**写
   digest（有测试断言 `sources.json` 里不出现 `sha256:`）。理由：把下载物自己 hash 一遍再和自己比，
   是"验证"最常见的假动作，靠约定防不住。
2. **名称不匹配是 `NOT_FOUND`，不是模糊匹配。** 校验和文件里没有目标文件名时**绝不去挑最接近的
   一条**（有测试与证据文案）。这是"未知就是未知"在供应链上的同一条原则。
3. **离线模式只是一条捷径，不是一条豁免。** `--offline-checksum` 换个地方取校验和文件，
   但**仍然必须**有校验和来源、仍然要解析、仍然要与 artifact 对照；它同时也让这条链路能在
   不触网的情况下被端到端测试（§23.4-2 的证据就是它跑出来的）。

**诚实边界**：v1 **不做签名校验**（`signature: null`），并且 `source list` 与 Skill 都明确写出
**"摘要不等于签名"**。ADR-0001 的 Rust 工具链获取现在**在机制上通了**（`rust-toolchain` 条目
指向 `static.rust-lang.org` + `rustup-init.exe.sha256`），但**本轮没有真的下载它**——
那需要一个真实的网络动作与一次人工批准，属于下一步。

**仍然延后**：签名/来源证明（TUF/Sigstore 级）、镜像与重试、版本发现（"最新版是多少"）、
以及必须管理员权限的 P2 受保护状态。

---

## 24. 阶段计划：`rebuild`（从权威重建派生状态）

> **本节是实现计划。** 规划 §2.3 把 `rebuild` 列为核心动词，§3.6:217 要求它"给出证据和
> reason code"、发现陌生对象只报 `unmanaged`/`quarantine`，§9.6/§9.7 两次强调
> `doctor`/`discover`/`rebuild` **永不删除**，§9.11:968 要求"保留旧 registry 作为只读证据"。
> 而 `doctor` 的 `remediation` 字段**已经在让用户跑 `rebuild`**——但这条命令不存在。
> 本阶段把它补齐。完成情况回写 §24.6。

### 24.1 为什么这是"缺口"而不是"新功能"

D7（投影漂移）与 D10（审计投影漂移）已经会诊断，`doctor` 的 remediation 写着 `rebuild`，
但 CLI 里没有这个动词。一个会指向不存在命令的诊断，比不诊断更糟：用户照做会失败，
然后开始怀疑整套诊断。**产品自己引用了的动词必须存在。**

### 24.2 交付物

| # | 内容 |
|---|---|
| S24.1 | `caps/rebuild.py`：`rebuild_plan()`（要重建什么、发现了哪些陌生对象）+ `apply_rebuild()` |
| S24.2 | 重建对象**只有派生状态**：`state/registry.json`（D7）与 `logs/audit/events.json`（D10） |
| S24.3 | **旧投影归档为只读证据**（`state/rebuild/<seq>/`），而不是就地丢弃 |
| S24.4 | 陌生对象只报告：store 内无 registry 行的对象 → `orphan`；未匹配白名单的数据根对象 → `unmanaged`；**永不 adopt、永不删除** |
| S24.5 | CLI `airoot rebuild [--plan] [--json]`；幂等 |
| S24.6 | Skill 的 `not_implemented` 清单移除 `rebuild`（漂移守卫会强制这一步）+ 命令地图 |
| S24.7 | 测试（含"权威不可自重建"的拒绝）+ §24.6 + ADR-0010 + `AGENTS.md` |

### 24.3 需要做出的决策（含理由）

1. **重建的边界只到派生状态。** `state/registry.db` 是权威（§9.11），**不能从它的投影反推**。
   有人会希望 `rebuild` 连数据库一起修——那等于让权威从自己的影子重建自己，只会把损坏洗白。
   因此：DB 完整性问题 → `rebuild` 明确拒绝（`REGISTRY_INTEGRITY_FAILED`，remediation 指向
   人工/备份恢复），而不是假装修好了。
2. **归档而非丢弃。** §9.11:968 的"保留旧 registry 作为只读证据"在投影层的等价物是：
   把重建前的投影与审计快照存进 `state/rebuild/<seq>/`。删掉它们等于抹掉"刚才是什么样"。
3. **`rebuild` 不修事务。** 未完成事务属于 `repair`；`rebuild` 只报告它们（`PENDING_TRANSACTION`
   级别的事实写进响应），否则两个动词的职责会重叠，用户不知道该找谁。
4. **裸 `airoot rebuild` 直接执行**（规划 §15.1 的命令签名没有子命令），因为它只重写派生文件、
   幂等、无副作用；`--plan` 提供只读预演。这与 `gc`（会删文件，必须批准）形成对照——
   判据是"会不会改变权威或删东西"，不是"动词听起来凶不凶"。
5. **陌生对象永不接管**（§3.6:217、§9.6:716）：只报告，并且报告里明确写出"要管它需要先冻结
   capability"（§15.4 的增长路径）。

### 24.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**。
2. 故意弄脏投影 → `doctor` 报 `REGISTRY_PROJECTION_STALE`/`AUDIT_PROJECTION_DRIFT` →
   `airoot rebuild` → `doctor` 回到 healthy，且重建前的投影能在归档里找到。
3. `store/` 里放一个无登记对象 → `rebuild` 报告它、**不 adopt、不删除**，文件仍在。
4. 数据库被破坏 → `rebuild` 拒绝并说清"权威不能从自身重建"，**不动任何文件**。
5. 连跑两次 `rebuild` 结果一致（幂等）。

### 24.5 明确不做

`reconcile <manifest>`（规划里它的语义只有一句"先完成 repair/reconcile"，不足以实现——
凭空补一个算法比缺一个命令更糟，继续留在 Skill 的 `not_implemented` 里）；
`rebuild` 不做搜索索引（P3）、不做 GC（§9.6:714 明令禁止）、不迁移数据库。

### 24.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **480 项全绿**（新增 `test_l1_rebuild.py` 11 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；真机验收 PASS 并新增
`rebuild --plan` / `rebuild` 两行证据。**`schema_count` 未变**——重建只动两个派生 JSON 文件。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S24.1 | `caps/rebuild.py`：`rebuild_plan()`（只读）+ `apply_rebuild()` | ✅ |
| S24.2 | 重建范围**只有** `state/registry.json` 与 `logs/audit/events.json` | ✅ |
| S24.3 | 旧投影归档到 `state/rebuild/<seq>/`（每次运行一个快照，不覆盖上一个） | ✅ |
| S24.4 | 陌生对象只报告：orphan（store 内无登记）/ unmanaged / 未完成事务；`adopted=0`、`files_deleted=0` | ✅ |
| S24.5 | `airoot rebuild [--plan] [--json]`；幂等 | ✅ |
| S24.6 | Skill：`not_implemented` 移除 `rebuild`（漂移守卫强制）+ 命令地图 + `agents/airoot.json` 条目 | ✅ |
| S24.7 | 11 项测试 + §24.6 + ADR-0010 + `AGENTS.md` | ✅ |

**实现里三处必须记录的判断**：

1. **"权威不能从自身重建"是本阶段最重要的一条**，并且它是**拒绝**而不是尽力而为：
   数据库不一致时 `rebuild` 直接 `REGISTRY_INTEGRITY_FAILED`，把 remediation 交回
   `doctor`/人工决策。否则 `rebuild` 会变成洗白损坏的工具——把一份读起来很自信的投影
   盖在坏数据上。
2. **`rebuild` 不修事务**，只报告。`repair` 才是那个动词；两个动词职责重叠时，用户不知道该找谁。
3. **归档而不是丢弃**：规划 §9.11:968 的"保留旧 registry 作为只读证据"在投影层的等价物是
   `state/rebuild/<seq>/` 快照。测试断言重建前的字节能在归档里原样找到，且第二次运行
   不会覆盖第一次的快照。

**测试里必须说明的一处取舍**：数据库 FOREIGN KEY 与 partial unique index 会挡住大多数不一致，
所以"损坏时拒绝"这条用**注入**（monkeypatch `integrity_problems`）而不是伪造 SQL 来测——
测的是我们自己的拒绝逻辑，不是 SQLite 的约束。测试里写明了这一点。

**仍然延后**：`reconcile <manifest>`（规划里语义只有一句，不足以实现，继续留在 Skill 的
`not_implemented` 里）；`rebuild` 不碰搜索索引（P3）与 GC（§9.6:714 明令禁止）。

---

## 25. 阶段计划：session 激活的完整语义（§16.4 的欠账）

> **本节是实现计划。** 步骤 9 的 `env activate` 只做到"打印脚本"就收尾了，而规划 §16.4:1783
> 明确要求 `--json` 返回"环境 diff、来源 generation、**激活前快照 ID** 和 **deactivate 信息**"，
> 并要求"激活支持**嵌套栈**和 `deactivate`"、环境失效时返回 **`SESSION_STATE_STALE`**。
> 这些是已交付命令的欠账，不是新功能。完成情况回写 §25.6。

### 25.1 为什么先补它而不是先加新动词

`env activate` **已经在用**，但只满足 §16.4 的第一条（输出脚本）。缺快照就没有 `deactivate`，
缺嵌套栈就没法表达"先后激活两个能力"，缺 `SESSION_STATE_STALE` 就把"环境已经变了"当成成功。
**先让已交付的命令符合它自己的契约**，比再加一个动词更有价值。

### 25.2 交付物

| # | 内容 |
|---|---|
| S25.1 | `caps/session.py`：会话栈（`state/sessions/<session-id>.json`），每帧记录 external_id、解析后的变量新旧值、PATH 条目、激活时的 generation、快照 ID、时间 |
| S25.2 | `env activate <external-id> --session <id>`：压栈；同一能力重复激活 → 幂等提示；`--json` 增加 `diff` / `source_generation` / `snapshot_id` / `deactivate` |
| S25.3 | `env deactivate [--session <id>] [--all] [--shell powershell\|cmd] [--json]`：弹栈（或整栈），输出**恢复脚本**（旧值回填、原本不存在的变量则 unset、PATH 条目移除） |
| S25.4 | 新 reason code `SESSION_STATE_STALE`：root generation 变化 / 记录不可读 / 引用已消失时，激活与反激活都拒绝并说明 |
| S25.5 | 会话文件**不是凭据**、不进 registry、不写 machine/user 环境；`env list` 不受影响 |
| S25.6 | 测试 + 文档（§25.6、ADR-0011、诊断码表、`SKILL.md` / `agents` / `AGENTS.md`） |

### 25.3 需要做出的决策（含理由）

1. **会话 ID 必须显式注入**（`--session <id>`），不自动生成。规划把 `session_id` 的生成算法列入
   "未冻结项"，P1 的既定立场是"只接受注入或显式入参"；自动编一个 ID 会让 `deactivate` 找不到栈。
2. **快照写进 `state/sessions/`，这是对步骤 9 的一次修正。** 当时我写"会话激活什么都不写"，
   但 §16.4:1783 要求的"激活前快照 ID"必然要落盘。落在正确的位置即可：**CLI 根内、按 session 分文件、
   与 registry 无关、不是权限凭据**。同时把这条修正写进文档，而不是悄悄改行为。
3. **反激活以"恢复旧值"为准，不是"删除变量"。** 激活前不存在的变量要 unset；存在过的要回填旧值；
   PATH 只移除**本次会话加入的条目**（复用 §13 的差值思路）。否则 `deactivate` 会变成"删掉用户自己的东西"。
4. **`SESSION_STATE_STALE` 用退出码 2**（降级/漂移），不是 8：会话本身没写错，是世界变了
   （generation 变了、引用没了）。这与 `CURRENT_PROCESS_ENV_OLD` 是同一类"事实变了"。
5. **幂等**：`activate` 同一 external_id 再次调用不压第二帧；`deactivate` 在空栈上是成功空操作。
6. **不碰 registry**：会话状态只影响"这个 shell 该有什么环境"，不改 declared 状态、不 bump generation。

### 25.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**（会话文件没有对外 Schema，
   `env activate --json` 的文档本来就无 Schema——它是 CLI 响应，不是协议文档）。
2. 嵌套：激活 A 再激活 B → `deactivate` 先撤 B（A 仍在）；`--all` 清空。
3. 恢复正确：激活前 `JAVA_HOME` 有旧值 → 反激活后回到旧值；激活前不存在 → 反激活后被移除。
4. 陈旧：激活后让 generation 变化 → `deactivate` 报 `SESSION_STATE_STALE`（退出码 2）并给出原因。
5. 反激活**不写** machine/user 环境（宿主 PATH 守卫继续通过）。

### 25.5 明确不做

不实现 shell hook 自动加载（属于消费方）；不做跨进程会话发现（需要 session_id 生成算法）；
`env activate` 仍**不**持久化——持久化只有 `env persist` 一条路（§13）。

### 25.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **495 项全绿**（新增 `test_l1_session.py` 15 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；golden 已重生成
（`reason_code_table` 增加 `SESSION_STATE_STALE`）。新增 reason code 一个。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S25.1 | `caps/session.py`：会话栈（`state/sessions/<id>.json`），每帧记录 external_id、变量**新旧值**、PATH 条目与旧值、激活时 generation、快照 ID、时间 | ✅ |
| S25.2 | `env activate <external-id> --session <id>`：压栈；`--json` 增加 `diff` / `source_generation` / `snapshot_id` / `stack_depth` / `deactivate`（§16.4:1783 的四项全部到位） | ✅ |
| S25.3 | `env deactivate [--session <id>] [--all] [--shell] [--json]`：弹栈并输出**恢复脚本** | ✅ |
| S25.4 | `SESSION_STATE_STALE`（退出码 **2**）：generation 变化 / 栈文件不可读 / 版本不符时拒绝 | ✅ |
| S25.5 | 会话文件不是凭据、不进 registry、不写 machine/user 环境（有测试断言 generation 不变、`environment_persist` 为空） | ✅ |
| S25.6 | 测试 + 本节 + ADR-0011 + 诊断码表 + `SKILL.md` / `agents` / `AGENTS.md` | ✅ |

**顺带补上的欠账**：`unadopt`（规划 §15.1 的原名，草案 §8 承诺"保留兼容"）现在是 `forget` 的
兼容别名——它是**同一实现**，不复制第二份逻辑。

**实现里三处必须记录的判断**：

1. **这是对步骤 9 的一次修正，写下来而不是悄悄改。** 步骤 9 我写的是"会话激活什么都不写"，
   但 §16.4:1783 要求的"激活前快照 ID"必然要落盘。落点选在 CLI 根内的
   `state/sessions/<session-id>.json`：**不是权限凭据、不进 registry、不 bump generation**。
   测试断言激活+反激活全程 `registry.generation` 不变、`environment_persist` 为空。
2. **反激活是"恢复"而不是"删除"**：原先存在的变量回填旧值，原先不存在的移除，PATH 只卸下本次
   加入的条目。写成"删除变量"会把用户自己的 `JAVA_HOME` 一起删掉——这类 bug 只在用户真有过
   旧值的时候才暴露。
3. **会话 ID 必须注入**：`session_id` 的生成算法是规划里的未冻结项，P1 的立场是"只接受注入或显式
   入参"。自动编一个 ID 会让 `deactivate` 找不到自己的栈；而用户的 `--session` 值会被清洗成安全
   文件名（有测试）。

**漂移守卫升级（本阶段真正的副产品）**：原来的守卫只到**动词**粒度，于是 `tool retire` 存在就会
让 `tool pin` 看着"已文档化"。现在比较的是**命令路径**（`tool retire` vs `tool pin`），并且
`not_implemented` 也按路径列出（`tool list`/`path verify`/`root adopt` …）。升级过程中暴露了
一个提取器的真 bug：否定前瞻 `(?!\s*[-:=])` 会把 `airoot root status --json` 里的 `status`
也排除掉，于是"root"被当成未知动词——**守卫自己误报，比不报更糟**，已修正。

**未实现的命令路径（已按路径如实登记）**：`search`、`reconcile`、`tool list|status|verify|pin`、
`path verify|backup|restore`、`root adopt|relocate`。下一阶段继续补其中的只读部分。

---

## 26. 阶段计划：补齐冻结 CLI 的只读部分（`tool list/status/verify`、`path verify`）

> **本节是实现计划。** 规划 §15.1 列出的命令里还有 11 条没有实现（§25.6 已按**命令路径**清单
> 登记）。其中四条语义清楚、**只读、不需要管理员权限**，本阶段补齐：
> `tool list`、`tool status`、`tool verify`（§15.4:1601-1603）与 `path verify`（§15.1:1554）。
> 完成情况回写 §26.6。

### 26.1 为什么先做这四条

- `tool list/status/verify` 是**已有能力（owned 域）的观察面**：`doctor` 能整体诊断，却没有
  "只看这一个 instance"的命令；§15.4:1602-1603 明确它们的边界（只读、不切换 active、
  不自动 adopt/修复）。
- `path verify` 检查的是一条**被冻结成硬规则的不变量**（机器 PATH 只允许一个 AIROOT 条目
  `cli\exposure\bin`，版本目录绝不直接进 PATH）——这条规则至今**没有任何代码在检查**。
  一个只写在文档里的不变量，等于没有。

### 26.2 交付物

| # | 内容 |
|---|---|
| S26.1 | `caps/toolstate.py`：`list_tools()`（definition/instance/版本/架构/lifecycle/health/active binding/source） |
| S26.2 | `tool_status(instance_id)`：只读取并验证 manifest/payload/binding/exposure，**不切换 active** |
| S26.3 | `tool_verify(instance_id)`：digest/manifest/entrypoint 校验，**不自动 adopt、不修复** |
| S26.4 | `caps/pathexposure.py` + `path verify`：检查 PATH 上的 AIROOT 条目（重复、指向 store/版本目录、缺 launcher 目录） |
| S26.5 | CLI 四个子命令 + 测试 + 文档（§26.6、ADR-0012、`SKILL.md` / `agents` / `AGENTS.md`） |

### 26.3 需要做出的决策（含理由）

1. **只读就是只读**：四条命令都不写 registry、不 bump generation、不建/删文件，也不写 PATH
   （写 PATH 属 P2）。测试断言 generation 不变。
2. **`tool verify` 用整树 digest 而不是重跑安装**：与 `doctor --verify` 同一判据
   （`instance.artifact_digest` = `store/<id>` 的树摘要）。`probe` 只做**入口点存在性**，
   绝不执行任何 payload（§9.9 证据阶梯）。
3. **`path verify` 只用一个 reason code（`PATH_EXPOSURE_VIOLATION`，退出码 2）**，细节放在
   `findings[].severity`。理由：这是**环境偏离契约**的诊断（drift），不是 AIROOT 自身损坏；
   严重度已由 `severity` 表达，再用一堆码编码严重度只会让映射表变糊。
4. **launcher 目录不存在是 `info` 而不是问题**：P1 不写 launcher（`exposure\bin` 属 P2），
   如实报告"尚未实现"比报成缺失更诚实。
5. **`tool status` 对 retired/unbound 的 instance 是成功**：退役是合法状态，不是错误；
   只有 payload 缺失、binding 指向不存在的 instance 才算问题。

### 26.4 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**。
2. `tool list` 在装了实例后列出 capability/版本/架构/lifecycle/health/active binding/source。
3. `tool verify` 在 payload 被改后报 `MANIFEST_DIGEST_MISMATCH`（退出码 3），且**不修复**。
4. `path verify` 对四种 PATH 形态分别给出正确结论（干净 / 唯一合法条目 / 重复 / 版本目录）。
5. 四条命令均不改变 generation、不写任何文件。

### 26.5 明确不做

`tool pin`（需要五层模型里尚未落地的 `desired` 层）；`path backup/restore`（写入 PATH → P2）；
`root adopt/relocate`（需要完整 copy/verify/switch 规则）；`reconcile`（语义不足）；`search`（P3）。

### 26.6 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **511 项全绿**（新增 `test_l1_toolstate.py` 16 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；golden 已重生成
（码表加 `PATH_EXPOSURE_VIOLATION`）。新增 reason code 一个。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S26.1 | `tool list [--capability <id>]`：capability/版本/架构/lifecycle/health/active binding/source | ✅ |
| S26.2 | `tool status <id>`：payload 存在性、入口点齐全性、binding 与 lifecycle 一致性（**不切换 active**） | ✅ |
| S26.3 | `tool verify <id>`：`store/<id>` 整树 digest + 入口点存在性（**不 adopt、不修复**，有测试断言篡改后 payload 原样保留） | ✅ |
| S26.4 | `path verify`：重复条目（warning）、`store/` 版本目录上 PATH（error）、非授权 AIROOT 目录（warning）、launcher 目录缺失（info） | ✅ |
| S26.5 | 四条命令均为只读：测试断言 `generation` 不变；`path verify` 的文档里带 `path_written: false` | ✅ |

**实现里两处必须记录的判断**：

1. **`path verify` 只用一个 reason code**（`PATH_EXPOSURE_VIOLATION`，退出码 2），严重度放在
   `findings[].severity` 里。这是**环境偏离契约**的诊断（drift），不是 AIROOT 自身损坏；
   再用一串码去编码严重度只会让映射表变糊。
2. **launcher 目录缺失是 `info`**：P1 不写 launcher（`exposure\bin` 属 P2）。把它报成缺失等于
   把"尚未实现"伪装成"出问题了"。

**写测试时立刻抓到的一个真缺陷**：我加了新码却**忘了在 `exits.py` 注册**，于是
`exit_code_for("PATH_EXPOSURE_VIOLATION")` 直接抛 `INVALID_INPUT`——`AirootError` 的构造器对
未注册码是 fail-fast 的。这正是"码表是权威映射"这条规则的用处：漏注册会在第一次使用时炸，
而不是悄悄退化成别的退出码。

**仍然未实现的命令路径**：`search`（P3）、`reconcile`（语义不足）、`tool pin`（需要 `desired` 层）、
`path backup/restore`（写 PATH → P2）、`root adopt/relocate`（需要 copy/verify/switch 规则）。

---

## 27. 阶段计划：`desired` 层与 `tool pin`

> **本节与实现同轮写就，但设计判断在动手前就已固定**（记在这里，以免看起来像事后补的说明）：
> 五层事实模型 `desired`/`declared`/`physical`/`effective`/`historical` 里，**只有 `desired`
> 一层完全没有代码**。规划 §17 给了它的形状，§15.4:1604 给了 `tool pin` 的边界
> （"修改 desired/selection policy，生成新的 generation plan，**不能直接改 launcher**"）。

### 27.1 交付物

| # | 内容 |
|---|---|
| S27.1 | `caps/desired.py`：`state/desired.json`（§17 的形状）、`load/save/pin/clear` |
| S27.2 | `evaluate(registry, manifest)`：desired vs **declared**（active binding 的版本是否满足约束） |
| S27.3 | `tool pin <capability> --version <约束> [--scope] [--clear] [--offline-checksum]` |
| S27.4 | 测试 + 文档（§27.5、ADR-0013、`SKILL.md` / `agents` / `AGENTS.md`） |

### 27.2 需要做出的决策（含理由）

1. **desired 是输入，不是权威。** 它写在 `state/desired.json`，**绝不写 registry、不改 binding、
   不 bump generation**（有测试断言 pin 前后 generation 与 instances/bindings 均不变）。
   五层模型里 desired 与 declared 必须分开，否则"我想要 X"会变成"X 已存在"。
2. **`pin` 表达意图并给出计划，不执行计划。** §15.4:1604 说它"生成新的 generation plan"且
   "不能直接改 launcher"。因此：记录 desired → 若 declared 不满足则构造 plan → **应用仍需正常批准**；
   响应里带 `active_binding_changed: false`。
3. **没有可信来源时如实说"造不出计划"**，而不是伪造 locator：`sync[].plan_blocked_by` 带 reason code
   （`java` 这类没有来源的能力就是这种情况）。§23 已把 provenance 的来源钉死在清单上。
4. **版本约束与 `where --version` 同一套语法**（`>=3.11,<3.13`），并且在**写入前**校验：
   解析不了的 pin 会在更糟的时刻失败；两套语义不同的约束才是陷阱。
5. **manifest 不得携带脚本**：§17 明列 `curl | sh`、`pip install …`、任意 PowerShell；
   加载期按名字拒绝，而不是"看到就当数据"。

### 27.3 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿，`schema_count` 仍为 **19**（desired 是输入文件，
   不是对外协议文档，没有 Schema）。
2. pin 已装能力且满足 → `in_sync: true`、无计划；不满足 → `in_sync: false` + 计划（或如实说造不出）。
3. pin 前后 registry 的 generation / instances / bindings 完全不变。
4. 含 shell 片段或错误 `schema_version` 的 manifest 被拒绝。

### 27.4 明确不做

不做 manifest 的自动应用（`apply` 属既有 `plan`/`approve`/`install` 路径）；
不做 `policies.auto_approve` 的求值（自动批准属 P2 的批准通道，`policy_only` 下没有它）；
本轮不把 desired 接进 `doctor`（下一步）。

### 27.5 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **529 项全绿**（新增 `test_l1_desired.py` 18 项）；
旧切片 `validate-schemas`（`schema_count: 19`）与 `test` 全绿；**没有新增 reason code**
（复用 `NOT_FOUND`/`INVALID_INPUT`），golden 无变化；真机验收保持 PASS。

**实现里三处必须记录的判断**：

1. **造计划失败时把原因放在 `sync[].plan_blocked_by`**，而不是抛错或伪造 locator。
   `tool pin java --version 25.0.2.0` 的期望是"记下这个愿望"，不是"AIROOT 现在去装 java"——
   没有可信来源就如实说。这一条同时保护了 §23 的"来源必须来自清单"。
2. **`--clear` 与 `--version` 是互斥的两条路**：都不给就报 `INVALID_INPUT` 并说清要哪一个。
   一个"什么都不做但成功"的 pin 会让 desired 与事实悄悄分叉。
3. **约束在写入前校验**（只做解析验证）。测试断言约束非法时**文件根本没被创建**——
   半写入的 manifest 比没有 manifest 更危险。

**下一步的自然延伸**：把 desired vs declared 的分叉接进 `doctor`（用现有诊断码），
并在 `tool list` 里显示 `desired_version` / `in_sync`。

---

## 28. 阶段计划：把 desired 与 declared 的分叉接进诊断

> **本节是实现计划。** §27 让 desired 层存在了，但除了 `tool pin` 自己的响应，**没有任何诊断
> 会告诉你"你的意图没被满足"**。一层状态如果只在写入它的那条命令里可见，它就没有真正进入模型。
> 完成情况回写 §28.5。

### 28.1 交付物

| # | 内容 |
|---|---|
| S28.1 | 新诊断码 `DESIRED_NOT_SATISFIED`（退出码 2），登记进 `INVARIANTS` 的 **D5**（binding 完整性） |
| S28.2 | `doctor` 新增 `_check_desired`：逐条 desired 能力比较 declared；manifest 不可读也报同一条码 |
| S28.3 | `tool list` 每条 instance 带 `desired_version` / `in_sync`；顶层带 `desired` 摘要 |
| S28.4 | 测试 + 文档（§28.5、ADR-0014、诊断码表、`SKILL.md`、`AGENTS.md`） |

### 28.2 需要做出的决策（含理由）

1. **新码而不是复用 `DRIFT_DETECTED`**：后者在本项目里指"**观测**与声明不一致"，这里是
   "**意图**与声明不一致"——混淆会让用户不知道该改文件还是该修环境。
2. **归到 D5**：D1–D10 是冻结的十个不变量（有测试断言恰好十个），新码必须挂进其中之一。
   "某个能力的 active binding 不满足意图"本质是 binding 选择层面的问题，属 D5。
3. **manifest 不可读时也报同一条码**（severity warning，不是 error）：无法判断意图是否满足，
   与"确实没被满足"同属不确定；**但绝不让 `doctor` 崩掉**——诊断工具对坏输入要给结论，不是异常。
4. **没有 manifest 就一条都不报**：未 pin 的机器上 `doctor` 的输出必须与以前逐字节一致
   （golden 与既有测试都钉在这上面）。

### 28.3 验收标准

1. `pytest cli/tests` 全绿；旧切片全绿；`schema_count` 仍为 **19**；golden 重生成（新增一个码）。
2. 未 pin 时 `doctor` 的 `codes` 与以前完全相同。
3. pin 一个未满足的版本 → `doctor` 报 `DESIRED_NOT_SATISFIED`，`status=degraded`（退出码 2）。
4. pin 一个已满足的版本 → 不报。
5. manifest 坏掉 → 报同一条码（warning），`doctor` 不抛异常。

### 28.4 明确不做

不自动修补分叉（`pin` 已负责给出计划，应用仍需批准）；不做 `policies.auto_approve` 的求值。

### 28.5 完成情况（回写）

**已完成并验证**：`pytest cli/tests` **535 项全绿**（新增 6 项诊断测试）；旧切片
`validate-schemas`（`schema_count: 19`）与 `test` 全绿；golden 重生成（新增
`DESIRED_NOT_SATISFIED`）。新增 reason code 一个，登记进 `INVARIANTS["D5"]`。

| 子阶段 | 交付物 | 状态 |
|---|---|---|
| S28.1 | `DESIRED_NOT_SATISFIED`（退出码 2）→ `exits.py` + `INVARIANTS["D5"]` + 诊断码表 | ✅ |
| S28.2 | `doctor._check_desired`：逐条比较；manifest 不可读也报同一条码；**未 pin 时一条都不报** | ✅ |
| S28.3 | `tool list` 每条 instance 带 `desired_version`/`in_sync`，顶层带 `desired` 与 `out_of_sync` | ✅ |
| S28.4 | 6 项测试 + §28.5 + ADR-0014 + 诊断码表 + `AGENTS.md` | ✅ |

**实现里两处必须记录的判断**：

1. **未 pin 时 `in_sync` 是 `None` 而不是 `False`**。`None` = "没有意图可比"，
   `False` = "有意图且未满足"。把两者都压成 `False` 会让"这台机器什么都没声明"看起来像
   "有东西坏了"——这正是本阶段要防的那类误读。
2. **`doctor` 对坏 manifest 给结论而不是抛异常**（有测试）。诊断工具在输入损坏时的正确行为是
   "无法判断"这一条诊断，而不是让整个 `doctor` 失败——否则用户为了看别的诊断还得先修 manifest。

**顺带确认的既有约束**：`test_doctor_is_silent_when_nothing_is_pinned` 断言未 pin 的 root
诊断输出与以前完全一致——golden 语料与既有 D1–D10 测试都钉在这上面，所以这一条必须是测试
而不是注释。

---

## 29. 与"开除管家"的最终关系

删掉 `D:\AIRoot` 之后你会失去的、和不会失去的：

| 会失去 | 不会失去 |
|---|---|
| active 选择（哪个 python 是默认） | `D:\env`、`D:\tools`、`D:\env_apps` 的**全部文件** |
| `exposure\bin` 的 shim 与 PATH 上那一条 | 各 runtime 自己的版本号与可用性 |
| 观测历史、漂移记录、审计事件 | 项目的 `.venv` / `node_modules`（本来就 `project_owned`） |
| owned 域（若你曾让它装过东西） | 会话里已经存在的环境变量（进程内的，不受影响） |

这正是管家模型要保证的语义：**AIROOT 是可选的基础设施，不是环境的前提**。

---

## 30. 阶段记录：跨工件一致性审计（L0 常驻守卫）

> **本节是阶段记录，不是新契约。** 它记录一次**审计**：把"契约文字 / 代码 / 策略文件 / 散文"
> 四者互相核对一遍，并把核对本身变成随 `pytest` 常驻的检查。决策见 ADR-0015。

### 30.1 交付物

| # | 内容 |
|---|---|
| S30.1 | 新 `cli/tests/test_l0_consistency.py`：**24 项**跨工件一致性检查，属 L0（不做行为检查） |
| S30.2 | `bootstrap` 补进 `agents/airoot.json` 的 `not_implemented`，并在 `SKILL.md` 未实现清单里写明它属 P2 |
| S30.3 | `AGENTS.md` 仓库地图新增 `caps/` 模块总览一行（此前 11 个模块没有条目） |
| S30.4 | 测试计数口径：当前态必须一致，历史记录不得超过当前（ADR-0015 第 4 条） |
| S30.5 | `exec` 的实参形状与冻结 §15.1 对齐：`--env <id>` 与位置参数同义，`--` 之前属于 AIROOT（ADR-0016） |

### 30.2 审计发现的真实漂移

| 发现 | 处置 |
|---|---|
| `bootstrap` 在冻结的规划 §15.1 命令表里，却既没有实现、也没有声明未实现 | 声明进 `not_implemented`；`SKILL.md` 同处写明 `policy_only` 不是 ACL |
| **§15.1 / §16.4 的 `exec --env <name> -- <cmd>` 根本不能跑**（`unrecognized arguments: --env`） | 两种写法归一（ADR-0016）；`<name>` 明确为 external reference id |
| **写在 id 之后的 `--json` 被子进程参数表吞掉**，AIROOT 静默退回人类可读模式 | `--` 之前的一切归 AIROOT；5 项测试含"两种写法产出逐字节相同文档" |
| **缺子进程命令会抛 `IndexError` 回溯**（`subprocess.run([])`） | 改为 `INVALID_INPUT`（退出码 8） |
| 11 个 `caps/*.py`（`where`/`discovery`/`probe_pe`/`version`/`selection`/`doctor`/`inventory`/`effective`/`environment`/`planner`/`lifecycle`）在仓库地图里没有条目 | 新增 `caps/` 模块总览一行 |
| §16.0 进度表里有一行被字面 `\n` 粘在前一行尾部（早前批量替换留下的） | 修回真实换行 |
| `SKILL.md` 命令地图里有同样一处 `\|\|` 粘连（两行合成一行） | 拆回两行 |
| `policy/selection-policy.json` 的 revision `sp-1` **在任何文档里都没出现**——它是唯一改变 `where` 行为的策略 | 补进仓库地图；新增断言"每个策略 revision 必须被写下来" |
| §7.2 / §10.4 的 `schema_count` 历史行（18 → 19）会被"计数断言"误判 | 断言改为识别"已被取代 / 原提案"标注与"从 18 变成 19"这类转折写法 |

### 30.3 两处审计自身的缺陷（也记下来）

审计测试自己先是错的：正则把 `§17.6` 的 `17` 当成 schema 计数；`nested[0]` 在没有子命令的
parser 上抛 `IndexError`。**检查器出错会伪装成被检查对象出错**——两处都先修检查器，再下结论。

### 30.4 验收

1. `pytest cli/tests` **564 项全绿**（新增 24 项一致性检查 + 5 项 `exec` 实参测试）；旧切片
   `validate-schemas`（`schema_count: 19`）与 `test` 全绿；**golden 语料未变**（对外 JSON 的输出
   形状没有改动：`exec` 的文档字段与既有测试逐字节一致）。
2. `schema_count` 仍为 **19**；退出码 0–9 映射、事务状态机、`policy_only` 语义**均未改**。
3. 真机验收 `cli/tests/real_machine_acceptance.py` 全项 **PASS**（对真实 `D:\env` 只读）。

### 30.5 明确不做

不做运行时行为检查（L1 已覆盖）；不检查散文措辞；**不把历史数字改写成当前值**——
历史记录写的是当时的事实。

---

## 31. 阶段计划：`search` 的协议面（规划 §19 的 P3 第一步，不需要管理员权限）

> **本节是实现计划。** P2 的受保护状态（ACL / broker / machine PATH / machine 级环境变量）需要
> 管理员权限，暂时无法推进；而 `search` 的**协议面**不需要任何特权：它的请求与响应
> **早已是第 1 层冻结契约**（`cli/schema/search-request.schema.json`、
> `cli/schema/search-response.schema.json`），语义由《搜索能力与工具集成协议方案》§4–§8 规定。
> 因此本阶段把 `search` 从"文档里有、命令报不存在"变成"命令存在、语义诚实"。
> 完成情况回写 §31.5。

**本阶段不做高速路径。** Native Index（NTFS 元数据 + USN journal + 常驻索引器）需要后台进程、
`cache\search` 事务与 broker，属 P3 本体，明确留给后续。本阶段实现的是**受控 crawl fallback**：
它必须**如实标注自己是 fallback**，绝不把慢路径伪装成索引（协议 §5.3/§7 的原话）。

### 31.1 交付物

| # | 内容 |
|---|---|
| S31.1 | 决策记录：`file_search` **不进** `policy/capabilities.json`（理由见 §31.2-1） |
| S31.2 | 新 `cli/extensions/airoot-native-search-extension.json`（`capability_types: ["file_search"]`，走既有 manifest 校验） |
| S31.3 | 协议 §6.6 的 `SEARCH_*` 与通用 `EXTENSION_*` reason code 按 §6.6 表登记进 `exits.py` 与 `诊断码与ReasonCode表` |
| S31.4 | 新 `caps/search.py`：请求构造与上限、root 规则、有界 crawl、`physical_verify`、cursor 绑定、`freshness`、结果打标 |
| S31.5 | 新 `policy/search-policy.json`（`srch-1`）：实现上限、默认 root 来源、crawl 边界 |
| S31.6 | CLI：`search <query> …` 与保留子命令 `search status\|implementations\|explain`、`search --query <文本>` |
| S31.7 | Skill 面：`agents/airoot.json` 增 `search` 调用元数据；`SKILL.md` 把 `search` 移出未实现清单并写明"先 `search` 定位、再 `where` 解析" |
| S31.8 | 测试（L1 + CLI）+ golden 语料（search 响应）+ §31.5 回写 + ADR-0017 |

### 31.2 需要做出的决策（含理由）

1. **`file_search` 不写进 `policy/capabilities.json`**。那份清单是**能力边界**（哪些*对象*可以被
   discover/adopt）用的，条目都带 `entry`（可执行文件）与 `kind: tool|runtime`，并直接决定
   `scope decide` 的路由；`file_search` 不是可 adopt 的 payload，而是 extension 提供的能力。
   把两者混在一起会让 `scope decide file_search` 得出一个毫无意义的路由结论。它的"冻结"位置
   就是两个 published schema（第 1 层）。
2. **没有索引时唯一诚实的 `freshness` 是 `state=unknown` + `coverage=none`**。`stale` 表示
   "有索引但落后"，`degraded` 表示"索引存在但已不可信"——两者都隐含**存在一个索引**。
   在没有索引的机器上报 `stale` 是伪造事实。
3. **crawl fallback 的结果必须是 `status=degraded` + `reason_code=SEARCH_FALLBACK_USED`(2)**，
   `data.fallback = {"kind": "crawl", "reason": …}`。原因：调用方拿到的结果集是"遍历到的"而不是
   "索引里的"，新鲜度语义不同；不标出来就等于把两种确定性混成一个（协议 §6.4 的原话）。
4. **`limit` / `max_duration_ms` / `max_staleness_ms` 有实现上限**，超过上限报
   `EXTENSION_INPUT_INVALID`(8) 而不是静默截断（协议 §6.3）。上限写在 `policy/search-policy.json`
   里而不是代码里，这样它是可审计、可修订的数据。
5. **cursor 绑定 query hash + root 集合 + scope + implementation_id + index generation**，
   任一不同即 `SEARCH_CURSOR_INVALID`(8)，**绝不把旧 cursor 静默当成第一页**（协议 §6.3）。
   本阶段没有索引，因此 index generation 用"无索引"这一常量参与绑定。
6. **`refresh` / `rebuild` 子命令本阶段不实现**，明确报 `EXTENSION_OPERATION_UNKNOWN`(9) 并在
   `SKILL.md` 写清楚：需要后台索引器，属 P3 本体。不发明一个假的 refresh。
7. **`include_directories` 默认 false、`include_hidden` 默认 false、`allow_reparse_points` 默认 false**，
   crawl **不跟随** reparse point（避免链接环），且越界 root 直接报 `SEARCH_ROOT_UNAVAILABLE`(2)；
   `roots` 为空时使用 policy 声明的 roots，**绝不默认扫全卷**。

### 31.3 本阶段明确不做

- Native Index（`cache\search\index.db` / USN journal / 后台索引器 / `tx\search`）——P3 本体；
- Everything adapter（探测本机 Everything、走它的公开接口）——可选实现适配，属 P3 本体；
- 跨用户/跨权限元数据读取与 ACL 过滤（本阶段只有"当前进程能不能访问"这一条，且如实标 `accessible`）；
- `search refresh|rebuild`；
- 内容搜索（`content search`）——协议 §4 明确 v1 不承诺。

### 31.4 验收标准

1. `search <query> --json` 的响应**逐字段通过 `search-response.schema.json` 自校验**（含
   `freshness`、`stats`、`fallback`）。
2. 无索引的机器上：`status=degraded`、`reason_code=SEARCH_FALLBACK_USED`、退出码 2、
   `freshness.state=unknown`、`coverage=none`、`fallback.kind=crawl`——**四者同时成立**。
3. `--limit`/`--max-duration-ms` 超上限 → `EXTENSION_INPUT_INVALID`(8)，且**不返回结果**。
4. 越界 root / 不存在 root / UNC → `SEARCH_ROOT_UNAVAILABLE`(2)，不泄露被过滤路径。
5. 旧 cursor（换了 query 或 root set）→ `SEARCH_CURSOR_INVALID`(8)。
6. `--consistency physical_verify` 时逐项复核；查询期间消失的对象标 `verification=changed`，
   仍然返回该条而不是假装它还在（协议 §6.3）。
7. `search status|explain|implementations` 可用且**不执行遍历**；`search --query status` 能搜
   名为 status 的文件（解析器不得把查询当子命令）。
8. 全部测试与旧切片绿灯；真机验收新增 `search` 行（对真实数据根只读）。

### 31.5 完成情况（回写）

**本阶段已完成并验证。** 交付物与证据：

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S31.1 `file_search` 不进能力清单 | ✅ | ADR-0017 决策 1 |
| S31.2 search extension manifest | ✅ | `cli/extensions/airoot-native-search-extension.json` 过 `extension-manifest` 校验；只声明 `search`/`status`/`explain` |
| S31.3 reason code | ✅ | 11 个 `SEARCH_*` + 9 个新 `EXTENSION_*` 登记进 `exits.py` 与诊断码表；golden `reason_code_table` 已重生成 |
| S31.4 `caps/search.py` | ✅ | 请求构造与上限、root 规则、cursor 绑定、有界 crawl、`physical_verify`、结果打标、profile 信封；`cli/tests/test_l1_search.py` **35 项** |
| S31.5 `policy/search-policy.json` | ✅ | `srch-1`：上限是数据不是代码（有测试证明改策略即改阈值） |
| S31.6 CLI 接线 | ✅ | `search <query>`、`--search-root`、`--managed-only`、`--cursor`、`--query`；保留子命令 `status`/`explain`/`implementations`；`refresh`/`rebuild` 明确报 `EXTENSION_OPERATION_UNKNOWN`(9)；形状决策见 ADR-0018；`cli/tests/test_cli_search.py` **18 项** |
| S31.7 Skill/agents 更新 | ✅ | `agents/airoot.json` 去掉 `search` 的未实现声明并新增调用元数据；`SKILL.md` 命令地图新增"`search` 定位文件、`where` 解析能力"，未实现清单改成 `search refresh\|rebuild` |
| S31.8 golden + 真机验收 | ✅ | 新 golden `search_response.json`（第 19 个 fixture，两次重生成逐字节一致）；`real_machine_acceptance.py` 新增 `search` 五行证据 |

**验收证据**：`pytest cli/tests` **618 项全绿**；旧切片 `validate-schemas`（`schema_count: 19`）与
`test` 全绿；`schema_count` 未变（`search-*` 早已发布）；真机验收对真实 `D:\env` 只读运行并
新增：`search java --ext .exe` → 退出码 2 / `degraded` / `SEARCH_FALLBACK_USED` / 37 条匹配 /
`freshness=unknown`；`search status` → 退出码 9 / `SEARCH_NOT_READY`；`search --query java --limit 1`
→ 815 条匹配并发出 cursor；**同一个 cursor 换一个查询 → `SEARCH_CURSOR_INVALID`(8)**。

**本阶段刻意留下的缺口**（不是遗漏，是边界，见 §31.3）：Native Index（USN / 常驻索引器）、
Everything adapter、`search refresh|rebuild`、跨用户 ACL 过滤。**因此现在仍然不能说"AIROOT 具备
Everything 级性能"**——`search` 只是一个诚实的受控 crawl，它每次都这么说。

---

### 31.6 实施顺序（每一步结束时仓库都必须是绿的）

1. **S31.3 + S31.1**：先登记 reason code 与决策（`exits.py` + 诊断码表 + ADR-0017）。
   纯增量，不影响任何既有行为。
2. **S31.5 + S31.2**：`policy/search-policy.json` 与 search extension manifest；
   后者过 `extension-manifest` 校验（注意：schema 要 `operations`，文档写的是 `operation_policies`；
   `target_scope` 必须取 `system|machine|session|project` 之一，文档示例里的 `declared_roots` **无效**）。
3. **S31.4a**：`caps/search.py` 的请求构造 + root 规则 + 上限校验（不遍历磁盘，先只做纯函数）。
4. **S31.4b**：有界 crawl 执行器 + 过滤条件 + `limit`/`max_duration_ms` 截止。
5. **S31.4c**：`physical_verify`、cursor 绑定、结果打标（`management`/`capability_id` 取自 registry）。
6. **S31.6a**：CLI 接线 + 保留子命令 + `--query`。
7. **S31.7 + S31.8**：Skill/agents 更新、测试、golden、回写 §31.5——**最后**才把 `search` 从
   `not_implemented` 里拿掉（在那之前它必须仍然声明为未实现，否则 Skill 会说谎）。
## 32. 阶段计划：把索引真正建起来（P3 本体第一步，仍然不需要管理员权限）

> **本节是实现计划。** §31 交付了协议面与受控 crawl，代价是每次回答都在自报 fallback：
> `freshness` 只能是 `unknown`/`none`。这一步把 `AIROOT\cli\cache\search\index.db` 变成真的：
> `search refresh` 建索引，查询优先读索引，于是 `freshness` 第一次有了**真实值**
> （`current`/`stale` + `last_indexed_at` + `lag_ms` + `coverage`），顺利时退出码第一次是 0。
> 完成情况回写 §32.5。

**它仍然不是 USN / NTFS 索引。** 索引由一次**受控 crawl** 建立，所以 `freshness` 的含义是
"上一次遍历是什么时候"，**不是**"journal 游标追到哪里、有没有断档"。文档与回答里都不得把两者混为一谈。

### 32.1 交付物

| # | 内容 |
|---|---|
| S32.1 | 新 `caps/searchindex.py`：SQLite 索引的建/读/查/新鲜度/状态，整文件原子替换 |
| S32.2 | `policy/search-policy.json` → **`srch-2`**：新增 `index` 块（路径、记录上限、最大年龄） |
| S32.3 | search extension manifest 声明 `refresh`（写操作，`side_effects` 增 `derived_cache`） |
| S32.4 | `execute_search` 改为**索引优先**：覆盖不足时如实回落 crawl |
| S32.5 | CLI：`search refresh [--rebuild]` 真建索引；`status`/`explain` 报真实状态 |
| S32.6 | 测试 + golden（命中/过期/未覆盖）+ §32.5 回写 + ADR-0019 |

### 32.2 需要做出的决策（含理由）

1. **索引是"一次 crawl 的结果"**：`implementation_id` 仍是 `airoot-native-search-crawl`。
   协议里 `airoot-native-search-native-index` 这个名字是留给 USN/NTFS 设计的，**不能**拿它给
   一个目录遍历建的索引背书——那正是本项目最反对的"把慢路径伪装成高速索引"。
2. **派生缓存的整文件替换不算"删除用户文件"**（ADR-0019）：`gc` 仍是删除 **owned payload** 的
   唯一路径；而 `cache\search\index.db` 是协议 §5.1 明说"损坏后可以删除并重建"的派生数据。
   允许的只有**整文件原子替换**（`os.replace`），并且**永不触碰任何数据根内的文件**。
   没有这条澄清，"`gc` 是唯一删文件的路径"会与协议自相矛盾。
3. **索引只回答它覆盖的 root 集合**：请求的 roots 不被索引覆盖时，诚实回落为实时 crawl
   （`fallback.kind=crawl`），**不假装覆盖**。
4. **不用 WAL**：WAL 会留下 `-wal`/`-shm` 旁文件，等于又引入两条删除路径。索引用
   `journal_mode=DELETE` + 单事务构建 + 整文件替换，任何时刻要么是旧索引要么是新索引。
5. **`max_staleness_ms` 是判据不是建议**：`bounded_staleness` 下 lag 超过它 →
   `freshness.state=stale` + `SEARCH_RESULT_STALE`(2)，并如实说明"索引旧了：refresh 或放宽"。
6. **`refresh_then_read` 真的先刷新**：按请求的 roots 做一次有界 crawl 建索引再回答——
   这就是"先消费增量再查询"在 crawl 世界里可实现的形式。
7. **`coverage` 的定义**：完整覆盖请求 roots 且构建未被截断 → `complete_for_roots`；
   被截断/超时、或只覆盖其中一部分 → `partial`；没有索引 → `none`。
8. **匹配仍在 Python 侧全表扫描**：索引的价值是"免于重新遍历文件系统"，不是 SQL 查询优化；
   记录数受实现上限约束。这一点要写清，免得读者以为它是倒排索引。
9. **又一处"文档与 Schema 冲突"**：协议 §6.1 的示例 manifest 给 `refresh` 写
   `overwrite_policy: "replace_derived_cache"`，而 published schema 的枚举只有
   `deny | replace_same_instance | explicit_generation`。**照 schema 写 `replace_same_instance`**
   （ADR-0003 的规则），语义由 §32.2-2 的"整文件原子替换"补足。

### 32.3 本阶段明确不做

USN journal 消费、NTFS 元数据直读、后台常驻索引器、Everything adapter、内容搜索、跨用户 ACL 过滤。

### 32.4 验收标准

1. **没有索引时，§31 的四条诚实性不变量与退出码逐字不变**：`status=degraded`、
   `reason_code=SEARCH_FALLBACK_USED`、`data.fallback.kind=crawl`、
   `freshness=unknown/none`、退出码 2。（`evidence` 与 `warnings` 增加了一条
   `search_source`/更精确的措辞——回答必须能说明**有没有**咨询过索引；golden 随之重生成。）
2. `search refresh` 之后：`freshness.state=current`、`last_indexed_at` 非空、`lag_ms` 有值、
   `coverage=complete_for_roots`、`fallback=null`、**退出码 0**。
3. 把 `max_staleness_ms` 设得极小 → `freshness.state=stale` + `SEARCH_RESULT_STALE`(2)。
4. 请求的 root 不在索引覆盖范围内 → 回落 crawl（`fallback.kind=crawl`，退出码 2），且**不**声称覆盖。
5. 索引损坏（写入垃圾字节）→ **不崩**：`SEARCH_INDEX_DEGRADED`(2) + 回落 crawl。
6. `search refresh --rebuild` 之后 `cache\search` 里**没有** `-wal`/`-shm`/临时残留。
7. 构建失败（写临时文件失败）不破坏旧索引：旧索引仍可查询。
8. 全部测试、旧切片、真机验收绿灯；golden 新增"索引命中"与"索引过期"两份响应。

### 32.5 完成情况（回写）

**本阶段已完成并验证。** 交付物与证据：

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S32.1 `caps/searchindex.py` | ✅ | SQLite 索引的建/读/查/新鲜度/状态；整文件原子替换；`journal_mode=DELETE`；`cli/tests/test_l1_searchindex.py` **15 项** |
| S32.2 `srch-2` 策略 | ✅ | 新增 `index` 块（路径、记录上限、深度）；改策略即改行为（有测试） |
| S32.3 manifest 声明 `refresh` | ✅ | 写操作 + `side_effects: ["none","derived_cache"]` + `health_checks` 增 `index_integrity` |
| S32.4 索引优先 | ✅ | `execute_search(index_root=…)`：覆盖→索引；未覆盖/损坏→如实回落 crawl；cursor 绑定**索引 generation** |
| S32.5 CLI | ✅ | `search refresh\|rebuild`、`status`/`explain` 报真实状态；`cli/tests/test_cli_search.py` 增 4 项 |
| S32.6 golden + 真机 + 回写 | ✅ | 新 golden `search_index_response.json`（退出码 0）、`search_stale_index_response.json`（`SEARCH_RESULT_STALE`，退出码 2） |

**验收证据**：`pytest cli/tests` **638 项全绿**；旧切片 `validate-schemas`（`schema_count: 19`）与
`test` 全绿；`schema_count` 未变；真机验收对真实 `D:\env` 只读运行并新增：
`search refresh` → **51 073 条记录**、`coverage=complete_for_roots`、`data_root_files_touched=0`、
`cache\search` 里只有 `index.db`；随后 `search java --ext .exe` → **退出码 0 / `status=ok` /
`reason_code=null` / `freshness=current`（lag 0）/ `fallback=null`**；`search status` → 退出码 0 /
`SUCCESS`。**这是本项目第一次对真实机器给出"健康"的搜索回答**，而它仍然是 crawl 建的索引。

**golden 里没有 `refresh` fixture**：它的 `elapsed_ms` 由单调时钟测量，无法做成确定性语料；
响应的形状由上面两份 fixture 覆盖。

**仍未做的**（§32.3）：USN journal、NTFS 元数据直读、后台常驻索引器、Everything adapter、
内容搜索、跨用户 ACL 过滤。因此 `freshness.current` 的含义仍然只是"这个列表是最近一次遍历建的"。

---

### 32.6 实施顺序（每一步结束时仓库都必须是绿的）

1. **ADR-0019 + §32.2 落纸**（含"派生缓存替换"的边界澄清）；`policy/search-policy.json` → `srch-2`。
2. **manifest 声明 `refresh`**（写操作 + `derived_cache` 副作用），走 `extension-manifest` 校验。
3. **`caps/searchindex.py`**：建/读/查/新鲜度/状态，纯模块 + 测试（先不接 CLI）。
4. **接入 `execute_search`**：索引优先 + 覆盖判据 + 降级码；无索引路径保持逐字节不变。
5. **CLI**：`search refresh [--rebuild]`，`status`/`explain` 报真实状态。
6. **golden + 真机验收 + §32.5 回写**。

## 33. 阶段计划：把搜索索引接进诊断与审计

> **本节是实现计划。** §32 让索引变成了真的，但它现在只在自己的命令里可见（`search status`）。
> "上次遍历是什么时候""索引已经读不出来了"这类事实，用户不会主动去查——`doctor` 才是回答
> "这台机器现在有没有问题"的地方。同时审计要跟上：协议 §6.6 的码表与 §8 的保留子命令，现在
> 应该有常驻守卫。完成情况回写 §33.5。

### 33.1 交付物

| # | 内容 |
|---|---|
| S33.1 | `doctor` 新增 `_check_search_index`：索引**不可读**或**过旧**时报诊断 |
| S33.2 | D7 的含义扩写为"派生状态与权威一致（JSON 投影 + crawl 建的搜索索引）"，同步码表与 `AGENTS.md` |
| S33.3 | `policy/search-policy.json` 增 `index.max_age_ms`（`doctor` 判"过旧"的判据，是数据不是代码） |
| S33.4 | L0 审计新增两项守卫：协议文档列出的每个 reason code 都已注册；协议文档的保留子命令都能跑 |
| S33.5 | 测试 + 一份"索引过期"的 doctor golden 语料 + §33.5 回写 |

### 33.2 需要做出的决策（含理由）

1. **挂在 D7，不新造 D11**：D1–D10 是冻结的十个不变量（有测试断言恰好十个），新码必须归入其中之一。
   D7 现在叫"派生状态与权威一致"，搜索索引正是**又一个**派生状态（可重建、不是真相源）。
2. **索引缺失不报**：没建索引不是缺陷。若因为"没有索引"就报一条，每台从没用过搜索的机器都会多出
   一条噪音——而噪音会让真正严重的诊断一起被忽略（§12.1 的同一条道理）。
3. **过旧的判据是策略里的 `index.max_age_ms`**（默认 24 小时），不是某个请求参数：`doctor` 没有
   调用方上下文，判据必须来自数据。
4. **`doctor` 绝不自己去建索引**：一次全盘遍历是副作用，诊断工具只报告。
   remediation 写 `rebuild`，而"rebuild"对用户意味着 `airoot search refresh`（写进 evidence）。
5. **无索引时诊断输出逐字节不变**：这是回归面（四个既有 doctor golden 语料不许变），也是
   §28/§9h 已经立过的同一条规矩。

### 33.3 本阶段明确不做

不新增不变量；不把索引状态塞进 `registry.json`（它不属于 declared 状态）；不在诊断里做全盘遍历。

### 33.4 验收标准

1. 没有索引的 root：`doctor` 输出与 §32 时**逐字节相同**（golden 不变）。
2. 索引过旧：一条 `SEARCH_RESULT_STALE`（warning，remediation `rebuild`），退出码 2。
3. 索引损坏：一条 `SEARCH_INDEX_DEGRADED`（warning，remediation `rebuild`）；`doctor` 不抛异常。
4. 索引新鲜：不新增任何诊断（`status` 仍 healthy）。
5. 审计的两项新守卫在"有人加了码却没注册/没实现子命令"时会红。
6. 全部测试、旧切片、真机验收绿灯。

### 33.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S33.1 `_check_search_index` | ✅ | 索引不可读 → `SEARCH_INDEX_DEGRADED`；比 `index.max_age_ms` 更旧 → `SEARCH_RESULT_STALE`；两者 remediation 都是 `rebuild`，evidence 里给出 `airoot search refresh`；`test_l1_doctor.py` 新增 **4 项** |
| S33.2 D7 扩写 | ✅ | `INVARIANTS["D7"]` 三个码；诊断码表 D7 行改写（**不新增 D11**：D1–D10 是冻结的十个） |
| S33.3 `index.max_age_ms` | ✅ | `srch-3`，默认 24 小时；`doctor` 没有请求上下文，判据必须来自数据 |
| S33.4 L0 审计两项守卫 | ✅ | 协议 §6.6 的每个码都已注册（20/20）；§8 保留子命令集合与 `SEARCH_RESERVED` **完全一致**（有测试断言相等，不是包含） |
| S33.5 golden + 真机 | ✅ | 新 `doctor_stale_search_index.json`（D7 的 `SEARCH_RESULT_STALE`，退出码 2）；**四份既有 `doctor_*` 语料逐字节未变** |

**验收证据**：`pytest cli/tests` **645 项全绿**；旧切片 `validate-schemas`（`schema_count: 19`）与
`test` 全绿；真机验收新增两行：`doctor` 在**从没建过索引**时对搜索一字不提（`codes=None`），
`search refresh` 建出 51 073 条记录后**新鲜索引同样不新增诊断**。

**这一阶段真正买到的东西**：索引坏掉或过期不再需要用户主动去查 `search status`——`doctor` 会说，
而且只在**确实有索引**时才说。诊断的噪音与漏报是同一枚硬币的两面，所以"没有索引不报"和
"`doctor` 绝不自己建索引"两条都是刻意的。

---

### 33.6 实施顺序

1. `srch-3`（`index.max_age_ms`）+ D7 扩写 + 码表/AGENTS 同步。
2. `_check_search_index` + 测试（四种状态：无索引 / 新鲜 / 过旧 / 损坏）。
3. L0 审计两项新守卫。
4. golden（新增过期语料；既有四个 doctor 语料必须**不变**）+ 真机验收行 + 回写。

## 34. 阶段计划：USN 可用性探测——把"为什么没有快索引"变成可回答的问题

> **本节是实现计划。** §31–§33 之后，`search` 有了协议面、crawl 建的索引与诊断集成。协议里真正
> 快的那个实现（`airoot-native-search-native-index`，靠 NTFS 元数据 + USN journal）一直缺席，
> 而"为什么缺席"目前只写在文档里。这一阶段把它变成**工具能回答的问题**：一个**只读**的
> 能力探测 + `explain`/`status` 里如实的降级理由。完成情况回写 §34.5。

### 34.1 先记录本轮实测到的事实（不是假设）

在本机（`python 3.11.11`，`IsUserAnAdmin()=yes`，`C:` 是 NTFS）用一次性探针实测：

| 调用 | 句柄 | 结果 |
|---|---|---|
| `CreateFileW(\\.\C:, GENERIC_READ)` | — | **成功** |
| `FSCTL_QUERY_USN_JOURNAL` | GENERIC_READ | **成功**（读出 `UsnJournalID` / `FirstUsn` / `NextUsn` / `MaxUsn`） |
| `FSCTL_READ_USN_JOURNAL` | GENERIC_READ | **成功**（返回 65 520 字节记录） |
| `FSCTL_ENUM_USN_DATA` | GENERIC_READ | **成功**（返回 65 496 字节；**这是全量枚举**） |
| `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL` | GENERIC_READ | **失败 `err=87`（ERROR_INVALID_PARAMETER）**，用 `READ_USN_JOURNAL_DATA_V1` 也一样 |
| 以上四个（除打开外） | `FILE_READ_ATTRIBUTES` | 全部 **`err=1`（ERROR_INVALID_FUNCTION）** |

**这些结论的边界必须一起记录**：

1. **本次会话是提权进程**（`IsUserAnAdmin()=yes`），所以"非管理员能不能开卷句柄"这个问题**在这里
   无法验证**——不能把"这里能跑"当成"用户也能跑"，也不能把"文档说要管理员"当成已验证结论。
2. `FSCTL_READ_UNPRIVILEGED_USN_JOURNAL` 被系统**识别**（否则会是 `err=1` 而不是 `87`），但用 V0/V1
   两种输入都报参数错。**这是一个未解观察，不是一个结论**：本阶段只记录它，不据此推断能力。
3. 因此**全量枚举（ENUM）在通用情形下依赖提权**（文档如此，本机无法反证），而
   `airoot-native-search-native-index` 的必要条件正是全量枚举。

这条清单本身就是教训的兑现：本项目曾经把"工具的失败当作数据的事实"，把错误结论写进了契约
（§16.0 的自我纠错）。所以这里先把**能证明的**、**不能证明的**分开写清楚。

### 34.2 交付物

| # | 内容 |
|---|---|
| S34.1 | 新 `caps/usn.py`：**只读**能力探测，失败一律变成数据（绝不抛异常），默认**什么都不做**（惰性） |
| S34.2 | `search status --probe-native-index`：显式请求时才开卷探测；默认路径**零卷 I/O** |
| S34.3 | `search explain` / `implementations` 报出 native 候选与"不可用"的理由（`SEARCH_BACKEND_UNAVAILABLE`(9)） |
| S34.4 | `policy/search-policy.json` → `srch-4`：`implementations.native_candidate`（候选身份 + 前置条件，数据而非代码） |
| S34.5 | 测试（含**注入假探测器**，不碰真实卷）+ ADR-0020 + 回写 |

### 34.3 需要做出的决策（含理由）

1. **不做 USN 索引器**：它的第一步（全量枚举）在通用情形下需要提权，因此它是 P2 相关的；
   现在做等于把一半能力建在"开发机恰好提权"之上。
2. **探测默认惰性、必须显式请求**：任何命令的默认路径都不得开卷句柄——那是副作用，而且
   `doctor`/`search`/测试都不该因为"顺便看看"而触碰卷。
3. **失败是数据**：卷不存在、非 NTFS、句柄打不开、FSCTL 被拒——全部变成结构化字段，绝不抛异常
   （诊断工具对坏输入要给结论）。
4. **不声称未验证的事**：探测报告里必须有 `elevated` 与 `unprivileged_read` 的**原始观察**，
   而不是"支持/不支持"的二元判断；`reason_code` 只用来表达**当前不可用**这个事实。

### 34.4 验收标准

1. `search status`（不带旗标）**不打开任何卷句柄**（有测试用假探测器断言它一次都没被调用）。
2. `search status --probe-native-index` 在真实机器上给出结构化结果；无论成功失败都退出码 0/2 之一，
   **绝不抛异常**。
3. `search explain` 说清"会用什么回答"以及"为什么不是 native 索引"。
4. 非 NTFS / 不存在的卷 → 结构化 `reason`，不崩。
5. 全部测试、旧切片、真机验收绿灯；`search status` 的既有字段不变（只增字段）。

### 34.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S34.1 `caps/usn.py` | ✅ | 只读探测；`query=False` 时**什么都不做**；失败（卷不存在/非 NTFS/打不开/FSCTL 被拒）全部变成字段；`test_l1_usn.py` **8 项**（含注入假探测器，真实卷一次都不碰） |
| S34.2 `--probe-native-index` | ✅ | 只在显式旗标下开卷句柄；有一条测试断言不带旗标时探测器**一次都不会被调用** |
| S34.3 `explain`/`implementations` 报候选 | ✅ | native 候选常驻 `status`/`explain`/`implementations`，带 `SEARCH_BACKEND_UNAVAILABLE`(9) 与"怎么去查"的提示 |
| S34.4 `srch-4` | ✅ | `index.native_candidate`（身份 + 前置条件）是数据；`test_l1_usn.py` 断言它来自策略而不是硬编码 |
| S34.5 测试 + ADR | ✅ | ADR-0020 记录实测事实与"USN 索引器仍属 P2"；`test_l1_usn.py` 里有一条测试钉住四个 `CTL_CODE` 的数值 |

**验收证据**：`pytest cli/tests` **654 项全绿**；旧切片 `validate-schemas`（`schema_count: 19`）与
`test` 全绿；真机验收新增两行**真实读数**：

```text
search implementations   → native 候选在列，reason_code=SEARCH_BACKEND_UNAVAILABLE
search status --probe-native-index →
    filesystem=NTFS  elevated=True
    journal_present=True  enumeration_bytes=65496
    unprivileged_read=rejected (err=87)
    observation: journal id=…  first_usn=…  next_usn=…
    observation: volume enumeration succeeded (this is the privileged step)
```

**这一阶段买到的东西**：`file_search` 不再是"要么慢、要么没有"——`search explain` 现在能说清
"会用 crawl 回答，因为 native 索引在这个 build 里不可用，原因与查证方式如下"。同时把
`FSCTL_READ_UNPRIVILEGED_USN_JOURNAL` 那个 `err=87` 明确记成**未解观察**而不是结论，
以免下一个人把它读成"系统不支持"。

**仍然不做的**（§34.3-1，ADR-0020）：USN 索引器本体——它的第一步（全量枚举）在通用情形下需要
提权，因此与 P2 的 broker 绑定。

---

### 34.6 实施顺序

1. ADR-0020（记录 §34.1 的实测事实与"USN 索引器仍属 P2"的决定）+ `srch-4`。
2. `caps/usn.py` + 注入式探测器 + 单元测试（真实卷一次都不碰）。
3. `--probe-native-index` 接线 + `explain`/`implementations` 报候选与理由。
4. 真机验收行 + 回写 + AGENTS/SKILL 同步。

## 35. 阶段记录：对外承诺的交叉复核（常驻守卫第四组）

> **本节是阶段记录，不是新契约。** §31–§34 把 `search` 从"文档里有"变成"真的能用"，并新增了
> USN 探测。这一节的目的是把**对外承诺**（"你可以跑这个""这条诊断会告诉你""不能说它是什么"）
> 变成和代码一起跑的守卫，而不是继续依赖散文自律。

### 35.1 交付物

| # | 内容 |
|---|---|
| S35.1 | **`AGENTS.md` 的命令守卫**：文档里告诉 agent 去跑的每条 `airoot …` 都必须真的存在（或已声明未实现）。此前只有 `SKILL.md` 有这条守卫，而 `AGENTS.md` 才是 agent 的第一份读物 |
| S35.2 | **诊断码的"可产生性"守卫**：`INVARIANTS` 里的每个码都必须**有办法被产生**，或进**写明了理由的例外清单** |
| S35.3 | **两份"未实现"清单必须相等**：`SKILL.md` 的未实现块 ↔ `agents/airoot.json` 的 `not_implemented` |
| S35.4 | **诚实性禁令仍在**：两份文档都必须以否定语气保留"Everything 级性能"禁令 |

### 35.2 复核发现的真实问题

| 发现 | 处置 |
|---|---|
| **`AGENTS.md` 完全没有命令存在性守卫**——它是 agent 的第一份读物，却可以指向不存在的命令 | 新增守卫（S35.1） |
| 契约草案 §8 的命令表写着 `airoot retire <capability>`，而实现落地在 `airoot tool retire <instance-id>` | **不改提案记录**，在 §8 表格下方加一段"实现落地时的拼写"说明，并指明可执行拼写以 `SKILL.md`/`agents/airoot.json` 为准（两者有守卫） |
| `REGISTRY_MISSING` **不是** doctor.py 里的字面量：`_check_registry` 转发 `error.reason_code` | 守卫从"doctor.py 里有这个字面量"改成"**任何地方能产生这个码**"，否则会误报（同一个陷阱的新形态：把"形状"当成"事实"） |
| `DATA_ROOT_ACL_DRIFT` 自管家草案起就在 `INVARIANTS["D1"]`，但**产生不出来**（需要 P2 的 ACL 基线） | 变成**写明理由的例外清单**，并要求它同时在 `AGENTS.md` 里被登记为待做；P2 落地后这条例外必须删除 |
| 我自己的守卫正则先写错了一次：`path backup\|restore` 被解析成 `path` | 修的是**检查器**（先修检查器再下结论，§30.3 的同一条规矩） |

### 35.3 验收证据

`pytest cli/tests` **658 项全绿**（一致性守卫从 26 项增到 **30 项**）；旧切片、真机验收不受影响。
四条新守卫都对"未来某次诚实性倒退"敏感：删掉禁令句、把码写进目录却不实现、两份清单漂移、
文档指向不存在的命令，任何一种都会当场变红。

---

## 36. 阶段计划：搜索的稳定性——大小写 / Unicode / 长路径 / 保留名

> **本节是实现计划。** 搜索协议 §10 的"正确性"清单里有一条**结果稳定性**要求：
> "大小写、Unicode、长路径和保留名称结果稳定"。`search` 交付时没有针对这四样做任何专门验证。
> 本阶段先用探针把**实际行为**测出来（§36.1），再决定要改什么——而不是先写代码再找理由。

### 36.1 先记录实测到的事实（本机）

| 维度 | 实测 |
|---|---|
| 长路径 | `LongPathsEnabled=1`；构造了 **339 字符**的叶路径，`os.makedirs` 与写入均成功，crawl **完整走到**该叶（`examined=8 matched=1 unreadable_dirs=0`），报告的路径**不带** `\\?\` 前缀 |
| 大小写 | `python` 与 `PYTHON` 查询命中**同一组**文件；匹配是 Unicode 感知的小写折叠 |
| Unicode | `中文python.exe`、含 NBSP 的名字都能被 `contains` 正确命中；`pythön.exe` **不被** `python` 命中（不做变音折叠，符合预期） |
| 保留名 | `CON.exe` / `PRN.exe` / `NUL.exe` / `COM1.exe` 只有通过 `\\?\` 前缀才创建得出来；crawl **不崩**、不误报 `accessible=False`，且按查询正常筛选（`contains "con"` 只回 `CON.exe`） |
| 大小写与文件系统 | **NTFS 大小写不敏感**：先建 `python.exe` 再写 `PYTHON.EXE` **不会**产生第二个条目（目录里仍然只有一个 `python.exe`）。所以"大小写稳定"说的是**匹配**稳定，不是"两个拼写能共存"——写测试时踩到过这一点 |

**边界（必须一起记录）**：本机**开启了**长路径支持，所以"注册表策略关闭时怎么办"在这里**无法验证**。
不能把"这台机器能跑"当成"所有机器都能跑"（§34.1 的同一条规矩）。

### 36.2 交付物

| # | 内容 |
|---|---|
| S36.1 | `caps/search.py`：内部 syscall 一律走 **`\\?\` 扩展长度形式**，**报告出来的路径一律是普通形式**——这样长路径就不依赖机器策略 |
| S36.2 | 同一套前缀逻辑用在 `physical_verify` 的复核与 `_accessible` 上（否则深叶会被误判为不可访问） |
| S36.3 | 新 `cli/tests/test_l1_search_stability.py`：把 §36.1 的四行事实变成常驻测试 |
| S36.4 | 文档与回写：把"能验证的 / 不能验证的"写清楚 |

### 36.3 需要做出的决策（含理由）

1. **不修改机器策略、不声称验证过策略关闭的情形**：改 `LongPathsEnabled` 是机器级改动，测试绝不能碰
   （AGENTS 的"任何测试都不得污染开发机"）。
2. **前缀只用于 syscall，不进入任何对外字段**：`\\?\` 泄漏进 `results[].path` 会让路径不可比、
   也会破坏"路径与用户看到的字符串一致"这条基本预期。
3. **保留名不是错误**：能创建就得能被搜索到；crawl 对它们的行为与普通文件一致，不做特殊处理。
4. **不做变音/宽度折叠**：`pythön` 与 `python` 是不同名字。协议要求"稳定"，不要求"模糊"。

### 36.4 验收标准

1. 深于 260 字符的路径被完整搜索到，且 `results[].path` **不含** `\\?\`。
2. 查询大小写在四个组合下命中同一组结果。
3. Unicode 名字：`contains` / `exact` 的行为与 §36.1 一致且稳定。
4. 保留名可被搜到，且不产生"不可访问"之类的假发现。
5. 索引路径同样适用（索引里存的也是普通路径，查询时仍能访问深叶）。
6. 全部测试、旧切片、真机验收绿灯。

### 36.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S36.1 `\\?\` 只用于 syscall | ✅ | `extended_path`/`native_path`；crawl 的 `scandir`/`stat` 走扩展形式，`results[].path` 一律普通形式；测试断言"报告路径永不带前缀"与"来回转换可逆" |
| S36.2 复核与可访问性同样处理 | ✅ | `physical_verify` 与 `_accessible` 都经扩展形式；两条测试分别覆盖深叶的 `verification="verified"` 与索引查询里的 `accessible=True` |
| S36.3 常驻测试 | ✅ | 新 `cli/tests/test_l1_search_stability.py` **12 项**（长路径 / 前缀可逆 / UNC 形式 / 大小写对称 / NTFS 去重 / Unicode 精确语义 / 保留名 / 索引往返 / 深根 canonicalize） |
| S36.4 文档 | ✅ | 本节 + §36.1 的实测表（含 NTFS 大小写不敏感这条） |

**验收证据**：`pytest cli/tests` **670 项全绿**；旧切片与真机验收不受影响（本阶段不改对外形状）。

**一处仍然存在的同类缺口（如实记录，未修）**：`caps/discovery.py` 与 `caps/probe_pe.py` 的目录遍历
**没有**走扩展形式。它们只用于登记能力对象、深度通常很浅，但一个深于 `MAX_PATH` 的数据根在那里
同样会以"不可读"收场。统一处理应该抽一个共享的遍历原语，而不是把前缀逻辑复制第三遍——留给后续，
届时连同"策略关闭时"的行为一起验证。

> **后续**：这处缺口已由 **§38** 关闭（共享原语 + 守卫第六组）。本段保留当时的记录口径。

**无法在本机验证的**：注册表长路径策略**关闭**时的行为（改它是机器级改动，测试不许碰）。
本阶段能主张的是"syscall 的形式由我们决定"，**不能**主张"在所有机器上都验证过"。

### 36.6 实施顺序

1. §36 + 决策落纸；探针事实转成常驻测试（先让它们红/绿说清楚现状）。
2. `extended_path` / `native_path` + crawl/索引/`physical_verify`/`_accessible` 接入。
3. 回写 §36.5 + AGENTS 计数 + 真机验收不动（这层不改变对外形状）。

---

## 37. 阶段记录：agent 面的 reason code 速查补齐（守卫第五组）

> **本节是阶段记录，不是新契约。** §31–§36 新增了一大批 `SEARCH_*` 与 `EXTENSION_*` 码。
> 权威表（`诊断码与ReasonCode表.md`）有守卫盯着，但 **`references/reason-codes.md` —— agent 在查
> 权威表之前先读的那份速查 —— 没有**。

### 37.1 复核发现

`references/reason-codes.md` **93 个注册码里有 41 个根本没出现**，包括几乎整个 `SEARCH_*` 家族：
`SEARCH_FALLBACK_USED`、`SEARCH_RESULT_STALE`、`SEARCH_INDEX_DEGRADED`、`SEARCH_NOT_READY`、
`SEARCH_CURSOR_INVALID`……也就是说，agent 在 `search` 上最常看到的那些码，**恰恰是它查不到的**。
唯一比"没有速查表"更糟的是"速查表漏掉你会遇到的码"——agent 会自己编一个解释。

### 37.2 交付物

| # | 内容 |
|---|---|
| S37.1 | 速查表补齐到**全部 93 个码**：按退出码分组给出每个码的含义与"你要怎么说"；`EXTENSION_*` 这类家族简写改成**逐条列出** |
| S37.2 | 新增一节 **《搜索的降级阶梯》**：把索引答案 / crawl 答案 / 过期 / 不可读 / native 不可用 / cursor 失效 / root 不可用 / 权限过滤 / journal 断档逐条讲清"该怎么说"，并明确"**不要**说 AIROOT 已具备 Everything 级性能" |
| S37.3 | 守卫第五组：**每个注册的 reason code 都必须在这份速查里被点名** |

### 37.3 验收证据

`pytest cli/tests` **671 项全绿**（一致性守卫从 30 项增到 **31 项**）；旧切片与真机验收不受影响。
守卫的作用是让下一次"新增码却忘了告诉 agent"当场变红——§31–§36 连续五轮都在加码，
这条漂移正是这种情况下最容易发生的。

---

## 38. 阶段计划：把扩展长度路径抽成共享原语（关闭 §36.5 记录的那处缺口）

> **本节是实现计划。** §36 给 `search` 的遍历加了 `\\?\` 扩展长度形式，同时**如实记录**了一处
> 同类缺口：`caps/discovery.py` / `caps/probe_pe.py` 仍在用普通路径，深于 `MAX_PATH` 的**数据根**
> 在那里会以"不可读"收场。当时的结论是"要统一就得抽一个共享原语，而不是把前缀逻辑复制第三遍"。
> 本节就是那件事。完成情况回写 §38.5。

### 38.1 交付物

| # | 内容 |
|---|---|
| S38.1 | `paths.py` 成为**唯一**定义处：`EXTENDED_PREFIX` / `extended_path` / `native_path` 从 `caps/search.py` 迁入（`search` 改为重新导出，既有调用与测试不动） |
| S38.2 | `caps/discovery.py` 的遍历走扩展形式、**返回原生路径**（深度计数仍在同一形式上比较） |
| S38.3 | `caps/probe_pe.py` 的 `stat`/`open` 走扩展形式，`PeMetadata.path` 仍是原生路径 |
| S38.4 | 守卫第六组：**接触用户数据的那几个模块必须使用共享助手**，且不得自己定义前缀常量 |
| S38.5 | 测试 + 回写 |

### 38.2 需要做出的决策（含理由）

1. **原语放在 `paths.py`**：它已经管路径的规范化、包含判定、卷身份；再放一份"路径的另一种写法"
   到 `caps/` 里，下一个写遍历的人就会找不到它。`caps/search.py` 保留同名重新导出，
   这样既有 import 与测试一行都不用改。
2. **只覆盖"用户数据"路径**：`store/`、`state/`、`logs/` 这些 **AIROOT 根内部**的扫描刻意不纳入——
   它们的深度由根自身的路径加几个短片段决定，强行加前缀只会让每个写入方都改一遍，却换不到证据。
   这条边界写进守卫的注释里，免得下一个人以为是漏了。
3. **报告出来的路径永远是原生形式**：前缀是实现细节，不是契约的一部分。
4. **不声称验证过"策略关闭"的情形**（同 §36.5）：本机能验证的是"深路径可用"与"前缀不进对外字段"。

### 38.3 验收标准

1. 深于 260 字符的**数据根**能被 `discover` 扫到，且报告里**不含** `\\?\`。
2. 深于 260 字符的可执行文件能被 `probe_pe` 读到（`size_bytes > 0`，`errors` 里没有 `unreadable`），
   且 `PeMetadata.path` 是原生路径。
3. 前缀常量只有一处定义；接触用户数据的模块都必须引用共享助手。
4. `search` 的既有行为与测试**不受影响**（重新导出即可）。
5. 全部测试、旧切片、真机验收绿灯。

### 38.4 明确不做

不改 AIROOT 根内部的扫描；不为"策略关闭"写只能在本机跑的伪验证；不引入第三个遍历实现。

### 38.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S38.1 唯一定义处 | ✅ | `paths.extended_path`/`native_path`；`caps/search.py` 重新导出，`test_l1_search_stability.py` 的 12 项**一行未改**全绿 |
| S38.2 `discovery` | ✅ | 遍历在扩展形式上下降、返回原生路径；`test_l1_discovery.py` 新增"深于 `MAX_PATH` 的数据根能被扫到且报告不含前缀" |
| S38.3 `probe_pe` | ✅ | `stat`/`open` 走扩展形式；新增测试断言深路径**能读到**（不是 `unreadable`）且 `PeMetadata.path` 原生 |
| S38.4 守卫第六组 | ✅ | 一致性守卫 31 → **32 项**：用户数据模块必须用共享助手、不得自带前缀常量 |

**验收证据**：`pytest cli/tests` **674 项全绿**；旧切片与真机验收不受影响。

**边界（照 §36.5 的口径）**：本机 `LongPathsEnabled=1`，因此"策略关闭时"的行为仍**未验证**；
能主张的是"syscall 形式由我们决定，且前缀不进任何对外字段"，**不能**主张"在所有机器上都验证过"。
AIROOT 根内部（`store/`/`state/`/`logs/`）的扫描刻意不在本阶段范围内，理由见 §38.2-2。

---

## 39. 阶段计划：文档小节编号与引用的常驻守卫（守卫第七组）

> **本节是实现计划。** §30/§35/§37 把"文档与实现是否互相咬合"变成了常驻审计，但审计只看
> *内容*（码是否注册、命令是否存在、速查是否点名），**从不看文档的结构**。于是下面两处缺陷
> 在 674 项测试全绿的状态下一直存在——它们是本轮用一次性扫描找出来的，不是测试找出来的。
> 完成情况回写 §39.5。

### 39.1 本轮扫描发现的真实缺陷（不是假设）

| # | 位置 | 缺陷 | 为什么有害 |
|---|---|---|---|
| F1 | 本草案 | `### 36.6` 被留在文件**最末**、落在 `## 38` 之下 | §36 的"实施顺序"伪装成 §38 的子节；读 §36 的人根本看不到它 |
| F2 | 搜索协议 | 四个小节编号为 `### 3.1`–`### 3.4`，却挂在 `## 五、` 之下 | 同一份文档里 `## 六、` 用的是 `### 6.x`，两套编号并存；而 `AGENTS.md`、ADR-0017/0019、本草案都在引用协议里的 §3.1 / §3.3 |

F2 尤其值得记：那四处引用**当时是对的**（被引的原话确实在那两个小节里），所以任何"引用是否
存在"的检查都不会报错——错的是**编号与所在章节不符**。修它必须同时挪编号与改引用。

### 39.2 交付物

| # | 内容 |
|---|---|
| S39.1 | 本草案：`### 36.6` 归位到 `## 36` 之下（`## 37` 之前）；§36.5 那句"未修"补一条指向 §38 的后续说明 |
| S39.2 | 搜索协议：`### 3.1`–`3.4` 改号为 `### 5.1`–`5.4`；同步 4 处引用（本草案 §31/§32、ADR-0017/0019） |
| S39.3 | 守卫第七组（3 项）：编号小节必须归其所属 `##`、`N.M` 递增不重复、`N.M.K` 必须有 `N.M` 父节；带文档名的协议引用必须解析到真实标题；解析器自身的编号约定与假阳性 |
| S39.4 | 测试 + 回写 |

### 39.3 需要做出的决策（含理由）

1. **审计必须理解两种 H2 约定**：本草案与规划用 `## 12.`，三大核心契约、搜索协议、验证方案用
   `## 五、`。只认阿拉伯数字的扫描会把三份**健康**文档报成"子节无归属"——那是审计的缺陷，
   不是文档的缺陷。中文数字要认到 `十一`（搜索协议真有 `## 十一、`）。
2. **`### 8.1.1` 不是 `### 8.1` 的兄弟**：三级编号的归属章节仍是 `8`。把它当兄弟会凭空造出
   "重复的 8.1"，然后要求一次毫无意义的改名。
3. **只治理"带编号"的 `###`**：这些文档还用 `### 1. 搜索目标…` 这种列表式标题与 `### v1 支持`
   这种命名式标题；它们不携带章节身份，强加规则只会逼人改掉本来正确的写法。
4. **引用检查只认带文档名的写法**（`协议` + 节号）：裸 `§5.1` 有歧义——本草案、规划、ADR 都用
   `§N.M` 指自己的小节。跨行也算（ADR 里写的是"搜索协议"换行后接节号）。
5. **审计自己要有测试**：中文数字解析与两个标题正则的约定被单独钉住，连同三类假阳性。否则
   下一个人"简化"正则时，审计会安静地退化成永不报错。

### 39.4 明确不做

不改任何规范语义；不统一各文档的 H2 风格（那是风格，不是契约）；不把引用检查扩到裸 `§N.M`
（歧义会让它变成噪音）；不为"引用存在"做运行时检查——这是文档面的一致性，属于 L0。

### 39.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S39.1 `### 36.6` 归位 | ✅ | 现在位于 §36.5 与 `## 37` 之间；文件末不再是 §36 的子节 |
| S39.2 协议改号 + 引用 | ✅ | 协议 `### 5.1`–`5.4`；4 处引用改为协议 §5.1 / §5.3（本草案 §31/§32、ADR-0017/0019） |
| S39.3 守卫第七组 | ✅ | `cli/tests/test_l0_consistency.py` 32 → **35 项**：结构、引用解析、解析器自测 |
| S39.4 测试 + 回写 | ✅ | 本节 |

**验收证据**：`pytest cli/tests` **677 项全绿**；旧切片与真机验收不受影响（本阶段只动文档结构与测试）。

**守卫确实会红**：把 F1/F2 临时改回去，两项守卫立刻失败并指名文件与行号（`sits under ## 5`、
`not a ### heading`），改回即绿——"能失败的守卫"才算守卫。

**边界**：本阶段只覆盖**带编号**的小节与**带文档名**的引用。裸 `§N.M`、目录树、以及文档间的
语义一致性（例如"某码是否真的由 `doctor` 发射"）分别由 §4 的决策与守卫第四/五组负责，不在此处重复。

### 39.6 实施顺序

1. 先让扫描跑出真实缺陷清单（不预设问题在哪一份文档）。
2. 改文档结构 → 由引用守卫指出所有断掉的引用 → 只改那几处。
3. 把扫描落成常驻测试 + 解析器自测；回写本节与 AGENTS 计数。

---

## 40. 阶段计划：文档里出现的每个选项都必须真实存在（守卫第八组）

> **本节是实现计划。** §35 把"文档里让 agent 去跑的每条**命令**都必须存在"变成了常驻守卫，
> 但它**只看到动词**。`airoot search java --max-staleness-ms 1` 在第四组眼里完全合法——
> 哪怕那个选项第二天被改名成 `--staleness-ms`。agent 会照文档打字、拿到 `unrecognized
> arguments`，然后报告一个从未发生过的故障：与"命令不存在"同样的伤害，只是晚了一个词。
> 完成情况回写 §40.5。

### 40.1 交付物

| # | 内容 |
|---|---|
| S40.1 | 把**每个 `airoot ...` 调用**里的 `--选项` 解析到**真实的 argparse 节点**，要求它是该节点声明的选项（或已声明的预解析别名） |
| S40.2 | 覆盖面 = agent 的**操作面**：`AGENTS.md`、`SKILL.md`、`references/*.md` 的散文，加 `agents/airoot.json` 的 `command` 数组 |
| S40.3 | 三条切片规则的常驻自测（多条调用同一行 / 表格单元格 / `--` 分隔符 / 转义竖线） |
| S40.4 | `EXEC_ALIAS_FLAG` 常量：把 `exec --env` 这个别名从"埋在两处的字面量"变成**可从代码读到的声明** |
| S40.5 | 测试 + 回写 |

### 40.2 需要做出的决策（含理由）

1. **别名必须是声明，不能是巧合**：`exec --env <id> -- <cmd>` 是冻结规划表 §15.1 的拼写，
   由 `_normalize_exec_argv` 在 argparse 之前改写。它**不是** parser 选项，因此任何"问 parser"
   的守卫都会把它判成错。修法不是在测试里再写一遍 `"--env"`，而是把名字抽成
   `cli.EXEC_ALIAS_FLAG`（用到它的两处改写读同一个常量），守卫 import 它——**同一份事实只有一个出处**。
2. **只查操作面**：规划文档、本草案、ADR 是提案与记录，它们引用冻结表里的拼写（包括本 build
   尚未实现的形态）；那个方向已由"冻结命令清单"守卫负责。对操作面之外的文件做同样检查，
   只会逼着提案去迁就实现。
3. **窗口必须切得准**（见 §40.3）：一份文档里三个真实的假阳性全部来自切片，而不是来自文档写错。
   守卫把三条规则连同理由一起写成自测，免得下一个人"简化"掉它们。
4. **不查"文件里出现的任何 `--xxx`"**：试过了，是错的。文档合法地提到**不属于 AIROOT 的**选项
   （`--no-modify-path` 是 rustup 安装器的，写在 §8 的 P2 段落里），也合法地提到**只为禁止它而
   点名**的选项（`SKILL.md` 的"不发明 `--force`"）。逐调用检查才是诚实的判据：**让 agent 去打的
   那个选项必须存在**。

### 40.3 三条切片规则（每条都是被假阳性教出来的）

| 规则 | 触发它的真实假阳性 | 后果（若不做） |
|---|---|---|
| 一行可含**多个**调用，窗口在该行下一个 `airoot` 处结束 | `SKILL.md` 的答案表把 `discover` 与 `adopt … --mode reference` 放在同一行 | `--mode` 被算到 `discover` 头上 |
| **表格单元格**是一个单位，只有**未转义**的竖线才切分 | 同一行的第三个单元格写着"不发明 `--force`" | 一个被明令禁止的选项被当成文档错误 |
| ` -- ` 之后的一切属于**子进程** | `exec <id> -- <cmd>` 的写法 | 子命令自己的开关被算成 AIROOT 的 |

### 40.4 明确不做

不改任何 CLI 形状（本阶段只**读** parser）；不给别名加第二个定义处；不把检查扩到裸 `--xxx`
与提案类文档；不检查选项的**取值**（那是 argparse 自己的事，且各命令的取值语义差异太大）。

### 40.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S40.1 逐调用解析 | ✅ | 69 处调用 / `agents/airoot.json` 全部 `command` 数组逐个解析到真实节点 |
| S40.2 覆盖面 | ✅ | `AGENTS.md`、`SKILL.md`、`references/*.md`、`agents/airoot.json` |
| S40.3 切片自测 | ✅ | 四条断言分别钉住"多调用 / 单元格 / `--` 分隔符 / 转义竖线" |
| S40.4 `EXEC_ALIAS_FLAG` | ✅ | `cli.py` 唯一定义；`_normalize_exec_argv` 与守卫共读 |
| S40.5 守卫第八组 | ✅ | `cli/tests/test_l0_consistency.py` 35 → **37 项** |

**验收证据**：`pytest cli/tests` **679 项全绿**；旧切片与真机验收不受影响（本阶段只读 parser、只加测试）。

**无文档漂移**：这一组守卫上线时**没抓到任何真实的选项漂移**——唯一的"违规"是 `exec --env`，
而那是合法别名。这不是坏消息：它说明前面的阶段把文档和代码咬得还算紧；守卫的价值在于**下一次**
改名时立刻变红。

**守卫确实会红（两个方向都验过）**：把 `AGENTS.md` 的 `--max-staleness-ms` 改成
`--staleness-ms` → `AGENTS.md:226: airoot search ... --staleness-ms`；把 `agents/airoot.json`
的 `--class` 改成 `--category` → `agents/airoot.json: airoot inventory ... --category`。改回即绿。

**边界**：只检查**选项名**存在，不检查取值、不检查选项顺序、不检查"这个选项在这条命令上是否有
意义"。`agents/airoot.json` 的 `notes` 字段是散文（会在同一句里提到**别的**命令的选项，例如
`source resolve` 的备注提到"`plan --source-json`"），刻意不在检查范围内。

### 40.6 实施顺序

1. 先建立"文档选项 → parser 节点"的解析，再让它跑：**先看它报什么**，不预设文档有错。
2. 用真实假阳性把三条切片规则逐一钉下来（多调用 → 单元格 → `--` 分隔符）。
3. 把 `exec --env` 抽成 `EXEC_ALIAS_FLAG`，让守卫与改写逻辑共读同一份事实。
4. 两个方向各做一次"故意改坏 → 必须变红 → 改回"，再回写本节与 AGENTS 计数。

---

## 41. 阶段计划：两份"绝不做的清单"必须是同一份（守卫第九组）

> **本节是实现计划。** 守卫第五组把两份"未实现"声明绑在一起（§35）。安全面的**另一半**——
> 禁止项——没有这个绑定：`agents/airoot.json` 的 `never` 只被断言"非空"
> （`test_l1_skill.py` 的原话是 `assert document["never"]`），而它已经漂移。本节把它关掉。
> 完成情况回写 §41.5。

### 41.1 本轮发现的真实缺口

`SKILL.md` 的《绝不做的清单》有 8 条，`agents/airoot.json` 的 `never` 也有 8 条——
**但它们不是同样 8 条**：

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | 清单第 6 条"**不编造确定性**"（拿不准就说拿不准）在 `never` 里**没有**对应 | 诚实规则只存在于给人读的那一份；读机器可读元数据的 agent 学不到它 |
| F2 | 清单第 8 条"**不说 AIROOT 已实现 / 已可用 / Everything 级**"在 `never` 里没有对应 | 这是本项目的**头号禁令**，`AGENTS.md`、`SKILL.md` 与守卫第四组都在守它，唯独机器可读面漏了 |
| F3 | `never` 的 `describe policy_only as protected` 与 `call an unimplemented command` 锚在 SKILL 的**其他章节**（"第一步永远是确认状态"、"未实现的命令"），不在清单里 | 两条都是真禁令，但它们的归属没有被写下来——下一个人无法判断该不该进清单 |
| F4 | 数量**同为 8** | 纯属巧合，却正好让人以为两份清单是同一份——漂移因此更容易存活 |

F2 尤其刺眼：整个仓库反复强调"不得声称已实现"，而**唯一给程序读的那份禁令清单漏了它**。

### 41.2 交付物

| # | 内容 |
|---|---|
| S41.1 | `never` 改为**带稳定 `id` 的结构化条目**：`id` + `statement` + 锚点（`skill_item` 或 `stated_in`） |
| S41.2 | 补上 F1/F2 两条缺失禁令（锚到清单第 6、8 条） |
| S41.3 | 把 F3 的两条显式锚到 SKILL 的实际章节，而不是含糊地留在列表里 |
| S41.4 | 守卫第九组（2 项）：清单 ↔ 条目的**双向**对应；以及检查逻辑的**六种失败模式**逐条演练 |
| S41.5 | 测试 + 回写 |

### 41.3 需要做出的决策（含理由）

1. **对应关系必须"声明"，不能"猜"**：清单是中文、条目是英文；按关键词匹配语义等于把守卫变成
   噪音源。给每个条目一个稳定 `id` 与显式锚点，检查就变成精确的集合比较。
2. **锚点有两种，两种都合法**：`skill_item`（镜像清单第 N 条）或 `stated_in`（SKILL 的另一个
   章节已经说了它）。后者不是逃避——`policy_only` 与"不要调用未实现命令"分别在"第一步"与
   "未实现的命令"两节里说得比清单更清楚，硬塞进清单只是重复。
3. **守卫必须双向**：清单多一条却没有条目 → 红（F1/F2 就是这样发生的）；条目指向清单之外 →
   红。单向只覆盖一半，而漏掉的那一半正好是本轮的真实缺口。
4. **形状可以改，且不必抬 `protocol_version`**：`agents/airoot.json` 没有发布 schema，也没有
   任何代码消费 `never`（唯一消费者是那句"非空"断言）。改形状的代价是零，换来的是可检查的
   对应关系。**不因此提升 `protocol_version`**：那个字段没有任何强制逻辑，改了只是表演。
5. **检查逻辑抽成纯函数**：`_prohibition_problems(items, skill_text, entries)` 不碰文件，
   于是六种失败模式可以用合成输入逐条演练——"它对真实文件保持沉默"因此才是有信息的。

### 41.4 明确不做

不把两份清单合并成一处——`SKILL.md` 必须**自包含**：只读 SKILL 的 agent 也要拿到完整禁令；
不改清单的任何文案（本轮只补锚点与两条缺失禁令）；不给 `never` 加权重、严重度或执行顺序
（没有消费者，加了就是没人读的字段）；不在 `never` 里重复清单原文（两份文本会再次漂移）。

### 41.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S41.1 结构化条目 | ✅ | `never` 10 条，每条带 `id` + `statement` + 锚点；`id` 唯一且 snake_case |
| S41.2 补 F1/F2 | ✅ | `fabricate_certainty`（清单 6）、`claim_implemented_or_everything_class`（清单 8） |
| S41.3 显式锚 F3 | ✅ | `describe_policy_only_as_protected` → `stated_in: 第一步永远是确认状态`；`call_an_unimplemented_command` → `stated_in: 未实现的命令（不要调用）` |
| S41.4 守卫第九组 | ✅ | `cli/tests/test_l0_consistency.py` 37 → **39 项** |

**验收证据**：`pytest cli/tests` **681 项全绿**；旧切片与真机验收不受影响（本阶段只改元数据与测试）。

**守卫确实会红**：给 `SKILL.md` 的清单临时加第 9 条，两个测试立刻失败并指名
`SKILL.md item 9 has no machine-readable counterpart`；删掉即绿。六种失败模式
（清单独有条目 / 条目越界 / 同一清单项被两条认领 / `stated_in` 指向不存在的标题 / 既无
`skill_item` 又无 `stated_in` / `id` 重复）在合成输入上逐条演练。

**边界**：守卫检查的是**对应关系**，不是**语义**——它无法判断第 6 条的中文与
`fabricate_certainty` 的英文说的是不是同一件事。那一步由人做，并留下 `id` 作为可审计的锚点。
`statement` 字段是给人读的摘要，不参与匹配。

### 41.6 实施顺序

1. 先并列两份清单，逐条对齐——**先把差异摆出来**，不预设哪一边对。
2. 决定每条禁令的归属（清单项 or 其他章节），写成显式锚点。
3. 补上只存在于 SKILL 一侧的两条禁令。
4. 把检查写成纯函数 + 六种失败模式的自测；再做一次"清单加一条 → 必须变红 → 改回"。
5. 回写本节与 AGENTS 计数。

---

## 42. 阶段计划：agent 要读的每个字段都必须真的存在（守卫第十组）

> **本节是实现计划。** §41 把两份"禁止项"绑在一起；**输出面**还留着同一个模式：`agents/airoot.json`
> 的每个调用条目都写着 `"read": [...]`——"问这个问题时，读这些字段"。而它的**唯一**检查是
> `test_l1_skill.py` 里的 `assert entry["read"]`：**非空**。字段被改名，agent 就去找一个不存在
> 的东西；这比命令不存在更糟——**缺字段和 `false` 长得一样**。`in_sync` 不见了，读起来就是
> "不同步"。完成情况回写 §42.5。

### 42.1 为什么这条守卫必须"跑起来"才算数

前三组守卫都是**静态**的（文档结构、选项名、清单对应）。这一条不行：要证明 `diagnostics[].severity`
存在，就得**真的产出一份带诊断的 doctor 文档**。所以本阶段建一个真实 root，让 31 个调用逐个跑出
文档，再把 117 条 `read` 路径**结构化地**解析一遍。

顺带得到第二个好处：这个 root 就是一份**验收面**——它产出的正是 Rust 版要逐字节复现的那些
agent 面文档，而 golden 语料目前只覆盖了其中一小部分。

### 42.2 交付物

| # | 内容 |
|---|---|
| S42.1 | `cli/tests/test_l1_agent_read_fields.py`：一个真实 root（数据根 + 已 adopt 的 reference + 已安装的 owned 实例 + 已 seed 的持久化记录），逐个跑出 agent 面文档 |
| S42.2 | `unresolved()`：`a.b[].c` 记号的结构化解析（纯函数 + 自测） |
| S42.3 | `UNCOVERED`：跑不出来的调用必须**写明理由**；测试断言"已覆盖 ∪ 已声明 = 全部"，新条目不能悄悄漏掉 |
| S42.4 | 测试 + 回写 |

### 42.3 需要做出的决策（含理由）

1. **检查必须精确，不能靠关键词**：`read` 路径是结构，不是字符串。解析器按 `a.b[].c` 逐段下降，
   空列表视为"没有东西可查"（健康的 `doctor` 本来就没有 `diagnostics[]`——把它算失败会让守卫在
   最常见的情况下不可用）。这条语义连同反例写进自测。
2. **先观测、后变更**：同一个 root 上跑 31 个调用，顺序是语义的一部分——`tool retire` 之后
   `tool status` 就没有 payload 了，`forget` 之后 reference 就没了。所以**观察类动词全部排在前面，
   变更类排在最后**，`forget` 收尾。
3. **持久化记录只能从模块 API 注入**：CLI 的 `env persist` 会写真实的 `HKCU`，而测试**永不**
   碰宿主机（`conftest.py` 的会话级守卫就是这条）。所以用 `apply_reference_plan(..., store=InMemoryEnvironmentStore())`
   seed 一条记录，再让 CLI 从 registry 里把它读回来——`env forget --dry-run` 报的正是这份记录。
4. **`env persist` 的场景是"无 token"那一支**（退出码 4）：条目上写的就是不带 `--dry-run` 的拼写，
   而这一支才带着 `plan_hash`/`plan_file`/`required_action`——`--dry-run` 那一支本来就不该有它们。
5. **不接受"阈值式"断言**：`len(covered) >= 30` 这种写法会随着条目增加而悄悄变松。改成
   `len(covered) == len(invocations()) - len(UNCOVERED)`——**精确等式**。

### 42.4 明确不做

不把 golden 语料一次性扩到全部 31 个调用（那是另一件事，且 fixtures 是逐字节验收面，与本守卫的
目标不同）；不检查 `read` 字段的**类型**或**语义**（只检查"存在"）；不检查 `notes` 字段；不为
跑不通的调用伪造文档——跑不出来就进 `UNCOVERED` 并写明理由。

### 42.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S42.1 真实 root 跑 31 个调用 | ✅ | `test_l1_agent_read_fields.py`；数据根 + reference + owned 实例 + 持久化记录 |
| S42.2 结构化解析 | ✅ | `unresolved()` + 7 条自测（含"空列表算覆盖"与"承诺列表却给了字典"） |
| S42.3 覆盖率断言 | ✅ | `UNCOVERED` **为空**；`covered == 31 - 0`，精确等式 |
| S42.4 测试 + 回写 | ✅ | `pytest cli/tests` 681 → **684 项** |

**验收证据**：`pytest cli/tests` **684 项全绿**；旧切片与真机验收不受影响（本阶段只加测试）。

**没有发现字段漂移**：31 个调用、**117 条 `read` 路径全部解析成功**，`UNCOVERED` 一条都不需要。
这不是坏消息——它说明 `agents/airoot.json` 的 `read` 列表此前与实现是一致的；守卫的价值在于
**下一次改名**。与 §40 一样，如实记下"本轮没抓到真问题"，而不是把"没抓到"包装成"已验证无误"。

**守卫确实会红**：把 `doctor` 的 `diagnostics[].severity` 改成 `diagnostics[].level`，
测试立刻失败并给出 `doctor: exit 2 does not carry ['diagnostics[].level']`；改回即绿。

**边界**：只证明**存在**，不证明**取值有意义**（`in_sync: null` 与 `in_sync: false` 都"存在"）；
`UNCOVERED` 为空是**本机、本 root**的事实——将来某个调用需要跑不出来的前置条件时，它必须带着
理由进那张表，而不是被静默跳过。

### 42.6 实施顺序

1. 先用探针脚本把 31 个调用跑一遍，**先看哪些跑不出来、哪些字段缺**，不预设结论。
2. 按"先观测后变更"重排场景（这一步修掉了 4 个假的 MISS——它们全是顺序问题）。
3. 需要持久化记录的那两个场景，用注入式 store 从模块 API seed。
4. 把探针落成常驻测试 + 解析器自测 + 精确覆盖率断言。
5. 改一个字段验证它会红，再回写本节与 AGENTS 计数。

---

## 43. 阶段计划：策略数据只能使用代码真的实现的词汇（守卫第十一组）

> **本节是实现计划。** §40/§42 治理的是**文档**里的选项与字段。这一节治理另一类同源缺陷：
> **随包发布的策略数据**。`policy/discovery-whitelist.json` 决定"什么算作某个能力"，
> `policy/sources.json` 决定"从哪里取、取哪个文件"。两者都是**数据**，而代码对它们的处理方式
> 有一个共同点：**不认识的构造不会报错，只会静默不匹配**。完成情况回写 §43.5。

### 43.1 本轮发现的真实风险面

| # | 位置 | 缺陷形态 | 为什么有害 |
|---|---|---|---|
| F1 | `discovery.py` `_matches` | 未知谓词类型 → `return False` | 一个字母打错（`exectuable_name`），该能力的发现就**永久关闭**，而机器只表现为"没有这个能力"。作者当时的注释已经写明这条默认是 fail-closed——但它同时是**沉默**的 |
| F2 | `discovery.py` `_field_value` | 未知 PE 字段 → `None` → `False` | 同一类沉默失败的第二条路径 |
| F3 | `discovery.py` `_version_for` | 未知 `version_source` → 静默回落到默认字段 | 报出的是**错版本**，而不是错误 |
| F4 | `sources.py` `resolve_source` | 未知占位符 → `str.format` 抛 `KeyError` | 响亮得多，但只在**有人真的要装那个能力**时才响；在此之前目录看起来是好的 |

F1 是重点：它此刻是**静默**的，而包里的白名单是**随代码发布**的——一条 typo 会通过所有测试。

### 43.2 交付物

| # | 内容 |
|---|---|
| S43.1 | `discovery.py`：把谓词词汇声明成数据（`PREDICATE_TYPES` / `PE_PREDICATE_FIELDS` 由 `PeMetadata` 派生 / `PE_PREDICATE_OPERATORS` / `VERSION_SOURCE_PREFIX`），并在**加载期**拒绝不认识的构造 |
| S43.2 | `sources.py`：`SUBSTITUTION_KEYS` + 加载期校验三个模板（`artifact_url` / `checksum.url` / `filename`）的占位符 |
| S43.3 | 测试：负例逐条（未知谓词 / 未知字段 / 运算符缺失或重复 / 缺 `any_of` / 不可用 `version_source` / 未知占位符） |
| S43.4 | 测试：**危险方向**——声明了却没实现（`PREDICATE_TYPES` 里每一类都必须真能匹配；每个 `SUBSTITUTION_KEYS` 都必须真被替换） |
| S43.5 | 测试 + 回写 |

### 43.3 需要做出的决策（含理由）

1. **在加载期拒绝，而不是只在测试里扫 JSON**。§40/§42 是文档面，静态扫描是对的；这里是**代码
   自己加载的数据**，加载期拒绝比测试更强：它同时保护随包白名单和任何传入的白名单，而且失败
   发生在最早的点。这也与既有做法一致——加载器**已经**会拒绝"没有静态证据"的条目、会拒绝
   引用未冻结能力、会拒绝未知键、会拒绝未知校验和格式。
2. **`PE_PREDICATE_FIELDS` 从 `PeMetadata` 派生，不手抄**。手抄一份字段名清单，就是给下一次
   "探测加了字段但白名单校验没跟上"留位置。
3. **`pe_static` 必须恰好一个运算符**：一个都没有 → 永远不匹配；两个都有 → 其中一个静默失效。
   两种都要拒。
4. **危险方向要单独测**：加载期拒绝只保证"不认识的进不来"，不保证"认识的真的能用"。
   所以另有一条测试，用真实的解释器目录，对 `PREDICATE_TYPES` 的**每一类**跑一次必须命中的匹配，
   并断言"多了一类却漏了用例"会失败。
5. **不去动 `_matches` 的 fail-closed 默认**。匹配期的 `return False` 是对的（宁可漏报不可误报），
   本阶段只是让**装进来的数据**不可能落到那一步。

### 43.4 明确不做

不引入谓词插件机制或表达式语言（v1 只有三类谓词，够用）；不改白名单/来源清单的**任何取值**；
不校验 `notes`/`publisher` 等纯文字字段；不为 `version_source` 增加新前缀（那是能力，不是加固）。

### 43.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S43.1 白名单词汇加载期校验 | ✅ | `PREDICATE_TYPES` / `PE_PREDICATE_FIELDS`（14 个，派生自 `PeMetadata`）/ `PE_PREDICATE_OPERATORS` / `VERSION_SOURCE_PREFIX`；`_validate_predicates` + `_validate_version_source` |
| S43.2 来源模板占位符校验 | ✅ | `SUBSTITUTION_KEYS` + `_validate_placeholders`（三个模板） |
| S43.3 负例测试 | ✅ | `test_l1_discovery.py` 新增 8 项（含 2 个参数化） |
| S43.4 危险方向测试 | ✅ | `test_every_declared_predicate_type_is_actually_implemented`、`test_every_declared_substitution_is_actually_performed` |
| S43.5 测试 + 回写 | ✅ | `pytest cli/tests` 684 → **699 项** |

**验收证据**：`pytest cli/tests` **699 项全绿**；旧切片与真机验收不受影响。

**随包数据没有漂移**：白名单 `wl-3` 的 7 个条目、来源清单 `src-1` 的 3 个条目全部通过新的加载期
校验——今天没有需要修的条目。与 §40/§42 一样如实记下"本轮没抓到真问题"。

**§82 已改判**：这里的 `wl-3` / 7 个条目是 §43 当时的事实。**今天白名单是 `wl-4` / 6 个条目**（`media_probe` 随冻结清单一起移除，ADR-0025 的 D9）。原数字保留。

**守卫确实会红**：把随包白名单里 python 条目的 `executable_name` 打成 `exectuable_name`，
`load_whitelist()` 立刻抛
`whitelist entry python declares an unknown evidence predicate: 'exectuable_name'`，
`test_the_shipped_whitelist_uses_only_evaluable_vocabulary` 失败；改回即绿。
**在本次改动之前，那个 typo 不会让任何测试变红**——它只会让这台机器"看起来没有 Python"。

**边界**：校验的是**词汇**（类型、字段、运算符、占位符），不是**语义**——它无法判断
`contains: "Python"` 是否选对了产品名。那是人写白名单时的判断，本轮只是保证写错的**拼写**
不会静默生效。

### 43.6 实施顺序

1. 先把谓词词汇从代码里"读"出来（`if kind == ...` 的分支、`_field_value` 的字段、`getattr` 的
   `version_source`），确认清单是穷举的。
2. 把词汇声明成常量，加载器调用校验；先确认随包白名单仍能加载。
3. 负例逐条写测试（每条对应一种静默失败）。
4. 补危险方向测试（声明了却没实现）。
5. 对来源清单重复 2–3（占位符 + 校验和格式已有的先例）。
6. 注入 typo 验证会红，再回写本节与 AGENTS 计数。

---

## 44. 阶段计划：散文里的退出码必须与冻结映射一致（守卫第十二组）

> **本节是实现计划。** 退出码有两张**已被机器核对**的表：诊断码表（§30 的守卫）与
> `SKILL.md` 的退出码表（`test_l1_skill.py`）。**散文没有被核对**：`AGENTS.md` 与 `references/`
> 里到处是行内的退出码——`` `SEARCH_FALLBACK_USED`(2) ``、`` `OWNERSHIP_REQUIRED`(7) ``、
> `` `SELF_VALIDATION_FAILED`，退出码 8 ``。那些正是 agent**回报给用户**的数字。完成情况回写 §44.5。

### 44.1 本轮实测到的事实（不是假设）

扫描操作面（`AGENTS.md` / `SKILL.md` / `references/*.md` + 权威诊断码表）得到 **19 条**行内退出码声明：

| 文件 | 条数 | 例 |
|---|---|---|
| `AGENTS.md` | 14 | `SEARCH_BACKEND_UNAVAILABLE`(9)、`OWNERSHIP_REQUIRED`(7)、`SELF_VALIDATION_FAILED`(8) |
| `SKILL.md` | 3 | `SCOPE_CONFIRMATION_REQUIRED`(4)、`CHILD_PROCESS_FAILED`(2) |
| `references/confirmation.md` | 2 | `SCOPE_UPGRADE_REQUIRES_APPROVAL`(4)、`PRIVILEGE_REQUIRED`(5) |
| 诊断码表 | **0** | 它的声明都在那张已被守卫核对的**表**里 |

19 条**今天全部一致**。这不是坏消息，但它意味着：改一个退出码（冻结契约级改动）会让这 19 处
全部过时，而且**没有任何东西会响**。

### 44.2 交付物

| # | 内容 |
|---|---|
| S44.1 | 守卫第十二组（2 项）：操作面 + 权威诊断码表里每条行内退出码声明都必须等于 `REASON_EXIT` 的取值 |
| S44.2 | 形式**刻意收窄**的自测：只有显式标记才算声明（括号里的单数字、或"退出码"后接数字） |
| S44.3 | 非空洞下限：形式一旦不再匹配文档，测试要失败，而不是静默地什么都不查 |
| S44.4 | 测试 + 回写 |

### 44.3 需要做出的决策（含理由）

1. **只认显式标记**。代码名旁边出现一个数字**不构成**声明：`D7` 覆盖三个码、
   `§17.6` 是章节号、"**两个**码"是数量。把规则放宽成"代码附近有数字就算"，是把守卫变成噪音源
   ——与 §40.4、§42.4 否掉的两个方案同一个理由。两条正则因此都要求括号或"退出码"字样。
2. **边界用 `(?<![A-Z0-9_])` / `(?![A-Z0-9_])` 卡住**，免得 `SEARCH_FALLBACK_USED_EXTRA`(2)
   被当成 `SEARCH_FALLBACK_USED` 的声明（自测里钉住了这一条）。
3. **覆盖面沿用既有口径**：操作面 + 权威诊断码表。**不扫**规划文档、本草案与 ADR——它们是提案与
   记录，其中一处退出码可能是**当时**的历史事实，扫它们会制造假阳性（§40.2-2 的同一条理由）。
4. **下限是"非空洞"而不是"覆盖率"**：`claims >= 15` 与 §42.3-5 否掉的阈值不同——那里阈值会随清单
   增长而变松，这里唯一的作用是"正则不再匹配任何东西时必须失败"。
5. **线号要准**：报告 `文件:行` 才能直接跳过去改，所以匹配在整篇文本上做，行号由偏移量算
   （与 §39.2 引用检查同一做法）。

### 44.4 明确不做

不改任何退出码、不改任何文档措辞（本轮只加守卫）；不检查**退出码本身的语义**（那是冻结表与
`EXIT_MEANINGS` 的事）；不把两处已经是表格式的声明合并进本守卫（它们已有各自守卫，重复只会
让一处失败报出两个原因）；不扫提案类文档（见决策 3）。

### 44.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S44.1 守卫第十二组 | ✅ | `cli/tests/test_l0_consistency.py` 39 → **41 项** |
| S44.2 收窄形式自测 | ✅ | 5 条断言：括号声明 / "退出码"声明 / `D7` 旁的无关数字 / 更长标识符 / 行号 |
| S44.3 非空洞下限 | ✅ | `claims >= 15`，并在注释里写明它**不是**覆盖率阈值 |
| S44.4 测试 + 回写 | ✅ | `pytest cli/tests` 699 → **701 项** |

**验收证据**：`pytest cli/tests` **701 项全绿**；旧切片与真机验收不受影响（本阶段只加测试）。

**没有发现漂移**：19 条声明全部与 `REASON_EXIT` 一致，无需改动任何文档。与 §40/§42/§43 一样，
如实记下"本轮没抓到真问题"。

**守卫确实会红**：把 `AGENTS.md` 里 `SEARCH_BACKEND_UNAVAILABLE`(9) 改成 (8)，测试立刻失败并给出
`AGENTS.md:29: SEARCH_BACKEND_UNAVAILABLE is quoted as exit 8, but the mapping says 9`；改回即绿。

**边界**：只核对**行内散文**里的退出码。表格形式的声明由各自的守卫负责；退出码到
`EXIT_MEANINGS` 的语义一致性也在别处。

### 44.6 实施顺序

1. 先把操作面上所有"代码名 + 数字"的形态**列出来**（不预设它们都是退出码声明），逐条确认。
2. 确认诊断码表贡献 0 条（它的声明已经是表格），据此决定是否把它纳入覆盖面。
3. 两条正则 + 边界断言 + 行号；写自测把"无关数字不算声明"钉死。
4. 改一个数字验证会红，再回写本节与 AGENTS 计数。

---

## 45. 阶段计划：状态机的"合法移动表"要自洽，并且进验收语料（守卫第十三组）

> **本节是实现计划。** 事务状态机是**冻结契约**（三大核心契约 §4.1、ADR-0003），
> `tx/states.py` 里的 `TRANSITIONS` 是它的手工转录。但此前对这张表只检查了两件事：
> `EXPIRED` 是死胡同、schema 能**命名**每个状态。**没有任何东西检查这张表自不自洽。**
> 完成情况回写 §45.5。

### 45.1 本轮发现的两个缺口

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | 表的**内部不变量**无人检查：键集合与 `DOCUMENTED_STATES` 是否相等、目标是否都是已文档化状态、happy path 的每一步是否合法、终态是否真的没有出边、`PAYLOAD_STATES` 是否跟着 happy path、是否有**不可达**的已文档化状态 | 任何一条破了，都是"契约说一套、机器做一套"：能到达却没写进文档的状态、永远到不了的文档状态、终态还能往外走、happy path 里藏着非法步骤 |
| F2 | **合法移动表不在验收语料里** | ADR-0001 说 golden 语料是语言中立的验收面。可契约文档只给了 happy path 与异常状态**清单**，**没给边集**；于是 Rust 版可以把每个响应都复现得一模一样，却对"哪些移动合法"给出不同答案，而且**没有 fixture 会发现** |

F2 是本轮的重点：这不是"文档写错了"，而是**验收面缺了一块**。

### 45.2 交付物

| # | 内容 |
|---|---|
| S45.1 | 新 golden fixture `transaction_transitions.json`：`happy_path` / `terminal_states` / `payload_states` / `active_binding_commit_state` / `transitions` 整张表 |
| S45.2 | 守卫第十三组（8 项）：表与已文档化状态集合相等；目标都是已文档化状态；happy path 每步合法且 `next_happy_state` 一致；**终态 ⟺ 死胡同**（双向）；每个已文档化状态都可达；`PAYLOAD_STATES` 恰好是 COMMITTED 及其之后 + `ROLLBACK_PENDING`；提交点在 happy path 上；代码里的表与 fixture **逐项相等** |
| S45.3 | 把新 fixture 登记进"孤儿 fixture"守卫的已知集合 |
| S45.4 | 测试 + 回写 |

### 45.3 需要做出的决策（含理由）

1. **不变量写成可执行的断言，而不是注释**。这 8 条都是**纯结构**判据，不需要解析文档、不需要判断语义，
   所以没有假阳性风险；而它们每一条破了都是真缺陷。
2. **"终态 ⟺ 死胡同"必须双向**。只查一个方向会漏掉另一半：终态还能出边（`is_terminal` 说谎），
   或者非终态却有去无回（状态机走进死胡同却报告"未完成"）。
3. **`PAYLOAD_STATES` 从 happy path 推导，不手抄**。它的语义是"这里可能有 payload"，那就必须
   恰好等于 COMMITTED 及其之后的快乐路径状态，外加回滚挂起态——多一个会让 `gc` 对不存在的
   payload 动手，少一个会让它在有 payload 时不敢动。
4. **边集进语料，但不改冻结文档**。契约 §4.1 的措辞（"任何阶段都可能进入"五个异常状态）比这张表
   **宽**：逐字读它，`PROPOSED -> ROLLED_BACK` 也该合法，而表里不允许。这处**措辞歧义如实记下来**
   （§45.5），但**不单方面改写 layer-2 的冻结契约文本**——那是需要用户批准的动作（本仓库此前两次
   改冻结文本都走了批准）。可执行的修法是：**把边集变成验收语料**，让两种读法的差异在移植时
   立刻暴露，而不是靠读者猜。
5. **fixture 用列表而不是集合**：`transitions` 的目标顺序是契约的一部分（`evidence` 里会把允许的
   移动按这个顺序打印），JSON 里保持数组。

### 45.4 明确不做

**不**改冻结契约文档的措辞（见决策 4）；**不**给状态机加新的状态或边（本节只做加固）；**不**把
`TRANSITIONS` 换成数据文件（它现在是代码里的单一定义处，`states.py` 的注释就是这条约定）；
**不**为非法转移单独生成 fixture（`require_transition` 的拒绝行为已由 L1 测试覆盖）。

### 45.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S45.1 `transaction_transitions.json` | ✅ | 11 步 happy path、16 个状态、**29 条边**（ADR-0021 落地 §4.1 的"任何阶段"读法后为 **62 条**）、2 个终态、7 个 payload 状态 |
| S45.2 守卫第十三组 | ✅ | `test_l1_transaction.py` 新增 **8 项**（38 → 46） |
| S45.3 孤儿守卫登记 | ✅ | `test_l0_consistency.py` 的已知集合加入该 fixture |
| S45.4 测试 + 回写 | ✅ | `pytest cli/tests` 701 → **709 项** |

**验收证据**：`pytest cli/tests` **709 项全绿**；旧切片与真机验收不受影响。

**重新生成语料是外科式的**：`python cli/tests/golden.py` 之后逐一比对 SHA256——**21 个既有 fixture
逐字节不变**（含四份 `doctor_*`），只有新 fixture 与 `index.json`（多一条映射）发生变化。

**没有发现漂移**：8 条不变量今天全部成立（16 个状态全部可达、29 条边（**ADR-0021 后为 62 条**）、`PAYLOAD_STATES` 与推导一致），
所以本轮**没有改动任何状态机行为**——只补了检查与语料。与 §40/§42/§43/§44 一样如实记下
"本轮没抓到真问题"。

**守卫确实会红**：把 `TRANSITIONS["FINALIZED"]` 改成 `("FAILED",)` 后，
`test_terminal_states_are_exactly_the_dead_ends` 与
`test_the_legal_move_table_matches_its_acceptance_artifact` 立刻失败（后者报出
`{'FINALIZED': []} != {'FINALIZED': ['FAILED']}`）；改回即绿。这正是 F2 的价值：
**一个改动即使不改变任何响应，也会被验收语料抓住**。

**如实记录的一处措辞歧义**：三大核心契约 §4.1 在列出五个异常状态前写的是"**任何阶段都可能进入**"。
逐字读，`PROPOSED -> ROLLED_BACK` 与 `FETCHED -> RECOVERY_REQUIRED` 都应当合法；而 `TRANSITIONS`
只允许其中一部分（例如 `PROPOSED` 只能去 `APPROVED`/`FAILED`）。两种读法**目前都没有被文档裁决**。
本阶段的做法是：把**表的**边集钉进验收语料（决策 4），并在本节记下这处歧义，等使用者裁决是
"文档该收紧"还是"表该放宽"——**不自行二选一**。

**边界**：守卫只覆盖**结构与自洽**。它不判断"这条边在业务上是否合理"（那是契约设计），也不覆盖
转移的**副作用顺序**（那是 `simulate.py` 的职责，由 L1/L2 测试覆盖）。

### 45.6 实施顺序

1. 先把不变量当探针跑一遍（8 条），确认今天成立——不预设有问题。
2. 确认契约文档给的是**清单**而不是**边集**（决定 F2 的修法是进语料而不是改文档）。
3. 加 fixture：先确认 `golden.py` 重新生成只影响新文件与 `index.json`（SHA256 逐一比对）。
4. 写 8 项断言 + 与 fixture 的逐项相等；登记孤儿守卫。
5. 改一条边验证会红（并确认**响应不变**时也会红），再回写本节与 AGENTS 计数。

---

## 46. 阶段计划：D1–D10 目录在两处必须一致，并进验收语料（守卫第十四组）

> **本节是实现计划。** §45 的教训可以推广：**契约目录**（catalogue）会同时存在于文档与代码两处，
> 而"两处一致"往往没人检查，语料里也往往没有它。本轮处理诊断面最核心的那张目录：
> **哪个不变量拥有哪些 reason code**。完成情况回写 §46.5。

### 46.1 本轮发现的两个缺口

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | 目录**存在两处**——`docs/AIROOT-v0.3-诊断码与ReasonCode表.md` §2 的表，与 `caps/doctor.py` 的 `INVARIANTS`——但**没有任何守卫把它们互相比对**。既有的四组守卫只检查：目录恰好是 D1–D10、每个码都是已注册的 reason code、每个码都有产生路径 | 一个码从 D5 挪到 D7，表会继续按旧分组说话。而那张表正是读者用来判断"这是 D3 漂移还是 D7 陈旧"的依据 |
| F2 | 目录**不在验收语料里** | 与 §45 的 F2 同构：Rust 版可以把每份诊断文档都复现得一模一样，却把码分到不同的不变量下，而且没有 fixture 会发现 |

### 46.2 交付物

| # | 内容 |
|---|---|
| S46.1 | 守卫第十四组（3 项）：文档表 ↔ `INVARIANTS` 的双向比对（含 D 编号集合）；**没有码有两个归属**；以及解析器只读"码"那一列的自测 |
| S46.2 | 新 golden fixture `invariant_catalogue.json`：D1–D10 → 码列表，整张目录 |
| S46.3 | `test_golden.py` 中的全目录断言（编号恰好 D1–D10、列出的码恰好等于 `DIAGNOSTIC_CODES`、无重复） |
| S46.4 | 测试 + 回写 |

### 46.3 需要做出的决策（含理由）

1. **只读第三列**。D4 的**描述**里引用了 `` `UNMANAGED_OBJECT_PRESENT` ``，D7 的描述里引用了
   `` `index.max_age_ms` ``；按整行抓反引号会把散文当成目录条目。所以解析只取表格的第三个单元格，
   并把这条写进自测——**在同一个文件里，散文与目录长得很像**。
2. **不比对顺序**。表里码的书写顺序与 `INVARIANTS` 元组顺序目前一致（实测如此），但顺序**不承载
   语义**（元组只用于成员判断与遍历），强制它只会增加无谓的脆弱性。比对**集合**，并在回写里
   如实说明"顺序一致但不作为判据"。
3. **"没有码有两个归属"单独成一条**。它是目录自身的性质：一个码挂在两个不变量下，`doctor` 的
   分组就失去意义。今天成立，但要写成断言。
4. **两处都补，不只补一处**：§45 只补了语料（因为文档没给边集，无从比对）；这里**文档给了分组**，
   所以既要比对（F1）也要进语料（F2）。**修法的形状取决于文档里到底有什么**——这是两轮之间
   唯一实质的区别。
5. **不在本轮扩大目录清单**。`CONFIRMATION_OPTIONS`、冻结能力清单、搜索上限同样是"代码里的冻结
   目录"，但各有各的守卫需求；一次只把一件事做扎实（见 §46.4）。

### 46.4 明确不做

**不**在本轮把其它冻结目录（`CONFIRMATION_OPTIONS`、`policy/capabilities.json`、`policy/search-policy.json`
的上限）也搬进语料——它们是**已知的同类候选项**，列在这里以免被当成遗漏；**不**改任何分组或码
（本轮只加检查与语料）；**不**改那张表的措辞。

### 46.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S46.1 守卫第十四组 | ✅ | `test_l0_consistency.py` 41 → **44 项** |
| S46.2 `invariant_catalogue.json` | ✅ | D1–D10，**30 个码** |
| S46.3 语料全目录断言 | ✅ | `test_golden.py` 新增 1 项 |
| S46.4 测试 + 回写 | ✅ | `pytest cli/tests` 709 → **713 项** |

**验收证据**：`pytest cli/tests` **713 项全绿**；旧切片与真机验收不受影响。

**重新生成语料仍是外科式的**：逐一比对 SHA256，**22 个既有 fixture 逐字节不变**，只有新 fixture 与
`index.json` 变化。

**没有发现漂移**：文档表与 `INVARIANTS` 完全一致——D1–D10、合计 30 个码、且**书写顺序也一致**
（顺序不作为判据，见决策 2），没有码有两个归属。与 §40/§42/§43/§44/§45 一样如实记下
"本轮没抓到真问题"。

**守卫确实会红**：把 `DESIRED_NOT_SATISFIED` 从 `D5` 挪到 `D7`，
`test_the_documented_invariant_table_matches_the_code_catalogue` 报
`D5: code-only=[] table-only=['DESIRED_NOT_SATISFIED']`、`D7: table-only=[...] code-only=['DESIRED_NOT_SATISFIED']`，
同时 `test_the_invariant_catalogue_fixture_is_the_whole_catalogue` 失败；改回即绿。
注意这次改动**不改变任何一份诊断文档的内容**——只改变"它属于哪个不变量"，这正是 F1/F2 说的那类漂移。

**边界**：守卫覆盖**归属与集合**，不覆盖 D1–D10 的**文字含义**（那是规范判断），也不覆盖"某个码
是否真的由 `doctor` 发射"（那是守卫第四组与 `test_no_diagnostic_code_is_promised_without_a_path...`
的职责）。解析依赖表格形状：行首必须是 `| D<n> |`——表被改写形状时守卫会失败，而不是静默通过。

### 46.6 实施顺序

1. 先把两处目录**都读出来**并逐不变量比对（含顺序），确认现状——不预设有分歧。
2. 确认表格的哪一列才是码（D4/D7 的描述里也有反引号），据此定解析范围。
3. 加 fixture；SHA256 逐一确认既有语料未动。
4. 写三条断言（双向比对 / 无双重归属 / 解析器自测）+ 语料全目录断言；登记孤儿守卫。
5. 挪一个码验证会红，再回写本节与 AGENTS 计数。

---

## 47. 阶段计划：`scope` 一词承载三套词汇，每套钉到它已发布的来源（守卫第十五组）

> **本节是实现计划。** 前两轮补的是"契约目录没有验收语料"。本轮处理一类更隐蔽的问题：
> **同一个词在三套词汇里出现**，每套都合法，混用则不是。`scope` 就是这样一个词，而 CLI 已经
> 暴露了**五套不同的 `--scope` 取值**，此前没有任何东西把它们钉到各自的来源上。完成情况回写 §47.5。

### 47.1 本轮实测到的事实

三套词汇（各自的权威来源不同）：

| 词汇 | 含义 | 取值 | 已发布的来源 |
|---|---|---|---|
| binding | 能力可以绑定到哪一层 | `system`/`machine`/`session`/`project` | `common.schema.json#/$defs/scope` == `inventory.SCOPES` |
| persistence | 持久化的值影响多远 | `user`/`machine` | `reference-plan.schema.json` 的 `target_scope` == `exposure.PERSIST_SCOPES` |
| routing | 依赖该装到哪里 | `project`/`data-root` | 无 schema，只有 `planner.SCOPE_PROJECT`/`SCOPE_DATA_ROOT` |

五套 `--scope` 取值（全部是手写字面量）：

| 命令 | 取值 | 说明 |
|---|---|---|
| `where --scope` | 四值全集 | binding |
| `inventory --scope` | 四值全集 | binding |
| `tool pin --scope` | 三值（**缺 `system`**） | binding 的**子集** |
| `plan --scope` | `machine` + routing 两值 | **两套词汇混用** |
| `env persist --scope` | 两值 | persistence |

**重叠就是危险所在**：`machine` 同时属于 binding 与 persistence，`project` 同时属于 binding 与
routing——同一个拼写在两套词汇里意思不同。

### 47.2 本轮发现的三个缺口

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | `capabilities.json` 的 `scope` **没有加载期校验**（`kind` 与 `side_effects` 都有），而读取方式是 `tuple(str(v) for v in item.get("scope", []))` | 写成裸字符串 `"machine"` 会被**按字符**读成 `('m','a','c',...)`；打成 `"projet"` 则一路进入 `capability list` 的 agent 面输出 |
| F2 | 冻结清单里声明的 `scope` **没有任何消费者**（`check_admission` 三个条件都不看它，路由也不看） | 文件自己的 notes 把"its scope semantics"列为冻结的一部分，读者会以为它被强制执行 |
| F3 | 五套 `--scope` 取值都是**手写字面量**，没有一条守卫把它们钉到 `SCOPES`/`PERSIST_SCOPES`/routing 常量上 | 改一处常量而漏改字面量，表现为某个动词**静默地拒绝或接受**一个值 |

### 47.3 交付物

| # | 内容 |
|---|---|
| S47.1 | `boundary.py` 加载期校验 `scope`：必须是**名字列表**（拦住按字符读），每个值必须在 binding 词汇内；词汇从 `inventory.SCOPES` **import**，不复制 |
| S47.2 | 守卫第十五组（4 项）：binding 词汇 == `$defs/scope`；persistence 词汇 == `reference-plan` 的 `target_scope`；**五套 `--scope` 逐套钉死**（含两个子集/混用）；**重叠集合显式断言** |
| S47.3 | 新 golden fixture `frozen_capabilities.json`：冻结能力清单整表（第三个契约目录进语料） |
| S47.4 | 测试 + 回写 |

### 47.4 需要做出的决策（含理由）

1. **词汇只留一处定义**：binding 词汇的代码定义是 `inventory.SCOPES`（它同时是 `inventory --scope`
   的校验依据），`boundary` **import 它**而不是再写一份。已确认 `inventory` 不 import `boundary`，
   无环。
2. **只拦两种错，不多管**：`scope` 必须是字符串列表、值必须在词汇内。**重复值与空列表不拦**——
   `side_effects` 同样不管，而且目前没有消费者会给"重复"或"空"赋予含义；凭空立规就是把策略写进
   校验器。
3. **两个子集要"钉死"而不是"允许"**：`tool pin --scope` 缺 `system`、`plan --scope` 混用 routing，
   两者都是**刻意的**。守卫写成**精确相等**，于是任何变化必须是有意识的改动，而不是漂移。
4. **重叠写成断言**：`machine ∈ binding ∩ persistence`、`project ∈ binding ∩ routing` 是**缺陷来源**，
   但今天确实是事实。把它断言下来，是把隐患变成"已知事实"；**不是**把它正当化。
5. **不实现 scope 强制**（F2）：那要回答"未声明 `machine` 的能力能不能被绑定到 machine"这类契约
   语义问题，属于需要使用者裁决的设计决定，不是加固。

### 47.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S47.1 加载期校验 | ✅ | `boundary.py` 校验"名字列表"+ 词汇成员；`test_l1_boundary.py` 新增 **5 项**（含 3 个参数化） |
| S47.2 守卫第十五组 | ✅ | `test_l0_consistency.py` 44 → **48 项** |
| S47.3 `frozen_capabilities.json` | ✅ | `cap_revision=cap-1`，8 个能力；`test_golden.py` 新增 1 项 |

**§82 已改判**：S47.3 的 `cap-1` / 8 个能力是 §47 当时的事实；今天该 fixture 记的是 **`cap-2` / 7 个能力**（ADR-0025 的 D9），`execution_bounds.json` 里冻的 `capabilities` 也随之变成 `cap-2`。原数字保留。
| S47.4 测试 + 回写 | ✅ | `pytest cli/tests` 713 → **723 项** |

**验收证据**：`pytest cli/tests` **723 项全绿**；旧切片与真机验收不受影响。

**重新生成语料仍是外科式的**：SHA256 逐一比对，**23 个既有 fixture 逐字节不变**，只有新 fixture 与
`index.json` 变化。

**随包数据没有漂移**：8 个能力的 `scope` 全部落在 binding 词汇内（实际用到 `machine`/`project`/`session`），
五套 `--scope` 取值与各自词汇一致。与 §40/§42/§43/§44/§45/§46 一样如实记下"本轮没抓到真问题"。

**守卫确实会红（两个方向都验过）**：
* 把 `capabilities.json` 的 `scope` 写成裸字符串或 `["projet"]` → 加载期立刻抛
  `declares scope as something other than a list of names` / `declares unknown scopes: projet`；
* 把 `tool pin --scope` 的字面量补上 `system` → 守卫第十五组报
  `Extra items in the left set: 'system'`。改回即绿。

**一处如实记录的开放问题（F2，需要裁决）**：冻结清单声明的 `scope` **今天只是声明**——
`check_admission` 的三个条件里没有它，路由决策也不读它，它只出现在 `capability list` 的输出里。
所以"未声明 `machine` 的能力能否被 machine 级绑定"目前**没有答案**。本轮**不**擅自实现强制：
那会改变准入与路由的语义。请裁决是"实现强制（那么判据是什么、违反时给什么 reason code）"还是
"在清单里去掉 scope 或明说它只是文档性声明"。

**边界**：守卫覆盖**词汇与取值集合**，不覆盖"某个命令该不该提供某个 scope"（那是设计），也不覆盖
三套词汇**语义**上的区分是否合理（决策 5）。

### 47.6 实施顺序

1. 先把三套词汇与五套 `--scope` 取值**都打出来**（不预设它们一致），确认重叠与子集。
2. 确认 `capabilities.json` 的 `scope` 目前有无消费者——**先看再改**，避免把"声明"当成"已实现"。
3. 加加载期校验（含裸字符串这条）；`test_l1_boundary.py` 补正例/负例/危险方向。
4. 写守卫第十五组（两处 schema 绑定 + 五套取值 + 重叠）；加 `frozen_capabilities.json`。
5. 两个方向各改一处验证会红，再回写本节与 AGENTS 计数。

---

## 48. 阶段计划：状态文档不得否认已交付的东西（守卫第十六组）

> **本节是实现计划。** §40–§47 查的都是"两份声明是否一致"。这一节查的是更基本的一件事：
> **一份自称描述"当前状态"的文档，说了假话。** 本轮发现 `docs/AIROOT-v0.3-规范审查报告.md`
> 的追加状态节写着"根级 `SKILL.md` **仍未创建**，因此本仓库仍不是可安装 Skill"——而 `SKILL.md`
> 早在 §22 的 Skill 适配层阶段就交付了。完成情况回写 §48.5。

### 48.1 本轮发现的真实缺陷

| # | 位置 | 缺陷 | 为什么有害 |
|---|---|---|---|
| F1 | 审查报告的追加状态节 | 写着"根级 `SKILL.md` 仍未创建，因此本仓库不是可安装 Skill" | **陈述为假**。技能入口已交付二十余轮；读这段的 agent 会得出"本仓库不是可安装 Skill"的结论 |
| F2 | 同一节 | 测试计数写作 **180 项**（实际早已数百） | 与 F1 同源：整节是 P1 最小 Core 完成时写的，之后再没更新 |
| F3 | 同一节 | **完全没有**管家域（数据根 / reference / adopt / env persist / 删除分级 / 能力边界 / desired / rebuild / 来源清单）与 **`search` 协议面 + crawl 索引** | 一节自称"记录实现结果、是当前状态"，却漏掉了此后绝大部分交付 |
| F4 | 前文（审查当时）两处 | "18 个 JSON Schema"（现为 19） | 这是**历史记录**，本来就该是 18；问题是它没有任何标注，读者无从分辨 |

F1 是重点：**它不是"过时"，是"假"**。§40–§47 的守卫都在查一致性，没有一条读散文里的**事实性否定**。

### 48.2 交付物

| # | 内容 |
|---|---|
| S48.1 | 重写追加状态节：补上管家域与 `search` 面、修正计数、把 `SKILL.md` 那条改成"已交付"，并逐条列出**当前**仍缺的项 |
| S48.2 | 前文两处 18 标注为"审查时的数字；现为 19 个"，指向状态节（历史记录保留，但不再可能被误读为现状） |
| S48.3 | 守卫第十六组（1 项）：**自称描述当前状态的那一节**不得否认已交付的工件；另把审查报告纳入**测试计数**与 **Schema 计数**两项既有守卫 |
| S48.4 | 测试 + 回写 |

### 48.3 需要做出的决策（含理由）

1. **守卫只覆盖"追加的状态节"，不覆盖前文的审查快照**。前文里写的"当前 Skill scaffold 目录还没有
   根级 `SKILL.md`"在**当时是真的**——它在 §「仍需在实现前冻结的 P1 项」里是一条**要求**。用一个
   守卫去要求历史快照跟上今天，等于要求改写历史记录。**边界就是这条**：那节自称当前状态，
   所以它必须当前；前文自称审查当时，所以它按当时算。
2. **只守卫可判定的事实性否定**。条目写成"工件 + 一句话"，测试先断言**工件存在**，再断言那句话
   **不在状态节里**。散文里其余无法判定的说法不守卫——目的不是审查措辞，而是阻止"已交付的工件
   被说成不存在"。
3. **计数交给计数守卫，不重复实现**。状态节的计数由既有的两项守卫覆盖：
   测试计数用**精确相等**（它自称当前状态，不像草案那样是逐阶段历史，所以"不超过"不够），
   Schema 计数沿用既有规则（该行必须同时出现当前值，或被标注取代）。
4. **顺带把审查报告纳入这两项守卫的文档清单**。此前它不在 `(AGENTS, SCHEMA_README, DRAFT)` 里，
   所以它上面两个计数从来没被检查过——F2 就是这么活了二十多轮。

### 48.4 明确不做

**不**改写前文的审查结论或历史快照的计数（只在两处加"审查时"标注）；**不**为散文写通用的事实
校验器（做不到，也不该做）；**不**把审查报告升格为契约层文档（它是审查/状态记录，不是契约）；
**不**在状态节里重复 `AGENTS.md` 的完整交付清单（交叉引用即可，避免两处再次漂移）。

### 48.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S48.1 重写状态节 | ✅ | 交付表补上管家域步骤 1–9/11–12、`search` 协议面与 crawl 索引、Skill 适配层；计数改为当前值；"仍缺"逐条按当前状态列出 |
| S48.2 前文 18 标注 | ✅ | 两处改为"审查时的数字；现为 19 个" |
| S48.3 守卫第十六组 | ✅ | `test_l0_consistency.py` 48 → **49 项**；审查报告纳入测试计数（精确相等）与 Schema 计数两项守卫 |
| S48.4 测试 + 回写 | ✅ | `pytest cli/tests` 723 → **724 项** |

**验收证据**：`pytest cli/tests` **724 项全绿**；旧切片与真机验收不受影响（本阶段只改文档与测试）。

**守卫确实会红（两个方向都验过）**：
* 把"当前交付仍不是可安装 Skill"这一句塞回状态节 →
  `test_the_review_status_section_does_not_deny_a_delivered_artifact` 立刻失败并指出工件名；
* 状态节的计数与当前不符 → 测试计数守卫失败（新增的精确相等断言）。改回即绿。

**边界（照决策 1）**：守卫**不**覆盖前文快照。前文第 121 行"当前 Skill scaffold 目录还没有根级
`SKILL.md`"与第 162 行"当前交付仍不是可安装 Skill"都**保持原样**——那是审查当时的真实记录。
这条边界写进了守卫的注释，免得下一个人以为漏了。

**一处仍在的同类风险（如实记录）**：`docs/AIROOT-v0.3-验证与测试方案.md` 的 79 个 P-/S-/T-/C-
场景编号里，只有约 30 个在测试里被点名。**这不等同于"另外 49 个没被测"**（测试可能覆盖了行为而
没有引用编号），所以本轮**不**据此下结论。它需要的是给每个编号一个明确处置（已被引用 / 待实现，
并写明理由），那是一件独立的事。**（已由 §49 关闭**：实测两份文档合计 **107** 个编号、**39** 个被点名、
**68** 个没有，每一个 `uncited` 编号都有一条带结构性理由的记录，并进验收语料。上面的"79 / 约 30"
是**当轮**从验证方案单张表读出的数，本轮不回头改写它。）

### 48.6 实施顺序

1. 逐个读审查报告的追加节，把每条陈述**拿去核对**（不预设它还成立）——F1/F2/F3 都是这么出来的。
2. 分清哪些是"历史快照"、哪些是"当前状态"；只改后者，前者加"审查时"标注。
3. 重写状态节；顺带确认前文没有别的会被误读为现状的计数。
4. 加守卫第十六组 + 把该文档纳入两项计数守卫；验红、改回。
5. 回写本节、AGENTS 计数与仓库地图。


## 49. 阶段计划：107 个验收场景编号要有一份台账（守卫第十七组）

> **本节是实现计划。** §48.5 末尾如实记下了一处同类风险：验证方案的 79 个场景编号里只有约 30 个被测试点名，
> 而"没被点名"**不等于**"没被测"，所以 §48 **不**据此下结论——它需要的是"给每个编号一个明确处置"，
> 那是一件独立的事。**这一节就是那件独立的事。**
>
> 做法不是去补测试，而是把"编号 ↔ 证据"这件事从**读者的记忆**变成**可核对的台账**：谁定义了它、
> 有没有测试点名它、如果没有，缺的是什么。这样"68 个没被点名"从一个模糊的担忧，变成 68 条各有理由的记录。
> 完成情况回写 §49.6。

### 49.1 本轮实测到的事实

用一次性只读脚本（`docs/*.md` 全量扫表行 + `cli/tests/*.py` 全量扫引用）实测：

| 事实 | 数值 |
|---|---|
| 定义场景编号的文档 | **2** 份（验证与测试方案、本契约草案）；`docs/` 下没有第三处（按 `^\s*\|\s*[A-Z]{1,3}-\d{3}` 扫过全部 `*.md`） |
| 编号总数 | **107**：`P-001..P-021`(21) + `S-001..S-036`(36) + `T-001..T-017`(17) + `C-001..C-033`(33) |
| 被测试点名的编号 | **39** |
| 没有任何测试点名的编号 | **68** |
| 测试引用了**不存在**的编号 | **0**（这条是好消息：测试从没编造过编号） |
| 同一个编号被定义两次 | `S-014`（验证方案 149 行、本草案 302 行） |
| 两个编号定义同一个场景 | `S-010` 与 `S-015` |

引用语法有**五种**范围写法，会真实影响统计：`…`（`S-032…S-036`、`C-031…C-033`）、`..`
（`P-004..P-008`）、`...`、`~`、`至`；再叠加斜杠串（`T-002/T-006/…`）与单点引用。
**本轮的第一次探测就栽在这里**：它只认四种写法、漏了 `..`，于是把 `P-005/P-006/P-007` 误判为"没被点名"，
引用数少算 3 个（36 而不是 39）。这条记录进决策 5——解析规则本身必须自测。

### 49.2 本轮发现的四个缺口

| # | 位置 | 缺口 | 为什么有害 |
|---|---|---|---|
| F1 | 全局 | **没有"完整验收清单"**。Rust 版要逐字节复现的验收面 = 两张表的**并集**，而这个并集只存在于读者脑子里；没有任何测试知道"107"这个数 | ADR-0001 的移植验收面不完整，且缺哪一块无人能回答 |
| F2 | `S-014` 两处定义 | 两处措辞不同（验证方案写全了 `found/usable/candidates/BROKEN(3)`，草案只写降级结论）但**今天互不矛盾**（都已含 ADR-0006 的改写） | 不是"假话"，是**将来只改一处**：这正是 §46 那类"同一目录两份声明、没有任何一条把两处互相比对"的翻版 |
| F3 | `S-010` / `S-015` | **同一个场景登记了两次**（同为"project manifest 请求 machine scope"，期望也相同，只是措辞不同）：`test_l1_plan_routing.py` 引用 S-010，没有测试引用 S-015 | 引用它时选哪个编号是任意的；"107 个场景"实际是 **106** 个。不是笔误，是编号时没有回头比对 |
| F4 | 本草案 `P-021` | 写着"`data-root add` 指向 UNC / reparse point / **另一个卷** → 拒绝，退出码 8"。前两半**对**（`UNC_NOT_ALLOWED` / `REPARSE_POINT_REJECTED`，都退出码 8），**第三个半是假的** | 跨卷数据根是**明确允许**的：`cli.py:224` 的注释写着数据根的卷身份"**explicitly not** required to match" CLI root 的卷。这条期望是跨卷决策之前的遗留，且与用户明确提出的要求相反（"我查环境不应该被跨卷拒绝吧"） |

F4 与 §48 的 F1 是**同一类错误落在另一份文档上**：§48 修的是"状态文档否认已交付的东西"，
F4 是"验收文档要求一个已被明确否决的行为"。**没有任何测试能引用 P-021**——引用它就得断言一次不该发生的拒绝。

### 49.3 交付物

| # | 内容 |
|---|---|
| S49.1 | `cli/tests/scenario_ledger.py`：纯函数解析器 + 台账构建。定义只从**表行首格**抽（散文提及不算，例如验证方案 130 行"P-003 和 P-013 是防止…"不能变成定义）；引用支持五种范围写法与斜杠串，**只扫 `test_*.py`**，且**不**把 Windows SID（`S-1-5-21-1000`）当编号 |
| S49.2 | `cli/tests/fixtures/golden/scenario_ledger.json`：**107 条**的冻结声明（`id`/`family`/`status`/`cited_by`/`defined_in`/`duplicate_of`/`blocked_by`/`evidence`/`superseded`/`note`）+ 一份自报的 `summary`——**第四个**进验收语料的**契约目录**（前三个见 §45/§46/§47） |
| S49.3 | 守卫第十七组（**8 项**）：冻结台账与文档**双向**相等；冻结台账的 `status`/`cited_by` 与派生的引用一致；冻结台账与 `build_ledger` **逐项相等**（防陈旧）；`blocked_by` 在声明过的词汇内、`duplicate_of` 指向存在且自身不重复的编号、`blocked_by=none` 的**证据指针**（文件+token）都真实存在；两处定义的行都写了"同时更新"；被取代的行写了"已取代"；解析规则自测（SID 不是编号、散文不是定义、五种范围、只认表行）；**审计文件自己不得看起来引用了任何场景**；语料数 / 场景数 / 审计检查数三项计数一致 |
| S49.4 | 四处文档标注（**不删行**）：验证方案 `S-014` 行标注"两处必须同时更新"、`S-015` 行标注"与 `S-010` 是同一场景（重复登记）"；本草案 `S-014` 行同样标注、`P-021` 行标注"另一个卷**已取代**（跨卷已改为允许）" |
| S49.5 | 测试 + 回写 |

### 49.4 需要做出的决策（含理由）

1. **`status` 只允许 `evidenced` / `uncited` 两个值，不允许叫 `covered` 的第三个值。**
   `covered` 会是一个**守卫无法判定**的覆盖声明。本轮的产出是把"没被点名"变成一条**有理由的记录**，
   不是把 68 个编号说成"没被测"、更不是把它们说成"被测了"。
2. **`uncited` 条目必须带 `blocked_by`，取值来自声明过的词汇**：`p2-protected-state`（ACL / UAC / broker /
   machine PATH / machine 级变量）、`p4-real-backend`(真实下载·解压·stage·commit·磁盘·锁)、
   `p3-native-indexer`（USN 常驻索引器）、`undesigned`（该场景依赖的命令尚不存在或语义未定：
   `root relocate`、`external import`、`.ai/tooling.json` 的写入）、`none`（什么都不缺，行为由某个测试覆盖、
   只是那个测试没有引用编号——此时 `note` **必须**写出文件名，且守卫会检查该文件真实存在）。
   **`note` 是作者写的声明，守卫只做结构检查**（非空；`blocked_by=none` 时文件名必须存在）。
   台账的价值是让这些判断**可被复核**，而不是让守卫假装能判定覆盖。
3. **重复编号不删行、只标注**，并在台账里用 `duplicate_of` 指向规范编号。删掉 `S-015` 会改变"107"这个数
   并抹掉一次真实的编号事故；标注既保留事实，又让两个方向都能被守卫检查。规范方向是
   **"被测试引用的那个赢"**（`S-010` 赢，因为 `test_l1_plan_routing.py` 引用它）。
4. **`cited_by` 进 fixture 并要求精确相等。** 给一个测试加一行 `# S-011` 注释会让守卫变红，直到台账被有意重生。
   这正是想要的：**"编号从待办变成有证据"应当是一次被记录的动作**，而不是悄悄发生的。副作用是改测试注释
   也要重生语料——与"改对外 JSON 就要重生 golden"是同一条既有约定。
5. **解析规则本身要自测，且自测要用真实踩到的坑。** 第一次探测少算 3 个引用（漏了 `..`）、
   `S-1-5-21-1000` 长得像编号、验证方案 130 行的散文提及长得像定义——这三条都要有合成输入演练。
   这与 §40 的"三条切片规则各自都是被假阳性教出来的"同一做法。
6. **守卫只覆盖"定义 + 引用"，不覆盖期望文本。** 期望文本是散文，判它对不对需要人；
   F4 是我读出来的，不是守卫查出来的，所以它按 §48 的做法**人工核对 + 标注**，并**不**假装守卫能防下一次。

### 49.5 明确不做

**不**补齐那 68 个编号的测试（多数需要 P2/P4，或语义未定义——补测试是它们各自阶段的事）；
**不**把"被点名"当覆盖判据，也**不**把"没点名"当未测判据；**不**改任何编号的**含义**
（只加重复/取代标注）；**不**把台账做成需要手工维护的第二份清单——它是**从文档与测试推导**、
由守卫强制同步的投影，`id`/`family`/`status`/`cited_by` 全部可再生；**不**顺手统一两份文档的
编号风格或重排编号（那会一次性作废所有既有引用）。

### 49.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S49.1 解析器与处置表 | ✅ | `cli/tests/scenario_ledger.py`：解析器 + 68 条 `uncited` 编号的逐条处置 |
| S49.2 台账进语料 | ✅ | `scenario_ledger.json`：107 条 + `summary`；重新生成语料时**24 个既有 fixture 逐字节不变** |
| S49.3 守卫第十七组 | ✅ | `test_l0_consistency.py` 49 → **57 项** |
| S49.4 文档标注 | ✅ | 验证方案 `S-015` / `P-021` 两行；本草案 `S-014` 一行（都**没有删行**） |
| S49.5 测试 + 回写 | ✅ | `pytest cli/tests` 724 → **732 项** |

**台账的实测结论**：107 个编号 = **39 个有证据**（至少一个测试文件点名）+ **68 个没有**。68 个的处置是
**10 个** `p2-protected-state`、**7 个** `p4-real-backend`、**8 个** `undesigned`、**1 个**
`unchecked-invariant`、**41 个** `blocked_by=none`（有一个测试覆盖同一行为、只是那个测试没有引用编号，
每条都带一个可解析的 `文件名#token` 证据指针）、**1 个** 重复登记。第 5 个词汇值
`unchecked-invariant` 是被数据逼出来的：`S-012` 的卷身份检查**代码里已经存在**（`doctor.py`），
不属于"缺能力"，也不属于"没设计"——它是**测试债**，词汇必须能如实说出这件事。

**守卫确实会红（四个方向都验过）**：

* 文档里加一行 `P-099` → `test_the_frozen_ledger_accounts_for_every_defined_scenario` 报
  "a defined scenario the frozen ledger does not account for: P-099"；
* 审计文件里写一个编号字面量 → `test_the_audit_file_does_not_appear_to_cite_a_scenario` 报出它；
* 把草案 `S-014` 行的"同时更新"删掉 → `test_a_scenario_defined_twice_says_so_in_both_rows` 报出该行；
* 改坏一个证据 token / 改一个 `status` → 结构性检查与语料逐项相等各自报出。

**两个自指陷阱（都已做成守卫，因为它们都会静默给出错误答案）**：

1. 台账模块自己住在 `cli/tests/` 且**点名全部 107 个编号**，于是第一版把它自己算成证据，
   报出 **107/107 全有证据**。修法是**只扫 `test_*.py`**。
2. 审计文件自己因为"语法示例里写了编号"被算成某个编号的证据（改一行断言就改一份证据）。
   修法是**在运行时拼出编号**（代码与散文都算），并新增守卫**禁止本文件看起来引用了任何场景**。

**一处必须记下的守卫缺陷（本轮自己抓到的）**：第一版的两项守卫把"推导结果"和"文档"相比——
而 `build_ledger` **本来就是遍历文档建的**，所以那是**恒真式**，永远不可能红。造变更测试时才暴露：
往文档里加一个编号，守卫照样绿。修法是**一律拿冻结台账（fixture）当被检对象**，推导只当基准；
两个比较逻辑同时抽成纯函数并用合成输入演练"必须报错"。**教训与 §45–§48 一致**：
守卫要检查的是**能过期的那个东西**（冻结工件），不是**由源重建出来的那个东西**。

**两处顺带修掉的文档缺陷**（本轮实测发现，不属 §45–§48 任何一条守卫的覆盖面）：

* `P-021` 的"另一个卷 → 拒绝，退出码 8"是**假话**：跨卷数据根**明确允许**（`cli.py` 注释写着
  "explicitly **not** required to match"，`test_data_root_on_another_volume_is_accepted` 直接断言），
  只有 UNC / reparse point 仍拒绝。按 §48 的做法**标注取代、不删行**。
* **27 个 golden fixture** 是错的（实际 25 个），审查报告还写着 **48 项审计**（实际 49）。
  两者都是**从未被任何守卫检查过**的计数 → 并入守卫，并且审计检查数**由该文件自身源码推导**
  （函数个数 + 参数化展开），不写死常量。

### 49.7 实施顺序

1. 写解析器（`scenario_ledger.py`），先用一次性脚本核对"107 / 39 / 68 / 0"四个数与文档现状一致。
2. 按决策 2 逐条给 68 个 `uncited` 编号定 `blocked_by`（这一条要一条条读，不能批量猜）。
3. 生成 fixture，写守卫第十七组，**验红**（三个方向：文档多一个编号 / 引用与 status 不符 /
   `blocked_by=none` 指向不存在的文件）。
4. 加两处文档标注（`S-015`、`P-021`、`S-014`）。
5. 重生 golden、跑全量测试与旧切片、回写本节 + AGENTS 计数与仓库地图。


## 50. 阶段计划：决定"拒绝"的数值必须进验收语料（守卫第十八组）

> **本节是实现计划。** §46.4 在明确不做的清单里留下了两个名字——`CONFIRMATION_OPTIONS` 与
> `policy/search-policy.json` 的上限——并写着"**已知的同类候选项，列在这里以免被当成遗漏**"。
> 这一节把那份欠账还上，并且**按类还**：一个数字只要变了就会让 AIROOT 的**拒绝行为**改变，
> 它就属于这份语料。
>
> 为什么值得单独做：ADR-0001 说 golden 语料是语言中立的验收面。可一个只读语料 + Schema 的
> Rust 版，**无从知道** shipped 的 `limit` 上限是 2000、索引在 `cache/search/index.db`、
> 三选一是哪三个词、`MAX_ROOTS` 是 64、`https_artifact` 封顶 2 GiB。这些数今天只存在于
> **代码常量**与**随包策略文件**里——而策略文件是**源码**，不是验收面。完成情况回写 §50.6。

### 50.1 本轮实测到的事实

| 事实 | 细节 |
|---|---|
| 语料里的 fixture 数 | **26**，其中**没有一个**带"决定拒绝"的数值 |
| 这类数值的存放处 | **三处**：`policy/*.json`（5 个文件）、`caps/*.py` 的常量、`caps/*.py` 里**当策略缺项时的回退上限** |
| 搜索上限其实是**三个数** | 以 `limit` 为例：**策略值** `2000`（`search-policy.json`）、**代码回退上限** `10000`（`build_request` 里 `rules.limit("limit", 10000)` 的那个 fallback）、**schema 最小值** `1`。三者行为上无法区分，而**只有第一个**在数据文件里 |
| 冻结目录的 revision | `wl-3`（白名单）、`src-1`（来源）、`sp-1`（选择）、`cap-1`（能力）、`srch-4`（搜索）——全部只在各自文件里 |

**§82 已改判**：这一行里的 `wl-3` 与 `cap-1` 是 §49 当时的事实；今天两者是 **`wl-4`** 与 **`cap-2`**（ADR-0025 的 D9 移除了 `media_probe`）。`src-1` / `sp-1` / `srch-4` 不变。**这次改判恰好是"一个数值同时写在三处"的又一次实测**：文件、语料、以及这句话本身。
| §46.4 已经点名 | `CONFIRMATION_OPTIONS` 与 `search-policy.json` 的上限 |

`MAX_CURSOR_LENGTH`、`MAX_ROOTS`、`MAX_ARTIFACT_BYTES`、`DEFAULT_MAX_BYTES`、`MAX_HEADER_BYTES`、
`MAX_RESOURCE_SECTION_BYTES`、`DEFAULT_INDEX_PATH`、`discovery-whitelist.json` 的 `limits`
（`max_depth`/`max_relative_depth`/`max_files_per_object`/`max_objects`/`max_versions_per_object`/
`max_active_markers`）、`selection` 的 `PRECEDENCES`——都是同一类，都不在语料里。

### 50.2 本轮发现的缺口

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | **没有一份"界限清单"**。决定拒绝的数值散在三处，没有任何一份工件把它们列全 | Rust 版把每个响应都复现得一模一样，却可能在"2001 是被拒绝还是被接受"上给出不同答案，而**没有 fixture 会发现**（与 §45 的 F2 同构） |
| F2 | **搜索上限的"第二个数"从未被记录**：策略缺项时用的是代码里的回退上限（10000/120000/3600000） | 策略文件被删（代码明确允许这种降级："search must still work on a tree where someone deleted the policy"）时，行为由**只有代码知道**的数值决定；换一份实现就会不一致 |
| F3 | §46.4 的两个候选项欠了整整一轮 | 已经写进"以免被当成遗漏"，但"记下来"不等于"做了"；不还这份账，它就是一条永久待办 |

### 50.3 交付物

| # | 内容 |
|---|---|
| S50.1 | 新 golden fixture `execution_bounds.json`：**第六个契约目录**。分块记全三处来源——`search_request`（`max_roots`/`max_cursor_length`/**策略上限**/**代码回退上限**/`defaults`）、`crawl`、`index`、`discovery_scan`、`confirmations`、`selection`、`artifact_bounds`、`inspection_bounds`、`policy_revisions` |
| S50.2 | 守卫第十八组：语料与代码/策略**逐块精确相等**（失败信息指出是哪一块）；**危险方向**——语料写的界必须是**真的在执行的**界（在界内接受、越界拒绝），而不是从同一个地方读出来的常量（否则就是 §49 抓到的恒真式）；`EXERCISED` / `RECORDED_ONLY` 两份清单**划分**全部块，`RECORDED_ONLY` 的每条写明为什么没做执行检查 |
| S50.3 | 登记孤儿 fixture（§45 起就有的守卫）+ 语料计数 26 → **27**（AGENTS 与审查报告两处） |
| S50.4 | 测试 + 回写 |

### 50.4 需要做出的决策（含理由）

1. **按类还，不按名字还。** §46.4 点了两个名，但它们不是两个特例，是一类里的两个样本。只补那两个，
   下一个数字（`MAX_ROOTS`？）会以完全相同的方式缺失，而且**没人能分辨那是遗漏还是决定**。
   类的定义写死为：**改了它，某个拒绝会改**。
2. **每块都记"数值 + 它的来源"**（`policy` / `code_constant` / `code_fallback`），不记"一个数"。
   搜索上限有三个数，只记一个就是把 F2 留在原地。语料必须能回答"策略文件被删之后行为是什么"。
3. **危险方向的检查优先于相等检查。** 相等检查只能防"语料过期"；防不住"语料写了一个代码根本不执行的界"。
   所以每条能做执行检查的界，都要真的走一次"界内通过、越界拒绝"，并且**用 fixture 里的数字**去构造那次请求。
   这与 §49 的教训是同一条：**检查能过期的那个东西，并且检查它真的在生效**。
4. **不做执行检查的，必须在语料里写明理由。** `crawl.max_records`（250000 条记录的树）、
   索引 `max_records`、artifact 字节上限（2 GiB/8 GiB）没法在测试里低成本触发。
   它们进 `RECORDED_ONLY` 并逐条写原因——**"没检查"和"假装检查了"必须长得不一样**。
5. **策略文件本身不进语料**，语料记的是**解析后的数值**。策略文件是源码（有注释、有 revision、
   有排版），把它复制进语料只会制造第二份会漂移的副本；而"解析后的数值"才是行为。

### 50.5 明确不做

**不**改任何上限的数值（本轮只记录与检查）；**不**把回退上限搬进策略文件（那是语义变更，属另一件事）；
**不**给每个上限都写端到端用例（见决策 4）；**不**把策略文件的注释/排版搬进语料；
**不**新增任何契约字段或 Schema。

### 50.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S50.1 界限语料 | ✅ | `cli/tests/fixtures/golden/execution_bounds.json`：9 个块 + `provenance`（逐块写来源） |
| S50.2 守卫第十八组 | ✅ | `test_l0_consistency.py` 57 → **62 项**（5 项：语料逐项相等、划分与来源、搜索界**真的在执行**、三选一真的被返回、policy revision 可报告） |
| S50.3 孤儿登记 + 计数 | ✅ | `execution_bounds` 进孤儿守卫已知集合；语料 26 → **27**（AGENTS + 审查报告两处） |
| S50.4 测试 + 回写 | ✅ | `pytest cli/tests` 732 → **737 项** |

**语料的内容**（9 个块）：`search_request`（`max_roots` 64 / `max_cursor_length` 4096 / **策略上限**
2000·15000·300000 / **代码回退上限** 10000·120000·3600000 / **schema 最小值** 1·1·0 / `defaults`）、
`crawl`、`index`（含 `path=cache/search/index.db` 与 `native_candidate` 身份）、`discovery_scan`（6 个白名单
扫描上限）、`confirmations`（三选一）、`selection`（`PRECEDENCES` + shipped precedence）、
`artifact_bounds`（2 GiB / 8 GiB）、`inspection_bounds`（64 KiB / 8 MiB）、`policy_revisions`（`wl-3` /
`src-1` / `sp-1` / `cap-1` / `srch-4`）。

**§82 已改判**：`policy_revisions` 这一块里的 `wl-3` / `cap-1` 今天读作 **`wl-4` / `cap-2`**（ADR-0025 的 D9）。上面那句是 §49 当时语料的内容，保留原样。

**"承重项是执行而不是相等"这句话本轮被验红证明了**：把 `build_request` 里的
`published = rules.limit(name, ceiling)` 改成 `published = ceiling`（让**代码不再理会策略**）之后，
语料与策略**仍然完全一致**（相等断言照样绿），但执行断言报 `DID NOT RAISE`——
**2001 被接受了**。这正是恒真式守卫会漏掉的那一类：它只证明语料与代码同源，不证明这个界真的在生效。

**守卫确实会红（三个方向都验过）**：

* `search-policy.json` 的 `limit` 2000 → 2500 且不重生语料 → 逐项相等断言报"语料已陈旧"并打出差异；
* `MAX_ROOTS` 64 → 65 且不重生语料 → 同上（证明**代码常量**也在语料里）；
* 代码不再读策略 → 执行断言报 `DID NOT RAISE`（**只有这一条能发现**）。

**一处如实记录的取舍**：6 个块进 `RECORDED_ONLY` 并逐条写了理由（25 万条记录的树、2 GiB artifact、
合成 PE 头等）。守卫要求 `EXERCISED` 与 `RECORDED_ONLY` **划分**全部块、且 `RECORDED_ONLY` 的每条理由
非空——所以"没做执行检查"是一个**被记录的判断**，而不是一片沉默。

**两处如实记录的边界**：

1. **策略文件本身不进语料**，进的是**解析后的数值**。策略文件是源码（注释、revision、排版），复制它
   只会得到第二份会漂移的副本；而"策略文件被删之后行为是什么"由 `code_fallback_ceilings` 回答——
   这是本轮实测才发现的那**第三个数**。
2. **本轮没有改任何上限数值**，也没有把回退上限搬进策略文件（那是语义变更，属另一件事，
   已列入 §50.5）。

### 50.7 实施顺序

1. 把三处来源**列全**（策略文件 / 代码常量 / 代码回退上限）——先做这一步，否则语料必漏第三类（F2）。
2. 写 fixture 生成（`golden.py`）+ 逐块相等断言。
3. 写危险方向断言：**能低成本触发的**才写，其余进 `RECORDED_ONLY` 并写理由。
4. 登记孤儿 fixture、把语料计数改成 27（AGENTS + 审查报告）。
5. 重生 golden（SHA256 逐字节比对既有 fixture）、验红、跑全量 + 旧切片 + 真机验收、回写。


## 51. 阶段计划：散文里的退出码要"认得出是哪个码"，agent 面的三选一要进守卫（守卫第十九组）

> **本节是实现计划。** §44 把**散文里的退出码**变成了守卫，并且**故意只认两种显式写法**：
> `` `CODE`(2) `` 与 `` `CODE`，退出码 8 ``。本轮把另外三件事测清楚之后发现：
> **还有第三种写法它不认识**，而 `references/confirmation.md`——那份告诉 agent"该说什么话"的按需参考——
> **整个文件的关键词表没有被任何守卫绑定**。完成情况回写 §51.6。

### 51.1 本轮实测到的事实

| 事实 | 数值 |
|---|---|
| 操作面文档（`AGENTS.md`、`SKILL.md`、`references/*.md`、诊断码表）里出现 `退出码 N` 的次数 | **17** |
| 被守卫第十二组**已知的两种**写法绑定的 | **2** |
| **没有任何守卫**的 | **15** |
| 其中"码紧接 `（退出码 N）`"的第三种写法 | **6** 处（改完写法表后这 6 处被绑定，普查余 **9** 条） |
| `references/confirmation.md` 里"码紧接 `（退出码 N）`"的第三种写法 | 2 处（`SCOPE_UPGRADE_REQUIRES_APPROVAL`、`PRIVILEGE_REQUIRED`） |
| 我第一次写的探测脚本的**假阳性** | **135** 条 |

最后一行是本轮最重要的实测：我第一版探测按"**同一行里有码、也有数字**"配对，于是 AGENTS.md 第 29 行
那段（同时提到十几个码和好几个数字）立刻产出上百条"错误"。**逐条读回去，那 135 条全是假的**
（例如"退出码 4"属于同句里的 `SCOPE_UPGRADE_REQUIRES_APPROVAL`，而脚本抓到的是旁边另一个码；
"退出码 0"属于"索引健康时退出码是 0"，与同段提到的 `SESSION_STATE_STALE` 无关）。
这正是 §44 当年拒绝放宽的理由，**本轮用实测把它又确认了一次**。

### 51.2 本轮发现的三个缺口

| # | 位置 | 缺口 | 为什么有害 |
|---|---|---|---|
| F1 | `_PROSE_EXIT_FORMS` | 只认 `` `CODE`(2) `` 与 `` `CODE`，退出码 8 ``；**不认** `` `CODE`（退出码 N） `` | **实测**：把 `references/confirmation.md` 的 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 **4**）改成 **5**，`pytest` **739 项全绿**——一个错误的退出码在 agent 面存活而无人察觉 |
| F2 | `references/confirmation.md` 的**三选一** | 那个三行代码块（`project-isolated` / `data-root` / `cancel`）没有被任何守卫绑定 | **实测**：把 `data-root` 改成 `data_root`，**739 项全绿**。`SKILL.md` 的确认协议段被 `test_the_confirmation_triple_is_the_frozen_one` 钉住，**同一套词在 reference 里没人管**——而 reference 正是 agent 被要求"按需查阅"的那份 |
| F3 | `references/confirmation.md` 的"当前版本**只读**" | 一句关于**实现**的事实性声明（`.ai/tooling.json` 只有读入口，没有写入口），没有任何守卫 | 与 §48 同类：将来加了写通道，文档会继续说过时的假话。实测：`cli/app/airoot/**` 里只有 `read_tooling_memory` / `tooling_memory_path`，**没有写入口**（所以这句话今天是**真的**） |

### 51.3 交付物

| # | 内容 |
|---|---|
| S51.1 | `_PROSE_EXIT_FORMS` 增加第三种写法（码紧接 `（退出码 N）`），并且**要求紧邻**——不是"同一行" |
| S51.2 | 未绑定声明的**普查**：把"没有被任何显式写法绑定的 `退出码 N` 条数"钉成一个声明过的数字，写清楚它是**普查**而不是"这些声明是错的" |
| S51.3 | `references/confirmation.md` 的三选一按**精确相等**双向绑定（多了、少了、改名了都红） |
| S51.4 | `只读` 声明的**源码普查**：`cli/app/airoot/**` 不得出现写 `.ai/tooling.json` 的入口；将来加写入口必须同时改文档 |
| S51.5 | 测试 + 回写 |

### 51.4 需要做出的决策（含理由）

1. **只扩展"显式标记"的写法表，绝不放宽成"同一行里有码又有数字"。** 理由不是审美：我按那个规则写了一版，
   产出 **135 条假阳性、真的一条没有**（见 51.1）。一个吵闹的守卫会被绕过，一个安静的守卫才有人看。
2. **给不了归属的声明要"数出来"，不要假装能绑定。** 15 条未绑定里有 3 条**旁边根本没有码**
   （码在别处、或那句话讲的是别的码），任何自动归属都是猜。做法与 §50 的 `RECORDED_ONLY` 一致：
   把条数作为一条**被记录的事实**钉住。于是新增一句散文要么被显式写法绑定、要么让普查数字 +1——
   **"又有多少条没人管"从此不是一个沉默**。
3. **三选一按精确相等双向绑定**（与 `SKILL.md` 同一条规则）。不是"包含"：多一个词、少一个词、
   改一个连字符都算漂移——`data_root` 那次实测就是这么溜过去的。
4. **`只读` 声明用源码普查钉住，而不是去实现写入。** 写入属 P2 的人工批准通道（ADR-0004 §12.2、
   `references/confirmation.md` 自己也这么写），本轮**不**改变这个边界；钉住的是"文档说的与代码做的一致"。
   危险方向是"做了却没说"：加写入口的人必须同时改文档。
5. **审计守卫不受"放宽优先"约束**（ADR-0021 决策 3 已写明：把一致性检查加严不是"收紧用户权限"）。
   本轮**没有**放宽任何拒绝行为，也**没有**收紧任何用户可见的权限。

### 51.5 明确不做

**不**给裸 `退出码 N` 猜归属（决策 2）；**不**实现 `.ai/tooling.json` 的写入（属 P2，决策 4）；
**不**把 `references/` 里无法判定的散文（"确认退化成噪音"那段论证）纳入守卫——那是论证，不是事实声明；
**不**顺手统一三种写法的排版（那会一次性改动十几处散文，收益为零）；
**不**因此改任何 reason code 或退出码。

### 51.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S51.1 第三种写法 | ✅ | `_PROSE_EXIT_FORMS` 增加 `CODE`（退出码 N），**要求紧邻**；自测里加了"隔几个词就不算"的反例 |
| S51.2 未绑定普查 | ✅ | `UNBOUND_EXIT_WORDS = 9`；17 条 `退出码 N` 里 **8 条**被显式写法绑定、**9 条**进入普查 |
| S51.3 三选一绑定 | ✅ | `test_the_confirmation_reference_lists_exactly_the_frozen_triple`：按**列表精确相等**双向比对 |
| S51.4 只读普查 | ✅ | `test_the_tooling_memory_is_read_only_in_the_code_and_not_just_in_the_prose`：扫描 `cli/app/airoot/**` 里"tooling 附近的写调用" |
| S51.5 测试 + 回写 | ✅ | `pytest cli/tests` 739 → **742 项**；审计 62 → **65 项**。**注意本阶段的"从"不是上一阶段的"到"**：737 与 739 之间有一次没有阶段小节的提交，见 §83.4 的《跨阶段的计数变化》 |

**守卫确实会红（四个方向都验过）**：

* `confirmation.md` 的 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4）→ **5**：报
  "quoted as exit 5, but the mapping says 4"（**改之前这条是全绿的**，见 51.1）；
* 三选一里 `data-root` → `data_root`：报出两份列表；
* 在 `planner.py` 里注入一个真的写调用（`tooling_memory_path(...).write_text(...)`）：报出该行；
* 在 `confirmation.md` 里加一句裸"以退出码 3 停下"：普查从 9 变 10，报出并告诉你怎么处置。

**一处必须记下的探测失败（本轮最贵的教训）**：我第一版按"**同一行里有码、也有数字**"配对，
得到 **135 条"错误"**——逐条读回去**全是假阳性**（AGENTS.md 第 29 行那段同时提到十几个码和好几个
数字，任何配对都是巧合）。这条实测直接决定了修法的形状：**只扩展显式写法，并且要求码与括号紧邻**；
自测里专门留了一条"隔几个词就不算"的反例，把这个坑钉住。**§44 当年凭直觉拒绝的放宽，本轮用实测
重新确认了一次**——这是本项目第四次由假阳性教出一条切片规则（前三次见 §40）。

**两处如实记录的边界**：

1. **9 条未绑定的数字不是错误，是普查。** 其中 4 条讲的码出现在另一句里（例如"以退出码 4 停下"），
   其余描述的是退出码而不是 reason code。它们**无法自动归属**（135 条假阳性已经证明），所以做法是
   把条数钉住——**"还有多少条没人管"从此是一个被记录的数字**，与 §50 的 `RECORDED_ONLY` 同一手法。
2. **本轮没有实现 `.ai/tooling.json` 的写入。** 守住的是"文档说的与代码做的一致"（危险方向是
   **做了却没说**）；写入通道仍属 P2 的人工批准通道（ADR-0004 §12.2），这一点没有改变。

**与"放宽优先"的关系**：本轮是**审计守卫**，按 ADR-0021 决策 3 明确不受该策略约束——把一致性检查
加严不是"收紧用户权限"。同时本轮**没有**放宽或收紧任何用户可见的拒绝行为。

**一处在实施中发现的措辞约定（已经写进常量注释）**：写这段回写时我引用那三种写法，用了真实数字
（"`` `CODE`，退出码 8 ``"、"`（退出码 4）`"），于是**普查从 9 变成 11**——因为**描述写法本身**的句子
也含 `退出码 N`。那两条不是任何码的声明（`CODE` 是占位符），把它们计入普查只会让一个不说明任何行为的
数字漂移。所以：**描述写法时用占位符 `N`**（`` `CODE`，退出码 N ``），带真实数字的句子才算声明。
照做之后普查回到 **9**，而那个数字现在只统计"讲了某个退出码却没绑定码"的句子。

### 51.7 实施顺序

1. 先量三件事：未绑定声明的条数、第三种写法的出现处、`只读` 声明的真伪——**F1/F2/F3 都是量出来的**。
2. 扩展 `_PROSE_EXIT_FORMS`（紧邻要求）；加未绑定普查守卫。
3. 加 `references/confirmation.md` 的三选一守卫（双向、精确）。
4. 加 `.ai/tooling.json` 只读普查守卫。
5. 验红（三种写法各改一次、三选一改一次、加一个写入口）、跑全量 + 旧切片 + 真机验收、回写。


## 52. 阶段计划：台账里"作者的判断"要能拿去核对——而且已经有一条是错的（守卫第二十组）

> **本节是实现计划。** §49 把 107 个场景编号变成台账，并明确写下一句话：
> **推导出的事实**（编号集合、`status`、`cited_by`）按精确相等双向钉住，而**作者的判断**
> （`blocked_by` / `evidence`）**只做结构检查**——因为"某个行为是否真被测到"不是守卫能判定的。
> 本节做的正是那一句话留下的欠账：**把作者的判断逐条拿去核对**。
> 结果是一条**真的错了**。完成情况回写 §52.6。

### 52.1 本轮实测到的事实

只核对 8 条 `undesigned`（这一类"错"是二元的、可判定的：**行为到底存不存在**），方法是**读代码 + 跑行为**，
不是读自己的注记：

| 编号 | §49 的判断 | 核对结果 |
|---|---|---|
| **`C-021`** | `undesigned` | ❌ **错**：`decide_scope` 第 1 步就查 `.ai/tooling.json`（`memory_choice`），命中即 `confirmation=False`、`origin="memory"`；而且**已有测试**在跑它（`test_recorded_choice_short_circuits_the_question`） |
| `C-022` | `undesigned` | ✅ 对，但注记不准（见 F2） |
| `C-007` | `undesigned` | ✅ 对（没有 `airoot update`） |
| `C-008` | `undesigned` | ✅ 对（`root relocate` 在声明未实现清单里） |
| `C-009` | `undesigned` | ✅ 对（`adopt --import` 未实现，且有测试） |
| `S-011` | `undesigned` | ✅ 对（`reconcile` 在声明未实现清单里） |
| `S-027` | `undesigned` | ✅ 对（没有 `env\runtimes` 视图） |
| `S-031` | `undesigned` | ✅ 对，而且**有机器可读的证人**：`forget` 自己的输出里就有 `project_manifest_check: not_implemented_before_p6` |

> **这张表的编号写在反引号里，是必须的。** 见 §52.2 的 F4：第一版写成裸 `C-007` 这样的首格之后，
> 台账的解析器**把这八行当成了八条新的场景定义**（"首格是编号"就是它当时的定义规则），
> 于是守卫报出"defined in 2 places"这种与真实原因毫不相干的错误。

### 52.2 本轮发现的三个缺口

| # | 缺口 | 为什么有害 |
|---|---|---|
| F1 | **一条判断是错的**：C-021 被记成"依赖尚未设计的能力"，而那个能力**已经实现、已经测过** | 台账是给 Rust 版用的验收索引。一条把"已实现"标成"未设计"的记录，会让移植方**重新发明一个已经存在的行为**——而且它长得像一条经过核对的结论 |
| F2 | **C-022 的注记比事实宽**：写的是"拒绝（只读记忆）"，而实现的是"AIROOT 没有写入口"。**第三方**（Skill/Agent 直接改文件）写它时，没有任何东西会拒绝 | 把"没有通道"说成"会拒绝"，是 §48 那一类"文档比实现说得更满"的老毛病 |
| F3 | **6 条正确的判断里，只有 1 条挂了证人**（S-031 有那个字段）。其余 5 条的"能力不存在"只写在台账的散文里，**没有任何测试会因它被实现而变红** | 声明"未实现"和声明"已覆盖"是同一件事的两面：**没有证人的"未实现"迟早变成假话**（§35 已经用"未实现清单为真"这条守卫处理过同一类问题，但那条只管**命令**，管不到场景） |
| F4 | **本轮的计划表自己被当成了定义表**：§52.1 的八行首格写的是裸编号，而台账的定义规则是"首格是编号" | 守卫报出的是"defined in 2 places…does not mention 同时更新"——**与真实原因毫不相干**。这是 §49 那两个自指陷阱的**第三种变体**：*讨论*场景的表和*定义*场景的表长得一样。修法两处：定义规则收紧成"首格**只有**编号（可带 `（新）`/`改写` 后缀）"，以及在讨论表里用反引号写编号 |

### 52.3 交付物

| # | 内容 |
|---|---|
| S52.1 | 把 C-021 的处置**删掉**，并在**已经证明它的那个测试**里点名 C-021 → `status` 由 `uncited` 变 `evidenced`。这不是"改注记"，是**修正测量** |
| S52.2 | C-022 保留 `undesigned`，但注记改成**准确**的（没有写入口 ≠ 会拒绝；并说明"要实现拒绝，前提是先有一个会写它的通道"） |
| S52.3 | 台账新增 `witness`：`undesigned` 条目要么给出**可解析的证人**（`<文件>#<token>`，与 `evidence` 同一套解析规则），要么给出**写明理由的 `no_witness_reason`**。证人的定义写死为：**它会在"缺失的能力出现"或"缺失不再为真"时变红** |
| S52.4 | 守卫第二十组（1 项）：每条 `undesigned` 都有证人**或**写明理由；证人必须真的解析得到（文件存在 + token 存在）；两者不能同时缺、也不能同时给 |
| S52.5 | 测试 + 回写 |

### 52.4 需要做出的决策（含理由）

1. **只核对 `undesigned` 的 8 条，不假装核对了另外 41 条 `blocked_by=none`。** 前者"错"是二元的（行为在不在），
   后者"错"是判断性的（那个测试算不算覆盖）——一次做一件能做实的事。**本轮如实记下**：41 条 `none`
   的 `evidence` 指针只经过结构检查（文件与 token 存在），**没有**逐条复核它是否真的支撑那条期望。
2. **证人必须是"会变红的那个东西"，不是"相关的那个东西"。** 定义：把缺失的能力实现出来，或者让"缺失"不再为真，
   证人就必须失败。按这条定义：`reconcile` / `root relocate` 的证人是**既有的**"声明未实现清单仍为真"守卫；
   `adopt --import` 的证人是它自己的测试；S-031 的证人是断言那个字段的测试；C-022 的证人是 §51 的只读普查
   （**加了写入口它就会红**）。
3. **没有证人的就写明"没有"，不要造一个假的。** C-007（没有 `update` 命令）与 S-027（没有 `env\runtimes` 视图）
   **都没有**任何测试断言其缺失。本轮的选项是"顺手加两条断言"或"写明缺什么"——选后者，因为**为了让台账好看
   而新写的断言，正是台账要防的那种自证**；而 `no_witness_reason` 会把"这条没人看着"变成一条**被记录的事实**
   （与 §50 的 `RECORDED_ONLY`、§51 的普查同一手法）。
4. **C-021 的修法是"点名"，不是"改注释"。** 台账里 `status`（**测出来的**）与 `blocked_by`（**作者判断的**）
   是两件事；C-021 的问题不是判断写错了，而是**测量没做到位**——已有测试在跑这个行为却没引用编号。
   所以修法是在那个测试里加上 `C-021`，让 `status` 自己变。
5. **不改任何行为。** 本轮**没有**实现 `reconcile` / `update` / `env\runtimes`，也**没有**删除或放宽任何拒绝。
   它只修台账与注记，外加一条守卫。因此与 ADR-0021 的"放宽优先"无关——**没有任何用户可见行为发生变化**。

### 52.5 明确不做

**不**逐条复核 41 条 `blocked_by=none` 的证据是否真支撑（决策 1，已如实记录）；**不**为 C-007/S-027 新写"缺失断言"
（决策 3）；**不**实现 C-021 之外的任何 `undesigned` 行为；**不**改 `status` 的语义（它仍然只是"有没有测试点名"）；
**不**把证人当覆盖判据（证人证明的是**缺失**，不是覆盖）。

### 52.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S52.1 C-021 修正测量 | ✅ | `test_l1_planner.py` 的既有测试点名 `C-021`；错处置删掉；`status` 由 `uncited` → **`evidenced`**（39 → **40**） |
| S52.2 C-022 注记改准 | ✅ | 保留 `undesigned`，注记写明"实现的是没有写入口，**不是**会拒绝写入" |
| S52.3 `witness` / `no_witness_reason` | ✅ | 8 条里 **6 条**给了证人（`reconcile`/`root relocate` 用既有的"声明未实现仍为真"守卫、`adopt --import` 自己的测试、S-031 的字段测试、C-022 用 §51 的只读普查），**2 条**写明给不出（C-007、S-027） |
| S52.4 守卫第二十组 | ✅ | `test_every_undesigned_scenario_names_a_witness_or_says_why_it_cannot`（审计 65 → **66 项**） |
| S52.5 测试 + 回写 | ✅ | `pytest cli/tests` 742 → **743 项** |

**守卫确实会红（六个方向都验过）**：

* 摘掉一条 `undesigned` 的证人 → "C-009: undesigned with neither a witness nor a reason"；
* 把证人指向不存在的文件 → "S-031: witness names a test file that does not exist"；
* 把 §52.1 的表头改成 `| ID |` 且某一格写裸编号 → "defined in 2 places"（**证明表头那条锁是承重的**）；
* 只改表头（编号仍在反引号里）→ **全绿**：两把锁各自独立（如实记录）；
* 把 §52.1 表里的编号改成裸编号（表头仍是 `编号`）→ 也全绿：两把锁各自独立；
* 语料与解析结果不一致 → 逐项相等守卫报"语料已陈旧"。

**两处如实记录的边界**：

1. **只核对了 8 条 `undesigned`，没有假装核对另外 41 条 `blocked_by=none`。** 前者"错"是二元的（行为在不在），
   后者"错"是判断性的（那个测试算不算覆盖）。那 41 条的 `evidence` 指针**只经过结构检查**（文件与 token
   存在），是否真支撑各自的期望**没有复核**——这是本阶段明确留下的欠账，写在这里而不是含糊过去。
2. **没有为 C-007 / S-027 新造断言。** 为了让台账好看而新写一条"证明某能力不存在"的测试，正是台账要防的
   自证；`no_witness_reason` 把"这条没人看着"变成一条被记录的事实，代价是它需要人来读。

**与"放宽优先"的关系**：本轮**没有**实现 `reconcile` / `update` / `env\runtimes`，**没有**删除或放宽任何拒绝，
**没有**改动任何用户可见行为。它只修台账与注记、加一条守卫——所以与 ADR-0021 无关
（既不是放宽也不是收紧，而是把一个错误的记录改对）。

### 52.7 实施顺序

1. 逐条读代码 + 跑行为核对 8 条 `undesigned`（F1 就是这么出来的——先读 `decide_scope` 的第 1 步）。
2. 修 C-021：在既有测试里点名它，删掉错处置，重生台账看 `status` 自己翻。
3. 给 6 条补证人、给 2 条写 `no_witness_reason`、把 C-022 的注记改准。
4. 加守卫第二十组（含非空性演练：两个方向各造一次）。
5. 验红、跑全量 + 旧切片 + 真机验收、回写。

## 53. 第 53 阶段：复核 41 条 `blocked_by=none`（守卫第二十一组）

### 53.1 这一阶段要解决什么

§52.6 把一处欠账写在了明处：

> 那 41 条的 `evidence` 指针**只经过结构检查**（文件与 token 存在），是否真支撑各自的期望**没有复核**。

§52 只核对了 8 条 `undesigned`，理由是那 8 条"错"是二元的（行为到底存不存在）；而 41 条 `none` 的"错"
是判断性的（那个测试算不算覆盖）。这两句话都对，但它们合起来会留下一个**没人看着的 41 条**——
而 §35 的教训正是"记下来不等于做了"。

本阶段就把这 41 条**逐条拿去核对**：读被指的测试，再读它调用的代码。

### 53.2 复核方法

对每一条：

1. 从两份文档的**场景表**里取出它的 `setup` 与 `expectation`（不是从处置表里读自己的注记——那等于问自己）；
2. 打开 `evidence` 指针指向的**文件**，定位那个 token **真正落在哪一行**；
3. 若那一行不是断言，就继续在文件里找**哪一条测试真的断言了这条期望**；
4. 找到之后**读实现**，确认断言不是恒真式；
5. 记下期望里**没有**任何测试覆盖的那一半。

### 53.3 复核结果：判断大多是对的，指针大多是错的

**41 条里没有一条的 `blocked_by=none` 是因为"测试不存在"而错的**（除 53.4 的两条）——期望本身基本都成立。
错的是**指针**：41 条里约 **30 条**的 `evidence` 是一串**裸 token**，而把它们逐条落到行上之后，指向的东西是：

| 指针 | 它实际落在哪 | 类 |
|---|---|---|
| `test_l1_desired.py#desired` | 模块 **docstring** 与 import 块 | 散文 |
| `test_l1_sources.py#digest` | **import 行**（`digest_for`, `parse_single_digest`） | 散文 |
| `test_l1_toolstate.py#PATH` | 一段**小节注释**（`# the PATH invariant`） | 散文 |
| `test_l1_registry.py#payload` | **import 行** + 若干局部变量 | 散文 |
| `test_l1_registry.py#managed_tool_payload` | **import 行** | 散文 |
| `test_l1_rebuild.py#audit` | **局部变量**（`audit = Path(...)` / `before_audit`） | 散文 |
| `test_l1_where.py#effective` | docstring + **另一条**测试（`effective_now/new_process`，与 Zone W 无关） | 指错测试 |
| `test_l1_exposure.py#template` | **另一条**测试（未知模板 token，与"值含换行/`%VAR%`"是两件事） | 指错测试 |
| `test_cli_env.py#PERSISTENCE_TARGET_FORBIDDEN` | **另一条**判据（值在数据根之外——同码不同规则） | 指错测试 |
| `test_l1_discovery.py#candidate` | **另一条**测试（长路径 `\\?\` 前缀） | 指错测试 |
| `test_l1_doctor.py#broken` | **另一条**测试（状态 → 退出码映射） | 指错测试 |

而那 41 条里只有 **11 条**从一开始就写的是**测试函数名**（`test_l1_registry.py#test_only_one_active_binding_per_key_is_accepted`
这一类）。

**为什么这是一个缺陷而不是"风格问题"**：那时的结构检查是

```python
if token not in path.read_text(encoding="utf-8"):
    return [f"{key}: {kind} token {token!r} does not appear in {name}"]
```

——一个**子串**判断。子串在那个方向上**不可能变红**：把 `test_l1_rebuild_rewrites_the_derived_files_and_archives_the_previous_ones`
整个删掉，`audit` 仍然在文件里（它还是别处的局部变量名）。也就是说，**指针可以在它声称指向的测试消失之后继续"有效"**。
这正是 §50 的恒真式换了个地方：**一个不可能红的检查不是检查**。

最干净的一例是 `test_l1_registry.py#payload`：它**从来没有**出现在该文件的任何 `def test_` 行上，
只出现在 import 行与局部变量里——所以那条指针从一开始就**没有指向任何测试**，却通过了结构检查。

### 53.4 三条判断真的错了

**（F1）`C-028`：`env forget --all` 这个命令形式不存在。**

期望写的是"`env forget --all`：AIROOT 写过的全部恢复；用户自己的变量不变"，处置写的是 `blocked_by=none`、
指针指向 `env forget` 的 dry-run 测试。复核：`cli.py` 里 `env forget` 的 parser **只接受**
`external_id` / `--variable` / `--dry-run`；`agents/airoot.json` 登记的也是 `["env", "forget", "<external-id>"]`；
`not_implemented` 清单里没有它（那条清单管的是**命令路径**，而 `env forget` 本身是实现的）。
所以这条期望描述的是一个**不存在的命令形式** → 改判 `undesigned`，且与 `C-007` 同一处置：
**给不出证人**（加一条"`--all` 不被接受"的断言只会在别人把它实现出来时变红，那正是"让台账好看这件事
自己生出一条自证的断言"），所以写 `no_witness_reason`。

**（F2）`C-026`：行为已实现，测试一条都没有。**

期望是"值含换行 / `%VAR%` → 拒绝，退出码 8"，原指针指向的测试测的是**未知模板 token**。
复核 `caps/environment.py::validate_value`：它恰好实现了**换行**、**未配对引号**、**`%...%` 在 `REG_SZ` 下
会被字面存储**、**歧义 `%` 展开**四种拒绝，全部抛 `PERSISTENCE_TARGET_FORBIDDEN`（`exits.py` 映射到退出码 8，
与期望**逐字一致**），并在 `caps/exposure.py` 的两处被调用。但把测试全树翻一遍：`PERSISTENCE_TARGET_FORBIDDEN`
只出现在**另外三条判据**上（数据根之外、禁用变量名、无数据根），**没有任何测试构造过一个含换行的值**。
所以它既不是缺能力、也不是没设计，而是**测试债** → `unchecked-invariant`（这个词汇值在 §49 就是为
`S-012` 这类情形造的）。

**（F3）`S-006`：比"没点名"更严重——那条不变量根本没有执行点。**

期望是"W 中有同名 python → machine `where` 不选择 W；激活 session 后才可见"，原处置 `blocked_by=none`、
指针 `test_l1_where.py#effective`（指错测试）。复核：

* `where.py::_managed_candidates` 遍历 `registry.active_bindings_for_capability()`，只按 **`scope`** 过滤，
  **从不读 `zone`**；
* `Binding(...)` 的生产者只有两个——`tx/simulate.py` 与 `tx/artifact.py`——**都硬编码 `"R"`**。

所以"Zone W 永不进入 machine PATH"这条在 `AGENTS.md` §4 与 §5.8 里写着的恒定式，今天是**不可达但未强制执行**：
没有任何代码能违反它（因为没有代码产生 W），也没有任何代码阻止它被违反（因为没有过滤器）。
它**不是**"少一条测试"——先补测试的话那条测试会**当场变红**。
→ 改判 `unchecked-invariant`，并在注记里写明：补 `zone` 过滤会**改变 `where` 的选择语义**，
属单独决策，本轮**不擅自做**；它能被证伪的那一刻是"出现一个写 `zone="W"` 的生产者"。

> **§56 已关闭这一条**：ADR-0022 做了那次决策（"`where` 不得机器级发现 Zone W"），机器级槽位加了执行点，
> `test_l1_where.py` 有一条点名 `S-006` 的测试，处置删除。注意本段最后那句判断**没有完全应验**——
> 真正让它能被证伪的不是"出现一个写 W 的生产者"（测试直接写 registry 就够了），而是**有人去写那条测试**。

### 53.5 修法

1. **改判 3 条**：`C-028` → `undesigned`（+ `no_witness_reason`）、`C-026` → `unchecked-invariant`、
   `S-006` → `unchecked-invariant`。分布由 `none 41 / undesigned 7 / unchecked-invariant 1`
   变为 **`none 38 / undesigned 8 / unchecked-invariant 3`**。
2. **把每个指针改成测试函数名**（`<file.py>#test_...`），并让**守卫**要求这一点——见第 3 条。
   改的时候必须做一次选择：一条期望的两半落在两个测试里时，**指那个覆盖"危险方向"的**，
   另一半写进 `note`。于是"只证明了一半"第一次变成**写下来的事实**而不是读者的猜测。
3. **守卫第二十一组（2 项）**：指针必须是**该文件里真实存在的模块级 `def test_*`**——
   `evidence` 与 `witness` 用同一条规则（证人的职责一样）。第二项是**红向自测**，把旧规则的失效
   执行化：`test_l1_registry.py#payload` 在该文件里**从不出现在 `def test_` 行**上，且**删掉它本该指向的
   测试之后仍然存在**——所以旧规则无论如何都不会红。
4. **把"只覆盖了一半"逐条写进 `note`**（18 条），例如：
   `C-004` 的"ACL/path conformance 失败"半边要等 P2 的 ACL 基线；`C-005` 的"被 launcher 查询"半边
   没有对象（P1 **不写任何 launcher 文件**）；`C-010`/`C-030` 的"不得声称父进程已改变"是**措辞禁令**，
   测试能观察的是"没有持久化记录"；`C-013` 的"Skill 加载失败"半边没有对象（这个项目**没有 Skill 加载器**）；
   `C-016` 的期望把输入类型说宽了（**没有任何测试用 `.bat`/安装器作为输入**，发现面只有 PE 静态探测）；
   `C-023` 的"新进程可见"在 pytest 里没有断言（跨进程的观察在 `real_machine_acceptance.py`）；
   `C-024` **没有任何测试真的去持久化一个 `cli\exposure\bin` 值**（全仓库只有两处 `shim` 字样，都在 docstring 里）。
5. **改一处陈旧的措辞**：`S-031` 的证人是 `test_cli_steward.py#project_manifest_check`——那是个**字段名**不是测试名，
   在新规则下会红；改成断言它的那条测试 `test_forget_drops_the_record_and_keeps_the_file`。

### 53.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S53.1 逐条复核 41 条 | ✅ | 每条都读了被指的测试与其实现；11 条指针本来正确，约 30 条被改写 |
| S53.2 改判 3 条 | ✅ | `C-028` → `undesigned`；`C-026` / `S-006` → `unchecked-invariant`；分布 `none 41→38`、`undesigned 7→8`、`unchecked-invariant 1→3` |
| S53.3 指针一律改成测试函数名 | ✅ | 全部 `none` 条目 + 全部证人；`evidence_problems` 为 0 |
| S53.4 18 条"只覆盖一半"的 `note` | ✅ | 见 53.5-4 的清单 |
| S53.5 守卫第二十一组 | ✅ | `test_every_evidence_and_witness_pointer_names_a_test_function` + `test_the_old_substring_rule_could_not_go_red_and_the_new_one_does`（审计 66 → **68 项**） |
| S53.6 测试 + 回写 | ✅ | `pytest cli/tests` 743 → **745 项**；语料重新生成是**外科式**的（27 个 fixture 里**只有 `scenario_ledger.json` 逐字节改变**，`index.json` 都没动） |

**守卫确实会红（两个方向都验过）**：

* 把 `C-006` 的指针改回裸 token `test_l1_rebuild.py#audit` → `test_every_evidence_and_witness_pointer_names_a_test_function`
  报 `"C-006: evidence 'audit' is not a test function name — a bare token survives the deletion of the test it claims to point at"`；
* 把同一个指针改成**不存在**的函数名 → 报 `"C-006: evidence names a test function that test_l1_rebuild.py does not define"`；
* 语料与处置表不一致 → 逐项相等守卫报"语料已陈旧"。

**本轮被自己的守卫教了一次（如实记录）**：红向自测的第一版断言写的是"`audit` 在该文件里从不出现在
`def test_` 行上"。它**当场失败**——因为 `test_rebuild_repairs_a_stale_audit_projection` 的**函数名里就有 `audit`**。
我的论断是错的：那个 token 是**偶然**落在一个测试名上的，而它同时也落在别处的局部变量上，
所以"能解析"这件事不携带任何信息。修法是把两个形状**分开陈述**：用 `test_l1_registry.py#payload` 讲
"**从不**出现在测试名上"（强形式），用 `#audit` 讲"偶然是某个测试名的一部分"（弱形式，断言它
**既**出现在 `def` 行上**也**出现在非 `def` 行上）。这与 §51"135 条错误全是假阳性"是同一种教训：
**论断要拿实测校准，而不是拿直觉**。

**（F4）`AGENTS.md` 已经涨到超过 agent 入口文档的读取预算。**

这一条**不是**场景台账的缺陷，是本轮**自己造成**的：把 §53 的段落追加进 `AGENTS.md` §1 的阶段日志之后，文件达到
约 **65.8 KB**，而在**加载为工作区指令**时被静默截断到约 **65.2 KB**——被切掉的正好是**结尾**（§8 的末尾与
§9「维护远端仓库」）。也就是说：**agent 读到的 `AGENTS.md` 少了最后两节，而文件本身看起来是完整的**。
这与 §48（状态文档否认已交付的东西）是同一类问题的另一种形状：**文档在入口处静默说谎**，只是这次的
说谎者是加载路径而不是文档作者。

根因不是"这一轮写多了"，而是**结构**：§1 用一个不断增长的段落承载 §31–§53 的逐阶段日志，而**同样的内容
在契约草案里各有一节**（§31…§53），于是同一份事实被维护两遍、其中一遍还在膨胀。

**本轮不修**，因为它是一次**入口文档的结构变更**，需要先规划（按项目的既定要求"做下一阶段前先规划"）：

* 把 §1 那段**逐阶段细节**换成**一段摘要 + 指向契约草案各节的引用**（草案里已有全文，不是删除信息）；
* 保留 §1 里被守卫依赖的东西（schema 数、测试数、语料数、场景数、"尚未实现"清单、两条禁令）；
* 并考虑加一条**守卫**："入口文档不得超过 N 字节"，理由是**超过就会被加载路径截断**——这条守卫的危险方向
  是"文档长了却没人发现"，与 §35 的"记下来不等于做了"同一类。取 N 之前要先确认这个预算是**部署属性**
  还是**项目属性**，不把宿主机的限制硬编码成项目的契约。

在那之前，本轮的 `AGENTS.md` 是**完整且正确**的（守卫全绿、计数一致）；只是**作为指令被加载时会被截断**——
这件事本身就值得记在这里而不是留白。

> **§54 已关闭这一条**：入口文档由 65 511 字节缩到约 35 KB，逐阶段细节改为指向本草案 §31–§54 的引用，
> 并新增守卫第二十二组（尺寸 + 去路）。缩尺寸时实测"23 段在草案里各有一节、零独有"，所以没有丢信息。

**三处如实记录的边界**：

1. **没有为了把台账变绿而新写行为测试。** `C-026`（值注入拒绝）与 `C-024`（shim 路径）都是 §13.2 的硬规则、
   都值得有测试，但本轮的产物是**正确的测量**；此刻补测试会让"复核发现这两条是空的"这件事**消失**。
   它们以 `unchecked-invariant` 和 `note` 的形式留在台账里，补测试是后续步骤的事。
2. **没有为 `S-006` 补 `zone` 过滤。** 它会改变 `where` 的选择语义（`machine` 级绑定在 `zone="W"` 时的行为），
   属单独决策。本轮只把"不可达但未强制执行"这件事写下来。
3. **没有复核 `p2-protected-state`(10) / `p4-real-backend`(7) / `p3-native-indexer`(0) 那 17 条。**
   它们的"错"要等能力存在之后才可判定，§52.4 已经写明这条边界，本轮延续它。

**与"放宽优先"（ADR-0021）的关系**：把指针从"任何 token"收紧成"真实存在的测试函数"**不是收紧用户权限**，
而是把一致性检查加严——属 ADR-0021 明确列出的第四种例外（审计守卫）。本轮**没有**改动任何用户可见行为：
`env forget` 仍然没有 `--all`，`where` 仍然不按 `zone` 过滤。台账的 `status` 语义、`blocked_by` 词汇与
`none` 条目的门槛全部未变。

### 53.7 实施顺序

1. 写一个只读探针，把每条 `none` 的 `expectation` 与它的指针**实际落在哪一行**并排打出来（不读自己的注记）。
2. 逐条读被指的测试，再读它调用的代码；把"指针错了"与"期望只覆盖一半"分开记。
3. 改判 `C-028` / `C-026`，复核 `S-006` 时去读 `where.py` 与两个 `Binding(...)` 生产者（F3 是这么出来的）。
4. 指针全部改成函数名 + 写 18 条 `note` + 改 `S-031` 的证人。
5. 收紧 `_evidence_pointer_problems`，加守卫第二十一组（含红向自测）。
6. 验红（两个分支各来一次）、重生语料并核对**外科式**、跑全量 + 旧切片 + 真机验收、回写。

## 54. 第 54 阶段：把入口文档缩回可完整载入的尺寸（守卫第二十二组）

### 54.1 这一阶段要解决什么

§53.4 (F4) 记下了一处**由那一轮自己造成**的缺陷，并把裁决留给了本轮：

> 把 §53 的段落追加进 `AGENTS.md` §1 之后，文件达到约 **65.8 KB**，而在**加载为工作区指令**时被静默截断到约
> **65.2 KB**——被切掉的正好是**结尾**（§8 的末尾与 §9「维护远端仓库」）。**agent 读到的 `AGENTS.md` 少了最后两节，
> 而文件本身看起来是完整的。**

本轮把它当成一个阶段来做，因为它的性质与 §48 同族但**更隐蔽**：§48 是*文档作者*说了假话（状态节否认已交付的
`SKILL.md`），而这里是**加载路径**说了假话——文档没写错任何东西，只是**没被完整读到**。

### 54.2 实测

| 事实 | 数值 | 怎么得到的 |
|---|---|---|
| `AGENTS.md` 大小（磁盘上，CRLF） | **65 816 字节** | `len(Path("AGENTS.md").read_bytes())`——二进制读，不折叠换行 |
| 本部署的指令预算 | **65 536 字节** | 加载时的截断报告：`truncated AGENTS.md from 65840 to 65243 bytes` |
| 超出量 | **+280 字节** | 上两条相减——**文件当时已经在被静默截断**，不是"接近上限" |
| §1 阶段日志段落的占比 | **约 32.7 KB ≈ 50%** | 从 `**已实现（协议级，Python 3.11）**` 到 `项常驻跨工件一致性检查…）。` |
| §1 里的阶段段落数 | **23**（§31–§53） | `re.findall(r"契约草案 §(\d+)", 日志段)` |
| 其中**没有**对应草案 `## NN.` 节的 | **0** | 逐个比对草案的 H2 清单 |
| 缩小后 | **35 083 字节**（−30 733） | 余量从"超 280"变成约 **30 KB** |

**第一行是本阶段第一个教训：换一个更接近现实的测量，结论就变了。** 第一版量的是
`len(read_text(...).encode("utf-8"))`，得到 65 511——**量少了 305 字节，正好等于文件行数**：
文本模式读入把 `\r\n` 折成 `\n`，再编码回去就比磁盘上小。按那个数字，文件"还有 25 字节余量"；
按 `read_bytes()`，它**已经超了 280 字节**，也就是 §53 报告的截断**当时仍在发生**。
守卫用的是二进制读取，所以它第一次运行就把这件事报了出来。（§51 与 §53 各有一回同类教训：
**用方便的量替换准确的量**。）

最后两行是这一阶段的**判据**：那 23 段**在草案里各有一节**，所以从 §1 移除它们移走的是**摘要层**，不是信息。

### 54.3 判断：这是项目的问题，不是宿主机的问题

§53.4 (F4) 把待决问题写成"取 N 之前要先确认这个预算是**部署属性**还是**项目属性**"。本轮裁决：**两者都是，
但后果落在项目上，所以项目认领这个上限。**

理由是一句话：预算是部署给的，**被截断的后果**是本项目承担的——一个只读到半份规则的 agent 会按半份规则行动，
而它没有任何办法知道少了东西。把上限说成"宿主机的事"等于把一处**静默的**信息丢失推给读者。
所以：**上限来自部署（记录来源），约束由项目承担（写进守卫）。**

### 54.4 修法

1. **把 §1 的逐阶段日志换成一段交付摘要 + 指向草案的引用。** 判据已在 54.2 给出：23 段全部有草案对应节，
   且**保留全部被守卫依赖的陈述**（实测移出日志段后仍在文件里的：`745 项测试`、`27 个 fixture`、
   `107 个场景编号`、`68 项常驻跨工件`、`DATA_ROOT_ACL_DRIFT`、`Everything 级性能` 禁令）。
2. **守卫第二十二组（2 项）**，两项分别守这条修法的**两个危险方向**：
   * **(a) 尺寸**：`AGENTS.md` 不得超过 `ENTRY_DOC_BUDGET_BYTES = 65536`。危险方向是"文件长过了预算却没人发现"
     ——**超过是静默的**，所以它是这个项目里最该被守卫的一类事实。常量单独命名并**写明来源**（实测的部署预算），
     不写成一个散落在断言里的魔数。
   * **(b) 去路**：`AGENTS.md` 必须**说出逐阶段记录在哪里**。危险方向是**这条修法本身**——为了缩尺寸把细节删掉、
     却不留任何去路，那会把"入口文档"变成"入口摘要"，读者再也找不到 §31–§54 的记录。缩尺寸与可追溯必须**同时**成立。
3. **（F1）顺带关掉一处一直开着的缺口：文档里的测试总数从来没有和现实比对过。**

   这一条是**做守卫时才发现的**，不是事先计划的。写 (a) 时顺手看一眼旁边那个数，发现两者的性质完全不同：
   **审计检查数是从源码推导的**（`_audit_check_count` 读自己的源码），而**测试总数只和它自己的副本比对**
   （`AGENTS` ↔ `DRAFT` ↔ `REVIEW` 三方一致，外加"`AGENTS.md` 只能有一个数"）。也就是说：
   三份文档可以在一个**已经过期好几轮**的数字上保持一致，而没有任何东西会红——
   而这正是 agent 回报给用户的那个数。`test_the_skill_states_the_same_test_count_as_the_repo` 的文档字符串
   自己写着"stale number there is an agent-facing lie"，却只比较 `SKILL.md` 与 `AGENTS.md`。

   修法是把那个数与**这一次会话实际收集到的条目数**接上（`request.session.items`），并处理一个真实约束：
   单独跑一个文件时不能拿全量数字去比——所以**部分运行会 `skip` 并写明原因**（"说了不算"必须长得和"算了"不一样），
   而不是静默通过。首次运行就报出 `the documents say 745 tests and this session collected 747`。

### 54.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S54.1 实测 | ✅ | 见 54.2 表：65 511 字节 / 预算 65 536 / 日志段占 49.9% / 23 段零独有 |
| S54.2 §1 换成摘要 + 引用 | ✅ | 移除 32 673 字节的日志段，换成一段交付摘要并指明记录在草案 §31–§53 |
| S54.3 守卫第二十二组 | ✅ | `test_the_entry_document_fits_the_reader_budget` + `test_the_entry_document_says_where_the_stage_record_lives`（审计 68 → **70 项**） |
| S54.4 测试计数接上现实 | ✅ | **（F1）** 守卫第二十二组顺带发现：文档里的测试总数**从来没有和实际收集数比对过**。`test_the_documented_test_count_is_the_same_everywhere` 现在拿 `request.session.items` 当基准（部分运行会 `skip` 并说明），首次运行即报出"文档说 745、实际收集 747" |
| S54.5 测试 + 回写 | ✅ | `pytest cli/tests` **747 项**；`AGENTS.md` 由 65 511 降到 **35 KB 出头**（余量从 25 字节变成约 30 KB） |

**守卫确实会红（四个方向都验过）**：

* 尺寸方向：把 `AGENTS.md` 写长到超过预算 → 尺寸守卫报出实际字节数与预算；
* 去路方向：三种失败模式都拿**合成输入**演练过（两半缺一不算委派、只有文件名不算、两半齐才算），
  所以它不依赖"有人记得去破坏真文档"，也不靠改常量来假装红；
* 尺寸守卫的第二半（`size > 10_000` 的下限）：一个几乎空的文件不该"通过尺寸检查"，反方向也验过；
* **测试计数**：这条改完**第一次运行就红**——`the documents say 745 tests and this session collected 747`。
  这不是"验红"，是**它自己抓到了 (F1)**：三份文档一致地错，而此前没有任何东西会红。

**一处如实记录的边界**：本轮**没有**动 §1 的其他段落（"尚未实现"、两条禁令、"正在提案中的方向变更"、三条硬规则）
——它们不长，而且被守卫直接依赖。也**没有**改任何用户可见行为：这一轮只动文档结构、一条既有守卫的基准、
以及一条去路检查。

**与"放宽优先"（ADR-0021）的关系**：加一条尺寸守卫、并把测试总数接到现实上，**都不是收紧用户权限**，
属 ADR-0021 明确列出的第四种例外（审计守卫）。而且它守的是一处**已经真实发生过**的静默丢失、
以及一处**已经真实发生过**的计数漂移，不是假想的风险。

### 54.6 实施顺序

1. 先量：文件字节数、预算、日志段占比、**每段是否在草案里有对应节**（最后一条决定"删掉是否丢信息"）。
2. 写这一节（先规划再动手），把判据与危险方向写死。
3. 把 §1 的日志段换成摘要 + 引用；逐条确认被守卫依赖的陈述仍在。
4. 加守卫第二十二组（含常量与来源注释）。
5. 验红：尺寸方向（写长摘要 / 调小预算）、去路方向（三种合成输入）、计数方向（它自己会红）。
6. 跑全量 + 旧切片 + 真机验收、回写计数、提交。

## 55. 第 55 阶段：把 §53 复核出来的两条测试债还上（§13.2 的硬规则）

### 55.1 这一阶段要解决什么

§53 逐条复核 41 条 `blocked_by=none` 时抓到两条**规则已经实现、测试一条都没有**：

| 场景 | 期望 | §53 的实测 | 当时的处置 |
|---|---|---|---|
| `C-026` | 值含换行 / `%VAR%` → 拒绝，退出码 8 | `environment.py::validate_value` 实现了四种拒绝（换行、未配对引号、`REG_SZ` 下的字面 `%…%`、歧义 `%` 展开）；**全树没有任何测试构造过含换行的值** | `unchecked-invariant` |
| `C-024` | 尝试持久化 `exposure\bin` shim → `PERSISTENCE_TARGET_FORBIDDEN`，退出码 8 | 全仓库只有两处 `shim` 字样，**都在 docstring 里**；原指针指的那条测试判的是**同码不同规则**（值在数据根之外） | `blocked_by=none` + 注记 |

§53 明确写下了**当时为什么不补**：

> 没有为把台账变绿而新写行为测试（`C-026`/`C-024` 都是 §13.2 的硬规则、都值得有测试，但此刻补测试会让
> "复核发现这两条是空的"这件事**消失**；它们以 `unchecked-invariant` 与 `note` 留在台账里，补测试是后续步骤的事）。

本轮就是那个"后续步骤"。顺序很重要，且已经遵守：**先如实记账（§53），再把债还上（§55）**——
反过来做的话，台账会显示这两条一直有证据，而那次复核的发现就永远不会被写下来。

### 55.2 为什么值得单独一个阶段

因为它们是 **§13.2 的硬规则**，而 §13.2 的三条硬规则是管家模型对用户的行为承诺（`AGENTS.md` §1 末尾逐条列着）。
一条**实现了但没人看着**的硬规则，与一条**没实现**的硬规则，在真出事的时候没有区别——差别只存在于文档里。
`C-026` 尤其如此：它挡的是**往用户持久环境里注入第二条语句**（值里的换行会进 shell profile）。

### 55.3 判据（先写判据，再写测试）

1. **走公开路径**：测试驱动 `resolve_exposure`（值 → `validate_value` → 数据根检查那条链），
   而不是直接调私有助手——否则测的是助手，不是"用户能碰到的那条路"。
2. **断言"为什么被拒"**，不只断言 reason code。`C-026` 的四种拒绝与 `C-024` 的拒绝**共用同一个码**
   （`PERSISTENCE_TARGET_FORBIDDEN`），所以只比码的话，**一个"把所有值都拒掉"的实现也能过**。
   必须看 message/evidence 指向的是**换行**，而不是"数据根之外"之类。
3. **必须有负向对照**：`%JAVA_HOME%\bin` 在 `REG_EXPAND_SZ` 下**必须通过**。
   `%` 本身不是禁止的——禁止的是"会被字面存储"（`REG_SZ`）与"歧义展开"。
   没有这条对照，判据 2 只是把"什么都拒"换成了"凡带 `%` 就拒"，仍然测不出规则。
4. **`C-024` 要覆盖两条通往 shim 的路**：把 shim 当**值**（由 `validate_target_in_data_root` 拒），
   以及用**相对路径**从对象根爬出去（由 `validate_spec` 的"不得含 `..` / 不得绝对"拒，**不同的码**）——
   两条路都写上，并**如实标注哪条是哪个码**，不然会把两条规则混成一条。
5. **走真实注入向量**：除了在模板里写字面换行，还要用一个**名字里带换行的目录**当对象根，
   让换行从**路径**流进持久值。后者才是真向量（模板是随包数据，路径是用户数据）。

### 55.4 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S55.1 `C-026` 五种取值 | ✅ | 换行（模板）/ 换行（**目录名**）/ 未配对引号 / `REG_SZ` 下的字面 `%…%` / 歧义 `%` 全部拒绝且 evidence 指向正确原因；`%…%` 在 `REG_EXPAND_SZ` 下**通过**（负向对照） |
| S55.2 `C-024` 两条路 | ✅ | 值 = shim → `PERSISTENCE_TARGET_FORBIDDEN`（退出码 8）；`path_prepend` 用 `..` 爬向 shim → `INVALID_INPUT`（**不同的码**，如实标注） |
| S55.3 台账翻转 | ✅ | 两条的 `status` 由 `uncited` 自翻成 **`evidenced`**（40 → **42**）；`C-026` 的 `unchecked-invariant` 处置与 `C-024` 的 `none` 处置**删除**（与 §52 对 `C-021` 的处置一致：错的记录不是改写，是删掉） |
| S55.4 语料 + 计数 | ✅ | `scenario_ledger.json` 重生；`pytest cli/tests` 747 → **749 项** |

**守卫确实会红（三个方向都验过）**：

* 把 `validate_value` 里的换行分支删掉 → `C-026` 的测试报"应当以换行理由被拒"（**验的是行为，不是记录**）；
* 把"值必须在数据根内"那条检查放宽成"任意路径" → `C-024` 的测试报红；
* 把 `REG_EXPAND_SZ` 也纳入"含 `%` 即拒" → **负向对照**报红（这正是判据 3 存在的理由）。

**两处如实记录的边界**：

1. 本轮**只还这两条**。§53 记下的其他弱指针（`C-004` 的 ACL 半边、`C-005` 的 launcher 半边等）
   仍然只写在 `note` 里——它们各自要等 P2 的能力，**不是**测试债。
2. **没有**顺手把 `S-006` 的 `zone` 过滤实现掉。§53 已经写明它属单独决策（会改 `where` 的选择语义），
   本轮不混进来。

### 55.5 实施顺序

1. 读实现（`validate_value` 的四条分支、`resolve_exposure` 的调用顺序、`validate_spec` 的 `path_prepend` 约束），
   先把"哪条路被哪个码拒"弄清楚——`path_prepend` 的绝对路径约束是读代码才发现的（否则会写出一个必红的测试）。
2. 写 §55（先规划）。
3. 写两条测试：`C-026` 一条（五种取值 + 负向对照）、`C-024` 一条（两条路）。
4. 删掉两条过期处置，重生台账，确认 `status` 自己翻。
5. 验红三个方向、跑全量 + 旧切片 + 真机验收、回写计数、提交。

## 56. 第 56 阶段：给"Zone W 不参与机器级发现"一个执行点（ADR-0022）

### 56.1 这一阶段要解决什么

§53 复核时抓到的**最严重**一条（`F3`）：`AGENTS.md` §4 与 §5.8 里写着的恒定式"**Zone W 永不进入
machine PATH**"**没有任何执行点**——`where.py::_managed_candidates` 只按 `scope` 过滤、**从不读 `zone`**，
而两个 `Binding(...)` 生产者都硬编码 `"R"`。结论是六个字：**不可达，但未强制执行**。
§53 把它记成 `unchecked-invariant`，并写明"补 `zone` 过滤会改变 `where` 的选择语义，属单独决策"。

> **§56 已关闭这一条**：ADR-0022 做了那次决策，过滤器落地，`test_l1_where.py` 里有一条点名 `S-006`
> 的测试，台账处置删除（`evidenced` 42 → **43**）。

### 56.2 这一阶段**没有**发明规则

规则本来就在文档里，而且写了**四遍**（ADR-0022 里逐条抄了原文）：规划 §3.2 分区表、
规划 §5（:603）、三大核心契约（:466）、验证方案 `P-002`。四处措辞一致，**并且两条边界都写下来了**：

* W **不进**默认 `where` 结果；
* W **可以**通过**显式** session/project activation 执行。

所以本阶段的产物不是一条新规则，而是一个**执行点**，外加一条**负向对照**（见 56.3-2）。

### 56.3 决策与修法

决策记在 **ADR-0022**（它是决策记录，本节是阶段记录；两边不互相抄）。要点：

1. `where` 的**机器级槽位**（steward / machine）不得选中 `zone == "W"` 的绑定——承重的一条，
   因为契约说的是"发现"，而发现发生在**读者这一侧**；
2. **只**动机器级槽位：槽位 1–2（project/session）本来就要求 `identity_match`，那正是"显式激活"；
3. **不新增 reason code**（没有出错，回落到 `NOT_FOUND` 是诚实的；发明一个码等于声称违规）；
4. **不标 `usable: false`**（那会经由 `owned_unusable` **凭空制造一次 `DEGRADED_TO_REFERENCE`**）；
5. **让排除可见**：`candidates[]` 每行新增**可选** `machine_discoverable`。

四个被否掉的替代方案（准入处拒绝、新增码、从 `candidates[]` 删掉、`usable: false`）连同理由写在 ADR-0022。

### 56.4 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S56.1 ADR-0022 | ✅ | 决策记录新增一节：背景、四处契约原文、5 条决策、5 个被否方案、后果、明确不做 |
| S56.2 `where` 执行点 | ✅ | `_Candidate.machine_discoverable`（由 `zone` 推导）+ 机器级槽位跳过 + `_candidate_document` 投影 |
| S56.3 Schema（minor） | ✅ | `where-response.schema.json` 的 `candidates[]` 增加**可选** `machine_discoverable`；`docs/schema/README.md` 按规则 2 记录 |
| S56.4 测试（含负向对照） | ✅ | `test_l1_where.py::test_S006_…`：机器级**不**发现 W（且候选行 `usable: true`／`health: healthy`，说明拒的是 **zone** 不是健康），同一个 W 绑定在**显式 session 激活**下**能**被选中 |
| S56.5 台账 + 语料 | ✅ | `S-006` 处置删除、`status` 自翻 `evidenced`（42 → **43**、`unchecked-invariant` 2 → **1**）；语料**外科式**重生：`scenario_ledger.json` + 5 个 `where_*.json`（候选行各多一个字段），`index.json` 未动 |
| S56.6 测试 + 回写 | ✅ | `pytest cli/tests` 749 → **750 项** |

**守卫确实会红**：把机器级槽位那两行 `continue` 短路（`and False`）→ 点名 `S-006` 的测试报
`assert True is False`（`found` 变成 True，即 W 被机器级发现了）。验完立刻还原。

**为什么本阶段**没有**新增一组守卫**：代码与 Schema 的一致性**已经**由运行期自校验守着——
`where()` 在返回前调用 `validate_self("where-response", document)`，而候选行是
`additionalProperties: false`，所以 `_candidate_document` 多一个或少一个键，**每一次**跑 `where` 的测试
都会红。再加一条"比对键集合"的守卫会是**恒真式**（§50 的教训）。**这一条是判断，不是省略**：本阶段新增的
跨工件一致性面（Schema ↔ 代码）已有覆盖，而唯一没被覆盖的那一面（契约 ↔ 行为）由 S56.4 的测试覆盖。

**两处如实记录的边界**：

1. **W 依然是不可达的**（两个生产者仍写 `"R"`）。本阶段的成果是**执行点**，不是"新的 W 来源"；
   测试为了构造 W 绑定而**直接写 registry**，这正是 ADR-0022 说的"历史数据 / 迁移 / 直接写库"那条路径。
2. **`search` / `inventory` / `effective` 仍然不过滤 `zone`**，这是有意的：它们报告的是**事实**
   （W 绑定存在、某个引用指向 W 路径），不是机器级发现。ADR-0022 把这一点列进"明确不做"，
   因为 `search` 是**显式查询**——把它一起改是一次单独的裁决。
   **§73 更正了这份名单**：ADR-0022 的那一行只提到 `search --managed-only`；`effective` **不是命令**
   （`caps/effective.py` 是模块，被 `where`/`cli` 使用，但 CLI 没有 `effective` 子命令），
   `inventory` 则**确实**输出 `zone`（那正是它的价值）。真正存在的两个面是 `search` 与 `inventory`，
   见 §73.2。

### 56.5 实施顺序

1. 先读四处契约原文（不是读自己的注记），把规则的**两条边界**抄下来——负向对照就是从第二条来的。
2. 写 ADR-0022（先决策，再动手）。
3. 读 `where.py` 的槽位循环与 `owned_unusable`，确认"标 `usable: false`"会制造假降级——这是决策 4 的来源。
4. 改 `where.py`（字段 + 跳过 + 投影）、改 Schema（可选属性）、改 `docs/schema/README.md`。
5. 写测试：两半放**同一个**测试里（任一半单独都会放过一条错规则）。
6. 删处置、重生语料、核对**外科式**、验红、跑全量 + 旧切片 + 真机验收、回写计数、提交。

## 57. 第 57 阶段：关掉最后一个 `unchecked-invariant`——root 卷身份漂移（S-012）

### 57.1 这一阶段要解决什么

`S-012` 是 §49 造出 `unchecked-invariant` 这个词汇值的**原因**：

> 第 5 个词汇值 `unchecked-invariant` 是被数据逼出来的：`S-012` 的卷身份检查**代码里已经存在**（`doctor.py`），
> 不属于"缺能力"，也不属于"没设计"——它是**测试债**，词汇必须能如实说出这件事。

§52 复核了 8 条 `undesigned`、§53 复核了 41 条 `none`，**两次都绕过了它**（§52.4 写明"不假装核对了另外 41 条"，
而 `S-012` 在那 41 条之外）。§55 关掉 `C-026`、§56 关掉 `S-006` 之后，它是台账里**最后一个** `unchecked-invariant`。

### 57.2 实测（复核，不读自己的注记）

期望（验证方案）："root volume 身份改变 → 原 registry 进入 recovery required，**不自动接管新目录**"。两半分别复核：

| 半边 | 实现 | 有没有测试 |
|---|---|---|
| **不接管** | `root.py::open_root` 卷身份不符即抛 `VOLUME_IDENTITY_MISMATCH`（`exits.py` 映射到退出码 **6**）。**它是真路径**：`cli.py:63` **每条命令**都调用它——卷被换掉之后，所有命令都会拒绝，而不是接管现在躺在这个路径上的任何目录 | **没有**：全树**没有任何一处**调用 `open_root` |
| **recovery required** | `doctor.py` 报 `severity=critical` + `remediation=recover`，`doctor.py:756` 把这个码映射成 `recovery_required` | **没有**：最近的一条 `test_missing_root_marker_requires_recovery` 测的是 **marker 缺失**，不是卷漂移 |

唯一碰过"坏序列号"的测试是 `test_l0_protocol.py::test_relative_refs_resolve_against_common`，
它测的是 **schema 的格式规则**（`volume_serial` 必须匹配 `^[A-Fa-f0-9-]{1,128}$`），与漂移行为是两件事。
所以 §49 的判断成立，而且比它写得更具体：**行为在两条路上都在，看着它的测试一条都没有。**

**顺带一处实测**：`open_root` 有一个 `verify_volume: bool = True` 参数，**全仓库没有任何调用方传它**
（只有定义处）。本轮**不动**它，但把它记下来——一个没人用、又能关掉身份守卫的开关，值得有人知道它存在。

### 57.3 判据

1. **用有效的另一个序列号制造漂移**，不是格式错误——否则测的是 schema，不是守卫。做法是从真实序列号
   **翻转一个字符**（`0`↔`1`），保证既合法又必然不同。
2. **断言两半都发生**：`doctor` 的 `status`/`severity`/`remediation`，以及 `open_root` 的拒绝与退出码。
3. **"不接管"要可核对**：断言 doctor 之后 **registry 与 marker 一个字节没变**（"doctor 不变更任何东西"
   是项目里已有的规则，`test_corrupt_registry_is_broken_and_never_rebuilt_implicitly` 就是这么写的）。
4. **evidence 要能回答"为什么"**：断言 evidence 里同时出现 marker 值与实际值——否则答案只说"身份变了"，
   不说"从什么变成什么"。这与 §57 要守的那条"失败即数据"是一回事。
5. **负向对照**：把真实序列号写回去之后，**同一个 root 又能被打开**。没有这一条，"一律拒绝"也会通过，
   而那会让 AIROOT 在任何机器上都拒绝启动。

### 57.4 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S57.1 两半都测 | ✅ | `test_l1_doctor.py::test_S012_…`：`doctor` → `recovery_required`（退出码 6）/ `critical` / `recover`；`open_root` → `VOLUME_IDENTITY_MISMATCH`（退出码 6） |
| S57.2 不接管可核对 | ✅ | 断言 doctor 之后 registry 与 marker **逐字节未变** |
| S57.3 evidence 说清"从什么变成什么" | ✅ | 断言 evidence 同时含漂移值与实际值 |
| S57.4 负向对照 | ✅ | 写回真实序列号后 `open_root` 成功返回该 root |
| S57.5 台账 | ✅ | `S-012` 处置删除、`status` 自翻 `evidenced`（43 → **44**）；`unchecked-invariant` 用量 **1 → 0** |
| S57.6 测试 + 回写 | ✅ | `pytest cli/tests` 750 → **751 项** |

**守卫确实会红**：把 `open_root` 的卷校验短路（`verify_volume` 强制 False）→ 测试在 `pytest.raises` 处报
`DID NOT RAISE`；把 `doctor.py` 的那条诊断注释掉 → status 不再是 `recovery_required`。验完立刻还原。

**一处必须写下来的后果：`unchecked-invariant` 这个词汇值现在**没有条目在用**了。**
它**留在** `MISSING_CAPABILITIES` 里，理由写进那份词汇的注释：**这个类会复发**（"不变量写在文档里、
代码里有、没人看着"是一种会反复出现的形状，§53 一次就抓到三条），而 §49 已经写明了当初为什么要造这个值。
删掉它反而是错的——那会把"我们曾经需要第五个词汇值"这件事也删掉。

### 57.5 明确不做

1. **不测 `verify_volume=False`**。它没有调用方；为它写测试等于把一个**没人用的开关**升格成契约。
   它记在 57.2 里，作为"有人知道它存在"。
2. **不构造"另一个卷"**（那需要第二块卷）。翻转序列号是等价的、确定性的，而且**更精确地**测到守卫的判据
   （判据是"序列号不同"，不是"物理上是另一块盘"）。
3. **不顺手改 `open_root` 的签名**（例如删掉那个没人用的参数）——那是 API 变更，需要单独裁决。

### 57.6 实施顺序

1. 先复核：把期望拆成两半，各自找实现，再各自找测试（结论是"两半都有实现、都没有测试"）。
2. 写本节（先规划）。
3. 确认序列号格式（schema 的 pattern）与真实格式（`paths.py` 的 `{:08x}`），据此造一个**合法但不同**的值。
4. 写测试：两半 + 不接管 + evidence + 负向对照。
5. 验红两个方向、删处置、重生语料、核对**外科式**、跑全量 + 旧切片 + 真机验收、回写计数、提交。

## 58. 第 58 阶段：ACL 基线的读取与漂移发射（`DATA_ROOT_ACL_DRIFT` 第一次可产生）

### 58.1 这一阶段要解决什么

§35 把 `DATA_ROOT_ACL_DRIFT` 记成**写明理由的例外**：它在 `INVARIANTS["D1"]` 里，但**产生不出来**；
那条记录同时写着"**P2 落地后这条例外必须删除**"。规划 §601 则直接要求："`doctor` 必须检查 ACL 是否偏离"。

本阶段做**读**的那一半（**不需要管理员**）；把基线**强加**回目录（写 ACL）仍属 P2 的 broker——
那是这一阶段**明确不做**的部分，见 58.6。

### 58.2 实测：`ctypes` 能读 DACL，而且不需要提权

先探针后设计。用 `advapi32.GetNamedSecurityInfoW`（`OWNER|DACL`）+ `GetAce` + `ConvertSidToStringSidW`
读一个目录，实测（本机）：

* **成功**，没有提权、没有 `pywin32`（项目的第三方依赖只有 `jsonschema`，这条必须成立）；
* 拿到 owner SID 与每条 ACE 的 `type`/`flags`/`mask`/`SID`（`D:\AIRoot` 11 条，`D:\env` 10 条）；
* **失败即数据**：`GetNamedSecurityInfoW` 返回 rc 而不是抛异常，所以调用方必须自己转成数据。

**同一次探针还测出一件必须写下来的事**：它的输出里全是**真实的机器 SID**。远端仓库是 **public**，
`AGENTS.md` §9 明令不得提交本机指纹，且当前树里**确实没有**（已实测扫描过）。
所以本阶段的硬约束是：**语料与测试里不得出现本机 SID**——基线只在 `tests_tmp` 里现算，不写进 fixture。

### 58.3 决策（ADR-0023）

1. **基线是观测值。** 草案 §3.1 自己写着 "`acl_baseline`（**观测值**）"。所以漂移的含义是
   "**现在看到的和当初记下的不一样**"，**不是**"你违反了某条要求的 ACL"。这正是管家该有的形状：
   AIROOT 不拥有数据根，不能规定它的 ACL，但可以**注意到它变了**。
2. **`repair` 的含义是"重新记录基线"。** 同表里 `WHITELIST_REVISION_STALE` 的 `repair` 就是这个读法
   （把 AIROOT 记的东西更新成事实）。**不是**"把用户的 ACL 改回去"——那会**撤销用户有意的改动**，
   与 ADR-0004 直接冲突。这一条是本阶段最容易做错的地方，所以写进 ADR。
3. **ACE 顺序不排序。** Windows 的 ACE 顺序**有语义**（允许/拒绝的先后）；排序会把一次**有意义的**改动
   洗成"没变"。宁可对一次纯重排报漂移（那是真的变过），也不要漏报一次权限收紧。代价写进 58.5。
4. **不新增 reason code。** 读不出来时按本文件既有惯例报同一个码：`doctor.py` 对"卷身份**读不出来**"
   用的就是 `VOLUME_IDENTITY_MISMATCH`（`"volume identity unreadable"`）。但在 `evidence` 里
   **必须说清是"读不出来"而不是"变了"**——否则读者会把"未知"当成"已变"。
5. **没记基线 → 不报。** 无法比较就别说漂移；这也让"P2 之前不发"有了精确含义（P2 之前**没有基线**，
   所以不发）。

### 58.4 修法

1. 新模块 `caps/acl.py`：只读 DACL → 规范形态（`owner` + 有序的 `(type, flags, mask, sid)`）+ `acl_digest`。
   读不出来返回"为什么"，不抛。
2. `data-root add` 在登记时**记录观测到的基线**（`acl_baseline_json` 这一列至今没人写过）。
3. `doctor` 的 D1 数据根检查增加漂移判定（`warning` / `repair`）。
4. **删掉那条例外**：`test_l0_consistency.py` 的 `RESERVED_DIAGNOSTICS` 去掉 `DATA_ROOT_ACL_DRIFT`，
   `AGENTS.md` 的"尚未实现"里把"ACL 基线（`DATA_ROOT_ACL_DRIFT` 的发射）"改成"ACL **写入**/broker 一侧"。

### 58.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S58.1 只读 ACL 读取器 | ✅ | `caps/acl.py`：`get_named_security_info` + `GetAce` + `ConvertSidToStringSidW`；读不出来返回原因，不抛 |
| S58.2 登记时记录基线 | ✅ | `data-root add` 写 `acl_baseline`；`docs/schema/README.md` 无需改（列早已存在） |
| S58.3 `doctor` 发射 | ✅ | `DATA_ROOT_ACL_DRIFT`（`warning`/`repair`），evidence 区分"变了"与"读不出来" |
| S58.4 例外删除 | ✅ | `RESERVED_DIAGNOSTICS` 不再含它；守卫 `test_no_diagnostic_code_is_promised_without_a_path_that_can_emit_it` 现在要求它**真的可产生** |
| S58.5 测试 | ✅ | 见下：漂移、无基线不报、负向对照、顺序敏感、失败即数据 |
| S58.6 测试 + 回写 | ✅ | `pytest cli/tests` 751 → **756 项**；真机验收通过（`data-root add` 现在会读真实 `D:\env` 的 ACL，**无需提权**） |
| S58.7 场景进验收面 | ✅ | 新增 **`C-034`**（§9 的场景表）：在此之前这个行为会**带着零个场景**上线；场景数 107 → **108**，语料**外科式**重生（只有 `scenario_ledger.json` 变，doctor 语料逐字节不变——那正是"没基线不报"的验证） |

**测试的五个方向**（每个都对应一条判据，不是凑数）：

1. **基线缺席 → 不报**（"无法比较就不说漂移"）；
2. **基线过期 → 报**（伪造一条与现况不同的基线，确定性且不需要改 ACL）；
3. **负向对照：基线是刚读的 → 不报**（没有它，"凡有基线就报"也能通过）；
4. **顺序敏感**：把同一组 ACE **重排**后 digest 必须不同（钉住决策 3，而不是写着"不排序"却悄悄排序）；
5. **失败即数据**：对不存在的路径读 ACL 不抛异常，返回原因。

**守卫确实会红**：把漂移判定短路 → 第 2 条测试报红；把 digest 改成对 ACE 排序后比较 → 第 4 条报红。

### 58.6 明确不做

1. **不写 ACL。** 把基线"强加"回目录需要 `WRITE_DAC` 与 broker，属 P2 的写一侧；本阶段只读。
2. **不新增 reason code**（决策 4）。
3. **不把真实 SID 写进任何被提交的文件**（58.2）。语料里**不新增** ACL fixture：既有 doctor 语料的
   数据根**没有**基线，所以那条路径不发诊断，**语料逐字节不变**——这也是本阶段"不发"语义的直接验证。
4. **不对 CLI root 自己的目录做基线**（规划 §8.2 的那张表是 root 布局的必需 ACL，属 P2 的受保护模式），
   本阶段只做**数据根**（`DATA_ROOT_ACL_DRIFT` 这个名字本来就是数据根的）。

### 58.7 实施顺序

1. 先探针：确认 `ctypes` 能读、不需要提权、不需要新依赖（58.2）——**先测再设计**。
2. 写 ADR-0023（`repair` 的含义、顺序敏感、失败即数据）。
3. 写 `caps/acl.py` + 测试 5 条。
4. 接进 `data-root add` 与 `doctor`。
5. 删例外、改 AGENTS、重生语料（核对**逐字节不变**）、验红、跑全量 + 旧切片 + 真机验收、提交。

## 59. 第 59 阶段：真实获取路径第一次真的跑起来（`source resolve` 在线 + 真实下载 + 真实摘要校验）

### 59.1 这一阶段要解决什么

`AGENTS.md` 的"尚未实现"里有一条一直是这个形状：**"真实工具的下载与验证（机制已通，尚未对真实上游执行过）"**。
机制在代码里（`caps/sources.py` 的在线分支 + `caps/backends/https_artifact.py`），但它**从来没跟真实上游说过话**——
所有测试都注入 `fetch_text`，所有语料都是本地 fixture。一条"机制已通"的声明，在没有跑过之前，
只是一条**关于代码的**声明。

### 59.2 实测（先探针后动手，三步）

1. **网络可达性**：本机 `github.com` 可达（HTTP 200），`releases.python.org` 不通（TLS 失败）。
   `static.rust-lang.org` 可达。→ 阶段**能做**，但不能假设随便哪个上游都能做。
2. **独立读数**：用 `urllib` 直接取两份已发布的校验和文件，确认它们是**真的**且是**真格式**：
   * CMake `cmake-3.31.6-SHA-256.txt`：1646 字节，`sha256sums` 格式（摘要 + 文件名）；
   * rustup `rustup-init.exe.sha256`：65 字节 = 64 位十六进制 + 换行，`single` 格式。
3. **AIROOT 自己的在线分支**（`resolve_source` 不带 `offline_checksum_path`）对这两个上游：
   都能解析出 `backend_id=https_artifact` 与一个**真实摘要**，且 rustup 的摘要与第 2 步独立读到的
   **逐字节一致**（`sha256:6f4bef66…db7e`）。这不是同义反复：一边是 AIROOT 的解析器，一边是我直接读的字节。

### 59.3 判据：**到哪里为止**，以及为什么

真实路径的终点**不是**网络，而是**审批**。实测：每个消费批准的命令都取 `--token-file`，
而 `cmd_approve` 也只**加载/校验** token——**P1 没有生产批准签发方**，唯一的签发方是
`cli/tests/fake_issuer.py`（`AGENTS.md` §7：`airoot approve` 永远不能凭空制造批准）。

所以本阶段把边界画在这里，并且**明确说出它**：

| 步骤 | 需要批准？ | 本轮 |
|---|---|---|
| `source resolve`（在线，真实上游取校验和并解析） | 否 | ✅ 真跑了 |
| `fetch`（真实下载，HTTPS-only + 拒绝降级重定向） | 否 | ✅ 真跑了（12 721 664 字节） |
| `verify`（对**上游发布的**摘要做 SHA256） | 否 | ✅ 真跑了（`verified: true`） |
| `stage` / `commit`（事务的提交点） | **是** | ❌ **没有做**，理由写在输出里：P1 没有生产签发方；在这里**造一个 token 让它看起来跑完，正是本项目拒绝的那种假** |

### 59.4 修法：把它做成**可复现且可选**的一步

网络不该进 `pytest`（套件必须保持自足：上游挂掉不能让 756 项变红）。所以它进了
**真机验收脚本** `cli/tests/real_machine_acceptance.py`，并且是**显式可选**的 `--online`：

* **不传** `--online` → 这一行打印 `not run (pass --online)`。**"没检查"必须看得见**，不能与"检查过且没问题"长得一样（§50 的教训用在真机脚本上）；
* **传** `--online` → 在线解析、真实下载、真实校验，并把**边界**打印出来（stage/commit 为何没做）；
* 5 条断言：解析是在线而非本地文件、期望摘要来自已发布校验和文件、**已发布摘要与下载字节相符**、
  回来的东西是像样的可执行文件（>500 KB，不是错误页）、摘要由字节算出（所以不可能"自己和自己相等"）。

### 59.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S59.1 可达性与独立读数 | ✅ | 见 59.2；两份校验和文件的字节数与格式都实测过 |
| S59.2 在线解析（两个上游） | ✅ | `rust-toolchain`（`single`）与 `build`（`sha256sums` 按文件名匹配）都解析出真实摘要 |
| S59.3 真实下载 + 真实校验 | ✅ | `rustup-init.exe` **12 721 664 字节**，`verify` → `verified: true`，摘要与上游发布值一致 |
| S59.4 可复现的可选步骤 | ✅ | `real_machine_acceptance.py --online`；不传时**打印 not run** 而不是静默通过 |
| S59.5 目录如实记录 | ✅ | `policy/sources.json` 的 `notes` 现在**逐条写明验证状态**：两个"verified online（并写明**哪些**步骤做过、哪些没做）"，`archive` 仍标注"未验证" | **§72 已改判**：那条"未验证"没有被留下来当装饰——量下来它的校验和来源根本不存在，于是按目录自己的成长规则**移除**（§72.2）。"逐条写明验证状态"这条要求保留，并升级成守卫：每条 `notes` 必须写明**怎么验的** |
| S59.6 测试 + 回写 | ✅ | `pytest cli/tests` **756 项**不变（本阶段没加 pytest 测试，**故意的**：网络测试会让套件不再自足）；真机验收两种模式都 PASS |

**本阶段没有新增 pytest 测试，这是一个判断而不是遗漏**：要测的东西要么是网络（会让套件不自足），
要么是已经在 `test_l2_backends.py` 里用注入 opener 覆盖过的（HTTPS-only、拒绝降级重定向、摘要比较）。
把网络塞进单元测试，换来的不是覆盖而是脆弱。

### 59.6 如实记录的边界与发现

1. **stage/commit 没有跑**，因为 P1 没有生产批准签发方。这是本阶段**最主要的发现**：
   "真实工具的下载与验证"这句话里，**下载与验证**现在是真的；**安装**仍被批准边界挡着，而那道边界属 P2。
   `AGENTS.md` 的措辞随之改准（不再说"机制已通"这种关于代码的话，而是说**哪几步跑过、哪几步没跑**）。
2. **只验证了两个上游的解析**，`archive`（7z）**仍未验证**，目录里如实标着——本阶段没有顺手把它也跑掉的必要，
   也不该在没跑的情况下把它写成已验证。
3. **CMake 的产物本体没有下载**（约 50 MB）：它的价值在 `sha256sums` 的**按文件名匹配**路径，
   那条路径在解析阶段就已经真跑了；为了"跑得更彻底"而拉 50 MB 不是一个好理由。
4. 依赖"本机能连上哪个上游"是**环境属性**：`releases.python.org` 在本机不通。这一点写在这里，
   因为它决定"下一个人换台机器时，同样的命令未必同样通过"。

### 59.7 实施顺序

1. 先测可达性与独立读数（59.2 的三步）——**先确认能做，再规划**。
2. 读实现，确认"下载/校验"与"提交"是**分开的单元**（`fetch`/`verify` 不取 token，`commit` 在事务里）——
   这决定了边界画在哪里是**诚实的**而不是偷懒的。
3. 把步骤加进真机验收脚本，**默认关闭**、关闭时**自报未运行**。
4. 真跑一次 `--online`，把**真实字节数**与结论写进本节。
5. 回写 `policy/sources.json` 的验证状态、改准 `AGENTS.md` 的措辞、跑全量 + 两种模式验收、提交。

## 60. 第 60 阶段：把"没做"写成"为什么没做、什么才能解锁"（`not_implemented` 的理由登记，含一次被自己证伪的审计）

### 60.1 这一阶段要解决什么

`agents/airoot.json` 的 `not_implemented` 一直是一个**六条字符串的裸清单**：`bootstrap`、`reconcile`、
`path backup`、`path restore`、`root adopt`、`root relocate`。它能表达"没有"，但表达不了 agent 真正会问的
两件事——**为什么没有**，以及**什么才能有**。理由其实存在，却散在三份文档的散文里（规划 §8.1.1、§9.8、
§15.1、§15.5、§19 与 `AGENTS.md` §8），有的措辞还停在更早的状态；而 agent 面的调用元数据——它唯一的
读者就是 agent——一个字都没说。

所以本阶段做两件事：给每条路径写上 `{category, why, unblocked_by}`，以及**把每条理由重新推导一遍**。
第二件才是这一阶段真正的活，也是唯一能发现"清单没说谎、它只是什么都没说"的办法。

### 60.2 形状

- `deferred` 与 `not_implemented` **同键**：守卫双向比较（清单里没有理由的、理由里没有清单的都报出来）；
- `category` 是**三个**值（为什么不是四个见 60.4）：`needs-admin`、`needs-decision`、`needs-capability`；
- `unblocked_by` 也是**三个**值：`p2-protected-state`、`p6-project-manifest`、`decision`；

**§82 已改判（这一节整段都是 §60 当时的形状）**：今天 `category` 是**两个**值（`needs-decision` 被删）、`unblocked_by` 也是**两个**值（`decision` 随它一起被删）。唯一落在 `needs-decision` 里的 `root adopt` 等的东西已被 **ADR-0025** 裁决，于是它改判成 `needs-admin` / `p2-protected-state`；`approve`/`install` 的 lane 也从 `decision` 挪到 `p2-protected-state`。**裁决把一个"等人"的类别变成了"等 P2"的类别**，类别本身就没有成员了。详见 §82。
- 守卫第二十三组**三项**：结构（键集 + 词表 + 非空 `why`）、两个词表**恰好等于**实际用法、
  跨登记表拼写一致、以及三个类别词在入口文档里有解释。

### 60.3 审计：六条理由逐条重新推导

| 路径 | 原来的理由 | 重新推导 | 出处 |
|---|---|---|---|
| `bootstrap` | P1 拒绝在系统位置建 root；`init_root` 是测试/引导辅助，真 bootstrap 要的 marker 与 ACL 布局是受保护状态 | 结论不变（`needs-admin`），但**理由只写了一半**：规划把它列为核心命令却**没有正文**，又让它成为 Protected machine mode 的前置；而"重新进入 Skill 后的对账"被挂在它名下，那一半今天就是 `doctor` + `discover` + `plan`。`why` 改为两半都写明 | 规划 §15.1、§8.1、§9.8 |
| `reconcile` | `covered-elsewhere`：与 `doctor`/`desired`/`plan` 重叠，单独一个动词会是同义词 | **改判 `needs-capability` / `p6-project-manifest`**：规划写的是 `reconcile <manifest>`，P6 交付清单把它排在 `project manifest`、`project binding` **之后**，而 **CLI 自己也这么说**——`forget` 的输出里有 `project_manifest_check: "not_implemented_before_p6"`。P1 不产出也不读项目清单，所以这个动词**没有能诚实消费的输入**。重叠是真的，但是**较小**的一半 | 规划 §15.1、§19、§15.5；`cli.py:582` |
| `path backup` | 它与写 PATH 归在一组；只读记录 PATH 不是它的语义 | 站得住，补出处：P2 交付清单里**点名**列着 `path backup/restore`，与 ACL 基线、单一 machine PATH、elevated broker 并列 | 规划 §19 |
| `path restore` | 恢复 PATH 是 machine PATH 写，要 broker | 站得住；补一句 ADR-0021 的**放宽方向到不了它**——恢复不是读，没有更弱的形式可发 | 规划 §19；ADR-0021 |
| `root adopt` | 接管已有 root 需要 copy/verify/switch 规则，而规则不存在；写规则就是改契约 | 站得住，出处写准：§15.5 只写它的**校验**，§8.1.1 只**点名**一次 copy/verify/switch 规则而从未规定，并把 adopt 与"恢复备份"并列为身份不匹配时的两种处置——所以 adopt **意味着什么**是裁决，早于它是代码 | 规划 §15.5、§8.1.1 |
| `root relocate` | 移动 root 会重写 registry 与 machine PATH 条目，是受保护提交而不是文件移动 | 站得住，出处写准：建新 marker → 复制并校验持久数据 → 切 active root，而切换要动 machine PATH，后者**只允许一个 AIROOT 条目** | 规划 §15.5；`AGENTS.md` §5 第 8 条 |

**六条里有一条是错的**（`reconcile`），两条的理由**只写了一半**（`bootstrap` 的对账那一半、
`root adopt` 的出处），其余三条**只是缺出处**。这就是裸清单的具体样子：它没有说谎，它什么都没说。

### 60.4 发现的缺陷：刚发明的第四个类别被自己的审计证伪

- 原来的守卫是 `used <= set(CATEGORIES)`。它抓得住**拼错**的类别，放行**声明了却没人用**的类别——
  而这不是假设：`covered-elsewhere` 与 `p4-real-backend` 在同一轮里同时是"已声明、零成员"。
  一个没有成员的类别，正是读者会遇到、却查不到用处的词。
- 改成**恰好相等**（两个方向都报），于是 `covered-elsewhere` 必须删。与它一起被删的还有
  `p4-real-backend`（没有路径在等真实后端）与 `p6-desired-convergence`（那是我按 P6 的主题猜的词；
  规划那份交付清单里排在前面的条目叫 `project manifest`）。
- **与场景台账的 `unchecked-invariant` 刻意不同**：那个值留着，因为"文档写了、代码实现了、没人看着"
  是**会复发**的形状（一次审计就撞见三例），删掉它等于连"为什么需要第五个值"一起删掉。
  `covered-elsewhere` 没有任何复发性证据——它唯一的成员刚刚被改判——所以它删。
  **这个区别本身是判断，所以写下来。**

### 60.5 顺手发现的文档腐烂（守卫第十六组漏掉的两条）

规范审查报告文末那一节自称描述**当前状态**，而它当时还写着：

- **ACL 基线（`DATA_ROOT_ACL_DRIFT` 的发射）属 P2**——§58 已经交付了读一侧；
- **真实 artifact"尚未对真实上游执行过"**——§59 已经真跑了（`rustup-init.exe` 12 721 664 字节）。

两条都改成了事实，并且**都进了 `REVIEW_FALSE_CLAIMS`**（守卫第十六组从此管它们）——"没有守卫读散文里的
否认"正是第十六组存在的理由，而它的表当时只有 `SKILL.md` 一条。第二条是**弱形式**，注释里写明理由：
产物存在只能证明那条代码路径在，那次**运行**是记录下来的实测（网络不进 `pytest`），所以守卫能禁的
只是"从没发生过"这句话本身。**一个假装能验证网络运行的守卫，才是那种不诚实的绿。**

### 60.6 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| `category` 改成 `covered-elsewhere`（已删的值） | 危险 | **红**（不在词表） |
| `root adopt` 的 `unblocked_by` 从 `decision` 改成 `p2-protected-state` | 危险 | **红**（`decision` 声明了却没成员） |
| `p2-protected-state` 改成 `p2-broker` | 危险 | **红**（与台账的 `p2-protected-state` 不是同一个字符串） |
| `p6-project-manifest`（台账没听过的阶段） | 对照 | **绿**——**没有可比较的对象**；一条永远能红的规则是坏的，一条永远绿的是装饰，所以这个方向也必须实测 |
| 两句原话分别塞回审查报告的**状态节** | 危险 | **各自红** |
| 同一句话塞到状态节**上方**（审查当时的快照） | 对照 | **绿**——那是历史记录，不归这条守卫管 |
| 空 `deferred` / 给未声明的路径写理由 / 类别拼错 / `why` 只有空白 / `unblocked_by` 不在词表 | 危险 | 每种都由 `_deferral_problems` 报出（结构检查的非空性） |

每次变异后都从备份恢复，并核对**逐字节相同**（JSON 仍可解析、报告文件仍是原来的字节）。

### 60.7 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S60.1 理由登记 | ✅ | `agents/airoot.json` 的 `deferred`：6 条 = `not_implemented` 的 6 条（守卫双向比较） |
| S60.2 六条理由逐条审计 | ✅ | 见 60.3；**一条改判**（`reconcile`），`why` 与 `unblocked_by` 随之一并更正 |
| S60.3 词表**恰好等于**用法 | ✅ | 守卫第二十三组；`covered-elsewhere` / `p4-real-backend` / `p6-desired-convergence` 删除 |
| S60.4 跨登记表拼写一致 | ✅ | 与场景台账共享的阶段名必须**同一字符串**；台账没有的阶段名不设约束（60.6 的对照行） |
| S60.5 入口文档解释词表 | ✅ | `AGENTS.md` §8 解释三个类别与三个解锁词，并写下被删掉的第四类**及其原因** |
| S60.6 两条文档腐烂 + 守卫 | ✅ | `REVIEW_FALSE_CLAIMS` 从 1 条变 3 条；危险方向红、历史快照方向绿 |
| S60.7 测试 + 回写 | ✅ | `pytest cli/tests` 756 → **759 项**（审计 72 → **73**）；切片与真机验收两种模式均绿 |

### 60.8 如实记录的边界

1. **`why` 的出处是纪律，不是守卫。** 六条 `why` 现在都写了它在规划里的位置，但"有没有写出处"没法用
   一条不会误判的正则去查（任意一个数字都能满足它），所以**没有为它造守卫**。这是缺口，不是已完成项。
2. **类别答的是"什么必须先发生"，不是"这件事多重要"。** `bootstrap` 的两半（受保护的创建 / §9.8 的对账）
   分属不同的"先发生"，一个词只能挂住约束更强的那一半；另一半写在 `why` 里。
3. **审计的对象是"文档说了什么"，不是"世界上为真"。** `root adopt` 被判为 `needs-decision`，依据是
   "规定它的那段文本不存在"——如果那段文本在别处而我漏了，结论就该改；改它的入口就是这里写下的出处。
4. **两套登记表仍是两套词汇。** 台账答"这个**场景**缺什么"（含 `undesigned` / `unchecked-invariant` /
   `none`——那是关于**证据**的值），登记表答"这个**命令**要先等到什么"。共享的只有阶段名。
   `decision` 在台账里没有对应值：被"缺一次裁决"卡住的是命令路径，不是场景。
5. **`reconcile` 的类别是这一阶段唯一被改变的事实**，而它原来是一个**主张**。主张被推翻，
   是这一阶段最有用的产出——比新增三项守卫更有用。

### 60.9 实施顺序

1. 先把六条路径**重新推导**一遍（60.3）——**先确认理由真假，再决定形状**；
2. 写 `deferred`；
3. 写守卫（结构 + 词汇 + 反装饰 + 跨表拼写）；
4. 验红，每个危险方向都要红，并留一个**不该红**的对照方向（60.6）；
5. 修掉审计路上撞见的两条文档腐烂，并给它们加守卫（60.5）；
6. 跑全量 + 切片 + 两种验收模式，回写计数，提交。

## 61. 第 61 阶段：复核台账里那 17 条从未被复核过的"缺某能力"判断（顺带抓出一个真缺陷：重复 `commit` 把已落盘的事务倒回起点）

### 61.1 这一阶段要解决什么

场景台账的 `blocked_by` 是**作者判断**：它说"这个场景缺 P2 / P4 / 某个尚未设计的能力"。§53 复核过其中一部分，并且**如实记下了它没有复核的部分**——`p2-protected-state`(10) / `p4-real-backend`(7) / `p3-native-indexer`(0) 那 17 条。§60 在**另一张登记表**（`agents/airoot.json` 的 `deferred`）上做同类审计时刚抓到一条错判，于是"那 17 条呢"从一句备忘变成了必须回答的问题。

实测它们长什么样：**17 条全是裸的 `{"blocked_by": ...}`**——没有 `note`、没有出处、没有解释。台账另外 46 条要么有证据指针，要么有 note，要么有证人；这 17 条是**17 个没人检查过的断言**。

### 61.2 方法：逐条重新推导，**能测的先测**

对每一条：读场景定义原文 → 在代码里找它期望的两个半边 → 能测的**先跑一次探针**，不能测的说清"缺的东西为什么没有对象"。探针清单（都是真实执行过的，不是阅读所得的印象）：

| 探针 | 量到了什么 |
|---|---|
| `PYTHONPATH --scope machine` 走两条路径 | CLI 路径在**构造请求时**就被拒；库路径（手工构造 `ExposureRequest`）走到计划构造器 |
| 同一 token 两次 `commit`（两线程同起） | `['APPROVAL_REPLAYED', 'committed']`，一条 tx、一个 active binding、integrity 干净 |
| 同一 token，赢家停在 `ACTIVE_BOUND` 后再让第二个调用者跑 | 见 61.4——**这是本阶段最重要的读数** |
| `grep` 全树找 `revoked_at` / `approval_mode` / `zipfile` | `revoked_at` 无写入方；`events` 表无 `approval_mode` 列；全树无解压实现 |
| `_accessible` 与 backend 声明的读法 | 可访问性只有"本进程"这一个答案；`reversible` 已是九个冻结声明之一 |

### 61.3 结论表

| ID | 原判断 | 复核结论 | 依据 |
|---|---|---|---|
| `P-001` | `p2-protected-state` | **维持** | ACL 不存在，"被 ACL 拒绝"没有可执行对象；"doctor 报告证据"那半已交付（§58），但它测的是**漂移**不是**拒绝** |
| `P-002` | `p2-protected-state` | **改判 `none` + 证据** | Zone W 可写而机器级 `where` 不发现它，正是 §56/ADR-0022 的执行点，已有测试断言；第三句"不能被 machine PATH 发现"今天**真空成立**（没有 machine PATH 写入功能） |
| `P-009` | `p2-protected-state` | **维持** | UAC 不存在；"中断可恢复"那半已交付，缺的只是 UAC 取消这个入口 |
| `P-010` | `p2-protected-state` | **维持** | R 不受保护之前，无法区分"核心没提供直接写 R 的操作"与"它恰好没写"；这句话写进 note，不假装测过 |
| `P-012` | `p2-protected-state` | **改判 `undesigned`** | 自动批准**没有生产方**（P1 禁止核心铸造批准）；且 `events` 表**没有 `approval_mode` 列**——§13.3 说"审计里可区分"，今天区分不了 |
| `P-015` | `p2-protected-state` | **改判 `undesigned`** | 撤销那半**不可达**：`revoked_at` 全树只有"读它"与"声明它"两处，**没有写入方**；revision 那半已交付并已被断言 |
| `P-016` | `p2-protected-state` | **改判 + 修实现** | 见 61.4：它不需要 P2，而且**实现是错的** |
| `P-017` | `p2-protected-state` | **改判 `undesigned`** | "human 必须记录 `approved_by_sid`"实现了，但被 schema 挡在前面（纵深防御）；"与 issuer 证据不匹配"**没有第二个操作数**（P1 没有生产签发方） |
| `C-011` | `p2-protected-state` | **维持** | 可访问性只有"本进程"一个答案（`_accessible` 的 docstring 自己写着）；它要的机器级索引属 P3，而那个索引器的初始枚举本身要 broker（ADR-0020）——所以恢复顺序先 P2 |
| `C-025` | `p2-protected-state` | **改判 + 见 61.5** | 契约要 exit 8；实测**从来没有坏过**，但两条路径的答案不一致 |
| `T-001` | `p4-real-backend` | **维持** | 下载+摘要校验真跑过（§59），本地失败也不留 stage；缺的是**中断**那种失败的诊断 |
| `T-003` | `p4-real-backend` | **维持** | 全树没有解压实现（`zipfile`/`tarfile` 都没被导入） |
| `T-004` | `p4-real-backend` | **维持** | 与 T-001 同族：没有空间证据的生产方，也没有把 `ENOSPC` 变成带证据失败态的路径 |
| `T-005` | `p4-real-backend` | **维持** | 占用已存在的 store 路径是**被诊断**的（`INSTANCE_CONFLICT` 是 `AirootError`）；`shutil.move` 的**共享冲突**是 `OSError`，runner 不转换它 |
| `T-013` | `p4-real-backend` | **改判 `none` + 新测试** | 它判的是**声明**，不需要真实 artifact：`reversible` 是九个冻结字段之一，`low_risk_eligible` 要它 |
| `T-016` | `p4-real-backend` | **改判 `none` + 证据** | 崩溃对账不需要真实 artifact；`test_every_boundary_is_recoverable` 按状态参数化（含 `ACTIVE_BOUND`），断言恰好一个 active binding 且 repair 幂等 |
| `T-017` | `p4-real-backend` | **改判 `none` + 证据** | `gc` 已在**真实 payload** 上跑过；"不删仍有引用的 payload"与"重试幂等"都有证人 |

**十七条里四条错判**（P-002 / P-012 / P-015 / P-016 / P-017 / T-013 / C-025 之中，改变了"缺什么"这个判断本身的是 P-002、T-013 与 P-016 的缺陷，另有 P-012/P-015/P-017 从"等 P2"改成"根本没东西可等"、C-025 从"等 P2"改成"今天就能测"）。

### 61.4 抓到的真缺陷：重复 `commit` 会把已落盘的事务**倒回** `PROPOSED`

探针是确定性的，不用抢时序：让赢家 `commit` 在 `ACTIVE_BOUND` 处停下（`FaultInjector`），**然后**用同一个 plan 与同一个 token 让第二个调用者在自己的连接上再跑一次。读数：

```text
winner stopped at: ACTIVE_BOUND   journal_seq: 7
loser  ended at:   FINALIZED      journal_seq: 10
tx rows: [('tx/fake-tool/…', 'FINALIZED', 10)]
event states: PROPOSED APPROVED FETCHED VERIFIED STAGED COMMITTED REGISTERED ACTIVE_BOUND
              PROPOSED APPROVED FETCHED VERIFIED STAGED COMMITTED REGISTERED ACTIVE_BOUND
              EXPOSED VERIFIED_AGAIN FINALIZED
PROPOSED count: 2        generation: 2
```

一条 transaction 的审计轨迹里出现了**两次 `PROPOSED`、两次 `ACTIVE_BOUND`**，generation 白涨一次。三个后果，一个比一个重：

1. **审计轨迹对过去说了假话**：它声称事务回到过起点，而事实上绑定已经改过了；
2. **`ACTIVE_BOUND` 被通过了两次**，而 §5.6 把这个状态定义为"**唯一**可以改变 active binding 的提交点"——每个事务只应该经过它一次；
3. **journal 是恢复权威**（`AGENTS.md` §5.4、§14.1），把它改写回更早的状态不是"重试"，是**抹掉已发生的事实**：如果此时断电，恢复会从一个从未存在过的状态开始分类。

根因一行就能说清：`journal.create` 的 transaction id 由 plan+approval 推导，所以第二次 `commit` 落在**同一条** transaction 上，而它**无条件地**又写了一份全新的 `PROPOSED` envelope 与行。**修法**：`create` 改成 **get-or-create**——同一条记录已经在磁盘上，就返回它，让调用者从**它真实的持久状态**继续。这与 `resume` 从同一条记录出发的行为一致（`resume` 的注释本来就写着"resume 永不倒带 journal"），而"第二个调用者"能走到这里的前提是批准**尚未被消费**（`verify_approval` 查的就是这件事），所以"有人又跑了一次 commit"最诚实的读法是**续做**。

**为什么这不是"放宽权限"**（ADR-0021）：它不改变任何许可，不改任何状态转移表（16 状态 / 62 条边一条没动），只是让 journal 不再被倒着写。

### 61.5 C-025：审计差点"修好"一个**没有坏**的东西

契约（§13.7）要的是 exit 8。我读 `build_reference_plan` 时先看见 machine 门在请求校验之前，判定为"顺序缺陷"并改了顺序——**然后探针推翻了这个前提**：

* **CLI 路径**：`request_from_entry` → `spec_from_entry` → `validate_spec` 在**计划构造之前**就拒绝了禁用变量。所以 C-025 在真实命令路径上**从来没有坏过**；
* **库路径**：`ExposureRequest` 可以被直接构造，那条路上门确实在前面，会给出 5（"去提权"）——对一个**在每个 scope 都非法**的请求，这是个会把人引向错误方向的答案。

所以修法保留（两条路径现在给同一个答案），但**docstring 按实测改写**：不再写"契约被违反"，而是写"CLI 路径本来就对，库路径与它不一致"。测试同时锁两条路径，并额外断言一个**合法**的 machine 请求仍然得到 5——顺序改了不等于把门吞掉。

**教训写在这里**：只读场景点名的那个函数，会把没坏的东西改掉。探针（两条路径都跑一次）才是把"我以为"变成"我量到"的那一步。

### 61.6 新守卫：声明"缺东西"就必须写下为什么

规则（落在台账的结构校验 `evidence_problems` 里）：`blocked_by` 不是 `none` 时**必须**有 `note`；`undesigned` 的 `no_witness_reason` 也算解释（它本来就是针对这条判断的散文）；`witness` **不算**——指针说的是"哪条测试会红"，不是"为什么缺"。

**红证明是立刻发生的**：规则上线后第一次运行就在**真实语料**上打到 5 条（C-007 / C-009 / C-028 / S-027 / T-017），逐条看、逐条补。其中 T-017 那次的形状值得单独记：它"又变回 `p4-real-backend`"的原因是**重复字典键**——我新增的 `T-017` 块与原地那行同时存在，Python 静默取后者。**长表格里最危险的一类编辑错误**，而它是被这条新规则抓出来的（不是被我看见的）。

### 61.7 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S61.1 十七条逐条重新推导 | ✅ | 见 61.3；探针读数见 61.2 |
| S61.2 三条改判为"今天可测" | ✅ | P-002（`none`+证据）、T-013（新测试 `test_T013_...`）、T-016/T-017（`none`+证据） |
| S61.3 三条改判为 `undesigned` | ✅ | P-012（缺 `approval_mode` 列）、P-015（`revoked_at` 无写入方）、P-017（没有签发方身份证据） |
| S61.4 **修掉重复 commit 的倒带** | ✅ | `journal.create` 改 get-or-create；`test_l1_transaction.py#test_P016_...` 断言 `PROPOSED`/`ACTIVE_BOUND` 各一次、一条 tx、一次消费、integrity 干净；去掉 get-or-create 立刻变红 |
| S61.5 C-025 按实测改正 | ✅ | docstring 写实测；`test_l1_exposure.py#test_C025_...` 锁两条路径 + 合法请求仍得 5 |
| S61.6 新守卫 + 真实语料红证明 | ✅ | 规则上线即打到 5 条真条目并逐条补齐；内存变异证明它仍能红 |
| S61.7 计数与语料回写 | ✅ | evidenced **45 → 48**、uncited **63 → 60**；测试 **759 → 762**；审计 **73 项不变** |

### 61.8 如实记录的边界

1. **复核的是"文档说了什么"，不是"世界上为真"**——与 §60 同一条边界。`P-010` 尤其：它的两半都不可测，我把这件事写成 note，而不是造一条测不出东西的测试。
2. **错判率值得记住**：17 条里 7 条改了"缺什么"的结论（P-002/P-012/P-015/P-016/P-017/T-013/C-025），另外 10 条维持并补上"哪一半已交付"。这**不**意味着台账剩下 60 条也有同样的错判率——只有被复核过的那些才知道自己错没错。反过来说，"作者判断"这类字段不做复核就会漂，这一点现在有 7 个实例支撑。
3. **三件没修的事，本轮只把它们写成可读的事实**：(a) `events` 表缺 `approval_mode` 列，所以 §13.3 的"审计里可区分"今天不成立；(b) `revoked_at` 没有写入方，`APPROVAL_REVOKED` 是死分支（没有 revoke 这个操作）；(c) artifact runner 只捕 `AirootError`，`OSError`（中断下载 / 磁盘满 / 文件被锁，即 T-001/T-004/T-005）会直接穿出去。**这三件都该有自己的阶段**，而不是在审计轮里顺手改掉。
4. **`p3-native-indexer` 仍然零成员**：我把它当作 `C-011` 的候选值考察过，结论是 `C-011` 仍应先记 P2（ADR-0020 把 USN 索引器的初始枚举绑在 broker 上），所以那个值保持零成员并保留——与 `unchecked-invariant` 同一处置，理由也同一句：删掉它等于删掉"为什么需要这个值"。
5. **审计项的计数口径**：新规则住在 `scenario_ledger.py` 的结构校验里，而"73 项常驻审计"数的是 `test_l0_consistency.py` 的检查函数，所以那个数字不变。它是常驻审计，只是宿主文件不同——写下来，免得下次有人以为"73 没变 = 没加检查"。
6. **`ACTIVE_BOUND` 的记账口径**：修好之后，同一个 plan+token 的第二次调用会**续做**而不是重开，所以"一个事务恰好经过一次 `ACTIVE_BOUND`"现在是被断言的性质，而不是巧合。契约 §14.1 的措辞（"唯一提交点"）不需要改：它说的是**哪个状态可以改绑定**，而修的是"不要把一个事务倒退回去再经过它一次"。
7. **验红脚本本身踩了一个坑，记在这里免得下次再踩**：本阶段的验红是"改文件 → 跑测试 → 从备份恢复"，而脚本用的是 `read_text()` / `write_text()`。在 Windows 上那是**文本模式**：读进来时 `\r\n` 被规范化成 `\n`，写回去时 `\n` 又被翻译成 `\r\n`。于是恢复出来的 `environment.py` / `db.py` **一个字节都没改，却整文件变成了 CRLF**（`git diff --stat` 各报 848 与 1786 行），而 `exposure.py` / `journal.py` 的真正改动被埋在全新的行尾里。发现的时机是提交前的 `git diff --stat`——**行数多得不像自己改过的东西**就是那个信号。修法：`git checkout --` 掉两处纯行尾噪声，另两处按字节把 `\r\n` 折回 `\n`。**下次的规矩**：验红脚本一律用 `read_bytes()` / `write_bytes()`，或者 `open(..., newline='')`；`.gitattributes` 是 `* -text`，所以行尾噪声会被**原样提交**，不会有人替你纠正。

### 61.9 实施顺序

1. 先把 17 条**逐条重新推导**，能跑探针的先跑（61.2）——**先量，再判**；
2. 把结论写进台账（每条一个 `note`，改判的改 `blocked_by` 并给证据或 `no_witness_reason`）；
3. 只对**已被探针证明是真的缺陷**动实现（本阶段只有一处：`journal.create`）；
4. 给"声明缺失必须说为什么"加守卫，并让它在**真实语料**上先红一次（61.6）；
5. 跑全量 + 切片 + 两种验收模式，重生语料，回写计数，提交。

## 62. 第 62 阶段：文件系统说"不"的时候，安装路径要给出诊断而不是抛异常（T-001 / T-004 / T-005）

### 62.1 这一阶段要解决什么

§61 那轮审计把三条场景**留在了原地**并写下理由：`T-001`（下载中断）、`T-004`（stage 后磁盘空间不足）、`T-005`（commit 时目标被锁）都需要真实 artifact，而"真实 artifact"已经交付——缺的是**失败路径**。当时的原话是："artifact runner 只捕 `AirootError`，`OSError` 会直接穿出去"。这一阶段就是去**量**那件事，然后修。

### 62.2 先量：三处探针，三个缺陷

| 探针 | 读数 | 判定 |
|---|---|---|
| 注入 opener，流到一半抛 `ConnectionResetError` | `INSTALL_IO_FAILED`（当时的 `PROVENANCE_FAILED`），**部分文件已删** | 这一路本来是对的 |
| 注入 opener，流到一半抛 `http.client.IncompleteRead`（服务端声明 `Content-Length: N` 却少发字节——**真实截断**的形状） | 原始 `IncompleteRead` **逃出 `fetch`**，且**部分文件留在磁盘上** | **缺陷 1**：`HTTPException` 不在捕获列表里，清理分支因此从未执行 |
| 真实 backend（`portable_file`）而 `store` 是一个**普通文件** | 原始 `FileExistsError` **逃出 `commit()`** | **缺陷 2**（T-005）：journal 把事务留在 `STAGED`——一次**已经被处理**的失败，在 `doctor` 眼里却是"待恢复" |
| 真实 backend，`stage` 抛 `OSError(ENOSPC)` | 原始 `OSError` **逃出 `commit()`** | **缺陷 3**（T-004）：没有失败记录、没有回滚、stage 目录留在磁盘上 |

读数里还有一件结构性的事实：**`failure_cleanup` 从来没有被执行过**。两个 backend 都声明 `failure_cleanup="stage_only"`（九个冻结字段之一），而 `TransactionJournal.discard_stage` **在整个树里没有任何调用方**——一条被声明、被校验、被写进 plan、却从不兑现的承诺。

### 62.3 修法

1. **一个新 reason code：`INSTALL_IO_FAILED` → 退出码 2**。为什么不复用别的：3（损坏）不成立——没有东西损坏，出错前那一代还在，回滚会把它切回去；7（计划/来源问题）更不成立——磁盘满和被锁**没有**说明计划有问题。形状与 `CHILD_PROCESS_FAILED` 相同：**AIROOT 做了它那部分，环境没做**。按 ADR-0023 的先例（"不可读"与"已改变"用同一个码、靠证据区分），**没有**拆出 `DISK_FULL`/`FILE_LOCKED`：errno 与失败路径进证据，而调用者的下一步（腾空间／解锁／改权限，然后**重新出计划**）对三者是同一个动作。
2. **`fetch` 捕获 `http.client.HTTPException`**（与 `URLError`/`OSError`/`ValueError` 并列），保留"删掉部分文件"这一步，证据里加"收到多少字节"。同时把这一支的码从 `PROVENANCE_FAILED` 改成 `INSTALL_IO_FAILED`——**来源没问题，是传输断了**。
3. **两个 runner 都把 `OSError` 变成诊断**：`ArtifactRunner` 与 `SimulationRunner`（后者也 `copytree`/`os.replace`，同一个洞；"同一个 bug 在隔壁文件里"不是一个不同的 bug）。分类沿用既有规则：绑定还没动 → `FAILED`；动过 → `ROLLED_BACK`。
4. **兑现 `failure_cleanup`**：声明 `stage_only` 就 `discard_stage`，并把 `failure_cleanup=<声明> stage_cleaned=<结果>` 写进失败证据。清理**在记录失败之前**做，这样这个事实落在同一条记录里，而不需要在事务已经终态之后再发明一个事件类型去携带它（状态机不允许从 `ROLLED_BACK` 出去，为了一个清理标志去开一条边是本末倒置）。

### 62.4 为什么这个码是 2 而不是 7，以及**没有**拆细的理由

已在 62.3 第 1 条写明。补一句口径：`retryable` 字段在失败记录里仍然写 `false`，而这是**对的**——它问的是"这条 transaction 能不能重试"，而 `journal.create` 是 get-or-create（§61），失败后同一 plan+token 不会再开一条；要重试就得**重新出计划**。证据里明说了这一点，免得调用者把 `retryable=false` 读成"这件事没救"。

### 62.5 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| `fetch` 的 except 元组去掉 `http.client.HTTPException` | 危险 | **红**（T-001） |
| artifact runner 的 `except OSError` 改成 `raise` | 危险 | **红**（T-004 与 T-005 各一处） |
| `declared == "stage_only"` 分支不再调用 `discard_stage` | 危险 | **红**（T-004 断言 `stage_cleaned=True`） |

三个变异都用**字节级**读写施加与恢复（§61.8 第 7 条的教训），恢复后逐字节相同。

### 62.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S62.1 三处探针量出三个缺陷 | ✅ | 62.2 的表；每个读数都是真实执行得到的 |
| S62.2 `fetch` 捕获传输类异常 | ✅ | `http.client.HTTPException` 进捕获列表；`INSTALL_IO_FAILED` 替代 `PROVENANCE_FAILED`；部分文件照旧删除 |
| S62.3 两个 runner 诊断 `OSError` | ✅ | `ArtifactRunner` 与 `SimulationRunner`；分类沿用"绑定动没动" |
| S62.4 兑现 `failure_cleanup` | ✅ | `discard_stage` 第一次有调用方；证据里带 `stage_cleaned` |
| S62.5 守卫与文档同步 | ✅ | reason code 表 + `references/reason-codes.md`（退出码 2 行）；agent 面参考的"只列真实码"守卫当场抓到我把状态名 `ROLLED_BACK` 写进了码表 |
| S62.6 测试 + 回写 | ✅ | `pytest cli/tests` 762 → **765 项**；台账 evidenced **48 → 51**、uncited **60 → 57**（p4 从 4 条降到 1 条） |

### 62.7 如实记录的边界

1. **`T-003` 没有一起修**：安全解压要的是**解压器**，全树一个都没有（`zipfile`/`tarfile` 从未被导入）。写一个是新能力，不是修一条失败路径；它留在 `p4-real-backend`，note 里写明"§62 修了同族的三条，但没有顺手写一个解压器"。
2. **`INSTALL_IO_FAILED` 的退出码是判断，不是推导**：契约 §15.6 只给退出码的**含义**（2 = 降级/漂移），没有给"文件系统拒绝一次安装"该落哪一档。选 2 的理由是与 `CHILD_PROCESS_FAILED` 同形，并且它是唯一一个能诚实说"AIROOT 没错、环境没配合、出错前那一代还能用"的档。**如果这条判断是错的，改它的入口是这里**。
3. **回环服务器测试没有覆盖流式路径**：`test_https_backend_refuses_plaintext` 与 `test_https_backend_refuses_a_downgrading_redirect` 用的是**明文 http 回环**地址，而 `fetch` 在**scheme 检查**就拒绝了——所以那两个测试实际上断言的是同一件事，302 从未被发出。真正的流式路径在这之前**没有任何测试**（只有 §59 的真机在线跑过）。T-001 因此改用**注入 opener** 来驱动流式循环。那两个测试名不副实这件事没有改（改它们要起一个带证书的 TLS 回环，属另一件事），**记录在此**。
4. **`OSError` 的粒度是"整个安装步骤"**：`drive()` 里任何一个字节搬运步骤抛 `OSError` 都会落到同一个诊断，证据里带当时的 `tx["state"]` 来指认是哪一步。没有为"复制失败"与"重命名失败"分别造码——它们的恢复动作相同。
5. **`SimulationRunner` 的修复没有对应场景编号**：T-001/T-004/T-005 讲的是真实 artifact 路径。修它是**判断**（同一类 bug 不该只修被点名的那一处），不是被场景要求的；它由 `test_l1_transaction.py` 的既有中断面间接看着，没有为它新写测试。

### 62.8 实施顺序

1. 先探三个失败路径（62.2）——**先量，再决定修什么**；
2. 定 reason code 与退出码档次，并写下**为什么不拆细**（62.4）；
3. 改 `fetch`（捕获面 + 码 + 证据）；
4. 改两个 runner（`OSError` → 诊断；分类沿用"绑定动没动"）；顺带**兑现** `failure_cleanup`；
5. 三个场景各写一个点名测试，并逐个验红（62.5）；
6. 同步 reason code 表、agent 面参考、台账与计数，跑全量 + 切片 + 两种验收模式，提交。

## 63. 第 63 阶段：把"管家离场"那句话变成命令（`env forget --all`，C-028）

### 63.1 这一阶段要解决什么

台账里 C-028 的 `no_witness_reason` 是**整张表里最有意思的一条**：它不是"造不出证人"，而是"**证人造出来是错的**"——加一条 `--all` 不被接受的断言，只会在**别人把它实现出来**时变红，那正是"台账好看这件事自己生出一条自证断言"（与 C-007 同一处置）。所以诚实的做法不是给这条找一个证人，而是**把东西做出来**。

契约 §13.4 一直要求它：`airoot env forget <capability> --scope ...` **与** `airoot env forget --all`，两者都要"精确恢复旧值、绝不触碰 AIROOT 没写过的变量"。`environment_persist` 的建表注释也一直写着它：

```sql
-- "env forget --all" exact: the complete old value is recorded before the write, so
-- removing AIROOT never leaves a half-applied environment behind (draft §13.4).
```

**表是为这个命令建的，命令从来没写。** 与 §62 的 `failure_cleanup` 同一种形状：被声明、被写进注释、被验收，然后没有兑现。缺了它，管家模型的中心承诺——"删掉 AIROOT 不留痕"——做不到：留下的一批变量再也说不清是谁写的。

### 63.2 一次通过 ≠ 循环单能力形式：三处必须想清楚的地方

| 问题 | 结论 |
|---|---|
| **PATH 需要顺序吗？** | **不需要**。`remove_path_value` 移除的是**AIROOT 加进去的那些条目**（`added_path_entries` = 写进去的值减去写之前的值），所以两个能力按任何顺序撤销，留下的集合都一样。要移除的是每条记录"新增条目"的**并集** |
| **普通变量需要顺序吗？** | **需要，而且不能靠时间戳**。若两个能力写过同一个变量，前面那条的 `value` 就是后面那条的 `old_value`——按错的顺序撤销会把前一次写入**放回去**。而 `clock.isoformat` **刻意只到秒**（`replace(microsecond=0)`），"谁先写的"并不总能从 `written_at` 恢复。所以用的规则**不需要顺序**：要还原的值是**链首**，即那个**不是任何记录 `value`** 的 `old_value` |
| **漂移怎么判？** | 对着**每一条**记录判，而不是某一条：只有当活值**一条都不匹配**时，才说"机器上已经不是 AIROOT 写的那个值了" |

结果里 `shared_variables` 把"被多个能力写过"这件事**报出来而不是藏起来**：对普通变量它意味着还原目标取自链首，对 PATH 它意味着移除的是多个能力新增条目的并集。

### 63.3 形状

- `caps/exposure.py`：`ForgetAllResult` + `forget_all_persist(...)`；`forget_reference_persist` **原样保留**（单能力语义与输出一字未改，既有测试全绿）。
- `cli.py`：`env forget <external-id>` 的 `external_id` 变成可选，新增 `--all`；**两者同时给 → `INVALID_INPUT`(8)**，两个都不给 → 同样 8。理由写在代码里：一个问的是"别再管 java 了"，另一个问的是"管家走了，把我的环境还给我"，让其中一个**优先**于另一个是猜，所以拒绝。
- 结果文档 `operation: "forget_all_persist"`，带 `scopes` / `capability_ids` / `records` / `restored` / `removed` / `drifted` / `shared_variables` / `dry_run`。
- **没有记录时是成功而不是报错**（与单能力形式**故意不同**）："AIROOT 在这里什么都没记过"正是准备删掉 AIROOT 的人想听到的答案，报错等于说"这次检查本身失败了"。这个差异写在测试里，免得看起来像不一致。
- 记录**关闭而不删除**：`forgotten_at` 落上，`active_only=False` 还能查到——可审计的还原靠的就是这条历史。
- `agents/airoot.json` 增加一条 `["env", "forget", "--all"]` 调用元数据（问题、该读哪些字段），并把 `--all` 与 id 不可混用写进 notes。这条新登记**立刻触发了守卫**：`test_l1_agent_read_fields.py` 要求每条 invocation 都有"能产生该文档"的场景，于是那里补了 `--all --dry-run` 的录制（真跑会写 HKCU，被 conftest 的宿主守卫禁止）。

### 63.4 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 链首规则换成"取最新那条记录的 `old_value`"（两条记录**时间戳不同**） | 危险 | **红**（留下 `after-alpha`） |
| 同一个变异，但两条记录**共享同一秒** | 对照 | **绿**——而这一格是本轮最有用的读数：时间戳并列时 `max` 恰好也挑中链首，所以**只用并列时间戳的测试分辨不出这两条规则**。本测试的第一版正是那样写的，是这次变异把它抓出来的；现在它同时有"时间戳可分辨"与"时间戳不可分辨"两个用例，第一个负责让错规则变红，第二个负责证明答案不依赖顺序 |
| PATH 分支改成"写回 `old_value`"而不是按条目移除 | 危险 | **红**（用户自己的 PATH 条目被覆盖） |
| `--all` 与 `external_id` 同时给时不再拒绝 | 危险 | **红**（CLI 那条断言 8） |

三个危险方向都变了红；每个变异都用**字节级**读写施加与恢复，恢复后逐字节相同。

### 63.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S63.1 `forget_all_persist` | ✅ | `caps/exposure.py`；单能力路径未改动（既有测试全绿） |
| S63.2 PATH 并集 / 普通变量链首 / 对每条判漂移 | ✅ | 三处规则各有一个点名测试 |
| S63.3 CLI `--all` 与两种拒绝 | ✅ | `test_cli_env.py#test_C028_...`（exit 8 两次，且被拒绝的调用没有写任何东西） |
| S63.4 无记录时成功 | ✅ | `test_C028_forget_all_with_nothing_recorded_is_a_success_not_an_error` |
| S63.5 记录关闭不删除、事件留痕 | ✅ | 断言 `active_only=True == []` 且 `active_only=False` 仍是 4 条、事件里有 `FORGOTTEN` |
| S63.6 agent 面登记 + 场景 | ✅ | `agents/airoot.json` 新条目；`test_l1_agent_read_fields.py` 补录 `--all --dry-run` |
| S63.7 台账与计数回写 | ✅ | C-028 处置**删除**（改名点测试后 `status` 自己翻）；evidenced **51 → 52**、uncited **57 → 56**；测试 **765 → 771**（含链首规则那一条的两个用例） |

### 63.6 如实记录的边界

1. **"共享变量"这条路今天走不通，但规则必须是对的**：`PRIMARY KEY (capability_id, scope, variable)` 允许两个能力各写一行同一个变量，而**出厂白名单里只有 PATH 会被两个能力写**（`JAVA_HOME` 只由 java 声明）。也就是说普通变量的"链"规则现在**不可达**。我没有因此简化它——用一条只在可达时才正确的规则，等于把正确答案留给运气——而是把它写成一条**不依赖顺序**的规则并直接测它（`test_C028_forget_all_restores_the_chain_start_not_the_latest_write` 用手工记录构造了两条链）。歧义真正存在时（同一秒、多条链首）取最早的一条，把并列项写进 key 里，所以结果不依赖行序。
2. **`env forget --all` 的 CLI 测试只跑 dry run**：真实调用会写 `HKCU\Environment`，而 `conftest.py` 的宿主守卫禁止测试这么做。还原语义在 `test_l1_exposure.py` 用注入 store 覆盖——这个分工本来就是这个文件 docstring 写着的规矩，不是本轮偷懒。
3. **`--variable` 与 `--all` 不能合用**：`--all` 的语义是"全部"，加一个变量过滤就变成"全部里的一部分"，那是第三个问题，没有契约要求它。合用时给 `INVALID_INPUT` 而不是默默忽略。
4. **没有做的事**：单能力形式的"没有任何记录 → `ENVIRONMENT_PERSIST_NOT_FOUND`"语义**没有**跟着改（两条命令回答的问题不同，见 63.3）；machine scope 的记录仍然写不出来（P2），所以 `--all` 里的 machine 分支今天只能是 `PRIVILEGE_REQUIRED`——`_store_for_scope` 本来就那样，没有为它造假路径。
5. **这一轮改动没有 schema**：`forget_all_persist` 的文档没有发布 schema（`env forget` 的输出本来就没有），所以没有 golden fixture 变化——语料的 diff 只有台账那一份，是 C-028 翻成 evidenced 带来的。

### 63.7 实施顺序

1. 先确认契约与表注释都要求它（§13.4 + `ddl.sql`），再确认缺的是**命令形式**而不是语义；
2. 想清三处顺序/漂移问题（63.2）——**先把规则想对，再写代码**；第一版"循环单能力形式"就是在这里被否掉的；
3. 写 `forget_all_persist` + 结果文档；单能力路径不动；
4. 接 CLI：`external_id` 变可选、加 `--all`、两种混用拒绝；
5. 写点名测试（含手工构造的链、dry run、无记录、CLI 拒绝），逐个验红；
6. 补 agent 面登记与场景录制，删掉台账处置，重生语料、回写计数，跑全量 + 切片 + 两种验收模式，提交。

## 64. 第 64 阶段：给"我已经有的那个文件"一条入口（`adopt --mode import`）

### 64.1 这一阶段要解决什么

§63 挑的是"机制已交付、命令没写"的那一类。这一阶段是同一类的第二条：`adopt --mode import`。它自己的**错误信息**就是证据——那条拒绝说的是

```text
import/recreate need the P4 portable transaction
```

而那个事务**早就交付了**：真实 backend（`portable_file` / `https_artifact`）、sha256 校验、stage、commit、绑定、`gc`，§21.5 全套。也就是说这句话在某个更早的阶段之后就变成了**过时的事实**，而它一直挂在一条拒绝路径上。这正是 §60/§61 反复抓到的那一类腐烂。

再量一遍"今天到底有没有别的入口"，因为 §60 的教训是**先确认它不是同义词**：

| 命令 | 能不能装一个本机已有的文件 |
|---|---|
| `plan build <capability>` | 不能：产出的是 `fake_fixture` 的**模拟**计划 |
| `plan build --source-json <doc>` | 不能：`--source-json` 只接受 `source resolve` 的产物，即**从可信上游取来**的 artifact |
| `install <plan_file>` | 不能：它执行计划，不产生计划 |
| `adopt <dir> --mode reference` | 不能：登记外部引用，**不拥有**，`store/` 里什么都没有 |

所以没有入口：v1 能拥有的 payload 只能来自可信上游，用户**手里已经有的**那个文件没有路可走。

### 64.2 形状：产出计划，不越权安装

规划 §15.5 写的是"`adopt --mode import` 通过 **plan/approval** 将可验证的 portable 对象复制到 `tools`"。所以这一阶段交付的是**计划**，而不是安装：核心永远不铸造批准（`AGENTS.md` §7），而 `approve` + `install` 已经能驱动任何 backend（`_runner_for`）。`import` 补的是**入口**。

两个身份都不发明：

* **`--capability` 是入参，不是发现**。`classify_object` 读的是"数据根的直接子目录"，而松散文件没有这个上下文。P1 对**观察不到的身份**一贯只接受显式输入（`machine_id`/`session_id`/`project_id`，规划 §17），这里照同一条规则办；
* **`--version` 缺省记 `unversioned`**，并在文档里说明"文件里没有版本证据"（`metadata.import.version_source`）。不编一个 `1.0.0` 出来。

### 64.3 provenance：digest 不是来源

`create_artifact_plan` 会把 `requested_by` 填进 `source.provenance.publisher`。对**导入**来说那是凭空造一个出版者：文件是调用者递过来的。所以 `provenance` 被改成 `{"source_id": "local-import", "publisher": null}`，而"这个摘要只钉住这些字节、不证明它们来自哪里"这句话写进 `metadata.import.note`——**不是**写进 `provenance` 旁边，因为 `source.provenance` 在已发布 schema 里是**封闭对象**（`additionalProperties: false`，只允许 `source_id`/`publisher`/`retrieved_at`）。

这一点是本轮真正学到的形状：第一版把 `note` 放进 `provenance`，于是核心**拒绝了自己产出的文档**，报 `SELF_VALIDATION_FAILED`。守卫按设计工作了——自校验失败是**实现缺陷**，不是可以挥手放行的东西；修的是实现（把说明搬到 `metadata`，那里 `additionalProperties: true`），不是 schema。

### 64.4 顺带修正的一条过时事实

`--mode recreate` 的拒绝理由跟着改了：它不再是"需要 P4 portable transaction"（那个事务现在被 `import` 用着），而是规划 §15.5 的原文——按版本与项目声明**重建 runtime/环境**，属 **P5**。台账里 C-009 的判断随之改准：`import` 那一半已交付、`recreate` 那一半仍缺，**证人换成 `test_adopt_recreate_is_not_implemented`**。

### 64.5 一个差点被"散文"骗过去的测量

C-009 在这一轮里**两次**被误报成 `evidenced`，两次都是**散文**干的：

1. 新测试的 docstring 写了"the remaining half of C-009"；
2. 把这句话改掉、解释"为什么不能写编号"时，解释里**又写了那个编号**。

台账的 `status` 是**测量**（"有没有测试点名它"），而扫描是**逐文件的**——所以任何一行散文都能把它翻成 evidenced，哪怕那条测试断言的正是"这一半**还是缺的**"。这正是台账存在的理由（自证的绿）。处置：**在那个文件里连编号都不写**，把"为什么不能写"写进本节；`status` 回到 `uncited`，处置与证人保留。

### 64.6 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| `--capability` 缺失时不再拒绝 | 危险 | **红**（"needs --capability"断言） |
| 路径形状的 capability（`../../evil`）不再拒绝 | 危险 | **红** |
| 目录不再被拒（改成继续走文件路径） | 危险 | **红** |
| `provenance` 里塞回 `note` | 危险 | **红**——但**红在 `SELF_VALIDATION_FAILED`**，也就是核心拒绝自己的产物。这一格是**实测**过的（64.3 记的就是它），不是想象 |

三个危险方向都变了红，全部按**字节**施加与恢复。

### 64.7 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S64.1 先确认没有同义词 | ✅ | 64.1 的四行表；`plan build --source-json` 只吃上游来源文档 |
| S64.2 `adopt --mode import` 产出真实 artifact 计划 | ✅ | `cli.py` 的 `_adopt_import`；`backend_id=portable_file`，计划落盘 |
| S64.3 全链路：plan → approve → install | ✅ | `test_cli_steward.py#test_adopt_import_plans_and_installs_a_script_free_file`：**真实 payload 进 `store/`**、`tool list` 报 active、**源文件一字不动** |
| S64.4 身份不发明 | ✅ | `--capability` 必填、路径形状被拒、缺 `--version` 记 `unversioned` 并说明来源 |
| S64.5 provenance 诚实 | ✅ | `publisher: null` + "attests no origin"；`SELF_VALIDATION_FAILED` 那一次是守卫抓的 |
| S64.6 修正 `recreate` 的过时理由 + 台账改准 | ✅ | 证人换成 `test_adopt_recreate_is_not_implemented`；C-009 的 note 重写 |
| S64.7 agent 面 + 计数 | ✅ | `agents/airoot.json` 新增 `adopt --mode import` 调用元数据；`test_l1_agent_read_fields.py` 补录该场景；测试 **771 → 774** |

### 64.8 如实记录的边界

1. **`import` 只吃单文件**。`portable_file` 的 `stage` 复制**一个文件**（`artifact.path.name`），所以 `adopt --mode import <dir>` 被拒并指向 `--mode reference`。目录型 payload 需要一个新的 backend（"把整棵树 stage 进 store"），那是新能力，不是这一轮的接线。
2. **`--capability` 不校验冻结清单**。`plan build` 今天也不校验，所以这里**照它办**而不是顺手加一条它没有的规则。后果是 `--capability 任意名` 能产出一个计划；把它变成一条规则（"install 目标必须是冻结能力"）需要同时改 `plan build`，属**另一个判断**，记在这里。
3. **没有跑真实 approve/install 的 `--mode import` 到真机**：全链路测试用的是测试签发方（`cli/tests/fake_issuer.py`），因为 P1 没有生产签发方——与 §59 同一条边界，不是新的。
4. **`--mode recreate` 仍是 `UNSUPPORTED_BACKEND`**，现在指向 P5 而不是 P4；C-009 因此**仍是 `undesigned`**（evidenced 52 / uncited 56 不变）——这一轮让台账**更准**，而不是更好看。
5. **`adopt --mode import` 没有独立 schema**：它的文档就是 `plan`（已发布 schema，`validate_self` 通过），外加 `plan_file`/`required_action`/`reason_code` 三个 CLI 字段。所以这一轮**没有** golden fixture 变化，语料 diff 只有台账那一份。

### 64.9 实施顺序

1. 先量"有没有同义词"（64.1）——**先确认这条路是空的**，再动手；
2. 读 `create_artifact_plan` 与 `_runner_for`，确认 `import` 只是**接线**而不是新事务；
3. 写 `_adopt_import`：拒绝两档不该走的输入（目录、路径形状的 capability），产出计划；
4. provenance 按实测改（`SELF_VALIDATION_FAILED` 那次是守卫教的），计划重新哈希；
5. 写四个点名测试（正面全链路 + 两种拒绝 + `recreate` 证人），逐个验红；
6. 修正 `recreate` 的过时理由、改准台账、补 agent 面与场景、回写计数，跑全量 + 切片 + 两种验收模式，提交。

## 65. 第 65 阶段：让审计能说清"这次批准是谁给的"（`events.approval_mode`，迁移 v6）

### 65.1 这一阶段要解决什么

§61 把 P-012 的 `no_witness_reason` 写成了一句可直接检验的话：**"证人要断言一条不存在的列"**——`events` 表当时只有 `approval_id`，没有 `approval_mode`。而三大核心契约（决策3）的原话是：

> 低风险动作可以由受保护的 policy 自动批准，但**必须记录 `approval_mode=policy`**。

也就是说：契约把它写成**要求**，而记录这件事的地方根本没有那个字段。于是"不伪装成人工批准"这句话在**审计里不可验证**——一条 agent 自动批准的事件和一条人批准的事件，行形状完全一样，任何读者都分不出来。

这一阶段只做**记录的那一半**（`§61` 说的"缺的是列"），**不做**产生策略批准的那一方：核心永远不铸造批准（`AGENTS.md` §7），策略签发方是待裁决项。所以台账里 P-012 **仍然**记 `undesigned`——这一轮让那句话的后半句不再成立（列**存在**了），前半句（谁来产生）还在。

### 65.2 迁移 v6：一处需要小心的是**列位置**

`MIGRATIONS` 从 `{2,3,4,5}` 变成 `{2,3,4,5,6}`，新迁移用既有的 `_add_columns` 助手。但有一条容易忽略：

**`ALTER TABLE ADD COLUMN` 总是追加到末尾**，而 `ddl.sql` 是**新建**数据库的形状。所以新列在两边都必须位于**最后**——`approval_mode` 在 DDL 里写在 `occurred_at` **之后**，而不是语义上更顺眼的 `approval_id` 旁边。读起来稍差，但换来"迁移过的库"与"新建的库"在**列顺序**上也一致。

守卫跟着加严：既有的 `test_migrated_and_fresh_databases_have_identical_shape` 对每张表比的是 `{列名: 类型}` 字典——**对顺序不敏感**，所以列加错位置它看不见。现在 `events` 那一组改成比**列表**（有序），并断言最后一列就是 `approval_mode`。

### 65.3 哪些事件记录它：记一次，其余靠引用

| 事件 | 记录 `approval_mode`？ | 为什么 |
|---|---|---|
| `APPROVED`（`tx/approval.py` 的 `record_approval`） | **是** | 这是**建立**批准的那条事件；后面所有事件都通过 `approval_id` 指回这里 |
| `PROPOSED`（`tx/journal.py` 的 `create`） | **是** | 唯一同时持有 token 的**事务**事件：`advance` 只拿到事务，而事务的已发布 schema 是 `additionalProperties: false`，模式挂不到它身上 |
| `GC_INTENT` / `GC_APPLIED` | **是** | 调用点持有 token |
| `EXPOSED`（`env persist`） | **是** | 通过给 `apply_reference_plan` 增加可选 `approval_mode` 参数，由 CLI（持有 token）传入 |
| 其余状态事件 | 否 | 它们带 `approval_id`；审计的常规读法是**事实记一次、其余引用**，硬要每条都重复一遍只会让同一个事实有多个可能不一致的副本 |

**三种取值，不是两种**：`policy`、`human`、以及**没有批准时记 `NULL`**。"没有检查"与"检查了，是人批的"必须是不同的答案——这是 §50 对 bounds 用过的那条规则，用在这里是"作者身份"。

### 65.4 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 批准事件不再记录模式 | 危险 | **红**（`policy` 那条断言） |
| 无批准的事件也写 `human`（把 `NULL` 抹成默认值） | 危险 | **红**（第三种取值的断言） |
| `ddl.sql` 里把新列放到 `approval_id` 之后（不在末尾） | 危险 | **红**（顺序敏感的 `events` 形状断言）——这一格是**新增守卫的直接动机**，见 65.2 |

### 65.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S65.1 迁移 v6 | ✅ | `MIGRATIONS` 加 `6`；`_add_columns(connection, "events", …)`；迁移记录写明理由 |
| S65.2 DDL 与迁移**列顺序**一致 | ✅ | 两边都把 `approval_mode` 放末尾；`test_migrated_and_fresh_databases_have_identical_shape` 的 `events` 组改成有序比较 |
| S65.3 `append_event` + 五类调用点 | ✅ | `registry/db.py`；`tx/approval.py`、`tx/journal.py`、`caps/lifecycle.py`×2、`caps/exposure.py` + CLI 传参 |
| S65.4 三种取值可区分 | ✅ | `test_l1_transaction.py#test_the_audit_record_of_an_approval_says_which_kind_it_was` |
| S65.5 迁移计数与不变量守卫 | ✅ | 两处 `[1,2,3,4,5]` → `[1,2,3,4,5,6]`；迁移幂等仍成立 |
| S65.6 台账与文档回写 | ✅ | P-012 的 note 与 `no_witness_reason` 重写（缺的只剩**产生方**）；审查报告里"`events` 表没有 `approval_mode` 列"那个分句改成已交付；测试 **774 → 775** |

### 65.6 如实记录的边界

1. **没有动审计投影**（`logs/audit/events.json`）。它本来就不是完整事件日志，而是 `{event_count, last_seq, digest}` 的**摘要**（给 D10 的"可重建"判据用），连 `approval_id` 都不投。把模式投进去会改变它的摘要 digest，而"摘要里多一个字段"并不能让谁多知道一件事——权威记录是 `state/events`。
2. **`--mode` 与事件状态没有交叉校验**：`approval_mode` 取自 token（schema 已限定 `human|policy`），但事件表对它没有 `CHECK` 约束。加上约束需要一次表重建（SQLite 不能给已有表加 CHECK），而 token 已经由发布 schema 把住了取值——**这是判断，记在这里**，与 §62 用 evidenc 区分 errno 而不是拆码同一个取向。
3. **P-012 仍记 `undesigned`，计数不变**（evidenced 52 / uncited 56）。半条场景被做掉不会让它变成"已覆盖"：自动批准**仍然没有生产方**。这一轮的产出是"契约里那句要求现在可查"，不是"这个场景完成了"。
4. **只对 `fake_issuer` 造的 token 实测过**：P1 没有生产签发方（与 §59 同一条边界）。`policy` 与 `human` 两种取值的区分能力是这条边界内**真的**验证过的，不是推测。

### 65.7 实施顺序

1. 先量：读契约原文（"必须记录 `approval_mode=policy`"）+ 实测 `events` 表确实没有该列 → **先把"缺的是什么"说准**；
2. 量影响面：迁移集合、两处计数断言、形状守卫对**顺序**不敏感这件事、投影是否投该字段（结论：不投）；
3. 改 DDL 与迁移（列放末尾）+ `append_event` 参数；
4. 逐站点传参，**每处都问"这里拿得到 token 吗"**，拿不到就不传并在 65.3 里写明原因；
5. 写三种取值的点名测试，并给形状守卫加顺序敏感断言；
6. 改准台账与审查报告里那个已过时的分句，回写计数，跑全量 + 切片 + 两种验收模式，提交。

## 66. 第 66 阶段：`store` 是唯一 payload 存储——把这句话变成可执行的检查（S-027）

### 66.1 这一阶段要解决什么

S-027 的台账理由是这样写的：

> 没有 `env\runtimes` 这个视图，**也没有任何声明说它应该存在**，所以没有任何测试会因为"它出现了"而变红。要造一个证人，先得决定这个视图存不存在（那是设计，不是守卫）。

**这句话是错的，而且可查。** 规划 §7 的权限矩阵里就有一行：

```text
| env\runtimes（binding/view，无 payload） | Full | Full | Read/Execute |
```

视图**被声明了**，而且同一行还写着它**不带 payload**（§7:610 再说一次：`env` 是 runtime/environment binding，`runtimes` 属于 R）。所以这个场景**从来不是**卡在一次设计裁决上——它卡在一条**没人执行的不变量**上：`store` 是唯一 payload 存储（冻结契约 §5.3、`AGENTS.md` §5.3），而没有任何东西检查过它。

这是 §61 那条规则（"声明缺失必须写下为什么"）最有价值的一次收获：理由是写下来了，**而它是假的**。§61 复核了这条并**维持原判**——复核能查到"有没有写理由"，查不到"理由是不是真的"，除非去读被引用的那份文档。

### 66.2 今天到底有没有检查（实测）

| 面 | 原来的行为 |
|---|---|
| `doctor` D4 的 `_check_orphans` | **只扫 `store/`**——`env/runtimes` 里的载荷标记它根本看不见 |
| `doctor` D3 的 `_check_payloads` | 只要求 `store_path` 指向的目录**存在**：它指向 `env/runtimes/rogue` 且那里真有目录，就**通过** |
| `where` | `_executable_path` 只做 root 相对路径拼接，`env/runtimes/rogue/bin/x.exe` 完全合法 → **会被选中**（行声称 healthy，`usable=True`） |
| `tool status` / `tool verify` | 只查"在不在"、"digest 对不对" → 一个存在于错误位置的载荷**读起来一切正常** |

四张面**都静默**。而 S-027 要的正是两件事：**报告布局漂移**，**拒绝激活**。

### 66.3 修法：一条不变量，一个码，三个消费者

新 reason code `PAYLOAD_OUTSIDE_STORE` → 退出码 2，进 **D4** 组（"根内没有可用登记结论的对象：只报告，不接管"）。两种形状共用一个码，因为读者的下一步动作相同（**移进 `store`，或者停止声明它**）：

* **声明指到别处**（instance 行的 `store_path` 不在 `store/` 下）：`error`——那条声明**无法被兑现**，`where` 不选它；整体状态因此是 `broken`（退出码 3），不是 `degraded`；
* **没人声明的载荷标记**出现在视图目录（`tools/`、`env/`）里：`warning`——没有东西不可用，只是布局漂移了。

**同一个码、两种严重度**是刻意的：严重度讲的是**影响**，码讲的是**哪条不变量**；这里影响确实不同（一个不可用，一个只是不该在那儿）。

消费者：

* `doctor`：新 `_check_layout`，**无条件报告**（不像 `UNMANAGED_OBJECT_PRESENT` 藏在 `--include-unmanaged` 后面）。数据根里出现陌生对象是**信息**，视图目录里出现载荷是**违反冻结契约**——把后者藏在默认安静后面等于让默认值掩盖一条被违反的不变量；
* `where`：候选**仍然出现在 `candidates` 里**（静默跳过正是 ADR-0022 已经立过法反对的形状），但 `usable=False` 并带一条 `kind="layout"` 的证据；没有别的候选时整体 `BROKEN`；
* `tool status` / `tool verify`：在"存在性"检查**之前**判它，于是一个存在于错误位置的载荷不再读起来一切正常。

**"在不在 `store/` 下"这件事只有一处拼写**：`registry/entities.py` 的 `STORE_PREFIX` + `is_store_path()`，`lifecycle.OWNED_STORE_PREFIX` 现在只是它的别名。删掉数字符串的机会，就删掉了"同一条规则悄悄变成两条"的机会。

### 66.4 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| `where` 不再用 `is_store_path` 过滤（回到只看 health） | 危险 | **红**（`found` 又变成 True） |
| `doctor` 的 `_check_layout` 从 `doctor()` 里摘掉 | 危险 | **红** |
| 声明的那个诊断严重度改成 `warning` | 危险 | **红**（整体状态与退出码断言） |
| `tool status` 的存在性检查放回前面 | 危险 | **红**（顺序断言） |

### 66.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S66.1 实测四个面都静默 | ✅ | 66.2 的表；每一行都读过实现 |
| S66.2 新码 + D4 组 + 无条件报告 | ✅ | `exits.py`、`doctor.py` 的 `_check_layout`；退出码 2，`error`/`warning` 两种严重度 |
| S66.3 `where` 拒绝激活（但仍列出候选） | ✅ | `where.py` 的 `usable=healthy and in_store` + `kind="layout"` 证据 |
| S66.4 观察面同口径 | ✅ | `toolstate.py` 的 `tool_status`/`tool_verify` |
| S66.5 一条规则一处拼写 | ✅ | `registry/entities.STORE_PREFIX`/`is_store_path`；`lifecycle.OWNED_STORE_PREFIX` 是别名 |
| S66.6 点名测试 + 台账改正 | ✅ | `test_l1_doctor.py#test_S027_...`（一次跑三张面）；S-027 的处置**删除**，原理由被记成"事实错误" |
| S66.7 计数与语料 | ✅ | 测试 **775 → 776**；evidenced **52 → 53**、uncited **56 → 55**（`undesigned` 10 → 9）；`invariant_catalogue.json` 与 `reason_code_table.json` 重生 |

### 66.6 如实记录的边界

1. **同一个码两种严重度，不是笔误**：`error` 表示"有一条声明不能被兑现"，`warning` 表示"有个东西不该在那儿"。合成一种会丢掉其中一个事实；拆成两个码则会让读者以为下一步动作不同（其实相同）。
2. **"载荷"的判据是 `artifact.json`**，这是本项目给受控实例的标记（`_check_orphans` 一直用它）。所以**一个手工拷贝进来、不带该标记的目录不会被报**——它既不是 AIROOT 的载荷，也无法与用户自己的文件区分。这条边界是**刻意的**：把"任何看起来像运行时的目录"都报成漂移会在数据根之外制造假警报。真要有内容级判据，那是另一个判断。
3. **没有动 D5**：绑定本身仍然完好，坏的是实例的 `store_path`。让 `where` 的候选**存在但不可用**比让绑定变成"目标缺失"更准确。
4. **`rebuild.py` 的同类扫描没有跟着扩展**：它只重写派生投影、并且**只报告陌生对象**（不接管）；`doctor` 现在会报的那件事不在它的职责里。这是**已知的不对称**，记在这里而不是顺手改（改它要动派生重建的语义）。**§71 已改判**：量下来那份"不对称"底下还压着两层问题（扫描写了两遍、深度 1 有共同盲区），见 §71.2；本节保留当时的判断原样，只加这一句指向改判。
5. **S-027 的原始理由保留在案**：它不是被删掉，而是被 §66 的注释**记成一次事实错误**——"理由是写下来了，而它是假的"比"没有理由"更值得留档。

### 66.7 实施顺序

1. 先读被引用的那份文档（规划 §7），确认 S-027 的前提是**假的**——**先证伪，再动手**；
2. 实测四个面今天各自怎么回答（66.2）；
3. 定义唯一拼写 `STORE_PREFIX`/`is_store_path`，让 `lifecycle` 用它；
4. 加码、进 D4 组、写 `_check_layout`（两种严重度、无条件报告）；
5. `where` 与两个观察面同口径；
6. 写一个点名测试跑三张面（含"声明"与"没人声明"两种形状），逐个验红；
7. 文档三处（退出码行、D4 行、agent 面参考）+ 台账 + 计数 + 语料，跑全量 + 切片 + 两种验收模式，提交。

## 67. 第 67 阶段：把"缺一个签发方"量清，并让它有出处（ADR-0024）

### 67.1 这一阶段要解决什么

§59 记了一条边界（`stage`/`commit` 没跑过），§65.6 pt 4 记了一条边界（只对 `fake_issuer` 造的 token 实测过），§66 又记了一条（§59 同源）。**它们是同一件事**：P1 没有生产批准签发方。

每一处单独读都写得对。合起来读不出**规模**——一个读者很容易以为那只是"`ed25519` 还没实现"，也就是一个算法的事。把每一条要批准的路径逐条走一遍之后，事实是：

> **这个 build 里没有任何一条"要批准才能做"的路径能在真机上走完。**

这不是少一个算法，是**少一个信任根**。`state/test-keyring.json` 里的密钥就写在 root 内，任何能写这个 root 的进程都能签出"有效"token。它作为**测试**签发方完全够用——它要证的恰恰是消费侧会拒绝伪造、重放、过期、跨 root 的 token；但它**不能**升格成生产签发方，否则"批准"就退化成"任何同用户进程都能做的事"，而那正是这套设计存在的理由。

所以这一阶段**不实现签发方**（那是裁决，不是实现）。它做三件事：

1. **把挡住的东西量清并写下来**——不是散在三个阶段的"边界"里，而是一张表 + 一份决策简报（**ADR-0024**，状态**提案**，含三条路、各自代价与推荐）；
2. **让两条拒绝消息说同一件事、指向同一份裁决**：今天它们分别说"没有 keyring"和"`ed25519` 未实现"，读起来像两个互不相干的缺口，而它们是同一条待裁决项的两面；
3. **改掉三份文档里不成立的承诺**：`references/confirmation.md` 把"人工批准 `plan_hash`"列成第 3 步；`SKILL.md` 的《批准》一节告诉 Agent"没有得到人工批准时，唯一正确的行为是停下来，把 plan 文件路径与 hash 交给用户"；`agents/airoot.json` 的 `adopt --mode import` 通道以"approve it and run install"收尾——**三份文档都在描述一条走不通的流程**，而这个 build 里第 3、4 步没有实现。文档不能对 Agent 描述一条走不通的流程，尤其不能让它向用户这么说。

### 67.2 今天到底挡住什么（实测，不是推断）

每一条的失败点都是同一个 `load_keyring()`（`tx/approval.py`）**或**它的 `ed25519` 分支。只要 root 里没有测试 keyring，或 token 用的是 `ed25519`，就到此为止——**五条路径没有一条能绕过**。两类拒绝在 §67 之后都带同一句 `ISSUER_PENDING`，所以下表只列各自**不同的前半句**：

| 执行点 | 到达签发方的路径 | 前半句 |
|---|---|---|
| `approve <plan> --token-file <t>` | `cli.py` 的 `cmd_approve` | `no approval keyring is installed` |
| `install <plan> --token-file <t>` | `cmd_install` → `runner.commit()` → `tx/artifact.py` / `tx/simulate.py` 的 `keyring()` 回退 | 同上（同一个 `load_keyring`） |
| `env persist <ref> --token-file <t>` | `cli.py` 的 `cmd_env_persist` | 同上 |
| `tool gc --apply --token-file <t>` | `cmd_tool_gc` → `caps/lifecycle.py` 的 `apply_gc_plan` | 同上 |
| `uninstall <owned-instance> --token-file <t>` | `cmd_uninstall` → `caps/lifecycle.py` 的 `apply_gc_plan` | 同上 |
| 任何 `ed25519` token（有 keyring 也一样） | `tx/approval.py` 的 `verify_signature` 算法分支 | `ed25519 approval verification is not implemented` |

全部是 `PROVENANCE_FAILED`（退出码 7）。**表里写函数名而不是行号**：行号会被任何一次无关改动作废，读者 grep 一次名字就能定位；这条取舍本身也记在 ADR-0024 里。

**关键性质**：五条路径全部收敛到**同一个函数**。这让修法可以只有一处（67.3），也让"漏掉一条"这种错误不可能悄悄发生——如果将来多出第六条要批准的路径，它要么走这个函数（自动带上那句话），要么就是**绕过了批准检查**（那是另一个更严重的问题，会被批准侧测试抓到）。

台账侧同样对得上：**P-012**（policy 批准没有生产者）、**P-015**（撤销那一半）、**P-017**（没有 issuer 身份证据）、**C-022**（人类批准通道）四条都是 `undesigned`。它们今天不能被判成 `evidenced`，因为要证的那件事缺少生产侧的一半。

**"没跑过"与"跑不通"是两件事**：§59 已经对真实上游跑过解析 + 下载 + 摘要校验（`rustup-init.exe`，12 721 664 字节，SHA256 与上游发布值一致），`stage`/`commit` 这两步没跑过——原因就是这一条。而 `plan --dry-run` / `plan` 这两步是**能跑**的，说"整条路径不可用"同样不准确。

### 67.3 修法：一个共享句子，一个指针，一处文档

**一个共享句子。** `tx/approval.py` 新增模块级常量：

```python
ISSUER_PENDING = "no production approval issuer exists in this build (ADR-0024 is the pending decision)"
```

两处拒绝消息（`load_keyring` 的缺 keyring、`verify_signature` 的 `ed25519`）都带上它。**只改这两处，五条路径就都带上了**——因为它们都经过这两个函数之一。这不是"分别在五个命令里加提示"，那会把同一句话写五份，然后等着某一份漂移。

**各自的前半句保留。** "没有 keyring"与"`ed25519` 未实现"要修的是不同的事（一个要装签发方，一个要落地校验），所以测试同时断言**两条消息各自不同**——把它们合并成一句会丢掉"该修哪个"这个信息。

**指针必须可校验。** 新测试断言 `ISSUER_PENDING` 里写着 `ADR-0024`，**并且那份日志里真有 `## ADR-0024` 这一节**。指向一个不存在的东西比不指更糟：读者会去找，然后什么也找不到，于是"文档又过时了"变成默认预期。

**一处文档。** `references/confirmation.md` 的《批准的形状》底下新增《这个 build 里第 3、4 步没有可用实现》，明说第 1、2 步**可用**、第 3、4 步**没有实现**、以及不要试图自己造 token。守卫组 19 加第七十四项检查：那份参考里必须同时出现 `--token-file`、`ISSUER_PENDING`、`ADR-0024` 与"提案"——少一个就红。

**同一处缺陷还有第二、第三份文档。** 找第一处时顺手读了 Skill 入口，`SKILL.md` 的《批准》一节写着"没有得到人工批准时，唯一正确的行为是停下来，把 plan 文件路径与 hash 交给用户"——这句话假定**存在一条能批准它的通道**，而这个 build 里没有。它比 `confirmation.md` 更要紧：那是 Agent **每次**遇到需要批准的动作时照着做的一节。改法与参考文档相同（同一句 `ISSUER_PENDING`、同一个指针、明说"提案"），守卫加在 `test_l1_skill.py`——Skill 的漂移守卫就住在那里。

再顺着"Agent 还会读哪一份"往下：`agents/airoot.json`（机器可读的通道表，按 `AGENTS.md` §2 是"问题 → 命令 → 该读哪些字段"）的 `adopt --mode import` 通道写着 `"produces an install plan (nothing is copied yet); approve it and run install…"`——**同一个缺陷的第三份**，而且它是最先被读到的一份。它的边界句必须落在**同一条 `notes` 里**，不能只是"文件里某处提过"：读者看的是那一条通道。守卫因此断言"含 `approve it and run install` 的那条 note 自己带着 `ISSUER_PENDING` 与 `ADR-0024`"。三处守卫都同时断言原文仍在，所以它们不会靠"功能被删掉"而通过。

**ADR-0024 写进决策日志**（放在"尚未决策"之前），并把那份清单里的第 4 项升级成对它的交叉引用：原来那句"P1 只有 `test_hmac_sha256`，`ed25519` 校验显式未实现"读起来只像一个算法缺口。

**真机验收报告也说同一句话。** `real_machine_acceptance.py --online` 在跑完 `https_artifact` 之后会打印一条边界行，本来就说"`stage`/`commit` 需要批准 token，而 P1 没有生产签发方"——§67 给它补上同一个指针。三张面（拒绝消息 / 参考文档 / 验收报告）因此都指向同一份裁决，而不是各自说一件"差不多的事"。

### 67.4 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 把 `ISSUER_PENDING` 清空 | 危险 | **红**（两条消息都不再指向裁决） |
| 从 `load_keyring` 的拒绝消息里去掉共享句 | 危险 | **红** |
| 把 `ed25519` 分支的消息改回旧文本 | 危险 | **红** |
| 从决策日志里删掉 `## ADR-0024` 这一节 | 危险 | **红**（指针失去目标） |
| 从 `references/confirmation.md` 删掉那一节 | 危险 | **红**（守卫组 19 第七十四项） |
| 从 `SKILL.md` 的《批准》一节删掉诚实段落 | 危险 | **红**（`test_l1_skill.py` 的新守卫） |
| 从 `agents/airoot.json` 那条 `notes` 里删掉边界句 | 危险 | **红**（同文件的新守卫；注意它只认**同一条 note**） |
| 把两种成因的消息改成同一句 | 危险 | **红**（`messages[0] != messages[1]`） |

### 67.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S67.1 量清五条路径 + 两种成因 | ✅ | 67.2 的表；每一条都读过调用链（`cli.py` → `simulate`/`artifact`/`lifecycle` → `approval`） |
| S67.2 ADR-0024（提案）+ 清单第 4 项交叉引用 | ✅ | `docs/AIROOT-v0.3-实现决策记录.md`；含挡住表、三条路、代价、推荐、明确不做 |
| S67.3 共享句 `ISSUER_PENDING` + 两处拒绝一致 | ✅ | `tx/approval.py`；保留各自前半句，证据行改为可执行的三条 |
| S67.4 指针可校验 | ✅ | `test_l1_transaction.py#test_both_issuer_refusals_name_the_same_pending_decision`（含 `## ADR-0024` 存在性） |
| S67.5 参考文档、Skill 与通道表不再承诺走不通的步骤 | ✅ | `references/confirmation.md` 新增一节 + `test_l0_consistency.py#test_the_confirmation_reference_does_not_offer_a_step_this_build_cannot_perform`；`SKILL.md` 的《批准》一节原样写着"把 plan 路径与 hash 交给用户"，**同一个缺陷**，一并补上 + `test_l1_skill.py#test_the_skill_does_not_offer_an_approval_it_cannot_obtain`；`agents/airoot.json` 的 `adopt --mode import` 通道以"approve it and run install"收尾，**第三份**，边界句写进同一条 `notes` + `test_l1_skill.py#test_the_agent_metadata_does_not_offer_an_approval_it_cannot_obtain` |
| S67.6 入口文档如实指向裁决 | ✅ | `AGENTS.md` §8 那条 `ed25519` 边界升级成"挡住五条路径 + 见 ADR-0024"；§1 的 `§31`–`§67`；真机验收报告的边界行补上同一指针 |
| S67.7 计数与语料 | ✅ | 测试 **776 → 780**；审计检查 **73 → 74**；台账计数**不变**（evidenced 53 / uncited 55）；无需重生语料（只改了文本、一条 `print` 与一条 `notes`，没有改动任何对外 JSON 形状——已用 `git diff --stat` 核对） |

### 67.6 如实记录的边界

1. **这一阶段不解锁任何执行点。** 措辞统一不等于问题解决：那五条路径在真机上仍然走不完，`security_mode=policy_only` 与 `enforcement=same_user_can_bypass` 一个字没改。从这一节读成"批准问题解决了"是对它的误读。
2. **ADR-0024 是提案，不是决策。** 三条路一条都没选，本阶段也没有实现任何签发方。参考文档里"提案"二字由守卫钉住，正是为了防止文档把它说成已定。
3. **测试签发方没动，也不允许升级成生产。** `cli/tests/fake_issuer.py` 与 `state/test-keyring.json` 原样保留；`AGENTS.md` §7 的"`test_hmac_sha256` 只允许出现在测试/模拟路径"是这一阶段的**前提**，不是可以顺手放宽的东西。
4. **台账四条保持 `undesigned`，计数不变。** "我们讨论过它了"不是证据；裁决落地并且有生产侧可测之前，它们不改判。
5. **只统一了这一族的拒绝消息。** `APPROVAL_REQUIRED`（没带 token）、`INVALID_APPROVAL`（签名不匹配）、`APPROVAL_EXPIRED` 等的措辞没有一起动——它们讲的是另外的成因，各自的下一步动作也不同，并进同一句话反而会掩盖它。
6. **`ed25519` 分支的证据行提到 `caps/acl.py`**，只是说"受保护一侧今天只观测、不写入"。这不是声称 ACL 与批准共用一套机制——它们是两个都要等 P2 的东西，不是同一个东西。
7. **没有新增 reason code、退出码或码表条目。** 两类拒绝仍然是 `PROVENANCE_FAILED`(7)；这一阶段改的是文本与文档，不是分类。
8. **另外两份文档是"顺着调用者读"找到的，不是清单里写着的。** 这一阶段原本只点名了 `references/confirmation.md`；读到 Skill 入口发现同一个缺陷落在**更要紧**的位置（Agent 每次遇到需要批准的动作都照着那一节做），再顺着"Agent 还会读哪一份"找到**最先被读到**的 `agents/airoot.json` 通道表。这一类缺陷的判据是"**这份文档有没有让人去做一件做不到的事**"，而它天然会出现在**每一份**描述批准流程的文档里——所以下次修同类问题时，要顺着"谁会照着它做"读一遍，而不是只修被点名的那一份。

### 67.7 实施顺序

1. 先量：逐条走五条路径 + 两种成因，把**确切消息**抄下来（67.2 的表）——先有事实，再有说法；
2. 读被引用的契约（核心契约 §8.5、broker 方案 §5、`AGENTS.md` §7），确认"核心只验不签"是一条**规则**而不是一处遗漏——否则下一步会写错成"补上签发"；
3. 写 ADR-0024（提案）：**先把选项、代价与推荐写下来，再改任何代码**；
4. 共享句 + 两处拒绝 + 指针测试（含"ADR 真的存在"），逐个验红；
5. 改**三份**描述批准流程的文档（`references/confirmation.md` + `SKILL.md` + `agents/airoot.json`）并各加一条守卫——**先顺着"谁会照着它做"读一遍**，别只修被点名的那一份；
6. 回写计数与三处文档（`AGENTS.md`、ADR 清单、本草案），跑全量 + 切片 + 两种验收模式，提交。

## 68. 第 68 阶段：把"做不到就不能写成可做"推广到每一处**规定动作**的地方

### 68.1 这一阶段要解决什么

§67 真正的产出不是"那三份文档改好了"，而是**一条判据**：

> 一份 Agent 会照着做的文档，不能把**做不到的一步**写成可以做的。

§67 只把它用在被点名的那三处（`references/confirmation.md`、`SKILL.md` 的《批准》、`agents/airoot.json` 的一条 lane）。§68 先**量**：还有哪些地方在**规定动作**。

先量的是另一件事，结论是**干净的**：这三份文档里出现的每个 `--flag`，是否都存在于它被写在后面的那条命令上？一度怀疑 `exec --env` 是文档漂移（我直接调 `build_parser().parse_args()`，它报 `unrecognized arguments: --env`），但那是**测量方法**的问题：这个拼写由 `_normalize_exec_argv` 在 argparse **之前**改写，ADR-0016 冻结了这个别名、§40.4 把它抽成 `EXEC_ALIAS_FLAG`、真机验收也真的跑它。所以"选项漂移"这条线**已经有守卫**，不是这一阶段的事——**记在这里，免得下一个人再查一遍**。

真正还有缺口的是**规定动作的位置**：

| 位置 | 原来写的 | 缺什么 |
|---|---|---|
| `references/reason-codes.md` §4（退出码 4） | "**唯一正确的行为**：把 `plan_hash` 与 plan 文件路径交给用户，等人工批准" | 整页没有一个字说这一步今天走不到底。而**同一份文件的 §5（退出码 5）早就带着同类边界**（"P1 没有 broker，所以 machine 级写入一定报这个；不要建议用户手工改 HKLM 绕过"）——**同类文档内部不一致**，比"忘了写"更容易被读过去 |
| `SKILL.md` 命令地图三行（装 / 持久化 / pin）+ 确认协议那条 bullet | "然后按需要批准"、"再要 approval token"、"应用仍需批准"、"需要它自己的批准" | §67 加的边界在 **50 行以外**的《批准》一节；按 §67 自己的规则，指针要落在**规定动作处** |
| `agents/airoot.json` 两条 lane（`env persist`、`tool gc --plan`） | "without `--token-file` this stops at exit 4"、`--apply` needs an approval token" | 只说"需要 token"，没说 token 今天不存在。§67 只修了 `adopt --mode import` 那一条 |
| `AGENTS.md` §6 命令块 | 四条 `--token-file` 命令排在那里，可以直接复制 | 边界在 §1 与 §8（同文件，但不在命令旁边） |

### 68.2 修法：判据不变，覆盖面变了

**一句短指针**：`（这个 build 签不出 token：见《批准》）`。它故意短——要塞进表格单元格，而**长到读不完的警告等于没有警告**。共享句 `ISSUER_PENDING` 仍然只出现在两个地方：拒绝消息，以及"这一页到底在讲什么"的位置（`references/reason-codes.md` §4 用的是完整句）。

**守卫从"三份文档各一条"变成"按位置的性质判"**——这是这一阶段与 §67 的实质区别：

* `agents/airoot.json`：不再盯"我修过的那条 lane"，而是**任何 note 提到 token/approval 的 lane** 都必须带边界。第三条这样的 lane 出现时会自己红，不需要谁记得加守卫；
* `SKILL.md`：**任何在规定动作区提到批准/token 的行**都必须带指针。豁免**按理由**给，不按行号（行号会烂）：《批准》一节本身就是指针，**《绝不做的清单》讲的是禁止**（没有"怎么做"要指），围栏里是图例。匹配前先剥掉反引号内容——否则一个**名字里带 `APPROVAL` 的 reason code**（`SCOPE_UPGRADE_REQUIRES_APPROVAL`）会被当成一次规定动作；
* `references/reason-codes.md`：边界必须落在 **§4 那一节里**，不是"文件里某处"；
* `AGENTS.md`：命令块里有 `--token-file`，文件里就必须有那句话与那个指针。

四条守卫都同时断言**原文仍在**（"等人工批准" / `--token-file` / "把 plan 文件路径与 hash 交给用户" / "approve it and run install"），所以没有一条能靠"功能被删掉"通过。

### 68.3 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| `SKILL.md` 命令地图"持久化"那一行拿掉指针 | 危险 | **红** |
| `SKILL.md` 命令地图"装"那一行拿掉指针 | 危险 | **红** |
| `references/reason-codes.md` §4 拿掉边界 | 危险 | **红**（新守卫） |
| `tool gc --plan` lane 拿掉边界 | 危险 | **红**（放宽后的元数据守卫） |
| `env persist` lane 拿掉边界 | 危险 | **红**（证明守卫真的覆盖了第二、三条 lane，而不只是原来那条） |
| `AGENTS.md` 拿掉共享句 | 危险 | **红**（入口文档守卫） |

### 68.4 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S68.1 先量"选项漂移"这条线 | ✅ | 结论**干净**且**已有守卫**（`EXEC_ALIAS_FLAG` / §40.4 / ADR-0016 / 真机验收）；测量脚本用后即删，结论写在 68.1 |
| S68.2 量出四处规定动作 | ✅ | 68.1 的表；每一条都读过原文与上下文 |
| S68.3 `references/reason-codes.md` §4 补边界 | ✅ | 完整句 + ADR-0024 + "前两步可用"；`test_l1_skill.py#test_the_reason_code_reference_does_not_prescribe_an_approval_nobody_can_give` |
| S68.4 `SKILL.md` 四处补短指针 | ✅ | 命令地图三行 + 确认协议 bullet；守卫**按位置性质**判，豁免按理由给 |
| S68.5 两条 lane 补边界 + 守卫放宽到"任何提到 token 的 lane" | ✅ | `agents/airoot.json`；同名守卫体内改判据，测试名不变（§67 的记录因此仍然准确） |
| S68.6 `AGENTS.md` 命令块补边界 | ✅ | §6 命令块 + `test_l1_skill.py#test_the_entry_document_marks_the_commands_it_cannot_complete` |
| S68.7 计数与语料 | ✅ | 测试 **780 → 782**；审计检查 **74 不变**；台账计数**不变**；无需重生语料（只改文本与 `notes`，没有改动任何对外 JSON 形状） |

### 68.5 如实记录的边界

1. **这一阶段同样不解锁任何执行点。** 五条路径在真机上仍然走不完，`security_mode=policy_only` 与 `enforcement=same_user_can_bypass` 一个字没改。四份文档现在**都说这件事**，仅此而已。
2. **指针是短句，不是完整句。** 完整句在拒绝消息与 `references/reason-codes.md` §4 里。这是刻意的取舍（表格宽度 vs 自足性），代价是读者要跳一次——而跳转目标就在同一份文档的一节，不是外部链接。
3. **守卫的"规定动作区"是启发式的，不是形式化的。** 它按小节标题与围栏判断。把《批准》一节改名会让守卫**变严**（那一节不再被豁免，于是要求指针），新加一节讲禁止但标题不叫《绝不做的清单》也会**变严**（要求一个不该要求的指针）。两个方向都是**红**而不是静默通过，这是可以接受的失败方向；写在这里，免得有人以为它是精确判定。
4. **`exec --env` 的测量结论如实留着。** 这一阶段一开始怀疑它是文档漂移，实测是"在 argparse 之前改写"，所以**没有**改任何代码或文档。记下来是为了让下一个查它的人省一轮，也为了说明"实测会先给出错误答案，只要你用错了入口"。
5. **只改了"提到批准"的地方。** 退出码 5（`PRIVILEGE_REQUIRED` / `ACL_MISMATCH`）、退出码 6（恢复）等页各自已经有边界句，这一阶段不动它们——同一份文件里已经做对的地方，不需要为了整齐再改一遍。
6. **没有新增 reason code、退出码或码表条目**，也没有动 `references/confirmation.md`（§67 已经改过）。

### 68.6 实施顺序

1. 先量"选项漂移"这条线，**先接受一个否定的结论**（它已经有守卫，不是本阶段的事）；
2. 再量"规定动作的位置"：按**文档 × 规定动作**列表，逐处读上下文（68.1）；
3. 定短指针与豁免**理由**（不是行号），写进代码前先写清"什么算规定动作区"；
4. 四处分别补边界，**先短指针、后完整句**（越靠近动作越短）；
5. 四条守卫，两条是**放宽既有守卫的判据**而不是新增（元数据 lane、Skill 行），逐个验红；
6. 回写计数与文档，跑全量 + 切片 + 两种验收模式，提交。

## 69. 第 69 阶段：一个动作两个入口，只有一个检查边界（`adopt --mode import`）

### 69.1 这一阶段要解决什么

§64 交付了 `adopt --mode import`：它产出**真实 artifact 计划**，`approve` + `install` 之后把 payload 复制进 `store/` 并绑定。§64 检查了 `--capability` 的**形状**（是名字，不是路径），**没有**检查它**是否存在**。

而"AIROOT 允许管理什么"这条边界的事实来源是冻结能力清单（`policy/capabilities.json`）：规划 §15.2-1 写着"没有冻结能力 → `unmanaged`：只报告，永不接管"。`plan` 一直在强制这一条——于是**同一个动作（为受管实例产出计划）有两个入口，只有其中一个检查边界**。§15.4 的成长路径（提议 → 冻结 → 白名单）因此可以被 `adopt --mode import` 绕过去。

### 69.2 今天到底怎么样（实测，不是推断）

先量的是"**哪些入口接受 capability id**"。逐个用 `not-a-frozen-capability` 跑：

| 入口 | 结果 | 判断 |
|---|---|---|
| `where <cap>` | `NOT_FOUND`(1) | **对**：只读查询，"没找到"就是诚实答案 |
| `scope decide <cap>` | `CAPABILITY_NOT_DECLARED`(9) | **对** |
| `plan <cap> --scope data-root --target … --dry-run` | `CAPABILITY_NOT_DECLARED`(9)，消息 `no frozen capability is declared for …` | **对** |
| `source resolve <cap>` | `NOT_FOUND`(1) | **对**：来源目录查询 |
| `tool pin <cap> --version …` | `SUCCESS`，`plan: null`，`sync[].plan_blocked_by = NOT_FOUND` | **对**：desired 层只记愿望、不产出计划、不改绑定（先疑后证，见 69.7 pt 3） |
| **`adopt <file> --mode import --capability <cap>`** | **`SUCCESS`(0) + 一份完整计划** | **错**：既有边界对它只是建议 |

最后一行是这一轮的红：一条**能装进 `store/` 的**路径，用一个没冻结过的能力名字就能走完。

### 69.3 修法

在**形状检查之后、路径检查之前**加一次边界检查（顺序与 `plan` 一致：先看这个能力名字合不合法、再看目标）：

```python
    frozen = load_capabilities()
    if frozen.by_id(capability) is None:
        raise AirootError("CAPABILITY_NOT_DECLARED", f"no frozen capability is declared for {capability}", ...)
```

`evidence` 里给三件事：当前 revision **声明了哪些**能力（读者不必再去翻文件）、这条边界的含义（没有冻结能力就是 `unmanaged`）、以及成长路径（提议 → 冻结 → 白名单谓词）。

**消息与 `plan` 逐字相同**，而且测试断言两者**相等**：两个入口、一条规则——否则"一条边界"会悄悄变成两条会各自漂移的规则。这是 §66"同一条规则只有一处拼写"的同类做法，只是这次共享的是**措辞**而不是常量。

### 69.4 连带影响：示例自己会失败

`jq` 不在冻结清单里（清单是 `python`/`node`/`java`/`git`/`archive`/`media_probe`/`build`/`fake-tool`），而 **`AGENTS.md`** 与 **CLI 自己的证据串**都把 `--capability jq` 当例子。边界一旦生效，这两处就从"能跑"变成"跑不通"——**修一个"少一个检查"，如果不看例子，等于把一条能跑的命令改成跑不通的命令**。

所以：

* 两处例子改成 `7z.exe` / `archive`（冻结清单里最接近"用户本来就有的单文件 CLI"的那一个）；
* 例子旁边写明前置条件：必须在冻结清单里，否则 `CAPABILITY_NOT_DECLARED`(9)，口径与 `plan` 相同；
* 新增守卫：**文档里任何具体的 `--capability <id>` 例子都必须是冻结能力**。占位符（`<id>`）不算例子——它不是承诺。守卫同时断言"至少找到过一个例子"，否则它可以在"例子被删光"之后假装通过。

### 69.5 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 去掉 import 的边界检查 | 危险 | **红**（修之前实测到的正是这个状态：exit 0 + 一份完整计划） |
| 让两个入口的消息不再逐字相同 | 危险 | **红**（相等断言） |
| 把 `AGENTS.md` 的例子改回 `--capability jq` | 危险 | **红**（示例守卫） |

### 69.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S69.1 量清每个接受 capability id 的入口 | ✅ | 69.2 的表；六个入口逐个实测，四个"对"、一个"对（先疑后证）"、一个"错" |
| S69.2 import 强制冻结边界 | ✅ | `cli.py` 的 `_adopt_import`；`CAPABILITY_NOT_DECLARED`(9)，证据含 revision 与成长路径 |
| S69.3 两个入口一条规则 | ✅ | 测试断言 `plan` 与 `adopt --mode import` 的 `message` **相等** |
| S69.4 例子跟着改 + 示例守卫 | ✅ | `AGENTS.md` 与 CLI 证据串改成 `7z.exe`/`archive`；`test_l1_boundary.py#test_every_documented_capability_example_names_a_frozen_capability` |
| S69.5 拒绝时不落盘 | ✅ | 测试断言被拒的 adopt **没有**在 `state/plans/` 留下文件（"还没决定"在文件系统上可证，§20.3-2） |
| S69.6 计数与语料 | ✅ | 测试 **782 → 783**；审计检查 **74 不变**；台账计数**不变**；无需重生语料（没有改动任何对外 JSON 形状） |

### 69.7 如实记录的边界

1. **只加了一处检查。** 其余入口实测都已诚实（69.2），**没有**为了整齐去动它们。
2. **不清算 `kind` 的两种词表。** 冻结清单的 `kind` 是 `tool|runtime`（决定路由默认值），计划的 `target.kind` 是 `managed_tool|runtime`（另一种词表），而 P1 在 **6 处**硬编码 `managed_tool`（`tx/simulate.py` ×2、`tx/artifact.py` 的默认、`cli.py` ×3）。所以 `--capability python` import 出来的计划写的是 `managed_tool`，而冻结清单说 `python` 是 `runtime`。这是 **P1 的系统性简化**（这个 build 不产出 `runtime` 实例，P5 才有），不是 import 的缺陷——改它要动实例种类、`inventory` 的类集合与 gc 的语义。**记在这里，不改。**
3. **`desired` 层不查边界是判断，不是遗漏。** `tool pin` 对未冻结能力返回 `SUCCESS` + `plan: null` + `sync[].plan_blocked_by`，意思是"愿望记下了，但明确没有计划"。它不改 binding、不产出计划，所以拿它和 import 类比会得出错误结论。这一轮**先怀疑它是第二个洞，量完改成"不是"**——过程留在 69.2 的表里，因为"排除一个嫌疑"和"找到一个缺陷"一样是结论。
4. **import 只查"冻结"这一半，不查白名单那一半。** 规划 §15.4 的成长路径是"提议 → 冻结 → 加白名单谓词"，但白名单管的是**识别**（在数据根里怎么认出这种对象），而 import 的对象是显式交到手里的，识别不适用。这里对齐的是 `plan` 已经强制的那一半。
5. **`capability check` 与 import 同一口径**：用同一个 capability id 问同一份文件，前者按 `check_admission` 的默认 `source_verifiable=True` 说 `adoptable`，后者给出计划。两处没有分叉。
6. **台账计数不变。** C-009（`adopt` 的三档 mode）仍记 `undesigned`，因为缺的是 `recreate` 那一半；把 `import` 那一半做得更严，不构成"这条场景完成了"。
7. **没有新增 reason code 或退出码**：用的是既有的 `CAPABILITY_NOT_DECLARED`(9)，也没有动码表与不变量目录。

### 69.8 实施顺序

1. 先量"哪些入口接受 capability id、各自怎么回答"（69.2）——**先怀疑 `tool pin`，再用证据把它排除**；
2. 红先写：在既有的参数契约测试里加一例（未冻结 capability），实测拿到 `exit 0` + 一份完整计划；
3. 在形状检查之后、路径检查之前插入边界检查，**消息抄 `plan` 的原话**；
4. 追加"两个入口消息逐字相同"与"被拒时不落盘"两条断言；
5. **顺着例子读一遍**：`AGENTS.md` 与 CLI 证据串里的 `jq` 会被这条检查打死 → 换成冻结能力并写明前置条件；
6. 加示例守卫，逐个验红；回写计数与文档，跑全量 + 切片 + 两种验收模式，提交。

## 70. 第 70 阶段：同一动作的第二个门——`adopt --mode import` 不经过确认闸门

### 70.1 这一阶段要解决什么

§69 的形状是"**一个动作、两个入口，只有一个检查**"，它修的是**能力边界**。这一轮顺着同一条线去量**第二个门**：§12.1 的确认闸门。

§12.1 把三类动作标成**必须确认**（往运行时装包 / 创建环境 / 体积超阈值），`plan` 通过 `decide_scope` 强制它。而 `adopt --mode import` **也**产出受管实例的计划——它**从不调用那个决策**。

### 70.2 实测（两条发现）

**发现一：闸门只在一个入口存在。**

| 入口 | 能力（冻结 `kind`） | 结果 |
|---|---|---|
| `plan python --scope data-root --target … --dry-run` | `runtime` | exit **4** `SCOPE_CONFIRMATION_REQUIRED`（`decision=SCOPE_CONFIRMATION_REQUIRED`、`required_approval=scope_confirmation`） |
| `adopt <file> --mode import --capability python` | 同一个 `runtime` | exit **0** + 一份完整计划，每个 operation 的 `target_scope` 都是 `machine` |
| `plan archive` / `import archive` | `tool` | 两边都 exit 0——**正确**：这一类本来就不问（§12.1 第 2 行） |

**发现二：计划里看不见体积。** import 的计划**不携带体积**：`metadata.backend.estimated_size` 是 `null`、`source.integrity` 只有摘要、`operations` 里没有字节数。批准一次 250 MB 拷贝的人**看不到它有多大**——而这条命令**刚刚读完整个文件算完 sha256**。`plan` 那一边的体积是进 routing 块的（`size_estimate_bytes`）。

### 70.3 修法

1. `caps/planner.py` 加一个**有名字的接缝**：

   ```python
   def import_scope_decision(capability_id, *, source_bytes, threshold_bytes=DEFAULT_SIZE_THRESHOLD_BYTES)
   ```

   它只接**这条命令真能观察到的事实**：它已经量过的体积，加上（在 `decide_scope` 内部的）冻结 `kind`。`plan` 的两个声明式旗标（`--creates-environment`、`--requires-cuda-or-native`）**故意没有对应物**——正要复制 payload 的那条命令不该有权把自己的风险声明掉。用接缝而不是内联调用，是为了让"超阈值"那条在**不写 300 MB 文件**的前提下可测。
2. `_adopt_import` 调用它；`confirmation_required` 时**拒绝**（exit 4），证据里给四件事：路由层自己的 `high_risk` 理由、固定的三选项、**实测**体积与阈值、以及两条出路（用 `plan` 回答那个问题，或用 `--mode reference` 只登记不拥有）。
3. 计划记录 `metadata.import.size_bytes` + `size_source: "measured-from-the-file"` + 路由结论；人读的那一行也印出体积。

**为什么是"拒绝"而不是"绕过"**：import 固定绑机器级（§15.5 把它定义为复制进 `tools`），没有 `--scope` 可问；`plan` 自己也没有回答这个问题的旗标——确认是**人类的交互步骤**（而 `.ai/tooling.json` 的记忆写入属 P2）。所以 import 既不能问、也不能答。§12.1 的设计注解把后果说得更直接：确认一旦可以随手跳过，高风险确认会**一起**失效。

**这条限制不是新发明。** §12.1 表格第 2 行就是"单文件通用 CLI（jq / rg / ffmpeg / 7z）→ 装到数据根，**不询问**"——那正是 import 服务的那一类；runtime 是第 3 行"必须确认"。修完之后 import 覆盖的**正好是它所属的那一行**。

### 70.4 连带影响：两个既有测试在无边界状态下工作

随修法一起改：`test_adopt_import_plans_and_installs_a_script_free_file`（原来 import 一个 `python.exe`）与 `test_l1_agent_read_fields.py` 的 import 场景，都改成 `archive`。这**不是**"为了让测试通过而改测试"：`python` 在冻结清单里是 `runtime`（"任何 CPython 兼容的解释器目录"），把它当成单文件 portable payload 本来就与它声明的种类不符。

### 70.5 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 关掉确认闸门（`if False`） | 危险 | **红** |
| 不再把实测体积传进决策 | 危险 | **红** |
| 给接缝加一个 `creates_environment` 参数 | 危险 | **红**（"签名恰好是这三项"的断言） |
| 计划里去掉 `size_bytes` | 危险 | **红** |

### 70.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S70.1 量出闸门只在一个入口存在 | ✅ | 70.2 的表：`plan` exit 4、import exit 0，同一个 `runtime` |
| S70.2 量出计划不携带体积 | ✅ | `metadata.backend.estimated_size=null`、`source.integrity` 只有摘要、`operations` 无字节数 |
| S70.3 有名字的接缝 + 只接可观察事实 | ✅ | `caps/planner.import_scope_decision`；`test_l1_planner.py#test_import_routing_asks_about_the_two_facts_an_import_can_observe`（含参数集恰好为三项） |
| S70.4 import 过闸门并拒绝 | ✅ | `cli.py` 的 `_adopt_import`；`test_cli_steward.py#test_adopt_import_refuses_a_high_risk_class_the_routing_gate_asks_about`（两个入口同一 reason code、被拒时不落盘、lane note 里带边界） |
| S70.5 批准者看得见体积 | ✅ | `metadata.import.size_bytes` / `size_source` / `routing`；端到端测试断言等于文件真实大小；`agents/airoot.json` 的 lane 多一条 `read` 路径 |
| S70.6 计数与语料 | ✅ | 测试 **783 → 785**；审计检查 **74 不变**；台账计数**不变**；无需重生语料（没有改动已发布 JSON 的形状） |

### 70.7 如实记录的边界

1. **这一阶段收紧了一条路径**（runtime / 超阈值的 import 现在被拒），与 ADR-0021 的"放宽优先"方向相反。理由见 70.3：§12.1 把那三类定为**必须确认**，而 import 既不能问也不能答——门留在这条路上、却在另一条路上开着，等于把它变成装饰。这属于 §7 所说的"放宽会摧毁一条更强的规则"那一类，因此**不机械照搬放宽**，而是如实报出来。
2. **超阈值那条只测到接缝。** 守卫证明"实测体积确实进了决策、阈值判定正确、超阈值要确认"，但**没有**写一个 300 MB 文件的集成测试（写它会拖慢套件并占磁盘）。集成侧测的是 runtime 那条——它不需要大文件。
3. **`plan` 对 runtime 今天同样是死路**（exit 4，没有旗标能回答它；记忆文件只读属 P2）。这不是这一轮造成的，而是同一个"缺人类批准通道"（ADR-0024 家族）。import 现在与它**一致**，而不是比它更宽。
4. **没有新增 reason code 或退出码**：用的是既有的 `SCOPE_CONFIRMATION_REQUIRED`(4)，也没有动码表与不变量目录。
5. **计划里记的是路由结论，不是完整 routing 文档。** `metadata.import.routing` 有 `reason_code`/`origin`/`scope`/`threshold_bytes`；不搬 `evidence` 是因为 `metadata` 是自由对象，而 routing 的完整形状是 `plan --dry-run` 的对外契约——复制一份就会变成第二处需要同步的拼写（§66 的同一条理由）。
6. **台账计数不变**：C-009 仍等 `recreate` 那一半。`agents/airoot.json` 的 import lane 多了一条 `read` 路径，由 `test_l1_agent_read_fields` 逐个解析验证。

### 70.8 实施顺序

1. 先量两个入口对**同一个**能力的回答（70.2 第一条）——先确认差异存在，再找它该不该存在；
2. 读 §12.1 的表格与它的设计注解，确认"必须确认"不是建议，并确认 import 属于哪一行；
3. 再量**计划里能看见什么**（70.2 第二条）——同一份计划里少体积这件事，是批准者的信息缺口，与闸门是两个独立缺陷；
4. 写有名字的接缝，**参数集恰好等于可观察事实**；
5. import 过闸门并拒绝，消息与 `plan` 同一 reason code；计划补上体积与路由结论；
6. 改两个在无边界状态下工作的测试，补新测试，逐个验红；回写计数与文档，跑全量 + 切片 + 两种验收模式，提交。

## 71. 第 71 阶段：同一条不变量、两个读者、两套走法（payload 标记扫描收成一处）

### 71.1 这一阶段要解决什么

§66 让 D4 多了一组消费者（`where`、`tool status`/`tool verify`），并在 §66.6 pt 4 记下一条**刻意的不对称**：

> `rebuild.py` 的同类扫描没有跟着扩展……这是**已知的不对称**，记在这里而不是顺手改（改它要动派生重建的语义）。

所以这一轮不是"从零找缺陷"，而是**去量那条被记下来的不对称到底有多大**。

### 71.2 实测（三个发现，外加一个测量陷阱）

**发现一：`rebuild` 对这件事完全静默。** 同一个 root 上放两个 payload 标记（`tools/mystery`、`env/runtimes/rogue`）：`rebuild --plan` 的 findings 全空（`orphans: []`、其余同为空），而 `doctor` 把两个都报出来。也就是说，一个刚跑完恢复的操作者读到"没有孤儿、没有问题"，而一条冻结契约正被违反。

**发现二：两个读者的走法本来就是两份拷贝。** `doctor._check_orphans`（store 扫描）与 `rebuild._store_orphans` 是**同一个遍历写了两次**：同一个 `store.iterdir()`、同一个 `rglob` 谓词、同一个 `declared` 集合构造、同一个相对路径算法。差别只有输出形状与码名——所以"改一处忘一处"不是可能性，是**结构**。§66 统一了 `STORE_PREFIX`（"在不在 store 里"只拼一次），但没统一**走法**。

**发现三：这份拷贝还漏了一层。** 两边都写成"先取 `store/` 的每个直接子目录、再 `rglob` 它的后代"，于是 `store/handmade/artifact.json`（**深度 1**）两个读者都看不见——实测：`doctor` 只报 `POLICY_ONLY_MODE`，`rebuild` 报空。这一层是两份拷贝**共同**的盲区，所以它不会因为"把两份合成一份"就自动消失；合成时必须顺手把走法改对。

**一个测量陷阱，值得留档**：`diagnostics_by_code` 按码折叠，同码的多条诊断只看得到一条。我第一次读到的"doctor 只报了一个"是**测量工具的假象**（原始 `diagnostics` 里是两个）。差一点就据此写出一个不存在的缺陷——**量出来的差异要先怀疑量法**。

### 71.3 修法：一处扫描、三个形状、两个读者

新模块 `caps/layout.py`（`caps/` 从 23 个变成 24 个，AGENTS.md 的模块表同步）：

* `PAYLOAD_MARKER = "artifact.json"` —— 标记只拼一次（这个项目给受控实例的标记，`_check_orphans` 一直用它）；
* `PAYLOAD_VIEWS = (tools, env)`、`PAYLOAD_PLACES = (store, *views)`；
* `store_orphans(registry, root)` —— 在 `store/` 下、没人声明（孤立）；
* `mislocated_payloads(registry, root)` —— 在视图目录下、没人声明（布局漂移）；
* `misdeclared_payloads(registry)` —— 有声明但 `store_path` 不在 `store/` 下（用既有的 `is_store_path`，**不**另写一处"在不在 store 里"）。

**任意深度**：`base.rglob("*")` 取代"子目录 + 各自 rglob"，深度 1 的盲区随之消失。这个模块只回答两件事：**去哪里找**、**谁声明了什么**。

两个读者各自换用它，对外形状**不变**：

* `doctor` 仍把三个形状折进 D4 的两个码（`ORPHANED_STORE_INSTANCE`；`PAYLOAD_OUTSIDE_STORE` 的 `error` 与 `warning` 两半），severity 与 evidence 词句原样；
* `rebuild` 把同样三个形状写进 findings：`orphans` / `mislocated_payloads` / `misdeclared_payloads`。最后一项是 `<instance_id> -> <store_path>` 一行字符串——`rebuild` 的文档没有已发布 schema，而读它的是人，一句能读懂的话比两个字段更有用（`doctor` 那一边仍然字段化，因为它要供机器消费）。

### 71.4 一个仍未动的边界

`rebuild` **不把这些新发现算进 `problems`，也不改 `derived_state_stale`**：它们不是"派生状态过期"，而是"有些东西我不解释"。`problems` 保持它原来的含义。

### 71.5 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 让扫描重新跳过深度 1（`leaf.parent != base`） | 危险 | **红**（`store/handmade` 从 orphans 里消失） |
| `rebuild` 不再报视图目录漂移 | 危险 | **红** |
| `rebuild` 不再报"声明指向 store 外" | 危险 | **红** |
| `misdeclared_payloads` 什么都不报 | 危险 | **红**（`every_shape`） |
| `misdeclared_payloads` 把**每个**声明都报成 misdeclared | 危险 | **红**（`clean_root`，反方向） |

最后两条是刻意成对的：**少报**与**多报**各有一条守卫。第 4 条一开始被我写成了"过滤器取反"，量下来它其实让扫描**什么都不报**（`if False` 不是"全报"），于是补了第 5 条走真正相反的方向——**变异也要量，不能凭想象**。

### 71.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S71.1 量出 `rebuild` 静默 | ✅ | 71.2 发现一；同一 root 上 `rebuild` 空、`doctor` 报两个 |
| S71.2 量出扫描写了两遍 | ✅ | 71.2 发现二；两个函数的遍历逐行对照 |
| S71.3 量出共同的深度 1 盲区 | ✅ | 71.2 发现三（`store/handmade` 两个读者都看不见） |
| S71.4 扫描收进 `caps/layout.py` | ✅ | 新模块；`doctor.py` 与 `rebuild.py` 各自改用它；"在不在 store 里"仍是 `is_store_path` 一处 |
| S71.5 三个形状 + 反方向都点名 | ✅ | `test_l1_rebuild.py#test_rebuild_reports_every_shape_of_payload_outside_the_store`、`#test_rebuild_reports_nothing_for_a_clean_root` |
| S71.6 `doctor` 的对外形状没动 | ✅ | §66 的证人 `test_l1_doctor.py#test_S027_...` 原样通过（含"声明只报一次、不重复"） |
| S71.7 计数与语料 | ✅ | 测试 **785 → 787**；审计检查 **74 不变**；台账**不变**；无需重生语料（`rebuild` 文档无 schema、无 fixture）；AGENTS.md 模块表 23 → **24** |

### 71.7 如实记录的边界

1. **§66.6 pt 4 的判断被改判，而不是被忽略。** 那一段**原地保留**，只加一句"§71 已改判"并指向本节——历史记录标注、不重写。
2. **与 §69/§70 的关系**：那两轮修的是"**闸门**只在一个入口"，这一轮修的是"**同一个事实**有两套走法、且两份拷贝共享一层盲区"。三者形状不同，根因相同：**同一件事在两处各写一遍**。
3. **深度 1 的盲区修好后，报告的集合变大了**——这是行为变化（报得更多），不是新能力。旧盲区是"两个读者都不报"，所以没有哪条既有测试依赖它；套件全绿即为证据，但这条变化本身值得写下来。
4. **`doctor` 的对外契约没有变**：D4 两个码、severity 与 evidence 词句都不动。这条是刻意的：修内部走法不该改变任何人读到的东西。
5. **`rebuild` 的输出多了两个键。** 它没有已发布 schema、也没有 golden fixture，所以语料不必重生——但这也正是"自由对象"的风险：多了键没有守卫会红。写在这里，免得下一次以为"它受 schema 保护"。
6. **`misdeclared_payloads` 返回 `(instance_id, store_path)` 对，`rebuild` 把它拍平成一行。** 同一个事实、两种呈现：`doctor` 需要字段化的两半，`rebuild` 的读者需要一句话。
7. **台账不变**：没有场景因此改判——S-027 的处置在 §66 已删除，这一轮是同一不变量的第五个读者。测试 **785 → 787**。

### 71.8 实施顺序

1. 先量 §66.6 pt 4 那条"已知不对称"到底有多大（71.2 发现一）；
2. **把两个读者的代码并排读**，看它们是不是真的只有输出形状不同（发现二）；
3. 用一个刻意构造的输入（深度 1）量出两份拷贝**共同**的盲区（发现三）；
4. **先排除测量工具的假象**（同码诊断被折叠），再决定"少报"是不是真的；
5. 把扫描搬进 `caps/layout.py`，保持"在不在 store 里"仍只有一处拼写；
6. 两个读者各自换用，核对 `doctor` 的对外形状**没有**变；
7. 补测试（三个形状 + 干净 root 的反方向），逐个验红（含修正一个方向写错的变异）；回写 §66 的旁注、模块表与计数，跑全量 + 切片 + 两种验收模式，提交。

## 72. 第 72 阶段：来源清单违反了自己写下的成长规则（`archive` 条目）

### 72.1 这一阶段要解决什么

`policy/sources.json` 的文件级 `notes` 写着一条成长规则：

> Growth path: only hosts and capabilities that were actually verified end up here. **An entry that has never been resolved successfully must not be added 'for completeness'.**

而同一份文件的 `archive` 条目自己的 `notes` 写着：

> listed for completeness of the pattern; not verified on this machine, so treat it as untested

**一句话是规则，另一句话是它的反例，两份都在同一个文件里，而且已经共存了好几轮。** §59 甚至已经"如实记录"过这条未验证状态（S59.5），却没有人问过：既然规则说不该在这里，它为什么还在？

### 72.2 实测：那个条目根本不可能被验证

对着真实上游量（draft §72）：

| 量什么 | 结果 |
|---|---|
| 制品 URL 模板的形状 | **对**：`7z<version_nodots>-extra.7z` 是真的（`7z2603-extra.7z` 存在）；模板替换 `{version_nodots}` 的实现在 `caps/sources.py`，加载期校验占位符 |
| 校验和 URL（`checksums.txt`） | **404**（`25.01` / `25.00` / `24.09` / `24.08` / `23.01` 逐版试过） |
| 另外五个可能的名字 | **全 404**：`checksums.sha256` / `SHA256SUMS` / `sha256sums.txt` / `7z2603-extra.7z.sha256` / `7z2603-extra.7z.txt` |
| 这个 release 的资产清单 | **没有任何 `.txt`**：只有 `.exe` / `.7z` / `.msi` / `.tar.xz` |
| 上游到底公布不公布摘要 | 公布，但只在**发布页 UI** 里（页面 HTML 里有 12 个 64 位十六进制串），而 `single` / `sha256sums` 两种格式都**取不到也解析不了** |

所以这个条目**按目录自己的契约不可能成立**：唯一可接受的摘要来源是"上游发布的校验和**文件**"（§23.3-1），而这样的文件在允许的 host 上不存在。

**今天的实际后果**（也实测了）：

```text
source resolve archive --version 26.03
  -> PROVENANCE_FAILED(7)  could not fetch the checksum file .../checksums.txt
     evidence: HTTP Error 404: Not Found

source resolve python        # 一个"没有来源"的能力
  -> NOT_FOUND(1)  no trusted source is declared for python
     evidence: a capability without a source is a reference-only capability, not an installable one
```

也就是说：**列着但取不到**给出的是一句 HTTP 404；**不列**给出的是一句能读懂的政策结论。后者才是 `archive` 今天真实的状态。

### 72.3 修法：执行规则，并把量到的东西留下

1. **移除 `archive` 条目**（这正是文件自己要求的）。它不是能力消失：`archive` 仍在冻结能力清单里，仍可被发现、`--mode reference` 登记、`--mode import` 导入——**只是没有可信来源，因此 v1 里不可从上游安装**。
2. **把实测写进文件级 `notes`**：为什么移除、量了什么、以及"一个不存在的摘要来源比没有条目更糟"。这样下一个人不会因为模板"看起来很合理"而把它加回来。
3. **把"逐条验证状态"升级成守卫**：`test_every_shipped_source_entry_states_how_it_was_verified` —— 每条 `notes` 必须写明**怎么验的**（`Verified online in draft §N` 或 `Verified offline (…)`），也就是把 §59 那句"逐条写明验证状态"从散文变成可执行检查。规则说"没验过的不该在这里"，而"验过"是关于过去的事实——**但它的证据是可检查的**，这就是这一轮把规则落到地上的方式。
4. **另加一条守卫钉住"缺席 + 理由"**：`archive` 不在目录里，**并且**文件里记着移除它的那次测量。只钉"不在"会让理由随条目一起消失，下一次有人带着同样的模板回来。

### 72.4 连带影响

`archive` 是项目里最常用的"通用单文件工具"例子（`scope decide archive`、`plan archive`、`import --capability archive`、真机验收），但它作为**来源条目**只被 `source resolve` 消费。所以移除只影响一个入口：`source resolve archive`。已在 §23 与 §59 的阶段记录里各加一句"§72 已改判"（**原地保留当时的数字**，不重写历史）。

### 72.5 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 把 `archive` 条目按原样加回来（note 写"for completeness"） | 危险 | **红**（两条守卫同时红：它没有验证陈述，且能力不该有来源） |
| 删掉文件里那段 §72 实测记录，但保持条目缺席 | 危险 | **红**（"缺席 + 理由"那条） |
| 让一条已验证条目的 note 失去验证陈述（改成"checked once"） | 危险 | **红**（证明守卫盯的是**陈述**，不是条目是否存在） |

第三条是关键：它证明守卫不是"只要文件里有 `Verified` 这个词就行"，而是**逐条**要求。

### 72.6 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S72.1 量出"规则 vs 反例"共存 | ✅ | 72.1 的两段引文（同一文件） |
| S72.2 量出模板哪一半对、哪一半不存在 | ✅ | 72.2 的表：制品命名对、六种校验和名字全 404、release 无 `.txt` 资产、摘要只在页面 UI |
| S72.3 量出两种失败模式的差别 | ✅ | `PROVENANCE_FAILED(7)+404` vs `NOT_FOUND(1)+政策结论` |
| S72.4 移除条目 + 记录实测 | ✅ | `policy/sources.json`：两条来源、文件级 `notes` 记着 §72 的测量 |
| S72.5 逐条验证状态变成守卫 | ✅ | `test_l1_sources.py#test_every_shipped_source_entry_states_how_it_was_verified` |
| S72.6 缺席 + 理由都被钉住 | ✅ | `test_l1_sources.py#test_the_archive_capability_has_no_source_and_the_reason_is_recorded` |
| S72.7 历史记录标注 | ✅ | §23 的 S23.1 与 §59 的 S59.5 各加一句"§72 已改判"，数字保留 |
| S72.8 计数与语料 | ✅ | 测试 **787 → 789**；审计检查 **74 不变**；台账**不变**；无需重生语料（policy 文件不进 golden）；AGENTS.md 的来源清单行与 §6 命令块各补一句 |

### 72.7 如实记录的边界

1. **这是一次能力收缩，而且是刻意的。** 移除条目让 `archive` 从"名义上可安装"变成"明确不可安装"。它换到的是：不再有一个 HTTP 404 冒充政策结论。**没有东西是"本来能用、现在不能用了"**——那个条目从来没有成功解析过。按 ADR-0021，这属于"放宽会摧毁更强规则"的反面：这里守的是诚实规则（§8），所以不收窄反而是错的。
2. **没有新增 host、没有新增解析格式。** 一条更"宽容"的做法是把校验和来源指向发布页 HTML 并加一种 `html_release_page` 格式，把页面里的摘要抠出来。**没做**：那等于为了保住一个条目而发明一种来源，而契约说摘要的来源必须是**上游发布的校验和文件**。真要支持它，那是契约变更（新的 checksum kind + 解析器 + schema），不是这一轮该顺手塞进去的东西。
3. **`rust-toolchain` 条目保留，尽管它不是冻结能力。** 量的时候顺手发现：`source resolve rust-toolchain` 能解析，但 `plan` 会以 `CAPABILITY_NOT_DECLARED` 拒绝它，因为冻结清单里没有这个 id。这不是缺陷：它由 ADR-0001/§59 为 **P2 的语言切换**建立并**真的验证过**，而"冻结能力"是另一条轴（P2 才冻结它）。两条守卫都只要求"验过"，不要求"已冻结"——这正是它们该管的范围。
4. **`archive` 仍是冻结能力、仍有白名单谓词。** 移除的只是**来源条目**。发现、`where`、`reference`、`import` 全都不经过 `sources.json`，所以它们不受影响（套件里那些用 `archive` 的路由/计划/导入测试全绿即为证据）。
5. **文件级 `notes` 现在长了**（多了 9 行测量记录）。这是有意的：那一段是**下一次有人想加条目时唯一会读的东西**。policy 文件不进 golden 语料，所以没有语料成本。
6. **守卫盯的是"验证陈述"的形式，不是真实性。** 一个撒谎的 note（写"Verified online in draft §999"）会通过。可检查的边界就在这里：**陈述的存在**可以自动化，**陈述的真假**不能——后者只能由像 §72 这样的实测轮次来管。写清楚，免得把守卫当成证据。
7. **台账与计数**：台账不变；测试 787 → 789；审计检查 74 不变。

### 72.8 实施顺序

1. 读到"规则"与"反例"在同一份文件里（72.1）——先确认这不是笔误，而是真的共存了几轮；
2. **对着真实上游量模板的每一半**：制品命名对、校验和来源不存在（72.2）；
3. 量**两种失败模式**的实际输出（列着但 404 vs 不列的 NOT_FOUND），确认哪一种更诚实；
4. 执行规则：移除条目，把测量写进文件级 `notes`；
5. 把"逐条验证状态"升级成守卫，并成对地钉住"缺席 + 理由"；
6. 给两条阶段记录加"已改判"旁注（保留原数字）；补测试、逐个验红；回写计数与文档，跑全量 + 切片 + 两种验收模式，提交。

## 73. 第 73 阶段：`zone` 这条词汇没有任何一份 agent 文档认识它

### 73.1 这一阶段要解决什么

§72 收尾时留下的账里有一条"`search`/`inventory`/`effective` 刻意不过滤 `zone`，这是有意的"。这一轮去量这句话**今天还成不成立**——结果量出的不是"判断过时了"，而是三件更基础的事。

### 73.2 实测

**发现一：那三个名字里有一个不是命令。** 用 `main()` 跑 `effective`（带 `--session` 也一样）得到：

```text
argument command: invalid choice: 'effective'
(choose from 'root', 'where', 'doctor', 'inventory', …, 'source')
```

`caps/effective.py` 是**模块**（被 `where.py` 与 `cli.py` 使用），但 CLI **没有** `effective` 子命令。而 ADR-0022 的"被否掉的替代方案"那一行只提到 `search --managed-only`——**§56.6 pt 2 把名单写宽了**。真正存在、且真的携带这个事实的面只有两个：`search` 与 `inventory`。

**发现二（核心）：`zone` 在任何一份 agent 文档里出现 0 次。**

| 文档 | `zone` / `Zone W` / `分区` 命中数 |
|---|---|
| `SKILL.md` | **0** |
| `agents/airoot.json` | **0** |
| `references/reason-codes.md` | **0** |
| `references/confirmation.md` | **0** |

而两个 agent 面**确实**在输出它：

| 响应字段 | 谁输出 |
|---|---|
| `where` 命中时的 `zone`、候选行的 `machine_discoverable` | `where --json`（ADR-0022 决策 5 引入） |
| `bindings[].zone`（`R`/`W`/`P`） | `inventory --json` |

**发现三：会暴露这件事的两条 lane 都没有读携带它的字段。** `where` 的 lane 读
`["found","executable","management","selection_reason","reason_code","evidence"]`——**连 `candidates` 都没有**；
`inventory` 的 lane 读 `["instances","external_references"]`——**没有 `bindings`**。

合起来：一个 agent 看到 `machine_discoverable: false`，**没有任何文档化的含义可以讲给用户**；看到
`bindings[].zone: "W"`，也不知道它要不要紧。ADR-0022 决策 5 的理由正是"排除必须**可见**"——可见的字段
配上一份不认识它的文档，等于只在机器那一侧可见。

**发现四（量的时候撞到的）：`AGENTS.md` 里的 `read` 路径条数早就漂了。** 它写 **117**；
实际 **129**（本轮改动前）。查历史：117 在 §1 写下时（commit `79e8147`）**是真的**，之后每一轮加 lane、
加字段都没回头改这句话——和 §54 修掉"测试总数没人对账"是**同一个失效模式**，只是这个数字没人管。

### 73.3 修法

1. **把词汇教给 Skill 层。** `SKILL.md` 在 `where` 的 `selection_reason` 清单之后新增一段"`zone` 怎么念"：
   R/W/P 的含义、那条恒定式、`machine_discoverable: false` **不是故障**（候选行仍然 `usable: true`、
   `health: healthy`）、以及**反向边界**——W 可以通过**显式** `--session`/`--project` 激活执行，
   所以"这个 W 绑定永远用不了"同样是错的。两个方向都写，因为只写一个方向就是另一条规则（ADR-0022 的测试也是这么成对的）。
2. **让两条 lane 读携带事实的字段**：`where` lane 增加 `zone` 与 `candidates[].machine_discoverable`；
   `inventory` lane 增加 `bindings[].binding_key` 与 `bindings[].zone`。这些路径随后被
   `test_l1_agent_read_fields` **逐条结构化解析**——所以"文档说要读的字段"从这一轮起是**被验证存在**的。
3. **两份文档互相钉住**：新守卫要求 `SKILL.md` 里有 `machine_discoverable`、有 R/W/P 词汇、并且**两个方向**都在；
   同时要求那两条 lane 真的注册了对应的 `read` 路径。文档少了字段名，或者 lane 不再读它，都会红。
4. **两个面说同一件事**：新测试用**同一个** W 绑定跑 `where` 与 `inventory`，断言 `inventory` 报
   `bindings[].zone == "W"`（`binding_key` 对得上）、`where` 把**同样那个**候选标成
   `machine_discoverable: false` 且 `usable: true`/`healthy`。两个读者、一个事实、一个结论。
5. **把 `read` 路径条数接到现实**：`AGENTS.md` 117 → **133**，并加守卫让它与
   `agents/airoot.json` 的实际条数**相等**（和测试总数一样，从"散文里的数字"变成"可对账的数字"）。

**没有改的**：`where` 的推导（仍是 `zone != "W"`）、`inventory` 的字段（**没有**给绑定行加
`machine_discoverable`——它已经有了 `zone` 这个原始事实，而"怎么念"现在有文档了；给
`registry-projection` 这个**已发布 schema** 加派生字段是另一个决定，收益不明而代价明确）、
以及 `search` 的行为（ADR-0022 已经把"顺手让 `search --managed-only` 排除 W"列为单独裁决，本轮不碰）。

### 73.4 红了才算数

| 变异 | 方向 | 结果 |
|---|---|---|
| 把 `machine_discoverable` 从 `SKILL.md` 里抹掉 | 危险 | **红** |
| 把 `candidates[].machine_discoverable` 从 `where` lane 的 `read` 里删掉 | 危险 | **红** |
| 把 `bindings[].zone` 从 `inventory` lane 的 `read` 里删掉 | 危险 | **红** |
| 让 `where` 的推导不再看 `zone`（`machine_discoverable=True`） | 危险 | **红**（跨面一致性测试） |
| 把 `AGENTS.md` 的 `read` 路径条数改回 117 | 危险 | **红**（计数守卫） |

### 73.5 完成情况（回写）

**本阶段已完成并验证。**

| 子阶段 | 状态 | 证据 |
|---|---|---|
| S73.1 量出 `effective` 不是命令 | ✅ | 73.2 发现一（`main()` 的 `invalid choice` 列表）；§56.6 已加"§73 更正"旁注 |
| S73.2 量出 `zone` 在 agent 文档里 0 命中 | ✅ | 73.2 发现二（四份文档逐个 grep） |
| S73.3 量出两条 lane 都没读它 | ✅ | 73.2 发现三（lane 的 `read` 列表） |
| S73.4 词汇进 `SKILL.md`（两个方向） | ✅ | `SKILL.md` 新增"`zone` 怎么念"；`test_l1_skill.py#test_the_skill_explains_the_zone_vocabulary_its_responses_carry` |
| S73.5 两条 lane 注册字段 | ✅ | `agents/airoot.json`；`test_l1_agent_read_fields.py` 逐条解析（含新增的 4 条） |
| S73.6 两个面一致 | ✅ | `test_l1_where.py#test_inventory_and_where_agree_about_zone_w` |
| S73.7 `read` 条数接到现实 + 守卫 | ✅ | `AGENTS.md` 117 → **133**；`test_l1_agent_read_fields.py#test_the_documented_read_path_count_is_the_number_this_module_resolves`（117 在 commit `79e8147` 时是真的） |
| S73.8 计数与语料 | ✅ | 测试 **789 → 792**；审计检查 **74 不变**；台账**不变**；无需重生语料（`where`/`inventory` 的输出形状没动，只动了**谁读它**） |

### 73.6 如实记录的边界

1. **没有给 `inventory` 的绑定行加 `machine_discoverable`。** 它已经有 `zone` 这个**原始事实**，而"W 意味着什么"现在有文档了；给 `registry-projection.schema.json`（**已发布** schema，`state/registry.json` 也是它）加一个派生字段，代价是 schema + 文档 + 语料，收益只是把同一句话换个地方写。**判断，不是遗漏**——如果将来出现"agent 反复把 W 念成坏了"的证据，那才是加它的理由。
2. **守卫盯的是"文档提到字段名 + lane 注册路径"，不是"agent 真的讲对了"。** 一个把 W 讲成故障的 agent 仍然能通过。可自动化的是**指针**，语义正确性只能靠这段文字本身——所以那段文字把**两个方向**都写死，并各举一个反例（"不要念成降级"、"不要说永远用不了"）。
3. **`search` 仍然不看 `zone`**：ADR-0022 的"明确不做"那一行仍然有效，本轮没有扩大范围。它的响应里也**没有** `zone` 字段，所以"它不过滤 zone"这句话今天是**真空成立**——这一点现在写清楚了（73.2 发现一）。
4. **`read` 计数守卫只对总数相等**：它不检查"哪条 lane 该读哪个字段"。lane 与字段的搭配靠 S73.5 的注册 + 人工判断；总数守卫只保证**引用文档里的数字不再静默漂移**（这正是发现四暴露的失效模式）。
5. **`AGENTS.md` 的 117 是"当时为真"的**：这一轮没有把它写成"一直错"，而是查出它来自 `79e8147` 并记录了漂移过程——与本项目对历史记录的处理一致（标注，不重写）。
6. **台账与计数**：台账不变（S-006 的处置在 §56 已删除，本轮是同一事实的**文档面**）；测试 789 → 792；审计检查 74 不变。

### 73.7 实施顺序

1. 先量那句话还成不成立：三个面各跑一遍（发现一）；
2. **grep 四份 agent 文档**（发现二）——"0 命中"是这一轮的核心事实；
3. 读两条 lane 的 `read` 列表（发现三），确认**没有**任何一条指向携带事实的字段；
4. 顺手量 `read` 条数并与 `AGENTS.md` 对账，**并查它当年是不是真的**（发现四）；
5. 教词汇（两个方向）、注册字段、两个面互相钉住、条数接到现实；
6. 逐个验红（含"推导不再看 zone"这种改行为的变异）；回写 §56 的旁注与计数，跑全量 + 切片 + 两种验收模式，提交。

## 74. 第 74 阶段：被打开的词汇里，四分之三的取值没有任何一份 agent 文档解释过

### 74.1 这一阶段要解决什么

§73 把 `zone` 这个词教给了文档。收尾时冒出来的是它的**一般形式**：`zone` 不是唯一一个"输出里有、文档里没有"的字段。这一轮把 `cli/schema/*.schema.json` 里的**每一个枚举**都量一遍，回答三件事：

1. 哪些枚举的取值，在任何一份 agent 文档里都**没有被命名过**；
2. 其中哪些这个 build **真的会写出来**——只有会写出来的，才是 agent 真会遇到的；
3. 怎么让这件事**不能重新漂移**：一张按 schema 组织的取值表 + 一组会红的守卫。

### 74.2 实测

**发现一：换一个口径，数字就换一副面孔——说明 token 级测量回答不了这个问题。**

同一批 schema、同一批文档（`SKILL.md`、`agents/airoot.json`、`references/reason-codes.md`、`references/confirmation.md`），两种匹配方式：

| 口径 | 枚举值集合 | 全部取值都被命名 | 部分被命名 | 一个都没被命名 |
|---|---|---|---|---|
| **宽松**：裸词命中（`degraded` 出现在文件里就算） | 58 | 11 | 33 | 14 |
| **严格**：反引号命名（`` `degraded` `` 才算文档点了名） | **61** | **0** | **17** | **44** |

严格口径下"全部被命名"是 **0**。两个数字都不是这一轮的重点，重点是**两者都回答不了真正的问题**：一个字段的词表**解释过没有**。它们只能回答"这个词在这个文件里出现过没有"，而"出现过"和"解释过"是两件事——这正是**发现三**。

**发现二（核心）：真正的缺口是"字段"级的。** 下面这些字段**这个 build 会写出**，而在本轮之前没有任何一份 agent 文档解释它的取值（† 见发现四）：

| 字段 | 会写出的取值 | 之前 agent 文档里的解释 |
|---|---|---|
| `doctor.diagnostics[].severity` 的 `critical` | 会（`caps/doctor.py` 4 处：根标记/卷身份/registry 元数据读不出来） | 无。文档只写了 `healthy/degraded/broken` 这一档的 `status` |
| `doctor.diagnostics[].remediation` 的 `inspect` | 会（"人看一眼"，AIROOT 没有对应命令） | 无。文档只解释了 `rebuild`/`repair` |
| `search.status` 的 `timed_out` | 会（crawl 撞上 `max_duration_ms`） | 无 |
| `search.data.freshness.state` 的 `unknown`/`stale` | 会 | 无（文档只有 `freshness.current` 那句"不是 USN 游标"） |
| `search.data.freshness.coverage` 的三个值 | 会 | 无 |
| `search.data.results[].verification` 的四个值 | 会 | 无 |
| `extension-envelope.status` 的 `timed_out` | 会 | 无 |
| `plan.operations[].kind` 的 `fetch/verify/stage/commit/expose/delete` | 会 | 无 |
| `plan.operations[].source_mutation` 的 `none` | 会（且两个 backend 都**只能**是 `none`） | 无 |
| `reference-plan.exposure.value_kind` 的 `REG_SZ`/`REG_EXPAND_SZ` | 会 | 无 |
| `registry-projection.external_references[].source_kind` 的 `pe_static` | 会（只读 PE 静态探测，这一版唯一的来源种类） | 无 |
| `registry-projection.external_references[].management` 的三个值 | 会 | 无 |
| `common.sideEffect` 的九个值 | 会（能力清单 + 两个扩展 manifest） | 只解释过 `none` |
| `common.lifecycle` 的四个值 | 会 | 无 |
| `common.binding.exposure` 的 `stable_launcher` | 会 | 无 |

**发现三：最难看的不是"没写"，是**同名词的假覆盖**。** token 级测量会把**别的字段**的取值算成覆盖：

- `common.health` 的 `degraded` 在文档里"存在"，但那是 `doctor.status` 的 `degraded`（`status` 由 severity 推出）；`health=degraded` 这一版**根本没有写者**。
- 文档里的 `stale` 是 `search` 的**新鲜度**（`freshness.state`），不是 `health` 的 `stale`。
- 文档里的 `verified` 是 `search` 的**结果核验**（`verification`），不是 `lifecycle` 的 `verified`。
- `directory` 在文档里出现，指的是 `search` 的 `results[].kind`；`fileManifestEntry.mode` 的 `directory` 同样没有写者。
- `declared` 出现在规划里（"声明的事实"），但 `external_references[].source_kind` 的 `declared` 这一版没有写者。
- `none` 无处不在，但 `binding.exposure` 的 `none` 今天没有写者。

**这就是为什么这一轮的产出必须按"字段路径"组织，而不能按"词"组织**——守卫也必须按 schema 路径解析（见 74.4）。

**发现四：有些值是**词表里有、这一版一个写者都没有**。** 这不是文档缺口，是**词表与实现的差距**，而它恰好是 agent 最容易混的一件事（"schema 允许"≠"这个 build 会做"）：

| 位置 | 没有写者的取值 |
|---|---|
| `common.zone` | `W`、`P`——`machine_discoverable: false` 这条规则**已经生效**，但这一版**没有任何对象落在 W 里**（会话激活写的是会话快照栈，不建 binding）；项目分区要 P6 |
| `common.health` | `degraded`、`stale`、`drifted`（ACL 漂移走 `DATA_ROOT_ACL_DRIFT` 诊断，索引过旧走 `freshness.state`） |
| `common.management` | `orphaned`（孤儿是 `doctor`/`rebuild` 的**发现**，不是行上的取值）、`project_owned`（只在过滤集合里被点到名） |
| `common.lifecycle` | `discovered`、`external_reference`、`unmanaged`、`planned`、`staged`、`verified`、`garbage_collectable`（登记进 store 直接就是 `installed`/`active`；"可回收"是 `tool gc --plan` **算出来的**判据） |
| `common.binding.exposure` | `session_env`、`project_binding`、`none`（只写 `stable_launcher`） |
| `common.source.kind` | `local_directory`、`registry` |
| `common.$defs.fileManifestEntry.mode` | `directory`（`caps/canon.py` 只产文件条目） |
| `common.$defs.scope` | `system` |
| `plan.operation` | `import_tool`、`recreate_runtime`、`root_relocate` |
| `plan.target.kind` / `instances[].kind` / `gc-plan items[].kind` | `runtime`（P5 之前没有 runtime 实例） |
| `plan.operations[].kind` | `rollback`（回滚是状态机在失败/恢复时做的，不是计划的一步） |
| `plan.operations[].source_mutation` | `delete`、`move`、`overwrite`（两个 backend 都被强制成 `none`） |
| `reference-plan.operations[].kind` | `rollback` |
| `doctor.diagnostics[].remediation` | `reapprove`（要重新拿批准，而这个 build 没有生产签发方——ADR-0024） |
| `search.status` | `error`、`cancelled` |
| `search.data.freshness.state` | `degraded`、`rebuilding`（`rebuilding` 只在 `caps/search.py` 的 `FRESHNESS_STATES` **声明元组**里出现） |
| `extension-envelope.status` | `cancelled` |
| `registry-projection.external_references[].source_kind` | `declared`、`public_locator`、`extension_handler`、`approved_execution`（只有 `pe_static`） |
| `where-response.source` | `project`、`search` |
| `runtime-instance.runtime_family` | **全部五个**（这个 schema 这一版没有任何写者） |
| `desired-manifest.source.kind` | **全部三个**（`source` 恒为 `null`） |
| `approval-token.approval_mode` | `policy` |
| `extension-manifest.*` | `adapter`、`broker`、`user_or_broker`、`execute`、`mutate_system`、`explicit_generation`、`stop_before_commit` |

`search.status` 的这一条值得单独说：**`error` 是 schema 里的失败档，但这个 build 走 `degraded`/`timed_out`**——所以"按 `error` 分支处理"在今天是一段**永不执行**的代码。这正是要有 † 的理由。

### 74.3 做了什么

1. **新增 `references/field-values.md`（按需参考）**：按 schema 分 15 节，每行是 `字段 | 取值 | 含义 | 本版谁写出`；带 **†** 的值表示"这一版没有任何代码会写出它"；`null` 单独说明（空值不是字面量，不参与 † 判定）；开头提醒**三套 `scope` 不要混**（绑定的 `system|machine|session|project`、持久化的 `user|machine`、分流的 `project|data-root`）；末尾列出**不在这张表里的四个 schema** 及理由（`broker-*` 是 P2 未实现、`managed-tool-instance` 与 `root-marker` 文件里没有枚举）。
2. **`SKILL.md` 新增《看到不认识的取值》一节**：什么时候读这张表、† 怎么读（"**不要为它写分支**：遇到 † 的值说明有人在手写 JSON 或版本变了，去核对而不是猜"）、`null` 不进 †、三套 `scope`、以及"取值域的权威是 schema / 语义的权威是核心契约"。
3. **`cli/tests/test_l1_skill.py`**：reference 集合加 `field-values.md`；新增一条守卫"入口文档必须指向它、且必须解释 † 与三套 scope"（一条**没人被告知去打开**的参考只是文件，不是参考）。
4. **新增 `cli/tests/test_l1_field_values.py`**：10 条守卫（见 74.4）。
5. **`AGENTS.md`**：`references/` 行登记新参考；阶段范围 `§31`–`§73` → `§31`–`§74`；计数同步。

### 74.4 守卫与验红

新文件 10 条守卫，每条都盯一个**会失效**的方向：

| 守卫 | 盯什么 |
|---|---|
| `test_every_documented_field_resolves_to_exactly_the_documented_values` | 每一行的值集合 == 在 `cli/schema/<name>.schema.json` 里按该路径解析出来的枚举（**两个方向**：schema 加值不写文档会红，文档编一个 schema 没有的值也红） |
| `test_no_value_is_documented_without_appearing_in_the_schema` | 同上，专门给"文档多写了值"一个更清楚的失败信息 |
| `test_every_documented_producer_path_exists` | "本版谁写出"那一列指向的文件必须存在；有 † 却没有"没有写者"标记也算红 |
| `test_each_daggered_value_has_no_writer_in_its_own_field` | † ⇔ 该值**在写这个字段的代码里**不出现；非 † ⇔ 出现。**双向** |
| `test_a_daggered_value_that_could_mean_only_one_thing_is_written_nowhere` | 21 个"不可能是别的字段的值"的词，只要 `cli/app/airoot/**.py` 里出现就当 † 失效——补上一条守卫**看不见"文档没点名的那种写者"**的漏洞 |
| `test_every_daggered_value_is_named_in_the_daggers_note` | 文档必须解释 † 与 `null`（否则读者会把每个 † 读反） |
| `test_every_enum_of_an_in_scope_schema_is_documented` | 每个在表内的 schema，文件里的每个枚举值集合都必须有行（`common` 例外，见 74.6 第 3 条） |
| `test_the_documented_and_exempt_schemas_cover_every_schema_exactly_once` | "在表内" ∪ "不在表内" == `cli/schema/*.schema.json`，且不相交——**加一个新 schema 必须做一次决定** |
| `test_every_row_belongs_to_a_schema_that_exists` | 节标题必须是真 schema，行必须有取值 |
| `test_the_in_scope_schemas_are_the_ones_an_agent_reads` | 15 个在表内 + 4 个在表外**恰好等于**这份名单，且总行数 ≥ 50——删一节必须是**故意**的编辑，不是 diff 事故 |

**判据的关键设计**：`†` 不能靠"文件里有没有这个词"来判——`search.status = "degraded"` 与 `freshness.state = "degraded"` 在同一个文件里，`health = "healthy"` 与 `"healthy"` 出现在别的语境里也是。所以其中 **10 个字段**用**写形状的捕获**（例如 `lifecycle_status\s*=\s*["']([A-Za-z_]+)["']`、`["']state["']\]?\s*[:=]\s*["']([a-z_]+)["']`、`Binding\([^)]*["']([a-z_]+)["']`），其余用文件存在性——后者只在"这个词不可能是同文件里另一个字段的取值"时才安全。

**逐个验红（9 个变异，方向都取"危险的那一侧"）**：

| 变异 | 预期 | 结果 |
|---|---|---|
| 文档编一个 schema 没有的值（`healthy` 旁边加 `fine`） | 红 | ✅ 红 |
| 文档少写一个 schema 有的值（删 `timed_out`） | 红 | ✅ 红 |
| 把没人写的值的 † 去掉（`reapprove`） | 红 | ✅ 红 |
| 给有人写的值加 †（`repair`） | 红 | ✅ 红 |
| 把一行的字段名改成一个不存在的路径（整行失效） | 红 | ✅ 红 |
| 把**在表内**的 schema 写进"不在表内" | 红 | ✅ 红 |
| "本版谁写出"指向一个不存在的文件 | 红 | ✅ 红 |
| **在实现里写出一个被标 † 的值**（`caps/lifecycle.py` 加 `lifecycle_status = "staged"`） | 红 | ✅ 红 |
| **在实现里写出一个文档没点名的 † 值**（`caps/doctor.py` 加 `"reapprove"`） | 红 | ✅ 红 |

最后一个变异是这一轮最想要的：它证明这组守卫能在**有人真的把 † 值实现出来**时把文档喊醒，而不是只在文档被编辑时才响。

### 74.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **792 → 803**（新文件 10 条 + `test_l1_skill.py` 1 条） |
| 审计检查（`test_l0_consistency.py` 的 `^def test_`） | 74 **不变** |
| 场景台账 | 108 **不变** |
| schema | 19 **不变**（这一轮**没有动任何 schema**、没有增删枚举值） |
| golden 语料 | **未重生**（没动任何对外 JSON 形状；`git diff --stat cli/tests/fixtures/golden/` 为空） |
| `AGENTS.md` 里的计数 | 3 处；`docs/AIROOT-v0.3-规范审查报告.md` 2 处（同一提交） |

### 74.6 如实记录的边界

1. **† 的判据绑在"本版谁写出"这一列上。** 那一列写错，两个方向会**同时**失效（把写者漏掉，† 就假绿）。最后一条守卫（21 个独特词"全文不许出现"）**部分**补上这个洞，但它只对"不可能是别的字段的取值、也不会出现在声明元组里"的词有效——所以 `rebuilding` **不在**那一档里：`caps/search.py` 有一个 `FRESHNESS_STATES = (..., "rebuilding", ...)` **声明元组**，而**声明不是写出**。这是判断，不是遗漏。
2. **`null` 不可测。** JSON 空值在代码里由 `None` 写出，不是字符串字面量——字面量扫描对它既证明不了"会写"，也证明不了"不会写"。所以 `null` 不进 † 的判定，它的含义单独写在"含义"列里。
3. **`common` 允许在别节被覆盖。** `$defs.externalReference` 的三个字段（`capability_kind`/`management`/`source_kind`）与 `registry-projection` 的 `external_references[]` 是**同一组词汇**，行写在 agent 真正遇到它们的那一节，`common` 节不重复。这是**判断**（避免同一条规则写两遍），代价是 `common` 的覆盖检查只能对"全文"而不是"本节"——写在该节的开头，守卫按这个例外读。
4. **守卫比的是"值集合"，不是"某一行有没有被写到错误的路径上"。** 值集合相同的两个字段共用一行时，覆盖检查看不出来（例如 `plan.target.kind` 与 `gc-plan.items[].kind`）。已知的漏法，写在这里而不是假装它不存在。
5. **表里的"含义"是"够做决定"的最短解释，不是契约。** 权威指针写在表头（取值域→schema，语义→核心契约）。守卫检查**指针与集合**，**不检查语义正确性**：一个把 `degraded` 讲错的 agent 仍能通过全部 10 条。可自动化的边界就在这里。
6. **这一轮没有实现任何"没有写者"的值**，也没有为了让表好看而删除任何枚举值。发现四那张表是**记录**，不是待办——`zone.W` 没有写者不代表 ADR-0022 失效（恰恰相反：`machine_discoverable` 已经按 `zone != "W"` 算，只是今天没有 W 对象），`runtime_family` 没有写者是因为 P5 还没到。
7. **`search.status.error` 今天永不会出现**：按它写分支等于写一段死代码。这一条是**行为性**的，不是文档性的——记在这里，将来 `error` 真的被写出来时，† 守卫会红，那时再改文档。

### 74.7 实施顺序

1. 先把 19 个 schema 的枚举**全部**枚举出来，两种口径各量一遍（发现一：口径一换数字就换）；
2. 把"会写出"与"不会写出"分开（发现四）——这一步决定 † 落在哪；同名词的假覆盖（发现三）在这一步暴露出来；
3. 按 schema 分节写 `references/field-values.md`，写完用脚本把每一行与 schema 对一遍（两个方向）；
4. 写 10 条守卫；对 **9 个变异**逐个验红，其中两个变异改的是**实现**（证明守卫盯的是"有没有人开始写这个值"，不只是"文档有没有被编辑"）；
5. 入口文档加《看到不认识的取值》一节 + 一条"必须指向它"的守卫；回写 `AGENTS.md` 与审查报告的计数；
6. 跑全量 + 旧切片 + 两种验收模式，确认 golden 语料**未被改动**（这一轮只动文档与测试），提交。

## 75. 第 75 阶段：守 reason code 的那条守卫用的是子串匹配，于是它把 `DEGRADED` 读成了 `CURRENT_SOURCE_DEGRADED`

### 75.1 这一阶段要解决什么

§74 的镜片换一个面。上一轮查的是**字段取值**（schema 枚举），这一轮查**码**：`reason_code` 与 `diagnostics[].code` 是 agent 真正**先读**的东西（`SKILL.md` 的原话是"先读 `reason_code`，再读退出码"）。三个问题：

1. 95 个注册码里，有没有哪个在 agent 速查（`references/reason-codes.md`）里**查不到**？
2. 哪些码**这个 build 根本发不出来**——一个 agent 如果为它写分支，写的是一段死代码；
3. 上面两件事，**守卫能不能看见**。

### 75.2 实测

**发现一（核心）：那条守卫用的是子串匹配，所以它一直在说谎。**

`cli/tests/test_l0_consistency.py` 里守这条的断言是 `code not in text`。而 `references/reason-codes.md` 从头到尾**没有**把 `DEGRADED` 作为独立的词写过——它一直藏在 `CURRENT_SOURCE_DEGRADED` 里，于是"每个注册的码都在这份速查里出现"这句话**看起来**成立。可 `DEGRADED` 是**真的会发出来**的码：

```text
caps/toolstate.py:147      return "DEGRADED"          # tool status/verify 撞到 warning 级问题时
caps/toolstate.py:225      "DEGRADED",                # lifecycle 说 active 却没有 active binding
```

也就是说：一个跑 `airoot tool verify` 的 agent 拿到 `reason_code: "DEGRADED"`，翻速查**查不到**它。把守卫换成词边界匹配（`(?<![A-Z0-9_])DEGRADED(?![A-Z0-9_])`），它立刻红：

```text
AssertionError: codes an agent can see but cannot look up: ['DEGRADED']
```

这是本轮的第一份证据，而且它不是"文档少写一条"这种程度的事——**是守卫本身从没查过这件事**。

**发现二：12 个注册码在映射表之外一处都没有。**

判据是一句可机械核对的话：**除 `exits.py` 的映射表外，`cli/app/airoot/` 里再没有第二处出现这个字符串**。

| code | 退出码 | 为什么这一版没有写者 |
|---|---|---|
| `ACL_MISMATCH` | 5 | ACL 的写一侧是 P2（现在只有只读观测 `DATA_ROOT_ACL_DRIFT` 诊断） |
| `DRIFT_DETECTED` | 2 | 泛化漂移码；这一版用更具体的 `REFERENCE_DRIFTED` / `DATA_ROOT_ACL_DRIFT` |
| `EXTENSION_TIMEOUT` | 2 | 扩展的**进程**运行时没落地：超时的是搜索自己（`SEARCH_TIMEOUT`） |
| `EXTENSION_CANCELLED` | 2 | 同上：没有可取消的扩展进程 |
| `EXTENSION_HEALTH_DEGRADED` | 2 | 扩展健康检查是 manifest 里的**声明**，没有执行者 |
| `EXTENSION_OUTPUT_INVALID` | 8 | 没有"扩展返回的文档"可校验 |
| `EXTENSION_PERMISSION_DENIED` | 9 | 同上 |
| `EXTENSION_DEPENDENCY_MISSING` | 9 | 同上 |
| `EXTENSION_SIDE_EFFECT_BLOCKED` | 9 | 副作用上限**已经在准入时判**（`CAPABILITY_NOT_DECLARED`），"运行时越界"还没有执行者 |
| `EXTERNAL_REFERENCE_DRIFTED` | 3 | 更具体的 `REFERENCE_DRIFTED`（退出码 2）取代了它：观测漂移不是"损坏" |
| `SEARCH_JOURNAL_GAP` | 2 | USN journal 断档是 **P2 的 native 索引**才会有的状态 |
| `SEARCH_PERMISSION_FILTERED` | 2 | 这一版读不动的目录报在 `warnings` 与证据里，不减少结果集 |

**发现三：这句话原本只写在 3 个码旁边。** 文档里已经有"这一版发不出来"的旁注——`DATA_ROOT_ACL_DRIFT`（"P2 才可能发射；现在只是注册"）、`SEARCH_JOURNAL_GAP`（"现在不会出现"）、`CONFLICT_MANAGED_BROKEN`（"仍注册但不再由 `where` 发射"）——**但没有一处是给另外 11 个码的**，它们读起来像是会出现的。同一个事实写了三遍、且只写了三分之一，这是 §74 的老毛病（同一条规则写两遍 / 门只装在一个入口）。

**发现四：诊断码那一侧是干净的。** `doctor` 的 26 个 `_diagnostic` 码全部在权威表里，权威表里的码也全部有发射路径——这条早就被 `test_no_diagnostic_code_is_promised_without_a_path_that_can_emit_it` 守着。所以这一轮**只**动 `reason_code` 那一侧，不去重复已有的门。

### 75.3 做了什么

1. **修守卫**（`cli/tests/test_l0_consistency.py`）：新增 `names_code(text, code)` 助手（词边界匹配），`test_the_reason_code_table_documents_every_registered_code` 与 `test_the_agent_facing_reason_code_reference_names_every_registered_code` 都改用它。**先改守卫、先看它红**，再去补文档——顺序是这一轮的证据链。
2. **`references/reason-codes.md`**：
   - `DEGRADED` 进退出码 2 的表，写清它什么时候出现（warning 级问题，对象**可用**），并明确"**不要**把 `DEGRADED` 念成 `BROKEN`"；
   - 新增《这一版发不出来的码（†）》一节：12 行表（含每个码为什么没有写者）、判据那一句话、与 `references/field-values.md` 同一套 † 约定、以及"**出现在声明里不算生产者**"的例外（`DATA_ROOT_ACL_DRIFT` 因此不在这张表里）；
   - 表头那句"每个注册的码都在这份速查里出现"改成"**作为独立的词**出现"，并把 §75 的来由写在括号里——下一个人不会再把子串匹配当成覆盖。
3. **新增 `cli/tests/test_l1_reason_codes.py`**（4 条守卫）：解析文档的那一节，与"哪些码有写者"的实测结果**双向**对账。

### 75.4 守卫与验红

| 守卫 | 盯什么 |
|---|---|
| `test_the_section_this_guard_reads_is_the_one_it_thinks_it_is` | 那一节还在、† 还在、表里的码都注册过（节被改名/删掉会红） |
| `test_a_registered_code_is_either_producible_or_recorded_as_unproducible` | **双向**：没人写的码必须在表里；在表里的码必须真的没人写 |
| `test_every_recorded_code_is_also_look_up_able_in_the_document` | 那张表是**指针**：每个被列为"发不出来"的码，上文仍要有自己的词边界条目 |
| `test_the_unproducible_section_is_not_a_second_copy_of_the_whole_table` | 一节如果吞下所有码就不再是信息（上限：注册码的一半） |

**逐个验红（6 个变异，方向都取危险的那一侧）**：

| 变异 | 预期 | 结果 |
|---|---|---|
| 文档从表里删掉一个码（`ACL_MISMATCH`） | 红 | ✅ 红 |
| 文档把一个**会发出来**的码写进表（`NOT_FOUND`） | 红 | ✅ 红 |
| 表里把码名写错（`SEARCH_PERMISSION_FILTERED` → `...FILTER`） | 红 | ✅ 红 |
| 整节标题被改掉 | 红 | ✅ 红 |
| **删掉 `DEGRADED` 的那一行**（让它只剩 `CURRENT_SOURCE_DEGRADED` 这条子串） | 红 | ✅ 红 |
| **让实现开始写一个被标 † 的码**（`caps/lifecycle.py` 加 `EXTENSION_TIMEOUT = "..."`） | 红 | ✅ 红 |

倒数第二个变异就是这一轮要抓的失效模式本身：**只有把守卫先改成词边界，"删掉这一行"才会红**。

### 75.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **803 → 807**（新文件 4 条） |
| 审计检查（`test_l0_consistency.py` 的 `^def test_`） | 74 **不变**（这一轮在审计文件里**改了两条**守卫、没有新增审计函数） |
| 场景台账 / schema | 108 / 19 **不变** |
| golden 语料 | **未重生**（没动对外 JSON 形状） |
| `AGENTS.md` 计数 | 3 处；审查报告 2 处（同一提交） |

### 75.6 如实记录的边界

1. **判据是"字面量在 `exits.py` 之外一处都没有"，不是"这条路跑不到"。** 它**保守**：一个码只要在注释、文档字符串或**声明表**里被引号括起来出现过，就会被算成"有生产者"，于是这张表可能**漏**掉一个码——不会**冤枉**一个码（把一个真会出现的码说成不会，是两种错误里更坏的那种，判据刻意避开它）。
2. **因此 `DATA_ROOT_ACL_DRIFT` 不在这张表里**：它出现在 `caps/doctor.py` 的不变量声明表（D1 组）与诊断里，所以"字面量在别处出现过"成立；而它作为 `reason_code` 的发射要等 P2。它靠上文那一行自己的旁注说明，**不靠这张表**。要把它也纳入，需要"按字段位置判定生产者"，那是另一个量级的工作——这一轮不做，但写在这里。
3. **`reason_code` 与 `diagnostics[].code` 共用一套词表**（守卫 `test_every_diagnostic_code_is_a_registered_reason_code`），所以"这个码会出现"必须说清**在哪个字段里出现**。表里那 12 个的准确说法是"不作为 `reason_code` 出现"。
4. **`EXTENSION_*` 那一族还有第二层不确定性**：`ext/envelope.py` 把 `reason_code` 当**参数**收下（`reason_code: str | None = None`），将来的扩展运行时可以把码透传上来。这一版没有那条路（`ext/` 只有 manifest、envelope、假扩展三个模块），所以现在说"没有写者"是真话；但守卫只能在**透传时写了字面量**的情况下发现变化——如果透传用的是变量，它看不见。已知边界。
5. **守卫比的是词边界，不是"读起来对不对"**：一个把 `DEGRADED` 讲成"坏了"的 agent 仍然能通过全部守卫（与 §74 同一条边界）。
6. **这一轮没有改任何码的注册、退出码映射或发射行为**，也没有为了让某一行好写而新增/删除码。唯一的实现改动候选（让 `EXTENSION_*` 有人写）被**明确拒绝**：那需要扩展运行时，那是 P2/P4 的事。

### 75.7 实施顺序

1. 先量：**先看守卫自己**（子串还是词边界），再看 95 个码的生产者分布；
2. 把守卫改成词边界，**先让它红**——红出来的那个 `DEGRADED` 就是这一轮的问题陈述；
3. 补文档：一个码（`DEGRADED`）+ 一张表（12 个发不出来的码）+ 一句判据 + † 的约定；
4. 写 4 条守卫；对 **6 个变异**逐个验红，其中一个是"让实现开始写一个被标 † 的码"；
5. 回写 `AGENTS.md` 与审查报告的计数；跑全量 + 旧切片 + 两种验收模式；确认 golden 语料未动；提交。

## 76. 第 76 阶段：守卫问"这个词被写下来了吗"时用的是子串——这一轮轮到守卫自己

### 76.1 这一阶段要解决什么

§75 修的是**一条**守卫（reason code 的子串匹配）。这一轮的问题是它上面一层：**同一种写法在审计模块里还有几处？** 判据可以写成一句话：

> 凡是"某个**词表项**（命令名、类别、解锁词、码、schema 名）有没有出现在某份文档里"的问题，都必须**按词**问；"某段话/某个标题还在不在"才是子串的问题。

### 76.2 实测

**发现一：命令守卫只有一个边界。** `test_every_implemented_command_is_named_in_the_documentation` 用的模式是 `re.escape(entry) + r"\b"`——`\b` 只管**右**边。于是 `scope\b` 会被 `telescope` 满足、`list\b` 会被 `checklist` 满足。而命令名恰恰是最短的那类词（`add` / `list` / `plan` / `scope` / `check`），"某个更长的词以命令名结尾"在七份文档的合并语料里根本不是稀奇事。

**发现二：分流类别守卫用的是裸子串。** `test_every_deferral_category_is_explained_in_the_entry_document` 里是 `category in agents` 与 `unblocker in agents`。这一组词汇天然互相嵌套：`needs-capability` ⊃ `capability`、`needs-admin` ⊃ `admin`、`needs-decision` ⊃ `decision`。

**发现三：schema 提及守卫也是子串。** `name not in corpus`，而 schema 名（`plan` / `common` / `transaction` / `where-response`）同样是短词。

**发现四（量过才知道）：一个猜疑被量掉了。** 由发现二我推出"解锁词 `decision` 一直靠 `needs-decision` 冒充被解释过"。把 AGENTS.md 里那一处 `decision` 改掉之后实测：

```text
old check `'decision' in agents`          -> True
new check names_token(agents, 'decision') -> True
```

两种问法都仍然是 True——因为 AGENTS.md 里 `decision` **本来就作为独立的词**出现在别处。所以这一条**不是**缺陷。它被写进这一轮的记录，是因为"以为找到的缺陷"和"找到的缺陷"要一样如实：变异脚本里留着那两行输出，而不是把它删掉。

### 76.3 做了什么

1. **把 §75 的 `names_code` 提升成通用的 `names_token(text, token, *, word="A-Za-z0-9_")`**：两侧都不许是词内字符。`names_code` 保留为它的一个具名用法（码是单一 token），命令名/类别/schema 名传 `word="A-Za-z0-9_-"`（它们自己带连字符）。
2. **三条守卫改用它**：命令提及（发现一）、分流类别与解锁词（发现二）、schema 提及（发现三）。
3. **新增两条守卫**：
   - `test_the_vocabulary_helper_rejects_a_name_nested_in_a_longer_word`：**两个方向**都钉——前缀嵌套（`telescope` / `checklist`）与后缀嵌套（`scopes` / `listings`），再加三个"确实提到"的正例。**第一版只写了前缀那一侧**，是这一轮的变异（把 helper 的右边界删掉）让它绿着通过，才发现漏了一个方向——这条测试的 docstring 把这个过程写下来了。
   - `test_the_audit_module_asks_vocabulary_questions_through_the_helper`：**读自己的 AST**。两种形状必须要么改用它、要么进白名单并写出理由：`变量 in 文档名`（子串查词表）与带单边 `\b` 的 `re.search`。常量对文档的查询（`"--token-file" in text`）**故意不受限**——那问的是"这一段还在不在讲那件事"。
4. **白名单 `SUBSTRING_CHECKS_ARE_FINE`（4 条，各带理由）**：三条是标题/相邻行（`heading` / `following` / `REVIEW_STATUS_HEADING`），一条是整句诚实声明（`ISSUER_PENDING`）。它们是"整句存在性"，子串正是对的问题。

### 76.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| helper 去掉**左**边界 | 红 | ✅ 红 |
| helper 去掉**右**边界（第一版测试漏掉的方向） | 红 | ✅ 红 |
| **命令守卫改回单边 `\b`**（AST 那条守卫要抓的就是这个） | 红 | ✅ 红 |
| ~~AGENTS.md 不再把 `decision` 写成解锁词~~ | ~~红~~ | ⛔ **不成立**（发现四：两种问法都仍然 True，因为 `decision` 在别处独立出现） |

第三个变异是这一轮的重点：它证明"守卫有没有走 helper"这件事**本身**是钉住的，靠的不是评审者记得，而是 AST。

### 76.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **807 → 809** |
| 审计检查（`test_l0_consistency.py` 的 `^def test_`） | 75 → **76**（这一轮**新增了一条**审计函数——前两轮都只是改，所以这是本系列里这个数字第一次变大） |
| 场景台账 / schema | 108 / 19 **不变** |
| golden 语料 | **未重生**（没动对外 JSON 形状） |
| `AGENTS.md` / 审查报告计数 | 同一提交更新 |

### 76.6 如实记录的边界

1. **AST 那条守卫检查的是语法形状，不是"问得对不对"。** 把 `code not in text` 写成 `text.find(code) >= 0`（同样是子串）就绕过去了；`.count()`、`in` 套一层临时变量也一样。它挡的是**这一模块历史上真的出现过的那两种写法**，不是所有子串写法——要挡全部得写 lint 规则集，这一轮不做，写在这里。
2. **白名单是理由制的，进了白名单就不再被检查。** 与本项目其它白名单一致：它的价值是"新增一条必须是有意的"，不是"这一条是对的"。
3. **`word` 的字符集是逐处传的，传错会静默改变松紧。** 给 `where-response` 用默认集时 `.` 之后算边界（正好是对的）；给 `plan` 用默认集则 `plan-x` 也会被算成"提到过 `plan`"。这是**判断**，没有从数据里推导出来。
4. **这一轮没有把审计模块里其它上百处 `in text` 都改成 helper。** 它们查的是整句/整标题/整段（"那句诚实声明还在吗"），子串是对的问题。只把**变量对文档**的查询挑出来——这条界线是本轮的核心判断，写在守卫的 docstring 里。
5. **`names_token` 只回答"是不是作为独立的词出现"**，不回答"用法对不对"：把 `DEGRADED` 讲成"坏了"的文档照样通过（与 §74/§75 同一条边界）。

### 76.7 实施顺序

1. 先把 §75 的教训归纳成一句判据（**词表项按词问**），再拿这句话去找同一写法的其它实例；
2. 量出三处守卫 + 一个"以为有、量了没有"的猜疑（发现四）；
3. 修 helper（两侧边界）与三条守卫；写两条守卫（一条钉 helper 的两个方向，一条读 AST 钉"必须走它"）；
4. **3 个变异逐个验红**，其中一个是"把守卫改回旧写法"——只有 AST 那条能抓到它；被否掉的猜疑也跑一遍并记下来；
5. 回写计数（含审计检查 75 → 76）、跑全量 + 旧切片 + 两种验收模式、确认 golden 语料未动、提交。

## 77. 第 77 阶段：schema 没枚举的标签——`evidence[].kind` 与 `where.selection_reason`

### 77.1 这一阶段要解决什么

§74 查的是 **schema 枚举过**的字段取值。这一轮问题换了：**schema 根本没枚举、但代码天天在写的标签怎么办？** schema 只说 `type: string`，于是"合法取值"这件事**没有 schema 可查**——权威只能是代码。这正是它一直没人量的原因，也是它比前两轮更危险的地方：一个查不到手册的词，agent 只能自己编一个意思。

候选恰好有两个，而且都在 agent 最先读的位置上：

- `evidence[].kind`：每个命令的响应都带 `evidence[]`，`detail` 是人话、`kind` 是**分类标签**；
- `where` 的 `selection_reason`：回答"为什么选了它/为什么没选"。

### 77.2 实测

**发现一：`evidence[].kind` 有 27 个值，其中 14 个在任何 agent 文档里一次都没出现。**

| 分类 | 例子 | 文档 |
|---|---|---|
| 搜索 | `search_roots` / `search_policy` / `search_source` / `search_index` / `search_scope` | **全部零命中** |
| 计划/分流 | `whitelist` / `memory` / `project_manifest` / `source` / `high_risk` / `generic_tool` / `fallback` | `high_risk`/`memory`/`whitelist`/`project_manifest`/`generic_tool` 零命中 |
| 发现/选择 | `junction_target` / `weak_evidence` / `version_set` / `layout` / `effective` | `junction_target`/`weak_evidence`/`effective` 零命中 |

剩下那些"有命中"的也**不能算解释过**：`binding` / `registry` / `policy` / `query` / `fallback` 是**常见词**，命中来自别的意思（§74 的"同名词假覆盖"在这一族里更严重——因为这里连 schema 枚举都没有，没人被迫去数）。

**发现二（核心）：`where` 有**两个像码的字段**，而它们不是同一套词表。**

| 字段 | 词表 | 注册在 `exits.py`？ |
|---|---|---|
| `reason_code` | 95 个注册码 | 是（权威表 + `reason-codes.md`） |
| `selection_reason` | **12 个值** | **只有 3 个**（`CURRENT_SOURCE_DEGRADED` / `VERSION_UNSATISFIED` / `NOT_FOUND`） |

`SKILL.md` 早先的 `where` 小节把这些值**和 reason code 混在同一个 bullet 列表里**讲；`test_l1_skill.py` 的"参考里不许出现假码"守卫只查 `reason-codes.md`，而 `test_every_reason_code_the_app_can_raise_is_registered` 只扫 `AirootError(`/`"reason_code":` 这两种写法——`_selection_reason_for` 用的是 `return "PROJECT_MANAGED_HEALTHY"`，**两条守卫都看不见它**。于是：一个 agent 拿到 `selection_reason: MACHINE_MANAGED_HEALTHY_BY_POLICY`，去 reason code 表里查——查不到，而表里也没有任何东西告诉它"你查错表了"。

**发现三：量法本身要小心。** 第一版脚本用"`{kind, detail}` 相邻"来认 `evidence` 行，正确排除了 `operations[].kind`（那些是 `{kind, description}`）与 registry 行的 `kind`；第一版 `selection_reason` 提取却把 `CONFLICT_MANAGED_BROKEN`（模块里的另一个常量）也算了进来——**是守卫第一次跑红把它暴露出来的**，随后把提取器收紧成"只读决定这个字段的两个位置"（`_selection_reason_for` 的返回 + `where()` 里的赋值，常量跟到字面量）。

### 77.3 做了什么

1. **`references/field-values.md` 新增《schema 没有枚举的标签（判据在代码里）》**：两节，27 + 12 行，每行仍然带"**本版谁写出**"这一列（证据指针）。取值列里的 **∘** 表示"这个值**同时**是注册过的 reason code"——只有 3 个 `selection_reason` 带它。开头写明：这一节的权威是**代码**，不是 schema。
2. **`SKILL.md`** 的《看到不认识的取值》加一条：这两组是自由字符串、也在那张表里，并点名"`where` 有两个像码的字段，**别拿一个去查另一个的表**"；权威段补上 `test_l1_label_vocabularies.py`。
3. **新增 `cli/tests/test_l1_label_vocabularies.py`**：5 条守卫（见 77.4），解析文档自己的表，与**代码里真的会写出的值**双向对账。
4. 顺手修 `test_l1_skill.py` 里那条守卫，让它同时要求入口文档提到这两组标签。

### 77.4 守卫与验红

| 守卫 | 盯什么 |
|---|---|
| `test_the_section_this_guard_reads_is_the_one_it_thinks_it_is` | 那一节还在、两小节都在、都有行（节被改名会红） |
| `test_the_evidence_kinds_the_app_writes_are_exactly_the_documented_ones` | **双向**：代码写的 27 个必须有行；文档里的每个值都必须真的会被写出 |
| `test_the_selection_reasons_the_app_writes_are_exactly_the_documented_ones` | 同上，判据只读决定这个字段的两处代码（返回语句 + 赋值语句，常量跟到字面量） |
| `test_a_selection_reason_marked_as_a_reason_code_really_is_one` | ∘ ⟺ 在 `exits.REASON_EXIT` 里；并断言这个标记**不是空的**（否则整条说明是空话） |
| `test_every_documented_label_names_a_writer_that_exists` | "本版谁写出"指向的文件必须存在 |

**6 个变异逐个验红**：

| 变异 | 预期 | 结果 |
|---|---|---|
| 文档删掉一个 evidence kind | 红 | ✅ 红 |
| 文档编一个不存在的 evidence kind | 红 | ✅ 红 |
| 文档删掉一个 selection reason | 红 | ✅ 红 |
| **实现里新写一个 evidence kind** | 红 | ✅ 红 |
| 给一个**不是**注册码的 selection reason 打上 ∘ | 红 | ✅ 红 |
| **实现里改一个 selection reason 的字面量** | 红 | ✅ 红 |

### 77.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **809 → 814**（新文件 5 条） |
| 审计检查（`test_l0_consistency.py` 的 `^def test_`） | **76 不变** |
| 场景台账 / schema | 108 / 19 **不变**（又是没动 schema 的一轮：这一轮的结论恰恰是"schema 本来就没枚举它们"） |
| golden 语料 | **未重生** |
| 一处**被守卫拦下来的**改动 | 新表里我写了"（退出码 2，不是失败）"，`test_every_unbound_exit_number_is_a_census_and_not_a_silence` 立刻红：那会多出一个**没有相邻码**的退出码声明（census 8 → 9）。**没有去改 census**，而是把那句话改成指向 `reason-codes.md` 的同名条目——同一个事实不在两处记两遍 |

### 77.6 如实记录的边界

1. **这一节的权威只能是代码，所以"文档对代码"是它唯一能有的守卫形状**：它保证两边一致，**不保证含义写对**（与前两轮同一条边界）。schema 仍然是"这个字段允许任何字符串"，本轮**没有**去给它加枚举——把 27 个 evidence kind 写进已发布 schema 是一次契约变更（改枚举＝新 schema id，ADR-0003），而它们**本来就只是给人看的证据标签**，收紧它没有收益。这是判断，不是遗漏。
2. **∘ 只标"也是注册码"**，不标"哪个字段会用哪个词"：`selection_reason` 与 `reason_code` 的取值**至今有 3 个重叠**。重叠本身不是缺陷（同一个事实在两侧出现是合理的），但它是"两个词表"这件事必须写在文档里的理由。
3. **`evidence[].kind` 的判据是"`{kind, detail}` 相邻"**：将来若有人写成 `{"detail": ..., "kind": ...}`（顺序反过来）或先构造再 append，这个提取器会漏。**漏的后果是"新 kind 不被守卫发现"**，所以这一条值得记住；补法应该是把判据改成"evidence 数组里的元素"而不是字面相邻——本轮没做。
4. **没有枚举 `warnings[]`**：它是自由**句子**（不是标签），给句子做词表没有意义。这一条写在 SKILL 之外、留在本记录里。
5. **`data.fallback.kind` 只写 `crawl` 一个值**，§74 已经在 `search-response` 那节用散文说明了它（"非 `null` 时它的 `kind` 恒为 `crawl`"），所以本轮没有再为它单开一节。

### 77.7 实施顺序

1. 先问"schema 没枚举的字段有哪些"，再挑出**每个响应都带、且 agent 要读**的两个；
2. 量：`{kind, detail}` 相邻取 evidence 行；`selection_reason` 只从决定它的两处代码取；
3. 写文档两节（27 + 12 行，各带证据指针），把"两个词表"这件事写在节首；
4. 写 5 条守卫；**6 个变异逐个验红**（含"实现里新写一个标签"与"实现里改一个标签"）；
5. 被既有守卫拦下的那一处（未绑定的退出码声明）按它的指示**改文档而不是改 census**；
6. 回写计数、跑全量 + 旧切片 + 两种验收模式、确认 golden 语料未动、提交。

## 78. 第 78 阶段：信封自己的字段——`operation` / `origin` / `size_source` / `version_source` / `outcome`

### 78.1 这一阶段要解决什么

§77 立起的判据是"**schema 没枚举、代码天天在写的标签**要单独有一节、权威是代码"。这一轮把这句话**系统化**地问一遍：把 app 里所有"`字段`: `字面量`"的赋值收集起来，按字段分组，看哪些字段有 ≥2 个取值、却没有任何 schema 枚举它。§77 只处理了 `evidence[].kind` 与 `where.selection_reason`——因为它们是"我要解释结论"的标签；这一轮问的是**信封自己**的字段：我在读哪份文档、为什么这样分流、这个数字是哪来的。

### 78.2 实测

**发现一：最刺眼的是 `operation`。** 每个 `--json` 响应的顶层都有 `operation`，它回答"**我手里这份文档是哪条命令产出的**"。schema 只在 `search-response`/`broker-request` 里枚举过它；其余文档连信封 schema 都没有，所以**13 个取值里 10 个在任何文档里都查不到**：`apply_reference_exposure` / `explain` / `forget_all_persist` / `forget_reference_persist` / `gc_plan` / `pin` / `plan_dry_run` / `rebuild_plan` / `refresh` / `status`。剩下 3 个（`search`/`uninstall`/`rebuild`）只是因为它们**同时是命令名**才"看起来有文档"——这正是 §74 起的"同名词假覆盖"。

**发现二：`origin` 有两个意思。** 同一个词在两个位置各有一套值：

| 位置 | 取值 | 数量 |
|---|---|---|
| 路由 origin（`scope decide` 顶层、`metadata.import.routing.origin`） | `generic_tool` / `high_risk` / `memory` / `project_manifest` / `no_capability` / `unclassified` / `unverifiable_source` | 7 |
| 载荷 origin（`metadata.import.origin`） | `local_file` | 1 |

其中 `no_capability`、`unclassified`、`unverifiable_source` 三个**一个文档都没提过**。一个 agent 看到 `origin: unclassified` 会以为"没分类"是某种错误——它其实是"所有规则都没命中、也没有显然的兜底"这个**正常结论**。

**发现三：`size_source` / `version_source` 是"这个数字/版本是哪来的"。** 它们在 §70 的实现里存在（批准一个要复制 250 MB 的人有权知道体积是量的还是别人报的），但**从来没有文档**：`declared`（调用方声明）/ `unknown`（没有 —— **不猜**）/ `measured-from-the-file`（真的逐字节量过）；`declared-by-caller` / `no-version-in-the-file`。

**发现四：两个**解析器都不知道"节在哪里结束"**。** 这一轮把 5 张新表加进 `references/field-values.md` 的那一刻，§74 的守卫**立刻三连红**：它的行解析器只在遇到"schema 标题"时更新 `current`，所以标签节的 4 列表格被当成 `common` 的行。§77 的标签解析器是同一类毛病：它只认**光秃秃的** `### \`字段\`` 标题，于是 `### \`operation\`（每个响应信封的顶层）` 这种带后缀的标题不被识别，它下面的行**漏进了上一个字段**（`where.selection_reason` 里冒出 13 个"命令名"）。

> 这是 §75/§76 那条镜片的第三次现身，而这次落后在**解析器**上：**守卫的判据对不对，取决于它知不知道自己在读哪一段。**

### 78.3 做了什么

1. **`references/field-values.md` 的《schema 没有枚举的标签》再添 5 节**：`operation`(13) / `origin`(8) / `size_source`(3) / `version_source`(2) / `outcome`(2)，每行仍带"本版谁写出"证据指针；`origin` 那一节把**两个意思**分表说清。
2. **修两个解析器的节边界**：
   - `test_l1_field_values.py`：任何 `## ` 标题（不只是 schema 标题）都**结束上一节**；
   - `test_l1_label_vocabularies.py`：标题正则改成 `^###\s+\`名\``（允许后缀），且**每一段**都由下一个 `###` 收尾。
3. **`test_l1_label_vocabularies.py` 升级成"按字段驱动"**：新增 `EXPRESSION_PROBES`（每个字段怎么取值）+ `FIELD_FILES`（`operation` 只从四个**响应构造器**取，因为计划的 `operation` 是 `plan.schema.json` 自己的字段、已在 schema 节里documented）。取值时读**整个赋值表达式的右半边**并取其中每个字面量——所以 `size_source="declared" if ... else "unknown"` 贡献**两个**值，而不是一个。同时剥掉表达式里的"`键`:"（字段名自己不是它的取值）。
4. **`AGENTS.md` 的 `references/` 行**补上这一节（§77 时遗漏，这一轮一起补）。

### 78.4 守卫与验红

| 守卫 | 盯什么 |
|---|---|
| `test_every_envelope_label_the_app_writes_is_the_documented_one` | 5 个新字段逐个**双向**对账（代码写的必须有行；文档里的必须真的会被写） |
| （沿用）`evidence[].kind` / `where.selection_reason` / ∘ 标记 / 证据指针存在 | 见 §77 |

**7 个变异逐个验红**：

| 变异 | 预期 | 结果 |
|---|---|---|
| 文档删掉一个信封 `operation` | 红 | ✅ 红 |
| 文档编一个不存在的 `operation` | 红 | ✅ 红 |
| 文档把带连字符的取值写错（`measured-from-the-file`） | 红 | ✅ 红 |
| 实现里新写一个路由 `origin` | 红 | ✅ 红 |
| 实现里改掉一个 `size_source` 字面量 | 红 | ✅ 红 |
| 标签解析器退回"只认光秃秃标题" | 红 | ✅ 红 |
| **文档里塞一张 4 列表格**（节内） | 修好了就**绿**；把 §74 的解析器退回旧写法就**红** | ✅ 两个方向都对 |

最后一对是这一轮的关键证据：它同时证明了修复**有效**（新代码下杂表被忽略）和修复**必要**（旧代码下杂表被读成 `common` 的行）。

### 78.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **814 → 815**（新增 1 条；§77 的 5 条仍在） |
| 审计检查（`test_l0_consistency.py`） | **76 不变** |
| 场景台账 / schema | 108 / 19 **不变**（仍然没动 schema） |
| golden 语料 | **未重生** |

### 78.6 如实记录的边界

1. **`action` 被排除，理由是量不出来。** 它的字面量集合被 argparse 自己的关键字污染（`add_argument(..., action="store_true")` 与真正的 `"action": "no_action"`/`resumed` 混在一起），而这一轮的判据是"按字段收集字面量"——**判据在这个字段上不成立**，所以不写它，也不假装它被覆盖了。要覆盖它得换判据（按结果文档的位置取），那是另一件事。
2. **`backend_id`（`portable_file` / `fake_fixture`）也是标识而不是词表**：它们是**冻结清单里的身份**（`policy/capabilities.json` + `caps/backends/`），不是"取值域"。记在这里，不塞进标签表。
3. **`operation` 的判据被限制到四个响应构造器**：这是一个**判断**。计划的 `operation` 是 `plan.schema.json` 的字段（§74 已经逐值解释过，含 † 的三个），若把 `tx/` 也算进来，两种 `operation` 会被混成一张表——那正是这一节存在的意义（说清"哪个字段"），所以宁可限制判据。
4. **这一轮仍然没有动 schema。** 13 个信封 `operation` 值、8 个 `origin` 值都**不该**进 schema：它们描述的是"这份文档是谁"，属于信封协议而不是数据契约；收紧成枚举意味着每加一个命令都要发新版 schema，收益是零。
5. **守卫仍然只保证"两边一致"**，不保证含义写对（§74/§75/§76/§77 同一条边界）。
6. **§74 的守卫这次三连红**，值得记下来：不是因为文档错了，而是因为**它读不出节边界**。守卫红了要看**是哪一边错了**——这一轮是守卫。

### 78.7 实施顺序

1. 把 §77 的判据系统化：收集所有 `字段: 字面量` 赋值，按字段分组，筛出 ≥2 取值且无 schema 枚举的；
2. 人工过一遍这份清单，分三类：**要写的**（这就是本轮 5 个字段）、**判据不成立的**（`action`）、**身份不是词表**（`backend_id`）——后两类写进边界而不是塞进表里；
3. 写文档 5 节；**加进去的瞬间看守卫红**，由此发现两个解析器不知道节边界；
4. 修解析器 + 升级守卫为按字段驱动；**7 个变异逐个验红**，其中一对是"杂表在有/无修复下的两个方向"；
5. 回写计数与 `AGENTS.md` 的 `references/` 行；跑全量 + 旧切片 + 两种验收模式；确认 golden 语料未动；提交。

## 79. 第 79 阶段：机器可读的调用元数据，没有一条守卫在管它里面的动词

### 79.1 这一阶段要解决什么

`agents/airoot.json` 是 agent **照着调用**的东西：33 条"问题 → 命令 → 该读哪些字段"。它的两半**各有一条守卫**：

- §42 把每条 `read` 路径在**真实文档**上解析一遍（跑出一个真 root，逐个命令取 JSON）；
- 一条审计检查把文档里出现的每个 `--option` 在**真实解析器**上验一遍——它**也读这个文件**（`for meta["invocation"]`）。

这一轮问一个很窄的问题：**这两条守卫之间有没有缝？** 也就是"这个文件里的**动词与嵌套子动词**，谁在管？"

### 79.2 实测

**发现一（核心）：没有人在管。** 那条 option 守卫的解析函数遇到不认识的动词时**直接返回**，注释写着：

```python
if verb not in verbs:
    return None, []  # an unknown verb is guard group 4's finding, not this one's
```

而"第四组"扫的是**文档**（`AGENTS.md` + `references/*.md`），**不含这个文件**。实测：把 `["root", "status"]` 改成 `["rooot", "status"]`——新守卫红，**旧守卫绿**。也就是说：元数据里写一个不存在的动词，两条守卫都不响，而 agent 会照着它调用。

**发现二：嵌套子动词的拼写错误能不能被抓，取决于标志，而不是取决于子动词。** 旧守卫只在 token 落在子动词表里时才**下降**；拼错时不下降，于是它留在父命令上校验标志：

| 变异 | 旧守卫 | 为什么 |
|---|---|---|
| `["env", "activte", "<id>", "--shell", "powershell"]` | **红** | 但是**碰巧**：`--shell` 在 `env` 上不存在，它抓到的其实是症状 |
| `["env", "activte", "<external-id>"]`（没有会露馅的标志） | **绿** | 父命令的选项集里没有不认识的东西，于是什么也没发生 |

两条都实测过（见 79.4 的两行探测）。所以"子动词拼错会被抓到"这句话**只在特定写法下成立**。

**发现三：33 条调用今天全部能干净地走过真实解析器**（遍历了 52 个嵌套子动词）。所以这一轮是**在漂移之前**把门装上，而不是事后补——这一点值得写清楚，因为它决定了这一轮的产出是"守卫"而不是"修文档"。

**发现四：解析器有 24 个动词，lane 只覆盖 19 个。** 缺的五个里有一个是**真缺口**：

| 动词 | 说明 |
|---|---|
| `data-root` | 管家域的**第一步**（不注册数据根，`discover`/`adopt` 无事可做），`AGENTS.md` 的操作面里就有它——**却没有 lane** |
| `approve` / `install` | 需要签发方，而这个 build 没有生产签发方（ADR-0024）⇒ lane 只会指向一条走不到底的路 |
| `unadopt` | 规划 §15.1 的冻结名，CLI 把它注册成 **`forget` 的兼容别名** ⇒ 它没有问题，lane 在 `forget` 上 |
| `extension` | 扩展诊断（`extension list` / `extension status <id> --operation …`），**不在** agent 的问题面上（`SKILL.md` 的命令地图没有它） |

后四个**各有理由，但一个都没写下来**——一个读者（或 agent）看不出"没有 lane"是有意的还是漏了。

### 79.3 做了什么

1. **`agents/airoot.json` 补一条 lane**：`data-root add <path> --id <data_root_id> --role <runtime|tool|mixed>`，读 `data_root.data_root_id` / `data_root.path` / `data_root.role` / `data_root.volume_serial` / `reason_code` / `files_touched`，并带一句 note："管家域的第一步；注册**不写任何文件**（`files_touched` 是 0）"。
2. **新增 `uncovered_verbs`**（同一个文件，与 `not_implemented`/`deferred`/`never` 并列）：4 条，每条有 `why_no_lane` 与 `unblocked_by`。理由写在**读者能看到的地方**，不是测试的注释里。
3. **新增 `cli/tests/test_l1_agent_invocations.py`**（2 条守卫）：
   - `test_every_invocation_resolves_through_the_real_parser`：逐 token 走"动词 → 嵌套子动词 → 选项"，**在 token 真正所属的那一层**校验；另加一条非空断言（遍历次数 ≥ 40），防止 walker 退化成"不跟随子动词"。
   - `test_every_verb_is_either_a_lane_or_explained`：解析器的每个动词**恰好**属于"有 lane"或"有理由"之一；理由必须 ≥40 字符（挡占位符），`unblocked_by` 必须是 `deferred` 里用过的解锁词或 `null`。
4. **`test_l1_agent_read_fields.py`**：setup 里那句 `data-root add` 从 `run(...)` 改成 `record(...)`——新 lane 的 6 个字段因此真的在文档上解析过，§42 的"新条目必须有场景或写好的借口"那条规则**仍然成立**。
5. **计数接回现实**：read 路径 **133 → 139**（§73 立的那条守卫又一次把文档里的数字抓回现实）。

### 79.4 守卫与验红

| 变异 | 新守卫 | 既有 option 守卫 |
|---|---|---|
| lane 里写一个不存在的动词（`rooot`） | ✅ 红 | ⚠️ **绿——缝就在这里** |
| 子动词拼错，且带着会露馅的标志 | ✅ 红 | ✅ 红（**碰巧**） |
| 子动词拼错，标志在父命令上合法 | ✅ 红 | ⚠️ **绿** |
| 一个动词丢掉它的理由 | ✅ 红 | — |
| 给已经**有 lane** 的动词留理由（`where`） | ✅ 红 | — |
| 理由是占位符（`later`） | ✅ 红 | — |

前两行右边那两句探测是这一轮最重要的证据：它把"缝在哪"从猜测变成了**实测的两绿一红**。

### 79.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **815 → 817**（新文件 2 条） |
| 审计检查（`test_l0_consistency.py`） | **76 不变** |
| `read` 路径 | **133 → 139**（新 lane 6 条；`AGENTS.md` 那处已同步） |
| lane 数 / 未覆盖动词 | 33 → **34** / — → **4**（各带理由） |
| 场景台账 / schema / fixture | 108 / 19 / 27 **不变** |
| golden 语料 | **未重生**（没动任何输出形状：`data-root add` 的字段是被**读**，不是被改） |

### 79.6 如实记录的边界

1. **walker 不校验位置参数**：`<placeholder>` 一律放过，真实位置参数也不查类型与枚举——那是 argparse 运行时的事，静态守卫不该假装能替代它。
2. **`uncovered_verbs` 的"理由"是散文**：守卫只保证它**存在且不像占位符**（>40 字符），保证不了它**对**。
3. **`extension` "不在问题面上"是我的判断**（`SKILL.md` 的命令地图确实没有它）。如果将来 SKILL.md 教了它，这条理由会变成错的，而**这一半守卫不会红**；那时该做的是把它挪成一条 lane——**那一半是钉住的**（有 lane 却还留在表里会红）。
4. **没有去改 §42 那条 option 守卫的判据**。它那个早返回是**有意的**（"不认识的动词让给别的守卫"）——问题在于那个"别的守卫"当时不存在。这一轮补的是**缺的那条**，不是改它的判据；两条守卫的职责因此是分开的，写在这里，免得下一个人把它们合并成一条更聪明的、更不透明的检查。
5. **这一轮没有新增或删除任何 CLI 动词与选项**，也没有改任何输出形状。补的是**元数据**（一条 lane + 一张理由表）与**守卫**。
6. **"没 lane"不等于"agent 做不到"**：`data-root add` 在补 lane 之前就能跑，`AGENTS.md` 也写着它。缺的是**机器可读的那一半**——而恰恰是 agent 实际照做的那一半。

### 79.7 实施顺序

1. 先把两条既有守卫的判据读清楚（§42 的 read 解析、审计里的 option 解析），找出它们**都不管**的输入：这个文件里的**动词**与**嵌套子动词**；
2. 量：33 条调用逐 token 走一遍真实解析器（发现三），同时把"哪些动词没有 lane"数出来（发现四）；
3. **先跑探测式变异**（未知动词、两种子动词拼错），把旧守卫的缝测出来——不猜，直接测（发现一/二）；
4. 补文档面：一条 lane（`data-root`）+ 一张 `uncovered_verbs` 理由表；写 2 条守卫；把 §42 那条场景从 `run` 改成 `record`；
5. 6 个变异逐个验红；read 路径计数接回现实（133 → 139）；跑全量 + 旧切片 + 两种验收模式；确认 golden 语料未动；提交。

## 80. 第 80 阶段：契约目录自己写的规则，只有"19 个"这一条被机器守着

### 80.1 这一阶段要解决什么

`docs/schema/README.md` 是**契约自己的目录**：19 行边界表（一行一个 schema，写清它守哪个边界）+ 五条兼容规则。而它唯一的守卫是一个**计数**（"19 个 schema"，§第四章的计数守卫）。也就是说：`AGENTS.md` §7 要求"改 Schema：跑 `validate-schemas` + 同步 `docs/schema/README.md` 的边界表与兼容规则"——**这句话在任何一条守卫里都没有对应物**。

这一轮问的就是它：**目录里写的规则，有几条被机器守着？**

### 80.2 实测

**发现一：19 个 schema 与 19 行边界表今天完全对上**，没有残留行、没有未登记的文件。所以这一轮（和前几轮一样）是**在漂移之前**装门。

**发现二：规则 1（`schema_version: 1` 是唯一主版本）在 18 个文档里以两种形状成立。**

| 形状 | 数量 | 例子 |
|---|---|---|
| 文档版本：`{"$ref": "common.schema.json#/$defs/schemaVersion"}` | 17 | `plan`、`where-response`、`transaction`… |
| **信封版本**：`protocol_version: {"type": "integer", "const": 1}` | 1 | `broker-request`（与 `docs/broker/` 方案 §3 的信封示例一致） |
| 都不带 | 1 | `common.schema.json` —— 它是**定义**这两个东西的文件 |

结论：**"有没有版本钉子"是可查的，"用哪个名字"是设计选择**。在此之前没有任何检查——一个不带版本的 schema 可以直接合进来。

**发现三：规则 3（每个安全边界都拒绝未知属性）在 69 个对象节点上成立，而"成立"有三种形状。**

- **记录型**对象：`additionalProperties: false`（绝大多数）；
- **映射型**对象：`additionalProperties` 是一个 **schema**（键本身是数据）——只有两个：`extension-manifest` 的 `operations`/`operation_schemas`、`reference-plan` 的 `exposure.variables`；
- **条件分支**：`allOf` 里的 `if`/`then`（十来个）**没有** `additionalProperties`——它们**约束**一个已存在的对象，不描述一个对象，在那里写它没有意义。

这条界线不写下来，一个"要求所有对象都写 `additionalProperties`"的守卫会把人逼去改 JSON Schema 的条件分支。**它是判断，写进了守卫的常量里，并由一个变异钉住**（见 80.4 最后一个）。

**发现四（守卫第一次跑就抓到的）：边界表里 `search-request` 那一行只写了 "File search input"。** 17 个字符，全表最短（其余 ≥27）。它没回答"这份 schema 守的是哪个边界"，而 `search-request` 恰恰有真实语义（match/target/consistency 与实现上限，§74 已逐值解释）。已补写成："File search input: what to match, how to match it, and which consistency the caller will accept"。

### 80.3 做了什么

新增 `cli/tests/test_l1_schema_catalog.py`（4 条守卫）：

| 守卫 | 盯什么 |
|---|---|
| `test_the_catalog_table_and_the_schema_files_are_the_same_set` | 边界表 ⟷ schema 文件**双向**相等；每行 ≥20 字符（"说了等于没说"会红）；**没有两行共用一句描述**（复制粘贴占位会红） |
| `test_the_catalog_only_names_schemas_that_exist` | README 里出现的每个 `xxx.schema.json` 名字都必须真实存在（改名不会留下僵尸引用） |
| `test_every_schema_pins_the_version_its_documents_carry` | 每个 schema 必须钉住版本，且**钉死在 1**；两种形状；例外集合**恰好等于** `VERSION_BY_PROTOCOL`（`broker-request` 一条，带理由）；`common.$defs.schemaVersion` 本身必须是 `const 1` |
| `test_every_record_object_rejects_unknown_properties` | 走遍所有"有 `properties` 且不是条件分支"的节点（**≥50 个**的非空断言），每个都必须声明 `additionalProperties` |

**没有改任何 schema 文件**：这一轮量出来的结论是"规则都被遵守"，所以产出是**守卫**，不是修契约；唯一被改的是**目录里那一行薄描述**。

### 80.4 守卫与验红（7 个变异）

| 变异 | 预期 | 结果 |
|---|---|---|
| 边界表删掉一行（`gc-plan`） | 红 | ✅ 红 |
| 加一行给不存在的 schema | 红 | ✅ 红 |
| 某行变成空话（"plan"） | 红 | ✅ 红 |
| 某个文档 schema 丢掉版本钉子（`where-response`） | 红 | ✅ 红 |
| **IPC 信封的版本不再是 `const 1`** | 红 | ⚠️ 第一次**绿** → 收紧后 ✅ 红 |
| 某个记录对象开始接受未知属性（`plan.target`） | 红 | ✅ 红 |
| **把条件分支的例外集合清空** | 红 | ✅ 红 |

第 5 个是这一轮第二次"变异抓出更弱的检查"：我的第一版只断言 `protocol_version` **存在**，于是 `{"type": "integer"}`（不钉任何值）照样通过——**"存在"不等于"钉住"**。收紧成 `const == 1` 之后再测才红。第 7 个则证明那条例外**是必要的**：清空它，十来个 `if`/`then` 分支立刻报错。

### 80.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **817 → 821**（新文件 4 条） |
| 审计检查（`test_l0_consistency.py`） | **76 不变** |
| schema | **19 不变**（这一轮**没有改任何 schema 文件**） |
| 边界表 | 19 行不变；`search-request` 的描述被补写 |
| 场景台账 / fixture / golden | 108 / 27 / **未重生** |

### 80.6 如实记录的边界

1. **规则 2（"加可选属性是 minor 兼容；改类型/枚举/必填/digest 算法/状态含义需要新 schema id"）是流程规则，不是文件属性。** 它只能靠 diff 审查，静态守卫做不到——写在这里，而不是假装覆盖了。
2. **规则 3 的判据是"声明了 `additionalProperties`"，不是"拒绝得对"**：一个对象把它写成 `{"type": "string"}` 会被放过（那是映射形状）。要区分"有意的映射"与"漏写 false"，只能人读——两个映射型对象写在守卫的 docstring 里。
3. **规则 1 只管版本钉子的形状**，不管"这个 schema 该不该带版本"：`common` 的例外是**按文件名硬编码**的（`FRAGMENT_FILE`）。将来若再加一个片段文件，必须先改那条常量——这是有意的手续，守卫会红。
4. **规则 4/5**（`plan_hash` 的计算方式、`test_hmac_sha256` 只允许出现在假切片里）**早就有守卫**，这一轮没有重复它们。
5. **"两行不许共用一句描述"只防完全相同的复制**：改一个词就绕过。它挡的是最廉价的那种占位，不是所有偷懒。
6. **这一轮没有让 `docs/schema/README.md` 变得"机器可生成"**：边界描述是**人写的判断**（"这份 schema 守哪个边界"），机器生成不了。守卫只能保证它**存在、不重复、指向真实文件**。

### 80.7 实施顺序

1. 先读契约目录自己写了什么（19 行表 + 5 条规则），再逐条问"这条有没有守卫"——答案是：只有计数有；
2. 量规则 1 与规则 3：两种版本形状、69 个对象节点、两个映射形状、条件分支的例外；
3. 写 4 条守卫；**第一次跑就被自己的阈值抓到一个薄行**（`search-request`），补写它；
4. 7 个变异逐个验红；其中"IPC 信封的版本"第一次是绿的（"存在" vs "钉住"），收紧后重跑；
5. 回写计数与 `AGENTS.md` 的 schema README 行；跑全量 + 旧切片 + 两种验收模式；确认 schema 与 golden 语料都没动；提交。

## 81. 第 81 阶段：入口文档教的东西，与元数据知道的东西，差四个动词

### 81.1 这一阶段要解决什么

§79 让**机器可读的调用元数据**（`agents/airoot.json`）与 CLI 对上了账，并在结尾留了一句**判断**："`extension` 不在 agent 的问题面上"。这一轮问它的可检查形式：**入口文档（`SKILL.md`）与元数据说的是同一件事吗？**

`SKILL.md` 是 agent 学会"跑什么"的地方。它有两处在**指示**：**命令地图表**（"用户说 → 跑什么 → 不要做什么"）与**《第一步永远是确认状态》**那一段。§80 刚做完"契约目录自己的规则"，这一轮做"入口文档自己的指示"。

### 81.2 实测

**先把"指示"与"提及"分开**：`taught_verbs()` = 命令地图的行 + 第一步那一段里出现的 `airoot <verb>`；散文**不算**。实测这个定义在今天的文件上给出干净结果：

| | 数量 |
|---|---|
| 解析器动词 | 24 |
| 有 lane 的动词 | 20 |
| **地图教的动词** | **16** |
| 有 lane 但地图没教 | **4**：`data-root`、`forget`、`scope`、`uninstall` |

校验这个定义的两条：**教的 16 个里没有一个没有 lane**，也**没有一个不是真动词**；而在散文里另外出现的三个（`approve`、`bootstrap`、`reconcile`）恰好都出现在"它**不**做什么 / 还没实现"的句子里。**这就是"提到"与"教"的区别**——如果把散文也算作指示，这条守卫会逼着给一个被挡住的动词配 lane。

**四个差距里有两个是真缺口：**

- **`data-root add`**：地图教了 `discover` → `adopt`（还写着"不 `adopt` 数据根之外的路径"），却**从没说过先得有一个数据根**。§79 刚给它的 lane，在入口文档里仍然是空白——**同一个洞，高一层**。
- **`forget <external-id>`**：管家模型的头条硬规则是"**reference 永不 `uninstall`，只有 `forget`**"（`AGENTS.md` §1 规则 2），地图教了它的反面（`adopt`），没教它本身。

**另外两个是有意的省略，但一个字都没写下来**：

- `scope decide`：地图的 `plan` 行已经同时完成路由与计划（`--scope`/`--target`/`--dry-run`），单独教它等于把同一个决定讲两遍；
- `uninstall`：它是 `tool retire` + `tool gc` 的**合成**动词（CLI 的 help 原话是 `retire then collect an owned payload (needs approval)`），地图故意教那两个**分级**步骤——退役只清绑定，回收才删，门在 `gc --apply` 上。

### 81.3 做了什么

1. **`SKILL.md` 地图补两行**：
   - `data-root add <path> --id <id> --role <runtime|tool|mixed>`，"**注册不写任何文件**（`files_touched` 是 0）；这是管家域的第一步：没有数据根，`discover`/`adopt` 无事可做"；
   - `forget <external-id>`，"**永不删文件**，只丢记录；reference 没有 `uninstall`（`unadopt` 是它的兼容别名）"。
2. **`agents/airoot.json` 新增 `unmapped_verbs`**：`scope` 与 `uninstall` 各带 `why_not_mapped`（>40 字符）与 `unblocked_by`（都是 `null`）——把"有 lane 但地图不教"变成**读者能看到的决定**，与 §79 的 `uncovered_verbs` 同一个套路。
3. **`cli/tests/test_l1_agent_invocations.py` 加 2 条守卫**：
   - `test_every_lane_verb_is_taught_or_recorded_as_unmapped`：`有 lane − 地图教` **恰好等于** `unmapped_verbs`（双向），理由非占位符；
   - `test_nothing_is_taught_that_the_metadata_cannot_account_for`：地图教的每个动词都必须是真动词、有 lane（或在 `uncovered_verbs` 里），**并且不能**是元数据说"做不完"的那些（`unblocked_by` 非 `null`）——**这一条就是 §79 那句判断的可检查形式**。
   同时把 `taught_verbs()` 的定义与"为什么排除散文"写进 docstring。

### 81.4 守卫与验红（5 个变异）

| 变异 | 预期 | 结果 |
|---|---|---|
| 地图不再教 `data-root add` | 红 | ✅ 红 |
| 地图教 `install`（元数据说它被挡住） | 红 | ✅ 红 |
| 地图教一个 CLI 没有的动词（`teleport`） | 红 | ✅ 红 |
| 有 lane 的动词丢掉"没教"的理由（`scope`） | 红 | ✅ 红 |
| 已教的动词被记成 unmapped（`where`） | 红 | ✅ 红 |

**外加一次自证（这一轮最该记的一条）**：第一次跑"删掉那一行"的变异时，脚本按 `\r\n` 切行，而 `SKILL.md` 是 **LF** —— 于是它把整个文件**清空**了，"红"得毫无意义（空文件当然让一堆守卫报错）。改成按文件真实行尾切、并**断言"只少了一行"**之后重跑，才是真的红。这与 §71.5 记的"变异写反了（`if False` 什么都不报）"是同一类坑：**变异本身也要被测量**。

### 81.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **821 → 823**（新增 2 条） |
| 审计检查（`test_l0_consistency.py`） | **76 不变** |
| `read` 路径 | 139 不变（这一轮没给 lane 加读字段） |
| 地图行 / 未映射动词 | 20 → **22** / — → **2**（各带理由） |
| schema / fixture / 台账 / golden | 19 / 27 / 108 / **未重生** |

### 81.6 如实记录的边界

1. **`taught_verbs()` 是一个定义，不是真理。** 它取"命令地图的行 + 第一步那一段"。`SKILL.md` 若在别处（比如《批准》一节）写"你该跑 X"，那条**不会**被算作指令——这是**有意的**（散文里的提及多半是"它不做什么"），代价是：**一句真的指令写在散文里就会被漏掉，而守卫不会红**。
2. **`unblocked_by` 被借用了一次**：在 `uncovered_verbs` 里它表示"没有外部解锁条件"；这一轮用它判断"元数据说这个动词做不完"。`approve`/`install` 带 `decision`，所以它们**不许被当作指令教**——但它们**仍然可以**在散文里被点名（《批准》一节正是这么写的），这条守卫允许。**§82 已改判**：那两个字现在是 `p2-protected-state`（`decision` 随 `needs-decision` 一起被删，ADR-0025 的 D1/D2）；**守卫的结论一个字没变**——`unblocked_by` 非 `null` 就不许被当作指令教——变的只是它等的东西的名字。
3. **"教"与"有 lane"仍是两个集合，不是同一个**：地图可以教一个没有 lane 的动词（今天没有），lane 也可以存在而地图不教（今天两个，都写了理由）。守卫保证的是**差集恰好等于那张表**，不是"两者应当相同"。
4. **`extension` 那条判断这次**被**守卫覆盖了一半**：它在 `uncovered_verbs` 里带 `unblocked_by: null`，所以"不许被当作指令教"已经由新守卫保证；但"它不在 agent 的问题面上"这句话本身仍是散文。
5. **这一轮没有动 CLI**：改的是入口文档与元数据；`data-root add`/`forget` 的字段是被**读**，不是被改。

### 81.7 实施顺序

1. 先把"指示"与"提及"分开（命令地图 + 第一步 vs 散文），并**验证这个定义在今天的文件上给出干净结果**（16 个，无一例外）；
2. 量差距（4 个有 lane 却不教），把它们分成**真缺口**与**有意省略**；
3. 补地图两行；把两个有意省略写成 `unmapped_verbs`（读者能看到的地方，不是测试注释）；
4. 写 2 条守卫；5 个变异逐个验红——**第一版脚本因为行尾假设把文件清空了，重写并断言"只少一行"之后才是真红**；
5. 回写计数与 `AGENTS.md` 行；跑全量 + 旧切片 + 两种验收模式；确认 schema 与 golden 语料都没动；提交。


## 82. 第 82 阶段：把"没做"写成"决定不做"——七条教义与 D1–D9

### 82.1 这一阶段要解决什么

用户的原话是"我想以安全稳定为主，请你推荐一套决策"。这不是一个窄问题，而是**一次收口**：P1 已经交付完所有不需要提权的部分，剩下的是**一长串"没做"**——`AGENTS.md` §8 与决策日志的《尚未决策》里各有一份清单，写的是"P1 没有自行发明 X"。§67 已经证明这种写法有一个具体缺陷：**"还没想过"与"想过并决定不做"在文档里长得一模一样**。读者读不出区别，于是两种结局：

- 把**遗忘**读成**选择**（"作者显然权衡过了"）——然后照着那条不存在的决策往下做；
- 把**选择**读成**遗忘**（"迟早会有人补上"）——于是它永远不会被补上，而每个人都在等别人。

§67 已经在**一个**问题上做了这件事（`approve` 的签发方）：把"缺一个算法"升级成"三条路、各自代价、一条推荐"。这一阶段把同一件事做**一遍全套**：把 P1 剩下的每一个开口放进**同一套判据**下逐条裁决。

**为什么必须成组裁决，而不是一条一条来**：逐条问会得到互相矛盾的答案。单独看，`approve --interactive`（本地人类通道）比"什么都做不了"好；单独看，给 `machine_id` 加个硬件指纹比"必须显式注入"方便；单独看，写一个 Everything adapter 比"只能 crawl"快。只有把它们放在**同一套判据**下，才能看出它们做的是同一件事——**扩大信任基，或者制造一个看起来安全的假保证**。这个观察本身就是这一阶段的产出：**七条教义**。

### 82.2 实测：九条决定，每条都先问"它改变什么"

先量清"没做"到底有多少条。把三份文档里的开口并起来，得到九组：

| # | 开口 | 它今天挡住的 |
|---|---|---|
| D1 | 生产批准签发方 | `approve` / `install` / `env persist` / `tool gc --apply` / `uninstall` 五条路径在真机上的完成 |
| D2 | `root adopt` 的 copy/verify/switch 规则 | `root adopt`（`deferred`，`needs-decision`） |
| D3 | `.ai/tooling.json` 的写入通道 | `memory` 规则（"记住这次选择"）的落点 |
| D4 | `exposure\bin` launcher | `EXPOSED` 的写一侧 |
| D5 | machine 级环境变量持久化 | `env persist --scope machine`（今天 `PRIVILEGE_REQUIRED`(5)） |
| D6 | Native Index 的进程模型 | USN journal 消费与常驻索引器 |
| D7 | Everything adapter | 一条性能捷径 |
| D8 | 身份生成 / 根定位 / 迁移与裁剪 / 第一个真实 artifact | 四件互不相干但同类的事 |
| D9 | 冻结能力清单本身 | 白名单能引用哪些名字 |

**量出来的第一个事实**：这九组里**只有 D9 是"数据"**，其余八组都是"形状"或"结论"。这不是巧合——它说明这一阶段能做的事有两层：**改数据的只有一条**，**给结论的有八条**。把这一点写下来很重要，否则读者会以为"一次裁决"意味着"一次大改"。

**量出来的第二个事实（也是这一轮唯一一处真改判）**：`media_probe` 在冻结清单里**没有任何消费者**——白名单没有它、来源清单没有它、没有任何 CLI 路径读它。它是 §16 冻结流程早期留下的一个**预留位**。留着它的代价不是"多一行数据"，而是**读者会以为这台机器支持媒体探测**。

### 82.3 做了什么

**一次成组裁决，写进 `docs/AIROOT-v0.3-实现决策记录.md` 的新条目 `## ADR-0025`**（放在 `## 尚未决策` 之前），并把它落实成代码与文档里的具体动作：

1. **七条教义**（不新增"看起来安全"的能力 / 派生状态可重建而权威不自我修改 / 只有一个提交点且先验后切 / 不扩大信任基 / 永不删除、永不自动迁移、永不静默裁剪 / 网络只在已验证边界内且永不是测试依赖 / 契约变更最小化）。它们不是新发明，是把 P1 已经反复用到的判断**写下来**，并把 ADR-0021 的四类豁免**展开成可执行判据**。
2. **D1 选 A（维持现状）**：否决 B（本地人类通道：它把规则读成"不得在没有人在场时签发"，而密钥仍在 root 内，同用户照样能伪造）与 C（现在就做真实 `ed25519`：在没有受保护存储的机器上做，等于把"私钥就放在 root 里"变成事实上的设计，还给它披上"已签名"的外衣）。**执行路径一行没改。**
3. **D2 定下 `root adopt` 的四条规则**：源目录不动 / 先验后切 / 只有一个原子切换点 / adopt 自己不写机器 PATH。于是 `deferred["root adopt"]` 从 `needs-decision` 改判成 `needs-admin`，`unblocked_by` 从 `decision` 变成 `p2-protected-state`。
4. **类别与解锁词各少一个**：`needs-decision` 的唯一成员走了，类别随之删除；`decision` 这个解锁词同样消失（`approve`/`install` 的 `uncovered_verbs` 也改等 `p2-protected-state`）。判据与 §60 删掉 `covered-elsewhere` 时**同一条**：一个没人落在里面的词，是读者会遇到却查不到用处的词。
5. **ADR-0024 的状态从"提案"翻成"已裁决：A 维持现状"**，并保留它下面的背景、挡住表与三条路——它们是那次裁决的**依据**，不是作废的提案。两条拒绝消息里那句指针（`ISSUER_PENDING`）从 "ADR-0024 is the pending decision" 换成 "decided: ADR-0025 keeps it waiting for the P2 broker"。**措辞统一那条守卫一个字没改**，换的只是它指向的东西。
6. **D9 落地**：`policy/capabilities.json` 的 revision `cap-1` → **`cap-2`**（8 → **7** 个能力，移除 `media_probe`），`policy/discovery-whitelist.json` 的 `wl-3` → **`wl-4`**（7 → **6** 条）。两个文件各写了自己的移除理由。
7. **《尚未决策》八项在原编号下逐条标注裁决位置**（D8-1/D8-2/D8-3/D5/D4/D6/D3/D1），性质从"还没想过"变成"决定不做，等某个具体的东西"。**语义一字未改。**
8. **语料重生**：`frozen_capabilities.json`（`cap-2` / 7 个能力）、`execution_bounds.json`（`capabilities: cap-2`、`discovery_whitelist: wl-4`）、`discover_report.json` 与 `registry_with_data_root.json`（`wl-4`）。**其余 23 个 fixture 逐字节不变。**
9. **三份随包数据里的"当时事实"加了 §82 标注**（§12 的 `cap-1`/8、§43 的 `wl-3`/7、§47 的 `cap-1`/8、§49 的两处 revision 列表、§60 的三个类别词与三个解锁词、§81 的 `decision`）：**原数字一个都不改**，只在旁边写清今天读作什么。理由是 §48 已经定下的口径——那些小节自称描述**那一轮**的状态，改数字等于伪造历史。

### 82.4 守卫与验红

新增**三个**常驻检查（`cli/tests/test_l0_consistency.py`，守卫第二十四组），因为这一轮有两种不同的失败方向：

**(a) 被裁决的事情不许继续被广告成"待裁决"。** `test_the_settled_decision_says_so_and_names_every_decision_it_took` 检查：ADR-0024 的**标题行**必须写着"已裁决"且不含"待裁决"；`## ADR-0025` 必须存在且自己说"已裁决"；**D1–D9 每一个都要有自己的 `### D<n>` 小节**——不是数够九个，而是"指向 D6"必须**有地方可落**。

**(b) 指向**已被取代的提案**的指针是死胡同。** `test_the_agent_facing_surfaces_point_at_the_decision_that_was_taken` 逐**行**扫 `agents/airoot.json`、`SKILL.md` 与 `references/*.md`：一行里出现 `ADR-0024` 却不出现 `ADR-0025`，就是红的。按**行**而不是按文件，是因为失败的粒度就是一行——`references/field-values.md` 有三行写着"ADR-0024 是待裁决项"而同一文件其余部分是对的；**按文件检查会放过它**。

**(c) 登记表里的"为什么等"必须指向**做出这个改动**的那条 ADR。** `test_the_deferral_register_points_at_the_decision_that_moved_it` 钉住 §82 的两个具体动作：`deferred["root adopt"].why` 必须点名 ADR-0025 且 `unblocked_by` 必须是 `p2-protected-state`；`approve`/`install` 的 `why_no_lane` 必须点名 ADR-0025。

**验红（这一轮的教训比数字值钱）**：

| 变异 | 预期 | 结果 |
|---|---|---|
| ADR-0024 的标题换回"提案，待裁决" | 红 | ✅ 红（**第一版是绿的**，见下） |
| 删掉指针的目标（`## ADR-0025` 整节） | 红 | ✅ 红 |
| D1–D9 逐个改掉小节号（9 个变异） | 红 | ✅ 红（**第一版对 `D1x` 是绿的**，见下） |
| 某一行只写 `ADR-0024` 不写 `ADR-0025` | 红 | ✅ 红（**并且第一次运行就抓到 5 处真问题**） |
| `root adopt` 的 `why` 换回"ADR-0024 是待裁决项" | 红 | ✅ 红 |
| 文档里的审计检查计数不同步（76 ≠ 79） | 红 | ✅ 红（**设计如此**） |

**三处"守卫自己写错了"，都记下来**：

1. **第一个版本的 `_settled_decision_problems` 用 `str.split("## ADR-0025")` 定位目标小节——而 ADR-0024 的指针句里正好写了 `## ADR-0025`。** 于是它落在**被指的那一条 ADR 内部**，十条断言全部报在错误的小节上（"ADR-0025 没有 D1 小节"之类）。**修法**：改成**行锚定**的 `re.search(r"(?m)^## ADR-0025")`，并且**把指针句里的反引号标题去掉**（现在写"裁决见 **ADR-0025** 的 D1"）。这一条值得单记：**一个指向标题的指针本身会破坏按标题切分的解析**。
2. **D 小节的边界第一次写成 `(?![0-9])`，而 `### D1x` 照样通过**（"下一个字符不是数字"对 `x` 成立）。改 `(?!\w)` 之后才真的红。同一个模式还顺带说明 §76 那条 AST 规则的必要性：`\b` 被禁是因为它是**单边**的，而把边界**显式写出来**之后，"D10 会不会被 D1 匹配"这个问题才有个能被读到的答案。
3. **登记表那条守卫的第一版带一张硬编码的豁免名单**（"这四条等 P2 的理由与 ADR-0025 无关，跳过"）——那是一张**会随数据漂移的名单**。换成"只写 `ADR-0024` 不写 `ADR-0025` 的行就是红的"之后，豁免名单消失，判据变成一句可读的规则。**这正是 §60 删 `covered-elsewhere` 时学到的那件事的另一面**：加一个"例外"很容易，而例外正是守卫开始说谎的地方。

**这条守卫第一次运行就抓到一处真问题**：`agents/airoot.json` 的 `unmapped_verbs.uninstall.why_not_mapped` 末尾写着"(ADR-0024)"，而它解释的是"这个合成动词需要一枚这个 build 签不出的 token"——**指向的却是那条已经不再是答案的 ADR**。

**把范围从登记表扩到全部 agent 面之后，另有四处会红，但它们是事后核对出来的，不是守卫当场抓到的**：

| 位置 | 那一行说的是什么 |
|---|---|
| `references/field-values.md` 的 `approval_mode` 行 | "**这一版没有生产签发方**（ADR-0024 是待裁决项）" |
| `references/field-values.md` 的 `source.kind` 行 | "`remote_signed` 要等 **ADR-0024 的裁决**" |
| `references/field-values.md` 的 `$defs.source.signature.algorithm` 行 | "`ed25519` 这一版会显式报 `PROVENANCE_FAILED`(7)（ADR-0024）" |
| `AGENTS.md` §6 命令块 | "裁决见 §8 与 `docs/AIROOT-v0.3-实现决策记录.md` 的 ADR-0024" |

**这个区别值得写下来**：那四处是**我在写这条守卫之前就已经手工修掉的**——本轮前几步做过一次全仓 `ADR-0024` → `ADR-0025` 的指针替换，`field-values.md` 与 `AGENTS.md` §6 这几处当时漏掉了，是我读文件时补上的。说成"守卫抓到五处"会是一件好听但不真的话。
核对方法是把 `git show HEAD:` 的文本喂给这条守卫的判据逐行走一遍：在守卫覆盖的四个面（`agents/airoot.json`、`SKILL.md`、`references/*.md`）上，**HEAD 一共 15 行只写 `ADR-0024` 不写 `ADR-0025`**，其中 11 行（登记表的 5 行 + `SKILL.md` 2 行 + `reason-codes.md` 2 行 + `confirmation.md` 2 行）已被那次替换修掉，剩下 4 行就是 `field-values.md` 的三行加 `uninstall` 那一行；`AGENTS.md` §6 那一行**不在这条守卫的覆盖范围里**（它扫的是 agent 面，入口文档的命令块由另一组守卫管），是读文件时发现的。

**五处都是 §67–§68 那一轮写下的**，而当时它们全部是对的——ADR-0024 确实是待裁决项。**它们不是被写错，是被"时间"写错**：这正是这一类守卫存在的理由。

**外加一次完整的外部校验**：

- `python -m pytest cli/tests -q` → **826 项全绿**；
- 旧协议切片 `validate-schemas`（`schema_count=19`）与 `test` 均 `passed`，`path_unchanged=true`；
- `cli/tests/real_machine_acceptance.py`（不加 `--online`）→ **PASS，0 failed**，真机上 `search refresh` 仍建 **51 073** 条记录、`search status --probe-native-index` 仍报出 `elevated=True` 与真实 journal 读数。

### 82.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **823 → 826**（新增 3 条） |
| 审计检查（`test_l0_consistency.py`） | **76 → 79** |
| `read` 路径 | 139 不变（这一轮没给 lane 加读字段） |
| 冻结能力 / 白名单 | **8 → 7**（`cap-2`）/ **7 → 6**（`wl-4`） |
| `deferred` 类别 / 解锁词 | 3 → **2** / 3 → **2** |
| schema / fixture / 台账 | 19 / 27 / 108 **均不变**（其中 4 个 fixture 重生） |
| CLI 执行路径 | **一行未改**（D1–D8 都是裁决，不是实现） |

### 82.6 如实记录的边界

1. **D1 与 D2 今天解锁不了任何可执行的步骤。** 五条消费批准的路径全部停在 `PROVENANCE_FAILED`(7)；`root adopt` 只是把等的东西从"一个决定"换成"P2"。**"被裁决过"不等于"被实现过"**，这一阶段的产物是**可读性**，不是能力。
2. **`P-012` / `P-015` / `P-017` / `C-022` 保持 `undesigned`。** 选了 A 意味着这四条等的东西一条都没到。**"讨论过"不能改台账**——改判它们才是把一次讨论冒充成一次实现。
3. **七条教义是**判据**，不是**证明**。** 它们把"下次遇到同类问题怎么想"写下来了，但没有任何守卫能检查"这条新决定是否真的遵守了教义"——那仍然要人读。
4. **D6/D7/D4 只写了形状，没有写实现。** 形状定下来降低了 P2 的裁决成本，但它**不构成对"P2 会这么做"的保证**：真到了 P2，形状本身也要被重新检查一次（比如 broker 的 IPC 形态可能让"broker 做初始枚举"这句话需要更精确的措辞）。
5. **`D8-4` 的"第一个真实 artifact 是 `build`"是一个**排序决定**，不是"`build` 更重要"。** 判据只是"它是冻结清单里唯一有已注册可信来源的能力"。如果明天 `archive` 拿到了可取的上游校验和文件，这个顺序就应当被重新问一次。
6. **这一轮没有给 `machine_id` / `session_id` / `project_id` 的生成算法补任何东西**，`agent` 面仍然只接受注入或显式入参。D8-1 只是**把"不做"写成决定**。
7. **`media_probe` 的移除是级联的，而级联的每一环都是手写的**：清单文件、白名单文件、语料、`AGENTS.md` 的两处计数。守卫能抓住**计数**不一致，抓不住"有人只改了清单忘了白名单"——那一环靠的是加载期的交叉校验（白名单条目的 `capability_id` 必须在冻结清单里），**这是有意的分工**。

### 82.7 实施顺序

1. 把九组开口从三份文档里量出来（§8 的清单、决策日志的《尚未决策》、《deferred》/`uncovered_verbs`），先确认它们**没有互相重叠**；
2. 写七条教义，**然后**用它逐条裁决——顺序不能反：先有判据，答案才不是"每条的方便读法"；
3. 先落 **D9**（唯一改数据的），因为它会牵动语料与三处计数；再落 D1/D2/D3–D8 这些"只是结论"的；
4. ADR-0024 翻状态、写 ADR-0025、`ISSUER_PENDING` 换指针、`needs-decision`/`decision` 退场；
5. 写 3 条守卫；**登记表那条第一次运行就抓到 1 处真问题**（`uninstall` 的 `why_not_mapped`），把范围扩到全部 agent 面后另用 `git show HEAD:` 核对出 4 处（3 行 `field-values.md` + `AGENTS.md` 一行），如实记为"事后核对"而不是"守卫抓到"；
6. 逐个验红；其中**两个"绿得没意义"的变异**（指针句破坏了解析、`(?![0-9])` 放过了 `D1x`）各自修掉一处**守卫自己的**缺陷；
7. 重生语料、回写全部计数、给三份随包数据加 §82 标注；
8. 跑全量 + 旧切片 + 真机验收；确认 CLI 执行路径一行未改；提交。
## 83. 第 83 阶段：同一个数字写了两遍——一遍有锚，一遍没有

### 83.1 这一阶段要解决什么

每个阶段记录都用一行交代"这个阶段把测试从多少条变成多少条"。§54 立的那条守卫（`test_the_documented_test_count_is_the_same_everywhere`）只做一件事：把**当前**总数和这次会话真实收集到的数量对上。它对**历史**一句话都没说。

于是同一行里的两个数字，地位完全不同：

- **"到"是有锚的**——它是那次提交之后仓库的真实数量，而下一个阶段的"从"、以及最终的总数，都会从不同方向压住它；
- **"从"是没锚的**——它只是作者对上一阶段结束时的记忆。没有任何东西比对它，除了**上一个阶段的"到"**，而那条比对当时不存在。

**同一件事被写了两遍（上一阶段的"到"＝本阶段的"从"），而只有一遍被检查过。** 这是本项目反复遇到的那一类缺陷，只不过这次它落在**文档的时间轴**上：§82 刚处理完"指向一个已经结束的状态"的**指针**，这一阶段处理同一条时间轴上的**数字**。

### 83.2 实测：四个断口，三个是错的

把草案里 §17–§82 的阶段小段按顺序走一遍，从每一段里取出它对测试数量的交代（`| 测试 | **A → B** |`、`测试 **A → B**`、`` `pytest cli/tests` **N 项…** ``），得到一条链。链上有**四个**"本阶段的『从』≠ 上一阶段的『到』"：

| 断口 | 上一阶段止于 | 本阶段起于 | 裁决 |
|---|---|---|---|
| §50 → §51 | 737 | 739 | **合法**：中间有一次**没有阶段小节**的提交（见 83.4） |
| §59 → §60 | 756 | 758 | **写错**：§59 止于 756，§60 的提交把它带到 759 |
| §67 → §68 | 780 | 781 | **写错**：§67 止于 780，§68 的提交把它带到 782 |
| §75 → §76 | 807 | 808 | **写错**：§75 止于 807，§76 的提交把它带到 809 |

**裁决的办法不是读文档，是问仓库。** 每个阶段小节都是在**那一次提交**里加进去的（`git show --unified=0 <commit> -- <草案> | grep '^+## '` 给出提交 → 小节的精确映射），所以在那个提交上加一个 worktree、跑一次 `pytest --collect-only`，就能拿到那个阶段**真实的**数量。实测：

| 提交 | 阶段 | 真实收集数 |
|---|---|---|
| `79e8147` | §1–§50 | 737 |
| `baa02ca` | **（没有阶段小节）** | 739 |
| `ced8e6c` | §51 | 742 |
| `4a75590` | §54 | 747 |
| `862dc26` | §60 | 759 |
| `7c437e4` / `37411f0` / `4add5c2` | §66 / §67 / §68 | 776 / 780 / **782** |
| `868da5d` / `fe066e5` / `085bdb0` | §74 / §75 / §76 | 803 / 807 / **809** |

三个错的"从"于是各差 1 或 2：§60 差 2，§68 与 §76 各差 1。**它们的"到"一个都没错**——这正好说明哪一半有锚。

### 83.3 做了什么

1. **改掉三个错的"从"**：§60 的 `758 → 759` 改成 `756 → 759`；§68 的 `781 → 782` 改成 `780 → 782`；§76 的 `808 → 809` 改成 `807 → 809`。**只动"从"，不动"到"**——"到"是实测过的。
2. **把那次没有阶段小节的提交变成文档里的一行**（83.4 的声明表），并在 §51 的数量行留一句指向它的短句。断口本身是**合法**的，但"合法却说不出原因"与"写错了"在读者眼里没有区别。
3. **加一条常驻守卫**（`test_l0_consistency.py`，守卫第二十五组），把链本身变成可检查的事实：段内 `A ≤ B`、跨段单调不减、"从"必须等于上一段的"到"（**除非**在 83.4 的表里声明过）、最后一段的"到"必须等于这次会话真实收集到的数量。
4. **回写计数**：测试 826 → 827；审计检查 79 → 80。

### 83.4 跨阶段的计数变化（声明表）

**本草案唯一的声明处。** 只有在**提交没有对应的阶段小节**、却改变了测试数量时，链才允许断开；每一处都要在这里写明上下的确切数字与原因。守卫读这张表：表里没有的断口一律是红的，表里的数字与链上实际数字不符也是红的。

| 上一阶段 | 本阶段 | 上一阶段止于 | 本阶段起于 | 为什么 |
|---|---|---|---|---|
| §50 | §51 | 737 | 739 | `baa02ca`（ADR-0021「放宽优先」+ 状态机边集扩到 62 条）加了 **2 条**守卫却**没有新增阶段小节**——它改的是已有小节。§51 的"从"因此从 739 起，这不是笔误。 |

### 83.5 守卫与验红

新增一条常驻检查：`test_the_stage_records_count_the_suite_without_an_unexplained_jump`。判据五条：

1. **段内有序**：`A ≤ B`；
2. **跨段单调**：`B` 不下降（测试只会被加，不会被这次记录改小）；
3. **首尾相接**：`A_N == B_{N−1}`，除非 `(N−1, N)` 在 83.4 的声明表里；
4. **声明必须属实**：表里的两个数字必须**正好等于**链上那一对，否则声明本身是红的——不实声明比不声明更糟；
5. **终点接上现实**：最后一段的 `B` 必须等于这次会话真实收集到的数量（部分运行会 `skip` 并说明，与 §54 那条同一个机制）。

**验红（8 个变异）**：

| 变异 | 预期 | 结果 |
|---|---|---|
| §60 的"从"改回 758 | 红 | ✅ 红 |
| §68 的"从"改回 781 | 红 | ✅ 红 |
| §76 的"从"改回 808 | 红 | ✅ 红 |
| 声明表那一行删掉（断口失去声明） | 红 | ✅ 红 |
| 声明表里的 739 改成 740 | 红 | ✅ 红 |
| 声明表指向一对**不存在**的断口（§50 → §52） | 红 | ✅ 红 |
| 某段写成 `804 → 803`（段内倒挂） | 红 | ✅ 红 |
| 最后一段的"到"改成 826（终点不到 827） | 红 | ✅ 红 |

**八个变异全部写在守卫体内**（而不是跑一次就丢的脚本），所以它们每次跑测试都会被重新验一遍——这是 §76 那条"守卫问的问题必须真的会红"的延续：**一个只在那一轮手工验过的红，下一轮就没有人知道它还在不在。**

**另外两个方向在守卫体外量过一次**（不写进测试，因为它们与上面两条同类，只是更极端）：把最后一段的数量行整句删掉（链的终点退到 §82 的 826，报"the suite has 827"），以及把声明表那一行改成指向一对不存在的阶段（§50 → §53）——后者**同时**报出"断口没有声明"与"声明了一个链上没有的断口"，两个方向一起红。测量脚本用后即删（仓库里不留一次性脚本，这是 §68 起的惯例）。

**解析器本身被三次修正，三次都记下来**（与 §74/§77/§78 同一族：**解析器必须先知道那句话在说什么**）：

1. **第一版**把一段里出现的所有数字取 `min`/`max`，于是把**审计检查**的数量（§48 的 `44 → 48`）当成了测试数量，报出"§48 起于 48"。**修法**：数量必须出现在**引出词**之后——`` `pytest cli/tests` `` 或 `测试` 紧跟 `|` / `**`。
2. **第二版**的窗口太宽（20 个非数字字符），于是 §56 里那句**关于台账的话**（`> 的测试，台账处置删除（evidenced 42 → 43）`）被读成"测试 42 → 750"。**修法**：单独形态必须紧跟 `项`。
3. **第三版**仍然把 §52 里 `**已经证明它的那个测试**里点名 C-021` 读成一个数量（`C-021` 的 `021`）。同样是"单独形态必须紧跟 `项`"这条规则挡住的。

这三条合起来是一句：**"测试"这个词出现在关于台账、关于测试**文件**、关于测试**函数**的句子里，而只有一种是数量。** 一个只找"最像数字的那个"的解析器会在三种里挑错一种，然后拿这个错去指责文档。

### 83.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **826 → 827**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **79 → 80** |
| `read` 路径 | 139 不变 |
| schema / fixture / 台账 / 语料 | 19 / 27 / 108 / **未重生**（这一轮只改文本与测试） |
| 被修正的数字 | **3**（§60 / §68 / §76 的"从"） |
| 被声明的断口 | **1**（§50 → §51） |

### 83.7 如实记录的边界

1. **链的"到"值不是被这一轮验证的**——它是被**回推**的：§82 的"到"等于 826（有 §54 那条守卫与实测撑着），而每个阶段的"到"是否等于那次提交的真实数量，这一轮只抽查了 §50–§82 里能一一对上提交的那几段。**§1–§49 全在同一个初始提交里**，没有逐步的提交可以对照，所以那一段的"到"仍然只是文档自己说的。
2. **守卫看不见 git。** 它只能检查"断口有没有被声明"，**不能**检查声明里那句"因为 `baa02ca`"是不是真的。那句话的验证方式写在 83.2 里（在提交上加 worktree 跑收集），是一次**人工**动作，不是常驻检查。
3. **声明表是一张"例外名单"，而例外名单正是守卫开始说谎的地方**（§82 刚学过这条）。让它比上次好一点的只有两点：表在**被检查的文档里**（不是测试代码里的硬编码集合），而且表里每一行的数字都要与链上实际数字**双向相等**——一个不实的声明比没有声明更红。
4. **单调不减是"这次记录"的规则，不是历史的规则。** 测试数量在真实历史里当然可能下降（删测试）。真发生了，正确的做法是在声明表里写一行并说明——**而不是**让守卫以为它不会发生。
5. **这一轮没有让任何阶段的"到"更可信。** 它让"从"不再自由。

### 83.8 实施顺序

1. 先写一个**只测量、不改文件**的脚本，把 §17–§82 的交代逐段抽出来，打印成一条链——**先看它能不能读懂，再决定要不要信它**；
2. 让它报错：它报出 5 个断口，其中 2 个是它自己的解析错（把台账数字当测试数字、把 `C-021` 当数量），修解析器**三次**，直到断口收敛到 4 个；
3. 对每个断口问"仓库怎么说"：`git show --unified=0` 求提交 → 小节的映射，再在 worktree 里 `pytest --collect-only` 拿真实数量；
4. 裁决：一个合法（写进 83.4 的声明表），三个写错（改"从"）；
5. 写守卫（五条判据）；8 个变异逐个验红，其中"删掉声明行"与"改坏声明里的数字"是两个相反的方向，都要红；
6. 回写计数、跑全量 + 旧切片 + 真机验收、确认 schema 与语料没动；提交。
## 84. 第 84 阶段：语料"逐字节"是一句没有人检查的话

### 84.1 这一阶段要解决什么

`AGENTS.md` §9 写着 golden 语料是**逐字节**验收面，`.gitattributes` 用它来解释自己唯一的那条指令（`* -text`）：树里混着 CRLF 与 LF，`text=auto` 会在提交时重写一半，于是"语料逐字节可复现"对新克隆就不再成立。

**这一段推理链的每一环都只写在散文里。** §83 刚做完"把写了两遍的数字接上锚"，这一轮问一个更基础的问题：**"逐字节"这句话，到底有没有东西在检查？**

### 84.2 实测：三件事，一件比一件基础

**第一件：比较的是解析后的对象，不是字节。** `test_golden_fixtures_reproduce_exactly` 写的是

```python
expected = json.loads(path.read_text(encoding="utf-8"))
assert expected == payload["document"]
```

实测：`index.json` 以 CRLF 存是 **840 字节**，改成 LF 是 **812 字节**，而 `json.loads` 从两边得到**同一个值**。也就是说，**把整个语料改成 LF，这条测试会全绿**——而它自称守的是"逐字节"。

**第二件：提交里的 CRLF 不是决定，是操作系统的副作用。** `golden.py` 原来写的是 `path.write_text(json.dumps(...) + "\n", encoding="utf-8")`。`write_text` 用文本模式打开，**只在 Windows 上**把 `\n` 翻成 `\r\n`。所以：

- 在这台机器上重新生成 → 840 字节，与提交一致（实测 **27/27 字节相同**）；
- 在 Linux/macOS 上重新生成 → 812 字节那一版，与提交**不一致**，而验收面**看不见**。

一句话：那份"逐字节"的语料之所以逐字节，是因为**生成它的机器恰好是 Windows**。

**第三件：行尾在仓库里没有任何东西在管。** 179 个被跟踪文件的普查：

| | 数量 | 是哪些 |
|---|---|---|
| 只有 CRLF | **32** | `AGENTS.md`、`cli/bin/airoot.cmd`、**27 个 golden fixture**、草案、审查报告 |
| 只有 LF | 146 | 其余 |
| **两种都有** | **1** | `docs/AIROOT-v0.3-三大核心契约方案.md`（474 行里**有 3 行**是 CRLF） |
| 带 UTF-8 BOM | **0** | —— |

外加一个**孤例**：`cli/tests/test_l1_searchindex.py` 是 **266 行全 CRLF**，而它的 26 个同类测试模块全是 LF。一个"两种行尾"的文件说明某个工具只写了它的一部分；一个孤例说明某个工具写完就没再管过。两者都不是缺陷本身，但它们证明**没有人看着这件事**。

### 84.3 做了什么

1. **`golden.py` 新增 `render(document) -> bytes`**：把"一份 fixture 的字节"变成**一个定义**，行尾钉死为模块常量 `NEWLINE = "\r\n"`；`write_all` 改用 `write_bytes(render(...))`。于是"重新生成"在任何平台上产出**同一串字节**——把一次操作系统的偶然变成一次决定。
2. **`test_golden.py` 先比字节、再比解析后的文档**。顺序是有意的：字节失败时，第二次比较告诉读者**哪里**漂了，而不是只说"两个 blob 不一样"。
3. **归一化两个不一致的文件**：契约方案里那 3 行 CRLF 改成 LF；`test_l1_searchindex.py` 整个改成 LF（266 行）。
4. **新增常驻守卫（第二十六组）**，五条规则；7 个变异全部写在守卫体内，每次跑测试都重新验一遍。
5. **`.gitattributes` 的散文改判**：它原来那句"那些文件是文本模式写的，所以在 Windows 上是 CRLF"在 §84 之后**变成假的**（现在与平台无关）——这正是 §48 那一类"文档在断言代码已经不做的事"。改写成"生成器把字节钉死"，并补一句"这里只有一条非注释行，由字节守卫钉住"。
6. **回写计数**：测试 827 → **828**；审计检查 80 → **81**。

### 84.4 守卫与验红

`test_the_bytes_on_disk_are_the_ones_the_contracts_claim` 的五条规则：

1. **没有文件以 UTF-8 BOM 开头**（JSON 的严格解析器、`cmd.exe`、Python 都各有各的反应，而这里一个都不需要）；
2. **没有文件同时有两种行尾**（一个文件被写了两遍，从来没有一次是有意的，而它会静静地长）；
3. **`cli/bin/airoot.cmd` 只有 CRLF**（否则 `cmd.exe` 把 `rem` 行切成命令——这条是功能性的，不是风格）；
4. **每个 golden fixture 只有 CRLF，且以恰好一个 CRLF 结尾**（它们就是那份逐字节语料，而生成器现在钉死 CRLF）；
5. **`.gitattributes` 的非注释行恰好是 `* -text` 这一条**（精确相等，不是"包含"：任何新增指令都红，包括把转换重新打开）。

**验红（7 个变异）**：

| 变异 | 预期 | 结果 |
|---|---|---|
| 给一个文件加 UTF-8 BOM | 红 | ✅ 红 |
| 一个文件里混入一行 LF | 红 | ✅ 红 |
| `airoot.cmd` 改成 LF | 红 | ✅ 红 |
| 一个 fixture 改成 LF | 红 | ✅ 红 |
| 一个 fixture 去掉结尾换行 | 红 | ✅ 红 |
| `.gitattributes` 改成 `* text=auto` | 红 | ✅ 红 |
| `.gitattributes` 删掉那条指令 | 红 | ✅ 红 |

**`test_golden.py` 那条守卫另外独立验过一次**（这一轮唯一动了真实文件的验红）：把 `index.json` 写成 LF（840 → 812 字节），`test_golden_fixtures_reproduce_exactly` **当场变红**，随后按原字节恢复并核对长度。§83 学到的是"变异本身也要被测量"，这一条是它的第二次应用：**验红之后要确认文件回到了原来的字节，而不是"看起来回去了"。**

**这一轮一开头就把 §83 的一个变异打歪了，值得单记。** §83 的最后一个变异是"把链尾的『到』改小"，当时写成一个字面量 `| 测试 | **826 → 827**`。§84 一追加阶段小节，这个字面量**仍然存在**——它现在匹配的是 **§83 自己的那一行**，于是变异照旧变红，但**它测的已经不是"链的终点"**，而是"§83 与 §84 之间多出一个断口"。**一个锚在"下一个阶段会复用的值"上的变异，会静静地改指向自己，而且红得毫无破绽。**

修法是让变异**从链里推出来**，而不是照着今天的样子抄一遍：

```python
last_stage, _, last_to, _ = _stage_count_chain(text)[-1]
tail = f"| 测试 | **{last_to - 1} → {last_to}**"
assert text.count(tail) == 1
shortened = text.replace(tail, f"| 测试 | **{last_to - 1} → {last_to - 1}**", 1)
```

这一条与 §82 的"硬编码例外名单"、§83 的"解析器要先知道那句话在说什么"是同一族：**守卫里任何照着当前文本抄下来的锚，都是下一轮会歪掉的东西。**

（顺带：`assert tail in text` 被本模块自己的 AST 检查抓了——`变量 in text` 正是它要挡的形状。**没有给它开豁免**，改成 `text.count(tail) == 1`：既绕开那个形状，又多要了"恰好一次"。）

**同一个洞还有第二处，而且是被 §84 自己踩出来的。** §83 的解析器在"一个小节里出现多对数字"时取**第一对**。§84.4 上面那段解释就在 §84 的小节里**引用**了 `826 → 827`，而 §84 自己的计数表在它下面——于是解析器把 §84 读成"止于 827"，链条当场多出一个断口。

修法不是把那段解释挪走（那等于让文档迁就解析器），而是让"哪一对是本阶段的"变成**可推导**的：**链条单调，所以一个小节里被引用的历史数字一定比它自己的终点小**——取 `to` 最大的那一对。§83 已经写过"解析器必须先知道那句话在说什么"，这是同一句话的第三次：**还要知道同一句话里哪一个数字是主语。**

### 84.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **827 → 828**（新增 1 条；`test_golden.py` 那条是改写不是新增） |
| 审计检查（`test_l0_consistency.py`） | **80 → 81** |
| 行尾：两种都有的文件 | **1 → 0** |
| 行尾：CRLF 文件 | **32 → 31**（少的是被归一化的那个测试模块） |
| BOM | 0 → 0（本来就是 0；这一轮把它变成红的判据） |
| schema / fixture / 台账 | 19 / 27 / 108 **均不变**；**fixture 的字节一个都没变**（`render` 复现的就是原来的 840 字节那一种） |

### 84.6 如实记录的边界

1. **守卫故意不管全仓的 CRLF/LF 分布。** `* -text` 让两种都合法，而今天这份分布（少数文档与语料是 CRLF）是历史而不是承重结构——为它立一条规则就是**风格**规则，而风格规则正是守卫开始要求没人想要的改写的地方。管的是**两种都有的文件**、**BOM**、**两处真正承重的位置**，和**那条让其余全部成立的一行**。
2. **`test_l1_searchindex.py` 的归一化不是任何规则抓到的**：它是普查发现的，手工修掉的。说成"守卫修好了它"会是假的。这与 §82 那次"五处里有四处是事后核对"是同一类诚实。
3. **字节比较只在"生成器写什么"这一侧成立。** 它证的是"提交的东西与生成器今天写出来的东西一致"，**不是**"语料是对的"。后者由 schema 校验、台账、执行边界那些守卫管。
4. **`.gitattributes` 那条规则是文本相等，不是 git 行为。** 它不能证明 `git checkout` 真的不转换；真正的证据是 `* -text` 的语义加上"在两台机器上克隆并逐字节比对"，而那是一次人工动作。**这里只把"有人偷偷把转换打开"变成红。**
5. **这一轮没有把 `AGENTS.md`、草案、审查报告转成 LF。** 它们的 CRLF 没有理由，但转它们是**纯改动**，而草案有 6907 行——一次 6907 行的假 diff 会把真正的改动埋掉。留着，并在这里写清楚为什么留着。

### 84.7 实施顺序

1. 先问"这句话有没有东西在检查"，答案是"没有"，而且比想的更彻底：**连平台都不固定**；
2. 量第一件（改一个 fixture 的行尾，看测试是否变绿）——**先证明洞存在，再动手堵**；
3. 量第三件（全仓行尾普查）：32 / 146 / 1 / 0，四个数各有一个故事；
4. 把字节变成一个定义（`render`），先让生成器在新定义下**复现原来的 27 个文件**（27/27 字节相同）——**换实现而不换字节**；
5. 把测试改成先比字节；独立验红一次（改真实文件 → 红 → 按原字节恢复 → 核对长度）；
6. 归一化两个不一致的文件；写五条规则的守卫与 7 个变异；
7. 改判 `.gitattributes` 里那句已经变假的散文；回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 85. 第 85 阶段：入口文档的仓库地图，一半有人核对，一半只是散文

### 85.1 这一阶段要解决什么

`AGENTS.md` §2 的仓库地图是**读者最先看的那张表**：哪个文件装什么。§54 把它从 65 511 字节压到 35 KB 出头，正是为了让它能被完整载入——被完整载入的东西，读者会**当成事实用**。

§84 刚做完"字节契约没有字节检查"，这一轮问同一个问题的另一面：**这张表里的"事实"，有多少是有人比对的？**

答案是一个劈开的数字：**两个目录有人核对，其余全是散文。**

- `caps/*.py`：`test_every_caps_module_is_listed_in_the_repo_map` 保证每个模块都被点名；
- `policy/*.json`：`test_every_policy_file_is_listed_in_the_repo_map` + `test_every_policy_revision_is_named_in_the_repo_map`；
- `docs/`、`references/`、`cli/extensions/`、`cli/tests/` 的辅助模块：**今天是全的，但纯粹是运气**；
- 表里对每份文档的**描述**（它是什么、多大、里面有什么）：**没有任何东西在管**。

### 85.2 实测：三处描述已经错了

把地图的每一行当作断言逐条去核对，三处站不住：

| 行 | 它说 | 实际 |
|---|---|---|
| 主规划 | 「主规划（**2236 行**）」 | **2249 行**（差 13 行，没有任何东西比过） |
| ADR 日志 | 「**ADR 日志**：语言路线、三处 Schema 缺陷处置、**ADR-0004 管家模型**」 | 日志里有 **25 条** ADR；这一行读起来像内容清单，而它停在 ADR-0004——**最新那条（ADR-0025，P1 最大的裁决）一个字都没提** |
| 契约草案 | 「**提案（未落地，第二版）**」 | 同一份文件的 §1 写着「**逐阶段的实现记录**（`§31`–`§85`…）**全部在 `docs/AIROOT-v0.3-管家模型与数据根契约草案.md` 的对应小节里**」 |

第三处最重：**同一份文档里两句话在描述同一个文件，而且互相矛盾。** 这正是这个模块从头到尾在找的形状（"一份不知道自己输出是什么的文档"），而地图是**没有人读第二遍的那一半**。

第三处还解释了自己是怎么产生的：草案从 §20 起就同时是"提案章节"和"阶段记录"，而地图那一行是 §12 那轮写的、此后再没改过。**一个描述一旦写对过，就会一直看起来是对的。**

**顺带量出第四个缺口**：`cli/tests/` 那行点名了七个辅助模块里的四个——`conftest.py`、`golden.py`、`fake_issuer.py` 三个**不在**（它们各自在 §6/§7 里被提到，但地图里没有）。三个都是读者会去找的：一个负责 `sys.path` 与会话级不污染断言，一个负责重生语料，一个是**唯一的**批准签发方。

### 85.3 做了什么

1. **改掉三处错的描述**：
   - 行数 2236 → **2249**；
   - ADR 那行改成 **`ADR 日志（ADR-0001 … ADR-0025）`**，并明确写一句「**括号里是范围，不是内容清单**——每一条 ADR 自己的标题才是它的权威」——**把"别再手抄一份目录"写进被检查的那行本身**；
   - 草案那行改成「**提案 + 逐阶段实现记录（§1–§30 是契约章节，§31–§85 是每一阶段的实现记录）**」。
2. **补上三个缺失的辅助模块**：`conftest.py` / `golden.py` / `fake_issuer.py`，各带一句"它是干什么的"。
3. **新增常驻守卫（第二十七组）**，四条：
   - **地图的点名完整**：`docs/**/*.md`、`references/*.md`、`cli/extensions/*.json`、`cli/tests/*.py`（非测试）每一个都在**地图小节内**被点名——把 §? 只对两个目录做的事补到其余四个；
   - **行数声明必须等于文件行数**（`（2249 行）` 这类）；
   - **ADR 范围必须写在那一行里**：日志的首尾两条 id 都要出现在地图行中（把"policy revision 没人能读就不是 revision"那条规则搬到 ADR 上）；
   - **地图必须与 §1 的委派句一致**：§1 把 `§A`–`§B` 交给某个文件，那个文件在地图里的那一行就必须写着 `§A` 与 `§B`。**这一条就是那个矛盾的可检查形式。**
4. **回写计数**：测试 828 → **832**；审计检查 81 → **85**。

### 85.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把一个 `docs/*.md` 从地图里删掉 | 红 | ✅ 红 |
| 把一个 `cli/tests/*.py` 辅助模块从地图里删掉 | 红 | ✅ 红 |
| 行数改回 2236 | 红 | ✅ 红 |
| 给非文件路径声明行数（`docs/*.md`） | 红 | ✅ 红 |
| ADR 那行只写到 ADR-0004 | 红 | ✅ 红 |
| 地图里没有 ADR 日志这一行 | 红 | ✅ 红 |
| 草案那行改回「提案（未落地）」 | 红 | ✅ 红 |
| §1 的委派范围改了而地图行没跟 | 红 | ✅ 红 |
| 委派的文件在地图里没有行 | 红 | ✅ 红 |

**解析器踩了两次，都记下来**：

1. **`_repo_map_missing` 第一版拿 `path.name` 去查一张按**相对路径**建的字典**，于是**每一个**文件都被报成"没被点名"——一整页假红。改成"在**地图小节文本**里找文件名"才对：地图里有些文件是**目录行描述里**被点名的（`conftest.py` 在 `cli/tests/` 那行的正文里，三个 `references/` 文件在 `references/` 那行里），要求每个文件一行是**版式规则**，不是文档规则。
2. **`REPO_MAP_HEADING not in text` 被本模块自己的 AST 检查抓了**（§76 那条：拿一个变量去 `in` 一份文档，正是它要挡的形状）。**没有给它开豁免**——改成 `text.count(REPO_MAP_HEADING) == 0`，与 §84 对 `assert tail in text` 用的是同一招。**两轮之内同一条规则用上两次**，说明它不是边角料。

**一个非空性反例自己也写错过**：最初用 `Path("AGENTS.md")` 当"已被点名的文件"，而地图小节里根本没有 `AGENTS.md`（地图列的是 docs 与 cli，不是它自己）——那条"不该报错"的断言当场红。改成 `SKILL.md`（它在地图里确实有一行）。**这正是非空性检查该有的样子：它也接受被证伪。**

**第三次是 §84 修过的东西又露出另一半。** §84 把 §83 那个"改链尾"的变异从字面量改成**从链里推**，但推导写成 `**{last_to - 1} → {last_to}**`——它假定每个阶段加**一条**测试。§85 加的是**四条**（828 → 832），那条断言当场红：`§85's own count row is not in the shape this mutation needs`。修法是把 `from` 也从链里取，而不是自己减一：两个数都来自 `_stage_count_chain`。

这一条把 §84 的教训推到底：**"从链里推"只做了一半等于没做**——只要推导里还剩一个自己算出来的数，它就仍然在假定某种它不该假定的东西。修好之后，那个变异第一次真正测的是"链的终点"。

### 85.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **828 → 832**（新增 4 条） |
| 审计检查（`test_l0_consistency.py`） | **81 → 85** |
| 地图被修的描述 | **3** |
| 地图补上的文件名 | **3**（`conftest.py` / `golden.py` / `fake_issuer.py`） |
| 地图覆盖的目录（有守卫的） | **2 → 6** |
| schema / fixture / 台账 / 语料 | 19 / 27 / 108 / **未重生**（这一轮只改文本与测试） |

### 85.6 如实记录的边界

1. **"被点名"不等于"描述得对"。** 新守卫能保证一个文件**出现**在地图里，以及**行数**这一种描述属实；至于"这份文档是干什么的"那句话对不对，仍然只能人读。四条规则里只有一条半在管语义，其余三条管的是**出现**与**结构性的一致**（范围、行数）。
2. **ADR 那条只在管首尾。** 日志里 14 条 ADR 从未在 `AGENTS.md` 出现（`ADR-0002`、`ADR-0005`、`ADR-0008`…）——那是**有意的**，入口文档不该抄一份完整目录，守卫也因此只钉首尾两条。**中间变更一条 ADR 的标题，这条守卫不会红。**
3. **§1 与地图的一致性只覆盖"阶段范围"这一个事实。** §1 里对草案还有别的说法（比如"第二版"、"§12–§15 为新增"），它们没有对应的检查。
4. **行数声明会变成摩擦**：以后每改一次主规划，地图那一行就要跟着改。留着它是因为**主规划是冻结的**（一年改不了几次），而"2236"这个数字已经证明**没人会主动去改**。如果哪天主规划开始频繁变动，正确的做法是**删掉这个数字**，而不是让它天天红。
5. **这一轮没有把地图自动化。** 它是手写的判断（"这份文件装什么"），机器生成不了；守卫只能保证它**出现、不重复、不指向不存在的文件**。

### 85.7 实施顺序

1. 先把地图的每一行当断言读一遍，再逐条去核对——**三处错、一处缺口**；
2. 量出"哪些目录有人核对、哪些没有"（2 : 4），并确认后者今天**恰好是全的**（那是运气，不是保证）；
3. 修三处描述与三个缺名；
4. 写四条守卫；9 个变异逐个验红——其中**两次是解析器自己的错**（按名字查相对路径的字典、`not in text` 撞上 §76 的 AST 规则）；
5. 回写计数；跑全量 + 旧切片 + 真机验收；确认 schema 与语料没动；提交。
## 86. 第 86 阶段：两份"还没决定"的清单，被当成一份用了很久

### 86.1 这一阶段要解决什么

P1 有**两份**"还没决定"的清单，读者会各按各的找答案：

- **规划 §23**（权威层级第 3 层）："实现前仍需由评审记录以下选择，但不得破坏上述边界"；
- **决策日志的《尚未决策》**："以下 P1 明确没有自行发明算法或语义，需要单独决策"。

§82 给日志那份逐条标了 ADR-0025 的裁决位置，并在标题上写着"**仍属规划 §23 的未冻结项**"——**那句话把两份清单当成了同一份**。§86 先量这件事，再修它。

### 86.2 实测：两份清单从来不是同一份

| | 项数 | 内容 |
|---|---|---|
| 规划 §23 | **6** | capability 清单 / Everything / registry migration 与 event 保留 / human approval 通道 / 第一个真实 artifact 与 runner / Native Search |
| 决策日志 | **8** | 身份生成 / migration 与裁剪 / 根定位 / issuer / launcher / Native Search / tooling.json / machine 级环境变量 |

两边**只有三项重合**（migration、issuer 那一类、Native Search）。更重的是**规划独有的三项——capability 清单、Everything、第一个真实 artifact——在日志那份里一个字都没有**，而它们同样被 ADR-0025 裁决了：

| 规划 §23 的项 | 裁决 |
|---|---|
| v1 capability 清单 | **D9**（`cap-2`，7 个能力） |
| Everything 基准还是 adapter | **D7**（只做基准） |
| 第一个 portable artifact / runner / 夹具 | **D8-4**（`build`；`cli/bin/airoot.cmd`；本地 fixture） |

所以 §82 的复核**只覆盖了八项里的八项**，而"P1 的未决策"整体有**十二项**。一个只读日志的人，永远不会知道能力清单、Everything 和第一个真实 artifact 已经被决定了。**这不是文档过时，是两份索引各自完整地描述了一个更小的集合。**

**顺带量出第二个事实**：ADR-0025 的九条决定里，有一条（**D2**，`root adopt` 的 copy/verify/switch）**在两份清单里都没有入口**——它只出现在 `agents/airoot.json` 的 `deferred` 理由里。一个决定如果从"它回答的那个问题"上找不到，就等于归档进抽屉。

### 86.3 做了什么

1. **规划 §23 的六项逐条标上裁决位置**（D9 / D7 / D8-3 / D1 / D8-4 / D6），并加一段 §86 复核：原文一字未改（那是**当时**的事实），裁决写在括号里。这一节的措辞也把"两份清单从来不是同一份"写在读者会看到的地方，而不是只写在测试注释里。
2. **更正日志的标题**：`仍属规划 §23 的未冻结项` → `本日志自己的一份清单`，并说明这句话**从来不是真的**、代价是什么。
3. **补齐四项**（D2 / D7 / D8-4 / D9），日志那份从 8 项变成 **12 项**。§82 那段复核原话保留（它记的是那一轮），只在开头补一句"（当时是八项；§86 补到十二项）"。
4. **新增常驻守卫（第二十八组）**，两条：
   - **规划 §23 的每一项都必须写出是谁裁决的**——那一节在第 3 层权威上，一项没答案就是一条没有答案的规则；
   - **ADR-0025 的九条决定，每一条都要能从这两份清单之一找到入口**（双向：清单里写了、ADR 里没有的决定也算红）。
5. **回写计数**：测试 832 → **834**；审计检查 85 → **87**。

### 86.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 规划那一节六项全部去掉 D 标记 | 红 | ✅ 红 |
| 其中**一项**去掉 D 标记 | 红 | ✅ 红（只报那一项） |
| 规划里写一个 ADR-0025 没有的 `D99` | 红 | ✅ 红 |
| `### D4` 被改名成 `### D4x`（决定失去小节） | 红 | ✅ 红 |
| 两份清单都不再点名 `D7` | 红 | ✅ 红 |

**解析器与变异各踩一次，都与前两轮同一族**：

1. **`## ADR-0025` 又一次在 ADR-0024 内部先被找到**：ADR-0024 的"明确不做"里仍然用反引号写着 `## ADR-0025`（§82 只改了**指针**那一句）。这是同一块石头第三次绊人（§82 的守卫、§86 的一次性脚本、§86 的守卫），所以守卫用的是**行锚定**的 `(?m)^## ADR-0025`，并把这个理由写进 docstring。**文档里那句反引号本身没有改**——它是历史文本，改它只是为了迁就解析器。
2. **`### D(\d+)` 把 `### D4x` 也读成 `D4`**：那个变异当场**没红**。数字后面必须是"不是词字符"，`(?!\w)` 而不是 `\b`。§83 在 "D1 与 D10" 上遇到过同一件事，这里换了个字母。
3. **§85 的变异是照着当时的文本抄的字面量**（`主规划（2249 行）`），§86 把规划改长了 9 行，那个变异**静静地不再生效**（`stale == text`，断言直接红在"变异没应用"上）。这是同一类第三次：§84 修了链尾变异的字面量、§85 发现"只从链里推一半"、§86 发现**行数**那一个也是抄的。修法照旧——把两个数都从地图行里推出来。

   **写这一节时本来打算下一轮加一条"变异必须自证它应用了"的检查。当场量了一下，结论是这条检查没有东西可抓，所以没有立**——把否定的结论也记下来，免得下一轮有人再想一遍：

   - **全模块还有 5 处变异的第一个参数是从被守卫文档里抄来的字面量**（§60 的 `756 → **759 项**`、§74 的 `**792 → 803**`，以及 §50→§51 断口声明那一行的三份拷贝）。它们**不会**烂：前两处引用的是**历史阶段记录**（那些行本来就不该再动），第三处是三份彼此独立的拷贝，改一处不会让另外两处悄悄失效。
   - **"变异没应用"在本模块里已经是红的，不是静默的。** 量了 5 个构造变异的测试：3 个显式断言 `变量 != 原文`，另 2 个（§82 的 `pending` / `dropped`）即使 replace 空转，也会因为"本该报的错没报"而红。**没有一处是静默通过。**
   - 所以真正值得做的只有一件事，而它已经在做：**锚要从被检查的东西里推出来，而不是照着今天的文本抄**。三次事故都在这一条上，而不是在"有没有断言它应用了"上。

   一次没有产出守卫的测量，比一条抓不到东西的守卫有价值——§43 与 §59 都记过同一件事。

### 86.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **832 → 834**（新增 2 条） |
| 审计检查（`test_l0_consistency.py`） | **85 → 87** |
| 规划 §23 被标的项 | **6** |
| 日志清单 | **8 → 12 项** |
| 规划行数 | 2249 → **2258**（被 §85 那条行数守卫当场抓到） |
| schema / fixture / 台账 / 语料 | 19 / 27 / 108 / **未重生** |

**§85 那条行数守卫第一次真的响了**，而且响在**我自己**的改动上：§86 给规划加了几行，`test_every_file_size_the_repo_map_states_is_the_file_size` 立刻报 "the map says 2249 lines; the file has 2258"。§85.6 把这个数字称作"会变成摩擦"——**它当轮就摩擦了一次**。留着的理由仍然成立（主规划是冻结的，一年改不了几次），但这条证据说明：**摩擦不是假设，是当天发生的事。**

### 86.6 如实记录的边界

1. **守卫只保证"入口存在"，不保证入口内容正确。** 规划那一项写着"（**D9**：…）"，守卫不会去读括号里的解释对不对；它只知道这一项有了归属、那个决定真的存在。**解释仍然要人读。**
2. **两份清单的"重合/独有"是人量的，不是守卫算的。** 守卫检查的是"九条决定有没有入口"，不是"两份清单是不是同一份"。后者做不到：两边的措辞不同、粒度不同（规划一项"registry migration 与 event 保留"对应日志两项），把"同一件事"形式化比这件事本身贵。
3. **§23 冻结清单（9 项）与 `AGENTS.md` §5（11 项）仍然是两份不同的清单**，§86 没有动它们。它们的差异是**层级**差异（规划列方案层契约，入口文档列实现期契约并含后加的三条），不是错误——但**这个判断是人做的**，写在 86 里而不是守在代码里。
4. **D2 只在 `agents/airoot.json` 的 `deferred` 理由里有入口**，现在也进了日志清单。它不在规划 §23 里，因为 §23 那份是 §19 规划阶段写的，而 `root adopt` 的规则问题在 §15.5 与 §8.1.1 里，是一处**更细**的缺口——§86 把它补进日志，没有去改规划 §15.5。
5. **这一轮没有让"未决策清单"变得自动。** 一份"还没决定"的清单在问题解决后必须有人去关它；守卫能在有人**新加**一项时要求归属，不能发现"有一项其实早就解决了而没人来关"——除非那个决定出现在 ADR 里（这正是第二条守卫抓到 D7/D8-4/D9/D2 的方式）。

### 86.7 实施顺序

1. 先把两份清单并排读一遍，量出**项数与交集**（6 / 8 / 3）——**先看它们是不是同一份**；
2. 再问"ADR-0025 的九条决定，哪几条从这两份清单里找不到入口"（D2 / D7 / D8-4 / D9）；
3. 修文档：规划六项标裁决、日志标题改判、补四项；
4. 写两条守卫；5 个变异逐个验红——其中**两次是解析器自己的错**（`## ADR-0025` 的先现、`### D4x` 被读成 D4）；
5. 被 §85 的行数守卫拦下一次（规划变长），顺着它回写地图；
6. 回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 87. 第 87 阶段：验收方案里那张"要覆盖哪些结果"的清单，与语料各自描述了一个更小的集合

### 87.1 这一阶段要解决什么

验收方案（权威层级第 5 层）§8 的 `where` 一节写着"为每种结果固定 JSON fixture"，然后列了**八个名字**。§86 刚做完"两份'还没决定'的清单不是同一份"，这一轮把同一把尺子拿到**测试计划**这一层：**这张清单，和它描述的那份语料，对得上吗？**

### 87.2 实测：两个名字不存在，两个结果没有语料，两个语料没有名字

把 §8 的八个名字逐个拿去问代码与语料：

| 规划要求 | 代码里有吗 | 语料里有吗 | 裁决 |
|---|---|---|---|
| `healthy` / `not_found` / `version_unsatisfied` / `current_process_stale` | 有 | 有 | ✅ |
| `unmanaged_only` | **有**（`caps/where.py` 的 `UNMANAGED_ONLY`） | **没有** | 真缺口 |
| `broken` | **有**（`BROKEN`，退出码 3） | **没有**（语料只有"坏了但健康的 reference 顶上"那种，即 ADR-0006 之后的**降级**，退出码 2） | 真缺口 |
| `session_required` | **没有** | 没有 | 不是 `where` 的结果 |
| `recovery_required` | 有，但**不是 `where` 的**（它是 `doctor` 的 remediation 与事务状态） | 没有 | 不是 `where` 的结果 |
| —— | 有 | `where_owned_broken_degrades_to_reference` | 清单里没有 |
| —— | 有 | `where_deprecated_external_fallback_is_ignored` | 清单里没有 |

**两个方向同时错**：清单里有两个名字这个 build 根本不会产生，而语料里有两个 fixture 清单从没提过。这正是 §86 那个形状——一份索引完整地描述了一个**更小**的集合——只不过这次错的是**验收面自己**。验收面写错比索引写错更贵：它决定"什么算验过了"。

### 87.3 做了什么

1. **补两个 fixture**：`where_broken`（owned 坏了且没有别的候选，退出码 3）与 `where_unmanaged_only`（只看到 unmanaged 候选，退出码 1）。两个都在 `test_golden.py` 的 `SCHEMA_FOR_FIXTURE` 里注册，语料 **27 → 29**。
2. **把 §8 的 `where` 一节改成表**（场景 → fixture → 退出码），并写明**这张表与 `where_*.json` 双向相等**；两个不是 `where` 结果的名字从表里移出去，**原因写在表正下方**（一个不存在，一个属于别处）。
3. **新增守卫（第二十九组）**：表 ⟷ 语料**双向相等**，而且**退出码也要对**——§36 那条"文档里一个错的退出码能活过全绿套件"说的就是这一列。
4. **回写计数**：测试 834 → **835**；审计检查 87 → **88**；fixture 27 → **29**。

### 87.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 表里把一个 fixture 名改成不存在的 | 红 | ✅ 红（"在表里但没有 fixture"） |
| 表里删掉一行 | 红 | ✅ 红（两个方向各报一条） |
| 表里把某一行的退出码改掉 | 红 | ✅ 红（"表说 N，语料索引说 M"） |

**这一轮最该记的一条：我把语料改错了，而发现它的是一条守卫。**

补 `where_broken` 的时候，我把原来自成一体的那段拆成了两半，**顺手把"插入 external reference"那几行删掉了**——于是 `where_owned_broken_degrades_to_reference` 会退化成 `BROKEN`。在跑 `golden.py` 之前我读了一遍替换结果，把它补了回去，所以没有真的发生。**但值得写下来的是"如果没发现会怎样"**：

- `golden.py` 会把那个错的场景**重生**成 fixture，**逐字节**写进语料；
- `test_golden_fixtures_reproduce_exactly` 会绿（它就是"生成器写什么，语料就是什么"）；
- schema 校验会绿（`BROKEN` 是完全合法的取值）；
- 于是**一个错的验收面会被固化成"验收面"**，而每一道守卫都绿。

**这次新加的那一列（退出码）是唯一会红的东西**：表说那一行是 2，而 `index.json` 会变成 3。这不是巧合——"降级"与"坏掉"在契约上是**两个不同的结果**，而退出码是它们唯一稳定的区分。**一个只记 fixture 名的清单抓不到这件事**；这正是"同一件事被写了两遍，只有一遍有锚"的又一例，只不过锚这次是**退出码**。

### 87.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **834 → 835**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **87 → 88** |
| golden fixture | **27 → 29**（`where_broken` / `where_unmanaged_only`） |
| 语料重生 | 是（新增两个文件 + `index.json`）；**既有 26 个文档 fixture 逐字节不变**——`index.json` 变了，因为它要列出新的两个（`git status` 只报这一个既有文件改动，就是这一条的证据） |
| 被移出清单的名字 | **2**（`session_required` / `recovery_required`，原因写在表下） |
| 被加进清单的 fixture | **4**（两个新的 + 两个原本就没被提过的） |
| schema / 台账 / 边界 | 19 / 108 / 均不变 |

### 87.6 如实记录的边界

1. **守卫只管 `where` 这一节。** §8 里 `doctor` 那一节列的是"每条 D1–D10 至少有五个东西（健康 fixture、最小坏例、含证据的诊断、不误伤的 remediation 预览、修复后复验）"，那是**五个方面**，不是五个文件——同样的双向对账对它不成立，要另找判据。`CLI 行为`那一节列的是七件要测的事，同样不是文件名。
2. **守卫不读表里的"场景"那一列。** 它读的是 fixture 名与退出码；"这一行的场景描述对不对"仍然只能人读（与 §85 同一条边界）。
3. **被移出的两个名字不是被删掉。** 它们各自在别处是**真实词汇**：`session_required` 是 session 槽位的规划用语（今天由 `env activate` 的会话栈承担），`recovery_required` 是 `doctor` 的 remediation 取值与事务状态。移出去的是"它们是 `where` 的结果"这个说法，不是这两个词。
4. **补的两个 fixture 只证"这个结果能被产生"**，不证"它在真实机器上以这种方式出现"——它们是**合成的**，与既有 27 个一样。真机路径由 `real_machine_acceptance.py` 管。
5. **这一轮没有扩大语料的形状**：两个新 fixture 与既有的 `where_*` 同构（同一个 schema、同一套字段），所以第 1 层契约（`where-response.schema.json`）**一个字都没动**——这是有意的，改 schema 要走 ADR。

### 87.7 实施顺序

1. 先把验收方案里"为每种结果固定 fixture"这句话当断言读，再把那张清单逐个拿去问代码（**结果存在吗**）与语料（**fixture 存在吗**）；
2. 量出四个不一致（两个名字 + 两个 fixture），并逐个判"是缺 fixture，还是清单写错"；
3. 补两个 fixture；**先让生成器复现既有的 26 个文档 fixture 逐字节不变**（`git status` 只该报 `index.json`），再看新文件；
4. 改文档：清单从自由列表改成表，把两个不是结果的名字移出并写明原因；
5. 写守卫（两条方向 + 退出码）；3 个变异逐个验红；
6. 回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 88. 第 88 阶段：语料里有一条 fixture，它记录的成功码与它自己的字节相反

### 88.1 这一阶段要解决什么

§87 补完 `where` 那一半之后留下的边界是："`doctor` 那节列的是五个方面（健康 fixture、最小坏例、含证据的诊断、不误伤的 remediation 预览、修复后复验），同样的双向对账对它不成立。"

但验收方案 **§14** 自己写着一条更硬的判据：「**`where`/`doctor` 的所有机器可读结果都有固定 fixture**」。这一轮做 `doctor` 那一半——而动手之前先问一个更基础、也更便宜的问题：

> `index.json` 给每个 fixture 记的那个退出码，和 fixture **自己文档里**的结果，一致吗？

### 88.2 实测：28 条里只有一条不一致，而它是最贵的那种

`where` 的文档带 `reason_code`，`doctor` 的带 `status`；两者都由核心的函数决定退出码（`exits.exit_code_for` / `caps.doctor.status_exit_code`）。把 `index.json` 记的数与文档推出的数逐个对上：

| fixture | index 记的 | 文档自己的结果推出 |
|---|---|---|
| `doctor_healthy` | **0** | `status: degraded` → **2** |
| 其余 27 条 | —— | **全部一致**（八个 `where_*`、两条 `search_*` 一个不差） |

这一条错得比它看起来严重三倍：

1. **它的名字在说谎**：叫 `doctor_healthy`，文档里是 `degraded`；
2. **它的退出码与自己的字节相反**：一个端口只要"复现出 degraded 文档 + 退出 0"就会被判为**正确**——**验收面把一个错的实现判成对的**；
3. **它是 §14 那句话唯一的漏洞**：`doctor-response.schema.json` 的 `status` 枚举有四个取值（`healthy`/`degraded`/`broken`/`recovery_required`），语料里只出现三个——**`healthy` 一个 fixture 都没有**，而这个叫 healthy 的正好顶了它的位置。

**根因也量清了**：`_build_root` 建出来的新 root **没有 `state/registry.json` 投影**，`doctor` 于是报 `REGISTRY_PROJECTION_STALE`（证据："state/registry.json is missing or unreadable"）→ `degraded`。而 `doctor_degraded_stale_projection` 做的是"generation 涨了但投影没重写"——**在 §88 之前，这两个 fixture 是同一个场景的两份拷贝**，其中一份还挂着 `healthy` 的名字。

### 88.3 做了什么

1. **生成器里让 `doctor_healthy` 真的健康**：先 `registry.update_projection()` 再跑 `doctor`。现在 `status=healthy`、`exit_code=0`，只剩一条 `POLICY_ONLY_MODE`(info)。
2. **顺带把第二个 fixture 变成真的"过期"**（这是修好第一个的副产品）：投影存在了，于是 `REGISTRY_PROJECTION_STALE` 的证据从"missing or unreadable"变成 `projection generation=0` / `registry generation=1`，impact 也从"读不到声明状态"变成"投影不描述当前 generation"。**两个 fixture 从此是两个场景**，而不是一个场景的两个名字。
3. **新增守卫第三十组**，两条：
   - **每条 fixture 记录的退出码必须等于它自己文档推出的那个**——用核心自己的函数，不在这里重写一遍映射（重写一遍就是第三次把同一张表抄成两份）；
   - **§14 那句话本身可检查**：`doctor` 的 `status` 取值**从 schema 的枚举里读**，`where` 的 reason code 从 `caps/where.py` 里扫（常量会被解析，`DEGRADED_TO_REFERENCE` 算它持有的那个码），每一个都必须出现在某个 fixture 的对应字段里。
4. **在验收方案 §14 那条判据下补一句指针**，指明它现在由第二十九、三十组守着，以及 §87/§88 之前它哪里不成立。
5. **回写计数**：测试 835 → **837**；审计检查 88 → **90**；fixture 数量 **29 不变**。

### 88.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把 §88 那条缺陷恢复（`doctor_healthy` 记 0、文档改回 `degraded`） | 红 | ✅ 红 |
| 把某条 fixture 的 `reason_code` 换成另一个码（而 index 不变） | 红 | ✅ 红 |
| index 里加一条没有文档的 fixture | 红 | ✅ 红 |
| 给覆盖率检查加一个没有任何 fixture 的 doctor 状态（`unmanaged`） | 红 | ✅ 红 |
| 给它加一个没有任何 fixture 的 reason code（`TELEPORT_FAILED`） | 红 | ✅ 红 |

**规则范围一次扩到位**：第一版只对 `where_*` 与 `doctor_*` 生效（覆盖 13 条）。量了一遍全语料之后发现**任何带 `reason_code` 的文档**都能用同一把尺子——那 10 条（8 个 `where_*` + 2 个 `search_*`）**今天全部一致**，于是规则改成"带 `reason_code` 就用它推，否则 `doctor_*` 用 `status` 推"。这不是为了多抓一个缺陷，而是**少一条手写的范围列表**：判据从"前缀是 where\_ 还是 doctor\_"变成"文档自己有没有交代结果"。

**解析器踩了一次，与前几轮同一族**：扫描 `where.py` 时第一版用 `reason = ([A-Z_]+)`，把 `selection_reason = UNMANAGED_ONLY` 里的后半截也匹配上了，于是报出四个"没有任何 fixture 的结果"（`MANAGED_NOT_HEALTHY`、`REFERENCE_NOT_USABLE`、`UNMANAGED_ONLY`、`VERSION_AVAILABLE_BUT_INACTIVE`）——**那四个是选择理由，不是退出码级的结果**。加上 `(?<![A-Za-z_])` 之后才对。这是"解析器必须先知道那句话在说什么"的第四次：前三次是**哪一句**，这次是**哪个词**。

### 88.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **835 → 837**（新增 2 条） |
| 审计检查（`test_l0_consistency.py`） | **88 → 90** |
| golden fixture | **29 不变**（改了两条的**内容**，没有增删） |
| 语料重生 | 是；**`doctor_healthy.json` 与 `doctor_degraded_stale_projection.json` 两个文件变了**，其余 27 个逐字节不变 |
| schema / 台账 / 边界 | 19 / 108 / 均不变 |

### 88.6 如实记录的边界

1. **有一条 fixture 不受这条规则约束**：`search_index_response` 的 `reason_code` 是 `null`（成功），而 `exit_code_for` 需要一个码。它的退出码 0 今天**没有东西核对**。剩下 17 个 fixture 根本不带结果字段（台账、边界、投影、事务、扩展信封），规则对它们不适用——这是**形状不同**，不是缺口。
2. **覆盖率检查只覆盖 `doctor.status` 与 `where` 的 reason code。** `search` 的 `status` 枚举（`ok`/`degraded`/`error`/`cancelled`/`timed_out`）里有四个取值没有任何 fixture——这一轮**没有**给它加，因为 §14 那句话只点了 `where`/`doctor`，而"search 的每个状态都要有 fixture"是一次**新的工作量承诺**，不该顺手塞进这一轮。**记在这里，作为一个明确的未做项。**
3. **`doctor_healthy` 修好之后，`doctor_degraded_stale_projection` 的证据字符串变了**——它是**同一个改动**的必然结果（投影从"不存在"变成"过期"）。这不是"顺手改了别的 fixture"，而是把那两个 fixture 从"同一个场景两份拷贝"变成两个场景时，第二份自然要说的话。
4. **守卫不检查 fixture 与真机行为一致**。它检查的是**语料内部**自洽（index ⟷ 文档）与**覆盖面**（结果词汇 ⟷ 语料）。真机路径由 `real_machine_acceptance.py` 管。
5. **这一轮没有改 schema。** `doctor-response` 的 `status` 枚举本来就是那四个值——变的只是语料终于把第四个也覆盖了。

### 88.7 实施顺序

1. 先问最便宜的那个问题（**index 记的退出码与文档一致吗**），28 条里量出 1 条；
2. 追根因：新 root 没有投影 → `doctor` 报 `REGISTRY_PROJECTION_STALE` → 两个 fixture 其实是同一个场景；
3. 修生成器（先写投影），**并检查这次改动是否让别的 fixture 变化**（它让"过期"那条的证据真的变成过期）；
4. 写守卫：先是"每条 fixture 的退出码要能从它自己文档推出"，再是"§14 那句话可检查"；
5. 量一遍全语料，把规则范围从"两个前缀"扩成"文档自己有没有交代结果"（多覆盖 2 条，**少一条手写列表**）；
6. 5 个变异逐个验红；解析器踩一次（`selection_reason`）并修掉；
7. 在验收方案 §14 补指针；回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 89. 第 89 阶段：`search` 的三个状态里，一个可产生却没有语料，两个没有写者

### 89.1 这一阶段要解决什么

§88 的边界里留了一条明确的未做项：「`search` 的 `status` 枚举里有四个取值没有任何 fixture——§14 那句话只点了 `where`/`doctor`，给 search 加覆盖是一次新的工作量承诺。」

这一轮兑现它。`search-response.schema.json` 的 `status` 枚举是五个值：`ok` / `degraded` / `error` / `cancelled` / `timed_out`，而语料只覆盖两个（`ok`、`degraded`）。**"没覆盖"有两种完全不同的原因**，分开量：

| 取值 | 谁写它 | 语料 |
|---|---|---|
| `ok` | `caps/search.py`（索引答的且完整） | 有（`search_index_response`） |
| `degraded` | `caps/search.py`（索引旧了/覆盖不足/回落 crawl） | 有（两条） |
| `timed_out` | **`caps/search.py`**（crawl 撞上 `max_duration_ms`，`reason_code=SEARCH_TIMEOUT`，退出码 2） | **没有** |
| `error` | **没有任何写者** | 没有（`field-values.md` 已打 †） |
| `cancelled` | **没有任何写者** | 没有（同上） |

所以真正缺的只有 `timed_out` 一个——**而且它是个真结果**：退出码 2、`reason_code=SEARCH_TIMEOUT`、答案是部分且**没有 cursor**（`search.py:947`）。另外两个是"这一版发不出来"，`references/field-values.md` 早就打了 †，`test_l1_field_values.py` 还在两个方向上守着那个标记（打了 † 的必须没人写、没打 † 的必须有写者）。

**这就是"覆盖率"该有的形状：缺的那一个要补，另外两个要有据可查，而不是三者一起被忽略。**

### 89.2 实测：`timed_out` 可以确定性地做出来

第一反应是"超时是不可复现的，所以做不了 fixture"——**量了一下，不是**。`crawl` 的签名是：

```python
def crawl(..., time_source: Callable[[], float] = time.monotonic) -> CrawlOutcome:
    deadline = time_source() + (int(request["max_duration_ms"]) / 1000.0)
```

**时间源是注入的**。于是"第一次调用定下截止时刻、之后每次调用都已经过了它"就能让循环在第一轮就停下——**不是赛跑，是构造**：

```python
ticks = iter([0.0] + [10_000.0] * 64)
... time_source=lambda: next(ticks, 10_000.0)
```

产出的 fixture 正好是契约描述的那种结果：`status=timed_out`、`reason_code=SEARCH_TIMEOUT`、`next_cursor=null`、`coverage=none`、0 条命中、`index.json` 记 **2**。

**而它放在最后是有意的，这一条本身是个教训。** 第一版把它插在 `search_response` 之后，重新生成后发现**另外两条 `search_*` fixture 也变了**——它们的 `started_at`/`finished_at`/`last_indexed_at` **整体后移 2 秒**。原因：`execute_search` 会读共享的 `FakeClock` 两次，多插一次调用就把它后面每一次调用都推后。把新 fixture 挪到最后，diff 就只剩 `index.json` 加新文件。**"加一条 fixture"不该改动别的 fixture 的字节**——即使改动只是时间戳，它也会让"这次重生是外科式的"这句话变成假的。（§87 也记过同一类：`index.json` 变、26 个文档不变，那条是**可以解释的**；这一条如果不挪位置就是**不可解释的**。）

### 89.3 做了什么

1. **补 `search_timeout_response` fixture**（语料 29 → 30），并在 `SCHEMA_FOR_FIXTURE` 里注册为 `search-response`。
2. **新增守卫第三十一组**：`search-response.status` 的每个取值都必须**有 fixture**，**除非它在那张字段取值表里带 †**。**例外从被检查的文档里读，不在测试里再抄一份**——这正是 §85/§86/§82 反复得到的形状：硬编码的例外名单是守卫开始说谎的地方，而这张表本来就由另一个守卫（`test_l1_field_values.py`）双方向钉住。
3. **验收方案 §14 那句话跟着升级**：从 `where`/`doctor` 扩到 `where`/`doctor`/`search`，并写明第二十九/三十/三十一组各管哪一半。**是文档追上保证，不是把保证降到文档。**
4. **回写计数**：测试 837 → **838**；审计检查 90 → **91**；fixture 29 → **30**。

### 89.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 给枚举加一个没有任何 fixture 的状态（`wat`） | 红 | ✅ 红 |
| **把 † 例外全部去掉**（让 `error`/`cancelled` 也要求 fixture） | 红 | ✅ 红 |

第二个变异是这条守卫的关键：**它证明那张例外表是承重的**。如果去掉 † 之后守卫仍然是绿的，说明它其实什么都没管——那时它守的是"三个值"而不是"五个值减去两个有据可查的例外"。

（这一组还自动继承了第三十组：新 fixture 带 `reason_code`，于是"记录的退出码必须等于它自己文档推出的那个"当场覆盖了它——`SEARCH_TIMEOUT` → 2。**上一轮写的规则这一轮不用改就管住了新东西**，这是判断一条规则写得好不好的实际标准。）

### 89.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **837 → 838**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **90 → 91** |
| golden fixture | **29 → 30**（`search_timeout_response`） |
| 语料重生 | 是；**既有 28 个文档 fixture 逐字节不变，只有 `index.json` 变**（新 fixture 挪到最后之后） |
| schema / 台账 / 边界 | 19 / 108 / 均不变 |

### 89.6 如实记录的边界

1. **覆盖率规则的适用范围是"schema 里枚举过的结果字段"**，不是"所有字段的所有取值"。`search` 的 `freshness.state`（`current`/`stale`/`degraded`/`rebuilding`/`unknown`）与 `coverage`（`complete_for_roots`/`partial`/`none`）也有枚举，**这一轮没有给它们做覆盖率检查**——它们是**子字段**，fixture 的粒度是"一次搜索的结论"，而 `state`/`coverage` 由那一次的结论一起决定。要不要逐个覆盖，是一次**新的判断**，不该顺手加。
2. **`error`/`cancelled` 的 † 是"这一版没有写者"，不是"永远不会有"。** 守卫的形状是"要么有 fixture、要么有 †"，所以哪天有人真的写出 `error`，`test_l1_field_values.py` 会先红（† 与写者矛盾），改完 † 之后**这一组会紧接着红**（现在必须补 fixture）——两道守卫接得上，这是有意设计的顺序。
3. **`timed_out` 的 fixture 是合成的**：它构造时间源，不真的等一秒钟。它证的是"这个结果能被产生、且形状是这样"，**不是**"真机上超时会发生"。
4. **这一轮没有把 `search` 的其它机器可读结果纳入 §14 的句子**（`explain`/`status` 两个子命令的输出没有 `status` 枚举）。§14 那句话现在说的是 `where`/`doctor`/`search` 的**结果**，而这三个是它们的**结论字段**。
5. **`CrawlOutcome` 里还有一个 `truncated`**（撞上 `max_records` 或深度上限），它**不等于** `timed_out`：`truncated` 只影响 `coverage=partial` 与 cursor 的存在，`status` 仍是 `ok`/`degraded`。这一轮没有为"纯 truncated"另加 fixture——`search_response` 那条 crawl 的 `coverage` 就是完整的，而 `search_index_response` 覆盖了 `complete_for_roots`。**"truncated 但没有超时"这一格今天没有 fixture**，记在这里。

### 89.7 实施顺序

1. 先量 §88 留下的那条未做项：五个状态值，逐个问"谁写它"（`grep` 代码）与"有没有语料"；
2. 分开两类原因（**可产生但没覆盖** vs **没有写者**），只给前者补；
3. 试做 `timed_out` 之前先看 `crawl` 的时间源是不是注入的——**是**，所以这不是赛跑；
4. **加完之后立刻检查 diff 是不是外科式的**，发现另外两条 fixture 的时间戳被推后 2 秒 → 把新 fixture 挪到最后；
5. 写守卫（覆盖率 + 从字段取值表读例外）；2 个变异逐个验红，其中"去掉 †"那个是证明例外表承重的关键；
6. 升级验收方案 §14 的句子；回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 90. 第 90 阶段：核心打印的文档与语料覆盖的文档，是两个不同的集合

### 90.1 这一阶段要解决什么

`AGENTS.md` §7 有一条硬规则：**核心在打印任何对外 JSON 之前调用 `schema_io.validate_self`**。于是**传给它的那些名字，就是"这个 build 真的会打印的文档"的权威定义**——不是谁的清单，是代码说的。

§87–§89 把 `where`/`doctor`/`search` 的**结果字段**逐个对上了语料。这一轮换一个轴，问一个更粗也更基础的问题：

> **核心打印的文档，和语料里有 fixture 的文档，是同一批吗？**

### 90.2 实测：两个方向同时错

把 `validate_self("<name>")` 的调用点扫出来（8 个文档），与 `SCHEMA_FOR_FIXTURE` 的值（7 个 schema）并排：

| schema | 核心会打印（`validate_self`） | 语料有 fixture |
|---|---|---|
| `where-response` | ✅ | ✅ 8 条 |
| `doctor-response` | ✅ | ✅ 5 条 |
| `registry-projection` | ✅ | ✅ 2 条 |
| `search-response` | ✅ | ✅ 4 条 |
| `transaction` | ✅ | ✅ 1 条 |
| `extension-envelope` | ✅ | ✅ 1 条 |
| **`plan`** | ✅ | **❌ 一条都没有** |
| **`managed-tool-instance`** | ✅ | **❌ 一条都没有** |
| **`reference-plan`** | **❌（构建路径不校验）** | ✅ 1 条 |

**缺的两个正是事务引擎最中心的两份文档**：`plan` 是"被批准、被执行"的那份东西，`managed-tool-instance` 是"被登记、被绑定"的那份东西。它们在 `transaction_finalized` 里**间接**出现过（journal 里带着 plan 与 instance），但**没有一份 fixture 是它们自己**——一个端口可以把 plan 的形状做错，只要那份事务语料照样能对上。

**第三个是反方向的错**：`reference-plan` 有 fixture，`cli.py` 也会打印它，但**只有 `--plan-file` 那条路在加载时校验它**；`build_reference_plan` 造出来的那条路**从不自校验**——`cmd_env_persist` 直接 `_emit` 它。所以"核心打印前一定自校验"这句话在**一条真实可达的路径**上是假的（`env persist --dry-run` 与"没有 token"两条分支都会走到）。

### 90.3 做了什么

1. **补两个 fixture**：`plan_fake_tool`（`create_plan` 造出来的计划）与 `managed_tool_instance`（实例 payload），都在已经把这两样东西造出来的那个事务小节里登记，并在 `SCHEMA_FOR_FIXTURE` 里注册为 `plan` / `managed-tool-instance`。语料 30 → **32**。
2. **补上缺的那个自校验**：`cmd_env_persist` 里在 if/else 之后加 `validate_self("reference-plan", plan)`——两条路（加载的与构建的）都覆盖，位置就在"这份 plan 定稿、下面要开始打印"的地方。（同时补了 `from .schema_io import validate_self`：这个函数里原本没有导入它，**这正是那条路径没校验的原因**。）
3. **新增守卫第三十二组**：**两个集合恰好相等**，双向都查。名字列表从源码扫出来（`_?validate_self\("([^"]+)"`），不在这里重写一遍——所以"新加一个对外文档却没语料"会当场红，而"给一个没人打印的 schema 加语料"也会红。
4. **在 schema 目录里补一句人能读的话**：`docs/schema/README.md` 的边界表说的是"这份 schema 守哪条边界"，从来没说过"这个 build 会不会打印它"。新增一段散文（**刻意不用表格**：§80 那组守卫会把 `| \`x.schema.json\` | 描述 |` 形状的每一行都当成边界行，加一行表就等于悄悄改边界表），点名**9 个会被打印的 schema**，并如实说明其余十个的处境：**六个在任何代码里连名字都没出现**（`broker-request`、`broker-response`、`common`、`desired-manifest`、`gc-plan`、`runtime-instance`），**四个被代码按名字引用但不走自校验**（`approval-token`、`extension-manifest`、`root-marker`、`search-request`——它们是**入口**校验或落盘，不是打印）。
5. **回写计数**：测试 838 → **839**；审计检查 91 → **92**；fixture 30 → **32**。

### 90.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 造的集合里多一个没人语料的文档（`ghost-response`） | 红 | ✅ 红（"自校验但没有 fixture"） |
| 语料那头多一个没人打印的 schema（`runtime-instance`） | 红 | ✅ 红（"有 fixture 但核心从不自校验它"） |

**这一组是"双向恰好相等"的第三个实例**（前面是 §83 的测试计数链、§86 的决定入口、§87 的 `where` 清单与语料）。四次的形状完全一样：**两份集合各自"看起来完整"，只有把它们并排放才看得出谁少了谁。** 而这一轮的两个方向各有一次命中——**只查一个方向的守卫会漏掉一半**。

### 90.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **838 → 839**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **91 → 92** |
| golden fixture | **30 → 32**（`plan_fake_tool` / `managed_tool_instance`） |
| 语料重生 | 是；**既有 29 个文档 fixture 逐字节不变，只有 `index.json` 变** |
| 被修的实现 | **1 处**（`cmd_env_persist`：新增 `reference-plan` 的自校验与它缺的导入） |
| schema / 台账 / 边界 | 19 / 108 / 均不变（**没有改任何 schema**） |

### 90.6 如实记录的边界

1. **"会被打印"是从 `validate_self` 的调用点推出来的，不是从 CLI 的全部输出推出来的。** 一个**没有**自校验的打印路径不会被这条规则发现——`reference-plan` 正是这样被漏掉的（这一轮修了它，但规则本身抓不到"下一处"）。真正完备的形状是"每个 `_emit` 之前都有一次自校验"，那需要在 CLI 层做数据流分析，**这一轮没做**，记在这里。
2. **`common.schema.json` 不是文档**，是 `$defs` 片段，被别的 schema `$ref`。它落在"六个没人引用的"里，是因为扫描找的是**代码里的字符串**；这不表示它没用。
3. **`desired-manifest` / `gc-plan` / `runtime-instance` / `broker-*` 没有生产者，不等于"设计有问题"**：`broker-*` 属 P2 的受保护 IPC，`gc-plan` 与 `runtime-instance` 属 P4/P5。它们是**已发布契约先于实现**，这正是第 1 层契约该有的样子；README 里那句只陈述事实，**没有把它们写成缺陷**。
4. **`approval-token` 的"没有生产者"要打个折**：`cli/tests/fake_issuer.py` 会造它，真机上的生产签发方按 ADR-0025 的 D1 维持"等 P2"（见 §82）。它不在"会被打印"的集合里，因为它**不是被打印的**，是被消费的。
5. **新增的两个 fixture 是合成的**，与其余 30 条一样：它们证"这两份文档的形状被钉住了"，不证"真机上以这种方式产生"。
6. **这一轮没有给 `search-request` 加自校验**（它是**入口**文档，由 `build_request` 造出来后又嵌进 `search-response`；响应被自校验时是否连带校验了内嵌的请求，**这一轮没有查**）。记下来，免得下次以为是查过了。

### 90.7 实施顺序

1. 先找**权威定义**：不是"谁列过清单"，而是"代码在哪里说'这是我要打印的东西'"——答案是 `validate_self` 的调用点；
2. 把两个集合并排，**两个方向都看**（这一轮两边各命中一次）；
3. 补两个 fixture（放在已经把 plan 与 instance 造出来的那个小节里，不另起炉灶）；
4. 修第三处：`reference-plan` 的构建路径补自校验——**并顺手发现它连导入都缺**；
5. 写守卫（双向等集），两个变异各验一个方向；
6. 把"哪 9 个会被打印、其余 10 个为什么不"写进 schema 目录，**用散文不用表格**（§80 的边界表解析器按行的形状取行）；
7. 回写计数；跑全量 + 旧切片 + 真机验收；确认既有语料逐字节不变；提交。
## 91. 第 91 阶段：点名一个能力的入口，必须用同一条边界

### 91.1 这一阶段要解决什么

§90 用的轴是"核心打印的文档"。这一轮换一个轴，问一个更基础的问题：

> **"这个名字是不是一个 AIROOT 管得着的能力"——这条边界，是不是每个点名它的入口都问了？**

权威不是谁的清单，是 `policy/capabilities.json`（`cap-2`，**冻结能力清单**）。`AGENTS.md` §6 早就写明了 `adopt --mode import` 的口径："`--capability` 必须是**冻结能力清单**里的名字：不在清单里就返回 `CAPABILITY_NOT_DECLARED`(9)，与 `plan` 同一口径。"

而 §90 刚刚证明过另一件事：**一句写在文档里的"统一口径"，在代码里可以是几处各自的 `if`。** 于是这一轮去数。

### 91.2 实测：八个入口，七个设防，一个不设防

"点名一个能力"的入口不能靠人列清单——列出来的清单会随 parser 一起陈旧。所以**从 parser 自己扫**：哪个命令路径（含子命令路径）的 parser 上带着 `dest == "capability"` 的参数（位置参数或 `--capability`）。扫出来正好 **8** 个：

| 入口 | 边界 | 实测行为（给一个没冻结过的名字） |
|---|---|---|
| `plan` | ✅ | `CAPABILITY_NOT_DECLARED`(9) |
| `scope decide` | ✅ | `CAPABILITY_NOT_DECLARED`(9) |
| `adopt --mode import` | ✅ | `CAPABILITY_NOT_DECLARED`(9) |
| **`tool pin`** | **❌** | **退出码 0 / `SUCCESS`，并把名字写进 `state/desired.json`** |
| `where` | —— 查询 | exit 1 `NOT_FOUND`（"哪儿都没有"是个真答案） |
| `tool list --capability` | —— 查询 | exit 0 `SUCCESS`，0 条 |
| `capability check` | —— 它**就是**这条边界 | 自己就回答 `CAPABILITY_NOT_DECLARED` |
| `source resolve` | —— 解析下载配方，不是作用于能力 | `NOT_FOUND`(1) |

**第四个是缺陷，而且比"少一道门"更糟：它还报错了原因。**

`tool pin totally-not-a-frozen-capability --version 1.0` 的输出里有：

```json
"plan_blocked_by": "NOT_FOUND",
"plan_blocked_detail": "no trusted source is declared for totally-not-a-frozen-capability"
```

这是**第二个**阻塞。`pin` 先把意图落了盘，然后 `evaluate` 发现没同步，于是去构造计划——计划在 `resolve_source` 上失败（清单里当然没有这个配方），那个错误就成了报告里的 `plan_blocked_by`。**第一个阻塞（这个名字根本不是能力）从头到尾没有被说出口**，而它才是更靠前的那个：**就算给这个陌生的名字配一份来源配方，也仍然没有任何计划能满足它**（`planner` 拿不到冻结条目里的入口与副作用上限）。

所以这里有两处要修，不是一处：少了一道门；以及**报告把第二个原因当成第一个原因讲**（§8 的诚实规则："不编造确定性"）。

### 91.3 做了什么

1. **`caps/desired.py` 的 `pin()` 加边界**，用与 `plan` **同一个** reason code（`CAPABILITY_NOT_DECLARED`），`evidence` 里带两句话：这份冻结清单的 revision 与它声明的全部名字；以及"清单外的意图没有任何计划能满足它，请先走规划 §15.4 的提议 → 冻结 → 白名单"。**门开在最前面**：`pin()` 在写 `state/desired.json` **之前**就拒绝，所以修好之后连一行状态都不会留下。
2. **新增守卫（第三十三组，放在 `test_l1_boundary.py` 里——它们是行为守卫，不是跨工件一致性检查，所以 `test_l0_consistency.py` 的 92 项不变）**：
   - `capability_naming_commands()`：从 `build_parser()` 走一遍，返回"带 `capability` 参数的命令路径"集合。**分母是推出来的**，不是抄的——这是 §79/§81 处理动词与 lane 用过的同一个形状。
   - 三个分类：**动作**（写状态或造计划：`plan`/`tool pin`/`scope decide`/`adopt`）、**查询**（`where`/`tool list`）、**例外**（`capability check`/`source resolve`，各自的注释里写了理由）。
   - 一条测试守**分母**：`derived == classified`，两个方向都查；另外要求 `len(derived) >= 6`，否则"扫描坏了"会让这条守卫变成空转。
   - 四条（参数化）测试守**动作类**：每一个都必须对没冻结的名字返回 9 + `CAPABILITY_NOT_DECLARED`。
   - 两条（参数化）测试守**查询类**：每一个都必须**回答**而不是拒绝，并且（输出里有 `capability_id` 时）回答的是被问的那个名字。
3. **顺手修了一个 flake**：`test_concurrent_commits_keep_a_single_active_binding` 在约四次全量运行里红过一次——两个 worker 都报 `committed`，而夹具自己那条连接读到 **0** 个 active binding（单独跑是绿的）。原因没有钉死（夹具句柄持有的快照是候选之一），所以改成**用一条新开的 registry 读结果**，让断言不再依赖那个句柄的状态；`finally` 里关掉。

### 91.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把 `pin()` 的门去掉**（回到这一阶段发现的状态） | 红 | ✅ 红——**只有 `[tool pin]` 一条红**（退出码 0），另外三条本来就是绿的 |
| **给 parser 加第九个点名能力的命令**（`tool status --capability`）而不分类 | 红 | ✅ 红（`derived - classified = {tool status}`） |
| **给分类表加一个不再点名能力的名字**（`tool status` 进例外表） | 红 | ✅ 红（`classified - derived = {tool status}`） |
| **把 `where` 从查询类挪进动作类** | 红 | ✅ 红——但**红的是动作类那条**（`where` 返回 1，不是 9）；**分类那条仍然绿** |

第一个变异是这一阶段的关键：它证明那四条动作类测试**确实是靠这道门才绿的**，而另外三条不是因为"这道门"绿的——它们各自早就有自己的边界。**四合一的一刀切测试会掩盖这个区别**，参数化保住了它。

第四个变异（原本是想用它证明查询类是承重的）**反而量出了一件更有用的事**：把命令在两个桶之间搬家，分类守卫**看不见**——两个集合的并集没变。于是查询类那条测试只是**静默少了一个用例**（`where` 从 2 条参数变成 1 条），并且只有**动作类那条**才会响。记在 91.6 里。

### 91.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **839 → 846**（新增 7 条：1 条分母 + 4 条动作 + 2 条查询） |
| 审计检查（`test_l0_consistency.py`） | **92 → 92**（不变：这一组是行为守卫，在 `test_l1_boundary.py`） |
| golden fixture | **32 → 32**（不变） |
| 被修的实现 | **1 处**（`caps/desired.py`：`pin()` 加冻结边界） |
| 被修的测试 | **1 处**（并发提交那条改成新开 registry 读结果） |
| schema / 台账 / 边界 | 19 / 108 / 均不变（**没有改任何 schema、任何 policy**） |
| 语料重生 | **不需要**：`tool pin` 的**失败**形状变了，但那条路径不产 fixture；成功路径的对外 JSON 一个字节没动 |

### 91.6 如实记录的边界

1. **"哪些入口点名一个能力"是从 parser 推出来的，不是从语义推出来的。** 一个把能力名字放在 `capability` 之外的写法（比如 `--for java`，或某个命令收一个"目标"字符串而它其实是能力名）扫不到。`tool status` 与 `tool verify` 收的 `target` 就**可能是**能力 id 或 instance id——它们**不在**这 8 个里，因为参数名不是 `capability`。这一轮没有改它们的判断（它们只读、不改状态），但**"8 个"这个分母的边界是"参数名叫 `capability` 的路径"**，不是"所有会用到能力名字的路径"。
2. **分类是判断，守卫只能查"有没有分类"，查不出"分对了没有"。** 第四个变异就是这条：把 `where` 挪进动作类，分类守卫仍然绿。真正钉住分类的，是**行为测试跑在命令所在的那个桶上**；桶换了，覆盖也跟着换，而**守卫不会告诉你覆盖少了**。
3. **查询类那条测试对 `tool list` 的检查比 `where` 弱**：`where` 的输出里有 `capability_id`，`tool list` 只给一个过滤后的集合，所以"回答的是被问的那个名字"那一句是**有条件的**（字段在才查）。这一轮**没有**为 `tool list` 发明一个字段来把这件事变强。
4. **`source resolve` 的"例外"是有据可查的，不是放过**：`policy/sources.json`（revision `src-1`）里注册了 `rust-toolchain`——那个名字**还没冻结**（ADR-0001 先取工具链、后冻结能力），条目自带验证备注（§59）。也就是说"来源清单里有一个没冻结的名字"是**有意**的，不是漂移；`source resolve rust-toolchain` 会走到 `NOT_FOUND`(1)，**不是**边界拒绝。要不要给"来源清单里的名字必须在冻结清单里"再加一条守卫，是一次**新的**判断，这一轮没做（`build` 是冻结的，`rust-toolchain` 是 P2 语言切换的目标）。
5. **修好之后，"报告第一个阻塞"这件事只在 `pin` 这一条路径上成立。** `plan` 那一侧本来就在最前面拒绝，所以它从来不需要"报第一个"。我没有去改 `cmd_tool_pin` 里那段"计划失败就把错误记进 `plan_blocked_by`"的逻辑——门往前挪之后，那段逻辑够用了；但它**仍然是"最后失败的那个原因"**，不是"最靠前的那个原因"。多级阻塞的排序问题在别的命令上**没有查**。
6. **并发那个 flake 只是被绕开，没有被解释。** 断言不再依赖夹具句柄的状态，但"两个 worker 都提交成功、而夹具连接读到 0 个 active binding"这个现象**没有找到根因**。它现在绿，不代表那个快照行为已经被理解。这是本轮**最不干净**的一处，记在这里，而不是写成"已修复"。

### 91.7 实施顺序

1. 先把"哪些入口点名一个能力"**从 parser 推出来**，不列清单；
2. 逐个量它对没冻结的名字做了什么，把"拒绝/回答/例外"分开，并给每个例外写一句可查的理由；
3. 找到唯一不设防的那个（`tool pin`），**并读它到底报了什么**——这里发现它报的是第二个原因；
4. 门开在最前面（写状态之前），用与 `plan` 同一个 code；
5. 写守卫：分母双向、动作类行为、查询类行为；四个变异逐个验红，其中"搬家"那个**没有按预想的方式红**，如实记下来；
6. 顺手修并发 flake，并如实写"绕开，未解释"；
7. 回写计数（`AGENTS.md` 5 处：阶段范围 2 处、当前测试总数 3 处；审查报告 2 处）；跑全量 + 旧切片 + 真机验收；提交。
## 92. 第 92 阶段：一份已发布的 schema、一份真实存在的文件、和一句"这就是它的形状"

### 92.1 这一阶段要解决什么

§90 把"核心打印的文档"定义成 `validate_self` 的调用点，那是对的。但它紧接着写了一句更宽的话：那六个 schema"**在任何代码里连名字都没出现**"。这句话是**从更窄的测量里得出的更宽的断言**——它只扫了 `validate_self` 的调用点。**同一个错误 §85 刚在仓库地图的文件行数上犯过**（把一个抄来的数当成推出来的数）。

所以这一轮换一个轴，问一个更粗的问题：

> **19 个已发布的 schema 里，哪几个真的有人用？没人用的那几个，读者能不能查到一个"为什么"？**

### 92.2 实测：14 个在用，5 个没有写者

推导规则（不是清单）：一个 schema 算"在用"，当且仅当**代码把它的名字传给 `validate_self`/`validate_document`**（字面量），**或者别的 schema `$ref` 它**。19 个的结果：

| 侧 | 数量 | 内容 |
|---|---|---|
| 代码在校验 | 13 | `plan`(8 处)、`transaction`(3)、`approval-token`(2)、`root-marker`(2)、`managed-tool-instance`(2)、`reference-plan`(2)、`where-response`、`doctor-response`、`registry-projection`、`search-request`、`search-response`、`extension-envelope`、`extension-manifest` |
| 只被 `$ref` | 1 | `common`（**其余 18 个全都 `$ref` 它**，实测 0 例外） |
| **没有写者** | **5** | `broker-request`、`broker-response`、`desired-manifest`、`gc-plan`、`runtime-instance` |

**§90 那句"六个在任何代码里连名字都没出现"是错的**，两处：
- `desired-manifest` 的名字出现在 `caps/desired.py` 的**报错消息**里（`unsupported desired-manifest schema_version: ...`）——代码**声称**它写的就是那份 manifest；
- `common` 的名字当然"出现"——每个 schema 都用 `$ref` 指它。

正确的说法是"**5 个没有写者**"，而且它现在是**推出来的**。

**然后是这一轮真正的发现。** 拿这 5 个去对三份文档：

| schema | `references/field-values.md` 说的写者 | 实情 |
|---|---|---|
| `broker-request` / `broker-response` | "没有任何代码写出或读入它" | ✅ 一致 |
| `desired-manifest` | （没有写者），并写明 `source` 恒为 `null` | ⚠️ 与取值表一致，但**与它自己的 schema 冲突**（见 92.4） |
| `runtime-instance` | （没有写者） | ✅ 一致 |
| **`gc-plan`** | **`caps/lifecycle.py`, `tx/artifact.py`, `tx/simulate.py`** | ❌ **没有任何代码写出 `gc-plan`** |

### 92.3 那条假声明为什么能一路绿到现在

取值表那组守卫（§74）判"这一版谁写出"的方式是：**在被点名的文件里搜这个取值的字符串**。`gc-plan.items[].kind` 的两个取值是 `managed_tool`/`runtime`；而 `caps/lifecycle.py` 造的那份 `plan` 里，`target.kind` 写的**正好也是这两个词**——字符串当然找得到，守卫因此通过。

**这条盲点早就写在草案里**（§74/§77："守卫比的是值集合，不是某一行有没有被写到错误的路径上"），这一轮是它第一次**真的产出一条假声明**：一份**参考文档**（它自称描述当前版本）把一个 schema 的写者写成三个只在写**另一个字段**的模块。

### 92.4 第二处：`state/desired.json` 不是那份 manifest，而且没有人在校验它

实测：`tool pin` 写出的 `state/desired.json`（317 字节），拿去跑 `validate_self("desired-manifest", ...)` **被拒**：

```text
source: None is not of type 'object'
policies: 'auto_approve' is a required property
```

而**三处**都把它当成那份 manifest：模块 docstring 说它 "following the shape 规划 §17 prescribes"；`load_desired` 的报错说它是 `desired-manifest`；schema 目录的边界行说 `desired-manifest` = "Desired state supplied by project or signed manifest"。**三处指向一份它不满足的契约**，而这个文件**从来没有被任何代码校验过**——分歧因此一直不可见。

处置（**ADR-0026**，这一轮新增）：**两侧都不改形状，改的是"谁在说它是什么"。**

- **不把文件改成 schema-valid**：要满足 schema 就得往里写一个 `source` 对象，而这一版**没有**来源证明（ADR-0025 的 D1 裁决维持现状），写进去就是**编造来源**——为了让校验变绿而发明字段，比校验不绿更糟；`policies.auto_approve`（跳过确认的 `memory` 规则）同理，`.ai/tooling.json` 刻意保持只读（ADR-0025 的 D3）。
- **不改 schema**：改必填字段要新 schema id（README 规则 2），落在 ADR-0021 的图层级豁免里；而且那份 schema 描述的是**真正的 manifest 边界**（项目清单、带来源与签名的远程 manifest），它是对的，只是这一版没有实现它。
- **改称呼**：报错消息改成念**实际读的那个文件**，并说明它不是那份 manifest（`reason_code` 仍是 `INVALID_INPUT`(8)）；docstring 与 schema 目录的话跟着改。

### 92.5 第三处：一个名字，两份契约

同一个推导顺带量到的：代码里 **14 个** `"operation"` 字面量（13 个是信封标签；第 14 个 `install_tool` 是 `plan` 自己的字段——取值表早就写明这是两个不同字段），对上 **19 个** schema 名，把 `_`/`-` 归一化之后，**恰好一处完全碰撞**：

> 打印出来的信封标签 `gc_plan`（`tool gc --plan`）⟷ 已发布的 schema 名 `gc-plan`

那份 schema 描述的是**批量**回收计划（`items`/`blocked_items`/`requires_approval`），本版没有写者；`tool gc --plan` 打印的是**报告信封**；本版真正的回收计划是 `plan.schema.json`（`operation=gc_apply`，一次一份 payload）。查这个词的人会被送到一份与手上文档无关的契约——**而目录里没有一句话说这件事**。

### 92.6 做了什么

1. **`references/field-values.md`**：`gc-plan` 那一行改成 `（没有写者）`、两个取值都打 †、含义里写明旧声明错在"**同样两个词、不同字段**"；`operation` 表的 `gc_plan` 行点名 `gc-plan.schema.json` 并说明**它不是它**。
2. **`caps/desired.py`**：`load_desired` 的 schema_version 报错改成念 `state/desired.json`，并附一句"它不是已发布的 `desired-manifest`（ADR-0026）"；模块 docstring 同样改，并列出刻意省略的两个字段与理由。
3. **`docs/schema/README.md`**：把 §90 那段"六个连名字都没出现"换成**推导出来的**陈述 + **5 条带理由的声明**。**用列表不用表格**：§80 的边界行解析器按 `| \`x.schema.json\` | ... |` 的形状取行，**加一张第二列以 schema 名开头的表等于悄悄改边界表**（§90 为此用过散文，这一轮用列表，两种都不落进那个形状）。
4. **ADR-0026**：记录这次裁决、`state/desired.json` 的形状这一版只有代码定义这一**缺口**、以及"同名词假覆盖总体上仍可能发生"。
5. **新增守卫第三十四组**（`test_l0_consistency.py`，3 条）：在用 ⟷ 声明的**双向**等集；声明为无写者的 schema **不许**被取值表说成有写者；标签 ⟷ schema 名碰撞必须在取值表里申报。
6. 回写计数。

### 92.7 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把 `gc-plan` 的三个"写者"放回去**（回到这一轮发现的状态） | 红 | ✅ 红——**这一条拿真实缺陷验的，不是合成变异** |
| 把 `gc-plan` 的声明条目从 schema 目录删掉 | 红 | ✅ 红（`gc-plan is neither used by any code nor declared as having no writer`） |
| 声明一个并不存在的 schema（`ghost-schema`） | 红 | ✅ 红 |
| 声明一个无写者的 schema 但理由留空 | 红 | ✅ 红 |
| 把 `gc_plan` 那一行里的 `gc-plan.schema.json` 拿掉 | 红 | ✅ 红（"the row must name that schema and say the label is not it"） |
| 多一个与 schema 同名的标签（`plan`） | 红 | ✅ 红 |

**第一个变异是这一轮最重要的证据**：它跑的是**修之前的真实内容**，报出的正是原始缺陷——

```text
field-values.md names ['caps/lifecycle.py', 'tx/artifact.py', 'tx/simulate.py']
as writers of gc-plan, which no code builds
```

前面每个阶段的验红都是"写完守卫再造一个坏版本"；这一条是**守卫对着真实缺陷跑**——它本来就该红，只是当时还没有它。

### 92.8 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **846 → 849**（新增 3 条） |
| 审计检查（`test_l0_consistency.py`） | **92 → 95** |
| golden fixture | **32 → 32**（不变） |
| 被修的实现 | **1 处**（`caps/desired.py`：一句报错消息 + 一段 docstring） |
| 被修的文档 | **3 份**（取值表、schema 目录、ADR 日志新增一条） |
| schema | **19 → 19**（一个字节没动） |
| 语料重生 | **不需要**：重跑 `golden.py` 后语料**逐字节不变**（改的是一句错误消息，不在任何 fixture 的路径上） |

### 92.9 如实记录的边界

1. **"在用"是推出来的，但推导有两处近似。** 它只看**字面量**调用（`validate_self("plan", ...)`）与 `$ref`；用变量传 schema 名的地方扫不到（当前代码里没有，但规则抓不到"下一处"）。而且"有人校验它"**不等于**"校验的是这份文档"——这正是 §90 那条边界的同一句话。
2. **`state/desired.json` 的形状这一版仍然只有代码定义**（`caps/desired.py` 的 `to_document()`），没有任何已发布契约钉它。ADR-0026 把它记成**缺口**而不是"以后会补"：解锁要么是一条新的 desired-state schema，要么给 `desired-manifest` 发新 id，两条都是契约变更。
3. **同名词假覆盖没有根治。** 取值表的守卫仍然按"字符串出现在被点名的文件里"判断写者，这一轮只清掉已经发生的那一处。要根治得按**字段路径**查写者，那是一次独立的改动（要动 §74 那组守卫的核心判据）。
4. **`gc-plan` 与 `runtime-instance` 这两份 schema 我没有动。** 它们是 P4/P5 的契约，删掉是契约变更；这一轮只让"这一版没有写者"变得可查、可守。
5. **`operation` 字面量的集合是"扫出来的"，不是"协议规定的"**：权威是代码（§77），所以 `install_tool` 这种"其实是 `plan` 的字段"也会进来。守卫因此**比它需要的更严**（多查一个词），这是**有意**的：多查不会漏，少查会。
6. **这一轮没有做"每个 `_emit` 之前是否自校验"那件更细的事**（§90.6-1 记的那条未做项）。这一轮查的是**schema 这一层**有没有人用；`_emit` 与它打印的那份文档之间是否自校验是另一个问题——量过一次（**51 个 `_emit` 调用点、41 个函数**，其中 17 个函数在调用链上有一次可达的自校验、24 个没有），但**没有做数据流分析**，记在这里。

### 92.10 实施顺序

1. 先把"在用"**从 artefact 推出来**（调用点 + `$ref`），不列清单；
2. 与 §90 的散文对账，发现它把窄测量的结论写宽了（"连名字都没出现"）；
3. 拿这 5 个去对三份文档，找到唯一的**假声明**（`gc-plan`）与唯一的**称呼冲突**（`state/desired.json`）；
4. 量 `state/desired.json` 到底能不能过那份 schema——**不能**；于是选"不改形状、改称呼"，并把这次裁决写成 ADR-0026；
5. 量标签碰撞：14 个 `operation` 字面量 ⟷ 19 个 schema 名，归一化后恰好一处；
6. 修三份文档 + 一句错误消息；写守卫（3 条），**先用真实缺陷验红**，再补合成变异；
7. 回写计数（`AGENTS.md` 5 处：3 处计数、1 处 ADR 范围、1 处仓库地图的 desired 行；审查报告 2 处）；跑全量 + 旧切片 + 真机验收；提交。
## 93. 第 93 阶段：把"出现过这个字符串"当成"写过这个字段"

### 93.1 这一阶段要解决什么

§92 修掉了 `gc-plan` 那处假声明，并在自己的记录里留了一句话：

> **同名词假覆盖没有根治。** 取值表的守卫仍然按"字符串出现在被点名的文件里"判断写者，这一轮只清掉已经发生的那一处。**要根治得按字段路径查写者，那是一次独立的改动。**

这一轮就是那次改动——**但量过之后，改的不是那组守卫的判据，而是给它加了一条一直缺的上游判据。**

### 93.2 先把机制说清楚

claim 2 的证据是**文本搜索**：在被点名的文件里找这个取值的字符串。`WRITE_PATTERN` 给 **10 个**字段补了"写入形状"的正则——**只在人工判断"同一个词也是别的字段的取值"时才补**。于是三种东西在这个判据下**同形**：

| 种类 | 例子 | 是不是"写出" |
|---|---|---|
| **写** | `"kind": "managed_tool"` | 是 |
| **词表声明** | `FRESHNESS_STATES = ("current", "stale", ...)` | 不是（但它**定义**了字段的取值域，是合法证据） |
| **校验** | `if token["approval_mode"] == "human"` | **不是** |

第三种**根本不是在写**，而文本搜索分不出它和第一种。

### 93.3 实测：19 个 schema 里 12 个"被产生"，7 个没有

先按 §92 留下的方向试：**把判据换成语法**（取值出现在"值位置"——字典字面量的值、关键字实参、非全大写赋值的右侧）。量下来发现**这是个研究项目**：补上 `Return`（`doctor.py` 的状态是 `return` 出来的）、补上元组（`caps/environment.py` 的禁用变量名列表）、补上常量间接引用（`SCOPE_USER = "user"` 之后 `"scope": SCOPE_USER`）……**每补一种就冒出下一种**。一个每轮都要打补丁的判据不是判据——**放弃，并把这个结论写下来**。

换成上游的问题：**这一版到底会不会构造这份文档？** 判据仍然是语法，但只问一件事：**某个函数自己组出来的键（字典字面量的键 + `d["k"] = ...` 的键），覆盖不覆盖这份 schema 的全部必填属性。** 这是"构造一份文档"在代码里的样子：

| 侧 | 数量 | 内容 |
|---|---|---|
| 被某个函数产生 | **12** | `desired-manifest`（`to_document`）、`doctor-response`、`extension-envelope`、`managed-tool-instance`、`plan`、`reference-plan`、`registry-projection`、`root-marker`、`search-request`、`search-response`、`transaction`、`where-response` |
| **没有任何产生者** | **7** | `approval-token`、`broker-request`、`broker-response`、`common`、`extension-manifest`、`gc-plan`、`runtime-instance` |

**然后在这 7 个里找到第二处假声明。** `approval-token` 的两行"谁写出"都写着 `tx/approval.py`，而**同一行左边那格自己说的是"这一版没有生产签发方"**——一行里的两半互相矛盾。`tx/approval.py` 里"这个算法的出现"只有两类，都不是写：

```python
TEST_ALGORITHM = "test_hmac_sha256"      # 校验器接受的常量
PRODUCTION_ALGORITHM = "ed25519"
```

```python
if token["approval_mode"] == "human" and not token.get("approved_by_sid"):   # 校验器在读
```

量过的两条硬事实：**整个 app 里没有任何字典字面量带 `algorithm` 这个键（0 处）**；`human` 在 `tx/approval.py` 里**只出现一次，就是上面那次比较**。一份 token 里的算法长得像 `"algorithm": "..."`，而这个 build 里没有那样一处。

### 93.4 做了什么

1. **修三行**（`approval-token` 的 `approval_mode` / `signature.algorithm`，以及 `common` 的 `$defs.source.signature.algorithm`——同一个词汇）：取值**全部打 †**，"谁写出"改成 `（没有写者）`，含义那一格说明"`tx/approval.py` 只**接受与校验**它"。
2. **新增守卫 claim 4（在 `test_l1_field_values.py` 里，紧挨着 claim 2）**：`_produced_schemas()` 走一遍 app 的 AST，问每个 schema 的必填属性有没有被某个函数自己的键覆盖；**一个没有任何产生者的 schema，它的行不许在"谁写出"里点代码**。
3. **两条豁免都是推导的，不是名单**：
   - schema **没有必填属性** ⇒ 它是**片段**（`common`），不是文档（§80/§90/§92 三处独立说过同一件事）；
   - 行点名的文件里**有非 `.py`** ⇒ 这份文档是**数据写**的（`cli/extensions/*.json` 就是那些 extension manifest 的写者），不是代码构造的。

### 93.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| `approval-token::approval_mode` 的写者改回 `tx/approval.py`（**真实缺陷**） | 红 | ✅ 红：`no function builds any approval-token document, so the writer column cannot name code` |
| 把 `build_request` 里一个键改名（让 `search-request` 看起来没有产生者） | 红 | ✅ 红——**证明那个"构造走法"是承重的**，不是恒真 |
| `produced` 里去掉 `search-request`（守卫体内变异） | 红 | ✅ 红 |
| 把 † 去掉、或把 `（没有写者）` 改回代码（claim 2 已有的方向） | 红 | ✅ 红（原有守卫） |

第二个变异是这一轮**唯一一次去验"判据本身"而不是"判据的应用"**：把 `build_request` 里一个键改个名，`search-request` 立刻从 12 个里掉出去、它的五行当场变红。这证明那个走法**真的在读代码**，而不是永远返回"都被产生"。

### 93.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **849 → 850**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **95 → 95**（不变：这一组在 `test_l1_field_values.py`，与 §91 同一个理由） |
| golden fixture | **32 → 32**（不变） |
| 被修的文档 | **1 份**（取值表 3 行） |
| schema / 代码 | **一个字节没动**（这一轮只改取值表 + 加守卫） |
| 语料重生 | **不需要** |

### 93.7 如实记录的边界

1. **生产判据只认一种构造形状**：函数里"字典字面量的键 + `d["k"] = ...` 的键"覆盖全部必填属性。用 `dict(a=...)`、字典推导式、`update()`、或把键拼出来的构造函数**认不出来**。**认不出来的后果是"假红"**（那份 schema 看起来没有产生者，于是它的行被要求写 `（没有写者）`）——方向是安全的（它逼人来解释），但**不代表它是对的**。今天 12/19 命中，是因为这个 build 恰好都用了同一种写法。
2. **"有产生者"不等于"这些行真的被写了"**：一个函数构造过 `plan`，不代表 `plan` 的每个字段在每个分支都被写。这一轮只堵住"**整份文档没人构造**"这一格，**字段级的同名词问题仍然在**。
3. **claim 2 的判据没有换。** 试过换成语法，量下来是研究项目，放弃了（93.3）。所以"字符串出现在被点名的文件里"**仍然是** claim 2 的证据；它今天的保护是 `WRITE_PATTERN` 的人工补充**加上**这一轮新加的上游判据。**"把 §92 留下的那句话当成已经根治"会是错的。**
4. **`extension-manifest` 的豁免（"点名了非 `.py`"）是事实，不是放过**：`cli/extensions/*.json` 确实是那些 manifest 的写者。但同一条豁免也会放过"一份既由数据写、又由代码写"的文档——那种情况今天没有，规则也不区分。
5. **`common` 的豁免同样是事实**（没有 `required` ⇒ `$defs` 片段），但它**不是"没人写"**：它的取值由别处的文档写，`SHARED_SCHEMAS` 一直这么记。两条豁免都不改变 claim 3（"启用的 schema 不许有没登记的枚举"）。
6. **这一轮没有动 `desired-manifest` 那一格**（§92 / ADR-0026 已经处理）。它**有**产生者——`to_document()` 组出来的键覆盖全部 8 个必填属性——**分歧在值的形状上，不在"有没有人构造"上**。两条判据因此是**正交**的，这也是它没被这一轮抓到、也不该被抓到的原因。

### 93.8 实施顺序

1. 先按 §92 留的话去试"按字段路径查写者"——把判据换成**语法**；
2. 逐轮补语法形状，发现每补一种就冒出下一种 ⇒ **判定这不是一次能做完的改动**，如实放弃并写下原因（这一步和后面的实现一样重要：**试过并说明为什么不做，与没试过不是一回事**）；
3. 换上游判据（"这份文档有没有被构造"），量出 **12 / 7**；
4. 在 7 个没有产生者的里面逐个对取值表，找到 `approval-token` 那两行**自己和自己矛盾**；
5. 修那 3 行；写守卫，两条豁免都从 schema 与文件类型**推导**；
6. 用**真实缺陷**验红（把写者改回去），再用一次真实变异验"构造走法"本身承重；
7. 回写计数（`AGENTS.md` 3 处计数、审查报告 2 处）；跑全量 + 旧切片 + 真机验收；提交。
## 94. 第 94 阶段：agent 读的那些字段，被什么钉住

### 94.1 这一阶段要解决什么

`agents/airoot.json` 是给 agent 用的：34 条 lane，每条说"问什么问题 → 跑哪条命令 → **读哪些字段**"（139 条 `read` 路径）。`test_l1_agent_read_fields.py` 已经保证这些路径**今天存在**（真跑一遍每条命令，结构化解析每个路径）。

但"字段存在"与"字段被契约钉住"是两件事。§90–§93 这条线一直在问"哪份文档有人在打印/在写/在校验"，这一轮换到**读**的那一侧：

> **agent 被告知要读的字段，落在一份被已发布 schema 描述的文档里吗？**

### 94.2 实测：34 条 lane 里只有 5 条读的是"被契约描述的文档"

判据是**行为**的，不是猜的：对每条 lane 真跑命令拿到它打印的文档，把 13 个"文档型" schema（`common` 是没有 `required` 的**片段**，任何 JSON 都能过它，所以它不参与判断——§93 用同一条推导）逐个套上去，看**哪些 schema 真的接受这份文档**；再看该 schema 的**顶层属性**里有没有这条 lane 的读路径（只有两者都成立，才说明"读的字段就是这份契约的字段"）。

| 侧 | 数量 | 内容 |
|---|---|---|
| 读的是被 schema 描述的文档 | **5** | `doctor`→`doctor-response`；`where`×2→`where-response`；`inventory`→`registry-projection`；`search`→`search-response` |
| **读的是 CLI 自己的报告** | **29** | 其余全部（含 `plan`、`adopt --mode import`、`env persist`、`tool pin`、`tool gc`、`repair`、`exec`…） |

**先量出来的三处错，是这一轮真正的发现。** 我最初的声明是**静态推导**出来的（从处理函数沿调用图找可达的 `validate_self`），它给出 7 条；行为判据当场否掉三条：

| lane | 静态推导说 | 行为判据说 | 差在哪 |
|---|---|---|---|
| `search <query>` | `extension-envelope` | **`search-response`** | 两个 schema 的顶层都含 `status`/`data`/`reason_code`，静态推导按字母序取了错的那个 |
| `env persist <external-id>` | `reference-plan` | **没有 schema 描述它** | 打印出来的是**自校验过的 `reference-plan` 加三个报告键** |
| `adopt <file> --mode import` | `plan` | **没有 schema 描述它** | 同上：**自校验过的 `plan` 加同样三个键** |

那三个键是 `plan_file`、`required_action`、`reason_code`，两个 schema 都是 `additionalProperties: false`，所以打印出来的字节**两个都不满足**。

### 94.3 这三个键为什么不该塞进计划契约

它们看起来只是"三个可选属性"（README 规则 2 说加可选属性是 minor 兼容，ADR-0021 又默认放宽），但量的过程中露出两条更硬的理由：

1. **可批准物是文件，不是这份打印件。** `plan_hash` 是在**加这三个键之前**算出来并写进文件的（`state/plans/<id>.json` 里那份才是被批准、被消费的东西），这份打印件携带的是那份文件的 hash。把 `plan_file` 之类写进 `plan` 的契约，等于说"一份计划可以带着它自己被归档到哪"，而这与"hash 覆盖什么"直接打架。
2. **§90 那句"打印前一定自校验"因此仍然是不精确的。** 自校验的是**核心构造的那份文档**；打印出来的信封是它**加三个报告键**。§90 把 `validate_self` 的位置挪对了（挪到了 if/else 之后），但**打印的字节仍然不等于被校验的字节**——这一轮把这句话改精确（AGENTS.md §7）。

所以处置是：**两个 lane 声明 `null`，并把原因写进 lane 的 `notes`**（不是写进测试）——读者要知道的是"这份文档没有契约、但它的计划部分是自校验过的"。

### 94.4 做了什么

1. **每条 lane 新增 `document_schema`**：被 schema 描述的写 schema 名，否则 `null`。**值是从行为测量来的**，不是从调用图猜的。
2. **`agents/airoot.json` 的 `honesty` 块新增 `document_schema_note`**：说明这个键是什么、`null` 是**量过**的事实而不是含糊、5/34 是当前数、以及那两条"计划+三个报告键"的 lane 的特殊处境。
3. **新增守卫（3 条，在 `test_l1_agent_read_fields.py` 里）**：
   - 主检查并入既有的"每条 lane 都真跑一遍"的循环：**声明的 schema 必须真的接受打印出来的文档**，**且至少一条读路径是该 schema 的顶层属性**（否则就是"可达但没读到"——`tool pin` 的 `plan` 正是这个形状）；`null` 则必须**没有任何文档型 schema 接受它**。
   - 一个**双向**的合成检查（两个方向各造一个反例，含"可达但没读"与"没有契约却声明有"）。
   - 一句散文里的数字（`5 of 34`）必须等于实测数——同 §54/§73 给测试数与读路径数的那条绑法。
4. **AGENTS.md §7 的说法改精确**：自校验的是核心构造的文档，打印的信封可能不等于它，并点名那两条 lane 与 `document_schema` 这个可查记录。
5. 回写计数。

### 94.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| `search` 的声明改回 `extension-envelope`（**真实的错误声明**） | 红 | ✅ 红：`document_schema extension-envelope does not validate the printed document` |
| 给 `tool pin` 的读路径加一个 `found`（让"可达的 `plan`"看起来像"读的文档"） | 红 | ✅ 红（先被字段存在性那条抓住） |
| 合成检查里的四个方向（不校验 / 读路径不在顶层 / 有契约却声明 `null` / 没声明 / 名字不是文档型 schema） | 红 | ✅ 红（都在同一条测试里） |
| 散文里的 `5 of 34` 改成别的数 | 红 | ✅ 红 |

**第一个变异是拿真实错误验的**：它不是这一轮新造的，而是**这一轮量出来并改掉的**——把改之前的声明放回去，守卫当场指名。

### 94.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **850 → 852**（新增 2 条） |
| 审计检查（`test_l0_consistency.py`） | **95 → 95**（不变：这一组在 `test_l1_agent_read_fields.py`） |
| golden fixture | **32 → 32**（不变） |
| 被修的数据 | **1 份**（`agents/airoot.json`：34 条 lane 各加一个键、2 条 lane 的 `notes`、`honesty` 加一段） |
| schema / 代码 | **一个字节没动**（没有为了"让声明成立"去改契约） |
| `read` 路径 | **139 条不变**（只是多了一个键） |
| 语料重生 | **不需要** |

### 94.7 如实记录的边界

1. **`document_schema` 是"整份文档"的粒度，不是"每个字段"的。** 一条 lane 声明 `null`，它的**每一个**读路径都落在没有契约的地方；声明了 schema，也**只保证**读路径是该 schema 的顶层属性，**不保证**每个路径的类型/枚举都被 schema 钉死（那由 claim 1 与字段取值表管，是另一条线）。
2. **"哪些 schema 接受这份文档"是行为判据，会随输出变化。** 这是**有意**的：只要有人给这些报告加一个字段，声明就得重新量。代价是**改输出的那一轮会多一次红**，收益是"声明与实测不一致"这件事**不可能悄悄发生**。
3. **`null` 的 29 条里，有些读的是"报告里嵌着被 schema 描述的东西"**：`tool pin` 的报告里有一个 `plan` 字段（自校验过），`repair` 的报告里有 `transaction` 与实例。这一轮**没有**给这种"嵌套"发明一个粒度——它要么是 `null`，要么等一条报告 schema。**这是当前口径的已知损失**：读者知道"整份没有契约"，但不知道"里面哪一部分有"。
4. **没有新增任何 schema，也没有为这 29 条做报告 schema。** 契约变更最小化（ADR-0025 的教义 7）：要覆盖它们，正确做法是**一条**通用的"报告信封" schema（而不是 29 条），而那是一次**契约设计**，不是这一轮能顺手做的。它现在是这份清单唯一的解锁词。
5. **`search` 那条 lane 的两个 schema 顶层重名**（`status`/`data`/`reason_code` 在 `search-response` 与 `extension-envelope` 里都有）——行为判据把它判给了唯一**接受**这份文档的那个。这提醒：**顶层字段名不是 schema 的指纹**，§92 的"名字碰撞"在这里有第二个实例（同一个词出现在两份 schema 的顶层）。
6. **这一轮没有量 agent 面的其余两侧**：`SKILL.md` 的命令地图与 `agents/airoot.json` 的 lane 早已三方对账（§79/§81），但**"lane 教 agent 问的问题"与"这条命令真的能回答它"**没有独立的判据——`question` 仍是散文。

### 94.8 实施顺序

1. 先**静态**推导（处理函数沿调用图找 `validate_self`），拿到一份候选；
2. 立刻用**行为**判据去验它——**当场否掉三条**（这一步是这一轮的重点：静态推导"看起来对"，而它按字母序挑 schema、并把"可达"当成"读到"）；
3. 逐条看被否掉的那三条到底打印了什么：两条是"**已校验的计划 + 三个报告键**"，一条是"顶层重名挑错了"；
4. 判定那三个键**不该**进计划契约（可批准物是文件、hash 覆盖的是它），于是 `null` + `notes`，并把 §7 的句子改精确；
5. 写守卫：主检查并入"真跑一遍"的循环，另加双向合成检查与散文数字绑定；
6. 用**真实错误声明**验红，再补合成方向；
7. 回写计数（`AGENTS.md`：3 处计数 + §7 一句；审查报告 2 处）；跑全量 + 旧切片 + 真机验收；提交。
## 95. 第 95 阶段：`search` 的覆盖率那一格，和它自带的一个"数量陷阱"

### 95.1 这一阶段要解决什么

§89 给 `search-response.status` 立了一条覆盖率规则：**每个取值要么有 fixture，要么在字段取值表里带 †**。它当时在记录的边界里写下了两件没做的事，其中一件是：

> **`search` 的 `freshness.state`（`current`/`stale`/`degraded`/`rebuilding`/`unknown`）与 `coverage`（`complete_for_roots`/`partial`/`none`）也有枚举，这一轮没有给它们做覆盖率检查**——它们是**子字段**……要不要逐个覆盖，是一次**新的判断**，不该顺手加。

这一轮做那次判断，并按 §89 的同一个形状把它做完。

### 95.2 实测：`coverage` 的三个取值里有一个没有语料

把语料里每份 `search_*` fixture 的 `data.freshness.coverage` 列出来：

| 取值 | 有 fixture 吗 | 谁写它 |
|---|---|---|
| `none` | ✅ 两条（`search_response` 的 crawl 回落、`search_timeout_response`） | crawl 路径（没有索引可谈覆盖率） |
| `complete_for_roots` | ✅ 两条（`search_index_response`、`search_stale_index_response`） | 索引路径，**构建没有被截断** |
| **`partial`** | **❌ 一条都没有** | 索引路径，**构建被截断或超时** |

`partial` 不是纸上取值：`caps/searchindex.py` 的 `IndexState.coverage` 就是
`"complete_for_roots" if not (self.truncated or self.timed_out) else "partial"`，而它进的是**响应**（`freshness_for` 把它放回信封），所以它是 agent 真的会看到的一个结论。**顺带发现同一格里的第二个空白**：那条路径的 `reason_code` 是 `SEARCH_INDEX_DEGRADED`，这个码**没有任何 fixture 覆盖**——§14 那条"记录的退出码必须等于它自己文档推出的那个"从没在它身上跑过。

### 95.3 怎么把它做出来：边界是数据，所以可以注入

`index` 的记录上限来自 policy（`index.max_records`，回落到 `crawl.max_records`），而 `execute_search` 与 `build_index` 都收 `policy=`。于是**把上限设成 1** 就能确定性地造出"索引只覆盖了一部分"：构建停在第一条记录、标记 `truncated=true`，随后的查询读这份不完整的索引。**不是去造 25 万条记录，是把判据说小。**

产出的 fixture 正好是契约描述的那种结果：

```json
"status": "degraded", "reason_code": "SEARCH_INDEX_DEGRADED",
"data": {"freshness": {"coverage": "partial", "state": "current"},
         "next_cursor": null, "stats": {"index_records_examined": 1}}
```

`next_cursor` 是 `null`，理由与 `timed_out` 那条一样（`search.py:947`）：**一份不完整的清单不给出游标**，否则第二页会被当成完整答案的第一页。`warnings` 里带着那句话。

**它放在 `search_timeout_response` 之后是有意的**（§89 的教训）：`execute_search` 会读共享的 `FakeClock`，多插一次调用会把后面每一次调用都推后。重生成之后 diff 只有 `index.json` 加新文件——**既有 32 个 fixture 逐字节不变**。

### 95.4 做了什么

1. **补 `search_truncated_index_response` fixture**（语料 32 → 33），在 `SCHEMA_FOR_FIXTURE` 里注册为 `search-response`。
2. **把覆盖率守卫从"一个字段"推广成"一类字段"**：`SEARCH_RESULT_FIELDS` 是"**结论型**、有枚举、且落在 fixture 里"的字段表——`status` 与 `data.freshness.coverage`。取值与例外**都**从 `references/field-values.md` 读（那张表自己由另一组守卫双向钉住），所以取值丢了 † 就必须补 fixture，反之亦然。
3. **两条守卫各自的双向验红都补上**：有 † 的字段用"去掉 † 就报出来"验，**没有 † 的字段**（`coverage` 没有 †）改用"空语料必须报出全部取值"验——原来那句 `daggers` 变异对没有 † 的字段是**恒真的**，等于什么都没测（这一点是跑出来的，见 95.6-2）。

### 95.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把 `search_truncated_index_response` 从语料里移走**（回到这一阶段发现的状态） | 红 | ✅ 红：`no search fixture reports data.freshness.coverage = partial` |
| 给枚举加一个没有 fixture 的取值（`wat`） | 红 | ✅ 红（两个字段各一次） |
| 空语料 | 红 | ✅ 红——每个取值都必须被报出来 |
| 去掉 `status` 的 † | 红 | ✅ 红（`error`/`cancelled` 语料里没有） |
| `coverage`（**没有 †**）也套那句 † 变异 | —— | ❌ **不红**，因为它本来就没有 †；这一条当场把新守卫自己的空转暴露出来（下面记着） |

### 95.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **852 → 853**（覆盖率守卫由 1 条变成 2 条参数化用例，净 +1） |
| 审计检查（`test_l0_consistency.py`） | **95 → 95**（见 95.7-1：这个数按**测试函数**数，不是 pytest 收集数） |
| golden fixture | **32 → 33**（`search_truncated_index_response`） |
| 语料重生 | 是；**既有 32 个 fixture 逐字节不变，只有 `index.json` 变** |
| schema / 代码 / policy | **一个字节没动**（注入的是**测试**构造的 policy 对象，不是改 policy 文件） |

### 95.7 如实记录的边界

1. **"审计检查 95 项"这个数是模块里的测试函数数，不是 pytest 收集数。** 这一轮把一条守卫变成两条参数化用例，pytest 收集数 95 → 96，而那个守卫要的数**仍然是 95**——我先把文档改成 96，守卫当场红了，改回 95 才绿。**这不是缺陷，是口径**：那个数说的是"有多少条常驻检查"，不是"有多少个用例"；但它值得写下来，因为下一个人会踩同一脚。
2. **新的"双向验红"里有一条本来是空转的。** 原来的写法是"把 † 全去掉，必须报出来"——对有 † 的字段成立，对**没有 †** 的字段（`coverage`）恒为真，**等于没测**。跑出来才发现，改成"按有没有 † 分两种验法"。**这是本轮最值得记的一条**：同一条变异套在不同字段上，可能一个是真检查、一个是装饰。
3. **覆盖率规则的适用范围仍然是"结论型、有枚举、落在 fixture 里"的字段。** `freshness.state`（5 个取值）**这一轮没有纳入**：它由 `state`（索引新鲜度）与请求的 `max_staleness_ms` 一起决定，取值与 `coverage` 正交，纳入它是**下一次判断**——不是顺手加（§89 的边界 1 还在）。
4. **`SEARCH_RESULT_FIELDS` 是**手写的**字段表，不是推出来的。** 它只有两项，且两项都要在字段取值表里有对应的行（守卫会 assert 那行存在），所以它不会静默过期；但"哪些字段属于这一类"仍然是判断，不是推导——**第三个这样的字段出现时，没有任何东西会提醒你加进去**。
5. **那条 fixture 的 `state=current`** 是"索引是最近建的"，与 `coverage=partial` 并不矛盾：**新鲜**与**完整**是两件事，这也正是两个字段要分开覆盖的原因。
6. **`SEARCH_INDEX_DEGRADED` 现在有 fixture 了，但"每个有写者的 reason code 都要有 fixture"这条规则我没有立**：注册的码有上百个，硬立会让语料变成一份码表。§75 那条是**反方向**的（注册了但没人写），两条不对称是有意的。

### 95.8 实施顺序

1. 读 §89 留下的边界，选"`coverage` 的覆盖率检查"这件它明确说"是一次新的判断"的事；
2. 先量语料：`coverage` 三个取值里 `partial` 一条 fixture 都没有（顺带发现 `SEARCH_INDEX_DEGRADED` 也没被任何 fixture 覆盖）；
3. 找**可注入的判据**——上限来自 policy，`execute_search`/`build_index` 都收 `policy=`，所以把 `index.max_records` 设成 1，不造数据；
4. 把新 fixture 放在 `search_timeout_response` **之后**，重生成后确认 diff 是外科式的（既有 32 个逐字节不变）；
5. 把守卫从"一个字段"推广成"一类字段"，取值与例外都从字段取值表读；
6. 逐个验红，**发现"去掉 †"这条变异对没有 † 的字段是装饰**，按有没有 † 分成两种验法；
7. 回写计数——并发现"审计检查数"是测试函数数（不是收集数），改回 95；跑全量 + 旧切片 + 真机验收；提交。
## 96. 第 96 阶段：把"哪些字段要查覆盖率"这件事，从手抄变成走一遍 schema

### 96.1 这一阶段要解决什么

§95 把 `search-response` 的覆盖率检查从 `status` 扩到 `data.freshness.coverage`，并在自己的边界里留下一条自认的弱点：

> **`SEARCH_RESULT_FIELDS` 是手写的字段表，不是推出来的。** 它只有两项……**第三个这样的字段出现时，没有任何东西会提醒你加进去**。

这一轮先修那句弱点，再看修完之后它抓到什么。

### 96.2 实测：走一遍 schema，五个枚举里三个取值既没有语料、也没有 †

判据换成**走一遍 `search-response` 的 `$defs`/`properties`/`items`，收集每一个 `enum`**（路径按字段取值表的写法生成，如 `data.results[].kind`）。五个枚举，逐个对语料（把每条 `search_*` fixture 按该路径取出所有取值）与字段取值表的 †：

| 枚举字段 | 语料里有 | 没有的 | 有 † 吗 |
|---|---|---|---|
| `status` | `ok` / `degraded` / `timed_out` | `error` / `cancelled` | ✅ 两个都 † |
| `data.freshness.state` | `current` / `stale` / `unknown` | `degraded` / `rebuilding` | ✅ 两个都 † |
| `data.freshness.coverage` | 三个全有（§95 补的） | —— | 没有 †（**全部有写者**） |
| **`data.results[].kind`** | `file` | **`directory`** | ❌ **没有 †** |
| **`data.results[].verification`** | `unverified` / `indexed` | **`verified` / `changed`** | ❌ **没有 †** |

**八个"没有 fixture"的取值里，五个有 † 可查、三个没有**——那三个就是这一轮要补的。它们躲过 §89/§95 的原因很具体：**每个都由请求里的另一个字段决定**。

- `results[].kind = directory`：目录**只有调用方要**才是结果（`include_directories`），而 policy 默认 `false`——所以其余每一份 fixture 里都没有目录；
- `results[].verification = verified` / `changed`：这两个是 `physical_verify` 的裁决。`verify_records` 对每个命中重新 `stat`，**变了、没了、换了类型**就标 `changed`——协议明说**不许把它粉饰成当前事实**（§6.3），所以它值得有自己的一份语料。

### 96.3 两条 fixture，覆盖那三个取值

1. **`search_directories_response`**：一棵含子目录的树 + `include_directories=True` 的 crawl → `kind=directory`、`size=null`、`attributes=["directory"]`。
2. **`search_physical_verify_response`**：建索引 → **改写其中一个文件** → 用 `consistency=physical_verify` 查一次 → 一份文档里同时给出 `changed`（尺寸与索引里记的不符）与 `verified`（另一个一动不动）。

**第二条的构造过程里否掉了一个想当然的做法。** 第一版是"建完索引把文件**删掉**"，结果 `changed` 没出现：`query_index` 在把索引记录交给分页之前会**重新探一次可访问性**（`searchindex.py:355`），删掉的文件在 `accessible_only` 下当场被丢掉，根本走不到裁决那一步。**"改内容"能到、"删文件"到不了**——这条差别是量的结果，不是想出来的；它同时说明"索引里的记录在查询时已经被挡过一层"，值得写下来。

### 96.4 做了什么

1. **补两份 fixture**（语料 33 → 35），在 `SCHEMA_FOR_FIXTURE` 里注册为 `search-response`。
2. **把 §95 手写的 `SEARCH_RESULT_FIELDS` 换成走 schema 推导**：`_search_response_enums()` 收集 schema 里**每一个** `enum`（`properties` 下钻、`items` 生成 `[]` 段），取值**从路径取出**（`_values_at` 按 `.` 与 `[]` 拆段），† 从 `references/field-values.md` 读。**加一个枚举、或给已有枚举加一个取值，都会自动多一个用例**——§95 记下的那句弱点就此关闭。
3. 覆盖率守卫从 2 个用例变成 **5 个**（parametrize 在推导出来的路径上），并加了一句"路径走法本身要能穿过列表"的自检（空转防守）。

### 96.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把两份新 fixture 都移走**（回到这一阶段发现的状态） | 红 | ✅ 红两条用例：`no search fixture reports data.results[].kind = directory` 与 `... verification = changed / verified` |
| 空语料 | 红 | ✅ 红——每个取值都必须被报出来 |
| 有 † 的字段去掉 † | 红 | ✅ 红（`status` 与 `freshness.state` 各一次） |
| 给 schema 的某个枚举加一个取值 | 红 | ✅ 红（字段取值表那组守卫先红在 claim 1；这一组随后要求它有 fixture） |
| 路径走法不穿列表 | 红 | ✅ 红（同一测试里的自检） |

### 96.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **853 → 856**（覆盖率守卫由 2 个用例变成 5 个） |
| 审计检查（`test_l0_consistency.py`） | **95 → 95**（不变，见 §95.7-1：这个数按**测试函数**数） |
| golden fixture | **33 → 35**（`search_directories_response` / `search_physical_verify_response`） |
| 语料重生 | 是；**既有 33 个 fixture 逐字节不变，只有 `index.json` 变** |
| schema / 代码 / policy | **一个字节没动**（补的是语料与守卫，不是实现） |

### 96.7 如实记录的边界

1. **走 schema 只覆盖 `search-response`。** 其他有枚举的文档（`where-response`、`doctor-response`、`registry-projection`、`plan`……）**没有**做同样的覆盖率检查——§88/§89 只给 `where`/`doctor`/`search` 的**结论字段**立了规则。要不要把这条推广到每一份 schema 是**下一次判断**，而且代价明显更大（`plan`/`reference-plan` 的枚举取值里有很多是 P4/P5 才写的，会需要一大批 †）。
2. **"有 fixture"是"取值在语料里出现过"，不是"这条分支被断言过"。** 一份 fixture 可以同时被两组守卫用（§87 的退出码、§88 的覆盖率），但**没有**任何东西保证举例的那个取值是被**独立**构造出来的——`search_physical_verify_response` 是个例外，它一次给两个取值，也正因为如此，**如果哪天 `verify_records` 只写其中一个，这条守卫不会察觉**。
3. **`changed` 的 fixture 依赖"索引里的记录没有被查询时刷新尺寸"。** 这是实现事实（`query_index` 用索引里存的 `size` 过滤、`verify_records` 才去 `stat`）。哪天有人让查询顺手刷新尺寸，**`changed` 就会从这条路消失**——那时这条守卫会红，因为语料里那个取值会跟着消失。这是设计好的红，不是脆。
4. **`freshness.state` 纳入检查后是绿的**（三个有写者的取值都有语料，两个没写者的有 †）。也就是说这一轮**没有**发现它有问题——**"查了但没事"和"没查"必须分得清**，所以它仍然算这一轮的产出：它从"没查"变成了"查过、当时是干净的"。
5. **`_values_at` 的路径方言与字段取值表是同一套**（`a.b[].c`）。这意味着**字段取值表里写不出路径的字段，这个守卫也覆盖不到**——比如数组下标之外的结构（`$defs` 里的嵌套 `$defs`）。今天需要覆盖的五个都在这一套里。
6. **两份新 fixture 都是合成的**，与其余 33 条一样：它们证"这两个取值能被产生、且形状是这样"，**不证**"真机上会以这种方式发生"。

### 96.8 实施顺序

1. 先读 §95 留下的那句弱点（"手写的字段表"），把它当成这一轮的第一件事；
2. 写一个**走 schema 的枚举收集器**，先把五个枚举与语料、† 三张表并排摆出来；
3. 发现三个"既没有 fixture 也没有 †"的取值，并逐个找出**它们各自被请求里的哪个字段决定**（这解释了它们为什么躲过了前两轮）；
4. 构造前先试最直觉的做法（删文件）——**量出来它到不了裁决**（索引查询会重探可访问性），改成"改内容"；
5. 两份 fixture 都放在搜索小节**末尾**（`execute_search` 读共享 `FakeClock`，§89 的教训），重生成后确认 diff 是外科式的；
6. 把守卫的手写表换成推导，加"路径走法穿列表"的自检；
7. 用**两份 fixture 都移走**验红（这是这一轮发现的真实状态），再补空语料与 † 两个方向；回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 97. 第 97 阶段：把上一轮的守卫往外推一次，量出"不能推"和"推不了"

### 97.1 这一阶段要解决什么

§96 把覆盖率规则的字段表从手抄换成走 schema，并留下一条边界：

> **走 schema 只覆盖 `search-response`。** 其他有枚举的文档……**没有**做同样的覆盖率检查……要不要把这条推广到每一份 schema 是**下一次判断**，而且代价明显更大。

这一轮做那次判断。判断的办法只有一个：**真的去量**。

### 97.2 实测：推广的代价是 32 个"没有 † 也没有 fixture"的取值——而其中大多数不是缺口

把 §96 的判据套到**每一份被打印的文档**（`SCHEMA_FOR_FIXTURE` 里那 9 个 schema 的全部枚举）上：

| schema | 值没有 fixture 的字段 | † 能解释几个 |
|---|---|---|
| `doctor-response` | `diagnostics[].remediation`：`inspect` / `repair`（`reapprove` 有 †） | ❌ 两个解释不了 |
| `extension-envelope` | `status`：`degraded` / `error` / `timed_out`（`cancelled` 有 †） | ❌ 三个解释不了 |
| `plan` | `operation`（6 个）、`operations[].kind`（`delete`） | 部分：`import_tool`/`recreate_runtime`/`root_relocate`/`rollback` 有 † |
| `reference-plan` | `exposure.scope`（`machine`）、`exposure.value_kind`（`REG_SZ`）、`operations[].target_scope`（`machine`） | ❌ 三个都没有 † |
| `registry-projection` | `external_references[].capability_kind`（`tool`）、`management`（三个） | `project_owned` 有 † |
| `transaction` | `state`：**16 个里 15 个** | ❌ 一个都没有 |
| `where-response` | `source`：`project` / `search`（有 †）、以及 **`null`** | ✅ 两个有 †，`null` 是另一回事 |
| `search-response` | 两个（都有 †，§96 已覆盖） | ✅ |

**合计：51 个取值没有 fixture，其中 32 个连 † 都没有。** 逐个看这 32 个，它们分成三类，而**没有一类是"漏了语料"**：

1. **词表由别的工件覆盖**：`transaction.state` 的 15 个状态——它们的覆盖物是 `transaction_transitions.json`（那张合法移动表，§45），不是响应语料。要求每个状态都有一份事务 fixture，等于把"转移合法"这件事重复证一遍。
2. **计划由别的命令产出**：`plan.operation` 的 `gc_apply` / `retire_tool` 确实有写者，但写它们的是 `tool gc` / `tool retire`，而**那条命令打印的是报告**（§94 量过：报告没有 schema）。所以"没有 fixture"是真的，原因是结构性的。
3. **`null` 不是取值**：`where-response.source` 的枚举里有 JSON `null`（`"type": ["string","null"]`）。**语料永远报不出它**。

于是这一轮的判断是：**不推广。** 理由不是"太麻烦"，而是 ADR-0021 明确点名的那种情形——**机械推广会摧毁一条更强的规则**：要让 32 个变绿，要么给它们打 32 个 †（其中大多数是假话，那些取值**真的**有写者），要么为已经被别的工件覆盖的东西造一批 fixture（重复证明，还会让"语料 = 一次结果"这件事失去意义）。

**规则仍然只适用于 `search-response`，但理由是判据而不是偏好**：这条规则适合"枚举是**关于一次结果的结论**"的 schema（`status`、`freshness.*`、`results[].verification`），因为**一份 fixture 就是一次结果**，一个取值一份例子正是语料的用途。像 `transaction.state` 那样是**词汇表**的，覆盖物是拥有那份词汇表的工件（`transaction_transitions.json`）或字段取值表的 †。

判断写进了代码：`COVERAGE_BY_FIXTURE_SCHEMA = "search-response"` 连同上面这段判据就在它旁边——**范围是命名的一处，不是散在判据里的**。

### 97.3 顺带抓到一处缺陷：那个"`null`"会变成一个永远红的假警报

量 `where-response.source` 的时候发现的：§96 的走法写的是

```python
found[prefix] = [str(item) for item in node["enum"]]
```

JSON 的 `null` 过一遍 `str()` 就变成 Python 的字符串 `"None"`。**语料永远报不出 `"None"`**（一个字段要么是字符串、要么是 `null`，不会是 `"None"` 这个字符串），所以哪天真把规则推到 `where-response`，`source` 这一格会**永远红**——一个假警报，而且看起来像"语料缺一个例子"。

它今天不响，只是因为 `search-response` 的枚举里没有 `null`。**这是我上一轮刚交付的代码里的缺陷**，而且是被"试着往外推一次"逼出来的。修法两条，都用项目已有的约定：

1. **拼写**：按字段取值表的写法把 `null` 写成 `"null"`（`UNTESTABLE = {"null"}` 就是那套约定）。
2. **不索要**：`null` **不是语料能证的东西**（写它是 `None` 而不是字符串字面量），所以覆盖率判据**跳过它**——与取值表自己那组守卫的判断完全一致。跳过的是**从 `test_l1_field_values` 读来的 `UNTESTABLE`**，不是这一轮新写的名单。

### 97.4 做了什么

1. **把走法拆成纯函数** `_enums_in(schema)` + 一层 `_search_response_enums()`，后者读 `COVERAGE_BY_FIXTURE_SCHEMA`；判据与范围写在那个常量旁边。
2. **修 `null` 的两处**：`_enum_spelling()`（`None` → `"null"`）与覆盖率判据里的 `- set(UNTESTABLE)`。
3. **新增一条测试**（`test_a_schema_permitted_null_is_never_demanded_from_the_corpus`）：**在合成 schema 上**验两件事——`null` 拼成 `"null"`、且空语料**不**索要它；同时断言 `"null" in UNTESTABLE`（那条例外的所有权仍在字段取值表里）与路径走法能穿过 `items`。
4. 空语料那一条变异的期望值也跟着减去 `UNTESTABLE`——否则它会要求一个语料不可能报出的值。

### 97.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把 `_enum_spelling` 改回 `str(item)`**（§96 的写法） | 红 | ✅ 红：`{'nested[]': ['only'], 'source': ['registry', 'path', 'None']}` |
| 把 `- set(UNTESTABLE)` 去掉 | 红 | ✅ 红（合成用例会索要 `null`） |
| 各字段原有的四个方向（漏取值 / 空语料 / † 去掉 / 有 † 的字段） | 红 | ✅ 红（沿用 §96） |

**第一个变异是上一轮的真实代码**，不是这一轮新造的：把它放回去，新用例当场指名 `'None'`。这正是"只有把规则往外推，才知道它哪里不许推"的实例——**它今天不响，不是因为它是对的，而是因为被检查的 schema 恰好没有那一格。**

### 97.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **856 → 857**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **95 → 96**（多了一个测试函数） |
| golden fixture | **35 → 35**（不变——这一轮**没有**造新语料，判断就是不推广） |
| 被修的代码 | **1 处**（覆盖率走法：`null` 的拼写与"不索要"） |
| schema / 语料 / policy | **一个字节没动** |

### 97.7 如实记录的边界

1. **"不推广"是一个判断，它今天由一段散文守着，没有守卫。** 没有任何机械检查会阻止下一个人把 `COVERAGE_BY_FIXTURE_SCHEMA` 改成别的 schema——**改了就会红 32 处**，所以它会立刻被看见，但**看见的是红，不是"你为什么改这个"**。这是有意接受的：一条"这个常量只许是 X"的守卫就是一份手抄名单，而这里真正的保护是那 32 处红 + 这段理由。
2. **那 32 个取值里，有几个可能真是缺口**，我**没有**逐个判死。最可疑的是 `reference-plan.exposure.scope = machine`（有写者、没有 †、没有 fixture：`machine` 级持久化要提权，`--scope machine` 现在报 `PRIVILEGE_REQUIRED`，所以**它可能确实只在 P2 才写得出来**——那就该打 †，而不是造 fixture）。**这一轮没有改它**：逐个判定 32 个取值是**一次独立的审计**，混进"要不要推广规则"这一轮会让两件事都做不干净。
3. **`null` 的例外现在有两处**（取值表的 `UNTESTABLE`、覆盖率判据里的 `- set(UNTESTABLE)`），但它们**是同一个来源**（判据从字段取值表读），所以不会漂移；合成用例里那句 `assert NULL_SPELLING in UNTESTABLE` 就是防它变成第二份名单。
4. **`_enums_in` 仍然只认 `properties` / `items` / `enum` 三种形状。** `oneOf` / `allOf` 里的枚举、`$defs` 里没被 `$ref` 到的枚举**都收不到**。今天 `search-response` 没有这些形状（所以 §96 的结论仍然成立），但规则本身**看不出"有枚举没收全"**——下一次有人用 `oneOf` 写枚举，这一组会**静默少一个字段**。
5. **合成用例证明的是走法，不是真实 schema。** 真实被检查的文档恰好没有 `null` 枚举——这正是缺陷活过一轮的原因；**用合成输入补救，等于承认"在没有真实例子的地方，这条守卫只能靠构造的输入"**。

### 97.8 实施顺序

1. 先把 §96 的判据**真的套到每一份被打印的文档**上（而不是先决定要不要套）——量出 51 / 32 这两组数；
2. 把 32 个逐个归到三类原因里（别的工件覆盖 / 别的命令产出 / `null` 不是取值），确认**没有一类是"漏了语料"**；
3. 于是这一轮的产出从"补 fixture"变成"**写下判据、限定范围**"，并把范围命名为一个常量放在判据旁边；
4. 量 `where-response.source` 时发现 `str(item)` 把 `null` 变成 `"None"` ⇒ 上一轮的缺陷；
5. 修两处（拼写 + 不索要），例外**从字段取值表读**，不新开一份名单；
6. 用**上一轮的真实写法**验红（把它改回 `str(item)`），再补"去掉 `UNTESTABLE` 跳过"这个方向；
7. 回写计数（测试 +1、审计检查 +1）；跑全量 + 旧切片 + 真机验收；提交。
## 98. 第 98 阶段：给自己立的那条"每个守卫都要能变红"的规则，量一遍

### 98.1 这一阶段要解决什么

§95 在边界里记过一条：那组覆盖率守卫的两个变异里，**有一个是装饰**（对没有 † 的字段套"去掉 †"的变异恒为真）。它说明一件事：**一个守卫有没有验红方向，不能靠"我写过变异"来保证**——得去数。

于是这一轮把自己立的规矩量一遍：

> **审计模块里的每一条常驻检查，都要有一个"它真的会报东西"的证明。**
> 一个只被 `assert helper(...) == []` 调用过的 helper，**从来没有被证明能报出任何东西**——它在真文件上的沉默，因此什么都不说明。

### 98.2 实测：21 个 `_*_problems` 里，5 个"没有验红方向"——而其中 4 个是探测器的假警报

做法是机械的：挑出模块里所有 `_*_problems` 形态的 helper，看每个的调用点上有没有一次**不是**那句"主断言"（`== []`）的使用。结果：

| helper | 调用点 | 判为"有验红方向" |
|---|---|---|
| `_stage_chain_problems` | 3 | ❌ |
| `_byte_contract_problems` | 3 | ❌ |
| `_documented_option_problems` | 1 | ❌ |
| `_documented_prohibition_problems` | 2 | ❌ |
| `_prohibition_problems` | 7 | ❌ |
| 其余 16 个 | —— | ✅ |

**逐个读过之后，4 个是假警报**，而它们**假在同一件事**上——变异没有写在调用点上，而写在一个**本地包装器**里：

- `_stage_chain_problems` / `_byte_contract_problems`：测试里有一个 `def problems(mutated)` 或 `def problems(files=..., attributes=...)` 的**局部包装**，十几个变异都走它。探测器只看 `assert helper(...)`，于是看不见。
- `_prohibition_problems`：变异把结果先收进 `drift = [helper(...)]` 再断言 `drift[0]`——同样看不见。
- `_documented_prohibition_problems`：它是个**薄装载器**（读 SKILL.md 与 `agents/airoot.json` 再交给 `_prohibition_problems`），而后者已经被五个失败模式验证过。

**只有第 3 个是真的**：`_documented_option_problems` **一个变异都没有**。它的 docstring 写着"`--max-staleness-ms` 改名成 `--staleness-ms` 会让 AGENTS.md 撒谎，而且是静默的"——而**测试里没有任何东西证明它会发现**。更麻烦的是它**根本没法被变异**：它把真文件读在自己肚子里（`AGENT_OPERATIONAL_DOCS`、`AGENT_META`），没有参数，所以想喂它一份假文档都做不到。

**"不能变红"和"不能被喂"是同一件事的两面**——这才是这一轮真正的发现：一个守卫没有验红方向的常见原因不是忘了写变异，而是**它的输入不可注入**。

### 98.3 做了什么

1. **把判据从肚子里挪到参数上**：`_option_problems(documents, meta_commands)` 收"（文件名, 行）列表"与"lane 命令列表"；`_documented_option_problems()` 变成**只负责读真文件**的薄装载器（并把原来那句 `assert len(AGENT_OPERATIONAL_DOCS) >= 3` 的空转防守搬进来）。**parser 仍然直接读**——它才是权威，不该被注入。
2. **新增 `test_the_option_check_reports_each_way_it_can_fail`**：三种失败方式，每种一份合成输入——动词不接受的长选项、**属于另一个动词**的选项（改名而不是删除）、以及同一种错从机器可读 lane 列表进来；再加两条反向断言（真选项不许被报、真文件仍是干净的）。

### 98.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把 `unknown` 恒置为空**（"这个检查接受一切"） | 红 | ✅ 红：新用例指名 |
| 合成文档里放动词不认识的长选项 | 红 | ✅ 红 |
| 合成文档里放**别的动词**的选项（`--class`） | 红 | ✅ 红 |
| 机器可读 lane 列表里放不认识的选项 | 红 | ✅ 红 |
| 真选项（`--json`）与真文件 | 绿 | ✅ 绿 |

**写这三个变异的过程本身又教了两件事**，而两件事都说明"这条路径从没被人手喂过"：

1. 第一版"改名"变异用的是 `airoot search --root`，**结果它是干净的**——因为 `--root` 是**每个子命令都继承**的（`parents=[common]`），它不是外来选项。真正的外来选项是别的动词的，比如 `--class`。**"这个选项存在"与"这个选项在这个动词上是这个意思"是两件事**，而这条守卫管的是前者；AGENTS.md §3 记的那条 `search --root` 陷阱就不归它管（那是文档措辞问题）。这条差别写进了用例的注释。
2. 第二版"lane 列表"变异用的是 `airoot tool list --teleport`，**也是干净的**——因为 lane 的 `command` 字段**不带 `airoot` 前缀**（文档窗口那一路是在 `_invocation_windows` 里把它切掉的）。**两条路喂进来的形状不同**，而这一点只有真的喂一次才知道。

两个都是"守卫本身没问题、探针写错了"，也正因为如此，它们恰好证明**这条路径此前没有任何人手工喂过**。

### 98.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **857 → 858**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **96 → 97**（多了一个测试函数） |
| golden fixture | **35 → 35**（不变） |
| 被修的代码 | **1 处**（`_documented_option_problems` 拆成"可注入的判据 + 薄装载器"） |
| schema / 语料 / policy | **一个字节没动** |

### 98.6 如实记录的边界

1. **探测器只认 `_*_problems` 这个名字形态。** 不叫这个名字的检查（比如那些直接 `assert` 在一段内联逻辑上的测试）**根本没进这份清单**——它们里可能也有从没变红过的。这一轮没有换更聪明的探测方式（那需要判断"哪些断言是主断言、哪些是变异"，本质上要靠读），**这一条记在这里**：清单的完整性只有"名字形态"这一层保证。
2. **探测器的假警报率是 4/5。** 它看不见本地包装器、看不见先收进变量再断言、看不见"薄装载器 + 已被验证的内核"。**它做的是筛选，不是结论**——这一轮的用法（筛出 5 个、逐个读、只修 1 个）才是它的正确用法，而不是把它做成守卫。（做成守卫就会要求所有人按它的形状写变异，那是让守卫规定代码长相。）
3. **`_documented_prohibition_problems` 仍然没有自己的验红方向。** 我判定"不必补"，理由是它的内核 `_prohibition_problems` 有 5 个失败模式、而它自己只做"读文件 + 转发"。**但这是一次判断，不是一次证明**：如果它读错了文件（比如读了 `SKILL.md` 却该读别处），那条错误**不会**被它的验证覆盖。同类薄装载器在这个模块里还有几个，判定标准相同——**记下来，免得下次以为是查过了**。
4. **新增用例断言的是一串精确消息**（`"fake.md:1: airoot search ... --teleport"`）。这比 `!= []` 强（它同时钉住了文件名、行号与动词归属），代价是消息格式一改就要跟着改。**这是有意的**：这条守卫的全部价值就在于"它指得出是哪一份文档的哪一行"。
5. **`build_parser()` 仍然直接读真 parser**，所以这三个变异证的是"文档侧能报错"，不是"parser 读错了会给假绿"——后者由模块里另外几组守卫（动词/选项/命令地图三方对账）负责，它们各自有自己的验红方向。

### 98.7 实施顺序

1. 把"每条守卫都要能变红"这条自己立的规矩**变成一次可执行的筛选**（21 个 helper、5 个候选）；
2. **逐个读那 5 个**——4 个是假警报，且假在同一个原因（变异写在本地包装器里）；
3. 找到唯一真的那个，并发现它**不能被变异**（输入在肚子里）⇒ 这才是"没有验红方向"的根因；
4. 把判据改成可注入，装载器留薄，空转防守留在装载器里；
5. 写三种失败方式 + 两条反向断言；**写的路上两次探针写错**，两次都是"这条路径没人喂过"的证据；
6. 用"接受一切"这个实现变异验红（它对真文件仍然绿、只对新用例红——正是"以前是空转"的形状）；
7. 回写计数（测试 +1、审计检查 +1）；跑全量 + 旧切片 + 真机验收；提交。
## 99. 第 99 阶段：那两条"读路径"其实一直被空列表挡着

### 99.1 这一阶段要解决什么

§94 给每条 lane 加了 `document_schema`，顺手量了"被钉住的 lane 读的字段是不是都在契约里"——**量出来是 0 个越界**（5 条 lane、29 条读路径，全部落在声明的 schema 里）。一个干净的结果。**于是往旁边再看一步**：那些读路径是**真的被检查了**，还是只是"看起来解析成功"？

### 99.2 实测：`unresolved()` 把空列表算作"已解析"，于是 `repair` 的两个读路径从来没被看

`test_l1_agent_read_fields.py` 的解析器有一条**写在注释里的有意设计**：

> An `[]` segment over an **empty** list counts as resolved: there is nothing to check, and a healthy `doctor` legitimately has no diagnostics to carry `diagnostics[].code`.

这对 `doctor` 是对的（健康根就是没有 diagnostics，而"健康时不该有诊断"本身是个结论）。但它同时意味着：**一个 `x[].y` 的读路径，如果这次场景里 `x` 是空的，那么 `y` 这个名字从来没有被核对过**——把 `y` 改名，守卫照样绿，因为没有一行可看。

给每条 lane 算一遍"哪些读路径是**穿过空列表**解析的"：

| lane | 空穿的读路径 |
|---|---|
| **`repair`** | **`repaired[].action`、`repaired[].state`** |
| 其余 33 条 | 无 |

只有一条，而且它正是**最不容易被注意到的那条**：这条测试的根里没有什么需要修的东西，所以 `repair` 返回 `repaired: []`，一行都没有。**agent 被告知要读 `repaired[].state`，而"这个字段存在"这件事从来没有被真正检查过。**

（顺带确认了 `doctor` 那三条**不是**空穿的：这个场景的根本身是 degraded 的，`diagnostics` 非空——所以那里的检查是实的。**这正是"量一遍"和"看着像"的区别。**）

### 99.3 做了什么

1. **把空穿判断从探针变成判据**：新增 `vacuous_paths(document, paths)`（与 `unresolved` 并列，注释里说明它与 `unresolved` 是**一对**：一个问"在不在"，一个问"有没有东西可看"），并接进那条"逐条 lane 真跑一遍"的循环——**空穿就是一个 problem**，消息里说明"这个 lane 需要一个能产出非空列表的场景"。
2. **给 `repair` 造出可修的东西**：在 `repair` 那条 lane 之前，注入一次故障停下来的提交——`FaultInjector(stop_after="ACTIVE_BOUND")`（与 `test_e2e_p1.py` 里同一个做法），于是事务停在需要恢复的状态，`repair` 报出 `repaired[0].state == "FINALIZED"`，两条读路径**第一次被真的看不到**……被真的看到了。
3. **给新 helper 一条自己的用例**：空列表要报、非空不报、没有列表段的不报、嵌套的 drain 要报、以及**"被 drain 的列表"与"叶子值是空列表"是两件事**（`reports[].candidates` 读出一个空列表是**合法答案**，不是空穿）——最后这条是我第一版用例写错了、被 helper 纠正过来的，写进用例的注释里。

### 99.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| **把那两次注入拿掉**（回到这一阶段发现的状态） | 红 | ✅ 红：`repair: exit 0 makes ['repaired[].action', 'repaired[].state'] vacuous — the list is empty, so a renamed key would pass; give this lane a scenario that produces one` |
| helper 自己的六个方向（空/非空/无列表/嵌套/叶子空列表/文档里根本没有） | —— | ✅ 全绿（写错的期望当场被纠正） |
| 其余 33 条 lane | 绿 | ✅ 绿（它们本来就没有空穿） |

**第一个变异就是把这一阶段发现的状态放回去**——它以前是**绿**的，因为当时没有这条判据。

### 99.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **858 → 859**（新增 1 条 helper 用例） |
| 审计检查（`test_l0_consistency.py`） | **97 → 97**（不变：新用例与主检查都在 `test_l1_agent_read_fields.py`） |
| golden fixture | **35 → 35**（不变） |
| 被修的场景 | **1 处**（读契约测试里给 `repair` 造一个待恢复事务） |
| 新增判据 | **1 个**（`vacuous_paths`，接进既有循环） |
| schema / 语料 / 输出 | **一个字节没动** |

### 99.6 如实记录的边界

1. **空穿判据只覆盖"读路径里的 `[]` 段"。** 一个读路径如果在**非空**但缺字段的行上解析失败，那是 `unresolved` 的事（已覆盖）；一个读路径如果指向**嵌套一层以上的列表**（`a[].b[].c`）也能被抓到，但**只抓"某一级是空的"**，不抓"某些行有这个字段、某些行没有"——**部分覆盖**（比如三行里只有一行带该字段）**这一条看不出来**。这是判据的粒度，不是 bug，但值得写清楚。
2. **判据要求"场景里非空"，不要求"覆盖所有分支"。** `repair` 现在有一条待恢复事务，于是 `repaired[].state` 被核对；但 `state` 在这一场景里只取到 `FINALIZED` 一个值（另一种结局是回滚）。**"字段名被核对"与"每个取值都被举例"是两件事**——后者归 §96 那组覆盖率守卫管，而它只覆盖 `search-response`（§97 量过为什么不推广）。
3. **`vacuous_paths` 是这一轮新写的，它的正确性只由那六个合成断言保证。** 真实语料里它只命中过一次（`repair`），所以它在真实输入上的行为**样本极少**。
4. **给 `repair` 造场景时用了 `conftest.FaultInjector`**（函数内 import，与 `test_e2e_p1.py` 同一做法）。这让这条测试依赖 `conftest` 的夹具类而不只是一堆 fixture——**已经存在的依赖**（这个模块本来就 import `fake_issuer`），但多一处就多一处。
5. **这一轮没有回头去检查别的"把空集合算作已解析"的地方。** 同一个模式在别处也可能存在（比如 `documents`/`catalogue` 一类空集合的守卫）。**这一轮只在 agent 读契约这一处量过**——记在这里，因为它是一个**模式**，不是一个实例。

### 99.7 实施顺序

1. 先量"被钉住的 lane 读的字段是不是都在契约里"——**0 个越界**，于是往旁边看一步；
2. 把"空列表算已解析"这条**写在注释里的有意设计**当成嫌疑：给每条 lane 算空穿；
3. 命中唯一一条（`repair`），并确认 `doctor` 那三条**不是**空穿（场景本来就有诊断）；
4. 判断修哪一侧：**修场景**（造出可修的东西）而不是放宽判据——因为"agent 要读 `x[].y`"这句话本身就要求有一个非空的 `x`；
5. 把探针写成判据（`vacuous_paths` + 接进循环），并给它自己的用例；写用例时**第一版期望写错了**，被 helper 纠正，改完把那条区别写进注释；
6. 用**真实状态**验红（把注入拿掉）；
7. 回写计数；跑全量 + 旧切片 + 真机验收；提交。
### 99.8 顺带修掉的：一个把 `"99"` 写死的探针

把阶段范围从 `§31–§98` 推到 `§31–§99` 之后，`test_the_repo_map_agrees_with_the_sentence_that_delegates_the_stage_records` **红了**——红在它自己的第三个变异上：

```python
assert _delegation_problems(rows, "31", "99", target) == ["the row does not state §99"]
```

这个变异的用意是"给一个仓库地图行没有声明的范围"，而它把那个范围**写死成 `99`**。在 §99 存在之前，`99` 恰好落在行的范围之外，所以它会红；**§99 一存在，行里就合法地含有 `§99`**，这个变异于是变成绿的——**红的理由与它想证明的东西不再是同一个**。

这正是这个项目反复踩到的那一类（§84 的链尾字面量、§85 的文件行数、§90 的 +4 位移）：**探针锚在一个"下一个阶段会挪动"的字面量上**。修法与前面几次相同——**从工件推**：

```python
beyond = str(int(high) + 1)   # high 从 §1 那句委派里读出来
assert _delegation_problems(rows, low, beyond, target) == [f"the row does not state §{beyond}"]
```

**这一条是"这一轮把范围推进一格"才暴露出来的**：如果这一轮只写 §99 的记录而不动范围，它不会响。**第四次的同一类错误，而且这一次是守卫自己的探针**——写在这里，因为下一个人推进范围时会遇到完全一样的一格。
## 100. 第 100 阶段：29 条 lane 的"报告"，能不能被一份 schema 钉住

### 100.1 这一阶段要解决什么

§94 量出 **34 条 agent lane 里只有 5 条读的文档被已发布 schema 描述**，其余 29 条读的是 CLI 自己的报告面，并在记录里写下了它的**解锁词**：

> 要覆盖它们，正确做法是**一条**通用的"报告信封" schema（而不是 29 条），而那是一次**契约设计**。

这一轮做那次设计——**先量，再决定**。

### 100.2 实测：29 条 lane，**29 个互不相同**的顶层形状

把每条未钉住的 lane 真跑一遍，取它打印文档的顶层键集合：

| 侧 | 数量 |
|---|---|
| 未钉住的 lane | **29** |
| **互不相同的顶层形状** | **29** |
| **所有 29 份文档共有的键** | **恰好两个：`schema_version`、`reason_code`** |

**也就是说"一条信封 schema"这个解锁词不存在。** 一份 `additionalProperties: false` 的信封要么拒掉全部 29 份（每条的字段都不一样），要么就得写成 `additionalProperties: true`——而那是**一份"看起来像契约"的契约**：它接受任何多余字段，钉住的只有两个键。教义第 1 条（不新增"看起来安全"的能力）正是为这种东西写的；另一条路是**29 份报告 schema**，那是契约变更最大化，与教义第 7 条直接冲突。

所以裁定是：**两个都不做**，`document_schema: null` 是这一版的诚实状态。但把**理由**写进读者会看的地方——`agents/airoot.json` 的 `document_schema_note` 现在不只说"没有 schema 描述它"，而是说**为什么一份也没有**（29 个形状 / 一份宽信封等于没有信封 / 29 份是契约变更），这样下一个人不必重新推一遍。

### 100.3 但"共有两个键"本身是一件可以被钉住的事

量出交叉集是 `{schema_version, reason_code}` 之后，它就从"观察"变成了**可以直接守的性质**：

- 任何一份报告**丢掉**这两个键之一 ⇒ 红（一个不带 `reason_code` 的报告面，agent 读不出为什么）；
- **第三个键变成人人都有** ⇒ 红（那时"信封"的定义变了，而 `document_schema_note` 里那句话就成了假话）；
- 产出少于 20 份 ⇒ 红（人口太少的交叉集什么都说明不了）。

判据与两个键的表写在一起（`REPORT_ENVELOPE_KEYS`），注释里写明它是**哪一次测量**推出来的、以及为什么只钉这两个——**"能钉的钉住，不能钉的说清楚"**，而不是"要么全钉要么不提"。

### 100.4 做了什么

1. **量了三件事**：未钉住 lane 数（29）、互不相同的形状数（**29**）、共有键（**两个**）。
2. **把理由写进 `document_schema_note`**：从"没有 schema 描述它"改成"**为什么**一份也没有"。
3. **新增判据** `report_envelope_problems(documents, declared)`（纯函数，双向 + 人口下限）接进那条"逐条 lane 真跑一遍"的循环，并用真实语料跑通——**它当场确认了交叉集就是那两项**。
4. **新增一条合成用例**（4 个方向：人口不足 / 干净 / 多一个共有键 / 少一个共有键）。写的时候第一版合成语料**把每个报告都塞了同一个键**，于是"多一个共有键"那条把干净用例也判红了——**这正是判据在干活**，改成每条一个不同的键。
5. 回写计数。

### 100.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 人口 < 20（合成两份） | 红 | ✅ 红 |
| 全部报告共有一个未申报的第三个键 | 红 | ✅ 红：`extra is carried by every report but is not declared` |
| 全部报告都缺 `reason_code` | 红 | ✅ 红：`reason_code is declared as an envelope key but not every report carries it` |
| 真实 29 份报告 | 绿 | ✅ 绿（交叉集恰好两项） |

### 100.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **859 → 860**（新增 1 条） |
| 审计检查（`test_l0_consistency.py`） | **97 → 97**（不变：都在 `test_l1_agent_read_fields.py`） |
| golden fixture | **35 → 35**（不变） |
| 被修的文档 | **1 处**（`agents/airoot.json` 的 `document_schema_note` 补上理由） |
| schema / 语料 / 实现 | **一个字节没动** |
| 新增判据 | **1 个**（`report_envelope_problems`） |

### 100.7 如实记录的边界

1. **这条判据只钉"顶层键的交叉集"，不钉任何字段的类型或取值。** 两份文档共有 `reason_code` 这个**名字**，不代表这个键在两处**同型**——`reason_code` 可以是 `null` 也可以是字符串（`where-response` 的 schema 就写成 `["string","null"]`），而这条判据对此一无所知。它与 §96 的覆盖率规则是**两条不同的线**。
2. **"29 个互不相同的形状"是这一版的事实，不是设计。** 两条报告的字段哪天正好收敛成一样的，这个数就变小——那时"一份信封不够"的**理由**也就弱了一分。判据不会因此变红（它只查交叉集），**所以这句话需要人来复核**，而不是靠守卫。
3. **交叉集恰好是 `{schema_version, reason_code}` 这件事，是这份判据的前提而不是结论。** 如果哪天第三项变成人人都有，判据会红——但**红的处理方式**有两选（改 `REPORT_ENVELOPE_KEYS` 并改那句话，或者去查为什么多出来一个键），守卫选不了，得人来选。
4. **这一轮没有为任何一条 lane 造 fixture 或 schema。** 29 条 lane 读的字段仍然只由"字段存在性"那一组守着（§94）；这一轮做的是**把"为什么没有契约"变成可查的一句**，外加钉住其中确实存在的那一点点结构性事实。
5. **`reason_code` 与 `schema_version` 的普适性只在"这 29 条 lane 的这 29 份文档"上量过。** 别的命令（比如只在 `--json` 之外才存在的输出、或错误路径上的输出）**没有进这次测量**——那些路径的文档形状这一轮没有看。

### 100.8 实施顺序

1. 先读 §94 留下的**解锁词**（"一条通用信封 schema"），把它当成一个**待验的假设**而不是结论；
2. 量三条：未钉住 lane 数、互不相同的形状数、共有键——**假设被否掉了**（29/29）；
3. 于是把产出从"设计一份 schema"改成"**把没有契约的理由写清楚**"，并找出这个否证里**确实可以钉住的那一点**（交叉集）；
4. 写判据（双向 + 人口下限），注释里带上它来自哪次测量、以及为什么只钉两个键；
5. 用真实语料跑一次（确认交叉集）、再用合成语料跑四个方向；合成语料第一版写错，被"多一个共有键"那条抓住，改掉；
6. 回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 101. 第 101 阶段：声明"没实现"的动词，CLI 拒绝时说的是不是那句声明

### 101.1 这一阶段要解决什么

§60 给六条命令路径写了**为什么没有**与**什么才能解锁它**，并加了一组守卫要求这套登记表**准确**
（`needs-admin` / `needs-capability` 的类别、解锁词、以及"类别恰好等于实际用法"）。§82 之后登记表
是准的。**但从来没有人问过：调用那六条，实际会发生什么。** 这一轮先量这个问题。

### 101.2 实测：六条动词**全部**死在 argparse 里，消息读起来像拼错

```
$ airoot bootstrap
   exit   : 8
   stderr : error: INVALID_INPUT: argument command: invalid choice: 'bootstrap' (choose from ...)
             - usage: airoot --help
```

六个（`bootstrap`、`reconcile`、`path backup`、`path restore`、`root adopt`、`root relocate`）
**逐条相同**：退出码 8、`INVALID_INPUT`、消息是 argparse 的 `invalid choice`、`evidence` 只有 usage 行。
登记表里那段"为什么"与那个解锁词，**一个字都没到调用方手里**。三件事因此同时不成立：

| 事实 | 实测 |
|---|---|
| 它读起来像打字错误 | 与 `airoot frobnicate` 得到的**是同一句话**（`test_unknown_command_is_invalid_input` 就在旁边） |
| `INVALID_INPUT`(8) 说"你的输入错了" | 调用方的输入没有错 |
| `PRIVILEGE_REQUIRED`(5) 说"提权再试" | 本条裁决是**在已经提权的会话里**做的（`IsInRole(Administrator)=True`），六条一条也不通 —— 提权变不出这个命令 |
| 登记表与 CLI 有关系 | **没有**。双方可以永远各说各话，而且**没有任何守卫会红** |

最后一行是这一阶段的重点，它又是 §98 那条镜头的一个实例：**登记表承诺"调用方会被告知为什么"，
而 CLI 一个字都没说，却没有一条检查能发现这件事**——因为检查都在登记表这一侧，CLI 那一侧没有对应物。

### 101.3 裁定（ADR-0027）：新增 `NOT_IMPLEMENTED`（退出码 1），拒绝里带登记表的两个事实

- **码**：新增 `NOT_IMPLEMENTED`，映射到**退出码 1**（簇 1「没找到」）。真正为真的那句话是
  "**没有任何可用的东西回来**"，那是退出码 1 的定义。不是 8（输入没错），也不是 5（提权无用）。
  退出码 0–9 一个没动；`NOT_IMPLEMENTED` 与 P1 引入的其它码一样落在既有退出码内。
- **形状**：六条在**动词位置**上被拦下，走正常错误路径，所以 `--json` 得到标准错误信封；
  `details` 带 `deferred_category` 与 `unblocked_by`（机器可读），`evidence` 带同一件事的人读版本与
  指向登记表那段理由的指针。
- **只看动词位置**：`bootstrap` 仍然可以是能力名或路径名——`airoot where bootstrap` 必须走到查找并
  如实回答"没找到"，而不是拒绝这个**词**。

### 101.4 有意的复制，配一条让它无法单侧漂移的守卫

两个事实被复制进核心（`cli.DECLARED_ABSENT`），**理由是运行期不能读 Skill 层**：受保护 broker 的信任
边界（规划 §8.1）要求它不信任用户可写的 Skill 代码。有意的复制必须配一条让"改一侧不改另一侧"
不可能的守卫，所以 `test_l0_consistency.py` 新增**第三十五组**，把核心的表与登记表按
**动词集合、类别、解锁词**双向钉死——两个方向各自是一条不同的谎：

- 登记表 defer 了一个核心不按名字拒绝的动词 ⇒ 调用方又遇到 argparse，登记表的承诺悄悄失效；
- 核心按名字拒绝了一个登记表没 defer 的动词 ⇒ 核心声称了一个没人写下的决定（§8 诚实规则的另一侧）。

### 101.5 顺手修掉的两处陈述

入口文档 `SKILL.md` 的《未实现的命令》一节有两句**在 ADR-0025/§82 之后就过期了**：

- `root adopt|relocate` 写的是"**需要完整的 copy/verify/switch 规则**"——规则**已经决定了**
  （源目录不动、先验后切、一个原子切换点、adopt 自己不写机器 PATH），剩下的是受保护状态；§82 已经
  按这个理由把登记表的类别从 `needs-decision` 改成 `needs-admin`，散文没跟上。现在两处一致：
  **等的是 P2，不是一条还没写的规则**；
- `reconcile` 写的是"语义在规划里只有一句"——登记表里的理由更准确：它要读**项目清单**，而这一版
  没有任何东西产出或读它，属 P6。入口文档改为与登记表同一个理由。

### 101.6 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 核心的动词集合少一条（合成空表） | 红 | ✅ 红：`deferred in the register but not refused by name in the core` |
| 核心多一条登记表没有的动词 | 红 | ✅ 红：`refused by name in the core but not deferred in the register` |
| 类别对不上（合成：把样本换成另一个已声明类别） | 红 | ✅ 红：`core says ... register says ...` |
| 解锁词对不上（合成：换成另一个已声明解锁词） | 红 | ✅ 红：`core unlocks on ...` |
| 把拒绝的码改回 `INVALID_INPUT` | 红 | ✅ 红（`test_cli.py` 的三条行为用例里两条会红） |
| 把动词位置判断改成"词匹配" | 红 | ✅ 红（`where bootstrap` 会报 `NOT_IMPLEMENTED` 而不是 `NOT_FOUND`） |
| 真实状态（六条动词 + 一个拼错的动词 + `where bootstrap`） | 绿 | ✅ 绿 |

两条变异值**从工件推**而不是写死：`other_category`/`other_unlock` 取自 `DEFERRAL_CATEGORIES` /
`DEFERRAL_UNBLOCKERS`——§99 那个把 `"99"` 写死的探针就是因为写死才在下一轮变绿的。

### 101.7 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **860 → 864**（`test_cli.py` 新增 3 条行为用例，`test_l0_consistency.py` 新增 1 条审计） |
| 审计检查（`test_l0_consistency.py`） | **97 → 98**（新增第三十五组） |
| golden fixture | **35 → 35**（不变，但 `reason_code_table.json` 的**内容**变了：多一条码，已重生） |
| 新增 reason code | **1 个**（`NOT_IMPLEMENTED`，退出码 1） |
| 被修的文档 | **3 处**（`docs/AIROOT-v0.3-诊断码与ReasonCode表.md`、`references/reason-codes.md`、`SKILL.md`） |
| 新增 ADR | **ADR-0027** |
| schema / 命令面 | **一个字节没动，一个动词没加** |

### 101.8 如实记录的边界

1. **`--help` 仍然不会告诉读者这六条存在。** 它们只在登记表与 `SKILL.md` 的《未实现的命令》里。
   这是**有意的**（不教一个不存在的命令面），代价是：一个直接敲 `airoot --help` 的人不会知道
   `bootstrap` 是被想过并被推迟的，只会觉得它不存在。要改就是另一条裁决。
2. **复制仍然是一份复制。** 守卫让核心与登记表不可能悄悄分叉，但它没有消除复制；消除它需要核心在
   运行期读 Skill 层，而那正是受保护模式不允许的。这是**取舍**，不是遗漏。
3. **错误信封没有已发布的 schema 描述**（19 个 schema 里没有 error-response 那一份），所以
   `NOT_IMPLEMENTED` 的 `details` 形状是**代码定义的**——与 §92.9-6 记下的那条边界同一个来源。
   这一轮没有动它。
4. **`reconcile` 的类别依赖 P6 的 `project manifest` 真的在 P6 落地。** 如果 P6 换了形状，登记表与
   核心的表要**一起**改（守卫会红，但红的处理方式是人选的）。
5. **这一轮只量了"调用已声明缺席的动词"这一条路径。** 别的"只在文档里存在"的东西（比如
   `agents/airoot.json` 的 `uncovered_verbs` 里那几个**已实现但没有 lane** 的动词）**没有进这次测量**：
   它们能跑，只是不该被教——那是另一类问题。
6. **`NOT_IMPLEMENTED` 的普适性只在六条上量过。** 将来多一条 deferred 路径，守卫会要求核心与登记表
   同时更新（这是设计），但"这条码是否适合那一条新路径"仍然要人判。

### 101.9 实施顺序

1. 先量"六条声明缺席的动词，调用时到底发生什么"——发现它们死在 argparse 里，与拼错无法区分；
2. 把登记表与 CLI 摆在一起看，确认**它们之间没有任何关系、也没有守卫**（§98 镜头的又一实例）；
3. 裁决码（ADR-0027）：退出码 1、`NOT_IMPLEMENTED`、拒绝里带类别与解锁词；
4. 实现：核心的小表 + 动词位置判断 + 走正常错误路径；**不往 parser 里加动词**；
5. 写两条守卫（审计组双向钉死 + 三条行为用例，其中一条专门证明"拼错与缺席不再同答"）；
6. 用合成变异验红四个方向，并确认 `where bootstrap` 不被吞掉；
7. 顺手把 `SKILL.md` 两处过期陈述改成与登记表一致；
8. 回写计数、重生语料；跑全量 + 旧切片 + 真机验收；提交。
## 102. 第 102 阶段：每一次失败打印的那份文档，有没有契约

### 102.1 这一阶段要解决什么

§101 在记录末尾如实写下了一条边界：

> **错误信封没有已发布的 schema 描述**（19 个 schema 里没有 error-response 那一份），所以
> `NOT_IMPLEMENTED` 的 `details` 形状是**代码定义的**。

这一轮做那条边界：先量它**是不是可以被一份 schema 钉住**，再量**钉住它要付什么代价**。

### 102.2 实测一：一个形状覆盖全部 96 个码——与 §100 恰好相反

`AirootError.to_envelope` 是这份文档**唯一**的写者，`_report_error` 是**唯一**的打印点。
把 96 个已注册 reason code 各走一遍：

| 项 | 数 |
|---|---|
| 已注册 reason code | **96** |
| 顶层形状（`schema_version`/`status`/`reason_code`/`message`/`evidence`） | **1 个，覆盖 96/96** |
| 可选键 | **1 个**（`details`，今天只有 §101 的 `NOT_IMPLEMENTED` 写它） |

**§100 量出 29 条 lane 有 29 个互不相同的形状，所以"一份信封"不存在；这里一个形状覆盖全部，
所以一份 schema 是量出来的可行。** 同一个问题、两次测量、相反的结论——差别在**谁写它**：
那边 29 条 lane 各写各的报告，这边一个写者、一个打印点。

同时量到三件不该同时成立的事：

1. **19 个 schema 里没有一份描述它**，而 §7 写着"核心在打印任何对外 JSON 之前调用
   `validate_self`"——对这份文档那句话是**假的**：没有 schema 可校验；
2. 它是**每次失败**都会打印的文档，也就是 agent 出错时唯一会读的那份；
3. §101 刚往它里面加了一个键（`details`），加在一个**没有契约**的形状上。

### 102.3 实测二：加第 20 个 schema，今天会让 **41 行**变红，其中 **38 行是历史**

先把 `actual` 从 19 改成 20，看计数的守卫会说什么：

```
actual=19: checked=43 failing=0
actual=20: checked=43 failing=41     # 38 行在契约草案里
```

那 38 行是 §21/§31/§49… 的**逐阶段记录**（"`schema_count` 仍为 19"、"`schema_count: 19`"）。
**它们写下的时候都是真的。** 满足那条守卫的唯一办法是**改写历史**——而同一个仓库里**测试计数**
的守卫早就有正确的写法：草案里的总数是逐阶段记录，**允许不同，但不允许超过当前值**。

两条守卫、同一种文档、两套规则。这不是"将来会咬人"：**P4/P5/P6 每个阶段都可能新增 schema**，
每次都要求重写 38 行历史——这条守卫**扛不住路线图**。

### 102.4 裁定（ADR-0028）

1. **发布 `error-response.schema.json`（第 20 个）**：`schema_version` 钉 1、`status` 是
   `const: "failed"`、`reason_code` 用 `common.$defs.errorCode`（**第一次被 `$ref`**，pattern 从此
   只有一处定义）、`message` 非空、`evidence` 是**短字符串数组**、`details` 是可选的**标量映射**。
2. **`evidence` 用字符串数组不是新分歧**：已发布的 `doctor-response` 对单条诊断的 `evidence`
   **就是**字符串数组，而 `common.$defs.evidence` 的 `{kind, detail}` 对象在另外 8 处（`where`/
   `doctor` 顶层/`search`/`transaction`/扩展信封/`broker-response`/`registry-projection`/
   `common.externalReference`）。两类文档本来就是两种意思——**结构化发现**与**一句话说明**。
   本条把这条同形不同义**写进 schema 的 `description`**，不再让它当巧合。
3. **`_report_error` 自校验，但永不遮蔽失败**：缺陷追加到 `evidence`，`reason_code` 保持原样。
   别的对外文档都是"校验通过才替换上一个"，所以可以报 `SELF_VALIDATION_FAILED`；在这里那样做是
   **反的**——被校验的文档就是"失败的报告"。
4. **计数守卫按文档分档**：当前状态文档（`AGENTS.md`、schema README、审查报告状态节）必须写出当前
   计数；契约草案是逐阶段记录，只要求**不声称超过当前值**的数。**这是对审计守卫的修改，按 ADR-0021
   的第四条例外如实报出来**（放宽"历史行必须重述当前值"，收紧"草案不得声称超过当前的数"）。
5. **失败文档进验收语料**：守卫第三十二组要求"被 `validate_self` 校验的 schema"与"golden 里有
   fixture 的 schema"双向相等，所以新增 `error_not_implemented.json`——它**从工件推导**
   （`DECLARED_ABSENT` 的第一条 + `main` 用的同一个构造器），不是把手写句子抄进生成器
   （手抄的 fixture 会让"逐字节可复现"这句话变空：代码改了而 fixture 不变时，测试仍然绿）。

### 102.5 做了什么

1. 量失败文档的形状（96/96 一个形状）与它的写者数（1 个写者、1 个打印点）；
2. 量"加第 20 个 schema"的真实代价（41 行，38 行历史）；把新的分档规则写成 `CURRENT_COUNT_DOCUMENTS`
   + `_schema_count_lines` + `_stated_count_problems`，并给两个方向各写合成变异；
3. 写 `error-response.schema.json` 并让 `_report_error` 自校验（不遮蔽）；
4. 生成 `error_not_implemented.json`，接进 `SCHEMA_FOR_FIXTURE`；
5. 写两条守卫：**每个已注册码的失败文档都过 schema**（96 × 2 种形状）+ **形状缺陷不遮蔽失败**
   （monkeypatch 掉一个必填键，断言 `reason_code` 仍是 `NOT_IMPLEMENTED` 且 `evidence` 点名缺陷）；
6. 同步 schema README 的边界表 / "哪几个会被打印" / 修正记录表，并把 `error-response` 记进
   `references/field-values.md` 的《不在这张表的 schema》（它**没有枚举字段**：`status` 是 const，
   `reason_code` 的取值表就是 reason code 那张表）；
7. 回写计数（20 个 schema、36 个 fixture、867 项测试）；跑全量 + 旧切片 + 真机验收；提交。

### 102.6 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 某个码的失败文档少一个必填键 | 红 | ✅ 红（`test_every_registered_reason_code_produces_a_schema_valid_failure_document`） |
| `evidence` 里放一个对象（`common` 的形状）而不是字符串 | 红 | ✅ 红（同上，schema 只收字符串） |
| `to_envelope` 掉了 `message`（真实形状缺陷） | 红 | ✅ 红：失败**不被遮蔽**——`reason_code` 仍是 `NOT_IMPLEMENTED`，`evidence` 多一行 `self-validation failed: ...` |
| 当前状态文档里留一个旧计数 | 红 | ✅ 红（合成的 stale 行被 `_stated_count_problems` 报出） |
| 草案里声称一个**超过**当前值的计数 | 红 | ✅ 红（合成语料把声明值改成"当前值 + 1"；**这一格刻意不写具体数字**——写了，它自己就成了守卫读到的一条声明，第一版正是这么红的） |
| 草案里的历史行（19） | 绿 | ✅ 绿（这是这一轮**故意放开**的方向） |
| 阶段记录里同一行带测试总数（618 项） | 绿 | ✅ 绿（第一版**错**在这里：它把 618 读成 schema 计数，把 4 行真历史判成"超过当前"——所以改成只读**贴着 `schema_count`/`N 个 schema` 的数字**） |

### 102.7 计数与影响

| 项 | 变化 |
|---|---|
| schema | **19 → 20**（新增 `error-response.schema.json`；**已发布的其他 19 个一个字节没动**） |
| golden fixture | **35 → 36**（`error_not_implemented.json`） |
| 测试 | **864 → 867**（+1 全码覆盖、+2 CLI 行为） |
| 审计检查（`test_l0_consistency.py`） | **98 → 98**（改的是既有那条计数守卫，没有新增检查函数） |
| 对外输出 | 成功路径**一字未改**；失败信封多了**一位契约**（形状没改） |
| 新增 ADR | **ADR-0028** |
| 被修的文档 | schema README（边界表 + 打印集合 + 修正记录）、`references/field-values.md`、`AGENTS.md` §7 |

### 102.8 如实记录的边界

1. **计数守卫只扫"提到 `schema_count` 或 `JSON Schema`"的行。** 草案里"19 个 schema"那种散文
   （不含这两个字面量）**从来没被扫到**；`docs/schema/README.md` 里"从 18 涨到 19 个文件"那句本来
   也逃过了（这一轮顺手改对了，但**不是守卫逼的**）。一句话换个说法就能绕过它——这是粒度，不是新缺陷。
2. **`details` 的值被钉成标量**（string/integer/boolean/null）。将来某个码要放对象进去，按 README
   规则 2 那是一次需要新 schema id 的改动。这是**有意的**：今天唯一写它的地方全是字符串，而
   "接受任何东西"的契约等于没有契约。
3. **只有"顶层打印器"这一条路被钉住。** 三条命令把错误**折进领域文档**（`root status` 的
   `reason_code`、`discover` 的 `missing[]`、`tool pin` 的 `sync[].plan_blocked_by`），
   它们不走 `_report_error`，**没有被这条契约覆盖**。这一轮**没有**去量那三条折叠出来的形状
   有没有契约——它们是下一轮的候选（§103 就是那一轮）。
   **这一条最初写的是"四条"，第四项"`session` 的老值"是没量过就写上去的**（见 §103.2）。
4. **失败文档不是任何 lane 读的文档**：lane 读的是成功回答。它的字段是**被契约钉住**，
   不是**被 lane 覆盖**——这两件事从 §94 起就分开记。
5. **"每一次失败都校验"这句话仍然只对顶层打印器成立**：被折进领域文档的四条路径、以及 `--json`
   之外的人读输出，都不经过它。人读输出用的是同一个文档对象（`error.message` + `document["evidence"]`），
   但**没有人读校验**。
6. **`error-response` 的"没有写者"状态就此结束，但 ADR-0026 的 5 个仍没有写者**（`broker-*`、
   `desired-manifest`、`gc-plan`、`runtime-instance`）。守卫第三十四组那条推导是**算出来的**，
   所以它自己就跟着变——这一轮没有改它。

### 102.9 实施顺序

1. 先读 §101 留下的那条边界，把它当成**待验的假设**（"这份文档该不该有契约"）；
2. 量它的形状（96/96 一个形状）与写者数——**假设成立**，与 §100 的结论相反；
3. 再量"加第 20 个 schema 的代价"，发现计数守卫会要求重写 38 行历史——**先把这条挡住**，
   否则"发布第 20 个 schema"这件事本身不可做；
4. 写 schema、让 `_report_error` 自校验（不遮蔽）、生成 fixture；
5. 写两条守卫并用合成变异验红（含"失败不被遮蔽"这一条）；
6. 同步 README / 取值表 / 计数；跑全量 + 旧切片 + 真机验收；提交。
## 103. 第 103 阶段：失败被折进成功文档的三处，折进去的到底是什么

### 103.1 这一阶段要解决什么

§102 的 ADR-0028 在"仍然挡住的"里写下一条边界：**"被折进领域文档的错误，折出来的形状有没有契约"**
——并声称那是**四条**命令。这一轮先量那件事，再决定改什么。

### 103.2 实测一：不是四条，是**三条**折叠 + 一次降级（ADR-0028 记错了一条）

`grep -n 'except AirootError' cli/app/airoot` 在 CLI 里只有五个站点：99 / 156 / 373 / 1872 / 3334
（最后一个就是顶层的 `_report_error`）。逐个读它们，得到三类：

| 站点 | 做法 | 码还在吗 | 有断言吗 |
|---|---|---|---|
| `cmd_root_status`(99) | 折进**文档自己的** `reason_code`，退出码跟着变 | ✅ | ✅（既有测试） |
| `cmd_tool_pin`(1872) | 折进 `sync[].plan_blocked_by` / `plan_blocked_detail`；文档码仍 `SUCCESS`、退出 0 | ✅ | ✅（`test_l1_desired.py` + 真机验收） |
| `cmd_discover`(373) | 折进 `missing[]`，**只留 `f"{id}: {message}"`**；顶层写死 `DATA_ROOT_MISSING` | ❌ | 只有"`missing` 非空" |
| `cmd_doctor`(156) | **不是折叠**：不带 registry 继续跑，把原因作为一条**诊断**报出来（`code`/`severity`/`evidence` 齐备） | ✅ | ✅ |

**ADR-0028 里那第四项"`session` 的老值"是不存在的**：`caps/session.py` 里两个 `SESSION_STATE_STALE`
是 **`raise`**，CLI 里也没有 session 的 handler。**那是一项没量过就写进一张"量过"的表里的事实**——
两处文档（ADR-0028 与 §102.8-3）这一轮一起改对了，并把更正本身记在 ADR-0029 里。

### 103.3 实测二：`discover` 是三处折叠里唯一的异类，而它**今天恰好是对的**

`discover_data_root` 只有**一个** `raise`（`DATA_ROOT_MISSING`，两个站点），白名单在循环外加载——
所以写死的顶层码**总是**等于真正的码。**"恰好对"与"被守住"不是一回事。** 把第二处 `raise` 驱动出来
（monkeypatch 成 `REPARSE_POINT_REJECTED`）之后，旧代码的折叠结果里**除了一句散文什么都没有**：

```
missing:      ['dr-env: a data root may not be a reparse point']
reason_code:  DATA_ROOT_MISSING     → 退出码 6
```

调用方拿不到真正的码；而退出码**永远不会**随原因改变。这一条**能红**：新写的测试在旧代码上就是这样
红的（`assert ['dr-env: a data root...'] == [{'data_root_id': ...}]`）。

### 103.4 裁定（ADR-0029）

1. **`missing[]` 的每一项带上自己的码**：`{data_root_id, reason_code, detail}`。
2. **顶层保持 `DATA_ROOT_MISSING`（聚合）**：好几个数据根可能同时失败，顶层回答的是**类**
   （"某个已声明的数据根读不了"——状态问题，退出码 6），**原因**在行上。这与 `root status`
   （文档谈的就是那一个 registry ⇒ 折进自己的码）和 `tool pin`（一次意图写入成功、一件计划被挡 ⇒
   码落在字段里）是同一条原则的第三个实例：**折进哪个位置取决于文档在谈什么**。
3. **agent 面跟着改**：`discover` 的 lane 增加 `missing[].data_root_id` / `missing[].reason_code`
   两条 `read`。
4. **这两条立刻被 §99 的空穿判据逮住**：该 lane 的场景里 `missing` 是空的，"改个键名也会绿"，
   于是场景补了一个**读不到的数据根**（声明 → 删目录 → 跑 `discover` → 建回来，后面的命令不受影响）。
   **这正是 §99 想要的效果**：说要读 `x[].y`，就得有非空的 `x`。

### 103.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 折叠只留散文（旧形状） | 红 | ✅ 红：`['dr-env: a data root may not be a reparse point']` ≠ 三个字段的行 |
| 顶层码跟着折叠的码走（而不是聚合） | 红 | ✅ 红（新测试断言顶层是 `DATA_ROOT_MISSING`、退出 6） |
| lane 说要读 `missing[].reason_code`，而场景里 `missing` 为空 | 红 | ✅ 红（§99 的空穿判据，文字就是"give this lane a scenario that produces one"） |
| 真实状态（一个 declared 但目录被删的数据根） | 绿 | ✅ 绿（既有测试：退出 6 + `DATA_ROOT_MISSING` + `missing` 非空） |

### 103.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **867 → 868**（新增 1 条：第二条 `raise` 的折叠形状） |
| 审计检查（`test_l0_consistency.py`） | **98 → 98**（不变） |
| golden fixture | **36 → 36**（不变：`discover` 的 envelope 不进 golden，进 golden 的是 `DiscoveryReport`） |
| `read` 路径 | **139 → 141**（lane 多读两条） |
| 对外输出 | `discover` 的 `missing[]` 由字符串变成对象；**成功路径与其它命令一字未改** |
| 新增 ADR | **ADR-0029**（并更正 ADR-0028 的一处未测量陈述） |

### 103.7 如实记录的边界

1. **`missing[]` 没有已发布 schema。** `discover` 的文档是 §100 量到的 29 份未钉住的报告之一；
   这一轮只让它**自洽**（码是真的码），没有给它契约。§100 的结论（一份信封盖不住 29 个形状）没被推翻。
2. **`tool pin` 仍然报 `SUCCESS`(0)**，即使它的计划被挡。这是**有意的**（愿望写下了）且有断言，
   但"退出码 0 + 一个被挡的计划"只有读 `sync[].plan_blocked_by` 的调用方才知道。本轮**没有**改它。
3. **三处折叠里只有一处被修过。** `root status` 与 `tool pin` 本来就带码，这一轮只是**量了它们**，
   没有给它们加守卫；将来它们丢码，能红的仍然只有各自的测试。
4. **"折叠"本身没有台账。** 哪些命令允许把失败折进成功文档、折在哪个字段、顶层码该是聚合还是原码，
   散在代码与各阶段记录里。本轮只把**今天存在的三处**列全，没有建立一张登记表。
5. **`discover` 的聚合码仍然是写死在代码里的一行。** 它现在**说得通**（类 vs 原因），但"哪些折叠适合聚合、
   哪些适合原码"这条判据只有 §103.4 那段文字，没有机器可查的形式。

### 103.8 实施顺序

1. 先把 ADR-0028 的"四条折叠"当真，逐站点 `grep` 出来读——**发现只有三条**，第四项是没量过的；
2. 逐条判"码还在吗、有断言吗"，找出唯一的异类（`discover`）；
3. 驱动第二处 `raise`，用旧代码证明"折叠里什么都没有"（这也是新守卫的红方向）；
4. 改折叠形状 + 顶层聚合的注释 + 人读输出；写新测试；
5. 把 lane 的 `read` 补上两条，**让 §99 的空穿判据逼出场景**（声明→删→跑→建回）；
6. 更正 ADR-0028 与 §102.8-3，写 ADR-0029 与本记录；
7. 回写计数；跑全量 + 旧切片 + 真机验收；提交。
## 104. 第 104 阶段：同一句"schema 里的每个 enum"，两个判据一个看得见、一个看不见

### 104.1 这一阶段要解决什么

§97.7-4 记下过一条边界：

> `_enums_in` 不管 `oneOf`/`allOf`。

这一轮去量那条边界到底有多大——量出来的洞**比那句话写的更大**，而且**同一句问题在仓库里有两个走法**。

### 104.2 实测：同一个问题，两个走法，38 / 61

"这个 schema 声明了哪些 enum"这个问题，仓库里有**两个**实现：

| 走法 | 位置 | 它下降的关键字 | 看到 / 全部 |
|---|---|---|---|
| `_enums_in` | `test_l0_consistency.py`（fixture 覆盖规则 + 合成自检） | 只有 `properties`、`items` | **38 / 61** |
| `enum_value_sets` | `test_l1_field_values.py`（取值表完整性） | **每一个 dict/list**（完整） | 61 / 61 |

`_enums_in` 的 docstring 写着 "every `enum` the schema declares, **at any depth**"——**这句话是假的**。
它看不见的 **23 个**：

| schema | 漏掉 | 在哪个关键字下 |
|---|---|---|
| `common` | **18 / 18（全部）** | `$defs` |
| `extension-manifest` | 3 | `operations.additionalProperties` |
| `reference-plan` | 1 | `exposure.variables.propertyNames` / `not` |
| `registry-projection` | 1 | `external_references[].source_kind` 的 `anyOf` |

**而今天没有任何东西依赖那看不见的一半**：`_enums_in` 只被用来走 `search-response`（那里 5/5 都可见），
取值表的完整性用的是**另一个**走法（完整的那个）。所以那条守卫**是对的，对的理由却没人写下来**——
§97 就考虑过"把规则拓宽到第二份 schema"，一旦真去拓，它会**静默地**只覆盖一半。

**这就是这一轮要修的东西**：不是"某个守卫算错了"，而是"同一句话有两个走法，其中一个看不见 38% 的对象，
并且它的自我描述是假的"。

### 104.3 裁定（ADR-0030）

1. **一个走法**：`cli/tests/schema_walk.py` 的 `enums_by_path` 是唯一定义，`enum_value_sets` 由它派生；
   两个守卫都改为委托。
2. **路径拼法保持原样**（`properties` 用 `.`、`items` 追加 `[]`），新可见的关键字各有约定：
   map 形状（`additionalProperties`/`patternProperties`）贡献一段 `.*`；`oneOf`/`anyOf`/`allOf`/`not`/
   `if`/`then`/`else`/`propertyNames` **保持当前路径**；`$defs`/`definitions`/`dependentSchemas` 用名字连接。
3. **两条路到同一个键要合并，不许覆盖**——静默丢值正是本条要终结的缺陷。
4. **完整性用形状证明，不用计数证明**：一张合成 schema 在每个能带 subschema 的关键字下各放一个 enum，
   断言**路径集合恰好等于**预期（双向）。计数会随"任何地方多一个 enum"而变；形状只在这个关键字
   停止被下降时变红——后者才是要守的事。

### 104.4 做了什么

1. 写 `cli/tests/schema_walk.py`：一个完整的走法（`enums_by_path` + 由它派生的 `enum_value_sets`）；
2. `test_l0_consistency._enums_in` 与 `test_l1_field_values.enum_value_sets` 改为委托（后者**删掉了自己那份走法**）；
3. 新增合成形状判据（见 104.3-4）；
4. 量了一遍两边是否仍然一致：改完之后走法看到 **61/61**；取值表的 30 行**一条没动**（它本来就完整——
   人工读过 `$defs`），这一轮只是让**机器**也能看见那些行。

### 104.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把走法缩回旧的两关键字（`properties`/`items`） | 红 | ✅ 红：`{'[]': ['b']} != {'[]': ['b','c','d']}`，并列出缺的 `''`、`'*'`、`'dep'`、`'shared'` 四组 |
| 不改走法、只把合成 schema 里的某个关键字拿掉 | 红 | ✅ 红（路径集合是双向断言：多一个键也红） |
| 真实状态（20 个 schema 各走一遍） | 绿 | ✅ 绿（61/61；取值表守卫不变） |

### 104.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **868 → 869**（新增 1 条形状判据） |
| 审计检查（`test_l0_consistency.py`） | **98 → 99**（同上；这个文件里的检查数 = 测试函数数 + 参数化增量） |
| schema / 对外输出 / 退出码 | **一个字节没动**（改的是**读者**，不是**契约**） |
| 新增文件 | `cli/tests/schema_walk.py`（仓库地图里已登记——**守卫当场要求过**） |
| 新增 ADR | **ADR-0030** |

### 104.7 如实记录的边界

1. **新可见关键字的路径拼法（`operations.*.operation_kind`、`exposure.variables`）是这一轮引入的约定**，
   今天没有第二个消费者，所以它**不是契约**；将来谁按路径引用它们，得先把约定写下来。
2. **`enum_value_sets` 把路径丢掉了**：两个不同字段共用同一套词汇（`management` 出现在三处）在表里
   是**一行**。这正是取值表需要的（一处解释、多处引用），代价是它**查不出**"只解释了一次、却用在三处"。
3. **走法只认 `enum`，不认 `const`**：`status: {"const": "failed"}` 这样的字段永远进不了这个问题
   （`error-response` 就是；§102 的处置是把它放进取值表的 EXEMPT 一节人工说明）。所以"枚举完整性"
   这条守卫**盖不住 const 字段**——今天有 1 个。
4. **走法不跟 `$ref`**：它问的是"**这个文件里**声明了哪些 enum"。`where-response.health` 是
   `{"$ref": "common.schema.json#/$defs/health"}`，所以在 `where-response` 这一侧看不到那 5 个取值；
   完整性靠"每个文件各走一遍"的**并集**成立，而 `common` 自己在受检名单里。**如果哪天有人把 `common`
   移出受检名单，这条并集就破了，而没有守卫会说。**
5. **这一轮没有重新讨论"要不要把 fixture 覆盖规则拓宽到别的 schema"。** §97 拒绝拓宽的理由（别的 schema
   会产生大量假 †）没有被推翻；本轮的贡献是让那个决定在**信息完整**的前提下重新可做——真去拓时，
   走法不会再漏掉 23 个取值。

### 104.8 实施顺序

1. 先量 §97.7-4 说的那条边界：写一个"完整走法"当对照，逐 schema 比较——发现漏的不是 `oneOf`/`allOf`，
   而是 `$defs`（18 个）和 `additionalProperties`（3 个）；
2. 顺手量到**同一句话有两个走法**，且另一个是完整的——这才是根因；
3. 把完整走法抽成一个共享模块，两处都委托过去；
4. 用**形状**而不是计数证明完整性（合成 schema 覆盖每个关键字，路径集合双向断言）；
5. 把走法缩回旧形状验红（红出来的正是缺的那几组路径）；
6. 更新仓库地图（守卫要求新文件必须被登记）、回写计数、写 ADR-0030 与本记录；跑全量 + 旧切片 + 真机验收；提交。
## 105. 第 105 阶段：`$ref` 的另一半——"文件自己声明的"与"文档能携带的"

### 105.1 这一阶段要解决什么

§104.7-4 记下了一条边界：

> 走法**不跟 `$ref`**：它问的是"**这个文件里**声明了哪些 enum"。完整性靠"每个文件各走一遍"的
> **并集**成立，而 `common` 自己在受检名单里。**如果哪天有人把 `common` 移出受检名单，这条并集就破了，
> 而没有守卫会说。**

这一轮去做那件事。第一步还是量：**跟 `$ref` 之后会多看见什么**。

### 105.2 实测：口径一换，多出来一半

| schema | 自己声明 | 跟 `$ref` 后 | 差在哪 |
|---|---:|---:|---|
| `where-response` | 1 | **9** | `health`/`scope`/`zone`/`management`/`security_mode`/… 全来自 `common` |
| `plan` | 4 | **10** | `target.platform`/`architecture`/`operations[].target_scope` |
| `managed-tool-instance` | **0** | **10** | 自己一个 enum 都没有，却**能携带** 10 套 |
| `registry-projection` | 4 | **11** | `instances[].lifecycle_status`/`health`/`bindings[].exposure` |
| `runtime-instance` | 1 | **10** | 同上 |
| `search-response` | 5 | **6** | `data.results[].management` |
| `common` | 18 | 22 | 它内部的**本地** `$ref`（`binding.scope`/`zone`/`externalReference.health`/…） |

两条结论：

1. **受检 schema 的可达词汇今天全部有记录**（0 条漏）。所以把覆盖判据换成"可达"口径**不会带来假 †**，
   却让覆盖**不再依赖名单**：即便 `common` 哪天被移出受检名单，`where-response` 依然会替它要 `health` 那一行。
2. **`search-response` 会从 5 变成 6**，而它正是 **fixture 覆盖规则**用的 schema。所以"跟不跟 `$ref`"
   **不是实现细节，是换一个问题**：那条规则只能要求"文档自己的生产者能产出的值"，把继承来的共享
   词汇也塞进去，正是 §97 拒绝过的假 † 堆。

### 105.3 裁定（ADR-0031）

1. **一个走法、两种问法、用参数说明是哪一种**：`enums_by_path(schema)`＝这个文件自己声明了哪些；
   `enums_by_path(schema, resolve=...)`＝这个文档能携带哪些。**不是**又变回两个走法（§104 刚合并掉
   两个），而是同一个走法的两种用途，而用途写在调用处。
2. **解析器回答一对值**：`resolve(ref, document) -> (target, target_document) | None`。第二半是**目标所在的
   文件**——因为被引用文件里的**本地** `$ref`（`common` 的 18 → 22 全靠它）要在那个文件里解析；
   同一条引用在同一路径上出现两次即停（环不递归）。
3. **取值表的覆盖判据改成"可达"口径，并覆盖每一个已发布 schema**（不再分受检/豁免）：任何一条可达词汇
   要么有行，要么在 `UNDOCUMENTED_BY_DESIGN` 里**被点名**——那个集合**双向**断言，所以第五条不可能
   悄悄出现，过期的一条也不可能留着。今天被点名的恰好四条，全部来自 `broker-*`：
   `operation`、`client.integrity`、`status`、`enforcement`。
4. **fixture 覆盖规则保持"自己声明"口径**，并把理由写进代码（只有文档自己的词汇才由它的生产者控制）。
5. **删掉 §104 引入的 `enum_value_sets`**：给一条没被记录的词汇**点名**需要**路径**，而值集合说不出
   它来自哪个字段。走法仍然只有一个。
6. **新增目录级判据**：每个已发布 schema 写出的 `$ref` 都要**在集合内**解析得到。守卫用的解析器是
   **故意全函数**的（解析不到返回 None，那一支什么都不贡献），所以"哪些引用答不上来"必须由别处量。

### 105.4 做了什么

1. 量两种口径的差（上表）；
2. 给共享走法加 `resolve` 参数与 `schema_ref_resolver`（缓存 + JSON pointer + 本地/跨文件两种引用）；
3. 取值表的完整性判据改为"**每一个**已发布 schema 的可达词汇"，并把豁免从一个**散文表**变成
   一个**点名的精确集合**（双向）；
4. 目录判据：`$ref` 必须在集合内可解析（实测全部可解析，且解析器能说"不"——非空判据）；
5. 合成形状判据扩到引用：**无解析器＝只报自己的**、**有解析器＝跟引用且保留引用方路径**、
   **本地 `#/$defs/...` 在目标文件里解析**、**环不递归**——四条一起断言；
6. 改掉一条**说错了的豁免理由**：`managed-tool-instance` 原先写"这个文件里没有枚举字段"，
   那对**文件**为真、对**文档**为假（可达 10 套，全部来自 `common`）。

### 105.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把"可达"判据退回"只看自己声明" | 红 | ✅ 红（把 `common` 换成豁免后，`where-response` 继承的 `health` 等没有行可查） |
| 在 `UNDOCUMENTED_BY_DESIGN` 里加一条并不缺记录的 | 红 | ✅ 红（反方向断言：**过期豁免**也是红） |
| 去掉一条真的没记录的（例如 `broker-response.status`） | 红 | ✅ 红（"没人点名的未记录词汇"） |
| 无解析器 vs 有解析器（合成 schema，含本地引用与环） | 两种结果各自精确 | ✅ 绿（`{'local': ['one']}` 与 `{'local': [...], 'health': [...], 'direct': [...]}`） |
| 一个解析不到的 `$ref`（合成） | 红 | ✅ 红（目录判据；解析器对 `#/$defs/not_there` 与非集合内文件名都返回 None） |
| 真实状态 | 绿 | ✅ 绿（20 个 schema 全部可达词汇都已记录） |
| 把 `$defs.health` 那一行的三个取值删掉（**只有 `$ref` 才看得见**的那一行） | 红 | ✅ 红：新判据报 `undocumented vocabularies nobody named: common::$defs.health`——**这正是 §104.7-4 担心的那件事**：以前它只由 `common` 自己那一节间接看着，`common` 一移出受检名单就没人报了 |

### 105.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **869 → 871**（+1 引用形状判据；+1 目录级 `$ref` 判据） |
| 审计检查（`test_l0_consistency.py`） | **99 → 100** |
| schema / 对外输出 / 退出码 | **一个字节没动**（改的还是**读者**） |
| 取值表 | 30 行**一条没动**；改的是"哪条词汇该不该有行"的**判据**，以及一条说错的理由 |
| 新增 ADR | **ADR-0031** |

### 105.7 顺带修掉的：审计检查数变成三位数之后，测试总数的判据红了

计数回写之后 `test_the_documented_test_count_is_the_same_everywhere` 红了，理由是：

```
AGENTS.md states more than one current test total: [100, 871]
```

它把"哪个数字是测试总数"**按位数**区分（`(\d{3})\s*项`）——在审计检查还是 **99** 时这个办法凑巧成立；
审计检查到了 **100**，"含 100 项常驻跨工件一致性检查"就被读成了第二个测试总数。
**判据建立在巧合上，而巧合有一个明确的到期日。** 改成按**上下文**区分
（`(\d{3,4})\s*项(?!\s*常驻跨工件)`）：审计检查数由它自己的判据（`_audit_check_count`）守着，
不需要在这一条里再被数一遍。

### 105.8 如实记录的边界

1. **`managed-tool-instance` 的豁免理由原先是错的**，这一轮改成了测出来的说法。**同类的"理由说的是
   文件、表问的是文档"的混淆，只有这一处被量过**——别处的豁免理由没有逐条复核。
2. **`broker-*` 的 4 套词汇仍然不记录**（P2 未实现，没有读者会被交给这些值）。它们是**点名豁免**，
   不是"没人发现"；但"这一版不该记录"这个判断**仍然是人做的**，判据只能保证"点名了就一致"。
3. **可达口径用"引用方"的路径**，所以同一套词汇会在许多路径下重复出现。覆盖问题问的是**值集合**，
   路径只用来**给豁免命名**——因此这里不存在"同一事实写两遍"，但也意味着判据说不出
   "某个共享词汇只被一个 schema 用到"。
4. **走法仍然只认 `enum`，不认 `const`**（`error-response.status` 是唯一一处，§102 用 EXEMPT 的散文处理）。
5. **跨文件 `$ref` 只解析"集合内"的文件**：指向集合外的引用不报错、只**什么都不贡献**——正是因此
   才新增目录级判据；实测目前一个都没有，所以这条判据今天**只能靠合成输入验红**。
6. **这一轮没有重新讨论 fixture 覆盖规则要不要拓宽**：§97 的理由没被推翻。本轮的贡献是让"要不要拓宽"
   在**两种口径都量过**的前提下重新可做。

### 105.9 实施顺序

1. 先量 §104.7-4 说的那条边界：给走法加一个"跟 `$ref`"的对照实现，逐 schema 比较两种口径；
2. 发现两件事：受检 schema 的可达词汇**全部有记录**（好消息），而 `search-response` 会**多出一套**
   （说明这确实是换问题，不是换实现）；
3. 于是把"跟不跟引用"做成**同一个走法的参数**，并把两种用途写进 docstring 与调用处；
4. 取值表的判据改成可达口径 + **点名集合**（双向），把散文豁免换成可测的东西；
5. 给走法写引用的合成判据（本地引用、跨文件引用、环、无解析器时的行为）；
6. 新增目录级 `$ref` 判据（含"解析器要能说不"的非空判据）；
7. 改掉 `managed-tool-instance` 那条说错的理由，并在取值表末尾写清"两张表各回答什么"；
8. 回写计数、写 ADR-0031 与本记录；跑全量 + 旧切片 + 真机验收；提交。
## 106. 第 106 阶段：`const` 也是词汇，但 `if` 里的不是

### 106.1 这一阶段要解决什么

§105.8 留下的最后两条边界：

> **走法仍然只认 `enum`，不认 `const`**（`error-response.status` 是唯一一处）。
> **豁免理由说的是文件、表问的是文档**——只有一处被量过。

这一轮做前一条，并在过程中撞出两个新的缺陷。

### 106.2 实测：41 个 `const`，一半是"值"、一半是"条件"

把 20 个已发布 schema 的 `const` 全走一遍（跟 `$ref`）：

| 类 | 数 | 说明 |
|---|---:|---|
| 可达的 `const` 总数 | **41** | |
| **值位置**（`properties`/`items`/`then`/`additionalProperties`…） | **33** | 文档真的会携带的值 |
| **测试位置**（`if` 之下） | **8** | `binding.scope = "project"` 的真实含义是"**当** scope 是 project 时 `project_id` 必填"，**不是**"scope 恒为 project" |
| 其中**版本钉**（`schema_version`/`protocol_version`/`schemaVersion` = `1`） | **22** | 同一个事实，出现在 19 份文档里 |
| **有意义的唯一取值**（去掉版本钉） | **11** | 见下表 |
| 这 11 条里表里**一行都没有**的 | **10** | 只有 `error-response.status` 在一句豁免理由里被提过 |

11 条有意义的：`error-response.status="failed"`、`search-response.operation="search"`、
`search-response.data.fallback.kind="crawl"`、`plan.canonicalization`、`reference-plan.operation`、
`reference-plan.target.management`、`reference-plan.canonicalization`、`managed-tool-instance.kind`、
`runtime-instance.kind`、`gc-plan.items[].reason`、`gc-plan.requires_approval`。

**顺手量出两个别的缺陷：**

1. **取值表的取值正则看不见连字符**（字符类是 `[A-Za-z0-9_.]`）。于是新增的
   `jcs-rfc8785-compatible` 被解析成**空取值**——三条守卫（值对齐、† 是否过期、取值是否在 schema 里）
   **同时安静了**。这是第四次同一形状：**读的人比被读的文档窄**（§104 只认两个关键字、§105 不跟
   `$ref`、§106 不认 `const`，现在是正则不认 `-`）。
2. **`runtime-instance` 这一版根本没有构造者**（`managed-tool-instance` 有）。
   守卫 `test_no_row_claims_a_writer_for_a_document_this_build_never_builds` 当场纠正了第一版那一行
   ——它把 `registry/entities.py` 记成写者，是错的。

### 106.3 裁定（ADR-0032）

1. **一个走法多一个问法**：`vocabularies_by_path` = `enum` **加上值位置的 `const`**；
   **测试位置（`if` 之下）一律不算**。与 §105 的 `resolve` 参数并列，不是第二个走法。
2. **fixture 覆盖规则仍然只数 `enum`**：fixture 证明的是**枚举的成员**，一个 `const` 只有一个取值，
   没有"成员"可证。三个问法、一个走法。
3. **版本钉是一类，不是一个值**：字段名在 `VERSION_PIN_FIELDS` 里的按"由 README 规则 1 与
   `test_l1_schema_catalog` 守着"处理，不写表行（22 次同一件事写成 22 行是噪声），
   并**断言这类字段的值就是 `1`**。
4. **10 条有意义的 `const` 进表**：`managed-tool-instance` 与 `error-response` **从豁免升级为一节**
   （各自有一个 `const` 字段），豁免表缩到 3 个（两个 `broker-*` + `root-marker`）。
5. **取值只有一个拼法**：`schema_walk.spell` 是唯一定义（`null` 不是 `None`、布尔写 JSON 的
   `true`/`false`），表这边的 `spell` 改成调它——`const` 让布尔第一次真的出现在表里。
6. **取值正则放宽到连字符**，理由写进注释。

### 106.4 做了什么

1. 量两种位置、两种性质（值/条件、版本钉/有意义）的分布；
2. `schema_walk`：`vocabularies_by_path` + `TEST_KEYWORDS` + `spell` 的 JSON 布尔；
3. 表这边：行解析器学会 `const`（`resolve` 与 `spell` 都改），新增 10 行、2 节、豁免缩到 3 个；
4. 覆盖判据改用 `vocabularies_by_path` + `VERSION_PIN_FIELDS` 这一类（值必须是 `1`）；
5. 合成判据：值位置的 `const` 算、`if` 之下的不算、`then` 之下的算、三种拼法（字符串/布尔/`null`）；
6. 修掉取值正则的连字符盲点；把 `runtime-instance.kind` 的写者改成"（没有写者）"+ †（守卫纠正的）。

### 106.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把 `if` 之下的 `const` 也算进来 | 红 | ✅ 红（合成判据直接比对两份字典；真实语料里会多出 8 条"没人点名的词汇"） |
| 不计 `const`（§105 的行为） | 红 | ✅ 红（10 条有意义的唯一取值变成"没人点名"） |
| `VERSION_PIN_FIELDS` 里某个字段不再是 `1` | 红 | ✅ 红（那一类里断言 `values == ["1"]`） |
| 取值正则退回不含连字符 | 红 | ✅ 红（`plan::canonicalization: schema=[...] doc=[]`——这一条**当场红过**） |
| `runtime-instance.kind` 记一个并不存在的写者 | 红 | ✅ 红（`no function builds any runtime-instance document, so the writer column cannot name code`——也**当场红过**） |
| 真实状态 | 绿 | ✅ 绿（152 套可达词汇：有行、被点名、或属于版本钉三类之一） |

### 106.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **871 → 872**（+1 合成 `const` 判据） |
| 审计检查（`test_l0_consistency.py`） | **100 → 101** |
| schema / 对外输出 / 退出码 | **一个字节没动** |
| 取值表 | **+10 行、+2 节**（`managed-tool-instance`、`error-response`），豁免 **5 → 3** |
| 判据 | 覆盖规则从"枚举"扩到"枚举 + 唯一取值"；新增版本钉这一类 |
| 新增 ADR | **ADR-0032** |

### 106.7 如实记录的边界

1. **`if` 的排除按关键字，不按语义**：今天 `if` 之下没有任何"文档携带的值"（量的就是这个），
   但判据说不出"某个 `if` 里的 `const` 其实是取值"——那种写法要人来判。
2. **`then`/`else`/`not` 一律算约束**：`not` 之下今天只有 `propertyNames` 的黑名单（本来就有行）；
   若有人写 `not: {const: ...}` 表示"禁止这个值"，它会被当成一套词汇——**这条没量过**。
3. **一个节点同时有 `enum` 与 `const` 是自相矛盾的**，走法取 `enum`；**没有任何判据拦这种 schema**
   （目录守卫只查 meta-schema 合法性与版本钉）。
4. **取值正则仍然只认 `[A-Za-z0-9_.-]`**：带 `:`、`/`、`%` 的取值（digest、URL 片段之类）在表里
   **仍然看不见**——今天没有这样的行，但这条边界现在是**写下来的**，而不是靠没人写。
5. **"受检 schema"从 15 变 17**（新增两节）："哪个 schema 该有一节"仍然是人定的，判据只能保证
   "定了之后两边一致"。
6. **§105.8-1 的另一半没有做**：豁免理由"说的是文件还是文档"只在 `managed-tool-instance` 那一处
   被量过并改对；剩下三条（两个 `broker-*` + `root-marker`）**这一轮没有逐条复核**——它们的理由
   读起来都是关于"未实现"的，而判据现在把它们的词汇按"点名豁免 / 版本钉"两类处理，所以那句话
   即使措辞不精确也不会让覆盖出错。

### 106.8 实施顺序

1. 先量 `const` 的分布（值位置 vs `if` 之下、版本钉 vs 有意义），把"要处理多少"变成数字；
2. 走法加 `vocabularies_by_path`（`if` 之下不算），fixture 规则保持枚举口径；
3. 表这边学会读 `const`（行解析器），把 10 条有意义的写进去，两节从豁免升级为独立节；
4. 覆盖判据接上新走法 + 版本钉这一类（值必须为 `1`）；
5. 合成判据覆盖"算/不算"与三种拼法；
6. **让守卫把两个错误揪出来**：连字符取值解析成空（值对齐红）、`runtime-instance` 记了不存在的写者
   （写者判据红）——两处都按守卫说的改；
7. 回写计数；写 ADR-0032 与本记录；跑全量 + 旧切片 + 真机验收；提交。
## 107. 第 107 阶段：豁免的理由是"关于文档的断言"，那就量它

### 107.1 这一阶段要解决什么

§106.7 留下的两条边界：

> **§105.8-1 的另一半没有做**：豁免理由"说的是文件还是文档"只在 `managed-tool-instance` 那一处
> 被量过并改对；剩下三条**这一轮没有逐条复核**。

这一轮把那三条量一遍。顺手撞出一处**悬空指针**。

### 107.2 实测：一条理由是错的，一句话指向不存在的东西

逐条对**文档**量（`_produced_schemas` 回答"这一版有没有函数构造它"，走法回答"它自己有没有词汇"）：

| schema | 自己的 enum | 可达词汇 | 没记录的 | **这一版构造它吗** | 原来的理由 |
|---|---:|---:|---:|---|---|
| `broker-request` | 2 | 3 | 3 | **否** | "P2 未实现" ✅ |
| `broker-response` | 3 | 4 | 3 | **否** | "同上" ✅ |
| `root-marker` | 0 | 2 | 2（**两条都是版本钉**） | **是** | **"同上"——假的** |

两个发现：

1. **`root-marker` 的理由是假的**：它写"同上"，即按两个 `broker-*` 归到"P2 未实现"——而这一版
   **真的会写出根标记文件**（`state/root.json`，它是十三个被构造的 schema 之一）。它被列在豁免表里
   的真正原因是**它自己没有词汇**：`schema_version`/`protocol_version` 都是版本钉。
2. **同一行还指向一段不存在的东西**：§106 给它写的"见取值表末尾那段"——而版本钉这一类当时只在
   **测试模块**里（`VERSION_PIN_FIELDS`），表尾并没有那一段。**文档指向了一段没人写的说明**，
   这正是这个项目反复在量的同一类缺陷（§67/§75/§85 记的都是"指向不存在的东西"）。

### 107.3 裁定（ADR-0033）

1. **豁免表多一列 `类别`**（`unbuilt` / `no-own-vocabulary`）——**由文档声明**，判据读那一格。
2. **判据持有度量**：`_measured_exempt_class(name)` 从构建本身推出类别，与文档那一格**必须相等**；
   两类的成员集合**由表驱动**（不再是人手写的第二份副本），且**两类都不能为空**
   （没人落的类别是读者遇到却用不上的词——延后类别早就是这条规矩）。
3. **补上缺的那一段**：表尾写明"一套词汇不在表里"的**三种**合法理由（在别处有行 / 被点名豁免 /
   是版本钉），各自指向守着它的判据——悬空指针消失，而且它指向的内容**真的存在了**。
4. **`root-marker` 的理由改成测出来的事实**，并**明说原先那句是错的**。
5. **顺手把 `_produced_schemas` 缓存起来**：三个判据都问它，而它要 AST 走一遍整个 app——
   新判据让取值表那一个模块从 3 秒变成 12 秒，缓存后回到 3 秒。

### 107.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把 `root-marker` 的类别写成 `unbuilt`（**就是它原来那句话的意思**） | 红 | ✅ 红：`root-marker: the table says 'unbuilt', the build measures 'no-own-vocabulary'` |
| 把一个 `broker-*` 的类别写成 `no-own-vocabulary` | 红 | ✅ 红（同一个判据；它有自己的 enum，还测不出"没有词汇"） |
| 某一类没有成员 | 红 | ✅ 红（"classes with no member: ..."） |
| 表与解析器对"哪些 schema 豁免"看法不一致 | 红 | ✅ 红（`set(EXEMPT_CLASS) == set(EXEMPT)`） |
| 真实状态 | 绿 | ✅ 绿（两个 `broker-*` = `unbuilt`，`root-marker` = `no-own-vocabulary`） |

### 107.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **872 → 873**（+1：豁免理由的度量判据） |
| 审计检查（`test_l0_consistency.py`） | **101 → 101**（不变：新判据在 `test_l1_field_values.py`） |
| schema / 对外输出 / 退出码 / 表行 | **一处没动**；改的是豁免表的**一列**与表尾那段 |
| 取值表 | 豁免表 **2 列 → 3 列**；三条理由按测量重写；补上表尾《三种理由》一段 |
| 新增 ADR | **ADR-0033** |

### 107.6 如实记录的边界

1. **"在哪一节有行"仍然是人定的**：判据只保证"定了之后两边一致"，说不出某个 schema 该不该有自己的一节。
2. **`unbuilt` 是"这一版没有构造者"，不是"永远不会有"**：P2 把 broker 写出来之后这一格会自动变红，
   但**红的是表，不是文档的散文**——没有人会被提醒去改那段话。
3. **表尾那三种理由的措辞没有被判据读**：判据读的是类别那一格；散文仍可能写得比它宽。
   这与 §104–§106 一路上处理的是同一件事：**能被机器读的部分越来越小，剩下的靠人。**
4. **"还有没有别的地方在指向不存在的东西"没有查。** 这一轮只修了 `root-marker` 那一处指针；
   `references/` 与两份大文档里还有大量"见 §X"式引用，**没有任何判据在查它们**（§103.7-4 记过同类）。
5. **`_produced_schemas` 的缓存是本轮的**：它假设"跑测试时树不变"。如果哪天有测试改树再问它，
   缓存会给旧答案——今天没有这样的测试，但这是一条写在代码注释里的假设。

### 107.7 实施顺序

1. 先把 §106.7-1 当真：三条豁免理由逐条对**文档**量（构造者 + 自己的词汇 + 没记录的词汇）；
2. 发现 `root-marker` 那句"同上"是假的（它**有**构造者），并且它还指向一段不存在的说明；
3. 裁定：类别**由文档声明**、**由构建度量**，两边必须相等；
4. 改表（加一列、三条理由按测量重写、补表尾《三种理由》一段）；
5. 写判据（类别一致性 + 两类非空 + 两个解析器口径一致），并用"把 `root-marker` 写成 `unbuilt`"验红；
6. 顺手缓存 `_produced_schemas`（新判据让它被问三次）；
7. 回写计数；写 ADR-0033 与本记录；跑全量 + 旧切片 + 真机验收；提交。

## 108. P2 第一阶段：线路面的客户端一半（ADR-0034）

**这一阶段换了一个方向**：§31–§107 做的是「读」与「守」（协议面、取值表、一致性判据），
P2 的第一阶段开始做「写」——但只写**最不需要权限的那一半**。

### 108.1 动手前量到的四件事实

1. **两个 `broker-*` schema 没有任何使用者**。它们是 ADR-0026 记下的「五个没有写者」中的两个：
   没有代码构造或校验它们，36 个 golden fixture 里**一条 broker 文档都没有**。一份没有读者的契约，
   它的缺陷**不会有人碰到**——这正是第 3 条能一直活着的原因。
2. **客户端那一半不需要提权**。一个进程永远可以打开**自己**的 token，所以"给这两个 schema 一个
   使用者"今天就能做完、今天就能测完，而且**不碰宿主机**。
3. **两个已发布 schema 对 protected 模式的 `enforcement` 互相矛盾**：`broker-response` 写
   `acl_and_broker`，而 `common.$defs.enforcement`——以及 `$ref` 它的另外三份 schema、以及本 build
   到处打印的值（`cli.py`、`ext/envelope.py`、`caps/doctor.py`）——写 `acl_enforced`。全树实测：
   `acl_and_broker` **只出现在那一行 schema 里**，没有任何文档、fixture 或代码含它。
4. **设计文档自己的示例信封过不了自己的 schema**：`docs/broker/` 用 `plan` / `approval` 两个键，
   而 `broker-request` 要求 `plan_ref` / `approval_ref` 且 `additionalProperties: false`。

### 108.2 交付

| 件 | 内容 | 它**不**是什么 |
|---|---|---|
| `caps/identity.py` | 只读本进程 token 得出 `sid` / `pid` / `integrity` / `elevated`（`ctypes` 读 `TOKEN_USER` / `TOKEN_INTEGRITY_LEVEL` / `TOKEN_ELEVATION`），**失败即数据、绝不抛异常**；`is_complete()` 只回答"schema 要的四个字段能不能填满" | **不**判断"我够不够格"——那是 broker 的事；**不**读别人的 token |
| `broker/protocol.py` | `build_request` 造 `broker-request`（先 `validate_document` 再 `validate_self`）；`parse_response` 校验 `broker-response` 并**只在 `status=ok` 时返回**；`broker_unavailable()` 报 `NOT_IMPLEMENTED`(1) | **不是**传输、**不是**服务器、**不是**信任边界；没有 named pipe，没有对客户端 token 的校验，没有新动词 |
| `fixtures/golden/broker_request_commit_plan.json` | 第 37 份语料 | 它不是打印出来的文档，是**发出去**的文档；`client` 块是合成的（public 仓库不放本机 SID） |

**连带处置四处**（都属于「改一处必须改三处」）：原地修 `broker-response` 的两行（手抄枚举 → `$ref`
共享定义，依据 ADR-0003 的转录缺陷先例）；加一条 posture 判据；schema README 记下 `gc_apply` 的已知
不对称；改两处过期或自相矛盾的散文（设计文档的示例键名、诊断码表里那半句"`PRIVILEGE_REQUIRED` 在 P1
不会被发射"）。

### 108.3 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 保留 `broker-response.enforcement` 的手抄枚举（原状） | 红 | ✅ 红：`broker-response.enforcement allows ['acl_and_broker', ...], but common.$defs.enforcement is ['acl_enforced', ...]` |
| 声明 `security_mode` 而不声明 `enforcement` | 红 | ✅ 红（成对判据） |
| 把 `broker-request` 留在豁免表里（原状） | 红 | ✅ 红：`the table says 'unbuilt', the build measures None` |
| 把 `('broker-response','enforcement')` 留在 `UNDOCUMENTED_BY_DESIGN` 里（原状） | 红 | ✅ 红：`named as undocumented, but the table documents them now` |
| 真实状态 | 绿 | ✅ 绿 |

**更宽的那条规则被量过并否决**：先写成"同一个字段名在多份 schema 里出现就必须同值"，跑一遍全树——
它红了 **5** 处，其中 **4** 处是正当的（`management` / `scope` / `source` / `target_scope` 在不同文档里
是不同的概念）。**会因为正当理由变红的检查是噪音**，所以最后落地的判据只覆盖真正是同一件协议事实的
那一对（`security_mode` / `enforcement`）。

### 108.4 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **873 → 939**（+66：49 条线路面 + 16 条身份探针 + 1 条 posture 判据） |
| 审计检查（`test_l0_consistency.py`） | **101 → 101**（不变：三个新模块的判据都住在 `test_l1_*`） |
| schema | **20 → 20**（改的是 `broker-response` 的两行**写法**，不是成员含义） |
| golden 语料 | **36 → 37** |
| `caps` 模块 | **24 → 25** |
| 新增模块 / 测试文件 | `caps/identity.py`、`broker/__init__.py`、`broker/protocol.py` / `test_l1_identity.py`、`test_l1_broker_protocol.py` |
| 新增 ADR | **ADR-0034** |
| 取值表 | `broker-request` 离开豁免表并拿到两行；`UNDOCUMENTED_BY_DESIGN` 四条 → 一条 |

### 108.5 如实记录的边界

1. **它不证明信任边界**：没有 broker、没有 pipe、没有任何一处校验**别人**的 token，`client` 块仍然
   只是**自述**。这一阶段只把两个 schema 从"没有使用者"变成"有使用者"，**不改变 P2 的退出条件**。
2. **失败路径是模拟出来的**：`OpenProcessToken` 在一台健康的机器上无法真的失败，所以两条失败路径用
   模块里的两个可替换接缝模拟（失败即数据的形状被测到，**真实的拒绝没被测到**）。
3. **非提权读数没有观测过**：本次会话是 `high` / `elevated=True`，`elevated=False` 只作为类型与一致性
   性质被断言；`low` / `system` 两级由合成 SID 覆盖。
4. **`requested_at` 不被检查**：schema 写 `format: date-time`，而 `schema_io` 没配 format checker
   （规范里 `format` 默认只是注解）。本层**不**手写时间戳正则——那是全树决定，不是这一层的。
5. **读者不是写者**（顺带钉住的语义）：取值表的"构造者"只看 `cli/app/` 里有没有函数把文档造出来。
   所以 `broker-response` 有了 `parse_response` 之后**仍属 `unbuilt`**；下一阶段把 loopback harness
   放在 `cli/tests/` 时，它同样不会让任何 schema 变成"已构造"——测试替身不是产品文档的写者。
6. **没有出命令面**：`broker_unavailable()` 是库函数，CLI 里**没有**新增动词。`airoot broker ...`
   这样的动词要么不出，要么按 ADR-0027 登记成"已声明缺席"——这一阶段选了前者。
7. **两个 ctypes 缺陷是施工时发现的**，值得记下来（它们不是本项目的契约问题，是 API 的真实形状）：
   未标注 `argtypes` 的参数被当作 C `int`，于是 `(HANDLE)-1` 这个伪句柄让 `OpenProcessToken` 从
   ctypes 内部抛 `OverflowError`——是**崩溃**而不是它本来的访问错误；`GetTokenInformation` 把
   `SID_AND_ATTRIBUTES.Sid` 填成**指向调用方自己缓冲区内部**的指针，缓冲区释放后再解引用会读到已释放
   的堆内存（复现：`ConvertSidToStringSidW` 稳定返回 `ERROR_INVALID_SID`）。
8. **侦察简报没有提交**：`_p2_brief.md` 是工作产物，它的决策与"不能证明什么"已并入 ADR-0034 与本节；
   留在仓库根会变成一份没人守着的文档。

### 108.6 实施顺序

1. 先量 §P2 的现状（有没有使用者、要不要提权、契约之间有没有打架、示例过不过自己的 schema）；
2. 冻结客户端接口（`probe_identity` / `build_request` / `parse_response` / `broker_unavailable`），
   两条工作流并行：身份探针与线路面；
3. 把契约缺陷当成**同一阶段的事**办：修 `broker-response` 的两行 + 加 posture 判据（先验红）；
4. 让两个 schema 各自有使用者，并因此触发**必然的连带**：`broker-request` 离开豁免表、取值表加两行、
   `UNDOCUMENTED_BY_DESIGN` 缩到一条、`broker-request` 拿到一份逐字节语料；
5. 记下 `gc_apply` 的不对称（拒在心里、写在 README，**不改 schema**）；
6. 改两处过期散文；回写计数；写 ADR-0034 与本记录；跑全量 + 旧切片 + 真机验收；提交。

## 109. 一次真实的回滚缺陷：回滚只能撤销自己那一次激活（ADR-0035）

**这一节不是从"下一步该做什么"来的，是从一次排查偶发红灯来的**——而它翻出来的东西比排查本身重要。

### 109.1 怎么发现的（以及为什么它现在才被发现）

把 §107 之后那次全量跑里的两条偶发红灯查到底，结论分成两半：

| 红灯 | 结论 |
|---|---|
| `test_cli_search.py` 的两条（`test_managed_only_...`、`test_rebuild_is_the_protocol_spelling_of_a_full_refresh`） | **测试卫生问题**：`data_root` 夹具用一个**固定路径**（会话临时目录下的 `cli-search-data-root`），而 `test_rebuild_...` 会往里写一个**固定文件名**的脚本；另一条用例读的是**绝对**的 crawl 计数（`matched == 2`）。于是"上个会话留下的文件"或"用例顺序不同"就会让它红——**会因为正当理由变红的检查的反面：无缘无故变红的检查** |
| `test_l1_transaction.py::test_concurrent_commits_keep_a_single_active_binding` | **真实的产品缺陷**（见下），而且它**单跑也会红**：隔离临时目录后只跑这一个用例，60 次里红 1 次 |

**它一直绿的原因**是时序：那条守卫只有在 A 的回滚恰好落在 B 提交之后才红，而窗口很窄——**一条靠时序
才绿的守卫，对这个性质来说不是守卫**。确定性复现之后就一目了然了。

**第三个发现是排查过程自己带出来的**。为了在"干净副本"上验红，一趟并行工作把 checkout 复制到了
**它自己内部**（目标是 `cli/tests/.tmp/` 下的一个目录），于是副本里又有一份 `.tmp`、再一份……
直到路径长度把文件系统顶住。后果不是那份副本，而是它让
`test_the_bytes_on_disk_are_the_ones_the_contracts_claim` 以一个 `FileNotFoundError` 报错，
路径长达数千字符——**一条关于"字节契约"的判据，因为一个和字节毫无关系的原因变红**。
读那条判据的遍历才发现：跳过集合里**早已有** `.tmp`，但遍历用的是 `REPO.rglob("*")` **再**过滤，
也就是说它必须先**进入**它声明要跳过的目录。**同一个形状又出现了——读者比它守的那件事读得更宽。**

### 109.2 缺陷本身

让提交 A 在 `ACTIVE_BOUND` / `EXPOSED` 之间被打断，让提交 B（同 key、不同版本）完整提交，再 `repair` A：

```text
A=ROLLED_BACK   B=FINALIZED   活动绑定数=0
```

**A 的回滚把 B 已经提交的绑定一起拿掉了。** 三行代码里两个缺陷，而这三行在**两个 runner 里各写了一遍**：

1. `registry.clear_active_binding(key)` 停用该 key 的**每一行**活动绑定，不只是本事务装上的那一行；
2. 重新激活用的是 `tx["generation_before"]`，而那是 `journal.create` 记下的**注册表全局** generation
   （`journal.py:134`），不是这把 key 上一行的 generation——中间只要别的 key 提交过一次，
   `(key, generation_before)` 就指不到任何一行，**回滚静默地什么都没恢复**。

### 109.3 裁定与实现

回滚的语义被写成**一个**共享实现 `tx/rollback.py`（`revert_own_activation`），两个 runner 都调它：
活动行不是本事务的 → **不碰**；没有活动行 → **不碰**；是本事务的 → **只**停用自己那一行，再激活该 key
中 generation 严格小于本行且最大的那一行（没有就诚实地保持"无活动绑定"）。`generation_before` 保留原意
（journal、`repair`、`cli.py` 都在读），只是**不再**用来辨认被顶掉的那一行。

### 109.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把 `revert_own_activation` 换回 `clear_active_binding`（原状） | 红 | ✅ 红：**恰好一个活动绑定、且是 B 的**这条断言失败（`0 != 1`） |
| 用 `generation_before` 而不是"本行之下最大的那一行" | 红 | ✅ 红（两个 key 的交错用例：回滚后该 key 没有活动绑定） |
| 活动行属于别人时仍然动手 | 红 | ✅ 红（赢家的绑定消失） |
| 把树遍历换回 `rglob` + 过滤（原状） | 红 | ✅ 红（实测跑过一遍变异：那条判据 `1 failed`；改回剪枝后绿） |
| 真实状态 | 绿 | ✅ 绿 |

新增的是**确定性**用例（驱动那个交错，不靠时序）：A 被打断 → B 完整提交 → `repair` A；
外加一条两个 key 的交错，专门覆盖第 2 个缺陷；再加一条普通路径，证明"没有别人插手时前任照旧被恢复"。
打断只发生在**测试一侧**的接缝上，产品代码里没有为测试开的门。

树遍历那条判据的验红方式值得记一句：**复现原来那次失败需要一个"意外递归"那么深的路径**，而测试不该
去造那种东西。所以改为**注入拒绝**（与 `caps/identity.py` 处理"token 读不到"是同一个接缝思路）：
凡是列 `.tmp` 下的东西就抛错，遍历必须**因为从不开口问**而无所谓。断言里那句
`refused == []` 才是"没有进入"的判据——一个"进去了但把错误吞掉"的 `os.walk` 仍然会去 `scandir`，
它会红。

### 109.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **939 → 945**（+6：三条确定性回滚用例 + 两条搜索夹具卫生用例 + 一条"不进被跳过的目录"判据） |
| 审计检查（`test_l0_consistency.py`） | **101 → 102**（新增的是这一条树遍历判据，它住在这个模块里） |
| 产品代码 | `tx/simulate.py`、`tx/artifact.py` 的重复回滚各删一份，改调 `tx/rollback.py`（新文件） |
| 判据自身的读取面 | `test_l0_consistency.py` 的树遍历改为**进入前剪枝**（`os.walk` + `dirs[:]`），不再读它声明跳过的目录 |
| schema / 对外输出 / 退出码 / 语料 | **一处没动** |
| 新增 ADR | **ADR-0035** |

**两个阶段在同一次提交里落地**，理由是一条关于判据本身的事实：计数判据量的是**工作树**，
所以"两个阶段并行完成后分别提交"做不到自洽——先提交的那个，它的 AGENTS.md 计数已经包含了后一个阶段
的文件。要么把文件搬来搬去，要么承认一轮可以有两个阶段。这里选后者，并在两个阶段的记录里都说清楚。

### 109.6 如实记录的边界

1. **规则 3 有一个已知弱处**：它重新激活的是"本行之下最大的那一行"，不一定是"本事务开始时活动的那一行"
   ——一个在提交前被**故意**停用的前任会被回滚重新激活。要记下确切的那一行需要
   `transaction.schema.json` 里今天没有的字段，那是契约变更，不是缺陷修复，所以**不做**，写在这里。
2. **那条已有的并发守卫仍然存在**，但它不再是这个性质的证明；证明在三条确定性用例里。
3. **测试卫生那两条改的是夹具，不是产品**：它们与 §108 的计数判据无关，却是"偶发红灯"的另一半来源——
   不修掉它们，下一轮的偶发红灯还会被当成"时序问题"。
4. **8 次连续全绿**是这一阶段的验收口径（而不是"跑一次绿"）：偶发问题的修复必须用"不偶发"来证明。

### 109.7 实施顺序

1. 先分类：两条是测试卫生、一条是产品缺陷——**不要**把产品缺陷和夹具问题一起"重跑几次就好了"；
2. 隔离复现（自己的临时目录、只跑一个用例、60 次）拿到概率，再写出确定性复现；
3. 顺着确定性复现读代码，找到两个缺陷与"同一段被写了两遍"这件事；
4. 冻结语义（三条规则），抽成一个共享实现，两个 runner 都改调它；
5. 加确定性用例（交错 / 两个 key / 普通路径），并用"换回旧实现"验红；
6. 修搜索夹具的卫生问题；
7. 回写计数；写 ADR-0035 与本记录；跑 8 次全量；与 §108 同一次提交。

## 110. P2 第二阶段：进程内 loopback harness——`broker-response` 的第一个生产者（ADR-0036）

### 110.1 这一阶段补的是哪一半

§108 让线路面有了客户端：能造 `broker-request`、能读 `broker-response`。但**没有任何东西回答请求**
——没有 broker 进程、没有 named pipe、测试之外没有生产签发方（ADR-0025 的 D1）。于是四个 operation
一条端到端路径都没有，`broker-response` 也从来没有被真正**产出来**过（ADR-0026 的"没有写者"）。

### 110.2 交付

`cli/tests/fake_broker.py`（**测试路径**模块，不是产品件）+ `cli/tests/test_l2_fake_broker.py`（35 条）。
`serve(request, *, root=…, security_mode=…, clock=…, injector=…)` 把请求当**输入**校验
（`validate_document`），按 `operation` 分派到**已有的进程内实现**，返回前自校验（`validate_self`）。
`DISPATCH` 是分派表的唯一来源：

| operation | 调用的进程内实现 |
|---|---|
| `commit_plan` | `tx/simulate.py` 的 `SimulationRunner.commit`（fake-fixture 后端；计划与 token 从 `plan_ref`/`approval_ref` 指向的文件读出） |
| `recover_transaction` | `tx/journal.py` 的 `load_context`+`classify` → `tx/simulate.py` 的 `repair` |
| `probe_root` | `root.py` 的 `open_root`（卷守卫开着）+ `caps/acl.py` 的 `capture_acl` |
| `gc_apply` | `caps/lifecycle.py` 的 `apply_gc_plan` |

**拒绝是返回的文档，不是异常**：篡改计划（改后重算 hash → `INVALID_APPROVAL`(4)；改后不重算 →
`INVALID_PLAN`(7)）、重放 nonce（`APPROVAL_REPLAYED`(4)）、gc 载荷在批准之后变了
（`DIGEST_MISMATCH`(7)，status `failed`）、计划/批准缺失（`NOT_FOUND`(1)）、越出 root
（`PATH_ESCAPES_ROOT`(8)）、没有日志（`JOURNAL_TRUNCATED`(6)）、请求本身非法（`INVALID_INPUT`(8)）。
每一条都被断言为**一份通过 `broker-response` 校验的文档**。只有"答案是 schema 拒绝的文档"（实现缺陷）
才抛 `HarnessDefect`——这是 AGENTS.md §7 那条规则的例外，也是唯一例外。

### 110.3 它明确不是的三件事

**不是 broker**：不校验调用方的 token，`client` 块原样当"自述"收下（可以写任何 SID/pid/完整性级别）。
**不是 ACL 边界**：不建 ACL、不提权；`probe_root` 只是**观测** DACL，用的是 `doctor` 同一个只读调用。
**不是信任边界的证明**：一次绿跑只说"四个 operation 能从请求文档驱动、并回答一份 schema 接受的文档"。
恒为 `policy_only` + `same_user_can_bypass`，对"要求 protected 模式"**拒绝**（`PRIVILEGE_REQUIRED`(5)）
而不是改标签——这条拒绝本身是测试里的一格（删掉模式守卫 → 红）。

### 110.4 裁定：`status` 由 `reason_code` 推出（ADR-0036）

四值 `status` 与不枚举的 `reason_code` 之间原本没有任何已发布规则。现在有一条：退出码 0 → `ok`；
`RECOVERY_REQUIRED` 点名 → `recovery_required`（**退出码 6 同时承载 `JOURNAL_TRUNCATED`**，那是失败）；
其余按"开跑之前裁定 / 开跑之后出错"分 `rejected` / `failed`。第一版把 `INSTANCE_CONFLICT` 放在"拒绝"
里，而它由 `_fail` 在操作**开始之后**发射——按这条规则它是 `failed`，已移出并在表旁写明理由。

### 110.5 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 删掉模式守卫 | 红 | ✅ 红（`test_the_harness_refuses_to_answer_as_protected_machine`） |
| 让 `_plan_and_token` 忽略磁盘上的 approval | 红 | ✅ 红（8 条） |
| 把 `INSTANCE_CONFLICT` 放回"拒绝"集合 | —— | 没有测试钉这个码的 status：这是**判断**，写进 ADR-0036 而不是假装有判据 |

### 110.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **945 → 980**（+35：harness 的 35 条） |
| 审计检查（`test_l0_consistency.py`） | **102 → 102**（不变） |
| golden 语料 | **37 → 37**（**本阶段不加**，理由见 110.7-1） |
| schema / 退出码 / 对外输出 | 一处没动 |
| 新增 ADR | **ADR-0036** |

### 110.7 如实记录的边界

1. **`broker-response` 还没有逐字节语料。** 它的**形状**有一份比语料更强的验收面：35 条测试里每一份响应
   都过已发布 schema。但**字节级**的可复现示例还没有，理由是**还没有量过**四个响应在规范化时间戳与临时
   根路径之后是否逐字节确定；"没量过就写进语料"正是本项目一直在防的那类假话。它是下一阶段的第一件事。
2. `transaction_id` / `state` 对 `probe_root` / `gc_apply` **恒为 `null`**：它们没有事务行。编一个 id
   等于替没有写过的历史作证。
3. evidence 里**不放** owner/trustee SID：那是机器指纹，而远端是 public（AGENTS.md §9）。
4. harness 住在 `cli/tests/`：测试替身不是产品文档的写者，所以 `broker-response` 仍属 `unbuilt`
   ——§108 钉下的这条语义在本阶段第一次被**实测**（取值表那一格没变）。
5. "拒绝 vs 失败"是本阶段的**新契约**，其中 `RECOVERY_REQUIRED` 与 `JOURNAL_TRUNCATED` 同为退出码 6
   却分属两个 status——刻意的，见 ADR-0036。
6. **路径拼写**：把全量跑在"临时根含 8.3 短名"的位置上（例如 `%TEMP%` 拼作 `C:\Users\PROFIL~1\…`）
   会让四条比较路径字符串的用例变红，产品里没有任何 `GetShortPathName`/8.3 处理。机制未定，已单独派查，
   不混进本阶段。

## 111. 路径拼写：声明按规范形式比较（ADR-0037）

### 111.1 怎么发现的

§110 收尾时把推送后的仓库克隆到两处跑全量：`D:\` 下 980 passed / 0 failed；`%TEMP%` 下（本会话拼作
`C:\Users\PROFIL~1\…`）**4 failed**，而且红的名字在两次运行之间还会换。机制是 `Path.resolve()` 把 8.3
别名展开成长名，而产品的每个写入者（`paths.canonicalize`）与爬取（`caps/search.py` 的 `_canonical_root`）
都调用它，测试夹具却交出了**原始**拼写——同一个目录两种拼写，于是断言只在"临时根含短名"的位置上红。

### 111.2 一半是测试健壮性，一半是真实的产品不一致

| 现象 | 归属 |
|---|---|
| `test_cli_steward.py` 的路径比较；`test_cli_search.py` 里直接写注册表行的两个用例 | **测试健壮性**：夹具交出产品永远不会写的拼写 |
| `search explain` 与 `search` 对**同一个索引**给出不同回答（前者说实时遍历、后者从索引答） | **真实的产品内部不一致**：一个用未解析的根问覆盖，另一个用解析后的根问 |

`doctor` **不受影响**（它比卷序列号与观测到的版本/架构/入口点，不比路径字符串）。

### 111.3 裁定与实现

见 ADR-0037：声明在比较前 `resolve()` 一次（分类器每注册表行一次，不是每结果一次）；`explain` 的覆盖问题
复用 `search` 的 `resolve_roots`；`adopt` 的相等比较同样规范化；**不动** `canonicalize()` 与
`_canonical_root()`（长形式才是规范形式）。夹具改成交出 `resolve()` 之后的值。

### 111.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 分类器不规范化声明（原状） | 红 | ✅ 红 |
| `explain` 用原始根问覆盖（原状） | 红 | ✅ 红（两条一起跑：修好后 2 passed，换回旧代码 2 failed） |
| 真实状态 | 绿 | ✅ 绿；**原始复现**（`AIROOT_TEST_TMP` 指向短名临时根）从 4 红变成 **56 passed / 1 skipped** |

**验红本身修正了一次判断**：守卫最初拿 `\.` / `..` 段当"非规范拼写"，实测**覆盖问题那一半不会红**
（链路上某处把这类段折叠了），于是改成**正斜杠**拼写——`resolve()` 规范化它，而
`caps/searchindex.covers` 只做小写与去尾部反斜杠。**一个不能变红的检查不是检查**，这一条是当场量出来的。

### 111.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **980 → 982**（+2：分类器声明拼写、`explain` 与 `search` 一致） |
| 审计检查（`test_l0_consistency.py`） | **102 → 102** |
| 产品代码 | `cli/app/airoot/cli.py` 三处（分类器声明、`explain` 覆盖问题、`adopt` 认领比较） |
| 夹具 | `test_cli_steward.py` 与 `test_cli_search.py` 交出规范拼写 |
| schema / 退出码 / 语料 / 对外输出 | 一处没动（golden 重生后逐字节无漂移） |
| 新增 ADR | **ADR-0037** |

### 111.6 如实记录的边界

1. **`adopt` 那一处没有被守卫覆盖**：比较的拼写修了，但没有写一条会因为"非规范数据根行"而红的用例——
   触发它需要一个能通过白名单证据谓词的真实 PE（合成 PE 会被 `CAPABILITY_NOT_DECLARED` 挡住）。这是
   **已知缺口**，不是"已验证"。
2. **非规范注册表行今天只能由测试直接写入造出来**：CLI 的每个写入者都规范化。所以这一阶段修的是**潜伏**的
   不一致，不是用户已经踩到的 bug；但它一旦被第二个写入者（broker 侧注册？）触发就是活的。
3. `resolve()` 对**不存在**的尾部不做解析（非 `strict`），所以"声明指向一个还不存在的东西"这一类拼写差异
   **不在**本阶段的处理范围内。

## 112. `broker-response` 的逐字节语料（ADR-0038）

### 112.1 先量确定性，再写语料

§110.7-1 立的规矩：**没量过就不写进语料**。量法是在两个**独立的根**上各造一份同样的请求与答案，然后比较
规范化之后的 JSON：

| 答案 | 两个根之间逐字节 | 差异在哪 |
|---|---|---|
| `gc_apply` | ✅ 稳定 | — |
| `refused_missing_plan`（`NOT_FOUND`） | ✅ 稳定 | — |
| `probe_root` | ✅ 稳定 | — |
| `commit_plan` | ❌ | `transaction_id`、`evidence`（含 `approval_id`） |
| `commit_interrupted` / `recover_transaction` | ❌ | `transaction_id`（以及中断那份的 `evidence`） |

`transaction_id`/`approval_id` 不是随机的，而是**由内容推出的**——`plan_id`、`nonce`、`approval_id` 都是
入参（`golden.py` 给既有的事务 fixture 早就这么钉了）。钉死之后四份都能逐字节再生，由
`test_golden_fixtures_reproduce_exactly` 每次跑测试证明。

**`probe_root` 不进语料**：它的答案是**机器观测**（ACL 条目数与 DACL 摘要）。它在这台机器上稳定，但换一台
机器就不同——fixture 要么嵌入某台机器的数字，要么撒谎。**"在这台机器上稳定"不是"可复现"**，这是它与其余
三份的区别，也是它被排除的唯一理由（记录在案，不是遗漏）。

### 112.2 顺带修掉一个真实的泄漏

`probe_root` 的 `acl_trustees` 把排好序的受托者 SID 列表当 `detail` 发出去，而同一个函数的 docstring 写着
"SID 不进证据"——**一份文档在跟自己的说明打架**。返回的文档里带机器身份违反 AGENTS.md §9，并且让它的答案
永远不可能成为可复现语料。改成只报条目数。守卫是"整份答案的 JSON 里不出现 `S-1-`"。

**守卫的第一次写法没有红方向**：把泄漏那行放回去，判据仍然绿——因为**这台机器的 `capture_acl` 根本报不出
受托者 SID**（实测）。所以守卫改成**注入一份带 SID 的快照**（与 `caps/identity.py` 处理"token 读不到"是
同一个接缝思路），并加一条"仍然要报条目数"的断言，免得第一条因为字段消失而通过。改完：放回泄漏 → 红，
修好 → 绿。

### 112.3 语料的两个来源（ADR-0038）

`broker-response` 的唯一生产者是**测试路径**的 harness，而"核心打印的文档 ⟺ 语料"是精确相等。所以语料有
**第二个 map**（`test_golden.py` 的 `SCHEMA_FOR_HARNESS_FIXTURE`），孤儿判据认**两个** map——一个 fixture
只能有一个出处，而"这是谁造的"在文件里读得出来。

### 112.4 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **982 → 984**（+1 身份泄漏守卫、+1 harness 语料过 schema） |
| golden 语料 | **37 → 41**（+4：提交 / 恢复 / 回收 / 一次拒绝） |
| 审计检查（`test_l0_consistency.py`） | **102 → 102**（孤儿判据改成认两个 map，条数不变） |
| schema / 退出码 / 对外输出 | 一处没动 |
| 新增 ADR | **ADR-0038** |

### 112.5 如实记录的边界

1. **`probe_root` 没有字节级语料**，理由见 112.1（机器观测）。它的形状仍由 §110 的 35 条测试覆盖。
2. **`acl_digest` 仍然在 `probe_root` 的答案里**：摘要本身不泄漏 SID（不可逆），但它**随机器不同**，所以
   即使将来要给它一份语料，也必须先规范化这个字段（`golden.py` 已有 `<VERSION>` 式的规范化先例）。
3. **四份语料只覆盖"成功 + 一种拒绝"**：另外七条拒绝路径（`INVALID_APPROVAL`/`INVALID_PLAN`/
   `APPROVAL_REPLAYED`/`DIGEST_MISMATCH`/`PATH_ESCAPES_ROOT`/`JOURNAL_TRUNCATED`/`INVALID_INPUT`）有测试、
   没有语料。按同样的口径，它们**只在被量过确定性之后**才会进来。
4. **那段代码插错位置这件事**记在 ADR-0038 里：抓住它的是逐字节再生检查，不是任何文档判据——**文档判据
   守不住"我改坏了别的 fixture"**。

## 113. 受保护边界的三块地基（ADR-0039 / ADR-0040）

**这一阶段刻意只做「受保护边界需要用、但不需要先有那个边界就能建成并验证」的东西**，三条并行，加一条集成。

### 113.1 三条并行线

| 线 | 交付 | 它**不**是什么 |
|---|---|---|
| 真实 Ed25519 | `airoot/crypto/ed25519.py`（RFC 8032，**纯 Python**，不引依赖）+ 85 条测试，验收面是 RFC §7.1 的**五条官方向量**逐条过（密钥导出、签名逐字节重现、verify 接受），另加两条把 §7.2 ctx / §7.3 ph 当**不同算法**拒绝的用例 | **不是密钥存储**：私钥的受保护的家还不存在；`verify` 对任何畸形输入返回 `False`、绝不抛 |
| 读**别人**的 token | `caps/identity.py` 的 `probe_process(pid) -> ProcessIdentity`（SID / 完整性级别 / 是否提权，失败即数据）+ 11 条测试：复用既有 token 机制（为此把三处读法抽成一个共享 helper，**原有 16 条测试一字未改**，并由"让共享 helper 提前停下"的变异证明两条路径共用同一个实现） | **是观测，不是授权**：读到一个提权的调用方不等于它被允许——那由 broker 的策略决定，而策略今天不存在 |
| ACL 写一侧 | `caps/acl.py` 的 `acl_baseline`/`verify_baseline`/`apply_baseline`/`restore_acl`（`SetSecurityInfo`，`ctypes`）+ 16 条测试 | **只作为库**：不接任何动词、不接任何 schema。没有 broker 的今天接上去，等于给同用户进程一条改 DACL 的路 |

### 113.2 集成线：keyring 从"裸材料"改成"记录"

**旧形状有一个真实的洞**：由 token 自己那个 `signature.algorithm` 字段决定跑哪种校验，于是同一份注册材料会
被按 token 的声明**重新解释**（登记给 HMAC 的密钥被当成 Ed25519 公钥用）。现在每一条 keyring 记录说清
`algorithm`，两边不一致即 `INVALID_APPROVAL`(4)，两个方向各有测试；`load_keyring` **拒绝**旧形状而不是猜它的
算法（本仓库没有任何已落盘的旧 keyring，实测——只在测试运行时写）。

**`ed25519` 的"未实现"分支删掉**：签名不对 → `INVALID_APPROVAL`(4)；**整个 root 没有 keyring** →
`PROVENANCE_FAILED`(7) 且带 `ISSUER_PENDING`。这两件事以前用同一句话表达，读起来像两个互不相干的缺口。
**而"校验真了"不等于"本机可以批准"**：核心仍然不会签发，五条消费路径在真机上**仍然**停在
`PROVENANCE_FAILED`。

**顺带修掉一个泄漏**：`verify_signature` 的三个拒绝里，`unknown signing key` 原本不带任何 evidence——"我的
keyring 是不是我以为的那一个"这个唯一有用的问题在文档里没有答案。补上（key id 不是秘密，它本来就在 token 里）。

**一条必须写下来的安全边界**（来自 §113.1 那条线的发现）：RFC 8032 **不**拒绝小阶公钥，退化公钥下
`(R=[S]B, S)` 对**任意**消息都能通过——所以**验证用的公钥必须由边界钉住，不能取自被检查的文档**。今天
keyring 只是 root 里一个文件，任何能写 root 的进程都能换掉它；这正是受保护阶段要关的口，也是本次**没有**把
keyring 或公钥挪进 token / 调用方参数的唯一理由。

### 113.3 ACL 写一侧的两条边界（ADR-0040）

**非空基线一律保护式写入**（不带 `PROTECTED_DACL_SECURITY_INFORMATION` 时，一条显式 ACE 的基线在 `%TEMP%`
下被存成 **12** 条——不保护，基线根本落不下去）。**空基线拒绝**：保护式空 DACL 是一扇**单向门**，实测在
**提权 token** 下 `WRITE_DAC` 也被拒、`restore_acl` 被拒、目录删不掉；逃生口是"所有者 + 备份/还原特权"，
而那正是本 build 没有的东西。**同一个函数里 NULL DACL 仍然是被报告而不是被近似的那种姿态。**

### 113.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| Ed25519：`verify` 直接 `return True` / 删掉 `s >= L` 检查 / 取反基点 x / 删掉平方根校验 | 红 | ✅ 分别 8 / 1 / 36 / 7 条红 |
| Ed25519：删掉"非规范编码"检查 | 红 | ✅ 红——**第一次它是绿的**，说明我只在 `verify` 层测是测不出来的；补了一条直接测解码层的用例后才红 |
| 身份探针：让共享 helper 在第一个失败类别后就停 | 红 | ✅ 新目标用例与**原有**的自己-token 用例一起红（两条路径共用实现的直接证据） |
| 身份探针：失败时报 `False` / 不关句柄 | 红 | ✅ 5 条 / 1 条 |
| ACL：把保护标志去掉 | 红 | ✅ 红（非空基线落不下去） |
| keyring：把算法绑定检查删掉 | 红 | ✅ 红（两个方向各一条） |
| 真实状态 | 绿 | ✅ 1103 passed（唯一红的是计数守卫，本阶段收尾时同步） |

### 113.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **984 → 1104**（+85 Ed25519 · +11 身份探针 · +10 审批/Ed25519 集成 · +14 ACL 写一侧） |
| 审计检查（`test_l0_consistency.py`） | **102 → 102**（不变） |
| golden 语料 | **41 → 41**（不变，逐字节再生无漂移） |
| 新增模块 | `cli/app/airoot/crypto/`（`ed25519.py` + `__init__.py`）；新增测试文件 2 个 |
| 契约变更 | keyring 记录格式 + 算法绑定（ADR-0039）；ACL 写一侧的两条边界（ADR-0040） |
| 新增 ADR | **ADR-0039**、**ADR-0040** |

### 113.6 如实记录的边界

1. **这不是那条边界。** 没有 broker、没有 pipe、没有对调用方 token 的任何校验；`probe_process` 只给读一侧，
   ACL 写一侧还不接任何调用者。三块地基都验证了"能建成"，没有验证"被保护"。
2. **`application_id` 仍然没有着落**：探针只读 token 事实，`client` 块仍然只是自述。
3. **九个测试目录留在 gitignored 的临时目录里删不掉**：它们带着保护式空 DACL，是本阶段"空基线不可回收"的
   现场证据（提权 token 也打不开）。新运行不受影响（夹具父目录名带每次运行的标签）。
4. **ACL 写一侧的调用者存活判据不在这一层**：`SetSecurityInfo` 成功不等于调用者还能打开那个目录；一条没有
   匹配调用者 allow 的基线会让调用者自己出局。这条策略属于 broker，今天不存在，因此**不得**在边界存在之前
   给它接调用者。
5. **对象/回调 ACE 写不了**：真实数据根的 DACL 里可能有这类 ACE，本模块对它们的 apply/restore 都抛
   `ValueError`，即**这类目录的 ACL 本模块还原不了**——只在合成条目上测过。
6. **Ed25519 不是常数时间的**，`sign` 用私钥时有数据相关的加法链，**当时**因此判断"不得用它保管长期
   签发密钥"（P2/Rust 才拥有那把密钥）；`verify` 只处理公开数据。**这条已于 ADR-0046 被推翻，理由写在那里**：
   它防的是**本机进程的计时观测**，而 ADR-0045 把安全归属移到使用方之后，签发方不再需要抵抗本机进程，
   **约束的前提消失**。今天那把密钥由本模块的 `sign` 签，而"同用户进程读得到、也能自己签"是**已知且接受**
   的后果。原文保留在此，因为它是当时正确的判断。
7. **本阶段没有动 schema、退出码与 golden 语料**：改的是 keyring 的**格式**、一处拒绝消息、以及三块新地基。

## 114. 受保护边界要问的第一个问题：谁在问（ADR-0041）

§113 打好了三块地基。§114 接着做 `docs/broker` §3 要求的第一件事——**校验客户端进程的 token**——但**只做
那条判定**：named pipe 与提权服务没法在它自己不存在的时候被验证，而"谁在问、能不能问"可以。形状沿用 §113：
**建成库、不接动词、把量到的东西写下来**。

### 114.0 这一阶段交付了什么

| 项 | 内容 |
|---|---|
| 观测补齐（`caps/identity.py`） | `probe_process` 从三件事扩到**九件**：`sid`/`integrity`/`elevated` + `elevation_type`/`session_id`/`is_app_container`/`app_container_sid`/`creation_time`（外加 `pid`）。前三件仍走 `_read_token_facts`（与读**本进程**共享同一个实现），新增的五件走新的 `_read_peer_facts`，**只**被 `probe_process` 调用——`client` 块的形状由已发布 schema 钉死，不跟着长 |
| 两道交叉核对（同上） | `elevated` ⟷ `elevation_type`、`TokenSessionId` ⟷ `ProcessIdToSessionId`：两个**独立读法**不一致时报"**这个事实读不出来**"（该字段置 `None` + 写明两个读数），而不是取其中一边；能读到的那个仍按原样报 |
| 判定（`broker/policy.py`，新） | `admit_caller(identity, expectation)`：`observation_incomplete` / `app_container_caller` / `sid_not_allowed` / `integrity_below_minimum` / `pid_reused` 五条规则，共用一个新码 `CALLER_NOT_AUTHORIZED`(5)，`details.rule` 指认是哪一条；规则 id 与理由在 `REFUSAL_REASONS` 里一一对应（与 `protocol.py` 的 `ADDITIONAL_REQUIREMENT_REASONS` 同一个做法） |
| 契约 | 新增 reason code `CALLER_NOT_AUTHORIZED`(5)（ADR-0041）：`exits.py`、权威表、`references/reason-codes.md`、golden 的 `reason_code_table.json`（96 → 97 个码） |

### 114.1 量到的不对称：请求里那个**必填**的 `client` 块，答不了"谁在问"

`broker-request.schema.json` 的 `client` 块有**四个**字段且 `additionalProperties: false`；而服务端对同一个
调用方能读到的**事实**有**九个**。两个结论：

1. `sid`/`pid`/`integrity` 是**冗余**的——服务端自己就能读，而且**只能信自己读的那一份**；
2. `application_id` 在普通 Win32 进程的 token 里**没有对应物**：没有任何一个 `TOKEN_INFORMATION_CLASS`
   回答"这是哪个程序"，只有 AppContainer 进程带得动一个 application identity（`TokenAppContainerSid`；
   本机实测 `is_app_container=False`，所以对普通调用方这个字段**永远只是自述**）。也就是说
   `docs/broker` §3 写的"校验 application identity"，对普通调用方**无从校验**——真话是"这个事实在 token
   里不存在"，不是"它和声明一致"。**一个必填字段，要么冗余、要么是自述，而文档没有说它是哪一种。**

于是本阶段把"自述不参与判定"从散文变成**形状**（ADR-0030 的同一做法）：判定第一个参数是
`ProcessIdentity`——它**没有** `application_id` 字段；`admit_caller` 恰好两个参数，没有
`client`/`claim`/`request`。三件事各由一条测试钉住，其中一条是**谎报**：请求把 SID 说成允许集合里那一个、
并附上一个 `application_id`，而观测到的是别人 ⇒ **照样拒**。

### 114.2 量到一条**写错的不变量**——本阶段最值得记的一条

两道交叉核对里的第一条，原始写法是：**"`TokenElevation` 为真，当且仅当 `TokenElevationType` 是 `full`"**。
本机当场把它证伪：

```text
probe_process(os.getpid()) -> elevated=True, elevation_type="default", integrity=high
```

`TokenElevationTypeDefault` 的**定义**就是"这不是一个 elevated 或 limited 的 token"：它既覆盖标准用户
（`TokenElevation` 假），也覆盖**关掉 UAC 的管理员**（`TokenElevation` 真、完整性 `high`），本机是后者。
所以真话是一条**更弱**的蕴含：**`full ⇒ elevated` 真、`limited ⇒ elevated` 假，而 `default` 对两者都不作
约束**。按原来的写法，`elevation_type` 会在每一台关掉 UAC 的机器上被判成"读不出来"；而它又是判定**要求**的
事实之一，于是判定会把一个**完全合法的调用方**报成 `observation_incomplete`——**一个写错的交叉核对不是装饰，
它会把门焊死**。这正是"检查必须能验红、而且红的理由必须是对的"的反面：这一条**红得毫无道理**。

修法与验红：蕴含收窄到上面那两条；`default` 的两种搭配都**必须被接受**（四种组合各有测试），真矛盾仍然把
类型置 `None`、保留 `elevated`、两个读数都进证据。修完本机实测 `elevated=True, elevation_type="default"`、
**没有**矛盾证据行，且 `admit_caller` 对同一个调用方返回"准入"。**顺带记下这条教训的形式**：这条不变量是
**brief 里写下的**，施工方照着实现——它错在源头，而"实现与 brief 一致"的测试永远不会发现它；发现它的是
**一次手跑的真机探针**。

### 114.3 顺带量到的一处计数缺陷：那份"常驻检查"数一直被少数四个

`test_the_corpus_counts_are_the_same_everywhere` 用**源码推导**（正则匹配
`@pytest.mark.parametrize(...[...])`）算这个模块"收集了多少项"，而模块里最大的那个参数化取的是
`sorted(_search_response_enums())`——**一个在 import 时求值的调用**。于是推导器只数到一个装饰器、漏掉另一个，
报 **102**；pytest 实际收集 **106**。文档里的"102 项常驻跨工件一致性检查"因此连着几个阶段是错的，而**没有
任何东西比较这两个数**（唯一的消费者是文档）。修法是把权威换成**收集本身**：`_audit_check_count(request)`
读 `request.session.items`，并把它从 `test_the_corpus_counts_are_the_same_everywhere` 里拆成一条自己的检查
——因为 `-k`/`-m` 会收窄收集，混在一起会让过滤器把另外两个计数也一起弄成噪音（拆分后那条在被过滤时
**skip 并说明原因**，全量运行时才比较）。另加一条 `test_the_audit_count_cannot_be_read_off_the_source`：
只要那个参数化还是"算出来的"，源码就**不可能**知道这个数——它一旦变成字面量，这条就红，提醒重新裁决，
而不是让两个数并存。修完：**102 → 108**（本阶段自己又加了 2 条检查）。

### 114.4 新增一个码，以及第一次分清的"有写者"与"有路"

量过：冻结的码表里**没有一条**说的是"谁在问"——`ACL_MISMATCH` 讲目录的描述符、`OWNERSHIP_REQUIRED` 讲
AIROOT 不拥有的 payload、`PRIVILEGE_REQUIRED` 讲"这个操作要一个你没有的权限"，三条都不是对**调用方**的判断。
于是新增 `CALLER_NOT_AUTHORIZED`(5)：落 5 是因为 0–9 里"操作需要调用方不具备的权限/授权"就是这一层，**而不是**
因为它与 `PRIVILEGE_REQUIRED` 同义——提权不会把 `S-1-5-21-…` 换成另一个 SID，"提权再试"不是它的下一步，
这句话写进了证据，也写进了 `references/reason-codes.md` 的退出码 5 一节（ADR-0027 的同一条教训）。

它同时暴露了那本速查的一个**信息缺口**：《这一版发不出来的码》的判据（`test_l1_reason_codes.py`）是"**除
`exits.py` 外，这个字符串在 `cli/app/airoot` 里还出不出现**"——也就是**有没有写者**；而 `admit_caller` 有写者、
**却没有任何动词能走到它**。这个码因此既不该进那张表（它有写者），也不该被写进 agent 的分支逻辑（它收不到）。
本阶段在 `references/reason-codes.md` 文末补了《有写者，但没有任何动词能走到》这一小段，并把 `ACL_MISMATCH`
的处置说明改准（§113 之后它不再是"写一侧还没做"，而是"写一侧是库、没有调用者"）。**这是"有写者"与"有路"
第一次分了家**——判据本身没有错，错的是读者会把它读成"能遇到"。

### 114.5 守卫与验红

每一格是**一次**实现改动，跑完恢复并重跑确认绿。观测那一组跑的是 `pytest cli/tests/test_l1_identity.py -q`。

| 变异 | 预期 | 结果 |
|---|---|---|
| 观测：删掉 `elevated` ⟷ `elevation_type` 交叉核对 | 红 | ✅ 2 条 |
| 观测：**把那条写错的不变量放回去**（"elevated 当且仅当 type 是 `full`"） | 红 | ✅ 3 条（含真机那条与 `default`+`True` 的用例）——**这一格就是 114.2 的证据**：错的规则也能被"测出来"，区别是它红得没有道理 |
| 观测：取 token 的 `TokenSessionId` 而不过 `ProcessIdToSessionId` | 红 | ✅ 1 条 |
| 观测：AppContainer 的 `app_container_sid` 悄悄返回 `None` | 红 | ✅ 4 条 |
| 观测：跳过 `GetProcessTimes` | 红 | ✅ 4 条 |
| 观测：让 `to_document` 重新吐出 `null` | 红 | ✅ 3 条（其中一条证明那份文档**真的**会被 `broker-request` 拒，而拒绝消息会指向调用方——这正是要修的那个错方向） |
| 观测：把读不出来的 `TokenElevationType` 圆成 `default` | 红 | ✅ 1 条 |
| 观测：把非 0/1 的 `TokenIsAppContainer` 读成 `False` | 红 | ✅ 1 条 |
| 观测：进程**不在** AppContainer 里仍然去要 `TokenAppContainerSid` | 红 | ✅ 1 条 |
| 判定：删掉 `observation_incomplete` 规则 | 红 | ✅ 9 条 |
| 判定：删掉 `app_container_caller` 规则 | 红 | ✅ 3 条 |
| 判定：删掉 `sid_not_allowed` 规则 | 红 | ✅ 5 条 |
| 判定：删掉"低于下限"的比较 | 红 | ✅ 9 条 |
| 判定：删掉"认不出的完整性词"守卫 | 红 | ✅ 6 条 |
| 判定：删掉 `pid_reused` 规则 | 红 | ✅ 4 条 |
| 判定：**从必读集合里去掉一个事实**（`elevation_type`） | 红 | ❌ **0 条** → 修完后 3 条（见下） |
| 判定：阶梯比较反向（`<` 改 `>`） | 红 | ✅ 16 条 |
| 判定：把认不出的词当成 `low` 排序 | 红 | ✅ 6 条 |
| 判定：让 `creation_time` 无条件必读 | 红 | ✅ 3 条 |
| 判定：拒绝时不带 `details["rule"]` | 红 | ✅ 32 条 |
| 判定：SID 证据不再点名观测到的那个 SID | 红 | ✅ 1 条 |
| 判定：删掉一条 `REFUSAL_REASONS` | 红 | ✅ 1 条 |
| 判定：规则 id 打错一个字母 | 红 | ✅ 3 条 |
| 判定：多出第二个 `CALLER_NOT_AUTHORIZED` 抛出点 | 红 | ✅ 18 条 |
| 判定：给签名加一个"声明形状"的参数 | 红 | ✅ 1 条 |
| 判定：模块里出现 application id | 红 | ✅ 18 条 |
| 计数守卫：把 `AGENTS.md` 的 108 写成 107 | 红 | ✅ `AGENTS.md states [107, 108] audit checks; this module collected 108` |
| 计数守卫：把那个"算出来的"参数化改成字面量 | 红 | ✅ `test_the_audit_count_cannot_be_read_off_the_source` |
| 真实状态 | 绿 | ✅ 见 114.6 |

**有一格"改了却不红"，它是本阶段的第二个真发现**：判定那一组里"从必读集合里去掉一个事实"第一次跑出 **0 红**
（54 绿）。原因不是那条规则没被检查，而是**检查在照镜子**：那条按字段参数化的性质测试**从 `REQUIRED_FACTS`
自己取用例**，于是删掉一个事实的同时也删掉了它自己的用例，而没有任何别的东西钉住这个集合——`elevated` 与
`session_id` 有同一个洞。修法是把集合用**字面量**钉住（`REQUIRED_FACTS_EXPECTED` + 双向相等，连"`creation_time`
只在给了预期值时才必读"一起），再把性质测试与"这些字段真的存在"那条指向字面量；修完这一格红 **3** 条。
这一条与 ADR-0033 是同一族：**一个自我指涉的守卫看起来在检查，实际上只是在确认自己刚才写了什么**——它比
"没有守卫"更危险，因为它会让人以为那里有守卫。（这条教训单独立为 ADR-0042。）

观测那一组里另有两格值得单独看：把错的不变量放回去（红 3 条）与把读不出来的类型圆成 `default`（红 1 条）
——它们说明"**能验红**"与"**红得对**"是两件事：前者红得热闹，而它红的原因是**规则错了**。

### 114.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1104 → 1198**（**+94**）。**这一格的加数当时是错的，§115 量过之后改的是加数**：原文写 +120（+85 Ed25519 · +11 身份探针 · +10 审批/Ed25519 集成 · +14 ACL 写一侧），而那一刻（`63284b0`）的 `pytest cli/tests --collect-only` 是**正好 1198**，从 1104 起算即 +94。逐文件实测把这 94 拆得出来：`test_l1_broker_policy.py` **57**（随 `broker/policy.py` 一起在 `63284b0` 进来）、`test_l1_identity.py` 从 §113 的 20 涨到 **62**（+42，`probe_process` 读**别人** token 的那批）、`test_l1_ed25519.py` **85**、`test_l2_approval_ed25519.py` **10**、`test_l1_acl.py` 里写一侧那批 **14**——**四个加数各自都对，加起来却多了 26**，因为 `caps/identity.py` 的两半（§113 的 `probe_process` 与 §114 的"身份观测"）分属两次提交、却都记在同一个阶段小节里。不改掉而是写在这里：**一条被下一阶段实测推翻的加数，比一个被改平的数字有用**，而且它与 §54 是同一个缺口——数字只跟自己的副本对账，只是这次两份副本都是阶段记录 |
| 审计检查（`test_l0_consistency.py`） | **102 → 108**（**不是"加了 6 条"**：其中 4 条是那条推导器一直少数出来的） |
| golden 语料 | **41 → 41**（逐字节再生无漂移；`reason_code_table.json` 内容 +1 行） |
| reason code | **96 → 97** |
| 新增模块 | `cli/app/airoot/broker/policy.py`；新增测试文件 `cli/tests/test_l1_broker_policy.py` |
| 契约变更 | 新增 `CALLER_NOT_AUTHORIZED`(5)（ADR-0041）；`client` 块被明确为**诊断信息** |
| 新增 ADR | **ADR-0041**（边界要问的第一个问题）、**ADR-0042**（自指的守卫在照镜子） |

### 114.7 如实记录的边界

1. **这不是那条边界。** 没有 named pipe、没有提权进程、**没有任何动词走到 `admit_caller`**——它今天只被测试
   调用。读别人 token 的能力（§113）与"谁可以问"的判定都有了，缺的是**把它们接起来的那条线**。
2. **`allowed_sids` 与 `minimum_integrity` 从哪里来，本阶段刻意没有决定。** 它们只是显式入参：没有策略文件、
   没有默认阈值。**特别地，没有拿"根目录的 owner"当答案**——Protected machine mode 下 root 的 owner 很可能是
   Administrators，用它当"谁可以问"会把合法用户拒掉；这个来源要等 root 的声明与 pipe 的 DACL。
3. **判定的第一条规则是"不知道就是不允许"**，所以一个 token 读不出来的调用方会被拒。这是有意的（既有规则
   "不编造确定性"），但它的代价要写下来：**探针读不到的原因有五种**（pid 不存在、进程已退出、受保护进程、
   另一个用户、某个 token class 拒绝），判定把它们**一律**报成"不能决定"，而不是去猜哪一种。
4. **`pid_reused` 只在调用方给出了预期创建时间时才生效**：`creation_time` 是 `ProcessIdentity(t0)` 的一部分，
   而**谁在 t0 读的、到 t1 还能不能对应上**，本模块只做比较，不做重读——重读属于 pipe 那一侧。
5. **`fake_broker.py` 的 `REJECTION_CODES` 没有加这个码**，这是有意的：§110 把 `INSTANCE_CONFLICT` 从那张
   表里删掉的理由是"这个 harness 不会那么回答"，同一个理由在这里成立——harness 不调用 `admit_caller`，
   所以它答不出这个码。等它接上判定时再加，而不是先把码放进表里装作能答。
6. **本阶段没有给任何 schema、任何 CLI 动词、任何 golden 语料加东西**（除了那张 reason code 表的一行）：
   `client` 块的形状是已发布契约，**不因为服务端观测变宽而变宽**——这正是 114.1 那条不对称的处置。

## 115. 把线接起来：本机 named pipe（user compatibility mode，ADR-0043）

§113 给了"读**别人** token"，§114 给了"谁可以问"的判定，本阶段把两者接到**一条真实的线**上。权威是
`docs/broker` §2 与 §3：§2 明确允许"无 elevated Broker 的同用户模拟"，但要求**响应必须带**
`security_mode=policy_only` 与 `enforcement=same_user_can_bypass`；§3 规定 IPC 是用受 ACL 保护的 named pipe、
只接受本机客户端、并且**一次提交请求必须是一个完整 envelope，不能由客户端分段拼接安全字段**。

### 115.1 量到的一条：`PIPE_REJECT_REMOTE_CLIENTS` 不在 MSDN 说的那个参数里

"只接受本机客户端"（§3）在 Windows 上有现成的开关：`PIPE_REJECT_REMOTE_CLIENTS`（`0x00000008`）。
**MSDN 的 `CreateNamedPipeW` 页面把它列在 `dwOpenMode` 下，而这台机器不接受放在那里**——实测（两条独立路径，
一条 `ctypes`、一条 C# P/Invoke，所以不是 ctypes 的假象）：

| 放法 | 结果 |
|---|---|
| `dwOpenMode = 0x00000003`（duplex） | 建pipe 成功 |
| `dwOpenMode = 0x0000000B`（duplex\|0x8） | `INVALID_HANDLE_VALUE`，`GetLastError() = 87 (ERROR_INVALID_PARAMETER)`——**pipe 根本没被创建** |
| `dwOpenMode = 0x00000003` + `dwPipeMode = 0x00000008` | 成功，且 `GetNamedPipeInfo` 读回 `flags = 0x00000009`（server-end \| 0x8）；不带这个位时读回 `0x00000001` |

佐证在 Windows SDK 头文件本身：`WinBase.h` 把 `#define PIPE_REJECT_REMOTE_CLIENTS 0x00000008` 归在
"Define the **dwPipeMode** values for CreateNamedPipe" 一节里。**MSDN 与头文件不一致，而这台机器站在头文件
那一边；OS 说了算。** 两件事因此进了记录：(1) 按 brief 原样写，pipe 会在创建那一步就失败，而 `err=87`
**不是** DACL 的问题——一个看起来像权限问题的错误码，实际是参数位置；(2) **这个位是可以本地验证的**，
`GetNamedPipeInfo` 的 flags 会把它读回来（0x9 对 0x1），所以"我们设了它"可以变成"OS 说它设了"，不必只写在
散文里。（本阶段之前我把这一条判成"本地不可验证"，那句话被这次实测推翻了——记在这里，因为那个判断本会
让"只接受本机客户端"永远停留在声称。）

### 115.2 量到的一条：模式与执行方式**四对组合**，schema 一对都禁不掉，而这条规则当时写着两遍

`common.schema.json` 的 `securityMode` 有两个取值、`enforcement` 有两个取值，所以已发布的契约允许**四对**，
其中两对是**互相矛盾**的：

| 组合 | 是否自洽 |
|---|---|
| `policy_only` + `same_user_can_bypass` | ✅ 本 build 的诚实状态 |
| `protected_machine` + `acl_enforced` | ✅ 将来受保护 build 的状态 |
| `policy_only` + `acl_enforced` | ❌ 声称了一种没有任何实现的强制 |
| `protected_machine` + `same_user_can_bypass` | ❌ 声称了一种同用户进程可以绕过的"保护" |

schema **一对都禁不掉**：这两个字段是共享的 `$defs`，跨字段约束要在**每个**用到它们的 schema 里写 `if/then`，
而那会拒掉今天合法的文档——已发布的 schema 不能在 `schema_version: 1` 里加一条会拒绝的约束（AGENTS §7）。
所以自洽性只能住在代码里。而它当时住在**两处**，一字不差：

* `caps/doctor.py:861` — `"enforcement": "acl_enforced" if security_mode == "protected_machine" else "same_user_can_bypass",`
* `ext/envelope.py:49` — 同一个表达式。

本阶段的 pipe 会是**第三个**。处置：这条规则搬进一个新模块 `cli/app/airoot/posture.py`（`SECURITY_MODE`、
`enforcement_for()`、`posture()`），三处调用点全部改成引用它，并加一条守卫——**`acl_enforced` 这个字面量在
`cli/app/airoot` 下只允许出现在 `posture.py`**，所以第四份手抄本不可能悄悄出现。守卫还要求这条映射对
schema 的枚举**完备**（枚举多一个值而没有规则就红），并且 `enforcement_for` 对认不出的模式**抛错而不是给默认值**
——默认值等于替一个没人认识的模式宣布一种强制。

### 115.3 量到的一条：`broker-response` **没有结果通道**

`broker-response.schema.json` 是 `additionalProperties: false`，字段恰好十个：`schema_version`、`request_id`、
`status`（枚举 `ok|rejected|failed|recovery_required`）、`security_mode`、`enforcement`、`transaction_id`、
`state`、`reason_code`、`evidence`、`retryable`（只有最后一个是可选的，其余**必填**）。

**没有一个字段装得下一次操作的"结果"。** 后果要写清楚：这条线**不是查询通道**，它是**提交通道**——问一句
只读问题，能拿回来的只有"裁决 + 证据"（`status`/`reason_code`/`state` + `evidence`），拿不回一个 payload。
这不是缺陷，是 §4 的形状：那十七条步骤是一条提交流水线，字段是为"我提交的那件事怎么样了"准备的（所以有
`transaction_id`/`state`），而**读**属于 CLI 的只读投影面（`where`/`doctor`/`inventory`）。**能从这里读到的，
是别人对你这次提问的裁决，不是世界本身。** 把它读成查询接口，是这一层最近的误读。

### 115.4 量到的：这条线到底保护了什么

这一节全部是**实测**（一台机器、两条独立路径互相印证），不是推断。它是本阶段最有价值的部分，因为它把
"兼容模式"从一句声明变成一组读数。

| 问题 | 实测 | 后果 |
|---|---|---|
| 请求的 DACL 落下来了吗 | **是**：3 条 `ACCESS_ALLOWED`、无继承 ACE、无多余条目、掩码 `0x001F01FF` | 但有三件事必须记下来，见下 |
| 对端是谁 | 本机对端：`GetNamedPipeClientProcessId` **等于**真实连接进程的 pid（与子进程比对相等），请求里故意谎报的 pid 与它无关；`probe_process` 读到了那个真实 token | 进程号只对**本机**对端可信 |
| 远端（loopback-SMB）对端 | pid 返回 **65279**——不是连接进程、也不是本机任何进程（`OpenProcess` 报 87） | 那个数是**客户端填的**；撞上活着的本机 pid 就会把调用归错人 |
| connect 竞态 | 客户端先连上并保持 → `FALSE` + **535**，句柄仍可用 | **535 = 已经连上了，继续** |
| 同一个 API 的另一种 `FALSE` | 客户端连上后**又断开** → `FALSE` + **232 `ERROR_NO_DATA`**（两次） | 把任何 `FALSE` 当"已连接"的服务器会去服务一个**死掉的**对端 |
| 没有客户端时 | `ConnectNamedPipe` **永远阻塞**（服务端没有超时；探针自己挂在这里） | 没有可中断等待的服务端会**永远关不掉** |
| `PIPE_REJECT_REMOTE_CLIENTS` 可验证吗 | **可，两条路**：`GetNamedPipeInfo` 的 flags 位 `0x8` 精确跟随（`0x1` 对 `0x9`）；行为上 loopback-SMB **不带标志连得上、带上 `err=5`**，本机两种都成功 | "只接受本机客户端"是**读数**，不是声明 |
| 跨用户 | **测不了**（见下） | 兼容模式**不能**被读成跨用户行为的证据 |

**DACL 那三条要写下来**：(1) **读回的 SDDL 字符串不等于请求**——`GA` 被规范化成 `FA`，所以**用字符串比 SDDL
会永远误报"漂移"**，要比的是 ACE 集合（类型/标志/掩码/受托者）；(2) `GetNamedSecurityInfoW` 按**名字**
读（`\\.\pipe\<name>`）**失败 `rc=161 ERROR_BAD_PATHNAME`**，必须从**服务端自己的句柄**上读
（`GetSecurityInfo(handle, SE_KERNEL_OBJECT, ...)`）；(3) **不给 SDDL 不是"安全默认"**——DACL 来自创建 token
的默认 DACL，实测 **5 条**，里面有 `Everyone` 与 `ANONYMOUS LOGON` 的 `READ|EXECUTE`，也就是说 pipe 变成
"谁都能连上读"。所以 SDDL 必须显式给，**而"不给"这个选项要写清为什么不能用**。

**远端的进程号不可信，所以判定前必须先断言"本机"**：`GetNamedPipeClientComputerNameW` 对本机对端返回
`FALSE` 且 `err=229 (ERROR_PIPE_LOCAL)`，对远端返回真与一个非空名字。**远端的对端没有可信的进程号，因此没有
任何东西可以用来准入它**——拒绝它不是保守，而是没有可用的观测。

**客户端会看到什么**（这些决定了 `call_broker` 能说什么）：没有服务器 → `CreateFileW` `err=2`；**服务器中途
死掉也是 `err=2`**（两者不可区分，所以**不得**把它报成"broker 崩了"）；实例忙 → `231 ERROR_PIPE_BUSY`；
`WaitNamedPipeW` 超时 → **`121 ERROR_SEM_TIMEOUT`**（不是 1460）；对端在帧中间消失 → `ReadFile`
`err=109 ERROR_BROKEN_PIPE`。两条要命的：**字节模式下"短读"是成功**——服务器写了 10 字节中的 4 个，客户端
`ReadFile` 返回 `ok=True, bytes=4`，所以**截断的帧会以"成功"到达**，必须看字节数，绝不能把 `ok=True` 当
"一个完整帧"；**消息模式救不了你**——普通 `CreateFileW` 客户端在 `PIPE_TYPE_MESSAGE` 的 pipe 上仍按**字节**
模式读（只有显式 `SetNamedPipeHandleState(PIPE_READMODE_MESSAGE)` 之后短读才变成 `err=234
ERROR_MORE_DATA`）。这正是帧必须是**显式长度前缀**、不能依赖 pipe 消息边界的原因。另外 `WaitNamedPipeW` 的
`dwTimeout = 0` 意思是 `NMPWAIT_USE_DEFAULT_WAIT`，**不是**"不等"。

**名字从来没有独占性**：实测矩阵（两个独立进程）——holder=普通 / second=first → 后来者 `err=5`；
holder=first / second=**普通** → 后来者**创建成功**；first/first → `err=5`；普通/普通 → 两个都成功。所以
`FILE_FLAG_FIRST_PIPE_INSTANCE` 只让**你自己**的创建在名字已被占用时失败（**防蹲**，不是独占），后来者可以给
同一个名字挂上自己的实例；而名字在叶子与 `\pipe\` 两段都**大小写不敏感**（`\\.\PIPE\AIROOT-…` 能连上），
所以它也不能当身份令牌。顺带两条：`nMaxInstances` 是**上限**不是创建数（上限设 1 时第二次创建报 231，
`PIPE_UNLIMITED_INSTANCES` 读回 255）；overlapped 句柄上的 `ConnectNamedPipe` 返回 `FALSE` + `997
ERROR_IO_PENDING`，必须等事件再 `GetOverlappedResult`。

**跨用户为什么测不了**（写清它需要什么，而不是含糊过去）：需要**一个知道密码的第二个本地账户**，以及
`runas /user:` 或 `LogonUser`/`CreateProcessWithLogonW` 这样的载体；而且被测的 SDDL 按设计就允许管理员，所以
有意义的是"**非管理员**的第二个账户"。创建账户、设置密码、持久化 runas 凭据，正是 `AGENTS.md` §8 那句
"任何测试都不得污染开发机的 PATH、注册表、ACL 或真实 AIROOT root" 所指的那一类，而且它需要一个**不能凭空
发明**的秘密。**所以结论是：docs/broker §2 的 user compatibility mode 与跨用户行为无关，本轮没有也不能改变
这一点。**

**一句话的诚实位置**（与 docs/broker §2 完全一致）：**请求的 DACL 完整落下来了，OS（而不是请求）说出了
本机对端是谁，而这个名字永远不独占**——所以这是**同用户模拟**，不是信任边界。

### 115.5 守卫与验红

本阶段加的守卫分四组，每一组都量过"它能不能红"，因为**能验红**与**红得对**是两件事（§114.5 的教训）。

| 守卫 | 位置 | 它拒的是什么 | 验红 |
|---|---|---|---|
| 四个字面量只许出现在 `posture.py` | `test_l1_posture.py::test_only_posture_py_names_an_enforcement_value` | 第四份手抄本 | ✅ 往 `caps/doctor.py` 塞一行 `_INLINE_ENFORCEMENT = "acl_enforced"` → **1 红** |
| `enforcement_for` 的实参不许是字面量 | `test_l1_posture.py::test_no_module_passes_a_hard_coded_mode_to_enforcement_for` | 硬编码的模式（**它错，不是因为词写错，而是因为形状**） | ✅ 把 `enforcement_for(SECURITY_MODE)` 改回 `enforcement_for("policy_only")` → **1 红** |
| 提权服务端必须拒绝启动 | `test_l1_broker_pipe.py::test_an_elevated_server_refuses_to_start_and_says_why` | 把那条拒绝放宽成警告 | ✅ 见下（**这是本阶段唯一能在这台机器上跑的 live-pipe 断言**） |
| 打印集 ⟷ 语料双向相等 | `test_l0_consistency.py`（既有） | 核心自己写出的 `broker-response` 没有验收面 | ✅ 加 fixture 前它**就是红的**——它是本阶段第一个发现的缺口，不是事后补的 |

**第一组的范围是 §115 才从"一个词"扩到"四个词"的，而扩之前它是漏的。** 原来只扫 `acl_enforced`，而
`broker/pipe.py` 当时写的是 `enforcement_for('policy_only')`——一个**硬编码的模式**。把它改对之后，
`SECURITY_MODE` 的来源只剩 `posture.py` 一处，而 `SECURITY_MODE` 一旦换值、`enforcement_for(SECURITY_MODE)`
就会跟着换，两个字段**不可能**再各自说一套。这一条同时是第二组存在的理由：**只按词扫的守卫挡不住形状**
（把 `'policy_only'` 改成 `'protected_machine'` 一样能躲过按词扫描），所以补了一条按**形状**扫的。

**第三组要单独说，因为它在"测试都跳过"的机器上是唯一还在断言的东西。** 这台开发机的 shell 是**提权**的，
而 `serve_pipe` 按设计拒绝从提权 token 启动，于是本阶段 23 条 live-pipe 测试全部 `skip`。如果提权拒绝本身
也只由那些被跳过的测试覆盖，那么"放宽它"这件事在这台机器上**永远不会红**——那才是真正的坏形状：一个看起来
有覆盖、实际一次都没跑过的断言。所以这一条**不挂 `_requires_unelevated`**：提权时它断言拒绝（本机走这条），
非提权时它启动一个没人调用的服务器并断言它干净停下（`stop` 那条路，本机走不到）。**一条测试覆盖两个世界，
而这两个世界里它都是真的。**

**另一处验红顺带修掉了一个真缺陷**（不是守卫，是实现的判断）：`broker/pipe.py` 原本把"名字已被别人占住"
（`CreateNamedPipeW` 的 `err=5`）报成 `ACL_MISMATCH`。这个码讲的是**目录的 ACL 与基线不符**，而这里讲的是
**本进程没拿到这个名字**——两件事，只是共用退出码 5。**借一个码是因为退出码相同，正是码表开始失去意义的
方式**，所以改报 `PRIVILEGE_REQUIRED`，并在证据里写清"补救办法是一个别人不占的名字，不是提权"（提权拿不到
一个独占的名字）。改回旧写法 → `test_l1_reason_codes.py` **1 红**。副作用是好的：`ACL_MISMATCH` 恢复了它
在《这一版发不出来的码》里的位置——§113 的写一侧仍然**只是库**，没有调用者，所以这个码仍然没有写者。

### 115.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1198 → 1294**（**+96**）。逐文件实测：`test_l1_broker_transport.py` **29** · `test_l1_broker_pipe.py` **32** · `test_l1_posture.py` **9** · `test_l1_broker_policy.py` 里**本阶段新增的那 26 条**（该文件共收集 57 条，其余 31 条是 `63284b0` 的 §114 加的，所以它整体记在 §114.6 那一格）。**"从"取的是 §114.6 的"到"，不是 1124**：本阶段第一次写这张表时，把"HEAD 上收集到多少"当成了"本阶段从多少起算"——`git stash` 之后实测 HEAD 正好是 1198，与 §114.6 闭合；那个 1124 是把本阶段的 96 减过头算出来的。**这是本阶段第二处"看着像链断了、其实是加数错了"**（第一处是 §114.6 那一格），两者同因：链条守卫（§83）只在链**断**的地方红，它管不了某一段的加数本身 |
| 审计检查（`test_l0_consistency.py`） | **108 → 108**（本阶段没动这个模块；它上一次变是 §114 修掉推导器少数出来的 4 条） |
| golden 语料 | **41 → 42**（新增 `broker_response_pipe_refusal.json`，逐字节再生无漂移） |
| reason code | **97 → 97**（新增 `CALLER_NOT_AUTHORIZED` 是 §114 的事；本阶段只把一个用错的码改对） |
| 新增模块 | `cli/app/airoot/posture.py`、`cli/app/airoot/broker/transport.py`、`cli/app/airoot/broker/pipe.py` |
| 新增测试文件 | `cli/tests/test_l1_posture.py`、`cli/tests/test_l1_broker_transport.py`、`cli/tests/test_l1_broker_pipe.py` |
| 契约变更 | 无（`cli/schema/*.schema.json` 一个字节没动；本阶段加的是**使用者**，不是新边界） |
| 新增 ADR | **ADR-0043**（线路的五条决策：兼容模式只允许本机、模式与执行方式的自洽规则只能住在代码里、反驳必须到达调用方、名字永不独占、`probe_root` 只能回裁决） |

**fixture 的产法要写清楚，因为它是本阶段唯一一处"没有用最真的那条路"的地方。** 那份语料**不是**从一条活
pipe 里截下来的：`serve_pipe` 在提权 token 下拒绝启动，而 `test_golden_fixtures_reproduce_exactly` 要在
**每台机器**上重新生成语料——所以"只能由一条活 pipe 产出"的 fixture 在任何提权 shell 上都是不可复现的
（包括本机）。冻结的是**服务端的判定**：同一个 `ROUTING` 条目、同一个 `_refusal` 装配器（服务循环调的就是
它）、一个由 wire 层构造并被 `broker_request_commit_plan.json` 记下的请求。**哪些字节上线**是
`broker/transport.py` 的契约，由它自己的测试量；`probe_root` 不能当 fixture 的理由见 §112（它的答案是
机器观测），`CALLER_NOT_AUTHORIZED` 也不能（它的证据带观测到的 SID）。

**加这份 fixture 时，撞出一条既有断言的反向错误。** `test_golden.py` 原本要求两张映射的**值**（schema 名）
不相交。那条规则从来不是重点——两张表本来就允许展示同一个契约（一张来自核心路径、一张来自 harness 路径）
——而它会**主动拒绝**这里必须写下的东西：`broker-response` 现在是核心自己写的文档，它的 fixture 必须在
`SCHEMA_FOR_FIXTURE` 里，同时 harness 那四份仍在 `SCHEMA_FOR_HARNESS_FIXTURE` 里。改成按**fixture 名**
不相交：两张表不能对同一个名字声称不同的 schema，那才是真正的缺陷。

**`references/field-values.md` 的两行也改了，而改的过程暴露了那把尺子的极限。** 两个 posture 字段的
"写者"栏加上了 `posture.py`（词汇的唯一处所），于是那条按**字面量**判"有没有写者"的守卫**反了**：词汇模块
必须把**两个**成员都写出来（这样规则对枚举才是完备的），所以"这个词在文件里"不再等于"这个词被写出过"
——`protected_machine` 与 `acl_enforced` 会因为它俩正好是映射里没被选中的那一半而**看起来有写者**。处置：
这两个字段的**被发出的值**在一处数据里声明（`PAIR_COMPLEMENTS`），并且那个声明**不是凭信**——同一文件里
另有一条测试回到 `posture.py` 读它的 `SECURITY_MODE` 与映射，把声明与模块逐个对上。三种漂移都验过红：
把 `SECURITY_MODE` 翻成 `protected_machine`（**3 红**）、把映射的两个值对调（**3 红**）、把模式的键写错
（**6 红**）。**这条的教训与 §114.5 同族、方向相反**：那里的守卫在读自己写的用例（自指），这里的守卫在读
一个模块的**全部**词汇，而"文件里有这个词"对**两个值的字段**根本不构成证据。

### 115.7 如实记录的边界

1. **这条线不是信任边界，本阶段没有把它变成边界。** 同用户进程能做服务端能做的一切：pipe 的名字按设计
   就不独占（后来者可以给同一个名字挂自己的实例），DACL 也**故意**允许当前用户——那是这个模式的名字
   （same_user_can_bypass）的字面意思。DACL 买到的是**只接本机**（`PIPE_REJECT_REMOTE_CLIENTS`，可读回验证），
   不是"别的用户进不来"这个更强的说法（那条**这一轮没有量**，见下）。
2. **跨用户那一半没有测，而且这里记的是"为什么测不了"而不是"没测"。** 需要有密码的第二个本地账户；
   而创建账户、设密码、持久化 runas 凭据正是 `AGENTS.md` §8 禁止测试污染的那一类，且它要一个**不能凭空
   发明**的秘密。所以 **user compatibility mode 与跨用户行为无关**，本轮没有也不能改变这一点。
3. **提权拒绝的覆盖面是有条件的。** 本机 23 条 live-pipe 测试全部跳过，因为它们要的是一条能启动的服务。
   这不是"绿"，是 `skip`（`AGENTS.md` §8 要求测试不污染宿主机，而唯一能拆掉这个条件的东西是提权）。
   唯一在两个世界都运行的是 §115.5 第三组那条。
4. **没有任何动词走到这条线。** `serve_pipe` / `call_broker` 是库加一个测试路径上的服务端：CLI 里没有
   `broker` 动词、`agents/airoot.json` 里没有 lane、没有 schema 变化。**从 CLI 走不到这条 pipe**，它今天
   的消费者是测试。要接上它需要的东西不是代码，是策略来源（`allowed_sids` / `minimum_integrity` 从哪来）
   与一个**受保护的**服务端（提权 + 私钥）。
5. **`probe_root` 经这条线只回裁决与证据，不回值。** `broker-response` 没有结果字段（§115.3），所以调用方
   知道"根探针成功了、看了什么"，不知道探到的数。这不是缺陷，是那张 schema 的形状；要读世界请走 CLI 的
   只读投影面。
6. **`probe_root` 与 `CALLER_NOT_AUTHORIZED` 都不能进语料**（前者是机器观测、后者带 SID），所以这份
   fixture 冻结的**只有**"被拒绝的提交"这一条路。它不是"这条线的验收面全在这里"的意思。
7. **`nMaxInstances`、overlapped 句柄、`WaitNamedPipeW` 的 `dwTimeout=0` 语义**等读数记在 §115.4 与模块
   文档里，属于"将来写 Rust 版时不要再摸一遍"的知识，而不是本阶段的实现。

## 116. 本机签发：让真机上的 `install` 能跑完（ADR-0046）

§115 把线接上了，但**没有东西能签发批准**，所以 `install` 在真机上停在 `PROVENANCE_FAILED`——也就意味着
**Rust 工具链永远装不进来**（ADR-0001 的入口）。本阶段补上那一步。裁决是 **ADR-0046**，它是本阶段唯一的
权威；本节记录它怎么落地的、验了什么、以及边界在哪。

### 116.1 先记一条走不通的路（它一度是推荐解）

"上游（DSH 那类 harness）持私钥签发、公钥作为信任锚随 AIROOT 分发"曾是首选：不改 schema，且在新威胁模型
下那把私钥不需要强保护。**实测否掉**——keyring 在 root 里（`state/keyring.json`），而三大核心契约第 22 行
自己写着 user compatibility mode **不能声称能阻止同用户 Agent 直接修改用户目录**。于是本机进程可以把锚换成
**自己的**公钥、用自己的私钥签一份合法 plan，而 AIROOT 验签**通过**。

**"密钥不需要强保护"对密钥成立，对信任锚不成立**：锚必须比它约束的东西更强，而那需要提权或 ACL——正是
ADR-0045 决定不做的。所以这条路不是"代价高"，是**它买不到它声称的那件事**。

### 116.2 落地的形状

| 件 | 位置 | 它是什么 |
|---|---|---|
| **签发者** | `cli/app/airoot/tx/issuer.py`（新） | `provision` 生成密钥对并登记公钥；`load_private_key` 读私钥；`issue` 用 `ed25519` 签出一份 schema-valid 的 `approval-token`；`write_token` 落盘供 `--token-file` 消费 |
| **私钥** | `state/issuer-key.json` | root 里，**文件自己写着**"同用户进程可读，这是已知且接受的后果"——不让一个看起来像密钥库的文件承诺它不提供的保证 |
| **keyring** | `state/keyring.json`（改名，见下） | 只放**公钥**；ADR-0039 决策四说改名属于"给它一个生产写者"的那一阶段，现在那个写者存在了 |
| **语义** | `tx/approval.py` 的 `ISSUER_PENDING` | 这句拒绝语**被重写**了：旧的是"this build 里没有生产签发方"，而它**自本阶段起是假的**——build 里有签发者，缺的是**这个 root 里没有密钥**。指向也从 ADR-0025 改成 ADR-0046 |

**签发者不进核心，也不做成 CLI 动词**：恒定式是"核心永远没有签名侧"，而一个动词会让签名看起来像 CLI
自己就能做的事。它是**一次显式的人工步骤**，与核心分开。

### 116.3 守卫与验红

| 守卫 | 它在拒什么 | 验红 |
|---|---|---|
| `test_a_real_install_completes_with_a_locally_signed_approval` | 把"能跑完"从声称变成断言 | ✅ 本阶段前**必红**：没有 keyring 就停在 `PROVENANCE_FAILED` |
| `test_every_fact_comes_from_the_plan_so_a_mutation_breaks_verification`（4 个参数） | 签发者自己供给 plan 事实（那它就能授权一份没人看过的 plan） | ✅ 逐字段改后验签失败 |
| `test_provisioning_refuses_to_overwrite_an_existing_key` | "密钥换了而没人发现" | ✅ 第二次 provision 报 `INVALID_INPUT` |
| `test_human_mode_refuses_rather_than_inventing_a_sid` | 凭空造一个 SID 当作人的批准 | ✅ `mode=human` 无 SID 报 `INVALID_APPROVAL` |
| `test_the_approval_is_consistency_not_permission_so_a_foreign_key_still_verifies` | **方向反的那条谎**：把批准读成权限证明 | ✅ 未登记的钥匙被拒（一致性），而**用户可写 keyring** 这件事被断言出来（权限不在这一层） |
| 打印集 ⟷ 语料双向相等（既有） | `issue` 自校验 `approval-token`，所以这个 schema 成了"核心打印的文档" | ✅ 加 fixture 前它**就是红的**——又是它先发现的 |

**第五条是本阶段最该看的一条**：它的名字里就写着这个项目的诚实口径——**批准是账本，不是授权证明**。
它同时钉住两个方向：未登记的钥匙被拒（说明验签**真的在验**），以及密钥与 keyring 都可被用户改写
（说明它**不是**权限边界）。将来若有人"顺手"加一个权限检查让第一条红，那不是回归，是另一个设计。

### 116.4 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1294 → 1307**（+13，全部在 `cli/tests/test_l2_issuer.py`；含 4 个参数化用例） |
| 审计检查（`test_l0_consistency.py`） | **108 → 108**（本阶段只把 repo map 的 ADR 范围与 fixture 计数改对） |
| golden 语料 | **42 → 43**（新增 `approval_token.json`：固定 seed + 固定时钟 + 固定 nonce） |
| 新增模块 | `cli/app/airoot/tx/issuer.py`（签发者）；`state/issuer-key.json` 与 `state/keyring.json` 是它写出的运行时状态 |
| 契约变更 | **无 schema 变更**：`approval-token` 的必填项与 enum 一个字节没动；改的是**拒绝语**、**keyring 路径名**与 **Skill/reference 里七处措辞** |
| 新增 ADR | **ADR-0046**（本机签发——批准是账本，不是授权证明；推翻 §113.6 第 6 条） |

### 116.5 如实记录的边界

1. **批准不是授权**。私钥在 root 里、同用户进程读得到也能自己签，所以验签通过只证明**一致性**（这份 plan
   由该 root 信任的钥匙签过、此后没被改动或重放）。**挡同用户进程是使用方（上游 harness）的职责**——
   ADR-0045 把这条归属移出去了，本阶段只是接受它的后果。
2. **ADR-0044 的实测读数全部仍然成立**（CNG 不支持 Ed25519、软件 KSP 容器对拥有者开放、同用户进程读得到）。
   **变的不是读数，是"这算不算缺陷"**——旧模型下它是阻塞，新模型下它是被接受的后果。这一点写进了 ADR-0046。
3. **`state/keyring.json` 的旧名仍可读**（新名优先）：既有 root 不会因为改名而失效。**迁移 = 旧路径仍然可读**，
   不是把旧文件搬走或删掉。
4. **本阶段没有跑真实 Rust 安装**。`install` 的**机制**现在能跑完（116.3 第一条用真实文件证明了），但
   "把 `rustup-init.exe` 装进 store"是**下一次**的事——它要真机验收脚本改口径，而那是另一个决定。
5. **`test_hmac_sha256` 仍然只许出现在测试路径**：本机签发用 `ed25519`，不是把测试算法搬进生产。

## 117. 第一次真实安装：rustup-init.exe 进 store（ADR-0001 的入口打通）

§116 让 `install` 的机制能跑完。本节是**真机上第一次真的装一个东西**——而且踩到两个只有真跑才会暴露的问题，
两个都不是"操作失误"，是**账与账之间的不一致**。

### 117.1 建的 root 与"装到哪里"

| 项 | 值 |
|---|---|
| root | `D:\env\.airoot`（**新建**；建之前本机没有任何 root——`D:\airoot` 查过了，那是本仓库的另一份克隆） |
| 卷序列 | 真值已抹掉（属本机指纹，AGENTS.md §9 禁止提交）；结论是它与 `D:\env` **同一个卷**，所以 store 与数据根同卷 |
| `root_instance_id` | `root-home-d-env` |
| `machine_id` | `machine-home-single-user`（**显式人命名的，不是硬件指纹**——P1 禁止从硬件推导） |
| payload 落点 | `D:\env\.airoot\store\rust-toolchain\rustup-init\1.83.0\win-x64\rustup-init.exe` |

**谁建的 root**：`init_root` 自己的文档写着 "test/bootstrap helper only"，而 `bootstrap` 动词仍是
`needs-admin`（延后）。所以这个 root 是**一次性脚本**建的（`%TEMP%\airoot_bootstrap_root.py`），
不是发明一个动词——**在真机上做的事，记在真机脚本里**。

### 117.2 坑一：`machine_id` 不允许斜杠（**我自己造的**）

第一次 bootstrap 写了 `machine/home-single-user`，`plan` 立刻拒绝：

```text
machine_id: 'machine/home-single-user' does not match '^[A-Za-z0-9._:-]{8,128}$'
reason_code: SELF_VALIDATION_FAILED  →  "plan rejected a document produced by this build"
```

**自校验正确地抓住了它**，而且抓的是"这个 build 自己产出的文档不满足已发布契约"——正是它该管的事。
`machine_id` 写在 registry 的 `meta` 表里，而 `meta` **按设计没有更新路径**（它是不变的）。当时那个 root
里没有任何真实事务，所以处置是**重建那棵树**，而不是手工改权威：

```text
删除 D:\env\.airoot（只有 bootstrap 刚造的空壳）
  → 用 machine-home-single-user 重建 root 与 registry
  → 重新 provision 密钥（新 root 需要新的信任锚）
```

**为什么不直接 `UPDATE meta`**：那是绕过权威去做一件"删掉重来更便宜"的事。**删空壳比改权威便宜，而且不留歧义。**

### 117.3 坑二：来源清单声明了一个**从未被冻结**的能力（**合同里的真缺口**）

冻结清单 `cap-2` 有 7 项能力，**没有 `rust-toolchain`**；而可信来源清单 `src-1` **为它声明了来源**，
备注里还记着 §59 对真实上游的验证。于是 `plan rust-toolchain` 报 `CAPABILITY_NOT_DECLARED`。

**这是两份账在互相矛盾**：来源清单在为一个不存在的能力声明来源。契约 §15.4 的成长路径是
"提议 → 冻结 → 白名单"，并明说**不得为了让某个对象能被管而临时放宽**。所以处置是**冻结它**：

| | |
|---|---|
| revision | `cap-2` → **`cap-3`** |
| 新条目 | `rust-toolchain` / `kind=tool` / `entry=rustup-init.exe` / `scope=[machine, session]` |
| `side_effects` | `["writes_store", "writes_registry"]`——**不写 `[none]`**：安装会往 store 写 payload 并登记实例，而 ceiling 记的是**最坏情况**，不是客气的那一种 |
| 为什么是 `tool` 而不是 `runtime` | 入口是一个自足的安装器，不是一个解释器目录 |

代价照实说：这动了**冻结契约**，所以要重生 golden 语料（`frozen_capabilities.json`、`execution_bounds.json`）、
改 `test_l1_boundary.py` 里钉住 revision 的那条字面量、并把新名字加到 `AGENTS.md` 的仓库地图。

### 117.4 四步与它们的读数

| 步 | 命令 | 结果 |
|---|---|---|
| 解析来源 | `resolve_source(capability_id="rust-toolchain", version="1.83.0")` | 摘要 `sha256:6f4bef6626…` **来自上游发布的 `.sha256` 文件**，不是自己算的；`offline=false` |
| 计划 | `airoot --root D:\env\.airoot plan rust-toolchain --version 1.83.0 --source-json …` | `state\plans\plan_rust-toolchain_1.83.0_5e63c3f68c3a.json` |
| 签发 | `issuer.issue`（独立工具，**不是 CLI 动词**） | `approval/rust-toolchain-1.83.0`，`ed25519`，`key_id=airoot-local-issuer-1` |
| 安装 | `airoot --root D:\env\.airoot install <plan> --token-file <token>` | **`FINALIZED`**，`generation 0 → 1`，2.9 秒 |

**独立核对（不看记录，自己算）**：

```text
store 文件的 SHA256   : 6f4bef66261261fcb43131be8720bab817d403a09edec7455c371974b90bdb7e
上游发布的期望值      : 6f4bef66261261fcb43131be8720bab817d403a09edec7455c371974b90bdb7e  ← 逐字节相同
文件头                : MZ（真 PE 可执行文件，不是错误页）
字节数                : 12 721 664（与 §59 记的读取一致）
`tool verify`         : verified=true, problems=[]
registry              : capability=rust-toolchain, health=healthy, lifecycle_status=active,
                        install_backend_id=https_artifact, source=The Rust Project
```

### 117.5 如实记录的边界

1. **装的是 `rustup-init.exe` 本体，不是 Rust 工具链**。真正要让 Rust 可用，还要**显式运行它**
   （`rustup-init.exe --no-modify-path`），而那一步**不在本阶段**：它执行一个 payload，属于 P5 Runtime。
   所以现在说"本机有 Rust 了"是**假的**；准确说法是"rustup 安装器已由 AIROOT 受管、校验、登记、绑定"。
2. **`tx\<id>\fetch\rustup-init.exe` 会**留下**另一份 12.7 MB**。这是**故意**的，不是泄漏：`drive()` 在
   resume 时**复用**已下载的文件（崩溃后不重下），而 `stage` 是**复制**进 store 而不是移动。代价是每个
   成功事务多留一份 artifact。**要不要在 FINALIZED 之后回收 fetch，是一个未裁决的问题**——它牵动
   "从不删除"与"恢复优先"两条原则，不能在装完东西的顺手改掉。
3. **`capability list` 现在是 8 项**，而 `rust-toolchain` 在**数据根里没有白名单谓词**——那是刻意的：
   白名单管的是"在数据根里**认出**这种东西"，而 `rust-toolchain` 是**从可信来源装进来的**，不需要在
   用户目录里被认出来。§15.4 的两半是"冻结"与"识别"，这里只需要前半。
4. **这次安装没有动 PATH、没有执行 payload、没有提权**。`--no-modify-path` 是 ADR-0001 给 rustup 定的
   规矩，而"给 PATH 加东西"属于 P9（`exposure\bin` launcher，见可选加固）。

## 118. 执行那个 payload：Rust 工具链真的装上了

§117 把 `rustup-init.exe` 装进 store 并登记，但**它只是个安装器**。本节是真的**运行**它——也就是
ADR-0001 语言切换的前置条件，从"安装器在手"变成"rustc 能编译"。

### 118.1 跑之前先量：本机是干净的

| 检查 | 读数 |
|---|---|
| `rustup` / `rustc` / `cargo` | **都不存在**（`Get-Command` 全空） |
| `%USERPROFILE%\.cargo` / `.rustup` | **都不存在** |
| `rustup-init -V` | `rustup-init 1.29.1 (d95a37b6a 2026-08-13)` |

**所以是干净安装，不会覆盖任何已有环境。** 这一条是运行前必须量的：`rustup-init` 会改用户级的
`~\.cargo`/`~\.rustup`，在已有 Rust 的机器上那是一次**迁移**而不是安装。

### 118.2 装到哪：**rustup 自己的默认位置**（记录那个选择）

装之前有两个可选位置，选了 A：

| | 位置 | 为什么 |
|---|---|---|
| **A（选中）** | `%USERPROFILE%\.cargo` + `%USERPROFILE%\.rustup` | rustup 在 Windows 上的常规位置，与任何 Rust 文档、IDE、工具链行为一致 |
| B | 用 `CARGO_HOME`/`RUSTUP_HOME` 定向进 `store` | "删掉 root 就全没了"表面上成立，但偏离标准布局，而且 store 要的是**不可变 payload**语义，工具链不是 |

**无论选哪个都不写 PATH**——`--no-modify-path` 是 ADR-0001 定的规矩，而写 PATH 属于 P9。

### 118.3 实测

| 项 | 读数 |
|---|---|
| 命令 | `rustup-init.exe -y --no-modify-path --profile minimal --default-toolchain stable` |
| 结果 | `stable-x86_64-pc-windows-msvc installed - rustc 1.98.1 (48a229cea 2026-09-01)` |
| 耗时 | **19.1 秒** |
| `rustc --version` | `rustc 1.98.1` |
| `cargo --version` | `cargo 1.98.1 (797e8a9bc 2026-08-05)` |
| `rustup --version` | `rustup 1.29.1` |
| 体积 | `.cargo` **12.1 MB**，`.rustup` **576.0 MB** |
| PATH | **没动**：当前进程 PATH 不含 `.cargo\bin`，**用户级 PATH 注册表长度 1684 未变** |

**`--version` 只证明二进制存在，所以另外真编译了两个程序**（`%TEMP%` 下的临时目录，跑完删掉）：

```text
rustc 直编 →  airoot-rust-smoke: sum=15      （1 秒）
cargo new + cargo build →  Hello, world!     （cargo build: OK）
```

**这才是"能用"的证据**：一个只会打印版本号的 rustc 是坏的工具链。

### 118.4 AIROOT 的账本还诚不诚实（跑完 payload 之后必须复查）

**必须查，因为"AIROOT 装的东西"后来把 576 MB 写到了别处**。复查结果：

| 检查 | 读数 | 读法 |
|---|---|---|
| `tool verify rust-toolchain/rustup-init/1.83.0/win-x64` | `verified=true`, `problems=[]` | store 里那份**没被动过**——rustup-init 写的是 `~\.rustup`，不是自己的目录 |
| `where rust-toolchain` | `found=true`、`usable=true`、`health=healthy`、`executable=…\store\…\rustup-init.exe` | 它指向**自己拥有的**那份（store 里的安装器），**不是** `~\.cargo\bin\rustc.exe` |

**所以没有任何漂移**，而这不是侥幸，是**账的边界清楚**：AIROOT 登记的是"我装了 `rustup-init.exe`"，
而 rustup 之后把工具链写到它自己的常规位置——那是**另一个所有权域**，不是"我的 payload 变了"。

**这一条要写下来的原因**：它是"AIROOT 不拥有它没装的东西"这条不变量的第一次真实检验。`where` **没有**
假装知道 `rustc` 在哪，也没有因为 `~\.rustup` 出现了就报漂移。将来若要让 AIROOT **管**这个工具链，
正确的做法是走 §15.4 的路径（提议 → 冻结 → 白名单），把 `~\.rustup` **认出来**并 `adopt`，而不是让
`where` 去猜。

### 118.5 如实记录的边界

1. **这个工具链不在 AIROOT 的账上**。`.rustup` 576 MB 与 `.cargo` 12.1 MB 是**不受管的**：
   AIROOT 不知道它们存在，`doctor` 不会报它们，`uninstall` 不会删它们。**这是诚实的**——
   它们不是 AIROOT 装的（AIROOT 装的是那个安装器）。若要让它们入账，走 `discover`/`adopt`。
2. **`rustc` 不在 PATH 上，所以普通 shell 里 `cargo build` 会失败**。这是 `--no-modify-path` 的**直接后果**，
   不是遗漏。现在要用 Rust，两条路：显式用 `%USERPROFILE%\.cargo\bin\` 下的绝对路径，或者由**人在
   AIROOT 之外**自己加 PATH。**AIROOT 不替人做这个决定**，而"给 PATH 加条目"属于 P9。
3. **`--profile minimal` 是刻意的**：只要 `rustc`/`rustup`/`cargo`，不下 docs/clippy/rustfmt——
   本阶段要的是**能编译**，多下几百 MB 没有对应的验收价值。要补，`rustup component add` 是标准的下一步。
4. **运行 payload 这件事本身没有经过 AIROOT 的事务**。§117 的 plan/approve/install 管的是
   "把 artifact 放进 store 并登记"，而**执行**它不在那条链上——这一节是用绝对路径直接跑的。
   **AIROOT 今天没有"运行一个受管 payload"的动词**，而那正是 P5 Runtime 的题；记在这里，
   免得读者以为 `install` 会执行它装的东西（它不会，而且不该）。

## 119. 写者出现了，说"没有人写它"的那几格没动

### 119.1 起因：同一个拒绝语自己讲两个故事

§117 与 §118 让"真机签发 → 真机安装 → 真机执行"这条路第一次走完，于是在 §118 收尾时回头读
`tx/approval.py` 的拒绝语，看到的是一段**自相矛盾**的文字：

```text
no approval keyring is installed in this root; provision a signing key first
  (decided: ADR-0046 — approval is an audit record, so the signer is a local, explicit step)
evidence:
  the only issuer is the test one (cli/tests/fake_issuer.py); the core verifies but never mints
  approve/install/env persist/tool gc --apply/uninstall cannot complete on a real machine until
    a protected issuer exists
```

**摘要那句话是 ADR-0046 改过的，它下面两行 `evidence` 是改之前的样子**：同一个拒绝既说"本机可以签，
先 provision"，又说"核心永远不会签发、真机上走不完"。读者只会看到后者，因为证据看起来更具体。

### 119.2 顺着这一处扫出来的**三个同类**，以及为什么守卫没红

**同类 1：`references/field-values.md` 的 `approval-token` 两行仍写"（没有写者）"**，正文还写着
"这一版没有生产签发方""这一版没有任何代码构造出一份 token"。而 ADR-0046 的 `tx/issuer.py` 的 `issue`
**就是**核心里的写者（返回前过 `validate_self`，§116 的 golden fixture 就是它造的）。

**同类 2：`broker-response` 的豁免类别仍是 `unbuilt`**，理由那一格写着"**核心**没有构造者"。§115 的
`broker/pipe.py` 的 `_answer` 就是核心构造者（造完过 `validate_self`，`broker_response_pipe_refusal.json`
就是它写出来的），所以这句话从 §115 起就是假的。

**同类 3：五个面还在说"这一版没有生产签发方"**——`agents/airoot.json` 的 `approve`/`install` lane 理由、
`docs/AIROOT-总体方案规划-v0.3.md` §23 第 4 项、`references/confirmation.md` 的小节标题（标题说"没有可用
实现"，正文下一段说"自 ADR-0046 起这条路是通的"）、`cli/app/airoot/policy/sources.json` 的 note
（"stage/commit were NOT exercised"）、`cli/tests/real_machine_acceptance.py` 的边界行、`scenario_ledger.py`
的 P-012/P-017、`docs/AIROOT-v0.3-规范审查报告.md` 的**当前状态节**（它自称描述当前状态，所以它不能说
"stage/commit 没跑过"）。

**为什么守卫一直绿，是这一节最该记的事**：
`test_l1_field_values.py` 的写入者检查（§92/§93）**只跑一个方向**——"点了写者、却没有构造函数"报错。
ADR-0046 是**造出**写者的那一次，缺陷落在另一半："表格说没有写者、而写者存在"。**一个单向检查在它检查的
那个方向上永远正确，而它不检查的方向没有任何人看**。同类 2 还多一层：就算方向补齐了，**度量本身看不见
它**——`_produced_schemas` 的语法走法只数函数自己的字典字面量与下标赋值，而 `broker-response` 有两个
必填键是 `document.update(posture())` 合进来的，所以走法把这份文档报成"没人造"，**与文档一致地错着**。

### 119.3 加了哪两条守卫，以及怎么验红

**守卫一（同类 1，新）**：`test_l0_consistency.py` 的
`test_a_schema_the_value_table_calls_writerless_is_declared_unwritten`。它把**两张表的声明**对起来：
"`field-values.md` 里没有任何一行点写者的 schema"必须**恰好等于** "`docs/schema/README.md` 里那份由
**校验调用点 + `$ref` 图**推导出来的没有写者的清单"。§92 的既有守卫是同一对表的另一个方向（"声明没有写者
的，表格不得点写者"），两条合起来才是一个**等式**。

**验红（实测，不是推演）**：把 `approval-token` 两行的写者列还原成"（没有写者）"，守卫报出
`field-values.md gives approval-token no writer, but the catalog does not declare it unwritten`；
改回正确值后无话可说。**两边的数都是推导的**，所以它不会因为谁改了措辞而失准。

**守卫二（同类 2，改度量 + 已有的类别判据）**：`_produced_schemas` 补上一条合并形状
（`document.update(f())`，且 `f` 必须是**唯一**的、返回值全是字典字面量的应用内函数），`unbuilt` 这个
类别随之由已存在的 `test_each_exempt_schema_declares_the_reason_this_build_measures` 判死：
`broker-response: the table says 'unbuilt', the build measures None`。补完之后实测**只有这一份文档
位移**（15 → 16），没有第二处被"顺手"算进来。

### 119.4 结果：`unbuilt` 这一类被删掉，而"谁写出"没有失去守卫

`broker-response` 拿到它该有的那一节（`status` 一行，写者是 `broker/pipe.py`），于是豁免表只剩
`root-marker`，`unbuilt` **失去了最后一个成员**。**没有成员的类别就是一个读者会遇到却查不到用处的词**，
所以它整个删掉——与 §82 删 `needs-decision` 同一个理由。同时清空的还有
`UNDOCUMENTED_BY_DESIGN`：最后一条 `broker-response.status` 跟着那一节一起被记录了。

**这两处清空都不是"判据变松了"，而是它一直在说的那件事终于成立**：
"这份文档有没有写者"由 `docs/schema/README.md` 的推导清单接着答（守卫第三十四组，依据是校验调用点与
`$ref` 图），那份清单比豁免表更接近问题本身。

### 119.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1307 → 1308**（+1：新增的双向守卫；`test_l2_approval_ed25519.py` 的那条断言改成三句，是同一个用例） |
| 审计检查（`test_l0_consistency.py`） | **108 → 109** |
| golden 语料 | 43 → 43（**数量不变，两份内容变了**：`broker_response_pipe_refusal.json` 的拒绝语证据、`scenario_ledger.json` 的 P-012/P-017 两段） |
| 新增模块 | 无（改的是断言、措辞，与一处语法走法） |
| 契约变更 | **无 schema 变更**；`EXEMPT_CLASSES` 少一个词、`UNDOCUMENTED_BY_DESIGN` 清空、`field-values.md` 的 `approval_token` 两行与 `broker-response` 一节是文档改动 |
| 新增 ADR | **无**——这是 ADR-0046 的收尾，不是新裁决；裁决本身没有一处被改 |
| 验红过的守卫 | 两条（上节各一处，都是"把缺陷放回去看它红不红"） |

### 119.6 如实记录的边界

1. **措辞扫不干净，而且这件事是可证的。** 能证明的只有**推导出来的那两处声明**（表格的写者列、
   豁免表的类别）。**散文没有人守**：这一轮把所有找到的地方都改了——运行时拒绝语（`tx/approval.py` 的
   `evidence`、`broker/protocol.py` 与 `broker/pipe.py` 的拒绝语与模块 docstring、`crypto/__init__.py`
   的 docstring、`policy/sources.json` 的 note）、agent 面（`agents/airoot.json` 的三格、
   `references/field-values.md`、`references/confirmation.md`）、层 3 文档（规划 §23 第 4 项）、审查
   报告的当前状态节、验收脚本，以及四处测试注释与场景台账的 P-012/P-017；**但下一处同类仍会靠人查**。
   所以这一节同时是一份清单——写清"哪几类声明有守卫、哪些只是这次读到了"。
2. **走法仍然看不见 `{**f()}` 展开**。今天应用里唯一真正用到合并形状的地方就是 `update(posture())`
   （实测：`grep` 全树只此一处），所以那条规则够用；但**下一个"把文档补齐一半再合并"的写法会让同一类
   缺陷再藏一次**，位置就在 `_produced_schemas` 的走法里。
3. **`human` 那一格的不再打 † 是实测过的**，不是从字面推的：`test_l2_issuer.py` 的
   `test_human_mode_refuses_rather_than_inventing_a_sid` 既断言缺 SID 被拒，也断言给了
   `approved_by_sid` 之后 `approval_mode == "human"`。† 的含义是"**没有任何代码会写出它**"，而这一格
   现在有代码写出它——**缺的仍然是那条**人类**通道**（谁按下确认、SID 从哪来），那是 D1 的事，与
   "字段写不写得出来"是两件事。
4. **没有动那些属于历史的文字**：ADR 日志里 ADR-0024/0025/0046 的记录、本草案 §59/§65/§67/§93/§116
   各节、以及审查报告**前文**的快照（它写着"审查时的数字"）——ADR-0028 的规则是计数守卫不得要求重写
   历史，同一理由适用于这些句子：它们记录当时为真的事实，改了就成了伪造。

## 120. 真的跑一次受管 payload：`airoot run`（ADR-0047）

最小版本的定义（`docs/AIROOT-最小版本-v1.md` 判据 #9）要的是"**有一个动词**真的把这份受管 payload 跑
起来并如实报告它的退出码与输出"。§118 做到了那件事，但方式是**手拼绝对路径**：不是动词、不是验收面、
账本上也看不出来。本节把那次手跑变成一个动词，并且**真的在真机上跑了一次**——因为 §118 的教训就是
"不真的跑一遍，就不知道缺什么"。

### 120.1 交付

| 件 | 内容 |
|---|---|
| 裁决 | **ADR-0047**（`run` 是"执行一次"，不是"持久化暴露"：六条决策 + 三条被否决的路各自附代价） |
| 新模块 | `cli/app/airoot/caps/runtime.py`：`resolve_run_target`（把实例解析成 store 里的主入口点，所有权复用 `caps/lifecycle.find_target`，不新建第二个"AIROOT 拥有"的定义）与 `run_once`（起子进程并如实带回退出码与输出） |
| CLI | `airoot run <instance-id> [-- args...]`（`cli.cmd_run`） |
| 测试 | `cli/tests/test_l1_runtime.py`（**21 条**）：解析与四种拒绝、真子进程的退出码与输出、`--json` 的归属、以及一条**推导出来的**动词守卫 |

**动词的形状**：`run <id> [-- args...]`；`--` 之后原样交给 payload。**只按事实拒绝**（`NOT_FOUND`(1) /
`OWNERSHIP_REQUIRED`(7) / `PAYLOAD_MISSING`(3)——已回收的实例落在最后一个里，证据带 `collected_at`）。
`retired`/`broken` **不拒绝**，它们是**报告**的事实。不注入环境或 PATH、不复核摘要（文档里
`payload_digest_source: "registry"` 明说那个摘要是**登记值**，不是刚测的）、不动 registry。

### 120.2 真机上的那次运行（本节的证据）

在 §117 装进 store 的那份真实 payload 上跑：

```text
airoot --root D:\env\.airoot run rust-toolchain --json -- --version
exit=0   exit_status=0   reason_code=SUCCESS   persisted=false
command:  D:\env\.airoot\store\rust-toolchain\rustup-init\1.83.0\win-x64\rustup-init.exe --version
stdout:   rustup-init 1.29.1 (d95a37b6a 2026-08-13)
payload_digest: sha256:bbdc9e16b2faf619c4e5824717961a2024434317e8ec0ed185a0de781dad796e（**登记值**）
lifecycle_status=active  health=healthy
```

非零子进程（同一个 payload，喂一个它不认识的参数）：`child exit_status=1`、AIROOT 退出码 **2**、
`reason_code=CHILD_PROCESS_FAILED`，`stderr` 里是 rustup 自己的原话
（`unexpected argument '--no-such-flag' found`）——**子进程的失败被如实带回，没有被折成 AIROOT 的错**。
未登记的 id：`NOT_FOUND`(1)，证据列出**存在**的那些实例 id。

### 120.3 真跑与"加第二个动词"各暴露了一个缺陷（两个都已修、都加了守卫）

**缺陷一：共享的 pre-parse 重写把动词硬编码成 `"exec"`。** 第一次真跑就撞上：

```text
airoot run rust-toolchain --json -- --version
→ {"reason_code": "NOT_FOUND", "message": "unknown reference: rust-toolchain"}
```

`_normalize_child_argv`（当时叫 `_normalize_exec_argv`）最后一行写的是
`return [*arguments[:index], *hoisted, "exec", *owned, *child]`——**一个只服务一个动词时完全正确的字面量**。
第二个动词一进来，`run` 被**静默改写成 `exec`**，于是受管实例去查引用表。修法是把动词本身传出去；
守卫是**按 `CHILD_VERBS` 推导**的（每个成员都必须"以自己"穿过这次重写），所以第三个动词不可能再带同一个洞。

**缺陷二：没有 `--` 分隔符时，`--json` 会被交给 payload，而 AIROOT 静默留在 human 模式。**
`argparse.REMAINDER` 拥有标识符之后的一切，所以 `run <id> --json` 既把 `--json` 喂给子进程、又让
调用方拿不到文档。修法不是"再列一份自己的选项表"（那正是 §115 删掉的那种手抄副本），而是**从 parser
推导**（`_airoot_option_arity()` 走遍主 parser 与所有子 parser），于是"标识符之后、`--` 之前、且
**parser 认识**的选项"属于 AIROOT，其余属于 payload；`run <id> -version` 仍然原样交给 payload。
**逃生口是分隔符**：`run <id> -- --json` 里的那个 `--json` 是 payload 的（测试两个方向都断言）。

### 120.4 守卫与验红

| 变异 | 预期 | 结果 |
|---|---|---|
| 把动词字面量放回去（`"exec"`） | 红 | ✅ 1 条（`test_every_child_verb_survives_the_argv_rewrite_as_itself`） |
| 把无分隔符的提前返回放回去 | 红 | ✅ 1 条（`test_a_leading_airoot_option_is_not_handed_to_the_payload`） |
| 载荷缺失 / 已回收 / 未登记 / 是引用 / 无入口点声明 / 入口点被删 | 各自指名拒绝 | ✅ 六种都有测试，理由码与退出码逐个断言 |
| 子进程非零 | 报告而非抛 | ✅ `exit_status` + `CHILD_PROCESS_FAILED`(2)，输出原样带回 |
| 载荷存在但不能启动（非可执行文件） | 带 OS 原话拒绝 | ✅ 证据含 `WinError` 与"这台机器拒绝启动它"（§62 的教训：不让裸 `OSError` 逃成 traceback） |

**两次变异都是把缺陷放回去实测的**：`1 failed` / `1 failed`，改回后恢复全绿（脚本按字节还原并核对）。

### 120.5 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1308 → 1329**（+21，全部在 `cli/tests/test_l1_runtime.py`） |
| 审计检查（`test_l0_consistency.py`） | **109 → 109**（本阶段没动那个模块） |
| golden 语料 | **43 → 43**（`run` 的文档是**报告面**，没有已发布 schema，所以不进语料；ADR-0047 决策六） |
| 新增模块 | `cli/app/airoot/caps/runtime.py`（caps 模块 25 → **26**，repo map 同步） |
| 新增测试文件 | `cli/tests/test_l1_runtime.py` |
| 契约变更 | **无 schema 变更**；新增一个 CLI 动词与它打印的报告面 |
| 新增 ADR | **ADR-0047** |
| 新增最小版本文档 | `docs/AIROOT-最小版本-v1.md`（定义 + 16 条判据 + 显式排除清单，AGENTS.md 加了指针） |

### 120.6 如实记录的边界

1. **P4 那一半（`gc` 真的删掉真实 payload）本节没有实测。** 它需要一次 `gc --apply` 的批准，而签发批准
   的 CLI 动词是 W4（`airoot issue`）；本阶段排在它前面，所以"真的删掉真实 payload"这条断言落在 W6 的
   真机验收序列里，不在本节。**静态上这条路是通用的**（`apply_gc_plan` 只认 owned + 只碰 `store/` +
   删前重算摘要），但那不是"实测过"，写在这里以免被读成已经验过。
2. **`run` 不复核摘要**（ADR-0047 决策四）：一个已经漂移的 payload 仍然会被执行。要拦它，调用方在
   `run` 之前自己 `tool verify`。这是**刻意的分工**，不是遗漏。
3. **cwd 与 env 都继承调用方**（ADR-0047 决策三）：一个往 cwd 写文件的 payload 会把文件写到调用方那边，
   而不是 store 里——这是为了**不破坏 store 的整树摘要**而付出的代价。
4. **`run` 不是"受管运行时"的完整形态**：没有 runtime health 体系、没有多版本矩阵、没有持久化。
   三条都在最小版本文档的显式排除清单里。
5. **`--json` 之外没有别的 AIROOT 选项会 hoist 到子命令之前**：`run` 只有 `--root`/`--json`（以及
   `--help`），所以这条规则的实测面就是这三个；将来加动词选项时，`_airoot_option_arity()` 会自动带上它
   （它从 parser 推导），但**没有测试**专门断言"新选项一定被 hoist"——那是推导规则的固有性质，不是判据。

## 121. 把"安装器装的东西"登记进账本（ADR-0048）

最小版本的定义最后一段要求：**账本要能表述"AIROOT 装的安装器，又装了别的东西"**，而且"那批产物可以被
登记为 reference"。§118 之后本机正好有这个真实例子——AIROOT 装的是 `rustup-init.exe`，它把 rustc/cargo/
rustup 写进了 `%USERPROFILE%\.cargo` 与 `.rustup`。本节是去把它登记进账本，**而第一次尝试当场失败了**。

### 121.1 第一步（失败）：声明两个数据根，`discover` 什么都不认，`adopt` 拒绝

| 步 | 命令 | 结果 |
|---|---|---|
| 声明 | `data-root add <home>\.rustup --id dr-rustup --role tool` | `SUCCESS`，`files_touched=0`，0.13 s |
| 声明 | `data-root add <home>\.cargo --id dr-cargo --role tool` | 同上（**没有把用户主目录整个声明成数据根**：两个目录各自一个根，role 按它们实际是什么选 `tool`） |
| 扫描 | `discover` | `dr-cargo`：扫 4 个对象 / 1 个可执行文件；`dr-rustup`：扫 5 个对象 / 6 个可执行文件。**候选全是 `unmanaged`** |
| 为什么不认 | 每个候选的 `notes` | **`no whitelist entry matched (N executable(s) inspected)`**——逐条如此（`bin` 1 个、`toolchains` **6** 个） |
| 排除名单 | — | **一条都没命中**：挡下它们的不是缓存/GUI 名单，是**没有任何谓词认识它们** |
| 登记 | `adopt <home>\.cargo\bin --mode reference --capability rust-toolchain` | **`CAPABILITY_NOT_DECLARED`(9)** |

那条拒绝的证据是本次最重要的读数，它把原因说得比散文清楚：

```text
no whitelist entry matched (1 executable(s) inspected)
freeze the capability and add a whitelist entry first
```

**缺的不是能力名**（`rust-toolchain` 已在 `cap-3` 里冻结），**缺的是 §15.4 成长路径的第二步：白名单
证据谓词**。§117.5-3 当初刻意没给它写谓词，理由是"它是从可信来源装进来的，不需要在用户目录里被认出来"
——那条理由没错，它只是**没预见到"要登记安装器写出来的东西"这个局面**。

**先量再写谓词**（这一步决定的谓词形状）：

| 可执行文件 | PE 静态事实 |
|---|---|
| `.rustup\toolchains\stable-…\bin\rustc.exe`（110 592 B） | `product_name="Rust Compiler"`、`file_description="rustc"`、**`file_version="1.98.1.0"`** |
| 同目录 `cargo.exe`（31 435 776 B）、`rustdoc.exe` | **一个版本资源都没有** |
| `.cargo\bin` 下三个 12 721 664 B 的 rustup shim（`cargo.exe`/`rustc.exe`/`rustup.exe`） | **一个版本资源都没有** |

### 121.2 裁决与落地：`wl-4` → `wl-5`（ADR-0048）

给**已冻结**的 `rust-toolchain` 加上识别谓词，两条缺一不可：

```json
{"type": "executable_name", "any_of": ["rustc.exe"]},
{"type": "pe_static", "field": "product_name", "contains": "Rust Compiler"}
```

落地之后的 `discover`（同一条命令，同一个根）：

| 对象 | `wl-4` | `wl-5` |
|---|---|---|
| `.cargo\bin` | `unmanaged`（1 个可执行文件不匹配） | **仍然 `unmanaged`**——三个 shim **没有版本资源**，PE 那半不成立 |
| `.rustup\downloads` / `tmp` / `update-hashes` | `unmanaged` | 仍然 `unmanaged`（它们是下载暂存与元数据，不是能力对象） |
| `.rustup\toolchains` | `unmanaged`（6 个可执行文件不匹配） | **`external_reference` / `rust-toolchain` / `1.98.1.0`**，入口点 `stable-x86_64-pc-windows-msvc/bin/rustc.exe` |

**这条不对称是本节最该记住的一点**：谓词认的是**真的编译器**，不是"名字像 Rust 的东西"。`.cargo\bin`
留在 `unmanaged` 里被如实报告，而不是被凑成一条版本读不出来、证据只证明文件名的引用。

### 121.3 六条断言（逐条读数）

**① `uninstall` 对 reference 一律拒绝**

```text
uninstall external/dr-rustup/toolchains --dry-run  → exit 7, OWNERSHIP_REQUIRED
  absolute path: C:\Users\ProfileName\.rustup\toolchains
  AIROOT only records it; removing the files is your call
  suggestion: airoot forget external/dr-rustup/toolchains   # drops the record, keeps the files
```

**② `forget` 之后两个目录一个字节都没少**（这是管家模型的结构性不变量，实测）：

```text
forget external/dr-rustup/toolchains → files_touched=0, source_unchanged=true
.before: .cargo 17 files / 178 160 640 B；.rustup 154 files / 603 950 873 B
         rustc.exe sha256 ca9988af88b1463f6857fdfe909299fee50a63260b3e5f7dbcb5b4d3318b27bc
.after : 一模一样（逐字节比较为 True），之后重新登记
```

**③ `doctor` 与"注册前后"的对比**

| 状态 | `doctor` | `doctor --include-unmanaged` |
|---|---|---|
| 登记后 | exit 0 / `healthy` / 3 条（全部 `info`） | 7 条：`UNMANAGED_OBJECT_PRESENT` ×4（`bin`、`downloads`、`tmp`、`update-hashes`）+ `POLICY_ONLY_MODE` + `WHITELIST_REVISION_STALE` ×2 |
| `forget` 之后 | 同上 | 同上——**数量不变**，因为消失的那一类正是被登记的那个对象 |

**"哪一类消失了、哪一类还在"要看得更细一点**：`UNMANAGED_OBJECT_PRESENT` 里有 `toolchains` 的那一条
在**登记后不再出现**（它就是"登记之后不再是 unmanaged"的那个对象），而 `bin`/`downloads`/`tmp`/
`update-hashes` 一直在——前者是"谓词认不出来"，后三个是"它们本来就不是能力对象"。同一件事在
`discover --record` 上有一个更干净的数字：**登记前记录 5 条观测，登记后 4 条**。

**④ `where rust-toolchain`：新登记的引用被选中，而优先级一个字没改**

```text
selection_reason = STEWARD_REFERENCE_HEALTHY
source=path   management=external_reference   health=healthy   usable=true
executable = C:\Users\ProfileName\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin\rustc.exe
version    = 1.98.1.0
evidence   = capability=rust-toolchain version=* scope=*
             selection precedence=steward (revision=sp-1, source=file)
             observed at … (external_reference) active_version=1.98.1.0
```

**这就是 ADR-0048 决策二说的那件事的实测**：优先级规则（steward-first，`sp-1`）一个字节没动，但结果从
**store 里的安装器**换成了**真的 `rustc.exe`**。对 agent 来说这是变好了——`where rust-toolchain` 现在
指的是能编译的那个东西，而不是安装它的那个安装器。

**⑤ `where cargo` 给出诚实的回答：没有这个能力的实现**

```text
exit 1, reason_code=NOT_FOUND, found=false, candidates=[]
evidence: capability=cargo version=* scope=*
          selection precedence=steward (revision=sp-1, source=file)
          no satisfying active binding and no external reference
```

`cargo` **不是**冻结能力（`capability list` 里没有它），所以这条查询根本不看文件系统——**它没有去猜
`.cargo\bin\cargo.exe` 在哪**。这正是本项目反复写在文档里的那条禁令（§118 也写过：要让 AIROOT 管它，
走 `discover`/`adopt`，不是让 `where` 去猜）。

**⑥ 成本与有界性（实测，不是估计）**

| 命令 | 耗时 | 有界证据 |
|---|---|---|
| `discover`（`.cargo` + `.rustup`，604 MB / 154 文件） | **0.13–0.14 s** | 两个根的 `truncated` 都是 **false**；扫描上限（深度 4 / 相对深度 2 / 每对象 400 文件 / 200 对象）一个都没触到 |
| `adopt`（PE 探测 + 摘要） | **0.14 s** | 只算入口点那一个文件的摘要（110 592 B） |
| `tool verify`（12.7 MB 载荷整树摘要） | **0.13 s** | `verified=true`，`problems=[]` |
| `doctor --verify` | **0.15 s** | `healthy` |
| `data-root add` / `tool status` / `where` | 0.12–0.13 s | — |

**没有发现无界递归**（这一项是"要量不要猜"的，所以量了；结论是有界）。

### 121.4 顺带量到的一处**政策 revision 的后果**（不是缺陷，但会被误读成故障）

改白名单 revision 之后，`doctor` 立刻报了两条 `WHITELIST_REVISION_STALE`（`info`，不是 error）：

```text
data_root=dr-cargo    recorded=wl-4   current=wl-5
data_root=dr-rustup   recorded=wl-4   current=wl-5
```

它是**对的**：`data_roots.whitelist_revision` 记的是声明那个根时用的版本，而白名单已经变了。补救办法是
**重新声明那个根**（`data-root add` 是 upsert，会把 revision 刷新），实测之后这两条 `info` 消失、
`doctor` 仍是 `healthy` 且 `diagnostics` 只剩 `UNMANAGED_OBJECT_PRESENT` + `POLICY_ONLY_MODE`。
**记在这里的理由**：一个"改了策略版本就要重新声明数据根"的连带动作，是升级路径的一部分，而不是噪音。

### 121.5 写进文档的结论：**"安装器自己装的产物"在账本里算什么**

**它算 `external_reference`——一条 AIROOT 记录、不拥有的引用；不是 `managed_tool_instance`。** 三条理由：

1. **AIROOT 没有装它，是它装的工具装的**。`is_owned` 的两个信号（`store_path` 在 `store/` 下、后端不是
   外部后端）一条都不成立，而这两个信号正是"删除之前必须成立"的判据（§14.2-3）。所以它天然落在
   reference 那一侧：可观察、可诊断、可在 `where` 里被选中，**永不被 `uninstall`/`gc` 删除**。
2. **它不由 AIROOT 定义版本**。`.rustup` 的内容由 `rustup` 自己维护（`rustup update` 会改它），AIROOT
   的登记是**观测**：`version=1.98.1.0` 是那一刻从 PE 里读出来的，`observed_digest` 是那一刻的入口点
   摘要。把它当 owned 就等于声称"AIROOT 决定它是什么版本"——那是假的。
3. **外部发现默认不迁移**（规划 §5、ADR-0004 的管家模型）。这条不变量在"安装器装了别人"这个场景下
   **没有被放宽**：恰恰相反，它正是这个场景的正确答案——AIROOT 认出来、记下来、**不去搬它**。

**所以"AIROOT 装了一个安装器，那个安装器又装了别人"这件事在账本上是两行**：一行是 store 里的
`managed_tool_instance`（`rustup-init.exe`，owned、可 `uninstall`/`gc`），另一行是用户目录里的
`external_reference`（`toolchains`，非拥有、只可 `forget`）。**删掉 AIROOT 本体不会带走第二行指向的
任何文件**——这条由 §121.3 的 ② 逐字节实测。

### 121.6 计数与影响

| 项 | 变化 |
|---|---|
| 测试 | **1329 → 1330**（+1：`test_l1_discovery.py` 的新谓词守卫。§120 那 21 条记在它自己的表里） |
| 审计检查（`test_l0_consistency.py`） | **109 → 109** |
| golden 语料 | **43 → 43**（`discover_report.json` 与 `execution_bounds.json` 的**内容**变了：`wl-5`；`golden.py` 现在**从策略文件取** revision，不再写死一个字面量） |
| 政策文件 | `policy/discovery-whitelist.json`：`wl-4` → **`wl-5`**，+1 条 `rust-toolchain` 条目 |
| 新增 ADR | **ADR-0048** |
| 不动的东西 | **没有 schema 变更、没有退出码变更、没有新动词**；`selection-policy.json`（`sp-1`）与冻结能力清单（`cap-3`）一个字节没动 |

### 121.7 如实记录的边界

1. **`.cargo\bin` 没有被登记，这是结论而不是遗漏**：它的三个 shim 没有任何版本资源（实测），所以
   谓词认不出来。要让它们进入账本，需要一条**基于文件名**的谓词——而那会登记出三条"每一个事实都读不出来"
   的引用（ADR-0048 明确否决了那条路）。它今天仍然出现在 `UNMANAGED_OBJECT_PRESENT` 里。
2. **`.rustup\downloads` / `tmp` / `update-hashes` 也没有登记**：它们是下载暂存与元数据，不是能力对象。
   排除名单**没有**覆盖它们（`tmp` 与 `-tmp`/`.tmp` 差一个字符），所以它们靠"没有谓词匹配"留在
   `unmanaged` 里——**如果将来有人给某个能力写了匹配这些目录的谓词，这里会变**。
3. **这次登记不改变"谁能删什么"**：reference 仍然不可 `uninstall`/`gc`；数据根内任何目录仍然永不被删除。
4. **`where rust-toolchain` 的答案变了**（从安装器变成真编译器）。这是 steward-first 优先级早就写好的
   行为，不是新规则；但如果有人依赖旧答案，这里就是那个变化点，**不是回归**。
5. **跨用户与 ACL 仍然没测**：两个目录都在当前用户主目录下，登记与观测都只用本用户权限；这与 §115 的
   跨用户边界是两件事，本节没有改变它。
6. **`rust-toolchain` 的谓词只覆盖 MSVC 目标的工具链**（`rustc.exe` + `Rust Compiler`）：本机只有
   `stable-x86_64-pc-windows-msvc` 一个工具链，其它目标/工具链没量过——**它在谓词下会不会被认出来是
   未测的**，不是"应该也会"。


## 122. 签发是一个动词（ADR-0049）

**这一节修的是一个"能力真实存在、但接口里没有名字"的缺陷。** §117 已经在真机上走完 `plan` → 签发 →
`install` 报 `FINALIZED` 的全程，§119 又把"这一版没有生产签发方"这类旧话逐面收干净。可是到这一轮才发现：
那一步在 CLI 里**没有动词**，只能 `import airoot.tx.issuer`。于是一个按 `agents/airoot.json` 工作的
agent 读完 lane 表会得出结论——"安装这条路在这个 build 里走不通"——而它其实只差一次显式签发。
**一个真实但没有名字的能力，等于一个没人能用的能力**；这是 ADR-0049 的全部理由。

### 122.1 动词的形状（一个薄封装）

```text
airoot issue <plan_file> --out <token.json> [--provision] [--mode policy|human]
             [--approved-by-sid <SID>] [--ttl-minutes N] --json
```

**它是 `tx/issuer.py` 的薄封装，一行决策都不留在自己手里**：token 里的事实（`plan_hash`、
`root_instance_id`、`machine_id`、`policy_revision`）全部**读自计划**，`--provision` 走模块的
`provision`，`--mode human` 不带 `--approved-by-sid` 由模块拒绝（"an agent request is never a human
approval"），`--ttl-minutes` 只是把默认 5 分钟换掉。真机根上的一次真实调用：

```text
issue D:\env\plan_rust-toolchain_1.83.0_5e63c3f68c3a.json --out D:\env\token-probe.json --json
  exit 0  approval_id=approval/17d6e8fe3b7f  approval_mode=policy  key_id=airoot-local-issuer-1
  provisioned=false  permission_proof=false  reason_code=SUCCESS
```

**输出文档里必须带着那句"这值多少钱"。** 调用方会把这份文档引用回来，所以
`permission_proof: false` 与 `note` 中的 ADR-0046 指针是**文档的一部分**，不是文档旁边的一行注释——
它们由 `test_cli_issue.py` 单独守卫（把 `permission_proof` 改成 `true`，那条测试立刻红）。

### 122.2 没有新增任何协议面

| 问题 | 答案 |
|---|---|
| 新 schema？ | **没有**。token 是既有的 `approval-token`，输出报告没有 schema（§94 的 `document_schema: null`） |
| 新 reason code / 退出码？ | **没有**。`SUCCESS`、`INVALID_INPUT`(8)、`PROVENANCE_FAILED`(7)、`INVALID_APPROVAL`(4) 全部是既有的码 |
| 核心会不会自己签发？ | **不会**。`approve`/`install` 仍然是**消费**方：`test_consuming_an_approval_never_provisions_a_signing_key` 断言走完 `approve` 之后 root 里**没有** `state/issuer-key.json` |
| 有没有给它新权力？ | **没有**。同用户进程本来就能 `import` 这个模块或换掉 keyring；动词增加的是**可发现性**，而这一点写在它自己的输出里而不是留给人猜 |
| `fake_issuer.py` 要改吗？ | **不用**。测试路径仍然用它（不需要操作者的 root 里已经 provision 过密钥），两条路互不影响 |

### 122.3 守卫与"验红"

新文件 `cli/tests/test_cli_issue.py`（10 条）。除端到端三步（`provision` → `issue` → `approve`）之外，
每条守卫都对应一个具体的坏法，并且**逐个把坏法放回去验过红**：

| 放回去的缺陷 | 被哪条守卫抓住 |
|---|---|
| 文档不再说"这不是授权证明"（`permission_proof: true`） | `test_the_document_says_the_approval_is_not_a_permission_proof` |
| `provision` 的拒绝被吞掉，动词继续往下签 | `test_provision_refuses_to_overwrite_a_key_and_the_refusal_is_passed_through` |
| `--ttl-minutes` 解析了但没传给签发方 | `test_ttl_minutes_reaches_the_token` |
| `human` 模式自己编一个 SID | `test_human_mode_without_a_sid_is_refused_by_the_module` |
| `--provision` 变成无条件执行（普通 `issue` 会轮换密钥） | `test_a_plain_issue_rotates_nothing_and_writes_only_the_token` |

**`PROVENANCE_FAILED` 那一条要分清两个不同的拒绝**：没有**密钥**时是 `tx/issuer.py` 的
"no signing key is provisioned"（并点名 `provision` 这个补救步骤），没有**keyring** 时才是
`ISSUER_PENDING`。测试断言的是前者，因为 `issue` 走的是模块的 `load_private_key`；把两者混起来写，
测试会在"拒绝理由对不对"这件事上说假话。

### 122.4 这个动词让哪些话变成了假话（§119 的同一类，又长出来一次）

§119 记的是"裁决改了、五个面还在说旧结论"。这一次是"**新动词出现了，五个面还在说没有动词**"——
同一类缺陷的第二个实例，所以修法也一样：**不许把那句话改软，只能改成真的**。

| 面 | 原来那句 | 现在 |
|---|---|---|
| `agents/airoot.json` 的 `approve.why_no_lane` | "no CLI verb mints one" | 承认动词已存在，剩下的是**决定**而不是缺口：要不要让 agent 无人值守地走完 approve/install，是 ADR-0025 D1 留给 P2 的问题 |
| 同文件 `install.why_no_lane` | 同上 | 同上；计划那一半仍然有 lane（`plan`、`scope decide`） |
| 同文件 `unmapped_verbs.uninstall` | "needs a token that no CLI verb mints" | 指向命令地图里新加的那一行（`issue`） |
| `SKILL.md` 命令地图 | 没有这一行 | 新增一行，且**只教 `airoot issue`**：`approve`/`install` 在 `uncovered_verbs` 里带着非空解锁词，命令地图**不许**把它们教成可执行的 |
| `references/confirmation.md`、`references/reason-codes.md` | "没有 CLI 动词会替你签" | 写成 `airoot issue … --provision`，并保留"已有密钥时拒绝覆盖"这条 |
| `tx/approval.py` 的 `ISSUER_PENDING` 证据行 | 指向 `airoot.tx.issuer` 这个**模块** | 指向 `airoot issue` 这个**动词**（句子本身不动：指向 ADR-0046 的那句仍由 §119 的守卫钉着） |
| `AGENTS.md` §6 的 `--token-file` 命令块 | "先 provision 本 root 的密钥（`airoot.tx.issuer`）" | 三步命令写全（`issue` → `approve` → `install`），并写明 `--provision` 只在第一次 |

**顺带发现并补上了一条守卫。** `honesty.document_schema_note` 一直写着"这 30 份报告有 30 个互不相同的
顶层形状"，而 §100 的论证（"一个 schema 描述不了 N 个不同形状，所以不收口"）**整个压在"形状互不相同"
这句话上**——此前没有任何东西度量它。这一轮加了 `shape_distinctness_problems`：把同一批未固定的报告按
`frozenset(document)` 分组，任何一组多于一份即报缺陷，并配一条反向用例（两个键集相同的报告必须被抓）。
加上 `issue` 之后实测是 **31 份报告 / 31 个形状**，那句话现在是**被度量的**而不是被相信的。

**这一轮的“验红”有一半是自然发生的**：动词加进去之后，整套测试以 **7 条红灯**报出七个面各自的缺口
（`issue` 没有 lane、有 lane 却在命令地图里没被教、命令地图教了它不该教的 `approve`、`run` 的 lane 注释
丢了“怎么拿到 token”、`AGENTS.md` 的 read 路径计数、审查报告的测试总数、草案计数链的终点）。
**“加一个动词要动多少面”这件事是这 7 条红灯数出来的，不是想出来的**——这正是这些守卫存在的理由。

### 122.5 一个具体的记账修正

`AGENTS.md` §7 原写"**34 条** agent lane 里只有 5 条读的文档被已发布 schema 描述"。§120 加 `run`
之后 lane 数是 35，本节加 `issue` 之后是 36——**这句话在 §120 那一轮就已经旧了，只是没人查**。
它与 `honesty.document_schema_note` 是同一事实的两处手抄；后者由测试逐字比对（5 of 36），前者靠人。
这一节把前者改成 36，并记下**它没有守卫**这件事：想让它有守卫，得让 §7 引用文件里的数字而不是自己写。

### 122.6 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1330 → 1340**（+10：新增 `cli/tests/test_cli_issue.py` 十条；守卫加严用的是既有测试函数，不新增计数） |
| 常驻一致性检查（`test_l0_consistency.py`） | **109 → 109** |
| golden 语料 | **43 → 43**（`issue` 的输出是报告、不进语料；没有改任何被语料固定的文档） |
| schema | **20 → 20** |
| `agents/airoot.json` | +1 lane（`issue`，11 条 `read` 路径）、3 处理由重写、`document_schema_note` 的计数 35→36 / 30→31 |
| 新增 ADR | **ADR-0049** |
| 新守卫 | `shape_distinctness_problems` + `test_cli_issue.py` 的 10 条；`AGENTS.md` 的 read 路径计数 151 → 162 |

### 122.7 这一节没有做的事

1. **`approve`/`install` 仍然没有 lane**，只是理由改成了真的。要不要给它们 lane 是一个**裁决**（agent
   能不能无人值守走完安装），本节刻意不顺手回答——顺手回答就会变成"用一次改文档代替一次裁决"。
2. **受保护签发方仍然不做**（ADR-0044 的结论不变）。这个动词不让私钥变得更安全：它仍然可被同用户进程
   读取，`permission_proof: false` 就是这句话的机器可读形式。
3. **没有给 `issue` 加策略**：它不判断这个计划该不该被批准，也不记 `approved_by_sid` 以外的"谁批准了"。
   批准是账本，不是授权——把它当授权用是使用方（上游 harness）的责任（ADR-0045）。
4. **没有动 `env persist` / `tool gc --apply` / `uninstall` 的 token 路径**：它们本来就是消费侧，本节只在
   `AGENTS.md` 与两份参考文档里把"怎么拿到 token"写全。
5. **`--mode human` 不会去读本机 SID**：调用方给什么就记什么，不给就拒。自动填一个 SID 会让
   "human"这个取值变成装饰。
## 123. 稳定入口：一个静态 `.cmd`，权威仍在 registry（ADR-0050）

**这一节把最小版本判据 #10 从"❌ 目录不存在"变成"✅ 真实存在且能转发"。** ADR-0050 推翻了 ADR-0025 的 D4
（"P1 不写任何 launcher"），理由不是"我们改主意了"，而是 D4 把两件事合在一句话里：**让文件存在**在
`policy_only` 下本来就不受保护，**把这一条放进 machine PATH** 才需要受保护状态——而后者 AIROOT 仍然不做。

### 123.1 形状与写入点

```text
<root>\cli\exposure\bin\<capability_id>.cmd        # 一个能力一个文件，CRLF
```

内容里**没有版本、没有 instance id、没有 `store/` 路径**：它调用
`airoot run --capability <id> -- %*`，由 CLI 在**调用时**从 registry 解析 active binding。写入点是事务的
`EXPOSED` 步（两个 runner 共用 `caps/launcher.py:write_launcher`），在 journal 推进之前完成，所以写不出来
就等于 `EXPOSED` 没有发生；重放幂等（内容相同不写字节）。

**顺带修好了一个从没被对过的目录**：`LAYOUT_DIRS` 建的是 `<root>\exposure\bin`，而
`pathexposure.sanctioned_entry` 按冻结契约算的是 `<root>\cli\exposure\bin`——在一个刚建好的 root 上，
"sanctioned entry"**根本不存在**，而 `path verify` 的 `launcher_present` 恰好在问那个目录在不在。两个都建；
`exposure/bin` 保留（runtime 的 binding/view 记录在 `exposure/` 下）。**这是本节第 1 个"读代码才发现"的缺陷。**

### 123.2 跑一次才发现的两个缺陷（都在 argv 与转义层）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `run --capability <id>` 报 `invalid choice: '<id>'`；`exec --env X -- cmd` 同样 | `_normalize_child_argv` 把"提升的选项"放在**动词之前**，而它们是**子解析器**的选项，顶层解析器不认识它们（选项的值被搬到了动词位置） | 改成 `[全局..., verb, *hoisted, *owned, *child]`。§120 的那条守卫原来断言的是"选项在动词之前"这个**实现细节**，现在断言的是**性质**：AIROOT 的选项在动词与分隔符之间，且永不进入 payload 的 argv |
| 2 | launcher 跑起来报 `unrecognized arguments: --version` | 转发行是 `... run --capability <id> %*`，**没有 `--`**，于是调用方的旗标被 AIROOT 解析 | 转发行改成 `... run --capability <id> -- %*`。**一个叫 `cargo` 的稳定入口必须表现得像 `cargo`**——这句是实测出来的，不是设计出来的 |
| 3 | `run --capability <id> -- --version` 解析成 `instance='--version'` | 位置参数 `instance` 是 `nargs="?"` 且另一个是 `REMAINDER`：`--` 之后的第一个 token 被喂给了可选位置参数 | `run` 只留一个 `REMAINDER`（`rest`），`cmd_run` 显式切分：`rest[0]` 是目标（除非它是 `--`），`--` 之后全是 payload 的 |

**第 3 条是第 2 条逼出来的**：没有 `--` 就没法转发旗标，加了 `--` 才暴露 argparse 这个形状问题。

### 123.3 `where` 与 `path verify`

- `where` 新增 `launcher`（`string|null`）：只有 owned + machine 级 + 文件真的存在时才给路径。**schema 里是
  「可选」属性**——加必填是破坏性变更（`AGENTS.md` §7 要新 schema id），而"可选"与"总是发"是两件事，后者
  才是承诺，由 `test_l1_launcher.py` 钉住。
- `path verify` 的 `launcher_present` **改了含义**：从"那个目录在不在"改成"**至少有一个稳定入口**"。旧读法
  在一个空目录上报 `true`，那不是判据 #10 问的事。同一次加了三类漂移发现（仍是冻结码 `PATH_EXPOSURE_VIOLATION`）：
  **binding 没有入口**、**入口与这个 build 会写的不一致**（手改、解释器或 checkout 搬家）、**入口没有 binding**
  （retire 清了绑定没清文件）。判漂移的方法是**重新渲染再比字节**，不解析 `.cmd` 的一行。
  `path verify` 还要在 registry 读不出来时**照常工作**：那种情况作为一条 info 说明，而不是把只读诊断变成失败。

### 123.4 守卫与验红

新文件 `cli/tests/test_l1_launcher.py`（16 条）。四个"把缺陷放回去"的实测：

| 放回去的缺陷 | 被哪条守卫抓住 |
|---|---|
| 转发行去掉 `--` | `test_the_launcher_bytes_are_crlf_and_name_no_version`、`test_the_launcher_itself_forwards` |
| 提升的选项放回动词之前 | `test_every_child_verb_survives_the_argv_rewrite_as_itself`（性质断言）、`test_run_with_a_capability_resolves_the_active_binding` |
| `EXPOSED` 不再写入口 | `test_the_exposure_step_writes_the_stable_entry` |
| `launcher_present` 退回"那个目录在不在" | `test_an_empty_launcher_directory_is_not_a_stable_entry` |

**最强的一条是 `test_the_launcher_itself_forwards`**：它用 `cmd.exe` **真的执行**那个 `.cmd`，并要求子进程的
输出回来。判据 #10 问的是"能转发"，所以测量也必须是"真的转发"——它也正是先报出缺陷 2 的那条。

### 123.5 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1340 → 1357**（+17：新增 `cli/tests/test_l1_launcher.py`；既有测试里有若干条按新语义改判据，不新增计数） |
| golden 语料 | **43 → 43**（`where_*.json` 七份各多一行 `launcher`，重新生成；`index.json` 同步） |
| schema | **20 → 20**（`where-response` 新增**可选** `launcher`，不新增 id） |
| `agents/airoot.json` | `where` 两条 lane 读 `launcher`；`path verify` lane 读 `launchers[].path`/`launchers[].matches_current`；read 路径 162 → 166 |
| 新增 ADR | **ADR-0050** |
| 新增模块 | `cli/app/airoot/caps/launcher.py`（`caps/` 26 → 27） |
| 新增参数/动词 | `run --capability <id>`；`run` 的位置参数改成单个 `REMAINDER` |

### 123.6 这一节没有做的事

1. **没有写 machine PATH**（仍然是唯一没做的那一条，属使用方/可选 P9）。稳定入口只是"存在且能转发"。
2. **没有把 launcher 变成二进制**（ADR-0050 的比较里写了为什么）。它同用户进程可改写，`path verify` **度量**
   这件事而不是假装关掉了它。
3. **`where` 的 `launcher` 不是"能不能用"的判据**：`usable` 仍然只讲那个 payload，入口在不在是另一件事
   （由 `path verify` 报）。把两者混起来会让"入口丢了"看起来像"能力坏了"。
4. **没有给 reference 建入口**：reference 的稳定入口是用户自己的环境（§14.2），AIROOT 不替它建。
## 124. 把整条闭环跑成可复跑的验收（W6），并清掉残留的测试目录（W1）

**判据 #16 要的是"这条闭环能被重复跑一遍"，而不是"某一次跑通过"。** 这一节把最小版本定义里那条闭环
（`plan → issue → approve → install → FINALIZED → verify → run → where → 稳定入口 → retire → gc（真的删
payload）→ doctor 无 error`）做成 `cli/tests/real_machine_acceptance.py` 里的第二个半边：它自建一个临时
root，**不联网**，用 `adopt --mode import` 从一个本地文件造出真实 artifact——于是整条路既不需要上游，也不
需要操作者的 root 里预先有东西。

### 124.1 它量到的两件事

| 断言 | 为什么单独列出来 |
|---|---|
| **第二次 `install` 同一份计划是终态，且不推高 generation** | 这条才是"幂等"的机器可读形式。签名被消费掉之后要再签一份 token 才能重放，所以它同时量到了"批准是一次性的"与"重放不会第二次安装" |
| **`retire` 之后 `path verify` 报的是漂移（exit 2 / violations=1），不是健康** | ADR-0050 的三类漂移里最容易悄悄消失的一类：入口还在、绑定没了。**如果它报 0，那这条验收就是空的** |

同一次还量到：`--provision` 第二次是 `INVALID_INPUT`(8) 且**密钥字节不变**（不是轮换）、`run --capability`
真的启动了 payload 且 `persisted=false`、`where` 给出的 `launcher` 就是 `cli\exposure\bin\archive.cmd`、
`gc --apply` 之后那个 store 目录**真的不在了**。**.tmp 之外没有写任何东西，PATH/注册表/ACL 全程未动。**

### 124.2 验收自己报了一次假阴性

第一次跑，"`where` 给出的稳定入口就是那个文件"这条**红了**，而两边打印出来是同一个路径：`root` 来自
`tempfile.mkdtemp`，CLI 拿到的是它**规范化**之后的拼写（大小写/短名），字符串比较因此不等。修法是两边都
`.resolve()` 再比，而且**把两个拼写打出来**。这一条值得记：一个验收脚本的假阴性会和假阳性一样浪费一轮，
而它比假阳性更坏——**它会让人去改没坏的东西**。

### 124.3 这一节没有做的事（写清楚，免得被当成做到了）

1. **P4 的四个退出条件没有做故障注入**：这一节的回滚证据是"真实的成功路径 + 真实的删除"，不是"中途断电
   再 `repair`"。故障注入目前只在 pytest 套件里（`conftest.FaultInjector` + `tx/rollback.py`）。
2. **`--online` 那一段仍然只到 fetch/verify**，不跑 stage/commit（§59 的边界，不是新的）。
3. **没有跨 root 重跑**：验收每次自建临时 root；"同一个 root 上跑第二遍"由 pytest 的幂等测试覆盖。

### 124.4 成本与 W1

| 项目 | 结果 |
|---|---|
| 测试 | **1357 → 1357**（验收脚本不是 pytest 模块，不进这个数；它的结论是 `real-machine acceptance: PASS` + `closed loop: PASS`） |
| golden 语料 / schema | **43 / 20**（不变） |
| 新增 ADR | 无（这一节是验收，不是设计变更） |
| W1 | `cli/tests/.tmp/` 的 7 个残留目录（`agent-acl-finish`、`agent-aclwrite`、`orchestrator`×2、`airoot-test-*`×3）已清掉；它本来就会被 pytest 的会话夹具删掉，残留只说明某些运行是被中断的 |
## 125. 判据 #11 的另一半：在真机闭环上切一次活跃版本

**§124 把闭环跑通了，但没有切过版本**；判据 #11（切换活跃版本不改 PATH、不重写入口）当时只有 pytest 断言。
这一节把它搬进真机闭环：同一个能力先装 `9.9.9`，再装 `9.9.10`，然后量四件事。

| 量的东西 | 实测 |
|---|---|
| 切换是否真的绑到了新版本 | `where archive` 的 `instance_id` 变成 `archive/probe-tool/9.9.10/win-x64`，`version=9.9.10` |
| 稳定入口有没有被重写 | `entry.read_bytes()` **前后逐字节相同** |
| `where` 报的入口路径有没有变 | 不变，仍是 `cli\exposure\bin\archive.cmd` |
| machine PATH 有没有被动过 | `machine_path()` 前后**逐项相同**（这里是**读**出来的，不是假设"没人写它"） |

**第四条值得单独说**：整个项目一直声称"没有任何动词写 machine PATH"，但那是**没有被度量过的声称**。
验收脚本现在在切换前后各读一次真实的 machine PATH 并比较——**"我们没写"变成了"读出来没变"**。
这不会让写 PATH 变成不可能（同用户进程仍可写），它只是让**这一条**从信条变成读数。

### 125.1 为什么这条不能只用 pytest 代替（而 §124 之前正是那样）

pytest 里的 `test_a_new_version_rewrites_no_launcher_bytes` 用的是**模拟 runner**，它证明"写入口的那段逻辑
与版本无关"。真机闭环证明的是另一件事：**从 `adopt` 到 `install` 这条真实路径上，切换到第二个版本之后
整条链（entry 字节、入口路径、PATH、`where` 的目标）仍然一致**。前者是单元性质，后者是验收判据 #11 的
字面要求。**两者都要有，而在此之前只有前者。**

### 125.2 成本

| 项目 | 结果 |
|---|---|
**测试数不变**：1357 → 1357。验收脚本不是 pytest 模块，它的结论仍是 `closed loop: PASS`——这一节只多了四条 check，没有多一个测试函数。
| golden 语料 / schema / ADR | **不变**（这一节没有设计变更，只有一次测量） |
| 定义文档 | `docs/AIROOT-最小版本-v1.md` §5 的 #11 从"⚠️ 一半"改成"✅"，#14 仍是"⚠️ 一半"，§4 的括注随之只剩一条 |

**剩下的一条（#14）现在有了确切的配方**：`TransactionJournal` 吃一个带 `checkpoint(state)` 的注入器
（`conftest.FaultInjector` 就是它），`ArtifactRunner(registry, backend, injector=...)` 是可注入的入口，
所以下一个阶段要做的只是"在真机闭环上逐个状态注入一次、再跑 `repair`"，而不是再设计什么。
**把配方写在这里，是为了让下一轮不必重新测量一遍同样的东西。**

**#14 下一轮必须先从这一个矛盾量起。** 注入的接口是清楚的：`TransactionJournal` 吃一个带 `checkpoint(state)` 的对象（`conftest.FaultInjector` 就是它），`ArtifactRunner(registry, backend, injector=...)` 接受它，`_runner_for` 用 `resolve_backend(plan["metadata"]["backend_id"], root=...)` 选后端。但上一次按同样方式构造时，**在到达任何状态之前**就报了 `AirootError: path escapes the AIROOT root: <root>\store\archive\probe-tool\9.9.9\win-x64`；而按代码读，`ArtifactRunner` 的 `self.root = Path(registry.path).parent.parent`、`Registry.db_path = root/state/registry.db`，两者推出来**都应该是 root**——**读数与代码不一致，所以先解释这个矛盾，再写断言**。那一次后续 `repair` 报的是 `action=resume_or_expire`、`repaired[0].result.outcome=DIGEST_MISMATCH`（`where` → `NOT_FOUND`、`doctor` healthy），但那是被那个早期失败污染过的事务，**不能当作 #14 的期望形状**。
## 126. 一次真机注入尝试挖出的路径缺陷：同一目录的两种拼写（W6 的 #14 副产品）

**这一节是"跑一次"比"读代码"强的又一个例子。** §125 记下：为了给判据 #14 接故障注入，按同样方式构造
`ArtifactRunner`，结果**在到达任何状态之前**就报 `path escapes the AIROOT root: <root>\store\...`，
而按代码读两边推出来都应该是 root。这一节把那次的矛盾量清了。

### 126.1 实测（一次就够）

```text
ROOT          = C:\Users\PROFIL~1\AppData\Local\Temp\airoot-round4-...\root     ← 8.3 短名
registry.path = ...\root\state\registry.db
runner.root   = ...\root                                        ← 与 ROOT 同拼写
candidate     = ...\root\store\archive\probe-tool\9.9.9\win-x64   ← 同上
异常里的路径   = C:\Users\ProfileName\AppData\Local\Temp\...\root\store\...  ← **长名**
```

`canonicalize`（`paths.py:91`）在 `paths.py:100` **解析候选**（`candidate.resolve()` 会把 `PROFIL~1`
展开成长名），却在 `paths.py:103` 拿这个解析结果去和**没有解析的** `Path(root)` 比。于是同一个目录的两半
拼写不同，一个**明明在 root 里**的路径被判成越界。**这不是测试环境的问题，是函数本身只解析了一边。**
CLI 一路没踩到，是因为 `Context` 在入口把 root 规范化过一次；**把裸 root 交给库的调用方会踩到**。

### 126.2 修法与守卫

两边都解析之后再判包含关系（`resolved_root = Path(root).resolve()`），异常文本改报**调用方给的那个
`path`**而不是解析后的候选（报候选会让"你给的路径"和"我看到的路径"混在一起，正是 §119 那一类）。

守卫 `cli/tests/test_l1_paths.py`（新文件，2 条）：

| 用例 | 断言 |
|---|---|
| 同一个 root 的**两种拼写** | `GetShortPathNameW` 拿到 8.3 拼写（拿不到就**跳过并说明**），两种拼写都必须接受同一个子目录，且返回同一个路径 |
| 真的越界 | 仍然报 `PATH_ESCAPES_ROOT` —— **修法不能把检查关掉** |

**验红**：把 `paths.py` 改回"只解析候选"，第一条立刻失败（这台机器的 `TEMP` 就是短名路径，所以这不是
理论用例）；还原后逐字节相同。

### 126.3 为什么它属于这一轮而不是"顺手"

判据 #14 要求"中途崩溃能恢复或按规则回滚"在真机闭环上被验过，而**那一步的前提是能把事务停在某个状态**。
停在半路需要直接调用库；而**直接调用库在这台机器上会先撞上这个缺陷**。所以"先修它"不是绕路，是 #14 的
前置条件——**#14 本身仍然只做了一半**（注入器已接得上，断言尚未写），这一点在 §5 的判据表里没有变。

### 126.4 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1357 → 1359**（+2：`cli/tests/test_l1_paths.py` 两条——两种拼写都要被接受、真的越界仍然拒绝。**跳过的那条也算一条测试**：它在没有 8.3 拼写的卷上 skip，但仍然被收集） |
| 行为变化 | `canonicalize` 的**接受集变宽**（同一目录的另一种拼写不再被误判），**拒绝集不变**（真的越界仍然拒绝）——按 ADR-0021 属"放宽"，而它修的是一条被错误实现的规则，不是放松一条约束 |
| golden 语料 / schema / ADR | 不变 |

### 126.5 修好之后试注入：**注入器没有触发，而且直驱与 CLI 走出来的结果不一样**

路径缺陷修掉之后，同一条直驱路径能跑完了，但它量出的东西与预期不同，**两条都必须先解释清楚**：

```text
commit returned without interruption (injector did not fire)
where (parked)            exit=0 instance=archive/probe-tool/9.9.9/win-x64 health=healthy
repair                    exit=0 action=reconcile_binding_then_resume_or_revert
                          repaired[0].result: state=ROLLED_BACK journal_seq=10
                          failure.code=VERIFY_FAILED 'post-bind verification failed'
                          generation_before=0 generation_after=2
where (after repair)      exit=1 NOT_FOUND
doctor                    exit=0 healthy
```

1. **注入器确实触发了，是我的探针读错了。** 上一轮这里写着"一次都没触发"——**那句话是错的**，测量如下：`journal.py:214` 调 `self.checkpoint(tx)`，`:266-268` 把 `str(tx["state"])` 交给注入器。换一个**记录每一个状态**的注入器之后，序列是

   ```text
   ['PROPOSED','APPROVED','FETCHED','VERIFIED','STAGED','COMMITTED','REGISTERED','ACTIVE_BOUND']
   commit returned: {"state": "ACTIVE_BOUND", "journal_seq": 7, "failure": null}
   ```

   **`commit` 不抛 `InterruptedError`——它把停住的事务原样返回**。上一轮的探针只区分"抛了 InterruptedError"与"抛了别的"，于是把"正常返回的停住事务"读成了"注入器没触发"。**要写 #14 的断言，必须读返回值里的 `state`，不能等异常。**
2. **同一条直驱路径的结果与 CLI 不同**：`adopt --mode import` 的同一份计划，走 CLI 的 `install` 报 `FINALIZED`（§124/§125 实测），直驱 `ArtifactRunner.commit(plan, token)` 却在 post-bind 校验处 `VERIFY_FAILED` 并回滚（`tree_digest(store_dir) != row["artifact_digest"]`）。**同一个 API、同一份计划、两种结果**，说明两者之间有一处我们没看见的差异（时钟、registry 句柄、还是 `drive()` 的入口条件）。**在解释它之前，任何 #14 断言都会把其中一个当成"对的那个"。**

2. **停住之后 `repair` 的行为（这一条才是真问题）**：`where` 在停住时看到实例**已绑且 healthy**（提交点确实生效了），随后 `repair` 报

   ```text
   action=reconcile_binding_then_resume_or_revert  state=ROLLED_BACK  outcome=VERIFY_FAILED
   同一份计划不注入时是 FINALIZED，generation 1；注入后 repair 走到 generation 2
   where (after repair) -> NOT_FOUND
   ```

   **同一个提交点被走第二遍，`generation_after` 从 1 变成 2**，然后在 post-bind 校验处失败并回滚。所以下一轮的真正问题不是"怎么接注入器"（已解决），而是：**从 `ACTIVE_BOUND` 恢复时重放提交点、再因 post-bind 校验失败而回滚——这是设计要的行为，还是一个重放缺陷？**
   判据是现成的：pytest 里已有针对 **artifact runner** 的故障注入覆盖（若它期望 `FINALIZED` 而这里回滚，那么两者之间又有一处差异要解释；若它也期望回滚，那 #14 的断言就照这个形状写，并把它写进文档）。**在回答之前不要动 `tx/rollback.py` 或 resume 逻辑。**
**这两条是这一轮的实际产出**：路径缺陷是真缺陷（已修、已守卫）；而注入本身还差"读一处调用点 + 解释一处差异"，不是"设计还缺什么"。**#14 仍然是 16 条里唯一只做了一半的那条。**
## 127. #14 的答案：`repair` 用错 driver ——真 artifact 的事务**永远修不回来**

上一节把问题问对了："从 `ACTIVE_BOUND` 恢复时重放提交点再回滚，是设计还是缺陷？" 这一节给出答案，
而且它比"缺覆盖"严重：**这是一个可复现的缺陷**。

### 127.1 判据是现成的，而且它说的很清楚

`cli/tests/test_l1_transaction.py` 的 `test_every_boundary_is_recoverable` **对 happy path 的每个状态各跑一次**
（`@pytest.mark.parametrize("boundary", list(happy_path_states()))`），断言：

```python
tx = runner.commit(plan, token)
assert tx["state"] == boundary, "the interruption happens only after the state is durable"
...
result = repair(registry, tx["transaction_id"], clock=clock, keyring=fake_issuer.keyring())
assert result["state"] == "FINALIZED"          # ← 每一个边界，包括 ACTIVE_BOUND
assert len(active) == 1 and registry.generation == active[0]["generation"]
assert registry.integrity_problems() == []
```

**设计要的是"每个边界都恢复到 FINALIZED"**，不是回滚。而 §126.5 量到的是：真 artifact 停在 `ACTIVE_BOUND`
之后 `repair` 报 `ROLLED_BACK / VERIFY_FAILED`、`where` 变成 `NOT_FOUND`。

### 127.2 原因（读一处就够）

`tx/simulate.py` 的 `repair()` **把 driver 写死成模拟 runner**：

```python
def repair(registry, transaction_id, *, clock=SYSTEM_CLOCK, keyring=None):
    journal = TransactionJournal(registry, clock=clock)
    tx, _plan, _token = journal.load_context(transaction_id)
    ...
    runner = SimulationRunner(registry, clock=clock, keyring=keyring)   # ← 无论这份计划是什么后端
    result = runner.resume(transaction_id)
```

于是**真 artifact 的事务被模拟 runner 接着跑**：它把提交点重走一遍（`generation` 1 → 2），用**它自己的**
post-bind 校验去核一个真 artifact 实例，失败，回滚。三个读数（generation 2、`VERIFY_FAILED`、
`NOT_FOUND`）与这个原因**逐条对上**。

**而 `install` 那一侧是不写死的**：`cli.py` 的 `_runner_for` 按 `plan["metadata"]["backend_id"]` 选
（`fake_fixture` → 模拟，其余 → `ArtifactRunner`）。**同一个状态机、两种 driver，恢复路径上只有一处会选错。**

### 127.3 后果（为什么这不是"覆盖不够"而是缺陷）

* 判据 #14 的字面要求是"中途崩溃能恢复**或按规则回滚**"。**这里既没恢复、回滚也不按规则**：规则说回滚只切
  binding，而这次回滚把一次**本来会成功**的真实安装判成了失败——`where` 从 healthy 变成 `NOT_FOUND`。
* 真实场景下这意味着：**真机安装中途崩一次，`repair` 会把已经装好的东西解绑**，而用户看到的是一次
  "校验失败"。§117 那次真机安装能成功，是因为它没崩。
* §109 / ADR-0035 把"回滚语义的唯一实现"放在 `tx/rollback.py`，两个 runner 共读它——**共享的是回滚，
  不是 driver 选择**；`repair` 的 driver 选择从来没被接上。

### 127.4 下一轮要做的（配方，不再需要测量）

1. 把 driver 选择抽成**一处**（`tx/runners.py` 的 `runner_for(registry, plan, *, clock, keyring=None,
   injector=None)`，在函数内 import 两个 runner 以避免环），`cli._runner_for` 与 `repair` 都用它；
2. 用 journal 里已有的 `plan` 决定 backend（`repair` 已经 `load_context` 出了 `plan`，它现在把 `_plan` 丢掉了）；
3. 守卫写成 artifact runner 版的 `test_every_boundary_is_recoverable`：停在 `ACTIVE_BOUND`（以及其余边界）
   之后 `repair` 必须到 `FINALIZED`、`generation` 只加 1、`integrity_problems()` 为空、二次 `repair` 是
   `no_action`——**先把这条测试写出来看它红**，再修；
4. 定义文档 §5 的 #14 要等这条测试绿了才能改成 ✅。

**在测试红之前不改 `repair`**：这是这一节留给下一轮的顺序。
## 128. #14 的修复：`repair` 用**事务自己的** driver —— 以及修完之后露出来的第二个缺陷

§127 把缺陷定位到一行：`repair()` 无论计划什么后端都构造 `SimulationRunner`。这一节按 §127.4 的顺序
**先写守卫、看它红、再修**（红：`6 failed`）。

### 128.1 修法：把"选 driver"变成一处

新模块 `cli/app/airoot/tx/runners.py` 的 `runner_for(registry, plan, *, clock, keyring=None, injector=None)`，
按 `plan["metadata"]["backend_id"]` 选（`fake_fixture` → 模拟；其余 → `ArtifactRunner` + `resolve_backend`）。
两个调用方都改用它：`cli._runner_for`（本来就是这个规则）与 `tx/simulate.repair`（**它原来写死模拟 runner**）。
`repair` 同时用上 journal 里**本来就取出来又被丢掉的**那份 `plan`（`tx, _plan, _token` → `tx, plan, _token`）。
两个 runner 在**函数内** import：它们是兄弟模块，模块级互相 import 会成环。

### 128.2 效果（实测）

`STAGED` / `REGISTERED` / `ACTIVE_BOUND` / `EXPOSED` 四个边界现在都恢复到 `FINALIZED`：active binding 恰好
一条、`generation` 与 binding 一致（**提交点不再被走第二遍**）、`integrity_problems()` 为空、二次 `repair`
是 `no_action`。**§127 里那个"安装中途崩一次会把装好的东西解绑"的行为没有了。**

### 128.3 修完之后露出来的第二个缺陷（未修，已标）

第五条边界 `FETCHED` **仍然不行**，而且失败方式比回滚更糟：

```text
AttributeError: 'ArtifactRunner' object has no attribute '_artifact'
```

`_artifact` 是**进程内**的字段，只在第一次 `drive()` 里被赋值；从 `FETCHED` 恢复时 runner 是新建的，
`drive()` 的 `if not artifact_path.is_file()` 分支不会重新 fetch（fetch 目录里已经有了），于是后面某处
直接读 `_artifact` 就炸。**一个 `AttributeError` 逃出来当答复，正是 §62 那条教训禁止的形状**：
崩溃恢复必须给一个**裁决**（继续或按规则回滚），不是 traceback。

守卫写成一个 `xfail(strict=True)` 的测试（`test_resuming_from_fetched_does_not_crash`）：**strict 是关键**——
修好那天它会 XPASS，逼迫修的人把标记删掉，而不是让"已知缺陷"变成一个永久豁免。

### 128.4 所以 #14 现在是**一半的一半**，仍然不能打勾

* 真 artifact 事务在**提交点及之后**的恢复：修好了，与模拟 runner 同语义（这是 §127 那个缺陷）；
* 真 artifact 事务在**提交点之前**（`FETCHED`）的恢复：**仍会崩**，已用 strict xfail 钉住。

判据 #14 的字面要求覆盖前者与后者，所以定义文档 §5 的 #14 **保持 ⚠️**，并在那里写明"缺的是
`FETCHED` 的恢复"；§4 的括注也相应改写（不是删掉）。**把这一条说成完成，就是把一个 `AttributeError`
当成已验证。

### 128.5 下一轮要做的一件事

`ArtifactRunner.drive()` 在 `FETCHED` 状态下的恢复路径：要么在 fetch 目录缺文件时**重新 fetch**并把
`self._artifact` 设起来，要么从 plan/journal 里重建 `Artifact` 描述（`source.locator` + 已校验的
digest 都在，够重建）。**先让那条 `xfail(strict)` 变成正常测试，再删标记**——顺序与本节相同。

### 128.6 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1359 → 1365**（+6：新文件 `cli/tests/test_l2_recovery_drivers.py`，含五条参数化 + 一条 strict xfail） |
| 新增模块 | `cli/app/airoot/tx/runners.py` |
| golden 语料 / schema / ADR | 不变 |
| 行为变化 | `repair` 对真 artifact 事务在 `STAGED` 及之后**不再回滚**，改为完成 |
## 129. #14 收口：恢复时重建**进程内**状态（`_artifact`），16 条判据全部成立

§128 修掉了"`repair` 用错 driver"，并把 `FETCHED` 那一格的崩溃用 `xfail(strict=True)` 钉住。这一节修掉那一格。

### 129.1 缺陷的形状

`ArtifactRunner._artifact` 是**进程内**字段：只有 `drive()` 的 fetch 分支给它赋值。而 `FETCHED` 之后的每个
状态（`FETCHED`/`VERIFIED`/`STAGED`/…）都会解引用它——于是**从这些状态恢复时**，新建的 runner 上它是
**未赋值**，读它直接 `AttributeError`。上一轮只把 `FETCHED` 钉住了；这一轮顺手把 `VERIFIED` 也加进参数化，
因为它属于**同一类**（状态在 fetch 之后、stage 之前），而当时的参数化正好漏掉它。

**一个 `AttributeError` 当答复，是 §62 那条教训禁止的形状**：恢复要么继续、要么按规则回滚，不能给 traceback。

### 129.2 修法

在 `drive()` 进入状态机**之前**，若本进程还没拿到 artifact 且当前状态不是 `PROPOSED`/`APPROVED`，就从 fetch
目录**重建**那份描述（`_artifact_from_fetch_dir`：文件在就 `sha256_file` + `stat`；文件不在就报
`PAYLOAD_MISSING`，并说明"恢复靠 fetch 目录重建，而文件不在了"）。判断用
`getattr(self, "_artifact", None)`，因为新建的 runner 上根本没有这个属性。

**为什么重建是安全的**：那份文件就是 fetch 步骤写的同一个文件，而 `VERIFIED` 那一步本来就要重新校验它
（`backend.verify(..., expected_digest=plan.digest)`）——重建只说明"我读到的是哪个文件"，**决定"它是否可信"
的仍然是那次校验**，与首次运行同一条判据。文件真的丢了时，恢复给出的是 `PAYLOAD_MISSING`（一个裁决）。

### 129.3 守卫

`cli/tests/test_l2_recovery_drivers.py` 的参数化扩到六个边界（`FETCHED` / `VERIFIED` / `STAGED` /
`REGISTERED` / `ACTIVE_BOUND` / `EXPOSED`），`xfail` 标记删除。每个边界都断言：`repair` 到 `FINALIZED`、
active binding 恰好一条、`generation` 与之一致、`integrity_problems()` 为空。**顺序与 §128 相同：先看它红
（`2 failed`：`FETCHED` 与 `VERIFIED`），再修，再看绿（`7 passed`）。**

### 129.4 成本与口径

| 项目 | 结果 |
|---|---|
| 测试 | **1365 → 1366**（参数化从四条扩到六条、去掉一条 xfail） |
| 行为变化 | 恢复时 `_artifact` 由 fetch 目录重建；文件缺失时报 `PAYLOAD_MISSING` 而不是属性错误 |
| golden / schema / ADR | 不变 |
| 16 条判据 | **全部成立**：`docs/AIROOT-最小版本-v1.md` §5 的 #14 改为 ✅；§4 的括注改成"闭环由真机脚本证成、崩溃恢复由 pytest 逐边界证成" |

**口径没有放宽**：这一节没有把故障注入搬上真机、也没有把 pytest 的边界覆盖说成机器级验证——它把 #14 的
**两条证据来源**写清楚（各自覆盖什么），这比一句"全部通过"更接近事实。**"最小版本完成"这句话现在可以说了，
但只能按 §4 的写法说。**
## 130. §129 的收尾句没跟上：判据表说 16 条全部成立，同一节的散文还说 14 条

§129 收掉了最后一条判据，并更新了**三个说法里的两个**——§5 的 #14 那一行、以及 §4 的括注。第三个没有更新，
于是同一份文档在相隔几行的地方自相矛盾。这一节记的就是那第三个，以及把它变成推导关系的守卫。

### 130.1 缺陷与逐字证据

`docs/AIROOT-最小版本-v1.md` 在 **`0973e5c`** 时的 §5：表格 16 行全是 ✅，而表格**下面**那段仍逐字写着：

> **所以这一版可以说的和不可以说的，界线就在这里**：16 条里 **14 条完整、2 条一半（#11、#14）**。两条的
> 共同点是"机制在、真机闭环上还没有那一步"，而不是"没做"——所以 §4 那句话现在**不能再原样说**，
> 必须把这两条点出来（见 §4 的括注）。

三个后果，每一个都是这个模块一直在防的形状：

1. **同一份文档自相矛盾**，而且只隔几行：§4 说"16 条判据现在全部成立"，§5 的收尾句说"14 条完整"，
   还指着 §4 的括注说它"不能再原样说"。
2. **它错的方向是"少报"**：文档低报了已经交付的东西，同时让读者以为 §4 不能用。§119 是同一形状的另一半
   ——那里是裁决改了结论、而五个面还在说旧的**拒绝**。
3. **它为什么能活下来才是最该记的**：没有任何东西比较这两者。表格由人读，句子没人读。§54 把"文档里的
   计数"接到了树上，§83 把阶段的计数链接了起来，而这个句子既不是总数也不是链的一环——它是**表的读数**，
   而没有人问过"这个读数是不是从表里算出来的"。

### 130.2 规则：收尾句是**读表**，不是第二份说法

§5 的 `结束时` 一列是**测量**（一行一条判据），收尾句是对它的**读数**。所以这四件事都从表里推：

| 被推的东西 | 从哪来 |
|---|---|
| §1 的"拆成 N 条" | §2 表的行数 |
| 收尾句的成立数 / 未证成数 | §5 表里 `结束时` 含 ✅ 的行数 |
| 收尾句点名的 `#…` | **恰好**是那些不含 ✅ 的行 |
| §4 的"N 条判据（现在）全部成立" | 没有未证成的行时才允许出现；有未证成的行时，它们必须被逐个点名 |
| `AGENTS.md` 的三处提及（交接句、仓库地图、§8 的指针） | 同一张表的行数 |

外加一条口径要求：**两个说法节（§4 与 §5）都必须点名两条证据来源**（`real_machine_acceptance.py`、
`test_l2_recovery_drivers.py`）。这不是形式——`AGENTS.md` §8 要求"闭环由真机脚本证成、崩溃恢复由 pytest
逐边界证成，不要说 16 条都在真机上验过"，而那句话只有在这两个文件都被点名时才可查。

判据表的三种形状都被认：`✅`（成立）、`⚠️`（只做了一半）、别的（未做）——**只有含 ✅ 的算成立**，其余都走
"未证成"那一支，并且必须在收尾句里被点名。今天 16 行全是 ✅，走的是"全部成立"那一支；"未证成"那一支由
变异测试盯着（130.3 的第 5 条），不会因为今天用不到就变成一段没人验过的代码。

### 130.3 守卫（第三十六组）

`cli/tests/test_l0_consistency.py` 新增 `_minimum_version_problems(text)`（纯函数，返回问题列表而不是断言，
所以每个失败形状都能在**不是真文档的文本**上被测）与一个测试。五条变异，**每一条的输入都从表里推**：

| 变异 | 期望 |
|---|---|
| 把收尾句换回 `0973e5c` 的逐字原文 | 报"表里 16 条全是 ✅，而收尾句没这么说" |
| 把**第一个含 ✅ 的行**改成 ⚠️ | 报那一条的编号，且 §4 被点名 |
| 删掉一行 | 报 §5 与 §2 的判据集合不相等 |
| 把 §1 的"拆成 N 条"改动一个数 | 报 §1 的计数与表不符 |
| 一致的部分结果（一行 ⚠️ + 收尾句写对 + §4 点名它） | **不报**——正向对照，证明"未证成"那一支不是见谁都红 |
| 把 `AGENTS.md` 里那两处 `N 条逐条验收判据` 改成 N-1（改的是**副本**，不动真文件） | 报 `AGENTS.md` 的计数与表不符 |

变异的目标行**从表里取**（"第一个含 ✅ 的行"），不是写死 `#7`：§84 与 §85 各因为一个写死的字面量白丢过一轮
（§85 那个字面量在 §86 把文档改长之后**静静地不再生效**）。

### 130.4 验红

顺序与前两节相同：**先把缺陷放回真文档，看它红，再改回来。**

把 `0973e5c` 那段逐字原文还原之后，守卫报（pytest 把非 ASCII 转义了，这里解码写回）：

```text
AssertionError: every one of §5's 16 judgements is ✅ and its summary does not say so;
§5 does not name the evidence source(s) ['real_machine_acceptance.py', 'test_l2_recovery_drivers.py']
```

两条都指对了：**表与收尾句不符**，以及**那句话里没有两条证据来源的名字**。改回来之后绿。

守卫自己第一次也红过一次：`_TABLE_ROW` 少了 `re.M`，于是"表里一行都没有"——**那是守卫的缺陷，不是文档的**，
修的是守卫。

同一次还触发了两个**已经存在的**守卫，两次都指对了：

1. `test_the_audit_module_asks_vocabulary_questions_through_the_helper`：新守卫里写了 `source not in body`
   （文件名对文档做子串检查）。它要求这类检查走 `names_token` 或**声明理由**。文件名是整段字面短语，子串
   就是正确的问题，所以按它设计的方式加进 `SUBSTRING_CHECKS_ARE_FINE` 并写了理由——**不是**绕过它。
2. `test_the_audit_check_count_is_the_one_this_module_collects`：本模块多了一条测试，`AGENTS.md` 的
   "110 项"与总数 1367 因此在同一个改动里更新。

### 130.5 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1366 → 1367**（+1：守卫第三十六组；本模块的常驻审计 **109 → 110**） |
| 行为变化 | 无——这一节只改文档与守卫 |
| golden / schema | 不变 |
| ADR | **不加**：这里没有新决定、也不改语义，而是把一条已有的诚实规则（§8 的"允许怎么说"）变成可度量的。需要新 ADR 的是"结论变了"那一种，比如 §119 的 ADR-0046 |

**这一节最值得记的不是那段话写错了，而是它能写错并活着**：判据表与它的读数是两个工件，只守着其中一个
等于没守。同一个问法可以继续往别处问——`docs/AIROOT-最小版本-v1.md` 里还有哪些句子是某张表的读数？
能证明的只有被守卫盯住的那一处，其余的靠读者按同样的问题去查。
## 131. 公共仓库里的本机指纹：一句"已实测扫描过"，与它漏掉的东西

推送之后做验证时，顺手量了一次 `AGENTS.md` §9 的那句话——**"当前树里没有这些（已实测扫描过）"是假的**，
而且从这里长出了一条此前不存在的守卫。本节**不写出那些值本身**（原因见 131.3 的最后一格与 131.4），
这不是修辞：守卫扫的是**全树文本，包括这份记录**。

### 131.1 实测

新守卫（第三十七组）第一次运行，报出 **13 处**命中、分布在 **7 个**被提交文件里：

| 命中的是什么 | 落在哪里 |
|---|---|
| `D:` 卷序列号 | 三处：`cli/tests/fixtures/golden/registry_with_data_root.json`、`cli/tests/golden.py`、草案 §116 的实测表 |
| 本机账户名（profile 目录长名） | 草案（§118 的 rustup 输出、§126 的异常路径） |
| profile 目录的 8.3 短名（带路径） | 草案（§111/§126）、ADR-0037 |
| 8.3 短名本身（不带路径） | `cli/app/airoot/paths.py`、`cli/app/airoot/cli.py`、`cli/tests/test_cli_steward.py`、`cli/tests/test_cli_search.py`、草案、ADR-0037 |

引入提交是 `79e8147`（`git merge-base --is-ancestor 79e8147 3a77370` 实测为真），也就是它**在这次推送之前
就已经在公共远端上**。所以这一轮既没有制造它、也没有把它从历史里拿掉——只把它从**当前树**里拿掉了。

### 131.2 为什么它值一节

其中一处落在 **golden 语料**上，而那是 Rust 迁移的**逐字节验收面**。把本机事实抄进验收面，等于让
"语料可复现"这句保证里混进一个**只在这台机器上成立**的值。语料里其他身份值全是合成的
（`S-1-5-21-1000`、`C:\Users\me`），所以这一个不是设计，是抄进来的——而 §9 那句"已实测扫描过"让它看起来
像被检查过。**一句没人执行过的检查，比没有这句话更糟**：它让下一个人不再去量。

### 131.3 守卫：指纹必须**从 OS 现读**，不能是一张名单

手写一张"禁止出现的值"清单，只能禁住**当时有人记得写下来**的值；而这次泄漏的值，在写那句"已经扫过"的
时候就已经在树里了。所以第三十七组每次运行都从 OS 现读四样东西：

| 指纹 | 从哪读 |
|---|---|
| 每个固定盘的卷序列号（两种拼法：`xxxxxxxx` 与 `XXXX-XXXX`） | `paths.volume_serial`——**就是 root 身份守卫用的那个读数**，不是第二份实现 |
| profile 目录的长名 | `Path.home()` |
| 它的 8.3 短名 | `GetShortPathNameW`；盘上没启用 8.3 时它返回长名，于是两个条目自然合并（**这是机器的性质，不是守卫失败**） |
| 用户 SID | `caps/identity.probe_identity()`，与 §113/§114 同一个读数 |

然后搜全树文本——复用 §84 那份**剪枝**遍历 `_walked_text_files`，不新写一个走法。三条非空性：读到的
指纹少于 2 条、遍历到的文本文件不超过 50 个、或在**合成输入**上种一个指纹而匹配器不命中——各自都报
"这条守卫没有内容"。最后一条尤其必要：**干净树是常态，一个永不命中的匹配器与一棵干净的树长得一模一样。**

**只在 8.3 真的缩短了它时才搜那个短名**：本机账户名是 `Administrators` 的子串，而后者在这个仓库里被
正确地频繁使用（`BUILTIN\Administrators`、`IsInRole`），搜它会把这条守卫变成拼写检查。

### 131.4 处置

| 位置 | 处置 |
|---|---|
| golden 语料与生成器 | 卷序列号换成合成的 `deadbeef`，重生语料（`git diff` 只有一行，就是 `volume_serial` 那一列） |
| 草案 §116 的实测表 | **抹掉真值、保留结论**（结论是"与 `D:\env` 同一个卷，所以 store 与数据根同卷"），并写明真值属本机指纹 |
| 代码/测试/ADR/草案里的本机拼写 | 换成合成拼写：短名 `PROFIL~1` ↔ 长名 `Profile Name`（8.3 关系仍然成立），rustup 输出里用 `ProfileName`（无空格，与真实输出同形） |
| 受保护签发方草案 §2.4 的 ACL 输出 | 账户名按该文档**已有的省略风格**写成 `<user>`（它的机器名原本就写作 `PC-…`） |

**改的是"值"，不是"结论"**：§9 是出版物规则，它压在历史记录之上——被换掉的只有本机拼写，
**结论与形状一字未动**（"长名/短名不一致会让 `resolve()` 把合法路径报成越界"这个坑照旧是那一节的教训）。

### 131.5 验红

顺序照旧，但这一次**缺陷本来就在**：守卫先写、用它当工作清单，第一次运行就是红的，报出 131.1 那 13 处。
修完拼写后只剩 1 处（语料里的卷序列号），重生语料后绿。

### 131.6 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1367 → 1368**（+1：守卫第三十七组；本模块常驻审计 **110 → 111**） |
| golden 语料 | `registry_with_data_root.json` 一行（`volume_serial`） |
| 行为变化 | 无产品行为变化；验收面不再携带本机事实 |
| ADR | **不加**：§9 早就写着这条禁令，缺的是**量它的东西**，这里没有新决定 |
| 公共历史 | **未重写**：真值仍在公共仓库的历史提交里（`79e8147` 及其后代）。要清历史得 `filter-repo` + force-push，那是**另一个决定**，也是本项目至今唯一一处"已知仍然公开、且已如实记下"的东西 |

**这一节的形状与 §115 的 `ACL_MISMATCH` 同源**：一个说法被别人借去表达它不描述的东西，而"检查"从来
没有真的跑过。差别是这次借的是**事实本身**——文档借了一句"已扫描过"，而那个扫描器不存在。
## 132. 清历史：把指纹从**公共历史**里重写掉（承 §131）

§131 把真值从**当前树**里拿掉了，并在成本表里如实写着「公共历史 | **未重写**」。这一节做那一件。
执行与代价都记在这里。

### 132.1 怎么做的（以及为什么不是 `filter-repo`）

实测本机**没有** `git filter-repo`（`git filter-repo --version` → `git: 'filter-repo' is not a git command`），
而为一次重写往这台机器的 Python 环境里装一个包，正是 §131 要避免的那类副作用。所以用内置的
`git filter-branch --tree-filter`：**91 个提交、24 秒**，动到内容的那些提交各改 **9 个文件**。

过滤脚本是**字节级**的（`.git/rewrite-fingerprints.py`，不随仓库发布）：读字节、替换字节、写字节。
不用文本模式是因为**行尾本身是契约**（`.gitattributes` 是 `* -text`），一次"解码再写回"会顺手改掉它。
二进制文件按同一个判据跳过（UTF-8 解不开）。替换表里每一对都与 HEAD 已经承载的合成值**相同**，
所以重写后的 HEAD 必须与重写前的 HEAD **逐字节相同**——这就是验收判据。

### 132.2 实测

| 检查 | 结果 |
|---|---|
| 重写前后 HEAD 的 **tree hash** | 都是 `360782f6c9846018c841c2e02a3a9619c870f824`——**内容一字未变** |
| 提交数 | 91 → 91（没丢历史、没折叠） |
| `git log -S <真值> main` | 五个串（卷序列号两种拼法、8.3 短名、长名、ACL 输出里的账户名）**各 0 个提交** |
| 旧 tip → 新 tip | `9c40f3a` → **`adfd54e0`** |
| 备份 | `.git/airroot-pre-rewrite.bundle`（2 341 026 字节；`git bundle verify` 报 **complete**）+ 标签 `backup/pre-fingerprint-rewrite` + filter-branch 自建的 `refs/original/refs/heads/main` |

### 132.3 代价：**每一个被重写的提交都换了 id**

从这一版第一个提交起，每个提交 id 都变了——而文档**引用**它们。实测：重写后有 **14 个被引用的 id
不再能从 `main` 到达**，分布在 **25 行、26 个 token** 上（草案 25 个 + 最小版本 1 个）。全部按旧→新重指，
重指后 **0 个悬空**。下表既是这次重写的**映射**，也是"重写历史会让引用失效"这句话的实测：

| 旧 | 新 | token 数 |
|---|---|---|
| `40f4937` | `79e8147` | 7（其中一行引用两次） |
| `607cfd9` | `0973e5c` | 3 |
| `16cffdf` | `baa02ca` | 3 |
| `c838fe4` | `63284b0` | 3 |
| `cf6fba3` | `ced8e6c` | 1 |
| `ed12221` | `4a75590` | 1 |
| `7219b4d` | `862dc26` | 1 |
| `9bfc33d` | `7c437e4` | 1 |
| `7909192` | `37411f0` | 1 |
| `4598e6f` | `4add5c2` | 1 |
| `413c2b9` | `868da5d` | 1 |
| `bb560f8` | `fe066e5` | 1 |
| `e26221d` | `085bdb0` | 1 |
| `8d9e98c` | `3a77370` | 1 |

**判据是"能不能从 `main` 到达"，不是"对象存不存在"。** 重写后旧 id 仍然 `git cat-file -e` 得到 0
（备份标签让它们可达），所以按"存在"判会得到"引用全部正常"的**假绿**——实测：按存在判，14 个悬空
一个都报不出来。同一个问题在两种口径下答案相反，而错的那个恰好是更容易写的那一个。

### 132.4 这一节**没有**做到的（不要把它说成"清干净了"）

- **GitHub 一侧**：force-push 只改 `main` 指向的历史。服务端的不可达对象仍会存在一段时间，旧的
  commit URL 也可能被缓存视图继续应答；要立刻清干净只能联系 GitHub 支持。**本节不作这个承诺。**
- **已经克隆过的人**：他们手上的历史原样保留——这是 Git 的性质，不是"没做"。
- **本机备份**：上面那个 bundle 与标签**故意**留着真值，等操作者确认后再删。

所以准确说法是：**真值已从发布出去的历史里移除**，不是"世界上不存在了"。

### 132.5 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368**：本节动的是 git 历史，而测试只读工作树。**不加守卫**——一条需要 `.git` 的守卫会让从 tarball/导出目录里跑测试的人直接跑不了，而 golden 语料与文档都必须能在那种形态下用 |
| 文件内容 | **零变化**（tree hash 相同）；变的只有提交 id 与引用它们的 26 个 token |
| golden / schema | 不变 |
| ADR | **不加**：§131 里那条取舍（宁可公告也不假装）在这里被操作者的决定覆盖，不是新的设计决定 |
## 133. 真机测试的第一步：把"不碰我的环境"变成每次运行都量一遍的契约

最小版本收口之后，真机测试从"能跑一次"进入"可以经常跑"。而经常跑的第一条前提不是覆盖更多命令，是
**它对这台机器必须完全无副作用**——否则每跑一次都是在赌。本节把那条前提从模块开头的一句话变成**测出来的**。

### 133.1 覆盖什么，怎么比

`cli/tests/real_machine_acceptance.py` 现在用两次快照夹住整段运行（`host_state()` 在前、
`isolation_report()` 在后），任何一处动了就是失败：

| 面 | 怎么比 | 强度 |
|---|---|---|
| 机器环境块（`HKLM\…\Session Manager\Environment`） | `winreg` **只读**枚举，逐值相等 | 精确 |
| 用户环境块（`HKCU\Environment`） | 同上 | 精确 |
| `D:\env\.airoot`、`~\.cargo`、`~\.rustup` | 逐文件 `(size, mtime_ns)` 清单 | 精确 |
| `D:\env` 本身 | `(文件数, 字节数, 最新 mtime)` | **汇总** |

最后一行的强度是**故意**的，也写在代码里：19 万个文件的逐文件清单存两份，是为了一句"没有文件被写"付
内存；汇总能抓住新增、删除、大小变化、以及任何把最新 mtime 往前推的写入，**抓不住"改了同样大小又把
时间戳改回去"的编辑**——那件事没有任何检查抓得住。

**没被覆盖的三个面也写下来**，免得它们的缺席看起来像疏漏：进程环境（子进程改不了父进程的）、PATH
（这个脚本从不写它）、提权（整轮都没有要求过）。

### 133.2 非空性：**这一次运行本来就应该什么都没找到**

干净的结果与"永远不会报的检查"长得一模一样。所以每次运行都额外在**本机自己的快照**上做三次合成变异
——种一个文件、改一个环境值、把数据根汇总往前推——三次都必须被报出来，否则这一项自己失败。它借用当次
真实快照，不额外走一遍盘。

### 133.3 实测（真机，本轮）

```text
step 5-6 + 8-9 + 31-34 real-machine acceptance: PASS (0 failed check(s))
closed loop (minimum version, scratch root, no network)
closed loop: PASS (0 failed check(s))
isolation audit (nothing outside a scratch root may change)
  compared: 42 environment value(s), 181 file(s) in 3 watched tree(s), and a 191750-file summary of D:\env
  [ok ] no tracked surface moved, and the comparison reports a planted change
isolation: PASS (0 difference(s))
```

**这一行是本节最想留下的东西**：它同时说了**比了什么**（42 个环境值、181 个文件、191 750 个文件的汇总）
和**结果是什么**（0 处不同），所以 PASS 不是空集对空集的比较。同轮的另外两个读数也记在这里，因为它们
正是"不碰环境"在**产品行为**上的对应物：

- `data_root_files_touched: 0`——`search refresh` 在真实 `D:\env` 上建了 **51 183** 条记录的索引
  （`coverage=complete_for_roots`），而数据根里**没有任何一个文件被碰过**（索引写在临时 root 的
  `cache\search` 下）；
- `discover` 在真实 `D:\env` 上给出 `excluded=9 / external_reference=3 / unmanaged=5`（`wl-5`），
  `adopt --mode reference` 的 `files_touched` 同样是 0。

`--online`（对真实上游下载 + 摘要校验）本轮**未运行**，脚本如实报 `not run (pass --online)`。

### 133.4 覆盖范围没变，变的是它现在**证明**自己不碰环境

本节**没有**新增任何对真机的写操作，也没有把任何被排除的动作打开：machine 级环境变量、machine PATH、
ACL 写入、受保护 broker 仍然全部不在真机路径上（§8 的排除清单未变）。加的是一个**判据**，不是一个
动作——而它正是"可以开始经常跑真机测试"这句话的前提。

### 133.5 顺带修掉的一处守卫缺陷（它由本节自己发现）

加完本节之后，**阶段计数链的守卫红了**——而且三次都红在**它自己的变异**上，不在链上。三次都值得记，
因为每一次的修法都被下一次证伪了一次：

1. **红在"文本不唯一"**：变异用"这一行计数在全文里出现几次"来定位最后那个阶段的行，而 §132 与 §133
   都是**没有改变测试数**的阶段，所以同一行文本出现了两次：

   ```text
   E   AssertionError: §133's own count row is not in the shape this mutation needs
   E   assert 2 == 1
   ```

2. **改成"在该阶段小节里找"之后，又红了一次**：本节为了记录第 1 条，**引用了那行文本**（就在上面那个
   fenced 块里），于是小节里有两处"像行"的东西，而被改写的是**引用那一处**——链没动，变异自然报不出
   东西（`a chain that ends below the real total must be reported`）。
3. **最终修法有两半，都不是绕开断言**：
   - 解析器**把它选中的那一行的行号一起返回**（并列时取最后一个，因为"本阶段自己的行"在它的成本表里，
     总在引用之后），变异直接改写**那一行**；
   - 扫描阶段小节时**先把 fenced 代码块遮掉**（逐字符换成空格，**不动任何偏移与行号**），因为引用
     一段输出与陈述一个总数，对正则来说没有区别——只有"在小节之外"这一点能区分。若某小节在 fenced
     之外什么也没有，则回退到不过滤，**免得把那个阶段从链里悄悄删掉**（那比报错更糟）。

**这三条连起来是一句话**：行的**文本**不唯一，行的**位置**唯一；而引用的位置又会被误当陈述，所以还要
先分清"哪块是引用"。§84 与 §85 是同一条线上的前两段（写死的字面量会自己改指、推导里还留着自己算的数），
这是第三段。**修完实测**：链仍为 112 个带计数的阶段，§130 `1366 → 1367`、§131 `1367 → 1368`、
§132 与 §133 `1368 → 1368`，全绿。

### 133.6 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368**：契约写在真机脚本里，不进 pytest。理由与 §132.5 同源——套件必须能在没有真机、没有 `winreg`、没有 `D:\env` 的地方跑完；契约的**非空性**因此由脚本每次运行时自己做（§133.2），不靠套件 |
| 真机脚本 | +1 段（快照 / 比较 / 自检 / 打印）；两次额外盘遍历，实测约 9 秒/次 |
| 行为 | 无产品行为变化 |
## 134. 真机测试第二步：唯一会碰网络的那条路径也进契约，并量"跑完自己收干净"

§133 把"不碰宿主机"变成两次快照之间的判据。这一节做两件事：**唯一会碰网络的那一步**也走同一份契约，
以及把"它跑完会自己清理"从模块开头的一句话变成一次点名——点名的结果当场查出了两条真缺陷。

### 134.1 `--online`：真实上游、真机、仍然只读

`--online` 是本脚本唯一会碰网络的一步（也是这一版对真实上游取 artifact 的那条路）。本轮实测：

| 读数 | 值 |
|---|---|
| 解析出的 artifact URL | `static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe` |
| 期望摘要的**来源** | 上游发布的校验和文件（`offline=false`，`sha256:6f4bef66…`） |
| 取回字节数 | **12 721 664**（与 §117 那次真机安装一致） |
| 摘要校验 | `verified=true` |
| 边界 | **止于 fetch + verify**：stage/commit 要批准，而签发批准是显式本机步骤（ADR-0046）——脚本**报告**这条边界，不代替操作者签 |

它把文件写进**临时 scratch root**，所以隔离审计同时覆盖了这一步：整轮仍是 **0 处不同**（§134.3）。
"访问网络"与"改宿主机"是两件事，这一节量的是后者没有发生。

### 134.2 "自清理"被点名之后：两条真缺陷

`host_state()` 增加了临时目录那一格，`isolation_problems()` 只把**本次新增**的算作问题（早先崩掉的一次
运行留下的不算——一条会因为历史而变红的检查会被学会忽略），并加了一条合成变异（种一个
`airoot-planted-*`）证明这一格真的会报。第一次跑就查出：

**缺陷 1：这个脚本自己有一条漏的路。** `ROOT` 曾经在**导入时**就 `mkdtemp`，于是任何没走到 `main_run`
清理的出口——"这里没有数据根"的拒绝、跑到一半抛异常——都会留下一个空目录。实测：临时目录里有 3 个
`airoot-acceptance-*` 就是它留下的。**修法**：`ROOT` 改成 `main_run` 里惰性创建，`__main__` 用
`try/finally` 兜住两个半边。**验红**：修之前 `python -c "import real_machine_acceptance"` 会创建目录；
修之后实测 `ROOT after import: None`，且临时目录里 `airoot-*` **目录数 42 → 42**。

**缺陷 2：新加的那一格自己在量错东西。** 第一版按"前缀匹配的所有条目"计数，报 **48**；而目录只有
**42**。多出来的 6 个是**文件**——本次会话重定向的日志，以及更早一次会话留下的 `airoot-rust-*.json`
产物——它们与前缀相撞但不是 scratch root。**修法**：只数**目录**。一个会被日志文件名推动的检查，量的
不是它声称的东西。

**顺带量到的、没有动的东西**：那 42 个目录共 **7 030 657 字节**，按前缀是 `golden` 21、`fault` 4、
`acceptance`/`readprobe` 各 3、`fake`/`r4`/`r5a` 各 2、`art`/`doctor88`/`diff`/`ed25519`/`probe` 各 1。
`golden`/`fake`/`readprobe`/`r4`/`r5a` 这些前缀来自**更早会话里的一次性开发脚本**，不是这个脚本。
**只报告，不删**：那是操作者的磁盘，而且"AIROOT 自己删自己以前留下的东西"正是本项目一直守的那条边界，
要有明确指示才做。

### 134.3 实测（真机，本轮，`--online`）

```text
source resolve (online, real upstream)  exit=0 {artifact_url=…static.rust-lang.org/…
                                                expected_digest=sha256:6f4bef66…, offline=false}
    fetched 12721664 bytes -> rustup-init.exe digest=sha256:6f4bef66261261fc
https_artifact fetch + verify           exit=0 {"verified": true, "size": 12721664}
    boundary: stage/commit need an approval token, and signing one is an explicit local step
              (airoot.tx.issuer, ADR-0046) - not taken here, reported not faked
step 5-6 + 8-9 + 31-34 real-machine acceptance: PASS (0 failed check(s))
closed loop: PASS (0 failed check(s))
isolation audit (nothing outside a scratch root may change)
  compared: 42 environment value(s), 181 file(s) in 3 watched tree(s), a 191750-file summary of
            D:\env, and 42 scratch entr(y/ies) under the system temp directory
  [note] 42 scratch entr(y/ies) were already there, e.g. airoot-acceptance-15_v1cr6; not created by
         this pass, and not removed by it either
  [ok ] no tracked surface moved, and the comparison reports a planted change
isolation: PASS (0 difference(s))        exit code 0
```

跑完之后临时目录里的 `airoot-*` **目录**仍然是 **42**——不是 43 也不是 41：这一次运行什么都没留下，
也什么都没删。（`[note]` 里举的例子是**按名字排第一**的那个，不是最老的那个——打印语句只说 "e.g."，
而"最老"这个说法在原始输出里没有任何依据。）

### 134.4 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368**（契约与自清理都在真机脚本里，不进 pytest，理由同 §133.6） |
| 真机脚本 | +1 格快照（临时目录**目录**条目）+1 条合成变异；`ROOT` 惰性创建；`__main__` 的 `try/finally` |
| 行为 | 无产品行为变化；`--online` 仍是 opt-in，且现在有"它也不碰宿主机"的实测 |
## 135. 真机测试第三步：把"我没想到的面"纳入快照，并把 judgement 16 拆成"能测的"与"不能测的"

§133/§134 让"不碰宿主机"与"自己收干净"变成判据。这一节补两处**结构性**缺口：一处是审计**自己的视野**，
一处是"姿态"这个词究竟由哪几句可测的话支撑。

### 135.1 审计的视野：名单 vs 面（§131 那张课，换一层再上一遍）

原来的观看面是一张**名单**：`.airoot`、`.cargo`、`.rustup`、`D:\env`、两个环境块、临时目录。名单的问题
§131 已经量过——**它只覆盖有人想到的地方**：一个把文件丢进用户主目录、或丢进这个 checkout 的动词，
整套检查会一声不响。

现在多了**两处"面"**：主目录与仓库根的**顶层条目列表**。比的是**名字**，不比 mtime——比 mtime 会让任何
别的进程在主目录里建文件都变成红，而这条检查要问的是"多了一个/少了一个条目"。它**抓不住"改了里面一个
文件"**，这一点写在代码里：那是清单式检查的诚实边界。

**非空性**因此多了一条合成变异（往被观看的列表里加一个条目），每次运行都验；本轮运行的五条变异全部
被报出，于是主比较的"0 处不同"才有意义。

### 135.2 judgement 16 的四句话：三句可测，一句不可测——而不可测的那句要说出来

| 那半句 | 怎么量 | 状态 |
|---|---|---|
| `policy_only` / `same_user_can_bypass` | `root status` 的**根自己那份文档** | **本轮新加断言** |
| 不写 machine PATH | `path verify` 的 `path_written=false`（已有）+ 审计比对 HKLM 环境块 | 已覆盖 |
| 需要提权的写入被拒 | `env persist --scope machine` → `PRIVILEGE_REQUIRED`(5)（已有） | 已覆盖 |
| **整轮不提权** | —— | **不测，并写明为什么不测** |

最后一行是本节最值得记的：本会话的 token 实测**是提权的**（`integrity=high`、SID `…-500`），所以
"这个进程没有提权"这句断言会在这台机器上**因为操作者用哪个 shell 而变红**——那量的不是产品。改成本地
**打印** token 事实，并把可测的那一句留给"在任何 token 下都该成立"的那条：**需要提权的那次写入，
在提权的 token 下也必须被拒**——而唯一能证明它的地方恰好就是这里。

**能测的测、不能测的说清为什么不能测**，比一句"全程不提权 ✅"更接近事实。

### 135.3 顺带补上的一条读路径

`inventory --class external_reference` 此前没被真机路径走过（`tool list` 走的是 owned 一侧）。现在断言：
**恰好一条**登记在案的 reference，且数据根与它并列。仍然只读。

### 135.4 实测（真机，本轮）

```text
    token: integrity=high elevated=True sid=S-1-5-21-…-500
root status                             exit=0 {"security_mode": "policy_only",
                                                "enforcement": "same_user_can_bypass",
                                                "registry_generation": 0}
inventory --class external_reference     exit=0
step 5-6 + 8-9 + 31-34 real-machine acceptance: PASS (0 failed check(s))
closed loop: PASS (0 failed check(s))
isolation audit (nothing outside a scratch root may change)
  compared: 42 environment value(s), 181 file(s) in 3 watched tree(s), a 191750-file summary of
            D:\env, 99 top-level entr(y/ies) in 2 watched listing(s), and 42 scratch entr(y/ies)
            under the system temp directory
  [note] 42 scratch entr(y/ies) were already there, e.g. airoot-acceptance-15_v1cr6; not created by
         this pass, and not removed by it either
  [ok ] no tracked surface moved, and the comparison reports a planted change
isolation: PASS (0 difference(s))       exit code 0
```

`99` 是那两处列表的条目总数（主目录 + 仓库根顶层），**它随机器变化**——这一行要记的是"它比了什么"，
不是"它必须是几"。跑完临时目录仍是 42 个目录：没多、没少。

### 135.5 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368**（契约与断言都在真机脚本里，不进 pytest，理由同 §133.6） |
| 真机脚本 | +1 格快照（两处顶层列表）+1 条合成变异 +4 条断言（姿态 2、清单 2）+ token 事实一行打印 |
| 行为 | 无产品行为变化 |
## 136. 试过"降权再跑一次"：`runas /trustlevel` 没有降下来——而这次尝试本身量到了东西

§135.2 把"整轮不提权"标成**不测**，理由是"那会量到操作者的 shell"。那一节之后又试了一次：用 Windows
自带的 `runas /trustlevel:0x20000`（SAFER 受限令牌，不需要密码）把整段真机验收**再跑一遍**，看能不能
真的拿到一个非提权 token。

### 136.1 实测：没有降下来

```text
    token: integrity=high elevated=True sid=S-1-5-21-…-500      ← 受限令牌下仍然如此
```

`runas /trustlevel` 走的是 `CreateRestrictedToken`（禁用最大权限 + 受限 SID 列表），但它**不降低完整性
级别**：子进程从提权会话继承的仍是 `high`。所以"这个进程没有提权"这句断言**仍然不能在这台机器上做**；
这次尝试真正的价值是另外两件：

1. **它把"不测"从一句判断变成了一个实测过的判断**——不是"大概没有手段"，而是**试过一个具体机制并拿到
   了具体读数**，读数说这个机制不改完整性级别。§135.2 那句话因此可以读得更精确。
2. **它在另一个令牌下把整段验收又跑了一遍**：`step 5-6 + 8-9 + 31-34 … PASS (0 failed check(s))`、
   `closed loop: PASS (0 failed check(s))`、`isolation: PASS (0 difference(s))`、退出码 0；而且
   `env persist --scope machine` 仍然报 `PRIVILEGE_REQUIRED`(5)。**这个拒绝与令牌无关**——§135.2 表里
   那句"在任何 token 下都该成立"由此多了一个令牌的支撑，而不是多了一句断言。

### 136.2 要做到真正的"非提权运行"，还缺什么

- **从非提权 shell 跑一次**：最直接，命令在 §136.3，由操作者执行；
- **计划任务 + `schtasks /create /rl limited`**：能拿到真正的 limited token，但它要往任务计划库里写东西
  ——**那是改宿主机状态**，本项目不在测试里做这类事，除非操作者明确要求。

两条都**没有**在本次尝试里使用。**别把"试过 `runas /trustlevel`"说成"试过所有办法"**——本节记的是
一次具体尝试与它的具体结果。

### 136.3 操作者可以怎么验（一行）

在**非提权**的 shell 里跑：

```powershell
python cli\tests\real_machine_acceptance.py
```

输出里那行 `token:` 会显示 `elevated=False`——那一刻，"整轮不提权"才第一次有真机证据。**这一节把命令
留在这里，而不是假装已经验过。**

### 136.4 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368**（本节只多了一次"换一个令牌跑同一份脚本"的实测，脚本本身未改） |
| 真机脚本 | 无改动 |
| 行为 | 无 |
## 137. 先跑验收，再把旧东西删干净——以及"非提权运行"那条路的四个实测

### 137.1 先跑验收

真机全轮，删旧之前：`step 5-6 + 8-9 + 31-34 … PASS (0 failed check(s))`、`closed loop: PASS (0 failed
check(s))`、`isolation: PASS (0 difference(s))`，退出码 0；compared 行是 42 个环境值 / 181 个文件 /
191 750 文件汇总 / 99 条顶层条目 / **42 个** scratch 条目。

### 137.2 删旧的：临时目录 53 个条目 → 0

| 类别 | 数量 | 字节 |
|---|---|---|
| `airoot-*` **目录**（更早会话的一次性开发脚本留下的） | 42 | 7 030 657 |
| `airoot-*` **文件**（本次会话重定向的日志 + 更早的 `airoot-rust-*.json`） | 11 | 96 531 |

删完再数：**0**。然后**重跑验收**——`compared` 行的 scratch 那一段变成 **0**，而 `[note]` 那行**整行消失**
（它只在"早先就存在"时打印）。**note 的消失本身就是"确实清干净了"的证据**，而且它**从不影响 PASS**：
note 一直只报告、不判失败，这条设计现在被反向用了一次。

### 137.3 本机那份含真值的备份：删掉，并且**真的丢弃对象**

§132.4 写着"那个 bundle 与标签**故意**留着真值，等操作者确认后再删"。操作者确认了。于是：删标签
`backup/pre-fingerprint-rewrite`、删 filter-branch 自建的 `refs/original/refs/heads/main`、
`git reflog expire --expire=now --all`、`git gc --prune=now`，并删掉当时的一次性脚本——其中
`rewrite-fingerprints.py` **含真值替换表**，所以它必须一起走。

| 检查 | 结果 |
|---|---|
| 重写前的旧 tip（`9c40f3a…`）还在对象库里吗 | **不在了**（`git cat-file -e` 失败） |
| HEAD / tree | `4cda8d78…` / `b43628bb…`——与 gc 之前**逐字节相同** |
| 五个真值串在可达历史里 | 各 **0 个提交** |
| `git fsck --unreachable --no-reflogs` | **0 个不可达对象**——旧链在本机**彻底没了** |
| 引用 | 只剩 `main` + `origin/main`（同一个 commit） |
| `git count-objects -vH` | 0 个松散对象，pack **2.37 MiB** |
| 测试 | **1368 项**，全绿 |

**这一节做到的是"本机也没有了"，不是"世界上不存在了"**：GitHub 服务端的不可达对象与已克隆的副本
仍然不在本节能承诺的范围内（§132.4 那句依然成立）。

### 137.4 顺带：把"非提权运行"这条路的四个机制都试了

§136 试过 `runas /trustlevel:0x20000`（受限令牌，完整性级别不变）。这一轮把剩下的也试了，**四组读数**：

| 机制 | 实测 |
|---|---|
| `runas /trustlevel:0x20000` | 子进程仍 `integrity=high elevated=True`（`CreateRestrictedToken` 不降 IL） |
| `explorer.exe` 转交（经典的"借中等完整性 shell"） | 仍然 `high elevated=True`——用**项目自己的** `probe_process` 量到 **explorer.exe 本身就是 high**（pid 14044 / 212752） |
| 自己造过滤令牌（deny `Administrators` + IL 设 medium） | 进程能起来，子进程在 **DLL 初始化**时死掉：`0xC0000142`；补上显式环境块与 `winsta0\default` 桌面后**仍然一样** |
| （对照）winlogon | `integrity=system`（如常） |

**结论不是"我做不到"，而是一条关于这台机器的事实**：**它的整个交互会话都是提权的**（内建 Administrator
账户、没有 UAC 过滤令牌），所以从会话内部**借不到**一个中等完整性的上下文。于是"整轮不提权"这句断言
仍然只能由操作者在**非提权 shell** 里跑一次来证成（§136.3 那一行命令），或者在一台有正常过滤令牌的
机器上跑。**四个机制都试过、都记下读数**，比一句"测不了"有用。

### 137.5 成本

| 项目 | 结果 |
|---|---|
| 测试 | **1368 → 1368** |
| 真机脚本 | 无改动 |
| 本机磁盘 | 临时目录少 **53** 个条目（≈7.13 MB）；`.git` 现在 0 个松散对象、pack 2.37 MiB |
| 行为 | 无产品行为变化 |
