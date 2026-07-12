"""SQLite access layer — engineering-spec §3.

Uses stdlib sqlite3 (no heavy ORM, per §1.2). Applies schema.sql on first open.
Two-tier memory (design-doc §7.6): full session transcript in ``messages``, plus
a ``memory_facts`` table written ONLY on explicit user confirmation — never
silently.

Most read/write helpers below are declared for the orchestrator, undo manager,
and routine library to call; bodies are filled in as those consumers land
(engineering-spec §11).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
        raise NotImplementedError("Persist ActionSnapshot — spec §11 step 6.")

    def recent_unreverted_snapshots(self, limit: int) -> list[ActionSnapshot]:
        raise NotImplementedError("Query unreverted snapshots — spec §11 step 6.")

    def mark_snapshot_reverted(self, snapshot_id: str) -> None:
        raise NotImplementedError("Mark snapshot reverted — spec §11 step 6.")

    def prune_action_snapshots(self, cutoff: int, keep_last: int) -> None:
        raise NotImplementedError("Prune snapshots — spec §11 step 6.")

    # --- messages / conversations -----------------------------------------
    def truncate_messages(self, conversation_id: str, to_message_id: str) -> None:
        raise NotImplementedError("Conversational rewind — spec §11 step 6.")

    def close(self) -> None:
        self._conn.close()
