"""guards.* handlers — the Custom profile's two tunable prompting guards (scope
amendment 2026-07-20, §7; D2/D5).

Two settings-backed guards, each a closed vocabulary (``agent_core/policy.py``).
They change ONLY how often Addison asks before acting; they can never touch a
GLOBAL floor (G1–G4). Lowering one (a "weakening") mints the G4 undeletable
anchor FIRST — a permanent restore point of the last setup Addison saw working —
so weakening a safeguard always leaves a guaranteed way back (guards.set, D5).

Storage: two ``app_settings`` keys, captured by snapshots like any other config
(``scope.py`` already captures ``app_settings``). The guards are EFFECTIVE only
while the active profile is Custom; Simple/Developer use the fixed defaults,
byte-for-byte unchanged. ``_effective_guards`` is the ONE resolution function the
live loop, the routine engine and the widget rail all read (D3), so no path can
drift its own idea of the posture.

This module is the sole camelCase mapper for its namespace, at the wire boundary
— the settings and ``GuardConfig`` are snake_case; the wire keys are camelCase,
and their VALUES are the snake-case slugs the vocabularies define.
"""

from __future__ import annotations

from agent_core.policy import (
    AUTO_GRANT_SCOPE_VALUES,
    DEFAULT_AUTO_GRANT_SCOPE,
    DEFAULT_DESTRUCTIVE_CARD,
    DESTRUCTIVE_CARD_VALUES,
    GuardConfig,
    weakenings_between,
)
from agent_core.profiles import ProfileId
from agent_core.rpc.base import ServerContext

# Settings keys (D2). Plain app_settings rows, so a restore rolls them back with
# everything else — they are reversible config, not floors, and are NOT in
# scope._PRESERVED_SETTING_KEYS.
_GUARD_DESTRUCTIVE_CARD_KEY = "guard_destructive_card"
_GUARD_AUTO_GRANT_SCOPE_KEY = "guard_auto_grant_scope"

# Frozen refusals (D5). An unknown value changes nothing; a mint failure changes
# nothing — because weakening WITHOUT a way back is exactly what G4 forbids.
_UNKNOWN_VALUE = "That isn't a setting Addison recognises, so nothing was changed."
_MINT_FAILED = (
    "Addison couldn't save the permanent restore point that goes with lowering a "
    "safeguard, so nothing was changed. Try again in a moment."
)


class GuardsMixin(ServerContext):
    # --- resolution (the one source of truth, D3) --------------------------
    def _stored_guard_config(self) -> GuardConfig:
        """The two guards as persisted in ``app_settings``, each coerced to its
        default on any unknown/missing value. A garbage setting therefore reads as
        the strict default and never silently weakens the gate."""
        card = self.store.get_setting(_GUARD_DESTRUCTIVE_CARD_KEY, DEFAULT_DESTRUCTIVE_CARD)
        scope = self.store.get_setting(_GUARD_AUTO_GRANT_SCOPE_KEY, DEFAULT_AUTO_GRANT_SCOPE)
        return GuardConfig(
            destructive_card=(
                card if card in DESTRUCTIVE_CARD_VALUES else DEFAULT_DESTRUCTIVE_CARD
            ),
            auto_grant_scope=(
                scope if scope in AUTO_GRANT_SCOPE_VALUES else DEFAULT_AUTO_GRANT_SCOPE
            ),
        )

    def _effective_guards(self) -> GuardConfig | None:
        """The GuardConfig in force right now, or ``None`` for the fixed defaults.

        Effective ONLY under the Custom profile; Simple (SAFE) and Developer (OPEN)
        always resolve to ``None`` — which the gate treats as today's behaviour
        byte-for-byte (the freeze). This is the single function the orchestrator and
        routine engine receive as their guards provider and the widget rail calls
        directly, so all three read the same posture (D3)."""
        profile = self._active_profile
        if profile is None or profile.id is not ProfileId.CUSTOM:
            return None
        return self._stored_guard_config()

    # --- RPC (D5) ----------------------------------------------------------
    def _guards_get(self) -> dict:
        """guards.get -> the current two guard values, the defaults, and whether
        they are active (i.e. the active profile is Custom). The frontend keys its
        panel off ``active`` / the active profile, never off the policy mode (D1)."""
        self._ensure_built()
        cfg = self._stored_guard_config()
        active = self._active_profile is not None and self._active_profile.id is ProfileId.CUSTOM
        return {
            "destructiveCard": cfg.destructive_card,
            "autoGrantScope": cfg.auto_grant_scope,
            "defaults": {
                "destructiveCard": DEFAULT_DESTRUCTIVE_CARD,
                "autoGrantScope": DEFAULT_AUTO_GRANT_SCOPE,
            },
            "active": active,
        }

    def _guards_set(self, params: dict) -> dict:
        """guards.set {destructiveCard?, autoGrantScope?} -> the new effective
        config, or a plain refusal. The flow is D5, in order:

          1. validate against the closed vocabularies — an unknown value refuses
             and NOTHING changes;
          2. compute weakenings vs. the CURRENT stored values;
          3. if anything weakened, mint the G4 anchor FIRST — on failure/None,
             refuse the WHOLE set (nothing persists), so a guard is never lowered
             without a saved way back. ``mint_anchor``'s fingerprint dedupe [R7]
             means one anchor per distinct weakening save, and a crash between mint
             and persist re-mints nothing on retry;
          4. persist both keys and respond.

        Not gated on the active profile: the panel is only shown for Custom, but
        the guards are stored config and validating/anchoring them is correct
        wherever the call originates."""
        self._ensure_built()
        current = self._stored_guard_config()
        card = params.get("destructiveCard", current.destructive_card)
        scope = params.get("autoGrantScope", current.auto_grant_scope)
        if card not in DESTRUCTIVE_CARD_VALUES or scope not in AUTO_GRANT_SCOPE_VALUES:
            return {"ok": False, "error": _UNKNOWN_VALUE}
        new = GuardConfig(destructive_card=card, auto_grant_scope=scope)
        if weakenings_between(current, new):
            try:
                anchor = self.snapshot_manager.mint_anchor(reason="guard_weakened")
            except Exception:
                anchor = None
            if anchor is None:
                return {"ok": False, "error": _MINT_FAILED}
        self.store.set_setting(_GUARD_DESTRUCTIVE_CARD_KEY, card)
        self.store.set_setting(_GUARD_AUTO_GRANT_SCOPE_KEY, scope)
        return {"ok": True, "destructiveCard": card, "autoGrantScope": scope}
