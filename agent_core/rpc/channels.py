"""channel.* handlers — the messaging channels a person talks to Addison through
from their phone (PHASES 1 AND 2 of three).
[docs/messaging-channel-plan.md](../../docs/messaging-channel-plan.md) owns the
design, the phase order and the eleven owner decisions of 2026-08-22.

**PHASE 1 WAS CONFIGURATION, AND NOTHING CONNECTED** — the rows, the keychain
pair and a Settings section, with no adapter and no network call anywhere in the
tree. That was the MCP phase-1 shape, chosen so the reversible-config half, the G1
half and the snapshot-capture decision all landed before anything could reach a
network.

**PHASE 2 IS CONNECT, PAIR, AND ANSWER WITH WORDS ONLY.** A paired phone can hold
a conversation with Addison, and **Addison can use no tools at all while doing
it**: `REMOTE_TOOL_IDS` is EMPTY, `remote_tools(mode)` returns `[]`, and a
`tool_use` naming anything is refused before the gate at both dispatch paths. That
is not a placeholder — it is the strongest version of the phase, and it proves
every seam (the thread, the hand-off, the conversation isolation, the screening,
the splitting, the pairing) with the tool question factored out entirely.

**WHAT THIS MODULE MAY NOT DO, still.** It reads and writes rows and it asks the
SERVICE for everything else. It starts no thread (``channel_service.py`` owns the
only ones and ``main.py`` accounts for them), opens no socket, and imports neither
`httpx` nor `threading` — ``tests/test_channels.py`` asserts that structurally,
because a thread started from an RPC handler would run outside the one place that
accounts for threads, and an HTTP call here would be a network request on the
worker with no budget over it.

**G1, by construction.** No token column, no token parameter, no token in any
payload this module builds. The webview hands the token straight to the shell's
`store_channel_key` command, which writes it to the OS keychain under
`channel-key:<kind>`; the core reads it at the moment of use through
`keychain.getChannelKey`, inside the service, for the length of one request. What a
row carries instead is `token_present`, the `provider_config.secret_presence`
vocabulary: whether a token is BELIEVED to exist, never any part of one. It becomes
'present' only when a transport has been ASKED (`channel.connect`) and answered.

**G2 is untouched, and this is where the inbound edge lands.** Addison never
triggers itself and never speaks first. Every remote turn below traces to a
message a paired human sent; ``_run_channel_turn`` is only ever reached from a job
the poll loop put on the worker queue, and the poll loop only ever produces one
when somebody typed something. ``channel_service.py``'s docstring makes the
argument in full, and `tests/test_g2_no_self_trigger.py` holds it.

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

from agent_core.channel_pairing import PairingOutcome, offer
from agent_core.channel_service import STATE_STOPPED
from agent_core.channels.adapter import (
    MAX_LABEL_CHARS,
    TOKEN_REJECTED,
    TRANSPORT_UNREACHABLE,
    ChannelAuthFailed,
    ChannelError,
    clean_untrusted_text,
)
from agent_core.orchestrator import Conversation
from agent_core.policy import PolicyMode, TurnSurface
from agent_core.protocol import Method
from agent_core.providers.base import Message
from agent_core.rpc.base import ServerContext
from agent_core.rpc.conversation import _encode_tool_calls
from agent_core.screening import ScreeningResult, mark_untrusted
from agent_core.skills import compose_skills_prompt

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
_NO_SUCH_CHANNEL = "That phone connection isn't saved any more."
_NO_TOKEN_SAVED = (
    "No token is saved for this connection yet. Paste the one BotFather gave you, "
    "then press Check now."
)
_CHECK_FIRST = (
    "Addison hasn't checked this connection yet. Press Check now, then switch it on."
)
_CHECK_FAILED = "Addison couldn't check that connection just now. Try again in a moment."

# Owner decision 11 (2026-08-22): v1 runs ONE enabled channel at a time. The schema
# and the service both permit several — several rows, several threads — so this is a
# v1 SURFACE restriction, kept so the first release has one pairing story and one
# status line to get right. It is checked against what is RUNNING rather than
# against the stored `enabled` column, because the restriction is about a live
# channel and a stored 1 from a previous session has no thread behind it.
_ONE_AT_A_TIME = (
    "Addison listens to one phone connection at a time. Switch {other} off first."
)

# Said ON THE PHONE — every one of these leaves this computer, so each is written
# to be read by somebody holding a phone with no other context, and none of them
# says anything about Addison's internals.
#
# The guard refusal (owner decision 6, plan §3.6). Under the Custom profile a
# person can set `auto_grant_scope = "none"`, at which point even a LOW call routes
# to the asking flow — and a card raised for a phone would park the worker thread
# FOREVER (`_ask_once` waits with no timeout), taking every desktop turn with it.
# So the channel refuses the turn instead. This is a NARROWING, which is the
# permitted direction.
_GUARDS_REFUSE_REMOTE = (
    "You've asked Addison to check with you before every action, so it can't answer "
    "from your phone."
)
# Owner decision 8's DEFAULT: messages that arrived while the Mac was asleep are
# declined, each with one sentence. (The queue-or-decline SETTING itself is a later
# diff; this build ships the default only, which is the safe direction.)
_ARRIVED_WHILE_ASLEEP = (
    "Addison wasn't running when you sent that, so it didn't answer. Send it again "
    "and it will."
)
# The one message a successful pairing produces. It is the ONLY thing any pairing
# attempt can produce: a wrong code, an expired window and a spent budget all say
# nothing at all, because a reply is an oracle (channel_pairing.py's safety frame).
_PAIRED = (
    "This phone is paired with Addison. Send it a message and it will answer in "
    "words — it won't change anything on your computer from here."
)
# When a turn failed on Addison's side. No stack trace, no provider name, no status
# code: one sentence and one next step, exactly as the desk gets.
_TURN_FAILED = "Addison couldn't answer that just now. Try again in a moment."

# The dedicated conversation every channel's turns land in. Plain words, and the
# same title for every channel: a person with two connections has two threads
# titled the same, which is what the desk's own per-launch conversations already do.
_REMOTE_CONVERSATION_TITLE = "From your phone"

# How stale a message may be before it counts as having arrived while nobody was
# listening. Longer than any long poll (50s) plus network slop, short enough that
# "the Mac was asleep" is the only ordinary cause. Measured against the TRANSPORT'S
# own timestamp, which is the only clock that can answer the question at all — see
# InboundMessage.sent_at, which explains why Addison's own cannot.
_STALE_AFTER_SECONDS = 120

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
        # THE LOOP GOES FIRST (phase 2). A poll thread outliving its row would be a
        # thread polling on behalf of a channel nothing can name, with a token
        # nothing can find — and it would keep handing messages to a worker whose
        # pairing lookups now answer nothing. Stopping is idempotent and does not
        # wait, so this costs nothing when the channel was never switched on.
        self._channel_service.stop(channel_id)
        self.store.delete_channel(channel_id)
        return {"ok": True}

    # =====================================================================
    # PHASE 2 — connect, pair, and answer with words only
    # =====================================================================

    def _channel_connect(self, params: dict) -> dict:
        """channel.connect {id} -> {ok, connectedAs} | {ok:false, error}.

        Ask the transport who this token belongs to. ONE REQUEST, BOTH JOBS —
        ``provider.connect``'s rule (the reply IS the answer), written into the
        adapter contract so no adapter can validate and then throw the reply away.

        A WORKER JOB, never the read loop, for ``mcp.refresh``'s stated reason: it
        reaches a network, and a stranger's server must not hold the IPC pump.

        It records `token_present` and STARTS NO LOOP. Switching a channel on is
        `setEnabled`, which is deliberately a second, separate act: checking a token
        is a question, and listening is a decision.

        No snapshot hook: this writes one derived non-secret flag, and the flag is
        excluded from capture anyway. A restore point that captured neither the
        token nor the answer about it would promise something it cannot give."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY}
        row = self._channel_row(params.get("id"))
        if row is None:
            return {"ok": False, "error": _NO_SUCH_CHANNEL}
        try:
            identity = self._channel_service.verify(row["id"], row["kind"])
        except ChannelAuthFailed:
            # A DEFINITIVE answer about the credential — the transport looked and
            # said no — so it is recorded. 'absent' rather than 'unknown': Addison
            # asked, and now knows there is nothing usable saved.
            self.store.set_channel_token_present(row["id"], "absent")
            return {"ok": False, "error": TOKEN_REJECTED}
        except ChannelError:
            # Unreachable, refused, or nothing saved to ask with. NOTHING IS
            # RECORDED: a failed check is not evidence about a token, and writing
            # 'absent' here would tell somebody their token is gone because their
            # wifi dropped.
            return {"ok": False, "error": TRANSPORT_UNREACHABLE}
        except Exception:
            return {"ok": False, "error": _CHECK_FAILED}
        self.store.set_channel_token_present(row["id"], "present")
        return {"ok": True, "connectedAs": identity.display_name}

    def _channel_set_enabled(self, params: dict) -> dict:
        """channel.setEnabled {id, enabled} -> {ok} | {ok:false, error}.

        THE ONE CONTROL THAT MAKES A CHANNEL LIVE. Switching on starts the poll
        loop; switching off stops it. Both write the row, which records the
        person's intent — but the row is not what a surface believes: nothing starts
        a loop at launch, so after a restart the truth about listening is the
        service's alone (`channel.status`), which is step 8's "ask what is really
        running" one subsystem over.

        Switching ON requires a checked token, and refuses without one. That is not
        ceremony: the loop would otherwise start, read an empty keychain entry and
        stop again, leaving a switch that turns itself off with no explanation.

        Switching OFF answers in EVERY profile and needs no token, no check and no
        network. A person must always be able to stop Addison listening, and a
        profile switch must never be able to trap that."""
        self._ensure_built()
        channel_id = params.get("id")
        row = self._channel_row(channel_id)
        if row is None:
            return {"ok": False, "error": _NO_SUCH_CHANNEL}
        enabled = params.get("enabled") is True
        if not enabled:
            self._channel_service.stop(row["id"])
            self.store.set_channel_enabled(row["id"], False)
            return {"ok": True}
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY}
        if row["token_present"] != "present":
            return {"ok": False, "error": _CHECK_FIRST if row["token_present"] == "unknown"
                    else _NO_TOKEN_SAVED}
        # THE GUARD INTERLOCK, AT START (owner decision 6; the other half is per
        # turn, in `_run_channel_turn`, because guards change under a running
        # service). Switching on a channel that would refuse every message is a
        # switch that does nothing, and finding that out one message at a time — from
        # a phone, with no explanation on this screen — is the worst version of it.
        guards = self._effective_guards()
        if guards is not None and guards.auto_grant_scope == "none":
            return {"ok": False, "error": _GUARDS_REFUSE_REMOTE}
        # Owner decision 11. Asked of what is RUNNING, not of the stored column.
        for other_id in self._channel_service.listening_channels():
            if other_id == row["id"]:
                continue
            other = self.store.get_channel(other_id)
            return {
                "ok": False,
                "error": _ONE_AT_A_TIME.format(other=other["name"] if other else "the other one"),
            }
        self._channel_service.start(row["id"], row["kind"])
        self.store.set_channel_enabled(row["id"], True)
        return {"ok": True}

    def _channel_status(self, params: dict) -> dict:
        """channel.status {id} -> what the SERVICE knows, in the closed vocabulary
        ``channel_service.py`` owns.

        Answers in every profile, and answers about a channel that was never
        switched on with the honest default (stopped, nothing known). ``error`` is
        one of Addison's frozen sentences and never a transport's own text."""
        self._ensure_built()
        channel_id = params.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            return {"state": STATE_STOPPED, "backoffSeconds": 0, "unknownSenders": 0}
        status = self._channel_service.status(channel_id)
        payload: dict = {
            "state": status.state,
            "backoffSeconds": status.backoff_seconds,
            "unknownSenders": status.unknown_senders,
        }
        if status.connected_as:
            payload["connectedAs"] = status.connected_as
        if status.last_poll_at:
            payload["lastPollAt"] = status.last_poll_at
        if status.error:
            payload["error"] = status.error
        return payload

    def _channel_begin_pairing(self, params: dict) -> dict:
        """channel.beginPairing {id} -> {ok, code, expiresAt} | {ok:false, error}.

        Mint a code and open the window. THE DESKTOP SHOWS IT AND THE PHONE SENDS
        IT — not the reverse: sending a code to a number somebody types in requires
        already knowing an address, which is the thing pairing exists to establish,
        and this direction puts the secret on the trusted screen and the proof on
        the wire.

        The window lives in memory on the service and is gone on restart, which is
        correct: an open pairing window is a moment, not a setting.

        THE CODE IS NEVER PERSISTED, never logged and never put in a model's
        context — ``automation_nonce``'s own property, kept by not writing a second
        implementation of it."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _DEV_ONLY}
        row = self._channel_row(params.get("id"))
        if row is None:
            return {"ok": False, "error": _NO_SUCH_CHANNEL}
        pending = self._channel_service.begin_pairing(row["id"])
        return {"ok": True, "code": pending.code, "expiresAt": pending.expires_at}

    def _channel_cancel_pairing(self, params: dict) -> dict:
        """channel.cancelPairing {id} -> {ok}. Closes the window, in every profile:
        closing one is a tightening."""
        self._ensure_built()
        channel_id = params.get("id")
        if isinstance(channel_id, str) and channel_id:
            self._channel_service.cancel_pairing(channel_id)
        return {"ok": True}

    def _channel_pairings(self, params: dict) -> dict:
        """channel.pairings {id} -> {pairings: [{id, label, pairedAt}]}.

        THE TRANSPORT'S ID FOR THE HUMAN IS NOT ON THE WIRE. It authorises nothing
        on the webview's side and it is the one field that would let this surface
        identify a person on an outside service; the label is what a person
        recognises, and the pairing id is what Revoke needs.

        ``label`` is text the TRANSPORT supplied. It arrives cleaned and capped, and
        it is re-capped here on the boundary itself for the reason
        ``_emit_activity`` re-caps a detail: this method hands a string to the
        webview and should not depend on every future writer having gone through
        the same door."""
        self._ensure_built()
        channel_id = params.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            return {"pairings": []}
        return {
            "pairings": [
                {
                    "id": row["id"],
                    "label": clean_untrusted_text(row["label"], MAX_LABEL_CHARS),
                    "pairedAt": row["paired_at"],
                }
                for row in self.store.list_channel_pairings(channel_id)
            ]
        }

    def _channel_revoke_pairing(self, params: dict) -> dict:
        """channel.revokePairing {pairingId} -> {ok}. Idempotent.

        REVOCATION IS THE WHOLE CONTROL SURFACE a pairing has, and it answers in
        EVERY PROFILE — a tightening must never be trapped by a profile switch, the
        rule docs/SAFETY.md owns and step 8 phase 4 followed when Simple kept Remove
        and only Remove.

        No snapshot hook, and that is the capture decision restated as behaviour:
        `channel_pairings` is excluded from capture precisely so that revoking is
        final, and minting a restore point that could put the row back would undo
        the decision from the other end."""
        self._ensure_built()
        pairing_id = params.get("pairingId")
        if isinstance(pairing_id, str) and pairing_id:
            self.store.delete_channel_pairing(pairing_id)
        return {"ok": True}

    # --- the row helper ----------------------------------------------------

    def _channel_row(self, channel_id: object) -> dict | None:
        """One saved channel by id, or None for anything that is not a live row."""
        if not isinstance(channel_id, str) or not channel_id:
            return None
        return self.store.get_channel(channel_id)

    # =====================================================================
    # WHAT A REMOTE MESSAGE BECOMES (plan §3.5)
    # =====================================================================

    def _run_channel_turn(self, params: dict) -> None:
        """One message from a phone, on the worker thread, in six steps.

        REACHED ONLY FROM THE POLL LOOP'S JOB. There is no RPC method behind this
        and no way to ask for it from the webview: it is the arm of the worker's
        job chain that the service's ``enqueue_turn`` fills, with ``request_id``
        None because nothing is waiting for a JSON-RPC reply — the answer goes to a
        phone, not to a window.

        FROM HERE IT IS AN ORDINARY TURN ON THE ORDINARY THREAD, and everything
        follows from that: one registry, one gate, one orchestrator, one SQLite
        thread. ``conversation.stop`` reaches this turn the way it reaches a desk
        turn, the audit rows and the undo stack work, and the snapshot hooks work,
        because nothing about them was special-cased.

        ``self.conversation`` IS NEVER TOUCHED. The desktop's thread, its
        ``_message_ids`` alignment and its rewind anchors are untouched by
        construction (plan constraint c): ``run_turn`` takes a Conversation OBJECT,
        so this job owns one of its own.

        NOTHING HERE RAISES INTO THE WORKER. Every failure ends as one plain
        sentence on the phone or as silence, because the caller is a job with no
        request id — an exception would be logged nowhere and answered to nobody."""
        try:
            self._run_channel_turn_inner(params)
        except Exception:
            # Last-resort belt. The inner method already answers its own failures;
            # this is what keeps a defect from ending the worker thread, which
            # would take every desktop turn with it.
            return

    def _run_channel_turn_inner(self, params: dict) -> None:
        self._ensure_built()
        channel_id = params.get("channelId")
        chat_id = params.get("chatId")
        sender_id = params.get("senderId")
        text = params.get("text")
        if not (
            isinstance(channel_id, str)
            and isinstance(chat_id, str)
            and isinstance(sender_id, str)
            and isinstance(text, str)
            and text.strip()
        ):
            return
        row = self._channel_row(channel_id)
        if row is None:
            return
        # THE PROFILE, RE-ASKED PER TURN. Channels are dev-only for v1 (owner
        # decision 10) and `profile.set` already stops every loop on the way out of
        # Developer — this is the second layer, for the message that was already in
        # flight when the switch happened. Silence rather than a sentence: the
        # person did not do anything wrong, and the phone learning that Addison is
        # there but declining is exactly what the silence rule protects against.
        if self._mode() is not PolicyMode.OPEN:
            self._channel_service.stop_all()
            return

        # ---- 1. Resolve the pairing. An unknown sender is ignored IN SILENCE.
        pairing = self.store.find_channel_pairing(channel_id, sender_id)
        if pairing is None:
            self._offer_pairing(row, chat_id, sender_id, params.get("senderLabel"), text)
            return

        # ---- 2. Was this waiting while nobody was listening? (owner decision 8)
        now = int(time.time())
        sent_at = params.get("sentAt")
        if isinstance(sent_at, int) and sent_at > 0 and now - sent_at > _STALE_AFTER_SECONDS:
            if self._channel_service.may_decline(channel_id, chat_id, now):
                self._channel_service.deliver(channel_id, chat_id, _ARRIVED_WHILE_ASLEEP)
            self._notify_remote_turn(channel_id, "declined", _ARRIVED_WHILE_ASLEEP)
            return

        # ---- 3. The guard interlock (owner decision 6).
        guards = self._effective_guards()
        if guards is not None and guards.auto_grant_scope == "none":
            self._channel_service.deliver(channel_id, chat_id, _GUARDS_REFUSE_REMOTE)
            self._notify_remote_turn(channel_id, "refused", _GUARDS_REFUSE_REMOTE)
            return

        # ---- 4. Screen the text, and mark the copy the model is handed.
        # The verdict was computed at the door by the service (one screening, on
        # the pure pattern matcher, travelling WITH the text); this is the marking
        # half. `mark_untrusted` prefixes a note and never rewrites or drops the
        # passage — removing it would destroy the evidence that somebody tried and
        # leave the model answering from a hole it cannot see.
        #
        # THE MARKED COPY IS ALSO WHAT IS STORED, deliberately: the desk's own
        # transcript of this thread should show the note that the model saw, and a
        # stored copy that differed from the model's would be a record of a turn
        # that did not happen.
        kinds = params.get("screened")
        verdict = ScreeningResult(
            kinds=tuple(k for k in kinds if isinstance(k, str)) if isinstance(kinds, list) else (),
            flagged=bool(kinds),
        )
        marked = mark_untrusted(text, verdict)

        # ---- 5. Find or make THIS CHANNEL'S conversation, and run the turn.
        conversation = self._channel_conversation(channel_id)
        user_message = Message(role="user", content=marked)
        conversation.messages.append(user_message)
        self._persist_channel_message(conversation, user_message)
        self._notify_remote_turn(channel_id, "started", None)
        # One "thinking" signal, best-effort. It lapses after a few seconds on
        # Telegram and is deliberately not re-issued: a thread whose only job is to
        # wake up on a cadence is the shape G2's scan refuses without review, and a
        # courtesy is not worth arguing a floor over. The adapter records the
        # cadence (`typing_hint_every_seconds`) for the phase that wants it.
        self._channel_service.working_hint(channel_id, chat_id)
        system_message = self._channel_system_message()
        if system_message is not None:
            conversation.messages.insert(0, system_message)
        pre_turn = len(conversation.messages)
        answer = ""
        try:
            self.orchestrator.run_turn(
                conversation,
                mode=self._mode(),
                # THE TWO NEW PARAMETERS, and the whole of what makes this turn
                # different: the REMOTE view of the tool registry (empty in this
                # phase, refused at dispatch either way), and no stream sink — a
                # remote turn's deltas must never be pushed into the desktop thread
                # by the server-level `stream_to_frontend` wiring, which would be
                # the same class of mistake as reusing the active conversation.
                surface=TurnSurface.REMOTE,
                stream_to=None,
            )
            # PERSISTED INSIDE THE TRY, and the position is load-bearing rather than
            # tidy. ``pre_turn`` counts the transient system message, and the
            # ``finally`` below REMOVES it — so a persist loop placed after the block
            # would index a list that had shifted by one and quietly write nothing
            # at all, delivering nothing with it. (It did, for one draft, and only in
            # the app: a test server has no app prompt, so there was no system message
            # to shift anything. `test_the_app_prompt_does_not_swallow_the_answer`
            # exists for exactly that gap.) The desk path is written the same way for
            # the same reason.
            for message in conversation.messages[pre_turn:]:
                self._persist_channel_message(conversation, message)
                if message.role == "assistant" and str(message.content or "").strip():
                    answer = str(message.content)
        except Exception:
            # A failed turn must leave NO partial exchange behind, for the reason
            # the desk path gives: an unpaired tool_use makes the provider reject
            # every later request in this conversation.
            del conversation.messages[pre_turn:]
            self._channel_service.deliver(channel_id, chat_id, _TURN_FAILED)
            self._notify_remote_turn(channel_id, "failed", _TURN_FAILED)
            return
        finally:
            if system_message is not None:
                try:
                    conversation.messages.remove(system_message)
                except ValueError:
                    pass

        # ---- 6. Answer, and tell the desk what happened.
        if answer:
            self._channel_service.deliver(channel_id, chat_id, answer)
        self._notify_remote_turn(channel_id, "answered", None)

    def _offer_pairing(
        self, row: dict, chat_id: str, sender_id: str, label: object, text: str
    ) -> None:
        """A message from somebody who is not paired.

        FOUR OUTCOMES, ONE OF WHICH SPEAKS. A match writes the row and sends the one
        confirming message; a wrong code, an expired window and a spent budget all
        say NOTHING — the attempt is still spent, and the only thing produced is a
        counter on the desk. A reply would tell a stranger who guessed a bot name
        that the bot is real, that it is running, and that somebody is behind it.

        The same silence covers a message arriving with no window open at all, which
        is the ordinary case: most unpaired messages are not pairing attempts."""
        channel_id = row["id"]
        pending = self._channel_service.pending_pairing(channel_id)
        if pending is None:
            self._channel_service.note_unknown_sender(channel_id)
            return
        outcome = offer(pending, sender_id, text)
        if outcome is PairingOutcome.MATCHED:
            self._channel_service.cancel_pairing(channel_id)
            self.store.insert_channel_pairing(
                id=str(uuid4()),
                channel_id=channel_id,
                sender_id=sender_id,
                label=clean_untrusted_text(label, MAX_LABEL_CHARS),
                paired_at=int(time.time()),
            )
            self._channel_service.deliver(channel_id, chat_id, _PAIRED)
            self._notify_remote_turn(channel_id, "paired", None)
            return
        if outcome in (PairingOutcome.EXPIRED, PairingOutcome.EXHAUSTED):
            # The window is over. Closing it here rather than leaving it to time out
            # is what makes a fresh `beginPairing` the only way back — which is what
            # makes guessing pointless rather than merely slow, since the next
            # window has a different code and a full budget.
            self._channel_service.cancel_pairing(channel_id)
        self._channel_service.note_unknown_sender(channel_id)

    # --- the remote conversation -------------------------------------------

    def _channel_conversation(self, channel_id: str) -> Conversation:
        """The one conversation this channel's turns land in, created on first use.

        HELD IN MEMORY FOR THE LIFE OF THE PROCESS, and a fresh one after a restart
        — which is exactly what the desktop already does (``self.conversation`` is a
        new uuid per launch). The alternative, remembering the id in a setting,
        would put a pointer to a conversation into CAPTURED configuration while
        conversations themselves are excluded from capture: a restore would then
        hand back an id whose thread does not exist.

        It is a ``Conversation`` OBJECT THIS JOB OWNS. ``self.conversation`` is not
        read, not written and not swapped — the desktop's thread, its 1:1
        ``_message_ids`` alignment and its rewind anchors are untouched by
        construction (plan constraint c).

        It never sees the desktop's history (owner decision 3: no, until there is an
        app), because nothing here copies anything into it."""
        existing = self._channel_conversations.get(channel_id)
        if existing is not None:
            return existing
        conversation = Conversation(id=str(uuid4()))
        self.store.create_conversation(
            id=conversation.id,
            title=_REMOTE_CONVERSATION_TITLE,
            provider_id="primary",
            started_at=int(time.time()),
        )
        self._channel_conversations[channel_id] = conversation
        return conversation

    def _persist_channel_message(self, conversation: Conversation, message: Message) -> str:
        """One message of a remote turn, onto the same ``messages`` table every other
        turn writes to.

        NO ``_message_ids`` BOOKKEEPING. That list is the desktop's rewind anchor and
        is aligned 1:1 with ``self.conversation``; appending a remote turn's ids to
        it would break the alignment that rewind depends on, in a conversation the
        person is not even looking at. A remote thread is readable history and is not
        rewindable in this phase, which is the honest shape rather than a missing
        feature."""
        message_id = str(uuid4())
        self.store.insert_message(
            id=message_id,
            conversation_id=conversation.id,
            role=message.role,
            content=str(message.content),
            created_at=int(time.time()),
            tool_call_id=message.tool_call_id,
            tool_calls_json=_encode_tool_calls(message, conversation.shown_steps),
        )
        return message_id

    def _channel_system_message(self) -> Message | None:
        """The same app-context prompt a desk turn gets, for this turn only.

        Identical machinery and identical rules: never persisted, never in the
        stored transcript (which cannot hold a 'system' role anyway — the
        ``messages.role`` CHECK is user/assistant/tool), and dropped in the caller's
        ``finally``. Enabled guidance skills compose in exactly as they do at the
        desk: a skill's text can only STEER Addison and can never widen what Addison
        may DO — the registry and the gate stay the sole authority, and on this
        surface the registry offers nothing at all.

        No Setup-Assistant branch, deliberately. A turn with no key yet routes to a
        shared external relay at the desk, and routing somebody's phone messages
        there — a service they never chose, for a conversation they cannot see —
        is not a decision this phase gets to make quietly. With no key the turn
        fails and the phone gets one plain sentence."""
        prompt = (self._primary_prompt or "") + compose_skills_prompt(
            self.store.list_enabled_skills()
        )
        return Message(role="system", content=prompt) if prompt else None

    def _notify_remote_turn(self, channel_id: str, phase: str, summary: str | None) -> None:
        """``channel.remoteTurn`` — the desk's only view of a phone turn.

        DELIBERATELY NOT ``conversation.streamChunk`` AND NOT ``tool.activityUpdate``
        (plan §3.5). Both of those are read by the frontend as belonging to the
        thread on screen, and a phone turn's words appearing inside somebody's
        desktop conversation is the failure this whole design exists to avoid.

        ``summary`` is ADDISON'S OWN LINE about what happened — the refusal it sent,
        or nothing. Never the person's message and never the answer: this frame is a
        notice that a turn happened, and the thread itself is where the words live."""
        params: dict = {"id": channel_id, "phase": phase}
        if summary:
            params["summary"] = summary
        self._notify(Method.CHANNEL_REMOTE_TURN, params)
