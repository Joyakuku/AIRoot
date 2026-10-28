use crate::{model::*, platform, store::Store};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{path::Path, time::Duration};
fn call(exe: &str, args: &[String]) -> Result<String> {
    let out = platform::run(exe, args, Duration::from_secs(15), |_| Ok(()))?;
    anyhow::ensure!(
        out.code == 0,
        "Platform command failed ({}); runtime may not be installed/running",
        out.code
    );
    Ok(out.stdout)
}
fn docker() -> Result<(String, String)> {
    let exe = platform::executable("docker")
        .context("Docker CLI is unavailable")?
        .to_string_lossy()
        .into_owned();
    let context = call(&exe, &["context".into(), "show".into()])?;
    let info: Value = serde_json::from_str(&call(
        &exe,
        &["context".into(), "inspect".into(), context.trim().into()],
    )?)?;
    let host = info[0]["Endpoints"]["docker"]["Host"]
        .as_str()
        .context("Docker context has no engine endpoint")?;
    anyhow::ensure!(
        host.starts_with("npipe:////./pipe/") && !host.contains('@'),
        "REMOTE_DOCKER_CONTEXT_REJECTED: only local named-pipe engines are eligible"
    );
    Ok((exe, host.into()))
}
fn docker_args(host: &str, tail: &[&str]) -> Vec<String> {
    let mut a = vec!["--host".into(), host.into()];
    a.extend(tail.iter().map(|s| (*s).into()));
    a
}
pub fn probe(kind: &str) -> Result<Vec<Resource>> {
    let mut found = vec![];
    match kind {
        "wsl" => {
            let exe = platform::executable("wsl").context(
                "WSL command unavailable; system enablement requires a separate high-risk plan",
            )?;
            let exe = exe.to_string_lossy();
            let names = call(&exe, &["--list".into(), "--quiet".into()])?;
            let mut runtime = Resource::new(
                "env",
                "wsl-runtime",
                "Windows Subsystem for Linux",
                "wsl://platform",
            );
            runtime.scope = "wsl-platform".into();
            runtime.status = "available".into();
            runtime.metadata = json!({"manager":exe,"internal_scan":"on-demand"});

            found.push(runtime.clone());

            for name in names.lines().map(str::trim).filter(|n| !n.is_empty()) {
                let mut r = Resource::new("env", "wsl", name, &format!("wsl://{name}"));
                r.scope = "wsl-platform".into();
                r.parent_id = Some(runtime.id.clone());
                r.status = "available".into();
                r.metadata = json!({"manager":exe,"distribution":name,"data_status":"unknown","internal_scan":"on-demand"});
                r.metadata["identity"] = platform::wsl_identity(name)?;
                found.push(r);
            }
        }
        "docker" => {
            let (exe, host) = docker()?;
            let info = call(
                &exe,
                &docker_args(&host, &["info", "--format", "{{json .ServerVersion}}"]),
            )?;
            let mut runtime = Resource::new("env", "docker-runtime", "Docker local engine", &host);
            runtime.scope = "docker-platform".into();
            runtime.version = Some(info.trim().trim_matches('"').into());
            runtime.status = "available".into();
            runtime.metadata = json!({"manager":exe,"host":host});

            found.push(runtime.clone());
            for (kind, args) in [
                (
                    "docker-image",
                    vec!["image", "ls", "--no-trunc", "--format", "{{json .}}"],
                ),
                (
                    "docker-container",
                    vec!["ps", "--all", "--no-trunc", "--format", "{{json .}}"],
                ),
            ] {
                let lines = call(&exe, &docker_args(&host, &args))?;
                for line in lines.lines().filter(|l| !l.trim().is_empty()) {
                    let v: Value = serde_json::from_str(line)?;
                    let object = v["ID"].as_str().context("Missing Docker ID")?;
                    let name = if kind == "docker-image" {
                        format!(
                            "{}:{}",
                            v["Repository"].as_str().unwrap_or("unknown"),
                            v["Tag"].as_str().unwrap_or("unknown")
                        )
                    } else {
                        v["Names"].as_str().unwrap_or(object).into()
                    };
                    let mut r =
                        Resource::new("env", kind, &name, &format!("{host}/{kind}/{object}"));
                    r.scope = "docker-platform".into();
                    r.parent_id = Some(runtime.id.clone());
                    r.status = "available".into();
                    r.metadata = json!({"manager":exe,"host":host,"object":object,"data_status":"unknown","running":v["State"],"image":v["Image"]});
                    found.push(r);
                }
            }
        }
        _ => bail!("Supported platform probes: wsl, docker"),
    }
    Ok(found)
}
pub fn discover(s: &Store, kind: &str) -> Result<Value> {
    let found = probe(kind)?;
    let registered = register_rows(s, found)?;
    s.backup()?;
    Ok(
        json!({"resources":registered,"scope":"host platform only; internal resources not recursively scanned"}),
    )
}
pub fn register_rows(s: &Store, found: Vec<Resource>) -> Result<Vec<Option<Resource>>> {
    let mut registered = vec![];
    let mut ids = std::collections::HashMap::<String, String>::new();
    for mut r in found {
        let old = r.id.clone();
        if let Some(parent) = &r.parent_id {
            r.parent_id = ids.get(parent).cloned();
        }
        if let Some(r) = s.upsert(r)? {
            ids.insert(old, r.id.clone());
            registered.push(Some(r));
        } else {
            registered.push(None);
        }
    }
    Ok(registered)
}

pub fn plan(s: &Store, input: PlanInput) -> Result<Operation> {
    let r = s.resource(
        input
            .resource_id
            .as_deref()
            .context("Platform operation requires a registered resource")?,
    )?;
    if r.kind == "wsl" && input.action == "delete" {
        bail!(
            "WSL_AUTO_UNREGISTER_DISABLED: WSL unregister targets a reusable distribution name, not its stable registration identity; unregister it manually outside AIRoot after independently verifying the target"
        );
    }
    if input.action == "delete" && matches!(r.kind.as_str(), "wsl" | "docker-container") {
        backup_precondition(&input)?;
    }
    let mut requirements = vec![];
    let mut steps = vec![];
    let mut effects = vec![];
    match (r.kind.as_str(), input.action.as_str()) {
        ("docker-container", "delete") | ("docker-image", "delete") => {
            let (exe, host) = docker()?;
            anyhow::ensure!(
                r.metadata["host"] == host,
                "PLAN_CHANGED: local context changed"
            );
            let object = r.metadata["object"].as_str().context("Missing object ID")?;
            requirements.push(format!("delete:{}", r.id));
            if r.kind == "docker-container" {
                requirements.push("backup-or-migration-verified".into());
                let inspected: Value = serde_json::from_str(&call(
                    &exe,
                    &docker_args(
                        &host,
                        &["inspect", object, "--format", "{{json .State.Running}}"],
                    ),
                )?)?;
                anyhow::ensure!(
                    inspected == false,
                    "Container is running; stop it as a separate operation before deletion"
                );
                effects.push("Container filesystem will be removed; data must be backed up or migrated. Host bind mounts and named/anonymous volumes are retained.".into());
            }
            steps.push(Step {
                environment: Default::default(),
                program: exe,
                args: docker_args(
                    &host,
                    &[
                        if r.kind == "docker-container" {
                            "container"
                        } else {
                            "image"
                        },
                        "rm",
                        object,
                    ],
                ),
            });
        }
        ("wsl", "delete") => {
            bail!("WSL_AUTO_UNREGISTER_DISABLED: WSL deletion is not supported by AIRoot")
        }
        ("docker-container", "stop" | "start") => {
            let (exe, host) = docker()?;
            anyhow::ensure!(r.metadata["host"] == host, "Local Docker context changed");
            requirements.push(format!("{}:{}", input.action, r.id));
            steps.push(Step {
                environment: Default::default(),
                program: exe,
                args: docker_args(
                    &host,
                    &[
                        &input.action,
                        r.metadata["object"].as_str().context("Object missing")?,
                    ],
                ),
            });
        }
        _ => bail!(
            "Platform adapter does not implement this operation; system installation/import/pull require separate recipes"
        ),
    }
    for step in &steps {
        effects.push(format!(
            "Executable SHA256 {} {}",
            step.program,
            platform::hash_file(Path::new(&step.program))?
        ));
    }
    let op = Operation {
        id: id(),
        input,
        resource: r.clone(),
        fingerprint: Some(crate::operations::fingerprint(&r)?),
        risk: "high".into(),
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
        reliability: Default::default(),
    };
    s.save("operations", &op.id, &op)?;
    Ok(op)
}
pub fn deleted(r: &Resource) -> Result<bool> {
    if r.kind == "wsl" {
        let names = call(
            r.metadata["manager"].as_str().context("Missing manager")?,
            &["--list".into(), "--quiet".into()],
        )?;
        return Ok(!names.lines().any(|s| s.trim() == r.name));
    }
    let (exe, host) = docker()?;
    anyhow::ensure!(r.metadata["host"] == host, "Context changed");
    let object = r.metadata["object"].as_str().context("Missing ID")?;
    let tail = if r.kind == "docker-image" {
        vec!["image", "ls", "--no-trunc", "--quiet"]
    } else {
        vec!["ps", "--all", "--no-trunc", "--quiet"]
    };
    let ids = call(&exe, &docker_args(&host, &tail))?;
    Ok(!ids.lines().any(|id| id.trim() == object))
}

fn backup_precondition(input: &PlanInput) -> Result<()> {
    if let Some(archive) = &input.archive {
        anyhow::ensure!(
            archive.is_absolute() && archive.is_file(),
            "Backup must be an existing absolute file"
        );
        anyhow::ensure!(
            std::fs::metadata(archive)?.len() > 0,
            "Backup file is empty"
        );
        let expected = input.sha256.as_deref().context("Backup SHA256 required")?;
        anyhow::ensure!(
            platform::hash_file(archive)?.eq_ignore_ascii_case(expected),
            "Backup verification failed"
        );
    } else if let Some(destination) = &input.target {
        anyhow::ensure!(
            destination.is_absolute() && destination.is_dir(),
            "Migration destination must exist on the host"
        );
        anyhow::ensure!(
            std::fs::read_dir(destination)?.next().is_some(),
            "Migration destination is empty"
        );
    } else {
        bail!(
            "BACKUP_OR_MIGRATION_REQUIRED: supply a verified backup archive and SHA256, or a nonempty host migration target; no platform deletion was planned"
        );
    }
    Ok(())
}
pub fn revalidate(op: &Operation) -> Result<()> {
    if op.resource.kind == "wsl" && op.input.action == "delete" {
        bail!(
            "WSL_AUTO_UNREGISTER_DISABLED: saved WSL deletion plans cannot be executed because unregister targets a reusable distribution name, not its stable registration identity; unregister it manually outside AIRoot after independently verifying the target"
        );
    }
    if op.input.action == "import-wsl" {
        let archive = op.input.archive.as_deref().context("Missing archive")?;
        anyhow::ensure!(
            platform::hash_file(archive)?
                .eq_ignore_ascii_case(op.input.sha256.as_deref().context("Missing SHA256")?),
            "PLAN_CHANGED: rootfs archive changed"
        );
        let names = call(
            op.resource.metadata["manager"]
                .as_str()
                .context("Missing manager")?,
            &["--list".into(), "--quiet".into()],
        )?;
        anyhow::ensure!(
            !names
                .lines()
                .any(|n| n.trim().eq_ignore_ascii_case(&op.resource.name)),
            "PLAN_CHANGED: distribution name exists"
        );
    } else if op.resource.kind == "wsl" {
        anyhow::ensure!(
            platform::wsl_identity(&op.resource.name)? == op.resource.metadata["identity"],
            "PLAN_CHANGED: WSL registration identity changed; discover again"
        );
    }

    if op.input.action == "delete"
        && matches!(op.resource.kind.as_str(), "wsl" | "docker-container")
    {
        backup_precondition(&op.input)?;
    }
    if op.resource.kind.starts_with("docker-") {
        let (exe, host) = docker()?;
        anyhow::ensure!(
            op.resource.metadata["host"] == host,
            "Docker context changed"
        );
        if op.resource.kind == "docker-container" && op.input.action == "delete" {
            let running = call(
                &exe,
                &docker_args(
                    &host,
                    &[
                        "inspect",
                        op.resource.metadata["object"]
                            .as_str()
                            .context("ID missing")?,
                        "--format",
                        "{{json .State.Running}}",
                    ],
                ),
            )?;
            anyhow::ensure!(
                running.trim() == "false",
                "Container is running; deletion blocked"
            );
        }
    }
    Ok(())
}

pub fn inspect_internal(shared: crate::ipc::Shared, id: &str, path: &str) -> Result<Value> {
    anyhow::ensure!(
        path.starts_with('/') && !path.contains(['\n', '\r', '\t']),
        "Specify an absolute Linux path without control characters"
    );
    let (parent, mut lease) = {
        let s = shared.lock().unwrap();
        let r = s.resource(id)?;
        anyhow::ensure!(
            matches!(r.kind.as_str(), "wsl" | "docker-container"),
            "Internal lookup requires a distribution or local container"
        );
        let l = s.acquire(s.related_ids(&r)?, "read", None)?;
        (r, l)
    };
    let script = r#"set -eu
cd -P -- "$1"
p=$(pwd -P)
if [ -f pyvenv.cfg ]; then printf 'env\tvenv\t%s\t%s/bin/python\n' "$p" "$p";
elif [ -f conda-meta/history ]; then printf 'env\tconda\t%s\t%s/bin/python\n' "$p" "$p";
elif [ -x bin/python3 ]; then printf 'env\tpython\t%s\t%s/bin/python3\n' "$p" "$p"; fi
if [ -x bin/java ] && [ -f release ]; then printf 'env\tjava\t%s\t%s/bin/java\n' "$p" "$p"; fi
if [ -x bin/node ]; then printf 'env\tnode\t%s\t%s/bin/node\n' "$p" "$p"; fi
for n in ffmpeg ffprobe blender git rg fd uv cmake cargo rustc docker; do
 for dir in "$p" "$p/bin"; do
  if [ -f "$dir/$n" ] && [ -x "$dir/$n" ]; then printf 'tool\tcli\t%s/%s\t%s/%s\n' "$dir" "$n" "$dir" "$n"; fi
 done
done
"#;
    let mut started = false;
    let result = (|| -> Result<Value> {
        let (exe, mut args) = if parent.kind == "wsl" {
            (
                parent.metadata["manager"]
                    .as_str()
                    .context("Missing WSL executable")?
                    .to_owned(),
                vec![
                    "--distribution".into(),
                    parent.name.clone(),
                    "--exec".into(),
                ],
            )
        } else {
            let (exe, host) = docker()?;
            anyhow::ensure!(
                parent.metadata["host"] == host,
                "Local Docker context changed"
            );
            (
                exe,
                docker_args(
                    &host,
                    &[
                        "exec",
                        parent.metadata["object"]
                            .as_str()
                            .context("Missing container ID")?,
                    ],
                ),
            )
        };
        args.extend([
            "sh".into(),
            "-c".into(),
            script.into(),
            "airoot-inspect".into(),
            path.into(),
        ]);
        let out = platform::run(&exe, &args, Duration::from_secs(30), |pid| {
            started = true;
            lease.pid = Some(pid);
            lease.process_stamp = platform::process_stamp(pid)?;
            lease.state = "running".into();
            shared.lock().unwrap().save_lease(&lease)
        })?;
        anyhow::ensure!(
            !out.descendants_active,
            "Internal lookup left related host processes; protection retained"
        );
        lease.state = "finished".into();
        anyhow::ensure!(
            out.code == 0 && !out.output_truncated,
            "Internal location is unavailable, unsupported, or lookup failed; coverage unknown"
        );
        let s = shared.lock().unwrap();
        let mut found = vec![];
        for line in out.stdout.lines() {
            let fields: Vec<_> = line.split('\t').collect();
            anyhow::ensure!(fields.len() == 4, "Invalid internal discovery response");
            let (class, kind, p, entry) = (fields[0], fields[1], fields[2], fields[3]);
            let mut r = Resource::new(class, kind, p.rsplit('/').next().unwrap_or(p), p);
            r.scope = format!("{}:{}", parent.kind, parent.id);
            r.parent_id = Some(parent.id.clone());
            r.entry = Some(entry.into());
            r.metadata = json!({"internal_path":p,"owner_id":parent.id,"owner_name":parent.name,"discovery":"explicit structural lookup","execution":"through owner platform; host resource run refused"});
            if let Some(r) = s.upsert(r)? {
                found.push(r);
            }
        }
        Ok(
            json!({"resources":found,"coverage":"only the requested internal directory; signatures verified, executables not run"}),
        )
    })();
    let s = shared.lock().unwrap();
    if !started || lease.state == "finished" {
        s.finish_lease(&mut lease)?;
    } else {
        lease.state = "needs_review".into();
        s.save_lease(&lease)?;
    }
    result
}

/// New platform instances are planned separately from destructive resource operations.
pub fn provision(s: &Store, mut input: PlanInput) -> Result<Operation> {
    let project = input
        .project
        .as_deref()
        .context("New platform resources require project")?;
    anyhow::ensure!(!project.trim().is_empty(), "Project cannot be blank");
    let mut requirements = vec!["source-reviewed".into()];
    let mut effects = vec![];
    let (mut r, step) = match input.action.as_str() {
        "import-wsl" => {
            let name = input
                .name
                .as_deref()
                .context("Task abbreviation required")?;
            valid_name(name)?;
            let exe = platform::executable("wsl")
                .context("WSL prerequisite missing; enable platform separately")?;
            let exe = exe.to_string_lossy().into_owned();
            let names = call(&exe, &["--list".into(), "--quiet".into()])?;
            anyhow::ensure!(
                !names.lines().any(|n| n.trim().eq_ignore_ascii_case(name)),
                "Distribution name already exists"
            );
            let root = s
                .config
                .env_root
                .as_ref()
                .context("Environment root required")?;
            let target = platform::checked_target(
                &input.target.clone().unwrap_or_else(|| root.join(name)),
                root,
            )?;
            anyhow::ensure!(!target.exists(), "Target already exists");
            let archive = platform::canonical(
                input
                    .archive
                    .as_deref()
                    .context("Reviewed rootfs TAR required")?,
            )?;
            anyhow::ensure!(
                !archive.starts_with(platform::canonical(&s.root)?),
                "Rootfs archive cannot be in information directory"
            );
            let sha = input.sha256.as_deref().context("Rootfs SHA256 required")?;
            anyhow::ensure!(
                platform::hash_file(&archive)?.eq_ignore_ascii_case(sha),
                "Rootfs hash mismatch"
            );
            let mut r = Resource::new("env", "wsl", name, &format!("wsl://{name}"));
            r.scope = "wsl-platform".into();
            r.metadata = json!({"manager":exe,"distribution":name,"storage_path":target,"archive":archive,"sha256":sha,"data_status":"unknown","created_by_airoot":true});
            input.target = Some(target.clone());
            input.archive = Some(archive.clone());
            effects.push(format!(
                "Import reviewed rootfs into {}; no platform enablement or restart is performed",
                target.display()
            ));
            requirements.push("system-platform-change".into());
            (
                r,
                Step {
                    program: exe,
                    args: vec![
                        "--import".into(),
                        name.into(),
                        target.to_string_lossy().into(),
                        archive.to_string_lossy().into(),
                        "--version".into(),
                        "2".into(),
                    ],
                    environment: Default::default(),
                },
            )
        }
        "pull-image" => {
            let source = input
                .source
                .as_deref()
                .context("Pinned image reference required")?;
            let (repository, digest) = source.rsplit_once("@sha256:").context(
                "Use repository@sha256:digest; mutable tags are not accepted for execution plans",
            )?;
            anyhow::ensure!(
                !repository.is_empty()
                    && !repository.starts_with('-')
                    && !repository.contains(['@', ' ', '\r', '\n'])
                    && digest.len() == 64
                    && digest.chars().all(|c| c.is_ascii_hexdigit()),
                "Invalid pinned image reference"
            );
            let (exe, host) = docker()?;
            call(
                &exe,
                &docker_args(&host, &["info", "--format", "{{json .ServerVersion}}"]),
            )?;
            let mut r = Resource::new(
                "env",
                "docker-image",
                input.name.as_deref().unwrap_or(repository),
                &format!("{host}/pending-image/{digest}"),
            );
            r.scope = "docker-platform".into();
            r.metadata = json!({"manager":exe,"host":host,"source":source,"data_status":"unknown"});
            effects.push("Download size/duration unknown; Docker manages storage at its configured normal data root. Existing images and volumes are preserved.".into());
            (
                r,
                Step {
                    program: exe,
                    args: docker_args(&host, &["image", "pull", source]),
                    environment: Default::default(),
                },
            )
        }
        "create-container" => {
            let source = s.resource(
                input
                    .resource_id
                    .as_deref()
                    .context("Registered image resource_id required")?,
            )?;
            anyhow::ensure!(
                source.kind == "docker-image",
                "Container source must be an image"
            );
            let name = input
                .name
                .as_deref()
                .context("Task abbreviation required")?;
            valid_name(name)?;
            let (exe, host) = docker()?;
            anyhow::ensure!(
                source.metadata["host"] == host,
                "Docker local endpoint changed"
            );
            let names = call(
                &exe,
                &docker_args(&host, &["ps", "--all", "--format", "{{.Names}}"]),
            )?;
            anyhow::ensure!(
                !names.lines().any(|n| n.trim() == name),
                "Container name already exists"
            );
            let object = source.metadata["object"]
                .as_str()
                .context("Image ID missing")?;
            let mut args = docker_args(
                &host,
                &[
                    "container",
                    "create",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    object,
                ],
            );
            let creation_token = id();
            let image_position = args.len() - 1;
            args.splice(
                image_position..image_position,
                [
                    "--label".into(),
                    format!("airoot.creation={creation_token}"),
                ],
            );
            args.extend(input.packages.clone());
            let mut r = Resource::new(
                "env",
                "docker-container",
                name,
                &format!("{host}/pending-container/{name}"),
            );
            r.scope = "docker-platform".into();
            r.parent_id = source.parent_id.clone();
            r.metadata = json!({"manager":exe,"host":host,"source_resource":source.id,"source_fingerprint":crate::operations::fingerprint(&source)?,"image":object,"object_name":name,"creation_token":creation_token,"data_status":"unknown"});
            effects.push("Create a stopped local container; no host mounts, published ports, privilege or automatic start. packages supplies literal container command arguments.".into());
            (
                r,
                Step {
                    program: exe,
                    args,
                    environment: Default::default(),
                },
            )
        }
        _ => bail!("Unsupported platform provisioning action"),
    };
    r.projects.push(project.into());
    effects.push(format!(
        "Executable SHA256 {} {}",
        step.program,
        platform::hash_file(Path::new(&step.program))?
    ));
    let op = Operation {
        id: id(),
        input,
        resource: r,
        fingerprint: None,
        risk: "high".into(),
        requirements,
        effects,
        steps: vec![step],
        status: "planned".into(),
        execution_status: "not_started".into(),
        verification_status: "not_started".into(),
        registration_status: "not_started".into(),
        created_at: now(),
        updated_at: now(),
        error: None,
        attempts: 0,
        reliability: Default::default(),
    };
    s.save("operations", &op.id, &op)?;
    Ok(op)
}
fn valid_name(name: &str) -> Result<()> {
    anyhow::ensure!(
        !name.is_empty()
            && name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_')),
        "Platform name must contain ASCII letters, digits, hyphen or underscore"
    );
    Ok(())
}
pub fn verify_operation(op: &Operation) -> Result<Resource> {
    let mut r = op.resource.clone();
    if r.kind == "wsl" {
        let names = call(
            r.metadata["manager"].as_str().context("Missing manager")?,
            &["--list".into(), "--quiet".into()],
        )?;
        anyhow::ensure!(
            names.lines().any(|n| n.trim() == r.name),
            "Distribution not registered after command"
        );
    } else {
        let (exe, host) = docker()?;
        anyhow::ensure!(r.metadata["host"] == host, "Docker endpoint changed");
        let object = if op.input.action == "pull-image" {
            r.metadata["source"].as_str()
        } else if op.input.action == "create-container" {
            r.metadata["object_name"].as_str()
        } else {
            r.metadata["object"].as_str()
        }
        .context("Missing platform identity")?;
        let kind = if r.kind == "docker-image" {
            "image"
        } else {
            "container"
        };
        let objects: Value = serde_json::from_str(&call(
            &exe,
            &docker_args(&host, &[kind, "inspect", object]),
        )?)?;
        let obj = &objects[0];
        if op.input.action == "create-container" {
            let expected = r.metadata["creation_token"].as_str().context(
                "Container creation identity missing; recovery must not trust reused names",
            )?;
            anyhow::ensure!(
                obj["Config"]["Labels"]["airoot.creation"] == expected
                    && obj["Image"] == r.metadata["image"],
                "Container creation identity or image changed"
            );
        }
        let object = obj["Id"].as_str().context("Missing inspected object ID")?;
        if op.input.action == "stop" {
            anyhow::ensure!(obj["State"]["Running"] == false, "Container did not stop");
        }
        if op.input.action == "start" {
            anyhow::ensure!(obj["State"]["Running"] == true, "Container did not start");
        }
        r.path = format!("{host}/{}/{object}", r.kind);
        r.metadata["object"] = json!(object);
        r.metadata["running"] = obj["State"]["Running"].clone();
        r.metadata["image"] = obj["Image"].clone();
    }
    if r.kind == "wsl" {
        let identity = platform::wsl_identity(&r.name)?;
        if op.input.action == "import-wsl" {
            let target = op
                .input
                .target
                .as_ref()
                .context("WSL import target missing")?;
            anyhow::ensure!(
                platform::canonical(Path::new(
                    identity["base_path"]
                        .as_str()
                        .context("WSL storage identity missing")?
                ))? == platform::canonical(target)?,
                "WSL storage instance changed"
            );
        } else if let Some(expected) = r.metadata.get("identity") {
            anyhow::ensure!(expected == &identity, "WSL registration identity changed");
        }
        r.metadata["identity"] = identity;
    }
    r.status = "available".into();
    Ok(r)
}
