use crate::{adapters, model::*, platform, store::Store};
use anyhow::Result;
use rusqlite::params;
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
static WORKERS: std::sync::OnceLock<Mutex<std::collections::HashSet<String>>> =
    std::sync::OnceLock::new();
pub type Shared = Arc<Mutex<Store>>;
fn in_boundary(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|r| platform::path_is_under(path, r))
}
pub fn start(shared: Shared, requested: Vec<PathBuf>) -> Result<Scan> {
    let full_scan = requested.is_empty();
    let roots = if full_scan {
        platform::local_drives()
    } else {
        requested
    };
    anyhow::ensure!(!roots.is_empty(), "No eligible local disks");
    let disks = platform::local_drives();
    let disks: Vec<_> = disks
        .iter()
        .filter_map(|p| platform::canonical(p).ok())
        .collect();
    let roots: Vec<PathBuf> = roots
        .iter()
        .map(|r| {
            let c = platform::canonical_unlinked(r)?;
            anyhow::ensure!(
                in_boundary(&c, &disks),
                "Only local fixed disks are eligible"
            );
            Ok(c)
        })
        .collect::<Result<_>>()?;
    let s = shared.lock().unwrap();
    anyhow::ensure!(
        !s.scans()?.iter().any(|x| x.status == "running"),
        "Another scan is running"
    );
    let mut scan = Scan {
        id: id(),
        status: "running".into(),
        roots: roots.clone(),
        visited: 0,
        discovered: 0,
        skipped: vec![],
        coverage: json!({"state":"in_progress","queue_complete":false}),
        providers: vec![
            json!({"name":"manager-records","status":"enabled"}),
            json!({"name":"directory-walk","status":"fallback"}),
        ],
        created_at: now(),
        updated_at: now(),
    };
    scan.providers
        .push(json!({"name":"disk-boundaries","inventory":platform::disk_inventory()}));
    if full_scan {
        for name in ["wsl", "docker"] {
            scan.providers.push(json!({"name":name,"status":"pending"}));
        }
    }
    for name in ["es", "PET", "fd"] {
        scan.providers.push(json!({"name":name,"status":"pending"}));
    }
    let tx = s.db.unchecked_transaction()?;
    for r in &roots {
        tx.execute(
            "INSERT OR IGNORE INTO scan_queue(scan_id,path) VALUES(?1,?2)",
            params![scan.id, r.to_string_lossy()],
        )?;
    }
    for p in adapters::manager_candidates() {
        if let Ok(p) = platform::canonical_unlinked(&p) {
            if in_boundary(&p, &roots) {
                tx.execute(
                    "INSERT OR IGNORE INTO scan_queue(scan_id,path) VALUES(?1,?2)",
                    params![scan.id, p.to_string_lossy()],
                )?;
            }
        }
    }
    s.save("scans", &scan.id, &scan)?;
    tx.commit()?;
    drop(s);
    spawn(shared, scan.id.clone());
    Ok(scan)
}
pub fn resume(shared: Shared, id: &str) -> Result<Scan> {
    anyhow::ensure!(
        !WORKERS
            .get_or_init(Default::default)
            .lock()
            .unwrap()
            .contains(id),
        "SCAN_PAUSING: current batch is still finishing; resume after it stops"
    );
    let s = shared.lock().unwrap();
    let mut scan: Scan = s.get("scans", id)?;
    anyhow::ensure!(
        matches!(scan.status.as_str(), "paused" | "interrupted" | "failed"),
        "Scan cannot resume from {}",
        scan.status
    );
    anyhow::ensure!(
        !s.scans()?.iter().any(|x| x.status == "running"),
        "Another scan is running"
    );
    scan.status = "running".into();
    s.save("scans", id, &scan)?;
    drop(s);
    spawn(shared, id.into());
    Ok(scan)
}
fn spawn(shared: Shared, id: String) {
    WORKERS
        .get_or_init(Default::default)
        .lock()
        .unwrap()
        .insert(id.clone());
    std::thread::spawn(move || {
        let _ = providers(shared.clone(), &id);
        if let Err(e) = worker(shared.clone(), &id, None) {
            if let Ok(s) = shared.lock() {
                if let Ok(mut scan) = s.get::<Scan>("scans", &id) {
                    scan.status = "failed".into();
                    scan.skipped.push(json!({"reason":e.to_string()}));
                    let _ = s.save("scans", &id, &scan);
                }
            }
        }
        WORKERS
            .get_or_init(Default::default)
            .lock()
            .unwrap()
            .remove(&id);
    });
}
pub fn worker(shared: Shared, id: &str, deadline: Option<Instant>) -> Result<()> {
    loop {
        let (path, roots, excluded, indexed) = {
            let s = shared.lock().unwrap();
            let mut scan: Scan = s.get("scans", id)?;
            if scan.status != "running" {
                return Ok(());
            }
            if deadline.is_some_and(|d| Instant::now() >= d) {
                scan.status = "paused".into();
                s.save("scans", id, &scan)?;
                return Ok(());
            }
            let next = s.db.query_row(
                "SELECT path FROM scan_queue WHERE scan_id=?1 AND done=0 ORDER BY rowid LIMIT 1",
                [id],
                |r| r.get::<_, String>(0),
            );
            match next {
                Ok(p) => (PathBuf::from(p), scan.roots, excluded_roots(&s), false),
                Err(rusqlite::Error::QueryReturnedNoRows) => {
                    let mut gaps = scan.skipped.clone();
                    for provider in &scan.providers {
                        if matches!(provider["name"].as_str(), Some("wsl" | "docker"))
                            && !matches!(provider["status"].as_str(), Some("completed" | "queried"))
                        {
                            gaps.push(json!({"provider":provider,"reason":"platform coverage separate or unavailable"}));
                        }
                    }
                    scan.coverage = json!({"state":if gaps.is_empty(){"supported_signatures_checked"}else{"partial"},"queue_complete":true,"scope":scan.roots,"uncovered":gaps,"note":"Directory fallback checks supported signatures only; platform provider outcomes remain separate"});
                    scan.status = if scan.coverage["state"] == "partial" {
                        "completed_with_gaps"
                    } else {
                        "completed"
                    }
                    .into();
                    scan.updated_at = now();
                    s.save("scans", id, &scan)?;
                    s.backup()?;
                    return Ok(());
                }
                Err(e) => return Err(e.into()),
            }
        };
        let mut children = vec![];
        let mut resources = vec![];
        let mut skipped = vec![];
        // Keep the persisted spelling as the queue key even when resolution changes it.
        let queued_path = path;
        let checked = platform::canonical_unlinked(&queued_path).and_then(|path| {
            anyhow::ensure!(
                in_boundary(&path, &roots),
                "Resolved path is outside fixed scan roots"
            );
            anyhow::ensure!(
                !in_boundary(&path, &excluded),
                "AIRoot data or component directory excluded"
            );
            anyhow::ensure!(
                !inside_component_bundle(&path),
                "AIRoot bundled components excluded"
            );
            Ok(path)
        });
        if let Err(error) = &checked {
            skipped.push(json!({"path":queued_path,"reason":error.to_string()}));
        }
        if let Ok(path) = checked {
            match adapters::inspect(&path) {
                Ok(r) => resources = r,
                Err(e) => skipped.push(json!({"path":path,"reason":e.to_string()})),
            }
            if !indexed {
                match fs::read_dir(&path) {
                    Ok(entries) => {
                        for entry in entries {
                            match entry {
                                Ok(e) => match child_directory(&e, &mut skipped) {
                                    Some(p)
                                        if in_boundary(&p, &roots)
                                            && !inside_component_bundle(&p) =>
                                    {
                                        children.push(p);
                                    }
                                    _ => {}
                                },
                                Err(e) => skipped.push(json!({"path":path,"reason":e.to_string()})),
                            }
                        }
                    }
                    Err(e) => skipped.push(json!({"path":path,"reason":e.to_string()})),
                }
            }
        }
        let s = shared.lock().unwrap();
        let mut scan: Scan = s.get("scans", id)?;
        let tx = s.db.unchecked_transaction()?;
        for r in resources {
            let resource_path = r.path.clone();
            match s.upsert(r) {
                Ok(Some(_)) => scan.discovered += 1,
                Ok(None) => {}
                Err(error) if error.to_string().starts_with("RESOURCE_INSTANCE_") => {
                    skipped.push(json!({"path":resource_path,"reason":error.to_string()}));
                }
                Err(error) => return Err(error),
            }
        }
        for child in children {
            tx.execute(
                "INSERT OR IGNORE INTO scan_queue(scan_id,path) VALUES(?1,?2)",
                params![id, child.to_string_lossy()],
            )?;
        }
        tx.execute(
            "UPDATE scan_queue SET done=1 WHERE scan_id=?1 AND path=?2",
            params![id, queued_path.to_string_lossy()],
        )?;
        scan.coverage = json!({"state":"in_progress","queue_complete":false,"scope":scan.roots,"uncovered":scan.skipped,"fallback":"persistent-directory-queue"});
        scan.visited += 1;
        append_skipped(&mut scan.skipped, skipped);
        scan.updated_at = now();
        s.save("scans", id, &scan)?;
        tx.commit()?;
    }
}
pub fn find(shared: Shared, query: &str, roots: Vec<PathBuf>) -> Result<serde_json::Value> {
    let (matched, timeout, excluded) = {
        let s = shared.lock().unwrap();
        (
            s.resources()?
                .into_iter()
                .filter(|r| r.name.to_lowercase().contains(&query.to_lowercase()))
                .collect::<Vec<_>>(),
            s.config.find_timeout_seconds,
            excluded_roots(&s),
        )
    };
    if !matched.is_empty() {
        return Ok(json!({"matches":matched,"coverage":"registry"}));
    }
    if roots.is_empty() {
        return Ok(
            json!({"matches":[],"coverage":"registry only","uncovered":"Specify --root for bounded supplementary search; absence is not proof of nonexistence"}),
        );
    }
    let deadline = Instant::now() + Duration::from_secs(timeout);
    let mut queue = std::collections::VecDeque::new();
    let mut matches = vec![];
    let mut skipped = vec![];
    let disks: Vec<_> = platform::local_drives()
        .iter()
        .filter_map(|p| platform::canonical(p).ok())
        .collect();
    let mut fixed_roots = vec![];
    for root in &roots {
        match platform::canonical_unlinked(root) {
            Ok(path) if in_boundary(&path, &disks) => {
                if !fixed_roots.contains(&path) {
                    fixed_roots.push(path.clone());
                    queue.push_back(path);
                }
            }
            Ok(_) => {
                skipped.push(json!({"path":root,"reason":"Only local fixed disks are eligible"}))
            }
            Err(error) => skipped.push(json!({"path":root,"reason":error.to_string()})),
        }
    }
    while let Some(p) = queue.pop_front() {
        if Instant::now() >= deadline {
            queue.push_front(p);
            break;
        }
        let original = p;
        let checked = platform::canonical_unlinked(&original).and_then(|p| {
            anyhow::ensure!(
                in_boundary(&p, &fixed_roots) && in_boundary(&p, &disks),
                "Resolved path is outside fixed local search roots"
            );
            anyhow::ensure!(
                !in_boundary(&p, &excluded),
                "AIRoot data or component directory excluded"
            );
            anyhow::ensure!(
                !inside_component_bundle(&p),
                "AIRoot bundled components excluded"
            );
            Ok(p)
        });
        let p = match checked {
            Ok(p) => p,
            Err(error) => {
                skipped.push(json!({"path":original,"reason":error.to_string()}));
                continue;
            }
        };
        match adapters::inspect(&p) {
            Ok(resources) => {
                for r in resources {
                    if r.name.to_lowercase().contains(&query.to_lowercase()) {
                        let path = r.path.clone();
                        match shared.lock().unwrap().upsert(r) {
                            Ok(Some(r)) => matches.push(r),
                            Ok(None) => {}
                            Err(error) => skipped.push(json!({"path":path,"reason":format!("Resource registration conflict: {error:#}")})),
                        }
                    }
                }
            }
            Err(error) => skipped
                .push(json!({"path":p,"reason":format!("Resource inspection failed: {error:#}")})),
        }
        if let Ok(entries) = fs::read_dir(&p) {
            for entry in entries {
                if Instant::now() >= deadline {
                    queue.push_front(p.clone());
                    break;
                }
                match entry {
                    Ok(entry) => {
                        if let Some(child) = child_directory(&entry, &mut skipped) {
                            if !inside_component_bundle(&child) {
                                queue.push_back(child);
                            }
                        }
                    }
                    Err(error) => skipped.push(json!({"path":p,"reason":error.to_string()})),
                }
            }
        } else {
            skipped.push(json!({"path":p,"reason":"unreadable"}));
        }
    }
    let complete = queue.is_empty() && skipped.is_empty();
    Ok(
        json!({"matches":matches,"scope":roots,"uncovered":queue,"skipped":skipped,"coverage":"bounded local fallback","complete":complete}),
    )
}

fn providers(shared: Shared, id: &str) -> Result<()> {
    for kind in ["wsl", "docker"] {
        let should_probe = {
            let s = shared.lock().unwrap();
            let scan: Scan = s.get("scans", id)?;
            if scan.status != "running" {
                return Ok(());
            }
            if !scan
                .providers
                .iter()
                .any(|p| p["name"] == kind && p["status"] == "pending")
            {
                continue;
            }
            true
        };
        if !should_probe {
            continue;
        }
        let result = crate::platform_resources::probe(kind);
        if !commit_platform_probe(&shared, id, kind, result)? {
            return Ok(());
        }
    }

    let roots = { shared.lock().unwrap().get::<Scan>("scans", id)?.roots };
    for root in &roots {
        for name in ["es", "PET", "fd"] {
            let (config, data, done) = {
                let s = shared.lock().unwrap();
                let scan: Scan = s.get("scans", id)?;
                if scan.status != "running" {
                    return Ok(());
                }
                (
                    s.config.clone(),
                    s.root.clone(),
                    scan.providers.iter().any(|p| {
                        p["name"] == name && p["root"] == json!(root) && p["status"] != "pending"
                    }),
                )
            };
            if done {
                continue;
            }
            let view = Store {
                db: rusqlite::Connection::open_in_memory()?,
                root: data,
                config,
                recovery: None,
                active_verifications: Default::default(),
            };
            let result = (|| {
                if name == "es" {
                    crate::components::start(&view, &roots)?;
                }
                crate::components::candidates(&view, name, std::slice::from_ref(root))
            })();
            let s = shared.lock().unwrap();
            let mut scan: Scan = s.get("scans", id)?;
            let tx = s.db.unchecked_transaction()?;
            let status = match result {
                Ok(batch) => {
                    for path in &batch.paths {
                        if in_boundary(path, &scan.roots) {
                            tx.execute(
                                "INSERT OR IGNORE INTO scan_queue(scan_id,path) VALUES(?1,?2)",
                                params![id, path.to_string_lossy()],
                            )?;
                        }
                    }
                    json!({"name":name,"root":root,"status":"queried","candidates":batch.paths.len(),"truncated":batch.truncated,"reason":batch.reason,"fallback":"persistent-directory-queue"})
                }
                Err(e) => {
                    json!({"name":name,"root":root,"status":"unavailable","reason":e.to_string(),"fallback":"persistent-directory-queue"})
                }
            };
            scan.providers
                .retain(|p| !(p["name"] == name && p.get("root").is_none()));
            scan.providers.push(status);
            s.save("scans", id, &scan)?;
            tx.commit()?;
        }
    }
    Ok(())
}

fn commit_platform_probe(
    shared: &Shared,
    id: &str,
    kind: &str,
    result: Result<Vec<Resource>>,
) -> Result<bool> {
    let s = shared.lock().unwrap();
    let mut scan: Scan = s.get("scans", id)?;
    if scan.status != "running" {
        return Ok(false);
    }
    let status = match result {
        Ok(rows) => {
            scan.discovered += crate::platform_resources::register_rows(&s, rows)?
                .iter()
                .filter(|r| r.is_some())
                .count() as u64;
            json!({"name":kind,"status":"queried","coverage":"host platform only"})
        }
        Err(e) => json!({"name":kind,"status":"unavailable","reason":e.to_string()}),
    };
    if let Some(provider) = scan.providers.iter_mut().find(|p| p["name"] == kind) {
        *provider = status;
    }
    s.save("scans", id, &scan)?;
    Ok(true)
}

fn excluded_roots(s: &Store) -> Vec<PathBuf> {
    let mut roots = vec![s.root.clone(), s.config.component_dir.clone()];
    for root in [&s.config.env_root, &s.config.tools_root]
        .into_iter()
        .flatten()
    {
        roots.push(root.join(".cache"));
    }
    roots
}

fn inside_component_bundle(path: &Path) -> bool {
    path.ancestors().any(|ancestor| {
        let Ok(bytes) = fs::read(ancestor.join("manifest.json")) else {
            return false;
        };
        let Ok(value) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
            return false;
        };
        value["schema_version"] == 1
            && value["components"].as_array().is_some_and(|items| {
                !items.is_empty()
                    && items.iter().all(|item| {
                        item["name"].is_string()
                            && item["file"].is_string()
                            && item["sha256"].is_string()
                    })
            })
    })
}
fn append_skipped(destination: &mut Vec<serde_json::Value>, entries: Vec<serde_json::Value>) {
    for entry in entries {
        if destination.len() < 1000 {
            destination.push(entry);
        } else if destination.len() == 1000 {
            destination.push(json!({"reason":"Further skipped paths omitted; coverage remains incomplete", "additional_count":1}));
        } else {
            let count = destination[1000]["additional_count"].as_u64().unwrap_or(0);
            destination[1000]["additional_count"] = json!(count + 1);
        }
    }
}

/// Inspect links before filtering directories: symlink/junction file types are not directories.
fn child_directory(entry: &fs::DirEntry, skipped: &mut Vec<serde_json::Value>) -> Option<PathBuf> {
    let path = entry.path();
    let result = (|| -> Result<Option<PathBuf>> {
        let kind = entry.file_type()?;
        #[cfg(windows)]
        let linked = {
            use std::os::windows::fs::MetadataExt;
            // DirEntry metadata uses the directory enumeration's cached attributes.
            entry.metadata()?.file_attributes() & 0x400 != 0
        };
        #[cfg(not(windows))]
        let linked = kind.is_symlink();
        if linked {
            skipped.push(json!({"path":path,"reason":"linked entry excluded"}));
            return Ok(None);
        }
        Ok(kind.is_dir().then_some(path.clone()))
    })();
    match result {
        Ok(directory) => directory,
        Err(error) => {
            skipped.push(json!({"path":path,"reason":error.to_string()}));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn platform_probe_result_is_not_registered_after_pause() {
        let tmp = tempfile::tempdir().unwrap();
        let store = Store::open(&tmp.path().join("data")).unwrap();
        let shared = Arc::new(Mutex::new(store));
        let scan = Scan {
            id: id(),
            status: "paused".into(),
            roots: vec![],
            visited: 0,
            discovered: 0,
            skipped: vec![],
            providers: vec![json!({"name":"wsl","status":"pending"})],
            coverage: json!({"state":"in_progress"}),
            created_at: now(),
            updated_at: now(),
        };
        shared
            .lock()
            .unwrap()
            .save("scans", &scan.id, &scan)
            .unwrap();
        let mut candidate = Resource::new("env", "wsl", "test-distro", "wsl://test-distro");
        candidate.scope = "wsl-platform".into();

        assert!(!commit_platform_probe(&shared, &scan.id, "wsl", Ok(vec![candidate])).unwrap());

        let store = shared.lock().unwrap();
        let saved: Scan = store.get("scans", &scan.id).unwrap();
        assert_eq!(saved.providers[0]["status"], "pending");
        assert!(store.resources().unwrap().is_empty());
    }

    #[test]
    fn scan_skips_conda_fixtures_and_airroot_component_bundles() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("scan-root");
        let conda_fixture = root.join("pet/tests/unix/conda-env");
        fs::create_dir_all(conda_fixture.join("conda-meta")).unwrap();
        fs::write(conda_fixture.join("conda-meta/history"), "fixture").unwrap();

        let bundle = root.join("plugin/runtime/components");
        fs::create_dir_all(&bundle).unwrap();
        fs::write(
            bundle.join("manifest.json"),
            r#"{"schema_version":1,"components":[{"name":"fd","file":"fd.exe","sha256":"abc"}]}"#,
        )
        .unwrap();
        fs::write(bundle.join("fd.exe"), b"bundled component").unwrap();

        let store = Store::open(&tmp.path().join("data")).unwrap();
        let shared = Arc::new(Mutex::new(store));
        let root = platform::canonical(&root).unwrap();
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
                    params![scan.id, root.to_string_lossy()],
                )
                .unwrap();
        }

        worker(shared.clone(), &scan.id, None).unwrap();

        let store = shared.lock().unwrap();
        assert!(store.resources().unwrap().is_empty());
        let completed: Scan = store.get("scans", &scan.id).unwrap();
        assert!(matches!(
            completed.status.as_str(),
            "completed" | "completed_with_gaps"
        ));
    }
}
