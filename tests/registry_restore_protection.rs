//! Restoring management must not bypass resource protection or lose probe evidence.
#![cfg(windows)]
use airoot::{engine, model::*, store::Store};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
type Shared = Arc<Mutex<Store>>;

fn fixture() -> (tempfile::TempDir, Shared, Resource, Resource, PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let target = tmp.path().join("envs").join("project with spaces");
    fs::create_dir_all(&target).unwrap();
    let sentinel = target.join("project-data.txt");
    fs::write(&sentinel, b"keep project data").unwrap();
    let entry = target.join("fd.exe");
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("components/fd.exe"),
        &entry,
    )
    .unwrap();
    let store = Store::open(&tmp.path().join("data")).unwrap();
    let parent = store
        .upsert(Resource::new(
            "env",
            "venv",
            "project",
            &target.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    let mut tool = Resource::new("tool", "cli", "fd", &entry.to_string_lossy());
    tool.entry = Some(tool.path.clone());
    tool.parent_id = Some(parent.id.clone());
    tool.projects.push("original-project".into());
    let tool = store.upsert(tool).unwrap().unwrap();
    store.ignore(&tool.id).unwrap();
    (tmp, Arc::new(Mutex::new(store)), parent, tool, sentinel)
}
fn restore(shared: &Shared, id: &str) -> anyhow::Result<serde_json::Value> {
    engine::dispatch(
        shared.clone(),
        Request {
            command: "registry.restore".into(),
            args: json!({"id":id}),
        },
    )
}

#[test]
fn restore_rejects_an_active_ancestor_writer_without_touching_ignore_record() {
    let (_tmp, shared, parent, tool, sentinel) = fixture();
    let store = shared.lock().unwrap();
    let before = serde_json::to_value(store.ignored_resource(&tool.id).unwrap()).unwrap();
    let mut writer = store
        .acquire(store.related_ids(&parent).unwrap(), "write", None)
        .unwrap();
    drop(store);
    let result = restore(&shared, &tool.id);
    assert!(
        result.is_err(),
        "Restore ran under an active ancestor writer"
    );
    assert!(result.unwrap_err().to_string().contains("Resource in use"));
    let store = shared.lock().unwrap();
    assert_eq!(
        serde_json::to_value(store.ignored_resource(&tool.id).unwrap()).unwrap(),
        before
    );
    assert!(store.resource(&tool.id).is_err());
    assert_eq!(store.leases().unwrap().len(), 1);
    assert_eq!(fs::read(&sentinel).unwrap(), b"keep project data");
    store.finish_lease(&mut writer).unwrap();
}

#[test]
fn unsuccessful_restore_keeps_ignore_and_durable_protection() {
    let (_tmp, shared, parent, tool, sentinel) = fixture();
    // Change bytes without replacing the filesystem object: spawn must fail.
    fs::write(&tool.path, b"not a Windows executable").unwrap();
    let result = restore(&shared, &tool.id);
    assert!(result.is_err());
    let store = shared.lock().unwrap();
    assert!(store.ignored_resource(&tool.id).is_ok());
    assert!(store.resource(&tool.id).is_err());
    let leases = store.leases().unwrap();
    assert_eq!(leases.len(), 1, "Probe failure lost its usage protection");
    assert_eq!(leases[0].state, "needs_review");
    assert!(
        store
            .acquire(store.related_ids(&parent).unwrap(), "write", None)
            .is_err()
    );
    assert_eq!(fs::read(&sentinel).unwrap(), b"keep project data");
}

#[test]
fn successful_restore_preserves_associations_and_releases_tracked_probe() {
    let (_tmp, shared, _parent, tool, sentinel) = fixture();
    let restored: Resource = serde_json::from_value(restore(&shared, &tool.id).unwrap()).unwrap();
    assert_eq!(restored.id, tool.id);
    assert_eq!(restored.projects, tool.projects);
    assert_eq!(restored.parent_id, tool.parent_id);
    assert_eq!(restored.status, "available");
    let store = shared.lock().unwrap();
    assert!(store.ignored_resource(&tool.id).is_err());
    let leases = store.leases().unwrap();
    assert_eq!(
        leases.len(),
        1,
        "Successful restore skipped protected verification"
    );
    assert_eq!(leases[0].state, "released");
    assert!(leases[0].pid.is_some());
    assert!(leases[0].process_stamp.is_some());
    assert!(leases[0].process_job.is_some());
    assert_eq!(fs::read(&sentinel).unwrap(), b"keep project data");
}

#[test]
fn failed_restore_commit_keeps_ignore_and_reviewable_probe() {
    let (_tmp, shared, parent, tool, sentinel) = fixture();
    shared.lock().unwrap().db.execute_batch(
        "CREATE TRIGGER deny_restore BEFORE INSERT ON resources BEGIN SELECT RAISE(ABORT, 'injected restore commit failure'); END;"
    ).unwrap();
    let error = restore(&shared, &tool.id).unwrap_err();
    assert!(format!("{error:#}").contains("injected restore commit failure"));
    let s = shared.lock().unwrap();
    assert!(s.ignored_resource(&tool.id).is_ok());
    assert!(s.resource(&tool.id).is_err());
    let lease = s.leases().unwrap().remove(0);
    assert_eq!(lease.state, "needs_review");
    assert!(lease.process_job.is_some());
    assert!(
        s.acquire(s.related_ids(&parent).unwrap(), "write", None)
            .is_err()
    );
    drop(s);
    // The request has ended, so explicit post-error review can release it.
    engine::dispatch(
        shared.clone(),
        Request {
            command: "usage.release".into(),
            args: json!({"id":lease.id,"descendants_checked":true}),
        },
    )
    .unwrap();
    assert_eq!(fs::read(sentinel).unwrap(), b"keep project data");
}

#[test]
fn backup_failure_after_restore_keeps_committed_registration_and_protection() {
    let (_tmp, shared, parent, tool, sentinel) = fixture();
    let root = shared.lock().unwrap().root.clone();
    // Block backup creation without deleting previous valid backups.
    let backup = root.join("backups");
    fs::rename(&backup, root.join("saved-backups")).unwrap();
    fs::write(&backup, b"injected backup destination obstruction").unwrap();
    assert!(restore(&shared, &tool.id).is_err());
    let s = shared.lock().unwrap();
    assert!(s.ignored_resource(&tool.id).is_err());
    let restored = s.resource(&tool.id).unwrap();
    assert_eq!(restored.projects, tool.projects);
    assert_eq!(restored.parent_id, tool.parent_id);
    assert_eq!(s.leases().unwrap()[0].state, "needs_review");
    assert!(
        s.acquire(s.related_ids(&parent).unwrap(), "write", None)
            .is_err()
    );
    assert_eq!(fs::read(sentinel).unwrap(), b"keep project data");
}

#[test]
fn verify_commit_failure_retains_reviewable_protection_and_process_job() {
    let (_tmp, shared, _parent, tool, _sentinel) = fixture();
    // Restore succeeds first; then inject failure into the existing verify path.
    restore(&shared, &tool.id).unwrap();
    shared.lock().unwrap().db.execute_batch(
        "CREATE TRIGGER deny_verify BEFORE UPDATE ON resources BEGIN SELECT RAISE(ABORT, 'injected verify commit failure'); END;"
    ).unwrap();
    let error = engine::dispatch(
        shared.clone(),
        Request {
            command: "registry.verify".into(),
            args: json!({"id":tool.id}),
        },
    )
    .unwrap_err();
    assert!(format!("{error:#}").contains("injected verify commit failure"));
    let s = shared.lock().unwrap();
    let leases = s.leases().unwrap();
    let lease = leases.iter().find(|l| l.state != "released").unwrap();
    assert_eq!(lease.state, "needs_review");
    assert!(lease.pid.is_some());
    assert!(lease.process_stamp.is_some());
    assert!(lease.process_job.is_some());
}

fn concurrent_probe(command: &str) {
    use std::{
        sync::mpsc,
        time::{Duration, Instant},
    };
    let (tmp, shared, parent, _fd, sentinel) = fixture();
    let marker = tmp.path().join("probe-started");
    let gate = tmp.path().join("probe-continue");
    let ps = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap()
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut connector = Resource::new("tool", "mcp-connector", "isolated mcp", &parent.path);
    connector.entry = Some(ps.to_string_lossy().into());
    connector.parent_id = Some(parent.id.clone());
    // Path literals are escaped for PowerShell, not interpolated as commands.
    let literal = |p: &Path| format!("'{}'", p.to_string_lossy().replace('\'', "''"));
    let script = format!(
        r#"$null=[Console]::ReadLine(); [IO.File]::WriteAllText({},'started'); $deadline=[DateTime]::UtcNow.AddSeconds(8); while(-not [IO.File]::Exists({})) {{ if([DateTime]::UtcNow -gt $deadline){{exit 3}}; Start-Sleep -Milliseconds 20 }}; [Console]::WriteLine('{{"jsonrpc":"2.0","id":1,"result":{{"protocolVersion":"2024-11-05","serverInfo":{{"name":"isolated-probe","version":"1"}},"capabilities":{{}}}}}}'); $null=[Console]::ReadLine()"#,
        literal(&marker),
        literal(&gate)
    );
    connector.metadata["transport"] = json!("stdio");
    connector.metadata["args"] = json!([
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script
    ]);
    let connector = {
        let s = shared.lock().unwrap();
        let r = s.upsert(connector).unwrap().unwrap();
        if command == "registry.restore" {
            s.ignore(&r.id).unwrap();
        }
        r
    };
    let copy = shared.clone();
    let id = connector.id.clone();
    let command = command.to_owned();
    let task = std::thread::spawn(move || {
        engine::dispatch(
            copy,
            Request {
                command,
                args: json!({"id":id}),
            },
        )
    });
    let deadline = Instant::now() + Duration::from_secs(8);
    while !marker.exists() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    let started = marker.exists();
    let (tx, rx) = mpsc::channel();
    let copy = shared.clone();
    let query = std::thread::spawn(move || {
        let result = engine::dispatch(
            copy.clone(),
            Request {
                command: "usage.list".into(),
                args: json!({}),
            },
        );
        let _ = tx.send(result);
    });
    let queried = rx.recv_timeout(Duration::from_secs(2));
    let release_result = if let Ok(Ok(ref value)) = queried {
        let leases: Vec<Lease> = serde_json::from_value(value.clone()).unwrap();
        let lease = leases.iter().find(|l| l.state != "released");
        lease.map(|lease| {
            engine::dispatch(
                shared.clone(),
                Request {
                    command: "usage.release".into(),
                    args: json!({"id":lease.id,"descendants_checked":true}),
                },
            )
        })
    } else {
        None
    };
    // Always release the probe before assertions, including the old-code failure.
    fs::write(&gate, b"continue").unwrap();
    query.join().unwrap();
    let result = task.join().unwrap();
    assert!(started, "Probe failed to start");
    assert!(
        queried.is_ok(),
        "Probe held the registry mutex, blocking queries"
    );
    let error = release_result
        .expect("No durable probe lease")
        .unwrap_err()
        .to_string();
    assert!(error.contains("VERIFICATION_IN_PROGRESS"), "{error}");
    result.unwrap();
    assert_eq!(
        shared
            .lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .filter(|l| l.state != "released")
            .count(),
        0
    );
    assert_eq!(fs::read(sentinel).unwrap(), b"keep project data");
}

#[test]
fn restore_probe_does_not_block_queries_and_cannot_be_manually_released() {
    concurrent_probe("registry.restore");
}
#[test]
fn verify_probe_does_not_block_queries_and_cannot_be_manually_released() {
    concurrent_probe("registry.verify");
}
