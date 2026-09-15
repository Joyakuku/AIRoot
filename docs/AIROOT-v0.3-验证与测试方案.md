# AIROOT v0.3：验证与测试方案

本文是实现前的测试契约。它描述要证明什么、如何构造证据以及哪些测试必须在隔离 Windows 环境中执行。它不包含 AIROOT 实现代码，也不要求修改当前机器的 PATH、注册表或 ACL。

测试必须同时覆盖两个对象域：Capability Extension（能力协议模块）和 Managed Tool/Runtime Instance（AIROOT 实际维护的 payload/运行时）。前者回答“AIROOT 能做什么”，后者回答“AIROOT 维护哪一份可执行对象”；External Reference 只表示外部引用，不得被测试误判为 managed。

## 一、测试目标

AIROOT v1 的质量不是“成功安装了几个工具”，而是证明下面四件事：

1. 受控对象只能按照明确的权限和批准规则发生改变；
2. registry、磁盘和进程可见性之间的差异能够被准确解释；
3. 在中断、重复调用和部分失败后，系统能恢复到一个已知 generation；
4. 不可信的 manifest、artifact、项目目录、Capability Extension 和 Install Backend 不能越过安全边界。

## 二、测试环境分层

### L0：纯函数与协议测试

不接触真实 PATH、注册表和管理员权限，可在任何开发机运行。

覆盖：

- canonical path 和 reparse point 解析；
- plan canonicalization 和 `plan_hash`；
- manifest、registry、`where`、`doctor` JSON schema；
- capability/version/architecture 满足关系；
- desired/declared/physical/effective diff；
- 事务状态转移是否合法；
- exit code 和 reason code；
- policy 的 allow/deny/approval-required 计算。

### L1：隔离文件系统和 SQLite 测试

使用临时目录、临时 SQLite 数据库、fake extension 和 fake install backend，不写宿主机的 PATH 或 R 区。

覆盖：

- staging、commit、rollback；
- WAL 并发和 generation compare-and-swap；
- journal 重放；
- cache 到 immutable store 的移动；
- file manifest 和 artifact digest；
- 磁盘空间不足、文件锁、重复提交。

### L2：Windows 集成测试

运行在干净 Windows runner 或专用虚拟机中，允许使用 HKLM/HKCU、ACL、UAC helper 和真实 PowerShell。

覆盖：

- `AIROOT\cli` ACL；
- machine PATH 的单一 exposure entry；
- User PATH 中陌生项的保留；
- elevation 取消和权限不足；
- reparse point、junction、UNC path、长路径；
- x64/arm64 和新旧进程 environment block；
- PowerShell、cmd 的 session activation。

### L3：故障注入和恢复测试

由测试 runner 在每个事务边界主动终止进程、断网、制造文件锁或耗尽空间，然后重新运行 `doctor`/`repair`。

这是强制测试，不允许只依赖人工演示成功路径。

## 三、测试夹具

每个测试都应使用可重建夹具：

```text
TestRoot
  store/
  tools/                  managed tool binding/view
  env/                    runtime/environment binding/view
  exposure/bin/
  state/registry.db
  tx/
  cache/

FakeExtension
  deterministic response envelope
  controllable side effects

FakeInstallBackend
  deterministic artifact
  declared side effects
  controllable failure points

FakeClock
  expiry and retry tests

FaultInjector
  abort after every journal state
  network/disk/lock failures

MachineFixture
  machine_id
  root_instance_id
  user SID
  admin/non-admin token
```

测试结束必须删除临时 root、恢复环境变量快照，并确认没有把测试路径写入宿主机 PATH。

## 四、权限和批准测试

| ID | 场景 | 预期结果 |
|---|---|---|
| P-001 | 普通用户向 R 的 `store` 写文件 | 被 ACL 拒绝；doctor 报告证据，不改变 registry |
| P-002 | 普通用户向 W/cache 写文件 | 允许写入，但不能被 machine `where` 或 machine PATH 发现 |
| P-003 | User PATH 被当前用户直接追加陌生项 | AIROOT 保留该项并标记 unmanaged；不声称它受保护 |
| P-004 | 计划 hash 与批准 token 不一致 | elevated broker 拒绝执行，产生 `INVALID_APPROVAL` |
| P-005 | approval 过期 | 拒绝执行，不能自动刷新批准 |
| P-006 | approval nonce 重放 | 第二次消费失败；原 transaction 不重复执行 |
| P-007 | machine_id 或 root_instance_id 改变 | 原计划失效，必须重新 plan |
| P-008 | artifact 在批准后被替换 | digest 校验失败，R 和 exposure 不发生变化 |
| P-009 | UAC 被取消 | transaction 进入可恢复失败态，旧 active instance 保持可用 |
| P-010 | Extension/Install Backend 直接尝试写 R | 在接口层拒绝；核心只接受 Extension 的 envelope 或 Install Backend 返回的计划和 artifact |
| P-011 | policy 在批准后改变 | plan 绑定的 policy revision 不匹配，拒绝提交 |
| P-012 | low-risk policy 自动批准 | 允许执行，但 event 明确写 `approval_mode=policy`，不能伪装成人工批准 |
| P-013 | User compatibility mode 安装 | 返回 `security_mode=policy_only` 和 `same_user_can_bypass` |
| P-014 | token issuer/signature 无效 | broker 拒绝；不创建 active binding，不消费 nonce |
| P-015 | approval 被撤销或 policy revision 改变 | broker 拒绝；记录 `APPROVAL_REVOKED`/`POLICY_REVISION_MISMATCH` |
| P-016 | 同一 token 并发消费 | 只有一个 SQLite transaction 成功，其他返回 `APPROVAL_REPLAYED` |
| P-017 | `approval_mode=human` 但 approved_by_sid 与 issuer 证据不匹配 | 拒绝；不把 CLI 调用当成人工批准 |
| P-018 | Managed Tool payload 直接写入 tools 或覆盖旧 store instance | 拒绝；payload 必须进入新的 immutable store instance，tools 只能生成 binding/view |
| P-019 | Search Extension 找到外部 jq.exe 后自动 adopt | 拒绝；只产生 external/unmanaged candidate，必须显式 reference、import 或 recreate |
| P-020 | adopt --mode reference 后执行 tool verify | 保持 external_reference；verify 只能验证引用状态，不能把它升级为 managed |

P-003 和 P-013 是防止安全口径夸大的测试。它们证明系统能正确说明“这是约定和审计”，而不是虚假声称“同用户无法绕过”。

## 五、状态模型测试

| ID | 场景 | 预期结果 |
|---|---|---|
| S-001 | desired 有 python，declared 没有 | 报告 policy drift，可生成 plan，不自动安装 |
| S-002 | declared 有 instance，物理目录不存在 | 报告 installation drift，状态为 broken |
| S-003 | 物理文件存在，但 registry 没登记 | 进入 discovered/unmanaged，不自动 adopt |
| S-004 | 文件 hash 与 manifest 不符 | 状态为 broken；`where` 不返回 healthy |
| S-005 | registry 已 active，但当前旧 shell 找不到新版本 | `effective_now=false`、`effective_new_process=true` |
| S-006 | W 中有同名 python | machine `where` 不选择 W；激活 session 后才可见 |
| S-007 | 两个版本满足不同项目约束 | 返回明确 instance，不用模糊的裸 `python` 结果替代约束解析 |
| S-008 | rebuild 扫描到未知 exe | 只生成 unmanaged/quarantine 记录 |
| S-009 | registry generation 过期的 Agent 提交写入 | compare-and-swap 失败，旧状态不被覆盖 |
| S-010 | project manifest 请求 machine scope | 进入 approval-required 或 policy denied，不能由项目目录直接升级权限 |
| S-011 | root 盘符改变但 volume 身份一致 | 可进入 reconcile；不能把新盘符直接当作可信 root |
| S-012 | root volume 身份改变 | 原 registry 进入 recovery required，不自动接管新目录 |
| S-013 | `tools`/`env` 中无 payload、`store` 中有 instance | binding/view 可重建；不会把同一 payload 复制两份 |
| S-014 | managed binding 损坏、external reference 健康 | 正常降级：`found=true`、`usable=true`、`reason_code=CURRENT_SOURCE_DEGRADED`（退出码 2），`candidates` 保留两者证据；**不再**是 `CONFLICT_MANAGED_BROKEN`（ADR-0006）。无任何可用候选时才 `BROKEN`（3）。**同一编号在管家草案 §12 另有一条定义，两处必须同时更新**（守卫第十七组） |
| S-015 | project manifest 请求 machine scope | policy denied/approval required；不能由项目目录升级权限。（**重复登记**：与 S-010 是同一个场景、同一个期望，只有措辞不同；规范编号取 S-010，因为测试引用的是它。保留本行不改号，以免作废既有引用。） |
| S-016 | source 在 import plan 后 hash/版本变化 | 提交前重新 probe，digest mismatch；source 和旧 binding 不变 |
| S-017 | source 消失或权限变化 | reference 变为 `stale`/`drifted`，不自动删除或替换 |
| S-018 | 同一 binding key 竞争两个 active implementation | SQLite unique constraint/CAS 只允许一个 active，另一个必须重读 generation |
| S-019 | `where found=false` | instance/executable/version/management 等主体字段为 null，候选只在 `candidates` |
| S-020 | current shell 旧、new process 新 | `effective_now=false`、`effective_new_process=true`，reason code 稳定 |
| S-021 | 两个 managed tool 版本共存 | registry/store 可保留多个 instance，但同一完整 binding key 只有一个 active |
| S-022 | 新 managed tool 安装在 VERIFIED_AGAIN 前失败 | 旧 active instance 继续可用，新 instance 标为 broken/retired |
| S-023 | tool retire 后查询 | instance 不再被默认 where 选中，payload 保留到 GC 条件满足 |
| S-024 | tool gc 命中仍被 rollback 或 transaction 引用的 instance | 计划排除该 instance，不能删除 |
| S-025 | managed tool payload hash 漂移 | 标记 broken，冻结 active selection，保留证据和修复计划 |
| S-026 | discover 产生 external_reference/unmanaged 候选后未提供显式 import/recreate/adopt 计划 | 不生成 `planned` 状态，不写入 managed binding |
| S-027 | `env\\runtimes` 视图中出现 runtime payload | 报告布局漂移并拒绝激活；payload 必须位于 `store` |

## 六、事务和恢复测试

### 6.1 每个状态边界强制终止

对以下每个边界分别执行 `kill -9` 等价的 Windows 进程终止，然后重新运行恢复逻辑：

```text
PROPOSED
APPROVED
FETCHED
VERIFIED
STAGED
COMMITTED
REGISTERED
ACTIVE_BOUND
EXPOSED
VERIFIED_AGAIN
FINALIZED
```

预期：

- `PROPOSED` 到 `STAGED` 不改变 active capability；
- `COMMITTED` 后 store 要么完整存在，要么被安全清理；
- `REGISTERED` 必须仍为 inactive，不得被 launcher 选择；
- `ACTIVE_BOUND`、`EXPOSED` 和 registry generation 不一致时，repair 能依据 journal 修复；
- 旧 active generation 在新版本未验证前始终可回退；
- 不产生无法解释的半个 shim、半个 registry row 或悬空 current pointer。

### 6.2 事务测试矩阵

| ID | 故障 | 预期结果 |
|---|---|---|
| T-001 | 下载中断 | 只留下可复用或可删除 cache，不改变 R |
| T-002 | digest 校验失败 | stage 和 exposure 清理；transaction 标记 failed |
| T-003 | 安全解压发现路径穿越 | 拒绝整个 artifact，不产生部分安装树 |
| T-004 | stage 后磁盘空间不足 | 保留旧 active；清理 stage 并报告空间证据 |
| T-005 | commit 时目标文件被锁定 | 进入 rollback/recovery，不覆盖旧 generation |
| T-006 | `REGISTERED` inactive 后崩溃 | 不得被 launcher 发现；可安全清理或继续绑定 |
| T-007 | `ACTIVE_BOUND` 后 exposure/验证前崩溃 | 下次启动根据 journal 对账 generation；成功暴露或切回旧 binding |
| T-008 | registry 写入成功、验证失败 | active binding 回到旧 instance，新 instance 标为 broken |
| T-009 | 同一 plan 重复 install | 第二次幂等返回已应用 transaction，不重复下载或覆盖 |
| T-010 | 两个 Agent 并发 install | named mutex/DB lock 保证一个提交，另一个重读 generation 后重试或退出 |
| T-011 | repair 被重复调用 | 结果幂等，不能重复删除或重复写 audit event |
| T-012 | rollback 后再次启动 | old generation 可发现，新 instance 保留为 retired/broken |
| T-013 | Install Backend 声明不可逆副作用 | 默认 policy 拒绝或要求显式确认；核心不伪造 rollback 成功 |
| T-014 | SQLite WAL 恢复 | 数据库恢复到最后一个完整事务，不出现半行 metadata |
| T-015 | transaction journal 被截断 | doctor 报 `RECOVERY_REQUIRED`，不猜测缺失步骤 |
| T-016 | managed tool update 在 ACTIVE_BOUND 后崩溃 | 依据 journal/generation 对账；旧或新 binding 只能有一个 active，失败时回旧 generation |
| T-017 | tool gc --apply 中断 | 不删除仍有 binding/rollback/transaction 引用的 payload；重试必须幂等 |

## 七、Windows 安全负面测试

至少加入以下输入变体：

- `..\\`、绝对盘符、UNC 路径和大小写变体；
- junction/reparse point 指向 root 外；
- zip 中的符号链接、重复文件名和超长文件名；
- executable 与 DLL 同名但来自 cache/W；
- PATH 项末尾空格、引号、分号、大小写重复；
- 环境变量值包含换行、引号、`%VAR%` 和 PowerShell 特殊字符；
- manifest 使用未允许的 extension、Install Backend、域名、协议或架构；
- 下载重定向到不同域名；
- artifact digest 变化但文件名不变；
- project manifest 位于不可信仓库且请求 machine scope；
- registry 中的相对路径解析到 root 外；
- 旧版本 launcher 被替换成用户可写路径。

所有负面测试必须证明三件事：没有越过 root、没有改变 active generation、诊断中有稳定 reason code。

## 八、`where`、`doctor` 和 CLI 契约测试

### `where`

每种结果固定一个 JSON fixture。**下表是完整清单，与 `cli/tests/fixtures/golden/where_*.json` 双向相等**（守卫第二十九组：fixture 名与退出码都比）——一张"要覆盖哪些场景"的清单如果不与语料对账，就会像 §86 那两份"还没决定"的清单一样，各自完整地描述一个更小的集合（§87）。

| 场景 | fixture | 退出码 |
|---|---|---|
| healthy | `where_healthy` | 0 |
| not_found | `where_not_found` | 1 |
| version_unsatisfied | `where_version_unsatisfied` | 1 |
| unmanaged_only（只看到 unmanaged 候选） | `where_unmanaged_only` | 1 |
| broken（owned 坏了，且没有别的候选） | `where_broken` | 3 |
| current_process_stale | `where_current_process_stale` | 0 |
| owned 坏了但健康的 reference 顶上（ADR-0006 的正常降级） | `where_owned_broken_degrades_to_reference` | 2 |
| 弃用的 external fallback 开关不改变结果 | `where_deprecated_external_fallback_is_ignored` | 2 |

**原清单里的两个名字不是 `where` 的结果，已从表里去掉，原因记在这里**（§87 的实测；它们在这一节里待了很久，而语料里从来没有对应的东西）：

- `session_required`：这个 build 里**没有**这个结果——`caps/where.py` 不产生它，`where-response.schema.json` 里也没有对应取值。它是 session 槽位的**规划**用语，而 session 槽位今天由 `env activate` 的会话栈承担；
- `recovery_required`：它是 **`doctor` 的 remediation 与事务状态**，不是 `where` 的候选结果。恢复期为 pending 时 `where` 返回 `found=false` 加相应的 reason code，不会自称 `recovery_required`。

字段顺序不重要，字段名称、类型、schema version、reason code 和退出码必须稳定。文本输出只能作为 JSON 的渲染结果。

### `doctor`

每条 D1-D10 不变量至少有：

- 一个健康 fixture；
- 一个最小坏例；
- 一个包含证据的诊断结果；
- 一个不会误伤陌生对象的 remediation 预览；
- 一个 repair 后的再次验证。

### CLI 行为

测试：

- stdin 非交互和 JSON 模式；
- approval 缺失、过期和权限不足；
- Ctrl+C/进程终止后的退出码；
- locale 不同但 JSON 不变；
- 子命令未知、参数缺失和 schema version 不兼容；
- 现有 PATH 项的保留顺序；
- PowerShell 和 cmd 激活后的新子进程可见性。
- `search status` 等保留子命令与 `--query status` 的解析无歧义。

CLI fixture 必须验证统一退出码：`0` success、`1` not found、`2` degraded/drift、`3` broken、`4` approval required/expired、`5` privilege required、`6` recovery required、`7` invalid plan/provenance、`8` invalid input/schema、`9` extension unavailable。Search 的 `SEARCH_*` reason code 必须有明确映射，不能在 profile 中重新定义一套整数退出码。

## 九、属性测试和模糊测试

### 属性测试

对随机生成的路径、manifest 和事务序列验证：

1. canonical path 不能越出 root；
2. 同一 plan 的字段顺序变化不改变 `plan_hash`；
3. 重复执行幂等步骤不改变最终 generation；
4. rollback 后旧 active instance 仍满足原健康条件；
5. unmanaged 对象不会被 reconcile 自动提升为 managed；
6. 任意合法状态序列都不会同时出现两个 active machine binding。

### 模糊测试

优先 fuzz：

- zip/manifest/parser；
- registry migration 和损坏恢复；
- `where` 约束解析；
- environment activation 输出；
- plan canonicalization；
- reparse point 和 Windows 路径归一化；
- Extension manifest、通用 envelope 和 profile `data` schema；
- approval token 的签名、nonce 和一次性消费；
- search cursor 与 index generation 绑定。

## 十、跨文档契约和边界行为

以下测试用于验证四份文档没有“各自正确、合起来冲突”的情况：

| ID | 场景 | 预期结果 |
|---|---|---|
| C-001 | Extension response 缺少 `extension_id`、`data` 或 `reason_code` | schema 拒绝；不得按 search 专用顶层字段兜底 |
| C-002 | Search response 使用 `provider_id`、`backend` 或顶层 `results` | schema 拒绝；必须使用 `implementation_id` 和 `data.results` |
| C-003 | 同一 `(root,machine,platform,capability,scope)` 注册两个 active implementation | unique constraint/CAS 拒绝第二个；保留冲突证据 |
| C-004 | search index 写入 `state\\search` | ACL/path conformance 失败；规范位置必须是 `cache\\search` |
| C-005 | `REGISTERED` 事务被 launcher 查询 | 返回 inactive，不得暴露；只有 `ACTIVE_BOUND` 后才可选中 |
| C-006 | event table 与 `logs\\audit` 内容不一致 | 以 SQLite event 为权威；audit projection 可重建并报告漂移 |
| C-007 | Skill 更新替换 `app` 但删除 `state/store` | 更新被拒绝或回滚；持久数据必须保留 |
| C-008 | root relocate 过程中断电 | 新旧 root 至少一个可恢复；未验证新 root 不能成为 active |
| C-009 | external import/recreate 源变化、目标同名或取消 | source 保留；stage 清理；返回稳定 reason code；不覆盖既有 digest |
| C-010 | `airoot env activate` 试图修改父 shell | 只输出脚本/JSON 或由 `exec` 创建子进程；不得声称父进程已改变 |
| C-011 | machine index 返回其他用户无权路径 | 结果被过滤并带 coverage/freshness 证据；不泄露路径 |
| C-012 | manifest 缺少 digest、签名或 scope 权限 | 只能生成 plan/diagnostic，不能 install 或提升到 machine scope |
| C-013 | Skill 根缺少 `SKILL.md` 或文件在 `cli` 下 | Skill 加载失败；不能把目录误识别为可执行 Skill |
| C-014 | Extension 与 Managed Tool 使用同一个 instance identity 却声明不同生命周期 | schema/registry 拒绝；Extension implementation identity 与 managed payload identity 必须分离 |
| C-015 | tool pin 与 active implementation 不一致 | 先生成新的 selection/generation plan；不能直接改 launcher 或覆盖 store |

质量门槛中的名称应使用 `FakeExtension`、`FakeInstallBackend`，不要继续使用含义不清的 `FakeProvider`。

## 十一、性能和可靠性目标

实现前先冻结相对目标，而不是等到工具数量增加后再测：

- `where --json` 在无网络时只读 registry，不扫描整盘；
- healthy `doctor` 不重复 hash 整个 store，使用 manifest/cache；
- 1000 个 instance 的 registry 查询不依赖 PATH 长度线性扫描；
- 失败 transaction 的 repair 可重复执行；
- 进程被终止后，下一次启动能识别 pending transaction；
- registry backup 使用一致性快照，而不是复制正在写入的数据库文件。

具体毫秒阈值应在第一条垂直链路完成后以 Windows runner 的基线确定，不能提前伪造精确数字。

## 十二、实现前的质量门槛

开始写生产代码前，必须先具备：

1. registry、plan、where、doctor 的 JSON schema；
2. permission matrix 和 Protected/User mode 的明确承诺；
3. 事务状态机及每个状态的恢复动作；
4. fake extension、fake install backend、fault injector 和可重建 Windows fixture 的设计；
5. P/S/T 测试编号对应的验收记录模板；
6. 一条不涉及真实工具的 simulation test，能证明计划、批准、失败和恢复闭环。

第一条生产级垂直切片应使用固定的单文件 fake artifact。当前协议级切片位于 `..\\cli\\fake_vertical_slice\\`，Schema 位于 `..\\cli\\schema\\`，Broker 安全契约位于 `..\\docs\\broker\\AIROOT-受保护Broker方案-v1.md`（旧文写作 `..\\cli\\broker\\`）。只有该切片通过所有 P-、S-、T- 和负面测试，且真实 Windows L2/L3 测试完成，才允许接入 Python、Node、conda 或 npm。

## 十三、参考项目与测试借鉴

- Kubernetes controller：参考 desired/observed 状态、status conditions 和可重复 reconcile；
- Nix/Guix：参考 generation、不可变 store、profile 切换和保留旧版本；
- OSTree：参考 deployment 原子切换和 rollback 验证；
- Scoop/winget：参考 Windows manifest、portable artifact 和 shim 测试；
- dpkg/apt/rpm：参考多阶段状态、未完成安装和 repair，而不是假设所有脚本安装都可原子回滚；
- SQLite WAL：参考并发、崩溃恢复和一致性 snapshot 测试；
- TUF、in-toto、Sigstore：参考 artifact integrity、provenance 和签名验证的分离。

## 十四、测试完成的判定

方案阶段可以宣布“测试备齐”，需要满足：

- 任何核心安全承诺都有对应的正向、负向和误报测试；
- 每个事务边界都有故障注入和恢复预期；
- `where`/`doctor` 的所有机器可读结果都有固定 fixture（**守卫第二十九、三十组**：前者把 `where` 那张场景表与语料双向对账，后者要求每个 fixture 记录的退出码等于**它自己文档**推出的那个，并要求 schema 里 `doctor.status` 的每个取值都出现在某个 fixture 里——§87/§88 之前，`doctor_healthy` 的名称、状态与退出码三者互不相符，而 `healthy` 一个 fixture 都没有）；
- 测试不会污染开发机环境；
- 测试能区分协议失败、物理损坏、权限不足、恢复待处理和外部副作用；
- 所有不可能强制保证的内容，例如同用户绕过 User PATH，都被明确标记为 `policy_only` 而不是测试成“安全通过”。
