use airoot::{model::*, operations, platform, store::Store};
use serde_json::json;
use std::{fs, path::PathBuf};
#[test]
fn registry_identity_ignore_restore_and_projects() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let env_path = t.path().join("env-a");
    fs::create_dir(&env_path).unwrap();
    fs::write(env_path.join("pyvenv.cfg"), "version = 3.12.0\n").unwrap();
    fs::create_dir(env_path.join("Scripts")).unwrap();
    fs::write(
        env_path.join("Scripts/python.exe"),
        "verified test executable",
    )
    .unwrap();
    let path = env_path.to_string_lossy().into_owned();
    let mut r = Resource::new("env", "venv", "a", &path);
    r.entry = Some(env_path.join("Scripts/python.exe").to_string_lossy().into());
    r.version = Some("3.12.0".into());
    r.projects = vec!["one".into()];
    let r = s.upsert(r).unwrap().unwrap();
    let mut next = Resource::new("env", "venv", "a", &path.to_uppercase());
    next.projects = vec!["two".into()];
    let next = s.upsert(next).unwrap().unwrap();
    assert_eq!(r.id, next.id);
    assert_eq!(next.projects.len(), 2);
    s.ignore(&r.id).unwrap();
    assert!(s.upsert(r.clone()).unwrap().is_none());
    assert!(s.resources().unwrap().is_empty());
    let mut discovered = Resource::new("env", "venv", "a", &path);
    discovered.entry = Some(env_path.join("Scripts/python.exe").to_string_lossy().into());
    discovered.version = Some("3.12.0".into());
    discovered.status = "available".into();
    let restored = s.restore_verified(&r.id, discovered).unwrap();
    assert_eq!(restored.projects.len(), 2);
    assert_eq!(restored.status, "available");
}

#[test]
fn restore_rejects_replaced_instance_and_keeps_ignore_record() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let path = t.path().join("resource");
    fs::create_dir(&path).unwrap();
    fs::write(path.join("marker"), "original").unwrap();
    let resource = s
        .upsert(Resource::new(
            "tool",
            "portable",
            "resource",
            &path.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    s.ignore(&resource.id).unwrap();
    fs::remove_dir_all(&path).unwrap();
    fs::create_dir(&path).unwrap();
    fs::write(path.join("marker"), "replacement").unwrap();
    let discovered = Resource::new("tool", "portable", "resource", &path.to_string_lossy());
    assert!(s.restore_verified(&resource.id, discovered).is_err());
    assert!(s.resources().unwrap().is_empty());
    assert!(s.ignored_resource(&resource.id).is_ok());
}

#[test]
fn restore_fails_closed_for_legacy_ignore_record_without_instance_identity() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let path = t.path().join("resource");
    fs::create_dir(&path).unwrap();
    let resource = s
        .upsert(Resource::new(
            "tool",
            "portable",
            "resource",
            &path.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    s.ignore(&resource.id).unwrap();
    s.db.execute("UPDATE ignored SET physical_identity=NULL", [])
        .unwrap();

    let discovered = Resource::new("tool", "portable", "resource", &path.to_string_lossy());
    assert!(s.restore_verified(&resource.id, discovered).is_err());
    assert!(s.resources().unwrap().is_empty());
    assert!(s.ignored_resource(&resource.id).unwrap().1.is_none());
}

#[test]
fn upsert_rejects_same_path_replacement_without_inheriting_projects() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let path = t.path().join("env");
    fs::create_dir(&path).unwrap();
    fs::write(path.join("original"), "old").unwrap();
    let mut original = Resource::new("env", "venv", "env", &path.to_string_lossy());
    original.projects = vec!["old-project".into()];
    let original = s.upsert(original).unwrap().unwrap();

    let moved = t.path().join("old-env");
    fs::rename(&path, &moved).unwrap();
    fs::create_dir(&path).unwrap();
    fs::write(path.join("replacement"), "new").unwrap();
    let mut replacement = Resource::new("env", "venv", "env", &path.to_string_lossy());
    replacement.projects = vec!["new-project".into()];

    let error = s.upsert(replacement).unwrap_err().to_string();
    assert!(error.contains("RESOURCE_INSTANCE_CHANGED"));
    let still_registered = s.resource(&original.id).unwrap();
    assert_eq!(still_registered.projects, vec!["old-project"]);
}

#[test]
fn upsert_does_not_transfer_projects_when_same_instance_moves_to_another_path() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let old_path = t.path().join("old-env");
    fs::create_dir(&old_path).unwrap();
    let mut original = Resource::new("env", "venv", "env", &old_path.to_string_lossy());
    original.projects = vec!["historic-project".into()];
    let original = s.upsert(original).unwrap().unwrap();

    let new_path = t.path().join("new-env");
    fs::rename(&old_path, &new_path).unwrap();
    let mut moved = Resource::new("env", "venv", "env", &new_path.to_string_lossy());
    moved.projects = vec!["current-project".into()];
    let moved = s.upsert(moved).unwrap().unwrap();

    assert_ne!(moved.id, original.id);
    assert_eq!(moved.projects, vec!["current-project"]);
    assert_eq!(
        s.resource(&original.id).unwrap().projects,
        vec!["historic-project"]
    );
}

#[test]
fn upsert_rejects_legacy_record_without_instance_identity() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let path = t.path().join("env");
    fs::create_dir(&path).unwrap();
    let mut original = Resource::new("env", "venv", "env", &path.to_string_lossy());
    original.projects = vec!["historic-project".into()];
    let original = s.upsert(original).unwrap().unwrap();
    let mut legacy = s.resource(&original.id).unwrap();
    legacy
        .metadata
        .as_object_mut()
        .unwrap()
        .remove("instance_identity");
    s.update_resource(&legacy).unwrap();

    let replacement = Resource::new("env", "venv", "env", &path.to_string_lossy());
    let error = s.upsert(replacement).unwrap_err().to_string();
    assert!(error.contains("RESOURCE_INSTANCE_IDENTITY_MISSING"));
    assert_eq!(
        s.resource(&original.id).unwrap().projects,
        vec!["historic-project"]
    );
}

#[test]
fn registry_verify_rejects_replaced_instance_before_running_adapter() {
    use std::sync::{Arc, Mutex};
    let t = tempfile::tempdir().unwrap();
    let path = t.path().join("tool");
    fs::create_dir(&path).unwrap();
    fs::write(path.join("original"), "old").unwrap();
    let s = Store::open(t.path()).unwrap();
    let resource = s
        .upsert(Resource::new(
            "tool",
            "cli",
            "test",
            &path.to_string_lossy(),
        ))
        .unwrap()
        .unwrap();
    let moved = t.path().join("old-tool");
    fs::rename(&path, moved).unwrap();
    fs::create_dir(&path).unwrap();
    fs::write(path.join("replacement"), "new").unwrap();

    let result = airoot::engine::dispatch(
        Arc::new(Mutex::new(s)),
        Request {
            command: "registry.verify".into(),
            args: json!({"id":resource.id}),
        },
    );
    assert!(
        result
            .unwrap_err()
            .to_string()
            .contains("RESOURCE_INSTANCE_CHANGED")
    );
}
#[test]
fn related_resource_locks_allow_readers_and_block_writers() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let parent = s
        .upsert(Resource::new("env", "venv", "a", "D:\\env\\a"))
        .unwrap()
        .unwrap();
    let mut child = Resource::new("tool", "cli", "tool", "D:\\env\\a\\tool.exe");
    child.parent_id = Some(parent.id.clone());
    let child = s.upsert(child).unwrap().unwrap();
    let mut l1 = s
        .acquire(s.related_ids(&parent).unwrap(), "read", None)
        .unwrap();
    let mut l2 = s
        .acquire(s.related_ids(&child).unwrap(), "read", None)
        .unwrap();
    assert!(
        s.acquire(s.related_ids(&parent).unwrap(), "write", None)
            .is_err()
    );
    s.finish_lease(&mut l1).unwrap();
    assert!(
        s.acquire(s.related_ids(&parent).unwrap(), "write", None)
            .is_err()
    );
    s.finish_lease(&mut l2).unwrap();
    assert!(
        s.acquire(s.related_ids(&parent).unwrap(), "write", None)
            .is_ok()
    );
}
#[test]
fn corruption_restore_preserves_original_and_usage() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let mut r = Resource::new("env", "venv", "a", "D:\\env\\a");
    r.status = "available".into();
    let r = s.upsert(r).unwrap().unwrap();
    s.acquire(vec![r.id.clone()], "read", None).unwrap();
    s.backup().unwrap();
    drop(s);
    fs::write(t.path().join("registry.sqlite3"), b"broken sqlite").unwrap();
    let s = Store::open(t.path()).unwrap();
    assert!(s.recovery.is_some());
    assert_eq!(s.resource(&r.id).unwrap().status, "unverified");
    assert_eq!(s.leases().unwrap()[0].state, "reserved");
    assert_eq!(fs::read_dir(t.path().join("recovery")).unwrap().count(), 1);
}
#[test]
fn startup_interrupts_but_does_not_replay_scan() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let scan = Scan {
        id: id(),
        status: "running".into(),
        roots: vec![],
        visited: 12,
        discovered: 3,
        skipped: vec![],
        coverage: Default::default(),
        providers: vec![],
        created_at: now(),
        updated_at: now(),
    };
    s.save("scans", &scan.id, &scan).unwrap();
    drop(s);
    let s = Store::open(t.path()).unwrap();
    let restored: Scan = s.get("scans", &scan.id).unwrap();
    assert_eq!(restored.status, "interrupted");
    assert_eq!(restored.visited, 12);
}
#[test]
fn refuse_root_escape_and_root_deletion() {
    let t = tempfile::tempdir().unwrap();
    let root = t.path().join("env");
    fs::create_dir(&root).unwrap();
    assert!(platform::checked_target(&root, &root).is_err());
    assert!(platform::checked_target(&t.path().join("other"), &root).is_err());
    assert!(platform::checked_target(&root.join("child"), &root).is_ok());
}
#[test]
fn plan_requires_project_and_does_not_overwrite() {
    let t = tempfile::tempdir().unwrap();
    let mut s = Store::open(&t.path().join("data")).unwrap();
    let root = t.path().join("env");
    fs::create_dir(&root).unwrap();
    s.config.env_root = Some(root.clone());
    let mut input:PlanInput=serde_json::from_value(json!({"action":"create","kind":"venv","name":"demo","manager":std::env::current_exe().unwrap()})).unwrap();
    assert!(operations::plan(&s, input.clone()).is_err());
    input.project = Some("project".into());
    fs::create_dir(root.join("demo")).unwrap();
    let op = operations::plan(&s, input.clone()).unwrap();
    assert_eq!(
        PathBuf::from(op.resource.path).file_name().unwrap(),
        "demo-2"
    );
    input.target = Some(root.join("demo"));
    assert!(operations::plan(&s, input).is_err());
}
