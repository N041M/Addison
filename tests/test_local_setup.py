"""Local-model setup flow + item-B threading + the item-A vision gate (§4.1.2, §4.1.1).

Server level (harness style from tests/test_ipc_server.py): ``model.startLocalSetup``
runs its reachability/hardware pre-flight on the read loop and answers via the RPC
response, then pulls/verifies/registers on a background thread emitting
``model.localSetupProgress`` notifications; once verified, ModelRole.LOCAL and the
model appear in ``model.availableRoles``. Ollama-not-running and insufficient-disk
are plain-language refusals. ``model.setRoleForNextMessage``/``conversation.sendMessage``
thread an explicit LOCAL model pick (item B) into ``resolve()``.

Orchestrator level: the vision gate (item A) replaces an image tool result with a
plain switch-model notice when the active model can't see pictures — a warning +
explicit switch, never an automatic model change.

No real Ollama and no network: the Ollama HTTP boundary is an httpx.MockTransport,
disk/RAM checks are monkeypatched, and providers are fakes.
"""

from __future__ import annotations

import json
import queue
import threading
import time

import httpx

from agent_core import main as main_module
from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.protocol import Method
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry


# --- harness (house style: tests/test_ipc_server.py) -----------------------
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
    """Records replayed histories; returns a canned reply. Tagged so LOCAL-pick
    tests can tell instances apart."""

    def __init__(self, tag: str, response: ModelResponse | None = None) -> None:
        self.tag = tag
        self._response = response or ModelResponse(text=f"{tag} reply", tool_calls=[])
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=(self.tag != "cloud"),
        )

    def send(self, messages, tools) -> ModelResponse:
        self.histories.append(list(messages))
        return self._response


def _ollama_client(routes):
    """MockTransport client routing Ollama HTTP by path (see test_ollama_provider)."""

    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, json={"error": "no route"})
        if callable(route):
            return route(request)
        status, payload = route
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _server(tmp_path, *, router=None, ollama_client=None):
    router = router or ModelRouter(configured={ModelRole.PRIMARY: _RecordingProvider("cloud")})
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "local-setup.sqlite3"),
        model_router=router,
        ollama_base_url="http://ollama.test",
        ollama_client=ollama_client,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, thread


def _shutdown(reader, thread) -> None:
    reader.close()
    thread.join(timeout=5)


def _plenty_of_hardware(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_free_disk_bytes", lambda: 500 * main_module._GB)
    monkeypatch.setattr(main_module, "_total_ram_bytes", lambda: 128 * main_module._GB)


# --- startLocalSetup: happy path -------------------------------------------
def test_start_local_setup_pulls_verifies_registers(tmp_path, monkeypatch):
    _plenty_of_hardware(monkeypatch)
    pull_body = (
        b'{"status":"pulling manifest"}\n'
        b'{"status":"downloading","total":100,"completed":50}\n'
        b'{"status":"success"}\n'
    )
    client = _ollama_client(
        {
            "/api/tags": (200, {"models": []}),
            "/api/pull": lambda r: httpx.Response(200, content=pull_body),
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (200, {"message": {"content": "ready"}}),
        }
    )
    server, reader, writer, thread = _server(tmp_path, ollama_client=client)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_START_LOCAL_SETUP,
             "params": {"modelName": "llama3:8b"}}
        )
        ack = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert ack["result"]["started"] is True

        # Progress streams in plain language, ending in a "done" stage.
        downloading = writer.wait_for(
            lambda f: f.get("method") == Method.MODEL_LOCAL_SETUP_PROGRESS
            and f["params"].get("stage") == "downloading"
            and "percent" in f["params"]
        )
        assert downloading["params"]["percent"] == 50
        assert "Downloading the model" in downloading["params"]["message"]
        done = writer.wait_for(
            lambda f: f.get("method") == Method.MODEL_LOCAL_SETUP_PROGRESS
            and f["params"].get("stage") == "done"
        )
        assert done["params"]["percent"] == 100

        # After verify, the model is registered: LOCAL + the model surface.
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.MODEL_AVAILABLE_ROLES})
        roles = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert "local" in roles["roles"]
        assert roles["localModels"] == ["llama3:8b"]
    finally:
        _shutdown(reader, thread)


def test_start_local_setup_missing_model_is_plain_error(tmp_path, monkeypatch):
    _plenty_of_hardware(monkeypatch)
    client = _ollama_client({"/api/tags": (200, {"models": []})})
    server, reader, writer, thread = _server(tmp_path, ollama_client=client)
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_START_LOCAL_SETUP})
        error = writer.wait_for(lambda f: f.get("id") == 1 and "error" in f)
        assert error["error"]["message"] == "Choose a model to set up first."
    finally:
        _shutdown(reader, thread)


# --- startLocalSetup: Ollama not running -----------------------------------
def test_start_local_setup_ollama_not_running_is_plain_error(tmp_path, monkeypatch):
    _plenty_of_hardware(monkeypatch)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _ollama_client({"/api/tags": refuse})
    server, reader, writer, thread = _server(tmp_path, ollama_client=client)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_START_LOCAL_SETUP,
             "params": {"modelName": "llama3:8b"}}
        )
        error = writer.wait_for(lambda f: f.get("id") == 1 and "error" in f)
        message = error["error"]["message"]
        assert "Ollama isn't running" in message
        assert "ollama.com" in message  # points the user at installing it themselves
        # Nothing was registered.
        assert server.model_router.available_local_models() == []
    finally:
        _shutdown(reader, thread)


# --- startLocalSetup: insufficient disk ------------------------------------
def test_start_local_setup_insufficient_disk_is_plain_refusal(tmp_path, monkeypatch):
    # Ollama is up, but only 1 GB free vs. a 70B model's ~52 GB need.
    monkeypatch.setattr(main_module, "_free_disk_bytes", lambda: 1 * main_module._GB)
    monkeypatch.setattr(main_module, "_total_ram_bytes", lambda: 128 * main_module._GB)
    client = _ollama_client({"/api/tags": (200, {"models": []})})
    server, reader, writer, thread = _server(tmp_path, ollama_client=client)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_START_LOCAL_SETUP,
             "params": {"modelName": "llama3:70b"}}
        )
        error = writer.wait_for(lambda f: f.get("id") == 1 and "error" in f)
        message = error["error"]["message"]
        assert "GB of free space" in message  # names real GB, not parameter counts
        assert server.model_router.available_local_models() == []
    finally:
        _shutdown(reader, thread)


def test_start_local_setup_busy_rejects_second_request(tmp_path, monkeypatch):
    _plenty_of_hardware(monkeypatch)
    # A pull that blocks until released keeps the first setup "active".
    release = threading.Event()

    def slow_pull(request: httpx.Request) -> httpx.Response:
        release.wait(timeout=5)
        return httpx.Response(200, content=b'{"status":"success"}\n')

    client = _ollama_client(
        {
            "/api/tags": (200, {"models": []}),
            "/api/pull": slow_pull,
            "/api/show": (200, {"capabilities": ["tools"]}),
            "/api/chat": (200, {"message": {"content": "ready"}}),
        }
    )
    server, reader, writer, thread = _server(tmp_path, ollama_client=client)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_START_LOCAL_SETUP,
             "params": {"modelName": "llama3:8b"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        # Second request while the first is mid-pull -> plain "already" refusal.
        reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.MODEL_START_LOCAL_SETUP,
             "params": {"modelName": "qwen:8b"}}
        )
        error = writer.wait_for(lambda f: f.get("id") == 2 and "error" in f)
        assert "already setting up" in error["error"]["message"]
    finally:
        release.set()
        _shutdown(reader, thread)


# --- item B: explicit LOCAL model pick threads into resolve() --------------
def _router_with_two_local():
    return ModelRouter(
        configured={ModelRole.PRIMARY: _RecordingProvider("cloud")},
        local_models={"m-a": _RecordingProvider("m-a"), "m-b": _RecordingProvider("m-b")},
    )


def test_set_role_with_model_id_then_send_resolves_that_model(tmp_path):
    router = _router_with_two_local()
    server, reader, writer, thread = _server(tmp_path, router=router)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE,
             "params": {"role": "local", "modelId": "m-b"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hi"}}
        )
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)

        # Only the explicitly-picked local model handled the turn.
        assert len(router._local_models["m-b"].histories) == 1
        assert router._local_models["m-a"].histories == []
        assert router._configured[ModelRole.PRIMARY].histories == []
    finally:
        _shutdown(reader, thread)


def test_send_message_model_id_param_threads_to_resolve(tmp_path):
    router = _router_with_two_local()
    server, reader, writer, thread = _server(tmp_path, router=router)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hi", "role": "local", "modelId": "m-a"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert len(router._local_models["m-a"].histories) == 1
        assert router._local_models["m-b"].histories == []
    finally:
        _shutdown(reader, thread)


def test_set_role_unknown_local_model_is_plain_error(tmp_path):
    router = _router_with_two_local()
    server, reader, writer, thread = _server(tmp_path, router=router)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_SET_ROLE_FOR_NEXT_MESSAGE,
             "params": {"role": "local", "modelId": "ghost"}}
        )
        error = writer.wait_for(lambda f: f.get("id") == 1 and "error" in f)
        assert error["error"]["message"] == "That model option isn't available."
    finally:
        _shutdown(reader, thread)


# --- item A: the vision gate (orchestrator) --------------------------------
class _ImageTool:
    """LOW-risk tool returning an image result (as the shell reports it)."""

    definition = ToolDefinition(
        id="read_file",
        label="Read files you choose",
        description="Reads a file you pick.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content={"content": "<image-bytes>", "kind": "image"})


class _ImageThenTextProvider:
    def __init__(self, vision: bool) -> None:
        self._vision = vision
        self._responses = [
            ModelResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="call-1", tool_id="read_file", args={})],
            ),
            ModelResponse(text="all done", tool_calls=[]),
        ]

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=True,
            vision=self._vision,
        )

    def send(self, messages, tools) -> ModelResponse:
        return self._responses.pop(0)


def _orchestrator_for(provider):
    registry = ToolRegistry()
    registry.register(_ImageTool())
    gate = PermissionGate()
    gate.grant("read_file")
    streamed: list = []

    class _Store:
        def insert_action_snapshot(self, snapshot):  # pragma: no cover - unused
            pass

    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_Store(), tool_registry=registry),
        stream_to_frontend=streamed.append,
    )
    return orchestrator, streamed


def test_vision_gate_blocks_image_for_text_only_model():
    orchestrator, streamed = _orchestrator_for(_ImageThenTextProvider(vision=False))
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="look at this"))
    orchestrator.run_turn(conv)

    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert "can't look at pictures" in tool_msg.content
    assert "<image-bytes>" not in tool_msg.content        # raw image never fed onward
    assert any("can't look at pictures" in s for s in streamed)  # surfaced to the user


def test_vision_capable_model_passes_image_through():
    orchestrator, _ = _orchestrator_for(_ImageThenTextProvider(vision=True))
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="look at this"))
    orchestrator.run_turn(conv)

    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert "<image-bytes>" in tool_msg.content            # image reaches a vision model
    assert "can't look at pictures" not in tool_msg.content
