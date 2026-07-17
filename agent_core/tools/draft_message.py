"""Draft an email or message — MEDIUM risk (design-doc §7.4.1).

Composes ONLY. Opens the draft in the user's own mail/messaging app for them to
review and send. Addison never presses send — there is no send-capable tool in
v1 by design. Undo = discard the draft.

Like save_file, the draft is opened through the shell bridge (engineering-spec
§1.3). ``execute()`` uses ``context.shell_bridge``; ``undo()`` uses the bridge
injected at construction (build_registry supplies it), never a no-op — a MEDIUM
tool with a fake undo would break CLAUDE.md §2.
"""

from __future__ import annotations

import time
import uuid

from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ShellBridge,
    ToolDefinition,
    ToolResult,
)

_NO_SHELL_MESSAGE = "Drafting needs the desktop shell; not available in this mode."


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

    def __init__(self, shell_bridge: ShellBridge | None = None) -> None:
        # Injected once by build_registry, used ONLY by undo(); execute() reads
        # the bridge off the ExecutionContext instead.
        self._undo_bridge = shell_bridge

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL_MESSAGE)
        draft_ref = context.shell_bridge.open_draft(
            args.get("to", ""),
            args.get("subject", ""),
            args["body"],
        )
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",
            tool_id=self.definition.id,
            undo_payload={"draft_ref": draft_ref},
            created_at=int(time.time()),
        )
        return ToolResult(success=True, content=draft_ref, snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Discard the draft this action opened."""
        if self._undo_bridge is None:
            raise RuntimeError("Can't discard that draft — the desktop shell isn't available.")
        self._undo_bridge.discard_draft(snapshot.undo_payload["draft_ref"])
