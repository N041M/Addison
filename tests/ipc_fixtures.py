"""Shared IPC payload fixtures — the cross-process contract artifact (§9 spirit).

The frontend's defensive parsers (shell/src/lib/parse.ts, shell/src/ipc/client.ts)
are the only thing standing between a shifted core payload and a crashed webview,
and until now their tests used hand-written copies of the core's shapes. This
module generates the REAL payloads by calling the same handler methods the
JSON-RPC dispatch uses, and writes them to shell/src/__tests__/fixtures/*.json —
one artifact both sides share:

- tests/test_ipc_fixture_drift.py regenerates live and fails if a handler's
  shape drifts from the committed files (regenerate: ``python tests/ipc_fixtures.py``
  from the repo root, then re-run the vitest suite);
- the vitest suites consume the same files: parsers.fixtures.test.ts pins what
  each parser makes of a request result, and activityPanel.test.tsx renders the
  tool.activityUpdate notification through the real component.

So a core change that would break the frontend parsers fails CI on whichever
side runs first — the method-name drift test covers *names*, this covers *shapes*.

Determinism: usage rows use fixed year-2100 epoch timestamps (far inside any
future "this month" window for ``usage_totals_since``), so ``checkedAt`` and the
token totals are byte-stable no matter when the fixtures are regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.models_catalog import CloudModel, EffortLevel
from agent_core.profiles import SIMPLE
from agent_core.providers.base import ModelResponse, ModelRole, ProviderCapabilities
from agent_core.providers.router import ModelRouter
from agent_core.secret_presence import SecretPresence
from agent_core.snapshots.model import ConfigSnapshot
from agent_core.snapshots.scope import _CAPTURED_TABLES
from agent_core.snapshots.snapshot_manager import _canonical, _fingerprint
from agent_core.tools.base import call_permission_detail
from agent_core.tools.read_web_page import ReadWebPageTool
from agent_core.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = _REPO_ROOT / "shell" / "src" / "__tests__" / "fixtures"

# Fixed epoch timestamps (2100-01-01 + offsets): always inside the current
# month-window queries, never pruned, byte-stable in the emitted payloads.
_T0 = 4102444800

# Every captured table present and empty — the smallest payload that genuinely
# DECODES, which it has to, because `lastWorkingId` is only filled in when the
# restore walk can actually read the candidate it would target. A blob the
# decoder rejects would silently drop those three fields out of the fixture and
# pin the frontend against a payload the real one never has.
_FIXTURE_TABLES: dict[str, list] = {table: [] for table in _CAPTURED_TABLES}
# Rows holding identical tables share a fingerprint — exactly what two real
# captures of an unchanged config produce, since the fingerprint is over
# `tables` alone (contract §5.5 item 6).
_FIXTURE_FINGERPRINT = _fingerprint(_FIXTURE_TABLES)


def _fixture_payload(
    *,
    snapshot_id: str,
    created_at: int,
    trigger: str,
    reason: str,
    verified: bool,
    undeletable: bool,
    binary_ref: str | None,
) -> str:
    """One snapshot payload in the shape ``SnapshotManager._write_row`` produces.

    Built through the real serialiser and the real fingerprint rather than
    hand-written, and carrying the FULL `meta` block, because `meta` is not
    decoration — it is the row's only backup (contract §5.5 item 7). A fixture
    whose meta is `{}` is not a payload this system can produce, and it cannot
    catch the regression it is here to catch: `rebuild_rows_from_payloads` reads
    `meta["id"]` and skips any payload without one, so a cold rebuild from such
    a fixture writes zero rows — the G4 anchor included — and the fixture stays
    green throughout.

    Timestamps are fixed rather than clock-read so the emitted JSON is
    byte-stable; real captures stamp `time.time_ns()`, which is only there to
    break same-second ties and would make this file flap on every regeneration.
    """
    return _canonical(
        {
            "version": 1,
            "captured_at": created_at,
            "captured_at_ns": created_at * 1_000_000_000,
            "meta": {
                "id": snapshot_id,
                "trigger": trigger,
                "reason": reason,
                "created_in_mode": "safe",
                "state_fingerprint": _FIXTURE_FINGERPRINT,
                "verified_working": int(verified),
                "undeletable": int(undeletable),
                "captures_binary": int(binary_ref is not None),
                "binary_ref": binary_ref,
            },
            "tables": _FIXTURE_TABLES,
        }
    )


class _StubProvider:
    """Satisfies ModelProvider for router registration; never actually called —
    the fixture handlers only *list* the router's configuration."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        raise AssertionError("fixture stub provider is never invoked")


def _seeded_store(db_path: Path) -> Store:
    """A Store holding one deterministic row of everything the handlers read."""
    store = Store(db_path)
    # Suppress first-run widget seeding (_seed_default_widgets) so this fixture keeps
    # exactly its three explicit widgets and the committed widget.list.json stays at 3.
    store.set_setting("widgets_seeded", "1")
    # Developer profile → OPEN mode: the richest payloads (command widget visible,
    # profile.get shows the relaxed mode). The parsers' SAFE fallbacks are covered
    # by the junk-input tests; the fixtures pin the fullest real shape.
    store.set_setting("active_profile", "developer")
    # Exactly one connected cloud provider → one "reachable" connection row in
    # stats.get. It is a STORED ROW rather than a key probe because presence left
    # the keychain (plan §4.1): stats.get is polled, so it answers from
    # provider_config and never asks the OS whether a key is saved.
    store.upsert_provider_config(
        "anthropic",
        connected=True,
        added_at=_T0,
        last_check_ok=True,
        secret_presence=SecretPresence.PRESENT,
    )
    for i, (provider, inp, out, ms) in enumerate(
        [("anthropic", 1200, 400, 850), ("anthropic", 300, 90, 640), ("openai", 500, 120, 720)]
    ):
        store.insert_usage(
            id=f"usage-fixture-{i}",
            conversation_id="conv-fixture",
            provider=provider,
            model="model-fixture",
            input_tokens=inp,
            output_tokens=out,
            latency_ms=ms,
            created_at=_T0 + i,
        )
    for i, (spec, mode) in enumerate(
        [
            ({"kind": "routine", "routineId": "routine-morning-brief", "title": "Morning brief"},
             "safe"),
            ({"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}, "safe"),
            ({"kind": "command", "command": "git status", "title": "Repo status"}, "open"),
            # An interactive kind WITH state (step 6 half A). The fixture carries
            # one on purpose: `state` is a new optional key on the widget.list row,
            # and a fixture without a single stateful widget would pin the exact
            # payload the parsers must not be judged on.
            ({"kind": "checklist", "items": ["Buy milk", "Call Ana"], "title": "Saturday"},
             "safe"),
        ]
    ):
        store.insert_widget(
            id=f"widget-fixture-{i}",
            spec_json=json.dumps(spec),
            pinned=i == 0,
            position=i,
            created_at=_T0 + i,
            created_in_mode=mode,
        )
    # Half-ticked, so the fixture distinguishes a state that was read from one that
    # was defaulted — an all-False array is what a parser produces when it gives up.
    store.set_widget_state(
        "widget-fixture-3", json.dumps({"checked": [True, False]}), _T0
    )
    # G3 snapshots. Seeded HERE, before the server builds, because the genesis
    # snapshot is only written when the table is empty — so seeding first is what
    # keeps this fixture at exactly these three rows. One ordinary auto row, one
    # on-command verified row (the restore target), one permanent G4 anchor.
    for i, (trigger, reason, verified, undeletable, binary_ref) in enumerate(
        [
            ("auto", "mode_switch", False, False, None),
            ("on_command", "user_request", True, False, None),
            ("auto", "guard_weakened", True, True, '{"version": "0.1.0"}'),
        ]
    ):
        snapshot_id = f"snapshot-fixture-{i}"
        store.insert_config_snapshot(
            ConfigSnapshot(
                id=snapshot_id,
                created_at=_T0 + i,
                trigger=trigger,
                reason=reason,
                payload_version=1,
                state_blob=_fixture_payload(
                    snapshot_id=snapshot_id,
                    created_at=_T0 + i,
                    trigger=trigger,
                    reason=reason,
                    verified=verified,
                    undeletable=undeletable,
                    binary_ref=binary_ref,
                ),
                # The real fingerprint of the real tables, matching the payload's
                # own `meta`. It is a fixed value in practice because the tables
                # are, so the emitted fixture stays byte-stable.
                state_fingerprint=_FIXTURE_FINGERPRINT,
                verified_working=verified,
                undeletable=undeletable,
                captures_binary=binary_ref is not None,
                binary_ref=binary_ref,
                created_in_mode="safe",
            )
        )
    return store


def _catalog() -> list[CloudModel]:
    effort = (
        EffortLevel("low", "low"),
        EffortLevel("high", "high", default=True),
        EffortLevel("xhigh", "xhigh"),
    )
    return [
        CloudModel(
            id="claude-opus-4-8",
            label="Claude Opus 4.8",
            description="",
            adaptive_thinking=True,
            effort_levels=effort,
            default=True,
        ),
        CloudModel(
            id="claude-haiku-4-5-20251001",
            label="Claude Haiku 4.5",
            description="",
        ),
        CloudModel(
            id="gpt-fixture",
            label="Fixture GPT",
            description="",
            provider="openai",
        ),
    ]


# A page read with a query string on it. The committed fixture is the proof of the
# property that matters: what reaches the frontend is `en.wikipedia.org` and NOT this
# whole string. A full URL on screen would be its own leak — the query is where an
# injected instruction would put whatever it wanted carried out of the machine, and a
# panel is a thing people screenshot.
_ACTIVITY_FIXTURE_URL = "https://en.wikipedia.org/wiki/Fern?utm_source=addison&note=hello"


def _activity_notification(server: JsonRpcServer) -> dict:
    """The ``tool.activityUpdate`` params emitted for a call that names a destination.

    This one is a notification, not a request result: there is no handler to call and
    the fixture server has no writer, so the frame is captured by standing in for
    ``_write_frame``. That is deliberate — it keeps the fixture coming from the
    shipping emit path (``_emit_activity`` -> ``_notify`` -> the frame) instead of a
    dict hand-written to match it, which is the failure this whole module exists to
    prevent.

    The detail is asked for exactly the way ``orchestrator.py`` asks — through
    ``call_permission_detail``, with no mention of which tool this is — so the fixture
    also pins the general path, not a read_web_page special case. Using the real tool
    means the day its ``permission_detail`` stops returning just the host, this
    fixture changes and the drift test says so out loud.
    """
    tool = ReadWebPageTool()
    captured: list[dict] = []
    original = server._write_frame
    server._write_frame = captured.append  # type: ignore[method-assign]
    try:
        server._emit_activity(
            tool.definition.id,
            tool.definition.label,
            call_permission_detail(tool, {"url": _ACTIVITY_FIXTURE_URL}),
        )
    finally:
        server._write_frame = original  # type: ignore[method-assign]
    return captured[0]["params"]


def generate_fixtures(tmp_dir: Path) -> dict[str, dict]:
    """Method name -> the exact payload the core puts on the wire for it today.

    Mostly request results, read straight off their handlers; ``tool.activityUpdate``
    is a Core -> Frontend notification and carries its ``params`` instead.
    """
    router = ModelRouter(configured={ModelRole.PRIMARY: _StubProvider()})
    router.register_local_model("llama3.2:3b", _StubProvider())

    def _down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = JsonRpcServer(
        reader=None,
        writer=None,
        tool_registry=ToolRegistry(),
        store_factory=lambda: _seeded_store(tmp_dir / "fixtures.sqlite3"),
        model_router=router,
        cloud_catalog=_catalog(),
        # Ollama probe fails fast → the deterministic "idle / not running" row.
        ollama_base_url="http://127.0.0.1:11434",
        ollama_client=httpx.Client(transport=httpx.MockTransport(_down)),
    )
    server._ensure_built()
    return {
        "stats.get": server._stats_get(),
        "widget.list": server._widget_list(),
        "profile.get": server._profile_get(),
        "model.availableRoles": server._available_roles(),
        "snapshot.list": server._snapshot_list(),
        # Step-4/5 payloads. These are here because their absence had a cost: the
        # frontend read `workspace.list` as `{roots}` while the core sent
        # `{folders}`, the trusted-folder list rendered empty in the shipped app,
        # and both suites stayed green because each asserted its own idea of the
        # shape. A fixture generated from the real handler is the only artifact
        # both sides share, so add one for every new payload a parser consumes.
        "workspace.list": _workspace_list_fixture(server),
        "mcp.list": _mcp_list_fixture(server),
        "automation.list": _automation_list_fixture(server),
        # The same method in the OTHER profile. Not a method name — the only fixture
        # key that is not one — because the payload genuinely has two shapes and the
        # frontend has to render both: a marked row is what Simple gets for every
        # automation it holds (step 8 phase 4), and no single call can show both.
        "automation.list.simple": _automation_list_simple_fixture(server),
        "costPlan.propose": server._cost_plan_propose(),
        "endpoint.proposeFromConversation": server._endpoint_propose(),
        "tool.activityUpdate": _activity_notification(server),
    }


def _workspace_list_fixture(server: JsonRpcServer) -> dict:
    """A workspace.list payload with a row in it — an empty list would parse the
    same whichever key the frontend read, which is exactly how the `roots`/`folders`
    mismatch survived. The root is a fixed literal (never a real directory) so the
    fixture is byte-stable and names nobody's home folder."""
    server.store.insert_workspace_trust(root="/fixture/project", granted_at=_T0)
    try:
        return server._workspace_list()
    finally:
        server.store.delete_workspace_trust("/fixture/project")


def _mcp_list_fixture(server: JsonRpcServer) -> dict:
    """An mcp.list payload with a row in it (step 7) — an empty list would
    parse the same whichever key the frontend read, which is exactly how the
    `roots`/`folders` mismatch above survived both suites. Written through the store
    and read back through the real handler, so the camelCase mapping (`created_at`
    -> `addedAt`) is pinned rather than assumed. The address is a fixed literal.

    THREE rows, one per discovery state, because the shape is not one shape:
    an unchecked row carries no `checkedAt` at all, a checked one carries its tools
    and counts, and a failed one carries a plain sentence and no tool list. A fixture
    with only the happy row would let the parser drop `error`, or invent a `toolCount`
    for a server that answered nothing, and both suites would stay green.

    The discovered row is produced through the REAL catalog and the REAL registry, so
    the wire shape is what a genuine refresh emits — **including a tool REFUSED for an
    id collision**, which is why the ghost registration below exists. The collision is
    arranged rather than described: an id is taken first, by a catalog entry with no
    `mcp_servers` row behind it (so it appears in no payload), and the checked server
    then offers a tool that composes to the same id. What that pins is the property a
    hand-fed count cannot — `toolCount` and `tools` describe what was REGISTERED, the
    refused name is in neither, and `skipped` carries both what the client turned away
    and what admission did. A fixture where `skipped` is simply a number passed in
    tests the parser against arithmetic nobody performed.

    Everything is torn down afterwards so the rest of the fixtures (snapshot payloads
    capture this table) stay byte-stable and no fixture tool is left in the registry."""
    from agent_core.mcp_client import DiscoveredTool

    rows = [
        ("mcp-fixture-0", "Fixture tool server", "https://tools.example/mcp"),
        ("mcp-fixture-1", "Checked server", "https://checked.example/mcp"),
        ("mcp-fixture-2", "Unreachable server", "https://offline.example/mcp"),
    ]
    for index, (server_id, name, url) in enumerate(rows):
        server.store.insert_mcp_server(id=server_id, name=name, url=url, created_at=_T0 + index)
    # The id `mcp:Checked server:open_ticket`, taken before the checked server is
    # admitted. Registered under a server id that has no row in `mcp_servers`, so it
    # is in the registry and in NO payload — the whole of its job is to be in the way.
    server._mcp_catalog.record_success(
        server.tool_registry,
        server_id="mcp-fixture-ghost",
        server_name="Checked server",
        tools=(DiscoveredTool("open_ticket", "Open a support ticket."),),
        skipped=0,
        checked_at=_T0,
    )
    server._mcp_catalog.record_success(
        server.tool_registry,
        server_id="mcp-fixture-1",
        server_name="Checked server",
        tools=(
            DiscoveredTool("search_docs", "Search the team's documentation."),
            DiscoveredTool("open_ticket", "Open a support ticket."),
        ),
        skipped=1,
        checked_at=_T0,
    )
    server._mcp_catalog.record_failure(
        server.tool_registry,
        server_id="mcp-fixture-2",
        error="Addison couldn't reach that server. Check the address, and that the server is running.",
        checked_at=_T0,
    )
    try:
        return server._mcp_list()
    finally:
        server._mcp_catalog.forget(server.tool_registry, "mcp-fixture-ghost")
        for server_id, _name, _url in rows:
            server._mcp_catalog.forget(server.tool_registry, server_id)
            server.store.delete_mcp_server(server_id)


def _automation_list_fixture(server: JsonRpcServer) -> dict:
    """An automation.list payload with a row in it (step 8, phase 2).

    Written through the store and read back through the REAL handler, so the two
    things this payload does at the boundary are pinned rather than assumed: the
    camelCase rename (``schedule_kind`` -> ``scheduleKind``) and — the field this
    fixture was added for — ``scheduleSentence``, which the core renders and the
    frontend prints without touching. A hand-written fixture would let the two
    drift into two different renderings of one schedule, which is exactly the
    failure mode the sentence exists to prevent.

    THREE ROWS, one per meaningful state, because the shape is not one shape:

      * an ``interval`` row (its sentence collapses 60 minutes to "Every hour");
      * a ``calendar`` row WITH a weekday, so the day name and the two-digit minute
        are in the artifact rather than in somebody's memory of the function;
      * a row whose ``schedule_json`` is JUNK — which is what a hand edit, an older
        build or a restored payload can genuinely put in that column. It must arrive
        as ``{}`` and "No schedule saved yet.", and it must not take the other two
        off the list with it. A fixture with only well-formed rows would let the
        frontend's fallback go untested against a real payload and let the core
        start raising on a bad row without either suite noticing.

    Nothing here is armed and nothing here could be: no field on this payload says
    so, and the plist a job would be armed from is the shell's to build.

    This is the DEVELOPER shape — the whole fixture store runs the Developer profile
    (see ``_seeded_store``), so no row here carries ``unavailable``. That key exists
    only while Simple is active and then on EVERY row, so the two shapes cannot share
    one payload; ``automation.list.simple`` below is the other one.

    The rows are deleted afterwards so the snapshot payload fixtures (which capture
    this table) stay byte-stable."""
    rows = [
        (
            "automation-fixture-0",
            "Tidy up downloads",
            "com.addison.auto.tidy-downloads",
            "/usr/bin/find ~/Downloads -mtime +30 -delete",
            "interval",
            json.dumps({"minutes": 60}),
        ),
        (
            "automation-fixture-1",
            "Back up notes",
            "com.addison.auto.backup-notes",
            "/usr/local/bin/backup-notes --to ~/Backups",
            "calendar",
            json.dumps({"hour": 7, "minute": 30, "weekday": 1}),
        ),
        (
            "automation-fixture-2",
            "Something older",
            "com.addison.auto.something-older",
            "/usr/bin/say hello",
            "interval",
            # Not JSON at all — the column is TEXT, and this is what a hand edit or an
            # older build's payload can genuinely leave in it.
            "every now and then",
        ),
    ]
    for index, (row_id, name, label, command, kind, schedule_json) in enumerate(rows):
        server.store.insert_automation(
            id=row_id,
            name=name,
            label=label,
            command=command,
            schedule_kind=kind,
            schedule_json=schedule_json,
            created_in_mode="open",
            created_at=_T0 + index,
        )
    try:
        return server._automation_list()
    finally:
        for row_id, *_rest in rows:
            server.store.delete_automation(row_id)


def _automation_list_simple_fixture(server: JsonRpcServer) -> dict:
    """The SAME three rows, answered while the Simple profile is active (step 8,
    phase 4) — the one shape the payload above cannot carry.

    A SECOND file rather than a different one, because ``unavailable`` is present on
    every row or on none: an automation's payload is a shell command, so Simple can
    use none of them. Regenerating ``automation.list`` in Simple would have bought the
    disabled shape by giving up the available shape that every existing parser reads,
    and a fixture is only worth what both sides can be pinned against.

    Answered through the REAL handler with the REAL profile swapped underneath it,
    not by adding the key to a copied dict: the marker's sentence, its slug and the
    fact that it is ABSENT rather than null on an available row all come from the
    shipping code path, which is the whole point of generating these files. The
    profile is restored afterwards so every fixture after this one is unaffected."""
    previous = server._active_profile
    server._active_profile = SIMPLE
    try:
        return _automation_list_fixture(server)
    finally:
        server._active_profile = previous


def write_fixtures(tmp_dir: Path) -> list[Path]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in generate_fixtures(tmp_dir).items():
        path = FIXTURE_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for p in write_fixtures(Path(td)):
            print(f"wrote {p.relative_to(_REPO_ROOT)}")
