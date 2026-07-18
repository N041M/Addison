// Frontend mirror of agent_core/protocol.py — engineering-spec §7.
// HAND-SYNCED for v1. A golden-file drift test (§9) compares this against the
// Python side; codegen from the dataclasses is a Phase 3 improvement, not a v1
// requirement. Keep method names and shapes in lockstep with protocol.py.

export const Method = {
  ConversationSendMessage: "conversation.sendMessage",
  ConversationNew: "conversation.new",
  ConversationLoad: "conversation.load",
  ConversationList: "conversation.list",
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

  // Widgets — DECLARATIVE specs only (agent_core/widgets.py): a saved-routine Run
  // pill or a whitelisted stat display, NEVER code. Proposed like routines
  // (draft-in-memory + explicit confirm) and saved LOW-risk (display-only).
  WidgetList: "widget.list",
  WidgetSetPinned: "widget.setPinned",
  WidgetDelete: "widget.delete",
  WidgetProposeFromConversation: "widget.proposeFromConversation",
  WidgetConfirmSave: "widget.confirmSave",
  // Core-computed, read-only stat sources for the token meter / connections cards.
  StatsGet: "stats.get",

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
}
