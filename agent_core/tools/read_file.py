"""Read a dropped-in / picker-selected file — LOW risk, read-only (design-doc §7.4.1).

SECURITY (design-doc §9, engineering-spec §1.3): the tool never receives a raw
path it can wander with. It gets a *handle* the OS-native file picker returned,
resolved through the Rust shell via ``context.shell_bridge``. This module must
not open arbitrary filesystem paths directly.

STATUS: stub. Wire ``shell_bridge.read_scoped_file(handle)`` once the Tauri
filesystem bridge (shell/src-tauri/src/filesystem.rs) exists — engineering-spec §11 step 7.
"""

from __future__ import annotations

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)


class ReadFileTool:
    definition = ToolDefinition(
        id="read_file",
        label="Read files you choose",
        description=(
            "Opens files you explicitly select or drag in (PDF, Word, image, csv, text). "
            "It cannot browse your folders on its own."
        ),
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {
                "file_handle": {
                    "type": "string",
                    "description": "Opaque handle from the OS file picker; NOT a raw path.",
                }
            },
            "required": ["file_handle"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(
                success=False,
                content="File reading needs the desktop shell; not available in this mode.",
            )
        # TODO(step 7): text/pdf/docx/image extraction via the scoped handle.
        raise NotImplementedError("Wire to shell_bridge.read_scoped_file — spec §11 step 7.")
