// Typed wrapper around Tauri IPC — engineering-spec §7.
// The webview NEVER talks to the Agent Core or the network directly (§1.3); it
// goes through the Rust shell's `send_to_core` command, which relays JSON-RPC
// frames to/from the Python core over stdio.

import { Method, type JsonRpcResponse, type ModelRole } from "../types/protocol";

// import { invoke } from "@tauri-apps/api/core";       // wired in step 7
// import { listen } from "@tauri-apps/api/event";      // for Core -> Frontend notifications

async function call(method: string, params: Record<string, unknown> = {}): Promise<JsonRpcResponse> {
  // TODO(step 7): return invoke("send_to_core", { frame: { jsonrpc: "2.0", method, params, id } });
  throw new Error(`IPC not wired yet (${method}) — engineering-spec §11 step 7.`);
}

export const ipc = {
  sendMessage: (text: string, role?: ModelRole) =>
    call(Method.ConversationSendMessage, { text, role }),

  respondToPermission: (toolId: string, allow: boolean) =>
    call(Method.PermissionRespond, { toolId, allow }),

  undoLastAction: () => call(Method.UndoUndoLastAction),
  rewindConversation: (toMessageId: string) =>
    call(Method.UndoRewindConversation, { toMessageId }),

  listRoutines: () => call(Method.RoutineList),
  runRoutine: (routineId: string, variables: Record<string, string>) =>
    call(Method.RoutineRun, { routineId, variables }),
  proposeRoutine: () => call(Method.RoutineProposeFromConversation),

  availableRoles: () => call(Method.ModelAvailableRoles),
  setRoleForNextMessage: (role: ModelRole) =>
    call(Method.ModelSetRoleForNextMessage, { role }),
  startLocalSetup: () => call(Method.ModelStartLocalSetup),
};

// TODO(step 7): subscribe to Core -> Frontend notifications:
//   conversation.streamChunk, permission.requestGrant, tool.activityUpdate,
//   model.localSetupProgress — via listen(...) and route into React state.
