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

The snapshot also records ``wrote_sha256`` (Phase-3 review-surface plan Build §2):
the digest of what Addison put on disk, so the review surface can tell "the file as
Addison left it" from "the file as somebody has edited it since". Without it, a diff
would attribute a person's own later work to Addison and Revert would throw it away
with no warning. See ``snapshots/file_revert.py``, which reads it.

And ``wrote_ident`` beside it (2026-08-08): WHICH FILE the write landed on. Two writes
belong to one revert chain when they share a name or a file, and both have to be facts
recorded HERE — asked afterwards, "which file is this" answers about the disk as it is
now, so ``rm a.txt; ln b.txt a.txt`` made two files' chains one and a revert wrote one
file's prior bytes into the other. ``file_revert`` owns that reasoning; this module owns
recording the fact while it is still true.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

# The identity this tool RECORDS and the question the reverting side later ASKS are one
# function, imported rather than reimplemented: an identity minted by one spelling of
# "which file is this" and checked by another is an identity that drifts, and the drift
# would be silent in the direction that writes bytes. (A pure metadata question, not an
# effect — every effect still crosses the shell, §1.3 — and no module-boundary edge:
# ``snapshots`` is not one of the three packages that may not import each other.)
from agent_core.snapshots.file_revert import (
    ANOTHER_FILE_STANDS_THERE_WRITE,
    IDENTITY_KEY,
    another_file_stands_there,
    revert_key,
)
from agent_core.tools.base import (
    UNRESOLVABLE_PATH,
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
#: The review surface's refusal, IMPORTED rather than re-worded: it is the same fact
#: refused for the same reason, and ``file_revert`` owns the sentence. Raised rather than
#: returned — ``undo()`` has no result type, and ``UndoManager`` puts the exception's text
#: into ``UndoResult.detail``, which is why it has to be a sentence and not a diagnostic.
_UNDO_ANOTHER_FILE = ANOTHER_FILE_STANDS_THERE_WRITE


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
        """The absolute path this write would touch, resolved ONCE (expanduser +
        realpath). The caller checks this exact value for confinement and hands it back through
        ``ExecutionContext.resolved_path`` for ``execute`` (D4/R6)."""
        raw = args.get("path")
        if not raw or not isinstance(raw, str):
            # The SENTINEL, never None: this tool IS path-bounded, so an argument it
            # cannot read must be refused by confinement, not waved past it (None
            # means "not a path tool" and skips the boundary entirely).
            return UNRESOLVABLE_PATH
        return str(Path(raw).expanduser().resolve())

    def permission_detail_for_path(self, resolved_path: str | None) -> str | None:
        """The RESOLVED file's name only (see ``read_project_file`` — no full path to
        the webview, and read its docstring for why the caller hands the path in
        rather than this tool resolving one). Shown on the destructive card and the
        Activity Panel.

        This half is the one that matters more, exactly as it is for the resolve-once
        rule this file already carries: a decoy name on a READ mislabels what Addison
        saw, a decoy name on a WRITE mislabels what Addison destroyed. And here the
        card is the last thing between the person and an overwrite, so the name on it
        being the file that actually changes is the whole value of the card."""
        if not resolved_path or resolved_path == UNRESOLVABLE_PATH:
            return None
        return Path(resolved_path).name or None

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
                # WHAT ADDISON LEFT ON DISK, hashed (review-surface plan Build §2).
                # No migration and no dataclass change — the column is TEXT holding
                # JSON — so a row written before this key simply lacks it, and the
                # surface answers "Addison can't tell whether this changed since"
                # rather than guessing. The shell writes these exact characters as
                # UTF-8, so this digest is the digest of the file's bytes.
                #
                # NEVER read by ``undo()``: undo restores ``prior`` unconditionally,
                # exactly as it did before this key existed. This is evidence for a
                # person deciding, not a precondition for putting a file back.
                "wrote_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                # WHICH FILE those bytes went to, asked while the answer is still the
                # truth about this write. Read only by the reverting side, and only ever
                # to compare — never to decide where to write, which stays ``path``
                # above. A row without it (every row written before today) makes the
                # comparison unavailable rather than wrong, and ``file_revert`` says what
                # that costs.
                IDENTITY_KEY: revert_key(resolved),
            },
            created_at=int(time.time()),
        )
        # The RESOLVED name, for ``permission_detail``'s reason: every name this tool
        # puts in front of a person must be the file that actually changed, and this
        # sentence is the one the model then repeats back in chat.
        return ToolResult(success=True, content=f"Wrote {Path(resolved).name}.", snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Put the file back exactly as it was: restore the prior text if the write
        overwrote an existing file, or delete the file if the write created it. The
        shell only ever touches a path THIS tool wrote this session (its ledger), so
        undo can never write or delete an arbitrary path — and it still works if the
        workspace's trust was revoked between the write and the undo.

        THE LEDGER HOLDS THE NAME, NOT THE FILE, which is the gap the identity closes.
        A name Addison wrote can be made to reach a different file afterwards — ``rm
        a.txt; ln b.txt a.txt`` — and the ledger still says yes, so the prior bytes would
        land in a file this row never touched. The review surface's Revert refuses that
        (``file_revert.another_file_stands_there``); this button reaches the same shell
        method from one click, so it asks the same question. Nothing else changes: a file
        that is simply GONE still reverts, and a row from before the identity existed
        cannot trigger the check at all."""
        if self._undo_bridge is None:
            raise RuntimeError(_UNDO_NO_SHELL)
        payload = snapshot.undo_payload
        path = payload["path"]
        identity = payload.get(IDENTITY_KEY)
        known = frozenset({identity}) if isinstance(identity, str) and identity else frozenset()
        if another_file_stands_there(path, known):
            raise RuntimeError(_UNDO_ANOTHER_FILE)
        if payload.get("existed"):
            self._undo_bridge.restore_workspace_file(path, payload.get("prior") or "")
        else:
            # Created by the write — restoring "no prior" means removing it.
            self._undo_bridge.restore_workspace_file(path, None)
