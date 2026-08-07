"""MCP dispatch — step 7, PHASE 3 (docs/step-7-mcp-plan.md §4.3).

Phase 2 proved Addison could SEE what a stranger's server offers and run none of
it. Phase 3's claim is the one every earlier phase was arranging for, and it is the
first time in this step that somebody else's program does something on a person's
say-so: **an MCP tool is invoked through the ordinary gate, its answer is redacted
and capped before a model sees it, every outcome leaves a durable row, and a hung
server costs a wait rather than a turn.**

So this file is organised around what must remain true while that happens:

  (1) THE CALL — the protocol exchange, one session per call, the server's own
      tool name (never the namespaced id), and the deadline that rides with it.
  (2) THE ANSWER — redacted BEFORE it is capped (the ordering is the test), the cap
      itself, a server-declared error, and an answer with nothing in it.
  (3) THE GATE, UNCHANGED — approval runs the call, refusal reaches no network at
      all, and SAFE refuses before either. No special case for an ``mcp:`` id
      exists at any dispatch site, which is what this section proves by using the
      same gate every native tool passes through.
  (4) EVERY OUTCOME LEAVES A ROW, in BOTH dispatch paths — granted naming what the
      redactor removed, denied, dev_only, not_callable, failed.
  (5) THE VOCABULARY MIGRATION — an old database's rows survive it, the new values
      become insertable, and re-opening changes nothing.
  (6) THE ADDRESS, AT THE MOMENT OF USE — a server removed between the check and
      the call refuses cleanly instead of being reached where it used to be.

No test here touches a network: every exchange runs through ``httpx.MockTransport``
below name resolution, exactly as phase 2's do.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from agent_core import mcp_client
from agent_core.mcp_catalog import (
    CALL_FAILED,
    SERVER_GONE,
    McpCatalog,
    McpTool,
)
from agent_core.mcp_client import (
    DROPPED_PARTS,
    MAX_RESULT_CHARS,
    NEEDS_SIGN_IN,
    NOTHING_TO_SHOW,
    RESULT_TRIMMED,
    TOOK_TOO_LONG,
    DiscoveredTool,
)
from agent_core.memory.store import Store
from agent_core.orchestrator import Conversation, Message, Orchestrator
from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.providers.base import (
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep
from agent_core.rpc.mcp import McpMixin
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext
from agent_core.tools.registry import DEV_ONLY_REFUSAL, NOT_CALLABLE_REFUSAL, ToolRegistry
from tests.conftest import _shutdown, build_server

SERVER_URL = "https://tools.example/mcp"
SERVER_ID = "s-1"
SERVER_NAME = "Design docs"
TOOL_ID = f"mcp:{SERVER_NAME}:search_docs"

# A realistic credential shape, assembled at runtime so no line in this file
# matches a secret scanner's rule (test_redaction_and_audit.py's reasoning — its
# fixture was blocked by GitHub push protection for being too convincing).
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


# ---------------------------------------------------------------------------
# A fake tool server, and the wiring one call travels through
# ---------------------------------------------------------------------------


class FakeToolServer:
    """A scripted MCP server that can also be CALLED.

    Records every request, so a test can assert what Addison sent as well as what
    it made of the answer — and, in the refusal tests, that it sent nothing."""

    def __init__(
        self,
        *,
        text: str = "the answer",
        is_error: bool = False,
        content: list | None = None,
        status: int = 200,
        hangs: bool = False,
    ) -> None:
        self.text = text
        self.is_error = is_error
        self.content = content
        self.status = status
        self.hangs = hangs
        self.bodies: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.hangs:
            raise httpx.ReadTimeout("the server said nothing", request=request)
        self.requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "no"})
        method = body.get("method")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result: dict = {"protocolVersion": mcp_client.PROTOCOL_VERSION}
        elif method == "tools/call":
            result = {
                "content": (
                    self.content
                    if self.content is not None
                    else [{"type": "text", "text": self.text}]
                )
            }
            if self.is_error:
                result["isError"] = True
        else:
            result = {}
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"},
            json={"jsonrpc": "2.0", "id": body.get("id"), "result": result},
        )

    def calls_made(self) -> list[dict]:
        return [b for b in self.bodies if b.get("method") == "tools/call"]

    def handshakes(self) -> list[dict]:
        return [b for b in self.bodies if b.get("method") == "initialize"]


class Wiring:
    """One registry with one discovered MCP tool in it, wired to a fake server.

    Holds the two seams so a test can assert on them directly: ``budgets`` records
    the deadline each call was given, and ``endpoints`` records what the resolver
    was asked (and can be emptied to simulate a server that went away)."""

    def __init__(self, server: FakeToolServer, *, schema: dict | None = None) -> None:
        self.server = server
        self.registry = ToolRegistry()
        self.budgets: list[float] = []
        self.asked: list[tuple[str, str]] = []
        self.saved: dict[tuple[str, str], str] = {(SERVER_ID, SERVER_NAME): SERVER_URL}
        self._client = httpx.Client(
            transport=httpx.MockTransport(server.handler), follow_redirects=False
        )
        self.catalog = McpCatalog(endpoint_for=self._endpoint_for, call_tool=self._call_tool)
        self.catalog.record_success(
            self.registry,
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            tools=(
                DiscoveredTool(
                    "search_docs",
                    "Searches the docs.",
                    schema or {"type": "object", "properties": {"q": {"type": "string"}}},
                ),
            ),
            skipped=0,
            checked_at=1,
        )

    def _endpoint_for(self, server_id: str, server_name: str) -> str | None:
        self.asked.append((server_id, server_name))
        return self.saved.get((server_id, server_name))

    def _call_tool(self, url: str, name: str, args: dict, budget: float):
        self.budgets.append(budget)
        return mcp_client.call_tool(
            url, name, args, budget, client=self._client, resolve=lambda host: ["127.0.0.1"]
        )

    def close(self) -> None:
        self._client.close()


@pytest.fixture
def wiring():
    """The default arrangement: one server, one tool, one plain answer."""
    made = Wiring(FakeToolServer())
    try:
        yield made
    finally:
        made.close()


def run_tool(made: Wiring, args: dict | None = None, mode: PolicyMode = PolicyMode.OPEN):
    """Call the registered tool directly — section (1) and (2)'s subject. The gate
    is section (3)'s, and runs the same tool through the real dispatch paths."""
    tool = made.registry.get(TOOL_ID)
    return tool.execute(args or {}, ExecutionContext(conversation_id="c", policy_mode=mode))


# ---------------------------------------------------------------------------
# (1) The call
# ---------------------------------------------------------------------------


def test_a_call_runs_the_whole_handshake_and_then_the_tool(wiring):
    """The protocol's order, per call: initialize, the initialized notification,
    then ``tools/call``. A server may refuse work that arrives before it has been
    told the client is ready, and phase 3 keeps no session to have done it earlier.

    Mutation: drop ``session.initialize()`` from ``mcp_client.call_tool`` — the
    method order assertion fails."""
    result = run_tool(wiring, {"q": "roadmap"})

    assert result.success is True
    assert result.content == "the answer"
    assert [b["method"] for b in wiring.server.bodies] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


def test_the_server_is_sent_its_own_tool_name_and_the_arguments_as_given(wiring):
    """The registry id is ``mcp:<server>:<tool>`` and the server has never heard of
    it. Sending the namespaced form would name a tool that does not exist, on every
    call, for every server.

    Mutation: pass ``self.definition.id`` instead of ``self._tool_name`` — this
    fails on the name."""
    run_tool(wiring, {"q": "roadmap", "limit": 3})
    (call,) = wiring.server.calls_made()
    assert call["params"]["name"] == "search_docs"
    assert call["params"]["arguments"] == {"q": "roadmap", "limit": 3}


def test_two_calls_are_two_sessions_and_nothing_is_reused(wiring):
    """ONE CALL, ONE SESSION, ONE BUDGET (decision 3). A pooled connection would be
    state outliving the turn that authorised it, held open to a program nobody here
    has audited — and a session id is one server's handle on one person's wait.

    Mutation: cache the ``_Session`` between calls — the second handshake
    disappears and this fails."""
    run_tool(wiring, {"q": "one"})
    run_tool(wiring, {"q": "two"})
    assert len(wiring.server.handshakes()) == 2
    assert len(wiring.server.calls_made()) == 2


def test_every_call_carries_the_deadline_and_it_comes_from_the_tool(wiring):
    """"The deadline is not optional" — the plan says so of this phase specifically,
    and it lives in ``execute`` because that is where a person is waiting. One
    budget covers the whole exchange, handshake included.

    Mutation: call ``self._call_tool`` without a budget — this fails to even run,
    which is the point of it being a positional argument rather than a default."""
    run_tool(wiring)
    assert wiring.budgets == [mcp_client.CALL_BUDGET_SECONDS]
    assert mcp_client.CALL_BUDGET_SECONDS <= 20, "a person waits this long for one tool"


def test_a_server_that_never_answers_gives_up_with_one_plain_sentence():
    """A hung server must not stall a turn. The sentence is ``mcp_client``'s own —
    a person is told the server was slow, not shown a timeout frame."""
    made = Wiring(FakeToolServer(hangs=True))
    try:
        result = run_tool(made)
        assert result.success is False
        assert result.content == TOOK_TOO_LONG
        assert result.audit_outcome == "failed"
    finally:
        made.close()


def test_a_server_that_wants_a_sign_in_is_answered_with_the_same_sentence_discovery_uses():
    """Decision 4: auth stays unsupported in phase 3, so a 401 during a CALL says
    exactly what a 401 during a check says. Two sentences for one situation would
    have a person hunting for a difference that does not exist."""
    made = Wiring(FakeToolServer(status=401))
    try:
        result = run_tool(made)
        assert result.content == NEEDS_SIGN_IN
        assert result.audit_outcome == "failed"
    finally:
        made.close()


@pytest.mark.parametrize(
    "content, expected",
    [
        pytest.param("not a list", mcp_client.NOT_A_TOOL_SERVER, id="content is not a list"),
        pytest.param(
            [{"type": "text"}],
            NOTHING_TO_SHOW + "\n" + DROPPED_PARTS.format(things="1 other part"),
            id="a text item with no text",
        ),
        pytest.param(
            [{"type": "text", "text": 7}],
            NOTHING_TO_SHOW + "\n" + DROPPED_PARTS.format(things="1 other part"),
            id="text that is not text",
        ),
    ],
)
def test_a_malformed_answer_fails_closed_with_one_sentence(content, expected):
    """Fail closed, like every other shape this client reads, and on the RIGHT
    sentence — which is what makes this a test of the validation rather than of
    whatever happens to raise. ``content`` that is not a list is not a result at
    all; an item whose text is missing or is not text is a well-formed answer
    carrying nothing, which is a different thing and says so.

    **Phase 4 made the second half say MORE, not less** (2026-08-07): the sentence
    it said in phase 3 is unchanged and still asserted, and the disclosure line now
    follows it, because an item that reached Addison and was not carried is a fact
    the phase-4 decision says out loud rather than swallows. A text item whose text
    is not text is exactly that item."""
    made = Wiring(FakeToolServer(content=content))
    try:
        assert run_tool(made).content == expected
    finally:
        made.close()


def test_an_argument_addison_cannot_send_is_one_sentence_rather_than_a_crash():
    """A defect on Addison's side — an argument that will not serialize — ends the
    STEP, never the turn. A tool that crashes a turn leaves its ``tool_use``
    unanswered, and the provider then rejects every later request."""
    made = Wiring(FakeToolServer())
    try:
        result = run_tool(made, {"bad": object()})
        assert result.success is False
        assert result.content == CALL_FAILED
        assert result.audit_outcome == "failed"
        assert made.server.calls_made() == []
    finally:
        made.close()


# ---------------------------------------------------------------------------
# (2) The answer
# ---------------------------------------------------------------------------


def test_a_credential_in_a_servers_answer_is_removed_and_named(wiring):
    """Decision 1, pulled forward from phase 4 deliberately: shipping dispatch
    without the redaction seam would put credential-bearing server output in front
    of a model between two merges. The kinds ride back on the ToolResult so the
    audit row can say what was caught — the only durable evidence there is.

    Mutation: return ``answer.text`` unscrubbed — this fails on both halves."""
    made = Wiring(FakeToolServer(text=f"here you go: {AWS_KEY} — enjoy"))
    try:
        result = run_tool(made)
        assert AWS_KEY not in result.content
        assert "[redacted: AWS access key]" in result.content
        assert result.redacted_kinds == ("AWS access key",)
    finally:
        made.close()


def test_the_answer_is_redacted_before_it_is_cut_not_after():
    """THE TEST THIS SECTION EXISTS FOR, and ``run_command``'s hard-won lesson
    applied to a second source of output. Every redaction rule is anchored on a
    vendor prefix followed by a minimum body, so a cut THROUGH a credential leaves
    a head that matches nothing afterwards and travels to the provider intact —
    still enough to name the vendor and the account. The cut can defeat the
    redactor, so the redactor goes first.

    The secret here straddles the cap on purpose.

    Mutation: swap the two calls in ``McpTool.execute`` — this fails, and it is the
    only test in the tree that would."""
    # The space matters: the AWS rule is anchored on a word boundary, so a key with
    # a letter jammed against it is not a key. This one starts eight characters
    # before the cap and ends twelve past it.
    straddling = "x" * (MAX_RESULT_CHARS - 9) + " " + AWS_KEY + " " + "y" * 200
    made = Wiring(FakeToolServer(text=straddling))
    try:
        result = run_tool(made)
        assert AWS_KEY not in result.content
        # Not vacuous: no PREFIX of it survived the cut either, which is exactly
        # what the wrong order would have left behind.
        assert AWS_KEY[:12] not in result.content
        assert result.redacted_kinds == ("AWS access key",)
    finally:
        made.close()


def test_a_very_long_answer_is_trimmed_and_says_so():
    """Decision 2: a server can return megabytes, and phase 3 must not ship
    uncapped. The marker is plain and it is present — a silent trim is a tool
    answering a different question than the one it was asked.

    **Phase 4 gave the marker the totals** (2026-08-07), which is the one thing
    changed here: a bare "this was trimmed" sits identically under a nine-character
    overflow and a nine-megabyte one, and a model that cannot tell those apart
    cannot decide whether to ask the tool something narrower.

    Mutation: return the text uncapped — this fails on the length."""
    sent = MAX_RESULT_CHARS * 3
    marker = RESULT_TRIMMED.format(sent=sent, shown=MAX_RESULT_CHARS)
    made = Wiring(FakeToolServer(text="a" * sent))
    try:
        result = run_tool(made)
        assert len(result.content) == MAX_RESULT_CHARS + len(marker)
        assert result.content.endswith(marker)
        assert "trimmed" in marker
        assert str(sent) in marker and str(MAX_RESULT_CHARS) in marker
    finally:
        made.close()


def test_a_server_that_calls_its_own_answer_an_error_produces_a_failed_result():
    """``isError`` is the server's claim about its own call, and it is believed —
    it says nothing about permission or safety, only whether the thing the model
    asked for worked. The text still crosses redaction and the cap: an error
    message is exactly where a stack trace with a token in it turns up."""
    made = Wiring(FakeToolServer(text=f"failed to auth with {AWS_KEY}", is_error=True))
    try:
        result = run_tool(made)
        assert result.success is False
        assert AWS_KEY not in result.content
        assert "[redacted: AWS access key]" in result.content
        # NOT 'failed': the call landed, and the row records the decision plus what
        # came back. 'failed' is reserved for a call that never reached anything.
        assert result.audit_outcome is None
    finally:
        made.close()


def test_only_text_content_is_carried_and_an_answer_with_none_says_so():
    """Phase 3's scope, stated as behaviour: an image or an embedded resource is
    not half-carried, and an answer with nothing textual in it reaches the model as
    a sentence rather than as an empty string — which reads as a tool that quietly
    did nothing.

    **Phase 4 arrived, as this test's last line said it would** (2026-08-07). The
    half phase 3 pinned is unchanged — nothing was decoded, nothing was carried,
    and the empty-answer sentence is still what a model reads. What is new is the
    second line: the pictures and the file are COUNTED and disclosed, because a
    tool that returned two charts and a caption must not be indistinguishable from
    a tool that returned a caption. ``tests/test_mcp_output.py`` owns that
    behaviour in full; this keeps the phase-3 half honest beside it."""
    made = Wiring(
        FakeToolServer(
            content=[
                {"type": "image", "data": "…", "mimeType": "image/png"},
                {"type": "resource", "resource": {"uri": "file:///x"}},
            ]
        )
    )
    try:
        result = run_tool(made)
        assert result.content.startswith(NOTHING_TO_SHOW)
        assert result.content.endswith(DROPPED_PARTS.format(things="1 image and 1 file"))
        assert "…" not in result.content, "an image's own bytes never travel"
    finally:
        made.close()


# ---------------------------------------------------------------------------
# (3) The gate, unchanged
# ---------------------------------------------------------------------------


class _CallsTheMcpTool:
    """One turn: call the MCP tool once, then answer."""

    def __init__(self, args: dict | None = None, tool_id: str = TOOL_ID) -> None:
        self._args = args or {"q": "roadmap"}
        self._tool_id = tool_id
        self.sent: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=100_000,
            supports_streaming=False,
            runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sent.append(list(messages))
        if len(self.sent) == 1:
            return ModelResponse(
                text=None,
                tool_calls=[ToolCallRequest(id="c1", tool_id=self._tool_id, args=self._args)],
            )
        return ModelResponse(text="done", tool_calls=[])


def _turn(made: Wiring, *, granted=True, mode=PolicyMode.OPEN, rows=None, tmp_path=None):
    """One real turn through the real orchestrator, with the real gate."""
    provider = _CallsTheMcpTool()
    store = Store(tmp_path / "turn.sqlite3") if tmp_path is not None else None
    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: provider}),
        tool_registry=made.registry,
        permission_gate=PermissionGate(
            on_request=lambda *a, **k: (
                PermissionStatus.GRANTED if granted else PermissionStatus.DENIED
            )
        ),
        undo_manager=UndoManager(store=store, tool_registry=made.registry),
        on_tool_audit=None if rows is None else rows.append,
    )
    conversation = Conversation(id="conv-1")
    conversation.messages.append(Message(role="user", content="look it up"))
    orchestrator.run_turn(conversation, requested_role=ModelRole.PRIMARY, mode=mode)
    return provider, conversation


def test_an_approved_call_reaches_the_server_and_its_answer_reaches_the_model(tmp_path):
    """Dispatch, end to end, through the gate every native tool passes through.

    The model-facing half is asserted ON THE WIRE (test_redaction_and_audit's rule):
    a check on the ToolResult would pass while the provider still received the
    secret, because the send boundary sits between the two."""
    made = Wiring(FakeToolServer(text=f"found it: {AWS_KEY}"))
    try:
        provider, conversation = _turn(made, tmp_path=tmp_path)

        assert len(made.server.calls_made()) == 1
        assert any(m.role == "assistant" and m.content == "done" for m in conversation.messages)
        # The second send is the one that carries the tool's answer.
        last = " ".join(str(getattr(m, "content", "")) for m in provider.sent[-1])
        assert "found it" in last
        assert AWS_KEY not in last
    finally:
        made.close()


def test_a_refused_call_touches_no_network_at_all(tmp_path):
    """"Not now" must mean nothing happened, and for a tool server that means no
    packet left the machine — not a connection opened and abandoned, and certainly
    not a handshake. The gate sits above ``execute``, which is where every byte of
    this exchange is written.

    Mutation: move the call above the gate in ``_run_tool_calls`` — the request
    list is no longer empty and this fails."""
    made = Wiring(FakeToolServer())
    try:
        _turn(made, granted=False, tmp_path=tmp_path)
        assert made.server.bodies == [], "a denied call reached the server"
        assert made.asked == [], "a denied call resolved the server's address"
    finally:
        made.close()


def test_safe_mode_refuses_an_mcp_id_before_the_gate_and_before_the_network(tmp_path):
    """SAFE never sees an ``mcp:`` id — that is the dev-only decision and SAFE
    invariant 1, and phase 3 did not touch either. Reaching this branch means a
    tool_use named a hidden id anyway; it is refused above the gate, so nothing is
    approved and nothing is reached.

    Mutation: drop ``dev_only=True`` from the registration in ``mcp_catalog`` —
    this fails, and a Simple-profile turn calls a stranger's program."""
    made = Wiring(FakeToolServer())
    try:
        rows: list[dict] = []
        _, conversation = _turn(made, mode=PolicyMode.SAFE, rows=rows, tmp_path=tmp_path)
        assert made.server.bodies == []
        assert any(
            DEV_ONLY_REFUSAL in str(getattr(m, "content", "")) for m in conversation.messages
        )
        assert [r["outcome"] for r in rows] == ["dev_only"]
    finally:
        made.close()


def test_the_gate_cards_every_single_invocation(tmp_path):
    """§3: HIGH and destructive unconditionally, so the gate raises a card PER CALL
    and a grant never carries over. A server declares its own risk and that is
    exactly what v1 refuses to trust.

    Mutation: return False from ``McpTool.is_destructive`` — OPEN auto-grants and
    the card count drops to zero."""
    made = Wiring(FakeToolServer())
    try:
        cards: list[str] = []

        def _ask(tool_id, detail=None):
            cards.append(tool_id)
            return PermissionStatus.GRANTED

        # ONE gate across both turns — that is the whole assertion. A fresh provider
        # per turn, because a provider is scripted and a reused one answers the
        # second turn without asking for anything.
        gate = PermissionGate(on_request=_ask)
        for _ in range(2):
            orchestrator = Orchestrator(
                model_router=ModelRouter(configured={ModelRole.PRIMARY: _CallsTheMcpTool()}),
                tool_registry=made.registry,
                permission_gate=gate,
                undo_manager=UndoManager(
                    store=Store(tmp_path / "cards.sqlite3"), tool_registry=made.registry
                ),
            )
            conversation = Conversation(id="conv-1")
            conversation.messages.append(Message(role="user", content="again"))
            orchestrator.run_turn(
                conversation, requested_role=ModelRole.PRIMARY, mode=PolicyMode.OPEN
            )
        assert cards == [TOOL_ID, TOOL_ID]
        assert len(made.server.calls_made()) == 2
    finally:
        made.close()


def test_turning_the_phase_gate_back_off_stops_dispatch_everywhere(monkeypatch, wiring):
    """The phase constant is ONE constant, and turning it off must be a complete
    answer rather than half of one. ``McpTool.execute``'s own check is the innermost
    of the layers — on ``run_command``'s belt-and-suspenders precedent — and it does
    not depend on the tool having been registered while the constant was off.

    Mutation: delete the ``MCP_TOOLS_ARE_CALLABLE`` check in ``execute`` — a tool
    registered before the flip keeps calling out after it."""
    monkeypatch.setattr("agent_core.mcp_catalog.MCP_TOOLS_ARE_CALLABLE", False)
    result = run_tool(wiring)
    assert result.content == NOT_CALLABLE_REFUSAL
    assert result.audit_outcome == "not_callable"
    assert wiring.server.bodies == []


# ---------------------------------------------------------------------------
# (4) Every outcome leaves a row — in BOTH dispatch paths
# ---------------------------------------------------------------------------


def test_a_granted_call_leaves_a_row_naming_what_the_redactor_removed(tmp_path):
    """The row is the only durable evidence that a credential came back from
    somebody else's program: ``tool_audit`` is excluded from snapshots and never
    pruned, and the transcript is not searched by anybody asking "did anything
    leak?".

    Mutation: drop ``result.redacted_kinds`` from the orchestrator's granted branch
    — the row goes quiet about a leak that was caught, because re-running the
    redactor over already-scrubbed text finds nothing."""
    made = Wiring(FakeToolServer(text=f"token is {AWS_KEY}"))
    try:
        rows: list[dict] = []
        _turn(made, rows=rows, tmp_path=tmp_path)
        (row,) = [r for r in rows if r["tool_id"] == TOOL_ID]
        assert row["outcome"] == "granted"
        assert row["mode"] == "open"
        assert row["destructive"] is True
        assert row["redacted"] == "AWS access key"
        assert AWS_KEY not in json.dumps(row)
    finally:
        made.close()


def test_a_denied_call_leaves_a_row_too(tmp_path):
    made = Wiring(FakeToolServer())
    try:
        rows: list[dict] = []
        _turn(made, granted=False, rows=rows, tmp_path=tmp_path)
        assert [r["outcome"] for r in rows if r["tool_id"] == TOOL_ID] == ["denied"]
    finally:
        made.close()


def test_a_call_that_never_landed_is_recorded_as_failed_not_as_granted(tmp_path):
    """The distinction phase 3 added to the vocabulary, and the reason it needed
    adding: "approved, and nothing happened" is a different history from "approved,
    and it ran" — and for a program at the far end of a network, that is the
    question somebody will actually ask afterwards.

    Mutation: use "granted" unconditionally in the orchestrator's granted branch —
    this fails, and the log claims a call reached a server it never touched."""
    made = Wiring(FakeToolServer(hangs=True))
    try:
        rows: list[dict] = []
        _, conversation = _turn(made, rows=rows, tmp_path=tmp_path)
        assert [r["outcome"] for r in rows if r["tool_id"] == TOOL_ID] == ["failed"]
        # ...and the turn CONTINUED. A hung server costs a step, never the work.
        assert any(m.role == "assistant" and m.content == "done" for m in conversation.messages)
        assert any(TOOK_TOO_LONG in str(getattr(m, "content", "")) for m in conversation.messages)
    finally:
        made.close()


def test_a_not_callable_refusal_leaves_a_row_in_both_paths(tmp_path):
    """The KNOWN-GAPS entry phase 3 retires. Every neighbouring refusal —
    dev_only, forbidden, confined_out — left a row and this one did not, because
    the vocabulary had no value for it and widening a CHECK is a migration rather
    than an insert. Both paths, because a boundary only one of them records is a
    boundary only half the history knows about."""
    registry = ToolRegistry()
    registry.register(
        McpTool("mcp:S:t", "t", ""), dev_only=True, removable=True, not_callable=True
    )
    store = Store(tmp_path / "nc.sqlite3")

    live: list[dict] = []
    orchestrator = Orchestrator(
        model_router=ModelRouter(
            configured={ModelRole.PRIMARY: _CallsTheMcpTool(tool_id="mcp:S:t")}
        ),
        tool_registry=registry,
        permission_gate=PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
        undo_manager=UndoManager(store=store, tool_registry=registry),
        on_tool_audit=live.append,
    )
    conversation = Conversation(id="c")
    conversation.messages.append(Message(role="user", content="go"))
    orchestrator.run_turn(conversation, requested_role=ModelRole.PRIMARY, mode=PolicyMode.OPEN)
    assert [r["outcome"] for r in live] == ["not_callable"]
    assert any(
        NOT_CALLABLE_REFUSAL in str(getattr(m, "content", "")) for m in conversation.messages
    )

    routine_rows: list[dict] = []
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        registry,
        PermissionGate(on_request=lambda *a, **k: PermissionStatus.GRANTED),
        UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=routine_rows.append,
    )
    engine.run(
        Routine(
            id="r-1", name="T", description="", variables=[],
            steps=[RoutineStep("s1", "mcp:S:t", {})],
        ),
        {},
        mode=PolicyMode.OPEN,
    )
    assert [r["outcome"] for r in routine_rows] == ["not_callable"]
    # ...and every one of these rows is a value SQLite will actually accept, which
    # is the half a CHECK-constrained vocabulary can fail on its own.
    for row in live + routine_rows:
        store.insert_tool_audit(**row)
    assert [r["outcome"] for r in store.list_tool_audit()] == ["not_callable"] * 2


def _routine_run(made: Wiring, tmp_path, *, granted=True, rows=None, mode=PolicyMode.OPEN):
    store = Store(tmp_path / "routine.sqlite3")
    store.insert_routine(
        id="r-1", name="T", description="", plan_json={},
        created_from_conversation_id=None, created_at=1, created_in_mode="open",
    )
    engine = RoutineEngine(
        made.registry,
        PermissionGate(
            on_request=lambda *a, **k: (
                PermissionStatus.GRANTED if granted else PermissionStatus.DENIED
            )
        ),
        UndoManager(store=store, tool_registry=made.registry),
        store=store,
        on_tool_audit=None if rows is None else rows.append,
    )
    return engine.run(
        Routine(
            id="r-1", name="T", description="", variables=[],
            steps=[RoutineStep("s1", TOOL_ID, {"q": "roadmap"})],
        ),
        {},
        mode=mode,
    )


def test_a_routine_step_calls_a_tool_server_the_same_way_and_records_the_same_row(tmp_path):
    """Parity, which is SAFE invariant 3's whole shape: the routine engine shares
    the registry, the gate and the audit sink, so a step reaches a server exactly
    as a live turn does — including the redaction the answer crosses on its way
    back, which this path needed a row of its own to record.

    Mutation: leave the routine's granted audit BEFORE ``execute`` — the row loses
    the redacted kinds and this fails."""
    made = Wiring(FakeToolServer(text=f"found {AWS_KEY}"))
    try:
        rows: list[dict] = []
        _routine_run(made, tmp_path, rows=rows)
        assert len(made.server.calls_made()) == 1
        (row,) = [r for r in rows if r["tool_id"] == TOOL_ID]
        assert row["outcome"] == "granted"
        assert row["redacted"] == "AWS access key"
        assert row["conversation_id"] is None
    finally:
        made.close()


def test_a_routine_step_that_is_declined_reaches_nothing_and_says_so(tmp_path):
    made = Wiring(FakeToolServer())
    try:
        rows: list[dict] = []
        result = _routine_run(made, tmp_path, granted=False, rows=rows)
        assert made.server.bodies == []
        assert [r["outcome"] for r in rows] == ["denied"]
        assert result.status == "failed"
    finally:
        made.close()


def test_a_routine_step_against_a_hung_server_is_a_failed_step_not_a_failed_engine(tmp_path):
    """A routine runs with nobody watching, so a hung server there is the case that
    would otherwise leave a run recorded as 'running' forever."""
    made = Wiring(FakeToolServer(hangs=True))
    try:
        rows: list[dict] = []
        result = _routine_run(made, tmp_path, rows=rows)
        assert [r["outcome"] for r in rows] == ["failed"]
        assert result.status == "failed"
        assert result.detail == TOOK_TOO_LONG
    finally:
        made.close()


def test_no_audit_row_from_a_tool_server_call_can_carry_a_secret(tmp_path):
    """G1 applied to history, asserted where the row LANDS. The kinds column names
    what was removed and never a value, a length or a prefix — and the answer
    itself never reaches this table at all."""
    made = Wiring(FakeToolServer(text=f"token {AWS_KEY}"))
    try:
        store = Store(tmp_path / "audit.sqlite3")
        rows: list[dict] = []
        _turn(made, rows=rows, tmp_path=tmp_path)
        for row in rows:
            store.insert_tool_audit(**row)
        stored = store.list_tool_audit()
        assert stored, "the call must have left a row — otherwise this proves nothing"
        for row in stored:
            blob = " ".join(str(value) for value in row.values())
            assert AWS_KEY not in blob
            assert AWS_KEY[:12] not in blob
    finally:
        made.close()


# ---------------------------------------------------------------------------
# (5) The vocabulary migration
# ---------------------------------------------------------------------------

_OLD_TOOL_AUDIT = """
CREATE TABLE tool_audit (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,
    tool_id         TEXT NOT NULL,
    detail          TEXT,
    mode            TEXT NOT NULL CHECK(mode IN ('safe','open')),
    destructive     INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT NOT NULL
                        CHECK(outcome IN ('granted','denied','forbidden',
                                          'confined_out','dev_only')),
    redacted        TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX idx_tool_audit_created ON tool_audit(created_at);
"""


def _database_on_the_old_check(path) -> None:
    """A database as it existed before phase 3: the old CHECK, its index, and rows
    somebody's history depends on."""
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_TOOL_AUDIT)
    conn.executemany(
        "INSERT INTO tool_audit (id, conversation_id, tool_id, detail, mode, "
        "destructive, outcome, redacted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("a", "c-1", "run_command", "./deploy.sh", "open", 1, "granted", "GitHub token", 10),
            ("b", None, "save_file", None, "safe", 0, "denied", None, 20),
            ("c", "c-2", "read_web_page", "example.com", "safe", 0, "forbidden", None, 30),
        ],
    )
    conn.commit()
    conn.close()


def test_the_migration_keeps_every_row_it_finds(tmp_path):
    """THE TEST THE MIGRATION EXISTS TO PASS. These rows are durable by design —
    excluded from snapshots, never pruned — so they are the whole record of what
    Addison has done, and a rebuild that dropped them would destroy it to widen a
    constraint.

    Mutation: skip the ``INSERT INTO ... SELECT`` in the rebuild — three rows of
    somebody's history disappear and this fails."""
    path = tmp_path / "old.sqlite3"
    _database_on_the_old_check(path)

    store = Store(path)
    rows = {r["id"]: r for r in store.list_tool_audit()}

    assert set(rows) == {"a", "b", "c"}
    assert rows["a"]["outcome"] == "granted"
    assert rows["a"]["detail"] == "./deploy.sh"
    assert rows["a"]["redacted"] == "GitHub token"
    assert rows["b"]["conversation_id"] is None
    assert rows["c"]["outcome"] == "forbidden"


def test_the_migration_makes_the_new_vocabulary_insertable(tmp_path):
    """The point of the rebuild. Without it, 'not_callable' and 'failed' would work
    on a fresh database and be rejected on every upgraded one — where each audit
    caller's best-effort ``except`` swallows the failure, so the row would simply
    never appear and the log would read as "nothing happened".

    Mutation: return early from ``_migrate_tool_audit_outcomes`` — this fails with
    a CHECK constraint error, which is exactly what a person's database would have
    done in silence."""
    path = tmp_path / "old.sqlite3"
    _database_on_the_old_check(path)
    store = Store(path)

    for index, outcome in enumerate(Store._TOOL_AUDIT_OUTCOMES):
        store.insert_tool_audit(
            id=f"new-{index}",
            conversation_id=None,
            tool_id=TOOL_ID,
            detail=None,
            mode="open",
            destructive=True,
            outcome=outcome,
            redacted=None,
            created_at=100 + index,
        )
    stored = {r["outcome"] for r in store.list_tool_audit()}
    assert set(Store._TOOL_AUDIT_OUTCOMES) <= stored


def test_an_outcome_nobody_registered_is_still_refused(tmp_path):
    """The widening is a widening, not a removal. A CHECK that accepted anything
    would let a typo become a row that no query can find."""
    store = Store(tmp_path / "fresh.sqlite3")
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_tool_audit(
            id="x", conversation_id=None, tool_id="t", detail=None, mode="open",
            destructive=False, outcome="probably_fine", redacted=None, created_at=1,
        )


def test_the_migration_is_idempotent_and_leaves_a_fresh_database_alone(tmp_path):
    """Three states, one method: an old database is rebuilt exactly once, an
    already-widened one is recognised from its own DDL, and a fresh one has no
    table for this to touch at all.

    Mutation: drop the ``all(... in existing)`` guard — the second open rebuilds a
    table that did not need it, which is a data copy on every single launch."""
    path = tmp_path / "old.sqlite3"
    _database_on_the_old_check(path)

    first = Store(path)
    first.insert_tool_audit(
        id="new", conversation_id=None, tool_id=TOOL_ID, detail=None, mode="open",
        destructive=True, outcome="failed", redacted=None, created_at=99,
    )
    first.close()

    second = Store(path)
    assert {r["id"] for r in second.list_tool_audit()} == {"a", "b", "c", "new"}
    # The index schema.sql declares is back, once, under its own name.
    indexes = second._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tool_audit'"
    ).fetchall()
    assert sorted(r["name"] for r in indexes if not r["name"].startswith("sqlite_")) == [
        "idx_tool_audit_created"
    ]
    # And nothing is left behind from the rebuild.
    leftovers = second._conn.execute(
        "SELECT name FROM sqlite_master WHERE name='tool_audit_old'"
    ).fetchall()
    assert leftovers == []
    second.close()

    fresh = Store(tmp_path / "fresh.sqlite3")
    fresh.insert_tool_audit(
        id="x", conversation_id=None, tool_id=TOOL_ID, detail=None, mode="open",
        destructive=True, outcome="not_callable", redacted=None, created_at=1,
    )
    assert [r["outcome"] for r in fresh.list_tool_audit()] == ["not_callable"]


def _audit_tables(path) -> dict[str, int]:
    """Every ``tool_audit*`` table on disk and how many rows it holds."""
    conn = sqlite3.connect(path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'tool_audit%'"
            )
        ]
        return {name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}
    finally:
        conn.close()


def _index_is_on(path) -> str | None:
    """The table ``idx_tool_audit_created`` is bound to, or None if it is gone.

    An index belongs to its table, so a rename takes it along and a drop takes it
    away — which is why "the rows survived" is only half of what a rebuild has to
    be asked. The other half is whether ``list_tool_audit``'s ``ORDER BY
    created_at DESC`` still has anything to walk, or full-scans for ever."""
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' "
            "AND name='idx_tool_audit_created'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_a_rebuild_that_is_interrupted_leaves_the_rows_exactly_where_they_were(tmp_path):
    """THE PROPERTY THE WHOLE REBUILD RESTS ON, and the one it did not have.

    ``executescript`` commits what is pending and then runs its statements in
    AUTOCOMMIT, so ``ALTER TABLE tool_audit RENAME TO tool_audit_old`` was durable
    the instant it ran. Anything that failed after it — a full disk, a lock outliving
    ``busy_timeout``, the power — left the rows in ``tool_audit_old`` with no
    ``tool_audit`` beside them. On the next open the migration saw no ``tool_audit``,
    returned early, and schema.sql created an empty one; the rows were stranded for
    good and the index stayed bound to the orphan.

    The interruption here is a real statement failing mid-sequence rather than a
    simulated one: the copy is asked for a column the new table does not have, which
    is what any schema disagreement between the two halves would look like.

    Mutation: swap the explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` back for an
    ``executescript`` — the rebuilt table survives the failure and this fails on
    every assertion below."""
    path = tmp_path / "old.sqlite3"
    _database_on_the_old_check(path)

    original = Store._TOOL_AUDIT_COLUMNS
    Store._TOOL_AUDIT_COLUMNS = original + ("no_such_column",)
    try:
        with pytest.raises(sqlite3.OperationalError):
            Store(path)
    finally:
        Store._TOOL_AUDIT_COLUMNS = original

    assert _audit_tables(path) == {"tool_audit": 3}, "a half-rebuilt database was left on disk"
    assert _index_is_on(path) == "tool_audit"
    # ...and the next open, with nothing sabotaged, completes the migration it
    # rolled back. A refusal that cannot be retried is a different kind of loss.
    store = Store(path)
    assert {r["id"] for r in store.list_tool_audit()} == {"a", "b", "c"}
    store.close()


def test_a_database_stranded_by_an_earlier_rebuild_recovers_its_rows(tmp_path):
    """SELF-HEALING, for the databases the defect above already reached. A
    ``tool_audit_old`` on disk is the signature of an interrupted rebuild and
    nothing else creates one, so it is a source to copy FROM rather than a table to
    step around — and the migration runs for its sake even when today's
    ``tool_audit`` already carries the current CHECK.

    Both rows survive and land once each: the stranded copy holds what was written
    before the interruption and the live table what has been written since, the id
    is the primary key, and ``INSERT OR IGNORE`` is what makes a row that exists in
    both arrive once rather than fail the recovery of every row beside it.

    Mutation: return early when ``tool_audit`` already has the current CHECK — the
    stranded rows stay stranded and this fails on the ids."""
    path = tmp_path / "stranded.sqlite3"
    _database_on_the_old_check(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE tool_audit RENAME TO tool_audit_old")
    conn.commit()
    conn.close()

    # The open that used to strand them: no ``tool_audit``, so schema.sql makes an
    # empty one and the three rows sit in the orphan beside it.
    first = Store(path)
    first.insert_tool_audit(
        id="since", conversation_id=None, tool_id=TOOL_ID, detail=None, mode="open",
        destructive=True, outcome="failed", redacted=None, created_at=40,
    )
    assert {r["id"] for r in first.list_tool_audit()} == {"a", "b", "c", "since"}
    first.close()

    assert _audit_tables(path) == {"tool_audit": 4}
    assert _index_is_on(path) == "tool_audit"

    second = Store(path)
    assert {r["id"] for r in second.list_tool_audit()} == {"a", "b", "c", "since"}
    second.close()
    assert _audit_tables(path) == {"tool_audit": 4}, "the recovery ran a second time"


def test_a_leftover_scratch_table_is_cleared_rather_than_refused(tmp_path):
    """THE SCRATCH NAME GETS THE SAME MERCY AS THE STRANDED ONE.

    The rebuild builds under `tool_audit_rebuilt` and renames it into place, so a
    process killed between the CREATE and the rename can leave that name behind.
    Meeting it with a bare CREATE raises out of ``Store.__init__`` — the core then
    cannot open the database AT ALL, which is a worse failure than the row loss
    this whole method exists to prevent, and one no restore point reaches because
    nothing is running to offer one.

    Mutation: drop the ``DROP TABLE IF EXISTS`` — this raises OperationalError."""
    path = tmp_path / "scratch.sqlite3"
    _database_on_the_old_check(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tool_audit_rebuilt (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    store = Store(path)
    assert {r["id"] for r in store.list_tool_audit()} == {"a", "b", "c"}
    store.close()
    assert _audit_tables(path) == {"tool_audit": 3}
    assert _index_is_on(path) == "tool_audit"


def test_the_rebuild_re_creates_the_index_itself(tmp_path):
    """The index used to be left to schema.sql's ``CREATE INDEX IF NOT EXISTS``, in
    the ``executescript`` that runs moments later — which is a SECOND step, and a
    crash can land between two steps. It is created inside the same transaction as
    everything else now, so the table and its index arrive together or neither does.

    Asserted with schema.sql DELIBERATELY OUT OF THE WAY — the migration is run on
    a bare connection, so the only thing that could have made the index is the
    rebuild. Through ``Store(path)`` the script would create it a moment later and
    this test would pass with the line deleted.

    Mutation: delete the ``CREATE INDEX`` from the rebuild — this fails."""
    path = tmp_path / "index.sqlite3"
    _database_on_the_old_check(path)

    bare = Store.__new__(Store)
    bare._conn = sqlite3.connect(path)
    bare._conn.row_factory = sqlite3.Row
    bare._migrate_tool_audit_outcomes()
    indexes = {
        row["name"]
        for row in bare._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tool_audit'"
        )
    }
    bare._conn.close()

    assert "idx_tool_audit_created" in indexes


def test_the_schema_and_the_migration_agree_on_the_vocabulary():
    """Two spellings of one vocabulary is how a value ends up legal in one place and
    rejected in the other — on an upgraded database only, which is the half nobody
    tests by hand."""
    schema = (
        __import__("pathlib")
        .Path(Store.__module__.replace(".", "/"))
        .with_name("schema.sql")
        .read_text(encoding="utf-8")
    )
    for outcome in Store._TOOL_AUDIT_OUTCOMES:
        assert f"'{outcome}'" in schema, outcome


# ---------------------------------------------------------------------------
# (6) The address, resolved at the moment of use
# ---------------------------------------------------------------------------


def test_a_server_removed_between_the_check_and_the_call_refuses_cleanly(wiring):
    """A tool's address is looked up when it is CALLED, never remembered from the
    check that registered it. Between the two, a row can be removed, restored away
    by a snapshot, or saved again under another name — and a call to where a server
    used to be is a request the person never authorised, going to an address they
    can no longer see.

    Mutation: capture ``url`` in ``McpTool.__init__`` and use it in ``execute`` —
    the call goes out to the old address and this fails."""
    wiring.saved.clear()
    result = run_tool(wiring)
    assert result.content == SERVER_GONE
    assert result.audit_outcome == "failed"
    assert wiring.server.bodies == []


def test_a_server_saved_again_under_a_different_name_is_a_different_server(wiring):
    """The name is half of the tool id, so it is half of every grant and audit row
    keyed by it. A row whose name no longer matches is not the server this tool
    claims to come from, whatever its id says."""
    wiring.saved = {(SERVER_ID, "Something else"): SERVER_URL}
    assert run_tool(wiring).content == SERVER_GONE
    assert wiring.server.bodies == []


class _ResolverOnly(McpMixin):
    """The real ``_mcp_endpoint_for`` over a real store, and nothing else.

    ``ServerContext`` is an empty class at runtime (it declares members for the
    type checker only), so a mixin can be exercised on its own — which is what lets
    this assert on the SHIPPED resolver rather than on a fixture that resembles it.
    Calling it through the running server instead is not an option: the store
    belongs to the worker thread, and sqlite says so."""

    def __init__(self, store: Store | None) -> None:
        # ``_store`` is the NULLABLE field, exactly as on the real server: it is
        # None until the worker builds one, and it is what code meaning "has the
        # store been built?" reads. The resolver runs from a tool's execute, so it
        # is the field it has to ask.
        self._store = store

    @property
    def store(self) -> Store:
        # A property rather than an attribute, matching ``ServerContext``'s own
        # declaration: the real server exposes ``store`` this way and ASSERTS, which
        # is why nothing that must answer None may go through it.
        assert self._store is not None
        return self._store


def test_the_live_resolver_reads_the_saved_row_and_stops_when_it_is_gone(tmp_path):
    """The real resolver, against the real store — the seam the catalog is handed in
    the running app, and what makes the two tests above describe production rather
    than a fixture.

    Mutation: drop the ``str(row["name"]) != server_name`` check — the renamed row
    resolves and this fails."""
    store = Store(tmp_path / "servers.sqlite3")
    resolver = _ResolverOnly(store)
    store.insert_mcp_server(id=SERVER_ID, name=SERVER_NAME, url=SERVER_URL, created_at=1)

    assert resolver._mcp_endpoint_for(SERVER_ID, SERVER_NAME) == SERVER_URL
    assert resolver._mcp_endpoint_for(SERVER_ID, "Some other name") is None
    assert resolver._mcp_endpoint_for("no-such-server", SERVER_NAME) is None

    store.delete_mcp_server(SERVER_ID)
    assert resolver._mcp_endpoint_for(SERVER_ID, SERVER_NAME) is None


def test_a_server_that_is_switched_off_has_no_address_at_all(tmp_path):
    """The dispatch half of ``enabled``. The column is stored, snapshot-captured and
    restored, so a resolver that ignored it would leave a setting the recovery path
    faithfully puts back and nothing obeys — and the hole it leaves is the worst
    kind, because the tools of a server somebody switched off stay registered until
    the next check and would keep reaching it.

    Nothing sets the column today (no toggle RPC, none added here), which is why the
    row is written straight into the table.

    Mutation: drop the ``not row["enabled"]`` check — the address resolves and this
    fails."""
    store = Store(tmp_path / "servers.sqlite3")
    resolver = _ResolverOnly(store)
    store.insert_mcp_server(id=SERVER_ID, name=SERVER_NAME, url=SERVER_URL, created_at=1)
    assert resolver._mcp_endpoint_for(SERVER_ID, SERVER_NAME) == SERVER_URL

    store._conn.execute("UPDATE mcp_servers SET enabled = 0 WHERE id = ?", (SERVER_ID,))
    store._conn.commit()

    assert resolver._mcp_endpoint_for(SERVER_ID, SERVER_NAME) is None


def test_a_resolver_with_no_store_yet_answers_nothing_rather_than_raising():
    """``self.store`` asserts, so the "has it been built?" question has to be asked
    of the private field — main.py's own rule for this code. It matters here because
    this resolver runs from inside a tool's ``execute``, where an AssertionError is
    a crashed turn rather than a refused call.

    Mutation: read ``self.store`` in ``_mcp_endpoint_for`` — this raises instead of
    answering None."""
    assert _ResolverOnly(None)._mcp_endpoint_for(SERVER_ID, SERVER_NAME) is None


def test_the_running_server_wires_dispatch_to_the_catalog(tmp_path):
    """The wiring itself, because every test above injects its own: the app's own
    catalog must carry BOTH seams, or a discovered tool registers and refuses to run
    while every test in this file passes.

    Mutation: drop ``endpoint_for`` from the ``McpCatalog(...)`` in main.py — this
    fails, and dispatch is dead in the shipped app only."""
    h = build_server(tmp_path, register_tool=False)
    try:
        assert h.server._mcp_catalog.endpoint_for is not None
        assert h.server._mcp_catalog.call_tool is mcp_client.call_tool
    finally:
        _shutdown(h.reader, h.thread)
