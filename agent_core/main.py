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

``JsonRpcServer`` is the composition root: it owns lifecycle, the read loop, the
dispatch table, shared state, and the narrowing store/orchestrator/undo/routine
properties. The §7 handler *bodies* live in per-namespace mixins under
``agent_core/rpc/`` that this class composes (see ``rpc/base.ServerContext``).
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
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_core import live_db_guard
from agent_core.memory.store import Store
from agent_core.models_catalog import (
    CatalogFetchError,
    CloudModel,
    default_cloud_model,
    fetch_cloud_catalog,
    find_cloud_model,
    load_cloud_catalog,
    static_catalog_for,
)
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, mode_for_profile
from agent_core.profiles import Profile, resolve_active_profile
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
from agent_core.rpc.constants import (
    _GENERIC_TURN_ERROR,
    _LOCAL_SETUP_BUSY_MESSAGE,
    _METHOD_NOT_FOUND,
    _NOT_BUILT_MESSAGE,
    _NOTHING_TO_REBUILD_FROM,
    _OLLAMA_NOT_INSTALLED_MESSAGE,
    _REBUILT_MESSAGE,
    _SERVER_ERROR,
    _STORE_UNAVAILABLE_MESSAGE,
    _BYOK_ONBOARDING_MESSAGE as _BYOK_ONBOARDING_MESSAGE,
    _UNKNOWN_PROFILE_MESSAGE as _UNKNOWN_PROFILE_MESSAGE,
)
from agent_core.rpc.conversation import ConversationMixin
from agent_core.rpc.models import ModelsMixin
from agent_core.rpc.profile import ProfileMixin
from agent_core.rpc.providers import ProvidersMixin
from agent_core.rpc.routines import RoutinesMixin
from agent_core.rpc.skills import SkillsMixin
from agent_core.rpc.snapshots import SnapshotsMixin, snapshot_list_from_payloads
from agent_core.rpc.undo import UndoMixin
from agent_core.rpc.widgets import WidgetsMixin
from agent_core.shell_bridge import IpcShellBridge
from agent_core.snapshots.snapshot_manager import (
    SnapshotManager,
    rebuild_rows_from_payloads,
    recover_payloads_from_disk,
    select_payload_to_restore,
)
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import MAX_PERMISSION_DETAIL_CHARS, ActionSnapshot
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.read_web_page import ReadWebPageTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import RunCommandTool
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.web_search import WebSearchTool

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# §4.8 usage-log retention. Keep ~6 months of usage rows; prune opportunistically
# from the record path so the table can't grow without bound. The prune runs once
# every _USAGE_PRUNE_EVERY records (a cheap in-process counter, not on every write).
# These stay module globals of agent_core.main so tests can monkeypatch them and
# _record_usage still reads the patched value through this module's namespace.
_USAGE_RETENTION_SECONDS = 183 * 24 * 60 * 60   # ~6 months, in epoch seconds
_USAGE_PRUNE_EVERY = 50                          # records between opportunistic prunes

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
        "read_web_page": ReadWebPageTool(),
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


# --- G3 cold-start copy -----------------------------------------------------
# These two live here rather than beside their siblings in rpc/constants.py only
# because of who owns which file in the remediation round that added them; they
# belong in rpc/constants.py with _REBUILT_MESSAGE and _NOTHING_TO_REBUILD_FROM,
# and moving them is a rename with no behaviour attached.
#
# Said when the rebuild worked but nothing on disk had ever been proven working.
# _REBUILT_MESSAGE would be a lie here, and this button's entire value is that
# its promise is true — "I put you back on your last working setup" is the
# sentence the user's trust in the floor rests on.
_REBUILT_FROM_UNVERIFIED = (
    "Addison's settings file was damaged. It couldn't find a setup it had seen "
    "working, so it rebuilt from the most recent settings it had saved instead. "
    "Have a look and check things are how you want them. Your chats and saved "
    "keys are untouched."
)
# ...and when restore points were there, readable, and none of them would go
# back in. Deliberately distinct from _NOTHING_TO_REBUILD_FROM: telling someone
# nothing is saved when several things are is a false statement about the
# floor's own storage, and it sends them looking for the wrong problem.
_REBUILD_FAILED = (
    "Addison couldn't open its settings file, and it couldn't rebuild from your "
    "saved restore points either. Restart Addison — nothing was deleted, and "
    "your restore points are still saved."
)


class JsonRpcServer(
    ConversationMixin,
    UndoMixin,
    RoutinesMixin,
    ProfileMixin,
    ModelsMixin,
    ProvidersMixin,
    WidgetsMixin,
    SkillsMixin,
    SnapshotsMixin,
):
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

    The §7 handler bodies are grouped by Method namespace into the mixins this class
    composes (``agent_core/rpc/``); this class keeps the shared plumbing they call
    (``_respond``, ``_mode``, the narrowing properties, ...) plus lifecycle, the read
    loop, and the dispatch table.
    """

    def __init__(
        self,
        *,
        reader,
        writer,
        tool_registry: ToolRegistry,
        store_factory,
        model_router: ModelRouter,
        db_path: str | Path | None = None,
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
        # G3: where the sidecar payloads live, derived from the DB path HERE rather
        # than from the Store — because the one situation this floor exists for is
        # the Store failing to open, and a path that only exists on a live Store is
        # no use then. None where the caller wired no path (CLI-ish tests): the
        # subsystem still works, it just has no belt and no cold-start rebuild.
        self._db_path = Path(db_path) if db_path else None
        self._snapshot_dir = (self._db_path.parent / "snapshots") if self._db_path else None
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
        # ``_store`` is the nullable backing field ("not built yet" is a real
        # state — _record_usage and provider reconnect check it); the ``store``
        # property narrows to Store for the handlers, which all run post-build.
        self._store: Store | None = None
        self._snapshot_manager: SnapshotManager | None = None
        self._undo_manager: UndoManager | None = None
        self._orchestrator: Orchestrator | None = None
        self._routine_builder: RoutineBuilder | None = None
        self._routine_library: RoutineLibrary | None = None
        self._routine_engine: RoutineEngine | None = None

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
        # G3 build-failure state. A store that will not open is answered with one
        # plain sentence per request instead of a dead worker (see _worker_loop);
        # both are CLEARED once a build finally succeeds, so a transient failure
        # doesn't brick the session until restart.
        self._build_error: str | None = None
        self._build_error_detail: dict | None = None
        # Sticky notice that an automatic snapshot failed — surfaced on
        # snapshot.list until the user saves one themselves. It does NOT clear on
        # the next successful auto-capture: a degraded floor that clears itself is
        # a degraded floor nobody sees.
        self._snapshot_warning: str | None = None

        # Method -> handler, built once (see _build_dispatch_table). Built last so
        # every handler it references (and self._queue) already exists.
        self._dispatch_table = self._build_dispatch_table()

    @property
    def store(self) -> Store:
        """The SQLite store, built once on the worker thread (_ensure_built).

        Every handler that touches it runs after the build, so the Optional is
        narrowed HERE rather than at dozens of call sites; reaching it earlier is
        a programming error, not a user-visible state. Code that genuinely means
        "has the store been built yet?" checks ``self._store is None`` instead."""
        assert self._store is not None, "store accessed before _ensure_built()"
        return self._store

    @store.setter
    def store(self, value: Store) -> None:
        self._store = value

    # The same narrowing pattern for the other worker-built singletons: nullable
    # backing field, non-Optional property. Setters exist because _ensure_built
    # (and tests) assign through the public names.
    @property
    def snapshot_manager(self) -> SnapshotManager:
        assert self._snapshot_manager is not None, (
            "snapshot_manager accessed before _ensure_built()"
        )
        return self._snapshot_manager

    @snapshot_manager.setter
    def snapshot_manager(self, value: SnapshotManager) -> None:
        self._snapshot_manager = value

    @property
    def undo_manager(self) -> UndoManager:
        assert self._undo_manager is not None, "undo_manager accessed before _ensure_built()"
        return self._undo_manager

    @undo_manager.setter
    def undo_manager(self, value: UndoManager) -> None:
        self._undo_manager = value

    @property
    def orchestrator(self) -> Orchestrator:
        assert self._orchestrator is not None, "orchestrator accessed before _ensure_built()"
        return self._orchestrator

    @orchestrator.setter
    def orchestrator(self, value: Orchestrator) -> None:
        self._orchestrator = value

    @property
    def routine_builder(self) -> RoutineBuilder:
        assert self._routine_builder is not None, "routine_builder accessed before _ensure_built()"
        return self._routine_builder

    @routine_builder.setter
    def routine_builder(self, value: RoutineBuilder) -> None:
        self._routine_builder = value

    @property
    def routine_library(self) -> RoutineLibrary:
        assert self._routine_library is not None, "routine_library accessed before _ensure_built()"
        return self._routine_library

    @routine_library.setter
    def routine_library(self, value: RoutineLibrary) -> None:
        self._routine_library = value

    @property
    def routine_engine(self) -> RoutineEngine:
        assert self._routine_engine is not None, "routine_engine accessed before _ensure_built()"
        return self._routine_engine

    @routine_engine.setter
    def routine_engine(self, value: RoutineEngine) -> None:
        self._routine_engine = value

    # --- lifecycle --------------------------------------------------------
    def run(self) -> None:
        worker = threading.Thread(target=self._worker_loop, name="turn-worker", daemon=True)
        worker.start()
        self._read_loop()
        self._queue.put(None)   # stop the worker once stdin closes

    def _database_created_by_this_launch(self) -> bool | None:
        """Did the database file come into existence on THIS launch? None when we
        cannot tell.

        The single fact ``SnapshotManager`` needs to decide whether this database
        gets a ``genesis`` bottom row (a permanent, one-click restore target) or
        the cautious ``pre_upgrade`` one. It is asked here, and only from the
        filesystem.

        WHY IT CANNOT BE FOOLED. It is a property of the file, not of anything
        written inside it, so nothing the user or the model can do through
        Addison moves it. The alternative — inferring "fresh" from the contents —
        is what this replaced, and it was wrong for the DEFAULT state of the
        people this app is for: someone who never connects a service, never
        writes a note, never saves a routine and never leaves Simple looks
        byte-identical to a new install no matter how many months of settings,
        widgets and chats they have. Their file, however, has been on disk since
        the day they installed Addison.

        WHY IT IS ASKED HERE and not next to the manager: ``Store.__init__``
        creates the file and applies the schema, so from the first line of the
        build onward the answer is always "it existed". This runs in the last
        instant before anything opens it.

        WHEN IT IS ABSENT: no configured path (CLI-ish callers and tests that
        wire a store factory without one), or a path we cannot even stat. Both
        answer None — "could not find out" — and the manager then writes
        ``pre_upgrade``. That is the cheap direction: being wrongly told your
        setup predates the update costs one honest sentence, while being wrongly
        told your install is brand new hands back the configuration you were
        trying to escape, from a row that cannot be deleted."""
        if self._db_path is None:
            return None
        try:
            os.stat(self._db_path)
        except FileNotFoundError:
            return True
        except OSError:
            # An unreadable parent, a path component that is not a directory, a
            # name the filesystem rejects: we have learned nothing, so say so
            # rather than reading "no file" out of "no answer".
            return None
        return False

    def _ensure_built(self) -> None:
        """Build the SQLite-backed singletons on the worker thread (once)."""
        if self._orchestrator is not None:
            return
        # G3: measured BEFORE the store opens (which creates the file), and
        # re-measured on a rebuild-and-retry — see _database_created_by_this_launch.
        created_the_database = self._database_created_by_this_launch()
        self.store = self._store_factory()
        # Seed the in-house default widgets on a fresh install so the rail isn't empty
        # (flag-gated — deleting them never re-seeds).
        self._seed_default_widgets()
        # §4.7: read the persisted profile now that the store exists (SIMPLE if unset).
        self._active_profile = resolve_active_profile(self.store)
        # GLOBAL FLOOR G3: built before the orchestration machinery so a restore
        # target exists from the first moment the store does. Construction writes
        # the permanent bottom row on a database that has none.
        #
        # ORDERING, LOAD-BEARING: this MUST stay below _seed_default_widgets()
        # above. Genesis is a snapshot of the state at this line, so constructed
        # first it would capture an empty rail — and 'widgets_seeded' is a
        # one-way latch that survives a restore (scope._PRESERVED_SETTING_KEYS),
        # so restoring that genesis would empty the rail permanently, with
        # re-seeding already latched off. There is a test on this order.
        self.snapshot_manager = SnapshotManager(
            store=self.store,
            snapshot_dir=self._snapshot_dir,
            created_the_database=created_the_database,
            app_build_ref=(
                self._shell_bridge.get_app_build_ref if self._shell_bridge else None
            ),
            mode_ref=lambda: mode_for_profile(self._active_profile).value,
        )
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
            # The same Activity Panel the live turn drives. A routine reaches the
            # web through the same tools, so it must name where it went too.
            on_activity=self._emit_activity,
        )
        # The build worked, so a remembered failure is stale — clear it rather than
        # answering "couldn't open its settings file" for the rest of the session.
        self._build_error = None
        self._build_error_detail = None

    # In-house premade widgets seeded on first run, so a fresh install's rail isn't
    # empty. These are ordinary DECLARATIVE stat widgets (invariant 4) built ONLY from
    # existing whitelisted stat sources — no new source, no new execution surface. The
    # 'widgets_seeded' flag makes it strictly first-run: once set, deleting the seeds
    # never brings them back.
    _DEFAULT_WIDGETS = (
        {"kind": "stat", "source": "connections", "title": "Connections"},
        {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"},
    )

    def _seed_default_widgets(self) -> None:
        if self.store.get_setting("widgets_seeded") is not None:
            return
        now = int(time.time())
        for position, spec in enumerate(self._DEFAULT_WIDGETS):
            self.store.insert_widget(
                id=str(uuid4()),
                spec_json=json.dumps(spec),
                pinned=True,
                position=position,
                created_at=now,
                created_in_mode=PolicyMode.SAFE.value,
            )
        self.store.set_setting("widgets_seeded", "1")

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

    def _emit_activity(self, tool_id: str, label: str, detail: str | None = None) -> None:
        """Which step is running, and — when the tool can say — WHAT it is reaching.

        WHY THE DETAIL IS HERE AT ALL. ``read_web_page`` is the first SAFE-view tool
        that sends a request to an address the MODEL chose, with no window opening
        where anyone would see it. ``PermissionGate._grants`` is keyed by tool id
        alone, so once the person has allowed one page read, every later read in the
        session is ungated and goes wherever the model points it — and page text is
        exactly what points it, so injected content can name an address that carries
        what Addison just read out inside the URL. The owner's answer (2026-07-20) is
        VISIBILITY, not per-site grant scoping: showing the destination adds no
        prompts (being asked too often is the complaint that started this work), and
        a person who sees a site they never asked about can stop the turn.

        It is deliberately not read_web_page-shaped. The value is whatever the tool's
        own ``permission_detail`` returns — the same string the permission card
        already uses — so any tool that can name what it is about to touch is
        surfaced here, and nothing in this method knows one tool from another.

        The key is omitted, not sent as null, when a tool has no detail to give: most
        tools don't, and the frontend treats the field as optional.

        The length cap lives in ``tools.base.call_permission_detail``, where the
        value is built, so the panel and the permission card cannot show different
        strings for the same call. The re-cap below is a belt on the boundary itself
        — this method hands a string to the webview, and it should not depend on
        every future caller having gone through that constructor — and it uses the
        same constant so there is one number, not two that have to be kept equal.
        """
        params: dict = {"toolId": tool_id, "label": label}
        if detail:
            params["detail"] = (
                detail
                if len(detail) <= MAX_PERMISSION_DETAIL_CHARS
                else detail[:MAX_PERMISSION_DETAIL_CHARS] + "…"
            )
        self._notify(Method.TOOL_ACTIVITY_UPDATE, params)

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

        # One dict, built once (_build_dispatch_table), maps every known method to
        # its handler. An unknown method answers -32601 exactly as before; the
        # handler call is wrapped so no handler can crash the read loop (-32000 + a
        # plain sentence). The inline-vs-worker split lives inside the handlers: a
        # worker-routed method's handler just enqueues the job (same job kinds, same
        # order behind an in-flight turn), while an inline handler answers here.
        handler = self._dispatch_table.get(method)
        if handler is None:
            self._respond_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
            return
        try:
            handler(params, request_id)
        except Exception as exc:  # never let a handler crash the read loop
            self._respond_error(request_id, _SERVER_ERROR, _plain(exc))

    def _build_dispatch_table(self) -> dict[str, Callable[[dict, Any], None]]:
        """Method -> handler, built once in __init__.

        Store-touching requests run on the worker (SQLite thread affinity), so their
        handler just puts a (kind, params, request_id) job on the queue; permission
        answers and store-free reads answer inline on the read loop. availableRoles,
        provider.*, and the routine/conversation/widget jobs go to the worker for the
        same reason (see the class docstring's threading model): they read the Store or
        make Core -> Shell / HTTPS round-trips the read loop must stay free to deliver.
        """
        def enqueue(kind: str) -> Callable[[dict, Any], None]:
            return lambda params, request_id: self._queue.put((kind, params, request_id))

        table: dict[str, Callable[[dict, Any], None]] = {
            Method.CONVERSATION_SEND_MESSAGE: enqueue("send"),
            Method.UNDO_REDO_LAST_ACTION: enqueue("redo"),
            Method.UNDO_UNDO_LAST_ACTION: enqueue("undo"),
            Method.UNDO_REWIND_CONVERSATION: enqueue("rewind"),
            Method.PERMISSION_RESPOND: self._handle_permission_respond,
            Method.MODEL_AVAILABLE_ROLES: enqueue("available_roles"),
            Method.PROFILE_GET: enqueue("profile_get"),
            Method.PROFILE_SET: enqueue("profile_set"),
            Method.STATS_GET: enqueue("stats_get"),
            # Inline on the read loop (store-free; each owns its own response).
            Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE: self._handle_set_role,
            Method.MODEL_START_LOCAL_SETUP: self._handle_start_local_setup,
        }
        for jobs in (
            _ROUTINE_JOBS,
            _CONVERSATION_JOBS,
            _PROVIDER_JOBS,
            _WIDGET_JOBS,
            _SKILL_JOBS,
            _SNAPSHOT_JOBS,
        ):
            for method_name, kind in jobs.items():
                table[method_name] = enqueue(kind)
        # Reserved-for-later methods answer a plain "not built yet" (empty today).
        for method_name in _NOT_BUILT_METHODS:
            table[method_name] = self._respond_not_built
        return table

    def _respond_not_built(self, params: dict, request_id) -> None:
        """A §7 method reserved for a later build step: a plain 'not built yet'
        error rather than a silent failure (see _NOT_BUILT_METHODS)."""
        self._respond_error(request_id, _SERVER_ERROR, _NOT_BUILT_MESSAGE)

    # --- worker thread (all SQLite-backed work) ---------------------------
    def _worker_loop(self) -> None:
        # G3: a store that will not build is the exact situation the snapshot
        # subsystem exists for, so a build failure must NOT kill this thread. It
        # used to: the raise escaped, the worker died, and every later request hung
        # forever with no error frame — an unrecoverable state produced by the
        # recovery machinery's own absence. Now the failure is remembered and every
        # dequeued job answers with one plain sentence, so the window stays
        # responsive and the user is told what to do.
        try:
            self._ensure_built()
        except Exception as exc:
            self._build_error = _STORE_UNAVAILABLE_MESSAGE
            self._build_error_detail = self._raw_detail(exc)
        while True:
            job = self._queue.get()
            if job is None:
                break
            kind, params, request_id = job
            if self._build_error is not None:
                # THE EXEMPTION. Without it this branch answers EVERY job —
                # including the restore the message above tells the user to run —
                # so the copy would point at a control the same code path
                # guarantees will fail. These two are served store-free from the
                # sidecar files; a successful rebuild clears _build_error, so the
                # session recovers in place rather than requiring a restart.
                if kind in ("snapshot_list", "snapshot_restore_last_working"):
                    self._handle_store_free_snapshot_job(kind, request_id)
                    continue
                self._respond_error(
                    request_id, _SERVER_ERROR, self._build_error, self._build_error_detail
                )
                continue
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
                    self._handle_routine_delete(params, request_id)
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
                elif kind == "conversation_rename":
                    self._handle_rename_conversation(params, request_id)
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
                elif kind == "skill_list":
                    self._respond(request_id, self._skill_list())
                elif kind == "skill_create":
                    self._respond(request_id, self._skill_create(params))
                elif kind == "skill_update":
                    self._respond(request_id, self._skill_update(params))
                elif kind == "skill_set_enabled":
                    self._respond(request_id, self._skill_set_enabled(params))
                elif kind == "skill_delete":
                    self._respond(request_id, self._skill_delete(params))
                elif kind == "snapshot_list":
                    self._respond(request_id, self._snapshot_list())
                elif kind == "snapshot_create":
                    self._respond(request_id, self._snapshot_create())
                elif kind == "snapshot_restore":
                    self._respond(request_id, self._snapshot_restore(params))
                elif kind == "snapshot_restore_last_working":
                    self._respond(request_id, self._snapshot_restore_last_working())
                elif kind == "snapshot_delete":
                    self._respond(request_id, self._snapshot_delete(params))
            except RuntimeError as exc:
                # Provider/tool errors already carry a plain, user-ready sentence.
                self._respond_error(request_id, _SERVER_ERROR, str(exc), self._raw_detail(exc))
            except Exception as exc:
                # Anything else collapses to one plain message — no stack trace (the
                # raw repr is attached only for the Developer profile, §4.7).
                self._respond_error(
                    request_id, _SERVER_ERROR, _GENERIC_TURN_ERROR, self._raw_detail(exc)
                )

    def _handle_routine_delete(self, params: dict, request_id) -> None:
        """routine.delete — hook H4. The snapshot comes FIRST, and a failed
        snapshot REFUSES the delete: deleting a routine cascades to its run
        history and the old content exists nowhere else afterwards, so proceeding
        without a restore point is the one outcome the floor must not allow.
        Refusing is recoverable; an unbackable delete is not."""
        routine_id = params.get("routineId")
        if isinstance(routine_id, str) and self.store.get_routine(routine_id) is not None:
            if not self._snapshot_auto("routine_delete"):
                self._respond(
                    request_id,
                    {
                        "ok": False,
                        "error": (
                            "Addison couldn't save a restore point just now, so it "
                            "didn't delete anything. Try again in a moment."
                        ),
                    },
                )
                return
        if isinstance(routine_id, str):
            self.routine_library.delete(routine_id)
        self._respond(request_id, {"ok": True})

    # --- G3 cold start: the database itself will not open ------------------
    def _handle_store_free_snapshot_job(self, kind: str, request_id) -> None:
        """Answer ``snapshot.list`` / ``snapshot.restoreLastWorking`` with NO Store.

        This is the headline claim of the whole subsystem — "restore always works,
        even from a broken config" — in the one grade of damage a Python-side
        SnapshotManager cannot reach, because it has no store to be constructed
        with. The sidecar files need no schema, no WAL and no sqlite3, so they are
        what is left to work from."""
        payloads = recover_payloads_from_disk(self._snapshot_dir) if self._snapshot_dir else []
        if kind == "snapshot_list":
            # A list is a look. It reads the files and touches nothing else — the
            # rename-and-rebuild below is reserved for the restore the user
            # actually asked for.
            payload = snapshot_list_from_payloads(payloads)
            if not payload["snapshots"]:
                payload["warning"] = _NOTHING_TO_REBUILD_FROM
            self._respond(request_id, payload)
            return
        ok, sentence = self._recover_from_sidecars(payloads)
        self._respond(
            request_id, {"ok": True, "detail": sentence} if ok else {"ok": False, "error": sentence}
        )

    def _recover_from_sidecars(self, payloads: list[dict]) -> tuple[bool, str]:
        """Rebuild a working database from the sidecar payloads. Returns
        ``(ok, sentence)`` — the plain sentence to show the user either way.

        Three outcomes, three sentences, because they are three different
        situations and only one of them is the user's problem to act on: nothing
        was saved, something was saved but would not go back in, or it worked.
        Reporting the middle case as the first one is a false statement about the
        floor's own storage and sends the user looking for the wrong problem.

        The rebuild happens in a SIDE FILE and is swapped in only once it has
        worked. The damaged file used to be renamed aside first, which meant a
        rebuild that then failed left a fresh empty database at the live path —
        so the next click renamed THAT aside too, and the user's real data sank
        one ``.damaged-`` file deeper with every attempt. Nothing moves until
        there is a working replacement to move it for.

        The damaged file is RENAMED ASIDE, never deleted: it may still be
        forensically useful, and destroying the user's data is not ours to do."""
        if not payloads or self._db_path is None:
            return False, _NOTHING_TO_REBUILD_FROM
        rebuilt = Path(f"{self._db_path}.rebuilding-{int(time.time())}")
        try:
            verified = self._rebuild_into(rebuilt, payloads)
            if verified is None:
                return False, _REBUILD_FAILED
            self._move_damaged_db_aside()
            self._swap_in(rebuilt)
        except Exception:
            return False, _REBUILD_FAILED
        finally:
            self._discard_rebuild(rebuilt)
        # Finish the build normally — _ensure_built opens the rebuilt file and
        # wires every singleton (and clears _build_error), so the session
        # continues in place instead of requiring a restart.
        try:
            self._ensure_built()
        except Exception:
            return False, _REBUILD_FAILED
        return True, _REBUILT_MESSAGE if verified else _REBUILT_FROM_UNVERIFIED

    def _rebuild_into(self, path: Path, payloads: list[dict]) -> bool | None:
        """Build a complete replacement database at ``path`` from the payloads
        alone. Returns whether the config it applied had been PROVEN WORKING, or
        None when nothing could be applied at all — three states, because
        "rebuilt from a setup I'd seen working" and "rebuilt from the most recent
        settings I had" are different promises and only one of them is true.

        ``Store(path)`` directly rather than ``self._store_factory()``: the
        factory is bound to the one path that, in this exact situation, holds a
        file that will not open. A recovery that can only build over the wreckage
        cannot be tried and discarded."""
        store = None
        try:
            store = Store(path)
            # The flags travel WITH the payload, so anchors come back as anchors —
            # a rebuild that dropped `undeletable` would quietly convert every G4
            # anchor into an ordinary deletable row, G4 defeated by G3's own
            # recovery machinery with no code path anywhere called "delete".
            rebuild_rows_from_payloads(store, payloads)
            candidates = list(payloads)
            while candidates:
                # ONE function chooses the payload here, in the manager's sidecar
                # arm, and in the listing that named it in the confirm step — so
                # the preview and the button can never describe different
                # restore points.
                payload, verified = select_payload_to_restore(candidates)
                if payload is None:
                    return None
                try:
                    store.apply_config_state(payload["tables"])
                    return verified
                except Exception:
                    # It decoded but would not go back in. Drop it by identity
                    # (two payloads can compare equal) and let the same function
                    # pick the next one, so the fallback order stays the one
                    # rule rather than a second one written here.
                    candidates = [item for item in candidates if item is not payload]
            return None
        except Exception:
            return None
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass

    def _swap_in(self, rebuilt: Path) -> None:
        """Put the rebuilt database where the damaged one used to be.

        The WAL/SHM siblings move with it — a cleanly closed store normally
        leaves none, but a leftover WAL beside a replaced database would be read
        as part of it. They move FIRST and the database itself LAST, so the main
        rename is the commit point: nothing can fail after the database is in
        place, and a rebuild that actually worked can never be reported as one
        that did not."""
        assert self._db_path is not None
        for suffix in ("-wal", "-shm"):
            sibling = Path(f"{rebuilt}{suffix}")
            if sibling.exists():
                os.replace(sibling, Path(f"{self._db_path}{suffix}"))
        os.replace(rebuilt, self._db_path)

    def _discard_rebuild(self, rebuilt: Path) -> None:
        """Clear away a half-built replacement, best effort. Runs after a
        successful swap too, where it finds nothing — cheaper than remembering
        which of the two paths got here."""
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{rebuilt}{suffix}").unlink(missing_ok=True)
            except Exception:
                pass

    def _move_damaged_db_aside(self) -> None:
        """Rename the unopenable database (and its WAL/SHM siblings) out of the
        way so a fresh one can be created beside them."""
        assert self._db_path is not None
        stamp = int(time.time())
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self._db_path) + suffix)
            if source.exists():
                source.rename(Path(f"{self._db_path}.damaged-{stamp}{suffix}"))

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
            waiter = self._permission_waiters.get(tool_id) if isinstance(tool_id, str) else None
            if waiter is not None:
                waiter["allow"] = allow
                waiter["event"].set()
        self._respond(request_id, {"ok": True})

    # --- usage recording (§4.8 substrate; orchestrator machinery) ---------
    def _record_usage(self, usage, latency_ms, requested_role, model_name) -> None:
        """Record one provider call's token usage + latency into ``usage_log``.

        The single choke point every turn's model calls flow through
        (Orchestrator.on_usage). NOT a registry tool — this is server machinery
        (§4.8 precedent). A call that reported no usage (``usage`` is None) or the
        onboarding relay is skipped. Never touches key material."""
        if usage is None or self._store is None:
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

    # --- local model setup (§4.1.2) ---------------------------------------
    # These live on the composition root (not the models mixin): they are OS/
    # threading plumbing — disk/RAM probes and a background pull thread — and the
    # probe helpers (_free_disk_bytes / _total_ram_bytes / _GB) are module globals
    # tests monkeypatch on ``agent_core.main``, so _hardware_refusal must resolve
    # them through THIS module's namespace.
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
    Method.CONVERSATION_RENAME: "conversation_rename",
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

# skill.* run on the worker like every other store op (SQLite thread affinity):
# the sqlite3 connection is bound to the worker thread, so these can't answer
# inline on the read loop. Method -> worker job kind.
_SKILL_JOBS = {
    Method.SKILL_LIST: "skill_list",
    Method.SKILL_CREATE: "skill_create",
    Method.SKILL_UPDATE: "skill_update",
    Method.SKILL_SET_ENABLED: "skill_set_enabled",
    Method.SKILL_DELETE: "skill_delete",
}

# snapshot.* touch the Store and must serialise behind any in-flight turn (a
# restore replaces the config tables wholesale), so they run on the worker like
# every other store op. Method -> worker job kind. Two of these kinds are ALSO
# answered when the store could not be built at all (_worker_loop's exemption).
_SNAPSHOT_JOBS = {
    Method.SNAPSHOT_LIST: "snapshot_list",
    Method.SNAPSHOT_CREATE: "snapshot_create",
    Method.SNAPSHOT_RESTORE: "snapshot_restore",
    Method.SNAPSHOT_RESTORE_LAST_WORKING: "snapshot_restore_last_working",
    Method.SNAPSHOT_DELETE: "snapshot_delete",
}


def _plain(exc: Exception) -> str:
    """A user-ready sentence for a handler failure — never the raw exception."""
    if isinstance(exc, RuntimeError) and str(exc):
        return str(exc)
    return _GENERIC_TURN_ERROR


def main() -> None:
    # This process IS the app, so it is the one thing allowed to open ~/.addison.
    # Importing agent_core armed a default-deny guard over sqlite3.connect; every
    # launch route (env override, bundled binary, `-m agent_core.main`) ends here,
    # and nothing else calls this. See agent_core/live_db_guard.py.
    live_db_guard.allow_live_database()

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
            # provider.connect validates and requires the base URL before we get here.
            assert base_url is not None
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
        # G3: the server derives the sidecar directory from this itself, so the
        # cold-start rebuild exists even when the Store cannot be opened.
        db_path=db_path,
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
