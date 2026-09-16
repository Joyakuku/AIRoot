# AIROOT 最小版本 v1 —— 定义、验收判据与显式排除

**这份文档说的是"做到哪里算做完"，不是路线图。** `AGENTS.md` §8 的《完成判定的口径》管的是
**允许怎么描述这个项目**（禁止说"已实现 AIROOT"之类）；这份文档管的是**这一轮迭代的目标形态**，
逐条给出可观测的判据、今天的状态、以及由哪个工作项把它变成真的。

基线（本次迭代起点）：commit `3a77370`，`pytest cli/tests` **1285 passed / 23 skipped**（合计
1308 项），golden 语料 43 个，已发布 schema 20 个，冻结能力 `cap-3`（8 项）。

## 1. 定义（这一版的口径）

> 最小版本 = 一条**从零到用起来再收干净**的真实闭环：声明能力 → 从可信上游取得真实 artifact →
> 校验 → 通过 CLI 签发批准 → 批准被消费 → 安装到 `store` 并 `FINALIZED` → `tool verify` 摘要与
> 上游校验和逐字节一致 → `where <capability>` 解析到它 → **有一个动词真的把这份受管 payload 跑
> 起来并如实报告它的退出码与输出** → 通过一个**不依赖 machine PATH 改动**的稳定入口也可调用它 →
> 切换活跃版本不需要改 PATH 也不需要重写入口 → `tool retire` + `tool gc` 收干净**（`gc` 必须真的
> 作用在这些真实 payload 上，不是只对模拟 payload 生效）** → `doctor` 无 error；
> 中途崩溃能从 journal 恢复或按规则回滚。
> 另外，**账本要能表述"AIROOT 装的安装器，又安装了别的东西"**：那批产物可以被登记为 reference，
> 登记之后删掉 AIROOT 本体不会带走它们，`where` 也不会开始猜测它们。
> 全程不需要管理员权限，全程不写 machine PATH，全程 `security_mode=policy_only`。

**这段话里每一句都是可观测的**，这是它作为"完成定义"而不是"愿景"的唯一资格。下面的判据表把它
拆成 16 条，每条写明**怎么看**、**今天是什么状态**、**谁负责把它变成真的**。

## 2. 逐条验收判据

`今天` 一列是本次迭代**起点**的实测状态（不是估计）。`W*` 是本轮的工作项编号。

| # | 判据 | 怎么看 | 今天（起点） | 工作项 |
|---|---|---|---|---|
| 1 | 能力在**冻结清单**里，不是临时发明 | `capability list --json` 里有这个名字，`plan <cap>` 不退化成 `CAPABILITY_NOT_DECLARED` | ✅ `rust-toolchain` 已在 `cap-3` | — |
| 2 | 从**可信上游**取得真实 artifact | `source resolve <cap> --version <v>` 给出 `artifact_url` 与上游校验和 URL；`offline=false` | ✅ 已通 | — |
| 3 | 摘要**来自上游**，且逐字节一致 | 上游 `.sha256` 文件里的期望值 == store 里文件的 SHA256 | ✅ §117 实测相同 | — |
| 4 | **通过 CLI** 签发批准 | 有一个 CLI 动词能签发（今天只有人写 Python 调 `tx.issuer`） | ❌ 没有动词 | **W4** |
| 5 | 批准被**消费**并记进账本 | `approve <plan_file> --token-file <token>` 成功，审计里留下 `approval_mode` | ✅ 机制在位（§116） | — |
| 6 | 安装到 `store` 并 `FINALIZED` | `install … --token-file …` 报 `state == "FINALIZED"`，generation 递增 | ✅ §117 真机走过一次，**无自动化** | **W6** |
| 7 | `tool verify` 与上游一致 | `tool verify <instance>` 的 `verified=true`，整树摘要 == 上游校验和 | ✅ 同上 | **W6** |
| 8 | `where <capability>` 解析到它 | `where <cap>` 的候选是那条 owned binding，`source=registry` | ✅ 已通（指向安装器本体） | — |
| 9 | **有一个动词真的把受管 payload 跑起来**，并如实报告退出码与输出 | 新增 `run <instance-id> [-- args]`；退出码与 stdout/stderr 原样带回；未登记/已回收/载荷缺失**明确拒绝** | ❌ 没有动词（§118 是用绝对路径手跑的） | **W2** |
| 10 | 通过**不依赖 machine PATH** 的稳定入口也可调用它 | `cli\exposure\bin\<entry>.cmd` 真实存在且能转发；`path verify` 的 `launcher_present=true` | ❌ 目录不存在（`launcher_present: false`） | **W5** |
| 11 | 切换活跃版本**不改 PATH、不重写入口** | 切换后转发器字节不变、machine PATH 一字未改、`where` 报的入口路径不变而目标变了 | ❌ 没有入口，因而没有可切的东西 | **W5** |
| 12 | `tool retire` + `tool gc` 收干净（**真的删掉真实 payload**） | `gc --plan` → `gc --apply`（要批准）之后 store 里那个实例**不见了**，且只删了 AIROOT 自己装的 | ⚠️ 机制在，只在**模拟** payload 上验过 | **W2** |
| 13 | `doctor` 无 error | `doctor --json` 的 `diagnostics` 里没有 `severity=error` | 待本轮结束后实测 | **W6** |
| 14 | 中途崩溃能**恢复或按规则回滚** | 每个事务状态各一次故障注入；回滚只切 binding（`tx/rollback.py` 是唯一实现） | ⚠️ 有机制，缺真实闭环上的覆盖 | **W6** |
| 15 | 账本能表述"**安装器又装了别人**" | `.cargo`/`.rustup` 作为 `external_reference` 登记；`uninstall` 报 `OWNERSHIP_REQUIRED`、`forget` 后文件一字节未少 | ❌ 账本里一句可查的话都没有 | **W3** |
| 16 | 全程**不提权、不写 machine PATH、`policy_only`** | 任何一步都不出现 UAC；`path verify` 的 `path_written=false`；文档里的 posture 恒为 `policy_only` + `same_user_bypass` | ✅ 起点即如此，本轮不得破坏 | 全部 |

**"逐字节一致"与"真的跑起来"这两条是本定义的重心**：前者是"取回来的东西没被换过"，后者是
"它在 AIROOT 的理解里真的能用"。§118 的教训是这两件事都不能靠声称——**必须真的执行一次**。

## 3. 显式排除清单

下面这些**不属于**本最小版本，**本轮不做，后面几轮也不因为"顺手"而做**。它们每一项都至少需要
提权或受保护状态，或者属于路线图上更后面的阶段：

| 排除项 | 为什么不在这一版 |
|---|---|
| 提权 / UAC / 受保护 broker 服务器那一半 / 跨用户行为 | 需要受保护状态；ADR-0045 已把安全归属移到使用方 |
| ACL 强制（`caps/acl.py` 写一侧接动词或 schema） | 同上；写一侧**继续只作为库**，不接任何动词、不接任何 schema |
| machine 级环境变量持久化 | 需要提权（`--scope machine` 今天报 `PRIVILEGE_REQUIRED`） |
| P6：project manifest / `reconcile` | 路线图在 `file_search` 与 Runtime 之后 |
| P3 的 USN 常驻索引器 | 需要 broker 做初始枚举；其前提本身**尚未裁决**（`AGENTS.md` §8 记着） |
| 签名与来源证明（TUF / Sigstore 级） | v1 只做 digest，来源清单已如实这么写 |
| macOS / Linux provider | v1 只做 Windows |
| （P5 内部）runtime health 的完整体系、多 runtime 版本矩阵 | **P5 这一版只做"能跑起来"那一半** |

**也不允许**：把任何一条 `NOT_IMPLEMENTED`(1) 改成假的实现；把失败码用在它不描述的东西上；
让 `where` 去猜未登记的位置。

## 4. 完成时允许说的话

按 `AGENTS.md` §6 的模板口径（**不加码**）：

> 最小版本闭环已可复现——`plan → issue（本机签发）→ approve → install → FINALIZED → verify →
> run（真的执行一次）→ where → 稳定入口调用 → retire → gc（真的删掉真实 payload）→ doctor 无 error`；
> 切换活跃版本不改 PATH、不重写入口；`.cargo`/`.rustup` 已作为 reference 登记、删掉 AIROOT 本体不会
> 带走它们且 `where` 不会去猜它们；崩溃可从 journal 恢复；全程不需要管理员权限、
> `security_mode=policy_only`；X 项测试通过（其中 Y 项跳过），golden 语料与全部计数守卫一致，
> 真机验收脚本逐项通过。
>
>
> **16 条判据现在全部成立**（逐条实测状态见 §5）。崩溃恢复由 pytest 的
> `test_l2_recovery_drivers.py` 逐边界覆盖，真机闭环由 `real_machine_acceptance.py` 覆盖——
> **两者合起来**才是这一条的证据，缺一条都不算。

**不允许说**：AIROOT 已实现 / 已可用 / 具备 Everything 级性能 / 已具备受保护边界。
## 5. 结果：本轮结束时的实测状态

**"起点"那一列不动**（它自称是起点的实测状态，改它就等于改历史）。这一节是它的另一半：每条判据现在
是什么状态、由什么证据支撑、**哪一条没有做全**。

| # | 判据 | 结束时 | 证据 |
|---|---|---|---|
| 1 | 能力在冻结清单里 | ✅ | `cap-3` 未变 |
| 2 | 从可信上游取得真实 artifact | ✅ | §117（真机、上游校验和） |
| 3 | 摘要来自上游且逐字节一致 | ✅ | §117：12 721 664 B，SHA256 与上游相同 |
| 4 | **通过 CLI 签发批准** | ✅ | §122 / ADR-0049：`airoot issue`，10 条测试，5 个坏法验红 |
| 5 | 批准被消费并记进账本 | ✅ | §116 |
| 6 | 安装到 `store` 并 `FINALIZED` | ✅ | §117 真机 + §124 闭环（`state=FINALIZED`，generation 从 1 起） |
| 7 | `tool verify` 与上游一致 | ✅ | §124：`verified=true`，`problems=[]` |
| 8 | `where <capability>` 解析到它 | ✅ | §124：`management=managed`、`source=registry` |
| 9 | 有动词真的把受管 payload 跑起来 | ✅ | §120 / ADR-0047 + §124：`run --capability` 的 `exit_status=0`、`persisted=false` |
| 10 | 不依赖 machine PATH 的稳定入口 | ✅ | §123 / ADR-0050 + §124：`cli\exposure\bin\archive.cmd` 真实存在、**由 `cmd.exe` 真的转发**、`launcher_present=true` |
| 11 | 切换活跃版本不改 PATH、不重写入口 | ✅ | §125：真机闭环上装了 `9.9.9` 再装 `9.9.10`，入口**逐字节相同**、入口路径不变、`where` 的目标变成新版本，machine PATH **读出来前后逐项相同** |
| 12 | `retire` + `gc` 真的删掉真实 payload | ✅ | §124：`gc --apply` 之后那个 store 目录不在了 |
| 13 | `doctor` 无 error | ✅ | §124：`status=healthy`、无 error/warning |
| 14 | 中途崩溃能恢复或按规则回滚 | ✅ | §128 + §129：`repair` 用**事务自己的** driver，且恢复时会从 fetch 目录重建进程内状态——真 artifact 事务停在 `FETCHED`/`VERIFIED`/`STAGED`/`REGISTERED`/`ACTIVE_BOUND`/`EXPOSED` **任一边界**之后都到 `FINALIZED`，generation 只加 1、`integrity_problems()` 为空、二次 `repair` 是 `no_action` |
| 15 | 账本能表述"安装器又装了别人" | ✅ | §121 / ADR-0048：`.cargo`/`.rustup` 是 `external_reference`，`uninstall` 报 `OWNERSHIP_REQUIRED`(7)，`forget` 后文件一字节未少 |
| 16 | 全程不提权、不写 machine PATH、`policy_only` | ✅ | 全轮无 UAC；`path verify` 的 `path_written=false`；五份文档的 posture 未变 |

**所以这一版可以说的和不可以说的，界线就在这里**：**16 条判据全部成立**，没有一条停在"机制在、那一步
还没有证据"上。§4 那句话因此可以原样说，但它必须带着那句括注说——**16 条的证据来自两处，不是一处**：
闭环由 `real_machine_acceptance.py` 在真机上跑通（含真的删除），崩溃恢复由
`cli/tests/test_l2_recovery_drivers.py` 逐边界注入证成。**不要说 16 条都在真机上验过**——
故障注入不是、也不该是机器级的动作。

这一句和上面那张表**不是两份独立的说法**：收尾句里的数字、它点名"未证成"的那几条、以及 §4 那句
"全部成立"，都由 `结束时` 这一列推出（守卫第三十六组，草案 §130）。**改表而不改它会被报出来**；
"起点"那一列仍然不动——它记的是起点的事实。

**顺带记下这一轮的三个口径变化**，免得下一轮把它们当成回退：

1. `launcher_present` 从"那个目录在不在"改成"**至少有一个稳定入口**"（§123）：判据 #10 问的是后者。
2. `run` 的位置参数从"可选 instance + REMAINDER"改成"**单个 REMAINDER + 显式切分**"（§123.2）：前者让
   `--` 之后的第一个 token 被喂给可选位置参数。
3. `where` 新增**可选**属性 `launcher`（`schema_version` 仍是 1）：加必填是破坏性变更，而"可选"与
   "总是发"是两件事，后者由测试钉住。
