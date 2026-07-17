"""Shared JSON-RPC message types — engineering-spec §7.

Kept hand-synced with the frontend's ``shell/src/types/protocol.ts`` for v1
(codegen is a Phase 3 improvement, not a v1 requirement). The golden-file drift
test (§9) compares the two.

METHODS (representative subset, §7):
  Frontend -> Core:
    conversation.sendMessage
    permission.respond
    undo.rewindConversation, undo.undoLastAction
    routine.proposeFromConversation, routine.confirmSave
    routine.list, routine.run, routine.delete
    model.setRoleForNextMessage
    model.startLocalSetup
  Core -> Frontend:
    conversation.streamChunk
    permission.requestGrant
    tool.activityUpdate
    model.availableRoles
    model.localSetupProgress
  Core -> Shell (Rust-internal, not exposed to the frontend):
    keychain.getDeviceKey, keychain.getProviderKey
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JsonRpcRequest:
    method: str
    params: dict = field(default_factory=dict)
    id: str | int | None = None
    jsonrpc: str = "2.0"


@dataclass
class JsonRpcResponse:
    id: str | int | None
    result: Any = None
    error: dict | None = None
    jsonrpc: str = "2.0"


# Method name constants — keep in lockstep with protocol.ts.
class Method:
    CONVERSATION_SEND_MESSAGE = "conversation.sendMessage"
    CONVERSATION_STREAM_CHUNK = "conversation.streamChunk"
    PERMISSION_REQUEST_GRANT = "permission.requestGrant"
    PERMISSION_RESPOND = "permission.respond"
    TOOL_ACTIVITY_UPDATE = "tool.activityUpdate"
    UNDO_REWIND_CONVERSATION = "undo.rewindConversation"
    UNDO_UNDO_LAST_ACTION = "undo.undoLastAction"
    ROUTINE_PROPOSE_FROM_CONVERSATION = "routine.proposeFromConversation"
    ROUTINE_CONFIRM_SAVE = "routine.confirmSave"
    ROUTINE_LIST = "routine.list"
    ROUTINE_RUN = "routine.run"
    ROUTINE_DELETE = "routine.delete"
    MODEL_AVAILABLE_ROLES = "model.availableRoles"
    MODEL_SET_ROLE_FOR_NEXT_MESSAGE = "model.setRoleForNextMessage"
    MODEL_START_LOCAL_SETUP = "model.startLocalSetup"
    MODEL_LOCAL_SETUP_PROGRESS = "model.localSetupProgress"

    # Core -> Shell (handled in Rust, NEVER exposed to or callable from the
    # webview — §1.3, §5). Listed here and mirrored in protocol.ts only so the
    # golden-file drift test (§9) covers the full method surface. These carry
    # the ShellBridge contract (tools/base.py) across the process boundary.
    SHELL_SAVE_NEW_FILE = "shell.saveNewFile"          # {filename, content} -> {path}
    SHELL_DELETE_FILE = "shell.deleteFile"             # {path} -> {}
    SHELL_OPEN_DRAFT = "shell.openDraft"               # {to, subject, body} -> {draftRef}
    SHELL_DISCARD_DRAFT = "shell.discardDraft"         # {draftRef} -> {}
    SHELL_READ_CLIPBOARD = "shell.readClipboard"       # {} -> {text}
    SHELL_OPEN_EXTERNAL = "shell.openExternal"         # {url} -> {}
    SHELL_PICK_FILE = "shell.pickFile"                 # {} -> {fileHandle} (opaque, not a path)
    SHELL_READ_SCOPED_FILE = "shell.readScopedFile"    # {fileHandle} -> {content, kind}
    KEYCHAIN_GET_DEVICE_KEY = "keychain.getDeviceKey"      # {} -> {deviceId, publicKey}; public half ONLY
    KEYCHAIN_GET_PROVIDER_KEY = "keychain.getProviderKey"  # {role} -> {key}; per-call, never cached
    # {payload} -> {signature, deviceId}. The shell signs relay requests with the
    # device private key, which never leaves the OS keychain (§5) — the core sends
    # bytes to sign, never sees key material.
    KEYCHAIN_SIGN_RELAY_REQUEST = "keychain.signRelayRequest"
