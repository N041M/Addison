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
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.models_catalog import CloudModel
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

    def send(self, messages, tools, effort=None) -> ModelResponse:
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
        assert done["result"]["ok"] is True
        # The persisted ids ride along so the frontend can anchor rewind.
        assert isinstance(done["result"]["userMessageId"], str)
        assert isinstance(done["result"]["assistantMessageId"], str)
    finally:
        _shutdown(reader, thread)


class _CrashingTool:
    """MEDIUM-shaped tool whose execute() raises like a shell-bridge refusal
    (e.g. Rust's "A file with that name is already there")."""

    definition = ToolDefinition(
        id="crashy_tool",
        label="Save something for you",
        description="A test tool that refuses.",
        risk_tier=RiskTier.LOW,   # LOW so no undo() is required for registration
        parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        raise RuntimeError("A file with that name is already there — please choose another name.")


def test_tool_refusal_fails_the_step_not_the_turn_and_next_turn_is_clean(tmp_path):
    # 2026-07 manual pass: a save-over-existing refusal used to CRASH the turn,
    # leaving an unpaired tool_use that made the provider reject every later
    # request (API 400). Now: the step fails, the turn completes, and the next
    # turn starts from clean, fully-paired history.
    responses = [
        _tool_call_response("crashy_tool"),
        ModelResponse(text="That name is taken — pick another.", tool_calls=[]),
        ModelResponse(text="Second turn works.", tool_calls=[]),
    ]
    registry = ToolRegistry()
    registry.register(_CrashingTool())
    provider = _ScriptedProvider(responses)
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=lambda: Store(tmp_path / "ipc-test.sqlite3"),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "save it"}}
        )
        # Grant the permission card the crashy tool triggers.
        card = writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed(
            {"jsonrpc": "2.0", "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": card["params"]["toolId"], "allow": True}}
        )
        done = writer.wait_for(lambda f: f.get("id") == 1)
        assert done.get("result", {}).get("ok") is True, done.get("error")
        # The model saw the refusal as a failed tool step, in plain language.
        failed_step = next(m for m in provider.histories[1] if m.role == "tool")
        assert "already there" in failed_step.content

        # Turn 2 must run on clean history: every tool message pairs with a call.
        reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "ok, thanks"}}
        )
        done2 = writer.wait_for(lambda f: f.get("id") == 2)
        assert done2.get("result", {}).get("ok") is True, done2.get("error")
    finally:
        _shutdown(reader, thread)


def test_failed_turn_rolls_back_partial_history_and_next_turn_succeeds(tmp_path):
    # A provider blow-up mid-turn must leave no partial exchange in memory —
    # the next turn's history has to be exactly what is persisted.
    class _FlakyProvider(_ScriptedProvider):
        def __init__(self, responses):
            super().__init__(responses)
            self.fail_first = True

        def send(self, messages, tools, effort=None):
            if self.fail_first:
                self.fail_first = False
                self.histories.append(list(messages))
                raise RuntimeError("The Anthropic service had a problem. Please try again in a moment.")
            return super().send(messages, tools, effort)

    provider = _FlakyProvider([ModelResponse(text="Recovered fine.", tool_calls=[])])
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "ipc-test.sqlite3"),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "first"}}
        )
        failed = writer.wait_for(lambda f: f.get("id") == 1)
        assert "error" in failed

        reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "second"}}
        )
        done = writer.wait_for(lambda f: f.get("id") == 2)
        assert done.get("result", {}).get("ok") is True, done.get("error")
        # In-memory alignment held: user "first" persisted, nothing partial kept.
        roles = [m.role for m in server.conversation.messages]
        assert roles == ["user", "user", "assistant"]
        assert len(server._message_ids) == len(server.conversation.messages)
    finally:
        _shutdown(reader, thread)


def test_rewind_with_returned_store_id_truncates_memory_and_store(tmp_path):
    # The frontend anchors rewind on the userMessageId from the sendMessage
    # result (its own display ids mean nothing to the core — that mismatch is
    # why rewind never worked in the desktop app before).
    responses = [
        ModelResponse(text="First answer.", tool_calls=[]),
        ModelResponse(text="Second answer.", tool_calls=[]),
    ]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "one"}}
        )
        first = writer.wait_for(lambda f: f.get("id") == 1)["result"]
        reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "two"}}
        )
        writer.wait_for(lambda f: f.get("id") == 2)
        assert len(server.conversation.messages) == 4

        reader.feed(
            {"jsonrpc": "2.0", "id": 3,
             "method": Method.UNDO_REWIND_CONVERSATION,
             "params": {"toMessageId": first["userMessageId"]}}
        )
        done = writer.wait_for(lambda f: f.get("id") == 3)
        assert done.get("result", {}).get("ok") is True, done.get("error")
        # Edit-and-resend semantics: the anchor leaves history too — its text
        # goes back to the composer, so nothing re-runs until the user sends.
        assert server.conversation.messages == []
        assert server._message_ids == []
        # Store agrees (fresh connection: the server's is bound to its thread).
        with sqlite3.connect(tmp_path / "ipc-test.sqlite3") as conn:
            stored = conn.execute(
                "SELECT content FROM messages ORDER BY rowid"
            ).fetchall()
        assert stored == []
    finally:
        _shutdown(reader, thread)


def test_second_launch_on_same_database_still_chats(tmp_path):
    # Regression (2026-07 manual pass): a second app launch on a persistent DB
    # used to fail every turn (the then-fixed "main" conversation row raised
    # IntegrityError on re-insert). Each launch now starts its own uuid
    # conversation; this guards that a reused database still chats fine.
    for launch in (1, 2):
        responses = [ModelResponse(text=f"Hello from launch {launch}.", tool_calls=[])]
        server, reader, writer, _, thread = _server(tmp_path, responses)
        try:
            reader.feed(
                {"jsonrpc": "2.0", "id": 1,
                 "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "hi"}}
            )
            done = writer.wait_for(lambda f: f.get("id") == 1)
            assert done.get("result", {}).get("ok") is True, done.get("error")
        finally:
            _shutdown(reader, thread)


def test_send_message_short_text_titles_row_verbatim(tmp_path):
    # A short first message becomes the title verbatim — no ellipsis, no trim.
    responses = [ModelResponse(text="Sure.", tool_calls=[])]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    text = "Plan my week"
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_LIST})
        listing = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        (row,) = listing["conversations"]
        assert row["id"] == server.conversation.id
        assert row["title"] == text        # verbatim, under the 60-char cutoff
        assert "…" not in row["title"]
        assert row["messageCount"] == 2     # user + assistant
    finally:
        _shutdown(reader, thread)


def test_send_message_then_list_shows_one_titled_row(tmp_path):
    responses = [ModelResponse(text="Sure.", tool_calls=[])]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    text = "Please help me plan a small garden party for twelve people next weekend"
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_LIST})
        listing = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        (row,) = listing["conversations"]
        assert row["id"] == server.conversation.id
        # Auto-title: the first 60 characters of the first user message.
        assert row["title"] == text[:60] + "…"
        assert row["messageCount"] == 2
    finally:
        _shutdown(reader, thread)


def test_conversation_new_starts_second_row_and_abandoned_new_adds_none(tmp_path):
    responses = [
        ModelResponse(text="First chat.", tool_calls=[]),
        ModelResponse(text="Second chat.", tool_calls=[]),
    ]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "one"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        first_id = server.conversation.id

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_NEW})
        fresh = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert fresh["conversationId"] != first_id

        reader.feed(
            {"jsonrpc": "2.0", "id": 3,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "two"}}
        )
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)

        # A "new" the user abandons (no message ever sent) must add no row:
        # conversation rows are created lazily on the first turn.
        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.CONVERSATION_NEW})
        writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 5, "method": Method.CONVERSATION_LIST})
        listing = writer.wait_for(lambda f: f.get("id") == 5 and "result" in f)["result"]
        assert [c["title"] for c in listing["conversations"]] == ["two", "one"]
    finally:
        _shutdown(reader, thread)


def test_load_restores_history_and_next_turn_replays_it(tmp_path):
    # Chat in A, start B, then load A again: the load response carries A's
    # transcript in order, and the NEXT turn's provider request must begin with
    # that reloaded history — resuming is real, not just a redraw.
    responses = [
        ModelResponse(text="A answer.", tool_calls=[]),
        ModelResponse(text="B answer.", tool_calls=[]),
        ModelResponse(text="A again.", tool_calls=[]),
    ]
    registry = ToolRegistry()
    registry.register(_SpyTool())
    provider = _ScriptedProvider(responses)
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=lambda: Store(tmp_path / "ipc-test.sqlite3"),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "A question"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        conv_a = server.conversation.id

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_NEW})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        reader.feed(
            {"jsonrpc": "2.0", "id": 3,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "B question"}}
        )
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)

        reader.feed(
            {"jsonrpc": "2.0", "id": 4, "method": Method.CONVERSATION_LOAD,
             "params": {"conversationId": conv_a}}
        )
        loaded = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        assert loaded["conversationId"] == conv_a
        assert loaded["title"] == "A question"
        assert [(m["role"], m["content"]) for m in loaded["messages"]] == [
            ("user", "A question"),
            ("assistant", "A answer."),
        ]

        reader.feed(
            {"jsonrpc": "2.0", "id": 5,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "A follow-up"}}
        )
        done = writer.wait_for(lambda f: f.get("id") == 5)
        assert done.get("result", {}).get("ok") is True, done.get("error")
        replayed = [(m.role, m.content) for m in provider.histories[2]]
        assert replayed == [
            ("user", "A question"),
            ("assistant", "A answer."),
            ("user", "A follow-up"),
        ]
    finally:
        _shutdown(reader, thread)


def test_load_missing_conversation_answers_plainly(tmp_path):
    server, reader, writer, _, thread = _server(tmp_path, [])
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_LOAD,
             "params": {"conversationId": "no-such-conversation"}}
        )
        error = writer.wait_for(lambda f: f.get("id") == 1 and "error" in f)
        assert error["error"]["message"] == "Couldn't find that conversation."
    finally:
        _shutdown(reader, thread)


def test_rewind_after_load_truncates_store_and_memory(tmp_path):
    # The load response's message ids must be REAL rewind anchors: loading a
    # conversation rebuilds the id/message alignment, so a rewind to a loaded
    # user id truncates both the store and the in-memory transcript.
    responses = [
        ModelResponse(text="First answer.", tool_calls=[]),
        ModelResponse(text="Second answer.", tool_calls=[]),
    ]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "one"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "two"}}
        )
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        conv_id = server.conversation.id

        reader.feed(
            {"jsonrpc": "2.0", "id": 3, "method": Method.CONVERSATION_LOAD,
             "params": {"conversationId": conv_id}}
        )
        loaded = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        second_user_id = loaded["messages"][2]["id"]
        assert loaded["messages"][2] == {"id": second_user_id, "role": "user", "content": "two"}

        reader.feed(
            {"jsonrpc": "2.0", "id": 4, "method": Method.UNDO_REWIND_CONVERSATION,
             "params": {"toMessageId": second_user_id}}
        )
        done = writer.wait_for(lambda f: f.get("id") == 4)
        assert done.get("result", {}).get("ok") is True, done.get("error")

        # Edit-and-resend semantics: the anchor left too — the first exchange stays.
        assert [(m.role, m.content) for m in server.conversation.messages] == [
            ("user", "one"),
            ("assistant", "First answer."),
        ]
        assert len(server._message_ids) == 2
        with sqlite3.connect(tmp_path / "ipc-test.sqlite3") as conn:
            stored = [r[0] for r in conn.execute(
                "SELECT content FROM messages ORDER BY rowid"
            ).fetchall()]
        assert stored == ["one", "First answer."]
    finally:
        _shutdown(reader, thread)


def test_load_after_tool_turn_skips_tool_rows_and_empty_stubs(tmp_path):
    # insert_message never persists assistant tool_calls, so a reload must keep
    # only user messages and the assistant's final prose — replaying persisted
    # tool rows would send unpaired tool_results and the provider would 400.
    responses = [_tool_call_response(), ModelResponse(text="Done.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "go"}}
        )
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed(
            {"jsonrpc": "2.0", "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": True}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        # The stored transcript has 4 rows (user, empty tool-request stub, tool
        # result, final prose); the reload keeps exactly two of them.
        reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_LOAD,
             "params": {"conversationId": server.conversation.id}}
        )
        loaded = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert [(m["role"], m["content"]) for m in loaded["messages"]] == [
            ("user", "go"),
            ("assistant", "Done."),
        ]
        assert len(server.conversation.messages) == 2
        assert len(server._message_ids) == 2
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


def test_unknown_method_answers_method_not_found(tmp_path):
    server, reader, writer, _, thread = _server(tmp_path, [])
    try:
        reader.feed({"jsonrpc": "2.0", "id": 8, "method": "bogus.method"})
        error = writer.wait_for(lambda f: f.get("id") == 8 and "error" in f)
        assert error["error"]["code"] == -32601

        # Local setup is BUILT at step 10; its pre-flight guard answers plainly
        # when no model is named (the full flow lives in tests/test_local_setup.py).
        reader.feed({"jsonrpc": "2.0", "id": 9, "method": Method.MODEL_START_LOCAL_SETUP})
        error = writer.wait_for(lambda f: f.get("id") == 9 and "error" in f)
        assert error["error"]["message"] == "Choose a model to set up first."
    finally:
        _shutdown(reader, thread)


def test_available_roles_answers_without_store(tmp_path):
    server, reader, writer, _, thread = _server(tmp_path, [])
    try:
        reader.feed({"jsonrpc": "2.0", "id": 10, "method": Method.MODEL_AVAILABLE_ROLES})
        response = writer.wait_for(lambda f: f.get("id") == 10 and "result" in f)
        # availableRoles now also carries the cloud-model menu (§4.1.1, §6.8); this
        # server is built without a catalog, so it is empty. (The populated shape is
        # covered by tests/test_model_picker.py.)
        assert response["result"] == {"roles": ["primary"], "localModels": [], "cloudModels": []}
    finally:
        _shutdown(reader, thread)


def test_routine_propose_confirm_list_run_round_trip(tmp_path):
    """§6.3/§6.4 over IPC: a live tool turn becomes a saved routine, and running
    it reuses the live grant — the permission card appears exactly once."""
    responses = [
        _tool_call_response(),
        ModelResponse(text="Saved your summary.", tool_calls=[]),
    ]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    # Give the tool call a generalizable arg (§6.3 heuristic).
    server.conversation  # (built at construction; provider script drives the call)
    responses[0].tool_calls[0].args = {"filename": "summary.txt"}
    try:
        # Live turn: permission card -> allow -> turn completes.
        reader.feed(
            {"jsonrpc": "2.0", "id": 20,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "save it"}}
        )
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed(
            {"jsonrpc": "2.0", "id": 21, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": True}}
        )
        writer.wait_for(lambda f: f.get("id") == 20 and "result" in f)

        # Propose: plain-language preview, nothing saved yet.
        reader.feed({"jsonrpc": "2.0", "id": 22,
                     "method": Method.ROUTINE_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 22 and "result" in f)["result"]
        assert preview["steps"] == ["1. Check something for you"]
        assert preview["variables"][0]["name"] == "output_filename"
        assert preview["variables"][0]["default"] == "summary.txt"

        # Confirm with a rename -> persisted.
        reader.feed({"jsonrpc": "2.0", "id": 23, "method": Method.ROUTINE_CONFIRM_SAVE,
                     "params": {"name": "Save my summary"}})
        saved = writer.wait_for(lambda f: f.get("id") == 23 and "result" in f)["result"]
        routine_id = saved["routineId"]

        reader.feed({"jsonrpc": "2.0", "id": 24, "method": Method.ROUTINE_LIST})
        listing = writer.wait_for(lambda f: f.get("id") == 24 and "result" in f)["result"]
        assert [r["name"] for r in listing["routines"]] == ["Save my summary"]

        # Run: the live grant carries over (§8.5) — completes with NO second card.
        reader.feed({"jsonrpc": "2.0", "id": 25, "method": Method.ROUTINE_RUN,
                     "params": {"routineId": routine_id, "variables": {}}})
        run = writer.wait_for(lambda f: f.get("id") == 25 and "result" in f)["result"]
        assert run["ok"] and run["status"] == "completed"
        assert len(tool.calls) == 2  # once live, once from the routine
        cards = [f for f in writer.frames
                 if f.get("method") == Method.PERMISSION_REQUEST_GRANT]
        assert len(cards) == 1
    finally:
        _shutdown(reader, thread)


# ===========================================================================
# Multi-provider API keys — provider.list / connect / disconnect (owner
# decision 2026-07-18). Scripted connect + key-probe callables keep it offline.
# ===========================================================================
def _provider_server(tmp_path, connect_fn=None, key_probe=None, catalog=None):
    registry = ToolRegistry()
    provider = _ScriptedProvider([])
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=lambda: Store(tmp_path / "ipc-test.sqlite3"),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        cloud_catalog=catalog or [],
        connect_provider=connect_fn,
        provider_key_probe=key_probe,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, thread


def _no_key_material(writer: _FrameWriter, secret: str) -> None:
    """No captured frame may ever carry key material (invariant §8.3)."""
    blob = json.dumps(writer.frames)
    assert secret not in blob


def test_provider_list_reports_all_four_and_never_leaks_keys(tmp_path):
    # A key-probe that reports anthropic connected (a legacy/migrated key), the rest not.
    server, reader, writer, thread = _provider_server(
        tmp_path, key_probe=lambda pid: pid == "anthropic"
    )
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_LIST})
        res = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        providers = {p["id"]: p for p in res["providers"]}
        assert set(providers) == {"anthropic", "openai", "google", "custom"}
        assert providers["anthropic"]["connected"] is True   # implicit via key probe
        assert providers["openai"]["connected"] is False
        # NON-secret metadata only — no key field anywhere.
        for p in res["providers"]:
            assert set(p) <= {"id", "label", "connected", "addedAt", "baseUrl", "lastCheckOk"}
    finally:
        _shutdown(reader, thread)


def test_provider_connect_success_marks_connected_and_unions_catalog(tmp_path):
    secret = "sk-openai-super-secret"
    connect_calls = {"n": 0}

    def connect_fn(provider_id, base_url):
        connect_calls["n"] += 1
        # A real connect would use ``secret`` from the keychain; the server must never
        # place it in any response frame.
        assert secret  # (the key lives only inside the connect closure in production)
        return [CloudModel(id="gpt-4.1", label="GPT-4.1", description="", provider="openai")]

    server, reader, writer, thread = _provider_server(tmp_path, connect_fn=connect_fn)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_CONNECT,
             "params": {"provider": "openai"}}
        )
        res = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert res == {"ok": True}
        assert connect_calls["n"] == 1

        # provider.list now shows openai connected with an added date.
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.PROVIDER_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        openai_row = next(p for p in listed["providers"] if p["id"] == "openai")
        assert openai_row["connected"] is True
        assert isinstance(openai_row["addedAt"], int)
        assert openai_row["lastCheckOk"] is True

        # availableRoles carries the new provider's model in the union catalog.
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.MODEL_AVAILABLE_ROLES})
        roles = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        ids = {m["id"] for m in roles["cloudModels"]}
        assert "gpt-4.1" in ids
        providers = {m["provider"] for m in roles["cloudModels"]}
        assert "openai" in providers
        _no_key_material(writer, secret)
    finally:
        _shutdown(reader, thread)


def test_provider_connect_failure_reports_plain_error_and_stays_disconnected(tmp_path):
    def connect_fn(provider_id, base_url):
        raise RuntimeError("That key doesn't work. Check it and try again.")

    server, reader, writer, thread = _provider_server(tmp_path, connect_fn=connect_fn)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_CONNECT,
             "params": {"provider": "google"}}
        )
        res = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert res["ok"] is False
        assert res["error"] == "That key doesn't work. Check it and try again."

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.PROVIDER_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        google_row = next(p for p in listed["providers"] if p["id"] == "google")
        assert google_row["connected"] is False
        assert google_row["lastCheckOk"] is False
    finally:
        _shutdown(reader, thread)


def test_provider_connect_custom_requires_valid_http_url(tmp_path):
    called = {"n": 0}

    def connect_fn(provider_id, base_url):
        called["n"] += 1
        return []

    server, reader, writer, thread = _provider_server(tmp_path, connect_fn=connect_fn)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_CONNECT,
             "params": {"provider": "custom", "baseUrl": "ftp://nope"}}
        )
        res = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert res["ok"] is False
        assert "http" in res["error"]
        assert called["n"] == 0  # never even attempted the connect
    finally:
        _shutdown(reader, thread)


def test_provider_disconnect_removes_from_catalog_and_list(tmp_path):
    def connect_fn(provider_id, base_url):
        return [CloudModel(id="gemini-2.5-pro", label="Gemini 2.5 Pro", description="", provider="google")]

    server, reader, writer, thread = _provider_server(tmp_path, connect_fn=connect_fn)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_CONNECT,
             "params": {"provider": "google"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.PROVIDER_DISCONNECT,
             "params": {"provider": "google"}}
        )
        res = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert res == {"ok": True}

        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.MODEL_AVAILABLE_ROLES})
        roles = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        assert all(m["provider"] != "google" for m in roles["cloudModels"])

        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.PROVIDER_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        google_row = next(p for p in listed["providers"] if p["id"] == "google")
        assert google_row["connected"] is False
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
        # availableRoles now probes the shell for a PRIMARY key before deciding whether
        # to fetch the live model list; with no key it returns the built-in fallback.
        # Answer that keychain probe (empty key) so the server doesn't wait out its
        # shell-timeout, then read on to the availableRoles response.
        frame = None
        while frame is None:
            line = proc.stdout.readline()
            assert line, "server closed before answering availableRoles"
            msg = json.loads(line)
            if msg.get("method") == Method.KEYCHAIN_GET_PROVIDER_KEY:
                proc.stdin.write(
                    json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"key": ""}}) + "\n"
                )
                proc.stdin.flush()
            elif msg.get("id") == 1 and "result" in msg:
                frame = msg
        assert "primary" in frame["result"]["roles"]
    finally:
        watchdog.cancel()
        proc.kill()
        proc.wait(timeout=5)
