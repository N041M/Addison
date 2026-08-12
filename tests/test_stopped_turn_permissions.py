"""THE CARD DIES WITH ITS TURN — KNOWN-BUGS #4, owner decision 2026-08-09.

An approval card used to outlive the turn that raised it. The webview's Stop was
purely local (there was no core-side stop at all), so the card stayed on screen
fully actionable and the worker stayed blocked inside ``_ask_once`` — pressing
Allow minutes later ran the tool for a turn the person had ended, in a
conversation that had moved on.

What is enforced here is the CORE half, because that is the half that is the
enforcement: the frontend greying the card out is presentation, and this file is
what makes a hand-edited or merely slow frontend get the same answer an honest
one does. Four properties, in the order they matter:

  1. Stop resolves every pending card as a refusal — the tool never runs;
  2. an approval that arrives after the stop is REFUSED, not applied and not
     silently swallowed;
  3. the stopped turn — which keeps running, there being no mid-step interrupt in
     v1 — cannot raise a SECOND card at somebody who has left;
  4. the stop is turn-scoped: the next turn asks again, exactly as it always did.

House style is tests/test_ipc_server.py: the real server on fake pipes with a
scripted provider, driven by real JSON-RPC frames.
"""

from __future__ import annotations

from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse
from agent_core.rpc.constants import (
    _ANSWER_AFTER_STOP_MESSAGE,
    _ANSWER_NOT_PENDING_MESSAGE,
)
from agent_core.tools.base import Tool
from tests.conftest import _shutdown, _SpyTool, _tool_call_response, build_server


def _spy(tool: Tool | None) -> _SpyTool:
    """The registered tool is always the conftest spy here (test_ipc_server.py's
    narrowing helper — pyright cannot see through the registry's ``Tool``)."""
    assert isinstance(tool, _SpyTool)
    return tool


def _server(tmp_path, responses):
    h = build_server(tmp_path, responses=responses, tool=_SpyTool())
    return h.server, h.reader, h.writer, h.tool, h.thread


def _send(reader, request_id: int, text: str = "go") -> None:
    reader.feed(
        {"jsonrpc": "2.0", "id": request_id,
         "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
    )


def _stop(reader, writer, request_id: int) -> dict:
    reader.feed({"jsonrpc": "2.0", "id": request_id, "method": Method.CONVERSATION_STOP})
    return writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _respond(reader, writer, request_id: int, allow: bool, tool_id: str = "spy_tool") -> dict:
    reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": Method.PERMISSION_RESPOND,
         "params": {"toolId": tool_id, "allow": allow}}
    )
    return writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def test_stop_refuses_the_pending_card_and_the_tool_never_runs(tmp_path):
    """Property 1. The worker is blocked on the card when Stop lands, so Stop has
    to be answered on the READ LOOP — a stop queued behind the turn it is ending
    would arrive after that turn finished, which is to say never."""
    responses = [_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        _send(reader, 1)
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        assert _spy(tool).calls == []

        result = _stop(reader, writer, 2)
        assert result["ok"] is True
        # One card was standing when Stop landed, and the person is told nothing
        # about it — this count exists so a test can see what the flag did.
        assert result["endedRequests"] == 1

        # The turn finishes on its own (the model is told the call was declined).
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert _spy(tool).calls == []
        # Nothing is left waiting: a live waiter after a stop is the bug itself,
        # dressed as bookkeeping.
        assert server._permission_waiters == {}
        tool_messages = [m for m in server.conversation.messages if m.role == "tool"]
        assert tool_messages and "declined" in tool_messages[0].content
    finally:
        _shutdown(reader, thread)


def test_an_allow_arriving_after_the_stop_is_refused(tmp_path):
    """Property 2 — THE ENFORCEMENT. This is the exact bug: the person pressed
    Allow on a card whose turn was dead. It is refused with a plain sentence, and
    the tool stays unrun."""
    responses = [_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        _send(reader, 1)
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        _stop(reader, writer, 2)
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        result = _respond(reader, writer, 3, allow=True)
        assert result["ok"] is False
        assert result["error"] == _ANSWER_AFTER_STOP_MESSAGE
        assert _spy(tool).calls == []
        assert server._permission_waiters == {}
    finally:
        _shutdown(reader, thread)


def test_a_stopped_turn_never_raises_a_second_card(tmp_path):
    """Property 3. The worker keeps going after Stop, so the turn's NEXT tool call
    would put a fresh, fully live card in front of somebody who has already left —
    the same defect one step later. Two tool calls are scripted; only the first
    may ever reach the webview."""
    responses = [
        _tool_call_response(),
        _tool_call_response(),
        ModelResponse(text="Okay.", tool_calls=[]),
    ]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        _send(reader, 1)
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        _stop(reader, writer, 2)
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        cards = [f for f in writer.frames if f.get("method") == Method.PERMISSION_REQUEST_GRANT]
        assert len(cards) == 1
        assert _spy(tool).calls == []
        assert server._permission_waiters == {}
    finally:
        _shutdown(reader, thread)


def test_the_stop_is_turn_scoped_and_the_next_turn_asks_again(tmp_path):
    """Property 4. A stop must not become a session-long mute: the flag is lowered
    when the worker takes its next job, so the next message is asked about exactly
    as it always was — and answering it still works."""
    responses = [
        _tool_call_response(),
        ModelResponse(text="Okay.", tool_calls=[]),
        _tool_call_response(),
        ModelResponse(text="Done.", tool_calls=[]),
    ]
    server, reader, writer, tool, thread = _server(tmp_path, responses)
    try:
        _send(reader, 1)
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        _stop(reader, writer, 2)
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        _send(reader, 3, "again")
        writer.wait_for(
            lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT
            and len([g for g in writer.frames
                     if g.get("method") == Method.PERMISSION_REQUEST_GRANT]) == 2
        )
        result = _respond(reader, writer, 4, allow=True)
        assert result["ok"] is True
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        assert _spy(tool).calls == [{}]
    finally:
        _shutdown(reader, thread)


def test_an_answer_with_nothing_pending_says_so_without_blaming_a_stop(tmp_path):
    """The other way a waiter can be missing — a double press, a duplicated frame.
    Nothing went wrong, so the sentence must not invent a stop that never
    happened. It used to answer ``{"ok": true}`` to both, which is how an approval
    that authorised nothing came to look like one that did."""
    responses = [ModelResponse(text="Hello.", tool_calls=[])]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        result = _respond(reader, writer, 1, allow=True)
        assert result["ok"] is False
        assert result["error"] == _ANSWER_NOT_PENDING_MESSAGE
        assert server._permission_waiters == {}
    finally:
        _shutdown(reader, thread)


def test_stopping_with_no_card_open_is_harmless(tmp_path):
    """Stop while Addison is merely thinking — the ordinary case. Nothing to end,
    and the turn still lands: this method ends a turn's CONSENT, never its work."""
    responses = [ModelResponse(text="Hello there.", tool_calls=[])]
    server, reader, writer, _, thread = _server(tmp_path, responses)
    try:
        _send(reader, 1)
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        result = _stop(reader, writer, 2)
        assert result == {"ok": True, "endedRequests": 0}
        assert server._permission_waiters == {}
    finally:
        _shutdown(reader, thread)
