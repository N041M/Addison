"""Step-3 stage 0 — the provider failure hierarchy (contract §"Stage 0").

Three named exceptions the attempt loop branches on (D4). Each MUST:
  * subclass RuntimeError, so every pre-existing ``except RuntimeError`` still
    catches it (behaviour freeze), and
  * carry the SAME plain user-facing message the provider raised before the split
    (message freeze — the type is new, the wording byte-identical).

Every raise site in all six providers is pinned here. These tests go red the
moment a raise site is reverted to a plain ``RuntimeError`` (``pytest.raises`` on
the specific subclass will not catch a bare RuntimeError), which is the mutation
kill for stage 0.
"""

from __future__ import annotations

import httpx
import pytest

from agent_core.providers import anthropic_provider
from agent_core.providers.base import (
    Message,
    ProviderAuthFailed,
    ProviderRequestRejected,
    ProviderUnavailable,
    effective_timeout,
    exception_for_http_status,
)
from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.google_provider import GoogleProvider
from agent_core.providers.ollama_provider import OllamaProvider
from agent_core.providers.openai_provider import OpenAIProvider
from agent_core.providers.setup_assistant_provider import SetupAssistantProvider


# --- the hierarchy itself ---------------------------------------------------
def test_all_three_subclass_runtimeerror():
    # The freeze property: any existing ``except RuntimeError`` still catches these.
    assert issubclass(ProviderUnavailable, RuntimeError)
    assert issubclass(ProviderRequestRejected, RuntimeError)
    assert issubclass(ProviderAuthFailed, RuntimeError)


def test_exception_for_http_status_classification():
    # 401/403 -> auth; 429 or any 5xx -> unavailable; every other 4xx -> rejected.
    assert isinstance(exception_for_http_status(401, "m"), ProviderAuthFailed)
    assert isinstance(exception_for_http_status(403, "m"), ProviderAuthFailed)
    assert isinstance(exception_for_http_status(429, "m"), ProviderUnavailable)
    assert isinstance(exception_for_http_status(500, "m"), ProviderUnavailable)
    assert isinstance(exception_for_http_status(503, "m"), ProviderUnavailable)
    assert isinstance(exception_for_http_status(400, "m"), ProviderRequestRejected)
    assert isinstance(exception_for_http_status(404, "m"), ProviderRequestRejected)
    assert isinstance(exception_for_http_status(422, "m"), ProviderRequestRejected)
    # The carried message is passed through untouched (message freeze).
    assert str(exception_for_http_status(429, "the exact words")) == "the exact words"


def test_401_is_auth_not_the_429_5xx_band():
    # Order guard: 401 must land on auth even though the second branch would also
    # not claim it — a reordering that put the 5xx test first would still miss 401,
    # but a mutation that widened the 429/5xx test to include 401 is caught here.
    assert not isinstance(exception_for_http_status(401, "m"), ProviderUnavailable)


# --- a MockTransport handler that also captures the per-request timeout ------
def _client_returning(status_code: int, payload: dict | None = None):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(status_code, json=payload or {})

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


def _client_raising(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Anthropic: every raise site, exact message freeze ----------------------
def test_anthropic_429_is_unavailable_exact_message():
    client, _ = _client_returning(429)
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable) as exc:
        provider.send([Message(role="user", content="hi")], [])
    assert str(exc.value) == (
        "Anthropic is busy right now (too many requests). Wait a moment and try again."
    )


def test_anthropic_500_is_unavailable():
    client, _ = _client_returning(500)
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_anthropic_401_is_auth_exact_message():
    client, _ = _client_returning(401)
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderAuthFailed) as exc:
        provider.send([Message(role="user", content="hi")], [])
    assert str(exc.value) == (
        "Your Anthropic API key was rejected. Check that it's entered correctly in Settings."
    )


def test_anthropic_400_is_rejected_not_unavailable():
    # The load-bearing distinction: a bad request must NOT look walkable.
    client, _ = _client_returning(400)
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderRequestRejected):
        provider.send([Message(role="user", content="hi")], [])


def test_anthropic_network_error_is_unavailable_exact_message():
    client = _client_raising(httpx.ConnectError("refused"))
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable) as exc:
        provider.send([Message(role="user", content="hi")], [])
    assert str(exc.value) == (
        "Couldn't reach the Anthropic service. Check your internet connection and try again."
    )


def test_anthropic_missing_key_is_auth_exact_message():
    provider = AnthropicProvider(api_key_getter=lambda: "")
    with pytest.raises(ProviderAuthFailed) as exc:
        provider.send([Message(role="user", content="hi")], [])
    assert str(exc.value) == (
        "No API key is set up yet. Add your Anthropic API key in Settings to start chatting."
    )


def test_anthropic_malformed_key_is_auth():
    provider = AnthropicProvider(api_key_getter=lambda: "sk-truncated…")
    with pytest.raises(ProviderAuthFailed):
        provider.send([Message(role="user", content="hi")], [])


# --- Ollama -----------------------------------------------------------------
def test_ollama_404_is_rejected():
    # /api/chat 404 == the model isn't pulled: a request problem, not "busy".
    client, _ = _client_returning(404)
    provider = OllamaProvider(model="llama3", client=client)
    with pytest.raises(ProviderRequestRejected):
        provider.send([Message(role="user", content="hi")], [])


def test_ollama_500_is_unavailable():
    client, _ = _client_returning(500)
    provider = OllamaProvider(model="llama3", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_ollama_network_error_is_unavailable_exact_message():
    client = _client_raising(httpx.ConnectError("refused"))
    provider = OllamaProvider(model="llama3", client=client)
    with pytest.raises(ProviderUnavailable) as exc:
        provider.send([Message(role="user", content="hi")], [])
    assert str(exc.value) == "Ollama isn't running on this computer. Start Ollama, then try again."


# --- OpenAI (and the OpenAI-compatible custom server) -----------------------
def test_openai_401_is_auth():
    client, _ = _client_returning(401)
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderAuthFailed):
        provider.send([Message(role="user", content="hi")], [])


def test_openai_429_is_unavailable():
    client, _ = _client_returning(429)
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_openai_missing_key_is_auth():
    provider = OpenAIProvider(model="gpt-4.1", api_key_getter=lambda: "")
    with pytest.raises(ProviderAuthFailed):
        provider.send([Message(role="user", content="hi")], [])


def test_custom_server_network_error_is_unavailable():
    # require_key=False is the keyless LAN server; a network drop is still transient.
    client = _client_raising(httpx.ConnectError("refused"))
    provider = OpenAIProvider(
        model="m", api_key_getter=lambda: "", base_url="http://localhost:1234/v1",
        client=client, require_key=False, service_label="the server",
    )
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


# --- Google -----------------------------------------------------------------
def test_google_429_is_unavailable():
    client, _ = _client_returning(429)
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "k", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_google_network_error_is_unavailable():
    client = _client_raising(httpx.ConnectError("refused"))
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "k", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_google_missing_key_is_auth():
    provider = GoogleProvider(model="gemini-2.5-pro", api_key_getter=lambda: "")
    with pytest.raises(ProviderAuthFailed):
        provider.send([Message(role="user", content="hi")], [])


# --- Setup Assistant relay --------------------------------------------------
class _StubBridge:
    def get_device_key(self):
        return {"deviceId": "dev-1", "publicKey": "pk"}

    def sign_relay_request(self, body):
        return {"signature": "sig", "deviceId": "dev-1"}


def test_relay_429_is_unavailable():
    client, _ = _client_returning(429)
    provider = SetupAssistantProvider(shell_bridge=_StubBridge(), client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


def test_relay_network_error_is_unavailable():
    client = _client_raising(httpx.ConnectError("refused"))
    provider = SetupAssistantProvider(shell_bridge=_StubBridge(), client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])


# --- [MF-A] per-call timeout override ---------------------------------------
def test_effective_timeout_only_tightens():
    # None -> the default (freeze); a value only ever tightens, never extends.
    assert effective_timeout(None, 60.0) == 60.0
    assert effective_timeout(10.0, 60.0) == 10.0      # tighter wins
    assert effective_timeout(120.0, 60.0) == 60.0     # never extends past default


def test_timeout_override_rides_on_the_request():
    client, captured = _client_returning(
        200, {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    provider.send([Message(role="user", content="hi")], [], timeout=5.0)
    # httpx expands a float timeout into a Timeout across all four channels; the
    # read channel is what the deadline is really about.
    assert captured["timeout"]["read"] == 5.0


def test_timeout_none_uses_provider_default():
    client, captured = _client_returning(
        200, {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    provider.send([Message(role="user", content="hi")], [])
    assert captured["timeout"]["read"] == anthropic_provider._TIMEOUT_SECONDS


def test_timeout_override_clamped_to_default_when_larger():
    client, captured = _client_returning(
        200, {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    )
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    # 999s is larger than the 60s default -> the request must use 60, not 999.
    provider.send([Message(role="user", content="hi")], [], timeout=999.0)
    assert captured["timeout"]["read"] == anthropic_provider._TIMEOUT_SECONDS


# --- [MF-A] the internal connect-retry must not double the fallback budget ---
# (post-build adversarial pass, 2026-07-24). A caller-supplied deadline means the
# routing attempt loop is driving, and the CHAIN is the retry mechanism: with a
# deadline, a connect failure gets exactly ONE attempt; without one (standalone
# call), today's one-retry robustness is unchanged.


def _counting_connect_fail_client():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("connect timed out")

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_a_deadline_disables_the_internal_connect_retry():
    client, calls = _counting_connect_fail_client()
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [], timeout=5.0)
    assert calls["n"] == 1, (
        "a connect failure under a per-attempt deadline must make exactly ONE "
        "attempt — the hidden second attempt doubles the per-turn budget"
    )


def test_no_deadline_keeps_todays_one_retry():
    client, calls = _counting_connect_fail_client()
    provider = AnthropicProvider(api_key_getter=lambda: "sk-x", client=client)
    with pytest.raises(ProviderUnavailable):
        provider.send([Message(role="user", content="hi")], [])
    assert calls["n"] == 2  # the standalone-call freeze: attempt + one retry
