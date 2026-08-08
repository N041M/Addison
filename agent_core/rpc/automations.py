"""automation.* handlers — the automation Addison AUTHORS for the OS to run
(step 8). Addison never triggers itself; the OS does.

**THERE IS NO ADD, NO UPDATE AND NO ARM HERE, and that is still the point.**
Authoring is the ``create_automation`` TOOL (phase 2, registered ``open_only``) and
arming is the ``arm_automation`` TOOL (phase 3, behind the ordinary card plus a
typed per-automation code). Both go through the registry, the gate and the audit
like every other tool. This namespace only lists, reports and removes what they
made — no payload here installs, starts or schedules anything.
[docs/step-8-automation-plan.md](../../docs/step-8-automation-plan.md) owns the
phase order, and ``agent_core/automations.py`` owns the row and the closed schedule
vocabulary.

**This module can start nothing.** It reads rows, deletes rows, reports what the OS
holds, and turns a row into a payload. It cannot spawn a process, write a plist or
reach ``launchctl``: those live in the highest-trust process (plan §2), behind a
typed shell surface that builds the document itself.

**It DOES cross the shell bridge, in exactly one direction** — ``list_armed`` and
``disarm_automation``, and only from ``_disarm_before_forgetting`` (phase 3's
review fix). That
is a TIGHTENING and the reason is below: a removal that forgot an armed row would
leave a job running with nothing on screen to name it. It can ask the OS to stop
something and to say what it holds; it has no way to ask it to start anything, and
the structural test in ``tests/test_automations.py`` is what keeps that true rather
than merely intended.

**Both methods answer in EVERY profile.** A saved row is configuration, not a
capability: what an automation's shell command needs is Developer, and that belongs
where the capability is — ``create_automation`` registers ``open_only`` (as the
phase-3 arming tools will), absent from ``registry.visible_tools(SAFE)`` and refused
at dispatch outside OPEN (plan §5.3). Listing a saved row is not one of those
things. Hiding somebody's saved configuration when they switch to
Simple is the failure the 2026-08-06 artifact decision reversed
([docs/SAFETY.md](../../docs/SAFETY.md) owns that rule), and ``automation.remove`` is
a TIGHTENING — a profile switch must never be the thing that traps configuration
somebody wants gone.

**Phase 4 gives Simple the listed-but-disabled treatment, and it asks NOTHING of the
row.** Every automation's payload is a shell command, so every one of them is waiting
for Developer — there is no such thing as one Simple could arm — and the marker is
therefore handed a decided ``True`` rather than a question. That uniformity is the
whole defence: with no per-row question there is no ``created_in_mode`` to be tempted
into reading, which is the mistake ``rpc/routines.py`` still makes (the routines gap
in [docs/KNOWN-GAPS.md](../../docs/KNOWN-GAPS.md) is the cautionary entry, and a
source-level test in ``tests/test_automations.py`` keeps this module out of it). The
marker is DISPLAY ONLY, as everywhere: what refuses arming is ``arm_automation``'s
registration and its dispatch, and if the two ever disagree, dispatch
wins.

**Nothing here says whether an automation is ARMED, in either direction.** No column
stores it, no payload carries it, and no handler infers it. Armed truth lives in the
OS and is asked for when the surface loads (plan §5.6): a stored flag is exactly what
a one-action G3 restore would put back, and a restore cannot perform the keyword
ceremony that arming requires. Removing a row is therefore removing a RECORD — if a
plist was ever installed for one, taking it out is the shell's job through the typed
surface phase 3 adds.
"""

from __future__ import annotations

from agent_core.automations import Automation, schedule_fields, schedule_sentence
from agent_core.policy import PolicyMode
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _AUTOMATION_DEV_ABILITIES_MESSAGE,
    _unavailable_marker,
)

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
# Removal is refused rather than half-done: the row is what makes a running job
# nameable and its Disarm button reachable, so it stays until the job is off.
_COULDNT_DISARM_TO_REMOVE = (
    "Addison couldn't switch that automation off just now, so it left it in place — "
    "removing it while your computer was still running it would leave it running "
    "with nothing here to stop it. Try again in a moment."
)


class AutomationsMixin(ServerContext):
    def _automation_wire_row(self, row: Automation, mode: PolicyMode) -> dict:
        """One automation as the frontend parses it — camelCase at the boundary
        (``created_at`` -> ``createdAt``), and nothing on it the person did not set.

        ``schedule`` is the PROJECTION of the stored JSON against this kind's closed
        fields (``automations.schedule_fields``), never the column decoded and passed
        along: only that kind's names survive and only as numbers, so a row edited by
        hand or restored from an older payload cannot push a key of its own onto a
        surface. A row whose JSON says nothing this vocabulary recognises arrives as
        ``{}`` rather than making the whole list unanswerable.

        ``scheduleSentence`` is that same schedule in ONE plain sentence ("Every 30
        minutes", "Every Monday at 7:30", or "No schedule saved yet." for a row this
        vocabulary does not recognise). Two decisions are folded into that one line:

          * **The words come from the CORE, not from each surface.** A frontend that
            assembled English out of ``schedule`` would be a second renderer of the
            same fact, and the second one is the one that says "Every day at 7:5" or
            guesses am/pm — on a row whose whole job is to tell somebody what will run
            while they are asleep. One wording, said the same way in chat, on the
            Settings row and (phase 3) above the keyword field they type into.
          * **It is rendered from the PROJECTION computed here, once** — the very
            object this row carries as ``schedule`` — never from a second read of the
            column. That is what makes the sentence and the numbers beside it
            incapable of disagreeing: a row that answers ``{}`` cannot also claim an
            hourly schedule, because the sentence was made out of that same ``{}``.

        ``command`` rides WHOLE. It is the one field a person must read before arming
        anything, and the keyword ceremony phase 3 adds exists to make them read it —
        a truncated or summarised command would defeat the defence at its one moment.

        WHAT IS NOT HERE, AND STRUCTURALLY CANNOT BE: the plist preview. A schedule
        rendered as a sentence is a fact ABOUT the row; the plist is a DOCUMENT, and
        the shell builds its own from typed fields and never accepts one from this
        process (plan §5.8). So no payload may normalise carrying one, and a test in
        ``tests/test_automations.py`` pins that this module neither imports the
        preview builder nor names it.

        ``unavailable`` is the phase-4 half, and it is the ONLY field on this row that
        depends on the profile: ``{reason, message}`` while Simple is active, and the
        key ABSENT — never present-and-null — in Developer and Custom, so an available
        row's shape is byte-for-byte what every existing parser already reads.

        ``updated_at`` is deliberately not on the wire: nothing can edit a row yet, so
        it equals ``created_at`` on every row that exists, and a field that is always
        a copy of another one teaches a frontend to render a fact nobody has. Phase 2
        adds it with the edit that makes it differ."""
        schedule = schedule_fields(row.schedule_kind, row.schedule_json)
        wire: dict = {
            "id": row.id,
            "name": row.name,
            "label": row.label,
            "command": row.command,
            "scheduleKind": row.schedule_kind,
            "schedule": schedule,
            "scheduleSentence": schedule_sentence(row.schedule_kind, schedule),
            "createdInMode": row.created_in_mode,
            "createdAt": row.created_at,
        }
        # THE DECIDED BOOLEAN IS A LITERAL ``True``, and that is the point. An
        # automation's payload is a shell command, so every row is waiting for
        # Developer; there is no such thing as one Simple could arm, so there is
        # nothing to ask this row — and a decision that asks a row nothing can never
        # drift into asking it the WRONG thing. ``createdInMode`` above is display
        # provenance for a badge; it is not consulted here, and a source-level test
        # in tests/test_automations.py keeps it that way (rpc/routines.py reads that
        # stamp for availability and is wrong to, docs/KNOWN-GAPS.md).
        #
        # DISPLAY ONLY — this marker is not what stops an automation being armed.
        # ``arm_automation`` is registered ``open_only`` — NOT ``dev_only``, which
        # would take the undo waiver from a HIGH tool that has a real ``undo()``
        # (``main.py`` explains the pair). It is absent from
        # ``registry.visible_tools(SAFE)`` and is refused at dispatch outside OPEN.
        # If the marker and dispatch ever disagree, DISPATCH WINS: the absence of a
        # marker is not a permission (docs/SAFETY.md owns the rule).
        unavailable = _unavailable_marker(mode, True, _AUTOMATION_DEV_ABILITIES_MESSAGE)
        # Absent entirely when the profile can use the row, exactly as the routine
        # and widget lists do it — an available row keeps the shape it always had.
        if unavailable is not None:
            wire["unavailable"] = unavailable
        return wire

    def _automation_list(self) -> dict:
        """automation.list -> {automations: [{id, name, label, command, scheduleKind,
        schedule, scheduleSentence, createdInMode, createdAt, unavailable?}]}, oldest
        first.

        Answers in EVERY profile (see the module docstring), and lists whatever
        ``create_automation`` has written — ``[]`` until somebody asks for one. In
        Simple every row it returns carries ``unavailable``; it returns the same rows
        either way, because a disabled row is the artifact decision and a hidden one
        was the failure it reversed.

        The live mode is read ONCE for the whole answer (the ``widget.list`` /
        ``routine.list`` shape) so every row in one payload describes the same
        profile. ``_mode()`` derives it fresh from the active profile rather than from
        anything cached, which is what makes a ``profile.set`` visible on the very
        next list with no restart.

        Reading rows only: no plist is looked for, no ``launchctl`` is asked anything,
        nothing is reconciled. Reconciliation against what the OS actually holds is
        ``automation.status`` (phase 3), on the mcp temperament — no action the person
        did not just cause — and it is a separate answer rather than a field this
        payload guesses at."""
        self._ensure_built()
        mode = self._mode()
        return {
            "automations": [
                self._automation_wire_row(row, mode) for row in self.store.list_automations()
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

        **THE OS FIRST, THE RECORD SECOND** — the order this docstring specified in
        phase 1 and phase 3 now honours. Until it did, removing an ARMED automation
        deleted the row and left the job running: `disarm_automation` then answered
        *"that automation isn't saved any more, so there was nothing to turn off"*
        while the computer ran it every hour, and the Automations surface — which
        renders armed-ness per ROW — could not show it at all. A job nobody can see
        and nobody can stop, produced by pressing Remove (adversarial review,
        2026-08-07).

        So a row that the OS is holding is disarmed BEFORE it is forgotten, and a
        disarm that fails REFUSES the removal rather than proceeding: leaving the row
        is what keeps the job nameable and the Disarm button reachable. On a machine
        with no arming at all (not macOS, or the bridge is absent) there is nothing
        to hold, so removal proceeds — that is the honest reading of "nothing is
        armed here", not a bypass."""
        self._ensure_built()
        automation_id = params.get("id")
        if not isinstance(automation_id, str) or not automation_id:
            return {"ok": False, "error": _NO_SUCH_AUTOMATION}
        row = self.store.get_automation(automation_id)
        if row is None:
            return {"ok": False, "error": _NO_SUCH_AUTOMATION}
        # THE RESTORE POINT IS MINTED FIRST, and the order is the whole of it. It ran
        # the other way until the phase-4 review: the OS was told to stop the job, the
        # capture then failed, and the frozen sentence told the person "it didn't
        # remove anything" — while their automation had in fact been switched off,
        # with no snapshot, no `tool_audit` row (this is an RPC, not a tool) and no
        # undo. Minting first costs a restore point on a removal that then refuses,
        # which is the cheap direction: an extra way back, versus a message that is
        # false about the one thing the person was watching.
        if not self._snapshot_auto("automation_remove"):
            return {"ok": False, "error": _NO_SNAPSHOT_ON_REMOVE}
        if not self._disarm_before_forgetting(row.label):
            return {"ok": False, "error": _COULDNT_DISARM_TO_REMOVE}
        self.store.delete_automation(automation_id)
        return {"ok": True}

    def _disarm_before_forgetting(self, label: str) -> bool:
        """Switch this automation off if the OS is holding it. True when it is safe
        to forget the row — either nothing was armed, or the disarm succeeded.

        Reads what launchd actually holds rather than trusting anything stored: there
        IS no stored armed state (plan §5.6), which is exactly why this has to ask.
        A shell that cannot answer is treated as "nothing armed here" only when it
        says arming is unsupported; any other failure is a refusal, because
        "I could not find out" and "there is nothing to switch off" are the two
        answers that must never be collapsed."""
        bridge = self._shell_bridge
        if bridge is None:
            return True
        try:
            status = bridge.list_armed()
        except Exception:
            return False
        if not isinstance(status, dict):
            return False
        if not status.get("supported", False):
            return True
        if label not in (status.get("armed") or []):
            return True
        try:
            answer = bridge.disarm_automation(label)
        except Exception:
            return False
        return bool(isinstance(answer, dict) and answer.get("ok"))
