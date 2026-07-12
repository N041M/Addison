"""The single most important test in the codebase (engineering-spec §9):
registering a MEDIUM/HIGH-risk tool without a real undo() must raise. This is
the mechanical enforcement of the entire safety model (design-doc §7.9)."""

import pytest

from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.calculator import CalculatorTool
from agent_core.tools.registry import ToolRegistry


class _MediumToolWithoutUndo:
    definition = ToolDefinition(
        id="bad_tool",
        label="Bad tool",
        description="A mutating tool that forgot to implement undo().",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="mutated something")


def test_medium_tool_without_undo_is_rejected():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_MediumToolWithoutUndo())


def test_low_risk_tool_registers_fine():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    assert registry.get("calculator").definition.risk_tier is RiskTier.LOW


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CalculatorTool())
