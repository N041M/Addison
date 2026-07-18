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

use base64::Engine as _;
use ed25519_dalek::{Signer, SigningKey};
use keyring::Entry;
use rand_core::OsRng;
use serde_json::{json, Value};
use uuid::Uuid;

use crate::ipc::{required_str, RpcError};

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

/// Webview -> Shell. Write-only path for a BYOK key the user typed. The key goes
/// straight into the OS keychain, keyed by provider id, and is never echoed back
/// anywhere (§8.3).
#[tauri::command]
pub fn store_provider_key(provider: String, key: String) -> Result<(), String> {
    let entry = Entry::new(SERVICE, &account_for_provider(&provider))
        .map_err(|_| "Couldn't reach the system keychain to save your key.".to_string())?;
    entry
        .set_password(&key)
        .map_err(|_| "Couldn't save your key to the system keychain.".to_string())?;
    Ok(())
}

/// Webview -> Shell. Delete a provider's stored key (the "Remove" action). A
/// missing entry is treated as success — removing an absent key is idempotent.
#[tauri::command]
pub fn delete_provider_key(provider: String) -> Result<(), String> {
    let entry = Entry::new(SERVICE, &account_for_provider(&provider))
        .map_err(|_| "Couldn't reach the system keychain to remove your key.".to_string())?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err("Couldn't remove your key from the system keychain.".to_string()),
    }
}

/// Agent-Core-internal read. Returns the stored key for a provider, or a keyring
/// error (notably `NoEntry` when nothing is saved yet). Never exposed as a Tauri
/// command, so the webview has no route to it.
///
/// Backward compat: on the first read for `anthropic` with no per-provider entry,
/// fall back to the legacy role-based account (`provider-key:primary`) and MIGRATE
/// it into `provider-key:anthropic` (best-effort) so an existing key survives the
/// upgrade to the per-provider scheme without the user re-pasting it.
fn get_provider_key(provider: &str) -> Result<String, keyring::Error> {
    let entry = Entry::new(SERVICE, &account_for_provider(provider))?;
    match entry.get_password() {
        Ok(key) => Ok(key),
        Err(keyring::Error::NoEntry) if provider == "anthropic" => {
            let legacy = Entry::new(SERVICE, LEGACY_ANTHROPIC_ACCOUNT)?;
            let key = legacy.get_password()?; // propagates NoEntry when there's nothing to migrate
            // Best-effort migration: copy into the per-provider account and drop the
            // legacy one. A failure here doesn't fail the read — the value is returned
            // regardless, and the migration retries on the next read.
            let _ = entry.set_password(&key);
            let _ = legacy.delete_credential();
            Ok(key)
        }
        Err(e) => Err(e),
    }
}

/// The device identity: a stable public `device_id` plus the ed25519 signing key
/// whose PRIVATE half lives only in the OS keychain. Built exclusively by
/// `ensure_device_keypair` (load-or-generate). Deliberately does NOT derive
/// `Debug`, so the private key can never be accidentally formatted into a log line.
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

/// Load the device identity, generating and persisting it on first use. Idempotent:
/// once stored, every later call LOADS the same keypair and never regenerates —
/// regenerating would rotate the device's identity out from under the relay. The
/// private key is only ever materialized here as an in-memory `SigningKey`; it is
/// never returned, logged, or emitted.
fn ensure_device_keypair() -> Result<DeviceIdentity, RpcError> {
    let entry = Entry::new(SERVICE, DEVICE_ACCOUNT).map_err(|_| {
        RpcError::app("Couldn't reach the system keychain for your device identity.")
    })?;
    match entry.get_password() {
        Ok(blob) => DeviceIdentity::from_stored(&blob),
        Err(keyring::Error::NoEntry) => {
            let identity = DeviceIdentity::generate();
            entry
                .set_password(&identity.to_stored())
                .map_err(|_| RpcError::app("Couldn't save your device identity to the keychain."))?;
            Ok(identity)
        }
        Err(_) => Err(RpcError::app("Couldn't read your device identity from the keychain.")),
    }
}

/// Handle a `keychain.*` request the core sent over stdout. Returns the JSON-RPC
/// `result` value on success, or an `RpcError` the core relays as plain language.
/// The returned key value is written straight back to the core's stdin by the
/// caller (agent_process.rs) — it never passes through the webview.
pub fn handle(method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        // {provider} -> {key}. Per-call, never cached shell-side (§5).
        "keychain.getProviderKey" => {
            let provider = required_str(params, "provider", "A provider is required.")?;
            match get_provider_key(provider) {
                Ok(key) => Ok(json!({ "key": key })),
                // Clean, value-free error. The core turns this into its own
                // "no key yet, here's how to add one" message.
                Err(keyring::Error::NoEntry) => {
                    Err(RpcError::app("No API key is saved for this yet."))
                }
                Err(_) => Err(RpcError::app("Couldn't read your saved key from the keychain.")),
            }
        }
        // {} -> {deviceId, publicKey}. Generates the keypair on first use, loads it
        // thereafter (§5). Returns the PUBLIC half only — the private key never
        // leaves the keychain.
        "keychain.getDeviceKey" => {
            let identity = ensure_device_keypair()?;
            Ok(json!({
                "deviceId": identity.device_id,
                "publicKey": identity.public_key_b64(),
            }))
        }
        // {payload} -> {signature, deviceId}. Signs the canonical JSON of `payload`
        // with the device private key (which stays in the keychain) and hands back
        // only the base64 signature + the public device id.
        "keychain.signRelayRequest" => {
            let payload = params
                .get("payload")
                .ok_or_else(|| RpcError::invalid_params("A payload to sign is required."))?;
            let identity = ensure_device_keypair()?;
            Ok(json!({
                "signature": identity.sign_payload(payload)?,
                "deviceId": identity.device_id,
            }))
        }
        other => Err(RpcError::method_not_found(other)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};

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
        assert_ne!(LEGACY_ANTHROPIC_ACCOUNT, account_for_provider("anthropic"));
        assert_eq!(LEGACY_ANTHROPIC_ACCOUNT, "provider-key:primary");
    }

    #[test]
    fn device_account_is_distinct_from_provider_accounts() {
        assert_ne!(DEVICE_ACCOUNT, account_for_provider("anthropic"));
        assert!(!DEVICE_ACCOUNT.starts_with("provider-key:"));
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
        // Shape assembled from a generated identity, mirroring the handle() arm
        // without touching the OS keychain.
        let identity = DeviceIdentity::generate();
        let response = json!({
            "deviceId": identity.device_id,
            "publicKey": identity.public_key_b64(),
        });
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
        let identity = DeviceIdentity::generate();
        let payload = json!({ "path": "/v1/setup", "body": { "hi": 1 } });
        let response = json!({
            "signature": identity.sign_payload(&payload).unwrap(),
            "deviceId": identity.device_id,
        });
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
}
