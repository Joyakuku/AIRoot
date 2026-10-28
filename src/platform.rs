use anyhow::{Context, Result, bail};
use sha2::{Digest, Sha256};
use std::{
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

pub fn hash_file(path: &Path) -> Result<String> {
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut b = [0u8; 65536];
    loop {
        let n = f.read(&mut b)?;
        if n == 0 {
            break;
        }
        h.update(&b[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}
pub fn digest(value: &impl serde::Serialize) -> Result<String> {
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(value)?)))
}
pub fn uptime_millis() -> Result<u64> {
    #[cfg(windows)]
    {
        Ok(unsafe { windows_sys::Win32::System::SystemInformation::GetTickCount64() })
    }
    #[cfg(not(windows))]
    {
        bail!("System uptime is available only on Windows")
    }
}
/// Stable UTC boot timestamp; unlike uptime ordering this also detects a later,
/// longer-running boot. Failure is conservative: never infer a restart.
pub fn boot_stamp() -> Result<u64> {
    #[cfg(windows)]
    {
        let out = run_wait_tree(
            "powershell.exe",
            &[
                "-NoProfile".into(), "-NonInteractive".into(), "-Command".into(),
                "$ErrorActionPreference='Stop'; (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks".into(),
            ],
            Duration::from_secs(30),
            |_| Ok(()),
        )?;
        anyhow::ensure!(
            out.code == 0 && !out.output_truncated && !out.descendants_active,
            "Windows boot identity unavailable"
        );
        let stamp: u64 = out
            .stdout
            .trim_start_matches('\u{feff}')
            .trim()
            .parse()
            .context("Invalid Windows boot identity")?;
        anyhow::ensure!(stamp > 0, "Invalid Windows boot identity");
        Ok(stamp)
    }
    #[cfg(not(windows))]
    {
        bail!("Windows boot identity is available only on Windows")
    }
}
pub fn canonical(path: &Path) -> Result<PathBuf> {
    let p = std::fs::canonicalize(path)
        .with_context(|| format!("Cannot resolve {}", path.display()))?;
    #[cfg(windows)]
    {
        let text = p.to_string_lossy();
        if let Some(local) = text.strip_prefix(r"\\?\") {
            if !local.starts_with("UNC\\") {
                return Ok(PathBuf::from(local));
            }
        }
    }
    Ok(p)
}
pub fn is_link(path: &Path) -> Result<bool> {
    let m = std::fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        Ok(m.file_attributes() & 0x400 != 0)
    }
    #[cfg(not(windows))]
    {
        Ok(m.file_type().is_symlink())
    }
}
/// Resolve a directory only after checking the original spelling's ancestor chain.
/// Canonicalization alone would erase symlink/junction provenance.
pub fn canonical_unlinked(path: &Path) -> Result<PathBuf> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    anyhow::ensure!(absolute.is_absolute(), "Cannot resolve absolute scan path");
    for ancestor in absolute.ancestors() {
        anyhow::ensure!(
            !is_link(ancestor).with_context(|| format!("Cannot inspect {}", ancestor.display()))?,
            "Linked scan path excluded: {}",
            ancestor.display()
        );
    }
    canonical(&absolute)
}

/// Resolve a path without links and expand Windows 8.3 components where possible.
pub fn canonical_long_unlinked(path: &Path) -> Result<PathBuf> {
    let resolved = canonical_unlinked(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        let wide: Vec<u16> = resolved.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut buffer = vec![0u16; 32768];
        let len = unsafe {
            windows_sys::Win32::Storage::FileSystem::GetLongPathNameW(
                wide.as_ptr(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
            )
        };
        anyhow::ensure!(
            len > 0 && (len as usize) < buffer.len(),
            "Cannot expand path aliases"
        );
        Ok(std::ffi::OsString::from_wide(&buffer[..len as usize]).into())
    }
    #[cfg(not(windows))]
    {
        Ok(resolved)
    }
}

/// Compare resolved paths using the platform's existing path-case convention.
pub fn path_is_under(path: &Path, root: &Path) -> bool {
    #[cfg(windows)]
    {
        windows_path_is_under(path, root)
    }
    #[cfg(not(windows))]
    {
        path.starts_with(root)
    }
}

pub fn checked_target(path: &Path, root: &Path) -> Result<PathBuf> {
    anyhow::ensure!(path.is_absolute(), "Target must be absolute");
    let root = canonical(root)?;
    let parent = canonical(path.parent().context("Target needs a parent directory")?)?;
    anyhow::ensure!(
        parent.starts_with(&root),
        "Target is outside configured root"
    );
    let target = parent.join(path.file_name().context("Target needs a name")?);
    anyhow::ensure!(
        target != root,
        "Refusing to operate on resource root itself"
    );
    if target.exists() {
        anyhow::ensure!(!is_link(&target)?, "Refusing linked target");
        let actual = canonical(&target)?;
        anyhow::ensure!(
            actual.starts_with(&root) && actual != root,
            "Resolved target escaped root"
        );
    }
    Ok(target)
}
pub fn executable(name: &str) -> Option<PathBuf> {
    let p = Path::new(name);
    if p.is_absolute() {
        return p.is_file().then(|| p.to_path_buf());
    }
    std::env::var_os("PATH").and_then(|v| {
        std::env::split_paths(&v)
            .flat_map(|p| [p.join(name), p.join(format!("{name}.exe"))])
            .find(|p| p.is_file())
    })
}
pub fn command(program: &str, args: &[String]) -> Command {
    let mut c = Command::new(program);
    if Path::new(program)
        .file_stem()
        .is_some_and(|n| n.to_string_lossy().eq_ignore_ascii_case("docker"))
    {
        c.env_remove("DOCKER_HOST").env_remove("DOCKER_CONTEXT");
    }
    c.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x08000000);
    }
    c
}
fn bounded_read(mut r: impl Read) -> (Vec<u8>, bool) {
    let mut truncated = false;
    let mut out = vec![];
    let mut b = [0u8; 8192];
    while let Ok(n) = r.read(&mut b) {
        if n == 0 {
            break;
        }
        let keep = n.min((2 * 1024 * 1024usize).saturating_sub(out.len()));
        truncated |= keep < n;
        out.extend_from_slice(&b[..keep]);
    }
    (out, truncated)
}
#[derive(Debug)]
pub struct ProcessResult {
    pub timed_out: bool,
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
    pub descendants_active: bool,
    pub output_truncated: bool,
}
pub fn run(
    program: &str,
    args: &[String],
    timeout: Duration,
    on_start: impl FnOnce(u32) -> Result<()>,
) -> Result<ProcessResult> {
    run_with_env(
        program,
        args,
        timeout,
        &std::collections::BTreeMap::new(),
        on_start,
    )
}
pub fn run_with_env(
    program: &str,
    args: &[String],
    timeout: Duration,
    environment: &std::collections::BTreeMap<String, String>,
    on_start: impl FnOnce(u32) -> Result<()>,
) -> Result<ProcessResult> {
    run_process(program, args, timeout, environment, on_start, false, false)
}
/// Vendor uninstallers may hand off to an owned child. Wait within the original
/// deadline, retaining protection if the captured job still has active processes.
pub fn run_wait_tree(
    program: &str,
    args: &[String],
    timeout: Duration,
    on_start: impl FnOnce(u32) -> Result<()>,
) -> Result<ProcessResult> {
    run_process(
        program,
        args,
        timeout,
        &Default::default(),
        on_start,
        false,
        true,
    )
}
pub fn discovery_run(program: &str, args: &[String], timeout: Duration) -> Result<ProcessResult> {
    run_process(
        program,
        args,
        timeout,
        &Default::default(),
        |_| Ok(()),
        true,
        false,
    )
}
fn run_process(
    program: &str,
    args: &[String],
    timeout: Duration,
    environment: &std::collections::BTreeMap<String, String>,
    on_start: impl FnOnce(u32) -> Result<()>,
    preserve_timeout: bool,
    _wait_tree: bool,
) -> Result<ProcessResult> {
    let mut c = command(program, args);
    c.envs(environment);
    suspend_on_spawn(&mut c);
    let mut child = c
        .spawn()
        .with_context(|| format!("Failed to start {program}"))?;
    #[cfg(windows)]
    let job: Result<Job> = match Job::attach(&child) {
        Ok(j) => Ok(j),
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    };
    if let Err(e) = on_start(child.id()) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e);
    }
    if let Err(e) = resume_spawned(&child) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e);
    }
    let out = child.stdout.take().context("stdout unavailable")?;
    let err = child.stderr.take().context("stderr unavailable")?;
    let stdout = thread::spawn(|| bounded_read(out));
    let stderr = thread::spawn(|| bounded_read(err));
    let start = Instant::now();
    let mut timed_out = false;
    let status = loop {
        if let Some(s) = child.try_wait()? {
            break s;
        }
        if start.elapsed() > timeout {
            #[cfg(windows)]
            if let Ok(j) = &job {
                j.terminate();
            }
            let _ = child.kill();
            let status = child.wait()?;
            if !preserve_timeout {
                bail!("Process timed out; verify resources and usage journal before retry");
            }
            timed_out = true;
            break status;
        }
        thread::sleep(Duration::from_millis(50));
    };
    #[cfg(windows)]
    if let Ok(j) = &job {
        let grace = if _wait_tree {
            start + timeout
        } else {
            Instant::now() + Duration::from_secs(1)
        };
        while j.active().unwrap_or(1) > 0 && Instant::now() < grace {
            thread::sleep(Duration::from_millis(20));
        }
    }
    #[cfg(windows)]
    let active = job
        .as_ref()
        .map(|j| j.active().unwrap_or(1) > 0)
        .unwrap_or(true);
    #[cfg(not(windows))]
    let active = false;
    // Descendants may inherit pipe handles: do not wait indefinitely for their output.
    if active && (!stdout.is_finished() || !stderr.is_finished()) {
        return Ok(ProcessResult {
            timed_out,
            code: status.code().unwrap_or(-1),
            stdout: String::new(),
            stderr:
                "Descendant processes still running; output omitted and usage protection retained"
                    .into(),
            descendants_active: true,
            output_truncated: true,
        });
    }
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    Ok(ProcessResult {
        timed_out,
        code: status.code().unwrap_or(-1),
        stdout: decode_output(&out.0),
        stderr: decode_output(&err.0),
        output_truncated: out.1 || err.1,
        descendants_active: active,
    })
}
#[cfg(windows)]
pub(crate) struct Job(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Job {
    pub(crate) fn attach(child: &std::process::Child) -> Result<Self> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let name: Vec<u16> = process_job_name(child.id())?
                .encode_utf16()
                .chain(Some(0))
                .collect();
            let h = CreateJobObjectW(std::ptr::null(), name.as_ptr());
            if h.is_null() {
                bail!("Job allocation failed");
            }
            let j = Self(h);
            if AssignProcessToJobObject(h, child.as_raw_handle()) == 0 {
                bail!("Could not track descendant processes");
            }
            Ok(j)
        }
    }
    fn active(&self) -> Result<u32> {
        use windows_sys::Win32::System::JobObjects::*;
        let mut info = std::mem::MaybeUninit::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>::zeroed();
        unsafe {
            if QueryInformationJobObject(
                self.0,
                JobObjectBasicAccountingInformation,
                info.as_mut_ptr().cast(),
                std::mem::size_of_val(&info) as u32,
                std::ptr::null_mut(),
            ) == 0
            {
                bail!("Cannot query descendants");
            }
            Ok(info.assume_init().ActiveProcesses)
        }
    }
    pub(crate) fn terminate(&self) {
        unsafe {
            windows_sys::Win32::System::JobObjects::TerminateJobObject(self.0, 1);
        }
    }
}
#[cfg(windows)]
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
#[cfg(windows)]
pub fn process_stamp(pid: u32) -> Result<Option<u64>> {
    use windows_sys::Win32::{Foundation::*, System::Threading::*};
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            let e = GetLastError();
            if e == ERROR_INVALID_PARAMETER {
                return Ok(None);
            }
            bail!("Cannot determine process state: {e}");
        }
        let mut exit = 0;
        let mut start = std::mem::zeroed();
        let mut end = std::mem::zeroed();
        let mut kernel = std::mem::zeroed();
        let mut user = std::mem::zeroed();
        let ok = GetExitCodeProcess(h, &mut exit) != 0
            && GetProcessTimes(h, &mut start, &mut end, &mut kernel, &mut user) != 0;
        CloseHandle(h);
        if !ok {
            bail!("Cannot determine process identity");
        }
        if exit != 259 {
            return Ok(None);
        }
        Ok(Some(
            ((start.dwHighDateTime as u64) << 32) | start.dwLowDateTime as u64,
        ))
    }
}
#[cfg(not(windows))]
pub fn process_stamp(_pid: u32) -> Result<Option<u64>> {
    bail!("Process identity verification is Windows-only")
}
#[derive(serde::Serialize)]
pub struct DiskInfo {
    pub root: PathBuf,
    pub eligible: bool,
    pub reason: String,
}
#[cfg(windows)]
fn disk_eligibility(letter: char) -> Result<()> {
    use windows_sys::Win32::{
        Foundation::*,
        Storage::FileSystem::*,
        System::{IO::DeviceIoControl, Ioctl::*},
    };
    let name: Vec<u16> = format!(r"\\.\{letter}:")
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        let handle = CreateFileW(
            name.as_ptr(),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        );
        anyhow::ensure!(
            handle != INVALID_HANDLE_VALUE,
            "storage_identity_unavailable:{}",
            GetLastError()
        );
        let result = (|| -> Result<()> {
            let query = STORAGE_PROPERTY_QUERY {
                PropertyId: StorageDeviceProperty,
                QueryType: PropertyStandardQuery,
                ..Default::default()
            };
            let mut descriptor = STORAGE_DEVICE_DESCRIPTOR::default();
            let mut returned = 0;
            anyhow::ensure!(
                DeviceIoControl(
                    handle,
                    IOCTL_STORAGE_QUERY_PROPERTY,
                    &query as *const _ as *const _,
                    std::mem::size_of_val(&query) as u32,
                    &mut descriptor as *mut _ as *mut _,
                    std::mem::size_of_val(&descriptor) as u32,
                    &mut returned,
                    std::ptr::null_mut()
                ) != 0,
                "storage_identity_unavailable:{}",
                GetLastError()
            );
            anyhow::ensure!(
                !descriptor.RemovableMedia
                    && ![BusTypeUsb, BusType1394, BusTypeSd, BusTypeMmc, BusTypeiScsi]
                        .contains(&descriptor.BusType),
                "external_removable_or_network_storage"
            );
            let mut hotplug = STORAGE_HOTPLUG_INFO {
                Size: std::mem::size_of::<STORAGE_HOTPLUG_INFO>() as u32,
                ..Default::default()
            };
            anyhow::ensure!(
                DeviceIoControl(
                    handle,
                    IOCTL_STORAGE_GET_HOTPLUG_INFO,
                    std::ptr::null(),
                    0,
                    &mut hotplug as *mut _ as *mut _,
                    std::mem::size_of_val(&hotplug) as u32,
                    &mut returned,
                    std::ptr::null_mut()
                ) != 0,
                "hotplug_identity_unavailable:{}",
                GetLastError()
            );
            anyhow::ensure!(
                !hotplug.MediaRemovable && !hotplug.DeviceHotplug,
                "external_hotplug_storage"
            );
            Ok(())
        })();
        CloseHandle(handle);
        result
    }
}
#[cfg(windows)]
pub fn disk_inventory() -> Vec<DiskInfo> {
    use windows_sys::Win32::Storage::FileSystem::*;
    let mask = unsafe { GetLogicalDrives() };
    (0..26)
        .filter_map(|i| {
            if mask & (1 << i) == 0 {
                return None;
            }
            let letter = (b'A' + i) as char;
            let path = format!("{letter}:\\");
            let wide: Vec<u16> = path.encode_utf16().chain(Some(0)).collect();
            let result = if unsafe { GetDriveTypeW(wide.as_ptr()) } == 3 {
                disk_eligibility(letter)
            } else {
                Err(anyhow::anyhow!("not_a_local_fixed_disk"))
            };
            Some(DiskInfo {
                root: path.into(),
                eligible: result.is_ok(),
                reason: result
                    .err()
                    .map(|e| e.to_string())
                    .unwrap_or_else(|| "internal_fixed_storage".into()),
            })
        })
        .collect()
}
#[cfg(not(windows))]
pub fn disk_inventory() -> Vec<DiskInfo> {
    vec![]
}
pub fn local_drives() -> Vec<PathBuf> {
    disk_inventory()
        .into_iter()
        .filter(|d| d.eligible)
        .map(|d| d.root)
        .collect()
}
#[cfg(windows)]
pub fn spawn_daemon(exe: &Path, root: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::{Foundation::*, System::Threading::*};
    let app: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
    let line = format!(
        "\"{}\" --data-root \"{}\" daemon",
        exe.display(),
        root.display()
    );
    let mut line: Vec<u16> = line.encode_utf16().chain(Some(0)).collect();
    unsafe {
        let mut start: STARTUPINFOW = std::mem::zeroed();
        start.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
        let mut process: PROCESS_INFORMATION = std::mem::zeroed();
        // No inherited handles: PowerShell's capture pipe must close when the CLI exits.
        let ok = CreateProcessW(
            app.as_ptr(),
            line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            0,
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            std::ptr::null(),
            std::ptr::null(),
            &start,
            &mut process,
        );
        if ok == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        Ok(())
    }
}

fn decode_output(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xff, 0xfe]) || bytes.iter().take(80).filter(|&&b| b == 0).count() > 10 {
        let start = if bytes.starts_with(&[0xff, 0xfe]) {
            2
        } else {
            0
        };
        let words: Vec<u16> = bytes[start..]
            .chunks_exact(2)
            .map(|p| u16::from_le_bytes([p[0], p[1]]))
            .collect();
        String::from_utf16_lossy(&words)
    } else {
        String::from_utf8_lossy(bytes)
            .trim_matches('\u{feff}')
            .into()
    }
}
#[cfg(windows)]
pub fn processes_under(root: &Path) -> Result<Vec<u32>> {
    use windows_sys::Win32::{
        Foundation::*,
        System::{Diagnostics::ToolHelp::*, Threading::*},
    };
    let root = canonical(root)?;
    let windows_root = windows_directory()?;
    let mut found = vec![];
    let mut unknown = vec![];
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            bail!("Cannot inspect active processes");
        }
        let result = (|| -> Result<()> {
            let mut entry: PROCESSENTRY32W = std::mem::zeroed();
            entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
            let mut ok = Process32FirstW(snapshot, &mut entry);
            if ok == 0 {
                let error = GetLastError();
                anyhow::ensure!(
                    error == ERROR_NO_MORE_FILES,
                    "Cannot enumerate active processes: {error}"
                );
                return Ok(());
            }
            loop {
                let name_end = entry
                    .szExeFile
                    .iter()
                    .position(|c| *c == 0)
                    .unwrap_or(entry.szExeFile.len());
                let name = String::from_utf16_lossy(&entry.szExeFile[..name_end]);
                let process =
                    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.th32ProcessID);
                if process.is_null() {
                    let error = GetLastError();
                    if error != ERROR_INVALID_PARAMETER
                        && error != ERROR_NOT_FOUND
                        && !is_known_system_process(&name, &root, &windows_root)
                    {
                        unknown.push(format!(
                            "PID {} ({name}, error {error})",
                            entry.th32ProcessID
                        ));
                    }
                } else {
                    let mut buf = vec![0u16; 32768];
                    let mut len = buf.len() as u32;
                    if QueryFullProcessImageNameW(process, 0, buf.as_mut_ptr(), &mut len) != 0 {
                        let path = PathBuf::from(String::from_utf16_lossy(&buf[..len as usize]));
                        if windows_path_is_under(&path, &root) {
                            found.push(entry.th32ProcessID);
                        }
                    } else {
                        let error = GetLastError();
                        let mut exit_code = 0;
                        let exited = GetExitCodeProcess(process, &mut exit_code) != 0
                            && exit_code != STILL_ACTIVE as u32;
                        if !exited && !is_known_system_process(&name, &root, &windows_root) {
                            unknown.push(format!(
                                "PID {} ({name}, error {error})",
                                entry.th32ProcessID
                            ));
                        }
                    }
                    CloseHandle(process);
                }
                ok = Process32NextW(snapshot, &mut entry);
                if ok == 0 {
                    let error = GetLastError();
                    anyhow::ensure!(
                        error == ERROR_NO_MORE_FILES,
                        "Cannot enumerate active processes: {error}"
                    );
                    break;
                }
            }
            anyhow::ensure!(
                unknown.is_empty(),
                "Cannot verify whether a process is using this resource: {}",
                unknown
                    .iter()
                    .take(8)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(", ")
            );
            Ok(())
        })();
        CloseHandle(snapshot);
        result?;
    }
    Ok(found)
}

#[cfg(windows)]
fn windows_directory() -> Result<PathBuf> {
    use windows_sys::Win32::System::SystemInformation::GetWindowsDirectoryW;
    let mut buffer = vec![0u16; 32768];
    let len = unsafe { GetWindowsDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    anyhow::ensure!(
        len > 0 && (len as usize) < buffer.len(),
        "Cannot resolve Windows directory"
    );
    canonical(Path::new(&String::from_utf16_lossy(
        &buffer[..len as usize],
    )))
}

fn is_known_system_process(name: &str, root: &Path, windows_root: &Path) -> bool {
    let name = name.to_lowercase();
    let name = name.strip_suffix(".exe").unwrap_or(&name);
    matches!(
        name,
        "system" | "registry" | "secure system" | "memory compression" | "vmmem" | "vmmemwsl"
    ) && !windows_path_is_under(root, windows_root)
}

fn windows_path_is_under(path: &Path, root: &Path) -> bool {
    let normalize = |value: &Path| value.to_string_lossy().replace('/', "\\").to_lowercase();
    let path = normalize(path);
    let root = normalize(root);
    let root = if root.ends_with(':') || root.ends_with('\\') {
        root
    } else {
        format!("{root}\\")
    };
    path == root.trim_end_matches('\\') || path.starts_with(&root)
}

#[cfg(windows)]
pub fn native_index_volume(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;
    let mut root = PathBuf::new();
    for component in path.components().take(2) {
        root.push(component.as_os_str());
    }
    let wide: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut fs = [0u16; 32];
    unsafe {
        if windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW(
            wide.as_ptr(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            fs.as_mut_ptr(),
            fs.len() as u32,
        ) == 0
        {
            return false;
        }
    }
    let end = fs.iter().position(|c| *c == 0).unwrap_or(fs.len());
    matches!(
        String::from_utf16_lossy(&fs[..end]).as_str(),
        "NTFS" | "ReFS"
    )
}
#[cfg(not(windows))]
pub fn native_index_volume(_: &Path) -> bool {
    false
}

/// Snapshot the current user's registered WSL identity; names alone can be reused.
#[cfg(windows)]
pub fn wsl_identity(distribution: &str) -> Result<serde_json::Value> {
    use windows_sys::Win32::Foundation::{ERROR_NO_MORE_ITEMS, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::*;
    struct Key(HKEY);
    impl Drop for Key {
        fn drop(&mut self) {
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }
    fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(Some(0)).collect()
    }
    unsafe fn string(key: HKEY, name: &str) -> Result<String> {
        let name = wide(name);
        let mut length = 0;
        anyhow::ensure!(
            unsafe {
                RegGetValueW(
                    key,
                    std::ptr::null(),
                    name.as_ptr(),
                    RRF_RT_REG_SZ,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut length,
                )
            } == ERROR_SUCCESS,
            "Missing WSL registry identity"
        );
        anyhow::ensure!(length < 65536, "WSL identity too long");
        let mut value = vec![0u16; (length as usize).div_ceil(2)];
        anyhow::ensure!(
            unsafe {
                RegGetValueW(
                    key,
                    std::ptr::null(),
                    name.as_ptr(),
                    RRF_RT_REG_SZ,
                    std::ptr::null_mut(),
                    value.as_mut_ptr().cast(),
                    &mut length,
                )
            } == ERROR_SUCCESS,
            "Cannot read WSL identity"
        );
        let count = value.iter().position(|c| *c == 0).unwrap_or(value.len());
        Ok(String::from_utf16(&value[..count])?)
    }
    unsafe {
        let mut root = std::ptr::null_mut();
        anyhow::ensure!(
            RegOpenKeyExW(
                HKEY_CURRENT_USER,
                wide(r"Software\Microsoft\Windows\CurrentVersion\Lxss").as_ptr(),
                0,
                KEY_READ,
                &mut root
            ) == ERROR_SUCCESS,
            "WSL registry unavailable"
        );
        let root = Key(root);
        for i in 0..10000 {
            let mut name = [0u16; 256];
            let mut count = name.len() as u32;
            let code = RegEnumKeyExW(
                root.0,
                i,
                name.as_mut_ptr(),
                &mut count,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            );
            if code == ERROR_NO_MORE_ITEMS {
                break;
            }
            anyhow::ensure!(code == ERROR_SUCCESS, "Cannot enumerate WSL registry");
            let mut key = std::ptr::null_mut();
            anyhow::ensure!(
                RegOpenKeyExW(root.0, name.as_ptr(), 0, KEY_READ, &mut key) == ERROR_SUCCESS,
                "Cannot inspect WSL registration"
            );
            let key = Key(key);
            if string(key.0, "DistributionName")? == distribution {
                return Ok(
                    serde_json::json!({"registration_id":String::from_utf16(&name[..count as usize])?,"base_path":string(key.0,"BasePath")?}),
                );
            }
        }
    }
    anyhow::bail!("WSL distribution identity unavailable")
}
#[cfg(not(windows))]
pub fn wsl_identity(_: &str) -> Result<serde_json::Value> {
    anyhow::bail!("Windows WSL registry required")
}

/// Attach usage tracking and persist PID before the new process can spawn descendants.
pub(crate) fn suspend_on_spawn(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000 | 0x00000004);
    }
}
pub(crate) fn resume_spawned(child: &std::process::Child) -> Result<()> {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::{
            Foundation::*,
            System::{Diagnostics::ToolHelp::*, Threading::*},
        };
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        anyhow::ensure!(
            snapshot != INVALID_HANDLE_VALUE,
            "Cannot inspect suspended process threads"
        );
        let mut entry: THREADENTRY32 = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
        let mut next = Thread32First(snapshot, &mut entry);
        let mut resumed = 0;
        while next != 0 {
            if entry.th32OwnerProcessID == child.id() {
                let thread = OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID);
                if !thread.is_null() {
                    let status = ResumeThread(thread);
                    CloseHandle(thread);
                    if status != u32::MAX {
                        resumed += 1;
                    }
                }
            }
            next = Thread32Next(snapshot, &mut entry);
        }
        CloseHandle(snapshot);
        anyhow::ensure!(resumed > 0, "Could not resume tracked process");
    }
    Ok(())
}

#[cfg(windows)]
pub fn native_volume_details(root: &Path) -> Result<(String, String)> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::*;
    let wide: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut guid = [0u16; 64];
    let mut filesystem = [0u16; 32];
    unsafe {
        anyhow::ensure!(
            GetVolumeNameForVolumeMountPointW(wide.as_ptr(), guid.as_mut_ptr(), guid.len() as u32)
                != 0,
            "Volume GUID unavailable"
        );
        anyhow::ensure!(
            GetVolumeInformationW(
                wide.as_ptr(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                filesystem.as_mut_ptr(),
                filesystem.len() as u32
            ) != 0,
            "Filesystem unavailable"
        );
    }
    let text = |v: &[u16]| {
        String::from_utf16_lossy(&v[..v.iter().position(|c| *c == 0).unwrap_or(v.len())])
    };
    Ok((text(&guid).trim_end_matches('\\').into(), text(&filesystem)))
}
#[cfg(not(windows))]
pub fn native_volume_details(_: &Path) -> Result<(String, String)> {
    bail!("Windows volume required")
}

/// Name includes process creation identity so PID reuse cannot release another task's protection.
pub fn process_job_name(pid: u32) -> Result<String> {
    Ok(format!(
        "Local\\AIRoot-process-{}-{}",
        pid,
        process_stamp(pid)?.context("Process identity unavailable")?
    ))
}
#[cfg(windows)]
pub fn job_finished(name: &str) -> Result<bool> {
    use windows_sys::Win32::{Foundation::*, System::JobObjects::*};
    let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
    let handle = unsafe { OpenJobObjectW(0x0004, 0, wide.as_ptr()) };
    if handle.is_null() {
        let error = unsafe { GetLastError() };
        anyhow::ensure!(
            error == ERROR_FILE_NOT_FOUND,
            "Cannot inspect process job: {error}"
        );
        return Ok(true);
    }
    Ok(Job(handle).active()? == 0)
}
#[cfg(not(windows))]
pub fn job_finished(_: &str) -> Result<bool> {
    Ok(false)
}

#[cfg(test)]
mod process_path_tests {
    use super::windows_path_is_under;
    use std::path::Path;

    #[test]
    fn process_path_matching_is_case_insensitive_and_component_bounded() {
        assert!(windows_path_is_under(
            Path::new(r"d:\ENV\Python.exe"),
            Path::new(r"D:\env")
        ));
        assert!(!windows_path_is_under(
            Path::new(r"D:\env-other\Python.exe"),
            Path::new(r"D:\env")
        ));
        assert!(windows_path_is_under(
            Path::new(r"D:\Python.exe"),
            Path::new(r"D:\")
        ));
    }

    #[test]
    fn unqueryable_system_processes_are_ignored_only_outside_windows_root() {
        let windows = Path::new(r"C:\Windows");
        assert!(super::is_known_system_process(
            "Memory Compression",
            Path::new(r"D:\env"),
            windows
        ));
        assert!(super::is_known_system_process(
            "vmmemWSL",
            Path::new(r"D:\env"),
            windows
        ));
        assert!(!super::is_known_system_process(
            "vmmemWSL",
            Path::new(r"C:\Windows\System32"),
            windows
        ));
        assert!(!super::is_known_system_process(
            "Memory Compression",
            Path::new(r"C:\Windows\System32"),
            windows
        ));
        assert!(!super::is_known_system_process(
            "custom-tool.exe",
            Path::new(r"D:\env"),
            windows
        ));
    }

    #[cfg(windows)]
    #[test]
    fn process_enumeration_finds_the_current_test_binary() {
        let executable = std::env::current_exe().unwrap();
        let root = executable.parent().unwrap();
        let processes = crate::platform::processes_under(root).unwrap();
        assert!(processes.contains(&std::process::id()));
    }
}
