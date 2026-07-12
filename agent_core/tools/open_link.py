"""Open a link in the user's browser — LOW risk, no state change in Addison (design-doc §7.4.1).

STATUS: stub. Delegates to the OS default browser via the Rust shell.
"""

from __future__ import annotations

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)


class OpenLinkTool:
    definition = ToolDefinition(
        id="open_link",
        label="Open a link in your browser",
        description="Opens a web link in your normal browser. It doesn't change anything on your computer.",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The https link to open."}},
            "required": ["url"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="Opening links needs the desktop shell; not available in this mode.",
            )
        # TODO(step 7): validate scheme is http(s), then shell_bridge.open_external(url).
        raise NotImplementedError("Wire to shell_bridge.open_external — spec §11 step 7.")
