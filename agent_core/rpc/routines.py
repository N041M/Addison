"""routine.* handlers — propose a routine from the conversation, confirm-save it,
list, run, and delete (engineering-spec §7, §6)."""

from __future__ import annotations

import threading

from agent_core.policy import PolicyMode
from agent_core.protocol import Method
from agent_core.routines.model import Routine, RoutineStep, routine_uses_dev_abilities
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _ROUTINE_DEV_ABILITIES_MESSAGE,
    _SERVER_ERROR,
    _unavailable_marker,
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
