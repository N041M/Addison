"""arm_automation — the one thing in Addison that hands a job to the OS (step 8, phase 3).

================================ SAFETY FRAME ================================
**Addison authors; the OS runs; Addison never triggers itself** — GLOBAL FLOOR G2
(docs/SAFETY.md owns it). Phase 2 built the "authors" half: a row, a preview, and a
sentence saying nothing was armed. This is the other half, and it is the single
most consequential action in the app: after it, a command the person approved once
runs on a clock **when Addison is closed**, and **outside Addison's sandbox**
(plan §5.7 — both facts are on the card, verbatim, in :data:`WARNINGS`).

Four things carry that weight, and none of them is this file alone:

  1. **The card is a keyword gate, not a button.** ``arming_card`` builds the
     preview; the gate raises it; ``main`` mints a six-character code
     (``agent_core/automation_nonce.py``) at the moment the card is raised and the
     person retypes it. A model cannot answer a permission card at all — that
     boundary predates this step — and what the code adds is that *observed
     content cannot pre-script the approval*: "click OK" is an instruction a web
     page can write; a code that did not exist when the page was written is not.
     **The nonce is never in this module.** It is minted, held and compared in
     ``main``; nothing here sees it, so nothing here can put it in a tool_result,
     a snapshot payload or an audit row.
  2. **The core never builds the plist.** ``execute`` sends the shell FOUR TYPED
     FIELDS — label, command, schedule kind, schedule numbers — and the
     highest-trust process assembles the XML, validates the ``com.addison.auto.``
     prefix and writes only its own file (plan §5.8, ``Method.SHELL_ARM_AUTOMATION``).
     A bridge call that accepted a document would be ``run_command`` with extra
     steps.
  3. **The undo is real, so registration ENFORCES it.** Registered ``open_only``
     rather than ``dev_only`` (registry.py R3, ``write_project_file``'s shape):
     identical visibility — absent from ``registry.visible_tools(SAFE)``, refused at
     dispatch outside OPEN — but ``dev_only`` would also waive the
     undo-at-registration check, which is the single most important test in the
     codebase. This tool is HIGH with a genuine ``undo()`` (it disarms), so it must
     stay undo-ENFORCED: a future edit dropping the method has to fail
     registration, not register quietly.
  4. **Not from a routine, not from a widget** (plan §5.10). The ceremony belongs
     where a person is present and reading. Registered ``live_only``, so both
     stored-spec dispatch sites refuse it with a plain sentence before the gate is
     consulted.

THE DOOR — asked BEFORE the gate at the dispatch site (``arming_refusal``), and
asked again inside ``execute`` for any caller that reaches this tool without one
wired. A refused call is not a card anybody may approve:

  1. **A Mac.** Arming is launchd and nothing else (plan §5.4). Elsewhere one plain
     sentence, the same temperament as the seatbelt's non-mac disclosure.
  2. **The row must exist.** The id comes from a model reading a list that may have
     moved on; a card for an automation nobody can name is a card about nothing.
  3. **The row's command must STILL pass the denylist.** Carried by
     ``command_text``, so the check that refuses ``crontab -e`` at authoring
     refuses it again HERE, at every dispatch site, above the gate — a row written
     before the fence grew must not be armable now. (Phase 2's own door is not
     enough: the fence is a list, and lists grow.)
  4. **The schedule must be readable** (``automations.schedule_is_readable``). A row
     whose schedule the vocabulary cannot express arms a job with no trigger:
     launchd would load it and never fire it, and the card would be previewing a
     promise nothing keeps.

LATE-BOUND STORE, and a SEPARATE BRIDGE FOR UNDO — ``create_automation``'s and
``save_file``'s patterns respectively. ``build_registry()`` runs before the worker
thread builds the ``Store``, so the store arrives as a zero-arg ref resolved at use
time; ``undo()`` gets no ``ExecutionContext``, so its bridge is injected at
construction and used ONLY there (``execute`` still uses ``context.shell_bridge``,
per the orchestration contract).

Nothing raises out of ``execute``: every failure is
``ToolResult(success=False, content=<one plain sentence>)``, house-rule style.
=============================================================================
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_core.automations import (
    LABEL_PREFIX,
    Automation,
    schedule_fields,
    schedule_is_readable,
    schedule_sentence,
)
from agent_core.policy import PolicyMode
from agent_core.redaction import redact
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ShellBridge,
    ToolDefinition,
    ToolResult,
    call_is_forbidden,
)

if TYPE_CHECKING:
    from agent_core.memory.store import Store


#: Where launchd reads a user agent from — the ONE directory the shell will ever
#: write to (plan §5.8). Named here so the card can say where the file goes, and
#: pinned by a test to be a member of ``policy.OS_AUTOMATION_DIRS``: the fence that
#: makes this directory un-trustable, un-writable under the seatbelt and un-nameable
#: in a command is the same list, and this module must never drift off it.
#:
#: Shown with the ``~`` UNEXPANDED, deliberately. This string lands on a card, in a
#: screenshot of that card, and in whatever the person pastes when they ask for
#: help; the expanded form embeds their account name and answers no question the
#: tilde does not.
LAUNCH_AGENTS_DIR = "~/Library/LaunchAgents"


def install_path(label: str) -> str:
    """Where the OS will read this job from, as the person is shown it.

    The shell owns the real file and validates the label prefix before it writes
    anything; this is the sentence that tells somebody what they are agreeing to,
    computed the one way both sides spell it."""
    return f"{LAUNCH_AGENTS_DIR}/{label}.plist"


def arming_is_supported() -> bool:
    """Whether this computer has the mechanism at all — macOS, and only macOS.

    One mechanism, on the platform the seatbelt already commits to (plan §5.4): no
    LaunchDaemons ever, and no cron, because a second mechanism is a second set of
    edge cases to hold. Drafts exist everywhere; arming does not, and says so.

    Deliberately a function rather than a module constant, for
    ``policy.kernel_confines_writes``' reason: it reads as the question the caller
    is asking, and a test can drive both answers without pretending to be another
    operating system."""
    return sys.platform == "darwin"


#: THE TWO SENTENCES THAT MUST SURVIVE EVERY REDESIGN (plan §3, §5.7). Frozen,
#: core-owned, rendered verbatim by the webview — which is why they live here and
#: not in a component: a warning a surface can reword is a warning a surface can
#: soften. Both are true and neither is hedged:
#:
#:   * the job is launchd's from the moment it is installed, so Addison being shut
#:     down (or deleted) changes nothing about whether it runs;
#:   * the seatbelt confines ADDISON's commands. Wrapping somebody's own automation
#:     in a profile built from today's trusted folders would freeze a snapshot of
#:     trust that goes stale the moment they trust another one — a stale profile is
#:     a lie with a safety label. "Outside Addison's sandbox" is true forever.
WARNINGS: tuple[str, ...] = (
    "This will run on its own schedule even when Addison is closed.",
    "It runs outside Addison's sandbox.",
)


# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------
# Each says what happened first and what to do next. None names launchd, a plist,
# a mode, a registry or a denylist.

_NOT_READY = "Addison can't turn an automation on just yet. Try again in a moment."
_ONLY_IN_DEVELOPER = "Turning an automation on is only available in the Developer profile."
# The non-mac sentence. It names the fact, not the machinery, and it does not
# pretend a workaround exists — writing the job down still works everywhere.
_ONLY_ON_A_MAC = (
    "Addison can only switch automations on for your computer to run on a Mac. "
    "The automation stays saved, and nothing was switched on."
)
_NO_SUCH_AUTOMATION = "That automation isn't saved any more, so there was nothing to turn on."
# A row whose schedule the vocabulary cannot read. It would install a job with no
# trigger — loaded and never fired — so the honest answer is to write it again.
_NO_USABLE_SCHEDULE = (
    "That automation doesn't say when it should run, so switching it on would do "
    "nothing. Set it up again with a time or an interval."
)
_NO_SHELL = (
    "Addison can only switch an automation on from the desktop app, so it didn't "
    "switch that one on."
)
_COULDNT_ARM = (
    "Your computer wouldn't take that automation just now, so nothing was switched "
    "on. Try again in a moment."
)
_UNDO_NOT_READY = "Can't switch that automation back off just now — try again in a moment."


def _armed_text(row: Automation, sentence: str) -> str:
    """What the MODEL is handed, and therefore what it relays.

    Says the thing that changed, when it will happen, and the two facts the person
    just agreed to — because the model's summary is what they will re-read
    afterwards, and a summary that drops "even when Addison is closed" is a
    summary that quietly makes this sound smaller than it is."""
    return (
        f'"{row.name}" is switched on. Your computer will run it from now on — '
        f"{sentence.lower()}. " + " ".join(WARNINGS)
    )


class ArmAutomationTool:
    definition = ToolDefinition(
        id="arm_automation",
        label="Switch an automation on",
        description=(
            "Switches on an automation that was already written down, so this "
            "computer runs it on the schedule it says — by itself, even when "
            "Addison is closed, and outside Addison's usual sandbox. Addison can "
            "never do this on its own: the person is shown exactly what will run "
            "and has to type a short code to confirm it. Mac only, and available "
            "only in the Developer profile."
        ),
        risk_tier=RiskTier.HIGH,
        parameters_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Which saved automation to switch on — the id from the list "
                        "of automations."
                    ),
                }
            },
            "required": ["id"],
        },
    )

    def __init__(
        self,
        store_ref: Callable[[], Store | None],
        shell_bridge: ShellBridge | None = None,
    ) -> None:
        """``store_ref`` is late-bound (module docstring) and REQUIRED.

        ``shell_bridge`` is for ``undo()`` ONLY — it gets no ``ExecutionContext``,
        so ``save_file``'s injection is the only way it can reach the shell.
        ``execute`` uses ``context.shell_bridge`` and never this field, so a
        constructor with no bridge still arms correctly under the real server."""
        self._store_ref = store_ref
        self._shell_bridge = shell_bridge

    # --- what the dispatch layer asks about a call ---------------------------

    def is_destructive(self, args: dict) -> bool:
        """Always True, and the ``args`` are unused on purpose.

        No property of an automation makes arming it safe enough to skip the card:
        the card is where the preview and the code live, and the code is the whole
        ceremony. ``run_command``'s stance, for a stronger reason — a command runs
        once, under the seatbelt, and lands on the snapshot floor; an armed job
        recurs, outlives the session, and runs unconfined."""
        return True

    def affected_path(self, args: dict) -> str | None:
        """None — this tool is not path-bounded (step 5, D4).

        Stated rather than omitted because the mistake is available: arming DOES
        end in a file being written. It is not written by this process, it is not
        written to a path this call names, and the directory it lands in can never
        be a trusted root (the phase-1 fence). Returning a path here would put
        workspace confinement in charge of a file the shell owns — and, worse,
        could mark this call ``trusted`` at the gate, which is precisely the flag
        that skips a card."""
        return None

    def command_text(self, args: dict) -> str | None:
        """The command the OS would run, UNTRUNCATED, for the hardline denylist
        (``tools.base.call_is_forbidden``).

        THIS IS A DOOR, NOT A DETAIL. It is why a row authored before the fence
        grew cannot be armed today: the check that refuses ``crontab -e`` at
        authoring is asked again here, at the dispatch site, above the gate — so a
        forbidden command costs one ``forbidden`` audit row and no card. The
        alternative (trusting phase 2's door) assumes the denylist never changes,
        which is the one thing a denylist does.

        Read from the ROW, never from ``args``: what would run is what was saved,
        and a caller that could pass its own text here would be passing a command
        to arm."""
        row = self._row(args)
        return row.command.strip() if row is not None and row.command.strip() else None

    def permission_detail(self, args: dict) -> str | None:
        """The automation's NAME, redacted — never the command.

        ``create_automation``'s reasoning, unchanged: this value leaves the core for
        the webview and is shown on every granted call in the Activity Panel, so it
        must never carry a secret. The command is not here because it has a place of
        its own on this card — the ``arming`` preview, where it is shown whole and
        where reading it is the point."""
        row = self._row(args)
        if row is None:
            return None
        return redact(row.name.strip()).text or None

    # --- the door ------------------------------------------------------------

    def _row(self, args: dict) -> Automation | None:
        """The automation this call names, or None. Never raises.

        Asked by four different questions (the denylist, the panel detail, the
        card, ``execute``), each of which must survive a store that is not up yet,
        an id that is not a string, and a row somebody removed in another window."""
        store = self._store_ref()
        if store is None:
            return None
        automation_id = args.get("id")
        if not isinstance(automation_id, str) or not automation_id:
            return None
        try:
            return store.get_automation(automation_id)
        except Exception:
            return None

    def _data_dir(self, store: Store) -> str:
        """The live data directory, derived from the RUNNING store's own path.

        ``create_automation``'s method and its reasoning: never re-derived from the
        environment, because that protects the DEFAULT store while a store opened
        anywhere else goes unguarded (``test_step_5_5_containment``'s recurrence
        guard)."""
        return os.path.dirname(os.path.abspath(store.db_path))

    def arming_refusal(self, args: dict) -> str | None:
        """The door, in one plain sentence, or None — asked BEFORE the gate.

        The dispatch site calls this (``permissions.gate.call_arming_refusal``) so a
        call that cannot succeed is never shown to somebody as a thing they might
        approve. ``execute`` asks the same questions again for a caller with no
        dispatch layer wired; that is the same predicate twice, not two boundaries.

        Order is the order a person wants to be told: the whole capability first
        (this computer cannot do it), then the thing they named, then whether it
        says enough to be worth switching on."""
        store = self._store_ref()
        if store is None:
            return _NOT_READY
        if not arming_is_supported():
            return _ONLY_ON_A_MAC
        row = self._row(args)
        if row is None:
            return _NO_SUCH_AUTOMATION
        if not schedule_is_readable(row.schedule_kind, schedule_fields(
            row.schedule_kind, row.schedule_json
        )):
            return _NO_USABLE_SCHEDULE
        return None

    def arming_card(self, args: dict) -> dict | None:
        """What the person reads above the code field, or None if there is nothing
        to show.

        The whole truth rather than a summary (plan §3), because the code's job is
        to make somebody read this and the preview is the only defence against a
        job they have not understood. Five fields and no sixth: the name, when it
        runs in plain words, **the exact command**, where the file goes, and the two
        frozen warnings.

        THE COMMAND RIDES WHOLE, unredacted and unshortened — the same decision
        ``automation.list`` already made for the same reason. A truncated or
        scrubbed command would defeat the defence at the one moment it exists for.
        ``main`` adds ``nonce`` and ``attemptsLeft``; nothing about the code is
        computed here, so nothing here can leak one."""
        row = self._row(args)
        if row is None:
            return None
        fields = schedule_fields(row.schedule_kind, row.schedule_json)
        return {
            "automationName": row.name,
            "scheduleSentence": schedule_sentence(row.schedule_kind, fields),
            "command": row.command,
            "installPath": install_path(row.label),
            "warnings": list(WARNINGS),
        }

    # --- the act -------------------------------------------------------------

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        # Belt-and-suspenders (run_command's): the SAFE view never surfaces this
        # tool and dispatch refuses it outside OPEN, but a tool that hands a job to
        # the operating system checks the mode it is running under itself.
        if context.policy_mode is not PolicyMode.OPEN:
            return ToolResult(success=False, content=_ONLY_IN_DEVELOPER)
        refusal = self.arming_refusal(args)
        if refusal is not None:
            return ToolResult(success=False, content=refusal)
        store = self._store_ref()
        row = self._row(args)
        if store is None or row is None:  # pragma: no cover - arming_refusal covers both
            return ToolResult(success=False, content=_NOT_READY)
        # The denylist, asked again against the LIVE store's directory. The dispatch
        # sites already asked it through ``command_text``; this is the same predicate
        # for a caller that reaches this tool without one.
        forbidden = call_is_forbidden(self, args, self._data_dir(store))
        if forbidden is not None:
            return ToolResult(success=False, content=forbidden)
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL)

        # FOUR TYPED FIELDS, NEVER A DOCUMENT (plan §5.8). ``schedule_fields`` is the
        # same projection the preview and the wire use, so the numbers the person
        # read on the card are the numbers the shell is handed.
        try:
            result = context.shell_bridge.arm_automation(
                row.label,
                row.command,
                row.schedule_kind,
                schedule_fields(row.schedule_kind, row.schedule_json),
            )
        except RuntimeError as exc:
            # The bridge turns a shell error or a stall into one plain sentence.
            return ToolResult(success=False, content=str(exc))
        if not result.get("ok"):
            error = result.get("error")
            return ToolResult(
                success=False,
                content=str(error) if isinstance(error, str) and error.strip() else _COULDNT_ARM,
            )

        # The undo payload is the LABEL and nothing else: it is what the shell needs
        # to take the job back out, and it stays correct even if the row is removed
        # between the arm and the undo (which is exactly when undo matters most).
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",  # filled by the orchestrator
            tool_id=self.definition.id,
            undo_payload={"label": row.label},
            created_at=int(time.time()),
        )
        sentence = schedule_sentence(
            row.schedule_kind, schedule_fields(row.schedule_kind, row.schedule_json)
        )
        return ToolResult(success=True, content=_armed_text(row, sentence), snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Take the job back out of launchd — a real undo, and a complete one.

        This is the rare non-LOW tool whose undo is honest: the effect was one file
        installed under one label, and removing it puts the computer back exactly
        where it was. That is what lets this register undo-ENFORCED, and dropping
        this method must fail registration rather than register silently.

        It is NOT a second arming surface in reverse: ``undo`` only ever removes.
        (``disarm_automation`` deliberately has no undo for the mirror-image reason
        — ITS undo would be an arm with no card and no code.)

        The shell's disarm is idempotent, so a job already gone is the state undo
        wanted and this is silent rather than an error: the person may have switched
        it off from the Automations surface in between."""
        bridge = self._shell_bridge
        if bridge is None:
            raise RuntimeError(_UNDO_NOT_READY)
        label = snapshot.undo_payload.get("label")
        if not isinstance(label, str) or not label.startswith(LABEL_PREFIX):
            # A payload that does not name one of Addison's own labels is not a
            # payload this may act on. The shell validates the prefix too; refusing
            # here means a malformed snapshot never becomes a request at all.
            raise RuntimeError(_UNDO_NOT_READY)
        bridge.disarm_automation(label)
