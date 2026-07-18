"""SetupAssistantProvider + the §4.6 routing handoff.

Provider level (§4.6, §8.4, design-doc §7.5.1): a signed relay request carrying
the device id + signature and the translated history/tools; the at-cap wrap-up
surfaced as plain text, never an exception; the prompt-based fallback tool-call
parser (fenced JSON -> ToolCallRequest, malformed -> plain text); the hard
invariant that NO api-key material exists on the provider; and errors that never
echo the signature.

Server level (§4.6 handoff): with no PRIMARY key the turn resolves to the Setup
Assistant and the model sees the injected system prompt; with a key it resolves
to PRIMARY and does not. Harness style mirrors tests/test_ipc_server.py.

No live network anywhere — the relay HTTP boundary is an ``httpx.MockTransport``
and the shell is a fake bridge.
"""

from __future__ import annotations

import json
import queue
import threading
import time

import httpx
import pytest

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
)
from agent_core.providers.router import ModelRouter
from agent_core.providers.setup_assistant_provider import SetupAssistantProvider
from agent_core.tools.registry import ToolRegistry


# --- provider-level helpers ------------------------------------------------
class _StubTool:
    """Duck-typed ToolDefinition (providers/ must not import tools/)."""

    def __init__(self, id, description, parameters_schema):
        self.id = id
        self.description = description
        self.parameters_schema = parameters_schema


class _FakeBridge:
    """Stand-in for IpcShellBridge: reports the public device id and 'signs'
    (records what it was asked to sign). Holds no key material the core sees."""

    def __init__(self, device_id="dev-123", signature="sig-abc"):
        self._device_id = device_id
        self._signature = signature
        self.signed_payloads: list[dict] = []

    def get_device_key(self):
        return {"deviceId": self._device_id, "publicKey": "pub-xyz"}

    def sign_relay_request(self, payload):
        self.signed_payloads.append(payload)
        return {"signature": self._signature, "deviceId": self._device_id}


def _make(response_payload, *, status_code=200, signature="sig-abc"):
    captured: dict = {}
    bridge = _FakeBridge(signature=signature)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(status_code, json=response_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SetupAssistantProvider(
        shell_bridge=bridge, relay_url="https://relay.test/v1/chat", client=client
    )
    return provider, captured, bridge


# --- provider: request shape, signing, translation -------------------------
def test_request_carries_device_signature_and_translation():
    provider, captured, bridge = _make({"text": "Let's get you set up."})
    tool = _StubTool("web_search", "Search the web", {"type": "object", "properties": {}})
    messages = [
        Message(role="system", content="You are Addison's Setup Assistant."),
        Message(role="user", content="hi"),
    ]
    provider.send(messages, [tool])

    body = captured["body"]
    assert body["deviceId"] == "dev-123"
    # System goes to the top-level field (Anthropic-shaped), not the messages list.
    assert body["system"] == "You are Addison's Setup Assistant."
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tools"] == [
        {
            "name": "web_search",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    # Exactly the assembled body was signed, and the headers carry device + sig.
    assert bridge.signed_payloads == [body]
    assert captured["headers"]["x-addison-device"] == "dev-123"
    assert captured["headers"]["x-addison-signature"] == "sig-abc"


def test_capabilities_report_no_native_tool_calling():
    provider, _, _ = _make({"text": "ok"})
    caps = provider.capabilities()
    assert caps.native_tool_calling is False
    assert caps.runs_off_device is False


# --- provider: at-cap wrap-up ----------------------------------------------
def test_at_cap_returns_plain_wrapup_not_exception():
    provider, _, _ = _make(
        {"at_cap": True, "text": "That's the end of free setup — add your key to keep going."}
    )
    resp = provider.send([Message(role="user", content="more")], [])
    assert resp.tool_calls == []
    assert resp.finish_reason == "at_cap"
    assert "add your key" in resp.text


def test_at_cap_without_text_uses_default_wrapup():
    provider, _, _ = _make({"at_cap": True})
    resp = provider.send([Message(role="user", content="x")], [])
    assert resp.finish_reason == "at_cap"
    assert "Settings" in resp.text  # default wrap-up points at adding a key


# --- provider: prompt-based fallback tool-call parser ----------------------
def test_fenced_tool_json_becomes_tool_call():
    fenced = 'Sure.\n```json\n{"tool": "web_search", "args": {"query": "weather"}}\n```'
    provider, _, _ = _make({"text": fenced})
    resp = provider.send([Message(role="user", content="search")], [])
    assert resp.text is None
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.tool_id == "web_search"
    assert tc.args == {"query": "weather"}
    assert tc.id  # a generated id


def test_fenced_without_language_tag_still_parses():
    fenced = '```\n{"tool": "calculator", "args": {"expression": "2+2"}}\n```'
    provider, _, _ = _make({"text": fenced})
    resp = provider.send([Message(role="user", content="x")], [])
    assert resp.tool_calls[0].tool_id == "calculator"
    assert resp.tool_calls[0].args == {"expression": "2+2"}


def test_malformed_fenced_json_stays_plain_text():
    fenced = 'Here:\n```json\n{"tool": "web_search", "args": {oops}}\n```'
    provider, _, _ = _make({"text": fenced})
    resp = provider.send([Message(role="user", content="x")], [])
    assert resp.tool_calls == []
    assert resp.text == fenced  # unchanged; parser never crashes


def test_fenced_non_tool_json_stays_text():
    fenced = '```json\n{"note": "not a tool call"}\n```'
    provider, _, _ = _make({"text": fenced})
    resp = provider.send([Message(role="user", content="x")], [])
    assert resp.tool_calls == []
    assert resp.text == fenced


def test_plain_prose_stays_text():
    provider, _, _ = _make({"text": "I can search the web once you allow that."})
    resp = provider.send([Message(role="user", content="hi")], [])
    assert resp.tool_calls == []
    assert resp.text == "I can search the web once you allow that."


# --- provider: no key material, error hygiene ------------------------------
def test_provider_holds_no_api_key_getter_or_key():
    provider, _, _ = _make({"text": "ok"})
    assert not hasattr(provider, "_api_key_getter")
    # Only the bridge, relay url, and injected client — nothing key-shaped.
    assert set(provider.__dict__) == {"_bridge", "_relay_url", "_client"}
    assert "api_key" not in repr(provider.__dict__).lower()


def test_http_error_raises_plain_runtimeerror_without_signature():
    provider, _, _ = _make({"error": "boom"}, status_code=500, signature="sig-super-secret")
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sig-super-secret" not in str(excinfo.value)


def test_network_error_raises_plain_runtimeerror_without_signature():
    bridge = _FakeBridge(signature="sig-net-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SetupAssistantProvider(
        shell_bridge=bridge, relay_url="https://relay.test/v1/chat", client=client
    )
    with pytest.raises(RuntimeError) as excinfo:
        provider.send([Message(role="user", content="hi")], [])
    assert "sig-net-secret" not in str(excinfo.value)


# --- server-level §4.6 routing harness -------------------------------------
class _PipeReader:
    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def feed(self, frame: dict) -> None:
        self._lines.put(json.dumps(frame) + "\n")

    def close(self) -> None:
        self._lines.put("")

    def readline(self) -> str:
        return self._lines.get()


class _FrameWriter:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._cond = threading.Condition()

    def write(self, line: str) -> None:
        frame = json.loads(line)
        with self._cond:
            self.frames.append(frame)
            self._cond.notify_all()

    def flush(self) -> None:
        pass

    def wait_for(self, predicate, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for frame in self.frames:
                    if predicate(frame):
                        return frame
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"expected frame never arrived; got {self.frames}"
                self._cond.wait(remaining)


class _RecordingProvider:
    """Records each replayed history and returns a canned reply."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        self.histories.append(list(messages))
        return self._response


def _routing_server(
    tmp_path,
    *,
    key_available: bool,
    setup_prompt: str = "SETUP-PROMPT",
    primary_prompt: str | None = None,
):
    primary = _RecordingProvider(ModelResponse(text="primary reply", tool_calls=[]))
    setup = _RecordingProvider(ModelResponse(text="setup reply", tool_calls=[]))
    router = ModelRouter(
        configured={ModelRole.PRIMARY: primary, ModelRole.SETUP_ASSISTANT: setup}
    )
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "setup-route.sqlite3"),
        model_router=router,
        primary_key_probe=lambda: key_available,
        setup_prompt=setup_prompt,
        primary_prompt=primary_prompt,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, primary, setup, thread


def _shutdown(reader: _PipeReader, thread: threading.Thread) -> None:
    reader.close()
    thread.join(timeout=5)


def _send(reader, writer, request_id, text):
    reader.feed(
        {"jsonrpc": "2.0", "id": request_id,
         "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
    )
    writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)


def test_no_key_routes_to_setup_assistant_with_system_prompt(tmp_path):
    server, reader, writer, primary, setup, thread = _routing_server(tmp_path, key_available=False)
    try:
        _send(reader, writer, 1, "hi")
        # The Setup Assistant handled the turn; PRIMARY was never called.
        assert len(setup.histories) == 1
        assert primary.histories == []
        history = setup.histories[0]
        # The leading message it saw is the injected setup system prompt.
        assert history[0].role == "system"
        assert history[0].content == "SETUP-PROMPT"
        assert history[1].role == "user" and history[1].content == "hi"
        # The prompt is transient: never persisted, never left in memory, and the
        # stored transcript is just user+assistant (system isn't even a valid role).
        assert all(m.role != "system" for m in server.conversation.messages)
        assert len(server.conversation.messages) == 2
    finally:
        _shutdown(reader, thread)


def test_key_available_routes_to_primary_without_system_prompt(tmp_path):
    server, reader, writer, primary, setup, thread = _routing_server(tmp_path, key_available=True)
    try:
        _send(reader, writer, 1, "hi")
        assert len(primary.histories) == 1
        assert setup.histories == []
        history = primary.histories[0]
        assert all(m.role != "system" for m in history)
        assert history[-1].role == "user" and history[-1].content == "hi"
    finally:
        _shutdown(reader, thread)


def test_primary_turn_gets_app_context_prompt_transiently(tmp_path):
    # 2026-07 manual pass: with no app-context prompt, "save these steps as a
    # routine" typed into chat got an improvised non-answer. Regular turns now
    # carry the app prompt under the same transient rules as the setup prompt.
    server, reader, writer, primary, setup, thread = _routing_server(
        tmp_path, key_available=True, primary_prompt="APP-PROMPT"
    )
    try:
        _send(reader, writer, 1, "hi")
        assert setup.histories == []
        history = primary.histories[0]
        assert history[0].role == "system" and history[0].content == "APP-PROMPT"
        assert history[1].role == "user" and history[1].content == "hi"
        # Transient: gone from memory after the turn, never in the transcript.
        assert all(m.role != "system" for m in server.conversation.messages)
        assert len(server.conversation.messages) == 2
    finally:
        _shutdown(reader, thread)


def test_setup_turn_gets_setup_prompt_not_app_prompt(tmp_path):
    # The two prompts never stack: a no-key turn is the Setup Assistant's, with
    # ONLY the setup prompt, even when an app prompt is configured.
    server, reader, writer, primary, setup, thread = _routing_server(
        tmp_path, key_available=False, primary_prompt="APP-PROMPT"
    )
    try:
        _send(reader, writer, 1, "hi")
        assert primary.histories == []
        history = setup.histories[0]
        systems = [m.content for m in history if m.role == "system"]
        assert systems == ["SETUP-PROMPT"]
    finally:
        _shutdown(reader, thread)


def test_setup_assistant_role_hidden_from_selector(tmp_path):
    server, reader, writer, _, _, thread = _routing_server(tmp_path, key_available=False)
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_AVAILABLE_ROLES})
        resp = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        # SETUP_ASSISTANT is configured (so resolve() finds it) but must not surface
        # in the user-facing model picker (§4.1.1).
        assert "primary" in resp["result"]["roles"]
        assert "setup_assistant" not in resp["result"]["roles"]
    finally:
        _shutdown(reader, thread)
