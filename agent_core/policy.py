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

import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from enum import Enum

from agent_core.profiles import Profile, ProfileId


class PolicyMode(str, Enum):
    SAFE = "safe"   # Simple profile — the historical global safety model
    OPEN = "open"   # Developer/Custom profile — real execution, prompts only for destructive


class TurnSurface(str, Enum):
    """WHERE a turn came from — the desk, or a message from a paired phone.

    A second axis beside :class:`PolicyMode`, and deliberately not folded into it.
    The mode answers *what is this profile allowed to do*; the surface answers
    *who is in the room*. They are independent: a Developer profile is OPEN at the
    desk and OPEN from a phone, and the phone is narrower for a reason that has
    nothing to do with the profile — nobody is watching the screen, so a permission
    card would block the worker thread forever (``_ask_once`` waits with no
    timeout), and a read-only tool can still put local material on a transport's
    servers. See docs/messaging-channel-plan.md §2(d) and §3.6.

    It lives HERE, beside PolicyMode, because ``tools/registry.py`` and
    ``orchestrator.py`` both need it and ``policy.py`` is the module they already
    share — the same placement argument PolicyMode itself has.

    DESK is the default everywhere, and with it every path is byte-identical to
    what it was before this enum existed: the remote refusal answers None for DESK
    and nothing else in the loop reads the value. REMOTE narrows, and narrowing is
    the only direction it can move (SAFE invariant 3's permitted direction).
    """

    DESK = "desk"       # a person at this computer, watching the answer arrive
    REMOTE = "remote"   # a message from a paired phone, over a messaging channel


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


# Why a trust grant was refused — the two groups of un-trustable directory, so the
# RPC can answer with the sentence that is actually true. One sentence for both was
# the first draft, and it told a person picking ~/Library/LaunchAgents that the
# folder "holds Addison's own memory", which it does not.
TRUST_REFUSAL_PROTECTED = "protected"    # Addison's own storage (the G3 floor)
TRUST_REFUSAL_AUTOMATION = "automation"  # an OS-automation directory (the G2 fence)


def trust_refusal(
    path: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None
) -> str | None:
    """Why ``path`` may not be a trusted workspace — a ``TRUST_REFUSAL_*`` constant —
    or None when it may. The single loop behind ``workspace_trust_allows``; callers
    that only need yes/no use that, the grant RPC uses this so its refusal sentence
    can name the right reason.

    Two groups, for two different reasons:

      * ``_protected_dirs`` — Addison's own storage. Trusting it would put the G3
        recovery floor inside the card-free zone.
      * ``OS_AUTOMATION_DIRS`` — the places where writing a file IS arming
        automation (defined beside ``_CREDENTIAL_DIRS`` below; one list, three
        consumers, step-8 plan §5.5). Trusting one of these would let
        ``write_project_file`` install a launchd job behind an ordinary card,
        which is exactly the arming path G2's keyword gate exists to own.

    PROTECTED WINS when a path offends both (``~`` contains ``~/.addison`` AND
    ``~/Library/LaunchAgents``): the memory sentence was already the answer for
    every such path before the fence existed, and a refusal that changes its
    wording between builds reads like a change of policy. An unresolvable path is
    refused as protected — fail closed, byte-for-byte the pre-fence behaviour."""
    candidate = _canonical(path)
    if candidate is None:
        return TRUST_REFUSAL_PROTECTED
    groups = (
        (TRUST_REFUSAL_PROTECTED, _protected_dirs(data_dir)),
        # ``_automation_roots()`` rather than the expansion spelled again: the
        # floor's own list must have ONE spelling in this module (phase-2 review).
        (TRUST_REFUSAL_AUTOMATION, _automation_roots()),
    )
    for reason, dirs in groups:
        for protected in dirs:
            prot = _canonical(protected)
            if prot is None:
                continue
            if _within_or_equal(candidate, prot) or _within_or_equal(prot, candidate):
                return reason
    return None


def _canonical(path: str | os.PathLike[str]) -> str | None:
    """``realpath`` (resolves symlinks, ``..`` and relative paths against cwd) plus
    a case fold, so comparison is symlink- and case-insensitive-filesystem safe
    (``/tmp/link -> ~/.addison`` and ``~/.Addison`` both normalise onto the real
    data dir). Returns None if the path can't be resolved at all.

    THE FOLD IS UNCONDITIONAL, and that is a macOS assumption stated here rather
    than left to be rediscovered: APFS and HFS+ are case-INSENSITIVE by default, so
    two spellings really are one file and folding is the only correct comparison.
    On a case-SENSITIVE volume (APFS can be formatted that way, and a mounted
    Linux/NFS share usually is) the fold makes this answer YES for paths that are
    genuinely different files — ``/tmp/PROJECT/x`` reads as inside a trusted
    ``/tmp/project``. Note the DIRECTION: it widens what counts as inside a
    protected or trusted root, so the protected-dir refusal errs toward refusing
    and workspace confinement errs toward admitting a sibling of a folder somebody
    trusted. Not fixed here; ``docs/KNOWN-GAPS.md`` carries it as a platform note.
    Anything that would make the fold conditional has to decide per-VOLUME, never
    per-platform, because both kinds of volume mount on the same Mac."""
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
    """Return False when ``path`` is, contains, or is contained by any un-trustable
    directory (``_untrustable_dirs``: the data dir, its sidecar, ``~/.addison``, and
    every entry of ``OS_AUTOMATION_DIRS``); True otherwise. This is the floor that
    keeps Addison's own memory — and the G3 restore storage under it — un-trustable,
    so ``run_command`` inside a trusted parent can never ``rm -rf`` the floor's own
    files with no card (§6.6; the forward-declared xfail), AND the floor that keeps
    launchd/cron/systemd's directories un-trustable, so nothing in the tree can arm
    automation with an ordinary card (G2; step-8 plan §5.5).

    Refuses BOTH directions: a descendant (``~/.addison/x`` — inside it) and an
    ancestor (``~`` — contains it). Both sides are realpath+casefold canonicalised,
    so a symlink into the data dir and a case-folded spelling are both caught. A
    path that cannot be resolved is refused (fail closed).

    THE COST, WRITTEN DOWN: ``~/Library`` and ``~/.config`` can no longer be
    trusted, because each CONTAINS an automation directory — the same both-directions
    rule the data dir already imposes on ``~``. That is the whole point and it is not
    a bug report. It is also not a blanket refusal: a sibling that neither holds nor
    sits inside one (``~/Library/Preferences``) is still trustable, and a fence that
    over-refuses is one people route around."""
    return trust_refusal(path, data_dir) is None


# ===========================================================================
# The hardline denylist (step 5.5, item 3) — paths a CALL may never name.
# ===========================================================================
# Distinct from ``workspace_trust_allows`` above, which answers "may this folder
# be a trusted workspace". This one answers "may this call happen at all", and it
# is checked BEFORE the permission gate: it is not a card the person can approve.
#
# It exists because ``run_command`` has no ``affected_path``, so confinement never
# governs it (tools/base.call_affected_path) and the card is the only layer under
# it. The card is per-invocation and shows the exact command, which is strong —
# but a single layer guarded by human attention is not a floor.
#
# SCOPE THE GUARANTEE HONESTLY. Deciding what an arbitrary ``shell=True`` string
# touches is the game #48 lost three times (``ls\nrm -rf`` defeated ``shlex``;
# bundled and attached short flags defeated flag matching). This is a BACKSTOP
# AGAINST THE OBVIOUS, not a parser. The real boundary is the seatbelt profile's
# ``deny file-write*`` (step 5.5 item 2), which no amount of spelling evades; when
# that lands, this stays as the layer above it.
#
# Two evasions this DID concede and no longer does, because each was one character
# wide and both were live in the shipped build (2026-08-01):
#   * quoting — ``rm -rf ~/.addi"son"`` and ``cat "$HOME"/.ssh/id_rsa``. Quote
#     characters are now removed from a token before it is resolved, exactly as
#     the shell removes them.
#   * globbing — ``rm -rf ~/.addiso*``, ``rm -rf ~/.addi?on``, ``cat ~/.s*h/id_rsa``.
#     A wildcard WIDENS what a token can name, so it must widen the refusal too: a
#     pattern that COULD expand onto a protected path is refused. Anything else
#     makes the denylist trivially bypassable by a person who can type ``*``.
# What is still conceded, precisely: a separator this doesn't split on, command
# substitution and variables other than ``$HOME`` (``cat $(echo ~)/.ssh/id_rsa``),
# and anything the shell computes rather than spells.
_CREDENTIAL_DIRS = ("~/.ssh", "~/.aws", "~/.gnupg")

# Matched on the token's basename, wherever it lives — as a PREFIX, not an exact
# name. ``.env`` is the one file every project keeps its secrets in, it is not
# under a fixed root, and the dominant real-world spellings are the per-stage
# suffixes: ``.env.local``, ``.env.production``, ``.env.development``. An exact
# tuple protected the least-used spelling of the four.
_CREDENTIAL_BASENAMES = (".env",)

# The places where a WRITTEN FILE IS ARMED AUTOMATION (step-8 plan §5.5). launchd
# reads a plist dropped in a LaunchAgents directory and runs it at login and on a
# schedule; cron and systemd read theirs the same way. So a file written here is
# not data — it is a job the OS will run on its own, outside Addison's sandbox,
# with Addison closed and possibly uninstalled. That is one qualitative jump from
# everything else a file tool does, and it is why ONE closed list has THREE
# consumers, each treating it like the group it most resembles:
#
#   * ``_untrustable_dirs`` — un-trustable at grant time, exactly like Addison's
#     own directories (a trusted LaunchAgents folder = arming with no keyword);
#   * ``denylisted_roots`` — refused pre-gate when a command names one, exactly
#     like a credential store;
#   * the seatbelt profile's write-denies (``exec.rs``, the shell's own half).
#
# ``/etc/crontab`` is a FILE and belongs on a list of directories anyway: every
# comparison here is ``commonpath``, which answers "is this equal to or under
# that" without caring what kind of node either is — so naming the file is INSIDE
# and naming ``/etc`` CONTAINS it. ``test_a_file_root_is_handled_in_both_directions``
# pins both.
#
# CLOSED, and deliberately not extensible from a setting, a spec or a tool
# argument: a list that anything else can shorten is not a fence.
OS_AUTOMATION_DIRS = (
    "~/Library/LaunchAgents",
    "~/Library/LaunchDaemons",
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
    "/etc/cron.d",
    "/etc/crontab",
    "/var/spool/cron",
    "/var/at",
    "/usr/lib/cron",
    "/etc/systemd/system",
    "~/.config/systemd",
)

# The programs whose whole job is to ARM automation — hand work to the OS so it
# runs later, on its own. Refused as a command's first word, on every platform and
# in every mode: the seatbelt blocks ``launchctl``'s Mach traffic on macOS, and
# nothing blocks ``crontab`` anywhere else, so this refusal cannot be
# platform-conditional the way the CONTAINS direction is.
_ARMING_BINARIES = ("launchctl", "crontab", "at", "batch")

# Words the shell drops before running what follows, so that ``<prefix> crontab -e``
# runs crontab exactly as the bare spelling does. Stepping over these is the
# difference between refusing "the obvious" and refusing "the obvious, unless you
# type one more word" (see ``command_arms_automation``). Deliberately NOT here:
# ``xargs``, ``sh``, ``find -exec`` and friends, which run a DIFFERENT program that
# then runs the binary — a distinction this backstop does not chase.
#
# ``batch`` is on the ARMING list above for the same reason ``at`` is: it is at(1)'s
# companion, queuing a job for the system to run when it feels like it.
_TRANSPARENT_PREFIXES = ("sudo", "doas", "exec", "command", "builtin", "env", "nohup", "time")

# Shell separators. Splitting on these is NOT parsing — it only widens the set of
# tokens examined, so a missed separator can only fail open (which the docstring
# above already concedes), never wrongly refuse.
#
# ``{`` and ``}`` are deliberately NOT separators: splitting on them tears
# ``${HOME}/.addison`` into three pieces and the middle one resolves nowhere, which
# is a hole rather than the extra coverage the other separators buy.
_TOKEN_SPLIT = re.compile(r"[\s;|&()<>]+")

# Segment boundaries — where a NEW COMMAND starts, which is a narrower question
# than ``_TOKEN_SPLIT``'s. Two deliberate differences from it:
#
#   * no ``\s``: a segment's FIRST word is the program it runs, and splitting on
#     spaces would make every word a first word — ``man crontab`` and ``echo
#     launchctl`` would be refused for talking about a program rather than running
#     one. Newlines DO separate segments (``ls\ncrontab -`` is two commands, #48's
#     vector), so they are spelled out.
#   * no ``<`` / ``>``: those are REDIRECTS, and the word after one is a filename,
#     never a program. Treating them as segment starts refused ``echo x >
#     ./out/at`` as if it were running ``at``. The path checks still see that
#     token — ``_TOKEN_SPLIT`` does split on redirects — so nothing is lost.
_SEGMENT_SPLIT = re.compile(r"[;|&()\n\r]+")

# Characters that make a token an explicit reference to a directory rather than a
# bare word. Only these (plus the literal ``.``/``..``) are tested for CONTAINING a
# protected directory, so an ordinary argument is never resolved against the
# command's cwd and then refused for being somewhere under home.
_PATHISH_PREFIXES = ("~", "$HOME", "${HOME}")


def _automation_roots() -> list[str]:
    """``OS_AUTOMATION_DIRS``, home-expanded. Its own function because the CONTAINS
    direction has to be able to tell this group apart from the rest of
    ``denylisted_roots`` — see ``command_denied_path``."""
    return [os.path.expanduser(d) for d in OS_AUTOMATION_DIRS]


def denylisted_roots(data_dir: str | os.PathLike[str]) -> list[str]:
    """Every directory a call may never reach into: the protected dirs (the data
    dir, its snapshot sidecar, ``~/.addison``), the user's credential stores, and
    the OS-automation directories (``OS_AUTOMATION_DIRS``). The G3 floor's own files
    live in the first group; the second is there because ``cat ~/.ssh/id_rsa`` sends
    a private key to a cloud provider (step 5.5 item 4 redacts what still gets
    through; this stops the direct ask); the third is there because a file written
    into one of them is armed automation, and Addison must not be able to write one
    by spelling it as a command (step-8 plan §5.5).

    THE COST OF THE THIRD GROUP, STATED: ``cat ~/Library/LaunchAgents/x.plist`` is
    now refused, and merely READING a plist is harmless. This string cannot tell a
    read from a write — that is the whole reason this section calls itself a
    backstop against the obvious — and the seatbelt, which can, denies only WRITES
    there. Refusing the read is the price of the coarser layer, and it is the safe
    direction to be wrong in.

    ``data_dir`` IS REQUIRED — no ``None`` default, deliberately. The live data dir
    is the one the running store is open on, and only the server knows it
    (``WorkspaceMixin._data_dir``: *"derived from the running store's path, never a
    re-derivation"*). A convenience default here would silently re-derive it from
    the environment, so a store opened on any other path would be protected in
    name only. That mistake has already been made once inside this same step (see
    BUILD-LOG 07-31, finding 1) — the signature is what stops it recurring."""
    roots = list(_protected_dirs(data_dir))
    roots.extend(os.path.expanduser(d) for d in _CREDENTIAL_DIRS)
    roots.extend(_automation_roots())
    return roots


# Characters a shell removes before it resolves a path, so this must remove them
# too: ``~/.addi"son"``, ``~/'.ssh'/id_rsa`` and ``~/.addi\son`` all name exactly
# what their bare spellings name. Deleting characters can only WIDEN the set of
# tokens tested, so the dequoted form is added ALONGSIDE the raw one, never
# instead of it.
_QUOTE_CHARS = re.compile(r"""['"`\\]""")


def _command_tokens(command: str) -> list[str]:
    """Every substring of ``command`` that might name a path. Over-generates on
    purpose — an extra token costs one comparison, a missed one is a hole."""
    tokens: list[str] = []
    for raw in _TOKEN_SPLIT.split(command):
        if not raw:
            continue
        candidates = [raw]
        if "=" in raw:
            # ``--files0-from=/etc/shadow`` — the path is the right-hand side.
            candidates.append(raw.split("=", 1)[1])
        if raw.startswith("-"):
            # ``-f/etc/passwd`` — an attached value after bundled short flags.
            # ``if positions`` and not ``min(positions) > 0``: the de-dashed token
            # can START with the path character (``-/etc/x`` -> ``/etc/x``), and
            # requiring a non-zero index dropped exactly that candidate. Index 0
            # simply means the whole de-dashed token is the path.
            stripped = raw.lstrip("-")
            positions = [stripped.find(c) for c in "/~$" if c in stripped]
            if positions:
                candidates.append(stripped[min(positions):])
        for candidate in candidates:
            for form in (candidate.strip("'\"`,"), _QUOTE_CHARS.sub("", candidate).strip(",")):
                if form and form not in tokens:
                    tokens.append(form)
    return tokens


def _absolute_form(token: str) -> str:
    """A token as an absolute path STRING — expansions only, no realpath, so a
    token carrying wildcards survives as a pattern. Relative tokens resolve
    against the HOME directory because that is ``run_command``'s cwd; resolving
    them against the Agent Core's own cwd would test a path the command never
    touches."""
    expanded = token
    for name in ("${HOME}", "$HOME"):
        if expanded.startswith(name):
            expanded = os.path.expanduser("~") + expanded[len(name):]
            break
    expanded = os.path.expanduser(expanded)
    if not os.path.isabs(expanded):
        expanded = os.path.join(os.path.expanduser("~"), expanded)
    return expanded


def _resolved_token(token: str) -> str | None:
    """A token as an absolute canonical path (symlinks and ``..`` resolved)."""
    return _canonical(_absolute_form(token))


# --- globbing: a wildcard widens the refusal, it does not narrow it ----------
#
# ``rm -rf ~/.addiso*`` deletes the recovery floor exactly as ``rm -rf ~/.addison``
# does, and until 2026-08-01 the first was allowed because it is not literally the
# second. A pattern names a SET of paths; the honest question is therefore "could
# this expand onto a protected path", and a candidate that could must be refused.
_GLOB_CHARS = "*?["


def _has_glob(token: str) -> bool:
    return any(char in token for char in _GLOB_CHARS)


def _glob_component_matches(pattern: str, name: str) -> bool:
    """One path component against one pattern component, with the shell's own
    leading-dot rule: a wildcard does NOT match a name starting with ``.`` unless
    the pattern spells the dot. Without that rule ``ls *`` would be refused (``*``
    would "match" ``.addison``) while the real shell never touches it — the kind
    of false positive that gets a guard switched off rather than fixed."""
    if name.startswith(".") and not pattern.startswith("."):
        return False
    return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())


def _glob_offends(token: str, root: str) -> str | None:
    """How a WILDCARD token offends against one canonical root, or None.

    Component-wise, because that is what a shell glob is: every component of the
    pattern must be able to match the root's component in the same position.
    Longer-or-equal pattern -> it could name the root itself or something under it
    (INSIDE); shorter -> it could name a folder that HOLDS the root (CONTAINS)."""
    pattern_parts = os.path.normpath(_absolute_form(token)).split(os.sep)
    root_parts = root.split(os.sep)
    for pattern_part, root_part in zip(pattern_parts, root_parts):
        if not _glob_component_matches(pattern_part, root_part):
            return None
    return DENIED_INSIDE if len(pattern_parts) >= len(root_parts) else DENIED_CONTAINS


def _is_credential_basename(basename: str) -> bool:
    """True for ``.env`` and its per-stage spellings (``.env.local``,
    ``.env.production``…), and for a wildcard that could expand onto one
    (``.env*``, ``.en?``). Prefix + glob, never an exact-name tuple: exactly one
    of the four common spellings is the bare ``.env``."""
    name = basename.casefold()
    for known in _CREDENTIAL_BASENAMES:
        if name == known or name.startswith(known + "."):
            return True
        if _has_glob(basename) and _glob_component_matches(basename, known):
            return True
    return False


def _names_a_directory(token: str) -> bool:
    """True when the token explicitly designates a directory, so testing whether it
    CONTAINS a protected dir is meaningful. ``~``, ``/``, ``.`` and ``..`` qualify
    (cwd is home, so ``rm -rf .`` is ``rm -rf ~``); a bare word like ``notes`` does
    not, and must not be — otherwise every ordinary argument resolves to somewhere
    under home and home contains ``~/.addison``."""
    return token in (".", "..") or "/" in token or token.startswith(_PATHISH_PREFIXES)


# The two directions a token can offend in. They are reported separately because
# they are not equally recoverable, and one message for both told the model the
# wrong thing half the time:
#
#   INSIDE   — the token names something in a denylisted place. There is no
#              rephrasing; the answer is that Addison will not go there.
#   CONTAINS — the token names a folder that HOLDS a denylisted place (``~``,
#              ``/``, ``.``). Naming a subfolder works, and saying so turns a
#              dead end into a one-turn correction.
#   ARMING   — the command RUNS one of the programs that hands work to the OS
#              (``crontab``, ``launchctl``…). Not about a path at all, which is
#              why it is a third value rather than a spelling of INSIDE: the
#              sentence the person gets has to say that scheduled automation is
#              not built yet, not that Addison protects its own folders.
DENIED_INSIDE = "inside"
DENIED_CONTAINS = "contains"
DENIED_ARMING = "arming"


def command_arms_automation(command: str) -> str | None:
    """The arming program ``command`` invokes as a segment's first word, or None.

    ``cd /tmp && crontab -`` is refused (``crontab`` opens a segment); ``man
    crontab`` and ``echo launchctl`` are not, because there the word is an argument
    and a guard that refuses talking about a program is one people route around.
    The comparison is on the BASENAME, case-folded and dequoted, so
    ``/usr/bin/crontab``, ``CRONTAB`` and ``"crontab"`` are the same program.

    TRANSPARENT PREFIXES ARE STEPPED OVER (``_TRANSPARENT_PREFIXES``), because ``exec crontab -`` and ``sudo crontab -e``
    do not merely resemble arming — the shell drops the prefix and runs the arming
    binary itself, so they ARE the bare command with a word in front. The earlier
    version conceded these as "a wrapper that puts another word first", which
    lumped them in with genuinely different programs (``xargs crontab`` spawns
    ``xargs``); a reader took that concession to be narrower than it was
    (phase-2 review). Stepping over a KNOWN prefix cannot widen what is refused to
    anything else: the next word is still tested against the same four names, so
    ``sudo ls`` is as allowed as ``ls``.

    A BACKSTOP AGAINST THE OBVIOUS — the same honest scope the rest of this section
    claims, and not one inch more. Still conceded, precisely: a wrapper that runs a
    DIFFERENT program which then runs the binary (``xargs crontab``, ``sh -c
    'crontab -'``, ``find . -exec crontab``), a transparent prefix carrying its own
    FLAGS (``sudo -u me crontab -`` — stepping past those means knowing which flags
    take a value, i.e. the flag-arity parsing that defeated #48 three times, and
    this section stops exactly there), an environment assignment in front
    (``X=1 crontab -``, ``env A=1 crontab -`` — see the loop for why buying these
    cost more than they were worth), shell grouping and keywords
    (``{ crontab -; }``, ``if true; then crontab -; fi``), a wildcard spelling
    (``cronta*`` — unlike a path, a wildcarded PROGRAM name is not something people
    type, and matching it would make a leading ``*`` refuse arbitrary commands), and
    anything the shell computes rather than spells. None of that is the floor:
    ``run_command`` still cards per invocation with the exact text, the seatbelt
    still denies every write into an automation directory, and
    ``workspace_trust_allows`` closes the path that needs no shell at all.

    THE FALSE POSITIVE THIS SHAPE COSTS, since it is a real one and belongs in the
    open: every newline starts a new segment (``ls\\ncrontab -`` is two commands —
    #48's vector), so a line INSIDE a heredoc body is read as a command too. A
    document whose line begins "at last we fixed it", or a Python heredoc with
    ``batch = 5``, is refused. ``at`` and ``batch`` are ordinary English words and
    this is the cost of not writing a shell parser; it is
    [KNOWN-GAPS.md](../docs/KNOWN-GAPS.md)'s to track, with the negative tests in
    ``test_step_5_5_containment.py`` recording it as a decision rather than a
    surprise."""
    for segment in _SEGMENT_SPLIT.split(command):
        for word in segment.split():
            bare = _QUOTE_CHARS.sub("", word)
            program = os.path.basename(bare.rstrip("/")).casefold()
            if program in _ARMING_BINARIES:
                return word
            # Step over a prefix the shell itself drops. Anything else ends this
            # segment, because the word after it is an ARGUMENT and not a program.
            #
            # ONLY EXACT PREFIX WORDS, and the first draft of this loop also stepped
            # over any word CONTAINING ``=`` (meaning to cover ``X=1 crontab -``).
            # That chained: it walked past every ``=``-bearing word until it hit a
            # plain one, so a heredoc line like ``SUMMARY=Nightly batch`` or
            # ``label=Nightly batch job`` — ordinary .env and .properties content —
            # was refused as arming. The adversarial pass over the fix round caught
            # it. Writing config files through a heredoc is everyday Developer work,
            # and this section's own doctrine is that the false positive is what gets
            # a guard switched off rather than fixed. So ``env X=1 crontab -`` joins
            # the conceded list below rather than being bought at that price.
            if program not in _TRANSPARENT_PREFIXES:
                break
    return None


def kernel_confines_writes() -> bool:
    """Will a command's WRITES be stopped by the OS rather than by this string?

    True on macOS only, and that is not a guess. `shell.runCommand` builds a
    seatbelt profile from the live trusted roots and emits the data-dir denies
    after every allow; if `sandbox-exec` is missing or unusable it REFUSES the
    command rather than running it bare (`exec.rs`, "REFUSE, never fall back").
    So on macOS a command either runs confined or does not run. Everywhere else
    `sandbox_invocation` shells out to `/bin/sh` with `sandboxed: false`, and the
    only thing standing between `rm -rf ~` and the recovery floor is this module.

    Deliberately a function, not a constant: it reads as the QUESTION the caller
    is actually asking, and a test can drive both answers without pretending to
    be another operating system."""
    return sys.platform == "darwin"


def command_denied_path(
    command: str,
    data_dir: str | os.PathLike[str],
    *,
    kernel_confined: bool | None = None,
) -> tuple[str, str] | None:
    """The first thing ``command`` names that it may not, and HOW it offends —
    ``(token, DENIED_INSIDE | DENIED_CONTAINS | DENIED_ARMING)`` — or None.

    ARMING IS ANSWERED FIRST, and it is not about a path: a command that RUNS
    ``crontab``/``launchctl``/``at``/``batch`` is refused whatever its arguments
    say, on every platform (``command_arms_automation`` owns that question and its
    concessions). It lives here rather than beside the call sites so all three of
    them — the live loop, the routine engine, the widget rail — inherit it through
    the check they already make.

    Refuses BOTH path directions, the same way ``workspace_trust_allows`` does: a token
    INSIDE a denylisted root (``rm ~/.addison/addison.sqlite3``) and a token that
    CONTAINS one (``rm -rf ~``, which takes the floor with it). The second
    direction is why ``ls ~`` is refused as well as ``rm -rf ~`` — read and write
    are not distinguishable in this string, and the plan's own reasoning is that
    the safe choice is to refuse rather than to keep patching a classifier. The
    cost is real and narrow: naming a subfolder works, and step 5.5 item 2's
    sandbox is what will eventually make the read/write distinction properly.

    **The CONTAINS direction is now RETIRED WHERE THE KERNEL DOES THE JOB**
    (2026-08-06). It was scaffolding, and the condition its own docstring set for
    removal — "once the seatbelt profile denies writes outside the trusted roots,
    ``rm -rf ~`` fails at the kernel while ``ls ~`` succeeds" — has been met and
    hardened. So on a platform where writes are confined, this direction is
    skipped and ``ls ~``, ``ls .``, ``grep -r TODO .`` and
    ``npm run build -- --out .`` are ordinary commands again.

    It is retired BY PLATFORM, not deleted, because the guarantee is by platform:
    see ``kernel_confines_writes``. On anything without a profile the command
    runs bare, and this string is the only thing between ``rm -rf ~`` and the
    recovery floor — so there, it still refuses.

    WHY NOT A CLASSIFIER. The obvious middle road is to keep CONTAINS for
    "write-shaped" commands (``rm``, ``mv``, ``dd``…) and drop it for readers.
    That is precisely what this docstring warned against two paragraphs up —
    read and write are not distinguishable in a ``shell=True`` string, and a verb
    list is wrong in the PERMISSIVE direction the day someone writes ``python -c``
    or a command this list has never heard of. The kernel already makes the
    distinction correctly; a classifier would only be a second, worse copy of it.

    The refusal this removes was never protecting the floor on macOS anyway — it
    was refusing to let the model TRY. What made it worth removing is that a
    control a developer cannot approve past is one they route around (``cd``
    first), and that workaround also defeats the relative-path resolution here,
    so the old behaviour bought less safety than it appeared to.

    THREE SPELLINGS OF ONE PATH, all refused: the literal one, the quoted one
    (quote characters and backslashes are removed first, as the shell removes
    them), and the WILDCARD one — a pattern that could expand onto a protected
    path is refused, because ``rm -rf ~/.addiso*`` destroys the recovery floor as
    surely as naming it does. What is still conceded is listed at the head of this
    section; it is shorter than it was, and it is not empty.

    ``data_dir`` is required for the reason ``denylisted_roots`` gives."""
    arming = command_arms_automation(command)
    if arming is not None:
        return (arming, DENIED_ARMING)
    if kernel_confined is None:
        kernel_confined = kernel_confines_writes()
    roots = [_canonical(root) for root in denylisted_roots(data_dir)]
    roots = [root for root in roots if root is not None]
    # The OS-automation roots are INSIDE-ONLY, and the asymmetry is the point.
    # CONTAINS exists because naming a folder that HOLDS the recovery floor or a
    # credential store destroys it; nothing about naming ``~/Library`` arms
    # anything, and asking CONTAINS of an automation root would refuse ``rm -rf
    # ~/*`` on every platform the kernel does not confine — an ordinary command,
    # and exactly the false positive that gets a guard switched off rather than
    # fixed. What DOES matter about these roots is reaching into one, and that is
    # INSIDE, which is unconditional.
    automation = {c for c in (_canonical(r) for r in _automation_roots()) if c is not None}
    for token in _command_tokens(command):
        if _is_credential_basename(os.path.basename(token.rstrip("/"))):
            return (token, DENIED_INSIDE)
        # CONTAINS is only asked where nothing else will ask it. INSIDE is
        # unconditional — the kernel stops the write, but naming ~/.ssh/id_rsa is
        # a READ the sandbox deliberately permits, so that refusal stays.
        pathish = _names_a_directory(token) and not kernel_confined
        if _has_glob(token):
            # A pattern cannot be realpath'd — resolving it would compare a
            # literal ``*`` against real directory names and always miss — so it
            # is matched component-wise against each root instead. The literal
            # pass below still runs, for a token whose ``[`` is part of a name.
            for root in roots:
                contains = pathish and root not in automation
                direction = _glob_offends(token, root)
                if direction == DENIED_INSIDE or (direction == DENIED_CONTAINS and contains):
                    return (token, direction)
        resolved = _resolved_token(token)
        if resolved is None:
            continue
        for root in roots:
            if _within_or_equal(resolved, root):
                return (token, DENIED_INSIDE)
            if pathish and root not in automation and _within_or_equal(root, resolved):
                return (token, DENIED_CONTAINS)
    return None
