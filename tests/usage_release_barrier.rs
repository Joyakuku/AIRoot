//! Manual usage release must not bypass operation reconciliation/finalization.
use airoot::{
    engine,
    model::*,
    operations,
    store::{Store, read_json},
};
use serde_json::json;
use std::{
    fs,
    sync::{Arc, Mutex},
};

type Shared = Arc<Mutex<Store>>;

fn fixture() -> (tempfile::TempDir, Shared, Resource, Operation) {
    let tmp = tempfile::tempdir().unwrap();
    let env_root = tmp.path().join("envs");
    let target = env_root.join("project");
    fs::create_dir_all(&target).unwrap();
    fs::write(target.join("project-data.txt"), b"keep").unwrap();
    let mut store = Store::open(&tmp.path().join("data")).unwrap();
    store.config.env_root = Some(env_root);
    store.save_config(store.config.clone()).unwrap();
    let resource = store
        .upsert(Resource::new(
            "env",
            "venv",
            "project",
            &target.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    // Planning captures identity only; nothing in this test deletes the target.
    let operation = operations::plan(
        &store,
        serde_json::from_value(json!({"action":"delete", "resource_id":resource.id})).unwrap(),
    )
    .unwrap();
    (tmp, Arc::new(Mutex::new(store)), resource, operation)
}

fn release(shared: &Shared, lease: &Lease, checked: bool) -> anyhow::Result<serde_json::Value> {
    engine::dispatch(
        shared.clone(),
        Request {
            command: "usage.release".into(),
            args: json!({"id":lease.id,"descendants_checked":checked}),
        },
    )
}

#[test]
fn maintenance_lease_cannot_be_manually_released_between_processes_or_after_failed_commit() {
    for status in [
        "executing",
        "interrupted",
        "registration_failed",
        "failed",
        "completed",
    ] {
        let (_tmp, shared, resource, mut op) = fixture();
        let store = shared.lock().unwrap();
        let mut lease = store
            .acquire_for_operation(
                store.related_ids(&resource).unwrap(),
                "write",
                None,
                Some(op.id.clone()),
            )
            .unwrap();
        // The last child exited; maintenance may still be doing I/O, preparing
        // another child or trying to commit. No timeout/process guess is used.
        lease.state = "needs_review".into();
        store.save_lease(&lease).unwrap();
        op.status = status.into();
        op.reliability.lease_id = Some(lease.id.clone());
        op.reliability.finalization.status = "blocked".into();
        store.save("operations", &op.id, &op).unwrap();
        let lease_path = store.root.join("usage").join(format!("{}.json", lease.id));
        let original = fs::read(&lease_path).unwrap();
        drop(store);
        let result = release(&shared, &lease, true);
        assert!(result.is_err(), "Manually released operation in {status}");
        let error = result.unwrap_err().to_string();
        assert!(error.contains("OPERATION_RECOVERY_REQUIRED"), "{error}");
        assert_eq!(fs::read(&lease_path).unwrap(), original);
        let store = shared.lock().unwrap();
        assert!(
            store
                .acquire(store.related_ids(&resource).unwrap(), "read", None)
                .is_err()
        );
        assert_eq!(
            fs::read(std::path::Path::new(&resource.path).join("project-data.txt")).unwrap(),
            b"keep"
        );
    }
}

#[test]
fn legacy_operation_link_is_found_even_when_lease_has_no_operation_id() {
    let (_tmp, shared, resource, mut op) = fixture();
    let store = shared.lock().unwrap();
    let mut lease = store
        .acquire(store.related_ids(&resource).unwrap(), "write", None)
        .unwrap();
    lease.state = "needs_review".into();
    store.save_lease(&lease).unwrap();
    op.status = "interrupted".into();
    op.reliability.lease_id = Some(lease.id.clone());
    store.save("operations", &op.id, &op).unwrap();
    drop(store);
    let error = release(&shared, &lease, true).unwrap_err().to_string();
    assert!(error.contains("OPERATION_RECOVERY_REQUIRED"), "{error}");
    assert!(
        shared
            .lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .all(|l| l.state != "released")
    );
}

#[test]
fn operation_id_alone_preserves_protection_when_operation_record_is_missing() {
    let (_tmp, shared, resource, op) = fixture();
    let store = shared.lock().unwrap();
    let mut lease = store
        .acquire_for_operation(
            store.related_ids(&resource).unwrap(),
            "write",
            None,
            Some(op.id.clone()),
        )
        .unwrap();
    lease.state = "needs_review".into();
    store.save_lease(&lease).unwrap();
    store
        .db
        .execute("DELETE FROM operations WHERE id=?1", [&op.id])
        .unwrap();
    drop(store);
    let error = release(&shared, &lease, true).unwrap_err().to_string();
    assert!(error.contains("OPERATION_RECOVERY_REQUIRED"), "{error}");
}

#[test]
fn unlinked_external_usage_still_requires_review_and_can_be_released() {
    let (_tmp, shared, resource, _op) = fixture();
    let store = shared.lock().unwrap();
    let mut lease = store
        .acquire(store.related_ids(&resource).unwrap(), "read", None)
        .unwrap();
    lease.state = "needs_review".into();
    store.save_lease(&lease).unwrap();
    let lease_path = store.root.join("usage").join(format!("{}.json", lease.id));
    drop(store);
    let error = release(&shared, &lease, false).unwrap_err().to_string();
    assert!(
        error.contains("DESCENDANT_VERIFICATION_REQUIRED"),
        "{error}"
    );
    release(&shared, &lease, true).unwrap();
    let saved: Lease = read_json(&lease_path).unwrap();
    assert_eq!(saved.state, "released");
    // Existing already-released external leases retain their idempotent behavior.
    release(&shared, &lease, true).unwrap();
}

#[test]
fn already_released_operation_lease_remains_idempotent() {
    let (_tmp, shared, resource, op) = fixture();
    let store = shared.lock().unwrap();
    let mut lease = store
        .acquire_for_operation(
            store.related_ids(&resource).unwrap(),
            "write",
            None,
            Some(op.id),
        )
        .unwrap();
    store.finish_lease(&mut lease).unwrap();
    drop(store);
    release(&shared, &lease, true).unwrap();
}

#[cfg(windows)]
#[test]
fn exited_real_child_does_not_allow_manual_release_before_operation_finalization() {
    let (_tmp, shared, resource, mut op) = fixture();
    let mut lease = {
        let store = shared.lock().unwrap();
        store
            .acquire_for_operation(
                store.related_ids(&resource).unwrap(),
                "write",
                None,
                Some(op.id.clone()),
            )
            .unwrap()
    };
    op.status = "executing".into();
    op.reliability.lease_id = Some(lease.id.clone());
    airoot::recovery::persist(&shared, &mut op).unwrap();
    let result = airoot::platform::run(
        "cmd.exe",
        &["/d".into(), "/c".into(), "exit 0".into()],
        std::time::Duration::from_secs(10),
        |pid| {
            lease.pid = Some(pid);
            lease.process_stamp = airoot::platform::process_stamp(pid)?;
            lease.process_job = Some(airoot::platform::process_job_name(pid)?);
            lease.state = "verifying".into();
            shared.lock().unwrap().save_lease(&lease)
        },
    )
    .unwrap();
    assert_eq!(result.code, 0);
    assert!(!result.descendants_active);
    assert!(airoot::platform::job_finished(lease.process_job.as_deref().unwrap()).unwrap());
    assert!(
        release(&shared, &lease, true).is_err(),
        "Exited child is not a committed operation result"
    );
    assert!(
        shared
            .lock()
            .unwrap()
            .acquire(vec![resource.id.clone()], "read", None)
            .is_err()
    );
    // Only the operation recovery/finalization path releases the same lease,
    // after the completed recovery result is durable. No delete is replayed.
    op.status = "failed".into();
    op.reliability.recovery.status = "completed".into();
    airoot::recovery::persist(&shared, &mut op).unwrap();
    let recovered = airoot::recovery::start(shared.clone(), &op.id, false).unwrap();
    assert_eq!(recovered.reliability.finalization.status, "completed");
    assert!(
        shared
            .lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .all(|l| l.state == "released")
    );
    assert_eq!(
        fs::read(std::path::Path::new(&resource.path).join("project-data.txt")).unwrap(),
        b"keep"
    );
}

#[test]
fn unreadable_operation_associations_fail_closed_without_changing_external_lease() {
    let (_tmp, shared, resource, op) = fixture();
    let store = shared.lock().unwrap();
    let mut lease = store
        .acquire(store.related_ids(&resource).unwrap(), "read", None)
        .unwrap();
    lease.state = "needs_review".into();
    store.save_lease(&lease).unwrap();
    let path = store.root.join("usage").join(format!("{}.json", lease.id));
    let original = fs::read(&path).unwrap();
    store
        .db
        .execute("UPDATE operations SET body='{' WHERE id=?1", [&op.id])
        .unwrap();
    drop(store);
    assert!(release(&shared, &lease, true).is_err());
    assert_eq!(fs::read(&path).unwrap(), original);
    // Repairing the unrelated malformed record restores normal manual release;
    // failure must not silently discard an unknown reverse association.
    shared
        .lock()
        .unwrap()
        .save("operations", &op.id, &op)
        .unwrap();
    release(&shared, &lease, true).unwrap();
}
