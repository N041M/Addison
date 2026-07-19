"""Widget specs — DECLARATIVE, validated, NEVER code (engineering-spec §8.1 spirit).

================================ SAFETY FRAME ================================
A widget is a DECLARATIVE JSON spec, validated against a fixed schema at SAVE
time and at RENDER time. There is NO code, no expression, and no templating
anywhere in a widget. Exactly two shapes exist in v1:

  {"kind": "routine", "routineId": "<uuid>",             "title": "..."}
      A saved routine with a Run pill. Running it goes through the EXISTING
      routine.run path — the same ToolRegistry + PermissionGate as the live
      conversation — so a routine widget adds ZERO new execution surface. The
      routine keeps its own permission gates at run time.

  {"kind": "stat",    "source": "<whitelisted-source-id>", "title": "..."}
      Displays a value from a FIXED whitelist of core-computed sources:
      ``tokens_month``, ``provider_latency``, ``connections``. An unknown source
      is rejected at save and hidden at render.

Widget "actions" beyond these two kinds do not exist in v1. There is no eval,
no expression field, no raw-command field — validation below rejects anything
that is not one of the two shapes above, and rejects code-looking ids
defensively. This mirrors the Routine rule (a Routine is a declarative plan,
never a script — §6.1) and is enforced identically here.
=============================================================================
"""

from __future__ import annotations

import re

# The only stat sources a widget may display — each is core-computed and
# read-only (main.py's stats.get). Adding a source here is a deliberate, reviewed
# act, never something a saved spec can introduce.
STAT_SOURCES: tuple[str, ...] = ("tokens_month", "provider_latency", "connections")

WIDGET_KINDS: tuple[str, ...] = ("routine", "stat")

MAX_PINNED = 6          # at most six pinned widget cards (design-brief-fern §3)
MAX_TITLE_LEN = 60      # plain, short titles only

# A routine id is a uuid (or another plain slug). This deliberately rejects
# anything with code-shaped characters — "(", "${", "{", "}", ";", backticks,
# whitespace, "eval" would all fail — so a spec can never smuggle an expression
# in through the routineId field. Source is whitelist-checked (equality), which
# rejects code-looking sources the same way.
_PLAIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_widget_spec(spec) -> str | None:
    """Return None if ``spec`` is a valid v1 widget, else a plain-language reason.

    Called at SAVE time (before persisting) and at RENDER time (widget.list skips
    anything this rejects). A spec is valid ONLY if it is exactly one of the two
    fixed shapes with no extra fields — no code, no expressions, no templates."""
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

    return "That isn't a kind of widget Addison can make."


def widget_summary(spec: dict) -> str:
    """A one-line plain-language description of what a (valid) spec is — shown in
    the proposal card so the user sees exactly what they're adding."""
    kind = spec.get("kind")
    if kind == "routine":
        return "Runs your saved routine with one tap."
    if kind == "stat":
        return {
            "tokens_month": "Shows how many tokens you've used this month.",
            "provider_latency": "Shows how quickly your models are responding.",
            "connections": "Shows which models and services are connected.",
        }.get(spec.get("source"), "Shows a value from Addison.")
    return ""
