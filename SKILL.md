---
name: airoot
description: Local capability control plane for Windows agents. Use when the user asks where a capability is (python, java, node, ffmpeg, 7z, git, cmake), asks to install or remove one, asks what is already on this machine, or asks to make a capability usable in this session. AIROOT records and verifies; it does not take ownership of software the user already has.
---

# AIROOT — Skill 适配层

**你是这个 Skill 的解释器，不是它的实现。** 所有状态变化都必须经过 `airoot` CLI；你的工作是
把用户的一句自然语言变成一条确定性命令，再把 JSON envelope 如实解释回去。

规范层级：`SKILL.md` 只讲"怎么问、怎么解释、什么时候停"。具体字段与规则在
`cli/schema/*.schema.json`（机器可执行契约）与 `references/`（按需参考）。
**不要把可变运行状态写进本文件**（root 路径、版本号、registry 内容都一样不进）。

## 第一步永远是确认状态

任何操作之前先跑这两条，再决定后面做什么：

```bash
airoot root status --json     # root 身份是否可证明
airoot doctor --json          # D1-D10 不变量、数据根、reference 观测漂移
```

- `status=healthy` → 继续。
- `status=degraded` → 可以继续，但必须把降级原因说出来（`doctor` 的 `diagnostics[].impact`）。
- `status=broken` / `recovery_required` → **停下来报告**，先跑 `airoot repair --json`，
  不要"顺手修一下"（`doctor` 永不自动修复，`repair` 才是那个动词）。
- `security_mode=policy_only` → **照实说**：P1 的强制手段是约定与审计，不是 ACL；
  同一用户权限下的进程可以绕过它。绝不能说成"已受保护"。

## 用户想要一个能力时怎么走

| 用户的话 | 你要跑的命令 | 你不该做的事 |
|---|---|---|
| "这台电脑上有没有 X / 在哪" | `airoot where X --json` | 不用 `where` 之外的命令去猜；不递归 `shell` 搜索 |
| "有没有 X 且版本满足 …" | `airoot where X --version ">=1.2" --json` | 不替用户放宽版本约束；版本未知就是不满足 |
| "这台机器上都有什么" | `airoot inventory --class … --json` | 不把 `unmanaged` 说成"AIROOT 管的" |
| "帮我装 X / 准备环境" | `airoot plan X --scope … --target … --dry-run --json` 然后按需要批准（这个 build 签不出 token：见《批准》） | 不直接 `install`；不在未确认时落盘计划 |
| "这个 X 是从哪来的 / 凭什么信它" | `airoot source list --json`，再 `airoot source resolve X --version … --json` | **不编造 digest**；校验和来自上游发布的文件，不是你自己算的 |
| "这东西能不能交给 AIROOT 管" | `airoot capability check <path> --json` | 不为了让对象"能被管"而放宽判据 |
| "把这个目录里的东西登记一下" | `airoot discover --json`（只读）→ `airoot adopt <path> --mode reference --json` | 不 `adopt` 数据根之外的路径；数据根内不删任何文件 |
| "让 X 在这个会话/项目里可用" | `airoot env activate <external-id> --session <id> --shell powershell` 或 `airoot exec <external-id> -- <cmd>`（`exec --env <external-id> -- <cmd>` 同义） | 不声称能改父 shell（物理上做不到） |
| "这个会话里先别用 X 了" | `airoot env deactivate --session <id>`（或 `--all`） | 手工删变量；`deactivate` 是**恢复旧值**，不是删除 |
| "把它设成永久可用" | `airoot env persist <external-id> --dry-run --json`，再要 approval token（这个 build 签不出 token：见《批准》） | **没有 token 就不要写**；不发明 `--force` |
| "撤掉 / 不要再让它默认生效" | `airoot env forget <external-id> --dry-run --json` 然后执行 | 不手工删注册表值 |
| "把它卸掉" | 先 `airoot tool retire <id> --json`，再 `airoot tool gc --plan --json` | **对 reference 一律拒绝**：那不是 AIROOT 的东西 |
| "AIROOT 现在管着哪些东西 / 这个还好吗" | `airoot tool list --json`、`airoot tool status <id> --json`、`airoot tool verify <id> --json` | 不把 `retired` 说成错误；`verify` **不会**修复任何东西 |
| "以后一直用这个版本" | `airoot tool pin <cap> --version "<约束>" --json` | 不以为 pin 会立刻生效：它只写 desired 并给出计划，应用仍需批准（这个 build 签不出 token：见《批准》） |
| "PATH 有没有被弄乱" | `airoot path verify --json` | 不手工改 PATH（写 PATH 属 P2）；`info` 级发现不是问题 |
| "某个文件在哪 / 它叫什么名字" | `airoot search <query> --json` | **`search` 不是 `where`**：前者定位文件，后者解析能力。要按名搜一个叫 `status` 的文件用 `airoot search --query status --json` |
| "搜得太慢 / 想要它快点" | `airoot search refresh --json` 建一次索引（crawl 建的，**不是 USN 索引**），之后查询走索引 | 不声称它是 Everything 级性能；`freshness.state=current` 只表示"上次遍历是最近做的" |
| "这个索引是什么状态 / 为什么报了 stale" | `airoot search status --json`、`airoot search explain <query> --json` | `stale` 只说明索引比 `--max-staleness-ms` 旧：refresh 或放宽约束，不要说它"坏了" |
| "确认一下现在到底什么状态" | `airoot doctor --verify --json` | 不把 `--verify` 的结果当成"已修复" |
| "诊断说投影/审计漂移了" | `airoot rebuild --plan --json` 看清要重建什么，再 `airoot rebuild --json` | **不改数据库**；不 adopt 陌生对象；不删任何文件 |

`where` 会说清楚它为什么这么选（`selection_reason`）。常见值的含义：

- `STEWARD_REFERENCE_HEALTHY` — 选中的是**用户本来就有的**对象（管家域，默认优先）。
- `MACHINE_MANAGED_HEALTHY` / `MACHINE_MANAGED_HEALTHY_BY_POLICY` — 选中的是 AIROOT 自己装的
  payload。后者表示 `policy/selection-policy.json` 显式把 owned 排在前面，要一起说明。
- `CURRENT_SOURCE_DEGRADED` — owned payload 坏了，**正常降级**到健康的 reference。
  退出码 2，不是失败；把 `evidence` 里的 `degraded_from` 报出来。
- `PROJECT_MANAGED_HEALTHY` — 项目内有绑定，项目优先。
- `NOT_FOUND` / `UNMANAGED_ONLY` / `REFERENCE_NOT_USABLE` — 没有可用答案，或只有诊断性候选。

**`zone` 怎么念（R / W / P）**：绑定带一个分区（`inventory --json` 的 `bindings[].zone`、`where --json`
命中时的 `zone`）。规则只有一条恒定式：**Zone W 永不进入 machine PATH，也不参与机器级发现**。

- `where` 候选行里的 `machine_discoverable: false` **就是这个意思**：它**健康、可用**
  （`usable: true`、`health: healthy`），只是机器级发现不会选它。**不要**把它念成"坏了"、
  "降级"或 `CURRENT_SOURCE_DEGRADED`——那是一次**策略性排除**，不是故障。
- W **可以**用，但只能通过**显式**激活：调用方给出匹配的 `--session <id>` 或 `--project <id>`。
  所以"这个 W 绑定永远用不了"同样说错了——**两个方向都不要说过头**。
- 机器级 `where` 遇到唯一的候选是 W 时返回 `found: false` + `NOT_FOUND`：这是**正常结论**，
  不是错误；候选行仍在 `candidates[]` 里，`machine_discoverable` 就是读者能看到的那个原因。

## 确认协议（三选一，不得增删改名）

`plan` 判定"必须确认"时会返回 `SCOPE_CONFIRMATION_REQUIRED`（退出码 4）和三个选项。
你必须**原样**把这三个选项交给用户，不许自己加第四个、也不许替用户选：

```text
project-isolated   只装进这个项目（Zone P，项目自治）
data-root          装进数据根，机器可见（Zone P → 机器级，是权限提升）
cancel             取消
```

规则：

- 选 `data-root` 是**权限提升**，需要它自己的批准（这个 build 签不出 token：见《批准》）；项目目录里的 manifest 不能自己升级自己
  （`airoot plan … --scope data-root --project <项目>` 会返回 `SCOPE_UPGRADE_REQUIRES_APPROVAL`）。
- **简单的必须不问**：被项目清单引用的依赖、单文件通用 CLI，CLI 已经直接给答案；
  你不要再问一遍，否则确认会退化成噪音，真正高风险的三类（装包 / 建环境 / 超 300 MB）也会失效。
- 体积未知时 CLI 返回 `null` + `SIZE_ESTIMATE_UNAVAILABLE`。**照实说未知**，不要估一个数。

## 下载的可信来源

装任何东西之前，来源与校验和是两个**不同**的问题，不能混着说：

- `airoot source list --json` 说明**允许联系哪些 host**（允许列表之外的 host 一律拒）。
- `airoot source resolve <capability> --version <v> --json` 给出**上游发布的校验和**对应的 digest，
  连同一个可以直接喂给 `plan` 的 `source` 文档（`airoot plan … --source-json <file>`）。
- **digest 来自上游的校验和文件**，不是你下载完自己算的那个——自己算完和自己比等于没验。
- **v1 不做签名校验**（`signature` 为 `null`）。**"有摘要"不等于"有签名"**，不要暗示用户已验签。

## 批准

- 需要批准的动作会以退出码 4 返回，并在 `required_action` 里给出要批准的 `plan_hash`。
- `airoot approve` 只**消费**批准，永远不会凭空制造它；你也不得把"我调用了 approve"
  解释成"用户批准了"。
- 没有得到人工批准时，唯一正确的行为是停下来，把 plan 文件路径与 hash 交给用户。
- **这个 build 里没有任何东西能签发批准**：核心只校验，唯一的签发方是测试用的
  `cli/tests/fake_issuer.py`。所以上一条在当前版本里**走不到底**——`install` / `env persist` /
  `tool gc --apply` / `uninstall` 带 `--token-file` 时会返回 `PROVENANCE_FAILED`（退出码 7），
  消息里带这一句：`no production approval issuer exists in this build (ADR-0024 is the pending decision)`。
  裁决与三条路见 `docs/AIROOT-v0.3-实现决策记录.md` 的 **ADR-0024**（状态：提案）；
  **不要**试图自己造一个 token（伪造正是消费侧要拒绝的东西）。

## 绝不做的清单（§16.2）

1. 直接写 PATH、注册表、ACL、`store` 或 registry 数据库。
2. 自己实现安装逻辑，或调用外部软件私有的安装参数。
3. 把 `approve` 的调用当成人工批准。
4. 用递归 shell 命令替代 `airoot where` / `discover` / `inventory`。
5. 在 CLI 不可用、`schema_version` 不兼容或 recovery pending 时**模拟**一次安装。
6. 编造确定性：拿不准就说拿不准（`reason_code` 会替你区分"未知"与"没有"）。
7. 删除数据根内的任何文件——`forget` 只删登记；`uninstall` 只对 owned 生效。
8. 说"AIROOT 已实现 / 已可用 / 已具备 Everything 级性能"。允许的说法见仓库 `AGENTS.md` §8。

## 未实现的命令（不要调用）

以下命令在文档里出现过但**当前版本没有实现**。用户提到相关需求时，直说未实现，不要伪造：

```text
airoot bootstrap …        # Protected machine mode 的一次性提权窗口（broker 二进制、ACL、machine PATH），属 P2
airoot reconcile …       # 语义在规划里只有一句"先完成 repair/reconcile"，不足以实现
airoot path backup|restore   # 会写 PATH，属 P2 的受保护 broker
airoot root adopt|relocate   # 需要完整的 copy/verify/switch 规则
```

`airoot search` 本身**是可用的**（见命令地图）。它有三件事要记住：

- **索引是 crawl 建的**，不是 USN / NTFS 索引：`freshness.state=current` 的含义是"这份清单是最近一次
  遍历建立的"，不是"journal 没断档"。**永远不要**把它说成 Everything 级性能。
- 没有索引（或索引不覆盖这次请求的 root）时，回答仍然是 `degraded` + `SEARCH_FALLBACK_USED`（2），
  并且 `data.fallback.kind=crawl`；索引损坏时报 `SEARCH_INDEX_DEGRADED`（2）并回落遍历，不会崩。
- 用户问"为什么这么慢 / 有没有更快的实现"时，跑 `airoot search status --probe-native-index --json`：
  它会**只读地**看一眼卷，说明 native（USN）索引在这个 build 里不可用以及为什么。
  **不要**凭印象解释，也不要声称已实现 Everything 级性能。

没有 `bootstrap` 时 AIROOT 处于 `security_mode=policy_only`：这是**约定与审计**级别的约束，
不是 ACL 强制——同用户进程可以绕过。不要把它说成"受保护"。

会话激活的三条要点（`env activate` / `env deactivate` / `exec`）：

- `--session <id>` 是**注入的**，CLI 不会替你编一个：没有它就没有快照，也就没有 `deactivate`。
- `deactivate` 恢复的是**激活前的值**（原先不存在就移除），并移除本次加入的 PATH 条目；
  声明状态变化过之后它会报 `SESSION_STATE_STALE`（退出码 2），而不是假装成功。
- `exec` 是**唯一会派生子进程**的命令：`--` **之后**的一切原样交给子进程，**之前**的选项属于
  AIROOT（`--json`、`--root` 写在外部引用 id 之后也会被正确识别）。子进程非零退出不会污染
  AIROOT 自己的退出码——它会以 `CHILD_PROCESS_FAILED`（退出码 2）返回，真实状态在 `exit_status`。

## 看到不认识的取值（详见 references/field-values.md）

输出里的每个取值都应该能念出来。**念不出来就去查 `references/field-values.md`**——它按 schema 列出每个
字段的全部合法取值，并标出**这一版真的会写出来的是哪些**：

- 表里带 **†** 的值表示**这一版没有任何代码会写它**（合法、能通过校验，但你在真机上不会遇到）。
  遇到 † 的值**不要为它写分支**：那说明有人在手写 JSON，或者版本已经变了——去核对，不要猜。
- `null` 只在 `含义` 里解释（空值不是字符串，不参与 † 的判定）。
- **三套 `scope` 不要混**：绑定的 `system|machine|session|project`、环境变量持久化的 `user|machine`、
  依赖分流的 `project|data-root`。拼写相同，意思不同，看它出现在哪个字段里。
- **`evidence[].kind` 与 `where` 的 `selection_reason` 是自由字符串**（schema 没枚举），它们也在那张表里，
  权威是**代码**而不是 schema。特别注意：`where` 有**两个像码的字段**——`reason_code`（注册词表）与
  `selection_reason`（另一套，12 个值里只有 3 个也是注册码），**别拿一个去查另一个的表**。

取值**域**的权威是 `cli/schema/*.schema.json`（这张表逐行从 schema 解析出来，一致性由
`cli/tests/test_l1_field_values.py` 守着）；schema 没有枚举的那两组，权威是写它们的代码，由
`cli/tests/test_l1_label_vocabularies.py` 守着；取值**语义**的权威是核心契约。

## 退出码（详见 references/reason-codes.md）

```text
0 成功（含 degradation 与 info 级结论）      5 需要权限（如 machine 级环境变量）
1 没找到                                      6 需要恢复（root/registry/数据根身份问题）
2 降级或漂移（结果可用，但状态不完美）        7 计划/来源问题（含 OWNERSHIP_REQUIRED）
3 损坏                                        8 输入或 Schema 非法
4 需要批准                                    9 能力未冻结 / 扩展不可用
```

`reason_code` 永远比退出码更精确：**先读 `reason_code`，再读退出码**。
