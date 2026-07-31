// OPEN-mode command execution, under a seatbelt sandbox — step 5.5, items 1 + 2.
//
// WHY THIS FILE EXISTS. Step 5 shipped `run_command` as a `subprocess.run(shell=
// True)` inside the Agent Core. That contradicted engineering-spec §1.3 — "the
// Agent Core has no OS permissions of its own" — and it made `run_command` the one
// tool with no second enforcement layer: the typed file tools get
// `refuse_addison_data_dir` for free precisely BECAUSE they cross into this
// process. Moving execution here is an architecture correction that also puts the
// command in the only process where a sandbox can be applied.
//
// WHAT IT PROTECTS. Design-doc §9's first mitigation ("capability allow-list, not
// a shell") was broken by step 5 and never re-established. The property it
// protected — the model cannot issue an unbounded OS effect — is restored here, in
// bullet 2's idiom: the boundary is enforced at the process edge. The core decides
// WHETHER to ask (the permission card, per invocation, always) and the shell
// decides WHAT the command may touch.
//
// THE PROFILE IS NOT A GUARD. It has no toggle, never appears in the Custom guard
// panel, and is not user-tunable. The panel holds *prompting* guards; a
// user-disableable containment boundary would be a floor with an off switch. It
// behaves like `refuse_addison_data_dir` — invisible and not negotiable.
//
// HONEST DEGRADATION. `sandboxed` is in the response so nothing can quietly run
// unconfined. On macOS, where a profile is always available, a missing or
// unusable `sandbox-exec` REFUSES the command rather than running it bare — a
// silent unsandboxed fallback is this project's own anti-pattern (a guard that
// reports success while doing nothing). On other platforms there is no profile to
// apply yet (Landlock/bubblewrap is not built), so the command runs and the answer
// says `sandboxed: false`, which the core prints above the output.
//
// `sandbox-exec` is formally deprecated by Apple. It still works and is what
// Claude Code and Codex CLI both rely on. Acceptable; not permanent; recorded in
// the threat model rather than left as a surprise.

use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use serde_json::{json, Value};

use crate::ipc::{required_str, RpcError};

/// Where a command starts. HOME, not a trusted root: a command's cwd is a
/// convenience, never an effect bound (that is what the profile is for), and the
/// core has always run commands from home.
fn home_dir() -> PathBuf {
    std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("/"))
}

/// Absolute path, because a sandbox invoked through `PATH` is a sandbox an
/// attacker's `PATH` can replace.
const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";

/// Ceiling on captured output, mirroring the core's own transcript truncation.
/// Enforced here as well so a runaway `yes` cannot fill this process's memory
/// before the core ever sees it.
const MAX_CAPTURE_BYTES: usize = 512 * 1024;

// shell.runCommand {command, timeoutMs, writeRoots} -> {stdout, stderr, exitCode, sandboxed}
pub fn run_command(params: &Value) -> Result<Value, RpcError> {
    let command = required_str(params, "command", "A command is required.")?.to_string();
    let timeout_ms = params.get("timeoutMs").and_then(Value::as_u64).unwrap_or(30_000);
    let write_roots: Vec<PathBuf> = params
        .get("writeRoots")
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().filter_map(Value::as_str).map(PathBuf::from).collect()
        })
        .unwrap_or_default();

    let (program, args, sandboxed) = sandbox_invocation(&command, &write_roots)?;
    let output = run_with_timeout(&program, &args, Duration::from_millis(timeout_ms))?;

    Ok(json!({
        "stdout": output.stdout,
        "stderr": output.stderr,
        "exitCode": output.exit_code,
        "sandboxed": sandboxed,
    }))
}

/// Build the actual argv, and say whether it is confined.
///
/// Split out from the handler so both halves are testable without spawning
/// anything — the profile's TEXT is the security-critical part, and a test that
/// has to run a process to read it is a test nobody keeps.
#[cfg(target_os = "macos")]
fn sandbox_invocation(
    command: &str,
    write_roots: &[PathBuf],
) -> Result<(String, Vec<String>, bool), RpcError> {
    if !Path::new(SANDBOX_EXEC).exists() {
        // REFUSE, never fall back. On macOS the absence of sandbox-exec is an
        // anomaly, and running the command anyway would hand back exactly the
        // unconfined execution this file exists to remove.
        return Err(RpcError::app(
            "Addison couldn't set up its usual protection around that command, so it \
             didn't run it.",
        ));
    }
    // The protected dirs are resolved HERE and passed in, so `seatbelt_profile`
    // is a pure function of (what may be written, what may never be). It used to
    // read `addison_data_dirs()` itself, which made the profile depend on process
    // -global env — and the headline test, which points ADDISON_DB_PATH at a
    // fixture, raced any other test that touched the same variable. It passed
    // alone and failed in the full run: the worst shape of flake on the one test
    // this step is judged against, and a retry would have hidden it.
    let profile = seatbelt_profile(write_roots, &crate::filesystem::addison_data_dirs());
    Ok((
        SANDBOX_EXEC.to_string(),
        vec![
            "-p".to_string(),
            profile,
            "/bin/sh".to_string(),
            "-c".to_string(),
            command.to_string(),
        ],
        true,
    ))
}

/// No profile to apply yet — Linux needs its own Landlock/bubblewrap path. The
/// command runs, and the answer says so; the core prints a note above the output.
#[cfg(not(target_os = "macos"))]
fn sandbox_invocation(
    command: &str,
    _write_roots: &[PathBuf],
) -> Result<(String, Vec<String>, bool), RpcError> {
    Ok((
        "/bin/sh".to_string(),
        vec!["-c".to_string(), command.to_string()],
        false,
    ))
}

/// The seatbelt profile, generated from the LIVE trusted roots.
///
/// Order is the whole security argument, so read it in order:
///
///   1. `(deny default)` — nothing is permitted that is not named below.
///   2. reads stay broad. Confinement here would break ordinary developer work
///      (`git status` reads far outside a project), and exfiltration is item 4's
///      problem — output redaction — not this profile's.
///   3. `(deny file-write*)` then per-root allows: writes are denied wholesale and
///      re-permitted only inside folders the person has explicitly trusted. **This
///      is what finally makes workspace trust govern the shell.** Until now trust
///      bounded the careful typed file tools while `run_command` roamed all of
///      HOME — the boundary applied to the safe tools and not the dangerous one.
///   4. the data-dir denies come LAST, so the floor beats every allow above it,
///      including a trusted root that somehow contains the data dir. This is the
///      shell deciding INDEPENDENTLY, exactly as `refuse_addison_data_dir` does:
///      the core's `writeRoots` is an input to the boundary, never the boundary.
///
/// Seatbelt evaluates the last matching rule, so a later `deny` overrides an
/// earlier `allow`. If that ever stops being true, item 4's audit log is what
/// would show it — and `test_the_data_dir_deny_comes_after_every_allow` fails
/// first.
///
/// EVERY NON-FILE CAPABILITY, and why it is here. `(deny default)` means each one
/// is a deliberate grant, so each needs a reason that survives review. These were
/// established by MEASUREMENT — removing one and running the real toolchain — not
/// by copying a profile from elsewhere, which is how the first draft acquired two
/// grants nothing needed:
///
///   * `process-exec process-fork signal` — a shell that cannot fork is not a
///     shell. Descendants inherit the profile, so this does not widen anything.
///   * `sysctl-read` — REQUIRED, and narrowly: without it `node` aborts inside
///     `os.GetOSInformation` (a `uname` call), so `npm` of any kind crashes with a
///     native stack trace. Read-only system information; grants no effect.
///   * `network-outbound` — see the paragraph below.
///
/// Two capabilities the first draft granted and NOTHING needs: `mach-lookup` and
/// `ipc-posix-shm`. They were invented defensively, and unfiltered `mach-lookup`
/// is a known way to weaken a seatbelt profile — a confined process can ask a
/// system daemon to act on its behalf, outside the profile. git, node, python,
/// pytest and npm were each verified to work without both. **Do not re-add a
/// capability without measuring that something real fails without it**;
/// `the_profile_grants_no_capability_beyond_the_measured_set` will fail first.
///
/// NETWORK IS ALLOWED, and that is a decision rather than an oversight. Denying it
/// was the first draft's accidental default, and it broke `git fetch`, `npm
/// install`, `pip install` and `curl` with a DNS error that reads as a broken
/// machine rather than a policy — while buying nothing, because **the command's
/// output already travels to a cloud provider**. A profile that blocks `curl` and
/// then hands the same bytes to a model over HTTPS has not closed the
/// exfiltration path, only the useful half of the harness. Exfiltration is item
/// 4's problem (output redaction) and v2's (untrusted-content screening), exactly
/// as broad reads above are. `network-bind` is NOT granted: a model-issued command
/// has no business opening a listening socket, and the 30-second ceiling makes a
/// dev server pointless anyway.
#[cfg(target_os = "macos")]
fn seatbelt_profile(write_roots: &[PathBuf], protected: &[PathBuf]) -> String {
    let mut profile = String::from(
        "(version 1)\n\
         (deny default)\n\
         (allow process-exec process-fork signal)\n\
         (allow sysctl-read)\n\
         (allow network-outbound)\n\
         (allow file-read*)\n\
         (deny file-write*)\n",
    );
    // A command with nowhere to write is still useful (it can read and report), so
    // an empty root list is a legitimate state, not an error.
    for root in write_roots {
        if let Some(rule) = subpath_rule("allow file-write*", root) {
            profile.push_str(&rule);
        }
    }
    // Every command needs somewhere scratch; /private/tmp is outside the floor and
    // outside every project, so it costs nothing to allow.
    profile.push_str("(allow file-write* (subpath \"/private/tmp\"))\n");
    profile.push_str("(allow file-write-data (literal \"/dev/null\") (literal \"/dev/stdout\") (literal \"/dev/stderr\"))\n");
    // LAST. The floor always wins.
    for dir in protected {
        if let Some(rule) = subpath_rule("deny file-write*", dir) {
            profile.push_str(&rule);
        }
    }
    profile
}

/// One `(<verb> (subpath "<path>"))` line, or None for a path that cannot be
/// expressed safely.
///
/// The quoting matters more than it looks. A path containing `"` or `\` would
/// close the profile's own string literal and let the rest of the path be read as
/// profile source — a trusted-folder name would become a way to rewrite the
/// sandbox. There is no escaping syntax worth trusting here, so such a path is
/// DROPPED instead: dropping an allow can only ever make the sandbox tighter,
/// while dropping a deny is not possible (the data dirs are derived from HOME and
/// the env, not from user text, and are checked by the caller's own tests).
#[cfg(target_os = "macos")]
fn subpath_rule(verb: &str, path: &Path) -> Option<String> {
    let text = path.to_str()?;
    if text.is_empty() || !path.is_absolute() {
        return None;
    }
    if text.contains('"') || text.contains('\\') || text.contains('\n') {
        return None;
    }
    // Canonicalize so a symlinked root is expressed as the real path the kernel
    // will match against — the same realpath-vs-realpath discipline the core's
    // trust rows already use.
    let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let resolved = resolved.to_str()?;
    if resolved.contains('"') || resolved.contains('\\') || resolved.contains('\n') {
        return None;
    }
    Some(format!("({verb} (subpath \"{resolved}\"))\n"))
}

struct CapturedOutput {
    stdout: String,
    stderr: String,
    exit_code: i32,
}

/// Spawn, wait with a real deadline, and kill what overruns.
///
/// `std::process` has no timed wait, and the naive shapes are both wrong: reading
/// the pipes to EOF before waiting deadlocks behind a child that fills them, and
/// waiting first without draining deadlocks the child. So the pipes are drained on
/// their own threads and the wait happens on another, with the parent selecting on
/// a channel. A child that overruns is killed and whatever it printed first is
/// still returned — a timeout that discards the output is a timeout nobody can
/// debug.
///
/// **THE PROCESS GROUP IS THE WHOLE TIMEOUT.** `/bin/sh -c "echo x; sleep 30"`
/// FORKS: `sleep` is a grandchild, so signalling the direct child kills the shell
/// and leaves the real work running — still holding the write end of the stdout
/// pipe, so `drain` blocks until it finishes on its own. The first version of this
/// function did exactly that, and the effect was that the advertised timeout did
/// not exist for any compound command: a 600ms budget took the full 30 seconds,
/// the shell's IPC worker was held for as long as the longest orphan lived, and
/// the test still passed because it asserted on the OUTPUT and never on the CLOCK.
/// So: `process_group(0)` makes the child a group leader, the kill goes to
/// `-pgid`, and every descendant dies with it — which also closes the pipes and
/// lets the drains return. `an_overrunning_command_is_killed_promptly` now asserts
/// elapsed time, because that is the property, and it is the one an
/// output-shaped assertion cannot see.
fn run_with_timeout(
    program: &str,
    args: &[String],
    timeout: Duration,
) -> Result<CapturedOutput, RpcError> {
    let mut child = Command::new(program)
        .args(args)
        .current_dir(home_dir())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|_| RpcError::app("Addison couldn't run that command."))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let out_handle = std::thread::spawn(move || drain(stdout));
    let err_handle = std::thread::spawn(move || drain(stderr));

    let (tx, rx) = mpsc::channel();
    let mut waiter = child;
    let killer = waiter.id();
    let wait_thread = std::thread::spawn(move || {
        let status = waiter.wait();
        let _ = tx.send(status.map(|s| s.code().unwrap_or(-1)));
        waiter
    });

    let timed_out = match rx.recv_timeout(timeout) {
        Ok(_) => false,
        Err(_) => {
            // SIGKILL the whole GROUP (negative pid). The child is a group leader
            // via process_group(0), so this reaches every descendant it forked —
            // which is what actually stops the work AND closes the pipes the drain
            // threads are blocked on. Signalling `killer` alone leaves the
            // grandchildren running; see this function's docstring.
            //
            // SIGKILL rather than SIGTERM: the command is already past its budget,
            // and a catchable signal is one a runaway shell can ignore.
            unsafe {
                libc::kill(-(killer as i32), libc::SIGKILL);
            }
            true
        }
    };

    let mut child = wait_thread.join().map_err(|_| RpcError::app("Addison couldn't run that command."))?;
    let exit_code = child.wait().map(|s| s.code().unwrap_or(-1)).unwrap_or(-1);
    let stdout = out_handle.join().unwrap_or_default();
    let stderr = err_handle.join().unwrap_or_default();

    if timed_out {
        return Ok(CapturedOutput {
            stdout,
            stderr: format!(
                "{}\nThat command didn't finish in time, so Addison stopped it.",
                stderr
            )
            .trim()
            .to_string(),
            exit_code: -1,
        });
    }
    Ok(CapturedOutput { stdout, stderr, exit_code })
}

/// Read a pipe to EOF, capped. Lossy UTF-8: command output is bytes, and a tool
/// that emits one invalid sequence should not lose its whole output.
fn drain(pipe: Option<impl Read>) -> String {
    let mut pipe = match pipe {
        Some(p) => p,
        None => return String::new(),
    };
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        match pipe.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                let room = MAX_CAPTURE_BYTES.saturating_sub(buffer.len());
                if room == 0 {
                    break;
                }
                buffer.extend_from_slice(&chunk[..n.min(room)]);
            }
        }
    }
    String::from_utf8_lossy(&buffer).into_owned()
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;
    use std::fs;

    fn tmp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("addison-exec-{name}-{}", std::process::id()));
        let _ = fs::create_dir_all(&dir);
        fs::canonicalize(&dir).unwrap_or(dir)
    }

    /// Run one command under `profile` and return what it printed.
    fn sandboxed_run(profile: String, command: String) -> CapturedOutput {
        run_with_timeout(
            SANDBOX_EXEC,
            &["-p".into(), profile, "/bin/sh".into(), "-c".into(), command],
            Duration::from_secs(10),
        )
        .unwrap()
    }

    /// EVERY negative test below must call this, and here is why.
    ///
    /// A test that only asserts "the forbidden file is absent" passes just as
    /// happily when the profile was REJECTED and nothing ran at all — the strongest
    /// possible false green, on the one boundary this whole step exists to build.
    /// A mutation that widened the allowlist to `/` produced exactly that: it broke
    /// the profile, both negative tests went green, and only the positive test
    /// noticed. So each negative test also writes a marker into a permitted
    /// location in the SAME command, and asserts the marker landed. Marker present
    /// + target absent means the sandbox ran and refused; marker absent means the
    ///   test proved nothing and says so.
    fn assert_the_sandbox_actually_ran(marker: &Path, out: &CapturedOutput) {
        assert!(
            marker.exists(),
            "the profile never applied, so this test proved nothing \
             (stderr: {}, exit: {})",
            out.stderr,
            out.exit_code
        );
    }

    // ===================================================================
    // THE HEADLINE — the test step 5.5 is judged against.
    // ===================================================================
    // It lives here, in Rust, because this is where the boundary is. The Python
    // side can only prove that the core refuses to ASK; only this side can prove
    // that a command which DOES ask, and is approved, still cannot delete the
    // recovery floor. The command is quoted plainly on purpose — the core's string
    // denylist is not in play here at all; the point is that the kernel refuses.
    #[test]
    fn an_approved_command_cannot_delete_the_recovery_floor() {
        let data_dir = tmp_dir("floor");
        let snapshots = data_dir.join("snapshots");
        fs::create_dir_all(&snapshots).unwrap();
        let sidecar = snapshots.join("genesis.json");
        fs::write(&sidecar, b"{\"undeletable\": true}").unwrap();

        // A trusted root that CONTAINS the data dir — the worst case, and the one
        // the ordering of the profile exists for.
        let root = tmp_dir("floor-root");
        let marker = root.join("ran.txt");
        let _ = fs::remove_file(&marker);
        let profile = seatbelt_profile(&[data_dir.clone(), root.clone()], std::slice::from_ref(&data_dir));
        let out = sandboxed_run(
            profile,
            format!(
                "echo ran > '{}'; rm -f '{}'",
                marker.display(),
                sidecar.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(sidecar.exists(), "an approved command deleted the recovery floor");
    }

    #[test]
    fn a_write_inside_a_trusted_root_still_works() {
        // Not vacuous the other way either: the same profile that refuses the floor
        // must let ordinary project work through, or the sandbox is just a broken
        // shell that nobody would keep switched on.
        let root = tmp_dir("trusted");
        let target = root.join("hello.txt");
        let _ = fs::remove_file(&target);
        let out = sandboxed_run(
            seatbelt_profile(std::slice::from_ref(&root), &[]),
            format!("echo hi > '{}'", target.display()),
        );
        assert!(
            target.exists(),
            "a write inside a trusted root must succeed (stderr: {})",
            out.stderr
        );
    }

    #[test]
    fn a_write_outside_every_trusted_root_is_refused() {
        let root = tmp_dir("trusted-b");
        let marker = root.join("ran.txt");
        let _ = fs::remove_file(&marker);
        let outside = tmp_dir("outside").join("nope.txt");
        let _ = fs::remove_file(&outside);
        let out = sandboxed_run(
            seatbelt_profile(std::slice::from_ref(&root), &[]),
            format!(
                "echo ran > '{}'; echo hi > '{}'",
                marker.display(),
                outside.display()
            ),
        );
        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(!outside.exists(), "a write outside every trusted root must not land");
    }

    #[test]
    fn the_data_dir_deny_comes_after_every_allow() {
        // The ordering IS the floor-beats-a-root property. Seatbelt takes the last
        // matching rule, so a deny emitted before the allows would be overridden by
        // a trusted root that contains the data dir.
        let profile = seatbelt_profile(
            &[PathBuf::from("/tmp")],
            &[PathBuf::from("/tmp/addison-order")],
        );
        let last_allow = profile.rfind("(allow file-write*").unwrap();
        let first_deny_of_data_dir = profile.find("(deny file-write* (subpath").unwrap();
        assert!(
            first_deny_of_data_dir > last_allow,
            "the data-dir deny must come after every allow:\n{profile}"
        );
    }

    #[test]
    fn the_handler_feeds_the_real_protected_dirs_into_the_profile() {
        // The tests above hand `seatbelt_profile` its protected dirs explicitly —
        // which is what made them deterministic, and which left the WIRING
        // untested: dropping `addison_data_dirs()` at the call site killed the
        // floor and every one of them still passed. So assert the wiring directly,
        // on the argv the handler actually builds.
        let (program, args, sandboxed) =
            sandbox_invocation("true", &[PathBuf::from("/tmp")]).unwrap();
        assert_eq!(program, SANDBOX_EXEC);
        assert!(sandboxed);
        let profile = &args[1];
        let protected = crate::filesystem::addison_data_dirs();
        assert!(!protected.is_empty(), "HOME is always set, so there is always a floor");
        for dir in protected {
            let rule = subpath_rule("deny file-write*", &dir);
            if let Some(rule) = rule {
                assert!(
                    profile.contains(rule.trim_end()),
                    "the handler must deny {dir:?}; profile was:\n{profile}"
                );
            }
        }
    }

    #[test]
    fn the_wire_contract_matches_what_the_core_sends() {
        // THE ONE PATH NOTHING ELSE COVERS. Every other test here calls the inner
        // functions directly, and the Python tests stop at the bridge — so a
        // renamed field (`timeoutMs` -> `timeout_ms`, `exitCode` -> `exit_code`)
        // would pass both suites and fail the first time the app ran. The frame
        // below is built to match `IpcShellBridge.run_command` EXACTLY, and its
        // twin, `test_the_bridge_sends_exactly_what_the_shell_reads`, pins the
        // other end against the same four names. Hand-synced protocols need the
        // contract asserted on both sides or they are only asserted on neither.
        // EACH FIELD IS ASSERTED THROUGH ITS EFFECT, not just accepted. Both
        // inbound fields are read with `unwrap_or`, so a renamed key silently
        // becomes a default — and an earlier version of this test passed happily
        // with `timeoutMs` renamed to `timeout_ms`, which is the exact failure it
        // was written to catch. So: the write proves `writeRoots` arrived (no
        // roots => the sandbox refuses it) and the elapsed time proves `timeoutMs`
        // arrived (ignored => the 30s default, not 600ms).
        let root = tmp_dir("contract");
        let target = root.join("landed.txt");
        let _ = fs::remove_file(&target);

        let result = run_command(&json!({
            "command": format!("echo contract > '{}'; cat '{}'", target.display(), target.display()),
            "timeoutMs": 5_000,
            "writeRoots": [root.to_str().unwrap()],
        }))
        .expect("the handler must accept the core's frame");

        assert_eq!(result["stdout"].as_str().unwrap().trim(), "contract");
        assert!(target.exists(), "writeRoots did not reach the profile");
        assert!(result["stderr"].is_string(), "stderr must be a string");
        assert_eq!(result["exitCode"].as_i64(), Some(0));
        assert_eq!(result["sandboxed"].as_bool(), Some(true));

        let started = std::time::Instant::now();
        run_command(&json!({
            "command": "sleep 30",
            "timeoutMs": 600,
            "writeRoots": [],
        }))
        .unwrap();
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "timeoutMs did not reach the deadline — the handler fell back to its default"
        );
        // Nothing extra: an unread key is a key one side thinks it is providing.
        let keys: std::collections::BTreeSet<&str> =
            result.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(
            keys,
            ["exitCode", "sandboxed", "stderr", "stdout"].into_iter().collect()
        );
    }

    #[test]
    fn a_missing_timeout_or_roots_does_not_crash_the_shell() {
        // The core always sends all three, but the shell is the trusted process:
        // it must not panic on a frame it did not expect. Absent roots means
        // nothing outside temp is writable — the safe reading, not an open one.
        let result = run_command(&json!({"command": "true"})).unwrap();
        assert_eq!(result["exitCode"].as_i64(), Some(0));
        assert!(run_command(&json!({})).is_err(), "a frame with no command must be refused");
    }

    #[test]
    fn the_profile_grants_no_capability_beyond_the_measured_set() {
        // `(deny default)` makes every `(allow …)` a deliberate grant, and the
        // first draft carried two that nothing needed — `mach-lookup` (unfiltered,
        // a known way to reach outside a profile via a system daemon) and
        // `ipc-posix-shm`. They were copied in defensively rather than measured.
        //
        // So the set is pinned. Adding a line here means having removed it, run
        // the real toolchain, and watched something fail — the note in
        // `seatbelt_profile`'s docstring records what that measurement was for
        // each one.
        let profile = seatbelt_profile(&[PathBuf::from("/tmp")], &[]);
        let granted: Vec<&str> = profile
            .lines()
            .filter(|line| line.starts_with("(allow ") && !line.contains("file-"))
            .collect();
        assert_eq!(
            granted,
            vec![
                "(allow process-exec process-fork signal)",
                "(allow sysctl-read)",
                "(allow network-outbound)",
            ],
            "the non-file capability set changed; measure before granting"
        );
        // Named explicitly, because "absent from a list" is easy to miss in review.
        assert!(!profile.contains("mach-lookup"), "mach-lookup is not needed by anything");
        assert!(!profile.contains("ipc-posix-shm"), "ipc-posix-shm is not needed by anything");
        assert!(
            !profile.contains("network-bind"),
            "a model-issued command must not open a listening socket"
        );
    }

    #[test]
    fn a_root_that_could_break_out_of_the_profile_is_dropped() {
        // A folder name carrying a quote would close the profile's string literal
        // and let the rest be read as profile source. Dropping an allow can only
        // tighten the sandbox, so that is the safe answer.
        assert!(subpath_rule("allow file-write*", Path::new("/tmp/a\"b")).is_none());
        assert!(subpath_rule("allow file-write*", Path::new("/tmp/a\\b")).is_none());
        assert!(subpath_rule("allow file-write*", Path::new("relative/path")).is_none());
        assert!(subpath_rule("allow file-write*", Path::new("/tmp")).is_some());
    }

    #[test]
    fn an_overrunning_command_is_killed_promptly() {
        // THE ASSERTION THAT MATTERS IS THE CLOCK. An output-shaped assertion
        // passes even when nothing was killed at all: the earlier version of this
        // test took the full 30 seconds — the entire budget of the command it was
        // supposed to have stopped — and reported success, because the stderr note
        // is appended on the timeout path regardless of whether the kill landed.
        // `sleep 30 &` plus a foreground sleep is the shape that exposed it: both
        // are grandchildren of the process actually being signalled.
        let started = std::time::Instant::now();
        let out = run_with_timeout(
            "/bin/sh",
            &["-c".into(), "echo first; sleep 30 & sleep 30".into()],
            Duration::from_millis(600),
        )
        .unwrap();
        let elapsed = started.elapsed();

        assert!(
            elapsed < Duration::from_secs(5),
            "the timeout did not stop the command: took {elapsed:?} for a 600ms budget              (a forked grandchild survived the kill and held the pipe open)"
        );
        assert!(out.stdout.contains("first"), "output before the timeout must survive");
        assert!(out.stderr.contains("didn't finish in time"));
        assert_ne!(out.exit_code, 0);
    }
}
