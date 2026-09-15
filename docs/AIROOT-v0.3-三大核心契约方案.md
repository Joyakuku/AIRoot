# AIROOT v0.3：三大核心契约方案

本文承接 `AIROOT-PLAN-v0.2.md`，只解决三件事：

1. 权限、批准和 Windows 执行边界；
2. desired、declared、physical、effective 状态之间的关系；
3. 安装事务、崩溃恢复和回滚。

本文是架构方案，不是安装脚本。文中提到的开源项目用于借鉴机制，不表示 AIROOT 应照搬其全部实现。

## 一、先冻结三个架构决策

### 决策 1：把“策略约束”和“操作系统强制”分开

同一个用户身份运行的 Agent 可以直接调用 Windows API 修改自己的 HKCU PATH。因此，单靠 `plan -> approve -> install` 不能从技术上阻止绕过。

AIROOT 需要明确两种模式：

| 模式 | 适用对象 | 安全含义 |
|---|---|---|
| Protected machine mode | Zone R、machine capability | R 目录和机器 PATH 由管理员保护，变更经 UAC/elevated broker；可以防止普通用户进程直接写入受控对象 |
| User compatibility mode | 用户目录、临时工具、无管理员权限场景 | 可以提供计划、审计和约定，但不能声称能阻止同用户 Agent 直接修改 HKCU 或用户目录 |

v1 默认使用 Protected machine mode。User PATH 不再承担“安全边界”的职责；它最多作为兼容模式或显式的 session 入口。

### 决策 2：registry 采用 SQLite，JSON 作为投影

JSON 适合阅读和传给 Agent，但不适合作为并发写入、崩溃恢复和迁移的唯一数据库。v1 采用：

```text
state/registry.db       authoritative declared state
state/registry.json     read-only export/debug projection
state/events            authoritative SQLite event table
logs/audit              derived read-only audit projection
```

SQLite WAL 提供进程级锁、事务和一致性读取。所有写入经过 schema migration 和单一写入入口；Agent 不能直接编辑数据库。`state/events` 是历史事实的权威来源，`logs/audit` 丢失后可以重建，不能反向覆盖 event table。

### 决策 3：v1 只允许“无脚本 artifact Install Backend”进入受控事务

第一版只接收：

- 单文件 Managed Tool；
- 无安装脚本的 portable zip；
- 可验证的简单 Runtime Instance archive。

这些对象必须先登记为新的 instance，再通过 binding 暴露。Capability Extension 负责能力协议；Managed Tool/Runtime Manager 负责 payload、manifest、健康、版本切换和回滚；Install Backend 只负责获取和提交步骤，不能自行取得 active binding。

pip、conda、npm、厂商安装器可以作为后续 Install Backend，但必须声明外部副作用和回滚能力。核心事务不能把“执行任意第三方脚本”当成可原子回滚的步骤。

## 二、权限模型：保护 R，限制 W，显式激活 P

### 2.1 Windows 上的目录布局

根目录必须是专用目录，不使用盘符根目录：

```text
AIROOT\cli\
  app\         protected CLI Core and broker-facing code
  extensions\  signed capability extensions
  bin\          protected CLI and stable launcher
  tools\       managed tool bindings/views and manifest summaries, not payload storage
  env\         runtime/environment bindings, not runtime payload storage
  store\\       immutable managed tool/runtime payloads and verified artifacts
  exposure\    stable pointers and trusted shims
  state\       registry.db, root marker, generations, locks and plans
  tx\          transaction journals and staging metadata
  cache\       downloaded artifacts and derived search cache; never on global PATH
  logs\        diagnostic and audit records
```

`AIROOT\cli` 的实际绝对路径可以改变，但 registry 必须同时保存 `root_instance_id`、volume serial 和规范化绝对路径。路径解析要拒绝 reparse point、未预期的 UNC 路径和越出 root 的相对路径。

### 2.2 ACL 基线

Protected machine mode 的最低 ACL：

| 对象 | SYSTEM | Administrators | 普通 Users |
|---|---|---|---|
| `tools`、`env\runtimes`（binding/view，无 payload）、`store`、`exposure` | Full | Full | Read/Execute |
| `env\contexts`、`state\plans` | Full | Full | 受控读写；不参与 machine PATH |
| `state\registry.db`、`state\root.json`、`tx` | Full | Full | Read；只能由 broker 写 |
| `cache`、`cache\search` | Full | Full | Read/Write，但不可执行发现 |
| `logs\audit` | Full | Full | Read；只能由 broker 追加 |

其中 `env\runtimes` 仅保存 Runtime Instance 的 binding/view 和 launcher metadata；Python、Node 等 runtime payload 仍只能位于 `store`。`env\contexts` 保存受控环境变量视图，不代表 machine PATH 或 runtime 所有权。所有子目录继承基线 ACL，并由 `doctor` 检查实际 ACL 是否偏离。不要依赖父盘默认权限，也不要把用户可写 cache 放进 R 的执行路径。

### 2.3 PATH 设计

机器 PATH 只保留一个 AIROOT 管理项：

```text
AIROOT\cli\exposure\bin
```

`exposure\bin` 里面是受保护的 shim 或 launcher。它们根据 registry 解析当前 active instance，不把每个版本目录都写进 PATH，也不把 W/P 放进机器 PATH。

安装期间只由 elevated broker 修改 HKLM PATH。bootstrap 需要一次管理员确认；以后添加工具不需要反复重写 PATH。

如果必须支持无管理员运行，使用 User compatibility mode，并在所有返回值中标记：

```json
{
  "security_mode": "policy_only",
  "enforcement": "same_user_can_bypass"
}
```

这样可以避免把“审计约定”误报成“操作系统强制”。

### 2.4 三类执行主体

```text
airoot-cli          普通用户进程：discover / where / plan / doctor
airoot-elevated     短时管理员 broker：写 R、写 HKLM PATH、提交事务
human               UAC 或独立批准界面的最终授权者
```

Install Backend 只能返回计划、artifact 和检查结果，不能直接拿到 R 的写权限。CLI 将 canonical plan 写入临时位置后启动 elevated helper；helper 必须重新计算 `plan_hash`、校验 machine/root 绑定和 artifact digest，不能信任 CLI 传来的“已验证”字段。

### 2.5 批准记录

批准不能由命令名推断为人类批准。批准对象至少包含：

```json
{
  "approval_id": "approval-...",
  "plan_hash": "sha256:...",
  "root_instance_id": "...",
  "machine_id": "...",
  "policy_revision": 7,
  "approval_mode": "human | policy",
  "issuer": "protected-approval-broker",
  "approved_by_sid": "S-1-...",
  "issued_at": "...",
  "expires_at": "...",
  "nonce": "...",
  "signature": "base64:..."
}
```

低风险动作可以由受保护的 policy 自动批准，但必须记录 `approval_mode=policy`。需要人类确认的动作应在 UAC 或独立 UI 中完成；`airoot approve` 只能消费批准，不应成为 Agent 自己制造批准的入口。批准 token 必须由受保护 issuer 签名，绑定完整 plan hash、root、machine、policy revision 和一次性 nonce。broker 在 SQLite 事务中原子标记 token 已消费；重复消费、撤销、过期、签名无效或 policy revision 变化都必须拒绝提交并记录事件。

## 三、状态模型：增加 effective state，分离对象类型

### 3.1 五种事实

v0.2 的三种事实还不够。AIROOT 应维护以下五个概念：

| 事实 | 回答的问题 | 来源 |
|---|---|---|
| Desired | 应该有什么 | 受信任 manifest / 项目声明 |
| Declared | AIROOT 声明有什么 | `registry.db` |
| Physical | 磁盘和 OS 实际有什么 | 文件系统、ACL、注册表、版本探测 |
| Effective | 当前进程或新进程实际能发现什么 | PATH、session env、launcher 解析 |
| Historical | 之前发生过什么 | transaction/event log |

核心 diff：

```text
desired    <-> declared     policy drift
declared   <-> physical     installation/integrity drift
physical   <-> effective    exposure/session drift
historical <-> current      incomplete or unexpected change
```

没有 effective state，`where` 无法解释“文件存在但当前终端找不到”以及“registry 已更新但旧 IDE 仍使用旧版本”。

### 3.2 对象类型

Project 不应与 Capability、Managed Tool、Runtime 并列为同一种对象；建议拆成：

```text
Capability   逻辑能力，例如 python、node、jq
Implementation Instance   capability 的可选择实现
Managed Tool Instance   AIROOT 维护的不可变 payload 版本
Binding      instance 如何暴露到 machine/session/project
Environment  可激活的执行上下文及其变量
Project      能力的消费者、所有者和边界
```

对象域必须分开：

- Capability Extension 是能力协议模块；
- Implementation 是某个 binding key 当前选择的能力实现；
- Managed Tool Instance 是 AIROOT 拥有生命周期的具体二进制 payload；
- Runtime Instance 是 AIROOT 管理的 Python、Node 等运行时；
- External Reference 只保存外部路径和漂移证据，不获得源文件所有权；
- Install Backend 是获取、验证、stage、commit、rollback 的安装后端。

Managed Tool Instance 的完整形态是 registry instance + store payload + tools binding/view + exposure launcher。更新必须创建新的 instance 和 generation；旧 active 在新版本 VERIFIED_AGAIN 通过前继续可用。
示例：

```json
{
  "capability_id": "python",
  "instance_id": "python/cpython/3.12.7/win-x64",
  "kind": "runtime",
    "install_backend_id": "portable-archive",
  "artifact_digest": "sha256:...",
    "install_root": "AIROOT\\cli\\store\\python\\cpython\\3.12.7\\win-x64",
  "bindings": [
    {
      "scope": "machine",
      "zone": "R",
      "exposure": "stable_launcher",
      "active": true
    }
  ],
  "physical": {
    "status": "present",
    "manifest_digest": "sha256:..."
  },
  "effective": {
    "new_process": true,
    "current_process": false
  }
}
```

### 3.3 状态和所有权

推荐状态：

```text
planned -> staged -> installed -> active -> healthy
                         |          |
                         v          v
                       broken    retired

discovered -> unmanaged -> adopted
                 |
                 v
             excluded/quarantined
```

扫描到的陌生对象只能进入 `discovered` 或 `unmanaged`，不能因为它看起来像 Python 就自动变成 managed。`adopt` 是一次显式的、有来源和完整性检查的变更。
Managed Tool 生命周期为 discovered → planned → staged → installed → verified → active → healthy/degraded/broken → retired → garbage_collectable。retire 不删除 payload；gc 只能回收无 binding、无 rollback 保留和无 transaction 引用的 retired instance。doctor、discover 和 rebuild 不自动删除。

### 3.4 Registry 写入规则

- 所有路径在写入前规范化，并保存相对于 root 的 canonical path；
- 每个 instance 绑定 `install_backend_id`、artifact digest、平台和架构；
- registry 使用 generation 号做 compare-and-swap，防止旧 Agent 覆盖新状态；
- JSON 输出是数据库投影，不接受直接编辑；
- 重建时生成新的 registry generation，并保留旧数据库为只读证据。

## 四、事务模型：把“流程图”改成可恢复状态机

### 4.1 事务状态

```text
PROPOSED
  -> APPROVED
  -> FETCHED
  -> VERIFIED
  -> STAGED
  -> COMMITTED
  -> REGISTERED
  -> ACTIVE_BOUND
  -> EXPOSED
  -> VERIFIED_AGAIN
  -> FINALIZED
```

任何阶段都可能进入：

```text
FAILED
EXPIRED
ROLLBACK_PENDING
ROLLED_BACK
RECOVERY_REQUIRED
```

状态变化必须先写入 SQLite WAL 和 transaction journal，再执行可能产生外部效果的操作。每个步骤声明 `idempotent`、`reversible` 和 `side_effects`。`EXPIRED` 是批准或 plan 生命周期结束的终态，不改变 active binding；重新执行必须生成新的 plan/approval，不得复用原 nonce。

### 4.2 受控安装算法

1. CLI 生成 canonical plan，包含精确 artifact digest、目标 instance、root/machine 绑定和依赖闭包。
2. 获得 human 或 policy approval，批准只绑定 `plan_hash`，并设置过期时间。
3. elevated broker 创建全局 named mutex，拒绝同一 root 上的并发写事务。
4. artifact 下载到 content-addressed cache，cache 不参与执行发现。
5. 校验 HTTPS 来源、allowlist、digest，必要时校验签名或 provenance。
6. 在同一卷的 `tx/<id>/stage` 安全解压，拒绝路径穿越、reparse point 和未知脚本。
7. 生成安装树 file manifest，检查 entrypoint、架构和依赖。
8. 将 stage 原子移动到不可变 `store/<instance_id>`；旧 active instance 保留。
9. 写入 `installed` registry row，保持 inactive；
10. 在同一 SQLite transaction 中提交 active binding、generation，并将事务置为 `ACTIVE_BOUND`；
11. 让 stable launcher 观察新 binding，进入 `EXPOSED`，再运行 core/extension 验证确认 `where`、物理 manifest 和 exposure 一致。
12. 成功后进入 `FINALIZED`，记录前后状态、actor SID、approval_id、plan hash 和审计事件；失败则切回旧 generation。
13. 清理 stage；新版本只有在 retention 窗口过后才允许 garbage collection。

真正的跨文件系统原子事务不可实现，所以保证方式是“不可变对象 + 持久化日志 + 补偿动作 + 保留旧 generation”，不是假设所有步骤能一次性回滚。

### 4.3 崩溃恢复

`airoot` 启动、`doctor` 和 `repair` 都要检查未完成事务：

| 最后状态 | 恢复动作 |
|---|---|
| `FETCHED/VERIFIED` | 保留或删除 cache，不能改变 active 状态 |
| `STAGED` | 删除 stage，除非 journal 证明可以继续 |
| `COMMITTED` | 检查 store 是否完整；必要时继续 register 或回到旧 generation |
| `REGISTERED` | 确认 instance 仍为 inactive；不能被 launcher 选择 |
| `ACTIVE_BOUND` | 以 journal 的新旧 binding 和 registry generation 做对账 |
| `EXPOSED` | 重新执行验证；失败则切回旧 generation |
| `RECOVERY_REQUIRED` | 不自动猜测，输出证据并要求 `repair --tx <id>` |

回滚不删除新版本，只切换 active binding，并把新 instance 标记为 `retired` 或 `broken`。这样可以保留故障证据，避免“回滚同时丢失调查材料”。

### 4.4 Install Backend 和外部对象的边界

Install Backend 接口必须显式返回：

```text
discover()
plan()
fetch()
verify()
stage()
commit()
expose()
inspect()
rollback()
```

每个 Install Backend 还要声明：

```text
required_privilege
network_access
executes_scripts
reversible
reboot_required
estimated_size
source_mutation
supports_resume
failure_cleanup
```

每个 Capability Extension 的 operation 还必须独立声明：

```text
operation_kind: read | write | execute | mutate_system
target_scope
approval_required
overwrite_policy
cancellation_semantics
```

只要 `executes_scripts=true`、`reversible=false`、`source_mutation=delete|move` 或 operation 是 `mutate_system`，就不能走默认低风险自动批准路径。Install Backend 不能把外部 source 的删除、移动或覆盖隐含在 `commit` 中；必须由 plan 明确列出并单独批准。

外部对象的 source 变更规则：source 在 plan 后发生 hash、版本、架构、路径或权限变化时，broker 必须重新 probe，digest 不一致即 `FAILED`；目标 instance 已存在且 digest 相同时返回幂等成功，digest 不同则拒绝覆盖；import/recreate 失败或取消时保留原 external reference、清理 stage，不删除 source；`unadopt` 只移除 AIROOT 引用，不删除外部对象；source 位于 AIROOT 根内但无 registry 记录时先进入 `orphaned/recovery_required`，不能用普通 import 覆盖。
adopt --mode reference 仍是 external_reference；adopt --mode import 才会在安装事务成功后生成 managed tool instance；搜索发现的外部可执行文件不会自动进入 managed tools。

## 五、开源项目提供的可迁移经验

| 项目 | 可借鉴机制 | AIROOT 的采用方式 | 不应照搬的部分 |
|---|---|---|---|
| Nix / Guix | 不可变 store、profile、generation、rollback、GC | R 的 content-addressed instance、stable binding、generation 回退 | 不直接采用其完整声明语言和跨平台包生态 |
| OSTree | deployment、原子切换、保留旧版本、回滚 | 以 generation 管理 exposure 和 registry 对账 | AIROOT 不需要完整操作系统镜像模型 |
| Kubernetes controller | desired/observed state、reconcile、status conditions、重试 | desired/declared/physical/effective diff 和 doctor condition | 不把本机工具安装伪装成集群控制器 |
| Scoop | Windows portable 安装、manifest、shim、版本目录 | 作为 portable Install Backend 和 Windows shim 的参考 | Scoop 的用户可写根目录不能当作 R 的信任边界 |
| winget | manifest、来源、版本和 Windows 生态整合 | 后续 Install Backend 的来源和版本建模参考 | 不把 winget 的安装副作用直接纳入核心事务 |
| dpkg / apt / rpm | 多阶段安装、状态数据库、失败后恢复 | Install Backend 状态、未完成事务和 repair 语义 | 不声称脚本型安装可以完全原子回滚 |
| SQLite | WAL、锁、事务、一致性快照 | registry authoritative store 和 event log | 不让 Agent 直接编辑数据库文件 |
| TUF / in-toto / Sigstore | provenance、签名、artifact digest 分离 | source、integrity、signature 三个独立字段 | v1 可以先做 digest + allowlist，但不能混淆来源和 hash |

## 六、CLI/API 应冻结的最小契约

Managed Tool/Runtime 的最小管理入口：

    airoot tool list
    airoot tool status <tool-id>
    airoot tool verify <tool-id>
    airoot tool pin <tool-id>
    airoot tool retire <tool-id>
    airoot tool gc --plan
    airoot tool gc --apply --token-file <approval-token>

tool status 和 tool verify 只读取并验证 manifest、payload、binding、exposure 与 health，不切换 active；tool pin 通过新的 desired/selection policy 和 generation plan 生效；tool retire 取消 active binding 但不删除 payload；tool gc 只处理无 binding、无 rollback 保留和无 transaction 引用的 retired instance，apply 必须经过批准。External Reference 不得被这些命令隐式升级为 managed。
### `where`

```json
{
  "schema_version": 1,
  "found": true,
  "capability_id": "python",
  "instance_id": "python/cpython/3.12.7/win-x64",
  "executable": "C:\\Path\\To\\AIROOT\\cli\\exposure\\bin\\python.exe",
  "version": "3.12.7",
  "scope": "machine",
  "zone": "R",
  "health": "healthy",
  "management": "managed",
  "usable": true,
  "selection_reason": "MACHINE_MANAGED_HEALTHY",
  "candidates": [],
  "evidence": [],
  "effective_now": false,
  "effective_new_process": true,
  "source": "registry",
  "reason_code": "CURRENT_PROCESS_ENV_OLD"
}
```

### `doctor`

doctor 必须提供稳定的 `severity`、`code`、`evidence` 和 `remediation`，文本只是渲染层。未知对象默认报告，不删除。

### 退出码

至少区分：

```text
0 healthy/success
1 not found
2 degraded or drift
3 broken
4 approval required/expired
5 privilege required
6 transaction recovery required
7 invalid plan or provenance
8 invalid input/schema
9 extension unavailable
```

## 七、建议的 v1 垂直验收

不要先做一套脱离 Windows 的抽象框架。用一个单文件 portable tool 跑通：

```text
bootstrap
  -> plan
  -> human/policy approval
  -> fetch
  -> verify
  -> stage
  -> commit
  -> exposure
  -> where
  -> doctor
  -> 强制中断
  -> repair
  -> rollback
```

必须覆盖：

- 每个事务阶段被终止；
- registry 损坏和 JSON 投影损坏；
- active 文件 hash 被篡改；
- User PATH 被外部程序修改；
- 两个 Agent 并发提交；
- UAC 取消、网络中断、磁盘空间不足；
- zip 路径穿越和 reparse point；
- 当前旧终端与新进程的 effective state 不同；
- rebuild 发现陌生对象时不得自动 adopt。

## 八、最终落点

AIROOT v0.3 应冻结为下面这组原则：

1. R 的安全性来自 Windows ACL 和 elevated broker，不能来自 User PATH 约定。
2. W 可以执行，但只能通过显式 session/project activation，不参与机器级发现。
3. P 由项目拥有；AIROOT 只记录 binding 和结果，不接管项目生命周期。
4. SQLite 是 declared state 的权威存储，JSON 是 Agent/read-only 投影。
5. `effective state` 是独立事实，`where` 必须同时报告当前进程和新进程可见性。
6. 安装采用不可变 instance、generation、journal 和补偿式恢复。
7. 无脚本 portable Install Backend 是 v1 的安全基线；脚本型后端另行建模。
8. `human approval`、`policy approval` 和 `agent request` 必须可审计地区分。
9. 任何 rebuild 都先产生 unmanaged/quarantine 结果，不自动接管陌生文件。
10. 管理员威胁模型之外的情况下，AIROOT提供的是检测、隔离和恢复，而不是对抗管理员。
