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
    CONVERSATION_NEW = "conversation.new"    # {} -> {conversationId}
    CONVERSATION_LOAD = "conversation.load"  # {conversationId} -> {conversationId, title, messages}
    CONVERSATION_LIST = "conversation.list"  # {} -> {conversations}
    CONVERSATION_STREAM_CHUNK = "conversation.streamChunk"
    PERMISSION_REQUEST_GRANT = "permission.requestGrant"
    PERMISSION_RESPOND = "permission.respond"
    TOOL_ACTIVITY_UPDATE = "tool.activityUpdate"
    UNDO_REWIND_CONVERSATION = "undo.rewindConversation"
    UNDO_UNDO_LAST_ACTION = "undo.undoLastAction"
    UNDO_REDO_LAST_ACTION = "undo.redoLastAction"
    ROUTINE_PROPOSE_FROM_CONVERSATION = "routine.proposeFromConversation"
    ROUTINE_CONFIRM_SAVE = "routine.confirmSave"
    ROUTINE_LIST = "routine.list"
    ROUTINE_RUN = "routine.run"
    ROUTINE_DELETE = "routine.delete"
    PROFILE_GET = "profile.get"      # {} -> {activeProfile, profiles: [{id,label,description}], flags}
    PROFILE_SET = "profile.set"      # {profileId} -> {ok}; persisted in app_settings (§4.7)
    MODEL_AVAILABLE_ROLES = "model.availableRoles"
    MODEL_SET_ROLE_FOR_NEXT_MESSAGE = "model.setRoleForNextMessage"
    MODEL_START_LOCAL_SETUP = "model.startLocalSetup"
    MODEL_LOCAL_SETUP_PROGRESS = "model.localSetupProgress"
    # Multi-provider API keys (owner decision 2026-07-18). Keys themselves NEVER
    # cross this boundary — the webview stores them straight into the OS keychain via
    # the Rust command; these methods carry only non-secret status/metadata.
    PROVIDER_LIST = "provider.list"            # {} -> {providers: [{id,label,connected,addedAt?,baseUrl?,lastCheckOk?}]}
    PROVIDER_CONNECT = "provider.connect"      # {provider, baseUrl?} -> {ok, error?}
    PROVIDER_DISCONNECT = "provider.disconnect"  # {provider} -> {ok}

    # Widgets — DECLARATIVE specs only (agent_core/widgets.py): a saved-routine Run
    # pill or a whitelisted stat display. NEVER code. Widgets are proposed like
    # routines (draft-held-in-memory + explicit confirm) and saved LOW-risk.
    WIDGET_LIST = "widget.list"                # {} -> {widgets: [{id, spec, pinned, position}]}
    WIDGET_SET_PINNED = "widget.setPinned"     # {id, pinned} -> {ok, error?}
    WIDGET_DELETE = "widget.delete"            # {id} -> {ok}
    WIDGET_PROPOSE_FROM_CONVERSATION = "widget.proposeFromConversation"  # {} -> {title, kind, summary, spec}
    WIDGET_CONFIRM_SAVE = "widget.confirmSave"  # {accept} -> {ok, widgetId?}
    # Core-computed, read-only stat sources for the token meter / connections cards.
    STATS_GET = "stats.get"                    # {} -> {tokensMonth, providerLatency, connections}

    # Core -> Shell (handled in Rust, NEVER exposed to or callable from the
    # webview — §1.3, §5). Listed here and mirrored in protocol.ts only so the
    # golden-file drift test (§9) covers the full method surface. These carry
    # the ShellBridge contract (tools/base.py) across the process boundary.
    SHELL_SAVE_NEW_FILE = "shell.saveNewFile"          # {filename, content} -> {path}
    SHELL_DELETE_FILE = "shell.deleteFile"             # {path} -> {}
    SHELL_RESTORE_FILE = "shell.restoreFile"           # {path, content} -> {} (redo of delete)
    SHELL_OPEN_DRAFT = "shell.openDraft"               # {to, subject, body} -> {draftRef}
    SHELL_DISCARD_DRAFT = "shell.discardDraft"         # {draftRef} -> {}
    SHELL_READ_CLIPBOARD = "shell.readClipboard"       # {} -> {text}
    SHELL_OPEN_EXTERNAL = "shell.openExternal"         # {url} -> {}
    SHELL_PICK_FILE = "shell.pickFile"                 # {} -> {fileHandle} (opaque, not a path)
    SHELL_READ_SCOPED_FILE = "shell.readScopedFile"    # {fileHandle} -> {content, kind}
    KEYCHAIN_GET_DEVICE_KEY = "keychain.getDeviceKey"      # {} -> {deviceId, publicKey}; public half ONLY
    KEYCHAIN_GET_PROVIDER_KEY = "keychain.getProviderKey"  # {provider} -> {key}; per-call, never cached
    # {payload} -> {signature, deviceId}. The shell signs relay requests with the
    # device private key, which never leaves the OS keychain (§5) — the core sends
    # bytes to sign, never sees key material.
    KEYCHAIN_SIGN_RELAY_REQUEST = "keychain.signRelayRequest"
