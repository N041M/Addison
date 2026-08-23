// Native file picker + scoped file handles — engineering-spec §1.3, §7.4.1, design-doc §9.
//
// SECURITY PROPERTY: the Agent Core never receives a raw path it can wander with.
// It gets an opaque handle to whatever the OS-native picker returned, so it
// structurally cannot read/write outside the user's live selection. This module
// is the OS half of the ShellBridge contract (agent_core/tools/base.py); the core
// half calls these methods over stdio. Every effect here is user-initiated through
// a native dialog or scoped to a handle/path the shell itself minted this session.

use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use base64::Engine as _;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
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
/// and does not care who chose the file. And THIS path still charges a picture to
/// the turn as base64 TEXT: the shell's `{content, kind}` is JSON-serialized into a
/// `tool_result` STRING (`orchestrator._result_as_text`,
/// `anthropic_provider._translate_history`). 1 MiB is already ~1.4 MB of characters
/// and several hundred thousand tokens: the outer edge of the largest context
/// Addison can route to, and far past a local or free-tier one. A ceiling above
/// this would only buy a slower way to be told the turn is too big.
///
/// THERE IS AN IMAGE-BLOCK PATH NOW, AND IT IS NOT THIS ONE. `Message.images`
/// and the four adapters' block shapes landed with phase 1 of
/// [`docs/image-attach-plan.md`](../../../docs/image-attach-plan.md), and phase 2
/// gave them their own front door — `shell.pickImage` + `shell.readPickedImage`,
/// which decode, downscale and re-encode before anything crosses, and carry their
/// own two bounds (`PICKED_IMAGE_SIZE_BOUND` on the file, `ENCODED_IMAGE_SIZE_BOUND`
/// on what is sent). So this constant is the TEXT pick's bound. `read_file` — the
/// tool a MODEL calls — was deliberately left on the old shape by that plan (§7):
/// upgrading a tool RESULT to image blocks is real work per provider and waits for
/// a reason, so what it hands back is still base64 text, and the arithmetic above
/// is still the arithmetic that decides this number.
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

/// A picture the person picked to ATTACH, larger than this on disk, refuses the read
/// (`shell.readPickedImage`) before a byte of it is decoded.
///
/// A BOUND AGAINST ABSURDITY, NOT A BUDGET, and that is the whole difference between
/// this number and `PICKED_FILE_SIZE_BOUND` above. That one is a budget: what it lets
/// through is charged to a model turn as base64 text, so the ceiling is doing
/// arithmetic about context windows. Nothing of the sort applies here — what crosses
/// this path is the DOWNSCALED re-encode (`ENCODED_IMAGE_SIZE_BOUND`), never the
/// original, so the file's own size buys the turn nothing and costs it nothing. All
/// this number has to do is stop the two absurd cases: a 400 MB scan that would be
/// read into memory in one `Vec` before anything looked at it, and a decode that
/// allocates width × height × 4 bytes for an image nobody meant to pick.
///
/// TWENTY-FOUR MEBIBYTES because that is comfortably past every camera a person
/// actually owns — a 48-megapixel phone HEIC is ~5 MB, a full-frame RAW-adjacent JPEG
/// ~15 MB — and comfortably short of the sizes that are only ever a mistake. A photo
/// simply works, which is decision 3 of the plan and the entire point of the path.
///
/// STILL A REFUSAL AND STILL EARLY. This handler moves its work to
/// `spawn_blocking` (the pump-stall `PICKED_FILE_SIZE_BOUND` describes is why), so
/// an oversize read here does not wedge the app the way it would above — but a
/// 400 MB allocation in the highest-trust process is worth refusing on its own, and
/// refusing it BEFORE the read is what keeps the refusal from having already done
/// the damage.
///
/// Kept a whole number of MB: the sentence names it in MB and derives it from here.
const PICKED_IMAGE_SIZE_BOUND: u64 = 24 * 1024 * 1024;

/// What the ENCODED picture must fit under before it may cross the bridge.
///
/// THE ONE THAT IS ACTUALLY A BUDGET, where the constant above deliberately is not.
/// These bytes are base64'd onto one line of the stdio channel, held in the core's
/// attachment cache, written into `message_attachments` as text (plan §5) and sent to
/// a vision API on every turn the message is replayed in. Two mebibytes is where
/// every one of those stays boring: ~2.8 MB of characters on the wire, four of them
/// per message at most, and inside what all four providers accept for one image.
///
/// NOT A REFUSAL FIRST, unlike every other ceiling in this file. The encode STEPS
/// DOWN to meet it — quality 80 → 60 → 40, then the long edge 1600 → 1200 → 800 —
/// because the person has already chosen this picture and there is a version of it
/// that fits. Refusing a photo for being a photo would fail the one requirement the
/// path exists to meet. Only when the smallest step still will not fit is it refused,
/// and then plainly.
const ENCODED_IMAGE_SIZE_BOUND: usize = 2 * 1024 * 1024;

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

/// How much of one file the shell will read in order to HASH it
/// (`shell.digestWorkspaceFiles`, the review surface's "has this changed since Addison
/// wrote it?" — phase-3 plan Build §2).
///
/// ITS OWN CONSTANT, though it equals `UNDO_SIZE_BOUND` today, because it answers a
/// third question: how much may be read for an answer that is ONE WORD LONG. Nothing
/// crosses the bridge from this read — not a byte of the file — so the ceiling is not
/// protecting the channel the way `READ_SIZE_BOUND` does. It is protecting the pump:
/// this handler is awaited inline like every other, and it is asked about up to
/// `MAX_BATCH_PATHS` files at once — a number this process now enforces rather than
/// one the core was trusted to keep to.
///
/// The number is `UNDO_SIZE_BOUND` because that is the size class of file this can be
/// asked about at all. A file Addison OVERWROTE was at most that big when it did (the
/// write path refuses a larger prior), and a file Addison CREATED was written whole
/// inside one model turn. A file now larger than this has changed by more than a
/// digest was going to tell anyone.
///
/// OVER THE BOUND IS `null`, NEVER A REFUSAL and never a guess: "Addison can't tell
/// whether this changed since" is a true sentence and an honest one to show, and it is
/// the same answer this method gives for a row written before digests existed.
const DIGEST_SIZE_BOUND: u64 = UNDO_SIZE_BOUND as u64;

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

/// How many files ONE batch question may name (`shell.canRestoreWorkspaceFiles`,
/// `shell.digestWorkspaceFiles`).
///
/// CAPPED HERE, in the shell, for `MAX_DIR_ENTRIES`'s reason and `UNDO_SIZE_BOUND`'s:
/// this is where the bytes are, and the core's list is an INPUT to this boundary and
/// never the boundary. `digest_workspace_files`'s own doc comment already says "up to
/// two hundred files at once" — but two hundred was enforced only core-side, so the
/// sentence described a convention rather than a limit, and every element of that
/// array costs a `stat`, an open and up to `DIGEST_SIZE_BOUND` hashed, all of it
/// awaited INLINE on the core's stdout pump (`agent_process.rs`). An array of fifty
/// thousand paths is the same wedge every ceiling in this file exists to prevent,
/// arriving through a list rather than through one big file.
///
/// TWO HUNDRED because that is the number the core's caller was written to send and
/// the number this file already claims. A shell-side ceiling BELOW the core's would
/// turn a working screen into a refused one for no reason a person could see.
const MAX_BATCH_PATHS: usize = 200;

/// Worded once because both batch methods raise it, and derived from the constant so
/// the number in the sentence cannot drift from the number in the check.
fn refuse_oversize_batch(paths: &[Value]) -> Result<(), RpcError> {
    // A REFUSAL, not a truncation of the list, and this is the one place in this file
    // where that decision needs arguing rather than restating. Both callers read a MAP
    // keyed by path and treat an ABSENT key as the cautious answer ("Addison can't
    // tell", "not restorable"), so silently answering the first two hundred would be
    // SAFE — and that is exactly what makes it the wrong choice. It would be
    // indistinguishable, on screen, from two hundred files Addison genuinely could not
    // judge: a person would read "Addison can't tell" beside files it could tell about
    // perfectly well, and nothing anywhere would say the question had been cut short.
    // A caller past this bound is not the caller this was built for — it is a bug or a
    // steered payload — and a total failure is a better one than a plausible-looking
    // partial answer.
    //
    // SAID PLAINLY: the core folds a refusal from either of these into an empty map
    // (`workspace._restorable_map` / `_digest_map` catch and return `{}`), so this does
    // not reach a person as a sentence either way. What it buys is not a better error
    // message, it is the boundary holding at all — and it costs nothing today, because
    // the core's own list is capped at the same 200 (`file_revert._MAX_EDITS`, rows,
    // which group to at most that many paths). This refusal is unreachable from the
    // shipped caller by construction, which is exactly the condition under which a
    // floor is cheap to keep.
    if paths.len() > MAX_BATCH_PATHS {
        return Err(RpcError::app(format!(
            "Addison can only look at {MAX_BATCH_PATHS} files at once."
        )));
    }
    Ok(())
}

/// Worded once because six paths raise it — the three reads, the write's capture of the
/// prior text, the digest, and the undo's write-back. The person must not be able to tell
/// which refused.
const NOT_A_REGULAR_FILE: &str = "That isn't an ordinary file, so Addison won't open it.";

/// A path that is not a REGULAR file, refused from metadata that has already been
/// taken — before anything opens it.
///
/// THE WEDGE THIS CLOSES, and it is the sharpest one left in this file. A FIFO reports
/// `len() == 0`, so EVERY size ceiling above waves it through; `fs::read` and
/// `File::open` then BLOCK until somebody opens the other end, which on a named pipe
/// nobody ever does (POSIX). Every `shell.*` handler is awaited INLINE on the core's
/// stdout pump and none of these methods is in `dispatch_off_loop`, so that block is
/// permanent and total: no core frame of any kind is relayed again, the core's bridge
/// times out, and the app never recovers. `read_project_file` is SHIPPED, so this was
/// one `mkfifo` inside a trusted root away from a model in OPEN mode.
///
/// OPENING TO WRITE IS THE SAME WEDGE, which the first version of this check did not
/// enumerate: `fs::write` opens `O_WRONLY`, and on a FIFO that waits for a READER rather
/// than for data. `restore_workspace_path` is therefore the sixth caller, and the only
/// one a person reaches with a click rather than a model reaches with a tool.
///
/// THE CHECK IS THE METADATA, not a second syscall. Every caller already asks the OS
/// about the path in order to judge its size; `stat_on_disk` hands back the whole
/// answer so both questions are asked of ONE stat, at one moment. A separate
/// `is_file()` call would be a second syscall and — worse — a second moment.
///
/// A DIRECTORY is refused here too. That is not new behaviour dressed up as a fix:
/// `fs::read` on a directory already failed, with "Addison couldn't read that file"
/// mapped from an errno. This says the true thing instead of the generic one.
///
/// NO `O_NONBLOCK` OPENER. It would work on the FIFO and buy nothing elsewhere: it is
/// POSIX-only, it would have to be undone before the read to avoid short reads on a
/// slow device, and the honest answer to "may Addison read this?" is decided by what
/// the thing IS, not by whether opening it happened to return.
fn refuse_non_regular_file(meta: &std::fs::Metadata) -> Result<(), RpcError> {
    if !meta.is_file() {
        return Err(RpcError::app(NOT_A_REGULAR_FILE));
    }
    Ok(())
}

/// Worded once because the two paths that ask it — the viewer's read and the undo's
/// write-back — must read the same to a person: which one refused is not their business,
/// and it is the same fact about the same name in both.
const A_SHORTCUT_STANDS_THERE: &str =
    "That name is a shortcut to somewhere else, so Addison won't follow it.";

/// A SHORTCUT standing at the recorded name, refused before anything opens or writes it.
///
/// WHAT IT CLOSES (KNOWN-GAPS, "The shell follows a shortcut planted at a path it once
/// wrote"). `restore_workspace_path` checks its session ledger against the NAME and then
/// `fs::write`s that name; `read_workspace_view` opens it. Both of those follow a link,
/// so a path Addison legitimately wrote is a write-through — or a read-through — to
/// wherever that name later points, and it takes no attacker to arrive, only somebody
/// moving a config file into a dotfiles folder and linking it back.
///
/// DEFENCE IN DEPTH, and it is the shell's own half. The core refuses first for both
/// shipped callers (`file_revert.replaced_by_a_link` for the review surface,
/// `another_file_stands_there` for the chat header's Undo). What that leaves is a future
/// caller that has not asked core-side, and a row written before `wrote_ident` existed,
/// which cannot ask. Neither of those reaches a core guard; both reach this one.
///
/// `symlink_metadata`, NEVER `metadata` — the question is what this directory entry IS,
/// and the whole failure being closed is a check that resolved the name and then acted on
/// the resolution. Nothing here follows the link, and nothing here acts on its target.
///
/// A PATH THAT IS NOT THERE IS NOT REFUSED, the same "cannot judge yet" every other check
/// in this file treats as carry-on: an undo legitimately creates a file again when the
/// write overwrote one that has since been removed, and a read that is not there already
/// has its own sentence.
///
/// ONLY THE NAME ITSELF. A link somewhere in the parent chain is a different question with
/// a different owner — `refuse_addison_data_dir` walks the whole chain for the one thing
/// containment has to know — and this refusal deliberately does not answer it.
///
/// `adopt_workspace_path_in` asks the same question and answers `adopted: false` instead
/// of raising: it is a query about whether a restore WOULD be allowed, so a refusal there
/// is a `false`, not a sentence a person reads.
fn refuse_shortcut_at_path(path: &Path) -> Result<(), RpcError> {
    if std::fs::symlink_metadata(path).map(|meta| meta.is_symlink()).unwrap_or(false) {
        return Err(RpcError::app(A_SHORTCUT_STANDS_THERE));
    }
    Ok(())
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
        // Attaching a picture to a chat message (image-attach plan §4). TWO METHODS
        // of their own rather than a mode on the two above, because the answers are
        // different in kind: `pickFile` hands back a handle and nothing else, while a
        // composer chip needs a name and a size the moment the dialog closes, and
        // `readScopedFile` reads bytes through where this DECODES them, downscales
        // them and re-encodes them. A parameter would have made both contracts
        // conditional on a flag, which is the shape a later edit gets wrong.
        "shell.pickImage" => pick_image(app).await,
        "shell.readPickedImage" => read_picked_image(app, params).await,
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
        // The review surface's REVERT half (phase-3 plan Build §3). Neither of these
        // changes anything: the first is a pure question about this session's write
        // ledger and touches no file at all, the second reads files only to hash them
        // and answers one word per file. Together they are what lets the surface offer
        // a Revert only where one would actually work, and warn before it clobbers.
        "shell.canRestoreWorkspaceFiles" => can_restore_workspace_files(app, params),
        "shell.digestWorkspaceFiles" => digest_workspace_files(params),
        // The delete preview (5.6, first form). A bounded directory walk that opens
        // no file, follows no link and changes nothing, it answers "how much is
        // under here" so a permission card for a delete can say what the delete
        // costs. It belongs in this table rather than off the loop because the cap
        // is what bounds it: it stops after `MAX_PREVIEW_ENTRIES`, unlike a command,
        // which can hold its task for a whole budget.
        "shell.previewDeletePaths" => preview_delete_paths(params),
        // The narrow way BACK INTO the ledger after a restart, and the only one
        // (phase-3 plan Build §3's deferred item). It adds a path to the session
        // ledger if and only if the bytes standing there now hash to what the core
        // recorded when it wrote them, so what it re-admits is a file Addison itself
        // wrote and nobody has touched since, never an arbitrary path, and never a
        // widening of `restore_workspace_path` to "inside a trusted root".
        "shell.adoptWorkspacePath" => adopt_workspace_path(app, params),
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

    Ok(json!({ "fileHandle": mint_picked_handle(app, path) }))
}

/// Take a path the person just chose and give back the OPAQUE handle the core will
/// name it by, remembering the pairing for `resolve_picked_handle`.
///
/// One function for both pickers because it is one rule, and the rule is the whole
/// provenance argument (spec §9): the core learns a handle, never a path, so nothing
/// it reads can point it at a second file. Two copies of a three-line mint is how a
/// third picker one day gets a handle that is minted into a different map, or not
/// remembered at all — and both failures look like "please pick it again" to the
/// person, with nothing to say why.
fn mint_picked_handle(app: &AppHandle, path: PathBuf) -> String {
    let handle = uuid::Uuid::new_v4().to_string();
    lock(&app.state::<FileState>().handles).insert(handle.clone(), path);
    handle
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
    let path = resolve_picked_handle(state, handle)?;

    // Judged from the file's SIZE, before a byte is read, exactly as the workspace
    // paths do it: a refusal that first allocates the 2 GB it is refusing has
    // already stalled the app it was protecting. A size the OS won't give us is not
    // a refusal — fall through to the read, whose own error mapping speaks.
    //
    // And from the same stat, WHAT IT IS: a person can pick a FIFO in an OS dialog by
    // typing its name, and `fs::read` on one never returns (`refuse_non_regular_file`).
    if let Some(meta) = stat_on_disk(&path) {
        refuse_non_regular_file(&meta)?;
        refuse_oversize_pick(meta.len())?;
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

/// The path behind a handle THIS SESSION's picker minted, or the one refusal both
/// picked reads give.
///
/// RESOLVED IN ONE PLACE, because there are two readers now — `read_scoped_handle`
/// (the `read_file` tool) and `read_picked_image` (the attach path) — and the
/// property they share is the entire security argument for handles: only a handle the
/// shell itself minted resolves to anything at all, so nothing the core learned from
/// one file can point it at a second, and a raw path is not a handle.
///
/// ONE SENTENCE FOR EVERY WAY IT CAN FAIL. An unknown handle, a guessed one and one
/// left over from a previous run are indistinguishable here on purpose — they are the
/// same thing to the person, and picking again is the only answer to any of them.
fn resolve_picked_handle(state: &FileState, handle: &str) -> Result<PathBuf, RpcError> {
    lock(&state.handles)
        .get(handle)
        .cloned()
        .ok_or_else(|| RpcError::app("Addison can't read that file — please pick it again."))
}

/// What a person may be offered in the ATTACH picker (`shell.pickImage`).
///
/// WIDE ON PURPOSE, and wider than what the crate can actually decode. A filter's job
/// is to stop somebody hunting through a folder of spreadsheets for their photo, not
/// to decide what is real — that is the decoder's job, one step later, on the bytes
/// (`encode_picked_image`, where a file that does not parse is refused in plain
/// language). A filter used as a validator is the extension-guessing this path exists
/// to retire, and it fails in the direction that hurts: a `.png` that is really a
/// screenshot saved as something else is admitted by name and refused by content,
/// while a correct picture with an odd name would have been hidden from the person
/// who knew perfectly well where it was.
///
/// `heic` IS LISTED AND WILL NOT DECODE. It is what an iPhone hands you, so hiding it
/// would leave people unable to find the file they went looking for and unable to
/// learn why; listing it means they pick it and read one honest sentence saying
/// Addison does not know that kind. That is a better failure than an empty folder.
const PICKABLE_IMAGE_EXTENSIONS: &[&str] =
    &["png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "heic"];

/// The media types an encoded picture may cross the bridge as.
///
/// **HAND-SYNCED, ENTRY FOR ENTRY, WITH `ALLOWED_IMAGE_MEDIA_TYPES` IN
/// `agent_core/providers/base.py`** — the `OS_AUTOMATION_DIRS` precedent (exec.rs).
/// There is no codegen and no runtime handshake; the two lists are kept in lockstep
/// by hand, and the whole claim of a CLOSED set is that both sides mean the same four.
/// Change one, change the other in the same commit.
///
/// THIS SIDE IS THE ENFORCEMENT (image-attach plan §3), which is why the list has to
/// be here at all rather than only there. The provider adapters deliberately do NOT
/// re-check it: an assertion in an adapter could only turn a shell bug into a stack
/// trace in the middle of somebody's sentence, which the house rule forbids. So a
/// picture carries one of these because it was minted here, on this list, and there
/// is no other door.
///
/// FOUR, because four is what every vision API on the router's list accepts. A fifth
/// would work for whichever provider happened to answer that turn and be refused by
/// the other three — a message that fails for some people and not others, with
/// nothing on screen to say why.
const ALLOWED_IMAGE_MEDIA_TYPES: &[&str] =
    &["image/png", "image/jpeg", "image/gif", "image/webp"];

/// The long edge a picture is fitted to, and the two steps below it.
///
/// SIXTEEN HUNDRED is the plan's number (§4) and it is a vision-model number, not a
/// screen one: every provider on the list tiles an image internally at roughly this
/// scale, so pixels past it are paid for on every turn the message is replayed in and
/// read by nobody. The two steps below exist only for `ENCODED_IMAGE_SIZE_BOUND` — a
/// picture that will not fit at 1600 gets smaller rather than refused.
const IMAGE_LONG_EDGES: [u32; 3] = [1600, 1200, 800];

/// JPEG quality, and the two steps below it.
///
/// EIGHTY first, because that is where a photograph stops looking re-encoded. The
/// steps below are tried before any pixels are thrown away, in that order and not the
/// other one: a 1600px picture at quality 40 still shows a model everything an
/// 800px picture at quality 80 does, and more. Quality is the cheaper thing to spend.
const JPEG_QUALITY_STEPS: [u8; 3] = [80, 60, 40];

/// Said when the bytes do not parse as a picture — the ONE refusal that stands for
/// every way that can be true.
///
/// DECODING IS THE VALIDATION on this path (plan §4), and this sentence is what that
/// decision costs: a damaged file, a file renamed to `.png` that never was one, and a
/// HEIC the crate has no decoder for all arrive here and all read the same. That is
/// honest rather than vague — Addison genuinely cannot tell them apart, and the two
/// things a person can do about any of them (try another file, convert it) are the
/// same. Naming a format it "does not know" is the half that keeps it from reading as
/// an accusation about their file.
const NOT_A_PICTURE: &str =
    "Addison couldn't read that as a picture — it may be damaged or a kind Addison doesn't know.";

/// The final refusal, when even the smallest step will not fit under the encoded
/// bound. Unreachable for anything a camera produces — 800px at quality 40 is tens of
/// kilobytes — and kept anyway, because "we stepped down and it still did not fit" has
/// to end in a sentence rather than in whatever the last attempt happened to weigh.
const COULD_NOT_SHRINK_PICTURE: &str =
    "Addison couldn't make that picture small enough to send — please try a smaller one.";

/// Said when the encoder itself fails. Not a decode failure (that is `NOT_A_PICTURE`,
/// and it is about the person's file); this is Addison's own machinery giving up, and
/// it says so without a stack trace or a format name nobody asked about.
const COULD_NOT_PREPARE_PICTURE: &str = "Addison couldn't prepare that picture to send.";

/// One encoded picture, exactly as it will cross the bridge.
struct EncodedImage {
    bytes: Vec<u8>,
    /// Always a member of `ALLOWED_IMAGE_MEDIA_TYPES` — by construction, not by check:
    /// pass-through is filtered THROUGH that list, and the re-encode can only produce
    /// JPEG or PNG.
    media_type: &'static str,
    /// The FINAL dimensions, after any downscale — never the original's. The composer
    /// draws a thumbnail from these and the budget counts an image by them, so the
    /// number that describes the bytes is the only honest one to send.
    width: u32,
    height: u32,
}

/// Written by hand rather than derived, because a derived one would print up to two
/// mebibytes of pixel data into a test failure — burying the assertion that failed
/// under the picture it was about. What a reader needs is the shape: what it is, how
/// big it is, and how much it weighs.
impl std::fmt::Debug for EncodedImage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "EncodedImage {{ {} {}x{}, {} bytes }}",
            self.media_type,
            self.width,
            self.height,
            self.bytes.len()
        )
    }
}

// shell.pickImage {} -> {fileHandle, name, byteSize}
//
// `pick_file`'s sibling, and deliberately not `pick_file` with a flag: this one hands
// back a NAME and a SIZE as well as the handle, because the composer draws a chip the
// moment the dialog closes and the read that follows may take a second on a large
// photo. Two commands with two honest contracts beat one whose result shape depends
// on an argument.
//
// The handle is minted into the SAME session map, so everything the handle argument
// buys `pick_file` (see `resolve_picked_handle`) it buys here unchanged: the core
// learns a name to show a person, never a path it could walk from.
async fn pick_image(app: &AppHandle) -> Result<Value, RpcError> {
    let picked: Option<PathBuf> = on_main(app, move || {
        rfd::FileDialog::new()
            // "Pictures", not "Images": the personas are 54 and 68 and this is a file
            // dialog, not a developer tool (CLAUDE.md — plain language everywhere a
            // person reads).
            .add_filter("Pictures", PICKABLE_IMAGE_EXTENSIONS)
            .pick_file()
    })
    .await?;
    let path = picked.ok_or_else(|| RpcError::app("You closed the picker without choosing."))?;

    // The size is what the file claims RIGHT NOW, and it is display-only: the chip
    // says "2.4 MB" beside a name while the read is still going. Nothing decides
    // anything from it — `read_and_encode_picked_image` takes its own stat and its own
    // refusal — so a stat the OS will not answer is a 0 here rather than a failed
    // pick, and the read speaks if there is something to say.
    let byte_size = stat_on_disk(&path).map(|meta| meta.len()).unwrap_or(0);
    let name = display_name(&path);

    let handle = mint_picked_handle(app, path);
    Ok(json!({ "fileHandle": handle, "name": name, "byteSize": byte_size }))
}

// shell.readPickedImage {fileHandle} -> {content, mediaType, name, byteSize, width, height}
//
// ASYNC, and every byte of the work is on `spawn_blocking`. `handle()` is awaited
// INLINE on the core's stdout pump (`agent_process.rs`, and `PICKED_FILE_SIZE_BOUND`
// above explains what that costs), and this is the one method in this file whose work
// is measured in seconds rather than syscalls: decoding a 24 MB photo, resampling it
// with Lanczos3 and re-encoding it is real CPU, and doing it on the pump would freeze
// every frame in the app — the typing indicator, the streaming answer, all of it —
// for as long as it took. `run_command` gets `dispatch_off_loop` for the same reason
// at a larger scale; this needs only that its own body not sit on the loop.
//
// The handle is resolved BEFORE the hop, on the loop, because that is a map lookup and
// because `FileState` lives in Tauri's managed state and does not travel. What crosses
// into the blocking task is one `PathBuf` and nothing else.
async fn read_picked_image(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    let handle = required_str(params, "fileHandle", "A file handle is required.")?;
    let path = resolve_picked_handle(app.state::<FileState>().inner(), handle)?;

    // THE BASE64 IS PART OF THE WORK, so it happens in here with the rest of it. It
    // used to be computed on the line that builds the JSON below — back on the pump,
    // after the hop had carefully taken the decode off it — and encoding 2 MiB is not
    // free: it allocates a ~2.7 MiB String and walks every byte. The comment above
    // promises this method's body does not sit on the loop, and this is what makes the
    // promise true rather than nearly true.
    let picture = tauri::async_runtime::spawn_blocking(move || {
        let (name, encoded) = read_and_encode_picked_image(&path)?;
        Ok::<_, RpcError>(PreparedPicture {
            name,
            content: base64::engine::general_purpose::STANDARD.encode(&encoded.bytes),
            media_type: encoded.media_type,
            byte_size: encoded.bytes.len(),
            width: encoded.width,
            height: encoded.height,
        })
    })
    .await
    // The task itself panicking or being cancelled is not a thing a person can act
    // on, and it must not surface as a hang: one plain sentence, same as any other
    // failure of Addison's own machinery.
    .map_err(|_| RpcError::app(COULD_NOT_PREPARE_PICTURE))??;

    Ok(json!({
        "content": picture.content,
        "mediaType": picture.media_type,
        "name": picture.name,
        // The FINAL byte count, of the bytes actually being sent — never the file's
        // size on disk. The two differ by an order of magnitude for a phone photo, and
        // the number a person is shown beside a picture that is about to cost them a
        // turn should be the one that costs them the turn.
        "byteSize": picture.byte_size,
        "width": picture.width,
        "height": picture.height,
    }))
}

/// What the blocking task hands back: the picture already in the shape the wire
/// wants. Held as a struct rather than a six-tuple because the two `u32`s and the
/// `usize` beside them are exactly the arguments a tuple lets you swap by accident.
struct PreparedPicture {
    name: String,
    /// Base64 of the encoded bytes — computed on the blocking task, never on the pump.
    content: String,
    media_type: &'static str,
    /// The FINAL byte count, of the bytes actually being sent — never the file's size
    /// on disk. The two differ by an order of magnitude for a phone photo, and the
    /// number a person is shown beside a picture that is about to cost them a turn
    /// should be the one that costs them the turn.
    byte_size: usize,
    width: u32,
    height: u32,
}

/// Read the picked file and turn it into what crosses the bridge: `(name, encoded)`.
///
/// EVERYTHING BLOCKING LIVES HERE, on the blocking task — the stat, the read and the
/// decode/resize/encode — so the caller above is one lookup and an await.
fn read_and_encode_picked_image(path: &Path) -> Result<(String, EncodedImage), RpcError> {
    // The same two questions of one stat every read path in this file asks, in the
    // same order: WHAT IT IS first (a person can type a FIFO's name into an OS dialog,
    // and `fs::read` on one never returns — `refuse_non_regular_file`), then how big.
    // A size the OS will not give us is not a refusal; the read below has its own
    // sentence.
    if let Some(meta) = stat_on_disk(path) {
        refuse_non_regular_file(&meta)?;
        refuse_oversize_picture(meta.len())?;
    }
    let bytes = std::fs::read(path).map_err(|_| RpcError::app("Addison couldn't read that file."))?;
    // The file that GREW between the stat and the read, or one metadata could not
    // answer for at all. It has already cost this process the memory; it does not also
    // get to be decoded, which is where the cost multiplies. The same race backstop
    // the two reads above carry, and like them unreachable from a test.
    refuse_oversize_picture(bytes.len() as u64)?;

    Ok((display_name(path), encode_picked_image(bytes)?))
}

/// The picked-picture ceiling, refused in plain language. The size is named in the
/// sentence and derived from the constant, so the two cannot drift apart.
///
/// A SIBLING of `refuse_oversize_pick`, not a reuse of it, for that function's own
/// stated reason: same shape, different bound, different sentence — and this one says
/// "picture" because that is what the person is standing in front of.
fn refuse_oversize_picture(len: u64) -> Result<(), RpcError> {
    if len > PICKED_IMAGE_SIZE_BOUND {
        return Err(RpcError::app(format!(
            "That picture is too big for Addison to open — please pick one that's {} MB or smaller.",
            PICKED_IMAGE_SIZE_BOUND / (1024 * 1024)
        )));
    }
    Ok(())
}

/// The media type an ORIGINAL of this format may cross the bridge as, or `None` when
/// re-encoding is the only way to reach the closed set.
///
/// FILTERED THROUGH `ALLOWED_IMAGE_MEDIA_TYPES` rather than matched against a second
/// list of formats. The crate names a mime type for everything it can decode — BMP is
/// `image/bmp`, TIFF is `image/tiff` — and those are exactly the ones no vision API on
/// the list takes, so letting the closed set do the deciding means a format added to
/// the picker's filter cannot accidentally pass through as itself. One list, one
/// answer.
fn passthrough_media_type(format: image::ImageFormat) -> Option<&'static str> {
    let named = format.to_mime_type();
    ALLOWED_IMAGE_MEDIA_TYPES.iter().copied().find(|allowed| *allowed == named)
}

/// Decode, downscale and re-encode picked bytes — the whole of what
/// `shell.readPickedImage` does to them, as a function of the bytes alone.
///
/// A PURE FUNCTION, factored out for `read_scoped_handle`'s reason: none of this needs
/// a live Tauri app, a dialog or a file, so all of it is reachable from an ordinary
/// unit test, which is where the refusals and the closed media-type set are actually
/// pinned.
///
/// **DECODING IS THE VALIDATION** (plan §4). There is no magic-byte table and no
/// extension check on this path: bytes that parse as an image are an image, and bytes
/// that do not are refused with `NOT_A_PICTURE`. That is stronger than either guess —
/// it is the same decoder the re-encode will use — and it is what lets the picker's
/// filter be generous.
///
/// PASS-THROUGH IS AN EXCEPTION, and a narrow one: a picture that is ALREADY one of
/// the four, ALREADY under the encoded bound and ALREADY within the long edge crosses
/// byte-for-byte. Re-encoding a small photo is pure loss — a JPEG round-tripped
/// through quality 80 is visibly worse and no smaller — and a PNG screenshot of text,
/// the single most common thing anybody attaches, is exactly the image JPEG treats
/// worst. All three conditions have to hold: the point is that nothing changed, so
/// there is nothing to gain by touching it.
///
/// GIF LOSES ITS ANIMATION when it cannot pass through, and that is accepted rather
/// than solved. `load_from_memory` hands back the first frame, so a GIF too large or
/// too big-edged to pass through is re-encoded as that one frame. A model looks at
/// still pictures; the alternative is refusing the file outright, which serves nobody,
/// and re-encoding every frame is a video pipeline this path has no reason to grow.
fn encode_picked_image(original: Vec<u8>) -> Result<EncodedImage, RpcError> {
    // The format is read from the BYTES, never from a name — this function has never
    // seen a name. `Err` here simply means "no pass-through", because the decode below
    // is the answer that matters.
    let format = image::guess_format(&original).ok();
    let decoded = image::load_from_memory(&original).map_err(|_| RpcError::app(NOT_A_PICTURE))?;

    let (width, height) = (decoded.width(), decoded.height());
    if let Some(media_type) = format.and_then(passthrough_media_type) {
        if original.len() <= ENCODED_IMAGE_SIZE_BOUND && width.max(height) <= IMAGE_LONG_EDGES[0] {
            return Ok(EncodedImage { bytes: original, media_type, width, height });
        }
    }

    // ALPHA DECIDES THE FORMAT, and it is asked of the DECODED image rather than of the
    // original's format: a GIF's first frame comes back RGBA whatever the file looked
    // like, and a PNG that has an alpha channel it never uses is still a PNG. Erring
    // towards PNG costs bytes; erring the other way turns transparent pixels black,
    // which is a picture that lies about itself.
    let keeps_alpha = decoded.color().has_alpha();

    for edge in IMAGE_LONG_EDGES {
        let needs_resize = width.max(height) > edge;
        if !needs_resize && edge != IMAGE_LONG_EDGES[0] {
            // Already smaller than this step, so this iteration would encode exactly
            // the bytes the previous one did and fail exactly the same way.
            continue;
        }
        let scaled;
        let candidate = if needs_resize {
            // LANCZOS3, not Triangle. This is CPU on a blocking task, not on the pump,
            // so the cost is a second nobody is watching — and the thing most often
            // attached is a screenshot with text in it, where a soft filter smears the
            // strokes into something a model reads wrong with total confidence.
            // Lanczos3 keeps the edges. `resize` fits INSIDE the box and preserves the
            // aspect ratio, which is why both arguments are the same number.
            scaled = decoded.resize(edge, edge, image::imageops::FilterType::Lanczos3);
            &scaled
        } else {
            &decoded
        };

        // QUALITY BEFORE PIXELS: every step of the JPEG ladder is spent at this edge
        // before the loop moves to a smaller one. PNG has no such knob — it is lossless
        // — so the alpha branch has exactly one attempt per step, and shrinking is the
        // only thing left to try.
        if keeps_alpha {
            let bytes = encode_png(candidate)?;
            if bytes.len() <= ENCODED_IMAGE_SIZE_BOUND {
                return Ok(EncodedImage {
                    bytes,
                    media_type: "image/png",
                    width: candidate.width(),
                    height: candidate.height(),
                });
            }
        } else {
            for quality in JPEG_QUALITY_STEPS {
                let bytes = encode_jpeg(candidate, quality)?;
                if bytes.len() <= ENCODED_IMAGE_SIZE_BOUND {
                    return Ok(EncodedImage {
                        bytes,
                        media_type: "image/jpeg",
                        width: candidate.width(),
                        height: candidate.height(),
                    });
                }
            }
        }
    }

    Err(RpcError::app(COULD_NOT_SHRINK_PICTURE))
}

/// PNG bytes for a picture that has transparency to keep.
fn encode_png(image: &image::DynamicImage) -> Result<Vec<u8>, RpcError> {
    let mut buffer = std::io::Cursor::new(Vec::new());
    image
        .write_to(&mut buffer, image::ImageFormat::Png)
        .map_err(|_| RpcError::app(COULD_NOT_PREPARE_PICTURE))?;
    Ok(buffer.into_inner())
}

/// JPEG bytes at one quality step.
///
/// `to_rgb8` first, deliberately: the encoder is handed three channels because JPEG
/// has no fourth, and this is only ever called for an image that has no alpha to lose.
fn encode_jpeg(image: &image::DynamicImage, quality: u8) -> Result<Vec<u8>, RpcError> {
    let mut bytes: Vec<u8> = Vec::new();
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut bytes, quality)
        .encode_image(&image.to_rgb8())
        .map_err(|_| RpcError::app(COULD_NOT_PREPARE_PICTURE))?;
    Ok(bytes)
}

/// The last component of a path, for showing beside a thumbnail and nothing else.
///
/// DISPLAY ONLY, and that is what makes it safe to send where a path never is: it says
/// what the person called their file, it cannot be walked from, and the core stores it
/// on the attachment RECORD rather than on the wire to any model (plan §3 — a model
/// told the file was "receipt.png" will answer about a receipt it never saw).
fn display_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().to_string())
        // A path with no final component is not something a native picker returns, and
        // a nameless chip is worse than a generic one.
        .unwrap_or_else(|| "picture".to_string())
}

// shell.writeWorkspaceFile {path, content} -> {existed, prior, newlineRestored}  (step 5)
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
    let restored = needs_trailing_newline(prior.as_deref(), content);
    // ONE write, of the bytes that actually land — never a write followed by a fixing
    // second write, which would put an intermediate state on disk and give the file's
    // watchers two events for one edit.
    let effective = if restored { format!("{content}\n") } else { content.to_string() };
    std::fs::write(&path, &effective)
        .map_err(|_| RpcError::app("Addison couldn't save that file."))?;
    // Ledger the path so restore_workspace_file may target it — and ONLY it.
    lock(&state.workspace_written).insert(path);
    // `newlineRestored` tells the core WHAT WAS WRITTEN when it is not what was sent,
    // and it exists for exactly one reader: the digest `write_project_file` records of
    // "the file as Addison left it". A digest of the sent text after writing one more
    // byte would report the file as edited-by-somebody-else the moment it was written.
    Ok(json!({ "existed": existed, "prior": prior, "newlineRestored": restored }))
}

/// Did this edit LOSE the file's trailing newline? (KNOWN-BUGS P3 #7.)
///
/// A model asked to append a line hands back the whole file with the new line at the
/// end and no `\n` after it, so the next thing appended fuses onto it —
/// `edited: yesmy own edit`. The lost byte is real and the fix belongs on the write.
///
/// THE RULE IS DELIBERATELY NARROW, and each half of it is a file this must not
/// touch:
///
///   * the file ENDED WITH A NEWLINE BEFORE and does not now → restore it. The
///     newline was there; this edit dropped it; nobody asked for that.
///   * the file is NEW, or ended WITHOUT one → leave the content exactly as sent. A
///     file deliberately kept without a trailing newline (plenty exist — a one-line
///     `.env` value, a fixture pinned byte-for-byte, a generated file whose generator
///     writes none) must not acquire one because Addison rewrote it, and a new file
///     is the model's to shape.
///   * EMPTY new content is left alone too: truncating a file to nothing is a
///     deliberate act with a deliberate result, and "" is not a line missing its
///     newline.
///
/// So this can only ever put back a byte that was there before. It cannot invent a
/// convention for a file that did not have one, which is the version of this fix that
/// would quietly rewrite files nobody asked it to.
fn needs_trailing_newline(prior: Option<&str>, content: &str) -> bool {
    match prior {
        Some(prior) => prior.ends_with('\n') && !content.is_empty() && !content.ends_with('\n'),
        None => false,
    }
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
    //
    // The SAME stat also answers what the path IS. A prior that is a FIFO reports
    // length 0, passes the bound, and then blocks `fs::read` forever — the write path
    // is a read path first, and it wedges identically (`refuse_non_regular_file`).
    if let Some(meta) = stat_on_disk(path) {
        refuse_non_regular_file(&meta)?;
        if meta.len() > UNDO_SIZE_BOUND as u64 {
            return Err(RpcError::app(TOO_BIG_TO_EDIT));
        }
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
    //
    // And from the same stat, WHAT IT IS. This is the method behind the shipped
    // `read_project_file`, so it is the one a model can point at a FIFO inside a
    // trusted root — where the size ceiling is no protection at all, because a pipe's
    // length is 0 and the read never returns (`refuse_non_regular_file`).
    if let Some(meta) = stat_on_disk(path) {
        refuse_non_regular_file(&meta)?;
        refuse_oversize_read(meta.len())?;
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
    // AND WHAT STANDS AT THE NAME, before the stat that would follow it. `stat_on_disk`
    // below resolves the link on purpose — it measures what would be LOADED — so by the
    // time this file has a size in hand it is already describing somewhere else. The
    // question has to be asked of the name itself, and asked first.
    refuse_shortcut_at_path(path)?;
    // The size is asked FIRST, exactly as every other read path here asks it — but this
    // one is not deciding a refusal with it, so it is not load-bearing for the wedge:
    // the read below is bounded by `take` no matter what metadata claims, which is the
    // stronger version of the same property (a file that grows between the two calls
    // cannot cost this process more than one extra byte). What metadata buys is the
    // HONEST `bytes`: how big the file actually is, which a truncated read cannot say
    // and which is the number the person needs to know how much is not shown.
    //
    // WHAT IT IS, though, IS load-bearing here, and `take` does not help: `File::open`
    // on a FIFO blocks before there is anything to bound (`refuse_non_regular_file`).
    let stat = stat_on_disk(path);
    if let Some(meta) = &stat {
        refuse_non_regular_file(meta)?;
    }
    let on_disk = stat.map(|meta| meta.len());
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
    //
    // NEVER FEWER THAN WHAT WE ACTUALLY HOLD. `on_disk` is a claim about a moment that
    // has passed, and a file that GREW between the stat and the read made the surface
    // say "showing 256 KB of 100 bytes" — a sentence that is not merely wrong but
    // self-evidently wrong, which is the kind that costs a person their trust in the
    // whole pane. Re-statting would be another syscall and another stale moment; the
    // floor below is free and cannot be stale, because the bytes are in hand.
    let total = on_disk.unwrap_or(0).max(bytes.len() as u64);
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

/// What the OS says about the file at `path`, or `None` when it won't say — a path
/// that isn't there, or one metadata is refused for. Follows symlinks, exactly as the
/// `fs::read`/`File::open` that follows it does, so what is measured is what would be
/// loaded. Callers treat `None` as "cannot judge yet", never as "fine".
///
/// HANDS BACK THE WHOLE ANSWER, where this used to be a `size_on_disk` that kept only
/// `len()`. Every caller has to ask the OS about the path anyway, and it has TWO
/// questions to ask of it — how big is this, and is it an ordinary FILE (see
/// `refuse_non_regular_file`, and the FIFO that wedged the pump because `len()` on one
/// is 0). Throwing the file type away here forced the second question to be a second
/// syscall, at a second moment, or — as it was — not asked at all.
fn stat_on_disk(path: &Path) -> Option<std::fs::Metadata> {
    std::fs::metadata(path).ok()
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
    // THE FLOOR FIRST, exactly as every other path in this file takes it, and it was
    // the ONE write path that did not. The ledger looked like enough — a path only
    // reaches this set because a write already passed the floor — but the two checks
    // happen at different MOMENTS, and `fs::write` follows symlinks: a ledgered file
    // replaced by a link into the data dir between the write and its undo wrote
    // straight through it. `workspace.revertFile` made that a second door, reachable
    // from a click rather than only from an undo.
    //
    // It can only ever tighten. A path the write path accepted is a path this accepts,
    // unless the ground moved underneath it — which is the case this is here for.
    refuse_addison_data_dir(&path)?;
    {
        let written = lock(&state.workspace_written);
        if !written.contains(&path) {
            return Err(RpcError::app("Addison can only undo a file change it made."));
        }
    }
    // AND WHAT STANDS THERE NOW, from one stat, before anything opens it.
    //
    // THE ENUMERATION MISSED THIS ONE. `refuse_non_regular_file` went in over the five
    // paths that READ, and the sixth — the one that opens for WRITING — was not on the
    // list. `fs::write` opens `O_WRONLY`, which on a FIFO blocks until a reader appears
    // and on a named pipe nobody ever does; this handler is awaited INLINE on the core's
    // stdout pump, so that block is the same permanent, total wedge, arrived at through
    // the one door a person can open with a single click (`workspace.revertFile`,
    // `undo.undoLastAction`). Nothing above it refuses either: the core's
    // `replaced_by_a_link` is `islink`, which is False for a FIFO, and
    // `canRestoreWorkspaceFiles` deliberately stats nothing, so the button is offered.
    //
    // A PATH THAT IS NOT THERE IS NOT REFUSED, and must not be: an undo legitimately
    // CREATES the file again when the write overwrote one that has since been removed.
    // `stat_on_disk` answers `None` for it, which is the same "cannot judge yet" every
    // read path treats as "carry on".
    //
    // BOTH BRANCHES, from the one stat. The delete branch does not block — `remove_file`
    // opens nothing — but a pipe, a device node or a directory at that name is not the
    // file the write created, and removing somebody else's is not an undo of anything.
    // `/dev/null` answered `Ok` here before this, having "restored" nothing at all.
    if let Some(meta) = stat_on_disk(&path) {
        refuse_non_regular_file(&meta)?;
    }
    // AND WHETHER THE NAME IS STILL A NAME, which the stat above cannot say: it follows
    // the link, so a shortcut planted over a ledgered path answers for its TARGET and
    // comes back an ordinary file. Both branches below take it. `fs::write` would put the
    // prior bytes into whatever that shortcut reaches — a private key, in the test that
    // plants one — and `remove_file` would take away a shortcut this session never
    // created, which is not an undo of anything either.
    //
    // AFTER THE LEDGER, so the sentence a person gets for a path Addison never wrote is
    // still the one about the ledger. This one only ever speaks about a path that was
    // Addison's to put back and has since stopped being it.
    //
    // A LEDGERED PATH CAN BECOME THIS HONESTLY: `write_workspace_path` follows a link,
    // so a config file linked back from a dotfiles folder is written through and the LINK
    // is the name that is ledgered. Its undo is refused here from now on — which is what
    // the core already answers for both shipped callers, and the point of the refusal is
    // that the shell says it too, for a caller that never asked the core.
    refuse_shortcut_at_path(&path)?;
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

// shell.canRestoreWorkspaceFiles {paths} -> {restorable: {<path>: bool}}   (phase-3 §3)
//
// A PURE QUESTION ABOUT THIS SESSION'S LEDGER. It opens no file, stats no path and
// changes nothing — it asks the same `workspace_written` set `restore_workspace_path`
// asks, and answers what that call WOULD say without making it.
//
// WHY IT EXISTS. The ledger is session-scoped and `Default`-constructed at launch,
// while `action_snapshots` rows survive indefinitely. So after any restart the core
// holds a list of edits it can describe perfectly and cannot put back, and the review
// surface — which reads the database, not the session — would render a Revert button
// beside every one of them and every one would fail. Asking first turns a dead button
// into an honest line of text.
//
// THE TWO THINGS IT MUST NOT BECOME, both of which would be easier than this:
//   * PERSISTING the ledger. Its whole security property is that it is session-scoped
//     and unsteerable — a restore path that survives restarts is a restore path that
//     outlives the reason it was granted.
//   * WIDENING `restore_workspace_path` to "inside a currently-trusted root". This
//     file documents the opposite on purpose: the ledger is session, not trust.
// This method is what makes both unnecessary, which is why it is a QUERY and not a
// second door into the same set.
fn can_restore_workspace_files(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    can_restore_workspace_paths(app.state::<FileState>().inner(), params)
}

fn can_restore_workspace_paths(state: &FileState, params: &Value) -> Result<Value, RpcError> {
    let paths = params
        .get("paths")
        .and_then(Value::as_array)
        .ok_or_else(|| RpcError::app("A list of file paths is required."))?;
    refuse_oversize_batch(paths)?;
    let written = lock(&state.workspace_written);
    // A MAP keyed by the path, never an array positioned against the caller's. An
    // array couples the answer to an order two processes have to agree on, and the
    // failure when they stop agreeing is silent and exactly wrong: a Revert offered
    // for a file that cannot take one, or withheld from one that can. A key that is
    // absent from this map is `false` on the other side, which is the safe direction.
    let mut restorable = serde_json::Map::new();
    for entry in paths {
        if let Some(path) = entry.as_str() {
            restorable.insert(path.to_string(), Value::Bool(written.contains(&PathBuf::from(path))));
        }
    }
    Ok(json!({ "restorable": restorable }))
}

// shell.adoptWorkspacePath {path, expectedSha256} -> {adopted: bool}   (phase-3 §3)
//
// THE POST-RESTART CASE, recovered without giving anything away. The write ledger dies
// with the process, so after a restart Addison can describe every past edit and put
// none of them back, a review surface full of honest sentences where a person wants a
// button. This is the one way a path re-enters that ledger, and it re-enters only on
// PROOF: the bytes standing at the name now must hash, byte for byte, to the digest the
// core recorded at the moment it wrote them (`wrote_sha256`).
//
// WHAT THAT PROOF BUYS, said exactly. A match means the file on disk IS the file
// Addison last wrote and nobody has changed it since, so putting the earlier text back
// destroys no work of anybody's, which is the whole risk a restore carries. A mismatch
// is refused rather than warned about: the surface's "somebody has changed this since"
// warning belongs to a live session, where the ledger already says yes.
//
// WHAT IT IS NOT, and must never become:
//   * PERSISTING the ledger. A restore path that survives a restart on its own outlives
//     the reason it was granted. This grants nothing until it is asked, and what it
//     grants is one path whose contents already answer for themselves.
//   * WIDENING to "inside a currently-trusted root". The digest is the test, not trust,
//     and it is the narrower of the two: a trusted root holds every file in the project,
//     while this holds only files Addison itself wrote and nobody has touched.
// It reads a file to hash it and changes no file at all.
fn adopt_workspace_path(app: &AppHandle, params: &Value) -> Result<Value, RpcError> {
    adopt_workspace_path_in(app.state::<FileState>().inner(), params)
}

fn adopt_workspace_path_in(state: &FileState, params: &Value) -> Result<Value, RpcError> {
    let path = PathBuf::from(required_str(params, "path", "A file path is required.")?);
    let expected = required_str(
        params,
        "expectedSha256",
        "Addison needs to know what it wrote there before it can put it back.",
    )?
    .to_ascii_lowercase();
    // THE FLOOR FIRST, as every path in this file takes it. `digest_workspace_path`
    // takes it again below; taking it here as well is what keeps the refusal a refusal
    // rather than a `false` that reads like an ordinary mismatch.
    refuse_addison_data_dir(&path)?;
    // AND WHAT THE NAME REACHES, without following it. A shortcut standing where
    // Addison wrote a file would otherwise be adopted on the strength of its TARGET's
    // bytes, and the ledgered name would then write straight through it, the exact
    // swap `restore_workspace_path` refuses the data dir for. `symlink_metadata`, never
    // `metadata`: the question is what this directory entry IS.
    if std::fs::symlink_metadata(&path).map(|meta| meta.is_symlink()).unwrap_or(false) {
        return Ok(json!({ "adopted": false }));
    }
    // The same read, the same ceiling and the same "can't tell" as the surface's own
    // digest, one hashing path in this file, so a file this cannot judge is a file the
    // surface already says it cannot judge.
    let digest = digest_workspace_path(&path);
    let matches = digest
        .get("sha256")
        .and_then(Value::as_str)
        .is_some_and(|found| found.eq_ignore_ascii_case(&expected));
    if !matches {
        return Ok(json!({ "adopted": false }));
    }
    lock(&state.workspace_written).insert(path);
    Ok(json!({ "adopted": true }))
}

// shell.digestWorkspaceFiles {paths} -> {digests: {<path>: {sha256, missing}}}  (§2)
//
// WHAT IS ON DISK NOW, in one word per file. The core recorded the digest of what it
// WROTE (`wrote_sha256`); comparing the two is how the surface tells "the file as
// Addison left it" from "the file somebody has edited since" — and that difference is
// the difference between a diff that is true and a Revert that silently throws away
// somebody's own work.
//
// A HASH AND NEVER THE BYTES. Reading each file across the bridge to hash it core-side
// would ship megabytes for a payload that carries none of it, on a channel where one
// oversized line stalls every frame in the app — the very thing the plan refuses when
// it makes `workspace.listEdits` metadata-only. So the hashing happens where the bytes
// already are, and what crosses is 64 characters.
//
// ONE ROUND TRIP for the whole list, for the same reason `escapes` reads the trust
// rows once: this answers a click, and a per-file round trip would put two hundred of
// them behind it.
fn digest_workspace_files(params: &Value) -> Result<Value, RpcError> {
    let paths = params
        .get("paths")
        .and_then(Value::as_array)
        .ok_or_else(|| RpcError::app("A list of file paths is required."))?;
    refuse_oversize_batch(paths)?;
    let mut digests = serde_json::Map::new();
    for entry in paths {
        if let Some(path) = entry.as_str() {
            digests.insert(path.to_string(), digest_workspace_path(Path::new(path)));
        }
    }
    Ok(json!({ "digests": digests }))
}

/// One file's answer: `{sha256: <hex> | null, missing: bool}`.
///
/// NEVER AN ERROR, whatever happens. One unreadable file among two hundred must not
/// take the other hundred and ninety-nine off the screen, and every failure here has
/// the same honest reading anyway: `null` is "Addison can't tell", which is what the
/// surface then says out loud rather than guessing.
///
/// `missing` is separated from `null` because the two mean different things to the
/// person: a file that is GONE is a fact worth showing (Revert can still put it back),
/// while a file that cannot be judged is a warning to withhold.
fn digest_workspace_path(path: &Path) -> Value {
    // The floor, first and unchanged: Addison's own data directory is not a place the
    // harness may ask questions about either. A refusal is folded into the ordinary
    // "can't tell" answer rather than raised, because this method never fails a batch.
    if refuse_addison_data_dir(path).is_err() {
        return unknowable_digest();
    }
    // THE SIZE BEFORE THE BYTES, this file's standing rule. Asked with `fs::metadata`
    // rather than `size_on_disk` because this path needs the two answers that helper's
    // `Option` collapses into one: a file that is NOT THERE and a file the OS will not
    // describe are the same `None` to it, and they are different sentences here.
    let meta = match std::fs::metadata(path) {
        Ok(meta) => meta,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return json!({ "sha256": Value::Null, "missing": true })
        }
        Err(_) => return unknowable_digest(),
    };
    // WHAT IT IS, from the stat already taken, and before the open — a FIFO's length is
    // 0, so the bound below waves it through and `File::open` then blocks forever on
    // the core's stdout pump (`refuse_non_regular_file`). Folded into the ordinary
    // "can't tell" answer, like the floor above, because this method never fails a
    // batch — and "Addison can't tell whether this changed" is the true thing to say
    // about a pipe anyway.
    if refuse_non_regular_file(&meta).is_err() {
        return unknowable_digest();
    }
    if meta.len() > DIGEST_SIZE_BOUND {
        return unknowable_digest();
    }
    let file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return json!({ "sha256": Value::Null, "missing": true })
        }
        Err(_) => return unknowable_digest(),
    };
    // BOUND + 1 and a fixed buffer: a file that grows between the metadata call and
    // this read cannot cost this process more than one chunk, and one that crosses the
    // bound while being read is answered "can't tell" rather than hashed in part — a
    // hash of half a file is not a wrong answer, it is a confident one.
    let mut reader = file.take(DIGEST_SIZE_BOUND + 1);
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    let mut total: u64 = 0;
    loop {
        match reader.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => {
                total += read as u64;
                if total > DIGEST_SIZE_BOUND {
                    return unknowable_digest();
                }
                hasher.update(&buffer[..read]);
            }
            Err(_) => return unknowable_digest(),
        }
    }
    let mut hex = String::with_capacity(64);
    for byte in hasher.finalize() {
        let _ = write!(hex, "{byte:02x}");
    }
    json!({ "sha256": hex, "missing": false })
}

/// How many entries one delete preview may walk before it stops counting
/// (`shell.previewDeletePaths`).
///
/// CAPPED HERE for `MAX_DIR_ENTRIES`'s reason, plus one of its own: this walk is
/// awaited INLINE on the core's stdout pump while somebody is waiting to read a
/// permission card. A `node_modules` tree or a home directory would hold every frame
/// in the app for as long as the disk took, and the card is worth exactly one extra
/// line, never a pause. Hitting the cap is not a failure: the answer comes back with
/// `capped` true and the core says "more than N files", which is a true sentence and
/// an honest one.
const MAX_PREVIEW_ENTRIES: u64 = 5_000;

/// How recent "changed in the last day" is. A rolling 24 hours rather than local
/// midnight, because a rolling window needs no timezone and no calendar, and the
/// sentence it produces is true either way.
const RECENTLY_CHANGED_SECONDS: u64 = 24 * 60 * 60;

// shell.previewDeletePaths {paths} -> {files, directories, modifiedToday, missing, capped}
//
// THE DELETE PREVIEW (5.6, first form). Counts what sits under paths a command named,
// so the permission card for that command can say what it would cost. It runs NOTHING,
// copies nothing and opens no file: it reads directory entries and their metadata.
//
// The core decided these paths are a delete's targets (`agent_core/delete_preview.py`)
// and it fails towards silence, so a wrong answer here is worse than no answer: this
// handler never guesses either. A path it cannot see is counted as missing, and the
// walk stops at `MAX_PREVIEW_ENTRIES` with `capped` set rather than running long.
fn preview_delete_paths(params: &Value) -> Result<Value, RpcError> {
    let paths = params
        .get("paths")
        .and_then(Value::as_array)
        .ok_or_else(|| RpcError::app("A list of paths is required."))?;
    refuse_oversize_batch(paths)?;

    let mut files: u64 = 0;
    let mut directories: u64 = 0;
    let mut recent: u64 = 0;
    let mut missing: u64 = 0;
    let mut capped = false;
    let now = std::time::SystemTime::now();

    for entry in paths {
        let Some(text) = entry.as_str() else { continue };
        let path = PathBuf::from(text);
        // The floor, first: Addison's own memory is not a place this answers questions
        // about either. Folded into `missing` rather than raised, a card is not the
        // place to explain a refusal, and the line is dropped entirely when nothing
        // could be counted.
        if refuse_addison_data_dir(&path).is_err() {
            missing += 1;
            continue;
        }
        let mut stack = vec![path];
        while let Some(current) = stack.pop() {
            if files + directories >= MAX_PREVIEW_ENTRIES {
                capped = true;
                break;
            }
            // `symlink_metadata`, NEVER `metadata`, for `list_workspace_path`'s reason
            // and a sharper one: `rm` removes a link, it does not walk into it, so
            // counting the target's tree would report a number the delete would never
            // touch, and a link into `/` would walk the disk.
            let meta = match std::fs::symlink_metadata(&current) {
                Ok(meta) => meta,
                Err(_) => {
                    missing += 1;
                    continue;
                }
            };
            if meta.is_dir() {
                directories += 1;
                if let Ok(reader) = std::fs::read_dir(&current) {
                    for child in reader.flatten() {
                        stack.push(child.path());
                    }
                }
                continue;
            }
            files += 1;
            if let Ok(modified) = meta.modified() {
                if now
                    .duration_since(modified)
                    .map(|age| age.as_secs() < RECENTLY_CHANGED_SECONDS)
                    .unwrap_or(true)
                {
                    recent += 1;
                }
            }
        }
    }

    Ok(json!({
        "files": files,
        "directories": directories,
        "modifiedToday": recent,
        "missing": missing,
        "capped": capped,
    }))
}

/// "Addison can't tell" — the answer for every file this cannot judge, whatever the
/// reason. Worded once so a caller can never learn WHICH reason from the shape.
fn unknowable_digest() -> Value {
    json!({ "sha256": Value::Null, "missing": false })
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

/// How many symlink hops the floor follows before it stops guessing and refuses.
///
/// `read_link` reads exactly ONE hop, and one hop was the whole of the previous fix.
/// It closed `a -> <data dir>/x` and left `a -> b -> <data dir>/x` wide open: with `b`
/// dangling, `canonical_lossy` stops at the nearest existing ancestor for BOTH the
/// candidate and the one target it read, so both looked like harmless project files
/// while `fs::write` followed the whole chain and planted a file in the G3 sidecar
/// directory. A fixed number of hops is not a property; the CHAIN is.
///
/// FORTY, because that is the largest number of hops any kernel this app runs on will
/// follow: Linux's `MAXSYMLINKS` is 40, macOS's is 32, and POSIX only promises 8. The
/// floor must never stop resolving somewhere the kernel would keep going — that gap IS
/// this bug, one link further along — so the bound is the highest of them, not the
/// lowest. Past 40 the kernel itself answers `ELOOP`, so the write this refuses would
/// have failed anyway; refusing it here costs nothing real and says so honestly.
///
/// A chain of EXACTLY 40 links spends the budget and is refused rather than resolved,
/// which is an off-by-one on the safe side and stated rather than tuned away: this
/// walk either resolves a chain completely or refuses it, and it never returns "safe"
/// about a chain it has not finished reading. That is the only property that matters,
/// and nothing a person or a project has ever built comes near forty.
///
/// A LOOP IS BOUNDED BY THE SAME COUNTER and needs no visited-set: `a -> b -> a`
/// simply spends the budget. What must never happen is that this walk runs forever on
/// the core's stdout pump, and a counter is the smallest thing that guarantees it.
const MAX_SYMLINK_HOPS: usize = 40;

/// Said when a path's symlink chain is longer than the floor will follow. Plain, and
/// it does not pretend to know where the chain leads — because it does not.
const UNRESOLVABLE_LINK: &str =
    "Addison couldn't work out where that shortcut leads, so it left it alone.";

/// Every location `path` could actually land on: the path itself, and each target
/// along its symlink chain, all canonicalized. `Err(())` when the chain is still
/// unresolved after `MAX_SYMLINK_HOPS` — a loop, or a chain past what any kernel here
/// would follow.
///
/// WHY THE WHOLE CHAIN AND NOT THE END OF IT. `fs::canonicalize` already resolves a
/// chain whose end EXISTS, and `canonical_lossy` falls back to the nearest existing
/// ancestor when it does not — which is exactly the dangling case, and exactly where a
/// not-yet-created file under Addison's data dir hides. So the intermediate hops have
/// to be judged one at a time; only the walk sees them.
fn link_chain(path: &Path) -> Result<Vec<PathBuf>, ()> {
    let mut landing_places = vec![canonical_lossy(path)];
    let mut cursor = path.to_path_buf();
    for _ in 0..MAX_SYMLINK_HOPS {
        // Not a link, or a link the OS will not read: either way the chain ends here,
        // and what we have collected is every place it could reach.
        let Ok(target) = std::fs::read_link(&cursor) else {
            return Ok(landing_places);
        };
        let resolved = if target.is_absolute() {
            target
        } else {
            cursor.parent().unwrap_or(Path::new("")).join(target)
        };
        landing_places.push(canonical_lossy(&resolved));
        cursor = resolved;
    }
    Err(())
}

fn refuse_addison_data_dir(path: &Path) -> Result<(), RpcError> {
    let refused = || {
        Err(RpcError::app(
            "That location holds Addison's own memory, so Addison won't touch it there.",
        ))
    };
    // A DANGLING symlink resolves to nothing, so canonicalization stops at the link
    // itself and the containment test judges the link's own harmless location. But
    // `std::fs::write` FOLLOWS the link and creates the file at its target — so a link
    // inside a trusted project, pointing at a not-yet-existing file under Addison's
    // data dir, planted a file in the G3 sidecar directory while this check said yes.
    // Every hop of the chain is therefore judged, not just the first one.
    let Ok(landing_places) = link_chain(path) else {
        // A chain we cannot see the end of is not a chain we may declare safe. This
        // sits on the refusing side of the only question this function answers.
        return Err(RpcError::app(UNRESOLVABLE_LINK));
    };
    for dir in addison_data_dirs() {
        let protected = canonical_lossy(&dir);
        for candidate in &landing_places {
            // Refuse a path that IS, sits inside, or contains a protected directory.
            if candidate.starts_with(&protected) || protected.starts_with(candidate) {
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

    /// Run `f` on its own thread and give it two seconds to answer.
    ///
    /// EVERY WEDGE IN THIS FILE FAILS BY NOT FAILING. A guard that does not hold does
    /// not return the wrong answer — it returns NO answer, forever, because that is
    /// what `fs::read` on a FIFO and an unbounded walk of a symlink loop both do. A
    /// plain `assert!` cannot catch that: the test never reaches it. So the deadline
    /// has to belong to the test, exactly as `a_long_command_does_not_block_the_next_
    /// request` puts the assertion on the clock rather than on a message.
    ///
    /// Two seconds is generous for work that should take one `stat`. A thread left
    /// blocked outlives the test; that is the correct trade for a suite that reports
    /// the failure instead of hanging with it.
    fn within_two_seconds<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(f());
        });
        rx.recv_timeout(std::time::Duration::from_secs(2)).expect(
            "this call never returned — the guard that refuses it before the open is \
             missing, and that is the permanent stall of the core's stdout pump",
        )
    }

    /// A named pipe at a fresh temp path, or `None` when the platform has no `mkfifo`
    /// on PATH.
    ///
    /// SPAWNED RATHER THAN LINKED. `nix`/`libc` are not dependencies of this crate and
    /// adding one to the trusted process to make a test fixture would be a poor trade;
    /// `mkfifo(1)` is POSIX and present on both platforms this repo builds on. No
    /// `#[cfg]` guard, because this test module is ALREADY unix-only — it plants
    /// symlinks with `std::os::unix::fs::symlink` in five places without one, and
    /// guarding this one alone would imply the others are portable.
    fn make_fifo() -> Option<PathBuf> {
        let path = temp_path().with_extension("fifo");
        let made = std::process::Command::new("mkfifo")
            .arg(&path)
            .status()
            .ok()
            .is_some_and(|status| status.success());
        made.then_some(path)
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
    fn write_workspace_puts_back_a_trailing_newline_the_edit_dropped() {
        // KNOWN-BUGS P3 #7. A model asked to append a line hands back the whole file
        // with the new line last and no `\n` after it, so the NEXT thing appended
        // fuses onto it — `edited: yesmy own edit`. The file had the byte; this edit
        // lost it; the write puts it back and says it did, because the core hashes
        // what landed.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "one\ntwo\n").expect("seed");

        let result = write_workspace_path(&state, path.clone(), "one\ntwo\nthree").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "one\ntwo\nthree\n");
        assert_eq!(result.get("newlineRestored").and_then(Value::as_bool), Some(true));
        // The PRIOR is what was there, untouched by any of this — undo is exact.
        assert_eq!(result.get("prior").and_then(Value::as_str), Some("one\ntwo\n"));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn write_workspace_never_invents_a_trailing_newline() {
        // The other half of the rule, and the reason it is not "always end with \n":
        // a file deliberately kept without one must not acquire one because Addison
        // rewrote it, a NEW file is the model's to shape, and truncating to empty is
        // a deliberate act with a deliberate result. Each case is one mutation of
        // `needs_trailing_newline`.
        let state = FileState::default();

        let had_none = temp_path();
        std::fs::write(&had_none, "no newline").expect("seed");
        let result = write_workspace_path(&state, had_none.clone(), "still none").unwrap();
        assert_eq!(std::fs::read_to_string(&had_none).unwrap(), "still none");
        assert_eq!(result.get("newlineRestored").and_then(Value::as_bool), Some(false));

        let fresh = temp_path();
        write_workspace_path(&state, fresh.clone(), "brand new").unwrap();
        assert_eq!(std::fs::read_to_string(&fresh).unwrap(), "brand new");

        let emptied = temp_path();
        std::fs::write(&emptied, "gone soon\n").expect("seed");
        write_workspace_path(&state, emptied.clone(), "").unwrap();
        assert_eq!(std::fs::read_to_string(&emptied).unwrap(), "");

        let already = temp_path();
        std::fs::write(&already, "a\n").expect("seed");
        let result = write_workspace_path(&state, already.clone(), "a\nb\n").unwrap();
        assert_eq!(std::fs::read_to_string(&already).unwrap(), "a\nb\n", "no second newline");
        assert_eq!(result.get("newlineRestored").and_then(Value::as_bool), Some(false));

        for path in [had_none, fresh, emptied, already] {
            let _ = std::fs::remove_file(&path);
        }
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
        //
        // The DIGEST (`digest_workspace_path`) is the fifth, and it is why the size
        // marker became a parameter rather than the literal `stat_on_disk` this loop
        // used to assume: that path asks `fs::metadata` directly, because it has to tell
        // a file that is NOT THERE from one the OS will not describe and `stat_on_disk`
        // collapses both into `None`. Same judgement, same order, one fewer helper in
        // the way — and the loop now says which spelling it is looking for instead of
        // silently requiring one of them.
        //
        // WHAT IT IS is pinned in the same place and for a sharper reason. A size
        // ceiling judged early is a stall avoided; a KIND judged early is the only
        // thing between this process and a permanent one, because `fs::read` and
        // `File::open` on a FIFO never return at all and the size ceiling cannot see it
        // (`len()` on a pipe is 0). No runtime assertion can tell an early check from a
        // late one — a late one HANGS rather than failing — so the order is pinned here.
        let source = include_str!("filesystem.rs");
        for (name, sized_with, opens_with) in [
            ("fn read_workspace_path", "stat_on_disk", "std::fs::read("),
            ("fn capture_prior_text", "stat_on_disk", "std::fs::read("),
            ("fn read_scoped_handle", "stat_on_disk", "std::fs::read("),
            ("fn read_workspace_view", "stat_on_disk", "std::fs::File::open("),
            ("fn digest_workspace_path", "std::fs::metadata(", "std::fs::File::open("),
        ] {
            let start = source.find(name).unwrap_or_else(|| panic!("{name} must exist"));
            let rest = &source[start..];
            let end = rest.find("\n}\n").unwrap_or_else(|| panic!("{name} must be closed"));
            let body = &rest[..end];

            let sized = body
                .find(sized_with)
                .unwrap_or_else(|| panic!("{name} must ask the file's size:\n{body}"));
            let read = body
                .find(opens_with)
                .unwrap_or_else(|| panic!("{name} must read the file ({opens_with}):\n{body}"));
            assert!(
                sized < read,
                "{name} must judge the size BEFORE reading the bytes:\n{body}"
            );

            let kind = body.find("refuse_non_regular_file(").unwrap_or_else(|| {
                panic!("{name} must refuse a non-regular file — a FIFO's length is 0, \
                        so no size ceiling stops it and the open never returns:\n{body}")
            });
            assert!(
                kind < read,
                "{name} must judge WHAT the path is BEFORE opening it:\n{body}"
            );
        }

        // THE WRITE-BACK is the sixth path and it is pinned apart from the loop, because
        // it has no size to judge: the bytes come from an undo payload the core already
        // bounded, so there is no ceiling here to order. The KIND question is the same
        // question and it is the whole of what this path needs — `fs::write` opens
        // `O_WRONLY`, which on a FIFO waits for a reader instead of for data, and this
        // one is reachable from a click rather than from a tool.
        let start = source
            .find("fn restore_workspace_path")
            .expect("fn restore_workspace_path must exist");
        let rest = &source[start..];
        let end = rest.find("\n}\n").expect("fn restore_workspace_path must be closed");
        let body = &rest[..end];
        let kind = body.find("refuse_non_regular_file(").unwrap_or_else(|| {
            panic!("restore_workspace_path must refuse a non-regular file — writing to a \
                    FIFO blocks in the open, and this handler is awaited on the pump:\n{body}")
        });
        let write = body
            .find("std::fs::write(")
            .expect("restore_workspace_path must write the file");
        assert!(
            kind < write,
            "restore_workspace_path must judge WHAT the path is BEFORE writing it:\n{body}"
        );
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
    fn the_viewer_refuses_a_shortcut_and_never_shows_what_it_points_at() {
        // THE SHELL'S OWN HALF of the shortcut gap (KNOWN-GAPS). The listing has said
        // `kind: "symlink"` since it shipped, but the VIEWER opened whatever the name
        // reached: point one at `~/.ssh/id_rsa` and the private key came back as the
        // pane's text. The core refuses first for the shipped caller; this is the floor
        // underneath a caller that never asked it.
        //
        // The assertion that matters is the second one: the refusal is not merely an
        // error, it is an error INSTEAD OF the target's bytes.
        let dir = temp_dir_path();
        let secret = dir.join("id_rsa");
        std::fs::write(&secret, "PRIVATE KEY").expect("seed the target");
        let link = dir.join("notes.txt");
        std::os::unix::fs::symlink(&secret, &link).expect("plant the shortcut");

        let err = read_workspace_view(&link).unwrap_err();
        assert_eq!(err.message, "That name is a shortcut to somewhere else, so Addison won't follow it.");

        // And an ordinary file in the same folder still reads, so this refuses the
        // shortcut and not the surface.
        let plain = dir.join("plain.txt");
        std::fs::write(&plain, "hello").expect("seed a plain file");
        let result = read_workspace_view(&plain).unwrap();
        assert_eq!(result.get("content").and_then(Value::as_str), Some("hello"));

        let _ = std::fs::remove_dir_all(&dir);
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
    fn restore_workspace_refuses_a_ledgered_path_that_became_a_shortcut() {
        // THE SHELL'S OWN HALF of the shortcut gap (KNOWN-GAPS). The ledger is checked
        // against the NAME and `fs::write` FOLLOWS a link, so a ledgered path swapped for
        // a shortcut put the prior text into whatever it reached — the private key here,
        // and the file need not be under the data dir for that to be the harm, so the
        // floor above does not catch it.
        //
        // Delete `refuse_shortcut_at_path` from `restore_workspace_path` and the second
        // assertion fails with the target holding "PLANTED".
        let state = FileState::default();
        let dir = temp_dir_path();
        let secret = dir.join("id_rsa");
        std::fs::write(&secret, "PRIVATE KEY").expect("seed the target");
        let ledgered = dir.join("edited.txt");
        std::os::unix::fs::symlink(&secret, &ledgered).expect("plant the shortcut");
        // The ledger says yes: this is a name Addison wrote this session.
        lock(&state.workspace_written).insert(ledgered.clone());

        let params = json!({ "path": ledgered.to_string_lossy(), "content": "PLANTED" });
        let err = restore_workspace_path(&state, ledgered.clone(), &params).unwrap_err();
        assert_eq!(err.message, "That name is a shortcut to somewhere else, so Addison won't follow it.");
        assert_eq!(
            std::fs::read_to_string(&secret).unwrap(),
            "PRIVATE KEY",
            "an undo must not write through a shortcut"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn restore_workspace_refuses_to_delete_a_ledgered_path_that_became_a_shortcut() {
        // THE OTHER BRANCH. `remove_file` does not follow the link, so nothing is
        // written anywhere — but a shortcut somebody else put at that name is not the
        // file the write created, and taking it away is not an undo of anything. Exactly
        // the reasoning `refuse_non_regular_file` already applies to both branches, for a
        // kind that stat cannot report because stat follows it.
        let state = FileState::default();
        let dir = temp_dir_path();
        let target = dir.join("kept.txt");
        std::fs::write(&target, "somebody else's file").expect("seed the target");
        let ledgered = dir.join("created.txt");
        std::os::unix::fs::symlink(&target, &ledgered).expect("plant the shortcut");
        lock(&state.workspace_written).insert(ledgered.clone());

        let params = json!({ "path": ledgered.to_string_lossy(), "delete": true });
        let err = restore_workspace_path(&state, ledgered.clone(), &params).unwrap_err();
        assert_eq!(err.message, "That name is a shortcut to somewhere else, so Addison won't follow it.");
        assert!(
            std::fs::symlink_metadata(&ledgered).is_ok(),
            "an undo must not remove a shortcut it did not create"
        );
        assert!(target.exists());

        let _ = std::fs::remove_dir_all(&dir);
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

    /// A data dir, a project dir, and `ADDISON_DB_PATH` pointed at the first — the
    /// fixture every symlink test below plants its links in. Returns both paths and a
    /// restore for the environment variable, so each test says what it PLANTS rather
    /// than repeating fifteen lines of setup around it.
    fn data_dir_and_project(tag: &str) -> (PathBuf, PathBuf, Option<String>) {
        let data_dir = std::env::temp_dir().join(format!("addison-{tag}-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(data_dir.join("snapshots")).expect("seed data dir");
        let project = std::env::temp_dir().join(format!("addison-proj-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&project).expect("seed project");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", data_dir.join("addison.sqlite3"));
        (data_dir, project, prev)
    }

    fn restore_db_path(prev: Option<String>) {
        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
    }

    #[test]
    fn write_workspace_refuses_a_two_hop_dangling_symlink_into_the_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // THE FIX ABOVE CLOSED ONE HOP AND CLAIMED THE CLASS. It did not: `read_link`
        // reads exactly one link, so with `a -> b` and `b -> <data dir>/planted.json`
        // (b dangling) BOTH the candidate and the single target canonicalized to
        // harmless project paths, the containment loop found nothing — and `fs::write`
        // followed the whole chain and planted the file anyway. Shorten
        // `MAX_SYMLINK_HOPS` to 1 and this test writes into `snapshots/`.
        let state = FileState::default();
        let (data_dir, project, prev) = data_dir_and_project("dang2");

        let victim = data_dir.join("snapshots").join("planted.json");
        let hop_b = project.join("b.txt");
        let hop_a = project.join("a.txt");
        std::os::unix::fs::symlink(&victim, &hop_b).expect("plant the second hop");
        std::os::unix::fs::symlink(&hop_b, &hop_a).expect("plant the first hop");

        let err = write_workspace_path(&state, hop_a.clone(), "PLANTED").unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!victim.exists(), "a two-hop chain must not plant a file in the data dir");

        restore_db_path(prev);
        let _ = std::fs::remove_dir_all(&data_dir);
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn write_workspace_refuses_a_long_dangling_symlink_chain_into_the_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // TWO IS NOT THE CLASS EITHER, which is the lesson the one-hop fix taught: any
        // bound short of the chain is a bound an attacker adds one link to. Eight hops
        // is POSIX's own minimum `SYMLOOP_MAX` — the shortest chain every kernel is
        // guaranteed to follow, so the shortest one that is definitely exploitable.
        let state = FileState::default();
        let (data_dir, project, prev) = data_dir_and_project("dangN");

        let victim = data_dir.join("snapshots").join("planted.json");
        let mut previous = victim.clone();
        for hop in 0..8 {
            let link = project.join(format!("hop{hop}.txt"));
            std::os::unix::fs::symlink(&previous, &link).expect("plant a hop");
            previous = link;
        }

        let err = write_workspace_path(&state, previous.clone(), "PLANTED").unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!victim.exists(), "an eight-hop chain must not plant a file in the data dir");

        restore_db_path(prev);
        let _ = std::fs::remove_dir_all(&data_dir);
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn a_symlink_loop_is_refused_rather_than_walked_forever() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // THE COST OF WALKING THE CHAIN, paid for here. A `a -> b -> a` loop is what
        // turns "follow every hop" into a handler that never returns — and this handler
        // is awaited INLINE on the core's stdout pump, so never returning is the
        // permanent wedge, not a slow answer. `MAX_SYMLINK_HOPS` is what stops it;
        // delete the bound (a `while let` over `read_link`) and this test HANGS rather
        // than failing, which is the bug faithfully reproduced.
        //
        // And the answer is a REFUSAL, not a shrug: a chain whose end we cannot see is
        // not a chain we may declare safe.
        let state = FileState::default();
        let (data_dir, project, prev) = data_dir_and_project("loop");

        let a = project.join("a.txt");
        let b = project.join("b.txt");
        std::os::unix::fs::symlink(&b, &a).expect("plant a -> b");
        std::os::unix::fs::symlink(&a, &b).expect("plant b -> a");

        let answered = within_two_seconds(move || write_workspace_path(&state, a, "PLANTED"));
        let err = answered.unwrap_err();
        assert_eq!(
            err.message,
            "Addison couldn't work out where that shortcut leads, so it left it alone."
        );

        restore_db_path(prev);
        let _ = std::fs::remove_dir_all(&data_dir);
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn an_ordinary_file_behind_a_short_chain_is_still_written() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // THE HALF THAT KEEPS THE THREE ABOVE HONEST. A floor that refused every
        // symlink would pass all of them and break the ordinary case a coding harness
        // meets constantly — a `src/config.json -> ../config.json` inside a project.
        // The chain resolves, it lands nowhere protected, and the write goes through
        // the link to the file at its end.
        let state = FileState::default();
        let (data_dir, project, prev) = data_dir_and_project("chain-ok");

        let real = project.join("real.txt");
        std::fs::write(&real, "before").expect("seed the real file");
        let middle = project.join("middle.txt");
        let entry = project.join("entry.txt");
        std::os::unix::fs::symlink(&real, &middle).expect("plant the second hop");
        std::os::unix::fs::symlink(&middle, &entry).expect("plant the first hop");

        write_workspace_path(&state, entry, "after").expect("an ordinary chain must be written");
        assert_eq!(std::fs::read_to_string(&real).unwrap(), "after");

        restore_db_path(prev);
        let _ = std::fs::remove_dir_all(&data_dir);
        let _ = std::fs::remove_dir_all(&project);
    }

    #[test]
    fn restore_workspace_refuses_a_ledgered_path_that_became_a_link_into_the_data_dir() {
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        // THE ONE WRITE PATH WITH NO FLOOR. It relied entirely on the session ledger,
        // on the reasoning that a path only gets into that ledger by passing the floor
        // once — but the two checks happen at different MOMENTS and `fs::write` follows
        // links, so a ledgered file swapped for a link into the data dir wrote straight
        // through it. `workspace.revertFile` made that reachable from a click.
        //
        // Planted as TWO hops, so this also pins that restore gets the chain walk and
        // not merely a copy of the old one-hop check. Delete the
        // `refuse_addison_data_dir` call in `restore_workspace_path` and this plants a
        // file in `snapshots/`.
        let state = FileState::default();
        let (data_dir, project, prev) = data_dir_and_project("revert");

        let victim = data_dir.join("snapshots").join("planted.json");
        let hop_b = project.join("b.txt");
        let ledgered = project.join("edited.txt");
        std::os::unix::fs::symlink(&victim, &hop_b).expect("plant the second hop");
        std::os::unix::fs::symlink(&hop_b, &ledgered).expect("plant the first hop");
        // The ledger says yes: this is a path Addison wrote this session.
        lock(&state.workspace_written).insert(ledgered.clone());

        let params = json!({ "path": ledgered.to_string_lossy(), "content": "PLANTED" });
        let err = restore_workspace_path(&state, ledgered, &params).unwrap_err();
        assert_eq!(
            err.message,
            "That location holds Addison's own memory, so Addison won't touch it there."
        );
        assert!(!victim.exists(), "undo must not plant a file in the data dir");

        restore_db_path(prev);
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

    // --- The review surface's revert half (phase-3 plan Build §2/§3) -------------

    #[test]
    fn can_restore_answers_the_ledger_and_only_the_ledger() {
        // THE WHOLE POINT of this method: it says what `restore_workspace_path` WOULD
        // say, without doing anything. A path this session wrote is restorable; one it
        // did not is not — and after a restart the ledger is empty, which is the case
        // the surface has to render honestly instead of offering a button that fails.
        let state = FileState::default();
        let ledgered = temp_path();
        let stranger = temp_path();
        lock(&state.workspace_written).insert(ledgered.clone());

        let params = json!({ "paths": [ledgered.to_string_lossy(), stranger.to_string_lossy()] });
        let answer = can_restore_workspace_paths(&state, &params).unwrap();
        let restorable = answer.get("restorable").and_then(Value::as_object).expect("a map");
        assert_eq!(restorable.get(&ledgered.to_string_lossy().to_string()), Some(&json!(true)));
        assert_eq!(restorable.get(&stranger.to_string_lossy().to_string()), Some(&json!(false)));
        // KEYED BY PATH, never positional: an array would couple the answer to an order
        // two processes must agree on, and disagreeing is silent in both directions.
        assert_eq!(restorable.len(), 2);
    }

    #[test]
    fn can_restore_never_touches_the_filesystem() {
        // A PURE QUERY. Neither path below exists on disk at all: the ledgered one
        // still answers true and the other still answers false, which no implementation
        // that stats or opens anything could do. Add a `path.exists()` to the handler
        // and the first assertion fails.
        let state = FileState::default();
        let ledgered_but_absent = temp_path();
        let absent = temp_path();
        lock(&state.workspace_written).insert(ledgered_but_absent.clone());
        assert!(!ledgered_but_absent.exists() && !absent.exists());

        let params =
            json!({ "paths": [ledgered_but_absent.to_string_lossy(), absent.to_string_lossy()] });
        let answer = can_restore_workspace_paths(&state, &params).unwrap();
        let restorable = answer.get("restorable").and_then(Value::as_object).expect("a map");
        assert_eq!(
            restorable.get(&ledgered_but_absent.to_string_lossy().to_string()),
            Some(&json!(true)),
            "a file Addison created and the person then deleted is still restorable"
        );
        assert_eq!(restorable.get(&absent.to_string_lossy().to_string()), Some(&json!(false)));
    }

    /// SHA-256 of `"hello\n"`, written out rather than computed, for the reason
    /// `digest_hashes_what_is_on_disk_now` gives: a test that hashes with the library
    /// it is testing asserts only that the library is deterministic.
    const HELLO_SHA: &str = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03";

    #[test]
    fn adopt_re_ledgers_a_path_whose_bytes_are_what_addison_wrote() {
        // THE POST-RESTART CASE, recovered. The ledger starts empty exactly as it does
        // after a restart, so `restore_workspace_path` refuses; adopting on a matching
        // digest puts the one path back in, and the restore then works.
        let state = FileState::default();
        let path = temp_path();
        std::fs::write(&path, "hello\n").expect("seed");
        assert!(restore_workspace_path(
            &state,
            path.clone(),
            &json!({ "path": path.to_string_lossy(), "content": "before\n" })
        )
        .is_err());

        let params = json!({ "path": path.to_string_lossy(), "expectedSha256": HELLO_SHA });
        let answer = adopt_workspace_path_in(&state, &params).unwrap();
        assert_eq!(answer.get("adopted"), Some(&json!(true)));
        restore_workspace_path(
            &state,
            path.clone(),
            &json!({ "path": path.to_string_lossy(), "content": "before\n" }),
        )
        .unwrap();
        assert_eq!(std::fs::read_to_string(&path).expect("read"), "before\n");
    }

    #[test]
    fn adopt_refuses_bytes_that_are_not_what_addison_wrote() {
        // Kills: adopting on the path alone, or on any test weaker than the digest,
        // which is the whole of what makes this narrower than persisting the ledger.
        // A file somebody has edited since is exactly the file a revert must not
        // silently overwrite.
        let state = FileState::default();
        let edited = temp_path();
        std::fs::write(&edited, "somebody's own work\n").expect("seed");
        let params = json!({ "path": edited.to_string_lossy(), "expectedSha256": HELLO_SHA });
        assert_eq!(
            adopt_workspace_path_in(&state, &params).unwrap().get("adopted"),
            Some(&json!(false))
        );
        assert!(!lock(&state.workspace_written).contains(&edited), "and nothing is ledgered");

        // A file that is not there at all cannot answer for itself either.
        let gone = temp_path();
        let params = json!({ "path": gone.to_string_lossy(), "expectedSha256": HELLO_SHA });
        assert_eq!(
            adopt_workspace_path_in(&state, &params).unwrap().get("adopted"),
            Some(&json!(false))
        );
    }

    #[cfg(unix)]
    #[test]
    fn adopt_refuses_a_shortcut_standing_at_the_name() {
        // Kills: hashing through the link. The bytes at the far end are a perfect match
        //, that is what makes this the dangerous case, and adopting on them would
        // ledger a NAME that every later write follows straight through to a file
        // nobody trusted.
        let state = FileState::default();
        let target = temp_path();
        std::fs::write(&target, "hello\n").expect("seed");
        let link = temp_path();
        std::os::unix::fs::symlink(&target, &link).expect("link");

        let params = json!({ "path": link.to_string_lossy(), "expectedSha256": HELLO_SHA });
        assert_eq!(
            adopt_workspace_path_in(&state, &params).unwrap().get("adopted"),
            Some(&json!(false))
        );
        assert!(!lock(&state.workspace_written).contains(&link));
    }

    #[test]
    fn digest_hashes_what_is_on_disk_now() {
        // The digest the core compares against `wrote_sha256`. The literal below is the
        // SHA-256 of "hello\n" — written out rather than computed here, because a test
        // that hashes the content with the same library it is testing asserts only that
        // the library is deterministic.
        let path = temp_path();
        std::fs::write(&path, "hello\n").expect("seed");

        let answer = digest_workspace_path(&path);
        assert_eq!(
            answer.get("sha256").and_then(Value::as_str),
            Some("5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03")
        );
        assert_eq!(answer.get("missing").and_then(Value::as_bool), Some(false));

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn digest_says_missing_for_a_file_that_is_gone_and_cannot_tell_for_one_too_big() {
        // Three answers, not two, and the surface says a different sentence for each.
        // A file that is GONE is a fact (Revert can still put it back); a file too big
        // to judge is a warning to withhold, and it is `null` rather than a guess.
        let gone = temp_path();
        let missing = digest_workspace_path(&gone);
        assert_eq!(missing.get("missing").and_then(Value::as_bool), Some(true));
        assert!(missing.get("sha256").expect("present").is_null());

        let big = temp_path();
        std::fs::write(&big, "a".repeat(DIGEST_SIZE_BOUND as usize + 1)).expect("seed oversize");
        let over = digest_workspace_path(&big);
        assert!(
            over.get("sha256").expect("present").is_null(),
            "a file past the bound is unjudgeable, never hashed in part"
        );
        assert_eq!(over.get("missing").and_then(Value::as_bool), Some(false));

        // And exactly AT the bound it is still answered — the half that keeps the
        // ceiling from silently becoming "never answers for anything real".
        let at_bound = temp_path();
        std::fs::write(&at_bound, "a".repeat(DIGEST_SIZE_BOUND as usize)).expect("seed at bound");
        let judged = digest_workspace_path(&at_bound);
        assert!(judged.get("sha256").and_then(Value::as_str).is_some());

        let _ = std::fs::remove_file(&big);
        let _ = std::fs::remove_file(&at_bound);
    }

    #[test]
    fn digest_refuses_the_addison_data_dir_as_an_ordinary_cannot_tell() {
        // The shell's own floor holds here too — and folds into the "can't tell"
        // answer rather than raising, because this method must never fail a batch:
        // one unjudgeable file among two hundred cannot take the rest off the screen.
        let _env = DATA_DIR_ENV.lock().unwrap_or_else(|e| e.into_inner());
        let data_dir = std::env::temp_dir().join(format!("addison-dg-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&data_dir).expect("seed data dir");
        let secret = data_dir.join("addison.sqlite3");
        std::fs::write(&secret, "secret db bytes").expect("seed db");
        let prev = std::env::var("ADDISON_DB_PATH").ok();
        std::env::set_var("ADDISON_DB_PATH", &secret);

        let answer = digest_workspace_path(&secret);
        assert!(answer.get("sha256").expect("present").is_null());
        assert_eq!(answer.get("missing").and_then(Value::as_bool), Some(false));

        match prev {
            Some(v) => std::env::set_var("ADDISON_DB_PATH", v),
            None => std::env::remove_var("ADDISON_DB_PATH"),
        }
        let _ = std::fs::remove_dir_all(&data_dir);
    }

    // --- Nothing in this file may open a path that is not an ordinary FILE ---------

    #[test]
    fn every_read_path_refuses_a_named_pipe_instead_of_blocking_on_it() {
        // THE WEDGE, on every door that opens a file. A FIFO's `metadata().len()` is 0,
        // so every size ceiling in this file passed it — and `fs::read`/`File::open`
        // then block until somebody opens the other end, which nobody ever does. These
        // handlers are awaited INLINE on the core's stdout pump (`agent_process.rs`)
        // and none of them is in `dispatch_off_loop`, so the block is permanent: the
        // pump stops relaying frames of any kind, the core's bridge times out, and the
        // app never comes back. `read_project_file` is shipped, so this was one
        // `mkfifo` inside a trusted root away from any model in OPEN mode; today's
        // `readWorkspaceFileForView` and `digestWorkspaceFiles` are two more doors onto
        // the same thing.
        //
        // Delete any one `refuse_non_regular_file` call and its case here does not go
        // red — it HANGS, and `within_two_seconds` is what turns that back into a
        // failure a person can read.
        let Some(fifo) = make_fifo() else {
            eprintln!("no mkfifo on PATH — skipping the named-pipe refusals");
            return;
        };

        let path = fifo.clone();
        let err = within_two_seconds(move || read_workspace_path(&path)).unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "readWorkspaceFile");

        let path = fifo.clone();
        let err = within_two_seconds(move || read_workspace_view(&path)).unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "readWorkspaceFileForView");

        // The WRITE path is a read path first: it captures the prior text for the undo.
        let path = fifo.clone();
        let err = within_two_seconds(move || {
            write_workspace_path(&FileState::default(), path, "x")
        })
        .unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "writeWorkspaceFile");

        // The PICKED file: a person can type a pipe's name into an OS dialog.
        let path = fifo.clone();
        let err = within_two_seconds(move || {
            let state = FileState::default();
            let handle = uuid::Uuid::new_v4().to_string();
            lock(&state.handles).insert(handle.clone(), path);
            read_scoped_handle(&state, &handle)
        })
        .unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "readScopedFile");

        // The DIGEST never fails a batch, so its refusal is the ordinary "can't tell" —
        // and `missing: false`, because a pipe is not a file that is gone.
        let path = fifo.clone();
        let answer = within_two_seconds(move || digest_workspace_path(&path));
        assert!(answer.get("sha256").expect("present").is_null(), "digestWorkspaceFiles");
        assert_eq!(answer.get("missing").and_then(Value::as_bool), Some(false));

        let _ = std::fs::remove_file(&fifo);
    }

    #[test]
    fn the_undo_write_back_refuses_a_named_pipe_instead_of_blocking_on_it() {
        // THE SIXTH DOOR, and the one that opens for WRITING. The check above went in
        // over the five paths that READ; `fs::write` blocks on a FIFO too, in the `open`
        // rather than in the read — `O_WRONLY` on a pipe waits for a READER, and on a
        // named pipe nobody ever provides one.
        //
        // WORSE THAN THE OTHERS, because it takes no model. `workspace.revertFile` and
        // `undo.undoLastAction` both land here from one click, and everything upstream
        // says yes: the core's `replaced_by_a_link` is `islink` (False for a FIFO) and
        // `canRestoreWorkspaceFiles` answers from the ledger without stating a thing. A
        // `mkfifo` inside a trusted root — which `run_command` can perform in Developer —
        // is the whole of the setup.
        //
        // Delete the `refuse_non_regular_file` call and this does not go red, it HANGS;
        // `within_two_seconds` is what turns that back into a failure a person can read.
        let Some(fifo) = make_fifo() else {
            eprintln!("no mkfifo on PATH — skipping the named-pipe write refusal");
            return;
        };

        let path = fifo.clone();
        let err = within_two_seconds(move || {
            let state = FileState::default();
            lock(&state.workspace_written).insert(path.clone());
            let params = json!({ "path": path.to_string_lossy(), "content": "PUT BACK" });
            restore_workspace_path(&state, path, &params)
        })
        .unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "restoreWorkspaceFile");

        // The DELETE branch does not block — `remove_file` opens nothing — and is refused
        // all the same: a pipe standing at that name is not the file the write created,
        // and removing somebody else's is not an undo of anything.
        let path = fifo.clone();
        let state = FileState::default();
        lock(&state.workspace_written).insert(path.clone());
        let params = json!({ "path": path.to_string_lossy(), "delete": true });
        let err = restore_workspace_path(&state, path, &params).unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "restoreWorkspaceFile delete");
        assert!(fifo.exists(), "and the pipe is still there");

        let _ = std::fs::remove_file(&fifo);
    }

    #[test]
    fn the_undo_write_back_refuses_a_device_node_and_a_directory_but_still_creates_and_deletes() {
        // The other three thirds of the same guard, and the two halves that must NOT be
        // refused — a check that stopped the wedge by refusing everything would take the
        // undo with it.
        let state = FileState::default();

        // A DEVICE NODE. It does not block, it SWALLOWS: `restore_workspace_path`
        // answered `Ok` for `/dev/null`, having put nothing anywhere, and the core then
        // marked the row reverted — a file reported as put back that never was.
        let dev_null = PathBuf::from("/dev/null");
        lock(&state.workspace_written).insert(dev_null.clone());
        let params = json!({ "path": "/dev/null", "content": "PUT BACK" });
        let err = restore_workspace_path(&state, dev_null, &params).unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "/dev/null");

        // A DIRECTORY, which `fs::write` already refused with an errno that mapped to
        // "Addison couldn't undo that file change" — true of everything and saying
        // nothing. This says what is actually the matter.
        let dir = temp_dir_path();
        lock(&state.workspace_written).insert(dir.clone());
        let params = json!({ "path": dir.to_string_lossy(), "content": "PUT BACK" });
        let err = restore_workspace_path(&state, dir.clone(), &params).unwrap_err();
        assert_eq!(err.message, NOT_A_REGULAR_FILE, "a directory");

        // THE CREATE CASE. A write that overwrote a file somebody has since deleted must
        // still put it back — there is nothing at the name to judge, and `stat_on_disk`'s
        // `None` is "cannot judge yet", never "refuse".
        let gone = dir.join("was-deleted.txt");
        lock(&state.workspace_written).insert(gone.clone());
        let params = json!({ "path": gone.to_string_lossy(), "content": "the prior text" });
        restore_workspace_path(&state, gone.clone(), &params).expect("a missing file is created");
        assert_eq!(std::fs::read_to_string(&gone).unwrap(), "the prior text");

        // THE ORDINARY OVERWRITE, and then the DELETE branch on a real file: both
        // untouched by the kind check, or `is_file()` inverted would leave every
        // assertion above green while the undo refused everything.
        let params = json!({ "path": gone.to_string_lossy(), "content": "and again" });
        restore_workspace_path(&state, gone.clone(), &params).expect("an ordinary file is written");
        assert_eq!(std::fs::read_to_string(&gone).unwrap(), "and again");
        let params = json!({ "path": gone.to_string_lossy(), "delete": true });
        restore_workspace_path(&state, gone.clone(), &params).expect("an undone create is removed");
        assert!(!gone.exists());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_directory_is_refused_as_what_it_is_and_an_ordinary_file_is_untouched() {
        // The other two thirds of the same guard. A DIRECTORY was already refused —
        // by `fs::read` failing with an errno that mapped to "Addison couldn't read
        // that file", which is true of everything and says nothing. And an ORDINARY
        // file must still pass, or `is_file()` inverted would leave every test above
        // green while the whole surface refused everything.
        let dir = temp_dir_path();
        assert_eq!(read_workspace_path(&dir).unwrap_err().message, NOT_A_REGULAR_FILE);
        assert_eq!(read_workspace_view(&dir).unwrap_err().message, NOT_A_REGULAR_FILE);
        assert!(digest_workspace_path(&dir).get("sha256").expect("present").is_null());

        let plain = dir.join("ordinary.txt");
        std::fs::write(&plain, "hello").expect("seed");
        assert_eq!(
            read_workspace_path(&plain).unwrap().get("content").and_then(Value::as_str),
            Some("hello"),
            "an ordinary file must be unaffected by the kind check"
        );
        assert!(digest_workspace_path(&plain).get("sha256").expect("present").is_string());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_viewer_never_reports_fewer_bytes_than_it_is_showing() {
        // `bytes` is a claim metadata made BEFORE the file was opened, and the pane
        // renders it as "showing 256 KB of N". When N is smaller than what is on screen
        // the sentence is not merely wrong, it is visibly absurd — "showing 256 KB of
        // 100 bytes" — and that is the kind of wrong that costs a person the whole
        // surface rather than one number.
        //
        // THE RACE ITSELF (a file that grows between the stat and the read) is not
        // reachable from a test, exactly like this file's other post-read backstops.
        // But there is a deterministic instance of the same arithmetic on Linux, which
        // is where CI runs: a `/proc` file is an ORDINARY file whose `len()` is 0 and
        // whose contents are generated at read time. Drop the `.max(…)` and this
        // reports `bytes: 0` beside a screenful of text.
        let generated = Path::new("/proc/self/status");
        let path = if generated.exists() {
            generated.to_path_buf()
        } else {
            // macOS has no /proc. The ordinary case is still asserted — it is the
            // positive control that keeps the rule from being vacuous — and the
            // deterministic kill runs on the Linux gate.
            eprintln!("no /proc on this platform — asserting the ordinary case only");
            let path = temp_path();
            std::fs::write(&path, "0123456789").expect("seed");
            path
        };

        let result = read_workspace_view(&path).unwrap();
        let shown = result.get("content").and_then(Value::as_str).expect("text").len() as u64;
        assert!(shown > 0, "this fixture must actually show something, or it proves nothing");
        assert!(
            result.get("bytes").and_then(Value::as_u64).unwrap() >= shown,
            "`bytes` must never be smaller than what the pane is actually showing"
        );

        if !generated.exists() {
            let _ = std::fs::remove_file(&path);
        }
    }

    // --- Neither batch question may be asked about an unbounded list ---------------

    #[test]
    fn a_batch_at_the_cap_is_answered_and_one_over_it_is_refused() {
        // THE CEILING THAT WAS ONLY A CONVENTION. `digest_workspace_files`'s own doc
        // comment said "up to two hundred files", and 200 was enforced core-side only —
        // so an array of fifty thousand paths cost fifty thousand stats, opens and
        // hashes, all of it awaited INLINE on the core's stdout pump. Every other new
        // surface got a shell-side ceiling for the reason `UNDO_SIZE_BOUND` gives: this
        // is where the bytes are, and the core's number is an input to the boundary,
        // never the boundary.
        //
        // A REFUSAL, not a truncated answer, and the choice matters because truncating
        // would be SAFE: both callers read a map keyed by path and treat a missing key
        // as the cautious answer. That is exactly what makes it wrong — the missing
        // keys would render as "Addison can't tell" beside files it could tell about
        // perfectly, with nothing anywhere saying the question was cut short.
        let state = FileState::default();
        let at_cap: Vec<Value> =
            (0..MAX_BATCH_PATHS).map(|i| json!(format!("/tmp/addison-batch-{i}"))).collect();
        let mut over_cap = at_cap.clone();
        over_cap.push(json!("/tmp/addison-batch-one-too-many"));

        // At the cap both answer, and answer about every path they were given.
        let answered =
            can_restore_workspace_paths(&state, &json!({ "paths": at_cap.clone() })).unwrap();
        assert_eq!(
            answered.get("restorable").and_then(Value::as_object).map(serde_json::Map::len),
            Some(MAX_BATCH_PATHS)
        );
        let answered = digest_workspace_files(&json!({ "paths": at_cap })).unwrap();
        assert_eq!(
            answered.get("digests").and_then(Value::as_object).map(serde_json::Map::len),
            Some(MAX_BATCH_PATHS)
        );

        // One over, and both refuse — with the number named in the sentence, derived
        // from the constant so the two cannot drift.
        for err in [
            can_restore_workspace_paths(&state, &json!({ "paths": over_cap.clone() })).unwrap_err(),
            digest_workspace_files(&json!({ "paths": over_cap })).unwrap_err(),
        ] {
            assert_eq!(err.code, -32000);
            assert_eq!(err.message, "Addison can only look at 200 files at once.");
        }
    }

    // --- The attach path's decode/downscale/encode (image-attach plan §4). All of it
    // runs against `encode_picked_image`, which takes bytes and returns bytes — no
    // Tauri app, no dialog, no file — which is the reason it was factored out.

    /// PNG bytes for a `width` × `height` picture, opaque unless `alpha`.
    ///
    /// GENERATED RATHER THAN CHECKED IN. A fixture photo in the repo would be a
    /// binary blob nobody can review in a diff, and the properties these tests
    /// assert are about SIZE and CHANNELS — both of which are arguments here and
    /// neither of which a reader could tell by looking at a checked-in file.
    ///
    /// The pattern is deliberately not flat: a solid colour compresses to almost
    /// nothing in every format, which would make "did it fit under the bound?"
    /// vacuously true for any input at any size.
    ///
    /// The callers pick sizes JUST past the long edge rather than phone-photo sizes,
    /// and that is a deliberate trade. None of the properties here depend on the
    /// number of pixels — 1700 wide crosses the same branch 4000 wide does — while a
    /// debug build resamples and deflates at a few megapixels per SECOND, so a
    /// realistic fixture bought nothing and cost twenty seconds. A suite people start
    /// skipping is the failure mode that matters.
    fn generated_png(width: u32, height: u32, alpha: bool) -> Vec<u8> {
        let picture = image::RgbaImage::from_fn(width, height, |x, y| {
            let noise = ((x * 7 + y * 13) % 251) as u8;
            image::Rgba([noise, (x % 256) as u8, (y % 256) as u8, if alpha { 128 } else { 255 }])
        });
        let mut buffer = std::io::Cursor::new(Vec::new());
        let source = if alpha {
            image::DynamicImage::ImageRgba8(picture)
        } else {
            image::DynamicImage::ImageRgb8(image::DynamicImage::ImageRgba8(picture).to_rgb8())
        };
        source.write_to(&mut buffer, image::ImageFormat::Png).expect("encode the fixture");
        buffer.into_inner()
    }

    #[test]
    fn a_picture_past_the_long_edge_comes_back_within_it() {
        // THE WHOLE REASON THE SHELL DECODES AT ALL (owner decision 3): a phone photo
        // simply works, because it is downscaled here and never crosses the pump at
        // full size. Delete the resize and this comes back 2400 wide.
        let encoded = encode_picked_image(generated_png(1700, 340, false)).unwrap();
        assert!(
            encoded.width.max(encoded.height) <= IMAGE_LONG_EDGES[0],
            "a picture past the long edge must be downscaled, not sent as it is: {}x{}",
            encoded.width,
            encoded.height
        );
        // The ASPECT RATIO survives, or the thumbnail lies about the picture and the
        // model sees something the person did not.
        assert_eq!(encoded.width, 1600);
        assert_eq!(encoded.height, 320);
        // And the reported dimensions describe THESE bytes, not the original's.
        let sent = image::load_from_memory(&encoded.bytes).expect("what we send must decode");
        assert_eq!((sent.width(), sent.height()), (encoded.width, encoded.height));
    }

    #[test]
    fn a_small_picture_crosses_byte_for_byte() {
        // PASS-THROUGH, and it is asserted as BYTE IDENTITY rather than as "still a
        // PNG": re-encoding a small picture is pure loss, and the most common thing
        // anybody attaches is a screenshot of text, which is exactly what a JPEG
        // round-trip ruins. Under the bound, within the long edge, already one of the
        // four — nothing to gain by touching it.
        let original = generated_png(64, 48, false);
        let encoded = encode_picked_image(original.clone()).unwrap();
        assert_eq!(encoded.bytes, original, "a small picture must cross untouched");
        assert_eq!(encoded.media_type, "image/png");
        assert_eq!((encoded.width, encoded.height), (64, 48));
    }

    #[test]
    fn bytes_that_are_not_a_picture_are_refused_in_plain_language() {
        // DECODING IS THE VALIDATION (plan §4). No magic-byte table, no extension
        // check: what does not parse is refused, and the sentence covers a damaged
        // file, a renamed one and a HEIC the crate cannot read, because Addison
        // genuinely cannot tell them apart and the answer to all three is the same.
        for junk in [
            b"this is not a picture, it is a sentence".to_vec(),
            // A PNG header with nothing behind it — the shape that defeats every
            // check made from the first few bytes rather than from a decode.
            vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0],
            Vec::new(),
        ] {
            let err = encode_picked_image(junk).unwrap_err();
            assert_eq!(err.code, -32000);
            assert_eq!(err.message, NOT_A_PICTURE);
        }
    }

    #[test]
    fn a_picture_with_transparency_comes_back_as_png() {
        // JPEG has no fourth channel, so an alpha picture re-encoded as one comes back
        // with its transparent pixels turned black — a picture that lies about itself.
        // Big enough to force the re-encode, so this measures the ladder's choice and
        // not the pass-through.
        let encoded = encode_picked_image(generated_png(1700, 340, true)).unwrap();
        assert_eq!(encoded.media_type, "image/png", "transparency must survive the re-encode");
        let sent = image::load_from_memory(&encoded.bytes).expect("what we send must decode");
        assert!(sent.color().has_alpha(), "the alpha channel must still be there");

        // ...and the other half, or "always PNG" would pass this while costing every
        // photograph its size: an opaque picture of the same shape becomes a JPEG.
        let opaque = encode_picked_image(generated_png(1700, 340, false)).unwrap();
        assert_eq!(opaque.media_type, "image/jpeg");
    }

    #[test]
    fn every_encoded_picture_lands_on_the_closed_media_type_set() {
        // THE CLOSED SET IS ENFORCED HERE AND NOWHERE ELSE (plan §3): the provider
        // adapters deliberately do not re-check it, because an assertion there could
        // only turn a shell bug into a stack trace mid-sentence. So this side has to
        // be true by construction, and every route out of the encoder is walked:
        // pass-through, the JPEG ladder and the PNG branch.
        //
        // HAND-SYNCED with `ALLOWED_IMAGE_MEDIA_TYPES` in agent_core/providers/base.py.
        assert_eq!(
            ALLOWED_IMAGE_MEDIA_TYPES,
            ["image/png", "image/jpeg", "image/gif", "image/webp"],
            "the closed set must stay entry-for-entry with ALLOWED_IMAGE_MEDIA_TYPES \
             in agent_core/providers/base.py — a fifth type is accepted by whichever \
             provider happened to answer and refused by the other three"
        );

        for bytes in [
            generated_png(32, 32, false),   // pass-through
            generated_png(1700, 340, false), // downscale + JPEG
            generated_png(1700, 340, true),  // downscale + PNG
        ] {
            let encoded = encode_picked_image(bytes).unwrap();
            assert!(
                ALLOWED_IMAGE_MEDIA_TYPES.contains(&encoded.media_type),
                "{} is not one of the four every vision API accepts",
                encoded.media_type
            );
            assert!(
                encoded.bytes.len() <= ENCODED_IMAGE_SIZE_BOUND,
                "what crosses the bridge must fit the encoded bound"
            );
        }

        // A format the crate CAN decode but no vision API takes must never pass
        // through as itself — the reason `passthrough_media_type` filters through the
        // closed set rather than matching a second list of formats.
        assert_eq!(passthrough_media_type(image::ImageFormat::Bmp), None);
        assert_eq!(passthrough_media_type(image::ImageFormat::Tiff), None);
        assert_eq!(passthrough_media_type(image::ImageFormat::Png), Some("image/png"));
        assert_eq!(passthrough_media_type(image::ImageFormat::Gif), Some("image/gif"));
    }

    #[test]
    fn an_oversize_picture_is_refused_before_it_is_read() {
        // The bound against absurdity, and the sentence names the number in MB derived
        // from the constant so the two cannot drift. The refusal is taken from the
        // file's SIZE — the read that follows it never happens.
        let err = refuse_oversize_picture(PICKED_IMAGE_SIZE_BOUND + 1).unwrap_err();
        assert_eq!(err.code, -32000);
        assert_eq!(
            err.message,
            "That picture is too big for Addison to open — please pick one that's 24 MB or smaller."
        );
        assert!(refuse_oversize_picture(PICKED_IMAGE_SIZE_BOUND).is_ok(), "AT the bound passes");
    }

    #[test]
    fn a_picked_image_read_resolves_only_a_handle_the_shell_minted() {
        // The handle argument, unchanged from `read_scoped_file` and asserted for the
        // second reader: a raw path is not a handle, and a handle nobody minted reads
        // nothing. Same sentence for both, so the person cannot tell which read
        // refused — or an unknown handle from an expired one.
        let state = FileState::default();
        let err = resolve_picked_handle(&state, "/etc/passwd").unwrap_err();
        assert_eq!(err.message, "Addison can't read that file — please pick it again.");
        assert!(resolve_picked_handle(&state, &uuid::Uuid::new_v4().to_string()).is_err());

        let path = temp_path();
        std::fs::write(&path, generated_png(8, 8, false)).expect("seed a picture");
        lock(&state.handles).insert("minted".to_string(), path.clone());
        let (name, encoded) =
            read_and_encode_picked_image(&resolve_picked_handle(&state, "minted").unwrap()).unwrap();
        assert_eq!(name, path.file_name().unwrap().to_string_lossy());
        assert_eq!(encoded.media_type, "image/png");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn digest_answers_every_path_it_was_asked_about() {
        // ONE ROUND TRIP for the whole list, keyed by path for the same reason
        // `canRestoreWorkspaceFiles` is: a batch that dropped an unreadable member
        // would leave the caller unable to tell "unchanged" from "not answered".
        let there = temp_path();
        let gone = temp_path();
        std::fs::write(&there, "x").expect("seed");

        let params = json!({ "paths": [there.to_string_lossy(), gone.to_string_lossy()] });
        let answer = digest_workspace_files(&params).unwrap();
        let digests = answer.get("digests").and_then(Value::as_object).expect("a map");
        assert_eq!(digests.len(), 2);
        assert!(digests
            .get(&there.to_string_lossy().to_string())
            .and_then(|entry| entry.get("sha256"))
            .and_then(Value::as_str)
            .is_some());

        let _ = std::fs::remove_file(&there);
    }
}
