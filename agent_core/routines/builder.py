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

from agent_core.routines.model import Routine, RoutineStep, RoutineVariable, routine_to_json

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
        """Extract the tool calls (NOT the model's prose) from the last
        ``n_messages`` and generalize per-run literals into {{variables}}.
        Returns a DRAFT Routine — not yet saved. Raises ValueError (plain
        message) when there's nothing automatable to extract."""
        steps: list[RoutineStep] = []
        variables: dict[str, RoutineVariable] = {}
        previous_step_id: str | None = None

        recent = conversation.messages[-n_messages:]
        for message in recent:
            if message.role != "assistant":
                continue
            for call in getattr(message, "tool_calls", []) or []:
                step_id = f"step_{len(steps) + 1}"
                args_template = self._generalize_args(dict(call.args), variables)
                steps.append(
                    RoutineStep(
                        step_id=step_id,
                        tool_id=call.tool_id,
                        args_template=args_template,
                        # Sequential chain: each step waits for the one before it,
                        # mirroring the order things actually happened live.
                        depends_on=[previous_step_id] if previous_step_id else [],
                    )
                )
                previous_step_id = step_id

        if not steps:
            raise ValueError(
                "I couldn't find any actions to turn into a routine in our recent chat."
            )

        return Routine(
            id=str(uuid.uuid4()),
            name="My new routine",
            description="Repeats the steps Addison just did for you.",
            variables=list(variables.values()),
            steps=steps,
        )

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

    def save(self, draft: Routine, conversation_id: str | None = None) -> Routine:
        """Persist to the routines table — only ever called after the user's
        explicit confirmation (routine.confirmSave), never silently."""
        if self._store is None:
            raise RuntimeError("Routines can't be saved in this mode.")
        self._store.insert_routine(
            id=draft.id,
            name=draft.name,
            description=draft.description,
            plan_json=routine_to_json(draft),
            created_from_conversation_id=conversation_id,
            created_at=int(time.time()),
        )
        return draft