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


class _MediumToolWithNonCallableUndo:
    """``undo`` exists but is not callable. It passed the presence-only check and
    registered at HIGH into the SAFE view, where the UndoManager would blow up at
    the moment somebody actually needed to reverse something — i.e. the failure
    surfaces only when the safety net is being used."""

    definition = ToolDefinition(
        id="string_undo_tool",
        label="String undo",
        description="A mutating tool whose undo is not callable.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={"type": "object", "properties": {}},
    )
    undo = "not even callable"

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="mutated something")


class _ReversibleTool:
    """A tool with a real undo, used for the round-trip below. Its effect is a
    single entry in ``self.written`` so the test can assert the state genuinely
    changed and was genuinely restored."""

    definition = ToolDefinition(
        id="reversible_tool",
        label="Reversible tool",
        description="Writes an entry, and can take it back.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.written: list[str] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.written.append(args["value"])
        return ToolResult(success=True, content="wrote it")

    def undo(self, snapshot) -> None:
        self.written.remove(snapshot)


def test_a_non_callable_undo_is_refused_like_a_missing_one():
    """Presence is not substance. Before this, `undo = "a string"` registered at
    HIGH and landed in the SAFE view."""
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_MediumToolWithNonCallableUndo())
    assert registry.visible_tools(PolicyMode.SAFE) == []


def test_a_real_undo_actually_reverses_the_effect():
    """The round trip the registration check can never make: execute, prove the
    state CHANGED, undo, prove it was restored. A hollow `def undo: pass` passes
    every static check there is and fails this."""
    tool = _ReversibleTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = ExecutionContext(conversation_id="c", policy_mode=PolicyMode.SAFE)

    tool.execute({"value": "entry"}, context)
    assert tool.written == ["entry"], "the tool did not actually do anything"

    tool.undo("entry")
    assert tool.written == [], "undo did not reverse the effect"
