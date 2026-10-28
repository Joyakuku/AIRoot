use crate::{model::Resource, platform};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{fs, path::Path, time::Duration};
fn text(p: &Path) -> String {
    fs::read_to_string(p).unwrap_or_default()
}
fn version_line(s: &str, key: &str) -> Option<String> {
    s.lines().find_map(|l| {
        let (k, v) = l.split_once('=')?;
        (k.trim() == key).then(|| v.trim().trim_matches('"').to_owned())
    })
}
pub fn inspect(path: &Path) -> Result<Vec<Resource>> {
    let path = platform::canonical(path)?;
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let p = path.to_string_lossy();
    let mut out = vec![];
    if path.is_dir() {
        let (kind, entry) = if path.join("conda-meta/history").is_file() {
            ("conda", path.join("python.exe"))
        } else if path.join("pyvenv.cfg").is_file() {
            ("venv", path.join("Scripts/python.exe"))
        } else {
            ("", path.join("python.exe"))
        };
        if !kind.is_empty() {
            let mut r = Resource::new("env", kind, &name, &p);
            r.entry = entry.is_file().then(|| entry.to_string_lossy().into());
            r.version = version_line(&text(&path.join("pyvenv.cfg")), "version");
            r.status = if r.entry.is_some() {
                "unverified"
            } else {
                "unavailable"
            }
            .into();
            r.metadata = json!({"identity_verified":true,"evidence":if kind=="conda"{"conda-meta/history"}else{"pyvenv.cfg"}});
            if kind == "conda" {
                let mut deps = vec![];
                if let Ok(items) = fs::read_dir(path.join("conda-meta")) {
                    for item in items.flatten().take(10000) {
                        if item.path().extension().is_some_and(|e| e == "json") {
                            if let Ok(v) = serde_json::from_slice::<Value>(
                                &fs::read(item.path()).unwrap_or_default(),
                            ) {
                                deps.push(json!({"name":v["name"],"version":v["version"]}));
                            }
                        }
                    }
                }
                r.dependencies = json!(deps);
            }
            out.push(r);
        }
        if path.join("node.exe").is_file() {
            let mut r = Resource::new("env", "node", &name, &p);
            r.entry = Some(path.join("node.exe").to_string_lossy().into());
            out.push(r);
        }
        if path.join("bin/java.exe").is_file() && path.join("release").is_file() {
            let mut r = Resource::new("env", "java", &name, &p);
            r.entry = Some(path.join("bin/java.exe").to_string_lossy().into());
            r.version = version_line(&text(&path.join("release")), "JAVA_VERSION");
            out.push(r);
        }
        if kind.is_empty()
            && path.join("python.exe").is_file()
            && !path.join("conda-meta").exists()
            && !path
                .parent()
                .is_some_and(|p| p.join("pyvenv.cfg").is_file() || p.join("conda-meta").is_dir())
        {
            let mut r = Resource::new("env", "python", &name, &p);
            r.entry = Some(path.join("python.exe").to_string_lossy().into());
            out.push(r);
        }
        for exe in [
            "ffmpeg.exe",
            "ffprobe.exe",
            "blender.exe",
            "git.exe",
            "rg.exe",
            "fd.exe",
            "uv.exe",
            "cmake.exe",
            "cargo.exe",
            "rustc.exe",
            "docker.exe",
        ] {
            let file = path.join(exe);
            if file.is_file() {
                let mut r = Resource::new(
                    "tool",
                    "cli",
                    exe.trim_end_matches(".exe"),
                    &file.to_string_lossy(),
                );
                r.entry = Some(r.path.clone());
                out.push(r);
            }
        }
    }
    Ok(out)
}
pub fn verify(r: Resource) -> Result<Resource> {
    verify_with(r, |_| Ok(()))
}
pub fn verify_with(
    mut r: Resource,
    mut on_start: impl FnMut(u32) -> Result<()>,
) -> Result<Resource> {
    if r.scope != "host"
        || matches!(
            r.kind.as_str(),
            "wsl" | "docker-image" | "docker-container" | "docker-runtime"
        )
    {
        bail!("Platform resources require adapter verification; host execution refused");
    }
    let path = Path::new(&r.path);
    if !path.exists() {
        r.status = "unavailable".into();
        return Ok(r);
    }
    if r.kind == "mcp-connector" {
        anyhow::ensure!(
            r.metadata["transport"] == "stdio",
            "Only installed local stdio MCP connections are supported"
        );
        let args = r.metadata["args"]
            .as_array()
            .context("MCP args array required")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .context("MCP argument must be a string")
            })
            .collect::<Result<Vec<_>>>()?;
        let entry = r
            .entry
            .as_deref()
            .context("MCP installed command missing")?;
        r.metadata["connection"] = crate::mcp::probe(entry, &args, &mut on_start)?;
        r.metadata["verified_entry_sha256"] = json!(platform::hash_file(Path::new(entry))?);
        r.status = "available".into();
        if r.kind == "conda" && r.status == "available" {
            let mut dependencies = vec![];
            let mut complete = true;
            for item in fs::read_dir(path.join("conda-meta"))? {
                let item = item?;
                if item.path().extension().is_some_and(|x| x == "json") {
                    match fs::read(item.path())
                        .ok()
                        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                    {
                        Some(v) if v["name"].is_string() && v["version"].is_string() => {
                            dependencies.push(json!({"name":v["name"],"version":v["version"]}))
                        }
                        _ => complete = false,
                    }
                }
            }
            r.dependencies = json!(dependencies);
            r.metadata["dependency_status"] = json!(if complete { "verified" } else { "partial" });
        }
        r.updated_at = crate::model::now();
        return Ok(r);
    }
    if r.kind == "script" {
        anyhow::ensure!(
            path.is_file() && r.metadata["reusable"] == true && r.parent_id.is_some(),
            "Script is missing a reusable declaration or interpreter association"
        );
        r.metadata["verified_script_sha256"] = json!(platform::hash_file(path)?);
    }
    let entry = r
        .entry
        .as_ref()
        .context("Resource has no executable entry")?;
    match fs::metadata(entry) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            r.status = "unavailable".into();
            return Ok(r);
        }
        Err(e) => return Err(e.into()),
        Ok(m) if !m.is_file() => {
            r.status = "unavailable".into();
            return Ok(r);
        }
        _ => {}
    }
    let args = if r.kind == "java" || matches!(r.name.as_str(), "ffmpeg" | "ffprobe") {
        vec!["-version".into()]
    } else {
        vec!["--version".into()]
    };
    let result = platform::run(entry, &args, Duration::from_secs(10), &mut on_start)?;
    anyhow::ensure!(
        !result.descendants_active,
        "Verification left child processes; usage protection must remain active"
    );
    r.status = if result.code == 0 {
        "available"
    } else {
        "unavailable"
    }
    .into();
    let output = if result.stdout.trim().is_empty() {
        result.stderr
    } else {
        result.stdout
    };
    r.version = output.lines().next().map(|s| s.chars().take(200).collect());
    r.metadata["verified_entry_sha256"] = json!(platform::hash_file(Path::new(entry))?);
    if r.kind == "venv" && r.status == "available" {
        let packages = platform::run(
            entry,
            &[
                "-m".into(),
                "pip".into(),
                "list".into(),
                "--format=json".into(),
                "--disable-pip-version-check".into(),
            ],
            Duration::from_secs(20),
            &mut on_start,
        )?;
        anyhow::ensure!(
            !packages.descendants_active,
            "Dependency query left child processes; retain usage protection"
        );
        if packages.code == 0 && !packages.output_truncated {
            r.dependencies =
                serde_json::from_str(&packages.stdout).context("Invalid pip dependency output")?;
            r.metadata["dependency_status"] = json!("verified");
        } else {
            r.metadata["dependency_status"] = json!("unavailable");
        }
    }
    if r.kind == "conda" && r.status == "available" {
        let mut dependencies = vec![];
        let mut complete = true;
        for item in fs::read_dir(path.join("conda-meta"))? {
            let item = item?;
            if item.path().extension().is_some_and(|x| x == "json") {
                match fs::read(item.path())
                    .ok()
                    .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                {
                    Some(v) if v["name"].is_string() && v["version"].is_string() => {
                        dependencies.push(json!({"name":v["name"],"version":v["version"]}))
                    }
                    _ => complete = false,
                }
            }
        }
        r.dependencies = json!(dependencies);
        r.metadata["dependency_status"] = json!(if complete { "verified" } else { "partial" });
    }
    r.updated_at = crate::model::now();
    Ok(r)
}
pub fn manager_candidates() -> Vec<std::path::PathBuf> {
    let mut roots = vec![];
    if let Some(home) = std::env::var_os("USERPROFILE") {
        let home = std::path::PathBuf::from(home);
        let envs = text(&home.join(".conda/environments.txt"));
        for line in envs.lines() {
            let p = std::path::PathBuf::from(line.trim());
            if p.is_absolute() && p.is_dir() {
                roots.push(p);
            }
        }
    }
    if let Some(paths) = std::env::var_os("PATH") {
        for p in std::env::split_paths(&paths) {
            if p.is_absolute() && p.is_dir() {
                roots.push(p);
            }
        }
    }
    roots
}
pub fn capabilities() -> Value {
    json!([
     {"kind":"venv","discovery":true,"operations":["create","install-packages","upgrade-packages","remove-packages","delete"],"requires":"Explicit Python interpreter; updates requiring compatibility changes create a new environment"},
     {"kind":"conda","discovery":true,"operations":["create","install-packages","upgrade-packages","remove-packages","delete"],"requires":"Explicit conda executable and configured prefix"},
     {"kind":"node","discovery":true,"operations":["install-portable","delete-portable"],"fallback":"Agent installs a verified parallel version then registry add"},
     {"kind":"java","discovery":true,"operations":["install-portable","delete-portable"],"fallback":"Agent installs a verified parallel version then registry add"},
     {"kind":"python","discovery":true,"operations":["install-portable","delete-portable"],"fallback":"bootstrap official pinned Python installer, or Agent install then registry add"},
     {"kind":"cli","discovery":true,"operations":["install-portable","delete-portable"],"fallback":"Agent installs tool then registry add"},
     {"kind":"script","discovery":"explicit reusable registration with parent interpreter","operations":["verify","run"]},
     {"kind":"mcp-connector","discovery":"explicit installed stdio connector with parent resource","operations":["verify-handshake"],"protocol_versions":["2024-11-05"],"unsupported":["remote HTTP","live Agent session relay"]},
     {"kind":"wsl","discovery":"platform-discover wsl","operations":["import-wsl"],"unsupported":["automatic unregister; WSL unregister addresses a reusable distribution name and cannot bind the registered identity atomically"],"status":"Discovery/internal lookup tested; import tested in isolation; system enablement remains external high-risk preparation"},
     {"kind":"docker-runtime","discovery":"platform-discover docker","operations":["pull-image","create-container","start","stop","container-delete-with-backup","image-delete"],"status":"local named-pipe context only; live engine validation pending"}
    ])
}
