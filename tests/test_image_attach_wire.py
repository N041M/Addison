"""Image attach phase 3: the wire, the send, and what is remembered.

``tests/test_image_attach.py`` owns the block path (what each adapter says about a
picture) and ``shell/src-tauri/src/filesystem.rs`` owns the decode. This file owns
everything in between: how a picture gets from the person's picker into a message,
what refuses it, what is spent, and what a reopened chat still has.

The properties worth pinning here are mostly about what does NOT happen — nothing is
written by a refused send, nothing is spent by one, nothing model-addressed can mint
an attachment, and nothing carrying pictures is quietly dropped on the way to disk or
back off it. House style of tests/test_ipc_server.py and tests/test_routine_import.py:
the real server on fake pipes, with a fake shell answering the picker.
"""

from __future__ import annotations

import base64
import sqlite3

from agent_core.memory.store import Store
from agent_core.models_catalog import PROVIDER_VISION, CloudModel
from agent_core.protocol import Method
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import ModelResponse, ProviderCapabilities
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.setup_assistant_provider import SetupAssistantProvider
from agent_core.rpc.conversation import (
    _PICTURE_GONE,
    _PICTURE_KIND_REFUSED,
    _TOO_MANY_PICTURES,
    MAX_ATTACHMENTS_PER_MESSAGE,
)
from tests.conftest import (
    IPC_DB_NAME,
    ShellBridgeStubs,
    _ScriptedProvider,
    _shutdown,
    build_server,
)

_PIXELS = base64.b64encode(b"not really a png, and nothing here decodes it").decode()
_CANCELLED = "You closed the picker without choosing."


class _PictureBridge(ShellBridgeStubs):
    """The shell's half of the picker: one picture per pick, already encoded.

    ``media_type`` is what the shell CLAIMS the encoded bytes are. The real shell
    can only produce one of the closed four (decoding is the validation, phase 2),
    so setting it to anything else here is the test standing in for a shell bug —
    the one case ``pickAttachment`` exists to answer in a sentence.
    """

    def __init__(self, media_type: str = "image/png", cancel: bool = False) -> None:
        self.media_type = media_type
        self.cancel = cancel
        self.picks = 0
        self.reads = 0

    def pick_image(self) -> dict:
        if self.cancel:
            raise RuntimeError(_CANCELLED)
        self.picks += 1
        return {
            "fileHandle": f"handle-{self.picks}",
            "name": f"photo-{self.picks}.png",
            "byteSize": 4096,
        }

    def read_picked_image(self, file_handle: str) -> dict:
        self.reads += 1
        return {
            "content": _PIXELS,
            "mediaType": self.media_type,
            "name": f"{file_handle}.png",
            "byteSize": 4096,
            "width": 800,
            "height": 600,
        }


class _SeeingProvider(_ScriptedProvider):
    """A scripted provider that can look at pictures.

    The conftest double reports ``vision=False`` (the dataclass default), which
    phase 1's turn gate correctly refuses — so a send test using it would measure
    the gate instead of the wire. Every happy path below therefore says out loud
    that the model can see."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
            vision=True,
        )


def _server(tmp_path, *, media_type: str = "image/png", cancel: bool = False, replies: int = 4):
    bridge = _PictureBridge(media_type=media_type, cancel=cancel)
    provider = _SeeingProvider([ModelResponse(text="I see it.", tool_calls=[]) for _ in range(replies)])
    harness = build_server(tmp_path, provider=provider, bridge=bridge)  # type: ignore[arg-type]
    return harness, bridge


def _seeing(harness) -> _SeeingProvider:
    """The harness's provider is always the seeing double here (the narrowing
    device tests/test_ipc_server.py uses for the scripted one)."""
    provider = harness.provider
    assert isinstance(provider, _SeeingProvider)
    return provider


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """Drive one request and return its RESULT, asserting it was not an error."""
    frame = _frame(harness, method, params, request_id)
    assert "result" in frame, f"{method} answered an error: {frame.get('error')}"
    return frame["result"]


def _error(harness, method: str, params: dict | None = None, request_id: int = 1) -> str:
    """Drive one request and return the plain sentence it was refused with."""
    frame = _frame(harness, method, params, request_id)
    assert "error" in frame, f"{method} was expected to refuse, and answered {frame.get('result')}"
    return frame["error"]["message"]


def _frame(harness, method: str, params: dict | None, request_id: int) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id)


def _pick(harness, request_id: int = 1) -> str:
    return _call(harness, Method.CONVERSATION_PICK_ATTACHMENT, request_id=request_id)["attachmentId"]


def _rows(tmp_path, table: str) -> list[dict]:
    """Read a table with the test thread's own connection (the server's Store
    belongs to its worker) — tests/test_snapshot_hooks.py's device."""
    conn = sqlite3.connect(tmp_path / IPC_DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


# --- pickAttachment ---------------------------------------------------------


def test_a_pick_answers_with_a_preview_and_holds_the_bytes(tmp_path):
    """The happy path, both halves: the webview gets everything a chip needs to
    draw itself, and the CORE keeps the pixels — the reply is for display, and the
    send names the id."""
    h, bridge = _server(tmp_path)
    try:
        result = _call(h, Method.CONVERSATION_PICK_ATTACHMENT)
        assert result["mediaType"] == "image/png"
        assert result["dataB64"] == _PIXELS
        assert result["byteSize"] == 4096
        assert result["name"]
        # Read ONCE, at pick: one dialog, one read, and nothing goes back for the
        # file again between here and the send.
        assert (bridge.picks, bridge.reads) == (1, 1)
        held = h.server._pending_attachments
        assert list(held) == [result["attachmentId"]]
        assert held[result["attachmentId"]]["data_b64"] == _PIXELS
        # The id is the CORE's, never the shell's handle: the layers stay layered,
        # and a file-scoped capability never reaches the webview.
        assert result["attachmentId"] != "handle-1"
    finally:
        _shutdown(h.reader, h.thread)


def test_a_media_type_outside_the_closed_four_is_refused_plainly(tmp_path):
    """The second reader of ALLOWED_IMAGE_MEDIA_TYPES (providers/base.py names this
    call as the one place a violation can still be answered in a sentence). Delete
    the check and a BMP is cached and offered to adapters that would refuse it."""
    h, _ = _server(tmp_path, media_type="image/bmp")
    try:
        assert _error(h, Method.CONVERSATION_PICK_ATTACHMENT) == _PICTURE_KIND_REFUSED
        assert h.server._pending_attachments == {}
    finally:
        _shutdown(h.reader, h.thread)


def test_a_fifth_pending_picture_is_refused_before_the_picker_opens(tmp_path):
    """The cap bounds MEMORY, not just the message: what is held is decoded bytes.
    It is checked before the dialog, so nobody is sent to find a file there was
    never room for."""
    h, bridge = _server(tmp_path)
    try:
        for n in range(MAX_ATTACHMENTS_PER_MESSAGE):
            _pick(h, request_id=n + 1)
        assert _error(h, Method.CONVERSATION_PICK_ATTACHMENT, request_id=99) == _TOO_MANY_PICTURES
        assert bridge.picks == MAX_ATTACHMENTS_PER_MESSAGE
        assert len(h.server._pending_attachments) == MAX_ATTACHMENTS_PER_MESSAGE
    finally:
        _shutdown(h.reader, h.thread)


def test_discarding_a_pending_picture_frees_its_slot(tmp_path):
    h, _ = _server(tmp_path)
    try:
        ids = [_pick(h, request_id=n + 1) for n in range(MAX_ATTACHMENTS_PER_MESSAGE)]
        assert _call(
            h, Method.CONVERSATION_DISCARD_ATTACHMENT, {"attachmentId": ids[0]}, request_id=10
        ) == {"ok": True}
        assert ids[0] not in h.server._pending_attachments
        # The freed slot is real: the pick that was refused a moment ago now works.
        _pick(h, request_id=11)
        assert len(h.server._pending_attachments) == MAX_ATTACHMENTS_PER_MESSAGE
    finally:
        _shutdown(h.reader, h.thread)


def test_discarding_an_id_nobody_is_holding_says_nothing(tmp_path):
    """There is nothing to say about a thing that is already gone — and a person
    who clicks ✕ twice has caused no error."""
    h, _ = _server(tmp_path)
    try:
        kept = _pick(h)
        assert _call(
            h, Method.CONVERSATION_DISCARD_ATTACHMENT, {"attachmentId": "never-existed"},
            request_id=2,
        ) == {"ok": True}
        assert list(h.server._pending_attachments) == [kept]
    finally:
        _shutdown(h.reader, h.thread)


def test_a_new_chat_drops_every_pending_picture(tmp_path):
    """Pending pictures belong to the message being composed, and a new chat means
    that message is gone. Without this they are slots nobody can see, use or free."""
    h, _ = _server(tmp_path)
    try:
        _pick(h)
        _call(h, Method.CONVERSATION_NEW, request_id=2)
        assert h.server._pending_attachments == {}
    finally:
        _shutdown(h.reader, h.thread)


def test_closing_the_picker_passes_the_shell_sentence_through(tmp_path):
    h, _ = _server(tmp_path, cancel=True)
    try:
        assert _error(h, Method.CONVERSATION_PICK_ATTACHMENT) == _CANCELLED
        assert h.server._pending_attachments == {}
    finally:
        _shutdown(h.reader, h.thread)


# --- sendMessage ------------------------------------------------------------


def test_an_id_nobody_is_holding_refuses_before_anything_is_written(tmp_path):
    """The empty-text guard's own argument, applied to pictures: a send that cannot
    happen must leave NO conversation row and NO message row, because neither is
    removed by a rollback or by the failed-turn cleanup."""
    h, _ = _server(tmp_path)
    try:
        refusal = _error(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "look", "attachments": ["nope"]}
        )
        assert refusal == _PICTURE_GONE
        assert _rows(tmp_path, "conversations") == []
        assert _rows(tmp_path, "messages") == []
        assert _rows(tmp_path, "message_attachments") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_a_picture_with_no_words_is_an_ordinary_message(tmp_path):
    """The empty-text guard's ONE relaxation. The message goes as "" plus pictures —
    a person sending just a photo — and the pictures reach the model as pictures."""
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        result = _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "   ", "attachments": [attachment_id]},
            request_id=2,
        )
        assert result["ok"] is True

        history = _seeing(h).histories[0]
        user = [m for m in history if m.role == "user"][-1]
        assert user.content == ""      # the whitespace, not the spaces themselves
        assert len(user.images) == 1
        assert user.images[0].media_type == "image/png"
        assert user.images[0].data_b64 == _PIXELS

        # Spent by the send that named it.
        assert h.server._pending_attachments == {}
    finally:
        _shutdown(h.reader, h.thread)


def test_empty_text_with_nothing_attached_still_refuses(tmp_path):
    """The relaxation is ONE case wide. Without this, "relaxed" quietly becomes
    "removed" and blank rows come back."""
    h, _ = _server(tmp_path)
    try:
        assert "nothing to send" in _error(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "  "})
        # And a non-string text is still refused, pictures or no pictures.
        attachment_id = _pick(h, request_id=2)
        assert "nothing to send" in _error(
            h, Method.CONVERSATION_SEND_MESSAGE,
            {"text": None, "attachments": [attachment_id]}, request_id=3,
        )
        assert _rows(tmp_path, "messages") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_more_than_four_pictures_on_one_message_is_refused(tmp_path):
    h, _ = _server(tmp_path)
    try:
        ids = [_pick(h, request_id=n + 1) for n in range(MAX_ATTACHMENTS_PER_MESSAGE)]
        refusal = _error(
            h, Method.CONVERSATION_SEND_MESSAGE,
            {"text": "these five", "attachments": ids + ["one-more"]}, request_id=10,
        )
        assert refusal == _TOO_MANY_PICTURES
        # Refused whole: the four it WAS holding are untouched and still sendable.
        assert len(h.server._pending_attachments) == MAX_ATTACHMENTS_PER_MESSAGE
    finally:
        _shutdown(h.reader, h.thread)


def test_an_id_is_spent_by_the_send_that_names_it(tmp_path):
    """Sending the same id twice would attach the same picture to two messages off
    one pick — and, once persisted, two rows with one primary key."""
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "one", "attachments": [attachment_id]},
            request_id=2,
        )
        assert _error(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "again", "attachments": [attachment_id]},
            request_id=3,
        ) == _PICTURE_GONE
        # The same id twice in ONE send is the same thing said in one breath.
        second = _pick(h, request_id=4)
        assert _error(
            h, Method.CONVERSATION_SEND_MESSAGE,
            {"text": "twice", "attachments": [second, second]}, request_id=5,
        ) == _PICTURE_GONE
        assert len(_rows(tmp_path, "message_attachments")) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_attachment_rows_land_beside_the_message_row(tmp_path):
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        result = _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "what is this?", "attachments": [attachment_id]},
            request_id=2,
        )
        rows = _rows(tmp_path, "message_attachments")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == attachment_id           # the id the composer already knows
        assert row["message_id"] == result["userMessageId"]
        assert row["media_type"] == "image/png"
        assert row["data_b64"] == _PIXELS
        assert row["byte_size"] == 4096
        assert row["name"]
        assert row["conversation_id"] == h.server.conversation.id
    finally:
        _shutdown(h.reader, h.thread)


def test_a_picture_only_first_message_does_not_title_the_chat(tmp_path):
    """``_auto_title`` answers None for a message with no words, and the flag must
    stay DOWN when it does — otherwise the first turn with words in it would find
    the chat already 'titled' and it would stay Untitled forever."""
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "", "attachments": [attachment_id]},
            request_id=2,
        )
        assert h.server._conversation_titled is False
        _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "what is in that photo?"}, request_id=3)
        assert h.server._conversation_titled is True
        rows = _call(h, Method.CONVERSATION_LIST, request_id=4)["conversations"]
        assert rows[0]["title"] == "what is in that photo?"
    finally:
        _shutdown(h.reader, h.thread)


# --- conversation.load ------------------------------------------------------


def test_a_reopened_chat_carries_its_pictures_to_the_thread_and_to_the_model(tmp_path):
    """Both halves, and the second is the one that fails silently: the wire half
    redraws a thumbnail, the HISTORY half is what the model can still see. Without
    it a reopened chat replays the words of a message whose picture has vanished,
    and the model answers confidently about something it never received."""
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "and this one", "attachments": [attachment_id]},
            request_id=2,
        )
        conversation_id = h.server.conversation.id
        _call(h, Method.CONVERSATION_NEW, request_id=3)

        loaded = _call(
            h, Method.CONVERSATION_LOAD, {"conversationId": conversation_id}, request_id=4
        )
        user_rows = [m for m in loaded["messages"] if m["role"] == "user"]
        assert user_rows[0]["attachments"] == [
            {
                "id": attachment_id,
                "name": user_rows[0]["attachments"][0]["name"],
                "mediaType": "image/png",
                "dataB64": _PIXELS,
            }
        ]
        # An assistant row has none, and says so by not carrying the key at all.
        assert all("attachments" not in m for m in loaded["messages"] if m["role"] != "user")

        rebuilt = [m for m in h.server.conversation.messages if m.role == "user"][0]
        assert len(rebuilt.images) == 1
        assert rebuilt.images[0].media_type == "image/png"
        assert rebuilt.images[0].data_b64 == _PIXELS
    finally:
        _shutdown(h.reader, h.thread)


def test_a_rewind_takes_a_message_and_its_pictures_together(tmp_path):
    """Deleting a message row while its pictures point at it aborts the rewind at
    COMMIT (``PRAGMA foreign_keys = ON``), so this is not tidiness — it is whether
    a person can rewind a chat they attached anything to at all."""
    h, _ = _server(tmp_path)
    try:
        attachment_id = _pick(h)
        first = _call(
            h, Method.CONVERSATION_SEND_MESSAGE, {"text": "one", "attachments": [attachment_id]},
            request_id=2,
        )
        _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "two"}, request_id=3)
        store = Store(tmp_path / IPC_DB_NAME)
        try:
            store.truncate_messages(
                h.server.conversation.id, first["userMessageId"], keep_anchor=False
            )
        finally:
            store._conn.close()
        assert _rows(tmp_path, "message_attachments") == []
        assert _rows(tmp_path, "messages") == []
    finally:
        _shutdown(h.reader, h.thread)


# --- the vision flag on model.availableRoles --------------------------------


def test_cloud_rows_carry_vision_and_local_models_carry_no_claim(tmp_path):
    catalog = [
        CloudModel(id="claude-x", label="Claude X", description="", provider="anthropic"),
        CloudModel(id="gemini-x", label="Gemini X", description="", provider="google"),
    ]
    h, _ = _server(tmp_path)
    try:
        h.server._cloud_catalog = catalog
        roles = _call(h, Method.MODEL_AVAILABLE_ROLES)
        assert [m["vision"] for m in roles["cloudModels"]] == [True, True]
        # Local models are plain ids and never claim anything: Ollama's answer is per
        # model and this path does not fetch it. Absent means unknown.
        assert all(isinstance(m, str) for m in roles["localModels"])
        # The Setup Assistant relay is not offered as a model at all, so it never
        # gets to claim a capability it does not have (it reports vision=False, and
        # phase 1's gate is what enforces that).
        assert "setup_assistant" not in roles["roles"]
        relay = SetupAssistantProvider(shell_bridge=None, relay_url="https://example.invalid")
        assert relay.capabilities().vision is False
    finally:
        _shutdown(h.reader, h.thread)


def test_a_provider_this_build_has_never_heard_of_claims_nothing():
    """Absent is UNKNOWN, and the composer only speaks when it knows the answer is
    no. A default of False here would print "can't look at pictures" under a model
    that can."""
    wire = CloudModel(id="m", label="M", description="", provider="mystery").to_wire()
    assert "vision" not in wire


def test_provider_vision_matches_the_adapters():
    """PROVIDER_VISION is a COPY of four ``capabilities()`` lines, so this asks the
    adapters themselves. Flip any one of them and the picker would start lying about
    what the model on the other end can see."""
    live = {
        "anthropic": AnthropicProvider().capabilities().vision,
        "openai": OpenAIProvider(model="gpt-x").capabilities().vision,
        "google": GoogleProvider(model="gemini-x").capabilities().vision,
        # The custom OpenAI-compatible server IS the OpenAI adapter, pointed
        # somewhere else and allowed to run without a key.
        "custom": OpenAIProvider(
            model="whatever", base_url="http://localhost:1234", require_key=False,
            service_label="Your own server",
        ).capabilities().vision,
    }
    assert PROVIDER_VISION == live
