// Frontend mirror of agent_core/protocol.py — engineering-spec §7.
// HAND-SYNCED for v1. A golden-file drift test (§9) compares this against the
// Python side; codegen from the dataclasses is a Phase 3 improvement, not a v1
// requirement. Keep method names and shapes in lockstep with protocol.py.

export const Method = {
  ConversationSendMessage: "conversation.sendMessage",
  ConversationStreamChunk: "conversation.streamChunk",
  PermissionRequestGrant: "permission.requestGrant",
  PermissionRespond: "permission.respond",
  ToolActivityUpdate: "tool.activityUpdate",
  UndoRewindConversation: "undo.rewindConversation",
  UndoUndoLastAction: "undo.undoLastAction",
  RoutineProposeFromConversation: "routine.proposeFromConversation",
  RoutineConfirmSave: "routine.confirmSave",
  RoutineList: "routine.list",
  RoutineRun: "routine.run",
  RoutineDelete: "routine.delete",
  ModelAvailableRoles: "model.availableRoles",
  ModelSetRoleForNextMessage: "model.setRoleForNextMessage",
  ModelStartLocalSetup: "model.startLocalSetup",
  ModelLocalSetupProgress: "model.localSetupProgress",

  // Core -> Shell (handled in Rust, NEVER callable from this webview — spec
  // §1.3, §5). Mirrored from protocol.py only so the golden-file drift test
  // (§9) covers the full method surface; the frontend must never invoke these.
  ShellSaveNewFile: "shell.saveNewFile",
  ShellDeleteFile: "shell.deleteFile",
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
