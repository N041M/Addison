"""GoogleProvider (Gemini) request/response translation and key handling
(multi-provider, owner decision 2026-07-18).

Covers the generateContent mapping (§4.1) and the key invariant (§5 / CLAUDE.md
§8.3): fetched fresh per send(), sent only in the ``x-goog-api-key`` header, never
retained, never leaked into an error. HTTP is faked with ``httpx.MockTransport``.
"""

import json

import httpx
import pytest

from agent_core.providers.base import Message, ToolCallRequest
from agent_core.providers.google_provider import GoogleProvider, list_models


class _StubTool:
    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


def _make(response_payload, *, status_code=200, key="sk-goog"):
    captured: dict = {}
    calls = {"n": 0}

    def getter():
        calls["n"] += 1
        return key

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(status_code, json=response_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=getter, client=client)
    return provider, captured, calls


def test_request_translation_system_tools_and_function_response_pairing():
    provider, captured, _ = _make({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    tool = _StubTool("calculator", "Do math", {"type": "object", "properties": {}})
    messages = [
        Message(role="system", content="You are Addison."),
        Message(role="user", content="what is 1+1"),
        Message(
            role="assistant",
            content="Calculating.",
            tool_calls=[ToolCallRequest(id="tc1", tool_id="calculator", args={"expression": "1+1"})],
        ),
        Message(role="tool", content="2", tool_call_id="tc1"),
    ]
    provider.send(messages, [tool])
    body = captured["body"]

    # System prompt rides on systemInstruction, not in contents.
    assert body["systemInstruction"] == {"parts": [{"text": "You are Addison."}]}
    # Tools -> functionDeclarations.
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {"name": "calculator", "description": "Do math", "parameters": {"type": "object", "properties": {}}}
            ]
        }
    ]
    contents = body["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    # Assistant tool call -> model turn with a functionCall part.
    assert contents[1]["parts"][0] == {"text": "Calculating."}
    assert contents[1]["parts"][1] == {"functionCall": {"name": "calculator", "args": {"expression": "1+1"}}}
    # Tool result -> user turn with a functionResponse named for the function it answers.
    assert contents[2]["parts"][0] == {
        "functionResponse": {"name": "calculator", "response": {"result": "2"}}
    }
    # Key rides only in the header.
    assert captured["headers"]["x-goog-api-key"] == "sk-goog"
    assert captured["url"].endswith("/models/gemini-2.5-pro:generateContent")


def test_consecutive_tool_results_merge_into_one_user_turn():
    provider, captured, _ = _make({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    messages = [
        Message(role="user", content="read both"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCallRequest(id="a", tool_id="read_file", args={"p": "1"}),
                ToolCallRequest(id="b", tool_id="read_file", args={"p": "2"}),
            ],
        ),
        Message(role="tool", content="one", tool_call_id="a"),
        Message(role="tool", content="two", tool_call_id="b"),
    ]
    provider.send(messages, [])
    contents = captured["body"]["contents"]
    # user, model(2 functionCalls), then ONE merged user turn of 2 functionResponses.
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    responses = contents[2]["parts"]
    assert [p["functionResponse"]["name"] for p in responses] == ["read_file", "read_file"]
    assert [p["functionResponse"]["response"]["result"] for p in responses] == ["one", "two"]


def test_response_function_call_yields_tool_call_request():
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Let me check."},
                        {"functionCall": {"name": "calculator", "args": {"expression": "2+2"}}},
                    ]
                }
            }
        ]
    }
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="2+2?")], [])
    assert resp.text == "Let me check."
    assert resp.finish_reason == "tool_use"
    tc = resp.tool_calls[0]
    assert (tc.tool_id, tc.args) == ("calculator", {"expression": "2+2"})
    assert tc.id  # a synthetic id was assigned


def test_response_plain_text_has_no_tool_calls():
    provider, _, _ = _make({"candidates": [{"content": {"parts": [{"text": "Four."}]}}]})
    resp = provider.send([Message(role="user", content="2+2?")], [])
    assert resp.text == "Four."
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_system_and_tools_omitted_when_absent():
    provider, captured, _ = _make({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    provider.send([Message(role="user", content="hi")], [])
    assert "systemInstruction" not in captured["body"]
    assert "tools" not in captured["body"]


def test_key_fetched_per_send_not_retained():
    provider, captured, calls = _make(
        {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}, key="sk-secret-goog"
    )
    provider.send([Message(role="user", content="a")], [])
    provider.send([Message(role="user", content="b")], [])
    assert calls["n"] == 2
    assert captured["headers"]["x-goog-api-key"] == "sk-secret-goog"
    assert "sk-secret-goog" not in repr(provider.__dict__)


def test_missing_key_raises_plain_language():
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "")
    with pytest.raises(RuntimeError, match="No API key"):
        provider.send([Message(role="user", content="hi")], [])


def test_error_mapping_403_is_plain_key_message_and_leaks_no_key():
    provider, _, _ = _make({"error": "denied"}, status_code=403, key="sk-super-secret")
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert str(excinfo.value) == "That key doesn't work. Check it and try again."
    assert "sk-super-secret" not in str(excinfo.value)


def test_error_mapping_429_and_500():
    provider, _, _ = _make({}, status_code=429)
    with pytest.raises(RuntimeError, match="busy"):
        provider.send([Message(role="user", content="hi")], [])
    provider, _, _ = _make({}, status_code=503)
    with pytest.raises(RuntimeError, match="had a problem"):
        provider.send([Message(role="user", content="hi")], [])


def test_network_error_is_plain_and_leaks_no_key():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "sk-net", client=client)
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sk-net" not in str(excinfo.value)
    assert "Couldn't reach Google" in str(excinfo.value)


# --- list_models (connect-time validation) ---------------------------------
def test_list_models_strips_prefix_and_validates_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1beta/models")
        assert request.headers["x-goog-api-key"] == "sk-a"
        return httpx.Response(
            200, json={"models": [{"name": "models/gemini-2.5-pro"}, {"name": "models/gemini-2.5-flash"}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert list_models(lambda: "sk-a", client=client) == ["gemini-2.5-pro", "gemini-2.5-flash"]


def test_list_models_401_is_plain_key_error():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(RuntimeError, match="That key doesn't work"):
        list_models(lambda: "sk-a", client=client)


def test_list_models_missing_key_raises():
    with pytest.raises(RuntimeError, match="No API key"):
        list_models(lambda: "")
