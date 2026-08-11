"""Workspace trust + the OPEN-mode coding harness — step 5.

Covers the contract's ten verification items, each written to fail when the line it
guards is reverted (mutation-proven; see docs/HANDOFF.md "How step 1 was verified"):

  (1) the data-dir floor (also in test_ipc_snapshots.py) + grantTrust refusing ~;
  (2) read_project_file{path:"/etc/passwd"} hard-refused (confinement) + a symlink
      inside a trusted root pointing out is refused (resolve-once);
  (3) write_project_file inside trust (OPEN): no card, undoable (round-trip /
      created-file delete / binary refused / oversize refused); outside trust:
      refused, no write;
  (4) run_command inside a trusted cwd STILL cards with the command text;
  (5) write_project_file is IN the SAFE view and its undo is REGISTRATION-ENFORCED
      there (2026-08-11: it was open_only, and the SAFE view is where this line
      changed — see item 11);
  (6) a planted trust row for the data dir never confines (floor beats root);
  (7) SAFE ignores a supplied trusted bool, and is otherwise untouched except for
      the destructive per-invocation card item 11 brought with it;
  (8) a routine step / command widget always cards under trust;
  (9) restore never resurrects a revoked trust (excluded from snapshots).
Item (10) — read_web_page's SSRF suite — is unchanged here; no net-vet code moved.

  (11) THE SIMPLE PROFILE EDITS AN EXISTING FILE (owner decision 2026-08-11, the
       fix for the "Simple can only save a new file" defect): in SAFE the write is
       not refused, it raises one card naming the file, the edit lands only after
       the answer, a denial writes nothing, and confinement still refuses a path
       outside every trusted root before the gate is consulted at all.
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
    OS_AUTOMATION_DIRS,
    TRUST_REFUSAL_AUTOMATION,
    TRUST_REFUSAL_PROTECTED,
    GuardConfig,
    PolicyMode,
    path_is_within,
    trust_refusal,
    workspace_trust_allows,
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
    UNRESOLVABLE_PATH,
    ActionSnapshot,
    RiskTier,
    ToolDefinition,
    ToolResult,
    call_affected_path,
    call_permission_detail,
)
from agent_core.tools.read_project_file import ReadProjectFileTool
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.write_project_file import WriteProjectFileTool
from tests.conftest import ShellBridgeStubs


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

    def arm_automation(
        self, label: str, command: str, schedule_kind: str, schedule: dict
    ) -> dict:
        raise NotImplementedError

    def disarm_automation(self, label: str) -> dict:
        raise NotImplementedError

    # Step-5.5: execution crosses this Protocol now, so the fake carries it. The
    # command tests live in test_step_5_5_containment.py / test_run_command.py.
    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        raise NotImplementedError


class _ScriptedProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        return self._responses.pop(0)


class _FakeStore:
    def __init__(self) -> None:
        self.inserted: list[ActionSnapshot] = []

    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:
        self.inserted.append(snapshot)


class _FakeExecBridge(ShellBridgeStubs):
    """The shell's half of ``shell.runCommand`` (step 5.5, item 1). Everything
    else comes from ``ShellBridgeStubs`` and raises — this fake answers one
    method, and inheriting the rest is what makes it a real bridge to pyright
    rather than a subset that happened to be enough."""

    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        return {"stdout": "ok", "stderr": "", "exitCode": 0, "sandboxed": True}


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


def _run_single_tool_call(
    registry, gate, bridge, trust_check, tool_id, args, mode=PolicyMode.OPEN, on_activity=None
):
    provider = _ScriptedProvider([
        ModelResponse(text=None, tool_calls=[ToolCallRequest(id="c1", tool_id=tool_id, args=args)]),
        ModelResponse(text="done", tool_calls=[]),
    ])
    store = _FakeStore()
    # ``on_activity`` is optional and defaulted here rather than always passed: it is
    # the string that LEAVES the core for the webview (tool.activityUpdate), so a test
    # that cares what the person is told wires it, and every other test keeps the
    # orchestrator's own default.
    extra = {} if on_activity is None else {"on_activity": on_activity}
    orch = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=bridge,
        trust_check=trust_check,
        **extra,
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="go"))
    orch.run_turn(conv, mode=mode)
    tool_result = next(m for m in conv.messages if m.role == "tool")
    return conv, tool_result, store


def _run_routine_step(tmp_path, registry, gate, bridge, trust_check, tool_id, args):
    """One routine step through the REAL engine — the negative twin of the live
    loop's confinement check. Mirrors the positive routine test's setup."""
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    store = Store(tmp_path / "routine.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=store, tool_registry=registry),
        shell_bridge=bridge,
        store=store,
        trust_check=trust_check,
    )
    routine = Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", tool_id, args)],
    )
    return engine.run(routine, {}, mode=PolicyMode.OPEN)


def _harness_registry(bridge):
    """The two file tools registered exactly as ``main.build_registry`` registers
    them — NO FLAGS since 2026-08-11, so they are in the SAFE view and Simple can
    reach them behind a card (docs/SAFETY.md owns the decision). Registering them
    ``open_only`` here would make every test below assert against a registry the app
    does not build."""
    registry = ToolRegistry()
    registry.register(ReadProjectFileTool())
    write = WriteProjectFileTool(shell_bridge=bridge)
    registry.register(write)
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
# (5) — write_project_file is IN THE SAFE VIEW, and undo-ENFORCED there
# ============================================================================
class _MediumNoUndo:
    definition = ToolDefinition(
        id="broken_write", label="x", description="x",
        risk_tier=RiskTier.MEDIUM, parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args, context) -> ToolResult:
        return ToolResult(success=True, content="")


def test_write_project_file_is_in_the_safe_view_and_stays_undo_enforced():
    """THE 2026-08-11 FLIP, pinned as the app actually builds it.

    Until that day this test asserted the opposite — ``open_only``, absent from
    ``visible_tools(SAFE)`` — and the consequence was the defect the owner ruled
    on: the Simple profile could not change an existing file at all and could only
    offer to save a new one. What made the flip legal is the second half here, and
    it is the half that must never be quietly dropped: this tool is MEDIUM with a
    REAL ``undo()``, so SAFE invariant 2 applies to it in full. It never took the
    ``allow_missing_undo`` waiver, which is exactly why it may sit in this view —
    and a future edit removing ``undo()`` must fail registration rather than
    leaving an un-undoable MEDIUM tool in front of Mira and Petr.

    Read with ``test_open_only_alone_does_not_exempt_the_undo_check`` below: that
    one still pins the flag split itself, which is live for
    ``create_automation``/``arm_automation``.

    Mutations: (a) put ``open_only=True`` back on either registration in
    ``main.build_registry`` — the SAFE assertions fail; (b) delete
    ``WriteProjectFileTool.undo`` — the registration at the top raises instead."""
    bridge = _FakeWorkspaceBridge()
    # The app's own wiring, not a hand-rolled one: the point is what SIMPLE gets.
    registry = build_registry(shell_bridge=bridge)
    safe_ids = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    open_ids = {d.id for d in registry.visible_tools(PolicyMode.OPEN)}
    for tool_id in ("write_project_file", "read_project_file"):
        assert tool_id in safe_ids, f"{tool_id} must be reachable from Simple"
        assert tool_id in open_ids
        assert registry.is_dev_only(tool_id) is False
    write = registry.get("write_project_file")
    assert write.definition.risk_tier is RiskTier.MEDIUM
    assert registry.get("read_project_file").definition.risk_tier is RiskTier.LOW
    # Undo-ENFORCED in that view: the check that would have raised had the method
    # been missing is the same one every SAFE tool passes (invariant 2).
    assert callable(getattr(type(write), "undo", None))
    with pytest.raises(ValueError, match="no undo"):
        ToolRegistry().register(_MediumNoUndo())
    # Still path-bounded, so confinement governs it in BOTH modes — the card is not
    # what keeps it inside a trusted folder, and the flip did not move that line.
    assert call_affected_path(write, {"path": "~/nowhere/f.txt"}) is not None


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
def test_safe_mode_ignores_trusted_and_cards_every_destructive_call():
    """SAFE ignores ``trusted`` (F7) — asserted here for a DESTRUCTIVE call, which
    is the only kind trust could ever have suppressed.

    Since 2026-08-11 that call takes the per-invocation card rather than the coarse
    ask-once flow, so the second assertion is the one that changed: a SAFE
    destructive call asks EVERY time and remembers no grant. That is what makes
    "Simple can edit a file" mean "Simple is asked about each file" rather than
    "Simple is asked once, then Addison edits whatever it likes".

    Mutation: route SAFE+destructive back into ``_safe_flow`` — the second ask
    disappears and this fails."""
    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    # trusted=True must NOT auto-grant in SAFE — it cards, and it names the file.
    status = gate.authorize(
        "t", mode=PolicyMode.SAFE, trusted=True, destructive=True, detail="f.txt"
    )
    assert status == PermissionStatus.GRANTED
    assert gate.auto_grants == []                 # it asked; nothing was auto-granted
    # A SECOND destructive call asks again — no coarse grant was kept.
    gate.authorize("t", mode=PolicyMode.SAFE, trusted=True, destructive=True, detail="g.txt")
    assert asked == [("t", "f.txt"), ("t", "g.txt")]


def test_safe_mode_non_destructive_calls_still_run_the_coarse_flow():
    """The precision half of the change above: the SAFE gate is otherwise
    untouched. A non-destructive tool asks ONCE and the grant is remembered — the
    historical behaviour every other Simple tool depends on, and the freeze the
    2026-08-11 tightening was written to keep."""
    asked: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid: (asked.append(tid), PermissionStatus.GRANTED)[1]
    )
    for _ in range(2):
        assert gate.authorize("t", mode=PolicyMode.SAFE) == PermissionStatus.GRANTED
    assert asked == ["t"]                 # asked once, then remembered
    assert gate.auto_grants == []


def test_open_mode_trusted_destructive_auto_grants():
    # The contrast that proves the SAFE test isn't vacuous: OPEN + trusted + destructive
    # auto-grants card-free (recorded), which SAFE refuses to do.
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("trusted OPEN must not card"))
    status = gate.authorize("write_project_file", mode=PolicyMode.OPEN, trusted=True, destructive=True)
    assert status == PermissionStatus.GRANTED
    assert gate.auto_grants == ["write_project_file"]


# ============================================================================
# (11) — SIMPLE EDITS A FILE, behind a card that names it (owner decision
#        2026-08-11). Through the real orchestrator in SAFE mode, because the
#        claim is about the whole path — visibility, confinement, gate, effect —
#        and every one of those is a separate place the old answer lived.
# ============================================================================
def test_simple_edits_an_existing_file_behind_a_card_that_names_it(tmp_path):
    """THE FIX, end to end. In SAFE mode the write is not refused for being a
    developer affordance; it raises ONE card carrying the file's name, and the
    edit lands only after the answer.

    Three things are asserted together because the bug could come back through any
    of them: the call is not refused (visibility), a card was raised BEFORE the
    write (order — ``bridge.writes`` is empty when the handler runs), and the
    change is undoable (a snapshot was recorded, which is what makes the card an
    honest one).

    Mutations: (a) register the tool ``open_only`` again — the tool result is the
    dev-only refusal; (b) hand SAFE's destructive call to ``_safe_flow`` — the
    detail is lost and the file name assertion fails."""
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    resolved = str(target.resolve())

    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        # The card is shown BEFORE anything is written — that ordering IS the
        # decision ("show the permission card first and then do the edit").
        assert bridge.writes == []
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    _, tool_result, store = _run_single_tool_call(
        registry, PermissionGate(on_request=on_request), bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "after"},
        mode=PolicyMode.SAFE,
    )
    assert asked == [("write_project_file", "f.txt")]
    assert tool_result.content == "Wrote f.txt."
    assert target.read_text(encoding="utf-8") == "after"
    # Undoable: the snapshot carrying the prior bytes was recorded in SAFE too.
    assert store.inserted and store.inserted[0].undo_payload["prior"] == "before"


def test_a_denied_card_in_simple_writes_nothing(tmp_path):
    """"Not now" means the file is untouched — the other half of the card being
    real. A gate answer that arrived after the write would make the card a
    notification."""
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
    resolved = str(target.resolve())

    gate = PermissionGate(on_request=lambda tool_id, detail=None: PermissionStatus.DENIED)
    _, tool_result, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": str(target), "content": "after"},
        mode=PolicyMode.SAFE,
    )
    assert "declined" in tool_result.content
    assert bridge.writes == []
    assert target.read_text(encoding="utf-8") == "before"
    assert store.inserted == []


def test_simple_is_still_confined_to_trusted_folders(tmp_path):
    """Confinement did not move with the visibility. In SAFE, a path outside every
    trusted root is hard-refused BEFORE the gate — so there is no card to approve
    it with, which is the whole difference between confinement and consent."""
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("before", encoding="utf-8")
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, lambda p: False,
        "write_project_file", {"path": str(outside), "content": "after"},
        mode=PolicyMode.SAFE,
    )
    assert tool_result.content == _OUTSIDE_TRUST
    assert bridge.writes == []
    assert outside.read_text(encoding="utf-8") == "before"


def test_the_simple_card_says_which_file_and_never_reads_as_a_command():
    """The sentence Mira and Petr read. It names the file and says the change can
    be undone — and it must NOT contain ``run: ``, which the frontend splits on to
    render what follows as a command (``PermissionCard.tsx``): a card announcing
    "wants to run: shopping.txt" is a lie about what is about to happen, and the
    reason the wording belongs to the tool rather than to the server's one idiom.

    ``run_command``'s card is asserted beside it, because the change had to leave
    that idiom exactly where it was."""
    from agent_core.main import _card_consequence

    write = WriteProjectFileTool()
    sentence = _card_consequence(write, "shopping.txt")
    assert "shopping.txt" in sentence
    assert "undo" in sentence
    assert "run: " not in sentence
    # No detail (a SAFE coarse card) still falls back to the standing description.
    assert _card_consequence(write, None) == write.definition.description
    # The historical idiom, untouched, for the tool it was written for.
    assert _card_consequence(_FakeRunCommand(), "rm -rf /tmp/x") == (
        "This time it wants to run: rm -rf /tmp/x"
    )


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


def test_a_file_routine_now_runs_in_simple_with_the_ordinary_card(tmp_path):
    """THE INTENDED CONSEQUENCE of the 2026-08-11 flip, on the routine path.

    Availability is asked of the ARTIFACT — the plan AND the SAFE tool view
    (``rpc/routines.py::_routine_needs_dev``) — so a routine whose only step edits a
    file stopped "waiting in Developer profile" the moment that tool entered the SAFE
    view, and RUNS in Simple instead. It runs on exactly the live terms: confinement
    first, then a card per step (a stored spec never passes ``trusted``, D5), which is
    invariant 3 — a routine gets nothing the person could not have granted live.

    Mutation: register the tool ``open_only`` again — the engine's per-step dev-only
    check refuses the step and the file is untouched."""
    from agent_core.routines.engine import RoutineEngine
    from agent_core.routines.model import Routine, RoutineStep

    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    store = Store(tmp_path / "r.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    target.write_text("before", encoding="utf-8")
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
        trust_check=lambda p: p == resolved,
    )
    routine = Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "write_project_file", {"path": str(target), "content": "x"})],
    )
    result = engine.run(routine, {}, mode=PolicyMode.SAFE)
    assert result.status == "completed"
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
        # run_command no longer executes in the core (step 5.5, item 1): it crosses
        # the bridge so the command lands where a sandbox can be applied. This test
        # is about the CARD — a command widget must still ask even under trust — so
        # the bridge only has to answer.
        shell_bridge=_FakeExecBridge(),
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


# ============================================================================
# (10) — the gaps the post-build adversarial pass found. Every mutation named in a
# docstring below SURVIVED the entire 847-test suite before these tests existed.
# ============================================================================
def test_the_tool_acts_on_the_resolved_path_not_a_second_reading_of_the_argument(tmp_path):
    """The whole R6/D4 resolve-once mechanism was unwatched, and it was structural:
    pytest's ``tmp_path`` is already fully realpath'd, so in every other test the raw
    argument and the resolved path are byte-identical and ``affected_path``'s
    ``.resolve()``, ``ExecutionContext.resolved_path`` and the ``_NO_RESOLVED_PATH``
    fail-closed branch could all be deleted with nothing noticing.

    So this test hands the tool a path the caller must NORMALISE — reached through a
    symlinked alias — and asserts the shell was asked for the REAL path. A tool that
    re-read ``args["path"]`` would ask for the alias instead, which is the TOCTOU gap:
    confinement approves one path, the effect lands on another.
    """
    real_root = tmp_path / "project"
    real_root.mkdir()
    target = real_root / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_root)
    aliased_arg = str(alias / "notes.txt")
    resolved = str(target.resolve())
    assert aliased_arg != resolved, "the alias must not already equal the real path"

    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate()
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "read_project_file", {"path": aliased_arg},
    )
    assert tool_result.content == "hello"
    assert bridge.reads == [resolved], "the read must use the path confinement checked"


def test_the_write_acts_on_the_resolved_path_not_a_second_reading_of_the_argument(tmp_path):
    """The write half of the same gap — and the one that matters more, because here
    the mismatch would be a WRITE landing somewhere confinement never approved."""
    real_root = tmp_path / "project"
    real_root.mkdir()
    target = real_root / "out.txt"
    target.write_text("before", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_root)
    aliased_arg = str(alias / "out.txt")
    resolved = str(target.resolve())
    assert aliased_arg != resolved

    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate()
    _, _tool_result, store = _run_single_tool_call(
        registry, gate, bridge, lambda p: p == resolved,
        "write_project_file", {"path": aliased_arg, "content": "after"},
    )
    assert bridge.writes == [resolved], "the write must land on the confined path"
    assert target.read_text(encoding="utf-8") == "after"
    # ...and the undo restores THAT path, so undo cannot target a different file
    # than the one that was written.
    assert store.inserted and store.inserted[-1].undo_payload["path"] == resolved


def test_a_path_the_os_cannot_resolve_is_refused_not_crashed(tmp_path):
    """``Path(raw).resolve()`` raises ValueError on an embedded NUL, and the
    confinement call sites sit OUTSIDE the per-call error handling that exists so
    "a tool failure is a failed STEP, never a crashed turn". One model-authored
    tool call therefore took the whole turn down — and on the routine path left the
    run recorded as ``running`` forever.

    Refused, and refused as OUTSIDE TRUST rather than skipped: an unresolvable path
    must not collapse onto ``None``, which means "not a path tool" and bypasses
    confinement altogether.

    ``~addison_no_such_user_42/x.txt`` is the fourth shape and it was added on
    2026-08-08: ``Path.expanduser()`` raises **RuntimeError** — which the guard's
    ``(OSError, ValueError, TypeError)`` did not name — for a ``~someone`` the OS
    cannot look up, and ``~`` is what a path argument is likeliest to contain. It
    crashed the turn exactly as the NUL did before it. (``~nobody`` resolves fine on
    macOS, so this really is the unknown-user case and not "tilde is refused".)

    Mutation: drop RuntimeError from ``call_affected_path``'s except tuple."""
    root = tmp_path / "project"
    root.mkdir()
    root_real = str(root.resolve())
    data_dir = str((tmp_path / "data").resolve())
    os.makedirs(data_dir)

    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))
    # The REAL predicate, with a real trusted root — a trust_check that says yes to
    # everything would be the very assumption under test.
    for bad in ["/tmp/a\x00b", "~addison_no_such_user_42/x.txt", "", None, 42, {"not": "a path"}]:
        _, tool_result, _ = _run_single_tool_call(
            registry, gate, bridge,
            lambda p: is_trusted(p, [root_real], data_dir),
            "read_project_file", {"path": bad},
        )
        # The turn COMPLETED (no crash) and the step was refused by confinement.
        assert tool_result.content == _OUTSIDE_TRUST, bad
    assert bridge.reads == []


def test_a_write_outside_trust_in_a_routine_step_is_refused_and_writes_nothing(tmp_path):
    """The routine engine's confinement had only a POSITIVE test (a step that ran and
    carded); deleting the hard-refusal entirely left the whole suite green. This is
    the negative twin of the live loop's ``test_write_outside_trust_refuses_and_writes_nothing``.
    """
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate(on_request=lambda *a, **k: pytest.fail("must never reach the gate"))
    outside = str(tmp_path / "not-trusted" / "x.txt")

    result = _run_routine_step(
        tmp_path, registry, gate, bridge, lambda p: False,
        tool_id="write_project_file", args={"path": outside, "content": "nope"},
    )
    # The step was refused by confinement, the routine did not crash, and the run
    # is recorded with a real terminal status (never left at 'running').
    assert result.status in ("completed", "failed"), result.status
    step = result.step_results["s1"]
    assert step.success is False
    assert step.content == _OUTSIDE_TRUST
    assert bridge.writes == []
    assert not Path(outside).exists()


def test_a_turn_scoped_not_now_is_honoured_even_inside_a_trusted_folder(tmp_path):
    """The don't-nag rule cuts both ways. ``_auto_grant`` never consulted
    ``_denied``, so a person who was shown a card and pressed "Not now" could watch
    Addison edit a file in the same turn anyway. Nothing is escalated — the call was
    card-free regardless — what was broken is consent HONESTY, and that is the
    property this pins."""
    gate = PermissionGate(on_request=lambda *a, **k: PermissionStatus.DENIED)
    # The first call cards and is refused (a malformed path never reaches trust).
    first = gate.authorize(
        "write_project_file", mode=PolicyMode.OPEN, destructive=True, trusted=False
    )
    assert first == PermissionStatus.DENIED
    # The second is genuinely inside trust — and must still be refused this turn.
    second = gate.authorize(
        "write_project_file", mode=PolicyMode.OPEN, destructive=True, trusted=True
    )
    assert second == PermissionStatus.DENIED
    assert gate.auto_grants == []
    # ...and the denial is turn-scoped, so clearing it restores the card-free path.
    gate.clear_denials()
    assert (
        gate.authorize("write_project_file", mode=PolicyMode.OPEN, destructive=True, trusted=True)
        == PermissionStatus.GRANTED
    )


# ============================================================================
# THE NAME A PERSON READS IS THE FILE THAT WAS TOUCHED (2026-08-08)
# ============================================================================
# Review-surface prerequisite 1 (docs/phase-3-review-surface-plan.md,
# "Prerequisites"). ``permission_detail`` read the RAW argument while
# ``affected_path`` resolved, so the two named different files whenever a symlink
# sat between them. Confinement cannot catch it — the link and its target are both
# inside the trusted root, which is the ordinary case, not an exotic one — so the
# displayed name was the only thing standing there, and it named the decoy. The
# review surface makes the disagreement visible (it renders the RESOLVED path), but
# the defect is here, in what the person was told at the moment of the edit.
#
# **These now ask through ``call_permission_detail``, and that is the point rather than
# a change of spelling.** The 2026-08-08 fix left a SECOND realpath — each tool asked
# ``call_affected_path`` itself — so a symlink swapped between the label's resolution
# and the boundary's could still put a stale name on the card. Closing that window
# (KNOWN-GAPS, closed with the review surface's read paths) moved the single resolution
# to the CALLER, which hands it in; the tools implement ``permission_detail_for_path``
# and have no argument left to resolve a second time. Every property these two tests
# pinned is unchanged and still pinned — they ask at the door the app uses instead of
# reaching past it.


def test_the_displayed_name_is_the_symlinks_target_not_the_link(tmp_path):
    """A symlink INSIDE a trusted root pointing at another file INSIDE it — the case
    ``test_symlink_inside_a_trusted_root_pointing_out_is_refused`` deliberately does
    not cover, because nothing is refused here: the call is legitimate, it runs, and
    the only question is whether the person was told the truth about it.

    All three surfaces are asserted, because they are three separate strings and the
    bug was that two of them disagreed: ``permission_detail`` (the card + the audit
    row), the Activity Panel string that actually leaves the core for the webview,
    and the "Wrote …" sentence the model repeats back in chat.

    Mutation: restore ``Path(args["path"]).name`` in either tool — every assertion
    below then reads ``notes.txt``, the name of a file Addison did not touch."""
    root = tmp_path / "project"
    root.mkdir()
    secret = root / "secrets.env"
    secret.write_text("KEY=1", encoding="utf-8")
    link = root / "notes.txt"
    link.symlink_to(secret)
    root_real = str(root.resolve())
    resolved = str(secret.resolve())

    read_tool = ReadProjectFileTool()
    write_tool = WriteProjectFileTool()
    for tool in (read_tool, write_tool):
        # No resolved path supplied — the caller-less shape, which resolves exactly
        # once here. The live loop supplies its own, asserted end to end below.
        detail = call_permission_detail(tool, {"path": str(link)})
        assert detail is not None
        assert detail == "secrets.env", tool.definition.id
        assert detail != "notes.txt", tool.definition.id
        # Still the NAME only — a full path leaving for the webview can carry the
        # person's account name, and resolving is exactly what turns a bare argument
        # into a full path.
        assert "/" not in detail

    # End to end, on the write half: the panel names the resolved file, and the write
    # lands on that same file. One assertion pair, because the whole point is that
    # these two can no longer be different files.
    announced: list[tuple[str, str | None]] = []
    bridge = _FakeWorkspaceBridge()
    registry, _ = _harness_registry(bridge)
    gate = PermissionGate()
    _, tool_result, _ = _run_single_tool_call(
        registry, gate, bridge,
        lambda p: path_is_within(p, root_real),
        "write_project_file", {"path": str(link), "content": "KEY=2"},
        on_activity=lambda tool_id, label, detail=None: announced.append((tool_id, detail)),
    )
    assert announced == [("write_project_file", "secrets.env")]
    assert bridge.writes == [resolved]
    assert secret.read_text(encoding="utf-8") == "KEY=2"
    assert tool_result.content == "Wrote secrets.env."


def test_a_name_that_cannot_be_resolved_is_no_name_rather_than_a_wrong_one(tmp_path):
    """Resolving to display it means the display path can now fail, and it is reached
    on REFUSAL branches that sit outside the orchestrator's per-call error handling
    (the ``confined_out`` audit row calls ``permission_detail``) — so raising here
    would end the turn over a string nobody needed.

    ``None`` is the answer, never the sentinel: ``UNRESOLVABLE_PATH`` is a NUL-bearing
    string, and it would be rendered in the Activity Panel and written into
    ``tool_audit`` as if it were a file name. The surface degrades to the tool's own
    label, which is honest — Addison genuinely does not know which file that is, and
    confinement is about to refuse the call for the same reason.

    Mutation: resolve without the guard (raises on the first two), or return the
    sentinel/raw basename instead of None."""
    for tool in (ReadProjectFileTool(), WriteProjectFileTool()):
        for bad in [
            "/tmp/a\x00b",                      # ValueError out of resolve()
            "~addison_no_such_user_42/x.txt",   # RuntimeError out of expanduser()
            "", None, 42, {"not": "a path"},
        ]:
            detail = call_permission_detail(tool, {"path": bad})
            assert detail is None, (tool.definition.id, bad)
            # And for a CALLER that resolved first and got nothing usable: the
            # sentinel is exactly what confinement is handed for an unreadable
            # argument, and it must never reach a screen or an audit row as a name.
            assert call_permission_detail(tool, {"path": bad}, UNRESOLVABLE_PATH) is None

    # Not vacuous: the same call with a usable path still produces a name.
    target = tmp_path / "project" / "f.txt"
    target.parent.mkdir()
    assert call_permission_detail(ReadProjectFileTool(), {"path": str(target)}) == "f.txt"


# ============================================================================
# STEP 8 PHASE 1 — the OS-automation fence at the trust boundary
# ============================================================================
# The floor grew a second group on 2026-08-07 (step-8 plan §5.5). Until then
# ``workspace_trust_allows`` refused only Addison's own directories, so
# ``~/Library/LaunchAgents`` could be granted through the OS picker and
# ``write_project_file`` could put a plist in it behind an ordinary card — a
# login-time job, armed, with no keyword gate anywhere near it. Nothing about that
# path went through Addison's automation machinery, which is why the standing claim
# "nothing in the tree can arm automation" was true of the machinery and false of
# the tree.
#
# Every assertion below passes an explicit ``data_dir``: the autouse conftest
# fixture points ADDISON_DB_PATH inside ``tmp_path``, so a defaulted data dir would
# make every tmp path fail the FIRST group and the automation group would go
# untested while the test still passed.


def _dd(tmp_path) -> str:
    """A data dir that is not an ancestor of the paths under test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return str(data_dir)


def test_an_os_automation_directory_can_never_be_trusted(tmp_path):
    data_dir = _dd(tmp_path)
    for entry in OS_AUTOMATION_DIRS:
        expanded = os.path.expanduser(entry)
        assert workspace_trust_allows(expanded, data_dir) is False, entry
        # ...and a descendant of one, which is the shape that actually arms
        # something: the plist itself, not the folder.
        assert workspace_trust_allows(os.path.join(expanded, "job.plist"), data_dir) is False


def test_a_folder_that_holds_an_automation_directory_cannot_be_trusted(tmp_path):
    """The CONTAINS direction, and the cost the plan told us to write down:
    ``~/Library`` and ``~/.config`` are no longer trustable, because trusting a
    parent trusts everything under it. Same both-directions rule the data dir
    already imposes on ``~``."""
    data_dir = _dd(tmp_path)
    for holder in ("~/Library", "~/.config", "~", "/etc", "/Library", "/var/spool"):
        assert workspace_trust_allows(os.path.expanduser(holder), data_dir) is False, holder


def test_the_automation_fence_does_not_over_refuse(tmp_path):
    """The precision half — the one that decides whether this guard is still here in
    a month. ``~/Library/Preferences`` neither holds nor sits inside an automation
    directory, so it is still trustable; a fence that refused every neighbour would
    be switched off rather than fixed (tests/gate_precision.py owns this
    convention)."""
    data_dir = _dd(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    for allowed in (
        os.path.expanduser("~/Library/Preferences"),
        os.path.expanduser("~/Library/Application Support"),
        os.path.expanduser("~/.config/git"),
        os.path.expanduser("~/projects"),
        str(project),
    ):
        assert workspace_trust_allows(allowed, data_dir) is True, allowed


def test_a_file_root_is_handled_in_both_directions(tmp_path):
    """``/etc/crontab`` is a FILE on a list of directories, and the containment
    comparison (``commonpath``) does not care which is which — so it is INSIDE-equal
    to itself and CONTAINED BY ``/etc``.

    The equality case is the one that isolates this entry: ``/etc`` is refused by
    ``/etc/cron.d`` too, but nothing except the ``/etc/crontab`` row refuses
    ``/etc/crontab`` itself. And a sibling file is untouched, which is what proves
    the file root did not poison the directory it lives in."""
    data_dir = _dd(tmp_path)
    assert workspace_trust_allows("/etc/crontab", data_dir) is False
    assert workspace_trust_allows("/etc", data_dir) is False
    assert workspace_trust_allows("/etc/hosts", data_dir) is True


def test_the_strictest_custom_guard_is_not_overridden_by_workspace_trust(tmp_path):
    """``auto_grant_scope='none'`` is the strictest option the Custom panel offers and
    its copy says Addison asks about everything. Trust silently making destructive
    writes card-free under it is the same defect shape the step-2 rigor pass found —
    the strictest-LABELLED option carrying the quiet hole, with a tightening minting
    no anchor, so nothing marks the moment.

    Simple and Developer are untouched: their guards are the defaults, where trust
    behaves exactly as step 5 built it (asserted in the second half)."""
    asked: list[str] = []
    strict = GuardConfig(auto_grant_scope="none")
    gate = PermissionGate(
        on_request=lambda tool_id, detail=None: (asked.append(tool_id) or PermissionStatus.GRANTED)
    )
    status = gate.authorize(
        "write_project_file", mode=PolicyMode.OPEN, destructive=True,
        detail="out.txt", trusted=True, guards=strict,
    )
    assert status == PermissionStatus.GRANTED
    assert asked == ["write_project_file"], "the strictest guard must still ask"
    assert gate.auto_grants == []

    # The freeze: with the DEFAULT guards (Simple/Developer), trust suppresses.
    plain = PermissionGate(on_request=lambda *a, **k: pytest.fail("must not card"))
    assert (
        plain.authorize("write_project_file", mode=PolicyMode.OPEN, destructive=True, trusted=True)
        == PermissionStatus.GRANTED
    )
    assert plain.auto_grants == ["write_project_file"]


# ============================================================================
# The refusal names its true reason (coordinator, after the fence landed).
# ============================================================================
# One sentence covered every floor failure, and the fence made it false for the
# new group: picking ~/Library/LaunchAgents told the person the folder "holds
# Addison's own memory". ``policy.trust_refusal`` is the same single loop
# ``workspace_trust_allows`` runs — the bool is just ``is None`` — with the group
# reported so the grant RPC can answer with the sentence that is actually true.


def test_trust_refusal_names_the_group_that_refused(tmp_path):
    data_dir = _dd(tmp_path)
    assert trust_refusal(data_dir, data_dir) == TRUST_REFUSAL_PROTECTED
    for entry in OS_AUTOMATION_DIRS:
        assert trust_refusal(os.path.expanduser(entry), data_dir) == TRUST_REFUSAL_AUTOMATION, (
            entry
        )
    project = tmp_path / "project"
    project.mkdir()
    assert trust_refusal(str(project), data_dir) is None


def test_a_path_that_offends_both_groups_keeps_the_memory_sentence(tmp_path):
    """``~`` contains ``~/.addison`` AND ``~/Library/LaunchAgents``. Protected wins,
    deliberately: the memory sentence was already the answer for every such path
    before the fence existed, and a refusal that changes wording between builds
    reads like a change of policy. (data_dir=None exercises the default-derivation
    path, whose protected group includes ``~/.addison``.)"""
    assert trust_refusal(os.path.expanduser("~"), None) == TRUST_REFUSAL_PROTECTED


def test_workspace_trust_allows_is_exactly_trust_refusal_is_none(tmp_path):
    """The bool and the reason may never disagree — the bool IS the reason's
    ``is None``, and this pins that a future edit to one loop cannot quietly leave
    the other answering differently."""
    data_dir = _dd(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    probes = [data_dir, str(project), os.path.expanduser("~")]
    probes.extend(os.path.expanduser(entry) for entry in OS_AUTOMATION_DIRS)
    for probe in probes:
        assert workspace_trust_allows(probe, data_dir) is (
            trust_refusal(probe, data_dir) is None
        ), probe


def test_grant_trust_answers_the_automation_sentence_for_an_automation_dir(tmp_path):
    """The wire half: workspace.grantTrust on an OS-automation directory answers the
    fence's own sentence, not the data-dir one. The RPC checks ``os.path.isdir``
    before the floor, so the probe must be a directory that EXISTS — picked from the
    list per platform, and skipped honestly where none does (the group-selection
    logic above runs everywhere regardless)."""
    existing = next(
        (
            os.path.expanduser(entry)
            for entry in OS_AUTOMATION_DIRS
            if os.path.isdir(os.path.expanduser(entry))
        ),
        None,
    )
    if existing is None:
        pytest.skip("no OS-automation directory exists on this machine")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.sqlite3"
    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=build_registry(DEVELOPER),
        store_factory=lambda: Store(db_path),
        db_path=db_path,
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider([])}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        refused = _rpc(reader, writer, 1, "workspace.grantTrust", {"directory": existing})
        assert refused["result"]["ok"] is False
        assert "jobs it runs on a schedule" in refused["result"]["error"]
        assert "Addison's own memory" not in refused["result"]["error"]
        # And nothing was stored: the refusal really refused.
        assert _rpc(reader, writer, 2, "workspace.list")["result"]["folders"] == []
    finally:
        reader.close()
        thread.join(timeout=5)
