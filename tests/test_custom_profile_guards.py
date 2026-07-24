"""Custom profile + the two tunable prompting guards + the G4 anchor caller
(scope amendment 2026-07-20, §7; step-2 contract D1–D8).

Every behaviour here is mutation-proven in the report that accompanies this
change: revert the guarded line, exactly the named test goes red, restore, the
file is byte-identical. The guards can only change how often Addison asks — never
a GLOBAL floor — and lowering one always leaves a guaranteed way back.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import (
    DEFAULT_AUTO_GRANT_SCOPE,
    DEFAULT_DESTRUCTIVE_CARD,
    GuardConfig,
    PolicyMode,
    mode_for_profile,
    weakenings_between,
)
from agent_core.profiles import CUSTOM, DEVELOPER, SIMPLE
from agent_core.protocol import Method
from agent_core.snapshots.snapshot_manager import SnapshotManager
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


# ============================================================================
# D1 — Custom derives OPEN, and its mode never leaks onto the wire as 'custom'
# ============================================================================
def test_custom_profile_derives_open_mode():
    # The headline of D1/R6: if this is SAFE, the guards tune nothing.
    assert mode_for_profile(CUSTOM) is PolicyMode.OPEN
    assert mode_for_profile(SIMPLE) is PolicyMode.SAFE
    assert mode_for_profile(DEVELOPER) is PolicyMode.OPEN
    assert mode_for_profile(None) is PolicyMode.SAFE


def test_custom_is_developers_surface():
    # D8: Custom does everything Developer does — same tools, same flags.
    assert set(CUSTOM.tool_ids) == set(DEVELOPER.tool_ids)
    assert CUSTOM.headless_cli and CUSTOM.raw_diagnostics and CUSTOM.expose_routine_plan
    assert CUSTOM.advanced is True
    assert "Going back to a working setup always stays possible" in CUSTOM.description


# ============================================================================
# D2 — the guard model: defaults are today's OPEN, weakening is a move DOWN
# ============================================================================
def test_default_guardconfig_is_todays_open():
    cfg = GuardConfig()
    assert cfg.destructive_card == DEFAULT_DESTRUCTIVE_CARD == "per_invocation"
    assert cfg.auto_grant_scope == DEFAULT_AUTO_GRANT_SCOPE == "non_destructive"


def test_weakenings_between_names_only_downward_moves():
    strict = GuardConfig(destructive_card="per_invocation", auto_grant_scope="none")
    weak = GuardConfig(destructive_card="session", auto_grant_scope="everything")
    # Both moved down.
    assert set(weakenings_between(strict, weak)) == {"destructive_card", "auto_grant_scope"}
    # Tightening never appears.
    assert weakenings_between(weak, strict) == []
    # No change -> nothing weakened.
    assert weakenings_between(strict, strict) == []
    # One dimension only.
    part = GuardConfig(destructive_card="per_invocation", auto_grant_scope="everything")
    assert weakenings_between(GuardConfig(), part) == ["auto_grant_scope"]


# ============================================================================
# D3 — PermissionGate.authorize(guards=...) semantics; None ≡ today's behaviour
# ============================================================================
def test_guards_none_is_byte_for_byte_todays_open():
    """The freeze: guards=None in OPEN is exactly the historical OPEN gate —
    non-destructive auto-grants, destructive cards per invocation."""
    asked: list[str | None] = []
    logged: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid, detail=None: (asked.append(detail), PermissionStatus.GRANTED)[1],
        on_auto_grant=lambda tid: logged.append(tid),
    )
    assert gate.authorize("t", mode=PolicyMode.OPEN, destructive=False, guards=None) == (
        PermissionStatus.GRANTED
    )
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm a", guards=None
    ) == PermissionStatus.GRANTED
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm b", guards=None
    ) == PermissionStatus.GRANTED
    assert logged == ["t"]                 # only the non-destructive auto-granted
    assert asked == ["rm a", "rm b"]       # destructive carded per invocation
    assert gate.check("run_command") == PermissionStatus.NOT_YET_ASKED  # no coarse grant kept


def test_scope_everything_auto_grants_destructive_and_still_logs():
    """'Never ask' auto-grants destructive calls too — but every one is still
    recorded (fewer prompts, not no gate)."""
    logged: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid, detail=None: (_ for _ in ()).throw(
            AssertionError("scope 'everything' must never card")
        ),
        on_auto_grant=lambda tid: logged.append(tid),
    )
    guards = GuardConfig(destructive_card="per_invocation", auto_grant_scope="everything")
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm -rf x", guards=guards
    ) == PermissionStatus.GRANTED
    # Recorded both ways — the activity log still shows what happened.
    assert gate.auto_grants == ["run_command"]
    assert logged == ["run_command"]


def test_scope_none_runs_the_safe_coarse_flow_in_open():
    """'Ask about everything' asks for every not-yet-granted tool (even
    non-destructive) and then remembers a coarse grant, exactly like SAFE."""
    asked: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid: (asked.append(tid), PermissionStatus.GRANTED)[1]
    )
    guards = GuardConfig(destructive_card="per_invocation", auto_grant_scope="none")
    # A non-destructive call PROMPTS under 'none' (it would auto-grant by default).
    assert gate.authorize("web_search", mode=PolicyMode.OPEN, destructive=False, guards=guards) == (
        PermissionStatus.GRANTED
    )
    # ...and is then remembered as a coarse grant — the second call is silent.
    assert gate.authorize("web_search", mode=PolicyMode.OPEN, destructive=False, guards=guards) == (
        PermissionStatus.GRANTED
    )
    assert asked == ["web_search"]
    assert gate.auto_grants == []              # nothing auto-granted under 'none'
    assert gate.check("web_search") == PermissionStatus.GRANTED


def test_scope_none_never_downgrades_destructive_to_the_coarse_flow():
    """The adversarial-pass bug (2026-07-24): 'none' used to route DESTRUCTIVE
    calls through the coarse SAFE flow, so one approved ``ls`` silently covered
    every later ``rm -rf`` — with no command text, under the scope labelled "Ask
    about everything", counted as a tightening so no anchor was ever minted.

    Destructive calls under 'none' must stay on the per-invocation card: a card
    EVERY time, each carrying its own command text, nothing remembered."""
    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    guards = GuardConfig(destructive_card="per_invocation", auto_grant_scope="none")
    for command in ("ls", "rm -rf /important", "rm -rf /more"):
        assert gate.authorize(
            "run_command", mode=PolicyMode.OPEN, destructive=True,
            detail=command, guards=guards,
        ) == PermissionStatus.GRANTED
    # Three cards, one per call, each naming ITS command — never a coarse grant.
    assert asked == [
        ("run_command", "ls"),
        ("run_command", "rm -rf /important"),
        ("run_command", "rm -rf /more"),
    ]
    assert gate.check("run_command") == PermissionStatus.NOT_YET_ASKED


def test_scope_none_with_session_card_keeps_the_session_semantics():
    """The two knobs stay orthogonal: under 'none', a destructive call still obeys
    the CARD guard — so 'session' cards once and then remembers, in the dedicated
    session set, never as a coarse ``_grants`` entry the SAFE path could read."""
    asked: list[str | None] = []
    gate = PermissionGate(
        on_request=lambda tid, detail=None: (asked.append(detail), PermissionStatus.GRANTED)[1]
    )
    guards = GuardConfig(destructive_card="session", auto_grant_scope="none")
    for command in ("rm a", "rm b"):
        assert gate.authorize(
            "run_command", mode=PolicyMode.OPEN, destructive=True,
            detail=command, guards=guards,
        ) == PermissionStatus.GRANTED
    assert asked == ["rm a"]                                   # asked once
    assert gate.check("run_command") == PermissionStatus.NOT_YET_ASKED  # not coarse


def test_session_card_asks_once_then_remembers_for_the_session():
    """'Ask once': the first destructive call of a tool cards; the next is silent."""
    asked: list[str | None] = []
    gate = PermissionGate(
        on_request=lambda tid, detail=None: (asked.append(detail), PermissionStatus.GRANTED)[1]
    )
    guards = GuardConfig(destructive_card="session", auto_grant_scope="non_destructive")
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm a", guards=guards
    ) == PermissionStatus.GRANTED
    # Same tool, different destructive command: NO second card (remembered).
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm b", guards=guards
    ) == PermissionStatus.GRANTED
    assert asked == ["rm a"]


def test_destructive_session_grant_never_leaks_into_a_safe_check():
    """[R2] The SAFE-leak guard, structural: a 'session' grant lives in a DEDICATED
    set, never ``_grants``. SAFE ``check()`` reads only ``_grants``, so the grant
    is invisible to SAFE mode WITHOUT relying on revoke_all having run. If it were
    stored in ``_grants`` instead, the SAFE authorize below would silently grant."""
    cards: list[str | None] = []
    gate = PermissionGate(
        on_request=lambda tid, detail=None: (cards.append(detail), PermissionStatus.GRANTED)[1]
    )
    guards = GuardConfig(destructive_card="session", auto_grant_scope="non_destructive")
    gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm x", guards=guards
    )
    assert "run_command" in gate._destructive_session_grants
    assert "run_command" not in gate._grants        # NEVER the coarse store
    # Now the SAME tool authorised in SAFE mode: must card (coarse check sees nothing).
    assert gate.check("run_command") == PermissionStatus.NOT_YET_ASKED
    assert gate.authorize("run_command", mode=PolicyMode.SAFE) == PermissionStatus.GRANTED
    assert cards == ["rm x", None]                  # a second, SAFE-mode card was raised


def test_revoke_all_clears_the_destructive_session_grants_too():
    gate = PermissionGate(on_request=lambda tid, detail=None: PermissionStatus.GRANTED)
    guards = GuardConfig(destructive_card="session", auto_grant_scope="non_destructive")
    gate.authorize("run_command", mode=PolicyMode.OPEN, destructive=True, guards=guards)
    gate.grant("web_search")
    assert gate._destructive_session_grants and gate._grants
    gate.revoke_all()
    assert gate._destructive_session_grants == set()
    assert gate._grants == {}


# ============================================================================
# server harness for the RPC-level tests
# ============================================================================
def _caller(h):
    ids = itertools.count(1)

    def call(method: str, params: dict | None = None) -> dict:
        rid = next(ids)
        h.reader.feed({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return h.writer.wait_for(lambda f: f.get("id") == rid and "result" in f)["result"]

    return call


def _guard_weakened_rows(call) -> list[dict]:
    return [
        s
        for s in call(Method.SNAPSHOT_LIST)["snapshots"]
        if s.get("reason") == "guard_weakened"
    ]


# ============================================================================
# D5 — guards.get / guards.set + the anchor flow
# ============================================================================
def test_guards_get_reports_defaults_and_active_only_under_custom(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        got = call(Method.GUARDS_GET)
        assert got["destructiveCard"] == "per_invocation"
        assert got["autoGrantScope"] == "non_destructive"
        assert got["defaults"] == {
            "destructiveCard": "per_invocation",
            "autoGrantScope": "non_destructive",
        }
        assert got["active"] is False              # Simple by default
        call(Method.PROFILE_SET, {"profileId": "custom"})
        assert call(Method.GUARDS_GET)["active"] is True
        # Developer is OPEN but NOT Custom, so the guards are not active there.
        call(Method.PROFILE_SET, {"profileId": "developer"})
        assert call(Method.GUARDS_GET)["active"] is False
    finally:
        _shutdown(h.reader, h.thread)


def test_guards_set_weakening_mints_one_anchor_and_persists(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        assert _guard_weakened_rows(call) == []
        res = call(Method.GUARDS_SET, {"autoGrantScope": "everything"})
        assert res == {"ok": True, "destructiveCard": "per_invocation",
                       "autoGrantScope": "everything"}
        # Persisted.
        assert call(Method.GUARDS_GET)["autoGrantScope"] == "everything"
        # A G4 anchor was minted, and it is undeletable + verified.
        anchors = _guard_weakened_rows(call)
        assert len(anchors) == 1
        assert anchors[0]["undeletable"] is True
        assert anchors[0]["verifiedWorking"] is True
    finally:
        _shutdown(h.reader, h.thread)


def test_guards_set_tightening_mints_no_anchor(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        call(Method.GUARDS_SET, {"autoGrantScope": "everything"})  # weaken -> 1 anchor
        assert len(_guard_weakened_rows(call)) == 1
        # Tighten all the way back: no new anchor.
        call(Method.GUARDS_SET, {"autoGrantScope": "none", "destructiveCard": "per_invocation"})
        assert len(_guard_weakened_rows(call)) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_guards_set_repeated_weakening_mints_one_anchor(tmp_path):
    """[R7] weaken -> tighten -> weaken again: ONE anchor (fingerprint dedupe), and
    the anchor list never shrinks."""
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        call(Method.GUARDS_SET, {"autoGrantScope": "everything"})            # weaken
        first = _guard_weakened_rows(call)
        assert len(first) == 1
        call(Method.GUARDS_SET, {"autoGrantScope": "none"})                  # tighten
        call(Method.GUARDS_SET, {"autoGrantScope": "everything"})            # weaken again
        second = _guard_weakened_rows(call)
        assert len(second) == 1
        assert second[0]["id"] == first[0]["id"]                             # SAME anchor
    finally:
        _shutdown(h.reader, h.thread)


def test_guards_set_unknown_value_refuses_and_changes_nothing(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        res = call(Method.GUARDS_SET, {"autoGrantScope": "whatever"})
        assert res["ok"] is False
        assert "recognise" in res["error"]
        # Nothing persisted, no anchor minted.
        assert call(Method.GUARDS_GET)["autoGrantScope"] == "non_destructive"
        assert _guard_weakened_rows(call) == []
    finally:
        _shutdown(h.reader, h.thread)


def test_guards_set_refuses_when_the_anchor_cannot_be_minted(tmp_path):
    """[D5.3] A weakening save mints the anchor FIRST; if that fails, the whole set
    refuses and NOTHING persists — weakening without a way back is what G4 forbids."""
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})   # builds the store + manager
        # Force the mint to fail.
        h.server.snapshot_manager.mint_anchor = lambda **kwargs: None  # type: ignore[method-assign]
        res = call(Method.GUARDS_SET, {"autoGrantScope": "everything"})
        assert res["ok"] is False
        assert "restore point" in res["error"]
        # The guard did NOT move, and no anchor row was left behind.
        assert call(Method.GUARDS_GET)["autoGrantScope"] == "non_destructive"
        assert _guard_weakened_rows(call) == []
    finally:
        _shutdown(h.reader, h.thread)


# ============================================================================
# D3 [R2] — a profile change revokes the session's grants
# ============================================================================
def test_profile_change_revokes_all_grants(tmp_path):
    """A profile switch is a posture event: the session's coarse grants must not
    survive it, or the session stays wider than the profile just chosen."""
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        # Grant a coarse tool in SAFE (Simple), which lands in _grants.
        h.server.permission_gate.grant("spy_tool")
        assert "spy_tool" in h.server.permission_gate._grants
        call(Method.PROFILE_SET, {"profileId": "developer"})
        # revoke_all ran on the switch — the coarse grant is gone.
        assert h.server.permission_gate._grants == {}
    finally:
        _shutdown(h.reader, h.thread)


# ============================================================================
# D6 — created_in_mode: Custom artifacts stamp 'open', hide + refuse in SAFE
# ============================================================================
def test_custom_stamps_open_mode():
    """A widget/routine created under Custom stamps 'open' (the mode value), not
    'custom' — Custom IS an OPEN-derived mode, so its artifacts hide/refuse in SAFE
    exactly like Developer's. The insert helpers use ``self._mode().value``, so this
    equality is what makes that stamping correct."""
    assert mode_for_profile(CUSTOM).value == "open"


def test_custom_created_widget_hidden_and_refused_in_safe(tmp_path):
    """D6 regression: a command widget stamped 'open' (as a Custom-built one is)
    disappears from the SAFE list and refuses to run there, waiting for a
    developer-capable profile. Seeded via a separate Store before the server starts
    (SQLite thread affinity — the server's own store belongs to the worker)."""
    seed = Store(tmp_path / IPC_DB_NAME)
    seed.set_setting("widgets_seeded", "1")   # keep the rail to just this widget
    seed.insert_widget(
        id="cust-w",
        spec_json='{"kind": "command", "command": "ls", "title": "List"}',
        pinned=True,
        position=0,
        created_at=1,
        created_in_mode="open",   # what Custom stamps (see test_custom_stamps_open_mode)
    )
    seed.close()
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        widgets = {w["id"]: w for w in call(Method.WIDGET_LIST)["widgets"]}
        assert "cust-w" in widgets and widgets["cust-w"]["createdInMode"] == "open"
        # Switch to Simple (SAFE): the command widget disappears...
        call(Method.PROFILE_SET, {"profileId": "simple"})
        assert "cust-w" not in {w["id"] for w in call(Method.WIDGET_LIST)["widgets"]}
        # ...and running it is refused.
        res = call(Method.WIDGET_RUN, {"id": "cust-w"})
        assert res["ok"] is False
        assert "waiting in Developer profile" in res["error"]
    finally:
        _shutdown(h.reader, h.thread)


def test_config_snapshot_records_custom_mode_for_display(tmp_path):
    """main.py's mode_ref reports 'custom' for a snapshot taken under Custom — a
    DISPLAY-only column (C6), never filtered. The profile switch INTO custom takes
    its own snapshot while still Simple, so we take one explicitly under Custom."""
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        created = call(Method.SNAPSHOT_CREATE)
        assert created["ok"] is True
        rows = {s["id"]: s for s in call(Method.SNAPSHOT_LIST)["snapshots"]}
        assert rows[created["snapshotId"]]["createdInMode"] == "custom"
    finally:
        _shutdown(h.reader, h.thread)


# ============================================================================
# D1/R10 — profile.get mode stays 'safe'|'open' even for Custom
# ============================================================================
def test_profile_get_mode_is_open_never_custom_for_custom_profile(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        got = call(Method.PROFILE_GET)
        assert got["activeProfile"] == "custom"
        assert got["mode"] == "open"          # never 'custom'
    finally:
        _shutdown(h.reader, h.thread)


# ============================================================================
# D7 [R1] — a restore to a weaker guard posture under Custom discloses it
# ============================================================================
def test_restore_to_weaker_guards_under_custom_appends_notice(tmp_path):
    h = build_server(tmp_path)
    try:
        call = _caller(h)
        call(Method.PROFILE_SET, {"profileId": "custom"})
        # Weaken the guards, then save a restore point OF the weakened config.
        call(Method.GUARDS_SET, {"autoGrantScope": "everything", "destructiveCard": "session"})
        weak_point = call(Method.SNAPSHOT_CREATE)["snapshotId"]
        anchors_before = len(_guard_weakened_rows(call))
        # Tighten back to strict.
        call(Method.GUARDS_SET, {"autoGrantScope": "none", "destructiveCard": "per_invocation"})
        # Restoring the weak config leaves guards WEAKER than the current strict ones.
        res = call(Method.SNAPSHOT_RESTORE, {"id": weak_point})
        assert res["ok"] is True
        assert "turned down how often Addison asks before acting" in res["detail"]
        # The restore applied the weak guards...
        got = call(Method.GUARDS_GET)
        assert got["autoGrantScope"] == "everything" and got["destructiveCard"] == "session"
        # ...and minted NO new anchor (the original weakening's anchor still stands).
        assert len(_guard_weakened_rows(call)) == anchors_before
    finally:
        _shutdown(h.reader, h.thread)


# ============================================================================
# mint_anchor dedupe at the manager level (independent of the RPC plumbing)
# ============================================================================
class _Clock:
    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        self.now += 1
        return self.now


def test_mint_anchor_dedupes_a_repeated_weakening(tmp_path: Path) -> None:
    """[R7] Two mints against the same known-good config produce ONE undeletable
    anchor; the second returns the first row rather than writing a duplicate."""
    store = Store(tmp_path / "addison.sqlite3")
    try:
        manager = SnapshotManager(store=store, clock=_Clock(), created_the_database=True)
        store.set_setting("marker", "known-good")
        manager.mark_verified_working()

        first = manager.mint_anchor()
        second = manager.mint_anchor()

        assert first is not None and second is not None
        assert second.id == first.id
        anchors = [
            r for r in store.list_config_snapshots()
            if r.get("reason") == "guard_weakened" and r.get("undeletable")
        ]
        assert len(anchors) == 1
    finally:
        store.close()


def test_dedupe_never_confirms_an_anchor_whose_payload_cannot_load(tmp_path: Path) -> None:
    """Adversarial pass, 2026-07-24: dedupe is guards.set's confirmation that a
    way back EXISTS, so a matching anchor whose payload has rotted (row blob
    corrupt AND sidecar gone) must not count — the mint falls through to a fresh
    anchor instead of letting a weakening proceed against a row that cannot
    restore."""
    store = Store(tmp_path / "addison.sqlite3")
    try:
        manager = SnapshotManager(store=store, clock=_Clock(), created_the_database=True)
        store.set_setting("marker", "known-good")
        manager.mark_verified_working()

        first = manager.mint_anchor()
        assert first is not None
        # Rot the anchor: corrupt the row's blob and remove its sidecar. (The G4
        # triggers forbid deleting the row or clearing its flag — not this.)
        store._conn.execute(
            "UPDATE config_snapshots SET state_blob = ? WHERE id = ?",
            ("not json {", first.id),
        )
        store._conn.commit()
        sidecar = tmp_path / "snapshots" / f"{first.id}.json"
        if sidecar.exists():
            sidecar.unlink()

        second = manager.mint_anchor()
        assert second is not None
        assert second.id != first.id            # fresh mint, not the rotten match
        anchors = [
            r for r in store.list_config_snapshots()
            if r.get("reason") == "guard_weakened" and r.get("undeletable")
        ]
        assert len(anchors) == 2                # the rotten one stays (undeletable), a live one joins it
    finally:
        store.close()
