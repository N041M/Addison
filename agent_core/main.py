"""Agent Core entrypoint — JSON-RPC 2.0 server over stdio (engineering-spec §1.2, §7).

Spawned and supervised by the Tauri shell as a child process. Reads JSON-RPC
requests from stdin, writes responses/notifications to stdout. No network stack
for local IPC.

This module wires the singletons (Store, ToolRegistry, PermissionGate,
UndoManager, ModelRouter, Orchestrator) and dispatches the §7 methods. For build
steps 1–6 (engineering-spec §11) it can also be driven from a CLI harness
without the shell — see ``run_cli()``.

The stdio server itself lives in ``JsonRpcServer``, which takes injectable in/out
streams plus its collaborators so it can be exercised in-process by tests (§9);
``main()`` wires the real singletons and runs it on stdin/stdout. stdout carries
ONLY JSON-RPC frames — every write goes through a single lock — so any logging
must go to stderr.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from uuid import uuid4

from agent_core.memory.store import Store
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.profiles import Profile, resolve_active_profile
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.router import ModelRouter
from agent_core.shell_bridge import IpcShellBridge
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ActionSnapshot
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.web_search import WebSearchTool

# JSON-RPC error codes. -32601 is the reserved "method not found"; the -32000
# band is the "server error" range we use for provider/tool/not-built failures,
# each carrying a plain-language message (never a stack trace).
_METHOD_NOT_FOUND = -32601
_SERVER_ERROR = -32000

_NOT_BUILT_MESSAGE = "This isn't built yet."
_GENERIC_TURN_ERROR = (
    "Addison couldn't finish that just now. Check your internet connection and "
    "that your API key is still valid, then try again."
)


def build_registry(profile: Profile | None = None, shell_bridge=None) -> ToolRegistry:
    """Register the tools the active Profile exposes (engineering-spec §4.2, §4.7).

    A Profile chooses *which* tools are registered; it never changes *how* safety
    is enforced — registration still RAISES for any MEDIUM/HIGH tool lacking undo()
    (that's the safety invariant, not a bug). Defaults to the Simple profile, whose
    tool set is exactly the v1 §4.2 table.

    ``shell_bridge`` is threaded into the constructors of the tools whose ``undo()``
    needs it (save_file, draft_message): undo() gets no ExecutionContext, so its
    bridge is injected here once and used ONLY by undo() — ``execute()`` still uses
    ``context.shell_bridge`` per the orchestration contract (§4.4). CLI/``main``
    pass None today; the real bridge arrives with the shell at step 7.
    """
    profile = profile or resolve_active_profile()
    all_tools = {
        "web_search": WebSearchTool(),
        "read_file": ReadFileTool(),
        "read_clipboard": ReadClipboardTool(),
        "calculator": CalculatorTool(),
        "save_file": SaveFileTool(shell_bridge=shell_bridge),
        "draft_message": DraftMessageTool(shell_bridge=shell_bridge),
        "open_link": OpenLinkTool(),
    }
    registry = ToolRegistry()
    for tool_id in profile.tool_ids:
        # TODO(step 11): Developer-profile opt-in higher-risk tools will live in
        # this map too; they register through the same undo check as everything else.
        registry.register(all_tools[tool_id])
    return registry


def default_db_path() -> str:
    # An explicit override keeps tests and throwaway dev runs off the real
    # ~/.addison store; the shell never sets it in production.
    override = os.environ.get("ADDISON_DB_PATH")
    if override:
        return override
    # Local app-data dir, no system/admin path (design-doc §7.8).
    base = os.path.expanduser("~/.addison")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "addison.sqlite3")


class _InMemorySnapshotStore:
    """CLI/dev-only stand-in for ``memory.store.Store`` (spec §11 step 6).

    ``UndoManager.record()`` is the only method the CLI loop exercises, so this
    stub implements exactly that — appending to a list. The real SQLite-backed
    store (insert/query/prune of ``action_snapshots``) is built at step 6; do NOT
    grow this stub into it."""

    def __init__(self) -> None:
        self.snapshots: list[ActionSnapshot] = []

    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        self.snapshots.append(snapshot)


def _env_api_key() -> str:
    """Read the Anthropic key from the environment at the moment of use.

    CLI/dev-only key source. Read at call time (never cached at startup) so a
    rotated key is picked up without a restart, and so the key never lingers in
    Agent Core memory. The OS-keychain path (read by the Rust shell) replaces
    this when the desktop shell lands at step 7 (spec §5)."""
    return os.environ["ANTHROPIC_API_KEY"]


def _terminal_permission_handler(registry: ToolRegistry):
    """Terminal PermissionCard stand-in: plain-language ask, y/n answer.

    In the shell this consent is an IPC event the frontend renders; in the CLI
    harness we print the tool's plain-language label + description (this app's
    users are non-technical — CLAUDE.md) and read a yes/no from the terminal."""

    def handler(tool_id: str) -> PermissionStatus:
        definition = registry.get(tool_id).definition
        print()
        print(f"Addison would like to: {definition.label}")
        print(f"  {definition.description}")
        answer = input("Allow this? (y/n) ").strip().lower()
        if answer in ("y", "yes"):
            return PermissionStatus.GRANTED
        return PermissionStatus.DENIED

    return handler


def run_cli() -> None:
    """Drive the orchestration loop from the terminal, without the desktop shell.

    Build step 4 (spec §11): a working chat-with-tools loop is provable before the
    Tauri shell and IPC arrive at step 7. Everything shell-specific here — the
    env-var key source and the terminal permission prompt — is the CLI/dev path
    only, replaced by the keychain + PermissionCard IPC later.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Never print or log the key itself — just tell the user how to set it.
        print(
            "Addison needs your Anthropic API key before it can start.\n"
            "Set it, then run again:  export ANTHROPIC_API_KEY=your-key-here"
        )
        raise SystemExit(1)

    profile = resolve_active_profile()
    registry = build_registry(profile)
    permission_gate = PermissionGate(on_request=_terminal_permission_handler(registry))

    provider = AnthropicProvider(model="claude-opus-4-8", api_key_getter=_env_api_key)
    model_router = ModelRouter(configured={ModelRole.PRIMARY: provider})
    undo_manager = UndoManager(store=_InMemorySnapshotStore(), tool_registry=registry)

    orchestrator = Orchestrator(
        model_router=model_router,
        tool_registry=registry,
        permission_gate=permission_gate,
        undo_manager=undo_manager,
        stream_to_frontend=print,
    )

    conversation = Conversation(id="cli")
    print("Addison is ready. Type a message, or 'exit' to quit.")
    while True:
        try:
            user_input = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # leave the cursor on a fresh line
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        conversation.messages.append(Message(role="user", content=user_input))
        try:
            orchestrator.run_turn(conversation)
        except KeyboardInterrupt:
            print("\nStopped. You can type another message.")
            continue
        except RuntimeError as exc:
            # Providers raise RuntimeError with a user-ready plain-language
            # message (key rejected, service busy, offline...) — show it as-is.
            print(str(exc))
        except Exception:
            # No stack traces reach the user (CLAUDE.md): one plain sentence + a
            # next step. The underlying error is swallowed on purpose here.
            print(
                "Addison couldn't reach the model just now. Check your internet "
                "connection and that your API key is still valid, then try again."
            )


class JsonRpcServer:
    """The §7 JSON-RPC 2.0 stdio server, decoupled from the real stdin/stdout.

    Threading model:
      - the read loop (``_read_loop``) parses one frame per line and dispatches;
      - a single worker thread runs turns one at a time (``_worker_loop``), so a
        second ``conversation.sendMessage`` queues behind an in-flight turn;
      - permission and Core -> Shell round-trips block the *worker*, never the
        read loop, so the answering frame (``permission.respond`` / a shell
        response) is still received and can wake the waiter.

    All SQLite access is confined to the worker thread: ``sqlite3`` connections
    are usable only on the thread that opened them, so the ``Store`` (and the
    ``UndoManager`` / ``Orchestrator`` that reach it) are built lazily on the
    worker via ``_ensure_built`` — from a ``store_factory`` main() supplies — and
    every store-touching request (sendMessage, undo, rewind) runs there. Read-only,
    store-free requests (available roles, role selection) answer on the read loop
    so they aren't blocked behind an in-flight turn.

    Every outgoing frame — notification, response, or Core -> Shell request —
    goes through ``_write_frame`` under one lock; stdout therefore carries only
    JSON-RPC frames.
    """

    def __init__(
        self,
        *,
        reader,
        writer,
        tool_registry: ToolRegistry,
        store_factory,
        model_router: ModelRouter,
        shell_bridge: IpcShellBridge | None = None,
        conversation_id: str = "main",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()

        self.tool_registry = tool_registry
        self._store_factory = store_factory     # called once, on the worker thread
        self.model_router = model_router
        self._shell_bridge = shell_bridge
        if shell_bridge is not None:
            # The bridge sends its Core -> Shell requests through our locked writer.
            shell_bridge.bind_sender(self._write_frame)

        # The gate's consent prompt IS an IPC round-trip (§4.3): emit the card,
        # then block the worker on a per-tool Event until permission.respond lands.
        self.permission_gate = PermissionGate(on_request=self._on_permission_request)

        # Built on the worker thread by _ensure_built (SQLite thread affinity).
        self.store = None
        self.undo_manager: UndoManager | None = None
        self.orchestrator: Orchestrator | None = None

        self.conversation = Conversation(id=conversation_id)
        self._conversation_created = False
        self._message_ids: list[str] = []      # persisted id per conversation.messages entry
        self._next_role: ModelRole | None = None

        self._queue: queue.Queue = queue.Queue()
        self._perm_lock = threading.Lock()
        self._permission_waiters: dict[str, dict] = {}

    # --- lifecycle --------------------------------------------------------
    def run(self) -> None:
        worker = threading.Thread(target=self._worker_loop, name="turn-worker", daemon=True)
        worker.start()
        self._read_loop()
        self._queue.put(None)   # stop the worker once stdin closes

    def _ensure_built(self) -> None:
        """Build the SQLite-backed singletons on the worker thread (once)."""
        if self.orchestrator is not None:
            return
        self.store = self._store_factory()
        self.undo_manager = UndoManager(store=self.store, tool_registry=self.tool_registry)
        self.orchestrator = Orchestrator(
            model_router=self.model_router,
            tool_registry=self.tool_registry,
            permission_gate=self.permission_gate,
            undo_manager=self.undo_manager,
            stream_to_frontend=self._emit_stream_chunk,
            on_activity=self._emit_activity,
            shell_bridge=self._shell_bridge,
        )

    def _read_loop(self) -> None:
        while True:
            line = self._reader.readline()
            if line == "":       # EOF — the shell closed the pipe
                break
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                # A frame we can't parse has no id to answer to — drop it.
                continue
            self._dispatch(frame)

    # --- frame writing ----------------------------------------------------
    def _write_frame(self, frame: dict) -> None:
        line = json.dumps(frame) + "\n"
        with self._write_lock:
            self._writer.write(line)
            self._writer.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._write_frame({"jsonrpc": "2.0", "method": method, "params": params})

    def _respond(self, request_id, result) -> None:
        if request_id is None:
            return
        self._write_frame({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _respond_error(self, request_id, code: int, message: str) -> None:
        if request_id is None:
            return
        self._write_frame(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    # --- Core -> Frontend notifications -----------------------------------
    def _emit_stream_chunk(self, text: str | None) -> None:
        self._notify(Method.CONVERSATION_STREAM_CHUNK, {"text": text or ""})

    def _emit_activity(self, tool_id: str, label: str) -> None:
        self._notify(Method.TOOL_ACTIVITY_UPDATE, {"toolId": tool_id, "label": label})

    # --- dispatch ---------------------------------------------------------
    def _dispatch(self, frame: dict) -> None:
        method = frame.get("method")
        if method is None:
            # No method + an id => this is a response to one of OUR Core -> Shell
            # requests; route it to the bridge's pending map.
            if self._shell_bridge is not None:
                self._shell_bridge.resolve_response(
                    frame.get("id"), frame.get("result"), frame.get("error")
                )
            return

        request_id = frame.get("id")
        params = frame.get("params") or {}

        # Store-touching requests run on the worker (SQLite thread affinity);
        # permission answers and store-free reads answer here on the read loop.
        if method == Method.CONVERSATION_SEND_MESSAGE:
            self._queue.put(("send", params, request_id))
            return
        if method == Method.UNDO_UNDO_LAST_ACTION:
            self._queue.put(("undo", params, request_id))
            return
        if method == Method.UNDO_REWIND_CONVERSATION:
            self._queue.put(("rewind", params, request_id))
            return
        if method == Method.PERMISSION_RESPOND:
            self._handle_permission_respond(params, request_id)
            return

        try:
            if method == Method.MODEL_AVAILABLE_ROLES:
                self._respond(request_id, self._available_roles())
            elif method == Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE:
                self._handle_set_role(params, request_id)
            elif method in _NOT_BUILT_METHODS:
                # Routines (step 8) and local setup (step 10) — not built here.
                self._respond_error(request_id, _SERVER_ERROR, _NOT_BUILT_MESSAGE)
            else:
                self._respond_error(
                    request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}"
                )
        except Exception as exc:  # never let a handler crash the read loop
            self._respond_error(request_id, _SERVER_ERROR, _plain(exc))

    # --- worker thread (all SQLite-backed work) ---------------------------
    def _worker_loop(self) -> None:
        self._ensure_built()
        while True:
            job = self._queue.get()
            if job is None:
                break
            kind, params, request_id = job
            try:
                if kind == "send":
                    self._run_send_message(params, request_id)
                elif kind == "undo":
                    self._respond(request_id, self._undo_last_action())
                elif kind == "rewind":
                    self._handle_rewind(params, request_id)
            except RuntimeError as exc:
                # Provider/tool errors already carry a plain, user-ready sentence.
                self._respond_error(request_id, _SERVER_ERROR, str(exc))
            except Exception:
                # Anything else collapses to one plain message — no stack trace.
                self._respond_error(request_id, _SERVER_ERROR, _GENERIC_TURN_ERROR)

    def _run_send_message(self, params: dict, request_id) -> None:
        text = params.get("text", "")
        requested_role = self._role_from(params.get("role")) or self._next_role
        self._next_role = None

        self._ensure_conversation()
        user_msg = Message(role="user", content=text)
        self.conversation.messages.append(user_msg)
        self._persist_message(user_msg)

        pre_turn = len(self.conversation.messages)
        self.orchestrator.run_turn(self.conversation, requested_role=requested_role)

        # Full-transcript persistence (§4.8 substrate): every message the turn
        # appended, in order, so a later rewind can target any of them by id.
        for msg in self.conversation.messages[pre_turn:]:
            self._persist_message(msg)
        self._respond(request_id, {"ok": True})

    def _ensure_conversation(self) -> None:
        if self._conversation_created:
            return
        self.store.create_conversation(
            id=self.conversation.id,
            title=None,
            provider_id="primary",
            started_at=int(time.time()),
        )
        self._conversation_created = True

    def _persist_message(self, message: Message) -> None:
        message_id = str(uuid4())
        self.store.insert_message(
            id=message_id,
            conversation_id=self.conversation.id,
            role=message.role,
            content=str(message.content),
            created_at=int(time.time()),
            tool_call_id=message.tool_call_id,
        )
        self._message_ids.append(message_id)

    # --- permissions ------------------------------------------------------
    def _on_permission_request(self, tool_id: str) -> PermissionStatus:
        """Runs on the worker thread: render the card, block for the answer."""
        definition = self.tool_registry.get(tool_id).definition
        event = threading.Event()
        with self._perm_lock:
            self._permission_waiters[tool_id] = {"event": event, "allow": False}
        self._notify(
            Method.PERMISSION_REQUEST_GRANT,
            {
                "toolId": tool_id,
                "label": definition.label,
                "description": definition.description,
                "riskTier": definition.risk_tier.value,
            },
        )
        event.wait()
        with self._perm_lock:
            waiter = self._permission_waiters.pop(tool_id, None)
        allow = bool(waiter and waiter["allow"])
        return PermissionStatus.GRANTED if allow else PermissionStatus.DENIED

    def _handle_permission_respond(self, params: dict, request_id) -> None:
        tool_id = params.get("toolId")
        allow = bool(params.get("allow"))
        with self._perm_lock:
            waiter = self._permission_waiters.get(tool_id)
            if waiter is not None:
                waiter["allow"] = allow
                waiter["event"].set()
        self._respond(request_id, {"ok": True})

    # --- undo / rewind ----------------------------------------------------
    def _undo_last_action(self) -> dict:
        results = self.undo_manager.undo_last(1)
        if not results:
            return {"ok": False, "detail": "There was nothing to undo."}
        result = results[0]
        if result.success:
            return {"ok": True, "detail": f"Undid the last action ({self._label(result.tool_id)})."}
        return {
            "ok": False,
            "detail": "Couldn't undo the last action. You may need to reverse it yourself.",
        }

    def _handle_rewind(self, params: dict, request_id) -> None:
        to_message_id = params.get("toMessageId")
        try:
            # Store truncation first; it raises if the id isn't in this conversation.
            self.undo_manager.rewind_conversation(self.conversation.id, to_message_id)
        except KeyError:
            self._respond_error(
                request_id, _SERVER_ERROR, "Couldn't find that point to rewind to."
            )
            return
        # Mirror the truncation in the in-memory conversation, keeping the anchor.
        if to_message_id in self._message_ids:
            idx = self._message_ids.index(to_message_id)
            del self.conversation.messages[idx + 1:]
            del self._message_ids[idx + 1:]
        self._respond(request_id, {"ok": True, "detail": "Rewound the conversation."})

    # --- model roles ------------------------------------------------------
    def _available_roles(self) -> dict:
        return {
            "roles": [role.value for role in self.model_router.available_roles()],
            "localModels": self.model_router.available_local_models(),
        }

    def _handle_set_role(self, params: dict, request_id) -> None:
        role = self._role_from(params.get("role"))
        if params.get("role") and role is None:
            self._respond_error(
                request_id, _SERVER_ERROR, "That model option isn't available."
            )
            return
        self._next_role = role
        self._respond(request_id, {"ok": True})

    # --- helpers ----------------------------------------------------------
    def _label(self, tool_id: str) -> str:
        try:
            return self.tool_registry.get(tool_id).definition.label
        except KeyError:
            return tool_id

    @staticmethod
    def _role_from(role: str | None) -> ModelRole | None:
        if not role:
            return None
        try:
            return ModelRole(role)
        except ValueError:
            return None


# Methods that belong to later build steps — answered with a plain "not built"
# error rather than a silent failure (steps 8 and 10; do NOT implement here).
_NOT_BUILT_METHODS = {
    Method.ROUTINE_PROPOSE_FROM_CONVERSATION,
    Method.ROUTINE_CONFIRM_SAVE,
    Method.ROUTINE_LIST,
    Method.ROUTINE_RUN,
    Method.ROUTINE_DELETE,
    Method.MODEL_START_LOCAL_SETUP,
}


def _plain(exc: Exception) -> str:
    """A user-ready sentence for a handler failure — never the raw exception."""
    if isinstance(exc, RuntimeError) and str(exc):
        return str(exc)
    return _GENERIC_TURN_ERROR


def main() -> None:
    profile = resolve_active_profile()          # §4.7 — SIMPLE until step 11 persists a choice
    shell_bridge = IpcShellBridge()             # sender bound by the server below
    registry = build_registry(profile, shell_bridge=shell_bridge)

    # The real SQLite Store + UndoManager are built by the server on its worker
    # thread (sqlite3 connections are single-thread), so main() supplies a factory
    # rather than a live connection. ADDISON_DB_PATH keeps dev/tests off ~/.addison.
    db_path = default_db_path()

    def _store_factory() -> Store:
        return Store(db_path)

    def _api_key_getter() -> str:
        # Per-call key fetch from the OS keychain via the shell (§5), kept only in
        # this local. If the shell reports no key (or isn't reachable), fall back
        # to the env var so the core is runnable in dev without the desktop shell.
        # DEV FALLBACK — remove once BYOK-via-keychain is the only path.
        try:
            key = shell_bridge.get_provider_key("primary")
        except RuntimeError:
            key = ""
        return key or os.environ.get("ANTHROPIC_API_KEY", "")

    provider = AnthropicProvider(model="claude-opus-4-8", api_key_getter=_api_key_getter)
    model_router = ModelRouter(configured={ModelRole.PRIMARY: provider})

    server = JsonRpcServer(
        reader=sys.stdin,
        writer=sys.stdout,
        tool_registry=registry,
        store_factory=_store_factory,
        model_router=model_router,
        shell_bridge=shell_bridge,
    )
    # TODO(step 11): use profile.onboarding to pick Setup Assistant vs. BYOK-first,
    #                and expose profile.{headless_cli,raw_diagnostics,...} to the frontend.
    server.run()


if __name__ == "__main__":
    # `--cli` runs the step-4 terminal harness; the bare entry point runs the
    # step-7 JSON-RPC stdio loop the desktop shell speaks to.
    if "--cli" in sys.argv[1:]:
        run_cli()
    else:
        main()
