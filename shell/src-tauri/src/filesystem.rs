// Native file picker + scoped file handles — engineering-spec §1.3, §7.4.1, design-doc §9.
//
// SECURITY PROPERTY: the Agent Core never receives a raw path it can wander with.
// It gets an opaque handle to whatever the OS-native picker returned, so it
// structurally cannot read/write outside the user's live selection. This module
// is the OS half of the ShellBridge contract (agent_core/tools/base.py); the core
// half calls these methods over stdio. Every effect here is user-initiated through
// a native dialog or scoped to a handle/path the shell itself minted this session.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
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
    /// Paths the shell WROTE this session via `shell.writeWorkspaceFile` (the OPEN
    /// coding harness, step 5). `shell.restoreWorkspaceFile` (write_project_file's
    /// undo) will only put back or delete a path in this set — so undo can never
    /// write or delete an arbitrary path, and it still works if the workspace's trust
    /// was revoked between the write and the undo (the ledger is session, not trust).
    workspace_written: Mutex<HashSet<PathBuf>>,
}

/// Prior text content larger than this refuses the edit rather than bloating the
/// core's `action_snapshots.undo_payload` (step 5, R5). Matches the intent of the
/// core-side bound; the value lives HERE because the shell is where the bytes are.
const UNDO_SIZE_BOUND: usize = 256 * 1024;

/// Worded once, because it is now raised from two places: before the bytes are
/// read (from the file's size) and again after (metadata is a claim about a
/// moment). The person must not be able to tell which one refused.
const TOO_BIG_TO_EDIT: &str = "That file is too big for Addison to edit while keeping an undo.";

/// A file larger than this refuses the READ (`shell.readWorkspaceFile`, i.e. the
/// shipped `read_project_file` tool) rather than crossing the bridge.
///
/// ITS OWN CONSTANT, not a reuse of `UNDO_SIZE_BOUND`, because the two answer
/// different questions: that one asks what can round-trip as an undo payload in
/// the core's database, this one asks what may cross a LINE-DELIMITED stdio
/// channel and land whole in a single model turn. Reusing it would mean a later
/// change to how undo is stored silently changed what the harness may read.
///
/// They agree on 256 KiB today for two reasons that happen to coincide. Every
/// `shell.*` handler is awaited INLINE on the core's stdout pump
/// (`agent_process.rs`), so one oversized read stalls every frame in the app
/// until it finishes — the same failure `dispatch_off_loop` exists to avoid for
/// `run_command`. And 256 KiB of source is already tens of thousands of tokens:
/// past that a file is a bundle, a lockfile or a log, and handing it whole to a
/// turn is never what was wanted.
///
/// A REFUSAL, never truncation. A harness that reads half a file and then edits
/// from it is worse than one that read nothing and said so.
const READ_SIZE_BOUND: u64 = 256 * 1024;

/// A file the PERSON picked in the native dialog, larger than this, refuses the
/// read (`shell.readScopedFile`, i.e. the shipped `read_file` tool) rather than
/// crossing the bridge.
///
/// ITS OWN CONSTANT, and deliberately FOUR TIMES `READ_SIZE_BOUND`, because the two
/// answer different questions. That one bounds what a coding harness may swallow
/// from a path a MODEL named; here the person chose this exact file in an OS dialog
/// and no model can name it — so the ceiling is not standing between a model and a
/// file, and what a person picks is often a picture. A screenshot or a photo is
/// legitimately larger than any source file, and base64 adds a third on top before
/// it crosses.
///
/// NOT LARGER STILL, for the reason a ceiling exists here at all. The bytes are
/// serialized onto ONE line of a line-delimited stdio channel by a handler awaited
/// INLINE on the core's stdout pump (`agent_process.rs`), so a 2 GB file picked by
/// accident stalls every frame in the app while it loads — the wedge is mechanical
/// and does not care who chose the file. And v1 has NO image-block path: the shell's
/// `{content, kind}` is JSON-serialized into a `tool_result` STRING
/// (`orchestrator._result_as_text`, `anthropic_provider._translate_history`), so a
/// picked image is charged to the turn as base64 TEXT. 1 MiB is already ~1.4 MB of
/// characters and several hundred thousand tokens: the outer edge of the largest
/// context Addison can route to, and far past a local or free-tier one. A ceiling
/// above this would only buy a slower way to be told the turn is too big.
///
/// ONE bound for text and pictures alike, judged BEFORE the extension is consulted.
/// `is_image_path` is a guess about content made from a filename, and a guess must
/// never be what decides whether a ceiling applies.
///
/// A REFUSAL, never truncation — half a picture is not a picture, and half a
/// document read as text is worse than none, because it reads as the whole one.
///
/// Kept a whole number of MB: the sentence names it in MB and derives it from here.
const PICKED_FILE_SIZE_BOUND: u64 = 1024 * 1024;

/// How much of one file the read-only VIEWER may show (`shell.readWorkspaceFileForView`,
/// the review surface's file pane — phase-3 plan Build §1).
///
/// A DERIVATION, deliberately, where `READ_SIZE_BOUND` above is deliberately not one:
/// this bound exists so that **any file Addison could have edited is a file the viewer
/// can show whole**. The write path refuses a prior larger than `UNDO_SIZE_BOUND`, so
/// tying the viewer to that same number is the property, not a coincidence — if the undo
/// bound ever moves, this must move with it or the surface starts truncating diffs of
/// edits it is showing a person in order to ask "shall I put this back?".
///
/// TRUNCATION, never a refusal — the OPPOSITE of every other ceiling in this file, and
/// the asymmetry is the point. The tool must refuse (a harness that reads half a file and
/// then rewrites it from what it saw destroys the tail); the viewer must truncate and say
/// so (a person looking at the first 256 KB of a lockfile has lost nothing, and a refusal
/// would leave them with an empty pane and no way to look).
const VIEW_SIZE_BOUND: usize = UNDO_SIZE_BOUND;

/// How many entries one directory listing may carry (`shell.listWorkspaceDirectory`).
///
/// CAPPED HERE, in the shell, for `UNDO_SIZE_BOUND`'s reason: this is where the bytes
/// are. A 200k-entry `node_modules` is a multi-megabyte SINGLE LINE on a line-delimited
/// channel, and `agent_process.rs` reads the core's side with an uncapped
/// `BufReader::lines()` — the same wedge every ceiling in this file exists to prevent,
/// arriving through a folder rather than a file.
const MAX_DIR_ENTRIES: usize = 500;

/// Worded once because two read paths raise it: the tool's read and the viewer's. The
/// person must not be able to tell which one refused, and neither of them can show a
/// file that is not text.
const NOT_TEXT_TO_READ: &str = "That file isn't a text file, so Addison can't read it here.";

/// Route a `shell.*` request from the core to its handler. Returns the JSON-RPC
/// `result` value, or an `RpcError` the core relays as plain language.
pub async fn handle(app: &AppHandle, method: &str, params: &Value) -> Result<Value, RpcError> {
    match method {
        "shell.saveNewFile" => save_new_file(app, params).await,
        "shell.deleteFile" => delete_file(app, params),
        "shell.restoreFile" => restore_file(app, params),
        "shell.pickFile" => pick_file(app).await,
        "shell.readScopedFile" => read_scoped_file(app, params),
        // OPEN-mode coding harness (step 5). Path-based, NOT picker-scoped: the core
        // confines which paths reach here (trusted-root check, D3); the shell
        // independently refuses Addison's own data directory (defence in depth) and
        // ledgers what it wrote so undo can only touch a path it created/overwrote.
        "shell.writeWorkspaceFile" => write_workspace_file(app, params),
        "shell.readWorkspaceFile" => read_workspace_file(params),
        "shell.restoreWorkspaceFile" => restore_workspace_file(app, params),
        "shell.pickDirectory" => pick_directory(app).await,
        // The review surface's READ paths (phase-3 plan Build §1). A person clicking a
        // folder is not the model acting, so these are reached from a `workspace.*` RPC
        // and never from a registry tool — the core confines which paths arrive
        // (mode gate, resolve once, trusted-root check), exactly as it does for the two
        // above, and the shell keeps its own independent floor underneath.
        "shell.listWorkspaceDirectory" => list_workspace_directory(params),
        "shell.readWorkspaceFileForView" => read_workspace_file_for_view(params),
        // NOTE: `shell.runCommand` is deliberately NOT routed here. It is OPEN-mode
        // command execution (step 5.5, items 1+2) and it lands in the shell for the
        // same reason the workspace file methods do — this is the process with OS
        // permissions, and therefore the only one that can put a sandbox around what
        // the model asked for. But every handler in this table is awaited INLINE in
        // the core's stdout pump, and a command can hold its task for the whole of
        // its budget; run_command therefore dispatches off the loop
        // (agent_process.rs, `dispatch_off_loop`). Reaching this table would answer
        // "unknown method" — loudly, which is the point: the alternative failure is
        // a silent minutes-long stall of every frame in the app.
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
    delete_created_path(app.state::<FileState>().inner(), path)
}

// The session-scope core of delete, factored out of the Tauri wrapper so the guard
// is testable without a live app (mirrors app_build.rs splitting shape out of the
// handler). Behaviour is unchanged: the wrapper only fetches the managed state.
fn delete_created_path(state: &FileState, path: PathBuf) -> Result<Value, RpcError> {
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
    restore_deleted_path(app.state::<FileState>().inner(), path, &content)
}

// The session-scope core of restore, factored out of the Tauri wrapper so the guard
// is testable without a live app. Behaviour is unchanged from the inline version.
fn restore_deleted_path(state: &FileState, path: PathBuf, content: &str) -> Result<Value, RpcError> {
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
        content,
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

// shell.pickDirectory {} -> {path}   (native folder picker, step 5)
//
// Relays the OS folder chooser for the "Trust a folder" flow. Returns a raw path
// (unlike pickFile's opaque handle) BECAUSE workspace trust is path-scoped by
// design (R7): the core canonicalizes it, floor-refuses the data dir, and confines
// every later edit to it — the trusted-root model is the OPEN harness's equivalent
// of §9's picker scoping.
async fn pick_directory(app: &AppHandle) -> Result<Value, RpcError> {
    let picked: Option<PathBuf> =
        on_main(app, move || rfd::FileDialog::new().pick_folder()).await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;
    Ok(json!({ "path": path.to_string_lossy() }))
}

// shell.readScopedFile {fileHandle} -> {content, kind}
fn read_scoped_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let handle = required_str(params, "fileHandle", "A file handle is required.")?;
    read_scoped_handle(app.state::<FileState>().inner(), handle)
}

// The handle-scope core of readScopedFile, factored out of the Tauri wrapper so the
// guard is testable without a live app. Behaviour is unchanged from the inline version.
fn read_scoped_handle(state: &FileState, handle: &str) -> Result<Value, RpcError> {
    // Resolve ONLY a handle we minted; a raw/unknown handle reads nothing.
    let path = lock(&state.handles)
        .get(handle)
        .cloned()
        .ok_or_else(|| RpcError::app("Addison can't read that file — please pick it again."))?;

    // Judged from the file's SIZE, before a byte is read, exactly as the workspace
    // paths do it: a refusal that first allocates the 2 GB it is refusing has
    // already stalled the app it was protecting. A size the OS won't give us is not
    // a refusal — fall through to the read, whose own error mapping speaks.
    if let Some(len) = size_on_disk(&path) {
        refuse_oversize_pick(len)?;
    }

    let bytes = std::fs::read(&path).map_err(|_| RpcError::app("Addison couldn't read that file."))?;
    // The file that GREW between those two calls, or one metadata could not answer
    // for at all. It has already cost this process the memory; it does not also get
    // to cross the bridge — and base64 would make it a third bigger on the way. The
    // same race backstop the workspace read carries, and like it, unreachable from a
    // test: stated plainly rather than pinned by a test that would test only itself.
    refuse_oversize_pick(bytes.len() as u64)?;

    if is_image_path(&path) {
        let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
        Ok(json!({ "content": encoded, "kind": "image" }))
    } else if let Ok(text) = String::from_utf8(bytes) {
        Ok(json!({ "content": text, "kind": "text" }))
    } else {
        Err(RpcError::app("Addison can't read that kind of file yet."))
    }
}

// shell.writeWorkspaceFile {path, content} -> {existed, prior}   (step 5)
//
// Create-or-OVERWRITE, capturing the prior state ATOMICALLY so undo is exact.
// Refuses (writing nothing) a binary or oversize existing file — so undo can always
// round-trip as text — and refuses Addison's own data directory.
fn write_workspace_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    let content = required_str(params, "content", "There's nothing to write.")?.to_string();
    write_workspace_path(app.state::<FileState>().inner(), path, &content)
}

// Session-scope core of the write, testable without a live Tauri app (mirrors the
// delete/restore split above).
fn write_workspace_path(state: &FileState, path: PathBuf, content: &str) -> Result<Value, RpcError> {
    refuse_addison_data_dir(&path)?;
    let (existed, prior) = capture_prior_text(&path)?;
    std::fs::write(&path, content).map_err(|_| RpcError::app("Addison couldn't save that file."))?;
    // Ledger the path so restore_workspace_file may target it — and ONLY it.
    lock(&state.workspace_written).insert(path);
    Ok(json!({ "existed": existed, "prior": prior }))
}

// (existed, prior-text). Refuses a binary or oversize existing file so the undo
// payload can always round-trip; a missing file is a clean create (false, null).
fn capture_prior_text(path: &Path) -> Result<(bool, Option<String>), RpcError> {
    // The same judgement as the length check below, made from the file's SIZE
    // first: `fs::read` on a 500 MB file allocates 500 MB in this process before
    // that check could refuse it, and this handler is awaited inline on the core's
    // stdout pump — so the refusal would arrive having already stalled the app it
    // was protecting. Same bound, same sentence, only earlier. The check below
    // stays, because metadata is a claim about a moment and a file can grow.
    if size_on_disk(path).is_some_and(|len| len > UNDO_SIZE_BOUND as u64) {
        return Err(RpcError::app(TOO_BIG_TO_EDIT));
    }
    match std::fs::read(path) {
        Ok(bytes) => {
            if bytes.len() > UNDO_SIZE_BOUND {
                return Err(RpcError::app(TOO_BIG_TO_EDIT));
            }
            match String::from_utf8(bytes) {
                Ok(text) => Ok((true, Some(text))),
                Err(_) => Err(RpcError::app(
                    "That file isn't a text file, so Addison won't change it.",
                )),
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok((false, None)),
        Err(_) => Err(RpcError::app("Addison couldn't read that file.")),
    }
}

// shell.readWorkspaceFile {path} -> {content}   (step 5)
fn read_workspace_file(params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    read_workspace_path(&path)
}

fn read_workspace_path(path: &Path) -> Result<Value, RpcError> {
    refuse_addison_data_dir(path)?;
    // Judged from the file's SIZE, before a byte is read: a refusal that first
    // allocates the 500 MB it is refusing has already done the damage it exists to
    // prevent. A size the OS won't give us is not a refusal — fall through to the
    // read, whose own error mapping says honestly what went wrong.
    if let Some(len) = size_on_disk(path) {
        refuse_oversize_read(len)?;
    }
    let bytes = std::fs::read(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => RpcError::app("That file isn't there."),
        _ => RpcError::app("Addison couldn't read that file."),
    })?;
    // A file that GREW between those two calls — or one metadata could not answer
    // for at all — is caught here. It has already cost this process the memory; it
    // does not also get to cross the bridge. The mirror of the write path's own
    // post-read length check, and like it, a race backstop no test can reach:
    // stated plainly rather than pinned by a test that would only be testing itself.
    refuse_oversize_read(bytes.len() as u64)?;
    match String::from_utf8(bytes) {
        Ok(text) => Ok(json!({ "content": text })),
        Err(_) => Err(RpcError::app(NOT_TEXT_TO_READ)),
    }
}

// shell.listWorkspaceDirectory {path} -> {entries: [{name, kind, size}], truncated}
//
// ONE LEVEL, never recursive: the surface expands a folder when a person opens it, and
// a depth knob is how a full repo walk gets requested by accident (phase-3 plan §1).
fn list_workspace_directory(params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A folder path is required.")?);
    list_workspace_path(&path)
}

fn list_workspace_path(path: &Path) -> Result<Value, RpcError> {
    refuse_addison_data_dir(path)?;
    let reader = std::fs::read_dir(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => RpcError::app("That folder isn't there."),
        _ => RpcError::app("Addison couldn't open that folder."),
    })?;

    // NAMES ONLY, and nothing is hidden. `.git` and `node_modules` are listed like
    // everything else: hiding them is a lie about what is on disk, and telling the truth
    // about what is on disk is this surface's only value. The UI renders them collapsed
    // and never auto-expands, which is a rendering decision and belongs there.
    let mut entries: Vec<(String, &'static str, u64)> = Vec::new();
    for entry in reader.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        // `symlink_metadata`, NEVER `metadata`. Following the link would render
        // `project/link -> ~/.ssh` as an ordinary expandable directory, and the person
        // would click it before anything refused. The link's own kind is the honest
        // answer, and `size` is the link's own size for the same reason — never the
        // target's, which this call deliberately never looks at.
        let (kind, size) = match std::fs::symlink_metadata(entry.path()) {
            Ok(meta) => (kind_of(&meta), meta.len()),
            // A name the OS will not describe is still a name that is there. Saying so
            // is more honest than dropping the row, and "other" is already the value for
            // everything that is not a file, a folder or a link.
            Err(_) => ("other", 0),
        };
        entries.push((name, kind, size));
    }

    // Sorted BEFORE the cap, so a truncated listing is "the first 500 by name" rather
    // than "500 the OS happened to hand back first" — the same folder must answer the
    // same way twice, or a person cannot tell a missing file from an unlucky one. The
    // full name list is collected to do it; that is bounded by the directory's own
    // entries and is a fraction of the serialized line the cap exists to prevent.
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    let truncated = entries.len() > MAX_DIR_ENTRIES;
    entries.truncate(MAX_DIR_ENTRIES);

    let listed: Vec<Value> = entries
        .into_iter()
        .map(|(name, kind, size)| json!({ "name": name, "kind": kind, "size": size }))
        .collect();
    Ok(json!({ "entries": listed, "truncated": truncated }))
}

/// What one directory entry IS, from metadata that did not follow the link.
/// `symlink` is a kind of its own rather than a flag on a file, because the whole
/// point is that the surface must not present it as the thing it points at.
fn kind_of(meta: &std::fs::Metadata) -> &'static str {
    let file_type = meta.file_type();
    if file_type.is_symlink() {
        "symlink"
    } else if file_type.is_dir() {
        "directory"
    } else if file_type.is_file() {
        "file"
    } else {
        "other"
    }
}

// shell.readWorkspaceFileForView {path} -> {content, bytes, truncated}
//
// ITS OWN METHOD, not a flag on `shell.readWorkspaceFile`, because the tool and the
// viewer want OPPOSITE semantics for an oversize file (see `VIEW_SIZE_BOUND`): the tool
// refuses, the viewer truncates and says so. One method with a mode switch would be one
// edit away from handing the model a truncated file.
fn read_workspace_file_for_view(params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    read_workspace_view(&path)
}

fn read_workspace_view(path: &Path) -> Result<Value, RpcError> {
    refuse_addison_data_dir(path)?;
    // The size is asked FIRST, exactly as every other read path here asks it — but this
    // one is not deciding a refusal with it, so it is not load-bearing for the wedge:
    // the read below is bounded by `take` no matter what metadata claims, which is the
    // stronger version of the same property (a file that grows between the two calls
    // cannot cost this process more than one extra byte). What metadata buys is the
    // HONEST `bytes`: how big the file actually is, which a truncated read cannot say
    // and which is the number the person needs to know how much is not shown.
    let on_disk = size_on_disk(path);
    let file = std::fs::File::open(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => RpcError::app("That file isn't there."),
        _ => RpcError::app("Addison couldn't read that file."),
    })?;
    let mut bytes: Vec<u8> = Vec::new();
    // BOUND + 1: the one extra byte is what tells a file exactly AT the bound apart from
    // one over it, without reading a byte more of the one that is over.
    file.take(VIEW_SIZE_BOUND as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| RpcError::app("Addison couldn't read that file."))?;

    let truncated = bytes.len() > VIEW_SIZE_BOUND;
    if truncated {
        bytes.truncate(char_boundary(&bytes, VIEW_SIZE_BOUND));
    }
    // What the file IS, not what came back — the two differ exactly when `truncated` is
    // true, which is when the difference is the thing worth saying. `None` means the OS
    // would not answer, and then the honest number is the one we hold.
    let total = on_disk.unwrap_or(bytes.len() as u64);
    match String::from_utf8(bytes) {
        Ok(text) => Ok(json!({ "content": text, "bytes": total, "truncated": truncated })),
        // Binary detection needs no new code and no new sentence: the decode already
        // fails, and the refusal a person reads is the one the tool's read gives them.
        Err(_) => Err(RpcError::app(NOT_TEXT_TO_READ)),
    }
}

/// The largest cut at or below `at` that does not fall INSIDE a character.
///
/// A byte cut through a multi-byte character turns a text file into a binary one:
/// `String::from_utf8` then fails on a perfectly ordinary source file, and the viewer
/// reports it as unreadable for no reason except that it was big. Walking back over
/// UTF-8 continuation bytes (`10xxxxxx`) costs at most three steps.
fn char_boundary(bytes: &[u8], at: usize) -> usize {
    let mut cut = at.min(bytes.len());
    while cut > 0 && cut < bytes.len() && (bytes[cut] & 0b1100_0000) == 0b1000_0000 {
        cut -= 1;
    }
    cut
}

/// The read ceiling, refused in plain language. The size is named in the sentence
/// and derived from the constant, so the two cannot drift apart.
fn refuse_oversize_read(len: u64) -> Result<(), RpcError> {
    if len > READ_SIZE_BOUND {
        return Err(RpcError::app(format!(
            "That file is too big for Addison to read — Addison can read files up to {} KB.",
            READ_SIZE_BOUND / 1024
        )));
    }
    Ok(())
}

/// The picked-file ceiling, refused in plain language for someone standing at a
/// file dialog: it names a size they can act on and the one thing to do next.
///
/// A SIBLING of `refuse_oversize_read`, not a reuse of it. Same shape, different
/// bound and different sentence — and a shared version would be a function whose
/// entire body is the two arguments its callers pass in, which is not reuse, only
/// indirection. `TOO_BIG_TO_EDIT` is worded once because the SAME refusal is raised
/// twice; these are two refusals.
fn refuse_oversize_pick(len: u64) -> Result<(), RpcError> {
    if len > PICKED_FILE_SIZE_BOUND {
        return Err(RpcError::app(format!(
            "That file is too big for Addison to open — please pick one that's {} MB or smaller.",
            PICKED_FILE_SIZE_BOUND / (1024 * 1024)
        )));
    }
    Ok(())
}

/// What the OS says a file's size is, or `None` when it won't say — a path that
/// isn't there, or one metadata is refused for. Follows symlinks, exactly as the
/// `fs::read` that follows it does, so the size measured is the size that would be
/// loaded. Callers treat `None` as "cannot judge yet", never as "small enough".
fn size_on_disk(path: &Path) -> Option<u64> {
    std::fs::metadata(path).ok().map(|m| m.len())
}

// shell.restoreWorkspaceFile {path, content?|delete} -> {}   (step 5, write undo)
//
// Only ever touches a path THIS session's writes ledgered — the mirror of
// delete/restore's allowlists, so undo structurally cannot write or delete anywhere
// new. Restores prior text, or deletes a file the write created (`delete: true`).
fn restore_workspace_file(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    restore_workspace_path(app.state::<FileState>().inner(), path, params)
}

fn restore_workspace_path(
    state: &FileState,
    path: PathBuf,
    params: &Value,
) -> Result<Value, RpcError> {
    {
        let written = lock(&state.workspace_written);
        if !written.contains(&path) {
            return Err(RpcError::app("Addison can only undo a file change it made."));
        }
    }
    if params.get("delete").and_then(Value::as_bool).unwrap_or(false) {
        // Undo of a created file: remove it. A file already gone is a no-op success —
        // the point is that it is not there after undo.
        match std::fs::remove_file(&path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(RpcError::app("Addison couldn't undo that file change.")),
        }
    } else {
        let content = required_str(params, "content", "There's nothing to put back.")?;
        std::fs::write(&path, content)
            .map_err(|_| RpcError::app("Addison couldn't undo that file change."))?;
    }
    Ok(json!({}))
}

/// Addison's own data directories: the live store's parent (ADDISON_DB_PATH's parent
/// if set) and `~/.addison` — plus, in a packaged install, the app BUNDLE itself
/// (see `addison_app_bundle`). The core already refuses the data dirs
/// (policy.workspace_trust_allows); this is the shell's independent floor (§6.6,
/// defence in depth), so the coding harness can never write or read Addison's
/// memory even if the core's check were bypassed.
///
/// Named for what it was when it held two entries. It is now "the places the
/// harness may not touch", data and code alike, and every caller wants all of
/// them — the seatbelt profile and `refuse_addison_data_dir` both.
/// The running app's own BUNDLE, when there is one — `/Applications/Addison.app`
/// in a packaged install, `None` in a dev build.
///
/// THE FLOOR PROTECTED ADDISON'S DATA AND NOT ADDISON'S CODE, which has been the
/// sharper of the two edges since step 5.5 closed the data side: a packaged
/// install puts `policy.py` and the gate inside a bundle the harness could
/// rewrite card-free, and rewriting the rules is a more complete bypass than
/// deleting the snapshots ever was.
///
/// **`None` in dev, deliberately.** The dev binary lives at
/// `…/shell/src-tauri/target/debug/addison`, so there is no bundle to deny —
/// and the enclosing repo is exactly what the coding harness is FOR when the
/// developer working on Addison is the user. Denying it would break the harness's
/// most legitimate use to protect a threat that only exists once the code ships
/// read-only. The bundle test is therefore structural (`.app/Contents/MacOS/…`),
/// never a guess from the binary's name.
pub fn addison_app_bundle() -> Option<PathBuf> {
    bundle_root_of(&std::env::current_exe().ok()?)
}

/// The bundle containing `exe`, or None. Split out so both answers are testable:
/// a unit test cannot relocate `current_exe`, and the packaged case is the one
/// that will never be exercised on a developer's machine.
fn bundle_root_of(exe: &Path) -> Option<PathBuf> {
    // …/Addison.app/Contents/MacOS/addison -> …/Addison.app
    let macos = exe.parent()?;
    let contents = macos.parent()?;
    let bundle = contents.parent()?;
    let shaped = macos.file_name()? == "MacOS"
        && contents.file_name()? == "Contents"
        && bundle.extension()? == "app";
    shaped.then(|| bundle.to_path_buf())
}

pub fn addison_data_dirs() -> Vec<PathBuf> {
    data_dirs_with_bundle(addison_app_bundle())
}

/// The protected set, given whichever bundle the caller found. Takes the bundle
/// rather than looking it up so the packaged case is reachable from a test: on a
/// developer's machine `addison_app_bundle()` is always None, so a test that
/// called `addison_data_dirs()` could only ever measure the empty half — which is
/// exactly how the first version of this passed while contributing nothing.
fn data_dirs_with_bundle(bundle: Option<PathBuf>) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    // The app's own code, when it is a shipped bundle. First in the list so the
    // deny is emitted for it exactly like every data dir — one mechanism, not a
    // second one bolted on beside it.
    if let Some(bundle) = bundle {
        dirs.push(bundle);
    }
    if let Ok(env) = std::env::var("ADDISON_DB_PATH") {
        if let Some(parent) = PathBuf::from(&env).parent() {
            if !parent.as_os_str().is_empty() {
                dirs.push(parent.to_path_buf());
            }
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if !home.is_empty() {
            dirs.push(PathBuf::from(home).join(".addison"));
        }
    }
    dirs
}

fn refuse_addison_data_dir(path: &Path) -> Result<(), RpcError> {
    let refused = || {
        Err(RpcError::app(
            "That location holds Addison's own memory, so Addison won't touch it there.",
        ))
    };
    let candidate = canonical_lossy(path);
    // A DANGLING symlink resolves to nothing, so canonicalization stops at the link
    // itself and the containment test judges the link's own harmless location. But
    // `std::fs::write` FOLLOWS the link and creates the file at its target — so a
    // link inside a trusted project, pointing at a not-yet-existing file under
    // Addison's data dir, planted a file in the G3 sidecar directory while this
    // check said yes. Read the link's own target and judge that too.
    let link_target = std::fs::read_link(path)
        .ok()
        .map(|target| {
            if target.is_absolute() {
                target
            } else {
                path.parent().unwrap_or(Path::new("")).join(target)
            }
        })
        .map(|resolved| canonical_lossy(&resolved));
    for dir in addison_data_dirs() {
        let protected = canonical_lossy(&dir);
        // Refuse a path that IS, sits inside, or contains a protected directory.
        if candidate.starts_with(&protected) || protected.starts_with(&candidate) {
            return refused();
        }
        if let Some(target) = &link_target {
            if target.starts_with(&protected) || protected.starts_with(target) {
                return refused();
            }
        }
    }
    Ok(())
}

/// Best-effort canonicalization for containment checks. `canonicalize` needs the
/// path to exist; a path about to be created does not, so walk UP to the nearest
/// ancestor that does exist, canonicalize that, and re-attach the rest.
///
/// Walking up matters, not just checking the immediate parent: when any
/// intermediate component is missing, the old one-level fallback left the candidate
/// un-canonicalized while the protected dir WAS canonicalized, so `starts_with`
/// compared `/var/…` against `/private/var/…` and found no containment. On macOS —
/// where `/tmp` and `/var` are themselves symlinks — that is not a corner case.
///
/// On macOS this also folds the case of existing components onto their real on-disk
/// spelling.
///
/// `pub(crate)` for exec.rs: the seatbelt profile decides containment against the
/// SAME protected dirs this floor does, and two resolvers would eventually disagree
/// about what a path is — which is precisely the class of bug the walk-up above was
/// written to fix.
pub(crate) fn canonical_lossy(path: &Path) -> PathBuf {
    if let Ok(c) = std::fs::canonicalize(path) {
        return c;
    }
    let mut suffix: Vec<std::ffi::OsString> = Vec::new();
    let mut cursor = path;
    while let Some(parent) = cursor.parent() {
        let name = match cursor.file_name() {
            Some(n) => n.to_os_string(),
            None => break,
        };
        suffix.push(name);
        if let Ok(c) = std::fs::canonicalize(parent) {
            let mut out = c;
            for part in suffix.iter().rev() {
                out.push(part);
            }
            return out;
        }
        cursor = parent;
    }
    path.to_path_buf()
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
    fn a_packaged_install_protects_addisons_own_code_and_a_dev_build_does_not() {
        // THE FLOOR PROTECTED ADDISON'S DATA, NOT ADDISON'S CODE — the sharper of
        // the two edges since 5.5 closed the data side, because rewriting
        // `policy.py` inside a shipped bundle is a more complete bypass than
        // deleting the snapshots ever was.
        assert_eq!(
            bundle_root_of(Path::new("/Applications/Addison.app/Contents/MacOS/addison")),
            Some(PathBuf::from("/Applications/Addison.app")),
            "a packaged install must contribute its bundle to the protected set"
        );

        // ...and the OTHER half, which is not a technicality: the dev binary sits
        // in the repo, and that repo is exactly what the coding harness is FOR
        // when the person using it is the developer working on Addison. A rule
        // that denied it would break the harness's most legitimate use to protect
        // a threat that only exists once the code ships read-only.
        for dev in [
            "/Users/x/Addison/shell/src-tauri/target/debug/addison",
            "/Users/x/Addison/target/release/addison",
            "/tmp/addison",
        ] {
            assert_eq!(bundle_root_of(Path::new(dev)), None, "{dev}");
        }

        // Shape, never the name: a binary called `addison` outside a bundle is not
        // a bundle, and one called anything else inside a real bundle is.
        assert_eq!(
            bundle_root_of(Path::new("/Applications/Whatever.app/Contents/MacOS/helper")),
            Some(PathBuf::from("/Applications/Whatever.app")),
        );
        assert_eq!(bundle_root_of(Path::new("/x/notabundle/Contents/MacOS/addison")), None);
    }

    #[test]
    fn the_protected_set_carries_the_bundle_it_is_given() {
        // `addison_data_dirs` is what the seatbelt profile is built from, so a
        // bundle the profile never hears about is a bundle nothing denies.
        let bundle = PathBuf::from("/Applications/Addison.app");
        assert!(
            data_dirs_with_bundle(Some(bundle.clone())).contains(&bundle),
            "the running bundle must reach the protected set"
        );
        assert!(
            !data_dirs_with_bundle(None).iter().any(|d| d == &bundle),
            "and must not appear from nowhere when there is no bundle"
        );
    }

    #[test]
    fn the_protected_set_actually_consults_the_running_bundle() {
        // THE WIRING. Splitting the lookup out for testability moved the part that
        // matters to its caller — docs/HANDOFF.md trap 3, which has now bitten
        // this repo three times, once in this very change: the first version of
        // the test above called `addison_data_dirs()` and matched on whatever it
        // found, so deleting the bundle line entirely left it green, because a
        // test binary is never in a bundle.
        //
        // Nothing on a developer's machine can make `current_exe` report a bundle,
        // so the last link is pinned at the source, the same way the IPC pump's
        // is. Coarse on purpose: it asserts the call exists, which is the property
        // no runtime assertion here can reach.
        let source = include_str!("filesystem.rs");
        let start = source
            .find("pub fn addison_data_dirs")
            .expect("addison_data_dirs must exist");
        let body = &source[start..];
        let end = body.find("\n}\n").expect("addison_data_dirs must be a closed function");
        assert!(
            body[..end].contains("addison_app_bundle()"),
            "addison_data_dirs must consult addison_app_bundle — otherwise a packaged \
             install ships with its own code writable by the harness:\n{}",
            &body[..end]
        );
    }

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

    // --- Session-scope guards on the core's file-effect surface. These drive the
    // real guard logic against a plain FileState (no Tauri app), so inverting a
    // guard turns the matching test red.

    #[test]
    fn delete_refuses_a_path_it_did_not_create() {
        // The core supplies deleteFile's path directly; the ONLY thing standing between
        // it and an arbitrary file is the `created` allowlist. Prove that a real file
        // NOT in the set is refused AND left on disk — inverting `!created.contains`
        // would delete it here.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "not addison's to delete").expect("seed file");

        let err = delete_created_path(&state, path.clone()).unwrap_err();
        assert_eq!(err.message, "Addison can only remove a file it just created.");
        assert!(path.exists(), "an unlisted path must never be removed");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn delete_removes_a_created_file_and_marks_it_restorable() {
        // The happy path: a session-created file IS removed, and its path graduates
        // created -> deleted so restore can re-create it exactly once. Pins the guard
        // isn't simply always-refuse, and pins the `deleted.insert` bookkeeping.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "made this session").expect("seed file");
        lock(&state.created).insert(path.clone());

        delete_created_path(&state, path.clone()).unwrap();
        assert!(!path.exists(), "a created file should be removed");
        assert!(!lock(&state.created).contains(&path));
        assert!(lock(&state.deleted).contains(&path), "path must become restorable");
    }

    #[test]
    fn restore_refuses_a_path_it_did_not_remove() {
        // Restore's mirror guard: it may only re-create a path THIS session removed
        // (in `deleted`). A path that was never deleted must be refused and no file
        // written — inverting `!deleted.contains` would write an arbitrary path.
        let state = FileState::default();
        let path = temp_path();

        let err = restore_deleted_path(&state, path.clone(), "smuggled content").unwrap_err();
        assert_eq!(err.message, "Addison can only put back a file it just removed.");
        assert!(!path.exists(), "restore must not write a path it never removed");
    }

    #[test]
    fn restore_recreates_a_removed_file_and_clears_it_from_deleted() {
        // The happy path: a path in `deleted` is re-created with its content and moves
        // deleted -> created (so redo is one-shot). Pins the guard isn't always-refuse.
        let state = FileState::default();
        let path = temp_path();
        lock(&state.deleted).insert(path.clone());

        restore_deleted_path(&state, path.clone(), "put back").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "put back");
        assert!(!lock(&state.deleted).contains(&path));
        assert!(lock(&state.created).contains(&path));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn read_scoped_refuses_an_unminted_handle() {
        // The core only ever holds an opaque handle; the path stays in the shell. Prove
        // an unknown handle reads nothing — even when the handle string is itself a
        // real, readable path. Treating the handle as a path (or dropping the map
        // lookup) would leak that file's bytes to the core.
        let state = FileState::default();
        let secret = temp_path();
        std::fs::write(&secret, "should stay unreadable").expect("seed file");

        let err = read_scoped_handle(&state, &secret.to_string_lossy()).unwrap_err();
        assert_eq!(err.message, "Addison can't read that file — please pick it again.");

        let _ = std::fs::remove_file(&secret);
    }

    #[test]
    fn read_scoped_reads_a_file_behind_a_minted_handle() {
        // The happy path: a handle the shell minted resolves to its picked file and
        // returns the content as text. Pins that resolution works, so the refuse test
        // above can't pass under an always-error mutation.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "picked by the user").expect("seed file");
        let handle = uuid::Uuid::new_v4().to_string();
        lock(&state.handles).insert(handle.clone(), path.clone());

        let result = read_scoped_handle(&state, &handle).unwrap();
        assert_eq!(result.get("kind").and_then(Value::as_str), Some("text"));
        assert_eq!(result.get("content").and_then(Value::as_str), Some("picked by the user"));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn read_scoped_reads_a_picked_image_exactly_at_the_size_ceiling() {
        // The ceiling is inclusive, and this is the half that keeps the refusal
        // honest: without it the test below would pass under an always-refuse. It
        // drives the IMAGE branch on purpose — that is the one the picker bound was
        // raised above `READ_SIZE_BOUND` for, and the one base64 then inflates.
        let state = FileState::default();
        let path = temp_path().with_extension("png");
        let at_ceiling = vec![0xFFu8; PICKED_FILE_SIZE_BOUND as usize];
        std::fs::write(&path, &at_ceiling).expect("seed a picked file at the ceiling");
        let handle = uuid::Uuid::new_v4().to_string();
        lock(&state.handles).insert(handle.clone(), path.clone());

        let result = read_scoped_handle(&state, &handle).unwrap();
        assert_eq!(result.get("kind").and_then(Value::as_str), Some("image"));
        let encoded = result.get("content").and_then(Value::as_str).expect("base64 content");
        assert_eq!(
            base64::engine::general_purpose::STANDARD.decode(encoded).unwrap().len(),
            at_ceiling.len(),
            "a picked file at the ceiling must come back whole, never truncated"
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn read_scoped_refuses_a_picked_file_one_byte_over_the_size_ceiling() {
        // The picker is not a steering surface — the person named this file in a
        // native dialog and no model can — but the WEDGE is mechanical and does not
        // care who chose: the bytes go onto one line of a line-delimited channel,
        // loaded by a handler awaited inline on the core's stdout pump, so a file
        // picked by accident stalls every frame in the app. Delete the two
        // `refuse_oversize_pick` calls in `read_scoped_handle` and both of these
        // return Ok — the mutation this test exists to catch.
        //
        // BOTH BRANCHES, because the guard sits before the branch. A ceiling that
        // covered only text would leave the base64 path — the one that grows by a
        // third — wide open, which is precisely the wrong half to protect.
        let state = FileState::default();
        for ext in ["txt", "png"] {
            let path = temp_path().with_extension(ext);
            let over = vec![b'a'; PICKED_FILE_SIZE_BOUND as usize + 1];
            std::fs::write(&path, &over).expect("seed a picked file one byte over");
            let handle = uuid::Uuid::new_v4().to_string();
            lock(&state.handles).insert(handle.clone(), path.clone());

            let err = read_scoped_handle(&state, &handle).unwrap_err();
            assert_eq!(err.code, -32000, "{ext}");
            // A refusal, not a truncation, and worded for a person who is standing
            // at a file dialog: no byte counts, no "exceeds", a size they can act on
            // and the one thing to do next.
            assert_eq!(
                err.message,
                "That file is too big for Addison to open — please pick one that's 1 MB or smaller.",
                "{ext}"
            );
            assert_eq!(
                std::fs::metadata(&path).unwrap().len(),
                over.len() as u64,
                "a refused read must leave the file exactly as it was"
            );

            let _ = std::fs::remove_file(&path);
        }
    }

    /// Serializes every test that mutates the PROCESS-GLOBAL `ADDISON_DB_PATH`.
    /// cargo runs tests in parallel threads, so without this one test's `set_var`
    /// lands in the middle of another's assertion — which is exactly what happened
    /// when the two floor tests below were added: the suite went red while each
    /// test passed alone. A poisoned lock is fine to keep using here; the guard is
    /// ordering, not state.
    static DATA_DIR_ENV: Mutex<()> = Mutex::new(());

    // --- Workspace-trust file surface (step 5). The core confines WHICH paths reach
    // these; the shell guards undo soundness (ledger) and independently refuses
    // Addison's own data dir. Each test drives the real session-scope core.

    #[test]
    fn write_workspace_creates_a_new_file_and_reports_no_prior() {
        // A brand-new file: existed=false, prior=null, and the path is ledgered so
        // its undo (a delete) is authorized. Content lands on disk.
        let state = FileState::default();
        let path = temp_path();

        let result = write_workspace_path(&state, path.clone(), "fresh").unwrap();
        assert_eq!(result.get("existed").and_then(Value::as_bool), Some(false));
        assert!(result.get("prior").map(Value::is_null).unwrap_or(false));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "fresh");
        assert!(lock(&state.workspace_written).contains(&path), "written path must be ledgered");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_overwrites_and_returns_prior_text() {
        // An overwrite: existed=true and the prior text comes back verbatim, so the
        // core can snapshot it for an exact undo. Inverting the prior capture would
        // return the wrong bytes and this fails.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "before").expect("seed");

        let result = write_workspace_path(&state, path.clone(), "after").unwrap();
        assert_eq!(result.get("existed").and_then(Value::as_bool), Some(true));
        assert_eq!(result.get("prior").and_then(Value::as_str), Some("before"));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "after");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_refuses_a_binary_file_and_leaves_it_unchanged() {
        // A binary existing file can't round-trip as an undo payload, so the write is
        // refused and the file is left exactly as it was — no half-applied overwrite.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, [0u8, 159, 146, 150]).expect("seed non-utf8");

        let err = write_workspace_path(&state, path.clone(), "text").unwrap_err();
        assert_eq!(err.message, "That file isn't a text file, so Addison won't change it.");
        assert_eq!(std::fs::read(&path).unwrap(), vec![0u8, 159, 146, 150], "must be untouched");
        assert!(!lock(&state.workspace_written).contains(&path), "a refused write is not ledgered");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_refuses_an_oversize_prior_and_leaves_it_unchanged() {
        // A prior file over the undo bound is refused rather than bloating the undo
        // payload; the original stays on disk.
        let state = FileState::default();
        let path = temp_path();
        let big = "a".repeat(UNDO_SIZE_BOUND + 1);
        std::fs::write(&path, &big).expect("seed big");

        let err = write_workspace_path(&state, path.clone(), "small").unwrap_err();
        assert!(err.message.contains("too big"));
        assert_eq!(std::fs::read_to_string(&path).unwrap().len(), big.len(), "must be untouched");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_refuses_the_addison_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // Defence in depth: even if the core's floor were bypassed, the shell refuses
        // a write under ~/.addison. Drive it via ADDISON_DB_PATH so the test never
        // touches the real home directory.
        let state = FileState::default();
        let data_dir = std::env::temp_dir().join(format!("addison-dd-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(data_dir.join("snapshots")).expect("seed data dir");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", data_dir.join("addison.sqlite3"));

        let target = data_dir.join("snapshots").join("stolen.json");
        let err = write_workspace_path(&state, target.clone(), "x").unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!target.exists(), "nothing may be written into the data dir");

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    #[test]
    fn read_workspace_refuses_the_addison_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // The read side gets the same independent floor.
        let data_dir = std::env::temp_dir().join(format!("addison-dd-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&data_dir).expect("seed data dir");
        let secret = data_dir.join("addison.sqlite3");
        std::fs::write(&secret, "secret db bytes").expect("seed db");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", &secret);

        let err = read_workspace_path(&secret).unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    #[test]
    fn read_workspace_reads_a_file_exactly_at_the_size_ceiling() {
        // The ceiling is inclusive, and this is the half that keeps the refusal
        // honest: a bound that also refused the largest legitimate file would be
        // indistinguishable from one set too low, and every test below would pass
        // under an always-refuse mutation.
        let path = temp_path();
        let at_ceiling = "a".repeat(READ_SIZE_BOUND as usize);
        std::fs::write(&path, &at_ceiling).expect("seed a file at the ceiling");

        let result = read_workspace_path(&path).unwrap();
        assert_eq!(
            result.get("content").and_then(Value::as_str).map(str::len),
            Some(at_ceiling.len()),
            "a file at the ceiling must come back whole, never truncated"
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn read_workspace_refuses_a_file_one_byte_over_the_size_ceiling() {
        // `read_project_file` is SHIPPED and its content lands whole in a model
        // turn after crossing a line-delimited stdio channel, so an unbounded read
        // was a 500 MB file away from wedging the bridge. Delete the size check in
        // `read_workspace_path` and this returns Ok — the mutation that this test
        // exists to catch.
        let path = temp_path();
        let over = "a".repeat(READ_SIZE_BOUND as usize + 1);
        std::fs::write(&path, &over).expect("seed a file one byte over");

        let err = read_workspace_path(&path).unwrap_err();
        assert_eq!(err.code, -32000);
        // A refusal, not a truncation, and worded for a person: no byte counts, no
        // "exceeds", and it names the size they can act on.
        assert_eq!(
            err.message,
            "That file is too big for Addison to read — Addison can read files up to 256 KB."
        );
        assert_eq!(
            std::fs::read_to_string(&path).unwrap().len(),
            over.len(),
            "a refused read must leave the file exactly as it was"
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn every_size_ceiling_is_judged_before_any_bytes_are_read() {
        // THE POINT OF A CEILING IS THAT THE REFUSAL COSTS NOTHING. A check made
        // after `fs::read` has already allocated the 500 MB it is about to refuse,
        // and every `shell.*` handler is awaited inline on the core's stdout pump
        // (agent_process.rs) — so a refusal decided too late still stalls every
        // frame in the app, which is the harm, not the error message.
        //
        // No runtime assertion here can see the difference: both orders return the
        // same error. So the ORDER is pinned at the source, the same way the bundle
        // wiring above is — docs/HANDOFF.md trap 3.
        //
        // Was `both_…` when there were two paths with a ceiling. `read_scoped_handle`
        // is the third and it is one list, not a second mechanism beside it: a path
        // that grows a bound later must join this loop, and a name that counted the
        // members would quietly stop being a place to add one.
        //
        // The VIEWER's read (`read_workspace_view`) is the fourth, and it carries its
        // own marker because it never calls `fs::read` at all: it opens the file and
        // `take`s a bounded number of bytes, which is the STRONGER version of the same
        // property — metadata can be stale, a `take` cannot. The pin still asks the same
        // question of it (is the size consulted before the file is opened?), because the
        // answer it gets from metadata is what `bytes` reports, and a `bytes` computed
        // after a truncated read would quietly describe the truncation instead of the file.
        let source = include_str!("filesystem.rs");
        for (name, opens_with) in [
            ("fn read_workspace_path", "std::fs::read("),
            ("fn capture_prior_text", "std::fs::read("),
            ("fn read_scoped_handle", "std::fs::read("),
            ("fn read_workspace_view", "std::fs::File::open("),
        ] {
            let start = source.find(name).unwrap_or_else(|| panic!("{name} must exist"));
            let rest = &source[start..];
            let end = rest.find("\n}\n").unwrap_or_else(|| panic!("{name} must be closed"));
            let body = &rest[..end];

            let sized = body
                .find("size_on_disk")
                .unwrap_or_else(|| panic!("{name} must ask the file's size:\n{body}"));
            let read = body
                .find(opens_with)
                .unwrap_or_else(|| panic!("{name} must read the file ({opens_with}):\n{body}"));
            assert!(
                sized < read,
                "{name} must judge the size BEFORE reading the bytes:\n{body}"
            );
        }
    }

    // --- The review surface's read paths (phase-3 plan Build §1). The core confines
    // WHICH paths reach these (mode gate, resolve once, trusted-root check); these tests
    // drive the shell's own half against a real temp directory.

    fn temp_dir_path() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("addison-view-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).expect("seed temp dir");
        dir
    }

    #[test]
    fn listing_a_directory_caps_at_five_hundred_entries_and_says_it_truncated() {
        // A 200k-entry `node_modules` listing is a multi-megabyte SINGLE LINE on a
        // line-delimited channel that `agent_process.rs` reads with an uncapped
        // `BufReader::lines()`. Delete the `truncate` and this returns 501 rows — the
        // mutation this exists to catch — and `truncated: false` would be the lie
        // underneath it: a person cannot tell a missing file from an unlucky one unless
        // the payload says some are missing.
        let dir = temp_dir_path();
        for i in 0..(MAX_DIR_ENTRIES + 1) {
            // Zero-padded so the byte ordering the handler sorts by is also the numeric
            // one — this test asserts WHICH 500 came back, not merely how many.
            std::fs::write(dir.join(format!("f{i:04}.txt")), "x").expect("seed entry");
        }

        let result = list_workspace_path(&dir).unwrap();
        let entries = result.get("entries").and_then(Value::as_array).expect("entries");
        assert_eq!(entries.len(), MAX_DIR_ENTRIES, "the cap is the cap");
        assert_eq!(result.get("truncated").and_then(Value::as_bool), Some(true));
        // Sorted before the cap: the same folder answers the same way twice.
        assert_eq!(entries[0].get("name").and_then(Value::as_str), Some("f0000.txt"));
        assert_eq!(
            entries[MAX_DIR_ENTRIES - 1].get("name").and_then(Value::as_str),
            Some("f0499.txt")
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn listing_a_directory_under_the_cap_is_not_marked_truncated() {
        // The half that keeps the flag honest: without it, `truncated: true` for every
        // listing would pass the test above, and the UI would tell every person that
        // every folder is incomplete.
        let dir = temp_dir_path();
        std::fs::write(dir.join("a.txt"), "x").expect("seed");

        let result = list_workspace_path(&dir).unwrap();
        assert_eq!(result.get("truncated").and_then(Value::as_bool), Some(false));
        assert_eq!(result.get("entries").and_then(Value::as_array).map(Vec::len), Some(1));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_symlink_is_listed_as_a_symlink_and_never_as_what_it_points_at() {
        // THE NEW EXPOSURE, and the whole reason `symlink_metadata` is named in the
        // plan. Swap it for `metadata` and `project/link -> somewhere-else` comes back
        // `kind: "directory"` — an expandable folder the person clicks before anything
        // refuses. The refusal is the core's follow-up check; this is what stops the
        // surface from inviting the click in the first place.
        //
        // Nothing is hidden either: a dotfile directory is listed like everything else.
        let dir = temp_dir_path();
        let target = dir.join(".git");
        std::fs::create_dir_all(&target).expect("seed target dir");
        std::fs::write(dir.join("plain.txt"), "hello").expect("seed file");
        std::os::unix::fs::symlink(&target, dir.join("link")).expect("plant the link");

        let result = list_workspace_path(&dir).unwrap();
        let entries = result.get("entries").and_then(Value::as_array).expect("entries");
        let kind_of_name = |name: &str| -> String {
            entries
                .iter()
                .find(|e| e.get("name").and_then(Value::as_str) == Some(name))
                .and_then(|e| e.get("kind").and_then(Value::as_str))
                .unwrap_or("missing")
                .to_string()
        };
        assert_eq!(kind_of_name("link"), "symlink", "a link must never render as its target");
        assert_eq!(kind_of_name(".git"), "directory", "`.git` is listed, never hidden");
        assert_eq!(kind_of_name("plain.txt"), "file");
        // The size is the file's own; a link's is its own too, never the target's.
        let plain = entries
            .iter()
            .find(|e| e.get("name").and_then(Value::as_str) == Some("plain.txt"))
            .expect("plain.txt");
        assert_eq!(plain.get("size").and_then(Value::as_u64), Some(5));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn listing_refuses_the_addison_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // The shell's independent floor, on the new read path too — the core already
        // refuses it, and that is exactly why this must not depend on the core.
        let data_dir = std::env::temp_dir().join(format!("addison-dd-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(data_dir.join("snapshots")).expect("seed data dir");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", data_dir.join("addison.sqlite3"));

        let err = list_workspace_path(&data_dir).unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    #[test]
    fn the_viewer_refuses_the_addison_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        let data_dir = std::env::temp_dir().join(format!("addison-dd-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&data_dir).expect("seed data dir");
        let secret = data_dir.join("addison.sqlite3");
        std::fs::write(&secret, "secret db bytes").expect("seed db");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", &secret);

        let err = read_workspace_view(&secret).unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    #[test]
    fn the_viewer_shows_a_file_at_the_bound_whole_and_reports_its_size() {
        // The half that keeps the truncation honest: a viewer that always truncated
        // would pass the test below. At exactly the bound nothing is cut, `truncated` is
        // false, and `bytes` is the file's real size.
        let path = temp_path();
        let at_bound = "a".repeat(VIEW_SIZE_BOUND);
        std::fs::write(&path, &at_bound).expect("seed a file at the bound");

        let result = read_workspace_view(&path).unwrap();
        assert_eq!(result.get("truncated").and_then(Value::as_bool), Some(false));
        assert_eq!(result.get("bytes").and_then(Value::as_u64), Some(VIEW_SIZE_BOUND as u64));
        assert_eq!(
            result.get("content").and_then(Value::as_str).map(str::len),
            Some(at_bound.len()),
            "a file at the bound must come back whole"
        );

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn the_viewer_truncates_on_a_char_boundary_and_never_through_a_character() {
        // TRUNCATE AND SAY SO — the opposite of the tool's refusal, and the plan says
        // why. The sharp edge is the CUT: a byte cut through a multi-byte character
        // makes `String::from_utf8` fail, so an ordinary source file with an accent in
        // it would be reported as "not a text file" purely because it was big. The seed
        // puts a two-byte `é` straddling the bound exactly; delete the `char_boundary`
        // walk-back and this returns that refusal instead of text.
        let path = temp_path();
        let mut content = "a".repeat(VIEW_SIZE_BOUND - 1);
        content.push('é'); // its first byte is the last byte inside the bound
        content.push_str(&"b".repeat(1024));
        let raw = content.as_bytes().to_vec();
        std::fs::write(&path, &raw).expect("seed a file straddling the bound");

        let result = read_workspace_view(&path).unwrap();
        assert_eq!(result.get("truncated").and_then(Value::as_bool), Some(true));
        // `bytes` is the FILE, not the excerpt — the number that tells a person how much
        // is not on screen. Computing it from the returned content would report the cut.
        assert_eq!(result.get("bytes").and_then(Value::as_u64), Some(raw.len() as u64));
        let text = result.get("content").and_then(Value::as_str).expect("text content");
        assert_eq!(
            text.len(),
            VIEW_SIZE_BOUND - 1,
            "the cut must step back off the character it would have split"
        );
        assert!(text.ends_with('a') && !text.contains('é'));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn the_viewer_refuses_a_file_that_is_not_text() {
        // Binary detection is the decode that already exists, and the sentence is the
        // one the tool's read already gives a person — worded once (NOT_TEXT_TO_READ),
        // because two spellings of one refusal is how they drift.
        let path = temp_path();
        std::fs::write(&path, [0u8, 159, 146, 150]).expect("seed non-utf8");

        let err = read_workspace_view(&path).unwrap_err();
        assert_eq!(err.message, "That file isn't a text file, so Addison can't read it here.");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn restore_workspace_refuses_a_path_it_did_not_write() {
        // The undo guard: restore may only touch a path THIS session wrote. A path
        // not in the ledger is refused and no file written — inverting the check
        // would let undo write an arbitrary path.
        let state = FileState::default();
        let path = temp_path();

        let params = json!({ "path": path.to_string_lossy(), "content": "smuggled" });
        let err = restore_workspace_path(&state, path.clone(), &params).unwrap_err();
        assert_eq!(err.message, "Addison can only undo a file change it made.");
        assert!(!path.exists(), "restore must not write an unledgered path");
    }

    #[test]
    fn restore_workspace_puts_back_prior_text_for_a_ledgered_path() {
        // The overwrite-undo happy path: a ledgered path is rewritten with the prior
        // text. Works regardless of trust state (the ledger is session, not trust).
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "changed").expect("seed");
        lock(&state.workspace_written).insert(path.clone());

        let params = json!({ "path": path.to_string_lossy(), "content": "original" });
        restore_workspace_path(&state, path.clone(), &params).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "original");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_refuses_a_dangling_symlink_into_the_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // The shell's floor claims to hold "even if the core's check were bypassed".
        // It did not: a symlink inside a project pointing at a not-yet-existing file
        // under the data dir canonicalized to the LINK's own harmless location, the
        // containment test passed, and `fs::write` then followed the link and planted
        // a file in the G3 sidecar directory. Revert the read_link branch in
        // refuse_addison_data_dir and this test writes into `snapshots/`.
        let state = FileState::default();
        let data_dir = std::env::temp_dir().join(format!("addison-dang-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(data_dir.join("snapshots")).expect("seed data dir");
        let project = std::env::temp_dir().join(format!("addison-proj-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&project).expect("seed project");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", data_dir.join("addison.sqlite3"));

        // The target does NOT exist yet — that is the whole point.
        let victim = data_dir.join("snapshots").join("planted.json");
        let link = project.join("innocent.txt");
        std::os::unix::fs::symlink(&victim, &link).expect("plant the dangling link");

        let err = write_workspace_path(&state, link.clone(), "PLANTED").unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!victim.exists(), "a dangling link must not plant a file in the data dir");

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn the_data_dir_floor_holds_when_an_intermediate_directory_is_missing() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // canonical_lossy only checked the IMMEDIATE parent, so a path with any
        // missing intermediate component stayed un-canonicalized while the protected
        // dir was canonicalized — comparing /var/... against /private/var/... and
        // finding no containment. On macOS, where /tmp and /var are themselves
        // symlinks, that is the ordinary case, not a corner one.
        let state = FileState::default();
        let data_dir = std::env::temp_dir().join(format!("addison-mid-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&data_dir).expect("seed data dir");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", data_dir.join("addison.sqlite3"));

        // `nosuchdir` does not exist, so neither the path nor its parent resolves.
        let target = data_dir.join("nosuchdir").join("x.json");
        let err = write_workspace_path(&state, target.clone(), "x").unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!target.exists());

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    #[test]
    fn restore_workspace_deletes_a_created_file_for_a_ledgered_path() {
        // The created-file-undo happy path: `delete: true` removes a ledgered path.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "created by the write").expect("seed");
        lock(&state.workspace_written).insert(path.clone());

        let params = json!({ "path": path.to_string_lossy(), "delete": true });
        restore_workspace_path(&state, path.clone(), &params).unwrap();
        assert!(!path.exists(), "an undone create must be removed");
    }
}
