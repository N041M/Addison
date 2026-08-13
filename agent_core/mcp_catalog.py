"""What a tool server offered, how it enters the one tool registry, and what
happens when one of its tools is actually run (step 7, PHASES 2–4;
[docs/step-7-mcp-plan.md](../docs/step-7-mcp-plan.md) §3 owns the admission rules
this implements, §4.3 the dispatch and §4.4 the shape of what comes back).

``mcp_client.py`` speaks the protocol; this module decides what happens to what it
brings back. Three things live here and nothing else does:

**1. Admission.** Every discovered tool registers through the SAME
``ToolRegistry`` the orchestrator and the routine engine share — never a second
registry, never a side channel (SAFE invariant 3's reasoning, and §4.12's own
promise). Registration is ``dev_only=True`` (= ``open_only`` + ``allow_missing_undo``),
tier **HIGH and destructive unconditionally**: a server declares its own risk and
that cannot be taken on trust, so refining the tier is what the promoted-allowlist
decision is for, and guessing it now would put the trust hole somewhere less
visible. Because a HIGH tool with no ``undo()`` can never be LOW, it can never
reach the SAFE view whatever a server claims — invariant 2 is not weakened, it is
the thing doing the work.

**2. The in-memory catalog.** A server's discovered tools are HELD IN MEMORY and
never written to SQLite (scoping decision 3, 2026-08-07). A catalog is the
server's truth rather than Addison's configuration, and ``mcp_servers`` is
snapshot-CAPTURED — persisting a stranger's names and prose there would copy
attacker-controlled text into every later snapshot payload and plaintext sidecar,
for no gain, since the only honest way to know what a server offers today is to
ask it. After a restart a row says it has not been checked yet, which is true.

**3. Dispatch** (phase 3). :class:`McpTool`'s ``execute`` runs the call — through
the ordinary registry and the ordinary gate, with NO special case anywhere in
either. HIGH + destructive is the strongest thing a tool can declare itself to be,
so every call goes to the gate as a destructive one and SAFE never sees the tool at
all. **What the gate then DOES with a destructive call is the gate's own business,
and the Custom profile can tune it** — ``destructive_card='session'`` cards the
first call of a tool and remembers the answer for the session, and
``auto_grant_scope='everything'`` cards none of them (``permissions/gate.py``,
``policy.GuardConfig``). Those are prompting guards the owner designed and this
module neither knows about nor overrides; what it guarantees is the INPUT to that
decision — a stranger's tool arrives at the gate as HIGH and destructive, every
time, and no server's claim about itself can lower it. Three things happen inside
``execute`` and they are the whole of what phase 3 added to a call:

  * **The address is resolved at the moment of use**, never remembered from the
    discovery that registered the tool. A server the person removed or renamed in
    between refuses cleanly; nothing here can call a stale address.
  * **ONE call, ONE session, ONE budget** (decision 3) — the deadline lives here,
    at the call, because that is where the person is waiting.
  * **The answer crosses the redaction seam and then the caps, in that order**
    (decisions 1 and 2, and phase 4's structured channel). The order is
    ``run_command``'s hard-won lesson: a cut through a credential leaves a head the
    redactor no longer matches. What gets cut, what is disclosed rather than
    carried, and what the budget is shared between are all phase 4's, and they live
    in ``mcp_client.compose_result`` — this module redacts and hands it over.

**Namespaced ids are a safety requirement, not tidiness** (§3). A server can
declare a tool called ``save_file``; registered bare it would shadow the native
one, and every grant, audit row and risk rule keyed by that id would silently
point at a stranger's code. So ids are ``mcp:<server>:<tool>`` and a collision
REFUSES that tool — skipped and reported — rather than replacing anything.

:data:`MCP_TOOLS_ARE_CALLABLE` is the one constant the phases turn on, and phase 3
turned it on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agent_core.mcp_client import (
    CALL_BUDGET_SECONDS,
    CallResult,
    DiscoveredTool,
    McpError,
    compose_result,
)
from agent_core.policy import PolicyMode
from agent_core.redaction import redact
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import (
    DEV_ONLY_REFUSAL,
    NOT_CALLABLE_REFUSAL,
    ToolRegistry,
)

#: Every discovered id starts with this. One constant, because the SAFE-view test,
#: the model-visibility test and the surfaces all ask the same question of it.
MCP_ID_PREFIX = "mcp:"

#: **The phase gate, ON since phase 3 (2026-08-07).** Phase 2 was connect +
#: discovery: the person saw what a server offered, the model was never told, and
#: both dispatch paths refused. Phase 3 turned this ONE constant on and implemented
#: ``McpTool.execute`` — deliberately, in daylight, rather than by an omission
#: somewhere quietly ceasing to hold.
#:
#: What it still governs, so that turning it back off is a complete answer rather
#: than half of one: the ``not_callable`` registration flag (which keeps an
#: ``mcp:`` id out of every mode's ``visible_tools``, so the model is never offered
#: one), and ``McpTool.execute``'s own innermost refusal. The two dispatch-site
#: checks (``refuse_if_not_callable``) stay exactly where they are and go quiet for
#: MCP ids on their own — they are the mechanism this constant operates through,
#: not a phase-2 leftover, and the next externally-sourced tool inherits them.
#:
#: ``tests/doc_claims.py::MCP_TOOLS_ARE_NOT_CALLABLE`` is this same fact enforced
#: in prose. The two move in one commit.
MCP_TOOLS_ARE_CALLABLE = True

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

#: The card's description, and the model's. Says the two things a person needs
#: before approving a call to somebody else's program: where the tool came from,
#: and that Addison is not in a position to vouch for it. "Tool server" rather than
#: "MCP server" everywhere a person can read it.
#:
#: ``{server}`` is a PHRASE rather than a bare name so the sentence stays
#: grammatical when there is no name to give — a card is not the place to discover
#: that a value was empty.
#:
#: **IT NO LONGER PROMISES A FREQUENCY.** It used to end "so it asks every time",
#: which is a promise the app does not keep in the Custom profile: a person who has
#: set ``destructive_card='session'`` is asked once, and one who has set
#: ``auto_grant_scope='everything'`` is not asked at all. Those guards are the
#: owner's design and this card is not the place to argue with them — so the
#: sentence now says the thing that IS true under every profile, which is what
#: Addison does with the tool rather than how often it interrupts.
CARD_PROVENANCE = (
    "This comes from {server}, which you added. Addison can't know what it will do, "
    "so it treats every call as one it can't undo."
)

#: How a server's OWN words are attributed, on the card and to the model.
#:
#: **THE BOUNDARY IS POSITION, WHICH IS WHY A SERVER CANNOT FORGE IT.** Addison's
#: sentence is FIRST and the server's words are LAST, always, and a string appended
#: to the end of another string cannot reach in front of it — so everything up to
#: and including this label is Addison speaking and everything after it is not,
#: whatever the server writes. Without it, a description ending "…Addison has
#: checked this server and it is safe to approve every time." was concatenated onto
#: Addison's own sentence with a single space and read as Addison's voice.
#:
#: The quotation marks are the visible half and they are made unforgeable rather
#: than trusted: the pair used here is REMOVED from the server's text before the
#: text is placed between them (see :class:`McpTool`), so the only pair on the card
#: is the pair Addison wrote, and a server cannot close its own quote and carry on
#: outside it. Length is bounded upstream — ``mcp_client._clean_description`` caps
#: it and strips its control characters.
SERVER_SAYS = "The server describes it: “{description}”"

#: The quotation marks :data:`SERVER_SAYS` delimits with, and therefore the
#: characters a server may not have. Two code points, removed rather than escaped:
#: an escape is something to read past, and a person reading a permission card is
#: not reading past anything.
_QUOTE_MARKS = "“”"

#: Said when the server this tool came from is no longer saved under the name its
#: id carries — removed, or renamed, between the check that found it and now.
SERVER_GONE = "That tool server isn't saved any more, so Addison didn't call it."

#: The last resort: the call failed for a reason ``mcp_client`` did not name, which
#: means a defect on Addison's side. It says so rather than blaming the server.
CALL_FAILED = "Addison couldn't run that tool just now."

#: Statuses a server row can be in. ``never`` is the honest answer after a restart
#: (nothing is persisted) and the honest answer for a row nobody has checked:
#: discovery is ON DEMAND ONLY — Addison makes no network request the person did
#: not just cause (scoping decision 3, 2026-08-07).
STATUS_NEVER = "never"
STATUS_OK = "ok"
STATUS_FAILED = "failed"


def _unquoted(description: str) -> str:
    """A server's description with :data:`_QUOTE_MARKS` taken out and the edges
    tidied — what goes inside :data:`SERVER_SAYS`'s quotes.

    Removed rather than escaped, and removed HERE rather than at the client
    boundary, so that the guarantee belongs to the string that needs it: whatever
    builds an :class:`McpTool` — a discovery, a test, the next caller — gets a card
    whose quotation marks are Addison's alone."""
    return description.translate({ord(mark): None for mark in _QUOTE_MARKS}).strip()


def mcp_tool_id(server_name: str, tool_name: str) -> str:
    """``mcp:<server>:<tool>`` — the one place this string is built.

    The server NAME rather than its row id, because this id is read by a person in
    a permission card and in the audit log, and a UUID there answers "which server
    is this?" with a shrug. Names are unique case-insensitively (phase 1's UNIQUE
    index), which is what makes them safe to key on."""
    return f"{MCP_ID_PREFIX}{server_name}:{tool_name}"


class McpTool:
    """One tool a server offered, as the registry sees it — and, since phase 3, the
    thing that actually calls it.

    Constructed with the two seams dispatch needs and neither of which this module
    may reach for itself (the module-boundary rule, and a source test on the import
    graph): ``endpoint_for`` answers "where does this server live RIGHT NOW", and
    ``call_tool`` is the network. Both default to None, in which case ``execute``
    refuses — a tool wired to nothing must say so, never quietly do nothing."""

    def __init__(
        self,
        tool_id: str,
        label: str,
        description: str,
        *,
        schema: dict | None = None,
        server_id: str = "",
        server_name: str = "",
        tool_name: str = "",
        endpoint_for: Callable[[str, str], str | None] | None = None,
        call_tool: Callable[[str, str, dict, float], CallResult] | None = None,
    ) -> None:
        # ADDISON'S WORDS FIRST, THE SERVER'S LAST, ATTRIBUTED AND QUOTED. Both
        # audiences read this string and each needs a different half of it: the
        # model needs to know what the tool does, and the person approving the card
        # needs to know who wrote it and that Addison cannot vouch for it. The card
        # shows exactly this text (main._on_permission_request), which is also why
        # this tool declares no ``permission_detail`` — a detail REPLACES the
        # description on the card, and the provenance is the part that must not be
        # replaceable.
        #
        # The order is the boundary (see ``SERVER_SAYS``): the server's text is
        # appended, so it can never appear ahead of Addison's sentence, and its
        # quotation marks are taken out here so it cannot close the pair Addison
        # opened and keep writing outside it. A server with nothing to say gets no
        # attribution line at all, rather than Addison quoting silence.
        said = _unquoted(description)
        self.definition = ToolDefinition(
            id=tool_id,
            label=label,
            description=" ".join(
                part
                for part in (
                    CARD_PROVENANCE.format(
                        server=(
                            f"the tool server {server_name}" if server_name else "a tool server"
                        )
                    ),
                    SERVER_SAYS.format(description=said) if said else "",
                )
                if part
            ),
            # HIGH and destructive unconditionally (§3). Not a judgement about this
            # particular tool — a judgement about who described it.
            risk_tier=RiskTier.HIGH,
            # The server's own inputSchema, bounded at the client boundary
            # (mcp_client._clean_schema) and copied no further. A model cannot form
            # a call to a tool whose arguments nobody described; a schema that
            # failed the bounds is replaced by the empty one, so a bad schema costs
            # the model its hints rather than costing the person the tool.
            parameters_schema=schema if schema is not None else {"type": "object", "properties": {}},
        )
        self._server_id = server_id
        self._server_name = server_name
        self._tool_name = tool_name or tool_id
        self._endpoint_for = endpoint_for
        self._call_tool = call_tool

    def is_destructive(self, args: dict) -> bool:
        """Every call reaches the gate as a destructive one (§3). A server's own
        risk claim is exactly the thing v1 refuses to trust, so there is no argument
        and no annotation that makes a call to somebody else's program anything
        milder than this.

        What the gate does NEXT is the gate's, and under the Custom profile's guards
        that can be one card for the session or none at all — see the module
        docstring. This answer is the input to that decision and never a claim about
        its outcome."""
        return True

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        """Call the tool and hand back its answer, redacted and capped.

        Four refusals come first, and none of them is the gate's job — the gate has
        already said yes by the time this runs:

          1. **The phase gate.** With :data:`MCP_TOOLS_ARE_CALLABLE` off this is the
             innermost of three layers, on ``run_command``'s belt-and-suspenders
             precedent. Turning the constant off must stop dispatch everywhere, not
             just where the model can see.
          2. **Mode.** SAFE never sees an ``mcp:`` id and both dispatch paths refuse
             one outside OPEN, so reaching here in SAFE means two boundaries were
             already crossed; this refuses anyway, like ``run_command`` does.
          3. **Wiring.** No endpoint resolver and no client means a tool registered
             by something that cannot call it.
          4. **The address, resolved NOW.** A server removed or renamed since the
             check that registered this tool refuses cleanly. The alternative — an
             address captured at discovery — is a call to wherever that server used
             to be, made on the strength of a row that no longer exists.

        Then the answer: **redacted, then shaped, in that order** — every channel,
        every time (see ``mcp_client.compose_result``, which does all of the cutting
        and none of the redacting). A secret in a nested field is a secret, and a
        cut through the structured cap defeats the redactor exactly as a cut through
        the text cap does.

        **THE TWO CHANNELS CROSS THE REDACTOR IN TWO PLACES, AND THAT IS THE FIX
        RATHER THAN THE PROBLEM.** The text is redacted here, one line above the
        only thing that cuts. ``structuredContent`` is redacted a string at a time
        inside ``mcp_client._structured_answer``, BEFORE it is serialized, because
        the serializer escapes a NUL into six visible characters and a newline into
        two — and every rule in ``agent_core.redaction`` matches a contiguous
        pattern, so redacting the serialized form let a key split by a control
        character through while the same key in the text channel was caught. It also
        made ``redacted_kinds`` empty for a leak that happened, which is the audit
        row denying the one thing it exists to record.

        The kinds from BOTH channels ride back on the ``ToolResult`` so both
        dispatch paths can put them in the audit row, which is the only durable
        record that a credential came back from somebody else's program."""
        if not MCP_TOOLS_ARE_CALLABLE:
            return ToolResult(
                success=False, content=NOT_CALLABLE_REFUSAL, audit_outcome="not_callable"
            )
        if context.policy_mode is not PolicyMode.OPEN:
            return ToolResult(success=False, content=DEV_ONLY_REFUSAL, audit_outcome="dev_only")
        if self._endpoint_for is None or self._call_tool is None:
            return ToolResult(success=False, content=CALL_FAILED, audit_outcome="failed")
        url = self._endpoint_for(self._server_id, self._server_name)
        if not url:
            return ToolResult(success=False, content=SERVER_GONE, audit_outcome="failed")
        try:
            answer = self._call_tool(url, self._tool_name, dict(args or {}), CALL_BUDGET_SECONDS)
        except McpError as exc:
            # Already one plain sentence written for a person, and never the
            # server's own words. A call that never landed is 'failed' rather than
            # 'granted': the gate said yes and nothing happened, and those are
            # different rows to whoever later asks what Addison actually did.
            return ToolResult(success=False, content=str(exc), audit_outcome="failed")
        except Exception:
            # A defect on Addison's side (an argument that will not serialize, a
            # client that raised something new). One sentence, no stack trace, and
            # the turn continues — a tool that crashes a turn is worse than a tool
            # that fails.
            return ToolResult(success=False, content=CALL_FAILED, audit_outcome="failed")
        # THE SEAM for the text, immediately before the only thing that cuts.
        # Adjacency is the enforcement here: there is nowhere between these lines
        # for a cap to be added by somebody who has not read why. The structured
        # channel crossed the same redactor before it was serialized and arrives
        # carrying what that found — see this method's docstring for why it cannot
        # be done on this side of the serializer.
        scrubbed = redact(answer.text)
        return ToolResult(
            success=not answer.is_error,
            content=compose_result(
                text=scrubbed.text,
                structured=answer.structured,
                structured_unreadable=answer.structured_unreadable,
                images=answer.images,
                sounds=answer.sounds,
                files=answer.files,
                other=answer.other,
            ),
            redacted_kinds=scrubbed.kinds + answer.structured_kinds,
            # Somebody else's program wrote every byte of this. Data, never
            # behaviour: this method's four refusals and the gate above it are
            # unchanged by it, and the screening it enables happens once, in the
            # orchestrator, on the far side of the cap this line's `compose_result`
            # applies.
            content_origin="external",
        )


@dataclass
class ServerCatalog:
    """One server's discovery state — the whole of what the surfaces render.

    ``error`` is one plain sentence from ``mcp_client``, never a server's own text.
    ``refused`` names the tools an id collision turned away, so "this server offers
    nine tools, one of which Addison won't take" is answerable on the page rather
    than only in a log."""

    status: str = STATUS_NEVER
    tools: tuple[DiscoveredTool, ...] = ()
    checked_at: int | None = None
    error: str | None = None
    skipped: int = 0
    refused: tuple[str, ...] = ()


@dataclass
class McpCatalog:
    """Discovery state for every configured server, in memory only.

    Owns the registry side-effects too, so "what Addison believes a server offers"
    and "what is registered for that server" can never drift apart: both change in
    the same call or neither does.

    ``endpoint_for`` and ``call_tool`` are the two dispatch seams, handed to every
    tool this catalog registers. They are INJECTED rather than imported because
    this module may not reach the store (the address lives in SQLite, and the RPC
    layer owns that lookup) and because the network has to be substitutable in a
    test — the same reason ``_mcp_discover`` is a seam one layer up. Left unset,
    every discovered tool registers and refuses to run, which is the correct
    behaviour for a catalog nobody wired dispatch into."""

    _by_server: dict[str, ServerCatalog] = field(default_factory=dict)
    _ids_by_server: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: (server_id, server_name) -> the server's CURRENT address, or None if it is
    #: no longer saved under that name. Asked at the moment of use, never cached.
    endpoint_for: Callable[[str, str], str | None] | None = None
    #: (url, tool_name, arguments, budget) -> CallResult. ``mcp_client.call_tool``
    #: in the running app; a MockTransport-backed one in tests.
    call_tool: Callable[[str, str, dict, float], CallResult] | None = None

    def state(self, server_id: str) -> ServerCatalog:
        """This server's state — a never-checked one for anything unseen, which is
        the correct answer for a fresh row and after a restart alike."""
        return self._by_server.get(server_id, ServerCatalog())

    def registered_ids(self, server_id: str) -> tuple[str, ...]:
        return self._ids_by_server.get(server_id, ())

    def known_servers(self) -> tuple[str, ...]:
        """Every server this catalog holds anything for — state, registrations, or
        both. Snapshotted into a tuple because the caller (the post-restore resync)
        forgets servers while walking it."""
        return tuple({**self._by_server, **self._ids_by_server}.keys())

    def _drop_registrations(self, registry: ToolRegistry, server_id: str) -> None:
        for tool_id in self._ids_by_server.pop(server_id, ()):
            try:
                registry.unregister(tool_id)
            except ValueError:
                # Only reachable if something else already took the id out; the
                # catalog's job is to end up with nothing registered for this
                # server, and it has.
                pass

    def record_success(
        self,
        registry: ToolRegistry,
        *,
        server_id: str,
        server_name: str,
        tools: tuple[DiscoveredTool, ...],
        skipped: int,
        checked_at: int,
    ) -> ServerCatalog:
        """Replace this server's registrations with what it just offered.

        ATOMIC FOR THIS SERVER, and only this server: the previous ids come out
        before the new ones go in, so a tool a server has stopped offering stops
        being registered in the same call, and a re-discovery never leaves two
        generations of one server's tools behind. No other server is touched — a
        refresh is one row's business.

        **ATOMIC ALSO MEANS "OR NOTHING", AND THAT NEEDED SAYING IN CODE.** The
        catalog's record of what it registered is written at the END of the loop, so
        a ``register`` that raised halfway through used to leave the ids already
        taken in the registry and in NO id list — which made them unremovable by
        ``forget``, by ``mcp.remove`` and by a resync, and made every later refresh
        of the same server refuse its own tools as collisions, for ever. So a
        failure now unwinds what this call registered before it re-raises: the two
        sides move together or neither does, which is the property the docstring
        above was already claiming.

        A collision (with a native tool, or with another server's already-taken id)
        REFUSES that tool. Never replaces: replacing is the failure §3 exists to
        prevent, and it would be silent."""
        self._drop_registrations(registry, server_id)
        registered: list[str] = []
        refused: list[str] = []
        try:
            self._register_all(registry, server_id, server_name, tools, registered, refused)
        except BaseException:
            for tool_id in registered:
                try:
                    registry.unregister(tool_id)
                except ValueError:
                    pass
            self._ids_by_server[server_id] = ()
            # BOTH SIDES, or the unwind trades one drift for another. Leaving the
            # previous check's `ServerCatalog` in place would have this server
            # reporting `ok` with a list of tools while its id list is empty — the
            # surfaces would name tools dispatch could not find, which is the exact
            # disagreement this class exists to make impossible. The check did not
            # finish, so the honest record is that it has not been checked.
            self._by_server.pop(server_id, None)
            raise
        self._ids_by_server[server_id] = tuple(registered)
        state = ServerCatalog(
            status=STATUS_OK,
            tools=tuple(tools),
            checked_at=checked_at,
            error=None,
            skipped=skipped,
            refused=tuple(refused),
        )
        self._by_server[server_id] = state
        return state

    def _register_all(
        self,
        registry: ToolRegistry,
        server_id: str,
        server_name: str,
        tools: tuple[DiscoveredTool, ...],
        registered: list[str],
        refused: list[str],
    ) -> None:
        """The loop :meth:`record_success` unwinds. Appends to the two lists as it
        goes so that a caller catching an exception knows exactly what to take back
        out — which is the whole reason this is a separate method rather than an
        inline ``for``."""
        for tool in tools:
            tool_id = mcp_tool_id(server_name, tool.name)
            if registry.has(tool_id):
                refused.append(tool.name)
                continue
            registry.register(
                McpTool(
                    tool_id,
                    _label_for(server_name, tool.name),
                    tool.description,
                    schema=tool.schema,
                    # The server's IDENTITY travels with the tool, not its address:
                    # the address is looked up again at the moment of use, so a
                    # server removed or renamed between this check and the call
                    # refuses instead of being reached.
                    server_id=server_id,
                    server_name=server_name,
                    # The name the SERVER uses, which is not the registry id — the
                    # id is namespaced, and sending the namespaced form back to the
                    # server would name a tool it has never heard of.
                    tool_name=tool.name,
                    endpoint_for=self.endpoint_for,
                    call_tool=self.call_tool,
                ),
                # open_only + allow_missing_undo. An MCP tool has no undo() and
                # never will, which is exactly the shape the exemption exists for.
                dev_only=True,
                # A server can change its mind, and mcp.remove must take its tools
                # with it. Native registrations stay permanent.
                removable=True,
                # Phase 2's first layer: hidden from the model in EVERY mode, and
                # refused at both dispatch sites. One constant, flipped once.
                not_callable=not MCP_TOOLS_ARE_CALLABLE,
            )
            registered.append(tool_id)

    def record_failure(
        self, registry: ToolRegistry, *, server_id: str, error: str, checked_at: int
    ) -> ServerCatalog:
        """A check that did not land. The server's previous tools are unregistered
        rather than left standing: a row that says "couldn't reach this" while its
        tools are still listed elsewhere is the app disagreeing with itself on the
        one page a person reads to find out what Addison can reach."""
        self._drop_registrations(registry, server_id)
        state = ServerCatalog(status=STATUS_FAILED, checked_at=checked_at, error=error)
        self._by_server[server_id] = state
        return state

    def forget(self, registry: ToolRegistry, server_id: str) -> None:
        """The server is gone (``mcp.remove``): drop its tools and its state."""
        self._drop_registrations(registry, server_id)
        self._by_server.pop(server_id, None)


def _label_for(server_name: str, tool_name: str) -> str:
    """What a permission card would call this. Names the server first, because
    "from a tool server you added" is the part a person needs and the part the
    native label never has to say."""
    return f"{tool_name} · from {server_name}"
