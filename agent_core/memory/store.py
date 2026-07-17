"""SQLite access layer — engineering-spec §3.

Uses stdlib sqlite3 (no heavy ORM, per §1.2). Applies schema.sql on first open.
Two-tier memory (design-doc §7.6): full session transcript in ``messages``, plus
a ``memory_facts`` table written ONLY on explicit user confirmation — never
silently.

Most read/write helpers below are declared for the orchestrator, undo manager,
and routine library to call; bodies are filled in as those consumers land
(engineering-spec §11). Step 6 lands two of them: the ``action_snapshots``
helpers behind ``UndoManager`` (§4.5) and the ``messages``/``conversations``
transcript persistence that is the §4.8 substrate (the Context Budget Manager
that would *consume* it is v2 — this layer only reads and writes rows).

Every write commits immediately so a crash can't lose an already-returned
effect, and the class is safe against a ``:memory:`` or throwaway tmp-file path
(the tests use both).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_core.tools.base import ActionSnapshot

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._apply_schema()

    def _apply_schema(self) -> None:
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.commit()

    # --- action snapshots (UndoManager) -----------------------------------
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        """Persist one ``ActionSnapshot``. ``undo_payload`` is serialized as JSON
        (the column is TEXT); everything else maps 1:1 to the table."""
        self._conn.execute(
            "INSERT INTO action_snapshots "
            "(id, tool_call_id, tool_id, undo_payload, created_at, reverted) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                snapshot.id,
                snapshot.tool_call_id,
                snapshot.tool_id,
                json.dumps(snapshot.undo_payload),
                snapshot.created_at,
                int(snapshot.reverted),
            ),
        )
        self._conn.commit()

    def recent_unreverted_snapshots(self, limit: int) -> list[ActionSnapshot]:
        """Most recent unreverted snapshots first, at most ``limit`` of them.

        ``created_at`` (epoch seconds) can collide when several actions land in
        the same second, so rowid — a stable, monotonic insertion order — is the
        tiebreaker; both descend so the newest row is returned first. The JSON
        ``undo_payload`` is decoded back into the dataclass, the inverse of
        ``insert_action_snapshot``."""
        rows = self._conn.execute(
            "SELECT id, tool_call_id, tool_id, undo_payload, created_at, reverted "
            "FROM action_snapshots WHERE reverted = 0 "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            ActionSnapshot(
                id=row["id"],
                tool_call_id=row["tool_call_id"],
                tool_id=row["tool_id"],
                undo_payload=json.loads(row["undo_payload"]),
                created_at=row["created_at"],
                reverted=bool(row["reverted"]),
            )
            for row in rows
        ]

    def mark_snapshot_reverted(self, snapshot_id: str) -> None:
        """Flag a snapshot reverted so a later ``undo_last`` can't revert it
        again — ``recent_unreverted_snapshots`` filters ``reverted = 0``."""
        self._conn.execute(
            "UPDATE action_snapshots SET reverted = 1 WHERE id = ?",
            (snapshot_id,),
        )
        self._conn.commit()

    def prune_action_snapshots(self, cutoff: int, keep_last: int) -> None:
        """Retention for the action-rewind window (spec §4.5).

        Semantics: a snapshot is deleted only when it is BOTH older than
        ``cutoff`` (its ``created_at`` epoch seconds is strictly less) AND not
        among the ``keep_last`` most recent snapshots. The two conditions are
        ANDed, so the most-recent ``keep_last`` are always retained regardless of
        age (they can never satisfy the delete), and a snapshot newer than
        ``cutoff`` is always retained regardless of how many exist. This is the
        "20 actions OR 7 days" floor from §4.5: whichever keeps *more* wins.

        "Most recent" uses the same (created_at, rowid) ordering as
        ``recent_unreverted_snapshots``, and — being pure disk retention — spans
        both reverted and unreverted rows. ``keep_last = 0`` disables the
        recency floor (LIMIT 0 selects no rows), pruning purely by ``cutoff``."""
        self._conn.execute(
            "DELETE FROM action_snapshots "
            "WHERE created_at < ? AND id NOT IN ("
            "  SELECT id FROM action_snapshots "
            "  ORDER BY created_at DESC, rowid DESC LIMIT ?"
            ")",
            (cutoff, keep_last),
        )
        self._conn.commit()

    # --- messages / conversations -----------------------------------------
    def create_conversation(
        self,
        id: str,
        title: str | None,
        provider_id: str,
        started_at: int,
        continued_from: str | None = None,
    ) -> None:
        """Insert a conversation row. ``continued_from`` populates the §4.8
        lineage column (``continued_from_conversation_id``); ``summary`` is left
        NULL — v1 never writes it (the Context Budget Manager that would is v2,
        spec §10)."""
        self._conn.execute(
            "INSERT INTO conversations "
            "(id, title, started_at, provider_id, continued_from_conversation_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (id, title, started_at, provider_id, continued_from),
        )
        self._conn.commit()

    def insert_message(
        self,
        id: str,
        conversation_id: str,
        role: str,
        content: str,
        created_at: int,
        tool_call_id: str | None = None,
    ) -> None:
        """Append one message to the transcript. Columns map 1:1 to the schema;
        no summarization or token counting happens here — that is the v2 Context
        Budget Manager's job (spec §4.8/§10), not this read/write layer's."""
        self._conn.execute(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, tool_call_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, conversation_id, role, content, tool_call_id, created_at),
        )
        self._conn.commit()

    def messages_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """The full transcript of one conversation in stable insertion order
        (``created_at`` ascending, rowid ascending as the same-second tiebreaker).
        Rows are returned as plain column-keyed dicts — there is no messages
        ``model.py`` to mirror, and this stays a minimal read helper."""
        rows = self._conn.execute(
            "SELECT id, conversation_id, role, content, tool_call_id, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def truncate_messages(self, conversation_id: str, to_message_id: str) -> None:
        """Conversational rewind (spec §4.5): delete every message AFTER
        ``to_message_id`` in this conversation, keeping ``to_message_id`` itself.
        "After" uses the same (created_at, rowid) ordering as
        ``messages_for_conversation``.

        This is deliberately independent of action rewind — it does NOT touch
        ``action_snapshots``. An unknown ``to_message_id`` (absent, or belonging
        to another conversation) raises ``KeyError`` rather than silently
        deleting nothing or everything."""
        anchor = self._conn.execute(
            "SELECT created_at, rowid FROM messages "
            "WHERE id = ? AND conversation_id = ?",
            (to_message_id, conversation_id),
        ).fetchone()
        if anchor is None:
            raise KeyError(
                f"No message '{to_message_id}' in conversation "
                f"'{conversation_id}'; cannot rewind to it."
            )
        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND "
            "(created_at > ? OR (created_at = ? AND rowid > ?))",
            (conversation_id, anchor["created_at"], anchor["created_at"], anchor["rowid"]),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
