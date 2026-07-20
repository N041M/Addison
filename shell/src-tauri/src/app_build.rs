// shell.appBuildRef — the build-*reference* half of G4 (contract §7.3, amendment §14.1).
//
// A permanent anchor records which build of Addison it was minted on, so a
// restore can say plainly "this restore point was saved on a different version
// of Addison" instead of silently putting old settings under a newer app. That
// is all this is: a short string the core stores beside the snapshot and
// compares later. Nothing here — and nothing anywhere in this codebase —
// replaces a binary; re-installing a prior build is the updater's job and is
// deliberately not built (contract §12 Q8, owner decision 2026-07-20).
//
// Core->Shell only, like the rest of filesystem.rs: there is intentionally NO
// `#[tauri::command]` for this. ipc.rs refuses every `shell.*` frame that comes
// from the window, and that refusal must stay — the webview has no business
// asking the highest-trust process about the binary it is running from.
//
// TWO keys, `version` and `identifier`, and no third. An earlier draft carried
// the executable path; it is dropped on purpose. It goes stale on any move or
// reinstall, nothing reads it, and it usually embeds the user's account name —
// which would then be written into a plaintext snapshot sidecar and into every
// permanent anchor, forever.

use serde_json::{json, Value};
use tauri::AppHandle;

use crate::ipc::RpcError;

// The whole body of `app_build_ref` except for reading the two values off the
// AppHandle. Split out ONLY so the shape is testable for real: a test that
// rebuilds the same `json!` literal by hand cannot fail, and would stay green
// while someone added a third key to the function it claims to pin.
fn build_ref(version: &str, identifier: &str) -> Value {
    json!({
        "version": version,
        "identifier": identifier,
    })
}

// shell.appBuildRef {} -> {version, identifier}
pub fn app_build_ref(app: &AppHandle) -> Result<Value, RpcError> {
    Ok(build_ref(
        &app.package_info().version.to_string(),
        &app.config().identifier,
    ))
}

#[cfg(test)]
mod tests {
    use super::build_ref;
    use serde_json::Value;

    /// The shape is what the core stores and later compares, so pin it here as
    /// well as in the Python fixture: exactly two string keys, no path.
    ///
    /// This asserts over the REAL builder. The two values are supplied by the
    /// test because reading them needs a live AppHandle, which a unit test has no
    /// business spawning — but the keys, the count, and the absence of a path are
    /// the function's own, so adding a third key turns this red.
    #[test]
    fn the_build_reference_carries_two_keys_and_no_filesystem_path() {
        let value = build_ref("0.1.0", "app.addison.desktop");
        let obj = value.as_object().expect("object");
        assert_eq!(obj.len(), 2);
        assert_eq!(obj.get("version").and_then(Value::as_str), Some("0.1.0"));
        assert_eq!(
            obj.get("identifier").and_then(Value::as_str),
            Some("app.addison.desktop")
        );
        // The one field that must never come back: a path leaks the account name
        // into a plaintext sidecar (see the module comment).
        assert!(!obj.contains_key("exePath"));
    }
}
