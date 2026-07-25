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
//     `keychain.*` is handled OFF the loop (see `handle_line`) because the OS can put
//     a modal password dialog in front of it;
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

/// The running core's stdin plus a generation counter that ticks on every swap
/// (publish AND teardown). The counter exists for the detached keychain tasks: a
/// task parked at a password dialog can outlive the core that asked, and a
/// respawned core restarts its JSON-RPC ids at `core-req-1` — so a stale response
/// written blindly could resolve the NEW core's identically-numbered request with
/// the OLD core's answer. A task captures the generation with the request and only
/// writes back while it still matches.
pub struct CoreChannel {
    pub stdin: Option<ChildStdin>,
    pub generation: u64,
}

/// Managed state: the running core's channel, swappable across a restart. `stdin`
/// is `None` whenever no child is live (before first launch, between launches,
/// after the final give-up). Shared by `ipc::send_to_core` (webview traffic) and
/// this module's stdout reader (shell/keychain responses).
#[derive(Clone)]
pub struct CoreStdin(pub Arc<Mutex<CoreChannel>>);

/// Called from the Tauri `setup` hook. Registers the shared stdin state and kicks
/// off the supervisor on Tauri's async runtime.
pub fn spawn(app: &AppHandle) -> tauri::Result<()> {
    let stdin_state = CoreStdin(Arc::new(Mutex::new(CoreChannel { stdin: None, generation: 0 })));
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
    match run_and_wait(&app, &stdin_state, "Addison is ready.").await {
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

    // The respawn announces itself AS a restart — "Addison is ready." after a
    // crash reads like the crash never happened (2026-07 manual pass finding).
    if let Err(err) = run_and_wait(
        &app,
        &stdin_state,
        "Addison's engine restarted — you can keep chatting.",
    )
    .await
    {
        eprintln!("[addison] agent core respawn failed: {err}");
    }
    emit_status(&app, "stopped", "Addison's engine has stopped. Please restart the app.");
}

/// Spawn the core, publish its stdin, and pump its stdout until it closes.
/// Returns `Ok(())` when the child exits (EOF on stdout); `Err` only if it can't
/// be spawned at all.
async fn run_and_wait(
    app: &AppHandle,
    stdin_state: &CoreStdin,
    ready_message: &str,
) -> std::io::Result<()> {
    let mut command = resolve_core_command();
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // real logs go to the shell's stderr, not the user
        .kill_on_drop(true);

    let mut child = command.spawn()?;
    let stdout = child.stdout.take().expect("piped stdout");
    let stdin = child.stdin.take().expect("piped stdin");

    {
        let mut channel = stdin_state.0.lock().await;
        channel.stdin = Some(stdin);
        channel.generation += 1;
    }
    emit_status(app, "ready", ready_message);

    let mut lines = BufReader::new(stdout).lines();
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => handle_line(app, stdin_state, line).await,
            Ok(None) => break,  // EOF — core exited
            Err(_) => break,    // read error — treat as exit
        }
    }

    {
        let mut channel = stdin_state.0.lock().await;
        channel.stdin = None;
        channel.generation += 1;
    }
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

            if method.starts_with(ipc::KEYCHAIN_PREFIX) {
                spawn_keychain_request(stdin_state, method.to_string(), id, params);
                return;
            }

            let outcome = filesystem::handle(app, method, &params).await;
            write_to_core(stdin_state, &response_frame(id, outcome)).await;
        }
        // Core -> Frontend: relay untouched. The shell never interprets it (§1.3).
        _ => {
            let _ = app.emit("core-message", frame);
        }
    }
}

/// Answer a `keychain.*` request OFF this loop.
///
/// The OS keychain can put a MODAL password dialog in front of the user, and that
/// dialog blocks until it is answered. Handled inline, it stalled the whole pump: no
/// core frame of any kind could be read while the dialog was up, so the core's own
/// bridge timeout abandoned the request and the password, once typed, landed on a
/// request nobody was waiting on. So the work goes to a blocking task and the loop
/// moves straight to the next line.
///
/// Responses may now complete out of order. That is fine — JSON-RPC ids correlate
/// them core-side. Overlapping OS access is serialized inside keychain.rs, so two
/// requests can never raise two dialogs at once.
fn spawn_keychain_request(stdin_state: &CoreStdin, method: String, id: Value, params: Value) {
    let stdin_state = stdin_state.clone();
    tauri::async_runtime::spawn(async move {
        // Capture which core asked BEFORE doing the work. The request just came off
        // this core's stdout, and a respawn takes seconds, so the generation read
        // here is the asking core's.
        let generation = stdin_state.0.lock().await.generation;
        let handled =
            tauri::async_runtime::spawn_blocking(move || keychain::handle(&method, &params)).await;
        // The task itself failed (panicked or was cancelled). The core is waiting on
        // this id, so answer it in plain language rather than let it time out.
        let outcome = handled
            .unwrap_or_else(|_| Err(ipc::RpcError::app("The keychain didn't answer just now.")));

        let mut channel = stdin_state.0.lock().await;
        if channel.generation != generation {
            // The asking core is gone and its ids mean something else now. Dropping
            // the frame is the only correct delivery.
            eprintln!("[addison] dropped a keychain response for a core that has exited");
            return;
        }
        write_to_channel(&mut channel, &response_frame(id, outcome)).await;
    });
}

/// Wrap a handled Core->Shell outcome as its JSON-RPC response frame.
fn response_frame(id: Value, outcome: Result<Value, ipc::RpcError>) -> Value {
    match outcome {
        Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
        Err(err) => json!({ "jsonrpc": "2.0", "id": id, "error": err.to_value() }),
    }
}

/// Write one JSON-RPC frame (as a line) back to the core's stdin.
async fn write_to_core(stdin_state: &CoreStdin, frame: &Value) {
    let mut channel = stdin_state.0.lock().await;
    write_to_channel(&mut channel, frame).await;
}

/// The write itself, against an already-locked channel — split out so the keychain
/// task can check the generation and write under ONE lock tenure (check-then-write
/// across two tenures would reopen the stale-delivery window it exists to close).
async fn write_to_channel(channel: &mut CoreChannel, frame: &Value) {
    let mut line = match serde_json::to_string(frame) {
        Ok(s) => s,
        Err(_) => return,
    };
    line.push('\n');

    if let Some(stdin) = channel.stdin.as_mut() {
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
