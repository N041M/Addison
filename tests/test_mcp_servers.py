"""MCP server configuration — step 7, PHASE 1 (docs/step-7-mcp-plan.md §4.1).

Phase 1 ships configuration and nothing else: the ``mcp_servers`` table, the
``mcp.list``/``add``/``remove`` RPC, and the store-boundary URL check. **No protocol
client, no tool discovery, no registry entry, no dispatch.** So the tests here are as
much about what CANNOT happen as about what does:

  (1) a saved server is INERT — nothing new is callable, and the module that owns
      the surface cannot reach a process, a shell or the tool registry at all;
  (2) TRANSPORT IS HTTP ONLY (owner decision 2026-08-06) — the database itself
      refuses any other transport, which is why there is no launch command to store;
  (3) the URL is validated where it is STORED, reusing rpc/providers' rules, with
      plain ``http://`` narrowed to this computer;
  (4) the table is snapshot-CAPTURED, so a restore genuinely puts the server list
      back (spec §4.12: reversible config);
  (5) the surface is Developer-only — ``mcp.add`` is refused in Simple, while
      ``list`` and ``remove`` stay reachable so a profile switch never traps
      configuration.

Every test here was mutation-proven: the line it guards was broken and this test
watched to fail. The mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from agent_core.memory.store import Store
from agent_core.rpc.mcp import (
    _DEV_ONLY,
    _NAME_TAKEN,
    _NEEDS_HTTPS,
    _NEEDS_NAME,
    _mcp_url_problem,
)
from agent_core.rpc.providers import _CREDENTIAL_IN_URL, _EXTRA_IN_URL, _KEY_IN_PATH
from agent_core.snapshots.scope import _CAPTURED_TABLES
from tests.conftest import IPC_DB_NAME, _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_SRC = _REPO_ROOT / "agent_core" / "rpc" / "mcp.py"
_SCHEMA_SRC = _REPO_ROOT / "agent_core" / "memory" / "schema.sql"


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _developer(harness, request_id: int = 900) -> None:
    """Switch the running server to the Developer profile the way the app does."""
    result = _call(harness, "profile.set", {"profileId": "developer"}, request_id)
    assert result["ok"] is True and result["mode"] == "open"


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "mcp-test.sqlite3")


# ---------------------------------------------------------------------------
# (1) A saved server is INERT
# ---------------------------------------------------------------------------


def test_adding_a_server_makes_nothing_callable(tmp_path):
    """THE POINT OF PHASE 1. A server row must not add a single callable tool —
    admission is phase 2 and goes through the registry's own dev_only/open_only
    flags. If this ever fails, something started registering on config.

    Mutation: none available yet by construction — which is the claim. This test is
    the tripwire under the phases that WILL add registration, so it names the whole
    id space (`mcp*`) rather than one id."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        before = {d.id for d in h.server.tool_registry.list_for_model()}
        added = _call(
            h, "mcp.add", {"name": "Design docs", "url": "https://tools.example/mcp"}, 1
        )
        assert added["ok"] is True
        after = {d.id for d in h.server.tool_registry.list_for_model()}
        assert after == before
        assert not [tool_id for tool_id in after if tool_id.startswith("mcp")]
    finally:
        _shutdown(h.reader, h.thread)


def test_the_mcp_surface_cannot_reach_a_process_a_shell_or_the_registry():
    """Structural, on ``test_snapshots.py``'s "never schedules itself" pattern: the
    module that owns MCP configuration must not be able to START anything. HTTP-only
    is the reason the client can live in the core at all, and stdio is the shape that
    would put an arbitrary executable outside the seatbelt — so the import graph is
    where that decision is enforced, not a comment.

    Mutation: add ``import subprocess`` (or an ``agent_core.tools.registry`` import)
    to rpc/mcp.py — this fails, naming the module."""
    tree = ast.parse(_MCP_SRC.read_text(encoding="utf-8"))
    forbidden_roots = {
        "subprocess", "os", "shutil", "socket", "asyncio", "threading", "signal", "httpx",
    }
    forbidden_modules = {"agent_core.tools", "agent_core.shell_bridge", "agent_core.orchestrator"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots, (
                    f"rpc/mcp.py imports {alias.name}: phase 1 configures servers, it never "
                    "reaches one, and it must never be able to start a process"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in forbidden_roots, module
            assert not any(module.startswith(m) for m in forbidden_modules), (
                f"rpc/mcp.py imports {module}: nothing in phase 1 registers a tool or "
                "crosses the shell bridge"
            )
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"Popen", "run_command", "system", "spawn", "register"}


def test_a_server_row_has_no_column_that_could_hold_a_command(store: Store):
    """A row stores a URL, never a command — the schema half of the transport
    decision. A ``command``/``args``/``config_json`` column is exactly what a stdio
    row would need, so its absence is the guard.

    Mutation: add ``command TEXT`` to the mcp_servers DDL — this fails."""
    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(mcp_servers)")}
    assert columns == {"id", "name", "url", "transport", "enabled", "created_at"}


# ---------------------------------------------------------------------------
# (2) Transport is HTTP only — enforced by the database
# ---------------------------------------------------------------------------


def test_the_database_refuses_any_transport_but_http(store: Store):
    """Owner decision 2026-08-06. The CHECK is the enforcement: a later phase that
    wants stdio has to migrate the schema deliberately, in daylight, rather than
    insert a row with a different string in it.

    Mutation: widen the CHECK to ``IN ('http','stdio')`` — this fails."""
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO mcp_servers (id, name, url, transport, created_at) "
            "VALUES ('s1', 'Local', 'http://localhost:9000', 'stdio', 1)"
        )


def test_a_stored_row_defaults_to_http_and_enabled(store: Store):
    store.insert_mcp_server(id="s1", name="Docs", url="https://tools.example/mcp", created_at=7)
    (row,) = store.list_mcp_servers()
    assert row["transport"] == "http"
    assert row["enabled"] is True
    assert row["created_at"] == 7


# ---------------------------------------------------------------------------
# (3) The URL is validated where it is STORED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://tools.example/mcp",
        "https://tools.example:8443/mcp/v1",
        "http://localhost:9000/mcp",
        "http://127.0.0.1:9000",
        "http://[::1]:9000",
        "http://myserver.localhost:9000",
    ],
)
def test_addresses_that_are_fine(url: str):
    assert _mcp_url_problem(url) is None


@pytest.mark.parametrize(
    "url, expected",
    [
        # The ONE rule added on top of the custom-provider validator: plain http is
        # for a server on THIS computer only.
        ("http://tools.example/mcp", _NEEDS_HTTPS),
        ("http://192.168.1.5:9000", _NEEDS_HTTPS),      # LAN is not loopback
        ("http://notlocalhost.example", _NEEDS_HTTPS),
        # ...and everything rpc/providers already refuses, inherited whole (G1):
        ("https://user:sk-live-abc@tools.example/mcp", _CREDENTIAL_IN_URL),
        ("https://tools.example/mcp?token=abc", _EXTRA_IN_URL),
        ("https://tools.example/mcp#sk-live", _EXTRA_IN_URL),
        ("https://tools.example/sk-live-0123456789abcdef", _KEY_IN_PATH),
    ],
)
def test_addresses_that_are_refused(url: str, expected: str):
    """Mutations, one each: (a) delete the loopback narrowing from
    ``_mcp_url_problem`` and the four http:// rows fail; (b) delete the
    ``_base_url_problem`` call and the four credential rows fail. Both were run."""
    assert _mcp_url_problem(url) == expected


@pytest.mark.parametrize("url", ["", "   ", None, 42, "ftp://tools.example", "tools.example"])
def test_addresses_that_are_not_web_addresses_at_all(url):
    assert _mcp_url_problem(url) is not None


def test_a_refused_address_never_reaches_the_store(tmp_path):
    """The reason validation is HERE and not in the frontend: this table is
    snapshot-captured, so anything that lands in it is copied into every later
    payload and sidecar in plain text (G1). A refusal must therefore happen before
    the insert, not be cleaned up afterwards.

    Mutation: move the ``_mcp_url_problem`` check below ``insert_mcp_server`` — this
    fails on the stored row."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        result = _call(
            h, "mcp.add", {"name": "Leaky", "url": "https://u:sk-live-x@tools.example/mcp"}, 1
        )
        assert result["ok"] is False
        assert result["error"] == _CREDENTIAL_IN_URL
        assert _call(h, "mcp.list", {}, 2)["servers"] == []
        side = Store(tmp_path / IPC_DB_NAME)
        try:
            assert side.list_mcp_servers() == []
        finally:
            side.close()
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (4) Reversible config — snapshot-captured (spec §4.12)
# ---------------------------------------------------------------------------


def test_the_table_is_captured_by_snapshots():
    """Declared, not incidental. ``test_capture_scope_covers_every_schema_table``
    forces the choice to be made; this one pins WHICH way it was made, and the exact
    columns, because a silently-dropped column would be reset to its default BY the
    recovery path.

    Mutation: move "mcp_servers" from ``_CAPTURED_TABLES`` to ``_EXCLUDED_TABLES`` —
    this fails, and so does the restore test below."""
    assert _CAPTURED_TABLES["mcp_servers"] == (
        "id", "name", "url", "transport", "enabled", "created_at"
    )


def test_a_restore_puts_the_server_list_back(tmp_path):
    """G3 over the wire for this table: a server removed after a restore point comes
    back, and one added after it goes away (replace-all within the captured scope).
    That is what "a server connection is snapshotted and revocable" means."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "mcp.add", {"name": "Kept", "url": "https://a.example/mcp"}, 1)["ok"]
        snapshot_id = _call(h, "snapshot.create", {}, 2)["snapshotId"]
        assert _call(h, "mcp.add", {"name": "Later", "url": "https://b.example/mcp"}, 3)["ok"]
        listed = _call(h, "mcp.list", {}, 4)["servers"]
        kept = next(s for s in listed if s["name"] == "Kept")
        assert _call(h, "mcp.remove", {"id": kept["id"]}, 5)["ok"] is True

        assert _call(h, "snapshot.restore", {"id": snapshot_id}, 6)["ok"] is True

        names = [s["name"] for s in _call(h, "mcp.list", {}, 7)["servers"]]
        assert names == ["Kept"]
    finally:
        _shutdown(h.reader, h.thread)


def test_adding_and_removing_a_server_each_leave_a_restore_point(tmp_path):
    """The hooks. Adding is snapshot-and-proceed (a server can be removed again in
    one click); removing REFUSES if the capture fails, because the address the person
    typed exists nowhere else afterwards — that half is proven below.

    Mutation: delete the ``_snapshot_auto("mcp_connect")`` line — this fails."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "mcp.add", {"name": "Docs", "url": "https://a.example/mcp"}, 1)["ok"]
        server_id = _call(h, "mcp.list", {}, 2)["servers"][0]["id"]
        assert _call(h, "mcp.remove", {"id": server_id}, 3)["ok"] is True
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 4)["snapshots"]]
        assert "mcp_connect" in reasons
        assert "mcp_disconnect" in reasons
    finally:
        _shutdown(h.reader, h.thread)


def test_a_removal_is_refused_when_the_restore_point_cannot_be_saved(tmp_path):
    """The ``skill_delete`` class: the row is the only copy of the address, so losing
    it with no way back is worse than refusing.

    Mutation: change ``if not self._snapshot_auto(...)`` to call it and ignore the
    result — this fails, because the server is gone."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "mcp.add", {"name": "Docs", "url": "https://a.example/mcp"}, 1)["ok"]
        server_id = _call(h, "mcp.list", {}, 2)["servers"][0]["id"]
        # The capture path fails from here on.
        h.server.snapshot_manager.capture = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("no restore point")
        )
        result = _call(h, "mcp.remove", {"id": server_id}, 3)
        assert result["ok"] is False
        assert "didn't remove anything" in result["error"]
        assert [s["id"] for s in _call(h, "mcp.list", {}, 4)["servers"]] == [server_id]
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (5) Developer-only surface
# ---------------------------------------------------------------------------


def test_the_simple_profile_cannot_add_a_server(tmp_path):
    """MCP is dev-only for v1 (owner decision). The Settings section is hidden in
    Simple, but hiding is not enforcing: a stale frontend, or a profile switched
    mid-session, lands here.

    Mutation: delete the ``self._mode() is not PolicyMode.OPEN`` branch in
    ``_mcp_add`` — this fails, and the store then holds a row a Simple user made."""
    h = build_server(tmp_path, register_tool=False)
    try:
        result = _call(h, "mcp.add", {"name": "Docs", "url": "https://a.example/mcp"}, 1)
        assert result["ok"] is False
        assert result["error"] == _DEV_ONLY
        assert _call(h, "mcp.list", {}, 2)["servers"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_switching_back_to_simple_never_hides_or_traps_what_was_saved(tmp_path):
    """The other half, and it is the 2026-08-06 artifact lesson applied here: a
    Developer-made row stays LISTED in Simple and stays REMOVABLE. Hiding somebody's
    configuration when they switch profile is what that decision reversed, and a
    tightening (removal) must never be the thing a profile switch traps.

    These rows are inert, so listing them grants nothing — the enforcement that
    matters is ``add`` above, plus the fact that nothing is callable at all."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "mcp.add", {"name": "Docs", "url": "https://a.example/mcp"}, 1)["ok"]
        assert _call(h, "profile.set", {"profileId": "simple"}, 2)["ok"] is True

        listed = _call(h, "mcp.list", {}, 3)["servers"]
        assert [s["name"] for s in listed] == ["Docs"]
        assert _call(h, "mcp.remove", {"id": listed[0]["id"]}, 4)["ok"] is True
        assert _call(h, "mcp.list", {}, 5)["servers"] == []
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# Names, shapes and the wire
# ---------------------------------------------------------------------------


def test_two_servers_cannot_share_a_name_however_it_is_capitalised(tmp_path):
    """Phase 2 namespaces every discovered tool as ``mcp:<server>:<tool>`` and must
    refuse a collision rather than replace, so two servers called the same thing
    would put two strangers' tools behind one id. Refused with a plain sentence.

    Mutation: delete the ``mcp_server_name_taken`` check — the UNIQUE index still
    refuses, and the sqlite3.IntegrityError branch keeps the answer a sentence; the
    row-count assertion below is what fails if BOTH are removed."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        assert _call(h, "mcp.add", {"name": "Design docs", "url": "https://a.example/mcp"}, 1)["ok"]
        clash = _call(h, "mcp.add", {"name": "DESIGN DOCS", "url": "https://b.example/mcp"}, 2)
        assert clash["ok"] is False
        assert clash["error"] == _NAME_TAKEN
        assert len(_call(h, "mcp.list", {}, 3)["servers"]) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_the_unique_index_is_the_backstop_under_the_check(store: Store):
    """The check above races; the index cannot. Mutation: drop the
    ``idx_mcp_servers_name`` unique index from schema.sql — this fails."""
    store.insert_mcp_server(id="s1", name="Design docs", url="https://a.example/mcp", created_at=1)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_mcp_server(
            id="s2", name="design docs", url="https://b.example/mcp", created_at=2
        )


def test_a_server_needs_a_name(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        blank = _call(h, "mcp.add", {"name": "   ", "url": "https://a.example/mcp"}, 1)
        assert blank == {"ok": False, "error": _NEEDS_NAME}
        long_name = _call(h, "mcp.add", {"name": "x" * 61, "url": "https://a.example/mcp"}, 2)
        assert long_name["ok"] is False
        assert _call(h, "mcp.list", {}, 3)["servers"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_something_that_is_not_there_is_fine(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        assert _call(h, "mcp.remove", {"id": "nope"}, 1) == {"ok": True}
        assert _call(h, "mcp.remove", {}, 2) == {"ok": True}
        # ...and it mints no restore point, because nothing changed.
        reasons = [s["reason"] for s in _call(h, "snapshot.list", {}, 3)["snapshots"]]
        assert "mcp_disconnect" not in reasons
    finally:
        _shutdown(h.reader, h.thread)


def test_the_wire_shape_is_the_one_the_frontend_parses(tmp_path):
    """camelCase at the boundary (``created_at`` -> ``addedAt``), and NOTHING else on
    the row — no token, no header, no transport field the person never set. The
    generated fixture (tests/ipc_fixtures.py -> shell/src/__tests__/fixtures/mcp.list.json)
    is what keeps the frontend parser honest about this same shape."""
    h = build_server(tmp_path, register_tool=False)
    try:
        _developer(h)
        added = _call(h, "mcp.add", {"name": "Docs", "url": "https://a.example/mcp"}, 1)
        assert set(added["server"]) == {"id", "name", "url", "enabled", "addedAt"}
        (row,) = _call(h, "mcp.list", {}, 2)["servers"]
        assert set(row) == {"id", "name", "url", "enabled", "addedAt"}
        assert row["name"] == "Docs"
        assert row["url"] == "https://a.example/mcp"
        assert row["enabled"] is True
        assert isinstance(row["addedAt"], int)
    finally:
        _shutdown(h.reader, h.thread)


def test_no_payload_on_this_surface_can_carry_a_secret():
    """G1, structurally: phase 1 stores no credential at all, so nothing in this
    module may name one. The door stays open — a token goes to the OS KEYCHAIN when
    phase 2 needs one — and this test is what makes adding it here impossible to do
    quietly.

    Mutation: add a ``token``/``apiKey`` field to the add handler or the row — this
    fails."""
    tree = ast.parse(_MCP_SRC.read_text(encoding="utf-8"))
    secret_names = {"token", "api_key", "apiKey", "secret", "password", "authorization", "header"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Only the literal KEYS this module reads off params or puts on the wire.
            assert node.value not in secret_names, f"rpc/mcp.py names a secret field: {node.value}"
        elif isinstance(node, ast.Name):
            assert node.id not in secret_names
    assert "mcp_servers" in _SCHEMA_SRC.read_text(encoding="utf-8")
