# AIROOT 受保护 Broker 方案 v1

本文把 AIROOT 的 Protected machine mode 收敛成一个可以实现和测试的边界。Broker 是短时、受保护的本机提交服务，负责写入 Zone R、提交 SQLite generation、维护 machine exposure 和消费批准 token。普通 CLI、Skill、Capability Extension、Install Backend 和搜索 indexer 都不能替代 Broker。

## 1. 信任边界

```text
┌──────────────────────────────────────────────────────────────┐
│ Agent / Skill / user CLI                                     │
│ discover, where, doctor, plan, read-only projection          │
└──────────────┬───────────────────────────────────────────────┘
               │ canonical plan + approval token
               │ authenticated local IPC
┌──────────────▼───────────────────────────────────────────────┐
│ airoot-elevated Broker                                       │
│ revalidate -> authorize -> stage -> commit -> expose -> verify│
└───────┬────────────────────────────┬────────────────────────┘
        │                            │
        ▼                            ▼
  Protected R zone              SQLite WAL registry/event log
  store/tools/exposure          generation + transaction journal

W/cache/search and external sources remain outside Broker trust.
```

Broker 的可信根是受 ACL 保护的 Broker binary、application manifest、root marker、registry 和 approval issuer key。`SKILL.md`、用户输入、项目 manifest、search 结果和普通用户可写的 Extension 代码都不是权限凭据。

## 2. 主体与权限

| 主体 | 默认身份 | 能做什么 | 明确不能做什么 |
|---|---|---|---|
| `airoot-cli` | 普通用户 SID | 读 registry、生成 plan、提交只读查询 | 写 R、消费 approval、修改 HKLM PATH |
| Capability Extension | 用户或受限进程 | 执行声明的 capability operation | 取得 registry 写权限或执行隐含安装脚本 |
| Install Backend | 用户或受限进程 | 返回 artifact、manifest、检查和 plan | 直接写 `store/tools/env/exposure/state` |
| `airoot-elevated` | LocalSystem 或受控管理员服务 | 重新验证并提交批准的 plan | 接受未经验证的 CLI 字段、自动猜测 active |
| Approval issuer | 受保护 UI/policy service | 签发一次性 approval token | 直接改变 registry 或跳过 Broker |
| Search indexer | 普通用户或受限 broker helper | 写派生 `cache/search` | 写 R、改变 managed binding、泄露无权路径 |

User compatibility mode 可以运行无 elevated Broker 的同用户模拟，但响应必须带：

```json
{
  "security_mode": "policy_only",
  "enforcement": "same_user_can_bypass"
}
```

它不能声称提供 Protected machine mode 的 ACL 强制性。

## 3. IPC 契约

生产 Windows 实现使用受 ACL 保护的 named pipe，例如 `\\.\\pipe\\airoot-broker-v1`。Broker 启动时只接受本机客户端，并校验客户端进程 token、用户 SID、完整性级别和 application identity。TCP、临时文件轮询和任意可写 socket 不属于 v1 IPC。

一次提交请求必须是一个完整 envelope，不能由客户端分段拼接安全字段：

```json
{
  "protocol_version": 1,
  "request_id": "req/install/jq-001",
  "operation": "commit_plan",
  "plan": "state/plans/plan-jq-001.json",
  "approval": "state/approvals/approval-jq-001.json",
  "client": {
    "sid": "S-1-5-21-1000",
    "pid": 1234,
    "integrity": "medium",
    "application_id": "airoot-cli"
  }
}
```

Broker 不信任路径字段的字符串形式。它打开 plan 和 approval 后重新 canonicalize 内容，拒绝 reparse point、越出 root、UNC 路径和 schema 版本不兼容；`plan_hash` 必须由 Broker 重新计算。

## 4. 提交算法

```text
1. authenticate local IPC client
2. load root marker, registry generation and policy revision
3. parse and validate plan schema
4. canonicalize plan without plan_hash
5. compute plan_hash and compare supplied value
6. validate root_instance_id, machine_id and expiry
7. validate approval signature, issuer, nonce and policy revision
8. re-probe source and artifact digest
9. enforce operation side effects and target binding policy
10. create transaction row and staging directory
11. fetch/verify/stage without touching active binding
12. commit immutable payload to store/<instance_id>
13. register inactive instance in SQLite
14. atomically CAS active binding + generation at ACTIVE_BOUND
15. write stable exposure metadata and verify it
16. mark VERIFIED_AGAIN then FINALIZED
17. atomically consume approval nonce and append event
```

`ACTIVE_BOUND` 是唯一可以改变 active binding 的提交点。失败发生在该点之前时，旧 active 不动；失败发生在该点之后时，Broker 依据 journal 对账，必要时切回旧 generation。新 payload 不在回滚时删除，以保留证据。

## 5. Approval token 规则

生产 token 至少绑定以下字段：`approval_id`、`plan_hash`、`root_instance_id`、`machine_id`、`policy_revision`、`approval_mode`、`issuer`、`approved_by_sid`、`issued_at`、`expires_at`、`nonce` 和签名。Broker 必须在 SQLite 事务中以唯一 nonce 消费 token。

以下情况一律拒绝提交并写入审计事件：

- 签名无效、未知 key_id 或 issuer 不受信；
- plan hash、root、machine、policy revision 任一不匹配；
- token 过期、已撤销或 nonce 已消费；
- plan 中的 artifact digest、source path、版本、架构或权限在批准后发生变化；
- operation 声明的副作用超出 policy；
- target binding key 已被新 generation 占用；
- Broker application manifest 或 root ACL 校验失败。

`approval_mode=policy` 仍必须记录 policy revision 和规则命中证据；不能渲染为人工批准。`approval_mode=human` 必须有与 issuer 证据一致的 `approved_by_sid`。

## 6. ACL 和受保护启动

Protected machine mode 的 bootstrap 顺序：

1. 创建专用 AIROOT 根和 `root.json`；
2. 设置 `app/extensions/bin/store/tools/env\\runtimes/exposure/state/tx` 的管理员写、普通用户读执行 ACL；
3. 设置 `cache/search` 和诊断日志的用户可写 ACL，但将其排除于 PATH 和执行发现；
4. 安装 Broker binary 到受保护应用目录；
5. 校验 application manifest 中的文件 hash、协议版本和签名；
6. 注册短时 Broker 服务或按需 elevated helper；
7. 只把 `AIROOT\\cli\\exposure\\bin` 加入 machine PATH；
8. 用 probe、doctor 和协议 smoke test 验证后才把 root 标记为 ready。

Broker 发现应用层可写、签名不匹配、root marker 不匹配、volume serial 改变、ACL 漂移或 schema 不兼容时，进入 `recovery_required`，停止提交，不自动接管新目录。

## 7. Install Backend 限制

Install Backend 只能通过临时目录返回 artifact、manifest 和检查结果。Broker 自己执行最终 digest、manifest、架构和 entrypoint probe。脚本型后端、`source_mutation=delete|move|overwrite`、不可逆操作或需要重启的操作不得走默认低风险 policy；必须在 plan 中显式列出并取得额外批准。

对于外部 source：

- `reference` 只建立 `external_reference`，不写 R；
- `import` 复制到新的 immutable `store/<instance_id>`，原 source 保留；
- `recreate` 按声明重新创建 runtime/environment，不直接搬移 `.venv`、conda 或 `node_modules`；
- 失败、取消和超时只清理自己的 stage，保留 source 和原 binding；
- source 发生 hash 漂移时返回 `DIGEST_MISMATCH`，不得覆盖旧 instance。

## 8. 审计和恢复

权威历史事实写入 `state/events`；`logs/audit` 是可重建投影。每条 event 至少包含：actor SID、request ID、approval ID、plan hash、transaction ID、generation、前后状态、artifact digest、reason code、时间和结果。

Broker 重启后按以下规则恢复：

| Journal 状态 | 恢复动作 |
|---|---|
| `PROPOSED` 到 `VERIFIED` | 标记 pending/expired，不改变 active |
| `STAGED` | 校验 stage manifest；不完整则清理 stage |
| `COMMITTED`/`REGISTERED` | 确认 store 完整，保持 inactive |
| `ACTIVE_BOUND` | 对账 registry generation、exposure 和旧 binding |
| `EXPOSED` | 重新 probe；失败切回旧 generation |
| `VERIFIED_AGAIN` | 完成 event、nonce consume 和 FINALIZED |
| `RECOVERY_REQUIRED` | 只输出证据，等待显式 `repair --tx` |

任何自动恢复都不能删除外部 source、隐式升级 external reference 或选择“最新版本”作为 active。

## 9. 不属于本方案的内容

本方案不规定真实签名密钥的部署、企业级审批 UI、Everything 私有接口或完整 Windows 服务安装包。fake vertical slice 只验证 hash、nonce、plan binding、事务顺序和旧 active 保留；它不能证明真实 UAC、ACL、named pipe impersonation 或 Ed25519 密钥保护已经完成。

## 10. 验收门槛

进入真实 Windows Broker 实现前，必须通过：

1. 每份 plan/approval/transaction fixture 的 schema 校验；
2. plan hash 字段篡改、artifact 替换、token 重放、policy revision 改变的拒绝测试；
3. `ACTIVE_BOUND` 前后每个边界的进程终止恢复测试；
4. 普通用户无法写 R、无法改变 machine PATH 的 L2 测试；
5. root relocate、ACL 漂移、签名失效和 schema migration 的 recovery 测试。
