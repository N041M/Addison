"""Draft an email or message — MEDIUM risk (design-doc §7.4.1).

Composes ONLY. Opens the draft in the user's own mail/messaging app for them to
review and send. Addison never presses send — there is no send-capable tool in
v1 by design. Undo = discard the draft.

STATUS: partial. undo() contract is real so the registry accepts it at MEDIUM.
"""

from __future__ import annotations

import time
import uuid

from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)


class DraftMessageTool:
    definition = ToolDefinition(
        id="draft_message",
        label="Draft an email or message",
        description="Writes a draft and opens it in your own mail or messaging app. You review and send it yourself — Addison never sends anything.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient (optional)."},
                "subject": {"type": "string", "description": "Subject line (optional)."},
                "body": {"type": "string", "description": "The message text."},
            },
            "required": ["body"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="Drafting needs the desktop shell; not available in this mode.",
            )
        # TODO(step 5): shell_bridge.open_draft(...) via mailto:/app handoff.
        draft_ref = None  # opaque reference to the opened draft, for undo
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",
            tool_id=self.definition.id,
            undo_payload={"draft_ref": draft_ref},
            created_at=int(time.time()),
        )
        raise NotImplementedError("Wire to shell_bridge.open_draft — spec §11 step 5.")
        return ToolResult(success=True, content=draft_ref, snapshot=snapshot)  # noqa: unreachable

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Discard the draft this action opened."""
        # TODO(step 6): shell_bridge.discard_draft(snapshot.undo_payload["draft_ref"]).
        raise NotImplementedError("Wire to shell_bridge.discard_draft — spec §11 step 6.")
