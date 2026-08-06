"""Widget spec validation — the SAVE-time / RENDER-time gate (agent_core/widgets.py).

A widget is a DECLARATIVE spec from a CLOSED set of six kinds, NEVER code — five
usable in SAFE (routine, stat, checklist, note, timer) plus `command` in OPEN.
These tests pin that: every valid kind accepts; unknown kinds/sources reject;
code-looking ids reject; over-long titles and extra fields reject.

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
    MAX_CHECKLIST_ITEMS,
    MAX_ITEM_LEN,
    MAX_NOTE_LEN,
    MAX_PINNED,
    MAX_TIMER_SECONDS,
    MAX_TITLE_LEN,
    STAT_SOURCES,
    WIDGET_KINDS,
    initial_widget_state,
    validate_widget_spec,
    validate_widget_state,
    widget_summary,
    widget_uses_dev_abilities,
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


def test_a_command_widget_stamped_safe_is_disabled_and_unrunnable_in_simple(tmp_path):
    """What a row IS decides what Simple may do with it — not what its stamp says.

    ``created_in_mode`` is a stamp on the row, so it is only as good as whoever
    wrote it: a restored config, an older build, or a hand-edited database can all
    put a command spec behind a 'safe' stamp. This row is stamped 'safe' on
    purpose, so the stamp is pulling in the widget's favour throughout — and it
    buys the row nothing. It is marked unavailable because its SPEC needs
    Developer, and `widget.run` refuses it whatever the list said.

    It is LISTED-DISABLED rather than hidden, which is the same treatment an
    honestly-stamped dev widget gets (owner decision 2026-08-06) — the stamp
    cannot buy a row a different display any more than it can buy it a shell.

    Switching to Developer clears the marker on that same row, which is what makes
    the marker above mean 'this profile can't use it' rather than 'the test
    inserted something unreadable'."""
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
        assert _listed(reader, writer, 1)["w-command"]["unavailable"] == {
            "reason": "developer_abilities",
            "message": "That widget uses developer abilities, so it's waiting in "
            "Developer profile.",
        }

        # THE PROPERTY THAT MATTERS. The marker is display; this is enforcement,
        # and a 'safe' stamp does not reach it either.
        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_RUN,
                     "params": {"id": "w-command"}})
        refused = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert refused == {
            "ok": False,
            "error": "That widget uses developer abilities, so it's waiting in "
            "Developer profile.",
        }

        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.PROFILE_SET,
                     "params": {"profileId": "developer"}})
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        assert "unavailable" not in _listed(reader, writer, 4)["w-command"]
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


# ===========================================================================
# The three interactive SAFE kinds (Phase-2 step 6, half A) — checklist, note,
# timer. They are SAFE-legal because they invoke NO tool and add ZERO execution
# surface, so what has to be pinned is not "can they run something" (nothing can)
# but the shape rules: the closed vocabulary, the caps, and the SPEC/STATE split.
#
# The state rules carry the weight. A widget spec was immutable until now, and
# every safety argument in widgets.py rests on a stored spec being re-judgeable at
# render. Keeping the doing OUT of the declaration is what preserves that, so the
# tests below check both halves of every rule: what is accepted, and that the
# thing it would have corrupted is untouched afterwards.
# ===========================================================================

_CHECKLIST = {"kind": "checklist", "items": ["Buy milk", "Call Ana"], "title": "Saturday"}
_NOTE = {"kind": "note", "text": "Ana's address", "title": "Note"}
_TIMER = {"kind": "timer", "seconds": 300, "title": "5 minutes timer"}


def test_the_three_interactive_kinds_are_valid_in_the_simple_profile():
    """The feature itself: SAFE accepts them, with no mode argument at all — the
    default is SAFE, and these are the kinds the companion is for."""
    for spec in (_CHECKLIST, _NOTE, _TIMER):
        assert validate_widget_spec(spec) is None, spec["kind"]
        assert validate_widget_spec(spec, PolicyMode.SAFE) is None, spec["kind"]
        assert validate_widget_spec(spec, PolicyMode.OPEN) is None, spec["kind"]
    # ...and they are in the declared vocabulary, which is what widget.list and
    # the frontend both read the closed set from.
    for kind in ("checklist", "note", "timer"):
        assert kind in WIDGET_KINDS


def test_widget_uses_dev_abilities_classifies_every_kind_in_the_vocabulary():
    """The dev-ability test is DERIVED from the validator rather than written as a
    second list of kinds, and this walks WIDGET_KINDS to hold it to that.

    A hand-written pair of asserts would stay green forever while a seventh kind
    went unclassified — which is the failure mode that matters, because an
    unclassified OPEN-only kind is one that Simple would treat as ordinary. The
    coverage assertion is therefore the load-bearing line here, not the loop.

    ``command`` is the only OPEN-only kind today. Nothing about this test says so
    twice: it reads the answer out of the validator, exactly as the function
    does."""
    samples = {
        "routine": {"kind": "routine", "routineId": "r-1", "title": "Run it"},
        "stat": _STAT_WIDGET,
        "checklist": _CHECKLIST,
        "note": _NOTE,
        "timer": _TIMER,
        "command": _COMMAND_WIDGET,
    }
    assert set(samples) == set(WIDGET_KINDS), "a kind was added without a sample here"
    for kind, spec in samples.items():
        needs_dev = validate_widget_spec(spec, PolicyMode.SAFE) is not None
        assert widget_uses_dev_abilities(spec) is needs_dev, kind


def test_a_spec_no_mode_can_read_is_not_waiting_for_developer():
    """"Needs Developer" means OPEN accepts it and SAFE does not — not "SAFE said
    no", which is also true of every piece of nonsense in the table.

    The distinction is what keeps a disabled card meaning 'your work is waiting'.
    A row nothing can read is dropped by the render-time validation instead, and
    it would be listed as merely waiting if this returned True for it."""
    for junk in ({"kind": "agent", "title": "Do things", "prompt": "go"},
                 {"kind": "command", "title": "No command here"},
                 {"kind": "checklist", "items": [], "title": "Empty"},
                 "not even a dict",
                 None):
        assert widget_uses_dev_abilities(junk) is False, junk


def test_a_checklist_needs_real_lines_and_stays_within_its_caps():
    assert validate_widget_spec({**_CHECKLIST, "items": []}) is not None
    assert validate_widget_spec({**_CHECKLIST, "items": "milk"}) is not None
    assert validate_widget_spec({**_CHECKLIST, "items": ["ok", "   "]}) is not None
    assert validate_widget_spec({**_CHECKLIST, "items": ["ok", 7]}) is not None
    assert validate_widget_spec({**_CHECKLIST, "items": ["x" * (MAX_ITEM_LEN + 1)]}) is not None
    assert validate_widget_spec({**_CHECKLIST, "items": ["x"] * (MAX_CHECKLIST_ITEMS + 1)}) is not None
    # The boundary is inclusive on the allowed side, or the cap is off by one.
    assert validate_widget_spec({**_CHECKLIST, "items": ["x"] * MAX_CHECKLIST_ITEMS}) is None
    assert validate_widget_spec({**_CHECKLIST, "items": ["x" * MAX_ITEM_LEN]}) is None


def test_a_note_is_text_and_a_timer_is_a_positive_length():
    assert validate_widget_spec({**_NOTE, "text": ""}) is None      # a blank page is fine
    assert validate_widget_spec({**_NOTE, "text": 12}) is not None
    assert validate_widget_spec({**_NOTE, "text": "x" * (MAX_NOTE_LEN + 1)}) is not None
    assert validate_widget_spec({**_TIMER, "seconds": 0}) is not None
    assert validate_widget_spec({**_TIMER, "seconds": -5}) is not None
    assert validate_widget_spec({**_TIMER, "seconds": 5.5}) is not None
    assert validate_widget_spec({**_TIMER, "seconds": MAX_TIMER_SECONDS + 1}) is not None
    # bool is an int in Python, and `True` seconds would sail through a naive
    # isinstance check and become a one-second timer nobody asked for.
    assert validate_widget_spec({**_TIMER, "seconds": True}) is not None


def test_the_new_kinds_reject_extra_fields_like_every_other_kind():
    assert validate_widget_spec({**_CHECKLIST, "onDone": "run_command"}) is not None
    assert validate_widget_spec({**_NOTE, "script": "eval"}) is not None
    assert validate_widget_spec({**_TIMER, "onZero": "notify"}) is not None


def test_widget_summary_describes_the_new_kinds_in_plain_language():
    assert "tick" in widget_summary(_CHECKLIST).lower()
    assert widget_summary(_NOTE)
    # A duration a person reads, not "300s".
    assert "5 minutes" in widget_summary(_TIMER)


# --- state validation: the SPEC is the authority for the shape ---------------


def test_only_the_interactive_kinds_keep_state():
    """A routine/stat/command widget has nothing to change, and asking to change
    one is refused rather than quietly stored — otherwise widget_state becomes a
    place to park arbitrary JSON against any widget id.

    The second state below is the one that matters, and it is why this test is
    not a formality: `{"running": False, "remaining": 0, "startedAt": None}` is a
    VALID timer state, so without the kind check up front it walks straight
    through the timer arm (`0 > spec.get("seconds", 0)` is false for a spec that
    has no `seconds`) and a routine widget acquires a stored timer. An empty dict
    alone would have let that mutation live — it is rejected by the timer arm's
    own type checks either way."""
    timer_shaped = {"running": False, "remaining": 0, "startedAt": None}
    for spec in (
        {"kind": "routine", "routineId": "a", "title": "x"},
        {"kind": "stat", "source": "connections", "title": "x"},
        _COMMAND_WIDGET,
    ):
        assert validate_widget_state(spec, {}) is not None, spec["kind"]
        assert validate_widget_state(spec, timer_shaped) is not None, spec["kind"]


def test_a_checklist_state_must_be_exactly_as_long_as_its_spec():
    """The positional rule, which is the reason a mismatch is DISCARDED rather
    than padded: `checked[i]` means `items[i]`, so a state of the wrong length
    cannot be applied without ticking a line nobody ticked."""
    assert validate_widget_state(_CHECKLIST, {"checked": [True, False]}) is None
    assert validate_widget_state(_CHECKLIST, {"checked": [True]}) is not None
    assert validate_widget_state(_CHECKLIST, {"checked": [True, False, True]}) is not None
    assert validate_widget_state(_CHECKLIST, {"checked": ["yes", "no"]}) is not None
    assert validate_widget_state(_CHECKLIST, {"checked": [True, False], "note": "x"}) is not None
    assert validate_widget_state(_CHECKLIST, "ticked") is not None


def test_a_timer_state_holds_start_duration_and_paused_only():
    running = {"running": True, "remaining": 300, "startedAt": 1_700_000_000}
    paused = {"running": False, "remaining": 120, "startedAt": None}
    assert validate_widget_state(_TIMER, running) is None
    assert validate_widget_state(_TIMER, paused) is None
    # A start time is required exactly when it is running: a paused timer holding
    # one would count down twice, and a running one without it cannot count at all.
    assert validate_widget_state(_TIMER, {**running, "startedAt": None}) is not None
    assert validate_widget_state(_TIMER, {**paused, "startedAt": 1_700_000_000}) is not None
    # Never more than the timer was set for, and never negative.
    assert validate_widget_state(_TIMER, {**paused, "remaining": 301}) is not None
    assert validate_widget_state(_TIMER, {**paused, "remaining": -1}) is not None
    # Nothing else may ride along — no callback, no label, no "onZero".
    assert validate_widget_state(_TIMER, {**paused, "onZero": "run"}) is not None


def test_a_note_state_is_text_within_the_same_cap_as_its_spec():
    assert validate_widget_state(_NOTE, {"text": "moved to Brno"}) is None
    assert validate_widget_state(_NOTE, {"text": "x" * (MAX_NOTE_LEN + 1)}) is not None
    assert validate_widget_state(_NOTE, {"text": None}) is not None


def test_the_initial_state_comes_from_the_spec_not_from_a_caller():
    """What a freshly-saved widget starts as. The core derives it; nothing on the
    wire proposes it, which is why an un-ticked list is not something a draft can
    arrive pre-ticked."""
    assert initial_widget_state(_CHECKLIST) == {"checked": [False, False]}
    assert initial_widget_state(_NOTE) == {"text": "Ana's address"}
    assert initial_widget_state(_TIMER) == {"running": False, "remaining": 300, "startedAt": None}
    assert initial_widget_state({"kind": "stat", "source": "connections", "title": "x"}) is None
    # And whatever it produces is valid by its own validator — the two agree, or a
    # brand-new widget would render with its state dropped.
    for spec in (_CHECKLIST, _NOTE, _TIMER):
        assert validate_widget_state(spec, initial_widget_state(spec)) is None


# ===========================================================================
# widget.setState at the SERVER — the call sites, not the validator.
#
# Same lesson as the two call-site sections above: validating in isolation proves
# the rule and proves nothing about whether either caller still asks. setState has
# two checks that only exist in the handler (the widget must be usable under the
# ACTIVE profile, and the spec must still validate in it), and one that is shared
# with the render path. Each test below enters through the real JSON-RPC server and
# then reads the result back through widget.list or the table — because "was it
# refused" and "was nothing written" are different questions, and only the second
# one catches a handler that answers no and stores anyway.
# ===========================================================================


def _insert(tmp_path, widget_id: str, spec: dict, mode: str = "safe") -> None:
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        store.insert_widget(
            id=widget_id,
            spec_json=json.dumps(spec),
            pinned=True,
            position=0,
            created_at=int(time.time()),
            created_in_mode=mode,
        )
    finally:
        store.close()


def _listed(reader, writer, request_id: int) -> dict:
    reader.feed({"jsonrpc": "2.0", "id": request_id, "method": Method.WIDGET_LIST})
    listed = writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return {w["id"]: w for w in listed["result"]["widgets"]}


def test_ticking_a_box_is_stored_and_comes_back_on_the_next_list(tmp_path):
    """The feature, end to end and in the Simple profile: no permission card is
    raised anywhere in this exchange, because there is nothing here to gate."""
    _insert(tmp_path, "w-list", _CHECKLIST)
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        # Before: no state at all — the key is ABSENT, not an empty object, so an
        # older frontend sees exactly the payload it always saw.
        assert "state" not in _listed(reader, writer, 1)["w-list"]

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-list", "state": {"checked": [False, True]}}})
        saved = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        # The stored state is echoed back — that is what an optimistic rail
        # reconciles against, so it has to be the stored value and not an ack.
        assert saved == {"ok": True, "state": {"checked": [False, True]}}

        assert _listed(reader, writer, 3)["w-list"]["state"] == {"checked": [False, True]}
    finally:
        _shutdown(reader, h.thread)


def test_a_state_the_frontend_invented_is_refused_and_the_stored_one_is_untouched(tmp_path):
    """The wire is not trusted. A checklist of two lines can only ever have a
    two-long state, and the refusal must leave the real one in place — a handler
    that wrote first and validated after would pass a test that only read the
    reply."""
    _insert(tmp_path, "w-list", _CHECKLIST)
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-list", "state": {"checked": [True, False]}}})
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        for bad in ({"checked": [True, True, True]}, {"checked": "all"}, {"text": "sneaky"}):
            reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_SET_STATE,
                         "params": {"id": "w-list", "state": bad}})
            refusal = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
            assert refusal["ok"] is False, bad
            assert refusal["error"], bad

        assert _listed(reader, writer, 3)["w-list"]["state"] == {"checked": [True, False]}
    finally:
        _shutdown(reader, h.thread)


def test_a_checklist_made_in_developer_is_fully_usable_in_simple(tmp_path):
    """The regression test for the provenance bug, and it is about a real rail.

    A checklist needs nothing developer about it — it invokes no tool and has no
    execution surface, which is the whole reason it is one of the SAFE kinds. But
    availability used to be read off ``created_in_mode``, so making one while the
    Developer profile happened to be active stamped it 'open', and switching to
    Simple produced a shopping list that announced it "uses developer abilities"
    and refused to let its boxes be ticked. Both halves were false.

    Asked of the spec, the stamp is irrelevant here: no marker, and the state
    round-trips. The stamp itself still ships (the rail's DEV tag reads it), which
    is why the last line pins it — 'fixed' must not mean 'stopped recording where
    it came from'."""
    _insert(tmp_path, "w-list", _CHECKLIST, mode="open")
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        assert "unavailable" not in _listed(reader, writer, 1)["w-list"]

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-list", "state": {"checked": [True, False]}}})
        ticked = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert ticked == {"ok": True, "state": {"checked": [True, False]}}

        row = _listed(reader, writer, 3)["w-list"]
        assert row["state"] == {"checked": [True, False]}
        assert row["createdInMode"] == "open"
    finally:
        _shutdown(reader, h.thread)


def test_setting_the_state_of_a_widget_that_needs_developer_abilities_is_refused(tmp_path):
    """Dispatch is the enforcement, never the display marker (owner decision
    2026-08-06). A widget that needs Developer is LISTED in Simple as a disabled
    row, so a stale frontend — or a mode switch mid-click — arrives here, and here
    is where it is turned away. The refusal is the same sentence the row carries.

    The vehicle is a ROUTINE widget pointing at a dev routine, and it has to be:
    the three kinds that keep state are all SAFE by construction, so no stateful
    widget can need Developer on its own. This spec is SAFE-legal by SHAPE and
    needs Developer anyway, because of what it points AT — which is the case the
    look-through exists for, and the one a spec-only check would miss."""
    store = Store(tmp_path / IPC_DB_NAME)
    store.insert_routine(
        id="r-dev",
        name="Tidy up",
        description="",
        plan_json={"id": "r-dev", "name": "Tidy up", "description": "",
                   "variables": [], "steps": []},
        created_from_conversation_id=None,
        created_at=int(time.time()),
        created_in_mode="open",
    )
    store.close()
    _insert(tmp_path, "w-dev", {"kind": "routine", "routineId": "r-dev", "title": "Tidy up"})
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-dev", "state": {"checked": [True, True]}}})
        refusal = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert refusal == {
            "ok": False,
            "error": "That widget uses developer abilities, so it's waiting in "
            "Developer profile.",
        }
        assert "state" not in _listed(reader, writer, 2)["w-dev"]

        # ...and once Developer is on, the SAME call stops being turned away for
        # that reason and is turned away for the honest one instead. Both are
        # refusals, so an assertion on `ok` alone would have proved nothing: it is
        # the sentence CHANGING that shows the profile is what drove the first one.
        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.PROFILE_SET,
                     "params": {"profileId": "developer"}})
        writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 4, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-dev", "state": {"checked": [True, True]}}})
        in_open = writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        assert in_open == {"ok": False, "error": "That widget doesn't keep anything to change."}
    finally:
        _shutdown(reader, h.thread)


def test_setting_the_state_of_a_widget_that_is_gone_is_refused(tmp_path):
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "no-such-widget", "state": {"text": "hi"}}})
        refusal = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert refusal == {"ok": False, "error": "That widget isn't here any more."}
    finally:
        _shutdown(reader, h.thread)


def test_a_stored_state_that_no_longer_fits_its_spec_is_dropped_at_render(tmp_path):
    """The read-time half. Specs are immutable, so a mismatch cannot come from the
    app — it comes from a hand-edited database or a payload written by another
    build. The row still lists (the widget is fine), it simply arrives with no
    state, and the rail draws an untouched list rather than a wrongly ticked one."""
    _insert(tmp_path, "w-list", _CHECKLIST)
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        store.set_widget_state("w-list", json.dumps({"checked": [True]}), int(time.time()))
    finally:
        store.close()

    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        row = _listed(reader, writer, 1)["w-list"]
        assert row["spec"]["kind"] == "checklist"
        assert "state" not in row
    finally:
        _shutdown(reader, h.thread)


def test_a_checklist_proposed_from_the_conversation_arrives_saved_and_unticked(tmp_path):
    """The companion path all the way through: the person says what they want, the
    core drafts it from THEIR words (never the assistant's), and the saved widget
    starts in a state the core derived rather than one anybody sent."""
    h = build_server(
        tmp_path,
        responses=[ModelResponse(text="Here's a checklist.", tool_calls=[])],
        register_tool=False,
    )
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
                     "params": {"text": "make me a widget with a checklist: milk, bread"}})
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 2,
                     "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert preview["kind"] == "checklist"
        assert preview["spec"]["items"] == ["milk", "bread"]

        reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.WIDGET_CONFIRM_SAVE,
                     "params": {"accept": True}})
        saved = writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]
        assert saved["ok"] is True

        row = _listed(reader, writer, 4)[saved["widgetId"]]
        assert row["state"] == {"checked": [False, False]}
    finally:
        _shutdown(reader, h.thread)


def test_a_timer_is_proposed_with_the_length_the_person_asked_for(tmp_path):
    h = build_server(
        tmp_path,
        responses=[ModelResponse(text="Done.", tool_calls=[])],
        register_tool=False,
    )
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
                     "params": {"text": "put a 25 minute timer widget in the panel"}})
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        reader.feed({"jsonrpc": "2.0", "id": 2,
                     "method": Method.WIDGET_PROPOSE_FROM_CONVERSATION})
        preview = writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert preview["spec"] == {
            "kind": "timer", "seconds": 1500, "title": "25 minutes timer",
        }
    finally:
        _shutdown(reader, h.thread)


def test_removing_a_widget_takes_its_state_with_it(tmp_path):
    """`widget_state.widget_id` REFERENCES `widgets(id)` with foreign keys ON, so a
    delete that forgot the state row would RAISE rather than delete — and the
    person's Remove button would simply stop working on any widget they had used."""
    _insert(tmp_path, "w-list", _CHECKLIST)
    h = build_server(tmp_path, responses=[], register_tool=False)
    reader, writer = h.reader, h.writer
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": Method.WIDGET_SET_STATE,
                     "params": {"id": "w-list", "state": {"checked": [True, True]}}})
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)

        reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.WIDGET_DELETE,
                     "params": {"id": "w-list"}})
        assert writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"] == {"ok": True}
        assert _listed(reader, writer, 3) == {}
    finally:
        _shutdown(reader, h.thread)
        with sqlite3.connect(tmp_path / IPC_DB_NAME) as conn:
            assert conn.execute("SELECT COUNT(*) FROM widget_state").fetchone()[0] == 0
