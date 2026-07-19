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
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent_core.memory.store import Store
from agent_core.models_catalog import (
    PROVIDER_IDS,
    CatalogFetchError,
    CloudModel,
    default_cloud_model,
    fetch_cloud_catalog,
    find_cloud_model,
    load_cloud_catalog,
    merge_catalogs,
    provider_label,
    static_catalog_for,
)
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, mode_for_profile
from agent_core.profiles import (
    DEVELOPER,
    SIMPLE,
    Profile,
    ProfileId,
    get_profile,
    resolve_active_profile,
)
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.google_provider import list_models as google_list_models
from agent_core.providers.ollama_provider import (
    OllamaProvider,
    approx_requirements,
    is_running,
    pull_model,
)
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.openai_provider import list_models as openai_list_models
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
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    call_is_destructive,
    call_permission_detail,
)
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import RunCommandTool
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.web_search import WebSearchTool
from agent_core.widgets import MAX_PINNED, validate_widget_spec, widget_summary

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
# §4.7 Developer profile is BYOK-first: with no key it asks the user to add their own
# rather than routing to the Setup Assistant relay (which is the Simple onboarding).
_BYOK_ONBOARDING_MESSAGE = (
    "No API key is set up yet. Add your Anthropic API key in Settings."
)
_UNKNOWN_PROFILE_MESSAGE = "That profile isn't available."

_GB = 1024**3

# §4.8 usage-log retention. Keep ~6 months of usage rows; prune opportunistically
# from the record path so the table can't grow without bound. The prune runs once
# every _USAGE_PRUNE_EVERY records (a cheap in-process counter, not on every write).
_USAGE_RETENTION_SECONDS = 183 * 24 * 60 * 60   # ~6 months, in epoch seconds
_USAGE_PRUNE_EVERY = 50                          # records between opportunistic prunes


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


def _auto_title(text: str) -> str | None:
    """Derive a conversation title from its first user message: whitespace runs
    collapsed to single spaces, trimmed to the first 60 characters (with an
    ellipsis when something was cut). None for an effectively empty message —
    the history list then falls back to "Untitled"."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) > 60:
        return collapsed[:60] + "…"
    return collapsed


def build_registry(profile: Profile | None = None, shell_bridge=None) -> ToolRegistry:
    """Register the tools the active Profile exposes (engineering-spec §4.2, §4.7).

    A Profile chooses *which* SAFE-view tools are registered; it never changes *how*
    safety is enforced — registration still RAISES for any MEDIUM/HIGH tool lacking
    undo() (that's the safety invariant, not a bug). Defaults to the Simple profile,
    whose SAFE tool set is exactly the v1 §4.2 table.

    Mode-scoped safety (owner decision 2026-07-19, policy.py): the dev-only
    ``run_command`` tool is ALWAYS registered here, regardless of profile, but as
    ``dev_only`` — it is absent from the SAFE view (``visible_tools(SAFE)``) and only
    surfaces in OPEN mode. There is one shared registry; the SAFE/OPEN split is a
    filtered view over it (so routines use the same instances — §8.5), never a
    second registry. A runtime profile switch therefore needs no re-registration.

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
        registry.register(all_tools[tool_id])
    # OPEN-mode only, dev_only: real command execution. Registered once in the shared
    # registry; hidden from the SAFE view. Exempt from the undo check BECAUSE it is
    # dev_only and never reachable from SAFE mode (registry.register / run_command.py).
    registry.register(RunCommandTool(), dev_only=True)
    return registry


_SETUP_PROMPT_PATH = Path(__file__).resolve().parent / "providers" / "prompts" / "setup_assistant.txt"
_PRIMARY_PROMPT_PATH = Path(__file__).resolve().parent / "providers" / "prompts" / "primary.txt"


def load_setup_prompt() -> str:
    """The Setup Assistant system prompt (§4.6), injected for a turn when no
    PRIMARY key is configured yet. Read at startup — it is bundled with the app,
    not user data."""
    return _SETUP_PROMPT_PATH.read_text(encoding="utf-8")


def load_primary_prompt() -> str:
    """The app-context system prompt for regular (non-setup) turns: tells the
    model it is inside Addison and which UI control handles what, so a chat
    request like "save these steps as a routine" gets pointed at the real
    affordance instead of an improvised non-answer (found in the 2026-07 manual
    pass). Injected transiently per turn, exactly like the setup prompt."""
    return _PRIMARY_PROMPT_PATH.read_text(encoding="utf-8")


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

    def handler(tool_id: str, detail: str | None = None) -> PermissionStatus:
        definition = registry.get(tool_id).definition
        print()
        print(f"Addison would like to: {definition.label}")
        # The per-invocation destructive card names the exact command each time.
        print(f"  This time it wants to run: {detail}" if detail else f"  {definition.description}")
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
        conversation_id: str | None = None,
        primary_key_probe=None,
        setup_prompt: str | None = None,
        primary_prompt: str | None = None,
        ollama_base_url: str | None = None,
        ollama_client=None,
        cloud_catalog: list[CloudModel] | None = None,
        cloud_fetcher=None,
        cloud_provider_factory=None,
        connect_provider=None,
        provider_key_probe=None,
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
        # Multi-provider (owner decision 2026-07-18). ``connect_provider`` is a
        # (provider_id, base_url) -> list[CloudModel] callable that makes the "one
        # tiny request" to validate the provider, registers a provider instance per
        # model in the SAME ModelRouter, and returns that provider's catalog (raising
        # RuntimeError with a plain message on failure). ``provider_key_probe`` is a
        # (provider_id) -> bool telling whether a key is stored (drives provider.list's
        # implicit-connected state for a legacy/migrated key). Both None in CLI/tests
        # that don't wire them — provider.* then reports metadata only, no live connect.
        self._connect_provider = connect_provider
        self._provider_key_probe = provider_key_probe
        self._providers_reconnected = False
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
        # App-context prompt for every non-setup turn (None in CLI/tests that
        # don't pass one — those turns then run system-free, as before).
        self._primary_prompt = primary_prompt
        if shell_bridge is not None:
            # The bridge sends its Core -> Shell requests through our locked writer.
            shell_bridge.bind_sender(self._write_frame)

        # The gate's consent prompt IS an IPC round-trip (§4.3): emit the card,
        # then block the worker on a per-tool Event until permission.respond lands.
        # In OPEN mode a non-destructive call is auto-granted; ``on_auto_grant``
        # surfaces that in the activity log so the UI can still show what happened.
        self.permission_gate = PermissionGate(
            on_request=self._on_permission_request,
            on_auto_grant=self._on_auto_grant,
        )

        # The active §4.7 Profile, resolved from the store on the worker thread by
        # _ensure_built and held here so it can be consulted per-use (onboarding path,
        # raw diagnostics, routine-plan visibility) AND the policy mode it derives
        # (policy.py: Simple=SAFE, Developer=OPEN), which reshapes the visible tool set
        # and gate prompting. profile.set updates it in place so a switch takes effect
        # immediately, no restart. The two GLOBAL invariants never move with it: keys
        # stay keychain-only (never webview/SQLite) and there is no scheduling (§8.3, §6.7).
        self._active_profile: Profile | None = None

        # Built on the worker thread by _ensure_built (SQLite thread affinity).
        self.store = None
        self.undo_manager: UndoManager | None = None
        self.orchestrator: Orchestrator | None = None
        self.routine_builder: RoutineBuilder | None = None
        self.routine_library: RoutineLibrary | None = None
        self.routine_engine: RoutineEngine | None = None

        # A fresh uuid per launch unless the caller pins an id (tests do). The old
        # fixed "main" id appended every launch's turns to one ever-growing stored
        # transcript that the in-memory conversation never reloaded — the model
        # couldn't see those prior rows, so they were dead weight that also made
        # history a single giant entry. One conversation per launch matches what
        # the model actually sees; prior chats come back via conversation.load.
        self.conversation = Conversation(id=conversation_id or str(uuid4()))
        self._conversation_created = False
        self._conversation_titled = False      # auto-title has run for this conversation
        self._message_ids: list[str] = []      # persisted id per conversation.messages entry
        self._next_role: ModelRole | None = None
        self._next_model_name: str | None = None   # explicit LOCAL/cloud pick, §4.1.1, §6.8
        self._next_effort: str | None = None       # explicit "answer style" for next msg
        self._draft_routine = None             # pending §6.3 proposal awaiting confirmSave
        self._draft_widget = None              # pending widget proposal awaiting confirmSave
        # The most recently RUN saved routine this session — a widget proposed
        # right after a run offers that routine (mirrors "the last turn ran a
        # saved routine" heuristic; display-only signal, never a permission input).
        self._last_run_routine_id: str | None = None

        self._queue: queue.Queue = queue.Queue()
        self._perm_lock = threading.Lock()
        self._permission_waiters: dict[str, dict] = {}
        # Only one local-model setup may run at a time (§4.1.2); the flag is held
        # from pre-flight through the background pull/verify.
        self._local_setup_lock = threading.Lock()
        self._local_setup_active = False
        # Opportunistic usage-log pruning throttle (§4.8): counts recorded usage
        # rows so _record_usage prunes only once every _USAGE_PRUNE_EVERY writes.
        self._usage_records_since_prune = 0

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
        # §4.7: read the persisted profile now that the store exists (SIMPLE if unset).
        self._active_profile = resolve_active_profile(self.store)
        self.undo_manager = UndoManager(store=self.store, tool_registry=self.tool_registry)
        self.orchestrator = Orchestrator(
            model_router=self.model_router,
            tool_registry=self.tool_registry,
            permission_gate=self.permission_gate,
            undo_manager=self.undo_manager,
            stream_to_frontend=self._emit_stream_chunk,
            on_activity=self._emit_activity,
            on_usage=self._record_usage,
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

    def _respond_error(self, request_id, code: int, message: str, data: dict | None = None) -> None:
        if request_id is None:
            return
        error: dict = {"code": code, "message": message}
        # JSON-RPC allows an error ``data`` member; the Developer profile uses it to
        # carry raw diagnostics. The plain-language ``message`` is IDENTICAL in both
        # profiles — Developer just gets MORE detail, never a different message (§4.7).
        if data is not None:
            error["data"] = data
        self._write_frame({"jsonrpc": "2.0", "id": request_id, "error": error})

    def _raw_detail(self, exc: Exception) -> dict | None:
        """Developer-profile raw diagnostics for an error frame: the repr of the
        underlying exception, or None for Simple (which is unchanged). This adds
        VISIBILITY only — it never changes control flow or the plain message (§8.7)."""
        profile = self._active_profile
        if profile is not None and profile.raw_diagnostics:
            return {"raw": repr(exc)}
        return None

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
        if method == Method.UNDO_REDO_LAST_ACTION:
            self._queue.put(("redo", params, request_id))
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
        # conversation.new/load/list touch the Store and the worker-owned
        # conversation state, so they run on the worker like every other
        # store-backed job (SQLite thread affinity + turn serialization).
        if method in _CONVERSATION_JOBS:
            self._queue.put((_CONVERSATION_JOBS[method], params, request_id))
            return
        # profile.get/set read/write app_settings, so they run on the worker too.
        if method == Method.PROFILE_GET:
            self._queue.put(("profile_get", params, request_id))
            return
        if method == Method.PROFILE_SET:
            self._queue.put(("profile_set", params, request_id))
            return
        # provider.list/connect/disconnect touch the Store and the ModelRouter, and
        # connect makes an outbound HTTPS ping + a Core -> Shell key fetch — all of
        # which must run on the worker, never the read loop (same rule as
        # availableRoles; see the class docstring's threading model).
        if method in _PROVIDER_JOBS:
            self._queue.put((_PROVIDER_JOBS[method], params, request_id))
            return
        # widget.* and stats.get touch the Store (and the routine library / live
        # conversation), so they run on the worker like every other store-backed job.
        if method in _WIDGET_JOBS:
            self._queue.put((_WIDGET_JOBS[method], params, request_id))
            return
        if method == Method.STATS_GET:
            self._queue.put(("stats_get", params, request_id))
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
                    self._maybe_load_catalogs()
                    self._respond(request_id, self._available_roles())
                elif kind == "undo":
                    self._respond(request_id, self._undo_last_action())
                elif kind == "redo":
                    self._respond(request_id, self._redo_last_action())
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
                elif kind == "profile_get":
                    self._respond(request_id, self._profile_get())
                elif kind == "profile_set":
                    self._handle_profile_set(params, request_id)
                elif kind == "conversation_new":
                    self._handle_conversation_new(request_id)
                elif kind == "conversation_load":
                    self._handle_conversation_load(params, request_id)
                elif kind == "conversation_list":
                    self._ensure_built()
                    self._respond(request_id, {"conversations": self._conversation_rows()})
                elif kind == "provider_list":
                    self._respond(request_id, self._provider_list())
                elif kind == "provider_connect":
                    self._respond(request_id, self._provider_connect(params))
                elif kind == "provider_disconnect":
                    self._respond(request_id, self._provider_disconnect(params))
                elif kind == "widget_list":
                    self._respond(request_id, self._widget_list())
                elif kind == "widget_set_pinned":
                    self._respond(request_id, self._widget_set_pinned(params))
                elif kind == "widget_delete":
                    self._respond(request_id, self._widget_delete(params))
                elif kind == "widget_propose":
                    self._handle_widget_propose(request_id)
                elif kind == "widget_confirm":
                    self._handle_widget_confirm(params, request_id)
                elif kind == "widget_run":
                    self._handle_widget_run(params, request_id)
                elif kind == "stats_get":
                    self._respond(request_id, self._stats_get())
            except RuntimeError as exc:
                # Provider/tool errors already carry a plain, user-ready sentence.
                self._respond_error(request_id, _SERVER_ERROR, str(exc), self._raw_detail(exc))
            except Exception as exc:
                # Anything else collapses to one plain message — no stack trace (the
                # raw repr is attached only for the Developer profile, §4.7).
                self._respond_error(
                    request_id, _SERVER_ERROR, _GENERIC_TURN_ERROR, self._raw_detail(exc)
                )

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

        # Is a real PRIMARY key available right now? Both the BYOK-onboarding refusal
        # and the §4.6 Setup Assistant handoff below turn on this, so probe it ONCE
        # here rather than per branch — the probe is a keychain round-trip (§5). Only
        # a PRIMARY/default turn touches the key path; a LOCAL turn never probes.
        primary_role = requested_role in (None, ModelRole.PRIMARY)
        primary_key_available = self._primary_key_available() if primary_role else True

        # §4.7 onboarding by profile: the Developer profile is BYOK-first — with no
        # PRIMARY key it does NOT fall back to the Setup Assistant relay; it tells the
        # user to add their own key. Simple keeps the §4.6 relay handoff below,
        # untouched. This is an onboarding *surface* branch, not a safety branch —
        # neither path changes the gate/undo/key rules (§8.7).
        profile = self._active_profile
        if (
            primary_role
            and not primary_key_available
            and profile is not None
            and profile.onboarding == "byok_first"
        ):
            self._respond_error(request_id, _SERVER_ERROR, _BYOK_ONBOARDING_MESSAGE)
            return

        self._ensure_conversation()
        user_msg = Message(role="user", content=text)
        self.conversation.messages.append(user_msg)
        user_message_id = self._persist_message(user_msg)

        # Auto-title on the first user message with any content. The store call is
        # first-write-wins (title IS NULL guard), so the flag is only an
        # optimization that skips the write on every later turn; a whitespace-only
        # first message leaves the flag down so the next real one can title it.
        if not self._conversation_titled:
            title = _auto_title(text)
            if title is not None:
                self.store.set_conversation_title(self.conversation.id, title)
                self._conversation_titled = True

        # §4.6 handoff: a PRIMARY-bound turn with no key yet routes to the Setup
        # Assistant, with its system prompt injected FOR THIS TURN ONLY. The prompt
        # is never persisted and never enters the stored transcript (which also can't
        # hold a "system" role — messages.role CHECK is user/assistant/tool). Once a
        # key exists, the probe passes and turns go to PRIMARY, history untouched —
        # that IS the handoff; no transcript rewrite, no state to flip.
        system_msg = None
        if primary_role and not primary_key_available:
            requested_role = ModelRole.SETUP_ASSISTANT
            if self._setup_prompt:
                system_msg = Message(role="system", content=self._setup_prompt)
                self.conversation.messages.insert(0, system_msg)
        elif self._primary_prompt:
            # Every non-setup turn (cloud or local) gets the app-context prompt,
            # under the same transient rules: this turn only, never persisted.
            system_msg = Message(role="system", content=self._primary_prompt)
            self.conversation.messages.insert(0, system_msg)

        pre_turn = len(self.conversation.messages)
        assistant_message_id: str | None = None
        try:
            self.orchestrator.run_turn(
                self.conversation,
                requested_role=requested_role,
                model_name=model_name,
                effort=effort,
                mode=self._mode(),
            )
            # Full-transcript persistence (§4.8 substrate): every message the turn
            # appended, in order, so a later rewind can target any of them by id.
            for msg in self.conversation.messages[pre_turn:]:
                persisted_id = self._persist_message(msg)
                if msg.role == "assistant":
                    assistant_message_id = persisted_id
        except Exception:
            # A failed turn must leave NO partial exchange behind: an unpaired
            # tool_use would make the provider reject every later request (API
            # 400), and unpersisted entries would break the 1:1 alignment
            # between conversation.messages and _message_ids that rewind needs.
            del self.conversation.messages[pre_turn:]
            raise
        finally:
            # Drop the transient system prompt so it never lingers in history and
            # in-memory messages stay aligned 1:1 with the persisted _message_ids.
            if system_msg is not None:
                try:
                    self.conversation.messages.remove(system_msg)
                except ValueError:
                    pass
        # The persisted ids let the frontend anchor "Rewind to here" on REAL
        # store ids — its own display ids mean nothing to the core.
        self._respond(
            request_id,
            {
                "ok": True,
                "userMessageId": user_message_id,
                "assistantMessageId": assistant_message_id,
            },
        )

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

    def _persist_message(self, message: Message) -> str:
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
        return message_id

    # --- policy mode ------------------------------------------------------
    def _mode(self) -> PolicyMode:
        """The live policy mode, derived 1:1 from the active profile (policy.py).
        SAFE for Simple, OPEN for Developer. Read fresh each time so a profile.set
        takes effect immediately — no per-mode state is cached anywhere."""
        return mode_for_profile(self._active_profile)

    def _on_auto_grant(self, tool_id: str) -> None:
        """OPEN mode auto-allowed a non-destructive call: record it in the activity
        log so the UI can show it was approved automatically (not a user prompt)."""
        self._notify(
            Method.TOOL_ACTIVITY_UPDATE,
            {"toolId": tool_id, "label": self._label(tool_id), "autoGranted": True},
        )

    # --- permissions ------------------------------------------------------
    def _on_permission_request(self, tool_id: str, detail: str | None = None) -> PermissionStatus:
        """Runs on the worker thread: render the card, block for the answer.

        ``detail`` is set on the destructive-in-OPEN per-invocation path (the exact
        command text, already truncated by the tool) — the card's description then
        names precisely what is being approved this time, because that approval
        never carries over to the next destructive call."""
        definition = self.tool_registry.get(tool_id).definition
        description = definition.description
        if detail:
            description = f"This time it wants to run: {detail}"
        event = threading.Event()
        with self._perm_lock:
            self._permission_waiters[tool_id] = {"event": event, "allow": False}
        self._notify(
            Method.PERMISSION_REQUEST_GRANT,
            {
                "toolId": tool_id,
                "label": definition.label,
                "description": description,
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
        can_redo = self.undo_manager.can_redo()
        if not results:
            return {"ok": False, "detail": "There was nothing to undo.", "canRedo": can_redo}
        result = results[0]
        if result.success:
            return {
                "ok": True,
                "detail": f"Undid the last action ({self._label(result.tool_id)}).",
                "canRedo": can_redo,
            }
        return {
            "ok": False,
            "detail": "Couldn't undo the last action. You may need to reverse it yourself.",
            "canRedo": can_redo,
        }

    def _redo_last_action(self) -> dict:
        results = self.undo_manager.redo_last(1)
        can_redo = self.undo_manager.can_redo()
        if not results:
            return {"ok": False, "detail": "There was nothing to redo.", "canRedo": can_redo}
        result = results[0]
        if result.success:
            return {
                "ok": True,
                "detail": f"Did that again ({self._label(result.tool_id)}).",
                "canRedo": can_redo,
            }
        # The plain reason (e.g. "A file with that name is already there") beats
        # a generic sentence; redo failures carry user-ready details.
        return {
            "ok": False,
            "detail": result.detail or "Couldn't do that again.",
            "canRedo": can_redo,
        }

    def _handle_rewind(self, params: dict, request_id) -> None:
        to_message_id = params.get("toMessageId")
        try:
            # Edit-and-resend semantics: the anchor message is REMOVED too, so
            # nothing re-runs until the user actually sends again — its text goes
            # back into the composer on the frontend side.
            self.undo_manager.rewind_conversation(
                self.conversation.id, to_message_id, keep_anchor=False
            )
        except KeyError:
            self._respond_error(
                request_id, _SERVER_ERROR, "Couldn't find that point to rewind to."
            )
            return
        # Mirror the truncation in the in-memory conversation, anchor included.
        if to_message_id in self._message_ids:
            idx = self._message_ids.index(to_message_id)
            del self.conversation.messages[idx:]
            del self._message_ids[idx:]
        self._respond(request_id, {"ok": True, "detail": "Rewound the conversation."})

    # --- conversation history (new / load / list) --------------------------
    def _handle_conversation_new(self, request_id) -> None:
        """Start a fresh conversation: new uuid, empty in-memory state. NO store
        row is inserted here — rows stay lazy via ``_ensure_conversation`` (first
        real turn), so an abandoned empty chat never appears in history."""
        self.conversation = Conversation(id=str(uuid4()))
        self._message_ids = []
        self._conversation_created = False
        self._conversation_titled = False
        self._draft_routine = None
        self._respond(request_id, {"conversationId": self.conversation.id})

    def _handle_conversation_load(self, params: dict, request_id) -> None:
        """Reopen a stored conversation as the active one.

        The in-memory state is rebuilt from the persisted transcript in one
        filtered pass that keeps user messages and non-empty assistant messages.
        Persisted ``tool`` rows (and the empty assistant stubs that requested the
        tools) are SKIPPED on purpose: ``insert_message`` never persists assistant
        ``tool_calls``, so replaying persisted tool rows would send unpaired
        tool_results and the provider would 400 on every subsequent turn — a
        resumed conversation keeps the assistant's final prose only. Each kept row
        appends to BOTH the fresh Conversation and the fresh ``_message_ids`` list
        in the same pass; that 1:1 alignment is the rewind-anchoring invariant
        (``_handle_rewind`` indexes one list with the other's position)."""
        self._ensure_built()
        conversation_id = params.get("conversationId")
        header = self.store.get_conversation(conversation_id) if conversation_id else None
        if header is None:
            self._respond_error(request_id, _SERVER_ERROR, "Couldn't find that conversation.")
            return
        conversation = Conversation(id=conversation_id)
        message_ids: list[str] = []
        wire_messages: list[dict] = []
        for row in self.store.messages_for_conversation(conversation_id):
            keep = row["role"] == "user" or (row["role"] == "assistant" and row["content"])
            if not keep:
                continue
            conversation.messages.append(Message(role=row["role"], content=row["content"]))
            message_ids.append(row["id"])
            wire_messages.append({"id": row["id"], "role": row["role"], "content": row["content"]})
        self.conversation = conversation
        self._message_ids = message_ids
        self._conversation_created = True
        self._conversation_titled = header["title"] is not None
        self._draft_routine = None
        self._respond(
            request_id,
            {
                "conversationId": conversation_id,
                "title": header["title"],
                "messages": wire_messages,
            },
        )

    def _conversation_rows(self) -> list[dict]:
        """History rows for conversation.list. The title is never null: stored
        title, else the trimmed first user message (legacy rows that predate
        auto-titling), else "Untitled"."""
        rows = []
        for row in self.store.list_conversations():
            title = row["title"] or _auto_title(row["first_user_message"] or "") or "Untitled"
            rows.append(
                {
                    "id": row["id"],
                    "title": title,
                    "startedAt": row["started_at"],
                    "messageCount": row["message_count"],
                }
            )
        return rows

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
        # Saved under the current mode; builder.save refuses a command-step routine
        # in SAFE mode and stamps created_in_mode so SAFE can later hide it.
        try:
            self.routine_builder.save(
                draft, conversation_id=self.conversation.id, mode=self._mode()
            )
        except ValueError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        self._draft_routine = None
        self._respond(request_id, {"ok": True, "routineId": draft.id})

    def _routine_rows(self) -> list[dict]:
        # §4.7/§6.5: the Developer profile additionally sees a READ-ONLY view of the
        # declarative plan. This is safe to expose precisely because the plan has no
        # code field (§6.1) — it is pure data. There is NO editing surface here;
        # structural step editing stays v2 (§10).
        profile = self._active_profile
        expose_plan = profile is not None and profile.expose_routine_plan
        safe_mode = self._mode() is PolicyMode.SAFE
        rows = []
        for entry in self.routine_library.list():
            # Dev-created routines are hidden while the Simple profile is active
            # (policy.py) — never listed, and they return untouched in Developer mode.
            if safe_mode and entry.get("createdInMode") == PolicyMode.OPEN.value:
                continue
            routine = entry["routine"]
            row = {
                "id": routine.id,
                "name": routine.name,
                "description": routine.description,
                "runCount": entry["runCount"],
                "lastRunAt": entry["lastRunAt"],
                # Display-only mode provenance: lets the frontend badge dev-created
                # routines ("DEV" tag). Never consulted for permissions.
                "createdInMode": entry.get("createdInMode"),
                "variables": [
                    {"name": v.name, "prompt": v.prompt, "default": v.default}
                    for v in routine.variables
                ],
            }
            if expose_plan:
                row["planSteps"] = [
                    {
                        "stepId": step.step_id,
                        "toolId": step.tool_id,
                        "argsTemplate": step.args_template,
                        "dependsOn": step.depends_on,
                        "onFailure": step.on_failure,
                    }
                    for step in routine.steps
                ]
            rows.append(row)
        return rows

    def _handle_routine_run(self, params: dict, request_id) -> None:
        routine_id = params.get("routineId")
        try:
            routine = self.routine_library.get(routine_id)
        except KeyError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        # A dev-created routine is REFUSED in SAFE mode — it waits for Developer mode
        # (policy.py). Switching modes is always allowed, so the routine isn't lost.
        mode = self._mode()
        if (
            mode is PolicyMode.SAFE
            and self.routine_library.created_in_mode(routine_id) == PolicyMode.OPEN.value
        ):
            self._respond_error(
                request_id,
                _SERVER_ERROR,
                "That routine uses developer abilities, so it's waiting in "
                "Developer profile.",
            )
            return
        result = self.routine_engine.run(routine, params.get("variables") or {}, mode=mode)
        self.routine_library.record_run(routine.id)
        # Remember the routine just run so a widget proposed right after offers it
        # (display-only signal — never affects permissions).
        self._last_run_routine_id = routine.id
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

    # --- usage recording (§4.8 substrate; orchestrator machinery) ---------
    def _record_usage(self, usage, latency_ms, requested_role, model_name) -> None:
        """Record one provider call's token usage + latency into ``usage_log``.

        The single choke point every turn's model calls flow through
        (Orchestrator.on_usage). NOT a registry tool — this is server machinery
        (§4.8 precedent). A call that reported no usage (``usage`` is None) or the
        onboarding relay is skipped. Never touches key material."""
        if usage is None or self.store is None:
            return
        if requested_role is ModelRole.SETUP_ASSISTANT:
            return  # the free onboarding relay isn't metered
        provider_id, model = self._usage_identity(requested_role, model_name)
        now = int(time.time())
        self.store.insert_usage(
            id=str(uuid4()),
            conversation_id=self.conversation.id,
            provider=provider_id,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            created_at=now,
        )
        # Opportunistic retention: prune once every _USAGE_PRUNE_EVERY records
        # rather than on every write, so a bounded ~6-month window is kept cheaply.
        self._usage_records_since_prune += 1
        if self._usage_records_since_prune >= _USAGE_PRUNE_EVERY:
            self._usage_records_since_prune = 0
            self.store.prune_usage_log(now - _USAGE_RETENTION_SECONDS)

    def _usage_identity(self, requested_role, model_name) -> tuple[str, str]:
        """Best-effort (provider_id, model) for a usage row. A LOCAL turn is an
        Ollama model; a cloud/default turn resolves against the catalog, falling
        back to a plain default when there's no catalog (CLI/tests)."""
        if requested_role is ModelRole.LOCAL:
            return "ollama", (model_name or "local")
        entry = None
        if self._cloud_catalog:
            entry = (
                find_cloud_model(self._cloud_catalog, model_name)
                if model_name
                else default_cloud_model(self._cloud_catalog)
            )
        if entry is not None:
            return entry.provider, entry.id
        return "anthropic", (model_name or "default")

    # --- widgets + stats (declarative specs; core-computed sources) -------
    def _stats_get(self) -> dict:
        """stats.get -> the three core-computed sources. Carries NO key material:
        token totals, per-provider latency, and connection status only (§8.3)."""
        self._ensure_built()
        totals = self.store.usage_totals_since(_month_start_epoch())
        latency = self.store.latest_latency_per_provider()
        return {
            # No invented limit — v1 has no per-account cap to show (null).
            "tokensMonth": {"total": totals["total"], "limit": None},
            "providerLatency": latency,
            "connections": self._connections(latency),
        }

    def _connections(self, latency: list[dict]) -> list[dict]:
        """Ollama (probed live) + each connected cloud provider. Status/detail are
        plain strings; there is NEVER any key material in this payload (§8.3)."""
        conns: list[dict] = []
        try:
            ollama_up = is_running(self._ollama_base_url, self._ollama_client)
        except Exception:
            ollama_up = False
        conns.append(
            {
                "id": "ollama",
                "label": "Ollama · this computer",
                "status": "running" if ollama_up else "idle",
                "detail": "running" if ollama_up else "not running",
            }
        )
        latency_by_provider = {row["provider"]: row["ms"] for row in latency}
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            if not connected:
                continue
            ms = latency_by_provider.get(provider_id)
            label = provider_label(provider_id)
            conns.append(
                {
                    "id": provider_id,
                    "label": f"{label} API" if provider_id != "custom" else label,
                    "status": "reachable",
                    "detail": f"{ms} ms" if ms is not None else "connected",
                }
            )
        return conns

    def _widget_list(self) -> dict:
        """widget.list -> stored widgets, INVALID specs hidden at render (safety:
        a spec that fails validate_widget_spec is never surfaced or run)."""
        self._ensure_built()
        mode = self._mode()
        safe_mode = mode is PolicyMode.SAFE
        widgets: list[dict] = []
        for row in self.store.list_widgets():
            # Dev-created widgets are hidden while the Simple profile is active
            # (policy.py) — and command widgets also fail SAFE-mode validation below.
            if safe_mode and row.get("created_in_mode") == PolicyMode.OPEN.value:
                continue
            try:
                spec = json.loads(row["spec_json"])
            except ValueError:
                continue
            if validate_widget_spec(spec, mode) is not None:
                continue
            widgets.append(
                {
                    "id": row["id"],
                    "spec": spec,
                    "pinned": row["pinned"],
                    "position": row["position"],
                    # Display-only mode provenance for the frontend's "DEV" tag —
                    # never consulted for permissions (the gate re-checks at run).
                    "createdInMode": row.get("created_in_mode"),
                }
            )
        return {"widgets": widgets}

    def _widget_set_pinned(self, params: dict) -> dict:
        self._ensure_built()
        widget_id = params.get("id")
        if not widget_id or self.store.get_widget(widget_id) is None:
            return {"ok": False, "error": "That widget isn't here any more."}
        pinned = bool(params.get("pinned"))
        if pinned and self.store.count_pinned_widgets(exclude_id=widget_id) >= MAX_PINNED:
            return {"ok": False, "error": "You can pin up to six widgets. Unpin one first."}
        self.store.set_widget_pinned(widget_id, pinned)
        return {"ok": True}

    def _widget_delete(self, params: dict) -> dict:
        self._ensure_built()
        widget_id = params.get("id")
        if widget_id:
            self.store.delete_widget(widget_id)
        return {"ok": True}

    def _handle_widget_run(self, params: dict, request_id) -> None:
        """widget.run — the rail's Run pill for a COMMAND widget (OPEN mode only).

        Routine and stat widgets refuse here: their actions already have homes
        (routine.run / stats.get). The command runs through the SAME registry +
        gate path as a routine command step, so the per-invocation destructive
        prompt holds — clicking a widget can never skip a card the chat would
        have shown. SAFE mode refuses before touching the registry (dev-created
        widgets are already hidden from SAFE lists; this is the belt for a stale
        frontend or a raced mode switch)."""
        self._ensure_built()
        widget_id = params.get("id")
        row = self.store.get_widget(widget_id) if widget_id else None
        if row is None:
            self._respond(request_id, {"ok": False, "error": "That widget isn't here any more."})
            return
        try:
            spec = json.loads(row["spec_json"])
        except ValueError:
            self._respond(request_id, {"ok": False, "error": "That widget can't run."})
            return
        if spec.get("kind") != "command":
            self._respond(
                request_id,
                {"ok": False, "error": "That widget doesn't run commands."},
            )
            return
        mode = self._mode()
        if mode is PolicyMode.SAFE:
            self._respond(
                request_id,
                {
                    "ok": False,
                    "error": "That widget uses developer abilities, so it's waiting in "
                    "Developer profile.",
                },
            )
            return
        tool = self.tool_registry.get("run_command")
        args = {"command": spec.get("command", "")}
        status = self.permission_gate.authorize(
            "run_command",
            mode=mode,
            destructive=call_is_destructive(tool, args),
            detail=call_permission_detail(tool, args),
        )
        if status != PermissionStatus.GRANTED:
            self._respond(
                request_id,
                {"ok": False, "error": "You declined a permission it needs."},
            )
            return
        context = ExecutionContext(
            conversation_id=f"widget:{widget_id}",
            shell_bridge=self._shell_bridge,
            policy_mode=mode,
        )
        try:
            result = tool.execute(args, context)
        except RuntimeError as exc:
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return
        except Exception:
            self._respond(request_id, {"ok": False, "error": "That widget's command didn't work."})
            return
        # run_command truncates its own transcript output, so content passes through.
        self._respond(
            request_id,
            {"ok": result.success, "output": result.content}
            if result.success
            else {"ok": False, "error": result.content},
        )

    def _handle_widget_propose(self, request_id) -> None:
        """Draft a widget spec from the recent conversation (mirrors routine.propose:
        draft held in memory, nothing saved yet). v1 only proposes a routine widget
        (a routine just run or named) or a matching stat widget; otherwise refuses."""
        draft = self._draft_widget_from_conversation(self._mode())
        if draft is None:
            self._respond_error(request_id, _SERVER_ERROR, "I can't make a widget from this yet.")
            return
        self._draft_widget = draft
        self._respond(
            request_id,
            {
                "title": draft["title"],
                "kind": draft["kind"],
                "summary": widget_summary(draft),
                "spec": draft,
            },
        )

    def _handle_widget_confirm(self, params: dict, request_id) -> None:
        """widget.confirmSave {accept}: save the held draft ONLY on explicit accept.
        Saving a widget is LOW-risk (display-only), so no permission card — but the
        spec is re-validated here (never trust the held draft blindly)."""
        draft = self._draft_widget
        if draft is None:
            self._respond_error(
                request_id, _SERVER_ERROR, "There's no widget waiting to be added."
            )
            return
        if not params.get("accept"):
            self._draft_widget = None
            self._respond(request_id, {"ok": False, "declined": True})
            return
        mode = self._mode()
        error = validate_widget_spec(draft, mode)
        if error is not None:
            self._draft_widget = None
            self._respond_error(request_id, _SERVER_ERROR, error)
            return
        widget_id = str(uuid4())
        pinned = self.store.count_pinned_widgets() < MAX_PINNED
        self.store.insert_widget(
            id=widget_id,
            spec_json=json.dumps(draft),
            pinned=pinned,
            position=self.store.next_widget_position(),
            created_at=int(time.time()),
            created_in_mode=mode.value,
        )
        self._draft_widget = None
        self._respond(request_id, {"ok": True, "widgetId": widget_id, "pinned": pinned})

    def _draft_widget_from_conversation(self, mode: PolicyMode) -> dict | None:
        """The widget heuristic. Returns a valid spec dict or None (a refusal).

        Priority: an explicit ask for token/latency/connection info -> that stat
        widget; else (OPEN mode only) the last run_command in the recent chat -> a
        command widget; else the routine just run, or a routine named in the recent
        chat -> that routine widget; else None."""
        recent = self.conversation.messages[-10:]
        if mode is PolicyMode.OPEN:
            command = self._recent_command(recent)
            if command is not None:
                return {"kind": "command", "command": command, "title": command[:60]}
        joined = " ".join(
            m.content.lower()
            for m in recent
            if m.role == "user" and isinstance(m.content, str)
        )
        if any(k in joined for k in ("token", "usage", "how much have i used", "cost")):
            return {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}
        if any(k in joined for k in ("latency", "how fast", "response time", "how quick")):
            return {"kind": "stat", "source": "provider_latency", "title": "Model latency"}
        if any(k in joined for k in ("connection", "connected", "online", "reachable")):
            return {"kind": "stat", "source": "connections", "title": "Connections"}
        if self._last_run_routine_id is not None:
            try:
                routine = self.routine_library.get(self._last_run_routine_id)
                return {"kind": "routine", "routineId": routine.id, "title": routine.name[:60]}
            except KeyError:
                pass
        for entry in self.routine_library.list():
            routine = entry["routine"]
            if routine.name and routine.name.lower() in joined:
                return {"kind": "routine", "routineId": routine.id, "title": routine.name[:60]}
        return None

    @staticmethod
    def _recent_command(messages: list) -> str | None:
        """The most recent run_command invocation in ``messages`` (OPEN mode only),
        so a command widget can be proposed from it. None if there is no such call."""
        command: str | None = None
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                if getattr(call, "tool_id", None) == "run_command":
                    value = (call.args or {}).get("command")
                    if isinstance(value, str) and value.strip():
                        command = value
        return command

    # --- profiles (§4.7) --------------------------------------------------
    def _profile_get(self) -> dict:
        """The active profile, the selector's option list, and the feature flags
        for the ACTIVE profile. Flags are pure surface signals the frontend uses to
        show/hide Developer-only affordances — they never gate tool execution (§8.7)."""
        active = self._active_profile or SIMPLE
        return {
            "activeProfile": active.id.value,
            # The policy mode this profile runs under ('safe' | 'open'), derived 1:1
            # from the profile (policy.py). Consumed by the next (frontend) PR.
            "mode": mode_for_profile(active).value,
            "profiles": [
                {"id": p.id.value, "label": p.label, "description": p.description}
                for p in (SIMPLE, DEVELOPER)
            ],
            "flags": {
                "exposeRoutinePlan": active.expose_routine_plan,
                "rawDiagnostics": active.raw_diagnostics,
                "headlessCli": active.headless_cli,
                "byokFirstOnboarding": active.onboarding == "byok_first",
            },
        }

    def _handle_profile_set(self, params: dict, request_id) -> None:
        """Persist the chosen profile and re-resolve it for the running server so the
        switch takes effect immediately (no restart). An unknown id is refused plainly.

        Mode-scoped safety (owner decision 2026-07-19, policy.py): the profile also
        derives the policy mode — Simple=SAFE, Developer=OPEN — which reshapes the
        permission gate (OPEN prompts only for destructive actions) and the visible
        tool set (OPEN surfaces run_command). The two GLOBAL invariants never move:
        keys stay keychain-only and never reach the webview/SQLite, and there is no
        scheduling in either mode. Switching modes is always allowed; dev-created
        routines/widgets simply hide in SAFE and return in OPEN."""
        try:
            profile = get_profile(ProfileId(params.get("profileId")))
        except ValueError:
            self._respond_error(request_id, _SERVER_ERROR, _UNKNOWN_PROFILE_MESSAGE)
            return
        self.store.set_setting("active_profile", profile.id.value)
        self._active_profile = profile
        # Mode is derived live from _active_profile (policy.py) — the switch takes
        # effect immediately and needs no per-mode cache to refresh: the orchestrator
        # reads visible_tools(mode) per turn and the gate takes mode per call. Return
        # the new mode for the frontend (next PR).
        self._respond(request_id, {"ok": True, "mode": mode_for_profile(profile).value})

    # --- model roles ------------------------------------------------------
    def _maybe_load_catalogs(self) -> None:
        """First availableRoles: swap in the live Anthropic catalog (if a key is
        present) and reconnect every other provider the user connected in a previous
        launch, so the picker's union is whole again after a restart.

        Runs on the worker (never the read loop): the key probe, the Anthropic fetch,
        and each provider reconnect ping all do round-trips that block on frames the
        read loop must stay free to deliver. Failures are swallowed and leave the door
        open to retry (Anthropic) or to a manual reconnect (others)."""
        self._maybe_load_live_catalog()
        self._maybe_reconnect_saved_providers()

    def _maybe_load_live_catalog(self) -> None:
        """First availableRoles once a PRIMARY (Anthropic) key exists: swap the
        built-in fallback for the live list of every model the key can access, and
        register a provider per fetched entry so by-name picks resolve to it. Merges
        into the union (never clobbers other connected providers' models).

        Any failure — no key, offline, a bad response — keeps the fallback and leaves
        the door open to retry on a later availableRoles call (nothing is marked
        loaded). Registration is idempotent (dict replace), so repeated calls are safe."""
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

        self._set_provider_models("anthropic", catalog)
        self._cloud_catalog_loaded = True
        if self._cloud_provider_factory is not None:
            for entry in catalog:
                self.model_router.register_primary_model(
                    entry.id, self._cloud_provider_factory(entry)
                )

    def _maybe_reconnect_saved_providers(self) -> None:
        """Reconnect the non-Anthropic providers persisted as connected in a prior
        launch (their keys are still in the keychain). One-shot per launch: a provider
        that can't be reached right now simply has no models until the user reconnects
        it from Settings. Anthropic is handled by the live-catalog path above."""
        if self._providers_reconnected or self._connect_provider is None or self.store is None:
            return
        self._providers_reconnected = True
        for cfg in self.store.list_provider_configs():
            provider_id = cfg["provider_id"]
            if provider_id == "anthropic" or not cfg["connected"]:
                continue
            try:
                models = self._connect_provider(provider_id, cfg["base_url"])
            except Exception:
                continue   # transient failure — user can reconnect manually
            self._set_provider_models(provider_id, models)

    def _set_provider_models(self, provider_id: str, models: list[CloudModel]) -> None:
        """Replace one provider's slice of the union picker menu with ``models``,
        keeping a single default across the whole union (merge_catalogs). Other
        providers' entries are untouched."""
        others = [m for m in self._cloud_catalog if m.provider != provider_id]
        self._cloud_catalog = merge_catalogs([others, list(models)])

    # --- provider connections (multi-provider, §4.1.1) --------------------
    def _provider_key_present(self, provider_id: str) -> bool:
        probe = self._provider_key_probe
        if probe is None:
            return False
        try:
            return bool(probe(provider_id))
        except Exception:
            return False

    def _provider_list(self) -> dict:
        """provider.list -> {providers: [...]}. Carries ONLY non-secret status and
        metadata — NEVER any key material (invariant §8.3): id, plain label, whether
        it is connected, and (when known) the added date, custom base URL, and the
        last connect-check result.

        ``connected`` trusts a stored connection row exactly; only when there is NO
        row does it fall back to 'a key is already in the keychain' — that fallback
        exists so a legacy/migrated Anthropic key shows connected without a re-connect."""
        self._ensure_built()
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        rows: list[dict] = []
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            row: dict = {
                "id": provider_id,
                "label": provider_label(provider_id),
                "connected": connected,
            }
            if cfg is not None:
                if cfg["added_at"] is not None:
                    row["addedAt"] = cfg["added_at"]
                if provider_id == "custom" and cfg["base_url"]:
                    row["baseUrl"] = cfg["base_url"]
                if cfg["last_check_ok"] is not None:
                    row["lastCheckOk"] = cfg["last_check_ok"]
            rows.append(row)
        return {"providers": rows}

    def _provider_connect(self, params: dict) -> dict:
        """provider.connect {provider, baseUrl?} -> {ok, error?}. The key was already
        stored by the Rust command; here the core pulls it from the keychain, makes ONE
        tiny validating request, and — on success — records metadata and folds the
        provider's models into the picker union. On failure it does NOT mark the provider
        connected (the card offers Remove to clear the stored key)."""
        self._ensure_built()
        provider_id = params.get("provider")
        base_url = (params.get("baseUrl") or "").strip() or None
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        if provider_id == "custom" and not _valid_http_url(base_url):
            return {
                "ok": False,
                "error": "Enter a web address that starts with http:// or https://.",
            }
        if self._connect_provider is None:
            return {"ok": False, "error": "Connecting a provider needs the desktop app."}
        try:
            models = self._connect_provider(provider_id, base_url)
        except RuntimeError as exc:
            # Provider errors already carry a plain, user-ready sentence. Record the
            # failed check WITHOUT marking connected, so provider.list shows it off.
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": str(exc)}
        except Exception:
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": _GENERIC_TURN_ERROR}
        self.store.upsert_provider_config(
            provider_id,
            connected=True,
            added_at=int(time.time()),
            base_url=base_url,
            last_check_ok=True,
        )
        self._set_provider_models(provider_id, models)
        return {"ok": True}

    def _provider_disconnect(self, params: dict) -> dict:
        """provider.disconnect {provider} -> {ok}. Forget the connection metadata and
        drop that provider's models from the picker union and the router pool. The key
        itself is removed separately by the Rust keychain command (the webview calls it)."""
        self._ensure_built()
        provider_id = params.get("provider")
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        self.store.delete_provider_config(provider_id)
        for model in [m for m in self._cloud_catalog if m.provider == provider_id]:
            self.model_router.unregister_primary_model(model.id)
        self._cloud_catalog = [m for m in self._cloud_catalog if m.provider != provider_id]
        return {"ok": True}

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

# conversation.new/load/list also run on the worker: load/list read the Store,
# and new swaps the worker-owned active conversation, which must serialize
# behind any in-flight turn. Method -> worker job kind.
_CONVERSATION_JOBS = {
    Method.CONVERSATION_NEW: "conversation_new",
    Method.CONVERSATION_LOAD: "conversation_load",
    Method.CONVERSATION_LIST: "conversation_list",
}

# provider.list/connect/disconnect run on the worker (Store + router + connect ping).
_PROVIDER_JOBS = {
    Method.PROVIDER_LIST: "provider_list",
    Method.PROVIDER_CONNECT: "provider_connect",
    Method.PROVIDER_DISCONNECT: "provider_disconnect",
}

# widget.* run on the worker (Store + routine library + live conversation).
_WIDGET_JOBS = {
    Method.WIDGET_LIST: "widget_list",
    Method.WIDGET_SET_PINNED: "widget_set_pinned",
    Method.WIDGET_DELETE: "widget_delete",
    Method.WIDGET_PROPOSE_FROM_CONVERSATION: "widget_propose",
    Method.WIDGET_CONFIRM_SAVE: "widget_confirm",
    Method.WIDGET_RUN: "widget_run",
}


def _month_start_epoch() -> int:
    """Unix-epoch seconds for 00:00 on the first of the current month (UTC).

    'This month's tokens' is 'usage since this epoch' — the token meter sums
    ``usage_log`` rows at or after it. UTC matches how usage rows are stamped
    (``int(time.time())``)."""
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return int(start.timestamp())


def _valid_http_url(url) -> bool:
    """A custom-server base URL is accepted only when it is an ``http://`` or
    ``https://`` URL with a host after the scheme. ``http://`` is deliberately
    permitted — a custom server is the ONE allowed plain-HTTP case (localhost/LAN
    model hosts). No other scheme (``file:``, ``ftp:``, …) is ever accepted."""
    if not isinstance(url, str):
        return False
    for scheme in ("http://", "https://"):
        if url.startswith(scheme) and len(url) > len(scheme):
            return True
    return False


def _plain(exc: Exception) -> str:
    """A user-ready sentence for a handler failure — never the raw exception."""
    if isinstance(exc, RuntimeError) and str(exc):
        return str(exc)
    return _GENERIC_TURN_ERROR


def main() -> None:
    # §4.7: build the tool registry profile-agnostically — both v1 profiles register
    # the same §4.2 SAFE tool set plus the dev_only run_command (hidden from the SAFE
    # view). The server resolves the *persisted* active profile on its worker thread
    # (with the store), derives its policy mode (policy.py), and consults both per-use.
    profile = resolve_active_profile()
    shell_bridge = IpcShellBridge()             # sender bound by the server below
    registry = build_registry(profile, shell_bridge=shell_bridge)

    # The real SQLite Store + UndoManager are built by the server on its worker
    # thread (sqlite3 connections are single-thread), so main() supplies a factory
    # rather than a live connection. ADDISON_DB_PATH keeps dev/tests off ~/.addison.
    db_path = default_db_path()

    def _store_factory() -> Store:
        return Store(db_path)

    def _provider_key_getter(provider_id: str):
        """A per-call keychain getter for one provider (§5). The key is fetched fresh
        at the moment of use and kept only in the returned callable's local — never
        cached. Anthropic keeps the dev env-var fallback so the core is runnable
        without the desktop shell; other providers have keychain only."""

        def getter() -> str:
            try:
                key = shell_bridge.get_provider_key(provider_id)
            except RuntimeError:
                key = ""
            if not key and provider_id == "anthropic":
                # DEV FALLBACK — remove once BYOK-via-keychain is the only path.
                key = os.environ.get("ANTHROPIC_API_KEY", "")
            return key

        return getter

    _api_key_getter = _provider_key_getter("anthropic")

    def _primary_key_available() -> bool:
        # §4.6 probe: reuse the exact Anthropic getter — no key means this turn runs
        # on the Setup Assistant relay instead. Read fresh each turn, so adding a
        # key mid-conversation flips routing to PRIMARY with no restart.
        return bool(_api_key_getter())

    def _provider_key_present(provider_id: str) -> bool:
        # provider.list's implicit-connected signal for a legacy/migrated key.
        return bool(_provider_key_getter(provider_id)())

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

    def _connect_provider(provider_id: str, base_url: str | None) -> list[CloudModel]:
        """The "one tiny request" provider.connect makes: validate the stored key/
        server, register a provider instance per model in the shared ModelRouter, and
        return that provider's catalog. Raises RuntimeError with a plain message on
        failure (bad key, unreachable host) — the server turns it into the card's error
        line. The key rides only inside each getter, fetched per request, never cached
        here (§8.3)."""
        getter = _provider_key_getter(provider_id)
        if provider_id == "anthropic":
            # The live catalog fetch IS the validating request (it 401s on a bad key).
            try:
                models = fetch_cloud_catalog(getter)
            except CatalogFetchError:
                raise RuntimeError("That key doesn't work. Check it and try again.") from None
            for entry in models:
                model_router.register_primary_model(entry.id, _build_cloud_provider(entry))
            return models
        if provider_id == "openai":
            openai_list_models("https://api.openai.com/v1", getter)  # validates the key
            models = static_catalog_for("openai")
            for entry in models:
                model_router.register_primary_model(
                    entry.id, OpenAIProvider(model=entry.id, api_key_getter=getter)
                )
            return models
        if provider_id == "google":
            google_list_models(getter)  # validates the key
            models = static_catalog_for("google")
            for entry in models:
                model_router.register_primary_model(
                    entry.id, GoogleProvider(model=entry.id, api_key_getter=getter)
                )
            return models
        if provider_id == "custom":
            # GET {base}/v1/models both validates and lists the server's models; an
            # empty/unlistable server falls back to one visible "Custom model" entry.
            ids = openai_list_models(base_url, getter, require_key=False)
            if ids:
                models = [
                    CloudModel(id=mid, label=mid, description="", provider="custom") for mid in ids
                ]
            else:
                models = [
                    CloudModel(id="custom-model", label="Custom model", description="", provider="custom")
                ]
            for entry in models:
                model_router.register_primary_model(
                    entry.id,
                    OpenAIProvider(
                        model=entry.id,
                        api_key_getter=getter,
                        base_url=base_url,
                        require_key=False,
                        service_label="the server",
                    ),
                )
            return models
        raise RuntimeError("That provider isn't available.")

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
        primary_prompt=load_primary_prompt(),
        cloud_catalog=catalog,
        cloud_fetcher=_fetch_live_catalog,
        cloud_provider_factory=_build_cloud_provider,
        connect_provider=_connect_provider,
        provider_key_probe=_provider_key_present,
    )
    # §4.7: the server re-resolves the active profile from the store on its worker
    # thread (profile.get/set) and consults it per-use for the onboarding path, raw
    # diagnostics, and routine-plan visibility. The startup registry is profile-agnostic
    # here because both v1 profiles register the same §4.2 tool set (build_registry).
    server.run()


if __name__ == "__main__":
    # `--cli` runs the step-4 terminal harness; the bare entry point runs the
    # step-7 JSON-RPC stdio loop the desktop shell speaks to.
    if "--cli" in sys.argv[1:]:
        run_cli()
    else:
        main()
