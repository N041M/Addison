import type { ArtifactUnavailable } from "./ui";

// Frontend mirror of agent_core/protocol.py — engineering-spec §7.
// HAND-SYNCED for v1. A golden-file drift test (§9) compares this against the
// Python side; codegen from the dataclasses is a Phase 3 improvement, not a v1
// requirement. Keep method names and shapes in lockstep with protocol.py.

export const Method = {
  ConversationSendMessage: "conversation.sendMessage",
  ConversationNew: "conversation.new",
  ConversationLoad: "conversation.load",
  ConversationList: "conversation.list",
  ConversationRename: "conversation.rename",
  ConversationStreamChunk: "conversation.streamChunk",
  // Stop. There is still no mid-step interrupt in v1 — the core finishes the tool
  // call it started — so what this ends is the turn's CONSENT: every permission
  // card waiting for an answer is refused, and the turn may not raise another. The
  // webview showing a stopped card as expired is presentation; this call is what
  // makes a late Allow do nothing (agent_core/main.py, `_handle_conversation_stop`).
  ConversationStop: "conversation.stop",
  PermissionRequestGrant: "permission.requestGrant",
  // {} -> {request: PermissionRequest | null}. The re-sync query: `requestGrant` is
  // a notification, so a card that never reached a subscriber — or was rendered
  // where the reader could not see it — leaves the engine blocked with nothing on
  // screen and nothing that expires. Asking turns that into a short blip. Reading
  // only: it never answers a card and never costs an arming attempt.
  PermissionPending: "permission.pending",
  PermissionRespond: "permission.respond",
  ToolActivityUpdate: "tool.activityUpdate",
  UndoRewindConversation: "undo.rewindConversation",
  UndoUndoLastAction: "undo.undoLastAction",
  UndoRedoLastAction: "undo.redoLastAction",
  RoutineProposeFromConversation: "routine.proposeFromConversation",
  RoutineConfirmSave: "routine.confirmSave",
  // A `routine.list` row carries `unavailable` {reason, message} when the ACTIVE
  // profile can't use it — a routine made with developer abilities is listed in
  // Simple, visibly disabled, rather than disappearing (owner decision
  // 2026-08-06). Absent on a usable row; `reason` is an open slug vocabulary, not
  // a boolean, so a later cause slots in. DISPLAY ONLY: `routine.run` refuses on
  // its own, with the same sentence, whatever this field says. Mirrored in
  // protocol.py; the parsed shape lives in types/ui.ts (ArtifactUnavailable).
  RoutineList: "routine.list",
  RoutineRun: "routine.run",
  RoutineDelete: "routine.delete",
  ProfileGet: "profile.get",
  ProfileSet: "profile.set",
  ModelAvailableRoles: "model.availableRoles",
  ModelSetRoleForNextMessage: "model.setRoleForNextMessage",
  ModelStartLocalSetup: "model.startLocalSetup",
  ModelLocalSetupProgress: "model.localSetupProgress",
  // Multi-provider API keys (owner decision 2026-07-18). These carry only
  // non-secret status/metadata — the key itself goes to the OS keychain via the
  // Rust `store_provider_key` command, never through the core.
  ProviderList: "provider.list",
  ProviderConnect: "provider.connect",
  ProviderDisconnect: "provider.disconnect",

  // Add-a-server-by-prompt + "make it cheaper" (Phase-2 step 4). The turn reply
  // never carries a model-authored payload: the core inspects the current turn,
  // drafts, and HOLDS a base URL / a canned cost plan for an explicit confirm
  // card (the widget/routine precedent). Keys are pasted into the endpoint card
  // and stored straight to the OS keychain by the shell — never through the core.
  EndpointProposeFromConversation: "endpoint.proposeFromConversation",
  EndpointConfirmAdd: "endpoint.confirmAdd",
  CostPlanPropose: "costPlan.propose",
  CostPlanApply: "costPlan.apply",

  // Widgets — DECLARATIVE specs only (agent_core/widgets.py): a saved-routine Run
  // pill, a whitelisted stat display, or one of the three interactive SAFE kinds
  // (checklist / note / timer), NEVER code. Proposed like routines (draft-in-memory
  // + explicit confirm) and saved LOW-risk (display-only).
  // Rows carry the same `unavailable` {reason, message} marker on the same terms
  // as `routine.list` above (absent when usable, display only).
  WidgetList: "widget.list",
  WidgetSetPinned: "widget.setPinned",
  WidgetDelete: "widget.delete",
  // {id, state} -> {ok, state?} | {ok:false, error}. A tick, an edited note, a
  // paused timer — the mutable half of the three interactive kinds. The widget's
  // SPEC never changes; this writes the separate state the core keeps beside it,
  // and the core VALIDATES that state per kind before storing it, so nothing this
  // frontend sends is taken on trust. It answers with the state it actually
  // stored, which is what an optimistic update reconciles against. No permission
  // card, because these kinds invoke no tool and touch nothing outside Addison.
  WidgetSetState: "widget.setState",
  WidgetProposeFromConversation: "widget.proposeFromConversation",
  WidgetConfirmSave: "widget.confirmSave",
  WidgetRun: "widget.run",
  // Core-computed, read-only stat sources for the token meter / connections cards.
  StatsGet: "stats.get",

  // Skills — user-authored, plain-text guidance notes the person can toggle on;
  // when enabled, Addison follows them. PURE TEXT, no execution surface (unlike
  // routines/widgets there is no command/tool step) — the same in both modes.
  SkillList: "skill.list",
  SkillCreate: "skill.create",
  SkillUpdate: "skill.update",
  SkillSetEnabled: "skill.setEnabled",
  SkillDelete: "skill.delete",

  // Snapshots — the G3 guaranteed-rollback floor. A snapshot copies Addison's
  // settings/providers/skills/widgets/routines; it never contains your saved
  // keys (they stay in the system keychain) and never touches your chats.
  // "Restore" always goes back to the last setup that actually worked.
  SnapshotList: "snapshot.list",
  SnapshotCreate: "snapshot.create",
  SnapshotRestore: "snapshot.restore",
  SnapshotRestoreLastWorking: "snapshot.restoreLastWorking",
  SnapshotDelete: "snapshot.delete",

  // Guards — the two tunable prompting guards of the Custom profile (Phase-2
  // step 2). They modulate ONLY how often the gate asks before acting; they can
  // never touch a global floor (G1/G2/G3/G4). `get` returns the current values,
  // the fixed defaults, and whether they're effective right now (profile is
  // Custom); `set` validates, mints the G4 undeletable anchor when a save
  // weakens a guard, then persists — all core-side.
  GuardsGet: "guards.get",
  GuardsSet: "guards.set",

  // Workspace trust — the coding-harness trust boundary (Phase-2 step 5). A
  // trusted folder lets Addison's typed, undoable file tools read and edit inside
  // it WITHOUT a per-change card; commands it runs still ask every time. `grant`
  // takes an absolute directory (the core floor-checks it and refuses Addison's
  // own data dir); `revoke` drops one; `list` returns the currently-trusted
  // roots. `pickDirectory` opens the OS folder picker through the Rust shell and
  // returns the chosen path (or nothing if cancelled) — no key material, no file
  // contents ever ride these payloads.
  WorkspaceGrantTrust: "workspace.grantTrust",
  WorkspaceRevokeTrust: "workspace.revokeTrust",
  WorkspaceList: "workspace.list",
  WorkspacePickDirectory: "workspace.pickDirectory",

  // Looking at what is in a trusted folder — the review surface's read paths
  // (Phase 3). `listDirectory` answers ONE level of a folder you have trusted;
  // `readFile` answers one file's text so it can be shown, never changed. Nothing
  // here writes, and nothing here is a thing Addison can decide to do: these answer
  // a click you just made, which is why they are plain requests and not tools the
  // model can reach for — and why they raise no permission card.
  //
  // Both are Developer/Custom only. Trusting a folder does not expire when you
  // switch to Simple, so the core refuses these outside Developer rather than
  // trusting the window to stop asking.
  //
  // Nothing is hidden from a listing — `.git` and `node_modules` are there like
  // everything else, because a file tree that quietly leaves things out is worse
  // than none. A shortcut that points somewhere outside the folder you trusted is
  // marked (`escapes`) so it can be shown for what it is; opening it is refused by
  // the core, not by that mark. A very large folder answers with its first 500
  // entries by name and says it did (`truncated`); a very large file is shown from
  // the start and says how big it really is. Mirrored in protocol.py.
  WorkspaceListDirectory: "workspace.listDirectory",
  WorkspaceReadFile: "workspace.readFile",

  // Seeing what Addison changed, and putting one file back (Phase 3). `listEdits`
  // answers every file Addison has edited that is STILL edited — not the edits from
  // this conversation, but the ones still standing on disk, whichever chat they were
  // made in, because that is the one a person needs to find. It carries no file text:
  // `readEditDiff` fetches the before and after for the one file you opened.
  //
  // Several changes to one file collapse into one entry. What you are shown, and what
  // Revert produces, is the file as it was before the FIRST of those changes — a state
  // that really existed. There is no way to put back part of a file, deliberately:
  // that would write something that never existed on disk.
  //
  // `revertable` is your computer's answer, not a preference: Addison can only put back
  // a file it changed since the app last started. For anything older the app says so
  // plainly and leaves the earlier version on screen for you to copy. `onDiskChanged`
  // has THREE answers — yes, no, and "Addison can't tell" — and the third is honest
  // rather than a guess: reverting replaces whatever is there now, so a file you have
  // edited yourself since is warned about before anything is written. `replacedBy`
  // is the other question — not "has this file changed?" but "is this still the same
  // file?": a shortcut or a different file standing at the name is refused by the
  // core, so the screen says so instead of offering a Revert that cannot work.
  //
  // Developer/Custom only, like the two above. Mirrored in protocol.py.
  WorkspaceListEdits: "workspace.listEdits",
  WorkspaceReadEditDiff: "workspace.readEditDiff",
  WorkspaceRevertFile: "workspace.revertFile",

  // MCP servers — the external tool servers Addison consumes as a client (Phase-2
  // step 7, phases 1–4 of five). `refresh` connects, lists the server's tools, and
  // registers each one namespaced and Developer-only: absent from the SAFE view and
  // refused outside OPEN at both dispatch sites, and in OPEN offered to the model
  // and invoked through the ordinary gate, reaching it HIGH and destructive because
  // a server's own claim about its risk is the thing v1 refuses to trust. How often
  // that produces a card is the gate's answer, which the Custom profile can tune, so
  // nothing here promises a frequency. The address is HTTP(S)
  // (`https://`, or `http://` only for a server on this computer); there is never a
  // command to run, so nothing here starts a program. No key or token rides these
  // payloads and none is stored — a server that wants a sign-in gets one plain
  // sentence back. `add` and `refresh` are refused outside the Developer profile;
  // `list` and `remove` answer in every profile, so saved configuration never
  // disappears and can always be removed. Mirrored in protocol.py.
  //
  // A row's `status` is "never" | "ok" | "failed", and `McpServer.status` is that
  // vocabulary exactly. A check in flight is NOT one of them: the core answers
  // `list` and `refresh` on the same worker thread, so a list could never observe
  // one — the frontend tracks the row it is waiting on itself (useMcpServers).
  McpList: "mcp.list",
  McpAdd: "mcp.add",
  McpRemove: "mcp.remove",
  McpRefresh: "mcp.refresh",

  // Automations — the work Addison writes down for YOUR COMPUTER to run on a
  // schedule (Phase-2 step 8, phase 1 of four). Addison never runs anything by
  // itself and never sets a timer of its own: the operating system runs the job,
  // Addison only writes the file it runs from — and only once you arm it, which
  // is a later phase and asks you to type a short code first.
  //
  // Phase 1 has NO add and no arm. Nothing in the app can create an automation
  // yet, so `automation.list` answers an empty list on every install; writing one
  // is phase 2 and arming it is phase 3. Both methods here answer in EVERY
  // profile: a saved automation is configuration, not an ability, so switching to
  // Simple never hides one and never blocks removing one. What NEEDS the Developer
  // profile is writing and arming, and that is refused where the ability lives.
  //
  // Nothing in these payloads says whether an automation is currently armed. Your
  // computer owns that answer and is asked for it when the surface loads, so what
  // you see is what is really installed — after a restore, a reinstall, or a file
  // deleted by hand alike. Mirrored in protocol.py.
  //
  // A row is {id, name, label, command, scheduleKind, schedule, scheduleSentence,
  // createdInMode, createdAt}. `scheduleKind` is "interval" | "calendar" and
  // `schedule` holds that kind's numbers and nothing else — {minutes} or {hour,
  // minute, weekday?} — so a row can never carry a field of its own onto the screen.
  // `scheduleSentence` is those same numbers written out as one plain sentence, by
  // the core: "Every 30 minutes", "Every Monday at 7:30", or "No schedule saved
  // yet." when the row does not say. This side RENDERS that sentence and never
  // writes its own out of the numbers — one wording, wherever a schedule is shown.
  // `command` is the exact text that would run, whole and unshortened, because
  // reading it is the point. `createdInMode` is display-only provenance and decides
  // nothing. Nothing here is ever the file itself: your computer's job file is
  // written by the Rust shell from those fields, so no payload carries a document.
  AutomationList: "automation.list",
  AutomationRemove: "automation.remove",
  AutomationStatus: "automation.status",
  // Switch off a job your computer is running that Addison has no saved copy of.
  // Restoring a restore point from before an automation was written takes its saved
  // copy away, and your computer keeps running the job — so the Automations section
  // compares what the operating system says it is running against what is saved, and
  // shows anything left over as its own row with a "switch off" on it. That button
  // sends this. It takes a LABEL, only ever one Addison itself set up, and the only
  // thing it can do is stop it: there is still no way for the app to start, install
  // or schedule anything from this screen, in any profile. Mirrored in protocol.py.
  AutomationDisarmOrphan: "automation.disarmOrphan",

  // Routing — how Addison picks which model answers a turn (Phase-2 step 3).
  // `get` returns the current strategy, the strategies this surface may pick
  // from, the Developer custom order, and whether the person sees the Simple
  // TWO-option toggle ("toggle") or the full picker + chain builder ("full").
  // `set` validates the closed strategy vocabulary and the model ids, snapshots
  // per the core's hook split (a plain strategy change proceeds-with-warning; a
  // custom-chain overwrite is REFUSED if the snapshot can't be saved), then
  // persists — all core-side. No key material is ever in these payloads (G1).
  RoutingGet: "routing.get",
  RoutingSet: "routing.set",

  // Workspace trust (step 5) — the OPEN-mode coding harness's trust boundary.
  // Developer/Custom surfaces only. grantTrust floor-refuses Addison's own data dir.

  // Core -> Shell (handled in Rust, NEVER callable from this webview — spec
  // §1.3, §5). Mirrored from protocol.py only so the golden-file drift test
  // (§9) covers the full method surface; the frontend must never invoke these.
  ShellSaveNewFile: "shell.saveNewFile",
  ShellDeleteFile: "shell.deleteFile",
  ShellRestoreFile: "shell.restoreFile",
  ShellOpenDraft: "shell.openDraft",
  ShellDiscardDraft: "shell.discardDraft",
  ShellReadClipboard: "shell.readClipboard",
  ShellOpenExternal: "shell.openExternal",
  ShellPickFile: "shell.pickFile",
  ShellReadScopedFile: "shell.readScopedFile",
  // Workspace-trust file surface (step 5, OPEN harness) — path-based, Rust-enforced.
  ShellWriteWorkspaceFile: "shell.writeWorkspaceFile",
  ShellReadWorkspaceFile: "shell.readWorkspaceFile",
  ShellRestoreWorkspaceFile: "shell.restoreWorkspaceFile",
  ShellPickDirectory: "shell.pickDirectory",
  // The review surface's read paths (Phase 3). Reached only from the core's
  // `workspace.listDirectory` / `workspace.readFile`, never from here.
  ShellListWorkspaceDirectory: "shell.listWorkspaceDirectory",
  ShellReadWorkspaceFileForView: "shell.readWorkspaceFileForView",
  // The review surface's revert half (Phase 3). The first asks which files the shell
  // could still put back this session — a question, with no effect of any kind; the
  // second hashes files on disk so the core can tell a file as Addison left it from
  // one edited since. Reached only from the core, never from here.
  ShellCanRestoreWorkspaceFiles: "shell.canRestoreWorkspaceFiles",
  ShellDigestWorkspaceFiles: "shell.digestWorkspaceFiles",
  // The delete preview (5.6). A bounded directory walk that counts what sits under
  // the paths a delete command named, so the permission card can say what the delete
  // would cost. It runs nothing and opens no file. Reached only from the core, while
  // it is composing that card.
  ShellPreviewDeletePaths: "shell.previewDeletePaths",
  // ...and the one way a path re-enters the shell's session write ledger, on proof
  // that the bytes there now are the bytes Addison wrote. It is what lets a revert
  // work after a restart. Core -> shell only, like the two above.
  ShellAdoptWorkspacePath: "shell.adoptWorkspacePath",
  ShellRunCommand: "shell.runCommand",
  // Arming (step 8 phase 3). Core -> shell only; the shell builds the plist itself
  // from typed fields and never accepts a document (plan §5.8).
  ShellArmAutomation: "shell.armAutomation",
  ShellDisarmAutomation: "shell.disarmAutomation",
  ShellListArmed: "shell.listArmed",
  ShellAppBuildRef: "shell.appBuildRef",
  KeychainGetDeviceKey: "keychain.getDeviceKey",
  KeychainGetProviderKey: "keychain.getProviderKey",
  KeychainSignRelayRequest: "keychain.signRelayRequest",
} as const;

export type MethodName = (typeof Method)[keyof typeof Method];

export type ModelRole = "primary" | "local" | "setup_assistant";
export type RiskTier = "low" | "medium" | "high";
export type PermissionStatus = "granted" | "denied" | "not_yet_asked";

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  method: MethodName;
  params?: Record<string, unknown>;
  id?: string | number | null;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string };
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolCallId?: string;
}

export interface PermissionRequest {
  toolId: string;
  label: string;
  description: string;
  riskTier: RiskTier;
  /**
   * THE DELETE PREVIEW (5.6, first form). One plain sentence about what this
   * command would delete ("About to delete 1,240 files in 12 folders.") computed
   * in the core by WALKING the paths, never by running anything. Present only when
   * the core could read the command as a delete with paths it could name, which is
   * deliberately a narrow set: it says nothing rather than a wrong number.
   *
   * Its own field, not part of `description`, because the card splits that string
   * on the `run: ` prefix to draw the command as a machine fact, a sentence
   * appended there would be rendered as though it were part of the command.
   * Mirrored in protocol.py (`shell.previewDeletePaths` is the walk behind it).
   */
  preview?: string;
  /**
   * ARMING ONLY (step 8 phase 3). Present when this card is the keyword gate: the
   * person must retype `nonce` before the approval counts, and the fields beside it
   * are what they are being asked to read first — the exact command, where the file
   * goes, and the two sentences that make the stakes plain.
   *
   * The code is compared IN THE CORE (`agent_core/automation_nonce.py`) and travels
   * back on `permission.respond` as `typed`. It is never stored, never logged, and
   * never put in a model's context — a nonce a model can read is one it can type,
   * which is the whole thing the gate exists to prevent. The webview's job is to
   * SHOW it and to send back what was typed, never to decide whether it matched.
   */
  arming?: {
    nonce: string;
    automationName: string;
    scheduleSentence: string;
    command: string;
    /** Where the OS will read the job from, e.g. ~/Library/LaunchAgents/<label>.plist */
    installPath: string;
    /** Frozen copy the core owns; the webview renders it verbatim. */
    warnings: string[];
    /** How many wrong answers remain before this request denies outright. */
    attemptsLeft: number;
  };
}

/**
 * `automation.status` — what the OPERATING SYSTEM currently holds, asked on demand.
 * Never stored and never polled: a G3 restore can put a database row back and can
 * never put a running job back, so the honest answer always comes from launchd
 * (plan §5.6). `supported` is false off macOS, where arming does not exist.
 */
export interface AutomationStatus {
  armed: string[];
  supported: boolean;
  error?: string;
}

/**
 * One `automation.list` row — the work Addison has written down for THIS COMPUTER
 * to run on a schedule (Phase-2 step 8). HAND-SYNCED with `_automation_wire_row`
 * in agent_core/rpc/automations.py; the comment on `Method.AutomationList` above
 * is the same shape said in plain words, and shell/src/__tests__/fixtures/
 * automation.list.json is the generated artifact both sides are pinned against.
 *
 * NOTHING HERE SAYS WHETHER IT IS ARMED. No field carries it, because the
 * operating system owns that answer and is asked for it when the surface loads
 * (plan §5.6) — a remembered flag is exactly what a one-action restore would put
 * back, and a restore cannot perform the ceremony that arming requires.
 *
 * And nothing here is the job FILE. The shell writes that from typed fields; a
 * document crossing this boundary would make the webview a courier for something
 * the highest-trust process is supposed to author (plan §5.8).
 */
export interface Automation {
  /** The core's row id — what `automation.remove` takes. */
  id: string;
  /** The plain name this automation was saved under. */
  name: string;
  /** Its launchd label — the filename stem the shell would write, and unique. */
  label: string;
  /** The exact text that would run, whole. Never shortened: reading it is the point. */
  command: string;
  /** Which schedule vocabulary this row speaks. Absent when it says nothing usable. */
  scheduleKind?: "interval" | "calendar";
  /** That kind's numbers and nothing else — {minutes} or {hour, minute, weekday?}. */
  schedule: Record<string, number>;
  /**
   * The schedule as ONE plain sentence, written by the core. Rendered here as it
   * arrives: a second renderer on this side is how a row ends up reading "Every
   * day at 7:5" or guessing am/pm on the one line a person checks before letting
   * something run while they sleep. Falls back to the core's own "No schedule
   * saved yet." — never to an invented schedule.
   */
  scheduleSentence: string;
  /** Display-only provenance ("safe" | "open"). Decides nothing, here or anywhere. */
  createdInMode?: "safe" | "open";
  /** Unix seconds when it was saved, when the core reports it. */
  createdAt?: number;
  /**
   * Present when the ACTIVE PROFILE cannot use this row — in Simple, always, since
   * an automation runs a command; absent otherwise. **The same marker routines and
   * widgets carry, and deliberately the same TYPE** (`ArtifactUnavailable`, the
   * shape `normalizeUnavailable` returns): one marker must not grow two readings of
   * what counts as one.
   *
   * DISPLAY ONLY. What refuses is the arming tools' registration and their dispatch;
   * a row WITHOUT this marker is not thereby permitted, and if the two ever
   * disagree, dispatch wins.
   */
  unavailable?: ArtifactUnavailable;
}

/**
 * One row of a `workspace.listDirectory` answer — the review surface's file tree
 * (Phase-3 plan Build §1). HAND-SYNCED with `_workspace_list_directory` in
 * agent_core/rpc/workspace.py, and pinned against the generated
 * shell/src/__tests__/fixtures/workspace.listDirectory.json.
 *
 * TYPES ONLY for now: §1 ships the read paths, and the screen that renders them is
 * §4. A consumer added here before then would be a parser with nothing to parse.
 */
export interface WorkspaceEntry {
  /** The entry's own name — never a path. The parent is the listing's `directory`. */
  name: string;
  /**
   * What it IS on disk, read WITHOUT following links: a shortcut is "symlink" and
   * never the kind of the thing it points at. That distinction is the whole reason
   * this field exists — a link to somewhere else must not render as a folder the
   * person can open, because they would open it before anything refused.
   */
  kind: "file" | "directory" | "symlink" | "other";
  /** The entry's own size in bytes, as the OS reports it. A link's own, never its target's. */
  size: number;
  /**
   * True when this entry resolves OUTSIDE the folder that was trusted. An honesty
   * affordance — dim it, say it points outside — and never the boundary: opening it
   * is refused by the core's own check, which is the same predicate that computed
   * this. Treating it as the guard would put the boundary in the window.
   */
  escapes: boolean;
}

/** A `workspace.listDirectory` answer. One level, never recursive. */
export interface WorkspaceListing {
  /** The folder that was listed, as the core RESOLVED it (links and `~` collapsed). */
  directory: string;
  /** The trusted root it sits under, when one can be named. Display only. */
  root: string | null;
  entries: WorkspaceEntry[];
  /**
   * True when the folder holds more than the listing carries — a very large folder
   * answers with its first entries by name. Say so in the UI: a listing that is
   * quietly incomplete is indistinguishable from a file that is not there.
   */
  truncated: boolean;
}

/** A `workspace.readFile` answer — text to SHOW, never to edit. */
export interface WorkspaceFileView {
  /** The file, as the core RESOLVED it. */
  path: string;
  /** The trusted root it sits under, when one can be named. Display only. */
  root: string | null;
  content: string;
  /**
   * The file's size on disk — NOT the length of `content`. They differ exactly when
   * `truncated` is true, which is when the difference is the thing worth showing.
   */
  bytes: number;
  /** True when `content` is the beginning of a larger file, cut on a character boundary. */
  truncated: boolean;
}

/**
 * One file Addison has changed that is STILL changed — a row of `workspace.listEdits`
 * (Phase-3 plan Build §2). HAND-SYNCED with `_edit_payload` in
 * agent_core/rpc/workspace.py, and pinned against the generated
 * shell/src/__tests__/fixtures/workspace.listEdits.json.
 *
 * METADATA ONLY: no before/after text rides this payload. `workspace.readEditDiff`
 * fetches the two panes for the one file that was opened.
 *
 * TYPES ONLY for now — §2/§3 ship the data and the revert; the screen is §4.
 */
export interface WorkspaceEdit {
  /** The file, resolved. Every later call about this edit carries this exact value. */
  path: string;
  /** The trusted root it sits under, or null when that trust has since been revoked. */
  root: string | null;
  /** What to render by default. The whole path when there is no root to be inside. */
  relativePath: string;
  /**
   * The revert chain, NEWEST FIRST. Reverting settles all of them at once — the
   * whole chain or nothing, so no row is left behind claiming a change that is no
   * longer on disk.
   */
  snapshotIds: string[];
  /** How many writes collapsed into this one entry. */
  writes: number;
  /** Addison CREATED the file: the before pane is empty and Revert removes it. */
  created: boolean;
  firstWrittenAt: number;
  lastWrittenAt: number;
  /**
   * Whether Addison can actually put this file back — the SHELL's answer about the
   * files it has written since the app started, not a permission and not a guess.
   * False for every edit made before the last restart: render those read-only with a
   * plain line rather than a button that fails.
   */
  revertable: boolean;
  /**
   * Whether what is on disk differs from what Addison wrote. THREE-VALUED: `null` is
   * "Addison can't tell" (a change recorded before it started hashing, or a file it
   * cannot judge) and must be shown as that, never treated as `false` — `false` is
   * what lets a revert proceed without warning.
   */
  onDiskChanged: boolean | null;
  /** The file is not there any more. Revert can still put it back. */
  missing: boolean;
  /**
   * SOMETHING ELSE IS AT THAT NAME NOW — a shortcut (`"shortcut"`) or a different
   * file, hard link included (`"other-file"`). `null` is the ordinary case.
   *
   * Distinct from `onDiskChanged`, which is about a file's CONTENTS: this says the
   * name no longer reaches the file Addison wrote at all. The core refuses both the
   * diff and the revert for these, so the screen must not offer a Revert that could
   * only fail — but this is a MARKER, never the enforcement. The refusal is the
   * core's and wins if the two ever disagree.
   */
  replacedBy: "shortcut" | "other-file" | null;
}

/** A `workspace.listEdits` answer. Newest first. */
export interface WorkspaceEditList {
  edits: WorkspaceEdit[];
  /** More edits exist than this answer carries. Say so. */
  truncated: boolean;
}

/** A `workspace.readEditDiff` answer — the two panes for ONE file. */
export interface WorkspaceEditDiff {
  path: string;
  /** What was there before Addison's FIRST unreverted change — where Revert lands. */
  before: string;
  /** What is there NOW, including anything you have changed since. */
  after: string;
  /**
   * Always false in this build: a stored before-state is whole by construction,
   * because the shell refuses to overwrite a file too big to capture. On the wire so
   * that can never change silently.
   */
  beforeTruncated: boolean;
  /** The file on disk was longer than the viewer shows. */
  afterTruncated: boolean;
}

/**
 * A `workspace.revertFile` answer. `ok: true` carries the file it put back and one
 * plain sentence to show; a refusal carries `ok: false` and `error` instead — the same
 * shape `workspace.grantTrust` uses.
 *
 * There is no partial revert and never will be: putting back some of a file would mean
 * writing a combination of bytes that never existed on disk. The whole chain of
 * Addison's changes to that file goes back at once, to the state it was in before the
 * first of them.
 */
export interface WorkspaceRevertResult {
  ok: boolean;
  /** The file, resolved. Absent on a refusal. */
  path?: string;
  /** Shown on success — names the file and what happened to it. */
  detail?: string;
  /** Shown on a refusal. */
  error?: string;
}

export interface ActivityUpdate {
  label: string; // e.g. "Searching the web…", "Reading invoice_march.pdf…"
  toolId: string;
  // What this step is about to touch, when the tool can say — read_web_page sends
  // the SITE it is reaching. Absent for the tools that have nothing to name.
  // Not decoration: a permission grant is keyed by tool id, so once the person has
  // allowed one page read, every later read is ungated and its address is chosen by
  // the model. This line is where they see it (owner decision 2026-07-20).
  //
  // It names the site, NOT the payload. The core deliberately sends the host only —
  // a full URL would put the query string, and anything a page hid in it, on screen
  // and into any screenshot — so a familiar host here is not evidence that the read
  // was innocent, only that the destination was not a surprise.
  detail?: string;
}
