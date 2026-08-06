"""A rejected key must change something — secrets-and-keychain plan §5.2.

Before this, a 401 changed no stored state at all: a revoked or expired key failed
EVERY turn, forever, with a per-turn error and no path forward. Owner decision
2026-08-06: ONE definitive auth failure marks the provider needs-attention, because
a 401 is unambiguous in a way a 429 or a 500 is not.

Three properties carry the whole feature, and each is one revert away from being
false:

  * only 401/403 counts. A 429 must never tell somebody their key is broken;
  * marking is a THIRD signal. It must not touch ``secret_presence`` (which decides
    whether a turn may go to the external Setup Assistant relay) and must not touch
    ``connected`` (which gates the reconnect path);
  * the person is told ONCE.

The relay half is the reason this file exists rather than three assertions bolted
onto the routing tests. Conflating "rejected" with "no key saved" would route a
person's message to an external service while their key sits in the keychain — the
2026-07-25 bug, reached by a new road.
"""

from __future__ import annotations

import httpx
import pytest

from agent_core.memory.store import Store
from agent_core.models_catalog import CloudModel
from agent_core.orchestrator import _KEY_REJECTED_NOTE, Conversation, Orchestrator
from agent_core.permissions.gate import PermissionGate
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ModelRole,
    ProviderAuthFailed,
    ProviderCapabilities,
    ProviderKeyRejected,
    ProviderRequestRejected,
    ProviderUnavailable,
    exception_for_http_status,
)
from agent_core.providers.router import ModelRouter, RoutingCandidate
from agent_core.protocol import Method
from agent_core.secret_presence import SecretPresence, may_reach_setup_relay
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ActionSnapshot
from agent_core.tools.registry import ToolRegistry
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


# --- fakes (the routing-test shapes, kept local so neither file constrains the
#     other's fixtures) ----------------------------------------------------------
class _FakeStore:
    def insert_action_snapshot(self, snapshot: ActionSnapshot) -> None:  # pragma: no cover
        pass


class _Provider:
    def __init__(self, script):
        self._script = list(script)
        self.sends = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=1000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.sends += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _answer(text="hi"):
    return ModelResponse(text=text, tool_calls=[])


def _cand(model_id, provider_id):
    return RoutingCandidate(
        model_id=model_id, role=ModelRole.PRIMARY, provider_id=provider_id,
        quality_rank=None, free=False, local=False,
    )


def _build(providers: dict, chain, *, on_auth_rejected=None, on_activity=None):
    registry = ToolRegistry()
    gate = PermissionGate()
    router = ModelRouter(
        configured={}, primary_models={c.model_id: providers[c.model_id] for c in chain},
        local_models={},
    )
    orch = Orchestrator(
        model_router=router,
        tool_registry=registry,
        permission_gate=gate,
        undo_manager=UndoManager(store=_FakeStore(), tool_registry=registry),
        on_activity=on_activity or (lambda *a, **k: None),
        routing_chain=lambda role, name: list(chain),
        model_label=lambda mid: mid.upper(),
        on_auth_rejected=on_auth_rejected or (lambda pid: False),
        provider_label=lambda pid: pid.title(),
    )
    conv = Conversation(id="c")
    conv.messages.append(Message(role="user", content="hi"))
    return orch, conv


# ===========================================================================
# 1. Which statuses count — and, far more importantly, which do not.
# ===========================================================================
def test_a_401_marks_the_provider_and_a_429_does_not():
    """The plan's own named test. 401 and 403 are DEFINITIVE — the provider answered
    and refused the key. Everything else is a bad day, not a bad key, and saying
    otherwise sends somebody to revoke and re-issue a credential that works."""
    assert isinstance(exception_for_http_status(401, "m"), ProviderKeyRejected)
    assert isinstance(exception_for_http_status(403, "m"), ProviderKeyRejected)
    # The whole of the "deliberately does NOT trigger" set that comes through here.
    for status in (429, 500, 502, 503, 400, 404, 408, 422):
        assert not isinstance(exception_for_http_status(status, "m"), ProviderKeyRejected), (
            f"status {status} would tell somebody their key was rejected"
        )
    # 429 and 5xx stay walkable-but-transient; other 4xx stay a bad request.
    assert isinstance(exception_for_http_status(429, "m"), ProviderUnavailable)
    assert isinstance(exception_for_http_status(500, "m"), ProviderUnavailable)
    assert isinstance(exception_for_http_status(400, "m"), ProviderRequestRejected)


def test_a_rejected_key_is_still_an_auth_failure_to_every_existing_caller():
    """The subclass exists so the narrower fact can be seen, not so behaviour moves
    under anything already written. Every ``except ProviderAuthFailed`` (and every
    ``except RuntimeError``) still catches it, and the provider's own plain sentence
    rides through unchanged."""
    exc = exception_for_http_status(401, "That key doesn't work. Check it and try again.")
    assert isinstance(exc, ProviderAuthFailed)
    assert isinstance(exc, RuntimeError)
    assert str(exc) == "That key doesn't work. Check it and try again."


def test_a_missing_key_is_never_a_rejected_one():
    """``ProviderAuthFailed`` is also raised locally when there is no key to send or
    the bytes are unusable. Neither is evidence about a SAVED key, and marking a
    provider needs-attention because nothing was configured would be a lie with a
    "your key may have been revoked" attached to it."""
    from agent_core.providers import anthropic_provider

    # A provider with no key getter at all, and one handed unusable bytes: both raise
    # the PLAIN type, so neither can reach §5.2's marking path.
    no_key = anthropic_provider.AnthropicProvider(api_key_getter=None)
    with pytest.raises(ProviderAuthFailed) as caught:
        no_key.send([Message(role="user", content="hi")], [])
    assert not isinstance(caught.value, ProviderKeyRejected)

    malformed = anthropic_provider.AnthropicProvider(api_key_getter=lambda: "sk-ÿ")
    with pytest.raises(ProviderAuthFailed) as caught:
        malformed.send([Message(role="user", content="hi")], [])
    assert not isinstance(caught.value, ProviderKeyRejected)


def test_a_network_failure_is_never_a_rejected_key():
    """A timeout or a dropped connection produces no status code at all, so it can
    never be definitive evidence about a key. Providers map these to
    ``ProviderUnavailable`` and this pins that they do not drift."""
    import agent_core.providers.anthropic_provider as ap

    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    )
    provider = ap.AnthropicProvider(api_key_getter=lambda: "sk-live", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [], timeout=0.01)


# ===========================================================================
# 2. THE DANGEROUS INTERACTION — a rejected key is PRESENT and rejected.
# ===========================================================================
def test_a_needs_attention_provider_is_still_present_and_never_relay_eligible(tmp_path):
    """The 2026-07-25 bug, reached by a new road, pinned shut.

    ``may_reach_setup_relay`` fires on ``absent`` and only on ``absent``. If marking a
    rejection wrote 'absent' — a perfectly reasonable-sounding "the key doesn't work,
    so treat it as no key" — the person's next message would be handed to the external
    Setup Assistant relay while their key sat in the keychain.
    """
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config(
            "anthropic", connected=True, secret_presence=SecretPresence.PRESENT
        )
        assert store.record_key_rejected("anthropic") is True

        presence = store.secret_presence("anthropic")
        assert presence is SecretPresence.PRESENT, (
            "a rejected key was recorded as 'no key saved' — the relay rule reads this"
        )
        assert may_reach_setup_relay(presence) is False, (
            "a provider with a saved-but-rejected key became relay-eligible"
        )
    finally:
        store.close()


def test_marking_a_rejection_never_disconnects_the_provider(tmp_path):
    """The second thing not to overload. ``connected`` gates the reconnect path, and
    an auth blip must not silently drop a provider out of it — the person would find
    their provider un-set-up rather than needing a new key."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config(
            "anthropic", connected=True, secret_presence=SecretPresence.PRESENT
        )
        store.record_key_rejected("anthropic")
        cfg = store.get_provider_config("anthropic")
        assert cfg is not None
        assert cfg["connected"] is True, "a 401 disconnected the provider"
        assert cfg["secret_presence"] is SecretPresence.PRESENT
        # And the third signal is the one that moved.
        assert cfg["key_rejected_at"] is not None
    finally:
        store.close()


# ===========================================================================
# 3. Told once.
# ===========================================================================
def test_repeated_rejections_never_re_notify(tmp_path):
    """A revoked key fails every turn. The store write is what decides whether a
    sentence is said, so 'told once' is enforced where the state lives rather than by
    every caller remembering to check."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config("anthropic", connected=True)
        assert store.record_key_rejected("anthropic", at=100) is True
        assert store.record_key_rejected("anthropic", at=200) is False
        assert store.record_key_rejected("anthropic", at=300) is False
        # And the first timestamp is the one kept — the moment it started failing.
        assert store.key_rejected_at("anthropic") == 100
    finally:
        store.close()


def test_a_provider_with_no_row_at_all_is_still_recorded(tmp_path):
    """Something reached that provider with a key and was refused, which is worth
    recording even where no connect ever completed. The row it creates must not
    claim a connection, and must not claim 'no key saved' either."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        assert store.record_key_rejected("openai") is True
        cfg = store.get_provider_config("openai")
        assert cfg is not None
        assert cfg["connected"] is False
        assert cfg["secret_presence"] is SecretPresence.UNKNOWN, (
            "a rejection wrote 'absent' into a fresh row — the relay reads this"
        )
        assert may_reach_setup_relay(cfg["secret_presence"]) is False
    finally:
        store.close()


# ===========================================================================
# 4. Cleared when the person adds a working key.
# ===========================================================================
def test_a_successful_connect_clears_the_rejection(tmp_path):
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config("anthropic", connected=True)
        store.record_key_rejected("anthropic")
        store.clear_key_rejected("anthropic")
        assert store.key_rejected_at("anthropic") is None
        # Cleared means cleared: the NEXT rejection is a new "first time", so somebody
        # whose replacement key is also revoked is told about that one too.
        assert store.record_key_rejected("anthropic") is True
    finally:
        store.close()


def test_a_failed_connect_leaves_the_rejection_standing(tmp_path):
    """The failing branches of ``provider.connect`` write the config row too. A
    connect that did NOT pass is no evidence that the revoked key was replaced, so
    the row must survive an ordinary upsert."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config("anthropic", connected=True)
        store.record_key_rejected("anthropic", at=100)
        store.upsert_provider_config("anthropic", connected=False, last_check_ok=False)
        assert store.key_rejected_at("anthropic") == 100, (
            "a failed connect cleared the needs-attention mark"
        )
    finally:
        store.close()


def test_a_restored_snapshot_never_resurrects_a_rejection(tmp_path):
    """The mark is an observation about the live world, not configuration. A restore
    resets it to NULL — the honest post-restore answer — rather than asserting a
    fortnight-old rejection about a key that has been replaced since, or silencing
    the notice for one that really is revoked."""
    store = Store(tmp_path / "p.sqlite3")
    try:
        store.upsert_provider_config("anthropic", connected=True)
        store.record_key_rejected("anthropic", at=100)
        state = store.read_config_state()
        store.clear_key_rejected("anthropic")
        store.apply_config_state(state)
        assert store.key_rejected_at("anthropic") is None, (
            "a restore resurrected a rejection recorded when the snapshot was taken"
        )
    finally:
        store.close()


def _connect(tmp_path, provider_id, connect_fn):
    """Drive a real ``provider.connect`` over the RPC surface, on a store that
    already carries a recorded rejection for ``provider_id``. Returns the
    ``key_rejected_at`` value the connect left behind."""
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        store.upsert_provider_config(provider_id, connected=True)
        store.record_key_rejected(provider_id, at=100)
    finally:
        store.close()

    harness = build_server(tmp_path, register_tool=False, connect_provider=connect_fn)
    try:
        harness.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.PROVIDER_CONNECT,
             "params": {"provider": provider_id}}
        )
        harness.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
    finally:
        _shutdown(harness.reader, harness.thread)

    store = Store(tmp_path / IPC_DB_NAME)
    try:
        return store.key_rejected_at(provider_id)
    finally:
        store.close()


def test_a_connect_that_passes_clears_the_mark_and_one_that_fails_does_not(tmp_path):
    """The clearing signal, at the surface that owns it. `provider.connect` passing IS
    the person adding a key the provider accepts; every other branch of that handler
    also writes the config row, and none of them is evidence of anything.

    Both halves in one test on purpose — the pair is the property. A version that
    cleared unconditionally would pass the first assertion alone.
    """
    def works(provider_id, base_url):
        return [CloudModel(id="m1", label="M1", description="", provider=provider_id)]

    def fails(provider_id, base_url):
        raise RuntimeError("That key doesn't work. Check it and try again.")

    ok_dir = tmp_path / "ok"
    bad_dir = tmp_path / "bad"
    ok_dir.mkdir()
    bad_dir.mkdir()
    assert _connect(ok_dir, "openai", works) is None
    assert _connect(bad_dir, "openai", fails) == 100, (
        "a connect that FAILED cleared the needs-attention mark"
    )


# ===========================================================================
# 5. Routing degrades — through the mechanism that already exists.
# ===========================================================================
def test_a_rejected_key_falls_forward_to_the_next_provider():
    """§5.2: routing degrades exactly as it does for an unavailable provider. The
    original 'auth never walks' rule reasoned that the next provider gets the same
    bad key — true of a MISSING key, false of a rejected one, because the next
    provider has a different key entirely."""
    a = _Provider([exception_for_http_status(401, "That key doesn't work.")])
    b = _Provider([_answer("done")])
    marks = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
        on_auth_rejected=lambda pid: marks.append(pid) or True,
    )
    orch.run_turn(conv)

    assert a.sends == 1 and b.sends == 1
    assert [m.content for m in conv.messages if m.role == "assistant"][-1] == "done"
    assert marks == ["anthropic"], "the rejected provider was not marked"
    # Cooled, like any other candidate the loop walked past — the same mechanism,
    # not a second one.
    assert orch._is_cooled("anthropic")


def test_a_missing_key_still_fails_the_turn_without_walking_or_marking():
    """The narrow subclass walks; the parent does not. This is the control that keeps
    the change from being 'auth failures now always walk'."""
    a = _Provider([ProviderAuthFailed("no key")])
    b = _Provider([_answer("should not run")])
    marks = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
        on_auth_rejected=lambda pid: marks.append(pid) or True,
    )
    with pytest.raises(ProviderAuthFailed):
        orch.run_turn(conv)
    assert b.sends == 0
    assert marks == []


def test_the_person_is_told_once_even_though_the_turn_marks_every_time():
    """The orchestrator says the sentence only when the store reports the mark was
    NEW. A second turn against the same revoked key degrades in silence."""
    notes = []
    for first_time in (True, False):
        a = _Provider([exception_for_http_status(401, "no")])
        b = _Provider([_answer("done")])
        orch, conv = _build(
            {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
            on_auth_rejected=lambda pid: first_time,
            on_activity=lambda tid, label, detail=None: notes.append((tid, label, first_time)),
        )
        orch.run_turn(conv)

    routing = [n for n in notes if n[0] == "routing"]
    assert len(routing) == 1, "a repeated rejection said it again"
    assert routing[0][1] == "Anthropic rejected Addison's key — it may have been revoked. " \
                            "Add a new one in Settings."


def test_the_rejection_sentence_replaces_the_busy_one_for_the_head():
    """"Anthropic was busy" is a plain falsehood about a revoked key, and it is the
    second thing the person would read. The rejection line already explains the
    substitution, and explains it truthfully."""
    a = _Provider([exception_for_http_status(401, "no")])
    b = _Provider([_answer("done")])
    notes = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
        on_auth_rejected=lambda pid: True,
        on_activity=lambda tid, label, detail=None: notes.append((tid, label)),
    )
    orch.run_turn(conv)

    routing = [label for tid, label in notes if tid == "routing"]
    assert not any("was busy" in label for label in routing), (
        "a revoked key was reported to the person as a busy provider"
    )
    assert len(routing) == 1


def test_an_unavailable_head_still_says_it_was_busy():
    """The control for the test above: suppressing the busy note must be conditional
    on a REJECTION, not on falling forward at all. Without this, deleting the note
    outright would pass."""
    a = _Provider([ProviderUnavailable("A busy")])
    b = _Provider([_answer("done")])
    notes = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
        on_activity=lambda tid, label, detail=None: notes.append((tid, label)),
    )
    orch.run_turn(conv)
    assert [label for tid, label in notes if tid == "routing"] == [
        "A was busy, so Addison used B."
    ]


def test_an_unwired_orchestrator_marks_and_says_nothing():
    """CLI and tests construct the orchestrator with no callbacks at all. The default
    must be silent — never a notice about a provider nothing recorded."""
    a = _Provider([exception_for_http_status(401, "no")])
    b = _Provider([_answer("done")])
    notes = []
    orch, conv = _build(
        {"a": a, "b": b}, [_cand("a", "anthropic"), _cand("b", "openai")],
        on_activity=lambda tid, label, detail=None: notes.append((tid, label)),
    )
    orch.run_turn(conv)
    assert [n for n in notes if n[0] == "routing"] == []


# ===========================================================================
# 6. The copy.
# ===========================================================================
def test_the_sentence_is_plain_and_names_one_next_step():
    """CONVENTIONS: plain language, no jargon, no stack traces — a plain message plus
    one suggested next step, for personas 54 and 68."""
    said = _KEY_REJECTED_NOTE.format(provider="Claude")
    assert said == (
        "Claude rejected Addison's key — it may have been revoked. "
        "Add a new one in Settings."
    )
    for jargon in ("401", "403", "HTTP", "status", "auth", "token", "Traceback"):
        assert jargon not in said
    assert "Settings" in said, "the sentence has to name where to fix it"
