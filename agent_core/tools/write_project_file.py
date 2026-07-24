"""write_project_file — create or overwrite a text file, OPEN-only (step 5).

The write half of the coding harness (scope amendment 2026-07-20, §8; contract §2).
MEDIUM, with a REAL ``undo()`` (restore the prior bytes, or delete a file it
created) — so it registers ``open_only=True, allow_missing_undo=False`` (R3): hidden
from SAFE, yet the undo-at-registration check still enforces its undo, exactly the
case the flag split exists for. A future edit dropping ``undo()`` fails registration.

``is_destructive`` returns True unconditionally (R2): an overwrite is data loss, so
the mechanical LOW/MEDIUM rule must NOT auto-grant it. Inside a trusted workspace
the caller passes ``trusted=True`` and the gate auto-grants it card-free (that IS
the harness payoff — undoable, card-free editing inside a trusted project, §8.3);
if confinement were ever bypassed and ``trusted`` were False, this belt makes the
write CARD rather than silently auto-grant.

CONFINEMENT is the caller's job (D3) — see ``read_project_file``. ``execute`` acts on
``context.resolved_path`` (resolved ONCE by ``affected_path``, checked by the caller),
never a re-read of ``args["path"]`` (R6, TOCTOU).

Undo soundness (R5): the write is TEXT-ONLY. The shell captures the prior state
ATOMICALLY (``write_workspace_file`` returns ``{"existed", "prior"}``) and refuses,
writing nothing, if the existing file is binary (can't round-trip as text) or its
prior content exceeds the shell's size bound (would bloat ``action_snapshots``).
Every effect crosses the Rust shell (§1.3); ``undo()`` gets no ExecutionContext, so
its bridge is injected at construction and used only by ``undo()`` — mirroring
``save_file``.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ShellBridge,
    ToolDefinition,
    ToolResult,
)

_NO_SHELL_MESSAGE = "Editing project files needs the desktop shell; not available in this mode."
_NO_RESOLVED_PATH = "Addison couldn't work out which file that is."
_UNDO_NO_SHELL = "Can't undo that file change — the desktop shell isn't available."


class WriteProjectFileTool:
    definition = ToolDefinition(
        id="write_project_file",
        label="Edit a project file",
        description=(
            "Creates or updates a text file in a folder you've trusted. Each change "
            "can be undone. Available only in the Developer profile."
        ),
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The full path of the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The new text contents of the file.",
                },
            },
            "required": ["path", "content"],
        },
    )

    def __init__(self, shell_bridge: ShellBridge | None = None) -> None:
        # Injected once by build_registry, used ONLY by undo() (which gets no
        # ExecutionContext). execute() uses context.shell_bridge, never this.
        self._undo_bridge = shell_bridge

    def is_destructive(self, args: dict) -> bool:
        """Always True (R2). An overwrite destroys the prior contents, so it must
        never ride the mechanical "MEDIUM is non-destructive" auto-grant. Inside
        trust the caller's ``trusted=True`` is what suppresses the card; this keeps
        the card as the belt everywhere else."""
        return True

    def affected_path(self, args: dict) -> str | None:
        """The absolute path this write would touch, resolved ONCE (realpath). The
        caller checks this exact value for confinement and hands it back through
        ``ExecutionContext.resolved_path`` for ``execute`` (D4/R6)."""
        raw = args.get("path")
        if not raw or not isinstance(raw, str):
            return None
        return str(Path(raw).resolve())

    def permission_detail(self, args: dict) -> str | None:
        """The file name only (see ``read_project_file`` — no full path to the
        webview). Shown on the destructive card and the Activity Panel."""
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
        content = args.get("content")
        if not isinstance(content, str):
            return ToolResult(success=False, content="There's nothing to write.")
        # The shell captures the prior state and writes atomically, refusing (writing
        # nothing) a binary/oversize existing file so undo always round-trips. A
        # refusal arrives as RuntimeError (a failed step).
        prior = context.shell_bridge.write_workspace_file(resolved, content)
        snapshot = ActionSnapshot(
            id=str(uuid.uuid4()),
            tool_call_id="",  # filled by the orchestrator
            tool_id=self.definition.id,
            # existed=False -> undo deletes the created file; existed=True -> undo
            # restores these exact prior bytes. Recorded against the RESOLVED path,
            # so undo can never target a different path than the one written.
            undo_payload={
                "path": resolved,
                "existed": bool(prior.get("existed")),
                "prior": prior.get("prior"),
            },
            created_at=int(time.time()),
        )
        return ToolResult(success=True, content=f"Wrote {Path(resolved).name}.", snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Put the file back exactly as it was: restore the prior text if the write
        overwrote an existing file, or delete the file if the write created it. The
        shell only ever touches a path THIS tool wrote this session (its ledger), so
        undo can never write or delete an arbitrary path — and it still works if the
        workspace's trust was revoked between the write and the undo."""
        if self._undo_bridge is None:
            raise RuntimeError(_UNDO_NO_SHELL)
        payload = snapshot.undo_payload
        path = payload["path"]
        if payload.get("existed"):
            self._undo_bridge.restore_workspace_file(path, payload.get("prior") or "")
        else:
            # Created by the write — restoring "no prior" means removing it.
            self._undo_bridge.restore_workspace_file(path, None)
