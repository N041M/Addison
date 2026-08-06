"""mcp.* handlers — configuring the external MCP servers Addison consumes as a
CLIENT (step 7, phase 1). Addison is never an MCP server or a gateway.

**Nothing here is callable by the model, and nothing here connects to anything.**
Phase 1 is configuration only: a row in ``mcp_servers`` is inert. There is no
protocol client, no ``tools/list``, no registry entry, no tool id and no dispatch —
those are phases 2–4 of [docs/step-7-mcp-plan.md](../../docs/step-7-mcp-plan.md),
which owns the phase order. A person can therefore save a server and nothing at
all happens, which is the point of landing this half on its own.

**Transport is HTTP only for v1** (owner decision 2026-08-06). That is why this
module lives in the Agent Core at all: the core already speaks HTTPS to providers
through ``httpx`` and needs no new shell surface, whereas stdio would mean the core
launching an arbitrary executable — the process with no OS permissions of its own,
outside the seatbelt step 5.5 built. So a row stores a URL and never a command, and
nothing in this phase can spawn a process. The plan's §5 keeps stdio as the
documented later option.

**Developer-only surface.** MCP is dev-only for v1, so ``mcp.add`` refuses outside
OPEN mode. ``mcp.list`` answers in every mode on purpose — a list of inert rows is
not a capability, and hiding somebody's saved configuration when they switch to
Simple is the failure the 2026-08-06 artifact decision reversed
([docs/SAFETY.md](../../docs/SAFETY.md) owns that rule). ``mcp.remove`` answers in
every mode too, because a tightening must never be the thing a profile switch traps.

**Secrets: there are none in this phase, deliberately.** No token column, no header
field, no keychain write. Nothing connects, so nothing needs a credential yet; when
phase 2 does, it goes to the OS keychain through the shell on the provider-key
pattern (G1), never into SQLite and never back to the webview. What phase 1 *does*
carry is the half of that floor which cannot wait: a URL is refused at the store
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
_NO_SNAPSHOT_ON_REMOVE = (
    "Addison couldn't save a restore point just now, so it didn't remove anything. "
    "Try again in a moment."
)

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
    def _mcp_list(self) -> dict:
        """mcp.list -> {servers: [{id, name, url, enabled, addedAt}]}, oldest first.

        Answers in EVERY mode (see the module docstring): these rows are inert
        configuration, and the Developer-only gate is on the surface that shows them
        plus the ``add`` handler below."""
        self._ensure_built()
        return {
            "servers": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "url": row["url"],
                    "enabled": row["enabled"],
                    "addedAt": row["created_at"],
                }
                for row in self.store.list_mcp_servers()
            ]
        }

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
        never be able to trap configuration somebody wants gone."""
        self._ensure_built()
        server_id = params.get("id")
        if not isinstance(server_id, str) or not server_id:
            return {"ok": True}
        if self.store.get_mcp_server(server_id) is None:
            return {"ok": True}
        if not self._snapshot_auto("mcp_disconnect"):
            return {"ok": False, "error": _NO_SNAPSHOT_ON_REMOVE}
        self.store.delete_mcp_server(server_id)
        return {"ok": True}
