// Spawns and supervises the Python Agent Core child process — engineering-spec §1.1, §1.3.
//
// The core ships as a bundled child process (PyInstaller or similar), NOT a system
// dependency the user installs (§1.2, design-doc §7.8). Communication is JSON-RPC
// 2.0 over stdio (§7) — one frame per line, no network stack for local IPC.
//
// This module owns the process lifecycle and the stdout pump:
//   - resolve + spawn the core (piped stdin/stdout, inherited stderr);
//   - read stdout line-by-line; `shell.*`/`keychain.*` frames are handled in-process
//     (filesystem.rs / keychain.rs / exec.rs) and their response written back to the
//     core's stdin; everything else is relayed to the webview as a `core-message`
//     event; `keychain.*` and `shell.runCommand` are handled OFF the loop (see
//     `dispatch_off_loop`) because both can hold their handler for a long time — the
//     OS can put a modal password dialog in front of one, and the other is a whole
//     command's runtime;
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

    // Anything slow is claimed here FIRST and never awaited (see `dispatch_off_loop`).
    if dispatch_off_loop(stdin_state, &frame) {
        return;
    }

    match frame.get("method").and_then(Value::as_str) {
        Some(method) if ipc::is_shell_bound(method) => {
            let id = frame.get("id").cloned().unwrap_or(Value::Null);
            let params = frame.get("params").cloned().unwrap_or(Value::Null);
            let outcome = filesystem::handle(app, method, &params).await;
            write_to_core(stdin_state, &response_frame(id, outcome)).await;
        }
        // Core -> Frontend: relay untouched. The shell never interprets it (§1.3).
        _ => {
            let _ = app.emit("core-message", frame);
        }
    }
}

/// OPEN-mode command execution. Named here rather than matched inside
/// `filesystem::handle` because THIS is where the decision that matters is made:
/// whether the frame is awaited on the pump or taken off it.
const RUN_COMMAND: &str = "shell.runCommand";

/// Claim the frames that must NOT be awaited on the core's stdout pump, and answer
/// them on their own tasks. Returns true when the frame was claimed.
///
/// **THE FAILURE THIS EXISTS TO PREVENT, twice now.** The OS keychain can put a
/// MODAL password dialog in front of the user, and that dialog blocks until it is
/// answered. Handled inline, it stalled the whole pump: no core frame of any kind
/// could be read while the dialog was up, so the core's own bridge timeout abandoned
/// the request and the password, once typed, landed on a request nobody was waiting
/// on.
///
/// `shell.runCommand` then reintroduced exactly that, with a longer and GUARANTEED
/// block: `exec::run_command` is fully synchronous (a `recv_timeout` plus three
/// thread joins) and holds its task for the command's entire runtime — up to the
/// shell's timeout ceiling. While any command ran, no core->webview frame was
/// relayed at all: streaming assistant tokens, status events and every other
/// `shell.*` request stalled behind it. It also parked a Tokio worker thread in
/// blocking syscalls with no `spawn_blocking`.
///
/// Sync on purpose, and takes no `AppHandle`: that is what lets
/// `a_long_command_does_not_block_the_next_request` drive the real dispatcher and
/// assert on the CLOCK. A deadline test that asserts on message text proves nothing
/// about the deadline (docs/HANDOFF.md).
///
/// Responses may complete out of order. That is fine — JSON-RPC ids correlate them
/// core-side. Overlapping OS keychain access is serialized inside keychain.rs, so
/// two requests can never raise two dialogs at once.
fn dispatch_off_loop(stdin_state: &CoreStdin, frame: &Value) -> bool {
    let Some(method) = frame.get("method").and_then(Value::as_str) else {
        return false;
    };
    let id = frame.get("id").cloned().unwrap_or(Value::Null);
    let params = frame.get("params").cloned().unwrap_or(Value::Null);

    if method.starts_with(ipc::KEYCHAIN_PREFIX) {
        let method = method.to_string();
        spawn_request(
            stdin_state,
            id,
            move || keychain::handle(&method, &params),
            "The keychain didn't answer just now.",
        );
        return true;
    }
    if method == RUN_COMMAND {
        spawn_request(
            stdin_state,
            id,
            move || crate::exec::run_command(&params),
            "Addison couldn't run that command.",
        );
        return true;
    }
    false
}

/// Run one Core->Shell request on a blocking task and write its response back when
/// it finishes, whenever that is. `failure` is the plain-language answer if the task
/// itself dies — the core is waiting on this id, so it gets an answer rather than a
/// timeout.
fn spawn_request<F>(stdin_state: &CoreStdin, id: Value, work: F, failure: &'static str)
where
    F: FnOnce() -> Result<Value, ipc::RpcError> + Send + 'static,
{
    let stdin_state = stdin_state.clone();
    tauri::async_runtime::spawn(async move {
        // Capture which core asked BEFORE doing the work. The request just came off
        // this core's stdout, and a respawn takes seconds, so the generation read
        // here is the asking core's.
        let generation = stdin_state.0.lock().await.generation;
        let handled = tauri::async_runtime::spawn_blocking(work).await;
        let outcome = handled.unwrap_or_else(|_| Err(ipc::RpcError::app(failure)));

        let mut channel = stdin_state.0.lock().await;
        if channel.generation != generation {
            // The asking core is gone and its ids mean something else now. Dropping
            // the frame is the only correct delivery.
            eprintln!("[addison] dropped a response for a core that has exited");
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

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::BufReader as AsyncBufReader;

    #[test]
    fn the_pump_asks_dispatch_off_loop_before_it_awaits_anything() {
        // THE WIRING, not the function. `a_long_command_does_not_block_the_next_
        // request` below calls `dispatch_off_loop` directly — correctly, because
        // `handle_line` needs an `AppHandle` no unit test can build — so it proves
        // the dispatcher works and proves NOTHING about whether the pump still
        // calls it. Measured: commenting the call out of `handle_line` leaves the
        // whole suite green while every command goes back to being awaited on the
        // read loop, which is the exact 30-second stall this file exists to avoid.
        //
        // This is trap 3 from docs/HANDOFF.md ("purifying a function for
        // testability moves the untested part to its caller") landing a second
        // time in the same repo, so it gets a source-level pin rather than a
        // comment. Coarse on purpose: it asserts ORDER, which is the property, and
        // not the surrounding shape, which is free to change.
        let source = include_str!("agent_process.rs");
        let start = source
            .find("async fn handle_line")
            .expect("handle_line must exist for this test to mean anything");
        let body = &source[start..];
        let end = body.find("\n}\n").expect("handle_line must be a closed function");
        let body = &body[..end];

        // The CALL, not the word. The first version of this test searched for
        // `dispatch_off_loop` and passed under its own mutation, because deleting
        // the call left the comment above it saying "see `dispatch_off_loop`" —
        // a source test that matches prose measures the prose.
        let dispatch = body
            .find("dispatch_off_loop(stdin_state")
            .expect("handle_line must CALL dispatch_off_loop — otherwise a slow \
                    request is awaited on the core's read loop and stalls every frame");
        let awaited = body
            .find("filesystem::handle")
            .expect("handle_line must still route shell-bound methods somewhere");
        assert!(
            dispatch < awaited,
            "dispatch_off_loop must be consulted BEFORE the inline await, or the \
             claim off it is worthless:\n{body}"
        );
    }

    #[tokio::test]
    async fn a_long_command_does_not_block_the_next_request() {
        // THE ASSERTION THAT MATTERS IS THE CLOCK, and the frames are read back off a
        // real pipe so the ORDER is the order they were actually written. A deadline
        // test that asserts on message text proves nothing about the deadline
        // (docs/HANDOFF.md) — and "both answers arrived" is true of the broken
        // version too, just two seconds later than it should be.
        //
        // `cat` stands in for the core: it gives us a genuine `ChildStdin` to answer
        // into and hands every answered frame straight back on its stdout.
        let mut sink = Command::new("/bin/cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("spawn the stand-in core");
        let channel = CoreStdin(Arc::new(Mutex::new(CoreChannel {
            stdin: Some(sink.stdin.take().expect("piped stdin")),
            generation: 0,
        })));
        let mut answers = AsyncBufReader::new(sink.stdout.take().expect("piped stdout")).lines();

        let started = std::time::Instant::now();
        assert!(
            dispatch_off_loop(
                &channel,
                &json!({
                    "jsonrpc": "2.0", "id": "slow", "method": "shell.runCommand",
                    "params": { "command": "sleep 2", "timeoutMs": 10_000, "writeRoots": [] }
                })
            ),
            "shell.runCommand must be taken off the pump"
        );
        // Whatever comes next off the core's stdout must be answerable NOW. A
        // keychain frame with no provider is refused by required_str before any OS
        // access, so this test never touches the real keychain.
        assert!(dispatch_off_loop(
            &channel,
            &json!({ "jsonrpc": "2.0", "id": "quick", "method": "keychain.getProviderKey", "params": {} })
        ));

        let first = answers.next_line().await.unwrap().expect("a first answer");
        let first_at = started.elapsed();
        assert!(first.contains("\"quick\""), "the quick request must be answered first: {first}");
        assert!(
            first_at < Duration::from_millis(1000),
            "the pump was held by the running command: the next request waited {first_at:?}"
        );

        // POSITIVE CONTROL. Without this the test passes when the command never ran
        // at all — "answered fast" is trivially true of work that did not happen.
        let second = answers.next_line().await.unwrap().expect("a second answer");
        let second_at = started.elapsed();
        assert!(second.contains("\"slow\""), "the command's own answer must still arrive: {second}");
        assert!(
            second_at >= Duration::from_millis(1500),
            "the command did not actually occupy its 2 seconds ({second_at:?}), so \
             answering the other request early proved nothing"
        );

        let _ = sink.kill().await;
    }

    #[tokio::test]
    async fn only_the_requests_that_can_block_are_taken_off_the_loop() {
        // The other half of the routing decision. Everything here answers from
        // in-memory state or is refused outright, so nothing slow or OS-touching runs.
        let channel = CoreStdin(Arc::new(Mutex::new(CoreChannel { stdin: None, generation: 0 })));
        let frame = |method: &str| json!({ "jsonrpc": "2.0", "id": 1, "method": method });

        assert!(dispatch_off_loop(&channel, &frame("shell.runCommand")));
        // Params-less on purpose: both keychain methods are refused by `required_str`
        // before any OS access, so a `cargo test` run still raises no password dialog.
        assert!(dispatch_off_loop(&channel, &frame("keychain.getProviderKey")));
        assert!(dispatch_off_loop(&channel, &frame("keychain.signRelayRequest")));
        // A picker or a file write is fast and needs the AppHandle, so it stays on
        // the pump where its ordering is trivially the core's own.
        assert!(!dispatch_off_loop(&channel, &frame("shell.writeWorkspaceFile")));
        assert!(!dispatch_off_loop(&channel, &frame("shell.readClipboard")));
        // Core -> Frontend traffic is relayed, never handled.
        assert!(!dispatch_off_loop(&channel, &frame("conversation.messageDelta")));
        assert!(!dispatch_off_loop(&channel, &json!({ "jsonrpc": "2.0", "id": 1 })));
    }
}
