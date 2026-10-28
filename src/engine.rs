use crate::{adapters, ipc::Shared, model::*, operations, platform, scan};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v[key]
        .as_str()
        .with_context(|| format!("Missing string: {key}"))
}
fn optional(v: &Value, key: &str) -> Option<String> {
    v[key].as_str().map(str::to_owned)
}
fn strings(v: &Value, key: &str) -> Vec<String> {
    v[key]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}
/// Record each probe before it can execute, including its entire Windows job.
fn protected_verify(shared: &Shared, resource: Resource, lease: &mut Lease) -> Result<Resource> {
    adapters::verify_with(resource, |pid| {
        lease.pid = Some(pid);
        lease.process_stamp = platform::process_stamp(pid)?;
        lease.process_job = Some(platform::process_job_name(pid)?);
        lease.state = "verifying".into();
        shared.lock().unwrap().save_lease(lease)
    })
}

/// Cover both probe errors and post-probe commit/backup/finalization errors.
/// A database commit may already have succeeded; preserve its actual result and
/// the original error rather than claiming that registration was rolled back.
fn finish_verification(shared: &Shared, lease: &mut Lease, result: Result<Value>) -> Result<Value> {
    let s = shared.lock().unwrap();
    let result = result.and_then(|value| {
        s.finish_lease(lease)?;
        Ok(value)
    });
    match result {
        Ok(value) => Ok(value),
        Err(error) => {
            lease.state = "needs_review".into();
            match s.save_lease(lease) {
                Ok(()) => Err(error),
                Err(save_error) => Err(error.context(format!(
                    "Verification failed; could not persist needs_review: {save_error:#}. Existing durable protection must be reviewed"
                ))),
            }
        }
    }
}

pub fn dispatch(shared: Shared, request: Request) -> Result<Value> {
    let a = &request.args;
    match request.command.as_str() {
        "platform.inspect" => crate::platform_resources::inspect_internal(
            shared.clone(),
            string(a, "id")?,
            string(a, "path")?,
        ),
        "platform.discover" => {
            let rows = crate::platform_resources::probe(string(a, "kind")?)?;
            let s = shared.lock().unwrap();
            let resources = crate::platform_resources::register_rows(&s, rows)?;
            s.backup()?;
            Ok(
                json!({"resources":resources,"scope":"host platform only; internal resources not recursively scanned"}),
            )
        }
        "components.service-plan" => crate::components::service_plan(&shared.lock().unwrap()),
        "components.status" => Ok(crate::components::status(&shared.lock().unwrap())),
        "components.start" | "components.stop" => {
            let (root, config) = {
                let s = shared.lock().unwrap();
                (s.root.clone(), s.config.clone())
            };
            let view = crate::store::Store {
                db: rusqlite::Connection::open_in_memory()?,
                root,
                config,
                recovery: None,
                active_verifications: Default::default(),
            };
            if request.command == "components.stop" {
                crate::components::stop(&view)
            } else {
                crate::components::start(
                    &view,
                    &strings(a, "roots")
                        .into_iter()
                        .map(PathBuf::from)
                        .collect::<Vec<_>>(),
                )
            }
        }
        "status" => {
            let s = shared.lock().unwrap();
            Ok(
                json!({"version":env!("CARGO_PKG_VERSION"),"data_root":s.root,"recovery":s.recovery,"resources":s.resources()?.len(),"worker_pid":std::process::id(),"disks":platform::disk_inventory(),"recovery_operations":crate::recovery::summary(&s)?}),
            )
        }
        "worker.stop" => {
            let s = shared.lock().unwrap();
            anyhow::ensure!(
                !s.scans()?.iter().any(|x| x.status == "running")
                    && !s.operations()?.iter().any(|x| x.status == "executing"
                        || x.reliability.recovery.status == "running"
                        || x.reliability.cleanup.status == "running"),
                "Worker has active background tasks"
            );
            anyhow::ensure!(
                !s.leases()?.iter().any(|l| l.state != "released"),
                "Usage protection remains active; resolve usage before stopping"
            );
            crate::ipc::STOP.store(true, std::sync::atomic::Ordering::SeqCst);
            Ok(json!({"stopping":true}))
        }
        "capabilities" => Ok(adapters::capabilities()),
        "config.show" => Ok(serde_json::to_value(&shared.lock().unwrap().config)?),
        "config.set" => {
            let mut s = shared.lock().unwrap();
            let mut c = s.config.clone();
            for (key, dst) in [
                ("env_root", &mut c.env_root),
                ("tools_root", &mut c.tools_root),
            ] {
                if let Some(value) = a[key].as_str() {
                    let p = PathBuf::from(value);
                    anyhow::ensure!(
                        p.is_absolute() && p.parent().is_some(),
                        "Resource roots must be absolute non-volume-root paths"
                    );
                    if !p.exists() {
                        anyhow::ensure!(
                            a["initialize"].as_bool() == Some(true),
                            "ROOT_INITIALIZATION_CONFIRMATION_REQUIRED: {}",
                            p.display()
                        );
                        let parent =
                            platform::canonical(p.parent().context("Root requires parent")?)?;
                        anyhow::ensure!(
                            !parent.starts_with(platform::canonical(&s.root)?),
                            "Resource root cannot be in AIRoot information directory"
                        );
                        fs::create_dir(&p)?;
                    }
                    let resolved = platform::canonical(&p)?;
                    let data = platform::canonical(&s.root)?;
                    anyhow::ensure!(
                        !resolved.starts_with(&data) && !data.starts_with(&resolved),
                        "Resource roots cannot overlap AIRoot information directory"
                    );
                    anyhow::ensure!(
                        !platform::is_link(&p)?,
                        "Linked resource roots are not supported"
                    );
                    *dst = Some(resolved);
                }
            }
            if let (Some(e), Some(t)) = (&c.env_root, &c.tools_root) {
                anyhow::ensure!(
                    !e.starts_with(t) && !t.starts_with(e),
                    "Environment/tool roots must not overlap"
                );
            }
            s.save_config(c.clone())?;
            s.backup()?;
            Ok(serde_json::to_value(c)?)
        }
        "env.list" | "tool.list" => {
            let s = shared.lock().unwrap();
            let class = if request.command.starts_with("env") {
                "env"
            } else {
                "tool"
            };
            let resources: Vec<_> = s
                .resources()?
                .into_iter()
                .filter(|r| {
                    r.class == class
                        && a["project"]
                            .as_str()
                            .is_none_or(|p| r.projects.iter().any(|s| s == p))
                })
                .collect();
            Ok(json!(resources))
        }
        "env.show" | "tool.show" => {
            let s = shared.lock().unwrap();
            let r = s.resource(string(a, "id")?)?;
            anyhow::ensure!(
                request.command.starts_with(&r.class),
                "Resource belongs to {} registry",
                r.class
            );
            Ok(json!(r))
        }
        "project.show" => {
            let s = shared.lock().unwrap();
            let project = string(a, "project")?;
            Ok(
                json!({"project":project,"resources":s.resources()?.into_iter().filter(|r|r.projects.iter().any(|p|p==project)).collect::<Vec<_>>()}),
            )
        }
        "registry.add" => {
            let mut rs = if !a["resource"].is_null() {
                let r: Resource = serde_json::from_value(a["resource"].clone())?;
                anyhow::ensure!(matches!(r.class.as_str(), "env" | "tool"), "Invalid class");
                anyhow::ensure!(
                    matches!(
                        r.kind.as_str(),
                        "venv"
                            | "conda"
                            | "node"
                            | "java"
                            | "python"
                            | "cli"
                            | "script"
                            | "mcp-connector"
                    ),
                    "Unsupported registration kind"
                );
                anyhow::ensure!(
                    r.scope == "host",
                    "Internal resources require a platform-specific adapter"
                );
                anyhow::ensure!(Path::new(&r.path).exists(), "Resource path is unavailable");
                let mut r = r;
                r.path = platform::canonical(Path::new(&r.path))?
                    .to_string_lossy()
                    .into();
                r.status = "unverified".into();
                r.id = id();
                vec![r]
            } else {
                adapters::inspect(Path::new(string(a, "path")?))?
            };
            anyhow::ensure!(
                !rs.is_empty(),
                "No supported resource identity found; explicit reusable scripts/CLI/connectors need a resource JSON"
            );
            let s = shared.lock().unwrap();
            let mut result = vec![];
            for mut r in rs.drain(..) {
                if let Some(project) = optional(a, "project") {
                    r.projects.push(project);
                }
                if let Some(parent) = optional(a, "parent_id") {
                    let parent = s.resource(&parent)?;
                    anyhow::ensure!(
                        parent.class == "env" || r.kind == "mcp-connector",
                        "Parent must be an environment or the installed MCP server"
                    );
                    r.parent_id = Some(parent.id);
                }
                if r.kind == "mcp-connector" {
                    anyhow::ensure!(
                        r.class == "tool" && r.metadata["transport"] == "stdio",
                        "Only installed stdio connectors are eligible"
                    );
                    let parent = s.resource(r.parent_id.as_deref().context(
                        "MCP connector requires parent_id of the installed server or interpreter",
                    )?)?;
                    r.entry = parent.entry;
                    anyhow::ensure!(
                        r.metadata["args"]
                            .as_array()
                            .is_some_and(|a| a.iter().all(|v| v.is_string())),
                        "MCP args must be a string array"
                    );
                }
                if r.kind == "script" {
                    anyhow::ensure!(
                        r.class == "tool" && r.metadata["reusable"] == true,
                        "Only explicitly reusable scripts are tools; set metadata.reusable=true"
                    );
                    let parent = s.resource(r.parent_id.as_deref().context(
                        "Reusable scripts require parent_id of their interpreter environment",
                    )?)?;
                    anyhow::ensure!(
                        matches!(parent.kind.as_str(), "python" | "venv" | "conda" | "node"),
                        "Unsupported script interpreter"
                    );
                    let extension = Path::new(&r.path)
                        .extension()
                        .and_then(|e| e.to_str())
                        .unwrap_or("");
                    anyhow::ensure!(
                        if parent.kind == "node" {
                            matches!(extension, "js" | "mjs" | "cjs")
                        } else {
                            extension == "py"
                        },
                        "Script extension does not match interpreter"
                    );
                    r.entry = parent.entry.clone();
                    r.metadata["interpreter_id"] = json!(parent.id);
                }
                result.push(s.upsert(r)?);
            }
            s.backup()?;
            Ok(json!(result))
        }
        "registry.verify" => {
            let (r, mut lease, _request_owner) = {
                let s = shared.lock().unwrap();
                let r = s.resource(string(a, "id")?)?;
                let (lease, owner) = s.acquire_verification(s.related_ids(&r)?)?;
                (r, lease, owner)
            };
            let result = (|| {
                if r.scope == "host" && std::path::Path::new(&r.path).exists() {
                    let expected = r.metadata["instance_identity"].as_str().context(
                        "RESOURCE_INSTANCE_IDENTITY_MISSING: cannot verify legacy registration",
                    )?;
                    let actual = crate::recovery::identity(std::path::Path::new(&r.path))?;
                    anyhow::ensure!(
                        actual == expected,
                        "RESOURCE_INSTANCE_CHANGED: refusing to verify a replacement instance"
                    );
                }
                let r = protected_verify(&shared, r, &mut lease)?;
                shared.lock().unwrap().update_resource(&r)?;
                Ok(json!(r))
            })();
            finish_verification(&shared, &mut lease, result)
        }
        "registry.ignore" => {
            let s = shared.lock().unwrap();
            let r = s.resource(string(a, "id")?)?;
            let mut lease = s.acquire(s.related_ids(&r)?, "write", None)?;
            s.ignore(&r.id)?;
            s.finish_lease(&mut lease)?;
            s.backup()?;
            Ok(json!({"ignored":r.id,"files_preserved":true}))
        }
        "registry.restore" => {
            let id = string(a, "id")?;
            let (ignored, mut lease, _request_owner) = {
                let s = shared.lock().unwrap();
                let (ignored, physical_identity) = s.ignored_resource(id)?;
                anyhow::ensure!(
                    ignored.scope == "host",
                    "Restore verification for non-host resources is unsupported"
                );
                let expected = physical_identity.context(
                    "LEGACY_IGNORED_RESOURCE_HAS_NO_INSTANCE_IDENTITY: resource remains ignored",
                )?;
                anyhow::ensure!(
                    crate::recovery::identity(std::path::Path::new(&ignored.path))? == expected,
                    "RESOURCE_INSTANCE_CHANGED: ignored resource remains ignored"
                );
                let (lease, owner) = s.acquire_verification(s.related_ids(&ignored)?)?;
                (ignored, lease, owner)
            };
            // External processes must not hold the registry mutex. Queries stay
            // available while the durable write lease excludes maintenance.
            let result = (|| {
                let verified = protected_verify(&shared, ignored, &mut lease)?;
                anyhow::ensure!(
                    verified.status == "available",
                    "RESOURCE_NOT_AVAILABLE: ignored resource remains ignored"
                );
                let s = shared.lock().unwrap();
                let r = s.restore_verified(id, verified)?;
                s.backup()?;
                Ok(json!(r))
            })();
            finish_verification(&shared, &mut lease, result)
        }
        "registry.backup" => {
            shared.lock().unwrap().backup()?;
            Ok(json!({"backed_up":true}))
        }
        "scan.start" => Ok(json!(scan::start(
            shared,
            strings(a, "roots").into_iter().map(PathBuf::from).collect()
        )?)),
        "scan.status" => {
            let s = shared.lock().unwrap();
            if let Some(id) = a["id"].as_str() {
                Ok(json!(s.get::<Scan>("scans", id)?))
            } else {
                Ok(json!(s.scans()?))
            }
        }
        "scan.pause" => {
            let s = shared.lock().unwrap();
            let mut scan: Scan = s.get("scans", string(a, "id")?)?;
            anyhow::ensure!(scan.status == "running", "Scan is not running");
            scan.status = "paused".into();
            scan.updated_at = now();
            s.save("scans", &scan.id, &scan)?;
            Ok(json!(scan))
        }
        "scan.resume" => Ok(json!(scan::resume(shared, string(a, "id")?)?)),
        "resource.find" => scan::find(
            shared,
            string(a, "query")?,
            strings(a, "roots").into_iter().map(PathBuf::from).collect(),
        ),
        "operation.plan" => {
            let input: PlanInput = serde_json::from_value(a.clone())?;
            Ok(json!(operations::plan(&shared.lock().unwrap(), input)?))
        }
        "operation.apply" => Ok(json!(operations::apply(
            shared,
            string(a, "id")?,
            &strings(a, "confirmations")
        )?)),
        "operation.recover" | "operation.cleanup" => Ok(json!(crate::recovery::start(
            shared.clone(),
            string(a, "id")?,
            request.command == "operation.cleanup"
        )?)),
        "operation.show" => {
            let s = shared.lock().unwrap();
            if let Some(id) = a["id"].as_str() {
                let op = s.get::<Operation>("operations", id)?;
                Ok(json!(op))
            } else {
                Ok(json!(s.operations()?))
            }
        }
        "resource.run" => operations::run(
            shared,
            string(a, "id")?,
            strings(a, "args"),
            optional(a, "project"),
        ),
        "usage.list" => Ok(json!(shared.lock().unwrap().leases()?)),
        "usage.acquire" => {
            let s = shared.lock().unwrap();
            let r = s.resource(string(a, "id")?)?;
            let pid = u32::try_from(a["pid"].as_u64().context("PID required")?)
                .context("PID is outside the supported range")?;
            let stamp = platform::process_stamp(pid)?.context("Process is not running")?;
            let l = s.acquire_external(s.related_ids(&r)?, optional(a, "project"), pid, stamp)?;
            if let Some(p) = &l.project {
                let mut r = r;
                if !r.projects.contains(p) {
                    r.projects.push(p.clone());
                    s.update_resource(&r)?;
                }
            }
            Ok(json!(l))
        }
        "usage.release" => {
            let s = shared.lock().unwrap();
            let mut l = s
                .leases()?
                .into_iter()
                .find(|l| l.id == a["id"].as_str().unwrap_or(""))
                .context("Lease not found")?;
            if l.state != "released" {
                anyhow::ensure!(
                    !s.verification_active(&l.id)?,
                    "VERIFICATION_IN_PROGRESS: wait for registry verify/restore to finish; an exited probe is not a completed request"
                );
                // A finished child is not a finished operation. Only the
                // operation lifecycle can cross its durable result barrier.
                // Older leases may carry the association only in the journal.
                anyhow::ensure!(
                    l.operation_id.is_none()
                        && !s.operations()?.iter().any(|op| {
                            op.reliability.lease_id.as_deref() == Some(l.id.as_str())
                        }),
                    "OPERATION_RECOVERY_REQUIRED: wait for active maintenance; operation recover/cleanup must reconcile and finalize its protection"
                );
            }
            anyhow::ensure!(
                l.state != "reserved",
                "Unstarted reservation cannot be released without recovery review"
            );
            if let Some(pid) = l.pid {
                anyhow::ensure!(
                    platform::process_stamp(pid)? != l.process_stamp,
                    "Recorded process is still running"
                );
            }
            anyhow::ensure!(
                a["descendants_checked"].as_bool() == Some(true),
                "DESCENDANT_VERIFICATION_REQUIRED: Agent must verify related processes have ended; timeout alone is insufficient"
            );
            s.finish_lease(&mut l)?;
            Ok(json!(l))
        }
        "log.show" => {
            let s = shared.lock().unwrap();
            let op: Operation = s.get("operations", string(a, "id")?)?;
            let path = s.root.join("logs").join(format!("{}.jsonl", op.id));
            Ok(
                json!({"operation":op.id,"events":if path.is_file(){fs::read_to_string(path)?}else{String::new()}}),
            )
        }
        _ => bail!("Unknown command: {}", request.command),
    }
}

#[cfg(all(test, windows))]
mod verification_request_tests {
    use super::*;
    use crate::store::Store;
    use std::sync::{Arc, Mutex};

    fn release(shared: &Shared, id: &str) -> Result<Value> {
        dispatch(
            shared.clone(),
            Request {
                command: "usage.release".into(),
                args: json!({"id":id,"descendants_checked":true}),
            },
        )
    }
    // The checkpoint is constructed to deterministically exercise the interval
    // after a real child has exited but before request finalization. It is not
    // an end-to-end installation or an OS-crash test.
    #[test]
    fn verification_owner_blocks_release_between_exited_probe_and_commit() {
        let tmp = tempfile::tempdir().unwrap();
        let shared = Arc::new(Mutex::new(Store::open(tmp.path()).unwrap()));
        let (mut lease, owner) = shared
            .lock()
            .unwrap()
            .acquire_verification(vec!["verification-test".into()])
            .unwrap();
        let result = platform::run(
            "cmd.exe",
            &["/d".into(), "/c".into(), "exit 0".into()],
            std::time::Duration::from_secs(5),
            |pid| {
                lease.pid = Some(pid);
                lease.process_stamp = platform::process_stamp(pid)?;
                lease.process_job = Some(platform::process_job_name(pid)?);
                lease.state = "verifying".into();
                shared.lock().unwrap().save_lease(&lease)
            },
        )
        .unwrap();
        assert_eq!(result.code, 0);
        assert!(!result.descendants_active);
        assert!(platform::job_finished(lease.process_job.as_deref().unwrap()).unwrap());
        let before = fs::read(tmp.path().join("usage").join(format!("{}.json", lease.id))).unwrap();
        let error = release(&shared, &lease.id).unwrap_err().to_string();
        assert!(error.contains("VERIFICATION_IN_PROGRESS"), "{error}");
        let s = shared.lock().unwrap();
        assert!(
            s.acquire(vec!["verification-test".into()], "write", None)
                .is_err()
        );
        assert_eq!(
            fs::read(tmp.path().join("usage").join(format!("{}.json", lease.id))).unwrap(),
            before
        );
        // Finishing the request removes only the memory barrier, not the lease.
        // Simulate an error at commit and its conservative needs_review state.
        lease.state = "needs_review".into();
        s.save_lease(&lease).unwrap();
        drop(s);
        drop(owner);
        assert_eq!(
            shared.lock().unwrap().leases().unwrap()[0].state,
            "needs_review"
        );
        release(&shared, &lease.id).unwrap();
    }

    #[test]
    fn unstarted_request_unwind_and_worker_reopen_require_explicit_usage_review() {
        let tmp = tempfile::tempdir().unwrap();
        let shared = Arc::new(Mutex::new(Store::open(tmp.path()).unwrap()));
        let (lease, owner) = shared
            .lock()
            .unwrap()
            .acquire_verification(vec!["verification-test".into()])
            .unwrap();
        // Check the *first* durable intent, with no hook and no rewrite. It must
        // never be an orphan reserved lease with no recovery command.
        assert_eq!(lease.state, "verifying");
        assert!(lease.pid.is_none());
        assert_eq!(
            shared.lock().unwrap().leases().unwrap()[0].state,
            "verifying"
        );
        let error = release(&shared, &lease.id).unwrap_err().to_string();
        assert!(error.contains("VERIFICATION_IN_PROGRESS"), "{error}");
        assert!(
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
                let _owner = owner;
                panic!("injected request unwind");
            }))
            .is_err()
        );
        assert!(
            !shared
                .lock()
                .unwrap()
                .verification_active(&lease.id)
                .unwrap()
        );
        assert_eq!(
            shared.lock().unwrap().leases().unwrap()[0].state,
            "verifying"
        );
        drop(shared);
        // Store reopening simulates loss of worker memory, not an actual crash.
        let shared = Arc::new(Mutex::new(Store::open(tmp.path()).unwrap()));
        assert!(
            shared
                .lock()
                .unwrap()
                .acquire(vec!["verification-test".into()], "write", None)
                .is_err()
        );
        let error = dispatch(
            shared.clone(),
            Request {
                command: "usage.release".into(),
                args: json!({"id":lease.id}),
            },
        )
        .unwrap_err()
        .to_string();
        assert!(
            error.contains("DESCENDANT_VERIFICATION_REQUIRED"),
            "{error}"
        );
        // This is an explicit test assertion after checking the no-probe
        // fixture, not an automatic "pid=None means idle" decision.
        release(&shared, &lease.id).unwrap();
    }
}
