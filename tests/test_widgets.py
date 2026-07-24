"""Widget spec validation — the SAVE-time / RENDER-time gate (agent_core/widgets.py).

A widget is a DECLARATIVE spec, one of exactly two shapes, NEVER code. These
tests pin that: both valid kinds accept; unknown kinds/sources reject; code-
looking ids reject; over-long titles and extra fields reject; the pinned cap is a
constant the server enforces.

The last section leaves the validator and drives its two CALL SITES through the
real server (rpc/widgets.py). Validating in isolation proved the rule; it did not
prove either caller still asks — and each caller's filter was invisible to the
suite, because the other one masked its removal (see the section header).
"""

from __future__ import annotations

import json
import sqlite3
import time

from agent_core.memory.store import Store
from agent_core.policy import PolicyMode
from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse, ToolCallRequest
from agent_core.tools.run_command import RunCommandTool
from agent_core.widgets import (
    MAX_PINNED,
    MAX_TITLE_LEN,
    STAT_SOURCES,
    validate_widget_spec,
    widget_summary,
)
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


def test_valid_routine_widget_accepts():
    spec = {"kind": "routine", "routineId": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed", "title": "Weather note"}
    assert validate_widget_spec(spec) is None


def test_valid_stat_widget_accepts_each_whitelisted_source():
    for source in STAT_SOURCES:
        spec = {"kind": "stat", "source": source, "title": "A stat"}
        assert validate_widget_spec(spec) is None, source


def test_unknown_kind_rejects():
    assert validate_widget_spec({"kind": "agent", "title": "x"}) is not None
    assert validate_widget_spec({"kind": "command", "title": "x", "cmd": "rm -rf /"}) is not None


def test_unknown_stat_source_rejects():
    assert validate_widget_spec({"kind": "stat", "source": "disk_space", "title": "x"}) is not None
    # A code-looking source fails the whitelist equality check.
    assert validate_widget_spec({"kind": "stat", "source": "eval(1)", "title": "x"}) is not None


def test_code_looking_routine_id_rejects():
    for bad in ("eval(1)", "${danger}", "a; rm -rf /", "a b", "os.system('x')", "`x`", "{x}"):
        spec = {"kind": "routine", "routineId": bad, "title": "x"}
        assert validate_widget_spec(spec) is not None, bad


def test_missing_or_blank_title_rejects():
    assert validate_widget_spec({"kind": "stat", "source": "connections"}) is not None
    assert validate_widget_spec({"kind": "stat", "source": "connections", "title": "  "}) is not None


def test_over_long_title_rejects():
    spec = {"kind": "stat", "source": "connections", "title": "x" * (MAX_TITLE_LEN + 1)}
    assert validate_widget_spec(spec) is not None
    spec_ok = {"kind": "stat", "source": "connections", "title": "x" * MAX_TITLE_LEN}
    assert validate_widget_spec(spec_ok) is None


def test_extra_fields_reject():
    # No smuggling an extra field (e.g. an "action"/"code" key) past the schema.
    assert validate_widget_spec(
        {"kind": "stat", "source": "connections", "title": "x", "action": "run"}
    ) is not None
    assert validate_widget_spec(
        {"kind": "routine", "routineId": "abc", "title": "x", "code": "eval"}
    ) is not None


def test_non_dict_rejects():
    assert validate_widget_spec("not a dict") is not None
    assert validate_widget_spec(None) is not None
    assert validate_widget_spec(["kind", "stat"]) is not None


def test_pinned_cap_is_six():
    assert MAX_PINNED == 6


def test_widget_summary_is_plain_language():
    assert widget_summary({"kind": "routine", "routineId": "a", "title": "x"})
    assert "token" in widget_summary({"kind": "stat", "source": "tokens_month", "title": "x"}).lower()


# --- command widget kind: OPEN-mode only (owner decision 2026-07-19) ---------

def test_command_widget_rejected_in_safe_mode():
    spec = {"kind": "command", "command": "ls -la", "title": "List files"}
    # Default mode is SAFE, and SAFE mode never accepts a command widget.
    assert validate_widget_spec(spec) is not None
    assert validate_widget_spec(spec, PolicyMode.SAFE) is not None


def test_command_widget_accepts_in_open_mode():
    spec = {"kind": "command", "command": "ls -la", "title": "List files"}
    assert validate_widget_spec(spec, PolicyMode.OPEN) is None


def test_command_widget_needs_a_command_and_no_extra_fields_even_in_open_mode():
    assert validate_widget_spec({"kind": "command", "title": "x"}, PolicyMode.OPEN) is not None
    assert validate_widget_spec(
        {"kind": "command", "command": "  ", "title": "x"}, PolicyMode.OPEN
    ) is not None
    assert validate_widget_spec(
        {"kind": "command", "command": "ls", "title": "x", "shell": "bash"}, PolicyMode.OPEN
    ) is not None


def test_stat_and_routine_widgets_still_valid_in_open_mode():
    # OPEN mode is a superset — the two SAFE shapes remain valid.
    assert validate_widget_spec(
        {"kind": "stat", "source": "connections", "title": "x"}, PolicyMode.OPEN
    ) is None
    assert validate_widget_spec(
        {"kind": "routine", "routineId": "abc", "title": "x"}, PolicyMode.OPEN
    ) is None


def test_command_widget_summary_is_plain_language():
    assert "command" in widget_summary(
        {"kind": "command", "command": "ls", "title": "x"}
    ).lower()


# ===========================================================================
# The two call sites (agent_core/rpc/widgets.py) — SAFE-mode enforcement.
#
# validate_widget_spec is asked twice on a widget's life: once at SAVE
# (widget.confirmSave re-checks the held draft) and once at RENDER (widget.list
# skips anything it rejects). Both are defense in depth, and that is precisely why
# neither was pinned: remove the save check and widget.list still hides the row;
# remove the list filter and confirmSave still refuses to write one. Each layer
# masks the other's removal, so a test has to enter one layer with the other one
# unable to cover for it. That is what the two tests below are shaped around, and
# it is why the first asserts against the `widgets` TABLE rather than widget.list.
#
# What they defend is SAFE invariant 4 plus the artifact-hiding rule: a command
# widget is a shell command with a button on it, and the Simple profile must never
# store one, list one, or offer to run one.
# ===========================================================================

_COMMAND_WIDGET = {"kind": "command", "command": "rm -rf ~/Documents", "title": "Tidy up"}


def test_a_command_widget_drafted_in_developer_is_refused_when_saved_under_simple(tmp_path):
    """Pressing Add saves against the mode you are in NOW, not the one you drafted in.

    The whole user story, through the real server: in Developer, Addison offers a
    widget for the command it just proposed; the user switches back to Simple and
    then presses Add. The draft is still held in memory and is still a command
    widget, so the save must be refused in plain language and NOTHING may be
    written — a stored command widget is a shell command parked in a Simple rail.

    The command is declined at its permission card, so nothing runs here; the
    declined tool_call is still in the transcript, which is what the widget
    proposal is drafted from.
    """
    responses = [
        ModelResponse(
            text=None,
            tool_calls=[
                ToolCallRequest(
                    id="c1", tool_id="run_command", args={"command": "rm -rf ~/Documents"}
                )
            ],
        ),
        ModelResponse(text="I left everything alone.", tool_calls=[]),
    ]
    h = build_server(tmp_path, responses=responses, register_tool=False)
    # dev_only: the registration a HIGH tool with no undo is only allowed to make.
    h.server.tool_registry.register(RunCommandTool(), dev_only=True)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.PROFILE_SET,
                     "params": {"profileId": "developer"}})
        opened = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert opened["result"]["mode"] == "open"

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_SEND_MESSAGE,
                     "params": {"text": "clear out my documents folder"}})
        writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        reader.feed({"jsonrpc": "2.0", "method": Method.PERMISSION_RESPOND,
                     "params": {"toolId": "run_command", "allow": False}})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 3,
                     "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        assert preview["kind"] == "command", "Developer mode should offer the command widget"

        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.PROFILE_SET,
                     "params": {"profileId": "simple"}})
        closed = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)
        assert closed["result"]["mode"] == "safe"

        reader.feed({"jsonrpc": "2.0", "id": 5, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        refusal = writer.wait_for(lambda f: f.get("id") == 5 and "error" in f)
        assert refusal["error"]["message"] == (
            "That kind of widget only works in the Developer profile."
        )

        # Against the TABLE, not widget.list: a row written here would carry
        # created_in_mode='safe' and would be hidden by the render-time filter, so
        # an empty widget.list would say nothing about whether the save was refused.
        with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
            assert conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 0
    finally:
        _shutdown(reader, h.thread)


def test_a_command_widget_row_is_hidden_from_the_simple_rail_whatever_it_claims_it_was_made_in(
    tmp_path,
):
    """What a row IS decides whether Simple may see it — not what its stamp says.

    ``created_in_mode`` is a stamp on the row, so it is only as good as whoever
    wrote it: a restored config, an older build, or a hand-edited database can all
    put a command spec behind a 'safe' stamp. This row is stamped 'safe' on
    purpose, which takes the created_in_mode filter out of the picture entirely and
    leaves the render-time validation as the only thing standing between a shell
    command and the Simple rail.

    Switching to Developer lists the very same row, which is what makes the empty
    list above mean 'hidden' rather than 'the test inserted something unreadable'.
    """
    store = Store(tmp_path / IPC_DB_NAME)
    store.insert_widget(
        id="w-command",
        spec_json=json.dumps(_COMMAND_WIDGET),
        pinned=True,
        position=0,
        created_at=int(time.time()),
        created_in_mode="safe",
    )
    store.close()

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert listed["result"]["widgets"] == []

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.PROFILE_SET,
                     "params": {"profileId": "developer"}})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_LIST})
        in_open = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        assert [w["id"] for w in in_open["result"]["widgets"]] == ["w-command"]
    finally:
        _shutdown(reader, h.thread)
