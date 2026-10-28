use crate::{
    model::{Request, Response},
    store::Store,
};
use anyhow::{Context, Result, bail};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
pub static STOP: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
pub type Shared = Arc<Mutex<Store>>;
pub fn data_root() -> Result<PathBuf> {
    Ok(
        PathBuf::from(std::env::var_os("USERPROFILE").context("USERPROFILE missing")?)
            .join(".airoot"),
    )
}
#[cfg(windows)]
fn sid() -> Result<String> {
    use windows_sys::Win32::{
        Foundation::*, Security::Authorization::*, Security::*, System::Threading::*,
    };
    unsafe {
        let mut token = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            bail!("Cannot read user token");
        }
        let mut n = 0;
        GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut n);
        let mut buf = vec![0usize; (n as usize).div_ceil(std::mem::size_of::<usize>())];
        let ok = GetTokenInformation(token, TokenUser, buf.as_mut_ptr().cast(), n, &mut n);
        CloseHandle(token);
        if ok == 0 {
            bail!("Cannot read user SID");
        }
        let user = &*(buf.as_ptr() as *const TOKEN_USER);
        let mut text = std::ptr::null_mut();
        if ConvertSidToStringSidW(user.User.Sid, &mut text) == 0 {
            bail!("Cannot convert SID");
        }
        let mut len = 0;
        while *text.add(len) != 0 {
            len += 1;
        }
        let s = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
        LocalFree(text.cast());
        Ok(s)
    }
}
fn endpoint(root: &Path) -> Result<String> {
    Ok(format!(
        r"\\.\pipe\AIRoot-{}-{}",
        sid()?,
        &crate::platform::digest(&root.to_string_lossy().to_lowercase())?[..16]
    ))
}
#[cfg(windows)]
fn pipe(name: &str, first: bool) -> Result<tokio::net::windows::named_pipe::NamedPipeServer> {
    use windows_sys::Win32::{Foundation::*, Security::Authorization::*, Security::*};
    let sddl: Vec<u16> = format!("D:P(A;;GA;;;{})", sid()?)
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        let mut descriptor = std::ptr::null_mut();
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            std::ptr::null_mut(),
        ) == 0
        {
            bail!("Cannot build private pipe ACL");
        }
        let mut sa = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let result = tokio::net::windows::named_pipe::ServerOptions::new()
            .first_pipe_instance(first)
            .reject_remote_clients(true)
            .create_with_security_attributes_raw(
                name,
                (&mut sa as *mut SECURITY_ATTRIBUTES).cast(),
            );
        LocalFree(descriptor);
        Ok(result?)
    }
}
pub async fn daemon(root: PathBuf) -> Result<()> {
    std::fs::create_dir_all(&root)?;
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(root.join("worker.lock"))?;
    fs2::FileExt::try_lock_exclusive(&lock).context("Another worker owns this registry")?;
    let store = Arc::new(Mutex::new(Store::open(&root)?));
    let name = endpoint(&root)?;
    let mut server = pipe(&name, true)?;
    crate::recovery::startup(store.clone());
    loop {
        if STOP.load(std::sync::atomic::Ordering::SeqCst) {
            return Ok(());
        }
        match tokio::time::timeout(Duration::from_millis(250), server.connect()).await {
            Ok(r) => r?,
            Err(_) => continue,
        };
        let mut connected = server;
        server = pipe(&name, false)?;
        let shared = store.clone();
        tokio::spawn(async move {
            let result = async {
                let size = connected.read_u32_le().await?;
                anyhow::ensure!(size <= 1024 * 1024, "Request too large");
                let mut bytes = vec![0; size as usize];
                connected.read_exact(&mut bytes).await?;
                let request: Request = serde_json::from_slice(&bytes)?;
                let response = tokio::task::spawn_blocking(move || {
                    match crate::engine::dispatch(shared, request) {
                        Ok(v) => Response::success(v),
                        Err(e) => Response::failure(format!("{e:#}")),
                    }
                })
                .await?;
                let bytes = serde_json::to_vec(&response)?;
                connected.write_u32_le(bytes.len() as u32).await?;
                connected.write_all(&bytes).await?;
                connected.flush().await?;
                Ok::<(), anyhow::Error>(())
            }
            .await;
            if let Err(e) = result {
                eprintln!("IPC connection: {e}");
            }
        });
    }
}
pub async fn request(root: &Path, request: Request) -> Result<Response> {
    use tokio::net::windows::named_pipe::ClientOptions;
    let name = endpoint(root)?;
    let mut spawned = false;
    let start = std::time::Instant::now();
    let mut connection = loop {
        match ClientOptions::new().open(&name) {
            Ok(c) => break c,
            Err(e) => {
                if start.elapsed() > Duration::from_secs(15) {
                    return Err(e).context("Worker unavailable");
                }
                if !spawned {
                    crate::platform::spawn_daemon(&std::env::current_exe()?, root)?;
                    spawned = true;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
    };
    let bytes = serde_json::to_vec(&request)?;
    anyhow::ensure!(bytes.len() <= 1024 * 1024, "Request too large");
    connection.write_u32_le(bytes.len() as u32).await?;
    connection.write_all(&bytes).await?;
    let n = connection.read_u32_le().await?;
    anyhow::ensure!(n <= 32 * 1024 * 1024, "Response too large");
    let mut bytes = vec![0; n as usize];
    connection.read_exact(&mut bytes).await?;
    Ok(serde_json::from_slice(&bytes)?)
}
