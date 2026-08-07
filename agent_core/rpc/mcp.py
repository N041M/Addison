"""mcp.* handlers — the external MCP servers Addison consumes as a CLIENT
(step 7, phases 1–2). Addison is never an MCP server or a gateway.

**Phase 2 can SEE a server's tools and still cannot use one.** Phase 1 stored
addresses; phase 2 adds ``mcp.refresh``, which connects, lists what the server
offers, and registers each tool namespaced and dev-only through the ONE registry.
There is still no dispatch: an ``mcp:`` id is kept out of the tool list the model
is offered and refused at both dispatch sites, so what shipped is visibility, not
capability. Dispatch is phase 3 of
[docs/step-7-mcp-plan.md](../../docs/step-7-mcp-plan.md), which owns the phase
order; ``agent_core/mcp_catalog.py`` owns the one constant that turns it on.

**This module still holds no protocol and no registration.** It reads rows, calls
``agent_core/mcp_client.py`` for the network and ``agent_core/mcp_catalog.py`` for
admission, and turns the result into a payload. That split is why the phase-1
structural test still passes over this file unchanged: nothing here can start a
process, cross the shell bridge, or register a tool by itself.

**Transport is HTTP only for v1** (owner decision 2026-08-06). That is why this
module lives in the Agent Core at all: the core already speaks HTTPS to providers
through ``httpx`` and needs no new shell surface, whereas stdio would mean the core
launching an arbitrary executable — the process with no OS permissions of its own,
outside the seatbelt step 5.5 built. So a row stores a URL and never a command, and
nothing in this phase can spawn a process. The plan's §5 keeps stdio as the
documented later option.

**Discovery is ON DEMAND ONLY** (scoping decision 3, 2026-08-07). No connection at
core start, no background refresh, no timer: a saved row stays inert until somebody
presses "Check now". Addison makes no network request the person did not just
cause — the same temperament as reversible config. The discovered catalog is held
in memory and never written to SQLite, so after a restart a row honestly says it
has not been checked.

**Developer-only surface.** MCP is dev-only for v1, so ``mcp.add`` and
``mcp.refresh`` refuse outside OPEN mode — refresh especially, because it is the
one that reaches the network. ``mcp.list`` answers in every mode on purpose — a
list of rows is not a capability, and hiding somebody's saved configuration when
they switch to Simple is the failure the 2026-08-06 artifact decision reversed
([docs/SAFETY.md](../../docs/SAFETY.md) owns that rule). ``mcp.remove`` answers in
every mode too, because a tightening must never be the thing a profile switch traps.

**Secrets: there are none, deliberately, in phase 2 either.** No token column, no
header field, no keychain write. Phase 1 left that door open "when phase 2 needs
one"; connecting without credentials covers the local-server case an HTTP-only v1
already optimises for, so a server that wants a sign-in gets one plain sentence
instead of a credential surface (scoping decision 2, 2026-08-07). What this module
*does* carry is the half of G1 that cannot wait: a URL is refused at the store
boundary if it carries credential material, because ``mcp_servers`` is
snapshot-CAPTURED and anything stored here is copied into every later snapshot
payload and sidecar in plain text.
"""

from __future__ import annotations

import ipaddress
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4

from agent_core.mcp_catalog import STATUS_FAILED, STATUS_OK, ServerCatalog
from agent_core.mcp_client import McpError
from agent_core.policy import PolicyMode
from agent_core.rpc.base import ServerContext
from agent_core.rpc.providers import _base_url_problem

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

_NEEDS_NAME = "Give the server a short name so you can recognise it later."
_NAME_TOO_LONG = "Keep the name short."
_NAME_TAKEN = "You already have a tool server with that name. Pick a different one."
_NEEDS_HTTPS = (
    "Enter an address that starts with https:// — Addison only allows http:// for a "
    "server running on this computer."
)
_DEV_ONLY = (
    "Tool servers are part of the Developer profile. Switch to Developer in Settings "
    "to add one."
)
# The same refusal, for the other Developer-only action. A separate sentence rather
# than a shared one because the person is in the middle of a different verb, and
# "switch profile to ADD one" is not an answer to "check this one".
_DEV_ONLY_CHECK = (
    "Tool servers are part of the Developer profile. Switch to Developer in Settings "
    "to check one."
)
_NO_SNAPSHOT_ON_REMOVE = (
    "Addison couldn't save a restore point just now, so it didn't remove anything. "
    "Try again in a moment."
)
_NO_SUCH_SERVER = "That tool server isn't saved any more."
# The last resort when the check failed for a reason mcp_client did not name.
# mcp_client's own failures already arrive as plain sentences with a next step;
# this one covers a defect in Addison, so it says so rather than blaming the server.
_CHECK_FAILED = "Addison couldn't check that server just now. Try again in a moment."

# Same bar as a skill's name (agent_core/skills.py) — long enough for a real label,
# short enough that a row still reads as a row.
_MAX_NAME_LENGTH = 60


def _is_loopback_host(hostname: str | None) -> bool:
    """True when this host is THIS COMPUTER, and only then.

    Deliberately narrower than ``net_vetting.classify_local_or_lan``, which also
    answers True for the LAN: that function exists to DISCLOSE where an address
    points, so a wrong answer there costs a sentence. This one decides whether plain
    ``http://`` is allowed, so a LAN host answering True would put a plaintext
    address for somebody else's machine behind the exception.

    ``localhost`` and the reserved ``.localhost`` suffix (RFC 6761) count by name;
    everything else must be a literal loopback address. The IPv4-in-IPv6 forms are
    unwrapped first, on ``net_vetting.address_is_public``'s reasoning: ``::ffff:127.0.0.1``
    is the 127.0.0.1 it says it is.
    """
    if not hostname:
        return False
    lowered = hostname.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is None:
        mapped = getattr(address, "sixtofour", None)
    if mapped is not None:
        address = mapped
    return address.is_loopback


def _mcp_url_problem(url: object) -> str | None:
    """Why this MCP server address cannot be stored, as one plain sentence — or
    ``None`` when it is fine.

    **This is the custom-provider case, narrowed.** It calls
    ``rpc/providers._base_url_problem`` rather than re-deriving its rules, so an MCP
    address gets exactly the same treatment a custom OpenAI-compatible server's base
    URL gets: http(s) with a host and nothing else, NO userinfo, NO query string or
    fragment, and no key-shaped path segment. That check exists because a base URL is
    stored in a snapshot-captured table in plain text (G1), and ``mcp_servers`` is
    captured on the same terms — so the reason transfers whole, and a second
    validator would be a second thing to keep true.

    The ONE rule added on top: ``http://`` is allowed only for a server on this
    computer. ``providers.py`` permits plain http for the custom-server case at large
    (LAN model hosts, an owner decision recorded in CLAUDE.md's multi-provider
    section); an MCP server is commonly local, so the exception is worth keeping, but
    nothing here needs it for a host that is not this machine.
    """
    problem = _base_url_problem(url)
    if problem is not None:
        return problem
    assert isinstance(url, str)  # _base_url_problem rejects everything else
    if url.startswith("http://") and not _is_loopback_host(urlsplit(url).hostname):
        return _NEEDS_HTTPS
    return None


class McpMixin(ServerContext):
    def _mcp_wire_row(self, row, state: ServerCatalog) -> dict:
        """One server as the frontend parses it: the saved configuration plus what
        the last check found, in ONE row.

        Folded together rather than split into a second call because the two are one
        thought on every surface that renders them — the Settings panel needs a
        status beside each address, and the Tools surface needs a section per server
        with that server's tools in it. Two calls would mean two loads that can
        disagree about which servers exist.

        Optional fields are OMITTED rather than sent as null (``provider.list``'s
        idiom): a ``checkedAt`` on a row nobody has checked, or a ``toolCount`` on a
        row that failed, is a number the app made up. ``tools`` carries only names
        and descriptions — no schema, no server-authored anything else — and every
        string in it was cleaned at the ``mcp_client`` boundary."""
        wire: dict = {
            "id": row["id"],
            "name": row["name"],
            "url": row["url"],
            "enabled": row["enabled"],
            "addedAt": row["created_at"],
            "status": state.status,
        }
        if state.checked_at is not None:
            wire["checkedAt"] = state.checked_at
        if state.status == STATUS_OK:
            wire["toolCount"] = len(state.tools)
            wire["tools"] = [
                {"name": tool.name, "description": tool.description} for tool in state.tools
            ]
            # How many the server offered that Addison would not take — an
            # implausible name, a name already in use, or past the per-server cap.
            # A COUNT, never the names: it is the one number that lets the panel be
            # honest about "this server offers more than you see here" without
            # putting a refused string on a screen.
            not_taken = state.skipped + len(state.refused)
            if not_taken:
                wire["skipped"] = not_taken
        if state.status == STATUS_FAILED and state.error:
            wire["error"] = state.error
        return wire

    def _mcp_list(self) -> dict:
        """mcp.list -> {servers: [{id, name, url, enabled, addedAt, status, ...}]},
        oldest first.

        Answers in EVERY mode (see the module docstring): the configuration is not a
        capability, and the Developer-only gate is on the surface that shows it plus
        the ``add``/``refresh`` handlers below. What a Simple-profile person's list
        shows is whatever the last check found while they were in Developer — no
        check is run to build this payload, because listing is not asking."""
        self._ensure_built()
        return {
            "servers": [
                self._mcp_wire_row(row, self._mcp_catalog.state(row["id"]))
                for row in self.store.list_mcp_servers()
            ]
        }

    def _mcp_refresh(self, params: dict) -> dict:
        """mcp.refresh {id} -> {ok, server} | {ok:false, error}.

        Connect to ONE server, list what it offers, and replace that server's
        registrations with the result. Runs on the worker thread like every other
        store-touching job, which is also what keeps a slow server off the read loop:
        ``provider.connect``'s pattern exactly, and the lesson ``run_command`` taught
        when it held the IPC pump for thirty seconds. ``mcp_client`` bounds the whole
        walk to one budget on top of that, so the worst case is a wait, never a hang.

        TWO FAILURE CHANNELS, and the difference is deliberate. ``ok:false`` means
        the check did not RUN — wrong profile, or a server that is no longer saved —
        and there is no row to show. A check that ran and failed answers ``ok:true``
        with the row's ``status`` set to failed and one plain sentence in ``error``,
        because that IS the outcome the panel has to render, and a person who pressed
        "Check now" on a server that is switched off has not made a mistake.

        No snapshot hook: a refresh writes nothing to the store. What it changes —
        the in-memory catalog and this server's registry entries — is rebuilt by
        pressing the same button again, and a restore point that captured neither
        would be a restore point that promised something it could not give."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY_CHECK}
        server_id = params.get("id")
        if not isinstance(server_id, str) or not server_id:
            return {"ok": False, "error": _NO_SUCH_SERVER}
        row = self.store.get_mcp_server(server_id)
        if row is None:
            return {"ok": False, "error": _NO_SUCH_SERVER}
        checked_at = int(time.time())
        try:
            discovery = self._mcp_discover(row["url"])
        except McpError as exc:
            # Already one plain sentence, written for the person (mcp_client's
            # frozen copy). Never the server's own words.
            state = self._mcp_catalog.record_failure(
                self.tool_registry,
                server_id=server_id,
                error=str(exc),
                checked_at=checked_at,
            )
        except Exception:
            # A defect on Addison's side. Same treatment: one sentence, the server's
            # tools unregistered, no stack trace anywhere near the person.
            state = self._mcp_catalog.record_failure(
                self.tool_registry,
                server_id=server_id,
                error=_CHECK_FAILED,
                checked_at=checked_at,
            )
        else:
            state = self._mcp_catalog.record_success(
                self.tool_registry,
                server_id=server_id,
                server_name=row["name"],
                tools=discovery.tools,
                skipped=discovery.skipped,
                checked_at=checked_at,
            )
        return {"ok": True, "server": self._mcp_wire_row(row, state)}

    def _mcp_add(self, params: dict) -> dict:
        """mcp.add {name, url} -> {ok, server} | {ok:false, error}.

        Saves a row and does nothing else — no connection is attempted, because there
        is no client to attempt one with. Refuses outside OPEN (dev-only for v1),
        refuses a name that is blank, over-long or already taken, and refuses an
        address that fails ``_mcp_url_problem``.

        Hook (G3): ``mcp_connect``, snapshot-and-proceed on the ``provider_connect``
        class — a server that was added can be removed again in one click, so a
        capture failure warns (sticky) rather than blocking the add. Placed after
        validation so a refused add never mints a restore point."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY}
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": _NEEDS_NAME}
        name = name.strip()
        if len(name) > _MAX_NAME_LENGTH:
            return {"ok": False, "error": _NAME_TOO_LONG}
        url = params.get("url")
        if isinstance(url, str):
            url = url.strip()
        problem = _mcp_url_problem(url)
        if problem is not None:
            return {"ok": False, "error": problem}
        assert isinstance(url, str)
        if self.store.mcp_server_name_taken(name):
            return {"ok": False, "error": _NAME_TAKEN}
        self._snapshot_auto("mcp_connect")
        server_id = str(uuid4())
        added_at = int(time.time())
        try:
            self.store.insert_mcp_server(id=server_id, name=name, url=url, created_at=added_at)
        except sqlite3.IntegrityError:
            # The UNIQUE NOCASE index caught what the check above raced past. The
            # index is the authority; this branch is what keeps the person's answer a
            # sentence rather than an error frame.
            return {"ok": False, "error": _NAME_TAKEN}
        return {
            "ok": True,
            "server": {
                "id": server_id,
                "name": name,
                "url": url,
                "enabled": True,
                "addedAt": added_at,
            },
        }

    def _mcp_remove(self, params: dict) -> dict:
        """mcp.remove {id} -> {ok} | {ok:false, error}. Idempotent — removing an
        absent server is fine and mints no restore point.

        Hook (G3): ``mcp_disconnect``, and a failed capture REFUSES the removal
        (the ``skill_delete`` class): the address the person typed exists nowhere
        else once the row is gone.

        Allowed in EVERY mode. Removing is a tightening, and a profile switch must
        never be able to trap configuration somebody wants gone — which now includes
        this server's discovered tools: ``forget`` unregisters every one of them, so
        removing a server in Simple takes its tools out of the registry too, and the
        row cannot leave a stranger's ids behind it."""
        self._ensure_built()
        server_id = params.get("id")
        if not isinstance(server_id, str) or not server_id:
            return {"ok": True}
        if self.store.get_mcp_server(server_id) is None:
            return {"ok": True}
        if not self._snapshot_auto("mcp_disconnect"):
            return {"ok": False, "error": _NO_SNAPSHOT_ON_REMOVE}
        self.store.delete_mcp_server(server_id)
        # After the delete, never before: a removal that could not save a restore
        # point keeps BOTH the row and its tools, so the two never disagree.
        self._mcp_catalog.forget(self.tool_registry, server_id)
        return {"ok": True}

    def _resync_mcp_after_restore(self) -> None:
        """Post-restore resync (rpc/snapshots._finish_restore step f), on
        ``_resync_providers``' precedent and for the identical reason.

        ``mcp_servers`` is snapshot-CAPTURED, so a restore can delete a server the
        session had already checked — and the registry it registered tools into is
        in memory, which a restore does not touch. Without this, a restored config
        that no longer knows a server would leave that stranger's tool ids in the
        one registry every dispatch path consults, belonging to nothing the person
        can see or remove. Rolling back must never be the way to acquire a tool.

        Only the servers the restored config DROPPED are forgotten: a server that
        survived keeps whatever the last check found, which is still true of it."""
        known = {row["id"] for row in self.store.list_mcp_servers()}
        for server_id in self._mcp_catalog.known_servers():
            if server_id not in known:
                self._mcp_catalog.forget(self.tool_registry, server_id)
