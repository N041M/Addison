"""Read clipboard content — LOW risk, only when the user explicitly pastes (design-doc §7.4.1).

Clipboard access is mediated by the Rust shell; the Agent Core never reads the
system clipboard directly (engineering-spec §1.3). Read-only, so no undo and no
snapshot.
"""

from __future__ import annotations

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)


class ReadClipboardTool:
    definition = ToolDefinition(
        id="read_clipboard",
        label="Read what you paste in",
        description="Lets you paste an email or message in without saving a file first.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}, "required": []},
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="Clipboard access needs the desktop shell; not available in this mode.",
            )
        return ToolResult(success=True, content=context.shell_bridge.read_clipboard())
