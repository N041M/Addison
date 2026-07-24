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

import os
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


# --- Workspace-trust floor (step 5, D1; global-floor G3) ---------------------
#
# Pure, stdlib-only, store-free — this module never imports the store, so the
# check can be reused by the RPC (grant time), the gate/caller (authorize time),
# and the forward-declared xfail test alike, none of which may reach a live Store.
#
# ``workspace_trust_allows`` answers exactly ONE question: is ``path`` safe to sit
# INSIDE the trust boundary at all — i.e. is it NOT Addison's own data directory
# (or an ancestor/descendant of it)? It is the FLOOR, not the confinement check.
# "Is this path inside a currently-trusted root" is a different predicate the
# caller computes (rpc/workspace.is_trusted), because that one needs the stored
# trust rows and this one must not. The two compose: a path is genuinely trusted
# iff it sits under a granted root AND passes this floor (floor beats a root that
# was somehow planted over the data dir — order: match-a-root THEN floor).


def _derived_data_dir() -> str:
    """The live DB's parent directory, derived the SAME way ``main.default_db_path``
    derives the DB path — env override's parent, else ``~/.addison``. Used only when
    ``workspace_trust_allows`` is called with ``data_dir=None`` (the xfail's one-arg
    convenience); the gate and RPC always pass the live ``server._db_path.parent``.
    A test pins this against ``Path(main.default_db_path()).parent`` so the two can
    never drift."""
    override = os.environ.get("ADDISON_DB_PATH")
    if override:
        return os.path.dirname(os.path.abspath(os.path.expanduser(override)))
    return os.path.expanduser("~/.addison")


def _protected_dirs(data_dir: str | os.PathLike[str] | None) -> list[str]:
    """The directories that may never be, contain, or be contained by a trusted
    workspace: the live data dir + its ``snapshots/`` sidecar, AND ``~/.addison`` +
    its sidecar even when the live store is redirected elsewhere (ADDISON_DB_PATH) —
    the default home store must never be trustable either. Deduplication is left to
    the realpath comparison in ``workspace_trust_allows`` (case/symlink-folded)."""
    bases: list[str] = []
    live = os.path.expanduser(str(data_dir)) if data_dir is not None else _derived_data_dir()
    bases.append(live)
    home = os.path.expanduser("~/.addison")
    if home not in bases:
        bases.append(home)
    protected: list[str] = []
    for base in bases:
        protected.append(base)
        protected.append(os.path.join(base, "snapshots"))
    return protected


def _canonical(path: str | os.PathLike[str]) -> str | None:
    """``realpath`` (resolves symlinks, ``..`` and relative paths against cwd) plus
    a case fold, so comparison is symlink- and case-insensitive-filesystem safe
    (``/tmp/link -> ~/.addison`` and ``~/.Addison`` both normalise onto the real
    data dir). Returns None if the path can't be resolved at all."""
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(str(path)))).casefold()
    except (OSError, ValueError):
        return None


def _within_or_equal(inner: str, outer: str) -> bool:
    """True iff canonical ``inner`` is ``outer`` or sits inside it. ``commonpath``
    on already-canonicalised, case-folded strings — separators are untouched by the
    fold, so component boundaries are respected (``/a/bc`` is NOT inside ``/a/b``)."""
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:
        # Different drives / a mix of absolute and relative — not contained.
        return False


def path_is_within(path: str | os.PathLike[str], ancestor: str | os.PathLike[str]) -> bool:
    """True iff canonical ``path`` equals or sits inside canonical ``ancestor``.
    Symlink- and case-fold-safe, the same comparison ``workspace_trust_allows``
    uses. Used by the confinement check (rpc/workspace.is_trusted) to test a
    resolved path against a stored (already-canonical) trusted root."""
    p = _canonical(path)
    a = _canonical(ancestor)
    if p is None or a is None:
        return False
    return _within_or_equal(p, a)


def workspace_trust_allows(
    path: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None
) -> bool:
    """Return False when ``path`` is, contains, or is contained by any protected
    directory (the data dir, its sidecar, ``~/.addison``); True otherwise. This is
    the floor that keeps Addison's own memory — and the G3 restore storage under it
    — un-trustable, so ``run_command`` inside a trusted parent can never ``rm -rf``
    the floor's own files with no card (§6.6; the forward-declared xfail).

    Refuses BOTH directions: a descendant (``~/.addison/x`` — inside it) and an
    ancestor (``~`` — contains it). Both sides are realpath+casefold canonicalised,
    so a symlink into the data dir and a case-folded spelling are both caught. A
    path that cannot be resolved is refused (fail closed)."""
    candidate = _canonical(path)
    if candidate is None:
        return False
    for protected in _protected_dirs(data_dir):
        prot = _canonical(protected)
        if prot is None:
            continue
        if _within_or_equal(candidate, prot) or _within_or_equal(prot, candidate):
            return False
    return True
