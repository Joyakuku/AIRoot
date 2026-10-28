//! Regression coverage for startup finalization of already reconciled operations.
//! The fixture deliberately has no executable entry: verification would fail
//! before launching a command, and its saved step names a nonexistent program.

use airoot::{
    model::{Lease, Operation, PlanInput, Resource, Step, id, now},
    recovery,
    store::{Store, read_json},
};
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

type Shared = Arc<Mutex<Store>>;

struct Fixture {
    shared: Shared,
    original: Operation,
    registered: Resource,
    lease_id: String,
    original_lease: Lease,
    sentinel: PathBuf,
    // Close the store before removing its temporary directory on Windows.
    _temp: tempfile::TempDir,
}

struct Snapshot {
    operation: Operation,
    lease: Lease,
    resource: Resource,
    summary: Value,
}

fn completed_failed_recovery() -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let data = temp.path().join("data");
    let root = temp.path().join("envs");
    let target = root.join("existing project env");
    fs::create_dir_all(&target).unwrap();
    let sentinel = target.join("project-data.txt");
    fs::write(
        &sentinel,
        b"existing project data must survive finalization",
    )
    .unwrap();

    let mut store = Store::open(&data).unwrap();
    assert!(store.recovery.is_none());
    store.config.env_root = Some(root);
    store.save_config(store.config.clone()).unwrap();

    let mut resource = Resource::new("env", "venv", "existing-project", &target.to_string_lossy());
    resource.status = "available".into();
    resource.version = Some("3.12.0".into());
    resource.projects = vec!["existing-project".into()];
    resource.dependencies = json!([{"name":"sample-dependency","version":"1.0"}]);
    // No Python or other executable is needed, or allowed to run, in this test.
    resource.entry = None;
    let resource = store.upsert(resource).unwrap().unwrap();

    let input: PlanInput = serde_json::from_value(json!({
        "action": "install-packages",
        "resource_id": resource.id,
        "project": "existing-project",
        "packages": ["sample-dependency==2.0"]
    }))
    .unwrap();
    let mut operation = Operation {
        id: id(),
        input,
        resource: resource.clone(),
        fingerprint: None,
        risk: "low".into(),
        requirements: vec![],
        effects: vec!["Modify dependencies of the existing environment".into()],
        steps: vec![Step {
            program: temp
                .path()
                .join("must-not-be-executed.exe")
                .to_string_lossy()
                .into_owned(),
            args: vec!["must-not-replay".into()],
            environment: Default::default(),
        }],
        status: "failed".into(),
        execution_status: "failed".into(),
        verification_status: "succeeded".into(),
        registration_status: "not_started".into(),
        created_at: now(),
        updated_at: now(),
        error: Some(
            "Original dependency modification failed; actual dependencies reconciled".into(),
        ),
        attempts: 1,
        reliability: Default::default(),
    };
    operation.reliability.failure_confirmed = true;
    operation.reliability.recovery.status = "completed".into();
    operation.reliability.recovery.attempts = 1;
    operation.reliability.recovery.completed_steps = vec![
        "Actual dependencies refreshed; original modification not assumed successful or replayed"
            .into(),
    ];
    assert_eq!(operation.reliability.cleanup.status, "not_started");
    assert!(operation.reliability.owned.is_empty());

    let lease = store
        .acquire_for_operation(
            store.related_ids(&resource).unwrap(),
            "write",
            operation.input.project.clone(),
            Some(operation.id.clone()),
        )
        .unwrap();
    assert_eq!(lease.state, "reserved");
    assert!(lease.pid.is_none());
    assert!(lease.process_stamp.is_none());
    assert!(lease.process_job.is_none());
    operation.reliability.lease_id = Some(lease.id.clone());

    let shared = Arc::new(Mutex::new(store));
    recovery::persist(&shared, &mut operation).unwrap();
    drop(shared);

    // Reopen the real SQLite/journal/usage files, as after a daemon restart.
    let store = Store::open(&data).unwrap();
    assert!(store.recovery.is_none());
    let original: Operation = store.get("operations", &operation.id).unwrap();
    Fixture {
        shared: Arc::new(Mutex::new(store)),
        original,
        registered: resource,
        lease_id: lease.id.clone(),
        original_lease: lease,
        sentinel,
        _temp: temp,
    }
}

fn snapshot(fixture: &Fixture) -> Snapshot {
    let store = fixture.shared.lock().unwrap();
    Snapshot {
        operation: store.get("operations", &fixture.original.id).unwrap(),
        lease: store
            .leases()
            .unwrap()
            .into_iter()
            .find(|lease| lease.id == fixture.lease_id)
            .expect("the original operation lease must not disappear"),
        resource: store.resource(&fixture.registered.id).unwrap(),
        summary: recovery::summary(&store).unwrap(),
    }
}

fn wait_for(fixture: &Fixture, description: &str, ready: impl Fn(&Snapshot) -> bool) -> Snapshot {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let state = snapshot(fixture);
        if ready(&state) {
            return state;
        }
        assert!(
            Instant::now() < deadline,
            "Timed out waiting for {description}; lease={}, summary={}, operation={}",
            state.lease.state,
            state.summary,
            serde_json::to_value(&state.operation).unwrap()
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn assert_no_verification_or_replay(fixture: &Fixture, state: &Snapshot) {
    let mut expected = serde_json::to_value(&fixture.original).unwrap();
    let mut actual = serde_json::to_value(&state.operation).unwrap();
    for value in [&mut expected, &mut actual] {
        // Only finalization bookkeeping and the operation timestamp may change.
        value.as_object_mut().unwrap().remove("updated_at");
        value["reliability"]
            .as_object_mut()
            .unwrap()
            .remove("finalization");
    }
    assert_eq!(
        actual, expected,
        "Finalization must preserve the original failure, recovery/cleanup completed_steps, and execution history"
    );
    assert_eq!(
        serde_json::to_value(&state.resource).unwrap(),
        serde_json::to_value(&fixture.registered).unwrap(),
        "An already reconciled resource must not be verified or re-registered"
    );
    assert_eq!(
        fs::read(&fixture.sentinel).unwrap(),
        b"existing project data must survive finalization"
    );
    let mut expected_lease = serde_json::to_value(&fixture.original_lease).unwrap();
    expected_lease["state"] = json!(state.lease.state);
    assert_eq!(serde_json::to_value(&state.lease).unwrap(), expected_lease);
}

#[test]
fn completed_failed_recovery_with_reserved_lease_is_reported_pending() {
    let fixture = completed_failed_recovery();
    let before = snapshot(&fixture);
    assert_eq!(before.operation.status, "failed");
    assert_eq!(before.operation.reliability.recovery.status, "completed");
    assert_eq!(before.operation.reliability.cleanup.status, "not_started");
    assert_eq!(before.lease.state, "reserved");
    assert!(
        before.summary["pending"].as_u64().unwrap() >= 1,
        "A terminal recovery with an unreleased lease is pending finalization: {}",
        before.summary
    );
}

#[test]
fn startup_only_releases_lease_for_completed_failed_recovery() {
    let fixture = completed_failed_recovery();
    let before = snapshot(&fixture);
    recovery::startup(fixture.shared.clone());
    let after = wait_for(&fixture, "startup to release the reserved lease", |state| {
        state.lease.state == "released"
            && serde_json::to_value(&state.operation).unwrap()["reliability"]["finalization"]["status"]
                == "completed"
    });
    assert!(before.summary["pending"].as_u64().unwrap() >= 1);
    assert_no_verification_or_replay(&fixture, &after);
    assert_eq!(after.summary["pending"], json!(0));
    assert_eq!(after.summary["blocked"], json!(0));
}

#[cfg(windows)]
#[test]
fn lease_release_write_failure_is_visible_and_startup_can_retry() {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::{FILE_SHARE_READ, FILE_SHARE_WRITE};

    let fixture = completed_failed_recovery();
    let lease_path = fixture
        .shared
        .lock()
        .unwrap()
        .root
        .join("usage")
        .join(format!("{}.json", fixture.lease_id));
    // Permit reading the lease but deny delete-sharing, so atomic replacement
    // fails. Only this fixture's lease file is locked; the journal and DB remain writable.
    let blocker = fs::OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .open(&lease_path)
        .unwrap();

    recovery::startup(fixture.shared.clone());
    let blocked = wait_for(&fixture, "a visible finalization write failure", |state| {
        let value = serde_json::to_value(&state.operation).unwrap();
        let finalization = &value["reliability"]["finalization"];
        finalization["status"] == "blocked"
            && finalization["reason"]
                .as_str()
                .is_some_and(|reason| !reason.trim().is_empty())
    });
    assert_eq!(blocked.lease.state, "reserved");
    assert!(blocked.summary["pending"].as_u64().unwrap() >= 1);
    assert!(blocked.summary["blocked"].as_u64().unwrap() >= 1);
    assert_no_verification_or_replay(&fixture, &blocked);
    assert_eq!(read_json::<Lease>(&lease_path).unwrap().state, "reserved");
    assert!(
        fixture
            .shared
            .lock()
            .unwrap()
            .acquire(vec![fixture.registered.id.clone()], "read", None)
            .is_err(),
        "A failed release must retain exclusive protection"
    );

    drop(blocker);
    recovery::startup(fixture.shared.clone());
    let settled = wait_for(&fixture, "startup to retry lease finalization", |state| {
        state.lease.state == "released"
            && serde_json::to_value(&state.operation).unwrap()["reliability"]["finalization"]["status"]
                == "completed"
    });
    assert_no_verification_or_replay(&fixture, &settled);
    let value = serde_json::to_value(&settled.operation).unwrap();
    assert!(value["reliability"]["finalization"]["reason"].is_null());
    assert_eq!(settled.summary["pending"], json!(0));
    assert_eq!(settled.summary["blocked"], json!(0));
    assert_eq!(read_json::<Lease>(&lease_path).unwrap().state, "released");
}

#[test]
fn startup_only_finalizes_released_completed_operation_after_resource_was_ignored() {
    let Fixture {
        shared,
        mut original,
        registered,
        lease_id: _,
        mut original_lease,
        sentinel,
        _temp,
    } = completed_failed_recovery();

    // Simulate a successful operation whose release succeeded, but whose last
    // finalization metadata write failed. The user then cancelled management.
    original.status = "completed".into();
    original.execution_status = "succeeded".into();
    original.verification_status = "succeeded".into();
    original.registration_status = "succeeded".into();
    original.error = None;
    original.reliability.failure_confirmed = false;
    original.reliability.recovery.completed_steps = vec!["registration-synchronized".into()];
    original.reliability.finalization.status = "blocked".into();
    original.reliability.finalization.reason =
        Some("Protection released; finalization metadata pending: injected write failure".into());
    original.reliability.finalization.attempts = 1;
    recovery::persist(&shared, &mut original).unwrap();
    let data = {
        let store = shared.lock().unwrap();
        store.finish_lease(&mut original_lease).unwrap();
        store.ignore(&registered.id).unwrap();
        store.root.clone()
    };
    drop(shared);

    // Persist and reopen all evidence, including the explicit ignore record.
    let store = Store::open(&data).unwrap();
    assert!(store.recovery.is_none());
    assert!(store.resources().unwrap().is_empty());
    assert!(store.ignored(&registered).unwrap());
    let ignored_before =
        serde_json::to_value(store.ignored_resource(&registered.id).unwrap()).unwrap();
    let original: Operation = store.get("operations", &original.id).unwrap();
    let mut expected = serde_json::to_value(&original).unwrap();
    expected.as_object_mut().unwrap().remove("updated_at");
    expected["reliability"]
        .as_object_mut()
        .unwrap()
        .remove("finalization");
    assert_eq!(store.leases().unwrap().len(), 1);
    assert_eq!(store.leases().unwrap()[0].state, "released");
    let before = recovery::summary(&store).unwrap();
    assert!(before["pending"].as_u64().unwrap() >= 1);
    assert!(before["blocked"].as_u64().unwrap() >= 1);

    let shared = Arc::new(Mutex::new(store));
    recovery::startup(shared.clone());
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let store = shared.lock().unwrap();
        let saved: Operation = store.get("operations", &original.id).unwrap();
        let mut actual = serde_json::to_value(&saved).unwrap();
        actual.as_object_mut().unwrap().remove("updated_at");
        actual["reliability"]
            .as_object_mut()
            .unwrap()
            .remove("finalization");
        assert_eq!(
            actual, expected,
            "Startup finalization must not enter completed-registration repair or change operation history"
        );
        assert!(
            store.resources().unwrap().is_empty(),
            "Ignored resource must not be re-registered"
        );
        assert!(store.ignored(&registered).unwrap());
        assert_eq!(
            serde_json::to_value(store.ignored_resource(&registered.id).unwrap()).unwrap(),
            ignored_before,
            "Startup must preserve the explicit ignore record and its instance identity"
        );
        assert_eq!(
            serde_json::to_value(store.leases().unwrap()).unwrap(),
            json!([original_lease]),
            "Already released protection must stay released; no replacement lease may be acquired"
        );
        if saved.reliability.finalization.status == "completed" {
            assert!(saved.reliability.finalization.reason.is_none());
            assert_eq!(
                recovery::summary(&store).unwrap(),
                json!({"pending": 0, "blocked": 0})
            );
            let journal: Operation =
                read_json(&data.join("pending").join(format!("{}.json", original.id))).unwrap();
            assert_eq!(
                serde_json::to_value(journal).unwrap(),
                serde_json::to_value(saved).unwrap()
            );
            break;
        }
        assert!(
            Instant::now() < deadline,
            "Startup did not finish metadata-only finalization: {}",
            serde_json::to_value(saved).unwrap()
        );
        drop(store);
        thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(
        fs::read(&sentinel).unwrap(),
        b"existing project data must survive finalization"
    );
    drop(shared);
    drop(_temp);
}
