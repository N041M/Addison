"""SetupAssistantProvider — onboarding-only free relay (design-doc §7.5.1, §4.6).

Fills the SETUP_ASSISTANT role ONLY. Calls the external serverless relay; never
holds a real key locally (relay keys live server-side, out of this repo's trust
boundary entirely — §8.4). There is deliberately NO ``api_key_getter`` here and
no provider-key access of any kind: the only credential involved is the device
identity, and even that never enters this process — the shell signs each request
with the device private key, which never leaves the OS keychain (§5).

Degrades to a prompt-based tool-call parser if the underlying free model lacks
native function-calling (``native_tool_calling=False``): the system prompt asks
the model to emit a single fenced JSON block ``{"tool": ..., "args": {...}}``
when it wants a tool, and ``send()`` scans the reply for that block. Parsing is
``json.loads`` only — never ``eval`` (§8.1) — and malformed JSON degrades to
plain text rather than raising.

Request/response contract with the relay (this module defines it; the Rust
signer and the real relay follow it):

  POST <relay_url>
    headers: x-addison-device: <deviceId>, x-addison-signature: <signature>
    body:    {"deviceId", "messages", "tools", "system"?}
             messages/tools use the same shapes as AnthropicProvider's history
             translation; "system" carries the leading role="system" message.
  response (200):
    normal : {"text": "<assistant text, maybe with a fenced tool block>"}
    at-cap : {"at_cap": true, "text": "<plain wrap-up, offers a key>"}

Built after the core loop works end-to-end (engineering-spec §11 step 9).
"""

from __future__ import annotations

import httpx

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
    request_with_retry,
)
from agent_core.providers.tool_call_parser import parse_tool_call

# Documented placeholder — the real relay URL is supplied via ADDISON_RELAY_URL
# (main.py reads the env); this default only keeps dev/tests from needing it set.
DEFAULT_RELAY_URL = "https://relay.addison.example/v1/chat"

_TIMEOUT_SECONDS = 60.0

# Plain-language fallback if the relay flags at-cap without its own wrap-up text.
_DEFAULT_AT_CAP_MESSAGE = (
    "We've reached the end of the free setup conversation. Add your own API key in "
    "Settings and we can keep going with full speed and capabilities."
)


class SetupAssistantProvider:
    def __init__(self, shell_bridge, relay_url: str = DEFAULT_RELAY_URL, client=None) -> None:
        # The shell bridge signs each request and reports the (public) device id.
        # NOTE: intentionally no api_key_getter / key field on this instance.
        self._bridge = shell_bridge
        self._relay_url = relay_url
        # Optional injected httpx.Client (tests pass one on a MockTransport).
        # When None, send() creates and closes a client per request.
        self._client = client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=False,   # small free models; prompt-based parsing fallback
            max_context_tokens=8_192,
            supports_streaming=True,
            runs_off_device=False,
        )

    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
    ) -> ModelResponse:
        # ``effort`` is a PRIMARY cloud "answer style" (§4.1.1); the onboarding relay
        # has no such control, so it is accepted and ignored for a uniform call.
        # Device id first (the body carries it), then sign the assembled body.
        device_id = self._device_id()
        body: dict = {
            "deviceId": device_id,
            "messages": _translate_history(messages),
            "tools": _translate_tools(tools),
        }
        system = _extract_system(messages)
        if system:
            body["system"] = system

        signature = self._sign(body)
        headers = {
            "content-type": "application/json",
            "x-addison-device": device_id,
            "x-addison-signature": signature,
        }

        response = self._post(headers, body)
        if response.status_code >= 400:
            # Never echo the body, headers, or signature — just a plain next step.
            raise RuntimeError(_relay_error_message(response.status_code))

        data = response.json()

        # At-cap is a normal 200 with a flag — surface the wrap-up as an ordinary
        # assistant message, never an exception (design-doc §7.5.1).
        if data.get("at_cap"):
            return ModelResponse(
                text=data.get("text") or _DEFAULT_AT_CAP_MESSAGE,
                tool_calls=[],
                finish_reason="at_cap",
            )

        return _translate_relay_response(data)

    # --- device signing (§5) — the core never sees key material ------------
    def _device_id(self) -> str:
        device = self._bridge.get_device_key()  # {"deviceId", "publicKey"}; public half only
        return (device or {}).get("deviceId", "")

    def _sign(self, body: dict) -> str:
        signed = self._bridge.sign_relay_request(body)  # {"signature", "deviceId"}
        return (signed or {}).get("signature", "")

    def _post(self, headers: dict, body: dict) -> httpx.Response:
        injected = self._client
        client = injected if injected is not None else httpx.Client(timeout=_TIMEOUT_SECONDS)
        try:
            # POST: retry only when the request never reached the relay (§8.3).
            return request_with_retry(
                lambda: client.post(self._relay_url, headers=headers, json=body),
                idempotent=False,
            )
        except httpx.HTTPError:
            # Network/timeout failure. Clean message, no chained exception, so
            # nothing about the request (signature included) can leak.
            # This provider only ever handles turns when NO key is configured
            # (§4.6), so "add your own key" is always a valid way forward.
            raise RuntimeError(
                "Couldn't reach the free setup service just now. Check your "
                "internet connection and try again — or add your own API key "
                "in Settings and Addison will use that instead."
            ) from None
        finally:
            if injected is None:
                client.close()


def _translate_tools(tools: list) -> list[dict]:
    # Same shape as AnthropicProvider — duplicated (not imported) so this
    # provider stays independent of the primary provider's translation (§4.6).
    return [
        {"name": d.id, "description": d.description, "input_schema": d.parameters_schema}
        for d in tools
    ]


def _translate_history(messages: list[Message]) -> list[dict]:
    """Map Addison's flat message list to the same alternating turns
    AnthropicProvider produces (assistant tool_use blocks; merged tool_result
    turns). Kept in lockstep by duplication rather than a shared import so the
    two providers can evolve independently (§4.6)."""
    api_messages: list[dict] = []
    pending_results: list[dict] = []

    def flush_results() -> None:
        if pending_results:
            api_messages.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m.role == "system":
            continue  # carried by the top-level "system" field, not the messages list
        if m.role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": str(m.content),
                }
            )
            continue

        flush_results()

        if m.role == "user":
            api_messages.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if m.tool_calls:
                content: list[dict] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    content.append(
                        {"type": "tool_use", "id": c.id, "name": c.tool_id, "input": c.args}
                    )
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({"role": "assistant", "content": m.content})

    flush_results()
    return api_messages


def _extract_system(messages: list[Message]) -> str | None:
    parts = [m.content for m in messages if m.role == "system" and m.content]
    return "\n\n".join(parts) if parts else None


def _translate_relay_response(data: dict) -> ModelResponse:
    """A relay ``{"text": ...}`` reply -> a ModelResponse.

    Because the free model may lack native tool calling, a fenced JSON tool block
    in the text is promoted to a ToolCallRequest; anything else stays plain text.
    """
    text = data.get("text") or ""
    tool_call = parse_tool_call(text, id_prefix="setup")
    if tool_call is not None:
        # A tool call replaces the text (same as a native tool_use turn).
        return ModelResponse(text=None, tool_calls=[tool_call], finish_reason="tool_use")
    return ModelResponse(text=text or None, tool_calls=[], finish_reason="stop")


def _relay_error_message(status_code: int) -> str:
    if status_code == 429:
        return "The free setup service is busy right now. Wait a moment and try again."
    if status_code >= 500:
        return "The free setup service had a problem. Please try again in a moment."
    return "Couldn't reach the free setup service just now. Please try again."
