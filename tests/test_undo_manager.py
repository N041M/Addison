"""Undo Manager — engineering-spec §4.5, §9.

Exercised against a REAL ``Store`` on a tmp-file DB and REAL fake tools in a REAL
``ToolRegistry`` (not mocks of either), because the behaviour under test is the
interaction: undo_last reverts most-recent-first, marks each reverted so a second
pass can't double-revert it, and one tool whose ``undo`` raises fails in
isolation without blocking the rest. The fakes are MEDIUM-risk with genuine
``undo`` methods — a MEDIUM tool without a real undo can't even register
(CLAUDE.md invariant 2), which is the whole point.

The retention block near the bottom carries the 2026-08-08 owner decision:
``prune()`` deletes REVERTED rows only, and it now has a call site
(``JsonRpcServer._ensure_built``) — it had none, which is why the table grew
without bound. Each of those tests names the mutation it kills, because the
naive fix (wire the pre-decision prune as written) would have deleted unreverted
rows and taken the way back from changes still sitting on disk.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.providers.router import ModelRouter
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
def store(tmp_path: Path) -> Iterator[Store]:
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


# --- retention (§4.5) — owner decision 2026-08-08 ---------------------------
#
# The recency arm applies to REVERTED rows only; the age arm is kept. Reading
# survivors through ``recent_unreverted_snapshots`` would hide every reverted row
# whether or not retention deleted it, so these read the table directly.


def _all_ids(store: Store) -> set[str]:
    return {r["id"] for r in store._conn.execute("SELECT id FROM action_snapshots").fetchall()}


def _record_reverted(manager: UndoManager, store: Store, snap_id: str, created_at: int) -> None:
    """A snapshot whose action has already been undone — history, not a live
    change. Marked through the store rather than by running ``undo_last`` so the
    row's ``created_at`` stays exactly what the test set."""
    _record(manager, snap_id, "rec", created_at=created_at)
    store.mark_snapshot_reverted(snap_id)


def _manager(store: Store) -> UndoManager:
    registry = ToolRegistry()
    registry.register(_RecordingTool("rec", []))
    return UndoManager(store=store, tool_registry=registry)


def test_prune_never_deletes_unreverted_rows_however_old_or_many(store: Store):
    """KILLS: dropping ``reverted = 1`` from the DELETE — i.e. wiring the prune as
    it was written before the owner decision. Forty unreverted rows, every one of
    them a hundred days old, against a window that keeps one action and seven
    days: the pre-decision statement deletes thirty-nine of them. Each is a change
    still on disk whose ``undo_payload`` is the only way back, so this is the
    load-bearing assertion of the whole retention change."""
    manager = _manager(store)
    now = int(time.time())

    for i in range(40):
        _record(manager, f"live_{i:02d}", "rec", created_at=now - 100 * 86_400 + i)

    manager.prune(max_actions=1, max_age_days=7)

    assert len(_all_ids(store)) == 40
    assert len(store.recent_unreverted_snapshots(limit=100)) == 40   # all still undoable


def test_prune_deletes_reverted_rows_beyond_the_window_oldest_first(store: Store):
    """KILLS three mutations: (1) never deleting anything (retention would be a
    no-op and the table would still grow without bound); (2) computing the
    keep-set over ALL rows instead of reverted ones — the fresh unreverted row
    here is the newest row in the table, so an all-rows keep-set of two would
    protect only ``rev_c`` and delete ``rev_b``; (3) reversing the ORDER BY, which
    would keep the two OLDEST reverted rows instead of the two newest."""
    manager = _manager(store)
    now = int(time.time())

    _record_reverted(manager, store, "rev_a", created_at=now - 100 * 86_400)
    _record_reverted(manager, store, "rev_b", created_at=now - 50 * 86_400)
    _record_reverted(manager, store, "rev_c", created_at=now - 20 * 86_400)
    _record(manager, "live", "rec", created_at=now - 10 * 86_400)   # newest row overall

    manager.prune(max_actions=2, max_age_days=7)

    # Oldest reverted row goes; the two most recent reverted rows stay because the
    # recency floor outranks their age; the unreverted row is never a candidate.
    assert _all_ids(store) == {"rev_b", "rev_c", "live"}


def test_prune_keeps_reverted_rows_inside_the_age_window_however_many(store: Store):
    """The age arm exactly as §4.5 specifies it — "20 actions OR 7 days, whichever
    keeps MORE". KILLS turning the AND into an OR (or dropping the cutoff
    entirely): five reverted rows all one day old, a window that keeps one action,
    and every one of them still survives because none is older than the cutoff."""
    manager = _manager(store)
    now = int(time.time())

    for i in range(5):
        _record_reverted(manager, store, f"rev_{i}", created_at=now - 86_400 + i)

    manager.prune(max_actions=1, max_age_days=7)

    assert _all_ids(store) == {"rev_0", "rev_1", "rev_2", "rev_3", "rev_4"}


def test_prune_keep_last_floor_retains_old_reverted_snapshot_via_manager(store: Store):
    """The other half of "whichever keeps MORE": the recency floor. KILLS dropping
    the ``id NOT IN (...)`` clause — both rows are past the 7-day cutoff, so an
    age-only delete takes them both."""
    manager = _manager(store)
    now = int(time.time())

    _record_reverted(manager, store, "ancient", created_at=now - 100 * 86_400)
    _record_reverted(manager, store, "week_old", created_at=now - 10 * 86_400)

    manager.prune(max_actions=1, max_age_days=7)

    assert _all_ids(store) == {"week_old"}


def test_server_startup_build_prunes_action_snapshots(tmp_path: Path):
    """KILLS the defect this change exists for: ``prune()`` with zero call sites.
    Drives the REAL wiring — ``JsonRpcServer._ensure_built``, the once-per-launch
    worker-thread build — at its REAL defaults (20 actions / 7 days), so removing
    the call, or calling it with a window nobody meant, fails here.

    Twenty-five ancient reverted rows and three ancient unreverted ones: startup
    keeps the twenty most recent reverted rows and all three live ones."""
    db = tmp_path / "startup.db"
    seed = Store(db)
    ancient = int(time.time()) - 100 * 86_400
    for i in range(25):
        seed.insert_action_snapshot(
            ActionSnapshot(
                id=f"rev_{i:02d}",
                tool_call_id=f"call-rev_{i:02d}",
                tool_id="rec",
                undo_payload={},
                created_at=ancient + i,
            )
        )
        seed.mark_snapshot_reverted(f"rev_{i:02d}")
    for i in range(3):
        seed.insert_action_snapshot(
            ActionSnapshot(
                id=f"live_{i}",
                tool_call_id=f"call-live_{i}",
                tool_id="rec",
                undo_payload={},
                created_at=ancient + i,
            )
        )
    seed.close()

    server = JsonRpcServer(
        reader=None,
        writer=None,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(db),
        model_router=ModelRouter(configured={}),
    )
    server._ensure_built()

    survivors = _all_ids(server.store)
    # The five oldest reverted rows are gone; rev_05..rev_24 are the kept twenty.
    assert {f"rev_{i:02d}" for i in range(5)}.isdisjoint(survivors)
    assert {f"rev_{i:02d}" for i in range(5, 25)} <= survivors
    assert {"live_0", "live_1", "live_2"} <= survivors
    assert len(survivors) == 23
    server.store.close()


# --- redo (session-scoped, per-tool opt-in) ---------------------------------


class _RedoableTool(_RecordingTool):
    """A _RecordingTool that also supports redo, logging "redo:<id>"."""

    def redo(self, snapshot: ActionSnapshot) -> None:
        self._log.append(f"redo:{snapshot.id}")


def test_redoable_protocol_membership_matches_redo_support():
    # The UndoManager now discovers redo via isinstance(tool, RedoableTool) instead
    # of duck-typed getattr; this pins the Protocol so a tool with execute+undo+redo
    # is a member and one with only execute+undo is not. SaveFileTool is the real
    # RedoableTool in the tree.
    from agent_core.tools.base import RedoableTool
    from agent_core.tools.save_file import SaveFileTool

    assert isinstance(SaveFileTool(), RedoableTool)          # real redo() -> member
    assert isinstance(_RedoableTool("rec", []), RedoableTool)
    assert not isinstance(_RecordingTool("rec", []), RedoableTool)  # undo only -> not


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
