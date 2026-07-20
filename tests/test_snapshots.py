"""SnapshotManager — GLOBAL FLOOR G3, guaranteed rollback (amendment §3, spec §4.9).

The suite is headed by ``test_restore_always_works_from_a_broken_config``, which
is the step-1 test: a configuration broken in every way the subsystem could
plausibly depend on still restores, with no model, no provider, no router, no
parseable profile and no readable setting. Everything else here defends one of
the properties that test rests on — the unbounded newest-first walk, the
fingerprint skip that keeps a degraded-but-answering config from becoming the
restore target, the sidecar dual-write, the permanence of anchors and genesis,
and the import ban that keeps the restore path independent of everything that
can be misconfigured.

Real ``Store`` throughout, on a tmp-file database (not ``:memory:``), so the
commit-per-write and the sidecar directory are both genuinely exercised.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_core.memory import store as store_module
from agent_core.memory.store import Store
from agent_core.snapshots import snapshot_manager as sm
from agent_core.snapshots.scope import (
    _CAPTURED_TABLES,
    _EXCLUDED_COLUMNS,
    _EXCLUDED_TABLES,
)
from agent_core.snapshots.snapshot_manager import (
    REASONS,
    SnapshotManager,
    recover_payloads_from_disk,
    rebuild_rows_from_payloads,
    select_payload_to_restore,
)

_MANAGER_SRC = Path(sm.__file__)
_STORE_SRC = Path(store_module.__file__)
_SCHEMA_SRC = Path(store_module._SCHEMA_PATH)


class _Clock:
    """Deterministic, monotonic seconds. Snapshots tie-break on rowid, but an
    advancing clock keeps `created_at DESC` ordering meaningful in assertions."""

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        self.now += 1
        return self.now


def _one_second() -> int:
    """A clock that never moves, so every capture lands in the SAME second.

    Not a curiosity — it is the ordinary case. Several snapshots are written per
    user action, ``created_at`` is whole seconds, and every ordering defect in
    this subsystem hid behind the advancing ``_Clock`` above precisely because a
    test clock that ticks once per capture makes ties impossible. Tests that care
    about ordering use this one."""
    return 1_700_000_000


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "addison.sqlite3")
    yield s
    s.close()


def _manager(store: Any, **kwargs) -> SnapshotManager:
    """``store`` is deliberately untyped: the manager duck-types it (contract
    §5.2) so a test can hand it a double that fails the way a damaged database
    fails, which is the only way to exercise the paths that matter here.

    ``created_the_database`` defaults to True because it is TRUE here: every
    store these tests build sits on a database the test itself created a line
    earlier, so the helper states the fact the same way ``main.py`` does rather
    than leaving the manager to take the cautious road. The tests about an
    upgraded install pass False, and the classification matrix passes both
    explicitly — see ``_bottom_row``."""
    kwargs.setdefault("clock", _Clock())
    kwargs.setdefault("created_the_database", True)
    return SnapshotManager(store=store, **kwargs)


def _sidecar_dir(manager: SnapshotManager) -> Path:
    """The sidecar directory, narrowed. It is only None for a ``:memory:``
    database, and every test that reaches for it uses a tmp-file one."""
    assert manager._snapshot_dir is not None
    return manager._snapshot_dir


def _poison_blob(store: Store, snapshot_id: str) -> None:
    """Corrupt one row's payload behind the manager's back — the "the file opens
    but the row is unreadable" grade of damage."""
    store._conn.execute(
        "UPDATE config_snapshots SET state_blob = ? WHERE id = ?",
        ("{ this is not json", snapshot_id),
    )
    store._conn.commit()


# --- THE step-1 test -------------------------------------------------------


def test_restore_always_works_from_a_broken_config(tmp_path: Path) -> None:
    """THE step-1 test (amendment §14.1, spec:860-862). A configuration broken in
    every way the subsystem could plausibly depend on still restores — with no
    model, no provider, no router, no parseable profile, and no readable setting."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = _manager(store)  # genesis lands here

    # 1. A configuration that demonstrably worked.
    store.insert_skill(id="s1", name="Good", instructions="Be brief.",
                       enabled=True, created_at=100)
    store.upsert_provider_config("anthropic", connected=True, added_at=100,
                                 last_check_ok=True)
    store.set_setting("active_profile", "simple")
    good = manager.mark_verified_working()          # <- the last-known-good row
    assert good is not None

    # 2. Break it the way the friend's setup broke — every writable surface at once.
    store.set_setting("active_profile", "not-a-real-profile")   # unparseable profile
    store.set_setting("routing_strategy", "\x00 not json {[")   # junk setting
    store.delete_provider_config("anthropic")                   # no provider at all
    store.upsert_provider_config("custom", connected=True, base_url="nonsense")
    store.delete_skill("s1")
    store.insert_skill(id="s2", name="Bad", instructions="Use the priciest model.",
                       enabled=True, created_at=200)
    store.insert_widget(id="w-bad", spec_json="{not json", pinned=True, position=0,
                        created_at=200, created_in_mode="open")

    # 2b. THE PART THAT MAKES THIS THE FRIEND'S STORY: the broken setup still
    #     ANSWERS. A cheap/misrouted model replies, the turn completes, and H8
    #     dutifully marks the broken config verified-working. Restore must not
    #     take the bait — see restore_last_working()'s fingerprint skip.
    manager.mark_verified_working()

    # 3. One action. No arguments. No model. No provider. No valid profile.
    result = manager.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == good.id
    assert [s["name"] for s in store.list_skills()] == ["Good"]
    assert store.get_setting("active_profile") == "simple"
    assert store.get_setting("routing_strategy") is None
    assert store.get_provider_config("custom") is None
    anthropic = store.get_provider_config("anthropic")
    assert anthropic is not None and anthropic["connected"] is True
    assert store.get_widget("w-bad") is None
    # The recovery is itself reversible.
    assert any(r["reason"] == "pre_restore" for r in manager.list())
    store.close()


# --- the walk: corruption, sidecars, and the bottom ------------------------


def test_restore_last_working_skips_a_corrupt_payload_and_uses_the_next(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    first = manager.mark_verified_working()
    store.set_setting("marker", "two")
    second = manager.mark_verified_working()
    assert first is not None and second is not None
    # The newest verified row is unreadable in BOTH copies, so the walk must
    # fall through to the one below it rather than giving up.
    _poison_blob(store, second.id)
    manager._remove_sidecar(second.id)
    store.set_setting("marker", "three")

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == first.id
    assert store.get_setting("marker") == "one"


def test_restore_last_working_falls_back_to_the_sidecar_when_the_blob_is_gone(
    store: Store,
) -> None:
    manager = _manager(store)
    store.set_setting("marker", "kept")
    target = manager.mark_verified_working()
    assert target is not None
    _poison_blob(store, target.id)          # row unreadable, sidecar intact
    store.set_setting("marker", "broken")

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == target.id
    assert store.get_setting("marker") == "kept"


def test_restore_last_working_reaches_genesis_when_nothing_else_survives(
    store: Store,
) -> None:
    """More than 20 corrupt verified rows, because a capped walk would have
    stopped short of genesis — which is the OLDEST row, not one of the newest."""
    manager = _manager(store)
    for i in range(25):
        store.set_setting("marker", f"v{i}")
        row = manager.mark_verified_working()
        assert row is not None
        _poison_blob(store, row.id)
        manager._remove_sidecar(row.id)
    store.set_setting("marker", "broken")

    result = manager.restore_last_working()

    assert result.ok, result.error
    genesis = [r for r in manager.list() if r["reason"] == "genesis"]
    assert result.snapshot_id == genesis[0]["id"]
    assert store.get_setting("marker") is None


def test_restore_last_working_recovers_when_config_snapshots_is_unreadable(
    store: Store,
) -> None:
    """§6.4(b)'s second arm: the candidate query is against the very table that
    may be the damaged thing, so there are no ids to look sidecars up FOR."""
    manager = _manager(store)
    store.set_setting("marker", "kept")
    manager.mark_verified_working()
    store.set_setting("marker", "broken")
    store._conn.execute("DROP TABLE config_snapshots")
    store._conn.commit()

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert store.get_setting("marker") == "kept"


class _RefsAreDamaged:
    """A store whose ``config_snapshots`` reads fail, and nothing else.

    The precise shape of §6.4(b): the candidate list for the walk comes from a
    query against the very table that may be the damaged thing, so when that
    query is the broken part there are no ids to look sidecars up FOR."""

    def __init__(self, real: Store) -> None:
        self._real = real
        self.db_path = real.db_path

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def verified_config_snapshot_refs(self) -> list[dict]:
        raise RuntimeError("config_snapshots is damaged")

    def list_config_snapshots(self) -> list[dict]:
        raise RuntimeError("config_snapshots is damaged")


class _VerifiedRefsAreDamaged:
    """Narrower than ``_RefsAreDamaged``: ONLY the verified-rows query fails.

    The finer grade of damage, and the one that reaches the walk's exception arm
    with everything else — the row list, the blobs, the sidecars — still readable.
    A user can be mid-rollback here, which is what makes it different."""

    def __init__(self, real: Store) -> None:
        self._real = real
        self.db_path = real.db_path

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def verified_config_snapshot_refs(self) -> list[dict]:
        raise RuntimeError("the index over verified_working is corrupt")


class _CannotCapture:
    """A store whose snapshot INSERT fails — a full disk. Everything else works,
    so a restore still lands; only the row that remembers it is lost."""

    def __init__(self, real: Store) -> None:
        self._real = real
        self.db_path = real.db_path

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def insert_config_snapshot(self, snapshot: Any) -> None:
        raise RuntimeError("no space left on device")


def test_the_sidecar_arm_restores_the_last_working_setup_not_the_newest_file(
    store: Store,
) -> None:
    """The newest sidecar is almost always the automatic capture taken
    immediately BEFORE the change that broke things — unverified by definition.
    Applying it because it happens to be newest means "restore to my last working
    setup" reliably restores the broken setup, and says the ordinary success
    sentence while doing it."""
    manager = _manager(store)
    store.set_setting("model", "GOOD")
    manager.mark_verified_working()                          # verified, works
    manager.capture(trigger="auto", reason="provider_connect")   # auto, unverified
    store.set_setting("model", "BROKEN-NEVER-WORKED")            # the breaking change
    manager.capture(trigger="auto", reason="mode_switch")        # newest file, unverified

    stranded = _manager(_RefsAreDamaged(store), snapshot_dir=_sidecar_dir(manager))
    result = stranded.restore_last_working()

    assert result.ok, result.error
    assert store.get_setting("model") == "GOOD"
    # A setup that provably ran, so the ordinary sentence is the true one.
    assert result.detail.startswith("Your settings, services, notes, widgets and routines")


def test_the_sidecar_arm_says_when_it_only_found_an_unverified_setup(store: Store) -> None:
    """Falling back to a setup nothing was ever proven against is allowed —
    nothing at all is a worse answer. Saying it went back to the last WORKING
    setup while doing it is not: that sentence is what the user's trust in this
    button rests on."""
    manager = _manager(store)
    store.set_setting("model", "GOOD")
    saved = manager.capture(trigger="on_command", reason="user_request")   # unverified
    store.set_setting("model", "BROKEN")
    # Nothing verified survives: genesis is the only verified row and it is
    # unreadable in both copies.
    genesis = manager.list()[-1]
    _poison_blob(store, genesis["id"])
    manager._remove_sidecar(genesis["id"])

    stranded = _manager(_RefsAreDamaged(store), snapshot_dir=_sidecar_dir(manager))
    result = stranded.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == saved.id
    assert store.get_setting("model") == "GOOD"
    assert result.detail == (
        "Addison couldn't find a setup it had seen working, so it went back to the "
        "most recent settings it had saved instead. Have a look and check things are "
        "how you want them. Your chats and your saved keys weren't touched."
    )


def test_restore_points_that_cannot_be_read_are_not_reported_as_nothing_saved(
    store: Store,
) -> None:
    """"There is nothing to go back to" and "the restore points are there but I
    can't read them" are different situations with different next steps. Telling
    somebody the first while the second is true sends them looking for a restore
    point they already have."""
    manager = _manager(store)
    for marker in ("one", "two"):
        store.set_setting("marker", marker)
        row = manager.mark_verified_working()
        assert row is not None
        _poison_blob(store, row.id)
        manager._remove_sidecar(row.id)
    genesis = manager.list()[-1]
    _poison_blob(store, genesis["id"])
    manager._remove_sidecar(genesis["id"])
    store.set_setting("marker", "three")

    result = manager.restore_last_working()

    assert result.ok is False
    assert result.error == (
        "Addison couldn't read the setups it saved for you. Try picking one from "
        "the list of restore points."
    )


def test_a_working_setup_on_disk_beats_the_already_up_to_date_reply(store: Store) -> None:
    """The reassuring sentence is the dangerous one. "Your setup already matches
    your last working setup" ended the whole attempt before the sidecars were
    ever looked at, so a perfectly good restore point could sit unread on disk
    while the user was told there was nothing to go back to."""
    manager = _manager(store)
    store.set_setting("marker", "GOOD")
    good = manager.mark_verified_working()
    assert good is not None
    store.set_setting("marker", "BROKEN")
    manager.mark_verified_working()

    # The database keeps only rows that match the config we are already running:
    # the good row is gone from the table but its sidecar survives, and genesis
    # no longer counts as proven. Exactly the damage a half-lost table causes.
    store._conn.execute("DELETE FROM config_snapshots WHERE id = ?", (good.id,))
    store._conn.execute("UPDATE config_snapshots SET verified_working = 0 WHERE reason = 'genesis'")
    store._conn.commit()

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert store.get_setting("marker") == "GOOD"


# --- the motivating scenario ------------------------------------------------


def test_restore_last_working_skips_a_config_verified_after_the_last_risky_change(
    store: Store,
) -> None:
    """The friend's story end to end: the broken setup ANSWERS a message, so H8
    marks it verified, and the one-action button must still land on the good
    config rather than the state the user is trying to escape."""
    manager = _manager(store)
    store.insert_skill(id="good", name="Good", instructions="Be brief.",
                       enabled=True, created_at=100)
    good = manager.mark_verified_working()
    assert good is not None

    store.delete_skill("good")
    store.insert_skill(id="cheap", name="Cheapest", instructions="Use the cheapest model.",
                       enabled=True, created_at=200)
    manager.mark_verified_working()          # the broken config answered a turn

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == good.id
    assert [s["id"] for s in store.list_skills()] == ["good"]


def test_a_turn_that_changes_config_mid_flight_is_not_a_restore_trap(store: Store) -> None:
    """The mid-turn variant: the turn itself mutates the config and then reports
    success. The fingerprint skip keeps the floor sound either way."""
    manager = _manager(store)
    store.set_setting("marker", "known-good")
    good = manager.mark_verified_working()
    assert good is not None
    store.set_setting("marker", "changed-mid-turn")
    manager.mark_verified_working()

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert result.snapshot_id == good.id
    assert store.get_setting("marker") == "known-good"


def test_restore_last_working_reports_an_honest_no_op(store: Store) -> None:
    """Silently "succeeding" while changing nothing is exactly the failure this
    floor exists to prevent, so the one case with nothing below the current
    config — a fresh install, still on genesis — says so."""
    manager = _manager(store)

    result = manager.restore_last_working()

    assert result.ok is False
    assert result.error == (
        "Your setup already matches your last working setup, so there's nothing "
        "to go back to."
    )


def test_repeated_restores_walk_further_back(store: Store) -> None:
    """Two clicks reach two distinct proven configs — the documented user model.

    The ``mark_verified_working()`` between the clicks is the whole test. H8 runs
    after every completed turn in production, so a user who clicks Restore, reads
    the answer to one message and clicks Restore again gets exactly this
    sequence; a version of this test without it passes against a walk that sends
    the second click FORWARD, which is how that defect shipped."""
    manager = _manager(store)
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    store.set_setting("marker", "two")
    manager.mark_verified_working()
    store.set_setting("marker", "three")
    manager.mark_verified_working()

    assert manager.restore_last_working().ok
    assert store.get_setting("marker") == "two"
    manager.mark_verified_working()          # one ordinary turn, exactly as H8 does
    assert manager.restore_last_working().ok
    assert store.get_setting("marker") == "one"


def test_a_turn_after_a_restore_does_not_walk_the_user_forward_again(store: Store) -> None:
    """The friend's story, one click further on than the test above.

    After a restore, H8 marks the restored config verified and that row goes to
    the top of the list. A walk that only skips candidates identical to the
    current config then sees the BAD config as the newest distinct one and hands
    it straight back: the user presses "go back" a second time and lands in the
    setup they were escaping. One ordinary turn is all it takes."""
    manager = _manager(store)
    store.insert_skill(id="good", name="Good", instructions="Be brief.",
                       enabled=True, created_at=100)
    manager.mark_verified_working()
    store.delete_skill("good")
    store.insert_skill(id="cheap", name="Cheapest", instructions="Use the cheapest model.",
                       enabled=True, created_at=200)
    manager.mark_verified_working()          # the broken config answered a turn

    assert manager.restore_last_working().ok
    assert [s["id"] for s in store.list_skills()] == ["good"]

    manager.mark_verified_working()          # H8, on the restored config

    target = manager.last_working_target()
    second = manager.restore_last_working()

    assert second.ok, second.error
    assert [s["id"] for s in store.list_skills()] != ["cheap"], (
        "the second click walked FORWARD into the configuration the user was escaping"
    )
    # Nothing else was ever proven to work here, so the only step further back is
    # the fresh install — and the card said so before the click.
    assert store.list_skills() == []
    assert target is not None
    assert target["reason"] == "genesis"
    assert target["id"] == second.snapshot_id


def test_a_configuration_the_user_returned_to_does_not_trap_the_walk(store: Store) -> None:
    """Toggling a setting and toggling it back is entirely ordinary, and it puts
    the same fingerprint in the list twice.

    Anything that locates the walk's position by fingerprint locks onto the newer
    of the two occurrences, so every click flips between the same pair of
    configurations forever and everything older — including the fresh install at
    the bottom — is unreachable. Position has to be an identity."""
    manager = _manager(store)
    for theme in ("light", "dark", "light", "broken"):
        store.set_setting("theme", theme)
        manager.mark_verified_working()

    landed: list[str | None] = []
    visited: list[str | None] = []
    for _ in range(6):
        target = manager.last_working_target()
        result = manager.restore_last_working()
        if not result.ok:
            break
        visited.append(result.snapshot_id)
        landed.append(store.get_setting("theme"))
        assert target is not None and target["id"] == result.snapshot_id

    # Every click landed on a DIFFERENT saved point, and the walk ran out rather
    # than circling. The theme values repeat because the user's history did.
    assert len(visited) == len(set(visited))
    assert landed == ["light", "dark", "light", None]
    assert manager.restore_last_working().error == (
        "You're back at the oldest setup Addison saved, so there's nothing further "
        "back to go to."
    )


def _upgraded_install(tmp_path: Path) -> tuple[Store, SnapshotManager]:
    """An install that predates this subsystem: config already in place, and
    ``config_snapshots`` empty. Its permanent bottom row is ``pre_upgrade``.

    ``created_the_database=False`` is what makes it one — the same fact
    ``main.py`` reads off the filesystem. The config below is here so the tests
    have something to restore, not to signal anything: nothing about the
    contents decides the bottom row any more."""
    store = Store(tmp_path / "addison.sqlite3")
    store.set_setting("active_profile", "simple")
    store.insert_skill(id="from-before", name="From before", instructions="an older note",
                       enabled=True, created_at=1)
    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=False)
    return store, manager


def test_the_bottom_message_does_not_deny_a_restore_point_the_user_can_see(
    tmp_path: Path,
) -> None:
    """The walk's bottom sentence was FALSE on every upgraded install.

    There the permanent bottom row is ``pre_upgrade``, deliberately unverified so
    the one-action button can never hand back the possibly-broken setup the person
    upgraded with. The walk therefore stops above it and used to say "there's
    nothing further back to go to" — while that older row sat in the Settings
    list, saved, permanent, and visible on the same screen.

    The fix is to the SENTENCE, not the walk: the row must still not be a
    one-click target, and the person must still be told where it is."""
    store, manager = _upgraded_install(tmp_path)
    for theme in ("dark", "light"):
        store.set_setting("theme", theme)
        manager.mark_verified_working()

    assert manager.restore_last_working().ok
    result = manager.restore_last_working()

    assert result.ok is False
    # The exact label the Settings list renders for that row, so "the oldest one
    # there" names something the person can actually find and click.
    oldest = manager.list()[-1]
    assert oldest["reason"] == "pre_upgrade"
    assert oldest["reason_label"] in result.error
    assert result.error == (
        "That's as far back as Addison can go on its own. Your restore points go back "
        'further — the oldest one is "Your setup before this update". Addison never saw '
        "that one working, so it won't choose it for you, but you can pick it yourself."
    )
    # The old sentence, and any descendant of it, is now a falsehood on this path.
    assert "nothing further back" not in result.error
    # And it points at the restore points, not at a position on a screen: the manager
    # must not encode where the frontend puts its list.
    assert "below" not in result.error
    store.close()


def test_the_bottom_message_still_says_nothing_older_when_that_is_true(
    tmp_path: Path,
) -> None:
    """The other half, and the reason the fix is a branch rather than new wording.

    On a FRESH install the bottom row is genesis, it is verified, the walk lands
    ON it, and there is genuinely nothing below it in the list. Rewording the
    bottom sentence for everyone would swap one falsehood for another: this person
    would be sent looking down a list that ends where they are standing."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=True)
    for theme in ("dark", "light"):
        store.set_setting("theme", theme)
        manager.mark_verified_working()

    while manager.restore_last_working().ok:
        pass
    result = manager.restore_last_working()

    assert manager.list()[-1]["reason"] == "genesis"
    assert result.ok is False
    assert result.error == (
        "You're back at the oldest setup Addison saved, so there's nothing further "
        "back to go to."
    )
    store.close()


def test_the_disk_arm_does_not_second_guess_the_bottom_of_the_walk(tmp_path: Path) -> None:
    """The sidecar arm is skipped on ``'bottom'``, and that gate is load-bearing.

    Every other non-ok outcome means the database's answer was absent or
    incomplete, so the disk is worth consulting. ``'bottom'`` is the one outcome
    that means the database answered COMPLETELY: there is nothing older among the
    setups it has seen working.

    Let the arm run there and it reads the whole sidecar directory, finds the
    ``pre_upgrade`` payload sitting below the walk's position, and — since nothing
    below is verified — applies it as a last resort.

    What makes that wrong HERE, and not on ``'none'`` (see
    ``test_the_first_click_on_an_upgraded_install_restores_and_says_it_is_unproven``,
    where the same arm applying the same payload is the intended behaviour): on
    ``'bottom'`` the user has proven configurations and has already walked back
    through them. Stepping them past their own proven setups into an unproven one,
    without asking, is a choice that belongs to them — so the row is named
    (``_OLDER_IN_THE_LIST``) rather than applied. On ``'none'`` there are no proven
    configurations to step past and refusing would leave the button dead."""
    store, manager = _upgraded_install(tmp_path)
    for theme in ("dark", "light"):
        store.set_setting("theme", theme)
        manager.mark_verified_working()
    assert manager.restore_last_working().ok
    settled = store.get_setting("theme")

    # The payload IS on disk and IS readable, so nothing but the gate is stopping
    # it — otherwise this test would pass for the wrong reason.
    on_disk = recover_payloads_from_disk(_sidecar_dir(manager))
    pre_upgrade = [p for p in on_disk if p["meta"]["reason"] == "pre_upgrade"]
    assert len(pre_upgrade) == 1
    assert pre_upgrade[0]["meta"]["verified_working"] in (0, False)

    result = manager.restore_last_working()

    assert result.ok is False
    # Untouched: the config the user walked back to is still the config they have.
    assert store.get_setting("theme") == settled
    store.close()


def test_the_first_click_on_an_upgraded_install_restores_and_says_it_is_unproven(
    tmp_path: Path,
) -> None:
    """The ``'none'`` outcome — the state EVERY upgraded install is in at its first
    click — restores ``pre_upgrade`` rather than refusing, and labels it honestly.

    This is the branch CLAUDE.md used to describe as having "no target until the
    first turn completes"; the walk has none, but the disk arm runs with
    ``require_verified=False`` and applies the payload. The behaviour is deliberate
    (``select_payload_to_restore``: *"nothing at all" is a worse answer than "the
    most recent settings I had, and I said so"*), and it had no test at all — so
    the next reader reconciling the doc with the code could have "fixed" it into a
    dead button on the commonest path and broken nothing.

    The load-bearing half is the SENTENCE. Restoring an unverified config is fine;
    reporting it in the ordinary "put you back on your last working setup" copy is
    the dishonesty this floor was written against.
    """
    # Built inline rather than through _upgraded_install: the settings have to be
    # in place BEFORE the manager exists, because `pre_upgrade` is captured during
    # construction and is a copy of whatever the user already had.
    store = Store(tmp_path / "addison.sqlite3")
    store.set_setting("active_profile", "simple")
    store.set_setting("theme", "light")
    store.insert_skill(id="from-before", name="From before", instructions="an older note",
                       enabled=True, created_at=1)
    # What makes this an upgraded install is the fact, not the note: this launch
    # did not create the database. Same as _upgraded_install.
    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=False)
    assert [(r["reason"], bool(r["verified_working"])) for r in manager.list()] == [
        ("pre_upgrade", False)
    ]

    store.set_setting("theme", "dark")       # the change the user wants undone
    result = manager.restore_last_working()

    assert result.ok is True
    assert store.get_setting("theme") == "light"
    # Never dressed up as a verified restore.
    assert result.detail == (
        "Addison couldn't find a setup it had seen working, so it went back to the "
        "most recent settings it had saved instead. Have a look and check things are "
        "how you want them. Your chats and your saved keys weren't touched."
    )
    assert "last working setup" not in (result.detail or "")
    store.close()


def test_the_walk_remembers_where_it_got_to_across_a_restart(tmp_path: Path) -> None:
    """Held in memory alone, the walk's position dies with the process — and the
    next launch hands the user back the config they escaped an hour earlier. So
    it is written to disk, on the ``pre_restore`` row the restore itself takes."""
    db = tmp_path / "addison.sqlite3"
    store = Store(db)
    manager = SnapshotManager(store=store, clock=_Clock())
    for marker in ("one", "two", "three"):
        store.set_setting("marker", marker)
        manager.mark_verified_working()

    assert manager.restore_last_working().ok
    assert store.get_setting("marker") == "two"
    store.close()

    # Addison is closed and reopened. Same database, same sidecars, brand-new
    # manager with no memory of anything. The clock carries ON — a relaunch does
    # not rewind the wall clock, and a fixture that restarts it shuffles the row
    # order enough to make a broken walk look right.
    relaunched = Store(db)
    after = SnapshotManager(store=relaunched, clock=_Clock(start=2_000_000))
    after.mark_verified_working()            # the first turn of the new session

    assert after.restore_last_working().ok
    assert relaunched.get_setting("marker") == "one"
    relaunched.close()


def test_a_damaged_candidate_list_does_not_undo_a_rollback_in_progress(store: Store) -> None:
    """The user is two clicks into a rollback when the verified-rows query starts
    failing, and the sidecar arm takes over.

    That arm reads the WHOLE directory, so unless it is told where the walk has
    got to it applies the newest payload it likes the look of — which is the setup
    the user has spent two clicks escaping. It then reports the ordinary success
    sentence while doing it, and every click afterwards flips between the same two
    setups forever, leaving everything older unreachable."""
    manager = _manager(store)
    for marker in ("one", "two", "three", "broken"):
        store.set_setting("marker", marker)
        manager.mark_verified_working()

    assert manager.restore_last_working().ok
    assert manager.restore_last_working().ok
    assert store.get_setting("marker") == "two"

    # A brand-new manager, as a relaunch would build: the position has to come off
    # the disk, exactly as it does in production.
    stranded = _manager(_VerifiedRefsAreDamaged(store), snapshot_dir=_sidecar_dir(manager))
    result = stranded.restore_last_working()

    assert result.ok, result.error
    assert store.get_setting("marker") == "one", (
        "the click walked FORWARD past the walk position, into the setup the user "
        "was escaping"
    )


def test_a_damaged_candidate_list_is_not_reported_as_nothing_saved(
    store: Store, tmp_path: Path
) -> None:
    """The query that lists proven setups raising means "Addison couldn't read
    them", not "you haven't got any". The reassuring sentence is the dangerous one
    here too: it sends a user whose Settings list is full of restore points off
    looking for one they already have."""
    manager = _manager(store)
    store.set_setting("marker", "one")
    manager.mark_verified_working()

    # Nothing on disk either, so the sidecar arm has nothing to offer and the
    # sentence is all the user gets.
    stranded = _manager(_VerifiedRefsAreDamaged(store), snapshot_dir=tmp_path / "not-here")
    result = stranded.restore_last_working()

    assert result.ok is False
    assert result.error == (
        "Addison couldn't read the setups it saved for you. Try picking one from "
        "the list of restore points."
    )


def test_restoring_an_unverified_point_by_id_does_not_send_the_next_click_forward(
    store: Store,
) -> None:
    """A restore point picked out of the Settings list is a position like any
    other, and ``restore()`` records it like any other.

    Unverified rows are not in the verified list the walk reads, so a walk that
    looks for its position only there finds nothing, starts again from the top and
    hands back the newest proven config — the broken one the user has just stepped
    away from. It self-corrects on the following click, which makes it a small
    defect rather than a broken floor, but the promise is that each click steps
    BACK and this one goes the other way."""
    manager = _manager(store)
    store.set_setting("marker", "good")
    manager.mark_verified_working()
    store.set_setting("marker", "midway")
    saved = manager.capture(trigger="on_command", reason="user_request")   # unverified
    store.set_setting("marker", "broken")
    manager.mark_verified_working()          # the broken config answered a turn

    assert manager.restore(saved.id).ok
    assert store.get_setting("marker") == "midway"

    assert manager.restore_last_working().ok
    assert store.get_setting("marker") == "good", (
        "the click after a targeted restore walked FORWARD into the broken setup"
    )


def test_the_walk_position_survives_a_relaunch_when_the_pre_restore_row_is_lost(
    tmp_path: Path,
) -> None:
    """The position rides on the ``pre_restore`` row the restore captures, and
    that capture is best-effort on purpose — a restore must never be blocked by a
    snapshot that will not write. So a full disk left the position in memory
    alone, and the next launch walked the user forward into the setup they had
    escaped. It is written down beside the sidecars as well, after the config has
    actually landed."""
    db = tmp_path / "addison.sqlite3"
    store = Store(db)
    manager = SnapshotManager(store=store, clock=_Clock())
    for marker in ("one", "two", "broken"):
        store.set_setting("marker", marker)
        manager.mark_verified_working()

    # The restore lands, but nothing about it can be written into the database.
    full_disk = SnapshotManager(store=_CannotCapture(store), clock=_Clock())
    assert full_disk.restore_last_working().ok
    assert store.get_setting("marker") == "two"
    assert [r for r in manager.list() if r["reason"] == "pre_restore"] == []
    store.close()

    # Addison is closed and reopened: same database, same sidecars, a manager with
    # no memory of anything.
    relaunched = Store(db)
    after = SnapshotManager(store=relaunched, clock=_Clock(start=2_000_000))
    after.mark_verified_working()            # the first turn of the new session

    assert after.restore_last_working().ok
    assert relaunched.get_setting("marker") == "one", (
        "the relaunch forgot where the rollback had got to and walked forward"
    )
    relaunched.close()


def test_last_working_target_matches_what_restore_last_working_does(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    store.set_setting("marker", "two")

    target = manager.last_working_target()
    assert target is not None
    assert target["reason"] == "turn_verified"
    assert target["reason_label"] == REASONS["turn_verified"]

    assert manager.restore_last_working().snapshot_id == target["id"]


def test_restore_into_a_more_permissive_profile_is_disclosed(store: Store) -> None:
    """A restore can move the user between profiles and therefore between policy
    modes. It never happens silently."""
    manager = _manager(store, mode_ref=lambda: "open")
    store.set_setting("active_profile", "developer")
    manager.mark_verified_working()
    store.set_setting("active_profile", "simple")

    target = manager.last_working_target()
    assert target is not None
    assert target["profile_change"] == (
        "This restore point was saved in Developer mode, so Addison will switch "
        "back to Developer."
    )
    assert manager.restore_last_working().profile_change == target["profile_change"]


def test_restore_to_genesis_says_it_is_a_fresh_install(store: Store) -> None:
    manager = _manager(store)
    genesis = manager.list()[0]
    store.insert_skill(id="s1", name="Note", instructions="x", enabled=True, created_at=100)

    result = manager.restore(genesis["id"])

    assert result.ok, result.error
    assert result.detail.startswith(
        "This is Addison as it was first installed, so your services, notes, "
        "widgets and routines are cleared."
    )
    assert store.list_skills() == []


# --- genesis, verification, capture ----------------------------------------


def test_genesis_snapshot_is_written_on_first_build_and_only_once(store: Store) -> None:
    _manager(store)
    _manager(store)
    genesis = [r for r in _manager(store).list() if r["reason"] == "genesis"]
    assert len(genesis) == 1
    assert genesis[0]["undeletable"] is True or genesis[0]["undeletable"] == 1
    assert genesis[0]["verified_working"] is True or genesis[0]["verified_working"] == 1


def test_an_established_install_is_not_told_it_is_a_fresh_one(tmp_path: Path) -> None:
    """The bottom row is written whenever the table is empty — which is true for
    every install that predates this subsystem, the first time it launches. On
    that path it is a copy of whatever the user has RIGHT NOW, up to and
    including the broken setup they are about to need rescuing from.

    Written as genesis it is a permanent row labelled "as first installed" whose
    restore copy promises to clear exactly the things it puts back, marked proven
    when nothing was proven, and able to drop the user into Developer — and
    therefore OPEN mode — from the guaranteed bottom of the rollback walk."""
    store = Store(tmp_path / "addison.sqlite3")
    # Months of use, and the config is broken at the moment of the upgrade.
    store.set_setting("active_profile", "developer")
    store.set_setting("primary_model", "BROKEN-MODEL-DOES-NOT-EXIST")
    store.insert_skill(id="bad", name="bad", instructions="always refuse",
                       enabled=True, created_at=1)

    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=False)

    row = manager.list()[0]
    assert row["reason"] == "pre_upgrade"
    assert row["reason_label"] == "Your setup before this update"
    # Permanent, so there is always a way back to how things were before the
    # update — but NOT proven, because nothing has run against it here.
    assert row["undeletable"] in (True, 1)
    assert row["verified_working"] in (False, 0)
    assert manager.last_working_target() is None

    store.set_setting("primary_model", "something-else")
    result = manager.restore(row["id"])
    assert result.ok, result.error
    assert result.detail == (
        "This is how everything was set up when this version of Addison first "
        "started. Your chats and your saved keys weren't touched."
    )
    assert manager.delete(row["id"]) == (
        False,
        "That's how your setup was when this version of Addison first started. It "
        "stays, so there's always a way back.",
    )
    store.close()


def test_a_genuinely_fresh_install_still_gets_genesis(tmp_path: Path) -> None:
    """The other half of the same decision. Addison seeds its own default widgets
    and writes its own first-run rows before the bottom row is taken, so a
    database that already holds them is still the one this launch created."""
    store = Store(tmp_path / "addison.sqlite3")
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", "simple")
    store.insert_widget(id="w1", spec_json='{"kind":"stat","source":"connections"}',
                        pinned=True, position=0, created_at=100)

    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=True)

    row = manager.list()[0]
    assert row["reason"] == "genesis"
    assert row["verified_working"] in (True, 1)
    store.close()


# --- the classification matrix ---------------------------------------------
#
# Which permanent bottom row a database gets, across the states a real install
# can actually be in. It is one bit with three consequences, none of them
# recoverable by the person it happens to: `genesis` is verified, so it is a
# legitimate one-click restore TARGET; its copy promises to clear services,
# notes, widgets and routines; and the row is undeletable three deep (the
# message check, the store's `AND undeletable = 0`, and a RAISE(ABORT) trigger).
# Get it wrong toward `genesis` and the floor hands back the configuration the
# person is escaping, says something false about it in both directions, and
# leaves a permanent restore point that lies.
#
# It used to be INFERRED from the payload — true only when provider_config,
# skills and routines were all empty and the profile was still Simple. Every
# scenario below whose name says "months of use" passed that test: all of
# app_settings and all of widgets were invisible to it, and conversations and
# messages are not in the payload at all. That is not an exotic install. It is
# the default state of the two people this app is for, who never connect a
# service (with no key, turns run on the Setup Assistant relay), never write a
# note, never save a routine and never leave Simple.
#
# So the matrix is driven by the FACT — did this launch create the database —
# and each case states which fact main.py would have read off the filesystem.


def _bottom_row(tmp_path: Path, *, created_the_database: bool | None, arrange=None) -> dict:
    """Build a store, let ``arrange`` put the scenario's state in it, construct a
    manager, and return the single permanent bottom row it wrote."""
    store = Store(tmp_path / "addison.sqlite3")
    if arrange is not None:
        arrange(store)
    manager = SnapshotManager(
        store=store,
        snapshot_dir=tmp_path / "snapshots",
        clock=_Clock(),
        created_the_database=created_the_database,
    )
    rows = manager.list()
    store.close()
    assert len(rows) == 1, "the bottom row is written exactly once"
    return rows[0]


def _setup_assistant_ran(store: Store) -> None:
    """§4.6: with no key at all, the first turns run on the relay. They leave a
    conversation and messages — and NO provider row, because
    ``upsert_provider_config`` has exactly one caller, ``provider.connect``."""
    store.create_conversation(id="c1", title="Getting started",
                              provider_id="setup_assistant", started_at=100)
    store.insert_message(id="m1", conversation_id="c1", role="user",
                         content="hello", created_at=100)


def _default_widgets_seeded(store: Store) -> None:
    """What ``main.py._seed_default_widgets`` leaves behind, in the order the
    real one writes it — Addison's own first-run state, not a person's."""
    for position, spec in enumerate(
        ({"kind": "stat", "source": "connections", "title": "Connections"},
         {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}),
    ):
        store.insert_widget(id=f"seed-{position}", spec_json=json.dumps(spec),
                            pinned=True, position=position, created_at=100)
    store.set_setting("widgets_seeded", "1")


def _months_of_quiet_use(store: Store) -> None:
    """THE DEFECT CASE. Everything a companion user accumulates and nothing the
    old inference could see: tuned settings, a widget they added, chats — no
    service, no note, no routine, still on Simple."""
    _default_widgets_seeded(store)
    _setup_assistant_ran(store)
    store.set_setting("active_profile", "simple")
    store.set_setting("theme", "dark")
    store.set_setting("selected_model", "claude-model-that-was-retired")
    store.insert_widget(id="mine", spec_json=json.dumps(
        {"kind": "stat", "source": "provider_latency", "title": "My latency card"}),
        pinned=True, position=2, created_at=200)


def test_a_truly_fresh_install_gets_genesis(tmp_path: Path) -> None:
    """Nothing in the database, and the file did not exist a moment ago."""
    row = _bottom_row(tmp_path, created_the_database=True)
    assert (row["reason"], bool(row["verified_working"])) == ("genesis", True)
    assert row["undeletable"] in (True, 1)


def test_a_fresh_install_whose_first_turns_ran_on_the_relay_gets_genesis(
    tmp_path: Path,
) -> None:
    """Chats are not configuration. A first-run conversation on the Setup
    Assistant relay is still the install this launch created."""
    row = _bottom_row(tmp_path, created_the_database=True, arrange=_setup_assistant_ran)
    assert (row["reason"], bool(row["verified_working"])) == ("genesis", True)


def test_a_fresh_install_with_the_seeded_widgets_gets_genesis(tmp_path: Path) -> None:
    """Addison seeds its own rail before the bottom row is taken (see
    ``test_genesis_holds_the_widgets_addison_seeds_on_first_run``), so the seeds
    are part of the first-run state, never evidence of a person."""
    row = _bottom_row(tmp_path, created_the_database=True, arrange=_default_widgets_seeded)
    assert (row["reason"], bool(row["verified_working"])) == ("genesis", True)


def test_months_of_quiet_use_is_never_called_a_fresh_install(tmp_path: Path) -> None:
    """THE DEFECT, as a regression test.

    An established companion install carrying none of the four signals the old
    inference looked for. It was classified ``genesis``, ``verified_working``,
    undeletable — a permanent one-click restore target promising to be "Addison
    as first installed" while holding the retired model and the widgets the
    person would be clicking Restore to get away from.

    The three failures the one bit caused are asserted here together, because
    fixing the slug alone would leave two of them: it must not be the walk's
    target, and the copy must not promise to clear what it puts back."""
    row = _bottom_row(tmp_path, created_the_database=False, arrange=_months_of_quiet_use)

    assert row["reason"] == "pre_upgrade"
    assert row["verified_working"] in (False, 0)
    assert row["reason_label"] == "Your setup before this update"
    # Still permanent — the way back does not depend on getting the label right.
    assert row["undeletable"] in (True, 1)


def test_an_install_the_user_emptied_is_not_a_fresh_one(tmp_path: Path) -> None:
    """Deleting your services, notes and routines does not roll the clock back to
    the day you installed Addison, and the row that says otherwise cannot be
    deleted afterwards. The old inference read this database as brand new."""

    def emptied(store: Store) -> None:
        _months_of_quiet_use(store)
        store.upsert_provider_config("anthropic", connected=True, added_at=1)
        store.insert_skill(id="s1", name="Tone", instructions="Be brief.",
                           enabled=True, created_at=1)
        # ...and then removes every one of them from the UI.
        store.delete_provider_config("anthropic")
        store.delete_skill("s1")

    row = _bottom_row(tmp_path, created_the_database=False, arrange=emptied)
    assert (row["reason"], bool(row["verified_working"])) == ("pre_upgrade", False)


def test_a_database_copied_from_a_backup_is_not_a_fresh_install(tmp_path: Path) -> None:
    """Restoring a backup, or moving to a new machine, puts an established
    database somewhere Addison has never run. The app is new here; the
    configuration is not, and it is the configuration this row holds."""
    row = _bottom_row(tmp_path, created_the_database=False, arrange=_months_of_quiet_use)
    assert (row["reason"], bool(row["verified_working"])) == ("pre_upgrade", False)


def test_an_install_we_cannot_classify_takes_the_cautious_road(tmp_path: Path) -> None:
    """No fact supplied — the CLI, a test, any caller that cannot know. There is
    exactly one safe answer and it is not ``genesis``: an unknown install must
    land where a known-established one does, or "could not find out" becomes a
    way to mint a verified, undeletable, permanently wrong restore target."""
    row = _bottom_row(tmp_path, created_the_database=None, arrange=_months_of_quiet_use)
    assert (row["reason"], bool(row["verified_working"])) == ("pre_upgrade", False)

    # And the same for a database with nothing in it at all: absent the fact, an
    # empty database is not evidence of a fresh install either.
    second = tmp_path / "second"
    second.mkdir()
    empty = _bottom_row(second, created_the_database=None)
    assert (empty["reason"], bool(empty["verified_working"])) == ("pre_upgrade", False)


def test_the_honest_branch_says_only_what_pre_upgrade_actually_restores(
    tmp_path: Path,
) -> None:
    """The copy on the branch the defect case now takes, checked against what the
    restore DOES rather than against itself.

    ``pre_upgrade`` puts back the configuration that was there when this version
    first started, so its sentence claims exactly that and claims nothing about
    clearing anything. The genesis sentence — "your services, notes, widgets and
    routines are cleared" — is the one that was false here, and it is false in
    both directions at once: it promises a clearing that does not happen, and it
    hands back the very settings the person is trying to escape."""
    store = Store(tmp_path / "addison.sqlite3")
    _months_of_quiet_use(store)
    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=False)
    row = manager.list()[0]

    # The person changes something and clicks the row in Settings.
    store.set_setting("theme", "light")
    store.insert_widget(id="later", spec_json=json.dumps(
        {"kind": "stat", "source": "connections", "title": "Later"}),
        pinned=True, position=9, created_at=300)
    result = manager.restore(row["id"])

    assert result.ok, result.error
    assert result.detail == (
        "This is how everything was set up when this version of Addison first "
        "started. Your chats and your saved keys weren't touched."
    )
    # What the sentence describes is what happened: the settings went back...
    assert store.get_setting("theme") == "dark"
    assert store.get_setting("selected_model") == "claude-model-that-was-retired"
    # ...the widget added since is gone, the ones from before are not...
    titles = [json.loads(w["spec_json"])["title"] for w in store.list_widgets()]
    assert "Later" not in titles
    assert "My latency card" in titles
    # ...and the chats it says it did not touch are untouched.
    assert store.list_conversations()[0]["id"] == "c1"
    store.close()


def test_genesis_really_does_clear_what_its_copy_promises(tmp_path: Path) -> None:
    """The genesis sentence promises services, notes, widgets and routines are
    cleared. On a database this launch created that is true by construction —
    there was nothing to clear — and this is what makes the two sentences safe to
    keep apart. Anything the person adds afterwards is what genesis removes."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = SnapshotManager(store=store, snapshot_dir=tmp_path / "snapshots",
                              clock=_Clock(), created_the_database=True)
    genesis = manager.list()[0]
    assert genesis["reason"] == "genesis"

    store.upsert_provider_config("anthropic", connected=True, added_at=1)
    store.insert_skill(id="s1", name="Tone", instructions="Be brief.",
                       enabled=True, created_at=1)
    store.insert_routine(id="r1", name="Morning", description="d",
                         plan_json={"steps": []},
                         created_from_conversation_id=None, created_at=1)
    store.insert_widget(id="w1", spec_json=json.dumps(
        {"kind": "stat", "source": "connections", "title": "Connections"}),
        pinned=True, position=0, created_at=1)

    result = manager.restore(genesis["id"])

    assert result.ok, result.error
    assert result.detail == (
        "This is Addison as it was first installed, so your services, notes, "
        "widgets and routines are cleared. Your chats and your saved keys "
        "weren't touched."
    )
    assert store.list_skills() == []
    assert store.list_widgets() == []
    assert store.list_routines() == []
    assert store.list_provider_configs() == []
    store.close()


def test_mark_verified_working_is_a_noop_when_the_config_is_unchanged(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    assert manager.mark_verified_working() is not None
    before = len(manager.list())
    for _ in range(100):
        assert manager.mark_verified_working() is None
    assert len(manager.list()) == before


def test_mark_verified_working_captures_when_the_config_changed(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    first = manager.mark_verified_working()
    store.set_setting("marker", "two")
    second = manager.mark_verified_working()
    assert first is not None and second is not None and first.id != second.id
    assert first.state_fingerprint != second.state_fingerprint


def test_mark_verified_working_never_raises(store: Store) -> None:
    """It fires on every successful turn, so it must never be able to convert a
    successful turn into an error."""
    manager = _manager(store)
    store.close()
    assert manager.mark_verified_working() is None


def test_restore_takes_a_pre_restore_snapshot_first(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    target = manager.mark_verified_working()
    assert target is not None
    store.set_setting("marker", "two")

    assert manager.restore(target.id).ok
    pre = [r for r in manager.list() if r["reason"] == "pre_restore"]
    assert len(pre) == 1
    # Clicking Restore twice is safe: the pre_restore row holds the state we
    # were in, so the recovery is itself reversible.
    assert manager.restore(pre[0]["id"]).ok
    assert store.get_setting("marker") == "two"


def test_restore_of_an_unknown_id_returns_a_plain_sentence(store: Store) -> None:
    manager = _manager(store)
    result = manager.restore("no-such-id")
    assert result.ok is False
    assert result.error == "That restore point isn't here any more."


def test_capture_survives_a_sidecar_write_failure(tmp_path: Path) -> None:
    store = Store(tmp_path / "addison.sqlite3")
    unwritable = tmp_path / "readonly"
    unwritable.mkdir(mode=0o500)
    try:
        manager = SnapshotManager(store=store, snapshot_dir=unwritable / "snapshots",
                                  clock=_Clock())
        snapshot = manager.capture(trigger="on_command", reason="user_request")
        assert store.get_config_snapshot(snapshot.id) is not None
    finally:
        unwritable.chmod(0o700)
        store.close()


def test_sidecar_is_disabled_for_an_in_memory_database() -> None:
    store = Store(":memory:")
    manager = SnapshotManager(store=store, clock=_Clock())
    snapshot = manager.capture(trigger="on_command", reason="user_request")
    assert store.get_config_snapshot(snapshot.id) is not None
    assert manager._snapshot_dir is None
    store.close()


def test_prune_runs_after_capture_and_bounds_ordinary_rows(tmp_path: Path) -> None:
    store = Store(tmp_path / "addison.sqlite3")
    clock = _Clock()
    manager = SnapshotManager(store=store, clock=clock, created_the_database=True)
    for i in range(sm.KEEP_LAST + 12):
        store.set_setting("marker", f"v{i}")
        manager.capture(trigger="auto", reason="other")
        clock.now += 86_400 * 2          # push each row past the age floor
    rows = manager.list()
    assert len(rows) <= sm.KEEP_LAST + 2      # + genesis + the newest verified pair
    assert any(r["reason"] == "genesis" for r in rows)
    store.close()


# --- deletion, anchors, G4 --------------------------------------------------


def test_delete_refuses_an_anchor_with_a_plain_sentence(store: Store) -> None:
    manager = _manager(store)
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    anchor = manager.mint_anchor()
    assert anchor is not None

    ok, error = manager.delete(anchor.id)

    assert ok is False
    assert error == (
        "That restore point is permanent — it was saved when a safety setting "
        "was turned off, so it stays."
    )
    assert store.get_config_snapshot(anchor.id) is not None


def test_delete_refuses_genesis_with_its_own_sentence(store: Store) -> None:
    manager = _manager(store)
    genesis = manager.list()[0]
    ok, error = manager.delete(genesis["id"])
    assert ok is False
    assert error == (
        "That's Addison as it was first installed. It stays, so there's always a way back."
    )


def test_delete_removes_an_ordinary_snapshot_and_its_sidecar(store: Store) -> None:
    manager = _manager(store)
    snapshot = manager.capture(trigger="on_command", reason="user_request")
    sidecar = _sidecar_dir(manager) / f"{snapshot.id}.json"
    assert sidecar.exists()

    assert manager.delete(snapshot.id) == (True, None)
    assert store.get_config_snapshot(snapshot.id) is None
    assert not sidecar.exists()


def test_a_sidecar_left_behind_by_a_delete_does_not_come_back(store: Store) -> None:
    """Removing a snapshot's file is best-effort and silent, so one that fails
    leaves the payload on disk with no row. That is not cosmetic: the cold-start
    path reads the whole directory, so an orphan is a restore point the user
    deleted rising from the dead the next time the database has to be rebuilt."""
    manager = _manager(store)
    orphaned = manager.capture(trigger="on_command", reason="user_request")
    store.set_setting("marker", "changed")
    ordinary = manager.capture(trigger="on_command", reason="user_request")
    # The row goes, the unlink "fails": exactly the state a silent failure leaves.
    store._conn.execute("DELETE FROM config_snapshots WHERE id = ?", (orphaned.id,))
    store._conn.commit()
    assert (_sidecar_dir(manager) / f"{orphaned.id}.json").exists()

    assert manager.delete(ordinary.id) == (True, None)

    assert not (_sidecar_dir(manager) / f"{orphaned.id}.json").exists()


def test_the_sweep_leaves_every_file_alone_when_the_table_reads_empty(store: Store) -> None:
    """An empty row list means a database being rebuilt or one that has lost its
    rows — both states where the sidecars are the only surviving copy of the way
    back. Reading "no rows" as "every file is an orphan" would delete the entire
    rollback history from inside the machinery whose whole job is to still be
    there."""
    manager = _manager(store)
    manager.capture(trigger="on_command", reason="user_request")
    snapshot_dir = _sidecar_dir(manager)
    # Built first, then the files are counted: construction writes a bottom row
    # of its own, and what this test is about is what the SWEEP does.
    stranded = _manager(_EmptyTable(store), snapshot_dir=snapshot_dir)
    before = sorted(p.name for p in snapshot_dir.glob("*.json"))
    assert len(before) > 1

    stranded._sweep_sidecars()

    assert sorted(p.name for p in snapshot_dir.glob("*.json")) == before


class _EmptyTable:
    """A store whose ``config_snapshots`` reads as empty while the files on disk
    are intact — a database mid-rebuild, or one that lost its rows."""

    def __init__(self, real: Store) -> None:
        self._real = real
        self.db_path = real.db_path

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def list_config_snapshots(self) -> list[dict]:
        return []


def test_mint_anchor_copies_the_last_verified_payload_byte_for_byte(store: Store) -> None:
    """C10: an anchor is *of the last verified state*, never a capture of the
    state the user is in the act of weakening."""
    manager = _manager(store)
    store.set_setting("marker", "known-good")
    verified = manager.mark_verified_working()
    assert verified is not None
    store.set_setting("marker", "being-weakened")

    anchor = manager.mint_anchor()

    assert anchor is not None
    assert anchor.id != verified.id
    assert anchor.undeletable is True
    assert anchor.verified_working is True
    assert anchor.state_fingerprint == verified.state_fingerprint
    assert anchor.payload_version == verified.payload_version
    # The tables are the copied ones, not the current (weakened) ones. `meta` is
    # deliberately NOT copied — it is the only backup of a row's flags, so an
    # anchor carrying the source row's undeletable:0 would come back from a
    # sidecar rebuild demoted.
    copied = json.loads(anchor.state_blob)
    assert copied["tables"] == json.loads(verified.state_blob)["tables"]
    assert copied["meta"]["undeletable"] == 1
    assert copied["meta"]["id"] == anchor.id


def test_mint_anchor_without_a_shell_still_mints_undeletably(store: Store) -> None:
    """A wedged IPC round-trip must never be able to prevent a safety anchor."""
    def wedged() -> dict:
        raise RuntimeError("no shell")

    manager = _manager(store, app_build_ref=wedged)
    store.set_setting("marker", "one")
    manager.mark_verified_working()

    anchor = manager.mint_anchor()

    assert anchor is not None
    assert anchor.undeletable is True
    assert anchor.captures_binary is False
    assert anchor.binary_ref is None


def test_mint_anchor_records_the_build_it_was_minted_on(store: Store) -> None:
    manager = _manager(
        store, app_build_ref=lambda: {"version": "0.1.0", "identifier": "app.addison.desktop"}
    )
    store.set_setting("marker", "one")
    manager.mark_verified_working()

    anchor = manager.mint_anchor()

    assert anchor is not None
    assert anchor.captures_binary is True
    assert anchor.binary_ref is not None
    assert json.loads(anchor.binary_ref) == {
        "version": "0.1.0",
        "identifier": "app.addison.desktop",
    }


def test_mint_anchor_returns_none_when_no_verified_row_exists(tmp_path: Path) -> None:
    """Step 2's guard toggle must refuse to weaken anything when this is None."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = SnapshotManager(store=store, clock=_Clock())
    store._conn.execute("UPDATE config_snapshots SET verified_working = 0")
    store._conn.commit()

    assert manager.mint_anchor() is None
    store.close()


def test_anchor_survives_prune_and_delete_and_a_reopen(tmp_path: Path) -> None:
    """The full G4 lifecycle."""
    db = tmp_path / "addison.sqlite3"
    store = Store(db)
    clock = _Clock()
    manager = SnapshotManager(store=store, clock=clock)
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    anchor = manager.mint_anchor()
    assert anchor is not None

    for i in range(sm.KEEP_LAST + 5):
        store.set_setting("marker", f"v{i}")
        manager.capture(trigger="auto", reason="other")
        clock.now += 86_400 * 2
    assert manager.delete(anchor.id)[0] is False
    assert store.get_config_snapshot(anchor.id) is not None

    store.close()
    reopened = Store(db)
    assert reopened.get_config_snapshot(anchor.id) is not None
    reopened.close()


def test_anchors_survive_a_rebuild_from_sidecars_alone(tmp_path: Path) -> None:
    """G4: rebuilding the database from sidecars restores anchor-ness,
    verified-ness and the build reference. The recovery path cannot demote an
    anchor — there is no code path named "delete" anywhere in this hazard."""
    db = tmp_path / "addison.sqlite3"
    store = Store(db)
    manager = SnapshotManager(
        store=store,
        clock=_Clock(),
        app_build_ref=lambda: {"version": "0.1.0", "identifier": "app.addison.desktop"},
    )
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    anchor = manager.mint_anchor()
    assert anchor is not None
    snapshot_dir = _sidecar_dir(manager)
    store.close()

    fresh = Store(tmp_path / "rebuilt.sqlite3")
    written = rebuild_rows_from_payloads(fresh, recover_payloads_from_disk(snapshot_dir))

    assert written >= 1
    rebuilt = fresh.get_config_snapshot(anchor.id)
    assert rebuilt is not None
    assert rebuilt.undeletable is True
    assert rebuilt.verified_working is True
    assert rebuilt.captures_binary is True
    assert rebuilt.binary_ref is not None
    assert json.loads(rebuilt.binary_ref)["version"] == "0.1.0"
    assert rebuilt.state_fingerprint == anchor.state_fingerprint
    fresh.close()


def _three_proven_configs_in_one_second(tmp_path: Path) -> tuple[Path, list[dict]]:
    """A store with three distinct proven configs whose snapshots all share a
    second, then destroyed — leaving only the sidecars, which is what a cold
    rebuild has to work from."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = SnapshotManager(store=store, clock=_one_second)
    for name in ("one", "two", "three"):
        store.set_setting("marker", name)
        manager.mark_verified_working()
    snapshot_dir = _sidecar_dir(manager)
    store.close()
    return snapshot_dir, recover_payloads_from_disk(snapshot_dir)


def test_a_rebuilt_table_keeps_the_order_the_payloads_came_in(tmp_path: Path) -> None:
    """Rows are read back ``created_at DESC, rowid DESC``, so inserting
    newest-first makes rowid climb while time falls and the same-second tiebreak
    then points at the OLDEST row. The table comes back inside out."""
    _, payloads = _three_proven_configs_in_one_second(tmp_path)
    fresh = Store(tmp_path / "rebuilt.sqlite3")

    rebuild_rows_from_payloads(fresh, payloads)

    assert [row["id"] for row in fresh.list_config_snapshots()] == [
        payload["meta"]["id"] for payload in payloads
    ]
    fresh.close()


def test_the_floor_still_walks_one_step_back_after_a_cold_rebuild(tmp_path: Path) -> None:
    """The behaviour the ordering exists for. With the table rebuilt inside out,
    one click skipped every proven config in between and dropped the user
    straight to the fresh install at the bottom."""
    _, payloads = _three_proven_configs_in_one_second(tmp_path)
    fresh = Store(tmp_path / "rebuilt.sqlite3")
    rebuild_rows_from_payloads(fresh, payloads)
    fresh.apply_config_state(payloads[0]["tables"])       # land on the newest config
    assert fresh.get_setting("marker") == "three"
    manager = SnapshotManager(store=fresh, snapshot_dir=tmp_path / "unused", clock=_one_second)

    result = manager.restore_last_working()

    assert result.ok, result.error
    assert fresh.get_setting("marker") == "two", (
        "one click should step back ONE proven configuration, not skip to the bottom"
    )
    fresh.close()


def test_recover_payloads_from_disk_needs_no_store(tmp_path: Path) -> None:
    """The cold-start path works with a directory and nothing else."""
    store = Store(tmp_path / "addison.sqlite3")
    manager = SnapshotManager(store=store, clock=_Clock())
    store.set_setting("marker", "one")
    manager.capture(trigger="on_command", reason="user_request")
    snapshot_dir = _sidecar_dir(manager)
    store.close()

    payloads = recover_payloads_from_disk(snapshot_dir)

    assert len(payloads) >= 2
    # Newest first, and each is self-describing: no schema, no sqlite3.
    assert [p["captured_at"] for p in payloads] == sorted(
        (p["captured_at"] for p in payloads), reverse=True
    )
    assert set(_CAPTURED_TABLES) <= set(payloads[0]["tables"])


def test_every_payload_records_the_moment_it_was_captured(store: Store) -> None:
    """Whole seconds are not enough to order by. Several captures land in one
    second constantly — a hook's pre-change snapshot and the verified row that
    follows it — so each payload carries the nanosecond it was written too."""
    manager = _manager(store, clock=_one_second)
    first = manager.capture(trigger="on_command", reason="user_request")
    store.set_setting("marker", "changed")
    second = manager.capture(trigger="on_command", reason="user_request")

    stamps = [json.loads(s.state_blob)["captured_at_ns"] for s in (first, second)]

    assert json.loads(first.state_blob)["captured_at"] == (
        json.loads(second.state_blob)["captured_at"]
    ), "the fixture is meant to put both captures in the same second"
    assert stamps[1] > stamps[0]


def test_sidecars_captured_in_the_same_second_come_back_newest_first(tmp_path: Path) -> None:
    """Ordering used to fall through to ``sorted(os.listdir())`` on a tie — uuid4
    lexical order, i.e. a coin toss. The same directory could answer "your newest
    saved setup" differently on consecutive runs, and 2 runs in 5 restored an
    empty config while reporting success. A restore that is a coin toss is not a
    floor.

    The filenames here run oldest-to-newest alphabetically, so a sort that
    ignores the nanosecond returns them exactly backwards."""
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    for name, nanos, marker in (
        ("a.json", 111, "oldest"),
        ("b.json", 222, "middle"),
        ("c.json", 333, "newest"),
    ):
        payload = _minimal_payload()
        payload["captured_at"] = 1_700_000_000        # all in the SAME second
        payload["captured_at_ns"] = nanos
        payload["tables"]["app_settings"] = [
            {"key": "marker", "value": marker, "updated_at": 1_700_000_000}
        ]
        (snapshot_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    recovered = recover_payloads_from_disk(snapshot_dir)

    assert [p["tables"]["app_settings"][0]["value"] for p in recovered] == [
        "newest",
        "middle",
        "oldest",
    ]


def test_a_payload_written_before_the_stamp_existed_sorts_as_older(tmp_path: Path) -> None:
    """An upgrade must not reorder somebody's existing rollback history into
    nonsense. A payload with no nanosecond defaults to zero, which puts it below
    its same-second siblings — the safe direction."""
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    old = _minimal_payload()
    old["captured_at"] = 1_700_000_000
    old["tables"]["app_settings"] = [{"key": "marker", "value": "old", "updated_at": 1}]
    new = _minimal_payload()
    new["captured_at"] = 1_700_000_000
    new["captured_at_ns"] = 5
    new["tables"]["app_settings"] = [{"key": "marker", "value": "new", "updated_at": 1}]
    # "a" sorts first, so only the stamp can put the newer one on top.
    (snapshot_dir / "a-no-stamp.json").write_text(json.dumps(old), encoding="utf-8")
    (snapshot_dir / "b-stamped.json").write_text(json.dumps(new), encoding="utf-8")

    recovered = recover_payloads_from_disk(snapshot_dir)

    assert [p["tables"]["app_settings"][0]["value"] for p in recovered] == ["new", "old"]


def test_recover_payloads_from_disk_skips_undecodable_files(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "junk.json").write_text("{ not json", encoding="utf-8")
    (snapshot_dir / "notes.txt").write_text("ignored", encoding="utf-8")
    assert recover_payloads_from_disk(snapshot_dir) == []
    assert recover_payloads_from_disk(tmp_path / "nope") == []


# --- payload decoding -------------------------------------------------------


def test_payload_version_newer_than_this_build_is_rejected() -> None:
    """An older build must never half-apply a newer build's payload."""
    payload = _minimal_payload()
    payload["version"] = sm.PAYLOAD_VERSION + 1
    assert sm._decode_payload(json.dumps(payload)) is None


def test_decode_is_strict_about_missing_tables_and_bad_row_types() -> None:
    assert sm._decode_payload("{ not json") is None
    assert sm._decode_payload("[]") is None
    assert sm._decode_payload(json.dumps({"tables": {}})) is None

    truncated = _minimal_payload()
    del truncated["tables"]["skills"]
    assert sm._decode_payload(json.dumps(truncated)) is None

    not_a_list = _minimal_payload()
    not_a_list["tables"]["skills"] = {"id": "s1"}
    assert sm._decode_payload(json.dumps(not_a_list)) is None

    undeclared_column = _minimal_payload()
    undeclared_column["tables"]["skills"] = [{"id": "s1", "sneaky": "x"}]
    assert sm._decode_payload(json.dumps(undeclared_column)) is None

    bad_value = _minimal_payload()
    bad_value["tables"]["skills"] = [{"id": {"nested": True}}]
    assert sm._decode_payload(json.dumps(bad_value)) is None


def test_a_payload_missing_a_later_added_column_still_decodes() -> None:
    """A column added by a future build must not invalidate the entire rollback
    history at upgrade time — SQLite applies the declared default instead."""
    payload = _minimal_payload()
    payload["tables"]["skills"] = [{"id": "s1", "name": "Note"}]
    assert sm._decode_payload(json.dumps(payload)) is not None


def _minimal_payload() -> dict:
    return {
        "version": sm.PAYLOAD_VERSION,
        "captured_at": 4102444800,
        "meta": {"id": "x", "trigger": "auto", "reason": "other"},
        "tables": {table: [] for table in _CAPTURED_TABLES},
    }


# --- choosing a payload: the one function every restore path shares ---------


def _sidecar(reason: str, *, verified: bool, fingerprint: str, marker: str) -> dict:
    payload = _minimal_payload()
    payload["meta"] = {
        "id": f"id-{marker}",
        "trigger": "auto",
        "reason": reason,
        "state_fingerprint": fingerprint,
        "verified_working": int(verified),
    }
    payload["tables"]["app_settings"] = [{"key": "marker", "value": marker, "updated_at": 1}]
    return payload


def test_selecting_a_payload_prefers_a_setup_that_provably_ran() -> None:
    """Newest-first is not the rule — newest PROVEN-first is. The newest file is
    almost always the automatic capture taken just before the change that broke
    things, and it was never verified against anything."""
    payloads = [
        _sidecar("mode_switch", verified=False, fingerprint="f-broken", marker="broken"),
        _sidecar("provider_connect", verified=False, fingerprint="f-good", marker="stale"),
        _sidecar("turn_verified", verified=True, fingerprint="f-good", marker="good"),
    ]

    chosen, is_verified = select_payload_to_restore(payloads)

    assert is_verified is True
    assert chosen is not None and chosen["meta"]["id"] == "id-good"


def test_selecting_a_payload_never_picks_the_config_already_running() -> None:
    """Applying it would change zero bytes, so it is never a legitimate target
    however it is labelled."""
    payloads = [
        _sidecar("turn_verified", verified=True, fingerprint="f-now", marker="current"),
        _sidecar("turn_verified", verified=True, fingerprint="f-before", marker="earlier"),
    ]

    chosen, is_verified = select_payload_to_restore(payloads, current_fingerprint="f-now")

    assert is_verified is True
    assert chosen is not None and chosen["meta"]["id"] == "id-earlier"


def test_selecting_a_payload_admits_when_nothing_was_ever_proven() -> None:
    """The unverified fallback exists because nothing at all is a worse answer —
    but the caller is told, so it can say so instead of claiming it went back to
    the last working setup."""
    payloads = [
        _sidecar("mode_switch", verified=False, fingerprint="f-new", marker="newest"),
        _sidecar("user_request", verified=False, fingerprint="f-old", marker="older"),
    ]

    chosen, is_verified = select_payload_to_restore(payloads)

    assert is_verified is False
    assert chosen is not None and chosen["meta"]["id"] == "id-newest"
    assert select_payload_to_restore([]) == (None, False)


def test_restore_reports_a_build_mismatch_in_plain_language(store: Store) -> None:
    running = {"version": "0.1.0", "identifier": "app.addison.desktop"}
    manager = _manager(store, app_build_ref=lambda: running)
    store.set_setting("marker", "one")
    manager.mark_verified_working()
    anchor = manager.mint_anchor()
    assert anchor is not None
    running = {"version": "0.2.0", "identifier": "app.addison.desktop"}
    store.set_setting("marker", "two")

    result = manager.restore(anchor.id)

    assert result.ok, result.error
    assert result.binary_mismatch == (
        "This restore point was saved on Addison 0.1.0 and you're running 0.2.0. "
        "Your settings went back; the app itself didn't change."
    )


# --- G1: no key material, owner-only sidecars ------------------------------


def test_snapshot_payload_never_contains_key_material(store: Store) -> None:
    """G1. Key-shaped material sitting in a NON-captured table never reaches a
    payload, and no captured table is one that could hold a key."""
    manager = _manager(store)
    store._conn.execute(
        "INSERT INTO memory_facts (id, fact, created_at) VALUES (?, ?, ?)",
        ("f1", "sk-ant-api03-SECRETSECRETSECRET", 100),
    )
    store._conn.execute(
        "INSERT INTO device_identity (id, device_id, created_at) VALUES (?, ?, ?)",
        (1, "sk-ant-api03-ALSOSECRET", 100),
    )
    store._conn.commit()
    # A base_url is the one captured field a user can legitimately put a
    # credential into — it IS captured (removing it would break restore), which
    # is exactly why the sidecar is 0600. See test_sidecar_files_are_owner_only.
    store.upsert_provider_config("custom", connected=True,
                                 base_url="https://user:pw@example.test/v1")

    snapshot = manager.capture(trigger="on_command", reason="user_request")

    assert "sk-ant-api03" not in snapshot.state_blob
    assert "memory_facts" not in snapshot.state_blob
    assert set(json.loads(snapshot.state_blob)["tables"]) == set(_CAPTURED_TABLES)


def test_sidecar_files_are_owner_only(store: Store) -> None:
    """G1: 0700 on the directory and 0600 on each file, set explicitly rather
    than inherited from a umask that is world-readable on a typical macOS home."""
    manager = _manager(store)
    snapshot = manager.capture(trigger="on_command", reason="user_request")
    sidecar = _sidecar_dir(manager) / f"{snapshot.id}.json"

    assert os.stat(_sidecar_dir(manager)).st_mode & 0o777 == 0o700
    assert os.stat(sidecar).st_mode & 0o777 == 0o600


# --- C6: snapshots are never mode-hidden ------------------------------------


def test_snapshots_are_listed_and_restorable_in_every_mode(store: Store) -> None:
    """C6, behaviourally. A user who breaks things in Developer or Custom and
    returns to Simple must still see and restore every snapshot, or G3 fails in
    exactly the moment it exists for."""
    mode = {"value": "open"}
    manager = _manager(store, mode_ref=lambda: mode["value"])
    store.set_setting("marker", "made-in-open")
    open_row = manager.mark_verified_working()
    mode["value"] = "custom"
    store.set_setting("marker", "made-in-custom")
    custom_row = manager.mark_verified_working()
    assert open_row is not None and custom_row is not None

    mode["value"] = "safe"
    store.set_setting("marker", "back-in-simple")
    listed = {r["id"]: r for r in manager.list()}

    assert open_row.id in listed and custom_row.id in listed
    assert listed[open_row.id]["created_in_mode"] == "open"
    assert listed[custom_row.id]["created_in_mode"] == "custom"
    assert manager.restore(open_row.id).ok
    assert store.get_setting("marker") == "made-in-open"


def test_created_in_mode_records_the_live_mode(store: Store) -> None:
    """`mode_ref` is actually wired: without it the column would be a permanent
    lie that always read 'safe'."""
    manager = _manager(store, mode_ref=lambda: "open")
    assert manager.capture(trigger="auto", reason="other").created_in_mode == "open"

    exploding = _manager(store, mode_ref=lambda: 1 / 0)
    assert exploding.capture(trigger="auto", reason="other").created_in_mode == "safe"


def test_no_snapshot_query_filters_on_created_in_mode() -> None:
    """C6, structurally. The behavioural test above only proves today's code;
    this one fails if someone adds `AND created_in_mode = ?` to a new query next
    quarter. C6 is the doc set's most dangerous item and deserves a lock that
    survives the next contributor."""
    import re

    # Naming the column in a SELECT or INSERT column list is how it reaches the
    # UI, which is all it is for. Naming it anywhere a predicate can live — a
    # WHERE/HAVING/JOIN clause, or next to a comparison — is the thing that must
    # never exist.
    filter_context = re.compile(
        r"created_in_mode\s*(=|!=|<>|<|>|\bIN\b|\bLIKE\b|\bIS\b|\bNOT\b)", re.IGNORECASE
    )
    for source in (_STORE_SRC, _MANAGER_SRC):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            sql = node.value
            if "config_snapshots" not in sql or "created_in_mode" not in sql:
                continue
            assert not filter_context.search(sql), (
                f"{source.name}: a config_snapshots query filters on created_in_mode "
                f"— snapshots are visible and restorable in EVERY mode, or G3 fails "
                f"in exactly the moment it exists for (contract §0 C6): {sql!r}"
            )
            tail = re.split(r"\bWHERE\b|\bHAVING\b|\bJOIN\b", sql, flags=re.IGNORECASE)[1:]
            assert not any("created_in_mode" in part for part in tail), (
                f"{source.name}: created_in_mode appears in a predicate clause of a "
                f"config_snapshots query (contract §0 C6): {sql!r}"
            )


# --- the import ban (the unbreakability argument, structurally) -------------

_FORBIDDEN = (
    "agent_core.providers",
    "agent_core.tools",
    "agent_core.routines",
    "agent_core.orchestrator",
    "agent_core.policy",
    "agent_core.profiles",
    "agent_core.permissions",
    "httpx",
)


def test_restore_path_imports_nothing_that_can_be_misconfigured() -> None:
    """The classic failure is exactly "the models are misconfigured". A restore
    that needs a working model is a restore that fails when you need it."""
    package_init = _MANAGER_SRC.with_name("__init__.py")
    assert package_init.read_text(encoding="utf-8").strip() == "", (
        "agent_core/snapshots/__init__.py must stay EMPTY: undo_manager.py lives "
        "in this package and imports the tool registry, so a convenience "
        "re-export would drag it into the restore path at runtime."
    )
    for source in (_MANAGER_SRC, package_init):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                for banned in _FORBIDDEN:
                    assert not (name == banned or name.startswith(banned + ".")), (
                        f"{source.name} imports {name} — the restore path must not "
                        f"depend on anything that can be misconfigured (§6.1)."
                    )


def test_importing_the_manager_pulls_in_no_tool_machinery() -> None:
    """The same claim against the REAL import graph. An ast test cannot see a
    transitive import via the package __init__, and undo_manager.py next door
    imports the tool registry — the hazard is one convenience re-export away."""
    code = (
        "import sys; import agent_core.snapshots.snapshot_manager; "
        "print([m for m in sys.modules if m.startswith('agent_core.tools') "
        "or m.startswith('agent_core.providers') or m == 'httpx'])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        cwd=str(_MANAGER_SRC.parents[2]),
    )
    assert out.stdout.strip() == "[]", out.stdout


def test_snapshot_subsystem_never_schedules_itself() -> None:
    """G2, structurally: Addison never triggers itself, in any mode."""
    source = _MANAGER_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {
                    "threading", "sched", "asyncio", "signal"
                }, f"{alias.name} would let the snapshot subsystem trigger itself (G2)"
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in {
                "threading", "sched", "asyncio", "signal"
            }
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"Timer", "call_later", "create_task"}


# --- capture scope completeness (C13) ---------------------------------------


def test_capture_scope_covers_every_schema_table() -> None:
    """Closes C13 for every future Phase-2 table: a new table is either captured
    or explicitly excluded, and neither is something you can forget."""
    import re

    schema = _SCHEMA_SRC.read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema))
    declared = set(_CAPTURED_TABLES) | set(_EXCLUDED_TABLES)
    assert tables <= declared, (
        f"un-declared tables {sorted(tables - declared)}: add each to "
        f"_CAPTURED_TABLES or _EXCLUDED_TABLES in agent_core/snapshots/scope.py"
    )


def test_capture_scope_covers_every_column_of_every_captured_table(store: Store) -> None:
    """The column half of C13. Without it, a new column on a captured table would
    be silently reset to its default BY the recovery path — a restore would wipe
    the user's routing strategy and Custom guard toggles."""
    for table, columns in _CAPTURED_TABLES.items():
        actual = {row["name"] for row in store._conn.execute(f"PRAGMA table_info({table})")}
        declared = set(columns) | set(_EXCLUDED_COLUMNS.get(table, ()))
        assert actual <= declared, (
            f"{table}: columns {sorted(actual - declared)} are neither captured nor "
            f"listed in _EXCLUDED_COLUMNS — a restore would reset them to their "
            f"defaults"
        )


# --- the permanent row earns its verified flag (owner suggestion 2026-07-20) --


def test_a_turn_against_the_permanent_row_verifies_it_instead_of_cloning_it(
    store: Store,
) -> None:
    """The one restore point that is always there should be provable.

    Before this, an upgraded install that answered one message ended up with TWO
    rows holding byte-identical config: the permanent ``pre_upgrade`` row, still
    unverified, and a fresh ``turn_verified`` clone. So the row retention can
    never prune and the triggers refuse to delete — the row most worth returning
    to — was the one row that could never become a target, no matter how many
    turns ran against exactly its contents."""
    store.set_setting("selected_model", "the-model-they-upgraded-with")
    manager = _manager(store, created_the_database=False)

    manager.mark_verified_working()

    rows = manager.list()
    assert len(rows) == 1, f"expected the permanent row to be reused, got {rows}"
    assert rows[0]["reason"] == "pre_upgrade"
    assert rows[0]["verified_working"] is True
    assert rows[0]["undeletable"] is True


def test_the_verified_permanent_row_is_a_real_restore_target(store: Store) -> None:
    """Verifying it is only worth anything if the button then reaches THAT row.

    Asserting the settings came back is not enough — the clone holds identical
    config, so a restore onto it looks the same from the outside and the test
    would pass against the behaviour this change replaces. What distinguishes
    them is WHICH row the button landed on, so that is what gets asserted: the
    permanent one, the row retention can never prune."""
    store.set_setting("selected_model", "good-model")
    manager = _manager(store, created_the_database=False)
    manager.mark_verified_working()

    store.set_setting("selected_model", "broken-model")
    result = manager.restore_last_working()

    assert result.ok is True
    assert store.get_setting("selected_model", "") == "good-model"
    assert result.snapshot_id is not None
    landed = store.get_config_snapshot(result.snapshot_id)
    assert landed is not None
    assert landed.undeletable is True
    assert landed.reason == "pre_upgrade"


def test_an_ordinary_snapshot_is_never_flagged_after_the_fact(store: Store) -> None:
    """The narrowing is the safety. A pre-change snapshot holds a config the turn
    never ran against, so flagging one would make "restore lands somewhere that
    actually ran" false — the failure G3 exists to prevent. Only a PERMANENT row
    whose fingerprint proves the turn ran against its exact contents qualifies.

    This one cannot fail against the code it replaced — that code flagged nothing
    at all, so removing the feature makes it pass trivially. It is written
    against the PLAUSIBLE BAD FIX instead: drop ``row.get("undeletable") and``
    from ``_permanent_row_matching`` and it fails.

    The setup has to be exact or it proves nothing. The permanent row must hold a
    DIFFERENT config from the one being verified, otherwise it matches first and
    the ordinary row is never reached; and no verified row may exist yet, or the
    fingerprint check at the top returns before either lookup runs. So: boot on
    one config, move to another, snapshot there, and verify there."""
    store.set_setting("selected_model", "at-boot")
    manager = _manager(store, created_the_database=False)  # permanent row holds "at-boot"

    store.set_setting("selected_model", "changed-since")
    manager.capture(trigger="auto", reason="provider_connect")  # ordinary row holds current
    ordinary = [row for row in manager.list() if row["reason"] == "provider_connect"]
    assert ordinary, "the fixture did not produce the pre-change row this test needs"
    assert not any(row["verified_working"] for row in manager.list()), (
        "a verified row here would short-circuit before the lookup under test"
    )

    manager.mark_verified_working()

    after = {row["id"]: row for row in manager.list()}
    assert after[ordinary[0]["id"]]["verified_working"] is False
