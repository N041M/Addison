"""Presence left the keychain — the rule, the column, and the paths that read it.

Plan §4.1 (`docs/secrets-and-keychain-plan.md`). "Is a key saved for this provider?"
is not a secret and does not belong in the OS keychain: asking the store generated a
60-second password-dialog poll, a negative read cache, and three probe variants. The
authority is now ``provider_config.secret_presence``.

**The one property everything else is scaffolding for: ``unknown`` must never read as
"no key".** That collapse is the 2026-07-25 relay-routing bug — a dismissed macOS
password dialog read as "nothing saved", so a Simple turn was answered by an EXTERNAL
service while the person's key sat in their keychain. Every test below that mentions
UNKNOWN is defending that one sentence, from a different direction.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from agent_core.main import JsonRpcServer
from agent_core.memory.store import Store
from agent_core.providers.base import ModelRole
from agent_core.providers.router import ModelRouter
from agent_core.secret_presence import (
    SecretPresence,
    may_have_a_key,
    may_reach_setup_relay,
)
from agent_core.snapshots.scope import _CAPTURED_TABLES, _EXCLUDED_COLUMNS
from agent_core.tools.registry import ToolRegistry

from tests.conftest import _ScriptedProvider


def _row(store: Store, provider_id: str) -> dict:
    """One provider_config row, asserted to exist. A helper rather than a bare
    subscript so the type checker sees the narrowing and the failure names itself."""
    row = store.get_provider_config(provider_id)
    assert row is not None, f"no provider_config row for {provider_id}"
    return row


# ===========================================================================
# The rule itself. One function, stated over ALL THREE values — because the way
# this is got wrong is never "somebody wrote the wrong rule", it is somebody
# re-deriving it as `not present` at a call site and quietly admitting UNKNOWN.
# ===========================================================================
def test_unknown_presence_never_reads_as_no_key():
    """THE test this whole change exists to make possible.

    ``may_reach_setup_relay`` is the only place the 07-25 rule lives, and it must be
    true of ABSENT and of nothing else. Asserted across the entire vocabulary rather
    than for the interesting case alone: a rewrite as ``not present`` — the natural,
    plausible, wrong version — passes a two-value test and fails this one.

    The second rule is asserted beside it on purpose. UNKNOWN answers *yes* to "might
    there be a key?" and *no* to "may this go to the relay?", and the two answers
    pointing in opposite directions is exactly why a single boolean cannot serve.
    """
    assert may_reach_setup_relay(SecretPresence.ABSENT) is True
    assert may_reach_setup_relay(SecretPresence.UNKNOWN) is False, (
        "an unanswerable presence signal became 'no key saved' — that is the 07-25 "
        "relay bug: the turn goes to an external service while the key sits in the "
        "keychain"
    )
    assert may_reach_setup_relay(SecretPresence.PRESENT) is False

    assert may_have_a_key(SecretPresence.PRESENT) is True
    assert may_have_a_key(SecretPresence.UNKNOWN) is True
    assert may_have_a_key(SecretPresence.ABSENT) is False


def test_an_unrecognised_stored_value_widens_to_unknown_never_to_absent():
    """A hand-edited row, an older build's spelling, a value from the future.

    ``parse`` has to fail somewhere, and the direction is the whole decision: failing
    to ABSENT would turn a value Addison cannot read into a claim that there is no
    key — the same collapse as a failed keychain read, arriving through the database
    instead of the OS.
    """
    for junk in ("", "yes", "Present", None, 1, "missing"):
        assert SecretPresence.parse(junk) is SecretPresence.UNKNOWN
    # ...and the three real values still round-trip through their stored form.
    for presence in SecretPresence:
        assert SecretPresence.parse(presence.value) is presence


# ===========================================================================
# The column: schema, migration, and what each write means.
# ===========================================================================
def test_a_database_predating_the_column_migrates_to_unknown(tmp_path):
    """The migration default, and the reason it is not 'absent'.

    A row written before this column says NOTHING about whether a key is saved. The
    convenient default — "no rows means no keys" — would hand every upgrading user a
    stored "no key saved" for a provider they may well have connected, which the
    routing rule above is then asked to trust. Same idiom as ``created_in_mode``:
    ALTER TABLE with a safe default, guarded so a fresh DB is a no-op.
    """
    db = tmp_path / "old.sqlite3"
    # A pre-column provider_config, built by hand: the multi-provider shape as it
    # shipped, without secret_presence.
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE provider_config ("
        " provider_id TEXT PRIMARY KEY, connected INTEGER NOT NULL DEFAULT 0,"
        " added_at INTEGER, base_url TEXT, catalog_json TEXT, last_check_ok INTEGER,"
        " updated_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO provider_config (provider_id, connected, updated_at) VALUES (?, ?, ?)",
        ("anthropic", 1, 0),
    )
    conn.commit()
    conn.close()

    store = Store(db)
    try:
        assert store.secret_presence("anthropic") is SecretPresence.UNKNOWN
        # The rest of the row survived the migration — this is ADD COLUMN, not the
        # drop-and-recreate that _migrate_provider_config does to the pre-2026-07-18 shape.
        assert _row(store, "anthropic")["connected"] is True
    finally:
        store.close()


def test_presence_is_left_alone_by_a_write_that_did_not_learn_it(tmp_path):
    """"Did the connect ping pass?" and "is a key saved?" are learned on different
    occasions, so a caller that knows only the first must not overwrite the second.

    Without this, a routine metadata write (a base URL edit, a re-check) would reset a
    known PRESENT to the insert default and lose the fact.
    """
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config(
            "openai", connected=True, secret_presence=SecretPresence.PRESENT
        )
        store.upsert_provider_config("openai", connected=False, last_check_ok=False)
        assert store.secret_presence("openai") is SecretPresence.PRESENT
        # ...and an explicit value still overwrites.
        store.upsert_provider_config(
            "openai", connected=False, secret_presence=SecretPresence.ABSENT
        )
        assert store.secret_presence("openai") is SecretPresence.ABSENT
    finally:
        store.close()


def test_recording_presence_never_overrides_a_real_connect_result(tmp_path):
    """A presence read knows that BYTES are saved. It knows nothing about whether the
    provider accepts them, so it must not touch ``connected``.

    The one exception is a provider with no row at all, where recording PRESENT also
    marks it connected — that is not a new claim, it is the old "a key is in the
    keychain with no connection row" fallback ``provider.list`` used to compute by
    asking the OS, written down instead of re-asked.
    """
    store = Store(tmp_path / "p.sqlite3")
    try:
        # No row: the legacy/migrated-key shape.
        store.record_secret_presence("anthropic", SecretPresence.PRESENT)
        assert _row(store, "anthropic")["connected"] is True

        # An existing row that a connect attempt REJECTED keeps its answer.
        store.upsert_provider_config("openai", connected=False, last_check_ok=False)
        store.record_secret_presence("openai", SecretPresence.PRESENT)
        row = _row(store, "openai")
        assert row["connected"] is False
        assert row["secret_presence"] is SecretPresence.PRESENT

        # A provider nobody ever recorded anything for is ABSENT, not UNKNOWN: that
        # is a recorded state ("Addison has never saved a key here"), which is the
        # claim provider.list has always made by rendering it as not connected.
        assert store.secret_presence("google") is SecretPresence.ABSENT
    finally:
        store.close()


def test_a_restored_snapshot_resets_presence_rather_than_asserting_a_stale_one(tmp_path):
    """A restore must never resurrect an answer about a store the person has been
    editing since the snapshot was taken.

    ``secret_presence`` is the first entry in ``_EXCLUDED_COLUMNS``, so a restore
    resets it to the schema default. That default being 'unknown' is what makes this
    safe rather than merely tidy: the recovery path can therefore never write a "no
    key saved" that the relay rule would act on.
    """
    assert _EXCLUDED_COLUMNS["provider_config"] == ("secret_presence",)
    assert "secret_presence" not in _CAPTURED_TABLES["provider_config"]

    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config(
            "anthropic", connected=True, secret_presence=SecretPresence.ABSENT
        )
        state = store.read_config_state()
        store.upsert_provider_config(
            "anthropic", connected=True, secret_presence=SecretPresence.PRESENT
        )
        store.apply_config_state(state)
        assert store.secret_presence("anthropic") is SecretPresence.UNKNOWN, (
            "a restore asserted a snapshot-era answer about the keychain"
        )
    finally:
        store.close()


# ===========================================================================
# The paths that READ presence. None of them may touch the OS.
# ===========================================================================
def _presence_server(tmp_path, probe):
    """A server on no pipes, driven directly on this thread (the ipc_fixtures
    pattern), with ``probe`` wired as the provider-key probe."""

    def _down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = JsonRpcServer(
        reader=None,
        writer=None,
        tool_registry=ToolRegistry(),
        store_factory=lambda: Store(tmp_path / "presence.sqlite3"),
        db_path=tmp_path / "presence.sqlite3",
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider([])}),
        cloud_catalog=[],
        ollama_base_url="http://127.0.0.1:11434",
        ollama_client=httpx.Client(transport=httpx.MockTransport(_down)),
        provider_key_probe=probe,
    )
    server._ensure_built()
    return server


def test_presence_is_answered_without_touching_the_os(tmp_path):
    """Every polled or launch-driven consumer, against a probe that EXPLODES.

    ``stats.get`` is refreshed on a 60-second timer while the widget rail is open,
    and its connections loop used to ask the keychain "is a key saved?" for every
    provider without a stored row. That is a presence question, on a timer, with no
    person behind it, answered by the one call that can raise a password dialog —
    roughly ten of them stacked and unanswerable on 2026-08-01.

    COUNTED, NOT RAISED, and that is a lesson from this very change: a probe that
    raises is swallowed by the ``except Exception`` every honest presence caller wraps
    it in, so the assertion never reaches pytest and the mutation that re-adds the
    probe SURVIVES. A counter cannot be caught.

    POSITIVE CONTROL included: the recorded provider actually comes back connected,
    so this cannot pass by rendering nothing at all.
    """
    touches: list[str] = []

    server = _presence_server(tmp_path, lambda provider_id: touches.append(provider_id) or False)
    try:
        server.store.record_secret_presence("anthropic", SecretPresence.PRESENT)

        listed = {p["id"]: p for p in server._provider_list()["providers"]}
        assert listed["anthropic"]["connected"] is True
        assert listed["openai"]["connected"] is False

        conns = {c["id"]: c for c in server._connections([])}
        assert conns["anthropic"]["status"] == "reachable"
        assert "openai" not in conns

        assert server._secret_presence("anthropic") is SecretPresence.PRESENT
        assert server._secret_presence("openai") is SecretPresence.ABSENT
        # availableRoles' live-catalog gate reads the same recorded answer.
        server._maybe_load_catalogs()

        assert touches == [], (
            f"a presence question reached the OS keychain for {touches} — that is the "
            "poll-driven password dialog plan §4.1 exists to delete"
        )
    finally:
        server.store.close()


def test_a_failed_read_records_unknown_and_a_later_turn_still_refuses_to_relay(tmp_path):
    """The per-turn read is the ONE presence read with a person behind it, and it
    writes down what it learns. What it writes when the read FAILED has to be UNKNOWN.

    ABSENT here would be the 07-25 bug with a delay fuse: the dialog is dismissed
    once, "no key saved" is persisted, and every later consumer — including anything
    that ever gates routing on the stored answer — reads a fact that was never true.
    """
    server = _presence_server(tmp_path, None)
    try:
        def _unreadable() -> bool:
            raise RuntimeError("Couldn't read your saved key from the keychain.")

        server._primary_key_turn_probe = _unreadable
        assert server._primary_key_status() is SecretPresence.UNKNOWN
        assert server.store.secret_presence("anthropic") is SecretPresence.UNKNOWN
        assert may_reach_setup_relay(server.store.secret_presence("anthropic")) is False

        # A key that genuinely is not there records ABSENT — the one answer that may
        # onboard. Without this half the test above passes for a version that records
        # UNKNOWN unconditionally, which would never let anybody onboard.
        server._primary_key_turn_probe = lambda: False
        assert server._primary_key_status() is SecretPresence.ABSENT
        assert server.store.secret_presence("anthropic") is SecretPresence.ABSENT

        server._primary_key_turn_probe = lambda: True
        assert server._primary_key_status() is SecretPresence.PRESENT
        assert server.store.secret_presence("anthropic") is SecretPresence.PRESENT
    finally:
        server.store.close()


def test_a_store_that_cannot_be_written_never_fails_the_turn(tmp_path):
    """Recording presence is bookkeeping beside the answer, not the answer.

    A store that will not take the write must cost the person nothing — the read
    already succeeded, and the turn is theirs.
    """
    server = _presence_server(tmp_path, None)
    try:
        server.store.close()   # every later write raises ProgrammingError
        server._primary_key_turn_probe = lambda: True
        assert server._primary_key_status() is SecretPresence.PRESENT
    finally:
        pass


def test_no_consumer_answers_a_presence_question_with_the_key_probe():
    """A source-level backstop, in the idiom this repo already uses for C6.

    The behavioural tests above pin the paths that exist today. This pins the SHAPE:
    the two display handlers must not grow a keychain probe again, because the next
    version of this bug will not be a rewritten rule — it will be one convenient
    ``self._provider_key_probe`` added back to a loop that renders a dot.
    """
    import inspect

    from agent_core.rpc import providers as providers_module

    for name in ("_connections", "_provider_list"):
        source = inspect.getsource(getattr(providers_module.ProvidersMixin, name))
        assert "_provider_key_probe" not in source, (
            f"{name} reads the OS keychain for presence again"
        )
        assert "_presence_now" not in source, (
            f"{name} reads the OS keychain for presence again"
        )


@pytest.mark.parametrize("presence", [SecretPresence.PRESENT, SecretPresence.ABSENT])
def test_connect_records_what_it_learned_about_the_key(tmp_path, presence):
    """``provider.connect`` is the other occasion Addison legitimately learns whether
    a key is saved — the person pressed Connect seconds after the Rust command wrote
    it. Recording it there is what lets every later question be answered from SQLite.
    """
    server = _presence_server(tmp_path, lambda _pid: presence is SecretPresence.PRESENT)
    try:
        server._connect_provider = lambda provider_id, base_url: []
        assert server._provider_connect({"provider": "openai"})["ok"] is True
        assert server.store.secret_presence("openai") is presence
        assert bool(_row(server.store, "openai")["connected"]) is True
    finally:
        server.store.close()


def test_connect_records_unknown_when_the_keychain_would_not_answer(tmp_path):
    """A probe that raises is UNKNOWN, never ABSENT — plan lesson 4, at the one
    remaining OS-touching presence read outside the per-turn one."""
    def _unreadable(_provider_id):
        raise RuntimeError("Couldn't read your saved key from the keychain.")

    server = _presence_server(tmp_path, _unreadable)
    try:
        def _refuse(provider_id, base_url):
            raise RuntimeError("That key doesn't work. Check it and try again.")

        server._connect_provider = _refuse
        assert server._provider_connect({"provider": "openai"})["ok"] is False
        assert server.store.secret_presence("openai") is SecretPresence.UNKNOWN
    finally:
        server.store.close()
