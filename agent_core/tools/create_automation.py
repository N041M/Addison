"""create_automation — Addison WRITES an automation down. Nothing runs (step 8, phase 2).

================================ SAFETY FRAME ================================
**Addison authors; the OS runs; Addison never triggers itself** — GLOBAL FLOOR G2
(docs/SAFETY.md owns it). This tool is the "authors" half and only that half: it
writes one row to the ``automations`` table. It starts no process, writes no file,
touches no ``~/Library/LaunchAgents``, and cannot. Arming — the typed shell surface,
the keyword-nonce card, ``launchctl`` — is phase 3 and does not exist in the tree,
which is why every answer this tool gives ends with the same sentence saying so.
[docs/step-8-automation-plan.md](../../docs/step-8-automation-plan.md) §4 owns the
phase order.

**Dev-only (plan §5.3).** The payload of an automation is a shell command and SAFE
invariant 1 has no place for one, so this registers ``open_only`` in
``main.build_registry``: absent from ``registry.visible_tools(SAFE)`` and refused at
dispatch outside OPEN (``refuse_if_dev_only_outside_open``). It is ``open_only``
rather than ``dev_only`` for ``write_project_file``'s reason (registry.py, R3): it
is MEDIUM with a REAL ``undo()``, so it must stay undo-ENFORCED at registration —
``dev_only`` would exempt it, and a future edit dropping ``undo()`` would register
silently.

THE DOOR — four refusals, and why each is at AUTHORING rather than at arming:

  1. **The schedule** (``automations.schedule_problem``). A row whose schedule the
     vocabulary cannot render is a row whose preview shows a plist with no trigger:
     launchd would load it and never fire it. Refused here, in the one place a
     person can be told what to say instead.
  2. **The command's own denylist and arming refusals** — the SAME check dispatch
     applies to ``run_command`` (``tools.base.call_is_forbidden``), asked through
     the same function with the same sentences. *Addison must not store what it
     would refuse to run.* The delicious case is real and lands correctly: an
     automation whose command is ``crontab -e`` is refused as arming, because a
     scheduled job that arms another scheduled job is the ceremony being walked
     around, not a clever schedule.
  3. **Secret shapes in the stored text** (``redaction.redact``) — the mcp phase-1
     ``_base_url_problem`` precedent, and it transfers whole. ``automations`` is
     snapshot-CAPTURED, so anything written here is copied into every later snapshot
     payload and its plaintext sidecar, where no redaction stands between it and a
     backup. A command that needs a key reads it from a file; the sentence says so.
  4. **The label** (``automations.derive_label``). UNIQUE in the schema, so a
     collision is a database error unless something turns it into "…-2" first.

THE COMMAND TEXT IS DECLARED (``command_text``), which is not a detail. It means the
ordinary dispatch path refuses a forbidden command BEFORE the gate is consulted and
before ``execute`` is reached — no card, and one ``forbidden`` audit row — at all
three dispatch sites, exactly as ``run_command``'s is. The check inside ``execute``
is not a second boundary; it is the same predicate asked again against the LIVE
store's directory, for any caller that reaches this tool without one wired.

LATE-BOUND STORE, and THREAD AFFINITY — ``snapshot_now``'s pattern, for the same
reason. ``build_registry()`` runs in ``main()`` before the worker thread builds the
``Store`` (``main._ensure_built``), so the constructor takes a zero-arg
``store_ref`` resolved at execute time rather than a store. Before the store exists
the ref answers None and the tool says so in plain words. Tools run inside a turn on
the server's worker thread (``main._run_send_message``), which is the thread that
owns the SQLite connection — so the writes below happen on the connection's own
thread and no cross-thread SQLite access is possible.

Nothing raises out of ``execute``: every failure is
``ToolResult(success=False, content=<one plain sentence>)``, house-rule style.
=============================================================================
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_core.automations import (
    MAX_COMMAND_CHARS,
    MAX_INTERVAL_MINUTES,
    MAX_NAME_CHARS,
    SCHEDULE_FIELDS,
    SCHEDULE_KINDS,
    Automation,
    derive_label,
    plist_text,
    schedule_fields,
    schedule_problem,
    schedule_sentence,
)
from agent_core.redaction import redact
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
    UndoRefused,
    call_is_forbidden,
)

if TYPE_CHECKING:
    from agent_core.memory.store import Store


# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------
# Every one of these says what happened first and what to do next. None of them
# names a table, a mode, a plist or a denylist.

_NOT_READY = "Addison can't save an automation just yet. Try again in a moment."
_NEEDS_A_NAME = (
    "Give the automation a name with some letters or numbers in it — Addison uses "
    "the name to label what it writes."
)
_NEEDS_A_COMMAND = "Say what the automation should actually run."
# A name is ONE SHORT LINE, and refusing the rest is a safety property rather than
# tidiness. The tool's answer interpolates the name above the fenced preview, so a
# name carrying a newline can open a fence of its own — three backticks at the
# start of a line — and write attacker-chosen prose under Addison's sentence "this
# is exactly what would be handed to your computer". `_fenced` cannot see that: it
# is computed from the plist alone. Without a newline the sequence cannot begin a
# block, so this is where that vector closes. (The adversarial pass over the fix
# round found it: the first fix hardened the command's channel and left the name's.)
_COMMAND_HAS_ODD_CHARACTERS = (
    "That command has characters in it that your computer's scheduler won't accept. "
    "Write it as plain text on one line, or put it in a script file and run that."
)
_NAME_IS_NOT_ONE_LINE = (
    "Give the automation a name on a single line, without any special characters."
)
# The two LENGTH refusals. They live here, with the rest of this door's copy, while
# the bounds themselves live beside the row in `automations.py` — the numbers are
# facts about what a row may hold; the sentences are this door's way of saying so.
_NAME_TOO_LONG = (
    "That name is too long to sit on a row you can read at a glance. Give it a "
    "short one — a few words."
)
_COMMAND_TOO_LONG = (
    "That command is far longer than Addison keeps for an automation. Put it in a "
    "script file on this computer and have the automation run that file instead."
)
_NAME_IN_USE = (
    "You already have a lot of automations with that name. Give this one a name of "
    "its own so you can tell them apart."
)
# The secret refusal. It says WHY without saying "snapshot payload" or "sidecar":
# the person's actionable fact is that a saved automation gets copied, and the fix
# is the same one every runbook gives — keep the secret in a file and read it.
_LOOKS_LIKE_A_SECRET = (
    "That looks like it has a password or key in it, and Addison keeps copies of "
    "your saved automations. Put the secret in a file on this computer and have the "
    "automation read it from there instead."
)
_COULDNT_SAVE = "Addison couldn't save that automation just now. Try again in a moment."
_UNDO_NOT_READY = "Can't undo that automation just now — try again in a moment."

# THE STANDING TRUTH, on every successful answer. It is one sentence and it is the
# whole of what a draft is: written, not armed. **Phase 3 flipped it** (plan §7
# registered this string as one of that commit's edits): arming exists now, so the
# sentence stops saying "not built" and starts saying what the person does next —
# and it names the ceremony rather than hiding it, because a person who is going to
# be asked to retype a code should hear that here rather than discover it on a card.
NOT_ARMED_LINE = (
    "It isn't running yet. To switch it on, ask Addison to arm it — you'll see "
    "exactly what it will run and type a short code to confirm."
)


class CreateAutomationTool:
    definition = ToolDefinition(
        id="create_automation",
        label="Set up an automation",
        description=(
            "Writes down something for this computer to run on a schedule — what to "
            "run and when — and saves it as a draft. Writing one down does not start "
            "it: switching it on is a separate step the person takes, where they are "
            "shown exactly what will run and type a short code to confirm. Addison "
            "never switches one on by itself. Available only in the Developer profile."
        ),
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A short name for the automation, e.g. 'Nightly backup'.",
                },
                "command": {
                    "type": "string",
                    "description": (
                        "The command the computer would run, e.g. "
                        "'/usr/bin/rsync -a ~/Notes ~/Backups/Notes'."
                    ),
                },
                "schedule_kind": {
                    "type": "string",
                    # Derived from the closed vocabulary, never a second copy of it.
                    "enum": list(SCHEDULE_KINDS),
                    "description": (
                        "'interval' to run every so many minutes, or 'calendar' to "
                        "run at a time of day."
                    ),
                },
                "minutes": {
                    "type": "integer",
                    "description": (
                        "For 'interval': how many minutes between runs. At least 1, "
                        f"and at most {MAX_INTERVAL_MINUTES} (seven days) — for "
                        "anything longer use 'calendar'."
                    ),
                },
                "hour": {
                    "type": "integer",
                    "description": "For 'calendar': the hour, 0 to 23 (24-hour clock).",
                },
                "minute": {
                    "type": "integer",
                    "description": "For 'calendar': the minutes past the hour, 0 to 59.",
                },
                "weekday": {
                    "type": "integer",
                    "description": (
                        "For 'calendar', optional: the day of the week as a number — "
                        "0 is Sunday, 1 is Monday, up to 6 for Saturday. Leave it out "
                        "to run every day."
                    ),
                },
            },
            "required": ["name", "command", "schedule_kind"],
        },
    )

    def __init__(self, store_ref: Callable[[], Store | None]) -> None:
        """``store_ref`` is late-bound (see the module docstring) and REQUIRED, not
        optional — a tool constructed with no way to reach a store would answer
        "can't save just yet" forever, which is worse than a construction error.
        ``main.build_registry`` passes ``lambda: None`` on the paths that genuinely
        have no store (the CLI loop, tests), and that honest nothing is what
        ``_NOT_READY`` describes."""
        self._store_ref = store_ref

    # --- what the dispatch layer asks about a call ---------------------------

    def is_destructive(self, args: dict) -> bool:
        """False, and it is the one honest answer: a draft can run nothing.

        Writing a row that no mechanism in the tree can execute destroys nothing and
        is fully reversible by ``undo()`` and by ``automation.remove``. So in OPEN
        the gate auto-allows it card-free (policy.py) — the ceremony belongs to
        ARMING (plan §5.2), and spending a card here would be the second-strongest
        consent in the app asked for the safest half of the operation."""
        return False

    def affected_path(self, args: dict) -> str | None:
        """None — this tool is not path-bounded (step 5, D4).

        Stated rather than omitted, because the mistake is available: the command
        text may NAME any number of paths, and the plist phase 3 would write has a
        path of its own. Neither is a path this call touches — it writes a database
        row — and returning one would put confinement in charge of a file that does
        not exist yet, while leaving the row it does write unguarded."""
        return None

    def command_text(self, args: dict) -> str | None:
        """The command this automation would hand to a shell, UNTRUNCATED, for the
        hardline denylist (``tools.base.call_is_forbidden``).

        ``run_command`` is the other tool that declares this, and the protocol is
        open by design (its docstring says so). Declaring it here is what makes a
        forbidden command fail at the dispatch site — above the gate, with no card
        — rather than inside ``execute``: *Addison must not store what it would
        refuse to run*, and the refusal should not depend on which surface asked."""
        command = str(args.get("command", "")).strip()
        return command or None

    def permission_detail(self, args: dict) -> str | None:
        """The automation's NAME, redacted, and never the command.

        This value leaves the core for the webview and is shown on every granted
        call in the Activity Panel (``call_permission_detail`` — read it before
        returning anything new). The panel is where a person sees this happen at
        all, since a non-destructive call raises no card, so a bare label would
        leave them unable to tell one automation from another. The command is not
        here for the same reason ``read_web_page`` sends a host and not a URL:
        the name identifies the thing; the command is the part that could carry
        whatever the model was told to put in it.

        REDACTED BECAUSE THE DOOR IS TOO LATE FOR THIS PATH (phase-2 review).
        ``_refusal`` refuses a secret-shaped name — but it runs inside ``execute``,
        and this method's value is computed and emitted BEFORE that: the call is
        non-destructive, so the gate auto-grants and the orchestrator sends the
        detail on the way in. The row never reaches SQLite, and the Activity Panel
        has the secret anyway. ``call_permission_detail``'s own contract says a
        detail "must never contain a secret … it leaves the Agent Core for the
        webview", so the redactor runs here rather than the field being trusted
        because a *different* layer would have caught it later. G1 is about where a
        secret may travel, not about whether it was ultimately stored."""
        name = redact(str(args.get("name", "")).strip()).text
        return name or None

    # --- the door ------------------------------------------------------------

    def _data_dir(self, store: Store) -> str:
        """The live data directory, derived from the RUNNING store's own path.

        The same authority ``rpc/workspace.WorkspaceMixin._data_dir`` uses (*"the
        live DB's parent … derived from the running store's path"*), reached the
        only way a tool can reach it. This module deliberately does NOT re-derive
        the directory from the environment the way policy's store-free fallback
        does: that derivation protects the DEFAULT store while a store opened
        anywhere else goes unguarded, which is the exact bug
        ``test_step_5_5_containment`` pins its owner set for — and the reason the
        set is checked by scanning for the symbol, so this module must not even
        name it."""
        return os.path.dirname(os.path.abspath(store.db_path))

    def _refusal(self, args: dict, store: Store) -> str | None:
        """The whole door, in the order a person would want to be told.

        Order is not arbitrary. Shape first (a name, a command, a schedule Addison
        can express), then the two refusals that are about the CONTENT of what would
        be stored. The label comes after all of these, in ``execute``, because it is
        the only question whose answer depends on what is already saved — asking it
        last means a rejected draft never consumes a numbered spelling of its own
        name."""
        if derive_label(args.get("name")) is None:
            # Asked with NO taken labels, so this is the name-is-unusable case
            # rather than the collision case (``derive_label``'s docstring).
            return _NEEDS_A_NAME
        # One line, no control characters — see ``_NAME_IS_NOT_ONE_LINE``. Checked
        # on the RAW value, before stripping, because a newline in the middle is
        # the case that matters and stripping only touches the ends.
        raw_name = str(args.get("name", ""))
        if any(ch == "\n" or ch == "\r" or (ord(ch) < 32) for ch in raw_name):
            return _NAME_IS_NOT_ONE_LINE
        # THE COMMAND GETS THE SAME CHARACTER SCREEN, and it did not until the
        # phase-3 review: a command carrying a NUL or a vertical tab authored fine,
        # rendered into the preview, and raised the code card — and the SHELL then
        # refused it, because a control character cannot go into a plist. The person
        # performed the whole ceremony for a job that could never install. Refusing
        # here costs nothing (no real command needs one) and spends the ceremony only
        # on automations that can actually run.
        # MIRRORS THE SHELL'S RULE EXACTLY (`automation.rs::command_from`): every
        # control character except tab, newline and carriage return. Matching rather
        # than inventing is the point — a core stricter than the shell refuses
        # commands that would have worked (a multi-line command is ordinary), and a
        # core looser than the shell is the gap this closes. `unicodedata.category`
        # "Cc" is Rust's `char::is_control`.
        command = self.command_text(args) or ""
        if any(
            unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in command
        ):
            return _COMMAND_HAS_ODD_CHARACTERS
        if not self.command_text(args):
            return _NEEDS_A_COMMAND
        # LENGTH, before anything reads the text closely. The stored strings are
        # copied into every later snapshot payload and sidecar (the same reason the
        # secret check below exists), and nothing else in this path bounds them —
        # the mcp/skills precedent bounds its own (`automations.MAX_*_CHARS` says
        # why). Measured on the stripped text, which is what is stored.
        if len(str(args.get("name", "")).strip()) > MAX_NAME_CHARS:
            return _NAME_TOO_LONG
        if len(self.command_text(args) or "") > MAX_COMMAND_CHARS:
            return _COMMAND_TOO_LONG
        problem = schedule_problem(args.get("schedule_kind"), _declared_fields(args))
        if problem is not None:
            return problem
        forbidden = call_is_forbidden(self, args, self._data_dir(store))
        if forbidden is not None:
            return forbidden
        # BOTH stored strings, because both are captured. `kinds` is non-empty iff a
        # credential shape was found; the text redact() returns is thrown away here —
        # a redacted command is not a command, and storing one would leave a job that
        # silently does the wrong thing at 3am.
        if redact(self.command_text(args) or "").kinds or redact(
            str(args.get("name", ""))
        ).kinds:
            return _LOOKS_LIKE_A_SECRET
        return None

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        store = self._store_ref()
        if store is None:
            return ToolResult(success=False, content=_NOT_READY)
        refusal = self._refusal(args, store)
        if refusal is not None:
            return ToolResult(success=False, content=refusal)

        kind = str(args.get("schedule_kind"))
        # The SAME projection the door validated (``_declared_fields``), so the
        # schedule that was checked is the schedule that is written.
        fields = _declared_fields(args)
        try:
            existing = store.list_automations()
        except Exception:
            return ToolResult(success=False, content=_COULDNT_SAVE)
        label = derive_label(args.get("name"), [row.label for row in existing])
        if label is None:
            return ToolResult(success=False, content=_NAME_IN_USE)

        now = int(time.time())
        row = Automation(
            id=str(uuid.uuid4()),
            name=str(args["name"]).strip(),
            label=label,
            command=str(args["command"]).strip(),
            schedule_kind=kind,
            schedule_json=json.dumps(fields, sort_keys=True),
            # Display-only provenance, exactly as everywhere else: what a row is
            # ALLOWED to do is asked of the row, never of this stamp (CLAUDE.md).
            created_in_mode=context.policy_mode.value,
            created_at=now,
            # Never changed is last changed when it was written (store.py).
            updated_at=now,
        )
        try:
            store.insert_automation(
                id=row.id,
                name=row.name,
                label=row.label,
                command=row.command,
                schedule_kind=row.schedule_kind,
                schedule_json=row.schedule_json,
                created_in_mode=row.created_in_mode,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        except Exception:
            # The database is the authority on the label's UNIQUE constraint and on
            # the schedule vocabulary's CHECK; both are pre-empted above, so landing
            # here means something Addison did not anticipate. One plain sentence,
            # no stack trace, nothing half-written (the insert is one statement).
            return ToolResult(success=False, content=_COULDNT_SAVE)

        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",  # filled by the orchestrator
            tool_id=self.definition.id,
            undo_payload={"automation_id": row.id},
            created_at=now,
        )
        return ToolResult(success=True, content=_result_text(row), snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Delete the row this call created — a real undo, and a complete one.

        Complete because there is nothing else to reverse: the tool writes one row,
        no file exists, and nothing was armed. (Phase 3's ``arm_automation`` is the
        tool whose undo has real work to do.) A row already gone is the state undo
        wanted, so this is silent rather than an error — the person may have removed
        it from the Automations surface in between.

        MEDIUM with a real undo is what lets this register undo-ENFORCED
        (``open_only``, not ``dev_only``); dropping this method must fail
        registration, which is the invariant, not a convention."""
        store = self._store_ref()
        if store is None:
            raise UndoRefused(_UNDO_NOT_READY)
        store.delete_automation(snapshot.undo_payload["automation_id"])


def _declared_fields(args: dict) -> dict[str, object]:
    """Only the closed fields of the declared kind, and only the ones present.

    A PROJECTION at the door, mirroring ``automations.schedule_fields`` at the wire:
    nothing else in ``args`` can ride along into ``schedule_json``, so a model that
    sends ``hour`` with an ``interval`` schedule stores an interval and nothing more.
    Asked once and used twice — to validate, then to store — so the two can never
    describe different schedules. ``None`` counts as absent, which is what makes an
    omitted ``weekday`` and an explicit ``"weekday": null`` the same request."""
    kind = args.get("schedule_kind")
    names = SCHEDULE_FIELDS.get(kind, ()) if isinstance(kind, str) else ()
    return {name: args[name] for name in names if args.get(name) is not None}


def _fenced(payload: str) -> str:
    """The preview, fenced so the block cannot be closed from the inside.

    The fence is grown past the longest run of backticks INSIDE the payload —
    CommonMark's own rule, and the same one ``mcp_client._fenced`` follows for a
    tool server's structured answer, for the same reason and with the same honest
    limit (presentation, not a boundary).

    IT IS NOT DECORATION HERE, and the earlier version of this function said so in
    exactly the wrong direction: *"the fence is safe unquoted — ``plist_text``
    XML-escapes both the command and the label, and no escape sequence it emits
    contains a backtick."* True about the ESCAPING and irrelevant, because XML
    escaping touches ``&``, ``<`` and ``>`` and leaves a backtick exactly where it
    was. A command carrying ``\\n```\\n`` therefore closed the block mid-``<string>``
    and everything after it read as Addison's own words rather than as the
    preview's contents — so a model relaying "this is exactly what would be handed
    to your computer" could be made to relay attacker-chosen prose under that
    sentence. Phase 3's whole ceremony rests on the person having read this block;
    a block somebody else can write half of is not that (phase-2 review)."""
    longest = max((len(run) for run in re.findall(r"`+", payload)), default=0)
    fence = "`" * max(3, longest + 1)
    # The closing fence must START A LINE or it does not close anything. Today
    # ``plist_text`` always ends in a newline, so relying on that would work — and
    # would be an undeclared precondition on a function whose docstring says it
    # follows ``mcp_client._fenced``'s rule, which guarantees this itself.
    body = payload if payload.endswith("\n") else f"{payload}\n"
    return f"{fence}\n{body}{fence}"


def _result_text(row: Automation) -> str:
    """What the MODEL is handed, and therefore what it relays.

    Four things, in this order: the plain-words schedule, **the id**, the exact plist
    as a preview, and the standing truth that nothing is armed. The preview is here
    rather than "available in Settings" because the person is being asked to
    understand a thing that will one day run without them watching, and the phrase
    "the preview you approved" only means something if the preview was in front of
    them (plan §3).

    THE ID IS HERE BECAUSE ARMING NEEDS IT, and for two years of this file's life it
    was the one fact this answer left out. ``arm_automation`` takes an id; the only
    place an id is ever minted is the row this call just wrote; and nothing put it in
    front of the model — so "now switch it on", the very next thing a person says,
    could not be answered and was met with a sentence claiming the automation was not
    saved (KNOWN-BUGS P1 #1). It sits on its own line next to the sentence that names
    what was saved, NOT inside the preview: the preview is a verbatim copy of what
    would be handed to the computer, and Addison's own bookkeeping is not part of
    that document."""
    fields = schedule_fields(row.schedule_kind, row.schedule_json)
    return (
        f'Saved "{row.name}" as a draft automation. '
        f"{schedule_sentence(row.schedule_kind, fields)}.\n"
        f"Automation id: {row.id}\n\n"
        "This is exactly what would be handed to your computer:\n\n"
        f"{_fenced(plist_text(row))}\n\n"
        f"{NOT_ARMED_LINE}"
    )
