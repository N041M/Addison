"""SQLite Store — engineering-spec §3, §4.5, §4.8 (test names per §9).

Covers the step-6 additions: action_snapshot round-trip (payload dict survives
the JSON hop), recent-first ordering when created_at collides within a second,
and conversational rewind (``truncate_messages``) keeping the anchor + earlier
messages, dropping later ones, and leaving other conversations and the
action_snapshots table untouched. A tmp-file DB is used (not ``:memory:``) so the
commit-per-write durability path is actually exercised.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_core.memory.store import Store
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
