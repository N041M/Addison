"""ModelProvider protocol and shared model types — engineering-spec §3, §4.1.

The orchestrator is written entirely against ``ModelProvider`` and never
branches on the concrete provider. Capability differences are handled via
``capabilities()``, not ``isinstance`` checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, runtime_checkable

import httpx

# --- one conservative HTTP retry (shared by every provider) -----------------
_RETRY_SLEEP_SECONDS = 0.5  # a short fixed pause; no backoff/jitter — this codebase is minimal

# Connection-level failures where the request PROVABLY never reached the server,
# so replaying it can neither duplicate work nor double-bill. Safe to retry for
# any method, POST included.
_CONNECT_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout)
# Read/transport hiccups that are safe to retry ONLY for an idempotent GET: a
# ReadTimeout means the request DID reach the server (it just didn't answer in
# time), so on a POST it may already have been processed/billed and must never be
# resent — but on a side-effect-free GET a resend is harmless.
_IDEMPOTENT_ERRORS: tuple[type[Exception], ...] = _CONNECT_ERRORS + (
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def request_with_retry(
    send: Callable[[], httpx.Response], *, idempotent: bool
) -> httpx.Response:
    """Run ``send()`` (returns an ``httpx.Response``) with at most ONE retry after
    a short fixed pause. Invisible to callers: it returns the same Response or
    re-raises the same ``httpx`` error the caller's ``except httpx.HTTPError``
    already turns into a plain-language message — the only observable effect is
    latency.

    The idempotent split is the whole point (§8.3 no-double-billing reasoning):

    - ``idempotent=True`` — a GET (key validation / model listing) has no side
      effects, so retry it on any connection- OR read-level failure and on a 5xx
      (a transient server blip).
    - ``idempotent=False`` — a POST (send a message, cost tokens/money) is retried
      ONLY when the failure proves the request never arrived (ConnectError /
      ConnectTimeout). A ReadTimeout, a 5xx, or any received response means it MAY
      have been processed, so we never resend it.

    Both attempts reuse the caller's client, so the injected-client / per-call
    construct-and-close pattern at each call site is unchanged.
    """
    retryable = _IDEMPOTENT_ERRORS if idempotent else _CONNECT_ERRORS
    try:
        response = send()
    except retryable:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()  # second and final attempt; a re-raise propagates to the caller
    if idempotent and response.status_code >= 500:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()
    return response


class ModelRole(str, Enum):
    """Which job a configured provider is filling. Multiple roles may be
    configured and populated at once — this is NOT a single active-provider
    switch (see §4.1.1, ModelRouter)."""

    PRIMARY = "primary"                  # main conversation driver, typically a frontier cloud model
    LOCAL = "local"                      # self-hosted via Ollama, available once configured (§4.1.2)
    SETUP_ASSISTANT = "setup_assistant"  # onboarding-only free relay, unrelated to the above two


@dataclass
class ProviderCapabilities:
    native_tool_calling: bool
    max_context_tokens: int
    supports_streaming: bool
    runs_off_device: bool        # True only for local providers — informs privacy-sensitive routing
    vision: bool = False         # can analyze image input — gates the image path (§4.1.1, item A)
    audio: bool = False          # can analyze audio input
    # v2 auto-routing (§4.1.1) reads these flags to pick a capable model per task;
    # in v1 they only drive an explicit warning + manual switch, never an auto-switch.


@dataclass
class ToolCallRequest:
    id: str
    tool_id: str
    args: dict


@dataclass
class Message:
    role: str                    # 'user' | 'assistant' | 'tool'
    content: str
    tool_call_id: str | None = None
    # Set on assistant turns that requested tools. Providers with native tool
    # calling need the original tool_use blocks replayed in history so each
    # tool_call_id pairs with the tool_result that follows it.
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


@dataclass
class Usage:
    """Token counts for ONE provider call (§4.8 usage substrate). Populated from
    each API's own usage report and mapped honestly; a provider that reports no
    usage leaves ``ModelResponse.usage`` as None (no row is recorded for it)."""

    input_tokens: int
    output_tokens: int


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    # Token usage for the call that produced this response, when the provider
    # reports it (None otherwise). Orchestrator machinery records it into
    # ``usage_log`` — it is NEVER a registry tool (§4.8 precedent).
    usage: Usage | None = None


@runtime_checkable
class ModelProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    # ``effort`` is the per-message "answer style" (§4.1.1, models_catalog.py). Only
    # AnthropicProvider acts on it (and only for models that support it); every other
    # provider ACCEPTS and IGNORES it, so the orchestrator can pass it uniformly.
    def send(
        self,
        messages: list[Message],
        tools: list["ToolDefinition"],  # noqa: F821
        effort: str | None = None,
    ) -> ModelResponse: ...
