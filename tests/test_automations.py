"""Automations — step 8, PHASE 1 (docs/step-8-automation-plan.md §4.1).

Phase 1 shipped the row and the inert surface: the ``automations`` table, the
``automation.list``/``automation.remove`` RPC, the snapshot reason slugs, and
nothing else. **This NAMESPACE still has no way to create an automation, and
NOTHING anywhere can arm one.** Authoring arrived in phase 2 as a registered tool
(``create_automation`` — ``tests/test_create_automation.py`` owns it), which is
why the pins below say what they say: rows exist, and no code path here made one.
Arming is phase 3 and exists nowhere in the tree.

So — the step-7 phase-1 shape — these tests are as much about what CANNOT happen as
about what does:

  (1) NOTHING can add a row and nothing can arm one: no add surface exists, and the
      module that owns this namespace can neither start a process nor reach the
      shell bridge, so no phase-1 code path can write a plist or call ``launchctl``;
  (2) THERE IS NO ARMED STATE ANYWHERE — not a column, not a payload field, not a
      captured column (plan §5.6). A stored armed flag is exactly what a one-action
      G3 restore would put back, and a restore can never perform the keyword
      ceremony arming requires;
  (3) the schedule vocabulary is CLOSED — the database refuses a third kind, and
      what reaches the wire is a projection of that kind's fields, so a hand-edited
      row cannot push a key of its own onto a surface;
  (4) the table is snapshot-CAPTURED, so a restore genuinely puts the list back
      (reversible config, plan §1);
  (5) both methods answer in EVERY profile, because saved configuration is not a
      capability and a tightening must never be trapped by a profile switch;
  (6) every ``automation.*`` method is answered ON THE WORKER THREAD.

Every test here was mutation-proven: the line it guards was broken and this test
watched to fail. The mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from agent_core.automations import (
    NO_SCHEDULE,
    SCHEDULE_FIELDS,
    SCHEDULE_KINDS,
    Automation,
    plist_text,
    schedule_fields,
    schedule_is_readable,
    schedule_sentence,
)
from agent_core.memory.store import Store
from agent_core.rpc.automations import _NO_SUCH_AUTOMATION
from agent_core.snapshots.scope import _CAPTURED_TABLES
from agent_core.snapshots.snapshot_manager import REASONS
from tests.conftest import IPC_DB_NAME, _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUTOMATIONS_SRC = _REPO_ROOT / "agent_core" / "rpc" / "automations.py"
_MAIN_SRC = _REPO_ROOT / "agent_core" / "main.py"
_SCHEMA_SRC = _REPO_ROOT / "agent_core" / "memory" / "schema.sql"

# One saved automation, in the shape phase 2 will write. Written BY HAND here,
# because that is the only way a row can exist in phase 1 — which is the point of
# the phase and the reason these fixtures look like this.
_INTERVAL = {
    "id": "auto-1",
    "name": "Tidy up downloads",
    "label": "com.addison.auto.tidy-downloads",
    "command": "/usr/bin/find ~/Downloads -mtime +30 -delete",
    "schedule_kind": "interval",
    "schedule_json": json.dumps({"minutes": 60}),
    "created_in_mode": "open",
    "created_at": 1_700_000_000,
}
_CALENDAR = {
    "id": "auto-2",
    "name": "Back up notes",
    "label": "com.addison.auto.backup-notes",
    "command": "/usr/local/bin/backup-notes",
    "schedule_kind": "calendar",
    "schedule_json": json.dumps({"hour": 7, "minute": 30, "weekday": 1}),
    "created_in_mode": "open",
    "created_at": 1_700_000_100,
}


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _developer(harness, request_id: int = 900) -> None:
    """Switch the running server to the Developer profile the way the app does."""
    result = _call(harness, "profile.set", {"profileId": "developer"}, request_id)
    assert result["ok"] is True and result["mode"] == "open"


def _server_with(tmp_path, *rows: dict):
    """A live server whose database already holds these automations.

    Seeded through a ``store_factory`` — on the worker thread, before the server
    answers anything — because there is NO RPC and no tool that can write one. That
    is not a limitation of the harness; it is the phase-1 claim, and a test that had
    an easier way to make a row would be testing a surface this phase must not have."""

    def factory() -> Store:
        store = Store(tmp_path / IPC_DB_NAME)
        store.set_setting("widgets_seeded", "1")
        for row in rows:
            store.insert_automation(**row)
        return store

    return build_server(tmp_path, register_tool=False, store_factory=factory)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "automations-test.sqlite3")


def _docstring_string_ids(tree: ast.Module) -> set[int]:
    """The ids of every string Constant that is a DOCSTRING, so a scan over string
    literals can skip them.

    Shared by the two source-pin tests below, which both ask "does this module
    NAME a thing it must not" and must not trip over their own prose: this file's
    pins are about what the module DOES, and a docstring explaining why it may not
    reach a plist is not the module reaching one."""
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


# ---------------------------------------------------------------------------
# (1) Nothing can add a row, and nothing can arm one
# ---------------------------------------------------------------------------


def test_the_surface_has_no_way_to_create_an_automation(tmp_path):
    """THE POINT OF PHASE 1, and the reason this file exists before anything can use
    it. The namespace answers exactly two methods; ``automation.add`` (or any other
    spelling of "write a row") is phase 2 and must not be reachable by an early
    frontend, a stale client or a hopeful model.

    Mutation: add ``Method.AUTOMATION_ADD = "automation.add"`` to protocol.py and
    route it — this fails on the method set."""
    from agent_core.protocol import Method

    named = {
        value
        for value in vars(Method).values()
        if isinstance(value, str) and value.startswith("automation.")
    }
    assert named == {"automation.list", "automation.remove"}

    h = _server_with(tmp_path)
    try:
        _developer(h)
        assert _call(h, "automation.list", {}, 1)["automations"] == []
        # An unknown method is refused with an error frame, not silently accepted.
        h.reader.feed({"jsonrpc": "2.0", "id": 2, "method": "automation.add", "params": {}})
        frame = h.writer.wait_for(lambda f: f.get("id") == 2)
        assert "error" in frame and "result" not in frame
    finally:
        _shutdown(h.reader, h.thread)


def test_the_automation_surface_cannot_reach_a_process_a_shell_or_a_plist():
    """Structural, on ``tests/test_mcp_servers.py``'s pattern and for a sharper
    reason: arming is the one thing in this step that touches the OS, and the Agent
    Core has no OS permissions of its own (spec §1.3). Phase 3 puts arming behind a
    TYPED shell surface performed by the highest-trust process — so the module that
    owns automation configuration must not be able to run a binary, cross the shell
    bridge, or write a file, in this phase or by imitation in a later one.

    ``launchctl``/``launchd``/``plist`` are checked as literals too: the seatbelt
    already denies them (step 5.5), and a string here would be the first sign
    somebody was assembling the escape rather than asking the shell for it.

    Mutation: add ``import subprocess`` (or ``from agent_core.shell_bridge import
    ...``) to rpc/automations.py — this fails, naming the module."""
    source = _AUTOMATIONS_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_roots = {
        "subprocess", "os", "shutil", "socket", "asyncio", "threading", "signal",
        "httpx", "plistlib", "pathlib",
    }
    forbidden_modules = {"agent_core.tools", "agent_core.shell_bridge", "agent_core.orchestrator"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots, (
                    f"rpc/automations.py imports {alias.name}: this module configures "
                    "automations, it never runs one, and it must never be able to start "
                    "a process or write a file"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in forbidden_roots, module
            assert not any(module.startswith(m) for m in forbidden_modules), (
                f"rpc/automations.py imports {module}: nothing in this namespace runs "
                "a command or crosses the shell bridge"
            )
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"Popen", "run_command", "system", "spawn", "write_text"}
    # ...and no string this module can PUT ANYWHERE names the arming machinery. The
    # docstrings are excluded on purpose: prose explaining why arming lives in the
    # shell is exactly what should be written here, and a gate that refused it would
    # be a gate that punished the explanation. What is checked is every other string
    # literal — a command, a path, a payload field — because one of those would be
    # the first sign somebody was assembling the escape rather than asking the shell.
    docstrings = _docstring_string_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for literal in ("launchctl", "launchd", "crontab", "LaunchAgents", "plist"):
                assert literal not in node.value, (
                    f"rpc/automations.py builds a string naming {literal}: arming "
                    "belongs to the typed shell surface (plan §5.8), never to the "
                    "process with no OS permissions of its own"
                )


# ---------------------------------------------------------------------------
# (2) There is no armed state anywhere — plan §5.6
# ---------------------------------------------------------------------------


def test_the_table_has_no_column_that_could_record_being_armed(store: Store):
    """THE LOAD-BEARING ABSENCE. G3 restores configuration in ONE action, and the
    keyword ceremony that arms an automation cannot happen inside one action — so a
    row that remembered "armed" would either lie about what the OS holds or describe
    an arming nobody consented to. Armed truth lives in the OS and is asked for when
    the surface loads (plan §5.6).

    Mutation: add ``armed INTEGER NOT NULL DEFAULT 0`` to the automations DDL — this
    fails, and so does the captured-columns test below."""
    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(automations)")}
    assert columns == {
        "id", "name", "label", "command", "schedule_kind", "schedule_json",
        "created_in_mode", "created_at", "updated_at",
    }
    assert not [c for c in columns if "arm" in c or "enabl" in c or "active" in c]


def test_no_payload_and_no_stored_row_claims_an_automation_is_running(tmp_path):
    """The wire half of the same absence: nothing this surface answers may carry a
    state the OS owns. A frontend that could read ``armed`` off a list payload would
    render it after a restore — the exact moment the row and the OS disagree.

    Mutation: add ``"armed": False`` to ``_automation_wire_row`` — this fails."""
    h = _server_with(tmp_path, _INTERVAL)
    try:
        (row,) = _call(h, "automation.list", {}, 1)["automations"]
        assert not [key for key in row if "arm" in key.lower() or "enabl" in key.lower()]
        assert not [key for key in row if "running" in key.lower() or "install" in key.lower()]
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (3) The schedule vocabulary is CLOSED
# ---------------------------------------------------------------------------


def test_the_database_refuses_a_schedule_kind_outside_the_vocabulary(store: Store):
    """Two kinds, both mapping 1:1 onto launchd (plan §5.4a). No cron, no
    "whenever this file changes", no third thing that would need a second mechanism
    to hold. The CHECK is the enforcement: a later phase that wants another kind has
    to migrate the schema in daylight rather than insert a different string.

    Mutation: widen the CHECK to ``IN ('interval','calendar','cron')`` — this fails."""
    assert set(SCHEDULE_KINDS) == {"interval", "calendar"}
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_automation(
            **{**_INTERVAL, "schedule_kind": "cron", "schedule_json": json.dumps({"minutes": 5})}
        )


@pytest.mark.parametrize("row", [_INTERVAL, _CALENDAR])
def test_a_schedule_survives_the_round_trip_to_sqlite_and_back(store: Store, row: dict):
    """Both kinds, stored and read back through the real column, then projected. The
    JSON is TEXT in SQLite, so this is the test that would notice the day something
    started coercing or re-serialising it on the way through."""
    store.insert_automation(**row)
    (saved,) = store.list_automations()
    assert saved.schedule_kind == row["schedule_kind"]
    assert saved.schedule_json == row["schedule_json"]
    assert schedule_fields(saved.schedule_kind, saved.schedule_json) == json.loads(
        row["schedule_json"]
    )
    # updated_at defaults to created_at: a row nothing has changed was last changed
    # when it was written.
    assert saved.updated_at == saved.created_at


@pytest.mark.parametrize(
    "kind, stored, expected",
    [
        # Only this kind's fields, and only as numbers.
        ("interval", {"minutes": 15}, {"minutes": 15}),
        ("interval", {"minutes": 15, "hour": 9}, {"minutes": 15}),
        ("calendar", {"hour": 7, "minute": 0}, {"hour": 7, "minute": 0}),
        ("calendar", {"hour": 7, "minute": 0, "weekday": 3}, {"hour": 7, "minute": 0, "weekday": 3}),
        # A key nobody declared cannot ride out to a surface, whatever it holds.
        ("calendar", {"hour": 7, "minute": 0, "note": "run this first"}, {"hour": 7, "minute": 0}),
        ("interval", {"minutes": "sixty"}, {}),
        # True is an int in Python and is not a number of minutes.
        ("interval", {"minutes": True}, {}),
        ("interval", {}, {}),
    ],
)
def test_only_the_closed_fields_of_this_kind_reach_the_wire(kind, stored, expected):
    """A PROJECTION, not a parse. The column is captured and restored, and a payload
    written by an older build or edited by hand can hold anything at all — so what
    comes out is the vocabulary rather than the column.

    Mutation: return ``json.loads(schedule_json)`` from ``schedule_fields`` — the
    extra-key rows fail."""
    assert schedule_fields(kind, json.dumps(stored)) == expected


@pytest.mark.parametrize(
    "kind, stored",
    [
        ("interval", "not json at all"),
        ("interval", "[1, 2, 3]"),
        ("calendar", "null"),
        ("calendar", '"a string"'),
        ("moonrise", '{"hour": 7}'),   # a kind the CHECK would never have allowed
        ("interval", None),            # a column read as something other than TEXT
        (None, "{}"),
    ],
    ids=["not json", "a list", "null", "a string", "an unknown kind", "no text", "no kind"],
)
def test_a_row_that_makes_no_sense_answers_nothing_rather_than_raising(kind, stored):
    """One malformed row must never make the whole list unanswerable — ``schedule``
    is read on the list path, where a raise would take every OTHER automation off the
    screen with it. ``{}`` is the honest "this row does not say".

    Mutation: drop the ``except ValueError`` from ``schedule_fields`` — the first
    row raises instead of answering."""
    assert schedule_fields(kind, stored) == {}


def test_the_projection_declares_a_field_set_for_every_kind_the_database_accepts():
    """The two vocabularies are written down twice — the CHECK in schema.sql and
    ``SCHEDULE_FIELDS`` here — so this is the line that keeps them one vocabulary. A
    kind the database accepts but the projection does not know would answer ``{}`` for
    every row of it, silently.

    Mutation: delete the ``calendar`` entry from ``SCHEDULE_FIELDS`` — this fails."""
    schema = _SCHEMA_SRC.read_text(encoding="utf-8")
    assert "CHECK(schedule_kind IN ('interval','calendar'))" in schema
    assert set(SCHEDULE_FIELDS) == set(SCHEDULE_KINDS)


# ---------------------------------------------------------------------------
# (4) Reversible config — snapshot-captured, with the reasons declared
# ---------------------------------------------------------------------------


def test_the_table_is_captured_by_snapshots():
    """Declared, not incidental. ``test_capture_scope_covers_every_schema_table``
    forces the choice to be made; this one pins WHICH way it was made and the exact
    columns, because a silently-dropped column would be reset to its default BY the
    recovery path.

    Capturing is safe here BECAUSE of the absent armed column: there is no state a
    restore could write back that would claim the OS is running something.

    Mutation: move "automations" to ``_EXCLUDED_TABLES`` — this fails, and so does
    the restore test below."""
    assert _CAPTURED_TABLES["automations"] == (
        "id", "name", "label", "command", "schedule_kind", "schedule_json",
        "created_in_mode", "created_at", "updated_at",
    )


def test_a_restore_puts_the_automation_list_back(tmp_path):
    """G3 over the wire for this table: an automation removed after a restore point
    comes back. That is what "an automation row is snapshotted and revocable" means
    (plan §1) — and the row that comes back is a DRAFT, because there is no armed
    column for the restore to resurrect."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR)
    try:
        _developer(h)
        snapshot_id = _call(h, "snapshot.create", {}, 1)["snapshotId"]
        assert _call(h, "automation.remove", {"id": "auto-1"}, 2)["ok"] is True
        assert [a["id"] for a in _call(h, "automation.list", {}, 3)["automations"]] == ["auto-2"]

        assert _call(h, "snapshot.restore", {"id": snapshot_id}, 4)["ok"] is True

        restored = _call(h, "automation.list", {}, 5)["automations"]
        assert [a["id"] for a in restored] == ["auto-1", "auto-2"]
        assert not [key for key in restored[0] if "arm" in key.lower()]
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_an_automation_leaves_a_restore_point_with_its_own_reason(tmp_path):
    """The hook. The slug is not decoration: it is the sentence on the Restore-points
    list, so a removal that borrowed another step's reason would put "Before removing
    a tool server" in front of somebody who removed an automation.

    Mutation: delete the ``_snapshot_auto("automation_remove")`` line — this fails."""
    h = _server_with(tmp_path, _INTERVAL)
    try:
        assert _call(h, "automation.remove", {"id": "auto-1"}, 1)["ok"] is True
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 2)["snapshots"]]
        assert "automation_remove" in reasons
    finally:
        _shutdown(h.reader, h.thread)


def test_the_reason_vocabulary_declares_the_whole_step_up_front():
    """Reserved slugs, on the ``guard_weakened``/``mcp_connect`` precedent: the
    vocabulary is closed and written into snapshot rows, so declaring phases 2 and 3's
    reasons now means a later phase adds a caller rather than churning a vocabulary
    that old rows are already written against.

    Mutation: delete ``automation_remove`` from REASONS — the removal falls back to
    the "other" label and the test above fails."""
    for slug in ("automation_remove", "automation_create", "automation_arm", "automation_disarm"):
        assert REASONS[slug]
        assert not REASONS[slug].endswith(".")


# ---------------------------------------------------------------------------
# (5) Both methods answer in EVERY profile
# ---------------------------------------------------------------------------


def test_a_saved_automation_is_listed_and_removable_in_every_profile(tmp_path):
    """The 2026-08-06 artifact lesson applied here (plan §4.1). What an automation
    NEEDS is Developer — its payload is a shell command — but that is enforced where
    the capability is, at the phase-2/3 tools' ``dev_only`` registration and at
    dispatch. Listing a row grants nothing: it is a name, a schedule and the text of
    a command that is not running.

    Hiding somebody's configuration when they switch to Simple is the failure that
    decision reversed, and REMOVING is a tightening — a profile switch must never be
    the thing that traps configuration somebody wants gone.

    Mutation: add ``if self._mode() is not PolicyMode.OPEN: return {...}`` to either
    handler — this fails in the Simple half."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR)
    try:
        # Simple (the default profile) sees both rows...
        assert [a["id"] for a in _call(h, "automation.list", {}, 1)["automations"]] == [
            "auto-1", "auto-2"
        ]
        # ...and can remove one.
        assert _call(h, "automation.remove", {"id": "auto-1"}, 2)["ok"] is True
        _developer(h)
        assert [a["id"] for a in _call(h, "automation.list", {}, 3)["automations"]] == ["auto-2"]
        assert _call(h, "automation.remove", {"id": "auto-2"}, 4)["ok"] is True
        assert _call(h, "automation.list", {}, 5)["automations"] == []
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (6) Every automation.* method is answered on the worker thread
# ---------------------------------------------------------------------------


def test_no_automation_method_may_be_answered_inline_on_the_read_loop():
    """Both handlers read the Store, and ``remove`` mints a snapshot through the
    SnapshotManager — the sqlite3 connection is bound to the ONE ``turn-worker``
    thread, and a capture that ran beside an in-flight turn would be racing the
    restore floor's own writer.

    ``main.py`` already answers some methods INLINE on the read loop
    (``permission.respond``, ``model.setRoleForNextMessage``), which is exactly how a
    later ``automation.arm`` would get there by imitation. So the rule is structural:
    ``main.py`` may name an ``automation.*`` method in ONE place, the
    ``_AUTOMATION_JOBS`` table that routes it to the queue.

    Mutation: add ``Method.AUTOMATION_LIST: self._handle_something`` to the inline
    table in ``_build_dispatch_table`` — this fails, naming the method."""
    from agent_core import main as main_module
    from agent_core.protocol import Method

    named = {
        name
        for name, value in vars(Method).items()
        if isinstance(value, str) and value.startswith("automation.")
    }
    assert named, "no automation.* methods found in protocol.py — did they move?"
    assert {getattr(Method, name) for name in named} == set(main_module._AUTOMATION_JOBS)

    tree = ast.parse(_MAIN_SRC.read_text(encoding="utf-8"))
    jobs_table = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "_AUTOMATION_JOBS" for t in node.targets)
    )
    allowed = {id(node) for node in ast.walk(jobs_table)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Method"
            and node.attr in named
            and id(node) not in allowed
        ):
            raise AssertionError(
                f"main.py names Method.{node.attr} at line {node.lineno}, outside "
                "_AUTOMATION_JOBS: an automation.* method answered anywhere but the "
                "worker queue reads SQLite from the wrong thread and can mint a "
                "snapshot beside an in-flight turn"
            )


# ---------------------------------------------------------------------------
# Names, shapes and the wire
# ---------------------------------------------------------------------------


def test_the_wire_shape_is_the_one_the_frontend_parses(tmp_path):
    """camelCase at the boundary (``created_at`` -> ``createdAt``,
    ``schedule_kind`` -> ``scheduleKind``), oldest first, and NOTHING else on the row
    — no armed flag, no ``updatedAt`` that is only ever a copy of ``createdAt``, no
    plist path the person never chose.

    ``command`` rides WHOLE: it is the one field somebody must read before arming
    anything, and phase 3's typed keyword exists to make them read it.

    Mutation: send ``row.schedule_json`` instead of the projection, or rename a key —
    this fails."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR)
    try:
        rows = _call(h, "automation.list", {}, 1)["automations"]
        assert [r["id"] for r in rows] == ["auto-1", "auto-2"]
        for row in rows:
            assert set(row) == {
                "id", "name", "label", "command", "scheduleKind", "schedule",
                "scheduleSentence", "createdInMode", "createdAt",
            }
        assert rows[0]["name"] == "Tidy up downloads"
        assert rows[0]["label"] == "com.addison.auto.tidy-downloads"
        assert rows[0]["command"] == _INTERVAL["command"]
        assert rows[0]["scheduleKind"] == "interval"
        assert rows[0]["schedule"] == {"minutes": 60}
        assert rows[0]["createdInMode"] == "open"
        assert isinstance(rows[0]["createdAt"], int)
        assert rows[1]["schedule"] == {"hour": 7, "minute": 30, "weekday": 1}
    finally:
        _shutdown(h.reader, h.thread)


def test_every_row_says_its_schedule_in_one_plain_sentence(tmp_path):
    """Phase 2's addition to the row (plan §4.2): the schedule in words, said ONCE
    and said by the core.

    Frozen copy, byte-for-byte, and asserted as literals rather than by calling
    ``schedule_sentence`` again — a test that re-renders the sentence it is checking
    would pass no matter what either side said, and the frontend pins these same
    three strings so a reword lands as a red build on both sides rather than as new
    wording in front of a person.

    The third row is the one that matters most: a schedule column that says nothing
    this vocabulary recognises answers the plain "no schedule" line and does not take
    the other two off the list with it. That is a real state — a hand edit, an older
    build, a payload restored from a sidecar.

    Mutation: drop ``"scheduleSentence"`` from ``_automation_wire_row`` — this fails
    on the first row, and the wire-shape test above fails with it."""
    junk = {
        **_INTERVAL,
        "id": "auto-3",
        "label": "com.addison.auto.junk",
        "schedule_json": "every now and then",
        "created_at": 1_700_000_200,
    }
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR, junk)
    try:
        rows = _call(h, "automation.list", {}, 1)["automations"]
        assert [r["scheduleSentence"] for r in rows] == [
            "Every hour",                 # interval, 60 minutes, collapsed
            "Every Monday at 7:30",       # calendar, weekday 1, two-digit minute
            "No schedule saved yet.",     # a column this vocabulary cannot read
        ]
        # ...and the unreadable row is still a row, with everything else intact. One
        # malformed schedule costs itself and nothing else.
        assert rows[2]["schedule"] == {}
        assert rows[2]["command"] == _INTERVAL["command"]
    finally:
        _shutdown(h.reader, h.thread)


def test_the_sentence_and_the_numbers_beside_it_are_made_from_one_value(tmp_path):
    """The words on the row and the numbers on the row are the SAME projection,
    computed once per row — so they cannot disagree.

    This is the assertion that makes that structural rather than hoped-for: the
    sentence a payload carries must be exactly what ``schedule_sentence`` produces
    from the ``schedule`` that payload also carries. A wire row rendered from a
    second read of the column would pass every other test in this file and fail here
    the moment the two reads see different things — which is precisely the case a
    hand-edited or restored row creates.

    The rows below are chosen so a second read would notice: one carries a key the
    projection drops, one an out-of-range weekday the projection KEEPS (it is an
    integer) and the sentence refuses, and one a weekday spelled as a WORD — where
    the projection and a plain ``json.loads`` genuinely part company, the projection
    dropping it to "Every day at 7:30" and the raw column reading "no schedule". A
    surface that printed "Every 9 at 7:30" would be laundering a broken row into
    confident prose about when a command runs.

    Mutation: render the sentence from ``json.loads(row.schedule_json)`` — the
    obvious "I already have the column here" shortcut — and the worded-weekday row
    fails, because the payload's words and its numbers stop describing each other."""
    extra_key = {
        **_CALENDAR,
        "id": "auto-3",
        "label": "com.addison.auto.extra",
        "schedule_json": json.dumps({"hour": 7, "minute": 30, "note": "run this first"}),
        "created_at": 1_700_000_200,
    }
    bad_weekday = {
        **_CALENDAR,
        "id": "auto-4",
        "label": "com.addison.auto.weekday",
        "schedule_json": json.dumps({"hour": 7, "minute": 30, "weekday": 9}),
        "created_at": 1_700_000_300,
    }
    worded_weekday = {
        **_CALENDAR,
        "id": "auto-5",
        "label": "com.addison.auto.worded",
        "schedule_json": json.dumps({"hour": 7, "minute": 30, "weekday": "Monday"}),
        "created_at": 1_700_000_400,
    }
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR, extra_key, bad_weekday, worded_weekday)
    try:
        rows = _call(h, "automation.list", {}, 1)["automations"]
        for row in rows:
            assert row["scheduleSentence"] == schedule_sentence(
                row["scheduleKind"], row["schedule"]
            ), row["id"]
        # The dropped key leaves an every-day schedule behind, not a broken one...
        assert rows[2]["schedule"] == {"hour": 7, "minute": 30}
        assert rows[2]["scheduleSentence"] == "Every day at 7:30"
        # ...and a weekday no calendar has is refused in words, though the number
        # itself survives the projection (it is an integer, and the projection is a
        # field filter, not a validator).
        assert rows[3]["schedule"] == {"hour": 7, "minute": 30, "weekday": 9}
        assert rows[3]["scheduleSentence"] == "No schedule saved yet."
        # ...while a weekday spelled as a word never reaches the sentence at all: the
        # projection drops it for not being a number, so what the person reads is the
        # every-day schedule the row actually has.
        assert rows[4]["schedule"] == {"hour": 7, "minute": 30}
        assert rows[4]["scheduleSentence"] == "Every day at 7:30"
    finally:
        _shutdown(h.reader, h.thread)


def test_the_rpc_layer_cannot_put_a_plist_on_the_wire():
    """THE SHELL NEVER TAKES A DOCUMENT FROM THE CORE (plan §5.8), so no payload may
    normalise carrying one.

    ``agent_core/automations.py`` builds a plist preview, and it is a good thing to
    have: the person reads exactly what would be handed to the OS before they arm it.
    But it is a PREVIEW for a human, not an artifact for a machine — phase 3's shell
    surface takes typed fields and assembles the XML itself, enforcing the label
    prefix and its one directory. A shell surface that accepted markup would be
    ``run_command`` with extra steps, and the first step towards one is a payload
    that already contains the markup: at that point "just pass it through" is a
    one-line change nobody would think to question.

    So the rule is structural and it is at the boundary, not at the shell: the module
    that builds automation payloads cannot import the preview builder, cannot name
    it, and cannot fetch it by string. What it may do is EXPLAIN it — the docstrings
    are read too, and prose about why the document stays here is what should be
    written there. Same instinct as ``test_registration_feeds_the_list_call_into_the
    _catalog_rather_than_discarding_it``: read the source, because the mistake is a
    line that can be quietly added and the behaviour it breaks has no other witness
    until an OS is involved.

    Mutation: add ``from agent_core.automations import plist_text`` to
    rpc/automations.py, or send ``"plist": plist_text(row)`` on the row — either
    fails here."""
    source = _AUTOMATIONS_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "plist_text", (
                    "rpc/automations.py imports plist_text: the preview is for a "
                    "person to read, and the shell builds its own XML from typed "
                    "fields (plan §5.8) — nothing may carry a document across this "
                    "boundary"
                )
        elif isinstance(node, ast.Name):
            assert node.id != "plist_text", f"rpc/automations.py names plist_text at line {node.lineno}"
        elif isinstance(node, ast.Attribute):
            assert node.attr != "plist_text", (
                f"rpc/automations.py reaches automations.plist_text at line {node.lineno}"
            )

    # ...and not by string either, which is the way round an identifier check
    # (``getattr(automations, "plist_text")``). Docstrings are excluded for the
    # reason above; every other literal is checked, exactly as the
    # cannot-reach-a-process pin does it.
    docstrings = _docstring_string_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            assert "plist" not in node.value, (
                f"rpc/automations.py builds a string naming a plist: {node.value!r}"
            )

    # The preview itself is still there and still tested — this pin is about WHERE it
    # may be reached from, never about whether it exists.
    assert callable(plist_text)


def test_two_automations_cannot_share_a_label(store: Store):
    """The label is the plist filename stem, so two rows sharing one would fight over
    a single file in ~/Library/LaunchAgents — and removing either would take out the
    other's job. The UNIQUE constraint is the backstop under whatever phase 2's
    slug-maker does.

    Mutation: drop ``UNIQUE`` from the label column — this fails."""
    store.insert_automation(**_INTERVAL)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_automation(**{**_CALENDAR, "label": _INTERVAL["label"]})


def test_removing_something_that_is_not_there_says_so_and_changes_nothing(tmp_path):
    """A stale surface, a row already removed in another window, or one a restore
    took away. The answer is a plain sentence rather than a cheerful ``ok``, because
    there is a list on screen that should reload rather than tick off a row that has
    moved on — and no restore point is minted, because nothing changed."""
    h = _server_with(tmp_path, _INTERVAL)
    try:
        assert _call(h, "automation.remove", {"id": "nope"}, 1) == {
            "ok": False, "error": _NO_SUCH_AUTOMATION
        }
        assert _call(h, "automation.remove", {}, 2) == {
            "ok": False, "error": _NO_SUCH_AUTOMATION
        }
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 3)["snapshots"]]
        assert "automation_remove" not in reasons
        assert len(_call(h, "automation.list", {}, 4)["automations"]) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_a_removal_is_refused_when_the_restore_point_cannot_be_saved(tmp_path):
    """The ``skill_delete`` class: the command and schedule the person wrote exist
    nowhere else once the row is gone, so losing them with no way back is worse than
    refusing. THE ROW SURVIVES — that is the half worth asserting, because a refusal
    that still deleted would be the floor reporting a failure it had already caused.

    Mutation: change ``if not self._snapshot_auto(...)`` to call it and ignore the
    result — this fails, because the automation is gone."""
    h = _server_with(tmp_path, _INTERVAL)
    try:
        # One request first, so the worker has built the store and the manager.
        assert len(_call(h, "automation.list", {}, 1)["automations"]) == 1
        # The capture path fails from here on.
        h.server.snapshot_manager.capture = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no restore point")
        )
        result = _call(h, "automation.remove", {"id": "auto-1"}, 2)
        assert result["ok"] is False
        assert "didn't remove anything" in result["error"]
        assert [a["id"] for a in _call(h, "automation.list", {}, 3)["automations"]] == ["auto-1"]
        # ...and it is still in the database, not merely still in a payload.
        side = Store(tmp_path / IPC_DB_NAME)
        try:
            assert [a.id for a in side.list_automations()] == ["auto-1"]
        finally:
            side.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_no_payload_on_this_surface_can_carry_a_secret():
    """G1, structurally: an automation stores no credential — a secret belongs in the
    OS keychain — and this table is snapshot-captured, so anything that landed here
    would be copied into every later payload and sidecar in plain text.

    A ``command`` can of course be written to contain one, which is why phase 2's
    authoring tool checks the stored text for secret shapes at the door. What this
    test holds is the narrower line: no FIELD on this surface is for a secret.

    Mutation: add a ``token``/``apiKey`` field to the row — this fails."""
    tree = ast.parse(_AUTOMATIONS_SRC.read_text(encoding="utf-8"))
    secret_names = {"token", "api_key", "apiKey", "secret", "password", "authorization", "header"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in secret_names, (
                f"rpc/automations.py names a secret field: {node.value}"
            )
        elif isinstance(node, ast.Name):
            assert node.id not in secret_names
    assert "automations" in _SCHEMA_SRC.read_text(encoding="utf-8")


# ===========================================================================
# The PREVIEW itself — what `plist_text` actually emits (added by the phase-2
# review, 2026-08-07).
# ===========================================================================
# THESE TESTS EXIST BECAUSE THE ONES ABOVE DID NOT COVER THE FUNCTION AT ALL, and
# the way they failed to is this repo's signature failure mode rather than an
# oversight worth one line. `plist_text`'s only caller-side assertion was
#
#     assert f"```\n{plist_text(row)}```" in text
#
# where `text` is the tool answer that BUILDS that block by calling `plist_text`.
# That compares the function against itself: it holds no matter what the function
# emits. Three mutations were run against the whole suite and all three passed
# 1449 tests green — `minutes * 60` -> `* 30` (a job at twice the frequency the
# person approved), a misspelt `StartCalendarInterval` (a job that never fires),
# and dropping `_xml_escape` from the command (below). The function's own
# docstring calls that escaping "load-bearing" while nothing exercised it.
#
# WHY THE PREVIEW IS WORTH REAL TESTS, given it arms nothing today: it is what a
# person READS before arming, and phase 3's whole ceremony is built on their having
# read it. A preview that misstates the schedule, or that renders a command's
# characters as document structure, is a preview that describes a different job
# from the one that would run — which is the defect the ceremony cannot catch,
# because the ceremony's evidence IS this string. Phase 3 adds a lockstep test
# against the shell's own builder; these are the assertions that give that
# comparison a fixed side.


def _automation_row(**overrides) -> Automation:
    """An ``Automation`` for the preview tests, defaulting to the smallest row that
    renders. Every field the test under it cares about is passed explicitly, so a
    default changing here can never quietly become the thing an assertion relies
    on."""
    fields = {
        "id": "auto-preview",
        "name": "Tidy up",
        "label": "com.addison.auto.tidy",
        "command": "echo hi",
        "schedule_kind": "interval",
        "schedule_json": json.dumps({"minutes": 30}),
        "created_in_mode": "open",
        "created_at": 1_700_000_000,
        "updated_at": 1_700_000_000,
    }
    fields.update(overrides)
    return Automation(**fields)


def test_the_preview_states_the_interval_in_seconds_launchd_expects():
    """`StartInterval` is SECONDS and the stored field is MINUTES, so the ×60 is a
    real conversion with a real failure mode: a job that runs at some other
    frequency than the one the person read and approved.

    Mutation: `minutes * 60` -> `* 30` (or `* 1`) in ``plist_text``."""
    row = _automation_row(schedule_kind="interval", schedule_json='{"minutes": 30}')
    text = plist_text(row)
    assert "<key>StartInterval</key>" in text
    assert "<integer>1800</integer>" in text
    # ...and the calendar trigger is NOT also emitted: one schedule, one trigger.
    assert "StartCalendarInterval" not in text


def test_the_preview_states_a_calendar_time_the_way_launchd_reads_one():
    """`StartCalendarInterval` is a dict of Hour/Minute/Weekday integers, and the
    KEY SPELLINGS are load-bearing: launchd ignores a key it does not recognise, so
    a typo produces a plist that loads cleanly and never fires — the failure that
    looks like success until the day somebody notices nothing ran.

    Weekday rides through as the stored number (0 = Sunday, launchd's own
    convention — ``WEEKDAY_NAMES`` owns why the two agree).

    Mutation: misspell `StartCalendarInterval`, `Hour`, `Minute` or `Weekday`."""
    row = _automation_row(
        schedule_kind="calendar", schedule_json='{"hour": 7, "minute": 30, "weekday": 1}'
    )
    text = plist_text(row)
    assert "<key>StartCalendarInterval</key>" in text
    for key, value in (("Hour", 7), ("Minute", 30), ("Weekday", 1)):
        assert f"<key>{key}</key>\n        <integer>{value}</integer>" in text
    assert "StartInterval" not in text.replace("StartCalendarInterval", "")


def test_a_daily_preview_carries_no_weekday_at_all():
    """An omitted weekday means EVERY day, and the way launchd is told that is by
    the key being absent — a `Weekday` present with any value would pin the job to
    one day. So the difference between "every day at 7:30" and "every Sunday at
    7:30" is one key existing, which is worth its own assertion.

    Mutation: emit `Weekday` unconditionally (e.g. defaulting it to 0)."""
    row = _automation_row(schedule_kind="calendar", schedule_json='{"hour": 7, "minute": 30}')
    text = plist_text(row)
    assert "<key>StartCalendarInterval</key>" in text
    assert "<key>Hour</key>" in text
    assert "Weekday" not in text


def test_a_command_cannot_become_document_structure():
    """THE ONE THAT MATTERS. A command is text inside a `<string>`; a command that
    CONTAINS `</string>` must stay text. Unescaped, the payload below closes the
    element early and the preview grows a `RunAtLoad` key — which is the one key
    plan §5.7 says is never set, because it would make arming cause an immediate
    run. The person would then read a preview describing a job that starts the
    moment it is armed.

    Both fields are attacker-adjacent and both are asserted: the command comes from
    a model that may be relaying instructions it read somewhere, and the label is
    derived from a name that arrived the same way.

    Mutation: drop `_xml_escape` from either `automation.command` or
    `automation.label` in ``plist_text``."""
    hostile = "echo hi</string><key>RunAtLoad</key><true/><string>"
    row = _automation_row(command=hostile, label=f"com.addison.auto.x{hostile}")
    text = plist_text(row)
    # The structure the payload tried to open never appears as structure...
    assert "<key>RunAtLoad</key>" not in text
    assert "<true/>" not in text
    # ...it appears as the characters it is, twice — once per escaped field.
    assert text.count("&lt;key&gt;RunAtLoad&lt;/key&gt;") == 2
    # And the plist still says exactly what it always says about running at load:
    # nothing at all (plan §5.7 — arming never causes an immediate run).
    assert "RunAtLoad" not in text.replace("&lt;key&gt;RunAtLoad&lt;/key&gt;", "")


def test_the_preview_runs_the_command_through_one_shell_and_names_the_job():
    """The shape every branch shares: `/bin/sh -c <command>` — the same contract
    ``run_command`` gives a command everywhere else in Addison, so a person does not
    have to learn which dialect applies where — and the Label, which is the filename
    stem the shell will own at arming time (plan §5.8).

    Mutation: change the interpreter, drop `-c`, or drop the Label key."""
    row = _automation_row(command="echo hi", label="com.addison.auto.tidy")
    text = plist_text(row)
    assert "<string>/bin/sh</string>\n        <string>-c</string>" in text
    assert "<string>echo hi</string>" in text
    assert "<key>Label</key>\n    <string>com.addison.auto.tidy</string>" in text
    # A plist is a document before it is a dict: without the declaration and the
    # DOCTYPE, `plutil` rejects the file and launchd never reads it.
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC ')
    assert text.rstrip().endswith("</plist>")


def test_a_schedule_nobody_can_read_previews_a_job_that_would_never_fire():
    """A row whose schedule this vocabulary cannot read (a hand edit, an older
    build, a restored sidecar) previews with NO trigger — and that is the honest
    rendering, because a plist with no trigger is exactly what launchd would load
    and never run. The preview shows the nothing that would be armed rather than
    inventing a schedule to fill the gap.

    The authoring door is what keeps such a row from being written in the first
    place (``schedule_problem``); this is the renderer being honest about the rows
    that got in some other way.

    Mutation: default the trigger to an interval when the fields are unreadable."""
    row = _automation_row(schedule_kind="interval", schedule_json="every now and then")
    text = plist_text(row)
    assert "StartInterval" not in text
    assert "StartCalendarInterval" not in text
    # Still a valid, complete document naming the command — it just never fires.
    assert "<string>echo hi</string>" in text
    assert text.rstrip().endswith("</plist>")
    # ...and the sentence beside it says the same thing in words, so the two
    # renderings of one broken row agree.
    assert schedule_sentence("interval", schedule_fields("interval", "every now and then")) == (
        "No schedule saved yet."
    )


# ===========================================================================
# The two renderings of one row agree (phase-2 review, 2026-08-07).
# ===========================================================================


@pytest.mark.parametrize(
    "kind,stored",
    [
        ("interval", '{"minutes": 0}'),
        ("interval", '{"minutes": -5}'),
        ("calendar", '{"hour": 99, "minute": 88}'),
        ("calendar", '{"hour": 7, "minute": 30, "weekday": 9}'),
    ],
)
def test_a_row_the_words_call_unreadable_previews_no_trigger_either(kind, stored):
    """THE DEFECT THIS PINS: ``plist_text`` tested the PRESENCE of the schedule
    fields while ``schedule_sentence`` tested their BOUNDS, so every row below
    rendered "No schedule saved yet." in words beside a preview showing a
    fully-formed launchd trigger — ``StartInterval 0``, ``Hour 99``, ``Weekday 9``.

    One row, two renderings, contradicting each other. For a preview whose whole
    job is to be the thing somebody read before arming (plan §3), whichever one
    they believed the other was there to disprove. Both now ask
    ``schedule_is_readable``.

    Mutation: give ``plist_text`` back its own presence test
    (``"minutes" in schedule``), or drop the ``schedule_is_readable`` call."""
    row = _automation_row(schedule_kind=kind, schedule_json=stored)
    fields = schedule_fields(kind, stored)
    assert schedule_sentence(kind, fields) == NO_SCHEDULE
    text = plist_text(row)
    assert "StartInterval" not in text
    assert "StartCalendarInterval" not in text


def test_every_schedule_the_words_can_read_is_a_schedule_the_preview_arms():
    """The other direction, so the pair cannot be satisfied by refusing everything:
    a readable schedule must produce BOTH a real sentence and a real trigger."""
    for kind, stored, expect_key in [
        ("interval", '{"minutes": 30}', "StartInterval"),
        ("interval", '{"minutes": 10080}', "StartInterval"),
        ("calendar", '{"hour": 0, "minute": 0}', "StartCalendarInterval"),
        ("calendar", '{"hour": 23, "minute": 59, "weekday": 6}', "StartCalendarInterval"),
    ]:
        fields = schedule_fields(kind, stored)
        assert schedule_is_readable(kind, fields) is True, stored
        assert schedule_sentence(kind, fields) != NO_SCHEDULE, stored
        assert expect_key in plist_text(_automation_row(schedule_kind=kind, schedule_json=stored))


def test_the_longest_gap_addison_will_write_reads_as_days():
    """"Every 168 hours" was what a week rendered as — and a week is exactly the
    longest interval the authoring door accepts (``MAX_INTERVAL_MINUTES``), so the
    least legible sentence in the vocabulary was the one at its own boundary. For
    personas 54 and 68 that is not a schedule anybody can check at a glance.

    Mutation: remove the ``% 1440`` branch from ``schedule_sentence``."""
    assert schedule_sentence("interval", {"minutes": 10080}) == "Every 7 days"
    assert schedule_sentence("interval", {"minutes": 1440}) == "Every day"
    assert schedule_sentence("interval", {"minutes": 2880}) == "Every 2 days"
    # ...and the shorter forms are untouched: hours still read as hours, and a gap
    # that is not a whole number of days or hours still reads in minutes.
    assert schedule_sentence("interval", {"minutes": 120}) == "Every 2 hours"
    assert schedule_sentence("interval", {"minutes": 90}) == "Every 90 minutes"
    assert schedule_sentence("interval", {"minutes": 60}) == "Every hour"
