"""Pairing — which phone may talk to Addison (messaging channels, PHASE 2).

[docs/plans/messaging-channel-plan.md](../docs/plans/messaging-channel-plan.md) §3.7 owns the
design. What these tests hold:

  (1) the code is MINTED, not fixed — a fresh one per window, from the shared
      ``automation_nonce`` module rather than a second implementation of it;
  (2) the attempt budget and the expiry both BOUND a window, in that order, and a
      spent window is closed rather than left to be guessed at;
  (3) SILENCE ON EVERY NON-MATCH — only ``MATCHED`` is an outcome that can produce
      an outbound message, which is what stops a reply from telling a stranger that
      the bot is real and somebody is behind it;
  (4) REVOCATION ANSWERS IN EVERY PROFILE, and takes the row with it, because a
      tightening must never be what a profile switch traps;
  (5) ``automation_nonce`` GAINED NOTHING. Pairing needed a lifetime and the arming
      ceremony does not have one; the plan is explicit that no expiry may be added
      to that module, so the deadline lives with whoever holds the state.
  (6) Start links (2026-09-29). The Telegram adapter turns ``/start <code>`` back
      into the code with a start flag, keeps the bot's handle only when it has a
      username's shape, and builds ``https://t.me/<bot>?start=<code>`` only from a
      valid handle and a valid code. A paired phone that scans again is checked
      against the window with ``confirms``, which spends no attempt.
  (7) "Pair a phone" starts listening through the switch's own checks, returns the
      link, and records what it learned about the token the way "Check now" does.
  (8) Only text that could be a code spends an attempt. Ordinary chat, a bare Start
      and a backlog Telegram hands over leave the window's budget alone, while every
      code-shaped guess still costs one.

Every test here was mutation-proven; the mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import httpx
import pytest

from agent_core import automation_nonce
from agent_core.channel_pairing import (
    PAIRING_WINDOW_SECONDS,
    PairingOutcome,
    PendingPairing,
    begin,
    confirms,
    offer,
)
from agent_core.channels.adapter import TOKEN_REJECTED
from agent_core.channels.telegram import TelegramAdapter
from agent_core.rpc.channels import _CHECK_FAILED, _GUARDS_REFUSE_REMOTE, _ONE_AT_A_TIME
from tests.conftest import _shutdown, build_server
from tests.test_channel_turn import _Channel
from tests.test_channel_turn import _server as _turn_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PAIRING_SRC = _REPO_ROOT / "agent_core" / "channel_pairing.py"
_NONCE_SRC = _REPO_ROOT / "agent_core" / "automation_nonce.py"


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _profile(harness, profile_id: str, request_id: int = 900) -> None:
    _call(harness, "profile.set", {"profileId": profile_id}, request_id)


# ---------------------------------------------------------------------------
# The code itself
# ---------------------------------------------------------------------------


def test_a_window_mints_a_fresh_code_from_the_shared_module():
    """The code is the one string observed content could not have written down in
    advance — which is only true if it is minted per window. A fixed prefix, or a
    code reused between windows, would be forgeable by anything that can write
    English (step 8's argument, transferred).

    Mutation: make ``begin`` return a constant code — the freshness assertion
    fails."""
    first = begin("chan-1")
    second = begin("chan-1")
    assert first.code != second.code, "two windows must not share a code"
    for pending in (first, second):
        # Six characters from the shared alphabet, grouped ABC-DEF.
        assert len(pending.code) == automation_nonce.LENGTH + 1
        assert pending.code[automation_nonce.GROUP] == "-"
        assert set(pending.code.replace("-", "")) <= set(automation_nonce.ALPHABET)
    assert first.attempts_left == automation_nonce.MAX_ATTEMPTS
    # The deadline is this module's, set from the clock at the moment of asking.
    minted = begin("chan-1", now=1_000)
    assert minted.expires_at == 1_000 + PAIRING_WINDOW_SECONDS


def test_the_pairing_code_module_is_reused_and_not_reimplemented():
    """``channel_pairing`` must MINT and COMPARE through ``automation_nonce``, never
    with its own ``secrets`` call or its own ``==``. Two implementations of a
    credential comparison agree until one of them is edited, and the one that would
    be edited is the newer one.

    Structural, because the property is about which code runs rather than about a
    return value. Mutation: replace ``automation_nonce.matches`` with ``typed ==
    expected`` — this fails, naming the import."""
    tree = ast.parse(_PAIRING_SRC.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "agent_core" in imported or "agent_core.automation_nonce" in imported
    source = _PAIRING_SRC.read_text(encoding="utf-8")
    assert "automation_nonce.mint()" in source
    assert "automation_nonce.matches(" in source
    for forbidden in ("import secrets", "import hmac", "compare_digest"):
        assert forbidden not in source, (
            f"channel_pairing.py spells its own {forbidden} — the nonce module owns that"
        )


def test_no_expiry_was_added_to_the_arming_nonce_module():
    """The plan is explicit: ``automation_nonce`` is PURE and stateless, and lifetime
    belongs to whoever holds the state. Pairing needed a deadline; the arming
    ceremony has none, and giving it one to serve a second caller would put a
    lifetime rule into the module both callers share.

    Mutation: add ``EXPIRY_SECONDS`` or an ``expires_at`` to automation_nonce.py —
    this fails."""
    source = _NONCE_SRC.read_text(encoding="utf-8")
    for forbidden in ("expires", "expiry", "EXPIRY", "time.time", "import time"):
        assert forbidden not in source, (
            f"automation_nonce.py grew {forbidden!r} — lifetime belongs to its callers"
        )


# ---------------------------------------------------------------------------
# Offering a code
# ---------------------------------------------------------------------------


def _window(code: str = "ABC-DEF", *, expires_at: int = 1_000, attempts: int = 3) -> PendingPairing:
    return PendingPairing(
        channel_id="chan-1", code=code, expires_at=expires_at, attempts_left=attempts
    )


def test_the_right_code_matches_however_it_was_typed():
    """Separators dropped, case ignored — ``automation_nonce.normalise``'s generosity,
    inherited rather than re-decided. Being strict here would fail somebody who typed
    the code correctly, in a ceremony whose whole point is that they engaged with it.

    Mutation: compare the raw strings — the lowercase and spaced forms fail."""
    for typed in ("ABC-DEF", "abc-def", "ABC DEF", "abcdef", "abc—def"):
        window = _window()
        assert offer(window, "sender-1", typed, now=0) is PairingOutcome.MATCHED
        assert window.attempts_left == 3, "a match must not spend an attempt"


def test_a_wrong_code_spends_an_attempt_and_the_third_one_closes_the_window():
    """Three wrong answers end the window. The third is reported as EXHAUSTED rather
    than WRONG so the caller can close it on the same answer that spends the last
    attempt, instead of waiting for a fourth message that may never come.

    Mutation: drop the ``attempts_left -= 1`` — the budget never runs out."""
    window = _window()
    assert offer(window, "s", "AAA-AAA", now=0) is PairingOutcome.WRONG
    assert window.attempts_left == 2
    assert offer(window, "s", "AAA-AAA", now=0) is PairingOutcome.WRONG
    assert window.attempts_left == 1
    assert offer(window, "s", "AAA-AAA", now=0) is PairingOutcome.EXHAUSTED
    assert window.attempts_left == 0
    # And a spent window never matches again, even for the right code: the budget is
    # what bounds guessing, so a correct guess arriving after it is still no.
    assert offer(window, "s", "ABC-DEF", now=0) is PairingOutcome.EXHAUSTED


def test_an_expired_window_refuses_the_right_code_and_spends_nothing():
    """Expiry is asked FIRST. A window whose deadline has passed answers EXPIRED even
    for the right code, and spends no attempt — the budget exists to bound guessing
    inside a live window, and there is nothing left to guess at once one has closed.

    Mutation: move the expiry check below the match — the right code pairs an hour
    after the code was shown."""
    window = _window(expires_at=1_000)
    assert offer(window, "s", "ABC-DEF", now=1_000) is PairingOutcome.EXPIRED
    assert offer(window, "s", "ABC-DEF", now=9_999) is PairingOutcome.EXPIRED
    assert window.attempts_left == 3
    # One second earlier it is still live.
    assert offer(window, "s", "ABC-DEF", now=999) is PairingOutcome.MATCHED


def test_the_window_is_minutes_and_not_hours():
    """A code shown on a screen somebody walked away from must not still be live an
    hour later; a code that expires before a person can pick up their phone is a
    ceremony nobody completes. Both directions, pinned."""
    assert 60 <= PAIRING_WINDOW_SECONDS <= 900


def test_only_a_match_is_ever_an_outcome_that_speaks():
    """The silence rule, at the level this module can hold it: there are exactly four
    outcomes and exactly one of them means "send something". The behavioural half —
    that nothing goes out on the wire for the other three — is in
    ``tests/test_channel_turn.py``, where a real transport can be watched.

    Mutation: add a fifth outcome meaning "tell them it was wrong" — this fails."""
    assert {o.value for o in PairingOutcome} == {"matched", "wrong", "expired", "exhausted"}


def test_the_pairing_module_reaches_nothing_but_the_nonce():
    """``offer`` decides and nothing else: no row, no message, no transport. The
    caller does all three, and only on MATCHED.

    Asserted over the IMPORTS rather than over the prose, because that is the
    property — a module that cannot reach a store cannot write one by accident.
    Mutation: import ``agent_core.memory.store`` here — this fails, naming it."""
    tree = ast.parse(_PAIRING_SRC.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    assert targets <= {"__future__", "time", "dataclasses", "enum", "agent_core"}, (
        f"channel_pairing.py imports more than the nonce and the stdlib: {sorted(targets)}"
    )


# ---------------------------------------------------------------------------
# Revocation — in every profile
# ---------------------------------------------------------------------------


def _seed_channel_and_pairing(harness, channel_id: str = "chan-1", pairing_id: str = "pair-1"):
    """A saved channel with one paired phone, written the only honest way a test can:
    through the server's own store, on the worker's connection, by asking the server
    to do it. ``channel.add`` needs Developer, so the caller sets that first."""
    conn = sqlite3.connect(harness.server.store.db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        "INSERT INTO channels (id, kind, name, enabled, token_present, created_at) "
        "VALUES (?, 'telegram', 'My phone', 0, 'unknown', 1)",
        (channel_id,),
    )
    conn.execute(
        "INSERT INTO channel_pairings (id, channel_id, sender_id, label, paired_at) "
        "VALUES (?, ?, 'sender-1', 'petr', 1)",
        (pairing_id, channel_id),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("profile_id", ["simple", "developer", "custom"])
def test_revoking_a_pairing_answers_in_every_profile(tmp_path, profile_id):
    """A pairing is an AUTHORIZATION, and taking one away is a tightening. Step 8
    phase 4 established the rule when Simple kept Remove and only Remove: a
    tightening must never be the thing a profile switch traps.

    Mutation: add ``if self._mode() is not PolicyMode.OPEN: return {...}`` to
    ``_channel_revoke_pairing`` — the simple case fails."""
    harness = build_server(tmp_path)
    try:
        # The row has to exist before the profile moves, and writing it needs no
        # profile at all — it goes in through a second connection.
        harness.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "channel.list", "params": {}})
        harness.writer.wait_for(lambda f: f.get("id") == 1)
        _seed_channel_and_pairing(harness)
        _profile(harness, profile_id)
        listed = _call(harness, "channel.pairings", {"id": "chan-1"}, 10)
        assert [row["label"] for row in listed["pairings"]] == ["petr"]
        assert _call(harness, "channel.revokePairing", {"pairingId": "pair-1"}, 11) == {"ok": True}
        assert _call(harness, "channel.pairings", {"id": "chan-1"}, 12)["pairings"] == []
    finally:
        _shutdown(harness.reader, harness.thread)


def test_a_pairing_list_never_carries_the_transports_id_for_the_person(tmp_path):
    """``sender_id`` authorises nothing on the webview's side, and it is the one field
    that would let this surface identify a person on an outside service. The label is
    what a person recognises; the pairing id is what Revoke needs.

    Mutation: add ``"senderId": row["sender_id"]`` to the wire row — this fails."""
    harness = build_server(tmp_path)
    try:
        harness.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "channel.list", "params": {}})
        harness.writer.wait_for(lambda f: f.get("id") == 1)
        _seed_channel_and_pairing(harness)
        rows = _call(harness, "channel.pairings", {"id": "chan-1"}, 10)["pairings"]
        assert rows and set(rows[0]) == {"id", "label", "pairedAt"}
        assert "sender-1" not in str(rows)
    finally:
        _shutdown(harness.reader, harness.thread)


def test_a_pairing_window_is_gone_when_the_process_is(tmp_path):
    """An open pairing window is a moment, not a setting: it lives in memory on the
    service and no column holds it. A window that survived a restart would be a code
    somebody saw yesterday, still live today.

    Mutation: persist the pending window in ``settings`` — the second server would
    then answer with a pending window and this fails.

    The channel is checked first, against a fake transport, because "Pair a phone"
    now starts listening and refuses on a channel whose token nobody has checked."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel_id = _Channel(harness, telegram, provider).id
        opened = _call(harness, "channel.beginPairing", {"id": channel_id}, 7)
        assert opened["ok"] is True and len(opened["code"]) == 7
        assert harness.server._channel_service.pending_pairing(channel_id) is not None
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)

    second = build_server(tmp_path)
    try:
        assert second.server._channel_service.pending_pairing(channel_id) is None
    finally:
        _shutdown(second.reader, second.thread)


def test_the_code_is_never_written_to_the_database(tmp_path):
    """THE VALUE NEVER LEAVES THIS PROCESS EXCEPT TOWARD THE PERSON — the property
    ``automation_nonce``'s caller already keeps, and the one most likely to be lost in
    a second implementation. A code in a table is a code a restore can bring back and
    a plaintext sidecar can carry.

    Mutation: store the pending window in a settings row — the scan finds it.

    The start link carries the code, so it is held to the same rule and scanned
    for too."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel_id = _Channel(harness, telegram, provider).id
        opened = _call(harness, "channel.beginPairing", {"id": channel_id}, 7)
        code, link = opened["code"], opened["link"]
        db_path = harness.server.store.db_path
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)
    blob = Path(db_path).read_bytes()
    assert code.encode() not in blob
    assert code.replace("-", "").encode() not in blob
    assert link.encode() not in blob


# ---------------------------------------------------------------------------
# A paired phone that scans again: confirming a window spends nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["hello", "hello?", "hi there", "", "ACDEFGH", "acd efgh", None])
def test_text_that_cannot_be_a_code_spends_no_attempt(typed):
    """``matches`` compares normalised strings, so text that is not six characters
    from the code alphabet once normalised can never match a minted code. It is
    answered WRONG with the budget untouched, so "hello?" typed before the code, a
    bare Start and a backlog of ordinary messages no longer use the window up. The
    seven-character cases are made only of alphabet characters, so it is the length
    rule that turns them away. "hello?" is six characters, so it is the alphabet rule.

    Mutations: remove the ``could_be_code`` check from ``offer`` (every case spends);
    change ``== LENGTH`` to ``>= LENGTH`` in ``could_be_code`` (the seven-character
    cases spend); drop the alphabet test from ``could_be_code`` ("hello?" spends)."""
    window = _window(code="ACD-EFG")
    assert offer(window, "s", typed, now=0) is PairingOutcome.WRONG
    assert window.attempts_left == 3


def test_every_code_shaped_guess_still_spends_one_and_three_close_the_window():
    """The budget still bounds every guess that could match. Ordinary chat between
    the guesses changes nothing, and the third wrong code-shaped guess closes the
    window. The right code, typed however, still matches.

    Mutation: return WRONG before the decrement for every miss — the budget never
    runs out and this fails on the third guess."""
    window = _window(code="ACD-EFG")
    assert offer(window, "s", "hello", now=0) is PairingOutcome.WRONG
    assert offer(window, "s", "CDE-FGH", now=0) is PairingOutcome.WRONG
    assert window.attempts_left == 2
    assert offer(window, "s", "is this the right bot?", now=0) is PairingOutcome.WRONG
    assert offer(window, "s", "cde fgh", now=0) is PairingOutcome.WRONG
    assert window.attempts_left == 1
    assert offer(window, "s", "7777-77", now=0) is PairingOutcome.EXHAUSTED
    assert window.attempts_left == 0

    minted = begin("chan-1", now=0)
    assert offer(minted, "s", minted.code.lower().replace("-", " "), now=0) is (
        PairingOutcome.MATCHED
    )


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("ACD-EFG", True),
        ("acd efg", True),
        ("2347-9A", True),
        ("ABC-DEF", False),
        ("ACD-EF0", False),
        ("ACD-EF", False),
        ("ACD-EFGH", False),
        ("", False),
        (None, False),
    ],
)
def test_a_code_shape_is_six_characters_from_the_alphabet(typed, expected):
    """The predicate on its own. ``B`` and ``0`` are among the lookalikes the
    alphabet leaves out, so a code containing them was never minted.

    Mutation: drop the alphabet test from ``could_be_code`` — the ``B`` and ``0``
    cases pass as codes."""
    assert automation_nonce.could_be_code(typed) is expected
    assert all(automation_nonce.could_be_code(begin("c").code) for _ in range(100))


def test_only_the_pairing_window_skips_text_that_cannot_be_a_code():
    """The arming ceremony counts every wrong answer against its budget, and this
    change leaves it that way. ``could_be_code`` is defined in the nonce module and
    called from ``channel_pairing.py`` and nowhere else in ``agent_core``.

    Mutation: call ``automation_nonce.could_be_code`` from the arming path in
    ``main.py`` — this fails, naming the file."""
    allowed = {_NONCE_SRC.resolve(), _PAIRING_SRC.resolve()}
    for path in sorted((_REPO_ROOT / "agent_core").rglob("*.py")):
        if path.resolve() in allowed:
            continue
        assert "could_be_code" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(_REPO_ROOT)} uses could_be_code, which only pairing may"
        )
    assert "automation_nonce.could_be_code(" in _PAIRING_SRC.read_text(encoding="utf-8")


def test_confirming_the_code_of_a_live_window_spends_no_attempt():
    """``confirms`` is for a sender who is already paired and scans the QR code
    again. A match only closes the window on the desktop, so a mismatch has nothing
    to guess at and spends nothing. The same normalising as ``offer`` applies.

    Mutation: decrement ``attempts_left`` on a mismatch — the budget drops to 2 and
    this fails."""
    window = _window()
    assert confirms(window, "abc def", now=0) is True
    assert confirms(window, "AAA-AAA", now=0) is False
    assert confirms(window, "", now=0) is False
    assert window.attempts_left == 3


def test_an_expired_or_spent_window_is_never_confirmed():
    """Expiry and a spent budget close a window for ``confirms`` exactly as they do
    for ``offer``. A window that is over has nothing left to close.

    Mutations: drop the expiry check (the expired case matches); drop the
    ``attempts_left <= 0`` check (the spent case matches)."""
    assert confirms(_window(expires_at=1_000), "ABC-DEF", now=1_000) is False
    assert confirms(_window(expires_at=1_000), "ABC-DEF", now=999) is True
    assert confirms(_window(attempts=0), "ABC-DEF", now=0) is False


# ---------------------------------------------------------------------------
# Start links, at the adapter: the only file that knows Telegram's spelling
# ---------------------------------------------------------------------------


def _update(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1,
            "text": text,
            "chat": {"id": 999},
            "from": {"id": 77, "username": "petr", "is_bot": False},
        },
    }


@pytest.mark.parametrize(
    "typed,payload",
    [
        ("/start ABC-DEF", "ABC-DEF"),
        ("/start@addison_bot ABC-DEF", "ABC-DEF"),
        ("/start   ABC-DEF  ", "ABC-DEF"),
        ("/start", ""),
        ("/start@addison_bot", ""),
    ],
)
def test_a_start_message_arrives_as_its_payload_with_the_start_flag(typed, payload):
    """Scanning the desktop's QR code opens the bot, and tapping Start sends
    ``/start <code>``. The adapter drops the command, so nothing above it has to
    know how Telegram spells it. A bare ``/start``, which Telegram sends the first
    time anybody opens a bot, arrives as an empty start instead of being dropped
    for having no text.

    Mutations: remove the translation in ``_message_from`` (the text keeps the
    command and the flag stays False); drop ``(?:@[A-Za-z0-9_]+)?`` from
    ``_START_COMMAND`` (the group form stops matching); return None for an empty
    text after the translation (a bare start is dropped)."""
    message = TelegramAdapter()._message_from(_update(typed))
    assert message is not None, f"{typed!r} was dropped"
    assert message.is_start is True
    assert message.text == payload


@pytest.mark.parametrize("typed", ["ABC-DEF", "/startle", "hello /start ABC-DEF", "/stop"])
def test_anything_else_arrives_as_an_ordinary_message(typed):
    """Only the start command is translated. A typed code still arrives as the code,
    and text that only contains ``/start`` somewhere is somebody's words.

    Mutation: ``_START_COMMAND.search`` instead of ``fullmatch`` — ``/startle`` and
    the third case become starts."""
    message = TelegramAdapter()._message_from(_update(typed))
    assert message is not None
    assert message.is_start is False
    assert message.text == typed


def _answering_get_me(result: dict) -> TelegramAdapter:
    return TelegramAdapter(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True, "result": result})
            )
        )
    )


@pytest.mark.parametrize(
    "username,handle",
    [
        ("addison_bot", "addison_bot"),
        ("Addison_Home_Bot", "Addison_Home_Bot"),
        ("abcde", "abcde"),
        ("a" + "b" * 31, "a" + "b" * 31),
        ("abcd", None),
        ("a" + "b" * 32, None),
        ("1addison_bot", None),
        ("addison-bot", None),
        ("addison_bot?start=EVIL", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_the_handle_is_kept_only_when_it_has_a_usernames_shape(username, handle):
    """The handle becomes part of a URL a phone opens, so ``getMe``'s username is
    kept only when it has the shape of a Telegram username: 5 to 32 letters, digits
    and underscores, starting with a letter. The display name is unaffected.

    Mutations: set ``handle = username`` without the check (every None row fails);
    widen ``{4,31}`` to ``{0,31}`` (the four-character row fails)."""
    result = {"first_name": "Addison"} if username is None else {"username": username}
    identity = _answering_get_me(result).verify_token("tok")
    assert identity.handle == handle
    if username is None:
        assert identity.display_name == "Addison"


def test_the_pairing_link_opens_the_bot_with_the_code():
    """The link the desktop shows as a QR code. Every code the nonce module mints
    fits Telegram's start payload, so a link can always be built for one.

    Mutation: mint codes with a space between the groups — no link is built for
    them and this fails."""
    adapter = TelegramAdapter()
    assert (
        adapter.pairing_link("addison_bot", "ABC-DEF") == "https://t.me/addison_bot?start=ABC-DEF"
    )
    for _ in range(200):
        code = begin("chan-1").code
        assert adapter.pairing_link("addison_bot", code) == (
            f"https://t.me/addison_bot?start={code}"
        )
    assert adapter.pairing_link("addison_bot", "A" * 64) is not None


@pytest.mark.parametrize(
    "handle",
    [None, "", "abcd", "addison bot", "addison_bot/x", "addison_bot?a=b", "a" * 40],
)
def test_no_link_is_built_for_a_handle_without_a_usernames_shape(handle):
    """Mutation: drop the handle check in ``pairing_link`` — a link is built around
    whatever the handle was, including a path or a query."""
    assert TelegramAdapter().pairing_link(handle, "ABC-DEF") is None


@pytest.mark.parametrize(
    "code", ["", "ABC DEF", "ABC/DEF", "ABC-DEF&x=1", "A" * 65, "\u00c4BC-DEF"]
)
def test_no_link_is_built_for_a_code_a_start_link_cannot_carry(code):
    """Telegram's start payload is 1 to 64 characters of ``A-Z a-z 0-9 _ -``.

    Mutation: drop the code check in ``pairing_link`` — the link carries a space, a
    second query parameter or an over-long payload."""
    assert TelegramAdapter().pairing_link("addison_bot", code) is None


# ---------------------------------------------------------------------------
# "Pair a phone" starts listening, and returns the link
# ---------------------------------------------------------------------------


def _any_answer(harness, method: str, params: dict, request_id: int) -> dict:
    """The frame for one request whether it is a result or an error, so a test can
    assert which it was rather than wait for a result that never comes."""
    harness.reader.feed({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    return harness.writer.wait_for(
        lambda f: f.get("id") == request_id and ("result" in f or "error" in f)
    )


def test_pair_a_phone_starts_listening_when_a_restart_left_nothing_running(tmp_path):
    """The failure the owner hit. The row said the channel was on, nothing starts a
    loop when the app opens, and "Pair a phone" minted a code that nothing was
    listening for. It now starts the loop through the switch's own checks, and it
    returns the start link the desktop shows as a QR code.

    The restart is reproduced as the state it leaves behind: the row says the
    channel is on, and the service runs no loop for it.

    Mutations: remove the ``_start_listening`` call from ``_channel_begin_pairing``
    (nothing listens); stop adding ``link`` to the answer."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)
        service = harness.server._channel_service
        assert _call(harness, "channel.setEnabled", {"id": channel.id, "enabled": True}, 10)["ok"]
        service.stop(channel.id)
        assert _call(harness, "channel.list", {}, 11)["channels"][0]["enabled"] is True
        assert service.listening_channels() == []

        opened = _call(harness, "channel.beginPairing", {"id": channel.id}, 12)
        assert opened["ok"] is True
        assert opened["link"] == f"https://t.me/addison_bot?start={opened['code']}"
        assert service.listening_channels() == [channel.id]
        assert _call(harness, "channel.status", {"id": channel.id}, 13)["state"] == "listening"
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


def test_pair_a_phone_checks_an_unchecked_token_and_switches_the_channel_on(tmp_path):
    """"Pair a phone" asks the transport who the token belongs to, through the call
    "Check now" makes, and records the answer before the switch's checks read it.
    A person who pasted a token and went straight to pairing is therefore not told
    to press Check now, and the channel is switched on as the switch would do it.

    Mutations: drop the ``present`` write in ``_channel_begin_pairing`` (the switch
    refuses with its "Check now" sentence); pass the row read before the check to
    ``_start_listening`` (the same); drop ``set_channel_enabled`` from
    ``_start_listening`` (the row stays off)."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        _call(harness, "profile.set", {"profileId": "developer"}, 900)
        harness.server._channel_service._adapters["telegram"] = TelegramAdapter(
            client=httpx.Client(transport=httpx.MockTransport(telegram.handler))
        )
        _call(harness, "channel.add", {"kind": "telegram", "name": "My phone"}, 901)
        row = _call(harness, "channel.list", {}, 902)["channels"][0]
        assert (row["tokenPresent"], row["enabled"]) == ("unknown", False)

        opened = _call(harness, "channel.beginPairing", {"id": row["id"]}, 903)
        assert opened["ok"] is True and opened["link"].startswith("https://t.me/addison_bot?")
        row = _call(harness, "channel.list", {}, 904)["channels"][0]
        assert (row["tokenPresent"], row["enabled"]) == ("present", True)
        assert harness.server._channel_service.listening_channels() == [row["id"]]
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


@pytest.mark.parametrize("cause", ["guard", "another_channel"])
def test_pair_a_phone_refuses_with_the_switchs_own_reasons_and_opens_no_window(
    tmp_path, cause
):
    """"Pair a phone" and the switch share one set of checks, so each refusal the
    switch gives comes back from "Pair a phone" word for word. No window opens
    after a refusal, because a code that nothing is listening for can never be
    answered.

    Mutation: ignore the refusal ``_start_listening`` returns — a window opens and a
    code comes back in every case."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)
        service = harness.server._channel_service
        if cause == "guard":
            _call(harness, "profile.set", {"profileId": "custom"}, 20)
            _call(
                harness,
                "guards.set",
                {"destructiveCard": "per_invocation", "autoGrantScope": "none"},
                21,
            )
            expected = _GUARDS_REFUSE_REMOTE
        elif cause == "another_channel":
            _call(harness, "channel.add", {"kind": "telegram", "name": "The tablet"}, 20)
            rows = _call(harness, "channel.list", {}, 21)["channels"]
            other = next(row["id"] for row in rows if row["name"] == "The tablet")
            _call(harness, "channel.connect", {"id": other}, 22)
            assert _call(harness, "channel.setEnabled", {"id": other, "enabled": True}, 23)["ok"]
            expected = _ONE_AT_A_TIME.format(other="The tablet")
        listening_before = service.listening_channels()

        answer = _call(harness, "channel.beginPairing", {"id": channel.id}, 30)
        assert answer == {"ok": False, "error": expected}
        assert service.pending_pairing(channel.id) is None
        assert service.listening_channels() == listening_before
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


def test_pair_a_phone_that_could_not_check_a_new_token_says_the_check_failed(tmp_path):
    """Nothing has ever checked this token, and the check "Pair a phone" makes
    cannot reach Telegram. The switch's own answer would be "Press Check now, then
    switch it on", which makes no sense to somebody who pressed "Pair a phone". The
    person is told the check failed, and no window opens.

    Mutation: remove the ``token_present == "unknown"`` branch from
    ``_channel_begin_pairing`` — the answer is the switch's "Check now" sentence."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)
        conn = sqlite3.connect(harness.server.store.db_path)
        conn.execute(
            "UPDATE channels SET token_present = 'unknown' WHERE id = ?", (channel.id,)
        )
        conn.commit()
        conn.close()
        telegram.get_me_status = 502

        answer = _call(harness, "channel.beginPairing", {"id": channel.id}, 45)
        assert answer == {"ok": False, "error": _CHECK_FAILED}
        assert harness.server._channel_service.pending_pairing(channel.id) is None
        assert harness.server._channel_service.listening_channels() == []
        assert _call(harness, "channel.list", {}, 46)["channels"][0]["tokenPresent"] == "unknown"
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


def test_pair_a_phone_without_reaching_telegram_gives_a_code_and_no_link(tmp_path):
    """When ``getMe`` cannot be reached there is no handle, so there is no link.
    Nothing is recorded about the token, because a failed check says nothing about
    it. The person still gets a code to type, and the loop starts on the strength of
    the earlier check.

    Mutations: return the unreachable sentence instead of carrying on (no code
    comes back); write ``absent`` on the way (the switch refuses)."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)
        telegram.get_me_status = 502
        opened = _call(harness, "channel.beginPairing", {"id": channel.id}, 40)
        assert opened["ok"] is True and len(opened["code"]) == 7
        assert "link" not in opened
        assert _call(harness, "channel.list", {}, 41)["channels"][0]["tokenPresent"] == "present"
        assert harness.server._channel_service.listening_channels() == [channel.id]
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


def test_pair_a_phone_with_a_rejected_token_says_so_and_opens_no_window(tmp_path):
    """A rejected token is a definite answer about the credential, so it is recorded
    and refused exactly as "Check now" records and refuses it. Nothing starts and
    no window opens.

    Mutations: drop the ``absent`` write (the row still says present); carry on past
    the rejection (a code comes back)."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)
        telegram.get_me_status = 401
        answer = _call(harness, "channel.beginPairing", {"id": channel.id}, 50)
        assert answer == {"ok": False, "error": TOKEN_REJECTED}
        assert _call(harness, "channel.list", {}, 51)["channels"][0]["tokenPresent"] == "absent"
        assert harness.server._channel_service.pending_pairing(channel.id) is None
        assert harness.server._channel_service.listening_channels() == []
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)


def test_a_keychain_that_cannot_be_read_does_not_stop_pairing(tmp_path):
    """Asking for the handle is a convenience. When the keychain read itself fails,
    which a dialog nobody answered can cause, pairing goes ahead without a link
    instead of failing, and the loop's own keychain reads back off as they always
    have.

    Mutation: catch only ``ChannelError`` in step 2 — the keychain's exception
    reaches the worker and the answer is an error frame."""
    harness, telegram, provider = _turn_server(tmp_path)
    try:
        channel = _Channel(harness, telegram, provider)

        def unreadable(kind: str) -> str:
            raise RuntimeError("the keychain did not answer")

        harness.server._shell_bridge.get_channel_key = unreadable  # type: ignore[union-attr]
        frame = _any_answer(harness, "channel.beginPairing", {"id": channel.id}, 60)
        assert "result" in frame, frame
        assert frame["result"]["ok"] is True and "link" not in frame["result"]
    finally:
        harness.server._channel_service.stop_all()
        _shutdown(harness.reader, harness.thread)
