use crate::{
    adapters,
    ipc::Shared,
    model::*,
    platform,
    store::{Store, atomic_json},
};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};
fn executable(path: Option<&PathBuf>, fallback: Option<&str>) -> Result<String> {
    let p=path.cloned().or_else(||fallback.and_then(platform::executable)).context("Missing installation prerequisite: provide an installed manager/interpreter with --manager; prepare a bootstrap plan (official pinned Python/Miniforge installer) or let the Agent install and then register a verified prerequisite")?;
    anyhow::ensure!(
        p.is_absolute() && p.is_file(),
        "Manager must be an existing absolute executable path"
    );
    Ok(platform::canonical(&p)?.to_string_lossy().into())
}
pub fn fingerprint(r: &Resource) -> Result<String> {
    let mut copy = r.clone();
    copy.updated_at.clear();
    let physical = if r.scope == "host" {
        let p = platform::canonical(Path::new(&r.path))?;
        let m = fs::metadata(&p)?;
        json!({"canonical":p,"created":m.created().ok().and_then(|t|t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d|d.as_nanos().to_string())})
    } else {
        json!(null)
    };
    platform::digest(&(copy, physical))
}
fn check_tree(path: &Path) -> Result<()> {
    anyhow::ensure!(
        !platform::is_link(path)?,
        "Linked paths must not be deleted by this recipe"
    );
    if path.is_dir() {
        for e in fs::read_dir(path)? {
            check_tree(&e?.path())?;
        }
    }
    Ok(())
}

fn explicit_descendants(s: &Store, root: &Resource) -> Result<Vec<Resource>> {
    let resources = s.resources()?;
    let mut descendants = vec![root.clone()];
    let mut cursor = 0;
    while cursor < descendants.len() {
        let parent_id = descendants[cursor].id.clone();
        cursor += 1;
        for child in resources
            .iter()
            .filter(|resource| resource.parent_id.as_deref() == Some(&parent_id))
        {
            if !descendants.iter().any(|known| known.id == child.id) {
                descendants.push(child.clone());
            }
        }
    }
    Ok(descendants)
}

fn validate_host_deletion_scope(s: &Store, root: &Resource) -> Result<()> {
    if root.scope != "host" {
        return Ok(());
    }
    let descendants = explicit_descendants(s, root)?;
    for candidate in s.resources()?.iter().filter(|resource| {
        resource.scope == "host"
            && resource.id != root.id
            && crate::store::paths_overlap(&resource.path, &root.path)
    }) {
        anyhow::ensure!(
            descendants.iter().any(|known| known.id == candidate.id),
            "NESTED_RESOURCE_NOT_MANAGED: {} at {} is not in the explicit parent-child deletion chain",
            candidate.id,
            candidate.path
        );
    }
    Ok(())
}

fn remove_planned_target(target: &Path, expected_identity: &str) -> Result<()> {
    crate::owned_cleanup::remove(target, expected_identity)
}
pub fn plan(s: &Store, mut input: PlanInput) -> Result<Operation> {
    if input.action == "bootstrap" {
        return crate::bootstrap::plan(s, input);
    }
    if matches!(
        input.action.as_str(),
        "import-wsl" | "pull-image" | "create-container"
    ) {
        return crate::platform_resources::provision(s, input);
    }
    if let Some(id) = &input.resource_id {
        if s.resource(id)?.scope != "host" {
            return crate::platform_resources::plan(s, input);
        }
    }
    if input.action == "install-portable" {
        return crate::portable::plan(s, input);
    }
    let mut requirements = vec![];
    let mut effects = vec![];
    let mut steps = vec![];
    let r = if input.action == "create" {
        let kind = input.kind.as_deref().context("create requires kind")?;
        anyhow::ensure!(
            matches!(kind, "venv" | "conda"),
            "This adapter does not support create; use capabilities for the verified fallback"
        );
        let name = input
            .name
            .as_deref()
            .context("A task abbreviation is required as name")?;
        anyhow::ensure!(
            !name.is_empty()
                && name
                    .chars()
                    .all(|c| c.is_alphanumeric() || matches!(c, '-' | '_')),
            "Name must contain letters, digits, hyphen or underscore"
        );
        let project = input
            .project
            .as_ref()
            .context("New environments require a corresponding project")?;
        anyhow::ensure!(!project.trim().is_empty(), "Project cannot be blank");
        let root=s.config.env_root.as_ref().context("ENV_ROOT_REQUIRED: choose a normal environment root and initialize it explicitly with config set")?;
        anyhow::ensure!(
            root.is_dir(),
            "ENV_ROOT_INITIALIZATION_REQUIRED: configured root is missing"
        );
        let mut target = input.target.clone().unwrap_or_else(|| root.join(name));
        if input.target.is_none() {
            let mut n = 2;
            while target.exists() {
                target = root.join(format!("{name}-{n}"));
                n += 1;
            }
        }
        let target = platform::checked_target(&target, root)?;
        anyhow::ensure!(
            !target.exists(),
            "Create target already exists; never overwrite an existing environment"
        );
        input.target = Some(target.clone());
        let manager = executable(
            input.manager.as_ref(),
            Some(if kind == "venv" { "python" } else { "conda" }),
        )?;
        let args = if kind == "venv" {
            vec!["-m".into(), "venv".into(), target.to_string_lossy().into()]
        } else {
            let mut a = vec![
                "create".into(),
                "--yes".into(),
                "--prefix".into(),
                target.to_string_lossy().into(),
            ];
            a.push(format!(
                "python{}",
                input
                    .version
                    .as_ref()
                    .map(|v| format!("={v}"))
                    .unwrap_or_default()
            ));
            a
        };
        if kind == "conda" {
            requirements.push("source-reviewed".into());
        }
        steps.push(Step {
            environment: Default::default(),
            program: manager.clone(),
            args,
        });
        let mut r = Resource::new(
            "env",
            kind,
            &target.file_name().unwrap().to_string_lossy(),
            &target.to_string_lossy(),
        );
        r.projects.push(project.clone());
        r.entry = Some(
            target
                .join(if kind == "venv" {
                    "Scripts/python.exe"
                } else {
                    "python.exe"
                })
                .to_string_lossy()
                .into(),
        );
        r.metadata = json!({"manager":manager,"created_by_airoot":true});
        r
    } else {
        let r = s.resource(
            input
                .resource_id
                .as_deref()
                .context("resource_id required")?,
        )?;
        anyhow::ensure!(
            r.scope == "host"
                && (matches!(r.kind.as_str(), "venv" | "conda")
                    || (input.action == "delete" && r.metadata["recipe"] == "portable-zip")),
            "Adapter operation unsupported; platform deletion cannot bypass its backup/migration requirements"
        );
        let path = Path::new(&r.path);
        anyhow::ensure!(
            path.is_dir() && !platform::is_link(path)?,
            "Resource missing or linked; verify before planning"
        );
        match input.action.as_str() {
            "install-packages" | "upgrade-packages" | "remove-packages" => {
                anyhow::ensure!(!input.packages.is_empty(), "packages required");
                for package in &input.packages {
                    anyhow::ensure!(
                        !package.starts_with('-')
                            && !package.contains(['\r', '\n'])
                            && !package.contains("://")
                            && !package.contains(['\\', '/']),
                        "Only named package specifications accepted; URLs/paths/options require a separate source-aware recipe"
                    );
                }
                requirements.push("source-reviewed".into());
                if input.action == "remove-packages" {
                    requirements.push(format!("remove-packages:{}", r.id));
                }
                requirements.push("compatibility-reviewed".into());
                requirements.push("external-use-reviewed".into());
                if r.kind == "conda" {
                    let manager =
                        executable(input.manager.as_ref(), r.metadata["manager"].as_str())?;
                    let mut args = vec![
                        if input.action == "remove-packages" {
                            "remove"
                        } else if input.action == "upgrade-packages"
                            && input
                                .packages
                                .iter()
                                .all(|p| !p.contains(['=', '<', '>', '!']))
                        {
                            "update"
                        } else {
                            "install"
                        }
                        .into(),
                        "--yes".into(),
                        "--prefix".into(),
                        r.path.clone(),
                    ];
                    args.extend(input.packages.clone());
                    steps.push(Step {
                        environment: Default::default(),
                        program: manager,
                        args,
                    });
                } else {
                    let mut args = vec![
                        "-m".into(),
                        "pip".into(),
                        if input.action == "remove-packages" {
                            "uninstall"
                        } else {
                            "install"
                        }
                        .into(),
                    ];
                    if input.action == "remove-packages" {
                        args.push("--yes".into());
                    }
                    if input.action == "upgrade-packages" {
                        args.push("--upgrade".into());
                    }
                    args.extend(input.packages.clone());
                    steps.push(Step {
                        environment: Default::default(),
                        program: r.entry.clone().context("Missing interpreter")?,
                        args,
                    });
                }
                effects.push("Dependencies may change. If compatibility or rollback is uncertain, create a separate task environment instead.".into());
            }
            "delete" => {
                anyhow::ensure!(
                    r.metadata["recipe"] != "bootstrap-installer",
                    "Vendor-installed bootstrap requires its official uninstaller; direct folder deletion refused"
                );
                let root = if r.class == "env" {
                    s.config.env_root.as_ref()
                } else {
                    s.config.tools_root.as_ref()
                }
                .context("Configure the resource root before deleting resources")?;
                validate_host_deletion_scope(s, &r)?;
                platform::checked_target(path, root)?;
                check_tree(path)?;
                requirements.push(format!("delete:{}", r.id));
                requirements.push("external-use-reviewed".into());
                effects.push(format!(
                    "Delete exactly {} and its contents; projects: {:?}",
                    r.path, r.projects
                ));
                for child in explicit_descendants(s, &r)?.iter().skip(1) {
                    effects.push(format!("Related resource {} at {}: remove registry only if its actual file disappears",child.id,child.path));
                }
                if r.kind == "conda" {
                    let manager =
                        executable(input.manager.as_ref(), r.metadata["manager"].as_str())?;
                    steps.push(Step {
                        environment: Default::default(),
                        program: manager,
                        args: vec![
                            "env".into(),
                            "remove".into(),
                            "--yes".into(),
                            "--prefix".into(),
                            r.path.clone(),
                        ],
                    });
                }
            }
            _ => bail!(
                "Unsupported action; upgrades with uncertain compatibility require a new environment"
            ),
        }
        r
    };
    if input.action != "delete" {
        effects.push(
            "Download size and duration unknown; no estimate available from this adapter".into(),
        );
    }
    if r.kind == "conda" && input.action != "delete" && input.action != "remove-packages" {
        if let Some(source) = &input.source {
            anyhow::ensure!(
                source.starts_with("https://") && !source.contains(['@', '?', '#', '\r', '\n']),
                "Conda channel must be an explicit public HTTPS URL"
            );
            for step in &mut steps {
                step.args.extend([
                    "--override-channels".into(),
                    "--channel".into(),
                    source.clone(),
                ]);
            }
            effects.push(format!("Use only the selected conda channel: {source}; existing channel configuration is not changed"));
        } else {
            effects.push("Use existing conda channel configuration; Agent must review it before source-reviewed confirmation".into());
        }
    }
    let cache_root = s.config.env_root.as_ref().map(|r| r.join(".cache"));
    for step in &mut steps {
        if let Some(cache) = &cache_root {
            for (key, child) in [
                ("TEMP", "tmp"),
                ("TMP", "tmp"),
                ("PIP_CACHE_DIR", "pip"),
                ("CONDA_PKGS_DIRS", "conda"),
            ] {
                let path = cache.join(child);
                effects.push(format!(
                    "Manager {key} directory: {}; shared cache retained",
                    path.display()
                ));
                step.environment
                    .insert(key.into(), path.to_string_lossy().into());
            }
        }
        let path = Path::new(&step.program);
        anyhow::ensure!(path.is_file(), "Manager unavailable");
        effects.push(format!(
            "Executable SHA256 {} {}",
            step.program,
            platform::hash_file(path)?
        ));
    }
    let mut reliability = Reliability::default();
    if input.action == "delete" && r.scope == "host" {
        reliability.target_identity = Some(crate::recovery::identity(Path::new(&r.path))?);
    }
    let operation = Operation {
        id: id(),
        fingerprint: if input.action == "create" {
            None
        } else {
            Some(fingerprint(&r)?)
        },
        input,
        resource: r,
        risk: if requirements.is_empty() {
            "low"
        } else {
            "high"
        }
        .into(),
        requirements,
        effects,
        steps,
        status: "planned".into(),
        execution_status: "not_started".into(),
        verification_status: "not_started".into(),
        registration_status: "not_started".into(),
        created_at: now(),
        updated_at: now(),
        error: None,
        attempts: 0,
        reliability,
    };
    s.save("operations", &operation.id, &operation)?;
    Ok(operation)
}
fn event(s: &Store, id: &str, stage: &str) -> Result<()> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(s.root.join("logs").join(format!("{id}.jsonl")))?;
    writeln!(f, "{}", json!({"at":now(),"operation":id,"stage":stage}))?;
    Ok(())
}
pub fn apply(shared: Shared, id: &str, confirmations: &[String]) -> Result<Operation> {
    let (op, lease) = {
        let s = shared.lock().unwrap();
        let mut op: Operation = s.get("operations", id)?;
        anyhow::ensure!(
            op.status == "planned",
            "Only an unexecuted plan may be applied; inspect the prior result instead of repeating external work"
        );
        anyhow::ensure!(
            !(op.resource.kind == "wsl" && op.input.action == "delete"),
            "WSL_AUTO_UNREGISTER_DISABLED: WSL deletion is not supported by AIRoot"
        );
        if op.input.action == "delete" && op.resource.scope == "host" {
            anyhow::ensure!(
                op.reliability.target_identity.is_some(),
                "LEGACY_DELETE_PLAN_REQUIRES_REPLAN: filesystem identity was not captured"
            );
        }
        for required in &op.requirements {
            anyhow::ensure!(
                confirmations.contains(required),
                "AGENT_CONFIRMATION_REQUIRED: {required}"
            );
        }
        if let Some(fp) = &op.fingerprint {
            let current = s.resource(&op.resource.id)?;
            anyhow::ensure!(
                fingerprint(&current)? == *fp,
                "PLAN_CHANGED: resource metadata changed; generate a new plan"
            );
        }
        if op.input.action == "delete" {
            validate_host_deletion_scope(&s, &op.resource)?;
        }
        if op.resource.scope == "host" && op.fingerprint.is_some() {
            anyhow::ensure!(
                platform::processes_under(Path::new(&op.resource.path))?.is_empty(),
                "Resource has active processes; modification blocked"
            );
        }
        if op.resource.scope != "host" {
            if let Some(id) = op.resource.metadata["source_resource"].as_str() {
                let source = s.resource(id)?;
                anyhow::ensure!(
                    op.resource.metadata["source_fingerprint"] == fingerprint(&source)?,
                    "PLAN_CHANGED: source image changed"
                );
            }
            crate::platform_resources::revalidate(&op)?;
        }
        for step in &op.steps {
            if op.input.action == "bootstrap"
                && op
                    .input
                    .archive
                    .as_ref()
                    .is_some_and(|p| p.to_string_lossy() == step.program)
                && !Path::new(&step.program).exists()
            {
                continue;
            }
            let expected = format!(
                "Executable SHA256 {} {}",
                step.program,
                platform::hash_file(Path::new(&step.program))?
            );
            anyhow::ensure!(
                op.effects.contains(&expected),
                "PLAN_CHANGED: manager executable changed"
            );
        }
        if matches!(
            op.input.action.as_str(),
            "create" | "install-portable" | "import-wsl" | "bootstrap"
        ) {
            let root = if op.resource.class == "env" {
                s.config.env_root.as_ref()
            } else {
                s.config.tools_root.as_ref()
            }
            .context("resource root missing")?;
            let target = if op.input.action == "import-wsl" {
                op.input
                    .target
                    .as_deref()
                    .context("Missing WSL storage path")?
            } else {
                Path::new(&op.resource.path)
            };
            platform::checked_target(target, root)?;
            anyhow::ensure!(!target.exists(), "PLAN_CHANGED: target now exists");
        }
        let mut ids = s.related_ids(&op.resource)?;
        if let Some(id) = op.resource.metadata["source_resource"].as_str() {
            ids.push(id.to_owned());
        }
        if let Some(target) = &op.input.target {
            ids.push(format!(
                "path:{}",
                crate::store::path_key(&target.to_string_lossy())
            ));
        }
        ids.push(format!(
            "path:{}",
            crate::store::path_key(&op.resource.path)
        ));
        let mut lease =
            s.acquire_for_operation(ids, "write", op.input.project.clone(), Some(op.id.clone()))?;
        op.reliability.lease_id = Some(lease.id.clone());
        op.status = "executing".into();
        op.execution_status = "preparing".into();
        op.attempts += 1;
        op.updated_at = now();
        let prepared = (|| -> Result<()> {
            s.save("operations", id, &op)?;
            atomic_json(&s.root.join("pending").join(format!("{id}.json")), &op)?;
            event(&s, id, "preparing")?;
            s.backup()?;
            Ok(())
        })();
        if let Err(e) = prepared {
            op.status = "failed".into();
            op.error = Some(format!("Preparation failed before side effects: {e}"));
            op.execution_status = "not_started".into();
            let _ = s.save("operations", id, &op);
            let _ = atomic_json(&s.root.join("pending").join(format!("{id}.json")), &op);
            s.finish_lease(&mut lease)?;
            return Err(e);
        }
        (op, lease)
    };
    let returned = op.clone();
    std::thread::spawn(move || {
        execute(shared, op, lease);
    });
    Ok(returned)
}
fn execute(shared: Shared, mut op: Operation, mut lease: Lease) {
    let result = (|| -> Result<()> {
        let platform_resource = op.resource.scope != "host";
        let target = if op.input.action == "import-wsl" {
            op.input
                .target
                .as_deref()
                .context("Missing WSL storage path")?
        } else {
            Path::new(&op.resource.path)
        }
        .to_path_buf();
        let target = target.as_path();
        let created = matches!(
            op.input.action.as_str(),
            "create" | "install-portable" | "import-wsl" | "bootstrap"
        );
        if created {
            fs::create_dir(target).context("Could not exclusively reserve new target")?;
            crate::recovery::own(&shared, &mut op, target, "target")?;
        }
        op.execution_status = "executing".into();
        crate::recovery::persist(&shared, &mut op)?;
        {
            let s = shared.lock().unwrap();
            s.save("operations", &op.id, &op)?;
            event(&s, &op.id, "executing")?;
        }
        let execution = (|| -> Result<()> {
            if op.input.action == "bootstrap" {
                crate::bootstrap::before_install(&shared, &mut op)?;
            }
            if matches!(op.input.action.as_str(), "install-portable" | "bootstrap")
                && op.resource.metadata["download_owned"] == true
            {
                let archive = op.input.archive.as_ref().context("Missing download path")?;
                let root = {
                    let s = shared.lock().unwrap();
                    if op.resource.class == "env" {
                        s.config.env_root.clone()
                    } else {
                        s.config.tools_root.clone()
                    }
                }
                .context("Missing root")?;
                anyhow::ensure!(
                    archive.starts_with(root.join(".cache/downloads")) && !archive.exists(),
                    "PLAN_CHANGED: download destination changed or already exists"
                );
                let mut ancestor = archive.parent();
                while let Some(p) = ancestor {
                    anyhow::ensure!(
                        !p.exists() || !platform::is_link(p)?,
                        "Linked download parent refused"
                    );
                    ancestor = p.parent();
                }
                fs::create_dir_all(archive.parent().context("Missing download parent")?)?;
                let archive = archive.clone();
                fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&archive)?;
                crate::recovery::own(&shared, &mut op, &archive, "download")?;
            }
            for step in op.steps.clone() {
                if op.input.action == "bootstrap"
                    && op
                        .input
                        .archive
                        .as_ref()
                        .is_some_and(|p| p.to_string_lossy() == step.program)
                {
                    anyhow::ensure!(
                        platform::hash_file(Path::new(&step.program))?.eq_ignore_ascii_case(
                            op.input
                                .sha256
                                .as_deref()
                                .context("Missing installer SHA256")?
                        ),
                        "Installer hash mismatch; refused execution"
                    );
                }
                for path in step.environment.values() {
                    let path = Path::new(path);
                    let mut ancestor = Some(path);
                    while let Some(p) = ancestor {
                        if p.exists() {
                            anyhow::ensure!(
                                !platform::is_link(p)?,
                                "Manager cache/temp path has a linked ancestor"
                            );
                        }
                        ancestor = p.parent();
                    }
                    fs::create_dir_all(path)?;
                }
                if op.input.action == "bootstrap"
                    && op
                        .input
                        .archive
                        .as_ref()
                        .is_some_and(|p| p.to_string_lossy() == step.program)
                {
                    crate::bootstrap::record_vendor_start(&mut op.reliability.vendor, "installer")?;
                    crate::recovery::persist(&shared, &mut op)?;
                }
                let res = platform::run_with_env(
                    &step.program,
                    &step.args,
                    Duration::from_secs(3600),
                    &step.environment,
                    |pid| {
                        lease.pid = Some(pid);
                        lease.process_stamp = platform::process_stamp(pid)?;
                        lease.process_job = Some(platform::process_job_name(pid)?);
                        lease.state = "running".into();
                        shared.lock().unwrap().save_lease(&lease)
                    },
                )?;
                if op.input.action == "bootstrap"
                    && op.reliability.vendor["installer_started"] == true
                {
                    op.reliability.vendor["installer_exit_code"] = json!(res.code);
                    if res.code == 3010 {
                        op.resource.metadata["reboot_required"] = json!(true);
                    }
                    crate::recovery::persist(&shared, &mut op)?;
                }
                if res.descendants_active {
                    lease.state = "descendants_unknown".into();
                    shared.lock().unwrap().save_lease(&lease)?;
                    bail!("Child processes may still use the resource; protection retained");
                }
                lease.pid = None;
                lease.process_stamp = None;
                lease.state = "reserved".into();
                shared.lock().unwrap().save_lease(&lease)?;
                if res.code != 0 && !(op.input.action == "bootstrap" && res.code == 3010) {
                    op.reliability.failure_confirmed = true;
                    op.error.get_or_insert_with(||format!("Manager exited with code {}; raw output excluded to avoid storing credentials",res.code));
                    crate::recovery::persist(&shared, &mut op)?;
                }
                if op.input.action == "bootstrap"
                    && op.reliability.vendor["installer_started"] == true
                {
                    crate::bootstrap::capture_after(&shared, &mut op)?;
                }
                if op.input.action == "bootstrap" && res.code == 3010 {
                    op.resource.metadata["reboot_required"] = json!(true);
                    op.effects.push("Installer reports restart required; AIRoot does not restart. Agent must obtain explicit restart confirmation.".into());
                }
                if res.code != 0 && !(op.input.action == "bootstrap" && res.code == 3010) {
                    op.reliability.failure_confirmed = true;
                    crate::recovery::persist(&shared, &mut op)?;
                }
                anyhow::ensure!(
                    res.code == 0 || (op.input.action == "bootstrap" && res.code == 3010),
                    "Manager exited with code {}; raw output excluded from persistent logs to avoid storing credentials",
                    res.code
                );
            }
            if op.input.action == "install-portable" {
                crate::portable::extract(
                    op.input.archive.as_deref().context("Archive missing")?,
                    target,
                    op.input.sha256.as_deref().context("SHA256 missing")?,
                )?;
            }
            if op.input.action == "delete"
                && !platform_resource
                && crate::recovery::entry_exists(target)?
            {
                {
                    let s = shared.lock().unwrap();
                    validate_host_deletion_scope(&s, &op.resource)?;
                }
                let identity = op
                    .reliability
                    .target_identity
                    .as_deref()
                    .context("Delete plan lacks captured filesystem identity")?;
                remove_planned_target(target, identity)?;
            }
            Ok(())
        })();
        if let Err(e) = execution {
            // Process timeout/unknown descendants do not establish a failed installation.
            if lease.state == "reserved"
                && crate::recovery::new_host(&op)
                && op.reliability.vendor["installer_started"] != true
            {
                op.reliability.failure_confirmed = true;
            }
            return Err(e);
        }
        op.execution_status = "succeeded".into();
        crate::recovery::persist(&shared, &mut op)?;
        lease.state = "reserved".into();
        {
            let s = shared.lock().unwrap();
            s.save("operations", &op.id, &op)?;
            event(&s, &op.id, "verifying")?;
        }
        op.verification_status = "verifying".into();
        if op.input.action == "delete" {
            anyhow::ensure!(
                if platform_resource {
                    crate::platform_resources::deleted(&op.resource)?
                } else {
                    !crate::recovery::entry_exists(target)?
                },
                "Deletion incomplete; registry retained"
            );
        } else if platform_resource {
            op.resource = crate::platform_resources::verify_operation(&op)?;
        } else {
            if op.input.action == "install-portable" {
                op.resource = match crate::portable::identify(op.resource.clone()) {
                    Ok(resource) => resource,
                    Err(error) => {
                        if error.to_string().starts_with("BROKEN_INSTALLATION:")
                            && op.registration_status != "failed"
                        {
                            op.reliability.failure_confirmed = true;
                        }
                        return Err(error);
                    }
                };
            }
            op.resource = adapters::verify_with(op.resource.clone(), |pid| {
                lease.pid = Some(pid);
                lease.process_stamp = platform::process_stamp(pid)?;
                lease.process_job = Some(platform::process_job_name(pid)?);
                lease.state = "verifying".into();
                shared.lock().unwrap().save_lease(&lease)
            })?;
            lease.state = "reserved".into();
            lease.pid = None;
            lease.process_stamp = None;
            shared.lock().unwrap().save_lease(&lease)?;
            if op.resource.status != "available" && crate::recovery::new_host(&op) {
                op.reliability.failure_confirmed = true;
            }
            anyhow::ensure!(
                op.resource.status == "available",
                "Installation exists but verification failed; preserve resource and inspect before retry"
            );
        }
        if op.input.action != "delete"
            && op.resource.scope == "host"
            && !crate::recovery::version_matches(&op)
        {
            if crate::recovery::new_host(&op) {
                op.reliability.failure_confirmed = true;
            }
            bail!("Installed version differs from requested version");
        }
        op.verification_status = "succeeded".into();
        op.registration_status = "pending".into();
        {
            let s = shared.lock().unwrap();
            atomic_json(&s.root.join("pending").join(format!("{}.json", op.id)), &op)?;
        }
        let registration = (|| -> Result<()> {
            let s = shared.lock().unwrap();
            let tx = s.db.unchecked_transaction()?;
            if op.input.action == "delete" {
                s.register_deletion(&op.resource)?;
            } else {
                anyhow::ensure!(
                    s.upsert(op.resource.clone())?.is_some(),
                    "Resource is ignored; restore management explicitly"
                );
            }
            tx.commit()?;
            Ok(())
        })();
        if let Err(e) = registration {
            op.registration_status = "failed".into();
            bail!(
                "External action succeeded at {}; registration failed: {e}; do not repeat installation",
                op.resource.path
            );
        }
        op.registration_status = "succeeded".into();
        op.status = "completed".into();
        Ok(())
    })();
    if let Err(e) = result {
        if op.verification_status == "verifying" {
            op.verification_status = "failed".into();
        }
        op.status = if op.registration_status == "failed" {
            "registration_failed"
        } else {
            "failed"
        }
        .into();
        if op.execution_status != "succeeded" {
            op.execution_status = "failed".into();
        }
        if op.error.is_none() {
            op.error = Some(format!("{e:#}"));
        }
    }
    let recorded = crate::recovery::persist(&shared, &mut op).is_ok();
    if recorded
        && op.status == "failed"
        && op.reliability.failure_confirmed
        && crate::recovery::new_host(&op)
    {
        let _ = crate::recovery::cleanup(&shared, &mut op);
    }
    let _ = crate::recovery::finalize(&shared, &mut op);
    let s = shared.lock().unwrap();
    let _ = event(&s, &op.id, &op.status);
    let _ = s.backup();
    let _ = s.prune_logs();
    drop(s);
    if op.status == "failed"
        && op.reliability.failure_confirmed
        && !crate::recovery::new_host(&op)
        && op.input.action != "delete"
    {
        let _ = crate::recovery::start(shared.clone(), &op.id, false);
    }
}
pub fn run(shared: Shared, id: &str, args: Vec<String>, project: Option<String>) -> Result<Value> {
    let (r, mut lease, entry) = {
        let s = shared.lock().unwrap();
        let r = s.resource(id)?;
        anyhow::ensure!(
            r.scope == "host",
            "Host runner cannot execute internal resource identities"
        );
        anyhow::ensure!(
            r.status == "available",
            "Verify this resource before running"
        );
        if let Some(expected) = r.metadata["verified_entry_sha256"].as_str() {
            anyhow::ensure!(
                platform::hash_file(Path::new(r.entry.as_deref().context("Missing entry")?))?
                    == expected,
                "RESOURCE_CHANGED: executable changed; verify again before running"
            );
        }
        if r.kind == "script" {
            anyhow::ensure!(
                r.metadata["verified_script_sha256"] == platform::hash_file(Path::new(&r.path))?,
                "RESOURCE_CHANGED: script changed; verify again before running"
            );
            let parent = s.resource(
                r.parent_id
                    .as_deref()
                    .context("Interpreter association missing")?,
            )?;
            anyhow::ensure!(
                parent.entry == r.entry && parent.status == "available",
                "Interpreter changed or unavailable; verify the environment and script"
            );
        }
        anyhow::ensure!(
            r.kind != "mcp-connector",
            "MCP needs an interactive stdio client: use the registered connection with usage acquire/release; resource run has no stdin relay"
        );
        let entry = r.entry.clone().context("Missing entry")?;
        anyhow::ensure!(
            Path::new(&entry).is_absolute(),
            "Resource entry must be an absolute path"
        );
        let lease = s.acquire(s.related_ids(&r)?, "read", project.clone())?;
        (r, lease, entry)
    };
    let mut args = args;
    if r.kind == "script" {
        args.insert(0, r.path.clone());
    }
    let result = platform::run(&entry, &args, Duration::from_secs(86400), |pid| {
        lease.pid = Some(pid);
        lease.process_stamp = platform::process_stamp(pid)?;
        lease.process_job = Some(platform::process_job_name(pid)?);
        lease.state = "running".into();
        let s = shared.lock().unwrap();
        s.save_lease(&lease)?;
        if let Some(p) = &project {
            let mut r = s.resource(id)?;
            if !r.projects.contains(p) {
                r.projects.push(p.clone());
                s.update_resource(&r)?;
            }
        }
        Ok(())
    });
    let s = shared.lock().unwrap();
    match result {
        Ok(out) => {
            if !out.descendants_active {
                s.finish_lease(&mut lease)?;
            } else {
                lease.state = "descendants_unknown".into();
                s.save_lease(&lease)?;
            }
            Ok(
                json!({"exit_code":out.code,"stdout":out.stdout,"stderr":out.stderr,"output_truncated":out.output_truncated,"lease":lease}),
            )
        }
        Err(e) => {
            lease.state = "needs_review".into();
            s.save_lease(&lease)?;
            Err(e)
        }
    }
}

#[cfg(test)]
mod deletion_identity_tests {
    use super::remove_planned_target;
    use std::{fs, path::Path};

    #[test]
    fn planned_delete_refuses_replacement_at_same_path() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target");
        fs::create_dir(&target).unwrap();
        fs::write(target.join("original"), "old").unwrap();
        let identity = crate::recovery::identity(Path::new(&target)).unwrap();
        let moved = root.path().join("planned-instance");
        fs::rename(&target, &moved).unwrap();
        fs::create_dir(&target).unwrap();
        fs::write(target.join("replacement"), "new").unwrap();

        assert!(remove_planned_target(&target, &identity).is_err());
        assert!(target.join("replacement").exists());
        assert!(moved.join("original").exists());
    }

    #[cfg(unix)]
    #[test]
    fn planned_delete_refuses_dangling_file_symlink_replacement() {
        use std::os::unix::fs::symlink;

        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target");
        fs::create_dir(&target).unwrap();
        let identity = crate::recovery::identity(Path::new(&target)).unwrap();
        fs::remove_dir(&target).unwrap();
        symlink(root.path().join("missing-target"), &target).unwrap();

        assert!(remove_planned_target(&target, &identity).is_err());
        assert!(
            fs::symlink_metadata(&target)
                .unwrap()
                .file_type()
                .is_symlink()
        );
    }

    #[cfg(windows)]
    #[test]
    fn planned_delete_refuses_dangling_file_symlink_replacement() {
        use std::os::windows::fs::symlink_file;

        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("target");
        fs::create_dir(&target).unwrap();
        let identity = crate::recovery::identity(Path::new(&target)).unwrap();
        fs::remove_dir(&target).unwrap();
        symlink_file(root.path().join("missing-target"), &target).unwrap();

        assert!(remove_planned_target(&target, &identity).is_err());
        assert!(
            fs::symlink_metadata(&target)
                .unwrap()
                .file_type()
                .is_symlink()
        );
    }
}
