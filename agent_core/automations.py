"""Automations — what Addison AUTHORS for the OS to run (step 8, phase 1).

================================ SAFETY FRAME ================================
**Addison authors; the OS runs; Addison never triggers itself.** That is GLOBAL
FLOOR G2 (docs/SAFETY.md owns it), and nothing in this step — in any phase — gives
the app a timer, a watcher, a scheduler or a callback of its own. This module is
declarative: a dataclass mirroring the ``automations`` table and pure functions over
a row. It starts nothing and reaches nothing.

**Authoring exists; ARMING does not.** ``create_automation`` (phase 2, Developer
only) writes a draft row through this module's pure functions; there is no arming
surface anywhere in the tree, so nothing here has ever been handed to launchd —
that is phase 3. What this module holds is the row shape, the closed schedule
vocabulary, the two renderers (``schedule_sentence``, ``plist_text``) and the
authoring door's validators.
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


#: What both renderers say about a row whose schedule this vocabulary cannot read.
#: Frozen copy — the frontend pins these bytes too (`NO_SCHEDULE_SENTENCE`).
NO_SCHEDULE = "No schedule saved yet."


def schedule_is_readable(schedule_kind: object, fields: dict[str, int]) -> bool:
    """Whether the two renderers can BOTH express this schedule — one definition of
    "readable", asked by the sentence and by the plist alike.

    IT EXISTS BECAUSE THE TWO DISAGREED (found by the phase-2 review, 2026-08-07).
    ``schedule_sentence`` checked bounds and ``plist_text`` checked only PRESENCE, so
    a stored ``{"minutes": 0}`` — or ``{"hour": 99, "minute": 88}`` — rendered
    "No schedule saved yet." in words while the preview beside it showed a
    fully-formed trigger. Two renderings of one row saying different things is the
    worst possible shape for a preview whose entire job is to be what somebody read
    before arming (plan §3): whichever one they believed, the other was there to
    contradict it.

    Bounds live here rather than only at the authoring door because both renderers
    also draw rows that never passed the door — a hand edit, an older build, a
    payload restored from a sidecar. ``schedule_problem`` is the door's own,
    stricter check (it additionally caps an interval at a week, which is a policy
    about what Addison will WRITE, not about what launchd can express — so a
    restored 10-day interval still renders honestly here rather than vanishing)."""
    if schedule_kind == "interval":
        minutes = fields.get("minutes")
        return isinstance(minutes, int) and minutes >= 1
    if schedule_kind == "calendar":
        hour, minute, weekday = fields.get("hour"), fields.get("minute"), fields.get("weekday")
        if not isinstance(hour, int) or not isinstance(minute, int):
            return False
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return False
        # Absent means every day; present means one day and must be a real one.
        return weekday is None or (isinstance(weekday, int) and 0 <= weekday <= 6)
    return False


def schedule_sentence(schedule_kind: object, fields: dict[str, int]) -> str:
    """The schedule as ONE plain sentence — "Every 30 minutes", "Every Monday at
    7:30" — or ``NO_SCHEDULE`` for anything this vocabulary does not recognise
    (junk kind, missing fields, out-of-range values — ``schedule_is_readable``).

    Takes the PROJECTED fields (``schedule_fields``' output), not the raw column,
    so every caller renders the same closed vocabulary the wire carries. Times are
    24-hour ("18:00", "7:30") — an am/pm guess is exactly the ambiguity a
    scheduled job cannot afford, and the minute is always two digits."""
    if not schedule_is_readable(schedule_kind, fields):
        return NO_SCHEDULE
    if schedule_kind == "interval":
        minutes = fields["minutes"]
        if minutes == 1:
            return "Every minute"
        if minutes == 60:
            return "Every hour"
        # Days before hours: "Every 168 hours" is what a week used to read as, and
        # the door accepts exactly that value (MAX_INTERVAL_MINUTES) — so the
        # longest schedule Addison will write was also the least legible thing it
        # could say, for personas 54 and 68. Found by the phase-2 review.
        if minutes % 1440 == 0:
            days = minutes // 1440
            return "Every day" if days == 1 else f"Every {days} days"
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"Every {hours} hours"
        return f"Every {minutes} minutes"
    time_text = f"{fields['hour']}:{fields['minute']:02d}"
    weekday = fields.get("weekday")
    if weekday is None:
        return f"Every day at {time_text}"
    return f"Every {WEEKDAY_NAMES[weekday]} at {time_text}"


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
    if not schedule_is_readable(automation.schedule_kind, schedule):
        # A row the vocabulary cannot read previews as a plist with NO trigger —
        # launchd would load it and never fire it. Honest: the preview shows exactly
        # the nothing that would be armed, and the authoring door
        # (schedule_problem) exists to keep such a row from being written at all.
        #
        # THE PREDICATE IS SHARED WITH THE SENTENCE, and that is the point: this
        # branch used to test PRESENCE while the sentence tested BOUNDS, so a stored
        # `{"minutes": 0}` previewed a real trigger under the words "No schedule
        # saved yet." One row, two renderings, disagreeing (phase-2 review).
        trigger = ""
    elif automation.schedule_kind == "interval":
        trigger = (
            "    <key>StartInterval</key>\n"
            # StartInterval is SECONDS; the stored field is minutes.
            f"    <integer>{schedule['minutes'] * 60}</integer>\n"
        )
    else:
        entries = [("Hour", schedule["hour"]), ("Minute", schedule["minute"])]
        if "weekday" in schedule:
            entries.append(("Weekday", schedule["weekday"]))
        lines = "".join(
            f"        <key>{name}</key>\n        <integer>{value}</integer>\n"
            for name, value in entries
        )
        trigger = f"    <key>StartCalendarInterval</key>\n    <dict>\n{lines}    </dict>\n"
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

#: How much stored TEXT one automation may carry. The mcp phase-1 precedent this
#: door cites as transferring "whole" carries `_MAX_NAME_LENGTH = 60`
#: (`rpc/mcp.py`) and `skills.py` carries 60 / 2000 — only the secret-shape half of
#: that precedent was actually transferred, and the phase-2 review said so.
#:
#: It matters here for the same reason the secret check does: `automations` is
#: snapshot-CAPTURED, so every byte written lands in every later snapshot payload
#: and its plaintext sidecar, permanently. Nothing else bounds it — `is_destructive`
#: is False, so an authoring call is auto-allowed card-free in OPEN, and the
#: `-2..-99` label suffix bounds rows *per name* rather than text per row.
#: The command is the generous one because a real command can be long
#: (`rsync` with several flags and two paths); the name is a label a person reads.
MAX_NAME_CHARS = 80
MAX_COMMAND_CHARS = 2000

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
    # THE SUFFIX MUST FIT INSIDE THE SAME CAP, and it did not (adversarial review,
    # 2026-08-07). ``_slug`` caps the stem at ``MAX_SLUG_CHARS``; appending "-2"
    # then produced a 41-43 character stem, which the SHELL refuses — it validates
    # the label itself and caps at the same 40, deliberately not trusting the core.
    # So a second automation with a long name authored fine, previewed fine, showed
    # its code — and then answered "Addison can only set up and remove automations
    # it named itself" the moment the person typed it. Fail-closed, at the app's
    # single highest-ceremony moment, with a sentence that blamed the wrong thing.
    # Trimming the stem to make room keeps every label the core mints inside the
    # set the shell accepts, which is the property the cross-language tests pin.
    for suffix in range(2, MAX_LABEL_SUFFIX + 1):
        tail = f"-{suffix}"
        stem = f"{slug[: MAX_SLUG_CHARS - len(tail)]}{tail}".strip("-")
        candidate = f"{LABEL_PREFIX}{stem}"
        if candidate not in taken:
            return candidate
    return None


def _in_the_stem_alphabet(ch: str) -> bool:
    """One character of a label's stem, excluding the hyphen — which the first
    character may not be, so it is asked for separately below."""
    return ("a" <= ch <= "z") or ("0" <= ch <= "9")


def label_is_addisons_own(label: object) -> TypeGuard[str]:
    """Is this one of the labels Addison MINTS — ``com.addison.auto.<stem>``?

    ``^com\\.addison\\.auto\\.[a-z0-9][a-z0-9-]{0,39}$``, which is
    ``shell/src-tauri/src/automation.rs::label_is_valid`` said again on this side.
    The repetition is the design (plan §5.8): the shell validates every label it is
    handed because it is the process that WRITES the file, and it does not trust the
    core for it. This asks the same question one process earlier, so the core can
    refuse a label it should never have sent — with its own plain sentence, before a
    round trip — instead of relaying somebody else's.

    **Its one caller is a STOPPING path.** ``rpc/automations.py``'s orphan disarm
    (the only place a label arrives from a surface rather than from a row Addison
    wrote), where the question being asked is "is this a job Addison installed, and
    therefore one it may take back out". Nothing here decides that anything may be
    STARTED: ``arm_automation`` names a saved row and takes its label from
    ``derive_label`` above, never from a caller.

    A ``TypeGuard`` rather than a plain ``bool`` because every caller needs the
    narrowed ``str`` immediately afterwards, and re-asserting the type after asking
    the question is how the two drift apart.

    One deliberate difference from the Rust, and it costs nothing: the length is
    counted in CHARACTERS here and in BYTES there. They agree on every string this
    can return True for, because a stem outside ASCII fails the alphabet walk on
    both sides before either length is reached."""
    if not isinstance(label, str) or not label.startswith(LABEL_PREFIX):
        return False
    stem = label[len(LABEL_PREFIX) :]
    if not stem or len(stem) > MAX_SLUG_CHARS:
        return False
    # The first character carries no hyphen, so a label can never start with one and
    # can never be all punctuation — and, with the alphabet below, can never hold a
    # path separator, a dot or a NUL. That is what makes `<label>.plist` a file name
    # the shell cannot be walked out of its own directory with.
    if not _in_the_stem_alphabet(stem[0]):
        return False
    return all(_in_the_stem_alphabet(ch) or ch == "-" for ch in stem[1:])
