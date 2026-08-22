"""Messaging channels — PHASE 1 (docs/messaging-channel-plan.md §4, phase 1).

Phase 1 ships configuration and nothing else: the ``channels`` and
``channel_pairings`` tables, the ``channel.list``/``add``/``remove`` RPC, the Rust
keychain pair, and a Settings section. **No adapter, no poll loop, no pairing, no
network call, no tool.**

So the tests here are as much about what CANNOT happen as about what does:

  (1) a saved channel is INERT — nothing connects, nothing registers, and the module
      that owns the surface can neither start a thread nor reach a network;
  (2) THE TRANSPORT VOCABULARY IS CLOSED — the database itself refuses a kind with
      no adapter behind it, and there is no command column to store;
  (3) G1 — no payload and no column here can carry a token, and the module cannot
      even name one;
  (4) G3 — ``channels`` is snapshot-CAPTURED (minus ``token_present``) so a restore
      genuinely puts the list back, while ``channel_pairings`` is EXCLUDED, because a
      one-action restore must never re-instate an authorization somebody revoked;
  (5) the surface is Developer-only — ``channel.add`` is refused in Simple, while
      ``list`` and ``remove`` stay reachable so a profile switch never traps
      configuration;
  (6) every ``channel.*`` method is answered ON THE WORKER THREAD.

Every test here was mutation-proven: the line it guards was broken and this test
watched to fail. The mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from agent_core.memory.store import Store
from agent_core.rpc.channels import (
    _DEV_ONLY,
    _NAME_TAKEN,
    _NEEDS_NAME,
    _UNKNOWN_KIND,
)
from agent_core.snapshots.scope import _CAPTURED_TABLES, _EXCLUDED_COLUMNS, _EXCLUDED_TABLES
from tests.conftest import _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHANNELS_SRC = _REPO_ROOT / "agent_core" / "rpc" / "channels.py"
_MAIN_SRC = _REPO_ROOT / "agent_core" / "main.py"
_SCHEMA_SRC = _REPO_ROOT / "agent_core" / "memory" / "schema.sql"


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _developer(harness, request_id: int = 900) -> None:
    """Switch the running server to the Developer profile the way the app does."""
    result = _call(harness, "profile.set", {"profileId": "developer"}, request_id)
    assert result["ok"] is True and result["mode"] == "open"


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "channels-test.sqlite3")


def _direct(harness):
    """A second connection to the running server's database, for the two tests that
    need to write a row phase 1 has no RPC for.

    The server's own ``sqlite3`` connection belongs to its worker thread and may not
    be touched from here — that confinement is the thing several tests in this file
    exist to protect — so a pairing is written the only honest way a test can write
    one: another connection to the same file, with foreign keys on, closed straight
    afterwards. WAL (set by ``Store``) is what makes the two coexist."""
    conn = sqlite3.connect(harness.server.store.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _pair(harness, channel_id: str, pairing_id: str = "p1") -> None:
    """Write one pairing row directly. Phase 1 has no pairing RPC — pairing is
    phase 2 — and the capture decision about this table is a phase-1 decision, so it
    is proven now, while the shape of a restore is still cheap to change."""
    conn = _direct(harness)
    try:
        conn.execute(
            "INSERT INTO channel_pairings (id, channel_id, sender_id, label, paired_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pairing_id, channel_id, "sender-1", "Somebody's phone", 1),
        )
        conn.commit()
    finally:
        conn.close()


def _count_pairings(harness) -> int:
    conn = _direct(harness)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM channel_pairings").fetchone()["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (1) A saved channel is INERT
# ---------------------------------------------------------------------------


def test_the_channel_surface_cannot_reach_a_thread_a_network_or_the_registry():
    """Structural, on ``rpc/mcp.py``'s phase-1 test pattern. Phase 2 adds a poll
    thread and an HTTP adapter; both belong to ``channels/`` and ``channel_service``,
    neither of which exists yet. What must stay true of THIS module through every
    later phase is that the RPC layer reads rows and nothing else — a thread started
    from an RPC handler would run outside the one place main.py accounts for threads,
    and an ``httpx`` call here would be a network request on the worker with no
    budget over it.

    Mutation: add ``import httpx`` or ``import threading`` to rpc/channels.py — this
    fails, naming the import."""
    tree = ast.parse(_CHANNELS_SRC.read_text(encoding="utf-8"))
    forbidden = {
        "httpx", "requests", "urllib", "socket", "threading", "subprocess", "asyncio",
        "agent_core.tools.registry", "agent_core.mcp_client",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, (
                    f"rpc/channels.py imports {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert node.module not in forbidden and root not in forbidden, (
                f"rpc/channels.py imports from {node.module}"
            )


def test_adding_a_channel_reaches_nothing(tmp_path):
    """THE POINT OF PHASE 1. Saving a connection writes one row, switched off, with
    no token believed saved — and makes no request of any kind, because there is
    nothing in the build to make one with.

    Mutation: have ``_channel_add`` write ``enabled=1`` — this fails on the row."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        added = _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)
        assert added["ok"] is True
        assert added["channel"]["enabled"] is False
        assert added["channel"]["tokenPresent"] == "unknown"
        assert added["channel"]["pairedDevices"] == 0
        (row,) = _call(h, "channel.list", {}, 2)["channels"]
        assert row["enabled"] is False
        assert row["tokenPresent"] == "unknown"
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (2) The transport vocabulary is CLOSED, and there is no command
# ---------------------------------------------------------------------------


def test_a_channel_row_has_no_column_that_could_hold_a_command(store: Store):
    """The step-7 rule, transplanted: the schema is where "this row can never name a
    program" is enforced. A channel is a transport kind, never a thing to launch.

    Mutation: add a ``command TEXT`` column to ``channels`` — this fails."""
    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(channels)")}
    assert columns == {"id", "kind", "name", "enabled", "token_present", "created_at"}


def test_the_database_refuses_a_transport_with_no_adapter(store: Store):
    """The CHECK is the authority, not the handler's frozenset: a hand-edited row, an
    older build or a restored payload all go through the database.

    Mutation: drop the CHECK on ``kind`` — this fails."""
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_channel(id="c1", kind="whatsapp", name="Bridge", created_at=1)


def test_the_handler_refuses_the_same_kinds_the_database_does(tmp_path):
    """Two spellings of one closed vocabulary is how a value ends up legal in one
    place and refused in the other, so the handler's answer is pinned to be the
    plain sentence and never an error frame.

    Mutation: delete the ``kind not in _KINDS`` branch in ``_channel_add`` — the
    CHECK still refuses, and the IntegrityError branch keeps the answer a sentence;
    delete BOTH and the row-count assertion fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        refused = _call(h, "channel.add", {"kind": "whatsapp", "name": "Bridge"}, 1)
        assert refused == {"ok": False, "error": _UNKNOWN_KIND}
        assert _call(h, "channel.add", {"name": "No kind at all"}, 2)["ok"] is False
        assert _call(h, "channel.list", {}, 3)["channels"] == []
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (3) G1 — no token, anywhere on this surface
# ---------------------------------------------------------------------------


def test_no_payload_on_this_surface_can_carry_a_token():
    """G1, structurally. The token goes from the webview to the OS keychain through
    the shell's own command and is read by the core at the moment of use; nothing in
    this module may name one, in a param, on a row, or in a store call.

    ``token_present`` is the deliberate exception and is spelled out: it is a
    three-state OBSERVATION about whether a credential exists and can hold no part of
    one, which is exactly what ``provider_config.secret_presence`` is.

    Mutation: add a ``token`` field to the add handler or the wire row — this fails,
    naming it."""
    tree = ast.parse(_CHANNELS_SRC.read_text(encoding="utf-8"))
    secret_names = {"token", "api_key", "apiKey", "secret", "password", "authorization", "header"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in secret_names, (
                f"rpc/channels.py names a secret field: {node.value}"
            )
        elif isinstance(node, ast.Name):
            assert node.id not in secret_names


def test_no_column_here_can_hold_a_token(store: Store):
    """The other half, in the schema. ``channels`` records WHETHER a token is
    believed to exist and never any part of one, and ``channel_pairings`` holds a
    transport id and a display name.

    Mutation: add a ``token TEXT`` column to either table — this fails."""
    for table in ("channels", "channel_pairings"):
        columns = {row["name"] for row in store._conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            assert "token" not in column or column == "token_present", (
                f"{table}.{column} looks like it could hold a credential"
            )
            assert "key" not in column and "secret" not in column


def test_the_wire_shape_is_the_one_the_frontend_parses(tmp_path):
    """camelCase at the boundary (``created_at`` -> ``addedAt``, ``token_present`` ->
    ``tokenPresent``), the derived ``pairedDevices`` count, and NOTHING else — no
    token, no chat id, no transport field the person never set. The generated fixture
    (tests/ipc_fixtures.py -> shell/src/__tests__/fixtures/channel.list.json) keeps
    the frontend parser honest about this same shape."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        added = _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)
        expected = {"id", "kind", "name", "enabled", "tokenPresent", "pairedDevices", "addedAt"}
        assert set(added["channel"]) == expected
        (row,) = _call(h, "channel.list", {}, 2)["channels"]
        assert set(row) == expected
        assert row["name"] == "My phone"
        assert row["kind"] == "telegram"
        assert isinstance(row["addedAt"], int)
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (4) G3 — what a restore puts back, and what it must never put back
# ---------------------------------------------------------------------------


def test_the_channels_table_is_captured_and_token_presence_is_not():
    """Declared, not incidental. ``test_capture_scope_covers_every_schema_table``
    forces the choice; this pins WHICH way it was made and the exact columns, because
    a silently-dropped column would be reset to its default BY the recovery path —
    and a silently-ADDED ``token_present`` would let a restore assert that a token
    exists in a keychain no snapshot has ever touched.

    Mutation: add "token_present" to the captured tuple — this fails, and so does the
    restore test below."""
    assert _CAPTURED_TABLES["channels"] == ("id", "kind", "name", "enabled", "created_at")
    assert _EXCLUDED_COLUMNS["channels"] == ("token_present",)


def test_pairings_are_excluded_from_capture_with_a_stated_reason():
    """THE DECISION OF PHASE 1, and the one that is hardest to reverse later. A
    pairing is an AUTHORIZATION, not configuration: G3 promises that one action
    restores your configuration, and an authorization somebody deliberately revoked
    must not come back inside that one action. Nothing outside SQLite holds this
    truth — unlike an armed automation, which the OS holds and is asked for — so the
    row IS the authorization and the only honest answer is to keep it out.

    Mutation: move "channel_pairings" into ``_CAPTURED_TABLES`` — this fails, and so
    does the behavioural test below."""
    assert "channel_pairings" not in _CAPTURED_TABLES
    reason = _EXCLUDED_TABLES["channel_pairings"]
    assert "authorization" in reason and "restore" in reason


def test_a_restore_puts_the_channel_list_back(tmp_path):
    """G3 over the wire for this table: a channel removed after a restore point comes
    back, and one added after it goes away (replace-all within the captured scope).

    Mutation: move "channels" from ``_CAPTURED_TABLES`` to ``_EXCLUDED_TABLES`` —
    this fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "Kept"}, 1)["ok"]
        snapshot_id = _call(h, "snapshot.create", {}, 2)["snapshotId"]
        assert _call(h, "channel.add", {"kind": "telegram", "name": "Later"}, 3)["ok"]
        listed = _call(h, "channel.list", {}, 4)["channels"]
        kept = next(c for c in listed if c["name"] == "Kept")
        assert _call(h, "channel.remove", {"id": kept["id"]}, 5)["ok"] is True

        assert _call(h, "snapshot.restore", {"id": snapshot_id}, 6)["ok"] is True

        names = [c["name"] for c in _call(h, "channel.list", {}, 7)["channels"]]
        assert names == ["Kept"]
    finally:
        _shutdown(h.reader, h.thread)


def test_a_restore_never_re_pairs_a_phone_and_never_claims_a_token(tmp_path):
    """The two halves of the capture decision, proven together on one restore.

    A pairing written before the snapshot is GONE after the restore — the row is the
    authorization and it is not in the payload. And ``token_present``, set to
    'present' before the snapshot, comes back as 'unknown': the honest post-restore
    answer, and the safe one, since 'unknown' can never read as "a token is saved".

    Both are written straight to the store, because phase 1 has no RPC that writes
    either — which is the point of testing it now, while the shape of what a restore
    does is still cheap to change.

    Mutation: capture ``channel_pairings``, or add ``token_present`` to the captured
    tuple — either one fails here."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        (row,) = _call(h, "channel.list", {}, 2)["channels"]
        channel_id = row["id"]
        _pair(h, channel_id)
        conn = _direct(h)
        try:
            conn.execute(
                "UPDATE channels SET token_present = 'present' WHERE id = ?", (channel_id,)
            )
            conn.commit()
        finally:
            conn.close()
        assert _count_pairings(h) == 1

        snapshot_id = _call(h, "snapshot.create", {}, 3)["snapshotId"]
        assert _call(h, "snapshot.restore", {"id": snapshot_id}, 4)["ok"] is True

        (restored,) = _call(h, "channel.list", {}, 5)["channels"]
        assert restored["id"] == channel_id       # the configuration came back...
        assert restored["tokenPresent"] == "unknown"  # ...and the observation did not
        assert restored["pairedDevices"] == 0         # ...and neither did the authorization
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_a_channel_takes_its_pairings_with_it(tmp_path):
    """``ON DELETE CASCADE`` plus ``PRAGMA foreign_keys = ON``, so "no pairing
    outlives its channel" is the database's property rather than a handler's
    diligence — and so a pairing can never be left addressing a channel nobody can
    see or remove.

    Mutation: drop ``ON DELETE CASCADE`` from the schema — the delete raises a
    foreign-key error and this fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        (row,) = _call(h, "channel.list", {}, 2)["channels"]
        _pair(h, row["id"])

        assert _call(h, "channel.remove", {"id": row["id"]}, 3)["ok"] is True

        assert _count_pairings(h) == 0
        assert _call(h, "channel.list", {}, 4)["channels"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_adding_and_removing_a_channel_each_leave_a_restore_point(tmp_path):
    """The hooks. Adding is snapshot-and-proceed (a channel can be removed again in
    one click); removing REFUSES if the capture fails — proven below.

    Mutation: delete the ``_snapshot_auto("channel_add")`` line — this fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        channel_id = _call(h, "channel.list", {}, 2)["channels"][0]["id"]
        assert _call(h, "channel.remove", {"id": channel_id}, 3)["ok"] is True
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 4)["snapshots"]]
        assert "channel_add" in reasons
        assert "channel_remove" in reasons
    finally:
        _shutdown(h.reader, h.thread)


def test_a_removal_is_refused_when_the_restore_point_cannot_be_saved(tmp_path):
    """The ``skill_delete`` / ``mcp_disconnect`` class: the name and the kind exist
    nowhere else once the row is gone, so losing them with no way back is worse than
    refusing.

    Mutation: call ``_snapshot_auto`` and ignore its result — this fails, because the
    channel is gone."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        channel_id = _call(h, "channel.list", {}, 2)["channels"][0]["id"]
        h.server.snapshot_manager.capture = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no restore point")
        )
        result = _call(h, "channel.remove", {"id": channel_id}, 3)
        assert result["ok"] is False
        assert "didn't remove anything" in result["error"]
        assert [c["id"] for c in _call(h, "channel.list", {}, 4)["channels"]] == [channel_id]
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (5) Developer-only surface
# ---------------------------------------------------------------------------


def test_the_simple_profile_cannot_add_a_channel(tmp_path):
    """Channels are dev-only for v1 (owner decision 10, 2026-08-22). The Settings
    section is hidden in Simple, but hiding is not enforcing: a stale frontend, or a
    profile switched mid-session, lands here.

    Mutation: delete the ``self._mode() is not PolicyMode.OPEN`` branch in
    ``_channel_add`` — this fails, and the store then holds a row a Simple user
    made."""
    h = build_server(tmp_path, register_tool=False)
    try:
        result = _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)
        assert result["ok"] is False
        assert result["error"] == _DEV_ONLY
        assert _call(h, "channel.list", {}, 2)["channels"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_switching_back_to_simple_never_hides_or_traps_what_was_saved(tmp_path):
    """The other half, and it is the 2026-08-06 artifact lesson applied here: a
    Developer-made row stays LISTED in Simple and stays REMOVABLE. Hiding somebody's
    configuration when they switch profile is what that decision reversed, and a
    tightening — removal, which also deletes the token — must never be the thing a
    profile switch traps.

    A listed row grants nothing by being listed: it is a name and a transport kind,
    switched off, with no adapter in the build behind it."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        assert _call(h, "profile.set", {"profileId": "simple"}, 2)["ok"] is True

        listed = _call(h, "channel.list", {}, 3)["channels"]
        assert [c["name"] for c in listed] == ["My phone"]
        assert _call(h, "channel.remove", {"id": listed[0]["id"]}, 4)["ok"] is True
        assert _call(h, "channel.list", {}, 5)["channels"] == []
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (6) Every channel.* method is answered on the worker thread
# ---------------------------------------------------------------------------


def test_no_channel_method_may_be_answered_inline_on_the_read_loop():
    """Every ``channel.*`` method reads or writes SQLite and mints snapshots, and all
    SQLite access is confined to the worker thread (main.py's own docstring). The
    rule is structural rather than behavioural, because ``main.py`` already answers
    some methods INLINE (``permission.respond``, ``model.setRoleForNextMessage``) and
    that is exactly how a later ``channel.setEnabled`` would get there by imitation —
    and phase 2's version of that method STARTS A THREAD, which on the read loop
    would be a thread started from the one place that must stay free to deliver
    frames.

    Mutation: add ``Method.CHANNEL_LIST: self._handle_something`` to the inline table
    in ``_build_dispatch_table`` — this fails, naming the method."""
    from agent_core import main as main_module
    from agent_core.protocol import Method

    # The two OUTBOUND notifications are not requests and have no handler: nothing
    # dispatches them, main.py never names them, and they are emitted by the service
    # (`channel.stateChanged`) and by the remote turn (`channel.remoteTurn`). They are
    # named here so that adding a third notification is a deliberate edit rather than
    # something this test quietly swallows.
    notifications = {Method.CHANNEL_STATE_CHANGED, Method.CHANNEL_REMOTE_TURN}
    named = {
        name
        for name, value in vars(Method).items()
        if isinstance(value, str)
        and value.startswith("channel.")
        and value not in notifications
    }
    assert named, "no channel.* methods found in protocol.py — did they move?"
    assert {getattr(Method, name) for name in named} == set(main_module._CHANNEL_JOBS)
    assert not (notifications & set(main_module._CHANNEL_JOBS)), (
        "a notification is not a request and must not be dispatchable"
    )

    tree = ast.parse(_MAIN_SRC.read_text(encoding="utf-8"))
    jobs_table = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "_CHANNEL_JOBS" for t in node.targets)
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
                "_CHANNEL_JOBS: a channel.* method answered anywhere but the worker "
                "queue puts a store read on the wrong thread"
            )


# ---------------------------------------------------------------------------
# Names, and the ordinary shapes
# ---------------------------------------------------------------------------


def test_two_channels_cannot_share_a_name_however_it_is_capitalised(tmp_path):
    """A channel is named so it can be recognised in a list of them, and two rows
    called the same thing make the Remove button a coin toss.

    Mutation: delete the ``channel_name_taken`` check — this fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "channel.add", {"kind": "telegram", "name": "My phone"}, 1)["ok"]
        clash = _call(h, "channel.add", {"kind": "telegram", "name": "MY PHONE"}, 2)
        assert clash == {"ok": False, "error": _NAME_TAKEN}
        assert len(_call(h, "channel.list", {}, 3)["channels"]) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_a_channel_needs_a_name(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        blank = _call(h, "channel.add", {"kind": "telegram", "name": "   "}, 1)
        assert blank == {"ok": False, "error": _NEEDS_NAME}
        long_name = _call(h, "channel.add", {"kind": "telegram", "name": "x" * 61}, 2)
        assert long_name["ok"] is False
        assert _call(h, "channel.list", {}, 3)["channels"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_something_that_is_not_there_is_fine(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        assert _call(h, "channel.remove", {"id": "nope"}, 1) == {"ok": True}
        assert _call(h, "channel.remove", {}, 2) == {"ok": True}
        # ...and it mints no restore point, because nothing changed.
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 3)["snapshots"]]
        assert "channel_remove" not in reasons
    finally:
        _shutdown(h.reader, h.thread)


def test_an_upgraded_database_gains_both_tables(tmp_path):
    """``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists,
    which is why every column added to an existing table needs a migration line
    (store.py). A NEW table needs none — the schema is re-applied on every open — and
    this is the check that says so rather than assuming it: a database made before
    these tables existed gains them on the next launch.

    Built by opening a store, dropping the two tables (i.e. the state an older
    database is in), and opening it again."""
    path = tmp_path / "upgraded.sqlite3"
    old = Store(path)
    old._conn.execute("DROP TABLE channel_pairings")
    old._conn.execute("DROP TABLE channels")
    old._conn.commit()
    old._conn.close()

    upgraded = Store(path)
    tables = {
        row["name"]
        for row in upgraded._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"channels", "channel_pairings"} <= tables
    # And it is usable, not merely present.
    upgraded.insert_channel(id="c1", kind="telegram", name="My phone", created_at=1)
    assert [row["name"] for row in upgraded.list_channels()] == ["My phone"]
