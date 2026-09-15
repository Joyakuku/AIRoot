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

## 记忆（`.ai/tooling.json`）

它记录"上次用户选了什么"，**不是授权凭据**。当前版本**只读**：连 AIROOT 自己都不写它
（写入属于 P2 的人工批准通道）。记忆只在同一项目 + 同一 capability + 同一清单指纹下有效；
清单变了，记忆即失效，问题会重新问一次——**陈旧答案比没有答案更糟**。

## 与权限提升的关系

选 `data-root` 之后并不等于拿到写权限：P1 是 `security_mode=policy_only`，
machine 级写入（例如 `env persist --scope machine`）会明确返回 `PRIVILEGE_REQUIRED`（退出码 5）。
**不要**建议用户手工改注册表来绕过——那正是这套确认机制要防的事。
