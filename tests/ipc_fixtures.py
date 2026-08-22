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

import hashlib
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
from agent_core.tools.base import ActionSnapshot, call_permission_detail
from agent_core.tools.read_web_page import ReadWebPageTool
from agent_core.tools.web_search import WebSearchTool
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
        "workspace.listDirectory": _workspace_list_directory_fixture(server),
        "workspace.readFile": _workspace_read_file_fixture(server),
        "workspace.listEdits": _workspace_list_edits_fixture(server),
        "workspace.readEditDiff": _workspace_read_edit_diff_fixture(server),
        "workspace.revertFile": _workspace_revert_file_fixture(server),
        "mcp.list": _mcp_list_fixture(server),
        "channel.list": _channel_list_fixture(server),
        "automation.list": _automation_list_fixture(server),
        # The same method in the OTHER profile. Not a method name — the only fixture
        # key that is not one — because the payload genuinely has two shapes and the
        # frontend has to render both: a marked row is what Simple gets for every
        # automation it holds (step 8 phase 4), and no single call can show both.
        "automation.list.simple": _automation_list_simple_fixture(server),
        "automation.disarmOrphan": _automation_disarm_orphan_fixture(server),
        "routine.importPreview": _routine_import_preview_fixture(server),
        "costPlan.propose": server._cost_plan_propose(),
        "endpoint.proposeFromConversation": server._endpoint_propose(),
        "tool.activityUpdate": _activity_notification(server),
    }


# The shared-routine file the import fixture previews. A fixed literal, so the
# committed payload is byte-stable, and deliberately CLEAN: the flagged shape is
# pinned in pytest (tests/test_routine_import.py), where an injection string can
# live without being copied into a file the frontend suite reads on every run.
_IMPORT_FIXTURE_FILE = json.dumps(
    {
        "addison_routine": {"version": 1},
        "name": "Morning news",
        "description": "Looks up one topic each morning.",
        "variables": [{"name": "topic", "prompt": "What should I look up?", "default": None}],
        "steps": [
            {
                "step_id": "step_1",
                "tool_id": "web_search",
                "args_template": {"query": "{{topic}}"},
                "depends_on": [],
                "on_failure": "abort",
                "model_role": None,
            }
        ],
    }
)


class _FixtureFileBridge:
    """The shell's half of the import picker, answering one fixed file."""

    def pick_file(self) -> str:
        return "fixture-handle"

    def read_scoped_file(self, file_handle: str) -> dict:
        return {"content": _IMPORT_FIXTURE_FILE, "kind": "text"}


def _routine_import_preview_fixture(server: JsonRpcServer) -> dict:
    """A ``routine.importPreview`` payload with every optional part decided.

    Generated through the real handler, which is the point: the assurances, the
    numbered step list and the absent `screeningNote` are what the frontend renders,
    and a hand-written copy of them is exactly the drift this module exists to catch.
    The registry has to hold the step's tool for the preview to succeed, so the
    server is given the real one for the length of this call and put back after.

    NOTHING IS SAVED by a preview, so this leaves no row behind and the fixtures
    after it are unaffected.
    """
    previous_bridge = server._shell_bridge
    previous_registry = server.tool_registry
    registry = ToolRegistry()
    registry.register(WebSearchTool())
    server._shell_bridge = _FixtureFileBridge()  # type: ignore[assignment]
    server.tool_registry = registry
    captured: list[dict] = []
    original = server._write_frame
    server._write_frame = captured.append  # type: ignore[method-assign]
    try:
        server._handle_routine_import_preview(1)
    finally:
        server._write_frame = original  # type: ignore[method-assign]
        server.tool_registry = previous_registry
        server._shell_bridge = previous_bridge
    return captured[0]["result"]


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


class _FixtureBrowseBridge:
    """The shell's half of the review surface's two read paths, answering fixed rows.

    A fake rather than the real bridge because there is no shell in this process at all;
    the payload that matters is the CORE's, and everything the core adds — the resolved
    ``directory``/``path``, ``root``, and ``escapes`` on every entry — is produced by the
    real handler over these rows."""

    def list_workspace_directory(self, path: str) -> dict:
        return {
            "entries": [
                {"name": ".git", "kind": "directory", "size": 96},
                {"name": "README.md", "kind": "file", "size": 812},
                {"name": "link", "kind": "symlink", "size": 11},
                {"name": "src", "kind": "directory", "size": 96},
            ],
            "truncated": False,
        }

    def read_workspace_file_for_view(self, path: str) -> dict:
        return {"content": "# Fixture project\n", "bytes": 18, "truncated": False}


def _with_browse_bridge(server: JsonRpcServer, directory: str, call):
    """Run one read-path handler with a trusted root and a fake shell in place, then put
    the server back exactly as it was — the ``automation.list.simple`` pattern, for the
    same reason: the payload must come from the shipping code path, and every fixture
    after this one must be unaffected."""
    previous = server._shell_bridge
    server._shell_bridge = _FixtureBrowseBridge()  # type: ignore[assignment]
    server.store.insert_workspace_trust(root=directory, granted_at=_T0)
    try:
        return call()
    finally:
        server.store.delete_workspace_trust(directory)
        server._shell_bridge = previous


def _workspace_list_directory_fixture(server: JsonRpcServer) -> dict:
    """A ``workspace.listDirectory`` payload with rows in it (Phase-3 plan Build §1).

    Four entries, one per KIND, because the frontend renders them differently and a
    listing with only files would let a parser drop the field that decides whether a row
    can be expanded — the ``roots``/``folders`` class of failure, which shipped green on
    both sides. ``.git`` is in there deliberately: nothing is hidden, and a fixture that
    quietly omitted it would be the first place that rule could rot.

    The root is a fixed literal, never a real directory, so the file is byte-stable and
    names nobody's home folder. That costs one thing, and it is worth stating: every
    ``escapes`` here is ``false``. ``escapes`` is computed by RESOLVING each entry, so a
    ``true`` needs a real symlink on a real disk — which would put a machine-specific
    tmp path in a committed file. The ``true`` case is pinned in pytest instead
    (``tests/test_review_surface_read_paths.py``), against a real link; what this file
    pins is the shape, which is what the frontend parser can get wrong."""
    return _with_browse_bridge(
        server,
        "/fixture/project",
        lambda: server._workspace_list_directory({"directory": "/fixture/project"}),
    )


def _workspace_read_file_fixture(server: JsonRpcServer) -> dict:
    """A ``workspace.readFile`` payload — text to SHOW, never to edit.

    ``bytes`` is the FILE's size rather than the excerpt's, which is the field most
    likely to be mistaken for "length of content" on the other side; it and
    ``truncated`` are the pair the UI needs to say how much of a large file is not on
    screen."""
    return _with_browse_bridge(
        server,
        "/fixture/project",
        lambda: server._workspace_read_file({"path": "/fixture/project/README.md"}),
    )


# --- the review surface's diff + revert (Phase-3 plan Build §2/§3) -----------
#
# The paths are fixed literals under a root that is not a real directory, exactly as
# the read-path fixtures are: byte-stable, and nobody's home folder in a committed
# file. Nothing here touches a disk — the shell's half is a fake, and what these files
# pin is the CORE's payload, which is where every derived field is computed.

_EDIT_ROOT = "/fixture/project"
_EDIT_APP = f"{_EDIT_ROOT}/src/app.py"
_EDIT_NOTES = f"{_EDIT_ROOT}/notes.md"
_EDIT_LEGACY = f"{_EDIT_ROOT}/legacy.txt"
_EDIT_GONE = f"{_EDIT_ROOT}/gone.txt"
_EDIT_OUTSIDE = "/fixture/elsewhere/orphan.txt"

_APP_BEFORE = "def main():\n    pass\n"
_APP_WROTE = "def main():\n    return 1\n"
_APP_ON_DISK = "def main():\n    return 1  # my own tweak\n"
_NOTES_WROTE = "# Notes\n"
_GONE_WROTE = "temporary\n"
_OUTSIDE_BEFORE = "old\n"
_OUTSIDE_WROTE = "new\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FixtureEditBridge:
    """The shell's half of the three questions the diff and the list ask it.

    A fake because there is no shell in this process and no files on disk — and the
    payload that matters is the CORE's, which computes `root`, `relativePath`,
    `onDiskChanged` and `missing` from what these answers say."""

    def can_restore_workspace_files(self, paths: list[str]) -> dict:
        # Everything except the legacy row, which stands in for the RESTART case: an
        # edit the database remembers perfectly and the session ledger does not.
        return {"restorable": {path: path != _EDIT_LEGACY for path in paths}}

    def digest_workspace_files(self, paths: list[str]) -> dict:
        answers = {
            # Edited by the person since Addison wrote it -> onDiskChanged true.
            _EDIT_APP: {"sha256": _sha(_APP_ON_DISK), "missing": False},
            # Byte-for-byte as Addison left it -> false.
            _EDIT_NOTES: {"sha256": _sha(_NOTES_WROTE), "missing": False},
            # A digest the row has nothing to compare against -> null, not false.
            _EDIT_LEGACY: {"sha256": _sha("whatever is there now\n"), "missing": False},
            _EDIT_GONE: {"sha256": None, "missing": True},
            _EDIT_OUTSIDE: {"sha256": _sha(_OUTSIDE_WROTE), "missing": False},
        }
        return {"digests": {path: answers[path] for path in paths}}

    def adopt_workspace_path(self, path: str, expected_sha256: str) -> dict:
        # The post-restart recovery, which this fixture's legacy row deliberately cannot
        # use: it recorded no digest, so nothing is ever asked about it.
        return {"adopted": False}

    def read_workspace_file_for_view(self, path: str) -> dict:
        content = _APP_ON_DISK if path == _EDIT_APP else ""
        return {
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "truncated": False,
        }

    def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
        # The revert fixture's write. Nothing to do — there is no disk in this process
        # — and the payload being pinned is what the CORE says afterwards.
        return None


def _with_edit_rows(server: JsonRpcServer, call):
    """Five unreverted `write_project_file` snapshots, a trusted root and a fake shell —
    then everything back exactly as it was, so the fixtures after this one are
    unaffected (`automation.list.simple`'s pattern, for its reason).

    FIVE ROWS ACROSS FIVE FILES and one of them written three times, because the shape
    is not one shape and every difference below is one the frontend renders
    differently:

      * `src/app.py` — THREE writes collapsed into one edit, and changed on disk since;
      * `notes.md` — CREATED by Addison (empty before pane, Revert removes it);
      * `legacy.txt` — a row from before `wrote_sha256` existed AND one the shell can no
        longer put back: `onDiskChanged: null` and `revertable: false`, the two honest
        unknowns, which a fixture of only happy rows would let a parser drop;
      * `gone.txt` — the file is not there any more;
      * `/fixture/elsewhere/orphan.txt` — outside every trusted root, so `root` is null
        and `relativePath` is the whole path. Trust was revoked after the edit; the plan
        requires the row to stay listed anyway, and a payload where `root` is always a
        string would let the other side type it that way."""
    rows = [
        # (id, path, existed, prior, wrote_sha256, created_at)
        ("edit-app-1", _EDIT_APP, True, _APP_BEFORE, _sha("def main():\n    ...\n"), _T0 + 1),
        ("edit-app-2", _EDIT_APP, True, "def main():\n    ...\n", _sha("interim\n"), _T0 + 2),
        ("edit-app-3", _EDIT_APP, True, "interim\n", _sha(_APP_WROTE), _T0 + 3),
        ("edit-notes", _EDIT_NOTES, False, None, _sha(_NOTES_WROTE), _T0 + 4),
        ("edit-legacy", _EDIT_LEGACY, True, "the older text\n", None, _T0 + 5),
        ("edit-gone", _EDIT_GONE, False, None, _sha(_GONE_WROTE), _T0 + 6),
        ("edit-outside", _EDIT_OUTSIDE, True, _OUTSIDE_BEFORE, _sha(_OUTSIDE_WROTE), _T0 + 7),
    ]
    for row_id, path, existed, prior, digest, created_at in rows:
        payload: dict = {"path": path, "existed": existed, "prior": prior}
        # ABSENT, never null, for the legacy row: that is what a row written before the
        # key existed actually looks like in the column, and "missing key" is the state
        # the three-valued answer is derived from.
        if digest is not None:
            payload["wrote_sha256"] = digest
        server.store.insert_action_snapshot(
            ActionSnapshot(
                id=row_id,
                tool_call_id=f"call-{row_id}",
                tool_id="write_project_file",
                undo_payload=payload,
                created_at=created_at,
            )
        )
    previous = server._shell_bridge
    server._shell_bridge = _FixtureEditBridge()  # type: ignore[assignment]
    # The manager holds its own reference, taken once at build time (main.py never
    # reassigns `_shell_bridge` after construction, so that is fresh in the app). A
    # fixture that swaps the server's bridge underneath a built manager has to swap
    # both, or the revert half would still be talking to the previous one.
    server.file_revert_manager._shell_bridge = server._shell_bridge  # type: ignore[assignment]
    server.store.insert_workspace_trust(root=_EDIT_ROOT, granted_at=_T0)
    try:
        return call()
    finally:
        server.store.delete_workspace_trust(_EDIT_ROOT)
        server._shell_bridge = previous
        server.file_revert_manager._shell_bridge = previous  # type: ignore[assignment]
        for row_id, *_rest in rows:
            server.store._conn.execute("DELETE FROM action_snapshots WHERE id = ?", (row_id,))
        server.store._conn.commit()


def _workspace_list_edits_fixture(server: JsonRpcServer) -> dict:
    """A `workspace.listEdits` payload (Build §2) — METADATA ONLY.

    No before/after text is in here and none should be: the whole reason this method is
    separate from `readEditDiff` is that a twenty-file turn's text is megabytes on one
    line. A fixture that carried it would make that mistake permanent by pinning it."""
    return _with_edit_rows(server, server._workspace_list_edits)


def _workspace_read_edit_diff_fixture(server: JsonRpcServer) -> dict:
    """A `workspace.readEditDiff` payload (Build §2) — the two panes for ONE file.

    `src/app.py`, the three-write row: BEFORE is the prior of the OLDEST of the three
    (where a revert lands, and what the person is therefore looking at when they press
    it), never the prior of the newest. Reading it off the wrong end of the chain is
    the single most likely way for this feature to be quietly wrong, and it is why the
    seed has three writes rather than one."""
    return _with_edit_rows(
        server, lambda: server._workspace_read_edit_diff({"path": _EDIT_APP})
    )


def _workspace_revert_file_fixture(server: JsonRpcServer) -> dict:
    """A `workspace.revertFile` answer (Build §3) — the SUCCESS shape.

    Produced by actually reverting the three-write chain through the real handler, so
    the sentence in `detail` is the one the app renders and not a copy of it. It names
    the file (never the full path, the rule every tool label follows) and says which of
    the two things happened — put back, or removed because Addison had created it.

    A refusal is `{ok: false, error}` and is pinned in pytest rather than here: it needs
    no fixture to be unambiguous, and a second file for it would pin the ABSENCE of
    `path`/`detail` as though that were a shape somebody could parse wrong."""
    return _with_edit_rows(
        server, lambda: server._workspace_revert_file({"path": _EDIT_APP})
    )


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


def _channel_list_fixture(server: JsonRpcServer) -> dict:
    """A ``channel.list`` payload with rows in it (messaging channels, phase 1).

    Written through the store and read back through the REAL handler, so the two
    things this payload does at the boundary are pinned rather than assumed: the
    camelCase renames (``created_at`` -> ``addedAt``, ``token_present`` ->
    ``tokenPresent``) and the derived ``pairedDevices`` count, which is a second
    table's answer folded into this row. A hand-written fixture would let either
    drift, and the `roots`/`folders` mismatch is what that costs.

    TWO ROWS, because one would parse the same whichever key the frontend read.
    Both are switched off with ``tokenPresent: "unknown"``, which is not a poverty of
    the fixture — it is the ONLY shape phase 1 can produce: nothing in the tree turns
    a channel on and nothing can ask a transport whether a token works. The day a
    later phase can write 'present' or `enabled: true`, this fixture grows the row
    that proves it, and until then a fixture claiming either would pin the parser
    against a payload the real handler never emits.

    ``pairedDevices`` is 0 on both for the same reason — phase 1 has no pairing — and
    the count is exercised anyway, because it is COMPUTED here rather than stored.

    Everything is torn down afterwards so the rest of the fixtures (the snapshot
    payloads capture this table) stay byte-stable."""
    rows = [
        ("channel-fixture-0", "telegram", "My phone"),
        ("channel-fixture-1", "telegram", "The kitchen tablet"),
    ]
    for index, (channel_id, kind, name) in enumerate(rows):
        server.store.insert_channel(id=channel_id, kind=kind, name=name, created_at=_T0 + index)
    try:
        return server._channel_list()
    finally:
        for channel_id, _kind, _name in rows:
            server.store.delete_channel(channel_id)


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


def _automation_disarm_orphan_fixture(server: JsonRpcServer) -> dict:
    """An ``automation.disarmOrphan`` answer — the REFUSAL shape, for a label Addison
    did not mint (2026-08-08).

    The refusal rather than the success, because the success is ``{"ok": true}`` and a
    fixture of it pins nothing the mutation parser could get wrong. This one carries
    the field the surface actually renders: ``error``, a plain sentence written by the
    core, which the section prints verbatim in preference to anything it would say
    itself. A payload that lost it would leave a person pressing a button that appears
    to do nothing.

    Deterministic with no shell in the process at all, and that is a property of the
    handler rather than of this fixture: a label outside Addison's own namespace is
    refused BEFORE the store is read and before the bridge is reached, which is the
    ordering the core tests pin by name."""
    return server._automation_disarm_orphan({"label": "com.example.somebody-elses-job"})


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
