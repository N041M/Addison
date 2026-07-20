"""run_command — real shell execution, DEV-ONLY (owner decision 2026-07-19).

============================ MODE-SCOPED SAFETY =============================
This tool executes a real shell command. It exists ONLY in OPEN mode (the
Developer profile). It is registered ``dev_only`` (tools/registry.py), so:
  * it is NEVER present in the SAFE view of the registry — SAFE/Simple mode
    cannot see it, cannot send it to the model, and cannot run it;
  * it is exempt from the undo-at-registration check (it has no undo — a shell
    command is not generally reversible), which is only permissible BECAUSE it
    is dev_only and never reachable from SAFE mode;
  * as belt-and-suspenders, ``execute`` itself REFUSES to run under SAFE mode.

EVERY COMMAND CARDS (owner decision 2026-07-20). ``is_destructive`` returns True
unconditionally, so the PermissionGate raises the per-invocation destructive card
for every run_command call in OPEN mode — the card shows the exact command text,
and running it requires the user's approval each time.

There used to be a classifier here that auto-allowed "read-only" commands
(``ls``, ``grep``, a bare ``git status`` …) without a card. It was defeated three
separate ways during hardening: a metacharacter list beaten by a bare newline
(``shlex`` treats ``\n`` as whitespace, so ``ls\nrm -rf /`` read as a lone
``ls``); short-flag matching beaten by bundling (``grep -rf /etc/passwd``) and
attaching (``grep -f/etc/passwd``); and allowlisted readers turned into arbitrary
writes by a flag (``file -Cm`` compiles a magic file to disk). Statically deciding
whether an arbitrary shell command is read-only is a losing game: the failure
lands OUTSIDE the G3 rollback floor (an ``rm -rf`` is not undoable), so the safe
choice is to remove the auto-allow, not to keep patching the classifier. The card
is cheap; a misclassification is not.

This is a gate decision, not a sandbox — OPEN mode is still "nearly completely
open." What changed is only that the dangerous majority no longer has a
frictionless minority hiding a mutation inside it.
============================================================================
"""

from __future__ import annotations

import os
import subprocess

from agent_core.policy import PolicyMode
from agent_core.tools.base import (
    MAX_PERMISSION_DETAIL_CHARS,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

_MAX_OUTPUT_CHARS = 4000    # transcript-friendly truncation
_TIMEOUT_SECONDS = 30

_SAFE_MODE_REFUSAL = (
    "Running commands is only available in the Developer profile."
)


class RunCommandTool:
    definition = ToolDefinition(
        id="run_command",
        label="Run a command",
        description=(
            "Runs a command on this computer and shows its output. "
            "Available only in the Developer profile."
        ),
        risk_tier=RiskTier.HIGH,
        parameters_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run, e.g. 'ls -la'.",
                }
            },
            "required": ["command"],
        },
    )

    def is_destructive(self, args: dict) -> bool:
        """Always True — every run_command call cards (owner decision 2026-07-20;
        see the module docstring for why the read-only auto-allow was removed).

        The gate consults this (tools/base.call_is_destructive) and, because it is
        True, raises the destructive card PER INVOCATION (gate.authorize): the card
        shows this exact command, and approving one command never authorizes a
        later one. The ``args`` are unused — no property of the command text can
        make it safe enough to skip the card, which is the whole point."""
        return True

    def permission_detail(self, args: dict) -> str | None:
        """The exact command text, for the permission card and the Activity Panel.

        ``call_permission_detail`` caps this again with the same constant, so the
        truncation here is not what makes it fit — it is what keeps the ellipsis
        meaning "this command was longer" rather than being applied twice. Read that
        function before returning anything new from a ``permission_detail``: the
        value is shown to the person on every call, in both modes."""
        command = str(args.get("command", "")).strip()
        if not command:
            return None
        if len(command) > MAX_PERMISSION_DETAIL_CHARS:
            return command[:MAX_PERMISSION_DETAIL_CHARS] + "…"
        return command

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        # Belt-and-suspenders: the SAFE registry view never surfaces this tool, but
        # if it is ever reached under SAFE mode, refuse loudly rather than run.
        if context.policy_mode is not PolicyMode.OPEN:
            raise RuntimeError(_SAFE_MODE_REFUSAL)

        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult(success=False, content="No command was given to run.")

        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=os.path.expanduser("~"),
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                content=f"That command didn't finish within {_TIMEOUT_SECONDS} seconds.",
            )
        except OSError as exc:
            return ToolResult(success=False, content=f"Couldn't run that command: {exc}")

        output = completed.stdout or ""
        if completed.stderr:
            output = f"{output}\n{completed.stderr}" if output else completed.stderr
        output = output.strip()
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n… (output truncated)"

        success = completed.returncode == 0
        if not output:
            output = "(the command produced no output)" if success else (
                f"The command exited with status {completed.returncode}."
            )
        return ToolResult(success=success, content=output)
