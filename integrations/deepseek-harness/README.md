# AIRoot for DeepSeek Harness

Windows plugin bundle and Agent skill for AIRoot 0.1.1 preview. The package supports DeepSeek Harness `0.2.1-rc.2` through `0.2.1-rc.3` with Node 24 and PowerShell.

## Install from GitHub

In DeepSeek Harness, add `https://github.com/Joyakuku/AIRoot` as a plugin repository. The repository root contains the Harness package manifest and points to this integration. The install preparation builds AIRoot and assembles the bundled runtime.

This source install requires the Windows Rust MSVC toolchain. The pinned component set includes PET, whose Microsoft Visual C++ runtime is not checked into GitHub. If it is not already present in the generated component bundle, supply a licensed Visual Studio redist file through `AIROOT_VCRUNTIME_SOURCE` before installation. The script verifies component hashes and does not replace an existing invalid runtime bundle. Harness may require explicit approval to run the package build script.

Therefore, adding the GitHub repository is supported, but a clean machine is not yet a zero-prerequisite installation. No prebuilt executable or third-party runtime binary is distributed in this source repository.

For a local checkout, add the repository root as the plugin directory. To build an archive manually, run `npm pack` from the repository root and add the resulting archive to the Harness profile.

## Runtime behavior

The bundled executable is used by default. An absolute `executable` plugin config value or `AIROOT_EXE` environment variable can override it for development. The skill is bundled under `integrations/deepseek-harness/skills/airoot`; configure the Harness skill filesystem to load the installed package's `integrations/deepseek-harness/skills` directory. Adding the plugin alone does not guarantee that a separate skill loader has been configured. Optional `dataRoot` isolates tests; `timeoutMs` configures command limits.

The three tools are `airoot_command`, `airoot_plan`, and `airoot_register`. The bridge accepts the native Harness `ShellRunResult` without a `kind` field and the `dsh-ops` `ops_bash` foreground envelope. It rejects background, failed, truncated, aborted, or sandbox-denied results.

All calls go through Harness's public confining Shell service and shared escalation flow. Because AIRoot's daemon runs outside the child shell, the effective mode must be `danger-full-access`, including for queries that can start daemon recovery. AIRoot does not decide whether Full Access is enabled. Deletion targets and restarts still require the Agent to obtain explicit user confirmation.

## Validation

Run `npm test` from the repository root or this directory. The package remains preview and does not imply complete AIRoot first-release certification.
