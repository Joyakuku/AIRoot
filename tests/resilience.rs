use airoot::{engine, model::*, operations, platform, portable, scan, store::Store};
use serde_json::json;
use std::{
    fs,
    io::Write,
    sync::{Arc, Mutex},
};

#[test]
fn stale_plan_and_live_lease_block_delete() {
    let t = tempfile::tempdir().unwrap();
    let env = t.path().join("env");
    fs::create_dir(&env).unwrap();
    let p = env.join("demo");
    fs::create_dir(&p).unwrap();
    fs::write(p.join("pyvenv.cfg"), "home = test").unwrap();
    let mut s = Store::open(&t.path().join("data")).unwrap();
    s.config.env_root = Some(env);
    let r = s
        .upsert(Resource::new("env", "venv", "demo", p.to_str().unwrap()))
        .unwrap()
        .unwrap();
    let input: PlanInput =
        serde_json::from_value(json!({"action":"delete","resource_id":r.id})).unwrap();
    let op = operations::plan(&s, input.clone()).unwrap();
    let mut changed = r.clone();
    changed.projects.push("new-project".into());
    s.update_resource(&changed).unwrap();
    let shared = Arc::new(Mutex::new(s));
    let confirms = vec![format!("delete:{}", r.id), "external-use-reviewed".into()];
    assert!(
        operations::apply(shared.clone(), &op.id, &confirms)
            .unwrap_err()
            .to_string()
            .contains("PLAN_CHANGED")
    );
    let op = operations::plan(&shared.lock().unwrap(), input).unwrap();
    shared
        .lock()
        .unwrap()
        .acquire(vec![r.id], "read", None)
        .unwrap();
    assert!(operations::apply(shared, &op.id, &confirms).is_err());
    assert!(p.is_dir());
}

#[test]
fn paused_scan_keeps_queue_until_explicit_resume() {
    let t = tempfile::tempdir().unwrap();
    let dir = t.path().join("samples");
    fs::create_dir(&dir).unwrap();
    fs::write(dir.join("pyvenv.cfg"), "version = 3.13.9").unwrap();
    let dir = platform::canonical(&dir).unwrap();
    let s = Store::open(&t.path().join("data")).unwrap();
    let job = Scan {
        id: id(),
        status: "paused".into(),
        roots: vec![dir.clone()],
        visited: 0,
        discovered: 0,
        skipped: vec![],
        coverage: Default::default(),
        providers: vec![],
        created_at: now(),
        updated_at: now(),
    };
    s.save("scans", &job.id, &job).unwrap();
    s.db.execute(
        "INSERT INTO scan_queue(scan_id,path) VALUES(?1,?2)",
        rusqlite::params![job.id, dir.to_str().unwrap()],
    )
    .unwrap();
    let shared = Arc::new(Mutex::new(s));
    scan::worker(shared.clone(), &job.id, None).unwrap();
    assert_eq!(
        shared
            .lock()
            .unwrap()
            .get::<Scan>("scans", &job.id)
            .unwrap()
            .visited,
        0
    );
    scan::resume(shared.clone(), &job.id).unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    loop {
        let j: Scan = shared.lock().unwrap().get("scans", &job.id).unwrap();
        if j.status != "running" {
            assert_eq!(j.visited, 1);
            break;
        }
        assert!(std::time::Instant::now() < deadline);
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert_eq!(shared.lock().unwrap().resources().unwrap().len(), 1);
}

fn archive_test(names: &[&str], valid: bool) {
    let t = tempfile::tempdir().unwrap();
    let archive = t.path().join("sample.zip");
    let mut zip = zip::ZipWriter::new(fs::File::create(&archive).unwrap());
    for name in names {
        zip.start_file(*name, zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"data").unwrap();
    }
    zip.finish().unwrap();
    let target = t.path().join("target");
    fs::create_dir(&target).unwrap();
    let result = portable::extract(&archive, &target, &platform::hash_file(&archive).unwrap());
    assert_eq!(result.is_ok(), valid, "{result:?}");
    if !valid {
        assert_eq!(
            fs::read_dir(target).unwrap().count(),
            0,
            "prevalidate all members before writing any"
        );
    }
}
#[test]
fn archive_rejects_traversal_before_any_write() {
    archive_test(&["good/file.txt", "../escaped"], false);
}
#[test]
fn archive_rejects_windows_aliases() {
    archive_test(&["good", "NUL.txt"], false);
    archive_test(&["a", "A"], false);
    archive_test(&["a:stream"], false);
}
#[test]
fn archive_supports_spaces_and_chinese() {
    archive_test(&["中文 folder/test.txt"], true);
}
#[test]
fn info_and_resource_roots_cannot_overlap() {
    let t = tempfile::tempdir().unwrap();
    let shared = Arc::new(Mutex::new(Store::open(t.path()).unwrap()));
    let result = engine::dispatch(
        shared,
        Request {
            command: "config.set".into(),
            args: json!({"env_root":t.path(),"initialize":false}),
        },
    );
    assert!(result.is_err());
}
#[test]
fn preparation_database_failure_has_no_side_effects() {
    let t = tempfile::tempdir().unwrap();
    let env = t.path().join("env");
    fs::create_dir(&env).unwrap();
    let mut s = Store::open(&t.path().join("data")).unwrap();
    s.config.env_root = Some(env.clone());
    let op=operations::plan(&s,serde_json::from_value(json!({"action":"create","kind":"venv","name":"demo","project":"p","manager":std::env::current_exe().unwrap()})).unwrap()).unwrap();
    s.db.execute_batch("PRAGMA query_only=ON").unwrap();
    let shared = Arc::new(Mutex::new(s));
    assert!(operations::apply(shared.clone(), &op.id, &[]).is_err());
    assert!(!env.join("demo").exists());
    assert!(
        shared
            .lock()
            .unwrap()
            .leases()
            .unwrap()
            .iter()
            .all(|l| l.state == "released")
    );
}

#[test]
fn reusable_scripts_sharing_interpreter_remain_distinct() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    for path in ["D:/scripts/a.py", "D:/scripts/b.py"] {
        let mut r = Resource::new("tool", "script", "script", path);
        r.entry = Some("D:/env/python.exe".into());
        s.upsert(r).unwrap();
    }
    assert_eq!(s.resources().unwrap().len(), 2);
}
#[test]
fn linux_instance_paths_remain_case_sensitive() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    for path in ["/env/foo", "/env/Foo"] {
        let mut r = Resource::new("env", "venv", "env", path);
        r.scope = "wsl:test".into();
        r.entry = Some(format!("{path}/bin/python"));
        s.upsert(r).unwrap();
    }
    assert_eq!(s.resources().unwrap().len(), 2);
}
#[test]
fn ignored_portable_root_cannot_reappear_through_entry() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let mut r = Resource::new("tool", "cli", "fd", "D:/tools/fd");
    r.entry = Some("D:/tools/fd/bin/fd.exe".into());
    let r = s.upsert(r).unwrap().unwrap();
    s.ignore(&r.id).unwrap();
    let mut rediscovered = r.clone();
    rediscovered.path = rediscovered.entry.clone().unwrap();
    assert!(s.upsert(rediscovered).unwrap().is_none());
    assert!(s.resources().unwrap().is_empty());
}
#[test]
fn corrupt_config_restores_verified_snapshot_and_preserves_original() {
    let t = tempfile::tempdir().unwrap();
    let mut s = Store::open(t.path()).unwrap();
    let mut c = s.config.clone();
    c.env_root = Some(t.path().join("environments"));
    s.save_config(c.clone()).unwrap();
    s.backup().unwrap();
    drop(s);
    fs::write(t.path().join("config.json"), b"corrupt config").unwrap();
    let restored = Store::open(t.path()).unwrap();
    assert_eq!(restored.config.env_root, c.env_root);
    assert!(t.path().join("config-recovery-notice.json").is_file());
    assert!(
        fs::read_dir(t.path().join("recovery"))
            .unwrap()
            .any(|p| fs::read(p.unwrap().path()).unwrap() == b"corrupt config")
    );
}

#[test]
fn deletion_retains_external_script_history_and_removes_internal_descendants() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let mut owner = Resource::new("env", "wsl", "sample", "wsl://sample");
    owner.scope = "wsl-platform".into();
    let owner = s.upsert(owner).unwrap().unwrap();
    let mut internal = Resource::new("env", "venv", "nested", "/env/a");
    internal.scope = format!("wsl:{}", owner.id);
    internal.parent_id = Some(owner.id.clone());
    let internal = s.upsert(internal).unwrap().unwrap();
    let mut cli = Resource::new("tool", "cli", "tool", "/env/a/bin/tool");
    cli.scope = internal.scope.clone();
    cli.parent_id = Some(internal.id.clone());
    s.upsert(cli).unwrap();
    let mut external = Resource::new("tool", "script", "external", "D:/scripts/external.py");
    external.parent_id = Some(internal.id);
    let external = s.upsert(external).unwrap().unwrap();
    s.register_deletion(&owner).unwrap();
    let rows = s.resources().unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].id, external.id);
    assert_eq!(rows[0].status, "unverified");
    assert!(rows[0].parent_id.is_none());
}
#[test]
fn bootstrap_unreviewed_source_has_no_saved_plan() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let input:PlanInput=serde_json::from_value(json!({"action":"bootstrap","kind":"python","source":"https://untrusted.example/python-amd64.exe"})).unwrap();
    assert!(operations::plan(&s, input).is_err());
    assert!(s.operations().unwrap().is_empty());
}

#[test]
fn repeated_platform_discovery_preserves_parent_ids_and_projects() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let rows = || {
        let mut runtime = Resource::new("env", "wsl-runtime", "WSL", "wsl://platform");
        runtime.scope = "wsl-platform".into();
        let mut child = Resource::new("env", "wsl", "sample", "wsl://sample");
        child.scope = runtime.scope.clone();
        child.parent_id = Some(runtime.id.clone());
        vec![runtime, child]
    };
    let first = airoot::platform_resources::register_rows(&s, rows()).unwrap();
    let mut child = first[1].clone().unwrap();
    child.projects.push("project-a".into());
    s.update_resource(&child).unwrap();
    let second = airoot::platform_resources::register_rows(&s, rows()).unwrap();
    assert_eq!(
        first[0].as_ref().unwrap().id,
        second[0].as_ref().unwrap().id
    );
    let actual = second[1].as_ref().unwrap();
    assert_eq!(actual.id, child.id);
    assert_eq!(
        actual.parent_id,
        Some(second[0].as_ref().unwrap().id.clone())
    );
    assert_eq!(actual.projects, vec!["project-a"]);
    assert_eq!(s.resources().unwrap().len(), 2);
}

#[test]
fn wsl_delete_is_rejected_when_planning_and_revalidating_legacy_plans() {
    let t = tempfile::tempdir().unwrap();
    let s = Store::open(t.path()).unwrap();
    let mut resource = Resource::new("env", "wsl", "replaceable-name", "wsl://replaceable-name");
    resource.scope = "wsl-platform".into();
    resource.metadata = json!({"identity":{"registration_id":"old-guid","base_path":"old-path"}});
    let resource = s.upsert(resource).unwrap().unwrap();
    let input: PlanInput =
        serde_json::from_value(json!({"action":"delete","resource_id":resource.id})).unwrap();
    let error = airoot::platform_resources::plan(&s, input.clone())
        .unwrap_err()
        .to_string();
    assert!(error.contains("WSL_AUTO_UNREGISTER_DISABLED"));
    assert!(s.operations().unwrap().is_empty());

    let mut legacy_plan: Operation = serde_json::from_value(json!({
        "id":"legacy-wsl-delete",
        "input":input,
        "resource":resource,
        "fingerprint":"legacy",
        "risk":"high",
        "requirements":["delete:legacy"],
        "effects":["legacy deletion"],
        "steps":[{"program":"wsl.exe","args":["--unregister","replaceable-name"]}],
        "status":"planned",
        "execution_status":"not_started",
        "verification_status":"not_started",
        "registration_status":"not_started",
        "created_at":"2026-10-28T00:00:00Z",
        "updated_at":"2026-10-28T00:00:00Z",
        "error":null,
        "attempts":0
    }))
    .unwrap();
    let error = airoot::platform_resources::revalidate(&legacy_plan)
        .unwrap_err()
        .to_string();
    assert!(error.contains("WSL_AUTO_UNREGISTER_DISABLED"));

    legacy_plan.resource.scope = "host".into();
    s.save("operations", &legacy_plan.id, &legacy_plan).unwrap();
    let shared = std::sync::Arc::new(std::sync::Mutex::new(s));
    let error = operations::apply(shared, &legacy_plan.id, &[])
        .unwrap_err()
        .to_string();
    assert!(error.contains("WSL_AUTO_UNREGISTER_DISABLED"));
}

#[test]
fn conda_upgrade_and_explicit_source_are_concrete_plan_arguments() {
    let t = tempfile::tempdir().unwrap();
    let envs = t.path().join("envs");
    let target = envs.join("sample");
    fs::create_dir_all(&target).unwrap();
    let mut s = Store::open(&t.path().join("data")).unwrap();
    s.config.env_root = Some(envs);
    let manager = std::env::current_exe().unwrap();
    let mut r = Resource::new("env", "conda", "sample", target.to_str().unwrap());
    r.metadata["manager"] = json!(manager);
    let r = s.upsert(r).unwrap().unwrap();
    let input: PlanInput =
        serde_json::from_value(json!({"action":"upgrade-packages","resource_id":r.id,
        "packages":["pip"],"source":"https://conda.anaconda.org/conda-forge"}))
        .unwrap();
    let op = operations::plan(&s, input).unwrap();
    assert_eq!(op.steps[0].args[0], "update");
    assert!(op.steps[0].args.windows(3).any(|a| a
        == [
            "--override-channels",
            "--channel",
            "https://conda.anaconda.org/conda-forge"
        ]));
    assert!(
        op.requirements
            .contains(&"compatibility-reviewed".to_owned())
    );
}

#[test]
fn scan_reports_replaced_registered_instance_as_gap_and_continues() {
    let t = tempfile::tempdir().unwrap();
    let root = t.path().join("samples");
    fs::create_dir(&root).unwrap();
    fs::write(root.join("pyvenv.cfg"), "version = 3.12.0").unwrap();
    let store = Store::open(&t.path().join("data")).unwrap();
    let mut registered = Resource::new("env", "venv", "sample", &root.to_string_lossy());
    registered.projects = vec!["historic-project".into()];
    let registered = store.upsert(registered).unwrap().unwrap();

    let moved = t.path().join("old-samples");
    fs::rename(&root, &moved).unwrap();
    fs::create_dir(&root).unwrap();
    fs::write(root.join("pyvenv.cfg"), "version = 3.13.0").unwrap();
    assert_ne!(
        registered.metadata["instance_identity"],
        airoot::recovery::identity(&root).unwrap()
    );
    assert_eq!(
        airoot::recovery::identity(&root).unwrap(),
        airoot::recovery::identity(std::path::Path::new(&registered.path)).unwrap()
    );

    let root = platform::canonical(&root).unwrap();
    let job = Scan {
        id: id(),
        status: "running".into(),
        roots: vec![root.clone()],
        visited: 0,
        discovered: 0,
        skipped: vec![],
        coverage: Default::default(),
        providers: vec![],
        created_at: now(),
        updated_at: now(),
    };
    store.save("scans", &job.id, &job).unwrap();
    store
        .db
        .execute(
            "INSERT INTO scan_queue(scan_id,path) VALUES(?1,?2)",
            rusqlite::params![job.id, root.to_string_lossy()],
        )
        .unwrap();
    let shared = Arc::new(Mutex::new(store));

    scan::worker(shared.clone(), &job.id, None).unwrap();

    let store = shared.lock().unwrap();
    let finished: Scan = store.get("scans", &job.id).unwrap();
    assert_eq!(finished.status, "completed_with_gaps", "{finished:?}");
    assert_eq!(finished.coverage["queue_complete"], true);
    assert_eq!(finished.coverage["state"], "partial");
    assert!(finished.skipped.iter().any(|item| {
        item["reason"]
            .as_str()
            .is_some_and(|reason| reason.contains("RESOURCE_INSTANCE_CHANGED"))
    }));
    let preserved = store.resource(&registered.id).unwrap();
    assert_eq!(preserved.projects, vec!["historic-project"]);
    assert_eq!(preserved.version, None);
}
