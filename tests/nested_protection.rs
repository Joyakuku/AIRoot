use airoot::{engine, model::*, operations, store::Store};
use serde_json::json;
use std::{
    fs,
    sync::{Arc, Mutex},
};

fn fixture() -> (tempfile::TempDir, Store, std::path::PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().join("envs");
    fs::create_dir_all(&root).unwrap();
    let data = tmp.path().join("data");
    let mut store = Store::open(&data).unwrap();
    store.config.env_root = Some(root.clone());
    store.save_config(store.config.clone()).unwrap();
    (tmp, store, root)
}

fn env(store: &Store, root: &std::path::Path, name: &str, parent: Option<&str>) -> Resource {
    let path = root.join(name);
    fs::create_dir_all(&path).unwrap();
    let mut r = Resource::new("env", "venv", name, &path.to_string_lossy());
    r.parent_id = parent.map(str::to_owned);
    store.upsert(r).unwrap().unwrap()
}

fn delete_plan(store: &Store, id: &str) -> anyhow::Result<Operation> {
    operations::plan(
        store,
        serde_json::from_value(json!({"action":"delete","resource_id":id}))?,
    )
}

fn confirms(r: &Resource) -> Vec<String> {
    vec![format!("delete:{}", r.id), "external-use-reviewed".into()]
}

fn api_acquire(shared: &Arc<Mutex<Store>>, id: &str) {
    engine::dispatch(
        shared.clone(),
        Request {
            command: "usage.acquire".into(),
            args: json!({"id":id,"pid":std::process::id()}),
        },
    )
    .unwrap();
}

#[test]
fn independent_nested_registration_blocks_delete_plan() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let _inner = env(&store, &root, "outer/inner", None);
    let error = delete_plan(&store, &outer.id).unwrap_err().to_string();
    assert!(error.contains("NESTED_RESOURCE_NOT_MANAGED"), "{error}");
    assert!(root.join("outer/inner").is_dir());
}

#[test]
fn external_pid_usage_of_independent_nested_env_blocks_delete_apply() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let inner = env(&store, &root, "outer/inner", Some(&outer.id));
    let plan = delete_plan(&store, &outer.id).unwrap();
    let shared = Arc::new(Mutex::new(store));
    api_acquire(&shared, &inner.id);
    let result = operations::apply(shared, &plan.id, &confirms(&outer));
    assert!(result.is_err(), "delete unexpectedly started");
    assert!(root.join("outer").is_dir());
    assert!(root.join("outer/inner").is_dir());
}

#[test]
fn path_only_nested_reservation_conflicts_with_ancestor_writer_at_component_boundary() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let outer_path = format!("path:{}", airoot::store::path_key(&outer.path));
    let nested = format!(
        "path:{}",
        airoot::store::path_key(&root.join("outer/inner").to_string_lossy())
    );
    let neighbor = format!(
        "path:{}",
        airoot::store::path_key(&root.join("outer-other").to_string_lossy())
    );
    let mut writer = store
        .acquire(vec![outer.id.clone(), outer_path], "write", None)
        .unwrap();
    assert!(store.acquire(vec![nested], "read", None).is_err());
    let separate = store.acquire(vec![neighbor], "read", None).unwrap();
    store.finish_lease(&mut writer).unwrap();
    let mut separate = separate;
    store.finish_lease(&mut separate).unwrap();
}

#[test]
fn related_ids_close_multilevel_tool_and_mcp_links_and_host_paths() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let nested = env(&store, &root, "outer/inner", Some(&outer.id));
    let mut tool = Resource::new(
        "tool",
        "cli",
        "tool",
        &root.join("outer/inner/bin/tool.exe").to_string_lossy(),
    );
    tool.parent_id = Some(nested.id.clone());
    let tool = store.upsert(tool).unwrap().unwrap();
    let mut mcp = Resource::new(
        "tool",
        "mcp-connector",
        "connector",
        &root.join("outer/inner/mcp.json").to_string_lossy(),
    );
    mcp.parent_id = Some(tool.id.clone());
    let mcp = store.upsert(mcp).unwrap().unwrap();
    let protected = store.related_ids(&mcp).unwrap();
    assert!(protected.contains(&outer.id));
    assert!(protected.contains(&nested.id));
    assert!(protected.contains(&tool.id));
    let mut lease = store.acquire(protected, "read", None).unwrap();
    assert!(
        store
            .acquire(store.related_ids(&outer).unwrap(), "write", None)
            .is_err()
    );
    store.finish_lease(&mut lease).unwrap();
}

#[test]
fn plan_rechecks_new_independent_nested_resource_before_any_delete() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let plan = delete_plan(&store, &outer.id).unwrap();
    let _inner = env(&store, &root, "outer/inner", None);
    let shared = Arc::new(Mutex::new(store));
    let result = operations::apply(shared, &plan.id, &confirms(&outer));
    assert!(
        result.is_err(),
        "apply ignored newly registered nested resource"
    );
    assert!(root.join("outer/inner").is_dir());
}

#[test]
fn apply_rechecks_parent_chain_changed_after_plan() {
    let (_tmp, store, root) = fixture();
    let outer = env(&store, &root, "outer", None);
    let inner = env(&store, &root, "outer/inner", Some(&outer.id));
    let plan = delete_plan(&store, &outer.id).unwrap();
    let mut detached = inner.clone();
    detached.parent_id = None;
    store.update_resource(&detached).unwrap();
    let shared = Arc::new(Mutex::new(store));
    let result = operations::apply(shared, &plan.id, &confirms(&outer));
    assert!(
        result.is_err(),
        "apply ignored changed deletion management chain"
    );
    assert!(root.join("outer/inner").is_dir());
}
