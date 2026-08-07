"""Automations — step 8, PHASE 1 (docs/step-8-automation-plan.md §4.1).

Phase 1 ships the row and the inert surface: the ``automations`` table, the
``automation.list``/``automation.remove`` RPC, the snapshot reason slugs, and
nothing else. **There is no way to create an automation and no way to arm one.**
Authoring is phase 2 and arming is phase 3, so on every install today the table is
empty and the list answers ``[]``.

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

from agent_core.automations import SCHEDULE_FIELDS, SCHEDULE_KINDS, schedule_fields
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
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
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
                "createdInMode", "createdAt",
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
