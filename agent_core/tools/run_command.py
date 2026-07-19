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

DESTRUCTIVE CLASSIFICATION (feeds the PermissionGate in OPEN mode). A command is
treated as READ-ONLY (destructive=False -> the gate auto-allows it) only when
BOTH hold:
  1. its first token is in this vetted read-only allowlist —
       ls, cat, head, tail, grep, rg, find, pwd, echo, which, file, wc, du, df,
       uname, ps
     plus ``git`` when its subcommand is one of: status, log, diff, show;
  2. it contains NONE of the shell metacharacters that chain, pipe, or redirect:
       ;   &&   ||   |   >   <   `   $(
EVERYTHING else is destructive=True and takes the normal permission-card flow.
The allowlist is deliberately conservative: anything not provably read-only
prompts. This is a gate heuristic, not a sandbox — OPEN mode is "nearly
completely open", and the destructive check is what keeps a prompt in front of
the dangerous majority.
============================================================================
"""

from __future__ import annotations

import os
import subprocess

from agent_core.policy import PolicyMode
from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

# First tokens that are read-only. ``git`` is handled separately (only certain
# subcommands are read-only), so it is NOT in this bare-command set.
_READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls", "cat", "head", "tail", "grep", "rg", "find", "pwd", "echo",
        "which", "file", "wc", "du", "df", "uname", "ps",
    }
)
_READ_ONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"status", "log", "diff", "show"})

# Metacharacters that chain / pipe / redirect / substitute. Any of these makes a
# command destructive regardless of its first token (it could smuggle a mutation).
_METACHARACTERS: tuple[str, ...] = (";", "&&", "||", "|", ">", "<", "`", "$(")

_MAX_OUTPUT_CHARS = 4000    # transcript-friendly truncation
_MAX_DETAIL_CHARS = 120     # command text shown on the per-invocation card
_TIMEOUT_SECONDS = 30

_SAFE_MODE_REFUSAL = (
    "Running commands is only available in the Developer profile."
)


def is_read_only_command(command: str) -> bool:
    """True iff ``command`` is provably read-only per the module docstring's rules."""
    text = command.strip()
    if not text:
        return False
    if any(meta in text for meta in _METACHARACTERS):
        return False
    tokens = text.split()
    first = tokens[0]
    if first == "git":
        return len(tokens) >= 2 and tokens[1] in _READ_ONLY_GIT_SUBCOMMANDS
    return first in _READ_ONLY_COMMANDS


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
        """Per-call classification the gate consults (tools/base.call_is_destructive).
        Read-only commands are non-destructive (auto-allowed in OPEN mode); anything
        else is destructive and prompts — PER INVOCATION (gate.authorize): approving
        one destructive command never authorizes a later one."""
        return not is_read_only_command(str(args.get("command", "")))

    def permission_detail(self, args: dict) -> str | None:
        """The exact command text for the per-invocation permission card, truncated
        to keep the card readable (tools/base.call_permission_detail)."""
        command = str(args.get("command", "")).strip()
        if not command:
            return None
        if len(command) > _MAX_DETAIL_CHARS:
            return command[:_MAX_DETAIL_CHARS] + "…"
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
