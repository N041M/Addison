"""Truncation-aware Continue (KNOWN-GAPS, judged 2026-08-09; built 2026-08-22).

One fact travels from the wire to the thread: THIS answer stopped because it hit
the model's output cap. Three tests' worth of surface:

- **lockstep** — each adapter's declared ``truncation_finish_reasons`` is exactly
  what that adapter EMITS for a cap-hit response. A spelling changed in an adapter
  and not in its capability (or the other way round) fails here, which is the only
  reason the pair can be trusted at all;
- **the orchestrator** — decides ``truncated`` for the answer it reports, through
  the capability and never through the provider's identity, on both the single and
  the routed path, and refuses to claim it for anything but the final answer;
- **the reply** — ``answeredWith.truncated`` reaches the frontend, which is what
  puts "Continue this answer" beside Retry.

The per-adapter wire fixtures live in each adapter's own test file (and
``test_streaming.py`` for the streamed folds). What is pinned HERE is the join
between them.
"""

from __future__ import annotations

import httpx
import pytest

from agent_core.memory.store import Store
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ProviderUnavailable,
    ToolCallRequest,
)
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.ollama_provider import OllamaProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.router import ModelRouter, RoutingCandidate
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ActionSnapshot, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry

from tests.conftest import IPC_DB_NAME, _shutdown, build_server


# ---------------------------------------------------------------------------
# 1. Lockstep: what an adapter EMITS is what its capability DECLARES.
# ---------------------------------------------------------------------------


def _client_for(payload: dict) -> httpx.Client:
    """A client that answers every request with the same JSON body — enough for a
    single non-streaming send, which is all these fixtures need."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _anthropic_at_cap():
    provider = AnthropicProvider(
        api_key_getter=lambda: "k",
        client=_client_for(
            {"content": [{"type": "text", "text": "The first three"}],
             "stop_reason": "max_tokens"}
        ),
    )
    return provider, "max_tokens"


def _openai_at_cap():
    provider = OpenAIProvider(
        model="gpt-4.1",
        api_key_getter=lambda: "k",
        client=_client_for(
            {"choices": [{"message": {"content": "The first three"},
                          "finish_reason": "length"}]}
        ),
    )
    return provider, "length"


def _custom_server_at_cap():
    # The custom OpenAI-compatible server is the SAME adapter with a different base
    # URL and label — included so nobody has to rediscover that, and so a future
    # split into two classes fails here rather than in someone's chat.
    provider = OpenAIProvider(
        model="local-model",
        api_key_getter=None,
        base_url="http://localhost:1234/v1",
        require_key=False,
        service_label="the server",
        client=_client_for(
            {"choices": [{"message": {"content": "The first three"},
                          "finish_reason": "length"}]}
        ),
    )
    return provider, "length"


def _google_at_cap():
    provider = GoogleProvider(
        model="gemini-2.5-pro",
        api_key_getter=lambda: "k",
        client=_client_for(
            {"candidates": [{"content": {"parts": [{"text": "The first three"}]},
                             "finishReason": "MAX_TOKENS"}]}
        ),
    )
    return provider, "MAX_TOKENS"


def _ollama_at_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["tools"]})
        return httpx.Response(
            200,
            json={"message": {"content": "The first three"},
                  "done": True, "done_reason": "length"},
        )

    provider = OllamaProvider(
        model="llama:8b", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return provider, "length"


_ADAPTERS_AT_CAP = {
    "anthropic": _anthropic_at_cap,
    "openai": _openai_at_cap,
    "custom-openai-compatible": _custom_server_at_cap,
    "google": _google_at_cap,
    "ollama": _ollama_at_cap,
}


@pytest.mark.parametrize("name", sorted(_ADAPTERS_AT_CAP))
def test_each_adapter_emits_exactly_the_spelling_its_capability_declares(name):
    provider, expected = _ADAPTERS_AT_CAP[name]()
    response = provider.send([Message(role="user", content="list ten reasons")], [])
    declared = provider.capabilities().truncation_finish_reasons

    # The adapter says the word it says…
    assert response.finish_reason == expected
    # …and the capability declares that same word. Change one without the other and
    # this goes red — which is the whole point: nothing else in the system can tell
    # that a provider's spelling drifted.
    assert declared, f"{name} declares no truncation spelling at all"
    assert response.finish_reason in declared


@pytest.mark.parametrize("name", sorted(_ADAPTERS_AT_CAP))
def test_no_adapter_declares_the_ordinary_stop_as_a_cap(name):
    # "stop" is what every adapter reports for an answer that finished. A capability
    # that listed it would put "Continue this answer" under every reply in the app.
    provider, _ = _ADAPTERS_AT_CAP[name]()
    assert "stop" not in provider.capabilities().truncation_finish_reasons


def test_a_provider_that_declares_nothing_is_the_default():
    # The empty default is load-bearing: it is what makes silence the outcome for a
    # provider nobody has taught this fact to.
    assert ProviderCapabilities(
        native_tool_calling=False,
        max_context_tokens=1,
        supports_streaming=False,
        runs_off_device=False,
    ).truncation_finish_reasons == ()


# ---------------------------------------------------------------------------
# 2. The orchestrator decides it, through the capability and nothing else.
# ---------------------------------------------------------------------------


class _Provider:
    """Replays scripted responses and declares its own cap spellings.

    ``reasons`` is what this provider's ``capabilities()`` returns, so a test can
    hand the orchestrator a word no real API uses — which is exactly how a
    hardcoded "max_tokens" in the orchestrator gets caught."""

    def __init__(self, script, *, reasons=("cap_hit",), raises=False, local=False):
        self._script = list(script)
        self._reasons = reasons
        self._raises = raises
        self._local = local
        self.sends = 0

    def capabilities(self) -> ProviderCapabilities:
        if self._raises:
            raise RuntimeError("this provider cannot say what it can do")
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=1000,
            supports_streaming=False,
            runs_off_device=self._local,
            truncation_finish_reasons=self._reasons,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sends += 1
        return self._script.pop(0)


class _Unavailable(_Provider):
    """A candidate that is busy every time — the routed path walks past it."""

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sends += 1
        raise ProviderUnavailable("busy")


class _SpyTool:
    definition = ToolDefinition(
        id="spy", label="Spy", description="t",
        risk_tier=RiskTier.LOW, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content="ok")


class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:  # pragma: no cover
        pass


def _registry_and_gate():
    registry = ToolRegistry()
    registry.register(_SpyTool())
    gate = PermissionGate()
    gate.grant("spy")
    return registry, gate


def _conversation() -> Conversation:
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="list ten reasons"))
    return conv


def _run_single(provider) -> list[tuple]:
    """The unwired single-provider path (no routing chain) — CLI and tests."""
    registry, gate = _registry_and_gate()
    answered: list[tuple] = []
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        on_answered=lambda mid, label, free, routed, truncated: answered.append(
            (mid, truncated)
        ),
    )
    orch.run_turn(_conversation())
    return answered


def _cand(model_id, provider_id):
    return RoutingCandidate(
        model_id=model_id, role=ModelRole.PRIMARY, provider_id=provider_id,
        quality_rank=None, free=False, local=False,
    )


def _run_routed(providers: dict, chain) -> list[tuple]:
    """The routed path (D4) — production."""
    registry, gate = _registry_and_gate()
    answered: list[tuple] = []
    router = ModelRouter(
        configured={}, primary_models={c.model_id: providers[c.model_id] for c in chain}
    )
    orch = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        routing_chain=lambda role, name: list(chain),
        on_answered=lambda mid, label, free, routed, truncated: answered.append(
            (mid, truncated)
        ),
    )
    orch.run_turn(_conversation())
    return answered


def _answer(text="The first three", reason="stop"):
    return ModelResponse(text=text, tool_calls=[], finish_reason=reason)


def _tool_round(reason="tool_use"):
    return ModelResponse(
        text=None,
        tool_calls=[ToolCallRequest(id="c1", tool_id="spy", args={})],
        finish_reason=reason,
    )


def test_the_single_path_reports_an_answer_that_hit_the_cap():
    assert _run_single(_Provider([_answer(reason="cap_hit")])) == [("default", True)]


def test_the_single_path_reports_nothing_for_an_ordinary_answer():
    assert _run_single(_Provider([_answer(reason="stop")])) == [("default", False)]


def test_the_routed_path_reports_an_answer_that_hit_the_cap():
    b = _Provider([_answer(reason="cap_hit")])
    assert _run_routed({"b": b}, [_cand("b", "pb")]) == [("b", True)]


def test_a_fallen_forward_answer_is_judged_by_the_model_that_ANSWERED():
    # A walked its chain and never answered; B did, and B is the one whose cap the
    # claim is about. Reading the loop's leftover variables instead would attribute
    # the fact to whichever candidate happened to be last in scope.
    a = _Unavailable([])
    b = _Provider([_answer(reason="cap_hit")])
    assert _run_routed({"a": a, "b": b}, [_cand("a", "pa"), _cand("b", "pb")]) == [("b", True)]


def test_a_mid_loop_tool_round_that_hit_the_cap_is_not_the_answer():
    # THE RULE, decided 2026-08-22: only the final answer — the text on screen — can
    # be claimed as cut off. The first round here carries the provider's own cap
    # spelling; the person never reads that round, and the loop carried on and
    # produced a complete answer. Claiming truncation would offer to resume text
    # nobody saw.
    provider = _Provider([_tool_round(reason="cap_hit"), _answer(reason="stop")])
    assert _run_single(provider) == [("default", False)]
    assert provider.sends == 2


def test_a_provider_that_declares_no_spelling_never_claims_the_cap():
    # Even with a response that any other provider would call truncated.
    provider = _Provider([_answer(reason="max_tokens")], reasons=())
    assert _run_single(provider) == [("default", False)]


def test_a_provider_whose_capabilities_raise_never_claims_the_cap():
    # Ollama asks its own server what a model can do, so capabilities() is a live
    # call that can fail. Fail toward silence, never toward a wrong offer.
    provider = _Provider([_answer(reason="cap_hit")], raises=True)
    assert _run_single(provider) == [("default", False)]


def test_the_orchestrator_knows_no_spelling_of_its_own():
    # MUTATION GUARD. A hardcoded "max_tokens" (or "length", or an isinstance branch
    # on the provider class) passes every test above that uses a real-looking word.
    # This provider's cap spelling is a word no API uses, and it is declared, so the
    # only way to answer True is to have ASKED the capability.
    provider = _Provider([_answer(reason="ran-out-of-room")], reasons=("ran-out-of-room",))
    assert _run_single(provider) == [("default", True)]

    # And the mirror: the most famous spelling in the business, undeclared, is not
    # a cap for a provider that never said it was.
    other = _Provider([_answer(reason="max_tokens")], reasons=("ran-out-of-room",))
    assert _run_single(other) == [("default", False)]


# ---------------------------------------------------------------------------
# 3. The reply carries it (main.py → answeredWith).
# ---------------------------------------------------------------------------


def _send_and_read_answered(tmp_path, provider) -> dict:
    harness = build_server(tmp_path, provider=provider)
    try:
        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "list ten reasons"}}
        )
        done = harness.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert done["result"]["ok"] is True, done
        return done["result"].get("answeredWith") or {}
    finally:
        _shutdown(harness.reader, harness.thread)


def test_the_reply_says_an_answer_was_cut_off(tmp_path):
    answered = _send_and_read_answered(tmp_path, _Provider([_answer(reason="cap_hit")]))
    assert answered.get("truncated") is True
    # The rest of the block is untouched — the free-model chip and the fallback note
    # read the same shape they always did.
    assert set(answered) == {"modelId", "label", "free", "routed", "truncated"}


def test_the_reply_says_nothing_of_the_kind_for_an_ordinary_answer(tmp_path):
    answered = _send_and_read_answered(tmp_path, _Provider([_answer(reason="stop")]))
    assert answered.get("truncated") is False


def test_a_turn_that_was_cut_off_is_still_a_whole_turn_on_disk(tmp_path):
    # Continue is an offer, not a repair: what the model DID say is persisted and
    # stays exactly as it was. The resumed answer arrives later as its own message.
    _send_and_read_answered(tmp_path, _Provider([_answer("The first three", "cap_hit")]))
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        rows = [
            row
            for conversation in store.list_conversations()
            for row in store.messages_for_conversation(conversation["id"])
        ]
    finally:
        store.close()
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "list ten reasons"),
        ("assistant", "The first three"),
    ]


def test_a_reply_with_no_answer_at_all_carries_no_block(tmp_path):
    # Nothing changed here, and it is worth a line: a turn that produced no final
    # answer reports no candidate, so there is no block and nothing claims a cap.
    provider = _Provider([_tool_round() for _ in range(40)])
    harness = build_server(tmp_path, provider=provider)
    try:
        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "go"}}
        )
        done = harness.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert "answeredWith" not in done["result"]
    finally:
        _shutdown(harness.reader, harness.thread)
