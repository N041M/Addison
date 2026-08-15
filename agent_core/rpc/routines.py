"""routine.* handlers — propose a routine from the conversation, confirm-save it,
list, run, delete, and share it in and out of this machine (spec §7, §6).

THE SHARING HALF, in one paragraph, because it is the part that reads a file a
stranger wrote. A routine leaves as the portable format
(``routines/portable.py``) and comes back through it, and the reader there is
strict and refuses rather than repairs. What this module adds on top is
everything the pure reader deliberately does not do: the picker round-trip (so
the core never learns a path), the check that every step names an action THIS
build actually holds, the availability question the library already asks, the
screening of the file's wording, and a preview that saves nothing at all. Only
``routine.importConfirm`` writes, and it writes through ``RoutineBuilder.save``
like every other routine.
"""

from __future__ import annotations

import json
import threading
import time

from agent_core.orchestrator import _screenable_text
from agent_core.policy import PolicyMode
from agent_core.protocol import Method
from agent_core.routines.model import Routine, RoutineStep, routine_uses_dev_abilities
from agent_core.routines.portable import parse_portable, to_portable
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _ROUTINE_DEV_ABILITIES_MESSAGE,
    _SERVER_ERROR,
    _unavailable_marker,
)
from agent_core.screening import mark_untrusted, screen

# --- plain sentences for the sharing paths ---------------------------------
# Personas 54 and 68 (design-doc §5): every one of these is something a person can
# act on, and none of them names a file format, a rule, or a field of a payload.

_NEEDS_SHELL_MESSAGE = (
    "Addison can only open and save files from the desktop app. Try again there."
)
_FILE_UNREADABLE_MESSAGE = (
    "Addison couldn't read that file. Ask the person who sent it to share it again."
)
_NOTHING_TO_ADD_MESSAGE = (
    "There's no shared routine waiting to be added. Choose the file again."
)
_NO_RESTORE_POINT_MESSAGE = (
    "Addison couldn't save a restore point just now, so it didn't add anything. "
    "Try again in a moment."
)

#: Said when the file's wording is shaped like an instruction to Addison. ONE
#: SENTENCE, and deliberately toothless-sounding: it never names which rule fired
#: and never quotes what was found, because both would reproduce the payload on a
#: screen people photograph, and neither helps the person decide anything. What
#: helps them decide is knowing Addison will read it as text.
_SCREENING_NOTE = (
    "Some of the wording in this file is written as if it were an instruction to "
    "Addison. Addison will treat it as text."
)

#: The three sentences a person must be able to read before they say yes. They are
#: MANDATORY and they ride on every preview, flagged or not: the honest thing to
#: say about a file from somebody else is the same whether or not a pattern
#: matcher happened to recognise something in it.
_IMPORT_ASSURANCES = (
    "This routine can't do anything you haven't approved. Addison still asks "
    "before each action, exactly as it does now.",
    "Addison hasn't checked what this routine is for. Only add it if you trust "
    "the person who sent it.",
    "You can delete it at any time, and Addison saves a restore point before "
    "adding it.",
)


class RoutinesMixin(ServerContext):
    def _handle_routine_propose(self, request_id) -> None:
        """§6.3: draft a Routine from the recent conversation and hand the
        frontend a plain-language preview. NOTHING is saved yet — the draft
        waits for routine.confirmSave."""
        try:
            draft = self.routine_builder.propose_from_recent_actions(self.conversation)
        except ValueError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        self._draft_routine = draft
        self._respond(request_id, self.routine_builder.preview(draft, self.tool_registry))

    def _handle_routine_confirm(self, params: dict, request_id) -> None:
        draft = self._draft_routine
        if draft is None:
            self._respond_error(
                request_id, _SERVER_ERROR, "There's no routine waiting to be saved."
            )
            return
        # The user may rename/redescribe in the confirmation card (§6.3).
        if params.get("name"):
            draft.name = str(params["name"])
        if params.get("description"):
            draft.description = str(params["description"])
        # Saved under the current mode; builder.save refuses a command-step routine
        # in SAFE mode and stamps created_in_mode as DISPLAY PROVENANCE — what a
        # profile may use is decided from the plan itself (_routine_needs_dev).
        try:
            self.routine_builder.save(
                draft, conversation_id=self.conversation.id, mode=self._mode()
            )
        except ValueError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        self._draft_routine = None
        self._respond(request_id, {"ok": True, "routineId": draft.id})

    # --- sharing: out ---------------------------------------------------------

    def _handle_routine_export(self, params: dict, request_id) -> None:
        """routine.export: hand the shell a file to save, or say why not.

        ``to_portable`` is the whole of the policy about what may leave: a command
        step and a default pointing at a folder on this machine each come back as a
        sentence naming the field, and this method's only job when that happens is
        to say it. It never scrubs and never exports a repaired version, the author
        would learn on somebody else's machine that their routine had quietly
        changed."""
        routine_id = params.get("routineId")
        if not isinstance(routine_id, str):
            routine_id = ""
        try:
            routine = self.routine_library.get(routine_id)
        except KeyError as exc:
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return

        portable = to_portable(routine)
        if isinstance(portable, str):
            # A refusal from the format itself, already a plain sentence.
            self._respond(request_id, {"ok": False, "error": portable})
            return

        bridge = self._shell_bridge
        if bridge is None:
            self._respond(request_id, {"ok": False, "error": _NEEDS_SHELL_MESSAGE})
            return
        try:
            path = bridge.save_new_file(
                _export_filename(routine.name), json.dumps(portable, indent=2)
            )
        except RuntimeError as exc:
            # Includes the person cancelling the save dialog. The bridge's messages
            # are already plain (shell_bridge.py), so they are passed through.
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return
        self._respond(request_id, {"ok": True, "path": path})

    # --- sharing: in ----------------------------------------------------------

    def _handle_routine_import_preview(self, request_id) -> None:
        """routine.importPreview: read the file the person chooses and DESCRIBE it.

        Nothing here writes. That is the safety property the three-method split
        exists for: everything that reads a stranger's bytes happens in a call that
        cannot leave a row behind, so a file that is refused halfway through, or a
        person who changes their mind at the card, leaves the database exactly as it
        was.

        The order of the checks is deliberate and each one ends the call:

          1. the strict reader (``parse_portable``), a refusal is the WHOLE answer,
             because nothing else about a file it will not read is worth computing;
          2. every step names an action this build holds. The reader cannot ask
             this: ``routines/`` may not import ``tools/`` (CLAUDE.md §2), and the
             set of actions is the registry's to know;
          3. does it need Developer, asked with ``_routine_needs_dev``, the same
             function the library list and dispatch ask, so the preview cannot
             promise something the row will then contradict;
          4. screening, which NEVER refuses. A flagged file is added if the person
             says yes; what changes is that the description is stored marked and the
             preview says so in one sentence.
        """
        bridge = self._shell_bridge
        if bridge is None:
            self._respond(request_id, {"ok": False, "error": _NEEDS_SHELL_MESSAGE})
            return
        try:
            # THE CORE NEVER SEES A PATH. The shell puts up the picker and answers
            # with an opaque handle scoped to the one file the person chose, so
            # nothing read out of that file can be used to reach a second one.
            handle = bridge.pick_file()
            content = bridge.read_scoped_file(handle).get("content", "")
        except RuntimeError as exc:
            # Cancelling the picker lands here too, which is right: there is nothing
            # to preview and the shell's own sentence says so.
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return

        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            self._respond(request_id, {"ok": False, "error": _FILE_UNREADABLE_MESSAGE})
            return

        routine = parse_portable(data)
        if isinstance(routine, str):
            self._respond(request_id, {"ok": False, "error": routine})
            return

        unknown = self._unknown_tool_refusal(routine)
        if unknown is not None:
            self._respond(request_id, {"ok": False, "error": unknown})
            return

        verdict = screen(_routine_screenable_text(routine))

        # HELD, not answered with. The confirm re-reads this and re-parses it, so
        # the only thing that can reach the database is what this call actually
        # read off the disk, never a payload that went out to the webview and came
        # back. See _handle_routine_import_confirm.
        self._draft_import = data

        payload = {
            "ok": True,
            "name": routine.name,
            # AS WRITTEN. The person is being asked to judge this file, so they see
            # its own words; the marker goes on the copy a MODEL will later read
            # (the stored description), which is a different reader with a different
            # problem.
            "description": routine.description,
            "steps": [
                f"{index + 1}. {self._label(step.tool_id)}"
                for index, step in enumerate(routine.steps)
            ],
            "variables": [
                {"name": v.name, "prompt": v.prompt, "default": v.default}
                for v in routine.variables
            ],
            "needsDeveloper": self._routine_needs_dev(routine),
            "assurances": list(_IMPORT_ASSURANCES),
        }
        if verdict.flagged:
            # Kinds are NEVER on the wire (screening.py owns that rule), one plain
            # sentence, or the key is absent entirely.
            payload["screeningNote"] = _SCREENING_NOTE
        self._respond(request_id, payload)

    def _handle_routine_import_confirm(self, request_id) -> None:
        """routine.importConfirm: add the routine the preview described.

        IT TRUSTS NOTHING THE PREVIEW SAID, and takes no parameters at all. What it
        holds between the two calls is the file's own parsed bytes, and it runs the
        entire preview again over them: parse, screen, save. So the row that lands
        is built from what was read off the disk, not from a payload that made a
        round trip through the lowest-trust process in the system, a webview that
        edited a name, a step or a screening verdict on the way back changes
        nothing here, because none of those cross the wire.

        Re-parsing is also what makes two adds of the same file two routines rather
        than one: ``parse_portable`` mints the id, so each call mints a fresh one
        and neither can take the place of a row somebody already has.
        """
        data = self._draft_import
        if data is None:
            self._respond(request_id, {"ok": False, "error": _NOTHING_TO_ADD_MESSAGE})
            return

        routine = parse_portable(data)
        if isinstance(routine, str):
            self._draft_import = None
            self._respond(request_id, {"ok": False, "error": routine})
            return
        unknown = self._unknown_tool_refusal(routine)
        if unknown is not None:
            self._draft_import = None
            self._respond(request_id, {"ok": False, "error": unknown})
            return

        # SCREENED AGAIN, HERE, and the verdict used is this one. The preview's
        # answer is a fact about a moment that has passed, and the caller of
        # ``mark_untrusted`` has to be the one that screened the text it is marking
        # (screening.py says why the verdict parameter exists). Routine-file import
        # is the fifth origin of screened text alongside the web tools, the file
        # tools and MCP results, owner decision 2026-08-15, "import screens the
        # picked file's text"; docs/untrusted-screening-plan.md owns the list.
        verdict = screen(_routine_screenable_text(routine))
        # The stored description is what a MODEL reads when the routine is later
        # run or described, so that is the copy the note goes in front of. It is
        # marked and never dropped: removing the passage would leave the model
        # answering from a hole, and would destroy the evidence that somebody tried.
        routine.description = mark_untrusted(routine.description, verdict)

        # G3: the restore point comes FIRST, and a failure REFUSES the import. This
        # is the routine_delete rule pointed the other way, adding somebody else's
        # routine is exactly the change a person may want undone in one action, and
        # doing it with no way back is the outcome the floor exists to prevent.
        if not self._snapshot_auto("routine_import"):
            self._respond(request_id, {"ok": False, "error": _NO_RESTORE_POINT_MESSAGE})
            return

        try:
            self.routine_builder.save(
                routine,
                # NO conversation id: this routine was not made in a conversation on
                # this machine, and a made-up one would be a lie a surface reads.
                conversation_id=None,
                # THE RECEIVER'S mode, never the sender's, the portable format does
                # not carry ``created_in_mode`` at all (portable.py says why), and
                # this stamp is about where the row was born, which is here.
                mode=self._mode(),
                imported_at=int(time.time()),
            )
        except ValueError as exc:
            # RoutineBuilder.save stays the single writer, refusals included. The
            # format cannot express a command step, so this arm is unreachable
            # today; it is here because "the writer refuses" must be a sentence and
            # never a traceback if that ever changes.
            self._draft_import = None
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return

        self._draft_import = None
        self._respond(request_id, {"ok": True, "routineId": routine.id})

    def _unknown_tool_refusal(self, routine: Routine) -> str | None:
        """One plain sentence when a step names an action this build does not have.

        ``find`` rather than ``visible_tools``: the question here is whether the
        action EXISTS at all, which is what makes a file unreadable. Whether the
        active profile may use it is a different question, asked by
        ``_routine_needs_dev`` below, and answered by listing the row disabled
        rather than by refusing the file (owner decision 2026-08-15: any profile may
        import).
        """
        for step in routine.steps:
            if self.tool_registry.find(step.tool_id) is None:
                return (
                    f"This shared routine uses an action Addison doesn't have, called "
                    f"\"{step.tool_id}\". Ask the person who sent it to share it again "
                    "from the same version of Addison."
                )
        return None

    # --- the ONE availability question (owner decision 2026-08-08) -------------

    def _routine_needs_dev(self, routine: Routine) -> bool:
        """Does this routine need the Developer profile? Asked of the PLAN and of
        what the plan NAMES — never of the row's ``created_in_mode``.

        **ONE function, one owner, three callers**: the list marker
        (``_routine_rows``), the dispatch refusal (``_handle_routine_run``), and the
        widget rail's look-through (``rpc/widgets.py::_widget_needs_dev``, via
        ``_routine_id_needs_dev``). A marker and a refusal computed from two
        expressions are two answers waiting to differ about the same routine, and the
        person meets that disagreement as a row that offers a Run which is then
        refused — or, worse, the other way round.

        Two ways to need Developer, and the second is why this lives in the RPC layer
        rather than beside the plan:

          * **the plan carries an OPEN-only ability** — a command step
            (``routines.model.routine_uses_dev_abilities``, which is the whole of what
            a plan can say about itself);
          * **a step NAMES a tool the SAFE view does not hold.**
            ``read_project_file`` / ``write_project_file`` are the standing example
            (registered ``open_only``), joined by ``create_automation``,
            ``arm_automation``, ``disarm_automation``, ``run_command`` and every
            ``mcp:`` tool. Only the REGISTRY knows that set, and the module-boundary
            rule (CLAUDE.md §2) keeps ``routines/`` from importing ``tools/`` — so the
            combined question can only be answered here, where both are in scope.
            Asking ``routine_uses_dev_abilities`` alone would list a
            ``read_project_file`` routine as usable in Simple and let it reach a
            refusal one click later.

        Absence from ``visible_tools(SAFE)`` is the test, rather than
        ``registry.is_dev_only``, and the difference is a step naming a tool the
        registry does not hold AT ALL: an ``mcp:`` step whose server is not connected
        right now, a plan from a later build, a hand-edited row. ``is_dev_only``
        answers False for every one of them — "not in the open-only set" — and Simple
        would offer the row; the SAFE view answers "not something Simple can run",
        which is true whichever of those it is. Dispatch refuses it either way (the
        engine's ``refuse_if_dev_only_outside_open`` / ``refuse_if_not_callable``);
        this only decides which sentence the person reads first, and the honest one
        is the one that does not promise a run.

        THIS IS NOT THE ENFORCEMENT. It decides what a list SAYS and gives dispatch
        its early, plain refusal; the engine's per-step checks are what actually stop
        a step, in both modes and whatever any surface believed."""
        if routine_uses_dev_abilities(routine):
            return True
        safe_view = {tool.id for tool in self.tool_registry.visible_tools(PolicyMode.SAFE)}
        return any(step.tool_id not in safe_view for step in routine.steps)

    def _routine_id_needs_dev(self, routine_id: str) -> bool:
        """``_routine_needs_dev`` by id, for the one caller that holds an id and not a
        plan — the widget rail's look-through.

        A LOOKUP, never a second answer: it loads the plan and asks the function
        above, so the rail and the library cannot say different things about the same
        routine. That property is what the widget half was protecting when it read the
        routine's stamp (``rpc/widgets.py`` says so in its own words); it now holds
        against the right answer instead of the same wrong one.

        A routine that no longer exists answers False. A launcher pointing at nothing
        is not "waiting in Developer profile" — pressing it should fail on the missing
        routine, which is what ``routine.run`` says."""
        try:
            routine = self.routine_library.get(routine_id)
        except KeyError:
            return False
        return self._routine_needs_dev(routine)

    def _routine_rows(self) -> list[dict]:
        # §4.7/§6.5: the Developer profile additionally sees a READ-ONLY view of the
        # declarative plan. This is safe to expose precisely because the plan has no
        # code field (§6.1) — it is pure data. There is NO editing surface here;
        # structural step editing stays v2 (§10).
        profile = self._active_profile
        expose_plan = profile is not None and profile.expose_routine_plan
        mode = self._mode()
        rows = []
        for entry in self.routine_library.list():
            routine = entry["routine"]
            # A routine that needs developer abilities is LISTED while the Simple
            # profile is active, visibly disabled, instead of vanishing (owner
            # decision 2026-08-06; docs/SAFETY.md owns the rule). It returns
            # untouched in Developer.
            #
            # ASKED OF THE ROUTINE, never of where it was born (owner decision
            # 2026-08-08, closing the routines half of docs/KNOWN-GAPS.md). Under
            # the stamp, a routine of nothing but ``web_search`` steps that
            # happened to be saved while Developer was active arrived here stamped
            # 'open', was listed disabled, and said it "uses developer abilities" —
            # about a routine Simple can run perfectly well. ``createdInMode``
            # below still rides the wire; it is a badge, and nothing reads it to
            # decide anything.
            #
            # DISPLAY ONLY — this marker is not what stops the routine running.
            # _handle_routine_run refuses it below with this very sentence, from
            # this very function, and the engine refuses a dev_only step underneath
            # that. If the flag and dispatch ever disagree, DISPATCH WINS.
            unavailable = _unavailable_marker(
                mode,
                self._routine_needs_dev(routine),
                _ROUTINE_DEV_ABILITIES_MESSAGE,
            )
            row = {
                "id": routine.id,
                "name": routine.name,
                "description": routine.description,
                "runCount": entry["runCount"],
                "lastRunAt": entry["lastRunAt"],
                # Display-only mode provenance: lets the frontend badge dev-created
                # routines ("DEV" tag). Never consulted for permissions.
                "createdInMode": entry.get("createdInMode"),
                # The other display-only provenance field, on identical terms: when
                # this routine arrived from a shared file, or null when it was made
                # here. Nothing reads it to decide anything, here or anywhere else.
                "importedAt": entry.get("importedAt"),
                "variables": [
                    {"name": v.name, "prompt": v.prompt, "default": v.default}
                    for v in routine.variables
                ],
            }
            # Absent entirely when the routine is usable — an available row keeps
            # exactly the shape older frontends already parse.
            if unavailable is not None:
                row["unavailable"] = unavailable
            if expose_plan:
                row["planSteps"] = [
                    {
                        "stepId": step.step_id,
                        "toolId": step.tool_id,
                        "argsTemplate": step.args_template,
                        "dependsOn": step.depends_on,
                        "onFailure": step.on_failure,
                    }
                    for step in routine.steps
                ]
            rows.append(row)
        return rows

    def _handle_routine_run(self, params: dict, request_id) -> None:
        routine_id = params.get("routineId")
        if not isinstance(routine_id, str):
            routine_id = ""  # unknown id — falls into the same KeyError refusal below
        try:
            routine = self.routine_library.get(routine_id)
        except KeyError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        # A routine that NEEDS developer abilities is REFUSED in SAFE mode — it waits
        # for Developer mode (policy.py). Switching modes is always allowed, so the
        # routine isn't lost. The list marker above is display only and asks THE SAME
        # FUNCTION; this refusal is what makes it true, and it holds whatever a stale
        # frontend believes it may click.
        #
        # THIS REFUSAL WAS LOOSENED ON 2026-08-08 (owner decision), and here is the
        # argument, stated where the change is rather than in a document nobody reads
        # at the moment they edit this line. It used to refuse any routine STAMPED
        # 'open', which refused work Simple can do — a search-only routine that
        # happened to be saved while Developer was active. What replaced it refuses
        # any routine that NEEDS developer abilities, which is the question the person
        # was always being told the answer to.
        #
        # It is sound because this is not the enforcement and never was. The engine's
        # per-step ``refuse_if_dev_only_outside_open`` is what actually stops a
        # dev-only step, at dispatch, before the gate and before execute — so a plan
        # that slipped past this line still cannot run one. What a command-free
        # routine replays through is ``visible_tools(SAFE)``, on the same registry and
        # the same gate instance as the live loop, carding per invocation: SAFE
        # invariant 3, which says a Routine never gets permissions beyond what the
        # user granted live. Nothing here widens that.
        #
        # What changed is ONLY which question is asked. Dispatch still refuses a
        # routine that needs developer abilities; it no longer refuses one for where
        # it was born.
        mode = self._mode()
        if mode is PolicyMode.SAFE and self._routine_needs_dev(routine):
            self._respond_error(request_id, _SERVER_ERROR, _ROUTINE_DEV_ABILITIES_MESSAGE)
            return
        result = self.routine_engine.run(routine, params.get("variables") or {}, mode=mode)
        self.routine_library.record_run(routine.id)
        # Remember the routine just run so a widget proposed right after offers it
        # (display-only signal — never affects permissions).
        self._last_run_routine_id = routine.id
        self._respond(
            request_id,
            {
                "ok": result.status == "completed",
                "status": result.status,
                "detail": result.detail,
                # THE ANSWER (owner decision 2026-08-12, closing the QA artifact's
                # §06 open question). The last text the run produced — for a
                # calculator routine, the sum. It was already in `steps` below, as a
                # 200-character summary under a step id nothing displayed, which is a
                # record of the run rather than a reply to the person who started it.
                # Empty string when the run produced no text; the surface shows
                # nothing rather than an empty heading.
                "answer": result.answer,
                "steps": [
                    {
                        "stepId": step_id,
                        "ok": step_result.success,
                        "summary": str(step_result.content)[:200],
                    }
                    for step_id, step_result in result.step_results.items()
                ],
            },
        )

    def _ask_user_continue(self, step: RoutineStep, run_id: str, message: str) -> bool:
        """§6.2 on_failure="ask_user": pause the run and ask, reusing the exact
        permission-card round-trip — the frontend renders label/description and
        answers via permission.respond with this synthetic toolId.

        A STOPPED RUN IS NOT ASKED (KNOWN-BUGS #4). ``conversation.stop`` ends this
        run's consent exactly as it ends a turn's, so a card raised after it would
        be one nobody can answer — the frontend has already let go of the run — and
        one nothing would refuse if they did. "Don't keep going" is the honest
        reading of a stop, so the answer is False without a card. Checked under
        ``_perm_lock`` beside the waiter's registration for the same reason
        ``_ask_once`` does it: stop lands on the read loop while this thread is
        blocked, so the flag and the waiter must move together."""
        waiter_key = f"routine-step:{run_id}:{step.step_id}"
        event = threading.Event()
        with self._perm_lock:
            if self._turn_stopped:
                return False
            self._permission_waiters[waiter_key] = {"event": event, "allow": False}
        self._notify(
            Method.PERMISSION_REQUEST_GRANT,
            {
                "toolId": waiter_key,
                "label": "Keep going with this routine?",
                "description": (
                    f"One step didn't work: {message} "
                    "Addison can keep going with the rest, or stop here."
                ),
                "riskTier": "low",
            },
        )
        event.wait()
        with self._perm_lock:
            waiter = self._permission_waiters.pop(waiter_key, None)
        return bool(waiter and waiter["allow"])


# --- module helpers ---------------------------------------------------------


def _routine_screenable_text(routine: Routine) -> str:
    """Every piece of text in ``routine``, AS IT WAS WRITTEN, joined by REAL newlines.

    The screener's input, and the joining is the whole point rather than a detail.
    Screening ``json.dumps`` of the routine would be screening a different string:
    the escape turns a newline into the two characters backslash-n, which glues the
    next word shut and erases every line start, and a line start is exactly what
    the anchored rules key on (``screening.py``: an authority header, a forged turn
    marker). An instruction at the head of a line would survive that escape
    unreadable to every rule and perfectly readable to the model, which is the one
    combination that must not exist. Same reasoning, and the same technique, as the
    orchestrator's ``_screenable_text`` over a tool result; ``args_template`` is a
    nested structure, so its leaves are read by that function itself.

    Which fields: the name, the description, every variable's question and suggested
    answer, and every leaf of every step's arguments. That is all the free text the
    portable format carries, the rest of it is ids, a version number and a closed
    vocabulary of failure modes.
    """
    parts: list[str] = [routine.name, routine.description]
    for variable in routine.variables:
        parts.append(variable.name)
        parts.append(variable.prompt)
        if variable.default is not None:
            parts.append(variable.default)
    for step in routine.steps:
        parts.append(_screenable_text(step.args_template))
    return "\n".join(parts)


def _export_filename(name: str) -> str:
    """A safe, recognisable filename stem for an exported routine.

    The person names the file in their own save dialog; this is only the suggestion
    that appears in it. Reduced to letters, digits, spaces and hyphens because the
    name is the routine's own free text and a suggestion containing a slash or a
    ``..`` would be a path proposal rather than a filename. Empty after that (a
    name written entirely in punctuation) falls back to a fixed stem rather than an
    empty one.
    """
    kept = "".join(
        character if (character.isalnum() or character in " -_") else " "
        for character in name
    )
    stem = " ".join(kept.split())[:60].strip()
    return f"{stem or 'routine'}.json"
