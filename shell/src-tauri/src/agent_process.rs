// Spawns and supervises the Python Agent Core child process — engineering-spec §1.1, §1.3.
//
// The core ships as a bundled child process (PyInstaller or similar), NOT a system
// dependency the user installs (§1.2, design-doc §7.8). Communication is JSON-RPC
// 2.0 over stdio (§7) — one frame per line, no network stack for local IPC.
//
// This module owns the process lifecycle and the stdout pump:
//   - resolve + spawn the core (piped stdin/stdout, inherited stderr);
//   - read stdout line-by-line; `shell.*`/`keychain.*` frames are handled in-process
//     (filesystem.rs / keychain.rs) and their response written back to the core's
//     stdin; everything else is relayed to the webview as a `core-message` event;
//   - on unexpected exit, tell the user in plain language and respawn ONCE.

use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{ChildStdin, Command};
use tokio::sync::Mutex;

use crate::{filesystem, ipc, keychain};

/// Managed state: the running core's stdin, swappable across a restart. `None`
/// whenever no child is live (before first launch, between launches, after the
/// final give-up). Shared by `ipc::send_to_core` (webview traffic) and this
/// module's stdout reader (shell/keychain responses).
#[derive(Clone)]
pub struct CoreStdin(pub Arc<Mutex<Option<ChildStdin>>>);

/// Called from the Tauri `setup` hook. Registers the shared stdin state and kicks
/// off the supervisor on Tauri's async runtime.
pub fn spawn(app: &AppHandle) -> tauri::Result<()> {
    let stdin_state = CoreStdin(Arc::new(Mutex::new(None)));
    app.manage(stdin_state.clone());

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        supervise(app, stdin_state).await;
    });
    Ok(())
}

/// Launch, run until the core exits, then respawn exactly once. No tight restart
/// loop: if the single respawn also exits, surface a final plain-language notice
/// and stop (the user restarts the app).
async fn supervise(app: AppHandle, stdin_state: CoreStdin) {
    match run_and_wait(&app, &stdin_state).await {
        Ok(()) => {
            // Clean spawn, but the child exited (stdout closed).
            emit_status(&app, "restarting", "Addison's engine stopped — restarting…");
        }
        Err(err) => {
            eprintln!("[addison] agent core failed to start: {err}");
            emit_status(&app, "error", "Addison's engine couldn't start.");
            return; // couldn't even launch — respawning would just fail identically.
        }
    }

    tokio::time::sleep(Duration::from_millis(1500)).await;

    if let Err(err) = run_and_wait(&app, &stdin_state).await {
        eprintln!("[addison] agent core respawn failed: {err}");
    }
    emit_status(&app, "stopped", "Addison's engine has stopped. Please restart the app.");
}

/// Spawn the core, publish its stdin, and pump its stdout until it closes.
/// Returns `Ok(())` when the child exits (EOF on stdout); `Err` only if it can't
/// be spawned at all.
async fn run_and_wait(app: &AppHandle, stdin_state: &CoreStdin) -> std::io::Result<()> {
    let mut command = resolve_core_command();
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // real logs go to the shell's stderr, not the user
        .kill_on_drop(true);

    let mut child = command.spawn()?;
    let stdout = child.stdout.take().expect("piped stdout");
    let stdin = child.stdin.take().expect("piped stdin");

    *stdin_state.0.lock().await = Some(stdin);
    emit_status(app, "ready", "Addison is ready.");

    let mut lines = BufReader::new(stdout).lines();
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => handle_line(app, stdin_state, line).await,
            Ok(None) => break,  // EOF — core exited
            Err(_) => break,    // read error — treat as exit
        }
    }

    *stdin_state.0.lock().await = None;
    let _ = child.wait().await; // reap so no zombie lingers
    Ok(())
}

/// Process one line of the core's stdout. `shell.*`/`keychain.*` are handled here
/// and answered back to the core; anything else is a Core->Frontend
/// notification/response and is relayed verbatim to the webview.
async fn handle_line(app: &AppHandle, stdin_state: &CoreStdin, line: String) {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return;
    }
    let frame: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        // Not JSON — almost certainly a stray print/log line. Real logs already go
        // to inherited stderr; drop it rather than forward garbage to the webview.
        Err(_) => return,
    };

    match frame.get("method").and_then(Value::as_str) {
        Some(method) if ipc::is_shell_bound(method) => {
            let id = frame.get("id").cloned().unwrap_or(Value::Null);
            let params = frame.get("params").cloned().unwrap_or(Value::Null);

            let outcome = if method.starts_with(ipc::KEYCHAIN_PREFIX) {
                keychain::handle(method, &params)
            } else {
                filesystem::handle(app, method, &params).await
            };

            let response = match outcome {
                Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
                Err(err) => json!({ "jsonrpc": "2.0", "id": id, "error": err.to_value() }),
            };
            write_to_core(stdin_state, &response).await;
        }
        // Core -> Frontend: relay untouched. The shell never interprets it (§1.3).
        _ => {
            let _ = app.emit("core-message", frame);
        }
    }
}

/// Write one JSON-RPC frame (as a line) back to the core's stdin.
async fn write_to_core(stdin_state: &CoreStdin, frame: &Value) {
    let mut line = match serde_json::to_string(frame) {
        Ok(s) => s,
        Err(_) => return,
    };
    line.push('\n');

    let mut guard = stdin_state.0.lock().await;
    if let Some(stdin) = guard.as_mut() {
        let _ = stdin.write_all(line.as_bytes()).await;
        let _ = stdin.flush().await;
    }
}

/// Resolve how to launch the Agent Core, in priority order:
///   1. `ADDISON_CORE_CMD` env override (dev/testing) — whitespace-split argv.
///   2. a bundled `addison-core` binary next to the app executable (production;
///      PyInstaller packaging is Phase 3 — we only resolve the path here).
///   3. dev fallback: `python3 -m agent_core.main`, preferring the repo venv's
///      interpreter when present, run from the repo root.
fn resolve_core_command() -> Command {
    // 1. Explicit override.
    if let Ok(raw) = std::env::var("ADDISON_CORE_CMD") {
        let mut parts = raw.split_whitespace();
        if let Some(program) = parts.next() {
            let mut cmd = Command::new(program);
            cmd.args(parts);
            return cmd;
        }
    }

    // 2. Bundled binary next to the app executable.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let bundled =
                dir.join(if cfg!(windows) { "addison-core.exe" } else { "addison-core" });
            if bundled.exists() {
                return Command::new(bundled);
            }
        }
    }

    // 3. Dev fallback: python3 -m agent_core.main from the repo root.
    let root = repo_root();
    let venv_python = root.join("agent_core/.venv/bin/python3");
    let program = if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python3")
    };
    let mut cmd = Command::new(program);
    cmd.arg("-m").arg("agent_core.main");
    cmd.current_dir(root); // so `-m agent_core.main` resolves the package
    cmd
}

/// Repo root as known at build time: `CARGO_MANIFEST_DIR` is `<repo>/shell/src-tauri`,
/// so two levels up is `<repo>`. Only used by the dev fallback above; production
/// uses the bundled-binary branch and never touches this.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// Emit a plain-language engine-status notice to the webview (§ CLAUDE.md: no
/// stack traces reach the user — a plain message the frontend can render calmly).
fn emit_status(app: &AppHandle, state: &str, message: &str) {
    let _ = app.emit("core-status", json!({ "state": state, "message": message }));
}
