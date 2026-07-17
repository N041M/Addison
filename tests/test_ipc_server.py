"""JsonRpcServer round-trip tests — engineering-spec §7, §11 step 7.

Runs the real server in-process on fake pipes with a scripted provider (house
style of tests/test_orchestrator.py): frames go in through a blocking reader,
every outgoing frame is captured, and the assertions follow the §7 contract —
streaming, the blocking permission round-trip, Core -> Shell bridge requests,
and plain-language errors. One subprocess smoke test proves the real stdio
entrypoint answers. No network anywhere.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.base import (
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.shell_bridge import IpcShellBridge
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent


class _PipeReader:
    """Blocking readline() fed frame-by-frame from the test."""

    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def feed(self, frame: dict) -> None:
        self._lines.put(json.dumps(frame) + "\n")

    def close(self) -> None:
        self._lines.put("")  # readline() returning "" is EOF for the read loop

    def readline(self) -> str:
        return self._lines.get()


class _FrameWriter:
    """Captures outgoing frames and lets tests block until one matches."""

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


class _ScriptedProvider:
    """Returns canned ModelResponses in order; records replayed histories."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools) -> ModelResponse:
        self.histories.append(list(messages))
        return self._responses.pop(0)


class _SpyTool:
    """LOW-risk tool that records executions; optionally reads the clipboard
    through the shell bridge so tests can drive a Core -> Shell round-trip."""

    definition = ToolDefinition(
        id="spy_tool",
        label="Check something for you",
        description="A test tool.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self, use_bridge: bool = False) -> None:
        self.calls: list[dict] = []
        self._use_bridge = use_bridge

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        if self._use_bridge:
            return ToolResult(success=True, content=context.shell_bridge.read_clipboard())
        return ToolResult(success=True, content="spied")


def _tool_call_response(tool_id: str = "spy_tool") -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[ToolCallRequest(id="call-1", tool_id=tool_id, args={})],
    )


def _server(tmp_path, responses, tool=None, bridge=None):
    registry = ToolRegistry()
    tool = tool or _SpyTool()
    registry.register(tool)
    provider = _ScriptedProvider(responses)
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=lambda: Store(tmp_path / "ipc-test.sqlite3"),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        shell_bridge=bridge,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, tool, thread


def _shutdown(reader: _PipeReader, thread: threading.Thread) -> None:
    reader.close()
    thread.join(timeout=5)


def test_send_message_streams_and_completes(tmp_path):
    responses = [ModelResponse(text="Hello there.", tool_calls=[])]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "hi"}}
        )
        chunk = writer.wait_for(lambda f: f.get("method") == Method.CONVERSATION_STREAM_CHUNK)
        assert chunk["params"]["text"] == "Hello there."
        done = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert done["result"] == {"ok": True}
    finally:
        _shutdown(reader, thread)


def test_tool_turn_blocks_on_permission_then_runs(tmp_path):
    responses = [_tool_call_response(), ModelResponse(text="Done.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "go"}}
        )
        card = writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        assert card["params"]["toolId"] == "spy_tool"
        assert card["params"]["label"] == "Check something for you"
        assert card["params"]["riskTier"] == "low"
        # The turn must be blocked: no completion, and the tool has not run.
        assert tool.calls == []
        assert not any(f.get("id") == 2 for f in writer.frames)

        reader.feed(
            {"jsonrpc": "2.0", "id": 3, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": True}}
        )
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        assert tool.calls == [{}]
        activity = writer.wait_for(lambda f: f.get("method") == Method.TOOL_ACTIVITY_UPDATE)
        assert activity["params"] == {"toolId": "spy_tool", "label": "Check something for you"}
    finally:
        _shutdown(reader, thread)


def test_denied_permission_never_executes_tool(tmp_path):
    responses = [_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 4,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "go"}}
        )
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed(
            {"jsonrpc": "2.0", "id": 5, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": False}}
        )
        writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)
        assert tool.calls == []
        tool_messages = [m for m in server.conversation.messages if m.role == "tool"]
        assert tool_messages and "declined" in tool_messages[0].content
    finally:
        _shutdown(reader, thread)


def test_core_to_shell_bridge_round_trip(tmp_path):
    bridge = IpcShellBridge()
    responses = [_tool_call_response(), ModelResponse(text="Read it.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(
        tmp_path, responses, tool=_SpyTool(use_bridge=True), bridge=bridge
    )
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 6,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "paste"}}
        )
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed(
            {"jsonrpc": "2.0", "id": 7, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": True}}
        )
        # The tool's bridge call surfaces as a Core -> Shell request frame...
        request = writer.wait_for(lambda f: f.get("method") == Method.SHELL_READ_CLIPBOARD)
        assert request["id"] is not None
        # ...and feeding the shell's response resolves it and finishes the turn.
        reader.feed({"jsonrpc": "2.0", "id": request["id"], "result": {"text": "pasted text"}})
        writer.wait_for(lambda f: f.get("id") == 6 and "result" in f)
        tool_messages = [m for m in server.conversation.messages if m.role == "tool"]
        assert tool_messages[0].content == "pasted text"
    finally:
        _shutdown(reader, thread)


def test_unknown_method_and_not_built_methods(tmp_path):
    server, reader, writer, _, thread = _server(tmp_path, [])
    try:
        reader.feed({"jsonrpc": "2.0", "id": 8, "method": "bogus.method"})
        error = writer.wait_for(lambda f: f.get("id") == 8 and "error" in f)
        assert error["error"]["code"] == -32601

        reader.feed({"jsonrpc": "2.0", "id": 9, "method": Method.ROUTINE_LIST})
        error = writer.wait_for(lambda f: f.get("id") == 9 and "error" in f)
        assert error["error"]["message"] == "This isn't built yet."
    finally:
        _shutdown(reader, thread)


def test_available_roles_answers_without_store(tmp_path):
    server, reader, writer, _, thread = _server(tmp_path, [])
    try:
        reader.feed({"jsonrpc": "2.0", "id": 10, "method": Method.MODEL_AVAILABLE_ROLES})
        response = writer.wait_for(lambda f: f.get("id") == 10 and "result" in f)
        assert response["result"] == {"roles": ["primary"], "localModels": []}
    finally:
        _shutdown(reader, thread)


def test_stdio_entrypoint_subprocess_smoke(tmp_path):
    """The real `python -m agent_core.main` answers over real pipes."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_core.main"],
        cwd=_REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "ADDISON_DB_PATH": str(tmp_path / "smoke.sqlite3"),
        },
    )
    watchdog = threading.Timer(15, proc.kill)
    watchdog.start()
    try:
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": Method.MODEL_AVAILABLE_ROLES}) + "\n"
        )
        proc.stdin.flush()
        line = proc.stdout.readline()
        frame = json.loads(line)
        assert frame["id"] == 1
        assert "primary" in frame["result"]["roles"]
    finally:
        watchdog.cancel()
        proc.kill()
        proc.wait(timeout=5)
