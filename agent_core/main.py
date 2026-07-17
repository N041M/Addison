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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from agent_core.memory.store import Store
from agent_core.models_catalog import (
    CloudModel,
    default_cloud_model,
    fetch_cloud_catalog,
    find_cloud_model,
    load_cloud_catalog,
)
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.profiles import Profile, resolve_active_profile
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.ollama_provider import (
    OllamaProvider,
    approx_requirements,
    is_running,
    pull_model,
)
from agent_core.providers.router import ModelRouter
from agent_core.providers.setup_assistant_provider import (
    DEFAULT_RELAY_URL,
    SetupAssistantProvider,
)
from agent_core.routines.builder import RoutineBuilder
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.library import RoutineLibrary
from agent_core.routines.model import RoutineStep
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
# Plain-language model-picker refusals (§4.1.1; CLAUDE.md: no jargon).
_MODEL_UNAVAILABLE_MESSAGE = "That model option isn't available."
_EFFORT_UNAVAILABLE_MESSAGE = "That answer-style isn't available for this model."
_GENERIC_TURN_ERROR = (
    "Addison couldn't finish that just now. Check your internet connection and "
    "that your API key is still valid, then try again."
)

# Local-setup (§4.1.2) plain-language messages. Addison does NOT install Ollama
# in v1 — it points the user at doing that themselves.
_OLLAMA_NOT_INSTALLED_MESSAGE = (
    "Ollama isn't running on this computer. Install it from ollama.com (or start "
    "it if it's already installed), then try again — Addison can't install it for you."
)
_LOCAL_SETUP_BUSY_MESSAGE = (
    "Addison is already setting up a model. Let that one finish before starting another."
)

_GB = 1024**3


def _free_disk_bytes() -> int | None:
    """Free disk space in the user's home volume, or None if it can't be read."""
    try:
        return shutil.disk_usage(os.path.expanduser("~")).free
    except OSError:
        return None


def _total_ram_bytes() -> int | None:
    """Total physical RAM in bytes (macOS ``sysctl -n hw.memsize``).

    Any failure is a "couldn't check" — return None and let the caller SKIP the
    RAM gate rather than block setup on an unknowable value (§4.1.2 step 2)."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _pull_progress(update: dict) -> tuple[int | None, str | None]:
    """Turn one Ollama ``/api/pull`` NDJSON line into (percent, plain message).

    Only byte-progress lines (``total``/``completed``) get a user-facing message,
    so no Ollama jargon ("verifying sha256 digest") leaks into the UI. Returns
    ``(None, None)`` for lines with nothing worth showing."""
    total = update.get("total")
    completed = update.get("completed")
    if isinstance(total, (int, float)) and total and isinstance(completed, (int, float)):
        percent = max(0, min(100, int(completed / total * 100)))
        return percent, f"Downloading the model — {percent}%"
    return None, None


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


_SETUP_PROMPT_PATH = Path(__file__).resolve().parent / "providers" / "prompts" / "setup_assistant.txt"


def load_setup_prompt() -> str:
    """The Setup Assistant system prompt (§4.6), injected for a turn when no
    PRIMARY key is configured yet. Read at startup — it is bundled with the app,
    not user data."""
    return _SETUP_PROMPT_PATH.read_text(encoding="utf-8")


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
    every store-touching request (sendMessage, undo, rewind) runs there. Fast
    store-free reads (role selection) answer on the read loop so they aren't blocked
    behind an in-flight turn. ``availableRoles`` also runs on the worker, not the
    read loop: it may lazily fetch the live cloud-model list, which does a Core ->
    Shell key probe and an outbound HTTPS call — both block on frames the read loop
    must stay free to deliver, so they can never run on the read loop itself.

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
        primary_key_probe=None,
        setup_prompt: str | None = None,
        ollama_base_url: str | None = None,
        ollama_client=None,
        cloud_catalog: list[CloudModel] | None = None,
        cloud_fetcher=None,
        cloud_provider_factory=None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()

        self.tool_registry = tool_registry
        self._store_factory = store_factory     # called once, on the worker thread
        self.model_router = model_router
        self._shell_bridge = shell_bridge
        # The cloud-model menu (models_catalog.py) the picker renders and validates
        # explicit picks against. It starts as the built-in fallback (or empty in
        # CLI/some tests — then modelId/effort are unvalidated and flow to resolve()).
        # The FIRST availableRoles once a PRIMARY key is available swaps in the live
        # list of every model the key can access (``_maybe_load_live_catalog``).
        self._cloud_catalog = list(cloud_catalog or [])
        # ``cloud_fetcher`` is a ()-> list[CloudModel] that returns the live catalog
        # (raising on failure); ``cloud_provider_factory`` is a (CloudModel)-> provider
        # that builds one provider per fetched entry. Both None (CLI/tests without them)
        # means no live fetch ever runs — the fallback stands.
        self._cloud_fetcher = cloud_fetcher
        self._cloud_provider_factory = cloud_provider_factory
        self._cloud_catalog_loaded = False
        # Local-setup (§4.1.2) talks to Ollama over HTTP. base_url/client default
        # to the real localhost instance; tests inject an httpx.MockTransport
        # client so no real Ollama (or network) is ever touched.
        self._ollama_base_url = ollama_base_url
        self._ollama_client = ollama_client
        # §4.6 Setup Assistant handoff: with no PRIMARY key yet, a turn runs on the
        # SETUP_ASSISTANT relay under its onboarding system prompt. ``primary_key_probe``
        # is a ()-> bool that reports whether a real PRIMARY key is available right now
        # (it re-reads the keychain per call, so the handoff needs no other state). When
        # None (CLI/tests), the key is treated as present — normal PRIMARY routing.
        self._primary_key_probe = primary_key_probe
        self._setup_prompt = setup_prompt
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
        self.routine_builder: RoutineBuilder | None = None
        self.routine_library: RoutineLibrary | None = None
        self.routine_engine: RoutineEngine | None = None

        self.conversation = Conversation(id=conversation_id)
        self._conversation_created = False
        self._message_ids: list[str] = []      # persisted id per conversation.messages entry
        self._next_role: ModelRole | None = None
        self._next_model_name: str | None = None   # explicit LOCAL/cloud pick, §4.1.1, §6.8
        self._next_effort: str | None = None       # explicit "answer style" for next msg
        self._draft_routine = None             # pending §6.3 proposal awaiting confirmSave

        self._queue: queue.Queue = queue.Queue()
        self._perm_lock = threading.Lock()
        self._permission_waiters: dict[str, dict] = {}
        # Only one local-model setup may run at a time (§4.1.2); the flag is held
        # from pre-flight through the background pull/verify.
        self._local_setup_lock = threading.Lock()
        self._local_setup_active = False

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
        self.routine_builder = RoutineBuilder(store=self.store)
        self.routine_library = RoutineLibrary(store=self.store)
        # INVARIANT (§6.4, §8.5): the engine shares the orchestrator's exact
        # gate/registry/undo instances — a Routine can never out-permission the
        # live conversation.
        self.routine_engine = RoutineEngine(
            tool_registry=self.tool_registry,
            permission_gate=self.permission_gate,
            undo_manager=self.undo_manager,
            shell_bridge=self._shell_bridge,
            on_ask_user=self._ask_user_continue,
            store=self.store,
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
        if method == Method.MODEL_AVAILABLE_ROLES:
            # On the worker (not here): it may fetch the live cloud-model list, which
            # does a Core -> Shell key probe + an HTTPS call that must not block the
            # read loop (see the class docstring's threading model).
            self._queue.put(("available_roles", params, request_id))
            return
        if method in _ROUTINE_JOBS:
            self._queue.put((_ROUTINE_JOBS[method], params, request_id))
            return

        try:
            if method == Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE:
                self._handle_set_role(params, request_id)
            elif method == Method.MODEL_START_LOCAL_SETUP:
                # §4.1.2: pre-flight (reachability + hardware) answers here; the
                # long pull/verify runs on a background thread and streams progress.
                self._handle_start_local_setup(params, request_id)
            elif method in _NOT_BUILT_METHODS:
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
                elif kind == "available_roles":
                    self._maybe_load_live_catalog()
                    self._respond(request_id, self._available_roles())
                elif kind == "undo":
                    self._respond(request_id, self._undo_last_action())
                elif kind == "rewind":
                    self._handle_rewind(params, request_id)
                elif kind == "routine_propose":
                    self._handle_routine_propose(request_id)
                elif kind == "routine_confirm":
                    self._handle_routine_confirm(params, request_id)
                elif kind == "routine_list":
                    self._respond(request_id, {"routines": self._routine_rows()})
                elif kind == "routine_run":
                    self._handle_routine_run(params, request_id)
                elif kind == "routine_delete":
                    self.routine_library.delete(params.get("routineId"))
                    self._respond(request_id, {"ok": True})
            except RuntimeError as exc:
                # Provider/tool errors already carry a plain, user-ready sentence.
                self._respond_error(request_id, _SERVER_ERROR, str(exc))
            except Exception:
                # Anything else collapses to one plain message — no stack trace.
                self._respond_error(request_id, _SERVER_ERROR, _GENERIC_TURN_ERROR)

    def _run_send_message(self, params: dict, request_id) -> None:
        text = params.get("text", "")
        requested_role = self._role_from(params.get("role")) or self._next_role
        # §4.1.1 / §6.8: thread the explicit model pick (per-message param or the last
        # setRole) into resolve(); resolve() picks the named LOCAL/cloud model and
        # falls back gracefully if the name is unknown. ``effort`` is the per-message
        # "answer style" — validated against the chosen model, then threaded to send().
        model_name = params.get("modelId") or self._next_model_name
        effort = params.get("effort") or self._next_effort
        self._next_role = None
        self._next_model_name = None
        self._next_effort = None

        error = self._selection_error(requested_role, model_name, effort)
        if error is not None:
            self._respond_error(request_id, _SERVER_ERROR, error)
            return

        self._ensure_conversation()
        user_msg = Message(role="user", content=text)
        self.conversation.messages.append(user_msg)
        self._persist_message(user_msg)

        # §4.6 handoff: a PRIMARY-bound turn with no key yet routes to the Setup
        # Assistant, with its system prompt injected FOR THIS TURN ONLY. The prompt
        # is never persisted and never enters the stored transcript (which also can't
        # hold a "system" role — messages.role CHECK is user/assistant/tool). Once a
        # key exists, the probe passes and turns go to PRIMARY, history untouched —
        # that IS the handoff; no transcript rewrite, no state to flip.
        system_msg = None
        if requested_role in (None, ModelRole.PRIMARY) and not self._primary_key_available():
            requested_role = ModelRole.SETUP_ASSISTANT
            if self._setup_prompt:
                system_msg = Message(role="system", content=self._setup_prompt)
                self.conversation.messages.insert(0, system_msg)

        pre_turn = len(self.conversation.messages)
        try:
            self.orchestrator.run_turn(
                self.conversation,
                requested_role=requested_role,
                model_name=model_name,
                effort=effort,
            )
            # Full-transcript persistence (§4.8 substrate): every message the turn
            # appended, in order, so a later rewind can target any of them by id.
            for msg in self.conversation.messages[pre_turn:]:
                self._persist_message(msg)
        finally:
            # Drop the transient system prompt so it never lingers in history and
            # in-memory messages stay aligned 1:1 with the persisted _message_ids.
            if system_msg is not None:
                try:
                    self.conversation.messages.remove(system_msg)
                except ValueError:
                    pass
        self._respond(request_id, {"ok": True})

    def _primary_key_available(self) -> bool:
        probe = self._primary_key_probe
        if probe is None:
            return True   # CLI/tests: no probe wired -> treat PRIMARY as ready
        try:
            return bool(probe())
        except Exception:
            # A wedged/failing keychain probe shouldn't strand onboarding — fall
            # back to the Setup Assistant path rather than erroring the turn.
            return False

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

    # --- routines (§6) ----------------------------------------------------
    def _handle_routine_propose(self, request_id) -> None:
        """§6.3: draft a Routine from the recent conversation and hand the
        frontend a plain-language preview. NOTHING is saved yet — the draft
        waits for routine.confirmSave."""
        try:
            draft = self.routine_builder.propose_from_recent_actions(self.conversation)
        except ValueError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        self._draft_routine = draft
        self._respond(request_id, self.routine_builder.preview(draft, self.tool_registry))

    def _handle_routine_confirm(self, params: dict, request_id) -> None:
        draft = self._draft_routine
        if draft is None:
            self._respond_error(
                request_id, _SERVER_ERROR, "There's no routine waiting to be saved."
            )
            return
        # The user may rename/redescribe in the confirmation card (§6.3).
        if params.get("name"):
            draft.name = str(params["name"])
        if params.get("description"):
            draft.description = str(params["description"])
        self.routine_builder.save(draft, conversation_id=self.conversation.id)
        self._draft_routine = None
        self._respond(request_id, {"ok": True, "routineId": draft.id})

    def _routine_rows(self) -> list[dict]:
        rows = []
        for entry in self.routine_library.list():
            routine = entry["routine"]
            rows.append(
                {
                    "id": routine.id,
                    "name": routine.name,
                    "description": routine.description,
                    "runCount": entry["runCount"],
                    "lastRunAt": entry["lastRunAt"],
                    "variables": [
                        {"name": v.name, "prompt": v.prompt, "default": v.default}
                        for v in routine.variables
                    ],
                }
            )
        return rows

    def _handle_routine_run(self, params: dict, request_id) -> None:
        try:
            routine = self.routine_library.get(params.get("routineId"))
        except KeyError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        result = self.routine_engine.run(routine, params.get("variables") or {})
        self.routine_library.record_run(routine.id)
        self._respond(
            request_id,
            {
                "ok": result.status == "completed",
                "status": result.status,
                "detail": result.detail,
                "steps": [
                    {
                        "stepId": step_id,
                        "ok": step_result.success,
                        "summary": str(step_result.content)[:200],
                    }
                    for step_id, step_result in result.step_results.items()
                ],
            },
        )

    def _ask_user_continue(self, step: RoutineStep, run_id: str, message: str) -> bool:
        """§6.2 on_failure="ask_user": pause the run and ask, reusing the exact
        permission-card round-trip — the frontend renders label/description and
        answers via permission.respond with this synthetic toolId."""
        waiter_key = f"routine-step:{run_id}:{step.step_id}"
        event = threading.Event()
        with self._perm_lock:
            self._permission_waiters[waiter_key] = {"event": event, "allow": False}
        self._notify(
            Method.PERMISSION_REQUEST_GRANT,
            {
                "toolId": waiter_key,
                "label": "Keep going with this routine?",
                "description": (
                    f"One step didn't work: {message} "
                    "Addison can keep going with the rest, or stop here."
                ),
                "riskTier": "low",
            },
        )
        event.wait()
        with self._perm_lock:
            waiter = self._permission_waiters.pop(waiter_key, None)
        return bool(waiter and waiter["allow"])

    # --- model roles ------------------------------------------------------
    def _maybe_load_live_catalog(self) -> None:
        """First availableRoles once a PRIMARY key exists: replace the built-in
        fallback with the live list of every model the key can access, and register a
        provider per fetched entry so by-name picks resolve to it.

        Runs on the worker (never the read loop): the key probe and the fetch each do
        round-trips that block on frames the read loop must stay free to deliver. Any
        failure — no key, offline, a bad response — keeps the fallback and leaves the
        door open to retry on a later availableRoles call (nothing is marked loaded).
        Registration is idempotent (dict replace), so repeated calls are safe. The
        frontend always sends an explicit modelId (the live default's id after a swap),
        so the router's fallback selection is left as-is."""
        if self._cloud_catalog_loaded or self._cloud_fetcher is None:
            return
        if not self._primary_key_available():
            return
        try:
            catalog = self._cloud_fetcher()
        except Exception:
            return   # keep the fallback; a later availableRoles may succeed
        if not catalog:
            return

        self._cloud_catalog = catalog
        self._cloud_catalog_loaded = True
        if self._cloud_provider_factory is not None:
            for entry in catalog:
                self.model_router.register_primary_model(
                    entry.id, self._cloud_provider_factory(entry)
                )

    def _available_roles(self) -> dict:
        return {
            # SETUP_ASSISTANT is an internal onboarding role, never a user-selectable
            # option in the model picker (§4.1.1) — surface only PRIMARY/LOCAL.
            "roles": [
                role.value
                for role in self.model_router.available_roles()
                if role is not ModelRole.SETUP_ASSISTANT
            ],
            "localModels": self.model_router.available_local_models(),
            # The curated cloud menu the PRIMARY picker renders (§4.1.1, §6.8): each
            # entry carries its plain-language label/description and its "answer style"
            # (effort) choices — empty for a model with no effort control.
            "cloudModels": [model.to_wire() for model in self._cloud_catalog],
        }

    def _selection_error(
        self, role: ModelRole | None, model_id: str | None, effort: str | None
    ) -> str | None:
        """Validate an explicit model + effort pick for one message. Returns a plain
        error string, or None when the pick is allowed. A LOCAL pick names a local
        model and takes no effort; a PRIMARY (or default) pick names a cloud model
        and its effort must be one the model supports. An unknown id fails plainly
        HERE (early, explicit) rather than silently falling back at send time — the
        router keeps its own mid-conversation fallback as a separate safety net."""
        if role is ModelRole.LOCAL:
            if model_id is not None and model_id not in self.model_router.available_local_models():
                return _MODEL_UNAVAILABLE_MESSAGE
            if effort is not None:
                return _EFFORT_UNAVAILABLE_MESSAGE
            return None
        # PRIMARY, or role unset (which defaults to PRIMARY): a cloud pick.
        if model_id is not None and self._cloud_catalog:
            if find_cloud_model(self._cloud_catalog, model_id) is None:
                return _MODEL_UNAVAILABLE_MESSAGE
        if effort is not None:
            model = self._cloud_model_for(model_id)
            if model is None or effort not in model.supported_effort:
                return _EFFORT_UNAVAILABLE_MESSAGE
        return None

    def _cloud_model_for(self, model_id: str | None):
        """The catalog entry a cloud effort is validated against: the named model, or
        the catalog default when no model is named. None if there's no catalog or the
        named id isn't in it."""
        if not self._cloud_catalog:
            return None
        if model_id is None:
            return default_cloud_model(self._cloud_catalog)
        return find_cloud_model(self._cloud_catalog, model_id)

    def _handle_set_role(self, params: dict, request_id) -> None:
        role = self._role_from(params.get("role"))
        if params.get("role") and role is None:
            self._respond_error(request_id, _SERVER_ERROR, _MODEL_UNAVAILABLE_MESSAGE)
            return
        # An explicit pick may name WHICH model (a LOCAL model, item B, or a cloud
        # model, §6.8) and an "answer style" (effort). Validate both against the
        # configured pools/catalog so a stale/typo'd id or unsupported effort fails
        # plainly here rather than silently falling back at send time.
        model_id = params.get("modelId") or None
        effort = params.get("effort") or None
        error = self._selection_error(role, model_id, effort)
        if error is not None:
            self._respond_error(request_id, _SERVER_ERROR, error)
            return
        self._next_role = role
        self._next_model_name = model_id
        self._next_effort = effort
        self._respond(request_id, {"ok": True})

    # --- local model setup (§4.1.2) ---------------------------------------
    def _handle_start_local_setup(self, params: dict, request_id) -> None:
        """Steps 1-2 (reachability + hardware) answer via the RPC response; on
        success the pull/verify (steps 3-4) run on a background thread so the
        server stays responsive, streaming ``model.localSetupProgress``."""
        model_name = str(params.get("modelName") or "").strip()
        if not model_name:
            self._respond_error(request_id, _SERVER_ERROR, "Choose a model to set up first.")
            return

        with self._local_setup_lock:
            if self._local_setup_active:
                self._respond_error(request_id, _SERVER_ERROR, _LOCAL_SETUP_BUSY_MESSAGE)
                return
            self._local_setup_active = True

        # The flag is now held; every path that does NOT start the worker must
        # release it (the worker owns releasing it once started).
        started = False
        try:
            if not is_running(self._ollama_base_url, self._ollama_client):
                self._respond_error(request_id, _SERVER_ERROR, _OLLAMA_NOT_INSTALLED_MESSAGE)
                return
            refusal = self._hardware_refusal(model_name)
            if refusal is not None:
                self._respond_error(request_id, _SERVER_ERROR, refusal)
                return
            thread = threading.Thread(
                target=self._run_local_setup,
                args=(model_name,),
                name="local-setup",
                daemon=True,
            )
            thread.start()
            started = True
            self._respond(request_id, {"ok": True, "started": True})
        finally:
            if not started:
                with self._local_setup_lock:
                    self._local_setup_active = False

    def _hardware_refusal(self, model_name: str) -> str | None:
        """Plain-language refusal if disk/RAM is clearly insufficient, else None
        (design-doc §7.3.2 — name real GB numbers, never parameter counts)."""
        need = approx_requirements(model_name)
        free_disk = _free_disk_bytes()
        if free_disk is not None and free_disk < need["disk_gb"] * _GB:
            return (
                f"This model needs about {need['disk_gb']:.0f} GB of free space, but "
                f"only {free_disk / _GB:.0f} GB is free on this computer. Free up some "
                "space and try again."
            )
        total_ram = _total_ram_bytes()
        if total_ram is not None and total_ram < need["ram_gb"] * _GB:
            return (
                f"This model needs about {need['ram_gb']:.0f} GB of memory, but this "
                f"computer has about {total_ram / _GB:.0f} GB, so it wouldn't run well "
                "and isn't available."
            )
        return None

    def _run_local_setup(self, model_name: str) -> None:
        """Background worker: pull (step 3) → verify (step 4) → register. Every
        outcome is a ``model.localSetupProgress`` notification; nothing raises out
        of the thread."""
        try:
            self._emit_local_progress("downloading", "Getting the download started...", None)
            for update in pull_model(model_name, self._ollama_base_url, self._ollama_client):
                percent, message = _pull_progress(update)
                if message is not None:
                    self._emit_local_progress("downloading", message, percent)

            self._emit_local_progress("verifying", "Checking the model works...", None)
            provider = OllamaProvider(model_name, self._ollama_base_url, self._ollama_client)
            provider.send([Message(role="user", content="Hello")], [])

            # Verified: register it so ModelRole.LOCAL and the Local picker appear.
            self.model_router.register_local_model(model_name, provider)
            self._emit_local_progress("done", f"{model_name} is ready to use.", 100)
        except RuntimeError as exc:
            # Provider/pull errors already carry a plain, user-ready sentence.
            self._emit_local_progress("error", str(exc), None)
        except Exception:
            self._emit_local_progress("error", _GENERIC_TURN_ERROR, None)
        finally:
            with self._local_setup_lock:
                self._local_setup_active = False

    def _emit_local_progress(self, stage: str, message: str, percent: int | None) -> None:
        payload: dict = {"stage": stage, "message": message}
        if percent is not None:
            payload["percent"] = percent
        self._notify(Method.MODEL_LOCAL_SETUP_PROGRESS, payload)

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
# error rather than a silent failure. Empty now that step 10 (local setup) is
# built; kept as the seam for any future not-yet-implemented §7 method.
_NOT_BUILT_METHODS: set[str] = set()

# routine.* methods all touch the Store, so they run on the worker (§ threading
# model in JsonRpcServer's docstring). Method -> worker job kind.
_ROUTINE_JOBS = {
    Method.ROUTINE_PROPOSE_FROM_CONVERSATION: "routine_propose",
    Method.ROUTINE_CONFIRM_SAVE: "routine_confirm",
    Method.ROUTINE_LIST: "routine_list",
    Method.ROUTINE_RUN: "routine_run",
    Method.ROUTINE_DELETE: "routine_delete",
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

    def _primary_key_available() -> bool:
        # §4.6 probe: reuse the exact PRIMARY getter — no key means this turn runs
        # on the Setup Assistant relay instead. Read fresh each turn, so adding a
        # key mid-conversation flips routing to PRIMARY with no restart.
        return bool(_api_key_getter())

    def _build_cloud_provider(entry: CloudModel) -> AnthropicProvider:
        # One AnthropicProvider per catalog entry — all sharing the SAME key-getter
        # (one key, several models) — carrying that entry's adaptive-thinking flag and
        # supported effort levels. Used for the fallback pool at startup AND for each
        # live-fetched model (JsonRpcServer._maybe_load_live_catalog).
        return AnthropicProvider(
            model=entry.id,
            api_key_getter=_api_key_getter,
            adaptive_thinking=entry.adaptive_thinking,
            supported_effort=entry.supported_effort,
        )

    def _fetch_live_catalog() -> list[CloudModel]:
        # Every model _api_key_getter's key can access (§4.1.1); raises on any failure,
        # which the server catches to keep the fallback catalog.
        return fetch_cloud_catalog(_api_key_getter)

    # The cloud menu starts as the built-in fallback (models_catalog.py); the server
    # swaps in the live list on the first availableRoles once a key is present.
    # ADDISON_MODEL is a dev/test knob (like ADDISON_DB_PATH): it moves the default
    # onto a cheaper model for live sweeps without touching the shipped fallback.
    catalog = load_cloud_catalog()
    default_model = default_cloud_model(catalog)
    cloud_providers = {entry.id: _build_cloud_provider(entry) for entry in catalog}
    default_provider = cloud_providers[default_model.id]

    # SETUP_ASSISTANT is a distinct role that never holds a provider key — the shell
    # signs each relay request with the device key (§5). It sits alongside PRIMARY;
    # the §4.6 handoff is additive (PRIMARY populated), never a destructive swap.
    setup_provider = SetupAssistantProvider(
        shell_bridge=shell_bridge,
        relay_url=os.environ.get("ADDISON_RELAY_URL", DEFAULT_RELAY_URL),
    )
    model_router = ModelRouter(
        configured={
            ModelRole.PRIMARY: default_provider,      # the default/fallback cloud model
            ModelRole.SETUP_ASSISTANT: setup_provider,
        }
    )
    # Register the whole cloud pool for by-name picks (§6.8). Register the default
    # first so it is also the pool's selected default, consistent with configured[].
    model_router.register_primary_model(default_model.id, default_provider)
    for entry in catalog:
        if entry.id != default_model.id:
            model_router.register_primary_model(entry.id, cloud_providers[entry.id])

    server = JsonRpcServer(
        reader=sys.stdin,
        writer=sys.stdout,
        tool_registry=registry,
        store_factory=_store_factory,
        model_router=model_router,
        shell_bridge=shell_bridge,
        primary_key_probe=_primary_key_available,
        setup_prompt=load_setup_prompt(),
        cloud_catalog=catalog,
        cloud_fetcher=_fetch_live_catalog,
        cloud_provider_factory=_build_cloud_provider,
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
