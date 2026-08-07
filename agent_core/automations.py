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
from dataclasses import dataclass

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
