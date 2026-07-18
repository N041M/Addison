// JSON-RPC relay: frontend (webview) <-> Agent Core (Python) — engineering-spec §7.
//
// The shell is a relay, not a decision-maker: it forwards frames the Agent Core
// has already validated against the permission gate. The webview never talks to
// the Agent Core or the network directly (§1.3).
//
// Frames flow two ways:
//   webview -> core : the `send_to_core` command below writes one JSON line to the
//                     core's stdin. It is FIRE-AND-FORWARD — the core's response
//                     comes back asynchronously as a `core-message` event
//                     (agent_process.rs), which the webview correlates by `id`.
//   core   -> shell : `shell.*` / `keychain.*` requests are handled in-process by
//                     filesystem.rs / keychain.rs (never emitted to the webview).
//   core   -> webview: everything else is relayed verbatim as a `core-message` event.

use serde_json::{json, Value};
use tauri::State;
use tokio::io::AsyncWriteExt;

use crate::agent_process::CoreStdin;

/// Core->Shell method namespaces. These are Rust-internal (§1.3, §5): the webview
/// must never be able to invoke them, so `send_to_core` rejects any frame whose
/// method starts with either prefix (defense in depth — the core is the only
/// legitimate caller, over stdout).
pub const SHELL_PREFIX: &str = "shell.";
pub const KEYCHAIN_PREFIX: &str = "keychain.";

/// A JSON-RPC error the core can relay to the user as plain language. `message`
/// is already user-facing (no stack traces, no jargon — CLAUDE.md); the core
/// surfaces it as-is or maps it to its own no-key/next-step copy. `Debug` is safe
/// to derive: this type only ever carries an int code and a plain message — never
/// key material — so it can appear in test failures without leaking a secret.
#[derive(Debug)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
}

impl RpcError {
    /// Server-defined, application-level error (JSON-RPC reserves -32000..=-32099).
    /// Used for user-facing conditions: a cancelled dialog, a refused overwrite,
    /// a missing key. The `message` must already be plain language.
    pub fn app(message: impl Into<String>) -> Self {
        Self { code: -32000, message: message.into() }
    }

    pub fn invalid_params(message: impl Into<String>) -> Self {
        Self { code: -32602, message: message.into() }
    }

    pub fn method_not_found(method: &str) -> Self {
        Self { code: -32601, message: format!("Unknown method: {method}") }
    }

    pub fn to_value(&self) -> Value {
        json!({ "code": self.code, "message": self.message })
    }
}

/// Pull a required string parameter out of a JSON-RPC `params` object, or return a
/// plain-language `invalid_params` error. A missing key AND a present-but-non-string
/// value both yield `missing` — the caller words it for the field ("A file name is
/// required."). Consolidates the `get(..).and_then(as_str).ok_or_else(..)` that every
/// shell/keychain handler otherwise repeats verbatim.
pub fn required_str<'a>(params: &'a Value, key: &str, missing: &str) -> Result<&'a str, RpcError> {
    params
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params(missing))
}

/// True for methods the core sends *back* to the shell — never callable from the
/// webview, always handled in-process.
pub fn is_shell_bound(method: &str) -> bool {
    method.starts_with(SHELL_PREFIX) || method.starts_with(KEYCHAIN_PREFIX)
}

/// Validate a frame the webview wants relayed to the core. Rejects anything that
/// isn't a JSON-RPC-ish request object, and — critically — refuses `shell.*` /
/// `keychain.*` methods so the lowest-trust process can never drive the shell's
/// OS-level side (§1.3, §8). Returns a plain-language reason on rejection.
pub fn validate_outbound_frame(frame: &Value) -> Result<(), String> {
    let obj = frame
        .as_object()
        .ok_or_else(|| "Message to the engine must be a JSON object.".to_string())?;

    let method = obj
        .get("method")
        .and_then(Value::as_str)
        .ok_or_else(|| "Message to the engine must name a method.".to_string())?;

    if is_shell_bound(method) {
        return Err(format!(
            "Method \"{method}\" is internal to the app and can't be called from the window."
        ));
    }
    Ok(())
}

/// Webview -> Core. Validate, then write the frame as one line to the core's stdin.
/// Never interprets the frame's meaning; never waits for a reply (responses arrive
/// via the `core-message` event stream).
#[tauri::command]
pub async fn send_to_core(frame: Value, core: State<'_, CoreStdin>) -> Result<(), String> {
    validate_outbound_frame(&frame)?;

    let mut line =
        serde_json::to_string(&frame).map_err(|_| "That message couldn't be encoded.".to_string())?;
    line.push('\n');

    let mut guard = core.0.lock().await;
    let stdin = guard
        .as_mut()
        .ok_or_else(|| "Addison's engine isn't running right now.".to_string())?;
    stdin
        .write_all(line.as_bytes())
        .await
        .map_err(|_| "Addison's engine isn't responding right now.".to_string())?;
    stdin
        .flush()
        .await
        .map_err(|_| "Addison's engine isn't responding right now.".to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn accepts_a_normal_frontend_request() {
        let frame = json!({ "jsonrpc": "2.0", "method": "conversation.sendMessage", "id": 1 });
        assert!(validate_outbound_frame(&frame).is_ok());
    }

    #[test]
    fn rejects_shell_methods_from_the_webview() {
        for m in ["shell.saveNewFile", "shell.pickFile", "shell.readClipboard"] {
            let frame = json!({ "jsonrpc": "2.0", "method": m, "id": 1 });
            assert!(
                validate_outbound_frame(&frame).is_err(),
                "{m} must be rejected — it is Core->Shell only"
            );
        }
    }

    #[test]
    fn rejects_keychain_methods_from_the_webview() {
        for m in ["keychain.getProviderKey", "keychain.getDeviceKey", "keychain.signRelayRequest"] {
            let frame = json!({ "jsonrpc": "2.0", "method": m });
            assert!(
                validate_outbound_frame(&frame).is_err(),
                "{m} must be rejected — keys never cross to the webview"
            );
        }
    }

    #[test]
    fn rejects_non_object_and_methodless_frames() {
        assert!(validate_outbound_frame(&json!("hi")).is_err());
        assert!(validate_outbound_frame(&json!([1, 2, 3])).is_err());
        assert!(validate_outbound_frame(&json!({ "id": 1 })).is_err());
        assert!(validate_outbound_frame(&json!({ "method": 42 })).is_err());
    }

    #[test]
    fn required_str_extracts_present_string_and_rejects_missing_or_wrong_type() {
        let params = json!({ "name": "x", "count": 5 });
        assert_eq!(required_str(&params, "name", "need name").unwrap(), "x");
        // Missing key -> the caller's plain-language message, invalid_params code.
        let missing = required_str(&params, "other", "need other").unwrap_err();
        assert_eq!(missing.code, -32602);
        assert_eq!(missing.message, "need other");
        // Present but not a string -> same missing message (matches prior semantics).
        let wrong_type = required_str(&params, "count", "need count as text").unwrap_err();
        assert_eq!(wrong_type.code, -32602);
    }

    #[test]
    fn is_shell_bound_covers_both_namespaces() {
        assert!(is_shell_bound("shell.openExternal"));
        assert!(is_shell_bound("keychain.getProviderKey"));
        assert!(!is_shell_bound("conversation.sendMessage"));
        assert!(!is_shell_bound("model.availableRoles"));
    }
}
