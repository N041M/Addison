"""Bridge-backed v1 tools (engineering-spec §4.2, §11 step 5).

Every OS effect crosses the ShellBridge (spec §1.3); here that bridge is a fake
that records calls, so nothing touches the real filesystem, clipboard, or browser.
Covers the MEDIUM tools' undo contract (CLAUDE.md invariant 2), open_link's
scheme guard (design-doc §9), the read passthroughs, and that build_registry
still registers the full §4.2 table with its undo check intact.
"""

import pytest

from agent_core.main import build_registry
from agent_core.tools.base import ActionSnapshot, ExecutionContext, RiskTier
from agent_core.tools.draft_message import DraftMessageTool
from agent_core.tools.open_link import OpenLinkTool
from agent_core.tools.read_clipboard import ReadClipboardTool
from agent_core.tools.read_file import ReadFileTool
from agent_core.tools.save_file import SaveFileTool

_V1_TABLE = {
    "web_search": RiskTier.LOW,
    "read_web_page": RiskTier.LOW,   # read-only, so LOW and no undo (invariant 2)
    "read_file": RiskTier.LOW,
    "read_clipboard": RiskTier.LOW,
    "calculator": RiskTier.LOW,
    "save_file": RiskTier.MEDIUM,
    "draft_message": RiskTier.MEDIUM,
    "open_link": RiskTier.LOW,
}


class FakeShellBridge:
    """Records what each method was called with; never does a real OS effect."""

    def __init__(self) -> None:
        self.saved: list[tuple] = []
        self.deleted: list[str] = []
        self.restored: list[tuple] = []
        self.drafts: list[tuple] = []
        self.discarded: list[str] = []
        self.opened: list[str] = []
        self.clipboard_text = "pasted invoice text"
        self.scoped: dict[str, dict] = {}

    def save_new_file(self, filename: str, content: str) -> str:
        path = f"/Users/test/Desktop/{filename}"
        self.saved.append((filename, content, path))
        return path

    def delete_file(self, path: str) -> None:
        self.deleted.append(path)

    def restore_file(self, path: str, content: str) -> None:
        self.restored.append((path, content))

    def open_draft(self, to: str, subject: str, body: str) -> str:
        ref = f"draft-{len(self.drafts)}"
        self.drafts.append((to, subject, body, ref))
        return ref

    def discard_draft(self, draft_ref: str) -> None:
        self.discarded.append(draft_ref)

    def read_clipboard(self) -> str:
        return self.clipboard_text

    def open_external(self, url: str) -> None:
        self.opened.append(url)

    def read_scoped_file(self, file_handle: str) -> dict:
        return self.scoped.get(file_handle, {"content": "hello world", "kind": "text"})


def _ctx(bridge=None) -> ExecutionContext:
    return ExecutionContext(conversation_id="t", shell_bridge=bridge)


# --- save_file --------------------------------------------------------------


def test_save_file_roundtrip_and_undo():
    bridge = FakeShellBridge()
    tool = SaveFileTool(shell_bridge=bridge)

    result = tool.execute({"filename": "notes.txt", "content": "hi"}, _ctx(bridge))

    assert result.success is True
    assert bridge.saved == [("notes.txt", "hi", "/Users/test/Desktop/notes.txt")]
    assert result.content == "/Users/test/Desktop/notes.txt"
    # The final path lands in the undo payload for the UndoManager to replay.
    assert result.snapshot is not None
    assert result.snapshot.undo_payload["created_file"] == "/Users/test/Desktop/notes.txt"

    tool.undo(result.snapshot)
    assert bridge.deleted == ["/Users/test/Desktop/notes.txt"]


def test_save_file_undo_without_bridge_raises_plain_runtimeerror():
    snapshot = ActionSnapshot(
        id="s1",
        tool_call_id="c1",
        tool_id="save_file",
        undo_payload={"created_file": "/Users/test/Desktop/notes.txt"},
        created_at=0,
    )
    with pytest.raises(RuntimeError) as excinfo:
        SaveFileTool().undo(snapshot)  # no bridge configured
    assert "desktop shell" in str(excinfo.value)
    # Plain language: no stack-trace jargon, no NotImplementedError.
    assert not isinstance(excinfo.value, NotImplementedError)


def test_save_file_redo_restores_the_exact_file():
    bridge = FakeShellBridge()
    tool = SaveFileTool(shell_bridge=bridge)
    result = tool.execute({"filename": "notes.txt", "content": "hi"}, _ctx(bridge))

    assert result.snapshot is not None
    tool.undo(result.snapshot)
    tool.redo(result.snapshot)

    # Redo re-creates exactly what undo removed — same path, same content.
    assert bridge.restored == [("/Users/test/Desktop/notes.txt", "hi")]


def test_save_file_redo_without_content_payload_raises_plain_language():
    # A snapshot from before redo existed carries no content — refuse plainly.
    snapshot = ActionSnapshot(
        id="s1",
        tool_call_id="c1",
        tool_id="save_file",
        undo_payload={"created_file": "/Users/test/Desktop/notes.txt"},
        created_at=0,
    )
    with pytest.raises(RuntimeError) as excinfo:
        SaveFileTool(shell_bridge=FakeShellBridge()).redo(snapshot)
    assert "no longer has" in str(excinfo.value)


def test_save_file_without_bridge_is_graceful_in_cli_mode():
    result = SaveFileTool().execute({"filename": "x", "content": "y"}, _ctx(None))
    assert result.success is False
    assert "desktop shell" in result.content


# --- draft_message ----------------------------------------------------------


def test_draft_message_roundtrip_and_undo():
    bridge = FakeShellBridge()
    tool = DraftMessageTool(shell_bridge=bridge)

    result = tool.execute(
        {"to": "a@b.com", "subject": "Hi", "body": "the body"}, _ctx(bridge)
    )

    assert result.success is True
    assert bridge.drafts == [("a@b.com", "Hi", "the body", "draft-0")]
    assert result.snapshot is not None
    assert result.snapshot.undo_payload["draft_ref"] == "draft-0"

    tool.undo(result.snapshot)
    assert bridge.discarded == ["draft-0"]


def test_draft_message_optional_fields_default_empty():
    bridge = FakeShellBridge()
    result = DraftMessageTool(shell_bridge=bridge).execute({"body": "just a body"}, _ctx(bridge))
    assert result.success is True
    assert bridge.drafts == [("", "", "just a body", "draft-0")]


def test_draft_message_undo_without_bridge_raises_plain_runtimeerror():
    snapshot = ActionSnapshot(
        id="s2",
        tool_call_id="c2",
        tool_id="draft_message",
        undo_payload={"draft_ref": "draft-0"},
        created_at=0,
    )
    with pytest.raises(RuntimeError) as excinfo:
        DraftMessageTool().undo(snapshot)
    assert "desktop shell" in str(excinfo.value)


# --- open_link --------------------------------------------------------------


@pytest.mark.parametrize("bad_url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x/y", "notaurl"])
def test_open_link_rejects_non_web_schemes_without_touching_bridge(bad_url):
    bridge = FakeShellBridge()
    result = OpenLinkTool().execute({"url": bad_url}, _ctx(bridge))
    assert result.success is False
    assert "http" in result.content  # plain guidance mentions http(s)
    assert bridge.opened == []  # bridge never touched for a rejected scheme


@pytest.mark.parametrize("good_url", ["https://example.com/page", "http://example.org"])
def test_open_link_opens_web_urls(good_url):
    bridge = FakeShellBridge()
    result = OpenLinkTool().execute({"url": good_url}, _ctx(bridge))
    assert result.success is True
    assert bridge.opened == [good_url]


def test_open_link_without_bridge_is_graceful():
    result = OpenLinkTool().execute({"url": "https://example.com"}, _ctx(None))
    assert result.success is False
    assert "desktop shell" in result.content


# --- read passthroughs ------------------------------------------------------


def test_read_clipboard_passthrough():
    bridge = FakeShellBridge()
    result = ReadClipboardTool().execute({}, _ctx(bridge))
    assert result.success is True
    assert result.content == "pasted invoice text"
    assert result.snapshot is None  # read-only, nothing to undo


def test_read_clipboard_without_bridge_is_graceful():
    result = ReadClipboardTool().execute({}, _ctx(None))
    assert result.success is False
    assert "desktop shell" in result.content


def test_read_file_passthrough_returns_extracted_dict():
    bridge = FakeShellBridge()
    bridge.scoped["h1"] = {"content": "PDF text here", "kind": "text"}
    result = ReadFileTool().execute({"file_handle": "h1"}, _ctx(bridge))
    assert result.success is True
    assert result.content == {"content": "PDF text here", "kind": "text"}
    assert result.snapshot is None


def test_read_file_without_bridge_is_graceful():
    result = ReadFileTool().execute({"file_handle": "h1"}, _ctx(None))
    assert result.success is False
    assert "desktop shell" in result.content


# --- registry wiring --------------------------------------------------------


def test_build_registry_registers_full_v1_table_with_bridge():
    bridge = FakeShellBridge()
    registry = build_registry(shell_bridge=bridge)

    ids = {d.id for d in registry.list_for_model()}
    assert ids == set(_V1_TABLE)
    for tool_id, tier in _V1_TABLE.items():
        assert registry.get(tool_id).definition.risk_tier is tier


def test_build_registry_threads_bridge_into_medium_tool_undo():
    # The MEDIUM tools got past the registry's undo check AND their undo() reaches
    # the injected bridge (build_registry threaded it through the constructor).
    bridge = FakeShellBridge()
    registry = build_registry(shell_bridge=bridge)

    save_tool = registry.get("save_file")
    assert isinstance(save_tool, SaveFileTool)
    result = save_tool.execute({"filename": "r.txt", "content": "z"}, _ctx(bridge))
    assert result.snapshot is not None
    save_tool.undo(result.snapshot)
    assert bridge.deleted == ["/Users/test/Desktop/r.txt"]
