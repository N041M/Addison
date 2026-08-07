"""A minimal MCP client — Streamable HTTP: connect, list, and call
(step 7, PHASES 2–3 of five; [docs/step-7-mcp-plan.md](../docs/step-7-mcp-plan.md)
owns the phase order).

**This module speaks the protocol. It never registers, gates or audits
anything** — ``agent_core/mcp_catalog.py`` owns admission to the registry and the
tool body that runs a call, and the permission gate + ``tool_audit`` live where
they always did. What comes out of here is a list of names, descriptions and
bounded schemas a stranger's server offered, plus the text one of its tools
answered with, each already cut down to the shape the rest of Addison will hold.

**Top-level on purpose.** ``tools/``, ``providers/`` and ``routines/`` must not
import from one another (CLAUDE.md's module-boundary rule), and an MCP tool is
consumed by all three eventually; ``net_vetting`` sits here for the same reason.

**Transport is HTTP only** (owner decision 2026-08-06). That is what lets this
live in the Agent Core at all: the core already speaks HTTPS through ``httpx`` and
needs no new shell surface, whereas stdio would mean this process launching an
executable outside the seatbelt step 5.5 built. Nothing here may import
``subprocess``/``os``/``signal`` — ``tests/test_mcp_discovery.py`` enforces that on
the import graph, not in a comment.

Three properties do the safety work, and each one is a test:

1. **EVERYTHING A SERVER SENDS IS UNTRUSTED TEXT.** Not "probably fine" — the
   whole point of an MCP server is that somebody else wrote it. So the caps are at
   this boundary, before a single byte reaches a payload, a registry id or a
   screen: a bounded number of tools, a bounded number of pages, a bounded
   response body, names that must look like identifiers or be dropped, and control
   characters stripped out of every string. A tool that fails the name rule is
   SKIPPED AND COUNTED, never silently dropped and never repaired — repairing an
   implausible name would invent an id nobody's server actually offered.

2. **DEADLINES ARE NOT OPTIONAL.** The plan says so of phase 3's per-call deadline
   and the lesson is the same one ``run_command`` taught when it held the IPC pump
   for thirty seconds: a refresh runs on the worker thread and a call runs inside
   somebody's turn, so a server that accepts a connection and then says nothing
   would stall everything behind it. ONE budget covers the WHOLE exchange —
   handshake plus pagination (:data:`REFRESH_BUDGET_SECONDS`), or handshake plus
   the call (:data:`CALL_BUDGET_SECONDS`) — not each socket, because a per-attempt
   timeout is not a budget.

3. **FAIL CLOSED, IN ONE PLAIN SENTENCE.** Every failure here — unreachable,
   malformed, a version Addison doesn't speak, a sign-in it can't do — comes back
   as an :class:`McpError` carrying one sentence a person can read (CLAUDE.md:
   personas 54 and 68, no jargon, no stack traces). A server's OWN error text is
   never echoed: it is attacker-controlled, and the surfaces that would render it
   are the ones a person checks to decide what to trust.

No credential is sent, read or stored. Phase 1 left the keychain door open for
"when phase 2 needs one"; neither phase 2 nor phase 3 does (see the plan's phase-2
entry and phase-3 decision 4), so there is no token parameter here to be filled in
by accident, and a server that asks for a sign-in gets the same plain sentence
whether it is being listed or being called.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from agent_core import net_vetting

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------
#
# Each one says what happened and, where there is one, the next move. None of them
# quotes the server: its text is untrusted, and these strings are rendered on the
# page a person opens to decide what Addison may reach.

COULD_NOT_REACH = "Addison couldn't reach that server. Check the address, and that the server is running."
TOOK_TOO_LONG = "That server didn't answer in time. Try again in a moment."
NEEDS_SIGN_IN = "This server asks for a sign-in Addison doesn't support yet."
NOT_A_TOOL_SERVER = "That address answered, but not like a tool server. Check the address."
UNKNOWN_VERSION = "That server speaks a version of the tool-server protocol Addison doesn't know."
TOO_MUCH_BACK = "That server sent back more than Addison will read from one answer."
ADDRESS_NOT_ALLOWED = "Addison won't follow that server to a different address."
ODD_ADDRESS = "Addison couldn't make sense of that address."
# Phase 3. A call that landed and answered with nothing Addison can pass on —
# only pictures, only structured data, or an empty answer. Said rather than
# returned blank, because "" reaches a model as a tool that silently did nothing.
NOTHING_TO_SHOW = "That tool answered with nothing Addison can pass on."
# Appended when the answer was longer than one tool result may be. Leads with the
# ellipsis so it reads as a continuation of the text it follows.
RESULT_TRIMMED = "\n…Addison trimmed this tool's long answer."

# The sentences ``net_vetting`` hands back, in this caller's voice. Same mechanism
# as read_web_page and provider.connect; the words are ours (see its ``Sentences``).
_SENTENCES = net_vetting.Sentences(
    no_url=COULD_NOT_REACH,
    not_a_web_link=COULD_NOT_REACH,
    not_allowed=ADDRESS_NOT_ALLOWED,
    odd_web_address=ODD_ADDRESS,
    could_not_find_site=COULD_NOT_REACH,
    could_not_open=COULD_NOT_REACH,
    could_not_reach=COULD_NOT_REACH,
    took_too_long=TOOK_TOO_LONG,
    too_many_redirects=ADDRESS_NOT_ALLOWED,
    dropped_secure_link=ADDRESS_NOT_ALLOWED,
)

# --- Bounds. Every one of these is a cap on what a STRANGER can make Addison ---
# hold, so each is deliberately small enough to be boring and large enough for a
# real server. Widening one is a decision, not a tuning.

#: The whole refresh — handshake, initialized notification, and every page of
#: ``tools/list`` — must finish inside this. See property 2 above.
REFRESH_BUDGET_SECONDS = 10.0
#: The whole of ONE tool call — handshake, initialized notification, and the call
#: itself — must finish inside this (phase-3 decision 3: one call, one session, one
#: budget). Longer than a refresh because a real tool does real work at the far
#: end; still short enough that a hung server costs a person a wait and never a
#: turn, which is the thing the plan says is not optional.
CALL_BUDGET_SECONDS = 15.0
#: One socket. Bounded well under the budget so a stalled connection loses a
#: fraction of the walk rather than all of it.
_SOCKET_TIMEOUT_SECONDS = 5.0
#: A tool server is one endpoint the person typed. A same-origin hop (``/mcp`` ->
#: ``/mcp/``) is ordinary; anything further is refused by ``same_origin_only``.
_MAX_REDIRECTS = 2
#: Beyond this, a server is not offering a toolbox, it is filling one.
MAX_TOOLS_PER_SERVER = 100
#: ``nextCursor`` is server-controlled, so the page walk needs its own ceiling —
#: a server that always returns a cursor would otherwise loop until the budget.
_MAX_PAGES = 10
#: One JSON-RPC answer. 512 KB is far past any honest tools/list page.
_MAX_RESPONSE_BYTES = 512 * 1024
#: Long enough for a real tool id, short enough to read in a row.
MAX_TOOL_NAME_CHARS = 64
#: A description is prose for a person and, since phase 3, for a model. Truncated
#: rather than dropped — length alone is not dishonesty.
MAX_TOOL_DESCRIPTION_CHARS = 300
_MAX_URL_CHARS = 2048

# --- Phase-3 bounds: the schema going out, and the answer coming back ---------

#: One tool's ``inputSchema``, serialized. A JSON Schema describes a handful of
#: arguments; 16 KB is far past any honest one and small enough that a hundred of
#: them cannot crowd out a person's conversation in a model's context.
MAX_SCHEMA_BYTES = 16 * 1024
#: How deep that schema may nest. Depth is the cheap way to make a small document
#: expensive for everything downstream that walks it — a provider's translator, a
#: model's tokenizer, this module's own serializer.
MAX_SCHEMA_DEPTH = 8
#: What a tool advertises when its own schema was oversized, too deep, or not a
#: JSON-Schema object at all. THE TOOL IS STILL ADMITTED: a bad schema costs the
#: model its hints, never the person the tool. Callers copy it — it must not be
#: handed out as shared mutable state.
EMPTY_SCHEMA: dict = {"type": "object", "properties": {}}

#: How much of one tool's answer reaches a model. In CHARACTERS rather than bytes
#: because this bound is about a model's context, where characters are the unit
#: that matters; the byte bound on a stranger's answer is ``_MAX_RESPONSE_BYTES``
#: above, at the wire, and it is the one that stops an endless body. Twice
#: ``run_command``'s 4000 because a command's output is incidental to what was
#: asked and a tool's answer IS what was asked. Phase 4 owns refining this.
MAX_RESULT_CHARS = 8000

#: What a tool name must look like to be admitted. An MCP name becomes half of a
#: registry id (``mcp:<server>:<tool>``), which is compared, logged and shown, so
#: a name with a colon, a space, a slash or a right-to-left override in it is not
#: cleaned up — it is refused, and counted. Matches the shape every real MCP
#: server already uses.
_PLAUSIBLE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")

#: Offered on ``initialize``. The server answers with the version it will speak;
#: an OLDER one from this list is accepted (that is the protocol's own
#: negotiation), and anything else fails closed rather than guessing.
PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_CLIENT_INFO = {"name": "Addison", "version": "1.0"}


class McpError(Exception):
    """A refusal or a failure, carrying the one plain sentence to show. Never a
    stack trace, and never text the server chose."""


@dataclass(frozen=True)
class DiscoveredTool:
    """One tool a server offered, after the boundary cut it down to size.

    ``schema`` is the server's ``inputSchema`` if it fitted the bounds above and
    :data:`EMPTY_SCHEMA` if it did not. It defaults to the empty schema so a caller
    that names only what it cares about (every test in the tree) describes a tool
    with no arguments rather than a tool with none declared."""

    name: str
    description: str
    schema: dict = field(default_factory=lambda: dict(EMPTY_SCHEMA))


@dataclass(frozen=True)
class CallResult:
    """What one ``tools/call`` came back with: the text, and whether the server
    called it an error.

    ``is_error`` is the server's own ``isError`` flag, which is a different fact
    from the call failing — a tool that ran and reported "no such file" answers
    HTTP 200 with a perfectly good JSON-RPC result. A call that never landed raises
    :class:`McpError` instead and never reaches this shape."""

    text: str
    is_error: bool


@dataclass(frozen=True)
class Discovery:
    """What one refresh found. ``skipped`` is not decoration: it is how a person
    learns that a server offered something Addison would not hold, without that
    something reaching a screen."""

    tools: tuple[DiscoveredTool, ...]
    skipped: int
    protocol_version: str


@dataclass
class _Answer:
    """One HTTP response, already read and parsed. ``payload`` is None for an
    accepted notification (a 202 with no body)."""

    session_id: str | None
    payload: dict | None


def _strip_control(text: str) -> str:
    """Every control character out, and runs of whitespace flattened to one space.

    Both halves matter and for the same reason: this text is rendered in a row and
    (phase 3) sent to a model. A newline turns one row into several; a carriage
    return can overwrite what was drawn before it; U+202E flips the rest of a line
    right-to-left, which is the cheap way to make ``delete_everything`` read as
    something else entirely. None of it is repaired — it is removed."""
    kept = [" " if ch in "\t\n\r" else ch for ch in text if ch.isprintable() or ch in "\t\n\r"]
    return " ".join("".join(kept).split())


def _clean_name(value: object) -> str | None:
    """A usable tool name, or None when this one is not admitted.

    Fail-closed by construction: the value must already BE a plausible identifier
    of a sane length. It is never trimmed to fit or stripped of the character that
    made it implausible — a repaired name is an id the server never offered."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > MAX_TOOL_NAME_CHARS:
        return None
    if _PLAUSIBLE_NAME.fullmatch(name) is None:
        return None
    return name


def _clean_description(value: object) -> str:
    """A one-line description, control characters gone, truncated to the cap.

    Missing or non-string is "" rather than an error: a server that ships a tool
    with no description is unhelpful, not hostile, and the surface says so in its
    own words instead of refusing the tool."""
    if not isinstance(value, str):
        return ""
    text = _strip_control(value)
    if len(text) > MAX_TOOL_DESCRIPTION_CHARS:
        return text[:MAX_TOOL_DESCRIPTION_CHARS].rstrip() + "…"
    return text


def _too_deep(value: object, limit: int) -> bool:
    """Whether ``value`` nests deeper than ``limit``, walked ITERATIVELY.

    A recursive walk over an attacker-supplied document is the bug it is checking
    for, one level up: the depth that blows a stack is cheaper to send than the
    depth that costs anything else. The stack here is a list, and the walk stops at
    the first item past the limit rather than measuring how far past."""
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, dict):
            children: list = list(node.values())
        elif isinstance(node, list):
            children = list(node)
        else:
            continue
        if children and depth >= limit:
            return True
        pending.extend((child, depth + 1) for child in children)
    return False


def _clean_schema(value: object) -> dict:
    """The server's ``inputSchema``, admitted only within bounds — or the empty
    schema, with the tool still admitted.

    **A bad schema costs the model its hints, never the person the tool.** That is
    the whole rule. Since phase 3 this text goes to a model as the tool's argument
    description, so it is bounded on three axes rather than trusted: it must BE a
    JSON-Schema object (``type: object`` — what every provider's tool translator
    requires, and what MCP itself specifies), it must serialize inside
    :data:`MAX_SCHEMA_BYTES`, and it must not nest past :data:`MAX_SCHEMA_DEPTH`.

    Fail-closed on every axis, including the serializer's own limits:
    ``json.dumps`` raises ``RecursionError`` (not a ``ValueError``) on a document
    deep enough, and that is a refusal like any other rather than an exception a
    person's turn ends on."""
    if not isinstance(value, dict) or value.get("type") != "object":
        return dict(EMPTY_SCHEMA)
    if _too_deep(value, MAX_SCHEMA_DEPTH):
        return dict(EMPTY_SCHEMA)
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError, RecursionError):
        return dict(EMPTY_SCHEMA)
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        return dict(EMPTY_SCHEMA)
    return value


def trim_result(text: str) -> str:
    """One tool's answer, cut to :data:`MAX_RESULT_CHARS` with a plain marker.

    **Never call this on text that has not been redacted yet** (phase-3 decision 2,
    on ``run_command``'s hard-won precedent): every rule in ``agent_core.redaction``
    is anchored on a vendor prefix followed by a minimum body, so a cut through a
    credential leaves a head that matches nothing afterwards and travels intact.
    The cut can defeat the redactor, so the redactor goes first."""
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + RESULT_TRIMMED


def _sse_payload(raw: bytes) -> dict | None:
    """The one JSON-RPC object out of an ``text/event-stream`` answer.

    A MINIMAL PARSER, NOT A SUBSCRIPTION. Streamable HTTP lets a server answer a
    POST with SSE instead of JSON; for ``initialize`` and ``tools/list`` that
    stream carries the single response event and then ends. Everything else in it
    — comments, event names, retry hints, any later event — is ignored, because
    subscribing to a server's stream is a capability phase 2 does not have and
    must not grow by accident."""
    for block in raw.split(b"\n\n"):
        data = b"".join(
            line.split(b":", 1)[1].strip() if b":" in line else b""
            for line in block.splitlines()
            if line.startswith(b"data:")
        )
        if not data:
            continue
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            # RecursionError as well as ValueError: json's parser raises it on a
            # document nested deeply enough, and nesting is the cheapest thing a
            # server can send. It is malformed input, not a defect here.
            continue
        if isinstance(parsed, dict) and "jsonrpc" in parsed:
            return parsed
    return None


def _read_answer(response: httpx.Response, _logical: str) -> _Answer:
    """Turn one HTTP response into an ``_Answer``, or raise the plain sentence.

    Runs INSIDE ``net_vetting``'s stream context, which is why the body is pulled
    chunk by chunk against a cap rather than with ``response.read()``: a server
    that answers with an endless body would otherwise be bounded only by the clock
    and by memory."""
    status = response.status_code
    if status in (401, 403):
        # Phase 2 carries no credential and has nowhere to put one (see the module
        # docstring). This is the whole of Addison's answer to an authenticated
        # server, and it is a sentence rather than a retry.
        raise McpError(NEEDS_SIGN_IN)
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise McpError(TOO_MUCH_BACK)
        chunks.append(chunk)
    raw = b"".join(chunks)
    if status >= 400:
        raise McpError(NOT_A_TOOL_SERVER)
    session_id = response.headers.get("mcp-session-id") or None
    if session_id is not None:
        session_id = _strip_control(session_id)[:200] or None
    if not raw.strip():
        # An accepted notification. The protocol's answer to ``notifications/*``
        # is 202 with no body, so "nothing came back" is a success here and a
        # malformed answer anywhere a payload is actually required.
        return _Answer(session_id, None)
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        payload = _sse_payload(raw)
        if payload is None:
            raise McpError(NOT_A_TOOL_SERVER)
        return _Answer(session_id, payload)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        raise McpError(NOT_A_TOOL_SERVER) from None
    if not isinstance(payload, dict):
        raise McpError(NOT_A_TOOL_SERVER)
    return _Answer(session_id, payload)


class _Session:
    """One refresh against one server: the client, the budget, and the two pieces
    of state the protocol asks a client to carry (session id, negotiated version).

    Deliberately not reusable across servers or across refreshes — a session id is
    one server's, and a budget is one person's wait."""

    def __init__(
        self,
        client: httpx.Client,
        url: str,
        *,
        resolve: Callable[[str], list[str]],
        budget: float,
    ) -> None:
        self._client = client
        self._url = url
        self._resolve = resolve
        self._deadline = time.monotonic() + budget
        self._session_id: str | None = None
        self._version: str | None = None
        self._next_id = 0

    def _remaining(self) -> float:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise McpError(TOOK_TOO_LONG)
        return left

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            # Streamable HTTP: a server may answer either way, and a client that
            # accepts only one gets refused by half of them.
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id is not None:
            # Echoed back on EVERY request once the server issues one. This is the
            # server's own handle on the session, not a credential Addison holds —
            # but it is still only ever sent to the origin that issued it, which is
            # what ``same_origin_only`` below guarantees.
            headers["Mcp-Session-Id"] = self._session_id
        if self._version is not None:
            headers["MCP-Protocol-Version"] = self._version
        return headers

    def _post(self, body: dict) -> _Answer:
        """One JSON-RPC POST through the shared pinned, vetted path."""
        content = json.dumps(body).encode("utf-8")
        try:
            answer = net_vetting.open_vetted(
                self._client,
                self._url,
                resolve=self._resolve,
                on_final=_read_answer,
                sentences=_SENTENCES,
                base_headers=self._headers(),
                # The endpoint policy (rpc/providers' custom-server case): an MCP
                # server on this computer or the LAN is the case v1 optimises for,
                # so a private address is legitimate and a non-standard port is
                # normal. The pin still closes DNS rebinding.
                allow_private=True,
                require_default_port=False,
                max_url_chars=_MAX_URL_CHARS,
                max_redirects=_MAX_REDIRECTS,
                timeout=_SOCKET_TIMEOUT_SECONDS,
                total_timeout=self._remaining(),
                method="POST",
                content=content,
                # A hop off this origin is the server sending Addison somewhere the
                # person never typed, carrying the request body and the session id.
                same_origin_only=True,
            )
        except net_vetting.VettingError as exc:
            raise McpError(str(exc)) from None
        if answer.session_id is not None:
            self._session_id = answer.session_id
        return answer

    def _call(self, method: str, params: dict | None = None) -> dict:
        """One request/response pair. Returns the ``result`` object.

        A JSON-RPC ``error`` from the server is one plain sentence and never the
        server's own message — that text is attacker-controlled and would be
        rendered on the page a person reads to decide what to trust."""
        self._next_id += 1
        body: dict = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        answer = self._post(body)
        payload = answer.payload
        if payload is None or payload.get("jsonrpc") != "2.0":
            raise McpError(NOT_A_TOOL_SERVER)
        if payload.get("id") != self._next_id:
            # The answer to a different question is not an answer.
            raise McpError(NOT_A_TOOL_SERVER)
        if "error" in payload:
            raise McpError(NOT_A_TOOL_SERVER)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise McpError(NOT_A_TOOL_SERVER)
        return result

    def notify(self, method: str) -> None:
        """A notification — no id, so no answer is expected or read."""
        self._post({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> str:
        """The handshake. Returns the version both sides will speak.

        Addison offers the current version; a server answering with an OLDER one
        from the supported list is accepted, which is the protocol's own
        negotiation. Anything else fails closed: guessing at an unknown version
        means parsing a shape nobody has agreed on."""
        result = self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # Addison consumes tools. It offers a server nothing — no roots, no
                # sampling, no elicitation — and saying so plainly here is what
                # keeps a server from asking this process to do any of them.
                "capabilities": {},
                "clientInfo": dict(_CLIENT_INFO),
            },
        )
        version = result.get("protocolVersion")
        if not isinstance(version, str) or version not in _SUPPORTED_VERSIONS:
            raise McpError(UNKNOWN_VERSION)
        self._version = version
        return version

    def list_tools(self) -> tuple[tuple[DiscoveredTool, ...], int]:
        """Every page of ``tools/list``, capped, cleaned, deduplicated.

        Returns the admitted tools and how many were refused at this boundary. The
        cursor walk stops on a repeat as well as on the page ceiling: ``nextCursor``
        is the server's string, so a server that returns the same one forever is a
        loop Addison has to end itself."""
        admitted: list[DiscoveredTool] = []
        seen_names: set[str] = set()
        seen_cursors: set[str] = set()
        skipped = 0
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            result = self._call("tools/list", {"cursor": cursor} if cursor else {})
            entries = result.get("tools")
            if not isinstance(entries, list):
                raise McpError(NOT_A_TOOL_SERVER)
            for entry in entries:
                if len(admitted) >= MAX_TOOLS_PER_SERVER:
                    skipped += 1
                    continue
                if not isinstance(entry, dict):
                    skipped += 1
                    continue
                name = _clean_name(entry.get("name"))
                if name is None or name in seen_names:
                    # A duplicate is refused for the same reason a collision is:
                    # two tools cannot share one registry id, and picking a winner
                    # would be Addison deciding which stranger's tool wins.
                    skipped += 1
                    continue
                seen_names.add(name)
                admitted.append(
                    DiscoveredTool(
                        name,
                        _clean_description(entry.get("description")),
                        # Phase 3 keeps the schema — bounded (see ``_clean_schema``)
                        # — because a model cannot form a call to a tool whose
                        # arguments nobody described. Phase 2 deliberately did not,
                        # for the equal and opposite reason: it called nothing, so
                        # an unused JSON Schema from a stranger was text held for
                        # no purpose.
                        _clean_schema(entry.get("inputSchema")),
                    )
                )
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            if next_cursor in seen_cursors or len(admitted) >= MAX_TOOLS_PER_SERVER:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return tuple(admitted), skipped

    def call_tool(self, name: str, arguments: dict) -> CallResult:
        """One ``tools/call``, parsed down to text.

        **Only ``text`` content items are read, and that is phase 3's scope**
        (phase 4 owns content-type breadth and ``structuredContent``): an image or
        an embedded resource is silently not carried rather than half-carried, and
        an answer with nothing textual in it says so in one plain sentence rather
        than reaching a model as a tool that quietly did nothing.

        A malformed answer FAILS CLOSED, like every other shape this module reads:
        ``content`` that is not a list is not a result Addison will guess at."""
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        items = result.get("content")
        if not isinstance(items, list):
            raise McpError(NOT_A_TOOL_SERVER)
        parts = [
            item["text"]
            for item in items
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        text = "\n".join(parts).strip()
        return CallResult(
            text=text or NOTHING_TO_SHOW,
            # The server's own claim about its own call. Believed here because it
            # says nothing about permission or safety — only whether the thing the
            # model asked for worked — and a wrong answer costs one honest-looking
            # failure, not a capability.
            is_error=result.get("isError") is True,
        )


def discover_tools(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolve: Callable[[str], list[str]] | None = None,
    budget: float = REFRESH_BUDGET_SECONDS,
) -> Discovery:
    """Connect to one MCP server and list what it offers. Raises :class:`McpError`
    with one plain sentence on any failure.

    ``client`` and ``resolve`` are seams, not options: ``httpx.MockTransport``
    intercepts below name resolution, so an injectable resolver is the only way to
    test the vetting-and-pinning path offline (``net_vetting``'s own reasoning).
    The default client is built and closed here with ``trust_env=False`` — an MCP
    address is one the person typed, and an ambient proxy variable must not
    silently become part of the path it takes."""
    owned = client is None
    active = client or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        session = _Session(
            active,
            url,
            resolve=resolve or net_vetting.resolve_host,
            budget=budget,
        )
        version = session.initialize()
        # Order matters and the protocol says so: a server may reject work that
        # arrives before it has been told the client is ready.
        session.notify("notifications/initialized")
        tools, skipped = session.list_tools()
        return Discovery(tools=tools, skipped=skipped, protocol_version=version)
    finally:
        if owned:
            active.close()


def call_tool(
    url: str,
    name: str,
    arguments: dict,
    budget: float = CALL_BUDGET_SECONDS,
    *,
    client: httpx.Client | None = None,
    resolve: Callable[[str], list[str]] | None = None,
) -> CallResult:
    """Run ONE tool on one server. Raises :class:`McpError` with one plain sentence
    on any failure — unreachable, too slow, a sign-in Addison can't do, an answer it
    can't make sense of.

    **ONE CALL, ONE SESSION, ONE BUDGET** (phase-3 decision 3). A fresh
    initialize → initialized → call runs inside a single deadline, and the session
    ends with the call. No long-lived connection, no background session, nothing
    reused across calls: a session id is one server's handle on one person's wait,
    and a pool of them would be state that outlives the turn that authorised it —
    for a server whose author nobody here has met. A server needing state ACROSS
    calls is a v2 conversation, not a connection quietly left open.

    The cost is honest and was accepted: every call pays for a handshake. That is
    two extra round trips against a bounded budget, and it buys a call that cannot
    inherit anything from a call the person approved earlier.

    ``client``/``resolve`` are the same test seams ``discover_tools`` documents."""
    owned = client is None
    active = client or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        session = _Session(
            active,
            url,
            resolve=resolve or net_vetting.resolve_host,
            budget=budget,
        )
        session.initialize()
        session.notify("notifications/initialized")
        return session.call_tool(name, arguments)
    finally:
        if owned:
            active.close()
