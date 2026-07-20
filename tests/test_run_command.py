"""run_command — the dev-only shell tool (owner decision 2026-07-19).

Every command cards (owner decision 2026-07-20). These tests pin that: there is
no command — however innocent-looking — that ``is_destructive`` calls safe enough
to skip the permission card. The list below is not an allowlist being checked; it
is a set of vectors that a classifier WOULD have to get right and that this design
deliberately no longer tries to, because getting one wrong runs an unprompted
``rm -rf`` outside the G3 rollback floor. If any of these ever returns
non-destructive again, a classifier has crept back in.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.tools.base import ExecutionContext, RiskTier, call_is_destructive
from agent_core.tools.run_command import RunCommandTool

# Every one of these must card. The first group is the obviously-mutating; the
# rest are the exact vectors that defeated the old classifier, kept as named
# regressions so the reasoning survives even though the code that failed is gone.
_MUST_ALL_CARD = [
    "ls",                            # the most innocent read imaginable — still cards
    "git status",
    "cat file.txt",
    "rm -rf /",
    "git push",
    "ls; rm x",                      # shell operator
    "ls\nrm -rf /tmp/x",             # newline: shlex treats it as whitespace
    "ls & rm -rf /tmp/x",            # bare & — the old metachar list missed it
    "find . -delete",                # allowlisted reader, deleting primary
    "find . -exec rm {} +",          # allowlisted reader, exec primary
    "grep -rf /etc/passwd .",        # bundled short flag: arbitrary-file read
    "grep -f/etc/passwd x",          # attached short flag
    "file -Cm /tmp/x",               # allowlisted reader that WRITES a .mgc file
    "wc --files0-from=/etc/shadow",  # read the file-list from a secret path
    "",                              # empty
    "   ",                           # whitespace only
]


def test_every_command_cards_including_the_innocent_ones():
    tool = RunCommandTool()
    for command in _MUST_ALL_CARD:
        args = {"command": command}
        assert tool.is_destructive(args) is True, command
        # The gate helper (tools.base) is what the PermissionGate actually calls.
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
