"""Save output as a NEW file — MEDIUM risk (design-doc §7.4.1).

Only ever CREATES a file, never overwrites — which is exactly why its undo path
is trivial: delete the file it created. Overwriting/editing in place is out of
scope for v1 (design-doc §7.4.1).

The write itself is never a raw syscall from the Agent Core — it crosses the
shell bridge (engineering-spec §1.3). ``execute()`` uses ``context.shell_bridge``
per the orchestration contract; ``undo()`` gets no ExecutionContext, so the bridge
it needs is injected once at construction (``build_registry`` passes it) and used
ONLY by undo(). A MEDIUM tool with no working undo would be a safety-invariant
violation (CLAUDE.md §2), so undo() with no bridge raises rather than no-oping.
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

_NO_SHELL_MESSAGE = "Saving files needs the desktop shell; not available in this mode."


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

    def __init__(self, shell_bridge: ShellBridge | None = None) -> None:
        # Injected once by build_registry, used ONLY by undo() (which gets no
        # ExecutionContext). execute() uses context.shell_bridge, never this.
        self._undo_bridge = shell_bridge

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL_MESSAGE)
        # The shell's save dialog refuses to overwrite; it returns the final path.
        created_path = context.shell_bridge.save_new_file(args["filename"], args["content"])
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",  # filled by the orchestrator
            tool_id=self.definition.id,
            undo_payload={"created_file": created_path},
            created_at=int(time.time()),
        )
        return ToolResult(success=True, content=created_path, snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Delete the file this action created. Trivial because save_file only
        ever creates — it never overwrites (design-doc §7.4.1)."""
        if self._undo_bridge is None:
            raise RuntimeError("Can't undo saving that file — the desktop shell isn't available.")
        self._undo_bridge.delete_file(snapshot.undo_payload["created_file"])
