"""GLOBAL FLOOR G3 — guaranteed rollback (scope amendment §3, engineering-spec §4.9).

NOT the ``UndoManager`` beside it. ``UndoManager`` reverses ONE tool call
(``action_snapshots``, §4.5); this restores Addison's whole mutable
CONFIGURATION (``config_snapshots``, §4.9). They are complementary, independent,
and never call each other. Verbs here are capture / restore / mint_anchor /
prune — never record / undo_last.

Restore is an RPC path, never a registry tool: a permission gate that could deny
a restore would make "the restore path is itself unbreakable" false.

The single most important property of this module is that RESTORE STILL WORKS
WHEN EVERYTHING ELSE IS BROKEN. That is why it imports stdlib plus this
package's two schema-mirroring leaves and nothing else — no provider, no model
router, no profile, no policy mode, no tool registry, no permission gate; why it
reads no ``app_settings`` row (retention and payload version are module
constants below, so the model cannot write them); and why every payload is
written twice, once into the row and once as a plain JSON file beside the
database.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.snapshots.model import ConfigSnapshot, RestoreResult
from agent_core.snapshots.scope import _CAPTURED_TABLES

PAYLOAD_VERSION = 1

# Closed reason vocabulary. Free text is deliberately not allowed: `reason` is
# written by auto-hooks and, later, by model-orchestrated flows, and a free-text
# column would let model-authored prose into the config store. Unknown slugs
# collapse to "other" rather than raising.
REASONS: dict[str, str] = {
    # step 1, live
    "genesis": "Addison as first installed",
    # The genesis row's honest twin, and the one written whenever we are not
    # CERTAIN this launch created the database. This subsystem's first run
    # against an EXISTING install cannot truthfully call that install's config
    # "as first installed" — see _ensure_genesis.
    "pre_upgrade": "Your setup before this update",
    "turn_verified": "Working setup",
    "pre_restore": "Before restoring",
    "user_request": "You saved this",
    "mode_switch": "Before switching profile",
    "provider_connect": "Before connecting a service",
    "provider_disconnect": "Before disconnecting a service",
    "routine_delete": "Before deleting a routine",
    "widget_delete": "Before deleting a widget",
    "skill_delete": "Before deleting a note",
    "skill_update": "Before changing a note",
    # reserved — declared now so the vocabulary does not churn later
    "guard_weakened": "Before turning a guard off",  # step 2 (G4)
    "make_it_cheaper": "Before switching to cheaper models",  # step 3
    "add_endpoint": "Before adding a service",  # step 4
    "routing_change": "Before changing how models are picked",  # step 3
    "workspace_trust": "Before trusting a project folder",  # step 5
    "mcp_connect": "Before connecting an external tool",  # step 7
    "other": "Before a change",
}

# Retention (amendment §13 Q2, resolved in the step-1 contract §12). Module
# constants, not settings: the restore path must not depend on a readable
# setting, and nothing the model can write may shrink the rollback window.
KEEP_LAST = 50
MAX_AGE_DAYS = 30

_TRIGGERS = ("auto", "on_command")

# Where the rollback walk has got to, written beside the sidecars. Deliberately
# NOT a `.json` name: `recover_payloads_from_disk` and `_sweep_sidecars` both key
# off that suffix, so a `.json` note would be read as a payload by one and then
# deleted as an orphan by the other — losing the very thing that stops the next
# click walking the user forward again.
_WALK_NOTE_FILE = "walk-position"

# User-facing copy. Plain language, no jargon, no stack traces (house rule), and
# frozen in the step-1 contract §11.3 because the frontend tests assert the same
# bytes.
_MISSING = "That restore point isn't here any more."
_UNREADABLE = "That restore point can't be read. Try an earlier one."
_NO_TARGET = "There's no saved working setup to go back to yet."
_ALREADY_THERE = (
    "Your setup already matches your last working setup, so there's nothing to go back to."
)
# Distinct from _ALREADY_THERE on purpose. "Nothing to go back to" and "the
# restore points are there but Addison can't read them" are different situations
# with different next steps, and telling a user the reassuring one while the
# other is true is the shape of failure this floor exists to prevent.
_WALK_UNREADABLE = (
    "Addison couldn't read the setups it saved for you. Try picking one from the "
    "list of restore points."
)
# Reaching the oldest saved setup is a success story, not "there is nothing" —
# the user has walked all the way back and there is genuinely no older step.
# Said ONLY when the restore list agrees; when it does not, see _OLDER_IN_THE_LIST.
_AT_THE_BOTTOM = (
    "You're back at the oldest setup Addison saved, so there's nothing further back to go to."
)
# The other bottom. On an UPGRADED install the permanent bottom row is
# `pre_upgrade` and is deliberately NOT verified (see _ensure_genesis), so the
# walk can never target it — but it is older, it is saved, and it is sitting in
# the restore list the user is looking at. _AT_THE_BOTTOM was being said there,
# and it is simply false: telling somebody the way back does not exist while it
# is on their screen is the shape of failure this floor exists to prevent.
#
# Naming the row instead of targeting it keeps both halves honest. The one-action
# button still refuses to hand back a configuration nothing was ever proven
# against — that refusal is the whole point of _ensure_genesis and must not be
# traded away for a tidier message — while the person is told exactly where the
# older setup is and that choosing it is theirs to do.
# "Your restore points", not "the list below": the manager depends on nothing, and
# it must not start depending on where a React component happens to put a <p>. The
# same sentence has to stay true wherever it is rendered — including beside the
# per-row Restore control step 2 adds, which is not below anything.
_OLDER_IN_THE_LIST = (
    "That's as far back as Addison can go on its own. Your restore points go back "
    'further — the oldest one is "{label}". Addison never saw that one working, so it '
    "won't choose it for you, but you can pick it yourself."
)
_APPLY_FAILED = "Addison couldn't put your settings back. Try an earlier restore point."
_PERMANENT_GUARD = (
    "That restore point is permanent — it was saved when a safety setting was "
    "turned off, so it stays."
)
_PERMANENT_GENESIS = (
    "That's Addison as it was first installed. It stays, so there's always a way back."
)
_PERMANENT_PRE_UPGRADE = (
    "That's how your setup was when this version of Addison first started. It "
    "stays, so there's always a way back."
)
_KEYS_UNTOUCHED = "Your chats and your saved keys weren't touched."
_RESTORED = (
    "Your settings, services, notes, widgets and routines went back to how they were. "
    + _KEYS_UNTOUCHED
)
_RESTORED_GENESIS = (
    "This is Addison as it was first installed, so your services, notes, widgets and "
    "routines are cleared. " + _KEYS_UNTOUCHED
)
_RESTORED_PRE_UPGRADE = (
    "This is how everything was set up when this version of Addison first started. "
    + _KEYS_UNTOUCHED
)
# Said whenever a restore lands on a config no turn ever completed against. The
# normal copy would be a lie there: "I put you back on your last working setup"
# is exactly the sentence the user's trust in this button rests on.
_RESTORED_UNVERIFIED = (
    "Addison couldn't find a setup it had seen working, so it went back to the most "
    "recent settings it had saved instead. Have a look and check things are how you "
    "want them. " + _KEYS_UNTOUCHED
)
# Which sentence describes a restore, keyed by the target's reason. Anything not
# listed is an ordinary restore point and gets the ordinary sentence.
_RESTORED_DETAIL: dict[str, str] = {
    "genesis": _RESTORED_GENESIS,
    "pre_upgrade": _RESTORED_PRE_UPGRADE,
}

# Profile ids spelled out here rather than imported: §6.1 forbids importing
# agent_core.profiles, and this is only ever used to name a profile in a
# sentence — never to resolve one. An id we do not recognise is shown verbatim,
# which is honest and cannot fail.
_PROFILE_NAMES = {"simple": "Simple", "developer": "Developer", "custom": "Custom"}


def _canonical(payload: dict) -> str:
    """The one serialiser. Same bytes into the row and into the sidecar, so the
    two copies are interchangeable and a fingerprint means the same thing on
    both sides."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _fingerprint(tables: dict) -> str:
    """sha256 over ``tables`` ONLY — never the wrapper. ``captured_at`` and
    ``meta`` change on every capture, so including them would make two captures
    of an unchanged config look different, and ``mark_verified_working()`` would
    write a row per turn instead of a row per configuration."""
    return hashlib.sha256(
        json.dumps(tables, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decode_payload(raw: str) -> dict | None:
    """Strict decode: a payload is either fully usable or ``None``.

    Dropping a malformed row and applying the rest would hand the user a
    configuration assembled by the recovery path that was never verified
    working — the precise failure this floor exists to prevent. Strictness is
    only affordable because the fallback is not "nothing": the restore walk is
    unbounded, each candidate can fall back to its sidecar, and genesis is
    permanent at the bottom.

    Missing COLUMNS are tolerated on purpose (contract §6.3): a column added by
    a future build must not invalidate every payload written before it, which
    would silently evaporate a user's whole rollback history at upgrade time.
    SQLite applies the declared default instead."""
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    # An OLDER payload is accepted (and upgraded by a future reader); a NEWER
    # one is refused outright — an older build must never half-apply a newer
    # build's payload.
    if not isinstance(version, int) or isinstance(version, bool) or version > PAYLOAD_VERSION:
        return None
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        return None
    for table, columns in _CAPTURED_TABLES.items():
        rows = tables.get(table)
        if not isinstance(rows, list):
            return None
        allowed = set(columns)
        for row in rows:
            if not isinstance(row, dict):
                return None
            for key, value in row.items():
                if key not in allowed:
                    return None
                if value is not None and not isinstance(value, (int, float, str)):
                    return None
    return payload


def select_payload_to_restore(
    payloads: list[dict], *, current_fingerprint: str | None = None
) -> tuple[dict | None, bool]:
    """Pick the payload a restore should apply, from sidecar payloads alone.

    Returns ``(payload, is_verified)``. Newest-first, preferring rows whose
    ``meta.verified_working`` is truthy and whose ``meta.state_fingerprint``
    differs from ``current_fingerprint``. Falls back to the newest usable
    UNVERIFIED payload only when no verified candidate exists — and the caller
    MUST tell the user when that happened, because "I rebuilt it from your last
    working setup" is then false.

    Store-free and instance-free on purpose: the cold-start path in ``main.py``
    has no Store and no SnapshotManager to construct one with, and this is the
    ONE function every restore path uses to choose a payload — the manager's
    sidecar arm, the RPC cold-start rebuild, and the listing that names the
    target in the confirm step. One function is what makes it impossible for the
    confirm step to name one restore point while the button applies another.

    The unverified fallback exists because "nothing at all" is a worse answer
    than "the most recent settings I had, and I said so". It is never silently
    dressed up as the verified case — that dishonesty is the failure this floor
    was written against."""
    fallback: dict | None = None
    for payload in payloads:
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        fingerprint = meta.get("state_fingerprint")
        if (
            current_fingerprint is not None
            and isinstance(fingerprint, str)
            and fingerprint == current_fingerprint
        ):
            # Applying this would change zero bytes, so it is never a
            # legitimate target however it is labelled.
            continue
        if meta.get("verified_working"):
            return payload, True
        if fallback is None:
            fallback = payload
    return fallback, False


def _payloads_below(
    payloads: list[dict], position: str | None, current_fingerprint: str | None
) -> list[dict]:
    """Only the payloads strictly OLDER than the row the last restore landed on.

    The sidecar arm reads the WHOLE directory, so without this it happily applies
    one of the newer payloads the walk has deliberately stepped past: the user
    presses "go back", gets sent forward into the setup they were escaping, and
    is told the ordinary success sentence while it happens. Filtering the list is
    what lets that arm keep running when the database is the damaged part — the
    one situation it exists for — instead of being switched off to stay safe.

    The position expires exactly the way the database walk's does: it holds only
    while the user is still sitting on the config that row restored, so the moment
    they change anything the walk is over and the whole list is fair game again.

    Two cases deliberately leave the list untouched — a config too damaged to
    fingerprint, and a position naming a payload that is not on disk (its sidecar
    pruned or never written). Neither can be ordered against, and refusing to
    restore at all would strand a user whose database has already failed them.
    Recovery outranks tidiness of the walk."""
    if not position or current_fingerprint is None:
        return payloads
    for index, payload in enumerate(payloads):
        meta = payload.get("meta")
        if not isinstance(meta, dict) or meta.get("id") != position:
            continue
        if meta.get("state_fingerprint") != current_fingerprint:
            return payloads
        return payloads[index + 1 :]
    return payloads


def recover_payloads_from_disk(snapshot_dir: Path) -> list[dict]:
    """Read snapshot payloads straight off disk, with NO database at all.

    The last line of defence for "restore works even from a broken config" when
    the broken thing is the database file itself. Each sidecar is a
    self-describing JSON document, so this needs no schema, no WAL, no sqlite3
    and no other module. Newest first; undecodable files are skipped silently.

    Ordering is ``(captured_at, captured_at_ns)`` DESCENDING, and the nanosecond
    half is not a nicety. ``captured_at`` is whole seconds, and every capture
    made by one user action ties on it — a hook's pre-change snapshot and the
    verified row that follows it land in the same second constantly. With only
    the seconds to sort by, the tie fell to ``sorted(os.listdir())``, i.e. uuid4
    lexical order, so the same directory could answer "your newest saved setup"
    differently on consecutive runs. A restore that is a coin toss is not a
    floor. Payloads written before the stamp existed default to 0 and sort below
    their same-second siblings, which is the safe direction: older."""
    payloads: list[dict] = []
    try:
        names = sorted(os.listdir(snapshot_dir))
    except Exception:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            raw = Path(snapshot_dir, name).read_text(encoding="utf-8")
        except Exception:
            continue
        payload = _decode_payload(raw)
        if payload is not None:
            payloads.append(payload)
    payloads.sort(
        key=lambda p: (_meta_int(p, "captured_at"), _meta_int(p, "captured_at_ns")),
        reverse=True,
    )
    return payloads


def rebuild_rows_from_payloads(store, payloads: list[dict]) -> int:
    """Recreate ``config_snapshots`` rows in a FRESH database from sidecar
    payloads alone, preserving each row's flags. Returns the number written.

    ``meta`` is what survives when the database does not. A rebuild that dropped
    ``undeletable`` would quietly turn every G4 anchor into an ordinary
    deletable snapshot — G4 defeated by G3's own recovery machinery, with no code
    path anywhere called "delete".

    Rows are written OLDEST FIRST, and that is load-bearing rather than tidy.
    ``payloads`` arrives newest-first, and every read path orders by
    ``created_at DESC, rowid DESC`` — so inserting in the order given makes rowid
    ascend while time descends, and the same-second tiebreak then points at the
    OLDEST row instead of the newest. After a cold rebuild that leaves
    ``restore_last_working()`` answering "your setup already matches your last
    working setup" with a table full of distinct proven configs: the floor
    switched off by the recovery path itself."""
    written = 0
    for payload in reversed(payloads):
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        snapshot_id = meta.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            continue
        try:
            if store.get_config_snapshot(snapshot_id) is not None:
                continue
        except Exception:
            pass
        blob = _canonical(payload)
        version = payload.get("version")
        snapshot = ConfigSnapshot(
            id=snapshot_id,
            created_at=_meta_int(payload, "captured_at"),
            trigger=_choice(meta.get("trigger"), _TRIGGERS, "auto"),
            reason=_choice(meta.get("reason"), REASONS, "other"),
            payload_version=version if isinstance(version, int) else PAYLOAD_VERSION,
            state_blob=blob,
            state_fingerprint=str(meta.get("state_fingerprint") or _fingerprint(payload["tables"])),
            verified_working=bool(meta.get("verified_working")),
            undeletable=bool(meta.get("undeletable")),
            captures_binary=bool(meta.get("captures_binary")),
            binary_ref=meta.get("binary_ref") if isinstance(meta.get("binary_ref"), str) else None,
            created_in_mode=_choice(
                meta.get("created_in_mode"), ("safe", "open", "custom"), "safe"
            ),
        )
        try:
            store.insert_config_snapshot(snapshot)
        except Exception:
            continue
        written += 1
    return written


def _meta_int(payload: dict, key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _choice(value: Any, allowed, default: str) -> str:
    """One of a closed set, or the default. Sidecar ``meta`` is data off the
    disk, so nothing read from it is trusted to be the right type."""
    return value if isinstance(value, str) and value in allowed else default


class SnapshotManager:
    """GLOBAL FLOOR G3 — guaranteed rollback. See the module docstring."""

    def __init__(
        self,
        *,
        store,
        snapshot_dir: Path | None = None,
        created_the_database: bool | None = None,
        app_build_ref: Callable[[], dict] | None = None,
        mode_ref: Callable[[], str] | None = None,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        """``store`` is a memory.store.Store, duck-typed so tests can pass a
        double. ``snapshot_dir`` defaults to ``<db parent>/snapshots`` and is
        disabled entirely for a ``:memory:`` database. ``app_build_ref`` is a
        zero-arg callable returning ``{"version","identifier"}`` from the Rust
        shell — None in the CLI and in tests, in which case an anchor still
        mints, just without a build reference. ``mode_ref`` returns the active
        policy mode; it is injected rather than imported because §6.1 forbids
        importing ``agent_core.policy``, and it is CAPTURE-side only, so "the
        restore path reads exactly two things" still holds literally. ``clock``
        is injected only so tests get deterministic timestamps.

        ``created_the_database`` is the one fact that decides which permanent
        bottom row this database gets (see ``_ensure_genesis``): True only when
        the CALLER KNOWS this launch created the database file. It is passed in
        rather than worked out here because this module may not import anything
        that could tell it and may not read a setting — and because the answer
        is not in the database at all. ``main.py`` knows it for certain; every
        other caller (the CLI, tests, a duck-typed double) leaves it None, which
        is read as "could not find out" and takes the cautious road.

        Construction is side-effecting exactly once: on an empty table it writes
        the permanent bottom row, because G3 says a restore target exists AT ALL
        TIMES — including before the first turn."""
        self._store = store
        self._clock = clock
        self._created_the_database = created_the_database
        self._app_build_ref = app_build_ref
        self._mode_ref = mode_ref
        self._snapshot_dir = self._resolve_snapshot_dir(snapshot_dir)
        # Where the rollback walk has got to. Set by every successful restore and
        # ALSO written to disk twice — the note beside the sidecars
        # (_write_walk_note) and `restored_to` on the pre_restore row — because a
        # walk that forgot itself on relaunch would put the user straight back
        # into the config they had just escaped. Memory is only the fast path.
        self._last_restored_id: str | None = None
        self._ensure_genesis()

    # --- construction helpers ---------------------------------------------

    def _resolve_snapshot_dir(self, snapshot_dir: Path | None) -> Path | None:
        if snapshot_dir is not None:
            return Path(snapshot_dir)
        db_path = getattr(self._store, "db_path", None)
        # An in-memory database has no directory to put sidecars beside, and a
        # store double may not carry a path at all. Both are supported: the
        # subsystem works normally without the belt, it just loses the belt.
        if not isinstance(db_path, str) or not db_path or ":memory:" in db_path:
            return None
        return Path(db_path).parent / "snapshots"

    def _ensure_genesis(self) -> None:
        """Write the permanent bottom row on a database that has none.

        When this launch CREATED the database that is ``genesis``:
        ``undeletable = 1`` because it is the bottom of the restore walk —
        neither retention nor a delete may reach it — and ``verified_working = 1``
        because a brand-new install is a configuration that works. It is
        deliberately captured AFTER default-widget seeding (main.py wires it
        there) so it holds the real first-run state.

        Otherwise it is ``pre_upgrade``, and the difference matters more than the
        slug. This method fires whenever the table is empty, which is true for
        every install that predates the subsystem, the first time it launches —
        so on that path the row is a copy of whatever config the user happens to
        have RIGHT NOW, up to and including the broken one they are about to need
        rescuing from. Written as genesis it would be a permanent, undeletable
        row labelled "Addison as first installed" that restores services, notes
        and a Developer profile, and whose restore copy promises to clear exactly
        the things it puts back. Marked ``verified_working`` it would also become
        a legitimate target of the one-action button, so the guaranteed bottom of
        the walk could hand the user back the config they were escaping. So:
        honest slug, honest label, honest restore copy, and NOT verified —
        nothing has run against it and this subsystem has watched nothing run
        against it.

        THE CHOICE IS A FACT FROM THE CALLER, NEVER AN INFERENCE FROM THE
        CONTENTS. It used to be inferred here, from whether the payload held any
        provider, note, routine or non-default profile — and an install with
        none of those is not a fresh install, it is the DEFAULT STATE of the two
        people this app is for. Mira and Petr never open Settings > Services (no
        key means turns run on the Setup Assistant relay), never write a note,
        never save a routine and never leave Simple; months of tuned settings,
        widgets and chats left no trace in any of the four signals, so their
        established install was minting a permanent, undeletable, ``verified``
        row promising to be "Addison as first installed" while holding the very
        configuration they would be clicking Restore to escape.

        ``is True``, not truthiness, is the whole guard: None means the caller
        could not find out, and an unknown install must land exactly where a
        known-established one does. The two share this single branch rather than
        each getting their own, because there is only one safe answer and a
        second branch is somewhere to put a guess. Being wrongly told your setup
        predates the update costs one honest sentence and one completed turn;
        being wrongly told your install is brand new hands back the config you
        were escaping, and the row cannot be deleted afterwards."""
        try:
            if self._store.list_config_snapshots():
                return
        except Exception:
            # If we cannot even read the table there is nothing useful to do
            # here; the cold-start sidecar path (§6.4c) is the answer to that
            # grade of damage, and failing construction would take the whole
            # process down with it.
            return
        fresh = self._created_the_database is True
        try:
            self._capture(
                trigger="auto",
                reason="genesis" if fresh else "pre_upgrade",
                verified_working=fresh,
                undeletable=True,
                prune=False,
            )
        except Exception:
            return

    # --- capture -----------------------------------------------------------

    def capture(
        self,
        *,
        trigger: str,
        reason: str,
        verified_working: bool = False,
        prune: bool = True,
    ) -> ConfigSnapshot:
        """Take a snapshot of the current config and store it.

        ``trigger`` is 'auto' (before a risky change) or 'on_command' (the
        Settings control); anything else is coerced to 'auto'. ``reason`` is a
        slug from ``REASONS``; an unknown slug becomes 'other' rather than
        raising — a hook site with a typo must still produce a usable restore
        point.

        ``prune=False`` is used for the ``pre_restore`` capture inside
        ``restore()``: pruning there can delete the very row being restored, and
        a restore must not garbage-collect its own target.

        A sidecar write failure is swallowed — the row is the primary copy. A
        genuine ``sqlite3.Error`` propagates, because the caller (the RPC hook)
        decides per site whether a missing restore point is survivable."""
        return self._capture(
            trigger=trigger, reason=reason, verified_working=verified_working, prune=prune
        )

    def _capture(
        self,
        *,
        trigger: str,
        reason: str,
        verified_working: bool = False,
        undeletable: bool = False,
        prune: bool = True,
        restored_to: str | None = None,
    ) -> ConfigSnapshot:
        """The real capture. ``undeletable`` is private because only the bottom
        row takes it here — a G4 anchor is minted by copy (``mint_anchor``),
        never by capturing the state the user is in the act of weakening.
        ``restored_to`` is private for the same reason: only ``restore()`` sets
        it, on the ``pre_restore`` row it writes."""
        tables = self._store.read_config_state()
        snapshot = self._write_row(
            tables=tables,
            trigger=trigger,
            reason=reason,
            verified_working=verified_working,
            undeletable=undeletable,
            restored_to=restored_to,
        )
        if prune:
            try:
                self.prune()
            except Exception:
                pass
        return snapshot

    def _write_row(
        self,
        *,
        tables: dict,
        trigger: str,
        reason: str,
        verified_working: bool,
        undeletable: bool,
        fingerprint: str | None = None,
        payload_version: int = PAYLOAD_VERSION,
        captured_at: int | None = None,
        captures_binary: bool = False,
        binary_ref: str | None = None,
        restored_to: str | None = None,
    ) -> ConfigSnapshot:
        """Serialise, fingerprint, insert, dual-write. Shared by capture and
        ``mint_anchor`` so both produce the identical payload shape."""
        snapshot_id = str(uuid.uuid4())
        created_at = self._clock() if captured_at is None else captured_at
        fingerprint = _fingerprint(tables) if fingerprint is None else fingerprint
        meta: dict[str, Any] = {
            "id": snapshot_id,
            "trigger": trigger if trigger in _TRIGGERS else "auto",
            "reason": reason if reason in REASONS else "other",
            "created_in_mode": self._current_mode(),
            "state_fingerprint": fingerprint,
            "verified_working": int(verified_working),
            "undeletable": int(undeletable),
            "captures_binary": int(captures_binary),
            "binary_ref": binary_ref,
        }
        if restored_to is not None:
            # ADDITIVE payload-shape change, beyond the nine meta keys the step-1
            # contract §5.5 froze. Recorded here rather than in a changelog because
            # this line is where it happens: the key appears ONLY on `pre_restore`
            # rows, is only ever added, and every existing reader ignores it —
            # `_decode_payload` validates `tables` and `version` and never the meta
            # key set, `rebuild_rows_from_payloads` reads meta by name, and the
            # fingerprint is over `tables` alone, so an ordinary payload keeps the
            # exact bytes it has always had and no old build is disturbed by a new
            # one. It earns the exception by being what makes the rollback walk
            # survive a relaunch: without it a restart between two clicks rewinds
            # the walk and hands the user back the config they escaped (see
            # _recorded_restore_target, which reads it, and _write_walk_note, which
            # keeps the second copy).
            meta["restored_to"] = restored_to
        payload = {
            "version": payload_version,
            "captured_at": created_at,
            # Whole seconds tie constantly — several captures per user action —
            # and the tiebreak used to be uuid4 lexical order off the filesystem.
            # This is the real clock rather than the injected one on purpose: the
            # injected clock exists so tests get deterministic SECONDS, and a
            # deterministic tiebreak would hide exactly the ordering bug this
            # stamp is here to remove. See recover_payloads_from_disk.
            "captured_at_ns": time.time_ns(),
            # `meta` is not decoration — it is the row's only backup. A rebuild
            # from sidecars (§6.4c) reads it to recreate the row, so anchor-ness
            # and verified-ness travel WITH the payload; they exist nowhere else
            # once the database is gone.
            "meta": meta,
            "tables": tables,
        }
        blob = _canonical(payload)
        snapshot = ConfigSnapshot(
            id=snapshot_id,
            created_at=created_at,
            trigger=meta["trigger"],
            reason=meta["reason"],
            payload_version=payload_version,
            state_blob=blob,
            state_fingerprint=fingerprint,
            verified_working=verified_working,
            undeletable=undeletable,
            captures_binary=captures_binary,
            binary_ref=binary_ref,
            created_in_mode=meta["created_in_mode"],
        )
        self._store.insert_config_snapshot(snapshot)
        self._write_sidecar(snapshot_id, blob)
        return snapshot

    def _current_mode(self) -> str:
        """The live policy mode, for the display-only ``created_in_mode``
        column. Any failure or absence resolves to 'safe' — the column is never
        allowed to influence a query, so a wrong value here is cosmetic, while
        raising here would fail a capture."""
        if self._mode_ref is None:
            return "safe"
        try:
            mode = self._mode_ref()
        except Exception:
            return "safe"
        return mode if mode in ("safe", "open", "custom") else "safe"

    def mark_verified_working(self) -> ConfigSnapshot | None:
        """Record that the CURRENT configuration provably works.

        It does NOT flip a flag on an older row: the pre-change snapshot holds a
        config the turn never ran against, and marking it verified would make
        "restore lands somewhere that actually ran" false. Instead this captures
        the current config as a new ``verified_working`` row.

        Idempotence comes from the fingerprint, not from a dirty flag — the
        current state is compared with the newest verified row's fingerprint, so
        a hundred turns against an unchanged config produce one row. A
        fingerprint cannot MISS a mutation the way a hook-set flag can, which is
        why no ``note_config_changed()`` hook exists anywhere in this design.

        This marks a config verified as soon as ONE turn answers against it,
        including a config that was just broken — that is the amendment's literal
        predicate. The correction lives in ``restore_last_working()``, which
        never targets a config identical to the current one; the two are a single
        design and must be read together.

        Swallows every exception and returns None: a failure here must never
        convert a successful turn into an error."""
        try:
            tables = self._store.read_config_state()
            fingerprint = _fingerprint(tables)
            refs = self._store.verified_config_snapshot_refs()
            if refs and refs[0].get("state_fingerprint") == fingerprint:
                return None
            permanent = self._permanent_row_matching(fingerprint)
            if permanent is not None:
                if permanent["verified_working"]:
                    return None
                self._store.set_config_snapshot_verified(permanent["id"])
                return self._store.get_config_snapshot(permanent["id"])
            return self._write_row(
                tables=tables,
                trigger="auto",
                reason="turn_verified",
                verified_working=True,
                undeletable=False,
                fingerprint=fingerprint,
            )
        except Exception:
            return None

    def _permanent_row_matching(self, fingerprint: str) -> dict | None:
        """A permanent row holding EXACTLY the configuration that just answered.

        The one case where flagging an existing row is honest rather than the
        failure the docstring above warns about. A pre-change snapshot holds a
        config the turn never ran against — but a fingerprint match is proof the
        turn ran against precisely THIS content, which is the whole evidence
        ``verified_working`` is meant to record.

        Narrowed to ``undeletable`` rows on purpose, because that is where it
        pays. The permanent bottom row (genesis / pre_upgrade / a G4 anchor) is
        the one restore point retention can never prune and the triggers refuse
        to delete — so it is the row most worth being able to return to, and it
        was previously the one row that could never become a target, however many
        turns ran against it. Writing a second row with byte-identical content
        instead left the guaranteed row permanently unproven.

        Ordinary rows are deliberately left alone: verifying an arbitrary
        pre-change snapshot buys nothing the fresh ``turn_verified`` row does not
        already provide, and would widen a rule that only needs to be narrow."""
        for row in self._store.list_config_snapshots():
            if row.get("undeletable") and row.get("state_fingerprint") == fingerprint:
                return row
        return None

    # --- restore -----------------------------------------------------------

    def restore(self, snapshot_id: str) -> RestoreResult:
        """Restore one specific snapshot by id — the Settings list row, and the
        anchor path. Never raises; every failure becomes ok=False plus one plain
        sentence."""
        try:
            row = self._store.get_config_snapshot(snapshot_id)
        except Exception:
            row = None
        payload = _decode_payload(row.state_blob) if row is not None else None
        if payload is None:
            # The sidecar is a real second copy, not decoration: it is what
            # answers "the file opens but the row is unreadable".
            payload = self._read_sidecar(snapshot_id)
        if payload is None:
            return RestoreResult(ok=False, error=_MISSING if row is None else _UNREADABLE)

        # The recovery is itself reversible — clicking Restore twice is safe.
        # prune=False because pruning here could delete the very row we are
        # about to restore. A failure does NOT abort: recovery outranks the
        # reversibility of the recovery.
        try:
            self._capture(
                trigger="auto", reason="pre_restore", prune=False, restored_to=snapshot_id
            )
        except Exception:
            pass

        current = self._current_profile()
        try:
            self._store.apply_config_state(payload["tables"])
        except Exception:
            return RestoreResult(ok=False, snapshot_id=snapshot_id, error=_APPLY_FAILED)

        self._note_restored(snapshot_id)
        reason = row.reason if row is not None else _payload_reason(payload)
        detail = _RESTORED_DETAIL.get(reason, _RESTORED)
        return RestoreResult(
            ok=True,
            snapshot_id=snapshot_id,
            detail=detail,
            binary_mismatch=self._binary_mismatch(row),
            profile_change=_profile_change(current, _payload_profile(payload)),
        )

    def restore_last_working(self) -> RestoreResult:
        """THE G3 one-action restore. No arguments, by design — the floor cannot
        require the user to know an id.

        Walks the verified rows newest-first, unbounded, and restores the first
        candidate whose fingerprint DIFFERS from the current config's and whose
        payload decodes — starting strictly BELOW whatever the last restore put
        the user on, so successive clicks are monotonically older.

        **The fingerprint skip is the first half of the fix for the motivating
        scenario.** The amendment marks a config verified after one successful
        turn — but a degraded config still answers messages. Without the skip, a
        sweeping bad change that answers one turn becomes the newest verified row
        and the one-action button restores the user into the state they are
        trying to escape. A candidate identical to the current config would
        change zero bytes, so it is provably never a legitimate target.

        **The remembered position is the second half, and it is not optional.**
        The skip alone leaves the newest row — the bad config — distinct from the
        restored one, so the very next click walks FORWARD into it; H8 writes a
        fresh verified row for the restored config after one ordinary turn, which
        is enough to make it happen in normal use. Anchoring on "the newest row
        whose fingerprint is the current config" instead is worse: a config the
        user returns to (toggle a setting, toggle it back) appears twice, the
        anchor keeps jumping to the newer occurrence, and clicks oscillate
        between two states forever with the older ones unreachable. Only
        remembering WHICH ROW was restored — an identity, not a fingerprint —
        makes each click step back one distinct proven configuration.

        See ``_walk_last_working`` for what the four non-``ok`` outcomes mean and
        why each gets its own sentence."""
        target, why, position = self._walk_last_working()
        if target is not None:
            return self.restore(target["id"])
        # Second arm (§6.4b): the candidate list comes from a query against the
        # very table that, by hypothesis, may be the damaged thing. When that
        # query raises — or comes back empty, or comes back describing rows
        # nothing can read — there are no ids to look sidecars up FOR, so go to
        # the disk directly. This runs BEFORE any of the honest failure sentences
        # below, because "your setup already matches your last working setup" is a
        # reassurance, and printing it while a perfectly good restore point sits
        # unread on disk is the exact failure this floor exists to prevent.
        #
        # `position` is what keeps this arm from undoing a rollback already in
        # progress. It used to be a boolean veto instead, which switched the arm
        # off for a mid-walk user and left it wide open on the one path where the
        # veto could not be computed — so the click that most needed the disk got
        # nothing, and the click that most needed the position got a jump forward.
        #
        # 'bottom' is the one outcome the disk does NOT get to second-guess: it
        # can only be reached from a database that answered, completely, that
        # there is nothing older among the setups it has seen working. The other
        # outcomes all mean the database's answer was absent or incomplete.
        #
        # 'none' DELIBERATELY reaches the arm below with require_verified=False,
        # and it is worth being explicit because the two branches look
        # inconsistent side by side. 'none' means no verified row exists at all —
        # the state every upgraded install is in before its first turn — so there
        # is no proven config to prefer and nothing to step past. Refusing there
        # would leave the one-action button dead on the most common upgraded
        # install, with a perfectly good `pre_upgrade` payload on disk. So it
        # restores, and `_RESTORED_UNVERIFIED` says plainly that this was not a
        # setup Addison had seen working. 'bottom' is the opposite case: verified
        # rows DO exist and the user has already walked back through them, so
        # stepping them past their own proven configs into an unproven one is a
        # choice that belongs to them — hence naming the row instead
        # (_OLDER_IN_THE_LIST). See `select_payload_to_restore` for the governing
        # rule: an unverified restore is fine, an unverified restore *dressed up
        # as a verified one* is the failure this floor was written against.
        #
        # `require_verified` when the walk itself was healthy and merely had
        # nothing to offer: in that state the database is readable and the
        # sidecars hold the same story, so only a payload the DB failed to
        # surface AND that provably ran is worth overriding it with.
        if why != "bottom":
            recovered = self._restore_from_sidecars(
                require_verified=(why == "identical"), position=position
            )
            if recovered is not None:
                return recovered
        if why == "unreadable":
            return RestoreResult(ok=False, error=_WALK_UNREADABLE)
        if why == "identical":
            return RestoreResult(ok=False, error=_ALREADY_THERE)
        if why == "bottom":
            # "Nothing further back" is a claim about the LIST, so the list is
            # what gets asked. It disagrees on every upgraded install, where the
            # unverified `pre_upgrade` row sits below anything the walk can
            # reach. See _OLDER_IN_THE_LIST for why this names that row rather
            # than restoring it.
            older = self._oldest_restore_point_below(position)
            if older is not None:
                return RestoreResult(ok=False, error=_OLDER_IN_THE_LIST.format(label=older))
            return RestoreResult(ok=False, error=_AT_THE_BOTTOM)
        return RestoreResult(ok=False, error=_NO_TARGET)

    def last_working_target(self) -> dict | None:
        """The row ``restore_last_working()`` would target RIGHT NOW, or None.

        Shares one implementation with the walk, so the confirm step's preview
        can never disagree with the action. It exists because the two-step
        confirm has to be able to NAME what the user is about to get: "Addison as
        first installed" is a very different click from "Working setup,
        yesterday", and a restore that silently moves the user from Simple to
        Developer — which a restored ``active_profile`` will do, taking the
        policy mode with it — must say so BEFORE the click."""
        return self._walk_last_working()[0]

    def _walk_last_working(self) -> tuple[dict | None, str, str | None]:
        """``(target, why, position)``.

        ``why`` is one of:
          * ``'ok'``          — ``target`` is the row to restore.
          * ``'identical'``   — every candidate was the config already running.
          * ``'unreadable'``  — the saved setups could not be read: either the
            candidate list itself failed, or a candidate could not be decoded
            from its row OR its sidecar and nothing below it was usable either.
          * ``'bottom'``      — the walk has already reached the oldest saved
            setup; there is nothing further back.
          * ``'none'``        — there is nothing to go back to at all.

        The caller needs all five, because each is a different sentence to a
        person. Telling somebody "your setup already matches your last working
        setup" when the truth is "I could not read any of them" is the shape of
        failure this floor exists to prevent.

        ``position`` is the id the last restore landed on, or None when there is
        none. It is handed to the sidecar arm so that arm can select strictly
        BELOW it — it is not a veto on the arm, and it used to be exactly that.
        The disk holds the same payloads the walk has already stepped past, so an
        arm that ignored the position would undo the user's rollback with the
        recovery path itself; but an arm switched off whenever a position existed
        would strand the mid-walk user whose database is the damaged part, which
        is the one person it exists for. Selecting below the position serves both.
        (Whether the arm runs at all is decided by ``why``, in the caller.)

        It is the RAW marker, deliberately not expired here. Expiry needs a row
        to compare against, and the two arms have different rows available — the
        walk has the refs, the sidecar arm has only what is on disk, and on the
        path below there are no refs at all. Each arm therefore applies the same
        rule to the copy it can actually see."""
        position = self._walk_marker()
        try:
            refs = self._store.verified_config_snapshot_refs()
        except Exception:
            # The candidate list is unreadable, so there is no walk to run — but
            # a rollback already in progress still has to be honoured, which is
            # why the position goes back to the caller rather than a bare "no".
            # 'unreadable' rather than 'none': the query raising means the saved
            # setups could not be READ, and answering "there's nothing to go back
            # to yet" while the Settings list is full of restore points sends the
            # user looking for something they already have.
            return None, "unreadable", position
        try:
            current_fingerprint = _fingerprint(self._store.read_config_state())
        except Exception:
            # A config we cannot even read cannot be compared, so nothing is
            # skipped and the newest decodable verified row wins. Failing open
            # here is right: the alternative is no restore at all.
            current_fingerprint = None
        start = self._walk_start(refs, current_fingerprint, position)
        saw_identical = False
        saw_unreadable = False
        for ref in refs[start:]:
            fingerprint = ref.get("state_fingerprint")
            if current_fingerprint is not None and fingerprint == current_fingerprint:
                saw_identical = True
                continue
            payload = self._load_payload(ref.get("id"))
            if payload is None:
                saw_unreadable = True
                continue
            reason = ref.get("reason") if ref.get("reason") in REASONS else "other"
            return {
                "id": ref.get("id"),
                "reason": reason,
                "reason_label": REASONS[reason],
                "created_at": ref.get("created_at"),
                "created_in_mode": ref.get("created_in_mode", "safe"),
                # Additive to the frozen five keys: the confirm step has to be
                # able to say that a restore will move the user between profiles
                # (and therefore between policy modes) BEFORE the click.
                "profile_change": _profile_change(
                    self._current_profile(), _payload_profile(payload)
                ),
            }, "ok", position
        # A restore point we could not read outranks the other two: it is the one
        # outcome with something wrong in it, and the user can act on it.
        if saw_unreadable:
            return None, "unreadable", position
        if saw_identical:
            return None, "identical", position
        return None, ("bottom" if start > 0 else "none"), position

    def _walk_marker(self) -> str | None:
        """The id the last restore landed on, or None when there is no position.

        This session's memory first, then the note written beside the sidecars,
        then the ``pre_restore`` row. Each fallback survives something the one
        before it does not: memory dies with the process, the note needs a
        writable directory, the row needs a database that still accepts an
        INSERT."""
        if self._last_restored_id:
            return self._last_restored_id
        return self._recorded_restore_target()

    def _walk_start(
        self, refs: list[dict], current_fingerprint: str | None, marker: str | None
    ) -> int:
        """Where the walk begins: strictly BELOW the row the last restore landed
        on, for exactly as long as the user is still sitting on it.

        Position is kept by ROW IDENTITY, never by fingerprint. A user toggling a
        setting and toggling it back is entirely ordinary, and it puts the same
        fingerprint in the list twice; anything that locates the position by
        fingerprint locks onto the newer occurrence and the walk oscillates
        between two configurations forever, with everything older unreachable.
        Identity has no such failure mode — refs only ever grows at the top, so
        a remembered row's position can move down but never up, and "strictly
        below it" is monotonically older by construction.

        The position expires on its own. It holds only while the current config
        still fingerprint-matches the row that was restored, so the moment the
        user changes anything the walk is over and the next click starts from the
        top again. That also cleans up after a restore whose apply failed: the
        marker was written before the apply, but the config never moved, so the
        fingerprints disagree and the marker is ignored.

        A marker naming a row ``refs`` does not contain is not inert: it is an
        UNVERIFIED restore point the user picked out of the Settings list, and
        ``_start_below_the_full_list`` locates it among all the rows instead."""
        if not marker or current_fingerprint is None:
            # No position, or no readable config to check it against. Start at
            # the top: recovery outranks tidiness of the walk.
            return 0
        for index, ref in enumerate(refs):
            if ref.get("id") != marker:
                continue
            if ref.get("state_fingerprint") == current_fingerprint:
                return index + 1
            return 0
        return self._start_below_the_full_list(refs, current_fingerprint, marker)

    def _start_below_the_full_list(
        self, refs: list[dict], current_fingerprint: str, marker: str
    ) -> int:
        """Where the walk begins when the position names a row that is not in the
        verified list at all — the user restored an UNVERIFIED point by id from
        the Settings list, which ``restore()`` records like any other.

        Treating that as no position lets the very next click step FORWARD past
        the point the user just deliberately chose, into the config they were
        escaping. It self-corrects on the click after, which makes it a small
        defect rather than a broken floor, but "each click steps back one distinct
        proven configuration" is the promise and one click that goes the other way
        breaks it.

        The position is located by ``_locate_in_full_list``; see it for why the full
        list and not the verified refs, and why position rather than ``created_at``."""
        located = self._locate_in_full_list(marker)
        if located is None:
            return 0            # unreadable, pruned, or from another database
        rows, at = located
        ids = [row.get("id") for row in rows]
        if rows[at].get("state_fingerprint") != current_fingerprint:
            return 0            # the user has moved on; the position expired
        older = set(ids[at + 1 :])
        for index, ref in enumerate(refs):
            if ref.get("id") in older:
                return index
        # Every verified row is NEWER than where the user is standing, so there is
        # nothing further back to offer — which is 'bottom', not 'start at the top'.
        return len(refs)

    def _oldest_restore_point_below(self, position: str | None) -> str | None:
        """The plain-language label of the OLDEST restore point sitting below the
        walk's position, or None when the list really does end where the walk did.

        Shares ``_locate_in_full_list`` with ``_start_below_the_full_list``: same
        question of the same list, asked for the MESSAGE rather than the walk. The
        full list is the right one to ask because it is what the user can see and
        click — the sentence this feeds is a promise about their screen.

        Every row below the position is guaranteed UNVERIFIED whenever the caller
        is on the ``'bottom'`` outcome, which is what entitles the message to say
        Addison never saw it working without checking each one: reaching
        ``'bottom'`` means ``refs[start:]`` was empty, i.e. no verified row sits
        below the marker at all.

        Returns None rather than raising on anything unexpected — the store is
        duck-typed, so a damaged one can answer strangely here. A sentence that
        cannot be substantiated is simply not said, and the caller falls back to
        the plain bottom message, which is the safe direction: it claims less."""
        located = self._locate_in_full_list(position)
        if located is None:
            return None
        rows, at = located
        below = rows[at + 1 :]
        if not below:
            return None
        return REASONS[_choice(below[-1].get("reason"), REASONS, "other")]

    def _locate_in_full_list(self, marker: str | None) -> tuple[list[dict], int] | None:
        """``(all rows, index of ``marker``)``, or None when it cannot be placed.

        The one place that asks "where is this id among ALL the restore points". Two
        callers need exactly that and for related reasons — ``_start_below_the_full_list``
        so the walk does not step forward past a row the user chose by hand, and
        ``_oldest_restore_point_below`` so the bottom message can name what is under
        it — and they had the lookup written out twice, character for character apart
        from the fallback value.

        The FULL list, not the verified refs: both are ordered ``created_at DESC,
        rowid DESC``, so "below" means the same thing in each, but only the full list
        contains the unverified rows these two callers are reasoning about. Position
        within that shared ordering is what gets compared, never ``created_at``
        itself — several rows share a second constantly (a hook's pre-change capture
        and the verified row after it land in the same one), so a timestamp
        comparison either keeps a row it should skip or skips one it should keep.

        None covers all three ways this can fail to mean anything — an unreadable
        store, a marker that was pruned, a marker from another database — because
        every caller's answer to them is the same: fall back, claim less. Each keeps
        its OWN fallback value, which is why this returns None rather than choosing
        one."""
        if not marker:
            return None
        try:
            rows = self._store.list_config_snapshots()
        except Exception:
            return None
        ids = [row.get("id") for row in rows]
        try:
            return rows, ids.index(marker)
        except ValueError:
            return None

    def _recorded_restore_target(self) -> str | None:
        """The id the last restore landed on, read back off disk.

        In memory alone this would be lost on relaunch, and the walk would then
        hand the user the very config they had escaped an hour earlier — the
        floor failing quietly at exactly the moment somebody comes back to it. So
        it is written down twice, for the same reason every payload is.

        The note beside the sidecars is read FIRST because it is never staler: it
        is written after the config has actually landed and on every successful
        restore, including the sidecar arm's. The ``pre_restore`` row is written
        BEFORE the apply and only when the database will still take an INSERT —
        so a capture that fails (a full disk) leaves no row, and that is the case
        where the position used to live in memory only and a relaunch walked the
        user forward once.

        Only the NEWEST ``pre_restore`` row counts. An older one describes an
        older restore, and honouring it would rewind the walk. An unreadable one
        means no position, not "keep looking".

        Reads the disk only — ``_walk_marker`` prefers whatever this session
        already knows, which is never older than what is written down."""
        noted = self._read_walk_note()
        if noted:
            self._last_restored_id = noted       # read once per process
            return noted
        try:
            rows = self._store.list_config_snapshots()
        except Exception:
            return None
        for row in rows:                      # newest first
            if row.get("reason") != "pre_restore":
                continue
            payload = self._load_payload(row.get("id"))
            meta = payload.get("meta") if isinstance(payload, dict) else None
            target = meta.get("restored_to") if isinstance(meta, dict) else None
            if isinstance(target, str) and target:
                self._last_restored_id = target      # read once per process
                return target
            return None
        return None

    def _note_restored(self, snapshot_id: str) -> None:
        """Remember where the walk has got to. Called only after an apply that
        actually landed, so a failed restore never advances the walk — and
        written down as well as remembered, so a relaunch does not restart it."""
        self._last_restored_id = snapshot_id
        self._write_walk_note(snapshot_id)

    def _load_payload(self, snapshot_id: Any) -> dict | None:
        """One candidate's payload: the row's blob, then its sidecar. Returns
        None when neither is usable, so the walk moves on — one bad blob must
        never strand the floor."""
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return None
        try:
            row = self._store.get_config_snapshot(snapshot_id)
        except Exception:
            row = None
        if row is not None:
            payload = _decode_payload(row.state_blob)
            if payload is not None:
                return payload
        return self._read_sidecar(snapshot_id)

    def _restore_from_sidecars(
        self, *, require_verified: bool = False, position: str | None = None
    ) -> RestoreResult | None:
        """Restore from the sidecar files with no usable ``config_snapshots``
        table at all. None when there is nothing on disk worth applying.

        ``position`` is where the rollback walk has got to, and honouring it is
        what makes this arm safe to run for a user who is mid-walk. Without it
        the arm reads the whole directory and can only ever pick the newest thing
        it likes the look of — which is one of the payloads the walk has already
        stepped past. See ``_payloads_below``.

        Chooses through ``select_payload_to_restore`` — the same one function the
        RPC cold-start path and the listing that NAMES the target both use, so
        the confirm step can never say one thing while the button does another.

        It used to apply the newest payload that decoded, full stop. The newest
        sidecar is almost always an unverified automatic capture taken
        immediately BEFORE the change that broke things, so "restore to my last
        working setup" reliably restored the broken setup and reported the
        ordinary success sentence while doing it. Now an unverified payload is a
        last resort AND says so — see ``_RESTORED_UNVERIFIED``.

        A payload that fails to apply is dropped and the choice is made again, so
        one bad payload still cannot strand the floor."""
        if self._snapshot_dir is None:
            return None
        remaining = recover_payloads_from_disk(self._snapshot_dir)
        try:
            current_fingerprint = _fingerprint(self._store.read_config_state())
        except Exception:
            current_fingerprint = None
        remaining = _payloads_below(remaining, position, current_fingerprint)
        current = self._current_profile()
        while remaining:
            payload, is_verified = select_payload_to_restore(
                remaining, current_fingerprint=current_fingerprint
            )
            if payload is None or (require_verified and not is_verified):
                return None
            try:
                self._store.apply_config_state(payload["tables"])
            except Exception:
                remaining = [p for p in remaining if p is not payload]
                continue
            meta = payload.get("meta")
            snapshot_id = meta.get("id") if isinstance(meta, dict) else None
            if isinstance(snapshot_id, str):
                self._note_restored(snapshot_id)
            reason = _payload_reason(payload)
            detail = _RESTORED_DETAIL.get(reason, _RESTORED)
            return RestoreResult(
                ok=True,
                snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
                detail=detail if is_verified else _RESTORED_UNVERIFIED,
                profile_change=_profile_change(current, _payload_profile(payload)),
            )
        return None

    def _current_profile(self) -> str | None:
        """The live ``active_profile`` string, read out of the captured state —
        never parsed, never resolved, never imported. It is used for exactly one
        thing: deciding whether to tell the user the restore moves them between
        profiles. Any failure means we simply do not say it."""
        try:
            return _profile_from_tables(self._store.read_config_state())
        except Exception:
            return None

    def _binary_mismatch(self, row: ConfigSnapshot | None) -> str | None:
        """A plain sentence when an anchor records a different build from the one
        running. Step 1 reports; nothing in the codebase replaces a binary."""
        if row is None or not row.captures_binary or not row.binary_ref:
            return None
        if self._app_build_ref is None:
            return None
        try:
            saved = json.loads(row.binary_ref)
            running = self._app_build_ref()
        except Exception:
            return None
        if not isinstance(saved, dict) or not isinstance(running, dict):
            return None
        saved_version = saved.get("version")
        running_version = running.get("version")
        if not saved_version or not running_version or saved_version == running_version:
            return None
        return (
            f"This restore point was saved on Addison {saved_version} and you're "
            f"running {running_version}. Your settings went back; the app itself "
            "didn't change."
        )

    # --- listing, deleting, retention --------------------------------------

    def list(self) -> list[dict]:
        """Every snapshot, newest first, metadata only, each row carrying a
        plain-language ``reason_label`` for the UI.

        Returns ALL rows in EVERY mode. There is no mode filter and no
        ``created_in_mode`` predicate anywhere in this method: hiding a snapshot
        from the mode a recovering user lands in would defeat G3 in exactly the
        moment it exists for (contract §0 C6)."""
        rows = self._store.list_config_snapshots()
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            item["reason_label"] = REASONS[_choice(item.get("reason"), REASONS, "other")]
            out.append(item)
        return out

    def delete(self, snapshot_id: str) -> tuple[bool, str | None]:
        """Delete an ordinary snapshot. Returns ``(ok, error)``.

        A refusal is a plain sentence in a SUCCESSFUL result, never an exception
        (house rule), and the sentence is chosen by ``reason`` because the two
        permanent kinds need different explanations. The refusal is enforced
        three deep — this message check, the store's ``AND undeletable = 0``, and
        the schema trigger underneath both — because "neither user nor model can
        remove it" is a floor and must not rest on one layer remembering."""
        row = self._store.get_config_snapshot(snapshot_id)
        if row is None:
            return False, _MISSING
        if row.undeletable:
            return False, _permanent_message(row.reason)
        if not self._store.delete_config_snapshot(snapshot_id):
            return False, _permanent_message(row.reason)
        self._remove_sidecar(snapshot_id)
        # Belt for the sidecar unlink above, which is best-effort and silent. A
        # file left behind is not cosmetic: the cold-start path reads the whole
        # directory, so an orphan is a restore point the user deleted coming back
        # from the dead the next time the database has to be rebuilt.
        self._sweep_sidecars()
        return True, None

    def mint_anchor(self, *, reason: str = "guard_weakened") -> ConfigSnapshot | None:
        """Mint the G4 undeletable anchor. API present in step 1; the CALLER (a
        Custom-profile guard toggle) lands in step 2.

        An anchor is an undeletable snapshot OF THE LAST VERIFIED-WORKING STATE,
        not a capture of the state the user is in the act of weakening — so this
        COPIES the newest usable verified row's ``tables`` into a new row with
        ``undeletable = 1`` and ``verified_working = 1`` (it copies a payload
        that provably ran, so the flag is earned and the anchor is a legitimate
        restore target), preserving ``state_fingerprint`` and ``payload_version``
        exactly. Unlike ``restore_last_working()`` there is no fingerprint skip:
        an anchor's job is to preserve a known-good point, not to move away from
        one.

        The payload's ``meta`` block is rewritten for the new row rather than
        copied. It has to be: ``meta`` is the only backup of a row's flags, so an
        anchor carrying the source row's ``undeletable: 0`` would come back from
        a sidecar rebuild as an ordinary deletable snapshot — G4 defeated by G3's
        recovery path. ``state_fingerprint`` is over ``tables`` only, so the
        rewrite leaves it byte-identical.

        It then asks the shell for the build reference; if the shell is absent or
        the call fails the anchor STILL MINTS with ``captures_binary = 0``.
        Undeletability is the floor, the build reference is the bonus, and a
        wedged IPC round-trip must never be able to prevent a safety anchor.

        The reference is fetched NOW while the payload is copied from an OLDER
        row, so an anchor pairs *the config that last provably worked* with *the
        build you were running when you weakened your protections*. That is the
        useful pairing — it is the build you will be running when you need the
        way back — and step 1 records it; nothing restores it.

        Returns None only when there is no verified row to anchor at all, which
        genesis makes unreachable in practice. Step 2's caller must nonetheless
        refuse the guard toggle on None: weakening without a way back is exactly
        what G4 forbids."""
        try:
            refs = self._store.verified_config_snapshot_refs()
        except Exception:
            refs = []
        for ref in refs:
            payload = self._load_payload(ref.get("id"))
            if payload is None:
                continue
            binary_ref = self._build_reference()
            version = payload.get("version")
            return self._write_row(
                tables=payload["tables"],
                trigger="auto",
                reason=reason if reason in REASONS else "other",
                verified_working=True,
                undeletable=True,
                fingerprint=str(ref.get("state_fingerprint") or _fingerprint(payload["tables"])),
                payload_version=version if isinstance(version, int) else PAYLOAD_VERSION,
                captures_binary=binary_ref is not None,
                binary_ref=binary_ref,
            )
        return None

    def _build_reference(self) -> str | None:
        if self._app_build_ref is None:
            return None
        try:
            ref = self._app_build_ref()
        except Exception:
            return None
        if not isinstance(ref, dict):
            return None
        version = ref.get("version")
        identifier = ref.get("identifier")
        if not isinstance(version, str) or not isinstance(identifier, str):
            return None
        # A short JSON build REFERENCE, never bytes and never a filesystem path:
        # anchors are unbounded, so they must stay tiny.
        return _canonical({"version": version, "identifier": identifier})

    def prune(self) -> None:
        """Retention, called opportunistically at the end of ``capture``. The
        store's SQL exempts anchors, genesis and the newest two verified rows;
        this layer only supplies the window and tidies orphaned sidecars (an
        orphaned file is harmless, a missing one is already tolerated)."""
        cutoff = self._clock() - MAX_AGE_DAYS * 86_400
        self._store.prune_config_snapshots(cutoff=cutoff, keep_last=KEEP_LAST)
        self._sweep_sidecars()

    # --- sidecar files ------------------------------------------------------

    def _write_sidecar(self, snapshot_id: str, blob: str) -> None:
        """Best-effort second copy of the exact same canonical bytes. A failure
        never fails a capture — the row is the primary copy and this is the belt.

        0700 on the directory and 0600 on each file, set explicitly rather than
        inherited from the process umask (which on a typical macOS home is
        world-readable): these are a plaintext copy of config outside the
        database, and ``provider_config.base_url`` is a field a user can
        legitimately put a credential into (G1)."""
        if self._snapshot_dir is None:
            return
        try:
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self._snapshot_dir, 0o700)
            path = self._snapshot_dir / f"{snapshot_id}.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, blob.encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(path, 0o600)
        except Exception:
            return

    def _write_walk_note(self, snapshot_id: str) -> None:
        """Best-effort note of where the rollback walk has got to.

        The other copy of this fact rides on the ``pre_restore`` row, which is
        written before the apply and only if the database still takes an INSERT.
        This one is written after the config has landed and needs nothing but a
        writable directory, so between them a full disk, a failing table or a
        closed process each lose at most one copy.

        0600 like the sidecars, and no ``.json`` suffix so the orphan sweep and
        the payload reader both leave it alone. A failure is silent: an absent
        note costs one forward step on the next relaunch, which is exactly where
        this started, whereas raising here would fail a restore that worked."""
        if self._snapshot_dir is None:
            return
        try:
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self._snapshot_dir, 0o700)
            path = self._snapshot_dir / _WALK_NOTE_FILE
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, snapshot_id.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            return

    def _read_walk_note(self) -> str | None:
        """The written-down walk position. Whatever it says is only ever matched
        against ids we already have, so a stale or garbled note names nothing and
        is inert — it can misplace the walk, never misapply a restore."""
        if self._snapshot_dir is None:
            return None
        try:
            noted = (self._snapshot_dir / _WALK_NOTE_FILE).read_text(encoding="utf-8").strip()
        except Exception:
            return None
        return noted or None

    def _read_sidecar(self, snapshot_id: str) -> dict | None:
        if self._snapshot_dir is None:
            return None
        try:
            raw = (self._snapshot_dir / f"{snapshot_id}.json").read_text(encoding="utf-8")
        except Exception:
            return None
        return _decode_payload(raw)

    def _remove_sidecar(self, snapshot_id: str) -> None:
        if self._snapshot_dir is None:
            return
        try:
            (self._snapshot_dir / f"{snapshot_id}.json").unlink()
        except Exception:
            return

    def _sweep_sidecars(self) -> None:
        """Drop sidecars with no surviving row. Best-effort and deliberately
        conservative: if the row list cannot be read we remove nothing, because
        an unreadable table is precisely when the sidecars matter most.

        An EMPTY row list is treated the same way, and that guard is the
        important one. Empty means either a database being rebuilt or one that
        has lost its rows — both states where the sidecars are the only surviving
        copy of the user's way back. Reading "no rows" as "every file is an
        orphan" would delete the entire rollback history, from inside the
        machinery whose whole job is to still be there. A healthy database is
        never empty here: the permanent bottom row is written at construction and
        cannot be deleted."""
        if self._snapshot_dir is None:
            return
        try:
            live = {row["id"] for row in self._store.list_config_snapshots()}
            names = os.listdir(self._snapshot_dir)
        except Exception:
            return
        if not live:
            return
        for name in names:
            if not name.endswith(".json") or name[: -len(".json")] in live:
                continue
            try:
                (self._snapshot_dir / name).unlink()
            except Exception:
                continue


def _permanent_message(reason: str) -> str:
    """Why a permanent row would not go away, in the words that fit THIS row. The
    two permanent kinds mean different things to a person, and the fallback is
    the genesis sentence because it is the safe thing to say about a row whose
    provenance we do not recognise."""
    if reason == "guard_weakened":
        return _PERMANENT_GUARD
    if reason == "pre_upgrade":
        return _PERMANENT_PRE_UPGRADE
    return _PERMANENT_GENESIS


def _payload_reason(payload: dict) -> str:
    meta = payload.get("meta")
    reason = meta.get("reason") if isinstance(meta, dict) else None
    return reason if reason in REASONS else "other"


def _payload_profile(payload: dict) -> str | None:
    tables = payload.get("tables")
    return _profile_from_tables(tables) if isinstance(tables, dict) else None


def _profile_from_tables(tables: dict) -> str | None:
    for row in tables.get("app_settings", []) or []:
        if isinstance(row, dict) and row.get("key") == "active_profile":
            value = row.get("value")
            return value if isinstance(value, str) else None
    return None


def _profile_change(current: str | None, target: str | None) -> str | None:
    """The named sentence, or None when the profile does not move. A restore can
    move the user between profiles and therefore between policy modes — a user
    who deliberately switched to Simple and then clicked Restore would land in
    OPEN mode with ``run_command`` visible — so it has to be said out loud."""
    if not target or not current or target == current:
        return None
    name = _PROFILE_NAMES.get(target, target)
    return f"This restore point was saved in {name} mode, so Addison will switch back to {name}."
