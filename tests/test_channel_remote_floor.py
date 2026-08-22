"""The remote floor — what a turn that arrived from a phone may use (PHASE 3).

[docs/messaging-channel-plan.md](../docs/messaging-channel-plan.md) §3.6 owns the
design. The floor now carries **three read-only ids** (owner decision 5), and that
is the claim:

    A phone can ask Addison to look something up, and everything else comes back as
    a plain sentence and a note waiting on the desk.

It was EMPTY through phase 2, which was not a placeholder but the strongest version
of that phase: every other seam (the thread, the hand-off, the conversation
isolation, the screening, the splitting, the pairing) was proven with the tool
question factored out. Filling the set was one line — and the four properties below
are the speed bump that line has to clear, asked of the REAL registry rather than of
a fixture, because the whole value of a closed list is that a tool registered
tomorrow does not join it by accident.

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

from agent_core.main import build_registry
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, TurnSurface
from agent_core.profiles import DEVELOPER, SIMPLE
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
    call_is_destructive,
)
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


def test_the_floor_is_exactly_the_three_ids_the_owner_answered():
    """THE SPEED BUMP ON THE ONE LINE. Editing ``REMOTE_TOOL_IDS`` is how a phone
    gains or loses an ability, and it is one line in one file — so the line has a
    test whose only job is to make changing it deliberate.

    The three are owner decision 5's answer, as proposed in §3.6: look something up
    on the web, read the page it found, do the maths. Everything else — the
    clipboard, the two file readers, opening a link on an unattended screen — is
    argued out at the code beside the set.

    Mutation: add a fourth id — this fails, and so do the four properties below if
    the id is not what the floor may hold."""
    assert REMOTE_TOOL_IDS == frozenset({"calculator", "web_search", "read_web_page"})


# The FOUR PROPERTIES of §3.6, asked of the registry the app actually builds
# (`main.build_registry`) rather than of a fixture. A fixture would prove the
# properties of a set of test tools; what matters is that they hold of the real ones,
# in the real profile, with `run_command` and the automation tools registered beside
# them.


def test_property_1_every_id_on_the_floor_is_registered_and_low():
    """LOW is documented at the code as *read-only, no undo needed*. An id on this
    list that was MEDIUM or HIGH would be a phone reaching something that changes
    the machine — and an id that is not registered at all would be a floor that
    silently offers nothing, which is a different bug wearing the same face.

    Mutation: put a MEDIUM id (`write_project_file`) on the floor — this fails."""
    registry = build_registry(profile=SIMPLE)
    for tool_id in sorted(REMOTE_TOOL_IDS):
        tool = registry.find(tool_id)
        assert tool is not None, f"{tool_id} is on the remote floor but is not registered"
        assert tool.definition.risk_tier is RiskTier.LOW, tool_id


def test_property_2_no_id_on_the_floor_is_open_only():
    """Nothing dev-only, nothing from an outside tool server, nothing that runs a
    command — in ANY mode. `_open_only` is the set the SAFE boundary keys off, and an
    intersection with it would be the floor admitting something Simple never sees.

    Mutation: put `run_command` on the floor — this fails here, and property 4 too."""
    registry = build_registry(profile=DEVELOPER)
    for tool_id in sorted(REMOTE_TOOL_IDS):
        assert not registry.is_dev_only(tool_id), f"{tool_id} is open_only"


def test_property_3_no_call_of_a_floor_tool_can_be_destructive():
    """`call_is_destructive` is a per-CALL question: a tool may classify its own call
    through ``is_destructive(args)``. So two things are asserted, and the second is
    the one that holds for arguments nobody has thought of — none of these tools
    defines the classifier AT ALL, so no argument can turn one destructive.

    It matters because a destructive call raises a card, and a card raised for a
    phone parks the worker thread forever (`_ask_once` waits with no timeout), taking
    every desktop turn with it — plan §2(d), the consequence the whole floor is
    shaped around.

    Mutation: give `calculator` an ``is_destructive`` returning True — this fails."""
    registry = build_registry(profile=SIMPLE)
    for tool_id in sorted(REMOTE_TOOL_IDS):
        tool = registry.find(tool_id)
        assert tool is not None
        assert call_is_destructive(tool, {}) is False, tool_id
        assert getattr(tool, "is_destructive", None) is None, (
            f"{tool_id} classifies its own calls, so some argument could make one "
            "destructive — and a card cannot be answered from a phone"
        )


def test_property_4_the_floor_is_a_subset_of_the_safe_view():
    """THE SENTENCE THE FLOOR IS REALLY MADE OF: *a remote turn is never offered a
    tool Simple could not be offered.* One assertion, and it holds the whole floor —
    which is why it is also a `doc_claims` row and is asserted a second time in
    `tests/test_tool_registry.py`, beside the registry's own tests.

    Asked in BOTH profiles, because the SAFE view is the same view either way and a
    floor that only held in Simple would not be a floor.

    Mutation: add `run_command` (dev-only, absent from the SAFE view) to the set —
    this fails."""
    for profile in (SIMPLE, DEVELOPER):
        registry = build_registry(profile=profile)
        safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
        assert REMOTE_TOOL_IDS <= safe_ids, sorted(REMOTE_TOOL_IDS - safe_ids)


def test_a_remote_turn_is_offered_exactly_the_three_in_either_mode():
    """The view, asked directly of the real registry. The same three in SAFE and in
    OPEN — a phone gets the same short list whichever profile the desk is in, because
    the mode question and the surface question are independent (policy.TurnSurface).

    And the desk is untouched: this is a second VIEW, not a change to the first."""
    registry = build_registry(profile=DEVELOPER)
    for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
        assert sorted(d.id for d in registry.remote_tools(mode)) == [
            "calculator",
            "read_web_page",
            "web_search",
        ]
    desk = {d.id for d in registry.visible_tools(PolicyMode.OPEN)}
    assert "run_command" in desk and "write_project_file" in desk


def test_the_remote_view_is_a_subset_of_the_safe_view_in_every_mode():
    """Property 4 again, this time of the VIEW rather than of the SET — the shape
    that keeps it true for ids nobody has added yet. `remote_tools` is an
    intersection with `visible_tools(mode)`, so the subset property is structural
    and survives any edit to the list."""
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


def test_every_low_tool_off_the_list_is_refused_on_a_remote_turn_and_none_at_the_desk():
    """THE TABLE TEST, over the registry the app builds: a tool registered tomorrow
    is refused from a phone BY DEFAULT, and its admission is a deliberate edit to a
    closed list. That is the property that makes the floor safe to live with — the
    dangerous direction is a new LOW tool quietly inheriting a phone.

    LOW is called out because "read-only" is exactly the reasoning that would tempt
    somebody to widen the floor into a tier test: `read_clipboard`, `read_file`,
    `read_project_file` and `open_link` are all LOW, all in the SAFE view, and all
    refused here.

    The desk direction matters just as much: this check must be invisible to every
    path that is not a phone.

    Mutation: make ``refuse_if_not_remote`` a tier test (`risk_tier is LOW`) — the
    four ids above stop being refused and this fails, naming them."""
    registry = build_registry(profile=DEVELOPER)
    low_ids = [
        d.id for d in registry.visible_tools(PolicyMode.OPEN) if d.risk_tier is RiskTier.LOW
    ]
    off_the_list = [tool_id for tool_id in low_ids if tool_id not in REMOTE_TOOL_IDS]
    assert {"read_clipboard", "read_file", "read_project_file", "open_link"} <= set(off_the_list)
    for tool_id in off_the_list:
        assert registry.refuse_if_not_remote(tool_id, TurnSurface.REMOTE) == REMOTE_REFUSAL
        assert registry.refuse_if_not_remote(tool_id, TurnSurface.DESK) is None
    # And the three on the list are refused from neither surface.
    for tool_id in sorted(REMOTE_TOOL_IDS):
        assert registry.refuse_if_not_remote(tool_id, TurnSurface.REMOTE) is None
    # An id nothing is registered under is refused too: the floor is a list of what
    # MAY run, so anything not on it is refused whether it exists or not.
    assert registry.refuse_if_not_remote("invented_by_the_model", TurnSurface.REMOTE)


def test_the_refusal_names_the_next_move_and_now_keeps_the_promise_it_makes():
    """A refusal with no next move comes back as a blocked task (LIVE_ONLY_REFUSAL's
    own rule), so the sentence has to name one. Phase 2 said only the first half —
    *that's something Addison does at your computer* — because the second half of
    §3.6's quoted copy promises a note waiting on a screen, and nothing was written
    down yet. This phase builds the queue, so the sentence is now whole and the
    promise is one the app keeps.

    Mutation: delete the queue (or the ``note_request`` call in
    ``_queue_refused_requests``) and leave this sentence — nothing fails HERE, which
    is exactly why the promise is also asserted end-to-end in
    `tests/test_channel_turn.py`: the phone is told a request was saved, and that
    test is the one that reads it back off the desk.

    Mutation for THIS test: drop the second sentence — it fails."""
    assert "at your computer" in REMOTE_REFUSAL
    assert "saved as a request" in REMOTE_REFUSAL
    assert "waiting on your screen" in REMOTE_REFUSAL
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


def test_a_phone_naming_run_command_is_refused_before_the_gate_and_leaves_a_row():
    """THE ONE THE WHOLE FEATURE IS JUDGED ON, on the REAL registry rather than a
    fixture: a phone asks for a shell command in the Developer profile, where
    `run_command` is registered, visible at the desk, and dev-only-satisfied. It is
    refused before the gate and before `execute`, and a row says so.

    ORDER IS BEHAVIOUR here. The remote refusal is FIRST among the pre-gate checks,
    so the reason recorded is *not from there* rather than the dev-only check having
    nothing to say (it is satisfied in OPEN) or the seatbelt refusing later. The gate
    is wired to GRANT, so a refusal that reached it would show up as a run.

    Mutation: move the ``refuse_if_not_remote`` branch below the dev-only one — the
    call still refuses, but this test's premise (OPEN mode) means it would then run.
    """
    registry = build_registry(profile=DEVELOPER)
    audit: list[dict] = []
    provider = _Provider(
        [
            ModelResponse(
                text=None,
                tool_calls=[
                    ToolCallRequest(id="call-1", tool_id="run_command", args={"command": "ls"})
                ],
            ),
            ModelResponse(text="I can't do that from your phone.", tool_calls=[]),
        ]
    )
    orchestrator = _orchestrator(registry, provider, audit)
    conversation = Conversation(id="remote-run-command")
    conversation.messages.append(Message(role="user", content="run ls for me"))
    orchestrator.run_turn(
        conversation, mode=PolicyMode.OPEN, surface=TurnSurface.REMOTE, stream_to=None
    )
    results = [m for m in conversation.messages if m.role == "tool"]
    assert [m.content for m in results] == [REMOTE_REFUSAL]
    assert [(r["tool_id"], r["outcome"]) for r in audit] == [("run_command", "not_callable")]
    # And what it WAS offered is the floor, in a profile whose desk view is the lot.
    assert sorted(d.id for d in provider.offered[0]) == [
        "calculator",
        "read_web_page",
        "web_search",
    ]


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
