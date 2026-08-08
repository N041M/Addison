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
``disarm_automation``, from TWO callers and no others: ``_disarm_before_forgetting``
(phase 3's review fix) and ``_automation_disarm_orphan`` (2026-08-08). Both are
TIGHTENINGS, and each one's reason is at its own definition: a removal that forgot an
armed row would leave a job running with nothing on screen to name it, and a job the
OS holds with no row at all is that same shape arrived at through a G3 restore. It
can ask the OS to stop something and to say what it holds; it has no way to ask it to
start anything, and the structural test in ``tests/test_automations.py`` is what keeps
that true rather than merely intended — it reads the bridge's own method set and pins
that this module names exactly those two of them, so a ``bridge.arm_automation(...)``
added here fails by NAME rather than by anyone noticing.

**Every method here answers in EVERY profile.** A saved row is configuration, not a
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

from agent_core.automations import (
    Automation,
    label_is_addisons_own,
    schedule_fields,
    schedule_sentence,
)
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

# --- the orphan path's own copy (2026-08-08) ---------------------------------
# Said for a label Addison did not mint. The person cannot reach this from the
# Automations section — it only offers the labels the OS reported under Addison's own
# prefix — so this answers a stale surface, a hand-written request, or a future build
# that widened what it sends. Plain, and honest about the limit rather than about
# permission: Addison is not refusing to help, it genuinely only knows its own files.
_NOT_ADDISONS_OWN = (
    "Addison can only switch off the automations it set up itself, so it didn't "
    "switch that one off."
)
# Said when the label DOES have a saved automation again — a restore put the row back
# between the surface reading it and somebody pressing the button. Not a failure: the
# row is on screen with its own controls, and this path is only for jobs no row can
# reach.
_SAVED_AGAIN = (
    "That automation is saved again, so switch it off from its own row in the list."
)
_NO_SHELL_TO_DISARM = (
    "Addison can only switch an automation off from the desktop app, so it didn't "
    "switch that one off."
)
_COULDNT_DISARM_ORPHAN = (
    "Your computer wouldn't switch that off just now. Try again in a moment."
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

    def _automation_disarm_orphan(self, params: dict) -> dict:
        """automation.disarmOrphan {label} -> {ok} | {ok:false, error}.

        **Switch off a job the OS is holding that NO ROW can reach.** ``apply_config_state``
        is REPLACE-ALL, so restoring a snapshot that predates an automation deletes its
        row while ``<label>.plist`` stays installed and launchd goes on running it at
        every login. Every other way out then refuses: ``disarm_automation`` (the tool)
        and ``automation.remove`` both start by looking the row up, and the Settings
        section renders armed-ness per row, so it could not even name the thing. A job
        nobody can see and nobody can stop — the shape phase 3's review fixed for the
        Remove path, arrived at through Restore instead
        ([KNOWN-GAPS.md](../../docs/KNOWN-GAPS.md), closed 2026-08-08).

        **RECONCILE-ON-RESTORE, and specifically not the two alternatives.** A restore is
        never blocked and nothing is silently disarmed during one: arming decisions must
        not live inside the one action G3 promises is always available. What reconciles is
        the SURFACE — the section already asks the OS what it holds when it loads, and an
        armed label matching no row is rendered as its own row with this method behind
        its button. Addison changes nothing until the person presses it.

        **It can only ever stop something, and every line below is that sentence.**

          * **The label is validated against the set Addison MINTS**, before the store is
            read and before the shell is asked (``automations.label_is_addisons_own`` —
            the same rule ``automation.rs`` enforces on its own side, plan §5.8).
            Somebody else's launchd job is not Addison's to touch, and the check that
            says so costs nothing and reaches nothing.
          * **A label that HAS a row is refused**, which is the narrowness rather than
            an obstacle. A row-backed automation has its own controls; letting this
            answer for one too would put a second, cardless disarm path beside the tool
            that deliberately raises a card. The only way to see this refusal is a
            restore landing between the surface's read and the press, and then the row
            is on screen with its Disarm.
          * **No card and no typed code.** The nonce gates ARMING (plan §5.2); a
            ceremony in front of switching something OFF is a guard failing in the
            direction where every failure is unsafe — ``disarm_automation``'s module
            docstring owns that reasoning.
          * **Every profile**, and no ``_mode()`` call anywhere: a tightening must never
            be the thing a profile switch traps, and Simple keeping Remove is the
            precedent (plan §4.4).
          * **No snapshot**, because there is nothing captured to put back. ``remove``
            mints one for the ROW it deletes; here there is no row, and what changes is
            a file in the OS's own folder that no snapshot has ever held.

        Not idempotent-by-silence: the shell's ``disarmAutomation`` answers ``ok`` for a
        label it is not holding (its own contract), so a job somebody removed by hand
        between the read and the press reports success, which is true — it is off."""
        label = params.get("label")
        if not label_is_addisons_own(label):
            return {"ok": False, "error": _NOT_ADDISONS_OWN}
        self._ensure_built()
        if any(row.label == label for row in self.store.list_automations()):
            return {"ok": False, "error": _SAVED_AGAIN}
        bridge = self._shell_bridge
        if bridge is None:
            return {"ok": False, "error": _NO_SHELL_TO_DISARM}
        try:
            answer = bridge.disarm_automation(label)
        except Exception:
            # Including the shell's own plain-sentence RuntimeError: this path has a
            # person watching a button, and "it didn't work, try again" is the whole of
            # what they can act on either way.
            return {"ok": False, "error": _COULDNT_DISARM_ORPHAN}
        if isinstance(answer, dict) and answer.get("ok"):
            return {"ok": True}
        # The shell's own sentence when it sent one — it knows which of its refusals
        # happened (not a Mac, no home folder, the scheduler would not answer) and this
        # side would only be guessing.
        error = answer.get("error") if isinstance(answer, dict) else None
        said = str(error) if isinstance(error, str) and error.strip() else _COULDNT_DISARM_ORPHAN
        return {"ok": False, "error": said}

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
