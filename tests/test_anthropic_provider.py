"""AnthropicProvider request/response translation and key handling.

Covers the Messages API mapping (engineering-spec §4.1) and the hard key
invariant (§5 / CLAUDE.md §8.3): the key is fetched fresh per send(), used only
for that request, and never retained on the provider or leaked into an error.

The HTTP boundary is faked with ``httpx.MockTransport`` so no real network call
happens — the same technique the orchestrator tests use to stay offline.
"""

import json

import httpx
import pytest

from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, ToolCallRequest


class _StubTool:
    """Duck-typed ToolDefinition. providers/ must not import from tools/ (module
    boundary rule), so send() only sees an object with these three attributes."""

    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


def _make(response_payload, *, status_code=200, key="sk-test-key"):
    """Build a provider wired to a MockTransport plus the captured outgoing
    request and a per-send key-getter call counter."""
    captured: dict = {}
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return key

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(status_code, json=response_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(api_key_getter=getter, client=client)
    return provider, captured, calls


def test_request_translation_tools_and_tool_result_pairing():
    provider, captured, _ = _make(
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    tool = _StubTool(
        "read_file",
        "Read a file from disk",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )
    messages = [
        Message(role="user", content="read a.txt and b.txt"),
        Message(
            role="assistant",
            content="Sure, reading both.",
            tool_calls=[
                ToolCallRequest(id="tu_1", tool_id="read_file", args={"path": "a.txt"}),
                ToolCallRequest(id="tu_2", tool_id="read_file", args={"path": "b.txt"}),
            ],
        ),
        Message(role="tool", content="contents of a", tool_call_id="tu_1"),
        Message(role="tool", content="contents of b", tool_call_id="tu_2"),
    ]

    provider.send(messages, [tool])
    body = captured["body"]

    # Tool translation: id -> name, parameters_schema -> input_schema.
    assert body["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file from disk",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]

    api_messages = body["messages"]
    # user, assistant(tool_use), then ONE merged user turn of tool_results.
    assert [msg["role"] for msg in api_messages] == ["user", "assistant", "user"]

    assistant_content = api_messages[1]["content"]
    assert assistant_content[0] == {"type": "text", "text": "Sure, reading both."}
    assert assistant_content[1] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "read_file",
        "input": {"path": "a.txt"},
    }
    assert assistant_content[2] == {
        "type": "tool_use",
        "id": "tu_2",
        "name": "read_file",
        "input": {"path": "b.txt"},
    }

    # Consecutive tool messages merged into one user turn, ids paired to the
    # tool_use blocks above.
    merged = api_messages[2]["content"]
    assert [b["type"] for b in merged] == ["tool_result", "tool_result"]
    assert merged[0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "contents of a"}
    assert merged[1] == {"type": "tool_result", "tool_use_id": "tu_2", "content": "contents of b"}


def test_assistant_tool_use_without_text_omits_text_block():
    provider, captured, _ = _make(
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    messages = [
        Message(role="user", content="go"),
        Message(
            role="assistant",
            content="",  # no prose alongside the tool call
            tool_calls=[ToolCallRequest(id="tu_1", tool_id="calculator", args={"expression": "1+1"})],
        ),
        Message(role="tool", content="2", tool_call_id="tu_1"),
    ]
    provider.send(messages, [])
    assistant_content = captured["body"]["messages"][1]["content"]
    # Only the tool_use block — no leading empty text block.
    assert [b["type"] for b in assistant_content] == ["tool_use"]


def test_tools_key_omitted_when_no_tools():
    provider, captured, _ = _make(
        {"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
    )
    provider.send([Message(role="user", content="hello")], [])
    assert "tools" not in captured["body"]
    assert captured["body"]["model"] == "claude-opus-4-8"


def test_response_tool_use_yields_tool_call_requests():
    payload = {
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "tu_9", "name": "calculator", "input": {"expression": "2+2"}},
        ],
        "stop_reason": "tool_use",
    }
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="what is 2+2")], [])

    assert resp.text == "Let me check."
    assert resp.finish_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert (tc.id, tc.tool_id, tc.args) == ("tu_9", "calculator", {"expression": "2+2"})


def test_response_end_turn_text_only_has_no_tool_calls():
    payload = {"content": [{"type": "text", "text": "The answer is 4."}], "stop_reason": "end_turn"}
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="what is 2+2")], [])

    assert resp.text == "The answer is 4."
    assert resp.tool_calls == []
    assert resp.finish_reason == "end_turn"


def test_response_with_no_text_blocks_has_none_text():
    payload = {
        "content": [{"type": "tool_use", "id": "tu_x", "name": "t", "input": {}}],
        "stop_reason": "tool_use",
    }
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="go")], [])
    assert resp.text is None
    assert len(resp.tool_calls) == 1


def test_key_getter_called_once_per_send_and_key_not_retained():
    payload = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    provider, captured, calls = _make(payload, key="sk-secret-xyz")

    provider.send([Message(role="user", content="a")], [])
    provider.send([Message(role="user", content="b")], [])

    assert calls["n"] == 2  # one keychain fetch per send, never cached
    assert captured["headers"]["x-api-key"] == "sk-secret-xyz"  # it does get sent
    # ...but it is never stored on the instance.
    assert "sk-secret-xyz" not in repr(provider.__dict__)


def test_missing_getter_raises_plain_language():
    provider = AnthropicProvider(api_key_getter=None)
    with pytest.raises(RuntimeError, match="No API key"):
        provider.send([Message(role="user", content="hi")], [])


def test_empty_key_raises_plain_language():
    provider = AnthropicProvider(api_key_getter=lambda: "")
    with pytest.raises(RuntimeError, match="No API key"):
        provider.send([Message(role="user", content="hi")], [])


def test_http_error_raises_runtimeerror_without_leaking_key():
    payload = {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    provider, _, _ = _make(payload, status_code=401, key="sk-super-secret")
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sk-super-secret" not in str(excinfo.value)


def test_network_error_raises_runtimeerror_without_leaking_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(api_key_getter=lambda: "sk-net-secret", client=client)
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sk-net-secret" not in str(excinfo.value)
