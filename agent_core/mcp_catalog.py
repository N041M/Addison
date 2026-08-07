"""What a tool server offered, and how it enters the one tool registry
(step 7, PHASE 2; [docs/step-7-mcp-plan.md](../docs/step-7-mcp-plan.md) §3 owns
the admission rules this implements).

``mcp_client.py`` speaks the protocol; this module decides what happens to what it
brings back. Two things live here and nothing else does:

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

**Namespaced ids are a safety requirement, not tidiness** (§3). A server can
declare a tool called ``save_file``; registered bare it would shadow the native
one, and every grant, audit row and risk rule keyed by that id would silently
point at a stranger's code. So ids are ``mcp:<server>:<tool>`` and a collision
REFUSES that tool — skipped and reported — rather than replacing anything.

**Nothing is callable in phase 2**, and :data:`MCP_TOOLS_ARE_CALLABLE` is the one
deliberate thing phase 3 flips.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_core.mcp_client import DiscoveredTool
from agent_core.tools.base import ExecutionContext, RiskTier, ToolDefinition, ToolResult
from agent_core.tools.registry import NOT_CALLABLE_REFUSAL, ToolRegistry

#: Every discovered id starts with this. One constant, because the SAFE-view test,
#: the model-visibility test and the surfaces all ask the same question of it.
MCP_ID_PREFIX = "mcp:"

#: **The phase gate.** Phase 2 is connect + discovery: the person sees what a
#: server offers, the model is never told, and both dispatch paths refuse. Phase 3
#: (dispatch, through the existing gate, with ``tool_audit`` on every outcome and a
#: per-call deadline) flips this ONE constant and implements ``McpTool.execute`` —
#: deliberately, in daylight, rather than by an omission somewhere quietly ceasing
#: to hold.
MCP_TOOLS_ARE_CALLABLE = False

#: Statuses a server row can be in. ``never`` is the honest answer after a restart
#: (nothing is persisted) and the honest answer for a row nobody has checked:
#: discovery is ON DEMAND ONLY — Addison makes no network request the person did
#: not just cause (scoping decision 3, 2026-08-07).
STATUS_NEVER = "never"
STATUS_OK = "ok"
STATUS_FAILED = "failed"


def mcp_tool_id(server_name: str, tool_name: str) -> str:
    """``mcp:<server>:<tool>`` — the one place this string is built.

    The server NAME rather than its row id, because this id is read by a person in
    a permission card and in the audit log, and a UUID there answers "which server
    is this?" with a shrug. Names are unique case-insensitively (phase 1's UNIQUE
    index), which is what makes them safe to key on."""
    return f"{MCP_ID_PREFIX}{server_name}:{tool_name}"


class McpTool:
    """One tool a server offered, as the registry sees it.

    ``execute`` REFUSES. That is not a placeholder to be forgotten: phase 2 ships
    no dispatch at all, so a body that "did the call" would be the one part of this
    step nobody asked for. It is also the innermost of the three layers keeping
    phase 2 honest (model-invisible, refused at both dispatch sites, refused here),
    on ``run_command``'s belt-and-suspenders precedent. Phase 3 replaces this body
    and flips :data:`MCP_TOOLS_ARE_CALLABLE`."""

    def __init__(self, tool_id: str, label: str, description: str) -> None:
        self.definition = ToolDefinition(
            id=tool_id,
            label=label,
            description=description,
            # HIGH and destructive unconditionally (§3). Not a judgement about this
            # particular tool — a judgement about who described it.
            risk_tier=RiskTier.HIGH,
            # Phase 2 does not call anything, so it does not need the server's
            # inputSchema and does not keep it: an unused JSON Schema from a
            # stranger is text held for no reason. Phase 3 adds it WITH its own
            # bounds, which is a decision that belongs beside dispatch.
            parameters_schema={"type": "object", "properties": {}},
        )

    def is_destructive(self, args: dict) -> bool:
        """Every call cards, per invocation (§3). A server's own risk claim is
        exactly the thing v1 refuses to trust."""
        return True

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=False, content=NOT_CALLABLE_REFUSAL)


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
    the same call or neither does."""

    _by_server: dict[str, ServerCatalog] = field(default_factory=dict)
    _ids_by_server: dict[str, tuple[str, ...]] = field(default_factory=dict)

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

        A collision (with a native tool, or with another server's already-taken id)
        REFUSES that tool. Never replaces: replacing is the failure §3 exists to
        prevent, and it would be silent."""
        self._drop_registrations(registry, server_id)
        registered: list[str] = []
        refused: list[str] = []
        for tool in tools:
            tool_id = mcp_tool_id(server_name, tool.name)
            if registry.has(tool_id):
                refused.append(tool.name)
                continue
            registry.register(
                McpTool(tool_id, _label_for(server_name, tool.name), tool.description),
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
