"""Conversational Routine creation — engineering-spec §6.3.

Non-technical users don't hand-author Routines; Addison drafts one from a
recent conversation on request, generalizing literal values into {{variables}}
where they look like per-run inputs. That generalization is a HEURISTIC and can
be wrong — so the confirmation preview must show the user exactly what became a
variable (the most likely source of a second-run surprise).

Nothing is saved without explicit user confirmation — never silently.

Module boundary (CLAUDE.md §2): routines/ must not import from providers/, so
the conversation is duck-typed — we only touch ``conversation.messages`` and
each message's ``role`` / ``tool_calls`` (id, tool_id, args).
"""

from __future__ import annotations

import time
import uuid

from agent_core.policy import PolicyMode
from agent_core.routines.model import (
    Routine,
    RoutineStep,
    RoutineVariable,
    routine_to_json,
    routine_uses_dev_abilities,
)

# Heuristic generalization table (§6.3): argument names that are per-run inputs
# by nature. A file handle is SESSION-SCOPED (the picker granted it once), so it
# can never be replayed literally — it must become a variable with no default.
_GENERALIZE = {
    "filename": ("output_filename", "What should I name the file?", "keep_default"),
    "file_handle": ("chosen_file", "Which file should I use? (You'll pick it.)", "no_default"),
}


class RoutineBuilder:
    def __init__(self, store=None) -> None:
        self._store = store

    def propose_from_recent_actions(self, conversation, n_messages: int = 10) -> Routine:
        """Extract the tool calls (NOT the model's prose) of the LAST TURN and
        generalize per-run literals into {{variables}}. Returns a DRAFT Routine —
        not yet saved. Raises ValueError (plain message) when there's nothing
        automatable to extract.

        The window is ONE TURN, never "the last ``n_messages``". The person taps
        "Save as routine" under the work panel, and that panel shows exactly one
        turn's steps — it is cleared at every send (``useTurn.ts``) and rebuilt
        from the last turn on reload. A message-count window let a chat that had
        already done unrelated work (say, arming an automation) put those stale
        steps ahead of the calculation the person was actually looking at, so the
        proposal offered a plan nobody asked for.

        A turn begins at the last ``user`` message: everything after it is what
        this answer did, ALL of it — a long turn is not truncated, because half a
        plan is worse than a wrong one. ``n_messages`` survives only as the
        fallback window for a conversation with no user message at all (a
        reloaded transcript whose rows were filtered, a synthetic one), which is
        the widest honest guess available there.
        """
        steps: list[RoutineStep] = []
        variables: dict[str, RoutineVariable] = {}
        previous_step_id: str | None = None

        for message in self._last_turn(conversation.messages, n_messages):
            if message.role != "assistant":
                continue
            # A REOPENED conversation's calls live on a second attribute
            # (KNOWN-BUGS #5). ``conversation.load`` rebuilds them from the stored
            # transcript onto ``past_tool_calls``, deliberately NOT onto
            # ``tool_calls``, because a provider replays that one and an unpaired
            # tool_use would break every later request of the session. Both are the
            # same thing to a routine — steps the person watched happen — so this
            # reads whichever the message carries. Still duck-typed: routines/ may
            # not import providers/ (CLAUDE.md §2).
            live = getattr(message, "tool_calls", None) or []
            for call in live or (getattr(message, "past_tool_calls", None) or []):
                step_id = f"step_{len(steps) + 1}"
                depends_on = [previous_step_id] if previous_step_id else []
                if call.tool_id == "run_command":
                    # A run_command live action becomes a COMMAND step (OPEN mode
                    # only) — the single dev ability a proposed routine can carry.
                    steps.append(
                        RoutineStep(
                            step_id=step_id,
                            tool_id="run_command",
                            args_template={},
                            depends_on=depends_on,
                            command=str(dict(call.args).get("command", "")),
                        )
                    )
                else:
                    args_template = self._generalize_args(dict(call.args), variables)
                    steps.append(
                        RoutineStep(
                            step_id=step_id,
                            tool_id=call.tool_id,
                            args_template=args_template,
                            # Sequential chain: each step waits for the one before it,
                            # mirroring the order things actually happened live.
                            depends_on=depends_on,
                        )
                    )
                previous_step_id = step_id

        if not steps:
            raise ValueError(
                "I couldn't find any actions to turn into a routine in what I just did."
            )

        return Routine(
            id=str(uuid.uuid4()),
            name="My new routine",
            description="Repeats the steps Addison just did for you.",
            variables=list(variables.values()),
            steps=steps,
        )

    @staticmethod
    def _last_turn(messages: list, n_messages: int) -> list:
        """The messages of the last turn: everything from the last ``user``
        message onward. Duck-typed like the rest of this module — ``role`` only.

        The ``system`` role never appears here in practice (the transient prompt
        is inserted at index 0 and removed when the turn ends, and the store
        cannot hold one), and it carries no tool calls either way, so it needs no
        special case."""
        for index in range(len(messages) - 1, -1, -1):
            if getattr(messages[index], "role", None) == "user":
                return list(messages[index:])
        return list(messages[-n_messages:])

    def _generalize_args(self, args: dict, variables: dict) -> dict:
        template: dict = {}
        for key, value in args.items():
            rule = _GENERALIZE.get(key)
            if rule is None or not isinstance(value, str):
                template[key] = value
                continue
            var_name, prompt, default_mode = rule
            if var_name not in variables:
                variables[var_name] = RoutineVariable(
                    name=var_name,
                    prompt=prompt,
                    default=value if default_mode == "keep_default" else None,
                )
            template[key] = f"{{{{{var_name}}}}}"
        return template

    def preview(self, draft: Routine, tool_registry=None) -> dict:
        """The plain-language confirmation payload (§6.3) the frontend renders
        with PermissionCard-style UI — name, description, a numbered list of
        what it will do, and exactly what became a variable. Not raw JSON."""

        def label(tool_id: str) -> str:
            if tool_registry is not None:
                try:
                    return tool_registry.get(tool_id).definition.label
                except KeyError:
                    pass
            return tool_id

        return {
            "routineId": draft.id,
            "name": draft.name,
            "description": draft.description,
            "steps": [f"{i + 1}. {label(s.tool_id)}" for i, s in enumerate(draft.steps)],
            "variables": [
                {"name": v.name, "prompt": v.prompt, "default": v.default}
                for v in draft.variables
            ],
        }

    def save(
        self,
        draft: Routine,
        conversation_id: str | None = None,
        mode: PolicyMode = PolicyMode.SAFE,
    ) -> Routine:
        """Persist to the routines table — only ever called after the user's
        explicit confirmation (routine.confirmSave), never silently.

        A routine carrying an OPEN-mode-only ability (a command step) may be saved
        ONLY in OPEN mode; attempting it in SAFE mode raises ValueError with a plain
        sentence. ``created_in_mode`` records the mode as DISPLAY PROVENANCE (the
        frontend's DEV badge) and decides nothing: what a profile may list and run is
        asked of the routine itself, one layer up, where the registry is also in
        scope (``rpc/routines.py::_routine_needs_dev``; owner decision 2026-08-08)."""
        if self._store is None:
            raise RuntimeError("Routines can't be saved in this mode.")
        if mode is not PolicyMode.OPEN and routine_uses_dev_abilities(draft):
            raise ValueError(
                "That routine uses developer abilities, so it can only be saved in "
                "the Developer profile."
            )
        self._store.insert_routine(
            id=draft.id,
            name=draft.name,
            description=draft.description,
            plan_json=routine_to_json(draft),
            created_from_conversation_id=conversation_id,
            created_at=int(time.time()),
            created_in_mode=mode.value,
        )
        return draft