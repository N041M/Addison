"""The single most important test in the codebase (engineering-spec §9):
registering a MEDIUM/HIGH-risk tool without a real undo() must raise. This is
the mechanical enforcement of the entire safety model (design-doc §7.9)."""

import pytest

from agent_core.memory.store import Store
from agent_core.policy import PolicyMode
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
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
    changed and was genuinely restored.

    It returns a real ``ActionSnapshot`` because the round trip is driven through
    the production ``UndoManager``, not by the test calling ``undo`` itself: the
    payload is what the manager hands back, so a tool that recorded the wrong
    thing fails there rather than passing on a value the test supplied."""

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
        return ToolResult(
            success=True,
            content="wrote it",
            snapshot=ActionSnapshot(
                id="snap-1",
                tool_call_id="call-1",
                tool_id=self.definition.id,
                undo_payload={"value": args["value"]},
                created_at=1,
            ),
        )

    def undo(self, snapshot: ActionSnapshot) -> None:
        self.written.remove(snapshot.undo_payload["value"])


def test_a_non_callable_undo_is_refused_like_a_missing_one():
    """Presence is not substance. Before this, `undo = "a string"` registered at
    HIGH and landed in the SAFE view."""
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_MediumToolWithNonCallableUndo())
    assert registry.visible_tools(PolicyMode.SAFE) == []


def test_a_real_undo_actually_reverses_the_effect(tmp_path):
    """The round trip the registration check can never make: execute, prove the
    state CHANGED, undo, prove it was restored. A hollow `def undo: pass` passes
    every static check there is and fails this.

    Driven through the PRODUCTION replay path — a real ``Store``, and
    ``UndoManager.undo_last`` resolving the tool out of this very registry by the
    snapshot's tool_id — because the interesting claim is not that the fixture's
    own ``undo`` works. It is that what registration promised is what the machinery
    a person's "undo that" reaches actually gets to call. Calling ``tool.undo``
    from the test would prove the fixture and nothing about the tree.

    Distinct from tests/test_undo_manager.py, which pins the manager's ORDERING,
    marking and failure isolation with tools whose undo only appends to a log:
    what is checked here is that a registered tool's undo genuinely reverses a
    real effect when the manager invokes it."""
    tool = _ReversibleTool()
    registry = ToolRegistry()
    registry.register(tool)
    store = Store(tmp_path / "undo-round-trip.db")
    try:
        manager = UndoManager(store=store, tool_registry=registry)
        context = ExecutionContext(conversation_id="c", policy_mode=PolicyMode.SAFE)

        result = tool.execute({"value": "entry"}, context)
        assert tool.written == ["entry"], "the tool did not actually do anything"
        assert result.snapshot is not None, "a mutating tool must record what to undo"
        manager.record(result.snapshot)

        # Nothing below names the tool: the manager looks it up in the registry
        # from the stored snapshot, which is the only route the live undo has.
        [reverted] = manager.undo_last(n=1)
        assert reverted.success, reverted.detail
        assert reverted.tool_id == "reversible_tool"
        assert tool.written == [], "undo did not reverse the effect"
        # The production bookkeeping ran too: the snapshot is marked reverted, so a
        # second pass cannot undo an action that has already been taken back.
        assert store.recent_unreverted_snapshots(limit=10) == []
    finally:
        store.close()


def test_the_remote_floor_is_a_subset_of_the_safe_view():
    """THE SENTENCE THE REMOTE FLOOR IS MADE OF, asserted beside the registry's own
    tests because that is where it belongs (messaging channels, plan §3.6 and §4):
    *a turn that arrived from a phone is never offered a tool Simple could not be
    offered.*

    `REMOTE_TOOL_IDS` is a closed, hard-coded list and `remote_tools(mode)` is an
    INTERSECTION with `visible_tools(mode)` — so the property is structural, and it
    holds for whatever the list is edited to contain. Asked here of the registry the
    app actually builds, in the profile whose view is the narrowest, so that adding a
    dev-only id to the list fails at the registry's own boundary rather than only in
    the channel suite.

    `tests/test_channel_remote_floor.py` holds this and the other three properties of
    §3.6; `tests/doc_claims.py` holds the documents to it.

    Mutation: add `run_command` (registered dev_only, absent from the SAFE view) to
    REMOTE_TOOL_IDS — this fails, naming it."""
    from agent_core.main import build_registry
    from agent_core.profiles import SIMPLE
    from agent_core.tools.registry import REMOTE_TOOL_IDS

    registry = build_registry(profile=SIMPLE)
    safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    assert REMOTE_TOOL_IDS <= safe_ids, sorted(REMOTE_TOOL_IDS - safe_ids)
    # And the view over it agrees with the set, in both modes — the intersection is
    # what makes the assertion above about the VIEW and not only about the list.
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        assert {d.id for d in registry.remote_tools(mode)} <= safe_ids
