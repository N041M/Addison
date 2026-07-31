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
use std::os::unix::io::{AsRawFd, RawFd};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::filesystem::canonical_lossy;
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

/// What the core sends today, and what an absent `timeoutMs` means.
const DEFAULT_TIMEOUT_MS: u64 = 30_000;

/// The SHELL's own ceiling on a command's deadline, regardless of what the frame
/// asked for.
///
/// `timeoutMs` used to be taken verbatim, so `u64::MAX` off the wire meant a
/// command that is never stopped. The core always sends 30000 — but the shell is
/// the trusted process, and `a_missing_timeout_or_roots_does_not_crash_the_shell`
/// already states the rule this violated: it must not trust the frame. Deliberately
/// well above anything a real command needs and well below "forever", so a slow
/// build can still be given a longer budget by a future caller without handing the
/// core a way to park a worker indefinitely.
const MAX_TIMEOUT_MS: u64 = 120_000;

/// Scratch space every command gets. Outside the floor and outside every project,
/// so it costs nothing to allow — unless a dev config has put Addison's data dir
/// underneath it, which `write_root_collides_with_protected` catches.
const SCRATCH_DIR: &str = "/private/tmp";

// shell.runCommand {command, timeoutMs, writeRoots} -> {stdout, stderr, exitCode, sandboxed}
pub fn run_command(params: &Value) -> Result<Value, RpcError> {
    run_command_with_ceiling(params, MAX_TIMEOUT_MS)
}

/// The handler proper, with the timeout ceiling injected so a test can prove the
/// clamp on the CLOCK through the real path rather than only against the pure
/// helper — a clamp asserted only on a pure function is a clamp nobody has shown
/// the handler applies.
fn run_command_with_ceiling(params: &Value, ceiling_ms: u64) -> Result<Value, RpcError> {
    let command = required_str(params, "command", "A command is required.")?.to_string();
    let timeout = timeout_from(params, ceiling_ms);
    let write_roots: Vec<PathBuf> = params
        .get("writeRoots")
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().filter_map(Value::as_str).map(PathBuf::from).collect()
        })
        .unwrap_or_default();

    let (program, args, sandboxed) = sandbox_invocation(&command, &write_roots)?;
    let output = run_with_timeout(&program, &args, timeout)?;

    Ok(json!({
        "stdout": output.stdout,
        "stderr": output.stderr,
        "exitCode": output.exit_code,
        "sandboxed": sandboxed,
    }))
}

/// The command's deadline, read off the wire and CLAMPED. A missing or non-numeric
/// field is the core's default; anything above the ceiling becomes the ceiling.
fn timeout_from(params: &Value, ceiling_ms: u64) -> Duration {
    let asked = params.get("timeoutMs").and_then(Value::as_u64).unwrap_or(DEFAULT_TIMEOUT_MS);
    Duration::from_millis(asked.min(ceiling_ms))
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
///   2. reads stay broad, EXCEPT Addison's own data directories (item 4 below).
///      Confining reads generally would break ordinary developer work (`git
///      status` reads far outside a project), and exfiltration of a project's own
///      bytes is item 4's problem — output redaction — not this profile's. The
///      conversation store is a different matter: it is not the user's project,
///      nothing legitimate reads it, and `network-outbound` is granted.
///   3. `(deny file-write*)` then per-root allows: writes are denied wholesale and
///      re-permitted only inside folders the person has explicitly trusted. **This
///      is what finally makes workspace trust govern the shell.** Until now trust
///      bounded the careful typed file tools while `run_command` roamed all of
///      HOME — the boundary applied to the safe tools and not the dangerous one.
///      A root that collides with a protected dir is DROPPED rather than allowed —
///      see `write_root_collides_with_protected`, which is what makes item 4's
///      independence claim true.
///   4. the data-dir denies come LAST, so the floor beats every allow above it.
///      This is the shell deciding INDEPENDENTLY, exactly as
///      `refuse_addison_data_dir` does: the core's `writeRoots` is an input to the
///      boundary, never the boundary.
///
/// Seatbelt evaluates the last matching rule, so a later `deny` overrides an
/// earlier `allow`. If that ever stops being true, item 4's audit log is what
/// would show it — and `the_data_dir_deny_comes_after_every_allow` fails first.
///
/// **ORDERING ALONE IS NOT CONTAINMENT, which is why step 3 drops instead.**
/// Seatbelt matches a rename by its source and destination PATHS and does not
/// walk the subtree, so a write root that is a proper ANCESTOR of the data dir
/// let a command rename an intermediate directory out from under the deny —
/// `mv $ROOT/sub $ROOT/sub2 && rm -rf $ROOT/sub2` — and the recovery floor was
/// gone. Measured end-to-end against a byte-identical profile. The core's
/// `_workspace_grant` already refuses such a root at the door; until now that
/// meant the CORE held a property this file claimed to hold independently, and it
/// was live in dev/test where `ADDISON_DB_PATH` can put the floor under a
/// renameable ancestor.
///
/// EVERY NON-FILE CAPABILITY, and why it is here. `(deny default)` means each one
/// is a deliberate grant, so each needs a reason that survives review. These were
/// established by MEASUREMENT — removing one and running the real toolchain — not
/// by copying a profile from elsewhere, which is how the first draft acquired two
/// grants nothing needed:
///
///   * `process-exec process-fork` — a shell that cannot fork is not a shell.
///     Descendants inherit the profile, so this does not widen anything.
///   * `signal`, **filtered to `(target children)`**. It was granted unfiltered,
///     which meant an approved command could SIGKILL any process the user owns:
///     `pkill -9 addison` mid-write, or the Agent Core. Measured both ways — under
///     the wide grant a sandboxed `kill -9 <unrelated pid>` SUCCEEDS; under
///     `(target children)` the same kill is refused with "Operation not
///     permitted", while forking, `wait` and killing the shell's own jobs all keep
///     working, as do git, node, npm and pytest. This is the same reasoning that
///     removed `mach-lookup`; `signal` had simply been left in its widest form.
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
/// exfiltration path, only the useful half of the harness. Exfiltration of the
/// PROJECT's bytes is item 4's problem (output redaction) and v2's
/// (untrusted-content screening). Exfiltration of ADDISON's own store is not left
/// to them, because the read deny in step 4 removes it outright.
#[cfg(target_os = "macos")]
fn seatbelt_profile(write_roots: &[PathBuf], protected: &[PathBuf]) -> String {
    let mut profile = String::from(
        "(version 1)\n\
         (deny default)\n\
         (allow process-exec process-fork)\n\
         (allow signal (target children))\n\
         (allow sysctl-read)\n\
         (allow network-outbound)\n\
         (allow file-read*)\n\
         (deny file-write*)\n",
    );
    // A command with nowhere to write is still useful (it can read and report), so
    // an empty root list is a legitimate state, not an error. The scratch dir rides
    // through the SAME filter as the trusted roots: a dev config that puts the data
    // dir under /private/tmp would otherwise hand back the rename hole by the back
    // door.
    let scratch = PathBuf::from(SCRATCH_DIR);
    for root in write_roots.iter().chain(std::iter::once(&scratch)) {
        if write_root_collides_with_protected(root, protected) {
            continue;
        }
        if let Some(rule) = subpath_rule("allow file-write*", root) {
            profile.push_str(&rule);
        }
    }
    profile.push_str("(allow file-write-data (literal \"/dev/null\") (literal \"/dev/stdout\") (literal \"/dev/stderr\"))\n");
    // LAST. The floor always wins.
    for dir in protected {
        if let Some(rule) = subpath_rule("deny file-write*", dir) {
            profile.push_str(&rule);
        }
        // Addison's own memory is not the user's project. `filesystem.rs`'s
        // `refuse_addison_data_dir` has always refused READS here too, and its
        // doc-comment says the harness "can never write or read Addison's memory" —
        // but the profile emitted `(allow file-read*)` with no counterpart, so
        // `cat ~/.addison/addison.db` worked under the sandbox and the whole
        // conversation store was one `curl` away. Two adjacent files stated
        // opposite policies; this is the one that was wrong.
        if let Some(rule) = subpath_rule("deny file-read*", dir) {
            profile.push_str(&rule);
        }
    }
    profile
}

/// True when a write root must be DROPPED rather than allowed, because granting it
/// would put a protected directory inside a writable subtree (or hand out the
/// protected directory itself).
///
/// The predicate deliberately mirrors `refuse_addison_data_dir` exactly — refuse a
/// path that IS, sits inside, or CONTAINS a protected directory — so the shell's
/// independent floor and the core's grant-time rule cannot drift apart. The
/// contains case is the one with teeth: see `seatbelt_profile`'s doc-comment for
/// the rename that defeated ordering.
#[cfg(target_os = "macos")]
fn write_root_collides_with_protected(root: &Path, protected: &[PathBuf]) -> bool {
    let root = canonical_lossy(root);
    protected.iter().any(|dir| {
        let dir = canonical_lossy(dir);
        dir.starts_with(&root) || root.starts_with(&dir)
    })
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
    // trust rows already use. `canonical_lossy` (the filesystem floor's own
    // resolver) rather than a bare `canonicalize`, so a not-yet-created data dir
    // still resolves through /tmp and /var — and so this and
    // `write_root_collides_with_protected` can never disagree about what a path is.
    let resolved = canonical_lossy(path);
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
///
/// **AND THE PROCESS GROUP IS NOT ENOUGH EITHER.** A descendant that calls
/// `setsid()` LEAVES the group, so `kill(-pgid)` never reaches it — and it still
/// holds the inherited write end of the pipe, so an unconditional
/// `out_handle.join()` blocked for the orphan's entire lifetime, unbounded. Nor is
/// that only a timeout-path problem: `sh -c 'something & exit 0'` returns
/// instantly and leaves a background job holding the same pipe, so a command that
/// SUCCEEDED wedged the handler just as thoroughly. Measured: a setsid'ing
/// grandchild sleeping 5s took 5.04s to reach EOF though the direct child exited
/// immediately, and `sleep 86400` in its place would have wedged the shell's IPC
/// loop for a day. `an_overrunning_command_is_killed_promptly` cannot see this —
/// it only ever forked in-group.
///
/// So the drains do not block forever: their pipes are switched to non-blocking,
/// and once the child has been reaped they stop at the first moment the pipe is
/// empty (plus `DRAIN_GRACE` for a straggler write). The threads therefore always
/// terminate on their own — a bounded join with abandoned threads would leak a
/// thread and a file descriptor per hung command, which is the same unbounded
/// resource by a quieter name. Output an orphan writes AFTER its parent was reaped
/// is dropped, deliberately: nobody is waiting on it and no one can be.
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
    // Flipped once the child has been reaped; the drains read it to know that an
    // empty pipe means "nothing more is coming that anyone is waiting for".
    let reaped = Arc::new(AtomicBool::new(false));
    let out_handle = {
        let reaped = Arc::clone(&reaped);
        std::thread::spawn(move || drain(stdout, &reaped))
    };
    let err_handle = {
        let reaped = Arc::clone(&reaped);
        std::thread::spawn(move || drain(stderr, &reaped))
    };

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
    // Only now: everything the command itself wrote is already in the pipe buffers,
    // so from here an empty pipe that is still OPEN means an escaped descendant is
    // holding it, and waiting on that is waiting on nothing.
    reaped.store(true, Ordering::Relaxed);
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

/// How long a drain keeps reading an already-empty pipe after the child has been
/// reaped, before deciding the only writer left is an escaped descendant. Long
/// enough that a straggler write racing the reap is not lost; short enough that it
/// is invisible next to a command's own runtime.
const DRAIN_GRACE: Duration = Duration::from_millis(200);

/// How long a drain sleeps between polls of an empty pipe. Only ever reached when
/// there is nothing to read, so it costs a command that produces output nothing.
const DRAIN_POLL: Duration = Duration::from_millis(2);

/// Read a pipe to EOF, capped, and WITHOUT the possibility of blocking forever.
/// Lossy UTF-8: command output is bytes, and a tool that emits one invalid
/// sequence should not lose its whole output.
///
/// `reaped` is the caller's signal that the direct child is gone. Until then this
/// behaves exactly like a blocking read-to-EOF. After it, an empty-but-open pipe
/// means a descendant escaped the process group (see `run_with_timeout`), and this
/// gives up rather than holding the shell's IPC pump for that orphan's lifetime.
fn drain(pipe: Option<impl Read + AsRawFd>, reaped: &AtomicBool) -> String {
    let mut pipe = match pipe {
        Some(p) => p,
        None => return String::new(),
    };
    // Without this the read below blocks and no deadline in this function can fire.
    // A failing fcntl is not fatal — the drain simply degrades to the old blocking
    // behaviour rather than losing output — but it does not happen on a pipe this
    // process just created.
    set_nonblocking(pipe.as_raw_fd());

    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    let mut give_up_at: Option<Instant> = None;
    loop {
        match pipe.read(&mut chunk) {
            // EOF: every writer, escaped or not, has closed its end.
            Ok(0) => break,
            Ok(n) => {
                let room = MAX_CAPTURE_BYTES.saturating_sub(buffer.len());
                if room == 0 {
                    break;
                }
                buffer.extend_from_slice(&chunk[..n.min(room)]);
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                if reaped.load(Ordering::Relaxed) {
                    // NOT reset on a later successful read: an orphan that keeps
                    // writing must not be able to hold this thread open forever by
                    // printing. It is bounded by MAX_CAPTURE_BYTES either way.
                    let deadline = *give_up_at.get_or_insert_with(|| Instant::now() + DRAIN_GRACE);
                    if Instant::now() >= deadline {
                        break;
                    }
                }
                std::thread::sleep(DRAIN_POLL);
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&buffer).into_owned()
}

/// Put a descriptor into non-blocking mode, best effort.
fn set_nonblocking(fd: RawFd) {
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags >= 0 {
            libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK);
        }
    }
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

        // The data dir handed in as a trusted root ITSELF — the core asking for the
        // one folder it may never have. This is the case ordering was written for,
        // and the comment here used to claim it was the CONTAINMENT case ("a trusted
        // root that CONTAINS the data dir — the worst case"), which it is not: root
        // == protected is not root ⊃ protected, and the containment case is strictly
        // worse because ordering does not survive a rename. That case now has its own
        // test — `a_trusted_root_that_contains_the_data_dir_is_dropped` — and the
        // claim is no longer made twice by one assertion.
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
        // an allow that overlaps the data dir.
        //
        // The root here is a SIBLING of the protected dir, not its parent, and that
        // is the whole difference from the version this replaced. That one passed
        // `/tmp` as the root with the data dir underneath, which now produces a
        // profile with no `(allow file-write*` line at all — the ancestor is DROPPED
        // (`write_root_collides_with_protected`), because ordering alone never
        // contained a rename. So the old fixture stopped measuring ordering the
        // moment the drop landed, and unwrapped on None. Ordering still has to hold
        // for every root that IS allowed, which is what this now measures.
        let profile = seatbelt_profile(
            &[PathBuf::from("/tmp/addison-order-root")],
            &[PathBuf::from("/tmp/addison-order-data")],
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
        //
        // UPDATED DELIBERATELY, 2026-08-01, and NARROWED: `signal` was granted in
        // its widest form, which let an approved command SIGKILL any process the
        // user owns — the Addison shell mid-write, or the Agent Core. It is now
        // `(target children)`, on its own line so the filter is visible at a
        // glance. A human decided this; the pin was not bent to fit the code. The
        // measurement behind it is
        // `a_sandboxed_command_cannot_kill_an_unrelated_process` plus its positive
        // twin, which shows job control still works.
        let profile = seatbelt_profile(&[PathBuf::from("/tmp")], &[]);
        let granted: Vec<&str> = profile
            .lines()
            .filter(|line| line.starts_with("(allow ") && !line.contains("file-"))
            .collect();
        assert_eq!(
            granted,
            vec![
                "(allow process-exec process-fork)",
                "(allow signal (target children))",
                "(allow sysctl-read)",
                "(allow network-outbound)",
            ],
            "the non-file capability set changed; measure before granting"
        );
        // Said again, in the shape a widening would actually take: `signal` folded
        // back into the process line, or re-emitted bare. The equality above catches
        // both, but a reviewer reading only the profile should see the rule named.
        assert!(
            !profile.contains("(allow process-exec process-fork signal)"),
            "signal must not ride the process line unfiltered"
        );
        assert!(
            !profile.contains("(allow signal)"),
            "an unfiltered signal grant lets a command kill any process the user owns"
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

    #[test]
    fn a_grandchild_that_escapes_the_process_group_does_not_wedge_the_handler() {
        // `an_overrunning_command_is_killed_promptly` cannot see this: it only ever
        // forks IN-group, where `kill(-pgid)` closes the pipes for it. A descendant
        // that calls `setsid()` leaves the group, survives the kill, and keeps the
        // inherited stdout write end open — so an unconditional `join()` on the
        // drains blocked for that orphan's whole lifetime. Measured before the fix:
        // the direct `sh` exited in 5ms while the reader sat on the pipe for the
        // orphan's full sleep. With `sleep 86400` in its place that is the shell's
        // IPC pump held for a day.
        //
        // THE ASSERTION IS THE CLOCK, and the budget here is deliberately 10s while
        // the orphan lives 5s: a handler that returns quickly did so because the
        // child was reaped, not because the deadline fired. `exit_code == 0` says
        // the same thing from the other side — this is the SUCCESS path, which is
        // why the timeout path's kill never enters into it.
        let started = std::time::Instant::now();
        let out = run_with_timeout(
            "/bin/sh",
            &[
                "-c".into(),
                // perl, not `setsid(1)`: macOS does not ship that binary. Its stdout
                // is deliberately NOT redirected — holding the inherited pipe open is
                // the entire point.
                "perl -e 'use POSIX; POSIX::setsid(); sleep 5' & echo first; exit 0".into(),
            ],
            Duration::from_secs(10),
        )
        .unwrap();
        let elapsed = started.elapsed();

        assert!(
            elapsed < Duration::from_secs(3),
            "an escaped grandchild held the handler for {elapsed:?}: the drains are \
             waiting on a pipe nobody is going to close"
        );
        // POSITIVE CONTROL. Returning fast is trivially true of a command that never
        // ran, and of one whose output was dropped on the floor — so the direct
        // child's own output has to be here, and its own exit status has to be the
        // one reported.
        assert!(
            out.stdout.contains("first"),
            "the command's own output must still be captured (stderr: {})",
            out.stderr
        );
        assert_eq!(out.exit_code, 0, "this is the success path, not the timeout path");
        assert!(
            !out.stderr.contains("didn't finish in time"),
            "the command finished; nothing should have been stopped"
        );
    }

    #[test]
    fn a_sandboxed_command_cannot_kill_an_unrelated_process() {
        // `signal` was granted in its widest form, which meant an approved command
        // could SIGKILL anything the user owns — `pkill -9 addison` mid-write, or the
        // Agent Core, from inside a sandbox whose whole purpose is that a command
        // cannot reach past its own work.
        //
        // POSITIVE CONTROL, THREE WAYS, because "the victim is still alive" is the
        // classic false green — it is true when the profile was rejected and nothing
        // ran at all. So: the marker proves the sandbox applied and the command ran;
        // the recorded status proves the kill was ATTEMPTED; and the kernel's own
        // refusal text proves it was REFUSED rather than aimed at nothing.
        let root = tmp_dir("signal");
        let marker = root.join("ran.txt");
        let _ = fs::remove_file(&marker);

        // A child of the TEST process — a sibling of the sandboxed command, never its
        // descendant, which is exactly what `(target children)` distinguishes.
        let mut victim = Command::new("/bin/sleep")
            .arg("30")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn the unrelated process");
        let victim_pid = victim.id();

        let out = sandboxed_run(
            seatbelt_profile(std::slice::from_ref(&root), &[]),
            format!(
                "echo ran > '{}'; kill -9 {victim_pid} 2>&1; echo \"status=$?\"",
                marker.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(
            out.stdout.contains("status=1"),
            "the kill must have been attempted and refused; the shell reported \
             success instead (stdout: {}, stderr: {})",
            out.stdout,
            out.stderr
        );
        assert!(
            out.stdout.to_lowercase().contains("not permitted"),
            "the refusal must come from the KERNEL (EPERM), not from a bad pid or a \
             mangled command (stdout: {}, stderr: {})",
            out.stdout,
            out.stderr
        );
        let alive = victim.try_wait().expect("check the unrelated process").is_none();
        let _ = victim.kill();
        let _ = victim.wait();
        assert!(alive, "a sandboxed command killed a process it has no business touching");
    }

    #[test]
    fn a_sandboxed_command_can_still_kill_its_own_children() {
        // The other half of the narrowing, and the reason it is `(target children)`
        // rather than no `signal` grant at all: an ordinary shell has to be able to
        // stop the work it started. `timeout`, `xargs`, test runners and plain job
        // control all rest on it, so removing the capability outright would have
        // broken the harness while looking tidy in the profile.
        let root = tmp_dir("signal-own");
        let marker = root.join("ran.txt");
        let _ = fs::remove_file(&marker);

        let out = sandboxed_run(
            seatbelt_profile(std::slice::from_ref(&root), &[]),
            format!(
                "echo ran > '{}'; sleep 30 & pid=$!; kill -9 $pid 2>&1; echo \"own=$?\"",
                marker.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(
            out.stdout.contains("own=0"),
            "a command must still be able to kill its own child (stdout: {}, stderr: {})",
            out.stdout,
            out.stderr
        );
    }

    #[test]
    fn a_trusted_root_that_contains_the_data_dir_is_dropped() {
        // THE CASE ORDERING DOES NOT COVER, and the reason `seatbelt_profile` drops a
        // colliding root instead of relying on rule order. Seatbelt matches a rename
        // by source and destination PATHS and does not walk the subtree, so under an
        // ancestor allow this exact command moved the data dir out from under its own
        // deny and then deleted it — measured end-to-end against a byte-identical
        // profile. The core refuses such a grant at the door, but that made the CORE
        // the holder of a property this file claims to hold independently.
        //
        // WHAT IS RENAMED IS AN INTERMEDIATE DIRECTORY, not the data dir. Renaming
        // the data dir itself is refused even by ordering alone — the source path
        // matches the deny — so a test that did that passed with the drop removed
        // and measured nothing. `$ROOT/sub` matches no deny, and once it is
        // `$ROOT/sub2` neither does anything under it.
        let root = tmp_dir("ancestor");
        let sub = root.join("sub");
        let data_dir = sub.join("addison-data");
        let snapshots = data_dir.join("snapshots");
        fs::create_dir_all(&snapshots).unwrap();
        let sidecar = snapshots.join("genesis.json");
        fs::write(&sidecar, b"{\"undeletable\": true}").unwrap();
        let moved = root.join("sub2");
        let _ = fs::remove_dir_all(&moved);

        // The marker cannot live under `root` — the whole point is that `root` is not
        // writable — so it gets a permitted root of its own, which also proves the
        // drop is surgical rather than "the profile fell over".
        let allowed = tmp_dir("ancestor-allowed");
        let marker = allowed.join("ran.txt");
        let _ = fs::remove_file(&marker);

        let profile = seatbelt_profile(
            &[root.clone(), allowed.clone()],
            std::slice::from_ref(&data_dir),
        );
        let out = sandboxed_run(
            profile.clone(),
            format!(
                "echo ran > '{}'; mv '{}' '{}'; rm -rf '{}'",
                marker.display(),
                sub.display(),
                moved.display(),
                moved.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(
            sidecar.exists(),
            "a rename out from under the deny deleted the recovery floor \
             (stdout: {}, stderr: {})\nprofile:\n{profile}",
            out.stdout,
            out.stderr
        );
        assert!(!moved.exists(), "the intermediate directory must not have moved at all");
        assert!(
            !profile.contains(&format!("(allow file-write* (subpath \"{}\"))", root.display())),
            "an ancestor of a protected dir must never appear as an allow:\n{profile}"
        );
    }

    #[test]
    fn a_trusted_root_inside_the_data_dir_is_dropped_too() {
        // The mirror of the case above, and it was the one clause of
        // `write_root_collides_with_protected` that no test could see: deleting
        // `root.starts_with(&dir)` left the whole suite green, while deleting the
        // other half failed the ancestor test immediately.
        //
        // Ordering alone WOULD refuse this one — the deny is emitted after every
        // allow, so a write inside the data dir loses on last-match-wins — which is
        // exactly why it needs a test of its own rather than a shrug. Belt and
        // braces are worth having on the recovery floor, but an untested brace is
        // indistinguishable from a missing one the day somebody rewrites the
        // ordering, and this file's whole argument is that the shell decides
        // INDEPENDENTLY of anything the core sends.
        let data_dir = tmp_dir("inside-protected");
        let inside = data_dir.join("workspace");
        fs::create_dir_all(&inside).unwrap();

        let allowed = tmp_dir("inside-allowed");
        let marker = allowed.join("ran.txt");
        let _ = fs::remove_file(&marker);
        let target = inside.join("written.txt");
        let _ = fs::remove_file(&target);

        let profile = seatbelt_profile(
            &[inside.clone(), allowed.clone()],
            std::slice::from_ref(&data_dir),
        );
        let out = sandboxed_run(
            profile.clone(),
            format!(
                "echo ran > '{}'; echo leaked > '{}'",
                marker.display(),
                target.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(
            !target.exists(),
            "a write root inside the data dir let the harness write into the floor \
             (stdout: {}, stderr: {})\nprofile:\n{profile}",
            out.stdout,
            out.stderr
        );
        assert!(
            !profile.contains(&format!("(allow file-write* (subpath \"{}\"))", inside.display())),
            "a root inside a protected dir must never appear as an allow:\n{profile}"
        );
    }

    #[test]
    fn the_profile_refuses_to_read_addisons_own_store() {
        // `filesystem.rs`'s `addison_data_dirs` has always said the harness can never
        // "write or read" Addison's memory, and `refuse_addison_data_dir` refuses both
        // — but the profile emitted `(allow file-read*)` with no counterpart, so
        // `cat ~/.addison/addison.db` worked under the sandbox while `network-outbound`
        // is deliberately granted. Two adjacent files stated opposite policies. The
        // profile was the one that was wrong, so it now denies the read as well.
        let data_dir = tmp_dir("read-floor");
        let secret = data_dir.join("addison.db");
        fs::write(&secret, b"CONVERSATION-STORE-SECRET").unwrap();

        let allowed = tmp_dir("read-floor-allowed");
        let marker = allowed.join("ran.txt");
        let _ = fs::remove_file(&marker);

        let out = sandboxed_run(
            seatbelt_profile(std::slice::from_ref(&allowed), std::slice::from_ref(&data_dir)),
            format!(
                "echo ran > '{}'; cat '{}' 2>&1",
                marker.display(),
                secret.display()
            ),
        );

        assert_the_sandbox_actually_ran(&marker, &out);
        assert!(
            !out.stdout.contains("CONVERSATION-STORE-SECRET"),
            "the conversation store was readable under the sandbox, one curl from gone"
        );
        assert!(
            out.stdout.to_lowercase().contains("operation not permitted"),
            "the read must be REFUSED by the kernel, not merely missing (stdout: {}, \
             stderr: {})",
            out.stdout,
            out.stderr
        );
    }

    #[test]
    fn a_timeout_from_the_wire_cannot_exceed_the_shells_own_ceiling() {
        // The core always sends 30000, and that is exactly why this is the shell's
        // business: `a_missing_timeout_or_roots_does_not_crash_the_shell` states the
        // rule — the trusted process does not trust the frame. Unclamped, a single
        // `timeoutMs: u64::MAX` frame parked a blocking task until the command chose
        // to end.
        //
        // ON THE CLOCK, THROUGH THE REAL HANDLER. The pure `timeout_from` is asserted
        // below as well, but only the handler path proves the clamped value is the
        // one the deadline actually gets.
        let started = std::time::Instant::now();
        let result = run_command_with_ceiling(
            &json!({ "command": "sleep 30", "timeoutMs": u64::MAX, "writeRoots": [] }),
            600,
        )
        .expect("the handler must accept an absurd timeout, not fail on it");
        let elapsed = started.elapsed();

        assert!(
            elapsed < Duration::from_secs(5),
            "an unclamped timeoutMs held the handler for {elapsed:?}"
        );
        assert!(
            result["stderr"].as_str().unwrap().contains("didn't finish in time"),
            "the command must have been stopped by the ceiling, not have finished"
        );
        assert_eq!(timeout_from(&json!({ "timeoutMs": u64::MAX }), 600), Duration::from_millis(600));
        assert_eq!(timeout_from(&json!({ "timeoutMs": 500 }), 600), Duration::from_millis(500));
        assert_eq!(timeout_from(&json!({}), 600), Duration::from_millis(600));
        // The shipped ceiling, named: a change to it is a decision, not a typo.
        assert_eq!(MAX_TIMEOUT_MS, 120_000);
    }
}
