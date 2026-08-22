"""Pairing — which phone may talk to Addison (messaging channels, PHASE 2).

[docs/messaging-channel-plan.md](../docs/messaging-channel-plan.md) §3.7 owns the
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

Every test here was mutation-proven; the mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from agent_core import automation_nonce
from agent_core.channel_pairing import (
    PAIRING_WINDOW_SECONDS,
    PairingOutcome,
    PendingPairing,
    begin,
    offer,
)
from tests.conftest import _shutdown, build_server

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
    then answer with a pending window and this fails."""
    harness = build_server(tmp_path)
    try:
        _profile(harness, "developer")
        _call(harness, "channel.add", {"kind": "telegram", "name": "My phone"}, 5)
        channel_id = _call(harness, "channel.list", {}, 6)["channels"][0]["id"]
        opened = _call(harness, "channel.beginPairing", {"id": channel_id}, 7)
        assert opened["ok"] is True and len(opened["code"]) == 7
        assert harness.server._channel_service.pending_pairing(channel_id) is not None
    finally:
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

    Mutation: store the pending window in a settings row — the scan finds it."""
    harness = build_server(tmp_path)
    try:
        _profile(harness, "developer")
        _call(harness, "channel.add", {"kind": "telegram", "name": "My phone"}, 5)
        channel_id = _call(harness, "channel.list", {}, 6)["channels"][0]["id"]
        code = _call(harness, "channel.beginPairing", {"id": channel_id}, 7)["code"]
        db_path = harness.server.store.db_path
    finally:
        _shutdown(harness.reader, harness.thread)
    blob = Path(db_path).read_bytes()
    assert code.encode() not in blob
    assert code.replace("-", "").encode() not in blob
