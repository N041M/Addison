"""The permission card's THREE SHAPES, and which of them carries a command (H9).

The consent card is the control that authorises a destructive action, so what it
puts in front of a person has to be decided in one place and be provable from
outside it. Before 2026-09-04 the exact command was composed into an English
sentence in ``main.py`` and taken apart again in the webview by searching that
sentence for ``run: ``. Three things were wrong with that, and this file pins the
fix of each:

  * a SAFE sentence with those two words in ordinary prose grew a command block it
    had no command for — so a sentence-shaped card must carry NO ``command`` key
    even when there is a detail;
  * the split re-parsed prose the core writes, so rewording the lead sentence
    deleted the command block with nothing failing anywhere — the field is what the
    webview reads now, and the sentence is free to change;
  * the detail was truncated on screen with the rest in a tooltip, although the cap
    exists so the WHOLE of it fits — so ``command`` carries the detail verbatim,
    ellipsis and all when the tool itself had to cut it.

``tests/ipc_fixtures.py`` generates the committed artifact the frontend renders
from the same builder, and ``shell/src/__tests__/permissionCardCommand.test.tsx``
is the other half of this.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_core.main import build_permission_card
from agent_core.tools.base import MAX_PERMISSION_DETAIL_CHARS, call_permission_detail
from agent_core.tools.run_command import RunCommandTool
from agent_core.tools.write_project_file import WriteProjectFileTool

# ipc_fixtures.py sits beside this file but tests/ is not a package — put it on the
# path explicitly, the way test_ipc_fixture_drift.py does. The prose stand-in is
# BORROWED rather than copied: one tool, so the shape the committed fixture pins and
# the shape asserted here cannot drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ipc_fixtures import _ProseSentenceTool as _SentenceTool  # noqa: E402


def test_the_run_shape_carries_the_command_in_its_own_field():
    card = build_permission_card(RunCommandTool(), "rm -rf ~/Documents/Archive")
    assert card["command"] == "rm -rf ~/Documents/Archive"
    # THE DETAIL IS NOT IN THE SENTENCE any more. This is the assertion that fails
    # if anybody re-composes the two, which is what would let the webview go back
    # to parsing prose.
    assert "rm -rf" not in card["description"]
    assert card["description"] == "This time it wants to run:"
    assert card["toolId"] == "run_command"
    assert card["label"] == "Run a command"
    assert card["riskTier"] == "high"


def test_a_tool_that_words_its_own_sentence_carries_no_command():
    """Even though there IS a detail. Presence is decided by the card's SHAPE, never
    by whether a detail existed — the distinction the old prefix search could not
    make, because it only ever saw the finished sentence."""
    card = build_permission_card(_SentenceTool(), "calendar")
    assert "command" not in card
    assert "run: " in card["description"]  # the prose that used to be mis-drawn


def test_the_file_tools_card_carries_no_command():
    """The real tool the sentence hook was added for (2026-08-11): a file name must
    never be shown in the block that means "this is the exact command"."""
    card = build_permission_card(WriteProjectFileTool(), "shopping.txt")
    assert "command" not in card
    assert "shopping.txt" in card["description"]


def test_the_standing_description_card_carries_no_command():
    """A coarse card — no per-call detail at all — is the tool's own description and
    nothing else."""
    tool = WriteProjectFileTool()
    card = build_permission_card(tool)
    assert "command" not in card
    assert card["description"] == tool.definition.description


def test_the_command_is_the_capped_detail_verbatim_and_whole():
    """A full-length detail reaches the card UNCUT.

    ``MAX_PERMISSION_DETAIL_CHARS`` is the cap the tool applies, and it is chosen so
    the whole command can be shown; the card must not shorten it a second time. The
    committed frontend fixture is exactly this length for the same reason."""
    exactly = "e" * MAX_PERMISSION_DETAIL_CHARS
    detail = call_permission_detail(RunCommandTool(), {"command": exactly})
    assert detail == exactly
    assert build_permission_card(RunCommandTool(), detail)["command"] == exactly

    # And a command the TOOL had to cut keeps the tool's own ellipsis, so the
    # ellipsis on screen always means "this command was longer" and never "this
    # card shortened it".
    longer = "e" * (MAX_PERMISSION_DETAIL_CHARS + 40)
    cut = call_permission_detail(RunCommandTool(), {"command": longer})
    assert cut == exactly + "…"
    assert build_permission_card(RunCommandTool(), cut)["command"] == cut


def test_the_preview_rides_beside_the_command_and_neither_is_in_the_other():
    """The delete preview (5.6) is prose ABOUT the command. Both are fields, so
    neither can ever be read as part of the other."""
    card = build_permission_card(
        RunCommandTool(),
        "rm -rf /tmp/build",
        "About to delete 4 files in 1 folder.",
    )
    assert card["command"] == "rm -rf /tmp/build"
    assert card["preview"] == "About to delete 4 files in 1 folder."
    assert card["description"] == "This time it wants to run:"


def test_absent_keys_are_absent_and_never_null():
    """The webview asks ``request.command &&``, so a null would render an empty
    machine-fact block on a card that has no command."""
    card = build_permission_card(WriteProjectFileTool(), "shopping.txt")
    assert set(card) == {"toolId", "label", "description", "riskTier"}
