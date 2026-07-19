"""routine.* handlers — propose a routine from the conversation, confirm-save it,
list, run, and delete (engineering-spec §7, §6)."""

from __future__ import annotations

import threading

from agent_core.policy import PolicyMode
from agent_core.protocol import Method
from agent_core.routines.model import RoutineStep
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR


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
        # in SAFE mode and stamps created_in_mode so SAFE can later hide it.
        try:
            self.routine_builder.save(
                draft, conversation_id=self.conversation.id, mode=self._mode()
            )
        except ValueError as exc:
            self._respond_error(request_id, _SERVER_ERROR, str(exc))
            return
        self._draft_routine = None
        self._respond(request_id, {"ok": True, "routineId": draft.id})

    def _routine_rows(self) -> list[dict]:
        # §4.7/§6.5: the Developer profile additionally sees a READ-ONLY view of the
        # declarative plan. This is safe to expose precisely because the plan has no
        # code field (§6.1) — it is pure data. There is NO editing surface here;
        # structural step editing stays v2 (§10).
        profile = self._active_profile
        expose_plan = profile is not None and profile.expose_routine_plan
        safe_mode = self._mode() is PolicyMode.SAFE
        rows = []
        for entry in self.routine_library.list():
            # Dev-created routines are hidden while the Simple profile is active
            # (policy.py) — never listed, and they return untouched in Developer mode.
            if safe_mode and entry.get("createdInMode") == PolicyMode.OPEN.value:
                continue
            routine = entry["routine"]
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
        # A dev-created routine is REFUSED in SAFE mode — it waits for Developer mode
        # (policy.py). Switching modes is always allowed, so the routine isn't lost.
        mode = self._mode()
        if (
            mode is PolicyMode.SAFE
            and self.routine_library.created_in_mode(routine_id) == PolicyMode.OPEN.value
        ):
            self._respond_error(
                request_id,
                _SERVER_ERROR,
                "That routine uses developer abilities, so it's waiting in "
                "Developer profile.",
            )
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
        answers via permission.respond with this synthetic toolId."""
        waiter_key = f"routine-step:{run_id}:{step.step_id}"
        event = threading.Event()
        with self._perm_lock:
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
