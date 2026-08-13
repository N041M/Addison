"""The delete preview (5.6, first form), what a delete would take, computed by
LOOKING, never by running anything.

THE GAP THIS CLOSES (KNOWN-GAPS, "The permission card shows the command, not its
consequences"). A card for ``rm -rf build`` says ``rm -rf build``, which is the
least informative true thing that could be shown. When the command is confidently
a delete with paths this module can name, the card gains ONE extra plain line:
"About to delete 1,240 files in 12 folders. 3 were changed in the last day."

WHAT THIS IS NOT. Nothing is executed, nothing is copied, no sandbox is started
and no command is run twice. The count comes from a bounded directory walk in the
shell (``shell.previewDeletePaths``), the process that has OS permissions, for
the same reason every other filesystem read does: the core has none of its own
(CLAUDE.md §1.3). The copy-on-write clone form named in the same KNOWN-GAPS entry
is deliberately NOT built here and stays open.

=================== THE CLASSIFIER FAILS TOWARDS SILENCE ====================
``delete_targets`` answers None, meaning NO PREVIEW, card unchanged, for
anything it cannot read with confidence: an unknown program, a pipeline, a
substitution, a glob, a variable, a flag it does not know, a target it cannot
resolve. That is the whole safety argument for it existing at all.

A verb list was rejected for REFUSAL decisions (``tools/run_command.py`` records
how the old read-only classifier was defeated three ways) because a classifier
that is wrong in the permissive direction lets something dangerous through. Here
the direction is reversed: the preview is ADVISORY, it can only ever add a line to
a card that already shows the exact command and still requires an approval, and
its absence is the state the app has shipped with all along. So the failure to
avoid is not "no preview", it is a preview that says a WRONG NUMBER, somebody
reading "3 files" over an ``rm -rf`` that would take thirty thousand. Every
uncertain case therefore returns None, and no case guesses.
============================================================================
"""

from __future__ import annotations

import shlex
from pathlib import Path

# The programs whose arguments are, unambiguously, things they delete. Kept
# deliberately tiny: a program is on this list only if every non-flag argument it
# takes is a path it removes.
_DELETE_PROGRAMS = {"rm", "rmdir", "unlink"}

# Flags this module understands well enough to skip. Anything else, including a
# long flag, a bundle it has not seen, or anything that merely looks like a flag,
# means the command is not understood, and an ununderstood command gets no preview.
_KNOWN_FLAGS = {
    "-r", "-R", "-f", "-i", "-v", "-d", "-P", "-rf", "-fr", "-Rf", "-fR",
    "-rv", "-vr", "-rfv", "-rvf", "-fv", "-vf", "-p", "--recursive", "--force",
    "--verbose", "--dir", "--parents",
}

# Anything here means the text is not one plain command, so shlex's tokens are not
# the whole story and nothing may be concluded from them.
_SHELL_OPERATORS = ("|", "&", ";", "<", ">", "`", "$", "\n", "\r", "(", ")", "{", "}")

# Glob characters: the shell expands these, so the tokens are patterns rather than
# paths and the walk would be counting the wrong thing.
_GLOB_CHARS = ("*", "?", "[", "]")

# Prefixes a shell drops before the real program name; the same set the hardline
# denylist reads a command through (``policy.py``). A command hidden behind one of
# these is still a delete, and pretending not to see it would be the permissive
# direction of a mistake.
_TRANSPARENT_PREFIXES = {"sudo", "command", "exec", "nice", "nohup", "time"}

# How many paths one preview may ask about. A command naming more than this is not
# the command this was built for, and the card does better with nothing than with a
# question that takes a visible pause to answer.
MAX_PREVIEW_PATHS = 20


def delete_targets(command: str, *, home: Path | None = None) -> list[str] | None:
    """The absolute paths ``command`` would delete, or None when this cannot be
    said with confidence. None means the card is shown exactly as it is today.

    ``home`` is the directory a command starts in, HOME, matching
    ``shell/src-tauri/src/exec.rs`` (``home_dir``), which is where run_command
    actually runs. It is a parameter so a test can name its own, and so the one
    assumption this module makes about the shell is visible rather than buried.
    """
    if not command or not command.strip():
        return None
    if any(op in command for op in _SHELL_OPERATORS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        # An unbalanced quote: the text is not a command anybody can read.
        return None
    if not tokens:
        return None

    while tokens and tokens[0] in _TRANSPARENT_PREFIXES:
        tokens = tokens[1:]
    if not tokens:
        return None

    program = Path(tokens[0]).name
    if program not in _DELETE_PROGRAMS:
        return None

    base = Path(home) if home is not None else Path.home()
    targets: list[str] = []
    for token in tokens[1:]:
        if token == "--":
            # Everything after it is a path, but "everything after it" is exactly
            # the sort of thing worth not being clever about.
            return None
        if token.startswith("-"):
            if token in _KNOWN_FLAGS:
                continue
            return None
        if any(ch in token for ch in _GLOB_CHARS):
            return None
        resolved = _resolve(token, base)
        if resolved is None:
            return None
        targets.append(resolved)

    if not targets or len(targets) > MAX_PREVIEW_PATHS:
        return None
    return targets


def _resolve(token: str, base: Path) -> str | None:
    """One target, made absolute against the directory a command starts in.

    A relative path is joined to ``base``; a bare ``~`` or ``~/`` prefix expands to
    ``base`` itself, never to the process's own HOME, so the contract that a test
    can name its own home actually holds. A ``~user`` form names somebody else's
    directory, which this cannot verify, so it gets no preview. Nothing here touches
    the filesystem, no ``resolve()``, no ``exists()``, because the core does not
    read the disk (CLAUDE.md §1.3). The shell does the looking."""
    if token == "~":
        return str(base)
    if token.startswith("~/"):
        return str(base / token[2:]) or None
    if token.startswith("~"):
        return None
    path = Path(token)
    if not path.is_absolute():
        path = base / path
    text = str(path)
    return text or None


def describe(counts: dict) -> str | None:
    """The one plain line a card shows under the command, or None for no line.

    Plain language, no jargon, no paths, this string leaves the core for the
    webview, so it carries numbers only."""
    if not isinstance(counts, dict):
        return None
    files = _count(counts.get("files"))
    folders = _count(counts.get("directories"))
    recent = _count(counts.get("modifiedToday"))
    capped = bool(counts.get("capped"))

    if files == 0 and folders == 0:
        # Nothing found to describe. Silence, for the reason the whole module
        # fails towards it.
        return None

    if capped:
        # Said as a floor, never as a total: the walk stopped early, so any exact
        # number would be a smaller one than the truth.
        head = f"About to delete more than {files:,} files"
    elif files == 1:
        head = "About to delete 1 file"
    else:
        head = f"About to delete {files:,} files"

    if folders == 1:
        head += " in 1 folder"
    elif folders > 1:
        head += f" in {folders:,} folders"
    line = head + "."

    if recent == 1:
        line += " 1 of them was changed in the last day."
    elif recent > 1:
        line += f" {recent:,} of them were changed in the last day."
    return line


def _count(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def preview_for_command(
    command: str | None, shell_bridge: object, *, home: Path | None = None
) -> str | None:
    """The card's extra line for ``command``, or None to leave the card alone.

    None whenever anything at all is uncertain: not a delete, no shell to ask
    (the CLI harness has none), or a walk that could not be done. A failure here
    costs a line of text and nothing else, so it is never surfaced as an error."""
    if shell_bridge is None:
        return None
    targets = delete_targets(command or "", home=home)
    if not targets:
        return None
    walker = getattr(shell_bridge, "preview_delete_paths", None)
    if not callable(walker):
        return None
    try:
        counts = walker(targets)
    except Exception:
        return None
    return describe(counts if isinstance(counts, dict) else {})
