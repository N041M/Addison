"""OpenAIProvider request/response translation, key handling, and the custom
OpenAI-compatible server path (multi-provider, owner decision 2026-07-18).

Covers the Chat Completions mapping (§4.1) and the hard key invariant (§5 /
CLAUDE.md §8.3): the key is fetched fresh per send(), used only for that request,
never retained, and never leaked into an error. The HTTP boundary is faked with
``httpx.MockTransport`` — the same offline technique the Anthropic tests use.
"""

import json

import httpx
import pytest

from agent_core.providers.base import Message, ToolCallRequest
from agent_core.providers.openai_provider import OpenAIProvider, list_models


class _StubTool:
    """Duck-typed ToolDefinition (providers/ must not import tools/)."""

    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


def _make(response_payload, *, status_code=200, key="sk-test-key", **kwargs):
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
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=getter, client=client, **kwargs)
    return provider, captured, calls


def test_request_translation_tools_and_tool_result_pairing():
    provider, captured, _ = _make({"choices": [{"message": {"content": "ok"}}]})
    tool = _StubTool(
        "read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}}
    )
    messages = [
        Message(role="user", content="read a.txt"),
        Message(
            role="assistant",
            content="Reading it.",
            tool_calls=[ToolCallRequest(id="call_1", tool_id="read_file", args={"path": "a.txt"})],
        ),
        Message(role="tool", content="contents of a", tool_call_id="call_1"),
    ]
    provider.send(messages, [tool])
    body = captured["body"]

    # Tool translation: id -> function.name, parameters_schema -> function.parameters.
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    api = body["messages"]
    assert [m["role"] for m in api] == ["user", "assistant", "tool"]
    # Assistant tool_calls carry the id + JSON-string arguments.
    call = api[1]["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}
    # Tool result keyed by the same tool_call_id.
    assert api[2] == {"role": "tool", "tool_call_id": "call_1", "content": "contents of a"}
    assert captured["url"].endswith("/v1/chat/completions")


def test_response_tool_calls_parse_into_tool_call_requests():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Let me check.",
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'},
                        }
                    ],
                }
            }
        ]
    }
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="2+2?")], [])
    assert resp.text == "Let me check."
    assert resp.finish_reason == "tool_use"
    tc = resp.tool_calls[0]
    assert (tc.id, tc.tool_id, tc.args) == ("call_9", "calculator", {"expression": "2+2"})


def test_response_malformed_arguments_degrade_to_empty_dict():
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "t", "arguments": "not json {"}}
                    ]
                }
            }
        ]
    }
    provider, _, _ = _make(payload)
    resp = provider.send([Message(role="user", content="go")], [])
    assert resp.tool_calls[0].args == {}


def test_response_plain_text_has_no_tool_calls():
    provider, _, _ = _make({"choices": [{"message": {"content": "The answer is 4."}}]})
    resp = provider.send([Message(role="user", content="2+2?")], [])
    assert resp.text == "The answer is 4."
    assert resp.tool_calls == []
    assert resp.finish_reason == "stop"


def test_tools_key_omitted_when_no_tools():
    provider, captured, _ = _make({"choices": [{"message": {"content": "hi"}}]})
    provider.send([Message(role="user", content="hello")], [])
    assert "tools" not in captured["body"]
    assert captured["body"]["model"] == "gpt-4.1"


def test_key_fetched_per_send_not_retained_and_sent_as_bearer():
    provider, captured, calls = _make(
        {"choices": [{"message": {"content": "ok"}}]}, key="sk-secret-xyz"
    )
    provider.send([Message(role="user", content="a")], [])
    provider.send([Message(role="user", content="b")], [])
    assert calls["n"] == 2
    assert captured["headers"]["authorization"] == "Bearer sk-secret-xyz"
    assert "sk-secret-xyz" not in repr(provider.__dict__)


def test_missing_key_raises_plain_language():
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "")
    with pytest.raises(RuntimeError, match="No API key"):
        provider.send([Message(role="user", content="hi")], [])


def test_clipboard_whitespace_stripped_from_key():
    provider, captured, _ = _make(
        {"choices": [{"message": {"content": "ok"}}]}, key="  sk-pasted\n"
    )
    provider.send([Message(role="user", content="hi")], [])
    assert captured["headers"]["authorization"] == "Bearer sk-pasted"


def test_non_ascii_key_raises_without_leaking_key():
    provider, _, _ = _make({}, key="sk-truncated…")
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "Settings" in str(excinfo.value)
    assert "sk-truncated" not in str(excinfo.value)


def test_error_mapping_401_is_plain_key_message():
    provider, _, _ = _make({"error": "bad key"}, status_code=401, key="sk-super-secret")
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert str(excinfo.value) == "That key doesn't work. Check it and try again."
    assert "sk-super-secret" not in str(excinfo.value)


def test_error_mapping_429_and_500():
    provider, _, _ = _make({}, status_code=429)
    with pytest.raises(RuntimeError, match="busy"):
        provider.send([Message(role="user", content="hi")], [])
    provider, _, _ = _make({}, status_code=500)
    with pytest.raises(RuntimeError, match="had a problem"):
        provider.send([Message(role="user", content="hi")], [])


def test_network_error_is_plain_and_leaks_no_key():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "sk-net", client=client)
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sk-net" not in str(excinfo.value)
    assert "Couldn't reach OpenAI" in str(excinfo.value)


# --- custom OpenAI-compatible server (base_url override + optional key) -----
def test_custom_base_url_and_no_key_omits_authorization():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["has_auth"] = "authorization" in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(
        model="local-model",
        api_key_getter=lambda: "",  # keyless custom server
        base_url="http://192.168.1.9:1234/v1",
        client=client,
        require_key=False,
        service_label="the server",
    )
    provider.send([Message(role="user", content="hi")], [])
    assert captured["url"] == "http://192.168.1.9:1234/v1/chat/completions"
    assert captured["has_auth"] is False


def test_custom_server_with_key_still_sends_authorization():
    provider, captured, _ = _make(
        {"choices": [{"message": {"content": "ok"}}]},
        key="sk-local",
        base_url="http://localhost:1234/v1",
        require_key=False,
    )
    provider.send([Message(role="user", content="hi")], [])
    assert captured["headers"]["authorization"] == "Bearer sk-local"


# --- list_models (connect-time validation / custom catalog) -----------------
def test_list_models_returns_ids_and_validates_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/models")
        assert request.headers["authorization"] == "Bearer sk-a"
        return httpx.Response(200, json={"data": [{"id": "m-one"}, {"id": "m-two"}, {"bad": 1}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert list_models("https://api.openai.com/v1", lambda: "sk-a", client=client) == [
        "m-one",
        "m-two",
    ]


def test_list_models_401_is_plain_key_error():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    )
    with pytest.raises(RuntimeError, match="That key doesn't work"):
        list_models("https://api.openai.com/v1", lambda: "sk-a", client=client)


def test_list_models_no_key_allowed_for_custom_server():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "local-1"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert list_models("http://localhost:1234/v1", lambda: "", client=client, require_key=False) == [
        "local-1"
    ]


def test_list_models_network_error_is_plain():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    )
    with pytest.raises(RuntimeError, match="Couldn't reach that server"):
        list_models("http://localhost:9/v1", lambda: "", client=client, require_key=False)


def test_response_carries_usage_when_reported():
    provider, _, _ = _make(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29},
        }
    )
    res = provider.send([Message(role="user", content="hi")], [])
    assert res.usage is not None
    assert res.usage.input_tokens == 20
    assert res.usage.output_tokens == 9


def test_response_usage_none_when_absent():
    provider, _, _ = _make({"choices": [{"message": {"content": "ok"}}]})
    assert provider.send([Message(role="user", content="hi")], []).usage is None
