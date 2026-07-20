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

use crate::ipc::{required_str, RpcError};

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
        // Which build of Addison this is — recorded on a permanent restore point
        // so a later restore can say honestly that it came from another version
        // (G4; app_build.rs). Reads no user data and touches no file.
        "shell.appBuildRef" => crate::app_build::app_build_ref(app),
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
    let filename = required_str(params, "filename", "A file name is required.")?.to_string();
    let content = required_str(params, "content", "There's nothing to save.")?.to_string();

    let picked: Option<PathBuf> =
        on_main(app, move || rfd::FileDialog::new().set_file_name(filename).save_file()).await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;

    create_new_and_write(
        &path,
        &content,
        "A file with that name is already there — please choose another name.",
        "Addison couldn't save that file.",
    )?;

    lock(&app.state::<FileState>().created).insert(path.clone());
    Ok(json!({ "path": path.to_string_lossy() }))
}

// shell.deleteFile {path} -> {}   (save_file's undo path)
fn delete_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);

    let state = app.state::<FileState>();
    {
        let created = lock(&state.created);
        if !created.contains(&path) {
            // Only ever remove what we made this session — never an arbitrary path.
            return Err(RpcError::app("Addison can only remove a file it just created."));
        }
    }
    std::fs::remove_file(&path).map_err(|_| RpcError::app("Addison couldn't remove that file."))?;
    lock(&state.created).remove(&path);
    // The path graduates to the restorable set: redo may re-create it, once.
    lock(&state.deleted).insert(path);
    Ok(json!({}))
}

// shell.restoreFile {path, content} -> {}   (save_file's redo path)
//
// Only re-creates a file that `shell.deleteFile` removed THIS SESSION — the
// mirror of delete's allowlist, so redo structurally cannot write anywhere new.
fn restore_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    let content = required_str(params, "content", "There's nothing to put back.")?.to_string();

    let state = app.state::<FileState>();
    {
        let deleted = lock(&state.deleted);
        if !deleted.contains(&path) {
            return Err(RpcError::app("Addison can only put back a file it just removed."));
        }
    }
    // create_new: if something ELSE now lives at that path, refuse rather than
    // overwrite — same §7.4.1 rule as saving.
    create_new_and_write(
        &path,
        &content,
        "A file with that name is already there — nothing was changed.",
        "Addison couldn't put that file back.",
    )?;

    lock(&state.deleted).remove(&path);
    lock(&state.created).insert(path);
    Ok(json!({}))
}

// shell.pickFile {} -> {fileHandle}   (opaque handle, never a raw path)
async fn pick_file(app: &AppHandle) -> Result<Value, RpcError> {
    let picked: Option<PathBuf> =
        on_main(app, move || rfd::FileDialog::new().pick_file()).await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;

    let handle = uuid::Uuid::new_v4().to_string();
    lock(&app.state::<FileState>().handles).insert(handle.clone(), path);
    Ok(json!({ "fileHandle": handle }))
}

// shell.readScopedFile {fileHandle} -> {content, kind}
fn read_scoped_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let handle = required_str(params, "fileHandle", "A file handle is required.")?;

    // Resolve ONLY a handle we minted; a raw/unknown handle reads nothing.
    let state = app.state::<FileState>();
    let path = lock(&state.handles)
        .get(handle)
        .cloned()
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
    let url = required_str(params, "url", "A link is required.")?;

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

/// Acquire a session-state lock, recovering the guard if a previous holder panicked.
/// These sets/maps only ever see whole insert/remove/contains/get operations, so a
/// poisoned lock carries no half-updated invariant — recovering is strictly safer
/// than letting a stray panic cascade into the stdio supervisor that answers the core.
fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Create `path` fresh and write `content`, never overwriting an existing file
/// (§7.4.1 — the anti-clobber rule that keeps save_file's undo trivial). If the
/// write fails after the file was created, the just-created file is rolled back so a
/// mid-write failure can't strand a partial orphan that the undo path won't touch.
/// `exists_msg`/`fail_msg` carry the caller's plain-language wording.
fn create_new_and_write(
    path: &Path,
    content: &str,
    exists_msg: &str,
    fail_msg: &str,
) -> Result<(), RpcError> {
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| match e.kind() {
            std::io::ErrorKind::AlreadyExists => RpcError::app(exists_msg),
            _ => RpcError::app(fail_msg),
        })?;
    if file.write_all(content.as_bytes()).is_err() {
        drop(file); // release the handle before unlinking (matters on Windows)
        let _ = std::fs::remove_file(path); // best-effort: leave no partial orphan
        return Err(RpcError::app(fail_msg));
    }
    Ok(())
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

    fn temp_path() -> PathBuf {
        std::env::temp_dir().join(format!("addison-fs-test-{}.txt", uuid::Uuid::new_v4()))
    }

    #[test]
    fn create_new_and_write_writes_a_fresh_file() {
        let path = temp_path();
        assert!(create_new_and_write(&path, "hello", "exists", "fail").is_ok());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "hello");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn create_new_and_write_refuses_to_overwrite_and_leaves_the_original() {
        let path = temp_path();
        std::fs::write(&path, "original").expect("seed file");
        // An existing file must be refused with the caller's exists message, never
        // clobbered — this is the anti-overwrite property save/restore both rely on.
        let err = create_new_and_write(&path, "new", "already there", "fail").unwrap_err();
        assert_eq!(err.code, -32000);
        assert_eq!(err.message, "already there");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "original");
        let _ = std::fs::remove_file(&path);
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
