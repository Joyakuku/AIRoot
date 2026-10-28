use crate::{
    adapters,
    ipc::Shared,
    model::*,
    platform,
    store::{Store, atomic_json},
};
use anyhow::{Context, Result};
use serde_json::json;
use std::{
    collections::HashSet,
    fs,
    path::Path,
    sync::{Mutex, OnceLock},
};
static ACTIVE: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();

/// Stable filesystem identity, not just a pathname or modification time.
pub fn identity(path: &Path) -> Result<String> {
    anyhow::ensure!(
        !platform::is_link(path)?,
        "Linked object cannot establish ownership"
    );
    #[cfg(windows)]
    {
        use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
        use windows_sys::Win32::Storage::FileSystem::*;
        let f = fs::OpenOptions::new()
            .read(true)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
            .open(path)?;
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        anyhow::ensure!(
            unsafe { GetFileInformationByHandle(f.as_raw_handle(), &mut info) } != 0,
            "Cannot identify owned object"
        );
        Ok(format!(
            "{}:{}:{}:{}:{}",
            info.dwVolumeSerialNumber,
            info.nFileIndexHigh,
            info.nFileIndexLow,
            info.ftCreationTime.dwHighDateTime,
            info.ftCreationTime.dwLowDateTime
        ))
    }
    #[cfg(not(windows))]
    {
        use std::os::unix::fs::MetadataExt;
        let m = fs::metadata(path)?;
        Ok(format!("{}:{}", m.dev(), m.ino()))
    }
}

/// Test for the directory entry itself; `Path::exists` follows links and reports
/// a dangling symlink as absent, which is unsafe for deletion reconciliation.
pub fn entry_exists(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

pub fn persist(shared: &Shared, op: &mut Operation) -> Result<()> {
    op.updated_at = now();
    let s = shared.lock().unwrap();
    atomic_json(&s.root.join("pending").join(format!("{}.json", op.id)), op)?;
    s.save("operations", &op.id, op)
}
pub fn own(shared: &Shared, op: &mut Operation, path: &Path, purpose: &str) -> Result<()> {
    op.reliability.owned.push(OwnedPath {
        path: platform::canonical(path)?,
        identity: identity(path)?,
        purpose: purpose.into(),
        removed: false,
    });
    persist(shared, op)
}
pub fn new_host(op: &Operation) -> bool {
    op.resource.scope == "host"
        && matches!(
            op.input.action.as_str(),
            "create" | "install-portable" | "bootstrap"
        )
}
pub fn version_matches(op: &Operation) -> bool {
    let Some(wanted) = &op.input.version else {
        return true;
    };
    let wanted = wanted.trim_start_matches('v');
    op.resource.version.as_deref().is_some_and(|v| {
        v.split_whitespace().any(|word| {
            let word = word.trim_matches('"').trim_start_matches('v');
            word == wanted
                || (wanted.matches('.').count() < 2 && word.starts_with(&format!("{wanted}.")))
        })
    })
}
fn safe_tree(path: &Path) -> Result<()> {
    anyhow::ensure!(!platform::is_link(path)?, "Linked tree retained");
    if path.is_dir() {
        for e in fs::read_dir(path)? {
            safe_tree(&e?.path())?;
        }
    }
    Ok(())
}
pub fn guard(shared: &Shared, op: &Operation) -> Result<()> {
    let s = shared.lock().unwrap();
    let mut protected_ids = s.related_ids(&op.resource)?;
    protected_ids.push(format!(
        "path:{}",
        crate::store::path_key(&op.resource.path)
    ));
    for mut l in s.leases()? {
        if l.state == "released" {
            continue;
        }
        if op.reliability.lease_id.as_deref() == Some(&l.id) {
            if l.state != "reserved" {
                let job = l
                    .process_job
                    .as_deref()
                    .context("Legacy process tree evidence missing; protection retained")?;
                let parent_finished = match (l.pid, l.process_stamp) {
                    (Some(pid), Some(stamp)) => platform::process_stamp(pid)? != Some(stamp),
                    _ => false,
                };
                anyhow::ensure!(
                    parent_finished && platform::job_finished(job)?,
                    "Operation or descendant process remains active"
                );
                l.state = "reserved".into();
                l.pid = None;
                l.process_stamp = None;
                s.save_lease(&l)?;
            }
            anyhow::ensure!(
                l.state == "reserved",
                "Operation process/descendants require verification: {}",
                l.state
            );
        } else if crate::store::protection_conflicts(&l.resource_ids, &protected_ids) {
            anyhow::ensure!(false, "Resource protected by another task: {}", l.id);
        }
    }
    // Process enumeration must not hold the registry mutex and block query commands.
    drop(s);
    if op.resource.scope == "host" && Path::new(&op.resource.path).try_exists()? {
        anyhow::ensure!(
            platform::processes_under(Path::new(&op.resource.path))?.is_empty(),
            "Resource process is still running"
        );
    }
    Ok(())
}
pub fn cleanup(shared: &Shared, op: &mut Operation) -> Result<()> {
    if op.reliability.cleanup.status == "completed"
        && op.reliability.owned.iter().all(|p| p.removed)
    {
        return Ok(());
    }
    op.reliability.cleanup.status = "running".into();
    op.reliability.cleanup.attempts += 1;
    persist(shared, op)?;
    let result = (|| -> Result<()> {
        anyhow::ensure!(
            new_host(op),
            "Cleanup never deletes existing environments or platform data"
        );
        anyhow::ensure!(
            op.reliability.failure_confirmed,
            "Failure not confirmed; installation retained"
        );
        anyhow::ensure!(
            op.registration_status != "failed" && op.registration_status != "succeeded",
            "Registered or registration-failed installation retained"
        );
        anyhow::ensure!(
            !op.reliability.owned.is_empty(),
            "Legacy or interrupted ownership evidence missing"
        );
        guard(shared, op)?;
        anyhow::ensure!(
            !shared.lock().unwrap().ignored(&op.resource)?,
            "Resource management was explicitly cancelled; installation retained"
        );
        // Vendor uninstall is destructive too: validate all ownership boundaries before invoking it.
        for owned in &op.reliability.owned {
            if owned.removed || !entry_exists(&owned.path)? {
                continue;
            }
            anyhow::ensure!(
                identity(&owned.path)? == owned.identity,
                "Owned object replaced before cleanup"
            );
            for ancestor in owned.path.ancestors() {
                anyhow::ensure!(!platform::is_link(ancestor)?, "Linked ancestor retained");
            }
            let s = shared.lock().unwrap();
            let root = if op.resource.class == "env" {
                s.config.env_root.as_ref()
            } else {
                s.config.tools_root.as_ref()
            }
            .context("Resource root missing")?;
            platform::checked_target(&owned.path, root)?;
            let key = normalized_path(&owned.path)?;
            for resource in s.resources()?.into_iter().filter(|r| r.scope == "host") {
                let other = normalized_path(Path::new(&resource.path))?;
                anyhow::ensure!(
                    key != other
                        && !other.starts_with(&format!("{key}\\"))
                        && !key.starts_with(&format!("{other}\\")),
                    "Cleanup overlaps a registered resource: {}",
                    resource.id
                );
            }
            drop(s);
            safe_tree(&owned.path)?;
        }
        if op.input.action == "bootstrap" {
            crate::bootstrap::cleanup_vendor(shared, op)?;
        }
        for index in 0..op.reliability.owned.len() {
            let owned = op.reliability.owned[index].clone();
            if owned.removed {
                continue;
            }
            if entry_exists(&owned.path)? {
                anyhow::ensure!(
                    identity(&owned.path)? == owned.identity,
                    "Owned object was replaced: {}",
                    owned.path.display()
                );
                let s = shared.lock().unwrap();
                let root = if op.resource.class == "env" {
                    s.config.env_root.as_ref()
                } else {
                    s.config.tools_root.as_ref()
                }
                .context("Resource root missing")?;
                platform::checked_target(&owned.path, root)?;
                for ancestor in owned.path.ancestors() {
                    anyhow::ensure!(!platform::is_link(ancestor)?, "Linked ancestor retained");
                }
                for r in s.resources()? {
                    if r.id != op.resource.id && r.scope == "host" {
                        let p = normalized_path(Path::new(&r.path))?;
                        let owned_key = crate::store::path_key(&owned.path.to_string_lossy());
                        let overlaps = p == owned_key
                            || p.starts_with(&format!("{owned_key}\\"))
                            || owned_key.starts_with(&format!("{p}\\"));
                        anyhow::ensure!(
                            !overlaps,
                            "Owned path is shared with registered resource {}",
                            r.id
                        );
                    }
                }
                drop(s);
                crate::owned_cleanup::remove(&owned.path, &owned.identity)?;
            }
            op.reliability.owned[index].removed = true;
            op.reliability
                .cleanup
                .completed_steps
                .push(format!("removed:{}", owned.path.display()));
            persist(shared, op)?;
        }
        Ok(())
    })();
    op.reliability.cleanup.status = if result.is_ok() {
        "completed"
    } else {
        "blocked"
    }
    .into();
    if result.is_ok() && op.reliability.failure_confirmed {
        op.status = "failed".into();
    }
    op.reliability.cleanup.reason = result.as_ref().err().map(|e| format!("{e:#}"));
    persist(shared, op)?;
    result
}
fn terminal(op: &Operation) -> bool {
    op.status == "completed"
        || op.reliability.cleanup.status == "completed"
        || (op.status == "failed" && op.reliability.recovery.status == "completed")
}
fn finish_lease(shared: &Shared, op: &Operation) -> Result<()> {
    let s = shared.lock().unwrap();
    if let Some(id) = &op.reliability.lease_id {
        for mut l in s.leases()? {
            if &l.id == id && l.state != "released" {
                anyhow::ensure!(
                    l.state == "reserved",
                    "Process protection is not ready for release"
                );
                s.finish_lease(&mut l)?;
            }
        }
    }
    Ok(())
}
/// The durable terminal result is the release barrier. In particular a registry
/// commit alone must not expose a resource while its installation journal is stale.
pub(crate) fn finalize(shared: &Shared, op: &mut Operation) -> Result<()> {
    if !terminal(op) {
        return persist(shared, op);
    }
    op.reliability.finalization.status = "running".into();
    op.reliability.finalization.reason = None;
    op.reliability.finalization.attempts += 1;
    let result = (|| -> Result<()> {
        persist(shared, op)
            .context("Final result was not durably committed; protection retained")?;
        finish_lease(shared, op).context("Final result committed; protection release pending")?;
        op.reliability.finalization.status = "completed".into();
        persist(shared, op).context("Protection released; finalization metadata pending")
    })();
    if let Err(error) = &result {
        op.reliability.finalization.status = "blocked".into();
        op.reliability.finalization.reason = Some(format!("{error:#}"));
        // Best effort diagnostics never replace the original installation error.
        // If storage stays unavailable the pre-release journal and lease remain.
        let _ = persist(shared, op);
    }
    result
}
fn reconcile(shared: &Shared, op: &mut Operation, only_cleanup: bool) -> Result<()> {
    guard(shared, op)?;
    if only_cleanup || (op.reliability.failure_confirmed && new_host(op)) {
        return cleanup(shared, op);
    }
    if op.input.action == "delete" {
        let gone = if op.resource.scope == "host" {
            !entry_exists(Path::new(&op.resource.path))?
        } else {
            crate::platform_resources::deleted(&op.resource)?
        };
        anyhow::ensure!(
            gone,
            "Deletion incomplete; never replay deletion during recovery"
        );
    } else {
        if new_host(op) {
            let owner = op
                .reliability
                .owned
                .iter()
                .find(|o| o.purpose == "target")
                .context("Ownership evidence missing; manual verification required")?;
            anyhow::ensure!(
                owner.path.exists() && identity(&owner.path)? == owner.identity,
                "Target missing or replaced; state unknown"
            );
        }
        if op.resource.scope != "host" {
            op.resource = crate::platform_resources::verify_operation(op)?;
        } else {
            if op.input.action == "install-portable" {
                op.resource = match crate::portable::identify(op.resource.clone()) {
                    Ok(resource) => resource,
                    Err(error) => {
                        if error.to_string().starts_with("BROKEN_INSTALLATION:")
                            && op.registration_status != "failed"
                        {
                            op.reliability.failure_confirmed = true;
                        }
                        return Err(error);
                    }
                };
            }
            let lease_id = op
                .reliability
                .lease_id
                .clone()
                .context("Recovery lease missing")?;
            let verified = adapters::verify_with(op.resource.clone(), |pid| {
                let s = shared.lock().unwrap();
                let mut l = s
                    .leases()?
                    .into_iter()
                    .find(|l| l.id == lease_id)
                    .context("Lease lost")?;
                l.pid = Some(pid);
                l.process_stamp = platform::process_stamp(pid)?;
                l.process_job = Some(platform::process_job_name(pid)?);
                l.state = "verifying".into();
                s.save_lease(&l)
            })?;
            let s = shared.lock().unwrap();
            let mut l = s
                .leases()?
                .into_iter()
                .find(|l| l.id == lease_id)
                .context("Lease lost")?;
            l.pid = None;
            l.process_stamp = None;
            l.state = "reserved".into();
            s.save_lease(&l)?;
            drop(s);
            op.resource = verified;
        }
        if op.resource.status != "available" || !version_matches(op) {
            if new_host(op) && op.registration_status != "failed" {
                op.reliability.failure_confirmed = true;
                persist(shared, op)?;
                return cleanup(shared, op);
            }
            anyhow::bail!("Verification differs from plan; existing resource retained");
        }
        if !new_host(op) && op.resource.scope == "host" && op.registration_status != "failed" {
            shared.lock().unwrap().upsert(op.resource.clone())?;
            op.status = "failed".into();
            op.verification_status = "succeeded".into();
            op.reliability.recovery.completed_steps.push("Actual dependencies refreshed; original modification not assumed successful or replayed".into());
            return Ok(());
        }
    }
    op.reliability
        .recovery
        .completed_steps
        .push("actual-result-verified".into());
    op.execution_status = "succeeded".into();
    op.verification_status = "succeeded".into();
    op.registration_status = "pending".into();
    persist(shared, op)?;
    let registered = (|| -> Result<()> {
        let s = shared.lock().unwrap();
        let tx = s.db.unchecked_transaction()?;
        if op.input.action == "delete" {
            s.register_deletion(&op.resource)?;
        } else {
            anyhow::ensure!(
                s.upsert(op.resource.clone())?.is_some(),
                "Resource ignored; explicitly restore management"
            );
        }
        tx.commit()?;
        s.backup()?;
        Ok(())
    })();
    if let Err(e) = registered {
        op.registration_status = "failed".into();
        op.status = "registration_failed".into();
        return Err(e);
    }
    op.registration_status = "succeeded".into();
    op.reliability
        .recovery
        .completed_steps
        .push("registration-synchronized".into());
    op.status = "completed".into();
    Ok(())
}
pub fn start(shared: Shared, id: &str, only_cleanup: bool) -> Result<Operation> {
    start_with_mode(shared, id, only_cleanup, false)
}
fn start_with_mode(
    shared: Shared,
    id: &str,
    only_cleanup: bool,
    finalize_only: bool,
) -> Result<Operation> {
    let mut active = ACTIVE.get_or_init(Default::default).lock().unwrap();
    anyhow::ensure!(!active.contains(id), "Recovery already running");
    let mut op: Operation = { shared.lock().unwrap().get("operations", id)? };
    anyhow::ensure!(
        op.status != "executing" && op.status != "planned",
        "Operation not eligible for recovery"
    );
    if finalize_only {
        anyhow::ensure!(
            terminal(&op),
            "Operation changed; terminal-only startup recovery refused"
        );
        guard(&shared, &op)?;
        finalize(&shared, &mut op)?;
        return Ok(op);
    }
    let repair_completed_registration =
        if op.status == "completed" && !only_cleanup && !finalize_only {
            let s = shared.lock().unwrap();
            let registered = s.resources()?.iter().any(|resource| {
                resource.id == op.resource.id
                    || Store::identity(resource) == Store::identity(&op.resource)
            });
            s.recovery.is_some()
                || if op.input.action == "delete" {
                    registered
                } else {
                    !registered
                }
        } else {
            false
        };
    if (op.status == "completed" && !repair_completed_registration)
        || op.reliability.cleanup.status == "completed"
        || (op.status == "failed" && op.reliability.recovery.status == "completed")
    {
        guard(&shared, &op)?;
        finalize(&shared, &mut op)?;
        return Ok(op);
    }
    if repair_completed_registration {
        // A completed journal can outlive a rolled-back registry. Explicit recovery
        // verifies and synchronizes it, but must never clean a successful install
        // or replay its external steps, even if verification now fails.
        op.status = "registration_failed".into();
        op.registration_status = "failed".into();
        op.reliability.recovery.reason = Some(
            "Completed operation requires registration reconciliation; external steps will not replay"
                .into(),
        );
    }
    {
        let s = shared.lock().unwrap();
        let leases = s.leases()?;
        let existing = op
            .reliability
            .lease_id
            .as_ref()
            .and_then(|id| leases.iter().find(|l| &l.id == id && l.state != "released"));
        if existing.is_none() {
            if let Some(orphaned) = leases
                .iter()
                .find(|l| l.operation_id.as_deref() == Some(id) && l.state != "released")
            {
                op.reliability.lease_id = Some(orphaned.id.clone());
            } else {
                let mut ids = s.related_ids(&op.resource)?;
                ids.push(format!(
                    "path:{}",
                    crate::store::path_key(&op.resource.path)
                ));
                let lease = s.acquire_for_operation(
                    ids,
                    "write",
                    op.input.project.clone(),
                    Some(id.to_owned()),
                )?;
                op.reliability.lease_id = Some(lease.id);
            }
        }
    }
    op.reliability.recovery.status = "running".into();
    op.reliability.recovery.attempts += 1;
    persist(&shared, &mut op)?;
    active.insert(id.into());
    let returned = op.clone();
    std::thread::spawn(move || {
        let mut result = reconcile(&shared, &mut op, only_cleanup);
        if result.is_err()
            && op.reliability.failure_confirmed
            && new_host(&op)
            && op.reliability.cleanup.status == "not_started"
        {
            if op.error.is_none() {
                op.error = result.as_ref().err().map(|e| format!("{e:#}"));
            }
            result = cleanup(&shared, &mut op);
        }
        op.reliability.recovery.status = if result.is_ok() {
            "completed"
        } else {
            "blocked"
        }
        .into();
        op.reliability.recovery.reason = result.err().map(|e| format!("{e:#}"));
        // Never release protection based only on an in-memory successful result.
        let _ = finalize(&shared, &mut op);
        ACTIVE.get().unwrap().lock().unwrap().remove(&op.id);
    });
    Ok(returned)
}
pub fn startup(shared: Shared) {
    let eligible = {
        let s = shared.lock().unwrap();
        if s.recovery.is_some() {
            return;
        }
        if s.reclaim_unstarted_operation_leases().is_err() {
            return;
        }
        s.operations().unwrap_or_default()
    };
    std::thread::spawn(move || {
        for op in eligible {
            let final_lease_pending = if terminal(&op) {
                op.reliability.finalization.status == "blocked"
                    || op.reliability.finalization.status == "running"
                    || shared.lock().unwrap().leases().is_ok_and(|leases| {
                        leases.iter().any(|l| {
                            Some(&l.id) == op.reliability.lease_id.as_ref() && l.state != "released"
                        })
                    })
            } else {
                false
            };
            if final_lease_pending
                || matches!(op.status.as_str(), "interrupted" | "registration_failed")
                || (op.status == "failed"
                    && (!op.reliability.owned.is_empty()
                        || op.execution_status == "succeeded"
                        || op.reliability.failure_confirmed)
                    && op.reliability.cleanup.status != "completed"
                    && op.reliability.recovery.status != "completed")
            {
                // Startup terminal recovery is lease/metadata-only. A user may
                // have cancelled management after the lease was released; do not
                // turn a metadata retry into explicit registration repair.
                if let Err(error) =
                    start_with_mode(shared.clone(), &op.id, false, final_lease_pending)
                {
                    // start/finalize may already have persisted newer evidence. Never
                    // overwrite it with the startup snapshot or erase a completed recovery.
                    let mut op = shared
                        .lock()
                        .unwrap()
                        .get("operations", &op.id)
                        .unwrap_or(op);
                    if terminal(&op) {
                        op.reliability.finalization.status = "blocked".into();
                        op.reliability.finalization.reason = Some(format!("{error:#}"));
                    } else {
                        op.reliability.recovery.status = "blocked".into();
                        op.reliability.recovery.reason = Some(format!("{error:#}"));
                    }
                    let _ = persist(&shared, &mut op);
                }
            }
        }
    });
}
pub fn summary(s: &Store) -> Result<serde_json::Value> {
    let ops = s.operations()?;
    let leases = s.leases()?;
    let pending = ops
        .iter()
        .filter(|o| {
            matches!(o.status.as_str(), "interrupted" | "registration_failed")
                || o.reliability.recovery.status == "running"
                || matches!(
                    o.reliability.finalization.status.as_str(),
                    "blocked" | "running"
                )
                || (terminal(o)
                    && leases.iter().any(|l| {
                        Some(&l.id) == o.reliability.lease_id.as_ref() && l.state != "released"
                    }))
                || (o.status == "failed"
                    && o.reliability.cleanup.status != "completed"
                    && o.reliability.recovery.status != "completed"
                    && (!o.reliability.owned.is_empty() || o.reliability.failure_confirmed))
        })
        .count();
    let blocked = ops
        .iter()
        .filter(|o| {
            o.reliability.recovery.status == "blocked"
                || o.reliability.cleanup.status == "blocked"
                || o.reliability.finalization.status == "blocked"
        })
        .count();
    Ok(json!({"pending":pending,"blocked":blocked}))
}

fn normalized_path(path: &Path) -> Result<String> {
    if path.try_exists()? {
        return Ok(crate::store::path_key(
            &platform::canonical(path)?.to_string_lossy(),
        ));
    }
    let parent = path.parent().context("Missing path parent")?;
    Ok(format!(
        "{}\\{}",
        normalized_path(parent)?,
        path.file_name()
            .context("Missing path name")?
            .to_string_lossy()
            .to_lowercase()
    ))
}

#[cfg(test)]
mod deletion_entry_tests {
    use super::entry_exists;
    use std::path::Path;

    #[cfg(unix)]
    #[test]
    fn dangling_symlink_is_still_a_directory_entry() {
        use std::os::unix::fs::symlink;

        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("missing-target");
        let link = tmp.path().join("deleted-resource");
        symlink(&target, &link).unwrap();

        assert!(!link.exists());
        assert!(entry_exists(Path::new(&link)).unwrap());
    }

    #[cfg(windows)]
    #[test]
    fn dangling_symlink_is_still_a_directory_entry() {
        use std::os::windows::fs::symlink_file;

        let tmp = tempfile::tempdir().unwrap();
        let target = tmp.path().join("missing-target");
        let link = tmp.path().join("deleted-resource");
        symlink_file(&target, &link).unwrap();

        assert!(!link.exists());
        assert!(entry_exists(Path::new(&link)).unwrap());
    }
}

#[cfg(test)]
mod finalization_tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    fn fixture() -> (tempfile::TempDir, Shared, Operation) {
        let tmp = tempfile::tempdir().unwrap();
        let mut store = Store::open(&tmp.path().join("data")).unwrap();
        let root = tmp.path().join("tools");
        fs::create_dir(&root).unwrap();
        store.config.tools_root = Some(root.clone());
        store.save_config(store.config.clone()).unwrap();
        let target = root.join("installed");
        fs::create_dir(&target).unwrap();
        let resource = Resource::new("tool", "cli", "test-tool", &target.to_string_lossy());
        let lease = store
            .acquire(vec![resource.id.clone()], "write", None)
            .unwrap();
        let mut op: Operation = serde_json::from_value(json!({
            "id":id(),"input":{"action":"install-portable","packages":[]},
            "resource":resource,"fingerprint":null,"risk":"low","requirements":[],
            "effects":[],"steps":[],"status":"interrupted","execution_status":"succeeded",
            "verification_status":"succeeded","registration_status":"pending",
            "created_at":now(),"updated_at":now(),"error":null,"attempts":1
        }))
        .unwrap();
        op.reliability.lease_id = Some(lease.id);
        let shared = Arc::new(Mutex::new(store));
        own(&shared, &mut op, &target, "target").unwrap();
        (tmp, shared, op)
    }

    fn assert_protected(shared: &Shared, op: &Operation) {
        let s = shared.lock().unwrap();
        assert!(s.leases().unwrap().iter().any(|lease| {
            Some(&lease.id) == op.reliability.lease_id.as_ref() && lease.state == "reserved"
        }));
        assert!(
            s.acquire(vec![op.resource.id.clone()], "read", None)
                .is_err()
        );
    }

    #[test]
    fn startup_finalize_only_refuses_a_nonterminal_operation() {
        let (_tmp, shared, op) = fixture();
        let error = start_with_mode(shared.clone(), &op.id, false, true).unwrap_err();
        assert!(error.to_string().contains("terminal-only"));
        assert_protected(&shared, &op);
        let saved: Operation = shared.lock().unwrap().get("operations", &op.id).unwrap();
        assert_eq!(saved.reliability.recovery.status, "not_started");
        assert!(Path::new(&op.resource.path).exists());
    }

    #[test]
    fn final_journal_write_failure_keeps_exclusive_protection() {
        let (_tmp, shared, mut op) = fixture();
        shared.lock().unwrap().upsert(op.resource.clone()).unwrap();
        let journal = shared
            .lock()
            .unwrap()
            .root
            .join("pending")
            .join(format!("{}.json", op.id));
        fs::remove_file(&journal).unwrap();
        fs::create_dir(&journal).unwrap(); // Force the atomic journal replacement to fail.
        op.status = "completed".into();
        op.registration_status = "succeeded".into();
        assert!(finalize(&shared, &mut op).is_err());
        assert_protected(&shared, &op);
        assert_eq!(op.reliability.finalization.status, "blocked");
        let saved: Operation = shared.lock().unwrap().get("operations", &op.id).unwrap();
        assert_eq!(saved.status, "interrupted");
    }

    #[test]
    fn final_database_write_failure_keeps_protection_and_restart_only_finishes_commit() {
        let (tmp, shared, mut op) = fixture();
        {
            let s = shared.lock().unwrap();
            s.upsert(op.resource.clone()).unwrap();
            s.db.execute_batch("CREATE TRIGGER fail_final BEFORE UPDATE ON operations WHEN json_extract(NEW.body, '$.status')='completed' BEGIN SELECT RAISE(FAIL, 'injected final write failure'); END;").unwrap();
        }
        op.status = "completed".into();
        op.registration_status = "succeeded".into();
        op.reliability.recovery.status = "completed".into();
        assert!(finalize(&shared, &mut op).is_err());
        assert_protected(&shared, &op);
        shared
            .lock()
            .unwrap()
            .db
            .execute_batch("DROP TRIGGER fail_final")
            .unwrap();
        drop(shared);
        let shared = Arc::new(Mutex::new(Store::open(&tmp.path().join("data")).unwrap()));
        // The committed registry and durable journal agree. A now-unusable tool
        // must not be reclassified as a failed install during lease-only recovery.
        let sentinel = Path::new(&op.resource.path).join("user-data.txt");
        fs::write(&sentinel, b"keep").unwrap();
        start(shared.clone(), &op.id, false).unwrap();
        {
            let s = shared.lock().unwrap();
            let saved: Operation = s.get("operations", &op.id).unwrap();
            assert_eq!(saved.status, "completed");
            assert_eq!(saved.reliability.finalization.status, "completed");
            assert!(
                s.leases()
                    .unwrap()
                    .iter()
                    .all(|lease| lease.state == "released")
            );
            s.ignore(&op.resource.id).unwrap();
        }
        drop(shared);
        let shared = Arc::new(Mutex::new(Store::open(&tmp.path().join("data")).unwrap()));
        startup(shared.clone());
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert_eq!(fs::read(&sentinel).unwrap(), b"keep");
        assert!(shared.lock().unwrap().resources().unwrap().is_empty());
    }

    #[test]
    fn stale_failed_install_cannot_cleanup_explicitly_ignored_instance() {
        let (_tmp, shared, mut op) = fixture();
        {
            let s = shared.lock().unwrap();
            s.upsert(op.resource.clone()).unwrap();
            s.ignore(&op.resource.id).unwrap();
        }
        let sentinel = Path::new(&op.resource.path).join("user-data.txt");
        fs::write(&sentinel, b"keep").unwrap();
        op.reliability.failure_confirmed = true;
        assert!(cleanup(&shared, &mut op).is_err());
        assert!(
            op.reliability
                .cleanup
                .reason
                .as_deref()
                .unwrap()
                .contains("explicitly cancelled")
        );
        assert_eq!(fs::read(sentinel).unwrap(), b"keep");
        assert_protected(&shared, &op);
    }
}
