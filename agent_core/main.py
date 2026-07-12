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

from agent_core.permissions.gate import PermissionGate
from agent_core.providers.router import ModelRouter
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.web_search import WebSearchTool


def build_registry() -> ToolRegistry:
    """Register exactly the v1 tool set (engineering-spec §4.2 table).
    Registration will RAISE for any MEDIUM/HIGH tool lacking undo() — that's the
    safety invariant, not a bug."""
    registry = ToolRegistry()
    for tool in (
        WebSearchTool(),
        ReadFileTool(),
        ReadClipboardTool(),
        CalculatorTool(),
        SaveFileTool(),
        DraftMessageTool(),
        OpenLinkTool(),
    ):
        registry.register(tool)
    return registry


def default_db_path() -> str:
    # Local app-data dir, no system/admin path (design-doc §7.8).
    base = os.path.expanduser("~/.addison")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "addison.sqlite3")


def main() -> None:
    registry = build_registry()
    permission_gate = PermissionGate()
    model_router = ModelRouter(configured={})   # populated from provider_config at startup
    _ = (registry, permission_gate, model_router)
    # TODO(step 7): JSON-RPC stdio read/dispatch/write loop over protocol.Method.
    raise NotImplementedError("JSON-RPC stdio loop — spec §11 step 7.")


if __name__ == "__main__":
    main()
