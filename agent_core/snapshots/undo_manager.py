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

from agent_core.tools.base import ActionSnapshot
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

    def record(self, snapshot: ActionSnapshot) -> None:
        """Called by the orchestrator after any tool execution returning a
        non-None snapshot. Persists to the action_snapshots table."""
        self._store.insert_action_snapshot(snapshot)

    def undo_last(self, n: int = 1) -> list[UndoResult]:
        """Reverts the most recent n unreverted snapshots, most recent first,
        calling ``tool.undo(snapshot)`` for each; marks them reverted=1."""
        results: list[UndoResult] = []
        for snapshot in self._store.recent_unreverted_snapshots(limit=n):
            tool = self._tool_registry.get(snapshot.tool_id)
            try:
                tool.undo(snapshot)
                self._store.mark_snapshot_reverted(snapshot.id)
                results.append(UndoResult(snapshot.id, snapshot.tool_id, True))
            except Exception as exc:  # surfaced to the user in plain language upstream
                results.append(UndoResult(snapshot.id, snapshot.tool_id, False, str(exc)))
        return results

    def rewind_conversation(self, conversation_id: str, to_message_id: str) -> None:
        """Truncates message history back to (and including) to_message_id.
        Does NOT touch action_snapshots — the two mechanisms are independent."""
        self._store.truncate_messages(conversation_id, to_message_id)

    def prune(self, max_actions: int = 20, max_age_days: int = 7) -> None:
        cutoff = int(time.time()) - max_age_days * 86_400
        self._store.prune_action_snapshots(cutoff=cutoff, keep_last=max_actions)
