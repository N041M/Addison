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
    exception_for_http_status,
    technical_detail,
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

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
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

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
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
           model_name=None, on_provider_attempt=None):
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
        on_provider_attempt=on_provider_attempt,
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
    assert answered == [("b", True, True)]         # free -> the disclaimer renders


def test_a_free_model_the_user_PICKED_still_reports_free():
    """The case the disclaimer was missing (owner decision 2026-08-12).

    Choosing your local model is the ORDINARY way to get a free answer, and it
    sets routed=False. While the chip required ``free && routed`` that turn showed
    nothing at all — a cost disclosure hidden precisely when it was most certainly
    true. ``free`` must be reported on its own; the frontend now renders on it."""
    b = _Provider([_answer("free!")], local=True)
    answered = []
    orch, conv = _build(
        {"b": b}, [_cand("b", "ollama", role=ModelRole.LOCAL, free=True, local=True)],
        on_answered=lambda mid, label, free, routed: answered.append((mid, free, routed)),
    )
    orch.run_turn(conv, model_name="b")            # the user picked the local model
    assert answered == [("b", True, False)]


def test_a_paid_answer_never_reports_free():
    """Free is a claim about cost, so it is only ever made where it is established
    by construction. A cloud candidate carries free=False and must report it."""
    a = _Provider([_answer("hi")])
    answered = []
    orch, conv = _build(
        {"a": a}, [_cand("a", "anthropic")],
        on_answered=lambda mid, label, free, routed: answered.append((mid, free, routed)),
    )
    orch.run_turn(conv)
    assert answered == [("a", False, True)]


def test_an_ollama_answer_on_the_unrouted_path_is_free_too():
    """The single-provider path has no candidate to read ``free`` off, so it used to
    report False unconditionally — a claim about cost, not the absence of one. It
    now asks the one thing that is free BY CONSTRUCTION: an Ollama local."""
    b = _Provider([_answer("free!")], local=True)
    registry = ToolRegistry()
    gate = PermissionGate()
    answered = []
    orch = Orchestrator(
        model_router=ModelRouter(configured={}, local_models={"b": b}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        on_answered=lambda mid, label, free, routed: answered.append((mid, free, routed)),
        # routing_chain deliberately UNWIRED -> the single-provider path.
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="hi"))
    orch.run_turn(conv, requested_role=ModelRole.LOCAL, model_name="b")
    assert answered == [("b", True, False)]


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


def test_all_cooled_tries_anyway_in_normal_order():
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


# --- The provider-attempt log (2026-08-07) ----------------------------------
# `usage_log` records the calls that WORKED, so a provider that never once
# succeeded left no trace anywhere. A Google key answered 404 for an evening while
# the Connections panel said "connected"; the only evidence was an activity line
# that scrolled away, and it said "busy". These pin the two halves that were
# missing: that every failure class leaves a row, and that the row carries the
# STATUS CODE — the thing that distinguishes "wait a moment" from "that model does
# not exist" and that was being discarded where the failure was classified.


def test_every_failure_class_leaves_a_row_with_the_status_the_server_sent():
    """One row per failed attempt, whatever the class — including `rejected`, which
    ends the turn and therefore never reaches the fallback note. That one is the
    reason this exists: it used to raise straight past every recording site."""
    rows: list[dict] = []
    a = _Provider([exception_for_http_status(400, "The request to Google failed (status 400).")])
    b = _Provider([_answer("should not run")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=rows.append)
    with pytest.raises(ProviderRequestRejected):
        orch.run_turn(conv)

    assert len(rows) == 1
    assert rows[0]["provider"] == "pa"
    assert rows[0]["model"] == "a"
    assert rows[0]["outcome"] == "rejected"
    # THE POINT OF THE ROW. Without this the log says a provider failed and leaves
    # you exactly where the activity line did.
    assert rows[0]["status_code"] == 400
    assert "400" in rows[0]["detail"]


def test_a_transient_failure_is_recorded_even_though_the_turn_succeeds():
    """The degrade path answers fine, so nothing else marks it — which is precisely
    how a provider can be broken for days while every turn looks healthy."""
    rows: list[dict] = []
    a = _Provider([exception_for_http_status(429, "A busy")])
    b = _Provider([_answer("done")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=rows.append)
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "done"
    assert [(r["outcome"], r["status_code"]) for r in rows] == [("unavailable", 429)]


def test_a_failure_that_never_reached_a_server_records_no_status():
    """NULL is the honest answer for a timeout. Inventing a 0 or a 500 would claim
    a reply nobody sent, and this row's whole value is that it does not guess."""
    rows: list[dict] = []
    a = _Provider([ProviderUnavailable("Couldn't reach Google.")])
    b = _Provider([_answer("done")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=rows.append)
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert rows[0]["status_code"] is None


def test_the_failure_that_reaches_the_person_names_the_provider_it_came_from():
    """KNOWN-BUGS P3 #12. The status and the server's sentence ride on the exception
    already; WHICH provider was being talked to exists nowhere but the walk, and the
    Developer profile's "Technical details" fold is where it has to end up.

    Stamped even with no attempt sink wired — the log is history, the exception is on
    its way to a person, and the second must not depend on the first."""
    exc = exception_for_http_status(400, "That key doesn't work.", "API key not valid.")
    orch, conv = _build({"a": _Provider([exc])}, [_cand("a", "pa")])
    with pytest.raises(ProviderRequestRejected) as raised:
        orch.run_turn(conv)

    assert technical_detail(raised.value).splitlines()[:3] == [
        "provider: pa \u00b7 a",
        "http status: 400",
        "provider said: API key not valid.",
    ]


def test_a_throwing_attempt_sink_never_breaks_the_turn():
    """Recording a failure must not be able to cause one. Every caller of this is
    already handling a bad day; a raise here would replace a provider problem the
    person can act on with a crash they cannot."""
    def explode(_row):
        raise RuntimeError("audit store is down")

    a = _Provider([exception_for_http_status(429, "A busy")])
    b = _Provider([_answer("done")])
    orch, conv = _build({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=explode)
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "done"


def test_the_log_keeps_what_the_SERVER_said_not_only_what_addison_said():
    """Two different sentences, and only one of them ends an investigation.

    Addison shows "The request to Google failed (status 404). Please try again."
    — plain language, the house rule, and the right thing on screen. The server
    said WHICH model and WHICH API version. Recording only ours meant the log
    faithfully preserved Addison's own guess about a failure nobody understood,
    which is how a real 404 survived four rounds of theorising."""
    rows: list[dict] = []
    exc = exception_for_http_status(
        404,
        "The request to Google failed (status 404). Please try again.",
        "This model models/gemini-2.5-flash is no longer available to new users.",
    )
    orch, conv = _build({"a": _Provider([exc]), "b": _Provider([_answer("x")])},
                        [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=rows.append)
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert rows[0]["detail"].startswith("The request to Google failed")
    assert "no longer available to new users" in rows[0]["server_detail"]


def test_an_unreadable_error_body_records_no_server_detail():
    """A failure to explain a failure must never become a failure. A proxy's HTML,
    an empty body, a truncated stream — all yield None rather than raising."""
    rows: list[dict] = []
    exc = exception_for_http_status(500, "The service had a problem.", None)
    orch, conv = _build({"a": _Provider([exc]), "b": _Provider([_answer("done")])},
                        [_cand("a", "pa"), _cand("b", "pb")],
                        on_provider_attempt=rows.append)
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)
    assert rows[0]["server_detail"] is None


def test_a_retired_model_falls_forward_to_its_SIBLING_not_to_another_vendor():
    """The failure this class was split out for, reported from a running app.

    Google's listing returned `gemini-2.5-flash` advertising `generateContent`;
    the generate endpoint answered 404 — "no longer available to new users". As a
    `ProviderRequestRejected` that ended the turn without walking, so a dead Gemini
    2.5 handed the conversation to Claude while a perfectly good Gemini 3 sat next
    in the chain. D4's "the next provider gets the same bad request" is true of a
    malformed body and false of a retired model.

    Both Google candidates share `provider_id`, so this ALSO pins the cooldown
    split: cooling the provider would have taken the sibling out with it and the
    turn would have reached Claude anyway — passing the assertion above for the
    wrong reason."""
    dead = _Provider([exception_for_http_status(404, "gone", "no longer available")])
    sibling = _Provider([_answer("gemini 3 answered")])
    claude = _Provider([_answer("should not be needed")])
    orch, conv = _build(
        {"dead": dead, "sibling": sibling, "claude": claude},
        [_cand("dead", "google"), _cand("sibling", "google"), _cand("claude", "anthropic")],
    )
    orch.run_turn(conv, mode=orch_mod.PolicyMode.SAFE)

    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "gemini 3 answered"
    assert claude.sends == 0, "a retired model must not hand the turn to another vendor"
    # The MODEL is stood down; its provider is untouched.
    assert orch._is_model_cooled("dead")
    assert not orch._is_cooled("google")
