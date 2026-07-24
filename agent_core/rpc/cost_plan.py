"""costPlan.* handlers — the "make it cheaper" control (step 4, contract F3/D4).

One conversationally-initiated action that makes Addison cheaper to run: it adds a
fixed brevity + prefer-cheaper guidance NOTE and switches routing to
``cost_first``. Both halves are CANNED in code — the model authors NONE of the
fields (F3), exactly like the endpoint card carries no model-authored label. The
turn reply never carries an actionable payload; ``costPlan.propose`` returns the
canned plan for a card, and a separate ``costPlan.apply`` applies it on explicit
accept (the widget/routine precedent).

WHY APPLY IS HARDER THAN IT LOOKS (D4). It is "a compound, conversationally
initiated degradation for the at-risk persona, whose only recovery is the restore
point": it turns Addison's answers terse AND changes model selection, in one
click, for a non-technical user. So the snapshot hook here is a DELIBERATE new
class that REFUSES the whole apply if the restore point cannot be minted — unlike
``rpc/routing.set``, where a bare strategy toggle PROCEEDS-with-warning because the
user can simply flip it back. The asymmetry is intentional and noted in both
places. Persistence is ONE atomic ``Store`` commit (skill + setting together, R4)
so a half-applied plan is impossible, and the whole thing is idempotent and skips
entirely when already in effect (R7).
"""

from __future__ import annotations

import time
from uuid import uuid4

from agent_core.providers.router import COST_FIRST
from agent_core.rpc.base import ServerContext

# Single source of truth for the two things this handler drives, imported rather
# than re-typed so they cannot drift: the routing settings key (rpc/routing.py) and
# the sticky capture-failure warning (rpc/snapshots.py — shown on snapshot.list).
from agent_core.rpc.routing import _ROUTING_STRATEGY_KEY
from agent_core.rpc.snapshots import _CAPTURE_FAILED_WARNING
from agent_core.skills import validate_skill

# The canned plan (F3). The NAME is distinctive on purpose so idempotency keys on
# it without colliding with a user-authored skill (R7/D4.6). The instructions steer
# the MODEL toward brevity (fewer tokens); ``cost_first`` steers ROUTING toward
# free/cheaper models. Together they are "make it cheaper".
_COST_SKILL_NAME = "Addison: keep it cheap"
_COST_SKILL_INSTRUCTIONS = (
    "Keep answers short and to the point — a sentence or two when that will do, and "
    "no long preamble or recap. Prefer the simplest approach that answers the "
    "question, and don't reach for a bigger, more expensive model when a smaller one "
    "would do the job just as well."
)

# D5, frozen: the refusal when the restore point that must accompany this change
# could not be saved. Distinct from the sticky warning — this is the answer to THIS
# apply, telling the person nothing changed.
_APPLY_REFUSED = (
    "Addison couldn't save the restore point that goes with this change, so nothing "
    "was changed. Try again in a moment."
)


class CostPlanMixin(ServerContext):
    def _cost_plan_propose(self) -> dict:
        """costPlan.propose -> the CANNED plan for the confirm card (F3). No store
        read, no model input — the fields are constants, so the card can show BOTH
        the skill name and its full instructions text (D5) with nothing derived."""
        return {
            "skillName": _COST_SKILL_NAME,
            "skillInstructions": _COST_SKILL_INSTRUCTIONS,
            "strategy": COST_FIRST,
        }

    def _cost_plan_apply(self, params: dict) -> dict:
        """costPlan.apply {accept} -> {ok, snapshotId?} | {ok:false, error}.

        Order, all enforced server-side (D4):
          1. validate the canned skill FIRST — before any snapshot (mirrors
             skill_update); rejected plans mint no restore point.
          2. strategy is ``cost_first``, hard-set here — the wire never chooses it.
          3. R7: if already in effect (cost_first AND the canned skill exists,
             enabled), skip — no snapshot, no write, no churn.
          4. snapshot FIRST with the deliberate ``make_it_cheaper`` slug, and REFUSE
             the whole apply if it fails (see the module note — the asymmetry with
             routing.set is intentional).
          5. persist the skill AND the setting in ONE atomic Store commit (R4), so a
             half-applied plan is impossible."""
        self._ensure_built()
        if not params.get("accept"):
            # An explicit decline — nothing held, nothing written (widget precedent).
            return {"ok": False, "declined": True}

        # 1. Validate the canned content before anything else. It is fixed and
        #    valid, so this is a belt — but placing it first means a future edit that
        #    broke the constants would refuse cleanly instead of minting a snapshot
        #    for a plan it then couldn't write.
        error = validate_skill(_COST_SKILL_NAME, _COST_SKILL_INSTRUCTIONS)
        if error is not None:
            return {"ok": False, "error": error}

        # 3. Already in effect? Do nothing at all — no snapshot, no write (R7).
        if self._cost_plan_in_effect():
            return {"ok": True, "alreadyInEffect": True}

        # 4. Snapshot FIRST, REFUSE on failure. Captured directly (not via
        #    _snapshot_auto) only so the new restore point's id can ride back on the
        #    reply; the refuse-and-sticky-warning behaviour is otherwise identical to
        #    every other hook (rpc/snapshots._snapshot_auto).
        try:
            snapshot = self.snapshot_manager.capture(trigger="auto", reason="make_it_cheaper")
        except Exception:
            self._snapshot_warning = _CAPTURE_FAILED_WARNING
            return {"ok": False, "error": _APPLY_REFUSED}

        # 5. One atomic commit: the canned skill (upsert by name) AND the routing
        #    strategy. Worker-thread affinity means no race with a live turn.
        self.store.apply_cost_plan(
            skill_id=str(uuid4()),
            skill_name=_COST_SKILL_NAME,
            skill_instructions=_COST_SKILL_INSTRUCTIONS,
            strategy_key=_ROUTING_STRATEGY_KEY,
            strategy_value=COST_FIRST,
            now=int(time.time()),
        )
        return {"ok": True, "snapshotId": snapshot.id}

    def _cost_plan_in_effect(self) -> bool:
        """True when cost_first is already the strategy AND the canned skill exists
        and is enabled — the exact state ``apply`` would produce (R7). Both halves
        must hold: a user who set cost_first by hand but has no note, or who kept the
        note but changed the strategy, is NOT "already cheaper" and apply proceeds."""
        if self.store.get_setting(_ROUTING_STRATEGY_KEY) != COST_FIRST:
            return False
        return any(
            skill["name"] == _COST_SKILL_NAME and skill["enabled"]
            for skill in self.store.list_skills()
        )
