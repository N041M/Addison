"""Step 5.5 — containment for the OPEN harness.

Plan: docs/step-5.5-containment-plan.md. This file covers the CORE half of items
1, 2 and 3. **The boundary itself is tested in Rust** (`shell/src-tauri/src/exec.rs`,
`mod tests`), because that is the process the boundary lives in — including the
plan's headline, `an_approved_command_cannot_delete_the_recovery_floor`, which
only that side can prove.

The split is deliberate, and it is the honest one:

  * Rust proves that a command which IS approved cannot escape the seatbelt
    profile — the floor survives, writes land only inside trusted roots.
  * Python (here) proves that the core never runs a command itself, that a
    forbidden call never reaches the permission gate at any of the three dispatch
    sites, and that the denylist reads the LIVE data directory rather than
    guessing at one.

The debt being paid: step 5 shipped `run_command` as a `subprocess.run(shell=True)`
in the Agent Core, with the per-invocation card as its only layer. `run_command`
has no `affected_path`, so confinement never governed it, and one approved command
deleted the G3 recovery floor — database, sidecars, genesis row, and every
`undeletable` G4 anchor.

Every test here was mutation-proven — reverted its own guard line in a scratch
copy outside the repo and watched it fail (docs/HANDOFF.md, "How step 1 was
verified").
"""

from __future__ import annotations

import os
import pathlib

from agent_core.orchestrator import Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import (
    DENIED_ARMING,
    DENIED_CONTAINS,
    kernel_confines_writes,
    DENIED_INSIDE,
    OS_AUTOMATION_DIRS,
    PolicyMode,
    _derived_data_dir,
    command_arms_automation,
    command_denied_path,
    denylisted_roots,
)
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    FORBIDDEN_CALL_ARMING,
    FORBIDDEN_CALL_CONTAINS,
    FORBIDDEN_CALL_INSIDE,
    RiskTier,
    ToolDefinition,
    ToolResult,
    call_is_forbidden,
)
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import (
    _NO_SHELL_REFUSAL,
    _UNSANDBOXED_NOTE,
    RunCommandTool,
)

# The data dir the live server would pass. Every call below is explicit about it,
# which is the point of the signature change — see policy.denylisted_roots.
DATA_DIR = _derived_data_dir()


def _forbidden(tool, args) -> str | None:
    return call_is_forbidden(tool, args, DATA_DIR)


def _denied(command: str, **kwargs) -> tuple[str, str]:
    """``command_denied_path`` for the cases that MUST be refused, narrowed.

    The predicate returns ``(token, direction) | None`` and a refusal test wants
    the direction, so every call site used to read ``…​[1]`` — which subscripts an
    Optional and is a type error the day anyone runs pyright (it was: four of
    them). Asserting the refusal here means the failure message says "this was
    allowed" instead of "None is not subscriptable"."""
    result = command_denied_path(command, DATA_DIR, **kwargs)
    assert result is not None, f"expected {command!r} to be refused, and it was not"
    return result


# ===========================================================================
# ITEM 1 — the core does not run commands
# ===========================================================================
# The headline lives in Rust. What Python owns is the OTHER half of the same
# property: that there is no second execution path left in this process for a
# sandbox to be bypassed through.


def test_the_agent_core_cannot_execute_a_command_itself():
    """A source-level guard, because this is a property of the FILE, not of one
    call. `run_command.py` used to hold `subprocess.run(shell=True)`; if that (or
    `os.system`, or `popen`) ever comes back, execution silently leaves the
    sandboxed process again and every Rust test above it becomes decorative."""
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "agent_core" / "tools" / "run_command.py"
    ).read_text(encoding="utf-8")
    # Call shapes, not the words: the module's own docstring has to be able to
    # explain what was removed and why, and a guard that forbids the prose would
    # be paid for by deleting the explanation.
    for forbidden in (
        "import subprocess", "subprocess.run", "subprocess.Popen",
        "os.system", "os.popen", "os.exec", "pty.spawn",
    ):
        assert forbidden not in source, (
            f"run_command.py contains {forbidden!r} again — execution must stay in "
            "the shell, where the seatbelt profile is (step 5.5 item 1)"
        )


def test_with_no_shell_wired_the_tool_refuses_rather_than_running_bare():
    # The tempting fallback — "no bridge, so just use subprocess" — is exactly the
    # silent-unsandboxed failure this step exists to prevent.
    tool = RunCommandTool()
    result = tool.execute({"command": "echo hi"}, _open_context(bridge=None))
    assert result.success is False
    assert result.content == _NO_SHELL_REFUSAL


def test_the_command_crosses_the_bridge_with_the_live_trusted_roots():
    tool = RunCommandTool()
    bridge = _FakeExecBridge()
    context = _open_context(bridge=bridge, roots=lambda: ["/tmp/project"])
    result = tool.execute({"command": "ls"}, context)
    assert result.success is True
    assert bridge.calls == [("ls", 30_000, ["/tmp/project"])]
    assert result.content == "hello"


def test_trusted_roots_are_read_at_execute_time_not_captured():
    """Revoking a folder's trust must take effect on the NEXT command, not the next
    conversation. The context carries a callable for exactly this reason: a list
    captured when the turn began would keep a revoked root writable for the rest
    of it — and stale-wider is the one direction that matters."""
    roots = ["/tmp/project"]
    tool = RunCommandTool()
    bridge = _FakeExecBridge()
    context = _open_context(bridge=bridge, roots=lambda: list(roots))
    tool.execute({"command": "ls"}, context)
    roots.clear()                       # the user revokes trust mid-turn
    tool.execute({"command": "ls"}, context)
    assert [call[2] for call in bridge.calls] == [["/tmp/project"], []]


def test_an_unsandboxed_run_is_never_silent():
    """`sandboxed: false` must reach the person and the model. A guard that reports
    success while doing nothing is this project's own anti-pattern, and an absent
    sandbox that nobody is told about is that anti-pattern exactly."""
    tool = RunCommandTool()
    bridge = _FakeExecBridge(sandboxed=False)
    result = tool.execute({"command": "ls"}, _open_context(bridge=bridge))
    assert result.content.startswith(_UNSANDBOXED_NOTE)
    assert "hello" in result.content
    # ...and the sandboxed path says nothing, so the note means something.
    quiet = tool.execute({"command": "ls"}, _open_context(bridge=_FakeExecBridge()))
    assert _UNSANDBOXED_NOTE not in str(quiet.content)


# ===========================================================================
# ITEM 3 — the predicate itself
# ===========================================================================

# Each of these must be refused OUTRIGHT — not carded, not approvable. The first
# group is the floor (G3's own storage); the second is the credential stores that
# would otherwise be read straight into a cloud provider's request body.
_MUST_BE_FORBIDDEN_INSIDE = [
    "rm -rf ~/.addison",
    "rm -rf ~/.addison/snapshots",
    "rm ~/.addison/addison.sqlite3",
    "cat ~/.addison/snapshots/genesis.json",
    "rm -rf $HOME/.addison",             # $HOME spelling
    "rm -rf ${HOME}/.addison",           # ${HOME} spelling
    "rm -rf .addison",                   # relative — cwd is home
    "ls -la ~/.ADDISON",                 # case fold (macOS default volume)
    "cat ~/.ssh/id_rsa",
    "cat ~/.aws/credentials",
    "gpg --export-secret-keys ~/.gnupg",
    "cat .env",                          # basename match, anywhere
    "cat /tmp/project/.env",
    # The per-stage .env spellings — the dominant real-world ones. An exact
    # basename tuple protected the least-used of the four (2026-08-01).
    "cat .env.local",
    "cat .env.production",
    "cat .env.development",
    "cat /tmp/project/.env.production",
    # QUOTING. The shell strips these before it resolves the path, so anything
    # that does not strip them is reading a different string than the shell will.
    'rm -rf ~/.addi"son"',
    "rm -rf ~/'.addison'",
    'cat "$HOME"/.ssh/id_rsa',           # the idiomatic $HOME spelling, quoted
    "cat ~/.ssh/'id_rsa'",
    "rm -rf ~/.addi\\son",               # backslash escape, same principle
    # GLOBBING. A wildcard WIDENS what a token can name, so it has to widen the
    # refusal: each of these expands onto the floor or a credential store, and
    # each was allowed by the shipped build until 2026-08-01.
    "rm -rf ~/.addiso*",
    "rm -rf ~/.addi?on",
    "rm -rf ~/.addis[o]n",
    "cat ~/.s*h/id_rsa",
    "rm -rf ~/.*",                       # matches every dotfile, floor included
    "cat .env*",
    "cat .en?",
    # Attached short flag (#48's vector). Spelled against the REAL home: an
    # earlier draft used a fictional /Users/x/… and a trailing ".", so it passed
    # on the "." (a CONTAINS hit) while the flag path it was written to cover went
    # entirely untested. A vector that passes for the wrong reason is worse than
    # no vector.
    f"grep -f{os.path.expanduser('~')}/.ssh/id_rsa needle",
    "wc --files0-from=$HOME/.addison/x", # =-attached value
    "ls\nrm -rf ~/.addison",             # newline separator (#48's vector)
    "ls; rm -rf ~/.addison",
    "ls && rm -rf ~/.addison",
    "echo hi | tee ~/.addison/x",
]

# The other direction: the token NAMES a folder that holds the floor. Recoverable
# — the message says to name the subfolder meant — and pure scaffolding: once the
# seatbelt profile denies the write, `rm -rf ~` fails at the kernel and `ls ~`
# succeeds, and this list (with `_names_a_directory`) should be DELETED.
_MUST_BE_FORBIDDEN_CONTAINS = [
    "rm -rf ~",                          # takes the floor with it
    "rm -rf /",
    "ls .",                              # cwd IS home; see _names_a_directory
    "ls $HOME",
]

# These must still RUN (after their card). A denylist that refuses ordinary work
# is a denylist people route around, and the coding harness is the whole point of
# Developer mode.
_MUST_STILL_BE_ALLOWED = [
    "ls",
    "ls -la",
    "git status",
    "npm test",
    "cat notes.txt",
    "ls ~/projects",
    "rm -rf ~/projects/build",
    "grep -rn TODO src/",
    "python3 -m pytest tests/",
    "echo addison",                      # the word, not the path
    "cat environment.txt",               # NOT .env
    "cat .environment_notes",            # .env is a prefix of NAMES, not of text
    "cat .envoy.yaml",                   # ditto — the boundary is the dot
    # Globs that must NOT be widened onto the floor. A wildcard does not match a
    # leading dot in any shell, and a denylist that pretends otherwise refuses
    # `ls *` in the home directory — the false positive that gets a guard
    # switched off rather than fixed.
    "ls *",
    "ls *.py",
    "rm -rf ~/*",
    "grep -rn TODO src/*.ts",
    "ls ~/projects/*/dist",
    "echo \"hello world\"",              # quotes that reveal nothing when removed
    "git commit -m 'fix: a thing'",
    "",
]


def test_every_forbidden_command_is_refused():
    tool = RunCommandTool()
    for command in _MUST_BE_FORBIDDEN_INSIDE:
        assert _forbidden(tool, {"command": command}) == FORBIDDEN_CALL_INSIDE, command


def test_only_a_platform_that_really_confines_writes_claims_to(monkeypatch):
    """The predicate the whole relaxation hangs on, pinned to the platform.

    Every other test here passes `kernel_confined` explicitly — which is right,
    because a test that only runs on the other operating system is a test nobody
    runs, but it leaves `kernel_confines_writes` itself unmeasured. Verified:
    hard-coding it to True keeps the entire suite green while a Linux build
    silently loses the only thing standing between `rm -rf ~` and the recovery
    floor. So the mapping gets its own assertion, in both directions.

    `sandbox_invocation` is `#[cfg(target_os = "macos")]`; everything else shells
    out to /bin/sh with `sandboxed: false`. If a Landlock or bubblewrap path ever
    lands, this is the line that changes — and the CONTAINS direction can retire
    there too."""
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    assert kernel_confines_writes() is False
    # ...and the default argument follows it, so the tool-level path is covered.
    assert command_denied_path("rm -rf ~", DATA_DIR) is not None

    monkeypatch.setattr(_sys, "platform", "darwin")
    assert kernel_confines_writes() is True
    assert command_denied_path("rm -rf ~", DATA_DIR) is None


def test_the_contains_direction_still_guards_a_platform_the_kernel_does_not():
    """`rm -rf ~` on a machine with no sandbox. Retired on macOS, NOT deleted.

    Where `sandbox_invocation` shells out to `/bin/sh` with `sandboxed: false`,
    this string is the only thing between the recovery floor and a command that
    takes the whole home directory with it. The list is asserted at the predicate
    rather than through the tool because the tool reads the real platform, and a
    test that can only run on the OTHER operating system is a test nobody runs."""
    for command in _MUST_BE_FORBIDDEN_CONTAINS:
        assert _denied(command, kernel_confined=False)[1] == DENIED_CONTAINS, command


def test_reading_the_home_directory_is_ordinary_work_where_writes_are_confined():
    """The other half, and the reason the direction was retired (2026-08-06).

    `ls ~`, `ls .` and `grep -r TODO .` were refused OUTRIGHT — not carded,
    refused — because read and write are indistinguishable in a `shell=True`
    string. The seatbelt now makes that distinction at the kernel, so refusing
    the read buys nothing and costs the coding harness its most ordinary
    commands. A control a developer cannot approve past is one they route around
    with `cd`, which also defeats this module's relative-path resolution."""
    for command in (*_MUST_BE_FORBIDDEN_CONTAINS, "grep -r TODO .", "npm run build -- --out ."):
        assert command_denied_path(command, DATA_DIR, kernel_confined=True) is None, command
    # ...while every INSIDE refusal is untouched by confinement: the sandbox
    # deliberately PERMITS reads, so `cat ~/.ssh/id_rsa` still has to be refused
    # here or it is not refused anywhere.
    for command in _MUST_BE_FORBIDDEN_INSIDE:
        assert command_denied_path(command, DATA_DIR, kernel_confined=True) is not None, command


def test_the_two_refusals_say_different_things():
    """One message for both directions told the model the wrong thing half the
    time: a CONTAINS refusal has an obvious next move (name the subfolder) and a
    dead-end sentence made every `ls ~` look like a blocked task."""
    assert FORBIDDEN_CALL_INSIDE != FORBIDDEN_CALL_CONTAINS
    assert "Name the folder inside it" in FORBIDDEN_CALL_CONTAINS


def test_ordinary_developer_commands_are_untouched():
    tool = RunCommandTool()
    for command in _MUST_STILL_BE_ALLOWED:
        assert _forbidden(tool, {"command": command}) is None, command


def test_the_denylist_covers_the_floor_and_the_credential_stores():
    roots = [os.path.normcase(os.path.realpath(r)) for r in denylisted_roots(DATA_DIR)]
    home = os.path.expanduser("~")
    for expected in (
        os.path.join(home, ".addison"),
        os.path.join(home, ".addison", "snapshots"),
        os.path.join(home, ".ssh"),
        os.path.join(home, ".aws"),
        os.path.join(home, ".gnupg"),
    ):
        assert os.path.normcase(os.path.realpath(expected)) in roots, expected


def test_the_offending_token_is_reported_not_just_a_bool():
    # command_denied_path returns the token, so a future surface can say WHICH
    # path was refused without re-deriving it.
    assert command_denied_path("rm -rf ~/.addison", DATA_DIR) == ("~/.addison", DENIED_INSIDE)
    assert command_denied_path("rm -rf ~", DATA_DIR, kernel_confined=False) == (
        "~", DENIED_CONTAINS,
    )
    assert command_denied_path("ls -la ~/projects", DATA_DIR) is None


def test_a_glob_widens_the_refusal_rather_than_escaping_it():
    """The one-character bypass, pinned as its own property.

    `rm -rf ~/.addiso*` destroys the recovery floor exactly as naming it does, and
    the shipped build allowed it because it is not literally the same string. A
    pattern names a SET of paths, so the question a denylist has to answer is
    "could this expand onto a protected path", not "is this spelled like one".

    The direction matters as much as the refusal: a pattern that could only ever
    expand to something ABOVE the floor is CONTAINS (recoverable, "name the
    subfolder"), and one that could land on or inside it is INSIDE."""
    assert command_denied_path("rm -rf ~/.addiso*", DATA_DIR) == (
        "~/.addiso*", DENIED_INSIDE,
    )
    assert command_denied_path("cat ~/.s*h/id_rsa", DATA_DIR) == (
        "~/.s*h/id_rsa", DENIED_INSIDE,
    )
    # ...and the leading-dot rule holds, so ordinary globbing is untouched.
    assert command_denied_path("rm -rf ~/*", DATA_DIR) is None
    assert command_denied_path("ls *", DATA_DIR) is None


def test_a_quoted_path_names_what_the_shell_says_it_names():
    """`rm -rf ~/.addi"son"` was conceded in the docstring and live in the code.
    Conceding an evasion does not make it acceptable when the fix is to delete the
    characters the shell itself deletes."""
    assert _denied('rm -rf ~/.addi"son"')[1] == DENIED_INSIDE
    assert _denied('cat "$HOME"/.ssh/id_rsa')[1] == DENIED_INSIDE
    # Quotes around something harmless stay harmless once removed.
    assert command_denied_path("git commit -m 'fix: a thing'", DATA_DIR) is None


def test_a_dash_prefixed_token_that_is_entirely_a_path_is_still_examined():
    """The attached-short-flag candidate used to require the path character at a
    NON-ZERO index (`min(positions) > 0`), which silently dropped the candidate
    whenever the de-dashed token WAS the path. Index 0 is not a sentinel."""
    assert command_denied_path("rm -rf -~/.addison", DATA_DIR) is not None
    assert command_denied_path("cat -/etc/../root/.ssh/id_rsa", DATA_DIR) is None


def test_a_long_command_is_scanned_in_full():
    # command_text is untruncated on purpose: permission_detail caps at 120 chars
    # for the card, and a denylist reading THAT would stop seeing the dangerous
    # path of any command long enough to push it past the cap.
    padding = "echo " + ("x" * 400)
    command = f"{padding}; rm -rf ~/.addison"
    tool = RunCommandTool()
    detail = tool.permission_detail({"command": command})
    assert detail is not None and detail.endswith("…")
    assert _forbidden(tool, {"command": command}) == FORBIDDEN_CALL_INSIDE


# ===========================================================================
# STEP 8 PHASE 1 — the same denylist, grown to cover ARMING
# ===========================================================================
# Written here rather than in a new file because it is not a new mechanism: the
# OS-automation directories join `denylisted_roots` exactly as the credential
# stores did, and the arming binaries ride the same `command_denied_path` call, so
# all three dispatch sites above inherit both with no new plumbing.
#
# WHY IT IS ON THIS LIST AT ALL (step-8 plan §2/§5.5): a file written into
# ~/Library/LaunchAgents IS a login-time job the OS will run — outside Addison's
# sandbox, with Addison closed. Until 2026-08-07 that folder could be trusted as a
# workspace and written with an ordinary card, which is arming with no keyword
# gate. The trust half is in test_workspace_trust.py; this is the command half.

_MUST_BE_FORBIDDEN_AUTOMATION = [
    "cp job.plist ~/Library/LaunchAgents/com.evil.plist",
    "echo hi > ~/Library/LaunchAgents/x.plist",
    "cat ~/Library/LaunchAgents/x.plist",          # the accepted cost: reads too
    "ls ~/Library/LaunchAgents",
    "cp x /Library/LaunchDaemons/y.plist",
    "cp x /etc/cron.d/mine",
    "echo '* * * * * x' > /etc/crontab",           # a FILE root, not a directory
    "cp unit.service ~/.config/systemd/user/u.service",
    "cp unit.service /etc/systemd/system/u.service",
    "rm /var/spool/cron/karel",
    # QUOTING and GLOBBING, the two one-character evasions this module already
    # closed for the floor. An automation directory gets the same treatment or the
    # closure is spelling-deep.
    'cp x ~/Library/Launch"Agents"/y.plist',
    "cp x ~/Library/'LaunchAgents'/y.plist",
    "cp x ~/Library/LaunchAgent*/y.plist",
    "cp x ~/Library/LaunchAgent?/y.plist",
    "cp x ~/Library/LaunchAgent[s]/y.plist",
    # Separators, as elsewhere: the second command is scanned too.
    "ls && cp x ~/Library/LaunchAgents/y.plist",
    "ls\ncp x ~/Library/LaunchAgents/y.plist",
]

# The arming programs. Refused for what they RUN, not for what they name — so the
# refusal has to survive an absolute path, a case change and quotes, and must not
# fire on a command that merely mentions one.
_MUST_BE_FORBIDDEN_ARMING = [
    "crontab -",
    "crontab -e",
    "launchctl load ~/x.plist",
    "at now + 1 minute",
    "batch",
    "/usr/bin/crontab -",                          # basename, wherever it lives
    "CRONTAB -",                                   # case-folded (macOS volume)
    '"crontab" -',                                 # dequoted, as the shell does
    "cd /tmp && crontab -",                        # first word of a LATER segment
    "ls; launchctl list",
    "ls\nat now",
    "(crontab -l)",
    "cat jobs | at now",
]

_ARMING_MUST_STILL_BE_ALLOWED = [
    "man crontab",                                 # talking about it, not running it
    "echo launchctl",
    "grep -rn crontab docs/",
    "ls > batch",                                  # a redirect names a FILE
    "echo x > ./out/at",
    "git commit -m 'switch the batch size'",
]


def test_an_os_automation_directory_is_refused_like_a_credential_store():
    """Through the same public entry the rest of the denylist is tested through.

    ``cat ~/Library/LaunchAgents/x.plist`` being refused is a KNOWN AND ACCEPTED
    cost, asserted here so it is a decision rather than a surprise: this string
    cannot tell a read from a write, and the seatbelt — which can — denies only
    writes there."""
    tool = RunCommandTool()
    for command in _MUST_BE_FORBIDDEN_AUTOMATION:
        assert _forbidden(tool, {"command": command}) == FORBIDDEN_CALL_INSIDE, command
    # ...and the sentence they get has to be TRUE of this group. It named two kinds
    # of folder (restore points, credential stores) until this list grew a third.
    assert "on a schedule" in FORBIDDEN_CALL_INSIDE


def test_the_denylist_covers_every_os_automation_directory():
    roots = [os.path.normcase(os.path.realpath(r)) for r in denylisted_roots(DATA_DIR)]
    for entry in OS_AUTOMATION_DIRS:
        expected = os.path.normcase(os.path.realpath(os.path.expanduser(entry)))
        assert expected in roots, entry
    # The SET, spelled out, because every other assertion in this file iterates
    # OS_AUTOMATION_DIRS — so deleting a row from the tuple would make them all pass
    # on a shorter fence. This is the only test that notices. A legitimate ADDITION
    # fails here too, and should: the list is closed, and growing it is a decision.
    assert set(OS_AUTOMATION_DIRS) == {
        "~/Library/LaunchAgents", "~/Library/LaunchDaemons",
        "/Library/LaunchAgents", "/Library/LaunchDaemons",
        "/etc/cron.d", "/etc/crontab", "/var/spool/cron", "/var/at",
        "/usr/lib/cron", "/etc/systemd/system", "~/.config/systemd",
    }


def test_an_automation_root_is_refused_inside_but_never_contains():
    """The asymmetry, pinned in both directions, because it is the one part of this
    fence that had to be reasoned about rather than copied.

    CONTAINS exists because naming a folder that HOLDS the recovery floor destroys
    it. Nothing about naming ``~/Library`` arms anything — so asking CONTAINS of an
    automation root would refuse ``rm -rf ~/*`` and ``ls ~/Library`` wherever the
    kernel does not confine writes (which is every platform but macOS, including
    CI). That is an ordinary command, and a guard that refuses ordinary commands is
    one people route around."""
    # kernel_confined=False is the strict platform — if CONTAINS were asked of an
    # automation root anywhere, it would be asked here.
    for allowed in ("rm -rf ~/*", "ls ~/Library", "ls ~/Library/Preferences"):
        assert command_denied_path(allowed, DATA_DIR, kernel_confined=False) is None, allowed
    # ...while reaching INSIDE one is refused on the permissive platform too.
    inside = "cp x ~/Library/LaunchAgents/y.plist"
    assert command_denied_path(inside, DATA_DIR, kernel_confined=True) == (
        "~/Library/LaunchAgents/y.plist", DENIED_INSIDE,
    )


def test_a_command_that_arms_automation_is_refused_whatever_it_names():
    tool = RunCommandTool()
    for command in _MUST_BE_FORBIDDEN_ARMING:
        assert _forbidden(tool, {"command": command}) == FORBIDDEN_CALL_ARMING, command
        assert _denied(command)[1] == DENIED_ARMING, command


def test_talking_about_an_arming_program_is_not_running_one():
    """The precision half. A guard that refuses ``man crontab`` teaches the model
    that the whole subject is blocked, and the first thing a person does with a
    control they cannot approve past is route around it."""
    tool = RunCommandTool()
    for command in _ARMING_MUST_STILL_BE_ALLOWED:
        assert _forbidden(tool, {"command": command}) is None, command
        assert command_arms_automation(command) is None, command


def test_the_arming_refusal_says_what_to_do_instead():
    """Plain language, and a next move. The honest answer today is that scheduled
    automation is not built yet — so the sentence says that, and hands over the one
    thing that does work, rather than reading as a permission problem with a door
    somewhere."""
    assert FORBIDDEN_CALL_ARMING not in (FORBIDDEN_CALL_INSIDE, FORBIDDEN_CALL_CONTAINS)
    assert "Nothing was run" in FORBIDDEN_CALL_ARMING
    assert "your own terminal" in FORBIDDEN_CALL_ARMING
    for jargon in ("arm", "launchd", "denylist", "cron", "plist", "sandbox"):
        assert jargon not in FORBIDDEN_CALL_ARMING.lower(), jargon


# ===========================================================================
# ITEM 3 — at all three dispatch sites, and BEFORE the gate
# ===========================================================================
# "Assert PermissionGate.authorize was not called, not merely that the result
# failed." A forbidden call is not a card the person can approve; if it reaches
# the gate at all, the person has been shown a door that should not exist.


class _ExplodingGate(PermissionGate):
    """Any call to authorize is a test failure — the point of 'before the gate'."""

    def authorize(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a forbidden call reached the permission gate")


class _ScriptedProvider:
    def __init__(self, responses):
        self._responses = list(responses)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None):
        return self._responses.pop(0)


class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        pass


class _RecordingRunCommand:
    """run_command's exact shape — HIGH, dev-only, always destructive, no
    affected_path, declares command_text — but records instead of running."""

    definition = ToolDefinition(
        id="run_command", label="Run a command", description="dev-only",
        risk_tier=RiskTier.HIGH, parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.ran: list[dict] = []

    def is_destructive(self, args: dict) -> bool:
        return True

    def affected_path(self, args: dict) -> str | None:
        return None

    def command_text(self, args: dict) -> str | None:
        return str(args.get("command", "")).strip() or None

    def permission_detail(self, args: dict) -> str | None:
        return str(args.get("command", "")) or None

    def execute(self, args, context) -> ToolResult:
        self.ran.append(args)
        return ToolResult(success=True, content="ran")


def _registry_with(tool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool, dev_only=True, allow_missing_undo=True)
    return registry


class _FakeExecBridge:
    """The shell's half of `shell.runCommand`, recording what it was asked."""

    def __init__(self, sandboxed: bool = True) -> None:
        self.calls: list[tuple[str, int, list[str]]] = []
        self._sandboxed = sandboxed

    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        self.calls.append((command, timeout_ms, list(write_roots)))
        return {
            "stdout": "hello", "stderr": "", "exitCode": 0,
            "sandboxed": self._sandboxed,
        }


def _open_context(bridge=None, roots=None):
    return ExecutionContext(
        conversation_id="c",
        policy_mode=PolicyMode.OPEN,
        shell_bridge=bridge,
        trusted_roots=roots,
    )


def _run_one_call(registry, gate, args):
    provider = _ScriptedProvider([
        ModelResponse(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", tool_id="run_command", args=args)],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        shell_bridge=None,
        trust_check=lambda path: True,   # everything trusted: the denylist still wins
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="go"))
    orch.run_turn(conv, mode=PolicyMode.OPEN)
    return next(m for m in conv.messages if m.role == "tool")


def test_live_loop_refuses_before_the_gate_and_before_execute():
    tool = _RecordingRunCommand()
    registry = _registry_with(tool)
    result = _run_one_call(registry, _ExplodingGate(), {"command": "rm -rf ~/.addison"})
    assert result.content == FORBIDDEN_CALL_INSIDE
    assert tool.ran == []


def test_live_loop_is_not_vacuous_an_allowed_command_still_runs():
    # The negative twin: same harness, same everything-trusted setup, ordinary
    # command — it cards (a real gate that grants) and it runs.
    tool = _RecordingRunCommand()
    registry = _registry_with(tool)
    gate = PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED)
    result = _run_one_call(registry, gate, {"command": "ls ~/projects"})
    assert result.content == "ran"
    assert tool.ran == [{"command": "ls ~/projects"}]


def test_live_loop_refuses_an_arming_command_before_the_gate():
    """Step 8 phase 1's arming refusal gets the same proof the path refusals get:
    the person is never shown a door that should not exist. The routine engine and
    the widget rail need no test of their own for it — the refusal rides the one
    ``call_is_forbidden`` all three sites already make, and the tests below pin
    that they all make it."""
    tool = _RecordingRunCommand()
    registry = _registry_with(tool)
    result = _run_one_call(registry, _ExplodingGate(), {"command": "crontab -"})
    assert result.content == FORBIDDEN_CALL_ARMING
    assert tool.ran == []


def test_a_routine_step_is_refused_before_the_gate(tmp_path):
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    tool = _RecordingRunCommand()
    registry = _registry_with(tool)
    store = Store(tmp_path / "routine.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=_ExplodingGate(),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=None,
        store=store,
        trust_check=lambda path: True,
    )
    routine = Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "run_command", {"command": "rm -rf ~/.addison"})],
    )
    run = engine.run(routine, {}, mode=PolicyMode.OPEN)
    assert run.status == "failed"
    assert FORBIDDEN_CALL_INSIDE in str(run.detail)
    assert tool.ran == []


def test_a_command_widgets_run_pill_is_refused_before_the_gate(tmp_path):
    """The third and last site a command reaches a shell. Driven end-to-end
    through the real JSON-RPC server, because that is the only way to prove no
    ``permission.requestGrant`` frame was ever emitted — the widget equivalent of
    "assert authorize was not called"."""
    import json

    from agent_core.memory.store import Store
    from agent_core.protocol import Method
    from tests.test_policy_modes import _artifact_server, _rpc, _shutdown

    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        store = Store(db_path)
        store.insert_widget(
            id="forbidden-w",
            spec_json=json.dumps(
                {"kind": "command", "command": "rm -rf ~/.addison", "title": "Wipe"}
            ),
            pinned=True, position=9, created_at=9, created_in_mode="open",
        )
        store.close()
        result = _rpc(reader, writer, 1, Method.WIDGET_RUN, {"id": "forbidden-w"})["result"]
        assert result["ok"] is False
        assert result["error"] == FORBIDDEN_CALL_INSIDE
        # No card was ever raised: the person was not shown a door to approve.
        assert not any(
            frame.get("method") == Method.PERMISSION_REQUEST_GRANT
            for frame in writer.frames
        )
    finally:
        _shutdown(reader, thread)


def test_a_routine_variable_cannot_smuggle_a_forbidden_path(tmp_path):
    # The step template is checked AFTER resolve_template, so a routine whose
    # command is assembled from a variable is checked on what it will actually run.
    from agent_core.memory.store import Store
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep, RoutineVariable

    tool = _RecordingRunCommand()
    registry = _registry_with(tool)
    store = Store(tmp_path / "routine.sqlite3")
    store.insert_routine(
        id="r-2", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=_ExplodingGate(),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=None,
        store=store,
        trust_check=lambda path: True,
    )
    routine = Routine(
        id="r-2", name="T", description="",
        variables=[RoutineVariable(name="target", prompt="where?")],
        steps=[RoutineStep("s1", "run_command", {"command": "rm -rf {{target}}"})],
    )
    run = engine.run(routine, {"target": "~/.addison"}, mode=PolicyMode.OPEN)
    assert run.status == "failed"
    assert tool.ran == []


# ===========================================================================
# THE RECURRENCE GUARD — one owner for "which data directory?"
# ===========================================================================
# This exists because the mistake it prevents was made twice inside step 5.5
# itself, in both directions:
#
#   * the first cut re-derived the data dir to run a SECOND copy of the floor on
#     path-bounded tools — under the test harness (conftest points ADDISON_DB_PATH
#     at tmp_path) that judged every ordinary file to be inside the data dir, and
#     11 step-5 tests failed;
#   * the fix for that left `command_denied_path` still re-deriving, so a store
#     opened on any non-default path would have been protected in name only.
#
# The signatures now REQUIRE a data dir, and this test pins the small set of
# places allowed to answer "which one". Everything else must be handed it.


def test_only_the_owner_modules_may_derive_the_data_directory():
    root = pathlib.Path(__file__).resolve().parent.parent / "agent_core"
    # MATCHED ON THE PATH FROM ``agent_core/``, NOT ON ``path.name``. A basename
    # whitelist reads as if it names three modules; it actually exempts every file
    # in the tree that happens to share a basename — and `base.py` alone is
    # `tools/base.py`, `rpc/base.py` and `providers/base.py`. Two of those three
    # were silently allowed to re-derive the data dir by a guard whose entire job
    # is to stop exactly that.
    allowed = {
        # policy.py defines it.
        "policy.py",
        # rpc/workspace.py is the AUTHORITY: it answers from the running store's
        # path and falls back to the derivation only when no store is wired.
        "rpc/workspace.py",
        # tools/base.py's named fallback, for a construction with no server at all.
        "tools/base.py",
    }
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative in allowed:
            continue
        if "_derived_data_dir" in path.read_text(encoding="utf-8"):
            offenders.append(relative)
    assert not offenders, (
        "these modules re-derive the data directory instead of being handed the "
        f"live one: {offenders}. See policy.denylisted_roots for why that is a "
        "signature and not a convention."
    )


def test_the_live_server_binds_the_denylist_to_its_own_store(tmp_path):
    """The binding itself, end to end: a server whose store is NOT at the derived
    location must still protect that store. This is the test the second bug would
    have failed — the derivation would have guarded ~/.addison while the running
    data dir went unguarded."""
    from agent_core.memory.store import Store
    from agent_core.rpc.workspace import WorkspaceMixin

    live = tmp_path / "elsewhere"
    live.mkdir()

    class _Server(WorkspaceMixin):
        # Annotated because ServerContext declares `store` as a read-only
        # property; a bare assignment on the subclass is a type error even though
        # it is exactly what this stand-in needs to do.
        store: Store
        _db_path: pathlib.Path = live / "addison.sqlite3"

        def __init__(self):
            self.store = Store(self._db_path)

    server = _Server()
    tool = RunCommandTool()
    try:
        # The LIVE dir is protected...
        assert server._is_forbidden_call(
            tool, {"command": f"rm -rf {live}"}
        ) is not None
        # ...and an unrelated folder is still ordinary work.
        assert server._is_forbidden_call(
            tool, {"command": f"ls {tmp_path / 'somewhere-else'}"}
        ) is None
    finally:
        server.store.close()


def test_the_bridge_sends_exactly_what_the_shell_reads():
    """The twin of Rust's ``the_wire_contract_matches_what_the_core_sends``.

    `protocol.py` / `protocol.ts` are hand-synced (codegen is Phase 3), and the
    drift test covers the METHOD NAME only — not the shape of what travels under
    it. So a renamed field (``timeoutMs`` -> ``timeout_ms``, ``exitCode`` ->
    ``exit_code``) would pass every suite on both sides and fail the first time the
    app ran, in the highest-trust process. Each side pins the same four names
    against a literal; a hand-synced protocol asserted on only one side is asserted
    on neither."""
    from agent_core.protocol import Method
    from agent_core.shell_bridge import _EXEC_SLACK_MS, IpcShellBridge

    sent: list[dict] = []
    bridge = IpcShellBridge(send=sent.append)

    # Answer the call inline, the way the shell's response frame would.
    def _resolve(frame):
        sent.append(frame)
        bridge.resolve_response(
            frame["id"],
            {"stdout": "hi", "stderr": "", "exitCode": 0, "sandboxed": True},
            None,
        )

    bridge._send = _resolve
    result = bridge.run_command("echo hi", 30_000, ["/tmp/project"])

    frame = sent[0]
    assert frame["method"] == Method.SHELL_RUN_COMMAND == "shell.runCommand"
    assert frame["params"] == {
        "command": "echo hi",
        "timeoutMs": 30_000,
        "writeRoots": ["/tmp/project"],
    }
    assert set(result) == {"stdout", "stderr", "exitCode", "sandboxed"}
    # And the waiter's budget is the command's, not the shell's default.
    assert _EXEC_SLACK_MS > 0


def test_the_tool_reads_every_field_the_shell_returns():
    """The other half: the four names the TOOL unpacks. A field the shell provides
    and the tool never reads is a field one side thinks is doing something."""
    import inspect

    from agent_core.tools import run_command as module

    source = inspect.getsource(module.RunCommandTool.execute)
    for field in ("stdout", "stderr", "exitCode", "sandboxed"):
        assert f'"{field}"' in source, f"execute() never reads {field!r}"
