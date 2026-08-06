"""Is a key saved for this provider? — the three-way answer, and the two rules
that read it (secrets-and-keychain plan §4.1).

**Presence is not a secret, and it does not belong in the keychain.** Asking the OS
"is there a key?" is what generated the 60-second poll, the negative read cache, and
three probe variants — the plan's single largest source of that integration's size.
The authority is now ``provider_config.secret_presence``; this module owns the
vocabulary that column stores and the questions callers are allowed to ask of it.

**THREE outcomes, never two** (plan lesson 4). *Found* / *nothing saved* /
*unreadable* collapsed into a bool is the 2026-07-25 relay-routing bug: a dismissed
password dialog read as "no key saved", so a Simple turn was silently routed to the
external Setup Assistant relay while the person's key sat in the keychain. The two
rules below are separate functions for exactly that reason — ``UNKNOWN`` answers
*yes* to "might there be a key?" and *no* to "may this go to the relay?", and a
single boolean cannot say both.
"""

from __future__ import annotations

from enum import Enum


class SecretPresence(str, Enum):
    """What Addison has RECORDED about a provider's stored key.

    ``str``-valued so the enum member is what goes into (and comes out of) the
    ``provider_config.secret_presence`` TEXT column with no adapter.
    """

    #: A key was there the last time somebody with a reason to know looked.
    PRESENT = "present"
    #: Nothing is saved — a normal answer, and the ONLY one that may onboard.
    ABSENT = "absent"
    #: Unanswerable: the read failed (a key may well exist behind a dialog nobody
    #: answered), the row predates this column, or a restore reset it. Never "no key".
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: object) -> "SecretPresence":
        """Widen anything stored, restored or hand-edited into the vocabulary.

        Fails to ``UNKNOWN``, never to ``ABSENT``: an unrecognised value is by
        definition a signal Addison cannot read, and reading an unreadable signal as
        "no key saved" is the whole bug this module exists to prevent.
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


def may_reach_setup_relay(presence: SecretPresence) -> bool:
    """May a PRIMARY-bound turn hand off to the external Setup Assistant relay?

    **Only on ``ABSENT``.** This is the 2026-07-25 rule stated once, in one place, so
    no caller can re-derive it as ``not present``. ``UNKNOWN`` means Addison could not
    tell — and answering "no key" from a stale or unreadable signal is what sends a
    person's message to an external service while their key sits in the keychain.
    """
    return presence is SecretPresence.ABSENT


def may_have_a_key(presence: SecretPresence) -> bool:
    """Is it worth trying to USE a key for this provider (a catalog fetch, a
    reconnect)? True for ``PRESENT`` and ``UNKNOWN`` alike.

    Deliberately optimistic, and this is the safe direction: a false yes costs one
    request that fails and is retried on a later call, while a false no silently
    withholds the live model list from somebody who has a perfectly good key. It
    decides nothing about routing — see ``may_reach_setup_relay``.
    """
    return presence is not SecretPresence.ABSENT
