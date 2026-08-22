"""The transport contract — everything a messaging channel must be able to do.

Everything transport-specific sits behind this file, and nothing above it knows
the word Telegram (docs/messaging-channel-plan.md §3.2, which owns this design).

FOUR VALUE TYPES, ONE PROTOCOL, THREE EXCEPTIONS. A ``Protocol`` rather than a
base class, matching how ``Tool`` and ``ShellBridge`` are declared in
``agent_core/tools/base.py``: an adapter is a shape somebody satisfies, not an
inheritance chain to join.

THE TEXT THAT ARRIVES IS SOMEBODY ELSE'S WRITING. ``InboundMessage.text`` and
``.sender_label`` are attacker-controlled — anyone who learns a bot's name can
send it words — and they are treated the way a tool server's names and prose are:
control characters stripped, length capped here at the door, never put through a
markdown renderer, and (for ``text``) screened before a model reads it. The
screening happens where the message becomes a turn (``channel_service.py``), so
this file's job is the cheap mechanical half: :func:`clean_untrusted_text`.

A TRANSPORT'S OWN ERROR TEXT IS NEVER SHOWN. Every exception below carries one
plain sentence Addison wrote. This is ``mcp_client``'s rule and it is here for the
same reason: a stranger's server (or a stranger's *bot*) that can choose the words
on somebody's screen can write those words to be believed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

# --- what one inbound message is -------------------------------------------


@dataclass(frozen=True)
class InboundMessage:
    """One thing a person typed on their phone, as the service receives it."""

    #: The ``channels`` row this arrived on.
    channel_id: str
    #: The transport's id for the conversation — what a reply is addressed to.
    chat_id: str
    #: The transport's id for the HUMAN. This is what pairing binds, and the only
    #: thing in this dataclass that is ever an authorization input.
    sender_id: str
    #: A display name, for the paired-devices list. UNTRUSTED TEXT, capped.
    sender_label: str
    #: What they typed. UNTRUSTED TEXT, capped, screened before a model sees it.
    text: str
    #: Unix seconds on ADDISON'S clock: when this process first saw the message.
    #: Never the transport's number — ordering and freshness inside Addison are
    #: measured with a clock nobody else can set.
    received_at: int
    #: Unix seconds the TRANSPORT claims the person sent it, or 0 when it did not
    #: say. THE ONE PLACE A TRANSPORT'S CLOCK IS READ, and it is read for exactly
    #: one question: was this message waiting in a queue while the Mac was asleep
    #: (owner decision 8, default *decline*)? Addison's own clock cannot answer
    #: that — a message that arrived during a fifty-second long poll and one that
    #: was queued overnight both come back the instant the poll returns, so
    #: ``received_at`` is "now" for both. A wrong value here costs at most one
    #: declined message and one plain sentence, which is the safe direction; it is
    #: never an input to pairing, to the gate, or to what may run.
    sent_at: int
    #: The transport's own cursor for this message. The service advances past it
    #: only after the message has been handed on (§3.3: the offset IS the
    #: acknowledgement), which is what makes delivery at-least-once.
    update_id: str


@dataclass(frozen=True)
class ChannelLimits:
    """What one transport can carry. THE ADAPTER'S ANSWER, never a constant in the
    service: the splitting rule above it has to be right for whatever transport is
    underneath, and a number invented one layer up is a number that goes stale
    silently."""

    #: What ONE outbound message may carry, in characters.
    max_message_chars: int
    #: The longest a single poll may be held open, in seconds.
    max_poll_seconds: int
    #: Whether the transport has a "working on it" signal at all.
    supports_typing_hint: bool
    #: How often a typing hint must be re-issued while a turn runs, in seconds.
    #: Zero where the transport has no such signal.
    typing_hint_every_seconds: int = 0


@dataclass(frozen=True)
class VerifiedIdentity:
    """Who a token turned out to belong to.

    ``verify_token`` returns this rather than a bare bool for ``provider.connect``'s
    reason exactly: *that request's reply is the answer*, one call doing both jobs.
    The absence of that pattern is what once made a connected Google key offer two
    models and answer 404 to every message (CLAUDE.md, multi-provider), so it is
    written into the contract rather than left to each adapter to remember."""

    #: The bot's own display name — what the Settings row says it is connected AS.
    #: Text the TRANSPORT supplied, so it is cleaned and capped like any other.
    display_name: str


@dataclass(frozen=True)
class PollResult:
    """What one poll found. An empty ``messages`` is the ORDINARY case."""

    messages: tuple[InboundMessage, ...]
    #: What to ask from next time. None leaves the cursor exactly where it was.
    next_cursor: str | None
    #: How many the adapter refused for SHAPE — no text, media-only, oversized.
    #: A FLOOR and not a total, the honesty rule MCP's ``skipped`` keeps: an
    #: adapter reports what it noticed itself dropping and never guesses at what
    #: the transport dropped before it.
    dropped: int = 0


# --- the failure vocabulary -------------------------------------------------
#
# One exception per outcome the SERVICE has a different answer for. Three, and no
# more, because a fourth would be a distinction nothing above acts on.


class ChannelError(RuntimeError):
    """Base: something went wrong reaching a transport. Carries one plain sentence
    Addison wrote, never the transport's own words."""


class ChannelAuthFailed(ChannelError):
    """The token is wrong, revoked, or was never valid. The channel STOPS and says
    so: retrying a rejected credential in a loop is how an account gets locked, and
    the only thing that fixes it is a person pasting a new token."""


class ChannelUnavailable(ChannelError):
    """Network, timeout, 5xx — a transport that could not answer RIGHT NOW. Back
    off and try again; nothing about this says the configuration is wrong."""


class ChannelRefused(ChannelError):
    """The transport said no to this specific send. Surfaced once, never retried in
    a loop: the same message will be refused the same way, and a retry loop against
    a refusal is how a rate limit becomes a ban."""


# --- plain sentences, frozen ------------------------------------------------
#
# Every one of these is Addison's own words, written for a person and not for a
# log. No transport's error string, status code or URL may join them (the URL
# carries the bot token on Telegram, which is the sharpest reason of all).

TOKEN_REJECTED = (
    "That token was refused. Check you pasted the whole thing from BotFather, and "
    "that the bot hasn't been deleted."
)
TRANSPORT_UNREACHABLE = (
    "Addison couldn't reach the messaging service just now. It will keep trying."
)
SEND_REFUSED = "Addison couldn't deliver that message to your phone."
MESSAGE_TOO_LONG = "Addison tried to send a message that was too long for this service."


# --- untrusted text, at the door --------------------------------------------

#: Everything below space except tab/newline, plus the delete character and the
#: unicode line/paragraph separators. Stripped rather than escaped: a control
#: character in a display name has no legitimate meaning and every illegitimate
#: one (a right-to-left override that makes a label read as something else, a
#: carriage return that hides the rest of a line) works by not being visible.
#: Written as ESCAPES rather than as the characters themselves, on purpose: the
#: whole point of this class is that its members are invisible, and a literal
#: right-to-left override pasted into a source file is a source file nobody can
#: review. Tab and newline are deliberately absent — a person's message may have
#: both. The two ranges after the ASCII ones are the unicode line/paragraph
#: separators and the bidi overrides, which is how a label reads as one name and
#: is another.
_CONTROL_CHARS = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]"
)

#: What one inbound message may carry INTO Addison. Generous enough for a pasted
#: paragraph, bounded because everything downstream of here — a model's context, a
#: stored row, a card — is something a stranger would otherwise get to size.
MAX_INBOUND_CHARS = 4000

#: What a display name may carry. A name, not a document.
MAX_LABEL_CHARS = 80


def clean_untrusted_text(value: object, cap: int) -> str:
    """Somebody else's text, reduced to something safe to carry: control characters
    dropped, whitespace tidied at the ends, length capped.

    NOT a sanitiser and not a screen. It removes the characters that lie about what
    a string IS (see ``_CONTROL_CHARS``) and bounds the size; it makes no judgement
    about the words, which is :mod:`agent_core.screening`'s job and happens later,
    where the verdict can travel with the text.

    Anything that is not a string comes back as ``""`` — a malformed frame from a
    transport is an empty message, never an exception on the poll thread."""
    if not isinstance(value, str):
        return ""
    cleaned = _CONTROL_CHARS.sub("", value).strip()
    if len(cleaned) > cap:
        # A hard cut, and no ellipsis: an ellipsis Addison added would be a
        # character the person did not type, in text a model is about to read.
        cleaned = cleaned[:cap]
    return cleaned


# --- backoff: the adapter's own, never the provider machinery's --------------


@dataclass
class Backoff:
    """Bounded, growing delay over CONSECUTIVE unavailability, reset on the first
    good poll.

    DELIBERATELY NOT ``providers.request_with_retry`` AND NOT THE ORCHESTRATOR'S
    ``_cooldowns`` (plan §3.2). The first is one retry with no backoff, sized for a
    model call a person is waiting on; the second is a map keyed by provider id, and
    sharing it would let a Telegram outage cool down an Anthropic key. A channel is
    not a model provider, and the honest way to say so is a separate, tiny thing.

    It is a VALUE the adapter owns and the service reads: ``channel.status`` reports
    ``seconds`` so a person can see the difference between *quiet* and *broken*,
    which is the whole reason the state is visible at all."""

    #: The first wait after a failure. Short — most outages are a dropped socket.
    first_seconds: int = 5
    #: The ceiling. A minute is long enough to stop hammering a service that is
    #: down and short enough that recovery is not something you wait for.
    max_seconds: int = 60
    #: The current wait, 0 when nothing has failed. Read by ``status``.
    seconds: int = 0
    #: How many consecutive failures. Display only; the delay is what acts.
    failures: int = field(default=0)

    def note_failure(self) -> int:
        """Record one unavailable poll and return how long to wait before the next."""
        self.failures += 1
        self.seconds = min(
            self.first_seconds if self.seconds == 0 else self.seconds * 2, self.max_seconds
        )
        return self.seconds

    def note_success(self) -> None:
        """A poll worked. Everything resets — including after a long outage, because
        the next failure is a NEW outage and starting it at the ceiling would make a
        single blip cost a minute of silence."""
        self.seconds = 0
        self.failures = 0


# --- the contract -----------------------------------------------------------


class ChannelAdapter(Protocol):
    """What a transport must be able to do for Addison to speak through it."""

    #: The transport's id — ``"telegram"``. Matches the ``channels.kind`` CHECK in
    #: schema.sql, which is the database's own authority over the closed set.
    kind: str
    limits: ChannelLimits
    backoff: Backoff

    def verify_token(self, token: str) -> VerifiedIdentity:
        """One small request that both validates the credential and returns the
        identity behind it.

        Raises :class:`ChannelAuthFailed` on a rejected token and
        :class:`ChannelUnavailable` on anything else. Never returns a bare bool,
        and never reports success without having asked."""
        ...

    def poll(self, token: str, cursor: str | None, seconds: int) -> PollResult:
        """Ask for anything new, holding the request open up to ``seconds`` (bounded
        by ``limits.max_poll_seconds``).

        MUST RETURN AN EMPTY LIST RATHER THAN RAISE ON AN IDLE WINDOW: "nothing
        happened" is the ordinary case, and an exception is not how the ordinary
        case is spelled. :class:`ChannelUnavailable` means the transport could not
        be reached, which is a different fact."""
        ...

    def send(self, token: str, chat_id: str, text: str) -> str:
        """Deliver ONE message; return the transport's id for it.

        The CALLER has already split ``text`` to ``limits.max_message_chars``. An
        oversized string is REFUSED (:class:`ChannelRefused`) rather than
        truncated, because a silent cut in the transport layer is a cut nobody can
        report — the person on the phone would read a sentence that stops."""
        ...

    def working_hint(self, token: str, chat_id: str) -> None:
        """Best-effort "Addison is thinking" signal where the transport has one, and
        a no-op where it does not.

        IT MAY NEVER RAISE. A failed courtesy must not fail a turn — this is the one
        method in the contract whose every failure mode is "nothing visible
        happened"."""
        ...
