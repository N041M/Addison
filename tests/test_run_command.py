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


class _FakeExecBridge:
    """The shell's half of ``shell.runCommand``.

    Execution left the Agent Core in step 5.5 item 1 — it crosses the ShellBridge
    so the command lands in the process that can put a seatbelt profile around it.
    So these tests can no longer run a real ``echo``: what they check is the tool's
    half of the contract (what it sends, how it reads the answer). The sandbox
    itself is tested where it lives, in ``shell/src-tauri/src/exec.rs``."""

    def __init__(self, **response) -> None:
        self.calls: list[tuple] = []
        self._response = {
            "stdout": "", "stderr": "", "exitCode": 0, "sandboxed": True, **response
        }

    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        self.calls.append((command, timeout_ms, list(write_roots)))
        return self._response


def _open(bridge) -> ExecutionContext:
    return ExecutionContext(
        conversation_id="c", policy_mode=PolicyMode.OPEN, shell_bridge=bridge
    )


def test_execute_sends_the_command_to_the_shell_and_returns_its_output():
    tool = RunCommandTool()
    bridge = _FakeExecBridge(stdout="hello-from-addison")
    result = tool.execute({"command": "echo hello-from-addison"}, _open(bridge))
    assert result.success is True
    assert "hello-from-addison" in str(result.content)
    assert result.snapshot is None   # not undoable
    assert bridge.calls[0][0] == "echo hello-from-addison"


def test_execute_reports_nonzero_exit_without_crashing():
    tool = RunCommandTool()
    result = tool.execute({"command": "false"}, _open(_FakeExecBridge(exitCode=1)))
    assert result.success is False


def test_a_credential_straddling_the_truncation_point_cannot_survive_as_a_fragment():
    """Truncation must not be able to defeat the redactor.

    Every rule in ``agent_core.redaction`` is anchored on a vendor prefix plus a
    minimum body (``{16,}``), and the send boundary is what applies them. Cutting
    the output at ``_MAX_OUTPUT_CHARS`` first sliced a straddling credential BELOW
    that minimum, so the surviving head matched nothing at the boundary and
    travelled to the provider intact — the truncation silently disarming the one
    thing standing between command output and someone else's server.

    Assembled from prefix + body, never a literal: a credential-shaped literal is
    what a secret scanner is built to find, and it cannot tell a fixture from the
    real thing (GitHub push protection has already blocked this repo once)."""
    from agent_core.redaction import redact
    from agent_core.tools.run_command import _MAX_OUTPUT_CHARS

    secret = "sk-" + "proj-" + "0123456789abcdefghijklmnopqrstuv"
    kept = 18                       # what a naive cut would have left of it
    fragment = secret[:kept]
    # The head must start on a word boundary or no rule could anchor on it at all.
    head = "x" * (_MAX_OUTPUT_CHARS - kept - 1) + "\n"
    stdout = head + secret + "\nBuild finished.\n" + "y" * 200

    # NOT VACUOUS, in both directions: the WHOLE secret is catchable, and the
    # fragment a naive cut leaves behind is not — so truncate-first really did put
    # it on the wire, and this test is guarding a live hole rather than a
    # hypothetical one.
    assert redact(stdout).text != stdout
    assert redact(fragment).text == fragment

    tool = RunCommandTool()
    result = tool.execute({"command": "./deploy.sh"}, _open(_FakeExecBridge(stdout=stdout)))
    content = str(result.content)

    assert "(output truncated)" in content      # the cap still holds
    assert secret not in content
    assert fragment not in content
    # ...and it was NAMED, not merely lost: the model is told something was there,
    # so it does not helpfully re-run the command to fetch it.
    assert "[redacted:" in content


def test_output_under_the_cap_keeps_its_real_bytes():
    """The other half of the rule above, and the reason it is written as narrowly
    as it is: redaction toward the model is owned by the orchestrator's send
    boundary, and the transcript keeps the real bytes (agent_core.redaction,
    decision #1). Truncation is the ONE thing that can defeat that boundary, so
    only the truncated path scrubs — an ordinary command's output is untouched."""
    secret = "sk-" + "proj-" + "0123456789abcdefghijklmnopqrstuv"
    tool = RunCommandTool()
    result = tool.execute(
        {"command": "./deploy.sh"},
        _open(_FakeExecBridge(stdout=f"TOKEN={secret}\ndone")),
    )
    assert secret in str(result.content)
