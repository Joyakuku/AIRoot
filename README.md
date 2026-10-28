# AIRoot

AIRoot 是面向 Windows 的本地环境与开发工具登记、发现和维护命令行工具。Rust 核心通过当前用户专用 IPC 后台进程协调操作，并使用 SQLite 保存登记、项目关联、扫描进度与恢复信息。

**当前版本：0.1.1 preview。** 本仓库提供源码、构建脚本、DeepSeek Harness 集成和 Agent SKILL。完整首版仍有未完成的 Windows 与发布许可门槛；请参阅 [脱敏验收摘要](docs/release-acceptance-summary.md)。该摘要只说明已执行的验证，不构成产品认证或再分发授权。

## 构建与测试

需要 Windows x64、Rust 1.85+ MSVC 工具链；组件下载和打包需要 PowerShell。锁定组件清单、来源及许可证说明见 `packaging/`。

```powershell
cargo fmt --all -- --check
cargo test --locked --all-targets
cargo clippy --locked --all-targets -- -D warnings
Push-Location integrations/deepseek-harness
npm test
Pop-Location
```

准备锁定的搜索组件并构建离线预览安装包：

```powershell
powershell -File scripts/fetch-components.ps1
powershell -File scripts/package.ps1
```

安装包及其包含的第三方组件受各自许可证约束。特别是 Visual C++ Runtime 的公开再分发资格尚待发布者核实；未经确认，不应将安装包作为公开二进制发布。

## 命令示例

```powershell
airoot --json status
airoot --json env list
airoot --json tool list
airoot project show <项目名>
airoot scan start
airoot scan status <扫描ID>
airoot operation show <操作ID>
```

安装位置由用户指定；AIRoot 自有信息默认保存在 `%USERPROFILE%\.airoot`，环境和工具保存在配置的正常资源目录。命令和维护配方详见 [docs/命令与配方.md](docs/命令与配方.md)。

## Harness 集成

`integrations/deepseek-harness/` 提供 Harness 插件和 `skills/airoot/SKILL.md`。插件调用会遵循 Harness 的 sandbox 与 escalation 接口；后台模式当前要求有效的 `danger-full-access`。集成测试：

```powershell
Push-Location integrations/deepseek-harness
npm test
Pop-Location
```

## 许可

AIRoot 自有代码采用 MIT，版权归 Joyakuku，见 [LICENSE](LICENSE)。第三方组件和依赖不包含在 AIRoot 自有代码的 MIT 授权内；具体通知和许可文件见 `packaging/licenses/`。
