# 常用命令

插件 `airoot_command` 的 args 是参数数组，不含可执行文件、`--json` 和 `--data-root`。返回原始 API v1 envelope；先检查 ok，再读取 data。出错时保留 error。`resource run` 的 `ok=true` 只表示 AIRoot 调用成功；按所执行命令的退出码语义，结合 `data.exit_code`、`data.stdout` 和 `data.stderr` 判定子程序实际结果。另查 `data.lease.state`；非 `released` 时不能宣称使用保护已解除，应继续核实进程及后代。

| 目的 | args 示例 |
| --- | --- |
| 状态与能力 | `["status"]`、`["capabilities"]` |
| 环境与工具 | `["env","list"]`、`["tool","show","ID"]` |
| 项目资源 | `["project","show","audio"]` |
| 有范围补查 | `["resource","find","python","--root","D:\\env"]` |
| 执行维护 | `["operation","apply","ID","--confirm","已核实的条件"]` |
| 查询恢复 | `["operation","show","ID"]`、`["operation","recover","ID"]` |
| 运行支持的本机 CLI / 解释器 / 脚本 | `["resource","run","ID","--project","audio","--","--version"]` |
| 扫描控制 | `["scan","start"]`、`["scan","pause","ID"]`、`["scan","resume","ID"]` |
| 补登记 | `["registry","add","--path","D:\\tools\\ffmpeg.exe","--project","audio"]` |
| 日志 | `["log","show","操作ID"]` |

新建环境使用 `airoot_plan`：

```json
{"request":{"action":"create","kind":"venv","name":"audio","project":"audio","manager":"D:\\env\\python-base\\python.exe"}}
```

为已有环境维护依赖：action `install-packages` / `upgrade-packages`，resource_id 指向具体已登记环境，packages 为需求列表；先查 capabilities 是否支持，不猜测命令动作。

计划保存在 `.airoot/agent/requests` 中，命令读取后清理临时请求文件。operation plan 及 apply 的记录仍由 AIRoot 保存。原生 CLI 的完整参数可通过 `<airoot.exe> <命令组> --help` 查询；AIRoot 产品文档提供供应商安装、WSL、Docker 以及 MCP 配方。

AIRoot 后台不在命令沙箱内，插件在 read-only / workspace-write 下拒绝全部后台调用（查询也可能启动恢复）。在本 Harness 集成中，插件及明确部署的 CLI 模式调用后台前，都必须核实当前 Harness 会话已具备或通过正规审批取得 danger-full-access；策略未知、无法核实或审批未获准时停止并报告，不得切换工具或调用方式绕过。已有 Full Access 不重复询问。插件的 `sandbox_permissions` 与 `justification` 交由共享 Harness 审批机制处理，不能替代删除目标或重启确认。该权限前提由 Agent / Harness 核实，不要求 AIRoot 核心检测 Full Access，也不改变原生 CLI 的全局产品设计。命令超时后先查询操作状态，避免重复安装。

已安装 CLI 无法仅凭路径识别时，使用 `airoot_register`，不要伪造通用软件身份：

```json
{"resource":{"class":"tool","kind":"cli","name":"fd","path":"D:\\tools\\fd\\fd.exe","entry":"D:\\tools\\fd\\fd.exe","projects":["audio"]}}
```

登记后调用 `registry verify ID`；对支持的本机 CLI、解释器和脚本，再通过 `resource run` 核实实际入口，未验证不等同可用。MCP 连接器不支持 `resource run` 的交互式 stdin 转发，使用当前已有的交互式 stdio 客户端，并通过 `usage acquire` 登记实际 PID，结束后核实进程及后代，再 `usage release --descendants-checked`；登记或验证成功不代表已连接当前 Harness 的 MCP 客户端。`airoot_register` 不提供任意安装命令。


扫描返回的 `coverage.queue_complete` 仅表示队列处理结束；同时检查 `coverage.state`、`uncovered`、`skipped` 和 `providers`。来源截断、超时、未就绪、访问失败或链接排除时，只报告已发现资源与明确缺口。

`registry restore ID` 仅在原宿主实例身份、类型、入口、版本及可用性核实成功后恢复历史登记。缺少身份凭证的旧忽略记录、已替换实例或不可用资源保持忽略，报告具体原因，不手改数据库或换命令绕过。历史项目关联不代表当前使用关系。
