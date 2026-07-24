"""Policy modes — the SAFE / OPEN split (owner decision 2026-07-19), plus the
Custom profile's two tunable prompting guards (scope amendment 2026-07-20, §7).

The safety model is mode-scoped. There are exactly two POLICY MODES, and a mode
is DERIVED from the active §4.7 Profile — there is no separately-persisted "mode"
setting, so the profile stays the single source of truth:

  * Simple profile     -> SAFE mode  (today's behaviour, byte-for-byte):
      the historical global invariants hold — no arbitrary code/shell, every
      non-LOW tool needs a real undo(), routines/widgets are declarative-only,
      and the permission gate prompts for every not-yet-granted tool.

  * Developer profile  -> OPEN mode  ("nearly completely open"):
      real command execution exists (``run_command``), tools without undo() are
      allowed (dev-only), routines/widgets may carry command steps, and the gate
      auto-allows non-destructive actions — prompting ONLY for destructive ones.

  * Custom profile     -> OPEN mode, WITH a guard overlay (D1). Custom is
      Developer's surface — everything Developer allows — but the user chooses how
      often the gate asks first, via two settings-backed guards (``GuardConfig``
      below). A SAFE-derived Custom would have nothing to tune, so Custom derives
      OPEN and the guards only ever MODULATE the OPEN path. The two guards can
      only make the gate ask MORE or LESS often; they can never touch a GLOBAL
      floor (G1–G4). The derived mode reported on the wire stays 'safe' | 'open'
      — never 'custom' (the frontend keys the guard panel off the active PROFILE,
      not the mode).

Two GLOBAL invariants never relax, in any mode (spec §8.3, §6.7):
  1. API keys never reach the webview or SQLite — keychain-only, per-call.
  2. No scheduling / autonomous triggering.

This module holds the mode enum, the profile->mode derivation, and the guard
model (a plain value type + strictness helper; it reads no store and touches no
gate). It must never import from ``agent_core.tools`` (``tools/base.py`` imports
PolicyMode for the ExecutionContext, so the dependency runs one way only).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_core.profiles import Profile, ProfileId


class PolicyMode(str, Enum):
    SAFE = "safe"   # Simple profile — the historical global safety model
    OPEN = "open"   # Developer/Custom profile — real execution, prompts only for destructive


def mode_for_profile(profile: Profile | None) -> PolicyMode:
    """The mode a profile runs under. Developer OR Custom -> OPEN, else SAFE.

    Custom (scope amendment 2026-07-20, D1) derives OPEN so its two prompting
    guards have an OPEN gate to tune; a SAFE-derived Custom would tune nothing.
    A missing/None profile resolves to SAFE — an unknown surface never escalates
    the safety model, mirroring ``resolve_active_profile``'s SIMPLE default."""
    if profile is not None and profile.id in (ProfileId.DEVELOPER, ProfileId.CUSTOM):
        return PolicyMode.OPEN
    return PolicyMode.SAFE


# --- Custom-profile guards (scope amendment 2026-07-20, §7; D2) --------------
#
# Two settings-backed prompting guards, each a CLOSED vocabulary with a total
# strictness order. Defaults ARE today's OPEN behaviour byte-for-byte, so a
# default ``GuardConfig()`` is indistinguishable from the unguarded gate — that
# equivalence is the freeze, and it is what lets Simple/Developer keep passing
# None and behaving exactly as before. "Weakening" is any move to a strictly
# LOWER strictness rank; weakening (and only weakening) mints the G4 anchor.

# Ordered weakest -> strictest, so the tuples double as the wire vocabularies.
DESTRUCTIVE_CARD_VALUES = ("session", "per_invocation")
AUTO_GRANT_SCOPE_VALUES = ("everything", "non_destructive", "none")

# Higher rank = stricter (Addison asks more often).
_DESTRUCTIVE_CARD_RANK = {value: rank for rank, value in enumerate(DESTRUCTIVE_CARD_VALUES)}
_AUTO_GRANT_SCOPE_RANK = {value: rank for rank, value in enumerate(AUTO_GRANT_SCOPE_VALUES)}

# The defaults = today's OPEN gate: destructive cards per invocation, and only
# non-destructive calls auto-grant.
DEFAULT_DESTRUCTIVE_CARD = "per_invocation"
DEFAULT_AUTO_GRANT_SCOPE = "non_destructive"


@dataclass(frozen=True)
class GuardConfig:
    """The two prompting guards in force this turn. ``None`` anywhere the gate
    accepts a ``GuardConfig`` means "the fixed defaults" — which is exactly this
    dataclass with no arguments, i.e. today's OPEN behaviour."""

    destructive_card: str = DEFAULT_DESTRUCTIVE_CARD   # 'per_invocation' > 'session'
    auto_grant_scope: str = DEFAULT_AUTO_GRANT_SCOPE    # 'none' > 'non_destructive' > 'everything'


def weakenings_between(old: GuardConfig, new: GuardConfig) -> list[str]:
    """The guards that got WEAKER moving from ``old`` to ``new`` (each a move to a
    strictly lower strictness rank). Empty when nothing weakened — tightening or
    leaving a guard unchanged never appears here, so it never mints an anchor."""
    weakened: list[str] = []
    if _DESTRUCTIVE_CARD_RANK[new.destructive_card] < _DESTRUCTIVE_CARD_RANK[old.destructive_card]:
        weakened.append("destructive_card")
    if _AUTO_GRANT_SCOPE_RANK[new.auto_grant_scope] < _AUTO_GRANT_SCOPE_RANK[old.auto_grant_scope]:
        weakened.append("auto_grant_scope")
    return weakened
