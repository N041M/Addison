"""``TelegramAdapter`` — the only file in the design that knows a vendor's API.

Telegram is first because its Bot API has an OUTBOUND-ONLY mode: ``getUpdates``
long-polling, where the client asks and the server holds the request open until
there is something to say. The Agent Core has no OS permissions of its own and the
seatbelt grants outbound network and nothing inbound (spec §1.3), so a webhook —
a listener, a public address, a certificate — is not proposed in any phase. This
whole transport therefore lives here beside ``httpx`` and needs no new shell
surface at all except the keychain pair phase 1 shipped.

THE NUMBERS ARE READ FROM TELEGRAM'S PUBLISHED LIMITS AND WRITTEN DOWN HERE, with
the link, because a number in a design document is a claim somebody maintains
badly ([docs/CONVENTIONS.md](../../docs/CONVENTIONS.md) owns that rule). Where the
published documentation gives no number, this file says so rather than inventing
authority for one — see ``_MAX_POLL_SECONDS``.

THE TOKEN IS IN THE URL, which is Telegram's design and not a choice available
here (`https://api.telegram.org/bot<token>/METHOD`). Two consequences are
load-bearing and both are enforced below: **no request URL is ever logged, put in
an exception, or returned**, and every error this file raises carries one of
``adapter.py``'s frozen sentences instead of anything httpx produced — an httpx
exception's ``str()`` contains the URL, and therefore the token.

NO STREAMING, and that is the design (plan §3.3). The desktop streams because a
person is watching it happen; a phone is where you read an answer. An
edit-per-delta design spends a rate budget nobody here can measure, rewrites a
message under the reader's thumb, and adds a failure mode with no good recovery.
So: the typing hint while the turn runs, then ONE message when the turn is done,
split by the SERVICE at ``limits.max_message_chars``.

This file is also the only one that knows Telegram's start links. A link of the
form ``https://t.me/<bot>?start=<payload>`` opens the bot on a phone, and tapping
Start sends the bot the message ``/start <payload>``. Pairing uses this so a person
can scan a QR code on the desktop instead of typing the code.
:meth:`TelegramAdapter.pairing_link` builds the link, and
:meth:`TelegramAdapter._message_from` turns the start message back into its payload
with ``is_start`` set.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from agent_core.channels.adapter import (
    MAX_INBOUND_CHARS,
    MAX_LABEL_CHARS,
    SEND_REFUSED,
    TOKEN_REJECTED,
    TRANSPORT_UNREACHABLE,
    Backoff,
    ChannelAuthFailed,
    ChannelInUse,
    ChannelLimits,
    ChannelRefused,
    ChannelUnavailable,
    InboundMessage,
    PollResult,
    VerifiedIdentity,
    clean_untrusted_text,
)

#: A CONSTANT, and that is why this file does not go through
#: ``agent_core/net_vetting.py``. That module exists for destinations chosen by
#: untrusted or model-influenced input — a page the model named, a base URL
#: somebody typed — where a hostname can be made to resolve inside the trust
#: boundary. Nothing here is chosen by anybody: the host is this literal, the path
#: is a method name from this file, and no inbound message can move either. The day
#: a channel row can carry its own base URL is the day this needs vetting, and that
#: day is also the day the schema stops being a closed CHECK.
_API_ROOT = "https://api.telegram.org"

#: "Text of the message to be sent, 1-4096 characters after entities parsing"
#: — https://core.telegram.org/bots/api#sendmessage (read 2026-08-22).
_MAX_MESSAGE_CHARS = 4096

#: THE ONE NUMBER TELEGRAM DOES NOT PUBLISH. ``getUpdates`` documents ``timeout``
#: as "Timeout in seconds for long polling. Defaults to 0, i.e. usual short
#: polling. Should be positive, short polling should be used for testing purposes
#: only." — https://core.telegram.org/bots/api#getupdates (read 2026-08-22). It
#: states no maximum. 50 is chosen here, not read: it sits under the 60-second mark
#: where intermediaries commonly drop an idle connection, so an ordinary quiet
#: window ends with an empty answer rather than with a timeout that would read as
#: an outage and start the backoff. If Telegram ever publishes a ceiling, this
#: constant is where it goes.
_MAX_POLL_SECONDS = 50

#: "The status is set for 5 seconds or less (when a message arrives from your bot,
#: Telegram clients clear its typing status)" —
#: https://core.telegram.org/bots/api#sendchataction (read 2026-08-22). Re-issued
#: every 4 seconds while a turn runs, so the hint never lapses between refreshes.
_TYPING_HINT_EVERY_SECONDS = 4

#: How long ONE HTTP request may take before it counts as unreachable. The poll
#: gets its own, larger budget (the long poll is meant to sit there); everything
#: else is a small request that either answers quickly or is not going to.
_REQUEST_TIMEOUT_SECONDS = 15.0

#: Slack over the long-poll window, so a poll that Telegram holds for the full
#: ``timeout`` is not cut off by the client one instant before it answers.
_POLL_TIMEOUT_SLACK_SECONDS = 10.0

#: Where a start link points. A constant for the same reason ``_API_ROOT`` is one:
#: nothing a transport or a message says can move it.
_LINK_ROOT = "https://t.me"

#: The shape of a bot's username as ``getMe`` reports it: 5 to 32 characters of
#: letters, digits and underscores, starting with a letter. A value that fails this
#: is not put in a link, because the handle becomes part of a URL a phone opens.
_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")

#: What a start link may carry: 1 to 64 characters from ``A-Z a-z 0-9 _ -``. The
#: pairing code (``ABC-DEF``) fits. Both rules are taken from
#: https://core.telegram.org/bots/features#deep-linking as quoted in the build brief
#: of 2026-09-29, and this build did not re-read the page.
_START_PAYLOAD = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: The message Telegram sends when a person taps Start, whether they opened the bot
#: by name or through a start link. ``/start`` alone, ``/start <payload>``, and the
#: group form ``/start@<botname> <payload>``, all on one line. Anything else is an
#: ordinary message, including a message of several lines that begins with
#: ``/start``. A paired person's ordinary message must reach the model, and a start
#: link never carries a line break, so the pattern allows spaces and tabs before the
#: payload and nothing that crosses a line.
#:
#: The bot name after ``@`` is not checked against this bot. That is harmless. For
#: a sender who is not paired, the payload is offered exactly as a typed message
#: is, so a start addressed to another bot can do nothing a typed code could not.
#: For a paired sender, the only effect is the paired sentence in place of a model
#: turn.
_START_COMMAND = re.compile(r"/start(?:@[A-Za-z0-9_]+)?(?:[ \t]+(?P<payload>.*))?")


class TelegramAdapter:
    """The Bot API, behind ``ChannelAdapter``.

    ``client`` exists for tests: an ``httpx.Client`` wired to an
    ``httpx.MockTransport`` makes every path here exercisable without a network,
    which is what ``tests/test_channel_turn.py`` uses. In the app it is None and
    each request opens its own short-lived client — no connection pool is held
    across a suspend, and nothing survives a channel being switched off."""

    kind = "telegram"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.limits = ChannelLimits(
            max_message_chars=_MAX_MESSAGE_CHARS,
            max_poll_seconds=_MAX_POLL_SECONDS,
            supports_typing_hint=True,
            typing_hint_every_seconds=_TYPING_HINT_EVERY_SECONDS,
        )
        self.backoff = Backoff()
        self._client = client

    # --- the four contract methods -----------------------------------------

    def verify_token(self, token: str) -> VerifiedIdentity:
        """``getMe`` — the reply carries the bot's username, which becomes the
        "connected as" line. One request, both jobs (``provider.connect``'s rule)."""
        payload = self._request("getMe", token, {}, timeout=_REQUEST_TIMEOUT_SECONDS)
        result = payload.get("result")
        bot = result if isinstance(result, dict) else {}
        # ``username`` is what a person recognises (it is what they typed to find
        # the bot); ``first_name`` is the fallback for a bot that somehow has none.
        # Both are the transport's text and are cleaned like any other.
        name = clean_untrusted_text(bot.get("username") or bot.get("first_name"), MAX_LABEL_CHARS)
        # The handle is the same username, kept only when it has a username's shape.
        # It is checked rather than cleaned because it becomes part of a link.
        username = bot.get("username")
        handle = (
            username if isinstance(username, str) and _USERNAME.fullmatch(username) else None
        )
        return VerifiedIdentity(display_name=name, handle=handle)

    def poll(self, token: str, cursor: str | None, seconds: int) -> PollResult:
        """``getUpdates`` with ``offset`` and ``timeout``, filtered to message
        updates carrying text.

        THE OFFSET IS THE ACKNOWLEDGEMENT. Telegram drops an update once you ask
        past it, so the cursor returned here is ``highest update_id + 1`` and the
        SERVICE advances only after the messages have been handed on — never
        before. The consequence is stated rather than hidden: delivery is
        AT-LEAST-ONCE, and a crash between hand-off and the next poll re-delivers
        (owner decision 9, accepted for phases 1–3). On a floor with no tools at
        all, a duplicated turn costs a duplicated answer; the moment anything with
        an effect joins the floor, deduplication by ``update_id`` stops being a
        nicety.

        ``allowed_updates`` asks Telegram for message updates only. It is a
        NARROWING and not a security boundary — the filter below is what actually
        decides what becomes a message — but it keeps everything else (edits,
        callbacks, channel posts, my_chat_member) off the wire entirely."""
        window = max(1, min(seconds, self.limits.max_poll_seconds))
        params: dict[str, Any] = {
            "timeout": window,
            "allowed_updates": '["message"]',
        }
        if cursor:
            params["offset"] = cursor
        payload = self._request(
            "getUpdates", token, params, timeout=window + _POLL_TIMEOUT_SLACK_SECONDS
        )
        updates = payload.get("result")
        if not isinstance(updates, list):
            # A well-formed 200 that is not the documented shape. Nothing arrived
            # that this adapter can describe, so nothing arrived — and the cursor
            # is left exactly where it was rather than advanced past updates that
            # may never have been read.
            return PollResult(messages=(), next_cursor=cursor, dropped=0)
        messages: list[InboundMessage] = []
        dropped = 0
        highest: int | None = None
        for update in updates:
            if not isinstance(update, dict):
                dropped += 1
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                highest = update_id if highest is None else max(highest, update_id)
            built = self._message_from(update)
            if built is None:
                # No text, a media-only message, a sender Telegram did not name.
                # Counted, never guessed at: ``dropped`` is a floor.
                dropped += 1
                continue
            messages.append(built)
        # The cursor advances past EVERY update in this batch, including the ones
        # dropped for shape: a photo with no caption will never become a message,
        # and leaving it unacknowledged would make Telegram hand it back forever.
        next_cursor = str(highest + 1) if highest is not None else cursor
        return PollResult(messages=tuple(messages), next_cursor=next_cursor, dropped=dropped)

    def send(self, token: str, chat_id: str, text: str) -> str:
        """``sendMessage``. Refuses an oversized string rather than truncating one.

        No ``parse_mode``: the text goes as PLAIN TEXT. Addison's answer may contain
        underscores, asterisks and backticks from code or maths, and asking Telegram
        to parse them as markup means either a rejected message (unbalanced
        entities) or a mangled one. Plain text always arrives, exactly as written."""
        if len(text) > self.limits.max_message_chars:
            # The caller split wrong. Refuse loudly rather than cutting quietly —
            # a cut in the transport layer is a cut nobody can report.
            raise ChannelRefused(SEND_REFUSED)
        payload = self._request(
            "sendMessage",
            token,
            {"chat_id": chat_id, "text": text},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        result = payload.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return str(message_id) if message_id is not None else ""

    def working_hint(self, token: str, chat_id: str) -> None:
        """``sendChatAction`` with the typing action. NEVER RAISES — every failure
        mode here is "nothing visible happened", and a failed courtesy must not
        cost a turn."""
        try:
            self._request(
                "sendChatAction",
                token,
                {"chat_id": chat_id, "action": "typing"},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            return

    def pairing_link(self, handle: str | None, code: str) -> str | None:
        """``https://t.me/<handle>?start=<code>``, or None when either part fails
        Telegram's shape rules.

        Opening the link on a phone opens the bot, and tapping Start sends the bot
        ``/start <code>``, which :meth:`_message_from` turns back into the code.
        Both parts are validated here even though ``verify_token`` already checked
        the handle, because this method is where the URL is built."""
        if not isinstance(handle, str) or _USERNAME.fullmatch(handle) is None:
            return None
        if not isinstance(code, str) or _START_PAYLOAD.fullmatch(code) is None:
            return None
        return f"{_LINK_ROOT}/{handle}?start={code}"

    # --- the one place a request is made -----------------------------------

    def _request(self, method: str, token: str, params: dict, *, timeout: float) -> dict:
        """One Bot API call, with every failure translated into one of this
        design's four exceptions and one of Addison's own sentences.

        NOTHING FROM httpx REACHES A CALLER. An ``httpx`` exception's ``str()``
        carries the request URL, and on this API the URL carries the bot token — so
        a raised ``ChannelUnavailable(str(exc))`` would be a credential in whatever
        the caller does with the message. Every ``raise`` below therefore names a
        frozen constant, and no ``from exc`` chain is kept for the same reason.

        A 401 or 403 means the token. Any other 4xx means this specific request was
        refused, such as a chat that blocked the bot or a message Telegram would not
        take. A 5xx, a timeout and a transport error all mean "not right now".

        A 409 raises :class:`ChannelInUse`. Telegram sends it to ``getUpdates``
        when a webhook is set on the bot or when another program is polling the same
        token, and then it keeps coming until a person changes something outside
        Addison. Telegram also sends it once to the older of two open polls, which
        happens to Addison's own loop when a person presses Stop and then Start, and
        that one passes. The poll loop tells them apart by asking whether it is still
        the current loop. A current loop stops on the conflict, and a replaced loop
        returns without recording anything. A 409 is therefore never retried as an
        outage. "Check now" (``getMe``) still succeeds while the conflict lasts,
        which is why the state says what is wrong instead of saying Telegram is not
        answering."""
        url = f"{_API_ROOT}/bot{token}/{method}"
        client = self._client
        try:
            if client is not None:
                response = client.post(url, data=params, timeout=timeout)
            else:
                with httpx.Client(timeout=timeout) as owned:
                    response = owned.post(url, data=params)
        except Exception:
            raise ChannelUnavailable(TRANSPORT_UNREACHABLE) from None
        status = response.status_code
        if status in (401, 403):
            raise ChannelAuthFailed(TOKEN_REJECTED)
        if status == 429 or status >= 500:
            # A rate limit is a "not right now" and belongs with the outages: it is
            # the one 4xx that retrying later genuinely fixes, and the backoff above
            # this is exactly the right response to it.
            raise ChannelUnavailable(TRANSPORT_UNREACHABLE)
        if status == 409:
            raise ChannelInUse(SEND_REFUSED)
        if status >= 400:
            raise ChannelRefused(SEND_REFUSED)
        try:
            payload = response.json()
        except Exception:
            raise ChannelUnavailable(TRANSPORT_UNREACHABLE) from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            # A 200 that says ``ok: false``. Telegram puts its own explanation in
            # ``description``; it is deliberately NOT read — the person gets
            # Addison's sentence, and the transport does not get to write on their
            # screen.
            raise ChannelRefused(SEND_REFUSED)
        return payload

    # --- shape ---------------------------------------------------------------

    def _message_from(self, update: dict) -> InboundMessage | None:
        """One ``message`` update as an :class:`InboundMessage`, or None when it is
        not something a person typed at Addison.

        ``channel_id`` is left empty here: this adapter knows a transport, not
        which of Addison's rows is using it, and the service stamps the row id on
        the way past. Filling it in with a guess would be the adapter inventing a
        fact about Addison's configuration."""
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        text = clean_untrusted_text(message.get("text"), MAX_INBOUND_CHARS)
        if not text:
            return None
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        sender_id = sender.get("id")
        chat_id = chat.get("id")
        if sender_id is None or chat_id is None:
            return None
        if sender.get("is_bot") is True:
            # A bot talking to a bot is not a person asking Addison something. It
            # can never be paired (pairing is a code a human types), so this only
            # saves the work — but it says the intent where somebody will read it.
            return None
        label = clean_untrusted_text(
            sender.get("username") or sender.get("first_name") or "", MAX_LABEL_CHARS
        )
        # The start command is translated here, so nothing above this file needs to
        # know how Telegram spells it. The command itself is dropped and ``text``
        # keeps only the payload, which is the pairing code when the person scanned
        # the desktop's QR code. A bare ``/start`` becomes an empty text with
        # ``is_start`` set. It is kept rather than dropped for being empty, because a
        # paired phone that opens the chat again is answered with the paired
        # sentence.
        start = _START_COMMAND.fullmatch(text)
        if start is not None:
            text = (start.group("payload") or "").strip()
        date = message.get("date")
        return InboundMessage(
            channel_id="",
            chat_id=str(chat_id),
            sender_id=str(sender_id),
            sender_label=label,
            text=text,
            # Stamped by the SERVICE, which owns Addison's clock. Zero here is not
            # a claim about time; it is a field the next layer fills.
            received_at=0,
            sent_at=int(date) if isinstance(date, int) else 0,
            update_id=str(update.get("update_id", "")),
            is_start=start is not None,
        )
