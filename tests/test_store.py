"""SQLite Store — engineering-spec §3, §4.5, §4.8 (test names per §9).

Covers the step-6 additions: action_snapshot round-trip (payload dict survives
the JSON hop), recent-first ordering when created_at collides within a second,
and conversational rewind (``truncate_messages``) keeping the anchor + earlier
messages, dropping later ones, and leaving other conversations and the
action_snapshots table untouched. A tmp-file DB is used (not ``:memory:``) so the
commit-per-write durability path is actually exercised.
"""

from pathlib import Path

import pytest

from agent_core.memory.store import Store
from agent_core.tools.base import ActionSnapshot


@pytest.fixture
def store(tmp_path: Path) -> Store:
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


def test_continued_from_lineage_column_is_persisted(store: Store):
    # §4.8 substrate: v1 only stores lineage, never reads/acts on it.
    store.create_conversation("c1", title="orig", provider_id="anthropic", started_at=0)
    store.create_conversation("c2", title="cont", provider_id="anthropic",
                              started_at=10, continued_from="c1")
    row = store._conn.execute(
        "SELECT continued_from_conversation_id FROM conversations WHERE id = 'c2'"
    ).fetchone()
    assert row["continued_from_conversation_id"] == "c1"
