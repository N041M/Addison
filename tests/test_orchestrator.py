"""Orchestration loop — engineering-spec §4.4, §9.

Exercised with a scripted fake ``ModelProvider`` (house style: see
``test_model_router.py``) that returns fixed tool-call / text sequences, plus spy
tools and a fake snapshot store. The permission gate is consulted before every
execution, and — the regression these tests lock in — the assistant's tool-call
turn is recorded in history BEFORE its tool results so native tool-calling
providers can replay a valid transcript (§4.4).
"""

import pytest

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.policy import TurnSurface
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import UNKNOWN_TOOL_REFUSAL, ToolRegistry


# --- fakes -----------------------------------------------------------------


class _ScriptedProvider:
    """Replays a fixed list of ModelResponses, one per ``send()``.

    Records the message history it is handed each turn so tests can assert what
    the model would replay on the following round (native tool-calling needs each
    tool_result preceded by the assistant tool_use it answers)."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.histories: list[list[Message]] = []
        # What the provider was OFFERED, per send. Recorded since the turn gained a
        # SURFACE (messaging channels phase 2): "which tools did the model actually
        # see" is the only honest way to assert a tool view, and asking the registry
        # instead would be asking the thing under test.
        self.offered: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.histories.append(list(messages))
        self.offered.append(list(tools))
        return self._responses.pop(0)


def _tool_def(tool_id: str, risk: RiskTier = RiskTier.LOW) -> ToolDefinition:
    return ToolDefinition(
        id=tool_id,
        label=f"Use {tool_id}",
        description=f"A test tool named {tool_id}.",
        risk_tier=risk,
        parameters_schema={"type": "object", "properties": {}},
    )


class _SpyTool:
    """LOW-risk tool that records every ``execute()`` call so a test can assert
    it did (or, on denial, did NOT) run."""

    def __init__(self, tool_id: str = "calculator", content=None):
        self.definition = _tool_def(tool_id)
        self.calls: list[dict] = []
        self._content = "ok" if content is None else content

    def execute(self, args, context) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, content=self._content)


class _SnapshotTool:
    """MEDIUM tool returning a snapshot whose ``tool_call_id`` is unset — the
    orchestrator must stamp it with the call id before recording (§4.4). Its
    real ``undo()`` is what lets it register at MEDIUM at all."""

    def __init__(self, tool_id: str = "save_file"):
        self.definition = _tool_def(tool_id, risk=RiskTier.MEDIUM)
        self.calls: list[dict] = []

    def execute(self, args, context) -> ToolResult:
        self.calls.append(args)
        snapshot = ActionSnapshot(
            id="snap-1",
            tool_call_id="",  # unset on purpose; the orchestrator fills it in
            tool_id=self.definition.id,
            undo_payload={},
            created_at=0,
        )
        return ToolResult(success=True, content="saved", snapshot=snapshot)

    def undo(self, snapshot) -> None:  # real (non-abstract) undo → registers at MEDIUM
        ...


class _FakeStore:
    """Captures whatever ``UndoManager.record()`` persists."""

    def __init__(self):
        self.inserted: list[ActionSnapshot] = []

    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        self.inserted.append(snapshot)


# --- helpers ---------------------------------------------------------------


def _tool_call(call_id: str, tool_id: str, args: dict | None = None) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, tool_id=tool_id, args=args or {})


def _calls_response(*calls: ToolCallRequest) -> ModelResponse:
    return ModelResponse(text=None, tool_calls=list(calls))


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[])


def _make_orchestrator(provider, registry, gate, store=None):
    store = _FakeStore() if store is None else store
    router = ModelRouter(configured={ModelRole.PRIMARY: provider})
    orchestrator = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=store, tool_registry=registry),
    )
    return orchestrator, store


def _conversation_with(user_text: str) -> Conversation:
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content=user_text))
    return conv


# --- tests -----------------------------------------------------------------


def test_the_two_new_turn_parameters_change_nothing_when_they_are_not_passed():
    """THE FREEZE (messaging channels phase 2; the idiom
    docs/model-assignments-plan.md §2.2 uses and the routing chain's head got).

    ``run_turn`` gained ``surface`` and ``stream_to``. With ``surface=DESK`` and
    ``stream_to`` unset the path must be byte-identical to what it was before those
    parameters existed — because every caller in the app except one is that call,
    and a channel feature that changes the desktop's behaviour by a single delta is
    a channel feature that has already failed.

    "Byte-identical" is pinned on the four things a turn is made of: the tool list
    the provider was OFFERED, the history it was REPLAYED, the messages the
    conversation ended with, and the stream the frontend SAW. Passing the new
    parameters explicitly at their defaults must produce all four unchanged.

    Mutation: make ``sink`` default to None rather than to the orchestrator's own
    stream — the streamed text disappears and this fails on the last comparison."""

    def one_turn(**kwargs):
        tool = _SpyTool("calculator", content=42)
        registry = ToolRegistry()
        registry.register(tool)
        provider = _ScriptedProvider([
            _calls_response(_tool_call("call-1", "calculator", {"expression": "6*7"})),
            _text_response("The answer is 42."),
        ])
        gate = PermissionGate()
        gate.grant("calculator")
        router = ModelRouter(configured={ModelRole.PRIMARY: provider})
        streamed: list[str] = []
        orchestrator = Orchestrator(
            model_router=router,
            tool_registry=registry,
            permission_gate=gate,
            undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
            stream_to_frontend=streamed.append,
        )
        conv = _conversation_with("6*7")
        orchestrator.run_turn(conv, **kwargs)
        return {
            "offered": [[d.id for d in tools] for tools in provider.offered],
            "history": [
                [(m.role, str(m.content)) for m in replayed] for replayed in provider.histories
            ],
            "messages": [(m.role, str(m.content)) for m in conv.messages],
            "streamed": streamed,
            "calls": tool.calls,
        }

    before = one_turn()
    explicit = one_turn(surface=TurnSurface.DESK)
    assert explicit == before
    # And the remote surface is the ONE thing that differs, so the comparison above
    # is measuring something: no tools offered, and nothing streamed anywhere.
    remote = one_turn(surface=TurnSurface.REMOTE, stream_to=None)
    assert remote != before
    assert remote["offered"] == [[], []]
    assert remote["streamed"] == []
    assert remote["calls"] == [], "a remote turn may not reach a tool at all"
    assert before["streamed"] and before["calls"] == [{"expression": "6*7"}]


def test_assistant_tool_call_turn_recorded_before_results():
    # The regression Task 1 fixes: without the fix the assistant tool_use turn is
    # never appended, so history is [user, tool, assistant] and the tool_result is
    # left unpaired. With it, the assistant turn sits BEFORE its result.
    tool = _SpyTool("calculator")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-1", "calculator", {"expression": "2+2"})),
        _text_response("The answer is 4."),
    ])
    gate = PermissionGate()
    gate.grant("calculator")
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("what is 2+2")
    orchestrator.run_turn(conv)

    assert [m.role for m in conv.messages] == ["user", "assistant", "tool", "assistant"]
    assistant_tc, tool_result = conv.messages[1], conv.messages[2]
    assert assistant_tc.tool_calls[0].id == "call-1"          # tool_use carried in history
    assert tool_result.tool_call_id == "call-1"               # paired result follows it
    # The model actually saw that pairing on the second turn it was sent.
    replayed = provider.histories[1]
    assert [m.role for m in replayed] == ["user", "assistant", "tool"]
    assert replayed[1].tool_calls[0].id == "call-1"
    assert replayed[2].tool_call_id == "call-1"


def test_granted_permission_executes_and_appends_result():
    tool = _SpyTool("calculator", content=42)
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-9", "calculator", {"expression": "6*7"})),
        _text_response("done"),
    ])
    gate = PermissionGate(on_request=lambda tid: pytest.fail("must not ask; already granted"))
    gate.grant("calculator")
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("6*7")
    orchestrator.run_turn(conv)

    assert tool.calls == [{"expression": "6*7"}]
    tool_result = next(m for m in conv.messages if m.role == "tool")
    assert tool_result.tool_call_id == "call-9"
    assert tool_result.content == "42"  # append_tool_result stringifies content


def test_a_tool_use_naming_an_id_nothing_is_registered_under_is_answered_not_raised():
    # A model can name a tool that existed when it last saw the list: every `mcp:`
    # id leaves the registry on a refresh, a removal, a failed check or a snapshot
    # restore, and the transcript it was used in survives all four.
    #
    # The turn must SURVIVE it. A tool_use with no tool_result is the one shape the
    # provider rejects on every later request of the session (§4.4), which is why
    # every other failure on this path is a refused step — an unregistered id is
    # now one too. Mutation: change `tool_registry.find` back to `get` in
    # `_run_tool_calls` — this fails with a KeyError before the second send.
    tool = _SpyTool("calculator")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-7", "mcp:Design docs:search", {"q": "roadmap"})),
        _text_response("I couldn't look that up."),
    ])
    gate = PermissionGate(on_request=lambda tid: pytest.fail("nothing to ask about"))
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("search the docs")

    orchestrator.run_turn(conv)

    assert [m.role for m in conv.messages] == ["user", "assistant", "tool", "assistant"]
    tool_result = next(m for m in conv.messages if m.role == "tool")
    assert tool_result.tool_call_id == "call-7"          # the pairing holds
    assert tool_result.content == UNKNOWN_TOOL_REFUSAL
    assert "mcp:" not in tool_result.content             # plain language, not an id
    assert tool.calls == []                              # and nothing else ran instead
    # The model saw the pairing on the round it was sent, which is the half that
    # keeps the rest of the conversation usable.
    replayed = provider.histories[1]
    assert replayed[1].tool_calls[0].id == "call-7"
    assert replayed[2].tool_call_id == "call-7"


def test_denied_permission_skips_execution():
    tool = _SpyTool("calculator")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-3", "calculator")),
        _text_response("ok, skipped"),
    ])
    asked: list[str] = []
    gate = PermissionGate(on_request=lambda tid: (asked.append(tid), PermissionStatus.DENIED)[1])
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("do the thing")
    orchestrator.run_turn(conv)

    assert asked == ["calculator"]
    assert tool.calls == []  # execute() never reached
    tool_result = next(m for m in conv.messages if m.role == "tool")
    assert tool_result.content.startswith("User declined this step.")
    # The denial nudges the model to deliver what it already found in chat.
    assert "give it directly in your reply" in tool_result.content


def test_denial_lasts_only_its_own_turn_then_reasks():
    # 2026-07 manual pass: one "Not now" used to become a silent permanent
    # denial. It must last one turn only — the next user message asks again.
    tool = _SpyTool("calculator", content=7)
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-1", "calculator")),
        _text_response("skipped it"),
        _calls_response(_tool_call("call-2", "calculator", {"expression": "3+4"})),
        _text_response("7"),
    ])
    answers = [PermissionStatus.DENIED, PermissionStatus.GRANTED]
    asked: list[str] = []
    gate = PermissionGate(on_request=lambda tid: (asked.append(tid), answers.pop(0))[1])
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("do the thing")
    orchestrator.run_turn(conv)          # turn 1: user says Not now
    assert tool.calls == []

    conv.messages.append(Message(role="user", content="please try again"))
    orchestrator.run_turn(conv)          # turn 2: the card must come back

    assert asked == ["calculator", "calculator"]   # re-asked, not silently denied
    assert tool.calls == [{"expression": "3+4"}]   # and ran once granted


def test_denial_sticks_for_the_rest_of_its_turn():
    # Within ONE turn a model retry of the denied tool must not re-prompt —
    # that would nag the user who just said Not now.
    tool = _SpyTool("calculator")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-1", "calculator")),
        _calls_response(_tool_call("call-2", "calculator")),   # model insists
        _text_response("fine, no calculator"),
    ])
    asked: list[str] = []
    gate = PermissionGate(on_request=lambda tid: (asked.append(tid), PermissionStatus.DENIED)[1])
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("do the thing")
    orchestrator.run_turn(conv)

    assert asked == ["calculator"]   # asked once, denied once, never nagged
    assert tool.calls == []


def test_not_yet_asked_requests_then_executes_on_grant():
    tool = _SpyTool("calculator")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-4", "calculator", {"expression": "1+1"})),
        _text_response("2"),
    ])
    asked: list[str] = []
    gate = PermissionGate(on_request=lambda tid: (asked.append(tid), PermissionStatus.GRANTED)[1])
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("1+1")
    orchestrator.run_turn(conv)

    assert asked == ["calculator"]                 # the gate's request handler fired
    assert tool.calls == [{"expression": "1+1"}]   # and execution proceeded on grant


def test_snapshot_is_stamped_and_recorded():
    tool = _SnapshotTool("save_file")
    registry = ToolRegistry()
    registry.register(tool)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("call-5", "save_file", {"filename": "n.txt"})),
        _text_response("saved it"),
    ])
    gate = PermissionGate()
    gate.grant("save_file")
    orchestrator, store = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("save this")
    orchestrator.run_turn(conv)

    assert len(store.inserted) == 1
    assert store.inserted[0].tool_call_id == "call-5"  # stamped from the call id
    assert store.inserted[0].id == "snap-1"


def test_multi_round_two_tool_rounds_then_text():
    tool_a = _SpyTool("calculator")
    tool_b = _SnapshotTool("save_file")
    registry = ToolRegistry()
    registry.register(tool_a)
    registry.register(tool_b)
    provider = _ScriptedProvider([
        _calls_response(_tool_call("a1", "calculator", {"expression": "2+2"})),
        _calls_response(_tool_call("b1", "save_file", {"filename": "r.txt"})),
        _text_response("All done."),
    ])
    gate = PermissionGate()
    gate.grant("calculator")
    gate.grant("save_file")
    orchestrator, _ = _make_orchestrator(provider, registry, gate)

    conv = _conversation_with("compute then save")
    orchestrator.run_turn(conv)

    assert [m.role for m in conv.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    # Each tool_use is paired with the tool_result that follows it, in order.
    assert conv.messages[1].tool_calls[0].id == "a1"
    assert conv.messages[2].tool_call_id == "a1"
    assert conv.messages[3].tool_calls[0].id == "b1"
    assert conv.messages[4].tool_call_id == "b1"
    assert conv.messages[5].content == "All done."
    assert tool_a.calls and tool_b.calls           # both tools ran exactly once
    assert len(provider.histories) == 3            # loop terminated after three turns
