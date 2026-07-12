"""Web search — LOW risk, read-only (design-doc §7.4.1).

Core capability: the single most common reason a non-technical user opens the app.

PROMPT-INJECTION NOTE (design-doc §9): results returned here are UNTRUSTED data.
They must be marked as such when appended to the model context so the model does
not treat instructions found inside a page as user commands.

STATUS: stub. Pick a search backend and wire it (engineering-spec §11 step 5).
"""

from __future__ import annotations

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)


class WebSearchTool:
    definition = ToolDefinition(
        id="web_search",
        label="Search the web",
        description="Looks things up online. It only reads pages — it never changes anything.",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to search for."}},
            "required": ["query"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        # TODO(step 5): call the chosen search API; wrap results as untrusted content.
        raise NotImplementedError("Wire to the chosen web-search backend — spec §11 step 5.")
