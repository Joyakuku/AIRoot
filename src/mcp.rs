//! Local installed stdio MCP connections. Verification performs a bounded protocol handshake.
use crate::platform;
use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Read, Write},
    process::Stdio,
    time::{Duration, Instant},
};

pub fn probe(
    program: &str,
    args: &[String],
    mut on_start: impl FnMut(u32) -> Result<()>,
) -> Result<Value> {
    let mut command = platform::command(program, args);
    command.stdin(Stdio::piped()).stderr(Stdio::null());
    platform::suspend_on_spawn(&mut command);
    let mut child = command.spawn()?;
    #[cfg(windows)]
    let job = match platform::Job::attach(&child) {
        Ok(j) => j,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    };
    let result = (|| -> Result<Value> {
        on_start(child.id())?;
        platform::resume_spawned(&child)?;
        let mut input = child.stdin.take().context("MCP stdin unavailable")?;
        let output = child.stdout.take().context("MCP stdout unavailable")?;
        let (tx, rx) = std::sync::mpsc::sync_channel(16);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                let mut line = vec![];
                match reader
                    .by_ref()
                    .take(1024 * 1024 + 1)
                    .read_until(b'\n', &mut line)
                {
                    Ok(0) | Err(_) => break,
                    Ok(_) if line.len() > 1024 * 1024 => break,
                    Ok(_) => {
                        if tx.send(line).is_err() {
                            break;
                        }
                    }
                }
            }
        });
        writeln!(
            input,
            "{}",
            json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"AIRoot","version":env!("CARGO_PKG_VERSION")}}})
        )?;
        input.flush()?;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let line = rx
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .context("MCP initialize response timed out")?;
            let response: Value = serde_json::from_slice(&line)
                .context("MCP stdout must contain JSON-RPC messages only")?;
            if response["id"] != 1 {
                continue;
            }
            anyhow::ensure!(
                response["jsonrpc"] == "2.0" && response["error"].is_null(),
                "MCP initialization rejected"
            );
            let result = &response["result"];
            anyhow::ensure!(
                result["protocolVersion"] == "2024-11-05"
                    && result["serverInfo"]["name"].is_string()
                    && result["capabilities"].is_object(),
                "Incomplete MCP initialize response"
            );
            writeln!(
                input,
                "{}",
                json!({"jsonrpc":"2.0","method":"notifications/initialized"})
            )?;
            input.flush()?;
            return Ok(
                json!({"protocol_version":result["protocolVersion"],"server_name":result["serverInfo"]["name"],"server_version":result["serverInfo"]["version"],"capabilities":result["capabilities"],"transport":"stdio"}),
            );
        }
    })();
    // This is a disposable probe session, not an Agent-owned running server.
    #[cfg(windows)]
    job.terminate();
    let _ = child.kill();
    let _ = child.wait();
    result
}
