---
name: airoot
description: 在 Windows 上通过 AIRoot 查找、使用、安装、维护本机环境和工具，并关联项目。适用于 conda、venv、运行时、CLI、便携工具、Blender 和已安装 MCP 连接器的资源管理。
---

# AIRoot 本机资源管理

AIRoot 保存两张逻辑登记表，封装发现和维护命令。优先使用 `airoot_command`、`airoot_plan`、`airoot_register`。仅当部署明确采用 CLI 模式、未遭插件或权限拒绝且满足下述 Harness 后台访问权限前提时，才使用配置的绝对 `airoot.exe` 路径及 `--json`。在插件集成测试中，插件未加载必须停止报告，不能自动改用 CLI。命令形式见 [references/commands.md](references/commands.md)。

## 查找与使用

先查 `status`、`env list` / `tool list` / `project show <项目>`，按具体实例的路径、版本、项目和可用状态选择资源。同名工具可能有多个副本。没有匹配时用 `resource find <关键词>` 做有范围、有时限的补查，不能把未覆盖或未知解释为全机不存在。

对支持的本机 CLI、解释器和脚本，优先用 `resource run <资源ID> --project <项目> -- <参数>`，保留使用保护与项目记录。对已登记且支持 `resource run` 的解释器，版本、路径或可用性探测（如 `--version`、`-c`）也须通过 `resource run` 执行，插件模式使用 `airoot_command`；不得把“只读探测”作为绕过使用保护的例外。`ok=true` 不等于子程序成功；结合 `data.exit_code` 和输出判定实际结果，并检查 `data.lease.state`，未释放时继续核实占用。MCP 连接器使用当前已有的交互式 stdio 客户端，不通过 `resource run` 启动。外部启动程序时用 `usage acquire` 登记实际 PID，结束后核实进程及后代再 `usage release --descendants-checked`。不要凭超时释放保护。

`registry add --path` 不能核实 CLI 身份时，用 `airoot_register` 提交显式资源（class=tool、kind、name、绝对 path/entry、projects），再 `registry verify` 及受保护运行；不要猜测未支持的 CLI 参数。

`registry restore <ID>` 先核对原宿主资源的文件系统实例身份，再由对应适配器核实类型、入口、版本和可用性；成功后才恢复登记与历史关联。旧忽略记录缺少身份凭证、实例已替换或资源不可用时，保留忽略并报告阻塞，不绕过检查或手改数据库。旧项目关联仅是历史信息，不能据此声称项目当前仍在使用该实例。

只登记可供 Agent 使用的环境、工具、可复用脚本和已安装可用的 MCP 连接器。聊天、办公等普通软件不作为工具。连接器登记不等于已经连通当前 Harness 的 MCP 客户端。

## 安装与维护

需要新环境时，以当前任务简称命名，必须记录对应项目。先查已有基础解释器和 `capabilities`。兼容性风险不明时创建独立环境，不试改旧环境。新装工具、环境保存在用户正常的 env/tools 位置；AIRoot 信息留在用户 `.airoot`。根目录未配置或不存在时，先单独询问初始化位置及是否创建。

用 `airoot_plan` 准备方案，核对对象、实际目标路径、来源、版本、影响、风险及 requirements，然后单独 `operation apply <ID>`。计划不是执行结果。异步维护后持续查询 `operation show <ID>`，分别核实执行、验证、登记及恢复/清理状态。实质条件变化时重新规划。

权限由当前 Agent / Harness 管理，AIRoot 不识别 Full Access。普通权限下按 Harness 拒绝信息对具体调用请求提权；Full Access 下已获授权的普通安装无需额外确认。插件使用 Harness 公共 Shell 服务、当前会话沙箱策略和共享提权审批流程。AIRoot 独立后台不受命令子进程沙箱约束。在本 Harness 集成中，插件及明确部署的 CLI 模式调用后台前，都必须核实当前 Harness 会话已具备或通过正规审批取得 danger-full-access；查询也遵守此前提，因为启动后台可能触发恢复。这是当前 Harness 集成的后台访问限制，不是“只在安装时提权”，也不代表 AIRoot 已把权限隔离到单个资源或维护动作；权限未知、无法核实、审批未获准或用户拒绝提权时停止该次调用并报告，不能改用直接进程、其他工具或伪造确认绕过限制。此前提由 Agent / Harness 核实，不要求 AIRoot 核心检测 Full Access，也不改变原生 CLI 的全局产品设计。`--confirm` 是 Agent 对已满足条件的断言，不能独立证明人类授权；不能把返回的 requirements 原样复制就宣称全部已满足。

删除始终单独询问：先列出候选环境/工具、各自路径和对应项目，让用户明确选择一个或多个目标；任务完成、Full Access 或一般性允许维护均不等于同意删除。告知计划的连带影响后，仅针对本次已明确确认的实例传递相应断言。重启也必须另外明确确认。WSL/容器有项目数据或无法排除时，先满足备份/迁出条件；不自动删除数据卷或挂载目录。

## 失败与收尾

新装确定失败且残留归属本次操作时，AIRoot 可自动清理，不另行询问。核实超时、进程仍存活或归属不清时保留并报告。已有环境依赖修改失败时报告实际变化，不删整个环境、不猜测回退版本。

安装成功但登记失败时保留资源，用 `operation recover <ID>` 补登记；不重新安装。`operation cleanup <ID>` 只处理该失败操作的残留，不接受任意路径、不替代正常删除。恢复不能重放旧安装动作。查询原始错误、独立 recovery / cleanup 状态及阻塞原因后再决定下一步。

扫描暂停后必须显式继续，崩溃后也不擅自恢复扫描。`coverage.queue_complete=true` 只表示待处理队列已结束，不代表覆盖完整；同时检查 `coverage.state`、`uncovered`、`skipped` 及各 provider 状态。索引截断、超时、未就绪、无权限或跳过链接目录时，只报告已发现结果和缺口，不宣称找齐全机所有工具或“全机没有”。

任务完成可提示本次新增环境及对应项目，供用户选择是否另行清理；不主动删除。报告真实完成项和明确缺口，不把模拟、条件断言或插件加载成功当成真实权限联调通过。

真实模型联调结论只适用于报告中绑定的插件包、SKILL 哈希、Harness 版本和测试场景。仓库版 SKILL 后续有修改时，旧版对话记录不能作为当前文本已通过联调的证据；应先检查包与 SKILL 哈希，再重跑适用场景。不得将密钥写入 SKILL、profile、transcript 或验收报告。
