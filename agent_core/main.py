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

from agent_core import automation_nonce, live_db_guard
from agent_core.mcp_catalog import McpCatalog
from agent_core.mcp_client import call_tool as mcp_call_tool
from agent_core.mcp_client import discover_tools
from agent_core.memory.store import Store
from agent_core.models_catalog import (
    CatalogFetchError,
    CloudModel,
    catalog_from_live_ids,
    default_cloud_model,
    fetch_cloud_catalog,
    load_cloud_catalog,
)
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, mode_for_profile
from agent_core.profiles import Profile, ProfileId, resolve_active_profile
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ModelRole, technical_detail
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
    _ANSWER_AFTER_STOP_MESSAGE,
    _ANSWER_NOT_PENDING_MESSAGE,
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
from agent_core.rpc.automations import AutomationsMixin
from agent_core.rpc.conversation import ConversationMixin
from agent_core.rpc.cost_plan import CostPlanMixin
from agent_core.rpc.guards import GuardsMixin
from agent_core.rpc.mcp import McpMixin
from agent_core.rpc.models import ModelsMixin
from agent_core.rpc.profile import ProfileMixin
from agent_core.rpc.providers import ProvidersMixin
from agent_core.rpc.routines import RoutinesMixin
from agent_core.rpc.routing import RoutingMixin
from agent_core.rpc.skills import SkillsMixin
from agent_core.rpc.snapshots import SnapshotsMixin, snapshot_list_from_payloads
from agent_core.rpc.undo import UndoMixin
from agent_core.rpc.widgets import WidgetsMixin
from agent_core.rpc.workspace import WorkspaceMixin
from agent_core.secret_presence import SecretPresence
from agent_core.shell_bridge import IpcShellBridge, ServerShellBridge
from agent_core.snapshots.file_revert import FileRevertManager
from agent_core.snapshots.snapshot_manager import (
    SnapshotManager,
    rebuild_rows_from_payloads,
    recover_payloads_from_disk,
    select_payload_to_restore,
)
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.arm_automation import ArmAutomationTool
from agent_core.tools.base import MAX_PERMISSION_DETAIL_CHARS, ActionSnapshot
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.create_automation import CreateAutomationTool
from agent_core.tools.disarm_automation import DisarmAutomationTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.read_project_file import ReadProjectFileTool
from agent_core.tools.read_web_page import ReadWebPageTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import RunCommandTool
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.snapshot_now import SnapshotNowTool
from agent_core.tools.web_search import WebSearchTool
from agent_core.tools.write_project_file import WriteProjectFileTool

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


def build_registry(
    profile: Profile | None = None,
    shell_bridge=None,
    snapshot_manager_ref: Callable[[], SnapshotManager | None] | None = None,
    on_snapshot_captured: Callable[[], None] | None = None,
    store_ref: Callable[[], Store | None] | None = None,
) -> ToolRegistry:
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

    ``snapshot_manager_ref`` is the late-bound handle ``snapshot_now`` uses to reach
    the ``SnapshotManager`` (G3): the registry is built here in ``main()`` BEFORE the
    worker thread builds the store and manager (``_ensure_built``), so the tool holds
    a zero-arg callable resolved at execute time rather than a manager instance. Left
    None (the CLI path, tests) the tool registers normally and answers "can't save a
    restore point just yet" until a manager exists. ``on_snapshot_captured`` is run
    after a successful capture so a save via the tool clears the server's sticky
    capture-failure warning, exactly as the Settings control does.

    ``store_ref`` is the same late-bound shape for the same reason (step 8 phase 2):
    ``create_automation`` writes a row, and the ``Store`` is built later on the
    worker thread. Left None, the tool registers normally and answers "can't save an
    automation just yet" — the honest answer on a path with no store at all.
    """
    profile = profile or resolve_active_profile()
    # None → a ref that always resolves to no manager, so a snapshot_now built
    # without wiring (CLI, tests) is honest about not being able to save yet.
    manager_ref = snapshot_manager_ref if snapshot_manager_ref is not None else (lambda: None)
    # Same shape, same honesty, for the tool that writes an automation row.
    live_store_ref = store_ref if store_ref is not None else (lambda: None)
    all_tools = {
        "web_search": WebSearchTool(),
        "read_web_page": ReadWebPageTool(),
        "read_file": ReadFileTool(),
        "read_clipboard": ReadClipboardTool(),
        "calculator": CalculatorTool(),
        "save_file": SaveFileTool(shell_bridge=shell_bridge),
        "draft_message": DraftMessageTool(shell_bridge=shell_bridge),
        "open_link": OpenLinkTool(),
        "snapshot_now": SnapshotNowTool(
            manager_ref=manager_ref, on_captured=on_snapshot_captured
        ),
    }
    registry = ToolRegistry()
    for tool_id in profile.tool_ids:
        registry.register(all_tools[tool_id])
    # OPEN-mode only, dev_only: real command execution. Registered once in the shared
    # registry; hidden from the SAFE view. Exempt from the undo check BECAUSE it is
    # dev_only and never reachable from SAFE mode (registry.register / run_command.py).
    registry.register(RunCommandTool(), dev_only=True)
    # OPEN-mode coding harness (step 5). ALWAYS registered but open_only, so hidden
    # from the SAFE view and refused at dispatch outside OPEN — the confinement layer
    # (orchestrator/engine) additionally keeps them to trusted roots. The write tool
    # is open_only but undo-ENFORCED (allow_missing_undo defaults False, R3): a real
    # undo() is mandatory, so registration RAISES if a future edit drops it. Its undo
    # bridge is injected here (used only by undo(), which gets no ExecutionContext).
    registry.register(ReadProjectFileTool(), open_only=True)
    registry.register(WriteProjectFileTool(shell_bridge=shell_bridge), open_only=True)
    # Step 8 phase 2: WRITING an automation down (never arming one — that is phase 3
    # and does not exist). ``open_only`` on write_project_file's exact terms (R3):
    # hidden from the SAFE view and refused at dispatch outside OPEN, yet
    # undo-ENFORCED, because it is MEDIUM with a real undo() that deletes the row it
    # wrote. ``dev_only`` would waive that check for a tool that does not need the
    # waiver. Its store is late-bound (see ``store_ref`` above).
    registry.register(CreateAutomationTool(store_ref=live_store_ref), open_only=True)
    # Step 8 phase 3: SWITCHING one on, and switching it off again. Both are
    # ``live_only`` — refused from a routine step and a widget's Run pill, because
    # the ceremony belongs where a person is present and reading (plan §5.10) — and
    # both are absent from the SAFE view.
    #
    # THE TWO REGISTRATIONS ARE DELIBERATELY DIFFERENT, and reading them as a typo
    # is the mistake to avoid:
    #
    #   * ``arm_automation`` is ``open_only``, i.e. undo-ENFORCED. It is HIGH with a
    #     REAL ``undo()`` — it disarms — so the single most important check in the
    #     codebase must keep applying to it: an edit that drops the method has to
    #     fail registration. Its ``shell_bridge`` is injected here because ``undo()``
    #     gets no ExecutionContext (``save_file``'s pattern).
    #   * ``disarm_automation`` is ``dev_only``, i.e. it TAKES the undo waiver, for
    #     the one reason the waiver exists: it genuinely has no undo. Undoing a
    #     disarm would be an ARM performed by the UndoManager with no card and no
    #     typed code — the ceremony walked around from the inside. Registering it
    #     LOW instead would be the cheaper dodge and a false statement about the
    #     tier: LOW means read-only (RiskTier), and this changes what the operating
    #     system is running.
    registry.register(
        ArmAutomationTool(store_ref=live_store_ref, shell_bridge=shell_bridge),
        open_only=True,
        live_only=True,
    )
    registry.register(
        DisarmAutomationTool(store_ref=live_store_ref), dev_only=True, live_only=True
    )
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

    def handler(
        tool_id: str, detail: str | None = None, arming: dict | None = None
    ) -> PermissionStatus:
        if arming is not None:
            # THE KEYWORD CARD HAS NO TERMINAL FORM, and inventing one here would be
            # a second implementation of the ceremony — a second place for the
            # comparison, the attempt budget and the preview to be subtly weaker
            # than the real one. The CLI is a dev harness with no shell bridge, so
            # arming could not reach the operating system from it anyway; saying so
            # is the honest answer and it is also a refusal, which is the safe way
            # to be wrong.
            print("\nSwitching an automation on has to be done in the Addison app.")
            return PermissionStatus.DENIED
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
    # No snapshot_manager_ref here, knowingly: the CLI loop has no SnapshotManager
    # (only the in-memory undo stub), so snapshot_now answers its "can't save a
    # restore point just yet" sentence forever on this path. Misleading-but-honest
    # is accepted for a dev-only loop; the fix is a manager, not a message
    # (adversarial pass, 2026-07-24).
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
    GuardsMixin,
    RoutingMixin,
    CostPlanMixin,
    WorkspaceMixin,
    McpMixin,
    AutomationsMixin,
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
        shell_bridge: ServerShellBridge | None = None,
        conversation_id: str | None = None,
        primary_key_probe=None,
        primary_key_turn_probe=None,
        setup_prompt: str | None = None,
        primary_prompt: str | None = None,
        ollama_base_url: str | None = None,
        ollama_client=None,
        cloud_catalog: list[CloudModel] | None = None,
        cloud_fetcher=None,
        cloud_provider_factory=None,
        connect_provider=None,
        provider_key_probe=None,
        mcp_discover=None,
        mcp_call=None,
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
        # (provider_id) -> bool telling whether a key is stored. It is NOT a presence
        # source any more (plan §4.1 — provider_config is): the two callers left are
        # both person-driven, provider.connect recording what it just learned and the
        # post-restore keyless note. Both None in CLI/tests
        # that don't wire them — provider.* then reports metadata only, no live connect.
        self._connect_provider = connect_provider
        self._provider_key_probe = provider_key_probe
        self._providers_reconnected = False
        # Local-setup (§4.1.2) talks to Ollama over HTTP. base_url/client default
        # to the real localhost instance; tests inject an httpx.MockTransport
        # client so no real Ollama (or network) is ever touched.
        self._ollama_base_url = ollama_base_url
        self._ollama_client = ollama_client
        # Step 7 phases 2–3 (MCP discovery, then dispatch). The catalog holds what each
        # configured tool server last offered, IN MEMORY ONLY: a catalog is the
        # server's truth rather than Addison's configuration, and `mcp_servers` is
        # snapshot-captured, so writing a stranger's names and prose there would copy
        # untrusted text into every later payload and sidecar. After a restart every
        # row honestly reads "not checked yet" — which is also why nothing here
        # connects at start-up: discovery is on demand only.
        #
        # `_mcp_discover` and `mcp_call` are the two network seams, injected the way
        # `_ollama_client` is so tests drive an httpx.MockTransport instead of a real
        # server. The real ones own and close their own client per refresh and per
        # call — one call, one session, one budget (phase-3 decision 3).
        #
        # `endpoint_for` is the OTHER half of dispatch and is deliberately a lookup
        # rather than a value: a tool resolves its server's address at the moment of
        # use, so a server removed or renamed after the check that registered it
        # refuses cleanly instead of being called at an address the person can no
        # longer see.
        self._mcp_catalog = McpCatalog(
            endpoint_for=self._mcp_endpoint_for,
            call_tool=mcp_call or mcp_call_tool,
        )
        self._mcp_discover = mcp_discover or discover_tools
        # §4.6 Setup Assistant handoff: with no PRIMARY key yet, a turn runs on the
        # SETUP_ASSISTANT relay under its onboarding system prompt. ``primary_key_probe``
        # is a ()-> bool that reports whether a real PRIMARY key is available right now
        # (it re-reads the keychain per call, so the handoff needs no other state). When
        # None (CLI/tests), the key is treated as present — normal PRIMARY routing.
        # It may also RAISE RuntimeError, which is the third answer: the read itself
        # failed, so a key may well exist. ``_primary_key_status`` turns that into
        # SecretPresence.UNKNOWN, and no keychain failure can reach the relay branch.
        # ``primary_key_turn_probe`` is the same contract with `fresh` semantics —
        # used by the per-turn path only, so a person's message may retry past a
        # dismissed dialog while the launch/poll probes stay quiet. Falls back to
        # ``primary_key_probe`` when not wired (CLI/tests).
        self._primary_key_probe = primary_key_probe
        self._primary_key_turn_probe = primary_key_turn_probe
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
        # The review surface's per-file revert (phase-3 plan Build §3) — a THIRD
        # mechanism beside the two above, never a use of either. See file_revert.py.
        self._file_revert_manager: FileRevertManager | None = None
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
        # The answering candidate for the in-flight turn (D5), stashed by
        # _record_answered (orchestrator on_answered) and attached to the reply.
        self._answered_with: dict | None = None
        self._draft_routine = None             # pending §6.3 proposal awaiting confirmSave
        self._draft_widget = None              # pending widget proposal awaiting confirmSave
        # The most recently RUN saved routine this session — a widget proposed
        # right after a run offers that routine (mirrors "the last turn ran a
        # saved routine" heuristic; display-only signal, never a permission input).
        self._last_run_routine_id: str | None = None

        self._queue: queue.Queue = queue.Queue()
        self._perm_lock = threading.Lock()
        self._permission_waiters: dict[str, dict] = {}
        # THE CARD DIES WITH ITS TURN (KNOWN-BUGS #4, owner decision 2026-08-09).
        # Raised by ``conversation.stop`` and lowered when the worker picks up its
        # next job, so it always describes the job that is running RIGHT NOW. While
        # it is up no card may be raised (``_on_permission_request``, the routine
        # engine's ask) and every card already up has been answered "no" — a person
        # who ended a turn has not consented to anything that turn was about to do,
        # and a card left actionable is a way for them to consent to it minutes
        # later, to a step whose reason has scrolled away.
        #
        # Guarded by ``_perm_lock``, which the read loop and the worker already
        # share for the waiters: Stop arrives on the read loop while the worker is
        # blocked inside ``_ask_once``, so the flag and the waiter it invalidates
        # must move together or a card can be raised in the gap between them.
        self._turn_stopped = False
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
    def file_revert_manager(self) -> FileRevertManager:
        assert self._file_revert_manager is not None, (
            "file_revert_manager accessed before _ensure_built()"
        )
        return self._file_revert_manager

    @file_revert_manager.setter
    def file_revert_manager(self, value: FileRevertManager) -> None:
        self._file_revert_manager = value

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
            # Display-only provenance for a snapshot row (C6 — never filtered). It
            # reports 'custom' when the active profile is Custom, so a restore points
            # list can SHOW where a row was made, even though Custom derives OPEN for
            # every behavioural purpose. This is the ONLY place 'custom' is written as
            # a mode: routines and widgets stamp ``mode.value``, which is 'safe' or
            # 'open' and never 'custom' (D6).
            #
            # The routine-side availability checks still compare against 'open' and
            # stay untouched. The WIDGET side no longer compares at all — it asks what
            # a widget needs (2026-08-06, rpc/widgets.py::_widget_needs_dev) — so a
            # value added here could not reach it even in principle.
            mode_ref=lambda: (
                "custom"
                if self._active_profile is not None
                and self._active_profile.id is ProfileId.CUSTOM
                else mode_for_profile(self._active_profile).value
            ),
        )
        self.undo_manager = UndoManager(store=self.store, tool_registry=self.tool_registry)
        # BESIDE the UndoManager and sharing nothing with it but the table (phase-3
        # plan Build §3). Per-path, out-of-order, chain-collapsing and
        # write_project_file-only: a third mechanism, on the terms CLAUDE.md already
        # sets for SnapshotManager — complementary, independent, never calling each
        # other. It is handed no registry and no undo manager, so the redo stack is not
        # reachable from it even by accident.
        self.file_revert_manager = FileRevertManager(
            store=self.store, shell_bridge=self._shell_bridge
        )
        # §4.5 action-snapshot retention, and the ONLY call site: the spec asks for
        # this "on startup", and _ensure_built IS the startup — _worker_loop runs it
        # once before it dequeues its first job.
        #
        # Why a prune here can never run mid-undo or race a record(): all store
        # access is confined to this one worker thread, undo and every tool call are
        # jobs ON that thread, and this method is not reentrant (it returns early
        # once the orchestrator exists). So nothing can be in flight while this line
        # runs — which stays true on the two paths that reach _ensure_built later
        # than launch (a post-rebuild retry, a conversation.list after a failed
        # build), because those are themselves jobs and no other job runs beside them.
        # That is why this sits here rather than in the record path where
        # SnapshotManager puts its own prune: that one runs INSIDE a capture and pays
        # for it with a `prune=False` reentrancy escape (_capture).
        #
        # Window defaults live in UndoManager.prune (§4.5's own module), so nothing
        # is restated here.
        try:
            self.undo_manager.prune()
        except Exception:
            # Housekeeping must never cost a session. A retention DELETE that fails
            # (a locked or damaged database) would otherwise escape _ensure_built and
            # turn every later request into "couldn't open its settings file" — the
            # exact trade SnapshotManager._capture makes for the same reason.
            pass
        self.orchestrator = Orchestrator(
            model_router=self.model_router,
            tool_registry=self.tool_registry,
            permission_gate=self.permission_gate,
            undo_manager=self.undo_manager,
            stream_to_frontend=self._emit_stream_chunk,
            on_activity=self._emit_activity,
            on_usage=self._record_usage,
            on_tool_audit=self._record_tool_audit,
            # The two halves of "what did the model layer do": on_usage records the
            # calls that worked, this one the calls that did not. Only the first
            # existed until 2026-08-07, so a provider that never once succeeded was
            # invisible to every query anyone could run.
            on_provider_attempt=self._record_provider_attempt,
            shell_bridge=self._shell_bridge,
            # Custom-profile guards (D3): the one resolution function, so the live
            # turn honours the same posture as the widget rail and routine engine.
            guards_provider=self._effective_guards,
            # Routing (step 3, D4): the ordered fallback chain for the turn, the
            # answering-candidate report (answeredWith, D5), and model labels for the
            # chip + fallback note.
            routing_chain=self._routing_chain,
            on_answered=self._record_answered,
            model_label=self._model_label,
            # A rejected key marks the provider (plan §5.2). The store write and the
            # "told once" answer live in rpc/providers.py; the sentence and the
            # degradation live in the orchestrator.
            on_auth_rejected=self._record_key_rejected,
            provider_label=self._provider_display_label,
            # Workspace-trust confinement (step 5, D3): resolves whether a path is
            # inside a trusted root AND past the data-dir floor, reading the store.
            trust_check=self._is_trusted_path,
            forbidden_check=self._is_forbidden_call,
            trusted_roots=self._trusted_roots,
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
            # Same guard resolution as the live loop (D3) — a routine can never
            # out- or under-permission the conversation.
            guards_provider=self._effective_guards,
            # Same confinement resolver as the live loop (step 5, D3).
            trust_check=self._is_trusted_path,
            forbidden_check=self._is_forbidden_call,
            trusted_roots=self._trusted_roots,
            on_tool_audit=self._record_tool_audit,
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

    def _raw_detail(self, exc: BaseException) -> dict | None:
        """Developer-profile raw diagnostics for an error frame, or None for Simple
        (which is unchanged). This adds VISIBILITY only — it never changes control
        flow or the plain message (§8.7).

        WHAT IS IN IT is ``providers.base.technical_detail``'s to decide, and it owns
        why: for a provider failure the fold carries the provider, the HTTP status
        and the server's own sentence beside the repr; for everything else it is the
        repr alone, exactly as before.

        ``BaseException``, not ``Exception``: ``LiveDatabaseBlocked`` is one
        (live_db_guard.py) and the two handlers that name it want a raw detail like
        every other error. Widening the annotation adds no caller and loses nothing —
        ``repr`` is defined on both."""
        profile = self._active_profile
        if profile is not None and profile.raw_diagnostics:
            return {"raw": technical_detail(exc)}
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
            # INLINE, and it has to be: Stop's whole job is to reach a worker that
            # is blocked waiting for a permission answer. Queued behind the turn it
            # is trying to end, it would run after that turn finished — which is to
            # say never, for the only case it exists for.
            Method.CONVERSATION_STOP: self._handle_conversation_stop,
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
            _GUARDS_JOBS,
            _ROUTING_JOBS,
            _COSTPLAN_JOBS,
            _WORKSPACE_JOBS,
            _MCP_JOBS,
            _AUTOMATION_JOBS,
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
        except live_db_guard.LiveDatabaseBlocked as exc:
            # NAMED, and it has to be. The guard raises a BaseException so no broad
            # handler can quieten it — but a BaseException escaping this thread's
            # run() kills the worker, which is precisely the unrecoverable state the
            # comment above describes. Caught here, its own sentence becomes the
            # build error, so every later job says which guard refused instead of
            # "couldn't open its settings file" (see agent_core/live_db_guard.py).
            self._build_error = str(exc)
            self._build_error_detail = self._raw_detail(exc)
        except Exception as exc:
            self._build_error = _STORE_UNAVAILABLE_MESSAGE
            self._build_error_detail = self._raw_detail(exc)
        while True:
            job = self._queue.get()
            if job is None:
                break
            kind, params, request_id = job
            # A new job is a new turn's worth of consent: whatever was stopped, it
            # was the job before this one. Lowered HERE rather than in the send
            # handler because every job that can raise a card (a routine run, a
            # widget run, a message) has to start from an unstopped state, and jobs
            # run one at a time — so "the job being dequeued" and "the job a Stop
            # could have been aimed at" can never be the same one.
            with self._perm_lock:
                self._turn_stopped = False
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
                elif kind == "widget_set_state":
                    self._respond(request_id, self._widget_set_state(params))
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
                elif kind == "guards_get":
                    self._respond(request_id, self._guards_get())
                elif kind == "guards_set":
                    self._respond(request_id, self._guards_set(params))
                elif kind == "routing_get":
                    self._respond(request_id, self._routing_get())
                elif kind == "routing_set":
                    self._respond(request_id, self._routing_set(params))
                elif kind == "endpoint_propose":
                    self._respond(request_id, self._endpoint_propose())
                elif kind == "endpoint_confirm_add":
                    self._respond(request_id, self._endpoint_confirm_add(params))
                elif kind == "costplan_propose":
                    self._respond(request_id, self._cost_plan_propose())
                elif kind == "costplan_apply":
                    self._respond(request_id, self._cost_plan_apply(params))
                elif kind == "workspace_list":
                    self._respond(request_id, self._workspace_list())
                elif kind == "workspace_grant":
                    self._respond(request_id, self._workspace_grant(params))
                elif kind == "workspace_revoke":
                    self._respond(request_id, self._workspace_revoke(params))
                elif kind == "workspace_pick_directory":
                    self._respond(request_id, self._workspace_pick_directory())
                elif kind == "workspace_list_directory":
                    self._respond(request_id, self._workspace_list_directory(params))
                elif kind == "workspace_read_file":
                    self._respond(request_id, self._workspace_read_file(params))
                elif kind == "workspace_list_edits":
                    self._respond(request_id, self._workspace_list_edits())
                elif kind == "workspace_read_edit_diff":
                    self._respond(request_id, self._workspace_read_edit_diff(params))
                elif kind == "workspace_revert_file":
                    self._respond(request_id, self._workspace_revert_file(params))
                elif kind == "mcp_list":
                    self._respond(request_id, self._mcp_list())
                elif kind == "mcp_add":
                    self._respond(request_id, self._mcp_add(params))
                elif kind == "mcp_remove":
                    self._respond(request_id, self._mcp_remove(params))
                elif kind == "mcp_refresh":
                    self._respond(request_id, self._mcp_refresh(params))
                elif kind == "automation_list":
                    self._respond(request_id, self._automation_list())
                elif kind == "automation_remove":
                    self._respond(request_id, self._automation_remove(params))
                elif kind == "automation_status":
                    self._respond(request_id, self._automation_status())
                elif kind == "automation_disarm_orphan":
                    self._respond(request_id, self._automation_disarm_orphan(params))
            except live_db_guard.LiveDatabaseBlocked as exc:
                # A job can reach _ensure_built() too (conversation.list, and every
                # mixin handler that calls it), so the same rule as the startup build
                # applies here: name it, or the BaseException ends the worker thread
                # and every later request hangs with no frame at all. The guard's own
                # sentence is the answer — it is the only thing that says WHY.
                self._respond_error(request_id, _SERVER_ERROR, str(exc), self._raw_detail(exc))
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

    # --- what the OS is actually running (step 8 phase 3) ------------------
    def _automation_status(self) -> dict:
        """automation.status -> ``{armed: [<label>], supported: bool, error?: str}``.

        SERVER MACHINERY, not a mixin method, and that placement is the point:
        ``rpc/automations.py`` is structurally forbidden from reaching the shell
        bridge (``tests/test_automations.py`` pins that it cannot even import it),
        because the module that owns automation CONFIGURATION must never be able to
        touch the operating system. This answer comes from the shell, so it is
        answered here, beside the other things that cross that boundary.

        ASKED, NEVER REMEMBERED (plan §5.6). Armed truth lives in the OS: no column
        stores it, nothing polls for it, and nothing checks at startup — a G3 restore
        can put a ROW back and can never put a JOB back, so after a restore, a
        reinstall, or somebody deleting the file by hand, the honest answer is
        whatever launchd says right now.

        THREE OUTCOMES, KEPT APART. No shell (the CLI, tests) answers "not
        supported" with an empty list, which is true — there is nothing to ask. A
        shell that FAILS answers with the sentence it gave, because "Addison could
        not find out" is not the same as "nothing is running", and a surface that
        collapsed the two would tell somebody their automation was off while it ran.
        Only the shell decides ``supported``: this process does not test the
        platform, it reports what the one that can says."""
        bridge = self._shell_bridge
        if bridge is None:
            return {"armed": [], "supported": False}
        try:
            answer = bridge.list_armed()
        except RuntimeError as exc:
            return {"armed": [], "supported": False, "error": str(exc)}
        except Exception:
            return {"armed": [], "supported": False, "error": _GENERIC_TURN_ERROR}
        armed = answer.get("armed")
        return {
            # Projected, never passed along: a list of strings is what the wire
            # promises, and one malformed entry must not reach a surface.
            "armed": [x for x in armed if isinstance(x, str)] if isinstance(armed, list) else [],
            "supported": bool(answer.get("supported")),
        }

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
        except live_db_guard.LiveDatabaseBlocked as exc:
            # THE GAP THIS CATCH EXISTS FOR. A rebuild refused by the live-database
            # guard is not "your restore points wouldn't go back in" — it is one
            # specific, fixable mistake, and reporting it as _REBUILD_FAILED sent
            # the reader looking for a damaged snapshot that was never there. The
            # guard's own sentence names itself and the file to read; it reaches the
            # RPC answer from here.
            return False, str(exc)
        except Exception:
            return False, _REBUILD_FAILED
        finally:
            self._discard_rebuild(rebuilt)
        # Finish the build normally — _ensure_built opens the rebuilt file and
        # wires every singleton (and clears _build_error), so the session
        # continues in place instead of requiring a restart.
        try:
            self._ensure_built()
        except live_db_guard.LiveDatabaseBlocked as exc:
            return False, str(exc)
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
            # Deliberately NOT a bare handler: ``LiveDatabaseBlocked`` is a
            # BaseException, so a rebuild refused by the live-database guard walks
            # straight through here to _recover_from_sidecars, which names it. This
            # ``except Exception`` used to be the thing that turned that refusal into
            # a flat "rebuild failed" (live_db_guard.py).
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

    def _primary_key_status(self) -> SecretPresence:
        """Is a real PRIMARY key saved right now — present, absent, or unknown?

        **THE ONE CALLER WITH A PERSON BEHIND IT, and the only one that still reads
        the OS.** Everything else answers presence from ``provider_config``
        (plan §4.1); this runs on the person's own message, so a fresh read is both
        affordable and correct — it is what lets a key added mid-conversation take
        effect without a restart, and what makes a key deleted outside Addison stop
        being claimed on the very next turn.

        The probe IS the keychain read, so its RuntimeError means the READ failed, not
        that no key is saved. That distinction is the whole point: a failed read used
        to collapse to False, and False routes a Simple turn to the Setup Assistant
        relay — so dismissing a macOS password dialog quietly sent the person's
        message to an external service while their key sat in the keychain. UNKNOWN is
        answered plainly by the caller instead; ``may_reach_setup_relay`` is the single
        place that rule lives.

        Anything OTHER than a RuntimeError still reads as ABSENT: a probe that fails in
        some way this code cannot interpret must not become a claim about a key.

        Prefers the TURN probe (fresh semantics — may retry past a dismissed dialog,
        because this method only runs on the person's own message) and falls back to
        the plain probe when none is wired.

        What it learns is WRITTEN DOWN (``_record_presence``), which is how the
        stored signal every other consumer reads stays true without anybody polling
        the OS for it."""
        probe = self._primary_key_turn_probe or self._primary_key_probe
        if probe is None:
            # CLI/tests: no probe wired -> treat PRIMARY as ready, and record nothing.
            # A test harness's absence of a probe is not evidence about a keychain.
            return SecretPresence.PRESENT
        try:
            presence = SecretPresence.PRESENT if probe() else SecretPresence.ABSENT
        except RuntimeError:
            presence = SecretPresence.UNKNOWN
        except Exception:
            presence = SecretPresence.ABSENT
        self._record_presence("anthropic", presence)
        return presence

    def _record_presence(self, provider_id: str, presence: SecretPresence) -> None:
        """Persist what a live read just proved, so no later question has to ask the OS.

        NEVER RAISES and never fails a turn: this is bookkeeping beside the answer, not
        the answer. A store that cannot be opened simply leaves the record as it was —
        which is safe in both directions, because a stale record can only be PRESENT or
        UNKNOWN (neither of which may reach the relay) or an ABSENT the next
        person-driven read corrects."""
        if self._store is None:
            return
        try:
            self.store.record_secret_presence(provider_id, presence)
        except Exception:
            pass

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
    def _on_permission_request(
        self, tool_id: str, detail: str | None = None, arming: dict | None = None
    ) -> PermissionStatus:
        """Runs on the worker thread: render the card, block for the answer.

        ``detail`` is set on the destructive-in-OPEN per-invocation path (the exact
        command text, already truncated by the tool) — the card's description then
        names precisely what is being approved this time, because that approval
        never carries over to the next destructive call.

        ``arming`` (step 8 phase 3) turns this into the KEYWORD CARD and is handled
        by ``_ask_with_keyword`` below.

        A STOPPED TURN NEVER GETS A CARD. The worker keeps running after Stop (there
        is no mid-step interrupt in v1), so without this check the turn's next tool
        call would put a fresh, fully live card in front of somebody who has already
        said they were done — the same defect as the card that outlived its turn,
        one step later. Denied without emitting anything: the model is told no, and
        nobody is asked a question they have already answered by stopping."""
        if self._stopped():
            return PermissionStatus.DENIED
        definition = self.tool_registry.get(tool_id).definition
        description = definition.description
        if detail:
            description = f"This time it wants to run: {detail}"
        card = {
            "toolId": tool_id,
            "label": definition.label,
            "description": description,
            "riskTier": definition.risk_tier.value,
        }
        if arming is not None:
            return self._ask_with_keyword(tool_id, card, arming)
        allow, _ = self._ask_once(tool_id, card)
        return PermissionStatus.GRANTED if allow else PermissionStatus.DENIED

    def _ask_once(self, tool_id: str, card: dict) -> tuple[bool, object]:
        """Emit ONE card and block the worker until ``permission.respond`` lands.

        Returns ``(allowed, typed)`` — ``typed`` is whatever the person put in the
        code field, untouched, and is ``None`` on every ordinary card. Factored out
        of ``_on_permission_request`` because the keyword path asks up to
        ``automation_nonce.MAX_ATTEMPTS`` times and the two must not drift into two
        different round-trips."""
        event = threading.Event()
        with self._perm_lock:
            # Registering the waiter and reading the stop flag under ONE lock is
            # what makes Stop race-free: ``_handle_conversation_stop`` takes the
            # same lock to raise the flag and to wake every waiter, so a card is
            # either registered before the stop (and woken by it) or refused here
            # after it. There is no ordering in which one is emitted and nothing
            # answers it.
            if self._turn_stopped:
                return False, None
            self._permission_waiters[tool_id] = {"event": event, "allow": False, "typed": None}
        self._notify(Method.PERMISSION_REQUEST_GRANT, card)
        event.wait()
        with self._perm_lock:
            waiter = self._permission_waiters.pop(tool_id, None)
        if waiter is None:
            return False, None
        return bool(waiter["allow"]), waiter["typed"]

    def _ask_with_keyword(self, tool_id: str, card: dict, arming: dict) -> PermissionStatus:
        """THE KEYWORD GATE (step 8 phase 3, GLOBAL FLOOR G2) — the caller half.

        ``agent_core/automation_nonce.py`` is pure: it mints, normalises and
        compares. The BUDGET and the pending-request bookkeeping are here, because
        they are facts about one request's lifetime rather than arithmetic — and
        because this is the one place that owns the card round-trip.

        **THE CODE NEVER LEAVES THIS PROCESS EXCEPT TOWARD THE WEBVIEW.** It is
        minted here, put on the card, and compared here. It is not returned to the
        gate, not handed to a tool, not written to ``tool_audit``, not put in the
        transcript, and not in any tool_result — a nonce a model can read is a nonce
        a model can type, which is the whole of what this prevents. That property is
        structural rather than careful: nothing outside this method has a reference
        to the value.

        ONE CODE PER REQUEST, up to three tries at it (plan §3). A wrong answer
        re-emits the SAME card with ``attemptsLeft`` decremented rather than failing
        silently — somebody who mistyped needs to be told they mistyped, and a
        ceremony that fails without saying why is one people stop trusting. Running
        out DENIES the request; starting over is a new call, which mints a new code,
        which is what makes guessing pointless rather than merely slow.

        IT TERMINATES, and here is the whole argument: ``attempts_left`` starts at
        ``MAX_ATTEMPTS``, strictly decreases on the only branch that loops, and the
        loop exits at zero — so at most three cards are emitted. Every iteration
        blocks on ``_ask_once``, which returns only when an inbound
        ``permission.respond`` frame sets its event, so no iteration can spin. A
        "no" answer returns immediately, whatever is in the code field."""
        nonce = automation_nonce.mint()
        attempts_left = automation_nonce.MAX_ATTEMPTS
        while True:
            allow, typed = self._ask_once(
                tool_id,
                {**card, "arming": {**arming, "nonce": nonce, "attemptsLeft": attempts_left}},
            )
            if not allow:
                # "Not now" is an answer, not a wrong code. It ends the request at
                # once — the remaining attempts are for somebody who is trying to
                # say yes.
                return PermissionStatus.DENIED
            if automation_nonce.matches(typed, nonce):
                return PermissionStatus.GRANTED
            attempts_left -= 1
            if attempts_left <= 0:
                return PermissionStatus.DENIED

    def _handle_permission_respond(self, params: dict, request_id) -> None:
        """permission.respond {toolId, allow, typed?} — answered INLINE on the read
        loop, which is what lets it wake a worker blocked in ``_ask_once``.

        ``typed`` is the code the person retyped on an arming card. It is stashed on
        the waiter and compared in ``_ask_with_keyword``; it is never persisted,
        never logged, and never echoed back — this handler's whole job is to carry
        it the last few feet. It arrives as whatever the webview sent, including not
        a string at all, and ``automation_nonce.normalise`` turns anything that is
        not one into a wrong answer rather than an exception.

        An answer with NO WAITER is refused rather than swallowed (KNOWN-BUGS #4).
        It used to answer ``{"ok": True}`` whatever it found, so a card the person
        stopped — or one they double-pressed — reported success for an approval that
        authorised nothing, and the two indistinguishable outcomes were the reason
        the stopped card looked alive. Which sentence comes back depends on WHY
        nobody is waiting: a stopped turn is the case that has a next step worth
        naming, and an already-answered card is not a failure at all."""
        tool_id = params.get("toolId")
        allow = bool(params.get("allow"))
        with self._perm_lock:
            waiter = self._permission_waiters.get(tool_id) if isinstance(tool_id, str) else None
            if waiter is not None:
                waiter["allow"] = allow
                waiter["typed"] = params.get("typed")
                waiter["event"].set()
            stopped = self._turn_stopped
        if waiter is None:
            message = _ANSWER_AFTER_STOP_MESSAGE if stopped else _ANSWER_NOT_PENDING_MESSAGE
            self._respond(request_id, {"ok": False, "error": message})
            return
        self._respond(request_id, {"ok": True})

    def _handle_conversation_stop(self, params: dict, request_id) -> None:
        """conversation.stop — the person pressed Stop. THE CARD DIES WITH ITS TURN.

        Answered INLINE on the read loop (see the dispatch table), because the
        worker this has to reach is typically blocked inside ``_ask_once`` waiting
        for the very card this ends.

        What it does, and just as importantly what it does not:

          * every pending permission waiter is resolved as a REFUSAL and woken, so
            the blocked worker gets ``DENIED`` and its tool never runs. A stop is
            not consent, so there is no other honest answer to give the gate;
          * the stop flag stays up for the REST of this job, so the turn — which
            keeps running, there being no mid-step interrupt in v1 — cannot raise a
            second card at somebody who has left;
          * a late ``permission.respond`` for one of those cards then finds no
            waiter and is refused above. **That refusal is the enforcement.** The
            webview greying the card out is presentation, and a stale or
            hand-edited one is answered exactly like an honest one;
          * it does NOT cancel the turn, undo anything, or touch grants. Stop has
            never meant "unhappen"; it means Addison stops asking and stops acting
            on this turn's behalf.

        ``endedRequests`` is how many cards were standing when Stop landed — zero
        for the ordinary case of stopping a turn that was merely thinking."""
        with self._perm_lock:
            self._turn_stopped = True
            waiters = list(self._permission_waiters.values())
            for waiter in waiters:
                waiter["allow"] = False
                waiter["typed"] = None
                waiter["event"].set()
        self._respond(request_id, {"ok": True, "endedRequests": len(waiters)})

    def _stopped(self) -> bool:
        """Has the running job been stopped? Read under the lock that writes it."""
        with self._perm_lock:
            return self._turn_stopped

    # --- usage recording (§4.8 substrate; orchestrator machinery) ---------
    def _record_tool_audit(self, row: dict) -> None:
        """Persist one tool-decision row (step 5.5, item 4).

        Server machinery, the ``_record_usage`` precedent — never a registry tool:
        a tool able to write its own audit row is a tool able to skip one.

        DELIBERATELY NOT DEFENSIVE, and that is a real dependency rather than an
        oversight: this calls ``insert_tool_audit(**row)`` bare, so the only thing
        making a broken audit store survivable is each CALLER's blanket
        ``except``. A second layer of swallowing here would buy little and cost
        something — the failure that matters is rows going missing silently, and
        the fix for that was to make the write observable, not quieter.
        The contract is pinned behaviourally at all three sites instead
        (``test_a_throwing_sink_never_breaks_the_turn`` and its routine twin), so
        a caller that stops swallowing fails a test rather than a person's work.
        A store that isn't up yet simply drops the row."""
        if self.store is None:
            return
        self.store.insert_tool_audit(**row)

    def _record_provider_attempt(self, row: dict) -> None:
        """Persist one FAILED provider call (2026-08-07).

        The `_record_tool_audit` twin, bare for the same reason: the orchestrator's
        `_record_attempt` already swallows, and a second layer here would only make
        a broken store quieter. `usage_log` records the successes; this records the
        other half, which until now was recorded nowhere at all."""
        if self.store is None:
            return
        self.store.insert_provider_attempt(**row)

    def _record_usage(self, usage, latency_ms, provider_id, model_id) -> None:
        """Record one provider call's token usage + latency into ``usage_log``.

        The single choke point every turn's model calls flow through
        (Orchestrator.on_usage). NOT a registry tool — this is server machinery
        (§4.8 precedent). A call that reported no usage (``usage`` is None) or the
        onboarding relay is skipped. Never touches key material.

        ``provider_id``/``model_id`` are the RESOLVED identity of the candidate that
        produced THIS call, supplied by the orchestrator (D5 [N1]). This fixes the
        pre-existing mis-attribution: the previous version re-derived identity from
        (requested_role, model_name) here, so a routed/fallen-forward turn logged the
        catalog default instead of the model that actually answered. The row is now
        the truth of what ran."""
        if usage is None or self._store is None:
            return
        if provider_id == "setup_assistant":
            return  # the free onboarding relay isn't metered
        now = int(time.time())
        self.store.insert_usage(
            id=str(uuid4()),
            conversation_id=self.conversation.id,
            provider=provider_id,
            model=model_id,
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

    def _record_answered(self, model_id, label, free, routed) -> None:
        """Orchestrator ``on_answered`` sink (D5): stash the answering candidate so
        _run_send_message can attach ``answeredWith`` to the reply. Overwritten each
        turn and read once; a turn that raised leaves the prior value, which
        _run_send_message clears before the run and never reads on the error path."""
        self._answered_with = {
            "modelId": model_id,
            "label": label,
            "free": free,
            "routed": routed,
        }

    # (``_usage_identity`` was REMOVED in step 3: it re-derived the usage row's
    # (provider, model) from (requested_role, model_name) here, which mis-attributed a
    # routed / fallen-forward turn to the catalog default. The orchestrator now passes
    # the RESOLVED identity of the candidate that produced each call straight to
    # ``_record_usage`` (D5 [N1]), so the row is the truth of what ran.)

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
# endpoint.* (add-by-prompt, step 4) belong with them: propose reads the live
# conversation + validates a base URL, and confirmAdd runs the provider.connect
# custom path — both Store/router-touching, so both run on the worker.
_PROVIDER_JOBS = {
    Method.PROVIDER_LIST: "provider_list",
    Method.PROVIDER_CONNECT: "provider_connect",
    Method.PROVIDER_DISCONNECT: "provider_disconnect",
    Method.ENDPOINT_PROPOSE_FROM_CONVERSATION: "endpoint_propose",
    Method.ENDPOINT_CONFIRM_ADD: "endpoint_confirm_add",
}

# costPlan.* (make it cheaper, step 4) read/write app_settings + skills and mint a
# make_it_cheaper snapshot, so they run on the worker like every other store op.
_COSTPLAN_JOBS = {
    Method.COSTPLAN_PROPOSE: "costplan_propose",
    Method.COSTPLAN_APPLY: "costplan_apply",
}

# widget.* run on the worker (Store + routine library + live conversation).
_WIDGET_JOBS = {
    Method.WIDGET_LIST: "widget_list",
    Method.WIDGET_SET_PINNED: "widget_set_pinned",
    Method.WIDGET_DELETE: "widget_delete",
    Method.WIDGET_SET_STATE: "widget_set_state",
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

# guards.* touch the Store (read/write app_settings) and mint an anchor through
# the SnapshotManager, so they run on the worker like every other store op.
# Method -> worker job kind.
_GUARDS_JOBS = {
    Method.GUARDS_GET: "guards_get",
    Method.GUARDS_SET: "guards_set",
}

# routing.* read/write app_settings and routing.set mints an auto-snapshot through
# the SnapshotManager (D1 hook), so they run on the worker like every other store op.
_ROUTING_JOBS = {
    Method.ROUTING_GET: "routing_get",
    Method.ROUTING_SET: "routing_set",
}

# workspace.* touch the Store (read/write workspace_trust) and grantTrust mints an
# auto-snapshot through the SnapshotManager, so they run on the worker like every
# other store op. Method -> worker job kind. (Step 5.)
#
# The review surface's read paths (Phase-3 plan Build §1) queue here for BOTH halves of
# that reason at once. They read the `workspace_trust` rows — twice, for the boundary and
# then for `escapes` — and they make a Core -> Shell round-trip, which blocks whichever
# thread makes it; the read loop is the thread that has to deliver the answer. This is
# `provider.connect`'s lesson and `automation.status`'s, on a path a person can click
# repeatedly.
_WORKSPACE_JOBS = {
    Method.WORKSPACE_GRANT_TRUST: "workspace_grant",
    Method.WORKSPACE_REVOKE_TRUST: "workspace_revoke",
    Method.WORKSPACE_LIST: "workspace_list",
    Method.WORKSPACE_PICK_DIRECTORY: "workspace_pick_directory",
    Method.WORKSPACE_LIST_DIRECTORY: "workspace_list_directory",
    Method.WORKSPACE_READ_FILE: "workspace_read_file",
    # The diff and the revert (Build §2/§3) queue here for a THIRD reason on top of
    # those two: `revertFile` WRITES a file, and running it on the worker is what makes
    # it serialise behind an in-flight turn — a revert landing in the middle of a turn
    # that is itself editing the file would be two writers and no lock. The queue is
    # the lock, which is why no extra one exists.
    Method.WORKSPACE_LIST_EDITS: "workspace_list_edits",
    Method.WORKSPACE_READ_EDIT_DIFF: "workspace_read_edit_diff",
    Method.WORKSPACE_REVERT_FILE: "workspace_revert_file",
}

# mcp.* read/write the `mcp_servers` table and mint an auto-snapshot through the
# SnapshotManager, so they run on the worker like every other store op. Method ->
# worker job kind.
#
# `mcp_refresh` (step 7 phase 2) is here for a SECOND reason as well, and it is the
# one provider.connect already taught: it reaches the network. A connect + tools/list
# walk on the read loop would hold the IPC pump for as long as a stranger's server
# felt like taking — the run_command stall, with somebody else's hand on the clock.
# On the worker it queues like a turn, and mcp_client bounds the whole walk to one
# budget on top of that.
_MCP_JOBS = {
    Method.MCP_LIST: "mcp_list",
    Method.MCP_ADD: "mcp_add",
    Method.MCP_REMOVE: "mcp_remove",
    Method.MCP_REFRESH: "mcp_refresh",
}

# automation.* read the `automations` table and `automation.remove` mints an
# auto-snapshot through the SnapshotManager, so they run on the worker like every
# other store op (the sqlite3 connection is bound to that thread). Method -> worker
# job kind, and this table is the ONLY place main.py may name an automation.* method:
# answering one inline on the read loop would put a store read on the wrong thread
# and a snapshot capture beside an in-flight turn. Step 8 phase 1.
#
# `automation.status` (phase 3) does NOT read the store — it asks the shell what
# launchd currently holds — but it queues here for the other half of the same
# reason: a Core -> Shell round-trip blocks whichever thread makes it, and the read
# loop is the thread that has to deliver the ANSWER. Parking it on the worker is
# `provider.connect`'s lesson, and the shell bridge's own docstring says so.
#
# `automation.disarmOrphan` (2026-08-08) queues here for BOTH halves at once: it reads
# the store (is this label still saved?) and it makes a Core -> Shell round-trip to
# switch the job off.
_AUTOMATION_JOBS = {
    Method.AUTOMATION_LIST: "automation_list",
    Method.AUTOMATION_REMOVE: "automation_remove",
    Method.AUTOMATION_STATUS: "automation_status",
    Method.AUTOMATION_DISARM_ORPHAN: "automation_disarm_orphan",
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

    # G3: the snapshot_now tool needs the SnapshotManager, but the manager is built
    # later on the worker thread (server._ensure_built) and the registry has to exist
    # to pass to the server. So the tool gets late-bound closures over a holder that
    # main() fills in AFTER the server is constructed. The manager closure reads the
    # PRIVATE field, not the property: the property asserts before _ensure_built runs,
    # and this ref must resolve to None (not raise) during that window.
    _server_holder: dict = {}

    def _live_snapshot_manager() -> SnapshotManager | None:
        srv = _server_holder.get("server")
        return srv._snapshot_manager if srv is not None else None

    def _live_store() -> Store | None:
        # Step 8 phase 2, and the same window as the manager above: the registry is
        # built here, the Store is built later on the worker thread. Reads the
        # PRIVATE field for the same reason — the property asserts before
        # _ensure_built has run, and this ref must answer None during that window
        # rather than raise. Resolved at execute time, on the worker thread, which
        # is the thread that owns the connection.
        srv = _server_holder.get("server")
        return srv._store if srv is not None else None

    def _clear_snapshot_warning() -> None:
        # Sticky-warning parity with the Settings control (rpc/snapshots._snapshot_create):
        # a successful save proves writes work again, so the "couldn't save a restore
        # point" notice is cleared. Same worker thread as the capture, so this is a
        # plain assignment, not a cross-thread hop.
        srv = _server_holder.get("server")
        if srv is not None:
            srv._snapshot_warning = None

    registry = build_registry(
        profile,
        shell_bridge=shell_bridge,
        snapshot_manager_ref=_live_snapshot_manager,
        on_snapshot_captured=_clear_snapshot_warning,
        store_ref=_live_store,
    )

    # The real SQLite Store + UndoManager are built by the server on its worker
    # thread (sqlite3 connections are single-thread), so main() supplies a factory
    # rather than a live connection. ADDISON_DB_PATH keeps dev/tests off ~/.addison.
    db_path = default_db_path()

    def _store_factory() -> Store:
        return Store(db_path)

    def _provider_key_getter(provider_id: str, fresh: bool = False):
        """A per-call keychain getter for one provider (§5). The key is fetched fresh
        at the moment of use and kept only in the returned callable's local — never
        cached. Anthropic keeps the dev env-var fallback so the core is runnable
        without the desktop shell; other providers have keychain only.

        The getter speaks the shell's own three-way answer, unflattened: the key,
        ``""`` for "nothing saved", and a RuntimeError carrying a plain sentence for
        "the read failed". It used to swallow that RuntimeError into ``""``, which
        told every caller "no key here" whenever the person dismissed a password
        dialog — and "no key here" is what routes a turn to the Setup Assistant
        relay. A locked keychain must never send a message off this machine.

        ``fresh`` rides through to the shell: retry past a remembered failure. Only
        the per-turn probe passes it (see ``_primary_key_turn_available``)."""

        def getter() -> str:
            try:
                key = shell_bridge.get_provider_key(provider_id, fresh=fresh)
            except RuntimeError:
                # DEV FALLBACK, and it applies to an unreadable keychain as much as
                # to an empty one — a dev running without the desktop shell should
                # not care WHY the shell had nothing. Only re-raise when there is no
                # env key to fall back to, so the failure keeps its own sentence.
                if provider_id == "anthropic":
                    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
                    if env_key:
                        return env_key
                raise
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
        #
        # It answers True/False for the two states it can SEE, and lets the getter's
        # RuntimeError out for the third: "the read failed" is not "no key", and the
        # server (_primary_key_status) turns that raise into its own outcome so the
        # relay is unreachable from a keychain failure.
        return bool(_api_key_getter())

    _fresh_anthropic_getter = _provider_key_getter("anthropic", fresh=True)

    def _primary_key_turn_available() -> bool:
        # The PER-TURN probe, and the one place `fresh` is sent: a message is the
        # person acting, so it may retry past a dialog they dismissed earlier this
        # session (the shell re-asks the OS once, then remembers the new outcome).
        # The launch-time catalog probe and the pollers use the plain probe above,
        # so nothing re-prompts without a user action behind it.
        return bool(_fresh_anthropic_getter())

    def _provider_key_present(provider_id: str) -> bool:
        # The remaining PERSON-DRIVEN key probe. Polled callers no longer exist: the
        # dot, provider.list and stats.get all read provider_config now (plan §4.1).
        # What is left is provider.connect writing down what it just learned, and the
        # post-restore keyless note. Raises like the getter on an unreadable keychain;
        # each caller decides what that means (rpc/providers: UNKNOWN, never absent;
        # rpc/snapshots: drop the note rather than claim a key was removed).
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
        # Read ONCE here so a keychain read FAILURE surfaces as itself. The Anthropic
        # branch below turns every fetch failure into "That key doesn't work" — a
        # false statement about a key Addison never managed to read, and one that
        # sends the person off to replace a key that was fine all along. The getter's
        # RuntimeError already carries a plain, user-ready sentence, so letting it
        # out of here is the whole fix. The value is deliberately dropped: every
        # provider below fetches the key again at the moment of use, so nothing holds
        # key material in this frame (§8.3). An EMPTY key falls through unchanged —
        # that is the bad-key/no-key path each branch already handles.
        getter()
        if provider_id == "anthropic":
            # The live catalog fetch IS the validating request (it 401s on a bad key).
            try:
                models = fetch_cloud_catalog(getter)
            except CatalogFetchError:
                raise RuntimeError("That key doesn't work. Check it and try again.") from None
            for entry in models:
                model_router.register_primary_model(entry.id, _build_cloud_provider(entry))
            return models
        # The list call VALIDATES the key and SUPPLIES the models — one request,
        # both jobs. Its reply used to be discarded and the curated static catalog
        # registered instead, which is how a connected Google key could offer two
        # models in the picker and 404 on every message: connect proved the listing
        # worked, never that the ids about to be registered existed. Registering
        # anything the provider did not just list is the bug, not a fallback.
        if provider_id == "openai":
            models = catalog_from_live_ids(
                "openai", openai_list_models("https://api.openai.com/v1", getter)
            )
            for entry in models:
                model_router.register_primary_model(
                    entry.id, OpenAIProvider(model=entry.id, api_key_getter=getter)
                )
            return models
        if provider_id == "google":
            models = catalog_from_live_ids("google", google_list_models(getter))
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
        primary_key_turn_probe=_primary_key_turn_available,
        setup_prompt=load_setup_prompt(),
        primary_prompt=load_primary_prompt(),
        cloud_catalog=catalog,
        cloud_fetcher=_fetch_live_catalog,
        cloud_provider_factory=_build_cloud_provider,
        connect_provider=_connect_provider,
        provider_key_probe=_provider_key_present,
    )
    # Close the late-binding loop: snapshot_now's manager ref and warning-clear
    # closures resolve through this holder once the server exists (see above).
    _server_holder["server"] = server
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
