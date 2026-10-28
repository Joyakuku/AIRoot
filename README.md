# AIRoot

AIRoot 是面向 Windows 的本地环境与开发工具登记、发现和维护命令行工具。Rust 核心通过当前用户专用 IPC 后台进程协调操作，并使用 SQLite 保存登记、项目关联、扫描进度与恢复信息。

**当前版本：0.1.1 preview。** 完整首版认证仍有 Windows 验收门槛；请参阅[脱敏验收摘要](docs/release-acceptance-summary.md)。

## 构建与测试

需要 Windows x64、Rust 1.85+ MSVC 工具链；组件下载和打包需要 PowerShell。锁定组件清单、来源及许可证说明见 `packaging/`。

```powershell
cargo fmt --all -- --check
cargo test --locked --all-targets
cargo clippy --locked --all-targets -- -D warnings
node --test integrations/deepseek-harness/test/*.test.mjs
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

`integrations/deepseek-harness/` 提供 Harness 插件和 `skills/airoot/SKILL.md`。在 DeepSeek Harness 添加 `https://github.com/Joyakuku/AIRoot` 即安装插件；包内已包含 AIRoot 与扫描组件运行时，不执行安装期构建脚本，也不要求用户调整 pnpm `allowBuilds`。支持的 Harness 版本和运行要求见集成说明。插件调用遵循 Harness sandbox 与 escalation 接口；后台模式要求有效的 `danger-full-access`。

插件测试命令同上；GitHub 安装包不包含测试源码。

## 许可

AIRoot 自有代码采用 MIT，版权归 Joyakuku，见 [LICENSE](LICENSE)。第三方组件和依赖分别遵循其许可，随插件包附带对应通知。
