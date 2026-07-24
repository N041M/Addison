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
import time
from pathlib import Path
from typing import Any

from agent_core.skills import Skill
from agent_core.snapshots.model import ConfigSnapshot
from agent_core.snapshots.scope import _CAPTURED_TABLES, _PRESERVED_SETTING_KEYS
from agent_core.tools.base import ActionSnapshot

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)   # G3: the sidecar snapshot dir is derived from it
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        # WAL pairs cleanly with our commit-per-write convention: each write is still
        # durable (fsync on commit), but readers no longer block the writer (and the
        # writer no longer blocks readers), so the widget/history reads never contend
        # with a turn's writes. busy_timeout lets a momentarily-locked write wait
        # rather than raise "database is locked". journal_mode is persistent on disk,
        # so an older DB flips to WAL on first reopen — intended. (WAL needs a real
        # file; on the rare filesystem that can't do it sqlite silently keeps the old
        # journal mode, which is safe — the convention above still holds.)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._apply_schema()

    def _apply_schema(self) -> None:
        self._migrate_provider_config()
        self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._conn.commit()
        # Mode-scoped safety (owner decision 2026-07-19): add created_in_mode to
        # tables that predate it. Guarded like _migrate_provider_config — a fresh DB
        # already has the column from schema.sql (no-op), an older DB gets it added
        # with the safe default so existing artifacts stay visible in SAFE mode.
        self._add_column_if_missing(
            "routines", "created_in_mode", "TEXT NOT NULL DEFAULT 'safe'"
        )
        self._add_column_if_missing(
            "widgets", "created_in_mode", "TEXT NOT NULL DEFAULT 'safe'"
        )

    def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        cols = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if cols and not any(row["name"] == column for row in cols):
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            self._conn.commit()

    def _migrate_provider_config(self) -> None:
        """Drop a pre-multi-provider ``provider_config`` so the new per-provider
        shape is created fresh (owner decision 2026-07-18). The old role-keyed table
        was never written to by any code, so dropping it loses nothing; a brand-new
        database has no table yet and this is a no-op. ``CREATE TABLE IF NOT EXISTS``
        would otherwise leave a stale-shaped table in place forever."""
        cols = self._conn.execute("PRAGMA table_info(provider_config)").fetchall()
        if cols and not any(row["name"] == "connected" for row in cols):
            self._conn.execute("DROP TABLE provider_config")
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

    def mark_snapshot_unreverted(self, snapshot_id: str) -> None:
        """Redo's mirror of mark_snapshot_reverted: the action is live again,
        so it re-enters the undoable set."""
        self._conn.execute(
            "UPDATE action_snapshots SET reverted = 0 WHERE id = ?",
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
        """Insert a conversation row, or leave the existing one untouched.

        Idempotent on purpose: the server and CLI use fixed ids ("main"/"cli"),
        so on any launch after the first the row already exists on disk — that
        is resumption, not an error, and turns must keep working. ``continued_from``
        populates the §4.8 lineage column (``continued_from_conversation_id``);
        ``summary`` is left NULL — v1 never writes it (the Context Budget Manager
        that would is v2, spec §10)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO conversations "
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

    def truncate_messages(
        self, conversation_id: str, to_message_id: str, *, keep_anchor: bool = True
    ) -> None:
        """Conversational rewind (spec §4.5): delete every message AFTER
        ``to_message_id`` in this conversation. With ``keep_anchor`` (default)
        the anchor itself stays; ``keep_anchor=False`` removes it too — the
        edit-and-resend rewind, where the anchor's text goes back to the
        composer instead of staying in history as a pending request. "After"
        uses the same (created_at, rowid) ordering as
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
        comparison = ">=" if not keep_anchor else ">"
        self._conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND "
            f"(created_at > ? OR (created_at = ? AND rowid {comparison} ?))",
            (conversation_id, anchor["created_at"], anchor["created_at"], anchor["rowid"]),
        )
        self._conn.commit()

    def list_conversations(self) -> list[dict[str, Any]]:
        """Every conversation that has at least one message, newest first.

        The INNER JOIN is deliberate — a conversation row is created lazily on the
        first turn (``_ensure_conversation``), but an abandoned empty chat that
        somehow left a row behind must never surface in history, so a zero-message
        conversation is excluded. ``message_count`` counts only user/assistant
        turns (``m.role != 'tool'`` sums to the non-tool row count) so the tool
        plumbing rows never inflate the displayed length. ``first_user_message``
        backs a NULL-title fallback for legacy rows that were never auto-titled.
        Ordering matches the rest of the file: ``started_at`` then rowid, both
        descending, so the newest conversation comes first with a stable tiebreak
        when two share a start second."""
        rows = self._conn.execute(
            "SELECT c.id, c.title, c.started_at, "
            "       SUM(m.role != 'tool') AS message_count, "
            "       (SELECT content FROM messages "
            "        WHERE conversation_id = c.id AND role = 'user' "
            "        ORDER BY created_at ASC, rowid ASC LIMIT 1) AS first_user_message "
            "FROM conversations c JOIN messages m ON m.conversation_id = c.id "
            "GROUP BY c.id ORDER BY c.started_at DESC, c.rowid DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """One conversation's header row (id/title/started_at/provider_id), or
        None if there is no such conversation. Used to validate a load request
        before rebuilding its transcript."""
        row = self._conn.execute(
            "SELECT id, title, started_at, provider_id "
            "FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def set_conversation_title(self, conversation_id: str, title: str) -> None:
        """First-write-wins auto-title: set the title only while it is still NULL.

        The ``title IS NULL`` guard makes this idempotent and safe to call on every
        turn — once a conversation has a title (auto-derived from its first message,
        or one day user-set), a later call is a no-op and never overwrites it."""
        self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND title IS NULL",
            (title, conversation_id),
        )
        self._conn.commit()

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        """User rename — UNCONDITIONAL, unlike ``set_conversation_title``'s
        first-write-wins auto-title guard. The person is explicitly renaming the
        chat, so this overwrites whatever title it currently has."""
        self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )
        self._conn.commit()

    # --- routines (RoutineBuilder / RoutineLibrary / RoutineEngine, §6) -----
    def insert_routine(
        self,
        id: str,
        name: str,
        description: str,
        plan_json: dict,
        created_from_conversation_id: str | None,
        created_at: int,
        created_in_mode: str = "safe",
    ) -> None:
        """Persist a confirmed Routine (§6.3 — only ever after explicit user
        confirmation). ``plan_json`` is the §6.2 declarative plan; it is stored
        as JSON text and never contains code by construction. ``created_in_mode``
        records the policy mode it was saved under ('safe' | 'open') so SAFE mode
        can hide dev-created routines (policy.py)."""
        self._conn.execute(
            "INSERT INTO routines "
            "(id, name, description, plan_json, created_from_conversation_id, "
            " created_at, updated_at, created_in_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, name, description, json.dumps(plan_json),
             created_from_conversation_id, created_at, created_at, created_in_mode),
        )
        self._conn.commit()

    def list_routines(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, name, description, plan_json, run_count, last_run_at, created_in_mode "
            "FROM routines ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
        return [
            {**dict(row), "plan_json": json.loads(row["plan_json"])} for row in rows
        ]

    def get_routine(self, routine_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, name, description, plan_json, run_count, last_run_at, created_in_mode "
            "FROM routines WHERE id = ?",
            (routine_id,),
        ).fetchone()
        if row is None:
            return None
        return {**dict(row), "plan_json": json.loads(row["plan_json"])}

    def update_routine(
        self, id: str, name: str, description: str, plan_json: dict, updated_at: int
    ) -> None:
        """v1 metadata edit (§6.5): name/description/variable defaults arrive as
        a full re-serialized plan; the step sequence inside it is unchanged by
        the only caller (RoutineLibrary.update_metadata)."""
        self._conn.execute(
            "UPDATE routines SET name = ?, description = ?, plan_json = ?, updated_at = ? "
            "WHERE id = ?",
            (name, description, json.dumps(plan_json), updated_at, id),
        )
        self._conn.commit()

    def touch_routine_run_stats(self, routine_id: str, last_run_at: int) -> None:
        self._conn.execute(
            "UPDATE routines SET run_count = run_count + 1, last_run_at = ? WHERE id = ?",
            (last_run_at, routine_id),
        )
        self._conn.commit()

    def delete_routine(self, routine_id: str) -> None:
        # Run-log rows reference the routine; clear them first (FK enforcement on).
        self._conn.execute("DELETE FROM routine_runs WHERE routine_id = ?", (routine_id,))
        self._conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        self._conn.commit()

    # --- routine run log (§6.4: backs "show what you just did") -------------
    def insert_routine_run(self, id: str, routine_id: str, started_at: int) -> None:
        self._conn.execute(
            "INSERT INTO routine_runs (id, routine_id, started_at, status) "
            "VALUES (?, ?, ?, 'running')",
            (id, routine_id, started_at),
        )
        self._conn.commit()

    def finish_routine_run(
        self, id: str, status: str, completed_at: int, step_log: list[dict]
    ) -> None:
        self._conn.execute(
            "UPDATE routine_runs SET status = ?, completed_at = ?, step_log_json = ? "
            "WHERE id = ?",
            (status, completed_at, json.dumps(step_log), id),
        )
        self._conn.commit()

    # --- app settings (Profiles §4.7, and any other single-row key/value) ---
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Read one ``app_settings`` value, or ``default`` if the key is absent.

        Values are stored as TEXT (the schema's column type); callers that want a
        typed value parse it themselves. This backs the §4.7 ``active_profile``
        lookup, but is deliberately generic — it is plain key/value config, never
        user data or secrets (API keys live only in the OS keychain, §8.3)."""
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        """Upsert one ``app_settings`` value and commit immediately (durability,
        like every other write here). ``updated_at`` tracks the last change."""
        self._conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, int(time.time())),
        )
        self._conn.commit()

    def set_settings(self, values: dict[str, str]) -> None:
        """Upsert SEVERAL ``app_settings`` values in ONE commit — all land or none
        do. Exists for callers whose keys form a single decision (the two Custom
        guard values, ``rpc/guards.py``): written as two ``set_setting`` calls, a
        failure between them would persist half the pair while the handler reports
        "nothing was changed" (adversarial pass, 2026-07-24)."""
        now = int(time.time())
        try:
            self._conn.executemany(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                [(key, value, now) for key, value in values.items()],
            )
        except Exception:
            self._conn.rollback()
            raise
        self._conn.commit()

    # --- provider connection metadata (multi-provider, §4.1.1) --------------
    # NON-SECRET connection state only — which providers are connected, when, the
    # custom server base URL, and an optional cached catalog. API keys NEVER appear
    # here; they live only in the OS keychain (§5, §8.3).
    def upsert_provider_config(
        self,
        provider_id: str,
        *,
        connected: bool,
        added_at: int | None = None,
        base_url: str | None = None,
        catalog_json: str | None = None,
        last_check_ok: bool | None = None,
    ) -> None:
        """Insert or update one provider's connection metadata. ``added_at`` is
        first-write-wins (``COALESCE`` keeps the earliest connect time), so re-connecting
        a provider never resets its "added" date. ``last_check_ok`` maps True/False/None
        to 1/0/NULL."""
        last_ok = None if last_check_ok is None else int(last_check_ok)
        self._conn.execute(
            "INSERT INTO provider_config "
            "(provider_id, connected, added_at, base_url, catalog_json, last_check_ok, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET "
            "  connected = excluded.connected, "
            "  added_at = COALESCE(provider_config.added_at, excluded.added_at), "
            "  base_url = excluded.base_url, "
            "  catalog_json = excluded.catalog_json, "
            "  last_check_ok = excluded.last_check_ok, "
            "  updated_at = excluded.updated_at",
            (
                provider_id,
                int(connected),
                added_at,
                base_url,
                catalog_json,
                last_ok,
                int(time.time()),
            ),
        )
        self._conn.commit()

    def get_provider_config(self, provider_id: str) -> dict[str, Any] | None:
        """One provider's stored connection metadata, or None if never connected."""
        row = self._conn.execute(
            "SELECT provider_id, connected, added_at, base_url, catalog_json, last_check_ok "
            "FROM provider_config WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        return _provider_config_row(row) if row is not None else None

    def list_provider_configs(self) -> list[dict[str, Any]]:
        """Every provider that has connection metadata, in insertion order."""
        rows = self._conn.execute(
            "SELECT provider_id, connected, added_at, base_url, catalog_json, last_check_ok "
            "FROM provider_config ORDER BY rowid ASC"
        ).fetchall()
        return [_provider_config_row(row) for row in rows]

    def delete_provider_config(self, provider_id: str) -> None:
        """Forget a provider's connection metadata (the "Remove"/disconnect action).
        The key itself is deleted separately by the Rust keychain command."""
        self._conn.execute(
            "DELETE FROM provider_config WHERE provider_id = ?", (provider_id,)
        )
        self._conn.commit()

    # --- usage log (§4.8 substrate: token meter + provider latency) ----------
    def insert_usage(
        self,
        *,
        id: str,
        conversation_id: str | None,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int | None,
        created_at: int,
    ) -> None:
        """Record one provider call's token usage + latency. Written by
        orchestrator machinery only (main.py after a turn), never a registry tool."""
        self._conn.execute(
            "INSERT INTO usage_log "
            "(id, conversation_id, provider, model, input_tokens, output_tokens, "
            " latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                id,
                conversation_id,
                provider,
                model,
                int(input_tokens),
                int(output_tokens),
                None if latency_ms is None else int(latency_ms),
                created_at,
            ),
        )
        self._conn.commit()

    def prune_usage_log(self, cutoff: int) -> None:
        """Retention for the §4.8 usage substrate: delete every usage row strictly
        older than ``cutoff`` (its ``created_at`` epoch seconds is less than it).

        Unlike ``prune_action_snapshots`` there is no recency floor — usage rows
        are pure telemetry backing the token-meter/latency widgets (no undo depends
        on them), so a plain age cutoff is all this needs. The caller computes
        ``cutoff`` from the same epoch-seconds clock the rows are written with."""
        self._conn.execute(
            "DELETE FROM usage_log WHERE created_at < ?",
            (cutoff,),
        )
        self._conn.commit()

    def usage_totals_since(self, epoch: int) -> dict[str, int]:
        """Summed input/output tokens for every usage row at or after ``epoch``.

        ``epoch`` is the month boundary (computed by the caller) — 'this month' is
        just 'since the first of the month in epoch seconds'. Returns zeros when
        there is no usage yet, so the token meter renders a clean 0 rather than
        crashing on an empty table."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(input_tokens), 0) AS inp, "
            "       COALESCE(SUM(output_tokens), 0) AS out "
            "FROM usage_log WHERE created_at >= ?",
            (epoch,),
        ).fetchone()
        inp = int(row["inp"])
        out = int(row["out"])
        return {"input": inp, "output": out, "total": inp + out}

    def latest_latency_per_provider(self) -> list[dict[str, Any]]:
        """The most recent recorded latency for each provider, newest row wins.

        Backs the ``provider_latency`` stat + each connected provider's latency
        detail. Rows with no latency (NULL) are ignored. Ordering matches the rest
        of the file: (created_at, rowid) descending, so the newest call per
        provider is the one kept."""
        rows = self._conn.execute(
            "SELECT provider, latency_ms, created_at FROM usage_log u "
            "WHERE latency_ms IS NOT NULL AND created_at = ("
            "  SELECT MAX(created_at) FROM usage_log "
            "  WHERE provider = u.provider AND latency_ms IS NOT NULL"
            ") "
            "GROUP BY provider "
            "ORDER BY created_at DESC, rowid DESC",
            (),
        ).fetchall()
        return [
            {
                "provider": row["provider"],
                "ms": int(row["latency_ms"]),
                "checkedAt": row["created_at"],
            }
            for row in rows
        ]

    # --- widgets (declarative specs — see agent_core/widgets.py) --------------
    def insert_widget(
        self,
        *,
        id: str,
        spec_json: str,
        pinned: bool,
        position: int,
        created_at: int,
        created_in_mode: str = "safe",
    ) -> None:
        self._conn.execute(
            "INSERT INTO widgets (id, spec_json, pinned, position, created_at, created_in_mode) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, spec_json, int(pinned), int(position), created_at, created_in_mode),
        )
        self._conn.commit()

    def list_widgets(self) -> list[dict[str, Any]]:
        """Every stored widget, in user-visible order (position, then insertion)."""
        rows = self._conn.execute(
            "SELECT id, spec_json, pinned, position, created_at, created_in_mode FROM widgets "
            "ORDER BY position ASC, rowid ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "spec_json": row["spec_json"],
                "pinned": bool(row["pinned"]),
                "position": row["position"],
                "created_at": row["created_at"],
                "created_in_mode": row["created_in_mode"],
            }
            for row in rows
        ]

    def get_widget(self, widget_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, spec_json, pinned, position, created_at, created_in_mode "
            "FROM widgets WHERE id = ?",
            (widget_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "spec_json": row["spec_json"],
            "pinned": bool(row["pinned"]),
            "position": row["position"],
            "created_at": row["created_at"],
            "created_in_mode": row["created_in_mode"],
        }

    def set_widget_pinned(self, widget_id: str, pinned: bool) -> None:
        self._conn.execute(
            "UPDATE widgets SET pinned = ? WHERE id = ?", (int(pinned), widget_id)
        )
        self._conn.commit()

    def count_pinned_widgets(self, exclude_id: str | None = None) -> int:
        """How many widgets are currently pinned, optionally excluding one id (so a
        re-pin of an already-pinned widget doesn't count itself against the cap)."""
        if exclude_id is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM widgets WHERE pinned = 1"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM widgets WHERE pinned = 1 AND id != ?",
                (exclude_id,),
            ).fetchone()
        return int(row["n"])

    def next_widget_position(self) -> int:
        """One past the current highest position, so a new widget lands at the end."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM widgets"
        ).fetchone()
        return int(row["m"]) + 1

    def delete_widget(self, widget_id: str) -> None:
        self._conn.execute("DELETE FROM widgets WHERE id = ?", (widget_id,))
        self._conn.commit()

    # --- guidance skills (declarative steering text — see agent_core/skills.py) ---
    # A skill is plain TEXT appended to the transient per-turn system prompt; it never
    # executes and never widens permissions (the gate stays the sole authority). No
    # created_in_mode column — skills apply in both SAFE and OPEN modes.
    def insert_skill(
        self,
        *,
        id: str,
        name: str,
        instructions: str,
        enabled: bool,
        created_at: int,
    ) -> None:
        self._conn.execute(
            "INSERT INTO skills (id, name, instructions, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (id, name, instructions, int(enabled), created_at),
        )
        self._conn.commit()

    def list_skills(self) -> list[dict[str, Any]]:
        """Every stored skill, oldest first (created_at, then rowid tiebreak)."""
        rows = self._conn.execute(
            "SELECT id, name, instructions, enabled, created_at FROM skills "
            "ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "instructions": row["instructions"],
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_enabled_skills(self) -> list[Skill]:
        """The ENABLED skills as ``Skill`` dataclasses, oldest first — the exact
        order they are composed into the system prompt (compose_skills_prompt)."""
        rows = self._conn.execute(
            "SELECT id, name, instructions, enabled, created_at FROM skills "
            "WHERE enabled = 1 ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
        return [
            Skill(
                id=row["id"],
                name=row["name"],
                instructions=row["instructions"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, name, instructions, enabled, created_at FROM skills WHERE id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "instructions": row["instructions"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }

    def update_skill(self, id: str, name: str, instructions: str) -> None:
        """Edit a skill's name/guidance in place (enabled state is left untouched —
        set_skill_enabled owns that toggle)."""
        self._conn.execute(
            "UPDATE skills SET name = ?, instructions = ? WHERE id = ?",
            (name, instructions, id),
        )
        self._conn.commit()

    def set_skill_enabled(self, id: str, enabled: bool) -> None:
        self._conn.execute(
            "UPDATE skills SET enabled = ? WHERE id = ?", (int(enabled), id)
        )
        self._conn.commit()

    def delete_skill(self, skill_id: str) -> None:
        self._conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        self._conn.commit()

    # --- workspace trust (step 5; OPEN-mode coding harness) -------------------
    # Directories the user trusts for card-free undoable file edits. EXCLUDED from
    # snapshots (snapshots/scope.py) — standing consent, not config, like tool_grants.
    # Roots are stored ALREADY canonicalized (realpath) by the caller at grant time,
    # so the confinement check is realpath-vs-realpath.

    def insert_workspace_trust(self, *, root: str, granted_at: int) -> None:
        """Trust a (canonical) directory. Re-granting an existing root just refreshes
        its timestamp — trust is idempotent, never duplicated."""
        self._conn.execute(
            "INSERT INTO workspace_trust (root, granted_at) VALUES (?, ?) "
            "ON CONFLICT(root) DO UPDATE SET granted_at = excluded.granted_at",
            (root, granted_at),
        )
        self._conn.commit()

    def list_workspace_trust(self) -> list[dict[str, Any]]:
        """Every trusted root, newest first."""
        rows = self._conn.execute(
            "SELECT root, granted_at FROM workspace_trust ORDER BY granted_at DESC, rowid DESC"
        ).fetchall()
        return [{"root": row["root"], "granted_at": row["granted_at"]} for row in rows]

    def delete_workspace_trust(self, root: str) -> bool:
        """Revoke a trusted root. Returns True if a row was removed."""
        cur = self._conn.execute("DELETE FROM workspace_trust WHERE root = ?", (root,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- config snapshots (GLOBAL FLOOR G3 — see agent_core/snapshots/) -------
    # App-state rollback, NOT the per-tool-call undo above. These rows hold a JSON
    # row-image of Addison's mutable config tables; the SnapshotManager owns the
    # policy (when to capture, what counts as verified-working, retention) and this
    # layer owns only the SQL. Keys can never appear here: read_config_state SELECTs
    # named columns of tables that hold no key material (G1), and restore never
    # touches the keychain. created_in_mode is stored for display and MUST NOT be
    # used to filter any query — snapshots are visible in every mode, always.

    def insert_config_snapshot(self, snapshot: ConfigSnapshot) -> None:
        """Persist one ``ConfigSnapshot`` verbatim. Booleans cross as ints; the
        blob is already-serialised TEXT (the manager owns serialisation, so
        capture and fingerprint see the same exact bytes)."""
        self._conn.execute(
            "INSERT INTO config_snapshots "
            "(id, created_at, trigger, reason, payload_version, state_blob, "
            " state_fingerprint, verified_working, undeletable, captures_binary, "
            " binary_ref, created_in_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.id,
                snapshot.created_at,
                snapshot.trigger,
                snapshot.reason,
                snapshot.payload_version,
                snapshot.state_blob,
                snapshot.state_fingerprint,
                int(snapshot.verified_working),
                int(snapshot.undeletable),
                int(snapshot.captures_binary),
                snapshot.binary_ref,
                snapshot.created_in_mode,
            ),
        )
        self._conn.commit()

    def list_config_snapshots(self) -> list[dict[str, Any]]:
        """Every snapshot, newest first, WITHOUT ``state_blob`` (metadata only —
        this feeds ``snapshot.list``, and a multi-KB blob has no business on the
        wire). Ordered ``created_at DESC, rowid DESC``; rowid breaks the
        same-second tie exactly as ``recent_unreverted_snapshots`` does.

        Returns every row regardless of ``created_in_mode`` — snapshots are never
        mode-hidden (G3)."""
        rows = self._conn.execute(
            "SELECT id, created_at, trigger, reason, payload_version, state_fingerprint, "
            "       verified_working, undeletable, captures_binary, binary_ref, "
            "       created_in_mode "
            "FROM config_snapshots ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "trigger": row["trigger"],
                "reason": row["reason"],
                "payload_version": row["payload_version"],
                "state_fingerprint": row["state_fingerprint"],
                "verified_working": bool(row["verified_working"]),
                "undeletable": bool(row["undeletable"]),
                "captures_binary": bool(row["captures_binary"]),
                "binary_ref": row["binary_ref"],
                "created_in_mode": row["created_in_mode"],
            }
            for row in rows
        ]

    def get_config_snapshot(self, snapshot_id: str) -> ConfigSnapshot | None:
        """One full snapshot, blob included, as the dataclass. ``None`` when the
        id is unknown — an absent snapshot is a normal state, not an error."""
        row = self._conn.execute(
            "SELECT id, created_at, trigger, reason, payload_version, state_blob, "
            "       state_fingerprint, verified_working, undeletable, captures_binary, "
            "       binary_ref, created_in_mode "
            "FROM config_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return ConfigSnapshot(
            id=row["id"],
            created_at=row["created_at"],
            trigger=row["trigger"],
            reason=row["reason"],
            payload_version=row["payload_version"],
            state_blob=row["state_blob"],
            state_fingerprint=row["state_fingerprint"],
            verified_working=bool(row["verified_working"]),
            undeletable=bool(row["undeletable"]),
            captures_binary=bool(row["captures_binary"]),
            binary_ref=row["binary_ref"],
            created_in_mode=row["created_in_mode"],
        )

    def verified_config_snapshot_refs(self) -> list[dict[str, Any]]:
        """Every ``verified_working = 1`` row, newest first, as lightweight refs:
        ``{id, state_fingerprint, reason, created_at, created_in_mode}``.
        **No blobs.**

        Refs rather than rows, and UNBOUNDED rather than a ``limit``, both on
        purpose. ``restore_last_working()`` walks this list and loads one blob at
        a time via ``get_config_snapshot(id)``, so (a) it can skip a candidate on
        its fingerprint alone without paying to decode it, and (b) one corrupt
        blob can never strand the floor. A capped walk over the NEWEST rows would
        make "genesis sits at the bottom of the walk" false as soon as more
        verified rows than the cap existed — genesis is the OLDEST row. There is
        no cost argument for a cap either: the table holds at most ``KEEP_LAST``
        ordinary rows plus anchors, and this query reads no blobs."""
        rows = self._conn.execute(
            "SELECT id, state_fingerprint, reason, created_at, created_in_mode "
            "FROM config_snapshots WHERE verified_working = 1 "
            "ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def set_config_snapshot_verified(self, snapshot_id: str) -> None:
        """Flag one snapshot verified-working. Idempotent.

        Called from ``SnapshotManager.mark_verified_working()`` for exactly one
        case: a PERMANENT row whose fingerprint proves the completed turn ran
        against its precise contents. Everything else still gets the flag at
        INSERT time, because a pre-change snapshot holds a config the turn never
        ran against and flagging one would make "restore lands somewhere that
        actually ran" false — the exact failure G3 exists to prevent. The
        fingerprint match is what separates the two: it is evidence, not a guess.

        The narrow case is worth the method because the permanent row is the one
        restore point retention can never prune and the triggers refuse to
        delete. Before this it could never become a target however many turns ran
        against it, and a byte-identical clone was written beside it instead.

        Further callers are legitimate for the same reason — a step-2 anchor
        promoted by copy, a repair path rebuilding rows from sidecar payloads —
        provided each carries its own proof. If you are about to call it from a
        new "the turn worked" hook, read ``mark_verified_working()`` first: that
        hook is where the proof is computed, and calling this directly from
        another one would be flagging a row without it."""
        self._conn.execute(
            "UPDATE config_snapshots SET verified_working = 1 WHERE id = ?",
            (snapshot_id,),
        )
        self._conn.commit()

    def delete_config_snapshot(self, snapshot_id: str) -> bool:
        """Delete an ORDINARY snapshot. Returns True when a row was removed,
        False when the id is unknown OR the row is ``undeletable = 1``.

        Three independent guards, deliberately, because "neither user nor model
        can remove it" is a floor: the manager's message check, this method's
        ``AND undeletable = 0``, and — underneath both — the schema's BEFORE
        DELETE trigger, which refuses the row even to a statement that forgot the
        predicate entirely (spec:506 "undeletable by user AND model")."""
        cursor = self._conn.execute(
            "DELETE FROM config_snapshots WHERE id = ? AND undeletable = 0",
            (snapshot_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def prune_config_snapshots(self, *, cutoff: int, keep_last: int) -> None:
        """Retention for the rollback window (amendment §13 Q2).

        A row is deleted only when ALL of these hold:
          * ``created_at < cutoff``  (older than the age floor), AND
          * it is not among the ``keep_last`` most recent rows, AND
          * ``undeletable = 0``      (anchors AND genesis never prune), AND
          * it is not among the newest TWO ``verified_working = 1`` rows.

        The first two mirror ``prune_action_snapshots`` ("whichever keeps MORE
        wins"). The last two are load-bearing on the floor rather than
        housekeeping: pruning the last verified rows would leave
        ``restore_last_working()`` with no target, i.e. G3 silently off.

        Two verified rows, not one. ``restore_last_working()`` skips any verified
        row whose fingerprint equals the CURRENT config — restoring it would be a
        guaranteed no-op. If retention exempted only the newest verified row, the
        one exempt row could be exactly the row the walk skips, leaving nothing.
        Keeping the newest two guarantees the walk always has at least one
        candidate that can actually change something.

        The ``undeletable = 0`` clause is belt-and-braces: the schema trigger
        would abort the statement anyway. It stays because a statement that
        raises mid-prune is a worse failure than one that matches nothing."""
        self._conn.execute(
            "DELETE FROM config_snapshots "
            "WHERE created_at < ? AND undeletable = 0 "
            "  AND id NOT IN ("
            "    SELECT id FROM config_snapshots "
            "    ORDER BY created_at DESC, rowid DESC LIMIT ?"
            "  ) "
            "  AND id NOT IN ("
            "    SELECT id FROM config_snapshots WHERE verified_working = 1 "
            "    ORDER BY created_at DESC, rowid DESC LIMIT 2"
            "  )",
            (cutoff, keep_last),
        )
        self._conn.commit()

    def read_config_state(self) -> dict[str, list[dict[str, Any]]]:
        """A row-image of every CAPTURED config table, for the snapshot payload.

        Table name -> list of row dicts, each dict mapping the exact SQL column
        names to the exact SQLite values (ints stay ints, NULL stays None, TEXT
        stays str — NO coercion, because ``apply_config_state`` inserts them back
        verbatim and any coercion here would make the round-trip lossy). Rows are
        ordered by primary key ASC so the serialised bytes are deterministic and
        the fingerprint is stable.

        The captured set is exactly ``_CAPTURED_TABLES``. Columns are named
        explicitly — never ``SELECT *`` — so a future column cannot slip into a
        payload unreviewed, and no table that could hold key material is listed
        (G1)."""
        state: dict[str, list[dict[str, Any]]] = {}
        for table, columns in _CAPTURED_TABLES.items():
            rows = self._conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY {columns[0]} ASC"
            ).fetchall()
            state[table] = [dict(row) for row in rows]
        return state

    def apply_config_state(self, state: dict[str, list[dict[str, Any]]]) -> None:
        """Replace the captured config tables with ``state``, ATOMICALLY.

        Semantics are REPLACE-ALL within the captured scope: each captured table
        is emptied and refilled from ``state``. A widget/skill/provider created
        after the snapshot is therefore REMOVED, not merged — "restore to the
        last verified-working state" has to mean the state IS that state, or the
        result is a configuration that never actually ran (which is the failure
        amendment §3.2 exists to prevent, and the "make it cheaper" scenario
        exactly: the bad change ADDED a guidance skill, and a merge would leave
        it).

        Tables outside the captured scope are untouched: conversations, messages,
        usage_log, action_snapshots, memory_facts, device_identity, tool_grants,
        and config_snapshots itself all survive a restore unchanged.

        EXCEPTION — one-way latches in ``app_settings``. That table mixes
        reversible user config with irreversible one-way flags, and replace-all
        treats them identically. The keys in ``_PRESERVED_SETTING_KEYS`` (today:
        ``widgets_seeded``) are read before the wipe and written back after, so a
        payload that predates the flag cannot drop it. Without this, restoring an
        old payload would clear ``widgets_seeded`` and the next launch would
        re-seed default widgets the user had deleted.

        FOREIGN KEYS. ``PRAGMA foreign_keys = ON`` above, and SQLite enforces FK
        constraints IMMEDIATELY rather than at COMMIT. Two consequences:

          * INBOUND (``routine_runs.routine_id`` REFERENCES ``routines(id)``, no
            ON DELETE CASCADE). ``DELETE FROM routines`` fails the instant ANY
            run row references a routine — including a routine reinserted later
            in the same transaction — so deleting only the runs whose routine is
            absent from the incoming set is NOT enough: the surviving runs are
            exactly the ones that break the delete. That would abort the restore
            for every user who has ever run a routine, i.e. the floor off for
            precisely the people who use the feature. Hence
            ``PRAGMA defer_foreign_keys = ON`` as the first statement inside the
            transaction: per-transaction, auto-clearing at COMMIT, and exactly
            what a delete-then-refill needs. Orphaned run rows are still deleted,
            so no dangling run survives.
          * OUTBOUND (``routines.created_from_conversation_id`` REFERENCES
            ``conversations(id)``) — a captured table pointing at an excluded
            one. Dormant today, but not dormant at all when a fresh database is
            rebuilt from sidecar payloads: it has no conversations, so every
            routine carrying a provenance pointer would fail at COMMIT. Hence the
            column is NULLed on insert when the referent is absent. A routine
            losing its provenance pointer is cosmetic; a restore that aborts is
            the floor failing.

        UNKNOWN COLUMNS. The per-row INSERT column list is the intersection of
        the declared columns and the row's own keys, so a payload written by an
        older build (before a column was added) inserts without it and SQLite
        applies the declared default. ``_add_column_if_missing`` always supplies
        a default, so this is always well-defined.

        Raises ``sqlite3.Error`` on failure, after rolling back. The caller
        (SnapshotManager.restore) turns that into a plain-language
        RestoreResult."""
        # pysqlite manages implicit transactions (the connection is opened WITHOUT
        # isolation_level=None), so BEGIN IMMEDIATE is only valid if nothing is
        # already open. That holds today because every other Store method commits
        # per-write — an otherwise undocumented invariant this method's atomicity
        # rests on, so it is enforced here rather than assumed.
        if self._conn.in_transaction:
            self._conn.commit()
        preserved = self._read_preserved_settings()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Defer FK enforcement to COMMIT — see the docstring. Per-transaction.
            self._conn.execute("PRAGMA defer_foreign_keys = ON")

            # Run rows whose routine does not survive the restore would be left
            # dangling, so they go first. (The ones whose routine DOES survive are
            # kept — that history is real — and the deferral is what lets their
            # routine be deleted and reinserted underneath them.)
            surviving = [row["id"] for row in state.get("routines", []) if "id" in row]
            placeholders = ", ".join("?" for _ in surviving)
            self._conn.execute(
                "DELETE FROM routine_runs"
                + (f" WHERE routine_id NOT IN ({placeholders})" if surviving else ""),
                tuple(surviving),
            )

            known_conversations = {
                row["id"] for row in self._conn.execute("SELECT id FROM conversations")
            }
            for table, columns in _CAPTURED_TABLES.items():
                self._conn.execute(f"DELETE FROM {table}")
                for row in state.get(table, []):
                    values = dict(row)
                    if table == "routines":
                        referent = values.get("created_from_conversation_id")
                        if referent is not None and referent not in known_conversations:
                            values["created_from_conversation_id"] = None
                    present = [col for col in columns if col in values]
                    if not present:
                        continue
                    self._conn.execute(
                        f"INSERT INTO {table} ({', '.join(present)}) "
                        f"VALUES ({', '.join('?' for _ in present)})",
                        tuple(values[col] for col in present),
                    )

            # The one-way latches go back last, so a payload that predates a flag
            # cannot un-set it.
            for key, (value, updated_at) in preserved.items():
                self._conn.execute(
                    "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (key, value, updated_at),
                )
            self._conn.execute("COMMIT")
        except Exception:
            # A ROLLBACK can ITSELF raise (full disk, I/O error). Letting that
            # escape would replace the real exception AND leave the single worker
            # connection inside an open transaction, after which every later write
            # in the process fails. So the rollback's own failure never escapes,
            # and if it does fail we drop and rebuild the connection rather than
            # carry on with a poisoned one.
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                self._reconnect()
            raise

    def _read_preserved_settings(self) -> dict[str, tuple[str, int]]:
        """The current value of every one-way ``app_settings`` latch, so
        ``apply_config_state`` can write it back after the wipe."""
        preserved: dict[str, tuple[str, int]] = {}
        for key in sorted(_PRESERVED_SETTING_KEYS):
            row = self._conn.execute(
                "SELECT value, updated_at FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is not None:
                preserved[key] = (row["value"], row["updated_at"])
        return preserved

    def _reconnect(self) -> None:
        """Replace a connection stranded in an open transaction by a failed
        ROLLBACK. The schema is already on disk, so this only re-establishes the
        connection and its pragmas — it never re-applies schema.sql, because the
        caller is already unwinding an error and a second failure here would
        bury the first."""
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")

    def close(self) -> None:
        self._conn.close()


def _provider_config_row(row) -> dict[str, Any]:
    """One ``provider_config`` row as a plain dict with typed booleans (SQLite stores
    them as 0/1; NULL ``last_check_ok`` stays None)."""
    last_ok = row["last_check_ok"]
    return {
        "provider_id": row["provider_id"],
        "connected": bool(row["connected"]),
        "added_at": row["added_at"],
        "base_url": row["base_url"],
        "catalog_json": row["catalog_json"],
        "last_check_ok": None if last_ok is None else bool(last_ok),
    }
