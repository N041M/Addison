"""Workspace trust + the OPEN-mode coding harness — step 5.

Covers the contract's ten verification items, each written to fail when the line it
guards is reverted (mutation-proven; see docs/HANDOFF.md "How step 1 was verified"):

  (1) the data-dir floor (also in test_ipc_snapshots.py) + grantTrust refusing ~;
  (2) read_project_file{path:"/etc/passwd"} hard-refused (confinement) + a symlink
      inside a trusted root pointing out is refused (resolve-once);
  (3) write_project_file inside trust: no card, undoable (round-trip / created-file
      delete / binary refused / oversize refused); outside trust: refused, no write;
  (4) run_command inside a trusted cwd STILL cards with the command text;
  (5) write_project_file's undo is REGISTRATION-ENFORCED despite being OPEN-only;
  (6) a planted trust row for the data dir never confines (floor beats root);
  (7) SAFE untouched + ignores a supplied trusted bool;
  (8) a routine step / command widget always cards under trust;
  (9) restore never resurrects a revoked trust (excluded from snapshots).
Item (10) — read_web_page's SSRF suite — is unchanged here; no net-vet code moved.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

import pytest

from agent_core.main import JsonRpcServer, build_registry
from agent_core.memory.store import Store
from agent_core.orchestrator import _OUTSIDE_TRUST, Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import (
    PolicyMode,
    path_is_within,
)
from agent_core.profiles import DEVELOPER
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.rpc.workspace import is_trusted
from agent_core.snapshots import scope
from agent_core.snapshots.snapshot_manager import SnapshotManager
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.read_project_file import ReadProjectFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.write_project_file import WriteProjectFileTool


# --- fakes -----------------------------------------------------------------


class _FakeWorkspaceBridge:
    """A ShellBridge whose workspace file methods act on the real filesystem (tmp),
    mirroring the Rust shell closely enough to test undo round-trips + binary/oversize
    refusals at the Python layer."""

    _UNDO_SIZE_BOUND = 256 * 1024

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.restores: list[tuple[str, str | None]] = []

    def write_workspace_file(self, path: str, content: str) -> dict:
        existed = os.path.exists(path)
        prior: str | None = None
        if existed:
            data = Path(path).read_bytes()
            if len(data) > self._UNDO_SIZE_BOUND:
                raise RuntimeError("That file is too big for Addison to edit while keeping an undo.")
            try:
                prior = data.decode("utf-8")
            except UnicodeDecodeError:
                raise RuntimeError("That file isn't a text file, so Addison won't change it.")
        Path(path).write_text(content, encoding="utf-8")
        self.writes.append(path)
        return {"existed": existed, "prior": prior}

    def read_workspace_file(self, path: str) -> str:
        self.reads.append(path)
        return Path(path).read_text(encoding="utf-8")

    def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
        self.restores.append((path, prior_content))
        if prior_content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            Path(path).write_text(prior_content, encoding="utf-8")

    # The rest of the ShellBridge Protocol — unused here, present so the fake still
    # satisfies the (widened) contract wherever a ShellBridge is expected.
    def save_new_file(self, filename: str, content: str) -> str:
        raise NotImplementedError

    def delete_file(self, path: str) -> None:
        raise NotImplementedError

    def restore_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def open_draft(self, to: str, subject: str, body: str) -> str:
        raise NotImplementedError

    def discard_draft(self, draft_ref: str) -> None:
        raise NotImplementedError

    def read_clipboard(self) -> str:
        raise NotImplementedError

    def open_external(self, url: str) -> None:
        raise NotImplementedError

    def read_scoped_file(self, file_handle: str) -> dict:
        raise NotImplementedError

    def pick_directory(self) -> str:
        raise NotImplementedError


class _ScriptedProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None) -> ModelResponse:
        return self._responses.pop(0)


class _FakeStore:
    def __init__(self) -> None:
        self.inserted: list[ActionSnapshot] = []

    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        self.inserted.append(snapshot)


class _FakeRunCommand:
    """run_command's shape: HIGH, dev_only, always-destructive, no affected_path —
    records instead of touching a shell."""

    definition = ToolDefinition(
        id="run_command", label="Run a command", description="dev-only",
        risk_tier=RiskTier.HIGH, parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.ran: list[dict] = []

    def is_destructive(self, args: dict) -> bool:
        return True

    def affected_path(self, args: dict) -> str | None:
        return None

    def permission_detail(self, args: dict) -> str | None:
        return str(args.get("command", "")) or None

    def execute(self, args, context) -> ToolResult:
        self.ran.append(args)
        return ToolResult(success=True, content="ran")


# --- helpers ---------------------------------------------------------------


def _run_single_tool_call(registry, gate, bridge, trust_check, tool_id, args, mode=PolicyMode.OPEN):
    provider = _ScriptedProvider([
        ModelResponse(text=None, tool_calls=[ToolCallRequest(id="c1", tool_id=tool_id, args=args)]),
        ModelResponse(text="done", tool_calls=[]),
    ])
    store = _FakeStore()
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=bridge,
        trust_check=trust_check,
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="go"))
    orch.run_turn(conv, mode=mode)
    tool_result = next(m for m in conv.messages if m.role == "tool")
    return conv, tool_result, store


def _harness_registry(bridge):
    registry = ToolRegistry()
    registry.register(ReadProjectFileTool(), open_only=True)
    write = WriteProjectFileTool(shell_bridge=bridge)
    registry.register(write, open_only=True)
    return registry, write


# ============================================================================
# (2) + (6) — the confinement predicate is_trusted (pure)
# ============================================================================
def test_is_trusted_requires_a_root_and_passes_the_floor(tmp_path):
    root = str((tmp_path / "project").resolve())
    os.makedirs(root)
    data_dir = str((tmp_path / "data").resolve())
    os.makedirs(data_dir)

    inside = str(Path(root) / "src" / "main.py")
    assert is_trusted(inside, [root], data_dir) is True
    # Outside every root -> not trusted (the /etc/passwd shape).
    assert is_trusted("/etc/passwd", [root], data_dir) is False


def test_floor_beats_a_planted_data_dir_root(tmp_path):
    # (6) A trust row whose root IS the data dir must never confine — match-a-root
    # THEN floor, so the floor wins over a planted root.
    data_dir = str((tmp_path / "data").resolve())
    os.makedirs(os.path.join(data_dir, "snapshots"))
    target = os.path.join(data_dir, "snapshots", "restore.json")
    # Even with the data dir itself listed as a "trusted root", the floor refuses.
    assert is_trusted(target, [data_dir], data_dir) is False


# ============================================================================
# (2) — read_project_file confinement, at the orchestrator (the headline repro)
# ============================================================================
def test_read_project_file_etc_passwd_is_hard_refused(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))
    # Nothing is trusted -> /etc/passwd is refused before execute.
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, lambda p: False, "read_project_file", {"path": "/etc/passwd"},
    )
    assert tool_result.content == _OUTSIDE_TRUST
    assert bridge.reads == []   # the shell was never asked to read it


def test_read_project_file_inside_trust_runs(tmp_path):
    # Not vacuous: with the path trusted, the SAME tool DOES read.
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "project" / "notes.txt"
    target.parent.mkdir()
    target.write_text("hello", encoding="utf-8")
    resolved = str(target.resolve())
    gate = PermissionGate()
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "read_project_file", {"path": str(target)},
    )
    assert tool_result.content == "hello"
    assert bridge.reads == [resolved]


def test_symlink_inside_a_trusted_root_pointing_out_is_refused(tmp_path):
    # resolve-once (R6): the tool resolves the symlink to its real target, which is
    # OUTSIDE the root, so trust_check sees the real path and refuses.
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secrets.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    link.symlink_to(outside)

    root_real = str(root.resolve())
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))

    # trust_check trusts only paths genuinely under the root's realpath.
    def trust_check(p):
        return path_is_within(p, root_real)

    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, trust_check, "read_project_file", {"path": str(link)},
    )
    assert tool_result.content == _OUTSIDE_TRUST
    assert bridge.reads == []


# ============================================================================
# (3) — write_project_file inside/outside trust; undo round-trips
# ============================================================================
def test_write_inside_trust_is_card_free_and_undoable(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, write_tool = _harness_registry(bridge)
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    resolved = str(target.resolve())
    # No card inside trust: on_request must never fire for the destructive write.
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("trusted write must not card"))
    _, tool_result, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "after"},
    )
    assert tool_result.content.startswith("Wrote ")
    assert target.read_text(encoding="utf-8") == "after"
    assert gate.auto_grants == ["write_project_file"]   # auto-granted, logged
    # Undoable: replaying the recorded snapshot restores the prior bytes exactly.
    assert len(store.inserted) == 1
    write_tool.undo(store.inserted[0])
    assert target.read_text(encoding="utf-8") == "before"


def test_write_created_file_undo_deletes_it(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, write_tool = _harness_registry(bridge)
    target = tmp_path / "project" / "new.txt"
    target.parent.mkdir()
    resolved = str(target.resolve())
    gate = PermissionGate()
    _, _, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "created"},
    )
    assert target.read_text(encoding="utf-8") == "created"
    # existed=False -> undo removes the created file.
    write_tool.undo(store.inserted[0])
    assert not target.exists()


def test_write_outside_trust_refuses_and_writes_nothing(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "elsewhere" / "f.txt"
    target.parent.mkdir()
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))
    _, tool_result, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: False,
        "write_project_file", {"path": str(target), "content": "x"},
    )
    assert tool_result.content == _OUTSIDE_TRUST
    assert not target.exists()
    assert bridge.writes == []
    assert store.inserted == []   # nothing to undo — nothing happened


def test_write_refuses_a_binary_prior_file(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "project" / "bin.dat"
    target.parent.mkdir()
    target.write_bytes(bytes([0, 159, 146, 150]))
    resolved = str(target.resolve())
    gate = PermissionGate()
    _, tool_result, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "text"},
    )
    # A bridge refusal surfaces as the failed step's message content.
    assert "isn't a text file" in tool_result.content
    assert target.read_bytes() == bytes([0, 159, 146, 150])   # untouched
    assert store.inserted == []   # no snapshot for a write that never happened


def test_write_refuses_an_oversize_prior_file(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "project" / "big.txt"
    target.parent.mkdir()
    target.write_text("a" * (256 * 1024 + 1), encoding="utf-8")
    resolved = str(target.resolve())
    gate = PermissionGate()
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "small"},
    )
    assert "too big" in tool_result.content


# ============================================================================
# (4) — run_command inside a trusted cwd STILL cards with the command text
# ============================================================================
def test_run_command_still_cards_inside_a_trusted_workspace(tmp_path):
    bridge = _FakeWorkspaceBridge()
    registry = ToolRegistry()
    rc = _FakeRunCommand()
    registry.register(rc, dev_only=True)
    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    # EVERYTHING trusted — yet run_command has no affected_path, so it is never
    # trust-suppressed: the card fires anyway, carrying the exact command.
    _run_single_tool_call(
        registry, gate, bridge, lambda p: True,
        "run_command", {"command": "rm -rf build"},
    )
    assert asked == [("run_command", "rm -rf build")]
    assert rc.ran == [{"command": "rm -rf build"}]


# ============================================================================
# (5) — write_project_file's undo is REGISTRATION-ENFORCED despite open_only
# ============================================================================
class _MediumNoUndo:
    definition = ToolDefinition(
        id="broken_write", label="x", description="x",
        risk_tier=RiskTier.MEDIUM, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content="")


def test_write_project_file_registers_open_only_and_hidden_from_safe():
    registry = ToolRegistry()
    registry.register(WriteProjectFileTool(), open_only=True)
    assert registry.is_dev_only("write_project_file") is True
    safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    open_ids = {d.id for d in registry.visible_tools(PolicyMode.OPEN)}
    assert "write_project_file" not in safe_ids
    assert "write_project_file" in open_ids


def test_open_only_alone_does_not_exempt_the_undo_check():
    # The whole point of the flag split (R3): open_only hides from SAFE but does NOT
    # waive the undo-at-registration invariant. A MEDIUM open_only tool with no undo
    # must still RAISE — only allow_missing_undo (dev_only) waives it.
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_MediumNoUndo(), open_only=True)
    # allow_missing_undo (and its dev_only alias) is the ONLY waiver.
    ToolRegistry().register(_MediumNoUndo(), open_only=True, allow_missing_undo=True)
    ToolRegistry().register(_MediumNoUndo(), dev_only=True)


# ============================================================================
# (7) — SAFE ignores the trusted bool; the gate stays store-free
# ============================================================================
def test_safe_mode_ignores_trusted_and_runs_the_coarse_flow():
    asked: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid: (asked.append(tid), PermissionStatus.GRANTED)[1]
    )
    # trusted=True must NOT auto-grant in SAFE — SAFE runs the coarse ask/grant flow.
    status = gate.authorize("t", mode=PolicyMode.SAFE, trusted=True, destructive=True)
    assert status == PermissionStatus.GRANTED
    assert asked == ["t"]                 # it asked (safe flow), did not auto-grant
    assert gate.auto_grants == []


def test_open_mode_trusted_destructive_auto_grants():
    # The contrast that proves the SAFE test isn't vacuous: OPEN + trusted + destructive
    # auto-grants card-free (recorded), which SAFE refuses to do.
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("trusted OPEN must not card"))
    status = gate.authorize("write_project_file", mode=PolicyMode.OPEN, trusted=True, destructive=True)
    assert status == PermissionStatus.GRANTED
    assert gate.auto_grants == ["write_project_file"]


# ============================================================================
# (8) — routine step / command widget always card under trust (D5)
# ============================================================================
def test_a_routine_path_tool_step_still_cards_under_trust(tmp_path):
    # D5: a persisted, replayable spec never trust-suppresses. Even with a PATH tool
    # whose resolved path IS inside a trusted root (so confinement passes), the
    # routine engine passes trusted=False, so the destructive write STILL cards.
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    bridge = _FakeWorkspaceBridge()
    registry = ToolRegistry()
    registry.register(WriteProjectFileTool(shell_bridge=bridge), open_only=True)
    store = Store(tmp_path / "r.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    resolved = str(target.resolve())

    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=on_request),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=bridge,
        store=store,
        trust_check=lambda p: p == resolved,   # the path IS trusted
    )
    routine = Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "write_project_file", {"path": str(target), "content": "x"})],
    )
    result = engine.run(routine, {}, mode=PolicyMode.OPEN)
    assert result.status == "completed"
    # It ran (confinement passed) but it CARDED (trusted=False) — no auto-grant.
    assert asked == [("write_project_file", "f.txt")]
    assert target.read_text(encoding="utf-8") == "x"


def test_command_widget_still_cards_when_a_workspace_is_trusted(tmp_path):
    # D5 over the wire: a command widget cards even with a folder trusted (run_command
    # is never trust-suppressed). A mutation passing trusted=True would auto-grant and
    # no card would ever appear.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    db_path = data_dir / "policy.sqlite3"

    store = Store(db_path)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", "developer")
    store.insert_widget(
        id="dev-wd",
        spec_json=json.dumps({"kind": "command", "command": "true && true", "title": "Chain"}),
        pinned=False, position=0, created_at=1, created_in_mode="open",
    )
    store.insert_workspace_trust(root=str(project.resolve()), granted_at=1)
    store.close()

    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader, writer=writer,
        tool_registry=build_registry(DEVELOPER),
        store_factory=lambda: Store(db_path),
        db_path=db_path,
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider([])}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        reader.feed({"jsonrpc": "2.0", "id": 1, "method": "widget.run", "params": {"id": "dev-wd"}})
        card = writer.wait_for(lambda f: f.get("method") == "permission.requestGrant")
        assert card["params"]["toolId"] == "run_command"
        reader.feed({"jsonrpc": "2.0", "id": 100, "method": "permission.respond",
                     "params": {"toolId": "run_command", "allow": True}})
        result = writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert result["ok"] is True
    finally:
        reader.close()
        thread.join(timeout=5)


# ============================================================================
# (1) — grantTrust refuses the data dir; (9) restore never resurrects trust
# ============================================================================
def test_grant_trust_refuses_the_data_dir_and_allows_a_project(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    db_path = data_dir / "app.sqlite3"

    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader, writer=writer,
        tool_registry=build_registry(DEVELOPER),
        store_factory=lambda: Store(db_path),
        db_path=db_path,
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider([])}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        refused = _rpc(reader, writer, 1, "workspace.grantTrust", {"directory": str(data_dir)})
        assert refused["result"]["ok"] is False
        assert "Addison's own memory" in refused["result"]["error"]

        ok = _rpc(reader, writer, 2, "workspace.grantTrust", {"directory": str(project)})
        assert ok["result"]["ok"] is True
        listed = _rpc(reader, writer, 3, "workspace.list")["result"]["folders"]
        assert [f["directory"] for f in listed] == [str(project.resolve())]

        revoked = _rpc(reader, writer, 4, "workspace.revokeTrust", {"directory": str(project)})
        assert revoked["result"]["ok"] is True
        assert _rpc(reader, writer, 5, "workspace.list")["result"]["folders"] == []
    finally:
        reader.close()
        thread.join(timeout=5)


def test_workspace_trust_is_excluded_from_snapshots():
    # (9), static: workspace_trust is neither captured nor a stray table — it is a
    # DECLARED exclusion, so the capture-scope completeness test stays satisfied.
    assert "workspace_trust" in scope._EXCLUDED_TABLES
    assert "workspace_trust" not in scope._CAPTURED_TABLES


def test_restore_never_resurrects_a_revoked_trust(tmp_path):
    # (9), behavioural: trust granted -> snapshot -> trust revoked -> restore. The
    # restore must NOT bring the trust back (standing consent, D2). Because the table
    # is excluded, a restore leaves it byte-for-byte as it is now (revoked).
    store = Store(tmp_path / "addison.sqlite3")
    project = tmp_path / "project"
    project.mkdir()
    store.insert_workspace_trust(root=str(project.resolve()), granted_at=1)

    manager = SnapshotManager(
        store=store, snapshot_dir=tmp_path / "snapshots", created_the_database=True,
    )
    snap = manager.capture(trigger="on_command", reason="user_request")

    store.delete_workspace_trust(str(project.resolve()))
    assert store.list_workspace_trust() == []   # revoked

    manager.restore(snap.id)
    # The revoked trust is NOT resurrected by the restore.
    assert store.list_workspace_trust() == []
    store.close()


# --- minimal IPC harness (mirrors tests/test_policy_modes.py) ---------------


class _PipeReader:
    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def feed(self, frame: dict) -> None:
        self._lines.put(json.dumps(frame) + "\n")

    def close(self) -> None:
        self._lines.put("")

    def readline(self) -> str:
        return self._lines.get()


class _FrameWriter:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._cond = threading.Condition()

    def write(self, line: str) -> None:
        frame = json.loads(line)
        with self._cond:
            self.frames.append(frame)
            self._cond.notify_all()

    def flush(self) -> None:
        pass

    def wait_for(self, predicate, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for frame in self.frames:
                    if predicate(frame):
                        return frame
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"expected frame never arrived; got {self.frames}"
                self._cond.wait(remaining)


def _rpc(reader, writer, rid, method, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    reader.feed(frame)
    return writer.wait_for(lambda f: f.get("id") == rid and ("result" in f or "error" in f))
