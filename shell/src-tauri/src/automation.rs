// OS-run automation — the one surface in Addison that hands a job to the operating
// system. Step 8 phase 3, shell half.
//
// ================================ SAFETY FRAME ================================
// **Addison authors; the OS runs; Addison never triggers itself** — GLOBAL FLOOR G2
// (docs/SAFETY.md owns it). Nothing here is a timer, a watcher or a callback: this
// module writes one file into one directory and asks launchd to read it. Every
// invocation is a Core->Shell request the core only makes after the person has typed
// the automation's nonce into the arming card (plan §3) — the ceremony lives in the
// core, the effect lives here, and the two are separated by the process boundary on
// purpose.
//
// WHY THE SHELL AND NOT THE CORE (plan §2). The Agent Core has no OS permissions of
// its own (spec §1.3), and step 5.5's seatbelt already denies precisely what arming
// needs: `~/Library/LaunchAgents` is in `OS_AUTOMATION_DIRS`, write-denied in every
// profile, and `launchctl` is refused as a command's first token before the gate is
// even asked. So arming cannot ride `run_command`, and that is the feature: the only
// way to install or remove an armed job is this typed surface, which exists for
// nothing else.
//
// THE NARROWNESS IS THE CONTRACT (plan §5.8). The core sends STRUCTURED FIELDS and
// never a built document. This process assembles the XML itself, enforces the
// `com.addison.auto.` prefix ITSELF (the core is not trusted for it — the
// highest-trust process owns its own directory), and will only ever write or delete
// `<label>.plist` under one directory. A shell surface that accepted raw XML for
// LaunchAgents would be `run_command` with extra steps.
//
// `RunAtLoad` IS NEVER SET (plan §5.7). Arming must not cause an immediate run: the
// first execution happens on the OS's own schedule, which keeps "Addison never
// triggers itself" clean even at the moment of installation. Its absence is pinned
// by name in `the_plist_never_sets_run_at_load`.
//
// AN ARMED JOB RUNS UNCONFINED, and that is a decision rather than an omission (plan
// §5.7). The seatbelt confines *Addison's* commands; an armed job is the *person's*
// automation, consented to with the strongest ceremony the app has, run by the OS
// with Addison possibly not even installed any more. A frozen seatbelt profile would
// go stale the moment they trust another folder — a stale profile is a lie with a
// safety label. The card says "it runs outside Addison's sandbox" instead, which is
// true forever.
// =============================================================================
//
// PLATFORM GATING, and why the macOS half is one module rather than thirty
// attributes. `#[cfg(target_os = "macos")]` compiles on a developer's Mac and
// VANISHES on CI's Linux runner, taking its imports and constants with it, so
// `clippy -D warnings` finds dead code there that does not exist here
// (docs/HANDOFF.md, "Before you touch anything"). Per-item attributes make that risk
// proportional to the number of items, and every item below — every constant, every
// helper, every test — is used only by the macOS path. One gate over one module
// makes the analysis structural: there is exactly one boundary to check, and nothing
// on the other side of it references anything inside.

use serde_json::{json, Value};

use crate::ipc::RpcError;

/// What every method answers off macOS. One plain sentence, the same temperament as
/// the seatbelt's non-mac disclosure: say what is not available, do not pretend.
///
/// v1 arms launchd user agents and nothing else (plan §5.4) — no cron, no Windows
/// Task Scheduler. Drafts can be authored anywhere; only arming is macOS-only.
#[cfg(not(target_os = "macos"))]
const NOT_ON_THIS_COMPUTER: &str =
    "Addison can only set things to run on a schedule on a Mac, so it didn't set this one up.";

// shell.armAutomation {label, command, scheduleKind, schedule} -> {ok} | {ok:false, error}
#[cfg(not(target_os = "macos"))]
pub fn arm(_params: &Value) -> Result<Value, RpcError> {
    Ok(json!({ "ok": false, "error": NOT_ON_THIS_COMPUTER }))
}

// shell.disarmAutomation {label} -> {ok} | {ok:false, error}
#[cfg(not(target_os = "macos"))]
pub fn disarm(_params: &Value) -> Result<Value, RpcError> {
    Ok(json!({ "ok": false, "error": NOT_ON_THIS_COMPUTER }))
}

// shell.listArmed {} -> {armed, supported}
#[cfg(not(target_os = "macos"))]
pub fn list_armed(_params: &Value) -> Result<Value, RpcError> {
    Ok(json!({ "armed": [], "supported": false }))
}

#[cfg(target_os = "macos")]
pub use mac::{arm, disarm, list_armed};

#[cfg(target_os = "macos")]
mod mac {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::sync::mpsc;
    use std::time::Duration;

    use super::{json, RpcError, Value};

    /// Every label Addison writes starts here, and this process REFUSES ANY OTHER —
    /// `agent_core/automations.py::LABEL_PREFIX` mints them, and that is precisely
    /// why the check is repeated on this side. The core is the lower-trust process;
    /// a label is the file name this module will create and delete under the user's
    /// LaunchAgents folder, so the promise "Addison only ever touches its own files"
    /// has to be enforced by whoever writes the file.
    const LABEL_PREFIX: &str = "com.addison.auto.";

    /// How long a label's stem may be, matching `automations.MAX_SLUG_CHARS`. The
    /// accepted shape in full: `com.addison.auto.[a-z0-9][a-z0-9-]{0,39}` — no dots
    /// after the prefix, no separators, no `..`, no upper case, no NUL, nothing else.
    const MAX_STEM_CHARS: usize = 40;

    /// A ceiling on the command text this process will write into a plist. The core's
    /// authoring door already caps a command at 2000 characters
    /// (`automations.MAX_COMMAND_CHARS`); this is not that bound restated, it is the
    /// shell declining to write an unbounded file into the user's LaunchAgents folder
    /// on the say-so of the process it does not trust. Deliberately far above any
    /// real command so it can never be the thing that refuses honest work.
    const MAX_COMMAND_BYTES: usize = 8 * 1024;

    /// Absolute path, for the same reason `SANDBOX_EXEC` is absolute in exec.rs: a
    /// binary invoked through `PATH` is a binary an attacker's `PATH` can replace.
    const LAUNCHCTL: &str = "/bin/launchctl";

    /// Bounded wait on every `launchctl` invocation. It is a subprocess and it can
    /// hang; a hung one must not hold the task that is answering the core forever.
    /// Generous next to launchctl's real runtime (milliseconds) and short enough that
    /// a person watching the arming card is not left in front of a dead surface.
    const LAUNCHCTL_TIMEOUT: Duration = Duration::from_secs(10);

    // ---------------------------------------------------------------------------
    // Refusals. Plain language, no jargon, and each says what to do instead where
    // there is anything to say (CLAUDE.md's house rule).
    //
    // Several of these say what `agent_core/automations.py` says at its own door.
    // That duplication is deliberate and is NOT a lockstep: the core refuses so the
    // person gets a good sentence while they are authoring; this process refuses
    // because it must be able to refuse ALONE, on a frame it did not compose. If the
    // two ever word something differently, nothing breaks — only one of them is ever
    // reached in practice, and it is the core's.
    // ---------------------------------------------------------------------------
    const NOT_ADDISONS_OWN: &str =
        "Addison can only set up and remove automations it named itself, so it left that alone.";
    const NEEDS_A_COMMAND: &str = "There's nothing to run, so Addison didn't set anything up.";
    const COMMAND_TOO_LONG: &str =
        "That command is too long for Addison to put on a schedule. Try a shorter one, or put \
         the steps in a script and schedule the script.";
    const COMMAND_HAS_ODD_CHARACTERS: &str =
        "That command has characters Addison can't put in a schedule. Try writing it as plain \
         text on one line.";
    const NEEDS_A_KIND: &str = "Addison can run something every so many minutes, or at a set \
                                time of day. Pick one of those two.";
    const NEEDS_MINUTES: &str = "Say how many minutes to wait between runs — one or more.";
    const NEEDS_HOUR: &str = "Give the hour as a whole number from 0 to 23.";
    const NEEDS_MINUTE: &str = "Give the minutes as a whole number from 0 to 59.";
    const NEEDS_WEEKDAY: &str = "Give the day as a number from 0 for Sunday to 6 for Saturday, \
                                 or leave the day out to run every day.";
    const NO_HOME_FOLDER: &str =
        "Addison couldn't find your home folder, so it didn't set anything up.";
    const COULD_NOT_WRITE: &str =
        "Addison couldn't save the schedule, so nothing was set up. Please try again.";
    const COULD_NOT_REMOVE: &str =
        "Addison couldn't remove that schedule. Please try again in a moment.";
    const SCHEDULER_REFUSED: &str = "The computer's scheduler wouldn't take that job, so \
                                     Addison set nothing up. Please try again in a moment.";
    const SCHEDULER_DID_NOT_ANSWER: &str = "The computer's scheduler didn't answer, so Addison \
                                            stopped waiting and set nothing up.";
    /// A failed arm that REPLACED a working schedule. The ordinary failure sentences
    /// say "set nothing up", which is true of the new job and false about the old
    /// one: replacing means the previous copy is unloaded before the new one is
    /// bootstrapped, so a bootstrap that fails leaves the person with neither.
    /// Saying "nothing changed" there would be the one lie that matters — they would
    /// go on believing a job was running (adversarial review, 2026-08-07).
    const REPLACED_AND_FAILED: &str = "The computer's scheduler wouldn't take the new schedule, \
                                       and the old one was switched off first — so nothing is \
                                       running now. Arm it again to switch it back on.";
    /// Disarm could not even ASK the scheduler. Distinct from "the job wasn't
    /// loaded", which is the ordinary case and is fine: this one means Addison does
    /// not know whether the job is still running, so it leaves the file in place
    /// rather than removing the only thing that can name it later.
    const COULD_NOT_ASK_TO_STOP: &str = "Addison couldn't reach the computer's scheduler to \
                                         switch that off, so it left everything as it was. \
                                         Please try again in a moment.";

    // ===========================================================================
    // The three methods.
    // ===========================================================================
    //
    // EVERY OUTCOME IS AN `Ok` FRAME. The contract is `{ok} | {ok:false, error}`
    // (protocol.py), so a refusal is a RESULT the core can put on a card and in
    // `tool_audit`, never a JSON-RPC error the bridge would have to translate. The
    // `RpcError` in the signature is what `spawn_request` needs; nothing here
    // produces one.

    // shell.armAutomation {label, command, scheduleKind, schedule} -> {ok} | {ok:false, error}
    pub fn arm(params: &Value) -> Result<Value, RpcError> {
        Ok(answer(arm_inner(params)))
    }

    // shell.disarmAutomation {label} -> {ok} | {ok:false, error}
    pub fn disarm(params: &Value) -> Result<Value, RpcError> {
        Ok(answer(disarm_inner(params)))
    }

    // shell.listArmed {} -> {armed: [<label>], supported}
    //
    // A DIRECTORY READ, not `launchctl list`, and the choice is worth stating. What
    // the surface is asking is "which of Addison's automations are installed on this
    // computer" — and the plist IS that state: it is what arming creates, what
    // disarming removes, what a reinstall or a hand-deletion changes, and what
    // launchd loads at every login. `launchctl list` would answer a narrower
    // question (what is loaded in THIS login session) at the cost of a subprocess on
    // a surface-load path, and it would report a freshly-installed-but-not-yet-
    // bootstrapped job as absent when it will in fact run. The row is never the
    // record of armed-ness either way (plan §5.6) — this is the OS's own folder
    // being read, not Addison's memory.
    pub fn list_armed(_params: &Value) -> Result<Value, RpcError> {
        let armed = match launch_agents_dir() {
            Ok(dir) => installed_labels(&dir),
            // No home folder means no LaunchAgents folder to read. Nothing is armed,
            // which is the honest answer rather than an error on a read-only surface.
            Err(_) => Vec::new(),
        };
        Ok(json!({ "armed": armed, "supported": true }))
    }

    /// One shape for both write methods' answers.
    fn answer(outcome: Result<(), String>) -> Value {
        match outcome {
            Ok(()) => json!({ "ok": true }),
            Err(error) => json!({ "ok": false, "error": error }),
        }
    }

    /// Install the plist and hand it to launchd.
    ///
    /// ORDER IS THE ARGUMENT, so read it in order: nothing that can fail on the
    /// caller's data touches the disk. The label is validated (so the path is
    /// Addison's own file and nothing else), the command and schedule are validated
    /// (so the document is one launchd can read), and only then is a path built —
    /// from `launch_agents_dir()` and the VALIDATED label, never from anything the
    /// caller sent.
    fn arm_inner(params: &Value) -> Result<(), String> {
        let label = validated_label(params)?;
        let command = command_from(params)?;
        let trigger = trigger_from(params)?;

        let dir = launch_agents_dir()?;
        let text = plist_text(&label, &command, &trigger);
        // Whether this REPLACES a schedule decides what a failure below may claim,
        // so it is read before anything is written.
        let replacing = plist_path(&dir, &label)?.exists();
        let path = write_plist_atomically(&dir, &label, &text)?;

        // Replace in place. launchd refuses to bootstrap a label it already holds,
        // and a person re-arming an automation means "make this the schedule", not
        // "fail because an older copy is loaded". Best effort by design: the ordinary
        // case is that nothing is loaded and this fails with "no such process".
        let _ = launchctl(&["bootout", &service_target(&label)]);

        let installed = path.to_string_lossy().into_owned();
        if let Err(problem) = launchctl_must_succeed(&["bootstrap", &domain_target(), &installed]) {
            // A FAILED ARM LEAVES NOTHING BEHIND. A plist that stays in the folder is
            // armed at the next login whatever launchctl said today, so the file goes
            // back out with the failure.
            let _ = fs::remove_file(&path);
            // ...and if it replaced a working schedule, SAY SO. The ordinary
            // sentences promise "nothing was set up", which is true of the new job
            // and false about the old one this already unloaded.
            return Err(arm_failure(replacing, problem));
        }
        Ok(())
    }

    /// Which sentence a failed arm gets. A pure choice, extracted so it can be
    /// tested at its own boundary rather than only through `arm_inner`, whose real
    /// caller cannot reach a bootstrap failure in a test (this repo's recurring
    /// "guard unreachable from its caller" shape — a source-order assertion is not
    /// the same as exercising the branch).
    fn arm_failure(replacing: bool, problem: String) -> String {
        if replacing {
            // The new job did not start AND the old one is already unloaded, so the
            // ordinary "Addison set nothing up" would leave somebody believing a
            // schedule is still running.
            REPLACED_AND_FAILED.to_string()
        } else {
            problem
        }
    }

    /// Unload the job and remove its file.
    ///
    /// IDEMPOTENT (protocol.py): a label that is not installed is already in the state
    /// this asks for, so it answers `ok`. That matters because disarming is
    /// `arm_automation`'s undo — an undo that reports failure for a job somebody
    /// already removed by hand is an undo the person cannot rely on.
    ///
    /// THE FILE REMOVAL IS THE AUTHORITATIVE ACT, and `bootout` is best effort. The
    /// plist is what brings the job back at every login; without it launchd forgets
    /// the job at logout. A bootout that fails has almost always failed because the
    /// job was not loaded in the first place, and refusing to remove the file over
    /// that would leave a real schedule in place to protect a paperwork error.
    fn disarm_inner(params: &Value) -> Result<(), String> {
        let label = validated_label(params)?;
        let dir = launch_agents_dir()?;
        let path = plist_path(&dir, &label)?;

        // NON-ZERO IS FINE; NO ANSWER IS NOT. `bootout` exits non-zero for "no such
        // process", which is the ordinary case and exactly what this method promises
        // to treat as already-done. An `Err` is a different thing entirely — the
        // process could not be spawned, or it blew the deadline — and it means
        // Addison does not KNOW whether the job is still loaded. Reporting success
        // there told the person "your computer won't run it any more" while launchd
        // held it for the rest of the login session, and `list_armed` could not
        // contradict that because the file was already gone (adversarial review,
        // 2026-08-07). So the file stays: it is the only thing that can name the job
        // on a surface or reach it with a Disarm.
        if launchctl(&["bootout", &service_target(&label)]).is_err() {
            return Err(COULD_NOT_ASK_TO_STOP.to_string());
        }

        match fs::remove_file(&path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err(COULD_NOT_REMOVE.to_string()),
        }
    }

    // ===========================================================================
    // The label — this process's own promise about which files it touches.
    // ===========================================================================

    /// The `label` parameter, validated HERE rather than trusted from the core.
    fn validated_label(params: &Value) -> Result<String, String> {
        let label = params.get("label").and_then(Value::as_str).unwrap_or_default();
        if !label_is_valid(label) {
            return Err(NOT_ADDISONS_OWN.to_string());
        }
        Ok(label.to_string())
    }

    /// `^com\.addison\.auto\.[a-z0-9][a-z0-9-]{0,39}$`, spelled out.
    ///
    /// Written as a character walk rather than a regex because it is the whole
    /// promise of §5.8 and it must be readable without a second language in the way:
    /// after the prefix there are only lowercase ASCII letters, digits and hyphens, so
    /// there is no `/`, no `.`, no `..`, no NUL, no upper case and no non-ASCII
    /// anywhere in the file name this module builds. A path separator cannot appear,
    /// so `dir.join(format!("{label}.plist"))` cannot leave `dir` — that is a
    /// property of the alphabet, not of the caller's manners.
    fn label_is_valid(label: &str) -> bool {
        let Some(stem) = label.strip_prefix(LABEL_PREFIX) else {
            return false;
        };
        if stem.is_empty() || stem.len() > MAX_STEM_CHARS {
            return false;
        }
        let mut chars = stem.chars();
        // The first character carries no hyphen, so a label can never start with one
        // and can never be all punctuation.
        match chars.next() {
            Some(c) if c.is_ascii_lowercase() || c.is_ascii_digit() => {}
            _ => return false,
        }
        chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    }

    // ===========================================================================
    // The command and the schedule — re-validated, never trusted.
    // ===========================================================================

    /// The command as it will appear inside the plist's `<string>`.
    ///
    /// Control characters are refused rather than escaped: XML 1.0 cannot express
    /// most of them at all, so a command carrying one produces a document `plutil`
    /// rejects and launchd never reads — a file sitting armed-looking in the folder
    /// while nothing runs. Tab, newline and carriage return are legal in XML text and
    /// are legal in a shell command, so they ride through.
    fn command_from(params: &Value) -> Result<String, String> {
        let command = params.get("command").and_then(Value::as_str).unwrap_or_default();
        if command.trim().is_empty() {
            return Err(NEEDS_A_COMMAND.to_string());
        }
        if command.len() > MAX_COMMAND_BYTES {
            return Err(COMMAND_TOO_LONG.to_string());
        }
        if command.chars().any(|c| c.is_control() && c != '\t' && c != '\n' && c != '\r') {
            return Err(COMMAND_HAS_ODD_CHARACTERS.to_string());
        }
        Ok(command.to_string())
    }

    /// What makes the job fire. The CLOSED vocabulary of `automations.SCHEDULE_KINDS`,
    /// held a second time because this process is the one that writes the trigger.
    #[derive(Debug, PartialEq, Eq)]
    enum Trigger {
        /// Already in SECONDS — `StartInterval`'s unit. The ×60 happens once, at the
        /// door, so no renderer can forget it.
        Interval { seconds: i64 },
        Calendar { hour: i64, minute: i64, weekday: Option<i64> },
    }

    /// `scheduleKind` + `schedule` as a trigger, with EVERY BOUND RE-CHECKED.
    ///
    /// The core checks these too (`automations.schedule_problem`, and
    /// `schedule_is_readable` when it renders). This is not that check moved; it is
    /// the same check made independently by the process that writes the file, for the
    /// reason the whole module exists: out-of-range values do not fail loudly in
    /// launchd. `Hour: 99` loads cleanly and never fires, and a job that silently
    /// never runs is the failure that looks like success until somebody notices.
    fn trigger_from(params: &Value) -> Result<Trigger, String> {
        let kind = params.get("scheduleKind").and_then(Value::as_str).unwrap_or_default();
        let schedule = params.get("schedule").cloned().unwrap_or(Value::Null);
        match kind {
            "interval" => {
                let minutes = whole_number(&schedule, "minutes").ok_or(NEEDS_MINUTES)?;
                if minutes < 1 {
                    return Err(NEEDS_MINUTES.to_string());
                }
                // A value large enough to overflow the multiplication would wrap to
                // some other schedule entirely. Refused with the same sentence: what
                // the person has to do about it is the same.
                let seconds = minutes.checked_mul(60).ok_or(NEEDS_MINUTES)?;
                Ok(Trigger::Interval { seconds })
            }
            "calendar" => {
                let hour = whole_number(&schedule, "hour").filter(|h| (0..=23).contains(h));
                let hour = hour.ok_or(NEEDS_HOUR)?;
                let minute = whole_number(&schedule, "minute").filter(|m| (0..=59).contains(m));
                let minute = minute.ok_or(NEEDS_MINUTE)?;
                // ABSENT means every day, and the plist simply carries no `Weekday`.
                // Present-but-wrong is refused; `null` is treated as absent so a
                // caller may send the whole field set without pruning it first.
                let weekday = match schedule.get("weekday") {
                    None | Some(Value::Null) => None,
                    Some(_) => Some(
                        whole_number(&schedule, "weekday")
                            .filter(|d| (0..=6).contains(d))
                            .ok_or(NEEDS_WEEKDAY)?,
                    ),
                };
                Ok(Trigger::Calendar { hour, minute, weekday })
            }
            _ => Err(NEEDS_A_KIND.to_string()),
        }
    }

    /// One integer field of the schedule object, or None.
    ///
    /// `as_i64` is what excludes the shapes a schedule cannot be: `true` is not a
    /// number of minutes, `7.5` is not an hour, `"7"` is not either. The same
    /// exclusion `automations._is_whole_number` makes on the other side, for the same
    /// reason.
    fn whole_number(schedule: &Value, key: &str) -> Option<i64> {
        schedule.get(key).and_then(Value::as_i64)
    }

    // ===========================================================================
    // The document.
    // ===========================================================================

    /// The launchd plist, built from typed fields — BYTE-IDENTICAL to
    /// `agent_core/automations.py::plist_text` for the same inputs.
    ///
    /// That identity is the load-bearing property, and it is why this function is
    /// laid out to be diffed against the Python rather than written the way a Rust
    /// programmer would lay it out. The preview the person read before typing the
    /// nonce is the core's rendering; the file that lands in LaunchAgents is this
    /// one. If they can differ, "the preview you approved" stops being a meaningful
    /// phrase and the ceremony's entire evidence is a document nobody saw.
    ///
    /// Two properties inherited from that side, restated because they are the ones a
    /// rewrite would lose:
    ///   * **No `RunAtLoad` key, ever** (plan §5.7) — absent rather than false, so
    ///     arming never causes an immediate run. `the_plist_never_sets_run_at_load`
    ///     pins it by name.
    ///   * Label and command cross `xml_escape` — a command containing `</string>` is
    ///     a command, not document structure. Unescaped, that payload closes the
    ///     element early and the next thing it writes is `<key>RunAtLoad</key>`.
    fn plist_text(label: &str, command: &str, trigger: &Trigger) -> String {
        // WRITTEN ONE LINE PER LINE OF OUTPUT, deliberately. A `\`-continued literal
        // swallows the leading whitespace of the next source line, which is exactly
        // the plist's indentation — so the tidier-looking spelling of this function
        // silently emits a different document from the preview. `concat!` and
        // `push_str` keep every emitted line visible as itself, which is what makes
        // this readable side-by-side with `agent_core/automations.py::plist_text`.
        let mut out = String::from(concat!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n",
            "<plist version=\"1.0\">\n",
            "<dict>\n",
            "    <key>Label</key>\n",
        ));
        out.push_str(&format!("    <string>{}</string>\n", xml_escape(label)));
        out.push_str(concat!(
            "    <key>ProgramArguments</key>\n",
            "    <array>\n",
            "        <string>/bin/sh</string>\n",
            "        <string>-c</string>\n",
        ));
        out.push_str(&format!("        <string>{}</string>\n", xml_escape(command)));
        out.push_str("    </array>\n");
        match trigger {
            Trigger::Interval { seconds } => {
                out.push_str("    <key>StartInterval</key>\n");
                out.push_str(&format!("    <integer>{seconds}</integer>\n"));
            }
            Trigger::Calendar { hour, minute, weekday } => {
                out.push_str("    <key>StartCalendarInterval</key>\n");
                out.push_str("    <dict>\n");
                out.push_str(&format!("        <key>Hour</key>\n        <integer>{hour}</integer>\n"));
                out.push_str(&format!("        <key>Minute</key>\n        <integer>{minute}</integer>\n"));
                if let Some(weekday) = weekday {
                    out.push_str(&format!("        <key>Weekday</key>\n        <integer>{weekday}</integer>\n"));
                }
                out.push_str("    </dict>\n");
            }
        }
        out.push_str("</dict>\n");
        out.push_str("</plist>\n");
        out
    }

    /// `&`, `<`, `>` — exactly what `xml.sax.saxutils.escape` does, in the order it
    /// does it (ampersand first, or the escapes escape each other).
    ///
    /// Quotes are deliberately NOT escaped, and that is the Python's behaviour rather
    /// than an omission: nothing here is an attribute value, so `"` inside a
    /// `<string>` is text. Escaping it would produce a valid plist that differs
    /// byte-for-byte from the preview, which is the one thing this function may not
    /// do.
    fn xml_escape(text: &str) -> String {
        text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
    }

    // ===========================================================================
    // The one directory, and the one file in it.
    // ===========================================================================

    /// `<home>/Library/LaunchAgents`, or a refusal.
    ///
    /// AN ABSENT OR EMPTY `HOME` IS A REFUSAL, and this deliberately does NOT follow
    /// `exec.rs::home_dir`'s fallback to `/`. There, `/` keeps the seatbelt's deny
    /// rules absolute and therefore keeps the floor at full length. Here the same
    /// fallback would aim a WRITE at `/Library/LaunchAgents` — the system-wide agent
    /// folder, every user's login, root's domain — which plan §5.4 says no phase may
    /// ever touch. When the fallback and the effect point in opposite directions, the
    /// answer is to refuse, not to pick one.
    fn launch_agents_dir() -> Result<PathBuf, String> {
        launch_agents_dir_of(&std::env::var("HOME").unwrap_or_default())
    }

    /// The folder, given a home. Split from the environment read because the guard
    /// above is a REFUSAL and a test that proved it would have to blank `HOME` for
    /// the whole process — the exact shape exec.rs records as its worst flake
    /// (`ADDISON_DB_PATH` raced every other test that read it, passed alone, failed
    /// in the full run). A pure function costs one line at the caller and the guard
    /// becomes provable without touching process-global state.
    fn launch_agents_dir_of(home: &str) -> Result<PathBuf, String> {
        let home = PathBuf::from(home);
        if home.as_os_str().is_empty() || !home.is_absolute() {
            return Err(NO_HOME_FOLDER.to_string());
        }
        Ok(home.join("Library").join("LaunchAgents"))
    }

    /// The only file path this module ever builds, and it is built from a directory
    /// this module derived plus a label this module validated — never from anything
    /// that arrived in a frame.
    ///
    /// THE VALIDATION IS REPEATED HERE ON PURPOSE. Every caller already validates,
    /// and that is exactly the shape that decays: one new caller that forgets, and
    /// the file name is the caller's string. Asking again costs a string walk and
    /// makes "Addison only ever writes `<label>.plist` in its own folder" a property
    /// of this function rather than of the discipline of its callers.
    fn plist_path(dir: &Path, label: &str) -> Result<PathBuf, String> {
        if !label_is_valid(label) {
            return Err(NOT_ADDISONS_OWN.to_string());
        }
        Ok(dir.join(format!("{label}.plist")))
    }

    /// Write the document, atomically, and answer with the path it landed at.
    ///
    /// ATOMIC BECAUSE LAUNCHD IS A SECOND READER. A plain `fs::write` truncates first,
    /// so a login (or a `bootstrap` racing an overwrite) can observe a half-written
    /// plist and refuse the job. Writing a neighbouring temp file and renaming means
    /// the folder only ever holds the whole document or the previous one. The temp
    /// name is a dotfile that does not end in `.plist`, so launchd ignores it and
    /// `installed_labels` cannot report it as an automation.
    ///
    /// THE MODE IS SET EXPLICITLY, not left to the umask. This file names a command
    /// that the OS will run as this person, at their login, outside Addison's
    /// sandbox — so a file another local account could WRITE is that account
    /// choosing what runs as this one. `fs::write` takes 0666 minus whatever umask
    /// the process happened to inherit from whoever launched the app, which is not a
    /// thing this module gets to assume. 0644 rather than something tighter because
    /// it is what every other agent in that folder carries, and the property at stake
    /// is entirely in the write bits.
    fn write_plist_atomically(dir: &Path, label: &str, text: &str) -> Result<PathBuf, String> {
        let path = plist_path(dir, label)?;
        fs::create_dir_all(dir).map_err(|_| COULD_NOT_WRITE.to_string())?;
        let temp = dir.join(format!(".{label}.plist.tmp-{}", std::process::id()));
        fs::write(&temp, text).map_err(|_| COULD_NOT_WRITE.to_string())?;
        if fs::set_permissions(&temp, fs::Permissions::from_mode(0o644)).is_err() {
            let _ = fs::remove_file(&temp);
            return Err(COULD_NOT_WRITE.to_string());
        }
        if fs::rename(&temp, &path).is_err() {
            let _ = fs::remove_file(&temp);
            return Err(COULD_NOT_WRITE.to_string());
        }
        Ok(path)
    }

    /// Addison's own installed automations, by label, sorted.
    ///
    /// `label_is_valid` is the filter, so a file somebody else put in the folder — or
    /// one of this module's own temp files — can never become a label on the wire.
    /// That matters more than it sounds: this list is what a surface offers to
    /// REMOVE, and another app's login agent is none of Addison's business.
    ///
    /// A directory named like a plist is skipped as well, because launchd would
    /// ignore it: reporting one would be this surface claiming a job is armed when
    /// nothing can run. A missing folder is "nothing is armed" rather than an error —
    /// a Mac that has never run a user agent has no LaunchAgents directory.
    fn installed_labels(dir: &Path) -> Vec<String> {
        let mut labels: Vec<String> = Vec::new();
        let Ok(entries) = fs::read_dir(dir) else {
            return labels;
        };
        for entry in entries.flatten() {
            if entry.file_type().map(|kind| kind.is_dir()).unwrap_or(true) {
                continue;
            }
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            let Some(stem) = name.strip_suffix(".plist") else { continue };
            if label_is_valid(stem) {
                labels.push(stem.to_string());
            }
        }
        labels.sort();
        labels
    }

    // ===========================================================================
    // launchctl.
    // ===========================================================================

    /// The launchd domain Addison installs into: the calling user's GUI session.
    ///
    /// `gui/<uid>`, never `system/` and never a LaunchDaemon (plan §5.4). A user
    /// agent runs as the person, at their login, with their permissions, and can be
    /// removed by the same person from their own folder.
    fn domain_target() -> String {
        format!("gui/{}", unsafe { libc::getuid() })
    }

    /// One service inside that domain.
    fn service_target(label: &str) -> String {
        format!("{}/{}", domain_target(), label)
    }

    /// What one `launchctl` run produced.
    /// `Debug` carries no secret: an exit code and launchctl's own one-line message.
    #[derive(Debug)]
    struct Ran {
        code: i32,
        detail: String,
    }

    /// `launchctl bootstrap`, and nothing less than success.
    ///
    /// SPLIT FROM `launchctl` BECAUSE THE TWO CALLERS WANT OPPOSITE THINGS. Arming
    /// must fail loudly and clean up after itself; `bootout` is best-effort in both
    /// methods and its ordinary outcome is a non-zero exit ("no such process").
    fn launchctl_must_succeed(args: &[&str]) -> Result<(), String> {
        launchctl_must_succeed_with(LAUNCHCTL, args, LAUNCHCTL_TIMEOUT)
    }

    /// The same, with the binary and the deadline injected.
    ///
    /// THE SEAM EXISTS FOR THE TESTS, and exec.rs's `run_command_with_ceiling` is the
    /// precedent: a deadline asserted only on a pure helper is a deadline nobody has
    /// shown the real path applies, and the only way to prove this one through the
    /// real path is to run something that does not answer. Nothing but a test ever
    /// passes anything other than `LAUNCHCTL` here — arming has one binary.
    fn launchctl_must_succeed_with(
        program: &str,
        args: &[&str],
        deadline: Duration,
    ) -> Result<(), String> {
        let ran = launchctl_with(program, args, deadline)?;
        if ran.code != 0 {
            // The detail is for whoever is reading the app's log, never for the
            // person: launchctl speaks in numbered errnos. No stack trace and no
            // jargon reaches the user (CLAUDE.md).
            eprintln!("[addison] launchctl {args:?} exited {}: {}", ran.code, ran.detail);
            return Err(SCHEDULER_REFUSED.to_string());
        }
        Ok(())
    }

    /// Run `launchctl` with a REAL DEADLINE.
    ///
    /// Every invocation is bounded, because this runs on a task that owes the core an
    /// answer and a hung subprocess would hold it forever. `std::process` has no timed
    /// wait, so the wait happens on its own thread and the parent selects on a
    /// channel — the same shape `exec.rs::run_with_timeout` uses, implemented locally
    /// rather than shared because exec.rs's version is built for arbitrary user
    /// commands (a process group of forking descendants, non-blocking drains, a
    /// capture ceiling) and this one runs a single Apple binary that prints one line.
    /// Reaching into that file to generalise it would put step 5.5's seatbelt path
    /// and this one on the same code for no property either of them gains.
    ///
    /// The kill still goes to the process GROUP: launchctl is not expected to fork,
    /// but a signal to the leader alone is what left orphans holding pipes in exec.rs,
    /// and the correction costs one character.
    fn launchctl(args: &[&str]) -> Result<Ran, String> {
        launchctl_with(LAUNCHCTL, args, LAUNCHCTL_TIMEOUT)
    }

    fn launchctl_with(program: &str, args: &[&str], deadline: Duration) -> Result<Ran, String> {
        let child = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()
            .map_err(|_| SCHEDULER_DID_NOT_ANSWER.to_string())?;
        let pid = child.id();

        let (tx, rx) = mpsc::channel();
        // `wait_with_output` reads both pipes to EOF, which is safe for a command
        // whose whole output is one short line — this is not exec.rs's case, where an
        // arbitrary command can fill a pipe buffer and deadlock.
        std::thread::spawn(move || {
            let _ = tx.send(child.wait_with_output());
        });

        match rx.recv_timeout(deadline) {
            Ok(Ok(out)) => {
                let mut detail = String::from_utf8_lossy(&out.stderr).into_owned();
                detail.push_str(&String::from_utf8_lossy(&out.stdout));
                Ok(Ran { code: out.status.code().unwrap_or(-1), detail: detail.trim().to_string() })
            }
            Ok(Err(_)) => Err(SCHEDULER_DID_NOT_ANSWER.to_string()),
            Err(_) => {
                // SIGKILL the group: past its deadline, and a catchable signal is one
                // a wedged process can ignore. The waiting thread then finishes on its
                // own when the pipes close, so nothing is leaked by not joining it.
                unsafe {
                    libc::kill(-(pid as i32), libc::SIGKILL);
                }
                Err(SCHEDULER_DID_NOT_ANSWER.to_string())
            }
        }
    }

    // ===========================================================================
    #[cfg(test)]
    mod tests {
        use super::*;

        /// NOTHING IN THIS FILE'S TESTS TOUCHES `~/Library/LaunchAgents`, and nothing
        /// runs `launchctl`. Both rules are absolute rather than tidy: a test that
        /// armed a real job would install a recurring job on the machine running the
        /// suite, and `bootstrap` is the one call in Addison whose side effect
        /// outlives the process, the app and the account's next login. So the
        /// coverage is: the pure builders byte-for-byte, the validators class by
        /// class, the path shape, the directory read and the write — the last two
        /// against a temp directory, through the same functions the handler calls,
        /// with the handler's own composition pinned at source level by
        /// `arm_builds_its_path_from_the_real_folder_and_the_validated_label`.
        fn temp_dir(name: &str) -> PathBuf {
            let dir = std::env::temp_dir()
                .join(format!("addison-automation-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).expect("a temp directory for the test");
            fs::canonicalize(&dir).unwrap_or(dir)
        }

        fn interval(minutes: i64) -> Value {
            json!({ "scheduleKind": "interval", "schedule": { "minutes": minutes } })
        }

        // -------------------------------------------------------------------
        // The label.
        // -------------------------------------------------------------------

        #[test]
        fn a_label_that_is_not_addisons_own_is_refused() {
            // EVERY REJECTION CLASS, each with the reason it is a class rather than an
            // example. This is the check that makes "the shell only ever writes its
            // own files in its own folder" true, so it is asserted against the shapes
            // an attacker would actually reach for, not against a typo.
            for (label, why) in [
                ("", "empty"),
                ("tidy", "no prefix at all"),
                ("com.addison.auto.", "prefix with no stem"),
                ("com.addison.autotidy", "prefix without its final dot"),
                ("com.apple.launchd.tidy", "somebody else's reverse-DNS namespace"),
                ("com.addison.auto.tidy.plist", "a dot after the prefix"),
                ("com.addison.auto...", "dots as the whole stem"),
                ("com.addison.auto...%2f..%2fetc", "an escaped separator"),
                ("com.addison.auto./etc/passwd", "an absolute path"),
                ("com.addison.auto.a/b", "a path separator"),
                ("com.addison.auto.a\\b", "a backslash"),
                ("com.addison.auto...ssh", "a leading `..`"),
                ("com.addison.auto.a..b", "`..` in the middle"),
                ("com.addison.auto.Tidy", "an upper-case letter"),
                ("com.addison.auto.-tidy", "a leading hyphen"),
                ("com.addison.auto.ti dy", "a space"),
                ("com.addison.auto.ti\0dy", "a NUL"),
                ("com.addison.auto.ti\ndy", "a newline"),
                ("com.addison.auto.tidý", "a non-ASCII letter"),
                (" com.addison.auto.tidy", "leading whitespace before the prefix"),
                ("x.com.addison.auto.tidy", "the prefix buried mid-string"),
                ("com.addison.auto.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "41 characters"),
            ] {
                assert!(
                    !label_is_valid(label),
                    "the label {label:?} must be refused ({why})"
                );
                // ...and the refusal is the answer the core gets, not a panic and not
                // a JSON-RPC error: the whole surface answers {ok:false, error}.
                let refusal = arm(&json!({
                    "label": label, "command": "echo hi",
                    "scheduleKind": "interval", "schedule": { "minutes": 30 }
                }))
                .expect("a refusal is an answer, never an error frame");
                assert_eq!(refusal["ok"].as_bool(), Some(false), "arm must refuse {label:?}");
                assert_eq!(refusal["error"].as_str(), Some(NOT_ADDISONS_OWN));
                let refusal = disarm(&json!({ "label": label })).unwrap();
                assert_eq!(refusal["ok"].as_bool(), Some(false), "disarm must refuse {label:?}");
            }
            // A missing label is the same refusal, not a panic.
            assert_eq!(arm(&json!({})).unwrap()["ok"].as_bool(), Some(false));
            assert_eq!(disarm(&json!({})).unwrap()["ok"].as_bool(), Some(false));
            assert_eq!(disarm(&json!({ "label": 7 })).unwrap()["ok"].as_bool(), Some(false));
        }

        #[test]
        fn the_label_form_the_core_mints_is_accepted() {
            // POSITIVE CONTROL for the test above: a validator that refuses everything
            // passes every rejection class and breaks arming entirely. These are the
            // exact shapes `automations.derive_label` produces — a slug, a slug with a
            // uniqueness suffix, digits, and the longest stem it will ever mint.
            for label in [
                "com.addison.auto.tidy",
                "com.addison.auto.nightly-backup",
                "com.addison.auto.nightly-backup-2",
                "com.addison.auto.zalohovani-99",
                "com.addison.auto.7",
                "com.addison.auto.a",
                "com.addison.auto.tidy-",
                "com.addison.auto.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", // 40
            ] {
                assert!(label_is_valid(label), "{label:?} is a label Addison itself mints");
            }
        }

        // -------------------------------------------------------------------
        // The document.
        // -------------------------------------------------------------------

        #[test]
        fn the_interval_plist_is_byte_identical_to_the_cores_preview() {
            // THE WHOLE STRING, not a substring, and that is the point: the preview the
            // person read before typing the nonce is the CORE's rendering
            // (`agent_core/automations.py::plist_text`) and the file that lands in
            // LaunchAgents is this one. Anything less than byte equality makes "the
            // preview you approved" a phrase about two different documents. The
            // expected text below was produced by running the Python on the same
            // inputs; a lockstep test compares the two sides mechanically.
            //
            // The ×60 is inside this path (`trigger_from`), so a mutation to it —
            // `* 30`, `* 1` — changes this string. That is the mutation the Python's
            // own test was written for: a job at twice the frequency the person
            // approved.
            let trigger = trigger_from(&interval(30)).expect("30 minutes is a schedule");
            let text = plist_text("com.addison.auto.tidy", "echo hi", &trigger);
            assert_eq!(
                text,
                concat!(
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
                    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n",
                    "<plist version=\"1.0\">\n",
                    "<dict>\n",
                    "    <key>Label</key>\n",
                    "    <string>com.addison.auto.tidy</string>\n",
                    "    <key>ProgramArguments</key>\n",
                    "    <array>\n",
                    "        <string>/bin/sh</string>\n",
                    "        <string>-c</string>\n",
                    "        <string>echo hi</string>\n",
                    "    </array>\n",
                    "    <key>StartInterval</key>\n",
                    "    <integer>1800</integer>\n",
                    "</dict>\n",
                    "</plist>\n",
                )
            );
        }

        #[test]
        fn the_calendar_plist_is_byte_identical_to_the_cores_preview() {
            // `StartCalendarInterval` is a dict of Hour/Minute/Weekday integers, and
            // THE KEY SPELLINGS ARE LOAD-BEARING: launchd ignores a key it does not
            // recognise, so a typo produces a plist that loads cleanly and never fires
            // — the failure that looks like success until somebody notices nothing
            // ran. Weekday rides through as the stored number (0 = Sunday, launchd's
            // own convention, which is why the core stores it that way).
            let trigger = trigger_from(&json!({
                "scheduleKind": "calendar",
                "schedule": { "hour": 7, "minute": 30, "weekday": 1 }
            }))
            .expect("Monday at 7:30 is a schedule");
            let text = plist_text("com.addison.auto.tidy", "echo hi", &trigger);
            assert_eq!(
                text,
                concat!(
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
                    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n",
                    "<plist version=\"1.0\">\n",
                    "<dict>\n",
                    "    <key>Label</key>\n",
                    "    <string>com.addison.auto.tidy</string>\n",
                    "    <key>ProgramArguments</key>\n",
                    "    <array>\n",
                    "        <string>/bin/sh</string>\n",
                    "        <string>-c</string>\n",
                    "        <string>echo hi</string>\n",
                    "    </array>\n",
                    "    <key>StartCalendarInterval</key>\n",
                    "    <dict>\n",
                    "        <key>Hour</key>\n",
                    "        <integer>7</integer>\n",
                    "        <key>Minute</key>\n",
                    "        <integer>30</integer>\n",
                    "        <key>Weekday</key>\n",
                    "        <integer>1</integer>\n",
                    "    </dict>\n",
                    "</dict>\n",
                    "</plist>\n",
                )
            );
        }

        #[test]
        fn a_daily_plist_carries_no_weekday_at_all() {
            // An omitted weekday means EVERY day, and the way launchd is told that is
            // by the key being absent — a `Weekday` present with any value pins the
            // job to one day. So the difference between "every day at 7:05" and "every
            // Sunday at 7:05" is one key existing. The minute is NOT zero-padded here
            // (`<integer>5</integer>`), matching the Python exactly: a plist integer
            // is a number, and the padded form only belongs in the sentence a person
            // reads.
            let trigger = trigger_from(&json!({
                "scheduleKind": "calendar", "schedule": { "hour": 7, "minute": 5 }
            }))
            .expect("7:05 every day is a schedule");
            let text = plist_text("com.addison.auto.tidy", "echo hi", &trigger);
            assert_eq!(
                text,
                concat!(
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
                    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n",
                    "<plist version=\"1.0\">\n",
                    "<dict>\n",
                    "    <key>Label</key>\n",
                    "    <string>com.addison.auto.tidy</string>\n",
                    "    <key>ProgramArguments</key>\n",
                    "    <array>\n",
                    "        <string>/bin/sh</string>\n",
                    "        <string>-c</string>\n",
                    "        <string>echo hi</string>\n",
                    "    </array>\n",
                    "    <key>StartCalendarInterval</key>\n",
                    "    <dict>\n",
                    "        <key>Hour</key>\n",
                    "        <integer>7</integer>\n",
                    "        <key>Minute</key>\n",
                    "        <integer>5</integer>\n",
                    "    </dict>\n",
                    "</dict>\n",
                    "</plist>\n",
                )
            );
            assert!(!text.contains("Weekday"));
            // One schedule, one trigger — never both.
            assert!(!text.replace("StartCalendarInterval", "").contains("StartInterval"));
        }

        #[test]
        fn a_hostile_command_cannot_become_document_structure() {
            // THE ONE THAT MATTERS. A command is text inside a `<string>`; a command
            // that CONTAINS `</string>` must stay text. Unescaped, the payload below
            // closes the element early and the document grows a `RunAtLoad` key — the
            // one key plan §5.7 says is never set, because it makes arming cause an
            // immediate run. The person would then have approved a preview describing
            // one job and armed another.
            //
            // The command is attacker-adjacent by construction: it comes from a model
            // that may be relaying instructions it read on a web page. The label is
            // asserted through the same escape for the same reason, even though
            // `label_is_valid` already makes an unescapable character unreachable —
            // the escaping must not be the thing that is load-bearing only until
            // somebody widens the alphabet.
            let hostile = "echo a && b </string><key>RunAtLoad</key><true/><string> > c";
            let trigger = trigger_from(&interval(30)).unwrap();
            let text = plist_text("com.addison.auto.tidy", hostile, &trigger);

            assert!(!text.contains("<key>RunAtLoad</key>"), "the payload became structure:\n{text}");
            assert!(!text.contains("<true/>"));
            assert!(text.contains("&lt;key&gt;RunAtLoad&lt;/key&gt;"), "escaped, not dropped");
            assert!(text.contains("echo a &amp;&amp; b"), "& must escape before < and >");
            assert!(text.contains("&gt; c"), "a redirect is text, not a closing bracket");
            // The escape is asked of the label too, through the same function.
            assert_eq!(xml_escape("a&b<c>d"), "a&amp;b&lt;c&gt;d");
        }

        #[test]
        fn the_plist_never_sets_run_at_load() {
            // PINNED BY NAME, on every trigger shape, because it is a whole floor
            // stated as the absence of five words: `RunAtLoad` present (with any
            // value, true or false — launchd reads `<false/>` as a key it recognises
            // and a future edit to "just make it explicit" is exactly the shape this
            // catches) would make arming itself run the job, and "Addison never
            // triggers itself" would stop being true at the one moment it is easiest
            // to miss.
            for trigger in [
                trigger_from(&interval(1)).unwrap(),
                trigger_from(&interval(10_080)).unwrap(),
                trigger_from(&json!({
                    "scheduleKind": "calendar", "schedule": { "hour": 0, "minute": 0 }
                }))
                .unwrap(),
                trigger_from(&json!({
                    "scheduleKind": "calendar",
                    "schedule": { "hour": 23, "minute": 59, "weekday": 6 }
                }))
                .unwrap(),
            ] {
                let text = plist_text("com.addison.auto.tidy", "echo hi", &trigger);
                assert!(
                    !text.contains("RunAtLoad"),
                    "arming must never cause an immediate run:\n{text}"
                );
                // A document, not a fragment: without the declaration and the DOCTYPE
                // `plutil` rejects the file and launchd never reads it.
                assert!(text.starts_with("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC "));
                assert!(text.trim_end().ends_with("</plist>"));
                // And the one shell dialect, everywhere in Addison.
                assert!(text.contains("<string>/bin/sh</string>\n        <string>-c</string>"));
            }
        }

        // -------------------------------------------------------------------
        // The bounds, checked again on this side.
        // -------------------------------------------------------------------

        #[test]
        fn a_schedule_out_of_range_is_refused_even_though_the_core_also_checks() {
            // THE CORE CHECKING FIRST IS NOT A REASON TO SKIP THIS, and the failure
            // mode says why: launchd does not refuse an out-of-range trigger, it
            // ignores it. `Hour: 99` loads cleanly and never fires — a job that sits
            // in the folder looking armed while nothing ever runs, which is worse than
            // a refusal because nobody finds out.
            //
            // Every arm is asserted through the PUBLIC handler as well, so the bound
            // is proven where the frame arrives rather than only on the helper.
            for (schedule, sentence, why) in [
                (json!({ "minutes": 0 }), NEEDS_MINUTES, "zero minutes is not a gap"),
                (json!({ "minutes": -5 }), NEEDS_MINUTES, "a negative gap"),
                (json!({ "minutes": true }), NEEDS_MINUTES, "true is not a number of minutes"),
                (json!({ "minutes": 1.5 }), NEEDS_MINUTES, "a fraction of a minute"),
                (json!({ "minutes": "30" }), NEEDS_MINUTES, "text that looks like a number"),
                (json!({}), NEEDS_MINUTES, "no minutes at all"),
                (json!({ "minutes": i64::MAX }), NEEDS_MINUTES, "×60 would overflow"),
            ] {
                let params = json!({ "scheduleKind": "interval", "schedule": schedule });
                assert_eq!(
                    trigger_from(&params).unwrap_err(),
                    sentence,
                    "an interval schedule must be refused: {why}"
                );
            }
            for (schedule, sentence, why) in [
                (json!({ "hour": 24, "minute": 0 }), NEEDS_HOUR, "hours run 0-23"),
                (json!({ "hour": -1, "minute": 0 }), NEEDS_HOUR, "a negative hour"),
                (json!({ "minute": 0 }), NEEDS_HOUR, "no hour at all"),
                (json!({ "hour": 7, "minute": 60 }), NEEDS_MINUTE, "minutes run 0-59"),
                (json!({ "hour": 7, "minute": -1 }), NEEDS_MINUTE, "a negative minute"),
                (json!({ "hour": 7 }), NEEDS_MINUTE, "no minute at all"),
                (
                    json!({ "hour": 7, "minute": 0, "weekday": 7 }),
                    NEEDS_WEEKDAY,
                    "weekdays run 0-6 here, though launchd would take 7",
                ),
                (
                    json!({ "hour": 7, "minute": 0, "weekday": -1 }),
                    NEEDS_WEEKDAY,
                    "a negative weekday",
                ),
                (
                    json!({ "hour": 7, "minute": 0, "weekday": "monday" }),
                    NEEDS_WEEKDAY,
                    "a named day",
                ),
            ] {
                let params = json!({ "scheduleKind": "calendar", "schedule": schedule });
                assert_eq!(
                    trigger_from(&params).unwrap_err(),
                    sentence,
                    "a calendar schedule must be refused: {why}"
                );
            }
            // The kind itself is a CLOSED vocabulary, and an unknown one is refused
            // rather than waved through — the safe direction for a closed set to fail
            // in, and the same rule `automations.schedule_problem` states.
            for kind in [json!("cron"), json!("once"), json!(""), json!(null), json!(7)] {
                let params = json!({ "scheduleKind": kind, "schedule": { "minutes": 30 } });
                assert_eq!(trigger_from(&params).unwrap_err(), NEEDS_A_KIND);
            }
            // A missing `schedule` object is refused by its kind's own sentence, not
            // by a panic.
            assert_eq!(
                trigger_from(&json!({ "scheduleKind": "interval" })).unwrap_err(),
                NEEDS_MINUTES
            );
            assert_eq!(
                trigger_from(&json!({ "scheduleKind": "calendar", "schedule": "7am" })).unwrap_err(),
                NEEDS_HOUR
            );

            // THROUGH THE HANDLER, with a label and command that are otherwise
            // perfectly good: the refusal must arrive as {ok:false, error} and nothing
            // may reach the disk on the way.
            let refused = arm(&json!({
                "label": "com.addison.auto.tidy", "command": "echo hi",
                "scheduleKind": "calendar", "schedule": { "hour": 99, "minute": 0 }
            }))
            .unwrap();
            assert_eq!(refused["ok"].as_bool(), Some(false));
            assert_eq!(refused["error"].as_str(), Some(NEEDS_HOUR));
        }

        #[test]
        fn the_schedule_the_core_sends_is_accepted() {
            // POSITIVE CONTROL for the bounds above — a parser that refused everything
            // would pass every one of them. The edges are included deliberately: the
            // bounds are inclusive, and an off-by-one at 0 or 23 is exactly what a
            // "refuse out of range" edit gets wrong.
            assert_eq!(
                trigger_from(&interval(1)).unwrap(),
                Trigger::Interval { seconds: 60 }
            );
            assert_eq!(
                trigger_from(&interval(10_080)).unwrap(),
                Trigger::Interval { seconds: 604_800 }
            );
            assert_eq!(
                trigger_from(&json!({
                    "scheduleKind": "calendar", "schedule": { "hour": 0, "minute": 0 }
                }))
                .unwrap(),
                Trigger::Calendar { hour: 0, minute: 0, weekday: None }
            );
            assert_eq!(
                trigger_from(&json!({
                    "scheduleKind": "calendar",
                    "schedule": { "hour": 23, "minute": 59, "weekday": 0 }
                }))
                .unwrap(),
                Trigger::Calendar { hour: 23, minute: 59, weekday: Some(0) }
            );
            // An explicit null weekday is ABSENT, not wrong: a caller may send the
            // whole optional field set without pruning it.
            assert_eq!(
                trigger_from(&json!({
                    "scheduleKind": "calendar",
                    "schedule": { "hour": 7, "minute": 30, "weekday": null }
                }))
                .unwrap(),
                Trigger::Calendar { hour: 7, minute: 30, weekday: None }
            );
        }

        #[test]
        fn a_command_this_process_cannot_put_in_a_plist_is_refused() {
            // Empty is refused because a plist that runs nothing is not a schedule.
            // Control characters are refused rather than escaped: XML 1.0 cannot
            // express them, so the file would be one `plutil` rejects and launchd
            // never reads — armed-looking, never running.
            for (command, why) in [
                (json!(""), "nothing at all"),
                (json!("   \n "), "whitespace only"),
                (json!(null), "a missing command"),
                (json!(42), "a command that is not text"),
                (json!("echo \u{0}hi"), "a NUL"),
                (json!("echo \u{7}hi"), "a bell character"),
                (json!("echo \u{1b}[31m"), "an escape sequence"),
            ] {
                let params = json!({ "command": command });
                assert!(
                    command_from(&params).is_err(),
                    "a command must be refused: {why}"
                );
            }
            let long = "x".repeat(MAX_COMMAND_BYTES + 1);
            assert_eq!(
                command_from(&json!({ "command": long })).unwrap_err(),
                COMMAND_TOO_LONG
            );
            // POSITIVE CONTROL: the shapes a real command takes, including the
            // whitespace XML can carry and the characters the escaping exists for.
            for command in [
                "echo hi",
                "rsync -a ~/Documents /Volumes/Backup && echo done",
                "cd /tmp\nls",
                "echo 'a\tb'",
                "echo \"quoted\"",
            ] {
                assert!(
                    command_from(&json!({ "command": command })).is_ok(),
                    "{command:?} is an ordinary command"
                );
            }
        }

        // -------------------------------------------------------------------
        // The one directory, and the one file in it.
        // -------------------------------------------------------------------

        #[test]
        fn the_install_path_is_always_one_plist_inside_launch_agents() {
            // The path is built from a directory this module derived and a label this
            // module validated — so it cannot leave the folder, and it cannot be
            // anything but `<label>.plist`. Asserted against the escapes as well as
            // the happy case: `plist_path` refuses them ITSELF rather than trusting
            // that every caller validated first.
            let dir = PathBuf::from("/Users/someone/Library/LaunchAgents");
            assert_eq!(
                plist_path(&dir, "com.addison.auto.tidy").unwrap(),
                PathBuf::from("/Users/someone/Library/LaunchAgents/com.addison.auto.tidy.plist")
            );
            for escape in [
                "com.addison.auto./../../etc/cron.d/x",
                "com.addison.auto.a/b",
                "../../Library/LaunchDaemons/root",
                "/etc/passwd",
                "com.addison.auto.a\0b",
            ] {
                assert!(
                    plist_path(&dir, escape).is_err(),
                    "{escape:?} must never become a path"
                );
            }
            // And the folder itself is derived from HOME, never from a frame.
            let real = launch_agents_dir().expect("HOME is set when the tests run");
            assert!(real.is_absolute());
            assert!(real.ends_with("Library/LaunchAgents"), "{real:?}");
            assert_eq!(
                plist_path(&real, "com.addison.auto.tidy").unwrap().file_name().unwrap(),
                "com.addison.auto.tidy.plist"
            );
        }

        #[test]
        fn arm_builds_its_path_from_the_real_folder_and_the_validated_label() {
            // THE WIRING, not the helpers. Everything above tests a function the
            // handler calls; none of it can see whether the handler still calls them,
            // or in what order. That is trap 3 from docs/HANDOFF.md — purifying a
            // function for testability moves the untested part to its caller — and it
            // lands here harder than usual, because the only test that COULD exercise
            // the real composition is one that installs a launchd job on whatever
            // machine runs the suite, which this file will never do.
            //
            // So the composition is pinned at source level, coarsely: it asserts ORDER
            // (validate, then build a path, then write, then hand it to launchd), which
            // is the property, and not the surrounding shape, which is free to change.
            let source = include_str!("automation.rs");
            let start = source.find("fn arm_inner").expect("arm_inner must exist");
            let body = &source[start..];
            let end = body.find("\n    }\n").expect("arm_inner must be a closed function");
            let body = &body[..end];

            let validated = body.find("validated_label(").expect(
                "arm_inner must validate the label ITSELF — the core is not trusted for \
                 the name of a file this process writes",
            );
            let folder = body.find("launch_agents_dir()").expect(
                "arm_inner must derive the folder from HOME, never take one off the wire",
            );
            let written = body.find("write_plist_atomically(").expect(
                "arm_inner must write through the atomic writer, which is what re-checks \
                 the label and builds the only path this module ever writes",
            );
            // The ARGUMENT, not the word: `arm_inner`'s comments say "bootstrap" too,
            // and a source test that matches prose measures the prose (the same
            // correction `agent_process.rs`'s pump test carries).
            let handed_over = body.find("\"bootstrap\"").expect(
                "arm_inner must hand the installed file to launchd, or nothing is armed",
            );
            assert!(
                validated < folder && folder < written && written < handed_over,
                "nothing may touch the disk before the label, command and schedule are \
                 validated:\n{body}"
            );
            // And the failure path puts the file back out: a plist left behind is armed
            // at the next login whatever launchctl answered today.
            let cleanup = body.find("remove_file").expect(
                "a failed arm must remove the plist it just wrote — otherwise a job the \
                 scheduler refused today is armed at the next login",
            );
            assert!(cleanup > handed_over, "the cleanup belongs on the failure path:\n{body}");
        }

        #[test]
        fn the_plist_is_written_atomically_and_lands_under_its_own_name() {
            // The write, exercised through the same function the handler calls, against
            // a TEMP DIRECTORY — the seam is `write_plist_atomically`'s `dir`
            // parameter, which exists so this test never goes near
            // `~/Library/LaunchAgents`. `arm_inner` passes `launch_agents_dir()` into
            // that same parameter, and the source pin above is what holds that true.
            let dir = temp_dir("write");
            let trigger = trigger_from(&interval(30)).unwrap();
            let text = plist_text("com.addison.auto.tidy", "echo hi", &trigger);

            let path = write_plist_atomically(&dir, "com.addison.auto.tidy", &text).unwrap();
            assert_eq!(path, dir.join("com.addison.auto.tidy.plist"));
            assert_eq!(fs::read_to_string(&path).unwrap(), text);

            // Overwriting is how re-arming works, and it must not leave the folder
            // holding a half-written document or a stray temp file.
            let second = plist_text("com.addison.auto.tidy", "echo again", &trigger);
            write_plist_atomically(&dir, "com.addison.auto.tidy", &second).unwrap();
            assert_eq!(fs::read_to_string(&path).unwrap(), second);
            let strays: Vec<String> = fs::read_dir(&dir)
                .unwrap()
                .flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|name| name != "com.addison.auto.tidy.plist")
                .collect();
            assert!(strays.is_empty(), "the write left something behind: {strays:?}");

            // A missing folder is created — a Mac that has never run a user agent has
            // no LaunchAgents directory, and arming is the moment it should appear.
            let fresh = dir.join("nested/deeper");
            assert!(write_plist_atomically(&fresh, "com.addison.auto.tidy", &text).is_ok());
            assert!(fresh.join("com.addison.auto.tidy.plist").exists());

            // And the label is re-checked at the writer, not only at the door.
            assert!(write_plist_atomically(&dir, "com.evil.auto.x", &text).is_err());
            assert!(write_plist_atomically(&dir, "com.addison.auto.a/b", &text).is_err());

            let _ = fs::remove_dir_all(&dir);
        }

        #[test]
        fn nobody_else_can_rewrite_what_this_person_agreed_to_run() {
            // The plist names a command the OS runs AS THIS PERSON, at their login,
            // outside Addison's sandbox. A file another local account can write is
            // that account choosing what runs as this one — so the mode is SET rather
            // than inherited from whatever umask launched the app.
            //
            // MEASURED WITHOUT TOUCHING THE PROCESS UMASK, deliberately. Blanking the
            // umask would prove the same thing and would do it by mutating
            // process-global state while the rest of the suite runs in parallel
            // threads — the exact shape exec.rs records as its worst flake. A leftover
            // temp file works instead: `fs::write` truncates an existing file and
            // KEEPS its mode, so a 0666 leftover is a 0666 plist unless something
            // sets the mode. It also proves the second property that comes with the
            // temp file — a crashed earlier run leaves one behind, and the next arm
            // must not inherit anything from it.
            let dir = temp_dir("mode");
            let text = plist_text(
                "com.addison.auto.tidy",
                "echo hi",
                &trigger_from(&interval(30)).unwrap(),
            );
            let leftover =
                dir.join(format!(".com.addison.auto.tidy.plist.tmp-{}", std::process::id()));
            fs::write(&leftover, b"from a run that died").unwrap();
            fs::set_permissions(&leftover, fs::Permissions::from_mode(0o666)).unwrap();

            let path = write_plist_atomically(&dir, "com.addison.auto.tidy", &text).unwrap();
            assert_eq!(fs::read_to_string(&path).unwrap(), text, "the leftover's bytes survived");
            let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o644, "the plist came out {mode:o}");
            assert_eq!(mode & 0o022, 0, "group or world could rewrite the armed command");
            let _ = fs::remove_dir_all(&dir);
        }

        #[test]
        fn launchd_never_sees_half_a_plist() {
            // THE ATOMICITY IS MEASURED, not asserted by shape. The test above cannot
            // see it: a plain `fs::write` lands the same bytes at the same path and
            // leaves no stray file, so it passes with the temp-and-rename removed —
            // the mutation that produced this test.
            //
            // What is actually at stake: this folder has a SECOND READER that Addison
            // does not coordinate with. launchd reads it at every login, and
            // `bootstrap` reads it milliseconds after the write. `fs::write` truncates
            // to zero and then fills, so either reader can observe a prefix of the
            // document — and a truncated plist is not a plist launchd will load. So a
            // reader watches the path while the writer replaces it, and EVERY
            // observation has to be a whole document.
            let dir = temp_dir("atomic");
            let path = dir.join("com.addison.auto.tidy.plist");
            let trigger = trigger_from(&interval(30)).unwrap();
            // Big enough that a truncating write is several syscalls wide; a document
            // that fits in one is atomic by accident, which proves nothing.
            let first = plist_text("com.addison.auto.tidy", &"a".repeat(400_000), &trigger);
            let second = plist_text("com.addison.auto.tidy", &"b".repeat(600_000), &trigger);
            write_plist_atomically(&dir, "com.addison.auto.tidy", &first).unwrap();

            let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let torn = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let seen = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let watcher = {
                let (stop, torn, seen) = (stop.clone(), torn.clone(), seen.clone());
                let (path, first, second) = (path.clone(), first.clone(), second.clone());
                std::thread::spawn(move || {
                    use std::sync::atomic::Ordering::Relaxed;
                    while !stop.load(Relaxed) {
                        if let Ok(text) = fs::read_to_string(&path) {
                            seen.fetch_add(1, Relaxed);
                            if text != first && text != second {
                                torn.fetch_add(1, Relaxed);
                            }
                        }
                    }
                })
            };
            for turn in 0..60 {
                let text = if turn % 2 == 0 { &second } else { &first };
                write_plist_atomically(&dir, "com.addison.auto.tidy", text).unwrap();
            }
            stop.store(true, std::sync::atomic::Ordering::Relaxed);
            watcher.join().unwrap();

            use std::sync::atomic::Ordering::Relaxed;
            // POSITIVE CONTROL: a reader that never managed to read anything would
            // report zero torn documents just as happily.
            assert!(seen.load(Relaxed) > 100, "the watcher barely read the file; this proved nothing");
            assert_eq!(
                torn.load(Relaxed),
                0,
                "launchd could read a half-written plist ({} of {} reads)",
                torn.load(Relaxed),
                seen.load(Relaxed)
            );
            let _ = fs::remove_dir_all(&dir);
        }

        #[test]
        fn only_addisons_own_plists_are_reported_as_armed() {
            // `listArmed` answers what is installed by reading the folder. Everything
            // in it that is not one of Addison's own files must be invisible: other
            // apps' agents are none of Addison's business and must never appear on a
            // surface that offers to REMOVE what it lists.
            let dir = temp_dir("list");
            for name in [
                "com.addison.auto.tidy.plist",
                "com.addison.auto.backup.plist",
                "com.apple.something.plist",         // somebody else's agent
                "com.addison.auto.Tidy.plist",       // not a label this validator accepts
                "com.addison.auto.tidy.plist.bak",   // not a plist
                "com.addison.auto.tidy",             // no extension
                ".com.addison.auto.tidy.plist.tmp-1", // one of this module's own temp files
                "notes.txt",
            ] {
                fs::write(dir.join(name), b"x").unwrap();
            }
            fs::create_dir(dir.join("com.addison.auto.folder.plist")).unwrap();

            assert_eq!(
                installed_labels(&dir),
                vec!["com.addison.auto.backup".to_string(), "com.addison.auto.tidy".to_string()],
                "only Addison's own labels, sorted — and never the directory"
            );

            // A folder that is not there is "nothing is armed", never an error: this
            // answers a read-only surface that loads on every visit.
            assert!(installed_labels(&dir.join("gone")).is_empty());
            let _ = fs::remove_dir_all(&dir);
        }

        #[test]
        fn list_armed_answers_the_wire_shape_without_touching_launchctl() {
            // The contract, on the real handler: `{armed: [...], supported}`. Reading
            // the folder is a read — this test installs nothing, removes nothing and
            // runs no subprocess.
            let answer = list_armed(&json!({})).unwrap();
            assert_eq!(answer["supported"].as_bool(), Some(true), "this is a Mac");
            let armed = answer["armed"].as_array().expect("armed is always a list");
            for label in armed {
                assert!(
                    label_is_valid(label.as_str().unwrap_or_default()),
                    "listArmed may only ever report labels Addison itself minted"
                );
            }
            let keys: std::collections::BTreeSet<&str> =
                answer.as_object().unwrap().keys().map(String::as_str).collect();
            assert_eq!(keys, ["armed", "supported"].into_iter().collect());
        }

        #[test]
        fn without_a_home_folder_nothing_is_written_at_all() {
            // THIS GUARD POINTS THE OPPOSITE WAY FROM exec.rs's, on purpose. There, an
            // absent `HOME` falls back to `/` so the seatbelt's `~/`-prefixed denies
            // stay absolute and the floor keeps its full length. Here the same
            // fallback would aim a WRITE at `/Library/LaunchAgents` — the system-wide
            // folder, every account's login, root's domain — which plan §5.4 says no
            // phase may ever touch. When the fallback and the effect point in opposite
            // directions the answer is to refuse.
            for home in ["", "   ", "relative/path", "~"] {
                assert_eq!(
                    launch_agents_dir_of(home).unwrap_err(),
                    NO_HOME_FOLDER,
                    "{home:?} is not a home folder Addison will write under"
                );
            }
            // POSITIVE CONTROL: a real home resolves to the one folder this module
            // owns, and to nothing above or beside it.
            assert_eq!(
                launch_agents_dir_of("/Users/someone").unwrap(),
                PathBuf::from("/Users/someone/Library/LaunchAgents")
            );
        }

        #[test]
        fn a_failed_replace_says_the_old_schedule_went_too() {
            // THE ONE LIE THAT MATTERS. Arming replaces in place: the previous copy
            // is unloaded before the new one is bootstrapped, so a bootstrap that
            // fails leaves the person with NEITHER — while `SCHEDULER_REFUSED` and
            // `SCHEDULER_DID_NOT_ANSWER` both promise "Addison set nothing up". A
            // person who believes that goes on believing a job is running.
            //
            // Mutation: return `problem` unconditionally from `arm_inner`'s failure
            // branch (drop the `replacing` arm).
            assert!(REPLACED_AND_FAILED.contains("switched off first"));
            assert!(REPLACED_AND_FAILED.contains("nothing is \nrunning now")
                || REPLACED_AND_FAILED.contains("nothing is running now"));
            // ...and it must NOT make the claim the other two make.
            assert!(!REPLACED_AND_FAILED.contains("set nothing up"));
            assert!(SCHEDULER_REFUSED.contains("set nothing up"));
            // The CHOICE itself, exercised rather than read: a fresh arm keeps the
            // scheduler's own sentence, a replace swaps in the honest one.
            assert_eq!(
                arm_failure(false, SCHEDULER_REFUSED.to_string()),
                SCHEDULER_REFUSED
            );
            assert_eq!(
                arm_failure(true, SCHEDULER_REFUSED.to_string()),
                REPLACED_AND_FAILED
            );
            assert_eq!(
                arm_failure(true, SCHEDULER_DID_NOT_ANSWER.to_string()),
                REPLACED_AND_FAILED
            );

            // ...and `arm_inner` reads existence BEFORE writing, which is the only
            // point at which "was something already here" is knowable.
            let source = include_str!("automation.rs");
            // BOUNDED TO THE FUNCTION. `split(..).nth(1)` runs to end-of-file, which
            // includes this test — so an assertion looking for its own literal found
            // itself and passed with the mutation applied. (The same self-referential
            // shape the phase-2 review found in `plist_text`'s only test; it took a
            // surviving mutation here to notice it a second time.)
            let body = source
                .split("fn arm_inner(")
                .nth(1)
                .and_then(|rest| rest.split("\n    /// Which sentence").next())
                .expect("arm_inner exists and is followed by arm_failure's doc");
            let replacing_at = body.find("let replacing").expect("arm_inner reads existence");
            let write_at = body.find("write_plist_atomically").expect("arm_inner writes");
            assert!(
                replacing_at < write_at,
                "arm_inner must read whether a plist exists BEFORE overwriting it"
            );
            // MATCH THE CALL, NEVER THE WORD (docs/HANDOFF.md, trap 3). The first
            // two attempts at this assertion matched `arm_failure(replacing` — which
            // appears in this test's own text, and then in the function's own
            // SIGNATURE — so the mutation survived twice. The bound above ends at
            // `arm_failure`'s doc comment, and the needle is the return expression.
            assert!(
                body.contains("Err(arm_failure(replacing, problem))"),
                "arm_inner must route its failure through arm_failure"
            );
        }

        #[test]
        fn a_scheduler_that_cannot_be_asked_to_stop_is_not_reported_as_stopped() {
            // `bootout` exits non-zero for "no such process" — the ordinary case, and
            // one this method promises to treat as already-done. An `Err` is a
            // different thing: the process could not be spawned or blew the deadline,
            // so Addison does not KNOW whether the job is loaded. Reporting success
            // there told somebody "your computer won't run it any more" while launchd
            // held it for the rest of the session — and `list_armed` could not
            // contradict it, because the file was already gone.
            //
            // The two shapes, through the same seam the timeout test uses:
            let no_answer = launchctl_with("/bin/sleep", &["30"], Duration::from_millis(400));
            assert!(no_answer.is_err(), "a wedged scheduler must be an Err");
            let refused = launchctl_with("/usr/bin/false", &[], Duration::from_secs(5))
                .expect("a program that exits non-zero still ANSWERED");
            assert_ne!(refused.code, 0, "a non-zero exit is an answer, not a failure to answer");

            // And the source rule: the file removal must sit behind the Err check.
            let source = include_str!("automation.rs");
            let body = source
                .split("fn disarm_inner(")
                .nth(1)
                .expect("disarm_inner exists");
            let guard_at = body.find("COULD_NOT_ASK_TO_STOP").expect("disarm_inner guards");
            let remove_at = body.find("fs::remove_file").expect("disarm_inner removes");
            assert!(
                guard_at < remove_at,
                "disarm must refuse an unanswered scheduler BEFORE removing the file"
            );
        }

        #[test]
        fn a_scheduler_that_never_answers_does_not_hold_the_task_forever() {
            // THE ASSERTION THAT MATTERS IS THE CLOCK. Every `launchctl` call happens
            // on a task that owes the core an answer, so a wedged one holds that task
            // — and a deadline asserted only by reading the code is a deadline nobody
            // has shown fires. `/bin/sleep` stands in for a launchctl that never
            // returns; the seam is `launchctl_with`'s program + deadline parameters,
            // which nothing but a test ever passes anything else through.
            let started = std::time::Instant::now();
            let outcome = launchctl_with("/bin/sleep", &["30"], Duration::from_millis(600));
            let elapsed = started.elapsed();
            assert!(
                elapsed < Duration::from_secs(5),
                "a hung scheduler held the handler for {elapsed:?}"
            );
            assert_eq!(outcome.err(), Some(SCHEDULER_DID_NOT_ANSWER.to_string()));

            // POSITIVE CONTROL, three ways: returning fast is trivially true of a
            // spawn that never happened. A command that answers must have its exit
            // code and its output read, and a non-zero exit must become the refusal
            // that makes `arm_inner` clean up after itself.
            let ran = launchctl_with("/bin/echo", &["hi"], Duration::from_secs(5)).unwrap();
            assert_eq!(ran.code, 0);
            assert_eq!(ran.detail, "hi");
            assert!(launchctl_must_succeed_with("/bin/echo", &["hi"], Duration::from_secs(5)).is_ok());
            assert_eq!(
                launchctl_must_succeed_with(
                    "/bin/sh",
                    &["-c", "echo nope >&2; exit 3"],
                    Duration::from_secs(5)
                )
                .unwrap_err(),
                SCHEDULER_REFUSED,
                "a scheduler that refuses the job must not read as success"
            );
            // A binary that is not there is refused, not a panic — and it is the same
            // plain sentence, because there is nothing different for a person to do.
            assert_eq!(
                launchctl_with("/nonexistent/launchctl", &[], Duration::from_secs(5)).unwrap_err(),
                SCHEDULER_DID_NOT_ANSWER
            );
        }

        #[test]
        fn the_launchd_domain_is_the_users_own_gui_session() {
            // `gui/<uid>` and nothing else: never `system/`, which is root's domain and
            // where a LaunchDaemon would live (plan §5.4 — no phase may ever touch it).
            let uid = unsafe { libc::getuid() };
            assert_eq!(domain_target(), format!("gui/{uid}"));
            assert_eq!(
                service_target("com.addison.auto.tidy"),
                format!("gui/{uid}/com.addison.auto.tidy")
            );
            assert!(!domain_target().starts_with("system"));
        }
    }
}

// ===========================================================================
#[cfg(all(test, not(target_os = "macos")))]
mod other_platform_tests {
    use super::*;

    #[test]
    fn arming_is_refused_off_macos_in_plain_language() {
        // v1 arms launchd user agents and nothing else (plan §5.4). Elsewhere the
        // whole surface says so in one sentence rather than failing in a way that
        // reads like a broken machine — the same temperament as the seatbelt's
        // non-mac disclosure. There is no code path here that could write a file or
        // run a scheduler: the macOS module does not exist on this target.
        let params = json!({
            "label": "com.addison.auto.tidy", "command": "echo hi",
            "scheduleKind": "interval", "schedule": { "minutes": 30 }
        });
        for answer in [arm(&params).unwrap(), disarm(&params).unwrap()] {
            assert_eq!(answer["ok"].as_bool(), Some(false));
            assert_eq!(answer["error"].as_str(), Some(NOT_ON_THIS_COMPUTER));
        }
        let listed = list_armed(&json!({})).unwrap();
        assert_eq!(listed["supported"].as_bool(), Some(false));
        assert_eq!(listed["armed"].as_array().map(Vec::len), Some(0));
    }
}

