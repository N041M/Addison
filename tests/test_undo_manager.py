"""Undo Manager — engineering-spec §4.5, §9.

Exercised against a REAL ``Store`` on a tmp-file DB and REAL fake tools in a REAL
``ToolRegistry`` (not mocks of either), because the behaviour under test is the
interaction: undo_last reverts most-recent-first, marks each reverted so a second
pass can't double-revert it, and one tool whose ``undo`` raises fails in
isolation without blocking the rest. The fakes are MEDIUM-risk with genuine
``undo`` methods — a MEDIUM tool without a real undo can't even register
(CLAUDE.md invariant 2), which is the whole point.
"""

import time
from pathlib import Path

import pytest

from agent_core.memory.store import Store
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import ToolRegistry


# --- fake tools ------------------------------------------------------------


class _RecordingTool:
    """MEDIUM tool whose real ``undo`` appends the reverted snapshot id to a
    shared list, so a test can assert the order in which reversals happened."""

    def __init__(self, tool_id: str, log: list[str]):
        self.definition = ToolDefinition(
            id=tool_id,
            label=f"Recording {tool_id}",
            description="Fake mutating tool that records its undo calls.",
            risk_tier=RiskTier.MEDIUM,
            parameters_schema={"type": "object", "properties": {}},
        )
        self._log = log

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="did a thing")

    def undo(self, snapshot: ActionSnapshot) -> None:
        self._log.append(snapshot.id)


class _FailingTool:
    """MEDIUM tool whose real ``undo`` always raises — models a reversal that
    can't complete (e.g. the file it would restore is gone)."""

    def __init__(self, tool_id: str = "failing"):
        self.definition = ToolDefinition(
            id=tool_id,
            label="Failing tool",
            description="Fake mutating tool whose undo fails.",
            risk_tier=RiskTier.MEDIUM,
            parameters_schema={"type": "object", "properties": {}},
        )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="did a thing")

    def undo(self, snapshot: ActionSnapshot) -> None:
        raise RuntimeError("could not revert: backup missing")


# --- fixtures / helpers ----------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "undo.db")
    yield s
    s.close()


def _record(manager: UndoManager, snap_id: str, tool_id: str, created_at: int) -> None:
    manager.record(
        ActionSnapshot(
            id=snap_id,
            tool_call_id=f"call-{snap_id}",
            tool_id=tool_id,
            undo_payload={"snap": snap_id},
            created_at=created_at,
        )
    )


# --- tests -----------------------------------------------------------------


def test_undo_last_reverts_in_reverse_chronological_order(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", log))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    _record(manager, "s2", "rec", created_at=2)
    _record(manager, "s3", "rec", created_at=3)

    results = manager.undo_last(n=3)

    assert log == ["s3", "s2", "s1"]                       # newest reverted first
    assert [r.snapshot_id for r in results] == ["s3", "s2", "s1"]
    assert all(r.success for r in results)
    # All three are now marked reverted, so none remain to undo.
    assert store.recent_unreverted_snapshots(limit=10) == []


def test_reverted_snapshots_are_not_double_reverted(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", log))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    _record(manager, "s2", "rec", created_at=2)

    first = manager.undo_last(n=1)
    assert [r.snapshot_id for r in first] == ["s2"]
    assert log == ["s2"]

    # s2 is marked reverted; a second undo_last must move on to s1, never re-touch s2.
    second = manager.undo_last(n=5)
    assert [r.snapshot_id for r in second] == ["s1"]
    assert log == ["s2", "s1"]                             # s2 not reverted twice

    # Nothing left, and a further undo is a no-op rather than an error.
    assert manager.undo_last(n=5) == []
    assert log == ["s2", "s1"]


def test_failing_undo_isolated_others_still_revert(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", log))
    registry.register(_FailingTool("failing"))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s_a", "rec", created_at=1)
    _record(manager, "s_b", "failing", created_at=2)       # this one's undo will raise
    _record(manager, "s_c", "rec", created_at=3)

    results = manager.undo_last(n=3)

    # Order still newest-first; the middle one failed but did not abort the pass.
    by_id = {r.snapshot_id: r for r in results}
    assert [r.snapshot_id for r in results] == ["s_c", "s_b", "s_a"]
    assert by_id["s_c"].success is True
    assert by_id["s_a"].success is True
    assert by_id["s_b"].success is False
    assert "backup missing" in by_id["s_b"].detail          # plain-language failure detail carried
    assert log == ["s_c", "s_a"]                            # the two recording tools reverted

    # The failed snapshot was NOT marked reverted — it alone remains outstanding.
    remaining = store.recent_unreverted_snapshots(limit=10)
    assert [s.id for s in remaining] == ["s_b"]


def test_prune_deletes_old_but_keeps_recent_via_manager(store: Store):
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", []))
    manager = UndoManager(store=store, tool_registry=registry)
    now = int(time.time())

    _record(manager, "ancient", "rec", created_at=now - 100 * 86_400)
    _record(manager, "week_old", "rec", created_at=now - 10 * 86_400)
    _record(manager, "fresh", "rec", created_at=now - 1 * 86_400)

    # Default-ish window: 7 days cutoff, keep the single most recent action.
    manager.prune(max_actions=1, max_age_days=7)

    survivors = {s.id for s in store.recent_unreverted_snapshots(limit=10)}
    assert survivors == {"fresh"}                          # both older-than-7-days rows pruned


def test_prune_keep_last_floor_retains_old_snapshot_via_manager(store: Store):
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", []))
    manager = UndoManager(store=store, tool_registry=registry)
    now = int(time.time())

    _record(manager, "ancient", "rec", created_at=now - 100 * 86_400)
    _record(manager, "week_old", "rec", created_at=now - 10 * 86_400)

    # Both rows are older than the 7-day cutoff, but keep_last=1 forces the most
    # recent of them to be retained regardless of age — the "20 actions" floor.
    manager.prune(max_actions=1, max_age_days=7)

    survivors = {s.id for s in store.recent_unreverted_snapshots(limit=10)}
    assert survivors == {"week_old"}


# --- redo (session-scoped, per-tool opt-in) ---------------------------------


class _RedoableTool(_RecordingTool):
    """A _RecordingTool that also supports redo, logging "redo:<id>"."""

    def redo(self, snapshot: ActionSnapshot) -> None:
        self._log.append(f"redo:{snapshot.id}")


def test_redo_reapplies_in_reverse_undo_order_and_is_undoable_again(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RedoableTool("rec", log))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    _record(manager, "s2", "rec", created_at=2)

    manager.undo_last(n=2)                     # undoes s2, then s1
    assert manager.can_redo()

    results = manager.redo_last(n=2)
    # Editor semantics: the most recently UNDONE comes back first (s1, then s2).
    assert log == ["s2", "s1", "redo:s1", "redo:s2"]
    assert all(r.success for r in results)
    assert not manager.can_redo()
    # Re-applied actions are live again — both back in the undoable set.
    assert {s.id for s in store.recent_unreverted_snapshots(limit=10)} == {"s1", "s2"}


def test_new_action_clears_the_redo_stack(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RedoableTool("rec", log))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    manager.undo_last(n=1)
    assert manager.can_redo()

    # Doing something NEW discards the undone future (standard editor rule).
    _record(manager, "s2", "rec", created_at=2)
    assert not manager.can_redo()
    assert manager.redo_last(n=1) == []


def test_redo_on_tool_without_redo_fails_plainly(store: Store):
    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", log))   # undo only, no redo()
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    manager.undo_last(n=1)

    results = manager.redo_last(n=1)
    assert len(results) == 1 and results[0].success is False
    assert "can't be re-done" in results[0].detail
    # The snapshot stays reverted — nothing was silently re-applied.
    assert store.recent_unreverted_snapshots(limit=10) == []


def test_failed_redo_keeps_the_snapshot_for_retry(store: Store):
    class _FlakyRedoTool(_RedoableTool):
        def __init__(self, tool_id, log):
            super().__init__(tool_id, log)
            self.fail_once = True

        def redo(self, snapshot: ActionSnapshot) -> None:
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("A file with that name is already there — nothing was changed.")
            super().redo(snapshot)

    log: list[str] = []
    registry = ToolRegistry()
    registry.register(_FlakyRedoTool("rec", log))
    manager = UndoManager(store=store, tool_registry=registry)

    _record(manager, "s1", "rec", created_at=1)
    manager.undo_last(n=1)

    failed = manager.redo_last(n=1)
    assert failed[0].success is False
    assert "already there" in failed[0].detail
    assert manager.can_redo()                  # kept: the user may clear the blocker

    retried = manager.redo_last(n=1)
    assert retried[0].success is True
    assert log[-1] == "redo:s1"
