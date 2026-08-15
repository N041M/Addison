"""The narrow taint rule (owner decision 4B, 2026-08-15).

One edge and one edge only: a routine step's resolved arguments carrying text an
EARLIER FILE-READING STEP in the same run produced, into a NETWORK-BOUND step.
That step's permission card gets one extra plain line naming the file.

``agent_core/routines/taint.py`` owns the rule and the honest list of what it does
not catch. These tests hold the edge itself, the places the line must NOT appear
(a non-network step, a network step with none of the text in it, an output too
short to be evidence), the fact that the line reaches the card and nothing else,
and the fact that a run still behaves exactly as it did before.

The last test is source-level, in the ``test_live_model_registration`` idiom: the
network-tool tuple is a hand-maintained list, so something has to fail when a new
tool starts making requests and nobody updates it.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep
from agent_core.routines.taint import NETWORK_BOUND_TOOL_IDS
from agent_core.memory.store import Store
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)
from agent_core.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _REPO_ROOT / "agent_core" / "tools"

# Long enough to be evidence (over MIN_TAINTING_OUTPUT_CHARS), and distinctive.
_FILE_TEXT = "Bank sort code 40-11-58, account 61094422"


class _StubTool:
    """A tool registered under a REAL id (the ids are how taint classifies, so a
    stub has to wear one) with scripted output and no network of its own."""

    def __init__(self, tool_id: str, label: str, output: str = "done"):
        self.definition = ToolDefinition(
            id=tool_id,
            label=label,
            description="Test stand-in.",
            risk_tier=RiskTier.LOW,
            parameters_schema={"type": "object", "properties": {}},
        )
        self._output = output
        self.executed: list[dict] = []

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        self.executed.append(args)
        return ToolResult(success=True, content=self._output)


class _Cards:
    """A permission handler that accepts the preview, like the real frontend one."""

    def __init__(self, answer: PermissionStatus = PermissionStatus.GRANTED):
        self.seen: list[tuple[str, str | None, str | None]] = []
        self._answer = answer

    def __call__(self, tool_id, detail=None, preview=None):
        self.seen.append((tool_id, detail, preview))
        return self._answer

    def previews(self) -> list[str | None]:
        return [preview for _, _, preview in self.seen]

    def preview_for(self, tool_id: str) -> str | None:
        for seen_id, _, preview in self.seen:
            if seen_id == tool_id:
                return preview
        return None


def _run(tmp_path, tools, steps, mode=PolicyMode.SAFE, cards=None):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    cards = cards or _Cards()
    gate = PermissionGate(on_request=cards)
    store = Store(tmp_path / "taint.sqlite3")
    store.insert_routine(
        id="r-1", name="Test", description="", plan_json={},
        created_from_conversation_id=None, created_at=1,
    )
    audit: list[dict] = []
    engine = RoutineEngine(
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=audit.append,
    )
    routine = Routine(id="r-1", name="Test", description="", variables=[], steps=steps)
    result = engine.run(routine, {}, mode=mode)
    return result, cards, audit, gate


def _read_then(second_step: RoutineStep, second_tool) -> tuple[list, list]:
    reader = _StubTool("read_file", "Read files you choose", output=_FILE_TEXT)
    steps = [
        RoutineStep("read", "read_file", {"file_handle": "/Users/mira/Documents/notes.txt"}),
        second_step,
    ]
    return [reader, second_tool], steps


# --- the edge itself ---------------------------------------------------------


def test_file_text_into_a_web_search_names_the_file_on_the_card(tmp_path):
    """The rule, in the shape it exists for."""
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "what is {{read.result}}"}),
        _StubTool("web_search", "Search the web"),
    )
    result, cards, _, _ = _run(tmp_path, tools, steps)

    assert result.status == "completed"
    assert cards.preview_for("web_search") == (
        "This step would send text from the file 'notes.txt' to the web."
    )


def test_the_card_shows_the_basename_not_the_path(tmp_path):
    """A card is a thing people screenshot; the folders around the file are not
    part of the question it asks."""
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "{{read.result}}"}),
        _StubTool("web_search", "Search the web"),
    )
    _, cards, _, _ = _run(tmp_path, tools, steps)

    line = cards.preview_for("web_search") or ""
    assert "notes.txt" in line
    assert "/Users/mira" not in line


def test_the_line_fires_in_developer_mode_too(tmp_path):
    """The card is the control in both profiles, so the step must be carded in
    OPEN as well, where a non-destructive call would otherwise auto-grant."""
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "{{read.result}}"}),
        _StubTool("web_search", "Search the web"),
    )
    _, cards, _, _ = _run(tmp_path, tools, steps, mode=PolicyMode.OPEN)

    assert cards.preview_for("web_search") is not None


# --- the places it must not appear -------------------------------------------


def test_the_same_text_into_a_non_network_step_gets_no_line(tmp_path):
    """save_file writes the very same text to disk. Nothing leaves the machine, so
    there is nothing for the line to warn about."""
    tools, steps = _read_then(
        RoutineStep("save", "save_file", {"content": "{{read.result}}", "name": "copy.txt"}),
        _StubTool("save_file", "Save a file"),
    )
    _, cards, _, _ = _run(tmp_path, tools, steps)

    assert cards.preview_for("save_file") is None
    assert all(preview is None for preview in cards.previews())


def test_a_network_step_carrying_none_of_the_text_gets_no_line(tmp_path):
    """A run that reads a file AND searches the web is not by itself a flow."""
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "tomorrow's weather in Brno"}),
        _StubTool("web_search", "Search the web"),
    )
    _, cards, _, _ = _run(tmp_path, tools, steps)

    assert cards.preview_for("web_search") is None


def test_a_tiny_output_never_taints(tmp_path):
    """"ok" appearing inside a query is not evidence of anything."""
    reader = _StubTool("read_file", "Read files you choose", output="ok")
    steps = [
        RoutineStep("read", "read_file", {"file_handle": "/tmp/notes.txt"}),
        RoutineStep("search", "web_search", {"query": "is it ok to compost citrus"}),
    ]
    _, cards, _, _ = _run(tmp_path, [reader, _StubTool("web_search", "Search the web")], steps)

    assert cards.preview_for("web_search") is None


# --- more than one file ------------------------------------------------------


def test_two_reads_name_the_right_file_for_the_right_step(tmp_path):
    """Each network step is told about the file whose text IT is carrying."""
    first = "Bank sort code 40-11-58, account 61094422"
    second = "Passport number 707341188, issued 2024"
    registry_tools = [
        _StubTool("read_file", "Read files you choose", output=first),
        _StubTool("read_project_file", "Read a project file", output=second),
        _StubTool("web_search", "Search the web"),
    ]
    # Both readers wear real ids, so one stands in for each; the second reads a
    # project file, whose name comes off its arguments the same way.
    steps = [
        RoutineStep("bank", "read_file", {"file_handle": "/Users/mira/bank.txt"}),
        RoutineStep("search_a", "web_search", {"query": "{{bank.result}}"}),
        RoutineStep("passport", "read_project_file", {"path": "/Users/mira/ids/passport.md"}),
        RoutineStep("search_b", "web_search", {"query": "{{passport.result}}"}),
    ]
    _, cards, _, _ = _run(tmp_path, registry_tools, steps)

    web_previews = [preview for tool_id, _, preview in cards.seen if tool_id == "web_search"]
    assert len(web_previews) == 2
    assert "bank.txt" in (web_previews[0] or "")
    assert "passport.md" in (web_previews[1] or "")
    assert "passport.md" not in (web_previews[0] or "")


# --- the line goes to the card and nowhere else ------------------------------


def test_the_line_never_enters_the_audit_row_the_detail_or_a_grant(tmp_path):
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "{{read.result}}"}),
        _StubTool("web_search", "Search the web"),
    )
    _, cards, audit, gate = _run(tmp_path, tools, steps)

    line = cards.preview_for("web_search")
    assert line is not None
    for row in audit:
        assert line not in str(row.get("detail") or "")
        assert line not in str(row)
    for _, detail, _ in cards.seen:
        assert line not in str(detail or "")
    # Carded per invocation, so nothing is remembered: no coarse grant, no
    # session-destructive grant, and therefore nothing anywhere holding the line.
    assert gate.check("web_search") == PermissionStatus.NOT_YET_ASKED


# --- the run still behaves exactly as it did ---------------------------------


def test_an_approved_run_still_completes_normally(tmp_path):
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "{{read.result}}"}),
        _StubTool("web_search", "Search the web", output="three results"),
    )
    result, _, _, _ = _run(tmp_path, tools, steps)

    assert result.status == "completed"
    assert result.answer == "three results"
    assert tools[1].executed == [{"query": _FILE_TEXT}]


def test_a_declined_run_stops_exactly_as_before(tmp_path):
    tools, steps = _read_then(
        RoutineStep("search", "web_search", {"query": "{{read.result}}"}),
        _StubTool("web_search", "Search the web"),
    )
    result, _, _, _ = _run(
        tmp_path, tools, steps, cards=_Cards(PermissionStatus.DENIED)
    )

    assert result.status == "failed"
    assert result.detail == "You declined a permission it needs."
    assert tools[1].executed == []


# --- the tuple is hand-maintained, so something has to guard it --------------


def test_every_tool_that_imports_httpx_is_named_as_network_bound():
    """SOURCE-LEVEL, and for the same reason test_live_model_registration is: the
    network-tool list is written by hand, and the failure mode is silence. A tool
    added tomorrow that makes requests and is not named here would simply never
    raise the line, and every test above would still pass.

    This holds the direction that CAN be held automatically: a module importing
    httpx is unambiguously making requests. The other direction stays a judgement
    call: ``open_link`` is network-bound with no httpx in it (the browser makes the
    request), and ``draft_message`` composes without sending."""
    missing = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not re.search(r"^\s*(import httpx|from httpx import)", source, re.MULTILINE):
            continue
        found = re.search(r'^\s+id="([a-z_]+)",', source, re.MULTILINE)
        assert found is not None, f"{path.name} imports httpx but declares no tool id."
        tool_id = found.group(1)
        if tool_id not in NETWORK_BOUND_TOOL_IDS:
            missing.append(f"{tool_id} ({path.name})")

    assert not missing, (
        "These tools make network requests but are not in NETWORK_BOUND_TOOL_IDS "
        f"(agent_core/routines/taint.py): {', '.join(missing)}. A routine step "
        "using one would send file text to the web with no line on its card. Add "
        "the id to the tuple."
    )
