"""Save a restore point by asking Addison — LOW risk, CAPTURE-ONLY (amendment §3.2).

WHY THIS EXISTS. GLOBAL FLOOR G3 already ships two ways to save a restore point:
Addison takes one automatically before any risky change, and the Settings "Save a
restore point now" control takes one on command. This tool adds the third way the
amendment asks for — "save a restore point by asking Addison" — so a companion who
never opens Settings can still say "make a backup of how things are now" and have
it happen. It calls the SAME manager method the Settings button does
(``capture(trigger="on_command", reason="user_request")``), so the row it writes is
indistinguishable from the one the button writes.

CAPTURE-ONLY IS THE FLOOR, AND IT IS WHY THIS IS HONESTLY LOW.

This tool may only ever ADD a row. It must NEVER call ``restore``,
``restore_last_working``, ``delete``, ``prune``, ``mint_anchor`` or
``mark_verified_working`` on the manager — the ``SnapshotManager`` is reached only
through the one ``capture`` call below, and a source-level test
(``test_snapshot_now.py``) fails the build if any other manager verb appears in
this module.

The capture-only rule is what makes RiskTier.LOW honest even though the tool
writes a row. A LOW tool is read-only and needs no ``undo()`` (CLAUDE.md SAFE
invariants 1 and 2). Adding a restore point does not violate that spirit, for a
reason that has to be stated precisely: a capture can never LOSE anything — it only
ever grows the set of states the user can return to — so there is nothing an
``undo()`` would need to reverse. And the ``undo()`` of a capture would itself be a
DELETION, which is exactly the operation this tool is forbidden to perform. A tool
that could delete the row it just wrote would make the "capture-only" claim false
and would need a real undo, i.e. it would no longer be LOW. So capture-only and LOW
stand or fall together: the tool stays LOW precisely because it can only add.

LATE-BOUND MANAGER, BY NECESSITY. ``build_registry()`` runs in ``main()`` before
the server's worker thread builds the ``Store`` and the ``SnapshotManager``
(``main._ensure_built``), so the manager does not exist when this tool is
constructed. The constructor therefore takes a zero-arg ``manager_ref`` callable
resolved at ``execute`` time, not a manager instance. Before the store is up the
ref returns None and the tool says so in plain words rather than raising.

THREAD AFFINITY. Tools run inside a turn on the server's worker thread
(``main._run_send_message``, dispatched for ``kind == "send"``), which is the same
thread that owns the SQLite connection and built the manager. So the ``capture``
below runs on the manager's own thread — no cross-thread SQLite access.

Nothing raises out of ``execute``: every failure comes back as
``ToolResult(success=False, content=<one plain sentence>)``, house-rule style, no
stack trace ever reaching the person.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from agent_core.snapshots.snapshot_manager import SnapshotManager

# Plain language, no jargon (house rule). The "not yet" case is the pre-store
# window; it is worded as a "try again in a moment" because that is literally what
# fixes it — the store finishes building within the first turn.
_NOT_READY = "I can't save a restore point just yet — try again in a moment."
_FAILED = "Addison couldn't save a restore point just now. Try again in a moment."
_SAVED = (
    "Saved a restore point. You'll find it under Settings, Restore points, and you "
    "can go back to how things are right now any time."
)


class SnapshotNowTool:
    definition = ToolDefinition(
        id="snapshot_now",
        label="Save a restore point",
        description=(
            "Saves a restore point — a copy of your current settings, services, notes, "
            "widgets and routines — so you can go back to how things are right now if a "
            "later change doesn't work out. It only adds a restore point; it never "
            "changes or removes anything."
        ),
        risk_tier=RiskTier.LOW,
        # No arguments: an empty properties object, same shape the sibling tools use.
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(
        self,
        manager_ref: Callable[[], SnapshotManager | None],
        on_captured: Callable[[], None] | None = None,
    ) -> None:
        """``manager_ref`` is late-bound (see the module docstring): it returns the
        live ``SnapshotManager`` once the worker thread has built it, and None
        before that. It is REQUIRED, not optional — a tool with no way to reach the
        manager would silently answer "can't save yet" forever, which is worse than
        a construction error.

        ``on_captured`` is an optional zero-arg callback run after a successful
        capture. It exists for sticky-warning parity with the Settings button: a
        successful save there clears the server's "couldn't save a restore point"
        warning, because the user has just proved for themselves that writes work,
        and a save via this tool has proved exactly the same thing. It is a plain
        callable so this module imports NOTHING from ``agent_core.rpc`` — the
        tools/ boundary rule (CLAUDE.md §2) holds in spirit."""
        self._manager_ref = manager_ref
        self._on_captured = on_captured

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        # ``args``/``context`` are unused: the tool takes no arguments, and the
        # capture is a store write on the worker thread, not an OS effect crossing
        # the ShellBridge.
        manager = self._manager_ref()
        if manager is None:
            return ToolResult(success=False, content=_NOT_READY)
        try:
            # The one and only manager verb this tool may ever call. Exactly the
            # call the Settings control makes (rpc/snapshots._snapshot_create), so
            # the row is identical. CAPTURE-ONLY: never restore/delete/prune/anchor.
            manager.capture(trigger="on_command", reason="user_request")
        except Exception:
            return ToolResult(success=False, content=_FAILED)
        if self._on_captured is not None:
            # Best effort — clearing a display warning must never turn a successful
            # save into a failure.
            try:
                self._on_captured()
            except Exception:
                pass
        return ToolResult(success=True, content=_SAVED)
