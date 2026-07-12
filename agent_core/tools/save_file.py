"""Save output as a NEW file — MEDIUM risk (design-doc §7.4.1).

Only ever CREATES a file, never overwrites — which is exactly why its undo path
is trivial: delete the file it created. Overwriting/editing in place is out of
scope for v1 (design-doc §7.4.1).

STATUS: partial. The undo() contract is real (so the registry accepts it at
MEDIUM); the write itself is delegated to the Rust shell and left as a TODO.
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


class SaveFileTool:
    definition = ToolDefinition(
        id="save_file",
        label="Save something as a new file",
        description="Saves text or a document as a brand-new file where you choose. It never replaces an existing file.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Name for the new file."},
                "content": {"type": "string", "description": "What to put inside it."},
            },
            "required": ["filename", "content"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="Saving files needs the desktop shell; not available in this mode.",
            )
        # TODO(step 5): shell_bridge.save_new_file(...) via the OS save dialog.
        # Must refuse to overwrite; capture the final path for the undo snapshot.
        created_path = None  # set from the shell result
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",  # filled by the orchestrator
            tool_id=self.definition.id,
            undo_payload={"created_file": created_path},
            created_at=int(time.time()),
        )
        raise NotImplementedError("Wire to shell_bridge.save_new_file — spec §11 step 5.")
        return ToolResult(success=True, content=created_path, snapshot=snapshot)  # noqa: unreachable

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Delete the file this action created. Trivial because save_file only
        ever creates — it never overwrites (design-doc §7.4.1)."""
        # TODO(step 6): shell_bridge.delete_file(snapshot.undo_payload["created_file"]).
        raise NotImplementedError("Wire to shell_bridge.delete_file — spec §11 step 6.")
