"""``ChannelService`` — the poll loop, and the hand-off to the worker.

================================ SAFETY FRAME ================================
**G2 — ADDISON NEVER TRIGGERS ITSELF, and this is the file that comes nearest to
that floor.** The poll loop repeats, so the sentence deserves more than an
assertion (docs/messaging-channel-plan.md §3.4, which owns the argument):

  * **It hands no callback to a clock.** ``tests/test_g2_no_self_trigger.py`` bans
    ``Timer``, ``scheduler``, ``enterabs``, ``alarm``, ``call_later``, ``call_at``,
    ``create_task``, ``ensure_future`` and ``run_coroutine_threadsafe`` anywhere in
    ``agent_core/``, by AST scan and through aliases. This loop uses none of them.
    It blocks on a network read that A PERSON'S MESSAGE is what ends, and it waits
    on a ``threading.Event`` — the primitive that parks until somebody else acts —
    for its backoff and its stop.
  * **It originates no work.** A poll that finds nothing does nothing: no turn, no
    tool, no model call, no row. The only thing that produces work is a message a
    human typed, which is the same relationship the read loop has to the keyboard.
    What this loop IS, precisely, is a second inbound edge on a process that
    already has one.
  * **Addison still never speaks first.** No proactive message, no notification,
    no scheduled digest, no "you asked me to remind you". Every outbound message
    this file can send is an answer to an inbound one. If a later version ever
    wants otherwise, that is a new owner decision against this floor and not an
    extension of this feature.
  * **What the loop genuinely costs**, which is the honest half: a thread that
    repeats is a thing that must be switched off, so ``stop``/``stop_all`` are part
    of the contract and not an afterthought, and the enabled state is the person's
    — visible in Settings and one click from off.

**CONSTRAINT (b): THE WORKER THREAD IS THE ONLY SQLITE THREAD** (``main.py``'s own
docstring). This service TOUCHES NO STORE, EVER — that is what makes it legal as a
thread at all, and it is the same rule ``main.py`` states beside the folder picker
and the model pull. Everything it needs is INJECTED, which is also what keeps it
testable against a fake transport: ``token_for`` reaches the keychain through the
shell bridge, ``enqueue_turn`` is the worker queue, ``notify`` is ``_notify``,
``screen_text`` is :func:`agent_core.screening.screen`.

**HOW A TURN IS HANDED OVER.** The service does not call the orchestrator. It puts
a job on the same queue every RPC method uses, with ``request_id=None`` because
nothing is waiting for a JSON-RPC reply — the answer goes to a phone, not to the
webview. Everything follows from that one choice: SQLite stays on one thread,
remote turns serialize behind desk turns and vice versa, ``conversation.stop``
reaches a remote turn the same way it reaches a desk one, and the audit rows, the
undo stack and the snapshot hooks all work because nothing about them was
special-cased.

**G1 — the token is read at the moment of use and never retained.** ``token_for``
is called per poll and the value lives in a local for the length of one request.
It is never stored on this object, never logged, never put in an exception (see
``channels/telegram.py`` on why that matters when the token is in the URL), and
never written to SQLite.
=============================================================================

WHAT IS TRUE ABOUT RUNNING, AND WHAT IS MERELY SAVED. The ``channels.enabled``
column is the person's saved intent; THIS SERVICE is the truth about whether
Addison is listening. Nothing starts a loop at launch — ``channel.setEnabled`` is
the one control that makes a channel live (plan §3.10) — so after a restart a
switched-on row has no thread behind it, and every surface reads its state from
:meth:`status` rather than from the row. That is step 8's lesson in a different
costume: armed truth came from the OS because the OS held it; listening truth
comes from here because here is what holds it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from agent_core.channel_pairing import PendingPairing, begin
from agent_core.channels.adapter import (
    MAX_LABEL_CHARS,
    SEND_REFUSED,
    TRANSPORT_UNREACHABLE,
    ChannelAdapter,
    ChannelAuthFailed,
    ChannelRefused,
    ChannelUnavailable,
    InboundMessage,
    VerifiedIdentity,
    clean_untrusted_text,
)
from agent_core.protocol import Method
from agent_core.screening import ScreeningResult

# --- the states a channel can be in, in one closed vocabulary ---------------
#
# Read by `channel.status`, rendered by the panel in plain words. A CLOSED SET,
# because a surface that has to guess at an unknown state prints something nobody
# wrote. The panel's copy lives in ChannelsPanel.tsx; these are the keys.

STATE_STOPPED = "stopped"              # not listening — nothing is running
STATE_LISTENING = "listening"          # a poll is open, waiting for a message
STATE_BACKING_OFF = "backing_off"      # the transport is unreachable; still trying
STATE_TOKEN_REJECTED = "token_rejected"  # the token was refused; the loop stopped
STATE_NO_TOKEN = "no_token"            # nothing saved to poll with; the loop stopped

#: Said on the phone when Addison could not send a whole answer.
_UNDELIVERABLE = SEND_REFUSED

#: What one part of a split answer ends with when there is more to come. Plain
#: words rather than an ellipsis alone: "…" at the end of a message reads as
#: trailing off, and the person needs to know another message is arriving.
CONTINUATION_MARKER = "\n\n(continued…)"

#: How long a chat waits before it can be told again that Addison was asleep. A
#: night's worth of queued messages must not become a night's worth of apologies:
#: the first one says it, the rest are silent (owner decision 8's default, made
#: liveable).
_DECLINE_SUPPRESSION_SECONDS = 300


@dataclass(frozen=True)
class ChannelStatus:
    """What the service knows about one channel, right now.

    NEVER A TRANSPORT'S OWN ERROR TEXT: ``error`` is one of Addison's frozen
    sentences or None. ``unknown_senders`` is the count of messages from senders
    that are not paired — the ONLY thing an unpaired message produces, and the
    reason a person can see that strangers are knocking without any of them
    learning that anybody is home."""

    state: str = STATE_STOPPED
    connected_as: str | None = None
    last_poll_at: int | None = None
    backoff_seconds: int = 0
    unknown_senders: int = 0
    error: str | None = None


def split_message(text: str, limit: int, marker: str = CONTINUATION_MARKER) -> list[str]:
    """One answer as the parts a transport will actually carry.

    THE SERVICE SPLITS, THE ADAPTER REFUSES (plan §3.3). An adapter that truncated
    would be cutting where nobody can report it; this function cuts where the text
    itself offers a seam and says so on every part but the last.

    The seam, in order: a PARAGRAPH break, then a LINE break, then a hard cut. The
    hard cut is not a failure — a wall of text with no break in four thousand
    characters has no seam to find, and stopping mid-word is more honest than
    silently dropping the rest.

    Pure, and deliberately so: no state, no transport, no I/O. That is what lets a
    test assert the ordering and the marker without a network."""
    if limit <= 0 or len(text) <= limit:
        return [text]
    # Every part but the last carries the marker, so the marker has to fit INSIDE
    # the limit rather than be added on top of it.
    budget = max(1, limit - len(marker))
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:budget]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0 or not rest[:cut].strip():
            cut = budget
        parts.append(rest[:cut].rstrip() + marker)
        rest = rest[cut:].lstrip("\n")
        if not rest:
            break
    if rest:
        parts.append(rest)
    return parts


class ChannelService:
    """One thread per enabled channel, and no state that matters."""

    def __init__(
        self,
        adapters: dict[str, ChannelAdapter],
        token_for: Callable[[str], str],
        enqueue_turn: Callable[[tuple], None],
        notify: Callable[[str, dict], None],
        screen_text: Callable[[str], ScreeningResult],
    ) -> None:
        #: kind -> the adapter that speaks it. ONE INSTANCE PER TRANSPORT, which is
        #: exactly right while v1 runs one enabled channel at a time (owner decision
        #: 11): two channels of the same kind would share a backoff, and the day
        #: several may be enabled at once is the day this becomes a per-channel
        #: instance. Written down because it is the kind of sharing that is obvious
        #: when you look and invisible when you don't.
        self._adapters = dict(adapters)
        self._token_for = token_for
        self._enqueue_turn = enqueue_turn
        self._notify = notify
        self._screen_text = screen_text
        # Everything below is touched from BOTH the poll threads and the worker
        # thread, so all of it moves under one lock. The lock is never held across
        # a network call or a queue put — only around the dictionary work.
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._kinds: dict[str, str] = {}
        self._status: dict[str, ChannelStatus] = {}
        self._pending: dict[str, PendingPairing] = {}
        self._declined_at: dict[tuple[str, str], int] = {}

    # --- lifecycle ---------------------------------------------------------

    def start(self, channel_id: str, kind: str) -> None:
        """Start listening on one channel.

        ``kind`` is a parameter because THIS SERVICE HAS NO STORE (see the module
        docstring) and therefore cannot look a row up. The caller has just read the
        row, so it hands over what it read — which is also what keeps the closed
        transport vocabulary in one place, the schema's CHECK, rather than growing
        a second copy here.

        The thread is created with an EXPLICIT ``target=self._poll_loop``. That is
        not a style choice: ``tests/test_g2_no_self_trigger.py`` reads every
        ``threading.Thread(...)`` construction in ``agent_core/`` and refuses a
        Thread subclass outright, because a subclass hides the entry point in
        ``run()`` and G2 is only checkable when the entry point is visible."""
        with self._lock:
            if channel_id in self._threads and self._threads[channel_id].is_alive():
                return
            stop = threading.Event()
            self._stops[channel_id] = stop
            self._kinds[channel_id] = kind
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(
                previous, state=STATE_LISTENING, error=None, backoff_seconds=0
            )
            thread = threading.Thread(
                target=self._poll_loop,
                args=(channel_id, kind, stop),
                name=f"channel-{kind}",
                daemon=True,
            )
            self._threads[channel_id] = thread
        thread.start()
        self._announce(channel_id)

    def stop(self, channel_id: str) -> None:
        """Stop listening. Idempotent, and it does not wait for the thread.

        The poll may be parked in a fifty-second long poll, and joining would make
        switching a channel off take as long as the transport feels like. The stop
        event is what the loop honours: it checks before every hand-off, before
        every advance and before every wait, so nothing new happens after this
        returns even while the socket is still open."""
        with self._lock:
            stop = self._stops.pop(channel_id, None)
            self._threads.pop(channel_id, None)
            self._pending.pop(channel_id, None)
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(
                previous, state=STATE_STOPPED, error=None, backoff_seconds=0
            )
        if stop is not None:
            stop.set()
        self._announce(channel_id)

    def stop_all(self) -> None:
        """Every channel, off. Called when the core shuts down and when the profile
        leaves Developer — a capability that belongs to a profile must not keep
        running after somebody switches away from it."""
        with self._lock:
            ids = list(self._stops)
        for channel_id in ids:
            self.stop(channel_id)

    def is_listening(self, channel_id: str) -> bool:
        """Whether a poll loop is alive for this channel RIGHT NOW."""
        with self._lock:
            thread = self._threads.get(channel_id)
            return thread is not None and thread.is_alive()

    def listening_channels(self) -> list[str]:
        """Every channel with a live loop. What the one-at-a-time rule (owner
        decision 11) is actually asked, because the rule is about what is RUNNING —
        a stored ``enabled`` from a previous session has no thread behind it and
        must not be able to refuse somebody's switch."""
        with self._lock:
            return [cid for cid, thread in self._threads.items() if thread.is_alive()]

    # --- what the desk can ask ---------------------------------------------

    def status(self, channel_id: str) -> ChannelStatus:
        """What this channel is doing, as the panel renders it."""
        with self._lock:
            status = self._status.get(channel_id, ChannelStatus())
            kind = self._kinds.get(channel_id)
            alive = channel_id in self._threads and self._threads[channel_id].is_alive()
        adapter = self._adapters.get(kind) if kind else None
        backoff = adapter.backoff.seconds if adapter is not None else 0
        if not alive and status.state in (STATE_LISTENING, STATE_BACKING_OFF):
            # The thread died without saying so (an unexpected exception). Report
            # the truth rather than the last thing that was written down: a status
            # line that says "listening" over a dead thread is the one lie this
            # surface must not tell.
            return replace(status, state=STATE_STOPPED, backoff_seconds=0)
        return replace(status, backoff_seconds=backoff if alive else 0)

    def note_connected(self, channel_id: str, identity: VerifiedIdentity) -> None:
        """Remember who a verified token belongs to, for the "connected as" line.

        In memory, never in the store: it is a fact about a credential the keychain
        holds, and a captured row claiming a bot name a restored token may not match
        would be the ``token_present`` mistake with a display name attached."""
        with self._lock:
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(
                previous, connected_as=identity.display_name or None
            )

    def verify(self, channel_id: str, kind: str) -> VerifiedIdentity:
        """Check the saved token and learn what it belongs to.

        Called from the WORKER thread by ``channel.connect`` — a network round trip
        on the worker, which is ``mcp.refresh``'s pattern and is there for its
        stated reason: a stranger's server must never hold the IPC pump. Raises the
        adapter's own three exceptions; the RPC layer turns them into a sentence."""
        adapter = self._adapters.get(kind)
        if adapter is None:
            raise ChannelUnavailable(TRANSPORT_UNREACHABLE)
        identity = adapter.verify_token(self._token_for(kind))
        with self._lock:
            # Remember WHICH TRANSPORT this row speaks. The service holds no store,
            # so every method that has to reach a transport for a channel — `deliver`
            # and `working_hint`, both called from the worker after a turn — needs
            # somebody to have told it. `start` is the other teller; this one covers
            # a channel that has been checked but not switched on.
            self._kinds[channel_id] = kind
        self.note_connected(channel_id, identity)
        return identity

    # --- pairing windows (in memory; gone on restart) ----------------------

    def begin_pairing(self, channel_id: str) -> PendingPairing:
        """Open a pairing window, replacing any window already open on this channel."""
        pending = begin(channel_id)
        with self._lock:
            self._pending[channel_id] = pending
        return pending

    def cancel_pairing(self, channel_id: str) -> None:
        with self._lock:
            self._pending.pop(channel_id, None)

    def pending_pairing(self, channel_id: str) -> PendingPairing | None:
        with self._lock:
            return self._pending.get(channel_id)

    def note_unknown_sender(self, channel_id: str) -> None:
        """One more message from somebody who is not paired.

        THE ONLY THING AN UNPAIRED MESSAGE PRODUCES. No reply, no error, no read
        receipt — a reply is an oracle. A counter on the desk tells the person that
        strangers are knocking without telling any of them that anybody is home."""
        with self._lock:
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(
                previous, unknown_senders=previous.unknown_senders + 1
            )
        # Told to the DESK, and to nobody else. The notification carries the channel
        # id and the state and nothing about the sender — there is no version of this
        # feature in which a stranger learns anything from having knocked. Without it
        # the count would sit unread until some other event refreshed the panel, and
        # "strangers are knocking" is exactly the thing a person should see promptly.
        self._announce(channel_id)

    def may_decline(self, channel_id: str, chat_id: str, now: int) -> bool:
        """Whether this chat may be told, again, that Addison was not listening.

        Owner decision 8's default is to DECLINE messages that arrived while the Mac
        slept, and a night of queued messages would otherwise be a night of
        identical apologies arriving at once. The first one says it; the rest are
        silent for a while."""
        key = (channel_id, chat_id)
        with self._lock:
            last = self._declined_at.get(key)
            if last is not None and now - last < _DECLINE_SUPPRESSION_SECONDS:
                return False
            self._declined_at[key] = now
            return True

    # --- the send side ------------------------------------------------------

    def deliver(self, channel_id: str, chat_id: str, text: str) -> bool:
        """Send one answer to a phone, split into the parts the transport carries.

        Called from the WORKER thread when a turn finishes, and store-free like
        everything else here. A refusal is reported ONCE — into the status line and
        the ``channel.stateChanged`` notification — and never retried in a loop: the
        same message would be refused the same way.

        Returns whether the whole answer went. A partial delivery is reported as a
        failure even though some parts arrived, because "your phone has the first
        half" is the fact the desk needs, not "it worked"."""
        if not text:
            return True
        with self._lock:
            kind = self._kinds.get(channel_id)
        adapter = self._adapters.get(kind) if kind else None
        if adapter is None:
            # Nothing has ever told this service which transport that channel speaks
            # (`start` and `verify` are the two tellers), so there is no way to send.
            # Said out loud rather than dropped: an answer that goes nowhere in
            # silence is the failure a status line exists to prevent.
            self._fail(channel_id, _UNDELIVERABLE)
            return False
        try:
            token = self._token_for(kind or "")
        except Exception:
            self._fail(channel_id, TRANSPORT_UNREACHABLE)
            return False
        if not token:
            self._fail(channel_id, TRANSPORT_UNREACHABLE)
            return False
        parts = split_message(text, adapter.limits.max_message_chars)
        for part in parts:
            try:
                adapter.send(token, chat_id, part)
            except ChannelRefused:
                self._fail(channel_id, _UNDELIVERABLE)
                return False
            except ChannelAuthFailed:
                self._token_rejected(channel_id)
                return False
            except ChannelUnavailable:
                self._fail(channel_id, TRANSPORT_UNREACHABLE)
                return False
            except Exception:
                self._fail(channel_id, _UNDELIVERABLE)
                return False
        return True

    def working_hint(self, channel_id: str, chat_id: str) -> None:
        """Tell the phone Addison is thinking, where the transport has a way to say
        so. Best-effort and silent on every failure — a courtesy that could fail a
        turn is not a courtesy."""
        with self._lock:
            kind = self._kinds.get(channel_id)
        adapter = self._adapters.get(kind) if kind else None
        if adapter is None or not adapter.limits.supports_typing_hint:
            return
        try:
            adapter.working_hint(self._token_for(kind or ""), chat_id)
        except Exception:
            return

    # --- the loop ------------------------------------------------------------

    def _poll_loop(self, channel_id: str, kind: str, stop: threading.Event) -> None:
        """The whole runtime: read the token, poll, hand each message to the worker,
        advance the cursor, repeat.

        THE ORDER IS THE CONTRACT. Messages are handed off BEFORE the cursor
        advances, because on this transport the cursor IS the acknowledgement —
        asking past an update is what tells Telegram to forget it. That makes
        delivery at-least-once and says so out loud (owner decision 9).

        THE CURSOR IS A LOCAL. It lives for the life of this loop and is never
        stored: a cursor in a captured table would be restored, and a restored
        cursor is a claim about what a transport has already forgotten."""
        adapter = self._adapters.get(kind)
        if adapter is None:
            self._set_state(channel_id, STATE_STOPPED, error=TRANSPORT_UNREACHABLE)
            return
        cursor: str | None = None
        while not stop.is_set():
            try:
                token = self._token_for(kind)
            except Exception:
                # The keychain could not be read (a dialog nobody answered, a
                # locked keychain). That is not "no token" — a token may well be
                # there — so it is treated as an outage and retried, never as a
                # reason to stop and tell somebody their token is wrong.
                self._wait_backoff(channel_id, adapter, stop)
                continue
            if not token:
                self._set_state(channel_id, STATE_NO_TOKEN, error=None)
                return
            try:
                result = adapter.poll(token, cursor, adapter.limits.max_poll_seconds)
            except ChannelAuthFailed:
                self._token_rejected(channel_id)
                return
            except ChannelUnavailable:
                self._wait_backoff(channel_id, adapter, stop)
                continue
            except Exception:
                # A defect on Addison's side, or a transport doing something no
                # adapter anticipated. Same treatment as an outage: back off and
                # try again, and never a stack trace anywhere near a person.
                self._wait_backoff(channel_id, adapter, stop)
                continue
            adapter.backoff.note_success()
            if stop.is_set():
                # Switched off while the poll was open. Nothing is handed on and
                # the cursor is NOT advanced, so whatever arrived is still waiting
                # at the transport if the person switches back on.
                return
            for message in result.messages:
                self._hand_off(channel_id, message)
            cursor = result.next_cursor
            self._set_state(channel_id, STATE_LISTENING, error=None, polled_at=int(time.time()))

    def _hand_off(self, channel_id: str, message: InboundMessage) -> None:
        """One inbound message, screened and put on the worker queue.

        SCREENED HERE, MARKED THERE. :func:`agent_core.screening.screen` is a pure
        pattern matcher with no I/O, so running it on this thread costs the worker
        nothing and keeps the verdict travelling WITH the text — the worker marks
        the copy the model is handed using this verdict rather than re-deriving one.
        This is the SIXTH origin of screened text and the first that is not a tool
        result (docs/untrusted-screening-plan.md owns the list and the honest
        statement of what screening is worth: a backstop, not a boundary).

        ``request_id`` is None because nothing is waiting for a JSON-RPC reply."""
        verdict = self._screen_text(message.text)
        self._enqueue_turn(
            (
                "channel_turn",
                {
                    "channelId": channel_id,
                    "chatId": message.chat_id,
                    "senderId": message.sender_id,
                    "senderLabel": clean_untrusted_text(message.sender_label, MAX_LABEL_CHARS),
                    "text": message.text,
                    "receivedAt": int(time.time()),
                    "sentAt": message.sent_at,
                    # KINDS ONLY, never the matched text (screening.py's rule): a
                    # payload that quoted the injection back would reproduce it in a
                    # second place, and this one crosses a thread boundary.
                    "screened": list(verdict.kinds),
                },
                None,
            )
        )

    # --- status bookkeeping --------------------------------------------------

    def _wait_backoff(self, channel_id: str, adapter: ChannelAdapter, stop: threading.Event) -> None:
        """Record an outage, tell the desk, and wait — on the STOP EVENT, so
        switching the channel off during a backoff is instant rather than a wait for
        a delay to expire."""
        delay = adapter.backoff.note_failure()
        self._set_state(channel_id, STATE_BACKING_OFF, error=TRANSPORT_UNREACHABLE)
        stop.wait(delay)

    def _token_rejected(self, channel_id: str) -> None:
        """The transport refused the credential. The loop STOPS and says so — the
        only thing that fixes a rejected token is a person pasting a new one, and
        retrying one in a loop is how an account gets locked.

        THE EVENT IS SET, not merely dropped, because this is reached from BOTH
        threads: the poll loop learns from its own poll (and is about to return
        anyway), and the SEND side learns from the worker while the loop is parked in
        a fifty-second long poll. Forgetting the event there would leave a thread
        polling on behalf of a channel that every surface now reports as stopped."""
        with self._lock:
            stop = self._stops.pop(channel_id, None)
            self._threads.pop(channel_id, None)
        if stop is not None:
            stop.set()
        self._set_state(channel_id, STATE_TOKEN_REJECTED, error=None)

    def _fail(self, channel_id: str, sentence: str) -> None:
        """One plain sentence onto the status line, without changing what the loop
        is doing. Used by the send side, where a refusal is about one message rather
        than about the channel."""
        with self._lock:
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(previous, error=sentence)
        self._announce(channel_id)

    def _set_state(
        self, channel_id: str, state: str, *, error: str | None, polled_at: int | None = None
    ) -> None:
        with self._lock:
            previous = self._status.get(channel_id, ChannelStatus())
            self._status[channel_id] = replace(
                previous,
                state=state,
                error=error,
                last_poll_at=polled_at if polled_at is not None else previous.last_poll_at,
            )
        self._announce(channel_id)

    def _announce(self, channel_id: str) -> None:
        """``channel.stateChanged`` — the panel re-renders without polling.

        ONE DIRECTION, core to webview, and nothing reads it back: a dropped
        notification costs a stale line on a panel and never a message, a turn or a
        state. The frontend can always ask ``channel.status`` for the truth."""
        status = self.status(channel_id)
        params: dict = {"id": channel_id, "state": status.state}
        if status.error:
            params["error"] = status.error
        try:
            self._notify(Method.CHANNEL_STATE_CHANGED, params)
        except Exception:
            # A frame that could not be written must never kill a poll thread.
            return
