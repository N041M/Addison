"""Agent Core entrypoint — JSON-RPC 2.0 server over stdio (engineering-spec §1.2, §7).

Spawned and supervised by the Tauri shell as a child process. Reads JSON-RPC
requests from stdin, writes responses/notifications to stdout. No network stack
for local IPC.

This module wires the singletons (Store, ToolRegistry, PermissionGate,
UndoManager, ModelRouter, Orchestrator) and dispatches the §7 methods. For build
steps 1–6 (engineering-spec §11) it can also be driven from a CLI harness
without the shell — see ``run_cli()``.

STATUS: skeleton — dispatch table + stdio loop to be filled per §11 step 7.
"""

from __future__ import annotations

import os
import sys

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.profiles import Profile, resolve_active_profile
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.router import ModelRouter
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


def main() -> None:
    profile = resolve_active_profile()          # §4.7 — SIMPLE until step 11 persists a choice
    registry = build_registry(profile)
    permission_gate = PermissionGate()
    model_router = ModelRouter(configured={})   # populated from provider_config at startup
    _ = (profile, registry, permission_gate, model_router)
    # TODO(step 7): JSON-RPC stdio read/dispatch/write loop over protocol.Method.
    # TODO(step 11): use profile.onboarding to pick Setup Assistant vs. BYOK-first,
    #                and expose profile.{headless_cli,raw_diagnostics,...} to the frontend.
    raise NotImplementedError("JSON-RPC stdio loop — spec §11 step 7.")


if __name__ == "__main__":
    # `--cli` runs the step-4 terminal harness; the bare entry point stays the
    # step-7 JSON-RPC stdio loop (still NotImplementedError until then).
    if "--cli" in sys.argv[1:]:
        run_cli()
    else:
        main()
