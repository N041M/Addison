"""The review surface's read paths — Phase-3 plan Build §1.

``workspace.listDirectory`` and ``workspace.readFile``: RPC, never a registry tool. A
person clicking a folder is not the model acting, so a browse must not hand the model a
``list_directory`` capability as a side effect, and must not raise a permission card in
front of a click somebody just made.

Every test here is mutation-proven, and the mutation is named in the docstring (the
repo's standard — docs/HANDOFF.md "How step 1 was verified"). The four that matter most:

  * the registry pin — someone adding a browse TOOL;
  * the three confinement refusals — the mode gate dropped as "redundant", the floor
    skipped on a read path, a symlink walked out of trust;
  * resolve-once — the raw argument reaching the shell instead of the resolved value,
    which is only visible through a SYMLINKED ALIAS of tmp_path (pytest's tmp_path is
    already realpath'd, so without the alias the mechanism is invisible: HANDOFF rigor
    lesson 11, and the reason step-5's own path tests could not see it);
  * one resolution for the label AND the boundary — the KNOWN-GAPS name race, closed
    here (a symlink swapped between two realpaths could put a name on the card that was
    true only when it was read).
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from agent_core.main import JsonRpcServer, build_registry
from agent_core.memory.store import Store
from agent_core.permissions.gate import PermissionGate
from agent_core.policy import PolicyMode
from agent_core.profiles import DEVELOPER
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep
from agent_core.shell_bridge import IpcShellBridge
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ShellBridge,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import ToolRegistry
from tests.conftest import ShellBridgeStubs, _FrameWriter, _PipeReader

_MAIN_PY = Path(__file__).resolve().parent.parent / "agent_core" / "main.py"


# --- fakes -----------------------------------------------------------------


class _FakeBrowseBridge(ShellBridgeStubs):
    """The shell's half of the two read paths, recording exactly which path it was
    asked for — which is the assertion that proves only the RESOLVED value crosses.

    Everything else comes from ``ShellBridgeStubs`` and raises, so a test that
    accidentally reaches another method fails loudly instead of measuring nothing."""

    def __init__(self, entries=None, content: str = "hello\n", truncated: bool = False) -> None:
        self.listed: list[str] = []
        self.read: list[str] = []
        self._entries = entries if entries is not None else []
        self._content = content
        self._truncated = truncated

    def list_workspace_directory(self, path: str) -> dict:
        self.listed.append(path)
        return {"entries": list(self._entries), "truncated": self._truncated}

    def read_workspace_file_for_view(self, path: str) -> dict:
        self.read.append(path)
        return {
            "content": self._content,
            "bytes": len(self._content.encode("utf-8")) + (1024 if self._truncated else 0),
            "truncated": self._truncated,
        }


class _RefusingBrowseBridge(ShellBridgeStubs):
    """A shell that refuses in plain language, the way the real one does when its own
    data-dir floor or an unreadable folder answers."""

    def __init__(self, message: str) -> None:
        self._message = message

    def list_workspace_directory(self, path: str) -> dict:
        raise RuntimeError(self._message)

    def read_workspace_file_for_view(self, path: str) -> dict:
        raise RuntimeError(self._message)


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


class _SwappingPathTool:
    """A path-bounded tool whose ``affected_path`` answers a DIFFERENT path the SECOND
    time it is asked — the symlink swap, made deterministic and observable.

    This is the shape of the closed gap: two realpaths of one argument can disagree,
    and when they do, the first is what confinement approved and the second is what the
    person was shown. A tool cannot be made to swap on a real filesystem reliably
    (that race is a race), so the swap is expressed here instead, which is stricter —
    it fails on ANY second resolution, not merely on an unlucky one."""

    definition = ToolDefinition(
        id="swapping_path_tool",
        label="Touch a file",
        description="A test tool that is path-bounded.",
        risk_tier=RiskTier.LOW,
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    def __init__(self, first: str, then: str) -> None:
        self._answers = [first, then]
        self.resolutions = 0
        self.acted_on: list[str | None] = []

    def affected_path(self, args: dict) -> str | None:
        self.resolutions += 1
        return self._answers[min(self.resolutions - 1, len(self._answers) - 1)]

    def permission_detail_for_path(self, resolved_path: str | None) -> str | None:
        return Path(resolved_path).name if resolved_path else None

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.acted_on.append(context.resolved_path)
        return ToolResult(success=True, content="ok")


# --- helpers ---------------------------------------------------------------


def _rpc(reader, writer, rid, method, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    reader.feed(frame)
    return writer.wait_for(lambda f: f.get("id") == rid and ("result" in f or "error" in f))


class _Harness:
    """A live server on fake pipes, with a fake shell bridge and a real store.

    The data directory is a SIBLING of the project (``db_path`` lives under
    ``tmp/data``), never tmp_path itself — the floor refuses everything under the data
    dir, so a database at the top of tmp_path would make every project path in the test
    untrusted for a reason that has nothing to do with what is being asserted."""

    def __init__(self, tmp_path, bridge, developer: bool = True, planted_root=None) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        db_path = data_dir / "app.sqlite3"
        self.bridge = bridge
        self.reader = _PipeReader()
        self.writer = _FrameWriter()

        def factory() -> Store:
            store = Store(db_path)
            # Seeded HERE, on the worker thread, because sqlite3 connections are bound
            # to the thread that opened them — and because a planted row is the point:
            # `grantTrust` refuses the data dir at the door, so the only way to test
            # that the FLOOR still refuses a row that exists anyway is to write one
            # without going through the door.
            if planted_root is not None:
                store.insert_workspace_trust(root=str(Path(planted_root).resolve()), granted_at=1)
            return store

        self.server = JsonRpcServer(
            reader=self.reader,
            writer=self.writer,
            tool_registry=build_registry(DEVELOPER),
            store_factory=factory,
            db_path=db_path,
            model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider([])}),
            shell_bridge=bridge,
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self._rid = 0
        if developer:
            assert self.call("profile.set", {"profileId": "developer"})["mode"] == "open"

    def call(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        return _rpc(self.reader, self.writer, self._rid, method, params or {})["result"]

    def trust(self, directory) -> None:
        assert self.call("workspace.grantTrust", {"directory": str(directory)})["ok"] is True

    def close(self) -> None:
        self.reader.close()
        self.thread.join(timeout=5)


@pytest.fixture
def project(tmp_path) -> Path:
    """A project directory, and an ALIAS symlink pointing at it.

    The alias is the whole point of the fixture: pytest's ``tmp_path`` is already
    realpath'd, so browsing it proves nothing about resolving. Browsing through
    ``tmp_path/"alias"`` proves both halves at once — that the handler resolves BEFORE
    it asks whether the path is trusted, and that only the resolved value crosses to
    the shell."""
    directory = tmp_path / "project"
    directory.mkdir()
    (tmp_path / "alias").symlink_to(directory)
    return directory


def _entries(*rows: tuple[str, str, int]) -> list[dict]:
    return [{"name": name, "kind": kind, "size": size} for name, kind, size in rows]


# ============================================================================
# THE REGISTRY PIN — a browse is never a tool
# ============================================================================
def test_the_registry_holds_exactly_these_tools_and_no_browse_tool():
    """The design's load-bearing negative: the review surface adds ZERO model
    capability. Reading a directory arrived in this tree as an RPC handler, and the
    one way it could quietly become something the model can call is somebody adding
    ``ListDirectoryTool()`` to ``build_registry`` — which is a two-line change that
    every other test in the repo would survive.

    BOTH HALVES, because neither alone is enough. The source half reads the function
    with ``ast`` and pins the CONSTRUCTED tool classes: it sees a registration even if
    the tool is hidden from every view (``not_callable``), which a runtime query cannot.
    The runtime half pins the resulting tool IDS: it sees a class that kept its name and
    changed its id, which the source half cannot.

    Mutation: register any new tool in ``build_registry``. Both sets change and both
    assertions name it."""
    expected_classes = {
        "WebSearchTool", "ReadWebPageTool", "ReadFileTool", "ReadClipboardTool",
        "CalculatorTool", "SaveFileTool", "DraftMessageTool", "OpenLinkTool",
        "SnapshotNowTool", "RunCommandTool", "ReadProjectFileTool",
        "WriteProjectFileTool", "CreateAutomationTool", "ArmAutomationTool",
        "DisarmAutomationTool",
    }
    tree = ast.parse(_MAIN_PY.read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_registry"
    )
    constructed = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("Tool")
    }
    assert constructed == expected_classes, (
        "build_registry constructs a different set of tools than this test freezes. If "
        "a tool was genuinely added, add it here deliberately — and if it is a browse, "
        "a listing or a file read, it belongs on the workspace.* RPC instead "
        "(docs/phase-3-review-surface-plan.md Build §1)."
    )

    expected_ids = {
        "web_search", "read_web_page", "read_file", "read_clipboard", "calculator",
        "save_file", "draft_message", "open_link", "snapshot_now", "run_command",
        "read_project_file", "write_project_file", "create_automation",
        "arm_automation", "disarm_automation",
    }
    registry = build_registry(DEVELOPER)
    assert {d.id for d in registry.visible_tools(PolicyMode.OPEN)} == expected_ids
    # And nothing named for browsing exists in either view, under any spelling.
    for definition in registry.visible_tools(PolicyMode.OPEN):
        assert "list_directory" not in definition.id
        assert "browse" not in definition.id


# ============================================================================
# CONFINEMENT — three refusals, in the handler's own order
# ============================================================================
def test_browsing_under_simple_is_refused_even_with_a_live_trust_row(tmp_path, project):
    """THE MODE GATE, and it is load-bearing rather than decorative. Trust rows persist
    and nothing revokes them on a profile switch, so a folder trusted under Developer is
    still trusted-shaped when the person switches to Simple — without this gate the
    Simple window could browse it.

    The row is granted first, in Developer, exactly as a person would: a test that
    switched profiles with no trust row would pass with the gate deleted, because the
    trust check would refuse anyway. That vacuity is the trap.

    Mutation: drop the ``self._mode() is PolicyMode.OPEN`` check in either handler."""
    bridge = _FakeBrowseBridge(entries=_entries(("a.txt", "file", 1)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        # Still Developer: the browse works, so the refusal below is about the profile.
        assert harness.call("workspace.listDirectory", {"directory": str(project)})["entries"]

        assert harness.call("profile.set", {"profileId": "simple"})["mode"] == "safe"
        listed = harness.call("workspace.listDirectory", {"directory": str(project)})
        read = harness.call("workspace.readFile", {"path": str(project / "a.txt")})
        for answer in (listed, read):
            assert answer["ok"] is False
            assert answer["error"] == (
                "Looking inside your folders is part of the Developer profile. Switch "
                "to it in Settings to browse them here."
            )
        # And the shell was never asked — the refusal is BEFORE the bridge, not a
        # filtered answer after it.
        assert bridge.listed == [str(project.resolve())]
        assert bridge.read == []
    finally:
        harness.close()


def test_browsing_a_symlink_that_escapes_a_trusted_root_is_refused(tmp_path, project):
    """A link INSIDE a trusted folder pointing OUT of it — the shape confinement exists
    for, now on a read path. Resolving is what catches it: the link's own location is
    innocent and only its target is not.

    Both a directory link and a file link, because the surface offers both moves — a
    person expands a folder and opens a file, and a boundary that held for one of them
    would be a boundary that held for neither.

    Mutation: skip the resolve (step 2) or the ``_is_trusted_path`` check (step 3) in
    either handler — the escape is then browsed and its bytes cross the bridge."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.env").write_text("KEY=1", encoding="utf-8")
    (project / "elsewhere").symlink_to(outside)
    (project / "notes.txt").symlink_to(outside / "secrets.env")

    bridge = _FakeBrowseBridge(entries=_entries(("secrets.env", "file", 5)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        refusal = (
            "That's outside the folders you've trusted, so Addison won't look there. "
            "Trust the folder first if you want Addison to see inside it."
        )
        listed = harness.call("workspace.listDirectory", {"directory": str(project / "elsewhere")})
        assert listed == {"ok": False, "error": refusal}
        read = harness.call("workspace.readFile", {"path": str(project / "notes.txt")})
        assert read == {"ok": False, "error": refusal}
        # Nothing crossed to the shell: refused before the bridge, both times.
        assert bridge.listed == [] and bridge.read == []
    finally:
        harness.close()


def test_browsing_addisons_own_data_dir_is_refused_even_with_a_planted_trust_row(tmp_path):
    """THE FLOOR BEATS A ROOT, on the read paths too. ``grantTrust`` refuses the data dir
    at the door, so this plants the row directly in the store — the shape a pre-floor
    row, a hand-edited database or a future bug would produce — and asserts the browse
    still refuses. Match-a-root THEN the floor is what makes that true.

    Mutation: reorder ``is_trusted`` to let a matching root win, or drop the floor from
    the read paths, and Addison's own memory becomes browsable in a file tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "snapshots").mkdir()

    bridge = _FakeBrowseBridge(entries=_entries(("restore.json", "file", 10)))
    harness = _Harness(tmp_path, bridge, planted_root=data_dir)
    try:
        listed = harness.call("workspace.listDirectory", {"directory": str(data_dir / "snapshots")})
        assert listed["ok"] is False
        assert "outside the folders you've trusted" in listed["error"]
        read = harness.call("workspace.readFile", {"path": str(data_dir / "app.sqlite3")})
        assert read["ok"] is False
        assert bridge.listed == [] and bridge.read == []
    finally:
        harness.close()


# ============================================================================
# RESOLVE ONCE, AND PASS ONLY THE RESOLVED VALUE
# ============================================================================
def test_only_the_resolved_path_reaches_the_shell(tmp_path, project):
    """Both halves of the resolve-once rule, made visible by a SYMLINKED ALIAS.

    ``tmp_path`` is already realpath'd, so a test that browsed it directly could not
    tell a handler that resolves from one that does not (HANDOFF rigor lesson 11 — the
    step-5 path tests had exactly that blind spot). Browsing through the alias:

      * proves the resolve happens BEFORE the trust check — the trust row holds the real
        path, so an unresolved alias would be refused;
      * proves ONLY the resolved value crosses — the bridge records what it was asked
        for, and it is never the alias.

    Mutation: pass ``params["directory"]``/``params["path"]`` to the bridge instead of
    the resolved value, and the recorded path is the alias — the TOCTOU gap step 5
    closed for the file tools, reopened on a read."""
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    alias = tmp_path / "alias"
    bridge = _FakeBrowseBridge(entries=_entries(("a.txt", "file", 6)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)

        listed = harness.call("workspace.listDirectory", {"directory": str(alias)})
        assert listed["directory"] == str(project.resolve())
        assert listed["root"] == str(project.resolve())
        assert bridge.listed == [str(project.resolve())], "the alias must never cross"

        read = harness.call("workspace.readFile", {"path": str(alias / "a.txt")})
        assert read["path"] == str((project / "a.txt").resolve())
        assert bridge.read == [str((project / "a.txt").resolve())]
    finally:
        harness.close()


def test_a_path_the_os_cannot_resolve_is_refused_rather_than_raising(tmp_path, project):
    """A browse is a click, and a click must never be able to end a turn. ``realpath``
    raises ``ValueError`` on an embedded NUL and ``expanduser`` raises ``RuntimeError``
    for a ``~someone`` the OS cannot look up — the fourth exception that was missing from
    ``call_affected_path``'s tuple until 2026-08-08, found the same way.

    Mutation: drop the try/except in ``_browse_resolve`` — these become error frames
    instead of a plain refusal, and the NUL case takes the handler down."""
    harness = _Harness(tmp_path, _FakeBrowseBridge())
    try:
        harness.trust(project)
        for bad in ["/tmp/a\x00b", "~addison_no_such_user_42/x", "", "   ", None, 42]:
            listed = harness.call("workspace.listDirectory", {"directory": bad})
            assert listed["ok"] is False, bad
            assert "full path to the folder" in listed["error"], bad
            read = harness.call("workspace.readFile", {"path": bad})
            assert read["ok"] is False, bad
            assert "full path to the file" in read["error"], bad
    finally:
        harness.close()


# ============================================================================
# THE PAYLOADS — one level, `escapes` core-side, truncation reported
# ============================================================================
def test_escapes_marks_the_entries_that_lead_out_of_trust_and_only_those(tmp_path, project):
    """``escapes`` is computed CORE-SIDE by realpath + the same trust predicate the
    boundary uses — one predicate, never a second copy in Rust that could disagree with
    the refusal. It is an honesty affordance (dim the row, say it points outside), never
    the boundary: the row is still listed, and opening it is what refuses.

    Mutation: hard-code ``escapes: False``, or compute it from ``kind`` — a link that
    leads to another file inside the project would then be marked as leaving, and the
    one that leads out would not."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "inside.txt").write_text("x", encoding="utf-8")
    (project / "sibling").symlink_to(project / "inside.txt")   # a link that STAYS inside
    (project / "elsewhere").symlink_to(outside)                # and one that does not
    # A DANGLING link out of the folder: its target does not exist yet, which is exactly
    # the shape that defeated the shell's floor once before (the planted-file bug in
    # filesystem.rs). ``realpath`` follows a link whether or not the target is there, so
    # this must be marked as leaving too.
    (project / "dangling").symlink_to(outside / "not-created-yet.env")

    bridge = _FakeBrowseBridge(entries=_entries(
        ("inside.txt", "file", 1),
        ("sibling", "symlink", 12),
        ("elsewhere", "symlink", 20),
        ("dangling", "symlink", 24),
        # A name with nothing behind it at all. It resolves LEXICALLY to a path inside
        # the folder, so it does not escape — stated here because the opposite is the
        # tempting guess, and a row that dimmed every file the shell listed a moment
        # before it was deleted would teach people to ignore the marking.
        ("ghost", "file", 0),
    ))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        listed = harness.call("workspace.listDirectory", {"directory": str(project)})
        escapes = {e["name"]: e["escapes"] for e in listed["entries"]}
        assert escapes == {
            "inside.txt": False,
            "sibling": False,
            "elsewhere": True,
            "dangling": True,
            "ghost": False,
        }
        # The shell's own fields ride through untouched, kind included: a symlink is a
        # symlink here, never the kind of the thing it points at.
        kinds = {e["name"]: e["kind"] for e in listed["entries"]}
        assert kinds["elsewhere"] == "symlink" and kinds["inside.txt"] == "file"
        assert listed["truncated"] is False
    finally:
        harness.close()


def test_a_truncated_listing_and_a_truncated_view_both_say_so(tmp_path, project):
    """The shell caps a listing at 500 entries and cuts a view at 256 KiB; the core's
    job is to carry both facts to the surface unchanged. A payload that dropped them
    would render a partial folder and a partial file as complete ones, which is the one
    thing this surface exists not to do.

    ``bytes`` is the FILE's size, never the excerpt's — the number that says how much is
    not on screen, and the number a length of ``content`` cannot supply.

    Mutation: drop either ``truncated`` from the payload, or compute ``bytes`` from the
    content that came back."""
    bridge = _FakeBrowseBridge(
        entries=_entries(("a.txt", "file", 1)), content="visible part", truncated=True
    )
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        listed = harness.call("workspace.listDirectory", {"directory": str(project)})
        assert listed["truncated"] is True

        read = harness.call("workspace.readFile", {"path": str(project / "a.txt")})
        assert read["truncated"] is True
        assert read["content"] == "visible part"
        assert read["bytes"] == len("visible part") + 1024
        assert read["root"] == str(project.resolve())
    finally:
        harness.close()


def test_a_shell_refusal_reaches_the_person_as_the_shells_own_sentence(tmp_path, project):
    """The shell's refusals are already plain language (its data-dir floor, an
    unreadable folder, a file that is not text). Relaying it is what makes the second
    floor visible; swallowing it would leave a person staring at an empty pane.

    Mutation: catch and replace the message with the generic line, and the shell's
    independent floor becomes indistinguishable from a hiccup."""
    harness = _Harness(tmp_path, _RefusingBrowseBridge("That file isn't a text file, so Addison can't read it here."))
    try:
        harness.trust(project)
        read = harness.call("workspace.readFile", {"path": str(project / "a.png")})
        assert read == {
            "ok": False,
            "error": "That file isn't a text file, so Addison can't read it here.",
        }
    finally:
        harness.close()


def test_both_read_paths_are_wired_through_the_worker_job_map(tmp_path, project):
    """The wiring, asserted as wiring: an unrouted method answers -32601 and never
    reaches the handler at all. Every test above goes through the same door, but this
    one names the failure, because a handler with no job-map entry is the mistake that
    leaves a whole feature dead while its unit tests stay green (HANDOFF trap 3).

    It also pins that these run on the WORKER: they touch the store twice and make a
    Core -> Shell round-trip, and a round-trip made on the read loop is the thread that
    has to deliver the answer blocking on itself.

    Mutation: remove either entry from ``_WORKSPACE_JOBS`` or its branch in
    ``_worker_loop``."""
    from agent_core.main import _WORKSPACE_JOBS

    assert _WORKSPACE_JOBS["workspace.listDirectory"] == "workspace_list_directory"
    assert _WORKSPACE_JOBS["workspace.readFile"] == "workspace_read_file"

    harness = _Harness(tmp_path, _FakeBrowseBridge(entries=_entries(("a.txt", "file", 1))))
    try:
        harness.trust(project)
        answered = _rpc(
            harness.reader, harness.writer, 500,
            "workspace.listDirectory", {"directory": str(project)},
        )
        assert "result" in answered and "error" not in answered
        # A method that is NOT routed is what this must not look like.
        unknown = _rpc(harness.reader, harness.writer, 501, "workspace.listDirectoryDeep", {})
        assert unknown["error"]["code"] == -32601
    finally:
        harness.close()


# ============================================================================
# ONE RESOLUTION FOR THE LABEL AND THE BOUNDARY (KNOWN-GAPS, closed here)
# ============================================================================
def _run_one_call(registry, tool, args, trust_check, announced) -> None:
    from agent_core.orchestrator import Conversation, Orchestrator

    provider = _ScriptedProvider([
        ModelResponse(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", tool_id=tool.definition.id, args=args)],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])

    class _Store:
        def insert_action_snapshot(self, snapshot) -> None: ...

    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=_Store(), tool_registry=registry),
        trust_check=trust_check,
        on_activity=lambda tool_id, label, detail=None: announced.append((tool_id, detail)),
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="go"))
    orchestrator.run_turn(conversation, mode=PolicyMode.OPEN)


def test_the_card_and_the_boundary_share_one_resolution_in_the_live_loop(tmp_path):
    """THE CLOSED GAP (docs/KNOWN-GAPS.md, "the name on the card is resolved a SECOND
    time"). The label and the confinement check used to be two separate realpaths of one
    argument, so a symlink swapped between them showed a name that was true only when it
    was read — the label could lie while the effect stayed correct.

    Now the caller resolves ONCE and hands that value to ``call_permission_detail``,
    which passes it to the tool's ``permission_detail_for_path``. Three assertions,
    which are three ways of saying the same thing: the tool is asked for a path exactly
    once, the Activity Panel names THAT path, and ``execute`` acts on it.

    Mutation: revert either call site to ``call_permission_detail(tool, call.args)`` —
    ``resolutions`` becomes 2 and the panel announces ``after.txt``, a file the call
    never touched."""
    registry = ToolRegistry()
    tool = _SwappingPathTool(str(tmp_path / "before.txt"), str(tmp_path / "after.txt"))
    registry.register(tool)
    announced: list[tuple[str, str | None]] = []

    _run_one_call(registry, tool, {"path": "whatever"}, lambda p: True, announced)

    assert tool.resolutions == 1, "the path must be resolved once per call, not per reader"
    assert announced == [("swapping_path_tool", "before.txt")]
    assert tool.acted_on == [str(tmp_path / "before.txt")]


def test_a_refused_call_names_the_path_the_boundary_checked(tmp_path):
    """The same property on the branch that has no card at all: a confined-out call
    still writes an audit row, and that row's ``detail`` is built from the path
    confinement refused — not from a second reading of the argument. This is the branch
    the old code re-resolved on, and it sits OUTSIDE the orchestrator's per-call error
    handling, which is why it also must not raise.

    Mutation: revert the ``confined_out`` audit site — the row names ``after.txt`` while
    the refusal was about ``before.txt``."""
    registry = ToolRegistry()
    tool = _SwappingPathTool(str(tmp_path / "before.txt"), str(tmp_path / "after.txt"))
    registry.register(tool)
    rows: list[dict] = []
    announced: list[tuple[str, str | None]] = []

    from agent_core.orchestrator import Conversation, Orchestrator

    provider = _ScriptedProvider([
        ModelResponse(
            text=None,
            tool_calls=[ToolCallRequest(id="c1", tool_id="swapping_path_tool", args={"path": "x"})],
        ),
        ModelResponse(text="done", tool_calls=[]),
    ])

    class _Store:
        def insert_action_snapshot(self, snapshot) -> None: ...

    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=_Store(), tool_registry=registry),
        trust_check=lambda p: False,          # nothing is trusted -> confined_out
        on_activity=lambda tool_id, label, detail=None: announced.append((tool_id, detail)),
        on_tool_audit=rows.append,
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="go"))
    orchestrator.run_turn(conversation, mode=PolicyMode.OPEN)

    assert tool.resolutions == 1
    assert tool.acted_on == [], "a confined-out call must never execute"
    assert [row["detail"] for row in rows] == ["before.txt"]


def test_the_routine_engine_shares_the_same_single_resolution(tmp_path):
    """The live loop's twin, because a boundary only one dispatch path enforces is not a
    boundary (SAFE invariant 3's reasoning) — and the engine had the same two-realpath
    shape for the same reason.

    Mutation: revert the engine's ``detail = call_permission_detail(tool, resolved_args,
    affected)`` — the step is announced as ``after.txt`` and the tool is asked twice."""
    registry = ToolRegistry()
    tool = _SwappingPathTool(str(tmp_path / "before.txt"), str(tmp_path / "after.txt"))
    registry.register(tool)
    announced: list[tuple[str, str | None]] = []

    store = Store(tmp_path / "routines.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=PermissionGate(),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        store=store,
        trust_check=lambda p: True,
        on_activity=lambda tool_id, label, detail=None: announced.append((tool_id, detail)),
    )
    routine = Routine(
        id="r-1", name="T", description="", variables=[],
        steps=[RoutineStep("s1", "swapping_path_tool", {"path": "whatever"})],
    )
    result = engine.run(routine, {}, mode=PolicyMode.OPEN)

    assert result.status == "completed"
    assert tool.resolutions == 1
    assert announced == [("swapping_path_tool", "before.txt")]
    assert tool.acted_on == [str(tmp_path / "before.txt")]


# ============================================================================
# THE BRIDGE SEAM — these two are not tool surface
# ============================================================================
def test_the_browse_bridge_methods_are_absent_from_the_tool_protocol():
    """``tools/base.ShellBridge`` promises it is "exactly the surface the v1 tools need
    — nothing broader". A tool that could list a directory IS the ``list_directory``
    capability this whole design exists not to hand the model, so the two read paths
    live on the SERVER's bridge contract and nowhere a tool can reach.

    Mutation: move either method onto ``ShellBridge`` — which is precisely the shortcut
    somebody takes when a tool "just needs to list a folder"."""
    for method in ("list_workspace_directory", "read_workspace_file_for_view"):
        assert not hasattr(ShellBridge, method), (
            f"{method} must not be on the TOOL-facing Protocol — see the note in "
            "agent_core/tools/base.py"
        )
        assert hasattr(IpcShellBridge, method)

    # And the tool-facing methods really are still there, so the assertion above is
    # about placement rather than about a Protocol that lost its members.
    for method in ("read_workspace_file", "write_workspace_file"):
        assert hasattr(ShellBridge, method)


def test_the_bridge_sends_only_the_path_it_was_given(tmp_path):
    """The bridge is a transport and nothing else: one path in, one method call out. It
    must not expand, resolve or default anything — the caller already did that once, and
    a second opinion here would be the second resolution this change removed.

    Mutation: have either method touch its argument (``expanduser``, ``abspath``)."""
    sent: list[tuple[str, dict]] = []

    class _Recording(IpcShellBridge):
        def _call(self, method: str, params: dict, timeout: float | None = None) -> dict:
            sent.append((method, params))
            return {"entries": [], "truncated": False, "content": "", "bytes": 0}

    bridge = _Recording()
    bridge.list_workspace_directory("~/not/expanded")
    bridge.read_workspace_file_for_view("~/not/expanded/a.txt")
    assert sent == [
        ("shell.listWorkspaceDirectory", {"path": "~/not/expanded"}),
        ("shell.readWorkspaceFileForView", {"path": "~/not/expanded/a.txt"}),
    ]


def test_the_handlers_never_read_their_raw_parameter_after_resolving():
    """Step 4 of the confinement order, pinned at the source: ONLY the resolved value
    reaches the bridge. A runtime test can show that the value sent is correct for the
    paths it tries; this shows that there is no second reading of ``params`` anywhere in
    either handler, which is the property that makes the first one general.

    Mutation: add a ``params.get(...)`` below the resolve — the TOCTOU shape, where the
    check and the effect can be about different files."""
    source = (
        Path(__file__).resolve().parent.parent / "agent_core" / "rpc" / "workspace.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("_workspace_list_directory", "_workspace_read_file"):
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        # BOTH SPELLINGS, because a subscript is the one somebody reaches for when
        # they already "know" the key is there: `params["directory"]` is exactly the
        # second read this pin exists to forbid, and counting only `.get` would let it
        # through.
        reads = [
            node for node in ast.walk(function)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "params"
            )
            or (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "params"
            )
        ]
        assert len(reads) == 1, (
            f"{name} reads its raw parameter {len(reads)} times — it must read it once, "
            "resolve once, and pass only the resolved value on (plan Build §1)"
        )


def test_the_data_dir_and_roots_are_read_at_the_moment_of_use(tmp_path, project):
    """Revoking trust must take effect on the very next click, not the next launch. The
    handlers read the trust rows through the shared resolver each time rather than
    caching them on the server, which is the same rule ``_trusted_roots`` states for the
    seatbelt's allowlist.

    Mutation: cache the roots (on the server, on the mixin, anywhere) — the browse keeps
    working after the folder is untrusted."""
    bridge = _FakeBrowseBridge(entries=_entries(("a.txt", "file", 1)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        assert harness.call("workspace.listDirectory", {"directory": str(project)})["entries"]

        assert harness.call("workspace.revokeTrust", {"directory": str(project)})["ok"] is True
        after = harness.call("workspace.listDirectory", {"directory": str(project)})
        assert after["ok"] is False
        assert bridge.listed == [str(project.resolve())], "no second listing after revoke"
    finally:
        harness.close()


def test_a_missing_shell_answers_plainly_rather_than_crashing(tmp_path, project):
    """No desktop shell (the CLI path, a wedged bridge) is a plain sentence, not an
    exception and not an empty tree that reads as an empty folder.

    Mutation: drop the ``bridge is None`` guard — the handler raises ``AttributeError``
    on the read loop's behalf and the person gets a server error frame."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.sqlite3"
    reader, writer = _PipeReader(), _FrameWriter()
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
        assert _rpc(reader, writer, 1, "profile.set", {"profileId": "developer"})["result"]["ok"]
        assert _rpc(
            reader, writer, 2, "workspace.grantTrust", {"directory": str(project)}
        )["result"]["ok"]
        answer = _rpc(
            reader, writer, 3, "workspace.listDirectory", {"directory": str(project)}
        )["result"]
        assert answer == {
            "ok": False,
            "error": "Addison can't look at your files just now. Please try again.",
        }
    finally:
        reader.close()
        thread.join(timeout=5)


def test_the_listing_is_one_level_and_takes_no_depth(tmp_path, project):
    """No ``depth`` parameter, on purpose: a depth knob is how a full repo walk gets
    requested by accident. A caller that sends one is answered exactly as if it had not
    — the parameter is not honoured, not silently partly honoured.

    Mutation: add a ``depth`` read to the handler (and, downstream, a recursive walk in
    Rust) — the payload for the same folder stops being the same."""
    bridge = _FakeBrowseBridge(entries=_entries(("src", "directory", 96)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        plain = harness.call("workspace.listDirectory", {"directory": str(project)})
        deep = harness.call("workspace.listDirectory", {"directory": str(project), "depth": 99})
        assert plain == deep
        assert bridge.listed == [str(project.resolve())] * 2
    finally:
        harness.close()


def test_a_browse_needs_no_permission_card(tmp_path, project):
    """A click is not a tool call. If a browse ever raised a card, the surface would ask
    a person to approve the folder they just opened — which is the failure the plan
    names when it says this is RPC and never a registry tool.

    Mutation: route either handler through the gate."""
    bridge = _FakeBrowseBridge(entries=_entries(("a.txt", "file", 1)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        harness.call("workspace.listDirectory", {"directory": str(project)})
        harness.call("workspace.readFile", {"path": str(project / "a.txt")})
        cards = [
            f for f in harness.writer.frames if f.get("method") == "permission.requestGrant"
        ]
        assert cards == []
    finally:
        harness.close()


def test_the_trusted_root_is_the_nearest_one(tmp_path, project):
    """``root`` is display-only — the surface renders a path relative to it — and with
    nested roots the useful answer is the NEAREST, not whichever row came back first.

    Mutation: return the first match; the payload then names the outer folder for a file
    the person opened inside the inner one, and every rendered path grows a prefix."""
    inner = project / "packages" / "app"
    inner.mkdir(parents=True)
    harness = _Harness(tmp_path, _FakeBrowseBridge())
    try:
        harness.trust(project)
        harness.trust(inner)
        listed = harness.call("workspace.listDirectory", {"directory": str(inner)})
        assert listed["root"] == str(inner.resolve())
        # ...and a path that only the outer root covers still names the outer one.
        outer_only = harness.call("workspace.listDirectory", {"directory": str(project)})
        assert outer_only["root"] == str(project.resolve())
    finally:
        harness.close()


def test_os_is_used_for_paths_not_string_prefixes():
    """A guard on the module rather than a behaviour: ``escapes`` and the root match must
    go through the shared path predicates (``path_is_within`` / ``is_trusted``), never a
    ``startswith`` on two strings — ``/a/bc`` starts with ``/a/b`` and is not inside it.

    Mutation: implement either with ``str.startswith``."""
    source = (
        Path(__file__).resolve().parent.parent / "agent_core" / "rpc" / "workspace.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
        ):
            pytest.fail("workspace.py must compare paths with path_is_within, not startswith")
    assert "path_is_within" in source and "is_trusted(" in source


def test_the_handlers_are_the_only_camelcase_mapper(tmp_path, project):
    """The module docstring's claim, kept true for the new pair: every camelCase key the
    frontend reads is minted here, and the shell's own payload keys are plain. A second
    mapper is how ``roots``/``folders`` shipped green on both sides.

    Mutation: pass the shell's dict straight through — ``escapes`` disappears, and the
    payload gains whatever the shell happens to call things."""
    bridge = _FakeBrowseBridge(entries=_entries(("a.txt", "file", 1)))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        listed = harness.call("workspace.listDirectory", {"directory": str(project)})
        assert set(listed) == {"directory", "root", "entries", "truncated"}
        assert set(listed["entries"][0]) == {"name", "kind", "size", "escapes"}
        read = harness.call("workspace.readFile", {"path": str(project / "a.txt")})
        assert set(read) == {"path", "root", "content", "bytes", "truncated"}
    finally:
        harness.close()


def test_a_directory_outside_every_root_is_refused_with_nothing_leaked(tmp_path, project):
    """The ``/etc/passwd`` shape, on the browse path. The refusal says what is true and
    names no path back — a refusal that echoed the argument would put whatever the model
    (or a crafted link name) chose into the surface.

    Mutation: interpolate the path into the refusal, or drop the trust check."""
    harness = _Harness(tmp_path, _FakeBrowseBridge())
    try:
        harness.trust(project)
        listed = harness.call("workspace.listDirectory", {"directory": "/etc"})
        assert listed["ok"] is False
        assert "/etc" not in listed["error"]
        read = harness.call("workspace.readFile", {"path": "/etc/passwd"})
        assert read["ok"] is False
        assert "passwd" not in read["error"]
    finally:
        harness.close()


def test_nothing_is_hidden_from_a_listing(tmp_path, project):
    """``.git`` and ``node_modules`` are listed like everything else. Hiding is a lie
    about what is on disk, and telling the truth about what is on disk is this surface's
    only value — rendering them collapsed is the UI's job, and a filter here would take
    that choice away from it.

    Mutation: filter either name in the handler (or in Rust) — the tree then disagrees
    with the person's own file browser about what is in their project."""
    bridge = _FakeBrowseBridge(entries=_entries(
        (".git", "directory", 96), ("node_modules", "directory", 96), ("src", "directory", 96),
    ))
    harness = _Harness(tmp_path, bridge)
    try:
        harness.trust(project)
        listed = harness.call("workspace.listDirectory", {"directory": str(project)})
        assert [e["name"] for e in listed["entries"]] == [".git", "node_modules", "src"]
    finally:
        harness.close()


def test_the_shell_route_exists_for_both_methods():
    """The Rust half of the wiring, pinned from this side too: the method names the
    bridge sends must be the ones ``filesystem.rs`` routes. They are hand-synced strings
    across a process boundary, and a typo on either side is a runtime "unknown method"
    that no unit test on either side alone would catch.

    Mutation: rename either constant on one side only."""
    from agent_core.protocol import Method

    rust = (
        Path(__file__).resolve().parent.parent
        / "shell" / "src-tauri" / "src" / "filesystem.rs"
    ).read_text(encoding="utf-8")
    for method in (
        Method.SHELL_LIST_WORKSPACE_DIRECTORY,
        Method.SHELL_READ_WORKSPACE_FILE_FOR_VIEW,
    ):
        assert f'"{method}" =>' in rust, f"{method} is not routed in filesystem.rs"


def test_the_view_bound_is_tied_to_the_undo_bound_in_the_shell():
    """``VIEW_SIZE_BOUND = UNDO_SIZE_BOUND`` is a property, not a coincidence: any file
    Addison could have EDITED must be a file the viewer can show WHOLE, or the surface
    truncates the diff of an edit it is asking somebody to approve putting back.

    Mutation: give the viewer its own number — the two drift the day the undo bound
    moves, silently, in the direction that hides half of a change."""
    rust = (
        Path(__file__).resolve().parent.parent
        / "shell" / "src-tauri" / "src" / "filesystem.rs"
    ).read_text(encoding="utf-8")
    assert "const VIEW_SIZE_BOUND: usize = UNDO_SIZE_BOUND;" in rust
    assert "const MAX_DIR_ENTRIES: usize = 500;" in rust
