use airoot::{model::*, recovery, scan, store::Store};
use serde_json::json;
use std::{
    fs,
    path::Path,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
type Shared = Arc<Mutex<Store>>;
fn fixture() -> (tempfile::TempDir, Shared, Operation) {
    let tmp = tempfile::tempdir().unwrap();
    let mut s = Store::open(&tmp.path().join("data")).unwrap();
    let root = tmp.path().join("tools");
    fs::create_dir(&root).unwrap();
    s.config.tools_root = Some(root.clone());
    s.config.env_root = Some(root.clone());
    s.save_config(s.config.clone()).unwrap();
    let target = root.join("任务 sample");
    fs::create_dir(&target).unwrap();
    let r = Resource::new("tool", "cli", "fd", &target.to_string_lossy());
    let input: PlanInput =
        serde_json::from_value(json!({"action":"install-portable","packages":[]})).unwrap();
    let mut op = Operation {
        id: id(),
        input,
        resource: r,
        fingerprint: None,
        risk: "low".into(),
        requirements: vec![],
        effects: vec![],
        steps: vec![],
        status: "failed".into(),
        execution_status: "failed".into(),
        verification_status: "not_started".into(),
        registration_status: "not_started".into(),
        created_at: now(),
        updated_at: now(),
        error: Some("original installation error".into()),
        attempts: 1,
        reliability: Default::default(),
    };
    let lease = s
        .acquire(vec![op.resource.id.clone()], "write", None)
        .unwrap();
    op.reliability.lease_id = Some(lease.id);
    let shared = Arc::new(Mutex::new(s));
    recovery::own(&shared, &mut op, &target, "target").unwrap();
    (tmp, shared, op)
}
fn wait(shared: &Shared, id: &str) -> Operation {
    let end = Instant::now() + Duration::from_secs(15);
    loop {
        let op: Operation = shared.lock().unwrap().get("operations", id).unwrap();
        if op.reliability.recovery.status != "running" {
            return op;
        }
        assert!(Instant::now() < end, "recovery timeout");
        std::thread::sleep(Duration::from_millis(20));
    }
}
#[test]
fn owned_failure_cleanup_is_idempotent_and_preserves_original_error() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    fs::write(Path::new(&op.resource.path).join("partial"), b"x").unwrap();
    recovery::cleanup(&s, &mut op).unwrap();
    recovery::cleanup(&s, &mut op).unwrap();
    assert!(!Path::new(&op.resource.path).exists());
    assert_eq!(op.error.as_deref(), Some("original installation error"));
    assert_eq!(op.reliability.cleanup.completed_steps.len(), 1);
}
#[test]
fn replaced_directory_is_not_owned() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let path = std::path::PathBuf::from(&op.resource.path);
    fs::rename(&path, path.with_file_name("original")).unwrap();
    fs::create_dir(&path).unwrap();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(path.exists());
    assert_eq!(op.reliability.cleanup.status, "blocked");
}
#[cfg(unix)]
#[test]
fn dangling_symlink_replacement_blocks_owned_cleanup() {
    use std::os::unix::fs::symlink;

    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let path = std::path::PathBuf::from(&op.resource.path);
    fs::remove_dir(&path).unwrap();
    symlink(path.with_file_name("missing-target"), &path).unwrap();

    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(fs::symlink_metadata(path).unwrap().file_type().is_symlink());
    assert!(!op.reliability.owned[0].removed);
    assert_eq!(op.reliability.cleanup.status, "blocked");
}
#[cfg(windows)]
#[test]
fn dangling_symlink_replacement_blocks_owned_cleanup() {
    use std::os::windows::fs::symlink_dir;

    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let path = std::path::PathBuf::from(&op.resource.path);
    fs::remove_dir(&path).unwrap();
    symlink_dir(path.with_file_name("missing-target"), &path).unwrap();

    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(fs::symlink_metadata(path).unwrap().file_type().is_symlink());
    assert!(!op.reliability.owned[0].removed);
    assert_eq!(op.reliability.cleanup.status, "blocked");
}
#[test]
fn registration_failure_and_unknown_result_preserved() {
    let (_t, s, mut op) = fixture();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    op.reliability.failure_confirmed = true;
    op.registration_status = "failed".into();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(Path::new(&op.resource.path).exists());
}
#[test]
fn legacy_ownership_and_existing_environments_never_deleted() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    op.reliability.owned.clear();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    op.input.action = "install".into();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(Path::new(&op.resource.path).exists());
}
#[test]
fn shared_registered_child_blocks_cleanup() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let r = Resource::new(
        "tool",
        "cli",
        "other",
        &format!("{}\\child", op.resource.path),
    );
    s.lock().unwrap().upsert(r).unwrap();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(Path::new(&op.resource.path).exists());
}
#[test]
fn path_only_nested_lease_blocks_owned_cleanup() {
    let (_tmp, shared, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let nested = Path::new(&op.resource.path).join("nested");
    let path_id = format!(
        "path:{}",
        airoot::store::path_key(&nested.to_string_lossy())
    );
    shared
        .lock()
        .unwrap()
        .acquire(vec![path_id], "read", None)
        .unwrap();
    assert!(recovery::cleanup(&shared, &mut op).is_err());
    assert!(Path::new(&op.resource.path).exists());
    assert_eq!(op.reliability.cleanup.status, "blocked");
}

#[test]
fn resource_run_missing_entry_does_not_leave_reserved_lease() {
    let tmp = tempfile::tempdir().unwrap();
    let store = Store::open(&tmp.path().join("data")).unwrap();
    let mut resource = Resource::new("tool", "cli", "missing-entry", "C:\\tools\\missing-entry");
    resource.status = "available".into();
    let id = resource.id.clone();
    store.upsert(resource).unwrap();
    let shared = Arc::new(Mutex::new(store));

    assert!(airoot::operations::run(shared.clone(), &id, vec![], None).is_err());
    assert!(shared.lock().unwrap().leases().unwrap().is_empty());
}

#[test]
fn usage_acquire_rejects_pid_overflow_without_wrapping_to_live_process() {
    let tmp = tempfile::tempdir().unwrap();
    let tool = tmp.path().join("tool");
    fs::create_dir(&tool).unwrap();
    let store = Store::open(&tmp.path().join("data")).unwrap();
    let mut resource = Resource::new("tool", "cli", "sample", &tool.to_string_lossy());
    resource.status = "available".into();
    let id = store.upsert(resource).unwrap().unwrap().id;
    let shared = Arc::new(Mutex::new(store));
    let wrapped_pid = u64::from(std::process::id()) + (u64::from(u32::MAX) + 1);

    let result = airoot::engine::dispatch(
        shared.clone(),
        Request {
            command: "usage.acquire".into(),
            args: json!({"id":id,"pid":wrapped_pid}),
        },
    );

    assert!(result.is_err());
    assert!(shared.lock().unwrap().leases().unwrap().is_empty());
}

#[test]
fn usage_acquire_persists_complete_external_lease_in_one_record() {
    let tmp = tempfile::tempdir().unwrap();
    let tool = tmp.path().join("tool");
    fs::create_dir(&tool).unwrap();
    let store = Store::open(&tmp.path().join("data")).unwrap();
    let mut resource = Resource::new("tool", "cli", "sample", &tool.to_string_lossy());
    resource.status = "available".into();
    let id = store.upsert(resource).unwrap().unwrap().id;
    let shared = Arc::new(Mutex::new(store));
    let pid = std::process::id();
    let stamp = airoot::platform::process_stamp(pid).unwrap().unwrap();

    let lease = airoot::engine::dispatch(
        shared.clone(),
        Request {
            command: "usage.acquire".into(),
            args: json!({"id":id,"pid":pid}),
        },
    )
    .unwrap();

    assert_eq!(lease["state"], "external");
    assert_eq!(lease["pid"], pid);
    assert_eq!(lease["process_stamp"], stamp);
    let leases = shared.lock().unwrap().leases().unwrap();
    assert_eq!(leases.len(), 1);
    assert_eq!(leases[0].state, "external");
}

#[test]
fn user_archive_and_shared_cache_preserved() {
    let (t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let archive = t.path().join("user.zip");
    fs::write(&archive, b"user").unwrap();
    let cache = t.path().join("tools/.cache");
    fs::create_dir(&cache).unwrap();
    fs::write(cache.join("shared"), b"keep").unwrap();
    op.input.archive = Some(archive.clone());
    recovery::cleanup(&s, &mut op).unwrap();
    assert!(archive.exists() && cache.join("shared").exists());
}
#[test]
fn locked_file_cleanup_retries_without_losing_error() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let p = Path::new(&op.resource.path).join("held");
    fs::write(&p, b"x").unwrap();
    use std::os::windows::fs::OpenOptionsExt;
    let handle = fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(&p)
        .unwrap();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert_eq!(op.error.as_deref(), Some("original installation error"));
    drop(handle);
    recovery::cleanup(&s, &mut op).unwrap();
    assert_eq!(op.reliability.cleanup.status, "completed");
}
#[test]
fn log_pruning_preserves_active_recovery_and_cleanup_logs() {
    for phase in ["recovery", "cleanup", "finalization"] {
        let (_tmp, shared, mut op) = fixture();
        // The main operation is failed, but its recovery/cleanup is still active.
        if phase == "recovery" {
            op.reliability.recovery.status = "running".into();
        } else if phase == "cleanup" {
            op.reliability.cleanup.status = "running".into();
        } else {
            op.reliability.finalization.status = "running".into();
        }
        let mut store = shared.lock().unwrap();
        store.config.log_limit_bytes = 0;
        store.save("operations", &op.id, &op).unwrap();
        let log = store.root.join("logs").join(format!("{}.jsonl", op.id));
        let child_log = store
            .root
            .join("logs")
            .join(format!("{}-vendor.log", op.id));
        let inactive_log = store.root.join("logs").join("inactive.jsonl");
        fs::write(&log, b"active progress").unwrap();
        fs::write(&child_log, b"active vendor output").unwrap();
        fs::write(&inactive_log, b"finished").unwrap();
        store.prune_logs().unwrap();
        assert!(log.exists(), "Pruned active {phase} log");
        assert!(child_log.exists(), "Pruned active {phase} vendor log");
        assert!(!inactive_log.exists());
    }
}
#[test]
fn corrupt_journal_quarantined_and_remains_blocked_across_restart() {
    let t = tempfile::tempdir().unwrap();
    drop(Store::open(t.path()).unwrap());
    fs::write(t.path().join("pending/broken.json"), b"{truncated").unwrap();
    assert!(Store::open(t.path()).unwrap().recovery.is_some());
    assert!(Store::open(t.path()).unwrap().recovery.is_some());
    assert_eq!(fs::read_dir(t.path().join("recovery")).unwrap().count(), 1);
}
#[test]
fn old_operation_and_scan_json_defaults() {
    let (_t, _s, op) = fixture();
    let mut v = serde_json::to_value(op).unwrap();
    v.as_object_mut().unwrap().remove("reliability");
    let op: Operation = serde_json::from_value(v).unwrap();
    assert!(op.reliability.owned.is_empty());
    assert_eq!(op.reliability.cleanup.status, "not_started");
    let scan:Scan=serde_json::from_value(json!({"id":"old","status":"paused","roots":[],"visited":1,"discovered":0,"skipped":[],"providers":[],"created_at":"","updated_at":""})).unwrap();
    assert!(scan.coverage.is_null());
}
#[test]
fn restart_before_ownership_checkpoint_preserves_unknown_target_and_protection() {
    for (action, kind) in [
        ("create", "venv"),
        ("create", "conda"),
        ("install-portable", "cli"),
        ("bootstrap", "python"),
    ] {
        for replaced in [false, true] {
            let (tmp, shared, mut op) = fixture();
            let target = Path::new(&op.resource.path).to_path_buf();
            if replaced {
                fs::rename(&target, target.with_file_name("original-target")).unwrap();
                fs::create_dir(&target).unwrap();
            }
            let sentinel = target.join("unproven-owner.txt");
            fs::write(&sentinel, b"preserve this object").unwrap();
            let identity = recovery::identity(&target).unwrap();
            op.input.action = action.into();
            op.resource.kind = kind.into();
            op.resource.class = if kind == "cli" { "tool" } else { "env" }.into();
            op.status = "executing".into();
            op.execution_status = "preparing".into();
            op.error = None;
            // Simulate the durable state before create_dir -> own completed.
            op.reliability.owned.clear();
            op.steps.push(Step {
                program: "must-never-replay-installation.exe".into(),
                args: vec![],
                environment: Default::default(),
            });
            recovery::persist(&shared, &mut op).unwrap();
            drop(shared);

            let shared = Arc::new(Mutex::new(Store::open(&tmp.path().join("data")).unwrap()));
            recovery::startup(shared.clone());
            let deadline = Instant::now() + Duration::from_secs(15);
            let restored = loop {
                let current: Operation = shared.lock().unwrap().get("operations", &op.id).unwrap();
                if current.reliability.recovery.status == "blocked" {
                    break current;
                }
                assert!(Instant::now() < deadline, "startup recovery did not finish");
                std::thread::sleep(Duration::from_millis(20));
            };
            assert!(
                restored
                    .reliability
                    .recovery
                    .reason
                    .as_deref()
                    .unwrap()
                    .contains("Ownership evidence missing")
            );
            assert!(!restored.reliability.failure_confirmed);
            assert_eq!(restored.reliability.cleanup.status, "not_started");
            assert!(restored.reliability.owned.is_empty());
            assert_eq!(recovery::identity(&target).unwrap(), identity);
            assert_eq!(fs::read(&sentinel).unwrap(), b"preserve this object");
            let store = shared.lock().unwrap();
            assert!(store.resources().unwrap().is_empty());
            assert!(
                store
                    .acquire(vec![op.resource.id.clone()], "write", None)
                    .is_err()
            );
            assert!(store.leases().unwrap().iter().any(|lease| {
                Some(&lease.id) == restored.reliability.lease_id.as_ref()
                    && lease.state != "released"
            }));
        }
    }
}
#[test]
fn interrupted_delete_only_synchronizes_registry() {
    let (_t, s, mut op) = fixture();
    let r = s
        .lock()
        .unwrap()
        .upsert(op.resource.clone())
        .unwrap()
        .unwrap();
    op.resource = r;
    op.input.action = "delete".into();
    op.status = "interrupted".into();
    fs::remove_dir(&op.resource.path).unwrap();
    recovery::persist(&s, &mut op).unwrap();
    recovery::start(s.clone(), &op.id, false).unwrap();
    let op = wait(&s, &op.id);
    assert_eq!(op.status, "completed");
    assert!(s.lock().unwrap().resource(&op.resource.id).is_err());
}
#[test]
fn incomplete_delete_is_not_replayed() {
    let (_t, s, mut op) = fixture();
    op.input.action = "delete".into();
    op.status = "interrupted".into();
    recovery::persist(&s, &mut op).unwrap();
    recovery::start(s.clone(), &op.id, false).unwrap();
    let op = wait(&s, &op.id);
    assert_eq!(op.reliability.recovery.status, "blocked");
    assert!(Path::new(&op.resource.path).exists());
}
#[test]
fn successful_install_registration_is_repaired_without_install_steps() {
    let (_t, s, mut op) = fixture();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        Path::new(&op.resource.path).join("fd.exe"),
    )
    .unwrap();
    op.resource.projects.push("project".into());
    op.status = "registration_failed".into();
    op.registration_status = "failed".into();
    op.execution_status = "succeeded".into();
    op.steps.push(Step {
        program: "must-never-run.exe".into(),
        args: vec![],
        environment: Default::default(),
    });
    recovery::persist(&s, &mut op).unwrap();
    recovery::start(s.clone(), &op.id, false).unwrap();
    let op = wait(&s, &op.id);
    assert_eq!(op.status, "completed", "{:?}", op.reliability);
    assert_eq!(
        s.lock()
            .unwrap()
            .resource(&op.resource.id)
            .unwrap()
            .projects,
        vec!["project"]
    );
}
#[test]
fn confirmed_broken_interrupted_portable_is_cleaned() {
    let (_t, s, mut op) = fixture();
    op.status = "interrupted".into();
    recovery::persist(&s, &mut op).unwrap();
    recovery::start(s.clone(), &op.id, false).unwrap();
    let op = wait(&s, &op.id);
    assert_eq!(
        op.reliability.cleanup.status, "completed",
        "{:?}",
        op.reliability
    );
    assert!(!Path::new(&op.resource.path).exists());
}
#[test]
fn queue_completion_distinguishes_coverage_gaps() {
    let (t, s, _op) = fixture();
    let root = t.path().join("tools");
    let scan = Scan {
        id: id(),
        status: "running".into(),
        roots: vec![root],
        visited: 0,
        discovered: 0,
        skipped: vec![json!({"path":"unreadable","reason":"access denied"})],
        providers: vec![],
        coverage: Default::default(),
        created_at: now(),
        updated_at: now(),
    };
    s.lock().unwrap().save("scans", &scan.id, &scan).unwrap();
    scan::worker(s.clone(), &scan.id, None).unwrap();
    let scan: Scan = s.lock().unwrap().get("scans", &scan.id).unwrap();
    assert_eq!(scan.coverage["queue_complete"], true);
    assert_eq!(scan.coverage["state"], "partial");
}
#[test]
fn successful_platform_probes_do_not_create_coverage_gaps() {
    let (t, s, _op) = fixture();
    let root = t.path().join("tools");
    let scan = Scan {
        id: id(),
        status: "running".into(),
        roots: vec![root],
        visited: 0,
        discovered: 0,
        skipped: vec![],
        providers: vec![
            json!({"name":"wsl","status":"queried"}),
            json!({"name":"docker","status":"queried"}),
        ],
        coverage: Default::default(),
        created_at: now(),
        updated_at: now(),
    };
    s.lock().unwrap().save("scans", &scan.id, &scan).unwrap();
    scan::worker(s.clone(), &scan.id, None).unwrap();
    let scan: Scan = s.lock().unwrap().get("scans", &scan.id).unwrap();
    assert_eq!(scan.coverage["state"], "supported_signatures_checked");
    assert_eq!(scan.coverage["uncovered"].as_array().unwrap().len(), 0);
    assert_eq!(scan.status, "completed");
}
#[test]
fn restart_preserves_paused_queue_and_original_error() {
    let (t, s, mut op) = fixture();
    op.status = "executing".into();
    recovery::persist(&s, &mut op).unwrap();
    let scan = Scan {
        id: id(),
        status: "paused".into(),
        roots: vec![],
        visited: 3,
        discovered: 1,
        skipped: vec![],
        providers: vec![],
        coverage: json!({"state":"in_progress"}),
        created_at: now(),
        updated_at: now(),
    };
    {
        let s = s.lock().unwrap();
        s.save("scans", &scan.id, &scan).unwrap();
        s.db.execute(
            "INSERT INTO scan_queue(scan_id,path) VALUES(?1,?2)",
            [&scan.id, "pending"],
        )
        .unwrap();
    }
    drop(s);
    let s = Store::open(&t.path().join("data")).unwrap();
    let restored: Operation = s.get("operations", &op.id).unwrap();
    assert_eq!(restored.error, op.error);
    assert_eq!(restored.status, "interrupted");
    assert_eq!(s.get::<Scan>("scans", &scan.id).unwrap().status, "paused");
}
#[test]
fn orphaned_unstarted_operation_lease_is_reclaimed_and_legacy_leases_load() {
    let (t, shared, mut op) = fixture();
    let store = shared.lock().unwrap();
    if let Some(id) = op.reliability.lease_id.take() {
        let mut previous = store
            .leases()
            .unwrap()
            .into_iter()
            .find(|lease| lease.id == id)
            .unwrap();
        store.finish_lease(&mut previous).unwrap();
    }
    op.status = "planned".into();
    store.save("operations", &op.id, &op).unwrap();
    airoot::store::atomic_json(
        &store.root.join("pending").join(format!("{}.json", op.id)),
        &op,
    )
    .unwrap();
    let orphan = store
        .acquire_for_operation(
            vec![format!("path:{}", op.resource.path)],
            "write",
            None,
            Some(op.id.clone()),
        )
        .unwrap();
    drop(store);
    drop(shared);

    let reopened = Store::open(&t.path().join("data")).unwrap();
    assert_eq!(reopened.reclaim_unstarted_operation_leases().unwrap(), 1);
    let persisted = reopened
        .leases()
        .unwrap()
        .into_iter()
        .find(|lease| lease.id == orphan.id)
        .unwrap();
    assert_eq!(persisted.state, "released");

    let legacy: Lease = serde_json::from_value(json!({
        "id":"legacy",
        "resource_ids":[],
        "mode":"read",
        "pid":null,
        "process_stamp":null,
        "project":null,
        "state":"reserved",
        "created_at":"old"
    }))
    .unwrap();
    assert!(legacy.operation_id.is_none());
}
#[test]
fn interrupted_recovery_reuses_lease_written_before_operation_checkpoint() {
    let (_t, shared, mut op) = fixture();
    let store = shared.lock().unwrap();
    if let Some(id) = op.reliability.lease_id.take() {
        let mut previous = store
            .leases()
            .unwrap()
            .into_iter()
            .find(|lease| lease.id == id)
            .unwrap();
        store.finish_lease(&mut previous).unwrap();
    }
    op.status = "interrupted".into();
    drop(store);
    recovery::persist(&shared, &mut op).unwrap();
    let orphan = shared
        .lock()
        .unwrap()
        .acquire_for_operation(
            vec![op.resource.id.clone()],
            "write",
            None,
            Some(op.id.clone()),
        )
        .unwrap();

    let resumed = recovery::start(shared.clone(), &op.id, false).unwrap();
    assert_eq!(
        resumed.reliability.lease_id.as_deref(),
        Some(orphan.id.as_str())
    );
    let settled = wait(&shared, &op.id);
    assert_ne!(settled.reliability.recovery.status, "running");
}
#[test]
fn discovery_timeout_keeps_partial_stdout() {
    let out = airoot::platform::discovery_run(
        "powershell.exe",
        &[
            "-NoProfile".into(),
            "-Command".into(),
            "Write-Output 'known candidate'; Start-Sleep 10".into(),
        ],
        Duration::from_millis(1500),
    )
    .unwrap();
    assert!(out.timed_out);
    assert!(out.stdout.contains("known candidate"));
}
#[test]
fn restored_database_does_not_replay_old_operations() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    s.backup().unwrap();
    drop(s);
    fs::write(t.path().join("registry.sqlite3"), b"broken").unwrap();
    assert!(Store::open(t.path()).unwrap().recovery.is_some());
    assert!(Store::open(t.path()).unwrap().recovery.is_some());
}
#[test]
fn process_and_named_job_protection_requires_actual_exit() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let lease_id = op.reliability.lease_id.clone().unwrap();
    let worker_s = s.clone();
    let (tx, rx) = std::sync::mpsc::channel();
    let task = std::thread::spawn(move || {
        airoot::platform::run(
            "powershell.exe",
            &[
                "-NoProfile".into(),
                "-Command".into(),
                "Start-Sleep -Seconds 2".into(),
            ],
            Duration::from_secs(10),
            |pid| {
                let store = worker_s.lock().unwrap();
                let mut l = store
                    .leases()?
                    .into_iter()
                    .find(|l| l.id == lease_id)
                    .unwrap();
                l.pid = Some(pid);
                l.process_stamp = airoot::platform::process_stamp(pid)?;
                l.process_job = Some(airoot::platform::process_job_name(pid)?);
                l.state = "running".into();
                store.save_lease(&l)?;
                tx.send(()).unwrap();
                Ok(())
            },
        )
        .unwrap()
    });
    rx.recv().unwrap();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(Path::new(&op.resource.path).exists());
    task.join().unwrap();
    recovery::cleanup(&s, &mut op).unwrap();
    assert!(!Path::new(&op.resource.path).exists());
}
#[test]
fn candidate_limit_is_reported_per_root_and_does_not_claim_coverage() {
    let (t, s, _) = fixture();
    let root = t.path().join("candidates");
    fs::create_dir(&root).unwrap();
    for n in 0..10020 {
        let dir = root.join(n.to_string());
        fs::create_dir(&dir).unwrap();
        fs::write(dir.join("pyvenv.cfg"), b"home = fixture").unwrap();
    }
    let mut s = s.lock().unwrap();
    s.config.component_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("components");
    let root = airoot::platform::canonical(&root).unwrap();
    let batch = airoot::components::candidates(&s, "fd", std::slice::from_ref(&root)).unwrap();
    assert!(batch.truncated);
    assert_eq!(batch.paths.len(), 10000);
    assert!(batch.paths.iter().all(|p| p.starts_with(&root)));
    assert!(batch.reason.is_some());
}
#[test]
fn linked_owned_tree_is_retained() {
    let (t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let other = t.path().join("outside");
    fs::create_dir(&other).unwrap();
    fs::write(other.join("keep"), b"safe").unwrap();
    std::os::windows::fs::symlink_dir(&other, Path::new(&op.resource.path).join("link")).unwrap();
    assert!(recovery::cleanup(&s, &mut op).is_err());
    assert!(other.join("keep").exists());
}
#[test]
fn vendor_identity_validation_is_independent_of_restart_state_for_both_recipes() {
    for kind in ["python", "conda"] {
        let (_t, _s, mut op) = fixture();
        op.input.action = "bootstrap".into();
        op.resource.kind = kind.into();
        op.reliability.vendor =
            json!({"before":[],"after":[{"product":"ours"}],"installer_started":true});
        assert!(
            airoot::bootstrap::validate_vendor_records(&op, &json!([{"product":"ours"}])).is_ok()
        );
        assert!(
            airoot::bootstrap::validate_vendor_records(&op, &json!([{"product":"someone-else"}]))
                .is_err()
        );
        op.resource.metadata["reboot_required"] = json!(true);
        // Product identity checks must not bypass or preempt restart reconciliation.
        assert!(
            airoot::bootstrap::validate_vendor_records(&op, &json!([{"product":"ours"}])).is_ok()
        );
        assert!(
            airoot::bootstrap::reconcile_vendor_uninstall(
                &mut op,
                &json!([{"product":"ours"}]),
                Some(50),
                None
            )
            .is_err()
        );
        op.resource.metadata["reboot_required"] = json!(false);
        op.reliability
            .vendor
            .as_object_mut()
            .unwrap()
            .remove("after");
        assert!(airoot::bootstrap::validate_vendor_records(&op, &json!([])).is_err());
        op.reliability.vendor = json!({"before":[{"shared":true}],"after":[]});
        assert!(airoot::bootstrap::validate_vendor_records(&op, &json!([])).is_err());
    }
}

#[test]
fn vendor_uninstall_recovery_never_replays_uncertain_or_successful_attempts() {
    let (_t, _s, mut op) = fixture();
    op.input.action = "bootstrap".into();
    op.reliability.vendor = json!({
        "before": [],
        "after": [{"product":"ours"}],
        "installer_started": true,
        "uninstall_started": true,
        "uninstall_attempt_state": "running",
        "uninstall_attempts": 1
    });

    let error = airoot::bootstrap::reconcile_vendor_uninstall(
        &mut op,
        &json!([{"product":"ours"}]),
        Some(50),
        None,
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("outcome is unknown"));
    assert_ne!(op.reliability.vendor["uninstall_completed"], json!(true));

    op.reliability.vendor["uninstall_attempt_state"] = json!("returned");
    op.reliability.vendor["uninstall_exit_code"] = json!(0);
    assert!(
        airoot::bootstrap::reconcile_vendor_uninstall(&mut op, &json!([]), Some(50), None).unwrap()
    );
    assert_eq!(op.reliability.vendor["uninstall_completed"], json!(true));

    op.reliability.vendor["uninstall_completed"] = json!(false);
    let error = airoot::bootstrap::reconcile_vendor_uninstall(
        &mut op,
        &json!([{"product":"ours"}]),
        Some(50),
        None,
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("records remain"));
}

#[test]
fn vendor_3010_recovery_requires_a_later_boot_and_missing_product_records() {
    let (_t, _s, mut op) = fixture();
    op.input.action = "bootstrap".into();
    op.resource.metadata["reboot_required"] = json!(true);
    op.reliability.vendor = json!({
        "before": [],
        "after": [{"product":"ours"}],
        "installer_started": true,
        "uninstall_started": true,
        "uninstall_attempt_state": "returned",
        "uninstall_attempts": 1,
        "uninstall_exit_code": 3010,
        "reboot_uptime_ms": 1000
    });

    let error =
        airoot::bootstrap::reconcile_vendor_uninstall(&mut op, &json!([]), Some(1200), None)
            .unwrap_err()
            .to_string();
    assert!(error.contains("Restart required"));
    assert_eq!(op.reliability.vendor["uninstall_completed"], json!(null));

    let error = airoot::bootstrap::reconcile_vendor_uninstall(
        &mut op,
        &json!([{"product":"ours"}]),
        Some(100),
        None,
    )
    .unwrap_err()
    .to_string();
    assert!(error.contains("records remain"));
    assert_eq!(op.resource.metadata["reboot_required"], json!(true));

    assert!(
        airoot::bootstrap::reconcile_vendor_uninstall(&mut op, &json!([]), Some(100), None)
            .unwrap()
    );
    assert_eq!(op.resource.metadata["reboot_required"], json!(false));
    assert_eq!(op.reliability.vendor["restart_observed"], json!(true));
    assert_eq!(op.reliability.vendor["uninstall_completed"], json!(true));
}

#[test]
fn vendor_restart_uses_boot_identity_even_after_longer_uptime() {
    for kind in ["python", "conda"] {
        let (_t, _s, mut op) = fixture();
        op.resource.kind = kind.into();
        op.resource.metadata["reboot_required"] = json!(true);
        op.reliability.vendor = json!({
            "before": [], "after": [{"product":"ours"}],
            "uninstall_started": true, "uninstall_attempt_state": "returned",
            "uninstall_exit_code": 3010, "uninstall_started_boot_stamp": 100,
            "reboot_uptime_ms": 1000
        });
        for boot in [None, Some(100), Some(99)] {
            assert!(
                airoot::bootstrap::reconcile_vendor_uninstall(
                    &mut op,
                    &json!([]),
                    Some(1200),
                    boot
                )
                .is_err()
            );
            assert_eq!(op.resource.metadata["reboot_required"], json!(true));
        }
        assert!(
            airoot::bootstrap::reconcile_vendor_uninstall(
                &mut op,
                &json!([{"product":"ours"}]),
                Some(1200),
                Some(200)
            )
            .is_err()
        );
        assert!(
            airoot::bootstrap::reconcile_vendor_uninstall(
                &mut op,
                &json!([]),
                Some(1200),
                Some(200)
            )
            .unwrap()
        );
        assert_eq!(op.reliability.vendor["uninstall_completed"], json!(true));
    }
}

#[test]
fn vendor_restart_marker_never_overrides_unknown_uninstall_outcome() {
    for state in [json!("running"), json!(null), json!("returned")] {
        for code in [json!(null), json!(0), json!(1603), json!(3010)] {
            if state == "returned" && code == 3010 {
                continue;
            }
            let (_t, _s, mut op) = fixture();
            op.resource.metadata["reboot_required"] = json!(true);
            op.reliability.vendor = json!({
                "before": [], "after": [{"product":"ours"}],
                "uninstall_started": true, "uninstall_attempt_state": state,
                "uninstall_exit_code": code, "uninstall_started_boot_stamp": 100,
                "reboot_uptime_ms": 1000
            });
            assert!(
                airoot::bootstrap::reconcile_vendor_uninstall(
                    &mut op,
                    &json!([]),
                    Some(100),
                    Some(200)
                )
                .is_err()
            );
            assert_ne!(op.reliability.vendor["uninstall_completed"], json!(true));
            assert_eq!(op.resource.metadata["reboot_required"], json!(true));
        }
    }
}

#[test]
fn vendor_install_restart_does_not_claim_uninstall_complete() {
    let (_t, _s, mut op) = fixture();
    op.resource.metadata["reboot_required"] = json!(true);
    op.reliability.vendor = json!({
        "before": [], "after": [{"product":"ours"}],
        "installer_started": true, "installer_exit_code": 3010,
        "installer_started_boot_stamp": 100, "installer_started_uptime_ms": 1000
    });
    assert!(
        airoot::bootstrap::reconcile_vendor_uninstall(
            &mut op,
            &json!([{"product":"ours"}]),
            Some(1200),
            Some(100)
        )
        .is_err()
    );
    assert!(
        !airoot::bootstrap::reconcile_vendor_uninstall(
            &mut op,
            &json!([{"product":"ours"}]),
            Some(1200),
            Some(200)
        )
        .unwrap()
    );
    assert_eq!(op.resource.metadata["reboot_required"], json!(false));
    assert_eq!(
        op.reliability.vendor["installer_restart_observed"],
        json!(true)
    );
    assert_ne!(op.reliability.vendor["uninstall_completed"], json!(true));
}

#[test]
fn vendor_legacy_restart_requires_existing_positive_evidence() {
    let (_t, _s, mut op) = fixture();
    op.resource.metadata["reboot_required"] = json!(true);
    op.reliability.vendor = json!({
        "before": [], "after": [], "uninstall_started": true,
        "uninstall_attempt_state": "returned", "uninstall_exit_code": 3010
    });
    assert!(
        airoot::bootstrap::reconcile_vendor_uninstall(&mut op, &json!([]), Some(100), Some(200))
            .is_err()
    );
    assert_ne!(op.reliability.vendor["uninstall_completed"], json!(true));
}

#[test]
fn vendor_uninstall_retries_only_persisted_failures_and_is_bounded() {
    let (_t, _s, mut op) = fixture();
    op.input.action = "bootstrap".into();
    op.reliability.vendor = json!({
        "before": [],
        "after": [{"product":"ours"}],
        "installer_started": true,
        "uninstall_started": true,
        "uninstall_attempt_state": "returned",
        "uninstall_attempts": 2,
        "uninstall_exit_code": 1603
    });

    assert!(
        !airoot::bootstrap::reconcile_vendor_uninstall(
            &mut op,
            &json!([{"product":"ours"}]),
            Some(500),
            None
        )
        .unwrap()
    );
    op.reliability.vendor["uninstall_attempts"] = json!(3);
    assert!(
        airoot::bootstrap::reconcile_vendor_uninstall(
            &mut op,
            &json!([{"product":"ours"}]),
            Some(500),
            None
        )
        .is_err()
    );
}

#[test]
fn failed_existing_modification_refreshes_state_without_deletion_or_replay() {
    let (_t, s, mut op) = fixture();
    let entry = Path::new(&op.resource.path).join("fd.exe");
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        &entry,
    )
    .unwrap();
    op.resource.entry = Some(entry.to_string_lossy().into());
    op.input.action = "install-packages".into();
    op.reliability.failure_confirmed = true;
    s.lock().unwrap().upsert(op.resource.clone()).unwrap();
    recovery::persist(&s, &mut op).unwrap();
    recovery::start(s.clone(), &op.id, false).unwrap();
    let op = wait(&s, &op.id);
    assert_eq!(op.status, "failed");
    assert_eq!(
        op.reliability.recovery.status, "completed",
        "{:?}",
        op.reliability
    );
    assert_eq!(op.reliability.cleanup.status, "not_started");
    assert!(entry.exists());
    assert_eq!(op.error.as_deref(), Some("original installation error"));
    assert!(
        s.lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .all(|l| l.state == "released")
    );
}
#[test]
fn cleanup_final_checkpoint_releases_stale_reservation_without_redeleting() {
    let (_t, s, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    recovery::cleanup(&s, &mut op).unwrap();
    assert!(
        s.lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .any(|l| l.state == "reserved")
    );
    recovery::start(s.clone(), &op.id, true).unwrap();
    assert!(
        s.lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .all(|l| l.state == "released")
    );
}

#[test]
fn blocked_cleanup_keeps_exclusive_protection_until_recovery_finishes() {
    let (_t, shared, mut op) = fixture();
    op.reliability.failure_confirmed = true;
    let p = Path::new(&op.resource.path).join("held");
    fs::write(&p, b"owned").unwrap();
    use std::os::windows::fs::OpenOptionsExt;
    let handle = fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(&p)
        .unwrap();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, true).unwrap();
    let blocked = wait(&shared, &op.id);
    assert_eq!(blocked.reliability.recovery.status, "blocked");
    assert!(
        shared
            .lock()
            .unwrap()
            .acquire(vec![op.resource.id.clone()], "read", None)
            .is_err()
    );
    drop(handle);
    // The worker persists status before releasing its active-task bookkeeping.
    std::thread::sleep(Duration::from_millis(100));
    recovery::start(shared.clone(), &op.id, true).unwrap();
    let completed = wait(&shared, &op.id);
    assert_eq!(completed.reliability.cleanup.status, "completed");
    std::thread::sleep(Duration::from_millis(100));
    assert!(
        shared
            .lock()
            .unwrap()
            .acquire(vec![op.resource.id], "read", None)
            .is_ok()
    );
    assert!(!p.exists());
}

#[test]
fn successful_existing_modification_registration_repair_is_completed() {
    let (_t, shared, mut op) = fixture();
    let entry = Path::new(&op.resource.path).join("fd.exe");
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        &entry,
    )
    .unwrap();
    op.resource.entry = Some(entry.to_string_lossy().into());
    op.input.action = "install-packages".into();
    op.status = "registration_failed".into();
    op.execution_status = "succeeded".into();
    op.verification_status = "succeeded".into();
    op.registration_status = "failed".into();
    shared.lock().unwrap().upsert(op.resource.clone()).unwrap();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let repaired = wait(&shared, &op.id);
    assert_eq!(repaired.status, "completed");
    assert_eq!(repaired.registration_status, "succeeded");
    assert_eq!(repaired.attempts, 1);
    assert!(entry.exists());
    assert_eq!(repaired.reliability.cleanup.status, "not_started");
}

#[cfg(windows)]
#[test]
fn vendor_handoff_waits_for_owned_child_within_deadline() {
    let result = airoot::platform::run_wait_tree(
        "powershell.exe",
        &["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(),
          "Start-Process -WindowStyle Hidden powershell.exe -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 2'".into()],
        Duration::from_secs(20),
        |_| Ok(()),
    ).unwrap();
    assert_eq!(result.code, 0);
    assert!(!result.descendants_active);
}

#[test]
fn completed_install_after_database_rollback_repairs_registration_without_reinstall() {
    let (tmp, shared, mut op) = fixture();
    let directory = Path::new(&op.resource.path).to_path_buf();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        directory.join("fd.exe"),
    )
    .unwrap();
    op.resource.projects.push("rollback-project".into());
    op.status = "completed".into();
    op.execution_status = "succeeded".into();
    op.verification_status = "succeeded".into();
    op.registration_status = "succeeded".into();
    op.steps.push(Step {
        program: "must-never-run.exe".into(),
        args: vec![],
        environment: Default::default(),
    });
    {
        let s = shared.lock().unwrap();
        s.backup().unwrap();
        s.upsert(op.resource.clone()).unwrap();
        let mut lease = s.leases().unwrap().into_iter().next().unwrap();
        s.finish_lease(&mut lease).unwrap();
    }
    recovery::persist(&shared, &mut op).unwrap();
    drop(shared);
    fs::write(tmp.path().join("data/registry.sqlite3"), b"broken database").unwrap();
    let recovered = Store::open(&tmp.path().join("data")).unwrap();
    assert!(recovered.recovery.is_some());
    assert!(recovered.resource(&op.resource.id).is_err());
    let shared = Arc::new(Mutex::new(recovered));
    recovery::startup(shared.clone());
    assert!(shared.lock().unwrap().resource(&op.resource.id).is_err());
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let repaired = wait(&shared, &op.id);
    assert_eq!(repaired.status, "completed", "{:?}", repaired.reliability);
    let registered = shared.lock().unwrap().resource(&op.resource.id).unwrap();
    assert_eq!(registered.status, "available");
    assert!(registered.projects.contains(&"rollback-project".into()));
    assert!(directory.join("fd.exe").exists());
    assert_eq!(repaired.attempts, op.attempts);
    let mut backups: Vec<_> = fs::read_dir(tmp.path().join("data/backups"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "sqlite3")
        })
        .collect();
    backups.sort();
    let backup = rusqlite::Connection::open(backups.last().unwrap()).unwrap();
    let backed_up: u64 = backup
        .query_row(
            "SELECT count(*) FROM resources WHERE id=?1",
            [&op.resource.id],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(
        backed_up, 1,
        "registration repair must also preserve a valid backup"
    );
}

#[test]
fn completed_registration_repair_never_cleans_mismatched_successful_resource() {
    let (_tmp, shared, mut op) = fixture();
    let target = Path::new(&op.resource.path).to_path_buf();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        target.join("fd.exe"),
    )
    .unwrap();
    op.status = "completed".into();
    op.registration_status = "succeeded".into();
    op.input.version = Some("999.999.999".into());
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let repaired = wait(&shared, &op.id);
    assert_eq!(repaired.reliability.recovery.status, "blocked");
    assert_eq!(repaired.reliability.cleanup.status, "not_started");
    assert!(!repaired.reliability.failure_confirmed);
    assert!(target.join("fd.exe").exists());
}

#[test]
fn completed_delete_with_restored_registry_only_synchronizes_missing_instance() {
    let (_tmp, shared, mut op) = fixture();
    shared.lock().unwrap().upsert(op.resource.clone()).unwrap();
    op.input.action = "delete".into();
    op.status = "completed".into();
    op.registration_status = "succeeded".into();
    op.steps.push(Step {
        program: "must-never-run.exe".into(),
        args: vec![],
        environment: Default::default(),
    });
    fs::remove_dir(&op.resource.path).unwrap();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let repaired = wait(&shared, &op.id);
    assert_eq!(repaired.status, "completed", "{:?}", repaired.reliability);
    assert!(shared.lock().unwrap().resource(&op.resource.id).is_err());
}

#[test]
fn completed_recovery_preserves_ignored_resource_and_requires_explicit_restore() {
    let (_tmp, shared, mut op) = fixture();
    let target = Path::new(&op.resource.path).to_path_buf();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        target.join("fd.exe"),
    )
    .unwrap();
    {
        let s = shared.lock().unwrap();
        s.upsert(op.resource.clone()).unwrap();
        s.ignore(&op.resource.id).unwrap();
    }
    op.status = "completed".into();
    op.registration_status = "succeeded".into();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let result = wait(&shared, &op.id);
    assert_eq!(result.reliability.recovery.status, "blocked");
    assert!(
        result
            .reliability
            .recovery
            .reason
            .unwrap()
            .contains("explicitly restore management")
    );
    assert!(shared.lock().unwrap().resource(&op.resource.id).is_err());
    assert!(shared.lock().unwrap().ignored(&op.resource).unwrap());
    assert_eq!(result.reliability.cleanup.status, "not_started");
    assert!(target.join("fd.exe").exists());
}

#[test]
fn completed_delete_recovery_does_not_remove_a_reappeared_target() {
    let (_tmp, shared, mut op) = fixture();
    shared.lock().unwrap().upsert(op.resource.clone()).unwrap();
    op.input.action = "delete".into();
    op.status = "completed".into();
    op.registration_status = "succeeded".into();
    let sentinel = Path::new(&op.resource.path).join("new-user-file");
    fs::write(&sentinel, b"preserve").unwrap();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let result = wait(&shared, &op.id);
    assert_eq!(result.reliability.recovery.status, "blocked");
    assert_eq!(fs::read(&sentinel).unwrap(), b"preserve");
    assert!(shared.lock().unwrap().resource(&op.resource.id).is_ok());
}

#[test]
fn directory_link_exclusions_are_reported_by_both_scan_walkers() {
    for mode in ["symlink", "junction", "cycle"] {
        let (tmp, shared, _) = fixture();
        let root = tmp.path().join("linked scan root");
        let outside = tmp.path().join("outside environment");
        fs::create_dir(&root).unwrap();
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("pyvenv.cfg"), b"version = 3.13").unwrap();
        let root = airoot::platform::canonical(&root).unwrap();
        let ordinary = root.join("ordinary environment");
        fs::create_dir(&ordinary).unwrap();
        fs::write(ordinary.join("pyvenv.cfg"), b"version = 3.13").unwrap();
        let linked = root.join("linked environment");
        if mode == "junction" {
            let output = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path $env:AIROOT_TEST_LINK -Value $env:AIROOT_TEST_TARGET | Out-Null"])
            .env("AIROOT_TEST_LINK", &linked)
            .env("AIROOT_TEST_TARGET", &outside)
            .output().unwrap();
            assert!(
                output.status.success(),
                "junction creation: {}",
                String::from_utf8_lossy(&output.stderr)
            );
        } else {
            let target = if mode == "cycle" { &root } else { &outside };
            std::os::windows::fs::symlink_dir(target, &linked).unwrap();
        }
        // Run before worker registration so this checks find's own directory traversal.
        let positive =
            scan::find(shared.clone(), "ordinary environment", vec![root.clone()]).unwrap();
        let matches = positive["matches"].as_array().unwrap();
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0]["path"], json!(ordinary));
        assert!(positive["uncovered"].as_array().unwrap().is_empty());
        let scan = Scan {
            id: id(),
            status: "running".into(),
            roots: vec![root.clone()],
            visited: 0,
            discovered: 0,
            skipped: vec![],
            providers: vec![],
            coverage: Default::default(),
            created_at: now(),
            updated_at: now(),
        };
        {
            let store = shared.lock().unwrap();
            store.save("scans", &scan.id, &scan).unwrap();
            store
                .db
                .execute(
                    "INSERT INTO scan_queue(scan_id,path) VALUES(?1,?2)",
                    [&scan.id, root.to_str().unwrap()],
                )
                .unwrap();
        }
        scan::worker(shared.clone(), &scan.id, None).unwrap();
        let finished: Scan = shared.lock().unwrap().get("scans", &scan.id).unwrap();
        assert_eq!(finished.coverage["queue_complete"], true);
        assert_eq!(finished.coverage["state"], "partial");
        assert!(
            finished
                .skipped
                .iter()
                .any(|entry| entry["path"] == json!(linked))
        );
        assert_eq!(finished.discovered, 1);
        assert_eq!(finished.visited, 2);
        let found = scan::find(shared.clone(), "outside environment", vec![root]).unwrap();
        assert!(found["matches"].as_array().unwrap().is_empty());
        assert!(
            found["skipped"]
                .as_array()
                .unwrap()
                .iter()
                .any(|entry| entry["path"] == json!(linked))
        );
        assert!(found["uncovered"].as_array().unwrap().is_empty());
        let registered = shared.lock().unwrap().resources().unwrap();
        assert_eq!(registered.len(), 1);
        assert_eq!(registered[0].path, ordinary.to_string_lossy());
    }
}

#[test]
fn supplementary_search_reports_unreadable_root() {
    let (tmp, shared, _) = fixture();
    let missing = tmp.path().join("missing root");
    let found = scan::find(shared, "absent resource", vec![missing.clone()]).unwrap();
    assert!(found["matches"].as_array().unwrap().is_empty());
    assert!(
        found["skipped"]
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["path"] == json!(missing))
    );
}

#[test]
fn supplementary_search_returns_partial_results_on_instance_conflict() {
    let (tmp, shared, _) = fixture();
    let root = tmp.path().join("supplementary");
    fs::create_dir(&root).unwrap();
    let conflict = root.join("conflict env");
    fs::create_dir(&conflict).unwrap();
    fs::write(conflict.join("pyvenv.cfg"), "version = 3.13").unwrap();
    let mut old = Resource::new(
        "env",
        "venv",
        "legacy registration",
        &conflict.to_string_lossy(),
    );
    old.projects = vec!["project-a".into()];
    let mut old = shared.lock().unwrap().upsert(old).unwrap().unwrap();
    old.metadata["instance_identity"] = json!("stale-instance-identity");
    shared.lock().unwrap().update_resource(&old).unwrap();
    let healthy = root.join("healthy env");
    fs::create_dir(&healthy).unwrap();
    fs::write(healthy.join("pyvenv.cfg"), "version = 3.13").unwrap();

    let found = scan::find(shared.clone(), "env", vec![root]).unwrap();
    assert_eq!(found["matches"].as_array().unwrap().len(), 1);
    assert_eq!(
        airoot::platform::canonical_long_unlinked(Path::new(
            found["matches"][0]["path"].as_str().unwrap(),
        ))
        .unwrap(),
        airoot::platform::canonical_long_unlinked(&healthy).unwrap()
    );
    assert_eq!(found["complete"], false);
    assert!(
        found["skipped"].as_array().unwrap().iter().any(|entry| {
            airoot::platform::canonical_long_unlinked(Path::new(entry["path"].as_str().unwrap()))
                .is_ok_and(|path| {
                    path == airoot::platform::canonical_long_unlinked(&conflict).unwrap()
                })
                && entry["reason"]
                    .as_str()
                    .unwrap()
                    .contains("RESOURCE_INSTANCE_CHANGED")
        }),
        "{found}"
    );
    let preserved = shared.lock().unwrap().resource(&old.id).unwrap();
    assert_eq!(preserved.name, "legacy registration");
    assert_eq!(preserved.projects, vec!["project-a"]);
}

#[cfg(windows)]
#[test]
fn resumed_scan_rechecks_queued_paths_after_ancestor_becomes_a_link() {
    let (tmp, shared, _) = fixture();
    let base = fs::canonicalize(tmp.path()).unwrap();
    let root = base.join("scan root");
    let branch = root.join("branch");
    let queued = branch.join("child");
    let outside = base.join("outside");
    fs::create_dir_all(&queued).unwrap();
    fs::create_dir_all(outside.join("child")).unwrap();
    fs::write(outside.join("child/pyvenv.cfg"), "version = 3.13.9").unwrap();
    let checkpoint = Scan {
        id: id(),
        status: "running".into(),
        roots: vec![root.clone()],
        visited: 0,
        discovered: 0,
        skipped: vec![],
        providers: vec![],
        coverage: Default::default(),
        created_at: now(),
        updated_at: now(),
    };
    {
        let store = shared.lock().unwrap();
        store.save("scans", &checkpoint.id, &checkpoint).unwrap();
        store
            .db
            .execute(
                "INSERT INTO scan_queue(scan_id,path) VALUES(?1,?2)",
                [&checkpoint.id, queued.to_str().unwrap()],
            )
            .unwrap();
    }
    // A persisted ordinary child can now resolve through a replaced ancestor.
    fs::rename(&branch, base.join("original branch")).unwrap();
    std::os::windows::fs::symlink_dir(&outside, &branch).unwrap();
    scan::worker(shared.clone(), &checkpoint.id, None).unwrap();
    let store = shared.lock().unwrap();
    let finished: Scan = store.get("scans", &checkpoint.id).unwrap();
    assert_eq!(finished.coverage["queue_complete"], true);
    assert_eq!(finished.coverage["state"], "partial");
    assert!(
        finished
            .skipped
            .iter()
            .any(|item| item["path"] == json!(queued))
    );
    assert!(
        store.resources().unwrap().is_empty(),
        "linked ancestor escaped scan root"
    );
}

#[cfg(windows)]
#[test]
fn linked_ancestor_roots_are_rejected_before_canonicalization() {
    for inside in [false, true] {
        let (tmp, shared, _) = fixture();
        let base = airoot::platform::canonical(tmp.path()).unwrap();
        let root = base.join("request root");
        let actual = if inside {
            root.join("actual")
        } else {
            base.join("outside")
        };
        fs::create_dir_all(actual.join("child environment")).unwrap();
        fs::create_dir_all(&root).unwrap();
        fs::write(
            actual.join("child environment/pyvenv.cfg"),
            "version = 3.13",
        )
        .unwrap();
        let link = root.join("link");
        std::os::windows::fs::symlink_dir(&actual, &link).unwrap();
        let requested = link.join("child environment");
        assert!(scan::start(shared.clone(), vec![requested.clone()]).is_err());
        let found =
            scan::find(shared.clone(), "child environment", vec![requested.clone()]).unwrap();
        assert!(found["matches"].as_array().unwrap().is_empty());
        assert!(
            found["skipped"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v["path"] == json!(requested))
        );
        assert!(shared.lock().unwrap().resources().unwrap().is_empty());
        assert!(airoot::platform::canonical_unlinked(&requested).is_err());
    }
}

#[cfg(windows)]
#[test]
fn ordinary_scan_paths_accept_windows_aliases_and_keep_component_boundaries() {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    let (tmp, shared, _) = fixture();
    let base = airoot::platform::canonical(tmp.path()).unwrap();
    let ordinary = base.join("long ordinary environment");
    fs::create_dir(&ordinary).unwrap();
    fs::write(ordinary.join("pyvenv.cfg"), "version = 3.13").unwrap();
    let upper = ordinary.to_string_lossy().to_uppercase();
    let current = std::env::current_dir().unwrap();
    let extended = fs::canonicalize(&ordinary).unwrap();
    let mut aliases = vec![ordinary.clone(), upper.into(), extended];
    if let Ok(relative) = ordinary.strip_prefix(&current) {
        aliases.push(relative.to_path_buf());
    }
    assert!(airoot::platform::canonical_unlinked(Path::new(".")).is_ok());
    let wide: Vec<u16> = ordinary.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut buffer = vec![0u16; 32768];
    let len = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetShortPathNameW(
            wide.as_ptr(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
        )
    };
    assert!(len > 0 && (len as usize) < buffer.len());
    aliases.push(std::ffi::OsString::from_wide(&buffer[..len as usize]).into());
    for alias in &aliases {
        let resolved = airoot::platform::canonical_unlinked(alias).unwrap();
        assert!(airoot::platform::path_is_under(&resolved, &ordinary));
        assert!(!airoot::platform::path_is_under(
            &base.join("long ordinary environment-other"),
            &ordinary
        ));
        // Unique query cannot hit an already registered row, exercising each alias traversal.
        let found = scan::find(shared.clone(), "never-matches", vec![alias.clone()]).unwrap();
        assert!(found["skipped"].as_array().unwrap().is_empty(), "{found}");
        assert!(found["uncovered"].as_array().unwrap().is_empty(), "{found}");
    }
}

#[cfg(windows)]
#[test]
fn upsert_matches_legacy_83_path_alias_without_cross_path_identity_adoption() {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};

    let tmp = tempfile::tempdir().unwrap();
    let path = tmp.path().join("long environment name");
    fs::create_dir(&path).unwrap();
    fs::write(path.join("pyvenv.cfg"), "version = 3.13").unwrap();
    let s = Store::open(&tmp.path().join("data")).unwrap();
    let mut original = Resource::new("env", "venv", "sample", &path.to_string_lossy());
    original.projects = vec!["project-a".into()];
    let original = s.upsert(original).unwrap().unwrap();

    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut buffer = vec![0u16; 32768];
    let len = unsafe {
        windows_sys::Win32::Storage::FileSystem::GetShortPathNameW(
            wide.as_ptr(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
        )
    };
    assert!(len > 0 && (len as usize) < buffer.len());
    let short_path = std::ffi::OsString::from_wide(&buffer[..len as usize]);
    assert_ne!(short_path, path.as_os_str());

    let mut legacy = s.resource(&original.id).unwrap();
    legacy.path = short_path.to_string_lossy().into_owned();
    s.update_resource(&legacy).unwrap();

    let updated = s
        .upsert(Resource::new(
            "env",
            "venv",
            "sample",
            &path.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    assert_eq!(updated.id, original.id);
    assert_eq!(updated.projects, vec!["project-a"]);
    assert_eq!(updated.path, path.to_string_lossy());
}

#[test]
fn pet_requires_its_pinned_app_local_runtime() {
    let tmp = tempfile::tempdir().unwrap();
    let mut s = Store::open(&tmp.path().join("data")).unwrap();
    let dir = tmp.path().join("components");
    fs::create_dir(&dir).unwrap();
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("components");
    for file in ["manifest.json", "pet.exe", "vcruntime140.dll"] {
        fs::copy(source.join(file), dir.join(file)).unwrap();
    }
    s.config.component_dir = dir.clone();
    assert!(airoot::components::executable(&s, "PET").is_ok());
    fs::write(dir.join("vcruntime140.dll"), b"replaced runtime").unwrap();
    assert!(airoot::components::executable(&s, "PET").is_err());
    fs::remove_file(dir.join("vcruntime140.dll")).unwrap();
    assert!(airoot::components::executable(&s, "PET").is_err());
}
#[test]
fn stale_recovery_completion_cannot_skip_interrupted_registration_reconciliation() {
    let (_tmp, shared, mut op) = fixture();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        Path::new(&op.resource.path).join("fd.exe"),
    )
    .unwrap();
    op.status = "interrupted".into();
    op.execution_status = "succeeded".into();
    op.registration_status = "pending".into();
    op.reliability.recovery.status = "completed".into();
    recovery::persist(&shared, &mut op).unwrap();
    recovery::start(shared.clone(), &op.id, false).unwrap();
    let restored = wait(&shared, &op.id);
    assert_eq!(restored.status, "completed");
    assert_eq!(restored.registration_status, "succeeded");
    assert_eq!(restored.attempts, 1);
    assert_eq!(shared.lock().unwrap().resources().unwrap().len(), 1);
}
