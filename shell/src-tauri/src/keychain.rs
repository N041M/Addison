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
use std::time::Instant;

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
/// from here without touching the OS again, because the widget rail polls
/// `stats.get` every 60 seconds and each poll probes for a key: without this a
/// single denial becomes a fresh password dialog every minute, forever.
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

fn trace_enabled() -> bool {
    std::env::var("ADDISON_KEYCHAIN_TRACE").map(|v| v == "1").unwrap_or(false)
}

/// One trace line. `subject` is an account name or `provider=…`; `outcome` is a
/// short variant word. Both are program-authored constants or account names — never
/// a value read out of the keychain.
fn trace(event: &str, subject: &str, outcome: &str) {
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

/// The legacy role-based Anthropic account, from before the per-provider scheme.
/// Read once and migrated to `provider-key:anthropic` so an already-saved key keeps
/// working across the upgrade (see `get_provider_key`).
const LEGACY_ANTHROPIC_ACCOUNT: &str = "provider-key:primary";

/// The only provider the legacy account can hold a key for. Shared by the migration
/// read and the removal path, so the two can never disagree about which provider has
/// a second durable copy of its key.
const LEGACY_PROVIDER: &str = "anthropic";

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
    let _os = os_guard();
    let entry = Entry::new(SERVICE, &account_for_provider(provider))
        .map_err(|_| "Couldn't reach the system keychain to save your key.".to_string())?;
    entry
        .set_password(key)
        .map_err(|_| "Couldn't save your key to the system keychain.".to_string())?;
    // Keep the session cache coherent so a Replace takes effect immediately
    // without another OS keychain round-trip (and prompt).
    cache_put(provider, key);
    // Saving a key is the user's retry signal for a read that failed earlier this
    // session — drop the negative entry so reads reach the OS again.
    failure_forget(provider);
    Ok(())
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

    let _os = os_guard();
    // Evict AGAIN under the lock. A read parked at a password dialog holds the lock
    // with nothing cached yet; if the user answers that dialog after clicking Remove,
    // the read's `cache_put` (and a dismissal's `failure_remember`) land AFTER the
    // eviction above — and a removed key must never keep being served from memory.
    cache_evict(provider);
    failure_forget(provider);
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
/// written against (`provider_key_response`). The three cases are deliberately
/// distinct: collapsing the last two is what let a DENIED read look like "no key
/// saved", so the core quietly rerouted the turn to the external Setup Assistant
/// relay while the user's key sat in the keychain the whole time.
enum KeyRead {
    /// A key is saved. The value goes core-ward and nowhere else.
    Found(String),
    /// Nothing is saved for this provider — a normal answer, not an error.
    NothingSaved,
    /// The read itself failed (denied dialog, keychain error). A key MAY exist, so
    /// this must never be reported as "no key saved".
    Unreadable,
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
        }
    }
}

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
    trace("ask", &format!("provider={provider} fresh={fresh}"), "…");
    if let Some(answer) = cached_answer(provider, fresh) {
        trace("answered", &format!("provider={provider}"), &format!("{} (no OS touch)", answer.trace_word()));
        return answer;
    }
    let _os = os_guard();
    // Re-check under the OS lock: a caller that queued behind another request for the
    // same provider must read ITS result rather than raise a second dialog for an
    // item that has just been fetched.
    if let Some(answer) = cached_answer(provider, fresh) {
        trace("answered", &format!("provider={provider}"), &format!("{} (no OS touch, post-lock)", answer.trace_word()));
        return answer;
    }

    let outcome = read_provider_key_from_os(provider);
    trace("cached-as", &format!("provider={provider}"), outcome.trace_word());
    match &outcome {
        KeyRead::Found(key) => cache_put(provider, key),
        // Remembered so the next stats poll answers from memory instead of raising
        // another dialog. `NothingSaved` is deliberately never remembered.
        KeyRead::Unreadable => failure_remember(provider),
        KeyRead::NothingSaved => {}
    }
    outcome
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
fn read_provider_key_from_os(provider: &str) -> KeyRead {
    let account = account_for_provider(provider);
    // THE LINE THAT COSTS A PASSWORD DIALOG. Every one of these is one prompt on a
    // build whose ACL has been invalidated; counting them is the whole diagnostic.
    trace("OS-TOUCH", &account, "reading…");
    let Ok(entry) = Entry::new(SERVICE, &account) else {
        trace("OS-TOUCH", &account, "unreadable (no entry handle)");
        return KeyRead::Unreadable;
    };
    match entry.get_password() {
        Ok(key) => KeyRead::Found(key),
        Err(keyring::Error::NoEntry) if provider == LEGACY_PROVIDER => legacy_anthropic_key(&entry),
        Err(keyring::Error::NoEntry) => KeyRead::NothingSaved,
        Err(_) => KeyRead::Unreadable,
    }
}

/// Read the legacy Anthropic key and migrate it into `destination`. Returns the key
/// either way — a migration that couldn't complete must not cost the user their key
/// for this launch.
fn legacy_anthropic_key(destination: &Entry) -> KeyRead {
    // A SECOND OS touch inside one logical read — and therefore a second dialog.
    // Only reached when `provider-key:anthropic` reported NoEntry, but that is
    // exactly the case worth seeing in a trace: `NothingSaved` is never cached, so
    // if this path is live it runs again on the very next caller.
    trace("OS-TOUCH", LEGACY_ANTHROPIC_ACCOUNT, "reading (legacy migration)…");
    let Ok(legacy) = Entry::new(SERVICE, LEGACY_ANTHROPIC_ACCOUNT) else {
        return KeyRead::Unreadable;
    };
    match legacy.get_password() {
        Ok(key) => {
            trace("OS-TOUCH", LEGACY_ANTHROPIC_ACCOUNT, "found -> migrating + WRITING + deleting");
            migrate_legacy_key(|| destination.set_password(&key), || legacy.delete_credential());
            KeyRead::Found(key)
        }
        // Nothing to migrate: there is simply no Anthropic key saved.
        Err(keyring::Error::NoEntry) => KeyRead::NothingSaved,
        Err(_) => KeyRead::Unreadable,
    }
}

/// Best-effort migration, copy THEN delete. The ordering is load-bearing: until the
/// copy lands, the legacy entry is the only durable copy of the key, so the delete
/// is attempted only when `copy` reports success. When the copy fails the legacy
/// entry stays exactly where it is and the migration is retried on the next read —
/// which is only true in every ordering because of this guard.
///
/// Written over closures so the ordering is testable without an OS keychain.
fn migrate_legacy_key(
    copy: impl FnOnce() -> Result<(), keyring::Error>,
    delete_legacy: impl FnOnce() -> Result<(), keyring::Error>,
) {
    if copy().is_ok() {
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
    trace("ask", DEVICE_ACCOUNT, "…");
    if let Some(identity) = identity_cache_load(device_cache()) {
        trace("answered", DEVICE_ACCOUNT, "cached (no OS touch)");
        return Ok(identity);
    }
    let _os = os_guard();
    // Re-check under the OS lock, for the same reason as the provider read: the two
    // calls one relay message makes must not both raise a dialog for the same item.
    if let Some(identity) = identity_cache_load(device_cache()) {
        trace("answered", DEVICE_ACCOUNT, "cached (no OS touch, post-lock)");
        return Ok(identity);
    }

    trace("OS-TOUCH", DEVICE_ACCOUNT, "reading…");
    let entry = Entry::new(SERVICE, DEVICE_ACCOUNT).map_err(|_| {
        RpcError::app("Couldn't reach the system keychain for your device identity.")
    })?;
    let identity = match entry.get_password() {
        // A corrupt blob errors out rather than being cached — only a usable identity
        // is ever remembered.
        Ok(blob) => DeviceIdentity::from_stored(&blob)?,
        Err(keyring::Error::NoEntry) => {
            trace("OS-TOUCH", DEVICE_ACCOUNT, "no entry -> generating + WRITING");
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
    use std::cell::Cell;

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
        std::env::remove_var("ADDISON_KEYCHAIN_TRACE");
        assert!(!trace_enabled());
        std::env::set_var("ADDISON_KEYCHAIN_TRACE", "0");
        assert!(!trace_enabled(), "only an explicit 1 enables it");
        std::env::set_var("ADDISON_KEYCHAIN_TRACE", "1");
        assert!(trace_enabled());
        std::env::remove_var("ADDISON_KEYCHAIN_TRACE");
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

    // --- Migration ordering, exercised through the closure seam (see the module
    // comment above for why the mock keychain can't stand in here).

    #[test]
    fn migration_deletes_the_legacy_entry_only_after_the_copy_lands() {
        let deleted = Cell::new(false);
        migrate_legacy_key(
            || Ok(()),
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
            || Err(keyring::Error::NoEntry),
            || {
                deleted.set(true);
                Ok(())
            },
        );
        assert!(!deleted.get(), "a failed copy must leave the legacy entry in place");
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
