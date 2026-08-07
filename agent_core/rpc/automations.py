"""automation.* handlers — the automation Addison AUTHORS for the OS to run
(step 8, phase 1). Addison never triggers itself; the OS does.

**PHASE 1 SHIPS THIS DOING NOTHING, AND THAT IS THE POINT.** There is no add, no
update and no arm here: authoring is phase 2 (a ``dev_only`` registered tool that
writes a row) and arming is phase 3 (a typed shell surface behind a per-automation
keyword). Until those land the table stays empty except by hand, so
``automation.list`` answers ``{"automations": []}`` on every install — the same
shape the step-7 phase-1 surface had, for the same reason: the fence and the row
shape are worth landing before anything can create one.
[docs/step-8-automation-plan.md](../../docs/step-8-automation-plan.md) owns the
phase order, and ``agent_core/automations.py`` owns the row and the closed schedule
vocabulary.

**This module can start nothing.** It reads rows, deletes rows, and turns a row into
a payload. It cannot spawn a process, cross the shell bridge, write a plist or reach
``launchctl`` — arming needs a surface in the highest-trust process (plan §2), which
does not exist yet and will never be reachable from here. The structural test in
``tests/test_automations.py`` is what keeps that true rather than merely intended.

**Both methods answer in EVERY profile.** A saved row is configuration, not a
capability: what an automation's shell command needs is Developer, and that belongs
where the capability is — the phase-2/3 tools will register ``dev_only``, absent
from ``registry.visible_tools(SAFE)`` and refused at dispatch outside OPEN (plan
§5.3). Nothing in the tree registers one today, which is why this phase can be
honest that listing is not one of those things. Hiding somebody's saved configuration when
they switch to Simple is the failure the 2026-08-06 artifact decision reversed
([docs/SAFETY.md](../../docs/SAFETY.md) owns that rule), and ``automation.remove`` is
a TIGHTENING — a profile switch must never be the thing that traps configuration
somebody wants gone. Phase 4 gives Simple a listed-but-disabled treatment for these
rows; that is a display decision on top of a payload that was always answered, and it
must never be implemented by reading ``created_in_mode`` (the routines gap in
[docs/KNOWN-GAPS.md](../../docs/KNOWN-GAPS.md) is the cautionary entry).

**Nothing here says whether an automation is ARMED, in either direction.** No column
stores it, no payload carries it, and no handler infers it. Armed truth lives in the
OS and is asked for when the surface loads (plan §5.6): a stored flag is exactly what
a one-action G3 restore would put back, and a restore cannot perform the keyword
ceremony that arming requires. Removing a row is therefore removing a RECORD — if a
plist was ever installed for one, taking it out is the shell's job through the typed
surface phase 3 adds.
"""

from __future__ import annotations

from agent_core.automations import Automation, schedule_fields
from agent_core.rpc.base import ServerContext

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

# The `skill_delete` class of refusal, word-for-word alongside `mcp.remove`'s: the
# row is the only copy of what the person wrote, so losing it with no way back is
# worse than refusing and asking them to try again.
_NO_SNAPSHOT_ON_REMOVE = (
    "Addison couldn't save a restore point just now, so it didn't remove anything. "
    "Try again in a moment."
)
# Said for an id that names nothing — a stale surface, a row already removed in
# another window, or a restore that took it away. It answers ok:false rather than
# mcp.remove's cheerful idempotent ok:true because there is a surface behind this
# one that should reload rather than tick a row off a list that has moved on.
_NO_SUCH_AUTOMATION = "That automation isn't saved any more."


class AutomationsMixin(ServerContext):
    def _automation_wire_row(self, row: Automation) -> dict:
        """One automation as the frontend parses it — camelCase at the boundary
        (``created_at`` -> ``createdAt``), and nothing on it the person did not set.

        ``schedule`` is the PROJECTION of the stored JSON against this kind's closed
        fields (``automations.schedule_fields``), never the column decoded and passed
        along: only that kind's names survive and only as numbers, so a row edited by
        hand or restored from an older payload cannot push a key of its own onto a
        surface. A row whose JSON says nothing this vocabulary recognises arrives as
        ``{}`` rather than making the whole list unanswerable.

        ``command`` rides WHOLE. It is the one field a person must read before arming
        anything, and the keyword ceremony phase 3 adds exists to make them read it —
        a truncated or summarised command would defeat the defence at its one moment.

        ``updated_at`` is deliberately not on the wire: nothing can edit a row yet, so
        it equals ``created_at`` on every row that exists, and a field that is always
        a copy of another one teaches a frontend to render a fact nobody has. Phase 2
        adds it with the edit that makes it differ."""
        return {
            "id": row.id,
            "name": row.name,
            "label": row.label,
            "command": row.command,
            "scheduleKind": row.schedule_kind,
            "schedule": schedule_fields(row.schedule_kind, row.schedule_json),
            "createdInMode": row.created_in_mode,
            "createdAt": row.created_at,
        }

    def _automation_list(self) -> dict:
        """automation.list -> {automations: [{id, name, label, command, scheduleKind,
        schedule, createdInMode, createdAt}]}, oldest first.

        Answers in EVERY profile (see the module docstring) and answers ``[]`` on
        every install today, because nothing in the tree can write a row until phase 2.

        Reading rows only: no plist is looked for, no ``launchctl`` is asked anything,
        nothing is reconciled. Reconciliation against what the OS actually holds is
        phase 4's, on the mcp temperament — no action the person did not just cause —
        and it will arrive as a separate answer rather than a field this payload
        guesses at."""
        self._ensure_built()
        return {
            "automations": [
                self._automation_wire_row(row) for row in self.store.list_automations()
            ]
        }

    def _automation_remove(self, params: dict) -> dict:
        """automation.remove {id} -> {ok} | {ok:false, error}.

        Hook (G3): ``automation_remove``, and a failed capture REFUSES the removal —
        the ``skill_delete`` / ``mcp_disconnect`` class, because the command and
        schedule the person wrote exist nowhere else once the row is gone. Refusing is
        recoverable; an unbackable delete is not. The capture sits BELOW the
        does-it-exist check, so an id that names nothing mints no restore point of
        unchanged configuration, and ABOVE the delete, because a restore point records
        the configuration as it was before the change it is a way back from.

        Allowed in EVERY profile: removing is a tightening, and a profile switch must
        never trap configuration somebody wants gone.

        This removes the ROW. It does not, and structurally cannot, remove an
        installed plist — that is the shell's, through the typed surface phase 3 adds,
        and a phase that wires the two together will do it here in this order: the OS
        first, the record second, so a failure can never leave a job running with
        nothing on screen that names it."""
        self._ensure_built()
        automation_id = params.get("id")
        if not isinstance(automation_id, str) or not automation_id:
            return {"ok": False, "error": _NO_SUCH_AUTOMATION}
        if self.store.get_automation(automation_id) is None:
            return {"ok": False, "error": _NO_SUCH_AUTOMATION}
        if not self._snapshot_auto("automation_remove"):
            return {"ok": False, "error": _NO_SNAPSHOT_ON_REMOVE}
        self.store.delete_automation(automation_id)
        return {"ok": True}
