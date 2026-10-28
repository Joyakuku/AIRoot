use crate::{model::*, platform, store::Store};
use anyhow::{Context, Result};
use serde_json::json;
/// Collect all fallible boot evidence before recording an external action as started.
/// A failed probe must leave prior vendor state intact so recovery can retry safely.
pub(crate) fn record_vendor_start(vendor: &mut serde_json::Value, phase: &str) -> Result<()> {
    record_vendor_start_with(vendor, phase, || {
        Ok((platform::uptime_millis()?, platform::boot_stamp()?))
    })
}
fn record_vendor_start_with(
    vendor: &mut serde_json::Value,
    phase: &str,
    probe: impl FnOnce() -> Result<(u64, u64)>,
) -> Result<()> {
    anyhow::ensure!(
        matches!(phase, "installer" | "uninstall"),
        "Unknown vendor phase"
    );
    let (uptime, boot) = probe()?;
    vendor[format!("{phase}_started")] = json!(true);
    vendor[format!("{phase}_started_uptime_ms")] = json!(uptime);
    vendor[format!("{phase}_started_boot_stamp")] = json!(boot);
    Ok(())
}
/// Fixed per-user recipes only. No caller-supplied installer arguments are executed.
pub fn plan(s: &Store, mut input: PlanInput) -> Result<Operation> {
    let kind = input
        .kind
        .clone()
        .context("Bootstrap requires python or conda")?;
    let source = input
        .source
        .as_deref()
        .context("Official installer URL required")?;
    anyhow::ensure!(
        match kind.as_str() {
            "python" =>
                source.starts_with("https://www.python.org/ftp/python/")
                    && source.ends_with("-amd64.exe"),
            "conda" =>
                source.starts_with("https://github.com/conda-forge/miniforge/releases/download/")
                    && source.ends_with("Windows-x86_64.exe"),
            _ => false,
        },
        "Bootstrap supports official Windows x64 Python or Miniforge installers with pinned SHA256"
    );
    input.action = "install-portable".into();
    let mut op = crate::portable::prepare(s, input)?;
    op.input.action = "bootstrap".into();
    op.resource.metadata["recipe"] = json!("bootstrap-installer");
    op.risk = "high".into();
    let target = &op.resource.path;
    let archive = op.input.archive.as_mut().context("Installer missing")?;
    if op.resource.metadata["download_owned"] == true {
        let old = archive.to_string_lossy().into_owned();
        *archive = archive.with_extension("exe");
        for step in &mut op.steps {
            for arg in &mut step.args {
                if *arg == old {
                    *arg = archive.to_string_lossy().into();
                }
            }
        }
        op.resource.metadata["archive"] = json!(archive);
    }
    let args = if kind == "python" {
        vec![
            "/quiet".into(),
            "/norestart".into(),
            "InstallAllUsers=0".into(),
            format!("TargetDir={target}"),
            "PrependPath=0".into(),
            "AssociateFiles=0".into(),
            "Shortcuts=0".into(),
            "Include_launcher=0".into(),
            "Include_test=0".into(),
            "Include_doc=0".into(),
            "Include_pip=1".into(),
            "/log".into(),
            s.root
                .join("logs")
                .join(format!("{}-installer.log", op.id))
                .to_string_lossy()
                .into(),
        ]
    } else {
        vec![
            "/InstallationType=JustMe".into(),
            "/RegisterPython=0".into(),
            "/AddToPath=0".into(),
            "/S".into(),
            format!("/D={target}"),
        ]
    };
    op.resource.entry = Some(
        std::path::Path::new(target)
            .join("python.exe")
            .to_string_lossy()
            .into(),
    );
    if kind == "conda" {
        op.resource.metadata["manager"] =
            json!(std::path::Path::new(target).join("Scripts/conda.exe"));
    }
    op.effects.retain(|e| e.starts_with("Executable SHA256"));
    op.effects.push(format!("Install a new per-user {kind} base at {target}; installer at {}; no PATH, launcher, shortcuts or default associations",archive.display()));
    op.effects.push("Installer and operating-system owned installation records/caches may be maintained in their normal locations. AIRoot logs stay under its information root. Download size/duration unknown. No automatic reboot. Bootstrap roots require their vendor uninstaller; direct folder deletion is not offered.".into());
    op.effects.push(format!(
        "Executable SHA256 {} {}",
        archive.display(),
        op.input
            .sha256
            .as_deref()
            .context("Missing SHA256")?
            .to_lowercase()
    ));
    op.steps.push(Step {
        program: archive.to_string_lossy().into(),
        args,
        environment: Default::default(),
    });
    if archive.exists() {
        anyhow::ensure!(
            platform::hash_file(archive)?.eq_ignore_ascii_case(op.input.sha256.as_deref().unwrap()),
            "Installer SHA256 mismatch"
        );
    }
    s.save("operations", &op.id, &op)?;
    Ok(op)
}
/// Snapshot vendor-owned per-user installation records; executed only for bootstrap recipes.
fn vendor_records(kind: &str) -> Result<serde_json::Value> {
    let script = r#"$ErrorActionPreference='Stop'; $rows=@(); foreach($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) { $rows+=@(Get-ItemProperty $root -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'Python|Miniforge|Mambaforge' } | Select-Object PSPath,DisplayName,InstallLocation,UninstallString) }; ConvertTo-Json -InputObject @($rows) -Compress"#;
    let out = platform::run(
        "powershell.exe",
        &[
            "-NoProfile".into(),
            "-NonInteractive".into(),
            "-Command".into(),
            script.into(),
        ],
        std::time::Duration::from_secs(30),
        |_| Ok(()),
    )?;
    anyhow::ensure!(
        out.code == 0 && !out.output_truncated && !out.descendants_active,
        "Vendor installation records unavailable"
    );
    let rows: Vec<serde_json::Value> =
        serde_json::from_str(out.stdout.trim_start_matches('\u{feff}'))?;
    Ok(json!(
        rows.into_iter()
            .filter(|r| {
                let name = r["DisplayName"].as_str().unwrap_or("").to_lowercase();
                if kind == "python" {
                    name.contains("python")
                } else {
                    name.contains("miniforge") || name.contains("mambaforge")
                }
            })
            .collect::<Vec<_>>()
    ))
}
pub fn before_install(shared: &crate::ipc::Shared, op: &mut Operation) -> Result<()> {
    let before = vendor_records(&op.resource.kind)?;
    // Vendor uninstallers can address shared product identities. Never trial them over existing installations.
    anyhow::ensure!(
        before.as_array().is_some_and(|a| a.is_empty()),
        "Existing vendor installation records: shared installer identity cannot be excluded; use an independent portable runtime or external preparation"
    );
    op.reliability.vendor =
        json!({"before":before,"installer_started":false,"uninstall_completed":false});
    crate::recovery::persist(shared, op)
}
pub fn capture_after(shared: &crate::ipc::Shared, op: &mut Operation) -> Result<()> {
    let after = vendor_records(&op.resource.kind)?;
    op.reliability.vendor["after"] = after;
    if op.resource.kind == "python" {
        op.reliability.vendor["python_roots"] = python_installation_roots()?;
    }
    if op.resource.kind == "conda" {
        let path = std::path::Path::new(&op.resource.path).join("Uninstall-Miniforge3.exe");
        if path.is_file() && !platform::is_link(&path)? {
            op.reliability.vendor["uninstaller"] =
                json!({"path":path,"sha256":platform::hash_file(&path)?});
        }
    }
    crate::recovery::persist(shared, op)
}
pub fn cleanup_vendor(shared: &crate::ipc::Shared, op: &mut Operation) -> Result<()> {
    use std::{path::Path, time::Duration};
    if op.reliability.vendor["installer_started"] != true {
        return Ok(());
    }
    anyhow::ensure!(
        op.reliability.vendor["before"]
            .as_array()
            .is_some_and(|a| a.is_empty()),
        "Vendor ownership baseline missing or shared"
    );
    let owner = op
        .reliability
        .owned
        .iter()
        .find(|x| x.purpose == "target")
        .context("Vendor target evidence missing")?;
    verify_vendor_target(owner)?;
    let current = vendor_records(&op.resource.kind)?;
    validate_vendor_records(op, &current)?;
    let uptime = if op.resource.metadata["reboot_required"] == true {
        Some(platform::uptime_millis()?)
    } else {
        None
    };
    let boot = if op.resource.metadata["reboot_required"] == true {
        Some(platform::boot_stamp()?)
    } else {
        None
    };
    if reconcile_vendor_uninstall(op, &current, uptime, boot)? {
        crate::recovery::persist(shared, op)?;
        return Ok(());
    }
    if op.reliability.vendor["uninstall_started"] != true {
        validate_vendor_target(op)?;
    }
    let attempts = op.reliability.vendor["uninstall_attempts"]
        .as_u64()
        .unwrap_or(0);
    anyhow::ensure!(attempts < 3, "Vendor uninstaller retry limit reached");
    let (program, args) = if op.resource.kind == "python" {
        let installer = op
            .input
            .archive
            .as_ref()
            .context("Pinned installer unavailable")?;
        anyhow::ensure!(
            platform::hash_file(installer)?.eq_ignore_ascii_case(
                op.input
                    .sha256
                    .as_deref()
                    .context("Installer hash missing")?
            ),
            "Installer changed"
        );
        (
            installer.clone(),
            vec!["/uninstall".into(), "/quiet".into(), "/norestart".into()],
        )
    } else {
        let u = &op.reliability.vendor["uninstaller"];
        let path = std::path::PathBuf::from(
            u["path"]
                .as_str()
                .context("Official Miniforge uninstaller missing")?,
        );
        anyhow::ensure!(
            path.parent() == Some(Path::new(&op.resource.path))
                && !platform::is_link(&path)?
                && platform::hash_file(&path)?
                    == u["sha256"].as_str().context("Uninstaller hash missing")?,
            "Uninstaller identity changed"
        );
        (
            path,
            vec![
                "/S".into(),
                "/RemoveCaches=0".into(),
                "/RemoveConfigFiles=none".into(),
                "/RemoveUserData=0".into(),
            ],
        )
    };
    let lease_id = op
        .reliability
        .lease_id
        .clone()
        .context("Cleanup lease missing")?;
    record_vendor_start(&mut op.reliability.vendor, "uninstall")?;
    op.reliability.vendor["uninstall_attempts"] = json!(attempts + 1);
    op.reliability.vendor["uninstall_attempt_state"] = json!("running");
    crate::recovery::persist(shared, op)?;
    let out = platform::run_wait_tree(
        &program.to_string_lossy(),
        &args,
        Duration::from_secs(600),
        |pid| {
            let s = shared.lock().unwrap();
            let mut l = s
                .leases()?
                .into_iter()
                .find(|l| l.id == lease_id)
                .context("Lease missing")?;
            l.pid = Some(pid);
            l.process_stamp = platform::process_stamp(pid)?;
            l.process_job = Some(platform::process_job_name(pid)?);
            l.state = "running".into();
            s.save_lease(&l)
        },
    )?;
    op.reliability.vendor["uninstall_attempt_state"] = json!("returned");
    op.reliability.vendor["uninstall_exit_code"] = json!(out.code);
    if out.code == 3010 {
        op.resource.metadata["reboot_required"] = json!(true);
        op.reliability.vendor["reboot_uptime_ms"] =
            op.reliability.vendor["uninstall_started_uptime_ms"].clone();
    }
    crate::recovery::persist(shared, op)?;
    anyhow::ensure!(
        !out.descendants_active,
        "Vendor child process remains; usage protection retained"
    );
    {
        let s = shared.lock().unwrap();
        let mut l = s
            .leases()?
            .into_iter()
            .find(|l| l.id == lease_id)
            .context("Lease missing")?;
        l.pid = None;
        l.process_stamp = None;
        l.state = "reserved".into();
        s.save_lease(&l)?;
    }
    if out.code == 3010 {
        anyhow::bail!("Vendor cleanup requires restart; AIRoot will not restart automatically");
    }
    anyhow::ensure!(
        out.code == 0,
        "Vendor uninstaller failed with {}; residue retained",
        out.code
    );
    anyhow::ensure!(
        vendor_records(&op.resource.kind)? == op.reliability.vendor["before"],
        "Vendor installation records remain; cleanup incomplete"
    );
    op.reliability.vendor["uninstall_completed"] = json!(true);
    crate::recovery::persist(shared, op)
}

/// Reconcile a persisted vendor-uninstall attempt without replaying an uncertain action.
/// Returns true only when the vendor phase is positively complete.
pub fn reconcile_vendor_uninstall(
    op: &mut Operation,
    current: &serde_json::Value,
    uptime_ms: Option<u64>,
    boot_stamp: Option<u64>,
) -> Result<bool> {
    let before = op.reliability.vendor["before"].clone();
    let after = op.reliability.vendor["after"].clone();

    if op.reliability.vendor["uninstall_completed"] == true {
        return Ok(true);
    }

    if op.resource.metadata["reboot_required"] == true {
        let uninstall = op.reliability.vendor["uninstall_started"] == true;
        if uninstall {
            anyhow::ensure!(
                op.reliability.vendor["uninstall_attempt_state"] == "returned"
                    && op.reliability.vendor["uninstall_exit_code"] == 3010,
                "Restart marker lacks a persisted successful uninstall result; residue retained"
            );
        } else {
            anyhow::ensure!(
                op.reliability.vendor["installer_exit_code"] == 3010,
                "Restart marker lacks a persisted installer result; residue retained"
            );
        }
        let (boot_key, uptime_key) = if uninstall {
            ("uninstall_started_boot_stamp", "reboot_uptime_ms")
        } else {
            (
                "installer_started_boot_stamp",
                "installer_started_uptime_ms",
            )
        };
        let restarted = if let Some(started) = op.reliability.vendor[boot_key].as_u64() {
            let current = boot_stamp.context("Cannot verify Windows boot identity")?;
            started > 0 && current > started
        } else {
            // Legacy records cannot acquire historical evidence after the fact.
            let started = op.reliability.vendor[uptime_key]
                .as_u64()
                .context("Restart-required record lacks boot evidence; vendor residue retained")?;
            uptime_ms.context("Cannot verify whether Windows restarted")? < started
        };
        anyhow::ensure!(restarted, "Restart required; no automatic restart");
        if uninstall {
            anyhow::ensure!(
                *current == before,
                "Windows restarted, but vendor installation records remain; residue retained"
            );
            op.reliability.vendor["uninstall_completed"] = json!(true);
            op.reliability.vendor["restart_observed"] = json!(true);
            op.resource.metadata["reboot_required"] = json!(false);
            return Ok(true);
        }
        // Installation requiring restart is not evidence of any uninstall.
        op.reliability.vendor["installer_restart_observed"] = json!(true);
        op.resource.metadata["reboot_required"] = json!(false);
    }

    if op.reliability.vendor["uninstall_started"] != true {
        return Ok(false);
    }

    match op.reliability.vendor["uninstall_attempt_state"].as_str() {
        Some("running") | None => {
            anyhow::bail!(
                "Vendor uninstaller outcome is unknown; automatic replay refused and residue retained"
            )
        }
        Some("returned") => {
            let code = op.reliability.vendor["uninstall_exit_code"]
                .as_i64()
                .context("Vendor uninstaller exit result missing; residue retained")?;
            if code == 0 {
                anyhow::ensure!(
                    *current == before,
                    "Vendor uninstaller returned success but product records remain; automatic replay refused"
                );
                op.reliability.vendor["uninstall_completed"] = json!(true);
                return Ok(true);
            }
            anyhow::ensure!(
                code != 3010,
                "Restart result is missing its recovery marker; residue retained"
            );
            anyhow::ensure!(
                *current == after,
                "Vendor records changed after an unsuccessful uninstall; residue retained"
            );
            let attempts = op.reliability.vendor["uninstall_attempts"]
                .as_u64()
                .unwrap_or(0);
            anyhow::ensure!(
                attempts < 3,
                "Vendor uninstaller retry limit reached; residue retained"
            );
            Ok(false)
        }
        Some(_) => anyhow::bail!("Unknown vendor-uninstall state; residue retained"),
    }
}

/// Compare product records before invoking any destructive vendor recipe.
pub fn validate_vendor_records(op: &Operation, current: &serde_json::Value) -> Result<()> {
    anyhow::ensure!(
        op.reliability.vendor["before"]
            .as_array()
            .is_some_and(|rows| rows.is_empty()),
        "Shared vendor baseline retained"
    );
    anyhow::ensure!(
        op.reliability.vendor.get("after").is_some(),
        "Installer interrupted before product identities were captured; automatic vendor uninstall blocked"
    );
    anyhow::ensure!(
        *current == op.reliability.vendor["after"]
            || (op.reliability.vendor["uninstall_started"] == true
                && *current == op.reliability.vendor["before"])
            || (op.reliability.vendor["uninstall_completed"] == true
                && *current == op.reliability.vendor["before"]),
        "Vendor records changed; refusing shared/unrelated uninstall"
    );
    Ok(())
}

fn validate_vendor_target(op: &Operation) -> Result<()> {
    let records = op.reliability.vendor["after"]
        .as_array()
        .context("Vendor records missing")?;
    if op.resource.kind == "python" {
        let roots = op.reliability.vendor["python_roots"]
            .as_array()
            .context("Python installation identity missing; retained")?;
        anyhow::ensure!(
            roots.len() == 1,
            "Shared or incomplete Python installation identity; retained"
        );
        let actual = platform::canonical(std::path::Path::new(
            roots[0]["path"].as_str().context("Python path missing")?,
        ))?;
        anyhow::ensure!(
            actual == platform::canonical(std::path::Path::new(&op.resource.path))?,
            "Python installed to another target"
        );
        let source = op
            .input
            .source
            .as_deref()
            .context("Official Python source missing")?;
        let filename = source
            .rsplit('/')
            .next()
            .context("Python installer filename missing")?;
        let version = filename
            .strip_prefix("python-")
            .and_then(|v| v.strip_suffix("-amd64.exe"))
            .context("Python product version not identified")?;
        anyhow::ensure!(
            !records.is_empty()
                && records.iter().all(|r| r["DisplayName"]
                    .as_str()
                    .is_some_and(|n| n.starts_with(&format!("Python {version} ")))),
            "Shared or unidentified Python product records; retained"
        );
    } else {
        let target = crate::store::path_key(&op.resource.path);
        anyhow::ensure!(
            !records.is_empty()
                && records.iter().all(|r| {
                    r["InstallLocation"]
                        .as_str()
                        .is_some_and(|p| crate::store::path_key(p) == target)
                        || r["UninstallString"].as_str().is_some_and(|p| {
                            crate::store::path_key(p.trim_matches('"'))
                                .starts_with(&format!("{target}\\"))
                        })
                }),
            "Miniforge installation records not exclusive to target; retained"
        );
    }
    Ok(())
}

fn verify_vendor_target(owner: &OwnedPath) -> Result<bool> {
    if !crate::recovery::entry_exists(&owner.path)? {
        return Ok(false);
    }
    anyhow::ensure!(
        crate::recovery::identity(&owner.path)? == owner.identity,
        "Vendor target replaced"
    );
    Ok(true)
}

fn python_installation_roots() -> Result<serde_json::Value> {
    let output = platform::run("powershell.exe", &["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), r#"$ErrorActionPreference='Stop'; $r=@(); foreach($base in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')) { if(Test-Path -LiteralPath $base) { foreach($k in Get-ChildItem -LiteralPath $base) { $p=Join-Path $k.PSPath 'InstallPath'; if(Test-Path -LiteralPath $p) {$r+=@{version=$k.PSChildName;path=(Get-Item -LiteralPath $p).GetValue('')}} } } }; ConvertTo-Json -InputObject @($r) -Compress"#.into()], std::time::Duration::from_secs(30), |_| Ok(()))?;
    anyhow::ensure!(
        output.code == 0 && !output.output_truncated && !output.descendants_active,
        "Python installation identity unavailable"
    );
    let roots: Vec<serde_json::Value> =
        serde_json::from_str(output.stdout.trim_start_matches('\u{feff}'))?;
    Ok(json!(roots))
}

#[cfg(test)]
mod owned_target_tests {
    use crate::model::OwnedPath;
    use std::{fs, path::PathBuf};

    #[test]
    fn missing_target_is_allowed_after_vendor_uninstall() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("python");
        let owner = OwnedPath {
            path: target,
            identity: "removed-instance".into(),
            purpose: "target".into(),
            removed: false,
        };

        assert!(!super::verify_vendor_target(&owner).unwrap());
    }

    #[test]
    fn dangling_link_replacement_blocks_vendor_cleanup() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("python");
        let missing = root.path().join("missing");

        #[cfg(unix)]
        std::os::unix::fs::symlink(&missing, &target).unwrap();
        #[cfg(windows)]
        std::os::windows::fs::symlink_file(&missing, &target).unwrap();

        let owner = OwnedPath {
            path: PathBuf::from(&target),
            identity: "original-instance".into(),
            purpose: "target".into(),
            removed: false,
        };

        assert!(!target.exists());
        assert!(super::verify_vendor_target(&owner).is_err());
        assert!(fs::symlink_metadata(&target).is_ok());
    }
}

#[cfg(all(test, windows))]
mod tests {
    #[test]
    fn failed_vendor_boot_probe_never_records_a_started_action() {
        for phase in ["installer", "uninstall"] {
            let mut vendor = serde_json::json!({"before": [], "uninstall_attempts": 1,
                "uninstall_attempt_state": "returned", "uninstall_exit_code": 5});
            let before = vendor.clone();
            let result = super::record_vendor_start_with(&mut vendor, phase, || {
                let _uptime = 123_u64;
                anyhow::bail!("Injected boot identity query failure")
            });
            assert!(result.is_err());
            assert_eq!(vendor, before);
        }
    }
    #[test]
    fn successful_vendor_probe_records_complete_evidence_for_each_phase() {
        for phase in ["installer", "uninstall"] {
            let mut vendor = serde_json::json!({"before": []});
            super::record_vendor_start_with(&mut vendor, phase, || Ok((123, 456))).unwrap();
            assert_eq!(vendor[format!("{phase}_started")], true);
            assert_eq!(vendor[format!("{phase}_started_uptime_ms")], 123);
            assert_eq!(vendor[format!("{phase}_started_boot_stamp")], 456);
            assert_eq!(vendor["before"], serde_json::json!([]));
        }
    }
    #[test]
    fn windows_boot_identity_is_available_and_stable() {
        let first = crate::platform::boot_stamp().unwrap();
        assert!(first > 0);
        assert_eq!(first, crate::platform::boot_stamp().unwrap());
    }

    #[test]
    fn python_registry_identity_query_executes_and_returns_array() {
        // Read actual registry roots, without installing or changing a product.
        assert!(super::python_installation_roots().unwrap().is_array());
    }
}
