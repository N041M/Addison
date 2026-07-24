"""Shared JsonRpcServer test harness — and the per-test half of the live-database guard.

Two unrelated things live here, both of them global to the test run:

1. `_never_touch_the_live_database` (autouse) — the per-test half of the guard that
   keeps anything in this repo from opening the owner's real `~/.addison` database.
   The half that does the actual blocking is NOT here: it is armed by
   `import agent_core` (see agent_core/live_db_guard.py), because a pytest fixture
   protects only pytest, and the accident it was written for was an ad-hoc probe
   script. What remains here is test-run housekeeping — pointing ADDISON_DB_PATH at
   the test's tmp dir, and re-arming the guard between tests.
2. The IPC harness described below.

The IPC round-trip tests run the real server in-process on fake pipes: frames
go in through a blocking reader, every outgoing frame is captured, and a
scripted provider drives the turns (house style of tests/test_orchestrator.py).
The plumbing to stand one of those servers up — `_PipeReader`/`_FrameWriter`,
the scripted provider + spy tool, and the `JsonRpcServer` wiring — used to be
re-inlined (copy-pasted with small variations) at half a dozen sites. It lives
here now as ONE canonical builder (`build_server`) so the variations that
actually differ between sites (registered tool, provider instance, shell
bridge, cloud catalog, connect/key probes, Ollama client) are just keyword
arguments and there is no copy-paste to drift.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass

import pytest

from agent_core import live_db_guard
from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.providers.base import (
    ModelProvider,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.shell_bridge import IpcShellBridge
from agent_core.tools.base import ExecutionContext, RiskTier, Tool, ToolDefinition, ToolResult
from agent_core.tools.registry import ToolRegistry

# Every server in these tests persists to the same file under the test's tmp_path
# (each test gets a fresh tmp_path); some assertions reopen it with a plain
# sqlite3 connection, so the name is shared here.
IPC_DB_NAME = "ipc-test.sqlite3"


@pytest.fixture(autouse=True)
def _never_touch_the_live_database(monkeypatch, tmp_path):
    """Keep every test off the owner's real ``~/.addison`` database.

    The blocking itself is not here and cannot be — it belongs to any process that
    imports ``agent_core`` (``agent_core/live_db_guard.py``), because the accident
    that prompted all of this was a one-off probe script, which pytest never sees.
    This fixture supplies the two things that are genuinely per-test:

    1. ``ADDISON_DB_PATH`` points at the test's own tmp dir, so code that resolves
       the *default* path lands somewhere harmless instead of merely being refused.
    2. The app's opt-in is reset. ``allow_live_database()`` is process-wide by
       design, so a test that exercises it must not leave the guard disarmed for
       every test that follows.

    The assertion is not ceremony: if the import-time install ever stops running,
    every test in the suite silently loses its protection, and this is the one
    place that would notice.
    """
    assert live_db_guard.is_installed(), (
        "The live-database guard is not armed. It installs on `import agent_core` "
        "(agent_core/live_db_guard.py); without it nothing stops a test from writing "
        "to the owner's real database."
    )
    monkeypatch.setattr(live_db_guard, "_app_has_declared_itself", False)
    monkeypatch.setenv("ADDISON_DB_PATH", str(tmp_path / "addison-under-test.sqlite3"))
    yield


class _PipeReader:
    """Blocking readline() fed frame-by-frame from the test."""

    def __init__(self) -> None:
        self._lines: queue.Queue[str] = queue.Queue()

    def feed(self, frame: dict) -> None:
        self._lines.put(json.dumps(frame) + "\n")

    def close(self) -> None:
        self._lines.put("")  # readline() returning "" is EOF for the read loop

    def readline(self) -> str:
        return self._lines.get()


class _FrameWriter:
    """Captures outgoing frames and lets tests block until one matches."""

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
    """Returns canned ModelResponses in order; records replayed histories."""

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

    def send(self, messages, tools, effort=None, timeout=None) -> ModelResponse:
        self.histories.append(list(messages))
        return self._responses.pop(0)


class _SpyTool:
    """LOW-risk tool that records executions; optionally reads the clipboard
    through the shell bridge so tests can drive a Core -> Shell round-trip."""

    definition = ToolDefinition(
        id="spy_tool",
        label="Check something for you",
        description="A test tool.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {}},
    )

    def __init__(self, use_bridge: bool = False) -> None:
        self.calls: list[dict] = []
        self._use_bridge = use_bridge

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        if self._use_bridge:
            assert context.shell_bridge is not None
            return ToolResult(success=True, content=context.shell_bridge.read_clipboard())
        return ToolResult(success=True, content="spied")


def _tool_call_response(tool_id: str = "spy_tool") -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[ToolCallRequest(id="call-1", tool_id=tool_id, args={})],
    )


@dataclass
class IpcHarness:
    """The running server plus the handles tests drive it through."""

    server: JsonRpcServer
    reader: _PipeReader
    writer: _FrameWriter
    thread: threading.Thread
    provider: ModelProvider
    tool: Tool | None


def build_server(
    tmp_path,
    *,
    responses=None,
    provider=None,
    tool=None,
    register_tool: bool = True,
    bridge: IpcShellBridge | None = None,
    cloud_catalog=None,
    connect_provider=None,
    provider_key_probe=None,
    ollama_client=None,
    seed_widgets: bool = False,
    store_factory=None,
) -> IpcHarness:
    """Stand up a real JsonRpcServer on fake pipes and start its run loop.

    The pieces that vary between call sites are all keyword arguments:

    - ``responses``: the scripted provider's canned ModelResponses (ignored when
      an explicit ``provider`` is supplied — e.g. a custom flaky provider).
    - ``provider``: a ready-made provider instance to install as PRIMARY.
    - ``tool`` / ``register_tool``: which tool the registry carries. Defaults to a
      fresh ``_SpyTool``; pass ``register_tool=False`` for an empty registry.
    - ``bridge``, ``cloud_catalog``, ``connect_provider``, ``provider_key_probe``,
      ``ollama_client``: forwarded straight to the server (each defaults to the
      server's own default when left as None).
    - ``seed_widgets``: whether first-run default-widget seeding runs. Defaults to
      False (the harness pre-sets the 'widgets_seeded' flag so the rail starts empty),
      keeping the widget-mechanics tests isolated from the seeded defaults; the
      seeding tests pass True to exercise it.
    - ``store_factory``: replaces the default file-backed factory. Exists for the
      G3 tests, whose whole premise is a factory that RAISES — a store that will
      not open is the situation the snapshot floor exists for, and it has to be
      expressible here to be testable at all. The db_path is passed to the server
      either way, so the sidecar directory still lands in the test's tmp tree.

    The caller owns teardown via :func:`_shutdown` (kept explicit because a few
    tests relaunch on the same database and must stop one server before the next).
    """
    registry = ToolRegistry()
    if register_tool:
        tool = tool or _SpyTool()
        registry.register(tool)
    if provider is None:
        provider = _ScriptedProvider(responses or [])
    reader = _PipeReader()
    writer = _FrameWriter()

    def _default_store_factory() -> Store:
        store = Store(tmp_path / IPC_DB_NAME)
        if not seed_widgets:
            # Suppress first-run seeding so tests start with an empty rail.
            store.set_setting("widgets_seeded", "1")
        return store

    server = JsonRpcServer(
        reader=reader,
        writer=writer,
        tool_registry=registry,
        store_factory=store_factory or _default_store_factory,
        db_path=tmp_path / IPC_DB_NAME,
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        shell_bridge=bridge,
        cloud_catalog=cloud_catalog,
        connect_provider=connect_provider,
        provider_key_probe=provider_key_probe,
        ollama_client=ollama_client,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return IpcHarness(
        server=server, reader=reader, writer=writer, thread=thread, provider=provider, tool=tool
    )


def _shutdown(reader: _PipeReader, thread: threading.Thread) -> None:
    reader.close()
    thread.join(timeout=5)
