"""Per-token streaming — the provider translators and the orchestrator's relay.

Two properties are load-bearing here, and every test in this file exists to
protect one of them:

1. **What is streamed equals what is returned.** The display may lag the
   ``ModelResponse``; it may never say something the response does not. So each
   provider test asserts the joined deltas against ``response.text``, not just
   that "some deltas arrived".
2. **Only prose reaches the sink.** Tool-call arguments and reasoning blocks are
   machinery. Streaming them would show the reader half-built JSON as though
   Addison had said it, so several tests assert on what is ABSENT from the sink.

The orchestrator half then pins the two rules the relay enforces: a provider that
IGNORES ``on_delta`` still gets its answer pushed (exactly once), and a stream
that dies after showing text may not fall forward onto another model.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_core.orchestrator import Conversation, Orchestrator, PolicyMode
from agent_core.permissions.gate import PermissionGate
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ProviderUnavailable,
    Usage,
    iter_sse_json,
)
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.ollama_provider import OllamaProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.router import ModelRouter, RoutingCandidate
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.registry import ToolRegistry

# --- helpers ---------------------------------------------------------------


def sse(*frames: dict | str) -> bytes:
    """Build an SSE body. A raw string is emitted verbatim (malformed frames)."""
    out = b""
    for frame in frames:
        payload = frame if isinstance(frame, str) else json.dumps(frame)
        out += f"data: {payload}\n\n".encode()
    return out


def ndjson(*frames: dict | str) -> bytes:
    out = b""
    for frame in frames:
        payload = frame if isinstance(frame, str) else json.dumps(frame)
        out += payload.encode() + b"\n"
    return out


def client_for(body: bytes, status: int = 200, capture: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(json.loads(request.read() or b"{}"))
            capture.append(str(request.url))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class Sink:
    """A delta sink that records what the reader would have seen."""

    def __init__(self) -> None:
        self.pieces: list[str] = []

    def __call__(self, text: str) -> None:
        self.pieces.append(text)

    @property
    def shown(self) -> str:
        return "".join(self.pieces)


def a_tool(tool_id: str = "read_web_page"):
    class _Def:
        id = tool_id
        description = "d"
        parameters_schema: dict = {"type": "object"}

    return _Def()


HELLO = [Message(role="user", content="hello")]


# --- the shared SSE splitter ----------------------------------------------


def test_iter_sse_json_skips_everything_that_is_not_a_data_frame():
    body = (
        b"event: ping\n"
        b": a comment\n"
        b"\n"
        b'data: {"n":1}\n\n'
        b"data: \n\n"
        b"data: not json\n\n"
        b'data: ["an array, not an object"]\n\n'
        b"data: [DONE]\n\n"
        b'data: {"n":2}\n\n'
    )
    response = httpx.Response(200, content=body)
    assert [f["n"] for f in iter_sse_json(response)] == [1, 2]


# --- Anthropic -------------------------------------------------------------


def test_anthropic_streams_text_and_returns_the_same_answer():
    sink = Sink()
    body = sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Of "}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "course."},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
    )
    provider = AnthropicProvider(
        model="claude-opus-4-8", api_key_getter=lambda: "k", client=client_for(body)
    )

    response = provider.send(HELLO, [], on_delta=sink)

    assert sink.pieces == ["Of ", "course."]
    # Property 1: the display and the response agree, exactly.
    assert response.text == sink.shown == "Of course."
    assert response.finish_reason == "end_turn"
    assert response.usage == Usage(input_tokens=11, output_tokens=4)


def test_anthropic_sets_stream_true_on_the_request():
    captured: list = []
    provider = AnthropicProvider(
        model="m", api_key_getter=lambda: "k", client=client_for(sse(), capture=captured)
    )
    provider.send(HELLO, [], on_delta=Sink())
    assert captured[0]["stream"] is True


def test_anthropic_without_a_sink_does_not_stream():
    """The non-streaming path must stay exactly as it was: no ``stream`` key."""
    captured: list = []
    body = json.dumps(
        {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    ).encode()
    provider = AnthropicProvider(
        model="m", api_key_getter=lambda: "k", client=client_for(body, capture=captured)
    )
    response = provider.send(HELLO, [])
    assert "stream" not in captured[0]
    assert response.text == "hi"


def test_anthropic_never_streams_a_tool_calls_arguments():
    """``input_json_delta`` is machinery: accumulated, parsed, never shown."""
    sink = Sink()
    body = sse(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Let me look."},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "read_web_page"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"url": "https:'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '//example.com"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    )
    provider = AnthropicProvider(
        model="m", api_key_getter=lambda: "k", client=client_for(body)
    )

    response = provider.send(HELLO, [a_tool()], on_delta=sink)

    # Property 2: only the prose was shown.
    assert sink.shown == "Let me look."
    assert "url" not in sink.shown and "{" not in sink.shown
    # ...and the call itself came through whole.
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert (call.id, call.tool_id) == ("tu_1", "read_web_page")
    assert call.args == {"url": "https://example.com"}
    assert response.finish_reason == "tool_use"


def test_anthropic_drops_a_thinking_delta_rather_than_showing_it():
    """A reasoning block is not part of the answer, so it reaches neither the
    reader nor ``response.text``."""
    sink = Sink()
    body = sse(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm, let me consider"},
        },
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Yes."}},
    )
    provider = AnthropicProvider(model="m", api_key_getter=lambda: "k", client=client_for(body))

    response = provider.send(HELLO, [], on_delta=sink)

    assert sink.shown == "Yes."
    assert response.text == "Yes."


def test_anthropic_maps_a_bad_status_on_the_streaming_path_too():
    provider = AnthropicProvider(
        model="m", api_key_getter=lambda: "k", client=client_for(b"", status=429)
    )
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.send(HELLO, [], on_delta=Sink())
    # The same plain sentence the non-streaming path raises — no stack trace, no body.
    assert "busy right now" in str(excinfo.value)


def test_anthropic_turns_a_mid_stream_error_event_into_unavailable():
    sink = Sink()
    body = sse(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Star"}},
        {"type": "error", "error": {"type": "overloaded_error"}},
    )
    provider = AnthropicProvider(model="m", api_key_getter=lambda: "k", client=client_for(body))

    with pytest.raises(ProviderUnavailable):
        provider.send(HELLO, [], on_delta=sink)
    # What was shown before the failure is not un-shown; the orchestrator is what
    # decides that a turn in this state may not fall forward (see below).
    assert sink.shown == "Star"


def test_anthropic_tolerates_a_truncated_final_frame():
    """A stream is read as it arrives, so a half-written last frame is an ordinary
    way for one to end — never an exception over an answer already on screen."""
    sink = Sink()
    body = sse(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done."}}
    ) + b'data: {"type":"content_bl\n\n'
    provider = AnthropicProvider(model="m", api_key_getter=lambda: "k", client=client_for(body))

    response = provider.send(HELLO, [], on_delta=sink)
    assert response.text == sink.shown == "Done."


# --- OpenAI ----------------------------------------------------------------


def test_openai_streams_text_and_reassembles_fragmented_tool_arguments():
    sink = Sink()
    body = sse(
        {"choices": [{"delta": {"content": "One "}}]},
        {"choices": [{"delta": {"content": "moment."}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_a",
                                "function": {"name": "read_web_page", "arguments": '{"ur'},
                            }
                        ]
                    }
                }
            ]
        },
        # The later fragments carry NO id or name — only the index.
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'l": "x"}'}}]}}
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3}},
    )
    provider = OpenAIProvider(model="m", api_key_getter=lambda: "k", client=client_for(body))

    response = provider.send(HELLO, [a_tool()], on_delta=sink)

    assert sink.shown == "One moment."
    assert response.text == "One moment."
    assert "url" not in sink.shown
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert (call.id, call.tool_id, call.args) == ("call_a", "read_web_page", {"url": "x"})
    assert response.finish_reason == "tool_use"
    # include_usage is what keeps §4.8's usage_log honest for a streamed turn.
    assert response.usage == Usage(input_tokens=9, output_tokens=3)


def test_openai_asks_for_usage_when_streaming():
    captured: list = []
    provider = OpenAIProvider(
        model="m", api_key_getter=lambda: "k", client=client_for(sse(), capture=captured)
    )
    provider.send(HELLO, [], on_delta=Sink())
    assert captured[0]["stream"] is True
    assert captured[0]["stream_options"] == {"include_usage": True}


def test_openai_reports_no_usage_when_the_server_ignores_the_option():
    """A custom OpenAI-compatible endpoint may not honour ``include_usage``. That
    yields None — never a guessed count."""
    body = sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
    provider = OpenAIProvider(model="m", api_key_getter=lambda: "k", client=client_for(body))
    response = provider.send(HELLO, [], on_delta=Sink())
    assert response.text == "hi"
    assert response.usage is None


# --- Google ----------------------------------------------------------------


def test_google_streams_text_and_uses_the_sse_endpoint():
    captured: list = []
    sink = Sink()
    body = sse(
        {
            "candidates": [{"content": {"parts": [{"text": "Right"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1},
        },
        {
            "candidates": [{"content": {"parts": [{"text": " away."}]}}],
            # CUMULATIVE, not additive — the last frame is the true total.
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        },
    )
    provider = GoogleProvider(
        model="gemini-x", api_key_getter=lambda: "k", client=client_for(body, capture=captured)
    )

    response = provider.send(HELLO, [], on_delta=sink)

    assert ":streamGenerateContent" in captured[1]
    assert "alt=sse" in captured[1]
    assert sink.shown == response.text == "Right away."
    # Summing the frames would report 4 output tokens for a 3-token answer.
    assert response.usage == Usage(input_tokens=5, output_tokens=3)


def test_google_collects_a_streamed_function_call_without_showing_it():
    sink = Sink()
    body = sse(
        {"candidates": [{"content": {"parts": [{"text": "Checking."}]}}]},
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "read_web_page", "args": {"url": "x"}}}]
                    }
                }
            ]
        },
    )
    provider = GoogleProvider(model="g", api_key_getter=lambda: "k", client=client_for(body))

    response = provider.send(HELLO, [a_tool()], on_delta=sink)

    assert sink.shown == "Checking."
    assert [c.tool_id for c in response.tool_calls] == ["read_web_page"]
    assert response.tool_calls[0].args == {"url": "x"}
    assert response.finish_reason == "tool_use"


# --- Ollama ----------------------------------------------------------------


def _ollama(body: bytes, *, native: bool, capture: list | None = None) -> OllamaProvider:
    provider = OllamaProvider(model="llama", client=client_for(body, capture=capture))
    # Skip the /api/show round-trip: state the capability directly.
    provider._metadata_cache = {"capabilities": ["tools"] if native else []}
    return provider


def test_ollama_streams_ndjson_lines():
    sink = Sink()
    body = ndjson(
        {"message": {"content": "Local "}, "done": False},
        {"message": {"content": "answer."}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 4, "eval_count": 2},
    )
    provider = _ollama(body, native=True)

    response = provider.send(HELLO, [], on_delta=sink)

    assert sink.pieces == ["Local ", "answer."]
    assert response.text == "Local answer."
    assert response.usage == Usage(input_tokens=4, output_tokens=2)


def test_ollama_collects_native_tool_calls_while_streaming():
    sink = Sink()
    body = ndjson(
        {"message": {"content": "Looking."}, "done": False},
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "read_web_page", "arguments": {"url": "x"}}}],
            },
            "done": True,
        },
    )
    provider = _ollama(body, native=True)

    response = provider.send(HELLO, [a_tool()], on_delta=sink)

    assert sink.shown == "Looking."
    assert [c.tool_id for c in response.tool_calls] == ["read_web_page"]
    assert response.finish_reason == "tool_use"


def test_ollama_refuses_to_stream_the_fenced_json_fallback_path():
    """THE reason Ollama's streaming is conditional.

    A model without native tool calling returns its calls as a fenced JSON block
    inside the ordinary reply text. Streaming that would put the raw call on screen
    as though Addison had said it — and whether a given piece of text is prose or
    machinery is only knowable once the block is complete. So a fallback turn WITH
    tools answers whole, and the sink stays empty.
    """
    sink = Sink()
    captured: list = []
    fenced = '```json\n{"tool": "read_web_page", "args": {"url": "x"}}\n```'
    provider = _ollama(
        json.dumps({"message": {"content": fenced}}).encode(), native=False, capture=captured
    )

    response = provider.send(HELLO, [a_tool()], on_delta=sink)

    assert sink.pieces == []                     # nothing was shown...
    assert captured[0]["stream"] is False        # ...because it never streamed
    assert [c.tool_id for c in response.tool_calls] == ["read_web_page"]
    assert response.text is None


def test_ollama_still_streams_a_non_native_model_when_no_tools_are_offered():
    """With no tools there is no fenced block to mistake for prose, so a plain
    chat with a non-native model streams like any other."""
    sink = Sink()
    provider = _ollama(ndjson({"message": {"content": "Sure."}, "done": True}), native=False)
    response = provider.send(HELLO, [], on_delta=sink)
    assert sink.shown == response.text == "Sure."


# --- the orchestrator's relay ---------------------------------------------


class _Streaming:
    """A provider that honours ``on_delta``: emits each scripted chunk, then either
    returns the assembled answer or raises. ``fail_after`` streams the chunks first
    and THEN fails, which is the state the no-fall-forward rule exists for."""

    def __init__(self, chunks, *, honours_sink=True, fail_after=False, fail_first=False):
        self._chunks = list(chunks)
        self._honours_sink = honours_sink
        self._fail_after = fail_after
        self._fail_first = fail_first
        self.sends = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=1000,
            supports_streaming=self._honours_sink,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sends += 1
        if self._fail_first:
            raise ProviderUnavailable("Busy right now. Try again in a moment.")
        if self._honours_sink and on_delta is not None:
            for chunk in self._chunks:
                on_delta(chunk)
        if self._fail_after:
            raise ProviderUnavailable("The service had a problem. Please try again in a moment.")
        return ModelResponse(text="".join(self._chunks), tool_calls=[])


def _cand(model_id, provider_id):
    return RoutingCandidate(
        model_id=model_id,
        role=ModelRole.PRIMARY,
        provider_id=provider_id,
        quality_rank=None,
        free=False,
        local=False,
    )


def _orchestrator(providers: dict, chain, emitted: list):
    """An Orchestrator whose router resolves each candidate to its fake, recording
    everything that reaches the frontend. ``chain=None`` takes the single-provider
    path (the CLI/tests freeze) instead of the routed one."""
    registry = ToolRegistry()
    router = ModelRouter(configured={}, primary_models=dict(providers), local_models={})
    orch = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=_NoStore(), tool_registry=registry),
        stream_to_frontend=emitted.append,
        routing_chain=lambda role, name: (list(chain) if chain else None),
        model_label=lambda mid: mid.upper(),
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="hi"))
    return orch, conversation


class _NoStore:
    def insert_action_snapshot(self, snapshot) -> None:  # pragma: no cover - unused
        pass


def _final_assistant_text(conversation) -> str | None:
    said = [m.content for m in conversation.messages if m.role == "assistant"]
    return said[-1] if said else None


def test_a_streamed_answer_reaches_the_frontend_once_as_deltas():
    """The deltas ARE the delivery. Pushing ``response.text`` afterwards as well
    would show the reader the whole answer a second time."""
    emitted: list = []
    provider = _Streaming(["Good ", "morning."])
    orch, conversation = _orchestrator({"m": provider}, [_cand("m", "p")], emitted)

    orch.run_turn(conversation, mode=PolicyMode.SAFE)

    assert emitted == ["Good ", "morning."]
    assert _final_assistant_text(conversation) == "Good morning."


def test_a_provider_that_ignores_the_sink_still_gets_its_answer_pushed():
    """The other half of the same rule: no deltas means the finished text is the
    reader's ONLY copy of the answer, so it must still be sent — exactly once.
    This is the Setup Assistant relay's path, and any future adapter that cannot
    stream."""
    emitted: list = []
    provider = _Streaming(["All ", "done."], honours_sink=False)
    orch, conversation = _orchestrator({"m": provider}, [_cand("m", "p")], emitted)

    orch.run_turn(conversation, mode=PolicyMode.SAFE)

    assert emitted == ["All done."]
    assert _final_assistant_text(conversation) == "All done."


def test_the_single_provider_path_streams_too():
    """``_run_single`` is the CLI/test freeze path — it must relay deltas on the
    same terms, and must not re-push the finished text on top of them."""
    emitted: list = []
    provider = _Streaming(["Local ", "reply."])
    orch, conversation = _orchestrator({"m": provider}, None, emitted)

    orch.run_turn(conversation, model_name="m", mode=PolicyMode.SAFE)

    assert emitted == ["Local ", "reply."]


def test_a_stream_that_dies_after_showing_text_does_not_fall_forward():
    """The honesty rule for the routed path: the next candidate would produce a
    COMPLETE answer, which appends to the partial one and yields a single message
    that reads as one answer and is two. Nothing can un-say what was already
    shown, so the turn fails with this provider's own sentence instead."""
    emitted: list = []
    first = _Streaming(["I think "], fail_after=True)
    second = _Streaming(["A completely different answer."])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    orch, conversation = _orchestrator({"a": first, "b": second}, chain, emitted)

    with pytest.raises(ProviderUnavailable):
        orch.run_turn(conversation, mode=PolicyMode.SAFE)

    assert emitted == ["I think "]      # the partial answer, and nothing after it
    assert second.sends == 0            # the second model was never asked


def test_a_stream_that_dies_before_showing_anything_still_falls_forward():
    """The counterpart: nothing was shown, so the chain behaves exactly as it
    always has. This is what keeps streaming from costing robustness."""
    emitted: list = []
    first = _Streaming([], fail_first=True)
    second = _Streaming(["Second ", "model."])
    chain = [_cand("a", "pa"), _cand("b", "pb")]
    orch, conversation = _orchestrator({"a": first, "b": second}, chain, emitted)

    orch.run_turn(conversation, mode=PolicyMode.SAFE)

    assert emitted == ["Second ", "model."]
    assert first.sends == 1 and second.sends == 1
    assert _final_assistant_text(conversation) == "Second model."
