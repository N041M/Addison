"""Widget specs — DECLARATIVE, validated, mode-scoped (engineering-spec §8.1 spirit).

================================ SAFETY FRAME ================================
A widget is a DECLARATIVE JSON spec, validated against a fixed schema at SAVE
time and at RENDER time, ALWAYS against the current policy mode (policy.py).

THE VOCABULARY IS A CLOSED SET OF KINDS, hard-coded in this file (owner decision
2026-08-06). There is no capability-declaration lattice and no
``required_capabilities`` column: a closed vocabulary IS the capability gate SAFE
invariant 4 asks for, because every kind's whole behaviour is written here, in
the tree, where it can be read and tested. A spec that declared its own
capabilities would be asking the saved data what it is allowed to do.

SAFE mode (Simple profile) — five shapes, NEVER code. Two launchers:

  {"kind": "routine", "routineId": "<uuid>",             "title": "..."}
      A saved routine with a Run pill. Running it goes through the EXISTING
      routine.run path — the same ToolRegistry + PermissionGate as the live
      conversation — so a routine widget adds ZERO new execution surface. The
      routine keeps its own permission gates at run time.

  {"kind": "stat",    "source": "<whitelisted-source-id>", "title": "..."}
      Displays a value from a FIXED whitelist of core-computed sources:
      ``tokens_month``, ``provider_latency``, ``connections``. An unknown source
      is rejected at save and hidden at render.

...and three INTERACTIVE kinds (Phase-2 step 6, half A). Each of these invokes
NO tool and adds ZERO execution surface — that is the whole reason they are
SAFE-legal, and it is the property to check before adding a sixth kind:

  {"kind": "checklist", "items": ["Buy milk", "Call Ana"], "title": "..."}
  {"kind": "note",      "text": "<initial text>",          "title": "..."}
  {"kind": "timer",     "seconds": 300,                    "title": "..."}

OPEN mode (Developer profile) — adds ONE more shape, owner decision 2026-07-19:

  {"kind": "command", "command": "<shell command>",      "title": "..."}
      Runs the command via the run_command dev-only tool on click — the SAME
      registry + gate as a live command, so it still hits the destructive-prompt
      rule. Valid ONLY in OPEN mode; rejected at save and hidden at render in
      SAFE mode. Command widgets are stored with created_in_mode='open' and never
      surface while the Simple profile is active.

SPEC vs STATE. The three interactive kinds are the first widgets with mutable
state — a ticked box, an edited note, a paused timer. The spec stays IMMUTABLE
(it is what was DECLARED, and it is re-validated at every render); what the
PERSON has since done lives in the separate ``widget_state`` table, validated
here by ``validate_widget_state`` against the SAME closed vocabulary. See
schema.sql for why the two are not one column.

Beyond these there is no eval, no expression field, no template field — validation
below rejects anything else and rejects code-looking ids defensively. This mirrors
the Routine rule (a Routine is a declarative plan, never a script — §6.1).
=============================================================================
"""

from __future__ import annotations

import re

from agent_core.policy import PolicyMode

# The only stat sources a widget may display — each is core-computed and
# read-only (main.py's stats.get). Adding a source here is a deliberate, reviewed
# act, never something a saved spec can introduce.
STAT_SOURCES: tuple[str, ...] = ("tokens_month", "provider_latency", "connections")

# "command" is OPEN-mode only; validate_widget_spec gates it on the mode.
WIDGET_KINDS: tuple[str, ...] = (
    "routine", "stat", "checklist", "note", "timer", "command",
)

# The kinds that keep per-widget state (widget_state table). Every other kind
# has none, and widget.setState refuses one — a closed set on this side too, so
# "which widgets are mutable" is never inferred from what a caller sends.
STATEFUL_KINDS: tuple[str, ...] = ("checklist", "note", "timer")

MAX_PINNED = 6          # at most six pinned widget rows in the rail
MAX_TITLE_LEN = 60      # plain, short titles only
MAX_CHECKLIST_ITEMS = 20   # a glance-surface list, not a project tracker
MAX_ITEM_LEN = 100      # one short line per thing to do
MAX_NOTE_LEN = 2000     # a rail note, not a document
MAX_TIMER_SECONDS = 86_400   # 24h — a kitchen timer's worth, and a sane cap

# A routine id is a uuid (or another plain slug). This deliberately rejects
# anything with code-shaped characters — "(", "${", "{", "}", ";", backticks,
# whitespace, "eval" would all fail — so a spec can never smuggle an expression
# in through the routineId field. Source is whitelist-checked (equality), which
# rejects code-looking sources the same way.
_PLAIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_widget_spec(spec, mode: PolicyMode = PolicyMode.SAFE) -> str | None:
    """Return None if ``spec`` is a valid widget for ``mode``, else a plain reason.

    Called at SAVE time (before persisting) and at RENDER time (widget.list skips
    anything this rejects). SAFE mode accepts the five declarative shapes
    (routine, stat, checklist, note, timer); OPEN mode additionally accepts a
    ``command`` widget. The default mode is SAFE so a caller that forgets the
    argument gets the strict (never over-permissive) behaviour."""
    if not isinstance(spec, dict):
        return "That widget isn't in a form Addison can use."

    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        return "A widget needs a short title."
    if len(title) > MAX_TITLE_LEN:
        return "That widget title is too long."

    kind = spec.get("kind")
    if kind == "routine":
        routine_id = spec.get("routineId")
        if not isinstance(routine_id, str) or not routine_id.strip():
            return "That widget is missing the routine it should run."
        if not _PLAIN_ID.match(routine_id):
            return "That routine reference isn't valid."
        extra = set(spec) - {"kind", "routineId", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    if kind == "stat":
        source = spec.get("source")
        if source not in STAT_SOURCES:
            return "That widget shows something Addison doesn't have."
        extra = set(spec) - {"kind", "source", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    if kind == "checklist":
        # ITEMS ARE FIXED AT CREATION, and that is a decision, not an oversight.
        # v1 has no routine step-editing either (spec §6.5/§10 — "delete and
        # recreate via conversation"), and the same reasoning applies here: the
        # spec is the declaration, so an editable item list would mean the
        # SAVE-time validation no longer describes what is on screen. Ticking
        # persists (widget_state); adding or removing a line does not exist.
        items = spec.get("items")
        if not isinstance(items, list) or not items:
            return "A checklist needs at least one thing on it."
        if len(items) > MAX_CHECKLIST_ITEMS:
            return "That's more things than a checklist widget can hold."
        for item in items:
            if not isinstance(item, str) or not item.strip():
                return "Every line of a checklist needs some words."
            if len(item) > MAX_ITEM_LEN:
                return "One of those checklist lines is too long."
        extra = set(spec) - {"kind", "items", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    if kind == "note":
        # The spec's text is the INITIAL text; the current text lives in
        # widget_state. An empty initial note is fine — that is the blank page.
        text = spec.get("text")
        if not isinstance(text, str):
            return "That note is missing its text."
        if len(text) > MAX_NOTE_LEN:
            return "That note is too long for a widget."
        extra = set(spec) - {"kind", "text", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    if kind == "timer":
        # A LENGTH, not a schedule. Nothing in the core counts this down and
        # nothing fires at zero — the frontend does the counting, and G2
        # (Addison never triggers itself) is untouched by this kind existing.
        seconds = spec.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            return "A timer needs a length in seconds."
        if seconds > MAX_TIMER_SECONDS:
            return "That timer is longer than a day."
        extra = set(spec) - {"kind", "seconds", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    if kind == "command":
        # OPEN-mode only — a command widget is a developer ability.
        if mode is not PolicyMode.OPEN:
            return "That kind of widget only works in the Developer profile."
        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            return "That widget is missing the command it should run."
        extra = set(spec) - {"kind", "command", "title"}
        if extra:
            return "That widget has fields Addison doesn't recognize."
        return None

    return "That isn't a kind of widget Addison can make."


def validate_widget_state(spec: dict, state) -> str | None:
    """Return None if ``state`` is a valid state for ``spec``, else a plain reason.

    Asked on BOTH sides of storage, like ``validate_widget_spec``: at WRITE
    (widget.setState, so the frontend's shape is never trusted) and at READ
    (widget.list drops a state this rejects, so the widget renders from its spec
    instead). Nothing here can widen what a widget DOES — the three stateful
    kinds invoke no tool at all — so this is a shape check, not a permission one.

    The spec is the authority for the shape: a checklist's state is exactly as
    long as its spec's items, a timer's remaining seconds cannot exceed its
    spec's length. That is what makes a mismatch un-representable rather than
    merely unlikely."""
    kind = spec.get("kind")
    if kind not in STATEFUL_KINDS:
        return "That widget doesn't keep anything to change."
    if not isinstance(state, dict):
        return "Addison couldn't read that change."

    if kind == "checklist":
        # POSITIONAL, and DISCARDED when the length disagrees with the spec.
        # Items are fixed at creation, so a length mismatch means the pair came
        # apart somewhere the app cannot see (a hand-edited database, a restore
        # of a snapshot that predates the widget). Ticking the wrong row is a
        # worse outcome than ticking nothing, so a mismatch keeps neither.
        checked = state.get("checked")
        if not isinstance(checked, list):
            return "Addison couldn't read that change."
        if len(checked) != len(spec.get("items", [])):
            return "That list has changed since — Addison left it as it was."
        if any(not isinstance(value, bool) for value in checked):
            return "Addison couldn't read that change."
        extra = set(state) - {"checked"}
        if extra:
            return "Addison couldn't read that change."
        return None

    if kind == "note":
        text = state.get("text")
        if not isinstance(text, str):
            return "Addison couldn't read that change."
        if len(text) > MAX_NOTE_LEN:
            return "That note is too long for a widget."
        extra = set(state) - {"text"}
        if extra:
            return "Addison couldn't read that change."
        return None

    # timer — START/DURATION/PAUSED ONLY. `running` + `startedAt` (when it was
    # last started) + `remaining` (seconds left AT that moment) is everything the
    # frontend needs to draw a countdown, and it is deliberately everything the
    # core knows: no scheduler, no alarm, nothing that fires at zero (G2).
    running = state.get("running")
    remaining = state.get("remaining")
    started_at = state.get("startedAt")
    if not isinstance(running, bool):
        return "Addison couldn't read that change."
    if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
        return "Addison couldn't read that change."
    if remaining > spec.get("seconds", 0):
        return "That's longer than the timer was set for."
    # A start time is required exactly when the timer is running, and absent
    # otherwise: a paused timer holding a start time would count down twice.
    if running:
        if not isinstance(started_at, int) or isinstance(started_at, bool) or started_at < 0:
            return "Addison couldn't read that change."
    elif started_at is not None:
        return "Addison couldn't read that change."
    extra = set(state) - {"running", "remaining", "startedAt"}
    if extra:
        return "Addison couldn't read that change."
    return None


def initial_widget_state(spec: dict) -> dict | None:
    """The state a freshly-saved stateful widget starts in, or None for a kind
    that keeps no state. Written by the core, never by the model or the frontend —
    a new checklist starts un-ticked, a note starts at its declared text, and a
    timer starts paused at its full length."""
    kind = spec.get("kind")
    if kind == "checklist":
        return {"checked": [False] * len(spec.get("items", []))}
    if kind == "note":
        return {"text": spec.get("text", "")}
    if kind == "timer":
        return {"running": False, "remaining": spec.get("seconds", 0), "startedAt": None}
    return None


def widget_summary(spec: dict) -> str:
    """A one-line plain-language description of what a (valid) spec is — shown in
    the proposal card so the user sees exactly what they're adding."""
    kind = spec.get("kind")
    if kind == "routine":
        return "Runs your saved routine with one tap."
    if kind == "stat":
        descriptions = {
            "tokens_month": "Shows how many tokens you've used this month.",
            "provider_latency": "Shows how quickly your models are responding.",
            "connections": "Shows which models and services are connected.",
        }
        source = spec.get("source")
        if isinstance(source, str) and source in descriptions:
            return descriptions[source]
        return "Shows a value from Addison."
    if kind == "checklist":
        items = spec.get("items")
        count = len(items) if isinstance(items, list) else 0
        return f"A list of {count} thing{'' if count == 1 else 's'} you can tick off."
    if kind == "note":
        return "A note you can keep on the rail and edit any time."
    if kind == "timer":
        seconds = spec.get("seconds")
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            return f"A {plain_duration(seconds)} timer you start and pause yourself."
        return "A timer you start and pause yourself."
    if kind == "command":
        return "Runs a command on this computer with one tap."
    return ""


def plain_duration(seconds: int) -> str:
    """"5 minutes", "1 hour 30 minutes", "45 seconds" — plain language for the
    proposal card (no jargon, no "300s"; personas 54 and 68)."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'' if hours == 1 else 's'}")
    if minutes:
        parts.append(f"{minutes} minute{'' if minutes == 1 else 's'}")
    if secs and not hours:
        parts.append(f"{secs} second{'' if secs == 1 else 's'}")
    return " ".join(parts) or "0 second"
