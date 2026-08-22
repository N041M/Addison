"""The remote floor — what a turn that arrived from a phone may use (PHASE 2).

[docs/messaging-channel-plan.md](../docs/messaging-channel-plan.md) §3.6 owns the
design. In this phase the floor is **EMPTY**, and that is the claim:

    A paired phone can hold a conversation with Addison, and Addison can use no
    tools at all while doing it.

An empty set is not a placeholder. It is the strongest version of the phase, and it
lets every other seam (the thread, the hand-off, the conversation isolation, the
screening, the splitting, the pairing) be proven with the tool question factored
out. Phase 3 fills the set with the three ids §3.6 argues for; the properties below
are written so that they keep holding when it does — every one of them is about the
SHAPE of the floor rather than about its being empty, except the two that say
"empty" out loud and are named accordingly.

TWO LAYERS, and the second is the one that enforces:

  * ``remote_tools(mode)`` decides what the model is OFFERED — an INTERSECTION with
    ``visible_tools(mode)``, so a subset of the desk's view in every mode;
  * ``refuse_if_not_remote(id, surface)`` refuses a ``tool_use`` naming anything
    else, at BOTH dispatch paths, before the gate and before any effect.

The artifact-disabling lesson is explicit that a marker is never the enforcement and
that dispatch wins if the two disagree — so both are tested, separately, and the
dispatch one is tested through a real turn rather than by reading the source.
"""

from __future__ import annotations

import pytest

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, TurnSurface
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ActionSnapshot, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import REMOTE_REFUSAL, REMOTE_TOOL_IDS, ToolRegistry


class _Provider:
    """Records the tool definitions it was offered, and replays canned responses."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.offered: list[list[ToolDefinition]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.offered.append(list(tools))
        return self._responses.pop(0)


class _Tool:
    def __init__(self, tool_id: str, tier: RiskTier = RiskTier.LOW) -> None:
        self.definition = ToolDefinition(
            id=tool_id,
            label=f"Do {tool_id}",
            description="A test tool.",
            risk_tier=tier,
            parameters_schema={"type": "object", "properties": {}},
        )
        self.calls: list[dict] = []

    def execute(self, args, context) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, content="ran")

    def undo(self, snapshot) -> None:  # pragma: no cover - never reached here
        return None


def _registry() -> tuple[ToolRegistry, _Tool, _Tool]:
    registry = ToolRegistry()
    ordinary = _Tool("look_something_up")
    dev = _Tool("run_a_command")
    registry.register(ordinary)
    registry.register(dev, dev_only=True)
    return registry, ordinary, dev


class _FakeStore:
    """Captures whatever ``UndoManager.record()`` persists (test_orchestrator's)."""

    def __init__(self) -> None:
        self.inserted: list[ActionSnapshot] = []

    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        self.inserted.append(snapshot)


def _orchestrator(registry: ToolRegistry, provider: _Provider, audit: list[dict]) -> Orchestrator:
    return Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *_: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),  # type: ignore[arg-type]
        on_tool_audit=audit.append,
    )


# ---------------------------------------------------------------------------
# The set, and the view over it
# ---------------------------------------------------------------------------


def test_the_floor_is_empty_in_this_phase():
    """PHASE 2'S WHOLE CLAIM, as one assertion. Phase 3 is the commit that changes
    this line, and it changes it together with the four properties §3.6 asks of the
    ids it adds — which is exactly why the line is worth having on its own.

    Mutation: add an id to REMOTE_TOOL_IDS — this fails, which is the point: filling
    the set is a deliberate act with a test to update, not a quiet edit."""
    assert REMOTE_TOOL_IDS == frozenset()


def test_a_remote_turn_is_offered_nothing_in_either_mode():
    """The view, asked directly. Empty in SAFE and empty in OPEN — a phone gets the
    same nothing whichever profile the desk is in."""
    registry, _, _ = _registry()
    assert registry.remote_tools(PolicyMode.SAFE) == []
    assert registry.remote_tools(PolicyMode.OPEN) == []
    # And the desk is untouched: this is a second VIEW, not a change to the first.
    assert [d.id for d in registry.visible_tools(PolicyMode.SAFE)] == ["look_something_up"]
    assert len(registry.visible_tools(PolicyMode.OPEN)) == 2


def test_the_remote_view_is_a_subset_of_the_safe_view_in_every_mode():
    """THE SENTENCE THE FLOOR IS REALLY MADE OF: *a remote turn is never offered a
    tool Simple could not be offered.* Trivially true while the set is empty, and
    written now so that phase 3 inherits the assertion rather than having to think
    of it."""
    registry, _, _ = _registry()
    safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        assert {d.id for d in registry.remote_tools(mode)} <= safe_ids


def test_the_view_is_an_intersection_and_never_a_union(monkeypatch):
    """The property that makes the set safe to EDIT later: putting an id in
    ``REMOTE_TOOL_IDS`` can only ever take something out of the desk's view and put
    it in a smaller one — it can never ADD a tool to a surface.

    Proven by naming a dev-only tool in the set and watching it stay invisible in
    SAFE, where the mode already hides it. Mutation: implement ``remote_tools`` as
    ``[self._tools[i].definition for i in REMOTE_TOOL_IDS]`` — a union — and the SAFE
    assertion fails, which is the whole hole this shape closes."""
    registry, _, _ = _registry()
    monkeypatch.setattr(
        "agent_core.tools.registry.REMOTE_TOOL_IDS", frozenset({"run_a_command"})
    )
    assert registry.remote_tools(PolicyMode.SAFE) == []
    assert [d.id for d in registry.remote_tools(PolicyMode.OPEN)] == ["run_a_command"]


def test_the_view_is_a_filter_over_the_one_registry_and_not_a_second_one(monkeypatch):
    """SAFE invariant 3's shape, applied to a second SURFACE instead of a second
    caller. A tool registered after the fact appears through the remote view without
    anything being re-registered, which is only true of a filtered view."""
    registry, _, _ = _registry()
    monkeypatch.setattr(
        "agent_core.tools.registry.REMOTE_TOOL_IDS", frozenset({"arrived_later"})
    )
    assert registry.remote_tools(PolicyMode.OPEN) == []
    registry.register(_Tool("arrived_later"))
    assert [d.id for d in registry.remote_tools(PolicyMode.OPEN)] == ["arrived_later"]


# ---------------------------------------------------------------------------
# The refusal, at dispatch
# ---------------------------------------------------------------------------


def test_every_registered_tool_is_refused_on_a_remote_turn_and_none_at_the_desk():
    """A table test over the registry, so a tool registered tomorrow is refused by
    default and its admission is a deliberate edit. The desk direction matters just
    as much: this check must be invisible to every path that is not a phone."""
    registry, _, _ = _registry()
    for definition in registry.visible_tools(PolicyMode.OPEN):
        assert registry.refuse_if_not_remote(definition.id, TurnSurface.REMOTE) == REMOTE_REFUSAL
        assert registry.refuse_if_not_remote(definition.id, TurnSurface.DESK) is None
    # An id nothing is registered under is refused too: the floor is a list of what
    # MAY run, so anything not on it is refused whether it exists or not.
    assert registry.refuse_if_not_remote("invented_by_the_model", TurnSurface.REMOTE)


def test_the_refusal_says_what_to_do_instead_and_promises_nothing_it_cannot_keep():
    """A refusal with no next move comes back as a blocked task (LIVE_ONLY_REFUSAL's
    own rule), so the sentence has to name one. And it must not promise the DESK
    QUEUE, which is phase 3: a sentence saying a request is "waiting on your screen"
    while nothing was written down would be this app telling a plain lie.

    Mutation: paste §3.6's full quoted sentence in before the queue exists — the
    second half of this test fails."""
    assert "at your computer" in REMOTE_REFUSAL
    assert "back at your screen" in REMOTE_REFUSAL
    for promise in ("saved", "waiting", "request"):
        assert promise not in REMOTE_REFUSAL, (
            "the refusal promises a desk queue that phase 2 does not ship"
        )
    for jargon in ("remote", "floor", "surface", "dispatch", "tier"):
        assert jargon not in REMOTE_REFUSAL.lower()


@pytest.mark.parametrize("tool_id", ["look_something_up", "run_a_command"])
def test_a_remote_turn_naming_a_tool_is_refused_before_the_gate_and_leaves_an_audit_row(tool_id):
    """THE ENFORCEMENT, through a real turn rather than by reading the source.

    The model names a tool it was never offered — which is the only way it can name
    one here, since it was offered nothing — and the call is refused BEFORE the gate
    and before ``execute``. Two things are asserted about that: the tool did not run,
    and a row exists saying so. The gate would have GRANTED (it is wired to say yes),
    so a refusal reaching it would show up as a run.

    Mutation: delete the ``refuse_if_not_remote`` branch from ``_run_tool_calls`` —
    the tool runs, and both assertions fail."""
    registry, ordinary, dev = _registry()
    tool = ordinary if tool_id == "look_something_up" else dev
    audit: list[dict] = []
    provider = _Provider(
        [
            ModelResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call-1", tool_id=tool_id, args={})],
            ),
            ModelResponse(text="I can't do that from here.", tool_calls=[]),
        ]
    )
    orchestrator = _orchestrator(registry, provider, audit)
    conversation = Conversation(id="remote-1")
    conversation.messages.append(Message(role="user", content="please do it"))
    orchestrator.run_turn(
        conversation,
        mode=PolicyMode.OPEN,
        surface=TurnSurface.REMOTE,
        stream_to=None,
    )
    assert tool.calls == [], "a refused call must never reach execute"
    results = [m for m in conversation.messages if m.role == "tool"]
    assert [m.content for m in results] == [REMOTE_REFUSAL]
    assert [(r["tool_id"], r["outcome"]) for r in audit] == [(tool_id, "not_callable")]
    # And the model was offered nothing at all, on both rounds.
    assert provider.offered == [[], []]


def test_the_same_call_runs_normally_at_the_desk():
    """The other half of the same fact, and the one that says this feature is
    invisible to everything else: identical registry, identical gate, identical
    conversation shape — only the surface differs, and at the desk the tool runs."""
    registry, ordinary, _ = _registry()
    audit: list[dict] = []
    provider = _Provider(
        [
            ModelResponse(
                text=None,
                tool_calls=[
                    ToolCallRequest(id="call-1", tool_id="look_something_up", args={})
                ],
            ),
            ModelResponse(text="Here you go.", tool_calls=[]),
        ]
    )
    orchestrator = _orchestrator(registry, provider, audit)
    conversation = Conversation(id="desk-1")
    conversation.messages.append(Message(role="user", content="please do it"))
    orchestrator.run_turn(conversation, mode=PolicyMode.SAFE)
    assert ordinary.calls == [{}]
    assert [r["outcome"] for r in audit] == ["granted"]
    assert [d.id for d in provider.offered[0]] == ["look_something_up"]


def test_the_routine_engines_dispatch_path_refuses_the_same_way():
    """BOTH DISPATCH PATHS, because a boundary only one path enforces is not a
    boundary. Nothing can start a routine from a phone in this phase — the floor is
    empty, so no tool can be named at all — and the check is wired anyway, so the
    phase that admits a tool does not also have to remember the second site.

    Asked of the engine's pre-gate table directly, which is where the live loop's
    twin lives."""
    from agent_core.routines.engine import RoutineEngine

    registry, ordinary, _ = _registry()
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *_: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
    )
    refusal = engine._pre_gate_refusal(
        ordinary, "look_something_up", {}, None, PolicyMode.OPEN, TurnSurface.REMOTE
    )
    assert refusal is not None and refusal[0] == REMOTE_REFUSAL
    assert refusal[1] == "not_callable"
    # The desk is untouched, which is what the default argument is for.
    assert (
        engine._pre_gate_refusal(ordinary, "look_something_up", {}, None, PolicyMode.OPEN)
        is None
    )
