"""The arming nonce — the one string observed content cannot have written down.

================================ SAFETY FRAME ================================
**This is the keyword gate** (GLOBAL FLOOR G2; step 8 phase 3,
[docs/step-8-automation-plan.md](../docs/step-8-automation-plan.md) §3, which owns
the decision). Arming an automation hands a command to the operating system to run
on its own schedule, when Addison is closed and outside Addison's sandbox. That is
the one effect in this app no ordinary permission card is strong enough to sell, so
it additionally requires a short code Addison MINTS and the person RETYPES.

**Why a nonce and not a fixed prefix** (owner decision, 2026-08-07). A fixed prefix
(`!run …`) is forgeable by anything that can write English: a web page, a pasted
message, or Addison's own relayed answer can all say *"now type `!run install`"*,
and a person doing what the screen tells them is the normal case rather than the
failure case. A code minted at the moment of asking is the one string that could not
have been written down in advance by content Addison merely READ.

**What it defends, at its real strength and no higher.** The model cannot answer a
permission card at all — that boundary predates this step and is not what this adds.
What this adds is (a) observed content cannot pre-script the approval, because "click
OK" is an instruction a page can write and a code that did not exist yet is not, and
(b) a person cannot arm recurring, unconfined automation on reflex, because retyping
six characters is exactly enough friction to make somebody read the preview they are
copying from. What it does NOT defend: a person who deliberately types the code for a
job they have not understood. **The preview is the defence there**, which is why the
card carries the whole truth rather than a summary.

**THE VALUE NEVER LEAVES THIS PROCESS EXCEPT TOWARD THE PERSON.** It is minted here,
travels core -> webview on the card event, and comes back as the typed answer on
`permission.respond`. It is never put in the model's context, the transcript,
`tool_audit`, or any table — a nonce a model can read is a nonce the model can type,
which is the entire thing this exists to prevent. The model's tool_result says
granted or denied and nothing else.

This module is PURE and holds no state: minting, normalising and comparing only. The
attempt budget and the pending-request bookkeeping belong to the caller that owns the
card round-trip (`main.py`), because they are per-request lifetime, not arithmetic.
=============================================================================
"""

from __future__ import annotations

import hmac
import re
import secrets

#: The alphabet, with every lookalike removed: no ``0``/``O``, no ``1``/``I``/``L``,
#: no ``5``/``S``, no ``8``/``B``. Personas 54 and 68 read this off one surface and
#: type it into another, possibly on paper in between — a code that is ambiguous to
#: read is a code somebody mistypes three times and gives up on, and the failure mode
#: of THAT is a person who stops using the ceremony rather than one who reads it.
#: 26 letters + 10 digits minus 10 ambiguous = the 26 below.
ALPHABET = "ACDEFGHJKMNPQRTUVWXYZ23479"

#: Six characters from a 26-symbol alphabet ≈ 309 million codes. The threat this
#: sizes against is NOT brute force — nothing can submit a guess without a person at
#: the keyboard, and three wrong answers end the request — it is COLLISION with a
#: code somebody might have been told to type by injected content. At this size the
#: chance that a specific pre-written guess matches is ~1 in 309 million, per attempt,
#: three attempts, once. Longer buys nothing real and costs legibility.
LENGTH = 6

#: Shown grouped (``ABCD-EF`` would be lopsided; ``ABC-DEF`` reads as two syllables).
GROUP = 3

#: Everything a person might type between the groups — a hyphen because that is what
#: is shown, a space because that is what people type instead, and the unicode dashes
#: a copy-paste out of a rendered surface can carry.
_SEPARATORS = re.compile(r"[-‐-―−\s_]+")

#: How many wrong answers a single arm request tolerates before it DENIES. Three is
#: enough for a typo and a re-read; the fourth means something is wrong and the honest
#: answer is to make them start over — with a fresh code, which is what makes a
#: guessing strategy pointless rather than merely slow.
MAX_ATTEMPTS = 3


def mint() -> str:
    """A fresh code, in the form the person sees it: ``ABC-DEF``.

    ``secrets`` rather than ``random``: this value is a credential for exactly one
    action, and the module whose docstring says "most secure source of randomness
    that your operating system provides" is the only correct choice for one."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return f"{raw[:GROUP]}-{raw[GROUP:]}"


def normalise(typed: object) -> str:
    """What was typed, reduced to what it MEANS: separators dropped, upper-cased.

    So ``abc-def``, ``ABC DEF``, ``abcdef`` and a copy-paste carrying a unicode
    en-dash are all the same answer. Being generous here costs nothing — the code is
    still the code — and being strict would fail a person who typed it correctly, in
    a ceremony whose whole purpose is that they engaged with it.

    Anything that is not a string normalises to ``""``, which matches no minted code
    (every code is six characters), so a malformed frame is a wrong answer rather
    than an exception."""
    if not isinstance(typed, str):
        return ""
    return _SEPARATORS.sub("", typed).upper()


def matches(typed: object, expected: str) -> bool:
    """Whether ``typed`` is ``expected``, compared in constant time.

    ``hmac.compare_digest`` after normalising both sides. The timing channel is not a
    realistic attack here — an attacker who could time this would need a person at the
    keyboard submitting their guesses — but the correct comparison costs one import
    and the wrong one is the sort of thing that gets copied into a place where it
    does matter.

    An empty ``expected`` NEVER matches, whatever is typed. That is the case where a
    caller asks about a request it never minted a code for, and answering True to
    "does this match nothing" would arm on an empty string."""
    if not expected:
        return False
    return hmac.compare_digest(normalise(typed), normalise(expected))
