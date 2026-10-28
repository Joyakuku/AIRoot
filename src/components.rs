use crate::{
    platform,
    store::{Store, atomic_json, read_json},
};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};
pub fn executable(s: &Store, name: &str) -> Result<PathBuf> {
    let manifest: Value = read_json(&s.config.component_dir.join("manifest.json"))?;
    let path = verified_file(&manifest, &s.config.component_dir, name)?;
    if let Some(dependencies) = manifest["components"]
        .as_array()
        .and_then(|items| items.iter().find(|c| c["name"] == name))
        .and_then(|record| record["dependencies"].as_array())
    {
        for dependency in dependencies {
            verified_file(
                &manifest,
                &s.config.component_dir,
                dependency
                    .as_str()
                    .context("Invalid component dependency")?,
            )?;
        }
    }
    Ok(path)
}
fn verified_file(manifest: &Value, component_dir: &Path, name: &str) -> Result<PathBuf> {
    let record = manifest["components"]
        .as_array()
        .context("Invalid components manifest")?
        .iter()
        .find(|c| c["name"] == name)
        .context("Missing mandatory component")?;
    let file = record["file"]
        .as_str()
        .context("Invalid component filename")?;
    anyhow::ensure!(
        Path::new(file).components().count() == 1,
        "Invalid component path"
    );
    let path = component_dir.join(file);
    let expected = record["sha256"].as_str().context("Missing pinned SHA256")?;
    anyhow::ensure!(
        platform::hash_file(&path)? == expected,
        "Component hash mismatch: {name}"
    );
    Ok(path)
}
pub fn status(s: &Store) -> Value {
    json!(
        ["Everything", "es", "PET", "fd"]
            .iter()
            .map(|name| match executable(s, name) {
                Ok(p) => json!({"name":name,"status":"verified","path":p}),
                Err(e) => json!({"name":name,"status":"unavailable","error":e.to_string()}),
            })
            .collect::<Vec<_>>()
    )
}
pub fn instance(s: &Store) -> Result<String> {
    Ok(format!(
        "AIRoot-{}",
        &platform::digest(&s.root.to_string_lossy().to_lowercase())?[..16]
    ))
}
pub fn start(s: &Store, roots: &[PathBuf]) -> Result<Value> {
    use fs2::FileExt;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(s.root.join("components/index.lock"))?;
    lock.try_lock_exclusive()
        .context("Embedded index lifecycle change already running")?;
    let exe = executable(s, "Everything")?;
    let name = instance(s)?;
    let root = s.root.join("components/everything");
    fs::create_dir_all(&root)?;
    let disks: Vec<_> = platform::local_drives()
        .iter()
        .filter_map(|p| platform::canonical(p).ok())
        .collect();
    let roots: Vec<_> = roots
        .iter()
        .map(|r| {
            let p = platform::canonical_unlinked(r)?;
            anyhow::ensure!(
                disks.iter().any(|d| platform::path_is_under(&p, d)),
                "Index only local fixed disks"
            );
            Ok(p)
        })
        .collect::<Result<_>>()?;
    anyhow::ensure!(!roots.is_empty(), "Explicit index roots required");
    let state = root.join("instance.json");
    if state.exists() {
        let old: Value = read_json(&state)?;
        if let Some(pid) = old["pid"].as_u64() {
            if platform::process_stamp(pid as u32)? == old["process_stamp"].as_u64() {
                anyhow::ensure!(
                    old["roots"] == json!(roots),
                    "INDEX_SCOPE_CHANGED: stop embedded index before changing roots"
                );
                return Ok(old);
            }
        }
    }
    let native = native_service(s)?;
    let mut volumes = String::new();
    if native {
        for filesystem in ["NTFS", "ReFS"] {
            let selected: Vec<_> = disks
                .iter()
                .filter_map(|disk| {
                    platform::native_volume_details(disk)
                        .ok()
                        .filter(|(_, fs)| fs == filesystem)
                        .map(|(guid, _)| (disk, guid))
                })
                .collect();
            let prefix = filesystem.to_ascii_lowercase();
            let quote = |text: &str| format!("\"{}\"", text.replace('\\', "\\\\"));
            let guids = selected
                .iter()
                .map(|(_, guid)| quote(guid))
                .collect::<Vec<_>>()
                .join(",");
            let paths = selected
                .iter()
                .map(|(path, _)| quote(path.to_string_lossy().trim_end_matches('\\')))
                .collect::<Vec<_>>()
                .join(",");
            let ones = vec!["1"; selected.len()].join(",");
            let empty = vec!["\"\""; selected.len()].join(",");
            volumes.push_str(&format!("{prefix}_volume_guids={guids}\r\n{prefix}_volume_paths={paths}\r\n{prefix}_volume_roots={empty}\r\n{prefix}_volume_includes={ones}\r\n{prefix}_volume_monitors={ones}\r\n"));
        }
    }
    let config = root.join("Everything.ini");
    let folders = roots
        .iter()
        .filter(|p| !native || !platform::native_index_volume(p))
        .map(|p| {
            format!(
                "\"{}\"",
                p.to_string_lossy()
                    .replace('\\', "\\\\")
                    .replace('"', "\\\"")
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    fs::write(
        &config,
        format!(
            "[Everything]\r\ninstance_name={name}\r\nservice_pipe_name=\\\\.\\pipe\\{name}-service\r\napp_data=0\r\nrun_as_admin=0\r\nrun_in_background=1\r\nshow_tray_icon=0\r\ncheck_for_updates_on_startup=0\r\nauto_include_fixed_volumes=0\r\nauto_include_removable_volumes=0\r\nauto_include_fixed_refs_volumes=0\r\nauto_include_removable_refs_volumes=0\r\n{volumes}folders={folders}\r\n"
        ),
    )?;
    let args = vec![
        "-instance".into(),
        name.clone(),
        "-startup".into(),
        "-config".into(),
        config.to_string_lossy().into(),
        "-db".into(),
        root.join("Everything.db").to_string_lossy().into(),
    ];
    let mut c = platform::command(&exe.to_string_lossy(), &args);
    c.stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    let mut child = c.spawn()?;
    let mut ready = false;
    for _ in 0..100 {
        anyhow::ensure!(
            child.try_wait()?.is_none(),
            "Embedded Everything exited before IPC became ready"
        );
        if ipc_window(&name, child.id())? {
            ready = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    if !ready {
        let _ = child.kill();
        let _ = child.wait();
        bail!("Embedded Everything IPC did not become ready");
    }

    let value = json!({"instance":name,"pid":child.id(),"process_stamp":platform::process_stamp(child.id())?,"roots":roots,"mode":if native {"native-index"} else {"folder-index"},"native_ntfs":native,"service":format!("Everything ({name})")});
    atomic_json(&state, &value)?;
    Ok(value)
}
pub fn stop(s: &Store) -> Result<Value> {
    use fs2::FileExt;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(s.root.join("components/index.lock"))?;
    lock.try_lock_exclusive()
        .context("Embedded index lifecycle change already running")?;
    let state = s.root.join("components/everything/instance.json");
    let previous: Option<Value> = if state.exists() {
        Some(read_json(&state)?)
    } else {
        None
    };
    let exe = executable(s, "Everything")?;
    let name = instance(s)?;
    let out = platform::run(
        &exe.to_string_lossy(),
        &["-instance".into(), name, "-exit".into()],
        Duration::from_secs(10),
        |_| Ok(()),
    )?;
    anyhow::ensure!(out.code == 0, "Embedded Everything stop failed");
    if let Some(old) = previous {
        if let (Some(pid), Some(stamp)) = (old["pid"].as_u64(), old["process_stamp"].as_u64()) {
            let until = std::time::Instant::now() + Duration::from_secs(30);
            while platform::process_stamp(pid as u32)? == Some(stamp) {
                anyhow::ensure!(
                    std::time::Instant::now() < until,
                    "Embedded index is still exiting; retry after it saves its database"
                );
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        fs::remove_file(&state)?;
    }
    Ok(json!({"stopped":true}))
}
#[derive(Debug)]
pub struct Candidates {
    pub paths: Vec<PathBuf>,
    pub truncated: bool,
    pub reason: Option<String>,
}
pub fn candidates(s: &Store, name: &str, roots: &[PathBuf]) -> Result<Candidates> {
    let exe = executable(s, name)?;
    let mut args=match name{
 "PET"=>{let cache=s.root.join("components/pet");fs::create_dir_all(&cache)?;let mut args=vec!["find".into(),"--workspace".into(),"--list".into(),"--cache-directory".into(),cache.to_string_lossy().into()];args.extend(roots.iter().map(|p|p.to_string_lossy().into()));args},
 "fd"=>{let mut args=vec!["--absolute-path".into(),"--hidden".into(),"--no-ignore".into(),"--type".into(),"f".into(),"--max-results".into(),"10000".into(),"^(pyvenv\\.cfg|python\\.exe|node\\.exe|java\\.exe|ffmpeg\\.exe|ffprobe\\.exe|blender\\.exe|git\\.exe|rg\\.exe|fd\\.exe|uv\\.exe|cmake\\.exe|cargo\\.exe|rustc\\.exe|docker\\.exe|history)$".into()];args.extend(roots.iter().map(|p|p.to_string_lossy().into()));args},
 "es"=>vec!["-instance".into(),instance(s)?,"-cp".into(),"65001".into(),"-timeout".into(),"25000".into(),"-n".into(),"10000".into(),"-full-path-and-name".into(),"pyvenv.cfg|python.exe|node.exe|java.exe|ffmpeg.exe|ffprobe.exe|blender.exe|git.exe|rg.exe|fd.exe|uv.exe|cmake.exe|cargo.exe|rustc.exe|docker.exe|history".into()],
 _=>bail!("No candidate provider for {name}")};
    if name == "es" {
        anyhow::ensure!(roots.len() == 1, "Index queries require one explicit root");
        args.extend(["-path".into(), roots[0].to_string_lossy().into()]);
    }
    let out = platform::discovery_run(&exe.to_string_lossy(), &args, Duration::from_secs(30))?;
    let mut out = out;
    if (out.output_truncated || out.timed_out) && !out.stdout.ends_with('\n') {
        out.stdout
            .truncate(out.stdout.rfind('\n').map_or(0, |n| n + 1));
    }
    let truncated = out.timed_out
        || out.output_truncated
        || (name != "PET" && out.stdout.lines().count() >= 10000);
    let reason = if out.code != 0 {
        Some(format!(
            "Provider exit {}; directory fallback required",
            out.code
        ))
    } else if truncated {
        Some("Candidate/output limit reached; directory fallback required".into())
    } else {
        None
    };
    let mut paths = vec![];
    for line in out.stdout.lines() {
        let path = if name == "PET" {
            if let Some((key, v)) = line.split_once(':') {
                if key.trim() == "Prefix" {
                    Some(PathBuf::from(v.trim()))
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            let p = PathBuf::from(line.trim());
            p.parent().map(Path::to_path_buf)
        };
        if let Some(mut path) = path {
            if path
                .file_name()
                .is_some_and(|n| n == "conda-meta" || n == "Scripts" || n == "bin")
            {
                if let Some(parent) = path.parent() {
                    path = parent.into();
                }
            }
            if let Ok(path) = platform::canonical_unlinked(&path) {
                if roots.iter().any(|r| platform::path_is_under(&path, r)) {
                    paths.push(path);
                }
            }
        }
    }
    paths.sort();
    paths.dedup();
    Ok(Candidates {
        paths,
        truncated,
        reason,
    })
}

pub fn service_plan(s: &Store) -> Result<Value> {
    let exe = executable(s, "Everything")?;
    let name = instance(s)?;
    Ok(
        json!({"risk":"high","requirements":["administrator-required","system-service-change"],"service_name":format!("Everything ({name})"),"program":exe,"install_args":["-instance",name,"-install-service","-install-service-pipe-name",format!(r"\\.\pipe\{name}-service")],"uninstall_args":["-instance",name,"-uninstall-service"],"installed_and_owned":native_service_installed(s)?,"note":"Execute only in the Agent-approved elevated process. Embedded index must be stopped before changing mode. No external Everything service is reused."}),
    )
}
#[cfg(windows)]
fn service_state(s: &Store, require_running: bool) -> Result<bool> {
    use windows_sys::Win32::System::Services::*;
    struct Handle(SC_HANDLE);
    impl Drop for Handle {
        fn drop(&mut self) {
            unsafe {
                CloseServiceHandle(self.0);
            }
        }
    }
    let name = format!("Everything ({})", instance(s)?);
    let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
    unsafe {
        let scm = OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT);
        if scm.is_null() {
            return Ok(false);
        }
        let scm = Handle(scm);
        let svc = OpenServiceW(
            scm.0,
            wide.as_ptr(),
            SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS,
        );
        if svc.is_null() {
            return Ok(false);
        }
        let svc = Handle(svc);
        let mut needed = 0;
        QueryServiceConfigW(svc.0, std::ptr::null_mut(), 0, &mut needed);
        anyhow::ensure!(
            needed > 0 && needed < 1024 * 1024,
            "Unexpected service configuration size"
        );
        let mut buffer = vec![0usize; (needed as usize).div_ceil(std::mem::size_of::<usize>())];
        let config = buffer.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
        anyhow::ensure!(
            QueryServiceConfigW(svc.0, config, needed, &mut needed) != 0,
            "Cannot inspect embedded service"
        );
        let ptr = (*config).lpBinaryPathName;
        let mut length = 0;
        while *ptr.add(length) != 0 {
            length += 1;
        }
        let binary = String::from_utf16_lossy(std::slice::from_raw_parts(ptr, length));
        let expected = executable(s, "Everything")?
            .to_string_lossy()
            .to_lowercase();
        let binary = binary.to_lowercase();
        anyhow::ensure!(
            binary.starts_with(&format!("\"{expected}\" "))
                || binary.starts_with(&format!("{expected} ")),
            "Service name collision: existing service belongs to another installation"
        );
        let mut status = SERVICE_STATUS {
            dwServiceType: 0,
            dwCurrentState: 0,
            dwControlsAccepted: 0,
            dwWin32ExitCode: 0,
            dwServiceSpecificExitCode: 0,
            dwCheckPoint: 0,
            dwWaitHint: 0,
        };
        anyhow::ensure!(
            QueryServiceStatus(svc.0, &mut status) != 0,
            "Cannot inspect service state"
        );
        Ok(!require_running || status.dwCurrentState == SERVICE_RUNNING)
    }
}
#[cfg(not(windows))]
fn service_state(_: &Store, _: bool) -> Result<bool> {
    Ok(false)
}

pub fn native_service(s: &Store) -> Result<bool> {
    service_state(s, true)
}
pub fn native_service_installed(s: &Store) -> Result<bool> {
    service_state(s, false)
}

#[cfg(windows)]
fn ipc_window(instance: &str, pid: u32) -> Result<bool> {
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let class: Vec<u16> = format!("EVERYTHING_TASKBAR_NOTIFICATION_({instance})")
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        let window = FindWindowW(class.as_ptr(), std::ptr::null());
        if window.is_null() {
            return Ok(false);
        }
        let mut owner = 0;
        GetWindowThreadProcessId(window, &mut owner);
        Ok(owner == pid)
    }
}
#[cfg(not(windows))]
fn ipc_window(_: &str, _: u32) -> Result<bool> {
    Ok(false)
}
