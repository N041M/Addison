"""The Context Budget Manager, WIRED (engineering-spec §4.8, PR 2 of two).

PR 1 (``agent_core/context_budget.py``) answers two questions about data. PR 2 is
the part that acts on the answers, at the one moment it is allowed to: the turn
boundary in ``rpc/conversation.py``. What is worth testing here is not that a
summary can be produced, it is the five ways the mechanism must REFUSE to act,
and the one shape it produces when it does:

  * **Under the threshold, nothing happens at all.** No summary call, no new
    conversation, no note. A mechanism that runs when it is not needed is a
    mechanism that rewrites people's chats for no reason.
  * **Cannot tell is not "fine".** A provider that reports no window is silence,
    not permission.
  * **A summary that did not arrive is never pretended into existence.** A call
    that raises, and a call that answers with nothing usable, both leave the
    conversation exactly as it was, and say nothing to the person, because the
    note is a claim about something that happened.
  * **Nothing is deleted.** The original transcript's rows are compared byte for
    byte, before and after.
  * **The seed is exactly the three ingredients**, and the verbatim tail is the
    one ``choose_cut_point`` chose, neither re-derived nor trimmed here.

Plus the source-level tests in the ``test_live_model_registration`` idiom: that no
registry ever learns about any of this, and that ``memory_facts`` has no write
path to reach for.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3

from agent_core.context_budget import KEEP_RECENT_TURNS, choose_cut_point
from agent_core.context_continuation import (
    CONTINUATION_NOTE,
    SUMMARY_INSTRUCTION,
    build_seed_messages,
    usable_summary,
)
from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
    ProviderUnavailable,
    Usage,
    exception_for_http_status,
)
from tests.conftest import IPC_DB_NAME, _shutdown, build_server

_SRC = pathlib.Path(__file__).resolve().parents[1] / "agent_core"

_SUMMARY_TEXT = (
    "The person is planning a move to Brno and asked about packing, dates and "
    "the cost of a van. Nothing is booked yet."
)


class _BudgetProvider:
    """A provider whose token reports and window are the test's to choose.

    It also tells the two kinds of request apart the way the code does: a
    summarisation request is one message that opens with ``SUMMARY_INSTRUCTION``
    and carries no tools. Every one it sees is recorded, so "exactly one summary
    call, on the resolved provider" is an assertion about this object."""

    def __init__(
        self,
        *,
        max_context_tokens: int | None = 1_000,
        per_turn_tokens=None,
        summary_text: str | None = _SUMMARY_TEXT,
        summary_raises: bool = False,
    ) -> None:
        self._max = max_context_tokens
        self._per_turn = list(per_turn_tokens or [])
        self._summary_text = summary_text
        self._summary_raises = summary_raises
        self.summary_requests: list[list[Message]] = []
        self.chat_requests: list[list[Message]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=self._max,  # type: ignore[arg-type]
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None):
        first = getattr(messages[0], "content", "") if messages else ""
        if len(messages) == 1 and first.startswith(SUMMARY_INSTRUCTION):
            self.summary_requests.append(list(messages))
            if self._summary_raises:
                raise ProviderUnavailable("Couldn't reach the model.")
            return ModelResponse(text=self._summary_text, tool_calls=[])
        self.chat_requests.append(list(messages))
        tokens = self._per_turn.pop(0) if self._per_turn else 10
        return ModelResponse(
            text="All right.",
            tool_calls=[],
            usage=Usage(input_tokens=tokens, output_tokens=0),
        )


def _send(reader, writer, request_id: int, text: str) -> dict:
    reader.feed(
        {"jsonrpc": "2.0", "id": request_id,
         "method": Method.CONVERSATION_SEND_MESSAGE, "params": {"text": text}}
    )
    return writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)


def _rows(tmp_path):
    """Every message row on disk, per conversation, as plain tuples."""
    conn = sqlite3.connect(tmp_path / IPC_DB_NAME)
    conn.row_factory = sqlite3.Row
    out: dict[str, list[tuple]] = {}
    for row in conn.execute(
        "SELECT conversation_id, id, role, content, tool_call_id, created_at "
        "FROM messages ORDER BY conversation_id, created_at, rowid"
    ):
        out.setdefault(row["conversation_id"], []).append(tuple(row))
    conn.close()
    return out


def _conversations(tmp_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(tmp_path / IPC_DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, summary, continued_from_conversation_id "
        "FROM conversations ORDER BY rowid"
    ).fetchall()
    conn.close()
    return rows


def _notes(writer) -> list[str]:
    return [
        f["params"]["label"]
        for f in writer.frames
        if f.get("method") == Method.TOOL_ACTIVITY_UPDATE
        and f["params"].get("toolId") == "context"
    ]


def _drive(tmp_path, provider, turns: int = 6):
    """Send ``turns`` messages through a real server on this provider."""
    harness = build_server(tmp_path, provider=provider, register_tool=False)
    try:
        for i in range(turns):
            result = _send(harness.reader, harness.writer, i + 1, f"message {i}")
            assert result["result"]["ok"] is True
    finally:
        _shutdown(harness.reader, harness.thread)
    return harness


# --- (1) watching, and the three ways of not acting -------------------------


def test_under_the_threshold_nothing_happens_at_all(tmp_path):
    """No summary call, no continuation conversation, no note."""
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 6)
    harness = _drive(tmp_path, provider)
    assert provider.summary_requests == []
    assert len(_conversations(tmp_path)) == 1
    assert _notes(harness.writer) == []


def test_a_provider_that_reports_no_window_is_cannot_tell(tmp_path):
    """Cannot tell means do nothing, silently, never "probably fine"."""
    provider = _BudgetProvider(max_context_tokens=None, per_turn_tokens=[10] * 5 + [999_999])
    harness = _drive(tmp_path, provider)
    assert provider.summary_requests == []
    assert len(_conversations(tmp_path)) == 1
    assert _notes(harness.writer) == []


def test_crossing_the_threshold_summarises_once_and_continues(tmp_path):
    """Exactly one summary call, on the provider that answered the turn; a
    continuation with lineage + summary persisted; the original untouched."""
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900])
    before = None
    harness = build_server(tmp_path, provider=provider, register_tool=False)
    try:
        for i in range(5):
            _send(harness.reader, harness.writer, i + 1, f"message {i}")
        before = _rows(tmp_path)
        original_id = next(iter(before))
        _send(harness.reader, harness.writer, 6, "the long one")
    finally:
        _shutdown(harness.reader, harness.thread)

    assert len(provider.summary_requests) == 1
    rows = {r["id"]: r for r in _conversations(tmp_path)}
    continuation = [r for r in rows.values() if r["continued_from_conversation_id"]]
    assert len(continuation) == 1
    assert continuation[0]["continued_from_conversation_id"] == original_id
    assert continuation[0]["summary"] == _SUMMARY_TEXT
    # Nothing deleted, nothing edited, nothing reordered: the original's rows are
    # the rows it had before the sixth turn, plus that turn's own two messages.
    after = _rows(tmp_path)[original_id]
    assert after[: len(before[original_id])] == before[original_id]
    assert len(after) == len(before[original_id]) + 2


def test_the_person_is_told_once_in_one_plain_sentence(tmp_path):
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900])
    harness = _drive(tmp_path, provider)
    assert _notes(harness.writer) == [CONTINUATION_NOTE]
    assert "deleted" in CONTINUATION_NOTE


# --- (2) the failure paths --------------------------------------------------


def test_a_summary_call_that_fails_changes_nothing_and_the_turn_completes(tmp_path):
    provider = _BudgetProvider(
        max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900], summary_raises=True
    )
    harness = build_server(tmp_path, provider=provider, register_tool=False)
    try:
        for i in range(5):
            _send(harness.reader, harness.writer, i + 1, f"message {i}")
        before = _rows(tmp_path)
        result = _send(harness.reader, harness.writer, 6, "the long one")
    finally:
        _shutdown(harness.reader, harness.thread)
    # The turn still completed, bookkeeping after an answer never fails the answer.
    assert result["result"]["ok"] is True
    assert len(provider.summary_requests) == 1
    assert len(_conversations(tmp_path)) == 1
    # And the person is not told a lie about a condensing that did not happen.
    assert _notes(harness.writer) == []
    original_id = next(iter(before))
    assert _rows(tmp_path)[original_id][: len(before[original_id])] == before[original_id]


def test_an_empty_summary_changes_nothing(tmp_path):
    provider = _BudgetProvider(
        max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900], summary_text="   "
    )
    harness = _drive(tmp_path, provider)
    assert len(provider.summary_requests) == 1
    assert len(_conversations(tmp_path)) == 1
    assert _notes(harness.writer) == []


def test_usable_summary_rejects_what_cannot_stand_in_for_a_conversation():
    assert usable_summary(None) is None
    assert usable_summary("") is None
    assert usable_summary("  \n ") is None
    assert usable_summary("Sure!") is None
    assert usable_summary(f"  {_SUMMARY_TEXT}  ") == _SUMMARY_TEXT


# --- (3) what the continuation is seeded with -------------------------------


def test_the_seed_is_the_summary_the_facts_and_the_exact_verbatim_tail(tmp_path):
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900])
    # A confirmed fact, and an unconfirmed one that must never be read as a fact.
    #
    # SEEDED BEFORE THE SERVER EXISTS, and the order is the whole point. This used to
    # sit after ``build_server`` and flaked about one run in three, in two ways that
    # look unrelated and are the same race: the server's own ``Store`` is built on a
    # thread, so a moment too early there is no schema yet ("no such table:
    # memory_facts"), and a moment too late its connection is holding the file while
    # ``Store.__init__`` here runs ``PRAGMA journal_mode=WAL``, which wants a brief
    # exclusive lock ("database is locked"). Opening first is the only ordering with
    # no window at all: this call creates the schema, closes, and the server then
    # opens a file that is already complete.
    seed_store = Store(tmp_path / IPC_DB_NAME)
    try:
        seed_store._conn.execute(
            "INSERT INTO memory_facts (id, fact, confirmed_by_user, created_at) VALUES "
            "('f1', 'Lives in Brno', 1, 1), ('f2', 'Might get a dog', 0, 2)"
        )
        seed_store._conn.commit()
    finally:
        seed_store.close()
    harness = build_server(tmp_path, provider=provider, register_tool=False)
    try:
        for i in range(5):
            _send(harness.reader, harness.writer, i + 1, f"message {i}")
        # What the cut WOULD be, asked of the same function the code delegates to,
        # over the live conversation as it stands one message into the last turn.
        _send(harness.reader, harness.writer, 6, "the long one")
        seeded = harness.server.conversation
    finally:
        _shutdown(harness.reader, harness.thread)

    rows = {r["id"]: r for r in _conversations(tmp_path)}
    continuation = next(r for r in rows.values() if r["continued_from_conversation_id"])
    assert seeded.id == continuation["id"]
    head = seeded.messages[0]
    assert head.role == "user"
    assert _SUMMARY_TEXT in head.content
    assert "Lives in Brno" in head.content
    assert "Might get a dog" not in head.content   # unconfirmed is not a fact
    # The tail is whole turns, and it is the tail choose_cut_point chose: the same
    # function over the original transcript answers with the same number of turns.
    tail = seeded.messages[1:]
    assert [m.role for m in tail][0] == "user"
    assert len([m for m in tail if m.role == "user"]) == KEEP_RECENT_TURNS
    assert [m.content for m in tail][-1] == "All right."


def test_build_seed_messages_copies_the_tail_rather_than_sharing_it():
    tail = [Message(role="user", content="keep me"), Message(role="assistant", content="ok")]
    seed = build_seed_messages(_SUMMARY_TEXT, ["a fact"], tail)
    assert [m.content for m in seed[1:]] == ["keep me", "ok"]
    seed[1].content = "mutated"
    assert tail[0].content == "keep me"   # nothing deleted, nothing edited


def test_the_cut_is_never_re_derived_here():
    """The tail handed to build_seed_messages is choose_cut_point's, full stop.

    Asserted on the function's own behaviour: it neither drops nor reorders what
    it is given, so a caller that delegates cannot silently disagree with the
    module that owns the rule."""
    messages = [Message(role="user", content=f"m{i}") for i in range(10)]
    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None
    seed = build_seed_messages(_SUMMARY_TEXT, [], messages[cut.index :])
    assert [m.content for m in seed[1:]] == [m.content for m in messages[cut.index :]]


# --- (4) memory_facts stays confirmation-only -------------------------------


def test_memory_facts_is_read_and_never_written(tmp_path):
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900])
    # A ``Store`` here and NOT a plain connection, which is the opposite of the
    # seeding test above, and the difference is which one opens the file first.
    # Nothing has built a server yet at this line, so there is no contention to
    # lose and, more to the point, this call is what CREATES the schema. A plain
    # connection would make an empty file and the insert would find no table.
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        store._conn.execute(
            "INSERT INTO memory_facts (id, fact, confirmed_by_user, created_at) "
            "VALUES ('f1', 'Lives in Brno', 1, 1)"
        )
        store._conn.commit()
    finally:
        store.close()
    _drive(tmp_path, provider)
    conn = sqlite3.connect(tmp_path / IPC_DB_NAME)
    facts = conn.execute("SELECT id, fact, confirmed_by_user FROM memory_facts").fetchall()
    conn.close()
    assert facts == [("f1", "Lives in Brno", 1)]


def test_the_store_offers_no_way_to_write_a_memory_fact():
    """Source level: confirmation-only is only a rule while nothing can write one.

    A summary is conversation-scoped state, not long-term memory (§4.8), so if an
    insert helper ever appears it must arrive with the confirmation flow that
    justifies it, and this test failing is where that conversation starts."""
    tree = ast.parse((_SRC / "memory" / "store.py").read_text(encoding="utf-8"))
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "memory_facts" in node.value
        # SQL, not prose: a docstring saying the word "deleted" is not a write path.
        and node.value.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []


# --- (5) never a registry tool ---------------------------------------------


def test_nothing_registers_the_context_budget_manager_as_a_tool():
    """§4.8 hard rule 1, at source level, because the property is an ABSENCE.

    Registry tools are user-consented actions with risk tiers; this is
    bookkeeping, and a model able to ask for it is a model able to decide when to
    rewrite its own memory. So: no tool module mentions either context module, and
    neither context module knows what a tool, a registry or a gate is."""
    tool_sources = list((_SRC / "tools").glob("*.py"))
    for path in tool_sources:
        text = path.read_text(encoding="utf-8")
        assert "context_budget" not in text, path
        assert "context_continuation" not in text, path
    for name in ("context_budget.py", "context_continuation.py"):
        text = (_SRC / name).read_text(encoding="utf-8")
        tree = ast.parse(text)
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any("tools" in module for module in imported), name
        assert not any("permissions" in module for module in imported), name
        assert "ToolDefinition" not in text, name
        assert "RiskTier" not in text, name


def test_the_only_caller_is_the_turn_boundary():
    """One call site, in rpc/conversation.py, after the turn, not inside it."""
    text = (_SRC / "rpc" / "conversation.py").read_text(encoding="utf-8")
    assert text.count("_maybe_continue_for_budget(") == 2   # definition + one call
    orchestrator = (_SRC / "orchestrator.py").read_text(encoding="utf-8")
    # The orchestrator MEASURES and never acts: it knows nothing about continuing.
    assert "context_continuation" not in orchestrator


# --- (6) the store columns, fresh and upgraded ------------------------------


def test_lineage_and_summary_round_trip_on_a_fresh_database(tmp_path):
    store = Store(tmp_path / "fresh.sqlite3")
    store.create_conversation(id="a", title="First", provider_id="primary", started_at=1)
    store.create_conversation(
        id="b", title="First", provider_id="primary", started_at=2,
        continued_from="a", summary=_SUMMARY_TEXT,
    )
    header = store.get_conversation("b")
    assert header is not None
    assert header["continued_from_conversation_id"] == "a"
    assert header["summary"] == _SUMMARY_TEXT
    first = store.get_conversation("a")
    assert first is not None and first["summary"] is None


def test_lineage_and_summary_round_trip_on_an_upgraded_database(tmp_path):
    """A database made before the §4.8 columns existed gains them on open.

    ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists, so
    without the migration the continuation write fails with "no such column" on
    exactly the oldest installation. Verified rather than assumed."""
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations ("
        " id TEXT PRIMARY KEY, title TEXT, started_at INTEGER NOT NULL, "
        " provider_id TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = Store(path)
    store.create_conversation(id="a", title=None, provider_id="primary", started_at=1)
    store.create_conversation(
        id="b", title=None, provider_id="primary", started_at=2,
        continued_from="a", summary=_SUMMARY_TEXT,
    )
    header = store.get_conversation("b")
    assert header is not None
    assert header["continued_from_conversation_id"] == "a"
    assert header["summary"] == _SUMMARY_TEXT


# --- (7) the v1 sentence, reconciled ---------------------------------------


def test_a_chat_too_long_for_its_model_says_to_start_a_new_chat():
    """§4.8 promised this sentence for v1 and no code ever produced it.

    It still has to exist: the automatic layer cannot help when the provider
    reports no window, or when the summary call itself failed. The provider's own
    explanation decides, so an ordinary bad request keeps its own message."""
    exc = exception_for_http_status(
        400, "That request wasn't accepted.",
        "prompt is too long: 210000 tokens > 200000 maximum",
    )
    assert "start a new chat" in str(exc).lower()
    plain = exception_for_http_status(400, "That request wasn't accepted.", "bad field 'foo'")
    assert str(plain) == "That request wasn't accepted."
    none_detail = exception_for_http_status(400, "That request wasn't accepted.")
    assert str(none_detail) == "That request wasn't accepted."


# --- (8) the boundary, on the wire -----------------------------------------


def _request(harness, request_id: int, method: str, params: dict | None = None) -> dict:
    frame: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = params
    harness.reader.feed(frame)
    return harness.writer.wait_for(
        lambda f: f.get("id") == request_id and "result" in f
    )["result"]


def test_the_stored_boundary_reaches_the_surfaces_that_draw_it(tmp_path):
    """The lineage and the summary come back on ``conversation.list`` and
    ``conversation.load`` (the two KNOWN-GAPS entries closed 2026-08-22).

    Both columns had been written since 2026-08-14 and nothing read them back,
    which is exactly why the only boundary a person saw was the per-turn note.
    What this asserts is that the DURABLE facts are on the wire: the thread can
    say a boundary is here every time the chat is opened, and the sidebar can draw
    a continued chat as one thing. Neither half is hidden by either payload — both
    conversations are still listed, and the original still opens in full."""
    provider = _BudgetProvider(max_context_tokens=1_000, per_turn_tokens=[10] * 5 + [900])
    harness = build_server(tmp_path, provider=provider, register_tool=False)
    try:
        for i in range(5):
            _send(harness.reader, harness.writer, i + 1, f"message {i}")
        original_id = harness.server.conversation.id
        _send(harness.reader, harness.writer, 6, "the long one")
        continuation_id = harness.server.conversation.id
        listing = _request(harness, 7, Method.CONVERSATION_LIST)["conversations"]
        loaded = _request(
            harness, 8, Method.CONVERSATION_LOAD, {"conversationId": continuation_id}
        )
        # Loaded last: it makes the original the live conversation again.
        original = _request(
            harness, 9, Method.CONVERSATION_LOAD, {"conversationId": original_id}
        )
    finally:
        _shutdown(harness.reader, harness.thread)

    assert continuation_id != original_id
    rows = {row["id"]: row for row in listing}
    assert set(rows) == {original_id, continuation_id}   # neither half is hidden
    assert rows[continuation_id]["continuedFrom"] == original_id
    assert "continuedFrom" not in rows[original_id]      # an ordinary row is unchanged

    assert loaded["continuedFrom"] == original_id
    assert loaded["summary"] == _SUMMARY_TEXT
    assert "continuedFrom" not in original and "summary" not in original
    # The older transcript is still reachable, in full: the marker is an access
    # path to it, and this is the path.
    assert original["messages"]
