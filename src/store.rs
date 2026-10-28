use crate::model::*;
use anyhow::{Context, Result, bail};
use rusqlite::{Connection, params};
use serde::{Serialize, de::DeserializeOwned};
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

pub struct Store {
    pub db: Connection,
    pub root: PathBuf,
    pub config: Config,
    pub recovery: Option<String>,
    pub(crate) active_verifications: Arc<Mutex<HashSet<String>>>,
}

/// Worker-local request ownership supplements the durable lease. Dropping this
/// guard ends only the request; it must never release uncertain usage on disk.
pub(crate) struct VerificationRequest {
    lease_id: String,
    active: Arc<Mutex<HashSet<String>>>,
}
impl Drop for VerificationRequest {
    fn drop(&mut self) {
        // No Store lock: a guard may be dropped while its caller still owns it.
        // Poisoning remains fail-closed in verification_active().
        if let Ok(mut active) = self.active.lock() {
            active.remove(&self.lease_id);
        }
    }
}

struct LeaseRequest {
    ids: Vec<String>,
    mode: String,
    project: Option<String>,
    operation_id: Option<String>,
    pid: Option<u32>,
    process_stamp: Option<u64>,
    state: String,
}

pub fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let temp = path.with_extension(format!("{}.tmp", id()));
    let result = (|| {
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        use std::io::Write;
        f.write_all(&serde_json::to_vec_pretty(value)?)?;
        f.sync_all()?;
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            let src: Vec<u16> = temp.as_os_str().encode_wide().chain(Some(0)).collect();
            let dst: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            unsafe {
                if windows_sys::Win32::Storage::FileSystem::MoveFileExW(
                    src.as_ptr(),
                    dst.as_ptr(),
                    windows_sys::Win32::Storage::FileSystem::MOVEFILE_REPLACE_EXISTING
                        | windows_sys::Win32::Storage::FileSystem::MOVEFILE_WRITE_THROUGH,
                ) == 0
                {
                    return Err(std::io::Error::last_os_error().into());
                }
            }
        }
        #[cfg(not(windows))]
        fs::rename(&temp, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}
pub fn read_json<T: DeserializeOwned>(p: &Path) -> Result<T> {
    Ok(serde_json::from_slice(&fs::read(p)?)?)
}
pub fn path_key(path: &str) -> String {
    #[cfg(windows)]
    {
        path.trim_end_matches(['\\', '/'])
            .replace('/', "\\")
            .to_lowercase()
    }
    #[cfg(not(windows))]
    {
        path.trim_end_matches('/').into()
    }
}

pub fn paths_overlap(a: &str, b: &str) -> bool {
    path_is_within_or_equal(a, b) || path_is_within_or_equal(b, a)
}

fn path_is_within_or_equal(path: &str, root: &str) -> bool {
    let path = path_key(path).replace('/', "\\");
    let root = path_key(root).replace('/', "\\");
    path == root
        || path
            .strip_prefix(&root)
            .is_some_and(|tail| tail.starts_with('\\'))
}

fn paths_conflict(a: &str, b: &str) -> bool {
    let a = a.strip_prefix("path:").unwrap_or(a);
    let b = b.strip_prefix("path:").unwrap_or(b);
    path_is_within_or_equal(a, b) || path_is_within_or_equal(b, a)
}

/// Shared lease/path conflict check for normal operations and recovery guards.
pub fn protection_conflicts(a: &[String], b: &[String]) -> bool {
    a.iter().any(|left| {
        b.iter().any(|right| {
            if left == right {
                true
            } else if left.starts_with("path:") && right.starts_with("path:") {
                paths_conflict(left, right)
            } else {
                false
            }
        })
    })
}
impl Store {
    pub fn open(root: &Path) -> Result<Self> {
        fs::create_dir_all(root)?;
        for dir in [
            "backups",
            "logs",
            "usage",
            "recovery",
            "components",
            "pending",
        ] {
            fs::create_dir_all(root.join(dir))?;
        }
        let config_path = root.join("config.json");
        let config = if config_path.exists() {
            match read_json(&config_path) {
                Ok(c) => c,
                Err(error) => {
                    let mut candidates = fs::read_dir(root.join("backups"))?
                        .filter_map(|e| e.ok().map(|e| e.path()))
                        .filter(|p| p.extension().is_some_and(|e| e == "sqlite3"))
                        .collect::<Vec<_>>();
                    candidates.sort();
                    candidates.reverse();
                    let recovered=candidates.into_iter().filter(|p|Self::integrity(p).unwrap_or(false)).find_map(|p|read_json::<Config>(&p.with_extension("config.json")).ok()).with_context(||format!("Configuration unreadable and no valid configuration backup: {error}"))?;
                    fs::copy(
                        &config_path,
                        root.join("recovery").join(format!("config-{}.json", id())),
                    )?;
                    atomic_json(&config_path, &recovered)?;
                    atomic_json(
                        &root.join("config-recovery-notice.json"),
                        &serde_json::json!({"at":now(),"message":"Restored configuration from a verified backup; original preserved in recovery; verify configured paths"}),
                    )?;
                    recovered
                }
            }
        } else {
            let c = Config::default();
            atomic_json(&config_path, &c)?;
            c
        };
        let path = root.join("registry.sqlite3");
        let recovery = Self::recover_if_corrupt(root, &path)?;
        let db = Connection::open(&path)?;
        db.busy_timeout(std::time::Duration::from_secs(5))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, class TEXT NOT NULL CHECK(class IN ('env','tool')), identity TEXT NOT NULL UNIQUE, parent_id TEXT, status TEXT NOT NULL, body TEXT NOT NULL);
            CREATE VIEW IF NOT EXISTS environments AS SELECT * FROM resources WHERE class='env';
            CREATE VIEW IF NOT EXISTS tools AS SELECT * FROM resources WHERE class='tool';
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scan_queue(scan_id TEXT NOT NULL, path TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scan_id,path));
            CREATE TABLE IF NOT EXISTS ignored(identity TEXT PRIMARY KEY, body TEXT NOT NULL, physical_identity TEXT);")?;
        let has_physical_identity = {
            let mut stmt = db.prepare("PRAGMA table_info(ignored)")?;
            let columns = stmt.query_map([], |row| row.get::<_, String>(1))?;
            columns
                .collect::<rusqlite::Result<Vec<_>>>()?
                .iter()
                .any(|name| name == "physical_identity")
        };
        if !has_physical_identity {
            db.execute("ALTER TABLE ignored ADD COLUMN physical_identity TEXT", [])?;
        }
        let version: i64 = db.query_row("SELECT version FROM schema_version", [], |r| r.get(0))?;
        if version != 1 {
            bail!("Unsupported registry schema {version}; refusing to change it");
        }
        let mut s = Self {
            db,
            root: root.into(),
            config,
            recovery,
            active_verifications: Default::default(),
        };
        let version_path = s.root.join("storage-version.json");
        let old_version: Option<serde_json::Value> = read_json(&version_path).ok();
        if old_version.as_ref().and_then(|v| v["version"].as_str())
            != Some(env!("CARGO_PKG_VERSION"))
        {
            s.backup()?;
            atomic_json(
                &version_path,
                &serde_json::json!({"version":env!("CARGO_PKG_VERSION")}),
            )?;
        }
        for entry in fs::read_dir(s.root.join("pending"))? {
            let path = entry?.path();
            if path.extension().is_some_and(|e| e == "json") {
                let op: Operation = match read_json(&path) {
                    Ok(op) => op,
                    Err(error) => {
                        let dest = s.root.join("recovery").join(format!(
                            "corrupt-journal-{}-{}",
                            id(),
                            path.file_name().unwrap().to_string_lossy()
                        ));
                        fs::rename(&path, &dest)?;
                        atomic_json(
                            &s.root.join("recovery-journal-notice.json"),
                            &serde_json::json!({"path":dest,"error":error.to_string(),"automatic_recovery_blocked":true}),
                        )?;
                        s.recovery = Some(
                            "Unreadable operation journal quarantined; automatic recovery disabled"
                                .into(),
                        );
                        continue;
                    }
                };
                let old = s.get::<Operation>("operations", &op.id).ok();
                if old.is_none_or(|o| o.updated_at <= op.updated_at) {
                    s.save("operations", &op.id, &op)?;
                }
            }
        }
        if s.root.join("recovery-journal-notice.json").exists()
            || s.root.join("database-recovery-notice.json").exists()
        {
            s.recovery =
                Some("Recovery notice present; automatic maintenance recovery disabled".into());
        }
        for mut job in s.scans()? {
            if job.coverage.is_null() {
                job.coverage = serde_json::json!({"state":"unknown","queue_complete":false,"uncovered":job.roots,"reason":"Legacy scan has no coverage evidence"});
                s.save("scans", &job.id, &job)?;
            }
            if job.status == "running" {
                job.status = "interrupted".into();
                s.save("scans", &job.id, &job)?;
            }
        }
        for mut op in s.operations()? {
            if op.status == "executing" {
                op.status = "interrupted".into();
                op.reliability.recovery.reason =
                    Some("Previous worker ended; verifying actual result without replay".into());
                s.save("operations", &op.id, &op)?;
            }
        }
        if s.recovery.is_some() {
            for mut r in s.resources()? {
                r.status = "unverified".into();
                s.update_resource(&r)?;
            }
        }
        Ok(s)
    }
    fn integrity(path: &Path) -> Result<bool> {
        let db = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        Ok(db.query_row("PRAGMA quick_check", [], |r| r.get::<_, String>(0))? == "ok")
    }
    fn recover_if_corrupt(root: &Path, path: &Path) -> Result<Option<String>> {
        if !path.exists() {
            return Ok(None);
        }
        match Self::integrity(path) {
            Ok(true) => return Ok(None),
            Err(rusqlite_error) => {
                let corrupt = rusqlite_error.downcast_ref::<rusqlite::Error>().is_some_and(|e| matches!(e, rusqlite::Error::SqliteFailure(code, _) if matches!(code.code, rusqlite::ErrorCode::DatabaseCorrupt | rusqlite::ErrorCode::NotADatabase)));
                if !corrupt {
                    return Err(rusqlite_error.context(
                        "Registry inaccessible; not treating access errors as corruption",
                    ));
                }
            }
            Ok(false) => {}
        }
        let mut backups: Vec<_> = fs::read_dir(root.join("backups"))?
            .filter_map(|x| x.ok())
            .map(|x| x.path())
            .filter(|p| p.extension().is_some_and(|x| x == "sqlite3"))
            .collect();
        backups.sort();
        backups.reverse();
        let backup = backups
            .into_iter()
            .find(|p| Self::integrity(p).unwrap_or(false))
            .context("Registry corrupt and no verified backup; preserving original")?;
        atomic_json(
            &root.join("database-recovery-notice.json"),
            &serde_json::json!({"at":now(),"automatic_recovery_blocked":true}),
        )?;
        let quarantine = root.join("recovery").join(id());
        fs::create_dir(&quarantine)?;
        for suffix in ["", "-wal", "-shm"] {
            let original = PathBuf::from(format!("{}{suffix}", path.display()));
            if original.exists() {
                fs::copy(
                    &original,
                    quarantine.join(format!("registry.sqlite3{suffix}")),
                )?;
            }
        }
        // Copy originals before removing sidecars or replacing the database.
        for suffix in ["-wal", "-shm"] {
            let side = PathBuf::from(format!("{}{suffix}", path.display()));
            if side.exists() {
                fs::remove_file(side)?;
            }
        }
        fs::copy(&backup, path)?;
        let note = format!(
            "Recovered from {}; original retained in {}; recent changes may be missing, no operations replayed",
            backup.display(),
            quarantine.display()
        );
        atomic_json(
            &root.join("recovery-notice.json"),
            &serde_json::json!({"at":now(),"message":note}),
        )?;
        Ok(Some(note))
    }
    pub fn save_config(&mut self, c: Config) -> Result<()> {
        atomic_json(&self.root.join("config.json"), &c)?;
        self.config = c;
        Ok(())
    }
    pub fn identity(r: &Resource) -> String {
        format!(
            "{}|{}|{}|{}",
            r.class,
            r.kind,
            r.scope,
            if r.scope == "host" {
                path_key(&r.path)
            } else {
                r.path.clone()
            }
        )
    }
    pub fn resources(&self) -> Result<Vec<Resource>> {
        self.all("resources")
    }
    pub fn resource(&self, id: &str) -> Result<Resource> {
        self.get("resources", id)
    }
    pub fn ignored(&self, r: &Resource) -> Result<bool> {
        let mut stmt = self.db.prepare("SELECT body FROM ignored")?;
        let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
        for row in rows {
            let old: Resource = serde_json::from_str(&row?)?;
            if Self::identity(&old) == Self::identity(r)
                || (r.scope == "host"
                    && old.scope == r.scope
                    && old.class == r.class
                    && old.kind == r.kind
                    && !matches!(r.kind.as_str(), "script" | "mcp-connector")
                    && old.entry.is_some()
                    && old.entry.as_ref().map(|s| path_key(s))
                        == r.entry.as_ref().map(|s| path_key(s)))
            {
                return Ok(true);
            }
        }
        Ok(false)
    }
    pub fn upsert(&self, mut r: Resource) -> Result<Option<Resource>> {
        if self.ignored(&r)? {
            return Ok(None);
        }
        let observed_instance = if r.scope == "host" {
            match crate::platform::canonical_unlinked(Path::new(&r.path)) {
                Ok(canonical) => crate::recovery::identity(&canonical).ok(),
                Err(_) => None,
            }
        } else {
            None
        };
        if let Some(metadata) = r.metadata.as_object_mut() {
            match &observed_instance {
                Some(identity) if r.scope == "host" => {
                    metadata.insert("instance_identity".into(), serde_json::json!(identity));
                }
                _ if r.scope == "host" => {
                    metadata.remove("instance_identity");
                }
                _ => {}
            }
        }
        let identity = Self::identity(&r);
        let resources = self.resources()?;
        let mut location_matches = resources.iter().filter(|old| {
            if Self::identity(old) == identity {
                return true;
            }
            r.scope == "host"
                && old.scope == r.scope
                && old.class == r.class
                && old.kind == r.kind
                && crate::platform::canonical_long_unlinked(Path::new(&old.path)).is_ok_and(
                    |old_path| {
                        crate::platform::canonical_long_unlinked(Path::new(&r.path))
                            .is_ok_and(|new_path| old_path == new_path)
                    },
                )
        });
        let old_by_location = location_matches.next().cloned();
        if location_matches.next().is_some() {
            anyhow::bail!("RESOURCE_INSTANCE_AMBIGUOUS: refusing to inherit associations");
        }
        let old = old_by_location;
        if let Some(old) = old {
            if r.scope == "host" {
                let old_instance = old.metadata["instance_identity"].as_str();
                match (old_instance, observed_instance.as_deref()) {
                    (Some(expected), Some(actual)) if expected == actual => {}
                    (Some(_), Some(_)) => anyhow::bail!(
                        "RESOURCE_INSTANCE_CHANGED: refusing to inherit the previous registration"
                    ),
                    (None, _) => anyhow::bail!(
                        "RESOURCE_INSTANCE_IDENTITY_MISSING: refusing to inherit previous associations"
                    ),
                    (_, None) => anyhow::bail!(
                        "RESOURCE_INSTANCE_UNAVAILABLE: refusing to update the previous registration"
                    ),
                }
            }
            r.id = old.id;
            for p in old.projects {
                if !r.projects.contains(&p) {
                    r.projects.push(p);
                }
            }
            if r.parent_id.is_none() {
                r.parent_id = old.parent_id;
            }
            if r.version.is_none() {
                r.version = old.version;
            }
            if r.entry.is_none() {
                r.entry = old.entry;
            }

            if let (Some(a), Some(b)) = (r.metadata.as_object_mut(), old.metadata.as_object()) {
                for (k, v) in b {
                    a.entry(k.clone()).or_insert(v.clone());
                }
            }
        }
        if r.class == "tool" && r.scope == "host" && r.parent_id.is_none() {
            let path = Path::new(&r.path);
            r.parent_id = self
                .resources()?
                .into_iter()
                .filter(|p| p.class == "env" && p.scope == "host" && path.starts_with(&p.path))
                .max_by_key(|p| p.path.len())
                .map(|p| p.id);
        }
        r.updated_at = now();
        self.update_resource(&r)?;
        Ok(Some(r))
    }
    pub fn update_resource(&self, r: &Resource) -> Result<()> {
        self.db.execute("INSERT INTO resources(id,class,identity,parent_id,status,body) VALUES(?1,?2,?3,?4,?5,?6) ON CONFLICT(id) DO UPDATE SET identity=excluded.identity,parent_id=excluded.parent_id,status=excluded.status,body=excluded.body",
            params![r.id,r.class,Self::identity(r),r.parent_id,r.status,serde_json::to_string(r)?])?;
        Ok(())
    }
    pub fn ignore(&self, id: &str) -> Result<()> {
        let r = self.resource(id)?;
        let physical_identity = if r.scope == "host" {
            crate::recovery::identity(Path::new(&r.path)).ok()
        } else {
            None
        };
        let tx = self.db.unchecked_transaction()?;
        tx.execute(
            "INSERT OR REPLACE INTO ignored(identity,body,physical_identity) VALUES(?1,?2,?3)",
            params![
                Self::identity(&r),
                serde_json::to_string(&r)?,
                physical_identity
            ],
        )?;
        tx.execute("DELETE FROM resources WHERE id=?1", [id])?;
        tx.commit()?;
        Ok(())
    }
    pub fn ignored_resource(&self, id: &str) -> Result<(Resource, Option<String>)> {
        let mut stmt = self
            .db
            .prepare("SELECT body,physical_identity FROM ignored")?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
        })?;
        let mut found = None;
        for row in rows {
            let (body, physical_identity) = row?;
            let r: Resource = serde_json::from_str(&body)?;
            if r.id == id {
                found = Some((r, physical_identity));
                break;
            }
        }
        found.context("Ignored resource not found")
    }
    pub fn restore_verified(&self, id: &str, mut verified: Resource) -> Result<Resource> {
        let (old, physical_identity) = self.ignored_resource(id)?;
        anyhow::ensure!(
            old.scope == "host",
            "Restore verification for non-host resources is unsupported"
        );
        let expected = physical_identity.context(
            "LEGACY_IGNORED_RESOURCE_HAS_NO_INSTANCE_IDENTITY: resource remains ignored",
        )?;
        anyhow::ensure!(
            crate::recovery::identity(Path::new(&old.path))? == expected,
            "RESOURCE_INSTANCE_CHANGED: ignored resource remains ignored"
        );
        anyhow::ensure!(
            verified.scope == old.scope
                && verified.class == old.class
                && verified.kind == old.kind
                && verified.status == "available"
                && (old.version.is_none() || verified.version == old.version)
                && old.entry.as_ref().map(|path| path_key(path))
                    == verified.entry.as_ref().map(|path| path_key(path))
                && crate::platform::canonical(Path::new(&verified.path))?
                    == crate::platform::canonical(Path::new(&old.path))?,
            "RESOURCE_IDENTITY_OR_VERSION_MISMATCH: ignored resource remains ignored"
        );
        verified.id = old.id.clone();
        verified.projects = old.projects.clone();
        verified.parent_id = old.parent_id.clone();
        if let (Some(new), Some(previous)) =
            (verified.metadata.as_object_mut(), old.metadata.as_object())
        {
            for (key, value) in previous {
                new.entry(key.clone()).or_insert(value.clone());
            }
        }
        verified.status = "available".into();
        verified.updated_at = now();
        let tx = self.db.unchecked_transaction()?;
        let changed = tx.execute(
            "DELETE FROM ignored WHERE identity=?1",
            [Self::identity(&old)],
        )?;
        anyhow::ensure!(changed == 1, "Ignored record changed during restore");
        tx.execute("INSERT INTO resources(id,class,identity,parent_id,status,body) VALUES(?1,?2,?3,?4,?5,?6)",
            params![verified.id, verified.class, Self::identity(&verified), verified.parent_id, verified.status, serde_json::to_string(&verified)?])?;
        anyhow::ensure!(
            crate::recovery::identity(Path::new(&old.path))? == expected,
            "RESOURCE_INSTANCE_CHANGED_DURING_RESTORE: transaction rolled back"
        );
        tx.commit()?;
        Ok(verified)
    }
    pub fn save<T: Serialize>(&self, table: &str, id: &str, body: &T) -> Result<()> {
        assert!(matches!(table, "operations" | "scans"));
        self.db.execute(&format!("INSERT INTO {table}(id,body) VALUES(?1,?2) ON CONFLICT(id) DO UPDATE SET body=excluded.body"),params![id,serde_json::to_string(body)?])?;
        Ok(())
    }
    pub fn get<T: DeserializeOwned>(&self, table: &str, id: &str) -> Result<T> {
        assert!(matches!(table, "resources" | "operations" | "scans"));
        let body: String = self
            .db
            .query_row(
                &format!("SELECT body FROM {table} WHERE id=?1"),
                [id],
                |r| r.get(0),
            )
            .with_context(|| format!("{table} entry not found: {id}"))?;
        Ok(serde_json::from_str(&body)?)
    }
    pub fn all<T: DeserializeOwned>(&self, table: &str) -> Result<Vec<T>> {
        assert!(matches!(table, "resources" | "operations" | "scans"));
        let mut stmt = self.db.prepare(&format!("SELECT body FROM {table}"))?;
        stmt.query_map([], |r| r.get::<_, String>(0))?
            .map(|row| Ok(serde_json::from_str(&row?)?))
            .collect()
    }
    pub fn operations(&self) -> Result<Vec<Operation>> {
        self.all("operations")
    }
    pub fn scans(&self) -> Result<Vec<Scan>> {
        self.all("scans")
    }
    pub fn backup(&self) -> Result<()> {
        let path = self.root.join("backups").join(format!(
            "{}-{}.sqlite3",
            chrono::Utc::now().format("%Y%m%dT%H%M%S%3f"),
            id()
        ));
        self.db.backup("main", &path, None)?;
        if !Self::integrity(&path)? {
            bail!("Backup did not pass integrity check");
        }
        // Config lives outside SQLite; snapshot alongside each verified database.
        atomic_json(&path.with_extension("config.json"), &self.config)?;
        let mut entries: Vec<_> = fs::read_dir(self.root.join("backups"))?
            .filter_map(|x| x.ok())
            .map(|x| x.path())
            .filter(|p| p.extension().is_some_and(|x| x == "sqlite3"))
            .collect();
        entries.sort();
        let remove = entries
            .len()
            .saturating_sub(self.config.backup_count.max(1));
        for p in entries.into_iter().take(remove) {
            fs::remove_file(&p)?;
            let c = p.with_extension("config.json");
            if c.exists() {
                fs::remove_file(c)?;
            }
        }
        Ok(())
    }
    pub fn leases(&self) -> Result<Vec<Lease>> {
        let mut out = vec![];
        for p in fs::read_dir(self.root.join("usage"))? {
            let p = p?.path();
            if p.extension().is_some_and(|x| x == "json") {
                out.push(
                    read_json(&p).context(
                        "Usage journal unreadable; refusing to assume resources are idle",
                    )?,
                );
            }
        }
        Ok(out)
    }
    pub fn related_ids(&self, r: &Resource) -> Result<Vec<String>> {
        let resources = self.resources()?;
        let mut related = vec![r.clone()];
        let mut cursor = 0;
        while cursor < related.len() {
            let current = related[cursor].clone();
            cursor += 1;
            for candidate in &resources {
                let explicitly_related = current.parent_id.as_deref() == Some(&candidate.id)
                    || candidate.parent_id.as_deref() == Some(&current.id);
                let path_related = current.scope == "host"
                    && candidate.scope == "host"
                    && paths_overlap(&current.path, &candidate.path);
                if (explicitly_related || path_related)
                    && !related.iter().any(|known| known.id == candidate.id)
                {
                    related.push(candidate.clone());
                }
            }
        }
        let mut ids = related
            .iter()
            .map(|resource| resource.id.clone())
            .collect::<Vec<_>>();
        for resource in related.iter().filter(|resource| resource.scope == "host") {
            let token = format!("path:{}", path_key(&resource.path));
            if !ids.contains(&token) {
                ids.push(token);
            }
        }
        Ok(ids)
    }
    // Call under the same Store mutex used by usage.release so no request can
    // observe an unowned verification lease between acquisition and insertion.
    pub(crate) fn acquire_verification(
        &self,
        ids: Vec<String>,
    ) -> Result<(Lease, VerificationRequest)> {
        let mut active = self
            .active_verifications
            .lock()
            .map_err(|_| anyhow::anyhow!("Verification ownership lock poisoned"))?;
        // Verification requests have no operation recovery entry. Persist their
        // intent directly as verifying (no reserved intermediate write), so a
        // crash before the first probe can still be explicitly reviewed. Empty
        // process fields never authorize automatic release.
        let lease = self.acquire_with_state(LeaseRequest {
            ids,
            mode: "write".into(),
            project: None,
            operation_id: None,
            pid: None,
            process_stamp: None,
            state: "verifying".into(),
        })?;
        active.insert(lease.id.clone());
        let owner = VerificationRequest {
            lease_id: lease.id.clone(),
            active: self.active_verifications.clone(),
        };
        Ok((lease, owner))
    }
    pub(crate) fn verification_active(&self, id: &str) -> Result<bool> {
        Ok(self
            .active_verifications
            .lock()
            .map_err(|_| anyhow::anyhow!("Verification ownership lock poisoned"))?
            .contains(id))
    }
    pub fn acquire(&self, ids: Vec<String>, mode: &str, project: Option<String>) -> Result<Lease> {
        self.acquire_for_operation(ids, mode, project, None)
    }
    pub fn acquire_for_operation(
        &self,
        ids: Vec<String>,
        mode: &str,
        project: Option<String>,
        operation_id: Option<String>,
    ) -> Result<Lease> {
        self.acquire_with_state(LeaseRequest {
            ids,
            mode: mode.into(),
            project,
            operation_id,
            pid: None,
            process_stamp: None,
            state: "reserved".into(),
        })
    }
    pub fn acquire_external(
        &self,
        ids: Vec<String>,
        project: Option<String>,
        pid: u32,
        process_stamp: u64,
    ) -> Result<Lease> {
        self.acquire_with_state(LeaseRequest {
            ids,
            mode: "read".into(),
            project,
            operation_id: None,
            pid: Some(pid),
            process_stamp: Some(process_stamp),
            state: "external".into(),
        })
    }
    fn acquire_with_state(&self, request: LeaseRequest) -> Result<Lease> {
        for lease in self.leases()? {
            if lease.state != "released"
                && protection_conflicts(&lease.resource_ids, &request.ids)
                && (request.mode == "write" || lease.mode == "write")
            {
                bail!("Resource in use: lease {} ({})", lease.id, lease.state);
            }
        }
        let lease = Lease {
            id: id(),
            operation_id: request.operation_id,
            resource_ids: request.ids,
            mode: request.mode,
            pid: request.pid,
            process_stamp: request.process_stamp,
            process_job: None,
            project: request.project,
            state: request.state,
            created_at: now(),
        };
        self.save_lease(&lease)?;
        Ok(lease)
    }
    pub fn reclaim_unstarted_operation_leases(&self) -> Result<usize> {
        let planned: Vec<_> = self
            .operations()?
            .into_iter()
            .filter(|op| op.status == "planned")
            .map(|op| op.id)
            .collect();
        let mut reclaimed = 0;
        for mut lease in self.leases()? {
            if lease.state == "reserved"
                && lease.pid.is_none()
                && lease.process_stamp.is_none()
                && lease.process_job.is_none()
                && lease
                    .operation_id
                    .as_ref()
                    .is_some_and(|id| planned.contains(id))
            {
                self.finish_lease(&mut lease)?;
                reclaimed += 1;
            }
        }
        Ok(reclaimed)
    }
    pub fn save_lease(&self, l: &Lease) -> Result<()> {
        atomic_json(&self.root.join("usage").join(format!("{}.json", l.id)), l)
    }
    pub fn finish_lease(&self, l: &mut Lease) -> Result<()> {
        l.state = "released".into();
        self.save_lease(l)
    }
}

impl Store {
    pub fn prune_logs(&self) -> Result<()> {
        let active: Vec<_> = self
            .operations()?
            .into_iter()
            .filter(|o| {
                matches!(
                    o.status.as_str(),
                    "executing" | "interrupted" | "registration_failed"
                ) || o.reliability.recovery.status == "running"
                    || o.reliability.cleanup.status == "running"
                    || o.reliability.finalization.status == "running"
            })
            .map(|o| o.id)
            .collect();
        let mut files = Vec::new();
        let mut total = 0u64;
        for entry in fs::read_dir(self.root.join("logs"))? {
            let p = entry?.path();
            if p.extension().is_none_or(|x| x != "jsonl" && x != "log") {
                continue;
            }
            let meta = fs::metadata(&p)?;
            total = total.saturating_add(meta.len());
            files.push((meta.modified()?, meta.len(), p));
        }
        files.sort_by_key(|f| f.0);
        for (modified, len, path) in files {
            if path.file_stem().is_some_and(|n| {
                active.iter().any(|a| {
                    a == &n.to_string_lossy() || n.to_string_lossy().starts_with(&format!("{a}-"))
                })
            }) {
                continue;
            }
            let expired = modified
                .elapsed()
                .is_ok_and(|d| d.as_secs() > self.config.log_days.saturating_mul(86400));
            if expired || total > self.config.log_limit_bytes {
                fs::remove_file(path)?;
                total = total.saturating_sub(len);
            }
        }
        Ok(())
    }
}

impl Store {
    /// Called only after external deletion was verified, inside the registration transaction.
    pub fn register_deletion(&self, root: &Resource) -> Result<()> {
        let resources = self.resources()?;
        let mut removed = vec![root.id.clone()];
        let mut cursor = 0;
        while cursor < removed.len() {
            let parent = removed[cursor].clone();
            cursor += 1;
            for child in resources
                .iter()
                .filter(|r| r.parent_id.as_deref() == Some(&parent))
            {
                let contained = if root.scope == "host" {
                    child.scope == "host"
                        && path_key(&child.path).starts_with(&(path_key(&root.path) + "\\"))
                } else {
                    child.scope != "host"
                };
                let child_exists = if child.scope == "host" {
                    Path::new(&child.path).try_exists().with_context(|| {
                        format!(
                            "Could not verify whether registered child still exists: {}",
                            child.path
                        )
                    })?
                } else {
                    false
                };
                if contained && !child_exists {
                    if !removed.contains(&child.id) {
                        removed.push(child.id.clone());
                    }
                } else {
                    let mut kept = child.clone();
                    kept.parent_id = None;
                    kept.status = "unverified".into();
                    kept.metadata["removed_dependency"] = serde_json::json!(parent);
                    self.update_resource(&kept)?;
                }
            }
        }
        for id in removed {
            self.db.execute("DELETE FROM resources WHERE id=?1", [id])?;
        }
        Ok(())
    }
}
