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
| S23.1 | `policy/sources.json`（`src-1`）：4 个允许 host、3 个来源条目（`rust-toolchain` / `build` / `archive`），**不含任何 digest** | ✅ |
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
| S51.5 测试 + 回写 | ✅ | `pytest cli/tests` 739 → **742 项**；审计 62 → **65 项** |

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
| S59.5 目录如实记录 | ✅ | `policy/sources.json` 的 `notes` 现在**逐条写明验证状态**：两个"verified online（并写明**哪些**步骤做过、哪些没做）"，`archive` 仍标注"未验证" |
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
| S60.7 测试 + 回写 | ✅ | `pytest cli/tests` 758 → **759 项**（审计 72 → **73**）；切片与真机验收两种模式均绿 |

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












