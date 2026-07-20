"""SQLite Store — engineering-spec §3, §4.5, §4.8 (test names per §9).

Covers the step-6 additions: action_snapshot round-trip (payload dict survives
the JSON hop), recent-first ordering when created_at collides within a second,
and conversational rewind (``truncate_messages``) keeping the anchor + earlier
messages, dropping later ones, and leaving other conversations and the
action_snapshots table untouched. A tmp-file DB is used (not ``:memory:``) so the
commit-per-write durability path is actually exercised.
"""

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from agent_core import live_db_guard
from agent_core.memory.store import Store
from agent_core.snapshots.model import ConfigSnapshot
from agent_core.tools.base import ActionSnapshot


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "addison.db")
    yield s
    s.close()


def _snap(snap_id: str, created_at: int, payload: dict | None = None) -> ActionSnapshot:
    return ActionSnapshot(
        id=snap_id,
        tool_call_id=f"call-{snap_id}",
        tool_id="save_file",
        undo_payload={"created_file": f"/tmp/{snap_id}.txt"} if payload is None else payload,
        created_at=created_at,
    )


# --- action snapshots ------------------------------------------------------


def test_snapshot_round_trip_preserves_payload_dict(store: Store):
    payload = {"created_file": "/x/y.txt", "nested": {"n": 1}, "list": [1, 2, 3]}
    store.insert_action_snapshot(_snap("a", created_at=100, payload=payload))

    (loaded,) = store.recent_unreverted_snapshots(limit=10)
    assert loaded.id == "a"
    assert loaded.tool_call_id == "call-a"
    assert loaded.tool_id == "save_file"
    assert loaded.undo_payload == payload  # dict survived the JSON serialize/deserialize
    assert loaded.reverted is False


def test_recent_first_with_same_second_timestamps(store: Store):
    # All three collide on created_at; rowid (insertion order) is the tiebreaker,
    # so the last inserted must come back first.
    for snap_id in ("first", "second", "third"):
        store.insert_action_snapshot(_snap(snap_id, created_at=42))

    ordered = store.recent_unreverted_snapshots(limit=10)
    assert [s.id for s in ordered] == ["third", "second", "first"]


def test_recent_respects_limit_and_reverted_filter(store: Store):
    store.insert_action_snapshot(_snap("old", created_at=1))
    store.insert_action_snapshot(_snap("mid", created_at=2))
    store.insert_action_snapshot(_snap("new", created_at=3))

    assert [s.id for s in store.recent_unreverted_snapshots(limit=2)] == ["new", "mid"]

    store.mark_snapshot_reverted("new")
    # Reverted rows drop out entirely; ordering of the rest is unchanged.
    assert [s.id for s in store.recent_unreverted_snapshots(limit=10)] == ["mid", "old"]


def test_prune_respects_cutoff_and_keep_last(store: Store):
    store.insert_action_snapshot(_snap("ancient", created_at=100))
    store.insert_action_snapshot(_snap("old", created_at=200))
    store.insert_action_snapshot(_snap("recent", created_at=900))

    # cutoff=500 => "ancient" and "old" are old enough to delete, but keep_last=1
    # forces the single most-recent snapshot to be retained no matter its age.
    store.prune_action_snapshots(cutoff=500, keep_last=1)
    survivors = {s.id for s in store.recent_unreverted_snapshots(limit=10)}
    assert survivors == {"recent"}


def test_prune_keep_last_retains_recent_even_when_all_are_old(store: Store):
    for i, snap_id in enumerate(("s1", "s2", "s3", "s4")):
        store.insert_action_snapshot(_snap(snap_id, created_at=10 + i))

    # Everything is older than the cutoff, but keep_last=2 keeps the two newest.
    store.prune_action_snapshots(cutoff=10_000, keep_last=2)
    assert [s.id for s in store.recent_unreverted_snapshots(limit=10)] == ["s4", "s3"]


# --- conversations / messages / rewind -------------------------------------


def _seed_conversation(store: Store, conv_id: str, message_ids: list[str], base: int = 0) -> None:
    store.create_conversation(conv_id, title=conv_id, provider_id="anthropic", started_at=base)
    for i, mid in enumerate(message_ids):
        store.insert_message(
            id=mid,
            conversation_id=conv_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"content-{mid}",
            created_at=base + i,
        )


def test_messages_returned_in_insertion_order(store: Store):
    _seed_conversation(store, "c1", ["m1", "m2", "m3"])
    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1", "m2", "m3"]
    assert store.messages_for_conversation("c1")[0]["content"] == "content-m1"


def test_truncate_keeps_anchor_and_earlier_drops_later(store: Store):
    _seed_conversation(store, "c1", ["m1", "m2", "m3", "m4", "m5"])

    store.truncate_messages("c1", to_message_id="m3")

    kept = [m["id"] for m in store.messages_for_conversation("c1")]
    assert kept == ["m1", "m2", "m3"]  # anchor kept, everything after it gone


def test_truncate_with_same_second_timestamps_uses_rowid(store: Store):
    # Every message shares created_at=7; the rowid tiebreaker must define "after".
    store.create_conversation("c1", title=None, provider_id="anthropic", started_at=0)
    for mid in ("m1", "m2", "m3", "m4"):
        store.insert_message(id=mid, conversation_id="c1", role="user",
                             content=mid, created_at=7)

    store.truncate_messages("c1", to_message_id="m2")
    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1", "m2"]


def test_create_conversation_is_idempotent_across_relaunches(store: Store):
    # The server now mints a fresh uuid per launch, but the CLI still reuses its
    # fixed "cli" id, and conversation.load reopens an existing stored id — so a
    # reused id whose row is already on disk stays a real case. That must read as
    # resumption (the original row kept, no IntegrityError), which the fixed id
    # below stands in for. (Found in the 2026-07 manual pass: the desktop app,
    # then on a fixed id, failed every turn from its second launch onward.)
    store.create_conversation("main", title="first", provider_id="anthropic", started_at=1)
    store.create_conversation("main", title="second", provider_id="anthropic", started_at=2)

    store.insert_message(id="m1", conversation_id="main", role="user",
                         content="still works", created_at=3)
    assert [m["id"] for m in store.messages_for_conversation("main")] == ["m1"]


def test_truncate_without_anchor_removes_the_anchor_too(store: Store):
    # keep_anchor=False is rewind's edit-and-resend mode: the anchored user
    # message leaves history (its text returns to the composer instead).
    _seed_conversation(store, "c1", ["m1", "m2", "m3"], base=0)

    store.truncate_messages("c1", to_message_id="m2", keep_anchor=False)
    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1"]


def test_truncate_leaves_other_conversations_and_snapshots_alone(store: Store):
    _seed_conversation(store, "c1", ["m1", "m2", "m3"], base=0)
    _seed_conversation(store, "c2", ["n1", "n2", "n3"], base=100)
    store.insert_action_snapshot(_snap("snap", created_at=5))

    store.truncate_messages("c1", to_message_id="m1")

    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1"]
    # Untouched: the other conversation's transcript ...
    assert [m["id"] for m in store.messages_for_conversation("c2")] == ["n1", "n2", "n3"]
    # ... and the independent action-rewind mechanism.
    assert [s.id for s in store.recent_unreverted_snapshots(limit=10)] == ["snap"]


def test_truncate_unknown_message_id_raises(store: Store):
    _seed_conversation(store, "c1", ["m1", "m2"])
    with pytest.raises(KeyError, match="nope"):
        store.truncate_messages("c1", to_message_id="nope")


def test_truncate_message_from_other_conversation_raises(store: Store):
    _seed_conversation(store, "c1", ["m1", "m2"], base=0)
    _seed_conversation(store, "c2", ["n1", "n2"], base=100)
    # "n1" exists, but not in c1 — that is still unknown for this rewind.
    with pytest.raises(KeyError):
        store.truncate_messages("c1", to_message_id="n1")
    # And nothing in either conversation was deleted by the failed call.
    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1", "m2"]
    assert [m["id"] for m in store.messages_for_conversation("c2")] == ["n1", "n2"]


# --- conversation history (list / titles) -----------------------------------


def test_list_conversations_newest_first_with_rowid_tiebreak(store: Store):
    _seed_conversation(store, "older", ["m1", "m2"], base=100)
    _seed_conversation(store, "newer", ["n1", "n2"], base=200)
    # Same started_at as "newer": rowid (insertion order) breaks the tie, so the
    # later-created conversation still lists first.
    _seed_conversation(store, "tied", ["t1"], base=200)

    listed = store.list_conversations()
    assert [c["id"] for c in listed] == ["tied", "newer", "older"]
    assert listed[2]["started_at"] == 100


def test_list_conversations_message_count_excludes_tool_rows(store: Store):
    store.create_conversation("c1", title=None, provider_id="anthropic", started_at=0)
    store.insert_message(id="u1", conversation_id="c1", role="user",
                         content="hi", created_at=1)
    store.insert_message(id="t1", conversation_id="c1", role="tool",
                         content="tool output", created_at=2, tool_call_id="call-1")
    store.insert_message(id="a1", conversation_id="c1", role="assistant",
                         content="hello", created_at=3)

    (row,) = store.list_conversations()
    assert row["message_count"] == 2  # user + assistant; the tool row doesn't count


def test_list_conversations_excludes_empty_conversations(store: Store):
    # A row without messages (e.g. left behind before lazy creation existed)
    # must never surface in history.
    store.create_conversation("empty", title=None, provider_id="anthropic", started_at=50)
    _seed_conversation(store, "full", ["m1"], base=10)

    assert [c["id"] for c in store.list_conversations()] == ["full"]


def test_list_conversations_first_user_message_backs_null_title(store: Store):
    # Legacy rows predate auto-titling: title is NULL, so the caller falls back
    # to the FIRST user message — not an assistant one, not a later user one.
    store.create_conversation("legacy", title=None, provider_id="anthropic", started_at=0)
    store.insert_message(id="a0", conversation_id="legacy", role="assistant",
                         content="welcome", created_at=1)
    store.insert_message(id="u1", conversation_id="legacy", role="user",
                         content="first question", created_at=2)
    store.insert_message(id="u2", conversation_id="legacy", role="user",
                         content="second question", created_at=3)

    (row,) = store.list_conversations()
    assert row["title"] is None
    assert row["first_user_message"] == "first question"


def test_set_conversation_title_first_write_wins(store: Store):
    store.create_conversation("c1", title=None, provider_id="anthropic", started_at=0)
    store.insert_message(id="m1", conversation_id="c1", role="user",
                         content="x", created_at=1)

    store.set_conversation_title("c1", "First title")
    store.set_conversation_title("c1", "Second title")  # no-op: title already set

    (row,) = store.list_conversations()
    assert row["title"] == "First title"


def test_rename_conversation_overwrites_unconditionally(store: Store):
    # A user rename must overwrite an existing title (unlike the NULL-guarded
    # auto-title above), including a set one.
    store.create_conversation("c1", title=None, provider_id="anthropic", started_at=0)
    store.insert_message(id="m1", conversation_id="c1", role="user",
                         content="x", created_at=1)
    store.set_conversation_title("c1", "Auto title")

    store.rename_conversation("c1", "My name")

    (row,) = store.list_conversations()
    assert row["title"] == "My name"


def test_continued_from_lineage_column_is_persisted(store: Store):
    # §4.8 substrate: v1 only stores lineage, never reads/acts on it.
    store.create_conversation("c1", title="orig", provider_id="anthropic", started_at=0)
    store.create_conversation("c2", title="cont", provider_id="anthropic",
                              started_at=10, continued_from="c1")
    row = store._conn.execute(
        "SELECT continued_from_conversation_id FROM conversations WHERE id = 'c2'"
    ).fetchone()
    assert row["continued_from_conversation_id"] == "c1"


# --- provider connection metadata (multi-provider, owner decision 2026-07-18) ---


def test_provider_config_upsert_and_get(store: Store):
    store.upsert_provider_config(
        "anthropic", connected=True, added_at=1000, last_check_ok=True
    )
    cfg = store.get_provider_config("anthropic")
    assert cfg is not None
    assert cfg["provider_id"] == "anthropic"
    assert cfg["connected"] is True
    assert cfg["added_at"] == 1000
    assert cfg["last_check_ok"] is True
    assert cfg["base_url"] is None


def test_provider_config_added_at_is_first_write_wins(store: Store):
    # A later reconnect must NOT reset the original added date.
    store.upsert_provider_config("openai", connected=True, added_at=1000, last_check_ok=True)
    store.upsert_provider_config("openai", connected=True, added_at=9999, last_check_ok=True)
    cfg = store.get_provider_config("openai")
    assert cfg is not None
    assert cfg["added_at"] == 1000


def test_provider_config_failed_connect_stays_disconnected(store: Store):
    store.upsert_provider_config("google", connected=False, last_check_ok=False)
    cfg = store.get_provider_config("google")
    assert cfg is not None
    assert cfg["connected"] is False
    assert cfg["last_check_ok"] is False
    assert cfg["added_at"] is None


def test_provider_config_custom_base_url_round_trips(store: Store):
    store.upsert_provider_config(
        "custom", connected=True, added_at=5, base_url="http://localhost:1234/v1", last_check_ok=True
    )
    cfg = store.get_provider_config("custom")
    assert cfg is not None
    assert cfg["base_url"] == "http://localhost:1234/v1"


def test_provider_config_list_and_delete(store: Store):
    store.upsert_provider_config("anthropic", connected=True, added_at=1)
    store.upsert_provider_config("openai", connected=True, added_at=2)
    ids = [c["provider_id"] for c in store.list_provider_configs()]
    assert ids == ["anthropic", "openai"]
    store.delete_provider_config("anthropic")
    assert store.get_provider_config("anthropic") is None
    assert [c["provider_id"] for c in store.list_provider_configs()] == ["openai"]


def test_provider_config_rejects_unknown_provider_id(store: Store):
    # The CHECK constraint guards the four known provider ids.
    with pytest.raises(Exception):
        store.upsert_provider_config("bogus", connected=True)


# --- usage log (§4.8 substrate) --------------------------------------------


def _usage(
    store: Store, provider="anthropic", model="claude", inp=10, out=5, latency: int | None = 42,
    at=1000,
):
    import uuid

    store.insert_usage(
        id=str(uuid.uuid4()),
        conversation_id="conv-1",
        provider=provider,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        latency_ms=latency,
        created_at=at,
    )


def test_usage_totals_since_sums_input_and_output(store: Store):
    _usage(store, inp=100, out=40, at=1000)
    _usage(store, inp=10, out=5, at=2000)
    totals = store.usage_totals_since(0)
    assert totals == {"input": 110, "output": 45, "total": 155}


def test_usage_totals_since_respects_month_boundary(store: Store):
    _usage(store, inp=100, out=40, at=500)   # before the boundary
    _usage(store, inp=10, out=5, at=1500)    # at/after the boundary
    totals = store.usage_totals_since(1000)
    assert totals == {"input": 10, "output": 5, "total": 15}


def test_usage_totals_empty_is_zero(store: Store):
    assert store.usage_totals_since(0) == {"input": 0, "output": 0, "total": 0}


def test_latest_latency_per_provider_keeps_newest(store: Store):
    _usage(store, provider="anthropic", latency=100, at=1000)
    _usage(store, provider="anthropic", latency=42, at=2000)   # newer -> wins
    _usage(store, provider="openai", latency=88, at=1500)
    _usage(store, provider="ollama", latency=None, at=1600)    # no latency -> ignored
    rows = {r["provider"]: r for r in store.latest_latency_per_provider()}
    assert rows["anthropic"]["ms"] == 42
    assert rows["openai"]["ms"] == 88
    assert "ollama" not in rows


def test_prune_usage_log_deletes_only_older_than_cutoff(store: Store):
    _usage(store, at=100)     # old
    _usage(store, at=200)     # old
    _usage(store, at=500)     # exactly at cutoff -> retained (strict <)
    _usage(store, at=900)     # new
    store.prune_usage_log(cutoff=500)
    # Everything strictly before 500 is gone; the boundary row and newer stay.
    assert store.usage_totals_since(0)["total"] == (10 + 5) * 2  # two survivors


def test_prune_usage_log_empty_table_is_safe(store: Store):
    store.prune_usage_log(cutoff=1000)  # no rows -> no error
    assert store.usage_totals_since(0)["total"] == 0


# --- schema indexes ---------------------------------------------------------


def _index_names(store: Store) -> set[str]:
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_expected_indexes_exist_after_open(store: Store):
    assert {
        "idx_messages_conversation_created",
        "idx_usage_log_created",
        "idx_usage_log_provider_created",
        "idx_config_snapshots_created",
        "idx_config_snapshots_verified_created",
    } <= _index_names(store)


def test_reopening_existing_db_is_idempotent(tmp_path: Path):
    # A pre-index DB (schema applied once) must reopen cleanly — executescript
    # re-runs the CREATE INDEX IF NOT EXISTS statements without error.
    db_path = tmp_path / "reopen.db"
    first = Store(db_path)
    _usage(first, at=1000)
    first.close()

    second = Store(db_path)  # must not raise on the second executescript pass
    try:
        assert {
            "idx_messages_conversation_created",
            "idx_usage_log_created",
            "idx_usage_log_provider_created",
            "idx_config_snapshots_created",
            "idx_config_snapshots_verified_created",
        } <= _index_names(second)
        # Existing data is intact and still queryable through the indexed paths.
        assert second.usage_totals_since(0)["total"] == 15
    finally:
        second.close()


# --- connection pragmas -----------------------------------------------------


def test_wal_and_busy_timeout_set_on_open(tmp_path: Path):
    # A real tmp-file DB (APFS/ext4) can do WAL; ``:memory:`` can't, which is why
    # the whole suite already uses tmp files. journal_mode should read back "wal"
    # and busy_timeout should be the 5s we asked for.
    store = Store(tmp_path / "pragmas.db")
    try:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        store.close()


def test_reopening_flips_existing_db_to_wal(tmp_path: Path):
    # journal_mode is persistent on disk, so reopening an older (pre-WAL) DB flips
    # it to WAL — intended — and the reopen itself is clean (no data loss).
    db_path = tmp_path / "reopen-wal.db"
    first = Store(db_path)
    _usage(first, at=1000)
    first.close()

    second = Store(db_path)
    try:
        assert second._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert second.usage_totals_since(0)["total"] == 15  # data survived
    finally:
        second.close()


# --- widgets ----------------------------------------------------------------


def test_widget_crud_and_position_ordering(store: Store):
    store.insert_widget(id="w1", spec_json='{"kind":"stat"}', pinned=True, position=0, created_at=1)
    store.insert_widget(id="w2", spec_json='{"kind":"routine"}', pinned=False, position=1, created_at=2)
    rows = store.list_widgets()
    assert [r["id"] for r in rows] == ["w1", "w2"]
    assert rows[0]["pinned"] is True and rows[1]["pinned"] is False

    store.set_widget_pinned("w2", True)
    w2 = store.get_widget("w2")
    assert w2 is not None
    assert w2["pinned"] is True
    assert store.count_pinned_widgets() == 2
    assert store.count_pinned_widgets(exclude_id="w2") == 1

    assert store.next_widget_position() == 2
    store.delete_widget("w1")
    assert [r["id"] for r in store.list_widgets()] == ["w2"]
    assert store.get_widget("w1") is None


def test_widget_position_orders_by_position_not_insertion(store: Store):
    store.insert_widget(id="late", spec_json="{}", pinned=True, position=5, created_at=1)
    store.insert_widget(id="early", spec_json="{}", pinned=True, position=1, created_at=2)
    assert [r["id"] for r in store.list_widgets()] == ["early", "late"]
    # next position is one past the highest existing position.
    assert store.next_widget_position() == 6


# --- guidance skills --------------------------------------------------------


def test_skill_crud_and_enabled_filter(store: Store):
    store.insert_skill(id="s1", name="Be brief", instructions="Short.", enabled=True, created_at=1)
    store.insert_skill(id="s2", name="Formal", instructions="Formal tone.", enabled=False, created_at=2)

    rows = store.list_skills()
    assert [r["id"] for r in rows] == ["s1", "s2"]        # oldest first
    assert rows[0]["enabled"] is True and rows[1]["enabled"] is False

    # list_enabled_skills returns only enabled rows, as Skill dataclasses.
    enabled = store.list_enabled_skills()
    assert [s.id for s in enabled] == ["s1"]
    assert enabled[0].name == "Be brief" and enabled[0].enabled is True

    store.update_skill("s1", "Be very brief", "One sentence.")
    updated = store.get_skill("s1")
    assert updated is not None
    assert updated["name"] == "Be very brief" and updated["instructions"] == "One sentence."
    assert updated["enabled"] is True                     # update leaves enabled untouched

    store.set_skill_enabled("s2", True)
    assert {s.id for s in store.list_enabled_skills()} == {"s1", "s2"}

    store.delete_skill("s1")
    assert store.get_skill("s1") is None
    assert [r["id"] for r in store.list_skills()] == ["s2"]


# --- config snapshots (GLOBAL FLOOR G3) --------------------------------------
# The SQL half of guaranteed rollback. These tests are about one sentence:
# "restore always works, even from a broken config" — so most of them prove a
# failure mode CANNOT happen rather than that a happy path does.


def _config_snap(
    snap_id: str,
    created_at: int,
    *,
    reason: str = "on_command",
    trigger: str = "on_command",
    blob: str = '{"version": 1, "tables": {}}',
    fingerprint: str | None = None,
    verified: bool = False,
    undeletable: bool = False,
    created_in_mode: str = "safe",
) -> ConfigSnapshot:
    return ConfigSnapshot(
        id=snap_id,
        created_at=created_at,
        trigger=trigger,
        reason=reason,
        payload_version=1,
        state_blob=blob,
        state_fingerprint=fingerprint if fingerprint is not None else f"fp-{snap_id}",
        verified_working=verified,
        undeletable=undeletable,
        created_in_mode=created_in_mode,
    )


def test_config_snapshot_insert_get_and_list_newest_first(store: Store):
    store.insert_config_snapshot(_config_snap("a", created_at=100))
    store.insert_config_snapshot(_config_snap("b", created_at=100))  # same second
    store.insert_config_snapshot(_config_snap("c", created_at=99))

    # created_at DESC, then rowid DESC as the same-second tiebreak: b was inserted
    # after a, so it is the newer of the pair.
    assert [r["id"] for r in store.list_config_snapshots()] == ["b", "a", "c"]

    loaded = store.get_config_snapshot("a")
    assert loaded is not None
    assert loaded.state_blob == '{"version": 1, "tables": {}}'
    assert loaded.payload_version == 1
    assert loaded.verified_working is False and loaded.undeletable is False
    assert loaded.binary_ref is None and loaded.created_in_mode == "safe"


def test_list_config_snapshots_omits_the_state_blob(store: Store):
    store.insert_config_snapshot(_config_snap("a", created_at=1))
    (row,) = store.list_config_snapshots()
    assert "state_blob" not in row
    assert row["state_fingerprint"] == "fp-a"


def test_get_config_snapshot_returns_none_for_unknown_id(store: Store):
    assert store.get_config_snapshot("nope") is None


def test_verified_config_snapshot_refs_ignores_unverified_rows(store: Store):
    store.insert_config_snapshot(_config_snap("good", created_at=1, verified=True))
    store.insert_config_snapshot(_config_snap("unproven", created_at=2))
    assert [r["id"] for r in store.verified_config_snapshot_refs()] == ["good"]

    store.set_config_snapshot_verified("unproven")
    store.set_config_snapshot_verified("unproven")  # idempotent
    assert [r["id"] for r in store.verified_config_snapshot_refs()] == ["unproven", "good"]


def test_verified_config_snapshot_refs_carries_no_blob_and_no_limit(store: Store):
    # Genesis is the OLDEST row, so a capped walk would put it out of reach. 30
    # verified rows above it must not hide it.
    store.insert_config_snapshot(
        _config_snap("genesis", created_at=1, reason="genesis", verified=True, undeletable=True)
    )
    for i in range(30):
        store.insert_config_snapshot(_config_snap(f"s{i}", created_at=100 + i, verified=True))

    refs = store.verified_config_snapshot_refs()
    assert len(refs) == 31
    assert refs[-1]["id"] == "genesis"
    assert set(refs[0]) == {"id", "state_fingerprint", "reason", "created_at", "created_in_mode"}


def test_delete_config_snapshot_refuses_an_undeletable_row(store: Store):
    store.insert_config_snapshot(_config_snap("anchor", created_at=1, undeletable=True))
    store.insert_config_snapshot(_config_snap("ordinary", created_at=2))

    assert store.delete_config_snapshot("anchor") is False
    assert store.get_config_snapshot("anchor") is not None
    assert store.delete_config_snapshot("ordinary") is True
    assert store.delete_config_snapshot("ordinary") is False   # already gone


def test_raw_sql_delete_of_an_anchor_is_refused_by_the_database(store: Store):
    # G4: the statement a future contributor writes without knowing the rule —
    # no WHERE clause at all — must still leave the anchor standing.
    store.insert_config_snapshot(_config_snap("anchor", created_at=1, undeletable=True))
    store.insert_config_snapshot(_config_snap("ordinary", created_at=2))

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM config_snapshots")
    store._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM config_snapshots WHERE id = 'anchor'")
    store._conn.rollback()

    assert store.get_config_snapshot("anchor") is not None


def test_raw_sql_cannot_clear_the_undeletable_flag(store: Store):
    store.insert_config_snapshot(_config_snap("anchor", created_at=1, undeletable=True))
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE config_snapshots SET undeletable = 0")
    store._conn.rollback()

    anchor = store.get_config_snapshot("anchor")
    assert anchor is not None and anchor.undeletable is True


def test_prune_keeps_recent_or_young_whichever_keeps_more(store: Store):
    for i in range(5):
        store.insert_config_snapshot(_config_snap(f"s{i}", created_at=100 + i))

    # Everything is older than the cutoff, so only the recency floor keeps rows.
    store.prune_config_snapshots(cutoff=1000, keep_last=2)
    assert [r["id"] for r in store.list_config_snapshots()] == ["s4", "s3"]

    # And a row newer than the cutoff survives regardless of the recency floor.
    store.prune_config_snapshots(cutoff=0, keep_last=0)
    assert [r["id"] for r in store.list_config_snapshots()] == ["s4", "s3"]


def test_prune_never_removes_an_anchor(store: Store):
    store.insert_config_snapshot(
        _config_snap("anchor", created_at=1, reason="guard_weakened", undeletable=True)
    )
    for i in range(5):
        store.insert_config_snapshot(_config_snap(f"s{i}", created_at=100 + i))

    store.prune_config_snapshots(cutoff=10_000, keep_last=1)
    assert "anchor" in {r["id"] for r in store.list_config_snapshots()}


def test_prune_never_removes_genesis(store: Store):
    # Genesis is the bottom of the restore walk. 50 newer snapshots and a 30-day
    # age cutoff must not prune it out from under the floor.
    store.insert_config_snapshot(
        _config_snap("genesis", created_at=1, reason="genesis", verified=True, undeletable=True)
    )
    for i in range(50):
        store.insert_config_snapshot(_config_snap(f"s{i}", created_at=100 + i))

    store.prune_config_snapshots(cutoff=10_000, keep_last=10)
    assert "genesis" in {r["id"] for r in store.list_config_snapshots()}


def test_prune_never_removes_the_newest_two_verified_rows(store: Store):
    store.insert_config_snapshot(_config_snap("v1", created_at=10, verified=True))
    store.insert_config_snapshot(_config_snap("v2", created_at=20, verified=True))
    store.insert_config_snapshot(_config_snap("v3", created_at=30, verified=True))
    for i in range(5):
        store.insert_config_snapshot(_config_snap(f"s{i}", created_at=40 + i))

    store.prune_config_snapshots(cutoff=10_000, keep_last=0)
    survivors = {r["id"] for r in store.list_config_snapshots()}
    # v3 and v2 are exempt; v1 is not, and the ordinary rows are gone.
    assert survivors == {"v3", "v2"}


# --- the capture / apply state primitives ------------------------------------


def _seed_config(store: Store) -> None:
    """A small but complete config: one of each captured table."""
    store.set_setting("active_profile", "developer")
    store.upsert_provider_config("anthropic", connected=True, added_at=5)
    store.insert_skill(
        id="sk1", name="Be brief", instructions="Short.", enabled=True, created_at=7
    )
    store.insert_widget(
        id="w1", spec_json='{"kind":"stat"}', pinned=True, position=0, created_at=8
    )
    store.insert_routine(
        id="r1",
        name="Weekly summary",
        description="Sums the week up.",
        plan_json={"steps": []},
        created_from_conversation_id=None,
        created_at=9,
    )


def test_read_config_state_captures_exactly_the_declared_tables(store: Store):
    _seed_config(store)
    state = store.read_config_state()
    assert set(state) == {
        "app_settings", "provider_config", "skills", "widgets", "routines"
    }
    assert state["skills"][0]["name"] == "Be brief"
    assert set(state["skills"][0]) == {
        "id", "name", "instructions", "enabled", "created_at"
    }


def test_read_config_state_excludes_transcript_usage_and_undo_tables(store: Store):
    _seed_config(store)
    _seed_conversation(store, "c1", ["m1"])
    _usage(store, at=1)
    store.insert_action_snapshot(_snap("a", created_at=1))

    state = store.read_config_state()
    for excluded in (
        "conversations", "messages", "usage_log", "action_snapshots",
        "memory_facts", "device_identity", "routine_runs", "config_snapshots",
        "tool_grants",
    ):
        assert excluded not in state


def test_read_config_state_preserves_raw_sqlite_values(store: Store):
    store.upsert_provider_config("custom", connected=False, base_url=None)
    (row,) = store.read_config_state()["provider_config"]
    assert row["connected"] == 0 and isinstance(row["connected"], int)
    assert row["base_url"] is None                    # NULL stays None
    assert isinstance(row["provider_id"], str)


def test_apply_config_state_is_replace_all_within_scope(store: Store):
    _seed_config(store)
    state = store.read_config_state()

    # The "make it cheaper" shape: the bad change ADDS a skill and flips a setting.
    store.insert_skill(
        id="sk2", name="Cheapest", instructions="Use the cheapest model.",
        enabled=True, created_at=20,
    )
    store.set_setting("active_profile", "simple")

    store.apply_config_state(state)

    assert [r["id"] for r in store.list_skills()] == ["sk1"]   # removed, not merged
    assert store.get_setting("active_profile") == "developer"


def test_apply_config_state_leaves_conversations_and_messages_untouched(store: Store):
    _seed_config(store)
    _seed_conversation(store, "c1", ["m1", "m2"])
    state = store.read_config_state()

    store.apply_config_state(state)

    assert [m["id"] for m in store.messages_for_conversation("c1")] == ["m1", "m2"]
    assert store.get_conversation("c1") is not None


def test_apply_config_state_leaves_config_snapshots_untouched(store: Store):
    _seed_config(store)
    state = store.read_config_state()
    store.insert_config_snapshot(_config_snap("the-way-back", created_at=1, verified=True))

    store.apply_config_state(state)

    assert store.get_config_snapshot("the-way-back") is not None


def test_apply_config_state_succeeds_when_routines_have_run_history(store: Store):
    # THE restore-breaking case. The routine SURVIVES the restore, so its run rows
    # survive too — and those surviving rows are exactly what makes the DELETE FROM
    # routines fail without PRAGMA defer_foreign_keys.
    _seed_config(store)
    store.insert_routine_run(id="run1", routine_id="r1", started_at=11)
    state = store.read_config_state()

    store.apply_config_state(state)     # must not raise IntegrityError

    assert [r["id"] for r in store.list_routines()] == ["r1"]
    rows = store._conn.execute("SELECT id FROM routine_runs").fetchall()
    assert [r["id"] for r in rows] == ["run1"]


def test_apply_config_state_clears_orphaned_routine_runs(store: Store):
    _seed_config(store)
    state = store.read_config_state()      # captured while only r1 exists

    store.insert_routine(
        id="r2", name="Later", description="Added after the snapshot.",
        plan_json={"steps": []}, created_from_conversation_id=None, created_at=30,
    )
    store.insert_routine_run(id="run2", routine_id="r2", started_at=31)

    store.apply_config_state(state)

    assert [r["id"] for r in store.list_routines()] == ["r1"]
    assert store._conn.execute("SELECT COUNT(*) FROM routine_runs").fetchone()[0] == 0


def test_restore_of_a_routine_whose_conversation_is_gone_succeeds(store: Store):
    # The outbound FK, in the §6.4(c) shape: a payload rebuilt into a database
    # that has no conversations at all. The provenance pointer is cosmetic; a
    # restore that aborts is the floor failing.
    state = {
        "app_settings": [],
        "provider_config": [],
        "skills": [],
        "widgets": [],
        "routines": [{
            "id": "r1", "name": "Orphan", "description": "Its chat is gone.",
            "plan_json": "{}", "created_from_conversation_id": "vanished",
            "created_at": 1, "updated_at": 1, "run_count": 0, "last_run_at": None,
            "created_in_mode": "safe",
        }],
    }
    assert store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0

    store.apply_config_state(state)       # must not raise

    row = store._conn.execute(
        "SELECT created_from_conversation_id FROM routines WHERE id = 'r1'"
    ).fetchone()
    assert row["created_from_conversation_id"] is None


def test_apply_config_state_preserves_one_way_setting_latches(store: Store):
    # A payload that predates the flag must not un-set it, or the next launch
    # re-seeds default widgets the person deleted on purpose.
    state = store.read_config_state()          # captured before the latch was set
    store.set_setting("widgets_seeded", "1")

    store.apply_config_state(state)

    assert store.get_setting("widgets_seeded") == "1"


def test_a_payload_missing_a_later_added_column_still_restores(store: Store):
    # Stands in for a payload written before created_in_mode existed. Rejecting it
    # would evaporate the user's whole rollback history at upgrade time.
    state = {
        "app_settings": [], "provider_config": [], "skills": [], "routines": [],
        "widgets": [{
            "id": "w1", "spec_json": '{"kind":"stat"}', "pinned": 1,
            "position": 0, "created_at": 1,
        }],
    }
    store.apply_config_state(state)

    (widget,) = store.list_widgets()
    assert widget["id"] == "w1"
    assert widget["created_in_mode"] == "safe"      # the schema default applied


def test_apply_config_state_rolls_back_completely_on_failure(store: Store):
    _seed_config(store)
    before = _config_fingerprint(store)

    bad = store.read_config_state()
    bad["widgets"].append({          # spec_json is NOT NULL
        "id": "broken", "spec_json": None, "pinned": 1, "position": 9,
        "created_at": 1, "created_in_mode": "safe",
    })
    bad["skills"] = []               # would have landed before the failing row

    with pytest.raises(sqlite3.Error):
        store.apply_config_state(bad)

    assert _config_fingerprint(store) == before
    assert not store._conn.in_transaction


def test_apply_config_state_survives_a_failing_rollback(store: Store):
    # A ROLLBACK that itself fails must not replace the real error, and must not
    # strand the single worker connection inside an open transaction — every later
    # write in the process would fail.
    _seed_config(store)
    before = _config_fingerprint(store)
    real_conn = store._conn
    store._conn = _RollbackBreaker(real_conn)   # type: ignore[assignment]

    bad = store.read_config_state()
    bad["widgets"].append({"id": "broken", "spec_json": None, "pinned": 1,
                           "position": 9, "created_at": 1, "created_in_mode": "safe"})

    with pytest.raises(sqlite3.IntegrityError):   # the ORIGINAL error, not the rollback's
        store.apply_config_state(bad)

    assert store._conn is not real_conn           # reconnected, not poisoned
    assert not store._conn.in_transaction
    assert _config_fingerprint(store) == before
    store.set_setting("still_writable", "yes")    # the connection genuinely works


class _RollbackBreaker:
    """A connection whose ROLLBACK fails — the full-disk / I-O-error case."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def execute(self, sql: str, *args):
        if sql.strip().upper().startswith("ROLLBACK"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)


def _config_fingerprint(store: Store) -> str:
    """A cheap byte-level image of the captured tables, for atomicity asserts."""
    return repr(store.read_config_state())


def test_reopening_never_drops_config_snapshots(tmp_path: Path):
    # G4 against a future drop-and-recreate migration: an anchor written, the DB
    # closed, the DB reopened (which re-runs the whole schema script) — the anchor
    # is still there, because dropping this table would destroy the way back.
    db_path = tmp_path / "anchors.db"
    first = Store(db_path)
    first.insert_config_snapshot(
        _config_snap("anchor", created_at=1, reason="guard_weakened", undeletable=True)
    )
    first.close()

    second = Store(db_path)
    try:
        anchor = second.get_config_snapshot("anchor")
        assert anchor is not None and anchor.undeletable is True
    finally:
        second.close()


def test_the_live_database_guard_is_not_escapable_by_a_relative_path(tmp_path: Path):
    """The guard must resolve the path before judging it.

    It exists because a build agent once constructed a real ``Store`` against the
    default path and wrote an undeletable row into the owner's live database — a row
    the recovery machinery then refused to remove, by design. The guard compared
    ``expanduser()``'d parents, and ``Path.parents`` walks the LITERAL components, so
    a path that only resolves into ``~/.addison`` slipped past: ``~/Desktop/../.addison``
    has no ``~/.addison`` component at all.

    Deliberately not asserting on the ``..`` spelling alone — ``~/.addison/../.addison``
    was caught by the old guard too, which is what made the hole easy to miss.
    """
    escaping = Path.home() / "Desktop" / ".." / ".addison" / "should-never-open.sqlite3"

    # This test aims at the real directory, so when it FAILS it does the very thing
    # it forbids. That is not hypothetical: a 151 KB database of exactly this name
    # was found sitting in the owner's live directory, and it reappeared the moment
    # the guard was weakened to check the failure was detected. Clean up regardless
    # of outcome, and say so if a file was made.
    try:
        with pytest.raises(AssertionError, match="live database"):
            Store(escaping)
        assert not escaping.resolve().exists(), (
            "the guard let the file through before raising — check ~/.addison"
        )
    finally:
        # "" plus the WAL pair: Store turns on journal_mode=WAL, so a leaked database
        # is three files, and deleting only the first leaves a 300 KB -wal behind.
        landed = escaping.resolve()
        for suffix in ("", "-wal", "-shm"):
            landed.with_name(landed.name + suffix).unlink(missing_ok=True)

    # The guard still lets an ordinary test path through, so it cannot pass by
    # refusing everything.
    ordinary = Store(tmp_path / "fine.sqlite3")
    ordinary.close()


@pytest.fixture
def fake_live_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the guard's live-data directory at a throwaway tree.

    Lets the allow/deny logic be exercised in both directions without a test ever
    aiming at the owner's real ``~/.addison``. The end-to-end proof that the REAL
    directory is covered is
    ``test_the_guard_is_armed_outside_pytest_by_importing_agent_core`` below.
    """
    live = tmp_path / "pretend-home" / ".addison"
    live.mkdir(parents=True)
    monkeypatch.setattr(live_db_guard, "_LIVE_DATA_DIR", live)
    return live


def test_a_bare_sqlite3_connect_cannot_reach_the_live_directory(fake_live_dir: Path):
    """Bypass 1: code that never imports ``Store`` at all.

    The guard this replaced wrapped ``Store.__init__``, so four lines of stdlib
    walked straight around it. ``sqlite3.connect`` is the choke point every route
    to the file shares, which is why the check moved there.
    """
    target = fake_live_dir / "bare-connect.sqlite3"

    with pytest.raises(AssertionError, match="live database"):
        sqlite3.connect(target)

    assert not target.exists(), "the guard raised but sqlite3 had already made the file"


def test_a_store_subclass_that_skips_super_init_is_still_blocked(fake_live_dir: Path):
    """Bypass 2: a ``Store`` subclass that never calls ``super().__init__``.

    It inherits every Store method, so it is a working Store for all practical
    purposes — it just skips the one place the old guard was patched into.
    """

    class _RogueStore(Store):
        def __init__(self, db_path) -> None:  # deliberately no super().__init__()
            self.db_path = str(db_path)
            self._conn = sqlite3.connect(self.db_path)

    target = fake_live_dir / "subclass.sqlite3"

    with pytest.raises(AssertionError, match="live database"):
        _RogueStore(target)

    assert not target.exists()


def test_the_running_app_may_open_its_own_live_database(fake_live_dir: Path):
    """The guard must not break the one process that is SUPPOSED to open this path.

    This is the whole difficulty: the app opens exactly the path everything else is
    refused. It gets through by saying so — ``main()`` calls ``allow_live_database()``
    on itself — rather than by looking like the app to a heuristic, which a probe
    could satisfy by accident.
    """
    target = fake_live_dir / "addison.sqlite3"

    with pytest.raises(AssertionError, match="live database"):
        sqlite3.connect(target)

    live_db_guard.allow_live_database()
    connection = sqlite3.connect(target)
    try:
        connection.execute("CREATE TABLE t (x INTEGER)")
    finally:
        connection.close()
    assert target.exists()


def test_the_guard_is_armed_outside_pytest_by_importing_agent_core():
    """The gap that actually fired: an ad-hoc probe script, with no pytest anywhere.

    A 151 KB Addison-schema database was found in the owner's live directory,
    written by a one-off probe an hour before the old pytest-fixture guard was
    committed. Probe scripts are how this project is developed, so the guard has to
    be armed by importing the package, not by collecting a conftest.

    Deliberately a subprocess aimed at the REAL ``~/.addison``: every other test
    here uses a stand-in directory, so without this one nothing proves the path
    that matters is covered. The probe filename is unique and unlinked afterwards
    in case the guard is broken and the file is actually created.
    """
    repo_root = Path(__file__).resolve().parents[1]
    probe = Path.home() / ".addison" / f"guard-check-{uuid4().hex}.sqlite3"
    source = (
        "import sys, sqlite3\n"
        "import agent_core  # arming the guard is this import's whole job\n"
        "try:\n"
        f"    sqlite3.connect({str(probe)!r})\n"
        "except AssertionError as exc:\n"
        "    print(exc)\n"
        "    sys.exit(3)\n"
        "sys.exit(0)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 3, (
            "a plain python process opened the live directory unchallenged; "
            f"exit={result.returncode} stdout={result.stdout} stderr={result.stderr}"
        )
        assert "live database" in result.stdout
        assert not probe.exists()
    finally:
        probe.unlink(missing_ok=True)
