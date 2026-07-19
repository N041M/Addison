"""OllamaProvider — local-model HTTP translation and QUERIED capabilities (§4.1.2).

Capabilities are read from Ollama's ``/api/show`` metadata (tools / vision), never
assumed; ``runs_off_device`` is True for every local model — the flag privacy-
sensitive routing keys off (base.py). Native tool calls round-trip through
``/api/chat``; a model without native tool support falls back to the SHARED
fenced-JSON parser (the same one SetupAssistantProvider uses). The local-setup
HTTP helpers (reachability, streaming pull, plain-language sizing) are covered
here too.

No real Ollama and no network anywhere — the HTTP boundary is an
``httpx.MockTransport``, the same technique the other provider tests use.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_core.providers.base import Message, ToolCallRequest
from agent_core.providers.ollama_provider import (
    OllamaProvider,
    approx_requirements,
    is_running,
    pull_model,
)
from agent_core.providers.tool_call_parser import parse_tool_call


class _StubTool:
    """Duck-typed ToolDefinition (providers/ must not import tools/)."""

    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


def _client(routes):
    """Build an httpx.Client on a MockTransport that dispatches by URL path.

    ``routes`` maps a path to either ``(status, json_payload)`` or a callable
    ``(request) -> httpx.Response``. Each request body is captured under
    ``captured[path]`` so tests can assert what was actually sent."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        captured[path] = json.loads(request.content) if request.content else None
        route = routes.get(path)
        if route is None:
            return httpx.Response(404, json={"error": f"no route for {path}"})
        if callable(route):
            return route(request)
        status, payload = route
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


# --- capabilities: queried from /api/show ----------------------------------
def test_capabilities_tools_and_vision_present():
    client, captured = _client(
        {
            "/api/show": (
                200,
                {
                    "capabilities": ["completion", "tools", "vision"],
                    "model_info": {"llama.context_length": 131072},
                },
            )
        }
    )
    caps = OllamaProvider("ministral:14b", client=client).capabilities()
    assert caps.native_tool_calling is True
    assert caps.vision is True
    assert caps.runs_off_device is True             # local — the privacy flag
    assert caps.max_context_tokens == 131072        # read from metadata
    # /api/show is queried by model name.
    assert captured["/api/show"] == {"model": "ministral:14b"}


def test_capabilities_tools_and_vision_absent_default_false():
    client, _ = _client({"/api/show": (200, {"capabilities": ["completion"]})})
    caps = OllamaProvider("deepseek:8b", client=client).capabilities()
    assert caps.native_tool_calling is False
    assert caps.vision is False
    assert caps.runs_off_device is True
    assert caps.max_context_tokens == 8_192         # sane default when omitted


def test_capabilities_degrade_gracefully_when_metadata_unavailable():
    # A failing /api/show must NOT crash capabilities() — it yields conservative
    # caps so a turn can still surface a clean error at send() time.
    client, _ = _client({"/api/show": (500, {"error": "boom"})})
    caps = OllamaProvider("whatever", client=client).capabilities()
    assert caps.native_tool_calling is False
    assert caps.vision is False
    assert caps.runs_off_device is True


def test_metadata_cached_after_first_fetch():
    calls = {"n": 0}

    def show(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"capabilities": ["tools"]})

    client, _ = _client({"/api/show": show})
    provider = OllamaProvider("qwen:14b", client=client)
    provider.capabilities()
    provider.capabilities()
    assert calls["n"] == 1  # static per model — fetched once, then cached


# --- send: native tool calling ---------------------------------------------
def test_native_tool_call_round_trip():
    client, captured = _client(
        {
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (
                200,
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "web_search", "arguments": {"query": "weather"}}}
                        ],
                    }
                },
            ),
        }
    )
    tool = _StubTool("web_search", "Search the web", {"type": "object", "properties": {}})
    resp = OllamaProvider("qwen:14b", client=client).send(
        [Message(role="user", content="weather?")], [tool]
    )

    assert resp.finish_reason == "tool_use"
    tc = resp.tool_calls[0]
    assert (tc.tool_id, tc.args) == ("web_search", {"query": "weather"})
    assert tc.id  # a generated id

    chat_body = captured["/api/chat"]
    # Tools were sent in Ollama's function shape — NOT the fallback prompt block.
    assert chat_body["tools"][0]["function"]["name"] == "web_search"
    assert chat_body["stream"] is False
    assert all("Available tools" not in (m.get("content") or "") for m in chat_body["messages"])


def test_native_history_translation_tool_calls_and_results():
    client, captured = _client(
        {
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (200, {"message": {"content": "done"}}),
        }
    )
    messages = [
        Message(role="user", content="calc"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCallRequest(id="c1", tool_id="calculator", args={"expression": "1+1"})],
        ),
        Message(role="tool", content="2", tool_call_id="c1"),
    ]
    OllamaProvider("qwen:14b", client=client).send(messages, [])

    msgs = captured["/api/chat"]["messages"]
    assert msgs[0] == {"role": "user", "content": "calc"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["tool_calls"][0]["function"] == {
        "name": "calculator",
        "arguments": {"expression": "1+1"},
    }
    assert msgs[2] == {"role": "tool", "content": "2"}


# --- send: prompt-based fallback (no native tools) -------------------------
def test_fallback_parser_promotes_fenced_json_and_appends_instructions():
    fenced = '```json\n{"tool": "calculator", "args": {"expression": "2+2"}}\n```'
    client, captured = _client(
        {
            "/api/show": (200, {"capabilities": ["completion"]}),  # no "tools"
            "/api/chat": (200, {"message": {"role": "assistant", "content": fenced}}),
        }
    )
    tool = _StubTool("calculator", "Do math", {"type": "object", "properties": {}})
    messages = [
        Message(role="system", content="You are helpful."),
        Message(role="user", content="2+2?"),
    ]
    resp = OllamaProvider("tiny:1b", client=client).send(messages, [tool])

    tc = resp.tool_calls[0]
    assert (tc.tool_id, tc.args) == ("calculator", {"expression": "2+2"})
    assert tc.id.startswith("ollama-")

    chat_body = captured["/api/chat"]
    assert "tools" not in chat_body  # no native tools passed
    system = chat_body["messages"][0]
    assert system["role"] == "system"
    # The instruction block was appended to the existing system prompt.
    assert "You are helpful." in system["content"]
    assert "Available tools" in system["content"]
    assert "calculator" in system["content"]


def test_fallback_plain_prose_stays_text():
    client, _ = _client(
        {
            "/api/show": (200, {"capabilities": []}),
            "/api/chat": (200, {"message": {"content": "The answer is 4."}}),
        }
    )
    resp = OllamaProvider("tiny:1b", client=client).send(
        [Message(role="user", content="2+2?")], []
    )
    assert resp.tool_calls == []
    assert resp.text == "The answer is 4."


# --- errors ----------------------------------------------------------------
def test_connection_refused_gives_plain_language():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = _client({"/api/show": refuse, "/api/chat": refuse})
    with pytest.raises(RuntimeError) as excinfo:
        OllamaProvider("tiny:1b", client=client).send([Message(role="user", content="hi")], [])
    assert "Ollama isn't running" in str(excinfo.value)


def test_http_error_status_gives_plain_language_no_stack_trace():
    client, _ = _client(
        {
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (500, {"error": "internal"}),
        }
    )
    with pytest.raises(RuntimeError) as excinfo:
        OllamaProvider("qwen:14b", client=client).send([Message(role="user", content="hi")], [])
    assert "internal" not in str(excinfo.value)  # no server internals leak


# --- shared parser: prefix contract preserved for both callers -------------
def test_shared_parser_prefixes_ids_per_caller():
    fenced = '```json\n{"tool": "web_search", "args": {"q": "x"}}\n```'
    assert parse_tool_call(fenced, id_prefix="setup").id.startswith("setup-")
    assert parse_tool_call(fenced, id_prefix="ollama").id.startswith("ollama-")
    # Non-tool prose degrades to None (never raises) — unchanged behavior.
    assert parse_tool_call("just prose", id_prefix="ollama") is None


# --- local-setup HTTP helpers ----------------------------------------------
def test_is_running_true_on_tags_ok_false_on_error():
    ok_client, _ = _client({"/api/tags": (200, {"models": []})})
    assert is_running(client=ok_client) is True

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    down_client, _ = _client({"/api/tags": refuse})
    assert is_running(client=down_client) is False


def test_pull_model_streams_status_objects():
    body = (
        b'{"status":"pulling manifest"}\n'
        b'{"status":"downloading","total":100,"completed":40}\n'
        b'{"status":"success"}\n'
    )

    def stream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client, captured = _client({"/api/pull": stream})
    updates = list(pull_model("llama3:8b", client=client))

    assert updates[0]["status"] == "pulling manifest"
    assert updates[1]["completed"] == 40
    assert updates[-1]["status"] == "success"
    assert captured["/api/pull"] == {"model": "llama3:8b", "stream": True}


def test_approx_requirements_scales_and_has_safe_fallback():
    small = approx_requirements("qwen:0.5b")
    big = approx_requirements("llama3:70b")
    assert big["disk_gb"] > small["disk_gb"]
    assert big["ram_gb"] > small["ram_gb"]
    # A name with no readable size still yields a positive, non-crashing estimate.
    unknown = approx_requirements("some-model")
    assert unknown["disk_gb"] > 0 and unknown["ram_gb"] > 0


def test_response_carries_usage_when_reported():
    client, _ = _client(
        {
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (
                200,
                {"message": {"content": "ok"}, "prompt_eval_count": 30, "eval_count": 12},
            ),
        }
    )
    res = OllamaProvider("m:8b", client=client).send([Message(role="user", content="hi")], [])
    assert res.usage is not None
    assert res.usage.input_tokens == 30
    assert res.usage.output_tokens == 12


def test_response_usage_none_when_counts_absent():
    client, _ = _client(
        {
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (200, {"message": {"content": "ok"}}),
        }
    )
    res = OllamaProvider("m:8b", client=client).send([Message(role="user", content="hi")], [])
    assert res.usage is None
