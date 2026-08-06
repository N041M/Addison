"""Widget spec validation — the SAVE-time / RENDER-time gate (agent_core/widgets.py).

A widget is a DECLARATIVE spec, one of exactly two shapes, NEVER code. These
tests pin that: both valid kinds accept; unknown kinds/sources reject; code-
looking ids reject; over-long titles and extra fields reject.

The later sections leave the validator and drive the server's CALL SITES
(rpc/widgets.py). Validating in isolation proved the rule; it did not prove either
caller still asks — and each caller's filter was invisible to the suite, because
the other one masked its removal (see the section header). The final section does
the same for the pinned cap, which used to be "tested" by asserting the constant
equals six — a statement about the test file, not about anything the server does.
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


# ===========================================================================
# Listing a dev-created widget as DISABLED (owner decision 2026-08-06).
#
# The render-time filter above no longer drops a row just because it was made in
# Developer: switching profiles made the person's own work vanish. It is listed
# with a REASON instead — and the two tests here pin the pair of edges that
# distinction created, because both are ways the change could quietly go wrong:
#
#   * a dev-STAMPED row is now judged against OPEN's vocabulary, so it must not
#     become a hole for a spec nothing can read (a stamp is only as good as
#     whoever wrote it — see the test above);
#   * an ordinary SAFE widget must keep the payload it always had, with no
#     marker at all, or every widget in Simple starts reading as disabled.
# ===========================================================================


def test_a_dev_created_widget_is_listed_in_simple_with_the_reason_it_cant_be_used(tmp_path):
    """The feature itself, at the list handler: the row comes back, marked."""
    store = Store(tmp_path / IPC_DB_NAME)
    store.insert_widget(
        id="w-dev",
        spec_json=json.dumps(_COMMAND_WIDGET),
        pinned=True,
        position=0,
        created_at=int(time.time()),
        created_in_mode="open",
    )
    store.insert_widget(
        id="w-safe",
        spec_json=json.dumps({"kind": "stat", "source": "connections", "title": "Conns"}),
        pinned=True,
        position=1,
        created_at=int(time.time()),
        created_in_mode="safe",
    )
    store.close()

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_LIST})
        listed = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        rows = {w["id"]: w for w in listed["result"]["widgets"]}
        assert set(rows) == {"w-dev", "w-safe"}
        # A reason, not a boolean: a later cause is another slug in this field.
        assert rows["w-dev"]["unavailable"] == {
            "reason": "developer_abilities",
            "message": "That widget uses developer abilities, so it's waiting in "
            "Developer profile.",
        }
        # The positive control. An ordinary Simple widget is untouched — no
        # marker, so nothing in the rail can read it as disabled.
        assert "unavailable" not in rows["w-safe"]
    finally:
        _shutdown(reader, h.thread)


def test_a_dev_stamped_widget_whose_spec_is_unreadable_is_still_not_listed(tmp_path):
    """Judging a dev row against OPEN must not become 'anything stamped open goes'.

    A disabled card is for work that is merely waiting. This spec is not waiting
    for anything — it is not a widget in EITHER mode — so the render-time
    validation still drops it, and the Simple rail never renders a row it cannot
    describe. Listing it in Developer would be the same bug one profile along, so
    the second half checks OPEN drops it too."""
    store = Store(tmp_path / IPC_DB_NAME)
    store.insert_widget(
        id="w-nonsense",
        spec_json=json.dumps({"kind": "agent", "title": "Do things", "prompt": "go"}),
        pinned=True,
        position=0,
        created_at=int(time.time()),
        created_in_mode="open",
    )
    store.close()

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_LIST})
        in_safe = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert in_safe["result"]["widgets"] == []

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.PROFILE_SET,
                     "params": {"profileId": "developer"}})
        writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_LIST})
        in_open = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        assert in_open["result"]["widgets"] == []
    finally:
        _shutdown(reader, h.thread)


# ===========================================================================
# The pinned cap (MAX_PINNED), at the two places the server actually applies it.
#
# This section replaces `assert MAX_PINNED == 6`, which asserted that a constant
# equals its own literal and would have stayed green with both enforcement sites
# deleted. The cap has two of them, and they are easy to mistake for one:
#
#   * widget.setPinned refuses a pin that would exceed it (rpc/widgets.py), and
#     excludes the widget being pinned from the count, so re-pinning something
#     already pinned never counts itself against the cap;
#   * widget.confirmSave uses it to decide whether a BRAND-NEW widget arrives
#     pinned — a soft default, not a refusal: past the cap the widget is still
#     saved, it just lands unpinned.
#
# The number six is never asserted directly below. It is reached by pinning
# MAX_PINNED widgets and watching the next one be turned away — which is the only
# form in which "six" is a claim about the rail rather than about arithmetic.
# ===========================================================================

_STAT_WIDGET = {"kind": "stat", "source": "connections", "title": "Conns"}


def _seed_widgets(tmp_path, pinned_count: int, extra_unpinned: int = 0) -> None:
    """Put ``pinned_count`` pinned widgets (and optionally some unpinned ones) in
    the rail's database before the server opens it — the state the cap is a
    function of, without driving a save for each one."""
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        for index in range(pinned_count + extra_unpinned):
            store.insert_widget(
                id=f"w-{index}",
                spec_json=json.dumps(dict(_STAT_WIDGET, title=f"Conns {index}")),
                pinned=index < pinned_count,
                position=index,
                created_at=int(time.time()),
                created_in_mode="safe",
            )
    finally:
        store.close()


def _pinned_ids(reader, writer, request_id: int) -> list[str]:
    """The ids widget.list currently reports as pinned — read back through the
    server rather than the table, because the rail is what the person sees."""
    reader.feed({"jsonrpc": "2.0", "id": request_id, "method": Method.WIDGET_LIST})
    listed = writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return [w["id"] for w in listed["result"]["widgets"] if w["pinned"]]


def test_pinning_one_widget_too_many_is_refused_and_nothing_changes(tmp_path):
    """The cap is enforced, in plain language, and the refusal is a real no-op.

    A refusal that still wrote the row would be worse than no cap at all: the rail
    would say it was full while quietly filling further, and the person would have
    been told the opposite of what happened. So this checks the answer AND the
    state behind it — with one widget already unpinned and waiting, which is the
    only arrangement in which a silent success is visible."""
    _seed_widgets(tmp_path, pinned_count=MAX_PINNED, extra_unpinned=1)
    spare = f"w-{MAX_PINNED}"

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_PINNED,
                     "params": {"id": spare, "pinned": True}})
        refusal = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert refusal == {
            "ok": False,
            "error": "You can pin up to six widgets. Unpin one first.",
        }
        # Unchanged: the spare is still unpinned and the rail still holds exactly
        # the ones it held before, so nothing was pinned "anyway".
        pinned = _pinned_ids(reader, writer, 2)
        assert spare not in pinned
        assert pinned == [f"w-{i}" for i in range(MAX_PINNED)]

        # And the cap is a live count, not a permanent verdict: free a slot and the
        # same request succeeds. Without this, a handler that refused every pin
        # unconditionally would pass the half above.
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_SET_PINNED,
                     "params": {"id": "w-0", "pinned": False}})
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.WIDGET_SET_PINNED,
                     "params": {"id": spare, "pinned": True}})
        accepted = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        assert accepted == {"ok": True}
        assert spare in _pinned_ids(reader, writer, 5)
    finally:
        _shutdown(reader, h.thread)


def test_re_pinning_an_already_pinned_widget_at_the_cap_is_allowed(tmp_path):
    """``exclude_id`` earns its place here. A full rail must not make a widget's
    own pin state unwritable: the count the cap is compared against leaves out the
    widget being pinned, so pinning something that is already pinned is a no-op
    rather than the refusal a naive ``count >= MAX_PINNED`` would produce."""
    _seed_widgets(tmp_path, pinned_count=MAX_PINNED)

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_PINNED,
                     "params": {"id": "w-0", "pinned": True}})
        result = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert result == {"ok": True}
        assert _pinned_ids(reader, writer, 2) == [f"w-{i}" for i in range(MAX_PINNED)]
    finally:
        _shutdown(reader, h.thread)


def test_a_new_widget_arrives_pinned_until_the_rail_is_full_then_unpinned(tmp_path):
    """The cap's OTHER site: widget.confirmSave's default-pinned decision.

    This is a default, not a refusal — the distinction is the whole test. With a
    slot free the new widget lands on the rail; with none free it is still SAVED,
    just not pinned, because a full rail must not start throwing away the widgets
    the person asked for. Both halves come from one conversation and one real save
    path, so the transition happens at the cap and nowhere else.
    """
    _seed_widgets(tmp_path, pinned_count=MAX_PINNED - 1)

    h = build_server(
        tmp_path,
        responses=[ModelResponse(text="You've used a few.", tool_calls=[])],
        register_tool=False,
    )
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
                     "params": {"text": "how many tokens have I used?"}})
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        # One slot left -> the new widget is pinned, filling the rail.
        reader.feed({"jsonrpc": "2.0", "id": 2,
                     "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert preview["kind"] == "stat"
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        first = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        assert first["ok"] is True
        assert first["pinned"] is True

        # No slot left -> saved anyway, unpinned. Nothing is lost, it just waits.
        reader.feed({"jsonrpc": "2.0", "id": 4,
                     "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 5, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        second = writer.wait_for(lambda f: f.get("id") == 5 and "result" in f)["result"]
        assert second["ok"] is True
        assert second["pinned"] is False

        listed_ids = _pinned_ids(reader, writer, 6)
        assert first["widgetId"] in listed_ids
        assert second["widgetId"] not in listed_ids
        assert len(listed_ids) == MAX_PINNED
    finally:
        _shutdown(reader, h.thread)
