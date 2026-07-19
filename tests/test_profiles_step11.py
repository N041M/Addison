"""Profiles reshape surface AND policy mode (spec §4.7; owner decision 2026-07-19).

Build step 11 + the mode-scoped safety restructuring. The profile is the single
source of truth for the policy mode (policy.py): Simple=SAFE (today's behaviour,
byte-for-byte), Developer=OPEN (fewer prompts, dev-only tools). What the switch
must NOT touch — the two GLOBAL invariants — is the centerpiece here: keys stay
keychain-only (never on the wire), and there is no scheduling. Around that:
app_settings persistence, the profile.get/set surface (now carrying ``mode``),
onboarding routing, raw diagnostics, and the read-only routine-plan view. The
detailed SAFE-vs-OPEN gate/registry/routine/widget behaviour lives in
tests/test_policy_modes.py; here we pin the profile→mode wiring and the invariants
that hold regardless of mode.

Server-level harness style mirrors tests/test_ipc_server.py and
tests/test_setup_assistant.py: the real JsonRpcServer on fake pipes, a scripted
provider, no network anywhere.
"""

from __future__ import annotations

import dataclasses
import json
import queue
import threading
import time
from pathlib import Path

import pytest

from agent_core.main import (
    _BYOK_ONBOARDING_MESSAGE,
    _UNKNOWN_PROFILE_MESSAGE,
    JsonRpcServer,
    build_registry,
)
from agent_core.memory.store import Store
from agent_core.profiles import (
    DEVELOPER,
    SIMPLE,
    resolve_active_profile,
)
from agent_core.protocol import Method
from agent_core.providers.base import (
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.routines.model import Routine, RoutineStep, routine_to_json
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry


# --- pipe/writer harness (house style) -------------------------------------
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
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        self.histories.append(list(messages))
        return self._responses.pop(0)


class _RaisingProvider:
    """Raises a RuntimeError with a fixed plain message on send()."""

    def __init__(self, message: str) -> None:
        self._message = message
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None) -> ModelResponse:
        self.histories.append(list(messages))
        raise RuntimeError(self._message)


class _SpyTool:
    """LOW-risk tool that records executions."""

    definition = ToolDefinition(
        id="spy_tool",
        label="Check something for you",
        description="A test tool.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, content="spied")


class _BadMediumTool:
    """MEDIUM-risk tool that (deliberately) implements no undo() — registration
    of this MUST raise, in every profile (§4.2, §8.7, spec §9's test #1)."""

    definition = ToolDefinition(
        id="bad_medium",
        label="Mutate something",
        description="A mutating tool with no undo.",
        risk_tier=RiskTier.MEDIUM,
        parameters_schema={"type": "object", "properties": {}},
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, content="mutated")


def _tool_call_response(tool_id: str = "spy_tool") -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[ToolCallRequest(id="call-1", tool_id=tool_id, args={})],
    )


def _seed(db_path: Path, *, profile_id: str | None = None, routines=None) -> None:
    """Pre-populate the SQLite file the server's store_factory will open."""
    store = Store(db_path)
    if profile_id is not None:
        store.set_setting("active_profile", profile_id)
    for routine in routines or []:
        store.insert_routine(
            id=routine.id,
            name=routine.name,
            description=routine.description,
            plan_json=routine_to_json(routine),
            created_from_conversation_id=None,
            created_at=0,
        )
    store.close()


def _server(
    tmp_path,
    responses,
    *,
    profile_id: str | None = None,
    tool=None,
    routines=None,
    primary=None,
    setup=None,
    primary_key_probe=None,
    setup_prompt: str | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)  # callers may pass a sub-path
    db_path = tmp_path / "profiles-step11.sqlite3"
    _seed(db_path, profile_id=profile_id, routines=routines)

    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)

    configured = {}
    if primary is not None:
        configured[ModelRole.PRIMARY] = primary
    elif responses is not None:
        configured[ModelRole.PRIMARY] = _ScriptedProvider(responses)
    if setup is not None:
        configured[ModelRole.SETUP_ASSISTANT] = setup

    reader = _PipeReader()
    writer = _FrameWriter()
    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=lambda: Store(db_path),
        model_router=ModelRouter(configured=configured),
        primary_key_probe=primary_key_probe,
        setup_prompt=setup_prompt,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, reader, writer, thread


def _shutdown(reader: _PipeReader, thread: threading.Thread) -> None:
    reader.close()
    thread.join(timeout=5)


def _rpc(reader, writer, rid, method, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    reader.feed(frame)
    return writer.wait_for(lambda f: f.get("id") == rid and ("result" in f or "error" in f))


# ============================================================================
# Profile -> policy mode: SAFE (Simple) prompts; OPEN (Developer) auto-allows.
# ============================================================================
def test_simple_profile_gate_blocks_and_denial_prevents_execution(tmp_path):
    """SAFE mode (Simple profile) is byte-for-byte the historical behaviour: a
    not-yet-asked tool blocks on permission.requestGrant (nothing runs), and a
    denial keeps it from ever executing."""
    responses = [_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])]
    tool = _SpyTool()
    server, reader, writer, thread = _server(
        tmp_path, responses, profile_id="simple", tool=tool
    )
    try:
        reader.feed(
            {"jsonrpc": "2.0", "id": 1,
             "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": "go"}}
        )
        # It BLOCKS on the card: the tool has not run and the turn hasn't completed.
        card = writer.wait_for(lambda f: f.get("method") == Method.PERMISSION_REQUEST_GRANT)
        assert card["params"]["toolId"] == "spy_tool"
        assert tool.calls == []
        assert not any(f.get("id") == 1 and "result" in f for f in writer.frames)

        # A denial still prevents execution.
        reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.PERMISSION_RESPOND,
             "params": {"toolId": "spy_tool", "allow": False}}
        )
        writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert tool.calls == []
        tool_messages = [m for m in server.conversation.messages if m.role == "tool"]
        assert tool_messages and "declined" in tool_messages[0].content
        assert server._active_profile.id.value == "simple"
    finally:
        _shutdown(reader, thread)


def test_developer_profile_auto_allows_non_destructive_tool_no_card(tmp_path):
    """OPEN mode (Developer profile) is the deliberate change: a non-destructive
    (LOW) tool auto-grants — no permission card is emitted, the tool runs, the turn
    completes, and the auto-grant is recorded so the UI can show it happened.
    "Open" means fewer prompts, not no gate — the gate still ran (and logged)."""
    responses = [_tool_call_response(), ModelResponse(text="Okay.", tool_calls=[])]
    tool = _SpyTool()
    server, reader, writer, thread = _server(
        tmp_path, responses, profile_id="developer", tool=tool
    )
    try:
        result = _rpc(reader, writer, 1, Method.CONVERSATION_SEND_MESSAGE, {"text": "go"})
        assert result["result"]["ok"] is True
        # No permission card was ever emitted, and the tool DID run.
        assert not any(
            f.get("method") == Method.PERMISSION_REQUEST_GRANT for f in writer.frames
        )
        assert tool.calls == [{}]
        # The gate ran and recorded the auto-grant (the "activity log").
        assert server.permission_gate.auto_grants == ["spy_tool"]
        assert server._active_profile.id.value == "developer"
    finally:
        _shutdown(reader, thread)


def test_undo_registration_check_raises_regardless_of_profile():
    """Registering a MEDIUM tool without undo() raises in every profile — the
    single most important invariant (spec §9). A profile chooses WHICH tools get
    registered; it can never turn the undo check off."""
    for profile in (SIMPLE, DEVELOPER):
        # The registry each profile uses is the same class with the same check;
        # build_registry(profile) only registers the (already-safe) v1 set.
        build_registry(profile)  # does not raise: all v1 tools are undoable/LOW
        registry = ToolRegistry()
        with pytest.raises(ValueError):
            registry.register(_BadMediumTool())


def test_key_handling_is_not_a_profile_concern():
    """Key isolation (§8.3) holds identically in both profiles: nothing in the
    Profile config names or carries key material, so switching profiles cannot
    open a key-reading code path — there is nowhere in the config for a key to live."""
    for profile in (SIMPLE, DEVELOPER):
        for field in dataclasses.fields(profile):
            name = field.name.lower()
            assert not any(t in name for t in ("key", "token", "secret", "password"))
        blob = repr(profile).lower()
        assert "api_key" not in blob and "secret" not in blob


def test_profile_switch_emits_no_keychain_traffic(tmp_path):
    """Switching the profile touches only app_settings — it never triggers a
    keychain round-trip or puts a key on the wire (§8.3)."""
    server, reader, writer, thread = _server(tmp_path, [])
    try:
        # The switch flips the mode (GLOBAL invariant: no keys on the wire, either way).
        assert _rpc(reader, writer, 1, Method.PROFILE_SET, {"profileId": "developer"})[
            "result"
        ] == {"ok": True, "mode": "open"}
        _rpc(reader, writer, 2, Method.PROFILE_GET)
        assert not any(
            f.get("method") == Method.KEYCHAIN_GET_PROVIDER_KEY for f in writer.frames
        )
    finally:
        _shutdown(reader, thread)


# ============================================================================
# app_settings round-trip persistence
# ============================================================================
def test_setting_round_trips_across_store_instances(tmp_path):
    db = tmp_path / "settings.sqlite3"
    s1 = Store(db)
    # Missing key -> default; typed value stored as TEXT.
    assert s1.get_setting("active_profile") is None
    assert s1.get_setting("active_profile", "simple") == "simple"
    s1.set_setting("active_profile", "developer")
    # Upsert (not a duplicate insert).
    s1.set_setting("active_profile", "developer")
    s1.close()

    s2 = Store(db)  # a fresh connection on the same file
    assert s2.get_setting("active_profile") == "developer"
    assert resolve_active_profile(s2) is DEVELOPER
    s2.close()


def test_unknown_or_missing_persisted_profile_resolves_simple(tmp_path):
    db = tmp_path / "settings.sqlite3"
    s = Store(db)
    assert resolve_active_profile(s) is SIMPLE  # nothing persisted yet
    s.set_setting("active_profile", "banana")
    assert resolve_active_profile(s) is SIMPLE  # garbage never escalates surface
    s.close()


def test_resolve_without_store_is_simple():
    assert resolve_active_profile() is SIMPLE


# ============================================================================
# profile.get / profile.set server surface
# ============================================================================
def test_profile_get_default_then_set_flips_flags_immediately(tmp_path):
    server, reader, writer, thread = _server(tmp_path, [])
    try:
        got = _rpc(reader, writer, 1, Method.PROFILE_GET)["result"]
        assert got["activeProfile"] == "simple"
        assert got["mode"] == "safe"   # Simple derives SAFE mode (policy.py)
        assert [p["id"] for p in got["profiles"]] == ["simple", "developer"]
        # Selector copy is present and honest about identical safety.
        dev = next(p for p in got["profiles"] if p["id"] == "developer")
        assert dev["label"] == "Developer"
        assert "Same safety rules" in dev["description"]
        assert got["flags"] == {
            "exposeRoutinePlan": False,
            "rawDiagnostics": False,
            "headlessCli": False,
            "byokFirstOnboarding": False,
        }

        # Switch takes effect immediately — no restart — and reports the new mode.
        assert _rpc(reader, writer, 2, Method.PROFILE_SET, {"profileId": "developer"})[
            "result"
        ] == {"ok": True, "mode": "open"}
        flipped = _rpc(reader, writer, 3, Method.PROFILE_GET)["result"]
        assert flipped["activeProfile"] == "developer"
        assert flipped["mode"] == "open"   # Developer derives OPEN mode
        assert flipped["flags"] == {
            "exposeRoutinePlan": True,
            "rawDiagnostics": True,
            "headlessCli": True,
            "byokFirstOnboarding": True,
        }
        assert server._active_profile is DEVELOPER
    finally:
        _shutdown(reader, thread)


def test_profile_set_unknown_id_is_refused_plainly_and_leaves_active_unchanged(tmp_path):
    server, reader, writer, thread = _server(tmp_path, [], profile_id="developer")
    try:
        error = _rpc(reader, writer, 1, Method.PROFILE_SET, {"profileId": "root"})["error"]
        assert error["message"] == _UNKNOWN_PROFILE_MESSAGE
        # The failed switch did not change the active profile.
        assert _rpc(reader, writer, 2, Method.PROFILE_GET)["result"]["activeProfile"] == (
            "developer"
        )
        assert server._active_profile is DEVELOPER
    finally:
        _shutdown(reader, thread)


def test_profile_set_persists_to_app_settings(tmp_path):
    db = tmp_path / "profiles-step11.sqlite3"  # same name _server uses
    server, reader, writer, thread = _server(tmp_path, [])
    try:
        _rpc(reader, writer, 1, Method.PROFILE_SET, {"profileId": "developer"})
    finally:
        _shutdown(reader, thread)
    # A fresh Store on the same file sees the persisted choice.
    s = Store(db)
    assert s.get_setting("active_profile") == "developer"
    s.close()


# ============================================================================
# Onboarding path by profile (§4.7): Developer is BYOK-first, Simple relays.
# ============================================================================
def _routing_server(tmp_path, *, profile_id: str):
    primary = _ScriptedProvider([ModelResponse(text="primary reply", tool_calls=[])])
    setup = _ScriptedProvider([ModelResponse(text="setup reply", tool_calls=[])])
    server, reader, writer, thread = _server(
        tmp_path,
        None,
        profile_id=profile_id,
        primary=primary,
        setup=setup,
        primary_key_probe=lambda: False,  # no PRIMARY key configured
        setup_prompt="SETUP-PROMPT",
    )
    return server, reader, writer, thread, primary, setup


def test_developer_no_key_turn_gets_byok_message_and_never_hits_relay(tmp_path):
    server, reader, writer, thread, primary, setup = _routing_server(
        tmp_path, profile_id="developer"
    )
    try:
        error = _rpc(
            reader, writer, 1, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}
        )["error"]
        assert error["message"] == _BYOK_ONBOARDING_MESSAGE
        # The Setup Assistant relay was NEVER called (zero histories), nor PRIMARY.
        assert setup.histories == []
        assert primary.histories == []
    finally:
        _shutdown(reader, thread)


def test_simple_no_key_turn_still_routes_to_setup_assistant(tmp_path):
    server, reader, writer, thread, primary, setup = _routing_server(
        tmp_path, profile_id="simple"
    )
    try:
        assert _rpc(
            reader, writer, 1, Method.CONVERSATION_SEND_MESSAGE, {"text": "hi"}
        )["result"]["ok"] is True
        # Simple keeps the §4.6 relay handoff: the Setup Assistant handled the turn.
        assert len(setup.histories) == 1
        assert primary.histories == []
        assert setup.histories[0][0].role == "system"  # injected setup prompt
    finally:
        _shutdown(reader, thread)


# ============================================================================
# Raw diagnostics (Developer): extra error detail, IDENTICAL plain message.
# ============================================================================
def _error_frame_for_failed_turn(tmp_path, profile_id: str) -> dict:
    server, reader, writer, thread = _server(
        tmp_path, None, profile_id=profile_id, primary=_RaisingProvider("boom")
    )
    try:
        return _rpc(reader, writer, 1, Method.CONVERSATION_SEND_MESSAGE, {"text": "go"})[
            "error"
        ]
    finally:
        _shutdown(reader, thread)


def test_error_data_raw_only_for_developer_with_identical_plain_message(tmp_path):
    simple_err = _error_frame_for_failed_turn(tmp_path / "s", "simple")
    dev_err = _error_frame_for_failed_turn(tmp_path / "d", "developer")

    # The plain-language message is EXACTLY the same in both profiles.
    assert simple_err["message"] == dev_err["message"] == "boom"
    # Simple carries no raw detail; Developer attaches the underlying repr.
    assert "data" not in simple_err
    assert dev_err["data"] == {"raw": "RuntimeError('boom')"}


# ============================================================================
# Read-only routine-plan view (Developer, §6.5): planSteps only for Developer.
# ============================================================================
def _plan_routine() -> Routine:
    return Routine(
        id="r1",
        name="Save it",
        description="Saves a file.",
        variables=[],
        steps=[
            RoutineStep(
                step_id="step_1",
                tool_id="save_file",
                args_template={"filename": "{{name}}"},
                depends_on=[],
                on_failure="abort",
            )
        ],
    )


def test_routine_plan_steps_present_only_for_developer(tmp_path):
    routines = [_plan_routine()]

    server_s, reader_s, writer_s, thread_s = _server(
        tmp_path / "s", None, profile_id="simple", routines=routines
    )
    try:
        rows = _rpc(reader_s, writer_s, 1, Method.ROUTINE_LIST)["result"]["routines"]
        assert rows and "planSteps" not in rows[0]  # Simple: no plan view
    finally:
        _shutdown(reader_s, thread_s)

    server_d, reader_d, writer_d, thread_d = _server(
        tmp_path / "d", None, profile_id="developer", routines=routines
    )
    try:
        rows = _rpc(reader_d, writer_d, 1, Method.ROUTINE_LIST)["result"]["routines"]
        assert rows[0]["planSteps"] == [
            {
                "stepId": "step_1",
                "toolId": "save_file",
                "argsTemplate": {"filename": "{{name}}"},
                "dependsOn": [],
                "onFailure": "abort",
            }
        ]
    finally:
        _shutdown(reader_d, thread_d)
