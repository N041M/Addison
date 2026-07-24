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
  // pill or a whitelisted stat display, NEVER code. Proposed like routines
  // (draft-in-memory + explicit confirm) and saved LOW-risk (display-only).
  WidgetList: "widget.list",
  WidgetSetPinned: "widget.setPinned",
  WidgetDelete: "widget.delete",
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
