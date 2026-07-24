"""Step-3 stage 2 — the orchestrator attempt loop (contract D4/D5, [MF-A]/[S-a]/[S-b]).

Graceful fallback, cooldown, the per-turn budget deadline, cross-provider forbid,
resolved-identity usage, and answeredWith. Each test pins one behaviour and is
built to go red if its rule is reverted.

Fakes here accept the ``timeout`` kwarg the routed path threads down ([MF-A]) and
can be scripted to answer, request a tool, or raise a provider exception.
"""

from __future__ import annotations

import time

import pytest

import agent_core.orchestrator as orch_mod
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderAuthFailed,
    ProviderCapabilities,
    ProviderRequestRejected,
    ProviderUnavailable,
    ToolCallRequest,
    Usage,
)
from agent_core.providers.router import ModelRouter, RoutingCandidate
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ActionSnapshot, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry


# --- fakes ------------------------------------------------------------------
class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:  # pragma: no cover
        pass


class _Provider:
    """Replays a scripted list; each item is a ModelResponse to return or an
    Exception to raise. Records the timeout it was handed on every send."""

    def __init__(self, script, *, local=False):
        self._script = list(script)
        self.timeouts: list[float | None] = []
        self.sends = 0
        self._local = local

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=1000,
            supports_streaming=False, runs_off_device=self._local,
        )

    def send(self, messages, tools, effort=None, timeout=None) -> ModelResponse:
        self.timeouts.append(timeout)
        self.sends += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _BlockingProvider:
    """Sleeps for the deadline it is given, then raises ProviderUnavailable —
    stands in for a candidate that hangs until its timeout. A missing deadline
    falls back to a long sleep, so a reverted ``timeout=`` shows up as an overrun."""

    def __init__(self):
        self.sends = 0
        self.timeouts: list[float | None] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=1000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None) -> ModelResponse:
        self.sends += 1
        self.timeouts.append(timeout)
        time.sleep(timeout if timeout is not None else 5.0)
        raise ProviderUnavailable("busy")


def _usage(n=1):
    return Usage(input_tokens=n, output_tokens=n)


def _answer(text="hi", usage=None):
    return ModelResponse(text=text, tool_calls=[], usage=usage)


def _tool_then(tool_id="spy", usage=None):
    return ModelResponse(
        text=None, tool_calls=[ToolCallRequest(id="c1", tool_id=tool_id, args={})], usage=usage
    )


class _SpyTool:
    definition = ToolDefinition(
        id="spy", label="Spy", description="t",
        risk_tier=RiskTier.LOW, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content="ok")


def _cand(model_id, provider_id, *, role=ModelRole.PRIMARY, free=False, local=False):
    return RoutingCandidate(
        model_id=model_id, role=role, provider_id=provider_id,
        quality_rank=None, free=free, local=local,
    )


def _build(providers: dict, chain, *, on_usage=None, on_answered=None, on_activity=None,
           model_name=None):
    """Orchestrator whose router resolves each candidate to its fake provider, with a
    fixed chain and spy callbacks. Returns (orchestrator, conversation)."""
    registry = ToolRegistry()
    registry.register(_SpyTool())
    gate = PermissionGate()
    gate.grant("spy")
    primary, local = {}, {}
    for c in chain:
        (local if c.role is ModelRole.LOCAL else primary)[c.model_id] = providers[c.model_id]
    router = ModelRouter(configured={}, primary_models=primary, local_models=local)
    orch = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        on_usage=on_usage or (lambda *a: None),
        on_answered=on_answered or (lambda *a: None),
        on_activity=on_activity or (lambda *a, **k: None),
        routing_chain=lambda role, name: list(chain),
        model_label=lambda mid: mid.upper(),
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="hi"))
    return orch, conv


# --- Verification #2: 429 -> next answers; note; cooldown; resolved usage ----
def test_unavailable_falls_forward_and_records_resolved_identity():
    a = _Provider([ProviderUnavailable("A busy")])
    # B does a tool round then a final answer -> TWO usage rows, both B's identity.
    b = _Provider([_tool_then(usage=_usage(1)), _answer("done", usage=_usage(2))])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    usage_rows, notes = [], []
    orch, conv = _build(
        {"a": a, "b": b}, chain,
        on_usage=lambda u, ms, pid, mid: usage_rows.append((pid, mid, u.input_tokens)),
        on_activity=lambda tid, label, detail=None: notes.append((tid, label)),
    )
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert a.sends == 1 and b.sends == 2            # A tried once, B answered (2 sends)
    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "done"
    # Both usage rows carry B's resolved identity — not the catalog default (N1).
    assert usage_rows == [("pb", "b", 1), ("pb", "b", 2)]
    # The fallback note names the busy head and the model used (D8 copy). Filter to
    # the routing channel — tool execution emits its own activity notes too.
    assert [n for n in notes if n[0] == "routing"] == [
        ("routing", "A was busy, so Addison used B.")
    ]
    # A was cooled.
    assert orch._is_cooled("pa")


# --- Verification #3: Rejected fails immediately, chain NOT walked -----------
def test_request_rejected_does_not_walk_the_chain():
    a = _Provider([ProviderRequestRejected("bad request")])
    b = _Provider([_answer("should not run")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")])
    with pytest.raises(ProviderRequestRejected):
        orch.run_turn(conv)
    assert a.sends == 1 and b.sends == 0            # B never tried
    assert not orch._is_cooled("pa")               # a rejected request is not a cooldown


def test_auth_failed_does_not_walk_the_chain():
    a = _Provider([ProviderAuthFailed("no key")])
    b = _Provider([_answer("should not run")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")])
    with pytest.raises(ProviderAuthFailed):
        orch.run_turn(conv)
    assert b.sends == 0


# --- Verification #5 + [S-b]: the answeredWith chip --------------------------
def test_routed_free_answer_reports_the_chip():
    # No explicit pick + a free model answered -> routed True, free True -> chip.
    b = _Provider([_answer("free!")], local=True)
    answered = []
    orch, conv = _build(
        {"b": b}, [_cand("b", "ollama", role=ModelRole.LOCAL, free=True, local=True)],
        on_answered=lambda mid, label, free, routed: answered.append((mid, free, routed)),
    )
    orch.run_turn(conv)
    assert answered == [("b", True, True)]         # free && routed -> chip renders


def test_explicit_pick_that_answered_is_not_routed():
    a = _Provider([_answer("hi")])
    answered = []
    orch, conv = _build(
        {"a": a}, [_cand("a", "pa")],
        on_answered=lambda mid, label, free, routed: answered.append((mid, free, routed)),
    )
    orch.run_turn(conv, model_name="a")            # user explicitly picked "a"
    assert answered == [("a", False, False)]       # answered the pick -> not routed


def test_explicit_pick_that_fell_forward_is_routed():
    # [S-b]: an explicit pick that fell forward to a different model IS routed.
    a = _Provider([ProviderUnavailable("busy")])
    b = _Provider([_answer("hi")])
    answered = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")],
        on_answered=lambda mid, label, free, routed: answered.append((mid, routed)),
    )
    orch.run_turn(conv, model_name="a")            # picked A, but A was busy
    assert answered == [("b", True)]               # answered by B -> routed


# --- Verification #6 + [MF-E]: cross-provider forbid / same-provider allowed -
def test_cross_provider_advance_forbidden_after_a_tool_round():
    # A completes a tool round, then 429s on the follow-up send. B is a DIFFERENT
    # provider, so the mid-turn advance is forbidden -> the turn fails plainly.
    a = _Provider([_tool_then(), ProviderUnavailable("A busy")])
    b = _Provider([_answer("should not run")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")])
    with pytest.raises(ProviderUnavailable):
        orch.run_turn(conv)
    assert b.sends == 0                             # never crossed to B


def test_same_provider_advance_allowed_after_a_tool_round():
    # Two Ollama models share provider_id "ollama" ([MF-E]): after A's tool round,
    # A 429s and the advance to B (same provider) IS permitted.
    a = _Provider([_tool_then(), ProviderUnavailable("A busy")], local=True)
    b = _Provider([_answer("done")], local=True)
    chain = [
        _cand("a", "ollama", role=ModelRole.LOCAL, local=True),
        _cand("b", "ollama", role=ModelRole.LOCAL, local=True),
    ]
    orch, conv = _build({"a": a, "b": b}, chain)
    orch.run_turn(conv)
    assert b.sends == 1                             # advance within the ollama pool
    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "done"


# --- Verification #8 + [MF-A]: the budget deadline interrupts a hang ---------
def test_budget_deadline_bounds_a_blocking_candidate(monkeypatch):
    monkeypatch.setattr(orch_mod, "_FALLBACK_BUDGET_SECONDS", 0.3)
    blocker = _BlockingProvider()
    orch, conv = _build({"a": blocker}, [_cand("a", "pa")])
    start = time.monotonic()
    with pytest.raises(ProviderUnavailable):
        orch.run_turn(conv)
    elapsed = time.monotonic() - start
    # The deadline (threaded into send) cut the ~5s block down to the budget. A
    # reverted ``timeout=`` would let the block run its full 5s and blow this.
    assert elapsed < 1.5
    assert blocker.sends >= 1
    # The deadline handed to the send tracked the remaining budget, never None.
    assert blocker.timeouts and all(t is not None for t in blocker.timeouts)


def test_no_send_once_the_budget_is_spent(monkeypatch):
    # budget 0 -> remaining <= 0 before the first send -> nothing leaves the machine.
    monkeypatch.setattr(orch_mod, "_FALLBACK_BUDGET_SECONDS", 0.0)
    a = _Provider([_answer("should not run")])
    orch, conv = _build({"a": a}, [_cand("a", "pa")])
    with pytest.raises(ProviderUnavailable):
        orch.run_turn(conv)
    assert a.sends == 0                             # the pre-send budget check held


# --- cooldown behaviour (D4 / [S-a]) ----------------------------------------
def test_cooled_provider_is_skipped_next_turn():
    a = _Provider([ProviderUnavailable("busy"), _answer("A back")])
    b = _Provider([_answer("B1"), _answer("B2")])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    orch, conv = _build({"a": a, "b": b}, chain)
    orch.run_turn(conv)                            # turn 1: A busy -> B answers, A cooled
    assert a.sends == 1 and b.sends == 1
    conv2 = Conversation(id="c2")
    conv2.messages.append(Message(role="user", content="again"))
    orch.run_turn(conv2)                           # turn 2: A cooled -> straight to B
    assert a.sends == 1                            # A NOT retried while cooled
    assert b.sends == 2


def test_all_cooled_tries_anyway_in_normal_order(monkeypatch):
    a = _Provider([_answer("A answers")])
    b = _Provider([_answer("unused")])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    orch, conv = _build({"a": a, "b": b}, chain)
    # Pre-cool BOTH providers.
    orch._cool("pa")
    orch._cool("pb")
    orch.run_turn(conv)
    # Try-anyway walks in normal (preferred-first) order: A is tried and answers.
    assert a.sends == 1
    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "A answers"


def test_a_cooled_head_still_gets_the_fallback_note():
    """Post-build rigor pass, 2026-07-24: ``preferred`` must be the PRE-cooldown
    chain head. A head cooled by a previous turn's failure meant this turn went
    straight to a weaker model with NO note — the exact quiet substitution the
    note exists to surface. What the user's settings say should answer is
    'preferred', whether or not it is currently cooled."""
    a = _Provider([_answer("never asked")])
    b = _Provider([_answer("done")])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    notes = []
    orch, conv = _build(
        {"a": a, "b": b}, chain,
        on_activity=lambda tid, label, detail=None: notes.append((tid, label)),
    )
    orch._cool("pa")                       # a previous turn found A busy
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert a.sends == 0 and b.sends == 1   # cooldown skipped A, B answered
    assert [n for n in notes if n[0] == "routing"] == [
        ("routing", "A was busy, so Addison used B.")
    ]
