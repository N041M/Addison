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

# No shell wired — the desktop app is the only place a command can run safely,
# because the sandbox lives there (step 5.5, item 1).
_NO_SHELL_REFUSAL = (
    "Addison can only run commands from the desktop app, so it didn't run that one."
)

# Prepended to the output when the shell could not apply a sandbox profile. Said
# plainly, and said EVERY time — the whole point of returning `sandboxed` is that
# its absence is never invisible.
_UNSANDBOXED_NOTE = "Note: this ran without Addison's usual sandbox around it."


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

    def affected_path(self, args: dict) -> str | None:
        """Always None (step 5, D4). A command's cwd is a CONVENIENCE, never an
        effect bound — #48 settled that scanning command text for what it touches is
        unwinnable — so confinement (which only applies to a non-None affected_path)
        never governs run_command, and it is never trust-suppressed: it keeps
        carding every time regardless of any trusted workspace."""
        return None

    def command_text(self, args: dict) -> str | None:
        """The UNTRUNCATED command, for the hardline denylist (step 5.5, item 3;
        ``tools/base.call_is_forbidden``). Deliberately not ``permission_detail``,
        which is capped at MAX_PERMISSION_DETAIL_CHARS for the card and the
        Activity Panel — a denylist reading a truncated string would stop seeing
        the dangerous path of any command long enough to push it past the cap."""
        command = str(args.get("command", "")).strip()
        return command or None

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

        # THE CORE DOES NOT RUN COMMANDS (step 5.5, item 1). No subprocess here, by
        # design and by test: execution crosses the ShellBridge like every other OS
        # effect (§1.3), which is what puts it in the process that can apply a
        # sandbox. With no shell wired there is no sandbox to run under, so this
        # refuses rather than falling back to running unconfined — the fallback
        # would be exactly the silent-unsandboxed failure this step exists to
        # prevent, and it would reintroduce the deleted subprocess call.
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL_REFUSAL)

        # The live trusted roots become the sandbox's write allowlist. Resolved HERE,
        # at execute time, not captured on the turn: revoking a folder's trust takes
        # effect on the very next command. An empty list is meaningful and safe —
        # nothing is writable outside the shell's own temp allowance.
        write_roots = list(context.trusted_roots() if context.trusted_roots else [])

        try:
            result = context.shell_bridge.run_command(
                command, _TIMEOUT_SECONDS * 1000, write_roots
            )
        except RuntimeError as exc:
            # The bridge turns a shell error or a stall into one plain sentence.
            return ToolResult(success=False, content=str(exc))

        output = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        output = output.strip()
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + "\n… (output truncated)"

        exit_code = int(result.get("exitCode") or 0)
        success = exit_code == 0
        if not output:
            output = "(the command produced no output)" if success else (
                f"The command exited with status {exit_code}."
            )
        # Honest degradation, never silent: on a platform where no profile could be
        # applied the person and the model are both told, in the output itself,
        # rather than the sandbox's absence being invisible.
        if not result.get("sandboxed", False):
            output = f"{_UNSANDBOXED_NOTE}\n\n{output}"
        return ToolResult(success=success, content=output)
