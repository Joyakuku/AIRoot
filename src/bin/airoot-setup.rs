//! Offline command-line Windows installer. No resource deletion, global PATH or associations.
use airoot::{
    components, ipc, model, platform, portable,
    store::{Store, atomic_json, paths_overlap, read_json},
};
use anyhow::{Context, Result};
use clap::Parser;
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};
const PAYLOAD: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/setup-payload.zip"));
// Only owns the new staging directory. A retained previous installation is recovery material.
struct StageGuard {
    path: PathBuf,
    parent: PathBuf,
}
impl Drop for StageGuard {
    fn drop(&mut self) {
        let clean = (|| -> Result<()> {
            if !self.path.exists() || self.path.join("previous").exists() {
                return Ok(());
            }
            let actual = platform::canonical(&self.path)?;
            anyhow::ensure!(
                actual.parent() == Some(self.parent.as_path())
                    && actual
                        .file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with(".airoot-install-")),
                "Staging path identity changed"
            );
            let mut pending = vec![actual.clone()];
            while let Some(path) = pending.pop() {
                anyhow::ensure!(
                    !platform::is_link(&path)?,
                    "Linked staging content retained"
                );
                if path.is_dir() {
                    for item in fs::read_dir(&path)? {
                        pending.push(item?.path());
                    }
                }
            }
            fs::remove_dir_all(actual)?;
            Ok(())
        })();
        if let Err(e) = clean {
            eprintln!("Installer staging retained at {}: {e}", self.path.display());
        }
    }
}
#[derive(Parser)]
#[command(
    about = "AIRoot offline installer; preview build. Installs to an explicitly selected location; preserves managed resources and registry on uninstall."
)]
struct Args {
    #[arg(long)]
    install_dir: PathBuf,
    #[arg(long)]
    uninstall: bool,
    #[arg(long)]
    native_index: bool,
    #[arg(long, hide = true)]
    data_root: Option<PathBuf>,
}
#[derive(Clone, Serialize, Deserialize)]
struct FileRecord {
    path: String,
    sha256: String,
}
#[derive(Clone, Serialize, Deserialize)]
struct Manifest {
    version: String,
    channel: String,
    files: Vec<FileRecord>,
}
#[derive(Serialize, Deserialize)]
struct Installation {
    directory: PathBuf,
    manifest: Manifest,
    installed_at: String,
    #[serde(default)]
    uninstall_started: bool,
}
fn checked(root: &Path, name: &str) -> Result<PathBuf> {
    let p = Path::new(name);
    anyhow::ensure!(
        !p.is_absolute()
            && p.components()
                .all(|c| matches!(c, std::path::Component::Normal(_))),
        "Invalid package path"
    );
    let p = root.join(p);
    anyhow::ensure!(
        !p.exists() || !platform::is_link(&p)?,
        "Linked package member refused"
    );
    let mut ancestor = p.parent();
    while let Some(a) = ancestor {
        if a == root {
            break;
        }
        anyhow::ensure!(!platform::is_link(a)?, "Linked package ancestor refused");
        ancestor = a.parent();
    }
    Ok(p)
}
fn verify(root: &Path, m: &Manifest) -> Result<()> {
    for f in &m.files {
        let p = checked(root, &f.path)?;
        anyhow::ensure!(
            platform::hash_file(&p)? == f.sha256,
            "Installed/package file changed: {}; preserving it",
            p.display()
        );
    }
    Ok(())
}
fn reject_unowned(root: &Path, m: &Manifest) -> Result<()> {
    let mut pending = vec![root.to_owned()];
    while let Some(dir) = pending.pop() {
        for item in fs::read_dir(dir)? {
            let p = item?.path();
            anyhow::ensure!(
                !platform::is_link(&p)?,
                "Linked installation content refused: {}",
                p.display()
            );
            if p.is_dir() {
                pending.push(p);
            } else {
                let relative = p.strip_prefix(root)?.to_string_lossy().replace('\\', "/");
                anyhow::ensure!(
                    relative == "package-manifest.json"
                        || m.files
                            .iter()
                            .any(|f| f.path.replace('\\', "/") == relative),
                    "Unowned installation file preserved; move it before upgrade: {}",
                    p.display()
                );
            }
        }
    }
    Ok(())
}
fn remove_owned(root: &Path, m: &Manifest) -> Result<()> {
    // Every file is checked before removal; never recursively delete a user-selected directory.
    for f in &m.files {
        let p = checked(root, &f.path)?;
        if p.exists() {
            anyhow::ensure!(
                platform::hash_file(&p)? == f.sha256,
                "Modified file preserved during removal: {}",
                p.display()
            );
        }
    }
    let mut dirs = vec![];
    for f in &m.files {
        let p = checked(root, &f.path)?;
        if p.exists() {
            fs::remove_file(&p).with_context(|| {
                format!(
                    "Could not remove {}; retry uninstall after it is released",
                    p.display()
                )
            })?;
        }
        let mut a = p.parent();
        while let Some(d) = a {
            if d == root {
                break;
            }
            dirs.push(d.to_owned());
            a = d.parent();
        }
    }
    dirs.sort_by_key(|p| std::cmp::Reverse(p.components().count()));
    dirs.dedup();
    for d in dirs {
        let _ = fs::remove_dir(d);
    }
    let manifest = root.join("package-manifest.json");
    if manifest.is_file() {
        fs::remove_file(manifest)?;
    }
    let _ = fs::remove_dir(root);
    Ok(())
}
// Probe the entire owned installation, not just the current data root's index.
// Request write/delete access without changing contents. Release the probe
// handles before renaming their parent (Windows can otherwise deny the rename).
// Races after this preflight are handled as post-commit cleanup warnings.
#[cfg(windows)]
fn ensure_installation_unused(root: &Path, manifest: &Manifest) -> Result<()> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::{
        Foundation::GENERIC_WRITE,
        Storage::FileSystem::{DELETE, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE},
    };
    // Process image paths use long names even when TEMP/install paths use 8.3 aliases.
    let process_root = platform::canonical_long_unlinked(root)?;
    let processes = platform::processes_under(&process_root)
        .context("Cannot verify whether this installation is still in use")?;
    anyhow::ensure!(
        processes.is_empty(),
        "Processes still use this installation (PIDs: {processes:?}); close them, including AIRoot instances for other data roots, and retry. No other instance was terminated"
    );
    let _guards: Vec<fs::File> = manifest
        .files
        .iter()
        .map(|file| file.path.as_str())
        .chain(std::iter::once("package-manifest.json"))
        .map(|name| {
            let path = checked(root, name)?;
            // DELETE catches handles denying deletion; write access also detects
            // mapped executable/DLL images. Requesting access does not write data.
            fs::OpenOptions::new()
                .access_mode(DELETE | GENERIC_WRITE)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
                .open(&path)
                .with_context(|| {
                    format!(
                        "Installation file is in use or cannot be replaced: {}; close its users and retry",
                        path.display()
                    )
                })
        })
        .collect::<Result<_>>()?;
    Ok(())
}
fn prepare_previous(target: &Path, backup: &Path, manifest: &Manifest) -> Result<()> {
    #[cfg(windows)]
    ensure_installation_unused(target, manifest).context(
        "Upgrade blocked before publication; existing installation and metadata have not been moved or replaced",
    )?;
    #[cfg(not(windows))]
    let _ = manifest;
    fs::rename(target, backup)
        .context("Could not stage previous installation before publication")?;
    Ok(())
}
// Publication and metadata have already committed. A cleanup error must never
// turn this into an 'Installation failed' result or trigger a fictitious rollback.
fn cleanup_committed_installation(
    stage: &Path,
    backup: &Path,
    archive: &Path,
    previous: Option<&Manifest>,
) -> Vec<String> {
    let mut pending = Vec::new();
    if let Some(manifest) = previous {
        if let Err(error) = remove_owned(backup, manifest) {
            pending.push(format!(
                "Previous installation cleanup pending at {}: {error:#}",
                backup.display()
            ));
        }
    }
    if let Err(error) = fs::remove_file(archive) {
        pending.push(format!(
            "Installer archive cleanup pending at {}: {error}",
            archive.display()
        ));
    }
    if let Err(error) = fs::remove_dir(stage) {
        pending.push(format!(
            "Installer staging cleanup pending at {}: {error}",
            stage.display()
        ));
    }
    pending
}
fn stop_existing(target: &Path, data: &Path) -> Result<()> {
    let exe = target.join("airoot.exe");
    let out = platform::run(
        exe.to_str().context("Invalid install path")?,
        &[
            "--data-root".into(),
            data.to_string_lossy().into(),
            "--json".into(),
            "worker-stop".into(),
        ],
        Duration::from_secs(20),
        |_| Ok(()),
    )?;
    anyhow::ensure!(
        out.code == 0,
        "Worker has active operations/usage or could not stop: {}",
        out.stdout
    );
    // The CLI response precedes worker shutdown by at most its pipe polling interval.
    std::thread::sleep(Duration::from_millis(400));
    let s = Store::open(data)?;
    components::stop(&s)?;
    Ok(())
}
// Resolve an existing ancestor without creating the prospective information directory.
// This also normalizes existing Windows aliases before comparing layout boundaries.
fn prospective_path(path: &Path) -> Result<PathBuf> {
    anyhow::ensure!(
        path.is_absolute(),
        "Information/resource path must be absolute"
    );
    let mut existing = path;
    let mut suffix = vec![];
    while !existing.try_exists()? {
        suffix.push(existing.file_name().context("Unresolvable layout path")?);
        existing = existing
            .parent()
            .context("Layout path needs an existing ancestor")?;
    }
    let mut resolved = platform::canonical(existing)?;
    for name in suffix.into_iter().rev() {
        resolved.push(name);
    }
    Ok(resolved)
}
fn layout_overlaps(a: &Path, b: &Path) -> bool {
    paths_overlap(&a.to_string_lossy(), &b.to_string_lossy())
}
fn rollback_install_dirs(target: &Path, unpack: &Path, backup: &Path) -> Result<bool> {
    if !backup.exists() {
        return Ok(false);
    }
    fs::rename(target, unpack).with_context(|| {
        format!(
            "could not move new installation from {} to {}; previous installation remains at {}",
            target.display(),
            unpack.display(),
            backup.display()
        )
    })?;
    fs::rename(backup, target).with_context(|| {
        format!(
            "could not restore previous installation from {} to {}",
            backup.display(),
            target.display()
        )
    })?;
    Ok(true)
}
fn execute(args: Args) -> Result<()> {
    anyhow::ensure!(
        args.install_dir.is_absolute(),
        "Select an absolute --install-dir"
    );
    let parent = platform::canonical(
        args.install_dir
            .parent()
            .context("Installation needs a parent directory")?,
    )?;
    let target = parent.join(
        args.install_dir
            .file_name()
            .context("Cannot install at volume root")?,
    );
    anyhow::ensure!(
        !target.exists() || !platform::is_link(&target)?,
        "Linked installation directory refused"
    );
    let target = if target.try_exists()? {
        platform::canonical(&target)?
    } else {
        target
    };
    let data = prospective_path(&args.data_root.unwrap_or(ipc::data_root()?))?;
    anyhow::ensure!(
        !layout_overlaps(&target, &data),
        "Program and information directories must not overlap"
    );
    // Layout rejection must precede all creation in user-selected locations.
    fs::create_dir_all(&data)?;
    let data = platform::canonical(&data)?;
    anyhow::ensure!(
        !layout_overlaps(&target, &data),
        "Program and information directories must not overlap"
    );
    let installer_lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(data.join("installer.lock"))?;
    installer_lock
        .try_lock_exclusive()
        .context("Another installer is active")?;
    let old_config = match fs::read(data.join("config.json")) {
        Ok(bytes) => Some(bytes),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e).context("Cannot inspect existing configuration"),
    };
    if let Some(bytes) = &old_config {
        let c: model::Config = serde_json::from_slice(bytes)?;
        for root in [&c.env_root, &c.tools_root].into_iter().flatten() {
            let root = prospective_path(root)?;
            anyhow::ensure!(
                !layout_overlaps(&target, &root),
                "Installation and resource roots must not overlap"
            );
        }
    }
    let record = data.join("installation.json");
    let previous: Option<Installation> = if record.is_file() {
        Some(read_json(&record)?)
    } else {
        None
    };
    if let Some(old) = &previous {
        anyhow::ensure!(
            old.directory == target,
            "Existing installation is at {}; use that path for upgrade/uninstall",
            old.directory.display()
        );
        if !args.uninstall {
            anyhow::ensure!(
                !old.uninstall_started,
                "Finish interrupted uninstall before reinstalling"
            );
            verify(&target, &old.manifest)?;
        }
    }
    if args.uninstall {
        let mut old =
            previous.context("No owned installation record; refusing to delete arbitrary files")?;
        if !old.uninstall_started {
            verify(&target, &old.manifest)?;
            stop_existing(&target, &data)?;
            let s = Store::open(&data)?;
            if components::native_service_installed(&s)? {
                run_service(&s, false)?;
            }
            old.uninstall_started = true;
            atomic_json(&record, &old)?;
        }
        remove_owned(&target, &old.manifest)?;
        fs::remove_file(&record)?;
        let cache = data.join("components/everything/Everything.db");
        if cache.is_file() && !platform::is_link(&cache)? {
            fs::remove_file(cache)?;
        }
        println!(
            "Uninstalled AIRoot. Managed resources and information retained at {}",
            data.display()
        );
        return Ok(());
    }
    anyhow::ensure!(
        !PAYLOAD.is_empty(),
        "No embedded payload. Build installer with scripts/package.ps1"
    );
    if previous.is_none() {
        anyhow::ensure!(
            !target.exists() || fs::read_dir(&target)?.next().is_none(),
            "Fresh installation target must be empty"
        );
    }
    if let Some(old) = &previous {
        reject_unowned(&target, &old.manifest)?;
    }
    let stage = parent.join(format!(".airoot-install-{}", model::id()));
    fs::create_dir(&stage)?;
    let _stage_guard = StageGuard {
        path: stage.clone(),
        parent: platform::canonical(&parent)?,
    };
    let archive = stage.join("payload.zip");
    fs::write(&archive, PAYLOAD)?;
    let unpack = stage.join("files");
    fs::create_dir(&unpack)?;
    portable::extract(&archive, &unpack, &platform::hash_file(&archive)?)?;
    let manifest: Manifest = read_json(&unpack.join("package-manifest.json"))?;
    verify(&unpack, &manifest)?;
    let backup = stage.join("previous");
    let mut restore_native = false;
    if let Some(old) = &previous {
        reject_unowned(&target, &old.manifest)?;
        stop_existing(&target, &data)?;
        let view = Store::open(&data)?;
        restore_native = components::native_service_installed(&view)?;
        if restore_native {
            run_service(&view, false)?;
        }
        if let Err(error) = prepare_previous(&target, &backup, &old.manifest) {
            if restore_native {
                if let Err(restore_error) = run_service(&view, true) {
                    return Err(error.context(format!(
                        "Previous installation was not moved; native service restoration also failed: {restore_error:#}"
                    )));
                }
            }
            return Err(error);
        }
    } else if target.exists() {
        fs::remove_dir(&target)?;
    }
    if let Err(e) = fs::rename(&unpack, &target) {
        if backup.exists() {
            let _ = fs::rename(&backup, &target);
        }
        if restore_native {
            let _ = run_service(&Store::open(&data)?, true);
        }
        return Err(e.into());
    }
    let installation = Installation {
        directory: target.clone(),
        manifest: manifest.clone(),
        installed_at: model::now(),
        uninstall_started: false,
    };
    let commit = (|| -> Result<()> {
        let mut s = Store::open(&data)?;
        let mut c = s.config.clone();
        c.component_dir = target.join("components");
        s.save_config(c)?;
        atomic_json(&record, &installation)?;
        Ok(())
    })();
    if let Err(e) = commit {
        let mut rollback_errors = Vec::new();
        let old_installation_restored = match rollback_install_dirs(&target, &unpack, &backup) {
            Ok(restored) => restored,
            Err(error) => {
                rollback_errors.push(format!("{error:#}"));
                false
            }
        };
        if let Some(bytes) = old_config {
            if let Err(error) = fs::write(data.join("config.json"), bytes) {
                rollback_errors.push(format!("could not restore previous configuration: {error}"));
            }
        } else if data.join("config.json").exists() {
            if let Err(error) = fs::remove_file(data.join("config.json")) {
                rollback_errors.push(format!("could not remove new configuration: {error}"));
            }
        }
        if restore_native && old_installation_restored {
            match Store::open(&data).and_then(|store| run_service(&store, true)) {
                Ok(()) => {}
                Err(error) => {
                    rollback_errors.push(format!("could not restore embedded service: {error:#}"))
                }
            }
        }
        let detail = if rollback_errors.is_empty() {
            format!(
                "Previous installation restored; failed update material retained at {}",
                unpack.display()
            )
        } else {
            format!(
                "Rollback incomplete: {}. Preserve and inspect {} and {}",
                rollback_errors.join("; "),
                target.display(),
                backup.display()
            )
        };
        return Err(e.context(format!(
            "Installation metadata failed. {detail}; staging directory {}",
            stage.display()
        )));
    }
    let mut pending = cleanup_committed_installation(
        &stage,
        &backup,
        &archive,
        previous.as_ref().map(|old| &old.manifest),
    );
    if args.native_index || restore_native {
        if let Err(error) = Store::open(&data).and_then(|store| run_service(&store, true)) {
            pending.push(format!("Native index service setup pending: {error:#}"));
        }
    }
    if !pending.is_empty() {
        println!(
            "Installed AIRoot {} ({}) at {}. New installation and metadata committed successfully; post-install work is incomplete. Information: {}.",
            manifest.version,
            manifest.channel,
            target.display(),
            data.display()
        );
        eprintln!(
            "WARNING: {}. No rollback attempted; keep the new installation and inspect the retained paths before cleanup.",
            pending.join("; ")
        );
        return Ok(());
    }
    println!(
        "Installed AIRoot {} ({}) at {}. Information: {}. Configure existing env/tools roots through airoot config set.",
        manifest.version,
        manifest.channel,
        target.display(),
        data.display()
    );
    Ok(())
}
fn main() {
    if let Err(e) = execute(Args::parse()) {
        eprintln!("Installation failed: {e:#}");
        std::process::exit(1);
    }
}

fn run_service(s: &Store, install: bool) -> Result<()> {
    let plan = components::service_plan(s)?;
    let args = plan[if install {
        "install_args"
    } else {
        "uninstall_args"
    }]
    .as_array()
    .context("Missing service arguments")?
    .iter()
    .map(|v| v.as_str().unwrap().to_owned())
    .collect::<Vec<_>>();
    let out = platform::run(
        plan["program"].as_str().context("Missing service binary")?,
        &args,
        Duration::from_secs(30),
        |_| Ok(()),
    )?;
    anyhow::ensure!(
        out.code == 0,
        "Dedicated service change failed; an Agent-approved administrator process may be required"
    );
    let until = std::time::Instant::now() + Duration::from_secs(10);
    while std::time::Instant::now() < until {
        if components::native_service_installed(s)? == install {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    anyhow::bail!(
        "Dedicated service state did not reach requested state; inspect components service-plan"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owned_fixture(root: &Path) -> Manifest {
        fs::create_dir_all(root.join("components")).unwrap();
        let file = root.join("components/owned.bin");
        fs::write(&file, "original component").unwrap();
        let manifest = Manifest {
            version: "test".into(),
            channel: "test".into(),
            files: vec![FileRecord {
                path: "components/owned.bin".into(),
                sha256: platform::hash_file(&file).unwrap(),
            }],
        };
        atomic_json(&root.join("package-manifest.json"), &manifest).unwrap();
        manifest
    }

    #[cfg(windows)]
    #[test]
    fn locked_installation_file_blocks_upgrade_before_old_directory_moves() {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{FILE_SHARE_READ, FILE_SHARE_WRITE};
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("app");
        let backup = tmp.path().join("previous");
        let manifest = owned_fixture(&target);
        let metadata = tmp.path().join("installation.json");
        fs::write(&metadata, "original metadata").unwrap();
        let held = fs::OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .open(target.join("components/owned.bin"))
            .unwrap();
        let error = prepare_previous(&target, &backup, &manifest).unwrap_err();
        let message = format!("{error:#}");
        assert!(
            message.contains("Upgrade blocked before publication"),
            "{message}"
        );
        assert!(message.contains("owned.bin"), "{message}");
        assert!(target.is_dir());
        assert!(!backup.exists());
        verify(&target, &manifest).unwrap();
        assert_eq!(fs::read_to_string(&metadata).unwrap(), "original metadata");
        drop(held);
        prepare_previous(&target, &backup, &manifest).unwrap();
        assert!(!target.exists());
        assert!(backup.is_dir());
        verify(&backup, &manifest).unwrap();
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "child-process fixture, invoked only by the running-component regression"]
    fn installer_component_process_fixture() {
        assert_eq!(
            std::env::var("AIROOT_INSTALLER_PROCESS_FIXTURE").as_deref(),
            Ok("1")
        );
        fs::write(
            std::env::var_os("AIROOT_INSTALLER_PROCESS_READY").unwrap(),
            std::env::current_exe()
                .unwrap()
                .to_string_lossy()
                .as_bytes(),
        )
        .unwrap();
        std::thread::sleep(Duration::from_secs(30));
    }

    #[cfg(windows)]
    #[test]
    fn running_component_from_this_installation_blocks_upgrade_without_termination() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("app");
        let backup = tmp.path().join("previous");
        let mut manifest = owned_fixture(&target);
        let component = target.join("components/Everything.exe");
        fs::copy(std::env::current_exe().unwrap(), &component).unwrap();
        manifest.files.push(FileRecord {
            path: "components/Everything.exe".into(),
            sha256: platform::hash_file(&component).unwrap(),
        });
        atomic_json(&target.join("package-manifest.json"), &manifest).unwrap();
        let ready = tmp.path().join("component-ready");
        // Launch through a long path, but probe the original (possibly 8.3) root.
        let image = platform::canonical_long_unlinked(&component).unwrap();
        let mut child = std::process::Command::new(&image)
            .args([
                "--exact",
                "tests::installer_component_process_fixture",
                "--ignored",
            ])
            .env("AIROOT_INSTALLER_PROCESS_FIXTURE", "1")
            .env("AIROOT_INSTALLER_PROCESS_READY", &ready)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let until = std::time::Instant::now() + Duration::from_secs(10);
        while !ready.exists() && std::time::Instant::now() < until {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let child_ready = ready.exists();
        let result = prepare_previous(&target, &backup, &manifest);
        let still_running = child.try_wait().unwrap().is_none();
        // Only the child created by this regression is stopped, never a host process.
        let _ = child.kill();
        child.wait().unwrap();
        assert!(
            child_ready,
            "The component fixture must be running before the probe"
        );
        let error = result.unwrap_err();
        let message = format!("{error:#}");
        assert!(
            message.contains("Upgrade blocked before publication"),
            "{message}"
        );
        assert!(message.contains(&child.id().to_string()), "{message}");
        assert!(
            still_running,
            "The installer must not terminate another instance"
        );
        assert!(target.is_dir());
        assert!(!backup.exists());
        verify(&target, &manifest).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn unrelated_locked_file_does_not_block_installation_upgrade() {
        use std::os::windows::fs::OpenOptionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("app");
        let backup = tmp.path().join("previous");
        let manifest = owned_fixture(&target);
        let other = tmp.path().join("external-Everything.exe");
        fs::write(&other, "external").unwrap();
        let _held = fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(other)
            .unwrap();
        prepare_previous(&target, &backup, &manifest).unwrap();
        assert!(!target.exists());
        assert!(backup.is_dir());
    }

    #[test]
    fn committed_cleanup_failure_preserves_new_installation_and_recovery_material() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("app");
        let manifest = owned_fixture(&target);
        let metadata = tmp.path().join("installation.json");
        fs::write(&metadata, "committed new metadata").unwrap();
        let stage = tmp.path().join(".airoot-install-test");
        fs::create_dir(&stage).unwrap();
        let backup = stage.join("previous");
        let old_manifest = owned_fixture(&backup);
        fs::write(
            backup.join("components/owned.bin"),
            "changed after publication",
        )
        .unwrap();
        let archive = stage.join("payload.zip");
        fs::write(&archive, "payload").unwrap();
        let pending =
            cleanup_committed_installation(&stage, &backup, &archive, Some(&old_manifest));
        assert!(
            pending
                .iter()
                .any(|p| p.contains("Previous installation cleanup pending"))
        );
        assert!(backup.join("components/owned.bin").exists());
        assert!(backup.join("package-manifest.json").exists());
        verify(&target, &manifest).unwrap();
        assert_eq!(
            fs::read_to_string(metadata).unwrap(),
            "committed new metadata"
        );
        assert!(!archive.exists());
        drop(StageGuard {
            path: stage.clone(),
            parent: platform::canonical(tmp.path()).unwrap(),
        });
        assert!(
            backup.exists(),
            "Recovery material must survive staging cleanup"
        );
    }

    #[test]
    fn committed_cleanup_success_removes_previous_and_staging() {
        let tmp = tempfile::tempdir().unwrap();
        let stage = tmp.path().join(".airoot-install-test");
        fs::create_dir(&stage).unwrap();
        let backup = stage.join("previous");
        let manifest = owned_fixture(&backup);
        let archive = stage.join("payload.zip");
        fs::write(&archive, "payload").unwrap();
        assert!(
            cleanup_committed_installation(&stage, &backup, &archive, Some(&manifest)).is_empty()
        );
        assert!(!stage.exists());
    }

    #[test]
    fn prospective_layout_resolves_missing_descendants_without_creating_them() {
        let tmp = tempfile::tempdir().unwrap();
        let missing = tmp.path().join("new").join("information");
        assert_eq!(
            prospective_path(&missing).unwrap(),
            platform::canonical(tmp.path())
                .unwrap()
                .join("new")
                .join("information")
        );
        assert!(!missing.exists());
        assert!(prospective_path(Path::new("relative/information")).is_err());
    }

    #[test]
    fn layout_boundaries_do_not_confuse_sibling_prefixes() {
        let tmp = tempfile::tempdir().unwrap();
        let app = tmp.path().join("App");
        assert!(layout_overlaps(&app, &app.join("information")));
        assert!(layout_overlaps(&app.join("bin"), &app));
        assert!(!layout_overlaps(&app, &tmp.path().join("Application")));
    }

    #[cfg(windows)]
    #[test]
    fn windows_layout_overlap_is_case_insensitive_in_both_directions() {
        assert!(layout_overlaps(
            Path::new(r"D:\Apps\AIRoot"),
            Path::new(r"d:\apps\airoot\information")
        ));
        assert!(layout_overlaps(
            Path::new(r"d:\apps\airoot\bin"),
            Path::new(r"D:\Apps\AIRoot")
        ));
        assert!(!layout_overlaps(
            Path::new(r"D:\Apps\AIRoot"),
            Path::new(r"d:\apps\airoot-extra")
        ));
    }

    #[test]
    fn unreadable_configuration_is_not_treated_as_a_fresh_install() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("App");
        let data = tmp.path().join("information");
        fs::create_dir_all(data.join("config.json")).unwrap();
        let error = execute(Args {
            install_dir: target.clone(),
            uninstall: false,
            native_index: false,
            data_root: Some(data),
        })
        .unwrap_err();
        assert!(format!("{error:#}").contains("Cannot inspect existing configuration"));
        assert!(!target.exists());
    }

    #[cfg(windows)]
    #[test]
    fn installer_rejects_case_alias_of_configured_resource_root() {
        let tmp = tempfile::tempdir().unwrap();
        let resources = tmp.path().join("Resources");
        fs::create_dir(&resources).unwrap();
        let target = resources.join("app");
        let data = tmp.path().join("information");
        fs::create_dir(&data).unwrap();
        let config = model::Config {
            env_root: Some(PathBuf::from(resources.to_string_lossy().to_lowercase())),
            ..Default::default()
        };
        atomic_json(&data.join("config.json"), &config).unwrap();
        let error = execute(Args {
            install_dir: target.clone(),
            uninstall: false,
            native_index: false,
            data_root: Some(data),
        })
        .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("Installation and resource roots must not overlap"),
            "{error:#}"
        );
        assert!(!target.exists());
    }

    #[test]
    fn failed_new_directory_move_never_overwrites_previous_installation() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("app");
        let backup = tmp.path().join("previous");
        let unpack = tmp.path().join("missing-stage").join("files");
        fs::create_dir(&target).unwrap();
        fs::create_dir(&backup).unwrap();
        fs::write(target.join("version.txt"), "new").unwrap();
        fs::write(backup.join("version.txt"), "old").unwrap();
        let error = rollback_install_dirs(&target, &unpack, &backup).unwrap_err();
        assert!(error.to_string().contains(&backup.display().to_string()));
        assert_eq!(
            fs::read_to_string(target.join("version.txt")).unwrap(),
            "new"
        );
        assert_eq!(
            fs::read_to_string(backup.join("version.txt")).unwrap(),
            "old"
        );
    }

    #[test]
    fn installer_rejects_overlapping_layout_before_creating_information() {
        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("App");
        let data = target.join("information");
        let error = execute(Args {
            install_dir: target.clone(),
            uninstall: false,
            native_index: false,
            data_root: Some(data.clone()),
        })
        .unwrap_err();
        assert!(error.to_string().contains("must not overlap"), "{error:#}");
        assert!(
            !target.exists(),
            "Rejected layout must not create information inside the program directory"
        );
        assert!(!data.exists());
    }
}
