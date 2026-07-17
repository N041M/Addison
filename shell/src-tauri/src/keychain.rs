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

use keyring::Entry;
use serde_json::{json, Value};

use crate::ipc::RpcError;

/// Keychain service name — matches the app identifier (tauri.conf.json).
const SERVICE: &str = "app.addison.desktop";

/// Keychain account for a provider key, namespaced by model *role*
/// (`primary` | `local` | `setup_assistant`). The role is the only key the core
/// has when it later asks for it (`keychain.getProviderKey {role}`), so storage
/// is keyed by role: one key per role at a time, overwritten when the user
/// swaps the provider behind a role. The concrete provider id is non-secret and
/// tracked by the core in `provider_config` (SQLite, §3) — not needed here.
fn account_for_role(role: &str) -> String {
    format!("provider-key:{role}")
}

/// Webview -> Shell. Write-only path for a BYOK key the user typed. The key goes
/// straight into the OS keychain and is never echoed back anywhere. `provider` is
/// accepted for a complete call signature but isn't part of the storage location
/// (see `account_for_role`); the core owns the role->provider mapping.
#[tauri::command]
pub fn store_provider_key(role: String, provider: String, key: String) -> Result<(), String> {
    // Touch `provider` without ever touching `key` in a log line (§8.3).
    let _ = &provider;
    let entry = Entry::new(SERVICE, &account_for_role(&role))
        .map_err(|_| "Couldn't reach the system keychain to save your key.".to_string())?;
    entry
        .set_password(&key)
        .map_err(|_| "Couldn't save your key to the system keychain.".to_string())?;
    Ok(())
}

/// Agent-Core-internal read. Returns the stored key for a role, or a keyring error
/// (notably `NoEntry` when nothing is saved yet). Never exposed as a Tauri command,
/// so the webview has no route to it.
fn get_provider_key(role: &str) -> Result<String, keyring::Error> {
    Entry::new(SERVICE, &account_for_role(role))?.get_password()
}

/// Handle a `keychain.*` request the core sent over stdout. Returns the JSON-RPC
/// `result` value on success, or an `RpcError` the core relays as plain language.
/// The returned key value is written straight back to the core's stdin by the
/// caller (agent_process.rs) — it never passes through the webview.
pub fn handle(method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        // {role} -> {key}. Per-call, never cached shell-side (§5).
        "keychain.getProviderKey" => {
            let role = params
                .get("role")
                .and_then(Value::as_str)
                .ok_or_else(|| RpcError::invalid_params("A model role is required."))?;
            match get_provider_key(role) {
                Ok(key) => Ok(json!({ "key": key })),
                // Clean, value-free error. The core turns this into its own
                // "no key yet, here's how to add one" message.
                Err(keyring::Error::NoEntry) => {
                    Err(RpcError::app("No API key is saved for this yet."))
                }
                Err(_) => Err(RpcError::app("Couldn't read your saved key from the keychain.")),
            }
        }
        // Device identity keypair lands with the Setup Assistant relay (step 9).
        "keychain.getDeviceKey" => {
            Err(RpcError::app("Device identity isn't set up yet."))
        }
        other => Err(RpcError::method_not_found(other)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn account_is_namespaced_by_role() {
        assert_eq!(account_for_role("primary"), "provider-key:primary");
        assert_ne!(account_for_role("primary"), account_for_role("local"));
    }

    #[test]
    fn get_device_key_is_a_clean_not_ready_error() {
        let err = handle("keychain.getDeviceKey", &json!({})).unwrap_err();
        assert_eq!(err.code, -32000);
        assert!(!err.message.is_empty());
    }

    #[test]
    fn get_provider_key_requires_a_role() {
        let err = handle("keychain.getProviderKey", &json!({})).unwrap_err();
        assert_eq!(err.code, -32602);
    }

    #[test]
    fn unknown_keychain_method_is_method_not_found() {
        let err = handle("keychain.somethingElse", &json!({})).unwrap_err();
        assert_eq!(err.code, -32601);
    }
}
