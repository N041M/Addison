"""Opportunistic usage-log pruning from the record path — engineering-spec §4.8.

``_record_usage`` writes one ``usage_log`` row per metered provider call and, on a
throttle (once every ``_USAGE_PRUNE_EVERY`` records), prunes rows older than the
~6-month retention window. These tests drive that choke point directly with a real
``Store`` and assert both the throttle cadence and the age cutoff.

Updated for step 3 (D5 [N1]): ``_record_usage`` now takes the RESOLVED
``(provider_id, model_id)`` the orchestrator supplies for each call, instead of
re-deriving identity here from ``(requested_role, model_name)`` — the change that
fixes routed-turn mis-attribution. The relay skip is keyed on
``provider_id == "setup_assistant"``.
"""

from pathlib import Path
from types import SimpleNamespace

import agent_core.main as main
from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.providers.router import ModelRouter
from agent_core.tools.registry import ToolRegistry


def _server(tmp_path: Path) -> JsonRpcServer:
    """A minimal server wired only far enough to call ``_record_usage`` — no reader
    loop, no worker thread. The store is attached directly (bypassing the
    worker-thread ``_ensure_built``); the constructor just stashes reader/writer."""
    server = JsonRpcServer(
        reader=None,
        writer=None,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "usage.db"),
        model_router=ModelRouter(configured={}),
    )
    server.store = Store(tmp_path / "usage.db")
    return server


def _usage_obj(inp: int = 10, out: int = 5) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=inp, output_tokens=out)


def _row_count(server: JsonRpcServer) -> int:
    return server.store._conn.execute("SELECT COUNT(*) AS n FROM usage_log").fetchone()["n"]


def test_record_usage_prunes_on_throttle_boundary(tmp_path: Path, monkeypatch):
    server = _server(tmp_path)
    monkeypatch.setattr(main, "_USAGE_PRUNE_EVERY", 3)
    monkeypatch.setattr(main, "_USAGE_RETENTION_SECONDS", 100)

    # An ancient row that the first prune must delete (far older than 100s ago).
    server.store.insert_usage(
        id="ancient", conversation_id=None, provider="anthropic", model="m",
        input_tokens=1, output_tokens=1, latency_ms=None, created_at=1,
    )

    # Two records: below the throttle threshold, so no prune yet.
    for _ in range(2):
        server._record_usage(_usage_obj(), latency_ms=5, provider_id="anthropic",
                             model_id="claude-opus-4-8")
    assert _row_count(server) == 3  # ancient + 2 new, still present

    # Third record hits the boundary (3 >= 3): prune runs and drops the ancient row.
    server._record_usage(_usage_obj(), latency_ms=5, provider_id="anthropic",
                         model_id="claude-opus-4-8")
    ids = {r["id"] for r in server.store._conn.execute("SELECT id FROM usage_log").fetchall()}
    assert "ancient" not in ids
    assert _row_count(server) == 3  # the three fresh records, ancient pruned


def test_record_usage_skips_setup_assistant(tmp_path: Path):
    server = _server(tmp_path)
    server._record_usage(_usage_obj(), latency_ms=5,
                         provider_id="setup_assistant", model_id="relay")
    assert _row_count(server) == 0  # onboarding relay is never metered


def test_record_usage_records_resolved_identity(tmp_path: Path):
    # D5 [N1]: the row is the RESOLVED identity the orchestrator passed — the model
    # that actually answered — not a re-derivation from role/name.
    server = _server(tmp_path)
    server._record_usage(_usage_obj(), latency_ms=5, provider_id="google",
                         model_id="gemini-2.5-flash")
    row = server.store._conn.execute(
        "SELECT provider, model FROM usage_log"
    ).fetchone()
    assert row["provider"] == "google"
    assert row["model"] == "gemini-2.5-flash"


def test_record_usage_noop_when_usage_is_none(tmp_path: Path):
    server = _server(tmp_path)
    server._record_usage(None, latency_ms=5, provider_id="anthropic", model_id="m")
    assert _row_count(server) == 0
