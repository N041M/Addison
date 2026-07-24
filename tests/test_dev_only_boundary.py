"""SAFE-1 at DISPATCH: no dev-only tool executes outside OPEN mode, whoever wrote it.

``visible_tools(SAFE)`` hides dev-only tools from the MODEL, but hiding is not
enforcing: a ``tool_use`` naming a hidden id still reaches ``registry.get()``, and
the permission gate does not check dev-ness either. Before the guard these tests
pin, the boundary held ONLY because ``run_command`` refused inside its own
``execute`` — a per-tool convention that tool #2 would not inherit. Steps 5, 7
and 8 add more dev-only surface, so the guarantee has to be structural.

The rogue tool below is the point of the file: it is dev-only, HIGH, and has NO
self-check. Under the old code it EXECUTED under SAFE through both dispatch paths
(the orchestrator turn and the routine step). Each test here fails if the guard at
its dispatch site is removed.
"""

from __future__ import annotations

from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.providers.base import (
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import DEV_ONLY_REFUSAL, ToolRegistry


class _RogueDevTool:
    """A dev-only tool whose author forgot the SAFE self-check. Deliberately.

    ``run_command`` has one; nothing makes the next tool have one. If the boundary
    is real, this tool cannot run under SAFE even though it does nothing to stop
    itself."""

    definition = ToolDefinition(
        id="rogue_dev_tool",
        label="Do something dev-only",
        description="A dev-only tool with no self-check.",
        risk_tier=RiskTier.HIGH,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.ran: list[PolicyMode] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.ran.append(context.policy_mode)
        return ToolResult(success=True, content="ROGUE RAN")


class _CallsTheRogue:
    """One turn: ask for the rogue tool, then stop. Mirrors a model steered into
    naming a tool it was never offered."""

    def __init__(self) -> None:
        self._sent = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        self._sent += 1
        if self._sent == 1:
            return ModelResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="c1", tool_id="rogue_dev_tool", args={})],
            )
        return ModelResponse(text="done", tool_calls=[])


def _registry_with_rogue() -> tuple[ToolRegistry, _RogueDevTool]:
    registry = ToolRegistry()
    tool = _RogueDevTool()
    registry.register(tool, dev_only=True)
    return registry, tool


def _always_granting_gate() -> PermissionGate:
    """Grants everything, so a tool that still runs cannot be explained away as the
    gate having blocked it. The boundary must hold on its own."""
    return PermissionGate(on_request=lambda tool_id, detail=None: PermissionStatus.GRANTED)


def _engine(tmp_path, registry) -> RoutineEngine:
    store = Store(tmp_path / "devonly.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    return RoutineEngine(
        registry,
        _always_granting_gate(),
        UndoManager(store=store, tool_registry=registry),
        store=store,
    )


def _rogue_routine() -> Routine:
    return Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "rogue_dev_tool", {})],
    )


# --- the orchestrator turn path ---------------------------------------------


def test_a_dev_only_tool_never_executes_under_safe_via_the_orchestrator(tmp_path):
    from agent_core.orchestrator import Conversation, Message, Orchestrator

    registry, tool = _registry_with_rogue()
    router = ModelRouter(configured={ModelRole.PRIMARY: _CallsTheRogue()})
    orchestrator = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=_always_granting_gate(),
        undo_manager=UndoManager(
            store=Store(tmp_path / "o.sqlite3"), tool_registry=registry
        ),
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="go"))

    orchestrator.run_turn(conversation, requested_role=ModelRole.PRIMARY, mode=PolicyMode.SAFE)

    assert tool.ran == [], "a dev-only tool executed under SAFE mode"


def test_the_same_tool_does_run_under_open_via_the_orchestrator(tmp_path):
    """The guard must be exactly 'dev-only implies OPEN-only'. If this fails the
    harness is broken, which is a worse outcome than the hole being open."""
    from agent_core.orchestrator import Conversation, Message, Orchestrator

    registry, tool = _registry_with_rogue()
    router = ModelRouter(configured={ModelRole.PRIMARY: _CallsTheRogue()})
    orchestrator = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=_always_granting_gate(),
        undo_manager=UndoManager(
            store=Store(tmp_path / "o.sqlite3"), tool_registry=registry
        ),
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="go"))

    orchestrator.run_turn(conversation, requested_role=ModelRole.PRIMARY, mode=PolicyMode.OPEN)

    assert tool.ran == [PolicyMode.OPEN]


# --- the routine step path ---------------------------------------------------


def test_a_dev_only_step_never_executes_under_safe_via_a_routine(tmp_path):
    registry, tool = _registry_with_rogue()
    engine = _engine(tmp_path, registry)

    result = engine.run(_rogue_routine(), {}, mode=PolicyMode.SAFE)

    assert tool.ran == [], "a dev-only tool executed under SAFE mode via a routine step"
    assert DEV_ONLY_REFUSAL in str(result.detail)


def test_the_same_step_does_run_under_open_via_a_routine(tmp_path):
    registry, tool = _registry_with_rogue()
    engine = _engine(tmp_path, registry)

    engine.run(_rogue_routine(), {}, mode=PolicyMode.OPEN)

    assert tool.ran == [PolicyMode.OPEN]


# --- the rule itself ---------------------------------------------------------


def test_the_guard_is_scoped_to_dev_only_tools_and_says_so_plainly():
    """An ordinary tool is untouched in both modes, and the refusal never leaks a
    mechanism to the person reading it."""
    registry, _ = _registry_with_rogue()

    assert registry.refuse_if_dev_only_outside_open("rogue_dev_tool", PolicyMode.SAFE) is not None
    assert registry.refuse_if_dev_only_outside_open("rogue_dev_tool", PolicyMode.OPEN) is None
    # Not registered dev_only -> never refused, in either mode.
    assert registry.refuse_if_dev_only_outside_open("web_search", PolicyMode.SAFE) is None
    assert registry.refuse_if_dev_only_outside_open("web_search", PolicyMode.OPEN) is None

    for word in ("dev_only", "registry", "dispatch", "OPEN", "policy"):
        assert word not in DEV_ONLY_REFUSAL
