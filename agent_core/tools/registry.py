"""Tool registry with registration-time undo enforcement.

Engineering-spec §4.2 and §9 (test #1). Registering a MEDIUM/HIGH-risk tool
without a genuine ``undo()`` MUST raise — this is the single most important
invariant in the codebase, so do NOT satisfy it with a no-op ``undo``.
"""

from __future__ import annotations

from agent_core.tools.base import RiskTier, Tool, ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.definition.risk_tier != RiskTier.LOW:
            # A tool whose undo() is still the Protocol default (unimplemented)
            # or missing entirely is mechanically capped at read-only.
            own_undo = getattr(type(tool), "undo", None)
            if own_undo is None or getattr(own_undo, "__isabstractmethod__", False):
                raise ValueError(
                    f"Tool '{tool.definition.id}' has risk_tier="
                    f"{tool.definition.risk_tier.value} but no undo() implementation. "
                    "Either implement undo() or set risk_tier=LOW."
                )
        if tool.definition.id in self._tools:
            raise ValueError(f"Tool '{tool.definition.id}' is already registered.")
        self._tools[tool.definition.id] = tool

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"No tool registered with id '{tool_id}'.") from None

    def list_for_model(self) -> list[ToolDefinition]:
        """The tool definitions sent to the LLM as its available tools."""
        return [t.definition for t in self._tools.values()]
