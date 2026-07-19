"""Widget specs — DECLARATIVE, validated, mode-scoped (engineering-spec §8.1 spirit).

================================ SAFETY FRAME ================================
A widget is a DECLARATIVE JSON spec, validated against a fixed schema at SAVE
time and at RENDER time, ALWAYS against the current policy mode (policy.py).

SAFE mode (Simple profile) — exactly two shapes, NEVER code:

  {"kind": "routine", "routineId": "<uuid>",             "title": "..."}
      A saved routine with a Run pill. Running it goes through the EXISTING
      routine.run path — the same ToolRegistry + PermissionGate as the live
      conversation — so a routine widget adds ZERO new execution surface. The
      routine keeps its own permission gates at run time.

  {"kind": "stat",    "source": "<whitelisted-source-id>", "title": "..."}
      Displays a value from a FIXED whitelist of core-computed sources:
      ``tokens_month``, ``provider_latency``, ``connections``. An unknown source
      is rejected at save and hidden at render.

OPEN mode (Developer profile) — adds ONE more shape, owner decision 2026-07-19:

  {"kind": "command", "command": "<shell command>",      "title": "..."}
      Runs the command via the run_command dev-only tool on click — the SAME
      registry + gate as a live command, so it still hits the destructive-prompt
      rule. Valid ONLY in OPEN mode; rejected at save and hidden at render in
      SAFE mode. Command widgets are stored with created_in_mode='open' and never
      surface while the Simple profile is active.

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
WIDGET_KINDS: tuple[str, ...] = ("routine", "stat", "command")

MAX_PINNED = 6          # at most six pinned widget cards (design-brief-fern §3)
MAX_TITLE_LEN = 60      # plain, short titles only

# A routine id is a uuid (or another plain slug). This deliberately rejects
# anything with code-shaped characters — "(", "${", "{", "}", ";", backticks,
# whitespace, "eval" would all fail — so a spec can never smuggle an expression
# in through the routineId field. Source is whitelist-checked (equality), which
# rejects code-looking sources the same way.
_PLAIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_widget_spec(spec, mode: PolicyMode = PolicyMode.SAFE) -> str | None:
    """Return None if ``spec`` is a valid widget for ``mode``, else a plain reason.

    Called at SAVE time (before persisting) and at RENDER time (widget.list skips
    anything this rejects). SAFE mode accepts only the two declarative shapes
    (routine, stat); OPEN mode additionally accepts a ``command`` widget. The
    default mode is SAFE so a caller that forgets the argument gets the strict
    (never over-permissive) behaviour."""
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
    if kind == "command":
        return "Runs a command on this computer with one tap."
    return ""
