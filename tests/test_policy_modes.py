"""Mode-scoped safety — the SAFE / OPEN split (owner decision 2026-07-19).

The profile derives the mode (policy.py). SAFE (Simple) is byte-for-byte the
historical safety model; OPEN (Developer) is "nearly completely open": real
command execution, a gate that prompts only for destructive actions, and
routines/widgets that may carry command steps. Dev-created artifacts are hidden
and refused in SAFE mode and return untouched in OPEN mode. This file pins:

  * mode_for_profile derivation;
  * PermissionGate.authorize under both modes (auto-allow vs. prompt, logging);
  * routine command steps (dev-only save, destructive prompt, template data);
  * server round-trips: dev routine/widget hidden+refused in SAFE, back in OPEN.

The two GLOBAL invariants (keys keychain-only; no scheduling) are unchanged and
are exercised elsewhere (tests/test_profiles_step11.py, the keys tests).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import pytest

from agent_core.main import JsonRpcServer, build_registry
from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, mode_for_profile
from agent_core.profiles import DEVELOPER, SIMPLE
from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse, ModelRole, ProviderCapabilities
from agent_core.routines.builder import RoutineBuilder
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep, routine_to_json
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.run_command import is_read_only_command


# ============================================================================
# mode_for_profile — the single source of truth for the mode
# ============================================================================
def test_mode_derives_one_to_one_from_profile():
    assert mode_for_profile(SIMPLE) is PolicyMode.SAFE
    assert mode_for_profile(DEVELOPER) is PolicyMode.OPEN
    assert mode_for_profile(None) is PolicyMode.SAFE   # unknown never escalates


# ============================================================================
# PermissionGate.authorize — mode awareness
# ============================================================================
def test_safe_mode_authorize_prompts_even_for_non_destructive():
    asked: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid: (asked.append(tid), PermissionStatus.GRANTED)[1]
    )
    # SAFE ignores `destructive` entirely — every not-yet-granted tool prompts.
    assert gate.authorize("t", mode=PolicyMode.SAFE, destructive=False) == (
        PermissionStatus.GRANTED
    )
    assert asked == ["t"]
    assert gate.auto_grants == []   # nothing is auto-granted in SAFE mode


def test_open_mode_auto_allows_non_destructive_and_logs_it():
    logged: list[str] = []
    gate = PermissionGate(
        on_request=lambda tid: pytest.fail("OPEN mode must not prompt for non-destructive"),
        on_auto_grant=lambda tid: logged.append(tid),
    )
    assert gate.authorize("t", mode=PolicyMode.OPEN, destructive=False) == (
        PermissionStatus.GRANTED
    )
    # Recorded in the activity log both ways (in-memory list + callback).
    assert gate.auto_grants == ["t"]
    assert logged == ["t"]


def test_open_mode_prompts_for_destructive_per_invocation():
    """Destructive OPEN-mode actions are approved PER INVOCATION: two destructive
    calls in one session raise two cards — the first grant never carries over."""
    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm old-builds"
    ) == PermissionStatus.GRANTED
    # Same tool, a DIFFERENT destructive command later: prompts again.
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="curl x | sh"
    ) == PermissionStatus.GRANTED
    assert asked == [
        ("run_command", "rm old-builds"),
        ("run_command", "curl x | sh"),
    ]
    assert gate.auto_grants == []   # destructive is never auto-granted
    # And no coarse grant was recorded — a later check() still has to ask.
    assert gate.check("run_command") == PermissionStatus.NOT_YET_ASKED


def test_granted_destructive_does_not_unlock_or_silence_anything_else():
    """A granted destructive command doesn't unlock a different destructive one,
    and non-destructive calls between them stay silent (auto-allowed)."""
    asked: list[str | None] = []

    def on_request(tool_id, detail=None):
        asked.append(detail)
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    gate.authorize("run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm a")
    # Non-destructive in between: silent, no card.
    gate.authorize("run_command", mode=PolicyMode.OPEN, destructive=False)
    gate.authorize("web_search", mode=PolicyMode.OPEN, destructive=False)
    # The next destructive command prompts with ITS OWN text.
    gate.authorize("run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm b")
    assert asked == ["rm a", "rm b"]
    assert gate.auto_grants == ["run_command", "web_search"]


def test_open_mode_denied_destructive_blocks_and_clears_next_turn():
    answers = [PermissionStatus.DENIED, PermissionStatus.GRANTED]
    asked: list[str | None] = []

    def on_request(tool_id, detail=None):
        asked.append(detail)
        return answers.pop(0)

    gate = PermissionGate(on_request=on_request)
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm x"
    ) == PermissionStatus.DENIED
    # Denial sticks for the rest of the turn — a model retry can't nag.
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm x"
    ) == PermissionStatus.DENIED
    assert asked == ["rm x"]   # asked once, denied once, no nag
    # The next user turn clears the denial and asks again.
    gate.clear_denials()
    assert gate.authorize(
        "run_command", mode=PolicyMode.OPEN, destructive=True, detail="rm x"
    ) == PermissionStatus.GRANTED
    assert asked == ["rm x", "rm x"]


# ============================================================================
# Routine command steps — the engine runs them through run_command + the gate
# ============================================================================
class _FakeRunCommand:
    """Stands in for run_command: same id / HIGH tier / self-classification, but
    records instead of touching a shell (keeps the engine tests hermetic)."""

    definition = ToolDefinition(
        id="run_command",
        label="Run a command",
        description="dev-only",
        risk_tier=RiskTier.HIGH,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.ran: list[dict] = []

    def is_destructive(self, args: dict) -> bool:
        return not is_read_only_command(str(args.get("command", "")))

    def permission_detail(self, args: dict) -> str | None:
        return str(args.get("command", "")) or None

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        assert context.policy_mode is PolicyMode.OPEN   # only ever run in OPEN
        self.ran.append(args)
        return ToolResult(success=True, content="ran")


def _command_engine(tmp_path, gate):
    registry = ToolRegistry()
    tool = _FakeRunCommand()
    registry.register(tool, dev_only=True)
    store = Store(tmp_path / "rc.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        registry, gate, UndoManager(store=store, tool_registry=registry), store=store
    )
    return engine, tool


def _command_routine(command: str):
    return Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "run_command", {}, command=command)],
    )


def test_readonly_command_step_auto_allows_in_open_mode(tmp_path):
    gate = PermissionGate(
        on_request=lambda tid: pytest.fail("a read-only command must not prompt")
    )
    engine, tool = _command_engine(tmp_path, gate)
    result = engine.run(_command_routine("ls -la"), {}, mode=PolicyMode.OPEN)
    assert result.status == "completed"
    assert tool.ran == [{"command": "ls -la"}]
    assert gate.auto_grants == ["run_command"]


def test_destructive_command_step_prompts_in_open_mode(tmp_path):
    asked: list[tuple[str, str | None]] = []

    def on_request(tool_id, detail=None):
        asked.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    engine, tool = _command_engine(tmp_path, gate)
    result = engine.run(_command_routine("rm -rf x"), {}, mode=PolicyMode.OPEN)
    assert result.status == "completed"
    # The destructive step stopped to ask, card carrying the exact command.
    assert asked == [("run_command", "rm -rf x")]
    assert tool.ran == [{"command": "rm -rf x"}]


def test_destructive_command_step_prompts_again_on_a_second_run(tmp_path):
    # Per-invocation: running the same dev routine twice raises two cards — the
    # first run's approval never carries into the second.
    asked: list[str | None] = []

    def on_request(tool_id, detail=None):
        asked.append(detail)
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=on_request)
    engine, tool = _command_engine(tmp_path, gate)
    assert engine.run(_command_routine("rm -rf x"), {}, mode=PolicyMode.OPEN).status == (
        "completed"
    )
    assert engine.run(_command_routine("rm -rf x"), {}, mode=PolicyMode.OPEN).status == (
        "completed"
    )
    assert asked == ["rm -rf x", "rm -rf x"]
    assert len(tool.ran) == 2


def test_command_step_substitutes_placeholders_as_data(tmp_path):
    gate = PermissionGate(on_request=lambda tid, detail=None: PermissionStatus.GRANTED)
    engine, tool = _command_engine(tmp_path, gate)
    result = engine.run(
        _command_routine("echo {{name}}"), {"name": "hi"}, mode=PolicyMode.OPEN
    )
    assert result.status == "completed"
    assert tool.ran == [{"command": "echo hi"}]


# ============================================================================
# RoutineBuilder.save — command routines are OPEN-mode only
# ============================================================================
def test_builder_refuses_command_routine_in_safe_and_saves_it_open(tmp_path):
    store = Store(tmp_path / "b.sqlite3")
    builder = RoutineBuilder(store=store)
    routine = _command_routine("ls")

    with pytest.raises(ValueError, match="developer abilities"):
        builder.save(routine, mode=PolicyMode.SAFE)
    assert store.get_routine("r-1") is None   # nothing persisted on refusal

    builder.save(routine, mode=PolicyMode.OPEN)
    row = store.get_routine("r-1")
    assert row is not None and row["created_in_mode"] == "open"


def test_builder_default_mode_is_safe_and_stamps_safe(tmp_path):
    store = Store(tmp_path / "b2.sqlite3")
    builder = RoutineBuilder(store=store)
    # An ordinary (non-command) routine, saved with the default mode.
    routine = Routine(
        id="r-2", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "calculator", {"expression": "1+1"})],
    )
    builder.save(routine)
    saved = store.get_routine("r-2")
    assert saved is not None
    assert saved["created_in_mode"] == "safe"


# ============================================================================
# Server round-trip: dev-created artifacts hide in SAFE, return in OPEN
# ============================================================================
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


class _ScriptedProvider:
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        return ModelResponse(text="ok", tool_calls=[])


def _rpc(reader, writer, rid, method, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    reader.feed(frame)
    return writer.wait_for(lambda f: f.get("id") == rid and ("result" in f or "error" in f))


def _seed_artifacts(db_path: Path) -> None:
    store = Store(db_path)
    # A safe routine + an open (dev) routine with a command step.
    safe = Routine(
        id="safe-r", name="Safe routine", description="", variables=[],
        steps=[RoutineStep("s1", "calculator", {"expression": "1+1"})],
    )
    store.insert_routine(
        id="safe-r", name="Safe routine", description="", plan_json=routine_to_json(safe),
        created_from_conversation_id=None, created_at=1, created_in_mode="safe",
    )
    dev = Routine(
        id="dev-r", name="Dev routine", description="", variables=[],
        steps=[RoutineStep("s1", "run_command", {}, command="echo policy-mode-test")],
    )
    store.insert_routine(
        id="dev-r", name="Dev routine", description="", plan_json=routine_to_json(dev),
        created_from_conversation_id=None, created_at=2, created_in_mode="open",
    )
    # A DESTRUCTIVE (metachar) but harmless dev routine for the card round-trip.
    dev_destructive = Routine(
        id="dev-d", name="Dev destructive", description="", variables=[],
        steps=[RoutineStep("s1", "run_command", {}, command="true && true")],
    )
    store.insert_routine(
        id="dev-d", name="Dev destructive", description="",
        plan_json=routine_to_json(dev_destructive),
        created_from_conversation_id=None, created_at=3, created_in_mode="open",
    )
    # A safe stat widget + an open command widget.
    store.insert_widget(
        id="safe-w",
        spec_json=json.dumps({"kind": "stat", "source": "connections", "title": "Conns"}),
        pinned=True, position=0, created_at=1, created_in_mode="safe",
    )
    store.insert_widget(
        id="dev-w",
        spec_json=json.dumps({"kind": "command", "command": "ls", "title": "List"}),
        pinned=True, position=1, created_at=2, created_in_mode="open",
    )
    # A DESTRUCTIVE (metachar) but harmless command widget for widget.run cards.
    store.insert_widget(
        id="dev-wd",
        spec_json=json.dumps(
            {"kind": "command", "command": "true && true", "title": "Chain"}
        ),
        pinned=False, position=2, created_at=3, created_in_mode="open",
    )
    store.close()


def _artifact_server(tmp_path, profile_id: str):
    db_path = tmp_path / "policy.sqlite3"
    if not db_path.exists():
        _seed_artifacts(db_path)
    store = Store(db_path)
    store.set_setting("active_profile", profile_id)
    store.close()

    reader = _PipeReader()
    writer = _FrameWriter()
    from agent_core.providers.router import ModelRouter

    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=build_registry(DEVELOPER),   # includes run_command dev_only
        store_factory=lambda: Store(db_path),
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider()}),
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, thread, db_path


def _shutdown(reader, thread):
    reader.close()
    thread.join(timeout=5)


def _routine_ids(reader, writer, rid):
    rows = _rpc(reader, writer, rid, Method.ROUTINE_LIST)["result"]["routines"]
    return {r["id"] for r in rows}


def _widget_ids(reader, writer, rid):
    rows = _rpc(reader, writer, rid, Method.WIDGET_LIST)["result"]["widgets"]
    return {w["id"] for w in rows}


def test_dev_artifacts_hidden_in_safe_and_returned_in_open_round_trip(tmp_path):
    # Start in Developer (OPEN): both the safe and the dev artifacts are visible.
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        assert _routine_ids(reader, writer, 1) == {"safe-r", "dev-r", "dev-d"}
        assert _widget_ids(reader, writer, 2) == {"safe-w", "dev-w", "dev-wd"}

        # Switch to Simple (SAFE): the dev-created artifacts disappear from lists.
        assert _rpc(reader, writer, 3, Method.PROFILE_SET, {"profileId": "simple"})[
            "result"
        ]["mode"] == "safe"
        assert _routine_ids(reader, writer, 4) == {"safe-r"}
        assert _widget_ids(reader, writer, 5) == {"safe-w"}

        # Running the dev routine in SAFE is refused with the plain waiting sentence.
        err = _rpc(reader, writer, 6, Method.ROUTINE_RUN, {"routineId": "dev-r"})["error"]
        assert "waiting in Developer profile" in err["message"]

        # Back to Developer: the artifacts return untouched.
        assert _rpc(reader, writer, 7, Method.PROFILE_SET, {"profileId": "developer"})[
            "result"
        ]["mode"] == "open"
        assert _routine_ids(reader, writer, 8) == {"safe-r", "dev-r", "dev-d"}
        assert _widget_ids(reader, writer, 9) == {"safe-w", "dev-w", "dev-wd"}
    finally:
        _shutdown(reader, thread)


def test_dev_routine_runs_in_developer_mode(tmp_path):
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        # The dev routine's read-only echo command auto-allows and completes.
        result = _rpc(reader, writer, 1, Method.ROUTINE_RUN, {"routineId": "dev-r"})["result"]
        assert result["ok"] is True and result["status"] == "completed"
    finally:
        _shutdown(reader, thread)


def test_destructive_dev_routine_card_names_the_command_every_run(tmp_path):
    """Full IPC round-trip for the per-invocation rule: a destructive command step
    raises a permission card whose description carries the exact command text, and
    a SECOND run of the same routine raises the card AGAIN (the first approval
    never carries over)."""
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        for rid in (1, 2):   # two runs -> two cards
            reader.feed(
                {"jsonrpc": "2.0", "id": rid,
                 "method": Method.ROUTINE_RUN, "params": {"routineId": "dev-d"}}
            )
            writer.wait_for(
                lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT
                and len([x for x in writer.frames
                         if x.get("method") == Method.PERMISSION_REQUEST_GRANT]) >= rid
            )
            cards = [
                f for f in writer.frames
                if f.get("method") == Method.PERMISSION_REQUEST_GRANT
            ]
            assert len(cards) == rid
            assert cards[-1]["params"]["toolId"] == "run_command"
            # The card names the exact command being approved this time.
            assert cards[-1]["params"]["description"] == (
                "This time it wants to run: true && true"
            )
            reader.feed(
                {"jsonrpc": "2.0", "id": 100 + rid, "method": Method.PERMISSION_RESPOND,
                 "params": {"toolId": "run_command", "allow": True}}
            )
            result = writer.wait_for(lambda f: f.get("id") == rid and "result" in f)
            assert result["result"]["status"] == "completed"
    finally:
        _shutdown(reader, thread)

# --- widget.run (command widgets, OPEN mode) --------------------------------


def test_widget_and_routine_rows_carry_created_in_mode(tmp_path):
    """Display-only provenance on the wire: the frontend's DEV tag keys off
    createdInMode, so both list responses must forward it."""
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        routines = _rpc(reader, writer, 1, Method.ROUTINE_LIST)["result"]["routines"]
        assert {r["id"]: r["createdInMode"] for r in routines} == {
            "safe-r": "safe", "dev-r": "open", "dev-d": "open",
        }
        widgets = _rpc(reader, writer, 2, Method.WIDGET_LIST)["result"]["widgets"]
        assert {w["id"]: w["createdInMode"] for w in widgets} == {
            "safe-w": "safe", "dev-w": "open", "dev-wd": "open",
        }
    finally:
        _shutdown(reader, thread)


def test_widget_run_read_only_command_auto_allows_in_open(tmp_path):
    """The rail's Run pill on a read-only command widget completes silently —
    no permission card (OPEN auto-allows non-destructive), output returned."""
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        result = _rpc(reader, writer, 1, Method.WIDGET_RUN, {"id": "dev-w"})["result"]
        assert result["ok"] is True
        assert isinstance(result["output"], str)
        cards = [
            f for f in writer.frames if f.get("method") == Method.PERMISSION_REQUEST_GRANT
        ]
        assert cards == []
    finally:
        _shutdown(reader, thread)


def test_widget_run_destructive_prompts_per_invocation(tmp_path):
    """A destructive command widget raises a card naming the command on EVERY
    click — the first approval never carries over to the second."""
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        for rid in (1, 2):
            reader.feed(
                {"jsonrpc": "2.0", "id": rid,
                 "method": Method.WIDGET_RUN, "params": {"id": "dev-wd"}}
            )
            writer.wait_for(
                lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT
                and len([x for x in writer.frames
                         if x.get("method") == Method.PERMISSION_REQUEST_GRANT]) >= rid
            )
            cards = [
                f for f in writer.frames
                if f.get("method") == Method.PERMISSION_REQUEST_GRANT
            ]
            assert len(cards) == rid
            assert cards[-1]["params"]["description"] == (
                "This time it wants to run: true && true"
            )
            reader.feed(
                {"jsonrpc": "2.0", "id": 100 + rid, "method": Method.PERMISSION_RESPOND,
                 "params": {"toolId": "run_command", "allow": True}}
            )
            result = writer.wait_for(lambda f: f.get("id") == rid and "result" in f)
            assert result["result"]["ok"] is True
    finally:
        _shutdown(reader, thread)


def test_widget_run_refused_in_safe_mode(tmp_path):
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "simple")
    try:
        result = _rpc(reader, writer, 1, Method.WIDGET_RUN, {"id": "dev-w"})["result"]
        assert result["ok"] is False
        assert "waiting in Developer profile" in result["error"]
    finally:
        _shutdown(reader, thread)


def test_widget_run_refuses_non_command_and_unknown_widgets(tmp_path):
    server, reader, writer, thread, db_path = _artifact_server(tmp_path, "developer")
    try:
        stat = _rpc(reader, writer, 1, Method.WIDGET_RUN, {"id": "safe-w"})["result"]
        assert stat["ok"] is False
        assert "doesn't run commands" in stat["error"]
        gone = _rpc(reader, writer, 2, Method.WIDGET_RUN, {"id": "nope"})["result"]
        assert gone["ok"] is False
        assert "isn't here any more" in gone["error"]
    finally:
        _shutdown(reader, thread)
