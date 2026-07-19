"""One-retry HTTP policy shared by every provider (providers/base.request_with_retry).

The retry is deliberately conservative (CLAUDE.md — this codebase is minimal):
exactly ONE retry after a short fixed pause, and only where a resend cannot
duplicate work or double-bill. These tests pin that behaviour at the real call
sites using the house ``httpx.MockTransport`` pattern, counting attempts via the
transport handler so a stray third attempt would fail loudly.

The idempotent split is the crux:
- GET (key validation / model listing) — no side effects, so retried on any
  connection/read hiccup and on a 5xx.
- POST (send a message — costs tokens/money) — retried ONLY when the failure
  proves the request never arrived (ConnectError/ConnectTimeout); a ReadTimeout
  or any received response is never resent.
"""

import httpx
import pytest

from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import Message, request_with_retry
from agent_core.providers.openai_provider import list_models


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The retry sleeps ~0.5s in production; make it instant for the suite."""
    monkeypatch.setattr("agent_core.providers.base.time.sleep", lambda *_: None)


def _scripted_client(actions):
    """An httpx.Client on a MockTransport that plays ``actions`` in order.

    Each action is either an Exception instance (raised) or an httpx.Response
    (returned). Returns (client, counter) where counter["n"] is the attempt
    count the handler observed."""
    counter = {"n": 0}
    seq = list(actions)

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        action = seq.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    return httpx.Client(transport=httpx.MockTransport(handler)), counter


_OK_MESSAGE = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}


# --- helper unit-level: the idempotent switch ------------------------------
def test_helper_idempotent_retries_read_timeout_but_post_does_not():
    # GET (idempotent): a ReadTimeout is retried.
    got_client, counter = _scripted_client(
        [httpx.ReadTimeout("slow"), httpx.Response(200, json={})]
    )
    resp = request_with_retry(lambda: got_client.get("http://x/models"), idempotent=True)
    assert resp.status_code == 200
    assert counter["n"] == 2

    # POST (non-idempotent): the SAME ReadTimeout is never resent — it may have
    # been processed server-side. Exactly one attempt, error propagates.
    post_client, post_counter = _scripted_client([httpx.ReadTimeout("slow")])
    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(lambda: post_client.post("http://x"), idempotent=False)
    assert post_counter["n"] == 1


# --- GET (openai list_models): fails once then succeeds --------------------
def test_get_retries_once_after_connect_error_then_succeeds():
    client, counter = _scripted_client(
        [
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"data": [{"id": "gpt-x"}]}),
        ]
    )
    ids = list_models("https://api.openai.com/v1", lambda: "sk-test", client=client)
    assert ids == ["gpt-x"]
    assert counter["n"] == 2  # exactly one retry


# --- GET: fails twice -> plain error, exactly two attempts (no third) ------
def test_get_fails_twice_surfaces_plain_error_and_stops_at_two():
    client, counter = _scripted_client(
        [httpx.ConnectError("refused"), httpx.ConnectError("still refused")]
    )
    with pytest.raises(RuntimeError) as exc:
        list_models("https://api.openai.com/v1", lambda: "sk-test", client=client)
    # Byte-identical to the no-retry message — the retry is invisible.
    assert str(exc.value) == "Couldn't reach that server. Check the address and that it's running."
    assert counter["n"] == 2  # no third attempt


# --- GET: a 5xx is retried once --------------------------------------------
def test_get_retries_once_on_5xx_then_succeeds():
    client, counter = _scripted_client(
        [httpx.Response(503, json={}), httpx.Response(200, json={"data": [{"id": "m"}]})]
    )
    ids = list_models("https://api.openai.com/v1", lambda: "sk-test", client=client)
    assert ids == ["m"]
    assert counter["n"] == 2


# --- POST (anthropic send): ConnectError is retried once -------------------
def test_post_retries_once_after_connect_error():
    client, counter = _scripted_client(
        [httpx.ConnectError("refused"), httpx.Response(200, json=_OK_MESSAGE)]
    )
    provider = AnthropicProvider(api_key_getter=lambda: "sk-test", client=client)
    resp = provider.send([Message(role="user", content="hi")], [])
    assert resp.text == "ok"
    assert counter["n"] == 2


# --- POST: a ReadTimeout is NOT retried (may have been processed/billed) ----
def test_post_does_not_retry_after_read_timeout():
    client, counter = _scripted_client([httpx.ReadTimeout("slow")])
    provider = AnthropicProvider(api_key_getter=lambda: "sk-test", client=client)
    with pytest.raises(RuntimeError) as exc:
        provider.send([Message(role="user", content="hi")], [])
    # Byte-identical to the existing network-error message.
    assert str(exc.value) == (
        "Couldn't reach the Anthropic service. "
        "Check your internet connection and try again."
    )
    assert counter["n"] == 1  # single attempt — never resent


# --- POST: a 5xx is NOT retried (a response WAS received) ------------------
def test_post_does_not_retry_on_5xx():
    client, counter = _scripted_client([httpx.Response(503, json={})])
    provider = AnthropicProvider(api_key_getter=lambda: "sk-test", client=client)
    with pytest.raises(RuntimeError):
        provider.send([Message(role="user", content="hi")], [])
    assert counter["n"] == 1
