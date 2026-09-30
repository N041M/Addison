"""Presence left the keychain — the rule, the column, and the paths that read it.

Plan §4.1 (`docs/plans/secrets-and-keychain-plan.md`). "Is a key saved for this provider?"
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
import time

import httpx
import pytest

from agent_core.main import JsonRpcServer
from agent_core.memory import store as store_module
from agent_core.memory.store import Store
from agent_core.models_catalog import CloudModel
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

    A provider with no row at all counts as connected when PRESENT is recorded. That
    is not a new claim, it is the old "a key is in the keychain with no connection
    row" fallback ``provider.list`` used to compute by asking the OS, written down
    instead of re-asked. It is written to ``provider_observations`` and creates no
    ``provider_config`` row, because a turn must never change captured state
    (KNOWN-BUGS 94, owner decision 2026-09-30).

    Mutation: let ``record_secret_presence`` insert a ``provider_config`` row again and
    the no-row assertion fails.
    """
    store = Store(tmp_path / "p.sqlite3")
    try:
        # No row: the legacy/migrated-key shape.
        store.record_secret_presence("anthropic", SecretPresence.PRESENT)
        assert store.get_provider_config("anthropic") is None
        assert store.secret_presence("anthropic") is SecretPresence.PRESENT
        assert "anthropic" in store.connected_provider_ids()

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
    assert "secret_presence" in _EXCLUDED_COLUMNS["provider_config"]
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


# ===========================================================================
# KNOWN-BUGS 94: recording what a read proved never moves the restore walk
# ===========================================================================
class _AdvancingWallClock:
    """The store's ``time`` module, with ``time()`` five seconds later on every call.
    A fast test otherwise writes every timestamp in the same second, and a write that
    changes a captured timestamp would go unnoticed."""

    def __init__(self) -> None:
        self._now = 1_800_000_000.0

    def time(self) -> float:
        self._now += 5
        return self._now

    def __getattr__(self, name: str):
        return getattr(time, name)


def test_recording_what_a_key_read_proved_leaves_the_captured_setup_alone(
    tmp_path, monkeypatch
):
    """``secret_presence`` and ``key_rejected_at`` are observations and are excluded
    from capture. Writing either one on an existing row must leave every captured
    column as it was, or the first message after a restore changes the setup the
    restore walk compares against.

    Mutations: put ``updated_at`` back into the UPDATE in ``record_secret_presence``,
    ``record_key_rejected`` or ``clear_key_rejected``, and the matching assertion
    fails."""
    monkeypatch.setattr(store_module, "time", _AdvancingWallClock())
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config("anthropic", connected=True, added_at=1)
        captured = store.read_config_state()

        store.record_secret_presence("anthropic", SecretPresence.PRESENT)
        assert store.read_config_state() == captured, "recording presence"

        assert store.record_key_rejected("anthropic") is True
        assert store.read_config_state() == captured, "recording a rejected key"

        store.clear_key_rejected("anthropic")
        assert store.read_config_state() == captured, "clearing a rejected key"
    finally:
        store.close()


def _a_server_with_a_saved_key(tmp_path) -> JsonRpcServer:
    """A server on no pipes whose per-message key read finds a key saved, on a
    database this server creates, so its first restore point is the first-install
    one."""

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
        primary_key_probe=lambda: True,
    )
    server._ensure_built()
    return server


def _a_message(server: JsonRpcServer) -> None:
    """Do to the store what a message to the main cloud model does. The key read at
    its start is recorded first. The answered turn then marks the setup as working."""
    assert server._primary_key_status() is SecretPresence.PRESENT
    server._mark_verified_working()


def _notes(store: Store) -> list[str]:
    return [skill["name"] for skill in store.list_skills()]


def test_a_message_after_a_restore_does_not_send_the_next_press_forward(
    tmp_path, monkeypatch
):
    """The entry's repro through the server. The person connects a key, adds a note,
    breaks something, and presses the one-action restore. They send one message on
    the restored setup and press again. The second press has to go further back.

    A restore resets ``secret_presence`` to 'unknown', so the message's key read
    records it again. That write used to set the captured ``updated_at`` too. The
    walk then no longer recognised where it had landed, and the second press
    restored the broken setup.

    Mutation: put ``updated_at`` back into the UPDATE in ``record_secret_presence``
    and the second press restores the broken setup."""
    monkeypatch.setattr(store_module, "time", _AdvancingWallClock())
    server = _a_server_with_a_saved_key(tmp_path)
    store = server.store
    try:
        store.upsert_provider_config(
            "anthropic", connected=True, added_at=1, secret_presence=SecretPresence.PRESENT
        )
        _a_message(server)
        store.insert_skill(id="s-a", name="A", instructions="Be brief.", enabled=True,
                           created_at=1)
        _a_message(server)
        store.insert_skill(id="s-broken", name="Broken", instructions="Use the priciest.",
                           enabled=True, created_at=2)
        _a_message(server)

        first = server._snapshot_restore_last_working()
        assert first["ok"], first
        assert _notes(store) == ["A"]
        assert store.secret_presence("anthropic") is SecretPresence.UNKNOWN

        _a_message(server)
        second = server._snapshot_restore_last_working()

        assert "Broken" not in _notes(store)
        assert second["ok"], second
        assert _notes(store) == []
    finally:
        store.close()


def test_a_message_on_the_first_restore_point_does_not_send_the_next_press_forward(
    tmp_path, monkeypatch
):
    """The missing-row half of the entry. The first restore point of a new install
    has no Anthropic connection row, because nothing has connected yet. The person's
    key was saved without one, which is how a key from the time before several
    providers were supported still arrives. A broken change answers one message, a
    press lands on the first restore point, and one more message is sent there.

    That message's key read used to create the missing row, which is captured state.
    The walk then no longer recognised the first restore point, and the next press
    restored the broken setup. The owner decided on 2026-09-30 that a key read never
    creates a row. The answer goes to ``provider_observations``, which no restore
    point captures, and Settings still shows the key as connected.

    Mutations: let ``record_secret_presence`` insert a ``provider_config`` row again
    and the second press restores the broken setup. Drop the observation from
    ``connected_provider_ids`` and Anthropic stops showing as connected."""
    monkeypatch.setattr(store_module, "time", _AdvancingWallClock())
    server = _a_server_with_a_saved_key(tmp_path)
    store = server.store
    try:
        store.insert_skill(id="s-broken", name="Broken", instructions="Use the priciest.",
                           enabled=True, created_at=2)
        _a_message(server)

        first = server._snapshot_restore_last_working()
        assert first["ok"], first
        assert _notes(store) == []
        assert store.get_provider_config("anthropic") is None

        _a_message(server)
        second = server._snapshot_restore_last_working()

        assert "Broken" not in _notes(store)
        assert second["ok"] is False
        assert store.get_provider_config("anthropic") is None
        listed = {row["id"]: row for row in server._provider_list()["providers"]}
        assert listed["anthropic"]["connected"] is True
    finally:
        store.close()


def test_a_key_saved_without_a_row_counts_as_connected_and_nothing_else_does(tmp_path):
    """``Store.connected_provider_ids`` is the one definition of connected. A row
    answers for its provider. A provider with no row is connected only when its
    latest key read found a key saved, and never for ``custom``, whose address only
    its row holds. Removing a provider deletes its observation with its row, because
    its key is being deleted.

    Mutations: drop the ``!= 'custom'`` condition, drop the ``NOT EXISTS`` check that
    lets a row answer first, or stop ``delete_provider_config`` deleting the
    observation, and the matching assertion fails."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.record_secret_presence("anthropic", SecretPresence.PRESENT)
        store.record_secret_presence("custom", SecretPresence.PRESENT)
        store.record_secret_presence("google", SecretPresence.UNKNOWN)
        assert store.connected_provider_ids() == {"anthropic"}

        # A row answers first: a connect that failed says not connected.
        store.upsert_provider_config("openai", connected=False, last_check_ok=False)
        store.record_secret_presence("openai", SecretPresence.PRESENT)
        assert "openai" not in store.connected_provider_ids()

        store.delete_provider_config("anthropic")
        assert "anthropic" not in store.connected_provider_ids()
        assert store.secret_presence("anthropic") is SecretPresence.ABSENT
    finally:
        store.close()


def _a_connected_provider_the_restore_takes_away(server: JsonRpcServer, provider_id: str):
    """``provider_id`` connected the way ``provider.connect`` records it, then a
    restore to a setup saved before that connect. The key stays saved, because a
    restore never touches the keychain."""
    store = server.store
    before = store.read_config_state()
    store.upsert_provider_config(
        provider_id, connected=True, added_at=1, secret_presence=SecretPresence.PRESENT
    )
    store.apply_config_state(before)
    assert store.get_provider_config(provider_id) is None


def test_a_restore_that_takes_a_row_away_never_claims_fewer_connections(tmp_path):
    """The secrets plan §4.1 constraint. A restore to a setup saved before a provider
    was connected removes its row, and its key is still saved. Settings, the
    connections panel, the models kept after the restore, the reconnect at launch and
    the check for a provider other than Anthropic all have to keep treating it as
    connected, before any message has been sent.

    Mutations, each turning its own assertion red: stop ``upsert_provider_config``
    recording the observation, or point any one reader back at ``provider_config``
    alone (``_provider_list``, ``_connections``, ``_resync_providers``,
    ``_maybe_reconnect_saved_providers``, ``_other_cloud_provider_connected``)."""
    server = _a_server_with_a_saved_key(tmp_path)
    store = server.store
    try:
        _a_connected_provider_the_restore_takes_away(server, "openai")
        server._cloud_catalog = [CloudModel(id="gpt-x", label="GPT X", description="",
                                            provider="openai")]
        reconnected: list[str] = []

        def _connect(provider_id, base_url):
            reconnected.append(provider_id)
            return []

        server._connect_provider = _connect

        listed = {row["id"]: row for row in server._provider_list()["providers"]}
        assert listed["openai"]["connected"] is True, "Settings"
        assert "openai" in {c["id"] for c in server._connections([])}, "connections panel"
        server._resync_providers()
        assert [m.id for m in server._cloud_catalog] == ["gpt-x"], "models after a restore"
        assert server._other_cloud_provider_connected() is True, "another provider"
        server._providers_reconnected = False
        server._maybe_reconnect_saved_providers()
        assert reconnected == ["openai"], "reconnect at launch"
    finally:
        store.close()


def test_the_first_open_of_an_older_database_copies_its_key_answers(tmp_path):
    """A database from before ``provider_observations`` has its latest key answers
    only in ``provider_config``'s excluded columns. The first open by this build
    copies them, once, so a restore that takes a row away straight after the update
    still finds the key recorded as saved.

    Mutation: remove the copy in ``Store._apply_schema`` and the provider stops
    counting as connected once its row is gone."""
    path = tmp_path / "older.sqlite3"
    older = Store(path)
    older.upsert_provider_config(
        "anthropic", connected=True, added_at=1, secret_presence=SecretPresence.PRESENT
    )
    before = older.read_config_state()
    older._conn.execute("DROP TABLE provider_observations")
    older._conn.commit()
    older.close()

    store = Store(path)
    try:
        assert store.read_config_state() == before
        store.apply_config_state({table: [] for table in before})
        assert store.get_provider_config("anthropic") is None
        assert "anthropic" in store.connected_provider_ids()
        assert store.secret_presence("anthropic") is SecretPresence.PRESENT
    finally:
        store.close()


def test_a_rejection_cleared_by_a_connect_stays_cleared_after_a_restore(tmp_path):
    """A provider refused a key before it had a row, so the rejection lives only in
    ``provider_observations``. The person then connects a working key, which clears
    it. A later restore to a setup from before that connect takes the row away, and
    the provider must not come back flagged as refusing its key.

    Mutation: stop ``clear_key_rejected`` clearing the observation and the old
    rejection comes back."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        before = store.read_config_state()
        assert store.record_key_rejected("openai", at=1_700_000_000) is True
        store.upsert_provider_config("openai", connected=True, added_at=1)
        store.clear_key_rejected("openai")
        store.apply_config_state(before)

        assert store.get_provider_config("openai") is None
        assert store.key_rejected_at("openai") is None
    finally:
        store.close()
