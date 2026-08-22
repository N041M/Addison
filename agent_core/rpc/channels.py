"""channel.* handlers — the messaging channels a person talks to Addison through
from their phone (PHASE 1 of three).
[docs/messaging-channel-plan.md](../../docs/messaging-channel-plan.md) owns the
design, the phase order and the eleven owner decisions of 2026-08-22.

**PHASE 1 IS CONFIGURATION, AND NOTHING CONNECTS.** There is no adapter, no poll
loop, no pairing and no network call anywhere in this build: saving a channel
stores a name and a kind, and the token the person types goes to the OS keychain
without ever passing through here. That is the MCP phase-1 shape, chosen for the
same reason — the reversible-config half, the G1 half and the snapshot-capture
decision all land before anything can reach a network, so the questions that are
hard to unwind are answered while the feature is still inert.

**G1, by construction.** No token column, no token parameter, no token in any
payload this module builds. The webview hands the token straight to the shell's
`store_channel_key` command, which writes it to the OS keychain under
`channel-key:<kind>`; the core reads it at the moment of use through
`keychain.getChannelKey` — which nothing calls yet, and which is the only core-side
reach for it that will ever exist. What a row carries instead is `token_present`,
the `provider_config.secret_presence` vocabulary: whether a token is BELIEVED to
exist, never any part of one. In this phase it is 'unknown' on every row and stays
there, because deciding otherwise means asking a transport, and asking a transport
is phase 2.

**G2 is untouched, and worth saying while nothing here can run.** Addison never
triggers itself and never speaks first. This module starts no thread, holds no
timer and originates no work; the thread that will one day wait for a person's
message arrives with the service in phase 2, and even then what it waits on is a
person having typed something.

**G3.** `channels` is snapshot-CAPTURED (`snapshots/scope.py`) on the `mcp_servers`
terms, minus `token_present`; `channel_pairings` is deliberately EXCLUDED, because
a pairing is an authorization and a one-action restore must never put back an
authorization somebody revoked. So adding and removing both mint a restore point,
and a removal that cannot save one is REFUSED (the `skill_delete` class): the name
and the kind exist nowhere else once the row is gone.

**Developer-only, in the one place that enforces it.** `channel.add` refuses outside
OPEN — channels are dev-only for v1 (owner decision 10) — and that is the whole of
the profile boundary, exactly as `mcp.add` is. `channel.list` and `channel.remove`
answer in EVERY profile: saved configuration is not a capability, hiding somebody's
own rows on a profile switch is the failure the 2026-08-06 artifact decision
reversed, and a removal is a tightening, which must never be what a switch traps.

**Where the token is deleted, and why it is not deleted here.** A removal has to take
the keychain item with it, and the core has no way to delete one: the shell's keychain
surface toward the core is a READ (`keychain.getChannelKey`) and the plan adds no
second verb to it. So the webview calls the shell's `delete_channel_key` command
first — the same shape as the provider "Remove" action, which has always deleted the
key from the frontend — and then calls this method to drop the row. Handing the core
a delete-anything-in-the-keychain verb to save one round-trip is the trade this
project does not make.
"""

from __future__ import annotations

import sqlite3
import time
from uuid import uuid4

from agent_core.policy import PolicyMode
from agent_core.rpc.base import ServerContext

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

_NEEDS_NAME = "Give this connection a short name so you can recognise it later."
_NAME_TOO_LONG = "Keep the name short."
_NAME_TAKEN = "You already have a phone connection with that name. Pick a different one."
_UNKNOWN_KIND = "Addison can only connect to Telegram at the moment."
_DEV_ONLY = (
    "Connecting a phone is part of the Developer profile. Switch to Developer in "
    "Settings to set one up."
)
_NO_SNAPSHOT_ON_REMOVE = (
    "Addison couldn't save a restore point just now, so it didn't remove anything. "
    "Try again in a moment."
)

# The transports an adapter exists for. ONE list, matching the CHECK in schema.sql —
# a second spelling of a closed vocabulary is how a value ends up legal in one place
# and refused in the other. It is a frozenset in code rather than a column somewhere
# because the set is a property of what Addison has been built to speak, and a row
# must never be able to name a transport with nothing behind it. Widened in the same
# commit that adds an adapter, never before it.
_KINDS: frozenset[str] = frozenset({"telegram"})

# Same bar as a tool server's name (rpc/mcp.py) and a skill's: long enough for a real
# label, short enough that a row still reads as a row.
_MAX_NAME_LENGTH = 60


class ChannelsMixin(ServerContext):
    def _channel_wire_row(self, row) -> dict:
        """One channel as the frontend parses it.

        `tokenPresent` is the non-secret three-state record and NEVER a key, a
        length or a prefix. `pairedDevices` is a COUNT and never the rows: a pairing
        carries a display name the transport supplied, i.e. text somebody else wrote,
        and the surface this phase has needs the number. Both are read from the store
        rather than from the OS: rendering a list must not cost a keychain touch, which
        is `secret_presence`'s whole reason for existing."""
        return {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "enabled": row["enabled"],
            "tokenPresent": row["token_present"],
            "pairedDevices": self.store.count_channel_pairings(row["id"]),
            "addedAt": row["created_at"],
        }

    def _channel_list(self) -> dict:
        """channel.list -> {channels: [{id, kind, name, enabled, tokenPresent,
        pairedDevices, addedAt}]}, oldest first.

        Answers in EVERY profile (see the module docstring): a saved row is an inert
        name and a transport kind, and the Developer-only gate is on the Settings
        section that shows it plus `_channel_add` below. Nothing here reaches a
        network, a keychain or a thread — in this phase there is nothing to reach."""
        self._ensure_built()
        return {
            "channels": [self._channel_wire_row(row) for row in self.store.list_channels()]
        }

    def _channel_add(self, params: dict) -> dict:
        """channel.add {kind, name} -> {ok, channel} | {ok:false, error}.

        Saves a row, switched off and with no token believed saved, and CONNECTS TO
        NOTHING — there is nothing in this build to connect with. Refuses outside OPEN
        (dev-only for v1), refuses a kind outside the closed set, and refuses a name
        that is blank, over-long or already taken.

        Hook (G3): `channel_add`, snapshot-and-proceed on the `mcp_connect` class — a
        channel that was added can be removed again in one click, so a capture failure
        warns rather than blocking the add. It sits below every refusal that can be
        decided without touching the store and above the insert, because a restore
        point records the configuration as it was BEFORE the change it is a way back
        from."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY}
        kind = params.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            return {"ok": False, "error": _UNKNOWN_KIND}
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": _NEEDS_NAME}
        name = name.strip()
        if len(name) > _MAX_NAME_LENGTH:
            return {"ok": False, "error": _NAME_TOO_LONG}
        if self.store.channel_name_taken(name):
            return {"ok": False, "error": _NAME_TAKEN}
        self._snapshot_auto("channel_add")
        channel_id = str(uuid4())
        added_at = int(time.time())
        try:
            self.store.insert_channel(id=channel_id, kind=kind, name=name, created_at=added_at)
        except sqlite3.IntegrityError:
            # The CHECK on `kind` is the database's own authority over the closed
            # transport vocabulary, and this branch keeps its answer a sentence rather
            # than an error frame. Unreachable while `_KINDS` and the CHECK agree,
            # which is the point: the day they stop agreeing, the person still gets a
            # sentence instead of a stack trace.
            return {"ok": False, "error": _UNKNOWN_KIND}
        return {
            "ok": True,
            "channel": {
                "id": channel_id,
                "kind": kind,
                "name": name,
                "enabled": False,
                "tokenPresent": "unknown",
                "pairedDevices": 0,
                "addedAt": added_at,
            },
        }

    def _channel_remove(self, params: dict) -> dict:
        """channel.remove {id} -> {ok} | {ok:false, error}. Idempotent — removing an
        absent channel is fine and mints no restore point.

        The pairings go with the row, by `ON DELETE CASCADE` in the schema rather than
        by a second statement here, so "no pairing outlives its channel" is the
        database's property and not this handler's diligence. The keychain item is
        deleted by the WEBVIEW before this call (see the module docstring): the core
        has a read of the channel token and no verb that removes one.

        Hook (G3): `channel_remove`, and a failed capture REFUSES the removal (the
        `skill_delete` / `mcp_disconnect` class) — the name and the kind exist nowhere
        else once the row is gone.

        Allowed in EVERY mode. Removing is a tightening, and a profile switch must
        never be able to trap configuration somebody wants gone."""
        self._ensure_built()
        channel_id = params.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            return {"ok": True}
        if self.store.get_channel(channel_id) is None:
            return {"ok": True}
        if not self._snapshot_auto("channel_remove"):
            return {"ok": False, "error": _NO_SNAPSHOT_ON_REMOVE}
        self.store.delete_channel(channel_id)
        return {"ok": True}
