"""Conversational Routine creation — engineering-spec §6.3.

Non-technical users don't hand-author Routines; Addison drafts one from a
recent conversation on request, generalizing literal values into {{variables}}
where they look like per-run inputs. That generalization is a HEURISTIC and can
be wrong — so the confirmation UI must let the user see and correct exactly what
became a variable (the most likely source of a second-run surprise).

Nothing is saved without explicit user confirmation — never silently.

STATUS: stub.
"""

from __future__ import annotations

from agent_core.routines.model import Routine


class RoutineBuilder:
    def propose_from_recent_actions(self, conversation, n_messages: int = 10) -> Routine:
        """Extract the tool calls (NOT the model's prose) from the last
        n_messages and generalize per-run literals into {{variables}}.
        Returns a DRAFT Routine — not yet saved."""
        # TODO(step 8): walk recent tool_use/tool_result messages; build RoutineSteps.
        raise NotImplementedError("Routine proposal — spec §11 step 8.")

    def present_for_confirmation(self, draft: Routine) -> None:
        """Emit an IPC event rendering a plain-language preview (name,
        description, numbered list of what it will do) — NOT raw JSON. Reuses
        PermissionCard-style UI, not a new modal."""
        raise NotImplementedError("Routine confirmation UI event — spec §11 step 8.")

    def save(self, draft: Routine, conversation_id: str) -> Routine:
        """Persist to the routines table only after explicit user confirmation."""
        raise NotImplementedError("Routine save — spec §11 step 8.")
