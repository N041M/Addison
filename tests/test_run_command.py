"""run_command — the dev-only shell tool (owner decision 2026-07-19).

Covers the read-only classification table that feeds the PermissionGate (each
allowlisted form is non-destructive, each metachar/unknown is destructive), the
SAFE-mode belt refusal, and a real end-to-end execution in OPEN mode.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.tools.base import ExecutionContext, RiskTier, call_is_destructive
from agent_core.tools.run_command import RunCommandTool, is_read_only_command

# (command, expected read_only?) — the vetted allowlist AND its exclusions.
_READ_ONLY = [
    "ls",
    "ls -la",
    "cat file.txt",
    "head -n 5 file",
    "tail file",
    "grep foo file",
    "rg pattern",
    "find . -name x",
    "pwd",
    "echo hello",
    "which python",
    "file thing",
    "wc -l file",
    "du -sh .",
    "df -h",
    "uname -a",
    "ps aux",
    "git status",
    "git log --oneline",
    "git diff HEAD",
    "git show HEAD",
]

_DESTRUCTIVE = [
    "rm -rf /",                     # not on the allowlist
    "mv a b",                       # not on the allowlist
    "git push",                     # git, but a mutating subcommand
    "git commit -m x",              # git, but a mutating subcommand
    "ls; rm x",                     # metachar: ;
    "ls && rm x",                   # metachar: &&
    "ls || rm x",                   # metachar: ||
    "cat a | sh",                   # metachar: |
    "echo x > file",                # metachar: >
    "cat < file",                   # metachar: <
    "echo `whoami`",                # metachar: backtick
    "echo $(whoami)",               # metachar: $(
    "",                             # empty -> not provably read-only
    "   ",                          # whitespace only
    "sudo ls",                      # first token not allowlisted
]


def test_read_only_classification_table():
    for command in _READ_ONLY:
        assert is_read_only_command(command) is True, command
    for command in _DESTRUCTIVE:
        assert is_read_only_command(command) is False, command


def test_is_destructive_is_the_inverse_and_drives_the_gate_helper():
    tool = RunCommandTool()
    for command in _READ_ONLY:
        args = {"command": command}
        assert tool.is_destructive(args) is False, command
        # The gate helper (tools.base) honours the tool's own classifier.
        assert call_is_destructive(tool, args) is False, command
    for command in _DESTRUCTIVE:
        args = {"command": command}
        assert tool.is_destructive(args) is True, command
        assert call_is_destructive(tool, args) is True, command


def test_tool_is_high_risk_and_has_no_undo():
    tool = RunCommandTool()
    assert tool.definition.risk_tier is RiskTier.HIGH
    # No undo() implementation — legal only via a dev_only registration.
    assert not hasattr(type(tool), "undo")


def test_execute_refuses_under_safe_mode():
    tool = RunCommandTool()
    ctx = ExecutionContext(conversation_id="c", policy_mode=PolicyMode.SAFE)
    try:
        tool.execute({"command": "ls"}, ctx)
    except RuntimeError as exc:
        assert "Developer profile" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("run_command must refuse under SAFE mode")


def test_execute_runs_a_real_command_in_open_mode():
    tool = RunCommandTool()
    ctx = ExecutionContext(conversation_id="c", policy_mode=PolicyMode.OPEN)
    result = tool.execute({"command": "echo hello-from-addison"}, ctx)
    assert result.success is True
    assert "hello-from-addison" in str(result.content)
    assert result.snapshot is None   # not undoable


def test_execute_reports_nonzero_exit_without_crashing():
    tool = RunCommandTool()
    ctx = ExecutionContext(conversation_id="c", policy_mode=PolicyMode.OPEN)
    result = tool.execute({"command": "false"}, ctx)
    assert result.success is False
