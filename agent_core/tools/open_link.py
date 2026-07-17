"""Open a link in the user's browser — LOW risk, no state change in Addison (design-doc §7.4.1).

Delegates to the OS default browser via the Rust shell (engineering-spec §1.3).
The scheme is validated to http/https BEFORE the bridge is ever touched: a model
(possibly steered by injected web content, design-doc §9) must not be able to hand
the OS a ``file://``, ``javascript:``, or other non-web URL to open. Read-only, so
no undo and no snapshot.
"""

from __future__ import annotations

from urllib.parse import urlparse

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

_ALLOWED_SCHEMES = ("http", "https")


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
        url = str(args.get("url", ""))
        # Validate the scheme first — never hand the OS anything but a web link,
        # and do it before touching the bridge.
        scheme = urlparse(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            return ToolResult(
                success=False,
                content="I can only open web links that start with http:// or https://.",
            )
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="Opening links needs the desktop shell; not available in this mode.",
            )
        context.shell_bridge.open_external(url)
        return ToolResult(success=True, content=f"Opened {url} in your browser.")
