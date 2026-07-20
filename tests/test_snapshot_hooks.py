"""The auto-snapshot hook sites — GLOBAL FLOOR G3 (contract §8, §10).

The floor is only as good as the moments it fires at. These tests pin the seven
hook sites the handlers carry (H1 profile switch, H2/H3 provider connect and
disconnect, H4 routine delete, H5 widget delete, H6 skill delete, H7 skill
update) plus the one verified-working marking (H8), and — the part that is easy
to get wrong — the CAPTURE-FAILURE POLICY, which is deliberately not uniform:

- a change whose old content exists nowhere else (a delete, an in-place note
  overwrite) REFUSES when the restore point could not be saved, because
  performing an unbackable delete is the one outcome the floor must not allow;
- a change the person can simply redo (a profile switch, a provider connect or
  disconnect) PROCEEDS, with the sticky warning on snapshot.list, because
  blocking a profile switch on a disk hiccup is the worse failure.

And, over both: no hook may ever raise into its handler. A snapshot problem must
never turn a legitimate config change into a stack trace.

House style of tests/test_ipc_snapshots.py: the real server on fake pipes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agent_core.memory.store import Store
from agent_core.protocol import Method
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    frame = harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return frame["result"]


def _frame(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """The whole frame, so a test can assert on the error half too."""
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id)


def _side_store(tmp_path) -> Store:
    """A second connection to the same file, owned by the test thread (the
    server's own Store belongs to its worker thread and sqlite3 refuses
    cross-thread use). Same device as tests/test_ipc_snapshots.py."""
    return Store(tmp_path / IPC_DB_NAME)


def _reasons(store: Store) -> list[str]:
    return [row["reason"] for row in store.list_config_snapshots()]


def _payload_of(tmp_path: Path, snapshot_id: str) -> dict:
    """The sidecar payload for a snapshot, read straight off disk — the cheapest
    way to see WHAT a hook captured rather than merely that it fired."""
    return json.loads((tmp_path / "snapshots" / f"{snapshot_id}.json").read_text())


def _newest_with_reason(store: Store, reason: str) -> dict:
    rows = [row for row in store.list_config_snapshots() if row["reason"] == reason]
    assert rows, f"no snapshot was taken with reason {reason!r}"
    return rows[0]  # list_config_snapshots is newest-first


class _FailingManager:
    """The real manager with its two write paths broken.

    A full disk or a read-only data directory is exactly the condition the
    capture-failure policy exists for, so it has to be expressible here. Every
    other verb delegates, because the tests still need snapshot.list to answer
    (that is where the sticky warning surfaces)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.capture_reasons: list[object] = []
        self.verified_calls = 0

    def capture(self, **kwargs):
        self.capture_reasons.append(kwargs.get("reason"))
        raise OSError("No space left on device")

    def mark_verified_working(self):
        self.verified_calls += 1
        raise OSError("No space left on device")

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _RecordingManager:
    """Counts the hook traffic without changing any of it."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.capture_reasons: list[object] = []
        self.verified_calls = 0

    def capture(self, **kwargs):
        self.capture_reasons.append(kwargs.get("reason"))
        return self._inner.capture(**kwargs)

    def mark_verified_working(self):
        self.verified_calls += 1
        return self._inner.mark_verified_working()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _wrap_manager(harness, wrapper):
    """Force the lazy build, then swap the manager the hooks reach.

    snapshot.list is the cheapest request that builds the server, and it leaves
    genesis (and nothing else) behind."""
    _call(harness, Method.SNAPSHOT_LIST, request_id=900)
    wrapped = wrapper(harness.server._snapshot_manager)
    harness.server._snapshot_manager = wrapped
    return wrapped


# --- H1: profile switch ------------------------------------------------------


def test_profile_set_snapshots_before_switching(tmp_path):
    # The restore point has to hold the config the person is leaving, not the one
    # they are arriving at — a snapshot of the new state is no way back at all.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.PROFILE_SET, {"profileId": "developer"}, request_id=1)
        _call(h, Method.PROFILE_SET, {"profileId": "simple"}, request_id=2)
        store = _side_store(tmp_path)
        assert _reasons(store).count("mode_switch") == 2
        newest = _newest_with_reason(store, "mode_switch")
        settings = _payload_of(tmp_path, newest["id"])["tables"]["app_settings"]
        active = {row["key"]: row["value"] for row in settings}.get("active_profile")
        assert active == "developer"  # the profile being left, captured before the write
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_unknown_profile_id_takes_no_snapshot(tmp_path):
    # The ValueError guard runs first: a refused switch changes nothing, so it
    # must not churn a restore point either.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        frame = _frame(h, Method.PROFILE_SET, {"profileId": "wizard"}, request_id=2)
        assert "error" in frame
        store = _side_store(tmp_path)
        assert "mode_switch" not in _reasons(store)
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


# --- H2 / H3: providers ------------------------------------------------------


def test_provider_connect_snapshots_once_per_attempt(tmp_path):
    # Both branches of connect write provider_config, so one snapshot before the
    # attempt covers both — and a failed attempt does not double up.
    attempts: list[str] = []

    def connect(provider_id, base_url):
        attempts.append(provider_id)
        if len(attempts) == 1:
            raise RuntimeError("Couldn't reach that service.")
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        failed = _call(h, Method.PROVIDER_CONNECT, {"provider": "anthropic"}, request_id=1)
        assert failed["ok"] is False
        ok = _call(h, Method.PROVIDER_CONNECT, {"provider": "anthropic"}, request_id=2)
        assert ok == {"ok": True}
        store = _side_store(tmp_path)
        assert _reasons(store).count("provider_connect") == 2
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_provider_connect_refused_before_validation_takes_no_snapshot(tmp_path):
    # An unknown provider id is refused before anything is written.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        result = _call(h, Method.PROVIDER_CONNECT, {"provider": "nonesuch"}, request_id=2)
        assert result["ok"] is False
        store = _side_store(tmp_path)
        assert "provider_connect" not in _reasons(store)
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_provider_disconnect_snapshots_only_when_a_config_exists(tmp_path):
    # Disconnecting something that was never connected is a no-op, and a no-op
    # has nothing to roll back to.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        assert _call(h, Method.PROVIDER_DISCONNECT, {"provider": "openai"}, request_id=2) == {
            "ok": True
        }
        store = _side_store(tmp_path)
        assert "provider_disconnect" not in _reasons(store)

        store.upsert_provider_config("openai", connected=True, added_at=5)
        assert _call(h, Method.PROVIDER_DISCONNECT, {"provider": "openai"}, request_id=3) == {
            "ok": True
        }
        assert _reasons(store).count("provider_disconnect") == 1
        assert store.get_provider_config("openai") is None
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_connect_refuses_a_base_url_carrying_a_key_so_no_snapshot_can_hold_it(tmp_path):
    """GLOBAL FLOOR G1, at the one door that can breach it.

    provider_config.base_url is captured by every snapshot, so a key embedded in
    a base URL would be written in plain text into config_snapshots.state_blob,
    into the sidecar file, and into any permanent anchor — permanently, by the
    machinery that is supposed to be the safety net. The refusal lives at the
    point of ACCEPTANCE rather than on capture, because redacting on capture
    would make restore write back an address the person never configured.

    Both smuggling routes are covered, and the assertion is on the FLOOR (no
    secret anywhere in any payload), not merely on the error string."""
    attempts: list[str] = []

    def connect(provider_id, base_url):
        attempts.append(provider_id)
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        userinfo = _call(
            h,
            Method.PROVIDER_CONNECT,
            {"provider": "custom", "baseUrl": "https://user:sk-live-SECRET123@api.test/v1"},
            request_id=2,
        )
        query = _call(
            h,
            Method.PROVIDER_CONNECT,
            {"provider": "custom", "baseUrl": "https://api.test/v1?api_key=sk-QUERY456"},
            request_id=3,
        )
        for result in (userinfo, query):
            assert result["ok"] is False
            # Plain language, and it points at the key box rather than scolding.
            assert "key box" in result["error"]
        assert attempts == []  # never even attempted the connect

        store = _side_store(tmp_path)
        # Nothing was stored, so nothing could be captured...
        assert store.get_provider_config("custom") is None
        assert "provider_connect" not in _reasons(store)
        # ...and no payload on disk or in the row holds either secret.
        rows = [store.get_config_snapshot(row["id"]) for row in store.list_config_snapshots()]
        blobs = [row.state_blob for row in rows if row is not None]
        sidecars = [path.read_text() for path in (tmp_path / "snapshots").glob("*.json")]
        for text in blobs + sidecars:
            assert "sk-live-SECRET123" not in text
            assert "sk-QUERY456" not in text
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


# Every address the G1 sweep fires at the connect door, each with the shape of
# the smuggling route it stands for. The two-letter query names are the ones that
# beat the previous credential-NAME blocklist — they are why the check is now
# structural (no query string or fragment is accepted at all, whatever it holds).
_SECRET = "sk-live-SECRET123"
_CREDENTIALLED_ADDRESSES = [
    ("userinfo", f"https://user:{_SECRET}@api.example.com/v1"),
    ("userinfo-no-password", f"https://{_SECRET}@api.example.com/v1"),
    ("query api_key", f"https://api.example.com/v1?api_key={_SECRET}"),
    ("query apikey", f"https://api.example.com/v1?apikey={_SECRET}"),
    ("query APIKEY uppercase", f"https://api.example.com/v1?APIKEY={_SECRET}"),
    ("query access_token", f"https://api.example.com/v1?access_token={_SECRET}"),
    ("query X-Api-Key", f"https://api.example.com/v1?X-Api-Key={_SECRET}"),
    ("query secret", f"https://api.example.com/v1?secret={_SECRET}"),
    ("query auth", f"https://api.example.com/v1?auth={_SECRET}"),
    ("query sig", f"https://api.example.com/v1?sig={_SECRET}"),
    ("query sk (two letters)", f"https://api.example.com/v1?sk={_SECRET}"),
    ("query t (one letter)", f"https://api.example.com/v1?t={_SECRET}"),
    ("path segment", f"https://api.example.com/v1/{_SECRET}"),
    ("fragment", f"https://api.example.com/v1#{_SECRET}"),
    # Short on purpose. The decoded form ("sk-live-a") is caught by the "sk-"
    # prefix and nothing else: it is under the length bar, so the entropy rule
    # can't catch it. A longer vector here would pass even with the percent
    # decoding deleted, and the case would prove nothing about unquote().
    ("percent-encoded path segment", "https://api.example.com/v1/%73k-live-a"),
    ("long high-entropy path segment", "https://api.example.com/v1/AIzaSyD7q2XbN4mZ8rT1kW9pL0"),
    # Too short for the entropy rule to see, and caught only by the known key
    # openings. Without this case, deleting that branch passes every other test.
    ("short key-prefixed path segment", "https://api.example.com/v1/ghp_A1b2"),
]


def test_no_credentialled_address_survives_the_connect_door(tmp_path):
    """GLOBAL FLOOR G1, swept.

    Every one of these addresses would, if accepted, be written to
    ``provider_config.base_url`` — which every G3 snapshot copies into
    ``config_snapshots.state_blob`` (plain text in SQLite) AND into the plain-text
    sidecar file, and which a permanent anchor would then keep forever. So the
    assertion is on the FLOOR, not on any error wording: the connect is never
    attempted, nothing is stored, and the secret appears in no payload anywhere.

    The last four entries are the ones a NAME blocklist cannot reach — a
    one-letter query parameter, a fragment, a disguised path segment — which is
    why the check is structural."""
    attempts: list[str] = []

    def connect(provider_id, base_url):
        attempts.append(base_url)
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        for index, (label, address) in enumerate(_CREDENTIALLED_ADDRESSES, start=2):
            result = _call(
                h,
                Method.PROVIDER_CONNECT,
                {"provider": "custom", "baseUrl": address},
                request_id=index,
            )
            assert result["ok"] is False, label
            # Plain language, and every variant points at the way out rather than
            # scolding — the key box exists and it goes to the keychain.
            assert "key box" in result["error"], label
        assert attempts == []

        store = _side_store(tmp_path)
        assert store.get_provider_config("custom") is None
        assert "provider_connect" not in _reasons(store)
        rows = [store.get_config_snapshot(row["id"]) for row in store.list_config_snapshots()]
        payloads = [row.state_blob for row in rows if row is not None]
        payloads += [path.read_text() for path in (tmp_path / "snapshots").glob("*.json")]
        for text in payloads:
            assert "SECRET123" not in text
            assert "AIzaSyD7q2XbN4mZ8rT1kW9pL0" not in text
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_the_credential_check_guards_every_provider_not_only_custom(tmp_path):
    """A base URL reaches ``provider_config`` whatever the provider id is called,
    so the check cannot be spent on ``custom`` alone — otherwise the same leak is
    one word away, via {"provider": "anthropic", "baseUrl": ...}."""

    def connect(provider_id, base_url):
        raise AssertionError("the connect must never be attempted")

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        for index, provider_id in enumerate(("anthropic", "openai", "google"), start=1):
            result = _call(
                h,
                Method.PROVIDER_CONNECT,
                {"provider": provider_id, "baseUrl": f"https://api.example.com/v1?sk={_SECRET}"},
                request_id=index,
            )
            assert result["ok"] is False, provider_id
            assert "key box" in result["error"], provider_id
    finally:
        _shutdown(h.reader, h.thread)


def test_ordinary_server_addresses_are_still_accepted(tmp_path):
    """The other half of the bargain. Refusing is only free if it refuses the
    right things — a check that turned people away from their own working server
    would push them onto a worse path, so the shapes real endpoints use (a version
    segment, a dated preview segment, a hyphenated route, a deployment name) all
    have to keep working, including the ONE permitted plain http:// case."""
    from agent_core.rpc.providers import _base_url_problem

    for address in (
        "http://localhost:11434/v1",
        "http://192.168.1.9:1234/v1",
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://my-server.example.com/openai/v1/chat-completions",
        "https://example.com/2024-05-01-preview/v1",
        "https://host/v1/deployments/gpt-4o-mini",
    ):
        assert _base_url_problem(address) is None, address


def test_connect_still_accepts_a_plain_local_server_address(tmp_path):
    """The credential check must not cost the ONE permitted http:// case — a
    custom server on this computer or the local network."""
    seen: list[str | None] = []

    def connect(provider_id, base_url):
        seen.append(base_url)
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        result = _call(
            h,
            Method.PROVIDER_CONNECT,
            {"provider": "custom", "baseUrl": "http://192.168.1.9:1234/v1"},
            request_id=1,
        )
        assert result == {"ok": True}
        assert seen == ["http://192.168.1.9:1234/v1"]
    finally:
        _shutdown(h.reader, h.thread)


# --- H4: routine delete (owned by WS-C in main.py; pinned here with its peers) --


def test_routine_delete_snapshots_before_the_cascade(tmp_path):
    # Deleting a routine cascades to its run history, so the snapshot has to hold
    # both — after the statement they exist nowhere else.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.insert_routine(
            id="r-1",
            name="Tidy up",
            description="A saved plan.",
            plan_json={"steps": []},
            created_from_conversation_id=None,
            created_at=int(time.time()),
        )
        store.insert_routine_run(id="run-1", routine_id="r-1", started_at=int(time.time()))

        assert _call(h, Method.ROUTINE_DELETE, {"routineId": "r-1"}, request_id=2) == {"ok": True}
        assert store.get_routine("r-1") is None
        captured = _payload_of(tmp_path, _newest_with_reason(store, "routine_delete")["id"])
        assert [row["id"] for row in captured["tables"]["routines"]] == ["r-1"]
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


# --- H5 / H6 / H7: the irrecoverable trio ------------------------------------


def test_widget_delete_snapshots_only_when_the_widget_exists(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        # Deleting an id that isn't here stays idempotent AND mints nothing.
        assert _call(h, Method.WIDGET_DELETE, {"id": "ghost"}, request_id=2) == {"ok": True}
        assert "widget_delete" not in _reasons(store)

        store.insert_widget(
            id="w-1",
            spec_json=json.dumps({"kind": "stat", "source": "connections", "title": "Links"}),
            pinned=False,
            position=0,
            created_at=int(time.time()),
            created_in_mode="safe",
        )
        assert _call(h, Method.WIDGET_DELETE, {"id": "w-1"}, request_id=3) == {"ok": True}
        assert store.get_widget("w-1") is None
        assert _reasons(store).count("widget_delete") == 1
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_delete_snapshots_only_when_the_skill_exists(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        assert _call(h, Method.SKILL_DELETE, {"id": "ghost"}, request_id=2) == {"ok": True}
        assert "skill_delete" not in _reasons(store)

        store.insert_skill(
            id="s-1", name="Be brief", instructions="Short answers.", enabled=True, created_at=7
        )
        assert _call(h, Method.SKILL_DELETE, {"id": "s-1"}, request_id=3) == {"ok": True}
        assert store.get_skill("s-1") is None
        captured = _payload_of(tmp_path, _newest_with_reason(store, "skill_delete")["id"])
        assert [row["instructions"] for row in captured["tables"]["skills"]] == ["Short answers."]
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_update_snapshots_after_validation(tmp_path):
    # A rejected edit changes nothing, so it must not mint a restore point; an
    # accepted one overwrites the only copy of the old text, so it must.
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        store = _side_store(tmp_path)
        store.insert_skill(
            id="s-1", name="Be brief", instructions="Short answers.", enabled=True, created_at=7
        )
        rejected = _call(
            h, Method.SKILL_UPDATE, {"id": "s-1", "name": "  ", "instructions": ""}, request_id=2
        )
        assert rejected["ok"] is False
        assert "skill_update" not in _reasons(store)

        accepted = _call(
            h,
            Method.SKILL_UPDATE,
            {"id": "s-1", "name": "Be brief", "instructions": "Even shorter answers."},
            request_id=3,
        )
        assert accepted == {"ok": True}
        captured = _payload_of(tmp_path, _newest_with_reason(store, "skill_update")["id"])
        assert [row["instructions"] for row in captured["tables"]["skills"]] == ["Short answers."]
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_update_of_an_unknown_id_takes_no_snapshot(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        result = _call(
            h, Method.SKILL_UPDATE, {"id": "ghost", "name": "N", "instructions": "I"}, request_id=2
        )
        assert result == {"ok": False, "error": "That skill isn't here any more."}
        store = _side_store(tmp_path)
        assert "skill_update" not in _reasons(store)
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


# --- H8: verified-working marking --------------------------------------------


def test_successful_turn_marks_verified_working(tmp_path):
    # A config that just answered a message end to end is provably working, and
    # that is what restore_last_working() walks back to.
    from agent_core.providers.base import ModelResponse

    h = build_server(tmp_path, responses=[ModelResponse(text="Done.", tool_calls=[])])
    try:
        recorder = _wrap_manager(h, _RecordingManager)
        result = _call(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hello"}, request_id=1)
        assert result["ok"] is True
        assert recorder.verified_calls == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_failed_turn_does_not_mark_verified_working(tmp_path):
    # H8 sits after the try/except/finally, not inside the finally — a config
    # that just threw is the last thing that should be recorded as working.
    h = build_server(tmp_path, responses=[])  # the scripted provider runs dry and raises
    try:
        recorder = _wrap_manager(h, _RecordingManager)
        frame = _frame(h, Method.CONVERSATION_SEND_MESSAGE, {"text": "hello"}, request_id=1)
        assert "error" in frame
        assert recorder.verified_calls == 0
    finally:
        _shutdown(h.reader, h.thread)


def test_refused_turn_does_not_mark_verified_working(tmp_path):
    # An early refusal is neither a success nor a failure: nothing was sent, so
    # the config proved nothing.
    h = build_server(tmp_path, register_tool=False)
    try:
        recorder = _wrap_manager(h, _RecordingManager)
        frame = _frame(
            h,
            Method.CONVERSATION_SEND_MESSAGE,
            {"text": "hello", "role": "local", "modelId": "no-such-model"},
            request_id=1,
        )
        assert "error" in frame
        assert recorder.verified_calls == 0
    finally:
        _shutdown(h.reader, h.thread)


# --- the capture-failure policy ----------------------------------------------


def test_a_snapshot_failure_never_raises_into_its_handler(tmp_path):
    # The rule over every hook: a snapshot problem is never a stack trace. Each
    # handler answers plainly, and the server is still alive at the end.
    from agent_core.providers.base import ModelResponse

    h = build_server(tmp_path, responses=[ModelResponse(text="Done.", tool_calls=[])])
    try:
        failing = _wrap_manager(h, _FailingManager)
        store = _side_store(tmp_path)
        store.insert_skill(
            id="s-1", name="Be brief", instructions="Short answers.", enabled=True, created_at=7
        )
        store.insert_widget(
            id="w-1",
            spec_json=json.dumps({"kind": "stat", "source": "connections", "title": "Links"}),
            pinned=False,
            position=0,
            created_at=int(time.time()),
            created_in_mode="safe",
        )
        store.upsert_provider_config("openai", connected=True, added_at=5)

        calls = [
            (Method.PROFILE_SET, {"profileId": "developer"}),
            (Method.PROVIDER_DISCONNECT, {"provider": "openai"}),
            (Method.WIDGET_DELETE, {"id": "w-1"}),
            (Method.SKILL_DELETE, {"id": "s-1"}),
            (Method.SKILL_UPDATE, {"id": "s-1", "name": "Be brief", "instructions": "Shorter."}),
            (Method.CONVERSATION_SEND_MESSAGE, {"text": "hello"}),
        ]
        for index, (method, params) in enumerate(calls, start=10):
            frame = _frame(h, method, params, request_id=index)
            assert "result" in frame, f"{method} produced an error frame: {frame}"
        assert failing.capture_reasons  # the hooks really did try
        assert failing.verified_calls == 1  # and H8's failure was swallowed too
        store.close()
    finally:
        _shutdown(h.reader, h.thread)


def test_a_failed_snapshot_lets_a_recoverable_change_proceed(tmp_path):
    # H1/H2/H3: the person can redo a profile switch or a reconnect, so blocking
    # one on a disk hiccup would be the worse failure. It proceeds — and says so.
    def connect(provider_id, base_url):
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _wrap_manager(h, _FailingManager)
        assert _call(h, Method.PROFILE_SET, {"profileId": "developer"}, request_id=1)["ok"] is True
        assert _call(h, Method.PROVIDER_CONNECT, {"provider": "openai"}, request_id=2) == {
            "ok": True
        }
        store = _side_store(tmp_path)
        assert store.get_setting("active_profile") == "developer"  # the change landed
        assert store.get_provider_config("openai") is not None
        store.close()

        listed = _call(h, Method.SNAPSHOT_LIST, request_id=3)
        assert listed["warning"] == (
            "Addison couldn't save a restore point just now. Your older "
            "restore points are still there."
        )
    finally:
        _shutdown(h.reader, h.thread)


def test_a_failed_snapshot_refuses_an_irrecoverable_change(tmp_path):
    # H5/H6/H7: the old content exists nowhere else, so proceeding without a
    # restore point is the one outcome the floor must not allow. Refusing a
    # delete is recoverable; an unbackable delete is not.
    h = build_server(tmp_path, register_tool=False)
    try:
        _wrap_manager(h, _FailingManager)
        store = _side_store(tmp_path)
        store.insert_skill(
            id="s-1", name="Be brief", instructions="Short answers.", enabled=True, created_at=7
        )
        store.insert_widget(
            id="w-1",
            spec_json=json.dumps({"kind": "stat", "source": "connections", "title": "Links"}),
            pinned=False,
            position=0,
            created_at=int(time.time()),
            created_in_mode="safe",
        )

        deleted_widget = _call(h, Method.WIDGET_DELETE, {"id": "w-1"}, request_id=1)
        assert deleted_widget == {
            "ok": False,
            "error": "Addison couldn't save a restore point just now, so it "
            "didn't delete anything. Try again in a moment.",
        }
        deleted_skill = _call(h, Method.SKILL_DELETE, {"id": "s-1"}, request_id=2)
        assert deleted_skill == {
            "ok": False,
            "error": "Addison couldn't save a restore point just now, so it "
            "didn't delete anything. Try again in a moment.",
        }
        updated_skill = _call(
            h,
            Method.SKILL_UPDATE,
            {"id": "s-1", "name": "Be brief", "instructions": "Rewritten."},
            request_id=3,
        )
        assert updated_skill == {
            "ok": False,
            "error": "Addison couldn't save a restore point just now, so it "
            "didn't change the note. Try again in a moment.",
        }

        # Nothing was lost: every piece of content is exactly as it was.
        assert store.get_widget("w-1") is not None
        skill = store.get_skill("s-1")
        assert skill is not None and skill["instructions"] == "Short answers."
        store.close()
    finally:
        _shutdown(h.reader, h.thread)
