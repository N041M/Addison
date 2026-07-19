"""Policy modes — the SAFE / OPEN split (owner decision 2026-07-19).

The safety model is mode-scoped. There are exactly two modes, and a mode is
DERIVED 1:1 from the active §4.7 Profile — there is no separately-persisted
"mode" setting, so the profile stays the single source of truth:

  * Simple profile     -> SAFE mode  (today's behaviour, byte-for-byte):
      the historical global invariants hold — no arbitrary code/shell, every
      non-LOW tool needs a real undo(), routines/widgets are declarative-only,
      and the permission gate prompts for every not-yet-granted tool.

  * Developer profile  -> OPEN mode  ("nearly completely open"):
      real command execution exists (``run_command``), tools without undo() are
      allowed (dev-only), routines/widgets may carry command steps, and the gate
      auto-allows non-destructive actions — prompting ONLY for destructive ones.

Two GLOBAL invariants never relax, in either mode (spec §8.3, §6.7):
  1. API keys never reach the webview or SQLite — keychain-only, per-call.
  2. No scheduling / autonomous triggering.

This module holds ONLY the mode enum and the profile->mode derivation. It must
never import from ``agent_core.tools`` (``tools/base.py`` imports PolicyMode for
the ExecutionContext, so the dependency runs one way only).
"""

from __future__ import annotations

from enum import Enum

from agent_core.profiles import Profile, ProfileId


class PolicyMode(str, Enum):
    SAFE = "safe"   # Simple profile — the historical global safety model
    OPEN = "open"   # Developer profile — real execution, prompts only for destructive


def mode_for_profile(profile: Profile | None) -> PolicyMode:
    """The mode a profile runs under. Developer -> OPEN, everything else -> SAFE.

    A missing/None profile resolves to SAFE — an unknown surface never escalates
    the safety model, mirroring ``resolve_active_profile``'s SIMPLE default."""
    if profile is not None and profile.id is ProfileId.DEVELOPER:
        return PolicyMode.OPEN
    return PolicyMode.SAFE
