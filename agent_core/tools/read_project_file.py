"""read_project_file — read a text file by absolute path, OPEN-only (step 5).

The read half of the coding harness (scope amendment 2026-07-20, §8; contract §2).
LOW and read-only: no ``undo()`` needed. ``open_only`` (registry, R3) so it is
absent from the SAFE view and refused at dispatch outside OPEN — the Simple
companion never edits a project by raw path; that is a Developer affordance.

CONFINEMENT is not this tool's job and cannot be — ``tools/`` may not import the
store (module-boundary rule), and confinement needs the trust rows. The CALLER
(orchestrator / routine engine) resolves ``affected_path`` ONCE, checks it against
the trusted roots + the data-dir floor, and HARD-REFUSES before ``execute`` ever
runs if the path is outside trust (D3). So by the time ``execute`` is reached the
path is known-inside-trust, and the tool acts on ``context.resolved_path`` — the
very path the caller checked — never a re-read of ``args["path"]`` (R6, TOCTOU).

Every filesystem effect crosses the Rust shell (engineering-spec §1.3): the read
goes through ``shell_bridge.read_workspace_file``, which also independently refuses
Addison's own data directory (defence in depth, §6.6).
"""

from __future__ import annotations

from pathlib import Path

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

_NO_SHELL_MESSAGE = "Reading project files needs the desktop shell; not available in this mode."
# Only reachable if a path tool were ever run without the caller's confinement
# step (which sets resolved_path). Fail closed rather than fall back to re-reading
# args["path"] and resolving a second time — that is the TOCTOU gap R6 forbids.
_NO_RESOLVED_PATH = "Addison couldn't work out which file that is."


class ReadProjectFileTool:
    definition = ToolDefinition(
        id="read_project_file",
        label="Read a project file",
        description=(
            "Reads a text file from a folder you've trusted, so Addison can work "
            "with your project. Available only in the Developer profile."
        ),
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The full path of the file to read.",
                }
            },
            "required": ["path"],
        },
    )

    def affected_path(self, args: dict) -> str | None:
        """The absolute path this read would touch, resolved ONCE (realpath, so
        symlinks / ``..`` / a relative path are collapsed here). The caller uses the
        returned value for BOTH the confinement check and, via
        ``ExecutionContext.resolved_path``, the read itself (D4/R6)."""
        raw = args.get("path")
        if not raw or not isinstance(raw, str):
            return None
        return str(Path(raw).resolve())

    def permission_detail(self, args: dict) -> str | None:
        """The file name only — never the full path (the Activity Panel leaves the
        Agent Core for the webview; a full path can carry the user's account name).
        ``call_permission_detail`` caps length again at the one construction point."""
        raw = args.get("path")
        if not raw or not isinstance(raw, str):
            return None
        return Path(raw).name or None

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL_MESSAGE)
        resolved = context.resolved_path
        if not resolved:
            return ToolResult(success=False, content=_NO_RESOLVED_PATH)
        # The shell reads the confined path and refuses binary / the data dir; a
        # refusal arrives as RuntimeError with a plain sentence (handled by the
        # orchestrator as a failed step).
        content = context.shell_bridge.read_workspace_file(resolved)
        return ToolResult(success=True, content=content)
