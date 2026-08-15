"""One extra card line when a routine sends file text to the web (owner decision
4B, 2026-08-15).

THE ONE EDGE, AND ONLY THIS ONE. When a routine step's resolved arguments contain
text that came out of an EARLIER FILE-READING STEP IN THE SAME RUN, and the step
about to run is NETWORK-BOUND, its permission card carries one extra plain line
naming the flow:

    This step would send text from the file 'notes.txt' to the web.

That is the whole rule. It is NOT general taint tracking, and reading it as such
is the way it becomes a lie. Written out, the things it deliberately does not
catch:

  * a person pasting a file's contents into a routine variable themselves. The
    text never passed through a file-reading step, so nothing here knows where it
    came from;
  * a chain across two routines. Taint is per RUN and dies with the run, so a
    routine that reads a file and a later routine that sends the text are two
    unrelated runs to this module;
  * a transformation that launders the text through a non-file step. A step that
    summarises, translates or re-words the file's output produces a NEW string,
    and the substring test below will not find the original inside it.

WHY THOSE ARE ACCEPTABLE. The line is a prompt for a person reading a card, not a
containment boundary. The containment boundaries are elsewhere and unchanged:
confinement, the denylist, the gate itself. A card line that tried to be
exhaustive would have to guess, and a card that guesses wrong is a card people
learn to click through.

THE 4B BOUNDARY: THE ROUTINE ENGINE ONLY. Live conversation flows are out of
scope. A routine is a SEQUENCE AUTHORED IN ADVANCE, possibly by somebody else,
run with one click and often with nobody watching; that is what makes "this
step, which you did not write today, is about to put your file's text on the
wire" worth a line. In live chat the person is present for every turn and the
same sentence would be noise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# TOOLS THAT PUT TEXT ON THE WIRE. Classified BY TOOL ID and nothing else: the
# routine engine knows tools only through the registry (spec §2 module boundary),
# so importing tool modules here to inspect them is not available and would not be
# wanted even if it were.
#
# THIS TUPLE MUST BE UPDATED WHEN A NETWORK TOOL IS ADDED. A source-level test
# holds that line from the direction it can be held from
# (tests/test_routine_taint.py, the test_live_model_registration idiom): any tool
# module that imports httpx and is not named here fails the suite.
#
#   * ``web_search`` and ``read_web_page`` send a query / a URL over HTTPS;
#   * ``open_link`` hands a URL to the user's browser. No httpx, and the browser
#     is the thing that makes the request, but the URL still leaves the machine,
#     which is the only question this line asks.
#
# ``draft_message`` is deliberately ABSENT. It composes and opens a draft in the
# user's own mail app and stops there; Addison never presses send, and there is no
# send-capable tool in v1. Claiming a draft "would send text to the web" would be
# the wrong-claim failure this module's substring rule exists to avoid.
NETWORK_BOUND_TOOL_IDS = ("web_search", "read_web_page", "open_link")

# Tools whose OUTPUT is the contents of a file. Same classification-by-id rule.
FILE_READING_TOOL_IDS = ("read_file", "read_project_file")

# Below this, an output never taints anything. "ok" or "yes" coming back from a
# file read will appear inside half the queries a person could write, and a line
# that fires on that is a line that is wrong most of the times it appears.
MIN_TAINTING_OUTPUT_CHARS = 16


def _source_name(resolved_args: dict, affected: str | None) -> str:
    """The name to show for the file a step read: THE BASENAME, never the path.

    A permission card is a thing people photograph, screen-share and screenshot,
    and a full path carries the account name and often the whole folder structure
    around it. The basename is the entire piece a person needs in order to answer
    the question the card is asking."""
    if affected:
        return os.path.basename(str(affected)) or str(affected)
    # ``read_file`` is handle-based and has no ``affected_path``; the handle is the
    # only name available, so show its last component and nothing more.
    for key in ("path", "file_handle"):
        value = resolved_args.get(key)
        if isinstance(value, str) and value:
            return os.path.basename(value) or value
    return "you read earlier"


def _strings_in(value: object) -> list[str]:
    """Every string inside a resolved-args structure, nesting included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    return []


@dataclass
class RunTaint:
    """Which of this run's file-read outputs are still recognisable, and from what
    file. Per run, in memory, gone when the run ends."""

    # (output text, file name), in the order the steps produced them.
    sources: list[tuple[str, str]] = field(default_factory=list)

    def record(
        self, tool_id: str, resolved_args: dict, affected: str | None, output: object
    ) -> None:
        """Remember a successful file-read step's output. A no-op for every other
        tool, and for output too short to be evidence of anything."""
        if tool_id not in FILE_READING_TOOL_IDS:
            return
        text = str(output or "").strip()
        if len(text) < MIN_TAINTING_OUTPUT_CHARS:
            return
        self.sources.append((text, _source_name(resolved_args, affected)))

    def line_for(self, tool_id: str, resolved_args: dict) -> str | None:
        """The extra card line for the step about to run, or None.

        THE TRIGGER IS EXACT CONTAINMENT — the file's output appearing verbatim
        inside one of the resolved argument strings. No fuzzy matching, no
        similarity, no token overlap: a card that claims a flow that is not there
        teaches people that cards are noise, and that costs far more than the
        flows a strict test misses. Missing one is a line that never appears;
        inventing one is a control nobody reads any more."""
        if tool_id not in NETWORK_BOUND_TOOL_IDS:
            return None
        haystacks = _strings_in(resolved_args)
        for text, name in self.sources:
            if any(text in haystack for haystack in haystacks):
                return f"This step would send text from the file '{name}' to the web."
        return None
