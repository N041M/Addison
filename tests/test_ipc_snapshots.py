"""snapshot.* over the wire — GLOBAL FLOOR G3, guaranteed rollback (contract §10).

These are the floor's tests at the boundary the frontend actually speaks to: the
namespace round-trips, restore is reachable in one action and never passes
through the permission gate, no blob or key material crosses the wire, and — the
headline claim — a database that will not open at all still answers snapshot.list
and snapshot.restoreLastWorking, rebuilding from the sidecar files.

House style of tests/test_ipc_server.py: the real server on fake pipes,
``reader.feed`` / ``writer.wait_for``.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from agent_core.main import _REBUILD_FAILED, _REBUILT_FROM_UNVERIFIED
from agent_core.memory.store import Store
from agent_core.policy import PolicyMode
from agent_core.protocol import Method
from agent_core.rpc.constants import (
    _NOTHING_TO_REBUILD_FROM,
    _REBUILT_MESSAGE,
    _STORE_UNAVAILABLE_MESSAGE,
)
from agent_core.snapshots.snapshot_manager import (
    SnapshotManager,
    _canonical,
    _fingerprint,
    rebuild_rows_from_payloads,
)
from agent_core.snapshots.scope import _CAPTURED_TABLES
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """One request in, its result out."""
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    frame = harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return frame["result"]


def _error(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    frame = harness.writer.wait_for(lambda f: f.get("id") == request_id and "error" in f)
    return frame["error"]


def _side_store(tmp_path) -> Store:
    """A SECOND connection to the same database file, owned by the test thread.

    The server's own ``Store`` belongs to its worker thread and sqlite3 refuses
    cross-thread use, so a test that needs to arrange or inspect state directly
    opens its own connection — the same thing the existing IPC tests do with raw
    ``sqlite3.connect``. The server is idle between requests, so there is no
    contention to speak of."""
    return Store(tmp_path / IPC_DB_NAME)


def _side_manager(tmp_path, store: Store) -> SnapshotManager:
    """A manager over the side connection, pointed at the same sidecar directory
    the server uses. The bottom row is already written by then, so constructing
    this one adds nothing — ``created_the_database=False`` states the fact
    anyway, because the server created that file, not this test."""
    return SnapshotManager(
        store=store, snapshot_dir=tmp_path / "snapshots", created_the_database=False
    )


def _raising_store_factory():
    def factory():
        raise sqlite3.DatabaseError("file is not a database")

    return factory


def _populate_sidecars(tmp_path: Path) -> str:
    """A snapshot directory with a real, verified, decodable payload in it —
    written by the real manager against a throwaway store, then left behind after
    the database it came from is destroyed. This is the shape the cold-start path
    finds on disk."""
    db_path = tmp_path / IPC_DB_NAME
    store = Store(db_path)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", "simple")
    manager = SnapshotManager(
        store=store, snapshot_dir=tmp_path / "snapshots", created_the_database=True
    )
    snapshot = manager.capture(trigger="on_command", reason="user_request", verified_working=True)
    store.close()
    # The database is now gone; only the sidecars remain.
    db_path.unlink()
    return snapshot.id


def _populate_sidecars_ending_in_an_unproven_one(tmp_path: Path) -> tuple[str, str]:
    """Sidecars in the shape the amendment's motivating story leaves behind.

    An older setup a turn actually completed against, and a NEWER one saved just
    before a sweeping change — never proven to work, and holding exactly the
    configuration the user is trying to escape. Returns ``(good_id, broken_id)``.

    This is the arrangement the whole floor is judged on, and until the fix the
    cold-start path named the good one and applied the broken one."""
    db_path = tmp_path / IPC_DB_NAME
    store = Store(db_path)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", "simple")
    clock = [1000]
    manager = SnapshotManager(
        store=store,
        snapshot_dir=tmp_path / "snapshots",
        clock=lambda: clock[0],
        created_the_database=True,
    )
    store.set_setting("model_choice", "GOOD")
    clock[0] += 10
    good = manager.mark_verified_working()
    assert good is not None
    store.set_setting("model_choice", "BROKEN")
    clock[0] += 10
    broken = manager.capture(trigger="auto", reason="mode_switch")
    store.close()
    db_path.unlink()
    return good.id, broken.id


def _populate_sidecars_after_an_escape(tmp_path: Path) -> None:
    """Sidecars left behind by a disk-arm restore, database then destroyed.

    An upgraded install whose first click never reaches the walk: no verified row
    exists yet, so ``restore_last_working()`` hands over to the sidecars, saves a
    ``pre_restore`` point holding the setup being escaped, and applies the older
    one. That ``pre_restore`` payload is now the NEWEST unverified file on disk,
    which is exactly what makes it dangerous to the cold-start path."""
    db_path = tmp_path / IPC_DB_NAME
    store = Store(db_path)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("model_choice", "GOOD")
    clock = [1000]
    manager = SnapshotManager(
        store=store,
        snapshot_dir=tmp_path / "snapshots",
        clock=lambda: clock[0],
        created_the_database=False,
    )
    store.set_setting("model_choice", "BROKEN")
    clock[0] += 10
    assert manager.restore_last_working().ok
    assert store.get_setting("model_choice") == "GOOD"
    store.close()
    db_path.unlink()


def _strip_the_verified_sidecars(tmp_path: Path) -> None:
    """Leave only payloads no turn ever completed against — the state a user
    reaches when their very first setup attempt never produced a working turn."""
    for path in sorted((tmp_path / "snapshots").glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
        if meta.get("verified_working"):
            path.unlink()


def _write_unappliable_sidecar(tmp_path: Path, *, snapshot_id: str, captured_at: int) -> None:
    """A sidecar that DECODES cleanly but cannot be put back.

    Two ``app_settings`` rows share a primary key, so ``apply_config_state``
    raises on the second INSERT. That is the difference the user is entitled to
    hear about: restore points exist and are readable, they just would not go
    back in — which is not at all the same story as "there's nothing saved"."""
    directory = tmp_path / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    tables: dict = {table: [] for table in _CAPTURED_TABLES}
    tables["app_settings"] = [
        {"key": "duplicate", "value": "one", "updated_at": captured_at},
        {"key": "duplicate", "value": "two", "updated_at": captured_at},
    ]
    payload = {
        "version": 1,
        "captured_at": captured_at,
        "captured_at_ns": captured_at * 1_000_000_000,
        "meta": {
            "id": snapshot_id,
            "trigger": "on_command",
            "reason": "user_request",
            "created_in_mode": "safe",
            "state_fingerprint": _fingerprint(tables),
            "verified_working": 1,
            "undeletable": 0,
            "captures_binary": 0,
            "binary_ref": None,
        },
        "tables": tables,
    }
    (directory / f"{snapshot_id}.json").write_text(_canonical(payload), encoding="utf-8")


def _fail_once_then_open(tmp_path: Path):
    """The cold-start factory: raise on the first call (the damaged file), then
    hand back a real Store — which is what happens once the rebuild has put a
    readable database at that path."""
    state = {"failed": False}

    def factory() -> Store:
        if not state["failed"]:
            state["failed"] = True
            raise sqlite3.DatabaseError("file is not a database")
        store = Store(tmp_path / IPC_DB_NAME)
        store.set_setting("widgets_seeded", "1")
        return store

    return factory


# --- which bottom row the SERVER writes, end to end -------------------------
#
# The manager takes the fresh-vs-established fact as an argument; these are the
# tests that it is supplied CORRECTLY, which is the half that lives in main.py.
# A manager-level test cannot catch a server that reads the fact after the store
# has already created the file, or one that stops reading it at all.


def _an_established_database(tmp_path) -> None:
    """A database at the server's own path, holding what a companion user
    accumulates and NONE of the four signals the old inference looked for: no
    service (no key means turns run on the relay), no note, no routine, still on
    Simple. Only settings, widgets and chats — all of which it was blind to."""
    store = Store(tmp_path / IPC_DB_NAME)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", "simple")
    store.set_setting("theme", "dark")
    store.set_setting("selected_model", "claude-model-that-was-retired")
    store.insert_widget(id="mine", spec_json='{"kind":"stat","source":"provider_latency"}',
                        pinned=True, position=0, created_at=100)
    store.create_conversation(id="c1", title="Recipes", provider_id="anthropic",
                              started_at=100)
    store.insert_message(id="m1", conversation_id="c1", role="user",
                         content="hello", created_at=100)
    store.close()


def test_an_established_database_is_never_labelled_as_first_installed(tmp_path):
    """The database was already on disk when this launch started, so the bottom
    row must be the honest one — whatever is inside it.

    This is the defect at the boundary the frontend speaks to. Classified
    ``genesis`` the row is verified, so it becomes a legitimate target of the
    one-action Restore; it is rendered "Addison as first installed"; and it
    cannot be deleted. The person's way back would hand them the retired model
    they were trying to escape, and say it had cleared their widgets while
    putting them back."""
    _an_established_database(tmp_path)
    h = build_server(tmp_path, register_tool=False)
    try:
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        bottom = listed["snapshots"][-1]
        assert bottom["reason"] == "pre_upgrade"
        assert bottom["verifiedWorking"] is False
        assert bottom["reasonLabel"] == "Your setup before this update"
        assert "genesis" not in [row["reason"] for row in listed["snapshots"]]
        # Not verified means not a one-click target: the walk has nothing to
        # offer yet, and says so rather than offering this row.
        assert "lastWorkingId" not in listed
    finally:
        _shutdown(h.reader, h.thread)


def test_a_database_this_launch_created_still_gets_genesis(tmp_path):
    """The other half. Nothing at the path beforehand, so the row is genuinely
    "Addison as first installed" and is a legitimate restore target from before
    the first turn — which is what G3 asks for."""
    assert not (tmp_path / IPC_DB_NAME).exists()
    h = build_server(tmp_path, register_tool=False)
    try:
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        bottom = listed["snapshots"][-1]
        assert bottom["reason"] == "genesis"
        assert bottom["verifiedWorking"] is True
        assert bottom["undeletable"] is True

        # Verified means it is a real one-click target — which shows the moment
        # the config moves off it. (Sitting exactly ON genesis, the walk skips it:
        # restoring it would change nothing.)
        store = _side_store(tmp_path)
        try:
            store.insert_skill(id="s1", name="Note", instructions="Be brief.",
                               enabled=True, created_at=10)
            after = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        finally:
            store.close()
        assert after["lastWorkingId"] == bottom["id"]
        assert after["lastWorkingLabel"] == "Addison as first installed"
    finally:
        _shutdown(h.reader, h.thread)


def test_genesis_holds_the_widgets_addison_seeds_on_first_run(tmp_path):
    """The build order, asserted where it can actually break.

    ``_ensure_built`` seeds the default widget rail and THEN constructs the
    manager, so genesis is a snapshot of the seeded state. Reverse those two
    lines and genesis captures an empty rail — and because ``widgets_seeded`` is
    a one-way latch that survives a restore (``scope._PRESERVED_SETTING_KEYS``),
    restoring genesis would empty the rail for good, with re-seeding already
    switched off. Nothing else in the suite would notice."""
    h = build_server(tmp_path, register_tool=False, seed_widgets=True)
    try:
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        genesis = listed["snapshots"][-1]
        assert genesis["reason"] == "genesis"
        store = _side_store(tmp_path)
        try:
            row = store.get_config_snapshot(genesis["id"])
            assert row is not None
            payload = json.loads(row.state_blob)
            seeded = {json.loads(w["spec_json"])["source"] for w in payload["tables"]["widgets"]}
        finally:
            store.close()
        # Exactly what main.py._DEFAULT_WIDGETS seeds, captured by genesis.
        assert seeded == {"connections", "tokens_month"}
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cold_start_rebuild_does_not_mint_a_second_bottom_row(tmp_path):
    """The rebuild reopens the database, so the bottom-row decision runs a second
    time — on a database holding the user's real restored configuration.

    Nothing new may be written there. ``rebuild_rows_from_payloads`` puts the
    saved rows back first, so the table is not empty and the decision returns
    early; and the fact is re-read after the swap, so even if the table WERE
    empty the file is now on disk and the cautious row is the worst that can
    happen. The failure this guards is the recovery path stamping a fresh
    permanent row over somebody's recovered setup."""
    _populate_sidecars(tmp_path)
    h = build_server(
        tmp_path, register_tool=False, store_factory=_fail_once_then_open(tmp_path)
    )
    try:
        before = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        assert _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)["ok"] is True
        after = _call(h, Method.SNAPSHOT_LIST, request_id=3)
        # The rows that came back are the rows that were saved — same ids, same
        # reasons, and no bottom row minted on top of them.
        assert [row["id"] for row in after["snapshots"]] == [
            row["id"] for row in before["snapshots"]
        ]
        assert "pre_upgrade" not in [row["reason"] for row in after["snapshots"]]
    finally:
        _shutdown(h.reader, h.thread)


def test_a_path_that_cannot_be_stat_ed_takes_the_cautious_road(tmp_path):
    """"Could not find out" must never be read as "brand new".

    The check asks the filesystem, and the filesystem can decline to answer — an
    unreadable parent directory, a path component that is not a directory. A
    plain existence test reports "no file" for all of them, which is the severe
    direction: a permanent, undeletable, verified row claiming an established
    install is brand new."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    h = build_server(tmp_path, register_tool=False)
    try:
        # The real db_path is fine; point the fact-finder at one that is not.
        h.server._db_path = blocker / "addison.sqlite3"
        assert h.server._database_created_by_this_launch() is None
        # ...and an ordinary missing file still answers "yes, this launch made it".
        h.server._db_path = tmp_path / "not-there.sqlite3"
        assert h.server._database_created_by_this_launch() is True
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_create_list_and_delete_roundtrip(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        created = _call(h, Method.SNAPSHOT_CREATE, request_id=1)
        assert created["ok"] is True
        snapshot_id = created["snapshotId"]

        listed = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        ids = [row["id"] for row in listed["snapshots"]]
        assert snapshot_id in ids
        # Genesis is always there too — G3 wants a restore target before the
        # first turn, not after it.
        assert "genesis" in [row["reason"] for row in listed["snapshots"]]

        assert _call(h, Method.SNAPSHOT_DELETE, {"id": snapshot_id}, request_id=3) == {"ok": True}
        after = _call(h, Method.SNAPSHOT_LIST, request_id=4)
        assert snapshot_id not in [row["id"] for row in after["snapshots"]]
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_restore_last_working_over_the_wire(tmp_path):
    # The one-action floor, reachable from the frontend with no arguments.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)     # force the build
        store = _side_store(tmp_path)
        manager = _side_manager(tmp_path, store)
        manager.mark_verified_working()
        # ...then break the config, the way the friend in the amendment's story did.
        store.insert_skill(
            id="skill-1", name="Junk", instructions="Break things", enabled=True, created_at=10
        )

        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert result["ok"] is True
        assert result["detail"]
        # The change made after the last verified config is gone again.
        assert store.get_skill("skill-1") is None
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_delete_refuses_an_anchor_over_the_wire(tmp_path):
    # G4 survives the RPC layer, with the exact plain sentence.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=0)
        store = _side_store(tmp_path)
        anchor = _side_manager(tmp_path, store).mint_anchor()
        assert anchor is not None
        result = _call(h, Method.SNAPSHOT_DELETE, {"id": anchor.id}, request_id=1)
        assert result == {
            "ok": False,
            "error": (
                "That restore point is permanent — it was saved when a safety "
                "setting was turned off, so it stays."
            ),
        }
        # And it really is still there.
        assert store.get_config_snapshot(anchor.id) is not None
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_list_payload_carries_no_blob_or_key_material(tmp_path):
    # G1 at the wire boundary: the blob, the fingerprint and the binary reference
    # are internal machinery and none of them may cross.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=0)
        store = _side_store(tmp_path)
        store.upsert_provider_config("anthropic", connected=True, added_at=5)
        store.close()
        _call(h, Method.SNAPSHOT_CREATE, request_id=1)
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        for row in listed["snapshots"]:
            assert set(row) == {
                "id",
                "createdAt",
                "trigger",
                "reason",
                "reasonLabel",
                "verifiedWorking",
                "undeletable",
                "capturesBinary",
                "createdInMode",
            }
        blob = json.dumps(listed).lower()
        for needle in ("api_key", "apikey", "sk-ant", "secret", "state_blob", "stateblob"):
            assert needle not in blob
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_restore_never_requests_a_permission_grant(tmp_path):
    # The mirror of test_skills_never_touch_gate_or_registry. A floor the gate
    # could deny is not a floor, so restore is ungatable BY CONSTRUCTION: it is an
    # RPC method, never a registry tool, and nothing in its path calls authorize().
    h = build_server(tmp_path)
    try:
        before = len(h.server.tool_registry.visible_tools(PolicyMode.SAFE))
        _call(h, Method.SNAPSHOT_CREATE, request_id=1)
        _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert len(h.server.tool_registry.visible_tools(PolicyMode.SAFE)) == before
        assert not any(
            f.get("method") == Method.PERMISSION_REQUEST_GRANT for f in h.writer.frames
        )
        # No snapshot verb was ever registered as a tool, in either mode.
        for mode in (PolicyMode.SAFE, PolicyMode.OPEN):
            ids = {t.id for t in h.server.tool_registry.visible_tools(mode)}
            assert not any("snapshot" in tool_id or "restore" in tool_id for tool_id in ids)
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_restore_reresolves_the_profile_to_simple_on_garbage(tmp_path):
    # active_profile is restorable, user-and-model-writable data, so it can hold
    # anything. A garbage value must degrade to SIMPLE — i.e. to SAFE mode, never
    # to OPEN. The floor may not be a way to widen the policy mode.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=0)
        store = _side_store(tmp_path)
        store.set_setting("active_profile", "not-a-profile")
        _side_manager(tmp_path, store).capture(
            trigger="on_command", reason="user_request", verified_working=True
        )
        store.set_setting("active_profile", "developer")
        store.close()
        h.server._active_profile = None
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)
        assert result["ok"] is True
        assert h.server._mode() is PolicyMode.SAFE
    finally:
        _shutdown(h.reader, h.thread)


def test_worker_answers_plainly_when_the_store_cannot_be_built(tmp_path):
    # §6.5. This used to kill the worker thread outright: _ensure_built() raised
    # OUTSIDE the loop's try, the thread died, and every later request hung
    # forever with no frame at all — an unrecoverable state produced by the
    # recovery machinery's own absence.
    h = build_server(tmp_path, register_tool=False, store_factory=_raising_store_factory())
    try:
        assert _error(h, Method.SKILL_LIST, request_id=1)["message"] == _STORE_UNAVAILABLE_MESSAGE
        # Still alive for the NEXT request — the point of the fix.
        assert _error(h, Method.WIDGET_LIST, request_id=2)["message"] == _STORE_UNAVAILABLE_MESSAGE
    finally:
        _shutdown(h.reader, h.thread)


def test_restore_last_working_works_when_the_database_will_not_open(tmp_path):
    # §6.4(c), THE HEADLINE CLAIM: "restore always works, even from a broken
    # config" — including when the broken thing is the database file itself.
    # Without this the error copy above points the user at a control the same
    # code path would guarantee to fail.
    _populate_sidecars(tmp_path)
    failing = {"count": 0}
    real = []

    def factory():
        # Fail exactly once — the cold start — then hand back a real Store, which
        # is what the rebuild does after renaming the damaged file aside.
        failing["count"] += 1
        if failing["count"] == 1:
            raise sqlite3.DatabaseError("file is not a database")
        store = Store(tmp_path / IPC_DB_NAME)
        store.set_setting("widgets_seeded", "1")
        real.append(store)
        return store

    h = build_server(tmp_path, register_tool=False, store_factory=factory)
    try:
        # The list is answered store-free, straight off the sidecar files.
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        # Both rows are there, flags and all, read from `meta` alone — which is
        # exactly why `meta` carries them.
        assert {row["reason"] for row in listed["snapshots"]} == {"user_request", "genesis"}
        assert all(row["verifiedWorking"] for row in listed["snapshots"])
        assert listed["lastWorkingId"]

        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert result["ok"] is True
        assert "rebuilt it" in result["detail"]
        # The session recovered IN PLACE: an ordinary store request works again,
        # without a restart.
        assert _call(h, Method.SKILL_LIST, request_id=3) == {"skills": []}
    finally:
        _shutdown(h.reader, h.thread)


def test_the_damaged_database_is_renamed_aside_not_deleted(tmp_path):
    # Recovery never destroys the user's data. The damaged file may still be
    # forensically useful, and deleting it is not ours to do.
    _populate_sidecars(tmp_path)
    (tmp_path / IPC_DB_NAME).write_bytes(b"this is not a database")
    first = {"done": False}

    def factory():
        if not first["done"]:
            first["done"] = True
            raise sqlite3.DatabaseError("file is not a database")
        return Store(tmp_path / IPC_DB_NAME)

    h = build_server(tmp_path, register_tool=False, store_factory=factory)
    try:
        assert _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)["ok"] is True
        aside = list(tmp_path.glob(f"{IPC_DB_NAME}.damaged-*"))
        assert len(aside) == 1
        assert aside[0].read_bytes() == b"this is not a database"
    finally:
        _shutdown(h.reader, h.thread)


def test_the_cold_start_rebuild_applies_the_restore_point_the_list_just_named(tmp_path):
    # D16, and the single most important assertion in this file after the
    # headline claim itself. The confirm step names a target BEFORE the click;
    # if the button then applies a different snapshot, the naming is worse than
    # useless — it is a promise the floor breaks in the one degraded path it
    # exists for. Both sides now choose through select_payload_to_restore, so
    # they cannot disagree.
    good_id, broken_id = _populate_sidecars_ending_in_an_unproven_one(tmp_path)
    h = build_server(tmp_path, register_tool=False, store_factory=_fail_once_then_open(tmp_path))
    try:
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=1)
        assert listed["lastWorkingId"] == good_id
        # The unproven newer one is listed — it is a real restore point the user
        # may pick deliberately — but it is not what the one-action button gets.
        assert broken_id in [row["id"] for row in listed["snapshots"]]

        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert result["ok"] is True
        rebuilt = Store(tmp_path / IPC_DB_NAME)
        try:
            # The configuration the user was trying to escape did NOT come back.
            assert rebuilt.get_setting("model_choice") == "GOOD"
        finally:
            rebuilt.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cold_start_rebuild_never_puts_back_the_setup_the_user_escaped(tmp_path):
    # The other end of the way back the disk arm now saves. That `pre_restore`
    # point is a genuine restore point — it is how the user undoes a restore — but
    # it is also the newest unverified file on disk, so as a GUESS it would win
    # every time. Months later the database fails to open, the rebuild picks the
    # newest thing it has, and the person is put back into precisely the setup
    # they escaped, under copy saying "the most recent settings I had".
    _populate_sidecars_after_an_escape(tmp_path)
    h = build_server(tmp_path, register_tool=False, store_factory=_fail_once_then_open(tmp_path))
    try:
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)
        assert result["ok"] is True
        rebuilt = Store(tmp_path / IPC_DB_NAME)
        try:
            assert rebuilt.get_setting("model_choice") == "GOOD"
        finally:
            rebuilt.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cold_start_rebuild_from_an_unproven_setup_does_not_claim_it_was_working(tmp_path):
    # D16's honesty half. Rebuilding from the most recent settings on disk is a
    # good answer when nothing better exists — but saying "I rebuilt it from
    # your last working setup" when no turn ever completed against it is the
    # exact species of false reassurance this floor was written against.
    _populate_sidecars_ending_in_an_unproven_one(tmp_path)
    _strip_the_verified_sidecars(tmp_path)
    h = build_server(tmp_path, register_tool=False, store_factory=_fail_once_then_open(tmp_path))
    try:
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)
        assert result["ok"] is True
        assert result["detail"] == _REBUILT_FROM_UNVERIFIED
        assert "last working setup" not in result["detail"]
        assert result["detail"] != _REBUILT_MESSAGE
    finally:
        _shutdown(h.reader, h.thread)


def test_a_failed_cold_start_rebuild_is_not_reported_as_nothing_to_rebuild_from(tmp_path):
    # D17. There WERE restore points; they simply would not go back in. Telling
    # the user there is nothing saved is a false statement about the floor's own
    # storage, and it sends them looking for the wrong problem entirely.
    _write_unappliable_sidecar(tmp_path, snapshot_id="snap-one", captured_at=1000)
    _write_unappliable_sidecar(tmp_path, snapshot_id="snap-two", captured_at=1010)
    h = build_server(tmp_path, register_tool=False, store_factory=_raising_store_factory())
    try:
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)
        assert result["ok"] is False
        assert result["error"] == _REBUILD_FAILED
        assert result["error"] != _NOTHING_TO_REBUILD_FROM
    finally:
        _shutdown(h.reader, h.thread)


def test_a_failed_cold_start_rebuild_leaves_the_database_exactly_where_it_was(tmp_path):
    # D17's other half. The rename used to happen BEFORE the rebuild was known to
    # work, so a failed attempt left a fresh empty database at the live path —
    # and the next click renamed THAT aside too, burying the user's real data one
    # .damaged- file deeper every time they tried. Nothing moves until there is a
    # working replacement to move it for.
    _write_unappliable_sidecar(tmp_path, snapshot_id="snap-one", captured_at=1000)
    (tmp_path / IPC_DB_NAME).write_bytes(b"this is not a database")
    h = build_server(tmp_path, register_tool=False, store_factory=_raising_store_factory())
    try:
        assert _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)["ok"] is False
        assert _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)["ok"] is False
        # Untouched, and no pile of half-built leftovers beside it.
        assert (tmp_path / IPC_DB_NAME).read_bytes() == b"this is not a database"
        assert list(tmp_path.glob(f"{IPC_DB_NAME}.damaged-*")) == []
        assert list(tmp_path.glob(f"{IPC_DB_NAME}.rebuilding-*")) == []
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cold_start_with_no_sidecars_says_so_honestly(tmp_path):
    # The one place the floor genuinely cannot deliver. An honest failure beats a
    # silent one — and beats a cheerful one even more.
    h = build_server(tmp_path, register_tool=False, store_factory=_raising_store_factory())
    try:
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=1)
        assert result == {"ok": False, "error": _NOTHING_TO_REBUILD_FROM}
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        assert listed["snapshots"] == []
        assert listed["warning"] == _NOTHING_TO_REBUILD_FROM
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_list_surfaces_the_warning_after_a_failed_auto_capture(tmp_path):
    # An untested degraded-floor indicator is a degraded floor: this warning is
    # the ONLY signal the user gets that G3 has quietly stopped working.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)   # force the build

        def _boom(**kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        h.server.snapshot_manager.capture = _boom
        assert h.server._snapshot_auto("mode_switch") is False
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        assert listed["warning"] == (
            "Addison couldn't save a restore point just now. Your older "
            "restore points are still there."
        )
    finally:
        _shutdown(h.reader, h.thread)


def test_the_snapshot_warning_is_sticky_until_dismissed(tmp_path):
    # A later successful AUTO capture must not quietly erase the notice — a
    # degraded floor that clears itself is a degraded floor nobody sees. Only the
    # user's own "Save a restore point now" clears it, because that is them seeing it.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.insert_routine(
            id="routine-1",
            name="Morning brief",
            description="A saved routine.",
            plan_json={"steps": []},
            created_at=10,
            created_from_conversation_id=None,
            created_in_mode="safe",
        )
        store.close()
        h.server._snapshot_warning = "Addison couldn't save a restore point just now."
        # A later AUTO capture succeeds (H4, on the routine delete) and the
        # notice survives it.
        assert _call(h, Method.ROUTINE_DELETE, {"routineId": "routine-1"}, request_id=2)["ok"]
        assert "warning" in _call(h, Method.SNAPSHOT_LIST, request_id=5)

        assert _call(h, Method.SNAPSHOT_CREATE, request_id=3)["ok"] is True
        assert "warning" not in _call(h, Method.SNAPSHOT_LIST, request_id=4)
    finally:
        _shutdown(h.reader, h.thread)


def test_restore_clears_live_session_grants(tmp_path):
    # §7.5(d). Grants live only in memory, so a restore that left them alone
    # would leave the session MORE permissive than the config just rolled back to.
    h = build_server(tmp_path)
    try:
        _call(h, Method.SNAPSHOT_CREATE, request_id=1)
        # Change the config, so there is a DISTINCT proven configuration to go
        # back to — restore_last_working never targets the state you are in.
        _call(h, Method.SKILL_CREATE, {"name": "Note", "instructions": "Be brief"}, request_id=9)
        h.server.permission_gate.grant("spy_tool")
        assert h.server.permission_gate.check("spy_tool").value == "granted"
        assert _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)["ok"] is True
        assert h.server.permission_gate.check("spy_tool").value == "not_yet_asked"
    finally:
        _shutdown(h.reader, h.thread)


def test_restore_of_a_provider_whose_key_was_removed_reports_it(tmp_path):
    # §7.5(e). Keys are excluded from snapshots by design (G1), so a provider row
    # comes back saying connected even when its keychain entry is gone. Say so —
    # and do NOT rewrite `connected`, which would be wrong for every provider
    # whose key is perfectly fine.
    h = build_server(tmp_path, register_tool=False, provider_key_probe=lambda pid: False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.upsert_provider_config("anthropic", connected=True, added_at=5)
        _side_manager(tmp_path, store).capture(
            trigger="on_command", reason="user_request", verified_working=True
        )
        store.set_setting("noise", "1")
        result = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert result["ok"] is True
        assert "add it in Settings" in result["detail"]
        restored = store.get_provider_config("anthropic")
        assert restored is not None and restored["connected"] is True
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_snapshot_methods_are_present_in_the_dispatch_table(tmp_path):
    # Guards the _SNAPSHOT_JOBS tuple edit: a namespace that is defined but never
    # wired answers "unknown method" and nothing notices until the UI ships.
    h = build_server(tmp_path, register_tool=False)
    try:
        for method in (
            Method.SNAPSHOT_LIST,
            Method.SNAPSHOT_CREATE,
            Method.SNAPSHOT_RESTORE,
            Method.SNAPSHOT_RESTORE_LAST_WORKING,
            Method.SNAPSHOT_DELETE,
        ):
            assert method in h.server._dispatch_table
    finally:
        _shutdown(h.reader, h.thread)


def test_routine_delete_refuses_when_no_restore_point_could_be_saved(tmp_path):
    # Hook H4, which lives inside the worker loop. Deleting a routine cascades to
    # its run history and the old content exists nowhere else afterwards, so a
    # failed snapshot must REFUSE the delete: refusing is recoverable, an
    # unbackable delete is not.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.insert_routine(
            id="routine-1",
            name="Morning brief",
            description="A saved routine.",
            plan_json={"steps": []},
            created_at=10,
            created_from_conversation_id=None,
            created_in_mode="safe",
        )

        def _boom(**kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        h.server.snapshot_manager.capture = _boom
        result = _call(h, Method.ROUTINE_DELETE, {"routineId": "routine-1"}, request_id=2)
        assert result["ok"] is False
        assert "didn't delete anything" in result["error"]
        assert store.get_routine("routine-1") is not None
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_routine_delete_snapshots_before_the_cascade(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.insert_routine(
            id="routine-1",
            name="Morning brief",
            description="A saved routine.",
            plan_json={"steps": []},
            created_at=10,
            created_from_conversation_id=None,
            created_in_mode="safe",
        )
        assert _call(h, Method.ROUTINE_DELETE, {"routineId": "routine-1"}, request_id=2) == {
            "ok": True
        }
        assert store.get_routine("routine-1") is None
        listed = _call(h, Method.SNAPSHOT_LIST, request_id=3)
        assert "routine_delete" in [row["reason"] for row in listed["snapshots"]]
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_deleting_an_absent_routine_takes_no_snapshot(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        before = len(_call(h, Method.SNAPSHOT_LIST, request_id=1)["snapshots"])
        assert _call(h, Method.ROUTINE_DELETE, {"routineId": "nope"}, request_id=2) == {"ok": True}
        assert len(_call(h, Method.SNAPSHOT_LIST, request_id=3)["snapshots"]) == before
    finally:
        _shutdown(h.reader, h.thread)


# --- the shared IPC fixture is a real payload, not a stand-in ----------------


def _fixture_payloads(tmp_path) -> tuple[Store, list[dict]]:
    """The seeded fixture store and its snapshots' payloads, newest first."""
    from tests.ipc_fixtures import _seeded_store

    store = _seeded_store(tmp_path / "fixture.sqlite3")
    payloads = []
    for row in store.list_config_snapshots():
        snapshot = store.get_config_snapshot(row["id"])
        assert snapshot is not None
        payloads.append(json.loads(snapshot.state_blob))
    return store, payloads


def test_the_shared_ipc_fixture_payloads_carry_the_full_meta_block(tmp_path):
    # D18. `meta` is the row's ONLY backup — contract §5.5 item 7 freezes the
    # whole block precisely because anchor-ness and verified-ness exist nowhere
    # else once the database is gone. A fixture whose meta is `{}` is not a
    # payload the real system can produce, so it cannot pin the shape the
    # frontend and the recovery path both depend on.
    store, payloads = _fixture_payloads(tmp_path)
    try:
        assert payloads
        for payload in payloads:
            meta = payload["meta"]
            assert set(meta) == {
                "id",
                "trigger",
                "reason",
                "created_in_mode",
                "state_fingerprint",
                "verified_working",
                "undeletable",
                "captures_binary",
                "binary_ref",
            }
            snapshot = store.get_config_snapshot(meta["id"])
            assert snapshot is not None
            # meta agrees with the row it backs up, flag for flag.
            assert bool(meta["verified_working"]) is snapshot.verified_working
            assert bool(meta["undeletable"]) is snapshot.undeletable
            assert bool(meta["captures_binary"]) is snapshot.captures_binary
            assert meta["reason"] == snapshot.reason
            # ...and the fingerprint is the real one, over `tables` alone, so
            # rows holding identical tables share it exactly as two real
            # captures of an unchanged config do.
            assert meta["state_fingerprint"] == _fingerprint(payload["tables"])
            assert snapshot.state_fingerprint == _fingerprint(payload["tables"])
    finally:
        store.close()


def test_the_shared_ipc_fixture_payloads_can_rebuild_their_own_rows(tmp_path):
    # D18, behaviourally: this is the regression the fixture exists to catch and
    # could not. rebuild_rows_from_payloads reads `meta["id"]` and skips any
    # payload without one, so with an empty meta every row silently vanished
    # from a cold rebuild — including the G4 anchor, which would come back as an
    # ordinary deletable row if it came back at all.
    store, payloads = _fixture_payloads(tmp_path)
    fresh = Store(tmp_path / "rebuilt.sqlite3")
    try:
        assert rebuild_rows_from_payloads(fresh, payloads) == len(payloads)
        rebuilt = {row["id"]: row for row in fresh.list_config_snapshots()}
        assert set(rebuilt) == {snapshot["id"] for snapshot in store.list_config_snapshots()}
        anchor = rebuilt["snapshot-fixture-2"]
        assert anchor["undeletable"] is True
        assert anchor["verified_working"] is True
        assert anchor["reason"] == "guard_weakened"
    finally:
        fresh.close()
        store.close()


# --- C6 at the RPC layer: no mode may ever hide a way back -------------------

_SNAPSHOT_RPC_SOURCES = (
    Path(__file__).resolve().parent.parent / "agent_core" / "rpc" / "snapshots.py",
    Path(__file__).resolve().parent.parent / "agent_core" / "main.py",
)
# The store-level guard in test_snapshots.py scans SQL strings, which is the
# right shape for store.py and snapshot_manager.py and blind to these two files:
# they hold no SQL and would filter in PYTHON, on a dict key or a wire field.
_MODE_KEY = re.compile(r"created_in_mode|createdInMode")


def _mode_comparisons(source: Path) -> list[str]:
    """Every place this file compares or branches on the display-only mode column."""
    text = source.read_text(encoding="utf-8")
    found: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Compare):
            segment = ast.get_source_segment(text, node) or ""
        elif isinstance(node, ast.comprehension):
            segment = " ".join(ast.get_source_segment(text, c) or "" for c in node.ifs)
        elif isinstance(node, (ast.If, ast.IfExp)):
            segment = ast.get_source_segment(text, node.test) or ""
        else:
            continue
        if _MODE_KEY.search(segment):
            found.append(segment)
    return found


def test_no_snapshot_rpc_handler_filters_on_created_in_mode() -> None:
    """C6 at the layer the frontend actually reads from.

    ``created_in_mode`` is recorded for display and nothing else. A user who
    weakened a guard in Custom, broke things and switched back to Simple must
    still SEE and restore every snapshot — hide them and G3 fails in exactly the
    moment it exists for. The behavioural tests prove today's answer; this one
    fails if someone adds the filter next quarter."""
    for source in _SNAPSHOT_RPC_SOURCES:
        assert _mode_comparisons(source) == [], (
            f"{source.name} branches on created_in_mode. Snapshots are visible and "
            f"restorable in EVERY mode — artifact hiding does not apply to the "
            f"recovery machinery (contract §0 C6)."
        )


def test_the_created_in_mode_guard_would_actually_catch_a_filter(tmp_path) -> None:
    """The guard above passes because the property holds, which is also what it
    would do if the detector were broken. So point it at a file that DOES filter
    and check it objects — otherwise the lock is decorative."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "def _snapshot_list(self, mode):\n"
        "    rows = self.snapshot_manager.list()\n"
        "    return [r for r in rows if r['created_in_mode'] == mode]\n",
        encoding="utf-8",
    )
    assert _mode_comparisons(planted) != []


@pytest.mark.xfail(reason="workspace-trust lands in step 5; the rule is reserved now", strict=True)
def test_the_addison_data_dir_can_never_be_workspace_trusted():
    # §6.6, forward-declared. In OPEN mode run_command executes arbitrary shell,
    # and step 5's workspace-trust suppresses the destructive card inside a
    # trusted directory. If a user trusts a directory containing — or above —
    # Addison's data directory, `rm -rf snapshots/` runs with no card and the
    # floor's storage is protected by nothing but its location. Written now as
    # executable text so step 5 cannot discover this instead of implementing it.
    from agent_core.policy import workspace_trust_allows   # type: ignore[attr-defined]

    assert workspace_trust_allows(Path("~/.addison").expanduser()) is False
