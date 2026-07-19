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
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import httpx

from agent_core.models_catalog import CloudModel
from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse, Usage
from agent_core.shell_bridge import IpcShellBridge
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from tests.conftest import (
    IPC_DB_NAME,
    _FrameWriter,
    _ScriptedProvider,
    _shutdown,
    _SpyTool,
    _tool_call_response,
    build_server,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _server(tmp_path, responses, tool=None, bridge=None):
    h = build_server(tmp_path, responses=responses, tool=tool, bridge=bridge)
    return h.server, h.reader, h.writer, h.tool, h.thread


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
    h = build_server(tmp_path, responses=responses, tool=_CrashingTool())
    reader, writer, provider, thread = h.reader, h.writer, h.provider, h.thread
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
    h = build_server(tmp_path, provider=provider, register_tool=False)
    server, reader, writer, thread = h.server, h.reader, h.writer, h.thread
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
        with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
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
    h = build_server(tmp_path, responses=responses)
    server, reader, writer, provider, thread = (
        h.server, h.reader, h.writer, h.provider, h.thread
    )
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
        with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
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
    h = build_server(
        tmp_path,
        register_tool=False,
        cloud_catalog=catalog or [],
        connect_provider=connect_fn,
        provider_key_probe=key_probe,
    )
    return h.server, h.reader, h.writer, h.thread


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


# ===========================================================================
# Widgets + stats. Widgets are DECLARATIVE specs (a routine Run pill or a
# whitelisted stat display) proposed like routines and saved LOW-risk. stats.get
# reports token totals, per-provider latency, and connection status — NEVER keys.
# ===========================================================================
def _widget_server(tmp_path, responses=None, ollama_status=200, key_probe=None):
    def _ollama_handler(request):  # deterministic — no real Ollama, no network
        return httpx.Response(ollama_status, json={"models": []})

    h = build_server(
        tmp_path,
        responses=responses or [],
        register_tool=False,
        ollama_client=httpx.Client(transport=httpx.MockTransport(_ollama_handler)),
        provider_key_probe=key_probe,
    )
    return h.server, h.reader, h.writer, h.thread


def test_widget_propose_confirm_list_round_trip(tmp_path):
    # A message asking about token usage drafts a stat widget; confirming saves it.
    responses = [ModelResponse(text="You've used a bit.", tool_calls=[])]
    server, reader, writer, thread = _widget_server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "how many tokens have I used this month?"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        # Propose: a plain preview, nothing saved yet.
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert preview["kind"] == "stat"
        assert preview["spec"] == {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}
        assert preview["summary"]

        # widget.list is still empty until the explicit confirm.
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_LIST})
        assert writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]["widgets"] == []

        # Confirm -> saved and listed.
        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        saved = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        assert saved["ok"] is True and isinstance(saved["widgetId"], str)

        reader.feed({"jsonrpc": "2.0", "id": 5, "method": Method.WIDGET_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 5 and "result" in f)["result"]["widgets"]
        assert len(listed) == 1
        assert listed[0]["spec"]["source"] == "tokens_month"
        assert listed[0]["pinned"] is True
    finally:
        _shutdown(reader, thread)


def test_widget_confirm_decline_saves_nothing(tmp_path):
    responses = [ModelResponse(text="ok", tool_calls=[])]
    server, reader, writer, thread = _widget_server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "show my connection status"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": False}})
        res = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        assert res["ok"] is False and res.get("declined") is True
        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.WIDGET_LIST})
        assert writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]["widgets"] == []
    finally:
        _shutdown(reader, thread)


def test_widget_propose_refuses_when_nothing_matches(tmp_path):
    responses = [ModelResponse(text="hello", tool_calls=[])]
    server, reader, writer, thread = _widget_server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "tell me a story about a cat"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        err = writer.wait_for(lambda f: f.get("id") == 2 and "error" in f)
        assert err["error"]["message"] == "I can't make a widget from this yet."
    finally:
        _shutdown(reader, thread)


def test_widget_set_pinned_and_delete(tmp_path):
    responses = [ModelResponse(text="ok", tool_calls=[])]
    server, reader, writer, thread = _widget_server(tmp_path, responses)
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "how fast are my models, latency wise?"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        widget_id = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]["widgetId"]

        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.WIDGET_SET_PINNED,
                     "params": {"id": widget_id, "pinned": False}})
        assert writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]["ok"] is True
        reader.feed({"jsonrpc": "2.0", "id": 5, "method": Method.WIDGET_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 5 and "result" in f)["result"]["widgets"]
        assert listed[0]["pinned"] is False

        reader.feed({"jsonrpc": "2.0", "id": 6, "method": Method.WIDGET_DELETE,
                     "params": {"id": widget_id}})
        assert writer.wait_for(lambda f: f.get("id") == 6 and "result" in f)["result"]["ok"] is True
        reader.feed({"jsonrpc": "2.0", "id": 7, "method": Method.WIDGET_LIST})
        assert writer.wait_for(lambda f: f.get("id") == 7 and "result" in f)["result"]["widgets"] == []
    finally:
        _shutdown(reader, thread)


def test_usage_recorded_after_turn_and_stats_get_shape(tmp_path):
    # A scripted provider that reports usage => a usage_log row => stats.get totals.
    responses = [
        ModelResponse(text="Hello.", tool_calls=[], usage=Usage(input_tokens=100, output_tokens=40))
    ]
    server, reader, writer, thread = _widget_server(
        tmp_path, responses, key_probe=lambda pid: pid == "anthropic"
    )
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hi"}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        # The usage row landed in the store (fresh connection: worker owns its own).
        with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
            rows = conn.execute(
                "SELECT provider, input_tokens, output_tokens, latency_ms FROM usage_log"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "anthropic"
        assert rows[0][1] == 100 and rows[0][2] == 40
        assert isinstance(rows[0][3], int)  # latency recorded

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.STATS_GET})
        stats = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert stats["tokensMonth"] == {"total": 140, "limit": None}
        latency = {r["provider"]: r for r in stats["providerLatency"]}
        assert "anthropic" in latency and isinstance(latency["anthropic"]["ms"], int)

        # Connections: Ollama (probed 200 => running) + connected Anthropic.
        conns = {c["id"]: c for c in stats["connections"]}
        assert conns["ollama"]["status"] == "running"
        assert conns["anthropic"]["status"] == "reachable"
        # NON-secret payload only — no key material, no unexpected fields.
        for c in stats["connections"]:
            assert set(c) == {"id", "label", "status", "detail"}
    finally:
        _shutdown(reader, thread)


def test_stats_get_empty_has_clean_shape(tmp_path):
    # No usage yet: token total 0, no latency rows, Ollama probed as idle (500).
    server, reader, writer, thread = _widget_server(tmp_path, ollama_status=500)
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.STATS_GET})
        stats = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert stats["tokensMonth"] == {"total": 0, "limit": None}
        assert stats["providerLatency"] == []
        conns = {c["id"]: c for c in stats["connections"]}
        # The Connections card always has the Ollama row (core-provided, not stored).
        assert conns["ollama"]["status"] == "idle"
    finally:
        _shutdown(reader, thread)
