"""snapshot_now — the LOW, CAPTURE-ONLY tool that saves a restore point on request
(GLOBAL FLOOR G3, amendment §3.2).

Two things are under test and they are different in kind:

  * BEHAVIOUR — the tool answers "can't save yet" before the manager exists,
    makes the exact ``capture(trigger="on_command", reason="user_request")`` call
    the Settings control makes, translates a capture failure into one plain
    sentence, and clears the sticky capture-failure warning on success just like
    the Settings button does.
  * THE CAPTURE-ONLY FLOOR, structurally — a source-level test that fails the
    build if this module ever reaches any manager verb other than ``capture``
    (restore / restore_last_working / delete / prune / mint_anchor /
    mark_verified_working). Behaviour alone would only prove today's code; this
    stops a later edit from teaching the tool to undo its own capture, which would
    be a deletion and would make "capture-only" false.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from agent_core.snapshots.snapshot_manager import SnapshotManager
from agent_core.tools import snapshot_now as snapshot_now_module
from agent_core.tools.base import ExecutionContext, RiskTier
from agent_core.tools.snapshot_now import (
    _FAILED,
    _NOT_READY,
    _SAVED,
    SnapshotNowTool,
)

_SOURCE = Path(snapshot_now_module.__file__)


class _RecordingManager:
    """A stand-in that records ``capture`` calls. It deliberately implements ONLY
    ``capture`` — if the tool ever reached for another manager verb, the test would
    fail with an AttributeError rather than passing silently."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    def capture(self, *, trigger: str, reason: str, **_: Any) -> Any:
        self.calls.append((trigger, reason))
        if self._raises:
            raise RuntimeError("disk full")
        return object()


def _ctx() -> ExecutionContext:
    return ExecutionContext(conversation_id="c1")


def _ref(manager: object) -> Callable[[], SnapshotManager | None]:
    """A late-bound ref returning a duck-typed manager double. The cast tells the
    type checker the double stands in for a real ``SnapshotManager`` — the tool only
    ever calls ``capture`` on it, which ``_RecordingManager`` implements."""
    return lambda: cast("SnapshotManager | None", manager)


# --- definition ------------------------------------------------------------


def test_it_is_low_risk_and_has_no_undo() -> None:
    # Invariant 2, from the same side read_web_page is checked: a capture-only tool
    # loses nothing, so it stays LOW and must NOT grow a no-op undo() to look busy.
    assert SnapshotNowTool.definition.risk_tier is RiskTier.LOW
    assert getattr(SnapshotNowTool, "undo", None) is None


def test_it_takes_no_arguments() -> None:
    schema = SnapshotNowTool.definition.parameters_schema
    assert schema.get("type") == "object"
    assert schema.get("properties") == {}
    assert not schema.get("required")


def test_its_label_and_description_read_plainly() -> None:
    definition = SnapshotNowTool.definition
    assert definition.id == "snapshot_now"
    assert definition.label == "Save a restore point"
    # It must say what it does FOR the person and reassure that it only adds.
    assert "restore point" in definition.description
    assert "never changes or removes" in definition.description
    for jargon in ("snapshot", "capture", "config", "SQLite", "row"):
        assert jargon not in definition.description


# --- behaviour -------------------------------------------------------------


def test_it_says_it_cannot_save_yet_before_the_manager_exists() -> None:
    # The pre-store window: build_registry runs before the worker thread builds the
    # manager, so the ref resolves to None. That must be a plain sentence, not a raise.
    tool = SnapshotNowTool(manager_ref=lambda: None)
    result = tool.execute({}, _ctx())
    assert result.success is False
    assert result.content == _NOT_READY


def test_a_successful_save_makes_the_settings_button_call() -> None:
    manager = _RecordingManager()
    tool = SnapshotNowTool(manager_ref=_ref(manager))
    result = tool.execute({}, _ctx())
    assert result.success is True
    assert result.content == _SAVED
    # EXACTLY the call rpc/snapshots._snapshot_create makes — same trigger, same slug.
    assert manager.calls == [("on_command", "user_request")]


def test_a_capture_failure_becomes_one_plain_sentence() -> None:
    manager = _RecordingManager(raises=True)
    tool = SnapshotNowTool(manager_ref=_ref(manager))
    result = tool.execute({}, _ctx())
    assert result.success is False
    assert result.content == _FAILED
    # It still TRIED — the failure is the manager's, translated, not a skipped call.
    assert manager.calls == [("on_command", "user_request")]


def test_a_successful_save_clears_the_sticky_warning() -> None:
    # Parity with the Settings control (rpc/snapshots._snapshot_create): a save
    # proves writes work again, so the "couldn't save" notice is cleared.
    cleared: list[bool] = []
    tool = SnapshotNowTool(
        manager_ref=_ref(_RecordingManager()),
        on_captured=lambda: cleared.append(True),
    )
    result = tool.execute({}, _ctx())
    assert result.success is True
    assert cleared == [True]


def test_the_warning_is_not_cleared_when_the_save_fails() -> None:
    # A failed save has proved the opposite of "writes work", so the notice stays up.
    cleared: list[bool] = []
    tool = SnapshotNowTool(
        manager_ref=_ref(_RecordingManager(raises=True)),
        on_captured=lambda: cleared.append(True),
    )
    result = tool.execute({}, _ctx())
    assert result.success is False
    assert cleared == []


def test_the_warning_is_not_cleared_before_the_manager_exists() -> None:
    cleared: list[bool] = []
    tool = SnapshotNowTool(manager_ref=lambda: None, on_captured=lambda: cleared.append(True))
    assert tool.execute({}, _ctx()).success is False
    assert cleared == []


def test_a_broken_warning_clear_never_fails_a_good_save() -> None:
    # The clear is a display nicety; it must never turn a successful capture into a
    # failure the model then apologises for.
    def _boom() -> None:
        raise RuntimeError("frontend gone")

    tool = SnapshotNowTool(manager_ref=_ref(_RecordingManager()), on_captured=_boom)
    result = tool.execute({}, _ctx())
    assert result.success is True
    assert result.content == _SAVED


# --- integration: it really writes a row through a real manager ------------


def test_it_actually_writes_a_restore_point_through_a_real_manager(tmp_path: Path) -> None:
    """The mock tests prove the call shape; this proves the wiring is real. A live
    SnapshotManager over a tmp-file Store (never ~/.addison — live_db_guard) gains a
    ``user_request`` row after one execute, and nothing is deleted."""
    from agent_core.memory.store import Store

    store = Store(tmp_path / "addison.sqlite3")
    try:
        manager = SnapshotManager(
            store=store,
            snapshot_dir=tmp_path / "snapshots",
            created_the_database=True,
        )
        before = len(manager.list())
        tool = SnapshotNowTool(manager_ref=_ref(manager))

        result = tool.execute({}, _ctx())

        assert result.success is True
        rows = manager.list()
        assert len(rows) == before + 1, "a restore point was not added"
        newest = rows[0]
        assert newest["reason"] == "user_request"
        assert newest["trigger"] == "on_command"
    finally:
        store.close()


# --- the capture-only floor, structurally ----------------------------------

# The only SnapshotManager verb this module may ever call. Everything a restore or
# a deletion would need is on this list of things it must NOT touch.
_FORBIDDEN_MANAGER_VERBS = frozenset(
    {
        "restore",
        "restore_last_working",
        "delete",
        "prune",
        "mint_anchor",
        "mark_verified_working",
    }
)


def test_the_tool_only_ever_calls_capture_on_the_manager() -> None:
    """CAPTURE-ONLY, locked at the source. Adding a row can never lose anything, so
    the tool stays LOW with no undo; the moment it could restore/delete/prune it
    would need a real undo and would no longer be LOW. This fails the build if any
    forbidden manager verb appears anywhere in the module — e.g. a
    ``manager.delete(...)`` slipped in after the capture.

    Mirrors the AST-over-source idiom of ``test_snapshot_subsystem_never_schedules_
    itself`` in test_snapshots.py: walk every attribute access and pin the names."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    attrs = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    leaked = attrs & _FORBIDDEN_MANAGER_VERBS
    assert not leaked, (
        f"snapshot_now.py reaches a non-capture manager verb {sorted(leaked)} — the "
        f"tool is CAPTURE-ONLY and must only ever ADD a restore point (G3, amendment §3.2)."
    )
    # Positive half: it must actually take a snapshot, or the whole tool is a lie.
    assert "capture" in attrs, "snapshot_now.py never calls capture() — it saves nothing"


def test_the_tool_imports_nothing_from_the_rpc_layer() -> None:
    """The tools/ boundary (CLAUDE.md §2), held in spirit: sticky-warning parity is
    wired by a plain callback, not by importing the server's rpc namespace."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert not name.startswith("agent_core.rpc"), (
                f"snapshot_now.py imports {name} — a tool must not import the rpc layer"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
