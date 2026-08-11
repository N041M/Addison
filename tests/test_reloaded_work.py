"""A reopened chat keeps its work — KNOWN-BUGS #5.

THE DEFECT. "Addison's work" listed the steps of the turn that had just run, and
under it sat "Save as routine". Both were live-only: ``insert_message`` persisted
role and content and dropped ``Message.tool_calls`` on the floor, so quitting and
reopening the chat left a transcript with no record that anything had been DONE.
The panel did not come back, and — worse, because it is silent — the steps became
unsaveable: ``RoutineBuilder.propose_from_recent_actions`` reads the tool calls of
the recent messages, and there were none to read.

THE FIX, in one sentence: an assistant turn's tool calls are written down
(``messages.tool_calls_json``), and ``conversation.load`` puts them back where each
consumer needs them — the panel's steps on the wire as ``work``, the calls
themselves on ``Message.past_tool_calls``, which the builder reads and no provider
does.

That last split is the thing to break these tests over. A persisted ``tool_use``
replayed to a model without the ``tool_result`` that answered it makes the provider
reject every later request of the session (§4.4), which is why the reload has
always dropped tool rows — so the third test here sends a real message after a
reload and looks at what the provider was actually handed.

House style of tests/test_ipc_server.py: the real server on fake pipes, a scripted
provider, frames in and frames out. The relaunch is a genuine second server over
the same database file, because "quit and reopen" is the repro.
"""

from __future__ import annotations

import sqlite3

from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse
from tests.conftest import (
    IPC_DB_NAME,
    _ScriptedProvider,
    _shutdown,
    _SpyTool,
    _tool_call_response,
    build_server,
)


def _run_tool_turn(reader, writer, *, allow: bool, text: str = "go") -> None:
    """One turn that asks for the spy tool, answered at the permission card."""
    reader.feed(
        {"jsonrpc": "2.0", "id": 1,
         "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
    )
    writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
    reader.feed(
        {"jsonrpc": "2.0", "method": Method.PERMISSION_RESPOND,
         "params": {"toolId": "spy_tool", "allow": allow}}
    )
    writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)


def _load(reader, writer, conversation_id: str, *, frame_id: int = 9) -> dict:
    reader.feed(
        {"jsonrpc": "2.0", "id": frame_id, "method": Method.CONVERSATION_LOAD,
         "params": {"conversationId": conversation_id}}
    )
    return writer.wait_for(lambda f: f.get("id") == frame_id and "result" in f)["result"]


def test_relaunch_redraws_the_work_panel_and_still_saves_a_routine(tmp_path):
    """The whole bug, end to end: quit, relaunch, reopen — the steps are still
    there and can still become a routine."""
    first = build_server(
        tmp_path,
        responses=[_tool_call_response(), ModelResponse(text="Done.", tool_calls=[])],
    )
    try:
        _run_tool_turn(first.reader, first.writer, allow=True)
        conversation_id = first.server.conversation.id
    finally:
        _shutdown(first.reader, first.thread)

    # A SECOND server over the same database — nothing survives in memory, which
    # is exactly what made the old behaviour look like data loss to the person.
    second = build_server(tmp_path, responses=[])
    try:
        loaded = _load(second.reader, second.writer, conversation_id)
        # The panel's line, in the same {toolId, label, detail?} shape a live
        # tool.activityUpdate carries. The label comes from the registry, so it is
        # the tool's own words and not its id.
        assert loaded["work"] == [
            {"toolId": "spy_tool", "label": "Check something for you"}
        ]
        # The transcript filter is unchanged: prose only, no tool rows, no stubs.
        assert [(m["role"], m["content"]) for m in loaded["messages"]] == [
            ("user", "go"),
            ("assistant", "Done."),
        ]

        # And the link under the panel does what it says. This is the assertion
        # the bug was about: before the fix this answered "I couldn't find any
        # actions to turn into a routine in our recent chat."
        second.reader.feed(
            {"jsonrpc": "2.0", "id": 10,
             "method": Method.ROUTINE_PROPOSE_FROM_CONVERSATION}
        )
        proposal = second.writer.wait_for(
            lambda f: f.get("id") == 10 and ("result" in f or "error" in f)
        )
        assert "error" not in proposal, proposal.get("error")
        assert proposal["result"]["steps"] == ["1. Check something for you"]
    finally:
        _shutdown(second.reader, second.thread)


def test_a_declined_step_is_recorded_but_never_redrawn_as_work(tmp_path):
    """"Addison's work" is a record of what Addison DID.

    A call the person refused is still written down — the routine builder sees the
    same set live and after a reload, and changing that set only for reopened chats
    would make one surface disagree with the other — but it is marked not-run and
    never becomes a panel line. Nothing that did not happen may be redrawn as
    something that did.
    """
    first = build_server(
        tmp_path,
        responses=[_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])],
    )
    try:
        _run_tool_turn(first.reader, first.writer, allow=False)
        conversation_id = first.server.conversation.id
        assert isinstance(first.tool, _SpyTool)
        assert first.tool.calls == []  # it really never ran
    finally:
        _shutdown(first.reader, first.thread)

    # The row exists and says so, in the column itself.
    with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
        stored = [
            row[0]
            for row in conn.execute(
                "SELECT tool_calls_json FROM messages WHERE tool_calls_json IS NOT NULL"
            )
        ]
    assert len(stored) == 1 and '"ran": false' in stored[0]

    second = build_server(tmp_path, responses=[])
    try:
        loaded = _load(second.reader, second.writer, conversation_id)
        # No steps: the key is absent rather than an empty list, which is what the
        # frontend already reads as "no panel".
        assert "work" not in loaded
        # ...but the call is still there for the builder, exactly as it is live.
        second.reader.feed(
            {"jsonrpc": "2.0", "id": 10,
             "method": Method.ROUTINE_PROPOSE_FROM_CONVERSATION}
        )
        proposal = second.writer.wait_for(
            lambda f: f.get("id") == 10 and ("result" in f or "error" in f)
        )
        assert "error" not in proposal, proposal.get("error")
        assert proposal["result"]["steps"] == ["1. Check something for you"]
    finally:
        _shutdown(second.reader, second.thread)


def test_reloaded_tool_calls_never_reach_a_provider(tmp_path):
    """The reason the calls ride on a SECOND field.

    ``past_tool_calls`` exists so that restoring history cannot put an unpaired
    ``tool_use`` into a request: the tool_result that answered it is not replayed
    (the reload drops tool rows), and a provider that receives one without the other
    rejects every later request of the session. This sends a real message after a
    reload and inspects the history the provider was handed.
    """
    first = build_server(
        tmp_path,
        responses=[_tool_call_response(), ModelResponse(text="Done.", tool_calls=[])],
    )
    try:
        _run_tool_turn(first.reader, first.writer, allow=True)
        conversation_id = first.server.conversation.id
    finally:
        _shutdown(first.reader, first.thread)

    second = build_server(
        tmp_path, responses=[ModelResponse(text="Second answer.", tool_calls=[])]
    )
    try:
        _load(second.reader, second.writer, conversation_id)
        # The in-memory transcript carries the restored calls...
        restored = [
            m for m in second.server.conversation.messages if m.past_tool_calls
        ]
        assert [c.tool_id for m in restored for c in m.past_tool_calls] == ["spy_tool"]
        assert all(m.tool_calls == [] for m in second.server.conversation.messages)

        second.reader.feed(
            {"jsonrpc": "2.0", "id": 2,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "again"}}
        )
        done = second.writer.wait_for(lambda f: f.get("id") == 2)
        assert done.get("result", {}).get("ok") is True, done.get("error")

        provider = second.provider
        assert isinstance(provider, _ScriptedProvider)
        sent = provider.histories[0]
        # No tool_use goes out, and no orphan tool row either.
        assert all(not m.tool_calls for m in sent)
        assert [(m.role, m.content) for m in sent if m.role != "system"] == [
            ("user", "go"),
            ("assistant", "Done."),
            ("user", "again"),
        ]
    finally:
        _shutdown(second.reader, second.thread)


def test_a_database_written_before_the_column_existed_still_opens(tmp_path):
    """The upgrade path. ``CREATE TABLE IF NOT EXISTS`` does nothing to a table
    that already exists, so without the ``_add_column_if_missing`` line every
    install that predates this column would raise "no such column" on the first
    message — the schema change has to be a migration, not an edit."""
    db_path = tmp_path / IPC_DB_NAME
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages ("
            "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, "
            "content TEXT NOT NULL, tool_call_id TEXT, created_at INTEGER NOT NULL)"
        )
        conn.commit()

    harness = build_server(tmp_path, responses=[ModelResponse(text="Hi.", tool_calls=[])])
    try:
        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "hello"}}
        )
        done = harness.writer.wait_for(lambda f: f.get("id") == 1)
        assert done.get("result", {}).get("ok") is True, done.get("error")
    finally:
        _shutdown(harness.reader, harness.thread)
