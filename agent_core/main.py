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
from agent_core.profiles import Profile, resolve_active_profile
from agent_core.providers.router import ModelRouter
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.save_file import SaveFileTool
from agent_core.tools.web_search import WebSearchTool


def build_registry(profile: Profile | None = None) -> ToolRegistry:
    """Register the tools the active Profile exposes (engineering-spec §4.2, §4.7).

    A Profile chooses *which* tools are registered; it never changes *how* safety
    is enforced — registration still RAISES for any MEDIUM/HIGH tool lacking undo()
    (that's the safety invariant, not a bug). Defaults to the Simple profile, whose
    tool set is exactly the v1 §4.2 table.
    """
    profile = profile or resolve_active_profile()
    all_tools = {
        "web_search": WebSearchTool(),
        "read_file": ReadFileTool(),
        "read_clipboard": ReadClipboardTool(),
        "calculator": CalculatorTool(),
        "save_file": SaveFileTool(),
        "draft_message": DraftMessageTool(),
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
    main()
