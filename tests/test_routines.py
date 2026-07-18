"""Routine engine/builder/library tests — engineering-spec §6, §9.

§9 names the tests that matter most here: template resolution in isolation,
each on_failure mode, and — the §8.5 invariant — a step needing an ungranted
permission pauses rather than executes. The engine must share the live
orchestrator's gate/registry instances, so a grant given in live conversation
carries over and a Routine can never out-permission the user.
"""

from __future__ import annotations

import pytest

from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.routines.builder import RoutineBuilder
from agent_core.routines.engine import (
    RoutineEngine,
    resolve_template,
    topologically_sorted,
)
from agent_core.routines.library import RoutineLibrary
from agent_core.routines.model import Routine, RoutineStep, RoutineVariable
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import ToolRegistry


class _Result:
    def __init__(self, content):
        self.content = content


# --- template resolution (isolated — the §9 "highest-value test") ------------

def test_resolve_variable_and_step_result_placeholders():
    resolved = resolve_template(
        {"filename": "{{output_filename}}", "content": "Total: {{step_1.result}}"},
        {"output_filename": "summary.txt"},
        {"step_1": _Result(42)},
    )
    assert resolved == {"filename": "summary.txt", "content": "Total: 42"}


def test_resolve_handles_nested_structures_and_whitespace():
    resolved = resolve_template(
        {"outer": {"inner": ["{{ name }}", 7]}, "plain": True},
        {"name": "mira"},
        {},
    )
    assert resolved == {"outer": {"inner": ["mira", 7]}, "plain": True}


def test_resolve_unknown_placeholder_raises_plainly():
    with pytest.raises(ValueError, match="needs a value for 'missing'"):
        resolve_template({"a": "{{missing}}"}, {}, {})
    with pytest.raises(ValueError, match="hasn't run yet"):
        resolve_template({"a": "{{step_9.result}}"}, {}, {})


def test_resolved_values_are_data_not_code():
    # A value that looks like an expression stays a literal string (§6.1/§6.2).
    resolved = resolve_template(
        {"expression": "{{amount}} * 2"}, {"amount": "__import__('os')"}, {}
    )
    assert resolved == {"expression": "__import__('os') * 2"}


def test_topological_sort_and_cycle_detection():
    steps = [
        RoutineStep("c", "t", {}, depends_on=["b"]),
        RoutineStep("a", "t", {}),
        RoutineStep("b", "t", {}, depends_on=["a"]),
    ]
    assert [s.step_id for s in topologically_sorted(steps)] == ["a", "b", "c"]
    cyclic = [
        RoutineStep("x", "t", {}, depends_on=["y"]),
        RoutineStep("y", "t", {}, depends_on=["x"]),
    ]
    with pytest.raises(ValueError, match="Cycle"):
        topologically_sorted(cyclic)


# --- engine ------------------------------------------------------------------

class _FlakyTool:
    """LOW tool scripted per-args: {"fail": True} fails, otherwise succeeds."""

    definition = ToolDefinition(
        id="flaky",
        label="Do a step",
        description="Test tool.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self):
        self.executed: list[dict] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.executed.append(args)
        if args.get("fail"):
            return ToolResult(success=False, content="That step didn't work.")
        return ToolResult(success=True, content=args.get("value", "ok"))


class _SnapshotTool:
    definition = ToolDefinition(
        id="mutating",
        label="Change something",
        description="Test tool with undo.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self):
        self.undone: list[str] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        snapshot = ActionSnapshot(
            id="snap-1", tool_call_id="", tool_id="mutating",
            undo_payload={"x": 1}, created_at=1,
        )
        return ToolResult(success=True, content="changed", snapshot=snapshot)

    def undo(self, snapshot: ActionSnapshot) -> None:
        self.undone.append(snapshot.id)


class _RaisingTool:
    """LOW tool whose execute RAISES like a shell-bridge refusal — save_file's
    ``save_new_file`` raises RuntimeError ("A file with that name is already
    there") rather than returning success=False. A routine step must treat that
    as a FAILED step, not a crashed run."""

    definition = ToolDefinition(
        id="flaky",
        label="Do a step",
        description="Test tool that raises.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self, exc: Exception | None = None):
        self.executed: list[dict] = []
        self._exc = exc or RuntimeError(
            "A file with that name is already there — please choose another name."
        )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.executed.append(args)
        raise self._exc


def _engine(tmp_path, tool=None, gate=None, on_ask_user=None):
    registry = ToolRegistry()
    tool = tool or _FlakyTool()
    registry.register(tool)
    gate = gate or PermissionGate()
    store = Store(tmp_path / "routines.sqlite3")
    # The run log references the routines table (FK) — in production the engine
    # only ever runs SAVED routines (library.get), so tests persist one too.
    store.insert_routine(
        id="r-1", name="Test", description="", plan_json={},
        created_from_conversation_id=None, created_at=1,
    )
    undo = UndoManager(store=store, tool_registry=registry)
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=undo,
        on_ask_user=on_ask_user,
        store=store,
    )
    return engine, tool, gate, store


def _routine(steps, variables=()):
    return Routine(
        id="r-1", name="Test", description="", variables=list(variables), steps=steps
    )


def test_on_failure_abort_stops_the_run(tmp_path):
    engine, tool, gate, store = _engine(tmp_path)
    gate.grant("flaky")
    routine = _routine([
        RoutineStep("s1", "flaky", {"fail": True}, on_failure="abort"),
        RoutineStep("s2", "flaky", {}, depends_on=["s1"]),
    ])
    result = engine.run(routine, {})
    assert result.status == "failed"
    assert result.detail == "That step didn't work."
    assert len(tool.executed) == 1  # s2 never ran
    # Run log records the failure (§6.4).
    row = store._conn.execute("SELECT status FROM routine_runs").fetchone()
    assert row["status"] == "failed"


def test_on_failure_skip_continues(tmp_path):
    engine, tool, gate, _ = _engine(tmp_path)
    gate.grant("flaky")
    routine = _routine([
        RoutineStep("s1", "flaky", {"fail": True}, on_failure="skip"),
        RoutineStep("s2", "flaky", {"value": "second"}, depends_on=["s1"]),
    ])
    result = engine.run(routine, {})
    assert result.status == "completed"
    assert len(tool.executed) == 2


def test_on_failure_ask_user_continue_and_stop(tmp_path):
    answers = {"continue": True}
    asked: list[str] = []

    def ask(step, run_id, message):
        asked.append(message)
        return answers["continue"]

    engine, tool, gate, _ = _engine(tmp_path, on_ask_user=ask)
    gate.grant("flaky")
    routine = _routine([
        RoutineStep("s1", "flaky", {"fail": True}, on_failure="ask_user"),
        RoutineStep("s2", "flaky", {}, depends_on=["s1"]),
    ])

    result = engine.run(routine, {})
    assert result.status == "completed" and len(tool.executed) == 2
    assert asked == ["That step didn't work."]

    answers["continue"] = False
    tool.executed.clear()
    result = engine.run(routine, {})
    assert result.status == "cancelled"
    assert len(tool.executed) == 1  # stopped before s2


def test_ungranted_permission_pauses_and_denied_never_executes(tmp_path):
    requested: list[str] = []

    def on_request(tool_id):
        requested.append(tool_id)
        return PermissionStatus.DENIED

    engine, tool, gate, _ = _engine(tmp_path, gate=PermissionGate(on_request=on_request))
    routine = _routine([RoutineStep("s1", "flaky", {})])
    result = engine.run(routine, {})
    # The pause happened (the gate's request round-trip ran), the user said no,
    # and the tool NEVER executed — no auto-escalation (§8.5).
    assert requested == ["flaky"]
    assert result.status == "failed"
    assert tool.executed == []


def test_live_grant_carries_into_routine_run(tmp_path):
    # Shared-gate invariant: a grant given in live conversation means the
    # routine runs without asking again — and nothing more than that.
    engine, tool, gate, _ = _engine(tmp_path)
    gate.grant("flaky")   # "granted live, earlier"
    result = engine.run(_routine([RoutineStep("s1", "flaky", {})]), {})
    assert result.status == "completed"


def test_variable_defaults_fill_missing_values(tmp_path):
    engine, tool, gate, _ = _engine(tmp_path)
    gate.grant("flaky")
    routine = _routine(
        [RoutineStep("s1", "flaky", {"value": "{{name}}"})],
        variables=[RoutineVariable("name", "What name?", default="fallback")],
    )
    result = engine.run(routine, {})
    assert result.status == "completed"
    assert tool.executed == [{"value": "fallback"}]


def test_step_result_feeds_later_step_and_snapshots_recorded(tmp_path):
    registry = ToolRegistry()
    flaky, mutating = _FlakyTool(), _SnapshotTool()
    registry.register(flaky)
    registry.register(mutating)
    gate = PermissionGate()
    gate.grant("flaky")
    gate.grant("mutating")
    store = Store(tmp_path / "chain.sqlite3")
    store.insert_routine(
        id="r-1", name="Test", description="", plan_json={},
        created_from_conversation_id=None, created_at=1,
    )
    undo = UndoManager(store=store, tool_registry=registry)
    engine = RoutineEngine(registry, gate, undo, store=store)

    routine = _routine([
        RoutineStep("s1", "flaky", {"value": "42"}),
        RoutineStep("s2", "mutating", {}, depends_on=["s1"]),
        RoutineStep("s3", "flaky", {"value": "got {{s1.result}}"}, depends_on=["s2"]),
    ])
    result = engine.run(routine, {})
    assert result.status == "completed"
    assert flaky.executed[-1] == {"value": "got 42"}
    # The mutating step's snapshot is undoable like any live action (§6.4).
    undo_results = undo.undo_last(1)
    assert undo_results[0].success and mutating.undone == ["snap-1"]


def test_raising_tool_is_a_failed_step_not_a_crashed_run(tmp_path):
    # A tool that RAISES (shell-bridge refusal) must fail the step, honour the
    # on_failure policy, and — critically — still finish the run so its
    # routine_runs log isn't left stuck at 'running'. Before the fix the
    # exception propagated out of run(), skipping _finish entirely.
    engine, tool, gate, store = _engine(tmp_path, tool=_RaisingTool())
    gate.grant("flaky")
    routine = _routine([
        RoutineStep("s1", "flaky", {}, on_failure="abort"),
        RoutineStep("s2", "flaky", {}, depends_on=["s1"]),
    ])
    result = engine.run(routine, {})
    assert result.status == "failed"
    # The plain bridge sentence is carried through as the run detail (not a stack trace).
    assert "already there" in result.detail
    assert len(tool.executed) == 1  # aborted before s2
    # The run log was finalised, not abandoned mid-run.
    row = store._conn.execute("SELECT status FROM routine_runs").fetchone()
    assert row["status"] == "failed"


def test_raising_tool_with_skip_continues_to_next_step(tmp_path):
    # on_failure="skip" applies to a RAISED failure exactly as to a returned one.
    engine, tool, gate, _ = _engine(tmp_path, tool=_RaisingTool())
    gate.grant("flaky")
    routine = _routine([
        RoutineStep("s1", "flaky", {"n": 1}, on_failure="skip"),
        RoutineStep("s2", "flaky", {"n": 2}, depends_on=["s1"], on_failure="skip"),
    ])
    result = engine.run(routine, {})
    # Both steps ran (the raise didn't abort the run); the run completed.
    assert result.status == "completed"
    assert tool.executed == [{"n": 1}, {"n": 2}]


def test_non_runtime_error_from_tool_becomes_plain_failed_step(tmp_path):
    # A non-RuntimeError (a genuine bug in a tool) must not leak its repr — it
    # collapses to one plain sentence, same as the live orchestrator.
    engine, tool, gate, _ = _engine(
        tmp_path, tool=_RaisingTool(exc=ValueError("boom internal detail"))
    )
    gate.grant("flaky")
    result = engine.run(_routine([RoutineStep("s1", "flaky", {}, on_failure="abort")]), {})
    assert result.status == "failed"
    assert result.detail == "That step didn't work."
    assert "boom internal detail" not in result.detail


# --- builder (§6.3) ----------------------------------------------------------

class _Call:
    def __init__(self, tool_id, args):
        self.id = "c"
        self.tool_id = tool_id
        self.args = args


class _Msg:
    def __init__(self, role, content="", tool_calls=()):
        self.role = role
        self.content = content
        self.tool_calls = list(tool_calls)


class _Conv:
    def __init__(self, messages):
        self.id = "conv-1"
        self.messages = messages


def test_builder_extracts_tool_calls_not_prose_and_generalizes():
    conversation = _Conv([
        _Msg("user", "add up my invoices and save it"),
        _Msg("assistant", "", [_Call("read_file", {"file_handle": "handle-123"})]),
        _Msg("tool", "invoice text"),
        _Msg("assistant", "", [_Call("save_file", {"filename": "total.txt", "content": "x"})]),
        _Msg("tool", "/Users/mira/Desktop/total.txt"),
        _Msg("assistant", "All done! I saved the total."),
    ])
    draft = RoutineBuilder().propose_from_recent_actions(conversation)

    assert [s.tool_id for s in draft.steps] == ["read_file", "save_file"]
    # Sequential chain mirrors what happened live.
    assert draft.steps[1].depends_on == ["step_1"]
    # Session-scoped file handle -> variable with NO default (must re-pick).
    assert draft.steps[0].args_template == {"file_handle": "{{chosen_file}}"}
    # Filename -> variable keeping the literal as its default.
    assert draft.steps[1].args_template["filename"] == "{{output_filename}}"
    by_name = {v.name: v for v in draft.variables}
    assert by_name["chosen_file"].default is None
    assert by_name["output_filename"].default == "total.txt"


def test_builder_raises_plainly_when_nothing_to_extract():
    with pytest.raises(ValueError, match="couldn't find any actions"):
        RoutineBuilder().propose_from_recent_actions(_Conv([_Msg("user", "hello")]))


def test_preview_is_plain_language():
    registry = ToolRegistry()
    registry.register(_FlakyTool())
    draft = _routine([RoutineStep("s1", "flaky", {})])
    preview = RoutineBuilder().preview(draft, registry)
    assert preview["steps"] == ["1. Do a step"]      # label, not tool_id / raw JSON
    assert preview["routineId"] == draft.id


# --- library + persistence (§6.5) --------------------------------------------

def test_library_crud_round_trip(tmp_path):
    store = Store(tmp_path / "lib.sqlite3")
    builder = RoutineBuilder(store=store)
    library = RoutineLibrary(store=store)

    draft = _routine(
        [RoutineStep("s1", "flaky", {"value": "{{name}}"})],
        variables=[RoutineVariable("name", "What name?", default="a")],
    )
    builder.save(draft, conversation_id=None)

    rows = library.list()
    assert len(rows) == 1 and rows[0]["routine"].name == "Test"

    # v1 edit surface: metadata + variable defaults only (§6.5).
    library.update_metadata("r-1", name="Renamed", variable_defaults={"name": "b"})
    updated = library.get("r-1")
    assert updated.name == "Renamed"
    assert updated.variables[0].default == "b"
    # Step sequence untouched by a metadata edit.
    assert [s.step_id for s in updated.steps] == ["s1"]

    library.record_run("r-1")
    assert library.list()[0]["runCount"] == 1

    library.delete("r-1")
    assert library.list() == []
    with pytest.raises(KeyError):
        library.get("r-1")
