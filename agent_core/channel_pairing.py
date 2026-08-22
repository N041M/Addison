"""Pairing — which phone is allowed to talk to Addison.

================================ SAFETY FRAME ================================
**PAIRING IS THE AUTHORIZATION BOUNDARY, AND MESSAGE CONTENT NEVER IS.** No
keyword, no prefix, no "only obey messages that start with", nothing a message can
say about itself. Step 8's reasoning is the whole argument and it transfers
unchanged: a fixed prefix *"is forgeable by anything that can write English"*, and
the fix is a code minted at the moment of asking, which no observed content could
have written down in advance (docs/messaging-channel-plan.md §3.7).

**THE DESKTOP SHOWS THE CODE; THE PHONE SENDS IT.** Not the reverse. Sending a
code to a number the person types in requires already knowing an address, which is
the thing pairing exists to establish — and on most transports a bot cannot message
somebody who has not messaged it first anyway. This direction also puts the secret
on the TRUSTED screen and the proof on the wire, which is the correct way round.

**SILENCE ON EVERY NON-MATCH.** A reply is an oracle: it tells a stranger who
guessed a bot name that the bot is real, that it is running, and that somebody is
behind it. So only :attr:`PairingOutcome.MATCHED` produces any outbound message —
a wrong code, an expired window and an exhausted budget all say nothing at all,
and the attempt is still spent. The only thing an unpaired message produces is a
COUNTER the desk can see, so the person knows strangers are knocking.

**WHAT THE CODE DEFENDS, at its real strength.** It stops a stranger who knows the
bot's name from becoming the operator, and it stops observed content from
pre-scripting the pairing, because a code that did not exist when the instruction
was written cannot be quoted in it. **What it does not defend**: a person who is
shown a code and types it somewhere else, and a phone already unlocked in somebody
else's hand (plan §6).
=============================================================================

THE CODE ITSELF IS ``agent_core/automation_nonce.py``, REUSED AND NOT
REIMPLEMENTED. That module is pure and stateless — ``mint()``, ``normalise()``,
``matches()`` (constant-time) and ``MAX_ATTEMPTS`` — with an alphabet whose
lookalikes are already removed for personas 54 and 68. **There is no expiry in
that module and none is added to it**: its arming caller holds the attempt budget
in its own locals, because lifetime belongs to whoever holds the state. Here that
is :class:`PendingPairing`, which the service owns in memory.

PENDING PAIRINGS ARE GONE ON RESTART, and that is correct: an open pairing window
is a moment, not a setting. A pairing that COMPLETED is a row in
``channel_pairings`` — which is deliberately excluded from snapshot capture, so a
restore can never put back an authorization somebody revoked (plan §3.8).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from agent_core import automation_nonce

#: How long a pairing window stays open. Long enough to pick up a phone, unlock
#: it, find the bot and type six characters; short enough that a code shown on a
#: screen somebody walked away from is not still live an hour later. The number
#: lives HERE rather than in ``automation_nonce`` for the reason that module's
#: docstring gives: lifetime belongs to whoever holds the state.
PAIRING_WINDOW_SECONDS = 300


class PairingOutcome(str, Enum):
    """What one offered code turned out to be.

    Four values, and exactly one of them speaks: ``MATCHED``. The other three are
    kept apart because the SERVICE does different bookkeeping for each — a wrong
    code spends an attempt and leaves the window open, an expired or exhausted one
    closes it — and never because a person is told which happened."""

    MATCHED = "matched"
    WRONG = "wrong"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"


@dataclass
class PendingPairing:
    """An open pairing window: one code, one deadline, one attempt budget.

    MUTABLE, unlike most value types in this tree, because ``attempts_left`` is the
    thing that changes and it must change in the object the service is holding — a
    frozen copy would let a caller spend the budget on a duplicate and leave the
    original untouched, which is a budget that does not bound anything."""

    channel_id: str
    #: The minted code, in the form the person sees it (``ABC-DEF``).
    code: str
    expires_at: int
    attempts_left: int


def begin(channel_id: str, now: int | None = None) -> PendingPairing:
    """Open a pairing window for one channel.

    A FRESH code every time, from ``automation_nonce.mint()`` — six characters from
    a 26-symbol alphabet, ``secrets``-backed. Beginning again replaces the previous
    window rather than extending it, which is what makes a guessing strategy
    pointless rather than merely slow: the budget resets, and so does the target."""
    stamp = int(time.time()) if now is None else now
    return PendingPairing(
        channel_id=channel_id,
        code=automation_nonce.mint(),
        expires_at=stamp + PAIRING_WINDOW_SECONDS,
        attempts_left=automation_nonce.MAX_ATTEMPTS,
    )


def offer(
    pending: PendingPairing, sender_id: str, typed: object, now: int | None = None
) -> PairingOutcome:
    """Somebody sent something while a window was open. Was it the code?

    ORDER IS BEHAVIOUR, and it is expiry first: a window whose deadline has passed
    answers ``EXPIRED`` even for the right code, and spends no attempt — the budget
    exists to bound guessing inside a live window, and there is nothing left to
    guess at once one has closed. Then the constant-time compare, then the
    decrement, so a wrong answer always costs one whatever else is true.

    ``sender_id`` IS NOT CONSULTED, and the parameter is here anyway. Matching is on
    the code alone: the code IS the proof, and the sender is what the caller binds
    once the proof holds. It stays in the signature because the call site should
    read as *this sender offered this code* — and because a later per-sender budget
    (one stranger must not be able to burn a window somebody else opened) has its
    hook here rather than needing a new parameter and a new caller.

    Nothing here writes a row, sends a message or touches a store. The caller does
    all three, and only on ``MATCHED``."""
    stamp = int(time.time()) if now is None else now
    if stamp >= pending.expires_at:
        return PairingOutcome.EXPIRED
    if pending.attempts_left <= 0:
        return PairingOutcome.EXHAUSTED
    if automation_nonce.matches(typed, pending.code):
        return PairingOutcome.MATCHED
    pending.attempts_left -= 1
    # The budget having just reached zero is reported as EXHAUSTED rather than
    # WRONG, so the service can close the window on the same answer that spends
    # the last attempt instead of waiting for a fourth message that may never come.
    return PairingOutcome.WRONG if pending.attempts_left > 0 else PairingOutcome.EXHAUSTED
