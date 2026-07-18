// Native file picker + scoped file handles — engineering-spec §1.3, §7.4.1, design-doc §9.
//
// SECURITY PROPERTY: the Agent Core never receives a raw path it can wander with.
// It gets an opaque handle to whatever the OS-native picker returned, so it
// structurally cannot read/write outside the user's live selection. This module
// is the OS half of the ShellBridge contract (agent_core/tools/base.py); the core
// half calls these methods over stdio. Every effect here is user-initiated through
// a native dialog or scoped to a handle/path the shell itself minted this session.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use base64::Engine as _;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::ipc::RpcError;

/// Session-scoped bookkeeping, held in Tauri managed state.
#[derive(Default)]
pub struct FileState {
    /// Paths the shell CREATED this session via `shell.saveNewFile`. `shell.deleteFile`
    /// (save_file's undo path) will only touch a path in this set — defense in depth
    /// so the undo route can't be steered into deleting an arbitrary file.
    created: Mutex<HashSet<PathBuf>>,
    /// Paths the shell created and then REMOVED via `shell.deleteFile` this session.
    /// `shell.restoreFile` (save_file's redo path) will only write a path in this
    /// set — redo can re-create exactly what undo removed, and nothing else.
    deleted: Mutex<HashSet<PathBuf>>,
    /// Opaque handle -> path the user picked this session. The core only ever sees
    /// the handle; `shell.readScopedFile` resolves it. Not persisted: handles die
    /// with the session.
    handles: Mutex<HashMap<String, PathBuf>>,
}

/// Route a `shell.*` request from the core to its handler. Returns the JSON-RPC
/// `result` value, or an `RpcError` the core relays as plain language.
pub async fn handle(app: &AppHandle, method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        "shell.saveNewFile" => save_new_file(app, params).await,
        "shell.deleteFile" => delete_file(app, params),
        "shell.restoreFile" => restore_file(app, params),
        "shell.pickFile" => pick_file(app).await,
        "shell.readScopedFile" => read_scoped_file(app, params),
        "shell.openExternal" => open_external(params),
        "shell.readClipboard" => read_clipboard(),
        // Mail/messaging draft handoff (shell.openDraft/discardDraft) needs a real,
        // reversible compose surface to satisfy draft_message's undo contract; it
        // is not built in this step. Fail cleanly rather than pretend.
        "shell.openDraft" | "shell.discardDraft" => {
            Err(RpcError::app("Opening email drafts isn't available yet."))
        }
        other => Err(RpcError::method_not_found(other)),
    }
}

// shell.saveNewFile {filename, content} -> {path}
async fn save_new_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let filename = params
        .get("filename")
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params("A file name is required."))?
        .to_string();
    let content = params
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params("There's nothing to save."))?
        .to_string();

    let seed = filename.clone();
    let picked: Option<PathBuf> =
        on_main(app, move || rfd::FileDialog::new().set_file_name(seed).save_file()).await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;

    // create_new(true) REFUSES an existing file even if the dialog let the user
    // point at one — this is what keeps save_file's undo trivial (just delete what
    // we created) and is required by §7.4.1 regardless of dialog behaviour.
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::AlreadyExists => {
                RpcError::app("A file with that name is already there — please choose another name.")
            }
            _ => RpcError::app("Addison couldn't save that file."),
        })?;
    file.write_all(content.as_bytes())
        .map_err(|_| RpcError::app("Addison couldn't save that file."))?;

    app.state::<FileState>()
        .created
        .lock()
        .expect("created-files lock")
        .insert(path.clone());

    Ok(json!({ "path": path.to_string_lossy() }))
}

// shell.deleteFile {path} -> {}   (save_file's undo path)
fn delete_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(
        params
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::invalid_params("A file path is required."))?,
    );

    let state = app.state::<FileState>();
    {
        let created = state.created.lock().expect("created-files lock");
        if !created.contains(&path) {
            // Only ever remove what we made this session — never an arbitrary path.
            return Err(RpcError::app("Addison can only remove a file it just created."));
        }
    }
    std::fs::remove_file(&path).map_err(|_| RpcError::app("Addison couldn't remove that file."))?;
    state.created.lock().expect("created-files lock").remove(&path);
    // The path graduates to the restorable set: redo may re-create it, once.
    state.deleted.lock().expect("deleted-files lock").insert(path);
    Ok(json!({}))
}

// shell.restoreFile {path, content} -> {}   (save_file's redo path)
//
// Only re-creates a file that `shell.deleteFile` removed THIS SESSION — the
// mirror of delete's allowlist, so redo structurally cannot write anywhere new.
fn restore_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(
        params
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::invalid_params("A file path is required."))?,
    );
    let content = params
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params("There's nothing to put back."))?
        .to_string();

    let state = app.state::<FileState>();
    {
        let deleted = state.deleted.lock().expect("deleted-files lock");
        if !deleted.contains(&path) {
            return Err(RpcError::app("Addison can only put back a file it just removed."));
        }
    }
    // create_new: if something ELSE now lives at that path, refuse rather than
    // overwrite — same §7.4.1 rule as saving.
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::AlreadyExists => {
                RpcError::app("A file with that name is already there — nothing was changed.")
            }
            _ => RpcError::app("Addison couldn't put that file back."),
        })?;
    file.write_all(content.as_bytes())
        .map_err(|_| RpcError::app("Addison couldn't put that file back."))?;

    state.deleted.lock().expect("deleted-files lock").remove(&path);
    state.created.lock().expect("created-files lock").insert(path);
    Ok(json!({}))
}

// shell.pickFile {} -> {fileHandle}   (opaque handle, never a raw path)
async fn pick_file(app: &AppHandle) -> Result<Value, RpcError> {
    let picked: Option<PathBuf> =
        on_main(app, move || rfd::FileDialog::new().pick_file()).await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;

    let handle = uuid::Uuid::new_v4().to_string();
    app.state::<FileState>()
        .handles
        .lock()
        .expect("file-handles lock")
        .insert(handle.clone(), path);
    Ok(json!({ "fileHandle": handle }))
}

// shell.readScopedFile {fileHandle} -> {content, kind}
fn read_scoped_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let handle = params
        .get("fileHandle")
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params("A file handle is required."))?;

    // Resolve ONLY a handle we minted; a raw/unknown handle reads nothing.
    let state = app.state::<FileState>();
    let path = {
        let handles = state.handles.lock().expect("file-handles lock");
        handles.get(handle).cloned()
    }
    .ok_or_else(|| RpcError::app("Addison can't read that file — please pick it again."))?;

    let bytes = std::fs::read(&path).map_err(|_| RpcError::app("Addison couldn't read that file."))?;

    if is_image_path(&path) {
        let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
        Ok(json!({ "content": encoded, "kind": "image" }))
    } else if let Ok(text) = String::from_utf8(bytes) {
        Ok(json!({ "content": text, "kind": "text" }))
    } else {
        Err(RpcError::app("Addison can't read that kind of file yet."))
    }
}

// shell.openExternal {url} -> {}
fn open_external(params: &Value) -> Result<Value, RpcError> {
    let url = params
        .get("url")
        .and_then(Value::as_str)
        .ok_or_else(|| RpcError::invalid_params("A link is required."))?;

    // Re-validate the scheme in Rust — don't trust the core's check (§8, defense in depth).
    if !is_http_url(url) {
        return Err(RpcError::app("Addison can only open web links that start with http or https."));
    }
    open::that(url).map_err(|_| RpcError::app("Addison couldn't open that link."))?;
    Ok(json!({}))
}

// shell.readClipboard {} -> {text}
fn read_clipboard() -> Result<Value, RpcError> {
    let mut clipboard =
        arboard::Clipboard::new().map_err(|_| RpcError::app("Addison couldn't read the clipboard."))?;
    // No text on the clipboard is a valid empty result, not an error.
    let text = clipboard.get_text().unwrap_or_default();
    Ok(json!({ "text": text }))
}

/// Run a blocking native dialog on the main/UI thread (required on macOS/Windows/
/// Linux for native pickers) and await its result from async land.
async fn on_main<T, F>(app: &AppHandle, f: F) -> Result<T, RpcError>
where
    F: FnOnce() -> T + Send + 'static,
    T: Send + 'static,
{
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.run_on_main_thread(move || {
        let _ = tx.send(f());
    })
    .map_err(|_| RpcError::app("Addison couldn't open a system dialog just now."))?;
    rx.await
        .map_err(|_| RpcError::app("Addison couldn't open a system dialog just now."))
}

/// http/https only — matched on the URL's scheme, case-insensitively.
fn is_http_url(url: &str) -> bool {
    match url.split_once("://") {
        Some((scheme, _)) => {
            let s = scheme.to_ascii_lowercase();
            s == "http" || s == "https"
        }
        None => false,
    }
}

/// Common raster image extensions — these get base64 + kind "image"; everything
/// else is attempted as UTF-8 text by the caller.
fn is_image_path(path: &Path) -> bool {
    match path.extension().and_then(|e| e.to_str()) {
        Some(ext) => matches!(
            ext.to_ascii_lowercase().as_str(),
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "ico" | "tiff" | "tif"
        ),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_http_and_https_pass_the_scheme_check() {
        assert!(is_http_url("http://example.com"));
        assert!(is_http_url("https://example.com/path?q=1"));
        assert!(is_http_url("HTTPS://EXAMPLE.COM"));
        // Anything that could reach a local handler or run code must be refused.
        assert!(!is_http_url("file:///etc/passwd"));
        assert!(!is_http_url("javascript:alert(1)"));
        assert!(!is_http_url("ftp://example.com"));
        assert!(!is_http_url("mailto:x@example.com"));
        assert!(!is_http_url("example.com"));
        assert!(!is_http_url(""));
    }

    #[test]
    fn image_extensions_are_detected_case_insensitively() {
        assert!(is_image_path(Path::new("/tmp/a.png")));
        assert!(is_image_path(Path::new("/tmp/a.JPG")));
        assert!(is_image_path(Path::new("photo.jpeg")));
        assert!(!is_image_path(Path::new("/tmp/notes.txt")));
        assert!(!is_image_path(Path::new("/tmp/data.json")));
        assert!(!is_image_path(Path::new("/tmp/noext")));
    }
}
