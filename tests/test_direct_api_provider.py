"""DirectAPIProvider — BYOK dispatch to the underlying adapter (§4.1, §4.6).

Covers the one built path (Anthropic) round-tripping through the inner
AnthropicProvider adapter, the per-request key fetch (never cached), and the
plain-language error for any not-yet-built provider name (spec §10).

The HTTP boundary is faked with ``httpx.MockTransport`` — no real network, the
same technique the AnthropicProvider tests use.
"""

import json

import httpx
import pytest

from agent_core.providers.base import Message
from agent_core.providers.direct_api_provider import DirectAPIProvider


class _StubTool:
    """Duck-typed ToolDefinition (providers/ must not import tools/)."""

    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


def _make(provider_name, response_payload, *, status_code=200, key="sk-byok"):
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
    provider = DirectAPIProvider(
        provider_name=provider_name, model="claude-opus-4-8", api_key_getter=getter, client=client
    )
    return provider, captured, calls


def test_anthropic_dispatch_round_trip():
    provider, captured, _ = _make(
        "anthropic",
        {
            "content": [
                {"type": "text", "text": "Hi there."},
                {"type": "tool_use", "id": "tu_1", "name": "calculator", "input": {"expression": "1+1"}},
            ],
            "stop_reason": "tool_use",
        },
    )
    tool = _StubTool("calculator", "Do math", {"type": "object", "properties": {}})
    resp = provider.send([Message(role="user", content="what is 1+1")], [tool])

    # The inner AnthropicProvider translated the request and the response.
    assert captured["body"]["model"] == "claude-opus-4-8"
    assert captured["body"]["tools"][0]["name"] == "calculator"
    assert resp.text == "Hi there."
    assert resp.finish_reason == "tool_use"
    assert (resp.tool_calls[0].tool_id, resp.tool_calls[0].args) == (
        "calculator",
        {"expression": "1+1"},
    )


def test_capabilities_delegates_to_adapter():
    provider, _, _ = _make(
        "anthropic", {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    caps = provider.capabilities()
    assert caps.native_tool_calling is True
    assert caps.vision is True  # comes from the real AnthropicProvider adapter


def test_provider_name_is_case_insensitive():
    provider, captured, _ = _make(
        "Anthropic", {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    provider.send([Message(role="user", content="hi")], [])
    assert captured["headers"]["x-api-key"] == "sk-byok"


def test_key_fetched_per_send_and_not_cached():
    provider, captured, calls = _make(
        "anthropic",
        {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"},
        key="sk-secret-byok",
    )
    provider.send([Message(role="user", content="a")], [])
    provider.send([Message(role="user", content="b")], [])

    assert calls["n"] == 2  # one keychain fetch per send, never cached
    assert captured["headers"]["x-api-key"] == "sk-secret-byok"
    # The wrapper holds only the getter, never key material.
    assert "sk-secret-byok" not in repr(provider.__dict__)


def test_unsupported_provider_send_raises_plain_language():
    provider = DirectAPIProvider("openai", model="gpt-4o", api_key_getter=lambda: "sk-x")
    with pytest.raises(RuntimeError, match="Only Anthropic"):
        provider.send([Message(role="user", content="hi")], [])


def test_unsupported_provider_capabilities_raises_plain_language():
    provider = DirectAPIProvider("google", model="gemini", api_key_getter=lambda: "sk-x")
    with pytest.raises(RuntimeError, match="Only Anthropic"):
        provider.capabilities()
