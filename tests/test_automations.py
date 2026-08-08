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
      capability and a tightening must never be trapped by a profile switch — and
      (phase 4) Simple gets those rows LISTED AND DISABLED, carrying the sentence
      that says why, decided without ever asking a row where it was born;
  (6) every ``automation.*`` method is answered ON THE WORKER THREAD.

Every test here was mutation-proven: the line it guards was broken and this test
watched to fail. The mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from agent_core.automations import (
    LABEL_PREFIX,
    MAX_SLUG_CHARS,
    NO_SCHEDULE,
    SCHEDULE_FIELDS,
    SCHEDULE_KINDS,
    Automation,
    label_is_addisons_own,
    plist_text,
    schedule_fields,
    derive_label,
    schedule_is_readable,
    schedule_sentence,
)
from agent_core.memory.store import Store
from agent_core.rpc.automations import (
    _COULDNT_DISARM_ORPHAN,
    _NO_SHELL_TO_DISARM,
    _NO_SUCH_AUTOMATION,
    _NOT_ADDISONS_OWN,
    _SAVED_AGAIN,
)
from agent_core.snapshots.scope import _CAPTURED_TABLES
from agent_core.snapshots.snapshot_manager import REASONS
from tests.conftest import IPC_DB_NAME, ShellBridgeStubs, _shutdown, build_server

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
# A row stamped ``safe``. No tool can write one — ``create_automation`` is OPEN-only —
# but a hand-edited database, an older build or a restored payload can, and the phase-4
# tests below need one: it is the row that TELLS THE TWO QUESTIONS APART. Availability
# read off the stamp would call this one usable in Simple (it was "born safe"), and the
# person would get a Run-shaped row for a shell command in the profile that has no shell.
_SAFE_STAMPED = {
    "id": "auto-3",
    "name": "Say the time",
    "label": "com.addison.auto.say-the-time",
    "command": "/usr/bin/say the time",
    "schedule_kind": "interval",
    "schedule_json": json.dumps({"minutes": 30}),
    "created_in_mode": "safe",
    "created_at": 1_700_000_200,
}

# The frozen phase-4 sentence and slug, as literals. Written out here rather than
# imported from rpc/constants.py on purpose: a test that asserts a payload equals the
# constant the payload was built from passes whatever either of them says. The frontend
# pins this same string, so a reword lands as a red build on both sides rather than as
# new wording in front of somebody.
_DISABLED_IN_SIMPLE = {
    "reason": "developer_abilities",
    "message": "That automation runs a command, so it's waiting in Developer profile.",
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


def _server_with(tmp_path, *rows: dict, bridge=None):
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

    return build_server(tmp_path, register_tool=False, store_factory=factory, bridge=bridge)


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
    # Phase 3 added `automation.status` — a READ, and one that asks the operating
    # system rather than the store (plan §5.6). 2026-08-08 added
    # `automation.disarmOrphan`, which STOPS one: the orphan a G3 restore leaves
    # behind has no row, so `remove` and the disarm TOOL both refuse it and nothing
    # could reach it (KNOWN-GAPS, closed). Still no add and still no arm: writing a
    # row is the `create_automation` TOOL and switching one ON is the
    # `arm_automation` TOOL, both gated and audited, neither reachable from this
    # namespace — and the method whose name contains "disarm" is the direction that
    # is safe to reach without one.
    assert named == {
        "automation.list",
        "automation.remove",
        "automation.status",
        "automation.disarmOrphan",
    }

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


def test_this_namespace_may_only_ask_the_shell_to_STOP_things():
    """THE ONE-DIRECTION RULE, as an allowlist rather than a hope.

    This module DOES cross the shell bridge — `_disarm_before_forgetting` (phase 3's
    review fix) and `_automation_disarm_orphan` (2026-08-08) — and both are
    TIGHTENINGS: they ask the OS what it holds and ask it to stop something. The
    bridge sitting on `self._shell_bridge` also carries `arm_automation`, so what
    stands between this namespace and an install is nothing but the fact that nobody
    has typed it. This test is that fact, enforced.

    Read as an ALLOWLIST against the bridge's OWN method set, so it cannot go stale:
    a method added to the bridge is covered the moment it exists, and the assertion
    names the two this module may say. Docstrings are excluded for the reason the
    scan above gives — prose explaining why arming lives in the shell is exactly what
    belongs here, and this test reads ATTRIBUTES, which prose cannot be.

    Mutation: add ``bridge.arm_automation(label, command, kind, schedule)`` anywhere
    in rpc/automations.py — this fails, naming the method."""
    from agent_core.shell_bridge import ServerShellBridge
    from agent_core.tools.base import ShellBridge

    bridge_methods = {
        name
        for name in set(dir(ShellBridge)) | set(dir(ServerShellBridge))
        if not name.startswith("_")
    }
    assert "arm_automation" in bridge_methods, "the bridge no longer has an arm — did it move?"

    tree = ast.parse(_AUTOMATIONS_SRC.read_text(encoding="utf-8"))
    named = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } & bridge_methods
    assert named == {"list_armed", "disarm_automation"}, (
        f"rpc/automations.py names {sorted(named)} on the shell bridge: this namespace "
        "may ask the operating system what it holds and ask it to STOP something, and "
        "nothing else — arming is a gated, audited TOOL behind a typed code (G2)"
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
# (5b) ...and in Simple they are listed DISABLED, saying why — phase 4
# ---------------------------------------------------------------------------


def test_simple_lists_every_automation_disabled_and_says_why(tmp_path):
    """The artifact rule (docs/SAFETY.md), applied to a table where it is UNIFORM:
    an automation's payload is a shell command, so every row is waiting for Developer
    and every row says so, in the one sentence rpc/constants.py holds for both the
    surface and the refusal.

    The third row is the load-bearing one. It is stamped ``safe`` — which no tool can
    produce, but a hand edit, an older build or a restored payload can — and it is
    marked exactly like the other two, because what decides this is what an automation
    IS, not where it was born. Read off the stamp (the routines bug this file was
    written beside, closed 2026-08-08) this row would arrive in Simple looking usable.

    Frozen copy, asserted as a literal rather than against the constant it is built
    from: a test that compares a payload to its own source passes whatever that source
    says, and the frontend pins this same sentence.

    Mutations: (a) drop the ``if unavailable is not None`` block from
    ``_automation_wire_row`` — this fails on the first row; (b) pass
    ``row.created_in_mode == PolicyMode.OPEN.value`` as the decision, the routines
    bug — this fails on the ``safe``-stamped row alone; (c) reword
    ``_AUTOMATION_DEV_ABILITIES_MESSAGE`` — this fails on the sentence."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR, _SAFE_STAMPED)
    try:
        rows = _call(h, "automation.list", {}, 1)["automations"]
        assert [r["id"] for r in rows] == ["auto-1", "auto-2", "auto-3"]
        for row in rows:
            assert row["unavailable"] == _DISABLED_IN_SIMPLE, row["id"]
        # The stamp still rides along as display provenance — it is a badge, and this
        # test is not asking for it to be removed. It is asking for it to be ignored.
        assert [r["createdInMode"] for r in rows] == ["open", "open", "safe"]
    finally:
        _shutdown(h.reader, h.thread)


def test_developer_and_custom_list_the_same_rows_with_no_marker_at_all(tmp_path):
    """Both OPEN profiles, and the key is ABSENT rather than present-and-null — the
    shape every existing parser already reads, byte-for-byte. A ``"unavailable": null``
    would be a new key on a row nothing is wrong with, and a frontend that rendered
    truthiness would be right by accident until somebody sent it ``{}``.

    Custom is listed here because it derives OPEN exactly as Developer does
    (policy.mode_for_profile) and its guards are prompting guards, never floors: an
    automation is no more disabled there than it is in Developer.

    Mutation: hard-code ``PolicyMode.SAFE`` in ``_automation_list`` instead of calling
    ``self._mode()`` — this fails in both halves."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR, _SAFE_STAMPED)
    try:
        _developer(h)
        for row in _call(h, "automation.list", {}, 1)["automations"]:
            assert "unavailable" not in row, row["id"]
        assert _call(h, "profile.set", {"profileId": "custom"}, 2)["mode"] == "open"
        rows = _call(h, "automation.list", {}, 3)["automations"]
        assert [r["id"] for r in rows] == ["auto-1", "auto-2", "auto-3"]
        for row in rows:
            assert "unavailable" not in row, row["id"]
    finally:
        _shutdown(h.reader, h.thread)


def test_the_marker_is_the_only_thing_a_profile_switch_changes(tmp_path):
    """Two claims in one, because they are the same claim from either end.

    THE SHAPE IS NOT PERTURBED: strip ``unavailable`` from the Simple rows and what is
    left is the Developer payload, key for key and value for value. A display marker
    that also moved a schedule, dropped a command or reordered the list would be a
    profile switch quietly rewriting somebody's configuration.

    AND IT TAKES EFFECT WITH NO RESTART, in both directions: the same running server,
    the same rows, three answers. ``_mode()`` is derived from the live active profile
    on every call, so the switch is visible on the very next list — and switching back
    brings the marker back, which is what makes the disabling a display state rather
    than a latch.

    Mutation: cache the mode on the instance (``self._cached_mode = self._mode()``
    computed once) — this fails at the second listing, while every other test in this
    file still passes."""
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR, _SAFE_STAMPED)
    try:
        simple_rows = _call(h, "automation.list", {}, 1)["automations"]
        _developer(h)
        dev_rows = _call(h, "automation.list", {}, 2)["automations"]
        assert len(simple_rows) == len(dev_rows) == 3
        for before, after in zip(simple_rows, dev_rows, strict=True):
            assert set(before) == set(after) | {"unavailable"}
            assert {k: v for k, v in before.items() if k != "unavailable"} == after
        # ...and back again, on the same server, with no restart in between.
        assert _call(h, "profile.set", {"profileId": "simple"}, 3)["mode"] == "safe"
        again = _call(h, "automation.list", {}, 4)["automations"]
        assert again == simple_rows
    finally:
        _shutdown(h.reader, h.thread)


def test_a_disabled_automation_is_still_removable_in_simple(tmp_path):
    """A TIGHTENING IS NEVER TRAPPED — the phase-1 rule, restated against the thing
    phase 4 added that could break it. The obvious way to implement "disabled" is to
    refuse the row's methods too, and that would leave somebody looking at a command
    they no longer want, told it is unavailable, with no way to be rid of it short of
    turning on the profile they were avoiding.

    Mutation: return the ``unavailable`` sentence as an error from
    ``_automation_remove`` when the mode is SAFE — this fails."""
    h = _server_with(tmp_path, _INTERVAL, _SAFE_STAMPED)
    try:
        rows = _call(h, "automation.list", {}, 1)["automations"]
        assert all("unavailable" in row for row in rows)
        assert _call(h, "automation.remove", {"id": "auto-1"}, 2)["ok"] is True
        assert _call(h, "automation.remove", {"id": "auto-3"}, 3)["ok"] is True
        assert _call(h, "automation.list", {}, 4)["automations"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_this_surface_never_asks_a_row_where_it_was_born():
    """THE STRUCTURAL HALF, and the reason this file has one: the behavioural tests
    above stay green under a stamp-reading implementation for two rows out of three,
    because a row stamped ``open`` gets the right answer for the wrong reason. What
    must hold is stronger than the payload — this module must not ASK the question at
    all.

    So two pins. The decision handed to ``_unavailable_marker`` is a LITERAL ``True``:
    every automation runs a shell command, so there is nothing to ask a row, and a
    decision that asks nothing cannot drift into asking the wrong thing. And no
    branch, comparison or boolean operator anywhere in the module names
    ``created_in_mode`` — the stamp reaches the wire as display provenance
    (``createdInMode``) and is read for nothing else.

    ``rpc/routines.py`` is the module this one was written to stay out of: a
    search-only routine saved in Developer was listed disabled in Simple and refused
    at dispatch, both wrongly, until that was closed on 2026-08-08. Routines have a
    genuine per-row question and now ask it of the routine
    (``_routine_needs_dev``, pinned by its own scan in tests/test_routines.py); this
    table has none, so the literal below is the stronger shape and stays.

    Mutations: pass ``row.created_in_mode == PolicyMode.OPEN.value`` as the decision —
    this fails on the literal-``True`` pin; or keep the ``True`` and clear the marker
    behind ``if row.created_in_mode == "safe"`` — this fails on the branch scan, which
    is why one pin was not enough."""
    tree = ast.parse(_AUTOMATIONS_SRC.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_unavailable_marker"
    ]
    assert len(calls) == 1, "expected exactly one unavailability decision in this module"
    decision = calls[0].args[1] if len(calls[0].args) > 1 else None
    assert isinstance(decision, ast.Constant) and decision.value is True, (
        "rpc/automations.py asks something per row to decide availability: every "
        "automation's payload is a shell command, so the answer is a decided True and "
        "there is no per-row question for a stamp to sneak into (docs/KNOWN-GAPS.md)"
    )

    stamp = {"created_in_mode", "createdInMode"}
    decisions: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If | ast.IfExp | ast.While):
            decisions.append(node.test)
        elif isinstance(node, ast.Compare | ast.BoolOp):
            decisions.append(node)
    for node in decisions:
        for inner in ast.walk(node):
            named = (
                (isinstance(inner, ast.Attribute) and inner.attr in stamp)
                or (isinstance(inner, ast.Name) and inner.id in stamp)
                or (
                    isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and inner.value in stamp
                )
            )
            assert not named, (
                f"rpc/automations.py branches on created_in_mode at line {node.lineno}: "
                "the stamp records where a row was BORN and can never decide what it "
                "NEEDS — the mistake the routines half made, docs/KNOWN-GAPS.md"
            )

    # ...and it is read exactly once, where it becomes the display-only badge.
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "created_in_mode"
    ]
    assert len(reads) == 1, "the stamp is read once, to put it on the wire, and never again"


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

    Phase 4 added the ONE key that is profile-dependent — ``unavailable``, present on
    every row while Simple is active and absent entirely otherwise — so the shape is
    pinned in both profiles here, and section (5b) owns what it says.

    Mutation: send ``row.schedule_json`` instead of the projection, or rename a key —
    this fails."""
    base = {
        "id", "name", "label", "command", "scheduleKind", "schedule",
        "scheduleSentence", "createdInMode", "createdAt",
    }
    h = _server_with(tmp_path, _INTERVAL, _CALENDAR)
    try:
        # Simple (the default): the base row plus the disabled marker, and nothing else.
        for row in _call(h, "automation.list", {}, 1)["automations"]:
            assert set(row) == base | {"unavailable"}
        _developer(h)
        rows = _call(h, "automation.list", {}, 2)["automations"]
        assert [r["id"] for r in rows] == ["auto-1", "auto-2"]
        for row in rows:
            assert set(row) == base
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


# ===========================================================================
# The two plist builders agree, across the language boundary (step 8 phase 3).
# ===========================================================================
# THE PROMISE THIS KEEPS is the one the whole keyword gate rests on: "the preview
# you approved". The person reads `automations.plist_text`'s output on the arming
# card; the SHELL then builds its own document from typed fields and hands THAT to
# launchd (plan §5.8 — the core never sends markup, so the two are separate
# implementations by design). If they can differ, the ceremony is theatre: somebody
# would be reading one job and arming another.
#
# There is no codegen and no runtime handshake, so this is the same deal
# `protocol.py`/`protocol.ts` and `OS_AUTOMATION_DIRS` have — a hand-synced contract
# asserted on ONE side is asserted on neither. The Rust tests pin their side with
# byte-exact `concat!` literals; this reads those literals out of the source and
# compares them against what Python emits for the same row.


def _rust_expected_plists() -> list[str]:
    """Every byte-exact plist literal the Rust tests pin, reassembled from the
    `concat!("…", "…")` blocks in ``shell/src-tauri/src/automation.rs``."""
    source = (
        _REPO_ROOT / "shell" / "src-tauri" / "src" / "automation.rs"
    ).read_text(encoding="utf-8")
    blocks = re.findall(r"concat!\(\s*\n(.*?)\n\s*\)", source, re.S)
    plists: list[str] = []
    for block in blocks:
        # Each line is `    "…",` with Rust escapes. Only blocks that are a whole
        # document interest us — the builder's own `concat!`s are fragments.
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
        if not pieces:
            continue
        text = "".join(
            piece.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
            for piece in pieces
        )
        # WHOLE documents only. The builder itself uses `concat!` for the header
        # fragment, which also starts with `<?xml` — comparing that against a
        # complete plist would fail for a reason that is not drift.
        if text.startswith("<?xml") and text.rstrip().endswith("</plist>"):
            plists.append(text)
    return plists


def test_the_shell_builds_the_same_plist_the_person_was_shown():
    """The lockstep. Each document the Rust side pins must be exactly what
    ``plist_text`` produces for the row it describes — same declaration, DOCTYPE,
    key order, indentation and escaping, to the byte.

    The rows below are the ones the Rust tests use, so a change on either side
    lands here as a diff rather than as two builders quietly drifting.

    Mutation: change any of ``plist_text``'s literals — the indentation, a key
    name, the `/bin/sh -c` pair, the ×60 — or the matching line in the Rust
    builder. Either direction fails this."""
    rust = _rust_expected_plists()
    assert len(rust) >= 3, (
        "automation.rs no longer pins whole plists as concat! literals — the "
        "lockstep has lost its fixed side; re-anchor this reader before trusting it"
    )
    rows = [
        _automation_row(schedule_kind="interval", schedule_json='{"minutes": 30}'),
        _automation_row(
            schedule_kind="calendar", schedule_json='{"hour": 7, "minute": 30, "weekday": 1}'
        ),
        _automation_row(schedule_kind="calendar", schedule_json='{"hour": 7, "minute": 5}'),
    ]
    python = {plist_text(row) for row in rows}
    unmatched = [text for text in rust if text not in python]
    assert not unmatched, (
        "the shell pins a plist the core's preview does not produce — somebody "
        "would read one job and arm another:\n\n" + "\n---\n".join(unmatched)
    )
    # ...and the pairing is real rather than vacuous: every row we built is pinned
    # on the Rust side too, so this cannot pass by the Rust side pinning nothing.
    assert python <= set(rust), (
        "the core previews a plist the shell does not pin — add the case to "
        "automation.rs's tests so both sides stay fixed"
    )


def test_neither_builder_will_ever_set_run_at_load():
    """Arming must never cause an immediate run (plan §5.7): the first execution
    happens on the OS's own schedule, which is what keeps "Addison never triggers
    itself" clean even at the moment of installation. Pinned on BOTH sides here,
    because a key absent from one document and present in the other is exactly the
    divergence the lockstep above exists to catch — and this one would run a
    stranger's command the instant somebody typed the code."""
    for row in (
        _automation_row(schedule_kind="interval", schedule_json='{"minutes": 30}'),
        _automation_row(schedule_kind="calendar", schedule_json='{"hour": 0, "minute": 0}'),
    ):
        assert "RunAtLoad" not in plist_text(row)
    for text in _rust_expected_plists():
        assert "RunAtLoad" not in text


# ===========================================================================
# Removing an armed automation switches it off first (adversarial review, phase 3).
# ===========================================================================
# THE DEFECT: `automation.remove` deleted the row and left the job running. After
# that, `disarm_automation` answered "that automation isn't saved any more, so there
# was nothing to turn off" while the computer ran it every hour, and the Automations
# surface renders armed-ness PER ROW — so with no row there was nothing to render.
# A running job nobody could see and nobody could stop, produced by pressing Remove.
#
# The order was specified in phase 1's own docstring ("the OS first, the record
# second, so a failure can never leave a job running with nothing on screen that
# names it") and phase 3 shipped without honouring it.


class _ArmedBridge(ShellBridgeStubs):
    """A shell that reports one armed label and records what it was asked to do.

    Inherits the stubs so it is a WHOLE bridge — every other method raises, which is
    what makes "the orphan path asked the shell for exactly one thing" observable
    rather than assumed."""

    def __init__(self, armed: list[str], *, supported: bool = True, fail: bool = False):
        self._armed = list(armed)
        self._supported = supported
        self._fail = fail
        self.disarmed: list[str] = []

    def list_armed(self) -> dict:
        return {"armed": list(self._armed), "supported": self._supported}

    def disarm_automation(self, label: str) -> dict:
        self.disarmed.append(label)
        if self._fail:
            return {"ok": False, "error": "the scheduler didn't answer"}
        self._armed = [a for a in self._armed if a != label]
        return {"ok": True}


def _remover(bridge) -> type:
    """A bare AutomationsMixin with just enough server around it to call the two
    methods under test — the disarm-before-remove rule is about ORDER, and a live
    IPC server would only make the order harder to see."""
    from agent_core.rpc.automations import AutomationsMixin

    class _Server(AutomationsMixin):
        def __init__(self, store, shell_bridge):
            self._store = store
            self._shell_bridge = shell_bridge
            self.captured: list[str] = []

        @property
        def store(self):
            return self._store

        def _ensure_built(self) -> None:
            return None

        def _snapshot_auto(self, reason: str) -> bool:
            self.captured.append(reason)
            return True

    return _Server


def test_removing_an_armed_automation_switches_it_off_before_forgetting_it(store: Store):
    """The row is what makes a running job nameable and its Disarm button reachable,
    so the job goes off BEFORE the row goes away.

    Mutation: delete the ``_disarm_before_forgetting`` call from
    ``_automation_remove`` — the job stays armed with no row naming it."""
    store.insert_automation(**_INTERVAL)
    bridge = _ArmedBridge([_INTERVAL["label"]])
    server = _remover(bridge)(store, bridge)

    assert server._automation_remove({"id": _INTERVAL["id"]}) == {"ok": True}
    # Switched off first, then forgotten — and the snapshot still happened.
    assert bridge.disarmed == [_INTERVAL["label"]]
    assert bridge.list_armed()["armed"] == []
    assert store.list_automations() == []
    assert server.captured == ["automation_remove"]


def test_a_removal_that_cannot_switch_the_job_off_is_refused_and_keeps_the_row(store: Store):
    """"I could not switch it off" and "there was nothing to switch off" are the two
    answers that must never be collapsed. Refusing keeps the row — which is the only
    thing that can name the job on a surface or reach it with a Disarm.

    Mutation: return True from ``_disarm_before_forgetting``'s failure branch."""
    store.insert_automation(**_INTERVAL)
    bridge = _ArmedBridge([_INTERVAL["label"]], fail=True)
    server = _remover(bridge)(store, bridge)

    answer = server._automation_remove({"id": _INTERVAL["id"]})
    assert answer["ok"] is False
    assert "still running it" in answer["error"]
    # The row survives, so the person can still see it and press Disarm.
    assert [row.id for row in store.list_automations()] == [_INTERVAL["id"]]
    # A restore point WAS minted, and that is the corrected order (phase-4 review).
    # It used to disarm first and capture second, so a failed capture answered "it
    # didn't remove anything" AFTER the job had been switched off — false about the
    # one thing the person was watching, with no snapshot and no undo behind it.
    # Minting first costs a restore point on a removal that then refuses; that is
    # an extra way back, which is the cheap direction to be wrong in.
    assert server.captured == ["automation_remove"]


def test_a_row_the_os_is_not_holding_is_removed_without_ceremony(store: Store):
    """The common case must not grow a round-trip's worth of new ways to fail: a
    draft that was never armed is removed exactly as it was before, and nothing is
    asked to disarm."""
    store.insert_automation(**_INTERVAL)
    bridge = _ArmedBridge([])
    server = _remover(bridge)(store, bridge)

    assert server._automation_remove({"id": _INTERVAL["id"]}) == {"ok": True}
    assert bridge.disarmed == []
    assert store.list_automations() == []


def test_where_arming_does_not_exist_removal_is_untouched(store: Store):
    """Off macOS the shell says arming is unsupported, so there is nothing to hold
    and nothing to switch off. That is the honest reading of "nothing is armed
    here" — not a bypass, and the one case where a `False` from the bridge is a
    legitimate green light."""
    store.insert_automation(**_INTERVAL)
    bridge = _ArmedBridge([_INTERVAL["label"]], supported=False)
    server = _remover(bridge)(store, bridge)

    assert server._automation_remove({"id": _INTERVAL["id"]}) == {"ok": True}
    assert bridge.disarmed == []


def test_every_label_the_core_can_mint_is_one_the_shell_accepts():
    """THE SECOND HALF OF THE LOCKSTEP, and the one the plist comparison could not
    see: the two sides must agree about LABELS as well as documents.

    They did not (adversarial review, 2026-08-07). `_slug` caps the stem at
    `MAX_SLUG_CHARS`; `derive_label` then appended "-2" ON TOP, producing 41-43
    characters, and the shell — which validates the label itself and caps at the
    same 40, deliberately not trusting the core — refused it. A second automation
    with a long name authored fine, previewed fine, showed its code, and failed the
    instant the person typed it, with a sentence blaming Addison's own naming.

    The rule is read out of `automation.rs` rather than restated, so a change to
    either cap lands here.

    Mutation: drop the stem trim from ``derive_label``'s suffix loop."""
    source = (
        _REPO_ROOT / "shell" / "src-tauri" / "src" / "automation.rs"
    ).read_text(encoding="utf-8")
    cap = re.search(r"const MAX_STEM_CHARS: usize = (\d+);", source)
    prefix = re.search(r'const LABEL_PREFIX: &str = "([^"]+)";', source)
    assert cap and prefix, "automation.rs no longer states its label rule as constants"
    max_stem, shell_prefix = int(cap.group(1)), prefix.group(1)
    assert shell_prefix == LABEL_PREFIX, "the two sides disagree about the prefix itself"

    # The worst case the core can produce: a name that slugs to exactly the cap,
    # then every suffix on top of it.
    name = "n" * (MAX_SLUG_CHARS + 20)
    taken: list[str] = []
    for _ in range(12):
        label = derive_label(name, taken)
        assert label is not None
        taken.append(label)
    assert len(set(taken)) == len(taken), "the trim made two names collide"
    for label in taken:
        assert label.startswith(LABEL_PREFIX)
        stem = label[len(LABEL_PREFIX) :]
        assert 0 < len(stem) <= max_stem, f"{label} has a {len(stem)}-character stem"
        # ...and the stem is in the shell's alphabet, which is the other half of
        # what `validated_label` checks.
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem), label


# ---------------------------------------------------------------------------
# (8) The ORPHAN — a job the OS holds that no row can reach (2026-08-08)
# ---------------------------------------------------------------------------
# `apply_config_state` is REPLACE-ALL, so restoring a snapshot that predates an
# automation deletes its row while `<label>.plist` stays installed and launchd goes on
# running it at every login. Everything else then refuses: `disarm_automation` (the
# tool) and `automation.remove` both look the row up first, and the Settings section
# renders armed-ness per ROW, so it could not even name the thing. A job nobody can see
# and nobody can stop — phase 3's Remove-path bug, reached through Restore instead
# (KNOWN-GAPS, closed by `automation.disarmOrphan`).
#
# The fix is RECONCILE-ON-RESTORE and deliberately not the two alternatives: a restore
# is never blocked, and nothing is silently disarmed during one. An arming decision
# must not live inside the one action G3 promises is always available.


def test_a_job_the_os_holds_with_no_row_can_be_switched_off(store: Store):
    """THE GAP, CLOSED. Nothing is saved for this label — that is the whole premise —
    and the job still goes off.

    Mutation: make ``_automation_disarm_orphan`` look the row up and refuse when it is
    missing (the shape ``remove`` and the disarm tool have) — this fails, and the job
    stays running with nothing able to name it."""
    orphan = "com.addison.auto.tidy-downloads"
    bridge = _ArmedBridge([orphan])
    server = _remover(bridge)(store, bridge)
    assert store.list_automations() == []

    assert server._automation_disarm_orphan({"label": orphan}) == {"ok": True}
    assert bridge.disarmed == [orphan]
    assert bridge.list_armed()["armed"] == []
    # NO RESTORE POINT. ``remove`` mints one because it deletes a ROW somebody wrote;
    # here there is no row, and what changes is a file in the OS's own folder that no
    # snapshot has ever held or could put back.
    #
    # Mutation: add ``self._snapshot_auto("automation_disarm")`` — this fails.
    assert server.captured == []


@pytest.mark.parametrize(
    "label",
    [
        "com.example.nightly",              # somebody else's launchd job entirely
        "com.addison.auto",                 # the prefix without its final dot
        "com.addison.autotidy",             # the prefix run into the stem
        "com.addison.auto.",                # prefix, no stem
        "com.addison.auto.tidy.plist",      # a dot after the prefix
        "com.addison.auto.../../evil",      # traversal
        "com.addison.auto./etc/passwd",     # a path separator
        "com.addison.auto.Tidy",            # upper case
        "com.addison.auto.-tidy",           # a stem starting with a hyphen
        "com.addison.auto.tidy downloads",  # a space
        "com.addison.auto.tidy\n",          # a trailing newline
        " com.addison.auto.tidy",           # leading whitespace
        "x.com.addison.auto.tidy",          # the prefix buried mid-string
        "com.addison.auto.zálohování",      # non-ASCII
        "com.addison.auto." + "n" * 41,     # one character past the shell's cap
    ],
)
def test_a_label_addison_did_not_mint_never_reaches_the_shell(store: Store, label: str):
    """THE VALIDATION IS THE WHOLE OF WHAT MAKES THIS SAFE TO EXIST. This method takes a
    LABEL from a surface rather than an id from a row, so without it a stale or hostile
    caller could name any launchd job on the machine and have the highest-trust process
    delete its file. The shell validates too (plan §5.8 — it does not trust the core),
    and that is the point of asking here as well: Addison refuses its own bad request,
    with its own sentence, before a round trip.

    Refused BEFORE the store is read and before the bridge is reached, which is why the
    bridge recorded nothing.

    Mutation: replace the check with ``label.startswith(LABEL_PREFIX)`` — the traversal,
    separator, upper-case, whitespace and over-length rows all fail."""
    bridge = _ArmedBridge([])
    server = _remover(bridge)(store, bridge)

    answer = server._automation_disarm_orphan({"label": label})
    assert answer["ok"] is False
    assert answer["error"] == _NOT_ADDISONS_OWN
    assert bridge.disarmed == []


@pytest.mark.parametrize("label", [None, "", 12, {"label": "x"}, ["com.addison.auto.tidy"]])
def test_a_label_that_is_not_even_a_string_is_refused_the_same_way(store: Store, label):
    """One sentence for every unusable label, and no exception for any of them: the
    caller is a webview, and a handler that raises on a malformed param answers with an
    error frame a surface can only render as "something went wrong"."""
    bridge = _ArmedBridge([])
    server = _remover(bridge)(store, bridge)

    assert server._automation_disarm_orphan({"label": label})["error"] == _NOT_ADDISONS_OWN
    assert server._automation_disarm_orphan({})["error"] == _NOT_ADDISONS_OWN
    assert bridge.disarmed == []


def test_an_automation_that_is_saved_again_is_not_switched_off_by_this_path(store: Store):
    """The narrowness, enforced. A row-backed automation has its own controls — the
    ``disarm_automation`` TOOL, which raises an ordinary card, and Remove, which disarms
    before it forgets. Letting this answer for one too would put a second, CARDLESS
    disarm beside the one that deliberately asks.

    The only way a person meets this refusal is a restore landing between the surface
    reading the list and the press — and then the row is on screen with its own Disarm,
    which is what the sentence tells them.

    Mutation: delete the saved-row check — this fails."""
    store.insert_automation(**_INTERVAL)
    bridge = _ArmedBridge([_INTERVAL["label"]])
    server = _remover(bridge)(store, bridge)

    answer = server._automation_disarm_orphan({"label": _INTERVAL["label"]})
    assert answer == {"ok": False, "error": _SAVED_AGAIN}
    assert bridge.disarmed == []
    # ...and the row is untouched, so the surface still has something to render.
    assert [row.id for row in store.list_automations()] == [_INTERVAL["id"]]


def test_the_shells_own_refusal_is_relayed_rather_than_guessed_at(store: Store):
    """The shell knows which of its refusals happened (not a Mac, no home folder, the
    scheduler would not answer); this side would only be inventing one. The same rule
    the removal path follows.

    Mutation: return ``_COULDNT_DISARM_ORPHAN`` unconditionally — this fails."""
    orphan = "com.addison.auto.tidy-downloads"
    bridge = _ArmedBridge([orphan], fail=True)
    server = _remover(bridge)(store, bridge)

    answer = server._automation_disarm_orphan({"label": orphan})
    assert answer == {"ok": False, "error": "the scheduler didn't answer"}


def test_a_shell_that_cannot_be_reached_says_so_in_plain_words(store: Store):
    """Two ways the shell is not there: no bridge at all (the CLI, a test), and a bridge
    that raises. Both answer one plain sentence and NEVER ``ok:true`` — telling somebody
    a job is off while their computer runs it is the one lie this whole subsystem is
    arranged to avoid."""

    class _Broken(ShellBridgeStubs):
        def disarm_automation(self, label: str) -> dict:
            raise RuntimeError("the shell went away")

    orphan = "com.addison.auto.tidy-downloads"
    no_shell = _remover(None)(store, None)
    assert no_shell._automation_disarm_orphan({"label": orphan}) == {
        "ok": False,
        "error": _NO_SHELL_TO_DISARM,
    }

    broken = _remover(_Broken())(store, _Broken())
    assert broken._automation_disarm_orphan({"label": orphan}) == {
        "ok": False,
        "error": _COULDNT_DISARM_ORPHAN,
    }


def test_switching_off_an_orphan_answers_in_every_profile(tmp_path):
    """A TIGHTENING IS NEVER PROFILE-GATED — the rule ``automation.remove`` follows, for
    a sharper reason here. Every automation is armed from Developer (arming is
    ``dev_only``), so a person who switches to Simple and then restores an old snapshot
    is EXACTLY the person left with a job running and no way to stop it. Gating this on
    the profile would trap them.

    Mutation: add ``if self._mode() is not PolicyMode.OPEN: return {...}`` to the
    handler — this fails in the Simple half, which runs first because Simple is the
    default."""
    first, second = "com.addison.auto.tidy-downloads", "com.addison.auto.backup-notes"
    bridge = _ArmedBridge([first, second])
    h = _server_with(tmp_path, bridge=bridge)
    try:
        # Simple is the default profile, and it can stop a job it could never have
        # started.
        assert _call(h, "automation.disarmOrphan", {"label": first}, 1) == {"ok": True}
        _developer(h)
        assert _call(h, "automation.disarmOrphan", {"label": second}, 2) == {"ok": True}
        assert bridge.disarmed == [first, second]
    finally:
        _shutdown(h.reader, h.thread)


def test_the_core_refuses_exactly_the_labels_the_shell_refuses():
    """THE THIRD LEG OF THE CROSS-LANGUAGE LOCKSTEP, beside the plist comparison and the
    label-minting test above. ``label_is_addisons_own`` exists to refuse one process
    earlier than ``automation.rs::label_is_valid`` does — which is only worth anything
    if the two agree, so the RULE is read out of the Rust rather than restated here.

    Mutation: change ``MAX_STEM_CHARS`` in automation.rs (or ``MAX_SLUG_CHARS`` here)
    alone — this fails."""
    source = (
        _REPO_ROOT / "shell" / "src-tauri" / "src" / "automation.rs"
    ).read_text(encoding="utf-8")
    cap = re.search(r"const MAX_STEM_CHARS: usize = (\d+);", source)
    prefix = re.search(r'const LABEL_PREFIX: &str = "([^"]+)";', source)
    assert cap and prefix, "automation.rs no longer states its label rule as constants"
    max_stem, shell_prefix = int(cap.group(1)), prefix.group(1)
    assert shell_prefix == LABEL_PREFIX
    assert max_stem == MAX_SLUG_CHARS

    # The exact boundary, from both sides of it.
    assert label_is_addisons_own(f"{LABEL_PREFIX}{'n' * max_stem}")
    assert not label_is_addisons_own(f"{LABEL_PREFIX}{'n' * (max_stem + 1)}")
    # ...and every label the core can MINT is one this predicate accepts, so the check
    # can never refuse Addison's own work.
    taken: list[str] = []
    for _ in range(5):
        minted = derive_label("n" * (MAX_SLUG_CHARS + 20), taken)
        assert minted is not None and label_is_addisons_own(minted)
        taken.append(minted)
    for name in ("Tidy up downloads", "Zálohování", "back-up NOTES 2"):
        minted = derive_label(name)
        assert minted is not None and label_is_addisons_own(minted)
