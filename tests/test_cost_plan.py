"""costPlan.propose / costPlan.apply — "make it cheaper" (step 4, contract F3/D4,
verification items 1, 2).

House style of tests/test_snapshot_hooks.py: the real server on fake pipes, plus a
manager wrapper whose capture path is broken to exercise the refuse-on-failure
policy (the deliberate new hook class — a compound, conversationally-initiated
degradation whose only recovery is the restore point, so it REFUSES rather than
proceeds).
"""

from __future__ import annotations

from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.router import COST_FIRST
from agent_core.rpc.cost_plan import _COST_SKILL_INSTRUCTIONS, _COST_SKILL_NAME
from agent_core.rpc.routing import _ROUTING_STRATEGY_KEY
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    frame = harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return frame["result"]


def _side_store(tmp_path) -> Store:
    return Store(tmp_path / IPC_DB_NAME)


def _reasons(store: Store) -> list[str]:
    return [row["reason"] for row in store.list_config_snapshots()]


def _skills_named(store: Store, name: str) -> list[dict]:
    return [s for s in store.list_skills() if s["name"] == name]


class _FailingManager:
    """The real manager with its capture path broken — a full disk / read-only data
    dir, the exact condition the refuse-on-failure policy exists for. Everything
    else delegates so snapshot.list still answers."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.capture_reasons: list[object] = []

    def capture(self, **kwargs):
        self.capture_reasons.append(kwargs.get("reason"))
        raise OSError("No space left on device")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _wrap_failing(harness):
    _call(harness, Method.SNAPSHOT_LIST, request_id=900)  # force the lazy build
    wrapped = _FailingManager(harness.server._snapshot_manager)
    harness.server._snapshot_manager = wrapped
    return wrapped


# --- propose: every field is canned (F3) -------------------------------------


def test_propose_returns_the_canned_plan_verbatim(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        result = _call(h, Method.COSTPLAN_PROPOSE, request_id=1)
        assert result == {
            "skillName": _COST_SKILL_NAME,
            "skillInstructions": _COST_SKILL_INSTRUCTIONS,
            "strategy": COST_FIRST,
        }
    finally:
        _shutdown(h.reader, h.thread)


# --- apply: the happy path (item 2) ------------------------------------------


def test_apply_adds_the_skill_sets_cost_first_and_mints_the_restore_point(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)
        assert result["ok"] is True
        assert isinstance(result.get("snapshotId"), str) and result["snapshotId"]

        store = _side_store(tmp_path)
        try:
            skills = _skills_named(store, _COST_SKILL_NAME)
            assert len(skills) == 1
            assert skills[0]["enabled"] is True
            assert skills[0]["instructions"] == _COST_SKILL_INSTRUCTIONS
            assert store.get_setting(_ROUTING_STRATEGY_KEY) == COST_FIRST
            assert "make_it_cheaper" in _reasons(store)
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_apply_then_restore_last_working_undoes_both_halves(tmp_path):
    # Item 2's "restoreLastWorking returns both": the restore point predates the
    # change, so going back to the last working setup removes the note AND resets the
    # strategy — the recovery the at-risk persona depends on.
    h = build_server(tmp_path, register_tool=False)
    try:
        assert _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)["ok"] is True
        restore = _call(h, Method.SNAPSHOT_RESTORE_LAST_WORKING, request_id=2)
        assert restore["ok"] is True

        store = _side_store(tmp_path)
        try:
            assert _skills_named(store, _COST_SKILL_NAME) == []          # note gone
            assert store.get_setting(_ROUTING_STRATEGY_KEY) != COST_FIRST  # strategy reset
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


# --- apply: hardening (D4) ---------------------------------------------------


def test_apply_refuses_and_persists_nothing_when_the_snapshot_fails(tmp_path):
    # Verification item 1: snapshot forced to fail -> nothing persists (no skill
    # row, strategy unchanged), and the plan is reported un-applied.
    h = build_server(tmp_path, register_tool=False)
    try:
        failing = _wrap_failing(h)
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)
        assert result["ok"] is False
        assert "couldn't save the restore point" in result["error"]
        assert failing.capture_reasons == ["make_it_cheaper"]  # it really tried

        store = _side_store(tmp_path)
        try:
            assert _skills_named(store, _COST_SKILL_NAME) == []
            assert store.get_setting(_ROUTING_STRATEGY_KEY) != COST_FIRST
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_apply_is_idempotent_and_skips_when_already_in_effect(tmp_path):
    # R7: a second apply changes nothing — one skill row (not two), and NO second
    # make_it_cheaper snapshot (no churn).
    h = build_server(tmp_path, register_tool=False)
    try:
        assert _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)["ok"] is True
        second = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=2)
        assert second["ok"] is True and second.get("alreadyInEffect") is True

        store = _side_store(tmp_path)
        try:
            assert len(_skills_named(store, _COST_SKILL_NAME)) == 1
            assert _reasons(store).count("make_it_cheaper") == 1
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_apply_ignores_a_strategy_from_the_wire_and_hard_sets_cost_first(tmp_path):
    # D4.2: the model/webview cannot drive another strategy through this flow.
    h = build_server(tmp_path, register_tool=False)
    try:
        result = _call(
            h, Method.COSTPLAN_APPLY, {"accept": True, "strategy": "quality_first"}, request_id=1
        )
        assert result["ok"] is True
        store = _side_store(tmp_path)
        try:
            assert store.get_setting(_ROUTING_STRATEGY_KEY) == COST_FIRST
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_apply_decline_changes_nothing(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)  # build the server
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": False}, request_id=2)
        assert result["ok"] is False and result.get("declined") is True

        store = _side_store(tmp_path)
        try:
            assert _skills_named(store, _COST_SKILL_NAME) == []
            assert "make_it_cheaper" not in _reasons(store)
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_apply_updates_an_existing_canned_skill_rather_than_duplicating(tmp_path):
    # Idempotency keys on the distinctive NAME: an existing row with that name is
    # UPDATED and re-enabled (the skills table has no UNIQUE), never duplicated.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)  # build the server
        store = _side_store(tmp_path)
        try:
            store.insert_skill(
                id="pre-existing",
                name=_COST_SKILL_NAME,
                instructions="stale text",
                enabled=False,      # disabled, and with different text
                created_at=7,
            )
        finally:
            store.close()

        assert _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=2)["ok"] is True

        store = _side_store(tmp_path)
        try:
            rows = _skills_named(store, _COST_SKILL_NAME)
            assert len(rows) == 1                                  # updated, not duplicated
            assert rows[0]["enabled"] is True                      # re-enabled
            assert rows[0]["instructions"] == _COST_SKILL_INSTRUCTIONS
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# The guards the post-build adversarial pass found unwatched. Each mutation below
# left the WHOLE suite green before these tests existed; one of them produced a
# genuinely half-applied plan.
# ---------------------------------------------------------------------------
def test_apply_cost_plan_is_atomic_when_the_setting_write_fails(tmp_path):
    """The dedicated Store method exists for exactly one reason — "a half-applied
    plan is then impossible" (contract R4) — and nothing was watching it. With the
    rollback removed, the skill row survives a failure in the settings write: the
    person gets a terse Addison that never got cheaper, and no restore point
    describes that state because the snapshot was taken before it.
    """
    store = Store(tmp_path / IPC_DB_NAME)
    real_conn = store._conn

    class _FailsOnTheSetting:
        """The live connection with ONE statement broken. sqlite3.Connection's
        methods are read-only, so the seam is a wrapper, not a monkeypatch — and it
        must be a wrapper the rollback can still reach, or the test would prove
        nothing about the rollback."""

        def execute(self, sql, *args, **kwargs):
            if "app_settings" in sql:
                raise RuntimeError("disk gave out mid-plan")
            return real_conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    store._conn = _FailsOnTheSetting()  # type: ignore[assignment]
    try:
        store.apply_cost_plan(
            skill_id="s1",
            skill_name=_COST_SKILL_NAME,
            skill_instructions=_COST_SKILL_INSTRUCTIONS,
            strategy_key=_ROUTING_STRATEGY_KEY,
            strategy_value=COST_FIRST,
            now=1,
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - the stub always raises
        raise AssertionError("the seeded failure did not propagate")
    store._conn = real_conn  # type: ignore[assignment]

    try:
        assert _skills_named(store, _COST_SKILL_NAME) == [], "a half-applied plan persisted"
        assert store.get_setting(_ROUTING_STRATEGY_KEY) != COST_FIRST
        # The connection must be left usable: a rollback that stranded an open
        # transaction would poison every later write on the one shared connection.
        store.set_setting("unrelated", "value")
        assert store.get_setting("unrelated") == "value"
    finally:
        store.close()


def test_not_already_in_effect_when_only_the_strategy_matches(tmp_path):
    """R7 says BOTH halves must hold. Mutating ``_cost_plan_in_effect`` to check the
    strategy alone left the suite green — and under that mutant a person who had
    already chosen cost_first from the routing toggle presses "Make it cheaper",
    is told it worked, and the guidance note is never added. Forever."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.ROUTING_SET, {"strategy": COST_FIRST}, request_id=1)
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=2)
        assert result["ok"] is True
        assert not result.get("alreadyInEffect"), "the guidance note was never added"

        store = _side_store(tmp_path)
        try:
            assert len(_skills_named(store, _COST_SKILL_NAME)) == 1
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_not_already_in_effect_when_only_the_note_matches(tmp_path):
    """The mirror case: the note is there but the strategy was changed back, so the
    plan is genuinely not in effect and apply must proceed — without duplicating
    the note it already wrote."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)
        _call(h, Method.ROUTING_SET, {"strategy": "quality_first"}, request_id=2)
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=3)
        assert result["ok"] is True
        assert not result.get("alreadyInEffect")

        store = _side_store(tmp_path)
        try:
            assert store.get_setting(_ROUTING_STRATEGY_KEY) == COST_FIRST
            assert len(_skills_named(store, _COST_SKILL_NAME)) == 1, "the note was duplicated"
        finally:
            store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_a_refused_apply_leaves_the_sticky_capture_warning(tmp_path):
    """The refusal sentence answers THIS apply and then it is gone; the sticky
    warning on snapshot.list is the only durable trace that a restore point could
    not be minted, and only a successful manual save clears it."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _wrap_failing(h)
        result = _call(h, Method.COSTPLAN_APPLY, {"accept": True}, request_id=1)
        assert result["ok"] is False
        listing = _call(h, Method.SNAPSHOT_LIST, request_id=2)
        assert listing.get("warning"), "no sticky warning survived the refused apply"
    finally:
        _shutdown(h.reader, h.thread)
