# 确认与批准（按需参考）

## 什么时候必须问，什么时候**不能**问

判据只有一条（规划 §12.1）：**这个动作会不会往运行时装包、创建环境、下大体积**。

| 情形 | 判定 | 动作 |
|---|---|---|
| 被 `pyproject.toml` / `requirements*.txt` / `package.json` / lock 引用 | 属于项目 | **项目内隔离，不询问** |
| 单文件通用 CLI（jq / rg / ffmpeg / 7z） | 属于机器 | **装到数据根，不询问** |
| 往运行时装包 / 创建环境 / 体积超阈值 / CUDA 或非 Python 二进制 | 高风险 | **必须确认** |
| 来源或完整性不可验证 | 不可信 | **只允许 reference**，禁止 import/recreate |
| 对象没有已冻结的能力 | 越界 | 只报告 `unmanaged`，不接管 |

**为什么"简单的必须不问"**：确认退化成噪音之后，用户会在两周内习惯性点同意，
那时高风险确认也一起失效。所以判据的严边界只划在那三类动作上——这是本协议最重要的设计约束。

## 三选一（不得增删、不得改名）

```text
project-isolated   只装进这个项目
data-root          装进数据根，机器可见（权限提升）
cancel             取消
```

- `data-root` 是**权限提升**：项目目录里的 manifest 不能自己升级自己。显式请求它而策略结论是
  project 时，`plan` 返回 `SCOPE_UPGRADE_REQUIRES_APPROVAL`（退出码 4），**不落盘任何计划文件**。
- 反方向（自己收窄到项目内）不需要批准。

## 批准的形状

```text
1. airoot plan <cap> --scope … --target … --dry-run --json    # 先拿真实体积，拿不到就说未知
2. airoot plan <cap> --scope … --target … --json              # 生成 canonical plan（含 metadata.routing）
3. 人工批准 plan_hash（approval token）
4. airoot install <plan-file> --token-file <token>            # 提交事务
```

- `plan_hash` 覆盖**路由块**：同一 capability 指向两个 target 会得到两个 hash，
  批准不能复用。改一个字（路径、scope、digest）都必须重新批准。
- 需要确认时 `plan` **不写计划文件**——"还没决定"必须在文件系统上也可证明。
- `airoot approve` 只消费批准。你不能把"我调用了 approve"解释成"用户批准了"。

### 第 3、4 步**自 ADR-0046 起可用**——但"签发"是一个显式步骤

第 1、2 步（`plan --dry-run` / `plan`）今天就能跑，且需要确认时**不写任何文件**。

**ADR-0046 之前**，第 3、4 步没有可用实现：核心只做校验（§113 起两种算法都真的在验，`ed25519` 是
RFC 8032），唯一实现过的签发方是测试用的 `cli/tests/fake_issuer.py`，而 `state/test-keyring.json`
是**测试**密钥——它就写在 root 里，任何能写这个 root 的进程都能签，所以**不能**当生产签发方用。
**这句话今天仍然是对那条路的正确评价，只是它不再是全部**：裁定见下。

**自 ADR-0046 起这条路是通的，而自 ADR-0049 起它有名字**——先在本 root 里**签一次**：
`airoot issue <plan.json> --out <token.json> --provision --json`（`tx/issuer.py` 的 `provision` 生成密钥对、
`issue` 用 `ed25519` 签出一份 `approval-token`），再由 `install --token-file` 等命令消费。它是**显式
的本地步骤**：核心不会把签发当成任何别的事情的副作用，而**已有密钥时 `--provision` 拒绝覆盖**。
keyring 的每一条是**记录**（`algorithm` + 材料，§113 /
ADR-0039），所以同一个文件里放的是**公钥**，私钥留在 `state/issuer-key.json`。
**没有 keyring 的 root 仍然会拒绝**，消息里带这一句：

```text
no approval keyring is installed in this root; provision a signing key first (decided: ADR-0046 — approval is an audit record, so the signer is a local, explicit step)
```

所以：

- **不要**把批准说成"授权证明"：它是**账本**。私钥在 root 里、同用户进程读得到也能自己签，
  所以验签通过只说明"这份 plan 由这个 root 信任的钥匙签过、且此后没被改动或重放"——
  **一致性**，不是**权限**（ADR-0045/ADR-0046）。要挡同用户进程属于使用方（上游 harness）的职责；

- **不要**试图绕开签发方自己拼一份 token。消费侧要拒绝的正是伪造的 token；要签就走 `airoot issue`
  （实现仍在 `tx/issuer.py`），那条路是**显式**的（先 `provision`，再 `issue`，而且它在输出文档里
  写着 `permission_proof: false`），所以"我调用了什么"这件事在账上看得见；
- 这条路的裁决写在 `docs/AIROOT-v0.3-实现决策记录.md` 的 **ADR-0046**（状态：**已裁决：B——本机签发，
  动词由 **ADR-0049** 补上，
  批准 = 账本**），它推翻了草案 §113.6 第 6 条；被它取代的两条更早的裁决是 ADR-0025 的 D1 与 ADR-0044，
  两者仍然可读，且**实测读数全部仍然成立**（变的是"这算不算缺陷"）。

## 记忆（`.ai/tooling.json`）

它记录"上次用户选了什么"，**不是授权凭据**。当前版本**只读**：连 AIROOT 自己都不写它
（写入属于 P2 的人工批准通道）。记忆只在同一项目 + 同一 capability + 同一清单指纹下有效；
清单变了，记忆即失效，问题会重新问一次——**陈旧答案比没有答案更糟**。

## 与权限提升的关系

选 `data-root` 之后并不等于拿到写权限：P1 是 `security_mode=policy_only`，
machine 级写入（例如 `env persist --scope machine`）会明确返回 `PRIVILEGE_REQUIRED`（退出码 5）。
**不要**建议用户手工改注册表来绕过——那正是这套确认机制要防的事。
