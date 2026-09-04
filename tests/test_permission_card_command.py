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

Two more shapes are pinned here because the same field decides them:

  * THE KEYWORD GATE'S CARD, which carries no ``command`` at all. Its per-call
    detail is the automation's NAME, so a card-level command would put a name in the
    block that means "this is the exact command" — the lie the sentence hook exists
    to prevent, arriving by the other door;
  * THE CLI STAND-IN (``_terminal_permission_handler``), which reads the same two
    values out of the same function and so cannot word one call two ways. It had no
    test at all while its docstring claimed exactly that.

``tests/ipc_fixtures.py`` generates the committed artifact the frontend renders
from the same builder, and ``shell/src/__tests__/permissionCardCommand.test.tsx``
is the other half of this.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from agent_core.main import _terminal_permission_handler, build_permission_card
from agent_core.permissions.gate import PermissionStatus
from agent_core.protocol import Method
from agent_core.tools.base import (
    MAX_PERMISSION_DETAIL_CHARS,
    call_permission_detail,
    call_permission_sentence,
)
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import RunCommandTool
from agent_core.tools.write_project_file import WriteProjectFileTool
from tests.conftest import _shutdown, build_server

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


# ---------------------------------------------------------------------------
# The keyword gate's card (step 8 phase 3)
# ---------------------------------------------------------------------------

_ARMING = {
    "automationName": "Tidy up downloads",
    "scheduleSentence": "Every Monday at 7:30",
    "command": "/usr/bin/find /Users/mira/Downloads -mtime +30 -delete",
    "installPath": "~/Library/LaunchAgents/com.addison.auto.tidy-downloads.plist",
    "warnings": ["This will run on its own schedule even when Addison is closed."],
}


def test_an_arming_card_carries_no_card_level_command(tmp_path):
    """THE KEYWORD CARD HAS NO ``command`` FIELD, on the wire.

    ``arm_automation.permission_detail`` answers with the automation's NAME and
    never with a command, deliberately and for its own good reasons — so a
    card-level ``command`` would draw "Tidy up downloads" in the block whose whole
    visual grammar means "this is the exact command". That is the "wants to run:
    notes.txt" lie the sentence hook exists to prevent, arriving by the other door,
    and the expired card renders that block with no arming branch in front of it.

    Asserted on the EMITTED NOTIFICATION rather than on the builder, because the
    fact that keeps this card honest is threaded in by the caller: the card the
    frontend receives is the only thing that settles it.

    Mutation: pass no ``arming`` at the ``build_permission_card`` call site in
    ``_on_permission_request``, or drop the ``not arming`` guard inside it — the
    automation's name arrives as ``params["command"]`` and this fails.
    """
    harness = build_server(tmp_path)
    answers: list[PermissionStatus] = []
    worker = threading.Thread(
        target=lambda: answers.append(
            harness.server._on_permission_request(
                "spy_tool", detail=_ARMING["automationName"], arming=dict(_ARMING)
            )
        ),
        daemon=True,
    )
    worker.start()
    try:
        frame = harness.writer.wait_for(
            lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT
        )
        params = frame["params"]
        assert "command" not in params
        # The command this card IS about, whole, where the ceremony shows it.
        assert params["arming"]["command"] == _ARMING["command"]
        assert params["arming"]["automationName"] == _ARMING["automationName"]
        # The lead sentence still stands alone; the expired card draws
        # ``arming.command`` under it (PermissionCard.tsx).
        assert params["description"] == "This time it wants to run:"

        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": False}}
        )
        worker.join(timeout=5)
        assert answers == [PermissionStatus.DENIED]
    finally:
        _shutdown(harness.reader, harness.thread)


# ---------------------------------------------------------------------------
# The CLI stand-in
# ---------------------------------------------------------------------------


def _terminal_lines(capsys) -> list[str]:
    """What the stand-in printed, one stripped line per line, blanks dropped."""
    return [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]


def _answer_no(monkeypatch) -> None:
    """The terminal's answer, scripted. "n" so nothing can run: this file is about
    what the person READ before answering, and the safe answer is the one that
    leaves no side effect behind if the plumbing under it ever changes."""
    monkeypatch.setattr("builtins.input", lambda *_: "n")


def test_the_terminal_stand_in_prints_the_command_on_its_own_line(capsys, monkeypatch):
    """The CLI's permission prompt, which had no test at all.

    Its docstring claims the CLI and the card "cannot word one call two ways"
    because both read the same two values out of ``_card_consequence``. That claim
    is worth exactly what is asserted about it, so: the lead sentence on its line,
    the command WHOLE on the next, the delete preview on a third. Same promise the
    card makes, in the only formatting a terminal has.

    Mutation: fold the command back into the sentence line
    (``print(f"  {description} {command}")``) — three printed lines instead of four,
    and this fails.
    """
    registry = ToolRegistry()
    registry.register(RunCommandTool(), dev_only=True)
    _answer_no(monkeypatch)

    status = _terminal_permission_handler(registry)(
        "run_command",
        detail="rm -rf ~/Documents/Archive",
        preview="About to delete 4 files in 1 folder.",
    )

    assert status is PermissionStatus.DENIED
    assert _terminal_lines(capsys) == [
        "Addison would like to: Run a command",
        "This time it wants to run:",
        "rm -rf ~/Documents/Archive",
        "About to delete 4 files in 1 folder.",
    ]


def test_the_terminal_stand_in_prints_no_command_line_for_a_worded_sentence(capsys, monkeypatch):
    """The file tools' shape, on the same surface: the sentence and nothing else.

    A file name on a line of its own, under a sentence, is the terminal's version of
    the mono block — so the shape that carries no ``command`` on the card carries no
    second line here either.

    Mutation: return the detail as a command from ``_card_consequence``'s sentence
    branch — a "shopping.txt" line appears and this fails.
    """
    tool = WriteProjectFileTool()
    registry = ToolRegistry()
    registry.register(tool)
    _answer_no(monkeypatch)

    sentence = call_permission_sentence(tool, "shopping.txt")
    assert sentence is not None and "shopping.txt" in sentence

    _terminal_permission_handler(registry)("write_project_file", detail="shopping.txt")

    lines = _terminal_lines(capsys)
    assert lines == [f"Addison would like to: {tool.definition.label}", sentence]
    assert "shopping.txt" not in lines
