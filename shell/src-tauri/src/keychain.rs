// OS keychain access — engineering-spec §5, §8.3.
//
// API keys of any kind NEVER reach the frontend/webview and are never stored in
// SQLite. They live in the OS keychain, written by this module and read only at
// the moment of use. This module is the ONLY place a key value is handled in the
// shell, and it is strictly asymmetric:
//   - `store_provider_key` (webview -> shell): WRITE-only. The webview may save a
//     key the user typed; there is deliberately NO command that reads one back.
//   - `get_provider_key` (Agent-Core-internal): answers the core's per-call
//     `keychain.getProviderKey` over stdio. The value goes core-ward only.
// Key VALUES are never logged, never emitted, never returned to the webview.
//
// This module also owns the DEVICE IDENTITY keypair (§5, design-doc §7.5.1):
// an ed25519 keypair generated here on first launch. The PRIVATE half lives only
// in the OS keychain and never leaves this process except as an in-memory signing
// key; the core reaches it solely through two Core->Shell calls —
// `keychain.getDeviceKey` (public half + id) and `keychain.signRelayRequest`
// (bytes-to-sign in, signature out) — so the private key is never logged, never
// emitted, and never crosses an IPC boundary.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

use base64::Engine as _;
use ed25519_dalek::{Signer, SigningKey};
use keyring::Entry;
use rand_core::OsRng;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::ipc::{required_str, RpcError};

/// In-process, session-lifetime cache of provider keys (owner decision
/// 2026-07-19). Reading the OS keychain on every provider call made macOS
/// re-prompt for the login-keychain password — once per MESSAGE in the worst
/// case, and after every dev rebuild even with "Always Allow". The cache
/// collapses that to at most ONE OS read per provider per launch.
///
/// Invariant §8.3 is preserved: the cache lives only in this shell process's
/// memory (the same trust level that already handles every key read), is
/// updated on Replace, evicted on Remove, and vanishes when the app exits.
/// Keys still never touch SQLite, the webview, or long-lived core memory.
static KEY_CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();

fn key_cache() -> &'static Mutex<HashMap<String, String>> {
    KEY_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn cache_get(provider: &str) -> Option<String> {
    key_cache().lock().ok()?.get(provider).cloned()
}

fn cache_put(provider: &str, key: &str) {
    if let Ok(mut cache) = key_cache().lock() {
        cache.insert(provider.to_string(), key.to_string());
    }
}

fn cache_evict(provider: &str) {
    if let Ok(mut cache) = key_cache().lock() {
        cache.remove(provider);
    }
}

/// Provider ids whose last OS read FAILED for a reason other than "nothing saved" —
/// a denied or dismissed password dialog, or a keychain error. Those are answered
/// from here without touching the OS again.
///
/// **NO LONGER A PRESENCE CACHE** (secrets-and-keychain plan §4.1, built 2026-08-06).
/// It existed because the widget rail polled `stats.get` every 60 seconds and each
/// poll probed the keychain for a key, so a single denial became a fresh password
/// dialog every minute, forever. That poll is gone: "is a key saved?" is answered
/// core-side from `provider_config.secret_presence` and never reaches this file. What
/// is left is the DECLINE MEMORY (plan §5.5) for the callers that genuinely want a
/// value without a person behind them — the background catalog load and the saved-
/// provider reconnect — so a dismissed dialog is not re-raised by a launch task.
///
/// TRADE-OFF, stated plainly: a denial holds for the rest of the session. The retry
/// signal is the USER acting on the key — `store_provider_key` and
/// `delete_provider_key` both clear the provider's entry here, so re-saving or
/// removing a key touches the OS again immediately.
///
/// `NoEntry` is NEVER recorded here: "nothing saved" costs no dialog and is a normal
/// answer, so re-asking the OS for it every time is free.
static FAILED_READS: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();

fn failed_reads() -> &'static Mutex<HashSet<String>> {
    FAILED_READS.get_or_init(|| Mutex::new(HashSet::new()))
}

fn failure_remember(provider: &str) {
    if let Ok(mut failures) = failed_reads().lock() {
        failures.insert(provider.to_string());
    }
}

fn failure_recorded(provider: &str) -> bool {
    failed_reads().lock().map(|failures| failures.contains(provider)).unwrap_or(false)
}

fn failure_forget(provider: &str) {
    if let Ok(mut failures) = failed_reads().lock() {
        failures.remove(provider);
    }
}

/// Serializes every OS keychain access in this process. Keychain-bound requests run
/// on blocking tasks now (agent_process.rs) so a modal password dialog can never
/// stall the core's stdout pump — which means two of them can be in flight at once.
/// Without this lock concurrent requests could raise OVERLAPPING password dialogs,
/// or both miss the session cache and read the same item twice.
static OS_KEYCHAIN: Mutex<()> = Mutex::new(());

/// Take the OS-access lock. A poisoned lock is recovered rather than propagated: a
/// panic while holding it must not wedge every later keychain call.
fn os_guard() -> MutexGuard<'static, ()> {
    OS_KEYCHAIN.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

// ===========================================================================
// KEYCHAIN TRACE — a diagnostic, opt-in, and it must never print a secret
// ===========================================================================
// WHY IT EXISTS. Launching raises TWO password dialogs for the SAME item, and
// "Allow" answers only the first — because "Allow" is a single-access grant while
// "Always Allow" writes an ACL onto the item. Two dialogs therefore means two
// separate OS reads of one item, which `KEY_CACHE` and `OS_KEYCHAIN` were built to
// prevent. Static reading of this file cannot say which caller makes the second
// read: it depends on ordering and on which of the three outcomes the first read
// returns (`NothingSaved` is deliberately never cached). So: measure.
//
// OFF BY DEFAULT, both processes. `ADDISON_KEYCHAIN_TRACE=1` turns it on here and
// in the Agent Core (`shell_bridge.py`), which prints the CORE-side call site — so
// one launch shows both "who asked" and "what the OS was actually touched for",
// interleaved on the same stderr in real order.
//
// **NO KEY MATERIAL, EVER (G1).** A trace line carries the account name, the
// outcome VARIANT, and timing. Never a key, never a length, never a prefix — a
// length narrows a brute force and a prefix identifies the vendor. This is
// enforced by `a_trace_line_never_carries_key_material`, which is the test that
// matters in this block: a debug aid that leaks the thing it is debugging is worse
// than no debug aid.
static TRACE_START: OnceLock<Instant> = OnceLock::new();
static TRACE_SEQ: AtomicU32 = AtomicU32::new(0);
static TRACE_ON: OnceLock<bool> = OnceLock::new();

/// Whether the trace is on, decided from the environment ONCE per process.
///
/// The decision used to be `std::env::var(…)` on every call, which made the switch
/// process-global mutable state — and its test set and cleared the variable to
/// exercise it, racing every other test thread that reached a `trace()`. That is
/// exactly the flake shape `exec.rs` spends six lines condemning for
/// `ADDISON_DB_PATH`: it passes alone and fails in a full run, and it is a data
/// race besides (`set_var` is unsound while another thread reads the environment).
///
/// So the VALUE is read once here and the RULE lives in `trace_flag_from`, which is
/// pure and is what the test drives. Reading once is also the honest model of the
/// switch: it is a launch-time diagnostic, not something meant to change under a
/// running process.
fn trace_enabled() -> bool {
    *TRACE_ON
        .get_or_init(|| trace_flag_from(std::env::var("ADDISON_KEYCHAIN_TRACE").ok().as_deref()))
}

/// The rule itself: only an explicit `1`. Injected rather than read, so the test
/// asserts on values instead of on this process's environment.
fn trace_flag_from(value: Option<&str>) -> bool {
    value == Some("1")
}

/// Emit a trace line, formatting its arguments ONLY when the trace is on.
///
/// The guard belongs in the macro, not in the callee: the call sites build their
/// subject with `format!`, and an argument is evaluated before the call, so every
/// provider-key read allocated a `String` it then threw away — on the hot path, in
/// every build, with the trace off. Wrapping the whole call in the check is what
/// makes a disabled trace genuinely free.
macro_rules! trace {
    ($event:expr, $subject:expr, $outcome:expr $(,)?) => {
        if trace_enabled() {
            trace_line($event, $subject, $outcome);
        }
    };
}

/// One trace line. `subject` is an account name or `provider=…`; `outcome` is a
/// short variant word. Both are program-authored constants or account names — never
/// a value read out of the keychain.
///
/// The `trace!` macro is the only caller. The check is repeated here anyway so a
/// future direct call cannot quietly print in a build where the trace is off — the
/// cost is one atomic read of an already-initialised `OnceLock`.
fn trace_line(event: &str, subject: &str, outcome: &str) {
    if !trace_enabled() {
        return;
    }
    let started = TRACE_START.get_or_init(Instant::now);
    let seq = TRACE_SEQ.fetch_add(1, Ordering::Relaxed) + 1;
    // stderr, because the core's stdout is the JSON-RPC channel and the shell's
    // stderr is what `npm run tauri dev` shows (agent_process.rs inherits it, so
    // both processes' lines land in the one terminal, in order).
    eprintln!(
        "[keychain +{:>6}ms #{seq:<2}] shell  {event:<12} {subject:<28} -> {outcome}",
        started.elapsed().as_millis()
    );
}

/// Keychain service name — matches the app identifier (tauri.conf.json).
const SERVICE: &str = "app.addison.desktop";

/// Keychain account holding the device-identity blob (device id + private key).
/// A dedicated account, distinct from the `provider-key:*` accounts, so device
/// identity and BYOK keys never collide.
const DEVICE_ACCOUNT: &str = "device-identity";

/// Keychain account for a provider key, namespaced by PROVIDER id
/// (`anthropic` | `openai` | `google` | `custom`) — the multi-provider scheme
/// (owner decision 2026-07-18). One key per provider at a time, overwritten when
/// the user replaces it. The provider id is the only handle the core has when it
/// later asks for the key (`keychain.getProviderKey {provider}`).
fn account_for_provider(provider: &str) -> String {
    format!("provider-key:{provider}")
}

// ===========================================================================
// WRITES: delete-then-add, always — and the self-heal that rests on it
// ===========================================================================
// An item's ACL — the list of apps allowed to read it without asking — is minted
// AT CREATION, with the creating app on it. That is why creating never prompts,
// reading your own item never prompts, and reading someone else's always does. An
// item goes FOREIGN when a previous build made it, when a keychain is restored onto
// a new Mac, or when the signing identity rotates.
//
// **You cannot repair an ACL by writing to the item.** `security-framework`'s
// `set_password_internal` is:
//
//     let status = SecItemAdd(...);
//     if status == errSecDuplicateItem { SecItemUpdate(query, update) }
//
// — and `SecItemUpdate` preserves the item AND its old, foreign ACL. So the
// convenient write path can never heal anything: it rewrites the value under the
// stale access list, and the user's own instinct ("I'll just save it again")
// accomplishes precisely nothing. Every write here is therefore an explicit
// delete-then-add, which mints a fresh ACL naming this build.
//
// **THIS IS THE ONE OPERATION IN THE SUBSYSTEM THAT CAN LOSE DATA.** Between the
// delete and the add there is a window in which the user's key exists only in this
// process's memory. Hence, without exception: hold the value first, retry the add,
// VERIFY BY READING IT BACK before reporting success, and — if it still could not be
// restored — remember that so the person is told a plain sentence instead of being
// quietly handed "no key saved" (which would route their next message to the relay).
//
// SCOPE: provider keys only. The device-identity item is deliberately excluded —
// a provider key can be pasted again from the vendor's website, while the device
// identity's private half is recoverable by nobody. See docs/KNOWN-GAPS.md.

/// How long a successful OS read has to take before it is treated as a read of a
/// FOREIGN item — i.e. one that raised a password dialog somebody answered.
///
/// This is the detection mechanism, and it is exact rather than heuristic about the
/// thing that matters: a foreign item ALWAYS prompts (that is what foreign means),
/// and a prompt always waits on a human. Spike 1 measured an app-owned read at 29 ms;
/// the fastest conceivable human answer is two orders of magnitude above that. So the
/// gap this threshold sits in is enormous, and the failure mode is one-sided and
/// cheap: an unusually slow but app-owned read costs one unnecessary (and verified)
/// re-creation, while a foreign read cannot slip under it without a dialog nobody saw.
const FOREIGN_READ_THRESHOLD: Duration = Duration::from_millis(400);

/// The rule, pure and injected, so the test drives values rather than a real clock.
fn read_looked_foreign(elapsed: Duration) -> bool {
    elapsed >= FOREIGN_READ_THRESHOLD
}

/// Providers whose key was destroyed by a repair that could not put it back. There is
/// no honest way to hide this: the bytes are gone from the keychain, and the ONLY
/// thing that can fix it is the person pasting the key again.
///
/// It is consulted where it matters and nowhere else — when a later read finds NOTHING
/// for a provider whose repair failed, that is not "no key saved" (which is a normal
/// answer and the cue to onboard), it is a loss with a name. The session cache still
/// holds the value, so nothing breaks until the app is relaunched; this is what makes
/// the relaunch say why instead of silently offering to set the person up again.
static REPAIR_LOST: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();

fn repair_lost() -> &'static Mutex<HashSet<String>> {
    REPAIR_LOST.get_or_init(|| Mutex::new(HashSet::new()))
}

fn repair_lost_remember(provider: &str) {
    if let Ok(mut lost) = repair_lost().lock() {
        lost.insert(provider.to_string());
    }
}

fn repair_lost_recorded(provider: &str) -> bool {
    repair_lost().lock().map(|lost| lost.contains(provider)).unwrap_or(false)
}

fn repair_lost_forget(provider: &str) {
    if let Ok(mut lost) = repair_lost().lock() {
        lost.remove(provider);
    }
}

/// Why a credential write did not complete. The distinction is the whole point: one
/// of these leaves the user's key exactly where it was, and the other means it is gone.
#[derive(Debug, PartialEq, Eq)]
enum WriteFailure {
    /// The old item was never removed, so whatever was saved is still saved.
    Untouched,
    /// The delete landed and the value could not be put back, verified. The key is
    /// gone from the keychain and only the person can restore it.
    Lost,
}

/// How many times the whole delete-add-verify ladder is attempted before a write is
/// declared lost. Two: a first go, and one retry with the value still in hand.
const WRITE_ATTEMPTS: u32 = 2;

/// The write, as a sequence, over injected operations.
///
/// The closures exist for the reason the module comment gives for `migrate_legacy_key`:
/// keyring 3.6.3's mock store hands out a fresh empty credential per `Entry::new`, so a
/// test cannot pre-seed the entries the real code constructs, and a `cargo test` run
/// must never touch the user's own keychain. Injecting the three operations is what
/// makes the ORDER, the RETRY and the READ-BACK provable rather than asserted in prose.
///
/// The ladder, and why each rung is where it is:
///
///   1. `delete`. `NoEntry` is success — deleting an absent item is the normal case
///      for a first save. Any OTHER delete error on the FIRST attempt aborts with
///      `Untouched`: the old item may still be there under its foreign ACL, and
///      calling `add` then would fall into the `SecItemUpdate` trap this function
///      exists to avoid — writing the value under the stale access list while
///      reporting success.
///   2. `add`. Now a pure `SecItemAdd`, so the ACL is minted for this build.
///   3. `read_back`, compared byte-for-byte. **Success is never reported on the
///      strength of the add's return code alone.** This is the one operation that can
///      lose the user's key, and the only evidence worth having that it did not is
///      the value being readable again.
///
/// A failure at 2 or 3 retries the whole ladder — the value is still in memory, and a
/// transient keychain error is exactly the case a retry is for. Only after
/// `WRITE_ATTEMPTS` does it give up, and it says `Lost` when it does, because by then
/// the delete has landed.
///
/// G1: `value` is compared and passed on. It is never traced, never logged, never
/// returned. The trace lines below carry variant words only.
fn write_credential_with(
    value: &str,
    delete: &dyn Fn() -> Result<(), keyring::Error>,
    add: &dyn Fn(&str) -> Result<(), keyring::Error>,
    read_back: &dyn Fn() -> Result<String, keyring::Error>,
) -> Result<(), WriteFailure> {
    for attempt in 0..WRITE_ATTEMPTS {
        match delete() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(_) if attempt == 0 => return Err(WriteFailure::Untouched),
            Err(_) => continue,
        }
        if add(value).is_err() {
            continue;
        }
        // The read-back. A mismatch is as bad as an error: it means something else
        // holds that account name, and serving it later would hand the core somebody
        // else's bytes.
        if read_back().is_ok_and(|stored| stored == value) {
            return Ok(());
        }
    }
    Err(WriteFailure::Lost)
}

/// `write_credential_with` bound to a real keyring entry.
///
/// Called only with `os_guard()` already held — every caller is inside the OS lock,
/// and the lock is not reentrant.
fn write_credential(entry: &Entry, value: &str) -> Result<(), WriteFailure> {
    write_credential_with(
        value,
        &|| entry.delete_credential(),
        &|v| entry.set_password(v),
        &|| entry.get_password(),
    )
}

/// The legacy role-based Anthropic account, from before the per-provider scheme.
/// Read once and migrated to `provider-key:anthropic` so an already-saved key keeps
/// working across the upgrade (see `get_provider_key`).
const LEGACY_ANTHROPIC_ACCOUNT: &str = "provider-key:primary";

/// The only provider the legacy account can hold a key for. Shared by the migration
/// read and the removal path, so the two can never disagree about which provider has
/// a second durable copy of its key.
const LEGACY_PROVIDER: &str = "anthropic";

// --- §5.3: the shape a key must have before it is stored --------------------
//
// `SettingsPage.tsx` trims the box's contents, but that is the frontend's courtesy
// and not a contract: any other route into `store_provider_key` — or one regression
// in that component — persists whatever it was handed. A key with a trailing newline
// is the classic paste bug, and what it produces is an auth failure indistinguishable
// from a wrong key, days later, with nothing on screen pointing at the cause.
//
// Three refusals, each with its own sentence, because "that key looks wrong" tells
// somebody nothing they can act on. G1: every one of them is a CONSTANT. No branch
// here logs, traces or embeds the value, its length or its prefix — they name the
// SHAPE of the problem and nothing else.

/// Nothing left after the strip. Refused rather than stored, because an empty item
/// reads back as the empty string — which is how this whole subsystem says "nothing
/// saved", and "nothing saved" is the ONE answer that may reach the external Setup
/// Assistant relay. Storing whitespace would put a person's turn on that road.
const KEY_IS_BLANK: &str = "That key is blank — paste the key and try again.";

/// The paste bug §5.3 is named after, in its unfixable-by-trimming form: a break in
/// the MIDDLE. Refused, never repaired — a key Addison quietly altered is a key that
/// fails mysteriously later, which is the outcome this exists to prevent.
const KEY_HAS_A_LINE_BREAK: &str =
    "That key has a line break in it — paste it again as one line.";

/// Tabs, NULs, and the zero-width marks a copy out of a styled web page carries
/// along. Invisible by definition, so the sentence has to name the fix rather than
/// ask the person to look for something they cannot see.
const KEY_HAS_HIDDEN_CHARACTERS: &str =
    "That key has hidden characters in it — copy it straight from the provider's \
     page and paste it again.";

/// Characters that cannot legitimately appear inside an API key and are invisible
/// when they do. Line breaks are matched separately (they get their own sentence),
/// so this is the rest: `Cc` controls, the zero-width / directional marks in
/// U+200B–U+200F, the word joiner, the BOM, and the soft hyphen.
fn is_invisible(c: char) -> bool {
    c.is_control()
        || matches!(c, '\u{200B}'..='\u{200F}' | '\u{2060}' | '\u{FEFF}' | '\u{00AD}')
}

/// The store boundary's own normalisation (§5.3). Returns the value that will be
/// written — a borrowed slice of the caller's string — or the plain sentence to
/// refuse with.
///
/// Surrounding whitespace is STRIPPED, not refused: a trailing newline off a web
/// page is not a different key, it is the same key with a paste artefact on the end,
/// and refusing it would tell somebody to fix something they cannot see. Everything
/// else that cannot be repaired without guessing is refused.
fn normalised_key(raw: &str) -> Result<&str, &'static str> {
    let key = raw.trim();
    if key.is_empty() {
        return Err(KEY_IS_BLANK);
    }
    // U+2028/U+2029 are line breaks too; `trim` takes them off the ends (both are
    // White_Space) but neither is a `Cc` control, so an embedded one would otherwise
    // fall through both checks.
    if key.contains(['\n', '\r', '\u{2028}', '\u{2029}']) {
        return Err(KEY_HAS_A_LINE_BREAK);
    }
    if key.chars().any(is_invisible) {
        return Err(KEY_HAS_HIDDEN_CHARACTERS);
    }
    Ok(key)
}

/// Webview -> Shell. Write-only path for a BYOK key the user typed. The key goes
/// straight into the OS keychain, keyed by provider id, and is never echoed back
/// anywhere (§8.3).
///
/// `async` + `spawn_blocking` because the body waits on `OS_KEYCHAIN`: a
/// core-initiated read can hold that lock for as long as a password dialog sits
/// unanswered, and a sync command would park the main thread — the whole window —
/// behind it.
#[tauri::command]
pub async fn store_provider_key(provider: String, key: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || store_provider_key_blocking(&provider, &key))
        .await
        .unwrap_or_else(|_| Err("The keychain didn't answer just now.".to_string()))
}

fn store_provider_key_blocking(provider: &str, key: &str) -> Result<(), String> {
    // §5.3, and it happens FIRST, for two separate reasons. A key refused for its
    // shape must not queue behind a password dialog somebody else's read is parked
    // on — the refusal needs nothing from the OS. And the compare inside
    // `save_would_change_nothing` has to see the same bytes the write would store:
    // comparing the raw paste against a stored, already-normalised key reads a
    // trailing newline as a change and re-mints the item for nothing, which is the
    // one operation in this module that can lose a key (§5.4, §4.2).
    let key = match normalised_key(key) {
        Ok(normalised) => normalised,
        Err(problem) => {
            trace!("save", &format!("provider={provider}"), "refused-shape");
            return Err(problem.to_string());
        }
    };
    let _os = os_guard();
    let entry = Entry::new(SERVICE, &account_for_provider(provider))
        .map_err(|_| "Couldn't reach the system keychain to save your key.".to_string())?;
    if save_would_change_nothing(provider, &entry, key) {
        trace!("save", &format!("provider={provider}"), "unchanged (no write)");
        failure_forget(provider);
        return Ok(());
    }
    // NOT `set_password`. See the WRITES block above: `set_password` falls back to
    // `SecItemUpdate` on a duplicate, which preserves the old foreign ACL — so the
    // convenient call is exactly the one that makes "save it again" useless.
    match write_credential(&entry, key) {
        Ok(()) => {}
        Err(WriteFailure::Untouched) => {
            return Err("Couldn't save your key to the system keychain.".to_string())
        }
        Err(WriteFailure::Lost) => {
            // The old item is gone and the new one would not stick. Say so plainly and
            // remember it, rather than letting a later read report "nothing saved".
            repair_lost_remember(provider);
            trace!("save", &format!("provider={provider}"), "lost-in-write");
            return Err(KEY_LOST_MESSAGE.to_string());
        }
    }
    repair_lost_forget(provider);
    // Keep the session cache coherent so a Replace takes effect immediately
    // without another OS keychain round-trip (and prompt).
    cache_put(provider, key);
    // Saving a key is the user's retry signal for a read that failed earlier this
    // session — drop the negative entry so reads reach the OS again.
    failure_forget(provider);
    Ok(())
}

/// §5.4 — read-compare-then-write. Is this Save a no-op?
///
/// Every write is now a destroy-and-re-mint, so a naive Save on an unchanged value
/// would run the one data-losing operation in the subsystem for no reason at all. The
/// comparison costs nothing on the healthy path: the value is normally already in the
/// session cache, and a cached value means the item was read (and, if it needed it,
/// healed) earlier this session.
///
/// THREE conditions, all required, and the last two are what keep §5.4 from cancelling
/// §4.2:
///
///   * the stored value is byte-identical — otherwise there is a real change to write;
///   * the item is not FOREIGN. A person re-saving the same key onto an item this build
///     cannot read without a dialog is the exact case self-heal exists for, and
///     short-circuiting it would leave them pressing Save forever with nothing
///     happening — the original reported symptom;
///   * the provider is not on the repair-lost list, where the keychain holds nothing at
///     all and the cached value is the only copy left.
///
/// Anything unreadable answers `false`: when Addison cannot see what is stored, the
/// safe move is to write, which also repairs. A dialog dismissed here therefore still
/// lets the Save go through.
fn save_would_change_nothing(provider: &str, entry: &Entry, key: &str) -> bool {
    if repair_lost_recorded(provider) {
        return false;
    }
    if let Some(cached) = cache_get(provider) {
        return cached == key;
    }
    let started = Instant::now();
    let stored = entry.get_password();
    if read_looked_foreign(started.elapsed()) {
        return false;
    }
    stored.is_ok_and(|stored| stored == key)
}

/// Webview -> Shell. Delete a provider's stored key (the "Remove" action). A
/// missing entry is treated as success — removing an absent key is idempotent.
///
/// `async` + `spawn_blocking` for the same reason as `store_provider_key`.
#[tauri::command]
pub async fn delete_provider_key(provider: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || delete_provider_key_blocking(&provider))
        .await
        .unwrap_or_else(|_| Err("The keychain didn't answer just now.".to_string()))
}

fn delete_provider_key_blocking(provider: &str) -> Result<(), String> {
    // Evict BEFORE the OS delete: even if the OS call fails, a removed key must
    // never keep being served from memory. Removing a key is also the user's retry
    // signal for a read that failed earlier this session.
    cache_evict(provider);
    failure_forget(provider);
    // A deliberate removal settles any earlier repair loss: from here on "nothing
    // saved" is the truth, not a wound, and the person must be able to onboard again.
    repair_lost_forget(provider);

    let _os = os_guard();
    // Evict AGAIN under the lock. A read parked at a password dialog holds the lock
    // with nothing cached yet; if the user answers that dialog after clicking Remove,
    // the read's `cache_put` (and a dismissal's `failure_remember`) land AFTER the
    // eviction above — and a removed key must never keep being served from memory.
    cache_evict(provider);
    failure_forget(provider);
    repair_lost_forget(provider);
    let entry = Entry::new(SERVICE, &account_for_provider(provider))
        .map_err(|_| "Couldn't reach the system keychain to remove your key.".to_string())?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(_) => return Err("Couldn't remove your key from the system keychain.".to_string()),
    }
    // The legacy account is a SECOND durable copy of the same key: left behind, it
    // resurrects the removed key on the next read through the migration fallback in
    // `get_provider_key`. Removal has to clear both. A missing legacy entry is the
    // normal case (nothing to migrate) and counts as success; a real failure is
    // reported, because the key genuinely is still on the machine — and a retry is
    // idempotent, the per-provider entry is already gone.
    if provider == LEGACY_PROVIDER {
        let legacy = Entry::new(SERVICE, LEGACY_ANTHROPIC_ACCOUNT)
            .map_err(|_| "Couldn't remove your key from the system keychain.".to_string())?;
        match legacy.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(_) => return Err("Couldn't remove your key from the system keychain.".to_string()),
        }
    }
    Ok(())
}

/// The outcome of a provider-key read, mapped 1:1 onto the wire protocol the core is
/// written against (`provider_key_response`). The cases are deliberately distinct:
/// collapsing "nothing saved" with any of the others is what let a DENIED read look
/// like "no key saved", so the core quietly rerouted the turn to the external Setup
/// Assistant relay while the user's key sat in the keychain the whole time. Exactly
/// ONE of them means "you may onboard this person", and it is `NothingSaved`.
enum KeyRead {
    /// A key is saved. The value goes core-ward and nowhere else.
    Found(String),
    /// Nothing is saved for this provider — a normal answer, not an error.
    NothingSaved,
    /// The read itself failed (denied dialog, keychain error). A key MAY exist, so
    /// this must never be reported as "no key saved".
    Unreadable,
    /// The item is genuinely gone because a repair could not put it back (§4.2). Also
    /// never "no key saved" — it is a loss with a cause, and the only fix is the person
    /// pasting the key again, so it carries a sentence that says exactly that.
    LostInRepair,
}

impl KeyRead {
    /// The variant, as one word, for a trace line. **Never the key** — `Found`
    /// deliberately renders as a bare word and not as any function of the value:
    /// not the key, not its length, not a prefix. A length narrows a brute force
    /// and a prefix names the vendor, and neither is worth a debug line.
    fn trace_word(&self) -> &'static str {
        match self {
            KeyRead::Found(_) => "found",
            KeyRead::NothingSaved => "nothing-saved",
            KeyRead::Unreadable => "unreadable",
            KeyRead::LostInRepair => "lost-in-repair",
        }
    }
}

/// Said when a repair destroyed the item and could not restore it. Plain, and it names
/// the one action that fixes it — never a stack trace, never an error code, and never
/// silence. "your computer's keychain" is the noun used everywhere else the person
/// meets this subsystem.
const KEY_LOST_MESSAGE: &str =
    "Addison couldn't save your key back to your computer's keychain, so it isn't \
     stored any more. Add it again in Settings.";

/// Agent-Core-internal read. Never exposed as a Tauri command, so the webview has no
/// route to it.
///
/// Two memories sit in front of the OS, and both exist to hold the OS password
/// dialog to at most one per launch: the session KEY_CACHE for a key already read,
/// and FAILED_READS for a read that already failed.
///
/// `fresh` skips the FAILED_READS short-circuit (never the key cache): a per-turn
/// probe is the person acting, and a person who dismissed a dialog by mistake gets
/// asked again on their next message instead of being told to re-save the key. The
/// automatic pollers (stats, provider.list) stay non-fresh, so a denial still costs
/// at most one dialog per user action, never one per minute.
fn get_provider_key(provider: &str, fresh: bool) -> KeyRead {
    trace!("ask", &format!("provider={provider} fresh={fresh}"), "…");
    if let Some(answer) = cached_answer(provider, fresh) {
        trace!("answered", &format!("provider={provider}"), &format!("{} (no OS touch)", answer.trace_word()));
        return answer;
    }
    let _os = os_guard();
    // Re-check under the OS lock: a caller that queued behind another request for the
    // same provider must read ITS result rather than raise a second dialog for an
    // item that has just been fetched.
    if let Some(answer) = cached_answer(provider, fresh) {
        trace!("answered", &format!("provider={provider}"), &format!("{} (no OS touch, post-lock)", answer.trace_word()));
        return answer;
    }

    // TIMED, because the elapsed time is the foreign-item signal (see
    // FOREIGN_READ_THRESHOLD): a read that waited on a password dialog is by
    // definition a read of an item this build is not on the access list for.
    let started = Instant::now();
    let read = read_provider_key_from_os(provider);
    let elapsed = started.elapsed();

    let freshly_written = read.freshly_written;
    let mut outcome = read.outcome;
    trace!("cached-as", &format!("provider={provider}"), outcome.trace_word());
    match &outcome {
        KeyRead::Found(key) => {
            // CACHE BEFORE HEALING, deliberately. The heal is the one operation that
            // can destroy the item, and the session must survive it even in the worst
            // case: with the value already cached, a failed repair costs the person a
            // sentence at the next launch, never this message.
            cache_put(provider, key);
            if should_self_heal(freshly_written, elapsed) {
                self_heal(provider, key);
            }
        }
        // Remembered so a later background caller answers from memory instead of
        // raising another dialog. `NothingSaved` is deliberately never remembered.
        KeyRead::Unreadable => failure_remember(provider),
        // "Nothing saved" is a normal answer — UNLESS a repair in this session is why
        // there is nothing there. Then it is a loss, and saying "no key saved" would
        // hand the person an onboarding flow instead of the one sentence that fixes it.
        KeyRead::NothingSaved if repair_lost_recorded(provider) => {
            outcome = KeyRead::LostInRepair;
        }
        KeyRead::NothingSaved | KeyRead::LostInRepair => {}
    }
    outcome
}

/// Does this successful read call for a repair? Pure, so the decision is provable
/// without an OS keychain — and because the two conditions pull in opposite
/// directions and would otherwise sit inline in a match arm nobody can reach.
///
///   * the read WAITED, so a dialog was answered, so the item is foreign;
///   * and the item was not created during this very read. The legacy migration
///     mints a fresh item under this build's identity but spends its time on the
///     LEGACY item's dialog — so without this the elapsed signal would fire and
///     Addison would destroy-and-re-mint an item it made seconds ago, running the
///     one data-losing operation for nothing at all.
fn should_self_heal(freshly_written: bool, elapsed: Duration) -> bool {
    !freshly_written && read_looked_foreign(elapsed)
}

/// §4.2 — repair a foreign item by re-creating it, with the bytes we just read.
///
/// "One dialog every session, forever" becomes "one dialog, once": the delete and the
/// add both belong to this build, so the item that comes back names this app on its
/// access list and every later read is silent.
///
/// Called with `os_guard()` held (from `get_provider_key`, which owns it) — the lock is
/// not reentrant, so this must never take it.
///
/// PROVIDER KEYS ONLY. `ensure_device_keypair` deliberately does not call this: a
/// provider key can be pasted again from the vendor's site, and the device identity's
/// private half cannot be recovered by anyone. See docs/KNOWN-GAPS.md.
fn self_heal(provider: &str, key: &str) {
    trace!("heal", &format!("provider={provider}"), "foreign item -> re-creating");
    let Ok(entry) = Entry::new(SERVICE, &account_for_provider(provider)) else {
        return;   // no handle, nothing deleted — the item is exactly as it was
    };
    match write_credential(&entry, key) {
        Ok(()) => trace!("heal", &format!("provider={provider}"), "re-created"),
        // Nothing was removed; the person keeps the item they had, foreign ACL and all,
        // and the next session tries again.
        Err(WriteFailure::Untouched) => {
            trace!("heal", &format!("provider={provider}"), "declined (item untouched)")
        }
        // The bad one. The item is gone; remember it so the next read that finds
        // nothing says why instead of offering to set the person up from scratch.
        Err(WriteFailure::Lost) => {
            repair_lost_remember(provider);
            trace!("heal", &format!("provider={provider}"), "lost-in-repair");
        }
    }
}

/// The answer that can be given without touching the OS at all: a key already read
/// this session, or — unless the caller asked for a `fresh` attempt — a read that
/// already failed this session.
fn cached_answer(provider: &str, fresh: bool) -> Option<KeyRead> {
    if let Some(key) = cache_get(provider) {
        return Some(KeyRead::Found(key));
    }
    (!fresh && failure_recorded(provider)).then_some(KeyRead::Unreadable)
}

/// The OS-touching half of the read. Only ever called with `os_guard()` held and
/// after both memories have missed; the caller owns updating them.
///
/// Backward compat: on the first read for `anthropic` with no per-provider entry,
/// fall back to the legacy role-based account (`provider-key:primary`) and migrate
/// it into `provider-key:anthropic` so an existing key survives the upgrade to the
/// per-provider scheme without the user re-pasting it.
fn read_provider_key_from_os(provider: &str) -> OsRead {
    let account = account_for_provider(provider);
    // THE LINE THAT COSTS A PASSWORD DIALOG. Every one of these is one prompt on a
    // build whose ACL has been invalidated; counting them is the whole diagnostic.
    trace!("OS-TOUCH", &account, "reading…");
    let Ok(entry) = Entry::new(SERVICE, &account) else {
        trace!("OS-TOUCH", &account, "unreadable (no entry handle)");
        return OsRead::plain(KeyRead::Unreadable);
    };
    match entry.get_password() {
        Ok(key) => OsRead::plain(KeyRead::Found(key)),
        Err(keyring::Error::NoEntry) if provider == LEGACY_PROVIDER => legacy_anthropic_key(&entry),
        Err(keyring::Error::NoEntry) => OsRead::plain(KeyRead::NothingSaved),
        Err(_) => OsRead::plain(KeyRead::Unreadable),
    }
}

/// One OS read, plus the one thing the caller cannot infer from its outcome.
///
/// `freshly_written` says the item the key came back from was CREATED by this build
/// during the read — today only the legacy migration does that. It matters because
/// the caller's foreign-item signal is elapsed time, and the migration path spends
/// its time on the LEGACY item's dialog: without this flag, a migration would be
/// followed by a self-heal of an item that was minted seconds ago under this build's
/// own identity, which is the data-losing operation run for nothing.
struct OsRead {
    outcome: KeyRead,
    freshly_written: bool,
}

impl OsRead {
    fn plain(outcome: KeyRead) -> Self {
        Self { outcome, freshly_written: false }
    }
}

/// Read the legacy Anthropic key and migrate it into `destination`. Returns the key
/// either way — a migration that couldn't complete must not cost the user their key
/// for this launch.
fn legacy_anthropic_key(destination: &Entry) -> OsRead {
    // A SECOND OS touch inside one logical read — and therefore a second dialog.
    // Only reached when `provider-key:anthropic` reported NoEntry, but that is
    // exactly the case worth seeing in a trace: `NothingSaved` is never cached, so
    // if this path is live it runs again on the very next caller.
    trace!("OS-TOUCH", LEGACY_ANTHROPIC_ACCOUNT, "reading (legacy migration)…");
    let Ok(legacy) = Entry::new(SERVICE, LEGACY_ANTHROPIC_ACCOUNT) else {
        return OsRead::plain(KeyRead::Unreadable);
    };
    match legacy.get_password() {
        Ok(key) => {
            trace!("OS-TOUCH", LEGACY_ANTHROPIC_ACCOUNT, "found -> migrating + WRITING + deleting");
            // The copy goes through the delete-then-add path like every other write.
            // The destination reported NoEntry a moment ago, so the delete is a no-op
            // and this is a plain add — but routing it here is what keeps "every write
            // is delete-then-add" a property of the module rather than of three call
            // sites that each remembered.
            let copied = write_credential(destination, &key).is_ok();
            migrate_legacy_key(|| copied, || legacy.delete_credential());
            OsRead { outcome: KeyRead::Found(key), freshly_written: copied }
        }
        // Nothing to migrate: there is simply no Anthropic key saved.
        Err(keyring::Error::NoEntry) => OsRead::plain(KeyRead::NothingSaved),
        Err(_) => OsRead::plain(KeyRead::Unreadable),
    }
}

/// Best-effort migration, copy THEN delete. The ordering is load-bearing: until the
/// copy lands, the legacy entry is the only durable copy of the key, so the delete
/// is attempted only when `copy` reports success. When the copy fails the legacy
/// entry stays exactly where it is and the migration is retried on the next read —
/// which is only true in every ordering because of this guard.
///
/// Written over closures so the ordering is testable without an OS keychain. `copy`
/// reports whether the destination write landed AND VERIFIED (`write_credential`
/// reads it back), so the legacy entry is now only removed against proof rather than
/// against a return code.
fn migrate_legacy_key(
    copy: impl FnOnce() -> bool,
    delete_legacy: impl FnOnce() -> Result<(), keyring::Error>,
) {
    if copy() {
        let _ = delete_legacy();
    }
}

/// The device identity: a stable public `device_id` plus the ed25519 signing key
/// whose PRIVATE half lives only in the OS keychain. Built exclusively by
/// `ensure_device_keypair` (load-or-generate). Deliberately does NOT derive
/// `Debug`, so the private key can never be accidentally formatted into a log line —
/// that absence is load-bearing and must survive any future derive list. `Clone` is
/// what lets the session cache below hand out the loaded identity.
#[derive(Clone)]
struct DeviceIdentity {
    device_id: String,
    signing_key: SigningKey,
}

impl DeviceIdentity {
    /// First-launch generation: a fresh ed25519 keypair (seeded from the OS CSPRNG)
    /// and a v4 uuid as the public device id.
    fn generate() -> Self {
        let signing_key = SigningKey::generate(&mut OsRng);
        Self { device_id: Uuid::new_v4().to_string(), signing_key }
    }

    /// Base64 of the 32-byte PUBLIC key — the only half of the keypair that is ever
    /// allowed to leave this module.
    fn public_key_b64(&self) -> String {
        base64::engine::general_purpose::STANDARD.encode(self.signing_key.verifying_key().to_bytes())
    }

    /// Sign the canonical JSON bytes of `payload` and return base64 of the 64-byte
    /// ed25519 signature. ed25519 is deterministic (RFC 8032): the same payload
    /// under the same key always yields the same signature.
    fn sign_payload(&self, payload: &Value) -> Result<String, RpcError> {
        let bytes = canonical_json_bytes(payload)?;
        let signature = self.signing_key.sign(&bytes);
        Ok(base64::engine::general_purpose::STANDARD.encode(signature.to_bytes()))
    }

    /// Serialize for keychain storage: a JSON blob carrying the device id and the
    /// base64 private-key seed. Handed ONLY to the OS keychain, never anywhere else.
    fn to_stored(&self) -> String {
        json!({
            "deviceId": self.device_id,
            "privateKey": base64::engine::general_purpose::STANDARD.encode(self.signing_key.to_bytes()),
        })
        .to_string()
    }

    /// Inverse of `to_stored`. Errors (rather than regenerating) on a missing or
    /// corrupt blob, so a load never silently rotates the device's identity.
    fn from_stored(blob: &str) -> Result<Self, RpcError> {
        let value: Value = serde_json::from_str(blob)
            .map_err(|_| RpcError::app("Your device identity couldn't be read."))?;
        let device_id = value
            .get("deviceId")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::app("Your device identity is incomplete."))?
            .to_string();
        let priv_b64 = value
            .get("privateKey")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::app("Your device identity is incomplete."))?;
        let seed: [u8; 32] = base64::engine::general_purpose::STANDARD
            .decode(priv_b64)
            .ok()
            .and_then(|bytes| bytes.try_into().ok())
            .ok_or_else(|| RpcError::app("Your device identity couldn't be read."))?;
        Ok(Self { device_id, signing_key: SigningKey::from_bytes(&seed) })
    }
}

/// Canonical bytes signed for a relay request: `serde_json`'s compact encoding of
/// the exact value received. CONTRACT: the Python relay client must pass the same
/// object it transmits in the request body (and the relay must re-serialize it the
/// same way), so the server-side signature check reconstructs identical bytes.
fn canonical_json_bytes(payload: &Value) -> Result<Vec<u8>, RpcError> {
    serde_json::to_vec(payload)
        .map_err(|_| RpcError::app("That request couldn't be prepared for signing."))
}

/// Session-lifetime cache of the device identity — the same owner decision as
/// KEY_CACHE (2026-07-19), for the same reason: at most ONE OS read, and so at most
/// one OS password prompt, per launch. Without it `ensure_device_keypair` re-read the
/// keychain on every call, and a single relay message calls it TWICE
/// (`keychain.getDeviceKey`, then `keychain.signRelayRequest`) — two password dialogs
/// back to back for one message.
///
/// G1 is intact. The private key already transits this process's memory on every sign
/// call, so holding it for the session widens nothing: it stays in shell process
/// memory, never reaches SQLite or the webview, and vanishes when the app exits.
static DEVICE_CACHE: OnceLock<Mutex<Option<DeviceIdentity>>> = OnceLock::new();

fn device_cache() -> &'static Mutex<Option<DeviceIdentity>> {
    DEVICE_CACHE.get_or_init(|| Mutex::new(None))
}

/// Read from an identity cache slot. Takes the slot rather than reaching for the
/// global one so a test can exercise the semantics against its own slot — the global
/// is a single shared cell, and parallel tests writing to it would collide.
fn identity_cache_load(slot: &Mutex<Option<DeviceIdentity>>) -> Option<DeviceIdentity> {
    slot.lock().ok()?.as_ref().cloned()
}

fn identity_cache_store(slot: &Mutex<Option<DeviceIdentity>>, identity: &DeviceIdentity) {
    if let Ok(mut cache) = slot.lock() {
        *cache = Some(identity.clone());
    }
}

/// Load the device identity, generating and persisting it on first use. Idempotent:
/// once stored, every later call LOADS the same keypair and never regenerates —
/// regenerating would rotate the device's identity out from under the relay. The
/// private key is only ever materialized here as an in-memory `SigningKey`; it is
/// never returned, logged, or emitted.
fn ensure_device_keypair() -> Result<DeviceIdentity, RpcError> {
    trace!("ask", DEVICE_ACCOUNT, "…");
    if let Some(identity) = identity_cache_load(device_cache()) {
        trace!("answered", DEVICE_ACCOUNT, "cached (no OS touch)");
        return Ok(identity);
    }
    let _os = os_guard();
    // Re-check under the OS lock, for the same reason as the provider read: the two
    // calls one relay message makes must not both raise a dialog for the same item.
    if let Some(identity) = identity_cache_load(device_cache()) {
        trace!("answered", DEVICE_ACCOUNT, "cached (no OS touch, post-lock)");
        return Ok(identity);
    }

    trace!("OS-TOUCH", DEVICE_ACCOUNT, "reading…");
    let entry = Entry::new(SERVICE, DEVICE_ACCOUNT).map_err(|_| {
        RpcError::app("Couldn't reach the system keychain for your device identity.")
    })?;
    let identity = match entry.get_password() {
        // A corrupt blob errors out rather than being cached — only a usable identity
        // is ever remembered.
        Ok(blob) => DeviceIdentity::from_stored(&blob)?,
        Err(keyring::Error::NoEntry) => {
            trace!("OS-TOUCH", DEVICE_ACCOUNT, "no entry -> generating + WRITING");
            let identity = DeviceIdentity::generate();
            entry
                .set_password(&identity.to_stored())
                .map_err(|_| RpcError::app("Couldn't save your device identity to the keychain."))?;
            identity
        }
        Err(_) => {
            return Err(RpcError::app("Couldn't read your device identity from the keychain."))
        }
    };
    identity_cache_store(device_cache(), &identity);
    Ok(identity)
}

/// Build the `keychain.getProviderKey` response. Split out of `handle()` (the
/// app_build.rs call-the-real-builder pattern) so a test can pin the exact wire seam
/// the core is written against without touching the OS keychain.
///
/// THE SEAM, both halves of which must agree: a key is `{"key": "<value>"}`; nothing
/// saved is `{"key": ""}` — a normal RESULT; a failed read is an app error. Core-side
/// (agent_core/main.py) the empty string means "nothing saved" and the error means
/// "unreadable — a key may exist".
fn provider_key_response(outcome: KeyRead) -> Result<Value, RpcError> {
    match outcome {
        KeyRead::Found(key) => Ok(json!({ "key": key })),
        // Not an error: the core reads the empty string as "no key yet" and shows its
        // own "here's how to add one" message. Sending an error here would be
        // indistinguishable from a failed read.
        KeyRead::NothingSaved => Ok(json!({ "key": "" })),
        // Clean, value-free error — and deliberately NOT the empty string above. A
        // key may well be saved; treating this as "no key" is what silently rerouted
        // a turn to the external relay after the user dismissed a password dialog.
        KeyRead::Unreadable => {
            Err(RpcError::app("Couldn't read your saved key from the keychain."))
        }
        // Also an error, for the same reason and one more: the key is genuinely gone,
        // and the empty string would send the person into onboarding as though they
        // had never saved one. The sentence names the only thing that fixes it.
        KeyRead::LostInRepair => Err(RpcError::app(KEY_LOST_MESSAGE)),
    }
}

/// Build the `keychain.getDeviceKey` result — the PUBLIC device id and public key,
/// nothing else. Split out of `handle()` (the app_build.rs call-the-real-builder
/// pattern) so a test can assert over the EXACT value the core receives: a test that
/// rebuilt this `json!` by hand would stay green while the real response grew a field
/// that leaked the private seed.
fn device_key_response(identity: &DeviceIdentity) -> Value {
    json!({
        "deviceId": identity.device_id,
        "publicKey": identity.public_key_b64(),
    })
}

/// Build the `keychain.signRelayRequest` result from an already-computed signature.
/// Split out for the same reason as `device_key_response`: the shape the core sees is
/// pinned over the real builder, so adding key material here turns the leak test red.
fn sign_relay_response(identity: &DeviceIdentity, signature: &str) -> Value {
    json!({
        "signature": signature,
        "deviceId": identity.device_id,
    })
}

/// Handle a `keychain.*` request the core sent over stdout. Returns the JSON-RPC
/// `result` value on success, or an `RpcError` the core relays as plain language.
/// The returned key value is written straight back to the core's stdin by the
/// caller (agent_process.rs) — it never passes through the webview.
pub fn handle(method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        // {provider, fresh?} -> {key}. Served from the session cache after the first
        // OS read (owner decision 2026-07-19 — see KEY_CACHE); never persisted.
        // "Nothing saved" and "couldn't read" are separate answers — see
        // `provider_key_response` for the seam the core is written against. `fresh`
        // (default false) retries past a remembered failure — sent by the per-turn
        // probe only, so a person's own message can re-raise the dialog they
        // dismissed, while the automatic pollers stay quiet.
        "keychain.getProviderKey" => {
            let provider = required_str(params, "provider", "A provider is required.")?;
            let fresh = params.get("fresh").and_then(Value::as_bool).unwrap_or(false);
            provider_key_response(get_provider_key(provider, fresh))
        }
        // {} -> {deviceId, publicKey}. Generates the keypair on first use, loads it
        // thereafter (§5). Returns the PUBLIC half only — the private key never
        // leaves the keychain.
        "keychain.getDeviceKey" => {
            let identity = ensure_device_keypair()?;
            Ok(device_key_response(&identity))
        }
        // {payload} -> {signature, deviceId}. Signs the canonical JSON of `payload`
        // with the device private key (which stays in the keychain) and hands back
        // only the base64 signature + the public device id.
        "keychain.signRelayRequest" => {
            let payload = params
                .get("payload")
                .ok_or_else(|| RpcError::invalid_params("A payload to sign is required."))?;
            let identity = ensure_device_keypair()?;
            let signature = identity.sign_payload(payload)?;
            Ok(sign_relay_response(&identity, &signature))
        }
        other => Err(RpcError::method_not_found(other)),
    }
}

// Tests never touch the real OS keychain — a `cargo test` run must not raise a
// password dialog, and must not read or write anything the user owns. Everything
// here exercises a pure helper or a response builder.
//
// One consequence, deliberate: the migration ORDERING is tested through
// `migrate_legacy_key`'s closures rather than through real entries. keyring 3.6.3's
// mock store can't stand in for the OS here — its builder hands out a fresh,
// empty credential per `Entry::new` (`CredentialPersistence::EntryOnly`), so a test
// cannot pre-seed the entries `read_provider_key_from_os` constructs internally, and
// `set_default_credential_builder` is process-global, which would race across
// parallel test threads. The closure seam is what keeps the ordering provable.
#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};
    use std::cell::{Cell, RefCell};

    #[test]
    fn account_is_namespaced_by_provider() {
        assert_eq!(account_for_provider("anthropic"), "provider-key:anthropic");
        assert_ne!(account_for_provider("anthropic"), account_for_provider("openai"));
        assert_ne!(account_for_provider("google"), account_for_provider("custom"));
    }

    #[test]
    fn legacy_anthropic_account_differs_from_the_per_provider_one() {
        // The migration source must be a DIFFERENT account than the destination, or
        // the copy-and-delete would erase the value it just migrated.
        assert_ne!(LEGACY_ANTHROPIC_ACCOUNT, account_for_provider(LEGACY_PROVIDER));
        assert_eq!(LEGACY_ANTHROPIC_ACCOUNT, "provider-key:primary");
        // Read and removal must agree on which provider owns the legacy copy —
        // otherwise a removed key resurrects from the account nobody deleted.
        assert_eq!(LEGACY_PROVIDER, "anthropic");
    }

    #[test]
    fn device_account_is_distinct_from_provider_accounts() {
        assert_ne!(DEVICE_ACCOUNT, account_for_provider("anthropic"));
        assert!(!DEVICE_ACCOUNT.starts_with("provider-key:"));
    }

    // --- Session cache: exercised directly, no OS keychain involved. Tests own
    // distinct provider ids so parallel test threads can't collide.

    #[test]
    fn a_trace_line_never_carries_key_material() {
        // The one assertion that makes the trace safe to hand a user. A debug aid
        // that prints the secret it is debugging is worse than no debug aid — and
        // a keychain trace is precisely where that mistake is easy to make, because
        // the value is right there in the variant being reported.
        //
        // `trace_word` is asserted rather than the printed line because it is the
        // only place a KeyRead's contents could reach a trace: everything else the
        // tracer prints is a program-authored constant or an account name.
        let secret = "sk-ant-do-not-print-this-0123456789";
        let word = KeyRead::Found(secret.to_string()).trace_word();
        assert_eq!(word, "found");
        assert!(!word.contains(secret));
        // Not the length either: a length narrows a brute force.
        assert!(!word.contains(&secret.len().to_string()));
        // ...and not a prefix, which names the vendor.
        assert!(!word.contains("sk-"));
        assert_eq!(KeyRead::NothingSaved.trace_word(), "nothing-saved");
        assert_eq!(KeyRead::Unreadable.trace_word(), "unreadable");
    }

    #[test]
    fn the_trace_is_off_unless_explicitly_asked_for() {
        // Off by default in every build, release and debug: a keychain trace on by
        // default is a keychain trace in someone's support-log paste.
        //
        // ON THE VALUE, NOT ON THIS PROCESS'S ENVIRONMENT. The previous version set
        // and cleared ADDISON_KEYCHAIN_TRACE with `set_var`, which is process-global:
        // every other test thread that reached a `trace!` read the variable this one
        // was mutating, and `set_var` racing a reader is unsound, not merely untidy.
        // The rule is now a pure function and this is the whole of it.
        assert!(!trace_flag_from(None), "absent means off");
        assert!(!trace_flag_from(Some("")), "empty means off");
        assert!(!trace_flag_from(Some("0")));
        assert!(!trace_flag_from(Some("true")), "only an explicit 1 enables it");
        assert!(!trace_flag_from(Some("yes")), "only an explicit 1 enables it");
        assert!(!trace_flag_from(Some("1 ")), "not even a stray space");
        assert!(trace_flag_from(Some("1")));
    }

    #[test]
    fn a_disabled_trace_never_builds_the_line_it_will_not_print() {
        // The call sites hand `trace!` a `format!`, and an argument is evaluated
        // before the call it is an argument to — so with the check inside the callee
        // every provider-key read allocated and threw away a String, in every build,
        // with the trace off. The guard therefore lives in the MACRO, and this is the
        // difference stated as a test rather than as a claim in a comment.
        //
        // Two-sided on purpose: it asserts the line IS built when the trace is on, so
        // a run with ADDISON_KEYCHAIN_TRACE=1 measures something rather than skipping.
        let built = std::cell::Cell::new(0u32);
        fn subject(built: &std::cell::Cell<u32>) -> String {
            built.set(built.get() + 1);
            "provider=probe".to_string()
        }
        trace!("probe", &subject(&built), "…");
        assert_eq!(
            built.get(),
            u32::from(trace_enabled()),
            "the disabled trace still built its line (or the enabled one did not)"
        );
    }

    #[test]
    fn cache_round_trips_a_key() {
        cache_put("cache-test-a", "sk-value-1");
        assert_eq!(cache_get("cache-test-a").as_deref(), Some("sk-value-1"));
    }

    #[test]
    fn cache_replace_overwrites_in_place() {
        cache_put("cache-test-b", "sk-old");
        cache_put("cache-test-b", "sk-new");
        assert_eq!(cache_get("cache-test-b").as_deref(), Some("sk-new"));
    }

    #[test]
    fn cache_evict_removes_the_entry() {
        cache_put("cache-test-c", "sk-value");
        cache_evict("cache-test-c");
        assert_eq!(cache_get("cache-test-c"), None);
    }

    #[test]
    fn cache_miss_is_none_not_empty_string() {
        assert_eq!(cache_get("cache-test-never-written"), None);
    }

    #[test]
    fn cache_is_namespaced_per_provider() {
        cache_put("cache-test-d1", "sk-one");
        cache_put("cache-test-d2", "sk-two");
        cache_evict("cache-test-d1");
        assert_eq!(cache_get("cache-test-d1"), None);
        assert_eq!(cache_get("cache-test-d2").as_deref(), Some("sk-two"));
    }

    // --- Negative cache: a read that failed must not be retried at the OS until the
    // user acts on the key. Tests own distinct provider ids so parallel test threads
    // can't collide.

    #[test]
    fn a_failed_read_is_remembered_for_the_session() {
        assert!(!failure_recorded("fail-test-a"));
        failure_remember("fail-test-a");
        assert!(failure_recorded("fail-test-a"));
    }

    #[test]
    fn a_failed_read_is_forgotten_when_the_user_acts_on_the_key() {
        // Saving or removing the key is the retry signal — store/delete both call
        // this, so the very next read reaches the OS again.
        failure_remember("fail-test-b");
        failure_forget("fail-test-b");
        assert!(!failure_recorded("fail-test-b"));
    }

    #[test]
    fn failed_reads_are_tracked_per_provider() {
        failure_remember("fail-test-c1");
        failure_remember("fail-test-c2");
        failure_forget("fail-test-c1");
        assert!(!failure_recorded("fail-test-c1"));
        assert!(failure_recorded("fail-test-c2"));
    }

    #[test]
    fn a_remembered_failure_answers_without_reaching_the_os() {
        // The whole point: the widget rail probes for a key every 60 seconds, and an
        // answer from memory is what stops each poll raising a new password dialog.
        failure_remember("fail-test-d");
        assert!(matches!(cached_answer("fail-test-d", false), Some(KeyRead::Unreadable)));
    }

    #[test]
    fn a_fresh_read_retries_past_a_remembered_failure() {
        // A per-turn probe is the person acting: their next message must be allowed to
        // re-raise the dialog they dismissed, so `fresh` skips the failure memory and
        // falls through to the OS (None here — the caller goes on to read).
        failure_remember("fail-test-f");
        assert!(cached_answer("fail-test-f", true).is_none());
        // The non-fresh pollers still answer from memory.
        assert!(matches!(cached_answer("fail-test-f", false), Some(KeyRead::Unreadable)));
    }

    #[test]
    fn a_fresh_read_still_uses_a_cached_key() {
        // `fresh` bypasses only the FAILURE memory. A key already read this session is
        // the correct answer either way — re-reading it would re-prompt for nothing.
        cache_put("fail-test-g", "sk-value");
        assert!(matches!(cached_answer("fail-test-g", true), Some(KeyRead::Found(k)) if k == "sk-value"));
    }

    #[test]
    fn a_cached_key_outranks_a_remembered_failure() {
        // A readable key must never be reported as unreadable, whatever order the two
        // memories were written in.
        cache_put("fail-test-e", "sk-value");
        failure_remember("fail-test-e");
        assert!(
            matches!(cached_answer("fail-test-e", false), Some(KeyRead::Found(k)) if k == "sk-value")
        );
    }

    #[test]
    fn an_untouched_provider_has_no_answer_without_the_os() {
        assert!(cached_answer("fail-test-never-touched", false).is_none());
    }

    // --- The wire seam. Built over the real response builder (app_build.rs pattern),
    // so these stay honest if the shape changes.

    #[test]
    fn a_saved_key_is_returned_verbatim() {
        let response = provider_key_response(KeyRead::Found("sk-abc".to_string())).unwrap();
        assert_eq!(response.get("key").and_then(Value::as_str), Some("sk-abc"));
    }

    #[test]
    fn nothing_saved_is_an_empty_key_not_an_error() {
        let response = provider_key_response(KeyRead::NothingSaved).unwrap();
        assert_eq!(response.get("key").and_then(Value::as_str), Some(""));
    }

    #[test]
    fn a_failed_read_is_an_error_never_an_empty_key() {
        // The two must stay distinguishable: an empty string here tells the core "no
        // key saved", and the turn goes to the external relay while the user's key
        // sits unread in the keychain.
        let err = provider_key_response(KeyRead::Unreadable).unwrap_err();
        assert_eq!(err.code, -32000);
        assert_eq!(err.message, "Couldn't read your saved key from the keychain.");
    }

    // =======================================================================
    // WRITES — delete-then-add, verified, and what happens when it goes wrong.
    // =======================================================================
    // Exercised through `write_credential_with`'s injected operations, for the reason
    // the module comment gives: keyring's mock store cannot stand in for the OS here,
    // and a `cargo test` run must never touch the user's real keychain.

    /// A recording stand-in for one keychain item. `present` is the item itself, so a
    /// test can assert what the OS would hold after a torn write — which is the only
    /// thing that matters about the operation that can lose somebody's key.
    struct FakeItem {
        present: Cell<Option<String>>,
        ops: RefCell<Vec<&'static str>>,
        add_failures: Cell<u32>,
        delete_fails: Cell<bool>,
        read_back_fails: Cell<bool>,
    }

    impl FakeItem {
        fn holding(value: Option<&str>) -> Self {
            Self {
                present: Cell::new(value.map(str::to_string)),
                ops: RefCell::new(Vec::new()),
                add_failures: Cell::new(0),
                delete_fails: Cell::new(false),
                read_back_fails: Cell::new(false),
            }
        }

        fn write(&self, value: &str) -> Result<(), WriteFailure> {
            write_credential_with(
                value,
                &|| {
                    self.ops.borrow_mut().push("delete");
                    if self.delete_fails.get() {
                        return Err(keyring::Error::Invalid("delete".into(), "denied".into()));
                    }
                    match self.present.take() {
                        Some(_) => Ok(()),
                        None => Err(keyring::Error::NoEntry),
                    }
                },
                &|v| {
                    self.ops.borrow_mut().push("add");
                    let left = self.add_failures.get();
                    if left > 0 {
                        self.add_failures.set(left - 1);
                        return Err(keyring::Error::Invalid("add".into(), "denied".into()));
                    }
                    self.present.set(Some(v.to_string()));
                    Ok(())
                },
                &|| {
                    self.ops.borrow_mut().push("read");
                    if self.read_back_fails.get() {
                        return Err(keyring::Error::Invalid("read".into(), "denied".into()));
                    }
                    let value = self.present.take();
                    self.present.set(value.clone());
                    value.ok_or(keyring::Error::NoEntry)
                },
            )
        }

        fn ops(&self) -> Vec<&'static str> {
            self.ops.borrow().clone()
        }

        fn stored(&self) -> Option<String> {
            let value = self.present.take();
            self.present.set(value.clone());
            value
        }
    }

    #[test]
    fn a_write_is_never_an_update_in_place() {
        // THE trap, as a test. `security-framework`'s `set_password` does SecItemAdd
        // and, on errSecDuplicateItem, falls back to SecItemUpdate — which preserves
        // the item AND its old, foreign ACL. So a write that reaches an existing item
        // without deleting it first can never heal anything, and the user's own
        // instinct ("I'll just save it again") accomplishes nothing at all.
        //
        // The assertion is on the ORDER, because "delete happened" is not enough: an
        // add followed by a delete would leave the person with no key whatsoever.
        let item = FakeItem::holding(Some("sk-old"));
        assert_eq!(item.write("sk-new"), Ok(()));
        assert_eq!(
            item.ops(),
            vec!["delete", "add", "read"],
            "the item was rewritten in place — its foreign ACL survives, and so does the bug"
        );
        assert_eq!(item.stored().as_deref(), Some("sk-new"));
    }

    #[test]
    fn a_write_is_never_reported_successful_without_reading_it_back() {
        // This is the one operation in the subsystem that can lose the user's key, and
        // an add's return code is not evidence that it did not. A store that reports
        // success on a value that is not there hands the person a keychain they
        // believe holds their key, and they find out at the next launch.
        let item = FakeItem::holding(Some("sk-old"));
        item.read_back_fails.set(true);
        assert_eq!(item.write("sk-new"), Err(WriteFailure::Lost));
        assert!(
            item.ops().iter().filter(|op| **op == "read").count() >= 1,
            "the write never verified itself"
        );
    }

    #[test]
    fn a_write_retries_before_it_gives_up_on_the_key() {
        // The value is still in memory between the delete and the add, so a transient
        // failure is exactly what a retry is for — and the difference between one
        // attempt and two is the difference between a lost key and a saved one.
        let item = FakeItem::holding(Some("sk-old"));
        item.add_failures.set(1);
        assert_eq!(item.write("sk-new"), Ok(()));
        assert_eq!(item.ops(), vec!["delete", "add", "delete", "add", "read"]);
        assert_eq!(item.stored().as_deref(), Some("sk-new"));
    }

    #[test]
    fn a_write_that_cannot_be_completed_says_the_key_is_gone() {
        // Never silence, and never a stack trace. `Lost` is what makes the shipped
        // path surface KEY_LOST_MESSAGE instead of "nothing saved", which would send
        // the person into onboarding for a key they had until a moment ago.
        let item = FakeItem::holding(Some("sk-old"));
        item.add_failures.set(WRITE_ATTEMPTS);
        assert_eq!(item.write("sk-new"), Err(WriteFailure::Lost));
        assert_eq!(item.stored(), None, "the fake must model the real loss");
    }

    #[test]
    fn a_write_that_cannot_delete_leaves_the_old_key_exactly_where_it_was() {
        // The safe half of the failure split. If the delete is refused, the old item
        // may still be there under its foreign ACL — and calling `add` then is the
        // SecItemUpdate trap. So this branch writes nothing and says nothing was lost.
        let item = FakeItem::holding(Some("sk-old"));
        item.delete_fails.set(true);
        assert_eq!(item.write("sk-new"), Err(WriteFailure::Untouched));
        assert_eq!(item.ops(), vec!["delete"], "an add followed a refused delete");
        assert_eq!(item.stored().as_deref(), Some("sk-old"));
    }

    #[test]
    fn a_first_save_is_a_plain_add() {
        // Deleting an absent item is the normal case for somebody's first key, and it
        // must not be an error.
        let item = FakeItem::holding(None);
        assert_eq!(item.write("sk-first"), Ok(()));
        assert_eq!(item.stored().as_deref(), Some("sk-first"));
    }

    #[test]
    fn a_write_never_reports_success_for_somebody_elses_value() {
        // A read-back that returns something OTHER than what was written means another
        // item holds that account name. Serving it later would hand the core bytes
        // that are not the user's key, so a mismatch is a failure like any other.
        // The "add" lands, but the item reads back as something else every time.
        let result = write_credential_with(
            "sk-new",
            &|| Ok(()),
            &|_| Ok(()),
            &|| Ok("sk-somebody-else".to_string()),
        );
        assert_eq!(result, Err(WriteFailure::Lost));
    }

    // --- Self-heal: WHEN it runs. The delete-then-add above is what it runs.

    #[test]
    fn a_foreign_item_is_re_created_after_a_successful_read() {
        // Foreign means "this build is not on the item's access list", and macOS
        // answers that with a password dialog — so a successful read that WAITED is a
        // successful read of a foreign item. That is the signal, and it is why
        // self-heal needs no new OS API to know when to run.
        //
        // Spike 1 measured an app-owned read at 29 ms. The threshold sits two orders
        // of magnitude above that and far below any human answer.
        assert!(read_looked_foreign(Duration::from_secs(4)));
        assert!(read_looked_foreign(FOREIGN_READ_THRESHOLD));
        assert!(!read_looked_foreign(Duration::from_millis(29)));
        assert!(!read_looked_foreign(Duration::from_millis(200)));
        assert!(
            FOREIGN_READ_THRESHOLD >= Duration::from_millis(100),
            "a threshold this low re-mints healthy items on a slow disk — and the \
             re-mint is the one operation that can lose the key"
        );
    }

    #[test]
    fn a_migration_never_heals_the_item_it_just_created() {
        // The legacy migration waits on the LEGACY item's password dialog, so the
        // elapsed-time signal fires — but the destination item was minted by THIS
        // build moments ago and is not foreign at all. Healing it would run the one
        // data-losing operation in the subsystem for nothing.
        let slow = Duration::from_secs(3);
        assert!(!should_self_heal(true, slow), "a just-migrated item was re-created");
        // The control: an ordinary slow read of an item nobody just wrote IS healed,
        // or this test would pass for a version that never heals anything.
        assert!(should_self_heal(false, slow));
        assert!(!should_self_heal(false, Duration::from_millis(29)));
        // And the flag genuinely rides on an ordinary read as `false`, so the
        // condition above is the one the shipped path evaluates.
        assert!(!OsRead::plain(KeyRead::NothingSaved).freshly_written);
    }

    #[test]
    fn the_shipped_save_path_never_calls_set_password_directly() {
        // A source-level backstop, in the idiom this repo already uses for C6. The
        // behavioural tests above pin `write_credential_with`; this pins the SHAPE,
        // because the next version of this bug will not be a rewritten ladder — it
        // will be one convenient `entry.set_password(key)` put back into the save
        // path, which silently becomes SecItemUpdate and heals nothing.
        assert!(
            !item_source("fn store_provider_key_blocking").contains(".set_password("),
            "the save path writes with set_password again — that is SecItemUpdate on a \
             duplicate, which preserves the old foreign ACL and heals nothing"
        );
    }

    #[test]
    fn an_unchanged_save_does_not_re_create_the_item() {
        // §5.4. Every write is now a destroy-and-re-mint, so a Save that changes
        // nothing would run the dangerous operation for no reason. The cached value is
        // the comparison, and a cached value means the item was already read (and
        // healed if it needed it) this session.
        cache_put("save-test-a", "sk-same");
        let entry = Entry::new(SERVICE, "unused-in-this-path").unwrap();
        assert!(save_would_change_nothing("save-test-a", &entry, "sk-same"));
        assert!(!save_would_change_nothing("save-test-a", &entry, "sk-different"));
        cache_evict("save-test-a");
    }

    // --- §5.3: normalisation at the store boundary ------------------------

    #[test]
    fn a_key_with_a_line_break_is_refused_at_the_store_boundary() {
        // The refusal, not a repair. Addison cannot know whether the break is a paste
        // artefact or two halves of something the person meant to join, and a key it
        // quietly altered is a key that fails mysteriously a week later — which is the
        // failure §5.3 exists to convert into a fixable message.
        assert_eq!(normalised_key("sk-abc\ndef"), Err(KEY_HAS_A_LINE_BREAK));
        assert_eq!(normalised_key("sk-abc\r\ndef"), Err(KEY_HAS_A_LINE_BREAK));
        // U+2028 is a line break that is NOT a `Cc` control, so it slips past
        // `is_control` — it is named explicitly for exactly that reason.
        assert_eq!(normalised_key("sk-abc\u{2028}def"), Err(KEY_HAS_A_LINE_BREAK));
        // The control: an ordinary key is not refused, or this test would pass for a
        // version that refuses everything.
        assert_eq!(normalised_key("sk-abcdef"), Ok("sk-abcdef"));
    }

    #[test]
    fn surrounding_whitespace_is_stripped_rather_than_refused() {
        // The trailing newline is the COMMON case — it comes off the end of a copy
        // from a web page — and it is repairable without guessing, so refusing it
        // would tell somebody to fix something they cannot see. The frontend also
        // trims, but §5.3's whole point is that the frontend's trim is a courtesy and
        // this is the contract.
        assert_eq!(normalised_key("sk-abcdef\n"), Ok("sk-abcdef"));
        assert_eq!(normalised_key("  sk-abcdef\t\r\n "), Ok("sk-abcdef"));
        // NBSP off a rich-text paste is whitespace too, and at the edges it strips.
        assert_eq!(normalised_key("\u{00A0}sk-abcdef\u{00A0}"), Ok("sk-abcdef"));
    }

    #[test]
    fn hidden_characters_inside_a_key_are_refused() {
        // A zero-width space in the middle of a key is invisible in the box, invisible
        // in the provider's dashboard, and fatal at the API. Storing it produces a 401
        // that looks exactly like a wrong key.
        assert_eq!(normalised_key("sk-abc\u{200B}def"), Err(KEY_HAS_HIDDEN_CHARACTERS));
        assert_eq!(normalised_key("sk-abc\u{FEFF}def"), Err(KEY_HAS_HIDDEN_CHARACTERS));
        assert_eq!(normalised_key("sk-abc\tdef"), Err(KEY_HAS_HIDDEN_CHARACTERS));
        assert_eq!(normalised_key("sk-abc\u{00AD}def"), Err(KEY_HAS_HIDDEN_CHARACTERS));
    }

    #[test]
    fn a_blank_key_is_refused_rather_than_stored_as_nothing_saved() {
        // An empty item reads back as the empty string, which is how this subsystem
        // says "nothing saved" — and "nothing saved" is the one answer that may reach
        // the external Setup Assistant relay. A whitespace-only paste must therefore
        // never become a stored key.
        assert_eq!(normalised_key(""), Err(KEY_IS_BLANK));
        assert_eq!(normalised_key("   \n\t "), Err(KEY_IS_BLANK));
        // The seam this protects, restated: an empty stored value IS "nothing saved".
        assert_eq!(
            provider_key_response(KeyRead::NothingSaved).unwrap(),
            json!({ "key": "" })
        );
    }

    #[test]
    fn a_refusal_never_carries_any_part_of_the_key() {
        // G1. The three sentences are constants; nothing formats a value, a length or
        // a prefix into them, so a refusal cannot become the leak.
        for message in [KEY_IS_BLANK, KEY_HAS_A_LINE_BREAK, KEY_HAS_HIDDEN_CHARACTERS] {
            assert!(!message.contains("sk-"));
            assert!(!message.contains("characters long"));
        }
        // And the refusal path says what to do next, in every case (CONVENTIONS: a
        // plain message plus one suggested step).
        assert!(KEY_IS_BLANK.contains("paste"));
        assert!(KEY_HAS_A_LINE_BREAK.contains("paste"));
        assert!(KEY_HAS_HIDDEN_CHARACTERS.contains("paste"));
    }

    #[test]
    fn the_save_path_normalises_before_it_decides_the_save_changes_nothing() {
        // §5.3 composed with §5.4, and it can only be checked at the source: the real
        // path needs an OS keychain, and `cargo test` must never touch one.
        //
        // Order is the whole property. `save_would_change_nothing` compares against
        // what is STORED — a normalised value. Handing it the raw paste makes
        // "sk-same\n" look like a change to "sk-same", and the write that follows is a
        // delete-then-add: the one operation here that can lose somebody's key, run
        // for a difference that does not exist.
        let body = item_source("fn store_provider_key_blocking");
        let normalise = body
            .find("normalised_key(key)")
            .expect("the save path no longer normalises the key at all (§5.3)");
        // The CALL, not the mentions of it in the comments above.
        let compare = body
            .find("if save_would_change_nothing(")
            .expect("the unchanged-compare moved — re-point this test");
        assert!(
            normalise < compare,
            "the unchanged-compare runs before normalisation, so a re-pasted key with \
             a trailing newline re-mints the item for no change at all"
        );
    }

    #[test]
    fn a_normalised_key_matches_the_stored_one_so_the_save_short_circuits() {
        // The behavioural half of the pair above: once normalised, a re-paste of the
        // same key with paste whitespace on it compares EQUAL, so §5.4's short-circuit
        // fires and the item is left alone.
        cache_put("save-test-c", "sk-same");
        let entry = Entry::new(SERVICE, "unused-in-this-path").unwrap();
        let normalised = normalised_key(" sk-same\n").unwrap();
        assert!(save_would_change_nothing("save-test-c", &entry, normalised));
        // The control: the raw paste does NOT, which is what makes the order matter.
        assert!(!save_would_change_nothing("save-test-c", &entry, " sk-same\n"));
        cache_evict("save-test-c");
    }

    #[test]
    fn a_save_after_a_lost_repair_always_writes() {
        // The keychain holds nothing at all, and the session cache is the only copy of
        // the key left. Short-circuiting here on "it matches what I have in memory"
        // would leave the person pressing Save against an empty keychain forever —
        // the original reported symptom, reintroduced through the optimisation.
        cache_put("save-test-b", "sk-same");
        repair_lost_remember("save-test-b");
        let entry = Entry::new(SERVICE, "unused-in-this-path").unwrap();
        assert!(!save_would_change_nothing("save-test-b", &entry, "sk-same"));
        repair_lost_forget("save-test-b");
        assert!(save_would_change_nothing("save-test-b", &entry, "sk-same"));
        cache_evict("save-test-b");
    }

    #[test]
    fn a_lost_repair_is_never_reported_as_nothing_saved() {
        // The seam that keeps a lost key off the relay. "No key saved" is a normal
        // answer and the cue for the Setup Assistant; a key destroyed by a repair is
        // neither, and the sentence has to name the one thing that fixes it.
        let err = provider_key_response(KeyRead::LostInRepair).unwrap_err();
        assert_eq!(err.code, -32000);
        assert_eq!(err.message, KEY_LOST_MESSAGE);
        // The contrast that makes it load-bearing: "nothing saved" is still the
        // empty-string RESULT, and this must never become that.
        assert_eq!(
            provider_key_response(KeyRead::NothingSaved).unwrap(),
            json!({ "key": "" })
        );
        assert!(KEY_LOST_MESSAGE.contains("Add it again in Settings"));
        // G1: the sentence is a constant. It cannot carry the key, a length or a prefix.
        assert!(!KEY_LOST_MESSAGE.contains("sk-"));
    }

    #[test]
    fn the_repair_loss_memory_is_per_provider_and_cleared_by_removing_the_key() {
        // Per provider, because one provider's torn write says nothing about another's.
        // Cleared by Remove, because after a deliberate removal "nothing saved" is the
        // truth rather than a wound — and the person must be able to onboard again.
        repair_lost_remember("lost-test-a");
        assert!(repair_lost_recorded("lost-test-a"));
        assert!(!repair_lost_recorded("lost-test-b"));
        repair_lost_forget("lost-test-a");
        assert!(!repair_lost_recorded("lost-test-a"));
    }

    // --- Migration ordering, exercised through the closure seam (see the module
    // comment above for why the mock keychain can't stand in here).

    #[test]
    fn migration_deletes_the_legacy_entry_only_after_the_copy_lands() {
        let deleted = Cell::new(false);
        migrate_legacy_key(
            || true,
            || {
                deleted.set(true);
                Ok(())
            },
        );
        assert!(deleted.get(), "a successful copy must be followed by the delete");
    }

    #[test]
    fn migration_keeps_the_legacy_entry_when_the_copy_fails() {
        // Until the copy lands the legacy entry is the ONLY durable copy of the key —
        // deleting it here destroys it.
        let deleted = Cell::new(false);
        migrate_legacy_key(
            || false,
            || {
                deleted.set(true);
                Ok(())
            },
        );
        assert!(!deleted.get(), "a failed copy must leave the legacy entry in place");
    }

    /// The body of one top-level item in this file, for the source-level backstops.
    /// Two of them exist because the wiring they check cannot be reached in-process:
    /// the real paths need an OS keychain, and `cargo test` must never touch one.
    fn item_source(signature: &str) -> &'static str {
        let source = include_str!("keychain.rs");
        let start = source
            .find(signature)
            .unwrap_or_else(|| panic!("{signature} moved — re-point this test"));
        let body = &source[start..];
        let end = body[1..].find("\nfn ").expect("no following item") + 1;
        &body[..end]
    }

    #[test]
    fn the_legacy_migration_deletes_only_against_a_verified_copy() {
        // `migration_deletes_the_legacy_entry_only_after_the_copy_lands` pins the
        // ORDER through the closure seam. This pins the WIRING, which that seam
        // cannot see: the boolean handed to `migrate_legacy_key` has to be
        // `write_credential`'s VERIFIED result and not a literal or a bare return
        // code. Until the copy is confirmed readable, the legacy item is the only
        // durable copy of the key, and deleting it is how somebody loses it.
        let body = item_source("fn legacy_anthropic_key");
        assert!(
            body.contains("let copied = write_credential(destination, &key).is_ok();"),
            "the legacy copy no longer goes through the verified write"
        );
        assert!(
            body.contains("migrate_legacy_key(|| copied,"),
            "the legacy delete is gated on something other than the verified copy"
        );
    }

    #[test]
    fn get_provider_key_requires_a_provider() {
        let err = handle("keychain.getProviderKey", &json!({})).unwrap_err();
        assert_eq!(err.code, -32602);
    }

    #[test]
    fn unknown_keychain_method_is_method_not_found() {
        let err = handle("keychain.somethingElse", &json!({})).unwrap_err();
        assert_eq!(err.code, -32601);
    }

    // --- Device identity: crypto exercised directly, no OS keychain involved.

    /// Rebuild the public key from the base64 the shell would hand out and verify a
    /// produced signature against it — the exact check the relay performs server-side.
    fn verify(identity: &DeviceIdentity, payload: &Value, signature_b64: &str) -> bool {
        let pub_bytes: [u8; 32] = base64::engine::general_purpose::STANDARD
            .decode(identity.public_key_b64())
            .unwrap()
            .try_into()
            .unwrap();
        let verifying_key = VerifyingKey::from_bytes(&pub_bytes).unwrap();
        let sig_bytes: [u8; 64] = base64::engine::general_purpose::STANDARD
            .decode(signature_b64)
            .unwrap()
            .try_into()
            .unwrap();
        let signature = Signature::from_bytes(&sig_bytes);
        verifying_key
            .verify(&canonical_json_bytes(payload).unwrap(), &signature)
            .is_ok()
    }

    // --- Device-identity session cache. Exercised against a LOCAL slot, never the
    // global one: the global is a single shared cell, so parallel tests writing to it
    // would collide (unlike KEY_CACHE, which they can namespace by provider id).

    #[test]
    fn device_cache_is_empty_until_something_is_stored() {
        let slot: Mutex<Option<DeviceIdentity>> = Mutex::new(None);
        assert!(identity_cache_load(&slot).is_none());
    }

    #[test]
    fn device_cache_round_trips_the_identity() {
        let slot = Mutex::new(None);
        let identity = DeviceIdentity::generate();
        identity_cache_store(&slot, &identity);
        let cached = identity_cache_load(&slot).expect("a stored identity reads back");
        assert_eq!(cached.device_id, identity.device_id);
        assert_eq!(cached.public_key_b64(), identity.public_key_b64());
        // The PRIVATE half survives the clone: a signature made by the cached copy
        // verifies under the original public key. A cache that rotated the keypair
        // would break the relay silently, which is what `from_stored` refuses to do.
        let payload = json!({ "check": true });
        assert!(verify(&identity, &payload, &cached.sign_payload(&payload).unwrap()));
    }

    #[test]
    fn device_cache_forgets_when_the_slot_is_cleared() {
        let slot = Mutex::new(None);
        identity_cache_store(&slot, &DeviceIdentity::generate());
        *slot.lock().unwrap() = None;
        assert!(identity_cache_load(&slot).is_none());
    }

    #[test]
    fn device_cache_store_replaces_the_previous_identity() {
        let slot = Mutex::new(None);
        let first = DeviceIdentity::generate();
        let second = DeviceIdentity::generate();
        identity_cache_store(&slot, &first);
        identity_cache_store(&slot, &second);
        assert_ne!(first.device_id, second.device_id);
        assert_eq!(identity_cache_load(&slot).unwrap().device_id, second.device_id);
    }

    #[test]
    fn signature_verifies_against_the_public_key() {
        let identity = DeviceIdentity::generate();
        let payload = json!({ "sessionId": "abc", "nonce": 7 });
        let sig = identity.sign_payload(&payload).unwrap();
        assert!(verify(&identity, &payload, &sig));
    }

    #[test]
    fn signature_rejects_a_tampered_payload() {
        let identity = DeviceIdentity::generate();
        let sig = identity.sign_payload(&json!({ "amount": 1 })).unwrap();
        // Same shape, different value — must not verify under the original signature.
        assert!(!verify(&identity, &json!({ "amount": 2 }), &sig));
    }

    #[test]
    fn signing_is_deterministic_for_the_same_payload() {
        let identity = DeviceIdentity::generate();
        let payload = json!({ "a": 1, "b": [2, 3], "c": "x" });
        assert_eq!(
            identity.sign_payload(&payload).unwrap(),
            identity.sign_payload(&payload).unwrap(),
        );
    }

    #[test]
    fn canonical_bytes_are_stable_for_a_value() {
        let payload = json!({ "z": 1, "a": 2, "nested": { "k": "v" } });
        assert_eq!(
            canonical_json_bytes(&payload).unwrap(),
            canonical_json_bytes(&payload).unwrap(),
        );
    }

    #[test]
    fn stored_blob_round_trips_without_rotating_identity() {
        let identity = DeviceIdentity::generate();
        let loaded = DeviceIdentity::from_stored(&identity.to_stored()).unwrap();
        assert_eq!(loaded.device_id, identity.device_id);
        assert_eq!(loaded.public_key_b64(), identity.public_key_b64());
        // A signature made by the reloaded key still verifies — same private key.
        let payload = json!({ "check": true });
        assert!(verify(&identity, &payload, &loaded.sign_payload(&payload).unwrap()));
    }

    #[test]
    fn stored_blob_never_exposes_the_private_key_shape_as_public() {
        // The public key emitted to the core must be the 32-byte PUBLIC half, and it
        // must differ from the stored private seed.
        let identity = DeviceIdentity::generate();
        let public = identity.public_key_b64();
        let stored: Value = serde_json::from_str(&identity.to_stored()).unwrap();
        let private = stored.get("privateKey").and_then(Value::as_str).unwrap();
        assert_ne!(public, private);
        let public_bytes = base64::engine::general_purpose::STANDARD.decode(&public).unwrap();
        assert_eq!(public_bytes.len(), 32);
    }

    #[test]
    fn corrupt_stored_blob_errors_rather_than_regenerating() {
        assert!(DeviceIdentity::from_stored("not json").is_err());
        assert!(DeviceIdentity::from_stored(&json!({ "deviceId": "x" }).to_string()).is_err());
        assert!(
            DeviceIdentity::from_stored(
                &json!({ "deviceId": "x", "privateKey": "%%not-base64%%" }).to_string()
            )
            .is_err()
        );
    }

    #[test]
    fn sign_relay_request_requires_a_payload() {
        let err = handle("keychain.signRelayRequest", &json!({})).unwrap_err();
        assert_eq!(err.code, -32602);
    }

    #[test]
    fn get_device_key_response_shape() {
        // Assert over the REAL builder handle() returns (app_build.rs pattern), not a
        // json! rebuilt in the test body — otherwise this stays green while the real
        // response grows a field.
        let identity = DeviceIdentity::generate();
        let response = device_key_response(&identity);
        assert!(response.get("deviceId").and_then(Value::as_str).is_some());
        let pk = response.get("publicKey").and_then(Value::as_str).unwrap();
        assert_eq!(
            base64::engine::general_purpose::STANDARD.decode(pk).unwrap().len(),
            32
        );
        assert!(response.get("privateKey").is_none());
    }

    #[test]
    fn sign_relay_request_response_shape() {
        // Same as above: exercise the real builder, not a hand-rolled twin.
        let identity = DeviceIdentity::generate();
        let payload = json!({ "path": "/v1/setup", "body": { "hi": 1 } });
        let signature = identity.sign_payload(&payload).unwrap();
        let response = sign_relay_response(&identity, &signature);
        let sig = response.get("signature").and_then(Value::as_str).unwrap();
        assert_eq!(
            base64::engine::general_purpose::STANDARD.decode(sig).unwrap().len(),
            64
        );
        assert_eq!(
            response.get("deviceId").and_then(Value::as_str).unwrap(),
            identity.device_id
        );
    }

    #[test]
    fn no_keychain_response_ever_contains_the_private_seed() {
        // G1's most sensitive edge: the ed25519 private seed lives ONLY in the OS
        // keychain and must never reach the core. Build EVERY keychain response over
        // the real builders, serialize each, and prove the seed's base64 appears in
        // none of them. Adding the seed under ANY key name (as mutation K1 does to a
        // real handle arm) turns this red.
        let identity = DeviceIdentity::generate();
        let seed_b64 =
            base64::engine::general_purpose::STANDARD.encode(identity.signing_key.to_bytes());

        let payload = json!({ "path": "/v1/setup", "body": { "hi": 1 } });
        let signature = identity.sign_payload(&payload).unwrap();

        let responses = [
            device_key_response(&identity),
            sign_relay_response(&identity, &signature),
        ];
        for response in &responses {
            let serialized = serde_json::to_string(response).unwrap();
            assert!(
                !serialized.contains(&seed_b64),
                "a keychain response leaked the private seed: {serialized}"
            );
        }
    }
}
