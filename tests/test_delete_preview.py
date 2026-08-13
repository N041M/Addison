"""The delete preview (5.6, first form), the classifier, the sentence, the wiring.

The classifier's whole safety argument is that it FAILS TOWARDS SILENCE: a command
it cannot read with confidence gets no preview, and the card is what it always was.
Most of what follows is therefore a list of commands that must produce nothing.
"""

from __future__ import annotations

from pathlib import Path

from agent_core import delete_preview
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import GuardConfig, PolicyMode

HOME = Path("/Users/someone")


# --- the classifier ---------------------------------------------------------

def test_a_plain_delete_is_read_and_its_target_resolved():
    assert delete_preview.delete_targets("rm -rf /tmp/build", home=HOME) == ["/tmp/build"]
    assert delete_preview.delete_targets("rmdir /tmp/empty", home=HOME) == ["/tmp/empty"]
    assert delete_preview.delete_targets(
        "rm -f /tmp/a /tmp/b", home=HOME
    ) == ["/tmp/a", "/tmp/b"]


def test_a_relative_target_resolves_against_the_directory_a_command_starts_in():
    # exec.rs starts a command in HOME, so a bare `build` is HOME/build and the
    # preview must count that folder rather than one relative to nothing.
    assert delete_preview.delete_targets("rm -rf build", home=HOME) == [
        str(HOME / "build")
    ]
    assert delete_preview.delete_targets("rm -rf ./build/../build", home=HOME) == [
        str(HOME / "build/../build")
    ]


def test_a_home_relative_target_is_expanded():
    # Against the ``home`` PARAMETER, never the process's own HOME: the parameter is
    # the directory a command starts in, and the two must not silently diverge.
    assert delete_preview.delete_targets("rm -rf ~/junk", home=HOME) == [str(HOME / "junk")]
    assert delete_preview.delete_targets("rm ~", home=HOME) == [str(HOME)]


def test_somebody_elses_home_gets_no_preview():
    assert delete_preview.delete_targets("rm -rf ~other/junk", home=HOME) is None


def test_a_command_that_is_not_a_delete_is_left_alone():
    for command in ["ls -la", "git status", "npm install", "mv a b", "echo rm -rf /"]:
        assert delete_preview.delete_targets(command, home=HOME) is None, command


def test_anything_unparseable_gets_no_preview_rather_than_a_wrong_one():
    unreadable = [
        "",
        "   ",
        "rm -rf $BUILD_DIR",          # a variable this process cannot expand
        "rm -rf build/*",             # a glob the shell expands, not a path
        "rm -rf 'unbalanced",         # a quote that never closes
        "ls && rm -rf build",         # more than one command
        "rm -rf build | tee log",     # a pipeline
        "rm -rf $(cat targets)",      # a substitution
        "rm -rf build\nrm -rf dist",  # a newline, the metacharacter shlex hides
        "rm --one-file-system -rf /", # a flag this does not know
        "rm -rf --",                  # a separator this deliberately does not read
        "rm",                         # a delete naming nothing
        "rm -rf",                     # flags and no target
    ]
    for command in unreadable:
        assert delete_preview.delete_targets(command, home=HOME) is None, command


def test_a_delete_hidden_behind_a_prefix_the_shell_drops_is_still_read():
    assert delete_preview.delete_targets("sudo rm -rf /tmp/x", home=HOME) == ["/tmp/x"]
    assert delete_preview.delete_targets("/bin/rm -rf /tmp/x", home=HOME) == ["/tmp/x"]


def test_more_targets_than_the_cap_gets_no_preview():
    many = " ".join(f"/tmp/f{i}" for i in range(delete_preview.MAX_PREVIEW_PATHS + 1))
    assert delete_preview.delete_targets(f"rm -f {many}", home=HOME) is None


# --- the sentence -----------------------------------------------------------

def test_the_line_is_plain_and_counts_what_was_found():
    line = delete_preview.describe(
        {"files": 1240, "directories": 12, "modifiedToday": 3, "capped": False}
    )
    assert line == (
        "About to delete 1,240 files in 12 folders. 3 of them were changed in the last day."
    )


def test_one_file_and_one_folder_are_worded_singly():
    assert delete_preview.describe(
        {"files": 1, "directories": 1, "modifiedToday": 1, "capped": False}
    ) == "About to delete 1 file in 1 folder. 1 of them was changed in the last day."


def test_a_capped_walk_says_more_than_rather_than_a_total():
    line = delete_preview.describe(
        {"files": 5000, "directories": 40, "modifiedToday": 0, "capped": True}
    )
    assert line is not None
    assert line.startswith("About to delete more than 5,000 files")


def test_nothing_found_means_no_line_at_all():
    assert delete_preview.describe(
        {"files": 0, "directories": 0, "modifiedToday": 0, "capped": False}
    ) is None
    assert delete_preview.describe({}) is None
    assert delete_preview.describe("not a dict") is None  # type: ignore[arg-type]


# --- the wiring -------------------------------------------------------------

class _Bridge:
    def __init__(self, counts: dict | Exception):
        self.counts = counts
        self.asked: list[list[str]] = []

    def preview_delete_paths(self, paths: list[str]) -> dict:
        self.asked.append(list(paths))
        if isinstance(self.counts, Exception):
            raise self.counts
        return self.counts


def test_the_walk_is_asked_for_exactly_the_paths_the_command_named():
    bridge = _Bridge({"files": 4, "directories": 1, "modifiedToday": 0, "capped": False})
    line = delete_preview.preview_for_command("rm -rf /tmp/build", bridge, home=HOME)
    assert bridge.asked == [["/tmp/build"]]
    assert line == "About to delete 4 files in 1 folder."


def test_no_shell_means_no_preview_and_no_error():
    assert delete_preview.preview_for_command("rm -rf /tmp/build", None) is None


def test_a_walk_that_fails_costs_a_line_and_never_the_card():
    bridge = _Bridge(RuntimeError("the shell is wedged"))
    assert delete_preview.preview_for_command("rm -rf /tmp/build", bridge, home=HOME) is None


def test_a_non_delete_never_reaches_the_shell_at_all():
    bridge = _Bridge({"files": 9, "directories": 0, "modifiedToday": 0, "capped": False})
    assert delete_preview.preview_for_command("git status", bridge, home=HOME) is None
    assert bridge.asked == []


# --- the gate carries it, and changes nothing else --------------------------

def test_the_gate_hands_the_preview_to_a_handler_that_can_show_it():
    seen: dict = {}

    def handler(tool_id, detail=None, preview=None):
        seen["detail"] = detail
        seen["preview"] = preview
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=handler)
    status = gate.authorize(
        "run_command",
        mode=PolicyMode.OPEN,
        destructive=True,
        detail="rm -rf /tmp/build",
        preview="About to delete 4 files in 1 folder.",
    )
    assert status == PermissionStatus.GRANTED
    assert seen == {
        "detail": "rm -rf /tmp/build",
        "preview": "About to delete 4 files in 1 folder.",
    }


def test_a_two_argument_handler_still_gets_the_card_it_always_got():
    calls: list[tuple] = []

    def old_handler(tool_id, detail=None):
        calls.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=old_handler)
    assert gate.authorize(
        "run_command",
        mode=PolicyMode.OPEN,
        destructive=True,
        detail="rm -rf /tmp/build",
        preview="About to delete 4 files in 1 folder.",
    ) == PermissionStatus.GRANTED
    assert calls == [("run_command", "rm -rf /tmp/build")]


def test_the_session_card_carries_it_too():
    seen: list = []

    def handler(tool_id, detail=None, preview=None):
        seen.append(preview)
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=handler)
    gate.authorize(
        "run_command",
        mode=PolicyMode.OPEN,
        destructive=True,
        detail="rm -rf /tmp/build",
        guards=GuardConfig(destructive_card="session"),
        preview="About to delete 4 files in 1 folder.",
    )
    assert seen == ["About to delete 4 files in 1 folder."]


def test_the_arming_card_is_untouched_by_the_preview():
    # The keyword ceremony's card takes the arming path and nothing else, whatever
    # else is passed (gate.authorize's own ordering rule).
    seen: list = []

    def handler(tool_id, detail=None, arming=None, preview=None):
        seen.append((arming, preview))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=handler)
    gate.authorize(
        "arm_automation",
        mode=PolicyMode.OPEN,
        destructive=True,
        detail="nightly backup",
        arming={"automationName": "Nightly"},
        preview="About to delete 4 files in 1 folder.",
    )
    assert seen == [({"automationName": "Nightly"}, None)]
