"""endpoint.proposeFromConversation / endpoint.confirmAdd — add-a-server-by-prompt
(step 4, contract F2/R2/R6/D1/D5, verification items 3, 5, 6).

House style of tests/test_ipc_snapshots.py: the real server on fake pipes, driven
by scripted provider turns. The turn reply carries NO model-authored payload — the
core reads the CURRENT turn's user text, extracts a base URL, and holds nothing;
the frontend renders a card from the propose reply and a separate confirmAdd runs
the existing provider.connect custom path.
"""

from __future__ import annotations

from agent_core.main import _PRIMARY_PROMPT_PATH
from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.base import ModelResponse
from tests.conftest import IPC_DB_NAME, _shutdown, build_server


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    frame = harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)
    return frame["result"]


def _side_store(tmp_path) -> Store:
    return Store(tmp_path / IPC_DB_NAME)


def _reasons(tmp_path) -> list[str]:
    store = _side_store(tmp_path)
    try:
        return [row["reason"] for row in store.list_config_snapshots()]
    finally:
        store.close()


def _reply(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[])


def _send(harness, text: str, request_id: int) -> None:
    """Run one real turn so a user message (and the scripted assistant reply) land
    in the server's conversation, exactly as production does before propose runs."""
    _call(harness, Method.CONVERSATION_SEND_MESSAGE, {"text": text}, request_id=request_id)


# --- propose: the happy path -------------------------------------------------


def test_propose_extracts_a_lan_url_from_a_short_add_utterance(tmp_path):
    h = build_server(tmp_path, responses=[_reply("Sure — say the word and I'll show a card.")])
    try:
        _send(h, "please add my own server at http://192.168.1.9:1234/v1", request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result == {"baseUrl": "http://192.168.1.9:1234/v1", "isLocalOrLan": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_propose_marks_a_public_server_not_lan(tmp_path):
    h = build_server(tmp_path, responses=[_reply("OK.")])
    try:
        _send(h, "connect the server at https://api.example.com/v1", request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result["baseUrl"] == "https://api.example.com/v1"
        assert result["isLocalOrLan"] is False
        assert "error" not in result
    finally:
        _shutdown(h.reader, h.thread)


# --- propose: the refusals that keep the residual bounded (R2/R6) ------------


def test_propose_returns_none_when_the_current_turn_has_no_url(tmp_path):
    # Verification item 3: no qualifying URL -> {none}, never a connect.
    h = build_server(tmp_path, responses=[_reply("Tell me the address and I'll add it.")])
    try:
        _send(h, "can you add a model server for me?", request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result == {"none": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_propose_ignores_a_url_that_is_only_in_the_assistant_turn(tmp_path):
    # R2: a model that paraphrases https://evil into its answer must NOT become the
    # extraction source. The user asked to add a server but named no address; the
    # URL lives only in the assistant reply -> {none}.
    h = build_server(
        tmp_path,
        responses=[_reply("I could add http://192.168.1.9:1234/v1 if you like.")],
    )
    try:
        _send(h, "please add a server of my own", request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result == {"none": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_propose_ignores_a_url_pasted_in_a_wall_of_text(tmp_path):
    # R6: a URL buried in a long pasted passage does not arm a card — that keeps the
    # residual bounded to the pre-existing "user types a URL" risk.
    wall = (
        "Here is something I copied from a page: " + ("blah blah context " * 20)
        + " see http://192.168.1.9:1234/v1 for the server " + ("more text " * 10)
    )
    assert len(wall) > 200
    h = build_server(tmp_path, responses=[_reply("Noted.")])
    try:
        _send(h, wall, request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result == {"none": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_propose_ignores_a_url_from_an_earlier_turn(tmp_path):
    # R2: the CURRENT turn only — not a URL pasted several turns ago.
    h = build_server(
        tmp_path,
        responses=[_reply("Say the word."), _reply("You're welcome.")],
    )
    try:
        _send(h, "add my server at http://192.168.1.9:1234/v1", request_id=1)
        _send(h, "thanks, never mind for now", request_id=2)  # current turn: no URL
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=3)
        assert result == {"none": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_propose_surfaces_a_problem_for_a_url_carrying_a_key(tmp_path):
    # A found-but-invalid URL comes back WITH its problem (so the card can say what
    # to fix), not as {none}. The key never gets stored — the connect never runs.
    h = build_server(tmp_path, responses=[_reply("OK.")])
    try:
        _send(h, "add my server https://api.example.com/v1?api_key=sk-SECRET99", request_id=1)
        result = _call(h, Method.ENDPOINT_PROPOSE_FROM_CONVERSATION, request_id=2)
        assert result["baseUrl"] == "https://api.example.com/v1?api_key=sk-SECRET99"
        assert "error" in result and "key box" in result["error"]
    finally:
        _shutdown(h.reader, h.thread)


# --- confirmAdd: runs the existing provider.connect custom path --------------


def test_confirm_add_accept_runs_the_custom_connect_and_mints_add_endpoint(tmp_path):
    # Verification item 6: a custom connect carries the add_endpoint slug.
    connected: list[tuple] = []

    def connect(provider_id, base_url):
        connected.append((provider_id, base_url))
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        result = _call(
            h,
            Method.ENDPOINT_CONFIRM_ADD,
            {"baseUrl": "http://192.168.1.9:1234/v1", "accept": True},
            request_id=1,
        )
        assert result == {"ok": True}
        assert connected == [("custom", "http://192.168.1.9:1234/v1")]
        assert "add_endpoint" in _reasons(tmp_path)
        # ...and it did NOT use the cloud slug.
        assert "provider_connect" not in _reasons(tmp_path)
    finally:
        _shutdown(h.reader, h.thread)


def test_confirm_add_decline_connects_nothing(tmp_path):
    attempts: list = []

    def connect(provider_id, base_url):
        attempts.append(base_url)
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)  # build the server
        result = _call(
            h,
            Method.ENDPOINT_CONFIRM_ADD,
            {"baseUrl": "http://192.168.1.9:1234/v1", "accept": False},
            request_id=2,
        )
        assert result["ok"] is False
        assert attempts == []
        assert "add_endpoint" not in _reasons(tmp_path)
    finally:
        _shutdown(h.reader, h.thread)


def test_confirm_add_refuses_a_key_in_the_url_before_any_snapshot(tmp_path):
    # Verification items 5 + 6: the confirm path shares the G1 connect door, so a
    # base URL carrying a key is refused BEFORE the connect is attempted and BEFORE
    # any snapshot — the secret never reaches provider_config, a payload, or the wire
    # (the key's only legitimate route is the card -> keychain, never the core).
    def connect(provider_id, base_url):
        raise AssertionError("the connect must never be attempted for a credentialled URL")

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _call(h, Method.SNAPSHOT_LIST, request_id=1)
        result = _call(
            h,
            Method.ENDPOINT_CONFIRM_ADD,
            {"baseUrl": "https://api.example.com/v1?api_key=sk-SECRET99", "accept": True},
            request_id=2,
        )
        assert result["ok"] is False and "key box" in result["error"]
        assert "add_endpoint" not in _reasons(tmp_path)
        store = _side_store(tmp_path)
        try:
            assert store.get_provider_config("custom") is None
            sidecars = [p.read_text() for p in (tmp_path / "snapshots").glob("*.json")]
        finally:
            store.close()
        for text in sidecars:
            assert "sk-SECRET99" not in text
    finally:
        _shutdown(h.reader, h.thread)


def test_primary_prompt_steers_to_the_cards_and_never_emits_json_or_a_key(tmp_path):
    # primary.txt is MITIGATION guidance, not the mechanism (the RPC + card is). It
    # must STEER the user toward the cards — and it must never teach the model to
    # emit structured JSON or carry a key, because the key's only route is the card
    # -> keychain (G1). The #43/#45 widget history: a prompt guard alone is not a
    # mechanism, so this only guards the guidance, it does not carry safety.
    prompt = _PRIMARY_PROMPT_PATH.read_text(encoding="utf-8")
    lowered = prompt.lower()
    # Steers to the endpoint card and the cost card.
    assert "a card will appear" in lowered
    assert "restore point" in lowered
    # The key goes to the keychain via the card, never through the chat.
    assert "keychain" in lowered
    assert "never ask them to type a key into the chat" in lowered
    # Never authors settings/JSON itself.
    assert "do not write any settings or json yourself" in lowered
    # And the prose itself carries no key material or JSON payload template.
    assert "sk-" not in prompt
    assert '{"' not in prompt


def test_confirm_add_carries_no_key_field_only_the_base_url(tmp_path):
    # G1 (item 5): the connect handler is handed ONLY (provider_id, base_url) — the
    # key never rides the wire; it goes card -> keychain, read later by the getter.
    seen: list[tuple] = []

    def connect(provider_id, base_url):
        seen.append((provider_id, base_url))
        return []

    h = build_server(tmp_path, register_tool=False, connect_provider=connect)
    try:
        _call(
            h,
            Method.ENDPOINT_CONFIRM_ADD,
            # Even if a stray "key" is sent in params, it is ignored — no key path.
            {"baseUrl": "http://192.168.1.9:1234/v1", "accept": True, "key": "sk-IGNORED"},
            request_id=1,
        )
        assert seen == [("custom", "http://192.168.1.9:1234/v1")]
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# The add-endpoint SHAPE gate (R6). The post-build adversarial pass found it
# matched hints as SUBSTRINGS, so "add" matched **Addison** — the app's own name —
# and "api" matched "therapist". Deleting the whole gate left the suite green, so
# the contract's stated conservatism was resting on nothing.
# ---------------------------------------------------------------------------
def test_a_sentence_that_merely_names_addison_does_not_arm_a_connect_card():
    from agent_core.rpc.providers import _extract_endpoint_url

    # Every one of these contains a hint as a SUBSTRING and nothing more.
    assert _extract_endpoint_url("Addison, what is https://evil.example/x ?") is None
    assert _extract_endpoint_url("the address is https://evil.example/x") is None
    assert _extract_endpoint_url("my therapist sent https://evil.example/x") is None
    assert _extract_endpoint_url("my appointment https://evil.example/x") is None
    assert _extract_endpoint_url("read this api doc: https://evil.example/x") is None
    assert _extract_endpoint_url("observers said https://evil.example/x") is None
    # ...and the baseline that was already right.
    assert _extract_endpoint_url("what is https://evil.example/x") is None


def test_a_genuine_add_a_server_sentence_still_arms_the_card():
    """The boundary fix must not close the door it exists to open."""
    from agent_core.rpc.providers import _extract_endpoint_url

    assert (
        _extract_endpoint_url("add my own model server at http://192.168.1.5:11434")
        == "http://192.168.1.5:11434"
    )
    assert (
        _extract_endpoint_url("connect to my Ollama on http://localhost:11434")
        == "http://localhost:11434"
    )
    assert (
        _extract_endpoint_url("can you set up http://box.local:1234/v1 as an endpoint?")
        == "http://box.local:1234/v1"
    )


def test_an_autocapitalised_scheme_is_normalised_rather_than_refused():
    """The URL regex is case-insensitive; ``_base_url_problem`` compares the scheme
    case-sensitively. A phone's "Http://…" was extracted and then refused with
    "Enter a web address that starts with http:// or https://" — a sentence that is
    false about the address the person just typed. Only the scheme is lowered; the
    host and path belong to the server."""
    from agent_core.rpc.providers import _base_url_problem, _extract_endpoint_url

    found = _extract_endpoint_url("Add my server at HTTP://Box.Local:11434/V1")
    assert found == "http://Box.Local:11434/V1"
    assert _base_url_problem(found) is None
