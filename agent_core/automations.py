"""Automations — what Addison AUTHORS for the OS to run (step 8, phase 1).

================================ SAFETY FRAME ================================
**Addison authors; the OS runs; Addison never triggers itself.** That is GLOBAL
FLOOR G2 (docs/SAFETY.md owns it), and nothing in this step — in any phase — gives
the app a timer, a watcher, a scheduler or a callback of its own. This module is
declarative: a dataclass mirroring the ``automations`` table and pure functions over
a row. It starts nothing and reaches nothing.

**Phase 1 ships this doing nothing, which is the point.** There is no authoring tool
(phase 2) and no arming surface (phase 3), so the table stays empty except by hand.
What exists is the row shape, the closed schedule vocabulary, and the
``automation.list`` / ``automation.remove`` RPC in ``agent_core/rpc/automations.py``.
[docs/step-8-automation-plan.md](../docs/step-8-automation-plan.md) owns the phase
order.

**Nothing here records whether an automation is ARMED** — no field, no property, no
derived answer — because armed truth lives in the OS (plan §5.6). The surface asks
launchd what is installed when it loads; a stored flag is what a one-action G3
restore would put back, and a restore can never perform the keyword ceremony that
arming requires. So the honest answer after a restore, a reinstall, or somebody
deleting the plist by hand is the same answer, and it comes from the OS.

Like ``policy.py`` and ``skills.py`` this is a standalone declarative module: it must
NOT import from ``agent_core.tools`` / ``providers`` / ``routines``, and it holds no
I/O of any kind.
=============================================================================
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeGuard
from xml.sax.saxutils import escape as _xml_escape

#: The CLOSED schedule vocabulary (plan §5.4a). Two kinds, both renderable in one
#: plain sentence and both mapping 1:1 onto launchd — ``interval`` onto
#: ``StartInterval``, ``calendar`` onto ``StartCalendarInterval``. No cron: on macOS
#: it is a legacy shim, and a second mechanism is a second set of edge cases to hold.
#: Mirrored by schema.sql's CHECK constraint, which is the enforcement — widening
#: this tuple alone changes nothing a database will accept.
SCHEDULE_KINDS: tuple[str, ...] = ("interval", "calendar")

#: The closed FIELD set of each kind. Every value is an integer, deliberately: a
#: schedule is numbers, so nothing a person or a hand-edited row could put in
#: ``schedule_json`` can carry prose out to a surface through this door.
#:
#:   * ``interval``  -> ``minutes`` (>= 1)
#:   * ``calendar``  -> ``hour`` (0-23), ``minute`` (0-59), ``weekday`` (0-6, optional)
#:
#: The BOUNDS are phase 2's business, where a value is first authored and can be
#: refused with a plain sentence. This module's job is the projection below.
SCHEDULE_FIELDS: dict[str, tuple[str, ...]] = {
    "interval": ("minutes",),
    "calendar": ("hour", "minute", "weekday"),
}


@dataclass
class Automation:
    """One automation — mirrors the ``automations`` table 1:1 (schema.sql).

    Note what is NOT here, and read the schema comment before adding it: there is no
    ``armed`` field. This dataclass is the record of what WOULD run, never a claim
    about what the OS is currently running."""

    id: str
    name: str
    label: str
    command: str
    schedule_kind: str
    schedule_json: str
    created_in_mode: str
    created_at: int
    updated_at: int


def schedule_fields(schedule_kind: object, schedule_json: object) -> dict[str, int]:
    """The stored schedule as the closed fields of its kind — or ``{}``.

    A PROJECTION, not a parse: only the field names ``SCHEDULE_FIELDS`` declares for
    this kind survive, and only as integers. Two things follow, and both are the
    reason this is a function rather than a ``json.loads`` at the call site:

      * a row whose JSON grew an extra key — by a hand edit, by an older build, by a
        payload restored from a sidecar — cannot push that key onto a surface. The
        wire shape is the vocabulary, always, whatever the column holds.
      * nothing raises. This is read on the ``automation.list`` path, and a single
        malformed row must not be able to make the whole list unanswerable; an empty
        dict is the honest "this row does not say" and renders as one.

    ``bool`` is excluded explicitly because it is an ``int`` in Python and ``True``
    is not a number of minutes."""
    allowed = SCHEDULE_FIELDS.get(schedule_kind, ()) if isinstance(schedule_kind, str) else ()
    if not allowed or not isinstance(schedule_json, str):
        return {}
    try:
        parsed = json.loads(schedule_json)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        field: parsed[field]
        for field in allowed
        if isinstance(parsed.get(field), int) and not isinstance(parsed.get(field), bool)
    }


#: Weekday names for ``schedule_sentence``, indexed by the stored ``weekday``
#: value. **0 IS SUNDAY**, because that is launchd's own convention
#: (``StartCalendarInterval``'s ``Weekday`` takes 0–7 with both 0 and 7 meaning
#: Sunday) and the stored value maps onto the plist 1:1 — a friendlier-looking
#: 0=Monday here would need a translation at exactly the seam where an
#: off-by-one arms a job on the wrong day.
WEEKDAY_NAMES: tuple[str, ...] = (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
)


def schedule_sentence(schedule_kind: object, fields: dict[str, int]) -> str:
    """The schedule as ONE plain sentence — "Every 30 minutes", "Every Monday at
    7:30" — or "No schedule saved yet." for anything this vocabulary does not
    recognise (junk kind, missing fields, out-of-range values).

    Takes the PROJECTED fields (``schedule_fields``' output), not the raw column,
    so every caller renders the same closed vocabulary the wire carries. Times are
    24-hour ("18:00", "7:30") — an am/pm guess is exactly the ambiguity a
    scheduled job cannot afford, and the minute is always two digits. Bounds are
    checked here as well as at the authoring door, because this function also
    renders rows that predate the door (hand-edits, restores): a sentence claiming
    "Every day at 99:00" would launder a broken row into confident prose."""
    if schedule_kind == "interval":
        minutes = fields.get("minutes")
        if not isinstance(minutes, int) or minutes < 1:
            return "No schedule saved yet."
        if minutes == 1:
            return "Every minute"
        if minutes == 60:
            return "Every hour"
        if minutes % 60 == 0:
            return f"Every {minutes // 60} hours"
        return f"Every {minutes} minutes"
    if schedule_kind == "calendar":
        hour, minute = fields.get("hour"), fields.get("minute")
        if not isinstance(hour, int) or not isinstance(minute, int):
            return "No schedule saved yet."
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "No schedule saved yet."
        time_text = f"{hour}:{minute:02d}"
        weekday = fields.get("weekday")
        if weekday is None:
            return f"Every day at {time_text}"
        if isinstance(weekday, int) and 0 <= weekday <= 6:
            return f"Every {WEEKDAY_NAMES[weekday]} at {time_text}"
        return "No schedule saved yet."
    return "No schedule saved yet."


def plist_text(automation: Automation) -> str:
    """The launchd plist this automation WOULD arm — as preview text, byte-stable.

    **A PREVIEW, not the artifact.** Phase 3's arming surface lives in the Rust
    shell, which builds its own XML from typed fields and never accepts a
    document from the core (plan §5.8) — so nothing may ever pass this string
    across the bridge, and a test pins that the rpc layer cannot. What this is
    for: the person reads EXACTLY what will be handed to the OS before they arm
    it, in chat and on the card, and byte-stability is what makes "the preview
    you approved" a meaningful phrase. When phase 3 lands, a lockstep test
    compares this output against the shell's builder the same way the fence's
    two lists are compared today.

    Two properties that are load-bearing:
      * **No ``RunAtLoad`` key, ever** (plan §5.7) — arming must never cause an
        immediate run, so the key is absent rather than false, and a test pins
        its absence by name.
      * Command and label cross ``xml.sax.saxutils.escape`` — a command
        containing ``</string>`` is a command, not a document structure.

    The command runs via ``/bin/sh -c``, which is the same contract
    ``run_command`` gives it everywhere else in Addison — one shell dialect,
    not two."""
    schedule = schedule_fields(automation.schedule_kind, automation.schedule_json)
    if automation.schedule_kind == "interval" and "minutes" in schedule:
        trigger = (
            "    <key>StartInterval</key>\n"
            f"    <integer>{schedule['minutes'] * 60}</integer>\n"
        )
    elif automation.schedule_kind == "calendar" and "hour" in schedule and "minute" in schedule:
        entries = [("Hour", schedule["hour"]), ("Minute", schedule["minute"])]
        if "weekday" in schedule:
            entries.append(("Weekday", schedule["weekday"]))
        lines = "".join(
            f"        <key>{name}</key>\n        <integer>{value}</integer>\n"
            for name, value in entries
        )
        trigger = f"    <key>StartCalendarInterval</key>\n    <dict>\n{lines}    </dict>\n"
    else:
        # A row the vocabulary does not recognise previews as a plist with no
        # trigger — launchd would load it and never fire it. Honest: the preview
        # shows exactly the nothing that would be armed, and the authoring door
        # (schedule_problem) exists to keep such a row from being written at all.
        trigger = ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{_xml_escape(automation.label)}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        "        <string>/bin/sh</string>\n"
        "        <string>-c</string>\n"
        f"        <string>{_xml_escape(automation.command)}</string>\n"
        "    </array>\n"
        f"{trigger}"
        "</dict>\n"
        "</plist>\n"
    )


# =============================== THE DOOR (phase 2) ==========================
# Everything above renders a row that already exists. What follows is asked BEFORE
# one is written — by ``tools/create_automation.py``, the only authoring surface —
# and both functions are pure, so the sentences can be read in a test without a
# store, a mode or a model. The refusals live here rather than in the tool because
# they are facts about the vocabulary this module owns, and because phase 3's card
# will ask the same questions about a row it is about to arm.
#
# Every sentence below is PLAIN LANGUAGE and says what to do instead (CLAUDE.md's
# house rule): a refusal with no next move is a dead end the model reports back as
# a blocked task.


#: The longest gap ``interval`` may express: seven days, in minutes. Past this an
#: interval is a calendar job wearing a costume — "every 20160 minutes" drifts
#: against the clock, cannot say WHEN it runs, and is what a person means by "every
#: other week". Refusing it here is cheaper than explaining the drift later.
MAX_INTERVAL_MINUTES = 7 * 24 * 60

_NEEDS_A_KIND = (
    "Addison can run something every so many minutes, or at a set time of day. "
    "Pick one of those two."
)
_NEEDS_MINUTES = "Say how many minutes to wait between runs — one or more."
_INTERVAL_TOO_LONG = (
    "That's more than a week between runs, which is really a time of day rather "
    "than a gap. Ask for a time instead — once a day, or once a week on a "
    "particular day."
)
_NEEDS_HOUR = "Give the hour as a whole number from 0 to 23."
_NEEDS_MINUTE = "Give the minutes as a whole number from 0 to 59."
_NEEDS_WEEKDAY = (
    "Give the day as a number from 0 for Sunday to 6 for Saturday, or leave the "
    "day out to run every day."
)


def _is_whole_number(value: object) -> TypeGuard[int]:
    """An ``int`` that is not a ``bool``. Same exclusion, and the same reason, as
    ``schedule_fields``: ``True`` is an ``int`` in Python and is not a number of
    minutes. A ``TypeGuard`` so the comparisons that follow it read as arithmetic
    rather than as casts."""
    return isinstance(value, int) and not isinstance(value, bool)


def schedule_problem(kind: object, fields: dict[str, object]) -> str | None:
    """What is wrong with this schedule, in ONE plain sentence — or None.

    The BOUNDS ``SCHEDULE_FIELDS`` declines to state (see its comment): a value is
    first authored here, which is the one place a person can be told what to say
    instead. ``schedule_sentence`` checks the same bounds when it RENDERS, because
    it also renders rows that predate this door; the two agree by construction —
    every schedule this function accepts renders as a real sentence rather than
    "No schedule saved yet.", and a test pins that.

    **A kind with no bounds is REFUSED, not waved through.** The two arms below are
    written out by name, so a third entry in ``SCHEDULE_KINDS`` — which the database
    would accept the moment its CHECK grew — falls to ``_NEEDS_A_KIND`` and cannot be
    authored until somebody states what a valid value of it is. That is the safe
    direction for a closed vocabulary to fail in, and a test pins it."""
    if kind == "interval":
        minutes = fields.get("minutes")
        if not _is_whole_number(minutes) or minutes < 1:
            return _NEEDS_MINUTES
        if minutes > MAX_INTERVAL_MINUTES:
            return _INTERVAL_TOO_LONG
        return None
    if kind == "calendar":
        hour, minute = fields.get("hour"), fields.get("minute")
        if not _is_whole_number(hour) or not 0 <= hour <= 23:
            return _NEEDS_HOUR
        if not _is_whole_number(minute) or not 0 <= minute <= 59:
            return _NEEDS_MINUTE
        weekday = fields.get("weekday")
        # ABSENT is legal and means every day (the plist simply carries no Weekday);
        # present-but-wrong is not. ``None`` is treated as absent so a caller may
        # pass the whole optional field set without pruning it first.
        if weekday is not None and (not _is_whole_number(weekday) or not 0 <= weekday <= 6):
            return _NEEDS_WEEKDAY
        return None
    return _NEEDS_A_KIND


#: Every label Addison writes starts here. Phase 3's shell surface VALIDATES this
#: prefix before it writes a file (plan §5.8) and will only ever touch
#: ``<label>.plist`` under its own directory, so the prefix is the shape of that
#: promise — not decoration. Reverse-DNS is launchd's own convention for a Label.
LABEL_PREFIX = "com.addison.auto."

#: How much of the name survives into the label. A label becomes a FILE NAME on the
#: person's disk, and one made from a sentence-long name is unreadable in the folder
#: it lands in; 40 characters is comfortably more than a recognisable phrase.
MAX_SLUG_CHARS = 40

#: How many automations may share one name before Addison asks for a different one.
#: Ninety-nine is far past any honest use and stops an unbounded loop dead.
MAX_LABEL_SUFFIX = 99


def _slug(name: object) -> str:
    """The name as label-safe text — lowercase ASCII letters, digits and hyphens —
    or ``""`` when nothing survives.

    ASCII ONLY, and that is a deliberate narrowing rather than an oversight: the
    label becomes a file name (``<label>.plist``), and macOS filesystems fold
    Unicode normalisation forms, so two names that differ only by NFC vs NFD would
    pass the table's UNIQUE constraint as two rows and then collide as one file.
    Accents are FOLDED rather than replaced (``Zálohování`` -> ``zalohovani``) so a
    Czech or French name still reads as itself; a name written entirely in a
    non-Latin script folds to nothing and is refused by the caller with a plain
    sentence. That refusal is the honest v1 answer — inventing a fallback label
    nobody asked for would be untested surface on the one string phase 3 validates."""
    if not isinstance(name, str):
        return ""
    # NFKD splits an accented letter into letter + combining mark; dropping the
    # marks keeps the letter. Done BEFORE the character walk so the mark never
    # becomes a hyphen in the middle of a word.
    decomposed = "".join(
        ch for ch in unicodedata.normalize("NFKD", name.casefold())
        if not unicodedata.combining(ch)
    )
    slug = "".join(
        ch if ("a" <= ch <= "z" or "0" <= ch <= "9") else "-" for ch in decomposed
    )
    while "--" in slug:
        slug = slug.replace("--", "-")
    # Trim, then cut to length, then trim again: the cut can land on a hyphen.
    return slug.strip("-")[:MAX_SLUG_CHARS].strip("-")


def derive_label(name: object, taken_labels: Iterable[str] = ()) -> str | None:
    """The label to store for ``name``, or None when there isn't one.

    None means one of exactly two things, and the caller tells them apart by asking
    again with no taken labels (``tools/create_automation.py`` does): the name
    produced no usable text at all, or every spelling of it is already in use. Both
    are refusals with their own sentence; neither is an error.

    Uniqueness is enforced by the database (``automations.label`` is UNIQUE), and
    this is what keeps a person from meeting that constraint as a failure: a second
    "Nightly backup" becomes ``…nightly-backup-2``. The suffix goes on the SLUG, so
    every label Addison writes still starts with ``LABEL_PREFIX``."""
    slug = _slug(name)
    if not slug:
        return None
    taken = {str(label) for label in taken_labels}
    base = f"{LABEL_PREFIX}{slug}"
    if base not in taken:
        return base
    for suffix in range(2, MAX_LABEL_SUFFIX + 1):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    return None
