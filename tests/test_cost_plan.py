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
