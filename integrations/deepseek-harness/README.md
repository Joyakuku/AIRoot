# AIRoot for DeepSeek Harness

Windows plugin bundle and Agent skill for AIRoot 0.1.1 preview. The package supports DeepSeek Harness `0.2.1-rc.2` through `0.2.1-rc.3` with Node 24 and PowerShell.

## Install from GitHub

In DeepSeek Harness, add `https://github.com/Joyakuku/AIRoot` as a plugin repository. The repository root contains the package manifest and the complete, hash-pinned Windows runtime. Installation does not execute package build scripts and does not require Rust, Cargo, a local Visual C++ runtime source, or a pnpm `allowBuilds` change.

For a local checkout, add the repository root as the plugin directory. To inspect the exact package payload, run `npm pack --dry-run` from the repository root.

## Licensing

The `MIT` package metadata and root `LICENSE` cover AIRoot-owned code only. Bundled executables and their dependencies remain under their respective licenses; notices are included under `integrations/deepseek-harness/runtime/components/licenses` and `integrations/deepseek-harness/runtime/licenses`. The PET executable is a static-CRT build of Microsoft's `python-environment-tools` project. Its binary hash is pinned in the runtime manifest; the source commit used to produce this existing binary was not recorded, so this package does not claim a reproducible source-to-binary build.

## Runtime behavior

The bundled executable is used by default. An absolute `executable` plugin config value or `AIROOT_EXE` environment variable can override it for development. The skill is bundled under `integrations/deepseek-harness/skills/airoot`; configure the Harness skill filesystem to load the installed package's `integrations/deepseek-harness/skills` directory. Adding the plugin alone does not guarantee that a separate skill loader has been configured. Optional `dataRoot` isolates tests; `timeoutMs` configures command limits.

The three tools are `airoot_command`, `airoot_plan`, and `airoot_register`. The bridge accepts the native Harness `ShellRunResult` without a `kind` field and the `dsh-ops` `ops_bash` foreground envelope. It rejects background, failed, truncated, aborted, or sandbox-denied results.

All calls go through Harness's public confining Shell service and shared escalation flow. Because AIRoot's daemon runs outside the child shell, the effective mode must be `danger-full-access`, including for queries that can start daemon recovery. AIRoot does not decide whether Full Access is enabled. Deletion targets and restarts still require the Agent to obtain explicit user confirmation.

## Validation

From the repository root, run `node --test integrations/deepseek-harness/test/*.test.mjs`. Test sources are kept in the repository but excluded from the install package. Runtime component hashes are checked by AIRoot against the bundled manifest. The package remains preview and does not imply complete AIRoot first-release certification.
