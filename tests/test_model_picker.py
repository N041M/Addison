"""Explicit cloud model picker + per-message effort knob (§4.1.1, §6.8).

This is the *cloud* half of the named-model substrate LOCAL already had (item B):
a curated catalog (models_catalog.py), a PRIMARY named pool in the router, and an
"answer style" (effort) threaded to AnthropicProvider. Everything is explicit and
user-made — no auto-routing (that is v2). No network anywhere: the provider's HTTP
boundary is a MockTransport, and the server runs in-process on fake pipes.

Coverage:
  - catalog shape (one default model; opus/sonnet effort levels; haiku has none);
  - ModelRouter PRIMARY pool resolve (explicit pick, unknown-name fallback, default);
  - AnthropicProvider request bodies (adaptive thinking; output_config.effort only
    when the model supports the requested effort);
  - server: availableRoles carries cloudModels; sendMessage/setRole thread modelId +
    effort to the right provider; invalid effort → plain error; ADDISON_MODEL override.
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
from agent_core.models_catalog import (
    default_cloud_model,
    find_cloud_model,
    load_cloud_catalog,
)
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
)
from agent_core.providers.router import ModelRouter
from agent_core.tools.registry import ToolRegistry

_TEXT_RESPONSE = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}


# ===========================================================================
# Catalog shape
# ===========================================================================
def test_catalog_has_one_default_and_plain_labels():
    catalog = load_cloud_catalog()

    # Exactly one default model, and it is the strongest (opus).
    assert sum(1 for m in catalog if m.default) == 1
    assert default_cloud_model(catalog).id == "claude-opus-4-8"

    # Opus and Sonnet: three shared answer styles, exactly one default ("Balanced"),
    # adaptive thinking on.
    for model_id in ("claude-opus-4-8", "claude-sonnet-5"):
        model = find_cloud_model(catalog, model_id)
        assert model.adaptive_thinking is True
        assert [level.id for level in model.effort_levels] == ["low", "high", "xhigh"]
        defaults = [level for level in model.effort_levels if level.default]
        assert [level.id for level in defaults] == ["high"]
        # Plain-language labels, no jargon (CLAUDE.md).
        assert [level.label for level in model.effort_levels] == ["Quick", "Balanced", "Thorough"]


def test_haiku_has_no_effort_control():
    haiku = find_cloud_model(load_cloud_catalog(), "claude-haiku-4-5")
    assert haiku.adaptive_thinking is False
    assert haiku.effort_levels == ()
    assert haiku.supported_effort == ()


def test_catalog_wire_shape_matches_contract():
    catalog = load_cloud_catalog()
    opus = find_cloud_model(catalog, "claude-opus-4-8").to_wire()
    assert opus["id"] == "claude-opus-4-8"
    assert opus["label"] == "Most capable"
    assert opus["default"] is True
    assert {"id": "high", "label": "Balanced", "default": True} in opus["effortLevels"]

    haiku = find_cloud_model(catalog, "claude-haiku-4-5").to_wire()
    # Empty list = the effort control is hidden for that model.
    assert haiku["effortLevels"] == []
    assert haiku["default"] is False


def test_addison_model_override_moves_default_to_catalog_entry():
    catalog = load_cloud_catalog("claude-sonnet-5")
    assert default_cloud_model(catalog).id == "claude-sonnet-5"
    assert sum(1 for m in catalog if m.default) == 1
    # Sonnet keeps its effort levels; opus is no longer the default.
    assert find_cloud_model(catalog, "claude-sonnet-5").supported_effort == ("low", "high", "xhigh")
    assert find_cloud_model(catalog, "claude-opus-4-8").default is False


def test_addison_model_override_appends_bare_entry_for_unknown_model():
    catalog = load_cloud_catalog("claude-future-9")
    bare = find_cloud_model(catalog, "claude-future-9")
    assert bare is not None
    assert bare.default is True
    assert bare.effort_levels == ()          # unknown model: no effort control
    assert sum(1 for m in catalog if m.default) == 1
    # The curated entries are still present, just not default anymore.
    assert find_cloud_model(catalog, "claude-opus-4-8") is not None


def test_addison_model_override_read_from_env(monkeypatch):
    monkeypatch.setenv("ADDISON_MODEL", "claude-haiku-4-5")
    assert default_cloud_model(load_cloud_catalog()).id == "claude-haiku-4-5"


# ===========================================================================
# ModelRouter — PRIMARY named pool
# ===========================================================================
class _RecordingProvider:
    """Records the effort of each send() so tests can prove the pick reached it."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.efforts: list[str | None] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        self.efforts.append(effort)
        return ModelResponse(text=f"from {self.tag}", tool_calls=[])


def _primary_pool_router():
    opus = _RecordingProvider("opus")
    sonnet = _RecordingProvider("sonnet")
    haiku = _RecordingProvider("haiku")
    router = ModelRouter(configured={ModelRole.PRIMARY: opus})
    # Default registered first, so it is also the pool's selected default.
    router.register_primary_model("claude-opus-4-8", opus)
    router.register_primary_model("claude-sonnet-5", sonnet)
    router.register_primary_model("claude-haiku-4-5", haiku)
    return router, {"opus": opus, "sonnet": sonnet, "haiku": haiku}


def test_primary_pool_explicit_pick_default_and_unknown_fallback():
    router, providers = _primary_pool_router()

    # Explicit by-name pick.
    assert router.resolve(ModelRole.PRIMARY, "claude-sonnet-5") is providers["sonnet"]
    assert router.resolve(ModelRole.PRIMARY, "claude-haiku-4-5") is providers["haiku"]

    # No name -> the default primary; role omitted also defaults to PRIMARY.
    assert router.resolve(ModelRole.PRIMARY) is providers["opus"]
    assert router.resolve() is providers["opus"]

    # Unknown name never errors mid-conversation (§4.1.1) — falls back to default.
    assert router.resolve(ModelRole.PRIMARY, "claude-nonexistent") is providers["opus"]

    assert set(router.available_primary_models()) == {
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    }


def test_primary_pool_does_not_disturb_local_resolution():
    # Both pools populated: each role resolves within its own pool.
    router, providers = _primary_pool_router()
    local = _RecordingProvider("local")
    router.register_local_model("llama3:8b", local)
    assert router.resolve(ModelRole.LOCAL) is local
    assert router.resolve(ModelRole.PRIMARY, "claude-sonnet-5") is providers["sonnet"]


# ===========================================================================
# AnthropicProvider — thinking + output_config request bodies
# ===========================================================================
def _anthropic(adaptive_thinking, supported_effort):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_TEXT_RESPONSE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        api_key_getter=lambda: "sk-test",
        client=client,
        adaptive_thinking=adaptive_thinking,
        supported_effort=supported_effort,
    )
    return provider, captured


def test_adaptive_thinking_present_when_enabled_and_effort_sent_when_supported():
    provider, captured = _anthropic(adaptive_thinking=True, supported_effort=("low", "high", "xhigh"))
    provider.send([Message(role="user", content="hi")], [], effort="high")
    body = captured["body"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}


def test_no_effort_omits_output_config_but_keeps_thinking():
    provider, captured = _anthropic(adaptive_thinking=True, supported_effort=("low", "high", "xhigh"))
    provider.send([Message(role="user", content="hi")], [])
    body = captured["body"]
    assert body["thinking"] == {"type": "adaptive"}
    assert "output_config" not in body


def test_unsupported_effort_is_silently_dropped():
    # A model that supports SOME efforts: an effort outside that set is never sent.
    provider, captured = _anthropic(adaptive_thinking=True, supported_effort=("low", "high"))
    provider.send([Message(role="user", content="hi")], [], effort="xhigh")
    assert "output_config" not in captured["body"]


def test_haiku_like_model_sends_neither_thinking_nor_effort():
    # adaptive_thinking off, no supported effort — even a requested effort is dropped.
    provider, captured = _anthropic(adaptive_thinking=False, supported_effort=())
    provider.send([Message(role="user", content="hi")], [], effort="high")
    body = captured["body"]
    assert "thinking" not in body
    assert "output_config" not in body


def test_catalog_entries_drive_provider_bodies_end_to_end():
    # Build a provider per catalog entry exactly as main() does, send with each
    # model's default effort, and check the wire body matches the catalog knobs.
    for entry in load_cloud_catalog():
        provider, captured = _anthropic(entry.adaptive_thinking, entry.supported_effort)
        default_effort = next((lvl.id for lvl in entry.effort_levels if lvl.default), None)
        provider.send([Message(role="user", content="hi")], [], effort=default_effort)
        body = captured["body"]
        assert ("thinking" in body) is entry.adaptive_thinking
        if default_effort is not None:
            assert body["output_config"] == {"effort": default_effort}
        else:
            assert "output_config" not in body


# ===========================================================================
# Server-level — availableRoles cloudModels, and modelId/effort threading
# ===========================================================================
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


def _picker_server(tmp_path):
    """A server wired with the real catalog and a recording provider per cloud model."""
    catalog = load_cloud_catalog()
    providers = {entry.id: _RecordingProvider(entry.id) for entry in catalog}
    default_id = default_cloud_model(catalog).id

    router = ModelRouter(configured={ModelRole.PRIMARY: providers[default_id]})
    router.register_primary_model(default_id, providers[default_id])
    for model_id, provider in providers.items():
        if model_id != default_id:
            router.register_primary_model(model_id, provider)

    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "picker.sqlite3"),
        model_router=router,
        cloud_catalog=catalog,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, providers, thread


def _shutdown(reader: _PipeReader, thread: threading.Thread) -> None:
    reader.close()
    thread.join(timeout=5)


def test_available_roles_carries_cloud_models(tmp_path):
    _server, reader, writer, _providers, thread = _picker_server(tmp_path)
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_AVAILABLE_ROLES})
        result = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        cloud = result["cloudModels"]
        assert [m["id"] for m in cloud] == [
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ]
        assert sum(1 for m in cloud if m["default"]) == 1
        haiku = next(m for m in cloud if m["id"] == "claude-haiku-4-5")
        assert haiku["effortLevels"] == []
    finally:
        _shutdown(reader, thread)


def test_send_message_threads_model_id_and_effort_to_right_provider(tmp_path):
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                "params": {
                    "text": "hi",
                    "role": "primary",
                    "modelId": "claude-sonnet-5",
                    "effort": "xhigh",
                },
            }
        )
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        # Only the picked model was called, and with the requested effort.
        assert providers["claude-sonnet-5"].efforts == ["xhigh"]
        assert providers["claude-opus-4-8"].efforts == []
        assert providers["claude-haiku-4-5"].efforts == []
    finally:
        _shutdown(reader, thread)


def test_set_role_then_send_applies_stashed_model_and_effort(tmp_path):
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE,
                "params": {"role": "primary", "modelId": "claude-opus-4-8", "effort": "low"},
            }
        )
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                "params": {"text": "go"},
            }
        )
        writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)
        assert providers["claude-opus-4-8"].efforts == ["low"]
    finally:
        _shutdown(reader, thread)


def test_invalid_effort_is_plain_error_and_runs_no_turn(tmp_path):
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                # Haiku has no effort control, so any effort is invalid for it.
                "params": {"text": "hi", "role": "primary", "modelId": "claude-haiku-4-5", "effort": "high"},
            }
        )
        error = writer.wait_for(lambda f: f.get("id") == 5 and "error" in f)["error"]
        assert error["message"] == "That answer-style isn't available for this model."
        # No provider ran — the turn was rejected before resolution.
        assert all(p.efforts == [] for p in providers.values())
    finally:
        _shutdown(reader, thread)


def test_invalid_effort_via_set_role_is_plain_error(tmp_path):
    _server, reader, writer, _providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE,
                "params": {"role": "primary", "modelId": "claude-haiku-4-5", "effort": "low"},
            }
        )
        error = writer.wait_for(lambda f: f.get("id") == 6 and "error" in f)["error"]
        assert error["message"] == "That answer-style isn't available for this model."
    finally:
        _shutdown(reader, thread)


def test_unknown_cloud_model_id_is_plain_error(tmp_path):
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                "params": {"text": "hi", "role": "primary", "modelId": "claude-made-up"},
            }
        )
        error = writer.wait_for(lambda f: f.get("id") == 7 and "error" in f)["error"]
        assert error["message"] == "That model option isn't available."
        assert all(p.efforts == [] for p in providers.values())
    finally:
        _shutdown(reader, thread)


def test_default_send_uses_default_model_with_no_effort(tmp_path):
    # A plain message (no modelId/effort) resolves to the default cloud model and
    # sends no effort — the picker's "leave it alone" path.
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                "params": {"text": "hi"},
            }
        )
        writer.wait_for(lambda f: f.get("id") == 8 and "result" in f)
        assert providers["claude-opus-4-8"].efforts == [None]
    finally:
        _shutdown(reader, thread)


@pytest.mark.parametrize("effort", ["low", "high", "xhigh"])
def test_all_supported_efforts_reach_provider(tmp_path, effort):
    _server, reader, writer, providers, thread = _picker_server(tmp_path)
    try:
        reader.feed(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": Method.CONVERSATION_SEND_MESSAGE,
                "params": {"text": "hi", "role": "primary", "modelId": "claude-opus-4-8", "effort": effort},
            }
        )
        writer.wait_for(lambda f: f.get("id") == 9 and "result" in f)
        assert providers["claude-opus-4-8"].efforts == [effort]
    finally:
        _shutdown(reader, thread)
