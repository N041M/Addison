"""Rewind & Self-Repair — engineering-spec §4.5, design-doc §7.9.

Two independent mechanisms:
  - action rewind      : reverse the most recent N mutating tool actions
  - conversational rewind : truncate message history to an earlier point

They do NOT touch each other. Retention: a startup job prunes action_snapshots
older than the configured window (default 20 actions or 7 days).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from agent_core.tools.base import ActionSnapshot, RedoableTool, UndoableTool
from agent_core.tools.registry import ToolRegistry


@dataclass
class UndoResult:
    snapshot_id: str
    tool_id: str
    success: bool
    detail: str = ""


class UndoManager:
    def __init__(self, store, tool_registry: ToolRegistry) -> None:
        self._store = store               # memory.store.Store — persists action_snapshots
        self._tool_registry = tool_registry
        # Redo stack: snapshots undone this SESSION, most recent last. Deliberately
        # in-memory only — it mirrors the shell's session-scoped file allowlists
        # (a restart drops both), and any NEW action clears it (editor semantics:
        # doing something new discards the undone future).
        self._redo_stack: list[ActionSnapshot] = []

    def record(self, snapshot: ActionSnapshot) -> None:
        """Called by the orchestrator after any tool execution returning a
        non-None snapshot. Persists to the action_snapshots table. A new action
        invalidates whatever was undone before it — the redo stack empties."""
        self._store.insert_action_snapshot(snapshot)
        self._redo_stack.clear()

    def undo_last(self, n: int = 1) -> list[UndoResult]:
        """Reverts the most recent n unreverted snapshots, most recent first,
        calling ``tool.undo(snapshot)`` for each; marks them reverted=1. Each
        success becomes redoable (until a new action or the app closes)."""
        results: list[UndoResult] = []
        for snapshot in self._store.recent_unreverted_snapshots(limit=n):
            tool = self._tool_registry.get(snapshot.tool_id)
            # Registration guarantees every snapshot-recording tool has a real
            # undo (registry.py) — the isinstance narrows the static type to
            # match that runtime invariant.
            assert isinstance(tool, UndoableTool)
            try:
                tool.undo(snapshot)
                self._store.mark_snapshot_reverted(snapshot.id)
                self._redo_stack.append(snapshot)
                results.append(UndoResult(snapshot.id, snapshot.tool_id, True))
            except Exception as exc:  # surfaced to the user in plain language upstream
                results.append(UndoResult(snapshot.id, snapshot.tool_id, False, str(exc)))
        return results

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def redo_last(self, n: int = 1) -> list[UndoResult]:
        """Re-applies the most recently undone actions (LIFO), calling the
        tool's OPTIONAL ``redo(snapshot)``. Redo support is the formalized
        ``RedoableTool`` Protocol (tools/base.py): a tool that isn't one fails that
        step in plain language — redo is opt-in per tool and never weakens the
        mandatory-undo invariant. A re-applied snapshot is live (reverted=0)
        and undoable again."""
        results: list[UndoResult] = []
        for _ in range(min(n, len(self._redo_stack))):
            snapshot = self._redo_stack.pop()
            tool = self._tool_registry.get(snapshot.tool_id)
            if not isinstance(tool, RedoableTool):
                results.append(UndoResult(
                    snapshot.id, snapshot.tool_id, False,
                    "That action can't be re-done — only undone.",
                ))
                continue
            try:
                tool.redo(snapshot)
                self._store.mark_snapshot_unreverted(snapshot.id)
                results.append(UndoResult(snapshot.id, snapshot.tool_id, True))
            except Exception as exc:
                # Not silently dropped: the snapshot goes back so the user can
                # retry (e.g. after clearing whatever blocked the restore).
                self._redo_stack.append(snapshot)
                results.append(UndoResult(snapshot.id, snapshot.tool_id, False, str(exc)))
                break
        return results

    def rewind_conversation(
        self, conversation_id: str, to_message_id: str, *, keep_anchor: bool = True
    ) -> None:
        """Truncates message history back to to_message_id (kept by default;
        ``keep_anchor=False`` drops it too, for edit-and-resend rewind).
        Does NOT touch action_snapshots — the two mechanisms are independent."""
        self._store.truncate_messages(conversation_id, to_message_id, keep_anchor=keep_anchor)

    def prune(self, max_actions: int = 20, max_age_days: int = 7) -> None:
        cutoff = int(time.time()) - max_age_days * 86_400
        self._store.prune_action_snapshots(cutoff=cutoff, keep_last=max_actions)
