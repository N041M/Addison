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
  PermissionRequestGrant: "permission.requestGrant",
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
