"""Read a dropped-in / picker-selected file — LOW risk, read-only (design-doc §7.4.1).

SECURITY (design-doc §9, engineering-spec §1.3): the tool never receives a raw
path it can wander with. It gets a *handle* the OS-native file picker returned,
resolved through the Rust shell via ``context.shell_bridge``. This module must
not open arbitrary filesystem paths directly.

VISION GATING (§4.1.1, item A): reading an image in is always allowed, but
*analyzing* it needs a vision-capable model. When the extracted content is an
image and the active provider reports ``capabilities().vision == False``, the
orchestrator surfaces a plain-language warning and offers to switch to a
vision-capable model rather than feeding the image to a model that can't see it.
This tool just reports the content + its kind (the shell returns
``{"content": ..., "kind": "text"|"image"|...}``); the capability check/warn lives
at the orchestration layer. Automatic switching to a vision model is v2, not v1.
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
        # The shell owns extraction; it hands back {"content": ..., "kind": ...}.
        # The vision-capability check on "kind" == "image" lives in the
        # orchestrator (see module docstring), not here.
        extracted = context.shell_bridge.read_scoped_file(args["file_handle"])
        return ToolResult(success=True, content=extracted)
