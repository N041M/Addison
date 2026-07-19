"""The single most important test in the codebase (engineering-spec §9):
registering a MEDIUM/HIGH-risk tool without a real undo() must raise. This is
the mechanical enforcement of the entire safety model (design-doc §7.9)."""

import pytest

from agent_core.policy import PolicyMode
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


class _HighDevTool:
    """HIGH-risk, no undo() — legal ONLY as a dev_only registration (run_command's
    shape). SAFE-view invisible; OPEN-view visible."""

    definition = ToolDefinition(
        id="run_command",
        label="Run a command",
        description="A dev-only tool with no undo.",
        risk_tier=RiskTier.HIGH,
        parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="ran")


def test_medium_tool_without_undo_is_rejected():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_MediumToolWithoutUndo())


def test_high_tool_without_undo_still_rejected_when_not_dev_only():
    # The undo check is unchanged for a normal (non-dev_only) registration.
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_HighDevTool())


def test_dev_only_tool_may_skip_undo_but_is_hidden_from_safe_view():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(_HighDevTool(), dev_only=True)   # does NOT raise
    assert registry.is_dev_only("run_command")
    # SAFE view (and its alias list_for_model) NEVER contains the dev_only tool.
    safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    assert safe_ids == {"calculator"}
    assert {d.id for d in registry.list_for_model()} == {"calculator"}
    # OPEN view surfaces it alongside the safe tools.
    open_ids = {d.id for d in registry.visible_tools(PolicyMode.OPEN)}
    assert open_ids == {"calculator", "run_command"}
    # get() still returns the instance regardless of mode (used for execution).
    assert registry.get("run_command").definition.risk_tier is RiskTier.HIGH


def test_low_risk_tool_registers_fine():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    assert registry.get("calculator").definition.risk_tier is RiskTier.LOW


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CalculatorTool())
